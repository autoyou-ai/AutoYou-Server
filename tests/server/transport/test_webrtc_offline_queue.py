# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-37a75d0ccde277eec46fe8e8

"""
Tests for the WebRTC offline voice-reply queue.

Covers:
- _enqueue_to_offline_queue: stores messages, enforces TTL eviction and size cap
- _flush_offline_queue: delivers messages on reconnect, skips expired, privacy isolation
- _buffer_voice_chat_message: routes 'voice reply' to offline queue, others to pending buffer
- Full round-trip: voice reply buffered then delivered after reconnect
- Shutdown clears offline queue
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import base64
import os
import sys
import time

import pytest

from tests.support.connected_device import OpenChannel, connect_device
from tests.support.paths import ensure_repo_on_path

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-37a75d0ccde277eec46fe8e8"


ensure_repo_on_path()

import server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SendCapture:
    """Minimal DataChannelManager stub that captures sent messages."""

    def __init__(self, *, succeed: bool = True):
        self.sent: list = []
        self._succeed = succeed

    async def send_message(self, message):
        if self._succeed:
            self.sent.append(message)
        return self._succeed


class _SlowSendCapture(_SendCapture):
    async def send_message(self, message):
        await asyncio.sleep(0.01)
        return await super().send_message(message)


def _make_message(text: str) -> object:
    """Create a trivial stand-in chat message."""
    from types import SimpleNamespace
    return SimpleNamespace(text=text)


# ---------------------------------------------------------------------------
# _enqueue_to_offline_queue
# ---------------------------------------------------------------------------

def test_enqueue_to_offline_queue_stores_message():
    webrtc = server.WebRTCManager()
    msg = _make_message("hello")
    result = webrtc._enqueue_to_offline_queue("session-a", msg)
    assert result == "buffered"
    assert len(webrtc._offline_pending_messages["session-a"]) == 1
    assert webrtc._offline_pending_messages["session-a"][0]["message"] is msg


def test_enqueue_to_offline_queue_empty_session_id_returns_failed():
    webrtc = server.WebRTCManager()
    result = webrtc._enqueue_to_offline_queue("", _make_message("x"))
    assert result == "failed"
    assert not webrtc._offline_pending_messages


def test_enqueue_to_offline_queue_evicts_expired_entries():
    webrtc = server.WebRTCManager()
    # Plant an entry that is already past TTL
    old_ts = time.time() - server._WEBRTC_OFFLINE_QUEUE_TTL_SECONDS - 10
    webrtc._offline_pending_messages["s"] = [{"enqueued_at": old_ts, "message": _make_message("old")}]

    result = webrtc._enqueue_to_offline_queue("s", _make_message("new"))
    assert result == "buffered"
    queue = webrtc._offline_pending_messages["s"]
    assert len(queue) == 1
    assert queue[0]["message"].text == "new"


def test_enqueue_to_offline_queue_drops_oldest_at_cap(monkeypatch):
    monkeypatch.setattr(server, "_WEBRTC_OFFLINE_QUEUE_MAX_SIZE", 3)
    webrtc = server.WebRTCManager()
    for i in range(3):
        webrtc._enqueue_to_offline_queue("s", _make_message(f"msg{i}"))

    result = webrtc._enqueue_to_offline_queue("s", _make_message("overflow"))
    assert result == "buffered"
    queue = webrtc._offline_pending_messages["s"]
    assert len(queue) == 3  # cap still 3
    # Oldest (msg0) was dropped; overflow is at the end
    assert queue[-1]["message"].text == "overflow"
    assert queue[0]["message"].text == "msg1"


def test_enqueue_to_offline_queue_privacy_isolation():
    webrtc = server.WebRTCManager()
    webrtc._enqueue_to_offline_queue("alice", _make_message("alice-msg"))
    webrtc._enqueue_to_offline_queue("bob", _make_message("bob-msg"))
    assert len(webrtc._offline_pending_messages["alice"]) == 1
    assert len(webrtc._offline_pending_messages["bob"]) == 1
    assert webrtc._offline_pending_messages["alice"][0]["message"].text == "alice-msg"
    assert webrtc._offline_pending_messages["bob"][0]["message"].text == "bob-msg"


# ---------------------------------------------------------------------------
# _flush_offline_queue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flush_offline_queue_delivers_messages():
    webrtc = server.WebRTCManager()
    dc = _SendCapture()
    webrtc.datachannel_managers["s"] = dc

    webrtc._enqueue_to_offline_queue("s", _make_message("r1"))
    webrtc._enqueue_to_offline_queue("s", _make_message("r2"))
    await webrtc._flush_offline_queue("s")

    assert len(dc.sent) == 2
    assert dc.sent[0].text == "r1"
    assert dc.sent[1].text == "r2"
    assert "s" not in webrtc._offline_pending_messages


@pytest.mark.asyncio
async def test_concurrent_offline_queue_flush_delivers_once():
    webrtc = server.WebRTCManager()
    dc = _SlowSendCapture()
    webrtc.datachannel_managers["s"] = dc
    webrtc._enqueue_to_offline_queue("s", _make_message("r1"))

    await asyncio.gather(
        webrtc._flush_offline_queue("s"),
        webrtc._flush_offline_queue("s"),
    )

    assert [message.text for message in dc.sent] == ["r1"]
    assert "s" not in webrtc._offline_pending_messages


@pytest.mark.asyncio
async def test_flush_offline_queue_skips_when_no_datachannel():
    webrtc = server.WebRTCManager()
    webrtc._enqueue_to_offline_queue("s", _make_message("pending"))
    # No datachannel_manager registered
    await webrtc._flush_offline_queue("s")
    # Message should remain in the queue
    assert len(webrtc._offline_pending_messages["s"]) == 1


@pytest.mark.asyncio
async def test_flush_offline_queue_skips_expired_entries():
    webrtc = server.WebRTCManager()
    dc = _SendCapture()
    webrtc.datachannel_managers["s"] = dc

    old_ts = time.time() - server._WEBRTC_OFFLINE_QUEUE_TTL_SECONDS - 10
    webrtc._offline_pending_messages["s"] = [
        {"enqueued_at": old_ts, "message": _make_message("expired")},
        {"enqueued_at": time.time(), "message": _make_message("fresh")},
    ]
    await webrtc._flush_offline_queue("s")

    assert len(dc.sent) == 1
    assert dc.sent[0].text == "fresh"
    assert "s" not in webrtc._offline_pending_messages


@pytest.mark.asyncio
async def test_flush_offline_queue_stops_on_send_failure():
    webrtc = server.WebRTCManager()
    dc = _SendCapture(succeed=False)
    webrtc.datachannel_managers["s"] = dc

    webrtc._enqueue_to_offline_queue("s", _make_message("msg1"))
    webrtc._enqueue_to_offline_queue("s", _make_message("msg2"))
    await webrtc._flush_offline_queue("s")

    # Nothing delivered, queue preserved
    assert dc.sent == []
    assert len(webrtc._offline_pending_messages["s"]) == 2


@pytest.mark.asyncio
async def test_flush_offline_queue_privacy_no_cross_client_delivery():
    """Messages queued for alice must never be delivered to bob's datachannel."""
    webrtc = server.WebRTCManager()
    dc_alice = _SendCapture()
    dc_bob = _SendCapture()
    webrtc.datachannel_managers["alice"] = dc_alice
    webrtc.datachannel_managers["bob"] = dc_bob

    webrtc._enqueue_to_offline_queue("alice", _make_message("alice-secret"))
    webrtc._enqueue_to_offline_queue("bob", _make_message("bob-msg"))

    await webrtc._flush_offline_queue("alice")

    assert len(dc_alice.sent) == 1
    assert dc_alice.sent[0].text == "alice-secret"
    # Bob's datachannel must NOT have received alice's message
    assert dc_bob.sent == []


