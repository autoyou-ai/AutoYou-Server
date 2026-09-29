# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-fa52b3077067f670c83096f5


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
from types import SimpleNamespace

import pytest
from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
import google.adk.models.lite_llm as adk_lite_llm

import autoyou_agents.agent as root_agent_module

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-fa52b3077067f670c83096f5"


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


def _router_specialist_choices(root_agent) -> set[str]:
    """Specialist names the model is actually offered by the dispatcher.

    Under two-stage routing the root instruction deliberately stops listing
    specialists (see `_build_router_instruction_lines`: repeating all 32 entries
    doubled the system prompt for no added information). The names the model
    reads now live in the `route_to_specialist` schema as a JSON-schema `enum`,
    so that is where "can the model name this specialist?" has to be asserted.
    """
    for tool in root_agent.tools:
        if _tool_name(tool) != root_agent_module._ROUTER_TOOL_NAME:
            continue
        declaration = tool._get_declaration()
        schema = getattr(declaration, "parameters_json_schema", None) or {}
        enum = (schema.get("properties", {}).get("agent", {}) or {}).get("enum")
        if enum:
            return set(enum)
        # Prose-catalog fallback path (enum construction failed): the names are
        # in the description instead.
        return {
            name
            for name in root_agent_module._SPECIALIST_AGENT_TOOLS
            if name in (declaration.description or "")
        }
    return set()


def _routable_names(root_agent) -> set[str]:
    """Every specialist the root can reach, however it dispatches.

    Under two-stage routing specialists live in the dispatcher's registry rather
    than the advertised tool list, so asserting only on `root_agent.tools` would
    check the wire shape instead of whether the specialist is reachable.
    """
    return {_tool_name(tool) for tool in root_agent.tools} | set(
        root_agent_module._SPECIALIST_AGENT_TOOLS
    )


def test_effective_instruction_filters_factory_catalog_to_installed_agents(monkeypatch):
    sections = {
        "INTRODUCTION": "You are AutoYou.",
        "CORE_BEHAVIOR": "Answer directly.",
        "SUB_AGENTS_SECTION": (
            "Agent-routing tools:\n"
            "- Notes: `autoyou_notes_agent`\n"
            "- Internet: `autoyou_internet_agent`"
        ),
        "ROUTING_RULES_SECTION": (
            "Routing rules:\n"
            "- Notes requests: call `autoyou_notes_agent`.\n"
            "- Live web requests: call `autoyou_internet_agent`."
        ),
        "ATTACHMENTS_POLICY": "Forward attachments.",
        "SPECIAL_POLICIES": "Use the system clock.",
        "CONVERSATION_POLICY": "Keep continuity.",
        "SAFETY_RULES": "Follow safety rules.",
    }
    factory_instruction = "\n\n".join(sections.values())
    monkeypatch.setattr(
        root_agent_module,
        "root_prompt",
        SimpleNamespace(**{**sections, "AGENT_INSTRUCTION": factory_instruction}),
    )
    monkeypatch.setattr(
        root_agent_module,
        "_build_registry_defined_agent_sections",
        lambda *args, **kwargs: ([], []),
    )
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: False)
    monkeypatch.setattr(root_agent_module, "_two_stage_routing_enabled", lambda: False)

    instruction = root_agent_module._build_effective_agent_instruction(["notes_agent"])

    assert "`autoyou_notes_agent`" in instruction
    assert "`autoyou_internet_agent`" not in instruction
    assert "Forward attachments." in instruction


