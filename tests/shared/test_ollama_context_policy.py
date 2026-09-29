# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-cfe87ee51eabdb59e7d8be9d


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-cfe87ee51eabdb59e7d8be9d"

from shared.ollama_context_policy import (
    build_context_compaction_policy,
    estimate_model_size_billions,
    normalize_ollama_model_name,
    recommend_ollama_num_ctx,
)


def test_normalize_ollama_model_name_strips_provider_prefix():
    assert normalize_ollama_model_name("ollama_chat/qwen3:4b") == "qwen3:4b"
    assert normalize_ollama_model_name("qwen3:4b") == "qwen3:4b"
    assert normalize_ollama_model_name("hf.co/some/repo") == "hf.co/some/repo"


def test_estimate_model_size_billions_parses_suffix():
    assert estimate_model_size_billions("ollama_chat/qwen3:4b") == 4.0
    assert estimate_model_size_billions("ministral-3:8b") == 8.0
    assert estimate_model_size_billions("unknown-model") is None


def test_recommend_ollama_num_ctx_uses_ram_and_size_tiers():
    assert recommend_ollama_num_ctx("qwen3:4b", total_ram_gb=8.0) == 8192
    assert recommend_ollama_num_ctx("qwen3:4b", total_ram_gb=20.0) == 16384
    assert recommend_ollama_num_ctx("ministral-3:8b", total_ram_gb=20.0) == 8192
    assert recommend_ollama_num_ctx("ministral-3:8b", total_ram_gb=64.0) == 8192


def test_build_context_compaction_policy_disabled_without_context_window():
    assert build_context_compaction_policy(context_window=0) == {"enabled": False}
    assert build_context_compaction_policy(context_window=None) == {"enabled": False}


def test_build_context_compaction_policy_triggers_well_before_window_is_full():
    """Compaction must trigger with plenty of headroom left, not near-total
    exhaustion - a summarizer that reuses the same small conversation model
    needs room to actually produce a summary, not just barely fit the input."""
    policy = build_context_compaction_policy(context_window=16384, total_ram_gb=20.0)

    assert policy["enabled"] is True
    assert policy["threshold_ratio"] <= 0.65
    # At least a third of the window must still be free when compaction fires.
    assert policy["token_threshold"] <= int(16384 * 0.7)


def test_build_context_compaction_policy_ram_tiers_stay_ordered_and_conservative():
    low_ram = build_context_compaction_policy(context_window=8192, total_ram_gb=8.0)
    mid_ram = build_context_compaction_policy(context_window=16384, total_ram_gb=20.0)
    high_ram = build_context_compaction_policy(context_window=32768, total_ram_gb=64.0)

    assert low_ram["threshold_ratio"] < mid_ram["threshold_ratio"] < high_ram["threshold_ratio"]
    assert low_ram["event_retention_size"] <= mid_ram["event_retention_size"] <= high_ram["event_retention_size"]
    for policy in (low_ram, mid_ram, high_ram):
        assert policy["token_threshold"] >= 4096
