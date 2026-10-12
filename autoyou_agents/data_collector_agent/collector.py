# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-dda98a056557ec51346fb1ed

"""Local-first collection and training-export engine for Data Collector Agent.

It deliberately stores only data that the operator asks it to collect, never
reads credential files, and keeps all output below this agent's runtime root.
The module has no dependency on ``server.py`` so it can run as a standalone
loopback service.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import copy
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from subprocess import PIPE, run
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from shared.platform_runtime import get_service_data_dir
from shared.secure_storage import (
    SecureStorageError,
    enable_secure_storage_from_environment,
    read_secure_file,
    secure_storage_enabled,
    write_secure_file,
)

from .messaging import (
    collect_telegram_saved_messages,
    collect_whatsapp_history,
    messaging_capabilities,
    normalize_telegram_options,
    normalize_whatsapp_options,
)

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-dda98a056557ec51346fb1ed"


AGENT_NAME = "data_collector_agent"
DATA_DIR = get_service_data_dir(AGENT_NAME, anchor=__file__)
JOB_PATH = DATA_DIR / "collection-job.json"
OUTPUT_ROOT = DATA_DIR / "collected_context"
VERSION = 2
LOCAL_SOURCE_APPS = ("codex", "claude", "chatgpt")
SUPPORTED_APPS = (*LOCAL_SOURCE_APPS, "whatsapp", "telegram_saved_messages")
SUPPORTED_MODES = ("input_output", "input_only", "output_only")
SUPPORTED_DIRECTIONS = ("both", "sent", "received")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_CHATGPT_LINE_RE = re.compile(r"^\s*\d+\s+(?:text|heading)\s+(.*)$")
_SKIP_SOURCE_PARTS = {".credentials.json", "credentials.json", "auth.json", "token.json"}
_SYSTEM_PROMPT = "Continue the conversation naturally and safely using the supplied examples."


class CollectionCancelled(RuntimeError):
    """Stop a local collection at a safe source or record boundary."""


class CollectionRunBusy(RuntimeError):
    """Another process already owns the one local collection slot."""


def _cancel_requested(cancel_check: Optional[Callable[[], bool]]) -> bool:
    try:
        return bool(cancel_check and cancel_check())
    except Exception:
        return False


def _cancelled_report(report: Dict[str, Any]) -> Dict[str, Any]:
    report["status"] = "cancelled"
    report["finished_at"] = utc_now()
    return report


def _run_control_path() -> Path:
    """Keep the non-sensitive cross-process collection control beside its job."""
    return DATA_DIR / ".collection-run-control.json"


def _read_run_control() -> Dict[str, Any]:
    try:
        value = json.loads(_run_control_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _run_owner_is_alive(control: Dict[str, Any]) -> bool:
    """Treat a legacy control as active; new controls also have a live PID."""
    if control.get("state") not in {"running", "cancelling"} or not control.get("run_id"):
        return False
    try:
        owner_pid = int(control.get("owner_pid"))
    except (TypeError, ValueError):
        return True
    if owner_pid <= 0:
        return False
    if os.name == "nt":
        # Windows treats os.kill(pid, 0) as a real signal. Query the process
        # handle instead so a halt check cannot terminate its own collector.
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(0x1000, False, owner_pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied means it is live but not inspectable.
        try:
            exit_code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) and exit_code.value == 259)
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(owner_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def collection_run_status() -> Dict[str, Any]:
    """Return safe status for a run that may belong to another AutoYou process."""
    control = _read_run_control()
    if not _run_owner_is_alive(control):
        return {"state": "idle"}
    return {
        "state": str(control["state"]),
        "started_at": control.get("started_at"),
        "origin": "agent",
    }


def begin_collection_run() -> str:
    # ponytail: one local slot keeps shared exports consistent; use per-workspace
    # controls only if parallel collection becomes a supported product workflow.
    run_id = uuid.uuid4().hex
    control = {
        "run_id": run_id,
        "state": "running",
        "owner_pid": os.getpid(),
        "started_at": utc_now(),
    }
    control_path = _run_control_path()
    control_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            with control_path.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(control, handle)
            return run_id
        except FileExistsError:
            existing = _read_run_control()
            if _run_owner_is_alive(existing):
                raise CollectionRunBusy("A collection operation is already running in another AutoYou process.")
            try:
                control_path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise CollectionRunBusy("The previous collection control could not be cleared safely.") from exc
    raise CollectionRunBusy("A collection operation is already starting in another AutoYou process.")


def collection_cancellation_requested(run_id: str) -> bool:
    control = _read_run_control()
    return str(control.get("run_id") or "") == str(run_id) and control.get("state") == "cancelling"


def request_collection_cancellation() -> Dict[str, Any]:
    control = _read_run_control()
    run_id = str(control.get("run_id") or "")
    if not run_id:
        return {"status": "error", "message": "No collection operation is running."}
    try:
        with _atomic_text(_run_control_path()) as handle:
            json.dump({**control, "state": "cancelling", "cancel_requested_at": utc_now()}, handle)
    except OSError:
        return {"status": "error", "message": "Could not record the collection cancellation request."}
    return {
        "status": "cancelling",
        "message": "Cancellation requested; the active collection stops at its next safe boundary.",
    }


def finish_collection_run(run_id: str) -> None:
    control = _read_run_control()
    if str(control.get("run_id") or "") != str(run_id):
        return
    try:
        _run_control_path().unlink(missing_ok=True)
    except OSError:
        pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: object, fallback: str = "item") -> str:
    normalized = _SAFE_NAME_RE.sub("-", str(value or "").strip()).strip(".-")
    return normalized[:120] or fallback


@contextmanager
def _atomic_text(path: Path) -> Iterator[Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            yield handle
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _attach_secure_storage() -> None:
    """Join the server's protected-storage boundary when this is a worker."""
    enable_secure_storage_from_environment()


