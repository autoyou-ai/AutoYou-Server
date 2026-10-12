# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-6c95bbfcf25befb35c96d953

"""AutoYou Browser Agent - controller-backed real-browser automation.

Thin ADK layer over ``browser_tool.py`` (framework-agnostic logic that wraps the
auto-browser controller SDK). Conforms to the canonical agent layout:
``prompt.py`` + ``browser_tool.py`` + ``agent.py`` factory.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
from typing import Any, Dict

from google.adk.agents import Agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from . import browser_tool
from autoyou_agents.shared_tools.datetime_tool import (
    get_current_datetime,
    inject_realtime_datetime_into_request,
)

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-6c95bbfcf25befb35c96d953"


logger = logging.getLogger(__name__)
_NATIVE_BROWSER_VIEWER_SESSION_STATE_KEY = "autoyou_browser_agent_native_viewer_session"

# Tool surface exported to the model. These wrap the framework-agnostic
# implementations in ``browser_tool`` 1:1 so the ADK function-declaration schema
# is generated from the real signatures/docstrings.
_BROWSER_TOOLS = (
    browser_tool.browser_health,
    browser_tool.browser_open_session,
    browser_tool.browser_navigate,
    browser_tool.browser_observe,
    browser_tool.browser_click,
    browser_tool.browser_type,
    browser_tool.browser_scroll,
    browser_tool.browser_screenshot,
    browser_tool.browser_run_goal,
    browser_tool.browser_list_sessions,
    browser_tool.browser_close_session,
    browser_tool.browser_list_auth_profiles,
    browser_tool.browser_save_auth_profile,
)


async def _browser_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    """Inject the real host clock so the agent reasons with the correct date."""
    inject_realtime_datetime_into_request(llm_request)
    return None


async def _browser_after_tool_callback(
    tool: Any,
    args: Dict[str, Any],
    tool_context: Any,
    tool_response: Any,
) -> Any:
    """Show the live controller browser in AutoYou when a session starts."""
    tool_name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "").strip()
    # from __debug_provenance_k__ import donations
    if isinstance(tool_response, dict):
        status = str(tool_response.get("status") or "").strip().lower()
        if status and status not in {"success", "disabled"}:
            logger.warning(
                "browser_agent tool %s returned status=%s: %s",
                tool_name,
                status,
                str(tool_response.get("error") or tool_response.get("message") or "")[:300],
            )

        if tool_name in {"browser_open_session", "browser_navigate", "browser_run_goal"} and status in {
            "success", "takeover", "approval_required", "max_steps_reached",
        }:
            session = tool_response.get("session")
            session_id = str(tool_response.get("session_id") or "").strip()
            if not session_id and isinstance(session, dict):
                session_id = str(session.get("id") or session.get("session_id") or "").strip()
            state = getattr(tool_context, "state", None)
            if session_id and session_id != browser_tool._state_get(
                tool_context, _NATIVE_BROWSER_VIEWER_SESSION_STATE_KEY
            ):
                viewer_url = browser_tool.native_viewer_url(session)
                if viewer_url:
                    from autoyou_agents.client_browser_control_agent.agent import (
                        _dispatch_client_browser_control_payload,
                        build_client_browser_control_payload,
                    )

                    payload = build_client_browser_control_payload(
                        action="open_url", url=viewer_url, source="browser_agent"
                    )
                    if payload.get("success", True):
                        result = await _dispatch_client_browser_control_payload(payload, tool_context)
                        if result.get("success"):
                            browser_tool._state_set(tool_context, _NATIVE_BROWSER_VIEWER_SESSION_STATE_KEY, session_id)
    return None


def create_browser_agent(model_config: Any) -> Agent:
    """Create and configure the AutoYou Browser Agent.

    Args:
        model_config: The model configuration to use for the agent.

    Returns:
        Agent: Configured browser agent.
    """
    if not browser_tool.is_auto_browser_available():
        logger.warning(
            "auto-browser SDK unavailable (%s); browser_agent tools will report 'unavailable'.",
            browser_tool.auto_browser_import_error(),
        )

    tools = [*_BROWSER_TOOLS, get_current_datetime]

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=[_browser_before_model_callback],
        after_tool_callback=[_browser_after_tool_callback],
        tools=tools,
    )
