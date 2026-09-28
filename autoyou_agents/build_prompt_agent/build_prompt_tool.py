# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Small, deterministic prompt-builder tool set shared by the agent and Telegram.

The builder owns only the draft bookkeeping. Text is passed to the selected
desktop bridge as received, and Telegram media is materialized as a temporary
local file solely because the native desktop attachment controls require paths.
"""

from __future__ import annotations

import base64
import binascii
import importlib
import mimetypes
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shared.platform_runtime import get_service_data_dir


SUPPORTED_APPLICATION_AGENTS = ("codex_desktop_agent", "claude_desktop_agent")
DEFAULT_APPLICATION_AGENT = "codex_desktop_agent"
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
_IMAGE_EXTENSIONS = {".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".webp"}

_TARGET_MODULES = {
    "codex_desktop_agent": "autoyou_agents.codex_desktop_agent.agent",
    "claude_desktop_agent": "autoyou_agents.claude_desktop_agent.agent",
}
_TARGET_LABELS = {
    "codex_desktop_agent": "Codex Desktop",
    "claude_desktop_agent": "Claude Desktop",
}


@dataclass
class _PromptState:
    prompt_text: str = ""
    image_count: int = 0
    attachment_count: int = 0
    status: str = "empty"
    submitted_prompt: str = ""
    submitted: bool = False
    result_text: str = ""
    last_error: str = ""
    updated_at: float = 0.0
    # Separate from `status`: execute_prompt's own submission-in-flight guard.
    # `status` gets overwritten mid-flight by any get_prompt() call (it mirrors
    # whatever the desktop adapter currently reports, e.g. "draft"), so it can't
    # be trusted alone to keep two overlapping execute_prompt calls from both
    # reaching send_current_*_prompt.
    submitting: bool = False


_LOCK = threading.RLock()
_STATES: Dict[str, _PromptState] = {}


def normalize_application_agent(value: Optional[str]) -> str:
    raw = str(value or DEFAULT_APPLICATION_AGENT).strip().lower().replace("-", "_")
    aliases = {
        "codex": "codex_desktop_agent",
        "codex_desktop": "codex_desktop_agent",
        "claude": "claude_desktop_agent",
        "claude_desktop": "claude_desktop_agent",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in SUPPORTED_APPLICATION_AGENTS:
        raise ValueError(
            "application_agent must be codex_desktop_agent or claude_desktop_agent."
        )
    return normalized


def _state_for(application_agent: Optional[str]) -> Tuple[str, _PromptState]:
    target = normalize_application_agent(application_agent)
    with _LOCK:
        return target, _STATES.setdefault(target, _PromptState())


def _touch(state: _PromptState) -> None:
    state.updated_at = time.time()


def _metrics(state: _PromptState) -> Dict[str, Any]:
    words = len(state.prompt_text.split())
    return {
        "text": state.prompt_text,
        "characters": len(state.prompt_text),
        "total_characters": len(state.prompt_text),
        "words": words,
        "tokens": words,
        "images": state.image_count,
        "image_count": state.image_count,
        "attachments": state.attachment_count,
    }


def _payload(target: str, state: _PromptState) -> Dict[str, Any]:
    return {
        "success": True,
        "application_agent": target,
        "application_label": _TARGET_LABELS[target],
        "status": state.status,
        "processing": state.status == "processing",
        "updated_at": state.updated_at or None,
        **_metrics(state),
    }


def _live_prompt_payload(
    target: str,
    state: _PromptState,
    result: Dict[str, Any],
    *,
    fallback_status: str = "empty",
) -> Dict[str, Any]:
    """Build a response from a desktop adapter's live composer inspection."""
    raw_text = result.get("prompt_text")
    if raw_text is None:
        raw_text = result.get("text")
    prompt_text = str(raw_text or "")

    def _nonnegative_int(value: Any, fallback: int = 0) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return fallback

    image_count = _nonnegative_int(result.get("images", result.get("image_count", 0)))
    attachment_count = _nonnegative_int(result.get("attachments", result.get("attachment_count", image_count)), image_count)
    prompt_status = str(result.get("prompt_status") or fallback_status).strip().lower()
    if prompt_status in {"", "success", "error"}:
        prompt_status = fallback_status
    processing = bool(result.get("processing")) or prompt_status == "processing"

    with _LOCK:
        state.prompt_text = prompt_text
        state.image_count = image_count
        state.attachment_count = attachment_count
        state.status = prompt_status
        state.last_error = ""
        _touch(state)
        response = _payload(target, state)
    response["processing"] = processing
    response["desktop_action"] = result
    return response