def read_json(path: Path, default: Any = None, *, migrate_plaintext: bool = True) -> Any:
    try:
        _attach_secure_storage()
        return json.loads(read_secure_file(path, migrate_plaintext=migrate_plaintext).decode("utf-8"))
    except SecureStorageError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        return copy.deepcopy(default)


def write_json(path: Path, value: Any) -> None:
    _attach_secure_storage()
    write_secure_file(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    _attach_secure_storage()
    count = 0
    if secure_storage_enabled():
        payload = io.StringIO()
        for row in rows:
            payload.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
        write_secure_file(path, payload.getvalue().encode("utf-8"))
        return count
    with _atomic_text(path) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def default_job() -> Dict[str, Any]:
    home = Path.home()
    return {
        "version": VERSION,
        "machine_id": safe_name(os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or "local-machine"),
        "output_root": str(OUTPUT_ROOT),
        "mode": "input_output",
        "copy_raw": False,
        "apps": {**{app: True for app in LOCAL_SOURCE_APPS}, "whatsapp": False, "telegram_saved_messages": False},
        "source_roots": {
            "codex": [str(home / ".codex" / "sessions"), str(home / ".codex" / "archived_sessions")],
            "claude": [str(home / ".claude" / "projects")],
            "chatgpt": [],
        },
        "project_filters": [],
        "worktrees": [],
        "direction": "both",
        "whatsapp": normalize_whatsapp_options(),
        "telegram": normalize_telegram_options(),
    }


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def normalize_job(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    job = _deep_merge(default_job(), raw or {})
    job["version"] = VERSION
    job["machine_id"] = safe_name(job.get("machine_id"), "local-machine")
    output_root = Path(str(job.get("output_root") or OUTPUT_ROOT)).expanduser()
    if not output_root.is_absolute():
        output_root = DATA_DIR / output_root
    job["output_root"] = str(output_root.resolve())
    mode = str(job.get("mode") or "input_output")
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"mode must be one of: {', '.join(SUPPORTED_MODES)}")
    job["mode"] = mode
    direction = str(job.get("direction") or "both").strip().lower()
    if direction not in SUPPORTED_DIRECTIONS:
        raise ValueError(f"direction must be one of: {', '.join(SUPPORTED_DIRECTIONS)}")
    job["direction"] = direction
    job["copy_raw"] = bool(job.get("copy_raw"))
    apps = job.get("apps") if isinstance(job.get("apps"), dict) else {}
    job["apps"] = {app: bool(apps.get(app, False)) for app in SUPPORTED_APPS}
    roots = job.get("source_roots") if isinstance(job.get("source_roots"), dict) else {}
    normalized_roots: Dict[str, List[str]] = {}
    for app in LOCAL_SOURCE_APPS:
        values = roots.get(app, [])
        if isinstance(values, str):
            values = [values]
        normalized_roots[app] = [str(Path(value).expanduser()) for value in values if str(value).strip()]
    job["source_roots"] = normalized_roots
    filters = job.get("project_filters") or []
    job["project_filters"] = [str(value).strip() for value in filters if str(value).strip()]
    worktrees = job.get("worktrees") or []
    job["worktrees"] = [item for item in worktrees if isinstance(item, dict) and str(item.get("path") or "").strip()]
    job["whatsapp"] = normalize_whatsapp_options(job.get("whatsapp") if isinstance(job.get("whatsapp"), dict) else {})
    job["telegram"] = normalize_telegram_options(job.get("telegram") if isinstance(job.get("telegram"), dict) else {})
    return job


def load_job(job_path: Path = JOB_PATH) -> Dict[str, Any]:
    return normalize_job(read_json(job_path, {}))


def save_job(job: Dict[str, Any], job_path: Path = JOB_PATH) -> Dict[str, Any]:
    normalized = normalize_job(job)
    write_json(job_path, normalized)
    return normalized


def update_job(overrides: Dict[str, Any], job_path: Path = JOB_PATH) -> Dict[str, Any]:
    if not isinstance(overrides, dict):
        raise ValueError("Collection settings must be a JSON object.")
    return save_job(_deep_merge(load_job(job_path), overrides), job_path)


def _iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "content", "message", "output_text", "input_text", "parts"):
            text = _content_text(value.get(key))
            if text:
                return text
        return ""
    if not isinstance(value, list):
        return ""
    parts = [_content_text(item) for item in value]
    return "\n".join(part for part in parts if part).strip()


def _timestamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        try:
            seconds = float(value)
            if seconds > 10**12:
                seconds /= 1000
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return ""
    return str(value or "").strip()


