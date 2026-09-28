# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-4793a20fe6f1745abfe7eeba


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-4793a20fe6f1745abfe7eeba"

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import rest_api
import server
import signal_service
from signal_service import SignalService
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType


def test_plain_webrtc_pairing_mode_resolves_as_local_pair():
    assert server._resolve_pairing_mode_for_transport("webrtc") == "local_pair"
    assert server._resolve_pairing_mode_for_transport("datachannel") == "local_pair"


class _DummyExecutionManager:
    def __init__(self, identity):
        self.identity = identity

    def bind_transport_owner(self, transport: str, sender_id: str, *, raw_session_id=None):
        return self.identity

    def resolve_webrtc_identity(self, session_id: str):
        return self.identity

    async def submit_turn(self, identity, handler, *, on_status=None, timeout_seconds=None, label="chat"):
        if on_status:
            await on_status(SimpleNamespace(status="queued", queue_position=2))
        return SimpleNamespace(
            response="final reply",
            agent_name="AutoYou AI Agent",
            session_id=getattr(identity, "canonical_session_id", ""),
            message_id="msg-1",
            metadata={"processing_time_ms": 0},
        )


class _ExecutingDummyExecutionManager(_DummyExecutionManager):
    async def submit_turn(self, identity, handler, *, on_status=None, timeout_seconds=None, label="chat"):
        if on_status:
            await on_status(SimpleNamespace(status="queued", queue_position=1))
        return await handler()


class _SessionAwareExecutingExecutionManager:
    def __init__(self):
        self.resolved_session_ids = []

    def bind_transport_owner(self, transport: str, sender_id: str, *, raw_session_id=None):
        session_id = str(raw_session_id or sender_id)
        return self.resolve_webrtc_identity(session_id)

    def resolve_webrtc_identity(self, session_id: str):
        normalized_session_id = str(session_id)
        self.resolved_session_ids.append(normalized_session_id)
        return SimpleNamespace(
            canonical_session_id=f"session::guest:{normalized_session_id}",
            canonical_user_id=f"user::guest:{normalized_session_id}",
            owner_key=f"guest:{normalized_session_id}",
        )

    async def submit_turn(self, identity, handler, *, on_status=None, timeout_seconds=None, label="chat"):
        if on_status:
            await on_status(SimpleNamespace(status="queued", queue_position=1))
        return await handler()


@pytest.mark.asyncio
async def test_signal_does_not_send_queue_position_feedback(monkeypatch):
    service = SignalService()
    sent_messages = []
    expected_agent_label = server.get_configured_server_name()
    identity = SimpleNamespace(
        canonical_session_id="session::signal:+11234567890",
        canonical_user_id="user::signal:+11234567890",
        owner_key="signal:+11234567890",
    )

    async def fake_send_signal_message(phone_number: str, message: str):
        sent_messages.append((phone_number, message))
        return True

    monkeypatch.setattr(signal_service, "get_session_execution_manager", lambda: _DummyExecutionManager(identity))
    service._send_signal_message = fake_send_signal_message  # type: ignore[assignment]

    await service._forward_to_chat_api("+11234567890", "hello", 1234567890)
    await asyncio.gather(*list(service.chat_tasks))

    assert sent_messages == [
        ("+11234567890", f"Please wait while I am thinking...\n\n~ {expected_agent_label}"),
        ("+11234567890", f"final reply\n\n~ {expected_agent_label}"),
    ]


@pytest.mark.asyncio
async def test_signal_ignores_sync_messages_sent_to_non_self_destination(monkeypatch):
    service = SignalService()
    calls = []

    async def fake_log_message(*args, **kwargs):
        calls.append(("log", args, kwargs))

    async def fake_forward_to_chat_api(*args, **kwargs):
        calls.append(("forward", args, kwargs))

    async def fake_send_signal_message(*args, **kwargs):
        calls.append(("send", args, kwargs))
        return True

    service._log_message = fake_log_message  # type: ignore[assignment]
    service._forward_to_chat_api = fake_forward_to_chat_api  # type: ignore[assignment]
    service._send_signal_message = fake_send_signal_message  # type: ignore[assignment]

    await service._process_single_message(
        "+11234567890",
        {
            "envelope": {
                "timestamp": 1234567890,
                "sourceNumber": "+11234567890",
                "syncMessage": {
                    "sentMessage": {
                        "destinationNumber": "+19876543210",
                        "message": "not an AutoYou owner chat",
                    }
                },
            }
        },
    )

    assert calls == []


