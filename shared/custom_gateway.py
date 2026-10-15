# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Custom API Gateway adapter for AutoYou.

Turns AutoYou into the communication software layer for any external application.
Seamlessly routes:
  • Audio Channel: Transcribed voice / STT, spoken replies via TTS, voice notes
  • Video Channel: Live camera frames, screen captures, image/video attachments
  • Chat: Multi-turn text conversations, streaming chunks, session continuity
  • Native DataChannel Commands: Command relay, control messages, bidirectional events
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import ssl as _ssl
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp

from shared.openclaw_gateway import (
    build_openai_compatible_url,
    extract_attachments_from_context,
    _extract_chat_completions_text,
    _extract_responses_text,
    safe_filename,
)

try:
    import certifi as _certifi
    _SSL_CTX = _ssl.create_default_context(cafile=_certifi.where())
except ImportError:
    _SSL_CTX = _ssl.create_default_context()

logger = logging.getLogger("shared.custom_gateway")

DEFAULT_CUSTOM_API_BASE = "http://127.0.0.1:8000"


def _normalize_custom_base(api_base: Optional[str]) -> str:
    base = str(api_base or os.environ.get("CUSTOM_API_BASE") or DEFAULT_CUSTOM_API_BASE).strip().rstrip("/")
    if not base.startswith("http://") and not base.startswith("https://"):
        base = f"http://{base}"
    return base


async def probe_custom(
    api_base: Optional[str] = None,
    token: str = "",
) -> Dict[str, Any]:
    """Probe the Custom API endpoint to check if the external application is running."""
    base = _normalize_custom_base(api_base)
    headers = {"Content-Type": "application/json"}
    auth_token = token or os.environ.get("CUSTOM_TOKEN", "").strip()
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    timeout = aiohttp.ClientTimeout(total=2.5)
    candidates = [
        f"{base}/v1/models",
        f"{base}/api/health",
        f"{base}/health",
        f"{base}/",
    ]

    for candidate_url in candidates:
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_SSL_CTX), timeout=timeout) as sess:
                async with sess.get(candidate_url, headers=headers) as resp:
                    if resp.status in (200, 204):
                        models = ["custom", "default"]
                        try:
                            data = await resp.json()
                            if isinstance(data, dict):
                                data_models = data.get("data") or data.get("models")
                                if isinstance(data_models, list):
                                    found = [m.get("id", "") if isinstance(m, dict) else str(m) for m in data_models]
                                    if found:
                                        models = found
                        except Exception:
                            pass
                        return {
                            "running": True,
                            "available": True,
                            "api_base": base,
                            "models": models,
                            "status_code": resp.status,
                            "endpoint": candidate_url,
                        }
        except Exception:
            continue

    return {
        "running": False,
        "available": False,
        "api_base": base,
        "error": f"Custom API application not responding at {base}. Ensure your application is running.",
    }


