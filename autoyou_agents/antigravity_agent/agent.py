# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import os
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime
from autoyou_agents.shared_tools.desktop_app_agent_shortcuts import make_desktop_exact_tool_callback
from autoyou_agents.shared_tools.desktop_app_control import (
    add_text_to_desktop_app_prompt,
    answer_desktop_app_question,
    attach_files_to_desktop_app_prompt,
    capture_desktop_app_screenshot,
    clear_desktop_app_prompt,
    copy_final_response_from_desktop_app,
    get_desktop_app_prompt,
    get_desktop_app_prompt_status,
    get_desktop_app_question,
    get_desktop_app_release_status,
    get_desktop_app_status,
    get_desktop_app_usage,
    list_desktop_asset_packs,
    list_recent_desktop_screenshot_paths,
    queue_current_desktop_app_prompt,
    refresh_desktop_agent_llm_reference,
    replace_desktop_app_prompt,
    select_desktop_app_model,
    select_desktop_app_permissions,
    select_desktop_app_project,
    send_current_desktop_app_prompt,
    send_prompt_to_desktop_app,
    stop_desktop_app_prompt,
    wait_until_idle_and_copy_desktop_response,
)


_AGENT_DIR = Path(__file__).resolve().parent
_EXACT_TOOL_CALLBACK = make_desktop_exact_tool_callback(
    AGENT_NAME,
    (
        "get_antigravity_status",
        "get_antigravity_desktop_status",
        "get_antigravity_prompt",
        "get_antigravity_desktop_prompt",
        "get_antigravity_prompt_status",
        "get_antigravity_desktop_prompt_status",
        "get_antigravity_release_status",
        "get_antigravity_desktop_release_status",
        "list_antigravity_asset_packs",
        "list_antigravity_desktop_asset_packs",
        "select_antigravity_project",
        "select_antigravity_desktop_project",
        "select_antigravity_permissions",
        "select_antigravity_desktop_permissions",
        "select_antigravity_model",
        "select_antigravity_desktop_model",
        "add_to_antigravity_prompt",
        "add_to_antigravity_desktop_prompt",
        "replace_antigravity_prompt",
        "replace_antigravity_desktop_prompt",
        "add_antigravity_attachments",
        "add_antigravity_desktop_attachments",
        "new_antigravity_prompt",
        "new_antigravity_desktop_prompt",
        "send_current_antigravity_prompt",
        "send_current_antigravity_desktop_prompt",
        "queue_antigravity_prompt",
        "queue_antigravity_desktop_prompt",
        "copy_antigravity_final_response",
        "copy_antigravity_desktop_final_response",
        "send_prompt_to_antigravity",
        "send_prompt_to_antigravity_desktop",
        "stop_antigravity_prompt",
        "stop_antigravity_desktop_prompt",
        "ask_antigravity_and_return",
        "get_antigravity_question",
        "get_antigravity_desktop_question",
        "answer_antigravity_question",
        "answer_antigravity_desktop_question",
        "wait_for_antigravity_final_response",
        "wait_for_antigravity_desktop_final_response",
        "get_antigravity_usage",
        "get_antigravity_desktop_usage",
        "read_antigravity_usage",
        "read_antigravity_desktop_usage",
        "find_antigravity_screenshot_attachments",
        "find_antigravity_desktop_screenshot_attachments",
        "capture_antigravity_screenshot",
        "capture_antigravity_desktop_screenshot",
    ),
)


def _runtime_environment_compatibility() -> Dict[str, Any]:
    """Report cross-platform compatibility metadata for Windows, macOS, Linux, WSL, and Docker."""
    sys_name = platform.system().lower()
    is_wsl = bool(os.environ.get("WSL_DISTRO_NAME")) or ("microsoft" in platform.release().lower())
    is_docker = Path("/.dockerenv").exists() or bool(os.environ.get("AUTOYOU_DOCKER_CONTAINER"))
    agy_cli = shutil.which("agy") or shutil.which("antigravity")
    win_exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity" / "Antigravity.exe"
    mac_app = Path("/Applications/Antigravity.app")
    return {
        "os": sys_name,
        "wsl": is_wsl,
        "docker": is_docker,
        "agy_cli_path": str(agy_cli) if agy_cli else None,
        "windows_executable_exists": bool(win_exe.is_file()) if str(win_exe) else False,
        "macos_bundle_exists": bool(mac_app.exists()),
        "supported_environments": ["windows", "macos", "linux", "wsl2", "docker"],
    }


