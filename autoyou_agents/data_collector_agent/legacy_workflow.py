# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-8bed4bc5bfa84e954ff0273e

#!/usr/bin/env python3
"""DataCollector v2: local Codex and Claude session archival and training views.

The collector deliberately has two layers:
  * raw/ keeps materialized source artifacts for reprocessing;
  * index/ and index_unified/ contain natural-language session records only.

The default view is ``input_output``.  Rebuild it as ``input_only`` or
``output_only`` without copying the source logs again.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import argparse
import copy
import json
import os
import plistlib
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from shared.platform_runtime import get_service_data_dir

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-8bed4bc5bfa84e954ff0273e"


VERSION = 2
SUPPORTED_MODES = ("input_output", "input_only", "output_only")
OUTPUT_SCOPES = ("all", "final")
APP_STATUS = {
    "chatgpt": "supported",
    "codex": "supported",
    "claude": "supported",
    "antigravity": "experimental",
    "trae": "experimental",
    "vscode": "tbd",
    "cursor": "tbd",
}
_NOISE_PREFIXES = (
    "<permissions instructions>",
    "<environment_context>",
    "<user_instructions>",
    "# Collaboration Mode",
    "<automation_context>",
)
_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*(?:</USER_REQUEST>|\Z)", re.S)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_CHATGPT_A11Y_TEXT_RE = re.compile(r"^\s*\d+\s+(?:text|heading)\s+(.*)$")


def _agent_runtime_root() -> Path:
    """Keep the preserved v2 CLI's mutable state out of the source package."""
    return get_service_data_dir("data_collector_agent", anchor=__file__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str, fallback: str = "item") -> str:
    value = _SAFE_NAME_RE.sub("-", str(value).strip()).strip(".-")
    return value[:120] or fallback


def default_machine_id() -> str:
    return safe_name(f"{platform.system().lower()}-{socket.gethostname().lower()}")


def default_job(project_root: Path) -> dict[str, Any]:
    home = Path.home()
    autoyou = home / "Projects" / "AutoYou"
    return {
        "version": VERSION,
        "machine_id": default_machine_id(),
        "output_root": "collected_context",
        "mode": "input_output",
        "assistant_output": "all",
        "copy_raw": True,
        "apps": {
            "chatgpt": True,
            "codex": True,
            "claude": True,
            "antigravity": False,
            "trae": False,
            "vscode": False,
            "cursor": False,
        },
        "source_roots": {},
        "legacy_archives": [],
        "project_filters": [],
        "worktrees": ([{"path": str(autoyou), "mode": "git"}] if autoyou.is_dir() else []),
    }


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return copy.deepcopy(default)


@contextmanager
def atomic_text(path: Path) -> Iterator[Any]:
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


def write_json(path: Path, value: Any) -> None:
    with atomic_text(path) as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_jsonl_line(handle: Any, value: Any) -> None:
    handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def load_job(job_path: Path) -> dict[str, Any]:
    job = default_job(_agent_runtime_root())
    if job_path.is_file():
        loaded = read_json(job_path, {})
        if not isinstance(loaded, dict):
            raise ValueError(f"collection job must be a JSON object: {job_path}")
        job = _deep_merge(job, loaded)
    return normalize_job(job)


def normalize_job(job: dict[str, Any]) -> dict[str, Any]:
    job = copy.deepcopy(job)
    job["version"] = VERSION
    job["machine_id"] = safe_name(str(job.get("machine_id") or default_machine_id()))
    output_value = str(job.get("output_root") or "collected_context").strip()
    foreign_windows_path = os.name != "nt" and bool(re.match(r"^[A-Za-z]:[\\/]", output_value))
    foreign_posix_path = os.name == "nt" and output_value.startswith("/")
    if foreign_windows_path or foreign_posix_path:
        output_path = _agent_runtime_root() / "collected_context"
    else:
        output_path = Path(output_value).expanduser()
        if not output_path.is_absolute():
            output_path = _agent_runtime_root() / output_path
    job["output_root"] = str(output_path.resolve())
    mode = str(job.get("mode") or "input_output")
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"mode must be one of: {', '.join(SUPPORTED_MODES)}")
    job["mode"] = mode
    scope = str(job.get("assistant_output") or "all")
    if scope not in OUTPUT_SCOPES:
        raise ValueError(f"assistant_output must be one of: {', '.join(OUTPUT_SCOPES)}")
    job["assistant_output"] = scope
    job["copy_raw"] = bool(job.get("copy_raw", True))
    apps = job.get("apps") if isinstance(job.get("apps"), dict) else {}
    job["apps"] = {name: apps.get(name, False) for name in APP_STATUS}
    roots = job.get("source_roots") if isinstance(job.get("source_roots"), dict) else {}
    job["source_roots"] = roots
    legacy_archives: list[dict[str, Any]] = []
    used_archive_ids: set[str] = set()
    for position, raw_archive in enumerate(job.get("legacy_archives") or [], start=1):
        archive = {"path": raw_archive} if isinstance(raw_archive, str) else raw_archive
        if not isinstance(archive, dict):
            continue
        raw_path = str(archive.get("path") or "").strip()
        if not raw_path:
            continue
        foreign_windows_path = os.name != "nt" and bool(re.match(r"^[A-Za-z]:[\\/]", raw_path))
        foreign_posix_path = os.name == "nt" and raw_path.startswith("/")
        if foreign_windows_path or foreign_posix_path:
            archive_path = raw_path
        else:
            archive_location = Path(raw_path).expanduser()
            if not archive_location.is_absolute():
                archive_location = _agent_runtime_root() / archive_location
            archive_path = str(archive_location.resolve())
        archive_id = safe_name(str(archive.get("id") or Path(raw_path.rstrip("\\/ ")).name), f"archive-{position}")
        while archive_id in used_archive_ids:
            archive_id = f"{archive_id}-{position}"
        used_archive_ids.add(archive_id)
        source_sets = archive.get("source_sets") if isinstance(archive.get("source_sets"), dict) else {}
        legacy_archives.append(
            {
                "id": archive_id,
                "path": archive_path,
                "source_sets": {
                    safe_name(str(volume).upper(), "ROOT"): safe_name(str(machine), "legacy")
                    for volume, machine in source_sets.items()
                    if str(volume).strip() and str(machine).strip()
                },
            }
        )
    job["legacy_archives"] = legacy_archives
    filters = job.get("project_filters") or []
    job["project_filters"] = [str(item) for item in filters if str(item).strip()]
    job["worktrees"] = [item for item in (job.get("worktrees") or []) if isinstance(item, dict)]
    return job


