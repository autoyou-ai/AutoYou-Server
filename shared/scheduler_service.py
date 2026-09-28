# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-643937636335396144393239-b5a6169fb38dea13d3b30356


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-643937636335396144393239-b5a6169fb38dea13d3b30356"

import asyncio
import importlib
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
import weakref
from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Any, List, Optional

from shared.adk_state import derive_reply_target_from_owner_key, normalize_reply_target
from shared.platform_runtime import get_mutable_data_dir
from shared.secure_storage import (
    FILE_HEADER,
    SecureStorageError,
    load_secure_json,
    read_secure_file,
    save_secure_json,
    secure_storage_enabled,
    write_secure_file,
)

LOGGER = logging.getLogger(__name__)

def _runtime_path(*parts: str) -> str:
    return str(get_mutable_data_dir("AutoYou", anchor=Path(__file__)).joinpath(*parts))


REMINDERS_FILE = _runtime_path("reminders.json")
TASKS_FILE = _runtime_path("cron_tasks.json")
OUTBOUND_NOTIFICATION_QUEUE_FILE = _runtime_path("output", "scheduled_notification_queue.json")
SCHEDULER_MAX_SLEEP_SECONDS = 60.0
SCHEDULER_MIN_SLEEP_SECONDS = 1.0
SCHEDULER_ACTIVE_RETRY_SECONDS = 5.0
OUTBOUND_NOTIFICATION_BASE_BACKOFF_SECONDS = 15.0
OUTBOUND_NOTIFICATION_MAX_BACKOFF_SECONDS = 300.0
OUTBOUND_NOTIFICATION_DEFAULT_MAX_AGE_SECONDS = 30 * 60

def _get_outbound_notification_max_age_seconds() -> float:
    try:
        server = _get_runtime_server_module()
        config = server.STATE.config or server._default_config()
        return float(config.get("scheduler", {}).get("fallback_max_age_seconds", OUTBOUND_NOTIFICATION_DEFAULT_MAX_AGE_SECONDS))
    except Exception:
        return OUTBOUND_NOTIFICATION_DEFAULT_MAX_AGE_SECONDS

OUTBOUND_NOTIFICATION_MAX_ITEMS = 1000
SCHEDULER_ACTIVITY_LOG_FILE = _runtime_path("output", "scheduler_activity_log.jsonl")
SCHEDULER_ACTIVITY_LOG_MAX_ITEMS = 5000
SCHEDULED_TASK_RECENT_OUTPUT_LIMIT = 8
SCHEDULED_TASK_RECENT_OUTPUT_PREVIEW_CHARS = 280

_ACTIVE_TASK_IDS: set[str] = set()
_ACTIVE_TASK_HANDLES: Dict[str, asyncio.Task] = {}
_PENDING_NOTIFICATION_PROCESS_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()
_JSON_FILE_LOCKS_GUARD = threading.Lock()
_JSON_FILE_LOCKS: Dict[str, threading.RLock] = {}
_DOWNGRADED_STORE_WARNINGS: set[str] = set()

_LITERAL_JOKE_TASK_PATTERN = re.compile(
    r"\s*send(?:\s+me)?(?:\s+the)?\s+joke\s*:\s*(?P<quote>['\"])(?P<joke>.+?)(?P=quote)\.?\s*",
    re.IGNORECASE | re.DOTALL,
)
_SYSTEM_CLOCK_PLACEHOLDER_PATTERN = re.compile(r"\[(?:SYSTEM[ _]CLOCK)\]", re.IGNORECASE)
_LIVE_DATA_TASK_PATTERN = re.compile(
    r"\b(?:current|latest|recent|today(?:'s)?|live|online|external|news|headlines?|"
    r"weather|prices?|scores?|availability|releases?|internet|websites?|webpages?|urls?)\b",
    re.IGNORECASE,
)


def _format_local_timestamp(timestamp_s: Any) -> str:
    moment = datetime.fromtimestamp(_normalize_timestamp(timestamp_s, time.time())).astimezone()
    date_text = moment.strftime("%A, %B %d, %Y")
    time_text = moment.strftime("%I:%M %p").lstrip("0")
    tz_text = str(moment.tzname() or "").strip()
    if tz_text:
        return f"{date_text} {time_text} {tz_text}"
    return f"{date_text} {time_text}"


def _current_system_clock_display() -> str:
    return _format_local_timestamp(time.time())


def _replace_system_clock_placeholders(text: str) -> str:
    rendered = str(text or "")
    if not rendered or not _SYSTEM_CLOCK_PLACEHOLDER_PATTERN.search(rendered):
        return rendered
    return _SYSTEM_CLOCK_PLACEHOLDER_PATTERN.sub(_current_system_clock_display(), rendered)


def _json_file_lock(filepath: str) -> threading.RLock:
    resolved = os.path.abspath(str(filepath or ""))
    with _JSON_FILE_LOCKS_GUARD:
        lock = _JSON_FILE_LOCKS.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _JSON_FILE_LOCKS[resolved] = lock
        return lock


def _scheduler_store_path(filepath: str) -> str:
    """Keep working in a fresh sibling store when legacy Maximus data is sealed.

    A mode changed outside the supported downgrade flow can leave old scheduler
    files encrypted. Never open or replace those files without the Maximus key.
    Once created, the sibling remains the active store across later mode changes.
    """
    path = Path(filepath)

    def protected(candidate: Path) -> bool:
        if not candidate.is_file():
            return False
        try:
            with candidate.open("rb") as handle:
                return handle.read(len(FILE_HEADER)) == FILE_HEADER
        except OSError:
            return False

    index = 1
    latest_fallback = None
    while True:
        suffix = "" if index == 1 else f"-{index}"
        candidate = path.with_name(f"{path.stem}.secure-professional{suffix}{path.suffix}")
        if not candidate.exists():
            next_fallback = candidate
            break
        latest_fallback = candidate
        index += 1

    if secure_storage_enabled():
        return str(latest_fallback or path)
    if latest_fallback:
        if not protected(latest_fallback):
            return str(latest_fallback)
        fresh_path = next_fallback
    elif not path.is_file():
        return str(path)
    elif not protected(path):
        return str(path)
    else:
        fresh_path = next_fallback

    normalized = str(path.resolve())
    if normalized not in _DOWNGRADED_STORE_WARNINGS:
        _DOWNGRADED_STORE_WARNINGS.add(normalized)
        LOGGER.warning(
            "Scheduler is preserving the Maximus-protected %s and using a fresh sibling store",
            path.name,
        )
    return str(fresh_path)


def _unique_json_temp_path(filepath: str) -> str:
    return f"{filepath}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"


def _pending_notification_process_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _PENDING_NOTIFICATION_PROCESS_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _PENDING_NOTIFICATION_PROCESS_LOCKS[loop] = lock
    return lock


def _normalize_task_result_text(text: Any) -> str:
    return _replace_system_clock_placeholders(str(text or "")).strip()


def _format_task_result_message(result_text: Any, *, completed_at_s: Any) -> str:
    normalized_result = _normalize_task_result_text(result_text)
    if not normalized_result:
        return ""
    completed_at_text = _format_local_timestamp(completed_at_s)
    return f"✅ Task Result (completed at {completed_at_text}):\n{normalized_result}"

def load_json(filepath: str) -> Any:
    filepath = _scheduler_store_path(filepath)
    if not os.path.exists(filepath):
        return []
    try:
        with _json_file_lock(filepath):
            payload = load_secure_json(filepath, default=[])
            return payload if isinstance(payload, (list, dict)) else []
    except SecureStorageError:
        raise
    except Exception as e:
        LOGGER.error(f"Error loading {filepath}: {e}")
        return []

def save_json(filepath: str, data: Any):
    filepath = _scheduler_store_path(filepath)
    try:
        with _json_file_lock(filepath):
            save_secure_json(filepath, data)
    except SecureStorageError:
        raise
    except Exception as e:
        LOGGER.error(f"Error saving {filepath}: {e}")


