# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-cc2c15a33ff35e04fdea3deb


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
import inspect
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-cc2c15a33ff35e04fdea3deb"


ensure_repo_on_path()

import server
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType


@pytest.mark.asyncio
async def test_webrtc_retires_closed_datachannel_manager_but_protects_live_aliases():
    webrtc = server.WebRTCManager()

    class DummyDataChannelManager:
        def __init__(self, *, active, ready_state):
            self.connection_active = active
            self.datachannel = SimpleNamespace(readyState=ready_state)
            self.disconnect_calls = 0

        def disconnect(self):
            self.disconnect_calls += 1
            self.connection_active = False
            self.datachannel = None

    live_manager = DummyDataChannelManager(active=True, ready_state="open")
    webrtc.datachannel_managers["client-live"] = live_manager
    assert webrtc._datachannel_manager_is_live(live_manager)
    assert not webrtc._retire_inactive_datachannel_manager(live_manager)
    assert webrtc.datachannel_managers["client-live"] is live_manager
    assert live_manager.disconnect_calls == 0

    disconnected_manager = DummyDataChannelManager(active=False, ready_state="open")
    assert not webrtc._datachannel_manager_is_live(disconnected_manager)

    stale_manager = DummyDataChannelManager(active=True, ready_state="closed")
    stale_task = asyncio.create_task(asyncio.sleep(3600))
    try:
        webrtc.datachannel_managers["relay-stale"] = stale_manager
        webrtc.datachannel_managers["client-stale"] = stale_manager
        webrtc._voice_dc_session_id["relay-stale"] = "client-stale"
        webrtc.http_proxy_request_tasks["client-stale"] = {"request-1": stale_task}

        assert not webrtc._datachannel_manager_is_live(stale_manager)
        assert webrtc._retire_inactive_datachannel_manager(stale_manager)
        await asyncio.sleep(0)

        assert stale_manager.disconnect_calls == 1
        assert "relay-stale" not in webrtc.datachannel_managers
        assert "client-stale" not in webrtc.datachannel_managers
        assert "relay-stale" not in webrtc._voice_dc_session_id
        assert "client-stale" not in webrtc.http_proxy_request_tasks
        assert stale_task.cancelled()
    finally:
        if not stale_task.done():
            stale_task.cancel()
        await asyncio.gather(stale_task, return_exceptions=True)


def test_webrtc_datachannel_open_log_redacts_session_identifier(monkeypatch, caplog):
    webrtc = server.WebRTCManager()
    monkeypatch.setattr(server, "DataChannelManager", None)

    class DummyChannel:
        label = "chat"
        readyState = "open"

        def __init__(self):
            self.handlers = {}

        def on(self, event):
            def decorator(func):
                self.handlers[event] = func
                # from __debug_provenance_a__ import schedule
                return func

            return decorator

    handler = webrtc._create_datachannel_handler(
        "+11234567890@c.us",
        "autopair",
        enable_keepalive=False,
    )

    with caplog.at_level(logging.INFO, logger=server.LOGGER.name):
        handler(DummyChannel())

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "+11234567890" not in messages
    assert "***7890@c.us" in messages


@pytest.mark.asyncio
async def test_native_host_input_uses_transport_identity_not_message_header(monkeypatch):
    webrtc = server.WebRTCManager()
    queued = []

    class DummyDataChannelManager:
        def __init__(self, role):
            self.handlers = {}
            self.pong_payload_augmenter = None
            self.ping_payload_augmenter = None

        def set_datachannel(self, channel):
            self.datachannel = channel

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

    class DummyChannel:
        label = "voice"
        readyState = "connecting"

        def __init__(self):
            self.handlers = {}

        def on(self, event):
            def decorator(func):
                self.handlers[event] = func
                return func

            return decorator

    monkeypatch.setattr(server, "DataChannelManager", DummyDataChannelManager)
    monkeypatch.setattr(
        webrtc,
        "_track_session_task",
        lambda session_id, coroutine, *_args, **_kwargs: queued.append((session_id, coroutine)),
    )
    voice_handler = AsyncMock()
    monkeypatch.setattr(webrtc, "_handle_voice_call_control_message", voice_handler)

    channel = DummyChannel()
    webrtc._create_datachannel_handler(
        "transport-relay-synthetic",
        "local-pair",
        enable_keepalive=False,
    )(channel)
    manager = webrtc.datachannel_managers["transport-relay-synthetic"]
    message = DataChannelMessage(
        header=MessageHeader(
            message_id="synthetic-control-message",
            message_type=MessageType.VOICE_CALL_CONTROL,
            timestamp=0.0,
            session_id="client-asserted-synthetic",
            user_id="synthetic-client",
        ),
        payload={"event": "remote_desktop_input"},
    )

    await manager.handlers[MessageType.VOICE_CALL_CONTROL](message)
    assert len(queued) == 1
    await queued[0][1]

    assert queued[0][0] == "client-asserted-synthetic"
    assert voice_handler.await_args.kwargs["trusted_session_id"] == "transport-relay-synthetic"


