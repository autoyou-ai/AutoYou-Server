# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Every caller of an Ollama model asks for the same window and keep-alive.

Review 2026-11-05: the media prompt helper, the backup model and the native
gateway sent other context sizes (Ollama then loads the model again), a
memory error halved the window for one call only, and nothing set keep-alive
(Ollama unloads an idle model after 5 minutes). No Ollama is touched here.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import asyncio
from types import SimpleNamespace

import litellm
import pytest

import autoyou_agents.agent as root_agent_module
from autoyou_agents import model_config
from core_server import ai_warmup
from shared import gpu_memory
from shared import ollama_context_policy as policy
from shared.gpu_memory import GIB, GpuMemory

MODEL = "ollama_chat/synthetic-test:8b"
MEMORY_ERROR = 'litellm.APIConnectionError: Ollama_chatException - {"error":"memory layout cannot be allocated"}'
SHOW = {
    "capabilities": ["completion", "tools", "vision"],
    "details": {"family": "mistral3", "parameter_size": "8.9B"},
    "model_info": {
        "general.architecture": "mistral3",
        "mistral3.block_count": 34,
        "mistral3.attention.head_count": 32,
        "mistral3.attention.head_count_kv": 8,
        "mistral3.attention.key_length": 128,
        "mistral3.attention.value_length": 128,
        "mistral3.context_length": 262144,
    },
}


