# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""OpenRouter configuration and real ADK/LiteLLM transport contracts, offline."""

import copy
import json
import os
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from autoyou_agents import model_config
from shared.openrouter_config import OPENROUTER_API_BASE, normalize_openrouter_model


@pytest.fixture
def openrouter_env(monkeypatch):
    # Also restore environment values written directly by server config sync.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "synthetic/tool-model")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-openrouter-key")
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setattr(model_config, "get_litellm_behavior_kwargs", lambda: {})


@pytest.mark.parametrize("value,expected", [
    (" synthetic/tool-model ", "synthetic/tool-model"),
    ("openrouter/synthetic/tool-model", "synthetic/tool-model"),
    ("openrouter/auto", "openrouter/auto"),
    ("openrouter/openrouter/auto", "openrouter/auto"),
    ("synthetic/tool-model:free", "synthetic/tool-model:free"),
])
def test_model_ids_keep_the_openrouter_namespace(value, expected):
    assert normalize_openrouter_model(value) == expected


@pytest.mark.parametrize("value", ["", "model-without-provider", "/model", "provider/", "provider/model name"])
def test_invalid_model_ids_are_rejected(value):
    with pytest.raises(ValueError, match="provider/model"):
        normalize_openrouter_model(value)


def test_admin_save_preserves_redacts_and_rotates_key(openrouter_env, monkeypatch):
    import server

    original = server._default_config()
    updated, touched, _ = server._apply_admin_ui_config_patch(original, {"ai_provider": {
        "provider": "openrouter",
        "openrouter_model": "openrouter/synthetic/tool-model",
        "openrouter_api_key": " synthetic-saved-openrouter-key ",
    }})
    assert "ai_provider" in touched
    assert original["ai_provider"]["openrouter_api_key"] == ""
    assert updated["ai_provider"]["openrouter_model"] == "synthetic/tool-model"
    for secret_patch in ({}, {"openrouter_api_key": ""}, {"openrouter_api_key": server.MASKED_SECRET_PLACEHOLDER}):
        updated, _, _ = server._apply_admin_ui_config_patch(updated, {"ai_provider": {
            "provider": "openrouter", "openrouter_model": "synthetic/updated-model", **secret_patch,
        }})
        assert updated["ai_provider"]["openrouter_api_key"] == "synthetic-saved-openrouter-key"

    updated, _, _ = server._apply_admin_ui_config_patch(updated, {"ai_provider": {
        "openrouter_api_key": "synthetic-rotated-key",
    }})
    browser_copy = copy.deepcopy(updated)
    server._redact_admin_ai_provider_secrets(browser_copy)
    assert browser_copy["ai_provider"]["openrouter_api_key"] == ""
    assert updated["ai_provider"]["openrouter_api_key"] == "synthetic-rotated-key"

    monkeypatch.setattr(server.STATE, "config", updated)
    monkeypatch.setattr(server, "ollama_service", MagicMock())
    server._apply_google_api_config_to_env()
    assert os.environ["AI_PROVIDER"] == "openrouter"
    assert os.environ["OPENROUTER_MODEL"] == "synthetic/updated-model"
    assert os.environ["OPENROUTER_API_KEY"] == "synthetic-rotated-key"
    assert os.environ["USE_GOOGLE_API"] == "false"
    assert not server._is_native_gateway_provider()


@pytest.mark.parametrize("missing", ["model", "key"])
def test_incomplete_selection_is_rejected_before_saving(openrouter_env, monkeypatch, missing):
    import server

    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    original = server._default_config()
    patch = {"provider": "openrouter", "openrouter_model": "synthetic/tool-model", "openrouter_api_key": "synthetic-key"}
    patch.pop("openrouter_" + ("api_key" if missing == "key" else "model"))
    with pytest.raises(ValueError, match="OpenRouter"):
        server._apply_admin_ui_config_patch(original, {"ai_provider": patch})
    assert original["ai_provider"]["provider"] == "ollama"


