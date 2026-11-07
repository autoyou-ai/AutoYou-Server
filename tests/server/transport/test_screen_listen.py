# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-U-000000000000000000000000-55ed1739ce2fc4d1b7dad8cc

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import asyncio
import queue
import struct
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import server
from shared import video_call_manager
from shared.screen_listen import ScreenListenMixer, mix_pcm

__debug_trace_u__ = "AUTOYOU-PROVENANCE-U-000000000000000000000000-55ed1739ce2fc4d1b7dad8cc"


def test_local_screen_audio_mix_clips_without_wraparound():
    loud = struct.pack("<h", 30000) * 320
    assert struct.unpack("<h", mix_pcm([loud, loud])[:2])[0] == 32767
    opposite = struct.pack("<h", -30000) * 320
    assert struct.unpack("<h", mix_pcm([loud, loud, opposite])[:2])[0] == 30000


def test_listen_output_failure_stays_off(monkeypatch):
    mixer = ScreenListenMixer()
    def fail():
        raise OSError("synthetic output failure")
    monkeypatch.setattr(mixer, "_start", fail)
    with pytest.raises(OSError):
        mixer.configure("all")
    assert mixer.mode == "off"
    assert "synthetic output failure" in mixer.snapshot()["error"]


@pytest.mark.parametrize("output_rate", [16000, 44100, 48000])
@pytest.mark.parametrize("channels", [1, 2])
def test_listen_output_mixes_all_and_filters_selected_without_hardware(monkeypatch, output_rate, channels):
    output = queue.Queue()
    entered, release = threading.Event(), threading.Event()

    class Stream:
        def write(self, frame):
            entered.set()
            release.wait(timeout=2)
            output.put(frame)
            time.sleep(0.02)

        def stop_stream(self):
            pass

        def close(self):
            pass

    class Device:
        def get_default_output_device_info(self):
            return {"defaultSampleRate": output_rate, "name": "Synthetic speaker"}

        def open(self, **kwargs):
            assert kwargs["output"]
            if kwargs["rate"] != output_rate or kwargs["channels"] != channels:
                raise OSError("synthetic unsupported output rate")
            return Stream()

        def terminate(self):
            pass

    module = SimpleNamespace(paInt16=8, PyAudio=Device)
    monkeypatch.setattr(video_call_manager, "_load_pyaudio_module", lambda: module)
    monkeypatch.setattr(video_call_manager, "_PYAUDIO_RUNTIME", video_call_manager._PyAudioRuntime())
    mixer = ScreenListenMixer()

    def next_sample(expected):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            frame = output.get(timeout=0.2)
            sample = struct.unpack("<h", frame[:2])[0]
            if sample == expected:
                assert len(frame) >= (output_rate * 2 // 50 - 128) * channels
                if channels == 2:
                    assert struct.unpack("<hh", frame[:4]) == (expected, expected)
                return
        pytest.fail(f"Listen output never contained {expected}")

    try:
        mixer.configure("all")
        assert entered.wait(timeout=1)
        mixer.feed("synthetic-a", struct.pack("<h", 1000) * 320)
        mixer.feed("synthetic-b", struct.pack("<h", 2000) * 320)
        release.set()
        next_sample(3000)
        assert mixer.snapshot()["output_device"] == "Synthetic speaker"
        assert set(mixer.snapshot()["receiving"]) == {"synthetic-a", "synthetic-b"}
        mixer.configure("selected", {"synthetic-a"})
        mixer.feed("synthetic-b", struct.pack("<h", 2000) * 320)
        mixer.feed("synthetic-a", struct.pack("<h", 1000) * 320)
        next_sample(1000)
        assert mixer.snapshot()["receiving"] == ["synthetic-a"]
    finally:
        release.set()
        mixer.close()


def test_screen_audio_and_choices_never_enter_ai(monkeypatch):
    manager = server.WebRTCManager()
    session_id = "synthetic-screen-session"
    transport_id = "synthetic-transport-session"
    manager._voice_dc_session_id[transport_id] = session_id
    monkeypatch.setattr(manager, "client_display_name_snapshot", lambda sid: {"client_display_name": "Student"})
    manager._set_voice_call_client_active(session_id, True)
    manager._set_screen_session(session_id, "interactive")
    heard = []
    monkeypatch.setattr(manager.screen_listen_mixer, "feed", lambda sid, chunk: heard.append((sid, chunk)))
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Screen audio or controls reached AI")

    ai = SimpleNamespace(process_audio_chunk=forbidden, stop_speaking=forbidden,
                         set_segmentation_hold=forbidden, flush_utterance=forbidden)
    manager._handle_inbound_voice_audio_chunk(transport_id, ai, b"\0\0")
    assert heard == []
    manager.screen_sessions[session_id]["muted"] = False
    manager._handle_inbound_voice_audio_chunk(transport_id, ai, b"\1\0")
    assert heard == [(session_id, b"\1\0")]
    monkeypatch.setattr(server.STATE, "audio_managers", {session_id: ai})
    message = SimpleNamespace(header=SimpleNamespace(session_id=session_id), payload={})
    for event in ("stop_tts", "wuift_state", "wuift_trigger"):
        message.payload = {"event": event, "active": True}
        asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    message.payload = {"event": "screen_input", "kind": "choice", "value": "D", "phase": "press"}
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    assert manager.screen_listen_snapshot()["inputs"] == []
    message.payload = {"event": "screen_input", "kind": "layout", "value": "choices", "phase": "set"}
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    message.payload = {"event": "screen_input", "kind": "choice", "value": "D", "phase": "press"}
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    assert manager.screen_listen_snapshot()["inputs"][-1]["value"] == "D"
    assert manager.screen_listen_snapshot()["participants"][0]["layout"] == "choices"
    # Remote Desktop ("interactive") asks for screen control the same way a call
    # does; the control handler applies this computer's control settings.
    desktop_control = AsyncMock()
    # from __debug_trace_u__ import autoyou_provenance_u_000000000000000000000000_55ed1739ce2fc4d1b7dad8cc
    monkeypatch.setattr(manager, "_handle_remote_desktop_control", desktop_control)
    message.payload = {"event": "remote_desktop_control", "action": "start"}
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    desktop_control.assert_awaited_once()
    game_input = AsyncMock(side_effect=AssertionError("Screen session sent game input"))
    monkeypatch.setattr(manager, "_handle_game_input", game_input)
    message.payload = {"event": "game_input", "control_id": "synthetic", "input_type": "heartbeat"}
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    game_input.assert_not_awaited()
    manager._set_screen_session(session_id, "watch")
    # Watching is view-only.
    message.payload = {"event": "remote_desktop_control", "action": "start"}
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    desktop_control.assert_awaited_once()
    manager._handle_inbound_voice_audio_chunk(transport_id, ai, b"\2\0")
    assert len(heard) == 1
    message.payload = {"event": "call_state", "active": False, "platform": "ios"}
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    assert manager.screen_listen_snapshot()["participants"] == []
    manager._handle_inbound_voice_audio_chunk(transport_id, ai, b"\3\0")
    assert len(heard) == 1


def test_meet_without_voice_calls_shares_one_host_capture(monkeypatch):
    from aiortc import AudioStreamTrack

    cfg = server._default_config()
    cfg["video_call"]["audio_enabled"] = False
    monkeypatch.setattr(server.STATE, "config", cfg)
    monkeypatch.setattr(server, "_get_outbound_video_available", lambda **_kwargs: True)

    class Sender:
        track = None

        def replaceTrack(self, track):
            self.track = track

    async def run():
        manager = server.WebRTCManager()
        source = AudioStreamTrack()
        capture_ids = []
        monkeypatch.setattr(server, "_create_configured_outbound_audio_track", lambda **kwargs:
                            capture_ids.append(kwargs["session_id"]) or source)

        async def set_meet(session_id, active):
            payload = {"event": "call_state", "active": active, "screen_mode": "interactive"}
            await manager._handle_voice_call_control_message(SimpleNamespace(payload=payload), trusted_session_id=session_id)

        first, second = "synthetic-meet-ios", "synthetic-meet-android"
        manager.audio_transceivers[first] = SimpleNamespace(sender=Sender(), direction="inactive")
        manager.audio_transceivers[second] = SimpleNamespace(sender=Sender(), direction="inactive")
        await set_meet(first, True)
        await set_meet(second, True)
        first_track = manager.audio_transceivers[first].sender.track
        second_track = manager.audio_transceivers[second].sender.track
        await manager._handle_voice_call_control_message(SimpleNamespace(payload={
            "event": "background_audio_state", "active": False, "platform": "ios",
        }), trusted_session_id=first)
        assert manager.audio_transceivers[first].sender.track is first_track
        assert first_track is not second_track
        assert not manager._voice_call_client_active_for_session(first)
        assert not manager._voice_call_client_active_for_session(second)
        assert {row["id"] for row in manager.screen_listen_snapshot()["participants"]} == {first, second}
        assert len(capture_ids) == 1
        first_frame, second_frame = await asyncio.wait_for(asyncio.gather(first_track.recv(), second_track.recv()), 1)
        assert first_frame is second_frame
        await set_meet(first, False)
        assert manager.audio_transceivers[first].sender.track is None
        assert source.readyState == "live"
        assert (await asyncio.wait_for(second_track.recv(), 1)).samples > 0
        await set_meet(second, False)
        assert manager._screen_audio_source is None
        assert source.readyState == "ended"

    asyncio.run(run())


def test_meet_approval_and_multiple_clients_keep_drag_and_key_ownership(monkeypatch):
    cfg = server._default_config()
    cfg["video_call"]["audio_enabled"] = False
    cfg["video_call"]["remote_desktop"].update(control_enabled=True, meet_auto_approve=False)
    monkeypatch.setattr(server.STATE, "config", cfg)
    monkeypatch.setattr(server, "_get_remote_desktop_control_available", lambda **_kwargs: True)
    monkeypatch.setattr(server, "remote_desktop_cursor_position", lambda: (400, 300))
    pointer, keyboard, released = [], [], []
    monkeypatch.setattr(server, "execute_remote_desktop_input", lambda payload, **_kwargs: pointer.append(payload) or True)
    monkeypatch.setattr(server, "execute_remote_desktop_keyboard", lambda payload: keyboard.append(payload) or True)
    monkeypatch.setattr(server, "release_remote_desktop_inputs", lambda buttons=(), **kwargs:
                        released.append((set(buttons), kwargs)))

    async def run():
        manager = server.WebRTCManager()
        monkeypatch.setattr(manager, "_probe_remote_desktop_input_backend", AsyncMock(return_value=True))
        track = SimpleNamespace(
            is_enabled=True,
            remote_desktop_mapping=lambda: {
                "content_rect": {"x": 0, "y": 0, "width": 1, "height": 1},
                "monitor_bounds": {"left": 0, "top": 0, "width": 801, "height": 601},
            },
            map_output_point_to_desktop=lambda x, y: (round(x * 800), round(y * 600)),
        )
        first, second = "synthetic-ios", "synthetic-android"

        async def send(session_id, event, **fields):
            await manager._handle_voice_call_control_message(SimpleNamespace(payload={
                "event": event, "control_id": session_id, "source": "autoyou_lite",
                "platform": "ios" if session_id == first else "android", **fields,
            }), trusted_session_id=session_id)

        for session_id in (first, second):
            manager._set_screen_session(session_id, "interactive")
            manager.desktop_video_tracks[session_id] = track
            manager.datachannel_managers[session_id] = SimpleNamespace(send_message=AsyncMock(return_value=True))
            await send(session_id, "remote_desktop_control", action="start", fullscreen=True, touch_mode="relative")
        assert not manager.remote_desktop_control_leases_by_session
        assert {row["control_state"] for row in manager.screen_listen_snapshot()["participants"]} == {"requested"}
        await send(first, "remote_desktop_input", input_type="button", button="left", phase="click")
        assert pointer == []
        assert not await manager.review_screen_control(first, "synthetic-stale", "approve")
        assert await manager.review_screen_control(first, first, "approve")
        assert await manager.review_screen_control(second, second, "approve")
        assert manager.datachannel_managers[first].send_message.await_args.args[0].payload["cursor"] == {"x": 0.5, "y": 0.5}

        await send(first, "remote_desktop_input", input_type="button", button="left", phase="down")
        await send(first, "remote_desktop_input", input_type="button", button="left", phase="click")
        await send(second, "remote_desktop_input", input_type="move", coordinate_mode="relative", dx=0.1, dy=0)
        await send(first, "remote_desktop_input", input_type="move", coordinate_mode="relative", dx=0.1, dy=0)
        assert [item["control_id"] for item in pointer] == [first, first]
        await send(first, "remote_desktop_keyboard", action="key", key="control", phase="down")
        await send(second, "remote_desktop_keyboard", action="key", key="w", phase="press")
        await send(second, "remote_desktop_keyboard", action="key", key="ctrl", phase="down")
        assert len(keyboard) == 1
        assert await manager.review_screen_control(first, first, "revoke")
        assert released[-1] == ({"left"}, {"release_modifiers": False})
        await send(second, "remote_desktop_input", input_type="button", button="left", phase="double_click")
        assert pointer[-1]["control_id"] == second and "x" not in pointer[-1]
        assert await manager.review_screen_control(second, second, "revoke")
        assert released[-1][1]["held_keys"] == {"ctrl"}

    asyncio.run(run())


def test_interact_control_review_requires_the_local_admin(monkeypatch):
    from fastapi.testclient import TestClient

    review = AsyncMock(return_value=True)
    monkeypatch.setattr(server.WEBRTC, "review_screen_control", review)
    monkeypatch.setattr(server, "_require_api_login", lambda request: None)
    monkeypatch.setattr(server, "_is_loopback_client_host", lambda host: True)
    endpoint = "/api/screen-listen/control"
    decision = {"session_id": "synthetic-meet", "control_id": "synthetic-input", "action": "approve"}
    with TestClient(server.admin_app, base_url="http://127.0.0.1:8001") as client:
        assert client.post(endpoint, json=decision, headers={"X-AutoYou-Tunnel-Client-Ip": "203.0.113.9"}).status_code == 403
        assert client.post(endpoint, json={**decision, "action": "invalid"}).status_code == 400
        review.assert_not_awaited()
        assert client.post(endpoint, json=decision).status_code == 200
        review.assert_awaited_once_with("synthetic-meet", "synthetic-input", "approve")
        review.return_value = False
        assert client.post(endpoint, json=decision).status_code == 409


def _synthetic_camera(manager, monkeypatch):
    """One phone camera sink registered under two session ids, like a real call."""
    registry = video_call_manager.LatestVideoFrameRegistry(max_age_seconds=30)
    monkeypatch.setattr(server, "VIDEO_FRAME_REGISTRY", registry)
    sink = SimpleNamespace(session_ids={"synthetic-transport", "synthetic-call"})
    manager.video_sinks["synthetic-call"] = sink
    manager.video_sinks["synthetic-transport"] = sink
    monkeypatch.setattr(manager, "client_display_name_snapshot", lambda sid: {"client_display_name": "Student"})
    return registry


def test_interact_lists_each_camera_once_with_frame_details_only(monkeypatch):
    manager = server.WebRTCManager()
    assert manager.live_video_feeds() == []
    registry = _synthetic_camera(manager, monkeypatch)
    assert manager.live_video_feeds() == [], "a camera with no frame yet is not listed"
    for session_id in ("synthetic-call", "synthetic-transport"):
        registry.publish_jpeg(session_id=session_id, jpeg_bytes=b"\xff\xd8synthetic", width=640, height=480)
    feeds = manager.live_video_feeds()
    assert len(feeds) == 1
    assert feeds[0]["id"] == "synthetic-call" and feeds[0]["name"] == "Student"
    assert (feeds[0]["width"], feeds[0]["height"]) == (640, 480)
    assert "jpeg_bytes" not in feeds[0], "the list carries no pictures"
    newest = manager.live_video_frame("synthetic-transport")
    assert newest.sequence == feeds[0]["sequence"] and newest.jpeg_bytes == b"\xff\xd8synthetic"
    assert manager.live_video_frame("synthetic-other") is None


def test_interact_frame_route_serves_the_call_frame_on_this_computer_only(monkeypatch):
    from fastapi.testclient import TestClient

    registry = _synthetic_camera(server.WEBRTC, monkeypatch)
    monkeypatch.setattr(server, "_require_api_login", lambda request: None)
    monkeypatch.setattr(server, "_is_loopback_client_host", lambda host: True)
    try:
        frame = registry.publish_jpeg(session_id="synthetic-call", jpeg_bytes=b"\xff\xd8synthetic", width=320, height=240)
        with TestClient(server.admin_app, base_url="http://127.0.0.1:8001") as client:
            listing = client.get("/api/screen-listen").json()
            assert [feed["id"] for feed in listing["video_feeds"]] == ["synthetic-call"]
            picture = client.get("/api/screen-listen/frame.jpg", params={"id": "synthetic-call"})
            assert picture.status_code == 200 and picture.content == b"\xff\xd8synthetic"
            assert picture.headers["content-type"] == "image/jpeg"
            assert picture.headers["cache-control"].startswith("no-store")
            assert picture.headers["x-autoyou-frame-sequence"] == str(frame.sequence)
            # The page already shows this picture, so nothing is sent again.
            unchanged = client.get("/api/screen-listen/frame.jpg", params={"id": "synthetic-call", "after": frame.sequence})
            assert unchanged.status_code == 204 and unchanged.content == b""
            assert client.get("/api/screen-listen/frame.jpg", params={"id": "synthetic-missing"}).status_code == 404
            remote = client.get("/api/screen-listen/frame.jpg", params={"id": "synthetic-call"},
                                headers={"X-AutoYou-Tunnel-Client-Ip": "203.0.113.9"})
            assert remote.status_code == 403
    finally:
        server.WEBRTC.video_sinks.pop("synthetic-call", None)
        server.WEBRTC.video_sinks.pop("synthetic-transport", None)