def test_custom_instruction_is_preserved_and_warns_about_unavailable_agents(monkeypatch, caplog):
    sections = {
        "INTRODUCTION": "You are AutoYou.",
        "CORE_BEHAVIOR": "Answer directly.",
        "SUB_AGENTS_SECTION": "- Notes: `autoyou_notes_agent`",
        "ROUTING_RULES_SECTION": "- Notes requests: call `autoyou_notes_agent`.",
        "ATTACHMENTS_POLICY": "Forward attachments.",
        "SPECIAL_POLICIES": "Use the system clock.",
        "CONVERSATION_POLICY": "Keep continuity.",
        "SAFETY_RULES": "Follow safety rules.",
    }
    factory_instruction = "\n\n".join(sections.values())
    custom_instruction = factory_instruction + "\n\nCustom policy: never use `autoyou_internet_agent`."
    monkeypatch.setattr(
        root_agent_module,
        "root_prompt",
        SimpleNamespace(**{**sections, "AGENT_INSTRUCTION": custom_instruction}),
    )
    monkeypatch.setattr(
        root_agent_module,
        "_build_registry_defined_agent_sections",
        lambda *args, **kwargs: ([], []),
    )
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: False)
    monkeypatch.setattr(root_agent_module, "_two_stage_routing_enabled", lambda: False)

    with caplog.at_level("WARNING"):
        instruction = root_agent_module._build_effective_agent_instruction(["notes_agent"])

    assert custom_instruction in instruction
    assert "Custom root prompt references unavailable agents" in caplog.text
    assert "autoyou_internet_agent" in caplog.text


def test_custom_instruction_filters_bulleted_uninstalled_agents_from_effective_prompt(monkeypatch):
    sections = {
        "INTRODUCTION": "You are AutoYou.",
        "CORE_BEHAVIOR": "Answer directly.",
        "SUB_AGENTS_SECTION": "- Notes: `autoyou_notes_agent`\n- OpenClaw: `autoyou_openclaw_agent`",
        "ROUTING_RULES_SECTION": "- Notes: call `autoyou_notes_agent`.\n- OpenClaw: call `autoyou_openclaw_agent`.",
        "ATTACHMENTS_POLICY": "Forward attachments.",
        "SPECIAL_POLICIES": "Use the system clock.",
        "CONVERSATION_POLICY": "Keep continuity.",
        "SAFETY_RULES": "Follow safety rules.",
    }
    factory_instruction = "\n\n".join(sections.values())
    custom_instruction = factory_instruction + "\n\nCustom operator notes."
    monkeypatch.setattr(
        root_agent_module,
        "root_prompt",
        SimpleNamespace(**{**sections, "AGENT_INSTRUCTION": custom_instruction}),
    )
    monkeypatch.setattr(
        root_agent_module,
        "_build_registry_defined_agent_sections",
        lambda *args, **kwargs: ([], []),
    )
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: False)
    monkeypatch.setattr(root_agent_module, "_two_stage_routing_enabled", lambda: False)

    instruction = root_agent_module._build_effective_agent_instruction(["notes_agent"])

    assert "`autoyou_notes_agent`" in instruction
    assert "`autoyou_openclaw_agent`" not in instruction
    assert "Custom operator notes." in instruction


