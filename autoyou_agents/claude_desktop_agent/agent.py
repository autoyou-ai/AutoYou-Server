# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-cd9c32ed455414e1b2fbf53d

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-cd9c32ed455414e1b2fbf53d"


from pathlib import Path
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime
from autoyou_agents.shared_tools.desktop_app_agent_shortcuts import make_desktop_exact_tool_callback
from autoyou_agents.shared_tools.desktop_app_control import (
    add_text_to_desktop_app_prompt,
    attach_files_to_desktop_app_prompt,
    capture_desktop_app_screenshot,
    clear_desktop_app_prompt,
    copy_final_response_from_desktop_app,
    get_desktop_app_release_status,
    get_desktop_app_prompt,
    get_desktop_app_prompt_status,
    get_desktop_app_status,
    get_desktop_app_usage,
    list_recent_desktop_screenshot_paths,
    list_desktop_asset_packs,
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
        "get_claude_desktop_status",
        "get_claude_desktop_prompt",
        "get_claude_desktop_prompt_status",
        "get_claude_desktop_release_status",
        "list_claude_desktop_asset_packs",
        "select_claude_desktop_project",
        "select_claude_desktop_permissions",
        "select_claude_desktop_model",
        "add_to_claude_desktop_prompt",
        "replace_claude_desktop_prompt",
        "add_claude_desktop_attachments",
        "new_claude_desktop_prompt",
        "send_current_claude_desktop_prompt",
        "queue_claude_desktop_prompt",
        "copy_claude_desktop_final_response",
        "send_prompt_to_claude_desktop",
        "stop_claude_desktop_prompt",
        "ask_claude_and_return",
        "wait_for_claude_desktop_final_response",
        "get_claude_usage",
        "get_claude_desktop_usage",
        "read_claude_desktop_usage",
        "find_claude_desktop_screenshot_attachments",
        "capture_claude_desktop_screenshot",
    ),
)

def get_claude_desktop_status(capture_screenshot: bool = False) -> Dict[str, Any]:
    """Report whether the local Claude app is running, the active window bounds, and the selected asset pack."""
    return get_desktop_app_status(_AGENT_DIR, capture_screenshot=capture_screenshot)


def get_claude_desktop_prompt(launch_if_needed: bool = True) -> Dict[str, Any]:
    """Select/copy the live unsent Claude composer and return its metrics."""
    return get_desktop_app_prompt(_AGENT_DIR, launch_if_needed=launch_if_needed)


def get_claude_desktop_prompt_status(launch_if_needed: bool = False) -> Dict[str, Any]:
    """Inspect Claude's live composer/send control for processing state."""
    return get_desktop_app_prompt_status(_AGENT_DIR, launch_if_needed=launch_if_needed)

def list_claude_desktop_asset_packs(platform_tag: Optional[str] = None) -> Dict[str, Any]:
    """List the versioned Claude screenshot/coordinate packs available for the given (or current) platform."""
    return list_desktop_asset_packs(_AGENT_DIR, platform_tag=platform_tag)

def get_claude_desktop_release_status(
    platform_tag: Optional[str] = None,
    screenshot_limit: int = 1,
) -> Dict[str, Any]:
    """Return Claude's selected pack, release-action coverage, warnings, and screenshot paths."""
    return get_desktop_app_release_status(
        _AGENT_DIR,
        platform_tag=platform_tag,
        screenshot_limit=screenshot_limit,
    )