@pytest.mark.asyncio
async def test_webrtc_shutdown_closes_audio_managers_and_session_resources():
    webrtc = server.WebRTCManager()

    class DummyPeerConnection:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    class DummyDataChannelManager:
        def __init__(self):
            self.disconnect_calls = 0

        def disconnect(self):
            self.disconnect_calls += 1

    class DummyAudioManager:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    class DummyAudioSink:
        def __init__(self):
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1

    worker = asyncio.create_task(asyncio.sleep(3600))
    message_task = asyncio.create_task(asyncio.sleep(3600))
    establish_task = asyncio.create_task(asyncio.sleep(3600))
    original_audio_managers = server.STATE.audio_managers

    try:
        peer = DummyPeerConnection()
        datachannel_manager = DummyDataChannelManager()
        audio_manager = DummyAudioManager()
        audio_sink = DummyAudioSink()

        server.STATE.audio_managers = {"session-1": audio_manager}
        webrtc.session_peers["session-1"] = peer
        webrtc.audio_sinks["session-1"] = audio_sink
        webrtc.datachannel_managers["session-1"] = datachannel_manager
        webrtc.pending_candidates["session-1"] = ["candidate"]
        webrtc.outgoing_trickle_candidates["session-1"] = []
        webrtc.voice_call_status_by_session["session-1"] = {"state": "ready"}
        webrtc.voice_command_queues["session-1"] = asyncio.Queue(maxsize=2)
        webrtc.voice_command_workers["session-1"] = worker
        webrtc.session_message_tasks["session-1"] = {message_task}
        webrtc.session_establishment_tasks["session-1"] = establish_task

        await webrtc.shutdown()

        assert peer.close_calls == 1
        assert audio_sink.stop_calls == 1
        assert datachannel_manager.disconnect_calls == 1
        assert audio_manager.close_calls == 1
        assert not webrtc.audio_sinks
        assert not webrtc.session_peers
        assert not webrtc.datachannel_managers
        assert not webrtc.session_establishment_tasks
        assert not webrtc.pending_candidates
        assert not webrtc.outgoing_trickle_candidates
        assert not webrtc.voice_call_status_by_session
        assert not webrtc.voice_command_queues
        assert not webrtc.voice_command_workers
        assert not webrtc.session_message_tasks
        assert server.STATE.audio_managers == {}
        assert worker.done()
        assert message_task.done()
        assert establish_task.done()
    finally:
        if not worker.done():
            worker.cancel()
            try:
                await worker
            except Exception:
                pass
        if not message_task.done():
            message_task.cancel()
            try:
                await message_task
            except Exception:
                pass
        if not establish_task.done():
            establish_task.cancel()
            try:
                await establish_task
            except Exception:
                pass
        server.STATE.audio_managers = original_audio_managers


