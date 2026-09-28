# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-2400a4827da3aa2ac241eced

"""The Mac provider stays optional and tools still run through ADK."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-2400a4827da3aa2ac241eced"

import asyncio
import copy
import json
import os
from pathlib import Path
import sys

import pytest
from google.adk.agents import Agent
from google.adk.models.llm_request import LlmRequest
from google.adk.runners import InMemoryRunner
from google.genai import types

from autoyou_agents import apple_intelligence as adapter
from shared import apple_intelligence as native


@pytest.mark.parametrize("system,machine,version", [
    ("linux", "arm64", "26.5"), ("win32", "AMD64", ""),
    ("darwin", "x86_64", "26.5"), ("darwin", "arm64", "15.0"),
])
@pytest.mark.asyncio
async def test_unsupported_hosts_never_start_native_helper(monkeypatch, system, machine, version):
    monkeypatch.setattr(native.sys, "platform", system)
    monkeypatch.setattr(native.platform, "machine", lambda: machine)
    monkeypatch.setattr(native.platform, "mac_ver", lambda: (version, (), ""))
    monkeypatch.setenv("AUTOYOU_APPLE_MODEL_HELPER", sys.executable)
    assert native.helper_path() is None
    assert not (await native.status())["supported"]
    with pytest.raises(RuntimeError, match="Choose another chat mode"):
        await native.request({"operation": "generate"})


@pytest.mark.asyncio
async def test_provider_is_lazy_and_does_not_fall_back(monkeypatch):
    from autoyou_agents.model_config import get_model_config
    monkeypatch.setenv("AI_PROVIDER", "apple_intelligence")
    monkeypatch.setattr(native, "helper_path", lambda: None)
    model = get_model_config(None)
    assert isinstance(model, adapter.AppleIntelligenceLlm)
    with pytest.raises(RuntimeError, match="Apple Intelligence needs"):
        request = LlmRequest(contents=[types.Content(parts=[types.Part(text="hello")])])
        await anext(model.generate_content_async(request))


@pytest.mark.asyncio
async def test_cancellation_reaps_helper(monkeypatch, tmp_path):
    helper = tmp_path / "model-helper"
    helper.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    helper.chmod(0o700)
    monkeypatch.setattr(native, "helper_path", lambda: helper)
    processes = []
    spawn = asyncio.create_subprocess_exec
    async def capture(*args, **kwargs):
        if sys.platform == "win32":
            args = (sys.executable, "-c", "import time; time.sleep(60)")
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(native.asyncio, "create_subprocess_exec", capture)
    task = asyncio.create_task(native.request({"operation": "generate"}))
    for _ in range(100):
        if processes:
            break
        await asyncio.sleep(0.01)
    assert processes
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_tool_result_roundtrip_uses_adk(monkeypatch):
    calls, payloads = [], []
    def lookup_colour(sample_id: str) -> dict:
        """Look up a sample's stored colour."""
        calls.append(sample_id)
        return {"colour": "indigo"}
    async def fake_request(payload):
        payloads.append(copy.deepcopy(payload))
        if len(payloads) == 1:
            assert payload["tools"][0]["parameters"]["properties"]["sample_id"]["type"] == "string"
            return {"calls": [{"name": "lookup_colour", "arguments": '{"sample_id":"sample-42"}'}]}
        assert payload["messages"][-1]["role"] == "tool"
        assert json.loads(payload["messages"][-1]["text"])["colour"] == "indigo"
        return {"text": "The stored colour is indigo."}
    monkeypatch.setattr(adapter, "request", fake_request)
    runner = InMemoryRunner(agent=Agent(name="synthetic_agent", model=adapter.AppleIntelligenceLlm(),
        instruction="Use the lookup tool for stored data.", tools=[lookup_colour]), app_name="synthetic_apple")
    session = await runner.session_service.create_session(app_name="synthetic_apple", user_id="synthetic-user")
    events = [event async for event in runner.run_async(user_id="synthetic-user", session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="Look up sample-42.")]))]
    await runner.close()
    assert calls == ["sample-42"]
    assert events[-1].content.parts[0].text == "The stored colour is indigo."
    assert payloads[0]["instructions"].startswith("Use the lookup tool")