def test_environment_only_credentials_survive_config_sync(openrouter_env, monkeypatch):
    import server

    config, _, _ = server._apply_admin_ui_config_patch(server._default_config(), {
        "ai_provider": {"provider": "openrouter"},
    })
    monkeypatch.setattr(server.STATE, "config", config)
    monkeypatch.setattr(server, "ollama_service", MagicMock())
    server._apply_google_api_config_to_env()
    assert os.environ["OPENROUTER_API_KEY"] == "synthetic-openrouter-key"
    assert model_config.get_model_config(None).model == "openrouter/synthetic/tool-model"


def test_startup_does_not_print_provider_credentials(openrouter_env, capsys):
    import server

    server.setup_default_environment_variables()
    output = capsys.readouterr().out
    assert "synthetic-openrouter-key" not in output
    assert "OPENROUTER_API_KEY=(configured)" in output


def test_cloud_model_strips_local_only_options_and_keeps_fallback_endpoint(openrouter_env, monkeypatch):
    import autoyou_agents.agent as root
    import rest_api

    monkeypatch.setattr(model_config, "get_litellm_behavior_kwargs", lambda: {
        "temperature": 0.4, "top_k": 40, "num_ctx": 8192, "repeat_penalty": 1.1,
        "think": True, "num_predict": 1024,
    })
    for model in (model_config.get_model_config(None), root._build_resilient_fallback_model()):
        assert model.model == "openrouter/synthetic/tool-model"
        assert model._additional_args["api_base"] == OPENROUTER_API_BASE
        assert model._additional_args["api_key"] == "synthetic-openrouter-key"
        assert model._additional_args["temperature"] == 0.4
        assert not model_config._OLLAMA_ONLY_KWARGS.intersection(model._additional_args)
    assert rest_api._active_model_name_for_provider("openrouter") == "openrouter/synthetic/tool-model"

    monkeypatch.setenv("AI_PROVIDER", "litellm")
    monkeypatch.setenv("LITELLM_MODEL", "openrouter/synthetic/tool-model")
    monkeypatch.setenv("LITELLM_API_KEY", "synthetic-openrouter-key")
    monkeypatch.setenv("LITELLM_API_BASE", OPENROUTER_API_BASE)
    assert root._build_resilient_fallback_model()._additional_args["api_base"] == OPENROUTER_API_BASE


def test_bad_openrouter_config_never_falls_back_to_another_provider(openrouter_env, monkeypatch):
    import autoyou_agents.agent as root

    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setattr(root, "_reload_prompt_from_disk", lambda: None)
    with pytest.raises(ValueError, match="OpenRouter needs an API key"):
        root.initialize_root_agent()


@pytest.mark.parametrize("two_stage", ["0", "1"])
def test_root_and_installed_specialists_share_openrouter_model(openrouter_env, monkeypatch, two_stage):
    from google.adk.agents import Agent
    import autoyou_agents.agent as root

    created = []
    monkeypatch.setenv("AUTOYOU_TWO_STAGE_ROUTER", two_stage)

    def factory(model):
        specialist = Agent(name="autoyou_notes_agent", model=model, instruction="Handle synthetic notes.")
        created.append(specialist)
        return specialist

    monkeypatch.setattr(root, "root_agent", root.root_agent)
    monkeypatch.setattr(root, "_SPECIALIST_AGENT_TOOLS", {})
    monkeypatch.setattr(root, "_SPECIALIST_AGENT_DESCRIPTIONS", {})
    monkeypatch.setattr(root, "_AVAILABLE_RUNTIME_AGENT_NAMES", set())
    monkeypatch.setattr(root, "_RUNTIME_AGENT_TOOL_NAMES_BY_INSTALL_NAME", {})
    monkeypatch.setattr(root, "_reload_prompt_from_disk", lambda: None)
    monkeypatch.setattr(root, "_refresh_adk_app_binding", lambda agent: None)
    monkeypatch.setattr(root, "get_installed_agent_names", lambda **kwargs: ["notes_agent"])
    monkeypatch.setattr(root, "_load_agent_factory", lambda name: factory)
    agent = root.initialize_root_agent()
    assert created and created[0].model is agent.model
    assert agent.model.model == "openrouter/synthetic/tool-model"
    assert root._provider_requires_explicit_agent_tools()
    assert agent.sub_agents == []
    if two_stage == "1":
        assert root._SPECIALIST_AGENT_TOOLS["autoyou_notes_agent"].agent is created[0]
    else:
        assert any(getattr(tool, "agent", None) is created[0] for tool in agent.tools)
    assert "autoyou_notes_agent" in root._AVAILABLE_RUNTIME_AGENT_NAMES


