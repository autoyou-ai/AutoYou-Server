# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-8fb80ed6be2e66c96607a62b

"""Native Ollama gateway transport shared by AutoYou Server and Lite.

Hosts own configuration and conversation state.  This module only speaks the
native Ollama API, so the direct gateway path never needs the AutoYou agent
runtime to produce a reply.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-8fb80ed6be2e66c96607a62b"


import asyncio
import logging
import os
import ssl as _ssl
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Sequence

import aiohttp

from .ollama_capabilities import capabilities_from_show_payload
from .ollama_context_policy import recommend_ollama_num_ctx

try:
    import certifi as _certifi

    _SSL_CTX = _ssl.create_default_context(cafile=_certifi.where())
except ImportError:
    _SSL_CTX = _ssl.create_default_context()


logger = logging.getLogger(__name__)
_DEFAULT_BASE = "http://127.0.0.1:11434"
_DEFAULT_MODEL = "llama3.2"
_MODEL_CACHE: Dict[str, str] = {}


class OllamaGatewayError(RuntimeError):
    """A safe failure raised by the native Ollama transport."""


class OllamaConversationStore:
    """Small in-memory native-chat history shared by Server and Lite."""

    def __init__(self, max_history: int = 40) -> None:
        self._max_history = max(2, int(max_history or 40))
        self._history: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        self._turn_ids: Dict[str, List[str]] = defaultdict(list)

    def _aligned_ids(self, session_id: str, history: List[Dict[str, str]]) -> List[str]:
        ids = self._turn_ids[session_id]
        if len(ids) < len(history):
            ids.extend([""] * (len(history) - len(ids)))
        elif len(ids) > len(history):
            del ids[len(history):]
        return ids

    def append_user(self, session_id: str, message: str, turn_id: str = "") -> Dict[str, str]:
        history = self._history[session_id]
        ids = self._aligned_ids(session_id, history)
        turn = {"role": "user", "content": str(message or "")}
        history.append(turn)
        ids.append(str(turn_id or "").strip())
        if len(history) > self._max_history:
            self._history[session_id] = history[-self._max_history:]
            self._turn_ids[session_id] = ids[-self._max_history:]
        return turn

    def messages(self, session_id: str, system_prompt: str) -> List[Dict[str, str]]:
        return [{"role": "system", "content": str(system_prompt or "")}, *self._history.get(session_id, [])]

    def append_assistant(self, session_id: str, reply: str) -> None:
        history = self._history[session_id]
        ids = self._aligned_ids(session_id, history)
        history.append({"role": "assistant", "content": str(reply or "")})
        ids.append("")
        if len(history) > self._max_history:
            self._history[session_id] = history[-self._max_history:]
            self._turn_ids[session_id] = ids[-self._max_history:]

    def restore(self, session_id: str, turns: Sequence[Mapping[str, Any]]) -> None:
        key = str(session_id or "").strip()
        if not key:
            return
        restored = [
            {
                "role": str(turn.get("role") or "").strip(),
                "content": str(turn.get("content") or ""),
            }
            for turn in turns
            if isinstance(turn, Mapping)
            and str(turn.get("role") or "").strip() in {"user", "assistant"}
            and str(turn.get("content") or "")
        ][-self._max_history:]
        if not restored:
            self.clear(key)
            return
        self._history[key] = restored
        self._turn_ids[key] = [""] * len(restored)

    def discard_turn(self, session_id: str, turn: Dict[str, str]) -> None:
        history = self._history.get(session_id)
        if not history:
            return
        ids = self._aligned_ids(session_id, history)
        for index in range(len(history) - 1, -1, -1):
            if history[index] is turn:
                history.pop(index)
                ids.pop(index)
                break
        if not history:
            self.clear(session_id)

    def clear(self, session_id: str) -> None:
        self._history.pop(str(session_id or "").strip(), None)
        self._turn_ids.pop(str(session_id or "").strip(), None)

    def replace(
        self,
        session_id: str,
        *,
        target_message_id: str = "",
        target_text: str = "",
    ) -> Dict[str, Any]:
        key = str(session_id or "").strip()
        history = self._history.get(key)
        if not key or not history:
            return {"replaced": False, "reason": "session_history_not_found"}
        ids = self._aligned_ids(key, history)
        target_id = str(target_message_id or "").strip()
        target_value = str(target_text or "").strip()
        target_index: Optional[int] = None
        if target_id:
            for index, (turn, stored_id) in enumerate(zip(history, ids)):
                if turn.get("role") == "user" and stored_id == target_id:
                    target_index = index
                    break
        if target_index is None and target_value:
            matches = [
                index for index, turn in enumerate(history)
                if turn.get("role") == "user" and str(turn.get("content") or "").strip() == target_value
            ]
            if len(matches) == 1:
                target_index = matches[0]
            elif len(matches) > 1:
                return {"replaced": False, "reason": "ambiguous_target_text"}
        if target_index is None:
            return {"replaced": False, "reason": "target_turn_not_found"}
        removed_count = len(history) - target_index
        self._history[key] = history[:target_index]
        self._turn_ids[key] = ids[:target_index]
        if not self._history[key]:
            self.clear(key)
        return {
            "replaced": True,
            "session_id": key,
            "removed_turn_count": removed_count,
            "retained_turn_count": target_index,
        }


def _base_url(api_base: str) -> str:
    return str(api_base or _DEFAULT_BASE).strip().rstrip("/") or _DEFAULT_BASE


def resolve_ollama_thinking_enabled(configured: Any = None) -> bool:
    """Use an explicit config value, otherwise preserve Ollama env semantics."""
    if configured is not None:
        return bool(configured)
    value = (
        os.getenv("AUTOYOU_OLLAMA_THINKING")
        or os.getenv("OLLAMA_THINKING")
        or os.getenv("OLLAMA_THINK")
        or ""
    )
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def resolve_ollama_thinking_level(configured: Any = None) -> Optional[str]:
    """Return a supported thinking level, preserving environment fallback."""
    value = configured
    if value is None:
        value = os.getenv("AUTOYOU_OLLAMA_THINKING_LEVEL") or os.getenv("OLLAMA_THINKING_LEVEL")
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"low", "medium", "high", "max"} else None


def resolve_ollama_num_predict(*, thinking: bool = False, num_ctx: Any = None) -> Optional[int]:
    """Return the native output cap, preserving AutoYou Ollama behavior."""
    value = os.getenv("AUTOYOU_OLLAMA_NUM_PREDICT") or os.getenv("OLLAMA_NUM_PREDICT") or ""
    if not str(value).strip():
        default = 4096 if thinking else 1024
        try:
            context_window = int(num_ctx or 0)
        except (TypeError, ValueError):
            context_window = 0
        return min(default, max(1024, context_window // 4)) if context_window > 0 else default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return 1024
    return parsed if parsed > 0 else None


def _think_enables_reasoning(think: Any) -> bool:
    return bool(think.strip()) if isinstance(think, str) else bool(think)


def resolve_ollama_gateway_options(
    model_behavior: Optional[Mapping[str, Any]],
    mode_presets: Mapping[str, Mapping[str, Any]],
    model_name: str,
) -> Dict[str, Any]:
    """Resolve the existing Server model-behavior settings for native chat."""
    behavior = model_behavior if isinstance(model_behavior, Mapping) else {}
    mode = str(behavior.get("mode") or "accurate").strip().lower()
    preset = mode_presets.get(mode) or mode_presets.get("accurate") or {}
    params = preset.get("params", preset) if isinstance(preset, Mapping) else {}
    options: Dict[str, Any] = dict(params) if isinstance(params, Mapping) else {}

    for key in ("temperature", "top_p", "top_k", "repeat_penalty"):
        value = behavior.get(key)
        if value is None:
            continue
        try:
            options[key] = float(value)
        except (TypeError, ValueError):
            continue

    explicit_num_ctx = behavior.get("num_ctx")
    try:
        num_ctx = int(explicit_num_ctx) if explicit_num_ctx is not None else 0
    except (TypeError, ValueError):
        num_ctx = 0
    options["num_ctx"] = num_ctx if num_ctx > 0 else recommend_ollama_num_ctx(model_name)
    return options


async def get_ollama_model(ollama_api: str, configured_model: str = "") -> str:
    """Return the explicit model or the first model advertised by Ollama."""
    configured = str(configured_model or "").strip()
    if configured:
        return configured
    base = _base_url(ollama_api)
    cached = _MODEL_CACHE.get(base)
    if cached:
        return cached
    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_SSL_CTX),
            timeout=aiohttp.ClientTimeout(total=5.0),
        ) as session:
            async with session.get(f"{base}/api/tags") as response:
                if response.status == 200:
                    payload = await response.json()
                    models = payload.get("models") or []
                    name = str((models[0] or {}).get("name") or "").strip() if models else ""
                    if name:
                        _MODEL_CACHE[base] = name
                        return name
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        logger.debug("Ollama model discovery failed: %s", exc)
    return _DEFAULT_MODEL


def reset_ollama_model_cache(ollama_api: str = "") -> None:
    """Forget one endpoint's discovery result, or every result when omitted."""
    if str(ollama_api or "").strip():
        _MODEL_CACHE.pop(_base_url(ollama_api), None)
    else:
        _MODEL_CACHE.clear()