def test_optional_nested_maps_and_enums_keep_their_types():
    schema = {"type": "OBJECT", "properties": {
        "payload": {"type": "OBJECT", "additionalProperties": {"type": "STRING"}},
        "mode": {"type": "STRING", "enum": ["one", "two"]},
        "items": {"type": "ARRAY", "items": {"type": "INTEGER"}},
    }, "required": ["payload"]}
    translated = adapter._schema(schema)
    assert translated["properties"]["payload"]["type"] == "string"
    assert translated["properties"]["mode"]["enum"] == ["one", "two"]
    assert translated["required"] == ["payload"]
    assert adapter._restore({"payload": '{"label":"synthetic"}', "items": [1, 2]}, schema) == {
        "payload": {"label": "synthetic"}, "items": [1, 2]}
    with pytest.raises(ValueError, match="does not match"):
        adapter._validated({"payload": '{"label":7}'}, schema)
    with pytest.raises(ValueError, match="does not match"):
        adapter._validated({"payload": '{}', "mode": "not-advertised"}, schema)


@pytest.mark.asyncio
async def test_unavailable_selection_keeps_saved_settings(monkeypatch):
    import server
    original = {"ai_provider": {"provider": "ollama"}}
    monkeypatch.setattr(server.STATE, "config", copy.deepcopy(original))
    async def unavailable():
        return {"supported": True, "available": False, "detail": "Turn on Apple Intelligence."}
    monkeypatch.setattr(native, "status", unavailable)
    with pytest.raises(ValueError, match="Turn on"):
        await server._apply_admin_ui_config_update({"ai_provider": {"provider": "apple_intelligence"}})
    assert server.STATE.config == original


def test_native_selection_enables_existing_agent_runtime(monkeypatch):
    import server
    monkeypatch.setattr(native, "helper_path", lambda: Path("/synthetic/AutoYouModel"))
    config = server._default_config()
    updated, touched, _ = server._apply_admin_ui_config_patch(config, {"ai_provider": {"provider": "apple_intelligence"}})
    assert updated["ai_provider"]["provider"] == "apple_intelligence"
    assert not server._is_native_gateway_provider("apple_intelligence")
    monkeypatch.setattr(server.STATE, "config", updated)
    monkeypatch.setattr(server.os, "environ", os.environ.copy())
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    server._apply_google_api_config_to_env()
    assert os.environ["AI_PROVIDER"] == "apple_intelligence"


def test_context_and_errors_describe_selected_provider():
    import rest_api
    assert rest_api._active_model_name_for_provider("apple_intelligence") == "apple_intelligence/on-device"
    assert rest_api._resolve_context_window("apple_intelligence", "")[0] == 4096
    error = rest_api._classify_ai_backend_error("Apple Intelligence's context is full.")
    assert error["provider"] == "apple_intelligence"
    assert "Start a new chat" in error["user_message"]


def test_apple_root_keeps_custom_instructions_and_safety(monkeypatch):
    import autoyou_agents.agent as root
    monkeypatch.setenv("AI_PROVIDER", "apple_intelligence")
    instruction = root._build_effective_agent_instruction(["internet_agent", "notes_agent"])
    assert root.root_prompt.SAFETY_RULES in instruction
    assert len(instruction) < 7000
    assert root._provider_requires_explicit_agent_tools() and root._two_stage_routing_enabled()
    custom = "Synthetic operator rule: always say when a result is incomplete."
    monkeypatch.setattr(root.root_prompt, "AGENT_INSTRUCTION", custom)
    assert custom in root._build_effective_agent_instruction(["notes_agent"])


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("AUTOYOU_TEST_APPLE_MODEL") != "1", reason="Opt-in real Mac model probe")
async def test_live_native_tool_roundtrip_and_context_limit():
    assert (await native.status())["available"]
    payload = {"operation": "generate", "instructions": "Use lookup_colour for stored colours. Answer briefly from its result.",
        "messages": [{"role": "user", "text": "Look up the stored colour of sample-42."}],
        "tools": [{"name": "lookup_colour", "description": "Look up a sample's stored colour.", "parameters": {
            "type": "object", "properties": {"sample_id": {"type": "string"}}, "required": ["sample_id"]}}],
        "maximum_tokens": 128, "temperature": 0}
    first = await native.request(payload)
    call = first["calls"][0]
    assert call["name"] == "lookup_colour"
    assert json.loads(call["arguments"])["sample_id"] == "sample-42"
    payload["messages"] += [{"role": "call", "id": "synthetic-call", "name": call["name"], "text": call["arguments"]},
        {"role": "tool", "id": "synthetic-call", "name": call["name"], "text": '{"colour":"indigo"}'}]
    second = await native.request(payload)
    assert "indigo" in second["text"].lower()
    assert not second.get("calls")
    payload["instructions"] = "Keep this instruction. " * 5000
    with pytest.raises(RuntimeError, match="context"):
        await native.request(payload)
