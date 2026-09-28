# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Authenticated MCP-facing REST routes for the full AutoYou server.

The ChatGPT/MCP adapter is a separate process.  These routes are the narrow
same-machine or explicitly token-authenticated bridge into the full server,
matching the contract already exposed by AutoYou Lite.  They intentionally
delegate pairing, chat, and WebRTC delivery to the existing runtime instead
of duplicating any protocol or provider logic here.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pairing_router import PairingRouter


_PAIRING_COMMANDS = {
    "/pair",
    "/otp_pair",
    "/pair_hello",
    "/autopair_hello",
    "/autopair",
    "/autopair_candidates",
}
_SENDER_ID_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,256}$")


class SendMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=16_000)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    user_id: str = ""


class PairingMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=512_000)
    platform: str = "chatgpt"
    sender_id: str = "chatgpt-local"


class ChatMessageRequest(BaseModel):
    # The Lite façade uses OpenAI-compatible messages.  ``message`` is also
    # accepted so direct callers can use the full AI worker's terminology.
    message: Optional[str] = Field(default=None, max_length=16_000)
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    model: str = "autoyou/default"
    session_id: Optional[str] = Field(default=None, max_length=256)
    user: str = "autoyou-mcp"
    context: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


def _jsonable_model(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            dumped = model_dump()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        dumped = dict_method()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def _extract_chat_message(request: ChatMessageRequest) -> str:
    direct = str(request.message or "").strip()
    if direct:
        return direct
    for item in reversed(request.messages or []):
        if not isinstance(item, Mapping):
            continue
        if str(item.get("role") or "").strip().lower() not in {"user", ""}:
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:16_000]
        if isinstance(content, list):
            parts = [
                str(part.get("text") or "").strip()
                for part in content
                if isinstance(part, Mapping) and str(part.get("text") or "").strip()
            ]
            if parts:
                return "\n".join(parts)[:16_000]
    return ""


def _pairing_command(text: str) -> Optional[str]:
    normalized = str(text or "").lstrip()
    first_line = normalized.splitlines()[0].strip().lower() if normalized else ""
    if first_line:
        command = first_line.split(None, 1)[0]
        if command in _PAIRING_COMMANDS:
            return command
    if PairingRouter.looks_like_raw_autopair_fragment(normalized):
        return "autopair_fragment"
    return None


def _safe_sender_id(value: str) -> str:
    candidate = str(value or "").strip()
    if _SENDER_ID_RE.fullmatch(candidate):
        return candidate
    # The MCP adapter supplies a deterministic identifier.  If a direct
    # caller sends punctuation or whitespace, keep it in one stable bucket
    # without allowing unbounded pairing-router keys.
    return "chatgpt-" + uuid.uuid5(uuid.NAMESPACE_URL, candidate[:512]).hex


def _active_sessions(server: Any) -> List[tuple[str, Any]]:
    webrtc = getattr(server, "WEBRTC", None)
    if webrtc is None:
        return []
    entries = getattr(webrtc, "_unique_datachannel_manager_entries", None)
    if not callable(entries):
        return []
    try:
        result = entries(require_send_message=True)
    except Exception:
        return []
    return [
        (str(session_id), manager)
        for session_id, manager in result
        if str(session_id or "").strip() and manager is not None
    ]


def _session_payload(server: Any, session_id: str) -> Dict[str, Any]:
    webrtc = getattr(server, "WEBRTC", None)
    identity: Any = None
    if webrtc is not None:
        resolver = getattr(webrtc, "_resolve_chat_identity", None)
        if callable(resolver):
            try:
                identity = resolver(session_id)
            except Exception:
                identity = None
    return {
        "transport": "webrtc",
        "connected": True,
        "owner_key": str(getattr(identity, "owner_key", "") or ""),
        "canonical_session_id": str(
            getattr(identity, "canonical_session_id", "") or session_id
        ),
    }