def _direction_for_role(role: object) -> str:
    """Map local AI conversation roles to the operator's message direction."""

    return "sent" if str(role or "").strip().lower() == "user" else "received"


def _append_turn(turns: List[Dict[str, str]], role: object, content: Any, timestamp: Any = None, direction: Optional[str] = None) -> None:
    normalized_role = str(role or "").strip().lower()
    if normalized_role in {"human", "user_message"}:
        normalized_role = "user"
    elif normalized_role in {"agent", "assistant_message", "model"}:
        normalized_role = "assistant"
    if normalized_role not in {"user", "assistant", "system"}:
        return
    text = _content_text(content)
    if not text:
        return
    if text.startswith(("<permissions instructions>", "<environment_context>", "<automation_context>")):
        return
    turns.append(
        {
            "role": normalized_role,
            "direction": direction if direction in {"sent", "received"} else _direction_for_role(normalized_role),
            "text": text,
            "timestamp": _timestamp(timestamp),
        }
    )


def _session_id(prefix: str, path: Path, configured: Any = None) -> str:
    value = str(configured or "").strip()
    if value:
        return safe_name(value, prefix)
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8", "replace")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def parse_codex(path: Path) -> Optional[Dict[str, Any]]:
    turns: List[Dict[str, str]] = []
    session_id: Optional[str] = None
    project = ""
    created_at = ""
    for row in _iter_jsonl(path):
        timestamp = row.get("timestamp") or row.get("created_at")
        created_at = created_at or _timestamp(timestamp)
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        row_type = str(row.get("type") or payload.get("type") or "").lower()
        if row_type in {"session_meta", "session"}:
            session_id = session_id or payload.get("id") or payload.get("session_id")
            project = project or str(payload.get("cwd") or payload.get("project") or "")
            continue
        if row_type == "event_msg":
            event_type = str(payload.get("type") or "").lower()
            if event_type in {"user_message", "agent_message", "assistant_message"}:
                _append_turn(turns, event_type, payload.get("message") or payload.get("content"), timestamp)
            continue
        if row_type in {"response_item", "message"}:
            item_type = str(payload.get("type") or "").lower()
            role = payload.get("role")
            if item_type == "message" or role:
                _append_turn(turns, role, payload.get("content") or payload.get("message"), timestamp)
    if not turns:
        return None
    return {
        "id": _session_id("codex", path, session_id),
        "app": "codex",
        "title": path.stem,
        "project": project,
        "created_at": created_at or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "turns": turns,
    }


def parse_claude(path: Path) -> Optional[Dict[str, Any]]:
    turns: List[Dict[str, str]] = []
    session_id: Optional[str] = None
    project = ""
    created_at = ""
    for row in _iter_jsonl(path):
        row_type = str(row.get("type") or "").lower()
        timestamp = row.get("timestamp") or row.get("created_at")
        created_at = created_at or _timestamp(timestamp)
        session_id = session_id or row.get("sessionId") or row.get("session_id")
        project = project or str(row.get("cwd") or row.get("project") or "")
        if row_type not in {"user", "assistant", "system"}:
            continue
        message = row.get("message") if isinstance(row.get("message"), dict) else row
        _append_turn(turns, row_type, message.get("content") or message.get("text"), timestamp)
    if not turns:
        return None
    return {
        "id": _session_id("claude", path, session_id),
        "app": "claude",
        "title": path.stem,
        "project": project,
        "created_at": created_at or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "turns": turns,
    }


def _chatgpt_mapping_turns(mapping: Dict[str, Any]) -> List[Dict[str, str]]:
    ordered: List[Tuple[float, Dict[str, Any]]] = []
    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        try:
            sequence = float(message.get("create_time") or node.get("create_time") or 0)
        except (TypeError, ValueError):
            sequence = 0.0
        ordered.append((sequence, message))
    turns: List[Dict[str, str]] = []
    for _sequence, message in sorted(ordered, key=lambda item: item[0]):
        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        _append_turn(turns, author.get("role"), message.get("content"), message.get("create_time"))
    return turns