def send_prompt_to_claude_desktop(
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

def select_claude_desktop_project(project_name: str, launch_if_needed: bool = True) -> Dict[str, Any]:
    """Select a Claude sidebar recent/project by typing the project name into the Claude search control."""
    return select_desktop_app_project(
        _AGENT_DIR,
        project_name=project_name,
        launch_if_needed=launch_if_needed,
    )

def select_claude_desktop_permissions(permissions: str, launch_if_needed: bool = True) -> Dict[str, Any]:
    """Select Claude permissions mode. Use: ask permissions, accept edits, plan mode, auto mode, bypass permissions."""
    return select_desktop_app_permissions(
        _AGENT_DIR,
        permissions=permissions,
        launch_if_needed=launch_if_needed,
    )

def select_claude_desktop_model(
    model: Optional[str] = None,
    effort: Optional[str] = None,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Select Claude model and/or effort. Models include Opus 4.7, Sonnet 4.6, Haiku 4.5, and Opus 4.6 Legacy."""
    return select_desktop_app_model(
        _AGENT_DIR,
        model=model,
        effort=effort,
        launch_if_needed=launch_if_needed,
    )

def add_to_claude_desktop_prompt(
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    prepend_newline: bool = True,
    launch_if_needed: bool = True,
    preserve_text: bool = False,
) -> Dict[str, Any]:
    """Append text and optional local file attachments to Claude's prompt composer without sending it."""
    return add_text_to_desktop_app_prompt(
        _AGENT_DIR,
        prompt=prompt,
        working_directory=working_directory,
        attachment_paths=attachment_paths,
        prepend_newline=prepend_newline,
        launch_if_needed=launch_if_needed,
        preserve_text=preserve_text,
    )

def replace_claude_desktop_prompt(
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Select all existing Claude composer text and replace it with the provided text without sending it."""
    return replace_desktop_app_prompt(
        _AGENT_DIR,
        prompt=prompt,
        working_directory=working_directory,
        attachment_paths=attachment_paths,
        launch_if_needed=launch_if_needed,
    )

def add_claude_desktop_attachments(
    attachment_paths: List[str],
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Paste local files into Claude's composer as attachments without sending the prompt."""
    return attach_files_to_desktop_app_prompt(
        _AGENT_DIR,
        attachment_paths=attachment_paths,
        launch_if_needed=launch_if_needed,
    )

def new_claude_desktop_prompt(launch_if_needed: bool = True) -> Dict[str, Any]:
    """Clear the current Claude composer and start a fresh unsent prompt."""
    return clear_desktop_app_prompt(_AGENT_DIR, launch_if_needed=launch_if_needed)

def stop_claude_desktop_prompt(launch_if_needed: bool = False) -> Dict[str, Any]:
    """Stop the currently running Claude generation without editing the draft."""
    return stop_desktop_app_prompt(_AGENT_DIR, launch_if_needed=launch_if_needed)

def send_current_claude_desktop_prompt(
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    """Send the currently drafted Claude prompt with Enter."""
    return send_current_desktop_app_prompt(
        _AGENT_DIR,
        launch_if_needed=launch_if_needed,
        capture_after=capture_after,
    )

def queue_claude_desktop_prompt(
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    """Queue the currently drafted Claude prompt with Ctrl+Enter on Windows/Linux or Cmd+Enter on macOS."""
    return queue_current_desktop_app_prompt(
        _AGENT_DIR,
        launch_if_needed=launch_if_needed,
        capture_after=capture_after,
    )

def copy_claude_desktop_final_response(
    launch_if_needed: bool = True,
    expected_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Click Claude's copy-response control for the latest visible answer and return clipboard text."""
    return copy_final_response_from_desktop_app(
        _AGENT_DIR,
        launch_if_needed=launch_if_needed,
        expected_prompt=expected_prompt,
    )

def _schedule_claude_response_return(
    tool_context: Any,
    *,
    initial_delay_seconds: float,
    prompt: str,
) -> Dict[str, Any]:
    """Schedule a run-once direct task that polls Claude until idle, copies its final
    response, and delivers it back to the originating client+session (reply target)."""
    import time
    import uuid

    try:
        from autoyou_agents.tasks_agent.agent import (
            _extract_canonical_session_id,
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
    delivery_target = _extract_delivery_target(tool_context, owner_key)

    if not delivery_target and not owner_key and not canonical_session_id:
        return {
            "status": "skipped",
            "message": (
                "No originating client/session reply target was available, so the async "
                "Claude response can't be routed back. Use copy_claude_desktop_final_response "
                "manually instead."
            ),
        }

    task_id = str(uuid.uuid4())[:8]
    run_at = time.time() + max(5.0, float(initial_delay_seconds))
    action: Dict[str, Any] = {
        "type": "desktop_response_return",
        "agent_name": "claude_desktop_agent",
        "message_prefix": "🧩 Claude finished. Here's the result:",
        "poll_interval_seconds": 12,
        "stable_polls": 2,
        "max_wait_seconds": 900,
        "initial_wait_seconds": 0,
    }
    task: Dict[str, Any] = {
        "id": task_id,
        "instruction": f"Return Claude's response for: {str(prompt or '')[:120]}",
        "interval_minutes": 0,
        "run_once": True,
        "last_run_s": run_at,  # run-once fires at this time
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
            message="Claude async response return",
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

def ask_claude_and_return(
    prompt: str = "",
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    initial_delay_seconds: float = 25.0,
    launch_if_needed: bool = True,
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Send a prompt to Claude AND asynchronously return its final answer to this conversation.

    This is the full fire-and-forget loop: it submits the prompt now, then schedules a
    deferred task that polls Claude until its response stops changing (idle), copies the final
    answer, and delivers it back to the exact client+session this request came from. Returns
    immediately - the answer arrives later as a follow-up message.

    Pass ``prompt`` to replace+submit in one shot. Leave ``prompt`` empty to submit whatever is
    already drafted in the composer (e.g. after building it with ``add_to_claude_desktop_prompt``).
    """
    if str(prompt or "").strip():
        send = send_prompt_to_claude_desktop(
            prompt=prompt,
            working_directory=working_directory,
            attachment_paths=attachment_paths,
            launch_if_needed=launch_if_needed,
            capture_after_submit=True,
        )
    else:
        send = send_current_claude_desktop_prompt(
            launch_if_needed=launch_if_needed,
            capture_after=True,
        )
    if send.get("status") != "success":
        return send

    schedule = _schedule_claude_response_return(
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
            "Sent to Claude. Its final answer will be delivered back to this conversation "
            "when Claude finishes."
            if schedule.get("status") == "scheduled"
            else "Sent to Claude, but the async return was not scheduled: "
            + str(schedule.get("message") or schedule.get("status"))
        ),
    }

def wait_for_claude_desktop_final_response(
    poll_interval_seconds: float = 12.0,
    stable_polls: int = 2,
    max_wait_seconds: float = 900.0,
    initial_wait_seconds: float = 0.0,
    expected_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Wait until Claude's latest answer stops changing, then return the copied final text."""
    return wait_until_idle_and_copy_desktop_response(
        _AGENT_DIR,
        poll_interval_seconds=poll_interval_seconds,
        stable_polls=stable_polls,
        max_wait_seconds=max_wait_seconds,
        min_initial_wait_seconds=initial_wait_seconds,
        launch_if_needed=True,
        expected_prompt=expected_prompt,
    )

def get_claude_usage() -> Dict[str, Any]:
    """Read Claude's usage panel and return structured remaining usage.

    Opens Claude's account menu, expands "Usage remaining", and OCR-reads the two windows:
    the 5-hour rolling window and the Weekly window - each with percent remaining and a reset
    time/date. Reset times are returned as absolute timestamps in the SERVER machine's local
    timezone (plus seconds/minutes until reset), so they can drive usage-aware scheduled tasks.
    """
    return get_desktop_app_usage(_AGENT_DIR, launch_if_needed=True)

def get_claude_desktop_usage() -> Dict[str, Any]:
    """Read Claude's usage panel and return structured remaining usage (alias for get_claude_usage)."""
    return get_claude_usage()

def read_claude_desktop_usage() -> Dict[str, Any]:
    """Read Claude's usage panel and return structured remaining usage (alias for get_claude_usage)."""
    return get_claude_usage()

def find_claude_desktop_screenshot_attachments(
    directory: Optional[str] = None,
    limit: int = 3,
) -> Dict[str, Any]:
    """List recent screenshot image paths from ~/Desktop for Claude attachment_paths."""
    return list_recent_desktop_screenshot_paths(directory=directory, limit=limit)

def capture_claude_desktop_screenshot(label: str = "claude") -> Dict[str, Any]:
    """Capture a screenshot of the primary display (Claude focused) for debugging or sprite collection."""
    return capture_desktop_app_screenshot(_AGENT_DIR, label=label)

def refresh_claude_desktop_llm_reference() -> Dict[str, Any]:
    """Regenerate desktop_assets/llm.txt from the current manifest.json."""
    return refresh_desktop_agent_llm_reference(_AGENT_DIR)

def create_claude_desktop_agent(model_config):
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=[_EXACT_TOOL_CALLBACK],
        tools=[
            get_claude_desktop_status,
            get_claude_desktop_prompt,
            get_claude_desktop_prompt_status,
            get_claude_desktop_release_status,
            list_claude_desktop_asset_packs,
            select_claude_desktop_project,
            select_claude_desktop_permissions,
            select_claude_desktop_model,
            add_to_claude_desktop_prompt,
            replace_claude_desktop_prompt,
            add_claude_desktop_attachments,
            new_claude_desktop_prompt,
            send_current_claude_desktop_prompt,
            queue_claude_desktop_prompt,
            copy_claude_desktop_final_response,
            send_prompt_to_claude_desktop,
            stop_claude_desktop_prompt,
            ask_claude_and_return,
            wait_for_claude_desktop_final_response,
            get_claude_usage,
            get_claude_desktop_usage,
            read_claude_desktop_usage,
            find_claude_desktop_screenshot_attachments,
            capture_claude_desktop_screenshot,
            refresh_claude_desktop_llm_reference,
            get_current_datetime,
        ],
    )
