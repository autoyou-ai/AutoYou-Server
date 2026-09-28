# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-0c5b4f132c60ed64fa511786


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-0c5b4f132c60ed64fa511786"

import json
import builtins
import hashlib
import os
from pathlib import Path

import pytest

from shared import intent_router

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads((ROOT / "tests/fixtures/intent_router/v1.json").read_text(encoding="utf-8"))


def test_wordpiece_matches_publisher_tokenizer():
    tokenizer = intent_router.WordPiece((ROOT / "assets/intent_router/vocab.txt").read_text(encoding="utf-8"))
    for item in VECTORS["tokenizer"]:
        assert tokenizer.encode(item["text"]) == item["ids"]


def test_missing_or_disabled_model_defers_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_INTENT_ROUTER_DIR", str(tmp_path))
    assert intent_router.classify_intent("Show all my scheduled tasks")["route"] is None
    monkeypatch.setenv("AUTOYOU_LOCAL_ROUTING", "0")
    assert intent_router.classify_intent("Show all my scheduled tasks")["reason"] == "disabled"


def test_corrupt_model_rejected_before_inference(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {"model.onnx": {"bytes": 1, "sha256": "0" * 64}}}))
    (tmp_path / "model.onnx").write_bytes(b"x")
    with pytest.raises(ValueError, match="integrity"):
        intent_router.IntentRouter(tmp_path)


def test_missing_optional_dependency_keeps_generic_host_usable(tmp_path, monkeypatch):
    data = b"synthetic-model-data"
    (tmp_path / "model.onnx").write_bytes(data)
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {
        "model.onnx": {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    }}))
    original = builtins.__import__
    attempted = []
    def missing_numpy(name, *args, **kwargs):
        if name == "numpy":
            attempted.append(name)
            raise ModuleNotFoundError("Optional inference runtime is absent")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing_numpy)
    monkeypatch.setenv("AUTOYOU_INTENT_ROUTER_DIR", str(tmp_path))
    assert intent_router.classify_intent("Check my model training progress")["reason"] == "model_unavailable"
    assert attempted == ["numpy"]


@pytest.mark.skipif(os.getenv("AUTOYOU_TEST_INTENT_MODEL") != "1", reason="opt-in verified model inference")
def test_real_model_cross_platform_vectors_and_availability():
    router = intent_router.IntentRouter(ROOT / "assets/intent_router")
    for item in VECTORS["routing"]:
        assert router.classify(item["text"])["route"] == item["route"]
    assert router.classify("Show all my scheduled tasks", {"notes_agent"})["route"] is None
    assert router.classify("hello " * 300)["route"] is None
    assert router.classify("Can you help me with my note and also play music?")["route"] is None


@pytest.mark.asyncio
async def test_router_uses_existing_dispatch_and_preserves_request(monkeypatch):
    from types import SimpleNamespace
    from autoyou_agents import agent
    monkeypatch.setattr(agent, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(agent, "_is_runtime_agent_enabled", lambda name: name == "autoyou_fine_tuning_agent")
    monkeypatch.setattr(intent_router, "classify_intent", lambda text, allowed: {"route": "fine_tuning_agent"})
    request = "Check my model training progress"
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types
    llm_request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text=request)])])
    context = SimpleNamespace(state={}, invocation_id="synthetic-intent-invocation")
    response = await agent._root_router_before_model_callback(context, llm_request)
    assert response.custom_metadata["route_reason"] == "local_intent_model"
    call = response.content.parts[0].function_call
    assert call.args["request"] == request
    assert response.custom_metadata["route_target"] == "autoyou_fine_tuning_agent"
