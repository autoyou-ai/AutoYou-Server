"""Game frames stay on the authenticated WebRTC lease and local engine stream."""

import asyncio
import threading
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import server
from core_server.webrtc_engine import _HOSTED_GAME_LOOP
from shared import video_call_manager
from shared.game_input import GameInputHub, normalize_game_input


def test_legacy_canceled_mouse_dispatch_joins_then_releases_possible_hold(monkeypatch):
    entered, resume = threading.Event(), threading.Event()
    releases = []
    def apply(frame, *, track):
        entered.set(); assert resume.wait(3); return True
    monkeypatch.setattr(server, "execute_remote_desktop_input", apply)
    monkeypatch.setattr(server, "release_remote_desktop_inputs", lambda buttons=(), **kwargs:
        releases.append((set(buttons), set(kwargs.get("held_keys", ())))))
    async def run():
        engine = server.WebRTCManager()
        track = SimpleNamespace(remote_desktop_mapping=lambda: {"content_rect": [0, 0, 100, 100]})
        lease = engine._store_remote_desktop_control_lease("synthetic-legacy-controller",
            control_id="synthetic-control", touch_mode="direct", track=track)
        monkeypatch.setattr(engine, "_remote_desktop_control_state", lambda *args: (lease, ""))
        job = asyncio.create_task(engine._apply_owned_remote_input("synthetic-legacy-controller", lease,
            dict(input_type="button", button="left", phase="down")))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert lease["held_buttons"] == {"left"}
            job.cancel(); await asyncio.sleep(0)
            assert not job.done() and releases == []
            resume.set()
            with pytest.raises(asyncio.CancelledError): await job
            assert releases == [({"left"}, set())]
            assert lease["physical_input_task"].done() and not engine.remote_desktop_control_leases_by_session
        finally:
            resume.set(); await asyncio.gather(job, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("keyboard", [False, True])
def test_legacy_failed_down_or_key_pulse_retains_possible_hold_until_end(monkeypatch, keyboard):
    monkeypatch.setattr(server, "execute_remote_desktop_input", lambda *args, **kwargs: False)
    monkeypatch.setattr(server, "execute_remote_desktop_keyboard", lambda *args, **kwargs: False)
    releases = []
    monkeypatch.setattr(server, "release_remote_desktop_inputs", lambda buttons=(), **kwargs:
        releases.append((set(buttons), set(kwargs.get("held_keys", ())))))
    async def run():
        engine = server.WebRTCManager()
        track = SimpleNamespace(remote_desktop_mapping=lambda: {"content_rect": [0, 0, 100, 100]})
        lease = engine._store_remote_desktop_control_lease("synthetic-legacy-controller",
            control_id="synthetic-control", touch_mode="direct", track=track)
        monkeypatch.setattr(engine, "_remote_desktop_control_state", lambda *args: (lease, ""))
        value = dict(action="key", key="shift", phase="press") if keyboard else dict(input_type="button", button="left", phase="down")
        assert not await engine._apply_owned_remote_input("synthetic-legacy-controller", lease, value, keyboard=keyboard)
        expected = (set(), {"shift"}) if keyboard else ({"left"}, set())
        assert (lease["held_buttons"], lease["held_keys"]) == expected
        await engine._release_remote_desktop_control("synthetic-legacy-controller")
        assert releases == [expected]
    asyncio.run(run())


def test_legacy_input_end_joins_sdk_release_before_native_admission(monkeypatch):
    from shared.iroh_input import native_host_input_busy, NativeInputAuthority, NativeInputLease
    from shared.session_transport import SessionDenied
    entered, resume = threading.Event(), threading.Event()
    def release(buttons, **kwargs):
        assert buttons == {"left"}
        entered.set(); assert resume.wait(3)
    monkeypatch.setattr(server, "release_remote_desktop_inputs", release)
    async def run():
        engine = server.WebRTCManager()
        track = SimpleNamespace(remote_desktop_mapping=lambda: {"content_rect": [0, 0, 100, 100]})
        lease = engine._store_remote_desktop_control_lease("synthetic-legacy-controller",
            control_id="synthetic-control", touch_mode="direct", track=track)
        lease["held_buttons"].add("left")
        stopping = asyncio.create_task(engine._release_remote_desktop_control("synthetic-legacy-controller"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert native_host_input_busy() and lease["held_buttons"] == {"left"}
            with pytest.raises(SessionDenied, match="already has an owner"):
                NativeInputLease(authority=NativeInputAuthority(None, "synthetic-native", "synthetic-lease", 10000),
                    track=None, port=SimpleNamespace(), check_current=lambda: None, now_ms=lambda: 1,
                    on_failure=lambda error: None)
            stopping.cancel(); await asyncio.sleep(0)
            assert not stopping.done() and native_host_input_busy()
            resume.set()
            with pytest.raises(asyncio.CancelledError): await stopping
            assert not native_host_input_busy() and lease["held_buttons"] == set()
        finally:
            resume.set(); await asyncio.gather(stopping, return_exceptions=True)
    asyncio.run(run())


def test_legacy_input_failed_cleanup_keeps_native_admission_fenced(tmp_path, monkeypatch):
    from shared.iroh_input import native_host_input_busy
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    failure = RuntimeError("synthetic physical release failure")
    calls = []
    def release(buttons, **kwargs):
        calls.append(buttons); raise failure
    monkeypatch.setattr(server, "release_remote_desktop_inputs", release)
    async def run():
        engine = server.WebRTCManager()
        track = SimpleNamespace(remote_desktop_mapping=lambda: {"content_rect": [0, 0, 100, 100]})
        lease = engine._store_remote_desktop_control_lease("synthetic-legacy-controller",
            control_id="synthetic-control", touch_mode="direct", track=track)
        lease["held_buttons"].add("left")
        with pytest.raises(RuntimeError) as failed:
            await engine._release_remote_desktop_control("synthetic-legacy-controller")
        assert failed.value is failure and native_host_input_busy()
        assert lease["native_cleanup_barrier"].lease is lease and lease["held_buttons"] == {"left"}
        assert calls == [{"left"}] and lease["input_cleanup_task"].exception() is failure
    asyncio.run(run())


def test_game_input_normalization_bounds_multitouch_and_sensors():
    frame = normalize_game_input({
        "control_id": "synthetic-control", "input_type": "touch",
        "points": [{"id": 1, "x": 0.25, "y": 0.75}, {"id": 2, "x": 1, "y": 0}],
    })
    assert frame["points"] == [{"id": 1, "x": 0.25, "y": 0.75}, {"id": 2, "x": 1.0, "y": 0.0}]
    assert normalize_game_input({"control_id": "synthetic-control", "input_type": "touch",
                                 "points": [{"id": 1, "x": 0, "y": 0}, {"id": 1, "x": 1, "y": 1}]}) is None
    assert normalize_game_input({"control_id": "synthetic-control", "input_type": "sensor",
                                 "sensor": "accelerometer", "x": float("inf"), "y": 0, "z": 0}) is None
    assert normalize_game_input({"control_id": "synthetic-control", "input_type": "axis",
                                 "axis": "left_x", "value": 1.1}) is None
    assert normalize_game_input({"control_id": "synthetic-control", "input_type": "heartbeat"}) == {
        "event": "game_input", "control_id": "synthetic-control", "input_type": "heartbeat",
    }


def test_stalled_engine_coalesces_motion_and_resets_only_when_controls_overflow():
    hub = GameInputHub()
    queue = hub.attach()
    assert queue.maxsize == 8
    hub.publish("synthetic-session", {"event": "game_input", "input_type": "session_start",
                                      "control_id": "synthetic-control"})
    for value in range(queue.maxsize - 1):
        hub.publish("synthetic-session", {"event": "game_input", "input_type": "axis",
                                          "axis": "left_x", "value": value})
    hub.publish("synthetic-session", {"event": "game_input", "input_type": "button", "phase": "up"})
    assert queue.get_nowait()["input_type"] == "session_start"
    assert queue.get_nowait()["value"] == 6
    assert queue.get_nowait()["input_type"] == "button"
    assert queue.empty()

    for value in range(queue.maxsize):
        hub.publish("synthetic-session", {"event": "game_input", "input_type": "touch",
                                          "points": [{"id": 0, "x": value / 10, "y": 0.5}]})
    hub.publish("synthetic-session", {"event": "game_input", "input_type": "touch", "points": []})
    assert queue.get_nowait()["points"] == [{"id": 0, "x": 0.7, "y": 0.5}]
    assert queue.get_nowait()["points"] == []
    assert queue.empty()

    for value in range(queue.maxsize):
        hub.publish("synthetic-session", {"event": "game_input", "input_type": "button",
                                          "button": f"action_{value}", "phase": "down"})
    hub.publish("synthetic-session", {"event": "game_input", "input_type": "button",
                                      "button": "action_0", "phase": "up"})
    reset = queue.get_nowait()
    assert reset["input_type"] == "state_reset" and reset["all_sessions"] is True
    assert queue.get_nowait()["phase"] == "up"
    hub.detach(queue)


def test_game_capture_fills_native_video_and_restores_camera(monkeypatch):
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    class PreviewTrack:
        def __init__(self, color):
            self.color = color
            self.enabled = False
            self.monitor_id = 0
            self.output_size = (1280, 720)

        def enable(self):
            self.enabled = True

        def disable(self):
            self.enabled = False

        def apply_remote_desktop_profile(self, *, monitor_id, fps, max_width):
            self.monitor_id = monitor_id
            self.output_size = (max_width, max_width * 9 // 16)

        def capture_preview_image(self, *, max_width):
            assert self.enabled
            return video_call_manager.Image.new("RGB", (64, 36), self.color)

    cfg = server._default_config()
    monkeypatch.setattr(server.STATE, "config", cfg)
    desktop = PreviewTrack((180, 20, 20))
    camera = PreviewTrack((20, 180, 20))
    track = video_call_manager.CompositeVideoStreamTrack(
        [("remote_desktop", desktop), ("camera", camera)], max_width=1280,
    )
    track.enable()
    webrtc = server.WebRTCManager()

    webrtc._apply_game_capture_rate(track, game_active=True)
    track.enable()
    assert track.fps == 30 and desktop.enabled and not camera.enabled
    assert track.remote_desktop_mapping()["content_rect"]["width"] == 1.0
    assert track.capture_preview_image(max_width=640).getpixel((480, 180)) == desktop.color

    webrtc._apply_game_capture_rate(track, game_active=False)
    assert track.fps == server._get_remote_desktop_capture_profile(cfg=cfg)["fps"]
    assert camera.enabled and track.remote_desktop_mapping()["source_count"] == 2
    assert track.capture_preview_image(max_width=640).getpixel((480, 180)) == camera.color


def test_game_frames_require_game_lease_and_release_on_exit(monkeypatch):
    class Track:
        is_enabled = True
        fps = 12

        def apply_remote_desktop_profile(self, *, monitor_id, fps, max_width):
            self.fps = fps

        def remote_desktop_mapping(self):
            return {"content_rect": [0, 0, 100, 100]}

        def map_output_point_to_desktop(self, x, y):
            return int(x * 100), int(y * 100)

    async def run():
        cfg = server._default_config()
        cfg["video_call"]["remote_desktop"].update(control_enabled=True, game_enabled=True)
        original_config = server.STATE.config
        server.STATE.config = cfg
        host_inputs, host_keys, released = [], [], []
        backend = {"available": False, "probes": 0}
        monkeypatch.setattr(server, "remote_desktop_input_backend_probed", lambda: backend["available"])
        def probe_backend(refresh=False):
            backend["probes"] += 1
            return backend["available"]
        monkeypatch.setattr(server, "remote_desktop_input_backend_available", probe_backend)
        monkeypatch.setattr(server, "release_remote_desktop_inputs", lambda buttons=(), **kwargs:
                            released.append((set(buttons), set(kwargs.get("held_keys", ())))) )
        monkeypatch.setattr(server, "execute_remote_desktop_input", lambda payload, track: host_inputs.append(payload) or True)
        monkeypatch.setattr(server, "execute_remote_desktop_keyboard", lambda payload, **kwargs: host_keys.append(payload) or True)
        webrtc = server.WebRTCManager()
        session_id = "synthetic-game-session"
        webrtc.desktop_video_tracks[session_id] = Track()
        datachannel = SimpleNamespace(send_message=AsyncMock(return_value=True))
        webrtc.datachannel_managers[session_id] = datachannel
        webrtc._set_voice_call_client_active(session_id, True)
        queue = webrtc.game_input_hub.attach()
        try:
            assert server._get_game_mode_available(cfg=cfg)
            assert not server._get_remote_desktop_control_available(cfg=cfg)
            webrtc._set_voice_call_client_active(session_id, False)
            await webrtc._handle_remote_desktop_control(session_id, {
                "action": "start", "control_id": "synthetic-pending-control", "fullscreen": True,
                "mode": "game", "source": "autoyou_lite", "platform": "android",
            })
            assert datachannel.send_message.call_args.args[0].payload["retryable"] is True
            assert session_id not in webrtc.remote_desktop_control_leases_by_session
            await webrtc._handle_game_input(session_id, {
                "control_id": "synthetic-pending-control", "input_type": "touch", "points": [],
                "source": "autoyou_lite", "platform": "android",
            })
            assert datachannel.send_message.call_args.args[0].payload["retryable"] is True
            await webrtc._send_remote_desktop_control_status(
                session_id, control_id="synthetic-pending-control", active=False,
                reason="Game mode is disabled or unavailable.",
            )
            assert "retryable" not in datachannel.send_message.call_args.args[0].payload
            webrtc._set_voice_call_client_active(session_id, True)
            import shared.iroh_input as native_input
            monkeypatch.setattr(native_input, "native_host_input_busy", lambda: True)
            await webrtc._handle_remote_desktop_control(session_id, {
                "action": "start", "control_id": "synthetic-native-busy-control", "fullscreen": True,
                "mode": "game", "source": "autoyou_lite", "platform": "android",
            })
            assert session_id not in webrtc.remote_desktop_control_leases_by_session
            assert "Another controller" in datachannel.send_message.call_args.args[0].payload["reason"]
            assert queue.empty() and not host_inputs and not host_keys
            monkeypatch.setattr(native_input, "native_host_input_busy", lambda: False)
            await webrtc._handle_remote_desktop_control(session_id, {
                "action": "start", "control_id": "synthetic-control", "fullscreen": True,
                "mode": "game", "source": "autoyou_lite", "platform": "android",
            })
            assert webrtc.remote_desktop_control_leases_by_session[session_id]["mode"] == "game"
            assert backend["probes"] == 0
            assert datachannel.send_message.call_args.args[0].payload["engine_input"] is True
            assert queue.get_nowait()["input_type"] == "session_start"
            lease = webrtc.remote_desktop_control_leases_by_session[session_id]
            lease["expires_at"] = server.time.time() + 1
            await webrtc._handle_game_input(session_id, {
                "control_id": "synthetic-control", "input_type": "heartbeat",
                "source": "autoyou_lite", "platform": "android",
            })
            assert lease["expires_at"] > server.time.time() + 500
            assert queue.empty()
            renewed_at = lease["expires_at"]
            await webrtc._handle_game_input(session_id, {
                "control_id": "synthetic-other-control", "input_type": "heartbeat",
                "source": "autoyou_lite", "platform": "android",
            })
            assert lease["expires_at"] == renewed_at
            assert webrtc.desktop_video_tracks[session_id].fps == 30
            await webrtc.apply_video_call_settings()
            assert webrtc.remote_desktop_control_leases_by_session[session_id]["mode"] == "game"
            await webrtc._handle_game_input(session_id, {
                "event": "game_input", "control_id": "synthetic-control", "input_type": "touch",
                "points": [{"id": 0, "x": 0.5, "y": 0.5}],
                "source": "autoyou_lite", "platform": "android",
            })
            assert queue.get_nowait()["points"] == [{"id": 0, "x": 0.5, "y": 0.5}]
            assert host_inputs == []
            await webrtc._handle_remote_desktop_input(session_id, {
                "control_id": "synthetic-control", "input_type": "button", "button": "left",
                "phase": "down", "source": "autoyou_lite", "platform": "android",
            })
            assert queue.get_nowait()["button"] == "left"
            await webrtc._handle_call_remote_desktop_keyboard(session_id, {
                "control_id": "synthetic-control", "action": "key", "key": "space",
                "phase": "down", "source": "autoyou_lite", "platform": "android",
            })
            assert queue.get_nowait()["key"] == "space"
            assert host_inputs == host_keys == []
            webrtc.game_input_hub.detach(queue)
            await webrtc._sync_game_input_engine_state()
            assert session_id not in webrtc.remote_desktop_control_leases_by_session
            assert datachannel.send_message.call_args.args[0].payload["retryable"] is True
            await webrtc._handle_remote_desktop_input(session_id, {
                "control_id": "synthetic-control", "input_type": "button", "button": "left",
                "phase": "up", "source": "autoyou_lite", "platform": "android",
            })
            assert host_inputs == []
            assert session_id not in webrtc.remote_desktop_control_leases_by_session
            assert datachannel.send_message.call_args.args[0].payload["retryable"] is True
            queue = webrtc.game_input_hub.attach()
            await webrtc._handle_remote_desktop_control(session_id, {
                "action": "start", "control_id": "synthetic-control", "fullscreen": True,
                "mode": "game", "source": "autoyou_lite", "platform": "android",
            })
            assert queue.get_nowait()["input_type"] == "session_start"
            await webrtc._handle_remote_desktop_input(session_id, {
                "control_id": "synthetic-control", "input_type": "button", "button": "left",
                "phase": "up", "source": "autoyou_lite", "platform": "android",
            })
            assert queue.get_nowait()["phase"] == "up"
            await webrtc._handle_game_input(session_id, {
                "control_id": "synthetic-control", "input_type": "button", "button": "jump",
                "phase": "down", "source": "untrusted", "platform": "android",
            })
            assert queue.empty()
            await webrtc._handle_remote_desktop_control(session_id, {
                "action": "stop", "control_id": "synthetic-control",
                "source": "autoyou_lite", "platform": "android",
            })
            assert queue.get_nowait()["input_type"] == "session_end"
            assert webrtc.desktop_video_tracks[session_id].fps == 12

            webrtc.game_input_hub.detach(queue)
            host_inputs.clear()
            backend["available"] = True
            await webrtc._handle_remote_desktop_control(session_id, {
                "action": "start", "control_id": "synthetic-browser-control", "fullscreen": True,
                "mode": "game", "source": "autoyou_lite", "platform": "ios",
            })
            assert datachannel.send_message.call_args.args[0].payload["engine_input"] is False
            async def touch(points):
                await webrtc._handle_game_input(session_id, {
                    "control_id": "synthetic-browser-control", "input_type": "touch", "points": points,
                    "source": "autoyou_lite", "platform": "ios",
                })

            await touch([{"id": 0, "x": 0.2, "y": 0.3}])
            await touch([{"id": 0, "x": 0.4, "y": 0.3}])
            await touch([{"id": 0, "x": 0.4, "y": 0.3}, {"id": 1, "x": 0.5, "y": 0.3}])
            await touch([{"id": 1, "x": 0.5, "y": 0.3}])
            await touch([])
            await touch([{"id": 2, "x": 0.6, "y": 0.3}])
            assert [(item["input_type"], item.get("phase")) for item in host_inputs] == [
                ("button", "down"), ("move", None), ("button", "up"), ("button", "down"),
            ]
            assert host_inputs[0]["x"] == 0.2 and host_inputs[1]["x"] == 0.4
            await webrtc._handle_call_remote_desktop_keyboard(session_id, {
                "control_id": "synthetic-browser-control", "action": "key", "key": "space",
                "phase": "down", "source": "autoyou_lite", "platform": "ios",
            })
            assert host_keys[-1]["key"] == "space"
            queue = webrtc.game_input_hub.attach()
            await webrtc._sync_game_input_engine_state()
            assert released[-1] == ({"left"}, {"space"})
            assert datachannel.send_message.call_args.args[0].payload["engine_input"] is True
            assert queue.get_nowait()["input_type"] == "session_start"
            assert queue.get_nowait()["points"] == [{"id": 2, "x": 0.6, "y": 0.3}]
            assert queue.empty()
            host_input_count = len(host_inputs)
            await touch([{"id": 2, "x": 0.7, "y": 0.3}])
            assert released[-1] == ({"left"}, {"space"})
            assert datachannel.send_message.call_args.args[0].payload["engine_input"] is True
            assert queue.get_nowait()["points"][0]["x"] == 0.7
            await webrtc._handle_game_input(session_id, {
                "control_id": "synthetic-browser-control", "input_type": "sensor",
                "sensor": "accelerometer", "x": 0.1, "y": -9.8, "z": 0.2,
                "source": "autoyou_lite", "platform": "ios",
            })
            assert queue.get_nowait()["sensor"] == "accelerometer"
            await webrtc._handle_game_input(session_id, {
                "control_id": "synthetic-browser-control", "input_type": "axis",
                "axis": "left_x", "value": 0.5, "source": "autoyou_lite", "platform": "ios",
            })
            assert queue.get_nowait()["value"] == 0.5
            await webrtc._handle_game_input(session_id, {
                "control_id": "synthetic-browser-control", "input_type": "button",
                "button": "action_a", "phase": "down", "source": "autoyou_lite", "platform": "ios",
            })
            assert queue.get_nowait()["button"] == "action_a"
            await webrtc._handle_remote_desktop_input(session_id, {
                "control_id": "synthetic-browser-control", "input_type": "button", "button": "right",
                "phase": "down", "source": "autoyou_lite", "platform": "ios",
            })
            mouse = queue.get_nowait()
            assert mouse["event"] == "remote_desktop_input" and mouse["button"] == "right"
            assert len(host_inputs) == host_input_count
            await touch([{"id": 2, "x": 0.8, "y": 0.3}])
            assert host_inputs[-1]["phase"] == "down"
            await webrtc._handle_call_remote_desktop_keyboard(session_id, {
                "control_id": "synthetic-browser-control", "action": "key", "key": "space",
                "phase": "up", "source": "autoyou_lite", "platform": "ios",
            })
            assert queue.get_nowait()["points"][0]["x"] == 0.8
            assert queue.get_nowait()["phase"] == "up"
            assert host_keys[-1]["phase"] == "down"
            webrtc.game_input_hub.detach(queue)
            await webrtc._sync_game_input_engine_state()
            assert datachannel.send_message.call_args.args[0].payload["engine_input"] is False
            assert host_inputs[-1]["input_type"] == "button"
            assert host_inputs[-1]["phase"] == "down" and host_inputs[-1]["x"] == 0.8
            await touch([{"id": 4, "x": 0.3, "y": 0.3}])
            assert datachannel.send_message.call_args.args[0].payload["engine_input"] is False
            assert host_inputs[-1]["input_type"] == "button"
            assert host_inputs[-1]["phase"] == "down"
            await touch([])
            assert host_inputs[-1]["phase"] == "up"
            await webrtc._handle_game_input(session_id, {
                "control_id": "synthetic-browser-control", "input_type": "axis",
                "axis": "left_x", "value": -1, "source": "autoyou_lite", "platform": "ios",
            })
            assert host_keys[-1]["key"] == "left" and host_keys[-1]["phase"] == "press"
            key_count = len(host_keys)
            await webrtc._handle_game_input(session_id, {
                "control_id": "synthetic-browser-control", "input_type": "axis",
                "axis": "left_x", "value": -1, "source": "autoyou_lite", "platform": "ios",
            })
            assert len(host_keys) == key_count
            await webrtc._handle_game_input(session_id, {
                "control_id": "synthetic-browser-control", "input_type": "button",
                "button": "action_a", "phase": "down", "source": "autoyou_lite", "platform": "ios",
            })
            assert host_keys[-1]["key"] == "space" and host_keys[-1]["phase"] == "press"
            await webrtc._handle_remote_desktop_control(session_id, {
                "action": "stop", "control_id": "synthetic-browser-control",
                "source": "autoyou_lite", "platform": "ios",
            })
            backend["available"] = False
            await webrtc._handle_remote_desktop_control(session_id, {
                "action": "start", "control_id": "synthetic-waiting-control", "fullscreen": True,
                "mode": "game", "source": "autoyou_lite", "platform": "ios",
            })
            assert session_id not in webrtc.remote_desktop_control_leases_by_session
            assert datachannel.send_message.call_args.args[0].payload["retryable"] is True
            queue = webrtc.game_input_hub.attach()
            await webrtc._handle_remote_desktop_control(session_id, {
                "action": "start", "control_id": "synthetic-waiting-control", "fullscreen": True,
                "mode": "game", "source": "autoyou_lite", "platform": "ios",
            })
            assert queue.get_nowait()["input_type"] == "session_start"
            await webrtc._handle_game_input(session_id, {
                "control_id": "synthetic-waiting-control", "input_type": "touch",
                "points": [{"id": 3, "x": 0.4, "y": 0.3}],
                "source": "autoyou_lite", "platform": "ios",
            })
            assert queue.get_nowait()["points"][0]["x"] == 0.4
            await webrtc._handle_remote_desktop_control(session_id, {
                "action": "stop", "control_id": "synthetic-waiting-control",
                "source": "autoyou_lite", "platform": "ios",
            })
        finally:
            webrtc.game_input_hub.detach(queue)
            server.STATE.config = original_config

    asyncio.run(run())


def test_local_engine_stream_requires_token(monkeypatch):
    monkeypatch.setattr(server, "_get_game_mode_available", lambda cfg=None: True)
    monkeypatch.setattr(server, "_is_loopback_client_host", lambda host: host == "testclient")
    sync = AsyncMock(wraps=server.WEBRTC._sync_game_input_engine_state)
    monkeypatch.setattr(server.WEBRTC, "_sync_game_input_engine_state", sync)
    path = "/api/webrtc/game-input/stream"
    with TestClient(server.admin_app) as client:
        try:
            with client.websocket_connect(path):
                raise AssertionError("WebSocket accepted without a token")
        except WebSocketDisconnect as exc:
            assert exc.code == 1008
        try:
            with client.websocket_connect(path, headers={
                "Authorization": f"Bearer {server.WEBRTC.game_input_hub.token}",
                "X-Forwarded-For": "203.0.113.1",
            }):
                raise AssertionError("Proxied engine stream was accepted")
        except WebSocketDisconnect as exc:
            assert exc.code == 1008
        with client.websocket_connect(path, headers={
            "Authorization": f"Bearer {server.WEBRTC.game_input_hub.token}",
        }) as stream:
            client.portal.call(server.WEBRTC.game_input_hub.publish, "synthetic-session", {
                "event": "game_input", "input_type": "touch", "points": [],
            })
            assert stream.receive_json()["session_id"] == "synthetic-session"
            stream.close()
            client.portal.call(server.WEBRTC.game_input_hub.publish, "synthetic-session", {
                "event": "game_input", "input_type": "session_end",
            })
        assert sync.await_count == 2
        assert not server.WEBRTC.game_input_hub.connected


def test_hosted_game_audio_uses_playback_mixer_only_for_hosted_engine(monkeypatch):
    with wave.open(str(_HOSTED_GAME_LOOP), "rb") as sound:
        assert sound.getnchannels() == 1 and sound.getframerate() == 16000
        assert sound.getnframes() > 16000
    monkeypatch.setattr(server, "_get_audio_playback_enabled", lambda cfg=None: True)
    manager = SimpleNamespace(play_audio_file=MagicMock(), stop_playback=MagicMock())
    webrtc = server.WebRTCManager()
    monkeypatch.setattr(webrtc, "_resolve_audio_manager_for_reply_target", lambda target: ("synthetic-session", manager))
    lease = {"control_id": "synthetic-control", "mode": "game"}
    webrtc.remote_desktop_control_leases_by_session["synthetic-session"] = lease
    queue = webrtc.game_input_hub.attach(owner="hosted-neon")
    try:
        webrtc._publish_game_session_start("synthetic-session", lease)
        manager.play_audio_file.assert_called_once_with(str(_HOSTED_GAME_LOOP), source="hosted_game", loop=True)
        webrtc._stop_hosted_game_audio(lease)
        manager.stop_playback.assert_called_once_with(source="hosted_game")
    finally:
        webrtc.game_input_hub.detach(queue)
    assert webrtc.game_input_hub.owner is None
    queue = webrtc.game_input_hub.attach()
    try:
        webrtc._publish_game_session_start("synthetic-session", lease)
        manager.play_audio_file.assert_called_once()
    finally:
        webrtc.game_input_hub.detach(queue)


def test_hosted_game_page_accepts_only_local_same_origin_input(monkeypatch):
    monkeypatch.setattr(server, "_get_game_mode_available", lambda cfg=None: True)
    path = "/api/webrtc/hosted-game"
    with TestClient(server.admin_app, base_url="http://127.0.0.1:8001") as client:
        page = client.get(path + "/play")
        assert page.status_code == 200 and "Neon Horizon" in page.text
        assert client.get(path + "/api/game/native-input/status").json()["available"] is True
        assert client.get(path + "/play", headers={"Host": "example.test"}).status_code == 403
        for headers in ({"origin": "https://example.test", "host": "127.0.0.1:8001"}, {
            "origin": "http://127.0.0.1:8001", "host": "127.0.0.1:8001",
            "X-Forwarded-For": "203.0.113.1",
        }):
            try:
                with client.websocket_connect(path + "/api/game/native-input", headers=headers):
                    raise AssertionError("Untrusted page connected to game input")
            except WebSocketDisconnect as exc:
                assert exc.code == 1008
        with client.websocket_connect(path + "/api/game/native-input", headers={
            "origin": "http://127.0.0.1:8001", "host": "127.0.0.1:8001",
        }) as stream:
            client.portal.call(server.WEBRTC.game_input_hub.publish, "synthetic-session", {
                "event": "game_input", "input_type": "button", "button": "action_a", "phase": "down",
            })
            assert stream.receive_json()["button"] == "action_a"
            stream.close()
        assert not server.WEBRTC.game_input_hub.connected
def test_legacy_owned_text_releases_only_owned_keys_before_unicode_text(monkeypatch):
    events = []
    monkeypatch.setattr(server, "release_remote_desktop_inputs", lambda buttons=(), **kwargs:
        events.append(("release", set(buttons), set(kwargs.get("held_keys", ())), kwargs.get("strict"))))
    monkeypatch.setattr(server, "execute_remote_desktop_keyboard", lambda frame, **kwargs:
        events.append(("text", frame["text"], kwargs.get("release_modifiers"))) or True)
    async def run():
        engine = server.WebRTCManager()
        lease = engine._store_remote_desktop_control_lease("synthetic-text-controller",
            control_id="synthetic-text-control", touch_mode="direct", track=SimpleNamespace())
        lease["held_keys"] = {"ctrl"}
        monkeypatch.setattr(engine, "_remote_desktop_control_state", lambda *args: (lease, ""))
        assert await engine._apply_owned_remote_input("synthetic-text-controller", lease,
            dict(action="text", text="Synthetic Ελληνικά 日本語"), keyboard=True)
        assert events == [("release", set(), {"ctrl"}, True), ("text", "Synthetic Ελληνικά 日本語", False)]
        assert lease["held_keys"] == set()
        await engine._release_remote_desktop_control("synthetic-text-controller", expected_lease=lease)
    asyncio.run(run())


def test_legacy_failed_subset_release_is_not_retried_by_end(monkeypatch):
    from shared import iroh_input
    monkeypatch.setattr(iroh_input, "_legacy_input_joins", {})
    attempts = []
    def release(buttons=(), **kwargs):
        keys = set(kwargs.get("held_keys", ()))
        attempts.append((set(buttons), keys))
        if "ctrl" in keys:
            raise RuntimeError("Synthetic SDK key release failed")
    monkeypatch.setattr(server, "release_remote_desktop_inputs", release)
    async def run():
        engine = server.WebRTCManager()
        lease = engine._store_remote_desktop_control_lease("synthetic-failed-release",
            control_id="synthetic-release-control", touch_mode="direct", track=SimpleNamespace())
        lease["held_keys"] = {"ctrl"}; lease["held_buttons"] = {"left"}
        with pytest.raises(RuntimeError, match="Synthetic SDK"):
            await engine._release_owned_remote_inputs("synthetic-failed-release", lease, keys={"ctrl"})
        assert attempts == [(set(), {"ctrl"}), ({"left"}, set())]
        assert lease["input_cleanup_task"].done() and lease["held_keys"] == {"ctrl"}
        assert iroh_input._legacy_input_joins and not engine.remote_desktop_control_leases_by_session
    asyncio.run(run())
