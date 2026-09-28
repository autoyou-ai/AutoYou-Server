"""ADK entrypoint for the private Windows Security Agent."""

from __future__ import annotations

from typing import Any

from .network_tool import collect_network_snapshot
from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME


def get_network_snapshot(tool_context: Any = None) -> dict:
    del tool_context
    return collect_network_snapshot()


def create_win_security_agent(model_config: Any):
    from google.adk.agents import Agent

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=[get_network_snapshot],
    )

