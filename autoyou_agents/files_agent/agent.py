# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-232c161409db8cdea303c8e3

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from autoyou_agents.admin_agent.agent import (
    _check_admin_session,
    _extract_totp_reply_code,
    _get_invocation_id,
    _state_get,
    _state_set,
    check_admin_session,
    revoke_admin_session,
    verify_admin_totp,
)
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime, inject_realtime_datetime_into_request
from shared.session_execution import create_text_llm_response, create_tool_call_llm_response

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-232c161409db8cdea303c8e3"


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_FILES_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY = "_autoyou_files_tool_dispatch_invocation_id"
_FILES_TOOL_RESULT_INVOCATION_ID_STATE_KEY = "_autoyou_files_tool_result_invocation_id"
_FILES_TOOL_RESULT_MESSAGE_STATE_KEY = "_autoyou_files_tool_result_message"
_FILES_TOTP_PENDING_STATE_KEY = "user:files_admin_totp_pending"
_FILES_PENDING_OP_STATE_KEY = "_autoyou_files_pending_op"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FILE_REQUEST_PATTERN = re.compile(
    r"\b(?:inspect|list|show|open|rename|move|copy|delete|remove|create)\b.*\b(?:file|folder|directory|path|filesystem|local|song|track|music|document)\b",
    re.IGNORECASE,
)
_PATH_HINT_PATTERN = re.compile(r"(?:^|\s)(?:~?/|/[\w./ -]+|[A-Za-z]:\\[^\s]+)")
_CONFIRMATION_PATTERN = re.compile(
    r"^(?:yes|confirm(?:ed)?|ok(?:ay)?|sure|proceed|do\s+it|go\s+ahead|affirmative|yep|yup|yep|copy\s+it|move\s+it|done|enable[d]?)\b",
    re.IGNORECASE,
)

# Maps verb keywords to (tool_name, arg_names) for explicit path operation parsing.
# Two-path ops: (tool_name, src_key, dst_key); single-path ops: (tool_name, path_key, None)
_EXPLICIT_OP_MAP: Dict[str, tuple] = {
    "copy": ("copy_path", "source_path", "destination_path"),
    "move": ("move_path", "source_path", "destination_path"),
    "rename": ("rename_path", "path", "new_name"),
    "delete": ("delete_path", "path", None),
    "remove": ("delete_path", "path", None),
    "inspect": ("inspect_path", "path", None),
    "list": ("list_directory", "path", None),
    "show": ("inspect_path", "path", None),
}

def _looks_like_absolute_path(s: str) -> bool:
    """Return True if s looks like an absolute filesystem path (OS-agnostic)."""
    s = s.strip()
    if not s:
        return False
    # Unix / macOS
    if s.startswith("/") or s.startswith("~/"):
        return True
    # Windows drive letter (e.g. C:\ or C:/)
    if len(s) >= 3 and s[1] == ":" and s[2] in ("\\/"):
        return True
    return False

def _try_parse_file_op(user_text: str) -> Optional[tuple]:
    """
    Try to parse an explicit file operation from user text.

    Returns (tool_name, args_dict) if a recognisable operation with at least one
    absolute path can be extracted, otherwise None.  Works for Unix, macOS, and
    Windows paths and does not rely on OS-specific separators.
    """
    text = user_text.strip()
    lower = text.lower()

    for verb, (tool_name, key1, key2) in _EXPLICIT_OP_MAP.items():
        if not lower.startswith(verb + " ") and not lower.startswith(verb + "\t"):
            continue

        rest = text[len(verb):].strip()

        if key2 is None:
            # Single-path operation: everything after the verb is the path.
            path_val = rest.strip()
            if not _looks_like_absolute_path(path_val):
                return None
            return (tool_name, {key1: path_val})
        else:
            # Two-path operation: split on the LAST occurrence of " to ".
            # Using rfind to handle paths that contain " to " in their names.
            sep = " to "
            idx = rest.rfind(sep)
            if idx == -1:
                # Try whitespace-only separator for rename "rename /a/b newname"
                if tool_name == "rename_path":
                    parts = rest.rsplit(None, 1)
                    if len(parts) == 2 and _looks_like_absolute_path(parts[0]):
                        return (tool_name, {key1: parts[0], key2: parts[1]})
                return None
            src = rest[:idx].strip()
            dst = rest[idx + len(sep):].strip()
            if not _looks_like_absolute_path(src):
                return None
            return (tool_name, {key1: src, key2: dst})

    return None