@pytest.mark.asyncio
async def test_telegram_does_not_send_queue_position_feedback(monkeypatch):
    sent_messages = []
    expected_agent_label = server.get_configured_server_name()
    identity = SimpleNamespace(
        canonical_session_id="session::telegram:9876543210",
        canonical_user_id="user::telegram:9876543210",
        owner_key="telegram:9876543210",
    )
    before_tasks = set(server.background_tasks)

    async def fake_send_telegram_text_to_session(session_id: str, text: str, *, split_text: bool = True, reply_to_message_id=None):
        sent_messages.append((session_id, text, split_text, reply_to_message_id))

    monkeypatch.setattr(server, "get_session_execution_manager", lambda: _DummyExecutionManager(identity))
    monkeypatch.setattr(server, "_send_telegram_text_to_session", fake_send_telegram_text_to_session)

    server._schedule_telegram_chat_delivery(message="hello", identity=identity, metadata={"client": "telegram"})
    new_tasks = [task for task in server.background_tasks if task not in before_tasks]
    await asyncio.gather(*new_tasks)

    assert sent_messages == [
        (
            "session::telegram:9876543210",
            f"final reply\n\n~ {expected_agent_label}",
            True,
            None,
        )
    ]


@pytest.mark.asyncio
async def test_telegram_chat_delivery_sends_typing_indicator(monkeypatch):
    sent_messages = []
    sent_actions = []
    expected_agent_label = server.get_configured_server_name()
    identity = SimpleNamespace(
        canonical_session_id="session::telegram:9876543210",
        canonical_user_id="user::telegram:9876543210",
        owner_key="telegram:9876543210",
    )
    before_tasks = set(server.background_tasks)

    async def fake_send_telegram_text_to_session(session_id: str, text: str, *, split_text: bool = True, reply_to_message_id=None):
        sent_messages.append((session_id, text, split_text, reply_to_message_id))

    async def fake_send_chat_action_to_session(session_id: str, action: str | None = None):
        sent_actions.append((session_id, action))

    monkeypatch.setattr(server, "get_session_execution_manager", lambda: _DummyExecutionManager(identity))
    monkeypatch.setattr(server, "_send_telegram_text_to_session", fake_send_telegram_text_to_session)
    monkeypatch.setattr(server, "_send_telegram_chat_action_to_session", fake_send_chat_action_to_session)

    server._schedule_telegram_chat_delivery(message="hello", identity=identity, metadata={"client": "telegram"})
    new_tasks = [task for task in server.background_tasks if task not in before_tasks]
    await asyncio.gather(*new_tasks)

    assert sent_actions
    assert sent_actions[0] == (
        "session::telegram:9876543210",
        server._telegram_typing_action(),
    )
    assert sent_messages == [
        (
            "session::telegram:9876543210",
            f"final reply\n\n~ {expected_agent_label}",
            True,
            None,
        )
    ]


@pytest.mark.asyncio
async def test_telegram_chat_request_includes_reply_target_metadata(monkeypatch):
    captured = {}
    sent_messages = []
    identity = SimpleNamespace(
        canonical_session_id="session::telegram:9876543210",
        canonical_user_id="user::telegram:9876543210",
        owner_key="telegram:9876543210",
    )
    before_tasks = set(server.background_tasks)

    async def fake_process_chat_message(chat_request, ai_agent_url=None, on_chunk=None, on_media_reply=None):
        captured["metadata"] = dict(chat_request.metadata or {})
        return SimpleNamespace(
            response="final reply",
            agent_name="AutoYou AI Agent",
            session_id=identity.canonical_session_id,
            message_id="msg-1",
            metadata={"processing_time_ms": 0},
        )

    async def fake_send_telegram_text_to_session(session_id: str, text: str, *, split_text: bool = True, reply_to_message_id=None):
        sent_messages.append((session_id, text, split_text, reply_to_message_id))

    async def fake_send_chat_action_to_session(session_id: str, action: str | None = None):
        return None

    monkeypatch.setattr(server, "get_session_execution_manager", lambda: _ExecutingDummyExecutionManager(identity))
    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)
    monkeypatch.setattr(server, "_send_telegram_text_to_session", fake_send_telegram_text_to_session)
    monkeypatch.setattr(server, "_send_telegram_chat_action_to_session", fake_send_chat_action_to_session)

    server._schedule_telegram_chat_delivery(
        message="hello",
        identity=identity,
        chat_id=9876543210,
        metadata={"client": "telegram"},
        reply_to_message_id=55,
    )
    new_tasks = [task for task in server.background_tasks if task not in before_tasks]
    await asyncio.gather(*new_tasks)

    assert captured["metadata"]["reply_target"] == {
        "transport": "telegram",
        "chat_id": 9876543210,
        "reply_to_message_id": 55,
    }
    assert sent_messages[0][0] == "session::telegram:9876543210"