def get_antigravity_status(capture_screenshot: bool = False) -> Dict[str, Any]:
    """Report whether the local Antigravity app is running, the active window bounds, and the selected asset pack."""
    result = get_desktop_app_status(_AGENT_DIR, capture_screenshot=capture_screenshot)
    if isinstance(result, dict):
        result["runtime_environment"] = _runtime_environment_compatibility()
    return result


def get_antigravity_desktop_status(capture_screenshot: bool = False) -> Dict[str, Any]:
    """Alias for get_antigravity_status."""
    return get_antigravity_status(capture_screenshot=capture_screenshot)


def get_antigravity_prompt(launch_if_needed: bool = True) -> Dict[str, Any]:
    """Select/copy the live unsent Antigravity composer and return its metrics."""
    return get_desktop_app_prompt(_AGENT_DIR, launch_if_needed=launch_if_needed)


def get_antigravity_desktop_prompt(launch_if_needed: bool = True) -> Dict[str, Any]:
    """Alias for get_antigravity_prompt."""
    return get_antigravity_prompt(launch_if_needed=launch_if_needed)


def get_antigravity_prompt_status(launch_if_needed: bool = False) -> Dict[str, Any]:
    """Inspect Antigravity's live composer/send control for processing or question state."""
    return get_desktop_app_prompt_status(_AGENT_DIR, launch_if_needed=launch_if_needed)


def get_antigravity_desktop_prompt_status(launch_if_needed: bool = False) -> Dict[str, Any]:
    """Alias for get_antigravity_prompt_status."""
    return get_antigravity_prompt_status(launch_if_needed=launch_if_needed)


def list_antigravity_asset_packs(platform_tag: Optional[str] = None) -> Dict[str, Any]:
    """List the versioned Antigravity screenshot/coordinate packs available for the given (or current) platform."""
    return list_desktop_asset_packs(_AGENT_DIR, platform_tag=platform_tag)


def list_antigravity_desktop_asset_packs(platform_tag: Optional[str] = None) -> Dict[str, Any]:
    """Alias for list_antigravity_asset_packs."""
    return list_antigravity_asset_packs(platform_tag=platform_tag)


def get_antigravity_release_status(
    platform_tag: Optional[str] = None,
    screenshot_limit: int = 1,
) -> Dict[str, Any]:
    """Return Antigravity's selected pack, release-action coverage, warnings, and screenshot paths."""
    result = get_desktop_app_release_status(
        _AGENT_DIR,
        platform_tag=platform_tag,
        screenshot_limit=screenshot_limit,
    )
    if isinstance(result, dict):
        result["runtime_environment"] = _runtime_environment_compatibility()
    return result


def get_antigravity_desktop_release_status(
    platform_tag: Optional[str] = None,
    screenshot_limit: int = 1,
) -> Dict[str, Any]:
    """Alias for get_antigravity_release_status."""
    return get_antigravity_release_status(platform_tag=platform_tag, screenshot_limit=screenshot_limit)


def select_antigravity_project(project_name: str, launch_if_needed: bool = True) -> Dict[str, Any]:
    """Select an Antigravity workspace/project."""
    return select_desktop_app_project(
        _AGENT_DIR,
        project_name=project_name,
        launch_if_needed=launch_if_needed,
    )


def select_antigravity_desktop_project(project_name: str, launch_if_needed: bool = True) -> Dict[str, Any]:
    """Alias for select_antigravity_project."""
    return select_antigravity_project(project_name=project_name, launch_if_needed=launch_if_needed)


def select_antigravity_permissions(permissions: str, launch_if_needed: bool = True) -> Dict[str, Any]:
    """Select Antigravity execution/approval mode: 'request review', 'default', or 'turbo'."""
    return select_desktop_app_permissions(
        _AGENT_DIR,
        permissions=permissions,
        launch_if_needed=launch_if_needed,
    )


def select_antigravity_desktop_permissions(permissions: str, launch_if_needed: bool = True) -> Dict[str, Any]:
    """Alias for select_antigravity_permissions."""
    return select_antigravity_permissions(permissions=permissions, launch_if_needed=launch_if_needed)


