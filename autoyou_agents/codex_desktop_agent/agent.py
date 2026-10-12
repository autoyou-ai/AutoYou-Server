# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-9b619cee6c4ccecedd2ed47b

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


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

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-9b619cee6c4ccecedd2ed47b"


_AGENT_DIR = Path(__file__).resolve().parent
_EXACT_TOOL_CALLBACK = make_desktop_exact_tool_callback(
    AGENT_NAME,
    (
        "get_codex_desktop_status",
        "get_codex_desktop_prompt",
        "get_codex_desktop_prompt_status",
        "get_codex_desktop_release_status",
        "list_codex_desktop_asset_packs",
        "select_codex_desktop_project",
        "select_codex_desktop_permissions",
        "select_codex_desktop_model",
        "add_to_codex_desktop_prompt",
        "replace_codex_desktop_prompt",
        "add_codex_desktop_attachments",
        "new_codex_desktop_prompt",
        "send_current_codex_desktop_prompt",
        "queue_codex_desktop_prompt",
        "copy_codex_desktop_final_response",
        "send_prompt_to_codex_desktop",
        "stop_codex_desktop_prompt",
        "ask_codex_and_return",
        "wait_for_codex_desktop_final_response",
        "get_codex_usage",
        "get_codex_desktop_usage",
        "read_codex_desktop_usage",
        "find_codex_desktop_screenshot_attachments",
        "capture_codex_desktop_screenshot",
    ),
)

def get_codex_desktop_status(capture_screenshot: bool = False) -> Dict[str, Any]:
    """Report whether the local Codex app is running, the active window bounds, and the selected asset pack."""
    return get_desktop_app_status(_AGENT_DIR, capture_screenshot=capture_screenshot)


def get_codex_desktop_prompt(launch_if_needed: bool = True) -> Dict[str, Any]:
    """Select/copy the live unsent Codex composer and return its metrics."""
    return get_desktop_app_prompt(_AGENT_DIR, launch_if_needed=launch_if_needed)


def get_codex_desktop_prompt_status(launch_if_needed: bool = False) -> Dict[str, Any]:
    """Inspect Codex's live composer/send control for processing state."""
    return get_desktop_app_prompt_status(_AGENT_DIR, launch_if_needed=launch_if_needed)

def list_codex_desktop_asset_packs(platform_tag: Optional[str] = None) -> Dict[str, Any]:
    """List the versioned Codex screenshot/coordinate packs available for the given (or current) platform."""
    return list_desktop_asset_packs(_AGENT_DIR, platform_tag=platform_tag)

def get_codex_desktop_release_status(
    platform_tag: Optional[str] = None,
    screenshot_limit: int = 1,
) -> Dict[str, Any]:
    """Return Codex's selected pack, release-action coverage, warnings, and screenshot paths."""
    return get_desktop_app_release_status(
        _AGENT_DIR,
        platform_tag=platform_tag,
        screenshot_limit=screenshot_limit,
    )

def select_codex_desktop_project(project_name: str, launch_if_needed: bool = True) -> Dict[str, Any]:
    """Open a Codex project/chat by typing its name into the sidebar Search control."""
    return select_desktop_app_project(
        _AGENT_DIR,
        project_name=project_name,
        launch_if_needed=launch_if_needed,
    )

def select_codex_desktop_permissions(permissions: str, launch_if_needed: bool = True) -> Dict[str, Any]:
    """Set Codex approval mode. Use: ask for approval, approve for me, full access, custom."""
    return select_desktop_app_permissions(
        _AGENT_DIR,
        permissions=permissions,
        launch_if_needed=launch_if_needed,
    )

