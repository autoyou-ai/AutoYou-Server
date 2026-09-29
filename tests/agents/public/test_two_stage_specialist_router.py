# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-77bc4eca8d33eaf68a8e8213

"""Two-stage specialist routing.

The root used to advertise one AgentTool per installed specialist (~36 function
declarations). Small local models collapsed under that payload and echoed the
tool schema back as prose; the adapter's only recovery was to retry with
`tools=None`, leaving the model unable to reach any specialist. These tests pin
the dispatcher that replaced it.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import pytest
import litellm

import autoyou_agents.agent as root_agent

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-77bc4eca8d33eaf68a8e8213"


class _FakeAgentTool:
    """Records the arguments an AgentTool would have been invoked with."""

    def __init__(self, reply="ok"):
        self.reply = reply
        self.calls = []

    async def run_async(self, *, args, tool_context):
        self.calls.append({"args": args, "tool_context": tool_context})
        return self.reply


class _UnavailableOllamaTool:
    async def run_async(self, *, args, tool_context):
        raise litellm.APIConnectionError(
            message="Ollama_chatException - Cannot connect to host localhost:11434",
            llm_provider="ollama",
            model="test-model",
        )


@pytest.fixture
def registered_specialists(monkeypatch):
    tools = {
        "autoyou_notes_agent": _FakeAgentTool("Note created."),
        "autoyou_page_agent": _FakeAgentTool("Saved to page feed."),
    }
    monkeypatch.setattr(root_agent, "_SPECIALIST_AGENT_TOOLS", tools)
    monkeypatch.setattr(
        root_agent,
        "_SPECIALIST_AGENT_DESCRIPTIONS",
        {"autoyou_notes_agent": "Notes CRUD.", "autoyou_page_agent": "Page feed."},
    )
    monkeypatch.setenv("AUTOYOU_TWO_STAGE_ROUTER", "1")
    return tools


def test_router_is_inactive_without_registered_specialists(monkeypatch):
    monkeypatch.setattr(root_agent, "_SPECIALIST_AGENT_TOOLS", {})
    assert root_agent._two_stage_routing_active() is False


@pytest.mark.asyncio
async def test_unreachable_ollama_specialist_returns_actionable_error(monkeypatch):
    monkeypatch.setattr(root_agent, "_SPECIALIST_AGENT_TOOLS", {"autoyou_files_agent": _UnavailableOllamaTool()})
    result = await root_agent.route_to_specialist("files_agent", "List test files", tool_context=object())
    assert result["status"] == "error"
    assert "Start Ollama" in result["message"]
    assert "localhost" not in result["message"]


def test_router_can_be_disabled_by_env(registered_specialists, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TWO_STAGE_ROUTER", "0")
    assert root_agent._two_stage_routing_active() is False


def test_default_two_stage_router_uses_root_schema_capacity_not_callback_profile(monkeypatch):
    monkeypatch.delenv("AUTOYOU_TWO_STAGE_ROUTER", raising=False)
    monkeypatch.delenv("AUTOYOU_AGENT_HARNESS_PROFILE", raising=False)

    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:e4b")
    assert root_agent._two_stage_routing_enabled() is True

    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:26b")
    assert root_agent._two_stage_routing_enabled() is False

    monkeypatch.setenv("OLLAMA_MODEL", "ministral-3:8b")
    assert root_agent._two_stage_routing_enabled() is True


@pytest.mark.parametrize(
    "supplied",
    [
        "autoyou_notes_agent",
        "AUTOYOU_NOTES_AGENT",
        "notes_agent",
        "notes",
        "autoyou-notes-agent",
    ],
)
def test_specialist_names_resolve_despite_model_paraphrasing(registered_specialists, supplied):
    """A correct routing decision must not fail on a naming detail."""
    name, tool = root_agent._resolve_specialist_tool(supplied)
    assert name == "autoyou_notes_agent"
    assert tool is registered_specialists["autoyou_notes_agent"]


def test_unknown_specialist_resolves_to_nothing(registered_specialists):
    _, tool = root_agent._resolve_specialist_tool("autoyou_imaginary_agent")
    assert tool is None


def test_deterministic_route_is_rewritten_to_the_dispatcher(registered_specialists):
    """Naming an unadvertised tool would be a call to a tool that does not exist."""
    response = root_agent._dispatch_specialist_tool_call(
        "autoyou_notes_agent",
        {"request": "list my notes"},
    )
    call = response.content.parts[0].function_call
    assert call.name == root_agent._ROUTER_TOOL_NAME
    assert call.args == {"agent": "autoyou_notes_agent", "request": "list my notes"}


def test_non_specialist_tool_calls_are_left_alone(registered_specialists):
    response = root_agent._dispatch_specialist_tool_call(
        "get_current_datetime",
        {"request": "now"},
    )
    assert response.content.parts[0].function_call.name == "get_current_datetime"


def test_direct_agent_tool_call_survives_when_router_is_off(registered_specialists, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TWO_STAGE_ROUTER", "0")
    response = root_agent._dispatch_specialist_tool_call(
        "autoyou_notes_agent",
        {"request": "list my notes"},
    )
    assert response.content.parts[0].function_call.name == "autoyou_notes_agent"


def test_completed_route_reports_the_specialist_not_the_dispatcher(registered_specialists):
    """Active-agent tracking is what rest_api.py surfaces to clients."""
    resolved = root_agent._resolve_routed_agent_name(
        root_agent._ROUTER_TOOL_NAME,
        {"agent": "notes_agent"},
    )
    assert resolved == "autoyou_notes_agent"


def test_completed_route_for_unknown_specialist_reports_nothing(registered_specialists):
    assert root_agent._resolve_routed_agent_name(
        root_agent._ROUTER_TOOL_NAME, {"agent": "nope"}
    ) == ""


def test_plain_tool_names_pass_through_unchanged(registered_specialists):
    assert root_agent._resolve_routed_agent_name("process_media_content", {}) == "process_media_content"


@pytest.mark.asyncio
async def test_dispatcher_forwards_the_request_to_the_specialist(registered_specialists):
    sentinel = object()
    result = await root_agent.route_to_specialist(
        agent="notes",
        request="create a note titled Groceries with content buy milk",
        tool_context=sentinel,
    )
    notes_tool = registered_specialists["autoyou_notes_agent"]
    assert result == "Note created."
    assert notes_tool.calls == [
        {
            "args": {"request": "create a note titled Groceries with content buy milk"},
            "tool_context": sentinel,
        }
    ]


@pytest.mark.asyncio
async def test_unknown_specialist_returns_an_error_naming_the_alternatives(registered_specialists):
    result = await root_agent.route_to_specialist(
        agent="autoyou_imaginary_agent", request="do a thing", tool_context=object()
    )
    assert result["status"] == "error"
    assert "autoyou_notes_agent" in result["message"]


@pytest.mark.asyncio
async def test_specialist_failure_is_reported_not_raised(registered_specialists):
    class _Exploding:
        async def run_async(self, *, args, tool_context):
            raise RuntimeError("storage sealed")

    registered_specialists["autoyou_notes_agent"] = _Exploding()
    result = await root_agent.route_to_specialist(
        agent="notes", request="list notes", tool_context=object()
    )
    assert result["status"] == "error"
    assert "storage sealed" in result["message"]


def test_router_description_lists_every_specialist(registered_specialists):
    """The catalog is the model's only view of what it can route to."""
    description = root_agent._build_router_tool_description(list(registered_specialists))
    for name in registered_specialists:
        assert name in description
    assert "Notes CRUD." in description


def test_rewrite_rules_for_two_stage():
    prompt = "Notes: call `autoyou_notes_agent`. Memory: use `autoyou_memory_agent`. Coding: route to `autoyou_coding_agent`."
    expected = (
        'Notes: call `route_to_specialist` with `agent` set to "autoyou_notes_agent". '
        'Memory: call `route_to_specialist` with `agent` set to "autoyou_memory_agent". '
        'Coding: call `route_to_specialist` with `agent` set to "autoyou_coding_agent".'
    )
    # from __debug_provenance_e__ import pay
    assert root_agent._rewrite_rules_for_two_stage(prompt) == expected