def _extract_text_from_llm_request(llm_request: Any) -> str:
    for content in reversed(getattr(llm_request, "contents", []) or []):
        if str(getattr(content, "role", "") or "").strip().lower() not in {"", "user"}:
            continue
        parts: List[str] = []
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return " ".join(parts).strip()
    return ""

def _tool_dispatch_already_happened(state: Any, invocation_id: str) -> bool:
    if not invocation_id:
        return False
    return str(_state_get(state, _FILES_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY) or "").strip() == invocation_id

def _mark_tool_dispatch(state: Any, invocation_id: str) -> None:
    if not invocation_id:
        return
    _state_set(state, _FILES_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY, invocation_id)
    _state_set(state, _FILES_TOOL_RESULT_INVOCATION_ID_STATE_KEY, "")
    _state_set(state, _FILES_TOOL_RESULT_MESSAGE_STATE_KEY, "")

def _render_files_tool_response(tool_name: str, tool_response: Dict[str, Any]) -> str:
    message = str(tool_response.get("message") or "").strip()
    if message:
        return message

    if tool_name == "check_admin_session" and bool(tool_response.get("active")):
        minutes_remaining = int(tool_response.get("minutes_remaining") or 0)
        seconds_remaining = int(tool_response.get("seconds_remaining") or 0)
        return f"Admin session is active. {minutes_remaining} min {seconds_remaining % 60} sec remaining."

    status = str(tool_response.get("status") or "").strip().lower()
    if status == "success":
        return "File operation completed successfully."
    return "File operation failed."

def _record_files_tool_result(state: Any, invocation_id: str, tool_name: str, tool_response: Dict[str, Any]) -> None:
    if not invocation_id:
        return
    _state_set(state, _FILES_TOOL_RESULT_INVOCATION_ID_STATE_KEY, invocation_id)
    _state_set(state, _FILES_TOOL_RESULT_MESSAGE_STATE_KEY, _render_files_tool_response(tool_name, tool_response))

def _looks_like_file_request(user_text: str) -> bool:
    normalized = " ".join(str(user_text or "").lower().split())
    if not normalized:
        return False
    if "files agent" in normalized or "filesystem" in normalized:
        return True
    if _FILE_REQUEST_PATTERN.search(user_text):
        return True
    return bool(_PATH_HINT_PATTERN.search(user_text) and re.search(r"\b(?:rename|move|copy|delete|remove|inspect|list|show|create)\b", user_text, re.IGNORECASE))

async def _files_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)
    user_text = _extract_text_from_llm_request(llm_request)
    if not user_text:
        return None

    invocation_id = _get_invocation_id(callback_context)
    result_invocation_id = str(_state_get(callback_context, _FILES_TOOL_RESULT_INVOCATION_ID_STATE_KEY) or "").strip()
    if invocation_id and result_invocation_id == invocation_id:
        message = str(_state_get(callback_context, _FILES_TOOL_RESULT_MESSAGE_STATE_KEY) or "").strip()
        _state_set(callback_context, _FILES_TOOL_RESULT_INVOCATION_ID_STATE_KEY, "")
        _state_set(callback_context, _FILES_TOOL_RESULT_MESSAGE_STATE_KEY, "")
        # Clear the pending op that was just executed.
        _state_set(callback_context, _FILES_PENDING_OP_STATE_KEY, None)
        if message:
            return create_text_llm_response(message, custom_metadata={"response_author": AGENT_NAME})

    if _tool_dispatch_already_happened(callback_context, invocation_id):
        return None

    totp_code = _extract_totp_reply_code(user_text)
    if totp_code and (bool(_state_get(callback_context, _FILES_TOTP_PENDING_STATE_KEY)) or not _check_admin_session(callback_context)):
        _mark_tool_dispatch(callback_context, invocation_id)
        return create_tool_call_llm_response("verify_admin_totp", {"totp_code": totp_code})

    admin_active = _check_admin_session(callback_context)

    # --- Explicit operation dispatch (bypasses unreliable small-model tool calling) ---
    # Try to parse a deterministic file operation from the user message.
    parsed_op = _try_parse_file_op(user_text)

    if parsed_op is not None:
        tool_name, tool_args = parsed_op
        if admin_active:
            # Admin is live - dispatch the real file tool call right now.
            _mark_tool_dispatch(callback_context, invocation_id)
            return create_tool_call_llm_response(tool_name, tool_args)
        else:
            # Admin not yet verified - store the pending op and trigger auth.
            _state_set(callback_context, _FILES_PENDING_OP_STATE_KEY, {"tool": tool_name, "args": tool_args})
            _mark_tool_dispatch(callback_context, invocation_id)
            return create_tool_call_llm_response("check_admin_session", {})

    # --- Confirmation dispatch: user is confirming a previously proposed operation ---
    if admin_active and _CONFIRMATION_PATTERN.match(user_text.strip()):
        pending_raw = _state_get(callback_context, _FILES_PENDING_OP_STATE_KEY)
        if pending_raw and isinstance(pending_raw, dict):
            pending_tool = str(pending_raw.get("tool") or "").strip()
            pending_args = pending_raw.get("args") or {}
            if pending_tool and isinstance(pending_args, dict):
                _mark_tool_dispatch(callback_context, invocation_id)
                return create_tool_call_llm_response(pending_tool, pending_args)

    # --- Fallback: check admin session when a generic file request is detected ---
    if _looks_like_file_request(user_text) and not admin_active:
        _mark_tool_dispatch(callback_context, invocation_id)
        return create_tool_call_llm_response("check_admin_session", {})

    return None