@pytest.mark.asyncio
async def test_webrtc_text_chat_does_not_send_queue_position_feedback(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    identity = SimpleNamespace(
        canonical_session_id="session::webrtc:test-session",
        canonical_user_id="user::webrtc:test-session",
        owner_key="webrtc:test-session",
    )

    class DummyDataChannelManager:
        async def send_message(self, message):
            sent_messages.append(message)
            return True

    webrtc.datachannel_managers["test-session"] = DummyDataChannelManager()
    monkeypatch.setattr(server, "get_session_execution_manager", lambda: _DummyExecutionManager(identity))

    message = DataChannelMessage(
        header=MessageHeader(
            message_id="msg-1",
            message_type=MessageType.CHAT,
            timestamp=0.0,
            session_id="test-session",
            user_id="client",
        ),
        payload={"message": "hello", "context": [], "metadata": {}},
    )

    await webrtc._handle_chat_message(message)

    assert len(sent_messages) == 1
    assert sent_messages[0].payload["message"] == "final reply"


@pytest.mark.asyncio
async def test_webrtc_text_chat_request_includes_reply_target_metadata(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    captured = {}
    identity = SimpleNamespace(
        canonical_session_id="session::cloud:client-device-abc",
        canonical_user_id="user::cloud:client-device-abc",
        owner_key="cloud:client-device-abc",
        raw_session_id="relay-123",
    )

    class DummyDataChannelManager:
        async def send_message(self, message):
            sent_messages.append(message)
            return True

    async def fake_process_chat_message(chat_request, ai_agent_url=None, on_chunk=None, on_media_reply=None):
        captured["metadata"] = dict(chat_request.metadata or {})
        return SimpleNamespace(response="final reply", metadata={})

    webrtc.datachannel_managers["relay-123"] = DummyDataChannelManager()
    monkeypatch.setattr(webrtc, "_resolve_chat_identity", lambda session_id: identity)
    monkeypatch.setattr(server, "get_session_execution_manager", lambda: _ExecutingDummyExecutionManager(identity))
    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)

    message = DataChannelMessage(
        header=MessageHeader(
            message_id="msg-2",
            message_type=MessageType.CHAT,
            timestamp=0.0,
            session_id="relay-123",
            user_id="client",
        ),
        payload={"message": "hello", "context": [], "metadata": {}},
    )

    await webrtc._handle_chat_message(message)

    assert len(sent_messages) == 1
    assert captured["metadata"]["reply_target"] == {
        "transport": "webrtc",
        "owner_key": "cloud:client-device-abc",
        "session_id": "relay-123",
    }


@pytest.mark.asyncio
async def test_plain_webrtc_text_chat_request_carries_local_pair_metadata(monkeypatch):
    webrtc = server.WebRTCManager()
    captured = {}
    identity = SimpleNamespace(
        transport="webrtc",
        sender_id="device-synthetic",
        raw_session_id="device-synthetic",
        canonical_session_id="session::webrtc:device-synthetic",
        canonical_user_id="user::webrtc:device-synthetic",
        owner_key="webrtc:device-synthetic",
        thread_id=1,
    )

    class DummyDataChannelManager:
        async def send_message(self, message):
            return True

    async def fake_process_chat_message(chat_request, ai_agent_url=None, on_chunk=None, on_media_reply=None):
        captured["metadata"] = dict(chat_request.metadata or {})
        return SimpleNamespace(response="final reply", metadata={})

    webrtc.datachannel_managers["device-synthetic"] = DummyDataChannelManager()
    monkeypatch.setattr(webrtc, "_resolve_chat_identity", lambda session_id: identity)
    monkeypatch.setattr(server, "get_session_execution_manager", lambda: _ExecutingDummyExecutionManager(identity))
    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)

    message = DataChannelMessage(
        header=MessageHeader(
            message_id="msg-local-pair",
            message_type=MessageType.CHAT,
            timestamp=0.0,
            session_id="device-synthetic",
            user_id="client",
        ),
        payload={"message": "hello", "context": [], "metadata": {}},
    )

    await webrtc._handle_chat_message(message)

    assert captured["metadata"]["pairing_mode"] == "local_pair"
    assert captured["metadata"]["canonical_owner_key"] == "webrtc:device-synthetic"


