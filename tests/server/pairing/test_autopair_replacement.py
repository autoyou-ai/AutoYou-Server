# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import server


AUDIO_OFFER_SDP = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"


class _FakePeerConnection:
    last_instance = None

    def __init__(self, _config):
        self.handlers = {}
        self.iceGatheringState = "complete"
        self.localDescription = None
        _FakePeerConnection.last_instance = self

    def on(self, event, handler=None):
        if handler is not None:
            self.handlers[event] = handler
            return handler

        def _decorator(callback):
            self.handlers[event] = callback
            return callback

        return _decorator

    async def setRemoteDescription(self, desc):
        self.remoteDescription = desc

    def getTransceivers(self):
        return []

    async def createAnswer(self):
        return SimpleNamespace(type="answer", sdp="v=0")

    async def setLocalDescription(self, answer):
        self.localDescription = answer

    async def close(self):
        return None


class _FakeAudioManager:
    instances = []

    def __init__(self, on_text, settings_provider=None, status_callback=None):
        self.on_text = on_text
        self.settings_provider = settings_provider
        self.status_callback = status_callback
        self._closed = False
        self.close_calls = 0
        self.tts_track = None
        self.processed = []
        _FakeAudioManager.instances.append(self)

    def set_tts_track(self, track):
        self.tts_track = track

    def process_audio_chunk(self, chunk):
        self.processed.append(chunk)

    def close(self):
        self.close_calls += 1
        self._closed = True


class _FakeAudioTrackSink:
    instances = []

    def __init__(self, track, callback):
        self.track = track
        self.callback = callback
        _FakeAudioTrackSink.instances.append(self)

    async def start(self):
        return None


class _FakeDataChannelManager:
    instances = []

    def __init__(self, role="server"):
        self.role = role
        self.session_id = None
        self.datachannel = None
        self.handlers = {}
        self.sent_messages = []
        _FakeDataChannelManager.instances.append(self)

    def set_datachannel(self, datachannel):
        self.datachannel = datachannel

    def set_session_id(self, session_id):
        self.session_id = session_id

    def register_handler(self, message_type, handler):
        self.handlers[message_type] = handler

    # The keepalive ping-pong game installs these on every datachannel.
    def set_pong_payload_augmenter(self, callback):
        self.pong_payload_augmenter = callback

    def set_ping_payload_augmenter(self, callback):
        self.ping_payload_augmenter = callback

    def _recent_rtt_baseline_seconds(self):
        return 0.05

    def _record_rtt_sample(self, sample_seconds):
        pass

    async def start_periodic_tasks(self):
        return None

    async def send_message(self, message):
        self.sent_messages.append(message)
        return True

    def disconnect(self):
        return None


class _FakeDataChannel:
    def __init__(self, label="chat", ready_state="open"):
        self.label = label
        self.readyState = ready_state
        self.handlers = {}
        self.sent = []

    def on(self, event, handler=None):
        if handler is not None:
            self.handlers[event] = handler
            return handler

        def _decorator(callback):
            self.handlers[event] = callback
            return callback

        return _decorator

    def send(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_handle_autopair_offer_proceeds_when_stale_cleanup_times_out(monkeypatch):
    manager = server.WebRTCManager()
    existing_pc = object()
    manager.session_peers["chat-1"] = existing_pc

    cleanup_calls = []

    async def _slow_cleanup(session_id, expected_pc=None):
        cleanup_calls.append((session_id, expected_pc))
        await asyncio.sleep(0.1)

    monkeypatch.setattr(manager, "async_cleanup_session", _slow_cleanup)
    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "AUTOPAIR_REPLACEMENT_CLEANUP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", None)

    answer = await manager.handle_autopair_offer(
        "chat-1",
        {"offer": {"type": "offer", "sdp": "v=0"}, "iceServers": []},
    )

    assert answer.get("type") == "answer"
    assert answer.get("sdp").strip() == "v=0"
    assert cleanup_calls == [("chat-1", existing_pc)]
    assert manager.session_peers["chat-1"] is not existing_pc