def test_initialize_root_agent_after_registry_change_rebuilds_graph_and_prompt(monkeypatch):
    sections = {
        "AGENT_NAME": "autoyou_agent",
        "AGENT_DESCRIPTION": "Synthetic root agent.",
        "INTRODUCTION": "You are AutoYou.",
        "CORE_BEHAVIOR": "Answer directly.",
        "SUB_AGENTS_SECTION": (
            "- Notes: `autoyou_notes_agent`\n"
            "- Internet: `autoyou_internet_agent`"
        ),
        "ROUTING_RULES_SECTION": (
            "- Notes requests: call `autoyou_notes_agent`.\n"
            "- Live web requests: call `autoyou_internet_agent`."
        ),
        "ATTACHMENTS_POLICY": "Forward attachments.",
        "SPECIAL_POLICIES": "Use the system clock.",
        "CONVERSATION_POLICY": "Keep continuity.",
        "SAFETY_RULES": "Follow safety rules.",
    }
    sections["AGENT_INSTRUCTION"] = "\n\n".join(
        sections[name]
        for name in (
            "INTRODUCTION",
            "CORE_BEHAVIOR",
            "SUB_AGENTS_SECTION",
            "ROUTING_RULES_SECTION",
            "ATTACHMENTS_POLICY",
            "SPECIAL_POLICIES",
            "CONVERSATION_POLICY",
            "SAFETY_RULES",
        )
    )
    # from __debug_provenance_y__ import legal
    monkeypatch.setattr(root_agent_module, "root_prompt", SimpleNamespace(**sections))
    monkeypatch.setattr(root_agent_module, "get_model_config", lambda _: LiteLlm(model="synthetic/model"))
    monkeypatch.setattr(
        root_agent_module,
        "get_service_manager",
        lambda: SimpleNamespace(config=SimpleNamespace(internet_search_enabled=True, audio_playback_enabled=True)),
    )
    monkeypatch.setattr(root_agent_module, "_reload_prompt_from_disk", lambda: None)
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: False)
    monkeypatch.setattr(root_agent_module, "_two_stage_routing_enabled", lambda: False)
    monkeypatch.setattr(
        root_agent_module,
        "_build_registry_defined_agent_sections",
        lambda *args, **kwargs: ([], []),
    )
    factory_map = {
        "notes_agent": lambda model_config: _dummy_agent("autoyou_notes_agent"),
        "internet_agent": lambda model_config: _dummy_agent("autoyou_internet_agent"),
    }
    monkeypatch.setattr(root_agent_module, "_load_agent_factory", lambda name: factory_map.get(name))
    installed_agents = ["notes_agent", "internet_agent"]
    monkeypatch.setattr(
        root_agent_module,
        "get_installed_agent_names",
        lambda agents_root=None: list(installed_agents),
    )

    first_root = root_agent_module.initialize_root_agent()
    assert {agent.name for agent in first_root.sub_agents} == {
        "autoyou_notes_agent",
        "autoyou_internet_agent",
    }
    assert "`autoyou_internet_agent`" in first_root.instruction

    installed_agents[:] = ["notes_agent"]
    second_root = root_agent_module.initialize_root_agent()

    assert {agent.name for agent in second_root.sub_agents} == {"autoyou_notes_agent"}
    assert "`autoyou_notes_agent`" in second_root.instruction
    assert "`autoyou_internet_agent`" not in second_root.instruction


@pytest.mark.parametrize(
    "model_name",
    [
        "ollama_chat/ministral-3:8b",
        "ollama_chat/gemma4:e4b",
    ],
)
def test_initialize_root_agent_wraps_sub_agents_as_tools_for_ollama(monkeypatch, model_name):
    installed_agents = ["internet_agent", "notes_agent", "page_agent", "audio_agent", "memory_agent", "persona_agent"]
    factory_map = {
        "internet_agent": lambda model_config: _dummy_agent("autoyou_internet_agent"),
        "notes_agent": lambda model_config: _dummy_agent("autoyou_notes_agent"),
        "page_agent": lambda model_config: _dummy_agent("autoyou_page_agent"),
        "audio_agent": lambda model_config: _dummy_agent("autoyou_audio_agent"),
        "memory_agent": lambda model_config: _dummy_agent("autoyou_memory_agent"),
        "persona_agent": lambda model_config: _dummy_agent("autoyou_persona_agent"),
    }

    monkeypatch.setattr(
        root_agent_module,
        "get_model_config",
        lambda _: LiteLlm(model=model_name, api_base="http://localhost:11434"),
    )
    monkeypatch.setenv("OLLAMA_MODEL", model_name.removeprefix("ollama_chat/"))
    monkeypatch.setattr(
        root_agent_module,
        "get_service_manager",
        lambda: SimpleNamespace(config=SimpleNamespace(internet_search_enabled=True, audio_playback_enabled=True)),
    )
    monkeypatch.setattr(root_agent_module, "get_installed_agent_names", lambda agents_root=None: installed_agents)
    monkeypatch.setattr(root_agent_module, "_load_agent_factory", lambda agent_name: factory_map.get(agent_name))
    monkeypatch.setattr(root_agent_module, "_reload_prompt_from_disk", lambda: None)

    root_agent = root_agent_module.initialize_root_agent()

    tool_names = {_tool_name(tool) for tool in root_agent.tools}
    routable = _routable_names(root_agent)
    sub_agent_names = {agent.name for agent in root_agent.sub_agents}

    assert "autoyou_memory_agent" in tool_names
    assert {"read_persona", "append_persona"} <= tool_names
    assert "autoyou_internet_agent" in routable
    assert "autoyou_notes_agent" in routable
    assert "autoyou_page_agent" in routable
    assert "autoyou_audio_agent" in routable
    assert "autoyou_internet_agent" not in sub_agent_names
    assert "autoyou_notes_agent" not in sub_agent_names
    assert "autoyou_page_agent" not in sub_agent_names
    assert "autoyou_audio_agent" not in sub_agent_names

    # The reason the dispatcher exists: the advertised tool count must not grow
    # with the number of installed specialists.
    assert root_agent_module._ROUTER_TOOL_NAME in tool_names
    assert not {"autoyou_internet_agent", "autoyou_notes_agent"} & tool_names


