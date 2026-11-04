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
  the agent will ask for: a different context size would make Ollama load the
  model again on the first chat. Ollama keeps it for its usual keep-alive.

Set `AUTOYOU_WARM_AI_ON_START=0` to turn it off.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import asyncio
import json
import os
import time
import urllib.request
from typing import Any, Callable, Dict, Optional

WARMUP_ENV = "AUTOYOU_WARM_AI_ON_START"
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
    try:
        status = _request(f"{api_base.rstrip('/')}/api/generate", payload=payload, timeout=timeout)
        return {"ok": 200 <= status < 300, "status": status, "seconds": round(time.monotonic() - started, 1)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "seconds": round(time.monotonic() - started, 1)}


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
