# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The Ollama context window is sized from the model and the GPU.

Before 2026-11-05 every 5-9B model got 8192 tokens on any machine (the size
in the model's name plus total RAM). The model shapes below are the real
``/api/show`` fields of ministral-3:8b, gemma4:e4b and a Qwen 3.5 hybrid,
copied as synthetic payloads; no Ollama or GPU is touched.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import time

import pytest

from shared import gpu_memory
from shared import ollama_context_policy as policy
from shared.gpu_memory import GIB, GpuMemory

KIB = 1024

MINISTRAL_8B = {
    "model_info": {
        "general.architecture": "mistral3",
        "mistral3.block_count": 34,
        "mistral3.attention.head_count": 32,
        "mistral3.attention.head_count_kv": 8,
        "mistral3.attention.key_length": 128,
        "mistral3.attention.value_length": 128,
        "mistral3.embedding_length": 4096,
        "mistral3.context_length": 262144,
    }
}
GEMMA4_E4B = {
    "model_info": {
        "general.architecture": "gemma4",
        "gemma4.block_count": 42,
        "gemma4.attention.head_count": 8,
        "gemma4.attention.head_count_kv": 2,
        "gemma4.attention.key_length": 512,
        "gemma4.attention.value_length": 512,
        "gemma4.attention.key_length_swa": 256,
        "gemma4.attention.value_length_swa": 256,
        "gemma4.attention.sliding_window": 512,
        "gemma4.attention.sliding_window_pattern": ([True] * 5 + [False]) * 7,
        "gemma4.attention.shared_kv_layers": 18,
        "gemma4.embedding_length": 2560,
        "gemma4.context_length": 131072,
    }
}
QWEN35_HYBRID = {
    "modelinfo": {  # the key the ollama Python client uses
        "general.architecture": "qwen35",
        "qwen35.block_count": 65,
        "qwen35.attention.head_count": 24,
        "qwen35.attention.head_count_kv": 4,
        "qwen35.attention.key_length": 256,
        "qwen35.attention.value_length": 256,
        "qwen35.full_attention_interval": 4,
        "qwen35.embedding_length": 5120,
        "qwen35.context_length": 262144,
    }
}


@pytest.fixture(autouse=True)
def _auto_sizing(monkeypatch, tmp_path):
    monkeypatch.setenv(policy.CONTEXT_SIZING_ENV, "auto")
    for name in ("OLLAMA_FLASH_ATTENTION", "OLLAMA_KV_CACHE_TYPE", "OLLAMA_NUM_PARALLEL", policy.MAX_NUM_CTX_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(policy, "_registry_path", lambda: tmp_path / policy.REGISTRY_FILE)
    policy._registry_cache.update(path=None, mtime=None, data={})
    monkeypatch.setattr(gpu_memory, "detect_gpu_memory", lambda refresh=False: GpuMemory(24 * GIB, None, "nvidia", "test"))


def _per_token_kib(payload):
    return policy.kv_profile_from_show(payload)["kv_elements_per_token"] * 2 / KIB


def test_cache_cost_per_token_is_read_from_the_models_own_data():
    assert _per_token_kib(MINISTRAL_8B) == 136
    # Gemma 4: only the 4 full-attention layers before the 18 shared ones grow with the window.
    gemma = policy.kv_profile_from_show(GEMMA4_E4B)
    assert _per_token_kib(GEMMA4_E4B) == 16
    assert gemma["kv_fixed_elements"] == 20 * 2 * 512 * 512  # 20 sliding layers, 512 tokens each
    # Qwen 3.5 hybrid: every 4th layer is attention.
    assert _per_token_kib(QWEN35_HYBRID) == 64
    assert policy.kv_profile_from_show({"model_info": {}}) is None
    assert policy.kv_profile_from_show({"model_info": {"general.architecture": "x", "x.block_count": 2}}) is None


def test_the_8_bit_cache_counts_only_with_flash_attention(monkeypatch):
    assert policy.kv_bytes_per_element() == 2.0
    monkeypatch.setenv("OLLAMA_KV_CACHE_TYPE", "q8_0")
    assert policy.kv_bytes_per_element() == 2.0
    monkeypatch.setenv("OLLAMA_FLASH_ATTENTION", "1")
    assert policy.kv_bytes_per_element() == 34 / 32


def test_the_window_fills_the_gpu_left_after_the_weights():
    profile = policy.kv_profile_from_show(MINISTRAL_8B)
    weights = int(5.6 * GIB)

    # 24 GB: room for far more than the cap.
    assert policy.estimate_num_ctx_for_gpu(profile, weights_bytes=weights, gpu=GpuMemory(24 * GIB, None, "nvidia", "t")) == 32768
    # 8 GB: about 4.6k tokens fit, rounded down to whole steps (the old rule asked for 8192, which spilled).
    assert policy.estimate_num_ctx_for_gpu(profile, weights_bytes=weights, gpu=GpuMemory(8 * GIB, None, "nvidia", "t")) == 4096
    # 6 GB: the weights alone do not leave a 4096-token window; the older rule decides.
    assert policy.estimate_num_ctx_for_gpu(profile, weights_bytes=weights, gpu=GpuMemory(6 * GIB, None, "nvidia", "t")) is None
    # The trained context caps it.
    short = dict(profile, context_length=8192)
    assert policy.estimate_num_ctx_for_gpu(short, weights_bytes=weights, gpu=GpuMemory(24 * GIB, None, "nvidia", "t")) == 8192


def test_the_cap_is_configurable(monkeypatch):
    profile = policy.kv_profile_from_show(GEMMA4_E4B)
    monkeypatch.setenv(policy.MAX_NUM_CTX_ENV, "65536")
    gpu = GpuMemory(24 * GIB, None, "nvidia", "t")
    assert policy.estimate_num_ctx_for_gpu(profile, weights_bytes=9 * GIB, gpu=gpu) == 65536


def test_the_agents_choice_is_recorded_and_every_other_caller_gets_it():
    window, source = policy.resolve_context_window(
        "ministral-3:8b", show_payload=MINISTRAL_8B, weights_bytes=int(5.6 * GIB)
    )
    assert (window, source) == (32768, "gpu_estimate:test")

    # The server's direct callers pass no model data and read the same window.
    assert policy.resolve_context_window("ministral-3:8b") == (32768, "gpu_estimate:test")
    assert policy.resolve_context_window("ollama_chat/ministral-3:8b")[0] == 32768
    # An admin override wins; an unknown model falls back to the older rule.
    assert policy.resolve_context_window("ministral-3:8b", override=9000) == (9000, "model_behavior.override")
    assert policy.resolve_context_window("synthetic-model:8b") == (8192, "machine_heuristic")


def test_a_measured_limit_caps_the_window_until_it_expires_or_the_gpu_changes(monkeypatch):
    policy.resolve_context_window("ministral-3:8b", show_payload=MINISTRAL_8B, weights_bytes=int(5.6 * GIB))
    policy.record_context_limit("ministral-3:8b", 16384, "memory_error")
    assert policy.resolve_context_window("ministral-3:8b") == (16384, "measured_limit")

    policy._update_registry("ministral-3:8b", {"limit_at": time.time() - (policy.LIMIT_DAYS + 1) * 86400})
    assert policy.context_limit("ministral-3:8b") is None
    assert policy.resolve_context_window("ministral-3:8b") == (32768, "gpu_estimate:test")

    policy.record_context_limit("ministral-3:8b", 16384, "spilled_to_cpu")
    monkeypatch.setattr(gpu_memory, "detect_gpu_memory", lambda refresh=False: GpuMemory(48 * GIB, None, "nvidia", "test"))
    assert policy.context_limit("ministral-3:8b") is None


def test_heuristic_mode_is_the_previous_behaviour_exactly(monkeypatch):
    monkeypatch.setenv(policy.CONTEXT_SIZING_ENV, "heuristic")
    policy.record_context_limit("ministral-3:8b", 4096, "memory_error")
    policy.record_context_choice("ministral-3:8b", 32768, "gpu_estimate:test")

    assert policy.resolve_context_window("ministral-3:8b", show_payload=MINISTRAL_8B, weights_bytes=GIB) == (
        policy.recommend_ollama_num_ctx("ministral-3:8b"),
        "machine_heuristic",
    )
    assert not policy._registry_path().exists()


def test_keep_alive_defaults_to_30_minutes_and_follows_the_settings(monkeypatch):
    monkeypatch.delenv(policy.KEEP_ALIVE_ENV, raising=False)
    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)
    assert policy.resolve_ollama_keep_alive() == "30m"
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "2h")
    assert policy.resolve_ollama_keep_alive() == "2h"
    monkeypatch.setenv(policy.KEEP_ALIVE_ENV, "-1")
    assert policy.resolve_ollama_keep_alive() == -1
    monkeypatch.setenv(policy.KEEP_ALIVE_ENV, "default")
    assert policy.resolve_ollama_keep_alive() is None
