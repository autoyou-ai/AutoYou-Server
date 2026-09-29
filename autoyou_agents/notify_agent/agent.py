# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-4a6414f5a594dc69216ed302


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from autoyou_agents.shared_tools.datetime_tool import get_current_datetime
from shared.adk_state import (
    AUTOYOU_CONVERSATION_SESSION_STATE_KEY,
    AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY,
    AUTOYOU_OWNER_KEY_STATE_KEY,
    AUTOYOU_OWNER_KEY_USER_STATE_KEY,
    AUTOYOU_REPLY_TARGET_STATE_KEY,
    AUTOYOU_REPLY_TARGET_USER_STATE_KEY,
    derive_reply_target_from_owner_key,
    normalize_reply_target,
    state_get_first,
)
from shared.session_execution import SESSION_CONTROL_STATE_KEY, normalize_session_control_state
from shared.scheduler_service import (
    REMINDERS_FILE,
    append_scheduler_activity_log,
    load_json,
    save_json,
)

__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-breach-4a6414f5a594dc69216ed302"


def _extract_runtime_identifiers(tool_context: Optional[Any]) -> Tuple[Optional[str], Optional[str]]:
    if tool_context is None:
        return None, None

    candidates = [
        tool_context,
        getattr(tool_context, "_invocation_context", None),
        getattr(tool_context, "invocation_context", None),
        getattr(tool_context, "context", None),
    ]
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    for candidate in candidates:
        if candidate is None:
            continue
        if not user_id:
            for attribute in ("user_id", "userId"):
                raw_value = getattr(candidate, attribute, None)
                if raw_value is not None:
                    text = str(raw_value).strip()
                    if text:
                        user_id = text
                        break
        if not session_id:
            session_obj = getattr(candidate, "session", None)
            raw_session_id = getattr(candidate, "session_id", None)
            if raw_session_id is None and session_obj is not None:
                raw_session_id = getattr(session_obj, "id", None)
            if raw_session_id is not None:
                text = str(raw_session_id).strip()
                if text:
                    session_id = text
        if user_id and session_id:
            break
    return user_id, session_id


def _extract_owner_key(tool_context: Optional[Any]) -> Optional[str]:
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    control_state = normalize_session_control_state(state_get_first(state, SESSION_CONTROL_STATE_KEY))
    owner_key = str(
        control_state.get("owner_key")
        or state_get_first(state, AUTOYOU_OWNER_KEY_STATE_KEY, AUTOYOU_OWNER_KEY_USER_STATE_KEY)
        or ""
    ).strip()
    return owner_key or None


def _extract_canonical_session_id(tool_context: Optional[Any]) -> Optional[str]:
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    control_state = normalize_session_control_state(state_get_first(state, SESSION_CONTROL_STATE_KEY))
    canonical_session_id = str(control_state.get("canonical_session_id") or "").strip()
    return canonical_session_id or None


def _extract_conversation_session_id(tool_context: Optional[Any]) -> Optional[str]:
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    # from __debug_provenance_o__ import breach
    conversation_session_id = str(
        state_get_first(
            state,
            AUTOYOU_CONVERSATION_SESSION_STATE_KEY,
            AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY,
        )
        or ""
    ).strip()
    return conversation_session_id or None


def _extract_delivery_target(tool_context: Optional[Any], owner_key: Optional[str]) -> Optional[Dict[str, Any]]:
    if tool_context is None:
        return derive_reply_target_from_owner_key(owner_key)
    state = getattr(tool_context, "state", None)
    reply_target = normalize_reply_target(
        state_get_first(
            state,
            AUTOYOU_REPLY_TARGET_STATE_KEY,
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY,
        )
    )
    if reply_target:
        return reply_target
    return derive_reply_target_from_owner_key(owner_key)


def _parse_notification_schedule(
    *,
    target_time_iso: Optional[str] = None,
    relative_minutes_from_now: Optional[float] = None,
) -> Tuple[float, str]:
    from datetime import datetime, timedelta, timezone

    if relative_minutes_from_now is not None:
        dt = datetime.now(timezone.utc) + timedelta(minutes=float(relative_minutes_from_now))
        return dt.timestamp(), dt.isoformat()

    if target_time_iso is None:
        raise ValueError("Must provide either target_time_iso or relative_minutes_from_now.")

    dt = datetime.fromisoformat(str(target_time_iso).replace("Z", "+00:00"))
    return dt.timestamp(), dt.isoformat()


