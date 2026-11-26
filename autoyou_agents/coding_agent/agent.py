# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-4e78ff7341b47b12dafef009


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"
import logging
import asyncio
import json
import threading
import time
from typing import Any, Dict

from google.adk.agents import Agent
from google.adk.tools import ToolContext

from .harness import (
    ACTIVE_CODING_HANDOFF_STATE_KEY,
    CODING_HARNESS_STATE_KEY,
    LIGHTWEIGHT_CODING_TOOLS,
    check_duplicate_tool_call,
    manage_coding_plan,
    normalize_coding_harness_state,
    prepare_coding_llm_request,
    record_coding_tool_activity,
    research_web_reference,
)
from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from autoyou_agents.shared_tools.agent_execution import checkpoint_agent_state
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime
from autoyou_agents.shared_tools.coding_handoff import (
    CODING_HANDOFF_STATE_KEY,
    load_coding_handoff,
)
from autoyou_agents.shared_tools.workspace_rag import (
    find_symbol,
    get_file_outline,
    rag_search_workspace,
)
from autoyou_agents.shared_tools.workspace_tools import (
    append_file,
    apply_patch,
    create_directory,
    delete_path,
    find_files,
    get_workspace_root,
    git_diff,
    git_status,
    insert_after,
    insert_before,
    list_workspace,
    move_path,
    multi_edit_file,
    read_file,
    read_files_batch,
    replace_text,
    _run_command_with_cancel,
    search_workspace,
    verify_workspace_files,
    write_file,
)
from shared.session_execution import (
    SESSION_CONTROL_STATE_KEY,
    STATUS_RUNNING,
    build_coding_guard_message,
    build_session_execution_metadata,
    build_session_recovery_actions,
    create_text_llm_response,
    create_tool_call_llm_response,
    mark_session_control_paused,
    maybe_save_resume_artifact,
    normalize_session_control_state,
    resolve_coding_breaker_cooldown_seconds,
    resolve_coding_tool_call_budget,
    resolve_coding_turn_budget_seconds,
)

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-4e78ff7341b47b12dafef009"


try:
    from autoyou_agents.agent_builder_agent.agent import restart_ai_agent_server
except Exception as exc:
    logging.warning("restart_ai_agent_server import failed: %s", exc)

    def restart_ai_agent_server(*args, **kwargs):
        return {"status": "error", "message": "AI agent restart tool is unavailable"}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_command(command: str, cwd: str = ".", timeout_seconds: int = 120) -> Dict[str, Any]:
    """Run an opted-in workspace command without blocking chat; Stop cancels its process tree.

    timeout_seconds bounds this command only; use 0 for a command without a deadline.
    """
    cancel = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_run_command_with_cancel, command, cwd, timeout_seconds, cancel))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=15)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        raise


def _coding_pause_tool_response(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "error",
        "message": build_coding_guard_message(state),
        "paused": True,
        "resumable": True,
        "session_execution": build_session_execution_metadata(state),
        "recovery_actions": build_session_recovery_actions(state),
    }


async def _pause_coding_agent(
    callback_context: Any,
    state: Dict[str, Any],
    *,
    reason: str,
    open_breaker: bool = False,
) -> Any:
    breaker_open_until = None
    if open_breaker:
        breaker_open_until = time.time() + resolve_coding_breaker_cooldown_seconds()
    paused_state = mark_session_control_paused(
        state,
        reason=reason,
        breaker_open_until=breaker_open_until,
        last_agent_name=AGENT_NAME,
        last_invocation_id=getattr(callback_context, "invocation_id", None),
    )
    callback_context.state[SESSION_CONTROL_STATE_KEY] = paused_state
    await maybe_save_resume_artifact(callback_context, paused_state)
    return create_text_llm_response(
        build_coding_guard_message(paused_state),
        custom_metadata={
            "session_execution": build_session_execution_metadata(paused_state),
            "recovery_actions": build_session_recovery_actions(paused_state),
        },
    )


def _tool_args_line_span(args: Dict[str, Any]) -> int:
    try:
        start_line = int(args.get("start_line", 1) or 1)
        end_line = int(args.get("end_line", start_line) or start_line)
        return max(0, end_line - start_line)
    except Exception:
        return 0


