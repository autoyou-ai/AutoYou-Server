# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-4e78ff7341b47b12dafef009


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import logging
import time
from typing import Any, Dict

from google.adk.agents import Agent
from google.adk.tools import ToolContext

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime
from autoyou_agents.shared_tools.coding_handoff import CODING_HANDOFF_STATE_KEY
from autoyou_agents.shared_tools.workspace_tools import (
    create_directory,
    delete_path,
    get_workspace_root,
    git_diff,
    git_status,
    insert_after,
    insert_before,
    list_workspace,
    move_path,
    read_file,
    replace_text,
    run_command,
    search_workspace,
    write_file,
)
from shared.session_execution import (
    SESSION_CONTROL_STATE_KEY,
    STATUS_RUNNING,
    build_coding_guard_message,
    build_session_execution_metadata,
    build_session_recovery_actions,
    create_text_llm_response,
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
    del llm_request
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
    return None

async def _coding_before_tool_callback(tool: Any, args: Dict[str, Any], tool_context: ToolContext) -> Any:
    state = normalize_session_control_state(tool_context.state.get(SESSION_CONTROL_STATE_KEY))
    now = time.time()
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
        await maybe_save_resume_artifact(tool_context, paused_state)
        return _coding_pause_tool_response(paused_state)

    line_span = _tool_args_line_span(args)
    if tool_name == "run_command" and current_tool_calls >= max(4, tool_budget - 4):
        paused_state = mark_session_control_paused(
            state,
            reason="Coding task paused before another shell command to protect resources.",
            breaker_open_until=time.time() + resolve_coding_breaker_cooldown_seconds(),
            last_agent_name=AGENT_NAME,
            last_invocation_id=getattr(tool_context, "invocation_id", None),
        )
        tool_context.state[SESSION_CONTROL_STATE_KEY] = paused_state
        await maybe_save_resume_artifact(tool_context, paused_state)
        return _coding_pause_tool_response(paused_state)
    if tool_name == "search_workspace" and current_tool_calls >= max(6, tool_budget - 2):
        paused_state = mark_session_control_paused(
            state,
            reason="Coding task paused before another broad workspace search.",
            breaker_open_until=time.time() + resolve_coding_breaker_cooldown_seconds(),
            last_agent_name=AGENT_NAME,
            last_invocation_id=getattr(tool_context, "invocation_id", None),
        )
        tool_context.state[SESSION_CONTROL_STATE_KEY] = paused_state
        await maybe_save_resume_artifact(tool_context, paused_state)
        return _coding_pause_tool_response(paused_state)
    if tool_name == "read_file" and line_span >= 200 and current_tool_calls >= max(6, tool_budget - 2):
        paused_state = mark_session_control_paused(
            state,
            reason="Coding task paused before another large file read sweep.",
            breaker_open_until=time.time() + resolve_coding_breaker_cooldown_seconds(),
            last_agent_name=AGENT_NAME,
            last_invocation_id=getattr(tool_context, "invocation_id", None),
        )
        tool_context.state[SESSION_CONTROL_STATE_KEY] = paused_state
        await maybe_save_resume_artifact(tool_context, paused_state)
        return _coding_pause_tool_response(paused_state)

    state["tool_calls"] = current_tool_calls + 1
    state["status"] = STATUS_RUNNING
    tool_context.state[SESSION_CONTROL_STATE_KEY] = state
    return None

async def _coding_after_tool_callback(
    tool: Any,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict[str, Any],
) -> Any:
    del args
    state = normalize_session_control_state(tool_context.state.get(SESSION_CONTROL_STATE_KEY))
    tool_name = str(getattr(tool, "name", "") or type(tool).__name__)
    state["last_agent_name"] = AGENT_NAME
    state["last_invocation_id"] = getattr(tool_context, "invocation_id", "") or ""
    state["last_tool_name"] = tool_name
    state["updated_at"] = time.time()

    message_parts = [
        str(tool_response.get("message") or "").strip() if isinstance(tool_response, dict) else "",
        str(tool_response.get("stderr") or "").strip() if isinstance(tool_response, dict) else "",
    ]
    combined_message = " ".join(p for p in message_parts if p)
    if combined_message:
        state["last_error_message"] = combined_message
        lowered = combined_message.lower()
        if "timeout" in lowered or "timed out" in lowered:
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
            await maybe_save_resume_artifact(tool_context, paused_state)
            return {
                **(tool_response or {}),
                "session_execution": build_session_execution_metadata(paused_state),
                "recovery_actions": build_session_recovery_actions(paused_state),
            }

    tool_context.state[SESSION_CONTROL_STATE_KEY] = state
    return None

def get_pending_builder_handoff(tool_context: ToolContext) -> dict:
    """Return and clear any prepared builder -> coding handoff payload."""
    payload = tool_context.state.get(CODING_HANDOFF_STATE_KEY)
    if not payload:
        return {
            "status": "success",
            "handoff_present": False,
        }

    tool_context.state[CODING_HANDOFF_STATE_KEY] = None
    return {
        "status": "success",
        "handoff_present": True,
        "handoff": payload,
    }

def create_coding_agent(model_config):
    """Create the coding agent with repo-aware workspace tools."""
    tools = [
        get_workspace_root,
        get_pending_builder_handoff,
        list_workspace,
        search_workspace,
        read_file,
        write_file,
        replace_text,
        insert_before,
        insert_after,
        create_directory,
        move_path,
        delete_path,
        run_command,
        git_status,
        git_diff,
        restart_ai_agent_server,
        get_current_datetime,
    ]

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
        before_model_callback=[_coding_before_model_callback],
        after_model_callback=[_coding_after_model_callback],
        before_tool_callback=[_coding_before_tool_callback],
        after_tool_callback=[_coding_after_tool_callback],
    )