def test_initialize_root_agent_does_not_fallback_to_sub_agents_when_agenttool_wrap_fails(monkeypatch):
    installed_agents = ["internet_agent", "memory_agent"]
    factory_map = {
        "internet_agent": lambda model_config: _dummy_agent("autoyou_internet_agent"),
        "memory_agent": lambda model_config: _dummy_agent("autoyou_memory_agent"),
    }

    real_agent_tool = root_agent_module.AgentTool

    class ConditionalAgentTool:
        def __new__(cls, agent):
            if agent.name == "autoyou_internet_agent":
                raise RuntimeError("wrap failure")
            return real_agent_tool(agent)

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
    monkeypatch.setattr(root_agent_module, "AgentTool", ConditionalAgentTool)

    root_agent = root_agent_module.initialize_root_agent()

    tool_names = {_tool_name(tool) for tool in root_agent.tools}
    sub_agent_names = {agent.name for agent in root_agent.sub_agents}

    assert "autoyou_memory_agent" in tool_names
    assert "autoyou_internet_agent" not in tool_names
    assert sub_agent_names == set()


def test_initialize_root_agent_wraps_desktop_bridges_as_tools_for_ollama(monkeypatch):
    installed_agents = ["claude_desktop_agent", "codex_desktop_agent"]
    factory_map = {
        "claude_desktop_agent": lambda model_config: _dummy_agent("claude_desktop_agent"),
        "codex_desktop_agent": lambda model_config: _dummy_agent("codex_desktop_agent"),
    }

    monkeypatch.setattr(
        root_agent_module,
        "get_model_config",
        lambda _: LiteLlm(model="ollama_chat/ministral-3:3b", api_base="http://localhost:11434"),
    )
    monkeypatch.setattr(
        root_agent_module,
        "get_service_manager",
        lambda: SimpleNamespace(config=SimpleNamespace(internet_search_enabled=True, audio_playback_enabled=True)),
    )
    monkeypatch.setattr(root_agent_module, "get_installed_agent_names", lambda agents_root=None: installed_agents)
    monkeypatch.setattr(root_agent_module, "_load_agent_factory", lambda agent_name: factory_map.get(agent_name))
    monkeypatch.setattr(root_agent_module, "_reload_prompt_from_disk", lambda: None)

    root_agent = root_agent_module.initialize_root_agent()

    routable = _routable_names(root_agent)
    sub_agent_names = {agent.name for agent in root_agent.sub_agents}

    assert "claude_desktop_agent" in routable
    assert "codex_desktop_agent" in routable
    assert "claude_desktop_agent" not in sub_agent_names
    assert "codex_desktop_agent" not in sub_agent_names
    # The desktop bridges must be nameable by the model, not merely reachable
    # by the runtime - otherwise it can route to everything except them.
    router_choices = _router_specialist_choices(root_agent)
    assert "claude_desktop_agent" in router_choices
    assert "codex_desktop_agent" in router_choices


