# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-55b15d4885bb3ace2f9cf66f

"""Admin agent - ADK wiring (factory + callbacks) over ``admin_tool``.

The admin tool implementations, HTTP transport, and elevated-session helpers
live in :mod:`autoyou_agents.admin_agent.admin_tool` (mirroring ``page_tool.py``
and ``notes_tool.py``).  This module keeps only the ADK-specific glue: the
``before_model``/``after_tool`` callbacks and ``create_admin_agent``.

The tool implementations and session helpers are re-exported here unchanged so
that sibling agents (``model_picker_agent``, ``files_agent``, ``audio_agent``,
continue to ``from autoyou_agents.admin_agent.agent import ...`` exactly as
before.

See ``admin_tool`` for the elevated admin-session (TOTP 2FA) workflow.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import logging
from typing import Any, Dict

from google.adk.agents import Agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from autoyou_agents.shared_tools.datetime_tool import (
    get_current_datetime,
    inject_realtime_datetime_into_request,
)
from shared.session_execution import create_text_llm_response, create_tool_call_llm_response

# Re-export the full admin tool surface so existing imports of
# `autoyou_agents.admin_agent.agent` keep resolving unchanged.
from .admin_tool import (  # noqa: F401 (re-exported for sibling agents/tests)
    # Session auth
    verify_admin_totp,
    check_admin_session,
    revoke_admin_session,
    get_account_oauth_sign_in,
    get_saved_reply_target,
    send_saved_reply_target_message,
    dispatch_saved_reply_target_message,
    # Read-only status
    get_server_overview,
    get_ai_agent_status,
    get_model_library,
    get_model_details,
    get_model_behavior,
    get_speech_model_status,
    get_speech_model_download_status,
    get_model_download_status,
    get_installed_agents,
    get_signal_status,
    get_whatsapp_status,
    get_tunnelmole_status,
    get_internet_search_enabled,
    get_audio_playback_enabled,
    # Generic API caller
    admin_web_api_call,
    # Read-write
    set_internet_search_enabled,
    set_audio_playback_enabled,
    restart_whatsapp,
    restart_signal,
    # High-risk (session required)
    restart_ai_agent_server,
    select_model,
    download_model,
    delete_model,
    download_speech_model,
    delete_speech_model,
    set_model_behavior,
    install_agent,
    uninstall_agent,
    # Software update (authorized-server signed manifest + origin/main sync)
    get_update_status,
    apply_software_update,
    # Internal helpers consumed by callbacks and sibling agents
    _http,
    _check_admin_session,
    _get_session_token,
    _extract_text_from_llm_request,
    _extract_totp_reply_code,
    _extract_audio_playback_toggle,
    _get_invocation_id,
    _tool_dispatch_already_happened,
    _mark_tool_dispatch,
    _record_admin_tool_result,
    _state_get,
    _state_set,
    _dispatch_reply_target_message,
    _get_internal_ai_agent_api_token,
    _get_saved_reply_target,
    # State-key constants
    _ADMIN_TOTP_PENDING_STATE_KEY,
    _ADMIN_TOOL_RESULT_INVOCATION_ID_STATE_KEY,
    _ADMIN_TOOL_RESULT_MESSAGE_STATE_KEY,
)

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-55b15d4885bb3ace2f9cf66f"


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ADK callbacks
# ─────────────────────────────────────────────────────────────────────────────

async def _admin_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)

    user_text = _extract_text_from_llm_request(llm_request)
    if not user_text:
        return None

    invocation_id = _get_invocation_id(callback_context)
    if _tool_dispatch_already_happened(callback_context.state, invocation_id):
        result_invocation_id = str(
            _state_get(callback_context.state, _ADMIN_TOOL_RESULT_INVOCATION_ID_STATE_KEY) or ""
        ).strip()
        if invocation_id and invocation_id == result_invocation_id:
            result_message = str(
                _state_get(callback_context.state, _ADMIN_TOOL_RESULT_MESSAGE_STATE_KEY) or ""
            ).strip()
            if result_message:
                return create_text_llm_response(
                    result_message,
                    custom_metadata={
                        "response_author": AGENT_NAME,
                        "admin_deterministic_reply": True,
                    },
                )
        return None

    playback_toggle = _extract_audio_playback_toggle(user_text)
    if playback_toggle is not None:
        _mark_tool_dispatch(callback_context.state, invocation_id)
        if _check_admin_session(callback_context):
            return create_tool_call_llm_response(
                "set_audio_playback_enabled",
                {"enabled": playback_toggle},
                custom_metadata={
                    "response_author": AGENT_NAME,
                    "admin_action": "set_audio_playback_enabled",
                },
            )
        return create_tool_call_llm_response(
            "check_admin_session",
            {},
            custom_metadata={
                "response_author": AGENT_NAME,
                "admin_action": "check_admin_session",
            },
        )

    totp_code = _extract_totp_reply_code(user_text)
    # from __debug_provenance_a__ import schedule
    if totp_code:
        should_verify = bool(_state_get(callback_context, _ADMIN_TOTP_PENDING_STATE_KEY)) or not _check_admin_session(callback_context)
        if should_verify:
            _mark_tool_dispatch(callback_context.state, invocation_id)
            return create_tool_call_llm_response(
                "verify_admin_totp",
                {"totp_code": totp_code},
                custom_metadata={
                    "response_author": AGENT_NAME,
                    "admin_action": "verify_admin_totp",
                },
            )

    return None

async def _admin_datetime_injection_callback(callback_context: Any, llm_request: Any) -> Any:
    return await _admin_before_model_callback(callback_context, llm_request)

def _admin_after_tool_callback(tool: Any, args: Dict[str, Any], tool_context: Any, tool_response: Any) -> Any:
    del args
    invocation_id = _get_invocation_id(tool_context)
    if not invocation_id or not isinstance(tool_response, dict):
        return None

    tool_name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "").strip()
    if tool_name not in {
        "verify_admin_totp",
        "check_admin_session",
        "set_audio_playback_enabled",
        "revoke_admin_session",
    }:
        return None

    if tool_name == "verify_admin_totp":
        _state_set(tool_context, _ADMIN_TOTP_PENDING_STATE_KEY, not bool(tool_response.get("valid")))
    elif tool_name == "check_admin_session":
        active = bool(tool_response.get("active"))
        totp_configured = tool_response.get("totp_configured")
        _state_set(tool_context, _ADMIN_TOTP_PENDING_STATE_KEY, (not active) and totp_configured is not False)
    elif tool_name == "set_audio_playback_enabled":
        message_text = str(tool_response.get("message") or "").strip().lower()
        requires_admin_session = (
            str(tool_response.get("status") or "").strip().lower() == "error"
            and (
                "admin session required" in message_text
                or "verify_admin_totp" in message_text
                or "admin session token missing" in message_text
            )
        )
        _state_set(tool_context, _ADMIN_TOTP_PENDING_STATE_KEY, requires_admin_session)
    elif tool_name == "revoke_admin_session":
        _state_set(tool_context, _ADMIN_TOTP_PENDING_STATE_KEY, False)

    _record_admin_tool_result(tool_context.state, invocation_id, tool_name, tool_response)
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Agent factory
# ─────────────────────────────────────────────────────────────────────────────

def create_admin_agent(model_config: Any) -> Agent:
    """Create the admin agent with all management tools registered.

    Args:
        model_config: The model configuration to use for the agent.

    Returns:
        Agent: Fully configured admin agent.
    """
    tools = [
        # Session auth
        verify_admin_totp,
        check_admin_session,
        revoke_admin_session,
        get_account_oauth_sign_in,
        get_saved_reply_target,
        send_saved_reply_target_message,
        # Read-only status
        get_server_overview,
        get_ai_agent_status,
        get_model_library,
        get_model_details,
        get_model_behavior,
        get_speech_model_status,
        get_speech_model_download_status,
        get_model_download_status,
        get_installed_agents,
        get_signal_status,
        get_whatsapp_status,
        get_tunnelmole_status,
        get_internet_search_enabled,
        get_audio_playback_enabled,
        # Generic API caller
        admin_web_api_call,
        # Read-write (no session)
        set_internet_search_enabled,
        set_audio_playback_enabled,
        restart_whatsapp,
        restart_signal,
        # High-risk (session required)
        restart_ai_agent_server,
        select_model,
        download_model,
        delete_model,
        download_speech_model,
        delete_speech_model,
        set_model_behavior,
        install_agent,
        uninstall_agent,
        # Software update (authorized-server signed manifest + origin/main sync)
        get_update_status,
        apply_software_update,
        # Utility
        get_current_datetime,
    ]

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=[_admin_before_model_callback],
        after_tool_callback=[_admin_after_tool_callback],
        tools=tools,
    )
