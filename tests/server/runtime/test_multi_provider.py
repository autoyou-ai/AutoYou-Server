# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-e7b96498a2b018405e776cca

"""
Tests for the multi-provider AI model architecture.

Covers:
- _get_active_provider() dispatch and legacy USE_GOOGLE_API fallback
- _filter_ollama_kwargs() stripping Ollama-only params
- _configure_openclaw_model() / _configure_litellm_model() LiteLlm construction
- provider constants
- openclaw_agent defaults to uninstalled in agent_install_registry
- OpenClaw sub-agent package is importable and exposes create_openclaw_agent factory
- _build_resilient_fallback_model() returns provider-appropriate fallback
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-e7b96498a2b018405e776cca"

import os
import sys
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

# ── Provider constant tests ────────────────────────────────────────────────────

def test_provider_constants_defined():
    from autoyou_agents.model_config import (
        PROVIDER_OLLAMA,
        PROVIDER_OPENCLAW,
        PROVIDER_LITELLM,
        PROVIDER_GOOGLE,
    )
    assert PROVIDER_OLLAMA == "ollama"
    assert PROVIDER_OPENCLAW == "openclaw"
    assert PROVIDER_LITELLM == "litellm"
    assert PROVIDER_GOOGLE == "google"

def test_ollama_only_kwargs_frozenset():
    from autoyou_agents.model_config import _OLLAMA_ONLY_KWARGS
    assert "repeat_penalty" in _OLLAMA_ONLY_KWARGS
    assert "num_ctx" in _OLLAMA_ONLY_KWARGS
    assert "top_k" in _OLLAMA_ONLY_KWARGS
    assert "think" in _OLLAMA_ONLY_KWARGS
    assert "num_predict" in _OLLAMA_ONLY_KWARGS
    # Verify it is a frozenset (immutable)
    assert isinstance(_OLLAMA_ONLY_KWARGS, frozenset)

# ── _get_active_provider() tests ───────────────────────────────────────────────

def test_get_active_provider_reads_ai_provider_env(monkeypatch):
    from autoyou_agents import model_config
    monkeypatch.setenv("AI_PROVIDER", "openclaw")
    monkeypatch.delenv("USE_GOOGLE_API", raising=False)
    assert model_config._get_active_provider() == "openclaw"

def test_get_active_provider_all_four_values(monkeypatch):
    from autoyou_agents import model_config
    for provider in ("ollama", "openclaw", "litellm", "google"):
        monkeypatch.setenv("AI_PROVIDER", provider)
        assert model_config._get_active_provider() == provider

def test_get_active_provider_case_insensitive(monkeypatch):
    from autoyou_agents import model_config
    monkeypatch.setenv("AI_PROVIDER", "GOOGLE")
    assert model_config._get_active_provider() == "google"

def test_get_active_provider_invalid_falls_back_to_ollama(monkeypatch):
    from autoyou_agents import model_config
    monkeypatch.setenv("AI_PROVIDER", "something_unknown")
    monkeypatch.delenv("USE_GOOGLE_API", raising=False)
    assert model_config._get_active_provider() == "ollama"

def test_get_active_provider_missing_falls_back_to_ollama(monkeypatch):
    from autoyou_agents import model_config
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("USE_GOOGLE_API", raising=False)
    assert model_config._get_active_provider() == "ollama"

def test_get_active_provider_legacy_use_google_api_true(monkeypatch):
    from autoyou_agents import model_config
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("USE_GOOGLE_API", "true")
    assert model_config._get_active_provider() == "google"

def test_get_active_provider_legacy_use_google_api_1(monkeypatch):
    from autoyou_agents import model_config
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("USE_GOOGLE_API", "1")
    assert model_config._get_active_provider() == "google"

def test_get_active_provider_ai_provider_takes_precedence_over_use_google_api(monkeypatch):
    """AI_PROVIDER=ollama should win even if USE_GOOGLE_API=1 is also set."""
    from autoyou_agents import model_config
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("USE_GOOGLE_API", "1")
    assert model_config._get_active_provider() == "ollama"

# ── _filter_ollama_kwargs() tests ──────────────────────────────────────────────

def test_filter_ollama_kwargs_strips_ollama_only_keys():
    from autoyou_agents.model_config import _filter_ollama_kwargs
    inp = {
        "temperature": 0.7,
        "top_p": 0.95,
        "repeat_penalty": 1.1,
        "num_ctx": 4096,
        "top_k": 40,
        "think": False,
        "num_predict": 1024,
        "timeout": 1800,
    }
    result = _filter_ollama_kwargs(inp)
    assert "repeat_penalty" not in result
    assert "num_ctx" not in result
    assert "top_k" not in result
    assert "think" not in result
    assert "num_predict" not in result
    assert result["temperature"] == 0.7
    assert result["top_p"] == 0.95
    assert result["timeout"] == 1800

def test_filter_ollama_kwargs_empty_dict():
    from autoyou_agents.model_config import _filter_ollama_kwargs
    assert _filter_ollama_kwargs({}) == {}

def test_filter_ollama_kwargs_no_ollama_keys_passthrough():
    from autoyou_agents.model_config import _filter_ollama_kwargs
    inp = {"temperature": 0.5, "timeout": 900}
    assert _filter_ollama_kwargs(inp) == inp

def test_filter_ollama_kwargs_does_not_mutate_original():
    from autoyou_agents.model_config import _filter_ollama_kwargs
    inp = {"temperature": 0.7, "num_ctx": 4096}
    _filter_ollama_kwargs(inp)
    assert "num_ctx" in inp  # original must be untouched

# ── _configure_openclaw_model() tests ─────────────────────────────────────────

def test_configure_ollama_model_disables_thinking_by_default(monkeypatch):
    from autoyou_agents import model_config

    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:12b")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
    monkeypatch.delenv("AUTOYOU_OLLAMA_THINKING", raising=False)
    monkeypatch.delenv("OLLAMA_THINKING", raising=False)
    monkeypatch.delenv("OLLAMA_THINK", raising=False)

    class DummyOllamaService:
        def is_available(self):
            return True

        def list_models(self):
            return ["gemma4:12b"]

    created = []

    class FakeLiteLlm:
        def __init__(self, model, api_base, **kwargs):
            created.append({"model": model, "api_base": api_base, **kwargs})

    with patch.object(model_config, "LiteLlm", FakeLiteLlm):
        with patch.object(model_config, "get_litellm_behavior_kwargs", return_value={}):
            with patch.object(model_config, "resolve_ollama_num_ctx", return_value=(32768, "test")):
                model_config._configure_ollama_model(DummyOllamaService())

    assert created == [
        {
            "model": "ollama_chat/gemma4:12b",
            "api_base": "http://127.0.0.1:11434",
            "timeout": 1800.0,
            # The default behavior mode supplies no sampling parameters, so these
            # floors come from _configure_ollama_model. temperature alone is not
            # enough: near-greedy sampling without a repetition guard sent
            # ministral-3:8b into a degenerate "retrie: retrie:" loop.
            "temperature": 0.1,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "num_ctx": 32768,
            "think": False,
            "num_predict": 1024,
        }
    ]

def test_configure_ollama_model_allows_thinking_override(monkeypatch):
    from autoyou_agents import model_config

    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:12b")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
    monkeypatch.setenv("AUTOYOU_OLLAMA_THINKING", "1")

    class DummyOllamaService:
        def is_available(self):
            return True

        def list_models(self):
            return ["gemma4:12b"]

    created = []

    class FakeLiteLlm:
        def __init__(self, model, api_base, **kwargs):
            created.append({"model": model, "api_base": api_base, **kwargs})

    with patch.object(model_config, "LiteLlm", FakeLiteLlm):
        with patch.object(model_config, "get_litellm_behavior_kwargs", return_value={}):
            with patch.object(model_config, "resolve_ollama_num_ctx", return_value=(32768, "test")):
                model_config._configure_ollama_model(DummyOllamaService())

    assert created[0]["think"] is True


def test_configure_ollama_model_omits_thinking_for_model_without_capability(monkeypatch):
    from autoyou_agents import model_config

    monkeypatch.setenv("OLLAMA_MODEL", "ministral-3:14b")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")

    class FakeClient:
        def show(self, model):
            assert model == "ministral-3:14b"
            return {
                "capabilities": ["completion", "tools"],
                "details": {"family": "mistral3"},
            }

    class DummyOllamaService:
        client = FakeClient()

        def is_available(self):
            return True

        def list_models(self):
            return ["ministral-3:14b"]

    created = []

    class FakeLiteLlm:
        def __init__(self, model, api_base, **kwargs):
            created.append({"model": model, "api_base": api_base, **kwargs})

    with patch.object(model_config, "LiteLlm", FakeLiteLlm):
        with patch.object(model_config, "get_litellm_behavior_kwargs", return_value={}):
            with patch.object(model_config, "resolve_ollama_num_ctx", return_value=(32768, "test")):
                model_config._configure_ollama_model(DummyOllamaService())

    assert created[0]["model"] == "ollama_chat/ministral-3:14b"
    assert "think" not in created[0]


def test_configure_ollama_model_uses_thinking_level_for_gpt_oss(monkeypatch):
    from autoyou_agents import model_config

    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:120b")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
    monkeypatch.delenv("AUTOYOU_OLLAMA_THINKING", raising=False)
    monkeypatch.delenv("OLLAMA_THINKING", raising=False)
    monkeypatch.delenv("OLLAMA_THINK", raising=False)

    class FakeClient:
        def show(self, model):
            return {
                "capabilities": ["completion", "tools", "thinking"],
                "details": {"family": "gptoss", "parameter_size": "116.8B"},
            }

    class DummyOllamaService:
        client = FakeClient()

        def is_available(self):
            return True

        def list_models(self):
            return ["gpt-oss:120b"]

    created = []

    class FakeLiteLlm:
        def __init__(self, model, api_base, **kwargs):
            created.append({"model": model, "api_base": api_base, **kwargs})

    with patch.object(model_config, "LiteLlm", FakeLiteLlm):
        with patch.object(model_config, "get_litellm_behavior_kwargs", return_value={}):
            with patch.object(model_config, "resolve_ollama_num_ctx", return_value=(131072, "test")):
                model_config._configure_ollama_model(DummyOllamaService())

    assert created[0]["think"] == "low"
    assert os.environ["AUTOYOU_ACTIVE_OLLAMA_MODEL"] == "gpt-oss:120b"
    assert os.environ["AUTOYOU_ACTIVE_OLLAMA_PARAMETER_SIZE"] == "116.8B"


def test_configure_ollama_model_does_not_fallback_for_explicit_missing_model(monkeypatch):
    from autoyou_agents import model_config

    monkeypatch.setenv("OLLAMA_MODEL", "ministral-3:8b")
    monkeypatch.setenv("AUTOYOU_OLLAMA_MODEL_EXPLICIT", "1")

    class DummyOllamaService:
        def is_available(self):
            return True

        def list_models(self):
            return ["ministral-3:14b"]

        def get_latest_model(self):
            return "ministral-3:14b"

        def get_default_model(self):
            return "ministral-3:14b"

    with pytest.raises(RuntimeError, match="not installed"):
        model_config._configure_ollama_model(DummyOllamaService())

def test_configure_openclaw_model_default_env(monkeypatch):
    """Default env: port 18789, no token, model openclaw/default."""
    from autoyou_agents import model_config
    monkeypatch.delenv("OPENCLAW_PORT", raising=False)
    monkeypatch.delenv("OPENCLAW_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_MODEL", raising=False)

    created = []

    class FakeLiteLlm:
        def __init__(self, model, api_base, api_key, **kwargs):
            created.append({"model": model, "api_base": api_base, "api_key": api_key, **kwargs})

    with patch.object(model_config, "LiteLlm", FakeLiteLlm):
        model_config._configure_openclaw_model()

    assert len(created) == 1
    assert created[0]["model"] == "openai/openclaw/default"
    assert created[0]["api_base"] == "http://127.0.0.1:18789/v1"
    # Token falls back to "noop" when empty
    assert created[0]["api_key"] == "noop"

def test_configure_openclaw_model_custom_env(monkeypatch):
    from autoyou_agents import model_config
    monkeypatch.setenv("OPENCLAW_PORT", "9000")
    monkeypatch.setenv("OPENCLAW_TOKEN", "my-token-xyz")
    monkeypatch.setenv("OPENCLAW_MODEL", "nadirclaw/eco")

    created = []

    class FakeLiteLlm:
        def __init__(self, model, api_base, api_key, **kwargs):
            created.append({"model": model, "api_base": api_base, "api_key": api_key})

    with patch.object(model_config, "LiteLlm", FakeLiteLlm):
        model_config._configure_openclaw_model()

    assert created[0]["model"] == "openai/nadirclaw/eco"
    assert created[0]["api_base"] == "http://127.0.0.1:9000/v1"
    assert created[0]["api_key"] == "my-token-xyz"

def test_configure_openclaw_model_strips_ollama_kwargs(monkeypatch):
    """Ollama-specific kwargs must not be forwarded to OpenClaw."""
    from autoyou_agents import model_config
    monkeypatch.setenv("OPENCLAW_PORT", "18789")

    passed_kwargs = {}

    class FakeLiteLlm:
        def __init__(self, model, api_base, api_key, **kwargs):
            passed_kwargs.update(kwargs)

    fake_behavior = {"temperature": 0.7, "repeat_penalty": 1.1, "num_ctx": 4096}
    with patch.object(model_config, "LiteLlm", FakeLiteLlm):
        with patch.object(model_config, "get_litellm_behavior_kwargs", return_value=fake_behavior):
            model_config._configure_openclaw_model()

    assert "repeat_penalty" not in passed_kwargs
    assert "num_ctx" not in passed_kwargs
    assert passed_kwargs.get("temperature") == 0.7

# ── _configure_litellm_model() tests ──────────────────────────────────────────

def test_configure_litellm_model_anthropic(monkeypatch):
    from autoyou_agents import model_config
    monkeypatch.setenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-5")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-ant-test")
    monkeypatch.delenv("LITELLM_API_BASE", raising=False)

    created = []

    class FakeLiteLlm:
        def __init__(self, model, api_key=None, **kwargs):
            created.append({"model": model, "api_key": api_key, **kwargs})

    with patch.object(model_config, "LiteLlm", FakeLiteLlm):
        model_config._configure_litellm_model()

    assert created[0]["model"] == "anthropic/claude-sonnet-4-5"
    assert created[0]["api_key"] == "sk-ant-test"
    assert "api_base" not in created[0]

def test_configure_litellm_model_with_custom_base(monkeypatch):
    from autoyou_agents import model_config
    monkeypatch.setenv("LITELLM_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-openai-key")
    monkeypatch.setenv("LITELLM_API_BASE", "https://my-proxy.example.com/v1")

    created = []

    class FakeLiteLlm:
        def __init__(self, model, api_key=None, **kwargs):
            created.append({"model": model, "api_key": api_key, **kwargs})

    with patch.object(model_config, "LiteLlm", FakeLiteLlm):
        model_config._configure_litellm_model()

    assert created[0]["api_base"] == "https://my-proxy.example.com/v1"

def test_configure_litellm_model_raises_when_no_model(monkeypatch):
    from autoyou_agents import model_config
    monkeypatch.setenv("LITELLM_MODEL", "")

    with pytest.raises(ValueError, match="LITELLM_MODEL is not configured"):
        model_config._configure_litellm_model()

def test_configure_litellm_model_strips_ollama_kwargs(monkeypatch):
    from autoyou_agents import model_config
    monkeypatch.setenv("LITELLM_MODEL", "anthropic/claude-haiku-4-5")
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_API_BASE", raising=False)

    passed_kwargs = {}

    class FakeLiteLlm:
        def __init__(self, model, api_key=None, **kwargs):
            passed_kwargs.update(kwargs)

    fake_behavior = {"temperature": 0.5, "top_k": 40, "num_ctx": 8192}
    with patch.object(model_config, "LiteLlm", FakeLiteLlm):
        with patch.object(model_config, "get_litellm_behavior_kwargs", return_value=fake_behavior):
            model_config._configure_litellm_model()

    assert "top_k" not in passed_kwargs
    assert "num_ctx" not in passed_kwargs
    assert passed_kwargs.get("temperature") == 0.5

# ── Agent install registry: openclaw_agent defaults to uninstalled ─────────────

def test_openclaw_agent_default_install_state_is_false():
    from autoyou_agents.shared_tools.agent_install_registry import DEFAULT_AGENT_INSTALL_STATES
    assert "openclaw_agent" in DEFAULT_AGENT_INSTALL_STATES
    assert DEFAULT_AGENT_INSTALL_STATES["openclaw_agent"] is False

def test_audio_agent_default_install_state_is_true():
    from autoyou_agents.shared_tools.agent_install_registry import DEFAULT_AGENT_INSTALL_STATES
    assert "audio_agent" in DEFAULT_AGENT_INSTALL_STATES
    assert DEFAULT_AGENT_INSTALL_STATES["audio_agent"] is True

def test_server_openclaw_defaults_use_nadirclaw(monkeypatch):
    import server

    monkeypatch.delenv("OPENCLAW_MODEL", raising=False)
    monkeypatch.delenv("OPENCLAW_AGENT_MODEL", raising=False)

    server.setup_default_environment_variables()
    defaults = server._default_config()

    assert os.environ.get("OPENCLAW_MODEL") == "openclaw/default"
    assert os.environ.get("OPENCLAW_AGENT_MODEL") == "openclaw/default"
    assert defaults["ai_provider"]["openclaw_model"] == "openclaw/default"
    assert defaults["ai_provider"]["openclaw_agent_model"] == "openclaw/default"

def test_openclaw_agent_discovered_but_uninstalled_by_default(tmp_path):
    """When openclaw_agent dir exists on disk, it starts as uninstalled."""
    from autoyou_agents.shared_tools.agent_install_registry import refresh_agent_install_registry

    agents_root = tmp_path / "autoyou_agents"
    agents_root.mkdir()
    for name in ("notes_agent", "openclaw_agent", "internet_agent"):
        d = agents_root / name
        d.mkdir()
        (d / "agent.py").write_text("AGENT_NAME='test'\n", encoding="utf-8")

    registry_path = tmp_path / "registry.json"
    payload = refresh_agent_install_registry(
        agents_root=agents_root,
        registry_path=registry_path,
    )

    assert "openclaw_agent" in payload["available_agents"]
    assert "openclaw_agent" not in payload["installed_agents"]

# ── OpenClaw sub-agent package ─────────────────────────────────────────────────

def test_openclaw_agent_package_is_importable():
    """The openclaw_agent package must be importable without side effects."""
    import autoyou_agents.openclaw_agent.prompt as prompt_mod
    assert hasattr(prompt_mod, "AGENT_NAME")
    assert hasattr(prompt_mod, "AGENT_DESCRIPTION")
    assert hasattr(prompt_mod, "AGENT_INSTRUCTION")

def test_openclaw_agent_name_constant():
    from autoyou_agents.openclaw_agent.prompt import AGENT_NAME
    assert AGENT_NAME == "autoyou_openclaw_agent"

def test_openclaw_agent_description_mentions_key_capabilities():
    from autoyou_agents.openclaw_agent.prompt import AGENT_DESCRIPTION
    desc_lower = AGENT_DESCRIPTION.lower()
    assert "smart" in desc_lower or "home" in desc_lower
    assert "spotify" in desc_lower or "music" in desc_lower

def test_create_openclaw_agent_factory_exists():
    from autoyou_agents.openclaw_agent.agent import create_openclaw_agent
    assert callable(create_openclaw_agent)

def test_openclaw_agent_tools_exposed():
    from autoyou_agents.openclaw_agent.agent import query_openclaw, check_openclaw_status
    assert callable(query_openclaw)
    assert callable(check_openclaw_status)

def test_query_openclaw_returns_error_when_gateway_unreachable(monkeypatch):
    """query_openclaw must not raise - returns a user-friendly error string."""
    import asyncio
    from unittest.mock import AsyncMock
    from autoyou_agents.openclaw_agent.agent import query_openclaw

    monkeypatch.setenv("OPENCLAW_AGENT_PORT", "19999")  # port nobody is listening on

    with patch("autoyou_agents.openclaw_agent.agent.call_openclaw_gateway", AsyncMock(return_value=None)):
        result = asyncio.run(query_openclaw("turn on the lights"))

    assert "could not complete" in result.lower()

def test_check_openclaw_status_returns_not_running_when_unreachable(monkeypatch):
    import httpx
    from autoyou_agents.openclaw_agent.agent import check_openclaw_status

    monkeypatch.setenv("OPENCLAW_AGENT_PORT", "19998")

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    with patch("autoyou_agents.openclaw_agent.agent.httpx.Client", return_value=FakeClient()):
        result = check_openclaw_status()

    assert "not running" in result.lower() or "19998" in result

# ── Root agent prompt: openclaw routing rule present ──────────────────────────

def test_root_prompt_contains_openclaw_agent_name():
    from autoyou_agents.prompt import AGENT_INSTRUCTION, SUB_AGENTS_SECTION
    assert "autoyou_openclaw_agent" in AGENT_INSTRUCTION
    assert "autoyou_openclaw_agent" in SUB_AGENTS_SECTION

def test_root_prompt_contains_openclaw_routing_rule():
    from autoyou_agents.prompt import AGENT_INSTRUCTION, ROUTING_RULES_SECTION
    for text in (AGENT_INSTRUCTION, ROUTING_RULES_SECTION):
        assert "openclaw" in text.lower()

# ── get_model_config() dispatch ────────────────────────────────────────────────

def test_get_model_config_dispatches_to_openclaw(monkeypatch):
    from autoyou_agents import model_config

    monkeypatch.setenv("AI_PROVIDER", "openclaw")
    monkeypatch.setenv("OPENCLAW_PORT", "18789")
    monkeypatch.delenv("OPENCLAW_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_MODEL", raising=False)

    sentinel = object()

    with patch.object(model_config, "_configure_openclaw_model", return_value=sentinel) as mock_oc:
        result = model_config.get_model_config(MagicMock())

    mock_oc.assert_called_once()
    assert result is sentinel

def test_get_model_config_dispatches_to_litellm(monkeypatch):
    from autoyou_agents import model_config

    monkeypatch.setenv("AI_PROVIDER", "litellm")
    monkeypatch.setenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-5")

    sentinel = object()

    with patch.object(model_config, "_configure_litellm_model", return_value=sentinel) as mock_ll:
        result = model_config.get_model_config(MagicMock())

    mock_ll.assert_called_once()
    assert result is sentinel

def test_get_model_config_dispatches_to_google(monkeypatch):
    from autoyou_agents import model_config

    monkeypatch.setenv("AI_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-abc")
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.setenv("GOOGLE_MODEL", "gemini-2.5-flash")

    sentinel = object()

    with patch.object(model_config, "_configure_gemini_model", return_value=sentinel) as mock_g:
        result = model_config.get_model_config(MagicMock())

    mock_g.assert_called_once()
    assert result is sentinel

def test_get_model_config_ollama_keeps_full_model_config_when_runtime_starts_late(monkeypatch):
    from autoyou_agents import model_config

    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "synthetic-model:1b")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")

    mock_service = MagicMock()
    mock_service.is_available.return_value = False
    mock_service.client = None

    with patch.object(model_config, "get_litellm_behavior_kwargs", return_value={}):
        with patch.object(model_config, "resolve_ollama_num_ctx", return_value=(8192, "test")):
            result = model_config.get_model_config(mock_service)

    assert result.model == "ollama_chat/synthetic-model:1b"
    mock_service.list_models.assert_not_called()

# ── _apply_google_api_config_to_env: new provider keys synced ─────────────────

def test_apply_config_to_env_syncs_ai_provider_fields(monkeypatch):
    """_apply_google_api_config_to_env should write AI_PROVIDER and OpenClaw/LiteLLM env vars."""
    import server

    original_config = server.STATE.config

    class DummyOllamaService:
        def reload_from_env(self):
            pass

    monkeypatch.setattr(server, "ollama_service", DummyOllamaService())

    server.STATE.config = {
        "ollama": {
            "api_base": "http://127.0.0.1:11434",
            "model": "ministral-3:8b",
            "use_google_api": False,
            "google_model": "gemini-2.5-flash",
            "google_api_key": "",
        },
        "ai_provider": {
            "provider": "openclaw",
            "openclaw_port": 9001,
            "openclaw_token": "tok-xyz",
            "openclaw_model": "nadirclaw/eco",
            "openclaw_agent_port": 9001,
            "openclaw_agent_token": "tok-xyz",
            "openclaw_agent_model": "openclaw/default",
            "litellm_model": "anthropic/claude-sonnet-4-5",
            "litellm_api_key": "sk-ant-test",
            "litellm_api_base": "",
        },
    }

    try:
        server._apply_google_api_config_to_env()
    finally:
        server.STATE.config = original_config

    assert os.environ.get("AI_PROVIDER") == "openclaw"
    assert os.environ.get("OPENCLAW_PORT") == "9001"
    assert os.environ.get("OPENCLAW_TOKEN") == "tok-xyz"
    assert os.environ.get("OPENCLAW_MODEL") == "nadirclaw/eco"
    assert os.environ.get("LITELLM_MODEL") == "anthropic/claude-sonnet-4-5"
    assert os.environ.get("LITELLM_API_KEY") == "sk-ant-test"