def _target_module(application_agent: str) -> Any:
    return importlib.import_module(_TARGET_MODULES[application_agent])


def _call_target(application_agent: str, function_name: str, **kwargs: Any) -> Dict[str, Any]:
    module = _target_module(application_agent)
    function = getattr(module, function_name, None)
    if not callable(function):
        return {
            "status": "error",
            "message": f"{function_name} is not available for {application_agent}.",
        }
    result = function(**kwargs)
    return result if isinstance(result, dict) else {"status": "success", "result": result}


def _is_image_attachment(item: Any, *, filename: str = "", mimetype: str = "") -> bool:
    kind = str(item.get("kind") or "").strip().lower() if isinstance(item, dict) else ""
    return kind == "image" or mimetype.lower().split(";", 1)[0].startswith("image/") or Path(filename).suffix.lower() in _IMAGE_EXTENSIONS


def _safe_filename(value: Any, fallback: str) -> str:
    candidate = Path(str(value or "")).name
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._")
    return candidate[:120] or fallback


def _decode_attachment_data(value: Any) -> bytes:
    raw = str(value or "")
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        decoded = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("attachment data is not valid base64") from exc
    if not decoded or len(decoded) > MAX_ATTACHMENT_BYTES:
        raise ValueError("attachment exceeds the 25 MB limit or is empty")
    return decoded


def _materialize_attachments(attachments: Optional[Sequence[Any]]) -> Tuple[List[str], int, int, List[Path]]:
    paths: List[str] = []
    image_count = 0
    attachment_count = 0
    temporary_paths: List[Path] = []
    if not attachments:
        return paths, image_count, attachment_count, temporary_paths

    attachment_dir = get_service_data_dir("build_prompt_agent/attachments", anchor=__file__)
    for index, item in enumerate(attachments):
        if isinstance(item, str):
            path = Path(item).expanduser()
            filename = path.name
            mimetype = mimetypes.guess_type(filename)[0] or ""
            if not path.is_file():
                raise ValueError(f"attachment path does not exist: {path}")
            paths.append(str(path))
        elif isinstance(item, dict):
            filename = _safe_filename(item.get("filename"), f"attachment_{index}")
            mimetype = str(item.get("mimetype") or item.get("mime_type") or "")
            raw_path = str(item.get("path") or "").strip()
            if raw_path:
                path = Path(raw_path).expanduser()
                if not path.is_file():
                    raise ValueError(f"attachment path does not exist: {path}")
                paths.append(str(path))
            elif item.get("data"):
                suffix = Path(filename).suffix
                if not suffix and mimetype:
                    suffix = mimetypes.guess_extension(mimetype.split(";", 1)[0].strip()) or ""
                path = attachment_dir / f"{int(time.time() * 1000000)}_{index}_{filename}{suffix if suffix and not filename.endswith(suffix) else ''}"
                path.write_bytes(_decode_attachment_data(item.get("data")))
                paths.append(str(path))
                temporary_paths.append(path)
            else:
                raise ValueError("attachment must contain path or base64 data")
        else:
            raise ValueError("attachments must be strings or objects")

        attachment_count += 1
        if _is_image_attachment(item, filename=filename, mimetype=mimetype):
            image_count += 1
    return paths, image_count, attachment_count, temporary_paths


def _is_success(result: Dict[str, Any]) -> bool:
    return str(result.get("status") or "").lower() in {"success", "partial_success"}