def select_codex_desktop_model(
    model: Optional[str] = None,
    effort: Optional[str] = None,
    speed: Optional[str] = None,
    advanced: Optional[str] = None,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Select the Codex / ChatGPT Codex model, reasoning effort, and/or speed.

    Works on legacy Codex, 26.707 "ChatGPT Codex", and modern 26.803 builds (the right pack
    is chosen automatically from the installed version). Any argument is optional.

    Models: "GPT-6.1 Sol", "GPT-6 Astra", "GPT-6 Sol", "GPT-6 Luna", "GPT-5.6 Sol",
      "GPT-5.6 Terra", "GPT-5.6 Luna", "GPT-5.5". Legacy 26.707 and earlier names still resolve.
    Effort: low, medium, high, extra high, max (or ultra). On modern 26.803 builds,
      effort is selected via the 5-point slider popup (Low, Medium, High, Extra High, Max).
    Speed: "standard".
    """
    return select_desktop_app_model(
        _AGENT_DIR,
        model=model,
        effort=effort,
        speed=speed,
        advanced=advanced,
        launch_if_needed=launch_if_needed,
    )

def add_to_codex_desktop_prompt(
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    prepend_newline: bool = True,
    launch_if_needed: bool = True,
    preserve_text: bool = False,
) -> Dict[str, Any]:
    """Append text (and optional local file attachments) to Codex's composer WITHOUT sending it.

    Call this repeatedly to build one prompt from several AutoYou messages. Pass
    ``prepend_newline=False`` for the first chunk so it does not start with a blank line.
    """
    return add_text_to_desktop_app_prompt(
        _AGENT_DIR,
        prompt=prompt,
        working_directory=working_directory,
        attachment_paths=attachment_paths,
        prepend_newline=prepend_newline,
        launch_if_needed=launch_if_needed,
        preserve_text=preserve_text,
    )

def replace_codex_desktop_prompt(
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Select all existing Codex composer text and replace it with the provided text WITHOUT sending it."""
    return replace_desktop_app_prompt(
        _AGENT_DIR,
        prompt=prompt,
        working_directory=working_directory,
        attachment_paths=attachment_paths,
        launch_if_needed=launch_if_needed,
    )

def add_codex_desktop_attachments(
    attachment_paths: List[str],
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Paste local files (e.g. images) into Codex's composer as attachments WITHOUT sending the prompt."""
    return attach_files_to_desktop_app_prompt(
        _AGENT_DIR,
        attachment_paths=attachment_paths,
        launch_if_needed=launch_if_needed,
    )

def new_codex_desktop_prompt(launch_if_needed: bool = True) -> Dict[str, Any]:
    """Clear the current Codex composer and start a fresh unsent prompt."""
    return clear_desktop_app_prompt(_AGENT_DIR, launch_if_needed=launch_if_needed)

def stop_codex_desktop_prompt(launch_if_needed: bool = False) -> Dict[str, Any]:
    """Stop the currently running Codex generation without editing the draft."""
    return stop_desktop_app_prompt(_AGENT_DIR, launch_if_needed=launch_if_needed)

def send_current_codex_desktop_prompt(
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    """Send whatever is currently drafted in the Codex composer by pressing Enter."""
    return send_current_desktop_app_prompt(
        _AGENT_DIR,
        launch_if_needed=launch_if_needed,
        capture_after=capture_after,
    )

def queue_codex_desktop_prompt(
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    """Queue the currently drafted Codex prompt with Cmd+Enter (macOS) / Ctrl+Enter (Windows/Linux)."""
    return queue_current_desktop_app_prompt(
        _AGENT_DIR,
        launch_if_needed=launch_if_needed,
        capture_after=capture_after,
    )

def copy_codex_desktop_final_response(
    launch_if_needed: bool = True,
    expected_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Best-effort: click Codex's copy-response control for the latest answer and return the clipboard text."""
    return copy_final_response_from_desktop_app(
        _AGENT_DIR,
        launch_if_needed=launch_if_needed,
        expected_prompt=expected_prompt,
    )

def send_prompt_to_codex_desktop(
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

def _schedule_codex_response_return(
    tool_context: Any,
    *,
    initial_delay_seconds: float,
    prompt: str,
) -> Dict[str, Any]:
    """Schedule a run-once direct task that polls Codex until idle, copies its final
    response, and delivers it back to the originating client+session (reply target)
    and, when the asking turn was given one, to the conversation that asked."""
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
    # The id the device was given in this turn, kept word for word like the
    # reminder and task tools do, so the answer returns pinned to this
    # conversation even if the device has started another while Codex works.
    conversation_session_id = _extract_conversation_session_id(tool_context)
    delivery_target = _extract_delivery_target(tool_context, owner_key)

    if not delivery_target and not owner_key and not canonical_session_id:
        return {
            "status": "skipped",
            "message": (
                "No originating client/session reply target was available, so the async "
                "Codex response can't be routed back. Use copy_codex_desktop_final_response "
                "manually instead."
            ),
        }

    task_id = str(uuid.uuid4())[:8]
    run_at = time.time() + max(5.0, float(initial_delay_seconds))
    action: Dict[str, Any] = {
        "type": "desktop_response_return",
        "agent_name": "codex_desktop_agent",
        "message_prefix": "🧩 Codex finished. Here's the result:",
        "poll_interval_seconds": 12,
        "stable_polls": 2,
        "max_wait_seconds": 900,
        "initial_wait_seconds": 0,
    }
    # from __debug_provenance_k__ import donations
    task: Dict[str, Any] = {
        "id": task_id,
        "instruction": f"Return Codex's response for: {str(prompt or '')[:120]}",
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
            message="Codex async response return",
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

def ask_codex_and_return(
    prompt: str = "",
    working_directory: Optional[str] = None,
    attachment_paths: Optional[List[str]] = None,
    initial_delay_seconds: float = 25.0,
    launch_if_needed: bool = True,
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Send a prompt to Codex AND asynchronously return its final answer to this conversation.

    This is the full fire-and-forget loop: it submits the prompt now, then schedules a
    deferred task that polls Codex until its response stops changing (idle), copies the final
    answer, and delivers it back to the exact client+session this request came from. Returns
    immediately - the answer arrives later as a follow-up message.

    Pass ``prompt`` to replace+submit in one shot. Leave ``prompt`` empty to submit whatever is
    already drafted in the composer (e.g. after building it with ``add_to_codex_desktop_prompt``).
    """
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

    schedule = _schedule_codex_response_return(
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
            "Sent to Codex. Its final answer will be delivered back to this conversation "
            "when Codex finishes."
            if schedule.get("status") == "scheduled"
            else "Sent to Codex, but the async return was not scheduled: "
            + str(schedule.get("message") or schedule.get("status"))
        ),
    }

def wait_for_codex_desktop_final_response(
    poll_interval_seconds: float = 12.0,
    stable_polls: int = 2,
    max_wait_seconds: float = 900.0,
    initial_wait_seconds: float = 0.0,
    expected_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Wait until Codex's latest answer stops changing, then return the copied final text."""
    return wait_until_idle_and_copy_desktop_response(
        _AGENT_DIR,
        poll_interval_seconds=poll_interval_seconds,
        stable_polls=stable_polls,
        max_wait_seconds=max_wait_seconds,
        min_initial_wait_seconds=initial_wait_seconds,
        launch_if_needed=True,
        expected_prompt=expected_prompt,
    )

def get_codex_usage() -> Dict[str, Any]:
    """Read Codex's usage panel and return structured remaining usage.

    Opens Codex's account menu, expands "Usage remaining", and OCR-reads the two windows:
    the 5-hour rolling window and the Weekly window - each with percent remaining and a reset
    time/date. Reset times are returned as absolute timestamps in the SERVER machine's local
    timezone (plus seconds/minutes until reset), so they can drive usage-aware scheduled tasks.
    """
    return get_desktop_app_usage(_AGENT_DIR, launch_if_needed=True)

def get_codex_desktop_usage() -> Dict[str, Any]:
    """Read Codex's usage panel and return structured remaining usage (alias for get_codex_usage)."""
    return get_codex_usage()

def read_codex_desktop_usage() -> Dict[str, Any]:
    """Read Codex's usage panel and return structured remaining usage (alias for get_codex_usage)."""
    return get_codex_usage()

def find_codex_desktop_screenshot_attachments(
    directory: Optional[str] = None,
    limit: int = 3,
) -> Dict[str, Any]:
    """List recent screenshot image paths from ~/Desktop for Codex attachment_paths."""
    return list_recent_desktop_screenshot_paths(directory=directory, limit=limit)

def capture_codex_desktop_screenshot(label: str = "codex") -> Dict[str, Any]:
    """Capture a screenshot of the primary display (Codex focused) for debugging or sprite collection."""
    return capture_desktop_app_screenshot(_AGENT_DIR, label=label)

def refresh_codex_desktop_llm_reference() -> Dict[str, Any]:
    """Regenerate desktop_assets/llm.txt from the current manifest.json."""
    return refresh_desktop_agent_llm_reference(_AGENT_DIR)

def create_codex_desktop_agent(model_config):
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=[_EXACT_TOOL_CALLBACK],
        tools=[
            get_codex_desktop_status,
            get_codex_desktop_prompt,
            get_codex_desktop_prompt_status,
            get_codex_desktop_release_status,
            list_codex_desktop_asset_packs,
            select_codex_desktop_project,
            select_codex_desktop_permissions,
            select_codex_desktop_model,
            add_to_codex_desktop_prompt,
            replace_codex_desktop_prompt,
            add_codex_desktop_attachments,
            new_codex_desktop_prompt,
            send_current_codex_desktop_prompt,
            queue_codex_desktop_prompt,
            copy_codex_desktop_final_response,
            send_prompt_to_codex_desktop,
            stop_codex_desktop_prompt,
            ask_codex_and_return,
            wait_for_codex_desktop_final_response,
            get_codex_usage,
            get_codex_desktop_usage,
            read_codex_desktop_usage,
            find_codex_desktop_screenshot_attachments,
            capture_codex_desktop_screenshot,
            refresh_codex_desktop_llm_reference,
            get_current_datetime,
        ],
    )
