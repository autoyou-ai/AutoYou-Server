# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
import asyncio
from types import SimpleNamespace

from autoyou_agents.admin_agent import agent as admin_agent
from autoyou_agents.audio_agent import agent as audio_agent
from autoyou_agents.notes_agent import agent as notes_agent
from autoyou_agents.shared_tools.datetime_tool import (
    get_current_datetime,
    inject_realtime_datetime_into_request,
)

def test_get_current_datetime_returns_iso_and_date():
    result = get_current_datetime()
    assert "iso" in result
    assert "date" in result
    assert "time" in result
    assert result.get("error") is None

def test_inject_realtime_datetime_sets_system_instruction():
    config = SimpleNamespace(system_instruction=None)
    llm_request = SimpleNamespace(config=config)

    inject_realtime_datetime_into_request(llm_request)

    assert isinstance(config.system_instruction, str)
    assert "[SYSTEM CLOCK]" in config.system_instruction
    assert "real current date" in config.system_instruction

def test_inject_realtime_datetime_prepends_to_existing_instruction():
    config = SimpleNamespace(system_instruction="You are a helpful assistant.")
    llm_request = SimpleNamespace(config=config)

    inject_realtime_datetime_into_request(llm_request)

    assert config.system_instruction.startswith("[SYSTEM CLOCK]")
    assert "You are a helpful assistant." in config.system_instruction

def test_inject_realtime_datetime_does_not_duplicate():
    config = SimpleNamespace(
        system_instruction="[SYSTEM CLOCK] Today is March 27, 2026, 11:00 AM. This is the real current date from the host machine clock. Your training data may be older - trust this date.\n\nYou are helpful."
    )
    llm_request = SimpleNamespace(config=config)

    inject_realtime_datetime_into_request(llm_request)

    # Should not have double "[SYSTEM CLOCK]" prefixes
    assert config.system_instruction.count("[SYSTEM CLOCK]") == 1

def test_inject_realtime_datetime_handles_missing_config():
    llm_request = SimpleNamespace()  # no config attribute
    # Should not raise
    inject_realtime_datetime_into_request(llm_request)

def _fake_user_llm_request(text: str):
    return SimpleNamespace(
        config=SimpleNamespace(system_instruction=None),
        contents=[SimpleNamespace(role="user", parts=[SimpleNamespace(text=text)])],
    )

def test_audio_before_model_callback_injects_datetime():
    llm_request = _fake_user_llm_request("hello there")

    response = asyncio.run(
        audio_agent._audio_before_model_callback(
            SimpleNamespace(state={}, invocation_id="dt-audio"),
            llm_request,
        )
    )

    assert response is None
    assert "[SYSTEM CLOCK]" in llm_request.config.system_instruction

def test_notes_before_model_callback_injects_datetime():
    llm_request = _fake_user_llm_request("hello there")

    response = asyncio.run(notes_agent._notes_before_model_callback(SimpleNamespace(), llm_request))

    assert response is None
    assert "[SYSTEM CLOCK]" in llm_request.config.system_instruction

def test_admin_before_model_callback_injects_datetime():
    llm_request = _fake_user_llm_request("hello there")

    response = asyncio.run(admin_agent._admin_before_model_callback(SimpleNamespace(state={}, invocation_id="dt-admin"), llm_request))

    assert response is None
    assert "[SYSTEM CLOCK]" in llm_request.config.system_instruction