# ---------------------------------------------------------------------------
# _buffer_voice_chat_message routing
# ---------------------------------------------------------------------------

def test_buffer_voice_reply_goes_to_offline_queue():
    webrtc = server.WebRTCManager()
    msg = _make_message("ai reply")
    result = webrtc._buffer_voice_chat_message("s", msg, label="voice reply")
    assert result == "buffered"
    # Must be in offline queue, NOT in pending_voice_chat_messages
    assert "s" in webrtc._offline_pending_messages
    assert "s" not in webrtc.pending_voice_chat_messages


def test_buffer_voice_transcript_goes_to_short_term_buffer():
    webrtc = server.WebRTCManager()
    msg = _make_message("user said hello")
    result = webrtc._buffer_voice_chat_message("s", msg, label="voice transcript")
    assert result == "buffered"
    assert "s" in webrtc.pending_voice_chat_messages
    assert "s" not in webrtc._offline_pending_messages


def test_buffer_voice_status_goes_to_short_term_buffer():
    webrtc = server.WebRTCManager()
    msg = _make_message("processing...")
    result = webrtc._buffer_voice_chat_message("s", msg, label="voice status")
    assert result == "buffered"
    assert "s" in webrtc.pending_voice_chat_messages
    assert "s" not in webrtc._offline_pending_messages


