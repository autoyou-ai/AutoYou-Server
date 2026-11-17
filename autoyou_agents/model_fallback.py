# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Keep the assistant answering when the chosen Ollama model cannot load.

Owner report 2026-11-04: every reply was "the AI agent server returned an
internal error". Ollama could not load the configured 27B model
(`error loading model: std::bad_alloc`): Windows had about 2.4 GB of system
memory left, and the model keeps part of its weights in pinned system memory
even though the GPU had 107 GB free. Small models still loaded.

Three agent callbacks, installed on the root and on every specialist:

- `answer_with_fallback_model` (model error): when a model call fails because
  the model cannot load, the same request is answered by the first installed
  fallback model that loads, with the agent's own settings.
- `route_to_loadable_model` (before model): for a few minutes after that,
  calls go straight to the fallback instead of paying for another failed load.
- `add_fallback_notice` (after model): the first fallback answer in a
  conversation says which model answered and why, once.

`AUTOYOU_OLLAMA_FALLBACK_MODELS` sets the order (comma separated); `none`
turns the fallback off.
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
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FALLBACK_MODELS_ENV = "AUTOYOU_OLLAMA_FALLBACK_MODELS"
# The small models AutoYou is checked on, best first.
DEFAULT_FALLBACK_MODELS = ("ministral-3:8b", "gemma4:e4b", "ministral-3:3b")
NOTICE_STATE_KEY = "autoyou_model_fallback_notice"
# How long calls skip a model that failed to load before it is tried again.
SKIP_SECONDS = 300.0
_INSTALLED_CACHE_SECONDS = 60.0
_OLLAMA_PREFIXES = ("ollama_chat/", "ollama/")
_OLLAMA_CHAT = "ollama_chat/"

# Ollama's errors for a model that runs out of memory while loading or running.
_MEMORY_FRAGMENTS = (
    "bad_alloc",
    "requires more system memory",
    "memory layout cannot be allocated",
    "out of memory",
    "unable to allocate",
    "failed to allocate",
    "insufficient memory",
    "resource limitations",
)
# Errors for a model whose runner could not start or died.
_LOAD_FRAGMENTS = _MEMORY_FRAGMENTS + (
    "error loading model",
    "llama-server process has terminated",
    "runner has unexpectedly stopped",
    "runner process has terminated",
)

# bare primary model name -> (failed at, fallback that answered, "memory" or "load")
_skipped: Dict[str, Tuple[float, str, str]] = {}
_installed_cache: Dict[str, Any] = {"at": 0.0, "names": None}


def is_model_load_error(error: Any) -> bool:
    """True when a model call failed because the model could not be loaded or kept running."""
    text = str(error or "").lower()
    return any(fragment in text for fragment in _LOAD_FRAGMENTS)


def is_memory_shortage(error: Any) -> bool:
    text = str(error or "").lower()
    return any(fragment in text for fragment in _MEMORY_FRAGMENTS)


def _bare_name(model: Any) -> str:
    name = str(model or "").strip()
    for prefix in _OLLAMA_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _same_model(a: Any, b: Any) -> bool:
    def norm(value: Any) -> str:
        bare = _bare_name(value).lower()
        return bare if ":" in bare else f"{bare}:latest"
    return norm(a) == norm(b)


def _installed_models(api_base: str) -> Optional[List[str]]:
    now = time.monotonic()
    if _installed_cache["names"] is not None and now - _installed_cache["at"] < _INSTALLED_CACHE_SECONDS:
        return _installed_cache["names"]
    try:
        with urllib.request.urlopen(f"{api_base.rstrip('/')}/api/tags", timeout=5) as response:
            names = [str(item.get("name") or "") for item in json.loads(response.read()).get("models", [])]
    except Exception as exc:
        logger.debug("Could not list Ollama models for the load fallback: %s", exc)
        return None
    _installed_cache.update(at=now, names=names)
    return names


def fallback_models(exclude: List[str], api_base: str) -> List[str]:
    """The installed fallback models to try, in order, leaving out `exclude`."""
    raw = os.environ.get(FALLBACK_MODELS_ENV)
    if raw is not None and raw.strip().lower() in {"", "none", "off", "0", "false"}:
        return []
    wanted = [name.strip() for name in raw.split(",") if name.strip()] if raw is not None else list(DEFAULT_FALLBACK_MODELS)
    installed = _installed_models(api_base) or []
    chosen: List[str] = []
    for name in wanted:
        match = next((item for item in installed if _same_model(item, name)), None)
        if match and match not in chosen and not any(_same_model(match, other) for other in exclude):
            chosen.append(match)
    return chosen


def _agent_model(callback_context: Any) -> Any:
    invocation = getattr(callback_context, "_invocation_context", None)
    agent = getattr(invocation, "agent", None)
    try:
        return getattr(agent, "canonical_model", None)
    except Exception:
        return None


def _ollama_primary(callback_context: Any) -> Tuple[Any, str]:
    """The agent's model object and its bare name, or (None, "") when it is not Ollama."""
    model = _agent_model(callback_context)
    name = str(getattr(model, "model", "") or "")
    if not name.startswith(_OLLAMA_PREFIXES):
        return None, ""
    return model, _bare_name(name)


def _model_settings(model: Any) -> Dict[str, Any]:
    return dict(getattr(model, "_additional_args", None) or {})


