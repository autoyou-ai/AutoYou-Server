# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""One way for any agent to send the user another message.

A follow-up goes to the server's outbox (`POST /api/outbox/follow-up`), which
delivers it into the conversation it belongs to: live when the device is
connected, otherwise by push and when the device reconnects, through the
scheduler's notification queue. Agents used to post to `/api/webrtc/send`
themselves, which reaches a device only while it is connected.

`send_follow_up` is the model-facing tool: it lets a turn reply more than once
("I've started it; this takes a minute"), with the normal reply still ending
the turn. Background jobs that finish after the turn call `post_follow_up` with
the target they saved when they started.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from typing import Any, Dict, Optional

OUTBOX_PATH = "/api/outbox/follow-up"
_MAX_FOLLOW_UP_CHARS = 4000


def follow_up_target(tool_context: Any) -> Dict[str, Any]:
    """Where a follow-up from this turn goes, read from the turn's session state."""
    from autoyou_agents.tasks_agent.agent import (
        _extract_canonical_session_id,
        _extract_conversation_session_id,
        _extract_delivery_target,
        _extract_owner_key,
    )
    from shared.session_execution import build_canonical_user_id

    owner_key = _extract_owner_key(tool_context)
    target: Dict[str, Any] = {
        "owner_key": owner_key,
        "canonical_user_id": build_canonical_user_id(owner_key) if owner_key else None,
        "external_session_id": _extract_canonical_session_id(tool_context),
        "conversation_session_id": _extract_conversation_session_id(tool_context),
        "reply_target": _extract_delivery_target(tool_context, owner_key),
    }
    return {key: value for key, value in target.items() if value}


def post_follow_up(
    message: str,
    *,
    tool_context: Any = None,
    target: Optional[Dict[str, Any]] = None,
    source: str = "follow_up",
) -> Dict[str, Any]:
    """Hand a message to the server's outbox for this conversation."""
    text = str(message or "").strip()
    if not text:
        return {"status": "error", "message": "A follow-up needs a message."}
    destination = dict(target) if target is not None else follow_up_target(tool_context)
    if not destination.get("owner_key") and not destination.get("reply_target"):
        return {"status": "skipped", "message": "This conversation has no address to send a follow-up to."}

    from autoyou_agents.admin_agent.admin_tool import _get_internal_ai_agent_api_token, _http

    token = _get_internal_ai_agent_api_token()
    if not token:
        return {"status": "error", "message": "The internal AI agent token is not available."}
    result = _http(
        "POST",
        OUTBOX_PATH,
        {"message": text[:_MAX_FOLLOW_UP_CHARS], "source": source, **destination},
        timeout=30,
        token=token,
    )
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if result.get("status") != "success" or not data.get("success", True):
        return {"status": "error", "message": str(data.get("error") or data.get("message") or "The outbox refused it.")}
    return {
        "status": "success",
        "delivery": "queued" if data.get("queued") else "delivered",
        "message": str(data.get("message") or "Sent."),
    }


def send_follow_up(message: str, tool_context: Any) -> Dict[str, Any]:
    """Send the user another message in this conversation, separate from your reply.

    Use it to tell the user something now while you keep working ("This takes a
    minute; I'll post the result here."). It arrives in this conversation right
    away when their device is connected, otherwise when it reconnects. Your
    normal reply still ends the turn, so do not repeat the same text there.

    Args:
        message: The text to send, in the user's language.
    """
    return post_follow_up(message, tool_context=tool_context, source="send_follow_up")