@pytest.mark.asyncio
async def test_handle_autopair_offer_on_track_recreates_closed_audio_manager(monkeypatch):
    manager = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)

    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", _FakeAudioManager)
    monkeypatch.setattr(server, "AudioTrackSink", _FakeAudioTrackSink)

    _FakePeerConnection.last_instance = None
    _FakeAudioManager.instances.clear()
    _FakeAudioTrackSink.instances.clear()

    try:
        await manager.handle_autopair_offer(
            "chat-1",
            {"offer": {"type": "offer", "sdp": AUDIO_OFFER_SDP}, "iceServers": []},
        )

        first_manager = server.STATE.audio_managers["chat-1"]
        server.STATE.audio_managers.pop("chat-1", None)
        first_manager.close()

        _FakePeerConnection.last_instance.handlers["track"](SimpleNamespace(kind="audio"))
        await asyncio.sleep(0)

        replacement_manager = server.STATE.audio_managers["chat-1"]
        assert replacement_manager is not first_manager
        assert first_manager._closed is True
        assert replacement_manager._closed is False
        _FakeAudioTrackSink.instances[-1].callback(b"voice")
        assert replacement_manager.processed == [b"voice"]
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_handle_autopair_offer_replacement_preserves_fresh_audio_manager_during_late_cleanup(monkeypatch):
    manager = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)
    release_old_close = asyncio.Event()
    old_close_started = asyncio.Event()

    class _BlockingClosePeer:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1
            old_close_started.set()
            await release_old_close.wait()

    old_peer = _BlockingClosePeer()
    old_audio_manager = _FakeAudioManager(lambda _text: None)

    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "AUTOPAIR_REPLACEMENT_CLEANUP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", _FakeAudioManager)
    monkeypatch.setattr(server, "AudioTrackSink", _FakeAudioTrackSink)

    _FakePeerConnection.last_instance = None
    _FakeAudioManager.instances.clear()
    _FakeAudioTrackSink.instances.clear()

    try:
        server.STATE.audio_managers["chat-1"] = old_audio_manager
        manager.session_peers["chat-1"] = old_peer
        manager.cleanup_session("chat-1", expected_pc=old_peer)
        cleanup_task = manager.cleanup_tasks["chat-1"]
        await asyncio.wait_for(old_close_started.wait(), timeout=1.0)

        answer = await manager.handle_autopair_offer(
            "chat-1",
            {"offer": {"type": "offer", "sdp": AUDIO_OFFER_SDP}, "iceServers": []},
        )

        replacement_manager = server.STATE.audio_managers["chat-1"]
        replacement_peer = manager.session_peers["chat-1"]

        release_old_close.set()
        await asyncio.wait_for(cleanup_task, timeout=1.0)

        assert answer.get("type") == "answer"
        assert answer.get("sdp").strip() == "v=0"
        assert old_audio_manager._closed is True
        assert replacement_manager is not old_audio_manager
        assert replacement_manager._closed is False
        assert manager.session_peers["chat-1"] is replacement_peer
        assert manager.session_peers["chat-1"] is not old_peer
        assert server.STATE.audio_managers["chat-1"] is replacement_manager
    finally:
        release_old_close.set()
        if "chat-1" in manager.cleanup_tasks:
            await asyncio.gather(manager.cleanup_tasks["chat-1"], return_exceptions=True)
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_handle_autopair_offer_prebinds_cloud_client_session_id_for_datachannel(monkeypatch):
    manager = server.WebRTCManager()
    aliased = []

    async def _noop_async(*args, **kwargs):
        return None

    def _skip_background_task(coro):
        coro.close()
        return asyncio.create_task(_noop_async())

    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_publish_voice_call_status", _noop_async)
    monkeypatch.setattr(manager, "_flush_pending_voice_chat_messages", _noop_async)
    monkeypatch.setattr(server, "track_background_task", _skip_background_task)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", None)
    monkeypatch.setattr(server, "DataChannelManager", _FakeDataChannelManager)
    monkeypatch.setattr(server, "alias_webrtc_chat_session", lambda old_id, new_id: aliased.append((old_id, new_id)))

    _FakePeerConnection.last_instance = None
    _FakeDataChannelManager.instances.clear()
    manager.voice_call_status_by_session["relay-123"] = {"state": "ready"}

    answer = await manager.handle_autopair_offer(
        "relay-123",
        {
            "offer": {"type": "offer", "sdp": "v=0"},
            "iceServers": [],
            "_autoyou_pairing_platform": "cloud",
            "_autoyou_sender_id": "client-device-abc",
        },
    )

    channel = _FakeDataChannel()
    _FakePeerConnection.last_instance.handlers["datachannel"](channel)
    await asyncio.sleep(0)

    assert answer.get("type") == "answer"
    assert answer.get("sdp").strip() == "v=0"
    assert manager.datachannel_managers["relay-123"] is manager.datachannel_managers["client-device-abc"]
    assert manager.datachannel_managers["client-device-abc"].session_id == "client-device-abc"
    assert manager.voice_call_status_by_session["client-device-abc"]["state"] == "ready"
    assert manager._voice_dc_session_id["relay-123"] == "client-device-abc"
    assert ("relay-123", "client-device-abc") in aliased


