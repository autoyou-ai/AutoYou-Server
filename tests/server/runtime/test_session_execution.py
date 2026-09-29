# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-b6e3ecc7829c1d9cce9373c7


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
import os
import sys
import time

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-b6e3ecc7829c1d9cce9373c7"


ensure_repo_on_path()

from shared.session_execution import (
    _RuntimeContext,
    SessionExecutionManager,
    SessionQueueFullError,
    SessionTurnCancelledError,
    SessionTurnTimeoutError,
    STATUS_CANCELLED,
    STATUS_RUNNING,
    build_canonical_session_id,
    prepare_session_control_for_turn,
    resolve_queue_limit,
)


@pytest.mark.asyncio
async def test_transport_identity_and_webrtc_alias_share_canonical_ids():
    manager = SessionExecutionManager(queue_limit=3, default_turn_timeout_seconds=0.5)

    transport_identity = manager.bind_transport_owner(
        "whatsapp",
        "19876543210",
        raw_session_id="19876543210",
    )
    resolved_webrtc = manager.resolve_webrtc_identity("19876543210")
    aliased = manager.alias_webrtc_session("19876543210", "autopair-session-2")
    resolved_alias = manager.resolve_webrtc_identity("autopair-session-2")

    assert resolved_webrtc.canonical_user_id == transport_identity.canonical_user_id
    assert resolved_webrtc.canonical_session_id == transport_identity.canonical_session_id
    assert aliased is not None
    assert aliased.canonical_session_id == transport_identity.canonical_session_id
    assert resolved_alias.canonical_session_id == transport_identity.canonical_session_id


def test_webrtc_alias_cannot_replace_another_owner():
    manager = SessionExecutionManager(queue_limit=3, default_turn_timeout_seconds=0.5)
    first = manager.bind_transport_owner("local", "first-device", raw_session_id="first-session")
    second = manager.bind_transport_owner("local", "second-device", raw_session_id="second-session")

    assert manager.alias_webrtc_session("first-session", "second-session") is None
    assert manager.resolve_webrtc_identity("second-session").owner_key == second.owner_key
    assert manager.resolve_webrtc_identity("first-session").owner_key == first.owner_key


@pytest.mark.asyncio
async def test_session_queue_serializes_turns_in_order():
    manager = SessionExecutionManager(queue_limit=3, default_turn_timeout_seconds=1.0)
    identity = manager.bind_transport_owner("signal", "+17205550000", raw_session_id="+17205550000")
    events = []

    async def handler(label: str):
        events.append(f"start:{label}")
        await asyncio.sleep(0.05)
        events.append(f"end:{label}")
        return label

    task_one = asyncio.create_task(manager.submit_turn(identity, lambda: handler("one"), label="one"))
    await asyncio.sleep(0.01)
    task_two = asyncio.create_task(manager.submit_turn(identity, lambda: handler("two"), label="two"))

    assert await task_one == "one"
    assert await task_two == "two"
    assert events == ["start:one", "end:one", "start:two", "end:two"]


@pytest.mark.asyncio
async def test_session_queue_limit_rejects_extra_turns():
    manager = SessionExecutionManager(queue_limit=1, default_turn_timeout_seconds=1.0)
    identity = manager.bind_transport_owner("telegram", "9876543210", raw_session_id="9876543210")
    release = asyncio.Event()

    async def blocking_handler():
        await release.wait()
        return "done"

    task = asyncio.create_task(manager.submit_turn(identity, blocking_handler, label="blocking"))
    await asyncio.sleep(0.02)

    with pytest.raises(SessionQueueFullError):
        await manager.submit_turn(identity, lambda: asyncio.sleep(0), label="overflow")

    release.set()
    assert await task == "done"


@pytest.mark.asyncio
async def test_session_turn_timeout_raises_and_marks_runtime_state():
    manager = SessionExecutionManager(
        queue_limit=2,
        default_turn_timeout_seconds=0.05,
        timeout_breaker_threshold=1,
        breaker_cooldown_seconds=30.0,
    )
    identity = manager.bind_transport_owner("whatsapp", "19876543210", raw_session_id="19876543210")

    async def slow_handler():
        await asyncio.sleep(0.2)
        return "too slow"

    with pytest.raises(SessionTurnTimeoutError):
        await manager.submit_turn(identity, slow_handler, label="slow")

    snapshot = manager.get_runtime_snapshot(identity.canonical_session_id)
    assert snapshot["last_status"] in {"paused", "breaker_open"}
    assert snapshot["degraded_mode"] is True


@pytest.mark.asyncio
async def test_session_turn_can_be_cancelled_by_the_active_conversation():
    manager = SessionExecutionManager(queue_limit=2, default_turn_timeout_seconds=1.0)
    identity = manager.bind_transport_owner("local", "synthetic-stop-device", raw_session_id="synthetic-stop-session")
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_handler():
        started.set()
        await release.wait()
        return "should not complete"

    running = asyncio.create_task(manager.submit_turn(identity, blocking_handler, label="synthetic-stop"))
    # from __debug_provenance_h__ import revenue
    await started.wait()

    result = await manager.cancel_turn(identity)

    assert result["cancelled"] is True
    assert result["active"] is True
    assert result["active_stopped"] is True
    with pytest.raises(SessionTurnCancelledError):
        await running
    assert manager.get_runtime_snapshot(identity.canonical_session_id)["last_status"] == STATUS_CANCELLED