@pytest.mark.asyncio
async def test_webrtc_cleanup_dedupes_client_alias_resources(monkeypatch):
    webrtc = server.WebRTCManager()

    class DummyPeerConnection:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    class DummyDataChannelManager:
        def __init__(self):
            self.disconnect_calls = 0

        def disconnect(self):
            self.disconnect_calls += 1

    class DummyAudioManager:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    class DummySink:
        def __init__(self):
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1

    class DummyTrack:
        def __init__(self):
            self.disable_calls = 0

        def disable(self):
            self.disable_calls += 1

    peer = DummyPeerConnection()
    datachannel_manager = DummyDataChannelManager()
    audio_manager = DummyAudioManager()
    audio_sink = DummySink()
    video_sink = DummySink()
    desktop_track = DummyTrack()
    local_audio_track = DummyTrack()
    alias_ids = ["relay-123", "client-device-abc", "audio-relay-123", "video-relay-123"]

    monkeypatch.setattr(server.STATE, "audio_managers", {}, raising=False)
    monkeypatch.setattr(server.STATE, "local_audio_tracks", {}, raising=False)

    for alias_id in alias_ids:
        webrtc.session_peers[alias_id] = peer
        webrtc.datachannel_managers[alias_id] = datachannel_manager
        webrtc.audio_sinks[alias_id] = audio_sink
        webrtc.video_sinks[alias_id] = video_sink
        webrtc.desktop_video_tracks[alias_id] = desktop_track
        server.STATE.audio_managers[alias_id] = audio_manager
        server.STATE.local_audio_tracks[alias_id] = [local_audio_track]
    webrtc._voice_dc_session_id["relay-123"] = "client-device-abc"
    webrtc._voice_dc_session_id["audio-relay-123"] = "client-device-abc"
    webrtc._voice_dc_session_id["video-relay-123"] = "client-device-abc"

    await webrtc.async_cleanup_session("audio-relay-123")

    assert peer.close_calls == 1
    assert datachannel_manager.disconnect_calls == 1
    assert audio_manager.close_calls == 1
    assert audio_sink.stop_calls == 1
    assert video_sink.stop_calls == 1
    assert desktop_track.disable_calls == 1
    assert local_audio_track.disable_calls == 1
    assert not webrtc.session_peers
    assert not webrtc.datachannel_managers
    assert not webrtc.audio_sinks
    assert not webrtc.video_sinks
    assert not webrtc.desktop_video_tracks
    assert server.STATE.audio_managers == {}
    assert server.STATE.local_audio_tracks == {}


@pytest.mark.asyncio
async def test_webrtc_cleanup_removes_video_only_rekey_alias():
    webrtc = server.WebRTCManager()

    class DummyTrack:
        def __init__(self):
            self.disable_calls = 0

        def disable(self):
            self.disable_calls += 1

    track = DummyTrack()
    webrtc._voice_dc_session_id["transport-video-synthetic"] = "stable-video-synthetic"
    webrtc._voice_dc_session_id["replacement-video-synthetic"] = "stable-video-synthetic"
    webrtc.desktop_video_tracks["transport-video-synthetic"] = track
    webrtc.desktop_video_tracks["replacement-video-synthetic"] = track
    webrtc.desktop_video_tracks["stable-video-synthetic"] = track

    await webrtc.async_cleanup_session("transport-video-synthetic")

    assert track.disable_calls == 1
    assert not webrtc.desktop_video_tracks
    assert not webrtc._voice_dc_session_id


@pytest.mark.asyncio
async def test_forced_shutdown_waits_for_inflight_native_mouse_down(monkeypatch):
    webrtc = server.WebRTCManager()

    class DummyTrack:
        is_enabled = True

        def remote_desktop_mapping(self):
            return {
                "content_rect": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                "monitor_bounds": {"left": 0, "top": 0, "width": 100, "height": 100},
            }

        def map_output_point_to_desktop(self, x, y):
            return int(float(x) * 99), int(float(y) * 99)

    session_id = "forced-shutdown-synthetic"
    track = DummyTrack()
    webrtc.desktop_video_tracks[session_id] = track
    webrtc._set_voice_call_client_active(session_id, True)
    webrtc._store_remote_desktop_control_lease(
        session_id,
        control_id="forced-control",
        touch_mode="direct",
        track=track,
    )
    input_started = threading.Event()
    allow_input_to_finish = threading.Event()
    released = []

    def blocking_mouse_down(payload, *, track):
        input_started.set()
        assert allow_input_to_finish.wait(2.0)
        return True

    original_config = server.STATE.config
    original_audio_managers = dict(server.STATE.audio_managers)
    original_session_cache = dict(server.STATE.session_cache)
    cfg = server._default_config()
    cfg["video_call"]["remote_desktop"]["control_enabled"] = True
    try:
        server.STATE.config = cfg
        monkeypatch.setattr(server, "remote_desktop_input_backend_probed", lambda: True)
        monkeypatch.setattr(server, "execute_remote_desktop_input", blocking_mouse_down)
        monkeypatch.setattr(
            server,
            "release_remote_desktop_inputs",
            lambda buttons=(): released.append(set(buttons)),
        )

        input_task = asyncio.create_task(
            webrtc._handle_remote_desktop_input(
                session_id,
                {
                    "event": "remote_desktop_input",
                    "control_id": "forced-control",
                    "source": "autoyou_lite",
                    "platform": "ios",
                    "input_type": "button",
                    "button": "left",
                    "phase": "down",
                    "x": 0.5,
                    "y": 0.5,
                },
            )
        )
        assert await asyncio.to_thread(input_started.wait, 1.0)
        force_task = asyncio.create_task(webrtc._force_clear_shutdown_state())
        await asyncio.sleep(0)
        assert not force_task.done()

        allow_input_to_finish.set()
        await asyncio.gather(input_task, force_task)

        assert released == [{"left"}]
        assert not webrtc.remote_desktop_control_leases_by_session
    finally:
        allow_input_to_finish.set()
        server.STATE.config = original_config
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)
        server.STATE.session_cache.clear()
        server.STATE.session_cache.update(original_session_cache)