# ---------------------------------------------------------------------------
# Round-trip: voice reply survives disconnect and is delivered on reconnect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_voice_reply_buffered_then_delivered_on_reconnect():
    webrtc = server.WebRTCManager()
    msg = _make_message("AI response after disconnect")

    # Simulate: no datachannel (client disconnected)
    result = await webrtc._deliver_voice_chat_message("session-x", msg, label="voice reply")
    assert result == "buffered"
    assert "session-x" in webrtc._offline_pending_messages

    # Cleanup runs (simulating WebRTC session teardown) - offline queue must survive
    webrtc.pending_voice_chat_messages.pop("session-x", None)
    # _offline_pending_messages should still have the entry
    assert "session-x" in webrtc._offline_pending_messages

    # Client reconnects: datachannel becomes available
    dc = _SendCapture()
    webrtc.datachannel_managers["session-x"] = dc
    await webrtc._flush_pending_voice_chat_messages("session-x")

    assert len(dc.sent) == 1
    assert dc.sent[0].text == "AI response after disconnect"
    assert "session-x" not in webrtc._offline_pending_messages


@pytest.mark.asyncio
async def test_voice_note_reply_audio_attachment_survives_offline_queue_reconnect():
    from shared.datachannel_manager import create_chat_message

    webrtc = server.WebRTCManager()
    audio_bytes = b"OggS queued voice reply"
    audio_data = base64.b64encode(audio_bytes).decode("ascii")
    msg = create_chat_message(
        message="Queued spoken reply",
        session_id="session-voice-note",
        user_id="server",
        context=[
            {
                "source": "voice_reply",
                "attachments": [
                    {
                        "filename": "voice-reply.ogg",
                        "mimetype": "audio/ogg; codecs=opus",
                        "data": audio_data,
                        "size_bytes": len(audio_bytes),
                        "meta": {"kind": "voice", "role": "voice_reply"},
                    }
                ],
            }
        ],
        metadata={
            "source": "ai_agent",
            "voice_note": {
                "mode": "recorded_voice_note",
                "reply_audio_attached": True,
                "reply_transcript": "Queued spoken reply",
            },
        },
    )

    result = await webrtc._deliver_voice_chat_message("session-voice-note", msg, label="voice reply")
    assert result == "buffered"
    queued = webrtc._offline_pending_messages["session-voice-note"][0]["message"]
    assert queued.payload["context"][0]["attachments"][0]["data"] == audio_data

    dc = _SendCapture()
    webrtc.datachannel_managers["session-voice-note"] = dc
    await webrtc._flush_pending_voice_chat_messages("session-voice-note")

    assert len(dc.sent) == 1
    delivered = dc.sent[0]
    attachment = delivered.payload["context"][0]["attachments"][0]
    assert attachment["mimetype"] == "audio/ogg; codecs=opus"
    assert base64.b64decode(attachment["data"]) == audio_bytes
    voice_note = delivered.payload["metadata"]["voice_note"]
    assert voice_note["reply_audio_attached"] is True
    assert not any(str(key).endswith("_path") for key in voice_note)
    assert "session-voice-note" not in webrtc._offline_pending_messages


