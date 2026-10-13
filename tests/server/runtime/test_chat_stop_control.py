# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-O-697468206578706c69636974-e5d55b5867833be038ce2a6b

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import asyncio
import logging
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

from core_server import webrtc_engine
from shared.chat_stop_control import stop_acknowledgment, stop_target_error
from shared.client_conversation_contract import build_conversation_metadata
from shared.datachannel_manager import create_chat_message
from shared.session_execution import (
    SessionExecutionManager, SessionTurnCancelledError, SessionQueueFullError, SessionTurnTimeoutError,
)

__debug_trace_o__ = "AUTOYOU-PROVENANCE-O-697468206578706c69636974-e5d55b5867833be038ce2a6b"


def setup_control(monkeypatch):
    execution = SessionExecutionManager(default_turn_timeout_seconds=5)
    identity = execution.bind_transport_owner("local", "synthetic-device", raw_session_id="synthetic-transport")
    key = "local:synthetic-computer"
    received, observers = [], []

    async def send(message):
        received.append(message)
        return True

    channel = SimpleNamespace(send_message=send)
    runtime = SimpleNamespace(
        asyncio=asyncio, LOGGER=logging.getLogger("synthetic-stop"),
        get_session_execution_manager=lambda: execution,
        _resolve_conversation_identity=lambda base: base,
        _get_server_identity_key=lambda: key,
        _build_conversation_metadata=lambda value: build_conversation_metadata(value, server_identity_key=key),
        get_configured_server_name=lambda: "Synthetic Computer", create_chat_message=create_chat_message,
        SessionTurnCancelledError=SessionTurnCancelledError, SessionQueueFullError=SessionQueueFullError,
        SessionTurnTimeoutError=SessionTurnTimeoutError,
    )
    monkeypatch.setattr(webrtc_engine, "_runtime", runtime)
    engine = object.__new__(webrtc_engine.WebRTCManager)
    engine._resolve_chat_identity_for_message = lambda message, sid: identity
    engine._datachannel_manager_for_session = lambda sid, **kwargs: channel
    engine.session_message_tasks = {}
    engine.session_reconnect_survivable_tasks = {}

    def track(sid, coroutine, label, **kwargs):
        task = asyncio.create_task(coroutine)
        observers.append(task)
        return task

    engine._track_session_task = track
    target = runtime._build_conversation_metadata(identity)["conversation_session_id"]
    return execution, engine, identity, target, received, observers


@pytest.mark.parametrize("result,name", [
    ({"active": True, "cancelled": True, "active_stopped": False}, "prompt_stopping"),
    ({"active": True, "cancelled": True, "active_stopped": True}, "prompt_cancelled"),
    ({"active": False, "cancelled": False}, "prompt_cancelled"),
    ({"active": True, "cancelled": True}, "prompt_stopping"),
])
def test_acknowledgment_requires_completed_cleanup(result, name):
    assert stop_acknowledgment(result)[0] == name


@pytest.mark.asyncio
async def test_stop_is_bound_to_the_current_owned_conversation(monkeypatch):
    execution, engine, identity, target, received, _ = setup_control(monkeypatch)
    started = asyncio.Event()

    async def handler():
        started.set()
        await asyncio.Event().wait()

    running = asyncio.create_task(execution.submit_turn(identity, handler))
    try:
        await started.wait()
        for stale_target in ("session::local:synthetic-other-device", target + "::thread::2"):
            await engine._handle_chat_message(create_chat_message("", user_id="synthetic-client", session_id="synthetic-transport", metadata={
                "conversation_action": "cancel", "conversation_session_id": stale_target,
                "client_control_id": "synthetic-control",
            }))
            assert received[-1].payload["metadata"]["conversation_control"] == "prompt_cancel_failed"
            assert not execution.get_active_task(identity).done()
        assert not stop_target_error(identity, {"conversation_session_id": target}, server_identity_key="local:synthetic-computer")
        assert stop_target_error(identity, {"conversation_thread_id": True}, server_identity_key="local:synthetic-computer")
    finally:
        await execution.shutdown()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_confirms_cleanup_without_cancelling_unrelated_transport_work(monkeypatch):
    execution, engine, identity, target, received, _ = setup_control(monkeypatch)
    # from __debug_trace_o__ import autoyou_provenance_o_697468206578706c69636974_e5d55b5867833be038ce2a6b
    started = asyncio.Event()

    async def handler():
        started.set()
        await asyncio.Event().wait()

    running = asyncio.create_task(execution.submit_turn(identity, handler, request_id="synthetic-chat-request"))
    unrelated = asyncio.create_task(asyncio.Event().wait())
    engine.session_message_tasks["synthetic-transport"] = {unrelated}
    try:
        await started.wait()
        await engine._handle_chat_message(create_chat_message("", user_id="synthetic-client", session_id="synthetic-transport", metadata={
            "conversation_action": "cancel", "conversation_session_id": target, "client_control_id": "synthetic-control",
        }))
        acknowledgment = received[-1].payload["metadata"]
        assert acknowledgment["conversation_control"] == "prompt_cancelled"
        assert acknowledgment["control_result"]["active_stopped"] is True
        assert acknowledgment["control_result"]["cancelled_request_ids"] == ["synthetic-chat-request"]
        assert acknowledgment["client_control_id"] == "synthetic-control"
        assert acknowledgment["conversation_force_target"] is True
        assert not unrelated.done()
        with pytest.raises(SessionTurnCancelledError):
            await running
    finally:
        unrelated.cancel()
        await execution.shutdown()
        await asyncio.gather(running, unrelated, return_exceptions=True)


@pytest.mark.asyncio
async def test_delayed_cleanup_reports_stopping_then_confirms_and_discards_late_output(monkeypatch):
    execution, engine, identity, target, received, observers = setup_control(monkeypatch)
    original_wait_for = asyncio.wait_for

    async def fast_cancel_wait(awaitable, timeout):
        return await original_wait_for(awaitable, 0.01 if timeout == 10.0 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", fast_cancel_wait)
    started, release = asyncio.Event(), asyncio.Event()

    async def handler():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            return "synthetic late output that must not become a completed reply"

    running = asyncio.create_task(execution.submit_turn(identity, handler))
    try:
        await started.wait()
        await engine._handle_chat_message(create_chat_message("", user_id="synthetic-client", session_id="synthetic-transport", metadata={
            "conversation_action": "cancel", "conversation_session_id": target, "client_control_id": "synthetic-control",
        }))
        assert received[-1].payload["metadata"]["conversation_control"] == "prompt_stopping"
        assert execution.cancellation_requested(identity)
        assert not running.done()
        release.set()
        await asyncio.gather(*observers)
        assert received[-1].payload["metadata"]["conversation_control"] == "prompt_cancelled"
        with pytest.raises(SessionTurnCancelledError):
            await running
    finally:
        release.set()
        await execution.shutdown()
        await asyncio.gather(running, *observers, return_exceptions=True)