@pytest.mark.asyncio
async def test_webrtc_cleanup_bounds_hanging_peer_close_and_continues(monkeypatch):
    webrtc = server.WebRTCManager()
    original_audio_managers = server.STATE.audio_managers

    class HangingPeerConnection:
        def __init__(self):
            self.close_started = asyncio.Event()
            self.close_cancelled = False

        async def close(self):
            self.close_started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.close_cancelled = True
                raise

    class DummyDataChannelManager:
        def __init__(self):
            self.disconnect_calls = 0

        def disconnect(self):
            self.disconnect_calls += 1

    class DummyAudioManager:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    peer = HangingPeerConnection()
    datachannel_manager = DummyDataChannelManager()
    audio_manager = DummyAudioManager()

    monkeypatch.setattr(server, "WEBRTC_PEER_CLOSE_TIMEOUT_SECONDS", 0.01)
    server.STATE.audio_managers = {"session-hanging-close": audio_manager}
    webrtc.session_peers["session-hanging-close"] = peer
    webrtc.datachannel_managers["session-hanging-close"] = datachannel_manager

    try:
        await asyncio.wait_for(webrtc.async_cleanup_session("session-hanging-close"), timeout=1.0)
        await asyncio.sleep(0)

        assert peer.close_started.is_set()
        assert peer.close_cancelled is True
        assert datachannel_manager.disconnect_calls == 1
        assert audio_manager.close_calls == 1
        assert not webrtc.session_peers
        assert not webrtc.datachannel_managers
        assert server.STATE.audio_managers == {}
    finally:
        server.STATE.audio_managers = original_audio_managers


@pytest.mark.asyncio
async def test_webrtc_shutdown_clears_state_when_outer_timeout_cancels(monkeypatch):
    webrtc = server.WebRTCManager()
    blocker = asyncio.Event()
    cleanup_started = asyncio.Event()
    original_session_cache = dict(server.STATE.session_cache)
    original_audio_managers = dict(server.STATE.audio_managers)

    async def never_finishes_cleanup(session_id, expected_pc=None):
        del session_id, expected_pc
        cleanup_started.set()
        await blocker.wait()

    try:
        monkeypatch.setattr(webrtc, "async_cleanup_session", never_finishes_cleanup)
        webrtc.session_peers["session-stuck-shutdown"] = object()
        webrtc.datachannel_managers["session-stuck-shutdown"] = object()
        webrtc.pending_candidates["session-stuck-shutdown"] = ["candidate"]
        webrtc._voice_dc_session_id["relay-stuck"] = "session-stuck-shutdown"
        server.STATE.session_cache["session-stuck-shutdown"] = {"authenticated": True}
        server.STATE.audio_managers["session-stuck-shutdown"] = object()

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(webrtc.shutdown(), timeout=0.05)

        assert cleanup_started.is_set()
        assert not webrtc.session_peers
        assert not webrtc.datachannel_managers
        assert not webrtc.pending_candidates
        assert not webrtc._voice_dc_session_id
        assert not server.STATE.session_cache
        assert not server.STATE.audio_managers
    finally:
        server.STATE.session_cache.clear()
        server.STATE.session_cache.update(original_session_cache)
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_webrtc_stale_preopen_session_times_out_and_cleans_up():
    webrtc = server.WebRTCManager()
    webrtc.session_establish_timeout_seconds = 0.05

    class DummyPeerConnection:
        def __init__(self):
            self.close_calls = 0
            self.connectionState = "connecting"
            self.iceConnectionState = "checking"

        async def close(self):
            self.close_calls += 1

    class DummyAudioSink:
        def __init__(self):
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1

    peer = DummyPeerConnection()
    sink = DummyAudioSink()
    webrtc.session_peers["session-timeout"] = peer
    webrtc.audio_sinks["session-timeout"] = sink

    webrtc._arm_session_establishment_timeout("session-timeout", peer, label="autopair")

    await asyncio.sleep(0.18)

    assert peer.close_calls == 1
    assert sink.stop_calls == 1
    assert "session-timeout" not in webrtc.audio_sinks
    assert "session-timeout" not in webrtc.session_peers
    assert "session-timeout" not in webrtc.session_establishment_tasks