def _active_ai_summary(server: Any, cfg: Mapping[str, Any]) -> tuple[str, Optional[str]]:
    provider_cfg = cfg.get("ai_provider") if isinstance(cfg.get("ai_provider"), Mapping) else {}
    ollama_cfg = cfg.get("ollama") if isinstance(cfg.get("ollama"), Mapping) else {}
    provider = str(provider_cfg.get("provider") or "").strip().lower()
    if not provider:
        provider = str(ollama_cfg.get("enabled") and "ollama" or "")
    model: Optional[str] = None
    if provider in {"ollama", "ollama_gateway"}:
        model = str(ollama_cfg.get("model") or "").strip() or None
    elif provider == "apple_intelligence":
        model = "apple_intelligence/on-device"
    elif provider == "odysseus":
        model = str(provider_cfg.get("odysseus_model") or "").strip() or None
    elif provider == "openclaw":
        model = str(provider_cfg.get("openclaw_model") or "").strip() or None
    elif provider == "hermes":
        model = str(provider_cfg.get("hermes_model") or "").strip() or None
    elif provider == "litellm":
        model = str(provider_cfg.get("litellm_model") or "").strip() or None
    elif provider == "google":
        model = str(ollama_cfg.get("google_model") or "").strip() or None
    if not provider:
        provider = "configured-agent"
    return provider, model


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:
    """Attach the full-server MCP façade to the admin/runtime app."""

    async def _auth(request: Request):
        auth_error = server._require_mcp_api_auth(request)
        if auth_error:
            return auth_error
        return None

    @admin_app.get("/api/v1/mcp/status")
    async def mcp_status_endpoint(request: Request):
        auth_error = await _auth(request)
        if auth_error:
            return auth_error
        cfg = getattr(server, "STATE", None)
        config = getattr(cfg, "config", None) if cfg is not None else None
        config = config if isinstance(config, Mapping) else {}
        provider, model = _active_ai_summary(server, config)
        sessions = _active_sessions(server)
        get_mode = getattr(server, "get_security_mode", None)
        get_tier = getattr(server, "get_pairing_tier", None)
        security_mode = str(get_mode() if callable(get_mode) else "secure")
        security_tier = str(get_tier() if callable(get_tier) else "B").upper()
        return server._json_response_no_store(
            {
                "status": "running",
                "version": "full-server",
                "connected_sessions": len(sessions),
                "sessions": [session_id for session_id, _ in sessions],
                "ai_provider": provider,
                "ai_model": model,
                "proxy_target": None,
                "openclaw_token_auto_login": False,
                "security_mode": security_mode,
                "security_tier": security_tier if security_tier in {"A", "B"} else "B",
                "mcp_enabled": bool(server._mcp_api_enabled()),
            }
        )

    @admin_app.get("/api/v1/mcp/sessions")
    async def mcp_sessions_endpoint(request: Request):
        auth_error = await _auth(request)
        if auth_error:
            return auth_error
        return server._json_response_no_store(
            {
                "sessions": {
                    session_id: _session_payload(server, session_id)
                    for session_id, _ in _active_sessions(server)
                }
            }
        )

    @admin_app.post("/api/v1/mcp/send/{session_id}")
    async def mcp_send_endpoint(session_id: str, request: Request, payload: SendMessageRequest):
        auth_error = await _auth(request)
        if auth_error:
            return auth_error
        normalized_session_id = str(session_id or "").strip()
        metadata = {"source": "autoyou-mcp", "client": "chatgpt"}
        try:
            sent = bool(
                await server.WEBRTC.send_chat_to_session(
                    normalized_session_id,
                    payload.text,
                    metadata=metadata,
                    # The MCP caller is an adapter identity, not an AutoYou
                    # user identity. Let the native transport apply its
                    # configured server identity instead of accepting a
                    # caller-controlled user_id field.
                    user_id=None,
                )
            )
        except Exception as exc:
            server.LOGGER.warning("Full-server MCP WebRTC send failed: %s", exc)
            sent = False
        return server._json_response_no_store(
            {
                "sent": sent,
                "session_id": normalized_session_id,
                "source": "autoyou-mcp",
            }
        )

    @admin_app.post("/api/v1/mcp/pair")
    async def mcp_pair_endpoint(request: Request, payload: PairingMessageRequest):
        auth_error = await _auth(request)
        if auth_error:
            return auth_error
        command = _pairing_command(payload.text)
        if command is None:
            return JSONResponse(
                status_code=400,
                content={"accepted": False, "error": "unsupported_pairing_message"},
            )
        pairing_router = getattr(server, "pairing_router", None)
        if pairing_router is None:
            return JSONResponse(status_code=503, content={"accepted": False, "error": "pairing_unavailable"})
        platform = str(payload.platform or "chatgpt").strip().lower() or "chatgpt"
        sender_id = _safe_sender_id(payload.sender_id)
        try:
            response = await pairing_router.process_message(
                payload.text,
                platform=platform,
                sender_id=sender_id,
                identity_sender_id=sender_id,
            )
        except Exception as exc:
            server.LOGGER.warning("Full-server MCP pairing relay failed: %s", exc)
            return JSONResponse(status_code=502, content={"accepted": False, "error": "pairing_failed"})
        fragment_consumed = response == PairingRouter.FRAGMENT_CONSUMED
        return server._json_response_no_store(
            {
                "accepted": True,
                "command": command.lstrip("/"),
                "fragment_consumed": fragment_consumed,
                "reply": "" if fragment_consumed else str(response or ""),
            }
        )

    @admin_app.post("/api/v1/mcp/chat")
    async def mcp_chat_endpoint(request: Request, payload: ChatMessageRequest):
        auth_error = await _auth(request)
        if auth_error:
            return auth_error
        message = _extract_chat_message(payload)
        if not message:
            return JSONResponse(status_code=400, content={"error": "message is required"})
        session_id = str(payload.session_id or "").strip() or None
        metadata = {"source": "autoyou-mcp", "client": "chatgpt"}
        chat_request_cls = getattr(server, "ChatRequest", None)
        process_chat = getattr(server, "process_chat_message", None)
        if chat_request_cls is None or not callable(process_chat):
            return JSONResponse(status_code=503, content={"error": "ai_agent_unavailable"})
        try:
            chat_request = chat_request_cls(
                message=message,
                session_id=session_id,
                user_id="autoyou-mcp",
                context=[],
                metadata=metadata,
            )
            ai_port = int(getattr(server, "AI_AGENT_SERVER_PORT", 8081) or 8081)
            response = await process_chat(chat_request, f"http://localhost:{ai_port}")
        except Exception as exc:
            server.LOGGER.warning("Full-server MCP chat failed: %s", exc)
            return JSONResponse(status_code=502, content={"error": "ai_backend_unavailable"})

        response_payload = _jsonable_model(response)
        answer = str(response_payload.get("response") or "").strip()
        if not answer:
            return JSONResponse(status_code=502, content={"error": "ai_backend_invalid_response"})
        resolved_session_id = str(response_payload.get("session_id") or session_id or "").strip()
        model = str(response_payload.get("metadata", {}).get("model") or payload.model or "autoyou/default")
        return server._json_response_no_store(
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "response": answer,
                "session_id": resolved_session_id,
            }
        )

    return {
        "mcp_status_endpoint": mcp_status_endpoint,
        "mcp_sessions_endpoint": mcp_sessions_endpoint,
        "mcp_send_endpoint": mcp_send_endpoint,
        "mcp_pair_endpoint": mcp_pair_endpoint,
        "mcp_chat_endpoint": mcp_chat_endpoint,
    }
