# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Warm the AI worker and the model as soon as the worker is up.

The first message after a start took 53.8 s (owner report 2026-11-03): ADK
builds the agent tree (the root and 45 specialists) on first use, and Ollama
loads the model. Both happen here in the background once the worker answers
its health check, so the first message does not pay for them.

- The tree is built by `GET /apps/<app>/app-info`, which loads the app into
  ADK's agent cache (the instance the first chat then uses) without a session
  or a model call.
- The model is loaded by a prompt-less `/api/generate`, with the same `num_ctx`
  the agent will ask for (a different context size would make Ollama load the
  model again on the first chat) and the same keep-alive.
- Ollama then reports (`/api/ps`) how much of the model went onto the GPU. A
  model partly on the CPU at this window is slow on every message, so a
  window half as big is recorded for it, and the next start uses that.

Set `AUTOYOU_WARM_AI_ON_START=0` to turn it off.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import asyncio
import json
import logging
import os
import time
import urllib.request
from typing import Any, Callable, Dict, Optional

from shared.ollama_context_policy import (
    MIN_NUM_CTX,
    context_sizing_mode,
    record_context_limit,
    resolve_ollama_keep_alive,
)

logger = logging.getLogger(__name__)

WARMUP_ENV = "AUTOYOU_WARM_AI_ON_START"
# A model with less than this share of its bytes on the GPU counts as spilled.
_ON_GPU_SHARE = 0.97
AGENT_APP_NAME = "autoyou_agents"


def warmup_enabled() -> bool:
    return str(os.environ.get(WARMUP_ENV, "1")).strip().lower() not in {"0", "false", "no", "off"}


def _request(url: str, *, payload: Optional[Dict[str, Any]] = None, timeout: float) -> int:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()
        return int(response.status)


def build_agent_tree(host: str, port: int, *, timeout: float = 300.0) -> Dict[str, Any]:
    """Make the ADK worker load the app, building every agent, without running one."""
    started = time.monotonic()
    try:
        status = _request(f"http://{host}:{port}/apps/{AGENT_APP_NAME}/app-info", timeout=timeout)
        return {"ok": 200 <= status < 300, "status": status, "seconds": round(time.monotonic() - started, 1)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "seconds": round(time.monotonic() - started, 1)}


def load_ollama_model(api_base: str, model: str, *, num_ctx: Optional[int] = None, timeout: float = 600.0) -> Dict[str, Any]:
    """Load a model into Ollama's memory: a generate call without a prompt only loads it."""
    started = time.monotonic()
    payload: Dict[str, Any] = {"model": model}
    if num_ctx:
        payload["options"] = {"num_ctx": int(num_ctx)}
    keep_alive = resolve_ollama_keep_alive()
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    try:
        status = _request(f"{api_base.rstrip('/')}/api/generate", payload=payload, timeout=timeout)
        return {"ok": 200 <= status < 300, "status": status, "seconds": round(time.monotonic() - started, 1)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "seconds": round(time.monotonic() - started, 1)}


def _get_json(url: str, *, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read() or b"null")


def _same_model(a: str, b: str) -> bool:
    def norm(value: str) -> str:
        value = str(value or "").strip().lower()
        return value if ":" in value else f"{value}:latest"
    return norm(a) == norm(b)


def model_placement(api_base: str, model: str, *, timeout: float = 10.0) -> Optional[Dict[str, int]]:
    """Where Ollama put a loaded model: its size in memory and the part on the GPU."""
    try:
        data = _get_json(f"{api_base.rstrip('/')}/api/ps", timeout=timeout)
    except Exception:
        return None
    for entry in (data or {}).get("models") or []:
        if _same_model(str(entry.get("model") or entry.get("name") or ""), model):
            try:
                return {"size": int(entry.get("size") or 0), "size_vram": int(entry.get("size_vram") or 0)}
            except (TypeError, ValueError):
                return None
    return None


def limit_context_if_spilled(api_base: str, model: str, num_ctx: Optional[int]) -> Optional[Dict[str, Any]]:
    """Record a smaller window for a model Ollama could not fit on the GPU at ``num_ctx``.

    A model entirely on the CPU has no GPU to fit, so nothing is recorded.
    """
    placement = model_placement(api_base, model)
    if not placement or placement["size"] <= 0:
        return None
    size, on_gpu = placement["size"], placement["size_vram"]
    report: Dict[str, Any] = {**placement, "on_gpu_share": round(on_gpu / size, 3)}
    if 0 < on_gpu < size * _ON_GPU_SHARE and num_ctx and int(num_ctx) > MIN_NUM_CTX:
        smaller = max(MIN_NUM_CTX, (int(num_ctx) // 2) // 1024 * 1024)
        record_context_limit(model, smaller, f"spilled_to_cpu ({on_gpu / size:.0%} on the GPU at num_ctx={num_ctx})")
        report["next_num_ctx"] = smaller
        logger.warning(
            "Ollama put only %.0f%% of %s on the GPU at num_ctx=%s; the next start uses num_ctx=%s",
            100 * on_gpu / size, model, num_ctx, smaller,
        )
    return report


async def warm_ai_runtime(
    *,
    host: str,
    port: int,
    ollama_api_base: Optional[str] = None,
    ollama_model: Optional[str] = None,
    num_ctx: Optional[int] = None,
    log: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """Build the agent tree and load the model side by side; report how long each took."""
    if not warmup_enabled():
        return {"skipped": True}
    jobs = [asyncio.to_thread(build_agent_tree, host, port)]
    if ollama_api_base and ollama_model:
        jobs.append(asyncio.to_thread(load_ollama_model, ollama_api_base, ollama_model, num_ctx=num_ctx))
    results = await asyncio.gather(*jobs)
    report: Dict[str, Any] = {"agent_tree": results[0]}
    if len(results) > 1:
        report["model"] = {**results[1], "name": ollama_model, "num_ctx": num_ctx}
        if results[1].get("ok") and context_sizing_mode() == "auto":
            placement = await asyncio.to_thread(limit_context_if_spilled, ollama_api_base, ollama_model, num_ctx)
            if placement is not None:
                report["model"]["placement"] = placement
    if log is not None:
        log("Warmed the AI runtime at start: %s", report)
    return report


# Running warm-ups, kept referenced until they finish.
_TASKS: "set[asyncio.Task[Dict[str, Any]]]" = set()


def schedule(**kwargs: Any) -> Optional["asyncio.Task[Dict[str, Any]]"]:
    """Start `warm_ai_runtime` in the background; the caller does not wait for it."""
    if not warmup_enabled():
        return None
    task = asyncio.get_running_loop().create_task(warm_ai_runtime(**kwargs))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task