@pytest.mark.asyncio
async def test_webrtc_shutdown_cancels_reconnect_survivable_tasks():
    webrtc = server.WebRTCManager()
    task = webrtc._track_session_task(
        "session-survivable",
        asyncio.sleep(3600),
        "chat",
        survive_disconnect=True,
    )

    original_audio_managers = dict(server.STATE.audio_managers)
    try:
        await webrtc.shutdown()
        assert task.done()
        assert not webrtc.session_reconnect_survivable_tasks
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_webrtc_disconnected_state_waits_for_grace_before_cleanup():
    webrtc = server.WebRTCManager()
    webrtc.session_disconnect_grace_seconds = 0.05

    class DummyPeerConnection:
        def __init__(self):
            self.close_calls = 0
            self.connectionState = "disconnected"
            self.iceConnectionState = "disconnected"

        async def close(self):
            self.close_calls += 1

    peer = DummyPeerConnection()
    webrtc.session_peers["session-grace"] = peer

    webrtc._handle_transport_disconnect_state(
        "session-grace",
        peer,
        source="peer_connection",
        state="disconnected",
    )

    assert "session-grace" in webrtc.session_peers
    await asyncio.sleep(0.15)

    assert peer.close_calls == 1
    assert "session-grace" not in webrtc.session_peers
    assert "session-grace" not in webrtc.session_disconnect_grace_tasks


@pytest.mark.asyncio
async def test_webrtc_disconnect_grace_cancels_when_connection_recovers():
    webrtc = server.WebRTCManager()
    webrtc.session_disconnect_grace_seconds = 0.1

    class DummyPeerConnection:
        def __init__(self):
            self.close_calls = 0
            self.connectionState = "disconnected"
            self.iceConnectionState = "disconnected"

        async def close(self):
            self.close_calls += 1

    peer = DummyPeerConnection()
    webrtc.session_peers["session-recover"] = peer

    webrtc._handle_transport_disconnect_state(
        "session-recover",
        peer,
        source="peer_connection",
        state="disconnected",
    )

    await asyncio.sleep(0.02)
    peer.connectionState = "connected"
    peer.iceConnectionState = "connected"
    webrtc._handle_transport_disconnect_state(
        "session-recover",
        peer,
        source="peer_connection",
        state="connected",
    )

    await asyncio.sleep(0.15)

    assert peer.close_calls == 0
    assert "session-recover" in webrtc.session_peers
    assert "session-recover" not in webrtc.session_disconnect_grace_tasks


