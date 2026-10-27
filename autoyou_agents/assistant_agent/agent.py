# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""AutoYou Assistant agent: hands-free turns after the wake word.

The root agent hands Assistant mode turns here deterministically (see
``_route_assistant_mode_turn`` in ``autoyou_agents/agent.py``). When the owner
picks this agent directly, the same plans run from ``before_agent_callback`` and
the model answers everything else with the tools below.
"""

from __future__ import annotations

from typing import Any, Dict

from google.adk.agents import Agent
from google.genai import types

from autoyou_agents.shared_tools.datetime_tool import (
    get_current_datetime,
    inject_realtime_datetime_into_request,
)

from . import assistant_tool
from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME


async def assistant_open_web_page(url: str, read_text: bool = False) -> Dict[str, Any]:
    """Open a web page in the browser desktop and read its title (and text).

    Args:
        url: The page, e.g. "example.com" or "https://example.com/news".
        read_text: Also read the start of the visible text.

    Returns:
        ``status``, a spoken ``reply``, and the page ``title`` and ``url``.
    """
    if not assistant_tool.browser_agent_installed():
        return {"status": "not_installed", "reply": assistant_tool.BROWSER_AGENT_MISSING_REPLY}
    return await assistant_tool.read_web_page(url, read_text=bool(read_text))


def assistant_list_windows() -> Dict[str, Any]:
    """List the windows open on this computer."""
    return assistant_tool.list_windows()


def assistant_focus_window(title: str) -> Dict[str, Any]:
    """Bring the window whose title (or app name) matches ``title`` to the front."""
    return assistant_tool.focus_window(title)


def _user_text(callback_context: Any) -> str:
    content = getattr(callback_context, "user_content", None)
    return " ".join(
        str(part.text) for part in getattr(content, "parts", None) or [] if getattr(part, "text", None)
    ).strip()


async def _assistant_before_agent_callback(callback_context: Any) -> Any:
    """Web reads and window steps need no model; everything else does."""
    plan = assistant_tool.plan_request(_user_text(callback_context))
    if plan.route not in {assistant_tool.ROUTE_BROWSER, assistant_tool.ROUTE_DESKTOP}:
        return None
    if plan.route == assistant_tool.ROUTE_BROWSER and not assistant_tool.browser_agent_installed():
        return types.Content(role="model", parts=[types.Part(text=assistant_tool.BROWSER_AGENT_MISSING_REPLY)])
    if plan.needs_browser_agent:
        # Clicking, typing and signing in are the Browser agent's job.
        return None
    try:
        result = await assistant_tool.run_plan(plan)
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        return types.Content(role="model", parts=[types.Part(text=f"That didn't work: {exc}")])
    if not result:
        return None
    return types.Content(role="model", parts=[types.Part(text=str(result.get("reply") or ""))])


async def _assistant_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)
    return None


def create_assistant_agent(model_config: Any) -> Agent:
    """Factory used by the root agent's static agent map."""
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=[assistant_open_web_page, assistant_list_windows, assistant_focus_window, get_current_datetime],
        before_agent_callback=_assistant_before_agent_callback,
        before_model_callback=_assistant_before_model_callback,
    )