def test_initialize_root_agent_keeps_desktop_graph_when_provider_starts_late(monkeypatch):
    installed_agents = ["claude_desktop_agent", "codex_desktop_agent"]
    factory_map = {
        "claude_desktop_agent": lambda model_config: _dummy_agent("claude_desktop_agent"),
        "codex_desktop_agent": lambda model_config: _dummy_agent("codex_desktop_agent"),
    }

    monkeypatch.setattr(
        root_agent_module,
        "get_model_config",
        lambda _: (_ for _ in ()).throw(ConnectionError("synthetic Ollama startup race")),
    )
    monkeypatch.setattr(
        root_agent_module,
        "_build_resilient_fallback_model",
        lambda: LiteLlm(model="ollama_chat/synthetic-model:1b", api_base="http://127.0.0.1:11434"),
    )
    monkeypatch.setattr(
        root_agent_module,
        "get_service_manager",
        lambda: SimpleNamespace(config=SimpleNamespace(internet_search_enabled=True, audio_playback_enabled=True)),
    )
    monkeypatch.setattr(root_agent_module, "get_installed_agent_names", lambda agents_root=None: installed_agents)
    monkeypatch.setattr(root_agent_module, "_load_agent_factory", lambda agent_name: factory_map.get(agent_name))
    monkeypatch.setattr(root_agent_module, "_reload_prompt_from_disk", lambda: None)

    root_agent = root_agent_module.initialize_root_agent()

    assert {"claude_desktop_agent", "codex_desktop_agent"} <= _routable_names(root_agent)
    assert root_agent_module._ROUTER_TOOL_NAME in {_tool_name(tool) for tool in root_agent.tools}
    assert root_agent.before_model_callback, "deterministic router callbacks must survive degraded startup"


def test_adk_tool_history_repair_patch_heals_dangling_tool_results():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_y0k79bmu",
                    "type": "function",
                    "function": {"name": "autoyou_internet_agent", "arguments": "{}"},
                }
            ],
        },
        {"role": "user", "content": "follow up"},
    ]

    prepared = adk_lite_llm._ensure_tool_results(messages)

    assert prepared[1]["role"] == "tool"
    assert prepared[1]["tool_call_id"] == "call_y0k79bmu"
    assert "Missing tool result" in prepared[1]["content"]


def test_adk_tool_history_repair_patch_accepts_adk_2_model_argument():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_gemma4",
                    "type": "function",
                    "function": {"name": "autoyou_page_agent", "arguments": "{}"},
                }
            ],
        },
        {"role": "user", "content": "follow up"},
    ]

    prepared = adk_lite_llm._ensure_tool_results(messages, "ollama_chat/gemma4:12b")

    assert prepared[1]["role"] == "tool_responses"
    assert prepared[1]["tool_call_id"] == "call_gemma4"
    assert "Missing tool result" in prepared[1]["content"]


class _TruncatedToolCallError(Exception):
    """Mirrors the LiteLLM error raised when Ollama rejects a half-written call."""

    def __init__(self):
        super().__init__(
            "litellm.APIConnectionError: Ollama_chatException - "
            '{"error":"error parsing tool call: raw=\'{\\"url\\":\\"https://www.cnn.com/2026/08/05/world\', '
            'err=unexpected end of JSON input"}'
        )


def _ollama_completion_kwargs(**overrides):
    kwargs = {
        "model": "ollama_chat/gpt-oss:120b",
        "messages": [{"role": "user", "content": "summarize the news"}],
        "tools": [{"type": "function", "function": {"name": "internet_search"}}],
        "num_predict": 1024,
    }
    kwargs.update(overrides)
    return kwargs


async def test_truncated_ollama_tool_call_retries_with_more_room(monkeypatch):
    """A tool call cut off mid-JSON gets a second try with a bigger budget."""
    attempts = []

    async def _fake_acompletion(*args, **kwargs):
        attempts.append(dict(kwargs))
        if len(attempts) == 1:
            raise _TruncatedToolCallError()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])

    monkeypatch.setattr(root_agent_module, "_original_acompletion", _fake_acompletion)

    response = await root_agent_module._patched_acompletion(**_ollama_completion_kwargs())

    assert len(attempts) == 2
    assert attempts[0]["num_predict"] == 1024
    assert attempts[1]["num_predict"] == 4096  # 4x the cap that truncated
    assert attempts[1]["tools"], "the retry should still offer tools"
    assert response.choices[0].message.content == "ok"