async def _coding_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    state = normalize_session_control_state(callback_context.state.get(SESSION_CONTROL_STATE_KEY))
    now = time.time()
    state["last_agent_name"] = AGENT_NAME
    state["last_invocation_id"] = getattr(callback_context, "invocation_id", "") or ""
    if not state.get("turn_started_at"):
        state["turn_started_at"] = now

    breaker_open_until = float(state.get("breaker_open_until") or 0.0)
    if breaker_open_until > now:
        return await _pause_coding_agent(
            callback_context,
            state,
            reason=(
                state.get("pause_reason")
                or "The coding workflow is currently cooling down after recent timeouts."
            ),
            open_breaker=False,
        )

    turn_budget = float(state.get("turn_timeout_seconds") or resolve_coding_turn_budget_seconds())
    elapsed = max(0.0, now - float(state.get("turn_started_at") or now))
    if turn_budget > 0 and elapsed >= turn_budget:
        return await _pause_coding_agent(
            callback_context,
            state,
            reason=f"Coding task exceeded the {int(turn_budget)}s turn budget.",
            open_breaker=True,
        )

    current_model_calls = int(state.get("model_calls") or 0)
    state["model_calls"] = current_model_calls + 1
    state["status"] = STATUS_RUNNING
    # from __debug_provenance_n__ import license
    state["updated_at"] = now
    callback_context.state[SESSION_CONTROL_STATE_KEY] = state

    # Compact bulky historical tool outputs, inject durable plan/handoff state,
    # and raise the generation token floor for multi-line code generation.
    tool_budget = int(state.get("tool_call_budget") or resolve_coding_tool_call_budget())
    current_tool_calls = int(state.get("tool_calls") or 0)
    prepare_coding_llm_request(
        callback_context,
        llm_request,
        tool_calls=current_tool_calls,
        tool_budget=tool_budget,
    )
    return None


async def _coding_after_model_callback(callback_context: Any, llm_response: Any) -> Any:
    state = normalize_session_control_state(callback_context.state.get(SESSION_CONTROL_STATE_KEY))
    state["last_agent_name"] = AGENT_NAME
    state["last_invocation_id"] = getattr(callback_context, "invocation_id", "") or ""
    state["updated_at"] = time.time()
    error_message = str(getattr(llm_response, "error_message", "") or "").strip()
    if error_message:
        state["last_error_message"] = error_message
        lowered = error_message.lower()
        if "timeout" in lowered or "timed out" in lowered:
            return await _pause_coding_agent(
                callback_context,
                state,
                reason="Coding task timed out while waiting for model output.",
                open_breaker=True,
            )
    callback_context.state[SESSION_CONTROL_STATE_KEY] = state
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) or []
    if error_message or getattr(llm_response, "partial", False) or any(getattr(p, "function_call", None) for p in parts):
        return None
    text = "\n".join(str(getattr(p, "text", "") or "") for p in parts).strip()
    if not text:
        return None
    from autoyou_agents.agent_harness import is_progress_only_response
    harness = normalize_coding_harness_state(callback_context.state.get(CODING_HARNESS_STATE_KEY))
    pending = harness["pending_verification"]
    if pending or is_progress_only_response(text):
        harness["finalization_attempts"] += 1
        callback_context.state[CODING_HARNESS_STATE_KEY] = harness
        if harness["finalization_attempts"] > 3:
            return await _pause_coding_agent(
                callback_context, state, reason="Coding could not complete verification or move beyond progress-only replies. The checkpoint is available to resume.",
                open_breaker=False,
            )
        if pending and state.get("last_tool_name") != "verify_workspace_files":
            return create_tool_call_llm_response("verify_workspace_files", {"paths_json": json.dumps(pending)})
        return create_tool_call_llm_response("manage_coding_plan", {"action": "get"})
    callback_context.state[ACTIVE_CODING_HANDOFF_STATE_KEY] = None
    return None


async def _coding_model_error_callback(callback_context: Any, llm_request: Any, error: Exception) -> Any:
    """Recover gracefully from provider/JSON errors with honest progress status."""
    if isinstance(error, asyncio.CancelledError):
        raise error
    del llm_request
    harness_state = normalize_coding_harness_state(callback_context.state.get(CODING_HARNESS_STATE_KEY))
    modified = harness_state.get("files_modified") or []
    verifications = harness_state.get("verifications") or []
    err_text = str(error or "Unexpected model error").strip()
    logger.warning("Coding agent model error: %s", err_text)

    parts = [f"The coding agent hit a model error before finishing: {err_text}."]
    if modified:
        parts.append("Files already modified in this session: " + ", ".join(modified) + ".")
    if verifications:
        last_v = verifications[-1]
        parts.append(f"Last verification: {last_v.get('summary')}.")
    parts.append("Send 'continue' to resume from this checkpoint without redoing completed edits.")
    return create_text_llm_response(" ".join(parts))