def _build_custom_multichannel_payload(
    message: str,
    *,
    model: str,
    session_id: str,
    context: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct a comprehensive payload carrying audio, video, chat, and control data."""
    meta = dict(metadata or {})
    is_voice = bool(meta.get("is_transcription") or meta.get("source") == "voice_call")
    attachments = extract_attachments_from_context(context)

    # Format user content parts
    content_parts: List[Dict[str, Any]] = []
    if message:
        content_parts.append({"type": "text", "text": message})

    image_parts: List[Dict[str, Any]] = []
    file_parts: List[Dict[str, Any]] = []
    audio_parts: List[Dict[str, Any]] = []

    for att in attachments:
        mt = str(att.get("mimetype") or "").lower()
        data = str(att.get("data") or att.get("base64") or "")
        url = str(att.get("url") or "")
        filename = att.get("filename") or "attachment"

        if mt.startswith("image/"):
            img_item = {
                "type": "image_url",
                "image_url": {"url": url if url else f"data:{mt};base64,{data}"},
                "filename": filename,
            }
            content_parts.append(img_item)
            image_parts.append(img_item)
        elif mt.startswith("audio/"):
            audio_item = {
                "type": "audio",
                "mime_type": mt,
                "data": data,
                "url": url,
                "filename": filename,
            }
            audio_parts.append(audio_item)
            content_parts.append({"type": "text", "text": f"[Attached Audio: {filename}]"})
        elif mt.startswith("video/"):
            video_item = {
                "type": "video",
                "mime_type": mt,
                "data": data,
                "url": url,
                "filename": filename,
            }
            file_parts.append(video_item)
            content_parts.append({"type": "text", "text": f"[Attached Video: {filename}]"})
        else:
            file_item = {
                "type": "file",
                "mime_type": mt,
                "data": data,
                "url": url,
                "filename": filename,
            }
            file_parts.append(file_item)
            content_parts.append({"type": "text", "text": f"[Attached File: {filename}]"})

    user_message: Dict[str, Any] = {
        "role": "user",
        "content": content_parts if len(content_parts) > 1 else message,
    }

    payload: Dict[str, Any] = {
        "model": model or "custom",
        "messages": [user_message],
        "stream": False,
        "user": session_id,
        "channels": {
            "chat": {"message": message},
            "audio": {"is_voice": is_voice, "audio_attachments": audio_parts},
            "video": {"video_frames": image_parts},
            "control": {
                "command": meta.get("command") or meta.get("conversation_action"),
                "metadata": meta,
            },
        },
        "metadata": meta,
    }

    return payload


async def call_custom_gateway(
    *,
    api_base: Optional[str] = None,
    model: str = "custom",
    message: str,
    session_id: str,
    token: str = "",
    context: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    on_chunk: Optional[Callable[[Dict[str, Any]], Any]] = None,
    timeout: float = 120.0,
) -> Optional[Dict[str, Any]]:
    """Call external Custom API application bridging chat, voice audio, video, and datachannel commands."""
    base = _normalize_custom_base(api_base)
    auth_token = token or os.environ.get("CUSTOM_TOKEN", "").strip()
    target_model = model or os.environ.get("CUSTOM_MODEL", "custom").strip() or "custom"

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "x-openclaw-session-key": session_id,
        "x-autoyou-channel": "custom-communication-gateway",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    payload = _build_custom_multichannel_payload(
        message,
        model=target_model,
        session_id=session_id,
        context=context,
        metadata=metadata,
    )

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    target_urls = [
        f"{base}/v1/chat/completions",
        f"{base}/api/chat",
        f"{base}/v1/responses",
        f"{base}/",
    ]

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_SSL_CTX), timeout=client_timeout) as sess:
        for url in target_urls:
            try:
                async with sess.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 404 and url != target_urls[-1]:
                        continue
                    if resp.status >= 400:
                        err_text = await resp.text()
                        logger.warning("Custom API call to %s returned HTTP %s: %s", url, resp.status, err_text[:300])
                        return {
                            "response": f"Custom API returned HTTP {resp.status}: {err_text[:200]}",
                            "agent_name": "Custom API",
                            "finish_reason": "error",
                            "usage": {},
                            "provider": "custom",
                        }

                    data = await resp.json()
                    reply = ""
                    commands: List[Dict[str, Any]] = []

                    # Parse OpenAI / OpenResponses / simple JSON format
                    if isinstance(data, dict):
                        # Extract choices
                        choices = data.get("choices")
                        if isinstance(choices, list) and choices:
                            first = choices[0]
                            if isinstance(first, dict):
                                msg = first.get("message") or {}
                                reply = str(msg.get("content") or "")
                        # Extract output text
                        if not reply and "output" in data:
                            reply = _extract_responses_text(data)
                        # Extract direct response or message
                        if not reply:
                            reply = str(data.get("response") or data.get("message") or data.get("text") or "")

                        # Extract control / datachannel commands from response
                        if "commands" in data and isinstance(data["commands"], list):
                            commands = data["commands"]
                        elif "command" in data:
                            commands = [data["command"]]

                        # Also check embedded JSON command blocks in reply
                        if not commands and reply and "```" in reply:
                            try:
                                import re
                                for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.DOTALL):
                                    parsed = json.loads(block)
                                    if isinstance(parsed, dict) and ("command" in parsed or "type" in parsed):
                                        commands.append(parsed)
                            except Exception:
                                pass

                    if not reply and isinstance(data, str):
                        reply = data

                    if on_chunk and reply:
                        res = on_chunk({"content": reply, "type": "content"})
                        if asyncio.iscoroutine(res):
                            await res

                    return {
                        "response": reply or "Custom API processed the request.",
                        "agent_name": f"Custom API ({target_model})",
                        "finish_reason": "stop",
                        "usage": data.get("usage", {}) if isinstance(data, dict) else {},
                        "provider": "custom",
                        "openclaw_endpoint": url,
                        "commands": commands,
                        "attachments_forwarded": len(context or []),
                        "attachments_skipped": 0,
                    }
            except aiohttp.ClientConnectorError:
                continue
            except Exception as exc:
                logger.error("Custom API request to %s failed: %s", url, exc)
                continue

    logger.error("Could not reach Custom API at %s on any endpoint", base)
    return None