def test_live_control_datachannel_sessions_dedupes_aliases_and_prefers_client_id():
    class _SendableManager:
        def __init__(self):
            self.session_id = "client-device-abc"

        async def send_message(self, _message):
            return True

    manager = server.WebRTCManager()
    datachannel_manager = _SendableManager()
    manager.datachannel_managers["relay-123"] = datachannel_manager
    manager.datachannel_managers["audio-relay-123"] = datachannel_manager
    manager.datachannel_managers["video-relay-123"] = datachannel_manager
    manager.datachannel_managers["client-device-abc"] = datachannel_manager
    manager._voice_dc_session_id["relay-123"] = "client-device-abc"
    manager._voice_dc_session_id["audio-relay-123"] = "client-device-abc"
    manager._voice_dc_session_id["video-relay-123"] = "client-device-abc"

    assert manager._live_control_datachannel_sessions() == [
        ("client-device-abc", datachannel_manager)
    ]


def test_live_control_datachannel_sessions_dedupes_distinct_managers_by_owner(monkeypatch):
    class _SendableManager:
        async def send_message(self, _message):
            return True

    def _identity(session_id):
        if session_id in {"audio-relay-123", "video-relay-123"}:
            return SimpleNamespace(
                owner_key="ios:client-device-abc",
                canonical_session_id="session::ios:client-device-abc",
            )
        return SimpleNamespace(
            owner_key=f"guest:{session_id}",
            canonical_session_id=f"session::guest:{session_id}",
        )

    manager = server.WebRTCManager()
    audio_manager = _SendableManager()
    video_manager = _SendableManager()
    manager.datachannel_managers["audio-relay-123"] = audio_manager
    manager.datachannel_managers["video-relay-123"] = video_manager
    monkeypatch.setattr(server, "resolve_webrtc_chat_identity", _identity)

    assert manager._live_control_datachannel_sessions() == [
        ("audio-relay-123", audio_manager)
    ]


@pytest.mark.asyncio
async def test_alias_only_datachannel_receives_voice_status_and_chat(monkeypatch):
    manager = server.WebRTCManager()
    datachannel_manager = _FakeDataChannelManager()
    manager._voice_dc_session_id["relay-123"] = "client-device-abc"
    manager.datachannel_managers["client-device-abc"] = datachannel_manager

    await manager._publish_voice_call_status(
        "relay-123",
        {"event": "readiness", "state": "ready"},
    )
    chat_result = await manager._deliver_voice_chat_message(
        "relay-123",
        SimpleNamespace(kind="chat-reply"),
        label="chat message",
    )

    assert chat_result == "sent"
    assert len(datachannel_manager.sent_messages) == 2
    assert datachannel_manager.sent_messages[0].payload["state"] == "ready"
    assert datachannel_manager.sent_messages[1].kind == "chat-reply"


def test_reply_target_resolution_accepts_alias_only_datachannel(monkeypatch):
    manager = server.WebRTCManager()
    datachannel_manager = _FakeDataChannelManager()
    manager._voice_dc_session_id["relay-123"] = "client-device-abc"
    manager.datachannel_managers["client-device-abc"] = datachannel_manager
    monkeypatch.setattr(
        manager,
        "_resolve_chat_identity",
        lambda session_id: SimpleNamespace(owner_key="cloud:client-device-abc"),
    )

    target_session_id, target_manager, owner_key, resolution, error = manager._resolve_client_control_target(
        {"transport": "webrtc", "session_id": "relay-123"}
    )

    assert target_session_id == "relay-123"
    assert target_manager is datachannel_manager
    assert owner_key == "cloud:client-device-abc"
    assert resolution == "reply_target"
    assert error is None