def build_prompt(
    text: str = "",
    *,
    prompt: Optional[str] = None,
    attachments: Optional[Sequence[Any]] = None,
    application_agent: Optional[str] = None,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Append exact text and attachments to the selected unsent desktop prompt."""
    target, state = _state_for(application_agent)
    raw_text = str(prompt if prompt is not None else text)
    with _LOCK:
        had_content = bool(state.prompt_text or state.attachment_count)

    temporary_paths: List[Path] = []
    try:
        paths, image_count, attachment_count, temporary_paths = _materialize_attachments(attachments)
        result: Dict[str, Any]
        if raw_text:
            result = _call_target(
                target,
                f"add_to_{target.removesuffix('_agent')}_prompt",
                prompt=raw_text,
                attachment_paths=paths or None,
                prepend_newline=had_content,
                launch_if_needed=launch_if_needed,
                preserve_text=True,
            )
        elif paths:
            result = _call_target(
                target,
                f"add_{target.removesuffix('_agent')}_attachments",
                attachment_paths=paths,
                launch_if_needed=launch_if_needed,
            )
        else:
            result = {"status": "success", "message": "Nothing to append."}

        if not _is_success(result):
            with _LOCK:
                state.last_error = str(result.get("message") or "Desktop prompt append failed")
                state.status = "error"
                _touch(state)
            return {"success": False, "application_agent": target, **result}

        with _LOCK:
            if raw_text:
                state.prompt_text += ("\n" if had_content else "") + raw_text
            state.image_count += image_count
            state.attachment_count += attachment_count
            state.status = "draft"
            state.last_error = ""
            _touch(state)
            response = _payload(target, state)
        response["desktop_action"] = result
        return response
    except Exception as exc:
        with _LOCK:
            state.last_error = str(exc)
            state.status = "error"
            _touch(state)
        return {"success": False, "application_agent": target, "status": "error", "message": str(exc)}
    finally:
        for path in temporary_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def get_prompt(
    application_agent: Optional[str] = None,
    *,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Select/copy the live desktop composer and return exact prompt metrics."""
    target, state = _state_for(application_agent)
    prefix = target.removesuffix("_agent")
    try:
        result = _call_target(
            target,
            f"get_{prefix}_prompt",
            launch_if_needed=launch_if_needed,
        )
    except Exception as exc:
        result = {"status": "error", "message": str(exc)}
    if not _is_success(result):
        with _LOCK:
            state.last_error = str(result.get("message") or "Could not read the desktop composer.")
            _touch(state)
        return {"success": False, "application_agent": target, **result}
    return {"success": True, "application_agent": target, **_live_prompt_payload(target, state, result)}


def status_prompt(
    application_agent: Optional[str] = None,
    *,
    launch_if_needed: bool = False,
) -> Dict[str, Any]:
    """Inspect the selected desktop app for live processing state."""
    target, state = _state_for(application_agent)
    prefix = target.removesuffix("_agent")
    try:
        result = _call_target(
            target,
            f"get_{prefix}_prompt_status",
            launch_if_needed=launch_if_needed,
        )
    except Exception as exc:
        result = {"status": "error", "message": str(exc)}
    if not _is_success(result):
        with _LOCK:
            state.last_error = str(result.get("message") or "Could not inspect desktop prompt status.")
            _touch(state)
        return {"success": False, "application_agent": target, **result}

    prompt_status = str(result.get("prompt_status") or ("processing" if result.get("processing") else "idle"))
    with _LOCK:
        state.status = prompt_status
        _touch(state)
        response = _payload(target, state)
    response["processing"] = bool(result.get("processing"))
    response["desktop_action"] = result
    return response


def execute_prompt(
    application_agent: Optional[str] = None,
    *,
    launch_if_needed: bool = True,
    capture_after: bool = False,
) -> Dict[str, Any]:
    target, state = _state_for(application_agent)
    with _LOCK:
        if state.submitting or state.status == "processing":
            response = _payload(target, state)
            response.update(
                {
                    "already_submitted": True,
                    "message": "The prompt is already processing. Use Status prompt or Result prompt instead.",
                }
            )
            return response
        # Claim the submission slot before the slow desktop round-trip below so a
        # second overlapping call (double-click, client retry) sees the guard above
        # instead of also passing this check and double-submitting the prompt.
        # `state.status` itself can't be (ab)used for this: get_prompt() below
        # overwrites it mid-flight to mirror whatever the desktop currently reports.
        state.submitting = True
        _touch(state)
    try:
        live_prompt = get_prompt(target, launch_if_needed=launch_if_needed)
        if not live_prompt.get("success"):
            return live_prompt
        if not (live_prompt.get("text") or live_prompt.get("attachments")):
            return {"success": False, "application_agent": target, "status": "error", "message": "The prompt is empty."}
        try:
            result = _call_target(
                target,
                f"send_current_{target.removesuffix('_agent')}_prompt",
                launch_if_needed=launch_if_needed,
                capture_after=capture_after,
            )
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}
        if not _is_success(result):
            with _LOCK:
                state.last_error = str(result.get("message") or "Desktop prompt submission failed")
                state.status = "error"
                _touch(state)
            return {"success": False, "application_agent": target, **result}
        with _LOCK:
            state.submitted_prompt = state.prompt_text
            state.submitted = True
            state.status = "processing"
            state.result_text = ""
            state.last_error = ""
            _touch(state)
            response = _payload(target, state)
        response["desktop_action"] = result
        return response
    finally:
        with _LOCK:
            state.submitting = False
            _touch(state)