@pytest.mark.asyncio
async def test_voice_call_control_flushes_stt_when_android_mutes(monkeypatch):
    webrtc = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)

    class DummyAudioManager:
        def __init__(self):
            self.flush_calls = []

        def flush_utterance(self, *, source: str, timestamp_ms=None):
            self.flush_calls.append((source, timestamp_ms))
            return True

    try:
        audio_manager = DummyAudioManager()
        server.STATE.audio_managers["session-1"] = audio_manager
        monkeypatch.setattr(server, "VOICE_CALL_MUTE_FLUSH_GRACE_SECONDS", 0.0)

        message = DataChannelMessage(
            header=MessageHeader(
                message_id="msg-voice-1",
                message_type=MessageType.VOICE_CALL_CONTROL,
                timestamp=0.0,
                session_id="session-1",
                user_id="client",
            ),
            payload={"muted": True, "platform": "android", "timestamp_ms": 1234567890},
        )

        await webrtc._handle_voice_call_control_message(message)

        assert audio_manager.flush_calls == [("android:session-1", 1234567890)]
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_voice_call_control_ignores_unmute_events(monkeypatch):
    webrtc = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)

    class DummyAudioManager:
        def __init__(self):
            self.flush_calls = 0

        def flush_utterance(self, *, source: str, timestamp_ms=None):
            self.flush_calls += 1
            return True

    try:
        audio_manager = DummyAudioManager()
        server.STATE.audio_managers["session-1"] = audio_manager
        monkeypatch.setattr(server, "VOICE_CALL_MUTE_FLUSH_GRACE_SECONDS", 0.0)

        message = DataChannelMessage(
            header=MessageHeader(
                message_id="msg-voice-2",
                message_type=MessageType.VOICE_CALL_CONTROL,
                timestamp=0.0,
                session_id="session-1",
                user_id="client",
            ),
            payload={"muted": False, "platform": "android", "timestamp_ms": 1234567891},
        )

        await webrtc._handle_voice_call_control_message(message)

        assert audio_manager.flush_calls == 0
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


class _WuiftDummyAudioManager:
    def __init__(self):
        self.hold_calls = []
        self.flush_calls = []

    def set_segmentation_hold(self, active, *, source):
        self.hold_calls.append((active, source))
        return True

    def flush_utterance(self, *, source, timestamp_ms=None):
        self.flush_calls.append((source, timestamp_ms))
        return True


def _wuift_control_message(message_id, payload):
    return DataChannelMessage(
        header=MessageHeader(
            message_id=message_id,
            message_type=MessageType.VOICE_CALL_CONTROL,
            timestamp=0.0,
            session_id="session-1",
            user_id="client",
        ),
        payload=payload,
    )


@pytest.mark.asyncio
async def test_voice_call_control_wuift_state_engages_and_releases_hold():
    webrtc = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)
    original_config = server.STATE.config

    try:
        server.STATE.config = server._default_config()
        audio_manager = _WuiftDummyAudioManager()
        server.STATE.audio_managers["session-1"] = audio_manager

        await webrtc._handle_voice_call_control_message(
            _wuift_control_message(
                "msg-wuift-1",
                {"event": "wuift_state", "active": True, "platform": "ios"},
            )
        )
        await webrtc._handle_voice_call_control_message(
            _wuift_control_message(
                "msg-wuift-2",
                {"event": "wuift_state", "active": False, "platform": "ios"},
            )
        )

        assert audio_manager.hold_calls == [
            (True, "ios:session-1"),
            (False, "ios:session-1"),
        ]
        assert webrtc.wuift_hold_by_session["session-1"] is False
        assert audio_manager.flush_calls == []
    finally:
        server.STATE.config = original_config
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_voice_call_control_wuift_trigger_flushes_immediately():
    webrtc = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)
    original_config = server.STATE.config

    try:
        server.STATE.config = server._default_config()
        audio_manager = _WuiftDummyAudioManager()
        server.STATE.audio_managers["session-1"] = audio_manager

        await webrtc._handle_voice_call_control_message(
            _wuift_control_message(
                "msg-wuift-3",
                {"event": "wuift_trigger", "platform": "android", "timestamp_ms": 42},
            )
        )

        assert audio_manager.flush_calls == [("wuift:android:session-1", 42)]
        assert audio_manager.hold_calls == []
    finally:
        server.STATE.config = original_config
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_voice_call_control_wuift_events_rejected_when_disabled():
    webrtc = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)
    original_config = server.STATE.config

    try:
        cfg = server._default_config()
        cfg["video_call"]["wuift_enabled"] = False
        server.STATE.config = cfg
        audio_manager = _WuiftDummyAudioManager()
        server.STATE.audio_managers["session-1"] = audio_manager

        await webrtc._handle_voice_call_control_message(
            _wuift_control_message(
                "msg-wuift-4",
                {"event": "wuift_state", "active": True, "platform": "ios"},
            )
        )
        await webrtc._handle_voice_call_control_message(
            _wuift_control_message(
                "msg-wuift-5",
                {"event": "wuift_trigger", "platform": "ios", "timestamp_ms": 7},
            )
        )

        assert audio_manager.hold_calls == []
        assert audio_manager.flush_calls == []
        assert "session-1" not in webrtc.wuift_hold_by_session
    finally:
        server.STATE.config = original_config
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_apply_video_call_settings_releases_active_wuift_holds_when_disabled():
    """Flipping the admin WUIFT toggle off mid-call releases every engaged hold
    so normal VAD segmentation resumes."""
    webrtc = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)
    original_config = server.STATE.config

    try:
        cfg = server._default_config()
        cfg["video_call"]["wuift_enabled"] = False
        server.STATE.config = cfg
        audio_manager = _WuiftDummyAudioManager()
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers["session-1"] = audio_manager
        webrtc.wuift_hold_by_session["session-1"] = True
        # A session whose hold was already off must not be re-released.
        webrtc.wuift_hold_by_session["session-2"] = False

        await webrtc.apply_video_call_settings()

        assert audio_manager.hold_calls == [(False, "admin:session-1:wuift_disabled")]
        assert webrtc.wuift_hold_by_session["session-1"] is False
        assert webrtc.wuift_hold_by_session["session-2"] is False
    finally:
        server.STATE.config = original_config
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_publish_voice_call_status_buffers_and_sends_control_message():
    webrtc = server.WebRTCManager()

    class DummyDataChannelManager:
        def __init__(self):
            self.sent_messages = []

        async def send_message(self, message):
            self.sent_messages.append(message)
            return True

    await webrtc._publish_voice_call_status(
        "session-voice",
        {"state": "warming", "detail": "Preparing voice pipeline on server..."},
    )

    assert webrtc.voice_call_status_by_session["session-voice"]["state"] == "warming"

    datachannel_manager = DummyDataChannelManager()
    webrtc.datachannel_managers["session-voice"] = datachannel_manager

    await webrtc._publish_voice_call_status(
        "session-voice",
        {"state": "ready", "detail": "Voice pipeline ready."},
    )

    assert len(datachannel_manager.sent_messages) == 1
    sent_message = datachannel_manager.sent_messages[0]
    assert isinstance(sent_message, DataChannelMessage)
    assert sent_message.header.message_type == MessageType.VOICE_CALL_CONTROL
    assert sent_message.payload["event"] == "readiness"
    assert sent_message.payload["state"] == "ready"


