# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-81ac87e647209601697f713e


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import re
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

from shared.adk_state import (
    AUTOYOU_CONVERSATION_SESSION_STATE_KEY,
    AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY,
    AUTOYOU_LAST_SCHEDULED_TASK_STATE_KEY,
    AUTOYOU_LAST_SCHEDULED_TASK_USER_STATE_KEY,
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
    TASKS_FILE,
    append_scheduler_activity_log,
    cancel_active_task_execution,
    load_json,
    save_json,
)

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-81ac87e647209601697f713e"


_SEND_MESSAGE_PATTERN = re.compile(
    r"\s*send(?:\s+the)?(?:\s+exact)?\s+message\s*:\s*(?P<quote>['\"])(?P<message>.+?)(?P=quote)\.?\s*",
    re.IGNORECASE | re.DOTALL,
)


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


def _extract_send_message_action(instruction: str) -> Optional[Dict[str, str]]:
    match = _SEND_MESSAGE_PATTERN.fullmatch(str(instruction or ""))
    if not match:
        return None
    message = str(match.group("message") or "").strip()
    if not message:
        return None
    return {"type": "send_message", "message": message}


def _store_last_task_state(tool_context: Optional[Any], task: Dict[str, Any]) -> None:
    if tool_context is None:
        return
    snapshot = {
        "id": task.get("id"),
        "instruction": task.get("instruction"),
        "interval_minutes": task.get("interval_minutes"),
        "enabled": bool(task.get("enabled", True)),
    }
    # from __debug_provenance_i__ import or
    try:
        tool_context.state[AUTOYOU_LAST_SCHEDULED_TASK_STATE_KEY] = snapshot
        tool_context.state[AUTOYOU_LAST_SCHEDULED_TASK_USER_STATE_KEY] = snapshot
    except Exception:
        pass