def send_prompt(application_agent: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    """Legacy compatibility alias for execute_prompt; do not expose as a separate action."""
    return execute_prompt(application_agent=application_agent, **kwargs)


def _omit_submitted_prompt_prefix(response_text: Any, submitted_prompt: Any) -> Tuple[str, bool]:
    """Drop an exact copied composer prefix without touching a real response."""
    response = str(response_text or "").strip()
    prompt = str(submitted_prompt or "").strip()
    if not response or not prompt:
        return response, False
    if response == prompt:
        return "", True
    if not response.startswith(prompt):
        return response, False
    remainder = response[len(prompt):]
    if not remainder.startswith(("\n", "\r")):
        return response, False
    return remainder.lstrip(), True


def result_prompt(application_agent: Optional[str] = None, *, launch_if_needed: bool = False) -> Dict[str, Any]:
    target, state = _state_for(application_agent)
    with _LOCK:
        if not state.submitted and state.status not in {"processing", "processed"}:
            return {"success": False, "application_agent": target, "status": state.status, "message": "No submitted prompt is waiting for a result."}
        expected_prompt = state.submitted_prompt

    live_status = status_prompt(target, launch_if_needed=launch_if_needed)
    if live_status.get("success") and live_status.get("processing"):
        return {
            "success": False,
            "application_agent": target,
            "status": "processing",
            "processing": True,
            "message": "The desktop prompt is still processing.",
            "desktop_status": live_status.get("desktop_action"),
        }
    try:
        result = _call_target(
            target,
            f"copy_{target.removesuffix('_agent')}_final_response",
            launch_if_needed=launch_if_needed,
            expected_prompt=expected_prompt,
        )
    except Exception as exc:
        result = {"status": "error", "message": str(exc)}
    if not _is_success(result):
        with _LOCK:
            state.last_error = str(result.get("message") or "Desktop result is not ready")
            _touch(state)
        return {
            "success": False,
            "application_agent": target,
            "status": "processing",
            "processing": True,
            "message": "Desktop result is not ready.",
            "desktop_status": result.get("status"),
            "desktop_action": result,
        }
    response_text, omitted_submitted_prompt = _omit_submitted_prompt_prefix(
        result.get("response_text") or result.get("text") or "",
        expected_prompt,
    )
    with _LOCK:
        state.result_text = response_text
        state.status = "processed"
        state.last_error = ""
        _touch(state)
    artifacts = {
        key: result[key]
        for key in ("copy_click", "clipboard", "fallback", "screenshot", "crop", "screenshots")
        if key in result
    }
    return {
        "success": True,
        "application_agent": target,
        "status": "processed",
        "result_text": response_text,
        "omitted_submitted_prompt": omitted_submitted_prompt,
        "artifacts": artifacts,
        "desktop_action": result,
    }


def _clear_prompt(application_agent: Optional[str], *, action: str, launch_if_needed: bool) -> Dict[str, Any]:
    target, state = _state_for(application_agent)
    try:
        result = _call_target(target, f"new_{target.removesuffix('_agent')}_prompt", launch_if_needed=launch_if_needed)
    except Exception as exc:
        result = {"status": "error", "message": str(exc)}
    if not _is_success(result):
        return {"success": False, "application_agent": target, **result}
    with _LOCK:
        _STATES[target] = _PromptState(status="empty", updated_at=time.time())
        state = _STATES[target]
        response = _payload(target, state)
    response["action"] = action
    response["desktop_action"] = result
    return response


def delete_prompt(application_agent: Optional[str] = None, *, launch_if_needed: bool = True) -> Dict[str, Any]:
    return _clear_prompt(application_agent, action="delete_prompt", launch_if_needed=launch_if_needed)


def new_prompt(application_agent: Optional[str] = None, *, launch_if_needed: bool = True) -> Dict[str, Any]:
    return _clear_prompt(application_agent, action="new_prompt", launch_if_needed=launch_if_needed)


def stop_prompt(application_agent: Optional[str] = None, *, launch_if_needed: bool = False) -> Dict[str, Any]:
    target, state = _state_for(application_agent)
    live_status = status_prompt(target, launch_if_needed=launch_if_needed)
    if not live_status.get("success"):
        return live_status
    if not live_status.get("processing"):
        return {"success": False, "application_agent": target, "status": live_status.get("status", "idle"), "message": "The prompt is not processing."}
    try:
        result = _call_target(target, f"stop_{target.removesuffix('_agent')}_prompt", launch_if_needed=launch_if_needed)
    except Exception as exc:
        result = {"status": "error", "message": str(exc)}
    if not _is_success(result):
        return {"success": False, "application_agent": target, **result}
    with _LOCK:
        state.status = "stopped"
        _touch(state)
        response = _payload(target, state)
    response["desktop_action"] = result
    return response


def configure_prompt(
    application_agent: Optional[str] = None,
    *,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    speed: Optional[str] = None,
    advanced: Optional[str] = None,
    permissions: Optional[str] = None,
    project_name: Optional[str] = None,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Apply only native desktop-agent settings requested by the caller."""
    target, state = _state_for(application_agent)
    actions: List[Dict[str, Any]] = []
    prefix = target.removesuffix("_agent")
    if any(str(value or "").strip() for value in (model, effort, speed, advanced)):
        kwargs: Dict[str, Any] = {"model": model, "effort": effort, "launch_if_needed": launch_if_needed}
        if target == "codex_desktop_agent":
            kwargs.update(speed=speed, advanced=advanced)
        result = _call_target(target, f"select_{prefix}_desktop_model", **kwargs)
        if not _is_success(result):
            return {"success": False, "application_agent": target, **result}
        actions.append(result)
    if str(permissions or "").strip():
        result = _call_target(target, f"select_{prefix}_desktop_permissions", permissions=permissions, launch_if_needed=launch_if_needed)
        if not _is_success(result):
            return {"success": False, "application_agent": target, **result}
        actions.append(result)
    if str(project_name or "").strip():
        result = _call_target(target, f"select_{prefix}_desktop_project", project_name=project_name, launch_if_needed=launch_if_needed)
        if not _is_success(result):
            return {"success": False, "application_agent": target, **result}
        actions.append(result)
    with _LOCK:
        response = _payload(target, state)
    response["configured"] = bool(actions)
    response["actions"] = actions
    return response


def available_application_agents() -> List[Dict[str, Any]]:
    return [
        {
            "application_agent": name,
            "label": _TARGET_LABELS[name],
            "package_available": bool(_TARGET_MODULES[name]),
            "supported_platforms": ["windows", "macos", "linux"],
            "requires_interactive_desktop": True,
        }
        for name in SUPPORTED_APPLICATION_AGENTS
    ]


def reset_prompt_builder_state() -> None:
    """Clear in-memory state; intended for isolated tests and process restarts."""
    with _LOCK:
        _STATES.clear()


TOOL_FUNCTIONS = {
    "build_prompt": build_prompt,
    "get_prompt": get_prompt,
    "status_prompt": status_prompt,
    "result_prompt": result_prompt,
    "delete_prompt": delete_prompt,
    "new_prompt": new_prompt,
    "execute_prompt": execute_prompt,
    "stop_prompt": stop_prompt,
    "configure_prompt": configure_prompt,
}


def run_tool(tool_name: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    requested_tool_name = re.sub(r"[\s-]+", "_", str(tool_name or "").strip().lower())
    canonical_tool_name = "execute_prompt" if requested_tool_name == "send_prompt" else requested_tool_name
    function = TOOL_FUNCTIONS.get(canonical_tool_name)
    if function is None:
        return {"success": False, "status": "error", "message": f"Unknown prompt-builder tool: {tool_name}"}
    try:
        result = function(**(payload if isinstance(payload, dict) else {}))
        return result if isinstance(result, dict) else {"success": True, "result": result}
    except Exception as exc:
        return {"success": False, "status": "error", "message": str(exc)}
