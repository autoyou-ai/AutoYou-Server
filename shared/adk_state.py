# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-52118627eb06f6866253f8ad

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


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