def _files_after_tool_callback(tool: Any, args: Dict[str, Any], tool_context: Any, tool_response: Any) -> Any:
    del args
    tool_name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "").strip()
    if not tool_name or not isinstance(tool_response, dict):
        return None

    if tool_name not in {
        "check_admin_session",
        "verify_admin_totp",
        "revoke_admin_session",
        "inspect_path",
        "list_directory",
        "rename_path",
        "move_path",
        "copy_path",
        "create_directory",
        "delete_path",
    }:
        return None

    if tool_name == "check_admin_session":
        _state_set(tool_context, _FILES_TOTP_PENDING_STATE_KEY, not bool(tool_response.get("active")))
    elif tool_name == "verify_admin_totp":
        _state_set(tool_context, _FILES_TOTP_PENDING_STATE_KEY, not bool(tool_response.get("valid") or tool_response.get("active")))
    elif tool_name == "revoke_admin_session":
        _state_set(tool_context, _FILES_TOTP_PENDING_STATE_KEY, False)
        _state_set(tool_context, _FILES_PENDING_OP_STATE_KEY, None)
    elif "admin session required" in str(tool_response.get("message") or "").lower():
        _state_set(tool_context, _FILES_TOTP_PENDING_STATE_KEY, True)
    else:
        # Any completed file operation clears the stored pending op.
        _state_set(tool_context, _FILES_PENDING_OP_STATE_KEY, None)

    _record_files_tool_result(tool_context, _get_invocation_id(tool_context), tool_name, tool_response)
    return None

def _normalize_path(path_value: str) -> Path:
    raw_path = str(path_value or "").strip()
    if not raw_path:
        raise ValueError("A path is required.")
    return Path(raw_path).expanduser().resolve(strict=False)

def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path.resolve(strict=False))))

def _protected_path_reason(path: Path) -> Optional[str]:
    resolved = path.resolve(strict=False)
    if _path_key(resolved) == _path_key(Path(resolved.anchor)):
        return "filesystem root"
    if _path_key(resolved) == _path_key(Path.home()):
        return "home directory root"
    if _path_key(resolved) == _path_key(_REPO_ROOT):
        return "AutoYou repo root"
    return None

def _require_admin_session(tool_context: Optional[Any]) -> Optional[Dict[str, Any]]:
    if _check_admin_session(tool_context):
        return None
    return {
        "status": "error",
        "message": "Admin session required for local filesystem access. Call check_admin_session or verify_admin_totp first.",
    }

def _source_path_or_error(path_value: str) -> Path:
    source = _normalize_path(path_value)
    if not source.exists():
        raise FileNotFoundError(f"Path not found: {source}")
    return source

def _remove_existing_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink()