def create_reminder(
    message: str,
    target_time_iso: Optional[str] = None,
    relative_minutes_from_now: Optional[float] = None,
    tool_context: Optional[Any] = None,
) -> dict:
    """Schedule a timed notification to be sent to the user.

    Args:
        message: The notification message to send.
        target_time_iso: Scheduled time as ISO-8601 string (e.g. 2026-04-12T15:30:00Z).
        relative_minutes_from_now: Minutes from now to send the notification.
    """
    try:
        timestamp_s, normalized_iso = _parse_notification_schedule(
            target_time_iso=target_time_iso,
            relative_minutes_from_now=relative_minutes_from_now,
        )
    except Exception as exc:
        return {"status": "error", "message": f"Couldn't parse time: {exc}"}

    normalized_message = str(message or "").strip()
    if not normalized_message:
        return {"status": "error", "message": "Notification message is required."}

    creator_user_id, creator_session_id = _extract_runtime_identifiers(tool_context)
    owner_key = _extract_owner_key(tool_context)
    canonical_session_id = _extract_canonical_session_id(tool_context)
    conversation_session_id = _extract_conversation_session_id(tool_context)
    delivery_target = _extract_delivery_target(tool_context, owner_key)

    reminders = load_json(REMINDERS_FILE)
    entry_id = str(uuid.uuid4())[:8]
    entry: Dict[str, Any] = {
        "id": entry_id,
        "message": normalized_message,
        "timestamp_s": timestamp_s,
        "iso": normalized_iso,
        "created_at_s": time.time(),
        "updated_at_s": time.time(),
    }
    if creator_user_id:
        entry["creator_user_id"] = creator_user_id
    if creator_session_id:
        entry["creator_session_id"] = creator_session_id
    if owner_key:
        entry["creator_owner_key"] = owner_key
    if canonical_session_id:
        entry["creator_external_session_id"] = canonical_session_id
    if conversation_session_id:
        entry["creator_conversation_session_id"] = conversation_session_id
    if delivery_target:
        entry["delivery_target"] = delivery_target

    reminders.append(entry)
    save_json(REMINDERS_FILE, reminders)
    append_scheduler_activity_log(
        "reminder_created",
        agent_kind="notify",
        item_id=entry_id,
        status="created",
        message=normalized_message,
        details={"timestamp_s": timestamp_s, "owner_key": str(owner_key or "")},
    )
    return {
        "status": "success",
        "message": f"Notification '{normalized_message}' scheduled for {normalized_iso}",
        "id": entry_id,
    }


def list_reminders(tool_context: Optional[Any] = None, include_all: bool = False) -> dict:
    """List all currently scheduled notifications for the current user."""
    reminders = load_json(REMINDERS_FILE)
    current_user_id, _ = _extract_runtime_identifiers(tool_context)
    if include_all or not current_user_id:
        return {"reminders": reminders, "scope": "all", "hidden_count": 0}

    visible = []
    hidden_count = 0
    for r in reminders:
        owner_user_id = str(r.get("creator_user_id") or "").strip()
        if owner_user_id and owner_user_id != current_user_id:
            hidden_count += 1
            continue
        visible.append(r)
    return {"reminders": visible, "scope": "current_user", "hidden_count": hidden_count}


def delete_reminder(id: str, tool_context: Optional[Any] = None, include_all: bool = False) -> dict:
    """Delete a scheduled notification by its unique ID.

    Args:
        id: The unique ID of the notification to delete.
    """
    reminders = load_json(REMINDERS_FILE)
    current_user_id, _ = _extract_runtime_identifiers(tool_context)
    updated = []
    removed = False
    blocked = False
    for r in reminders:
        if r.get("id") != id:
            updated.append(r)
            continue
        owner_user_id = str(r.get("creator_user_id") or "").strip()
        if include_all or not current_user_id or not owner_user_id or owner_user_id == current_user_id:
            removed = True
            continue
        blocked = True
        updated.append(r)

    if not removed:
        if blocked:
            return {"status": "error", "message": f"Notification {id} belongs to a different user."}
        return {"status": "error", "message": f"No notification found with ID {id}"}
    save_json(REMINDERS_FILE, updated)
    append_scheduler_activity_log(
        "reminder_deleted",
        agent_kind="notify",
        item_id=str(id or "").strip() or None,
        status="deleted",
        message=f"Deleted notification {id}.",
    )
    return {"status": "success", "message": f"Notification {id} removed."}