async def delete_ollama_model(ollama_api: str, model_name: str) -> Dict[str, Any]:
    """Delete a local model via Ollama's native ``/api/delete`` endpoint."""
    base = _base_url(ollama_api)
    model = str(model_name or "").strip()
    if not model:
        return {"deleted": False, "api_base": base, "error": "Model name is required"}
    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_SSL_CTX),
            timeout=aiohttp.ClientTimeout(total=15.0),
        ) as session:
            async with session.delete(f"{base}/api/delete", json={"name": model}) as response:
                if response.status in {200, 204}:
                    reset_ollama_model_cache(base)
                    return {"deleted": True, "api_base": base, "model": model}
                return {"deleted": False, "api_base": base, "model": model, "error": f"HTTP {response.status}"}
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return {"deleted": False, "api_base": base, "model": model, "error": "Ollama is unreachable"}


async def probe_ollama(ollama_api: str) -> Dict[str, Any]:
    """Return sanitized availability and model information for an Ollama endpoint."""
    base = _base_url(ollama_api)
    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_SSL_CTX),
            timeout=aiohttp.ClientTimeout(total=5.0),
        ) as session:
            async with session.get(f"{base}/api/tags") as response:
                if response.status != 200:
                    return {"available": False, "api_base": base, "models": [], "error": f"HTTP {response.status}"}
                payload = await response.json()
                models = [str(item.get("name") or "") for item in (payload.get("models") or []) if isinstance(item, dict)]
                return {"available": True, "api_base": base, "models": models, "model_count": len(models)}
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return {"available": False, "api_base": base, "models": [], "error": "Ollama is unreachable"}