def _resolve_destination_path(source: Path, destination_path: str) -> Path:
    raw_destination = str(destination_path or "").strip()
    # from __debug_provenance_x__ import email
    destination = _normalize_path(raw_destination)
    if destination.exists() and destination.is_dir():
        return destination / source.name
    if raw_destination.endswith((os.sep, "/")):
        return destination / source.name
    return destination

def _validate_new_name(new_name: str) -> str:
    candidate = str(new_name or "").strip()
    if not candidate:
        raise ValueError("A new_name value is required.")
    if any(sep and sep in candidate for sep in (os.sep, "/")) or candidate in {".", ".."}:
        raise ValueError("new_name must be a single file or directory name, not a path.")
    return candidate

def inspect_path(path: str, tool_context: Optional[Any] = None) -> Dict[str, Any]:
    """Inspect a local file or directory path."""
    auth_error = _require_admin_session(tool_context)
    if auth_error:
        return auth_error

    try:
        target = _source_path_or_error(path)
        kind = "directory" if target.is_dir() else "symlink" if target.is_symlink() else "file"
        entry_count = None
        if target.is_dir():
            entry_count = sum(1 for _ in target.iterdir())
        size_bytes = target.stat().st_size if not target.is_dir() else None
        return {
            "status": "success",
            "path": str(target),
            "name": target.name,
            "kind": kind,
            "exists": True,
            "parent": str(target.parent),
            "entry_count": entry_count,
            "size_bytes": size_bytes,
            "message": f"Inspected {kind} at {target}.",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to inspect path: {exc}"}

def list_directory(
    path: str,
    recursive: bool = False,
    limit: int = 100,
    include_hidden: bool = False,
    tool_context: Optional[Any] = None,
) -> Dict[str, Any]:
    """List directory contents, optionally recursively."""
    auth_error = _require_admin_session(tool_context)
    if auth_error:
        return auth_error

    try:
        directory = _source_path_or_error(path)
        if not directory.is_dir():
            return {"status": "error", "message": f"Path is not a directory: {directory}"}

        resolved_limit = max(1, min(int(limit), 500))
        entries: List[Dict[str, Any]] = []
        iterator = directory.rglob("*") if recursive else directory.iterdir()
        for entry in iterator:
            if not include_hidden and entry.name.startswith("."):
                continue
            entries.append(
                {
                    "name": entry.name,
                    "path": str(entry.resolve(strict=False)),
                    "kind": "directory" if entry.is_dir() else "symlink" if entry.is_symlink() else "file",
                }
            )
            if len(entries) >= resolved_limit:
                break

        entries.sort(key=lambda item: (item["kind"], item["name"].lower()))
        truncated = len(entries) >= resolved_limit
        suffix = " (truncated)" if truncated else ""
        return {
            "status": "success",
            "path": str(directory),
            "count": len(entries),
            "recursive": bool(recursive),
            "entries": entries,
            "message": f"Listed {len(entries)} entries in {directory}{suffix}.",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to list directory: {exc}"}

def rename_path(path: str, new_name: str, overwrite: bool = False, tool_context: Optional[Any] = None) -> Dict[str, Any]:
    """Rename a file or directory within its current parent directory."""
    auth_error = _require_admin_session(tool_context)
    if auth_error:
        return auth_error

    try:
        source = _source_path_or_error(path)
        protected_reason = _protected_path_reason(source)
        if protected_reason:
            return {"status": "error", "message": f"Refusing to rename the {protected_reason}: {source}"}

        candidate_name = _validate_new_name(new_name)
        destination = source.with_name(candidate_name)
        if destination.exists():
            if not overwrite:
                return {"status": "error", "message": f"Destination already exists: {destination}"}
            if _protected_path_reason(destination):
                return {"status": "error", "message": f"Refusing to overwrite protected path: {destination}"}
            _remove_existing_path(destination)

        source.rename(destination)
        return {
            "status": "success",
            "source_path": str(source),
            "destination_path": str(destination),
            "message": f"Renamed {source} to {destination}.",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to rename path: {exc}"}

def move_path(
    source_path: str,
    destination_path: str,
    overwrite: bool = False,
    create_parent: bool = False,
    tool_context: Optional[Any] = None,
) -> Dict[str, Any]:
    """Move a file or directory to a new location."""
    auth_error = _require_admin_session(tool_context)
    if auth_error:
        return auth_error

    try:
        source = _source_path_or_error(source_path)
        protected_reason = _protected_path_reason(source)
        if protected_reason:
            return {"status": "error", "message": f"Refusing to move the {protected_reason}: {source}"}

        destination = _resolve_destination_path(source, destination_path)
        if _protected_path_reason(destination):
            return {"status": "error", "message": f"Refusing to overwrite protected path: {destination}"}
        if create_parent:
            destination.parent.mkdir(parents=True, exist_ok=True)
        elif not destination.parent.exists():
            return {"status": "error", "message": f"Destination parent does not exist: {destination.parent}"}
        if destination.exists():
            if not overwrite:
                return {"status": "error", "message": f"Destination already exists: {destination}"}
            _remove_existing_path(destination)

        final_path = Path(shutil.move(str(source), str(destination))).resolve(strict=False)
        return {
            "status": "success",
            "source_path": str(source),
            "destination_path": str(final_path),
            "message": f"Moved {source} to {final_path}.",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to move path: {exc}"}

def copy_path(
    source_path: str,
    destination_path: str,
    overwrite: bool = False,
    create_parent: bool = False,
    tool_context: Optional[Any] = None,
) -> Dict[str, Any]:
    """Copy a file or directory to a new location."""
    auth_error = _require_admin_session(tool_context)
    if auth_error:
        return auth_error

    try:
        source = _source_path_or_error(source_path)
        protected_reason = _protected_path_reason(source)
        if protected_reason:
            return {"status": "error", "message": f"Refusing to copy the {protected_reason}: {source}"}

        destination = _resolve_destination_path(source, destination_path)
        if _protected_path_reason(destination):
            return {"status": "error", "message": f"Refusing to overwrite protected path: {destination}"}
        if create_parent:
            destination.parent.mkdir(parents=True, exist_ok=True)
        elif not destination.parent.exists():
            return {"status": "error", "message": f"Destination parent does not exist: {destination.parent}"}
        if destination.exists():
            if not overwrite:
                return {"status": "error", "message": f"Destination already exists: {destination}"}
            _remove_existing_path(destination)

        if source.is_dir() and not source.is_symlink():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)

        return {
            "status": "success",
            "source_path": str(source),
            "destination_path": str(destination),
            "message": f"Copied {source} to {destination}.",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to copy path: {exc}"}

def create_directory(path: str, exist_ok: bool = True, tool_context: Optional[Any] = None) -> Dict[str, Any]:
    """Create a local directory."""
    auth_error = _require_admin_session(tool_context)
    if auth_error:
        return auth_error

    try:
        directory = _normalize_path(path)
        protected_reason = _protected_path_reason(directory)
        if protected_reason:
            return {"status": "error", "message": f"Refusing to create or replace the {protected_reason}: {directory}"}
        directory.mkdir(parents=True, exist_ok=bool(exist_ok))
        return {
            "status": "success",
            "path": str(directory),
            "message": f"Directory ready at {directory}.",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to create directory: {exc}"}

def delete_path(path: str, recursive: bool = False, tool_context: Optional[Any] = None) -> Dict[str, Any]:
    """Delete a local file or directory."""
    auth_error = _require_admin_session(tool_context)
    if auth_error:
        return auth_error

    try:
        target = _source_path_or_error(path)
        protected_reason = _protected_path_reason(target)
        if protected_reason:
            return {"status": "error", "message": f"Refusing to delete the {protected_reason}: {target}"}

        if target.is_dir() and not target.is_symlink():
            if recursive:
                shutil.rmtree(target)
            else:
                target.rmdir()
            kind = "directory"
        else:
            target.unlink()
            kind = "file"

        return {
            "status": "success",
            "path": str(target),
            "message": f"Deleted {kind} {target}.",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to delete path: {exc}"}

def create_files_agent(model_config: Any) -> Agent:
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=[_files_before_model_callback],
        after_tool_callback=[_files_after_tool_callback],
        tools=[
            check_admin_session,
            verify_admin_totp,
            revoke_admin_session,
            inspect_path,
            list_directory,
            rename_path,
            move_path,
            copy_path,
            create_directory,
            delete_path,
            get_current_datetime,
        ],
    )