def update_reminder(
    id: str,
    message: Optional[str] = None,
    target_time_iso: Optional[str] = None,
    relative_minutes_from_now: Optional[float] = None,
    tool_context: Optional[Any] = None,
    include_all: bool = False,
) -> dict:
    """Update a notification's message or scheduled time."""
    entry_id = str(id or "").strip()
    if not entry_id:
        return {"status": "error", "message": "Notification ID is required."}

    normalized_message = None
    if message is not None:
        normalized_message = str(message or "").strip()
        if not normalized_message:
            return {"status": "error", "message": "Notification message cannot be empty."}

    update_time = target_time_iso is not None or relative_minutes_from_now is not None
    timestamp_s = None
    normalized_iso = None
    if update_time:
        try:
            timestamp_s, normalized_iso = _parse_notification_schedule(
                target_time_iso=target_time_iso,
                relative_minutes_from_now=relative_minutes_from_now,
            )
        except Exception as exc:
            return {"status": "error", "message": f"Couldn't parse time: {exc}"}

    if normalized_message is None and not update_time:
        return {"status": "error", "message": "No changes were provided."}

    reminders = load_json(REMINDERS_FILE)
    current_user_id, _ = _extract_runtime_identifiers(tool_context)
    updated_entry: Optional[Dict[str, Any]] = None
    blocked = False

    for reminder in reminders:
        if str(reminder.get("id") or "").strip() != entry_id:
            continue
        owner_user_id = str(reminder.get("creator_user_id") or "").strip()
        if not (include_all or not current_user_id or not owner_user_id or owner_user_id == current_user_id):
            blocked = True
            break
        if normalized_message is not None:
            reminder["message"] = normalized_message
        if update_time and timestamp_s is not None and normalized_iso is not None:
            reminder["timestamp_s"] = timestamp_s
            reminder["iso"] = normalized_iso
        reminder["updated_at_s"] = time.time()
        updated_entry = reminder
        break

    if updated_entry is None:
        if blocked:
            return {"status": "error", "message": f"Notification {entry_id} belongs to a different user."}
        return {"status": "error", "message": f"No notification found with ID {entry_id}"}

    save_json(REMINDERS_FILE, reminders)
    append_scheduler_activity_log(
        "reminder_updated",
        agent_kind="notify",
        item_id=entry_id,
        status="updated",
        message=str(updated_entry.get("message") or ""),
        details={
            "timestamp_s": float(updated_entry.get("timestamp_s") or 0.0),
            "owner_key": str(updated_entry.get("creator_owner_key") or ""),
        },
    )
    return {"status": "success", "message": f"Notification {entry_id} updated.", "reminder": updated_entry}


def delete_all_reminders(tool_context: Optional[Any] = None, include_all: bool = False) -> dict:
    """Delete every visible notification for the current user."""
    reminders = load_json(REMINDERS_FILE)
    current_user_id, _ = _extract_runtime_identifiers(tool_context)
    retained = []
    removed_ids = []

    for reminder in reminders:
        owner_user_id = str(reminder.get("creator_user_id") or "").strip()
        if include_all or not current_user_id or not owner_user_id or owner_user_id == current_user_id:
            removed_ids.append(str(reminder.get("id") or "").strip())
            continue
        retained.append(reminder)

    if not removed_ids:
        return {"status": "success", "message": "No notifications matched the current scope.", "removed_count": 0}

    save_json(REMINDERS_FILE, retained)
    append_scheduler_activity_log(
        "reminder_bulk_deleted",
        agent_kind="notify",
        status="deleted",
        message=f"Deleted {len(removed_ids)} notification(s).",
        details={"removed_ids": removed_ids[:50]},
    )
    return {
        "status": "success",
        "message": f"Removed {len(removed_ids)} notification(s).",
        "removed_count": len(removed_ids),
    }


def get_tools() -> list:
    return [
        get_current_datetime,
        create_reminder,
        list_reminders,
        update_reminder,
        delete_reminder,
        delete_all_reminders,
    ]


async def _notify_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    from autoyou_agents.shared_tools.datetime_tool import inject_realtime_datetime_into_request
    inject_realtime_datetime_into_request(llm_request)
    return None


def create_notify_agent(model_config: Any) -> Any:
    from google.adk.agents import Agent
    from autoyou_agents.notify_agent.prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=get_tools(),
        before_model_callback=_notify_before_model_callback,
    )