def parse_chatgpt(path: Path) -> List[Dict[str, Any]]:
    payload = read_json(path, None, migrate_plaintext=False)
    records: Sequence[Any]
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict) and isinstance(payload.get("conversations"), list):
        records = payload["conversations"]
    else:
        records = [payload] if isinstance(payload, dict) else []
    sessions: List[Dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        mapping = record.get("mapping") if isinstance(record.get("mapping"), dict) else {}
        turns = _chatgpt_mapping_turns(mapping)
        if not turns and isinstance(record.get("messages"), list):
            for message in record["messages"]:
                if isinstance(message, dict):
                    _append_turn(turns, message.get("role"), message.get("content") or message.get("text"), message.get("timestamp"))
        if not turns and isinstance(record.get("raw_lines"), list):
            # Accessibility snapshots do not reliably label speakers. Preserve
            # usable text as alternating conversation turns rather than guessing
            # hidden browser/account data.
            role = "assistant"
            for line in record["raw_lines"]:
                match = _CHATGPT_LINE_RE.match(str(line))
                text = match.group(1).strip() if match else ""
                if text and "actions" not in text.lower():
                    _append_turn(turns, role, text, record.get("observed_at"))
                    role = "user" if role == "assistant" else "assistant"
        if not turns:
            continue
        sessions.append(
            {
                "id": _session_id("chatgpt", path, record.get("id") or record.get("conversation_id") or f"{path.stem}-{index}"),
                "app": "chatgpt",
                "title": str(record.get("title") or path.stem),
                "project": str(record.get("project_slug") or record.get("project") or ""),
                "created_at": _timestamp(record.get("create_time") or record.get("update_time") or record.get("observed_at")),
                "turns": turns,
            }
        )
    return sessions


def _patterns_for(app: str) -> Tuple[str, ...]:
    if app in {"codex", "claude"}:
        return ("*.jsonl",)
    return ("*.json",)


def iter_source_files(
    app: str,
    job: Dict[str, Any],
    *,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Iterator[Path]:
    seen: set[Path] = set()
    for raw_root in job.get("source_roots", {}).get(app, []):
        if _cancel_requested(cancel_check):
            raise CollectionCancelled()
        root = Path(raw_root).expanduser()
        if not root.is_dir():
            continue
        for pattern in _patterns_for(app):
            try:
                candidates = root.rglob(pattern)
                for candidate in candidates:
                    if _cancel_requested(cancel_check):
                        raise CollectionCancelled()
                    if not candidate.is_file() or candidate.name in _SKIP_SOURCE_PARTS:
                        continue
                    resolved = candidate.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        yield resolved
            except OSError:
                continue


def _matches_filters(session: Dict[str, Any], filters: Sequence[str]) -> bool:
    if not filters:
        return True
    target = session.get("target") if isinstance(session.get("target"), dict) else {}
    haystack = " ".join(
        str(value or "")
        for value in (
            session.get("title"),
            session.get("project"),
            session.get("app"),
            target.get("id"),
            target.get("name"),
        )
    ).casefold()
    return any(str(value).casefold() in haystack for value in filters)


def _session_target(session: Dict[str, Any]) -> Dict[str, str]:
    target = session.get("target") if isinstance(session.get("target"), dict) else {}
    name = str(target.get("name") or session.get("project") or session.get("title") or session.get("id") or "Conversation").strip()
    identifier = str(target.get("id") or session.get("project") or session.get("id") or name).strip()
    kind = str(target.get("kind") or ("project" if session.get("project") else "conversation")).strip()
    return {"kind": kind or "conversation", "id": identifier or "conversation", "name": name or "Conversation"}


def _normalize_session(session: Dict[str, Any], machine_id: str) -> Dict[str, Any]:
    """Add stable hierarchy metadata without changing collected message text."""

    normalized = copy.deepcopy(session)
    normalized["app"] = safe_name(normalized.get("app"), "unknown")
    normalized["id"] = safe_name(normalized.get("id"), normalized["app"])
    normalized["machine_id"] = safe_name(normalized.get("machine_id") or machine_id, "local-machine")
    normalized["target"] = _session_target(normalized)
    turns: List[Dict[str, Any]] = []
    for raw_turn in normalized.get("turns", []):
        if not isinstance(raw_turn, dict):
            continue
        turn = dict(raw_turn)
        role = str(turn.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system"}:
            continue
        turn["role"] = role
        turn["direction"] = turn.get("direction") if turn.get("direction") in {"sent", "received"} else _direction_for_role(role)
        turn["timestamp"] = _timestamp(turn.get("timestamp"))
        turns.append(turn)
    normalized["turns"] = turns
    return normalized


def _session_path(output_root: Path, session: Dict[str, Any], machine_id: str) -> Path:
    return output_root / "index" / "sessions" / safe_name(session["app"]) / safe_name(machine_id) / f"{safe_name(session['id'])}.json"


def _write_session(output_root: Path, session: Dict[str, Any], machine_id: str) -> bool:
    normalized = _normalize_session(session, machine_id)
    path = _session_path(output_root, normalized, normalized["machine_id"])
    previous = read_json(path, None)
    if previous == normalized:
        return False
    write_json(path, normalized)
    return True


def _capture_worktrees(
    job: Dict[str, Any],
    output_root: Path,
    *,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    captured: List[Dict[str, Any]] = []
    for item in job.get("worktrees", []):
        if _cancel_requested(cancel_check):
            raise CollectionCancelled()
        root = Path(str(item.get("path") or "")).expanduser()
        if not root.is_dir() or not (root / ".git").exists():
            continue
        name = safe_name(item.get("name") or root.name, "worktree")
        target = output_root / "exports" / "worktrees" / name
        target.mkdir(parents=True, exist_ok=True)
        commands = {
            "status.txt": ["git", "status", "--short"],
            "log.txt": ["git", "log", "--oneline", "-40"],
            "diff.txt": ["git", "diff", "--stat"],
        }
        for filename, command in commands.items():
            if _cancel_requested(cancel_check):
                raise CollectionCancelled()
            try:
                completed = run(command, cwd=root, stdout=PIPE, stderr=PIPE, text=True, timeout=20, check=False)
                write_secure_file(target / filename, (completed.stdout if completed.returncode == 0 else "").encode("utf-8"))
            except OSError:
                continue
        captured.append({"name": name, "path": str(target)})
    return captured


def _load_sessions(
    output_root: Path,
    *,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    sessions: List[Dict[str, Any]] = []
    root = output_root / "index" / "sessions"
    if not root.is_dir():
        return sessions
    for path in root.rglob("*.json"):
        if _cancel_requested(cancel_check):
            raise CollectionCancelled()
        session = read_json(path, None)
        if isinstance(session, dict) and isinstance(session.get("turns"), list):
            try:
                relative = path.relative_to(root).parts
                machine_id = relative[1] if len(relative) >= 3 else "local-machine"
            except ValueError:
                machine_id = "local-machine"
            sessions.append(_normalize_session(session, machine_id))
    return sorted(sessions, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))


def _visible_turns(session: Dict[str, Any], mode: str, direction: str = "both") -> List[Dict[str, Any]]:
    turns = [turn for turn in session.get("turns", []) if isinstance(turn, dict)]
    if mode == "input_only":
        turns = [turn for turn in turns if turn.get("role") == "user"]
    elif mode == "output_only":
        turns = [turn for turn in turns if turn.get("role") == "assistant"]
    return turns if direction == "both" else [turn for turn in turns if turn.get("direction") == direction]


def _matches_selected_apps(session: Dict[str, Any], apps: Dict[str, Any]) -> bool:
    """Keep a selected application from leaking prior collection into its export."""

    return bool(apps.get(str(session.get("app") or "")))


def _timeline_event(session: Dict[str, Any], turn: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Public review metadata only; message content remains in local export files."""

    return {
        "id": f"{session.get('id')}:{index}",
        "session_id": session.get("id"),
        "timestamp": turn.get("timestamp") or session.get("created_at"),
        "app": session.get("app"),
        "machine_id": session.get("machine_id"),
        "project": session.get("project"),
        "title": session.get("title"),
        "target": session.get("target"),
        "role": turn.get("role"),
        "direction": turn.get("direction"),
    }


def _write_views(
    job: Dict[str, Any],
    output_root: Path,
    *,
    collection_report: Optional[Dict[str, Any]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    sessions = [
        session
        for session in _load_sessions(output_root, cancel_check=cancel_check)
        if _matches_selected_apps(session, job["apps"])
        and _matches_filters(session, job.get("project_filters", []))
    ]
    view_root = output_root / "index_unified"
    mode = job["mode"]
    direction = job["direction"]
    training_sessions = []
    training_turns = []
    pairs = []
    timeline_events = []
    for session in sessions:
        if _cancel_requested(cancel_check):
            raise CollectionCancelled()
        turns = _visible_turns(session, mode, direction)
        training_sessions.append(
            {
                "id": session.get("id"),
                "app": session.get("app"),
                "machine_id": session.get("machine_id"),
                "project": session.get("project"),
                "target": session.get("target"),
                "title": session.get("title"),
                "created_at": session.get("created_at"),
                "turns": turns,
            }
        )
        prior_user: Optional[Dict[str, Any]] = None
        for index, turn in enumerate(turns):
            if _cancel_requested(cancel_check):
                raise CollectionCancelled()
            event = {
                "session_id": session.get("id"),
                "app": session.get("app"),
                "machine_id": session.get("machine_id"),
                "project": session.get("project"),
                "target": session.get("target"),
                **turn,
            }
            training_turns.append(event)
            timeline_events.append(_timeline_event(session, turn, index))
            if turn.get("role") == "user":
                prior_user = turn
            elif turn.get("role") == "assistant" and prior_user is not None and mode == "input_output":
                pairs.append(
                    {
                        "session_id": session.get("id"),
                        "app": session.get("app"),
                        "machine_id": session.get("machine_id"),
                        "project": session.get("project"),
                        "target": session.get("target"),
                        "input": prior_user.get("text"),
                        "output": turn.get("text"),
                    }
                )
    if _cancel_requested(cancel_check):
        raise CollectionCancelled()
    write_jsonl(view_root / "training_sessions.jsonl", training_sessions)
    write_jsonl(view_root / "training_turns.jsonl", training_turns)
    write_jsonl(view_root / "input_output_pairs.jsonl", pairs)
    write_jsonl(view_root / "timeline_events.jsonl", sorted(timeline_events, key=lambda event: str(event.get("timestamp") or "")))
    manifest = {
        "version": VERSION,
        "created_at": utc_now(),
        "mode": mode,
        "direction": direction,
        "counts": {"sessions": len(training_sessions), "turns": len(training_turns), "pairs": len(pairs)},
        "collection": collection_report or {},
    }
    write_json(view_root / "manifest.json", manifest)
    return manifest


def collect(
    job: Optional[Dict[str, Any]] = None,
    *,
    dry_run: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    _attach_secure_storage()
    normalized = normalize_job(job or load_job())
    output_root = Path(normalized["output_root"])
    report: Dict[str, Any] = {
        "status": "success",
        "started_at": utc_now(),
        "scanned": 0,
        "parsed": 0,
        "updated": 0,
        "errors": [],
        "messaging": {},
    }
    if _cancel_requested(cancel_check):
        return _cancelled_report(report)
    if not dry_run:
        output_root.mkdir(parents=True, exist_ok=True)
    for app in LOCAL_SOURCE_APPS:
        if _cancel_requested(cancel_check):
            return _cancelled_report(report)
        if not normalized["apps"].get(app):
            continue
        try:
            sources = iter_source_files(app, normalized, cancel_check=cancel_check)
            for source in sources:
                report["scanned"] += 1
                try:
                    parsed = parse_codex(source) if app == "codex" else parse_claude(source) if app == "claude" else parse_chatgpt(source)
                    sessions = [parsed] if isinstance(parsed, dict) else list(parsed or [])
                except Exception as exc:
                    report["errors"].append(f"{source.name}: {type(exc).__name__}")
                    continue
                for session in sessions:
                    if _cancel_requested(cancel_check):
                        return _cancelled_report(report)
                    if not session or not _matches_filters(session, normalized["project_filters"]):
                        continue
                    report["parsed"] += 1
                    if not dry_run and _write_session(output_root, session, normalized["machine_id"]):
                        report["updated"] += 1
                    if not dry_run and normalized["copy_raw"]:
                        raw_target = output_root / "raw" / safe_name(normalized["machine_id"]) / safe_name(app) / source.name
                        raw_target.parent.mkdir(parents=True, exist_ok=True)
                        if secure_storage_enabled():
                            write_secure_file(raw_target, read_secure_file(source, migrate_plaintext=False))
                        else:
                            shutil.copy2(source, raw_target)
        except CollectionCancelled:
            return _cancelled_report(report)
    if not dry_run and normalized["apps"].get("whatsapp"):
        whatsapp_result = collect_whatsapp_history(
            machine_id=normalized["machine_id"],
            options=normalized["whatsapp"],
            cancel_check=cancel_check,
        )
        report["messaging"]["whatsapp"] = {key: value for key, value in whatsapp_result.items() if key != "sessions"}
        if whatsapp_result.get("status") == "cancelled":
            return _cancelled_report(report)
        if whatsapp_result.get("status") == "success":
            for session in whatsapp_result.get("sessions", []):
                if not isinstance(session, dict) or not _matches_filters(session, normalized["project_filters"]):
                    continue
                report["parsed"] += 1
                if _write_session(output_root, session, normalized["machine_id"]):
                    report["updated"] += 1
        else:
            report["errors"].append(f"whatsapp: {whatsapp_result.get('message') or 'collection failed'}")
    if not dry_run and normalized["apps"].get("telegram_saved_messages"):
        telegram_result = collect_telegram_saved_messages(
            machine_id=normalized["machine_id"],
            options=normalized["telegram"],
            cancel_check=cancel_check,
        )
        report["messaging"]["telegram_saved_messages"] = {key: value for key, value in telegram_result.items() if key != "sessions"}
        if telegram_result.get("status") == "cancelled":
            return _cancelled_report(report)
        if telegram_result.get("status") == "success":
            for session in telegram_result.get("sessions", []):
                if not isinstance(session, dict) or not _matches_filters(session, normalized["project_filters"]):
                    continue
                report["parsed"] += 1
                if _write_session(output_root, session, normalized["machine_id"]):
                    report["updated"] += 1
        else:
            report["errors"].append(f"telegram_saved_messages: {telegram_result.get('message') or 'collection failed'}")
    if dry_run:
        capabilities = messaging_capabilities()
        for app in ("whatsapp", "telegram_saved_messages"):
            if normalized["apps"].get(app):
                report["messaging"][app] = {"status": "not_run", "support": capabilities[app]}
    if not dry_run:
        try:
            report["worktrees"] = _capture_worktrees(normalized, output_root, cancel_check=cancel_check)
            report["manifest"] = _write_views(
                normalized,
                output_root,
                collection_report=report,
                cancel_check=cancel_check,
            )
        except CollectionCancelled:
            return _cancelled_report(report)
    report["finished_at"] = utc_now()
    return report


def collection_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    """Return only safe collection counts for model-callable tools."""

    messaging: Dict[str, Dict[str, Any]] = {}
    for app, details in (report.get("messaging") or {}).items():
        if not isinstance(details, dict):
            continue
        messaging[str(app)] = {
            key: details[key]
            for key in ("status", "chat_count", "message_count", "time_limit_reached", "all_available_history", "time_budget_seconds")
            if key in details
        }
    return {
        key: report.get(key)
        for key in ("status", "scanned", "parsed", "updated", "finished_at")
        if key in report
    } | {"messaging": messaging}


def _application_job(application: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    app = str(application or "").strip().lower()
    if app not in SUPPORTED_APPS:
        raise ValueError(f"application must be one of: {', '.join(SUPPORTED_APPS)}")
    current = load_job()
    requested = dict(options or {})
    current["apps"] = {candidate: candidate == app for candidate in SUPPORTED_APPS}
    # from __debug_provenance_s__ import btc
    current["copy_raw"] = False
    current["worktrees"] = []
    if requested.get("mode") is not None:
        current["mode"] = requested["mode"]
    if requested.get("direction") is not None:
        current["direction"] = requested["direction"]
    if app == "whatsapp":
        current["whatsapp"] = {**current.get("whatsapp", {}), **requested}
    elif app == "telegram_saved_messages":
        current["telegram"] = {**current.get("telegram", {}), **requested}
    return normalize_job(current)


def collect_application(
    application: str,
    *,
    options: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Collect one selected source without changing the saved collector settings."""
    try:
        return collect(_application_job(application, options), dry_run=dry_run, cancel_check=cancel_check)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}


def collect_application_for_training(
    application: str,
    *,
    options: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Collect one app and create its private Fine Tuning-compatible export."""
    try:
        job = _application_job(application, options)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    report = collect(job, cancel_check=cancel_check)
    if report.get("status") == "cancelled":
        return {"status": "cancelled", "message": "The selected collection was cancelled."}
    if report.get("status") != "success" or report.get("errors"):
        return {
            "status": "error",
            "message": "The selected application could not be collected.",
            "errors": list(report.get("errors") or []),
        }
    exported = create_training_export(job, title=title)
    if exported.get("status") != "success":
        return exported
    return {
        "status": "success",
        "application": str(application).strip().lower(),
        "collection": collection_summary(report),
        "export": exported.get("export"),
    }


def rebuild(
    job: Optional[Dict[str, Any]] = None,
    *,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    _attach_secure_storage()
    normalized = normalize_job(job or load_job())
    output_root = Path(normalized["output_root"])
    try:
        manifest = _write_views(normalized, output_root, cancel_check=cancel_check)
    except CollectionCancelled:
        return {"status": "cancelled", "finished_at": utc_now()}
    return {"status": "success", "manifest": manifest, "finished_at": utc_now()}


def _export_samples(sessions: Sequence[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    for session in sessions:
        context: List[Dict[str, str]] = []
        for turn in session.get("turns", []):
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or "").strip()
            text = str(turn.get("text") or "").strip()
            if role not in {"user", "assistant", "system"} or not text:
                continue
            if role == "assistant":
                messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
                messages.extend(context[-8:])
                if not any(message["role"] == "user" for message in messages):
                    messages.append({"role": "user", "content": "Continue in the style of the examples."})
                messages.append({"role": "assistant", "content": text})
                yield {"messages": messages}
            context.append({"role": role, "content": text})


def _public_export_metadata(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Keep local implementation paths inside the local manifest only."""

    return {key: value for key, value in manifest.items() if key not in {"train_path", "eval_path"}}


def create_training_export(job: Optional[Dict[str, Any]] = None, *, title: Optional[str] = None) -> Dict[str, Any]:
    _attach_secure_storage()
    normalized = normalize_job(job or load_job())
    output_root = Path(normalized["output_root"])
    sessions: List[Dict[str, Any]] = []
    for session in _load_sessions(output_root):
        if not _matches_selected_apps(session, normalized["apps"]):
            continue
        if not _matches_filters(session, normalized["project_filters"]):
            continue
        selected = dict(session)
        selected["turns"] = _visible_turns(session, normalized["mode"], normalized["direction"])
        if selected["turns"]:
            sessions.append(selected)
    samples = list(_export_samples(sessions))
    if not samples:
        return {"status": "error", "message": "No assistant replies are available for a training export."}
    export_id = f"export-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    export_dir = output_root / "index_unified" / "exports_for_training" / export_id
    eval_count = max(1, int(len(samples) * 0.08)) if len(samples) >= 10 else 0
    train_samples = samples[:-eval_count] if eval_count else samples
    eval_samples = samples[-eval_count:] if eval_count else []
    train_count = write_jsonl(export_dir / "train.jsonl", train_samples)
    eval_count = write_jsonl(export_dir / "eval.jsonl", eval_samples) if eval_samples else 0
    message_count = sum(len(sample["messages"]) for sample in samples)
    manifest = {
        "id": export_id,
        "created_at": utc_now(),
        "title": str(title or "Data Collector training export"),
        "train_path": str((export_dir / "train.jsonl").resolve()),
        "eval_path": str((export_dir / "eval.jsonl").resolve()) if eval_count else None,
        "source_count": len(sessions),
        "sample_count": len(samples),
        "train_sample_count": train_count,
        "eval_sample_count": eval_count,
        "message_count": message_count,
        "assistant_message_count": len(samples),
        "filters": {
            "apps": [app for app, enabled in normalized["apps"].items() if enabled],
            "project_filters": normalized["project_filters"],
            "mode": normalized["mode"],
            "direction": normalized["direction"],
        },
    }
    write_json(export_dir / "manifest.json", manifest)
    return {"status": "success", "export": _public_export_metadata(manifest)}


def list_training_exports(job: Optional[Dict[str, Any]] = None, *, limit: int = 30) -> Dict[str, Any]:
    normalized = normalize_job(job or load_job())
    root = Path(normalized["output_root"]) / "index_unified" / "exports_for_training"
    exports: List[Dict[str, Any]] = []
    if root.is_dir():
        for manifest_path in root.glob("*/manifest.json"):
            manifest = read_json(manifest_path, None)
            if isinstance(manifest, dict) and manifest.get("id"):
                exports.append(_public_export_metadata(manifest))
    exports.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {"status": "success", "exports": exports[: max(1, min(int(limit), 100))], "count": len(exports)}


def export_zip(export_id: str, job: Optional[Dict[str, Any]] = None) -> bytes:
    _attach_secure_storage()
    normalized = normalize_job(job or load_job())
    export_dir = Path(normalized["output_root"]) / "index_unified" / "exports_for_training" / safe_name(export_id)
    manifest = export_dir / "manifest.json"
    train = export_dir / "train.jsonl"
    if not manifest.is_file() or not train.is_file():
        raise FileNotFoundError("Training export was not found.")
    buffer = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        metadata = read_json(manifest, {})
        archive.writestr("manifest.json", json.dumps(_public_export_metadata(metadata), indent=2, sort_keys=True))
        archive.writestr("train.jsonl", read_secure_file(train))
        evaluation = export_dir / "eval.jsonl"
        if evaluation.is_file():
            archive.writestr("eval.jsonl", read_secure_file(evaluation))
    buffer.seek(0)
    return buffer.read()


def application_capabilities(job: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Describe selectable sources without disclosing local filesystem paths."""

    normalized = normalize_job(job or load_job())
    labels = {"codex": "Codex", "claude": "Claude", "chatgpt": "ChatGPT export"}
    applications: List[Dict[str, Any]] = []
    for app in LOCAL_SOURCE_APPS:
        roots = normalized["source_roots"].get(app, [])
        found_roots = 0
        for value in roots:
            try:
                found_roots += int(Path(value).expanduser().is_dir())
            except OSError:
                continue
        if found_roots:
            message = f"{labels[app]} local conversation source is available."
        elif app == "chatgpt":
            message = "Choose a local ChatGPT conversations export folder to enable this source."
        else:
            message = f"{labels[app]} local conversation files were not found on this machine."
        applications.append(
            {
                "id": app,
                "label": labels[app],
                "available": bool(found_roots),
                "enabled": bool(normalized["apps"].get(app)),
                "message": message,
                "source_root_count": len(roots),
                "found_root_count": found_roots,
                "supports": {"projects": True, "worktrees": app in {"codex", "claude"}, "all_available_history": True},
            }
        )
    for app, capability in messaging_capabilities().items():
        applications.append({**capability, "enabled": bool(normalized["apps"].get(app))})
    return {"status": "success", "applications": applications}


def list_timeline(
    job: Optional[Dict[str, Any]] = None,
    *,
    app_id: str = "",
    machine_id: str = "",
    project: str = "",
    target: str = "",
    direction: str = "both",
    offset: int = 0,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return chronological hierarchy metadata in pages, never message bodies.

    Pagination protects the loopback UI response size; it does not limit what
    collection stores or what a later page can reach.
    """

    normalized = normalize_job(job or load_job())
    selected_direction = str(direction or "both").strip().lower()
    if selected_direction not in SUPPORTED_DIRECTIONS:
        raise ValueError(f"direction must be one of: {', '.join(SUPPORTED_DIRECTIONS)}")
    try:
        page_offset = max(0, int(offset))
        page_limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError) as exc:
        raise ValueError("offset and limit must be positive integers.") from exc
    filters = {
        "app": str(app_id or "").casefold(),
        "machine_id": str(machine_id or "").casefold(),
        "project": str(project or "").casefold(),
        "target": str(target or "").casefold(),
    }
    events: List[Dict[str, Any]] = []
    for session in _load_sessions(Path(normalized["output_root"])):
        session_target = session.get("target") if isinstance(session.get("target"), dict) else {}
        if filters["app"] and filters["app"] != str(session.get("app") or "").casefold():
            continue
        if filters["machine_id"] and filters["machine_id"] != str(session.get("machine_id") or "").casefold():
            continue
        if filters["project"] and filters["project"] not in str(session.get("project") or "").casefold():
            continue
        if filters["target"] and filters["target"] not in " ".join(
            str(session_target.get(key) or "") for key in ("id", "name", "kind")
        ).casefold():
            continue
        for index, turn in enumerate(_visible_turns(session, "input_output", selected_direction)):
            events.append(_timeline_event(session, turn, index))
    events.sort(key=lambda event: (str(event.get("timestamp") or ""), str(event.get("id") or "")))
    page = events[page_offset : page_offset + page_limit]
    next_offset = page_offset + len(page)
    return {
        "status": "success",
        "events": page,
        "total": len(events),
        "offset": page_offset,
        "limit": page_limit,
        "next_offset": next_offset if next_offset < len(events) else None,
        "filters": {**filters, "direction": selected_direction},
    }


def public_status(job: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    normalized = normalize_job(job or load_job())
    output_root = Path(normalized["output_root"])
    manifest = read_json(output_root / "index_unified" / "manifest.json", {})
    return {
        "status": "ok",
        "agent_name": AGENT_NAME,
        "version": VERSION,
        "loopback_handoff": True,
        "timeline": {"available": True, "message_bodies_exposed": False},
        "configured_apps": [app for app, enabled in normalized["apps"].items() if enabled],
        "counts": (manifest or {}).get("counts", {}),
    }


__all__ = [
    "AGENT_NAME",
    "JOB_PATH",
    "application_capabilities",
    "collect",
    "collect_application",
    "collect_application_for_training",
    "collection_summary",
    "create_training_export",
    "export_zip",
    "list_training_exports",
    "list_timeline",
    "load_job",
    "public_status",
    "rebuild",
    "save_job",
    "update_job",
]