def select_antigravity_model(
    model: Optional[str] = None,
    effort: Optional[str] = None,
    speed: Optional[str] = None,
    advanced: Optional[str] = None,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Select Antigravity model and/or effort level.

    Models: 'Gemini 4 Argon', 'Gemini 3.8 Flash', 'Claude Opus 5.5', 'Claude Sonnet 5.5', 'GPT-OSS 120B (Medium)'.
    Effort (nested fly-out submenu): 'low', 'medium', 'high'.
    """
    return select_desktop_app_model(
        _AGENT_DIR,
        model=model,
        effort=effort,
        speed=speed,
        advanced=advanced,
        launch_if_needed=launch_if_needed,
    )


def select_antigravity_desktop_model(
    model: Optional[str] = None,
    effort: Optional[str] = None,
    speed: Optional[str] = None,
    advanced: Optional[str] = None,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Alias for select_antigravity_model."""
    return select_antigravity_model(
        model=model,
        effort=effort,
        speed=speed,
        advanced=advanced,
        launch_if_needed=launch_if_needed,
    )


def add_to_antigravity_prompt(
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    prepend_newline: bool = True,
    launch_if_needed: bool = True,
    preserve_text: bool = False,
) -> Dict[str, Any]:
    """Append text (and optional local file/image attachments) to Antigravity's prompt composer without sending."""
    return add_text_to_desktop_app_prompt(
        _AGENT_DIR,
        prompt=prompt,
        working_directory=working_directory,
        attachment_paths=attachment_paths,
        prepend_newline=prepend_newline,
        launch_if_needed=launch_if_needed,
        preserve_text=preserve_text,
    )


def add_to_antigravity_desktop_prompt(
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    prepend_newline: bool = True,
    launch_if_needed: bool = True,
    preserve_text: bool = False,
) -> Dict[str, Any]:
    """Alias for add_to_antigravity_prompt."""
    return add_to_antigravity_prompt(
        prompt=prompt,
        working_directory=working_directory,
        attachment_paths=attachment_paths,
        prepend_newline=prepend_newline,
        launch_if_needed=launch_if_needed,
        preserve_text=preserve_text,
    )


def replace_antigravity_prompt(
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    launch_if_needed: bool = True,
    preserve_text: bool = False,
) -> Dict[str, Any]:
    """Select all existing Antigravity composer text and replace it with the provided text without sending."""
    return replace_desktop_app_prompt(
        _AGENT_DIR,
        prompt=prompt,
        working_directory=working_directory,
        attachment_paths=attachment_paths,
        launch_if_needed=launch_if_needed,
        preserve_text=preserve_text,
    )


def replace_antigravity_desktop_prompt(
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    launch_if_needed: bool = True,
    preserve_text: bool = False,
) -> Dict[str, Any]:
    """Alias for replace_antigravity_prompt."""
    return replace_antigravity_prompt(
        prompt=prompt,
        working_directory=working_directory,
        attachment_paths=attachment_paths,
        launch_if_needed=launch_if_needed,
        preserve_text=preserve_text,
    )


def add_antigravity_attachments(
    attachment_paths: List[str],
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Paste local files/images into Antigravity's composer as attachments without sending."""
    return attach_files_to_desktop_app_prompt(
        _AGENT_DIR,
        attachment_paths=attachment_paths,
        launch_if_needed=launch_if_needed,
    )


def add_antigravity_desktop_attachments(
    attachment_paths: List[str],
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Alias for add_antigravity_attachments."""
    return add_antigravity_attachments(attachment_paths=attachment_paths, launch_if_needed=launch_if_needed)


def new_antigravity_prompt(launch_if_needed: bool = True) -> Dict[str, Any]:
    """Clear the current Antigravity composer and start a fresh unsent prompt."""
    return clear_desktop_app_prompt(_AGENT_DIR, launch_if_needed=launch_if_needed)


def new_antigravity_desktop_prompt(launch_if_needed: bool = True) -> Dict[str, Any]:
    """Alias for new_antigravity_prompt."""
    return new_antigravity_prompt(launch_if_needed=launch_if_needed)


def stop_antigravity_prompt(launch_if_needed: bool = False) -> Dict[str, Any]:
    """Stop the currently running Antigravity generation without editing the draft."""
    return stop_desktop_app_prompt(_AGENT_DIR, launch_if_needed=launch_if_needed)


def stop_antigravity_desktop_prompt(launch_if_needed: bool = False) -> Dict[str, Any]:
    """Alias for stop_antigravity_prompt."""
    return stop_antigravity_prompt(launch_if_needed=launch_if_needed)


def send_current_antigravity_prompt(
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    """Send whatever is currently drafted in the Antigravity composer."""
    return send_current_desktop_app_prompt(
        _AGENT_DIR,
        launch_if_needed=launch_if_needed,
        capture_after=capture_after,
    )


def send_current_antigravity_desktop_prompt(
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    """Alias for send_current_antigravity_prompt."""
    return send_current_antigravity_prompt(launch_if_needed=launch_if_needed, capture_after=capture_after)


def queue_antigravity_prompt(
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    """Queue the currently drafted Antigravity prompt with Cmd+Enter (macOS) / Ctrl+Enter (Windows/Linux)."""
    return queue_current_desktop_app_prompt(
        _AGENT_DIR,
        launch_if_needed=launch_if_needed,
        capture_after=capture_after,
    )


def queue_antigravity_desktop_prompt(
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    """Alias for queue_antigravity_prompt."""
    return queue_antigravity_prompt(launch_if_needed=launch_if_needed, capture_after=capture_after)


def copy_antigravity_final_response(
    launch_if_needed: bool = True,
    expected_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Click Antigravity's copy-response control for the latest answer and return the clipboard text."""
    return copy_final_response_from_desktop_app(
        _AGENT_DIR,
        launch_if_needed=launch_if_needed,
        expected_prompt=expected_prompt,
    )


def copy_antigravity_desktop_final_response(
    launch_if_needed: bool = True,
    expected_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Alias for copy_antigravity_final_response."""
    return copy_antigravity_final_response(launch_if_needed=launch_if_needed, expected_prompt=expected_prompt)


def send_prompt_to_antigravity(
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    launch_if_needed: bool = True,
    capture_before_submit: bool = False,
    capture_after_submit: bool = True,
) -> Dict[str, Any]:
    """One-shot: replace the composer with ``prompt`` (plus optional attachments) and submit it."""
    return send_prompt_to_desktop_app(
        _AGENT_DIR,
        prompt=prompt,
        working_directory=working_directory,
        attachment_paths=attachment_paths,
        launch_if_needed=launch_if_needed,
        capture_before_submit=capture_before_submit,
        capture_after_submit=capture_after_submit,
    )


def send_prompt_to_antigravity_desktop(
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    launch_if_needed: bool = True,
    capture_before_submit: bool = False,
    capture_after_submit: bool = True,
) -> Dict[str, Any]:
    """Alias for send_prompt_to_antigravity."""
    return send_prompt_to_antigravity(
        prompt=prompt,
        working_directory=working_directory,
        attachment_paths=attachment_paths,
        launch_if_needed=launch_if_needed,
        capture_before_submit=capture_before_submit,
        capture_after_submit=capture_after_submit,
    )


def _schedule_antigravity_response_return(
    tool_context: Any,
    *,
    initial_delay_seconds: float,
    prompt: str,
) -> Dict[str, Any]:
    """Schedule a run-once direct task that polls Antigravity until idle, copies its final
    response, and delivers it back to the originating client+session."""
    import time
    import uuid

    try:
        from autoyou_agents.tasks_agent.agent import (
            _extract_canonical_session_id,
            _extract_conversation_session_id,
            _extract_delivery_target,
            _extract_owner_key,
            _extract_runtime_identifiers,
        )
        from shared.scheduler_service import (
            TASKS_FILE,
            append_scheduler_activity_log,
            load_json,
            save_json,
        )
    except Exception as exc:  # pragma: no cover - import guard
        return {"status": "error", "message": f"Scheduler unavailable: {exc}"}

    creator_user_id, creator_ai_session_id = _extract_runtime_identifiers(tool_context)
    owner_key = _extract_owner_key(tool_context)
    canonical_session_id = _extract_canonical_session_id(tool_context)
    conversation_session_id = _extract_conversation_session_id(tool_context)
    delivery_target = _extract_delivery_target(tool_context, owner_key)

    if not delivery_target and not owner_key and not canonical_session_id:
        return {
            "status": "skipped",
            "message": (
                "No originating client/session reply target was available, so the async "
                "Antigravity response can't be routed back. Use copy_antigravity_final_response "
                "manually instead."
            ),
        }

    task_id = str(uuid.uuid4())[:8]
    run_at = time.time() + max(5.0, float(initial_delay_seconds))
    action: Dict[str, Any] = {
        "type": "desktop_response_return",
        "agent_name": "antigravity_agent",
        "message_prefix": "🚀 Antigravity finished. Here's the result:",
        "poll_interval_seconds": 12,
        "stable_polls": 2,
        "max_wait_seconds": 900,
        "initial_wait_seconds": 0,
    }
    task: Dict[str, Any] = {
        "id": task_id,
        "instruction": f"Return Antigravity's response for: {str(prompt or '')[:120]}",
        "interval_minutes": 0,
        "run_once": True,
        "last_run_s": run_at,
        "scheduler_session_id": f"scheduled-task::{task_id}",
        "created_at_s": time.time(),
        "enabled": True,
        "action": action,
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

    try:
        tasks = load_json(TASKS_FILE)
        tasks.append(task)
        save_json(TASKS_FILE, tasks)
        append_scheduler_activity_log(
            "task_created",
            agent_kind="tasks",
            item_id=task_id,
            status="created",
            message="Antigravity async response return",
            details={"owner_key": str(owner_key or ""), "run_at_s": run_at},
        )
    except Exception as exc:
        return {"status": "error", "message": f"Could not persist scheduled task: {exc}"}

    return {
        "status": "scheduled",
        "task_id": task_id,
        "run_at_s": run_at,
        "delivery_target": delivery_target or None,
    }


def ask_antigravity_and_return(
    prompt: str = "",
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    initial_delay_seconds: float = 25.0,
    launch_if_needed: bool = True,
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Send a prompt to Antigravity AND asynchronously return its final answer to this conversation."""
    if str(prompt or "").strip():
        send = send_prompt_to_desktop_app(
            _AGENT_DIR,
            prompt=prompt,
            working_directory=working_directory,
            attachment_paths=attachment_paths,
            launch_if_needed=launch_if_needed,
            capture_after_submit=True,
        )
    else:
        send = send_current_desktop_app_prompt(
            _AGENT_DIR,
            launch_if_needed=launch_if_needed,
            capture_after=True,
        )
    if send.get("status") != "success":
        return send

    schedule = _schedule_antigravity_response_return(
        tool_context,
        initial_delay_seconds=initial_delay_seconds,
        prompt=prompt or "(drafted prompt)",
    )
    return {
        "status": "success",
        "submitted": True,
        "delivery": "async",
        "schedule": schedule,
        "asset_pack_id": send.get("asset_pack_id"),
        "message": (
            "Sent to Antigravity. Its final answer will be delivered back to this conversation "
            "when Antigravity finishes."
            if schedule.get("status") == "scheduled"
            else "Sent to Antigravity, but the async return was not scheduled: "
            + str(schedule.get("message") or schedule.get("status"))
        ),
    }


def get_antigravity_question(launch_if_needed: bool = False) -> Dict[str, Any]:
    """Inspect Antigravity for an active mid-turn inline Question card above the composer."""
    return get_desktop_app_question(_AGENT_DIR, launch_if_needed=launch_if_needed)


def get_antigravity_desktop_question(launch_if_needed: bool = False) -> Dict[str, Any]:
    """Alias for get_antigravity_question."""
    return get_antigravity_question(launch_if_needed=launch_if_needed)


def answer_antigravity_question(
    response: Optional[str] = None,
    skip: bool = False,
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    """Reply to or skip an inline Question card in Antigravity."""
    return answer_desktop_app_question(
        _AGENT_DIR,
        response=response,
        skip=skip,
        launch_if_needed=launch_if_needed,
        capture_after=capture_after,
    )


def answer_antigravity_desktop_question(
    response: Optional[str] = None,
    skip: bool = False,
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    """Alias for answer_antigravity_question."""
    return answer_antigravity_question(
        response=response,
        skip=skip,
        launch_if_needed=launch_if_needed,
        capture_after=capture_after,
    )


def wait_for_antigravity_final_response(
    poll_interval_seconds: float = 12.0,
    stable_polls: int = 2,
    max_wait_seconds: float = 900.0,
    initial_wait_seconds: float = 0.0,
    expected_prompt: Optional[str] = None,
    auto_answer_question: Optional[str] = None,
    auto_skip_question: bool = False,
) -> Dict[str, Any]:
    """Wait until Antigravity's latest answer stops changing, then return the copied final text."""
    return wait_until_idle_and_copy_desktop_response(
        _AGENT_DIR,
        poll_interval_seconds=poll_interval_seconds,
        stable_polls=stable_polls,
        max_wait_seconds=max_wait_seconds,
        min_initial_wait_seconds=initial_wait_seconds,
        launch_if_needed=True,
        expected_prompt=expected_prompt,
        auto_answer_question=auto_answer_question,
        auto_skip_question=auto_skip_question,
    )


def wait_for_antigravity_desktop_final_response(
    poll_interval_seconds: float = 12.0,
    stable_polls: int = 2,
    max_wait_seconds: float = 900.0,
    initial_wait_seconds: float = 0.0,
    expected_prompt: Optional[str] = None,
    auto_answer_question: Optional[str] = None,
    auto_skip_question: bool = False,
) -> Dict[str, Any]:
    """Alias for wait_for_antigravity_final_response."""
    return wait_for_antigravity_final_response(
        poll_interval_seconds=poll_interval_seconds,
        stable_polls=stable_polls,
        max_wait_seconds=max_wait_seconds,
        initial_wait_seconds=initial_wait_seconds,
        expected_prompt=expected_prompt,
        auto_answer_question=auto_answer_question,
        auto_skip_question=auto_skip_question,
    )


def get_antigravity_usage() -> Dict[str, Any]:
    """Open Antigravity's Model picker -> 'View Usage' and OCR-read remaining usage."""
    return get_desktop_app_usage(_AGENT_DIR, launch_if_needed=True)


def get_antigravity_desktop_usage() -> Dict[str, Any]:
    """Alias for get_antigravity_usage."""
    return get_antigravity_usage()


def read_antigravity_usage() -> Dict[str, Any]:
    """Alias for get_antigravity_usage."""
    return get_antigravity_usage()


def read_antigravity_desktop_usage() -> Dict[str, Any]:
    """Alias for get_antigravity_usage."""
    return get_antigravity_usage()


def find_antigravity_screenshot_attachments(
    directory: Optional[str] = None,
    limit: int = 3,
) -> Dict[str, Any]:
    """List recent screenshot image paths from ~/Desktop for Antigravity attachment_paths."""
    return list_recent_desktop_screenshot_paths(directory=directory, limit=limit)


def find_antigravity_desktop_screenshot_attachments(
    directory: Optional[str] = None,
    limit: int = 3,
) -> Dict[str, Any]:
    """Alias for find_antigravity_screenshot_attachments."""
    return find_antigravity_screenshot_attachments(directory=directory, limit=limit)


def capture_antigravity_screenshot(label: str = "antigravity") -> Dict[str, Any]:
    """Capture a screenshot of the display with Antigravity focused."""
    return capture_desktop_app_screenshot(_AGENT_DIR, label=label)


def capture_antigravity_desktop_screenshot(label: str = "antigravity") -> Dict[str, Any]:
    """Alias for capture_antigravity_screenshot."""
    return capture_antigravity_screenshot(label=label)


def refresh_antigravity_llm_reference() -> Dict[str, Any]:
    """Regenerate desktop_assets/llm.txt from the current manifest.json."""
    return refresh_desktop_agent_llm_reference(_AGENT_DIR)


def refresh_antigravity_desktop_llm_reference() -> Dict[str, Any]:
    """Alias for refresh_antigravity_llm_reference."""
    return refresh_antigravity_llm_reference()


def create_antigravity_agent(model_config):
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=_EXACT_TOOL_CALLBACK,
        tools=[
            get_current_datetime,
            get_antigravity_status,
            get_antigravity_prompt,
            get_antigravity_prompt_status,
            get_antigravity_release_status,
            list_antigravity_asset_packs,
            select_antigravity_project,
            select_antigravity_permissions,
            select_antigravity_model,
            add_to_antigravity_prompt,
            replace_antigravity_prompt,
            add_antigravity_attachments,
            new_antigravity_prompt,
            stop_antigravity_prompt,
            send_current_antigravity_prompt,
            queue_antigravity_prompt,
            copy_antigravity_final_response,
            send_prompt_to_antigravity,
            ask_antigravity_and_return,
            get_antigravity_question,
            answer_antigravity_question,
            wait_for_antigravity_final_response,
            get_antigravity_usage,
            read_antigravity_usage,
            find_antigravity_screenshot_attachments,
            capture_antigravity_screenshot,
            refresh_antigravity_llm_reference,
        ],
    )
