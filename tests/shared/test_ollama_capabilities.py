# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import pytest

from shared.ollama_capabilities import (
    capabilities_from_show_payload,
    resolve_installed_ollama_model,
    resolve_ollama_think_option,
)


def test_resolve_installed_model_accepts_one_untagged_variant():
    assert resolve_installed_ollama_model(
        "ollama_chat/ministral-3",
        [{"name": "ministral-3:14b"}],
    ) == "ministral-3:14b"


def test_resolve_installed_model_rejects_ambiguous_untagged_name():
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_installed_ollama_model(
            "ministral-3",
            ["ministral-3:8b", "ministral-3:14b"],
        )


def test_capabilities_normalize_thinking_tools_and_model_details():
    capability = capabilities_from_show_payload(
        "gpt-oss:120b",
        {
            "capabilities": ["completion", "tools", "thinking"],
            "details": {
                "family": "gptoss",
                "families": "gptoss",
                "parameter_size": "116.8B",
                "quantization_level": "MXFP4",
            },
        },
    )

    assert capability["supports_thinking"] is True
    assert capability["supports_tools"] is True
    assert capability["thinking_levels"] == ["low", "medium", "high", "max"]
    assert capability["family"] == "gptoss"
    assert capability["families"] == ["gptoss"]


def test_gpt_oss_uses_level_even_when_visible_thinking_is_off():
    capability = {"supports_thinking": True, "family": "gptoss"}
    assert resolve_ollama_think_option("gpt-oss:120b", False, capabilities=capability) == "low"
    assert resolve_ollama_think_option("gpt-oss:120b", True, level="high", capabilities=capability) == "high"


def test_non_thinking_model_omits_think_option():
    capability = {"supports_thinking": False, "family": "mistral3"}
    assert resolve_ollama_think_option("ministral-3:14b", True, capabilities=capability) is None