def test_media_prompt_enhancement_uses_selected_openrouter_model(openrouter_env, monkeypatch):
    import litellm
    from autoyou_agents.media_generation_agent import media_generation_tool

    complete = MagicMock(return_value=litellm.ModelResponse(choices=[{
        "message": {"role": "assistant", "content": "A synthetic mountain landscape."},
    }]))
    monkeypatch.setattr(litellm, "completion", complete)
    assert media_generation_tool.ai_enhance_prompt("A mountain", "image") == "A synthetic mountain landscape."
    assert complete.call_args.kwargs["model"] == "openrouter/synthetic/tool-model"
    assert complete.call_args.kwargs["api_base"] == OPENROUTER_API_BASE
    assert complete.call_args.kwargs["api_key"] == "synthetic-openrouter-key"


@pytest.mark.asyncio
async def test_admin_save_restarts_autoyou_agent_runtime(openrouter_env, monkeypatch):
    import server

    monkeypatch.setattr(server.STATE, "config", server._default_config())
    monkeypatch.setattr(server, "_loaded_config_for_update", lambda: copy.deepcopy(server.STATE.config))
    monkeypatch.setattr(server, "_save_and_reload_state_config", lambda config: config)
    monkeypatch.setattr(server, "ollama_service", MagicMock())
    restart = AsyncMock(return_value=True)
    stop = AsyncMock()
    monkeypatch.setattr(server, "restart_ai_agent_server", restart)
    monkeypatch.setattr(server, "stop_ai_agent_server", stop)
    monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", AsyncMock(return_value={}))
    await server._apply_admin_ui_config_update({"ai_provider": {
        "provider": "openrouter", "openrouter_model": "synthetic/tool-model", "openrouter_api_key": "synthetic-new-key",
    }})
    restart.assert_awaited_once()
    stop.assert_not_awaited()
    assert os.environ["OPENROUTER_API_KEY"] == "synthetic-new-key"
    assert "synthetic/tool-model" in await server._ai_provider_summary()


@pytest.mark.asyncio
async def test_adk_tool_roundtrip_through_openrouter_http(openrouter_env, monkeypatch):
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    requests, tool_calls = [], []

    def lookup_sample(sample_id: str) -> dict:
        """Look up a synthetic sample's colour."""
        tool_calls.append(sample_id)
        return {"colour": "indigo"}

    async def send(client, request, **kwargs):
        assert str(request.url) == OPENROUTER_API_BASE + "/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-openrouter-key"
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "synthetic/tool-model"
        assert payload["tools"][0]["function"]["name"] == "lookup_sample"
        assert not model_config._OLLAMA_ONLY_KWARGS.intersection(payload)
        if len(requests) == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "synthetic-call-1", "type": "function",
                "function": {"name": "lookup_sample", "arguments": '{"sample_id":"sample-42"}'},
            }]}
            finish_reason = "tool_calls"
        else:
            assert len(requests) == 2
            result = next(item for item in payload["messages"] if item["role"] == "tool")
            assert result["tool_call_id"] == "synthetic-call-1"
            assert "indigo" in result["content"]
            message = {"role": "assistant", "content": "The stored colour is indigo."}
            finish_reason = "stop"
        return httpx.Response(200, request=request, json={
            "id": "synthetic-completion", "object": "chat.completion", "created": 1,
            "model": payload["model"], "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    runner = InMemoryRunner(agent=Agent(
        name="synthetic_autoyou_agent", model=model_config.get_model_config(None),
        instruction="Use the lookup tool for sample data.", tools=[lookup_sample],
    ), app_name="synthetic_openrouter")
    try:
        session = await runner.session_service.create_session(app_name="synthetic_openrouter", user_id="synthetic-user")
        events = [event async for event in runner.run_async(
            user_id="synthetic-user", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="Look up sample-42.")]),
        )]
    finally:
        await runner.close()
    assert tool_calls == ["sample-42"]
    assert len(requests) == 2
    assert events[-1].content.parts[0].text == "The stored colour is indigo."