def _reasoning_on(settings: Dict[str, Any]) -> bool:
    think = settings.get("think")
    if isinstance(think, str):
        return bool(think.strip())
    return bool(think)


def _skip_target(primary: str) -> Tuple[str, str]:
    """The fallback answering for `primary` and why, or ("", "") when `primary` is not skipped."""
    entry = _skipped.get(primary)
    if not entry:
        return "", ""
    failed_at, fallback, reason = entry
    if time.monotonic() - failed_at >= SKIP_SECONDS:
        _skipped.pop(primary, None)
        return "", ""
    return fallback, reason


def _make_fallback_llm(model_name: str, settings: Dict[str, Any]) -> Any:
    from google.adk.models.lite_llm import LiteLlm

    return LiteLlm(model=_OLLAMA_CHAT + model_name, **settings)


async def answer_with_fallback_model(callback_context: Any, llm_request: Any, error: Exception) -> Any:
    """Model-error callback: answer with an installed model that loads, or return None."""
    if not is_model_load_error(error):
        return None
    model, primary = _ollama_primary(callback_context)
    if not primary:
        return None
    failed = _bare_name(getattr(llm_request, "model", "") or primary)
    settings = _model_settings(model)
    # A fallback may not support thinking; the agent's other settings carry over.
    settings.pop("think", None)
    api_base = str(settings.get("api_base") or os.environ.get("OLLAMA_API_BASE") or "http://localhost:11434")
    candidates = await asyncio.to_thread(fallback_models, [primary, failed], api_base)
    for name in candidates:
        try:
            request = llm_request.model_copy(update={"model": _OLLAMA_CHAT + name})
            final = None
            async for response in _make_fallback_llm(name, settings).generate_content_async(request, stream=False):
                final = response
        except Exception as exc:
            logger.warning("Fallback model %s did not answer either: %s", name, str(exc)[:200])
            continue
        if final is None:
            continue
        logger.warning("Model %s could not load (%s); %s answered instead", failed, str(error)[:200], name)
        reason = "memory" if is_memory_shortage(error) else "load"
        _skipped[primary] = (time.monotonic(), name, reason)
        metadata = dict(getattr(final, "custom_metadata", None) or {})
        metadata["model_fallback"] = {"from": primary, "to": name, "reason": reason}
        final.custom_metadata = metadata
        return final
    if candidates:
        logger.error("Model %s could not load and no fallback model answered (%s)", failed, ", ".join(candidates))
    return None


async def route_to_loadable_model(callback_context: Any, llm_request: Any) -> Any:
    """Before-model callback: send the call to the fallback while the agent's model will not load."""
    model, primary = _ollama_primary(callback_context)
    if not primary:
        return None
    fallback, _reason = _skip_target(primary)
    # A request with reasoning on goes to the model that supports it; the error path handles it.
    if fallback and not _reasoning_on(_model_settings(model)):
        llm_request.model = _OLLAMA_CHAT + fallback
    return None


def _final_text_only(response: Any) -> bool:
    if getattr(response, "partial", False):
        return False
    parts = list(getattr(getattr(response, "content", None), "parts", None) or [])
    has_text = any(getattr(part, "text", None) and not getattr(part, "thought", False) for part in parts)
    has_call = any(getattr(part, "function_call", None) for part in parts)
    return has_text and not has_call


def _notice(primary: str, fallback: str, reason: str) -> str:
    primary, fallback = (name[: -len(":latest")] if name.endswith(":latest") else name for name in (primary, fallback))
    if reason == "memory":
        return (
            f"(Answered by {fallback}: {primary} could not load because this computer is low on memory. "
            "Restarting the computer usually fixes it.)"
        )
    return (
        f"(Answered by {fallback}: {primary} failed to load. If this keeps happening, choose another "
        "model under Models on this computer's Admin Page.)"
    )


async def add_fallback_notice(callback_context: Any, llm_response: Any) -> Any:
    """After-model callback: say once per conversation that a fallback model answered."""
    if not _final_text_only(llm_response):
        return None
    _model, primary = _ollama_primary(callback_context)
    if not primary:
        return None
    info = (getattr(llm_response, "custom_metadata", None) or {}).get("model_fallback")
    if isinstance(info, dict):
        fallback, reason = str(info.get("to") or ""), str(info.get("reason") or "load")
    else:
        fallback, reason = _skip_target(primary)
        answered_by = getattr(llm_response, "model_version", None)
        # Only an answer the fallback actually wrote gets the notice.
        if not fallback or not answered_by or not _same_model(answered_by, fallback):
            return None
    if not fallback:
        return None
    state = getattr(callback_context, "state", None)
    key = f"{primary}->{fallback}"
    try:
        if state is None or state.get(NOTICE_STATE_KEY) == key:
            return None
        parts = llm_response.content.parts
        last = max(index for index, part in enumerate(parts) if getattr(part, "text", None))
        parts[last].text = f"{parts[last].text.rstrip()}\n\n{_notice(primary, fallback, reason)}"
        state[NOTICE_STATE_KEY] = key
    except Exception as exc:
        logger.debug("Could not add the model fallback notice: %s", exc)
        return None
    return llm_response


answer_with_fallback_model._autoyou_callback_marker = "model_fallback:on_error"
route_to_loadable_model._autoyou_callback_marker = "model_fallback:before"
add_fallback_notice._autoyou_callback_marker = "model_fallback:after"
