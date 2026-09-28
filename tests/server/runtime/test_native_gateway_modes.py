# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Small contracts for direct Ollama and Odysseus gateway modes."""

from __future__ import annotations

import copy
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import rest_api
import server
from routers import agents as agent_routes
from routers.models import register_routes as register_model_routes
from shared import ollama_gateway


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", [{"done": False}, {"done": True, "done_reason": "length"}, {}])
async def test_incomplete_ollama_response_does_not_enter_conversation(monkeypatch, completion):
    payload = {"message": {"content": "synthetic unfinished reply"}, **completion}
    sent = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def json(self):
            return payload

        def post(self, _url, *, json):
            sent.append(copy.deepcopy(json))
            return self

    monkeypatch.setattr(ollama_gateway.aiohttp, "ClientSession", lambda **_: Response())
    store = ollama_gateway.OllamaConversationStore()
    store.append_user("synthetic-session", "earlier prompt")
    store.append_assistant("synthetic-session", "earlier answer")
    before = copy.deepcopy(store.messages("synthetic-session", "synthetic system"))
    kwargs = dict(store=store, ollama_api="http://127.0.0.1:11434", model="synthetic-model",
                  session_id="synthetic-session", system_prompt="synthetic system")

    with pytest.raises(ollama_gateway.OllamaGatewayError, match="complete|limit"):
        await ollama_gateway.call_ollama_with_history(message="failed prompt", **kwargs)
    assert store.messages("synthetic-session", "synthetic system") == before

    payload = {"message": {"content": "synthetic complete reply"}, "done": True, "done_reason": "stop"}
    result = await ollama_gateway.call_ollama_with_history(message="retry prompt", **kwargs)
    assert result["response"] == "synthetic complete reply"
    assert sent[-1]["messages"] == [*before, {"role": "user", "content": "retry prompt"}]
    assert store.messages("synthetic-session", "synthetic system")[-1] == {
        "role": "assistant", "content": "synthetic complete reply"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "sender_name", "result", "expected_name"),
    (
        (
            "ollama_gateway",
            "send_message_to_native_ollama",
            {"response": "synthetic native Ollama reply", "model": "synthetic-ollama"},
            "Ollama",
        ),
        (
            "odysseus",
            "send_message_to_odysseus_gateway",
            {"response": "synthetic Odysseus reply", "model": "synthetic-odysseus"},
            "Odysseus",
        ),
    ),
)
async def test_native_gateway_chat_bypasses_agent_graph(monkeypatch, provider, sender_name, result, expected_name):
    async def fake_sender(*_args, **_kwargs):
        return dict(result)

    monkeypatch.setattr(rest_api, "_active_ai_provider", lambda: provider)
    monkeypatch.setattr(rest_api, sender_name, fake_sender)
    monkeypatch.setattr(
        rest_api,
        "_request_requires_autoyou_agent_graph",
        lambda _request: (_ for _ in ()).throw(AssertionError("direct mode must not classify via ADK routing")),
    )
    monkeypatch.setattr(
        rest_api,
        "get_session_manager",
        lambda: (_ for _ in ()).throw(AssertionError("direct mode must not create an ADK session")),
    )

    response = await rest_api.process_chat_message(
        rest_api.ChatRequest(
            message="Find the latest current web result.",
            session_id="synthetic-native-gateway-session",
            user_id="synthetic-user",
        )
    )

    assert response.response == result["response"]
    assert response.agent_name == expected_name
    assert response.metadata["provider"] == provider
    assert response.metadata["agent_runtime_bypassed"] is True