@pytest.mark.asyncio
async def test_handle_autopair_offer_replaces_existing_cloud_client_session(monkeypatch):
    manager = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)

    class _TrackedPeerConnection:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    old_peer = _TrackedPeerConnection()
    old_audio_manager = _FakeAudioManager(lambda _text: None)
    old_datachannel_manager = _FakeDataChannelManager()

    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", _FakeAudioManager)
    monkeypatch.setattr(server, "AudioTrackSink", _FakeAudioTrackSink)

    _FakePeerConnection.last_instance = None
    _FakeAudioManager.instances.clear()
    _FakeAudioTrackSink.instances.clear()

    try:
        manager.session_peers["relay-old"] = old_peer
        manager.datachannel_managers["relay-old"] = old_datachannel_manager
        manager.datachannel_managers["client-device-abc"] = old_datachannel_manager
        manager.voice_call_status_by_session["client-device-abc"] = {
            "state": "warming",
            "detail": "Preparing voice pipeline on server (base)...",
        }
        manager._voice_dc_session_id["relay-old"] = "client-device-abc"
        server.STATE.audio_managers["relay-old"] = old_audio_manager
        server.STATE.audio_managers["client-device-abc"] = old_audio_manager

        answer = await manager.handle_autopair_offer(
            "relay-new",
            {
                "offer": {"type": "offer", "sdp": AUDIO_OFFER_SDP},
                "iceServers": [],
                "_autoyou_pairing_platform": "cloud",
                "_autoyou_sender_id": "client-device-abc",
            },
        )

        replacement_manager = server.STATE.audio_managers["relay-new"]

        assert answer.get("type") == "answer"
        assert answer.get("sdp").strip() == "v=0"
        assert old_peer.close_calls == 1
        assert old_audio_manager._closed is True
        assert replacement_manager is not old_audio_manager
        assert replacement_manager._closed is False
        assert "relay-old" not in manager.session_peers
        assert "client-device-abc" not in manager.datachannel_managers
        assert "client-device-abc" not in manager.voice_call_status_by_session
        assert "client-device-abc" not in server.STATE.audio_managers
        assert manager.session_peers["relay-new"] is _FakePeerConnection.last_instance
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_handle_session_offer_replaces_existing_direct_pair_session(monkeypatch):
    manager = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)
    original_session_cache = dict(server.STATE.session_cache)

    class _TrackedPeerConnection:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    old_peer = _TrackedPeerConnection()
    old_audio_manager = _FakeAudioManager(lambda _text: None)
    old_datachannel_manager = _FakeDataChannelManager()

    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", _FakeAudioManager)
    monkeypatch.setattr(server, "AudioTrackSink", _FakeAudioTrackSink)

    _FakePeerConnection.last_instance = None
    _FakeAudioManager.instances.clear()
    _FakeAudioTrackSink.instances.clear()

    try:
        server.STATE.session_cache = {
            "direct-old-session": {
                "id": "direct-old-session",
                "authenticated": True,
                "stable_client_id": "ios-direct-client-abc",
            },
            "direct-new-session": {
                "id": "direct-new-session",
                "authenticated": True,
                "stable_client_id": "ios-direct-client-abc",
            },
        }
        manager.session_peers["direct-old-session"] = old_peer
        manager.datachannel_managers["direct-old-session"] = old_datachannel_manager
        manager.voice_call_status_by_session["direct-old-session"] = {
            "state": "warming",
            "detail": "Preparing voice pipeline on server (base)...",
        }
        server.STATE.audio_managers["direct-old-session"] = old_audio_manager

        answer = await manager.handle_session_offer(
            "direct-new-session",
            {"type": "offer", "sdp": AUDIO_OFFER_SDP, "iceServers": []},
        )

        replacement_manager = server.STATE.audio_managers["direct-new-session"]

        assert answer.get("type") == "answer"
        assert answer.get("sdp").strip() == "v=0"
        assert old_peer.close_calls == 1
        assert old_audio_manager._closed is True
        assert replacement_manager is not old_audio_manager
        assert replacement_manager._closed is False
        assert "direct-old-session" not in manager.session_peers
        assert "direct-old-session" not in manager.datachannel_managers
        assert "direct-old-session" not in manager.voice_call_status_by_session
        assert "direct-old-session" not in server.STATE.audio_managers
        assert "direct-old-session" not in server.STATE.session_cache
        assert "direct-new-session" in server.STATE.session_cache
        assert manager.session_peers["direct-new-session"] is _FakePeerConnection.last_instance
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)
        server.STATE.session_cache = original_session_cache


