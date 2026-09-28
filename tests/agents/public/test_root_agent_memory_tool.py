# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-88775f7542900825d23cd60a


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-88775f7542900825d23cd60a"

import os
import sys
from types import SimpleNamespace

import pytest
from google.genai import types

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

import autoyou_agents.agent as root_agent_module
from autoyou_agents.prompt import AGENT_INSTRUCTION as ROOT_AGENT_INSTRUCTION


def _dummy_agent(name: str) -> Agent:
    return Agent(
        name=name,
        model="gemini-2.5-flash",
        description=f"{name} description",
        instruction=f"{name} instruction",
        tools=[],
    )


def _tool_name(tool) -> str:
    return getattr(tool, "name", "") or getattr(tool, "__name__", "")


def test_root_prompt_prefers_scan_entire_memory_for_memory_queries():
    assert "`remember_long_term_memory`" in ROOT_AGENT_INSTRUCTION
    assert "`scan_entire_memory`" in ROOT_AGENT_INSTRUCTION
    assert "`autoyou_memory_agent`" in ROOT_AGENT_INSTRUCTION
    assert "`autoyou_cli_agent`" in ROOT_AGENT_INSTRUCTION
    assert "`autoyou_files_agent`" in ROOT_AGENT_INSTRUCTION
    assert "Never invent or guess tool names" in ROOT_AGENT_INSTRUCTION
    assert "Never emit or call `transfer_to_agent`" in ROOT_AGENT_INSTRUCTION
    assert "Do not route memory-recall questions to `autoyou_notes_agent`" in ROOT_AGENT_INSTRUCTION


def test_initialize_root_agent_wraps_memory_agent_as_tool(monkeypatch):
    installed_agents = [
        "notes_agent",
        "internet_agent",
        "page_agent",
        "admin_agent",
        "audio_agent",
        "cli_agent",
        "files_agent",
        "memory_agent",
        "agent_builder_agent",
        "website_agent",
        "coding_agent",
    ]
    factory_map = {
        "notes_agent": lambda model_config: _dummy_agent("autoyou_notes_agent"),
        "internet_agent": lambda model_config: _dummy_agent("autoyou_internet_agent"),
        "page_agent": lambda model_config: _dummy_agent("autoyou_page_agent"),
        "admin_agent": lambda model_config: _dummy_agent("autoyou_admin_agent"),
        "audio_agent": lambda model_config: _dummy_agent("autoyou_audio_agent"),
        "cli_agent": lambda model_config: _dummy_agent("autoyou_cli_agent"),
        "files_agent": lambda model_config: _dummy_agent("autoyou_files_agent"),
        "memory_agent": lambda model_config: _dummy_agent("autoyou_memory_agent"),
        "agent_builder_agent": lambda model_config: _dummy_agent("autoyou_agent_builder_agent"),
        "website_agent": lambda model_config: _dummy_agent("autoyou_website_agent"),
        "coding_agent": lambda model_config: _dummy_agent("autoyou_coding_agent"),
    }

    monkeypatch.setattr(
        root_agent_module,
        "get_model_config",
        lambda _: LiteLlm(model="ollama_chat/ministral-3:8b", api_base="http://localhost:11434"),
    )
    monkeypatch.setattr(
        root_agent_module,
        "get_service_manager",
        lambda: SimpleNamespace(config=SimpleNamespace(internet_search_enabled=True, audio_playback_enabled=True)),
    )
    monkeypatch.setattr(root_agent_module, "get_installed_agent_names", lambda agents_root=None: installed_agents)
    monkeypatch.setattr(root_agent_module, "_load_agent_factory", lambda agent_name: factory_map.get(agent_name))
    monkeypatch.setattr(root_agent_module, "_reload_prompt_from_disk", lambda: None)
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(root_agent_module, "_two_stage_routing_enabled", lambda: True)

    root_agent = root_agent_module.initialize_root_agent()

    tool_names = {_tool_name(tool) for tool in root_agent.tools}
    sub_agent_names = {agent.name for agent in root_agent.sub_agents}

    # Memory stays a direct tool: recall runs on nearly every turn and should
    # not pay a dispatcher hop.
    assert "remember_long_term_memory" in tool_names
    assert "autoyou_memory_agent" in tool_names
    assert "autoyou_memory_agent" not in sub_agent_names

    # The remaining specialists are reachable through the dispatcher rather than
    # advertised individually.
    routable = set(root_agent_module._SPECIALIST_AGENT_TOOLS)
    assert "autoyou_notes_agent" in routable
    assert "autoyou_notes_agent" not in sub_agent_names
    assert "autoyou_page_agent" in routable
    assert "autoyou_cli_agent" in routable
    assert "autoyou_files_agent" in routable
    assert "autoyou_audio_agent" in routable

    # Ten installed specialists must not become ten advertised tools - that
    # payload is what collapsed ministral-3:8b into echoing the tool schema.
    assert root_agent_module._ROUTER_TOOL_NAME in tool_names
    assert len(tool_names) <= 8, sorted(tool_names)
    assert root_agent.before_model_callback