def save_job(job_path: Path, job: dict[str, Any]) -> None:
    write_json(job_path, normalize_job(job))


def app_is_enabled(job: dict[str, Any], app: str) -> bool:
    setting = job["apps"].get(app, False)
    return bool(setting.get("enabled")) if isinstance(setting, dict) else bool(setting)


def source_roots_for(app: str, job: dict[str, Any]) -> list[tuple[Path, str]]:
    overrides = job["source_roots"].get(app)
    if overrides:
        if isinstance(overrides, str):
            overrides = [overrides]
        pattern = "*.jsonl" if app in ("codex", "claude") else "transcript*.jsonl"
        if app == "chatgpt":
            pattern = "*.json"
        if app == "trae":
            pattern = "entries.json"
        return [(Path(str(root)).expanduser(), pattern) for root in overrides]

    home = Path.home()
    roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    if app == "codex":
        return [
            (home / ".codex" / "sessions", "rollout-*.jsonl"),
            (home / ".codex" / "archived_sessions", "rollout-*.jsonl"),
        ]
    if app == "claude":
        return [(home / ".claude" / "projects", "*.jsonl")]
    if app == "antigravity":
        return [(home / ".gemini" / "antigravity" / "brain", "transcript*.jsonl")]
    if app == "trae":
        return [(roaming / "Trae" / "User" / "History", "entries.json")]
    return []


def iter_source_files(app: str, job: dict[str, Any]) -> Iterator[Path]:
    if APP_STATUS[app] == "tbd":
        return
    seen: set[Path] = set()
    for root, pattern in source_roots_for(app, job):
        for path in iter_matching_files(root, pattern):
            if app == "chatgpt" and not is_chatgpt_export_path(path):
                continue
            if path not in seen:
                seen.add(path)
                yield path


def iter_matching_files(root: Path, pattern: str) -> Iterator[Path]:
    if not root.is_dir():
        return
    try:
        for path in root.rglob(pattern):
            if path.is_file():
                yield path.resolve()
    except OSError:
        return


def legacy_roots_for(app: str, home: Path) -> list[tuple[Path, str]]:
    if app == "codex":
        return [
            (home / ".codex" / "sessions", "rollout-*.jsonl"),
            (home / ".codex" / "archived_sessions", "rollout-*.jsonl"),
        ]
    if app == "claude":
        return [(home / ".claude" / "projects", "*.jsonl")]
    if app == "antigravity":
        return [(home / ".gemini" / "antigravity" / "brain", "transcript*.jsonl")]
    if app == "trae":
        return [(home / "AppData" / "Roaming" / "Trae" / "User" / "History", "entries.json")]
    return []


def is_chatgpt_export_path(path: Path) -> bool:
    return path.parent.name == "chats" or path.name.startswith("chat_")


def iter_legacy_source_files(app: str, archive: dict[str, Any]) -> Iterator[tuple[Path, str]]:
    archive_root = Path(str(archive["path"]))
    if app == "chatgpt":
        source_sets = archive.get("source_sets") if isinstance(archive.get("source_sets"), dict) else {}
        machine_id = str(source_sets.get("CHATGPT") or f"{archive['id']}-chatgpt")
        seen: set[Path] = set()
        try:
            export_roots = sorted(archive_root.glob("exports*"))
        except OSError:
            return
        for export_root in export_roots:
            chatgpt_root = export_root / "chatgpt"
            for path in iter_matching_files(chatgpt_root, "*.json"):
                if is_chatgpt_export_path(path) and path not in seen:
                    seen.add(path)
                    yield path, machine_id
        return
    raw_root = archive_root / "raw"
    if not raw_root.is_dir():
        return
    source_sets = archive.get("source_sets") if isinstance(archive.get("source_sets"), dict) else {}
    seen: set[Path] = set()
    try:
        volumes = sorted(raw_root.iterdir())
    except OSError:
        return
    for volume in volumes:
        users = volume / "Users"
        if not users.is_dir():
            continue
        machine_id = str(source_sets.get(volume.name.upper()) or f"{archive['id']}-{volume.name}")
        try:
            homes = sorted(users.iterdir())
        except OSError:
            continue
        for home in homes:
            if not home.is_dir():
                continue
            for root, pattern in legacy_roots_for(app, home):
                for path in iter_matching_files(root, pattern):
                    if path not in seen:
                        seen.add(path)
                        yield path, machine_id


def external_archive_relative(archive_id: str, archive_root: Path, source: Path) -> Path:
    try:
        relative = source.resolve().relative_to(archive_root.resolve())
    except (OSError, ValueError):
        relative = Path(source.name)
    return Path("external", safe_name(archive_id, "archive"), *relative.parts)