@pytest.mark.parametrize(
    "num_predict, num_ctx, expected",
    [
        (1024, None, 4096),          # 4x the cap that truncated
        (1024, 16384, 4096),         # comfortably inside the window
        (1024, 4096, 2048),          # held under half the window
        (512, 2048, 1024),           # scales with the operator's own cap
        (4096, 32768, 8192),         # ceiling caps the raise
        (8192, 32768, None),         # already at the ceiling: nothing to gain
        (1024, 1024, None),          # window leaves no room; go tool-free
        (0, 16384, None),            # cap disabled by the operator
        (None, 16384, None),         # no cap set: truncation was contextual
    ],
)
def test_num_predict_raise_respects_the_context_window(num_predict, num_ctx, expected):
    """Raising the cap past what the window leaves unused cannot help."""
    kwargs = {}
    if num_predict is not None:
        kwargs["num_predict"] = num_predict
    if num_ctx is not None:
        kwargs["num_ctx"] = num_ctx

    assert root_agent_module._raised_num_predict_for_truncated_tool_call(kwargs) == expected


def test_num_predict_raise_honours_an_operator_pin(monkeypatch):
    monkeypatch.setenv("AUTOYOU_OLLAMA_TRUNCATED_TOOL_CALL_NUM_PREDICT", "3000")

    assert root_agent_module._raised_num_predict_for_truncated_tool_call(
        {"num_predict": 1024, "num_ctx": 16384}
    ) == 3000


async def test_repeated_truncated_ollama_tool_calls_fall_back_to_a_written_answer(monkeypatch):
    """When more room does not help, drop the tools so the turn can still answer."""
    attempts = []

    async def _fake_acompletion(*args, **kwargs):
        attempts.append(dict(kwargs))
        if kwargs.get("tools"):
            raise _TruncatedToolCallError()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Here is the news I gathered.", tool_calls=None))]
        )

    monkeypatch.setattr(root_agent_module, "_original_acompletion", _fake_acompletion)

    response = await root_agent_module._patched_acompletion(**_ollama_completion_kwargs())

    assert len(attempts) == 3
    assert attempts[-1]["tools"] is None
    assert response.choices[0].message.content == "Here is the news I gathered."


async def test_truncated_ollama_tool_call_without_a_cap_skips_straight_to_toolless_retry(monkeypatch):
    """No explicit num_predict means no cap to raise, so do not invent one."""
    attempts = []

    async def _fake_acompletion(*args, **kwargs):
        attempts.append(dict(kwargs))
        if kwargs.get("tools"):
            raise _TruncatedToolCallError()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="answer", tool_calls=None))])

    monkeypatch.setattr(root_agent_module, "_original_acompletion", _fake_acompletion)

    kwargs = _ollama_completion_kwargs()
    kwargs.pop("num_predict")
    await root_agent_module._patched_acompletion(**kwargs)

    assert len(attempts) == 2
    assert "num_predict" not in attempts[-1]
    assert attempts[-1]["tools"] is None


async def test_truncated_tool_call_error_still_raised_when_no_recovery_is_left(monkeypatch):
    """Recovery is bounded - a persistently failing call still surfaces."""

    async def _fake_acompletion(*args, **kwargs):
        raise _TruncatedToolCallError()

    monkeypatch.setattr(root_agent_module, "_original_acompletion", _fake_acompletion)

    with pytest.raises(_TruncatedToolCallError):
        await root_agent_module._patched_acompletion(**_ollama_completion_kwargs())


def test_adk_tool_safety_net_converts_root_self_call_to_direct_answer_guard():
    import google.adk.flows.llm_flows.functions as adk_functions

    function_call = SimpleNamespace(
        name="autoyou_agent",
        args={"request": "Hi what are you?"},
    )

    tool = adk_functions._get_tool(
        function_call,
        {
            "get_current_datetime": object(),
            "autoyou_page_agent": object(),
        },
    )

    assert getattr(tool, "name", "") == "autoyou_agent"
    assert function_call.args == {"request": "Hi what are you?"}