async def _coding_before_tool_callback(tool: Any, args: Dict[str, Any], tool_context: ToolContext) -> Any:
    state = normalize_session_control_state(tool_context.state.get(SESSION_CONTROL_STATE_KEY))
    harness_state = normalize_coding_harness_state(tool_context.state.get(CODING_HARNESS_STATE_KEY))
    now = time.time()
    invocation_id = str(getattr(tool_context, "invocation_id", "") or "")
    if harness_state["invocation_id"] != invocation_id:
        harness_state.update(invocation_id=invocation_id, lightweight_calls=0,
                             last_call_signature="", repeat_streak=0, finalization_attempts=0)
    tool_name = str(getattr(tool, "name", "") or type(tool).__name__)
    state["last_agent_name"] = AGENT_NAME
    state["last_invocation_id"] = getattr(tool_context, "invocation_id", "") or ""
    state["last_tool_name"] = tool_name
    state["updated_at"] = now

    breaker_open_until = float(state.get("breaker_open_until") or 0.0)
    if breaker_open_until > now:
        paused_state = mark_session_control_paused(
            state,
            reason=(
                state.get("pause_reason")
                or "The coding workflow is currently cooling down after recent timeouts."
            ),
            breaker_open_until=breaker_open_until,
            last_agent_name=AGENT_NAME,
            last_invocation_id=getattr(tool_context, "invocation_id", None),
        )
        tool_context.state[SESSION_CONTROL_STATE_KEY] = paused_state
        await maybe_save_resume_artifact(tool_context, paused_state)
        return _coding_pause_tool_response(paused_state)

    # Anti-thrash guard: nudge the model instead of burning budget on identical retries.
    duplicate_nudge = check_duplicate_tool_call(harness_state, tool_name, args)
    if duplicate_nudge is not None:
        tool_context.state[CODING_HARNESS_STATE_KEY] = harness_state
        tool_context.state[SESSION_CONTROL_STATE_KEY] = state
        return duplicate_nudge

    tool_budget = int(state.get("tool_call_budget") or resolve_coding_tool_call_budget())
    current_tool_calls = int(state.get("tool_calls") or 0)

    if tool_budget > 0 and current_tool_calls >= tool_budget:
        paused_state = mark_session_control_paused(
            state,
            reason="Coding task paused after repeated tool calls in this turn.",
            breaker_open_until=time.time() + resolve_coding_breaker_cooldown_seconds(),
            last_agent_name=AGENT_NAME,
            last_invocation_id=getattr(tool_context, "invocation_id", None),
        )
        tool_context.state[SESSION_CONTROL_STATE_KEY] = paused_state
        tool_context.state[CODING_HARNESS_STATE_KEY] = harness_state
        await maybe_save_resume_artifact(tool_context, paused_state)
        return _coding_pause_tool_response(paused_state)

    if tool_budget > 0 and tool_name == "run_command" and current_tool_calls >= max(4, tool_budget - 4):
        paused_state = mark_session_control_paused(
            state, reason="Coding task paused before another shell command under the configured budget.",
            breaker_open_until=time.time() + resolve_coding_breaker_cooldown_seconds(),
            last_agent_name=AGENT_NAME, last_invocation_id=getattr(tool_context, "invocation_id", None),
        )
        tool_context.state[SESSION_CONTROL_STATE_KEY] = paused_state
        tool_context.state[CODING_HARNESS_STATE_KEY] = harness_state
        await maybe_save_resume_artifact(tool_context, paused_state)
        return _coding_pause_tool_response(paused_state)

    if tool_name in LIGHTWEIGHT_CODING_TOOLS:
        harness_state["lightweight_calls"] = int(harness_state.get("lightweight_calls") or 0) + 1
    if tool_budget > 0 or tool_name not in LIGHTWEIGHT_CODING_TOOLS:
        state["tool_calls"] = current_tool_calls + 1
    state["status"] = STATUS_RUNNING
    tool_context.state[CODING_HARNESS_STATE_KEY] = harness_state
    tool_context.state[SESSION_CONTROL_STATE_KEY] = state
    if tool_name in {"write_file", "append_file", "replace_text", "multi_edit_file", "apply_patch", "insert_before", "insert_after", "move_path", "delete_path", "run_command"}:
        tool_context.state["_autoyou_coding_operation"] = {
            "tool": tool_name, "path": str(args.get("path") or args.get("source_path") or ""),
            "signature": harness_state["last_call_signature"], "phase": "started",
            "invocation_id": invocation_id,
        }
    await checkpoint_agent_state(tool_context)
    return None


