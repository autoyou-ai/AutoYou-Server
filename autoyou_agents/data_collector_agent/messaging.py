# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Consent-gated messaging collectors owned by Data Collector.

These adapters are optional.  The agent remains usable as a standalone
collector; a running AutoYou server only supplies a live WhatsApp or
owner-scoped Telegram Saved Messages runtime when one is already present.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from shared.platform_runtime import (
    find_bundled_browser_executable,
    get_node_command,
    get_node_service_dir,
    get_service_data_dir,
)


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DEFAULT_HISTORY_TIMEOUT_SECONDS = 4 * 60 * 60
_MAX_HISTORY_TIMEOUT_SECONDS = 24 * 60 * 60


def _safe_name(value: object, fallback: str = "item") -> str:
    normalized = _SAFE_NAME_RE.sub("-", str(value or "").strip()).strip(".-")
    return normalized[:120] or fallback


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (int, float)):
        try:
            seconds = float(value)
            if seconds > 10**12:
                seconds /= 1000
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return ""
    return str(value or "").strip()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_date(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = raw[:10]
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date {raw!r}; expected YYYY-MM-DD.") from exc
    return candidate


def _timeline_options(raw: Dict[str, Any]) -> Dict[str, Optional[str]]:
    mode = str(raw.get("timeline_mode") or raw.get("timeline") or "all_time").strip().lower()
    earliest = _parse_date(raw.get("earliest"))
    latest = _parse_date(raw.get("latest"))
    if mode not in {"range", "custom", "bounded"}:
        return {"timeline_mode": "all_time", "earliest": None, "latest": None}
    if earliest and latest and earliest > latest:
        raise ValueError("Earliest date must be on or before the latest date.")
    return {"timeline_mode": "range", "earliest": earliest, "latest": latest}


def normalize_whatsapp_options(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw = dict(raw or {})
    include_personal = _truthy(raw.get("include_personal")) if "include_personal" in raw else True
    include_groups = _truthy(raw.get("include_groups")) if "include_groups" in raw else False
    scope = str(raw.get("chat_scope") or raw.get("scope") or "").strip().lower()
    if scope not in {"personal", "groups", "all"}:
        scope = "all" if include_personal and include_groups else "groups" if include_groups else "personal"
    timeline = _timeline_options(raw)
    return {
        "chat_scope": scope,
        "include_personal": scope in {"personal", "all"},
        "include_groups": scope in {"groups", "all"},
        "timeline_mode": timeline["timeline_mode"],
        "earliest": timeline["earliest"],
        "latest": timeline["latest"],
        "all_available_history": True,
        "ready_timeout_seconds": _bounded_int(raw.get("ready_timeout_seconds"), default=120, minimum=20, maximum=600),
        "timeout_seconds": _bounded_int(
            raw.get("timeout_seconds"),
            default=_DEFAULT_HISTORY_TIMEOUT_SECONDS,
            minimum=60,
            maximum=_MAX_HISTORY_TIMEOUT_SECONDS,
        ),
        # The server-owned WhatsApp bridge may hold the same local auth profile.
        # A selected collection run may pause that bridge; no server/page/AI
        # process is restarted.
        "pause_runtime_bridge": _truthy(raw.get("pause_runtime_bridge")) if "pause_runtime_bridge" in raw else True,
    }


def normalize_telegram_options(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw = dict(raw or {})
    timeline = _timeline_options(raw)
    return {
        "timeline_mode": timeline["timeline_mode"],
        "earliest": timeline["earliest"],
        "latest": timeline["latest"],
        "all_available_history": True,
        "timeout_seconds": _bounded_int(
            raw.get("timeout_seconds"),
            default=_DEFAULT_HISTORY_TIMEOUT_SECONDS,
            minimum=60,
            maximum=_MAX_HISTORY_TIMEOUT_SECONDS,
        ),
    }


def _runtime_server_state() -> Optional[Any]:
    """Find the host only when this agent is embedded in AutoYou.

    The standalone collector never imports server.py.  This is intentionally a
    best-effort optional bridge, not an installation dependency.
    """

    for module_name in ("server", "__main__", "autoyou.server"):
        module = sys.modules.get(module_name)
        state = getattr(module, "STATE", None) if module is not None else None
        if state is not None:
            return state
    return None


def _service_event_loop(service: Any) -> Optional[asyncio.AbstractEventLoop]:
    for attr_name in ("websocket_task", "node_output_task", "_restart_task", "_control_channel_recovery_task", "_qr_wait_task"):
        task = getattr(service, attr_name, None)
        if task is None or not hasattr(task, "get_loop"):
            continue
        try:
            loop = task.get_loop()
        except Exception:
            continue
        if loop is not None and not loop.is_closed():
            return loop
    for task in getattr(service, "_chat_tasks", ()) or ():
        if not hasattr(task, "get_loop"):
            continue
        try:
            loop = task.get_loop()
        except Exception:
            continue
        if loop is not None and not loop.is_closed():
            return loop
    return None


def _resolve_awaitable(value: Any, *, service: Any = None, timeout_seconds: float = 10.0) -> Any:
    if not inspect.isawaitable(value):
        return value
    loop = _service_event_loop(service) if service is not None else None
    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(value, loop)
        try:
            return future.result(timeout=timeout_seconds)
        except TimeoutError:
            future.cancel()
            raise
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    result: Dict[str, Any] = {}

    def run_in_thread() -> None:
        try:
            result["value"] = asyncio.run(value)
        except Exception as exc:  # pragma: no cover - defensive runtime bridge
            result["error"] = exc

    thread = threading.Thread(target=run_in_thread, daemon=True, name="data-collector-awaitable")
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise TimeoutError("Timed out waiting for the local messaging service.")
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _whatsapp_worker_path() -> Optional[Path]:
    path = Path(__file__).with_name("whatsapp_history_dump.mjs")
    return path if path.is_file() else None


def _whatsapp_service_anchor() -> Path:
    return _repo_root() / "whatsapp_service.py"


def _runtime_whatsapp_service() -> Optional[Any]:
    state = _runtime_server_state()
    return getattr(state, "whatsapp_service", None) if state is not None else None


def _whatsapp_paths() -> Dict[str, Any]:
    service = _runtime_whatsapp_service()
    anchor = _whatsapp_service_anchor()
    state_dir = Path(getattr(service, "state_dir", "") or get_service_data_dir("whatsapp", anchor=anchor))
    node_dir = Path(getattr(service, "node_dir", "") or get_node_service_dir("whatsapp", anchor))
    node_command = str(getattr(service, "node_command", "") or get_node_command(anchor))
    device_name = str(getattr(service, "device_name", "") or os.getenv("DEVICE_NAME") or "AutoYou-WhatsApp")
    return {
        "node_dir": node_dir,
        "node_command": node_command,
        "device_name": device_name,
        "auth_path": Path(os.getenv("WWEBJS_AUTH_PATH") or state_dir / ".wwebjs_auth"),
        "cache_path": Path(os.getenv("WWEBJS_CACHE_PATH") or state_dir / ".wwebjs_cache"),
        "service_attached": service is not None,
    }


def _command_available(command: str) -> bool:
    value = str(command or "").strip()
    if not value:
        return False
    if os.path.sep in value or (os.path.altsep and os.path.altsep in value):
        return Path(value).is_file()
    return shutil.which(value) is not None


def _find_browser() -> Optional[Path]:
    roots: List[Path] = []
    if os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
        roots.append(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]))
    if os.getenv("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
    roots.extend(
        [
            _repo_root() / "servers" / "windows" / "artifacts" / "playwright-browsers",
            _repo_root() / "servers" / "windows" / "artifacts" / "backend" / "AutoYouServer" / "runtime" / "playwright",
        ]
    )
    patterns = (
        "chromium-*/chrome-win/chrome.exe",
        "chromium-*/chrome-win64/chrome.exe",
        "chromium-*/chrome-linux/chrome",
        "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    )
    for root in roots:
        try:
            candidates = [candidate for pattern in patterns for candidate in root.expanduser().resolve().glob(pattern)]
        except OSError:
            continue
        if candidates:
            return sorted(candidates)[-1]
    bundled = find_bundled_browser_executable(_whatsapp_service_anchor())
    return bundled if bundled is not None and bundled.is_file() else None


def whatsapp_support() -> Dict[str, Any]:
    paths = _whatsapp_paths()
    node_dir = Path(paths["node_dir"])
    auth_path = Path(paths["auth_path"])
    sessions = []
    try:
        if auth_path.is_dir():
            sessions = [path for path in auth_path.iterdir() if path.is_dir() and path.name.startswith("session-")]
    except OSError:
        sessions = []
    browser = _find_browser()
    node_available = _command_available(str(paths["node_command"]))
    modules_ready = (node_dir / "node_modules" / "whatsapp-web.js").exists()
    worker_path = _whatsapp_worker_path()
    ready = bool(node_available and modules_ready and worker_path and sessions and browser)
    if not node_available:
        message = "Node.js is not available for WhatsApp collection."
    elif not modules_ready:
        message = "WhatsApp collection is unavailable because its local Node runtime is missing."
    elif worker_path is None:
        message = "The Data Collector WhatsApp worker is unavailable."
    elif not sessions:
        message = "Pair WhatsApp in AutoYou before collecting its local history."
    elif not browser:
        message = "Chromium is unavailable for the local WhatsApp collection worker."
    else:
        message = "WhatsApp full available-history collection is ready."
    return {
        "id": "whatsapp",
        "label": "WhatsApp",
        "available": ready,
        "ready": ready,
        "message": message,
        "requires": ["local WhatsApp pairing"],
        "supports": {"personal": True, "groups": True, "all_available_history": True, "time_budget": True},
        "service_attached": bool(paths["service_attached"]),
    }


def _runtime_telegram_service() -> Optional[Any]:
    state = _runtime_server_state()
    return getattr(state, "telegram_user_service", None) if state is not None else None


def _telegram_status(service: Any) -> Dict[str, Any]:
    get_status = getattr(service, "get_status", None)
    if not callable(get_status):
        return {}
    try:
        value = _resolve_awaitable(get_status(), service=service, timeout_seconds=8)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def telegram_saved_messages_support() -> Dict[str, Any]:
    service = _runtime_telegram_service()
    if service is None:
        return {
            "id": "telegram_saved_messages",
            "label": "Telegram Saved Messages",
            "available": False,
            "ready": False,
            "message": "Connect Telegram Saved Messages in Admin Settings > Messaging first.",
            "requires": ["owner-only Telegram Saved Messages", "Admin Settings > Messaging consent"],
            "supports": {"all_available_history": True, "owner_only": True},
        }
    status = _telegram_status(service)
    owner_scoped = status.get("owner_scoped") is True
    consent = status.get("training_export_consent") is True
    client = getattr(service, "client", None)
    iterator = getattr(client, "iter_messages", None)
    ready = bool(owner_scoped and consent and callable(iterator))
    if not owner_scoped:
        message = "Telegram collection is restricted to the owner’s Saved Messages workspace."
    elif not consent:
        message = "Enable Saved Messages training consent in Admin Settings > Messaging to collect it."
    elif not callable(iterator):
        message = "Telegram Saved Messages is connected but its local history client is unavailable."
    else:
        message = "Telegram Saved Messages full available-history collection is ready."
    return {
        "id": "telegram_saved_messages",
        "label": "Telegram Saved Messages",
        "available": ready,
        "ready": ready,
        "message": message,
        "requires": ["owner-only Telegram Saved Messages", "Admin Settings > Messaging consent"],
        "supports": {"all_available_history": True, "owner_only": True, "dialog_enumeration": False},
    }


def messaging_capabilities() -> Dict[str, Dict[str, Any]]:
    return {
        "whatsapp": whatsapp_support(),
        "telegram_saved_messages": telegram_saved_messages_support(),
    }


def _pause_whatsapp_bridge() -> bool:
    service = _runtime_whatsapp_service()
    if service is None:
        return False
    try:
        status = _resolve_awaitable(service.get_status(), service=service, timeout_seconds=8)
    except Exception:
        status = {}
    active = bool(
        getattr(service, "node_process", None)
        or getattr(service, "websocket", None)
        or (
            isinstance(status, dict)
            and (status.get("websocket_connected") or status.get("node_process_running") or status.get("client_ready"))
        )
    )
    if not active:
        return False
    try:
        _resolve_awaitable(service.stop(), service=service, timeout_seconds=60)
        time.sleep(1)
        return True
    except Exception:
        return False


def _restart_whatsapp_bridge() -> None:
    service = _runtime_whatsapp_service()
    if service is None:
        return
    try:
        _resolve_awaitable(service.start(), service=service, timeout_seconds=120)
    except Exception:
        pass


def _within_dates(timestamp: str, earliest: Optional[str], latest: Optional[str]) -> bool:
    value = str(timestamp or "")[:10]
    if (earliest or latest) and not value:
        return False
    return bool((not earliest or value >= earliest) and (not latest or value <= latest))


def _cancel_requested(cancel_check: Optional[Callable[[], bool]]) -> bool:
    try:
        return bool(cancel_check and cancel_check())
    except Exception:
        return False


def _whatsapp_sessions(payload: Dict[str, Any], machine_id: str, options: Dict[str, Any]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in payload.get("messages") or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("body") or row.get("message") or row.get("text") or "").strip()
        if not text:
            continue
        chat = row.get("chat") if isinstance(row.get("chat"), dict) else {}
        chat_id = str(chat.get("id") or "").strip() or "unknown"
        chat_name = str(chat.get("name") or chat_id).strip() or "WhatsApp chat"
        is_group = bool(chat.get("isGroup") or chat.get("is_group"))
        timestamp = _timestamp(row.get("timestamp"))
        if not _within_dates(timestamp, options.get("earliest"), options.get("latest")):
            continue
        session = grouped.setdefault(
            chat_id,
            {
                "id": f"whatsapp-{_safe_name(chat_id, 'chat')}",
                "app": "whatsapp",
                "title": chat_name,
                "project": "WhatsApp groups" if is_group else "WhatsApp direct messages",
                "target": {"kind": "group" if is_group else "recipient", "id": chat_id, "name": chat_name},
                "machine_id": machine_id,
                "created_at": timestamp or _now_iso(),
                "turns": [],
            },
        )
        from_me = bool(row.get("fromMe") or row.get("from_me"))
        session["turns"].append(
            {
                "role": "assistant" if from_me else "user",
                "direction": "sent" if from_me else "received",
                "text": text,
                "timestamp": timestamp,
            }
        )
    sessions = []
    for session in grouped.values():
        session["turns"].sort(key=lambda item: str(item.get("timestamp") or ""))
        if session["turns"]:
            session["created_at"] = str(session["turns"][0].get("timestamp") or session["created_at"])
            sessions.append(session)
    return sessions


def collect_whatsapp_history(
    *,
    machine_id: str,
    options: Optional[Dict[str, Any]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    if _cancel_requested(cancel_check):
        return {"status": "cancelled", "message": "WhatsApp collection was cancelled."}
    try:
        config = normalize_whatsapp_options(options)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    support = whatsapp_support()
    if not support["ready"]:
        return {"status": "error", "message": support["message"], "support": support}
    paths = _whatsapp_paths()
    worker_path = _whatsapp_worker_path()
    if worker_path is None:  # Capability state can change between checks.
        return {"status": "error", "message": "The Data Collector WhatsApp worker is unavailable."}
    with tempfile.TemporaryDirectory(prefix="autoyou-whatsapp-history-") as temp_dir:
        output_path = Path(temp_dir) / "history.json"
        command = [
            str(paths["node_command"]),
            str(worker_path),
            "--output",
            str(output_path),
            "--node-whatsapp-dir",
            str(paths["node_dir"]),
            "--auth-path",
            str(paths["auth_path"]),
            "--cache-path",
            str(paths["cache_path"]),
            "--client-id",
            str(paths["device_name"]),
            "--scope",
            str(config["chat_scope"]),
            "--ready-timeout-ms",
            str(config["ready_timeout_seconds"] * 1000),
            "--history-timeout-ms",
            str(max(60, config["timeout_seconds"] - 30) * 1000),
            "--all-available-history",
        ]
        if config.get("earliest"):
            command.extend(["--earliest", str(config["earliest"])])
        if config.get("latest"):
            command.extend(["--latest", str(config["latest"])])
        environment = os.environ.copy()
        environment.update(
            {
                "AUTOYOU_WHATSAPP_NODE_DIR": str(paths["node_dir"]),
                "WWEBJS_AUTH_PATH": str(paths["auth_path"]),
                "WWEBJS_CACHE_PATH": str(paths["cache_path"]),
                "DEVICE_NAME": str(paths["device_name"]),
            }
        )
        from shared.native_webkit import browser_executable
        native_browser = browser_executable()
        if native_browser:
            environment["AUTOYOU_WEBKIT_EXECUTABLE"] = str(native_browser)
        else:
            environment.pop("AUTOYOU_WEBKIT_EXECUTABLE", None)
            browser = _find_browser()
            if browser is not None:
                environment["PUPPETEER_EXECUTABLE_PATH"] = str(browser)
        paused = bool(config["pause_runtime_bridge"] and _pause_whatsapp_bridge())
        try:
            process = subprocess.Popen(
                command,
                cwd=str(_repo_root()),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
            deadline = time.monotonic() + config["timeout_seconds"] + config["ready_timeout_seconds"] + 30
            while True:
                if _cancel_requested(cancel_check):
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    try:
                        process.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=10)
                    return {"status": "cancelled", "message": "WhatsApp collection was cancelled."}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.communicate(timeout=10)
                    return {"status": "error", "message": "WhatsApp collection exceeded its configured time budget."}
                try:
                    process.communicate(timeout=min(0.5, remaining))
                    return_code = int(process.returncode or 0)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except OSError as exc:
            return {"status": "error", "message": f"WhatsApp collection could not start: {exc}"}
        finally:
            if paused:
                _restart_whatsapp_bridge()
        if return_code != 0 or not output_path.is_file():
            return {"status": "error", "message": "WhatsApp history collection did not complete."}
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"status": "error", "message": "WhatsApp history collection produced an unreadable local result."}
    sessions = _whatsapp_sessions(payload, machine_id, config)
    return {
        "status": "success",
        "sessions": sessions,
        "chat_count": int(payload.get("chatCount") or 0),
        "message_count": int(payload.get("messageCount") or 0),
        "time_limit_reached": bool(payload.get("timeLimitReached")),
        "all_available_history": True,
        "time_budget_seconds": config["timeout_seconds"],
    }


def _telegram_message_text(message: Any) -> str:
    return str(getattr(message, "message", None) or getattr(message, "text", None) or "").strip()


def _telegram_message_is_forwarded(message: Any) -> bool:
    return bool(getattr(message, "fwd_from", None) or getattr(message, "forward", None))


def collect_telegram_saved_messages(
    *,
    machine_id: str,
    options: Optional[Dict[str, Any]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    if _cancel_requested(cancel_check):
        return {"status": "cancelled", "message": "Telegram collection was cancelled."}
    try:
        config = normalize_telegram_options(options)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    support = telegram_saved_messages_support()
    if not support["ready"]:
        return {"status": "error", "message": support["message"], "support": support}
    service = _runtime_telegram_service()
    client = getattr(service, "client", None)
    deadline = time.monotonic() + config["timeout_seconds"]

    async def read_history() -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        timed_out = False
        async for message in client.iter_messages("me", limit=None, reverse=True):
            if _cancel_requested(cancel_check):
                return {"rows": rows, "time_limit_reached": False, "cancelled": True}
            if time.monotonic() >= deadline:
                timed_out = True
                break
            if _telegram_message_is_forwarded(message):
                continue
            text = _telegram_message_text(message)
            if not text:
                continue
            timestamp = _timestamp(getattr(message, "date", None))
            if not _within_dates(timestamp, config.get("earliest"), config.get("latest")):
                continue
            from_me = bool(getattr(message, "out", False))
            rows.append(
                {
                    "role": "assistant" if from_me else "user",
                    "direction": "sent" if from_me else "received",
                    "text": text,
                    "timestamp": timestamp,
                }
            )
        return {"rows": rows, "time_limit_reached": timed_out}

    try:
        result = _resolve_awaitable(
            read_history(),
            service=service,
            timeout_seconds=config["timeout_seconds"] + 10,
        )
    except Exception:
        return {"status": "error", "message": "Telegram Saved Messages history could not be collected."}
    if isinstance(result, dict) and result.get("cancelled"):
        return {"status": "cancelled", "message": "Telegram collection was cancelled."}
    rows = result.get("rows", []) if isinstance(result, dict) else []
    session = {
        "id": "telegram-saved-messages",
        "app": "telegram_saved_messages",
        "title": "Telegram Saved Messages",
        "project": "Telegram Saved Messages",
        "target": {"kind": "recipient", "id": "saved-messages", "name": "Telegram Saved Messages"},
        "machine_id": machine_id,
        "created_at": str(rows[0].get("timestamp") if rows else _now_iso()),
        "turns": rows,
    }
    return {
        "status": "success",
        "sessions": [session] if rows else [],
        "message_count": len(rows),
        "time_limit_reached": bool(result.get("time_limit_reached")) if isinstance(result, dict) else False,
        "all_available_history": True,
        "time_budget_seconds": config["timeout_seconds"],
    }


__all__ = [
    "collect_telegram_saved_messages",
    "collect_whatsapp_history",
    "messaging_capabilities",
    "normalize_telegram_options",
    "normalize_whatsapp_options",
    "telegram_saved_messages_support",
    "whatsapp_support",
]