@pytest.mark.asyncio
async def test_media_reply_attachment_survives_offline_queue_reconnect():
    from shared.datachannel_manager import create_chat_message

    webrtc = server.WebRTCManager()
    image_bytes = b"\x89PNG\r\n\x1a\nqueued media reply"
    image_data = base64.b64encode(image_bytes).decode("ascii")
    msg = create_chat_message(
        message="",
        session_id="session-media-reply",
        user_id="server",
        context=[
            {
                "source": "media_reply",
                "attachments": [
                    {
                        "filename": "synthetic-image.png",
                        "mimetype": "image/png",
                        "data": image_data,
                        "size_bytes": len(image_bytes),
                        "meta": {"kind": "image", "role": "media_reply"},
                    }
                ],
            }
        ],
        metadata={
            "source": "media_reply",
            "media_reply": {
                "count": 1,
                "items": [
                    {
                        "filename": "synthetic-image.png",
                        "mimetype": "image/png",
                        "size_bytes": len(image_bytes),
                        "kind": "image",
                    }
                ],
            },
        },
    )

    result = await webrtc._deliver_voice_chat_message("session-media-reply", msg, label="media reply")
    assert result == "buffered"
    queued = webrtc._offline_pending_messages["session-media-reply"][0]["message"]
    assert queued.payload["context"][0]["attachments"][0]["data"] == image_data

    dc = _SendCapture()
    webrtc.datachannel_managers["session-media-reply"] = dc
    await webrtc._flush_pending_voice_chat_messages("session-media-reply")

    assert len(dc.sent) == 1
    delivered = dc.sent[0]
    attachment = delivered.payload["context"][0]["attachments"][0]
    assert attachment["mimetype"] == "image/png"
    assert base64.b64decode(attachment["data"]) == image_bytes
    assert delivered.payload["metadata"]["media_reply"]["items"][0]["kind"] == "image"
    assert "session-media-reply" not in webrtc._offline_pending_messages


@pytest.mark.asyncio
async def test_reconnect_survivable_chat_task_replays_final_reply_after_disconnect():
    webrtc = server.WebRTCManager()
    gate = asyncio.Event()
    msg = _make_message("Recovered final reply")

    async def in_flight_chat_turn():
        await gate.wait()
        webrtc._enqueue_to_offline_queue("stable-session", msg, label="chat reply")

    task = webrtc._track_session_task(
        "stable-session",
        in_flight_chat_turn(),
        "chat",
        survive_disconnect=True,
    )

    # Disconnect cleanup should cancel transport-bound tasks only, not the
    # in-flight chat turn that is waiting on the AI session executor.
    await webrtc._cancel_session_message_tasks("stable-session")
    assert not task.done()
    assert "stable-session" not in webrtc.session_message_tasks
    assert task in webrtc.session_reconnect_survivable_tasks.get("stable-session", set())

    gate.set()
    await task

    dc = _SendCapture()
    webrtc.datachannel_managers["stable-session"] = dc
    await webrtc._flush_pending_voice_chat_messages("stable-session")

    assert len(dc.sent) == 1
    assert dc.sent[0].text == "Recovered final reply"
    assert "stable-session" not in webrtc._offline_pending_messages


@pytest.mark.asyncio
async def test_offline_queue_not_cleared_by_session_cleanup():
    """async_cleanup_session must NOT remove offline queue entries."""
    webrtc = server.WebRTCManager()
    webrtc._enqueue_to_offline_queue("relay-id", _make_message("buffered reply"))

    # Simulate cleanup without a real peer connection
    await webrtc.async_cleanup_session("relay-id")

    assert "relay-id" in webrtc._offline_pending_messages
    assert len(webrtc._offline_pending_messages["relay-id"]) == 1


# ---------------------------------------------------------------------------
# Shutdown clears offline queue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shutdown_clears_offline_queue():
    webrtc = server.WebRTCManager()
    webrtc._enqueue_to_offline_queue("s1", _make_message("msg"))
    webrtc._enqueue_to_offline_queue("s2", _make_message("msg"))

    original_audio_managers = dict(server.STATE.audio_managers)
    try:
        await webrtc.shutdown()
        assert not webrtc._offline_pending_messages
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


# ---------------------------------------------------------------------------
# Text chat offline queue - send_chat_to_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_chat_to_session_enqueues_offline_when_no_datachannel():
    """send_chat_to_session must enqueue the message when no datachannel is present."""
    webrtc = server.WebRTCManager()
    # from __debug_provenance_y__ import legal
    # No datachannel_manager registered for "user-abc"
    result = await webrtc.send_chat_to_session("user-abc", "Hello from server")
    assert result is False  # not delivered live
    queue = webrtc._offline_pending_messages.get("user-abc", [])
    assert len(queue) == 1