async def inspect_ollama_capabilities(ollama_api: str, model_name: str) -> Dict[str, Any]:
    """Inspect ``/api/show`` without the agent runtime or a blocking client."""
    base = _base_url(ollama_api)
    model = str(model_name or "").strip()
    if not model:
        return {"name": "", "available": False, "supports_thinking": None, "capability_source": "unavailable"}
    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_SSL_CTX),
            timeout=aiohttp.ClientTimeout(total=5.0),
        ) as session:
            async with session.post(f"{base}/api/show", json={"model": model}) as response:
                if response.status != 200:
                    raise OllamaGatewayError(f"HTTP {response.status}")
                return capabilities_from_show_payload(model, await response.json())
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, OllamaGatewayError, ValueError) as exc:
        logger.debug("Ollama capability inspection failed: %s", exc)
        return {
            "name": model,
            "available": False,
            "capabilities": [],
            "supports_thinking": None,
            "thinking_levels": [],
            "capability_source": "unavailable",
        }


async def call_ollama_gateway(
    *,
    ollama_api: str,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    options: Optional[Mapping[str, Any]] = None,
    think: Any = None,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Send one native ``/api/chat`` request and return its plain reply metadata."""
    base = _base_url(ollama_api)
    selected_model = await get_ollama_model(base, model)
    payload: Dict[str, Any] = {
        "model": selected_model,
        "messages": [dict(message) for message in messages],
        "stream": False,
    }
    if think is not None:
        payload["think"] = think
    resolved_options = {str(key): value for key, value in (options or {}).items() if value is not None}
    num_predict = resolve_ollama_num_predict(
        thinking=_think_enables_reasoning(think),
        num_ctx=resolved_options.get("num_ctx"),
    )
    if num_predict is not None:
        resolved_options.setdefault("num_predict", num_predict)
    if resolved_options:
        payload["options"] = resolved_options

    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_SSL_CTX),
            timeout=aiohttp.ClientTimeout(total=max(5.0, float(timeout or 120.0))),
        ) as session:
            async with session.post(f"{base}/api/chat", json=payload) as response:
                if response.status >= 400:
                    raise OllamaGatewayError(f"Ollama returned HTTP {response.status}.")
                data = await response.json()
    except asyncio.CancelledError:
        raise
    except OllamaGatewayError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
        raise OllamaGatewayError("Ollama is unreachable.") from exc

    if not isinstance(data, dict) or data.get("done") is not True:
        raise OllamaGatewayError("Ollama did not complete the response.")
    if data.get("done_reason") == "length":
        raise OllamaGatewayError("Ollama reached the output limit before completing the response.")
    try:
        reply = str(data["message"]["content"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OllamaGatewayError("Ollama returned an unexpected response.") from exc
    if not reply.strip():
        raise OllamaGatewayError("Ollama returned an empty response.")
    return {
        "response": reply,
        "model": selected_model,
        "api_base": base,
        "done_reason": data.get("done_reason") if isinstance(data, dict) else None,
    }


async def call_ollama_with_history(
    *,
    store: OllamaConversationStore,
    ollama_api: str,
    model: str,
    message: str,
    session_id: str,
    system_prompt: str = "You are AutoYou, a helpful AI assistant.",
    options: Optional[Mapping[str, Any]] = None,
    think: Any = None,
    timeout: float = 120.0,
    turn_id: str = "",
    images: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Run one native chat turn while maintaining the host's in-memory session."""
    turn = store.append_user(session_id, message, turn_id)
    try:
        messages = store.messages(session_id, system_prompt)
        if images and messages:
            messages[-1] = {**messages[-1], "images": list(images)}
        result = await call_ollama_gateway(
            ollama_api=ollama_api,
            model=model,
            messages=messages,
            options=options,
            think=think,
            timeout=timeout,
        )
    except asyncio.CancelledError:
        store.discard_turn(session_id, turn)
        raise
    except Exception:
        store.discard_turn(session_id, turn)
        raise
    store.append_assistant(session_id, str(result.get("response") or ""))
    return result


__all__ = [
    "OllamaGatewayError",
    "OllamaConversationStore",
    "call_ollama_gateway",
    "call_ollama_with_history",
    "delete_ollama_model",
    "get_ollama_model",
    "inspect_ollama_capabilities",
    "probe_ollama",
    "reset_ollama_model_cache",
    "resolve_ollama_gateway_options",
    "resolve_ollama_num_predict",
    "resolve_ollama_thinking_enabled",
    "resolve_ollama_thinking_level",
]