@pytest.mark.asyncio
async def test_background_autopair_offer_does_not_start_audio_manager(monkeypatch):
    manager = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)

    async def _noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_publish_voice_call_status", _noop_async)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", _FakeAudioManager)

    _FakePeerConnection.last_instance = None
    _FakeAudioManager.instances.clear()

    try:
        answer = await manager.handle_autopair_offer(
            "relay-123",
            {
                "offer": {"type": "offer", "sdp": "v=0"},
                "iceServers": [],
                "background_audio": {
                    "active": True,
                    "platform": "ios",
                    "silent_recording": False,
                    "client_audio_direction": "recvonly",
                },
            },
        )

        assert answer.get("type") == "answer"
        assert _FakeAudioManager.instances == []
        assert "relay-123" not in server.STATE.audio_managers
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_datachannel_only_autopair_offer_does_not_start_audio_manager(monkeypatch):
    manager = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)

    async def _noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_publish_voice_call_status", _noop_async)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", _FakeAudioManager)

    _FakePeerConnection.last_instance = None
    _FakeAudioManager.instances.clear()

    try:
        answer = await manager.handle_autopair_offer(
            "relay-123",
            {"offer": {"type": "offer", "sdp": "v=0"}, "iceServers": []},
        )

        assert answer.get("type") == "answer"
        assert _FakeAudioManager.instances == []
        assert "relay-123" not in server.STATE.audio_managers
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_background_pair_offer_does_not_start_audio_manager(monkeypatch):
    manager = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)
    original_session_cache = dict(server.STATE.session_cache)

    async def _noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_publish_voice_call_status", _noop_async)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", _FakeAudioManager)

    _FakePeerConnection.last_instance = None
    _FakeAudioManager.instances.clear()

    try:
        server.STATE.session_cache = {
            "pair-session": {
                "id": "pair-session",
                "authenticated": True,
                "stable_client_id": "client-device-abc",
            }
        }
        answer = await manager.handle_session_offer(
            "pair-session",
            {
                "type": "offer",
                "sdp": "v=0",
                "iceServers": [],
                "background_audio": {
                    "active": True,
                    "platform": "ios",
                    "silent_recording": False,
                    "client_audio_direction": "recvonly",
                },
            },
        )

        assert answer.get("type") == "answer"
        assert _FakeAudioManager.instances == []
        assert "pair-session" not in server.STATE.audio_managers
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)
        server.STATE.session_cache = original_session_cache


@pytest.mark.asyncio
async def test_datachannel_only_pair_offer_does_not_start_audio_manager(monkeypatch):
    manager = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)
    original_session_cache = dict(server.STATE.session_cache)

    async def _noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_publish_voice_call_status", _noop_async)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", _FakeAudioManager)

    _FakePeerConnection.last_instance = None
    _FakeAudioManager.instances.clear()

    try:
        server.STATE.session_cache = {
            "pair-session": {
                "id": "pair-session",
                "authenticated": True,
                "stable_client_id": "client-device-abc",
            }
        }
        answer = await manager.handle_session_offer(
            "pair-session",
            {"type": "offer", "sdp": "v=0", "iceServers": []},
        )

        assert answer.get("type") == "answer"
        assert _FakeAudioManager.instances == []
        assert "pair-session" not in server.STATE.audio_managers
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)
        server.STATE.session_cache = original_session_cache