@pytest.mark.asyncio
async def test_send_chat_to_session_enqueues_offline_when_send_fails():
    """send_chat_to_session must enqueue when the datachannel send returns False."""
    webrtc = server.WebRTCManager()
    dc = _SendCapture(succeed=False)
    webrtc.datachannel_managers["user-abc"] = dc

    result = await webrtc.send_chat_to_session("user-abc", "Hello from server")
    assert result is False
    queue = webrtc._offline_pending_messages.get("user-abc", [])
    assert len(queue) == 1


@pytest.mark.asyncio
async def test_send_chat_to_session_does_not_enqueue_on_success():
    """send_chat_to_session must NOT add to offline queue when delivery succeeds."""
    webrtc = server.WebRTCManager()
    dc = _SendCapture(succeed=True)
    webrtc.datachannel_managers["user-abc"] = dc

    result = await webrtc.send_chat_to_session("user-abc", "Hello from server")
    assert result is True
    assert "user-abc" not in webrtc._offline_pending_messages


@pytest.mark.asyncio
async def test_send_chat_to_session_uses_stable_session_id_for_queue():
    """When _voice_dc_session_id maps raw→stable, the offline queue key must be stable."""
    webrtc = server.WebRTCManager()
    # Simulate that raw WebRTC ID "raw-xyz" was re-keyed to stable "stable-uuid"
    webrtc._voice_dc_session_id["raw-xyz"] = "stable-uuid"
    # No datachannel registered for "raw-xyz"
    await webrtc.send_chat_to_session("raw-xyz", "Missed reply")
    # Must be queued under the stable ID, not the raw one
    assert "stable-uuid" in webrtc._offline_pending_messages
    assert "raw-xyz" not in webrtc._offline_pending_messages


@pytest.mark.asyncio
async def test_send_chat_to_session_offline_message_delivered_on_reconnect():
    """Full round-trip: text chat enqueued offline → delivered when client reconnects."""
    webrtc = server.WebRTCManager()

    # Phase 1: client is offline
    result = await webrtc.send_chat_to_session("cli-123", "You have new mail")
    assert result is False
    assert len(webrtc._offline_pending_messages.get("cli-123", [])) == 1

    # Phase 2: client reconnects - datachannel becomes available
    dc = _SendCapture()
    webrtc.datachannel_managers["cli-123"] = dc
    await webrtc._flush_pending_voice_chat_messages("cli-123")

    assert len(dc.sent) == 1
    assert "cli-123" not in webrtc._offline_pending_messages


# ---------------------------------------------------------------------------
# Text chat - which conversation send_chat_to_session addresses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_chat_to_session_addresses_the_conversation_the_device_is_in_now(tmp_path, monkeypatch):
    """With no conversation named, a message goes where an AI reply would."""
    webrtc, channel, paired, manager = connect_device(tmp_path, monkeypatch)
    first_conversation = server._build_conversation_metadata(webrtc._resolve_chat_identity("synthetic-live"))
    # The device has since started a new conversation.
    manager.advance_conversation_thread(paired.owner_key)
    as_an_ai_reply = server._build_conversation_metadata(
        server._resolve_conversation_identity(webrtc._resolve_chat_identity("synthetic-live"))
    )

    assert await webrtc.send_chat_to_session("synthetic-live", "An alert") is True
    # The admin API and the agents reach the same default through a reply target.
    assert await webrtc.send_chat_to_reply_target(
        {"transport": "webrtc", "owner_key": paired.owner_key}, "From an agent"
    ) is True

    assert [message.payload["message"] for message in channel.sent] == ["An alert", "From an agent"]
    for message in channel.sent:
        metadata = message.payload["metadata"]
        assert metadata["conversation_session_id"] == as_an_ai_reply["conversation_session_id"]
        assert metadata["conversation_session_id"].endswith("::thread::2")
        assert metadata["conversation_thread_id"] == 2
        # A client ignores a message addressed to a conversation it has left,
        # and the transport identity alone names the device's first one.
        assert metadata["conversation_session_id"] != first_conversation["conversation_session_id"]
        # Addressed, not pinned: the client files it with the conversation it has open.
        assert "conversation_force_target" not in metadata