def test_audio_manager_readiness_status_prefers_live_snapshot():
    webrtc = server.WebRTCManager()
    original_audio_managers = dict(server.STATE.audio_managers)

    class DummyAudioManager:
        def get_readiness_status(self):
            return {
                "event": "readiness",
                "state": "ready",
                "detail": "Voice pipeline ready.",
                "timestamp_ms": 123,
                "platform": "server",
            }

    try:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers["session-voice"] = DummyAudioManager()

        status = webrtc._get_audio_manager_readiness_status("session-voice")

        assert status == {
            "event": "readiness",
            "state": "ready",
            "detail": "Voice pipeline ready.",
            "timestamp_ms": 123,
            "platform": "server",
        }
    finally:
        server.STATE.audio_managers.clear()
        server.STATE.audio_managers.update(original_audio_managers)


@pytest.mark.asyncio
async def test_conversation_context_snapshot_skips_stale_adk_session_replay(monkeypatch):
    class DummySessionManager:
        def get_mapped_session_id(self, external_session_id, user_id):
            assert external_session_id == "session::direct:device-1"
            assert user_id == "user::direct:device-1"
            return "ai-session-stale"

        async def get_user_session(self, user_id, session_id):
            assert user_id == "user::direct:device-1"
            assert session_id == "ai-session-stale"
            return None

        def get_session_context_usage_snapshot(self, session_id, user_id):
            raise AssertionError("stale sessions should not replay persisted context")

    monkeypatch.setattr(server, "_get_conversation_session_manager", lambda: DummySessionManager())

    identity = type(
        "Identity",
        (),
        {
            "canonical_session_id": "session::direct:device-1",
            "canonical_user_id": "user::direct:device-1",
            "thread_id": None,
        },
    )()

    snapshot = await server._conversation_context_usage_snapshot(identity)

    assert snapshot["available"] is False
    assert snapshot["summary_text"] == ""