async def _coding_after_tool_callback(
    tool: Any,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict[str, Any],
) -> Any:
    state = normalize_session_control_state(tool_context.state.get(SESSION_CONTROL_STATE_KEY))
    harness_state = normalize_coding_harness_state(tool_context.state.get(CODING_HARNESS_STATE_KEY))
    tool_name = str(getattr(tool, "name", "") or type(tool).__name__)
    state["last_agent_name"] = AGENT_NAME
    state["last_invocation_id"] = getattr(tool_context, "invocation_id", "") or ""
    state["last_tool_name"] = tool_name
    state["updated_at"] = time.time()

    record_coding_tool_activity(harness_state, tool_name, args, tool_response)
    tool_context.state[CODING_HARNESS_STATE_KEY] = harness_state
    operation = tool_context.state.get("_autoyou_coding_operation")
    if isinstance(operation, dict) and operation.get("tool") == tool_name:
        tool_context.state["_autoyou_coding_operation"] = {**operation, "phase": "completed", "status": tool_response.get("status", "unknown") if isinstance(tool_response, dict) else "unknown"}

    message_parts = [
        str(tool_response.get("message") or "").strip() if isinstance(tool_response, dict) else "",
        str(tool_response.get("stderr") or "").strip() if isinstance(tool_response, dict) else "",
    ]
    combined_message = " ".join(p for p in message_parts if p)
    response = tool_response if isinstance(tool_response, dict) else {}
    failed = response.get("status") == "error"
    # Command output can mention timeout tests or configuration on a successful
    # run. Only an explicit timeout (or legacy error message) should pause work.
    timed_out = response.get("timed_out") is True or (
        "timed_out" not in response
        and response.get("status") != "success"
        and (
            str(response.get("message") or "").lower().startswith("timeout")
            or "timed out" in str(response.get("message") or "").lower()
        )
    )
    if failed or timed_out:
        state["last_error_message"] = combined_message
        if timed_out:
            next_count = int(state.get("consecutive_timeouts") or 0) + 1
            state["consecutive_timeouts"] = next_count
            breaker_until = None
            if next_count >= 2:
                breaker_until = time.time() + resolve_coding_breaker_cooldown_seconds()
            paused_state = mark_session_control_paused(
                state,
                reason=f"Coding task paused after tool timeout in {tool_name}.",
                breaker_open_until=breaker_until,
                last_agent_name=AGENT_NAME,
                last_invocation_id=getattr(tool_context, "invocation_id", None),
            )
            tool_context.state[SESSION_CONTROL_STATE_KEY] = paused_state
            await checkpoint_agent_state(tool_context)
            await maybe_save_resume_artifact(tool_context, paused_state)
            return {
                **(tool_response or {}),
                "session_execution": build_session_execution_metadata(paused_state),
                "recovery_actions": build_session_recovery_actions(paused_state),
            }
    elif response.get("status") == "success":
        state["consecutive_timeouts"] = 0
        state["last_error_message"] = ""

    tool_context.state[SESSION_CONTROL_STATE_KEY] = state
    await checkpoint_agent_state(tool_context)
    return None


def get_pending_builder_handoff(tool_context: ToolContext) -> dict:
    """Return and clear any prepared builder -> coding handoff payload."""
    payload = load_coding_handoff(tool_context.state, clear=True)
    if not payload:
        return {
            "status": "success",
            "handoff_present": False,
        }

    tool_context.state[ACTIVE_CODING_HANDOFF_STATE_KEY] = payload

    return {
        "status": "success",
        "handoff_present": True,
        "handoff": payload,
    }


def create_coding_agent(model_config):
    """Create the coding agent with RAG, AST outline, patching, verification, and docs lookup."""
    from autoyou_agents.pi_agent.agent import forward_coding_to_pi

    tools = [
        get_workspace_root,
        get_pending_builder_handoff,
        manage_coding_plan,
        list_workspace,
        find_files,
        search_workspace,
        rag_search_workspace,
        find_symbol,
        get_file_outline,
        read_file,
        read_files_batch,
        write_file,
        append_file,
        replace_text,
        multi_edit_file,
        apply_patch,
        insert_before,
        insert_after,
        create_directory,
        move_path,
        delete_path,
        verify_workspace_files,
        run_command,
        git_status,
        git_diff,
        research_web_reference,
        restart_ai_agent_server,
        get_current_datetime,
    ]

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
        before_agent_callback=forward_coding_to_pi,
        before_model_callback=[_coding_before_model_callback],
        after_model_callback=[_coding_after_model_callback],
        on_model_error_callback=[_coding_model_error_callback],
        before_tool_callback=[_coding_before_tool_callback],
        after_tool_callback=[_coding_after_tool_callback],
    )