@pytest.mark.asyncio
async def test_native_ollama_uses_saved_behavior_without_agent_import(monkeypatch):
    cfg = server._default_config()
    cfg["ai_provider"]["provider"] = "ollama_gateway"
    cfg["ollama"]["api_base"] = "http://synthetic-ollama:11434"
    cfg["ollama"]["model"] = "synthetic-7b"
    cfg["model_behavior"].update(
        {"mode": "creative", "temperature": "0.45", "num_ctx": "8192", "show_thinking": True, "thinking_level": "high"}
    )
    monkeypatch.setattr(server.STATE, "config", cfg)
    captured = {}

    async def fake_get_model(api_base, configured_model):
        assert api_base == "http://synthetic-ollama:11434"
        return configured_model

    async def fake_capabilities(_api_base, _model):
        return {"available": True, "supports_thinking": True, "family": "", "thinking_levels": ["low", "high"]}

    async def fake_call(**kwargs):
        captured.update(kwargs)
        return {"response": "synthetic native Ollama reply", "model": kwargs["model"]}

    monkeypatch.setattr(rest_api, "get_ollama_model", fake_get_model)
    monkeypatch.setattr(rest_api, "inspect_ollama_capabilities", fake_capabilities)
    monkeypatch.setattr(rest_api, "call_ollama_with_history", fake_call)

    result = await rest_api.send_message_to_native_ollama("hello", "synthetic-options-session")

    assert result["response"] == "synthetic native Ollama reply"
    assert captured["options"] == {
        "temperature": 0.45,
        "top_p": 1.0,
        "repeat_penalty": 0.9,
        "num_ctx": 8192,
    }
    assert captured["think"] == "high"


@pytest.mark.asyncio
async def test_native_odysseus_uses_only_saved_gateway_settings(monkeypatch):
    cfg = server._default_config()
    cfg["ai_provider"].update(
        {
            "provider": "odysseus",
            "odysseus_api_base": "http://synthetic-odysseus:7000",
            "odysseus_model": "synthetic-odysseus-model",
            "odysseus_token": "ody_synthetic_chat_token",
        }
    )
    monkeypatch.setattr(server.STATE, "config", cfg)
    captured = {}

    async def fake_call(**kwargs):
        captured.update(kwargs)
        return "synthetic Odysseus reply"

    monkeypatch.setattr(rest_api, "call_odysseus", fake_call)
    result = await rest_api.send_message_to_odysseus_gateway("hello", "synthetic-odysseus-session")

    assert result["response"] == "synthetic Odysseus reply"
    assert captured == {
        "api_base": "http://synthetic-odysseus:7000",
        "model": "synthetic-odysseus-model",
        "message": "hello",
        "session_id": "synthetic-odysseus-session",
        "token": "ody_synthetic_chat_token",
    }


def test_native_gateway_config_preserves_saved_odysseus_token_and_redacts_browser_copy(monkeypatch):
    base = server._default_config()
    updated, touched, _ = server._apply_admin_ui_config_patch(
        base,
        {
            "ai_provider": {
                "provider": "odysseus",
                "odysseus_api_base": "http://synthetic-odysseus:7000",
                "odysseus_model": "synthetic-model",
                "odysseus_token": "ody_synthetic_chat_token",
            }
        },
    )
    assert "ai_provider" in touched
    assert updated["ai_provider"]["provider"] == "odysseus"
    assert updated["ai_provider"]["odysseus_token"] == "ody_synthetic_chat_token"

    preserved, _, _ = server._apply_admin_ui_config_patch(
        updated,
        {"ai_provider": {"provider": "odysseus", "odysseus_model": "synthetic-model-v2"}},
    )
    assert preserved["ai_provider"]["odysseus_token"] == "ody_synthetic_chat_token"

    browser_copy = copy.deepcopy(preserved)
    browser_copy["ollama"]["google_api_key"] = "synthetic-google-key"
    browser_copy["ai_provider"].update(
        {
            "openclaw_token": "synthetic-openclaw-token",
            "openclaw_agent_token": "synthetic-openclaw-agent-token",
            "hermes_token": "synthetic-hermes-token",
            "litellm_api_key": "synthetic-litellm-key",
        }
    )
    server._redact_admin_ai_provider_secrets(browser_copy)
    assert browser_copy["ollama"]["google_api_key"] == ""
    assert all(
        browser_copy["ai_provider"][key] == ""
        for key in ("openclaw_token", "openclaw_agent_token", "hermes_token", "litellm_api_key", "odysseus_token")
    )

    monkeypatch.setattr(server, "_has_loaded_config_session", lambda: True)
    monkeypatch.setattr(server.STATE, "used_default_password", False)
    monkeypatch.setattr(server.STATE, "config", {"ai_provider": {"provider": "ollama_gateway"}, "ai_agent": {"enabled": True, "auto_start": True}})
    assert server.should_start_ai_agent_server() is False