@pytest.mark.asyncio
async def test_send_chat_to_session_keeps_a_conversation_the_caller_named(tmp_path, monkeypatch):
    """A result that belongs to an earlier conversation still goes back to it."""
    webrtc, channel, paired, manager = connect_device(tmp_path, monkeypatch)
    first_conversation = server._build_conversation_metadata(
        webrtc._resolve_chat_identity("synthetic-live")
    )["conversation_session_id"]
    manager.advance_conversation_thread(paired.owner_key)

    # Named by the id the client knows it by, and pinned, as the scheduler does.
    await webrtc.send_chat_to_session(
        "synthetic-live",
        "A scheduled result",
        metadata={"conversation_session_id": first_conversation, "conversation_force_target": True},
    )
    # Named by the id history keeps it under.
    await webrtc.send_chat_to_session(
        "synthetic-live",
        "Another result",
        metadata={"conversation_session_id": paired.canonical_session_id},
    )

    pinned, by_history_id = (message.payload["metadata"] for message in channel.sent)
    assert pinned["conversation_session_id"] == first_conversation
    assert pinned["conversation_force_target"] is True
    assert by_history_id["conversation_session_id"] == first_conversation
    assert by_history_id["conversation_thread_id"] == 1


@pytest.mark.asyncio
async def test_send_chat_to_session_names_the_first_conversation_when_no_thread_is_known(monkeypatch):
    """Without a conversation store the device's first conversation is the only one."""
    from shared import session_execution

    monkeypatch.setattr(
        session_execution,
        "_GLOBAL_SESSION_EXECUTION_MANAGER",
        session_execution.SessionExecutionManager(),
    )
    monkeypatch.setattr(server, "_get_conversation_session_manager", lambda: None)
    webrtc = server.WebRTCManager()
    server.bind_transport_chat_owner("local", "synthetic-device", raw_session_id="synthetic-live")
    channel = OpenChannel()
    webrtc.datachannel_managers["synthetic-live"] = channel

    assert await webrtc.send_chat_to_session("synthetic-live", "An alert") is True

    metadata = channel.sent[0].payload["metadata"]
    expected = server._build_conversation_metadata(webrtc._resolve_chat_identity("synthetic-live"))
    assert metadata["conversation_session_id"] == expected["conversation_session_id"]
    assert metadata["conversation_thread_id"] == 1


# ---------------------------------------------------------------------------
# _enqueue_to_offline_queue - label parameter
# ---------------------------------------------------------------------------

def test_enqueue_to_offline_queue_accepts_custom_label():
    """_enqueue_to_offline_queue must succeed for any label string (chat reply, chat message, etc.)."""
    webrtc = server.WebRTCManager()
    for label in ("voice reply", "chat reply", "chat message", "custom"):
        result = webrtc._enqueue_to_offline_queue(f"sid-{label}", _make_message("x"), label=label)
        assert result == "buffered", f"Expected 'buffered' for label={label!r}"


# ---------------------------------------------------------------------------
# Offline replies nudge - content-free cloud push when a Cloud Pair client
# misses replies while unreachable
# ---------------------------------------------------------------------------

def _cloud_identity(session_id: str = "relay-1"):
    from types import SimpleNamespace
    return SimpleNamespace(
        owner_key="cloud:client-42",
        canonical_user_id="user::cloud:client-42",
        raw_session_id=session_id,
    )


def _local_identity(session_id: str = "local-1"):
    from types import SimpleNamespace
    return SimpleNamespace(
        owner_key="local:device-9",
        canonical_user_id="user::local:device-9",
        raw_session_id=session_id,
    )


class _NudgeHarness:
    """Patches STATE cloud config + _notify_cloud_client around a WebRTCManager."""

    def __init__(self, monkeypatch, *, identity, notify_result=None):
        self.calls: list = []
        self.webrtc = server.WebRTCManager()
        monkeypatch.setattr(self.webrtc, "_resolve_chat_identity", lambda sid: identity)
        monkeypatch.setattr(
            server.STATE, "config", {"cloud": {"server_token": "tok"}}, raising=False
        )
        monkeypatch.setattr(server, "get_configured_server_name", lambda: "TestServer")

        result = notify_result if notify_result is not None else {"sent": True, "success": True}

        async def fake_notify(**kwargs):
            self.calls.append(kwargs)
            return result

        monkeypatch.setattr(server, "_notify_cloud_client", fake_notify)


@pytest.mark.asyncio
async def test_offline_nudge_sent_for_cloud_owned_session(monkeypatch):
    harness = _NudgeHarness(monkeypatch, identity=_cloud_identity())
    await harness.webrtc._maybe_send_offline_replies_nudge("relay-1")

    assert len(harness.calls) == 1
    call = harness.calls[0]
    assert call["category"] == "offline_replies"
    # Content-free contract: no reply text in the push, only a generic notice.
    assert "TestServer" in call["body"]
    assert call["data"]["delivery"] == "offline_replies_nudge"