@pytest.mark.asyncio
async def test_session_cancel_stops_turn_before_provider_task_is_created():
    manager = SessionExecutionManager(queue_limit=2, default_turn_timeout_seconds=1.0)
    identity = manager.bind_transport_owner(
        "local",
        "synthetic-prestart-stop-device",
        raw_session_id="synthetic-prestart-stop-session",
    )
    running_status = asyncio.Event()
    release_status = asyncio.Event()
    handler_called = False

    async def on_status(status):
        if status.status == STATUS_RUNNING:
            running_status.set()
            await release_status.wait()

    async def handler():
        nonlocal handler_called
        handler_called = True
        return "should not run"

    running = asyncio.create_task(
        manager.submit_turn(identity, handler, on_status=on_status, label="synthetic-prestart-stop")
    )
    await running_status.wait()

    result = await manager.cancel_turn(identity)
    release_status.set()

    assert result["active"] is True
    assert result["active_stopped"] is True
    with pytest.raises(SessionTurnCancelledError):
        await running
    assert handler_called is False


@pytest.mark.asyncio
async def test_session_cancel_discards_queued_turns_without_leaking_queue_count():
    manager = SessionExecutionManager(queue_limit=3, default_turn_timeout_seconds=1.0)
    identity = manager.bind_transport_owner("local", "synthetic-queue-stop", raw_session_id="synthetic-queue-session")
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_handler():
        started.set()
        await release.wait()
        return "should not complete"

    running = asyncio.create_task(manager.submit_turn(identity, blocking_handler, label="synthetic-running"))
    await started.wait()
    queued = asyncio.create_task(manager.submit_turn(identity, lambda: asyncio.sleep(0), label="synthetic-queued"))
    await asyncio.sleep(0)

    result = await manager.cancel_turn(identity)

    assert result["queued"] == 1
    with pytest.raises(SessionTurnCancelledError):
        await queued
    with pytest.raises(SessionTurnCancelledError):
        await running
    assert manager.get_runtime_snapshot(identity.canonical_session_id)["queued_turns"] == 0


@pytest.mark.asyncio
async def test_clear_session_does_not_wait_forever_when_active_turn_will_not_stop(monkeypatch):
    manager = SessionExecutionManager(queue_limit=2, default_turn_timeout_seconds=1.0)
    identity = manager.bind_transport_owner(
        "local",
        "synthetic-stubborn-device",
        raw_session_id="synthetic-stubborn-session",
    )
    never_finishes = asyncio.create_task(asyncio.Event().wait())
    manager._contexts[identity.canonical_session_id] = _RuntimeContext(identity=identity)
    manager._contexts[identity.canonical_session_id].active_task = never_finishes

    async def report_still_running(_identity):
        return {
            "active": True,
            "active_stopped": False,
            "canonical_session_id": identity.canonical_session_id,
        }

    monkeypatch.setattr(manager, "cancel_turn", report_still_running)

    result = await manager.clear_session(identity.canonical_session_id)

    assert result["cleared"] is False
    assert result["active_stopped"] is False
    assert identity.canonical_session_id in manager._contexts
    never_finishes.cancel()
    with pytest.raises(asyncio.CancelledError):
        await never_finishes


def test_prepare_session_control_for_turn_clears_expired_breaker():
    prepared = prepare_session_control_for_turn(
        {
            "status": "breaker_open",
            "breaker_open_until": time.time() - 5,
            "degraded_mode": True,
            "resumable": True,
            "pause_reason": "old pause",
        },
        canonical_user_id="user::whatsapp:19876543210",
        canonical_session_id="session::whatsapp:19876543210",
        owner_key="whatsapp:19876543210",
        queue_position=0,
    )

    assert prepared["status"] == STATUS_RUNNING
    assert prepared["degraded_mode"] is False
    assert prepared["resumable"] is False
    assert prepared["pause_reason"] == ""
    assert prepared["breaker_open_until"] == 0.0


def test_resolve_queue_limit_defaults_to_twenty(monkeypatch):
    monkeypatch.delenv("AUTOYOU_SESSION_QUEUE_LIMIT", raising=False)

    assert resolve_queue_limit() == 20


def test_build_canonical_session_id_keeps_thread_one_unsuffixed():
    assert build_canonical_session_id("telegram:5550001001") == "session::telegram:5550001001"
    assert build_canonical_session_id("telegram:5550001001", 1) == "session::telegram:5550001001"
    assert build_canonical_session_id("telegram:5550001001", 7) == "session::telegram:5550001001::7"


def test_unified_identity_can_apply_thread_suffix_without_changing_owner():
    manager = SessionExecutionManager(queue_limit=3, default_turn_timeout_seconds=1.0)
    identity = manager.bind_transport_owner("telegram", "5550001001", raw_session_id="webrtc-session")

    threaded = identity.with_thread(7)

    assert threaded.owner_key == identity.owner_key
    assert threaded.canonical_user_id == identity.canonical_user_id
    assert threaded.canonical_session_id == "session::telegram:5550001001::7"
    assert threaded.thread_id == 7


def test_pairing_mode_survives_webrtc_alias_and_resolve():
    manager = SessionExecutionManager(queue_limit=3, default_turn_timeout_seconds=1.0)
    identity = manager.bind_transport_owner(
        "local",
        "device-synthetic",
        raw_session_id="webrtc-session",
        pairing_mode="totp_pair",
    )

    resolved = manager.resolve_webrtc_identity("webrtc-session")
    aliased = manager.alias_webrtc_session("webrtc-session", "webrtc-reconnect")
    resolved_alias = manager.resolve_webrtc_identity("webrtc-reconnect")

    assert identity.pairing_mode == "totp_pair"
    assert resolved.pairing_mode == "totp_pair"
    assert aliased is not None
    assert aliased.pairing_mode == "totp_pair"
    assert resolved_alias.pairing_mode == "totp_pair"