@pytest.mark.asyncio
async def test_handle_autopair_offer_replays_cached_voice_status_after_datachannel_open(monkeypatch):
    manager = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)

    async def _noop_async(*args, **kwargs):
        return None

    def _skip_background_task(coro):
        coro.close()
        return asyncio.create_task(_noop_async())

    class _ReadyAwareFakeDataChannelManager(_FakeDataChannelManager):
        async def send_message(self, message):
            ready_state = getattr(self.datachannel, "readyState", None)
            if ready_state not in (None, "open"):
                return False
            return await super().send_message(message)

    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_prime_conversation_context_status", _noop_async)
    monkeypatch.setattr(manager, "_flush_pending_voice_chat_messages", _noop_async)
    monkeypatch.setattr(manager, "_resolve_chat_identity", lambda _sid: SimpleNamespace(owner_key="", canonical_user_id=""))
    monkeypatch.setattr(server, "track_background_task", _skip_background_task)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", _FakeAudioManager)
    monkeypatch.setattr(server, "DataChannelManager", _ReadyAwareFakeDataChannelManager)

    _FakePeerConnection.last_instance = None
    _FakeAudioManager.instances.clear()
    _ReadyAwareFakeDataChannelManager.instances.clear()
    manager.voice_call_status_by_session["relay-123"] = {
        "state": "ready",
        "detail": "Voice pipeline ready.",
    }

    try:
        await manager.handle_autopair_offer(
            "relay-123",
            {
                "offer": {"type": "offer", "sdp": "v=0"},
                "iceServers": [],
                "_autoyou_pairing_platform": "cloud",
                "_autoyou_sender_id": "client-device-abc",
            },
        )

        channel = _FakeDataChannel(ready_state="connecting")
        _FakePeerConnection.last_instance.handlers["datachannel"](channel)
        await asyncio.sleep(0)

        datachannel_manager = manager.datachannel_managers["client-device-abc"]
        assert datachannel_manager.sent_messages == []

        channel.readyState = "open"
        channel.handlers["open"]()
        await asyncio.sleep(0)

        assert any(
            message.payload.get("event") == "readiness" and message.payload.get("state") == "ready"
            for message in datachannel_manager.sent_messages
        )
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_handle_autopair_offer_prefers_live_voice_status_over_stale_client_unavailable(monkeypatch):
    manager = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)

    async def _noop_async(*args, **kwargs):
        return None

    def _skip_background_task(coro):
        coro.close()
        return asyncio.create_task(_noop_async())

    class _ReadyAudioManager(_FakeAudioManager):
        def get_readiness_status(self):
            return {
                "event": "readiness",
                "state": "ready",
                "detail": "Voice pipeline ready.",
                "timestamp_ms": 123,
                "platform": "server",
            }

    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_prime_conversation_context_status", _noop_async)
    monkeypatch.setattr(manager, "_flush_pending_voice_chat_messages", _noop_async)
    monkeypatch.setattr(manager, "_resolve_chat_identity", lambda _sid: SimpleNamespace(owner_key="", canonical_user_id=""))
    monkeypatch.setattr(server, "track_background_task", _skip_background_task)
    monkeypatch.setattr(server, "RTCPeerConnection", _FakePeerConnection)
    monkeypatch.setattr(server, "RTCSessionDescription", lambda sdp, type: SimpleNamespace(sdp=sdp, type=type))
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server, "AudioManager", _ReadyAudioManager)
    monkeypatch.setattr(server, "DataChannelManager", _FakeDataChannelManager)

    _FakePeerConnection.last_instance = None
    _FakeDataChannelManager.instances.clear()
    manager.voice_call_status_by_session["client-device-abc"] = {
        "state": "unavailable",
        "detail": "This client connected without voice-call audio.",
    }

    try:
        await manager.handle_autopair_offer(
            "relay-123",
            {
                "offer": {"type": "offer", "sdp": AUDIO_OFFER_SDP},
                "iceServers": [],
                "_autoyou_pairing_platform": "cloud",
                "_autoyou_sender_id": "client-device-abc",
            },
        )

        channel = _FakeDataChannel()
        _FakePeerConnection.last_instance.handlers["datachannel"](channel)
        await asyncio.sleep(0)

        sent_readiness = [
            message.payload.get("state")
            for message in manager.datachannel_managers["client-device-abc"].sent_messages
            if message.payload.get("event") == "readiness"
        ]
        assert "ready" in sent_readiness
        assert "unavailable" not in sent_readiness
        assert manager.voice_call_status_by_session["client-device-abc"]["state"] == "ready"
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)