@pytest.mark.asyncio
async def test_offline_nudge_skipped_for_local_pair_session(monkeypatch):
    harness = _NudgeHarness(monkeypatch, identity=_local_identity())
    await harness.webrtc._maybe_send_offline_replies_nudge("local-1")
    assert harness.calls == []


@pytest.mark.asyncio
async def test_offline_nudge_skipped_when_not_cloud_linked(monkeypatch):
    harness = _NudgeHarness(monkeypatch, identity=_cloud_identity())
    monkeypatch.setattr(server.STATE, "config", {"cloud": {"server_token": ""}}, raising=False)
    await harness.webrtc._maybe_send_offline_replies_nudge("relay-1")
    assert harness.calls == []


@pytest.mark.asyncio
async def test_offline_nudge_rate_limited_per_session(monkeypatch):
    harness = _NudgeHarness(monkeypatch, identity=_cloud_identity())
    await harness.webrtc._maybe_send_offline_replies_nudge("relay-1")
    await harness.webrtc._maybe_send_offline_replies_nudge("relay-1")
    assert len(harness.calls) == 1


@pytest.mark.asyncio
async def test_offline_nudge_cooldown_cleared_when_delivery_fails(monkeypatch):
    harness = _NudgeHarness(
        monkeypatch,
        identity=_cloud_identity(),
        notify_result={"sent": False, "success": False, "error": "409"},
    )
    await harness.webrtc._maybe_send_offline_replies_nudge("relay-1")
    # The failed attempt must not consume the cooldown window.
    await harness.webrtc._maybe_send_offline_replies_nudge("relay-1")
    assert len(harness.calls) == 2


@pytest.mark.asyncio
async def test_offline_nudge_cooldown_reset_after_queue_flush(monkeypatch):
    harness = _NudgeHarness(monkeypatch, identity=_cloud_identity())
    webrtc = harness.webrtc
    dc = _SendCapture()
    webrtc.datachannel_managers["relay-1"] = dc

    await webrtc._maybe_send_offline_replies_nudge("relay-1")
    assert len(harness.calls) == 1

    # The client reconnects and drains the queue -> cooldown clears.
    webrtc._offline_pending_messages["relay-1"] = [
        {"enqueued_at": time.time(), "message": _make_message("r1")}
    ]
    await webrtc._flush_offline_queue("relay-1")
    assert "relay-1" not in webrtc._offline_nudge_last_sent_at

    await webrtc._maybe_send_offline_replies_nudge("relay-1")
    assert len(harness.calls) == 2


@pytest.mark.asyncio
async def test_offline_nudge_disabled_by_env(monkeypatch):
    harness = _NudgeHarness(monkeypatch, identity=_cloud_identity())
    monkeypatch.setenv("AUTOYOU_OFFLINE_REPLIES_NUDGE", "0")
    await harness.webrtc._maybe_send_offline_replies_nudge("relay-1")
    assert harness.calls == []


@pytest.mark.asyncio
async def test_enqueue_schedules_offline_nudge(monkeypatch):
    """_enqueue_to_offline_queue must kick off the nudge task without blocking."""
    webrtc = server.WebRTCManager()
    nudged: list = []

    async def fake_nudge(session_id):
        nudged.append(session_id)

    monkeypatch.setattr(webrtc, "_maybe_send_offline_replies_nudge", fake_nudge)
    webrtc._enqueue_to_offline_queue("relay-9", _make_message("r"), label="chat reply")
    await asyncio.sleep(0)
    assert nudged == ["relay-9"]


@pytest.mark.asyncio
async def test_send_chat_to_reply_target_respects_queue_if_undelivered_false(monkeypatch, tmp_path):
    webrtc, channel, paired, _ = connect_device(tmp_path, monkeypatch)
    webrtc.datachannel_managers["synthetic-live"] = _SendCapture(succeed=False)
    reply_target = {"transport": "webrtc", "session_id": "synthetic-live", "owner_key": paired.owner_key}
    sent = await webrtc.send_chat_to_reply_target(reply_target, "Hello", queue_if_undelivered=False)
    assert sent is False
    assert not webrtc._offline_pending_messages.get("synthetic-live")
