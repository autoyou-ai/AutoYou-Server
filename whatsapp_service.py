# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-60e5aa809b104ac5e5f8a906

"""
WhatsApp Web.js Service Integration

This module provides integration with the Node.js WhatsApp Web.js client
for the AutoYou Agents system. It handles:
- Node.js subprocess management (start/stop)
- WebSocket communication with Node.js client
- QR code generation for pairing
- Message processing for "Notes to Self" functionality
- Status monitoring and cleanup

Based on: whatsapp-web.js library
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
import base64
import uuid
from dataclasses import replace
import qrcode
import re
from io import BytesIO
from typing import Dict, Any, Optional, List, Set
from pairing_router import PairingRouter, pairing_router
from shared.platform_runtime import (
    configure_runtime,
    get_node_command,
    get_node_service_dir,
    get_service_data_dir,
    normalize_local_filesystem_path,
)
from shared.process_lifecycle import (
    add_parent_pid_environment,
    force_kill_process_tree,
    process_spawn_kwargs,
)
from shared.session_execution import (
    STATUS_QUEUED,
    SessionQueueFullError,
    SessionTurnTimeoutError,
    get_session_execution_manager,
)
from shared.client_conversation_contract import build_autoyou_conversation_metadata
from shared.pending_media_queue import (
    delete_media_payload,
    prune_and_trim_media_entries,
    read_media_payload,
    store_media_payload,
)
from shared.log_redaction import redact_identifier, redact_payload
from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-60e5aa809b104ac5e5f8a906"


try:
    import websockets
    from websockets.exceptions import ConnectionClosed, InvalidURI
except ImportError:
    websockets = None
    ConnectionClosed = Exception
    InvalidURI = Exception

try:
    import httpx
except ImportError:
    httpx = None

LOGGER = logging.getLogger("autoyou.whatsapp_service")

_WHATSAPP_JID_RE = re.compile(
    r"(?<![A-Za-z0-9_.+*-])"
    r"(?P<jid>[A-Za-z0-9_.+-]{2,}@(c\.us|g\.us|lid|broadcast|newsletter))"
    r"(?![A-Za-z0-9_.-])",
    re.IGNORECASE,
)
_WHATSAPP_LOG_KEY_RE = re.compile(
    r"\b(?P<key>chat|chat_id|chatid|from|phone|phoneNumber|phone_number|"
    r"recipient|remoteChatId|remote_chat_id|sender|selfChatId|self_chat_id|to)="
    r"(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_WHATSAPP_CONTEXT_PHONE_RE = re.compile(
    r"(?P<prefix>\b(?:chat|from|phone|recipient|sender|to)\s+)"
    r"(?P<value>\+?\d[\d\s().-]{5,}\d)"
    r"(?P<suffix>[:\s,.)]|$)",
    re.IGNORECASE,
)
_WHATSAPP_IGNORED_NON_SELF_LOG_RE = re.compile(
    r"\[WhatsApp\]\s+Ignoring non-self sent message\b",
    re.IGNORECASE,
)
_WHATSAPP_OPTIONAL_METADATA_LOG_RE = re.compile(
    r"\[WhatsApp\]\s+"
    r"(?:Contact lookup failed, continuing without contact metadata|"
    r"Chat contact lookup failed, continuing without chat contact metadata|"
    r"Contact lookup skipped optional metadata|"
    r"Chat contact lookup skipped optional metadata|"
    r"Skipping \S+ chat lookup for unsupported channel metadata)",
    re.IGNORECASE,
)
_WHATSAPP_IGNORABLE_METADATA_ERROR_RE = re.compile(
    r"(Data passed to getter must include an id property .* got undefined|"
    r"Cannot read properties of undefined \(reading ['\"]_serialized['\"]\)|"
    r"Cannot read properties of undefined \(reading ['\"]description['\"]\))",
    re.IGNORECASE,
)
_WHATSAPP_OBFUSCATED_LOG_ENV_NAMES = (
    "AUTOYOU_WHATSAPP_OBFUSCATED_LOGGING",
    "WHATSAPP_OBFUSCATED_LOGGING",
    "AUTOYOU_WHATSAPP_LOG_NON_SELF_SENT",
    "WHATSAPP_LOG_NON_SELF_SENT",
)


def _get_env_int(name: str, default: int, *, minimum: int = 1, maximum: Optional[int] = None) -> int:
    raw_value = str(os.getenv(name, "") or "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _get_env_bool_any(names: tuple[str, ...], default: bool = False) -> bool:
    for name in names:
        raw_value = os.getenv(name)
        if raw_value is None:
            continue
        normalized = str(raw_value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def _whatsapp_obfuscated_child_logging_enabled() -> bool:
    return _get_env_bool_any(_WHATSAPP_OBFUSCATED_LOG_ENV_NAMES, default=False)


def _is_optional_whatsapp_child_diagnostic(line: str) -> bool:
    if _WHATSAPP_IGNORED_NON_SELF_LOG_RE.search(line):
        return True
    if _WHATSAPP_OPTIONAL_METADATA_LOG_RE.search(line):
        return True
    return False


def _should_log_whatsapp_child_line(line: str) -> bool:
    if _WHATSAPP_IGNORED_NON_SELF_LOG_RE.search(line):
        return _whatsapp_obfuscated_child_logging_enabled()
    if _WHATSAPP_OPTIONAL_METADATA_LOG_RE.search(line) and _WHATSAPP_IGNORABLE_METADATA_ERROR_RE.search(line):
        return _whatsapp_obfuscated_child_logging_enabled()
    return True


def _find_browser_executable_in_root(root: Path) -> Optional[Path]:
    patterns = (
        "chromium-*/chrome-win64/chrome.exe",
        "chromium-*/chrome-win/chrome.exe",
        "chromium-*/chrome-linux/chrome",
        "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
    )
    for pattern in patterns:
        matches = sorted(root.glob(pattern))
        for candidate in reversed(matches):
            if candidate.is_file():
                return candidate
    return None


def _iter_playwright_browser_roots() -> List[Path]:
    roots: List[Path] = []
    raw_env_root = str(os.getenv("PLAYWRIGHT_BROWSERS_PATH", "") or "").strip()
    if raw_env_root and raw_env_root != "0":
        roots.append(Path(raw_env_root).expanduser())

    if sys.platform == "win32":
        local_app_data = str(os.getenv("LOCALAPPDATA", "") or "").strip()
        if local_app_data:
            roots.append(Path(local_app_data) / "ms-playwright")
    elif sys.platform == "darwin":
        roots.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    else:
        roots.append(Path.home() / ".cache" / "ms-playwright")
    return roots


def _find_local_chromium_executable() -> Optional[Path]:
    for root in _iter_playwright_browser_roots():
        try:
            candidate = _find_browser_executable_in_root(normalize_local_filesystem_path(root))
        except Exception:
            candidate = None
        if candidate is not None:
            return candidate

    if sys.platform == "win32":
        local_app_data = str(os.getenv("LOCALAPPDATA", "") or "").strip()
        windows_candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
        if local_app_data:
            windows_candidates.append(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe")
        for candidate in windows_candidates:
            if candidate.is_file():
                return candidate

    return None


def _redact_whatsapp_id(value: Any) -> Any:
    return redact_identifier(value)


def _redact_whatsapp_log_line(value: Any) -> str:
    """Redact WhatsApp identifiers in free-form child-process log lines."""
    text = str(value or "")
    if not text:
        return text

    def _redact_keyed(match: re.Match[str]) -> str:
        raw_value = match.group("value")
        leading = raw_value[:1] if raw_value[:1] in {"'", '"'} else ""
        trailing = raw_value[-1:] if raw_value[-1:] in {"'", '"'} and len(raw_value) > len(leading) else ""
        core = raw_value[len(leading) : len(raw_value) - len(trailing) if trailing else len(raw_value)]
        if not core or core == "-":
            return match.group(0)
        return f"{match.group('key')}={leading}{_redact_whatsapp_id(core)}{trailing}"

    def _redact_context_phone(match: re.Match[str]) -> str:
        return (
            f"{match.group('prefix')}"
            f"{_redact_whatsapp_id(match.group('value'))}"
            f"{match.group('suffix')}"
        )

    redacted = _WHATSAPP_LOG_KEY_RE.sub(_redact_keyed, text)
    redacted = _WHATSAPP_CONTEXT_PHONE_RE.sub(_redact_context_phone, redacted)
    return _WHATSAPP_JID_RE.sub(
        lambda match: str(_redact_whatsapp_id(match.group("jid"))),
        redacted,
    )


def _get_conversation_session_manager():
    try:
        from rest_api import get_session_manager

        return get_session_manager()
    except Exception:
        return None


def _resolve_conversation_identity(identity, *, start_new_thread: bool = False):
    session_manager = _get_conversation_session_manager()
    if session_manager is None:
        return identity

    owner_key = str(getattr(identity, "owner_key", "") or "").strip()
    if not owner_key:
        return identity

    try:
        if start_new_thread and hasattr(session_manager, "advance_conversation_thread"):
            thread_id, canonical_session_id = session_manager.advance_conversation_thread(owner_key)
        elif hasattr(session_manager, "get_current_conversation_thread") and hasattr(
            session_manager,
            "get_current_conversation_session_id",
        ):
            thread_id = session_manager.get_current_conversation_thread(owner_key)
            canonical_session_id = session_manager.get_current_conversation_session_id(owner_key)
        else:
            return identity
    except Exception:
        return identity

    try:
        return replace(
            identity,
            canonical_session_id=str(canonical_session_id),
            thread_id=(int(thread_id) if int(thread_id) > 1 else None),
        )
    except Exception:
        return identity


def _extract_conversation_request(message_text: str) -> tuple[bool, str, bool]:
    normalized_text = str(message_text or "").strip()
    lowered = normalized_text.lower()
    start_new_thread = lowered in {"/new", "/newconversation", "/newchat"}
    return start_new_thread, normalized_text, start_new_thread


def _normalize_whatsapp_address(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    local_part = raw.split("@", 1)[0].strip()
    if not local_part:
        return ""
    digits_only = re.sub(r"\D+", "", local_part)
    return digits_only or local_part

class WhatsAppNodeService:
    """Manages Node.js WhatsApp Web.js client and WebSocket communication."""
    
    def __init__(self, websocket_port: int = 8083, device_name: str = "AutoYou-WhatsApp", ai_api_url: str = "http://localhost:8081/api/chat"):
        configure_runtime(__file__)
        self.websocket_port = websocket_port
        self.device_name = device_name
        self.ai_api_url = ai_api_url
        self.node_process = None
        self.websocket = None
        self.websocket_task = None
        self.message_log = []  # Store "Notes to Self" messages
        self.last_qr_code = None
        self.connection_status = "disconnected"
        self.phone_number = None
        self.node_command = get_node_command(__file__)

        self.state_dir = get_service_data_dir("whatsapp", anchor=__file__)
        self._pending_voice_reply_file = self.state_dir / "pending_voice_replies.json"
        self._pending_media_reply_file = self.state_dir / "pending_media_replies.json"
        self.node_dir = get_node_service_dir("whatsapp", __file__)
        self.node_client_path = self.node_dir / "whatsapp_client.js"
        self.is_ready = False
        self.is_paired = False
        self.last_self_chat_id = None
        
        # Enhanced state tracking for comprehensive client synchronization
        self.client_state = "UNLAUNCHED"  # Track WhatsApp Web.js client state
        self.battery_info = None
        self.reconnection_attempts = 0
        self.last_state_change = None
        self.loading_screen_active = False
        self.remote_session_saved = False
        self.last_status_snapshot = None
        
        # Connection health tracking
        self.last_heartbeat = time.time()
        self.websocket_reconnect_attempts = 0
        self._stop_requested = False
        self._shutdown_in_progress = False
        self._control_recovery_attempts = _get_env_int("WHATSAPP_CONTROL_RECOVERY_ATTEMPTS", 3, minimum=1, maximum=20)
        self._ready_control_recovery_attempts = _get_env_int(
            "WHATSAPP_READY_CONTROL_RECOVERY_ATTEMPTS",
            6,
            minimum=self._control_recovery_attempts,
            maximum=30,
        )
        self._restart_task: Optional[asyncio.Task] = None
        self._control_channel_recovery_task: Optional[asyncio.Task] = None
        self.node_output_task: Optional[asyncio.Task] = None
        self._attached_external_node = False
        self.chat_tasks = set()
        self._restart_request_tasks: Set[asyncio.Task] = set()
        self._pending_voice_reply_limit = 20
        self._pending_voice_replies: List[Dict[str, Any]] = self._load_pending_voice_replies()
        self._pending_media_reply_limit = 20
        self._pending_media_replies: List[Dict[str, Any]] = self._load_pending_media_replies()
        self._pending_media_command_acks: Dict[str, asyncio.Future] = {}

    def _configured_agent_label(self) -> str:
        """Return the mutable server display name without changing WhatsApp auth identity."""
        try:
            import server

            configured_name = getattr(server, "get_configured_server_name", lambda: "")()
            if str(configured_name or "").strip():
                return str(configured_name).strip()
        except Exception:
            pass
        return self.device_name

    def _track_chat_task(self, coro, *, session_key: str, label: str) -> asyncio.Task:
        """Track background WhatsApp chat processing tasks."""
        task = asyncio.create_task(coro)
        self.chat_tasks.add(task)

        def _done(done_task: asyncio.Task) -> None:
            self.chat_tasks.discard(done_task)
            try:
                exc = done_task.exception()
            except asyncio.CancelledError:
                return
            except Exception as inspect_err:
                LOGGER.debug("Failed to inspect %s task for %s: %s", label, session_key, inspect_err)
                return
            if exc is not None:
                LOGGER.warning("%s task failed for %s: %s", label, session_key, exc)

        task.add_done_callback(_done)
        return task

    def _schedule_pending_media_flush(self, reason: str) -> None:
        self._track_chat_task(
            self._flush_pending_media_replies(),
            session_key=f"whatsapp-media-flush:{reason}",
            label="whatsapp-media-flush",
        )

    def _track_restart_request_task(self, coro) -> asyncio.Task:
        """Track client-originated restart requests so shutdown can cancel them."""
        task = asyncio.create_task(coro)
        self._restart_request_tasks.add(task)

        def _done(done_task: asyncio.Task) -> None:
            self._restart_request_tasks.discard(done_task)
            try:
                exc = done_task.exception()
            except asyncio.CancelledError:
                return
            except Exception as inspect_err:
                LOGGER.debug("Failed to inspect WhatsApp restart request task: %s", inspect_err)
                return
            if exc is not None:
                LOGGER.warning("WhatsApp restart request task failed: %s", exc)

        task.add_done_callback(_done)
        return task

    def _get_ai_agent_base_url(self) -> str:
        candidate = str(self.ai_api_url or "").strip()
        if candidate.endswith("/api/chat"):
            candidate = candidate[: -len("/api/chat")]
        return candidate or "http://127.0.0.1:8081"

    def _remember_self_chat_id(self, message_data: Optional[Dict[str, Any]] = None) -> Optional[str]:
        payload = message_data or {}
        for candidate in (
            payload.get("remoteChatId"),
            payload.get("to"),
            payload.get("from"),
        ):
            chat_id = str(candidate or "").strip()
            if chat_id and chat_id.lower() != "status@broadcast":
                self.last_self_chat_id = chat_id
                return chat_id
        return self.last_self_chat_id

    def _resolve_typing_chat_id(self, preferred_chat_id: Optional[str] = None) -> Optional[str]:
        for candidate in (
            preferred_chat_id,
            self.last_self_chat_id,
        ):
            chat_id = str(candidate or "").strip()
            if chat_id:
                return chat_id
        return None

    def _is_verified_inbound_self_message(self, message_data: Dict[str, Any]) -> bool:
        """Accept only messages from the QR-paired account's self chat.

        The Node bridge classifies the chat. Python keeps the final gate simple:
        only messages WhatsApp marks as sent by the paired account are accepted.
        """
        from_me = bool(message_data.get("fromMe", False))
        if not bool(message_data.get("selfChat", False)):
            return False

        return from_me

    async def _request_graceful_node_shutdown(self) -> bool:
        """Ask the Node.js client to destroy WhatsApp resources before exit."""
        if self._attached_external_node:
            LOGGER.info("Skipping graceful WhatsApp shutdown for externally-owned Node.js bridge")
            return False
        if not self._is_websocket_connected():
            return False
        try:
            await self.websocket.send(json.dumps({"action": "shutdown", "data": {}}))
            LOGGER.info("Requested graceful WhatsApp Node.js shutdown over WebSocket")
            return True
        except Exception as e:
            LOGGER.warning(f"Failed to request graceful WhatsApp shutdown: {e}")
            return False

    def _collect_process_tree_pids(self, process_pid: int) -> List[int]:
        """Snapshot a process tree before shutdown so children can still be killed after parent exit."""
        tracked_pids: List[int] = []
        try:
            import psutil

            parent = psutil.Process(process_pid)
            tracked_pids.append(parent.pid)
            tracked_pids.extend(child.pid for child in parent.children(recursive=True))
        except Exception:
            pass
        return sorted({pid for pid in tracked_pids if isinstance(pid, int) and pid > 0})

    def _live_process_tree_pids(self, process_pid: Optional[int], extra_pids: Optional[List[int]] = None) -> List[int]:
        """Return tracked process-tree PIDs that still appear alive."""
        candidate_pids = []
        try:
            if process_pid is not None:
                candidate_pids.append(int(process_pid))
        except Exception:
            pass
        for pid in extra_pids or []:
            try:
                candidate_pids.append(int(pid))
            except Exception:
                continue
        candidate_pids = sorted({pid for pid in candidate_pids if pid > 0})
        if not candidate_pids:
            return []

        try:
            import psutil

            live_pids: List[int] = []
            for pid in candidate_pids:
                try:
                    proc = psutil.Process(pid)
                    if proc.is_running() and str(proc.status()).lower() != "zombie":
                        live_pids.append(pid)
                except psutil.NoSuchProcess:
                    continue
                except Exception:
                    continue
            return live_pids
        except ImportError:
            if self.node_process is not None and self.node_process.returncode is None and process_pid:
                return [int(process_pid)]
            return []

    def _looks_like_whatsapp_node_process(self, proc) -> bool:
        script_name = Path(self.node_client_path).name.lower()
        try:
            if int(getattr(proc, "pid", 0) or 0) <= 0 or proc.pid == os.getpid():
                return False
            cmdline = [str(part or "") for part in (proc.cmdline() or [])]
        except Exception:
            return False

        lowered_parts = [part.lower() for part in cmdline if part]
        if not any(script_name in part for part in lowered_parts):
            return False

        try:
            process_cwd = str(proc.cwd() or "")
        except Exception:
            process_cwd = ""

        node_dir = os.path.normcase(str(Path(self.node_dir).resolve()))
        if process_cwd and os.path.normcase(process_cwd).startswith(node_dir):
            return True

        for part in cmdline:
            try:
                resolved = os.path.normcase(str(Path(part).expanduser().resolve()))
            except Exception:
                resolved = part.lower()
            if node_dir in resolved:
                return True

        return True

    async def _cleanup_lingering_whatsapp_processes(self) -> None:
        try:
            import psutil
        except ImportError:
            return

        lingering_pids: List[int] = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            if self._looks_like_whatsapp_node_process(proc):
                lingering_pids.append(int(proc.pid))

        for pid in sorted({pid for pid in lingering_pids if pid > 0}):
            LOGGER.warning("Found lingering WhatsApp Node.js process before start/after stop (PID: %s)", pid)
            await self._force_kill_process_tree(pid, extra_pids=[pid])

    def _is_node_process_running(self) -> bool:
        """Return True when the Node.js WhatsApp process is still running."""
        return self.node_process is not None and self.node_process.returncode is None

    def _websocket_uris(self, *, probe: bool = False) -> List[str]:
        suffix = "/?probe=1" if probe else ""
        candidates = [
            f"ws://localhost:{self.websocket_port}{suffix}",
            f"ws://127.0.0.1:{self.websocket_port}{suffix}",
            f"ws://[::1]:{self.websocket_port}{suffix}",
        ]
        seen: Set[str] = set()
        ordered: List[str] = []
        for uri in candidates:
            if uri not in seen:
                seen.add(uri)
                ordered.append(uri)
        return ordered

    async def _attach_existing_websocket_bridge(self) -> bool:
        """Attach to an already-running WhatsApp Node bridge without taking ownership."""
        if not websockets:
            return False

        try:
            if not await self._wait_for_websocket(timeout=3, log_failure=False):
                return False
            if not await self._connect_websocket():
                return False

            self.node_process = None
            self._attached_external_node = True
            self._shutdown_in_progress = False
            self._stop_requested = False
            LOGGER.info(
                "Attached to existing WhatsApp WebSocket bridge on port %s without taking process ownership",
                self.websocket_port,
            )
            return True
        except Exception as exc:
            LOGGER.debug("Existing WhatsApp WebSocket bridge attach failed: %s", exc)
            return False

    def _is_websocket_connected(self) -> bool:
        """Return True when the Python control WebSocket is open."""
        if self.websocket is None:
            return False
        try:
            if hasattr(self.websocket, "closed"):
                return not self.websocket.closed
            if hasattr(self.websocket, "close_code"):
                return self.websocket.close_code is None
            return True
        except Exception as e:
            LOGGER.debug(f"Error checking websocket status: {e}")
            return False

    def _had_ready_control_snapshot(self) -> bool:
        """Return True when recent bridge state says WhatsApp itself was ready."""
        if self.is_ready or str(self.connection_status or "").strip().lower() == "connected":
            return True
        if str(self.client_state or "").strip().upper() in {"READY", "CONNECTED"}:
            return True
        snapshot = self.last_status_snapshot if isinstance(self.last_status_snapshot, dict) else {}
        snapshot_service_status = str(snapshot.get("current_service_status") or "").strip().lower()
        snapshot_web_state = str(snapshot.get("web_state") or "").strip().upper()
        return bool(
            snapshot.get("ready")
            or snapshot_service_status == "connected"
            or snapshot_web_state in {"READY", "CONNECTED"}
        )

    def _control_recovery_sleep_seconds(self, attempt: int, *, was_ready: bool) -> float:
        if not was_ready:
            return float(attempt)
        return float(min(max(attempt * 2, 2), 8))

    async def _cancel_task(self, task: Optional[asyncio.Task], label: str) -> None:
        """Cancel a background task, unless it is the current task."""
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as e:
            LOGGER.warning(f"Error cancelling {label}: {e}")

    def _status_snapshot(self) -> str:
        phone = _redact_whatsapp_id(self.phone_number or "-")
        return (
            f"status={self.connection_status} ready={self.is_ready} paired={self.is_paired} "
            f"phone={phone} ws={self._is_websocket_connected()} node={self._is_node_process_running()} "
            f"client_state={self.client_state}"
        )

    @staticmethod
    def _payload_summary(payload: Any, limit: int = 240) -> str:
        try:
            rendered = json.dumps(redact_payload(payload), default=str)
        except Exception:
            rendered = repr(redact_payload(payload))
        if len(rendered) > limit:
            return rendered[: limit - 3] + "..."
        return rendered

    def _refresh_pairing_flags(self) -> None:
        self.is_paired = bool(
            self.connection_status == "connected"
            and self.is_ready
            and isinstance(self.phone_number, str)
            and self.phone_number.strip()
        )

    def _log_websocket_event(self, event: str, payload: Any, *, phase: str = "recv") -> None:
        if event in {
            "status",
            "status_response",
            "state_changed",
            "phone_number",
            "qr",
            "client_restarting",
            "service_restart_request",
            "reconnection_scheduled",
            "reconnection_failed",
        }:
            LOGGER.info(
                "WhatsApp WS %s event=%s payload=%s snapshot=%s",
                phase,
                event,
                self._payload_summary(payload),
                self._status_snapshot(),
            )

    async def _request_status_snapshot(self, reason: str) -> None:
        if not self._is_websocket_connected():
            return
        try:
            await self.websocket.send(json.dumps({"action": "get_status", "data": {"reason": reason}}))
            LOGGER.info("Requested WhatsApp status snapshot (%s): %s", reason, self._status_snapshot())
        except Exception as e:
            LOGGER.warning(f"Failed to request WhatsApp status snapshot ({reason}): {e}")
        
    async def start(self) -> bool:
        """Start the Node.js WhatsApp client and establish WebSocket connection."""
        try:
            self._shutdown_in_progress = False
            self._stop_requested = False
            self.connection_status = "starting"
            self.client_state = "UNLAUNCHED"
            self.is_ready = False
            self.is_paired = False
            self.phone_number = None
            self.last_self_chat_id = None
            self.last_qr_code = None
            self.last_status_snapshot = None
            self.websocket_reconnect_attempts = 0

            if await self._attach_existing_websocket_bridge():
                return True

            self._attached_external_node = False

            # Check if Node.js is available
            if not self._check_nodejs():
                LOGGER.error("Node.js is not available")
                return False
                
            # Check if WhatsApp client exists
            if not os.path.exists(self.node_client_path):
                LOGGER.error(f"WhatsApp client not found at {self.node_client_path}")
                return False
                
            # Check if node_modules exists
            node_modules_path = os.path.join(self.node_dir, "node_modules")
            if not os.path.exists(node_modules_path):
                LOGGER.error(f"Node modules not found at {node_modules_path}. Run 'npm install' in {self.node_dir}")
                return False
                
            # Stop existing process if running
            await self.stop()
            await self._cleanup_lingering_whatsapp_processes()
            self._shutdown_in_progress = False
            self._stop_requested = False
            self.connection_status = "starting"
            self.client_state = "UNLAUNCHED"
            self.is_ready = False
            self.is_paired = False
            self.phone_number = None
            self.last_self_chat_id = None
            self.last_qr_code = None
            self.last_status_snapshot = None
            self.websocket_reconnect_attempts = 0
            
            # Start Node.js WhatsApp client
            if not await self._start_node_client():
                return False
                
            # Wait for WebSocket server to be ready
            if not await self._wait_for_websocket():
                LOGGER.error("WhatsApp WebSocket server failed to start")
                await self.stop()
                return False
                
            # Connect to WebSocket
            if not await self._connect_websocket():
                LOGGER.error("Failed to connect to WhatsApp WebSocket")
                await self.stop()
                return False
                
            LOGGER.info(f"WhatsApp service started successfully on WebSocket port {self.websocket_port}")
            return True
            
        except Exception as e:
            LOGGER.error(f"Failed to start WhatsApp service: {e}")
            return False
    
    async def stop(self) -> None:
        """Stop the WhatsApp service and Node.js process."""
        try:
            self._shutdown_in_progress = True
            self._stop_requested = True

            for task in list(self.chat_tasks):
                if not task.done():
                    task.cancel()
            if self.chat_tasks:
                await asyncio.gather(*list(self.chat_tasks), return_exceptions=True)
            self.chat_tasks.clear()

            current_task = asyncio.current_task()
            restart_task = self._restart_task
            if current_task is not restart_task:
                restart_request_tasks = [
                    task for task in list(self._restart_request_tasks) if task is not current_task
                ]
                self._restart_request_tasks.clear()
                if restart_request_tasks:
                    for task in restart_request_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*restart_request_tasks, return_exceptions=True)

            if restart_task is not current_task:
                self._restart_task = None
                await self._cancel_task(restart_task, "restart task")

            recovery_task = self._control_channel_recovery_task
            self._control_channel_recovery_task = None
            await self._cancel_task(recovery_task, "control channel recovery task")
                
            # Terminate Node.js process with improved Windows handling
            websocket_task = self.websocket_task
            graceful_shutdown_requested = await self._request_graceful_node_shutdown()
            if self.node_process:
                process_pid = None
                tracked_pids: List[int] = []
                try:
                    process_pid = self.node_process.pid
                    tracked_pids = self._collect_process_tree_pids(process_pid)
                    LOGGER.info(f"Terminating Node.js process (PID: {process_pid})")

                    if graceful_shutdown_requested:
                        try:
                            await asyncio.wait_for(self.node_process.wait(), timeout=6.0)
                            LOGGER.info(f"Node.js process (PID: {process_pid}) exited after graceful shutdown request")
                        except asyncio.TimeoutError:
                            LOGGER.warning(f"Node.js process (PID: {process_pid}) did not exit after graceful shutdown request")

                    if self.node_process.returncode is None:
                        # Fall back to standard terminate if the WebSocket-requested shutdown did not complete.
                        self.node_process.terminate()

                    try:
                        await asyncio.wait_for(self.node_process.wait(), timeout=3.0)
                        LOGGER.info(f"Node.js process (PID: {process_pid}) terminated gracefully")
                    except asyncio.TimeoutError:
                        # Force kill if not terminated gracefully
                        LOGGER.warning(f"Node.js process (PID: {process_pid}) did not terminate gracefully, force killing")
                        try:
                            self.node_process.kill()
                            await asyncio.wait_for(self.node_process.wait(), timeout=2.0)
                            LOGGER.info(f"Node.js process (PID: {process_pid}) force killed")
                        except asyncio.TimeoutError:
                            LOGGER.warning(f"Node.js process (PID: {process_pid}) did not respond to kill signal")
                        
                except Exception as e:
                    LOGGER.warning(f"Error stopping Node.js process: {e}")
                finally:
                    self.node_process = None
                    if process_pid is not None:
                        live_pids = self._live_process_tree_pids(process_pid, tracked_pids)
                        if live_pids:
                            await self._force_kill_process_tree(process_pid, extra_pids=live_pids)
                        else:
                            LOGGER.debug("No live WhatsApp Node.js process tree remains after graceful stop")

            node_output_task = self.node_output_task
            self.node_output_task = None
            await self._cancel_task(node_output_task, "Node output task")

            # Close WebSocket connection with proper task handling after shutdown request is delivered
            self.websocket_task = None
            await self._cancel_task(websocket_task, "WebSocket task")

            if self.websocket:
                try:
                    await asyncio.wait_for(self.websocket.close(), timeout=2.0)
                except Exception as e:
                    LOGGER.warning(f"Error closing WebSocket: {e}")
                finally:
                    self.websocket = None
                    
            self.connection_status = "disconnected"
            self.is_ready = False
            self.is_paired = False
            self.phone_number = None
            self.last_self_chat_id = None
            self.last_qr_code = None
            self.last_status_snapshot = None
            self.websocket_reconnect_attempts = 0
            if self._attached_external_node:
                LOGGER.info("Detached from externally-owned WhatsApp Node.js bridge")
                self._attached_external_node = False
            else:
                await self._cleanup_lingering_whatsapp_processes()
            LOGGER.info("WhatsApp service stopped")
            
        except Exception as e:
            LOGGER.error(f"Error stopping WhatsApp service: {e}")
    
    async def _force_kill_process_tree(self, process_pid: int, extra_pids: Optional[List[int]] = None):
        """Force kill remaining WhatsApp child processes and free the WebSocket port."""
        force_kill_process_tree(
            process_pid,
            extra_pids=extra_pids or (),
            process_group=True,
        )
        
        await self._ensure_port_free(self.websocket_port)
    
    async def _ensure_port_free(self, port: int):
        """Ensure a port is free by killing any processes using it."""
        try:
            try:
                import psutil

                listeners = psutil.net_connections(kind="tcp")
                for conn in listeners:
                    if (
                        conn.laddr
                        and conn.laddr.port == port
                        and conn.pid
                        and conn.pid != os.getpid()
                    ):
                        try:
                            psutil.Process(conn.pid).kill()
                            LOGGER.info(f"Killed process {conn.pid} using port {port}")
                        except psutil.NoSuchProcess:
                            pass
                        except Exception as e:
                            LOGGER.warning(f"Failed to kill process {conn.pid}: {e}")
                return
            except ImportError:
                pass

            if sys.platform == "win32":
                result = subprocess.run(
                    ["netstat", "-ano", "-p", "TCP"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if result.returncode == 0:
                    lines = result.stdout.split('\n')
                    for line in lines:
                        if f":{port}" in line and "LISTENING" in line:
                            parts = line.split()
                            if len(parts) >= 5:
                                pid = parts[-1]
                                try:
                                    subprocess.run(
                                        ["taskkill", "/F", "/PID", pid],
                                        capture_output=True,
                                        timeout=5
                                    )
                                    LOGGER.info(f"Killed process {pid} using port {port}")
                                except Exception as e:
                                    LOGGER.warning(f"Failed to kill process {pid}: {e}")
            else:
                try:
                    result = subprocess.run(
                        ["lsof", "-ti", f"tcp:{port}"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                except FileNotFoundError:
                    LOGGER.debug(f"lsof not available while checking port {port}")
                    return

                if result.returncode == 0 and result.stdout:
                    for pid in {line.strip() for line in result.stdout.splitlines() if line.strip()}:
                        try:
                            subprocess.run(
                                ["kill", "-9", pid],
                                capture_output=True,
                                timeout=5
                            )
                            LOGGER.info(f"Killed process {pid} using port {port}")
                        except Exception as e:
                                    LOGGER.warning(f"Failed to kill process {pid}: {e}")
                                    
        except Exception as e:
            LOGGER.warning(f"Error ensuring port {port} is free: {e}")

    async def _remove_tree_with_retries(self, path: str, label: str, attempts: int = 8, initial_delay: float = 0.5) -> bool:
        """Retry directory removal to ride out short-lived Chromium/LevelDB locks on Windows."""
        delay = initial_delay

        def onerror(func, failed_path, exc_info):
            try:
                os.chmod(failed_path, 0o700)
                func(failed_path)
            except Exception:
                LOGGER.warning(f"Failed to remove path during cleanup: {failed_path}")

        for attempt in range(1, attempts + 1):
            try:
                if os.path.exists(path):
                    shutil.rmtree(path, onerror=onerror)
            except Exception as e:
                LOGGER.warning(f"Failed to remove {label} on attempt {attempt}/{attempts}: {e}")

            if not os.path.exists(path):
                return True

            if attempt < attempts:
                LOGGER.info(
                    "Waiting %.1fs before retrying WhatsApp cleanup for %s (%d/%d)",
                    delay,
                    label,
                    attempt,
                    attempts,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 2.0)

        return not os.path.exists(path)
    
    async def restart(self) -> bool:
        """Restart the WhatsApp service gracefully."""
        if self._shutdown_in_progress:
            LOGGER.info("Ignoring WhatsApp restart request because shutdown is already in progress")
            return False

        if self._restart_task and not self._restart_task.done():
            LOGGER.info("WhatsApp service restart already in progress")
            return await self._restart_task

        self._restart_task = asyncio.create_task(self._restart_impl())
        try:
            return await self._restart_task
        finally:
            if self._restart_task and self._restart_task.done():
                self._restart_task = None

    async def _restart_impl(self) -> bool:
        """Internal restart implementation."""
        try:
            LOGGER.info("Restarting WhatsApp service...")
            
            # Store current configuration before restart
            current_websocket_port = self.websocket_port
            current_device_name = self.device_name
            current_ai_api_url = self.ai_api_url
            
            # Stop the current service
            await self.stop()
            
            # Reset state variables for fresh start
            self.connection_status = "disconnected"
            self.is_ready = False
            self.client_state = "UNLAUNCHED"
            self.battery_info = None
            self.reconnection_attempts = 0
            self.last_state_change = None
            self.loading_screen_active = False
            self.remote_session_saved = False
            self.last_heartbeat = time.time()
            self.websocket_reconnect_attempts = 0
            self._pending_voice_replies.clear()
            self._persist_pending_voice_replies()
            self._pending_media_replies.clear()
            self._persist_pending_media_replies()
            self.last_status_snapshot = None
            
            # Clear transient connection state before reinitializing.
            self.last_qr_code = None
            self.phone_number = None
            self.last_self_chat_id = None
            
            # Restore configuration
            self.websocket_port = current_websocket_port
            self.device_name = current_device_name
            self.ai_api_url = current_ai_api_url
            
            # Start the service again
            success = await self.start()
            
            if success:
                LOGGER.info("WhatsApp service restarted successfully")
                return True
            else:
                LOGGER.error("Failed to restart WhatsApp service")
                return False
                
        except Exception as e:
            LOGGER.error(f"Error restarting WhatsApp service: {e}")
            return False

    def _iter_node_command_candidates(self) -> List[str]:
        """Yield plausible Node.js commands, preferring bundle-local fallbacks."""
        candidates: List[str] = []
        seen: Set[str] = set()

        def _append_candidate(value: Any) -> None:
            text = str(value or "").strip()
            if not text:
                return

            if os.path.sep in text or (os.path.altsep and os.path.altsep in text):
                try:
                    normalized_path = normalize_local_filesystem_path(text)
                    text = str(normalized_path)
                    normalized = os.path.normcase(text)
                except Exception:
                    normalized = os.path.normcase(text)
            else:
                normalized = text.lower()

            if normalized in seen:
                return
            seen.add(normalized)
            candidates.append(text)

        _append_candidate(self.node_command)

        try:
            node_dir = Path(self.node_dir).resolve()
            bundle_root = node_dir.parent.parent
            node_binary_names = ["node.exe", "node"] if sys.platform == "win32" else ["node"]
            for node_binary_name in node_binary_names:
                for candidate in (
                    bundle_root / "runtime" / "node" / "bin" / node_binary_name,
                    bundle_root / "runtime" / "node-runtime" / "bin" / node_binary_name,
                    bundle_root / "runtime" / node_binary_name,
                ):
                    if candidate.is_file():
                        _append_candidate(candidate)
        except Exception:
            pass

        _append_candidate("node")
        return candidates
    
    def _check_nodejs(self) -> bool:
        """Check if Node.js is available."""
        last_error = "unknown"
        attempted_commands: List[str] = []

        for node_command in self._iter_node_command_candidates():
            attempted_commands.append(node_command)
            try:
                result = subprocess.run(
                    [node_command, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except Exception as exc:
                last_error = f"{exc.__class__.__name__}: {exc}"
                LOGGER.warning("Node.js probe raised via %s: %s", node_command, exc)
                continue

            if result.returncode == 0:
                self.node_command = node_command
                LOGGER.info(f"Node.js found via {self.node_command}: {result.stdout.strip()}")
                return True

            stderr = str(result.stderr or "").strip()
            stdout = str(result.stdout or "").strip()
            last_error = stderr or stdout or f"exit={result.returncode}"
            LOGGER.warning("Node.js probe failed via %s: %s", node_command, last_error)

        LOGGER.error(
            "Node.js is not available after probing %s (last error: %s)",
            attempted_commands,
            last_error,
        )
        return False
    
    async def _start_node_client(self) -> bool:
        """Start the Node.js WhatsApp client."""
        try:
            # Get AI_AGENT_SERVER_PORT from environment variable to avoid circular import
            ai_agent_port = os.getenv("AI_AGENT_SERVER_PORT", "8081")
            # Change to node directory and start client
            env = add_parent_pid_environment(os.environ.copy())
            env["WEBSOCKET_PORT"] = str(self.websocket_port)
            env["DEVICE_NAME"] = self.device_name
            env["SERVER_NAME"] = self._configured_agent_label()
            env["CHAT_API_URL"] = self.ai_api_url or f"http://localhost:{ai_agent_port}/api/chat"
            
            whatsapp_config_dir = self.state_dir
            uploads_dir = get_service_data_dir("uploads", anchor=__file__)
            env["WWEBJS_AUTH_PATH"] = str(normalize_local_filesystem_path(whatsapp_config_dir / ".wwebjs_auth"))
            env["WWEBJS_CACHE_PATH"] = str(normalize_local_filesystem_path(whatsapp_config_dir / ".wwebjs_cache"))
            env["UPLOADS_DIR"] = str(normalize_local_filesystem_path(uploads_dir))

            from shared.native_webkit import browser_executable
            native_browser = browser_executable()
            if native_browser:
                env["AUTOYOU_WEBKIT_EXECUTABLE"] = str(native_browser)
            else:
                env.pop("AUTOYOU_WEBKIT_EXECUTABLE", None)

            if not native_browser:
                # Validate externally provided browser path first.
                # If it points to a non-existent binary, clear it so Puppeteer can
                # fall back to its own discovery/download logic.
                configured_browser_path = env.get("PUPPETEER_EXECUTABLE_PATH")
                if configured_browser_path and not os.path.exists(configured_browser_path):
                    LOGGER.warning(
                        "Configured PUPPETEER_EXECUTABLE_PATH does not exist: %s. "
                        "Clearing override and using Puppeteer default browser resolution.",
                        configured_browser_path
                    )
                    env.pop("PUPPETEER_EXECUTABLE_PATH", None)

                if "PUPPETEER_EXECUTABLE_PATH" not in env:
                    selected = _find_local_chromium_executable()
                    if selected is not None:
                        env["PUPPETEER_EXECUTABLE_PATH"] = str(normalize_local_filesystem_path(selected))
                        LOGGER.info(
                            "Using Chromium executable for WhatsApp browser runtime: %s",
                            env["PUPPETEER_EXECUTABLE_PATH"],
                        )

                # Detect Linux ARM architecture for Puppeteer (e.g. Raspberry Pi).
                # IMPORTANT: macOS Apple Silicon is arm64 too, but should not use
                # the Debian path '/usr/bin/chromium-browser'.
                import platform
                system = platform.system().lower()
                arch = platform.machine().lower()
                if system == "linux" and ('arm' in arch or 'aarch64' in arch):
                    if "PUPPETEER_EXECUTABLE_PATH" not in env:
                        linux_arm_candidates = [
                            "/usr/bin/chromium-browser",
                            "/usr/bin/chromium",
                        ]
                        selected = next((p for p in linux_arm_candidates if os.path.exists(p)), None)
                        if selected:
                            env["PUPPETEER_EXECUTABLE_PATH"] = str(normalize_local_filesystem_path(selected))
                            LOGGER.info(
                                "Linux ARM architecture detected (%s). Injecting PUPPETEER_EXECUTABLE_PATH=%s",
                                arch,
                                env["PUPPETEER_EXECUTABLE_PATH"],
                            )
                        else:
                            LOGGER.warning(
                                "Linux ARM architecture detected (%s) but no Chromium binary found in expected paths. "
                                "Proceeding with Puppeteer default browser resolution.",
                                arch
                            )
                elif system == "darwin" and "PUPPETEER_EXECUTABLE_PATH" not in env:
                    # Prefer the Playwright Chromium that is bundled with AutoYou
                    # (set by the Swift host via PLAYWRIGHT_BROWSERS_PATH).  Fall
                    # back to a system Chrome installation if the bundle is absent.
                    playwright_browsers = env.get("PLAYWRIGHT_BROWSERS_PATH", "")
                    bundled_chrome: Optional[str] = None
                    if playwright_browsers and os.path.isdir(playwright_browsers):
                        from pathlib import Path as _Path
                        for _chromium_dir in sorted(_Path(playwright_browsers).glob("chromium-*")):
                            _candidate = (
                                _chromium_dir
                                / "chrome-mac-arm64"
                                / "Google Chrome for Testing.app"
                                / "Contents"
                                / "MacOS"
                                / "Google Chrome for Testing"
                            )
                            if _candidate.is_file():
                                bundled_chrome = str(_candidate)
                                break
                            _candidate_x64 = (
                                _chromium_dir
                                / "chrome-mac-x64"
                                / "Google Chrome for Testing.app"
                                / "Contents"
                                / "MacOS"
                                / "Google Chrome for Testing"
                            )
                            if _candidate_x64.is_file():
                                bundled_chrome = str(_candidate_x64)
                                break

                    mac_candidates = list(filter(None, [
                        bundled_chrome,
                        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                        "/Applications/Chromium.app/Contents/MacOS/Chromium",
                    ]))
                    selected = next((p for p in mac_candidates if os.path.exists(p)), None)
                    if selected:
                        env["PUPPETEER_EXECUTABLE_PATH"] = str(normalize_local_filesystem_path(selected))
                        LOGGER.info(
                            "macOS detected (%s). Using browser executable at %s",
                            arch,
                            env["PUPPETEER_EXECUTABLE_PATH"],
                        )
            
            
            LOGGER.info(f"Starting Node.js client with WEBSOCKET_PORT={self.websocket_port}, DEVICE_NAME={self.device_name}, CHAT_API_URL={env['CHAT_API_URL']}")
            
            # Isolate the bridge in its own session/process group so explicit
            # shutdown can take Chromium descendants with it on every OS.
            _extra = process_spawn_kwargs(hide_window=True)
            self.node_process = await asyncio.create_subprocess_exec(
                self.node_command, "whatsapp_client.js",
                cwd=str(normalize_local_filesystem_path(self.node_dir)),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=False,
                **_extra,
            )
            
            LOGGER.info(f"Started Node.js WhatsApp client (PID: {self.node_process.pid})")
            self._attached_external_node = False
            
            # Start monitoring stdout and stderr
            self.node_output_task = asyncio.create_task(self._monitor_node_output(self.node_process))
            
            return True
            
        except Exception as e:
            LOGGER.error(f"Failed to start Node.js client: {e}")
            return False
    
    async def _monitor_node_output(self, process=None):
        """Monitor Node.js process output for debugging."""
        process = process or self.node_process
        if not process:
            return
        process_pid = getattr(process, "pid", None)
            
        async def read_stdout():
            try:
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    decoded_line = _redact_whatsapp_log_line(line.decode(errors="replace").strip())
                    if decoded_line and _should_log_whatsapp_child_line(decoded_line):
                        LOGGER.info("[Node.js PID %s] %s", process_pid, decoded_line)
            except Exception as e:
                LOGGER.error(f"Error reading stdout: {e}")
        
        async def read_stderr():
            try:
                while True:
                    line = await process.stderr.readline()
                    if not line:
                        break
                    decoded_line = _redact_whatsapp_log_line(line.decode(errors="replace").strip())
                    if decoded_line and _should_log_whatsapp_child_line(decoded_line):
                        if _is_optional_whatsapp_child_diagnostic(decoded_line):
                            LOGGER.info("[Node.js PID %s] %s", process_pid, decoded_line)
                        else:
                            LOGGER.error("[Node.js PID %s ERROR] %s", process_pid, decoded_line)
            except Exception as e:
                LOGGER.error(f"Error reading stderr: {e}")
        
        # Start both readers concurrently
        await asyncio.gather(
            read_stdout(),
            read_stderr(),
            return_exceptions=True
        )
    
    async def _wait_for_websocket(self, timeout: int = 30, *, log_failure: bool = True) -> bool:
        """Wait for WebSocket server to be ready."""
        start_time = time.time()
        attempt = 0
        
        while time.time() - start_time < timeout:
            attempt += 1
            last_error: Optional[Exception] = None
            for uri in self._websocket_uris(probe=True):
                try:
                    LOGGER.info(f"Attempt {attempt}: Trying to connect to WebSocket at {uri}")
                    
                    # Use asyncio.wait_for with websockets.connect
                    # Allow very large frames for media payloads coming from Node WebSocket
                    ws = await asyncio.wait_for(websockets.connect(uri, max_size=None), timeout=2)
                    await ws.close()
                    # If connection succeeds, WebSocket server is ready
                    LOGGER.info(f"Successfully connected to WebSocket server on attempt {attempt} via {uri}")
                    return True
                        
                except Exception as e:
                    last_error = e
                    LOGGER.debug(f"Attempt {attempt} failed to connect to WebSocket at {uri}: {e}")
            if last_error is not None:
                LOGGER.debug(f"Attempt {attempt} failed to connect to all WhatsApp WebSocket loopback candidates: {last_error}")
                
            # Check if Node.js process is still running
            if self.node_process and self.node_process.returncode is not None:
                LOGGER.error(f"Node.js process has exited with code: {self.node_process.returncode}")
                # Try to get stderr output for debugging
                if self.node_process.stderr:
                    try:
                        stderr_output = await asyncio.wait_for(self.node_process.stderr.read(), timeout=1)
                        if stderr_output:
                            LOGGER.error(f"Node.js stderr: {stderr_output.decode().strip()}")
                    except:
                        pass
                return False
                    
            await asyncio.sleep(1)
                
        log = LOGGER.error if log_failure else LOGGER.debug
        log(f"WebSocket connection failed after {attempt} attempts over {timeout} seconds")
        return False
    
    async def _connect_websocket(self) -> bool:
        """Connect to the Node.js WhatsApp WebSocket server."""
        try:
            if not websockets:
                LOGGER.error("websockets library not available")
                return False

            if self._is_websocket_connected():
                return True

            if self.websocket and not self._is_websocket_connected():
                try:
                    await self.websocket.close()
                except Exception:
                    pass
                finally:
                    self.websocket = None
                
            last_error: Optional[Exception] = None
            for uri in self._websocket_uris():
                try:
                    # Remove message size limit to support large media payloads from WhatsApp client
                    self.websocket = await websockets.connect(uri, max_size=None)
                    
                    # Start WebSocket message handler
                    self.websocket_task = asyncio.create_task(self._websocket_handler())
                    self.websocket_reconnect_attempts = 0
                    
                    LOGGER.info(f"Connected to WhatsApp WebSocket at {uri}")
                    await self._request_status_snapshot("post-connect")
                    return True
                except Exception as exc:
                    last_error = exc
                    self.websocket = None
                    LOGGER.debug("Failed to connect to WhatsApp WebSocket at %s: %s", uri, exc)

            if last_error is not None:
                raise last_error
            return False
            
        except Exception as e:
            LOGGER.error(f"Failed to connect to WebSocket: {e}")
            return False

    def _schedule_control_channel_recovery(self, reason: str) -> None:
        """Schedule a single control-channel recovery task."""
        if self._stop_requested or self._shutdown_in_progress:
            LOGGER.info(
                "Skipping WhatsApp control channel recovery during shutdown: %s",
                reason,
            )
            return
        if self._control_channel_recovery_task and not self._control_channel_recovery_task.done():
            LOGGER.info(
                "WhatsApp control channel recovery already in progress, ignoring duplicate request: %s",
                reason,
            )
            return
        self._control_channel_recovery_task = asyncio.create_task(
            self._recover_control_channel(reason)
        )

    async def _recover_control_channel(self, reason: str) -> None:
        """Recover the Python control WebSocket or restart the service as fallback."""
        try:
            if self._stop_requested or self._shutdown_in_progress:
                return

            if self._is_node_process_running() or self._attached_external_node:
                was_ready = self._had_ready_control_snapshot()
                self.connection_status = "recovering"
                self.is_ready = False
                max_attempts = (
                    self._ready_control_recovery_attempts
                    if was_ready
                    else self._control_recovery_attempts
                )

                for attempt in range(1, max_attempts + 1):
                    if self._stop_requested or self._shutdown_in_progress:
                        return
                    if self._is_websocket_connected():
                        return

                    self.websocket_reconnect_attempts = attempt
                    LOGGER.warning(
                        "Attempting WhatsApp control WebSocket recovery (%s), attempt %s/%s",
                        reason,
                        attempt,
                        max_attempts,
                    )
                    if await self._connect_websocket():
                        LOGGER.info("Recovered WhatsApp control WebSocket")
                        return
                    await asyncio.sleep(
                        self._control_recovery_sleep_seconds(attempt, was_ready=was_ready)
                    )

            if not self._stop_requested and not self._shutdown_in_progress:
                if self._attached_external_node:
                    LOGGER.warning(
                        "WhatsApp control WebSocket recovery failed (%s); leaving externally-owned bridge untouched",
                        reason,
                    )
                    self.connection_status = "disconnected"
                    self.is_ready = False
                    self._refresh_pairing_flags()
                    return
                LOGGER.warning(
                    "WhatsApp control WebSocket recovery failed (%s); restarting service",
                    reason,
                )
                if self._control_channel_recovery_task is asyncio.current_task():
                    self._control_channel_recovery_task = None
                await self.restart()
        except Exception as e:
            LOGGER.error(f"Error recovering WhatsApp control channel: {e}")
        finally:
            if self._is_websocket_connected():
                self.websocket_reconnect_attempts = 0
            if self._control_channel_recovery_task is asyncio.current_task():
                self._control_channel_recovery_task = None
    
    async def _websocket_handler(self):
        """Handle incoming WebSocket messages from Node.js client."""
        websocket = self.websocket
        # from __debug_provenance_p__ import submit
        disconnect_reason = "closed"
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self._handle_websocket_message(data)
                except json.JSONDecodeError:
                    LOGGER.warning(
                        "Invalid JSON received from WhatsApp WebSocket len=%d",
                        len(str(message or "")),
                    )
                except Exception as e:
                    LOGGER.error(f"Error handling WebSocket message: {e}")
        except asyncio.CancelledError:
            disconnect_reason = "cancelled"
            raise
        except ConnectionClosed:
            disconnect_reason = "connection_closed"
            LOGGER.info("WebSocket connection closed")
        except Exception as e:
            disconnect_reason = f"handler_error:{e}"
            LOGGER.error(f"WebSocket handler error: {e}")
        finally:
            if self.websocket is websocket:
                self.websocket = None
            if self.websocket_task is asyncio.current_task():
                self.websocket_task = None
            if disconnect_reason != "cancelled" and not self._stop_requested:
                self._schedule_control_channel_recovery(disconnect_reason)
    
    async def _handle_websocket_message(self, data: Dict[str, Any]):
        """Handle specific WebSocket message types."""
        event = data.get("event")
        payload = data.get("data")
        
        # Update heartbeat for connection health
        self.last_heartbeat = time.time()
        self._log_websocket_event(str(event), payload)
        
        if event == "qr":
            self.last_qr_code = payload
            if not self.is_ready:
                self.connection_status = "pairing"
            self._refresh_pairing_flags()
            LOGGER.info("Received QR code from WhatsApp client")
            
        elif event == "status":
            old_status = self.connection_status
            payload = (payload or "").strip().lower()
            if payload in {"starting", "pairing", "authenticated", "auth_stalled", "recovering", "restarting", "connecting"}:
                self.connection_status = payload
                self.is_ready = False
                self.is_paired = False
            else:
                self.connection_status = payload
            
            if payload == "connected":
                self.last_qr_code = None
                self.is_ready = True
                self._refresh_pairing_flags()
                LOGGER.info("WhatsApp client reported connected status")
                await self._request_status_snapshot("status:connected")
                await self._flush_pending_voice_replies()
                self._schedule_pending_media_flush("status:connected")
            elif payload == "pairing":
                self.is_ready = False
                self._refresh_pairing_flags()
                LOGGER.info("WhatsApp client is waiting for QR scan")
            elif payload == "starting":
                self.last_qr_code = None
                self.is_ready = False
                self._refresh_pairing_flags()
                LOGGER.info("WhatsApp client is still initializing")
            elif payload == "authenticated":
                self.last_qr_code = None
                self.is_ready = False
                self._refresh_pairing_flags()
                LOGGER.info("WhatsApp client authenticated; waiting for ready state")
                await self._request_status_snapshot("status:authenticated")
            elif payload == "auth_stalled":
                self.last_qr_code = None
                self.is_ready = False
                self._refresh_pairing_flags()
                LOGGER.warning("WhatsApp client authenticated but did not become ready")
                await self._request_status_snapshot("status:auth_stalled")
            elif payload == "disconnected":
                if self._is_node_process_running() and not self.is_ready and not self._stop_requested:
                    self.connection_status = "starting" if not self.last_qr_code else "pairing"
                    self._refresh_pairing_flags()
                    LOGGER.info("WhatsApp client control channel connected before pairing state was ready")
                else:
                    self.is_ready = False
                    self._refresh_pairing_flags()
                    LOGGER.info("WhatsApp client disconnected")
            elif payload == "auth_failure":
                self.is_ready = False
                self._refresh_pairing_flags()
                LOGGER.error("WhatsApp authentication failed")
                
            if old_status != self.connection_status:
                LOGGER.info(f"WhatsApp status changed: {old_status} -> {self.connection_status}")
                
        elif event == "state_changed":
            # Handle WhatsApp Web.js state changes
            old_state = self.client_state
            # Extract state from the data object
            new_state = payload.get("state") if isinstance(payload, dict) else payload
            LOGGER.debug(f"Received state_changed event: payload={payload}, extracted_state={new_state}")
            self.client_state = new_state
            self.last_state_change = time.time()
            
            if old_state != new_state:
                LOGGER.info(f"WhatsApp client state changed: {old_state} -> {new_state}")
                
            # Update connection status based on client state
            if new_state == "READY":
                self.last_qr_code = None
                self.connection_status = "connected"
                self.is_ready = True
                await self._request_status_snapshot("state:READY")
                await self._flush_pending_voice_replies()
                self._schedule_pending_media_flush("state:READY")
            elif new_state == "CONNECTED":
                # Web.js emits CONNECTED before the session is fully ready.
                snapshot = self.last_status_snapshot if isinstance(self.last_status_snapshot, dict) else {}
                snapshot_ready = bool(snapshot.get("ready"))
                snapshot_phone = str(snapshot.get("phone_number") or "").strip()
                snapshot_service_status = str(snapshot.get("current_service_status") or "").strip().lower()
                if self.connection_status == "connected" or self.is_ready or snapshot_ready or (snapshot_service_status == "connected" and snapshot_phone):
                    self.connection_status = "connected"
                    self.is_ready = True
                else:
                    self.connection_status = "starting"
                    self.is_ready = False
            elif new_state in ["DISCONNECTED", "UNPAIRED", "UNPAIRED_IDLE"]:
                self.connection_status = "disconnected"
                self.is_ready = False
            elif new_state in ["OPENING", "PAIRING"]:
                self.connection_status = "connecting"
                self.is_ready = False
            elif new_state in ["CONFLICT", "TIMEOUT", "PROXYBLOCK"]:
                self.connection_status = "error"
                self.is_ready = False
            self._refresh_pairing_flags()
        elif event == "status_response":
            self.last_status_snapshot = payload if isinstance(payload, dict) else {"raw": payload}
            if isinstance(payload, dict):
                snapshot_phone = str(payload.get("phone_number") or "").strip() or None
                if snapshot_phone and not self.phone_number:
                    self.phone_number = snapshot_phone
                snapshot_self_chat_id = str(
                    payload.get("self_chat_id") or payload.get("selfChatId") or ""
                ).strip()
                if snapshot_self_chat_id and snapshot_self_chat_id.lower() != "status@broadcast":
                    self.last_self_chat_id = snapshot_self_chat_id
                snapshot_ready = bool(payload.get("ready"))
                snapshot_qr_available = bool(payload.get("qr_available"))
                if snapshot_ready or not snapshot_qr_available:
                    self.last_qr_code = None
                snapshot_service_status = str(payload.get("current_service_status") or "").strip().lower()
                snapshot_web_state = str(payload.get("web_state") or "").strip().upper()
                if snapshot_ready or (snapshot_service_status == "connected" and snapshot_phone):
                    self.connection_status = "connected"
                    self.is_ready = True
                    await self._flush_pending_voice_replies()
                    self._schedule_pending_media_flush("status_response:connected")
                elif snapshot_service_status in {"pairing", "starting", "authenticated", "auth_stalled", "recovering", "restarting", "connecting"}:
                    self.connection_status = snapshot_service_status
                    self.is_ready = False
                elif snapshot_web_state in {"PAIRING", "OPENING"}:
                    self.connection_status = "connecting"
                    self.is_ready = False
                LOGGER.info(
                    "WhatsApp raw status snapshot ready=%s service=%s web_state=%s last_state=%s phone=%s",
                    payload.get("ready"),
                    payload.get("current_service_status"),
                    payload.get("web_state"),
                    payload.get("last_state"),
                    _redact_whatsapp_id(payload.get("phone_number")),
                )
            self._refresh_pairing_flags()
                
        elif event == "loading_screen":
            # Handle loading screen state
            old_loading = self.loading_screen_active
            self.loading_screen_active = payload
            LOGGER.debug(f"Loading screen active: {payload}")
            
            # Clear loading screen immediately when it reaches 100%
            if isinstance(payload, dict) and payload.get("percent") == 100:
                self.loading_screen_active = False
                LOGGER.info("Loading screen cleared - reached 100%")
            
        elif event == "battery_changed":
            # Handle battery information updates
            self.battery_info = payload
            LOGGER.debug(f"Battery info updated: {payload}")
            
        elif event == "remote_session_saved":
            # Handle remote session save events
            self.remote_session_saved = True
            LOGGER.info("Remote session saved")
            
        elif event == "reconnection_scheduled":
            # Handle reconnection attempts
            self.reconnection_attempts = payload.get("attempt", 0)
            LOGGER.info(f"Reconnection scheduled, attempt: {self.reconnection_attempts}")
            
        elif event == "reconnection_failed":
            # Handle failed reconnection attempts
            LOGGER.warning(f"Reconnection failed after {payload.get('attempts', 0)} attempts")
            
        elif event == "client_restarting":
            # Handle client restart events
            self.client_state = "RESTARTING"
            self.connection_status = "restarting"
            LOGGER.info("WhatsApp client is restarting")
            
        elif event == "service_restart_request":
            # Handle service restart requests from client (for session recovery)
            LOGGER.info(f"Service restart requested by client: {payload.get('reason', 'unknown')}")
            # Schedule the restart in a separate task to avoid blocking WebSocket
            self._track_restart_request_task(self._handle_service_restart_request(payload))

        elif event == "command_result":
            if isinstance(payload, dict):
                command_id = str(payload.get("commandId") or payload.get("command_id") or "").strip()
                action = str(payload.get("action") or "").strip()
                future = self._pending_media_command_acks.pop(command_id, None) if command_id else None
                if future is not None and not future.done():
                    future.set_result(payload)
                success = bool(payload.get("success"))
                if action == "send_media" and not success:
                    LOGGER.warning(
                        "WhatsApp media command failed in Node.js command_id=%s error=%s",
                        command_id or "-",
                        payload.get("error") or "unknown",
                    )
                
        elif event == "message":
            await self._handle_incoming_message(payload)
            
        elif event == "phone_number":
            phone = str(payload or "").strip()
            self.phone_number = phone or None
            if self.phone_number:
                self.last_qr_code = None
            self._refresh_pairing_flags()
            LOGGER.info("WhatsApp phone number: %s", _redact_whatsapp_id(payload))
            
        else:
            LOGGER.debug(f"Unknown WebSocket event: {event}")
    
    async def _handle_service_restart_request(self, payload: Dict[str, Any]):
        """Handle service restart request from client (for session recovery)."""
        try:
            if self._stop_requested or self._shutdown_in_progress:
                LOGGER.info("Ignoring client restart request because WhatsApp shutdown is in progress")
                return
            reason = payload.get('reason', 'client_request')
            LOGGER.info(f"Processing service restart request: {reason}")
            
            # Add a small delay to allow the WebSocket message to be processed
            await asyncio.sleep(1)

            if self._stop_requested or self._shutdown_in_progress:
                LOGGER.info("Skipping delayed WhatsApp restart request because shutdown is in progress")
                return
            
            # Perform the service restart
            success = await self.restart()
            
            if success:
                LOGGER.info("Service restart completed successfully")
            else:
                LOGGER.error("Service restart failed")
                
        except Exception as e:
            LOGGER.error(f"Error handling service restart request: {e}")

    async def _handle_incoming_message(self, message_data: Dict[str, Any]):
        """Handle incoming WhatsApp messages."""
        try:
            if not self._is_verified_inbound_self_message(message_data):
                if message_data.get("fromMe", False) or message_data.get("selfChat", False):
                    LOGGER.warning(
                        "Rejected WhatsApp message that failed self-message verification (source=%s, from=%s, to=%s, chat=%s, fromMe=%s, selfChat=%s)",
                        message_data.get("sourceEvent") or "-",
                        _redact_whatsapp_id(message_data.get("from") or "-"),
                        _redact_whatsapp_id(message_data.get("to") or "-"),
                        _redact_whatsapp_id(message_data.get("remoteChatId") or "-"),
                        bool(message_data.get("fromMe", False)),
                        bool(message_data.get("selfChat", False)),
                    )
                return

            message_text = message_data.get("body", "").strip()
            media = message_data.get("media")
            has_media = bool(message_data.get("hasMedia")) or isinstance(media, dict)
            if not message_text and not has_media:
                return
            if message_text.startswith("/otp") or message_text.startswith("/autopair_answer"):
                # These are generated from Pairing Router, so ignore them.
                return
            if message_text and message_text.splitlines()[-1].endswith(
                (f"~ {self._configured_agent_label()}", f"~ {self.device_name}")
            ):
                return
            if message_text.startswith("/autopair"):
                LOGGER.info(
                    "WhatsApp /autopair received len=%d lf=%d cr=%d u2028=%d u2029=%d",
                    len(message_text),
                    message_text.count("\n"),
                    message_text.count("\r"),
                    message_text.count("\u2028"),
                    message_text.count("\u2029"),
                )

            self._remember_self_chat_id(message_data)
                
            # Store message in log
            self.message_log.append({
                "timestamp": time.time(),
                "message": message_text,
                "id": message_data.get("id"),
                "chat_name": message_data.get("chatName"),
                "contact_name": message_data.get("contactName"),
                "remote_chat_id": self.last_self_chat_id,
                "source_event": message_data.get("sourceEvent"),
            })
            
            LOGGER.info(
                "Processing WhatsApp message len=%d has_media=%s source=%s",
                len(message_text),
                has_media,
                message_data.get("sourceEvent") or "-",
            )
            # Check for special pairing commands via central router
            try:
                response = await pairing_router.process_message(
                    message_text=message_text,
                    platform="whatsapp",
                    sender_id=self.phone_number or "unknown",
                )
            except Exception as e:
                LOGGER.warning(f"Pairing router error (WhatsApp): {e}")
                response = None

            if response and response != PairingRouter.FRAGMENT_CONSUMED:
                # A real pairing reply (e.g. /autopair_answer or /otp): send it back.
                if message_text.startswith("/autopair") and not response.startswith("/autopair_answer"):
                    LOGGER.warning("WhatsApp /autopair rejected len=%d", len(response))
                response_tokens = str(response or "").split()
                response_kind = response_tokens[0] if response_tokens else "-"
                LOGGER.info("Sending WhatsApp pairing response kind=%s len=%d", response_kind, len(response))
                # Send pairing response back without signature to preserve exact protocol format
                await self._send_message_to_self(response, append_signature=False)
                return
            if response == PairingRouter.FRAGMENT_CONSUMED:
                # Fragment buffered - waiting for more pieces; stay silent.
                LOGGER.debug(
                    "WhatsApp autopair fragment consumed for sender=%s - "
                    "waiting for continuation.",
                    _redact_whatsapp_id(self.phone_number or "unknown"),
                )
                return

            # Build context with attachments if present
            attachments: List[Dict[str, Any]] = []
            def _looks_like_base64(s: str) -> bool:
                """Heuristic check for base64-like strings (non-data URL)."""
                try:
                    if not isinstance(s, str):
                        return False
                    raw = s.strip()
                    if len(raw) < 128:
                        return False
                    # Accept only base64 charset characters and padding/newlines
                    return bool(re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", raw))
                except Exception:
                    return False

            # Guard: suppress AI dispatch if this message looks like a raw autopair
            # payload fragment (orphaned from a split /autopair that timed out or was
            # retried). pairing_router.process_message already had a chance to consume
            # it via the fragment buffer; if we're here, there's no active buffer but
            # the blob is still useless (and harmful) to forward to the AI.
            if PairingRouter.looks_like_raw_autopair_fragment(message_text):
                LOGGER.warning(
                    "WhatsApp message looks like a raw autopair payload fragment "
                    "(no pending buffer, len=%d). Suppressing AI dispatch. "
                    "User should resend /autopair.",
                    len(message_text),
                )
                return

            try:
                if message_data.get("hasMedia") and isinstance(media, dict):
                    att: Dict[str, Any] = {
                        "filename": media.get("filename") or "attachment",
                        "mimetype": media.get("mimetype"),
                    }
                    # Prefer path when provided by Node client for large media
                    if media.get("path"):
                        att["path"] = media.get("path")
                    elif media.get("data"):
                        att["data"] = media.get("data")
                    # Size hint if available
                    if media.get("size"):
                        att["size_bytes"] = media.get("size")
                    # Propagate caption for downstream title/content extraction
                    if message_text:
                        att["caption"] = message_text

                    # Fallback: if media.data is missing but message body looks like base64, use it
                    if not att.get("data") and not att.get("path") and _looks_like_base64(message_text):
                        att["data"] = message_text
                        LOGGER.info("WhatsApp media missing; using base64 from message body as fallback")

                    attachments.append(att)
                else:
                    # No media object but message body may contain data URL or base64
                    if isinstance(message_text, str) and message_text.startswith("data:"):
                        item = {
                            "filename": "attachment",
                            "mimetype": None,
                            "url": message_text
                        }
                        # Propagate caption
                        item["caption"] = message_text
                        attachments.append(item)
                        LOGGER.info("WhatsApp message contained data URL; forwarding as attachment url")
                    elif _looks_like_base64(message_text):
                        item = {
                            "filename": "attachment",
                            "mimetype": None,
                            "data": message_text
                        }
                        # Propagate caption
                        item["caption"] = message_text
                        attachments.append(item)
                        LOGGER.info("WhatsApp message body looked like base64; forwarding as attachment data")
            except Exception as e:
                LOGGER.debug(f"Failed to extract media from WhatsApp message: {e}")

            if attachments:
                try:
                    data_present = sum(1 for a in attachments if bool(a.get("data")))
                    url_present = sum(1 for a in attachments if isinstance(a.get("url"), str))
                    LOGGER.info(
                        "WhatsApp attachments prepared: count=%s, data=%s, url=%s",
                        len(attachments), data_present, url_present
                    )
                except Exception:
                    pass

            context_items = [{"attachments": attachments, "source": "whatsapp"}] if attachments else []

            # Forward to chat API for normal processing
            await self._forward_to_chat_api(
                message_text,
                context=context_items,
                reply_chat_id=self.last_self_chat_id,
            )
            
        except Exception as e:
            LOGGER.error(f"Error handling incoming message: {e}")
    
    async def _forward_to_chat_api(
        self,
        message_text: str,
        context: Optional[List[Dict[str, Any]]] = None,
        *,
        reply_chat_id: Optional[str] = None,
    ):
        """Queue WhatsApp message for background AI processing and late delivery."""
        execution_manager = get_session_execution_manager()
        identity = execution_manager.bind_transport_owner(
            "whatsapp",
            self.phone_number or "unknown",
            raw_session_id=self.phone_number or "unknown",
        )
        start_new_thread, normalized_message_text, control_only = _extract_conversation_request(message_text)
        if start_new_thread:
            identity = _resolve_conversation_identity(identity, start_new_thread=True)
        else:
            identity = _resolve_conversation_identity(identity)
        session_key = identity.canonical_session_id

        if start_new_thread and control_only:
            await self._send_message_to_self("Started a new conversation.")
            return

        async def _run_chat_request() -> None:
            from rest_api import ChatRequest, process_chat_message

            progress_state = {"last_sent_at": time.monotonic()}
            execution_state = {"queue_position": 0}
            typing_stop_event = asyncio.Event()
            typing_task = None
            chat_id = None

            async def _on_execution_status(status) -> None:
                execution_state["queue_position"] = int(getattr(status, "queue_position", 0) or 0)

            async def _on_chunk(chunk: Dict[str, Any]) -> None:
                if not chunk.get("_autoyou_progress"):
                    return
                now = time.monotonic()
                if now - progress_state["last_sent_at"] < 60.0:
                    return
                progress_state["last_sent_at"] = now
                progress_text = str(chunk.get("progress_text") or "Still working...")
                await self._send_message_to_self(progress_text)

            async def _send_immediate_media_reply(attachments: List[Dict[str, Any]]) -> bool:
                return await self._send_media_attachments_to_self(attachments)

            async def _execute_chat_request():
                request_metadata = {
                    "client": "whatsapp",
                    **build_autoyou_conversation_metadata(identity, reset=start_new_thread),
                    "session_execution": {
                        "queue_position": execution_state["queue_position"],
                    },
                }
                if self.phone_number:
                    request_metadata["reply_target"] = {
                        "transport": "whatsapp",
                        "to": self.phone_number,
                    }
                chat_req = ChatRequest(
                    message=normalized_message_text,
                    session_id=identity.canonical_session_id,
                    user_id=identity.canonical_user_id,
                    context=context or [],
                    metadata=request_metadata,
                )
                return await process_chat_message(
                    chat_req,
                    ai_agent_url=self._get_ai_agent_base_url(),
                    on_chunk=_on_chunk,
                    on_media_reply=_send_immediate_media_reply,
                )

            try:
                # Start typing heartbeat for the current chat
                chat_id = self._resolve_typing_chat_id(reply_chat_id)
                if chat_id:
                    await self.send_typing(chat_id)
                
                heartbeat_interval = 5.0
                try:
                    import server
                    heartbeat_interval = getattr(server, "WHATSAPP_TYPING_HEARTBEAT_SECONDS", 5.0)
                except ImportError:
                    pass

                async def _typing_heartbeat_loop():
                    while not typing_stop_event.is_set():
                        try:
                            await asyncio.wait_for(typing_stop_event.wait(), timeout=heartbeat_interval)
                        except asyncio.TimeoutError:
                            if not typing_stop_event.is_set() and chat_id:
                                await self.send_typing(chat_id)
                        except Exception:
                            break

                typing_task = asyncio.create_task(_typing_heartbeat_loop())

                chat_resp = await execution_manager.submit_turn(
                    identity,
                    _execute_chat_request,
                    on_status=_on_execution_status,
                    label="whatsapp-chat",
                )
                ai_message = (chat_resp.response or "").strip()
                media_reply_attachments = list(getattr(chat_resp, "media_reply_attachments", []) or [])
                if media_reply_attachments:
                    await self._send_media_attachments_to_self(media_reply_attachments)
                voice_reply_audio_path = getattr(chat_resp, "voice_reply_audio_path", None)
                if voice_reply_audio_path:
                    audio_sent = await self._send_audio_to_self(voice_reply_audio_path)
                    try:
                        from shared.voice_messaging import cleanup_paths as _vm_cleanup

                        _vm_cleanup(voice_reply_audio_path)
                    except Exception:
                        pass
                    if not audio_sent and ai_message:
                        await self._send_message_to_self(ai_message)
                elif ai_message:
                    await self._send_message_to_self(ai_message)
                else:
                    LOGGER.warning("Empty response from chat API")
            except SessionQueueFullError as e:
                await self._send_message_to_self(e.status.message)
            except SessionTurnTimeoutError as e:
                await self._send_message_to_self(e.status.message)
            except Exception as e:
                LOGGER.error("Error forwarding to chat API: %s", e)
                await self._send_message_to_self("Sorry, something went wrong while processing your request.")
            finally:
                if typing_task:
                    typing_stop_event.set()
                    typing_task.cancel()
                    try:
                        await typing_task
                    except asyncio.CancelledError:
                        pass
                # Stop typing explicitly
                if chat_id:
                    await self.stop_typing(chat_id)

        self._track_chat_task(_run_chat_request(), session_key=session_key, label="whatsapp-chat")
    
    async def _send_audio_to_self(self, file_path: str, *, as_voice: bool = True) -> bool:
        """Send a synthesized audio reply back to the active WhatsApp chat."""
        try:
            command_data = self._build_voice_reply_command_data(file_path, as_voice=as_voice)
            if not command_data:
                return False
            if not self._is_websocket_connected():
                LOGGER.warning("WhatsApp audio reply queued because WebSocket is not connected")
                self._queue_pending_voice_reply(command_data)
                self._schedule_control_channel_recovery("send_audio_without_websocket")
                return True
            if not await self._send_audio_command_data(command_data):
                self._queue_pending_voice_reply(command_data)
                return True
            LOGGER.info("Sent synthesized voice reply to WhatsApp")
            return True
        except Exception as e:
            LOGGER.error("Error sending WhatsApp audio reply: %s", e)
            self._schedule_control_channel_recovery("send_audio_error")
            return False

    def _voice_reply_mimetype_for_path(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in {".ogg", ".oga", ".opus"}:
            return "audio/ogg; codecs=opus"
        if ext == ".wav":
            return "audio/wav"
        if ext in {".m4a", ".aac", ".mp4"}:
            return "audio/mp4"
        if ext == ".mp3":
            return "audio/mpeg"
        return "application/octet-stream"

    def _build_voice_reply_command_data(self, file_path: str, *, as_voice: bool = True) -> Optional[Dict[str, Any]]:
        if not self.phone_number:
            LOGGER.error("Cannot send or queue WhatsApp audio: phone number unknown")
            return None
        try:
            with open(file_path, "rb") as handle:
                audio_b64 = base64.b64encode(handle.read()).decode("ascii")
        except OSError as exc:
            LOGGER.error("Cannot read WhatsApp audio file %s: %s", file_path, exc)
            return None
        command_data: Dict[str, Any] = {
            "to": self.phone_number,
            "data": audio_b64,
            "mimetype": self._voice_reply_mimetype_for_path(file_path),
            "asVoice": bool(as_voice),
            "caption": f"~ {self._configured_agent_label()}",
        }
        resolved_chat_id = self._resolve_typing_chat_id()
        if resolved_chat_id:
            command_data["chatId"] = resolved_chat_id
        return command_data

    def _store_queued_media_command_data(self, command_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(command_data, dict):
            return None
        stored = dict(command_data)
        if stored.get("media_path") or stored.get("data_path"):
            stored.pop("data", None)
            return stored
        encoded = str(stored.get("data") or "").strip()
        if not encoded:
            return None
        try:
            media_bytes = base64.b64decode(encoded, validate=False)
        except Exception as exc:
            LOGGER.warning("Cannot queue WhatsApp media: invalid base64 payload: %s", exc)
            return None
        payload_info = store_media_payload(
            self.state_dir,
            media_bytes,
            filename=stored.get("filename") or "whatsapp-media",
            mimetype=str(stored.get("mimetype") or "application/octet-stream"),
            prefix="whatsapp-media",
        )
        if not payload_info:
            return None
        stored.pop("data", None)
        stored.update(payload_info)
        return stored

    def _hydrate_media_command_data(self, command_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(command_data, dict):
            return None
        hydrated = dict(command_data)
        if hydrated.get("data"):
            return hydrated
        media_bytes = read_media_payload(hydrated)
        if media_bytes is None:
            LOGGER.warning(
                "Cannot send queued WhatsApp media %s: queued payload file is unavailable",
                hydrated.get("filename") or "attachment",
            )
            return None
        hydrated["data"] = base64.b64encode(media_bytes).decode("ascii")
        return hydrated

    def _build_media_reply_command_data(
        self,
        attachment: Dict[str, Any],
        *,
        to: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        recipient = str(to or self.phone_number or "").strip()
        if not recipient:
            LOGGER.error("Cannot send or queue WhatsApp media: recipient unknown")
            return None
        try:
            from shared.media_messaging import attachment_mimetype, load_attachment_bytes
            from shared.openclaw_gateway import safe_filename

            media_bytes, error = load_attachment_bytes(attachment)
            if media_bytes is None:
                LOGGER.warning("Cannot send WhatsApp media attachment: %s", error)
                return None
            mimetype = attachment_mimetype(attachment)
            filename = safe_filename(attachment.get("filename") or attachment.get("path"), mimetype)
        except Exception as exc:
            LOGGER.error("Cannot prepare WhatsApp media attachment: %s", exc)
            return None
        caption = str(attachment.get("caption") or "").strip()
        is_self_target = (
            not to
            or _normalize_whatsapp_address(recipient) == _normalize_whatsapp_address(self.phone_number)
        )
        if is_self_target:
            signature = f"~ {self._configured_agent_label()}"
            if not caption.rstrip().endswith(signature):
                caption = f"{caption}\n{signature}".strip() if caption else signature
        command_data: Dict[str, Any] = {
            "to": recipient,
            "data": base64.b64encode(media_bytes).decode("ascii"),
            "mimetype": mimetype,
            "filename": filename,
            "caption": caption,
            "hd": True,
        }
        if (
            not to
            or _normalize_whatsapp_address(recipient) == _normalize_whatsapp_address(self.phone_number)
        ):
            resolved_chat_id = self._resolve_typing_chat_id()
            if resolved_chat_id:
                command_data["chatId"] = resolved_chat_id
        return command_data

    async def _send_media_attachments_to_self(self, attachments: List[Dict[str, Any]]) -> bool:
        return await self._send_media_attachments(attachments, to=self.phone_number)

    async def send_media_attachments(self, to: str, attachments: List[Dict[str, Any]]) -> bool:
        """Public admin/scheduler send wrapper for image/video attachments."""
        return await self._send_media_attachments(attachments, to=to)

    async def _send_media_attachments(
        self,
        attachments: List[Dict[str, Any]],
        *,
        to: Optional[str] = None,
    ) -> bool:
        sent_or_queued = False
        for attachment in attachments or []:
            command_data = self._build_media_reply_command_data(attachment, to=to)
            if not command_data:
                continue
            if not self._is_websocket_connected():
                LOGGER.warning("WhatsApp media reply queued because WebSocket is not connected")
                self._queue_pending_media_reply(command_data)
                self._schedule_control_channel_recovery("send_media_without_websocket")
                sent_or_queued = True
                continue
            if await self._send_media_command_data(command_data):
                sent_or_queued = True
            else:
                self._queue_pending_media_reply(command_data)
                sent_or_queued = True
        return sent_or_queued

    async def _send_audio_command_data(self, command_data: Dict[str, Any]) -> bool:
        if not self._is_websocket_connected():
            return False
        payload = dict(command_data)
        if not payload.get("chatId"):
            resolved_chat_id = self._resolve_typing_chat_id()
            if resolved_chat_id:
                payload["chatId"] = resolved_chat_id
        try:
            await self.websocket.send(json.dumps({"action": "send_audio", "data": payload}))
            return True
        except Exception as exc:
            LOGGER.error("Error sending WhatsApp queued audio reply: %s", exc)
            self._schedule_control_channel_recovery("send_audio_error")
            return False

    async def _send_media_command_data(self, command_data: Dict[str, Any]) -> bool:
        if not self._is_websocket_connected():
            return False
        payload = self._hydrate_media_command_data(command_data)
        if not payload:
            return False
        if not payload.get("chatId"):
            target = str(payload.get("to") or "").strip()
            if (
                not target
                or _normalize_whatsapp_address(target) == _normalize_whatsapp_address(self.phone_number)
            ):
                resolved_chat_id = self._resolve_typing_chat_id()
                if resolved_chat_id:
                    payload["chatId"] = resolved_chat_id
        command_id = str(payload.get("commandId") or uuid.uuid4()).strip()
        payload["commandId"] = command_id
        loop = asyncio.get_running_loop()
        ack_future = loop.create_future()
        self._pending_media_command_acks[command_id] = ack_future
        try:
            await self.websocket.send(json.dumps({"action": "send_media", "data": payload}))
            timeout = float(os.environ.get("WHATSAPP_MEDIA_SEND_ACK_TIMEOUT_SECONDS", "120"))
            result = await asyncio.wait_for(ack_future, timeout=max(1.0, timeout))
            return bool(isinstance(result, dict) and result.get("success"))
        except asyncio.TimeoutError:
            LOGGER.error("Timed out waiting for WhatsApp media send acknowledgement command_id=%s", command_id)
            return False
        except Exception as exc:
            LOGGER.error("Error sending WhatsApp queued media reply: %s", exc)
            self._schedule_control_channel_recovery("send_media_error")
            return False
        finally:
            self._pending_media_command_acks.pop(command_id, None)

    def _queue_pending_voice_reply(self, command_data: Dict[str, Any]) -> None:
        if not isinstance(command_data, dict) or not command_data.get("data") or not command_data.get("to"):
            return
        pending = self._pending_voice_replies
        pending.append({"queued_at": time.time(), "command_data": dict(command_data)})
        if len(pending) > self._pending_voice_reply_limit:
            del pending[: len(pending) - self._pending_voice_reply_limit]
        self._persist_pending_voice_replies()
        LOGGER.info("Queued WhatsApp voice reply for later delivery (%d pending)", len(pending))

    def _queue_pending_media_reply(self, command_data: Dict[str, Any]) -> None:
        if not isinstance(command_data, dict) or not command_data.get("to"):
            return
        stored_command = self._store_queued_media_command_data(command_data)
        if not stored_command:
            return
        pending = self._pending_media_replies
        pending.append({
            "queued_at": time.time(),
            "command_data": stored_command,
            "media_path": stored_command.get("media_path"),
            "media_size_bytes": stored_command.get("media_size_bytes"),
        })
        self._pending_media_replies = prune_and_trim_media_entries(
            pending,
            limit_count=self._pending_media_reply_limit,
        )
        self._persist_pending_media_replies()
        LOGGER.info("Queued WhatsApp media reply for later delivery (%d pending)", len(self._pending_media_replies))

    async def _flush_pending_voice_replies(self) -> None:
        if not self._pending_voice_replies or not self._is_websocket_connected():
            return
        remaining: List[Dict[str, Any]] = []
        delivered = 0
        for entry in list(self._pending_voice_replies):
            command_data = dict(entry.get("command_data") or {})
            if command_data and await self._send_audio_command_data(command_data):
                delivered += 1
            else:
                remaining.append(entry)
                break
        if delivered:
            LOGGER.info("Delivered %d queued WhatsApp voice repl%s", delivered, "y" if delivered == 1 else "ies")
        self._pending_voice_replies = remaining + self._pending_voice_replies[delivered + len(remaining):]
        self._persist_pending_voice_replies()

    async def _flush_pending_media_replies(self) -> None:
        if not self._pending_media_replies or not self._is_websocket_connected():
            return
        remaining: List[Dict[str, Any]] = []
        delivered = 0
        for entry in list(self._pending_media_replies):
            command_data = dict(entry.get("command_data") or {})
            if command_data and await self._send_media_command_data(command_data):
                delete_media_payload(command_data)
                delivered += 1
            else:
                remaining.append(entry)
                break
        if delivered:
            LOGGER.info("Delivered %d queued WhatsApp media repl%s", delivered, "y" if delivered == 1 else "ies")
        self._pending_media_replies = remaining + self._pending_media_replies[delivered + len(remaining):]
        self._persist_pending_media_replies()

    def _load_pending_voice_replies(self) -> List[Dict[str, Any]]:
        try:
            path = self._pending_voice_reply_file
            if not path.exists():
                return []
            raw = load_secure_json(path, default=[])
            if not isinstance(raw, list):
                return []
            items = [item for item in raw if isinstance(item, dict)]
            return items[-self._pending_voice_reply_limit:]
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.warning("Failed to load pending WhatsApp voice replies: %s", exc)
            return []

    def _persist_pending_voice_replies(self) -> None:
        try:
            path = self._pending_voice_reply_file
            if not self._pending_voice_replies:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            save_secure_json(path, self._pending_voice_replies[-self._pending_voice_reply_limit:])
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.warning("Failed to persist pending WhatsApp voice replies: %s", exc)

    def _load_pending_media_replies(self) -> List[Dict[str, Any]]:
        try:
            path = self._pending_media_reply_file
            if not path.exists():
                return []
            raw = load_secure_json(path, default=[])
            if not isinstance(raw, list):
                return []
            items: List[Dict[str, Any]] = []
            changed = False
            for item in raw:
                if not isinstance(item, dict):
                    continue
                command_data = dict(item.get("command_data") or {})
                if command_data.get("data"):
                    stored = self._store_queued_media_command_data(command_data)
                    if not stored:
                        changed = True
                        continue
                    command_data = stored
                    changed = True
                item_copy = dict(item)
                item_copy["command_data"] = command_data
                item_copy["media_path"] = command_data.get("media_path") or item_copy.get("media_path")
                item_copy["media_size_bytes"] = command_data.get("media_size_bytes") or item_copy.get("media_size_bytes")
                items.append(item_copy)
            items = prune_and_trim_media_entries(
                items,
                limit_count=self._pending_media_reply_limit,
            )
            if changed:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    save_secure_json(path, items[-self._pending_media_reply_limit:])
                except SecureStorageError:
                    raise
                except Exception as persist_exc:
                    LOGGER.warning("Failed to rewrite pending WhatsApp media queue after migration: %s", persist_exc)
            return items[-self._pending_media_reply_limit:]
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.warning("Failed to load pending WhatsApp media replies: %s", exc)
            return []

    def _persist_pending_media_replies(self) -> None:
        try:
            path = self._pending_media_reply_file
            if not self._pending_media_replies:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            pruned = prune_and_trim_media_entries(
                self._pending_media_replies,
                limit_count=self._pending_media_reply_limit,
            )
            self._pending_media_replies = pruned
            save_secure_json(path, pruned[-self._pending_media_reply_limit:])
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.warning("Failed to persist pending WhatsApp media replies: %s", exc)


    async def _send_message_to_self(self, message: str, append_signature: bool = True):
        """Send a message to the user's own WhatsApp number.

        Args:
            message: Message text to send.
            append_signature: Whether to append the server signature line.
        """
        try:
            if not self._is_websocket_connected() or not self.phone_number:
                LOGGER.error("Cannot send message: WebSocket not connected or phone number unknown")
                if not self._is_websocket_connected():
                    self._schedule_control_channel_recovery("send_to_self_without_websocket")
                return
            # Optionally append signature to mark server-sent messages
            if append_signature:
                message += f"\n~ {self._configured_agent_label()}"
            # Send message command to Node.js client
            command_data = {
                "to": self.phone_number,
                "message": message,
            }
            resolved_chat_id = self._resolve_typing_chat_id()
            if resolved_chat_id:
                command_data["chatId"] = resolved_chat_id
            command = {
                "action": "send_message",
                "data": command_data
            }
            
            await self.websocket.send(json.dumps(command))
            LOGGER.info("Sent response to WhatsApp len=%d", len(message))
            
        except Exception as e:
            LOGGER.error(f"Error sending message to WhatsApp: {e}")
            self._schedule_control_channel_recovery("send_to_self_error")

    async def update_server_name(self, server_name: str) -> bool:
        """Update the live Node reply label without changing the paired device identity."""
        label = str(server_name or "").strip()
        if not label or not self._is_websocket_connected():
            return False
        try:
            await self.websocket.send(
                json.dumps({"action": "set_server_name", "data": {"serverName": label}})
            )
            return True
        except Exception as exc:
            LOGGER.debug("Could not update live WhatsApp server name: %s", exc)
            return False
    
    async def get_qr_code_data(self) -> Optional[str]:
        """Get QR code data as base64 encoded PNG."""
        try:
            if not self.last_qr_code:
                return None
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(self.last_qr_code)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            img_data = buffer.getvalue()
            return base64.b64encode(img_data).decode()
        except Exception as e:
            LOGGER.error(f"Error generating QR code: {e}")
            return None

    async def get_qr_code_data_url(self) -> Optional[str]:
        """Get QR code as a data URL. PNG preferred, SVG fallback when PIL missing."""
        try:
            if not self.last_qr_code:
                return None
            try:
                png_b64 = await self.get_qr_code_data()
                if png_b64:
                    return f"data:image/png;base64,{png_b64}"
            except Exception:
                pass
            try:
                from qrcode.image.svg import SvgImage
                img = qrcode.make(self.last_qr_code, image_factory=SvgImage)
                buffer = BytesIO()
                img.save(buffer)
                svg_b64 = base64.b64encode(buffer.getvalue()).decode()
                return f"data:image/svg+xml;base64,{svg_b64}"
            except Exception as e:
                LOGGER.error(f"SVG QR fallback failed: {e}")
                return None
        except Exception as e:
            LOGGER.error(f"Error preparing QR data URL: {e}")
            return None
    
    async def _clear_loading_screen_delayed(self):
        """Clear loading screen after a short delay when client is ready."""
        await asyncio.sleep(2)  # Wait 2 seconds
        if self.connection_status == "connected" and self.is_ready:
            self.loading_screen_active = False
            LOGGER.debug("Loading screen cleared after client ready")

    async def get_status(self) -> Dict[str, Any]:
        """Get current WhatsApp service status with comprehensive client state information."""
        websocket_connected = self._is_websocket_connected()
        
        # Calculate connection health - simple logic
        connection_healthy = (websocket_connected and 
                            self.connection_status == "connected" and 
                            self.is_ready and
                            bool(self.phone_number))
        
        return {
            "status": self.connection_status,
            "ready": self.is_ready,
            "paired": self.is_paired,
            "phone_number": self.phone_number,
            "node_process_running": self.node_process is not None and self.node_process.returncode is None,
            "websocket_connected": websocket_connected,
            "last_qr_available": self.last_qr_code is not None,
            
            # Enhanced client state information
            "client_state": self.client_state,
            "battery_info": self.battery_info,
            "reconnection_attempts": self.reconnection_attempts,
            "last_state_change": self.last_state_change,
            "loading_screen_active": self.loading_screen_active,
            "remote_session_saved": self.remote_session_saved,
            "connection_healthy": connection_healthy,
            "websocket_reconnect_attempts": self.websocket_reconnect_attempts,
            "last_heartbeat": self.last_heartbeat,
            "node_status_snapshot": self.last_status_snapshot,
            "pending_voice_replies": len(self._pending_voice_replies),
            "pending_media_replies": len(self._pending_media_replies),
        }

    async def send_typing(self, chat_id: str) -> bool:
        """Send a 'start typing' indicator signal to a WhatsApp chat."""
        if not self._is_websocket_connected():
            return False
        try:
            await self.websocket.send(json.dumps({
                "action": "start_typing",
                "data": {"chatId": chat_id}
            }))
            LOGGER.debug("Requested WhatsApp typing indicator for chat: %s", _redact_whatsapp_id(chat_id))
            return True
        except Exception as e:
            LOGGER.warning(f"Failed to send WhatsApp typing indicator: {e}")
            return False

    async def stop_typing(self, chat_id: str) -> bool:
        """Send a 'stop typing' indicator signal to a WhatsApp chat."""
        if not self._is_websocket_connected():
            return False
        try:
            await self.websocket.send(json.dumps({
                "action": "stop_typing",
                "data": {"chatId": chat_id}
            }))
            LOGGER.debug("Requested stop WhatsApp typing indicator for chat: %s", _redact_whatsapp_id(chat_id))
            return True
        except Exception as e:
            LOGGER.warning(f"Failed to stop WhatsApp typing indicator: {e}")
            return False

    async def wait_for_qr_code(self, timeout_seconds: float = 12.0) -> Optional[str]:
        """Wait briefly for the Node client to emit a QR code during startup."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self.last_qr_code:
                return self.last_qr_code
            if self.node_process is not None and self.node_process.returncode is not None:
                return None
            await asyncio.sleep(0.4)
        return self.last_qr_code
    
    async def cleanup_session(self) -> bool:
        """Clean up WhatsApp session data."""
        try:
            # Stop service first
            await self.stop()
            
            # Clear session data
            self.last_qr_code = None
            self.phone_number = None
            self.last_self_chat_id = None
            self.is_paired = False
            self.message_log.clear()
            self.client_state = "UNLAUNCHED"
            self.battery_info = None
            self.reconnection_attempts = 0
            self.last_state_change = None
            self.loading_screen_active = False
            self.remote_session_saved = False
            self.last_heartbeat = time.time()
            self.websocket_reconnect_attempts = 0
            
            failures = []

            auth_dir = str(self.state_dir / ".wwebjs_auth")
            cache_dir = str(self.state_dir / ".wwebjs_cache")

            from shared.native_webkit import browser_executable, delete_whatsapp_profiles
            native_browser = browser_executable()
            if native_browser:
                # Keep the profile marker if WebKit deletion fails, so an
                # explicit reset can retry without orphaning saved credentials.
                await delete_whatsapp_profiles(Path(auth_dir), native_browser)

            if await self._remove_tree_with_retries(auth_dir, ".wwebjs_auth"):
                LOGGER.info("Removed WhatsApp auth session directory (.wwebjs_auth)")
            else:
                failures.append(auth_dir)
                LOGGER.error("Failed to remove WhatsApp auth session directory (.wwebjs_auth)")

            if await self._remove_tree_with_retries(cache_dir, ".wwebjs_cache"):
                LOGGER.info("Removed WhatsApp web cache directory (.wwebjs_cache)")
            else:
                failures.append(cache_dir)
                LOGGER.error("Failed to remove WhatsApp web cache directory (.wwebjs_cache)")

            if failures:
                LOGGER.error("WhatsApp cleanup incomplete; remaining paths: %s", failures)
                return False
            
            return True
            
        except Exception as e:
            LOGGER.error(f"Error cleaning up WhatsApp session: {e}")
            return False
    
    async def send_message(self, to: str, message: str) -> bool:
        """Send a message to a specific WhatsApp number."""
        try:
            if not self._is_websocket_connected():
                LOGGER.error("Cannot send message: WebSocket not connected")
                self._schedule_control_channel_recovery("send_message_without_websocket")
                return False
                
            command = {
                "action": "send_message",
                "data": {
                    "to": to,
                    "message": message
                }
            }
            
            await self.websocket.send(json.dumps(command))
            LOGGER.info(
                "[WhatsApp Service] Sent message to %s len=%d",
                _redact_whatsapp_id(to),
                len(str(message or "")),
            )
            return True
            
        except Exception as e:
            LOGGER.error(f"Error sending message: {e}")
            self._schedule_control_channel_recovery("send_message_error")
            return False

    async def send_typing(self, chat_id: str) -> bool:
        """Send a typing indicator to a specific WhatsApp chat."""
        try:
            resolved_chat_id = self._resolve_typing_chat_id(chat_id)
            if not resolved_chat_id:
                LOGGER.debug("Skipping WhatsApp typing indicator because no chat target is available yet")
                return False
            if self._shutdown_in_progress or self._stop_requested:
                return False
            if not self._is_websocket_connected():
                LOGGER.debug("Cannot send WhatsApp typing indicator: WebSocket not connected")
                self._schedule_control_channel_recovery("send_typing_without_websocket")
                return False
                
            command = {
                "action": "start_typing",
                "data": {
                    "chatId": resolved_chat_id
                }
            }
            
            await self.websocket.send(json.dumps(command))
            LOGGER.debug("Sent start_typing command to WhatsApp client for %s", _redact_whatsapp_id(resolved_chat_id))
            return True
            
        except Exception as e:
            LOGGER.error(f"Error sending typing indicator: {e}")
            self._schedule_control_channel_recovery("send_typing_error")
            return False

    async def stop_typing(self, chat_id: str) -> bool:
        """Stop the typing indicator for a specific WhatsApp chat."""
        try:
            resolved_chat_id = self._resolve_typing_chat_id(chat_id)
            if not resolved_chat_id:
                return False
            if self._shutdown_in_progress or self._stop_requested:
                return False
            if not self._is_websocket_connected():
                LOGGER.debug("Cannot stop WhatsApp typing indicator: WebSocket not connected")
                self._schedule_control_channel_recovery("stop_typing_without_websocket")
                return False
                
            command = {
                "action": "stop_typing",
                "data": {
                    "chatId": resolved_chat_id
                }
            }
            
            await self.websocket.send(json.dumps(command))
            LOGGER.debug("Sent stop_typing command to WhatsApp client for %s", _redact_whatsapp_id(resolved_chat_id))
            return True
            
        except Exception as e:
            LOGGER.error(f"Error stopping typing indicator: {e}")
            self._schedule_control_channel_recovery("stop_typing_error")
            return False
    
    def get_message_log(self) -> List[Dict[str, Any]]:
        """Get the message log."""
        return self.message_log.copy()

# WhatsAppNodeService is used in all modes (dev and compiled).
# The node/whatsapp directory with whatsapp_client.js and node_modules is bundled
# into the Nuitka standalone output; get_node_service_dir() resolves correctly.
WhatsAppService = WhatsAppNodeService