def source_stamp(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def archive_relative(path: Path, machine_id: str) -> Path:
    path = path.resolve()
    if path.drive:
        drive = safe_name(path.drive.rstrip(":").upper(), "ROOT")
        relative = path.relative_to(path.anchor)
    else:
        drive = "ROOT"
        relative = path.relative_to(path.anchor)
    return Path("raw", machine_id, drive, *relative.parts)


def normalized_relative(session: dict[str, Any]) -> Path:
    return Path(
        "index",
        "sessions",
        safe_name(str(session.get("harness") or "unknown")),
        safe_name(str(session.get("source_set") or "machine")),
        safe_name(str(session.get("conv_id") or "session")) + ".json",
    )


def session_raw_stamp(session: dict[str, Any]) -> tuple[int, int]:
    meta = session.get("meta") if isinstance(session.get("meta"), dict) else {}
    try:
        return int(meta.get("raw_mtime_ns") or 0), int(meta.get("raw_bytes") or 0)
    except (TypeError, ValueError):
        return 0, 0


def write_canonical_session(
    output_root: Path,
    session: dict[str, Any],
    *,
    external_archive: dict[str, Any] | None = None,
) -> tuple[Path, bool]:
    """Keep the newest raw artifact when live and historical sources overlap."""
    metadata = session.setdefault("meta", {})
    if external_archive:
        metadata["storage"] = "external"
        metadata["external_archive_id"] = external_archive["id"]
        metadata["external_archive_root"] = external_archive["path"]
    else:
        metadata["storage"] = "local"
    session_path = output_root / normalized_relative(session)
    existing = read_json(session_path, None)
    should_write = not isinstance(existing, dict)
    if isinstance(existing, dict):
        existing_stamp = session_raw_stamp(existing)
        candidate_stamp = session_raw_stamp(session)
        existing_external = (existing.get("meta") or {}).get("storage") == "external"
        candidate_external = external_archive is not None
        # A live local log is authoritative over any historical snapshot,
        # even when an archival copy happens to have a newer file timestamp.
        should_write = not candidate_external if candidate_external != existing_external else candidate_stamp > existing_stamp
    if should_write:
        write_json(session_path, session)
    return session_path, should_write


def parse_timestamp(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_timestamp(value: Any) -> str | None:
    parsed = parse_timestamp(value)
    return parsed.isoformat() if parsed else None


def jsonl_rows(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        for key in ("text", "input_text", "output_text"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
                break
    return "\n".join(parts)


def claude_text_blocks(content: Any) -> Iterator[str]:
    if isinstance(content, str):
        if content.strip():
            yield content
        return
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, str) and block.strip():
            yield block
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                yield text


def is_noise(text: str) -> bool:
    return text.lstrip().startswith(_NOISE_PREFIXES)


def first_line(text: str, limit: int = 160) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:limit]
    return ""


def infer_project(*hints: str) -> str:
    blob = " ".join(str(hint) for hint in hints if hint).lower().replace("/", "\\")
    for name, needles in (
        ("signtoross", ("signtoross",)),
        ("datacollector", ("datacollector",)),
        ("autoyou", ("autoyou",)),
    ):
        if any(needle in blob for needle in needles):
            return name
    return "unknown"


def make_session(app: str, path: Path, machine_id: str, archive_path: Path, stamp: dict[str, int]) -> dict[str, Any]:
    return {
        "conv_id": path.stem,
        "harness": app,
        "title": "",
        "started_at": None,
        "ended_at": None,
        "cwd": "",
        "project": "unknown",
        "model": "",
        "source_set": machine_id,
        "source_path": archive_path.as_posix(),
        "turns": [],
        "tags": [],
        "meta": {
            "raw_bytes": stamp["bytes"],
            "raw_mtime_ns": stamp["mtime_ns"],
        },
    }


def append_turn(session: dict[str, Any], role: str, text: Any, timestamp: Any, *, final: bool = False) -> None:
    if not isinstance(text, str):
        return
    text = text.strip()
    if not text:
        return
    turns: list[dict[str, Any]] = session["turns"]
    # from __debug_provenance_t__ import address
    turn = {
        "role": role,
        "text": text,
        "ts": iso_timestamp(timestamp),
        "tool": None,
        "meta": {"final": True} if final else {},
        "_order": len(turns),
    }
    # Codex often records the same visible message in an event and a response
    # item.  Merge only adjacent duplicates so genuine repeated prompts remain.
    if turns and turns[-1]["role"] == role and turns[-1]["text"] == text:
        turns[-1]["meta"]["final"] = turns[-1]["meta"].get("final", False) or final
        if not turns[-1].get("ts"):
            turns[-1]["ts"] = turn["ts"]
        return
    turns.append(turn)


def finalize_session(session: dict[str, Any], source_mtime_ns: int) -> dict[str, Any] | None:
    turns = session["turns"]
    if not turns:
        return None
    turns.sort(key=lambda turn: (turn.get("ts") is None, turn.get("ts") or "", turn["_order"]))
    pending_assistant: int | None = None
    for index, turn in enumerate(turns):
        if turn["role"] == "user":
            if pending_assistant is not None:
                turns[pending_assistant]["meta"]["final"] = True
            pending_assistant = None
        elif turn["role"] == "assistant":
            pending_assistant = index
    if pending_assistant is not None:
        turns[pending_assistant]["meta"]["final"] = True
    for turn in turns:
        turn.pop("_order", None)
    timestamps = [turn["ts"] for turn in turns if turn.get("ts")]
    fallback = datetime.fromtimestamp(source_mtime_ns / 1_000_000_000, tz=timezone.utc).isoformat()
    session["started_at"] = timestamps[0] if timestamps else fallback
    session["ended_at"] = timestamps[-1] if timestamps else fallback
    first_user = next((turn["text"] for turn in turns if turn["role"] == "user"), "")
    session["title"] = session["title"] or first_line(first_user)
    session["project"] = infer_project(session["cwd"], session["source_path"], session["title"])
    return session


def parse_codex(path: Path, machine_id: str, archive_path: Path, stamp: dict[str, int]) -> dict[str, Any] | None:
    session = make_session("codex", path, machine_id, archive_path, stamp)
    for row in jsonl_rows(path):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        record_type = row.get("type")
        payload_type = payload.get("type")
        timestamp = row.get("timestamp")
        if record_type == "session_meta":
            session["conv_id"] = str(payload.get("id") or session["conv_id"])
            session["cwd"] = str(payload.get("cwd") or session["cwd"])
            session["model"] = str(payload.get("model") or session["model"])
            for key in ("originator", "cli_version", "source"):
                if payload.get(key) is not None:
                    session["meta"][key] = payload[key]
            continue
        if record_type == "turn_context":
            session["cwd"] = session["cwd"] or str(payload.get("cwd") or "")
            session["model"] = str(payload.get("model") or session["model"])
            continue
        if record_type == "event_msg":
            if payload_type == "user_message":
                text = str(payload.get("message") or "")
                if text and not is_noise(text):
                    append_turn(session, "user", text, timestamp)
            elif payload_type == "agent_message":
                append_turn(session, "assistant", payload.get("message"), timestamp)
            elif payload_type == "task_complete":
                append_turn(session, "assistant", payload.get("last_agent_message"), timestamp, final=True)
            continue
        if record_type != "response_item" or payload_type != "message":
            continue
        role = payload.get("role")
        text = content_text(payload.get("content"))
        if role == "user" and text and not is_noise(text):
            append_turn(session, "user", text, timestamp)
        elif role == "assistant":
            append_turn(session, "assistant", text, timestamp)
    return finalize_session(session, stamp["mtime_ns"])


def parse_claude(path: Path, machine_id: str, archive_path: Path, stamp: dict[str, int]) -> dict[str, Any] | None:
    session = make_session("claude", path, machine_id, archive_path, stamp)
    ai_title = ""
    custom_title = ""
    for row in jsonl_rows(path):
        row_type = row.get("type")
        timestamp = row.get("timestamp")
        if row.get("sessionId"):
            session["conv_id"] = str(row["sessionId"])
        if row_type == "ai-title":
            ai_title = str(row.get("aiTitle") or ai_title)
            continue
        if row_type == "custom-title":
            custom_title = str(row.get("customTitle") or custom_title)
            continue
        if row_type == "system":
            session["cwd"] = session["cwd"] or str(row.get("cwd") or "")
            for key in ("version", "gitBranch", "entrypoint"):
                if row.get(key) is not None:
                    session["meta"][key] = row[key]
            continue
        if row_type not in ("user", "assistant"):
            continue
        session["cwd"] = session["cwd"] or str(row.get("cwd") or "")
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        session["model"] = session["model"] or str(message.get("model") or "")
        for text in claude_text_blocks(message.get("content")):
            append_turn(session, row_type, text, timestamp)
    session["title"] = custom_title or ai_title
    return finalize_session(session, stamp["mtime_ns"])


def parse_antigravity(path: Path, machine_id: str, archive_path: Path, stamp: dict[str, int]) -> dict[str, Any] | None:
    if path.name != "transcript.jsonl":
        return None
    session = make_session("antigravity", path, machine_id, archive_path, stamp)
    try:
        session["conv_id"] = path.parents[2].name
    except IndexError:
        pass
    session["model"] = "gemini-antigravity"
    for row in jsonl_rows(path):
        row_type = str(row.get("type") or "")
        timestamp = row.get("created_at") or row.get("timestamp")
        content = str(row.get("content") or "")
        if row_type == "USER_INPUT":
            match = _USER_REQUEST_RE.search(content)
            append_turn(session, "user", match.group(1) if match else content, timestamp)
        elif row_type == "PLANNER_RESPONSE":
            append_turn(session, "assistant", content, timestamp)
    return finalize_session(session, stamp["mtime_ns"])


def parse_chatgpt_export(path: Path, machine_id: str, archive_path: Path, stamp: dict[str, int]) -> dict[str, Any] | None:
    exported = read_json(path, {})
    if not isinstance(exported, dict) or not isinstance(exported.get("raw_lines"), list):
        return None
    session = make_session("chatgpt", path, machine_id, archive_path, stamp)
    url = str(exported.get("url") or "")
    conversation = re.search(r"/c/([^/?#]+)", url)
    if conversation:
        session["conv_id"] = conversation.group(1)
    elif exported.get("project_slug"):
        session["conv_id"] = f"{exported['project_slug']}-{path.stem}"
    session["title"] = str(exported.get("title") or "")
    session["cwd"] = str(exported.get("project_slug") or "")
    session["model"] = "chatgpt"
    session["meta"]["source_url"] = url
    session["meta"]["captured_at"] = exported.get("observed_at")
    role = "assistant"
    parts: list[str] = []
    for raw_line in exported["raw_lines"]:
        line = str(raw_line)
        if "ChatGPT can make mistakes" in line:
            break
        if "group Response actions" in line:
            append_turn(session, "assistant", "\n".join(parts), exported.get("observed_at"))
            parts = []
            role = "user"
            continue
        if "group Your message actions" in line:
            append_turn(session, "user", "\n".join(parts), exported.get("observed_at"))
            parts = []
            role = "assistant"
            continue
        match = _CHATGPT_A11Y_TEXT_RE.match(line)
        if match and match.group(1).strip():
            parts.append(match.group(1).strip())
    append_turn(session, role, "\n".join(parts), exported.get("observed_at"))
    if not session["turns"]:
        append_turn(session, "assistant", "\n".join(str(line) for line in exported.get("text_lines") or []), exported.get("observed_at"))
    session = finalize_session(session, stamp["mtime_ns"])
    if session and exported.get("project_slug"):
        session["project"] = str(exported["project_slug"])
    return session


PARSERS = {
    "chatgpt": parse_chatgpt_export,
    "codex": parse_codex,
    "claude": parse_claude,
    "antigravity": parse_antigravity,
}


def project_matches(session: dict[str, Any], filters: list[str]) -> bool:
    if not filters:
        return True
    haystack = " ".join(
        str(session.get(key) or "") for key in ("cwd", "project", "source_path", "title")
    ).lower().replace("/", "\\")
    for item in filters:
        value = item.lower().replace("/", "\\").strip()
        if value and (value in haystack or Path(value).name.lower() in haystack):
            return True
    return False


def selected_turns(session: dict[str, Any], job: dict[str, Any]) -> list[dict[str, Any]]:
    mode = job["mode"]
    output_scope = job["assistant_output"]
    selected: list[dict[str, Any]] = []
    for turn in session.get("turns") or []:
        role = turn.get("role")
        if mode == "input_only" and role != "user":
            continue
        if mode == "output_only":
            if role != "assistant":
                continue
            if output_scope == "final" and not (turn.get("meta") or {}).get("final"):
                continue
        if mode == "input_output" and role not in ("user", "assistant"):
            continue
        selected.append(turn)
    return selected


def session_uid(session: dict[str, Any]) -> str:
    return ":".join(
        safe_name(str(session.get(key) or ""), "unknown")
        for key in ("source_set", "harness", "conv_id")
    )


def load_canonical_sessions(output_root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    root = output_root / "index" / "sessions"
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*.json")):
        row = read_json(path, None)
        if isinstance(row, dict) and row.get("conv_id") and row.get("harness"):
            yield path, row


def session_sort_key(session: dict[str, Any]) -> tuple[str, str, str]:
    timestamp = str(session.get("started_at") or session.get("ended_at") or "9999-12-31T23:59:59+00:00")
    return (timestamp, str(session.get("harness") or ""), session_uid(session))


def session_coverage(sessions: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    result: dict[str, Any] = {"count": len(sessions), "earliest": None, "latest": None, "by_app": {}}
    for _, session in sessions:
        app = str(session.get("harness") or "unknown")
        app_stats = result["by_app"].setdefault(app, {"count": 0, "earliest": None, "latest": None})
        app_stats["count"] += 1
        for timestamp in (parse_timestamp(session.get("started_at")), parse_timestamp(session.get("ended_at"))):
            if timestamp is None:
                continue
            for stats in (result, app_stats):
                earliest = parse_timestamp(stats["earliest"])
                latest = parse_timestamp(stats["latest"])
                if earliest is None or timestamp < earliest:
                    stats["earliest"] = timestamp.isoformat()
                if latest is None or timestamp > latest:
                    stats["latest"] = timestamp.isoformat()
    return result


def timeline_coverage(timeline: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [parsed for row in timeline if (parsed := parse_timestamp(row.get("timestamp")))]
    return {
        "count": len(timeline),
        "with_timestamp": len(timestamps),
        "earliest": min(timestamps).isoformat() if timestamps else None,
        "latest": max(timestamps).isoformat() if timestamps else None,
    }


def legacy_timeline_events(job: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for archive in job["legacy_archives"]:
        path = Path(str(archive["path"])) / "index_unified" / "timeline_events.json"
        rows = read_json(path, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            event = copy.deepcopy(row)
            event["origin"] = "legacy_archive"
            event["archive_id"] = archive["id"]
            events.append(event)
    return events


def timeline_sort_key(event: dict[str, Any]) -> tuple[bool, str, str]:
    timestamp = parse_timestamp(event.get("timestamp"))
    return (timestamp is None, timestamp.isoformat() if timestamp else "", str(event.get("reference") or ""))


def write_views(job: dict[str, Any], collection_report: dict[str, Any] | None = None) -> dict[str, Any]:
    output_root = Path(job["output_root"])
    unified = output_root / "index_unified"
    canonical_sessions = list(load_canonical_sessions(output_root))
    sessions: list[tuple[Path, dict[str, Any]]] = []
    for path, session in canonical_sessions:
        if not app_is_enabled(job, str(session["harness"])):
            continue
        if not project_matches(session, job["project_filters"]):
            continue
        if selected_turns(session, job):
            sessions.append((path, session))
    sessions.sort(key=lambda item: session_sort_key(item[1]))

    metadata_path = unified / "sessions.jsonl"
    training_path = unified / "training_sessions.jsonl"
    turns_path = unified / "training_turns.jsonl"
    pairs_path = unified / "input_output_pairs.jsonl"
    timeline_path = unified / "timeline_events.json"
    high_signal_path = unified / "timeline_high_signal.json"
    coverage_path = unified / "coverage.json"
    counts = {"sessions": 0, "turns": 0, "pairs": 0, "by_app": {}, "roles": {"user": 0, "assistant": 0}}
    timeline: list[dict[str, Any]] = []

    with ExitStack() as stack:
        metadata_handle = stack.enter_context(atomic_text(metadata_path))
        training_handle = stack.enter_context(atomic_text(training_path))
        turns_handle = stack.enter_context(atomic_text(turns_path))
        pairs_handle = stack.enter_context(atomic_text(pairs_path))
        global_sequence = 0
        for session_sequence, (_, session) in enumerate(sessions, start=1):
            turns = selected_turns(session, job)
            uid = session_uid(session)
            counts["sessions"] += 1
            harness = str(session["harness"])
            counts["by_app"][harness] = counts["by_app"].get(harness, 0) + 1
            metadata = {
                "session_uid": uid,
                "session_id": session["conv_id"],
                "app": harness,
                "machine_id": session["source_set"],
                "started_at": session.get("started_at"),
                "ended_at": session.get("ended_at"),
                "title": session.get("title", ""),
                "cwd": session.get("cwd", ""),
                "project": session.get("project", "unknown"),
                "source_path": session.get("source_path", ""),
                "turn_count": len(turns),
                "mode": job["mode"],
                "assistant_output": job["assistant_output"],
            }
            write_jsonl_line(metadata_handle, metadata)
            training_record = {**metadata, "turns": turns}
            write_jsonl_line(training_handle, training_record)
            for turn_sequence, turn in enumerate(turns, start=1):
                global_sequence += 1
                role = str(turn.get("role") or "")
                if role in counts["roles"]:
                    counts["roles"][role] += 1
                counts["turns"] += 1
                write_jsonl_line(
                    turns_handle,
                    {
                        "sequence": global_sequence,
                        "session_sequence": session_sequence,
                        "turn_sequence": turn_sequence,
                        "session_uid": uid,
                        "session_id": session["conv_id"],
                        "app": harness,
                        "machine_id": session["source_set"],
                        "timestamp": turn.get("ts") or session.get("started_at"),
                        "role": role,
                        "text": turn.get("text", ""),
                        "final": bool((turn.get("meta") or {}).get("final")),
                    },
                )
            if job["mode"] == "input_output":
                for pair in conversation_pairs(session, turns):
                    pair["session_uid"] = uid
                    pair["session_id"] = session["conv_id"]
                    pair["app"] = harness
                    pair["machine_id"] = session["source_set"]
                    write_jsonl_line(pairs_handle, pair)
                    counts["pairs"] += 1
            timeline.extend(session_timeline_events(session, uid))

    timeline.extend(legacy_timeline_events(job))
    timeline.sort(key=timeline_sort_key)
    write_json(timeline_path, timeline)
    write_json(high_signal_path, timeline)
    coverage = {
        "retention": {
            "date_filter": "none",
            "record_limit": "none",
            "turn_text_truncation": "none",
            "title_preview_characters": 160,
            "legacy_raw_storage": "referenced in place; never copied into this output root",
        },
        "canonical_sessions": session_coverage(canonical_sessions),
        "selected_sessions": session_coverage(sessions),
        "timeline_events": timeline_coverage(timeline),
        "legacy_archives": [
            {"id": archive["id"], "path": archive["path"], "available": Path(str(archive["path"])).is_dir()}
            for archive in job["legacy_archives"]
        ],
    }
    write_json(coverage_path, coverage)
    manifest = {
        "version": VERSION,
        "generated_at": utc_now(),
        "index_storage": "materialized JSON and JSONL files; no symbolic links",
        "mode": job["mode"],
        "assistant_output": job["assistant_output"],
        "machine_id": job["machine_id"],
        "apps": {app: {"enabled": app_is_enabled(job, app), "status": status} for app, status in APP_STATUS.items()},
        "project_filters": job["project_filters"],
        "counts": counts,
        "files": {
            "sessions": str(metadata_path.relative_to(output_root)).replace("\\", "/"),
            "training_sessions": str(training_path.relative_to(output_root)).replace("\\", "/"),
            "training_turns": str(turns_path.relative_to(output_root)).replace("\\", "/"),
            "input_output_pairs": str(pairs_path.relative_to(output_root)).replace("\\", "/"),
            "timeline_events": str(timeline_path.relative_to(output_root)).replace("\\", "/"),
            "coverage": str(coverage_path.relative_to(output_root)).replace("\\", "/"),
        },
        "coverage": coverage,
        "collection": collection_report or {},
    }
    write_json(unified / "manifest.json", manifest)
    return manifest


def conversation_pairs(session: dict[str, Any], turns: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    prompt: dict[str, Any] | None = None
    responses: list[dict[str, Any]] = []
    for turn in turns:
        if turn.get("role") == "user":
            if prompt and responses:
                yield {
                    "timestamp": prompt.get("ts") or session.get("started_at"),
                    "messages": [
                        {"role": "user", "content": prompt.get("text", "")},
                        {"role": "assistant", "content": "\n\n".join(str(item.get("text") or "") for item in responses)},
                    ],
                }
            prompt = turn
            responses = []
        elif turn.get("role") == "assistant" and prompt:
            responses.append(turn)
    if prompt and responses:
        yield {
            "timestamp": prompt.get("ts") or session.get("started_at"),
            "messages": [
                {"role": "user", "content": prompt.get("text", "")},
                {"role": "assistant", "content": "\n\n".join(str(item.get("text") or "") for item in responses)},
            ],
        }


def session_timeline_events(session: dict[str, Any], uid: str) -> list[dict[str, Any]]:
    common = {
        "source_set": session.get("source_set", ""),
        "source_type": session.get("harness", ""),
        "path": session.get("cwd", ""),
        "reference": uid,
        "session_id": session.get("conv_id", ""),
    }
    return [
        {
            **common,
            "timestamp": session.get("started_at"),
            "event_type": "session_first_activity",
            "title": session.get("title", ""),
            "details": f"{len(session.get('turns') or [])} normalized natural-language turns",
        },
        {
            **common,
            "timestamp": session.get("ended_at"),
            "event_type": "session_completed",
            "title": session.get("title", ""),
            "details": "final response retained when present",
        },
    ]


def capture_worktrees(job: dict[str, Any], output_root: Path) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    for item in job["worktrees"]:
        if not item.get("enabled", True) or str(item.get("mode") or "git") != "git":
            continue
        root = Path(str(item.get("path") or "")).expanduser()
        if not root.is_dir():
            captured.append({"path": str(root), "status": "missing"})
            continue
        destination = output_root / "exports" / "worktrees" / safe_name(root.name)
        destination.mkdir(parents=True, exist_ok=True)
        commands = {
            "status.txt": ["git", "-C", str(root), "status", "--short", "--branch"],
            "log.txt": ["git", "-C", str(root), "log", "--all", "--date=iso-strict", "--pretty=fuller", "--stat"],
            "worktree.diff": ["git", "-C", str(root), "diff", "--binary", "--no-ext-diff", "HEAD"],
        }
        command_status: dict[str, int] = {}
        for filename, command in commands.items():
            try:
                result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
                text = result.stdout if result.returncode == 0 else result.stderr
                with atomic_text(destination / filename) as handle:
                    handle.write(text)
                command_status[filename] = result.returncode
            except OSError as exc:
                with atomic_text(destination / filename) as handle:
                    handle.write(f"unable to run git: {exc}\n")
                command_status[filename] = -1
        metadata = {"path": str(root), "mode": "git", "captured_at": utc_now(), "commands": command_status}
        write_json(destination / "manifest.json", metadata)
        captured.append({"path": str(root), "status": "captured", "destination": str(destination), "commands": command_status})
    return captured


def disk_check(output_root: Path, bytes_to_copy: int, *, force: bool) -> None:
    if not bytes_to_copy:
        return
    try:
        free = shutil.disk_usage(output_root.parent).free
    except OSError:
        return
    # Raw artifacts are the dominant size. Reserve 20% for the normalized
    # text index and leave a minimum operational buffer for the host drive.
    needed = bytes_to_copy + max(512 * 1024**2, bytes_to_copy // 5)
    if not force and free < needed:
        raise RuntimeError(
            f"insufficient free space at {output_root.parent}: need about {needed:,} bytes, have {free:,}. "
            "Choose a larger output_root or rerun with --force."
        )


def collect(job: dict[str, Any], *, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    job = normalize_job(job)
    output_root = Path(job["output_root"])
    catalog_path = output_root / "index" / "raw_catalog.json"
    catalog = read_json(catalog_path, {"version": VERSION, "files": {}})
    if not isinstance(catalog, dict):
        catalog = {"version": VERSION, "files": {}}
    if not isinstance(catalog.get("files"), dict):
        catalog["files"] = {}
    discovered: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "started_at": utc_now(),
        "machine_id": job["machine_id"],
        "scanned": 0,
        "bytes_scanned": 0,
        "copied": 0,
        "unchanged": 0,
        "parsed": 0,
        "experimental": 0,
        "errors": [],
        "apps": {},
        "legacy_archives": {
            archive["id"]: {"path": archive["path"], "available": Path(str(archive["path"])).is_dir(), "files": 0, "bytes": 0}
            for archive in job["legacy_archives"]
        },
    }
    for app, status in APP_STATUS.items():
        if not app_is_enabled(job, app):
            continue
        if status == "tbd":
            report["apps"][app] = {"status": "tbd", "files": 0, "note": "registered but not collected until a parser is verified"}
            continue
        app_files = 0
        app_bytes = 0
        legacy_files = 0
        legacy_bytes = 0
        for external_archive in job["legacy_archives"]:
            archive_root = Path(str(external_archive["path"]))
            for source, machine_id in iter_legacy_source_files(app, external_archive):
                try:
                    stamp = source_stamp(source)
                except OSError:
                    continue
                archive = external_archive_relative(external_archive["id"], archive_root, source)
                discovered.append(
                    {
                        "app": app,
                        "source": source,
                        "stamp": stamp,
                        "archive": archive,
                        "machine_id": machine_id,
                        "external_archive": external_archive,
                    }
                )
                app_files += 1
                app_bytes += stamp["bytes"]
                legacy_files += 1
                legacy_bytes += stamp["bytes"]
                archive_report = report["legacy_archives"][external_archive["id"]]
                archive_report["files"] += 1
                archive_report["bytes"] += stamp["bytes"]
        for source in iter_source_files(app, job):
            try:
                stamp = source_stamp(source)
            except OSError:
                continue
            archive = archive_relative(source, job["machine_id"])
            discovered.append(
                {
                    "app": app,
                    "source": source,
                    "stamp": stamp,
                    "archive": archive,
                    "machine_id": job["machine_id"],
                    "external_archive": None,
                }
            )
            app_files += 1
            app_bytes += stamp["bytes"]
        report["apps"][app] = {
            "status": status,
            "files": app_files,
            "bytes": app_bytes,
            "legacy_files": legacy_files,
            "legacy_bytes": legacy_bytes,
        }
        report["scanned"] += app_files
        report["bytes_scanned"] += app_bytes

    pending_bytes = 0
    for artifact in discovered:
        if artifact["external_archive"] is not None:
            continue
        stamp = artifact["stamp"]
        archive = artifact["archive"]
        previous = catalog["files"].get(archive.as_posix(), {})
        if previous.get("bytes") != stamp["bytes"] or previous.get("mtime_ns") != stamp["mtime_ns"]:
            pending_bytes += stamp["bytes"]
    if dry_run:
        report["would_copy_bytes"] = pending_bytes if job["copy_raw"] else 0
        report["external_files_referenced"] = sum(1 for artifact in discovered if artifact["external_archive"] is not None)
        report["output_root"] = str(output_root)
        report["finished_at"] = utc_now()
        return report

    output_root.mkdir(parents=True, exist_ok=True)
    if job["copy_raw"]:
        disk_check(output_root, pending_bytes, force=force)

    for artifact in discovered:
        app = artifact["app"]
        source = artifact["source"]
        stamp = artifact["stamp"]
        archive = artifact["archive"]
        machine_id = artifact["machine_id"]
        external_archive = artifact["external_archive"]
        archive_key = archive.as_posix()
        previous = catalog["files"].get(archive_key, {})
        normalized_path = output_root / str(previous.get("session_path") or "")
        unchanged = (
            previous.get("bytes") == stamp["bytes"]
            and previous.get("mtime_ns") == stamp["mtime_ns"]
            and (not previous.get("session_path") or normalized_path.is_file())
        )
        try:
            target = output_root / archive
            if external_archive is None and job["copy_raw"] and (not target.is_file() or not unchanged):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                report["copied"] += 1
            elif unchanged:
                report["unchanged"] += 1

            entry = {
                "app": app,
                "archive_path": archive_key,
                "source_path": str(source),
                **stamp,
                "collected_at": utc_now(),
            }
            if external_archive is not None:
                entry.update(
                    {
                        "storage": "external",
                        "external_archive_id": external_archive["id"],
                        "external_archive_root": external_archive["path"],
                    }
                )
            else:
                entry["storage"] = "materialized" if job["copy_raw"] else "source-only"
            parser = PARSERS.get(app)
            if parser is None:
                report["experimental"] += 1
                entry["status"] = "raw-only"
            elif not unchanged:
                session = parser(source, machine_id, archive, stamp)
                if session:
                    session_path, _ = write_canonical_session(
                        output_root,
                        session,
                        external_archive=external_archive,
                    )
                    entry["session_path"] = str(session_path.relative_to(output_root)).replace("\\", "/")
                    entry["session_id"] = session["conv_id"]
                    entry["status"] = "normalized"
                    report["parsed"] += 1
                else:
                    entry["status"] = "no-natural-language-turns"
            else:
                entry["session_path"] = previous.get("session_path")
                entry["session_id"] = previous.get("session_id")
                entry["status"] = previous.get("status", "unchanged")
            catalog["files"][archive_key] = entry
        except Exception as exc:  # A corrupt live log must not abort a full collection.
            report["errors"].append({"source": str(source), "archive": archive_key, "error": str(exc)})

    catalog["version"] = VERSION
    catalog["machine_id"] = job["machine_id"]
    catalog["updated_at"] = utc_now()
    write_json(catalog_path, catalog)
    report["worktrees"] = capture_worktrees(job, output_root)
    report["finished_at"] = utc_now()
    manifest = write_views(job, report)
    report["view_counts"] = manifest["counts"]
    return report


def rebuild(job: dict[str, Any]) -> dict[str, Any]:
    job = normalize_job(job)
    report = {"recomputed_at": utc_now(), "source": "canonical index (no raw source copy)"}
    return write_views(job, report)


def apply_overrides(job: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    job = copy.deepcopy(job)
    if getattr(args, "mode", None):
        job["mode"] = args.mode
    if getattr(args, "assistant_output", None):
        job["assistant_output"] = args.assistant_output
    if getattr(args, "app", None):
        selected = set(args.app)
        job["apps"] = {name: name in selected for name in APP_STATUS}
    if getattr(args, "project", None):
        job["project_filters"] = args.project
    if getattr(args, "no_raw_copy", False):
        job["copy_raw"] = False
    return normalize_job(job)


def scheduler_command(job_path: Path) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), "collect", "--job", str(job_path)]


def install_schedule(job_path: Path, name: str, every_minutes: int, *, remove: bool, dry_run: bool) -> dict[str, Any]:
    if every_minutes < 1:
        raise ValueError("every_minutes must be at least 1")
    task_name = safe_name(name, "DataCollector-v2")
    if os.name == "nt":
        if remove:
            command = ["schtasks", "/Delete", "/TN", task_name, "/F"]
        else:
            task_command = subprocess.list2cmdline(scheduler_command(job_path))
            command = [
                "schtasks", "/Create", "/TN", task_name, "/SC", "MINUTE", "/MO", str(every_minutes),
                "/TR", task_command, "/F",
            ]
        if dry_run:
            return {"platform": "windows", "command": command, "dry_run": True}
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "schtasks failed")
        return {"platform": "windows", "task": task_name, "removed": remove, "output": result.stdout.strip()}

    launch_dir = Path.home() / "Library" / "LaunchAgents"
    plist_path = launch_dir / f"io.autoyou.datacollector.{task_name}.plist"
    uid = str(os.getuid())
    if remove:
        command = ["launchctl", "bootout", f"gui/{uid}", str(plist_path)]
        if dry_run:
            return {"platform": "macos", "command": command, "plist": str(plist_path), "dry_run": True}
        subprocess.run(command, capture_output=True, text=True, check=False)
        if plist_path.exists():
            plist_path.unlink()
        return {"platform": "macos", "task": task_name, "removed": True}
    plist = {
        "Label": f"io.autoyou.datacollector.{task_name}",
        "ProgramArguments": scheduler_command(job_path),
        "StartInterval": every_minutes * 60,
        "RunAtLoad": True,
        "StandardOutPath": str(job_path.parent / f"{task_name}.stdout.log"),
        "StandardErrorPath": str(job_path.parent / f"{task_name}.stderr.log"),
    }
    if dry_run:
        return {"platform": "macos", "plist": plist, "path": str(plist_path), "dry_run": True}
    launch_dir.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle)
    result = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "launchctl bootstrap failed")
    return {"platform": "macos", "task": task_name, "removed": False, "plist": str(plist_path)}


DASHBOARD = """<!doctype html>
<html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>DataCollector v2</title>
<style>
body{font:15px/1.45 Georgia,serif;max-width:860px;margin:40px auto;padding:0 18px;color:#1f2a26;background:#f5f2e8}
h1{font-size:34px;margin-bottom:4px} .sub{color:#53605b} fieldset{border:1px solid #aeb9ae;margin:20px 0;padding:14px;background:#fffdf8}
label{display:inline-block;margin:6px 16px 6px 0} select,button{font:inherit;padding:7px 10px} button{background:#294e45;color:#fff;border:0;cursor:pointer;margin-right:8px}
button.alt{background:#56665e} pre{background:#1f2a26;color:#e9f2e8;padding:14px;overflow:auto;white-space:pre-wrap} small{color:#68736c}
</style>
<body><h1>DataCollector v2</h1><p class=\"sub\">Local, materialized archive with a recomputable training view.</p>
<fieldset><legend>Training view</legend>
<label>Mode <select id=\"mode\"><option value=\"input_output\">Input + output</option><option value=\"input_only\">Input only</option><option value=\"output_only\">Output only</option></select></label>
<label>Assistant output <select id=\"assistant_output\"><option value=\"all\">All visible responses</option><option value=\"final\">Final response per prompt</option></select></label><br>
<label><input type=\"checkbox\" data-app=\"chatgpt\"> ChatGPT exports <small>supported</small></label>
<label><input type=\"checkbox\" data-app=\"codex\"> Codex <small>supported</small></label>
<label><input type=\"checkbox\" data-app=\"claude\"> Claude <small>supported</small></label>
<label><input type=\"checkbox\" data-app=\"antigravity\"> Antigravity <small>experimental</small></label>
<label><input type=\"checkbox\" data-app=\"trae\"> Trae <small>experimental raw history</small></label>
<p><button onclick=\"run('collect')\">Collect now</button><button class=\"alt\" onclick=\"run('rebuild')\">Recompute view only</button></p></fieldset>
<pre id=\"status\">Loading…</pre>
<script>
const status=document.querySelector('#status');
async function refresh(){const [job,state]=await Promise.all([fetch('/api/job').then(r=>r.json()),fetch('/api/status').then(r=>r.json())]);
 document.querySelector('#mode').value=job.mode;document.querySelector('#assistant_output').value=job.assistant_output;
 document.querySelectorAll('[data-app]').forEach(x=>x.checked=!!job.apps[x.dataset.app]);status.textContent=JSON.stringify(state,null,2)}
async function run(action){const apps={};document.querySelectorAll('[data-app]').forEach(x=>apps[x.dataset.app]=x.checked);
 const body={mode:document.querySelector('#mode').value,assistant_output:document.querySelector('#assistant_output').value,apps};
 const res=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});status.textContent=JSON.stringify(await res.json(),null,2);setTimeout(refresh,500)}
refresh();setInterval(refresh,3000);
</script></body></html>"""


class DashboardState:
    def __init__(self, job_path: Path):
        self.job_path = job_path
        self.lock = threading.Lock()
        self.status: dict[str, Any] = {"state": "idle"}

    def current_job(self) -> dict[str, Any]:
        return load_job(self.job_path)

    def begin(self, action: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.status.get("state") == "running":
                return {"state": "running", "accepted": False}
            job = _deep_merge(self.current_job(), patch)
            job = normalize_job(job)
            save_job(self.job_path, job)
            self.status = {"state": "running", "action": action, "started_at": utc_now()}
        thread = threading.Thread(target=self._run, args=(action, job), daemon=True)
        thread.start()
        return {"state": "running", "accepted": True}

    def _run(self, action: str, job: dict[str, Any]) -> None:
        try:
            result = collect(job) if action == "collect" else rebuild(job)
            state = {"state": "complete", "action": action, "finished_at": utc_now(), "result": result}
        except Exception as exc:
            state = {"state": "error", "action": action, "finished_at": utc_now(), "error": str(exc)}
        with self.lock:
            self.status = state

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            status = copy.deepcopy(self.status)
        manifest = Path(self.current_job()["output_root"]) / "index_unified" / "manifest.json"
        if manifest.is_file():
            status["manifest"] = read_json(manifest, {})
        return status


def serve(job_path: Path, port: int) -> None:
    state = DashboardState(job_path)

    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: Any, status_code: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                data = DASHBOARD.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/api/job":
                self._json(state.current_job())
            elif self.path == "/api/status":
                self._json(state.snapshot())
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in ("/api/collect", "/api/rebuild"):
                self._json({"error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except (ValueError, UnicodeError, json.JSONDecodeError):
                self._json({"error": "invalid JSON"}, 400)
                return
            if not isinstance(payload, dict):
                self._json({"error": "JSON object required"}, 400)
                return
            allowed = {key: payload[key] for key in ("mode", "assistant_output", "apps") if key in payload}
            self._json(state.begin("collect" if self.path.endswith("collect") else "rebuild", allowed), 202)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"DataCollector v2 dashboard: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, default=_agent_runtime_root() / "collection-job-legacy.json", help="collection job JSON")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="write the default collection job")
    for name, description in (("collect", "copy changed source logs and rebuild the view"), ("rebuild", "recompute the selected view without copying sources")):
        command = sub.add_parser(name, help=description)
        command.add_argument("--mode", choices=SUPPORTED_MODES)
        command.add_argument("--assistant-output", choices=OUTPUT_SCOPES)
        command.add_argument("--app", action="append", choices=tuple(APP_STATUS), help="include only this application; repeatable")
        command.add_argument("--project", action="append", help="include sessions whose cwd/path/title matches this value; repeatable")
        if name == "collect":
            command.add_argument("--no-raw-copy", action="store_true", help="index live sources without archiving them")
            command.add_argument("--dry-run", action="store_true", help="report source size and do not write")
            command.add_argument("--force", action="store_true", help="continue even when the disk-space preflight fails")
    scheduler = sub.add_parser("schedule", help="install a recurring local collection task")
    scheduler.add_argument("--name", default="DataCollector-v2")
    scheduler.add_argument("--every-minutes", type=int, default=60)
    scheduler.add_argument("--remove", action="store_true")
    scheduler.add_argument("--dry-run", action="store_true")
    dashboard = sub.add_parser("serve", help="start the localhost collection dashboard")
    dashboard.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    job_path = args.job.resolve()
    if args.command == "init":
        if job_path.exists():
            print(json.dumps({"status": "exists", "job": str(job_path)}, indent=2))
            return 0
        save_job(job_path, default_job(_agent_runtime_root()))
        print(json.dumps({"status": "created", "job": str(job_path)}, indent=2))
        return 0
    job = load_job(job_path)
    if args.command == "serve":
        if not job_path.exists():
            save_job(job_path, job)
        serve(job_path, args.port)
        return 0
    if args.command == "schedule":
        if not job_path.exists():
            save_job(job_path, job)
        print(json.dumps(install_schedule(job_path, args.name, args.every_minutes, remove=args.remove, dry_run=args.dry_run), indent=2))
        return 0
    job = apply_overrides(job, args)
    if not job_path.exists():
        save_job(job_path, job)
    if args.command == "collect":
        result = collect(job, dry_run=args.dry_run, force=args.force)
    else:
        result = rebuild(job)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
