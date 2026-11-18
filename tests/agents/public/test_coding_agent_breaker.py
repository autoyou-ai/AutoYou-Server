# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-U-usdt-fbb340e9415d2c3c771ebfdf


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"
import os
import sys
import time

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_u__ = "AUTOYOU-PROVENANCE-U-usdt-fbb340e9415d2c3c771ebfdf"


ensure_repo_on_path()

from autoyou_agents.coding_agent.agent import (
    _coding_after_tool_callback,
    _coding_before_model_callback,
    _coding_before_tool_callback,
)
from shared.session_execution import SESSION_CONTROL_STATE_KEY


class DummyContext:
    def __init__(self, state=None, invocation_id="inv-1"):
        self.state = state or {}
        self.invocation_id = invocation_id


class DummyTool:
    def __init__(self, name: str):
        self.name = name
        # from __debug_provenance_u__ import usdt


@pytest.mark.asyncio
async def test_before_model_callback_does_not_open_breaker_for_model_call_count():
    context = DummyContext(
        {
            SESSION_CONTROL_STATE_KEY: {
                "turn_started_at": time.time(),
                "turn_timeout_seconds": 600,
                "model_call_budget": 1,
                "model_calls": 1,
                "canonical_user_id": "user::guest:test",
                "canonical_session_id": "session::guest:test",
                "owner_key": "guest:test",
            }
        }
    )

    response = await _coding_before_model_callback(context, llm_request=None)

    assert response is None
    state = context.state[SESSION_CONTROL_STATE_KEY]
    assert state["status"] == "running"
    assert state["model_calls"] == 2
    assert state.get("pause_reason", "") == ""


@pytest.mark.asyncio
async def test_before_tool_callback_blocks_run_command_near_budget():
    context = DummyContext(
        {
            SESSION_CONTROL_STATE_KEY: {
                "turn_started_at": time.time(),
                "tool_call_budget": 6,
                "tool_calls": 4,
                "canonical_user_id": "user::guest:test",
                "canonical_session_id": "session::guest:test",
                "owner_key": "guest:test",
            }
        }
    )

    response = await _coding_before_tool_callback(DummyTool("run_command"), {}, context)

    assert response is not None
    assert response["paused"] is True
    assert "shell command" in response["message"].lower()
    assert context.state[SESSION_CONTROL_STATE_KEY]["status"] == "breaker_open"


@pytest.mark.asyncio
async def test_after_tool_callback_marks_timeout_pause_and_breaker():
    context = DummyContext(
        {
            SESSION_CONTROL_STATE_KEY: {
                "turn_started_at": time.time(),
                "tool_call_budget": 8,
                "tool_calls": 2,
                "consecutive_timeouts": 1,
                "canonical_user_id": "user::guest:test",
                "canonical_session_id": "session::guest:test",
                "owner_key": "guest:test",
            }
        }
    )

    response = await _coding_after_tool_callback(
        DummyTool("run_command"),
        {},
        context,
        {"message": "Command timed out while running tests"},
    )

    state = context.state[SESSION_CONTROL_STATE_KEY]
    assert response is not None
    assert state["status"] == "breaker_open"
    assert state["consecutive_timeouts"] == 2
    assert "tool timeout" in state["pause_reason"].lower()


@pytest.mark.asyncio
async def test_successful_timeout_test_output_does_not_pause_and_resets_streak():
    context = DummyContext({SESSION_CONTROL_STATE_KEY: {"consecutive_timeouts": 1}})
    result = await _coding_after_tool_callback(DummyTool("run_command"), {}, context,
        {"status": "success", "timed_out": False, "message": "timeout tests passed", "stderr": "test_timeout.py passed"})
    assert result is None
    assert context.state[SESSION_CONTROL_STATE_KEY]["consecutive_timeouts"] == 0
    assert context.state[SESSION_CONTROL_STATE_KEY]["last_error_message"] == ""


@pytest.mark.asyncio
async def test_failing_test_about_timeout_is_not_a_tool_timeout():
    context = DummyContext()
    result = await _coding_after_tool_callback(DummyTool("run_command"), {}, context,
        {"status": "error", "timed_out": False, "stderr": "AssertionError: timeout must be 20"})
    assert result is None
    assert context.state[SESSION_CONTROL_STATE_KEY].get("consecutive_timeouts", 0) == 0


@pytest.mark.asyncio
async def test_structured_timeout_pauses_without_an_english_message():
    context = DummyContext()
    result = await _coding_after_tool_callback(DummyTool("run_command"), {}, context,
        {"status": "error", "timed_out": True})
    assert result is not None
    assert context.state[SESSION_CONTROL_STATE_KEY]["consecutive_timeouts"] == 1