@pytest.mark.asyncio
async def test_conversation_context_snapshot_replays_when_adk_session_exists(monkeypatch):
    persisted_snapshot = {
        "available": True,
        "summary_text": "Ctx 6.1k/8k",
        "alert_level": "warning",
        "prompt_tokens": 6100,
        "context_window": 8192,
    }

    class DummySessionManager:
        def get_mapped_session_id(self, external_session_id, user_id):
            return "ai-session-live"

        async def get_user_session(self, user_id, session_id):
            return {"message_count": 10}

        def get_session_context_usage_snapshot(self, session_id, user_id):
            assert session_id == "ai-session-live"
            assert user_id == "user::direct:device-1"
            return dict(persisted_snapshot)

    monkeypatch.setattr(server, "_get_conversation_session_manager", lambda: DummySessionManager())

    identity = type(
        "Identity",
        (),
        {
            "canonical_session_id": "session::direct:device-1",
            "canonical_user_id": "user::direct:device-1",
            "thread_id": 3,
        },
    )()

    snapshot = await server._conversation_context_usage_snapshot(identity)

    assert snapshot["available"] is True
    assert snapshot["summary_text"] == "Ctx 6.1k/8k"
    assert snapshot["conversation_session_id"] == "session::direct:device-1"
    assert snapshot["conversation_thread_id"] == 3
    assert snapshot["ai_agent_session_id"] == "ai-session-live"


@pytest.mark.asyncio
async def test_webrtc_shutdown_cancels_lingering_turn_teardown_tasks():
    webrtc = server.WebRTCManager()

    async def turn_send_data():
        await asyncio.sleep(3600)

    async def unrelated_task():
        await asyncio.sleep(3600)

    turn_send_data.__qualname__ = "TurnClientMixin.send_data"

    lingering_task = asyncio.create_task(turn_send_data())
    unrelated = asyncio.create_task(unrelated_task())

    try:
        cancelled = await webrtc._cancel_lingering_webrtc_teardown_tasks(timeout_seconds=0.05)

        assert cancelled == 1
        assert lingering_task.done()
        assert not unrelated.done()
    finally:
        for task in (lingering_task, unrelated):
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass


def test_datachannel_keepalive_tracks_any_inbound_activity_not_just_pong():
    """Regression guard for a false-positive "connection may be dead" cleanup.

    A dedicated keepalive PONG can queue for a long time behind a large bulk
    HTTP-proxied transfer (e.g. an audio file streamed in small chunks) that
    shares the same ordered/reliable datachannel, even though that transfer's
    own CHUNK_ACK traffic is itself unambiguous proof the connection is alive
    and round-tripping. Gating liveness on PONG alone triggered an unwarranted
    cleanup mid-transfer on a perfectly healthy connection. The fix tracks a
    broader last_activity_time (updated on any inbound message) and checks
    that instead of last_pong_time in the dead-connection check.
    """
    handler_body = inspect.getsource(server.WebRTCManager._create_datachannel_handler).replace("_runtime.", "")

    assert "last_activity_time = time.time() if enable_keepalive else None" in handler_body
    assert "nonlocal last_pong_time, last_activity_time" in handler_body

    # The dead-connection check must key off last_activity_time, not the
    # narrower last_pong_time (which only a dedicated PONG updates).
    # The timeout is configurable (and defaults higher than the original 60 s)
    # so it cannot undercut the widened ICE consent budget - see
    # tests/server/transport/test_ice_consent_tolerance.py.
    dead_check_start = handler_body.index("Sent keepalive ping to")
    dead_check = handler_body[dead_check_start:dead_check_start + 1200]
    assert "idle_timeout = _datachannel_idle_timeout_seconds()" in dead_check
    assert "time.time() - last_activity_time > idle_timeout" in dead_check
    assert "last_pong_time >" not in dead_check

    # Every inbound datachannel message must refresh last_activity_time,
    # unconditionally and before any type-specific dispatch/parsing.
    on_message_start = handler_body.index('@channel.on("message")')
    on_message_body = handler_body[on_message_start:on_message_start + 1400]
    assert "nonlocal last_pong_time, last_activity_time, datachannel_manager" in on_message_body
    assert "last_activity_time = time.time()" in on_message_body
    assert on_message_body.index("last_activity_time = time.time()") < on_message_body.index("datachannel_manager is not None")