@pytest.mark.asyncio
async def test_webrtc_chat_ignores_client_claim_of_a_saved_conversation_thread(monkeypatch):
    """The authenticated channel, not client metadata, chooses the live thread."""
    webrtc = server.WebRTCManager()
    captured = {}
    base_identity = SimpleNamespace(
        transport="local",
        sender_id="device-synthetic",
        raw_session_id="device-synthetic",
        canonical_session_id="session::local:device-synthetic",
        canonical_user_id="user::local:device-synthetic",
        owner_key="local:device-synthetic",
        thread_id=None,
    )
    live_identity = SimpleNamespace(
        transport="local",
        sender_id="device-synthetic",
        raw_session_id="device-synthetic",
        canonical_session_id="session::local:device-synthetic::2",
        canonical_user_id="user::local:device-synthetic",
        owner_key="local:device-synthetic",
        thread_id=2,
    )

    class DummyDataChannelManager:
        async def send_message(self, message):
            return True

    async def fake_process_chat_message(chat_request, ai_agent_url=None, on_chunk=None, on_media_reply=None):
        del ai_agent_url, on_chunk, on_media_reply
        captured["request"] = chat_request
        return SimpleNamespace(response="final reply", metadata={})

    webrtc.datachannel_managers["device-synthetic"] = DummyDataChannelManager()
    monkeypatch.setattr(webrtc, "_resolve_chat_identity", lambda _session_id: base_identity)
    monkeypatch.setattr(
        server,
        "_resolve_conversation_identity",
        lambda _identity, start_new_thread=False: live_identity,
    )
    monkeypatch.setattr(
        server,
        "get_session_execution_manager",
        lambda: _ExecutingDummyExecutionManager(live_identity),
    )
    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)

    await webrtc._handle_chat_message(
        DataChannelMessage(
            header=MessageHeader(
                message_id="msg-saved-conversation-claim",
                message_type=MessageType.CHAT,
                timestamp=0.0,
                session_id="device-synthetic",
                user_id="client",
            ),
            payload={
                "message": "send only to the live conversation",
                "context": [],
                "metadata": {
                    "conversation_session_id": "session::local:device-synthetic",
                },
            },
        )
    )

    assert captured["request"].session_id == live_identity.canonical_session_id
    assert captured["request"].metadata["conversation_session_id"] == (
        server._build_client_conversation_session_id(live_identity)
    )
    assert captured["request"].metadata["conversation_thread_id"] == 2