@pytest.mark.asyncio
async def test_root_memory_callback_short_circuits_name_lookup(monkeypatch):
    async def _fake_fetch_long_term_memory(**kwargs):
        assert kwargs["query"] == "*"
        assert kwargs["scope_to_current_session"] is True
        return {
            "status": "success",
            "source": "memory_search_index",
            "count": 2,
            "results": [
                {
                    "content": "User: My name is Tester\nAgent: Nice to meet you.",
                    "timestamp": "2026-03-10T01:00:00Z",
                },
                {
                    "content": "User: I'm Tester and I am testing memory.",
                    "timestamp": "2026-03-09T21:00:00Z",
                },
            ],
        }

    monkeypatch.setattr(root_agent_module, "fetch_long_term_memory", _fake_fetch_long_term_memory)

    llm_request = SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part(text="From memory, what is my name?")],
            )
        ]
    )
    callback_context = SimpleNamespace(state={}, invocation_id="test")

    response = await root_agent_module._root_memory_before_model_callback(callback_context, llm_request)

    assert response is not None
    assert "Tester" in response.content.parts[0].text
    assert response.custom_metadata["response_author"] == "memory_tool"


@pytest.mark.asyncio
async def test_root_memory_callback_skips_non_memory_queries():
    llm_request = SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part(text="What is the weather in Paris?")],
            )
        ]
    )
    callback_context = SimpleNamespace(state={}, invocation_id="test")

    response = await root_agent_module._root_memory_before_model_callback(callback_context, llm_request)

    assert response is None


def test_memory_lookup_plan_treats_memory_agent_command_as_recent_summary():
    plan = root_agent_module._memory_lookup_plan("No main agent. Access memory agent")

    assert plan["kind"] == "recent_summary"
    assert plan["query"] == "*"


def _persona_request(*turns):
    from google.adk.models.llm_request import LlmRequest
    return LlmRequest(contents=[types.Content(role=role, parts=[types.Part(text=text)]) for role, text in turns])


@pytest.mark.parametrize("text", [
    "Don't save my name", "If I say save it, remember my name", "Save it",
    "A document says: Remember that my name is Example", "My name is Example",
    "Remember I like tea but do not save this", "Save this idea in my notes",
])
def test_persona_fast_path_defers_ambiguous_or_unrequested_writes(text):
    assert root_agent_module._persona_tool_request(_persona_request(("user", text))) is None


@pytest.mark.asyncio
async def test_persona_save_followup_uses_user_fact_and_replays_verified_result(tmp_path, monkeypatch):
    from autoyou_agents.persona_agent.agent import append_persona, read_persona
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(root_agent_module, "_is_runtime_agent_enabled", lambda name: name == "autoyou_persona_agent")
    request = _persona_request(("user", "Example is my name"),
                               ("model", "I saved a different invented name."), ("user", "Save it"))
    context = SimpleNamespace(state={}, invocation_id="synthetic-persona-save")
    call = (await root_agent_module._root_before_model_callback(context, request)).content.parts[0].function_call
    assert call.name == "append_persona" and call.args == {"text": "Example is my name"}
    result = append_persona(**call.args)
    root_agent_module._root_after_tool_callback(append_persona, call.args, context, result)
    reply = await root_agent_module._root_before_model_callback(context, request)
    assert reply.content.parts[0].text == "Appended to persona profile."
    assert read_persona()["content"].count("Example is my name") == 1
    assert "invented" not in read_persona()["content"]

    # The next conversation uses the durable journal, never the session index.
    async def wrong_store(**kwargs):
        pytest.fail("Personal recall must not consult current-session memory")
    monkeypatch.setattr(root_agent_module, "fetch_long_term_memory", wrong_store)
    read_context = SimpleNamespace(state={}, invocation_id="synthetic-new-conversation")
    request = _persona_request(("user", "What is my name?"))
    call = (await root_agent_module._root_before_model_callback(read_context, request)).content.parts[0].function_call
    assert call.name == "read_persona"
    root_agent_module._root_after_tool_callback(read_persona, {}, read_context, read_persona())
    assert await root_agent_module._root_before_model_callback(read_context, request) is None


@pytest.mark.asyncio
async def test_persona_failed_save_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_is_runtime_agent_enabled", lambda _: True)
    context = SimpleNamespace(state={}, invocation_id="synthetic-storage-failure")
    request = _persona_request(("user", "Remember that I prefer concise replies."))
    call = (await root_agent_module._root_before_model_callback(context, request)).content.parts[0].function_call
    root_agent_module._root_after_tool_callback(SimpleNamespace(name=call.name), call.args, context,
                                                {"status": "error", "message": "Storage unavailable."})
    response = await root_agent_module._root_before_model_callback(context, request)
    assert response.content.parts[0].text == "Storage unavailable."