def test_local_model_selection_keeps_native_ollama_gateway():
    persisted = []
    refreshes = []

    class FakeModelLibrary:
        @staticmethod
        def list_local_models(_api_base):
            return [{"name": "synthetic-ollama:latest"}]

    class FakeOllamaService:
        @staticmethod
        def reload_from_env():
            refreshes.append("reload")

    cfg = {
        "ollama": {"api_base": "http://synthetic-ollama:11434", "model": "old-model"},
        "ai_provider": {"provider": "ollama_gateway"},
        "ai_agent": {"enabled": True},
    }
    fake_server = SimpleNamespace(
        STATE=SimpleNamespace(config=cfg),
        asyncio=asyncio,
        os=SimpleNamespace(environ={}),
        model_library_service=FakeModelLibrary(),
        ollama_service=FakeOllamaService(),
        LOGGER=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
        _require_login=lambda _request: None,
        _config_write_block_reason=lambda: "",
        _json_config_write_blocked_response=lambda reason: (_ for _ in ()).throw(AssertionError(reason)),
        _default_config=lambda: copy.deepcopy(cfg),
        _loaded_config_for_update=lambda: cfg,
        _persist_state_config=lambda new_cfg: persisted.append(copy.deepcopy(new_cfg)),
        _ensure_local_ollama_runtime_ready=lambda: refreshes.append("ensure"),
    )

    async def restart_ai_agent_server():
        raise AssertionError("native Ollama model selection must not restart AutoYou AI")

    fake_server.restart_ai_agent_server = restart_ai_agent_server
    app = FastAPI()
    register_model_routes(app, app, fake_server)
    with TestClient(app) as client:
        response = client.post("/api/model-library/select", json={"model": "synthetic-ollama:latest"})

    assert response.status_code == 200
    assert response.json() == {"success": True, "model": "synthetic-ollama:latest", "restarted_ai": False}
    assert persisted[-1]["ai_provider"]["provider"] == "ollama_gateway"
    assert refreshes == ["reload", "ensure"]


def test_admin_ui_shows_gateway_settings_only_for_the_selected_mode():
    script = Path("assets/admin-ui.js").read_text(encoding="utf-8")

    for marker in (
        'id: "ollama_gateway"',
        'id: "odysseus"',
        'provider === "odysseus"',
        'aiProvider.odysseus_api_base',
        'aiProvider.odysseus_model',
        'aiProvider.odysseus_token',
        'type === "password" ? " autocomplete=\\"new-password\\""',
        'safeRequestJson("/api/ai/odysseus/status")',
        'safeRequestJson("/api/ai/ollama/status")',
    ):
        assert marker in script


def test_odysseus_status_is_authenticated_and_never_returns_its_token(monkeypatch):
    class FakeServer:
        STATE = SimpleNamespace(
            config={"ai_provider": {"odysseus_api_base": "http://synthetic-odysseus:7000", "odysseus_token": "ody_synthetic_chat_token"}}
        )
        os = SimpleNamespace(environ={})

        @staticmethod
        def _default_config():
            return {"ai_provider": {}}

        @staticmethod
        def _require_api_login(request):
            if request.headers.get("x-synthetic-auth") != "yes":
                return JSONResponse({"error": "Not authenticated"}, status_code=401)
            return None

    async def fake_probe(api_base, *, token):
        assert api_base == "http://synthetic-odysseus:7000"
        assert token == "ody_synthetic_chat_token"
        return {"available": True, "api_base": api_base, "models": ["synthetic-model"]}

    monkeypatch.setattr(agent_routes, "probe_odysseus", fake_probe)
    app = FastAPI()
    agent_routes.register_routes(app, app, FakeServer())
    with TestClient(app) as client:
        denied = client.get("/api/ai/odysseus/status")
        allowed = client.get("/api/ai/odysseus/status", headers={"x-synthetic-auth": "yes"})

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json() == {
        "available": True,
        "api_base": "http://synthetic-odysseus:7000",
        "models": ["synthetic-model"],
    }
    assert "ody_synthetic_chat_token" not in allowed.text
