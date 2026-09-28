# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-ec8c0d98cdf32916ecf1ed56


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-ec8c0d98cdf32916ecf1ed56"

import os
import sys

import pytest
from google.adk.agents import Agent

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from autoyou_agents.agent import _install_dynamic_agent_tool_loop_guard


class DummyContext:
    def __init__(self, invocation_id="inv-1"):
        self.invocation_id = invocation_id
        self.state = {}


class DummyTool:
    name = "lookup"


def _tool(query: str):
    return {"status": "success", "query": query}


@pytest.mark.asyncio
async def test_dynamic_agent_guard_short_circuits_after_tool_budget(monkeypatch):
    monkeypatch.setenv("AUTOYOU_DYNAMIC_AGENT_MAX_TOOL_CALLS", "2")
    agent = Agent(name="custom_weather_agent", model="gemini-2.5-flash", instruction="test", tools=[_tool])

    _install_dynamic_agent_tool_loop_guard("custom_weather_agent", agent)

    context = DummyContext()
    before_tool = agent.before_tool_callback[0]
    before_model = agent.before_model_callback[0]

    assert await before_tool(DummyTool(), {"query": "one"}, context) is None
    assert await before_tool(DummyTool(), {"query": "two"}, context) is None
    blocked = await before_tool(DummyTool(), {"query": "three"}, context)

    assert blocked["status"] == "error"
    assert blocked["paused"] is True
    assert blocked["tool_calls"] == 2

    response = await before_model(context, llm_request=None)
    assert response is not None
    assert "stopped after 2 tool calls" in response.content.parts[0].text


@pytest.mark.asyncio
async def test_dynamic_agent_guard_converts_memory_layout_model_error(monkeypatch):
    monkeypatch.setenv("AUTOYOU_DYNAMIC_AGENT_MAX_TOOL_CALLS", "3")
    agent = Agent(name="custom_weather_agent", model="gemini-2.5-flash", instruction="test", tools=[_tool])

    _install_dynamic_agent_tool_loop_guard("custom_weather_agent", agent)

    context = DummyContext()
    on_model_error = agent.on_model_error_callback[0]
    response = await on_model_error(
        context,
        llm_request=None,
        error=RuntimeError("memory layout cannot be allocated"),
    )

    assert response is not None
    assert "oversized tool-result history" in response.content.parts[0].text
