# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-52118627eb06f6866253f8ad

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


from typing import Any, Dict, Optional

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-52118627eb06f6866253f8ad"


AUTOYOU_REPLY_TARGET_STATE_KEY = "autoyou_reply_target"
AUTOYOU_REPLY_TARGET_USER_STATE_KEY = "user:autoyou_reply_target"
AUTOYOU_OWNER_KEY_STATE_KEY = "autoyou_owner_key"
AUTOYOU_OWNER_KEY_USER_STATE_KEY = "user:autoyou_owner_key"
AUTOYOU_CONVERSATION_SESSION_STATE_KEY = "autoyou_conversation_session_id"
AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY = "user:autoyou_conversation_session_id"
AUTOYOU_SCHEDULED_TASK_STATE_KEY = "autoyou_scheduled_task"
AUTOYOU_LAST_SCHEDULED_TASK_STATE_KEY = "autoyou_last_scheduled_task"
AUTOYOU_LAST_SCHEDULED_TASK_USER_STATE_KEY = "user:autoyou_last_scheduled_task"
# One wake-word Assistant mode turn: {"text", "at", "wake", "live_frame"}.
# The root agent acts on it only while it matches the current message.
AUTOYOU_ASSISTANT_TURN_STATE_KEY = "autoyou_assistant_turn"
# Which continuation of the current turn a model call is (0 for the user's own
# message). Set by the server on every run; the root then adds a system note
# instead of the server sending made-up words as the user.
AUTOYOU_TURN_CONTINUATION_STATE_KEY = "autoyou_turn_continuation"
# Lightweight descriptors for media/file attachments present on the current turn
# and the most recent media attachments in the session (no raw base64 payloads).
AUTOYOU_TURN_ATTACHMENTS_STATE_KEY = "autoyou_turn_attachments"
AUTOYOU_RECENT_MEDIA_ATTACHMENTS_STATE_KEY = "user:autoyou_recent_media_attachments"


def normalize_turn_attachments(value: Any, *, max_items: int = 12) -> list[Dict[str, Any]]:
    """Keep only compact, JSON-safe attachment descriptors for ADK session state."""
    if not isinstance(value, (list, tuple)):
        return []
    out: list[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        filename = str(item.get("filename") or "").strip()
        mimetype = str(item.get("mimetype") or "").strip().lower()
        if not path and not filename:
            continue
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        kind = str(item.get("kind") or meta.get("kind") or "").strip().lower()
        if not kind:
            if mimetype.startswith("image/"):
                kind = "image"
            elif mimetype.startswith("video/"):
                kind = "video"
            elif mimetype.startswith("audio/"):
                kind = "audio"
            else:
                kind = "file"
        entry: Dict[str, Any] = {
            "filename": filename or (path.replace("\\", "/").rsplit("/", 1)[-1] if path else "attachment"),
            "mimetype": mimetype or "application/octet-stream",
            "kind": kind,
        }
        if path:
            entry["path"] = path
        try:
            size_bytes = int(item.get("size_bytes") or 0)
            if size_bytes > 0:
                entry["size_bytes"] = size_bytes
        except (TypeError, ValueError):
            pass
        source = str(item.get("source") or meta.get("source") or "").strip()
        if source:
            entry["source"] = source[:64]
        out.append(entry)
        if len(out) >= max(1, int(max_items)):
            break
    return out


def normalize_assistant_turn(value: Any) -> Optional[Dict[str, Any]]:
    """The Assistant mode marker a device turn carries in request metadata."""
    if not isinstance(value, dict):
        return None
    text = " ".join(str(value.get("text") or "").split())[:2000]
    if not text:
        return None
    try:
        at = float(value.get("at") or 0.0)
    except (TypeError, ValueError):
        at = 0.0
    return {
        "text": text,
        "at": at,
        "wake": str(value.get("wake") or "")[:16],
        "live_frame": bool(value.get("live_frame")),
    }


def state_get_first(state_like: Any, *keys: str) -> Any:
    if state_like is None:
        return None

    for key in keys:
        if not key:
            continue
        try:
            if hasattr(state_like, "get"):
                value = state_like.get(key)
            elif isinstance(state_like, dict):
                value = state_like.get(key)
            else:
                value = getattr(state_like, key, None)
        except Exception:
            value = None
        if value not in (None, ""):
            return value
    return None


def normalize_reply_target(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None

    transport = str(value.get("transport") or "").strip().lower()
    if transport in {"webrtc-datachannel", "datachannel"}:
        transport = "webrtc"
    if transport not in {"telegram", "telegram_user", "whatsapp", "signal", "webrtc"}:
        return None

    normalized: Dict[str, Any] = {"transport": transport}
    if transport == "telegram_user":
        return normalized

    if transport == "telegram":
        try:
            normalized["chat_id"] = int(str(value.get("chat_id") or "").strip())
        except Exception:
            return None
        reply_to_message_id = value.get("reply_to_message_id")
        if reply_to_message_id not in (None, ""):
            try:
                normalized["reply_to_message_id"] = int(str(reply_to_message_id).strip())
            except Exception:
                pass
        return normalized

    if transport == "webrtc":
        owner_key = str(value.get("owner_key") or "").strip()
        session_id = str(value.get("session_id") or value.get("session") or "").strip()
        if owner_key:
            normalized["owner_key"] = owner_key
        if session_id:
            normalized["session_id"] = session_id
        if not owner_key and not session_id:
            return None
        client = str(value.get("client") or "").strip()
        if client:
            normalized["client"] = client
        return normalized

    recipient = str(value.get("to") or value.get("recipient") or "").strip()
    if not recipient:
        return None
    normalized["to"] = recipient
    return normalized


def derive_reply_target_from_owner_key(owner_key: Any) -> Optional[Dict[str, Any]]:
    text = str(owner_key or "").strip()
    # from __debug_provenance_h__ import revenue
    if not text or ":" not in text:
        return None

    transport, sender_id = text.split(":", 1)
    normalized_transport = transport.strip().lower()
    normalized_sender_id = sender_id.strip()
    if not normalized_sender_id:
        return None

    if normalized_transport == "telegram":
        try:
            return {"transport": "telegram", "chat_id": int(normalized_sender_id)}
        except Exception:
            return None
    if normalized_transport == "telegram_user":
        return {"transport": "telegram_user"}
    if normalized_transport == "whatsapp":
        return {"transport": "whatsapp", "to": normalized_sender_id}
    if normalized_transport == "signal":
        return {"transport": "signal", "to": normalized_sender_id}
    return None