def _coerce_enabled_flag(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError("enabled must be a boolean value.")

def create_cron_task(
    instruction: str,
    interval_minutes: float = 0,
    run_once: bool = False,
    run_once_at_iso: Optional[str] = None,
    tool_context: Optional[Any] = None,
) -> dict:
    """Schedule a task to be executed by the AI - either recurring or one-time.

    Args:
        instruction: A clear command for the AI to execute.
        interval_minutes: How often to run the task in minutes (for recurring tasks). Use 0 or omit when run_once=True.
        run_once: If True, the task runs a single time and then disables itself automatically.
        run_once_at_iso: ISO-8601 datetime to run the one-time task (e.g. 2026-05-10T09:00:00Z). If omitted, runs ASAP.
    """
    is_run_once = bool(run_once) or interval_minutes == 0
    if not is_run_once and interval_minutes < 1:
        return {"status": "error", "message": "Interval must be at least 1 minute for recurring tasks."}

    normalized_instruction = str(instruction or "").strip()
    if not normalized_instruction:
        return {"status": "error", "message": "Instruction is required."}

    tasks = load_json(TASKS_FILE)
    task_id = str(uuid.uuid4())[:8]
    creator_user_id, creator_ai_session_id = _extract_runtime_identifiers(tool_context)
    owner_key = _extract_owner_key(tool_context)
    canonical_session_id = _extract_canonical_session_id(tool_context)
    conversation_session_id = _extract_conversation_session_id(tool_context)
    delivery_target = _extract_delivery_target(tool_context, owner_key)
    action = _extract_send_message_action(normalized_instruction)

    # For run-once tasks: if a future run-at time is given, set last_run_s so the
    # scheduler fires at that time; otherwise set to 0 so it fires immediately.
    if is_run_once and run_once_at_iso:
        try:
            from datetime import datetime, timezone as _tz
            _dt = datetime.fromisoformat(str(run_once_at_iso).replace("Z", "+00:00"))
            _last_run = _dt.timestamp()
        except Exception:
            _last_run = 0.0
    else:
        _last_run = 0.0 if is_run_once else time.time()

    task: Dict[str, Any] = {
        "id": task_id,
        "instruction": normalized_instruction,
        "interval_minutes": 0 if is_run_once else interval_minutes,
        "run_once": is_run_once,
        "last_run_s": _last_run,
        "scheduler_session_id": f"scheduled-task::{task_id}",
        "created_at_s": time.time(),
        "enabled": True,
    }
    if creator_user_id:
        task["creator_user_id"] = creator_user_id
    if creator_ai_session_id:
        task["creator_ai_session_id"] = creator_ai_session_id
    if owner_key:
        task["creator_owner_key"] = owner_key
    if canonical_session_id:
        task["creator_external_session_id"] = canonical_session_id
    if conversation_session_id:
        task["creator_conversation_session_id"] = conversation_session_id
    if delivery_target:
        task["delivery_target"] = delivery_target
    if action:
        task["action"] = action

    tasks.append(task)
    save_json(TASKS_FILE, tasks)
    append_scheduler_activity_log(
        "task_created",
        agent_kind="tasks",
        item_id=task_id,
        status="created",
        message=normalized_instruction,
        details={
            "interval_minutes": interval_minutes,
            "enabled": True,
            "owner_key": str(owner_key or ""),
        },
    )
    _store_last_task_state(tool_context, task)
    return {
        "status": "success",
        "message": f"Task registered to run every {interval_minutes} minutes.",
        "id": task_id,
        "task": task,
    }

def list_active_tasks(tool_context: Optional[Any] = None, include_all: bool = False) -> dict:
    """View all active scheduled recurring tasks currently running."""
    tasks = load_json(TASKS_FILE)
    current_user_id, _ = _extract_runtime_identifiers(tool_context)
    if include_all or not current_user_id:
        return {"tasks": tasks, "scope": "all", "hidden_count": 0}

    visible_tasks = []
    hidden_count = 0
    for task in tasks:
        owner_user_id = str(task.get("creator_user_id") or "").strip()
        if owner_user_id and owner_user_id != current_user_id:
            hidden_count += 1
            continue
        visible_tasks.append(task)
    return {"tasks": visible_tasks, "scope": "current_user", "hidden_count": hidden_count}

def delete_cron_task(id: str, tool_context: Optional[Any] = None, include_all: bool = False) -> dict:
    """Remove a recurring task by its unique ID.
    
    Args:
        id: The unique ID string of the task.
    """
    tasks = load_json(TASKS_FILE)
    current_user_id, _ = _extract_runtime_identifiers(tool_context)
    updated = []
    removed = False
    blocked = False
    for task in tasks:
        if task.get("id") != id:
            updated.append(task)
            continue
        owner_user_id = str(task.get("creator_user_id") or "").strip()
        if include_all or not current_user_id or not owner_user_id or owner_user_id == current_user_id:
            removed = True
            continue
        blocked = True
        updated.append(task)

    if not removed:
        if blocked:
            return {"status": "error", "message": f"Task {id} belongs to a different user."}
        return {"status": "error", "message": f"No task found with ID {id}"}
    save_json(TASKS_FILE, updated)
    cancel_active_task_execution(id)
    append_scheduler_activity_log(
        "task_deleted",
        agent_kind="tasks",
        item_id=str(id or "").strip() or None,
        status="deleted",
        message=f"Deleted scheduled task {id}.",
    )
    return {"status": "success", "message": f"Task {id} removed."}


def update_cron_task(
    id: str,
    instruction: Optional[str] = None,
    interval_minutes: Optional[float] = None,
    enabled: Optional[Any] = None,
    tool_context: Optional[Any] = None,
    include_all: bool = False,
) -> dict:
    """Update a recurring task's instruction, interval, or enabled state."""
    task_id = str(id or "").strip()
    if not task_id:
        return {"status": "error", "message": "Task ID is required."}

    normalized_instruction = None
    if instruction is not None:
        normalized_instruction = str(instruction or "").strip()
        if not normalized_instruction:
            return {"status": "error", "message": "Instruction cannot be empty."}

    normalized_interval = None
    if interval_minutes is not None:
        try:
            normalized_interval = float(interval_minutes)
        except (TypeError, ValueError):
            return {"status": "error", "message": "Interval must be numeric."}
        if normalized_interval < 1:
            return {"status": "error", "message": "Interval must be at least 1 minute."}

    try:
        normalized_enabled = _coerce_enabled_flag(enabled)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    if normalized_instruction is None and normalized_interval is None and normalized_enabled is None:
        return {"status": "error", "message": "No changes were provided."}

    tasks = load_json(TASKS_FILE)
    current_user_id, _ = _extract_runtime_identifiers(tool_context)
    updated_task: Optional[Dict[str, Any]] = None
    blocked = False

    for task in tasks:
        if str(task.get("id") or "").strip() != task_id:
            continue
        owner_user_id = str(task.get("creator_user_id") or "").strip()
        if not (include_all or not current_user_id or not owner_user_id or owner_user_id == current_user_id):
            blocked = True
            break

        if normalized_instruction is not None:
            task["instruction"] = normalized_instruction
            task.pop("recent_outputs", None)
            task.pop("last_result_preview", None)
        if normalized_interval is not None:
            task["interval_minutes"] = normalized_interval
        if normalized_enabled is not None:
            previous_enabled = bool(task.get("enabled", True))
            task["enabled"] = normalized_enabled
            if previous_enabled != normalized_enabled:
                task["last_run_s"] = time.time()
                if not normalized_enabled:
                    cancel_active_task_execution(task_id)
        task["updated_at_s"] = time.time()
        updated_task = task
        break

    if updated_task is None:
        if blocked:
            return {"status": "error", "message": f"Task {task_id} belongs to a different user."}
        return {"status": "error", "message": f"No task found with ID {task_id}"}

    save_json(TASKS_FILE, tasks)
    append_scheduler_activity_log(
        "task_updated",
        agent_kind="tasks",
        item_id=task_id,
        status="updated",
        message=str(updated_task.get("instruction") or ""),
        details={
            "interval_minutes": float(updated_task.get("interval_minutes") or 0),
            "enabled": bool(updated_task.get("enabled", True)),
        },
    )
    _store_last_task_state(tool_context, updated_task)
    return {"status": "success", "message": f"Task {task_id} updated.", "task": updated_task}


def delete_all_cron_tasks(tool_context: Optional[Any] = None, include_all: bool = False) -> dict:
    """Delete every visible recurring task for the current user."""
    tasks = load_json(TASKS_FILE)
    current_user_id, _ = _extract_runtime_identifiers(tool_context)
    retained_tasks = []
    removed_ids = []

    for task in tasks:
        owner_user_id = str(task.get("creator_user_id") or "").strip()
        if include_all or not current_user_id or not owner_user_id or owner_user_id == current_user_id:
            removed_ids.append(str(task.get("id") or "").strip())
            continue
        retained_tasks.append(task)

    if not removed_ids:
        return {"status": "success", "message": "No scheduled tasks matched the current scope.", "removed_count": 0}

    save_json(TASKS_FILE, retained_tasks)
    cancelled_count = sum(1 for task_id in removed_ids if cancel_active_task_execution(task_id))
    append_scheduler_activity_log(
        "task_bulk_deleted",
        agent_kind="tasks",
        status="deleted",
        message=f"Deleted {len(removed_ids)} scheduled task(s).",
        details={"removed_ids": removed_ids[:50], "cancelled_count": cancelled_count},
    )
    return {
        "status": "success",
        "message": f"Removed {len(removed_ids)} task(s).",
        "removed_count": len(removed_ids),
        "cancelled_count": cancelled_count,
    }

def get_tools() -> list:
    return [
        get_current_datetime,
        create_cron_task,
        list_active_tasks,
        update_cron_task,
        delete_cron_task,
        delete_all_cron_tasks,
    ]

async def _tasks_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    from autoyou_agents.shared_tools.datetime_tool import inject_realtime_datetime_into_request
    inject_realtime_datetime_into_request(llm_request)
    return None

def create_tasks_agent(model_config: Any) -> Any:
    from google.adk.agents import Agent
    from autoyou_agents.tasks_agent.prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=get_tools(),
        before_model_callback=_tasks_before_model_callback,
    )
