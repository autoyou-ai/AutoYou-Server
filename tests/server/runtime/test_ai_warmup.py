# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The AI worker and the model are warm before the first message (owner report 2026-11-03).

The first message after a start took 53.8 s while the agent tree was built
and the model loaded. Nothing here touches a real server or Ollama.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import asyncio
import logging
import os
from types import SimpleNamespace

from core_server import ai_warmup, services


def _recording_requests(monkeypatch, *, fail=False):
    calls = []

    def fake_request(url, *, payload=None, timeout):
        calls.append((url, payload))
        if fail:
            raise OSError("synthetic connection refused")
        return 200

    monkeypatch.setattr(ai_warmup, "_request", fake_request)
    return calls


def test_warmup_builds_the_tree_and_loads_the_model_with_the_agents_context(monkeypatch):
    calls = _recording_requests(monkeypatch)

    report = asyncio.run(ai_warmup.warm_ai_runtime(
        host="127.0.0.1", port=8081,
        ollama_api_base="http://127.0.0.1:11434", ollama_model="synthetic-model:latest", num_ctx=12288,
    ))

    assert ("http://127.0.0.1:8081/apps/autoyou_agents/app-info", None) in calls
    # Same num_ctx as the agent's requests, or Ollama would load the model again.
    assert ("http://127.0.0.1:11434/api/generate", {"model": "synthetic-model:latest", "options": {"num_ctx": 12288}}) in calls
    assert report["agent_tree"]["ok"] is True and report["model"]["ok"] is True


def test_a_failed_warmup_is_reported_not_raised(monkeypatch):
    _recording_requests(monkeypatch, fail=True)

    report = asyncio.run(ai_warmup.warm_ai_runtime(host="127.0.0.1", port=8081))

    assert report["agent_tree"]["ok"] is False
    assert "synthetic connection refused" in report["agent_tree"]["error"]
    assert "model" not in report


def test_warmup_can_be_turned_off(monkeypatch):
    calls = _recording_requests(monkeypatch)
    monkeypatch.setenv(ai_warmup.WARMUP_ENV, "0")

    assert asyncio.run(ai_warmup.warm_ai_runtime(host="127.0.0.1", port=8081)) == {"skipped": True}
    assert calls == []


def _runtime(api_base, *, model_behavior=None):
    return SimpleNamespace(
        os=SimpleNamespace(getenv=lambda key, default=None: {
            "AI_PROVIDER": "ollama", "OLLAMA_API_BASE": api_base, "OLLAMA_MODEL": "synthetic-model",
        }.get(key, default)),
        _is_local_ollama_api_base=lambda base: "127.0.0.1" in base or "localhost" in base,
        _normalize_ollama_model_reference=lambda name: f"{name}:latest",
        DEFAULT_WIZARD_MODEL="synthetic-default",
        STATE=SimpleNamespace(config={"model_behavior": model_behavior or {}}),
        AI_AGENT_SERVER_PORT=8081,
        LOGGER=logging.getLogger("synthetic"),
    )


def test_the_server_warms_the_model_its_agent_will_use(monkeypatch):
    scheduled = []
    monkeypatch.setattr(ai_warmup, "schedule", lambda **kwargs: scheduled.append(kwargs))

    services._schedule_ai_runtime_warmup(_runtime("http://127.0.0.1:11434", model_behavior={"num_ctx": 9000}), "127.0.0.1")

    assert scheduled[0]["host"] == "127.0.0.1" and scheduled[0]["port"] == 8081
    assert scheduled[0]["ollama_model"] == "synthetic-model:latest"
    assert scheduled[0]["num_ctx"] == 9000


def test_a_remote_model_is_not_loaded_from_here(monkeypatch):
    scheduled = []
    monkeypatch.setattr(ai_warmup, "schedule", lambda **kwargs: scheduled.append(kwargs))

    services._schedule_ai_runtime_warmup(_runtime("https://ollama.example.invalid"), "127.0.0.1")

    assert scheduled[0]["ollama_api_base"] is None and scheduled[0]["ollama_model"] is None


def test_the_env_switch_is_respected_without_a_running_loop(monkeypatch):
    monkeypatch.setenv(ai_warmup.WARMUP_ENV, "off")
    assert ai_warmup.schedule(host="127.0.0.1", port=8081) is None
    assert os.environ[ai_warmup.WARMUP_ENV] == "off"