@pytest.fixture
def auto_sizing(monkeypatch, tmp_path):
    monkeypatch.setenv(policy.CONTEXT_SIZING_ENV, "auto")
    for name in ("OLLAMA_FLASH_ATTENTION", "OLLAMA_KV_CACHE_TYPE", "OLLAMA_NUM_PARALLEL", policy.MAX_NUM_CTX_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(policy, "_registry_path", lambda: tmp_path / policy.REGISTRY_FILE)
    policy._registry_cache.update(path=None, mtime=None, data={})
    monkeypatch.setattr(gpu_memory, "detect_gpu_memory", lambda refresh=False: GpuMemory(24 * GIB, None, "nvidia", "test"))


def _model_response(text="ok"):
    return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": text}}])


def test_the_agent_model_is_sized_to_the_gpu_and_kept_loaded(auto_sizing, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "synthetic-test:8b")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")

    class Service:
        client = SimpleNamespace(show=lambda name: SHOW)

        def is_available(self):
            return True

        def list_models(self):
            return ["synthetic-test:8b"]

        def get_available_models(self):
            return [{"model": "synthetic-test:8b", "size": int(5.6 * GIB)}]

    created = []
    monkeypatch.setattr(model_config, "LiteLlm", lambda model, api_base, **kwargs: created.append(kwargs))
    monkeypatch.setattr(model_config, "get_litellm_behavior_kwargs", lambda: {})
    monkeypatch.setattr(model_config, "_load_model_behavior_config", lambda: {})

    model_config._configure_ollama_model(Service())

    assert created[0]["num_ctx"] == 32768  # 24 GB GPU, 5.6 GB of weights, 136 KiB per token
    assert created[0]["keep_alive"] == "30m"
    # The server's own callers (warm start, gateway, probes) read the same window.
    assert policy.resolve_context_window("synthetic-test:8b") == (32768, "gpu_estimate:test")


def test_a_memory_error_shrinks_the_window_for_every_later_call(auto_sizing, monkeypatch):
    sent = []

    async def fake_original(self, model, messages, tools, **kwargs):
        sent.append(kwargs.get("num_ctx"))
        if len(sent) == 1:
            raise RuntimeError(MEMORY_ERROR)
        return _model_response()

    monkeypatch.setattr(root_agent_module, "_original_litellm_client_acompletion", fake_original)
    messages = [{"role": "user", "content": "hello"}]

    asyncio.run(root_agent_module._patched_litellm_client_acompletion(None, MODEL, messages, None, num_ctx=16384, num_predict=1024))
    asyncio.run(root_agent_module._patched_litellm_client_acompletion(None, MODEL, messages, None, num_ctx=16384, num_predict=1024))

    # Before: the second call asked for 16384 again, and Ollama loaded the model twice per turn.
    assert sent == [16384, 8192, 8192]
    assert policy.context_limit(MODEL) == 8192


def test_the_module_level_litellm_patch_holds_the_same_limit(auto_sizing, monkeypatch):
    policy.record_context_limit(MODEL, 8192, "spilled_to_cpu")
    sent = []

    async def fake_original(*args, **kwargs):
        sent.append(kwargs.get("num_ctx"))
        return _model_response()

    monkeypatch.setattr(root_agent_module, "_original_acompletion", fake_original)

    asyncio.run(root_agent_module._patched_acompletion(
        model=MODEL, messages=[{"role": "user", "content": "hello"}], num_ctx=32768,
    ))

    assert sent == [8192]


def test_a_model_that_spilled_onto_the_cpu_gets_half_the_window_next_start(auto_sizing, monkeypatch):
    monkeypatch.setattr(ai_warmup, "_request", lambda url, *, payload=None, timeout: 200)
    placements = {"size": 10 * GIB, "size_vram": 7 * GIB}
    monkeypatch.setattr(
        ai_warmup, "_get_json", lambda url, *, timeout: {"models": [{"model": "synthetic-test:8b", **placements}]}
    )

    report = asyncio.run(ai_warmup.warm_ai_runtime(
        host="127.0.0.1", port=8081,
        ollama_api_base="http://127.0.0.1:11434", ollama_model="synthetic-test:8b", num_ctx=32768,
    ))

    assert report["model"]["placement"]["next_num_ctx"] == 16384
    assert policy.context_limit("synthetic-test:8b") == 16384


@pytest.mark.parametrize("on_gpu", [10 * GIB, 0])
def test_a_model_wholly_on_the_gpu_or_wholly_on_the_cpu_records_nothing(auto_sizing, monkeypatch, on_gpu):
    monkeypatch.setattr(
        ai_warmup, "_get_json",
        lambda url, *, timeout: {"models": [{"name": "synthetic-test:8b", "size": 10 * GIB, "size_vram": on_gpu}]},
    )

    report = ai_warmup.limit_context_if_spilled("http://127.0.0.1:11434", "synthetic-test:8b", 32768)

    assert "next_num_ctx" not in report
    assert policy.context_limit("synthetic-test:8b") is None


def test_the_spill_check_stays_off_with_the_older_sizing_rule(monkeypatch):
    monkeypatch.setattr(ai_warmup, "_request", lambda url, *, payload=None, timeout: 200)
    monkeypatch.setattr(ai_warmup, "_get_json", lambda url, *, timeout: pytest.fail("no /api/ps call expected"))

    report = asyncio.run(ai_warmup.warm_ai_runtime(
        host="127.0.0.1", port=8081,
        ollama_api_base="http://127.0.0.1:11434", ollama_model="synthetic-test:8b", num_ctx=8192,
    ))

    assert "placement" not in report["model"]


def test_cpu_model_warmup_matches_chat_placement(monkeypatch):
    monkeypatch.setenv("AUTOYOU_OLLAMA_CPU_MODELS", "gemma4:e4b")
    requests = []

    def request(_url, *, payload, timeout):
        requests.append(payload)
        return 200

    monkeypatch.setattr(ai_warmup, "_request", request)
    assert ai_warmup.load_ollama_model("http://127.0.0.1:9", "gemma4:e4b", num_ctx=8192)["ok"]
    assert requests[0]["options"] == {"num_ctx": 8192, "num_gpu": 0}


def test_litellm_sends_keep_alive_to_ollama_at_the_top_level(monkeypatch):
    """The keep-alive fix relies on LiteLLM passing the option through; nothing reaches a network."""
    import json as json_module

    from litellm.llms.custom_httpx import http_handler

    captured = {}

    async def fake_post(self, url, data=None, json=None, **kwargs):
        # LiteLLM passes the body as `json`, or already encoded as `data`, depending on its transport.
        body = json if json is not None else json_module.loads(data) if isinstance(data, (str, bytes)) else data
        captured.update(url=url, body=body)
        raise RuntimeError("stopped before the network")

    monkeypatch.setattr(http_handler.AsyncHTTPHandler, "post", fake_post)
    with pytest.raises(Exception):
        asyncio.run(litellm.acompletion(
            model=MODEL, api_base="http://127.0.0.1:9", messages=[{"role": "user", "content": "hi"}],
            num_ctx=8192, keep_alive="30m",
        ))

    assert captured["url"].endswith("/api/chat")
    assert captured["body"]["keep_alive"] == "30m"
    assert captured["body"]["options"]["num_ctx"] == 8192 and "keep_alive" not in captured["body"]["options"]


def test_the_media_prompt_helper_asks_for_the_agents_window_and_keep_alive(monkeypatch):
    from autoyou_agents.media_generation_agent import media_generation_tool as tool

    monkeypatch.setattr(model_config, "resolve_ollama_num_ctx", lambda name: (12288, "test"))

    assert tool._ollama_model_options("synthetic-test:8b") == {"num_ctx": 12288, "keep_alive": "30m"}


def test_the_backup_model_uses_the_same_window_and_keep_alive(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "synthetic-test:8b")
    monkeypatch.setattr(root_agent_module, "resolve_ollama_num_ctx", lambda name: (12288, "test"))

    backup = root_agent_module._build_resilient_fallback_model()

    assert backup._additional_args["num_ctx"] == 12288
    assert backup._additional_args["keep_alive"] == "30m"


def test_the_native_gateway_asks_for_the_agents_window(auto_sizing):
    from shared import ollama_gateway

    policy.record_context_choice("synthetic-test:8b", 24576, "gpu_estimate:test")

    options = ollama_gateway.resolve_ollama_gateway_options({}, {"accurate": {"temperature": 0.1}}, "synthetic-test:8b")

    assert options["num_ctx"] == 24576


def test_the_parameter_count_ollama_reports_beats_the_name_outside_the_known_families(monkeypatch):
    monkeypatch.delenv("AUTOYOU_AGENT_HARNESS_PROFILE", raising=False)
    monkeypatch.setenv("AUTOYOU_ACTIVE_OLLAMA_MODEL", "synthetic-family:4b")
    monkeypatch.setenv("AUTOYOU_ACTIVE_OLLAMA_PARAMETER_SIZE", "30.5B")
    assert model_config.resolve_agent_harness_profile("ollama_chat/synthetic-family:4b") == model_config.AGENT_HARNESS_EXPANDED

    # Ministral keeps its tested rule: the 8B tag stays compact even though Ollama reports 8.9B.
    monkeypatch.setenv("AUTOYOU_ACTIVE_OLLAMA_MODEL", "ministral-3:8b")
    monkeypatch.setenv("AUTOYOU_ACTIVE_OLLAMA_PARAMETER_SIZE", "8.9B")
    assert model_config.resolve_agent_harness_profile("ollama_chat/ministral-3:8b") == model_config.AGENT_HARNESS_COMPACT