def append_scheduler_activity_log(
    event_type: str,
    *,
    agent_kind: str,
    item_id: Optional[str] = None,
    status: Optional[str] = None,
    message: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    record: Dict[str, Any] = {
        "timestamp_s": time.time(),
        "event_type": str(event_type or "").strip() or "unknown",
        "agent_kind": str(agent_kind or "").strip() or "scheduler",
    }
    if item_id:
        record["item_id"] = str(item_id).strip()
    if status:
        record["status"] = str(status).strip()
    if message:
        record["message"] = str(message).strip()
    if isinstance(details, dict) and details:
        record["details"] = details

    log_file = _scheduler_store_path(SCHEDULER_ACTIVITY_LOG_FILE)
    try:
        with _json_file_lock(log_file):
            existing_lines: deque[str] = deque(maxlen=SCHEDULER_ACTIVITY_LOG_MAX_ITEMS)
            if os.path.exists(log_file):
                text = read_secure_file(log_file).decode("utf-8")
                for raw_line in text.splitlines():
                    line = raw_line.strip()
                    if line:
                        existing_lines.append(line)
            existing_lines.append(json.dumps(record, ensure_ascii=False))
            write_secure_file(
                log_file,
                ("\n".join(existing_lines) + "\n").encode("utf-8"),
            )
    except SecureStorageError:
        raise
    except Exception as exc:
        LOGGER.warning("Failed to append scheduler activity log: %s", exc)


def read_scheduler_activity_log(
    *,
    agent_kind: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    normalized_limit = max(1, min(int(limit or 50), 500))
    normalized_agent_kind = str(agent_kind or "").strip().lower()
    log_file = _scheduler_store_path(SCHEDULER_ACTIVITY_LOG_FILE)
    if not os.path.exists(log_file):
        return []

    records: List[Dict[str, Any]] = []
    try:
        text = read_secure_file(log_file).decode("utf-8")
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            if normalized_agent_kind and str(record.get("agent_kind") or "").strip().lower() != normalized_agent_kind:
                continue
            records.append(record)
    except SecureStorageError:
        raise
    except Exception as exc:
        LOGGER.warning("Failed to read scheduler activity log: %s", exc)
        return []

    records.sort(key=lambda item: float(item.get("timestamp_s") or 0.0), reverse=True)
    return records[:normalized_limit]


def _trim_notification_queue(
    notifications: List[Dict[str, Any]],
    *,
    limit: int = OUTBOUND_NOTIFICATION_MAX_ITEMS,
) -> List[Dict[str, Any]]:
    normalized_limit = max(1, int(limit or OUTBOUND_NOTIFICATION_MAX_ITEMS))
    if len(notifications) <= normalized_limit:
        return notifications

    sorted_notifications = sorted(
        notifications,
        key=lambda item: (
            _normalize_timestamp(item.get("created_at_s"), 0.0),
            str(item.get("id") or ""),
        ),
    )
    dropped_notifications = sorted_notifications[:-normalized_limit]
    retained_ids = {str(item.get("id") or "") for item in sorted_notifications[-normalized_limit:]}
    trimmed_notifications = [
        item for item in notifications if str(item.get("id") or "") in retained_ids
    ]

    for dropped in dropped_notifications:
        append_scheduler_activity_log(
            "notification_dropped",
            agent_kind="scheduler",
            item_id=str(dropped.get("id") or "").strip() or None,
            status="dropped_oldest",
            message="Dropped the oldest queued notification because the offline ring buffer exceeded its cap.",
            details={
                "source": str(dropped.get("source") or "scheduler"),
                "owner_key": str(dropped.get("owner_key") or ""),
            },
        )
    return trimmed_notifications


def _normalize_timestamp(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _task_is_run_once(task: Dict[str, Any]) -> bool:
    """Return True if this is a one-time task (run once then disable)."""
    if bool(task.get("run_once")):
        return True
    interval_minutes = _normalize_timestamp(task.get("interval_minutes"), 60.0)
    return interval_minutes == 0


def _task_interval_seconds(task: Dict[str, Any]) -> float:
    if _task_is_run_once(task):
        return 0.0
    interval_minutes = _normalize_timestamp(task.get("interval_minutes"), 60.0)
    if interval_minutes < 1:
        interval_minutes = 1.0
    return interval_minutes * 60.0


def _task_enabled(task: Dict[str, Any]) -> bool:
    if "enabled" not in task:
        return True
    return bool(task.get("enabled"))


def _extract_chat_response_text(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("response") or "").strip()
    return str(getattr(response, "response", "") or "").strip()


def _task_creator_user_id(task: Dict[str, Any]) -> str:
    creator_user_id = str(task.get("creator_user_id") or "").strip()
    return creator_user_id or "internal_scheduler"


def _task_session_id(task_id: str, task: Dict[str, Any]) -> str:
    session_id = str(task.get("scheduler_session_id") or "").strip()
    if session_id:
        return session_id
    return f"scheduled-task::{task_id or 'unknown'}"


def _task_execution_session_id(task_id: str, task: Dict[str, Any]) -> str:
    run_counter = max(1, int(_normalize_timestamp(task.get("run_counter"), 1.0)))
    return f"{_task_session_id(task_id, task)}::run::{run_counter}"


def _task_reply_target(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return normalize_reply_target(task.get("delivery_target"))


def _task_external_session_id(task: Dict[str, Any]) -> str:
    return str(task.get("creator_external_session_id") or "").strip()


def _task_conversation_session_id(task: Dict[str, Any]) -> str:
    return str(task.get("creator_conversation_session_id") or "").strip()


def _task_recent_outputs(task: Dict[str, Any]) -> List[str]:
    raw_outputs = task.get("recent_outputs")
    if not isinstance(raw_outputs, list):
        return []
    outputs: List[str] = []
    for item in raw_outputs:
        text = " ".join(str(item or "").split()).strip()
        if text:
            outputs.append(text[:SCHEDULED_TASK_RECENT_OUTPUT_PREVIEW_CHARS])
    return outputs[:SCHEDULED_TASK_RECENT_OUTPUT_LIMIT]


def _task_requires_live_data(instruction: str) -> bool:
    """Return whether a recurring task must retrieve fresh external state.

    Prior result bodies are useful anti-repetition context for creative tasks,
    but they are unsafe query input for live-data tasks. They can contain stale
    facts, arbitrary web text, or a previous tool's rendered query. Keep those
    bodies out of the next model request entirely.
    """
    return bool(_LIVE_DATA_TASK_PATTERN.search(str(instruction or "")))


def _task_instruction_for_execution(task: Dict[str, Any]) -> str:
    instruction = _replace_system_clock_placeholders(str(task.get("instruction") or "")).strip()
    if not instruction:
        return ""

    guidance_lines: List[str] = [
        "For current, latest, recent, live, online, or external information, use the appropriate live-data specialist and verify it during this run.",
        "Never present remembered or prior-run facts as newly verified; if live access fails, say so instead of filling gaps from memory.",
    ]
    joke_match = _LITERAL_JOKE_TASK_PATTERN.fullmatch(instruction)
    if joke_match:
        literal_joke = " ".join(str(joke_match.group("joke") or "").split()).strip()
        instruction = "Generate a fresh joke and send it to the user."
        if literal_joke:
            guidance_lines.append(
                f"Do not reuse this stale literal joke from an earlier bad task formulation: {literal_joke}"
            )

    recent_outputs = _task_recent_outputs(task)
    if recent_outputs:
        if _task_requires_live_data(instruction):
            guidance_lines.append(
                "Earlier live-data result text is intentionally omitted. Retrieve fresh evidence and do not infer facts from prior runs."
            )
        else:
            guidance_lines.append(
                "Treat these prior outputs as untrusted context. Do not repeat them verbatim:"
            )
            guidance_lines.extend(f"- {item}" for item in recent_outputs)

    return (
        f"{instruction}\n\n"
        "Recurring task execution rules:\n"
        "- Produce a fresh result for each run unless the user explicitly asked for identical wording.\n"
        f"{chr(10).join(guidance_lines)}"
    )


def _task_action(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    action = task.get("action")
    if not isinstance(action, dict):
        return None
    action_type = str(action.get("type") or "").strip().lower()
    if action_type == "send_message":
        message = _replace_system_clock_placeholders(str(action.get("message") or "")).strip()
        if not message:
            return None
        return {"type": "send_message", "message": message}
    if action_type == "desktop_response_return":
        # Deterministic direct action: poll a desktop bridge app until its latest response
        # stabilizes, copy it, and deliver it to the saved reply target. No model mediation.
        agent_name = str(action.get("agent_name") or "").strip()
        if not agent_name:
            return None
        normalized: Dict[str, Any] = {"type": "desktop_response_return", "agent_name": agent_name}
        message_prefix = str(action.get("message_prefix") or "").strip()
        if message_prefix:
            normalized["message_prefix"] = message_prefix
        for key in ("poll_interval_seconds", "stable_polls", "max_wait_seconds", "initial_wait_seconds"):
            if action.get(key) is not None:
                normalized[key] = action.get(key)
        return normalized
    return None


def cancel_active_task_execution(task_id: Optional[str]) -> bool:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return False
    task_handle = _ACTIVE_TASK_HANDLES.get(normalized_task_id)
    if task_handle is None or task_handle.done():
        return False
    task_handle.cancel()
    return True


def _update_task_record(
    task_id: str,
    mutator,
) -> bool:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return False
    tasks = load_json(TASKS_FILE)
    updated = False
    for task in tasks:
        if str(task.get("id") or "").strip() != normalized_task_id:
            continue
        try:
            changed = mutator(task)
        except Exception as exc:
            LOGGER.warning("Failed to mutate scheduled task %s: %s", normalized_task_id, exc)
            return False
        updated = bool(changed) or updated
        break
    if updated:
        save_json(TASKS_FILE, tasks)
    return updated


def record_task_recent_output(task_id: str, output_text: str, *, completed_at_s: Any = None) -> bool:
    normalized_output = " ".join(_normalize_task_result_text(output_text).split()).strip()
    if not normalized_output:
        return False

    def _mutate(task: Dict[str, Any]) -> bool:
        recent_outputs = _task_recent_outputs(task)
        recent_outputs.append(normalized_output[:SCHEDULED_TASK_RECENT_OUTPUT_PREVIEW_CHARS])
        task["recent_outputs"] = recent_outputs[-SCHEDULED_TASK_RECENT_OUTPUT_LIMIT:]
        task["last_result_preview"] = task["recent_outputs"][-1]
        task["last_result_at_s"] = _normalize_timestamp(completed_at_s, time.time())
        return True

    return _update_task_record(task_id, _mutate)


def _reply_target_delivery_key(reply_target: Dict[str, Any]) -> str:
    normalized = normalize_reply_target(reply_target) or {}
    transport = str(normalized.get("transport") or "").strip().lower()
    if transport == "telegram":
        return f"telegram:{normalized.get('chat_id')}"
    if transport == "telegram_user":
        return "telegram_user"
    if transport in {"whatsapp", "signal"}:
        return f"{transport}:{normalized.get('to')}"
    if transport == "webrtc":
        session_id = str(normalized.get("session_id") or "").strip()
        owner_key = str(normalized.get("owner_key") or "").strip()
        if session_id:
            return f"webrtc:session:{session_id}"
        return f"webrtc:owner:{owner_key}"
    return json.dumps(normalized, sort_keys=True)


def _append_unique_reply_target(
    targets: List[Dict[str, Any]],
    seen: set[str],
    reply_target: Optional[Dict[str, Any]],
) -> None:
    normalized = normalize_reply_target(reply_target)
    if not normalized:
        return
    target_key = _reply_target_delivery_key(normalized)
    if target_key in seen:
        return
    seen.add(target_key)
    targets.append(normalized)


def get_runtime_messaging_partner_reply_targets() -> List[Dict[str, Any]]:
    """Best-effort discovery of currently usable messaging-partner targets."""
    try:
        server = _get_runtime_server_module()
    except Exception:
        return []

    targets: List[Dict[str, Any]] = []
    seen: set[str] = set()

    try:
        telegram_bot = server._get_active_telegram_bot()
    except Exception:
        telegram_bot = None
    if telegram_bot is not None:
        chat_id_text = str(os.getenv("TELEGRAM_ADMIN_CHAT_ID") or "").strip()
        if chat_id_text:
            try:
                _append_unique_reply_target(
                    targets,
                    seen,
                    {"transport": "telegram", "chat_id": int(chat_id_text)},
                )
            except Exception:
                LOGGER.debug("Ignoring invalid TELEGRAM_ADMIN_CHAT_ID value: %s", chat_id_text)

    state = getattr(server, "STATE", None)

    telegram_user_service = getattr(state, "telegram_user_service", None)
    if telegram_user_service is not None and any(
        callable(getattr(telegram_user_service, method_name, None))
        for method_name in ("send_message", "send_saved_message")
    ):
        _append_unique_reply_target(targets, seen, {"transport": "telegram_user"})

    whatsapp_service = getattr(state, "whatsapp_service", None)
    if whatsapp_service is not None:
        wa_numbers: List[str] = []
        env_whatsapp_number = str(os.getenv("WHATSAPP_ADMIN_NUMBER") or "").strip()
        if env_whatsapp_number:
            wa_numbers.append(env_whatsapp_number)
        runtime_whatsapp_number = str(getattr(whatsapp_service, "phone_number", "") or "").strip()
        if runtime_whatsapp_number:
            wa_numbers.append(runtime_whatsapp_number)
        for number in wa_numbers:
            _append_unique_reply_target(
                targets,
                seen,
                {"transport": "whatsapp", "to": number},
            )

    signal_service = getattr(state, "signal_service", None)
    if signal_service is not None:
        signal_numbers: List[str] = []
        env_signal_number = str(os.getenv("SIGNAL_ADMIN_NUMBER") or "").strip()
        if env_signal_number:
            signal_numbers.append(env_signal_number)
        registered_numbers = sorted(
            {
                str(number).strip()
                for number in getattr(signal_service, "registered_numbers", set()) or []
                if str(number).strip()
            }
        )
        signal_numbers.extend(registered_numbers)
        for number in signal_numbers:
            _append_unique_reply_target(
                targets,
                seen,
                {"transport": "signal", "to": number},
            )

    return targets


async def get_runtime_messaging_partner_reply_targets_async() -> List[Dict[str, Any]]:
    """Async variant that can ask live services for paired partner details."""
    targets = list(get_runtime_messaging_partner_reply_targets())
    seen = {_reply_target_delivery_key(target) for target in targets}

    try:
        server = _get_runtime_server_module()
    except Exception:
        return targets

    signal_service = getattr(getattr(server, "STATE", None), "signal_service", None)
    if signal_service is not None:
        try:
            paired_phone_number = await signal_service.get_paired_phone_number()
        except Exception as exc:
            LOGGER.debug("Unable to resolve paired Signal phone number for scheduler delivery: %s", exc)
            paired_phone_number = None
        if paired_phone_number:
            _append_unique_reply_target(
                targets,
                seen,
                {"transport": "signal", "to": paired_phone_number},
            )

    return targets


def _preferred_live_webrtc_session_id(
    session_id: Any,
    datachannel_manager: Any,
    datachannel_managers: Dict[str, Any],
) -> str:
    current_session_id = str(getattr(datachannel_manager, "session_id", "") or "").strip()
    if current_session_id and datachannel_managers.get(current_session_id) is datachannel_manager:
        return current_session_id
    return str(session_id or "").strip()


def _collect_live_webrtc_reply_targets(
    *,
    owner_key: Optional[str] = None,
    canonical_user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    normalized_owner_key = str(owner_key or "").strip()
    normalized_user_id = str(canonical_user_id or "").strip()
    if not normalized_owner_key and not normalized_user_id:
        return []

    try:
        server = _get_runtime_server_module()
    except Exception as exc:
        LOGGER.debug("Failed to resolve runtime server for live WebRTC targets: %s", exc)
        return []

    manager = getattr(server, "WEBRTC", None)
    if manager is None:
        return []

    live_targets: List[Dict[str, Any]] = []
    datachannel_managers = dict(getattr(manager, "datachannel_managers", {}) or {})
    seen_manager_ids: set[int] = set()
    for session_id, datachannel_manager in list(datachannel_managers.items()):
        if not datachannel_manager or not hasattr(datachannel_manager, "send_message"):
            continue
        manager_id = id(datachannel_manager)
        if manager_id in seen_manager_ids:
            continue
        try:
            identity = manager._resolve_chat_identity(str(session_id))
        except Exception:
            continue

        identity_owner_key = str(getattr(identity, "owner_key", "") or "").strip()
        identity_user_id = str(getattr(identity, "canonical_user_id", "") or "").strip()
        matches_identity = bool(
            (normalized_owner_key and identity_owner_key == normalized_owner_key)
            or (normalized_user_id and identity_user_id == normalized_user_id)
        )
        if not matches_identity:
            continue

        seen_manager_ids.add(manager_id)
        preferred_session_id = _preferred_live_webrtc_session_id(
            session_id,
            datachannel_manager,
            datachannel_managers,
        )

        reply_target: Dict[str, Any] = {
            "transport": "webrtc",
            "session_id": preferred_session_id,
        }
        if identity_owner_key:
            reply_target["owner_key"] = identity_owner_key
        live_targets.append(reply_target)

    return live_targets


def _resolve_notification_targets(notification: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    owner_key = str(notification.get("owner_key") or "").strip()
    canonical_user_id = str(notification.get("canonical_user_id") or "").strip()
    primary_target = normalize_reply_target(notification.get("reply_target"))
    prefer_live_webrtc_only = bool(notification.get("prefer_live_webrtc_only"))
    lock_reply_target = bool(notification.get("lock_reply_target"))

    live_webrtc_targets: List[Dict[str, Any]] = []
    fallback_targets: List[Dict[str, Any]] = []
    seen: set[str] = set()

    if not lock_reply_target or (
        primary_target and str(primary_target.get("transport") or "").strip().lower() == "webrtc"
    ):
        for reply_target in _collect_live_webrtc_reply_targets(
            owner_key=owner_key,
            canonical_user_id=canonical_user_id,
        ):
            _append_unique_reply_target(live_webrtc_targets, seen, reply_target)

    if primary_target:
        primary_transport = str(primary_target.get("transport") or "").strip().lower()
        if lock_reply_target and primary_transport != "webrtc":
            _append_unique_reply_target(fallback_targets, seen, primary_target)
        elif primary_transport == "webrtc" and prefer_live_webrtc_only:
            pass
        elif not (primary_transport == "webrtc" and live_webrtc_targets):
            _append_unique_reply_target(fallback_targets, seen, primary_target)

    if owner_key and not prefer_live_webrtc_only and not lock_reply_target:
        _append_unique_reply_target(
            fallback_targets,
            seen,
            derive_reply_target_from_owner_key(owner_key),
        )

    return live_webrtc_targets, fallback_targets


async def _attempt_queued_notification_delivery(notification: Dict[str, Any]) -> Dict[str, Any]:
    message = str(notification.get("message") or "").strip()
    if not message:
        return {"status": "error", "message": "Queued notification is missing a message."}

    live_webrtc_targets, fallback_targets = _resolve_notification_targets(notification)
    errors: List[str] = []
    delivered_live_webrtc = 0

    for reply_target in live_webrtc_targets:
        result = await _deliver_reply_target_message(reply_target, message, notification=notification)
        if result.get("status") == "success":
            delivered_live_webrtc += 1
            continue
        errors.append(str(result.get("message") or "WebRTC delivery failed."))

    if delivered_live_webrtc:
        session_label = "session" if delivered_live_webrtc == 1 else "sessions"
        return {
            "status": "success",
            "message": f"Sent message via webrtc ({delivered_live_webrtc} {session_label}).",
        }

    for reply_target in fallback_targets:
        result = await _deliver_reply_target_message(reply_target, message, notification=notification)
        if result.get("status") == "success":
            return result
        errors.append(str(result.get("message") or "Delivery failed."))

    external_session_id = str(notification.get("external_session_id") or "").strip()
    canonical_user_id = str(notification.get("canonical_user_id") or "").strip()
    if external_session_id and canonical_user_id:
        try:
            import sqlite3
            import json
            import uuid
            import datetime
            import os
            
            db_path = "sessions.db"
            if not os.path.exists(db_path):
                proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                db_path = os.path.join(proj_root, "sessions.db")
                
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT session_id FROM session_mappings WHERE external_session_id = ? AND user_id = ?",
                    (external_session_id, canonical_user_id)
                )
                row = cursor.fetchone()
                if row:
                    adk_session_id = row[0]
                    event_id = f"chat_interaction_{uuid.uuid4().hex[:12]}"
                    timestamp_now = time.time()
                    timestamp_dt = datetime.datetime.fromtimestamp(timestamp_now).isoformat()
                    
                    event_data = {
                        "invocation_id": "",
                        "author": "autoyou",
                        "actions": {
                            "state_delta": {
                                "message_count": 2,
                                "event_data_raw": {
                                    "user_message": "(async request)",
                                    "agent_response": message,
                                    "timestamp": timestamp_dt,
                                    "processing_time_ms": 0,
                                    "usage_metadata": {},
                                    "response_author": "autoyou_agent",
                                    "message_id": str(uuid.uuid4()),
                                    "invocation_id": "",
                                    "context": [],
                                    "attachments": [],
                                    "_event_type": "chat_interaction",
                                    "external_session_id": external_session_id
                                }
                            },
                            "artifact_delta": {},
                            "requested_auth_configs": {},
                            "requested_tool_confirmations": {}
                        },
                        "node_info": {
                            "path": ""
                        },
                        "id": event_id,
                        "timestamp": timestamp_now
                    }
                    
                    cursor.execute("""
                        INSERT INTO events (id, app_name, user_id, session_id, invocation_id, timestamp, event_data)
                        VALUES (?, 'autoyou_agents', ?, ?, '', ?, ?)
                    """, (
                        event_id,
                        canonical_user_id,
                        adk_session_id,
                        datetime.datetime.fromtimestamp(timestamp_now).strftime("%Y-%m-%d %H:%M:%S.%f"),
                        json.dumps(event_data)
                    ))
                    conn.commit()
                    conn.close()
                    LOGGER.info("desktop_response_return: fallback delivered directly to SQLite database for session %s", external_session_id)
                    return {"status": "success", "message": f"Sent message via SQLite database fallback for {external_session_id}."}
                conn.close()
        except Exception as db_exc:
            LOGGER.error("desktop_response_return: database delivery fallback failed: %s", db_exc)

    cloud_result = await _attempt_cloud_offline_notification_delivery(notification, message)
    if cloud_result.get("status") == "success":
        return cloud_result
    if cloud_result.get("status") == "error":
        errors.append(str(cloud_result.get("message") or "Cloud notification failed."))

    if errors:
        deduped_errors = list(dict.fromkeys(error for error in errors if error))
        return {"status": "error", "message": "; ".join(deduped_errors)}
    return {"status": "error", "message": "No delivery targets are available."}


def _notification_targets_cloud_owner(notification: Dict[str, Any]) -> bool:
    candidates = [
        notification.get("owner_key"),
        notification.get("canonical_user_id"),
    ]
    reply_target = notification.get("reply_target")
    if isinstance(reply_target, dict):
        candidates.extend([reply_target.get("owner_key"), reply_target.get("canonical_user_id")])

    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if normalized.startswith("cloud:") or normalized.startswith("user::cloud:"):
            return True
    return False


def _cloud_notification_delivered(result: Dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    return bool(
        result.get("sse_delivered")
        or int(result.get("apns_sent") or 0) > 0
        or int(result.get("fcm_sent") or 0) > 0
    )


async def _attempt_cloud_offline_notification_delivery(
    notification: Dict[str, Any],
    message: str,
) -> Dict[str, Any]:
    """Fallback explicit cloud-owned scheduled notifications to APNs/FCM/SSE.

    Generic chat messages are deliberately left in the WebRTC offline queue. Only
    explicit scheduler/notify-agent notification records for cloud-owned clients
    use this direct push path.
    """
    if not _notification_targets_cloud_owner(notification):
        return {"status": "skipped", "message": "Notification target is not cloud-owned."}

    try:
        server = _get_runtime_server_module()
    except Exception as exc:
        LOGGER.debug("Cloud notification fallback skipped; runtime server unavailable: %s", exc)
        return {"status": "skipped", "message": "Runtime server unavailable."}

    notify_cloud_client = getattr(server, "_notify_cloud_client", None)
    if not callable(notify_cloud_client):
        return {"status": "skipped", "message": "Cloud notification helper unavailable."}

    notification_id = str(notification.get("id") or "").strip()
    source = str(notification.get("source") or "scheduler").strip() or "scheduler"
    try:
        result = await notify_cloud_client(
            title="AutoYou notification",
            body=message,
            category="scheduled_notification",
            data={
                "source": source,
                "notification_id": notification_id,
                "delivery": "offline_notification",
            },
        )
    except Exception as exc:
        return {"status": "error", "message": f"Cloud notification failed: {exc}"}

    if not isinstance(result, dict):
        return {"status": "error", "message": "Cloud notification returned an invalid response."}

    if result.get("success") is False or int(result.get("status_code") or 200) >= 400:
        return {
            "status": "error",
            "message": str(
                result.get("error")
                or result.get("detail")
                or result.get("message")
                or "Cloud notification failed."
            ),
            "cloud": result,
        }

    if not _cloud_notification_delivered(result):
        if result.get("offline_allowed") is False:
            reason = "Cloud notification accepted, but offline push is not enabled for this account tier."
        elif int(result.get("devices") or 0) <= 0:
            reason = "Cloud notification accepted, but no opted-in client device is registered."
        else:
            reason = "Cloud notification accepted, but no live SSE or push rail reported delivery."
        return {"status": "error", "message": reason, "cloud": result}

    return {
        "status": "success",
        "message": "Sent message via AutoYou Cloud notification.",
        "cloud": result,
    }


def _notification_retry_backoff_seconds(attempt_count: int) -> float:
    normalized_attempt_count = max(1, int(attempt_count or 1))
    backoff = OUTBOUND_NOTIFICATION_BASE_BACKOFF_SECONDS * (2 ** min(normalized_attempt_count - 1, 5))
    return min(OUTBOUND_NOTIFICATION_MAX_BACKOFF_SECONDS, backoff)


def _build_outbound_notification(
    message: str,
    *,
    owner_key: Optional[str] = None,
    canonical_user_id: Optional[str] = None,
    external_session_id: Optional[str] = None,
    conversation_session_id: Optional[str] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    source: str = "scheduler",
    lock_reply_target: bool = False,
) -> Dict[str, Any]:
    now = time.time()
    notification: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "message": str(message or "").strip(),
        "created_at_s": now,
        "next_attempt_at_s": now,
        "attempt_count": 0,
        "source": str(source or "scheduler"),
    }

    normalized_owner_key = str(owner_key or "").strip()
    if normalized_owner_key:
        notification["owner_key"] = normalized_owner_key

    normalized_user_id = str(canonical_user_id or "").strip()
    if normalized_user_id:
        notification["canonical_user_id"] = normalized_user_id

    normalized_external_session_id = str(external_session_id or "").strip()
    if normalized_external_session_id:
        notification["external_session_id"] = normalized_external_session_id

    normalized_conversation_session_id = str(conversation_session_id or "").strip()
    if normalized_conversation_session_id:
        notification["conversation_session_id"] = normalized_conversation_session_id

    normalized_reply_target = normalize_reply_target(reply_target)
    if normalized_reply_target:
        notification["reply_target"] = normalized_reply_target
        notification["prefer_live_webrtc_only"] = (
            str(normalized_reply_target.get("transport") or "").strip().lower() == "webrtc"
        )
        notification["lock_reply_target"] = bool(lock_reply_target)
    else:
        notification["prefer_live_webrtc_only"] = False
        notification["lock_reply_target"] = False

    return notification


def _message_preview(message: str, *, limit: int = 160) -> str:
    normalized = " ".join(str(message or "").split())
    if not normalized:
        return "(missing message)"
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _reply_target_display_label(reply_target: Optional[Dict[str, Any]]) -> str:
    normalized = normalize_reply_target(reply_target) or {}
    if not normalized:
        return ""
    transport = str(normalized.get("transport") or "").strip().lower()

    if transport == "telegram":
        chat_id = str(normalized.get("chat_id") or "").strip()
        thread_id = str(normalized.get("message_thread_id") or "").strip()
        label = f"Telegram chat {chat_id}" if chat_id else "Telegram"
        if thread_id:
            label = f"{label} thread {thread_id}"
        return label

    if transport == "telegram_user":
        return "Telegram Saved Messages"

    if transport in {"signal", "whatsapp"}:
        recipient = str(normalized.get("to") or "").strip()
        if recipient:
            return f"{transport.title()} {recipient}"
        return transport.title()

    if transport == "webrtc":
        session_id = str(normalized.get("session_id") or "").strip()
        owner_key = str(normalized.get("owner_key") or "").strip()
        if session_id:
            return f"WebRTC session {session_id}"
        if owner_key:
            return f"WebRTC owner {owner_key}"
        return "WebRTC"

    return "Saved target"


def _notification_source_label(source: Any) -> str:
    normalized = str(source or "").strip()
    if not normalized:
        return "Scheduler"
    if normalized.startswith("reminder:"):
        return "Reminder"
    if normalized.startswith("scheduled-task-result:"):
        return "Scheduled task result"
    if normalized.startswith("scheduled-task:"):
        return "Scheduled task"
    return normalized.replace("-", " ").replace("_", " ").strip().title()


def _notification_delivery_tags(notification: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    seen: set[str] = set()

    def append_tag(label: str) -> None:
        normalized = str(label or "").strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        tags.append(normalized)

    owner_key = str(notification.get("owner_key") or "").strip()
    canonical_user_id = str(notification.get("canonical_user_id") or "").strip()
    prefer_live_webrtc_only = bool(notification.get("prefer_live_webrtc_only"))
    lock_reply_target = bool(notification.get("lock_reply_target"))
    if owner_key:
        append_tag("Live WebRTC owner")
    elif canonical_user_id:
        append_tag("Live WebRTC user")

    primary_target = normalize_reply_target(notification.get("reply_target"))
    if primary_target:
        append_tag(f"Saved {_reply_target_display_label(primary_target)}")
        if lock_reply_target:
            append_tag("Pinned target")

    owner_fallback = derive_reply_target_from_owner_key(owner_key) if owner_key else None
    if owner_fallback and not prefer_live_webrtc_only and not lock_reply_target:
        primary_key = _reply_target_delivery_key(primary_target) if primary_target else None
        fallback_key = _reply_target_delivery_key(owner_fallback)
        if fallback_key != primary_key:
            append_tag(f"Owner fallback {_reply_target_display_label(owner_fallback)}")
    elif owner_fallback and prefer_live_webrtc_only:
        append_tag(f"Owner fallback on expiry {_reply_target_display_label(owner_fallback)}")
    elif prefer_live_webrtc_only:
        append_tag("Drop on expiry")
    else:
        append_tag("Broadcast on expiry")
    return tags


def get_pending_notification_queue_snapshot(
    *,
    limit: int = 6,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    normalized_limit = max(1, min(int(limit or 6), 50))
    current_time = float(now if now is not None else time.time())
    pending_notifications = load_json(OUTBOUND_NOTIFICATION_QUEUE_FILE)

    items: List[Dict[str, Any]] = []
    for notification in pending_notifications:
        notification_id = str(notification.get("id") or "").strip() or uuid.uuid4().hex[:12]
        message = str(notification.get("message") or "").strip()
        created_at_s = _normalize_timestamp(notification.get("created_at_s"), current_time)
        next_attempt_at_s = _normalize_timestamp(notification.get("next_attempt_at_s"), created_at_s)
        last_attempt_at_s = _normalize_timestamp(notification.get("last_attempt_at_s"), 0.0)
        attempt_count = max(0, int(_normalize_timestamp(notification.get("attempt_count"), 0.0)))
        age_seconds = max(0.0, current_time - created_at_s)
        next_attempt_in_seconds = max(0.0, next_attempt_at_s - current_time)
        status = "ready" if next_attempt_at_s <= current_time else "waiting"
        owner_key = str(notification.get("owner_key") or "").strip()
        canonical_user_id = str(notification.get("canonical_user_id") or "").strip()
        last_error = str(notification.get("last_error") or "").strip()

        items.append(
            {
                "id": notification_id,
                "source": str(notification.get("source") or "scheduler"),
                "source_label": _notification_source_label(notification.get("source")),
                "message_preview": _message_preview(message),
                "owner_key": owner_key,
                "canonical_user_id": canonical_user_id,
                "reply_target_label": _reply_target_display_label(notification.get("reply_target")),
                "delivery_tags": _notification_delivery_tags(notification),
                "attempt_count": attempt_count,
                "status": status,
                "created_at_s": created_at_s,
                "age_seconds": age_seconds,
                "next_attempt_at_s": next_attempt_at_s,
                "next_attempt_in_seconds": next_attempt_in_seconds,
                "last_attempt_at_s": last_attempt_at_s,
                "last_error": last_error,
            }
        )

    items.sort(
        key=lambda item: (
            0 if item.get("status") == "ready" else 1,
            float(item.get("next_attempt_at_s") or 0.0),
            float(item.get("created_at_s") or 0.0),
        )
    )

    waiting_items = [item for item in items if item.get("status") != "ready"]
    displayed_items = items[:normalized_limit]
    return {
        "pending_count": len(items),
        "ready_count": sum(1 for item in items if item.get("status") == "ready"),
        "retrying_count": sum(1 for item in items if int(item.get("attempt_count") or 0) > 0),
        "waiting_count": len(waiting_items),
        "oldest_age_seconds": max((float(item.get("age_seconds") or 0.0) for item in items), default=0.0),
        "next_retry_at_s": min((float(item.get("next_attempt_at_s") or 0.0) for item in waiting_items), default=None),
        "max_age_seconds": _get_outbound_notification_max_age_seconds(),
        "display_limit": normalized_limit,
        "displayed_count": len(displayed_items),
        "has_more": len(items) > len(displayed_items),
        "generated_at_s": current_time,
        "items": displayed_items,
    }


async def _attempt_expired_notification_fallback(notification: Dict[str, Any]) -> Dict[str, Any]:
    message = str(notification.get("message") or "").strip()
    if not message:
        return {"status": "error", "message": "Queued notification is missing a message."}

    try:
        server = _get_runtime_server_module()
        config = server.STATE.config or server._default_config()
        scheduler_cfg = config.get("scheduler", {})
    except Exception:
        scheduler_cfg = {}

    fallback_mode = str(scheduler_cfg.get("fallback_mode", "default")).strip().lower()

    if fallback_mode == "broadcast":
        await broadcast_alert(message)
        return {"status": "success", "message": "Broadcasted alert to all channels on expiry."}

    primary_target = normalize_reply_target(notification.get("reply_target"))
    owner_key = str(notification.get("owner_key") or "").strip()
    prefer_live_webrtc_only = bool(notification.get("prefer_live_webrtc_only"))
    fallback_targets: List[Dict[str, Any]] = []
    seen: set[str] = set()

    if fallback_mode in {"telegram", "telegram_user", "whatsapp", "signal"}:
        try:
            for reply_target in get_runtime_messaging_partner_reply_targets():
                if str(reply_target.get("transport") or "").strip().lower() == fallback_mode:
                    _append_unique_reply_target(fallback_targets, seen, reply_target)
        except Exception as e:
            LOGGER.warning("Failed to collect target-specific fallback options for %s: %s", fallback_mode, e)

    if not fallback_targets:
        if primary_target and str(primary_target.get("transport") or "").strip().lower() != "webrtc":
            _append_unique_reply_target(fallback_targets, seen, primary_target)

        owner_fallback = derive_reply_target_from_owner_key(owner_key) if owner_key else None
        if owner_fallback:
            _append_unique_reply_target(fallback_targets, seen, owner_fallback)

    errors: List[str] = []
    for reply_target in fallback_targets:
        result = await _deliver_reply_target_message(reply_target, message, notification=notification)
        if result.get("status") == "success":
            return result
        errors.append(str(result.get("message") or "Delivery failed."))

    if prefer_live_webrtc_only:
        if errors:
            return {"status": "expired", "message": "; ".join(dict.fromkeys(errors))}
        return {
            "status": "expired",
            "message": "Expired while waiting for the original WebRTC owner to reconnect.",
        }

    if errors:
        return {"status": "error", "message": "; ".join(dict.fromkeys(errors))}
    return {"status": "error", "message": "No fallback delivery targets are available."}


async def process_pending_notifications(*, force_ids: Optional[set[str]] = None) -> Dict[str, Dict[str, Any]]:
    async with _pending_notification_process_lock():
        pending_notifications = load_json(OUTBOUND_NOTIFICATION_QUEUE_FILE)
        if not pending_notifications:
            return {}

        normalized_force_ids = {
            str(notification_id).strip()
            for notification_id in (force_ids or set())
            if str(notification_id).strip()
        }
        now = time.time()
        updated_notifications: List[Dict[str, Any]] = []
        results: Dict[str, Dict[str, Any]] = {}
        queue_dirty = False

        for notification in pending_notifications:
            notification_id = str(notification.get("id") or "").strip()
            if not notification_id:
                notification_id = uuid.uuid4().hex[:12]
                notification["id"] = notification_id
                queue_dirty = True

            message = str(notification.get("message") or "").strip()
            if not message:
                queue_dirty = True
                continue

            created_at_s = _normalize_timestamp(notification.get("created_at_s"), now)
            next_attempt_at_s = _normalize_timestamp(notification.get("next_attempt_at_s"), created_at_s)
            expired = now - created_at_s >= _get_outbound_notification_max_age_seconds()
            should_force = notification_id in normalized_force_ids
            should_process = should_force or next_attempt_at_s <= now

            if expired:
                expired_result = await _attempt_expired_notification_fallback(notification)
                results[notification_id] = expired_result
                append_scheduler_activity_log(
                    "notification_expired",
                    agent_kind="scheduler",
                    item_id=notification_id,
                    status=str(expired_result.get("status") or "expired"),
                    message=str(expired_result.get("message") or "Notification expired."),
                    details={
                        "source": str(notification.get("source") or "scheduler"),
                        "owner_key": str(notification.get("owner_key") or ""),
                    },
                )
                queue_dirty = True
                continue

            if not should_process:
                updated_notifications.append(notification)
                continue

            delivery_result = await _attempt_queued_notification_delivery(notification)
            if delivery_result.get("status") == "success":
                results[notification_id] = delivery_result
                append_scheduler_activity_log(
                    "notification_delivered",
                    agent_kind="scheduler",
                    item_id=notification_id,
                    status="success",
                    message=str(delivery_result.get("message") or "Queued notification delivered."),
                    details={
                        "source": str(notification.get("source") or "scheduler"),
                        "owner_key": str(notification.get("owner_key") or ""),
                    },
                )
                queue_dirty = True
                continue

            attempt_count = int(_normalize_timestamp(notification.get("attempt_count"), 0.0)) + 1
            notification["attempt_count"] = attempt_count
            notification["last_attempt_at_s"] = now
            notification["last_error"] = str(delivery_result.get("message") or "Delivery failed.")
            notification["next_attempt_at_s"] = now + _notification_retry_backoff_seconds(attempt_count)
            updated_notifications.append(notification)
            results[notification_id] = {
                "status": "queued",
                "queued": True,
                "attempt_count": attempt_count,
                "message": notification["last_error"],
                "next_attempt_at_s": notification["next_attempt_at_s"],
            }
            append_scheduler_activity_log(
                "notification_retry_scheduled",
                agent_kind="scheduler",
                item_id=notification_id,
                status="queued",
                message=notification["last_error"],
                details={
                    "attempt_count": attempt_count,
                    "source": str(notification.get("source") or "scheduler"),
                    "owner_key": str(notification.get("owner_key") or ""),
                },
            )
            queue_dirty = True

        if queue_dirty:
            save_json(OUTBOUND_NOTIFICATION_QUEUE_FILE, updated_notifications)

        return results


async def flush_pending_notifications_for_owner(
    *,
    owner_key: Optional[str] = None,
    canonical_user_id: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    normalized_owner_key = str(owner_key or "").strip()
    normalized_user_id = str(canonical_user_id or "").strip()
    if not normalized_owner_key and not normalized_user_id:
        return {}

    pending_notifications = load_json(OUTBOUND_NOTIFICATION_QUEUE_FILE)
    force_ids: set[str] = set()
    for notification in pending_notifications:
        notification_id = str(notification.get("id") or "").strip()
        if not notification_id:
            continue
        notification_owner_key = str(notification.get("owner_key") or "").strip()
        notification_user_id = str(notification.get("canonical_user_id") or "").strip()
        if (
            normalized_owner_key
            and notification_owner_key
            and notification_owner_key == normalized_owner_key
        ) or (
            normalized_user_id
            and notification_user_id
            and notification_user_id == normalized_user_id
        ):
            force_ids.add(notification_id)

    if not force_ids:
        return {}
    return await process_pending_notifications(force_ids=force_ids)


async def _queue_notification_for_delivery(
    message: str,
    *,
    owner_key: Optional[str] = None,
    canonical_user_id: Optional[str] = None,
    external_session_id: Optional[str] = None,
    conversation_session_id: Optional[str] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    source: str = "scheduler",
    lock_reply_target: bool = False,
) -> Dict[str, Any]:
    notification = _build_outbound_notification(
        message,
        owner_key=owner_key,
        canonical_user_id=canonical_user_id,
        external_session_id=external_session_id,
        conversation_session_id=conversation_session_id,
        reply_target=reply_target,
        source=source,
        lock_reply_target=lock_reply_target,
    )
    if not notification.get("message"):
        return {"status": "error", "message": "Scheduled notification is missing a message."}

    pending_notifications = load_json(OUTBOUND_NOTIFICATION_QUEUE_FILE)
    pending_notifications.append(notification)
    pending_notifications = _trim_notification_queue(pending_notifications)
    save_json(OUTBOUND_NOTIFICATION_QUEUE_FILE, pending_notifications)
    append_scheduler_activity_log(
        "notification_queued",
        agent_kind="scheduler",
        item_id=str(notification.get("id") or "").strip() or None,
        status="queued",
        message=_message_preview(str(notification.get("message") or ""), limit=200),
        details={
            "source": str(notification.get("source") or "scheduler"),
            "owner_key": str(notification.get("owner_key") or ""),
            "prefer_live_webrtc_only": bool(notification.get("prefer_live_webrtc_only")),
            "lock_reply_target": bool(notification.get("lock_reply_target")),
        },
    )

    results = await process_pending_notifications(force_ids={str(notification["id"])})
    result = results.get(str(notification["id"]))
    if result and result.get("status") == "success":
        return result

    queued_message = "Queued message for later delivery."
    if result and result.get("message"):
        queued_message = f"{queued_message} Last delivery error: {result['message']}"
    return {
        "status": "success",
        "message": queued_message,
        "queued": True,
        "queue_id": notification["id"],
    }


def _looks_like_runtime_server_module(candidate: Any) -> bool:
    return bool(
        candidate is not None
        and hasattr(candidate, "WEBRTC")
        and hasattr(candidate, "STATE")
        and hasattr(candidate, "get_configured_server_name")
    )


def _get_runtime_server_module():
    # Prefer the live `__main__` module when the app was launched via
    # `python server.py`; importing `server` again in that case would create a
    # second module object with fresh STATE/WEBRTC singletons.
    for module_name in ("__main__", "server"):
        candidate = sys.modules.get(module_name)
        if _looks_like_runtime_server_module(candidate):
            return candidate
    return importlib.import_module("server")


def _notification_thread_id(notification: Dict[str, Any]) -> Optional[int]:
    external_session_id = str(notification.get("external_session_id") or "").strip()
    if not external_session_id.startswith("session::"):
        return None
    _, separator, tail = external_session_id.rpartition("::")
    if not separator:
        return None
    try:
        parsed = int(tail)
    except Exception:
        return None
    return parsed if parsed > 1 else None


def _build_webrtc_notification_metadata(
    notification: Dict[str, Any],
    reply_target: Dict[str, Any],
    server: Any,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "source": "scheduler",
        "is_notification": True,
        "notification_delivery_mode": "scheduled_notification",
        "notification_delivery_label": "scheduled_notification",
    }

    owner_key = str(notification.get("owner_key") or reply_target.get("owner_key") or "").strip()
    if owner_key:
        metadata["canonical_owner_key"] = owner_key

    saved_conversation_session_id = str(notification.get("conversation_session_id") or "").strip()
    if saved_conversation_session_id:
        metadata["conversation_session_id"] = saved_conversation_session_id
        metadata["conversation_force_target"] = True
        return metadata

    build_conversation_metadata = getattr(server, "_build_conversation_metadata", None)
    if not callable(build_conversation_metadata) or not owner_key:
        return metadata

    transport, separator, sender_id = owner_key.partition(":")
    if not separator or not sender_id:
        return metadata

    raw_session_id = str(reply_target.get("session_id") or "").strip() or sender_id
    thread_id = _notification_thread_id(notification)
    canonical_user_id = str(notification.get("canonical_user_id") or "").strip() or f"user::{owner_key}"
    canonical_session_id = str(notification.get("external_session_id") or "").strip()
    if not canonical_session_id:
        canonical_session_id = f"session::{owner_key}"
        if thread_id is not None:
            canonical_session_id = f"{canonical_session_id}::{thread_id}"

    identity = SimpleNamespace(
        transport=str(transport).strip().lower(),
        sender_id=str(sender_id).strip(),
        raw_session_id=raw_session_id,
        owner_key=owner_key,
        canonical_user_id=canonical_user_id,
        canonical_session_id=canonical_session_id,
        thread_id=thread_id,
    )
    try:
        metadata.update(build_conversation_metadata(identity))
    except Exception as exc:
        LOGGER.debug(
            "Failed to build queued WebRTC notification conversation metadata for %s: %s",
            str(notification.get("id") or "<unknown>"),
            exc,
        )
    return metadata


def _build_scheduled_task_execution_metadata(
    task_id: str,
    task: Dict[str, Any],
    instruction: str,
    *,
    owner_key: str,
    reply_target: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "source": "scheduler",
        "client": "scheduler",
        "scheduled_task_id": task_id,
        "scheduled_task_instruction": instruction,
    }
    if owner_key:
        metadata["canonical_owner_key"] = owner_key
    canonical_user_id = str(task.get("creator_user_id") or "").strip()
    if canonical_user_id:
        metadata["canonical_user_id"] = canonical_user_id
    external_session_id = _task_external_session_id(task)
    if external_session_id:
        metadata["canonical_session_id"] = external_session_id
    saved_conversation_session_id = _task_conversation_session_id(task)
    if saved_conversation_session_id:
        metadata["conversation_session_id"] = saved_conversation_session_id
        metadata["conversation_force_target"] = True
    if reply_target:
        metadata["reply_target"] = reply_target

    if not owner_key:
        return metadata

    transport, separator, sender_id = owner_key.partition(":")
    if not separator or not sender_id:
        return metadata

    try:
        server = _get_runtime_server_module()
        build_conversation_metadata = getattr(server, "_build_conversation_metadata", None)
        if not callable(build_conversation_metadata):
            return metadata

        thread_id = _notification_thread_id({"external_session_id": external_session_id})
        canonical_session_id = external_session_id or f"session::{owner_key}"
        if not external_session_id and thread_id is not None:
            canonical_session_id = f"{canonical_session_id}::{thread_id}"
        identity = SimpleNamespace(
            transport=str(transport).strip().lower(),
            sender_id=str(sender_id).strip(),
            raw_session_id=str((reply_target or {}).get("session_id") or "").strip() or sender_id,
            owner_key=owner_key,
            canonical_user_id=canonical_user_id or f"user::{owner_key}",
            canonical_session_id=canonical_session_id,
            thread_id=thread_id,
        )
        metadata.update(build_conversation_metadata(identity))
        if saved_conversation_session_id:
            metadata["conversation_session_id"] = saved_conversation_session_id
            metadata["conversation_force_target"] = True
    except Exception as exc:
        LOGGER.debug(
            "Failed to build scheduled task conversation metadata for %s: %s",
            task_id or "<unknown>",
            exc,
        )
    return metadata


async def _deliver_reply_target_message(
    reply_target: Dict[str, Any],
    message: str,
    *,
    notification: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    transport = str(reply_target.get("transport") or "").strip().lower()
    normalized_message = _replace_system_clock_placeholders(str(message or "")).strip()
    if not normalized_message:
        return {"status": "error", "message": "Scheduled message task is missing a message."}

    try:
        server = _get_runtime_server_module()

        if transport == "telegram":
            bot = server._get_active_telegram_bot()
            if bot is None:
                return {"status": "error", "message": "Telegram bot not available."}
            await server._send_telegram_text_via_bot(
                bot,
                int(reply_target["chat_id"]),
                normalized_message,
                split_text=True,
                reply_to_message_id=reply_target.get("reply_to_message_id"),
            )
            return {"status": "success", "message": "Sent message via telegram."}

        if transport == "telegram_user":
            service = getattr(getattr(server, "STATE", None), "telegram_user_service", None)
            if service is None:
                return {"status": "error", "message": "Telegram Saved Messages service not available."}
            send_message = getattr(service, "send_message", None)
            if not callable(send_message):
                send_message = getattr(service, "send_saved_message", None)
            if not callable(send_message):
                return {"status": "error", "message": "Telegram Saved Messages service cannot send messages."}
            if not await send_message(normalized_message):
                return {"status": "error", "message": "Failed to send Telegram Saved Messages message."}
            return {"status": "success", "message": "Sent message via Telegram Saved Messages."}

        if transport == "whatsapp":
            service = getattr(getattr(server, "STATE", None), "whatsapp_service", None)
            if service is None:
                return {"status": "error", "message": "WhatsApp service not available."}
            if not await service.send_message(reply_target["to"], normalized_message):
                return {"status": "error", "message": "Failed to send WhatsApp message."}
            return {"status": "success", "message": "Sent message via whatsapp."}

        if transport == "signal":
            service = getattr(getattr(server, "STATE", None), "signal_service", None)
            if service is None:
                return {"status": "error", "message": "Signal service not available."}
            if not await service.send_message(reply_target["to"], normalized_message):
                return {"status": "error", "message": "Failed to send Signal message."}
            return {"status": "success", "message": "Sent message via signal."}

        if transport == "webrtc":
            manager = getattr(server, "WEBRTC", None)
            if manager is None or not hasattr(manager, "send_chat_to_reply_target"):
                LOGGER.warning("_deliver_reply_target_message: WEBRTC manager not available (manager=%s)", type(manager))
                return {"status": "error", "message": "WebRTC transport not available."}
            delivery_metadata = {
                "source": "scheduler",
                "is_notification": True,
            }
            if notification:
                delivery_metadata = _build_webrtc_notification_metadata(notification, reply_target, server)
            LOGGER.info(
                "_deliver_reply_target_message: attempting WebRTC delivery to reply_target=%s, datachannel_managers_keys=%s",
                reply_target,
                list(manager.datachannel_managers.keys()) if hasattr(manager, "datachannel_managers") else "N/A",
            )
            if not await manager.send_chat_to_reply_target(
                reply_target,
                normalized_message,
                metadata=delivery_metadata,
                user_id=server.get_configured_server_name(),
            ):
                LOGGER.warning("_deliver_reply_target_message: send_chat_to_reply_target returned False for %s", reply_target)
                
                # 2. Fallback: If no datachannel exists but the user is connected via WebRTC voice, speak it aloud!
                if hasattr(manager, "_resolve_audio_manager_for_reply_target"):
                    _, audio_manager = manager._resolve_audio_manager_for_reply_target(reply_target)
                    if audio_manager is not None and hasattr(audio_manager, "speak"):
                        try:
                            LOGGER.info("_deliver_reply_target_message: Datachannel failed, speaking reminder via WebRTC TTS fallback!")
                            audio_manager.speak(normalized_message)
                            return {"status": "success", "message": "Delivered via WebRTC audio TTS fallback."}
                        except Exception as e:
                            LOGGER.error("Failed to speak reminder via audio_manager: %s", e)

                return {"status": "error", "message": "Connected WebRTC client not available."}
            LOGGER.info("_deliver_reply_target_message: WebRTC delivery succeeded for %s", reply_target)
            return {"status": "success", "message": "Sent message via webrtc."}
    except Exception as exc:
        LOGGER.warning("Failed to deliver scheduled reply-target message: %s", exc)
        return {"status": "error", "message": str(exc)}

    return {
        "status": "error",
        "message": f"Unsupported scheduled reply target transport: {transport}",
    }


def _resolve_desktop_agent_dir(agent_name: str):
    """Locate a desktop bridge agent directory, tolerating compiled/packaged layouts."""
    from pathlib import Path

    safe_name = str(agent_name or "").strip()
    if not safe_name:
        return None
    candidate = Path(__file__).resolve().parents[1] / "autoyou_agents" / safe_name
    # Even if this path doesn't exist (compiled build), the manifest loader falls back to the
    # installed package by Path(agent_dir).name, so returning the name-bearing path is safe.
    return candidate


async def _resolve_desktop_response_return_message(action: Dict[str, Any]) -> str:
    """Poll the desktop bridge app until idle, copy its final response, and format it."""
    agent_name = str(action.get("agent_name") or "").strip()
    agent_dir = _resolve_desktop_agent_dir(agent_name)
    if agent_dir is None:
        return ""
    try:
        from autoyou_agents.shared_tools.desktop_app_control import (
            wait_until_idle_and_copy_desktop_response,
        )
    except Exception as exc:  # pragma: no cover - import guard
        LOGGER.error("desktop_response_return: engine import failed: %s", exc)
        return ""

    kwargs: Dict[str, Any] = {}
    for src_key, dst_key in (
        ("poll_interval_seconds", "poll_interval_seconds"),
        ("stable_polls", "stable_polls"),
        ("max_wait_seconds", "max_wait_seconds"),
        ("initial_wait_seconds", "min_initial_wait_seconds"),
    ):
        if action.get(src_key) is not None:
            try:
                kwargs[dst_key] = float(action.get(src_key)) if "polls" not in dst_key else int(action.get(src_key))
            except Exception:
                pass

    try:
        result = await asyncio.to_thread(
            wait_until_idle_and_copy_desktop_response, agent_dir, **kwargs
        )
    except Exception as exc:
        LOGGER.error("desktop_response_return: poll/copy failed: %s", exc)
        return ""

    text = str((result or {}).get("response_text") or "").strip()
    if not text:
        return ""
    prefix = str(action.get("message_prefix") or "").strip()
    return f"{prefix}\n\n{text}" if prefix else text


async def _execute_direct_task_action(task_id: str, task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    action = _task_action(task)
    if not action:
        return None

    reply_target = _task_reply_target(task)
    owner_key = str(task.get("creator_owner_key") or "").strip()
    canonical_user_id = str(task.get("creator_user_id") or "").strip()
    if not reply_target and not owner_key and not canonical_user_id:
        return {
            "status": "error",
            "message": "Scheduled task is missing a saved delivery target.",
        }

    action_type = str(action.get("type") or "").strip().lower()
    if action_type == "desktop_response_return":
        message = await _resolve_desktop_response_return_message(action)
        if not message:
            message = (
                "⚠️ The desktop app finished, but its response couldn't be auto-copied. "
                "Open the app to read the result."
            )
    else:
        message = action["message"]

    return await _queue_notification_for_delivery(
        message,
        owner_key=owner_key,
        canonical_user_id=canonical_user_id,
        external_session_id=_task_external_session_id(task),
        conversation_session_id=_task_conversation_session_id(task),
        reply_target=reply_target,
        source=f"scheduled-task:{task_id or 'unknown'}",
        lock_reply_target=bool(task.get("lock_reply_target")),
    )


def _launch_scheduled_task(task: Dict[str, Any]) -> None:
    task_id = str(task.get("id") or "")
    instruction = str(task.get("instruction") or "").strip()
    execution_instruction = _task_instruction_for_execution(task)

    async def run_and_broadcast() -> None:
        try:
            owner_key = str(task.get("creator_owner_key") or "").strip()
            canonical_user_id = str(task.get("creator_user_id") or "").strip()
            reply_target = _task_reply_target(task)
            append_scheduler_activity_log(
                "task_execution_started",
                agent_kind="tasks",
                item_id=task_id or None,
                status="running",
                message=_message_preview(instruction or execution_instruction, limit=200),
                details={
                    "owner_key": owner_key,
                    "reply_target": dict(reply_target or {}),
                    "run_counter": int(_normalize_timestamp(task.get("run_counter"), 0.0)),
                },
            )
            direct_result = await _execute_direct_task_action(task_id, task)
            if direct_result is not None:
                direct_status = str(direct_result.get("status") or "").strip() or "unknown"
                append_scheduler_activity_log(
                    "task_direct_delivery",
                    agent_kind="tasks",
                    item_id=task_id or None,
                    status=direct_status,
                    message=str(direct_result.get("message") or "Scheduled task direct delivery completed."),
                    details={
                        "queued": bool(direct_result.get("queued")),
                        "owner_key": owner_key,
                    },
                )
                if direct_result.get("status") != "success":
                    message = str(direct_result.get("message") or "Scheduled task delivery failed.")
                    LOGGER.error("Failed to deliver scheduled task %s directly: %s", task_id, message)
                    if reply_target or owner_key or canonical_user_id:
                        await _queue_notification_for_delivery(
                            f"❌ Task Execution Failed:\n{message}",
                            owner_key=owner_key,
                            canonical_user_id=canonical_user_id,
                            external_session_id=_task_external_session_id(task),
                            conversation_session_id=_task_conversation_session_id(task),
                            reply_target=reply_target,
                            source=f"scheduled-task-error:{task_id or 'unknown'}",
                            lock_reply_target=bool(task.get("lock_reply_target")),
                        )
                    else:
                        await broadcast_alert(f"❌ Task Execution Failed: {message}")
                return

            from rest_api import process_chat_message, ChatRequest, get_ai_agent_server_url

            metadata = _build_scheduled_task_execution_metadata(
                task_id,
                task,
                instruction,
                owner_key=owner_key,
                reply_target=reply_target,
            )

            req = ChatRequest(
                message=execution_instruction or instruction,
                metadata=metadata,
                session_id=_task_execution_session_id(task_id, task),
                user_id=_task_creator_user_id(task),
            )
            res = await process_chat_message(
                req,
                ai_agent_url=get_ai_agent_server_url(),
                on_chunk=None,
            )
            response_text = _extract_chat_response_text(res)
            normalized_response_text = _normalize_task_result_text(response_text)
            if normalized_response_text:
                completed_at_s = time.time()
                record_task_recent_output(
                    task_id,
                    normalized_response_text,
                    completed_at_s=completed_at_s,
                )
                append_scheduler_activity_log(
                    "task_execution_result",
                    agent_kind="tasks",
                    item_id=task_id or None,
                    status="completed",
                    message=_message_preview(normalized_response_text, limit=220),
                    details={"owner_key": owner_key},
                )
                delivery_message = _format_task_result_message(
                    normalized_response_text,
                    completed_at_s=completed_at_s,
                )
                delivery_result = await _queue_notification_for_delivery(
                    delivery_message,
                    owner_key=owner_key,
                    canonical_user_id=canonical_user_id,
                    external_session_id=_task_external_session_id(task),
                    conversation_session_id=_task_conversation_session_id(task),
                    reply_target=reply_target,
                    source=f"scheduled-task-result:{task_id or 'unknown'}",
                    lock_reply_target=bool(task.get("lock_reply_target")),
                )
                if delivery_result.get("status") != "success":
                    if reply_target or owner_key or canonical_user_id:
                        append_scheduler_activity_log(
                            "task_result_delivery_failed",
                            agent_kind="tasks",
                            item_id=task_id or None,
                            status=str(delivery_result.get("status") or "error"),
                            message=str(delivery_result.get("message") or "Task result delivery failed."),
                            details={"owner_key": owner_key},
                        )
                    else:
                        await broadcast_alert(delivery_message)
        except Exception as exc:
            LOGGER.error(f"Failed to execute cron task {task_id}: {exc}")
            append_scheduler_activity_log(
                "task_execution_failed",
                agent_kind="tasks",
                item_id=task_id or None,
                status="error",
                message=str(exc),
                details={"owner_key": str(task.get("creator_owner_key") or "").strip()},
            )
            owner_key = str(task.get("creator_owner_key") or "").strip()
            canonical_user_id = str(task.get("creator_user_id") or "").strip()
            reply_target = _task_reply_target(task)
            if reply_target or owner_key or canonical_user_id:
                await _queue_notification_for_delivery(
                    f"❌ Task Execution Failed:\n{exc}",
                    owner_key=owner_key,
                    canonical_user_id=canonical_user_id,
                    external_session_id=_task_external_session_id(task),
                    conversation_session_id=_task_conversation_session_id(task),
                    reply_target=reply_target,
                    source=f"scheduled-task-error:{task_id or 'unknown'}",
                    lock_reply_target=bool(task.get("lock_reply_target")),
                )
            else:
                await broadcast_alert(f"❌ Task Execution Failed: {exc}")
        finally:
            _ACTIVE_TASK_IDS.discard(task_id)
            if task_id:
                _ACTIVE_TASK_HANDLES.pop(task_id, None)

    task_handle = asyncio.create_task(run_and_broadcast())
    if task_id:
        _ACTIVE_TASK_HANDLES[task_id] = task_handle

async def broadcast_alert(text: str):
    """Attempt to send the text to all active messaging partners, notes, page feed, and WebRTC clients."""
    server = _get_runtime_server_module()
    LOGGER.info(f"Broadcasting alert: {text}")
    sent = False

    # Messaging partners
    for reply_target in await get_runtime_messaging_partner_reply_targets_async():
        try:
            result = await _deliver_reply_target_message(reply_target, text)
            if result.get("status") == "success":
                sent = True
            else:
                LOGGER.warning(
                    "%s broadcast failed: %s",
                    _reply_target_display_label(reply_target),
                    result.get("message") or "unknown delivery error",
                )
        except Exception as e:
            LOGGER.warning("%s broadcast failed: %s", _reply_target_display_label(reply_target), e)

    # Notes - persist every alert as a note so the user has a record
    try:
        from autoyou_agents.notes_agent.notes_tool import NotesTool
        notes = NotesTool()
        result = notes.create_note(
            title="Scheduler Alert",
            content=text,
            tags=["scheduler", "alert"],
            category="alert",
        )
        if result.get("success"):
            sent = True
            LOGGER.debug("Broadcast alert persisted as note %s", result.get("note_id"))
        else:
            LOGGER.warning("Notes broadcast failed: %s", result.get("error"))
    except Exception as e:
        LOGGER.warning(f"Notes broadcast failed: {e}")

    # AutoYou Page Service - push alert into the Page feed
    try:
        page_service = None
        if hasattr(server, "get_autoyou_page_service"):
            page_service = server.get_autoyou_page_service()
        if page_service is not None and hasattr(page_service, "_append_item"):
            page_service._append_item("article", "", text, "AutoYou Scheduler")
            sent = True
            LOGGER.debug("Broadcast alert pushed to AutoYou Page feed")
    except Exception as e:
        LOGGER.warning(f"Page Service broadcast failed: {e}")

    # WebRTC - send chat message to every connected datachannel session
    try:
        webrtc = getattr(server, "WEBRTC", None)
        if webrtc is not None and hasattr(webrtc, "datachannel_managers"):
            if hasattr(webrtc, "_live_control_datachannel_sessions"):
                live_sessions = list(webrtc._live_control_datachannel_sessions())
            else:
                live_sessions = []
                seen_manager_ids: set[int] = set()
                for session_id, datachannel_manager in list(webrtc.datachannel_managers.items()):
                    if datachannel_manager is None or not hasattr(datachannel_manager, "send_message"):
                        continue
                    manager_id = id(datachannel_manager)
                    if manager_id in seen_manager_ids:
                        continue
                    seen_manager_ids.add(manager_id)
                    live_sessions.append((str(session_id), datachannel_manager))
            session_ids = [str(session_id) for session_id, _ in live_sessions]
            LOGGER.info("broadcast_alert: WebRTC has %d live datachannel client(s): %s", len(session_ids), session_ids)
            for session_id in session_ids:
                try:
                    ok = await webrtc.send_chat_to_session(
                        session_id,
                        text,
                        metadata={"source": "scheduler", "is_notification": True},
                        user_id=server.get_configured_server_name(),
                    )
                    if ok:
                        sent = True
                        LOGGER.info("broadcast_alert: WebRTC chat delivered to session %s", session_id)
                    else:
                        LOGGER.warning("broadcast_alert: WebRTC send_chat_to_session returned False for %s", session_id)
                except Exception as inner_exc:
                    LOGGER.warning("WebRTC broadcast to session %s failed: %s", session_id, inner_exc)
        else:
            LOGGER.info("broadcast_alert: No WEBRTC manager or no datachannel_managers attribute")
    except Exception as e:
        LOGGER.warning(f"WebRTC broadcast failed: {e}")

    if not sent:
        LOGGER.warning("Could not broadcast alert to any channel. Are services configured?")


async def evaluate_tasks_and_reminders():
    while True:
        sleep_seconds = SCHEDULER_MAX_SLEEP_SECONDS
        try:
            now = time.time()
            next_due_at = now + SCHEDULER_MAX_SLEEP_SECONDS

            # 1. Reminders
            reminders = load_json(REMINDERS_FILE)
            active_reminders = []
            reminders_dirty = False
            for r in reminders:
                reminder_at = _normalize_timestamp(r.get("timestamp_s"), 0.0)
                if reminder_at <= now:
                    # Trigger - queue targeted delivery first, broadcast only as final fallback.
                    alert_text = f"⏰ REMINDER: {r.get('message')}"
                    delivery_target = normalize_reply_target(r.get("delivery_target"))
                    owner_key = str(r.get("creator_owner_key") or "").strip()
                    canonical_user_id = str(r.get("creator_user_id") or "").strip()
                    result: Dict[str, Any] = {"status": "broadcast"}
                    if delivery_target or owner_key or canonical_user_id:
                        result = await _queue_notification_for_delivery(
                            alert_text,
                            owner_key=owner_key,
                            canonical_user_id=canonical_user_id,
                            external_session_id=str(r.get("creator_external_session_id") or "").strip() or None,
                            conversation_session_id=str(r.get("creator_conversation_session_id") or "").strip() or None,
                            reply_target=delivery_target,
                            source=f"reminder:{str(r.get('id') or 'unknown')}",
                            lock_reply_target=bool(r.get("lock_reply_target")),
                        )
                        if result.get("status") != "success":
                            await broadcast_alert(alert_text)
                    else:
                        await broadcast_alert(alert_text)
                    append_scheduler_activity_log(
                        "reminder_triggered",
                        agent_kind="reminders",
                        item_id=str(r.get("id") or "").strip() or None,
                        status=str(result.get("status") if isinstance(result, dict) else "sent"),
                        message=_message_preview(alert_text, limit=200),
                        details={
                            "owner_key": owner_key,
                            "reply_target": dict(delivery_target or {}),
                        },
                    )
                    reminders_dirty = True
                else:
                    active_reminders.append(r)
                    next_due_at = min(next_due_at, reminder_at)
            if reminders_dirty:
                save_json(REMINDERS_FILE, active_reminders)

            # 2. Cron tasks / Intervals
            tasks = load_json(TASKS_FILE)
            updated_tasks = []
            tasks_dirty = False
            for t in tasks:
                task_id = str(t.get("id") or "")
                if not _task_enabled(t):
                    updated_tasks.append(t)
                    continue
                interval_seconds = _task_interval_seconds(t)
                last_run = _normalize_timestamp(t.get("last_run_s"), 0.0)
                due_at = last_run + interval_seconds
                if due_at > now:
                    next_due_at = min(next_due_at, due_at)
                    updated_tasks.append(t)
                    continue

                if task_id and task_id in _ACTIVE_TASK_IDS:
                    LOGGER.info("Scheduled task %s is still running; skipping overlapping execution", task_id)
                    next_due_at = min(next_due_at, now + min(interval_seconds, SCHEDULER_ACTIVE_RETRY_SECONDS))
                    updated_tasks.append(t)
                    continue

                if task_id:
                    _ACTIVE_TASK_IDS.add(task_id)
                t["run_counter"] = max(0, int(_normalize_timestamp(t.get("run_counter"), 0.0))) + 1
                _launch_scheduled_task(t)
                t["last_run_s"] = now
                tasks_dirty = True
                # One-time tasks disable themselves after the first execution
                if _task_is_run_once(t):
                    t["enabled"] = False
                else:
                    next_due_at = min(next_due_at, now + interval_seconds)
                updated_tasks.append(t)

            if tasks_dirty:
                save_json(TASKS_FILE, updated_tasks)

            await process_pending_notifications()

            sleep_seconds = max(
                SCHEDULER_MIN_SLEEP_SECONDS,
                min(SCHEDULER_MAX_SLEEP_SECONDS, next_due_at - time.time()),
            )

        except Exception as e:
            LOGGER.error(f"Scheduler loop error: {e}")
            sleep_seconds = SCHEDULER_MAX_SLEEP_SECONDS

        await asyncio.sleep(sleep_seconds)

async def start_scheduler():
    LOGGER.info("Background scheduler starting...")
    await evaluate_tasks_and_reminders()
