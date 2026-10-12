# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-ff32216c7729212c12dd8ecf


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import autoyou_agents.model_config as model_config

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-ff32216c7729212c12dd8ecf"
# from __debug_provenance_w__ import stripe


def test_resolve_litellm_timeout_defaults_to_1800(monkeypatch):
    for key in (
        "AUTOYOU_LITELLM_TIMEOUT_SECONDS",
        "LITELLM_TIMEOUT_SECONDS",
        "LITELLM_TIMEOUT",
        "OLLAMA_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)

    assert model_config._resolve_litellm_timeout_seconds() == 1800.0


def test_resolve_litellm_timeout_honors_project_override(monkeypatch):
    monkeypatch.setenv("AUTOYOU_LITELLM_TIMEOUT_SECONDS", "2400")

    assert model_config._resolve_litellm_timeout_seconds() == 2400.0
    assert model_config._compose_litellm_kwargs({"temperature": 0.2}) == {
        "temperature": 0.2,
        "timeout": 2400.0,
    }


def test_resolve_litellm_timeout_allows_disable(monkeypatch):
    monkeypatch.setenv("AUTOYOU_LITELLM_TIMEOUT_SECONDS", "0")

    assert model_config._resolve_litellm_timeout_seconds() is None
    assert model_config._compose_litellm_kwargs({"top_p": 0.9}) == {"top_p": 0.9}