@pytest.mark.asyncio
async def test_webrtc_reply_target_resolves_live_session_by_owner_key(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    identity = SimpleNamespace(
        transport="cloud",
        sender_id="client-device-abc",
        raw_session_id="live-session",
        canonical_session_id="session::cloud:client-device-abc",
        canonical_user_id="user::cloud:client-device-abc",
        owner_key="cloud:client-device-abc",
        thread_id=None,
    )

    class DummyDataChannelManager:
        async def send_message(self, message):
            sent_messages.append(message)
            return True

    webrtc.datachannel_managers["live-session"] = DummyDataChannelManager()
    monkeypatch.setattr(
        webrtc,
        "_resolve_chat_identity",
        lambda session_id: identity if session_id == "live-session" else SimpleNamespace(owner_key="guest:other"),
    )

    ok = await webrtc.send_chat_to_reply_target(
        {
            "transport": "webrtc",
            "owner_key": "cloud:client-device-abc",
            "session_id": "stale-relay-id",
        },
        "hello again",
        metadata={"source": "test"},
    )

    assert ok is True
    assert len(sent_messages) == 1
    assert sent_messages[0].header.session_id == "live-session"
    assert sent_messages[0].payload["message"] == "hello again"
    assert sent_messages[0].payload["metadata"]["source"] == "test"
    assert sent_messages[0].payload["metadata"]["canonical_owner_key"] == identity.owner_key
    assert sent_messages[0].payload["metadata"]["canonical_user_id"] == identity.canonical_user_id
    assert sent_messages[0].payload["metadata"]["conversation_session_id"] == server._build_client_conversation_session_id(identity)
    assert sent_messages[0].payload["metadata"]["reply_target"] == {
        "transport": "webrtc",
        "owner_key": identity.owner_key,
        "session_id": "live-session",
    }


@pytest.mark.asyncio
async def test_webrtc_reply_target_prefers_stable_client_id_for_alias(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    identity = SimpleNamespace(
        transport="cloud",
        sender_id="client-device-abc",
        raw_session_id="client-device-abc",
        canonical_session_id="session::cloud:client-device-abc",
        canonical_user_id="user::cloud:client-device-abc",
        owner_key="cloud:client-device-abc",
        thread_id=None,
    )

    class DummyDataChannelManager:
        session_id = "client-device-abc"

        async def send_message(self, message):
            sent_messages.append(message)
            return True

    manager = DummyDataChannelManager()
    webrtc.datachannel_managers["relay-123"] = manager
    webrtc.datachannel_managers["client-device-abc"] = manager
    webrtc._voice_dc_session_id["relay-123"] = "client-device-abc"
    monkeypatch.setattr(webrtc, "_resolve_chat_identity", lambda session_id: identity)

    ok = await webrtc.send_chat_to_reply_target(
        {
            "transport": "webrtc",
            "owner_key": "cloud:client-device-abc",
            "session_id": "relay-123",
        },
        "hello again",
        metadata={"source": "test"},
    )

    assert ok is True
    assert len(sent_messages) == 1
    assert sent_messages[0].header.session_id == "client-device-abc"
    assert sent_messages[0].payload["metadata"]["reply_target"] == {
        "transport": "webrtc",
        "owner_key": identity.owner_key,
        "session_id": "client-device-abc",
    }


@pytest.mark.asyncio
async def test_webrtc_reply_target_repairs_canonical_conversation_metadata(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    identity = SimpleNamespace(
        transport="cloud",
        sender_id="client-device-abc",
        raw_session_id="relay-123",
        canonical_session_id="session::cloud:client-device-abc",
        canonical_user_id="user::cloud:client-device-abc",
        owner_key="cloud:client-device-abc",
        thread_id=None,
    )

    class DummyDataChannelManager:
        async def send_message(self, message):
            sent_messages.append(message)
            return True

    webrtc.datachannel_managers["relay-123"] = DummyDataChannelManager()
    monkeypatch.setattr(webrtc, "_resolve_chat_identity", lambda session_id: identity)

    ok = await webrtc.send_chat_to_reply_target(
        {
            "transport": "webrtc",
            "owner_key": "cloud:client-device-abc",
            "session_id": "relay-123",
        },
        "hello again",
        metadata={
            "source": "cli_agent",
            "conversation_session_id": identity.canonical_session_id,
            "original_message_id": "cli-stream-1",
            "is_streaming": True,
        },
    )

    assert ok is True
    assert len(sent_messages) == 1
    payload_metadata = sent_messages[0].payload["metadata"]
    assert payload_metadata["conversation_session_id"] == server._build_client_conversation_session_id(identity)
    assert payload_metadata["canonical_owner_key"] == identity.owner_key
    assert payload_metadata["canonical_user_id"] == identity.canonical_user_id
    assert payload_metadata["reply_target"] == {
        "transport": "webrtc",
        "owner_key": identity.owner_key,
        "session_id": "relay-123",
    }


@pytest.mark.asyncio
async def test_webrtc_playback_reply_target_resolves_audio_manager_by_owner_key(monkeypatch):
    webrtc = server.WebRTCManager()

    class DummyDataChannelManager:
        async def send_message(self, message):
            return True

    class DummyAudioManager:
        def __init__(self):
            self.calls = []

        def play_audio_file(self, file_path, *, source="audio_file"):
            self.calls.append((file_path, source))
            return {"event": "playback", "state": "playing", "source": source, "detail": "queued"}

    original_audio_managers = server.STATE.audio_managers
    audio_manager = DummyAudioManager()
    server.STATE.audio_managers = {"voice-session": audio_manager}
    webrtc.datachannel_managers["live-session"] = DummyDataChannelManager()

    monkeypatch.setattr(server, "_get_audio_playback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(
        webrtc,
        "_resolve_chat_identity",
        lambda session_id: SimpleNamespace(
            owner_key="cloud:client-device-abc"
            if session_id in {"live-session", "voice-session"}
            else "guest:other"
        ),
    )

    try:
        ok, status = await webrtc.play_audio_file_to_reply_target(
            {
                "transport": "webrtc",
                "owner_key": "cloud:client-device-abc",
                "session_id": "stale-relay-id",
            },
            "C:/music/demo-track.mp3",
        )
    finally:
        server.STATE.audio_managers = original_audio_managers

    assert ok is True
    assert status["state"] == "playing"
    assert audio_manager.calls == [("C:/music/demo-track.mp3", "audio_file")]


@pytest.mark.asyncio
async def test_webrtc_playback_reply_target_uses_explicit_voice_session_for_transport_owner(monkeypatch):
    webrtc = server.WebRTCManager()

    class DummyAudioManager:
        def __init__(self):
            self.calls = []

        def play_audio_file(self, file_path, *, source="audio_file"):
            self.calls.append((file_path, source))
            return {"event": "playback", "state": "playing", "source": source, "detail": "queued"}

    original_audio_managers = server.STATE.audio_managers
    audio_manager = DummyAudioManager()
    server.STATE.audio_managers = {"voice-session": audio_manager}

    monkeypatch.setattr(server, "_get_audio_playback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(
        webrtc,
        "_resolve_chat_identity",
        lambda session_id: SimpleNamespace(owner_key="telegram:5550001001"),
    )

    try:
        ok, status = await webrtc.play_audio_file_to_reply_target(
            {
                "transport": "webrtc",
                "owner_key": "telegram:5550001001",
                "session_id": "voice-session",
            },
            "/tmp/synthetic-track.mp3",
        )
    finally:
        server.STATE.audio_managers = original_audio_managers

    assert ok is True
    assert status["state"] == "playing"
    assert audio_manager.calls == [("/tmp/synthetic-track.mp3", "audio_file")]


@pytest.mark.asyncio
async def test_webrtc_voice_does_not_send_queue_position_feedback(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    spoken_messages = []
    identity = SimpleNamespace(
        canonical_session_id="session::webrtc:test-session",
        canonical_user_id="user::webrtc:test-session",
        owner_key="webrtc:test-session",
    )
    original_audio_managers = dict(server.STATE.audio_managers)

    class DummyDataChannelManager:
        async def send_message(self, message):
            sent_messages.append(message)
            return True

    class DummyAudioManager:
        def speak(self, message: str):
            spoken_messages.append(message)

    try:
        webrtc.datachannel_managers["test-session"] = DummyDataChannelManager()
        server.STATE.audio_managers["test-session"] = DummyAudioManager()
        monkeypatch.setattr(server, "get_session_execution_manager", lambda: _DummyExecutionManager(identity))

        await webrtc._process_voice_command(
            "test-session",
            "hello",
            transcript_already_mirrored=True,
            identity=identity,
        )

        assert spoken_messages == ["final reply"]
        assert len(sent_messages) == 1
        assert sent_messages[0].payload["message"] == "final reply"
        assert sent_messages[0].payload["metadata"]["voice_reply_tts_suppressed"] is False
        assert sent_messages[0].payload["metadata"]["conversation_session_id"] == server._build_client_conversation_session_id(identity)
        assert sent_messages[0].payload["metadata"]["original_message_id"]
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_webrtc_voice_suppresses_spoken_reply_when_audio_playback_starts(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    spoken_messages = []
    identity = SimpleNamespace(
        canonical_session_id="session::webrtc:test-session",
        canonical_user_id="user::webrtc:test-session",
        owner_key="webrtc:test-session",
    )
    original_audio_managers = dict(server.STATE.audio_managers)

    class DummyDataChannelManager:
        async def send_message(self, message):
            sent_messages.append(message)
            return True

    class DummyAudioManager:
        def __init__(self):
            self.playback_status = {"state": "idle", "source": "", "playback_id": ""}

        def get_playback_status(self):
            return dict(self.playback_status)

        def speak(self, message: str):
            spoken_messages.append(message)

    async def fake_process_chat_message(chat_request, ai_agent_url=None, on_chunk=None, on_media_reply=None):
        del ai_agent_url, on_chunk
        audio_manager.playback_status = {
            "state": "playing",
            "source": "audio_file",
            "playback_id": "playback-1",
        }
        return SimpleNamespace(
            response="Playing Aura.",
            agent_name="autoyou_audio_agent",
            session_id=chat_request.session_id,
            message_id="msg-1",
            metadata={"processing_time_ms": 0},
        )

    try:
        webrtc.datachannel_managers["test-session"] = DummyDataChannelManager()
        audio_manager = DummyAudioManager()
        server.STATE.audio_managers["test-session"] = audio_manager
        monkeypatch.setattr(server, "get_session_execution_manager", lambda: _ExecutingDummyExecutionManager(identity))
        monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)

        await webrtc._process_voice_command(
            "test-session",
            "play aura song",
            transcript_already_mirrored=True,
            identity=identity,
        )

        assert spoken_messages == []
        assert len(sent_messages) == 1
        assert sent_messages[0].payload["message"] == "Playing Aura."
        assert sent_messages[0].payload["metadata"]["voice_reply_tts_suppressed"] is True
        assert sent_messages[0].payload["metadata"]["voice_reply_tts_suppression_reason"] == "audio_playback_started"
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_webrtc_voice_transcript_and_reply_share_conversation_identity(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    spoken_messages = []
    identity = SimpleNamespace(
        canonical_session_id="session::webrtc:test-session::2",
        canonical_user_id="user::webrtc:test-session",
        owner_key="webrtc:test-session",
        thread_id=2,
    )
    original_audio_managers = dict(server.STATE.audio_managers)

    class DummyDataChannelManager:
        async def send_message(self, message):
            sent_messages.append(message)
            return True

    class DummyAudioManager:
        def speak(self, message: str):
            spoken_messages.append(message)

    try:
        webrtc.datachannel_managers["test-session"] = DummyDataChannelManager()
        server.STATE.audio_managers["test-session"] = DummyAudioManager()
        monkeypatch.setattr(server, "get_session_execution_manager", lambda: _DummyExecutionManager(identity))

        await webrtc._process_voice_command(
            "test-session",
            "hello",
            transcript_already_mirrored=False,
            identity=identity,
        )

        assert spoken_messages == ["final reply"]
        assert [message.payload["message"] for message in sent_messages] == ["hello", "final reply"]
        transcript_metadata = sent_messages[0].payload["metadata"]
        reply_metadata = sent_messages[1].payload["metadata"]
        assert transcript_metadata["conversation_session_id"] == server._build_client_conversation_session_id(identity)
        assert transcript_metadata["is_transcription"] is True
        assert reply_metadata["conversation_session_id"] == server._build_client_conversation_session_id(identity)
        assert reply_metadata["original_message_id"]
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_webrtc_voice_transcript_and_reply_flush_after_datachannel_ready(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    spoken_messages = []
    identity = SimpleNamespace(
        canonical_session_id="session::webrtc:test-session::buffered",
        canonical_user_id="user::webrtc:test-session",
        owner_key="webrtc:test-session",
        thread_id=3,
    )
    original_audio_managers = dict(server.STATE.audio_managers)

    class DummyDataChannelManager:
        async def send_message(self, message):
            sent_messages.append(message)
            return True

    class DummyAudioManager:
        def speak(self, message: str):
            spoken_messages.append(message)

    try:
        server.STATE.audio_managers["test-session"] = DummyAudioManager()
        monkeypatch.setattr(server, "get_session_execution_manager", lambda: _DummyExecutionManager(identity))

        await webrtc._process_voice_command(
            "test-session",
            "hello",
            transcript_already_mirrored=False,
            identity=identity,
        )

        assert spoken_messages == ["final reply"]
        # Transcript is in the short-term pending buffer; voice reply is in the offline queue.
        assert [message.payload["message"] for message in webrtc.pending_voice_chat_messages["test-session"]] == [
            "hello",
        ]
        assert len(webrtc._offline_pending_messages.get("test-session", [])) == 1
        assert webrtc._offline_pending_messages["test-session"][0]["message"].payload["message"] == "final reply"

        webrtc.datachannel_managers["test-session"] = DummyDataChannelManager()
        await webrtc._flush_pending_voice_chat_messages("test-session")

        assert [message.payload["message"] for message in sent_messages] == ["hello", "final reply"]
        assert sent_messages[0].payload["metadata"]["conversation_session_id"] == server._build_client_conversation_session_id(identity)
        assert sent_messages[1].payload["metadata"]["conversation_session_id"] == server._build_client_conversation_session_id(identity)
        assert "test-session" not in webrtc.pending_voice_chat_messages
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_webrtc_voice_callbacks_drop_audio_and_transcripts_when_video_agents_disabled(monkeypatch):
    webrtc = server.WebRTCManager()
    processed_chunks = []
    process_calls = []
    original_config = server.STATE.config

    class DummyAudioManager:
        def process_audio_chunk(self, chunk: bytes):
            processed_chunks.append(chunk)

    async def fake_process_voice_command(*args, **kwargs):
        process_calls.append((args, kwargs))

    try:
        cfg = server._default_config()
        cfg["video_call"]["audio_enabled"] = True
        cfg["video_call"]["disable_autoyou_agents"] = True
        server.STATE.config = cfg
        monkeypatch.setattr(webrtc, "_process_voice_command", fake_process_voice_command)

        webrtc._handle_inbound_voice_audio_chunk(
            "disabled-session",
            DummyAudioManager(),
            b"synthetic audio bytes",
        )
        await webrtc._enqueue_voice_command("disabled-session", "synthetic transcript")

        assert processed_chunks == []
        assert process_calls == []
        assert webrtc.voice_command_queues == {}
        assert webrtc.voice_command_workers == {}
        assert webrtc.pending_voice_chat_messages == {}
    finally:
        server.STATE.config = original_config


def test_background_keepalive_audio_chunks_are_ignored_when_safety_recording_off(monkeypatch):
    webrtc = server.WebRTCManager()
    processed_chunks = []
    recorded_chunks = []
    original_config = server.STATE.config

    class DummyAudioManager:
        def process_audio_chunk(self, chunk: bytes):
            processed_chunks.append(chunk)

    try:
        cfg = server._default_config()
        cfg["video_call"]["audio_enabled"] = True
        cfg["video_call"]["disable_autoyou_agents"] = False
        cfg["video_call"]["silent_recording_enabled"] = True
        server.STATE.config = cfg
        webrtc._set_background_audio_state(
            "synthetic-background-session",
            active=True,
            silent_recording=False,
            muted=True,
            platform="ios",
            timestamp_ms=123000,
        )
        monkeypatch.setattr(
            webrtc,
            "_write_silent_recording_chunk",
            lambda session_id, chunk: recorded_chunks.append((session_id, chunk)),
        )

        webrtc._handle_inbound_voice_audio_chunk(
            "synthetic-background-session",
            DummyAudioManager(),
            b"synthetic audio bytes",
        )

        assert processed_chunks == []
        assert recorded_chunks == []
    finally:
        server.STATE.config = original_config
        webrtc.background_audio_state_by_session.clear()


def test_background_silent_recording_audio_chunks_are_recorded(monkeypatch):
    webrtc = server.WebRTCManager()
    processed_chunks = []
    recorded_chunks = []
    original_config = server.STATE.config

    class DummyAudioManager:
        def process_audio_chunk(self, chunk: bytes):
            processed_chunks.append(chunk)

    try:
        cfg = server._default_config()
        cfg["video_call"]["audio_enabled"] = True
        cfg["video_call"]["silent_recording_enabled"] = True
        server.STATE.config = cfg
        webrtc._set_background_audio_state(
            "synthetic-safety-session",
            active=True,
            silent_recording=True,
            muted=False,
            platform="ios",
            timestamp_ms=123000,
        )
        monkeypatch.setattr(
            webrtc,
            "_write_silent_recording_chunk",
            lambda session_id, chunk: recorded_chunks.append((session_id, chunk)),
        )

        webrtc._handle_inbound_voice_audio_chunk(
            "synthetic-safety-session",
            DummyAudioManager(),
            b"synthetic audio bytes",
        )

        assert processed_chunks == []
        assert recorded_chunks == [("synthetic-safety-session", b"synthetic audio bytes")]
    finally:
        server.STATE.config = original_config
        webrtc.background_audio_state_by_session.clear()


@pytest.mark.asyncio
async def test_webrtc_voice_queue_uses_rekeyed_client_session_identity(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    captured = {}
    execution_manager = _SessionAwareExecutingExecutionManager()

    class DummyDataChannelManager:
        async def send_message(self, message):
            sent_messages.append(message)
            return True

    async def fake_process_voice_command(session_id, text, transcript_already_mirrored=False, identity=None):
        captured["session_id"] = session_id
        captured["text"] = text
        captured["transcript_already_mirrored"] = transcript_already_mirrored
        captured["identity"] = identity

    original_audio_managers = dict(server.STATE.audio_managers)
    try:
        webrtc._voice_dc_session_id["raw-session"] = "client-session"
        webrtc.datachannel_managers["client-session"] = DummyDataChannelManager()
        monkeypatch.setattr(server, "get_session_execution_manager", lambda: execution_manager)
        monkeypatch.setattr(server, "resolve_webrtc_chat_identity", execution_manager.resolve_webrtc_identity)
        monkeypatch.setattr(webrtc, "_process_voice_command", fake_process_voice_command)

        await webrtc._enqueue_voice_command("raw-session", "hello")
        await asyncio.wait_for(webrtc.voice_command_queues["raw-session"].join(), timeout=1.0)

        assert sent_messages[0].header.session_id == "client-session"
        _expected_rekeyed_identity = SimpleNamespace(
            canonical_session_id="session::guest:client-session",
            canonical_user_id="user::guest:client-session",
            owner_key="guest:client-session",
        )
        assert sent_messages[0].payload["metadata"]["conversation_session_id"] == server._build_client_conversation_session_id(_expected_rekeyed_identity)
        assert captured["session_id"] == "raw-session"
        assert captured["text"] == "hello"
        assert captured["transcript_already_mirrored"] is True
        assert captured["identity"].canonical_session_id == "session::guest:client-session"
    finally:
        await webrtc._stop_voice_command_worker("raw-session")
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_webrtc_voice_rekeys_reply_target_and_identity_for_direct_pair(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    spoken_messages = []
    captured = {}
    execution_manager = _SessionAwareExecutingExecutionManager()
    original_audio_managers = dict(server.STATE.audio_managers)

    class DummyDataChannelManager:
        async def send_message(self, message):
            sent_messages.append(message)
            return True

    class DummyAudioManager:
        def speak(self, message: str):
            spoken_messages.append(message)

    async def fake_process_chat_message(chat_request, ai_agent_url=None, on_chunk=None, on_media_reply=None):
        captured["session_id"] = chat_request.session_id
        captured["user_id"] = chat_request.user_id
        captured["metadata"] = dict(chat_request.metadata or {})
        return SimpleNamespace(
            response="final reply",
            agent_name="AutoYou AI Agent",
            session_id=chat_request.session_id,
            message_id="msg-1",
            metadata={"processing_time_ms": 0},
        )

    stale_identity = SimpleNamespace(
        canonical_session_id="session::guest:raw-session",
        canonical_user_id="user::guest:raw-session",
        owner_key="guest:raw-session",
    )

    try:
        webrtc._voice_dc_session_id["raw-session"] = "client-session"
        webrtc.datachannel_managers["client-session"] = DummyDataChannelManager()
        server.STATE.audio_managers["raw-session"] = DummyAudioManager()
        monkeypatch.setattr(server, "get_session_execution_manager", lambda: execution_manager)
        monkeypatch.setattr(server, "resolve_webrtc_chat_identity", execution_manager.resolve_webrtc_identity)
        monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)

        await webrtc._process_voice_command(
            "raw-session",
            "hello",
            transcript_already_mirrored=True,
            identity=stale_identity,
        )

        assert execution_manager.resolved_session_ids[0] == "client-session"
        assert captured["session_id"] == "session::guest:client-session"
        assert captured["user_id"] == "user::guest:client-session"
        assert captured["metadata"]["canonical_owner_key"] == "guest:client-session"
        assert captured["metadata"]["reply_target"]["session_id"] == "client-session"
        assert spoken_messages == ["final reply"]
        assert len(sent_messages) == 1
        assert sent_messages[0].header.session_id == "client-session"
        _expected_rekeyed_identity2 = SimpleNamespace(
            canonical_session_id="session::guest:client-session",
            canonical_user_id="user::guest:client-session",
            owner_key="guest:client-session",
        )
        assert sent_messages[0].payload["metadata"]["conversation_session_id"] == server._build_client_conversation_session_id(_expected_rekeyed_identity2)
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)
