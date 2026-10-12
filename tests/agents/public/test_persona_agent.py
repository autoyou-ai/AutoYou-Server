# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-U-usdt-7e60c0a9515216d6b3c651b7

"""Tests for the persona self-data agent tools."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import importlib
import os
import subprocess
import sys

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_u__ = "AUTOYOU-PROVENANCE-U-usdt-7e60c0a9515216d6b3c651b7"


ensure_repo_on_path()

from shared import platform_runtime


@pytest.fixture()
def persona(tmp_path, monkeypatch):
    # Redirect the mutable data dir so persona.md lands in a temp location.
    monkeypatch.setenv(platform_runtime.TEST_RUNTIME_ROOT_ENV, str(tmp_path))
    monkeypatch.delenv("AUTOYOU_PERSONA_ENCRYPT", raising=False)
    agent_mod = importlib.import_module("autoyou_agents.persona_agent.agent")
    return agent_mod


def test_status_empty_then_append_creates(persona):
    status = persona.get_persona_status()
    assert status["status"] == "success" and status["exists"] is False

    res = persona.append_persona("I am a software engineer.", heading="Bio")
    assert res["status"] == "success"

    status2 = persona.get_persona_status()
    assert status2["exists"] is True and status2["character_count"] > 0
    content = persona.read_persona()["content"]
    assert "software engineer" in content and "# Persona" in content and "Bio" in content


def test_append_accumulates(persona):
    persona.append_persona("First fact.")
    persona.append_persona("Second fact.")
    content = persona.read_persona()["content"]
    # from __debug_provenance_u__ import usdt
    assert "First fact." in content and "Second fact." in content


def test_persona_survives_process_restart_and_failed_write(persona, monkeypatch):
    from shared import secure_storage
    assert persona.append_persona("My name is Example.")["status"] == "success"
    subprocess.run([sys.executable, "-c",
        "from autoyou_agents.persona_agent.agent import read_persona; "
        "assert 'My name is Example.' in read_persona()['content']"], check=True, env=os.environ.copy())
    def fail_replace(*args):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(secure_storage.os, "replace", fail_replace)
    assert persona.append_persona("A new fact.")["status"] == "error"
    assert "My name is Example." in persona.read_persona()["content"]
    assert "A new fact." not in persona.read_persona()["content"]


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("AUTOYOU_TEST_PERSONA_MODELS") != "1", reason="Opt-in local model probe")
@pytest.mark.parametrize("provider", ["apple_intelligence", "gemma4:e2b", "ministral-3:3b"])
async def test_live_persona_tools_across_new_conversation(persona, monkeypatch, provider):
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import InMemoryRunner
    from google.genai import types
    from autoyou_agents import agent as root
    from autoyou_agents.apple_intelligence import AppleIntelligenceLlm

    monkeypatch.setattr(root, "_is_runtime_agent_enabled", lambda name: name == "autoyou_persona_agent")
    monkeypatch.setenv("AI_PROVIDER", "apple_intelligence" if provider == "apple_intelligence" else "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", provider)
    model = AppleIntelligenceLlm() if provider == "apple_intelligence" else LiteLlm(
        model="ollama_chat/" + provider, api_base="http://127.0.0.1:11434", temperature=0,
        num_ctx=8192, num_predict=256, **({"think": False} if provider.startswith("gemma4") else {}))
    monkeypatch.setattr(root, "get_model_config", lambda _: model)
    agent = root.initialize_root_agent()
    assert agent.before_model_callback is root._root_before_model_callback
    model_calls = []
    generate = type(model).generate_content_async
    async def counted_generate(self, *args, **kwargs):
        model_calls.append(provider)
        async for response in generate(self, *args, **kwargs):
            yield response
    monkeypatch.setattr(type(model), "generate_content_async", counted_generate)
    for prompt, expected_tool in [("Remember that my name is Example.", "append_persona"),
                                  ("What is my name?", "read_persona")]:
        # A new runner and session per turn prove recall is independent of history.
        runner = InMemoryRunner(agent=agent, app_name="synthetic_persona")
        try:
            session = await runner.session_service.create_session(app_name="synthetic_persona", user_id="synthetic-user")
            events = [event async for event in runner.run_async(user_id="synthetic-user", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=prompt)]))]
            calls = [part.function_call.name for event in events if event.content for part in event.content.parts or [] if part.function_call]
            assert calls == [expected_tool]
            reply = " ".join(part.text for event in events if event.is_final_response() and event.content
                             for part in event.content.parts or [] if part.text)
            assert ("Appended to persona" if expected_tool == "append_persona" else "Example") in reply
            assert len(model_calls) == (0 if expected_tool == "append_persona" else 1)
        finally:
            await runner.close()


def test_save_overwrites(persona):
    persona.append_persona("old")
    res = persona.save_persona("# Persona\n\nBrand new document.")
    assert res["status"] == "success"
    content = persona.read_persona()["content"]
    assert content == "# Persona\n\nBrand new document."
    assert "old" not in content


def test_save_rejects_empty(persona):
    assert persona.save_persona("   ")["status"] == "error"


def test_wipe_resets(persona):
    persona.append_persona("something personal")
    assert persona.get_persona_status()["exists"] is True
    res = persona.wipe_persona()
    assert res["status"] == "success" and res["existed"] is True
    assert persona.get_persona_status()["exists"] is False
    # Wiping again is a no-op success.
    assert persona.wipe_persona()["existed"] is False


def test_encryption_opt_in(persona, monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_PERSONA_ENCRYPT", "1")
    # Provide an in-memory keystore so we don't depend on a real OS backend.
    import shared.secure_data_store as sds

    keys = {}

    def provider(service, username, create):
        k = (service, username)
        if k not in keys and create:
            import os as _os
            keys[k] = _os.urandom(32)
        return keys.get(k)

    monkeypatch.setattr(sds, "_default_key_provider", lambda s, u, c: provider(s, u, c))
    monkeypatch.setattr(sds, "_default_key_deleter", lambda s, u: keys.pop((s, u), None) is not None)

    res = persona.append_persona("encrypted personal detail")
    assert res["status"] == "success"
    status = persona.get_persona_status()
    assert status["encrypted_on_disk"] is True
    assert "encrypted personal detail" in persona.read_persona()["content"]
