# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-86f6fe8ee924d5808ac7e5d2

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import sys
from types import SimpleNamespace
from types import ModuleType

from shared.remote_desktop_input import (
    execute_remote_desktop_input,
    normalize_remote_desktop_control_payload,
    normalize_remote_desktop_input_payload,
    release_remote_desktop_inputs,
)
from shared.video_call_manager import CompositeVideoStreamTrack

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-86f6fe8ee924d5808ac7e5d2"


class _FakeDesktopTrack:
    has_live_content = True
    monitor_id = 2
    output_size = (1600, 900)

    def desktop_bounds(self):
        return {"left": 100, "top": 200, "width": 1600, "height": 900}

    def remote_desktop_mapping(self):
        return {
            "content_rect": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            "monitor_bounds": self.desktop_bounds(),
        }

    def map_output_point_to_desktop(self, x, y):
        return 100 + round(float(x) * 1599), 200 + round(float(y) * 899)


class _FakeInputBackend:
    FAILSAFE = True
    PAUSE = 1

    def __init__(self):
        self.calls = []

    def moveTo(self, x, y):
        self.calls.append(("moveTo", x, y))

    def moveRel(self, x, y):
        self.calls.append(("moveRel", x, y))

    def click(self, *, button):
        self.calls.append(("click", button))

    def doubleClick(self, *, button):
        self.calls.append(("doubleClick", button))

    def mouseDown(self, *, button):
        self.calls.append(("mouseDown", button))

    def mouseUp(self, *, button):
        self.calls.append(("mouseUp", button))

    def scroll(self, amount):
        self.calls.append(("scroll", amount))

    def hscroll(self, amount):
        self.calls.append(("hscroll", amount))

    def keyUp(self, key):
        self.calls.append(("keyUp", key))


def test_remote_desktop_control_requires_fullscreen_and_preserves_native_source():
    assert normalize_remote_desktop_control_payload({
        "event": "remote_desktop_control",
        "action": "start",
        "control_id": "control-1",
        "source": "autoyou_lite",
        "platform": "ios",
    }) is None

    normalized = normalize_remote_desktop_control_payload({
        "event": "remote_desktop_control",
        "action": "start",
        "control_id": "control-1",
        "source": "autoyou_lite",
        "platform": "android",
        "fullscreen": True,
        "touch_mode": "relative",
    })
    # from __debug_provenance_s__ import btc

    assert normalized == {
        "event": "remote_desktop_control",
        "action": "start",
        "control_id": "control-1",
        "source": "autoyou_lite",
        "platform": "android",
        "fullscreen": True,
        "touch_mode": "relative",
    }


def test_remote_desktop_input_normalizes_absolute_relative_and_button_events():
    absolute = normalize_remote_desktop_input_payload({
        "control_id": "control-1",
        "input_type": "move",
        "coordinate_mode": "absolute",
        "x": 2,
        "y": -1,
        "source": "autoyou_lite",
        "platform": "ios",
    })
    relative = normalize_remote_desktop_input_payload({
        "control_id": "control-1",
        "input_type": "move",
        "coordinate_mode": "relative",
        "dx": 0.75,
        "dy": -0.75,
    })
    button = normalize_remote_desktop_input_payload({
        "control_id": "control-1",
        "input_type": "button",
        "button": "right",
        "phase": "double-click",
        "x": 0.25,
        "y": 0.5,
    })

    assert absolute["x"] == 1.0
    assert absolute["y"] == 0.0
    assert absolute["source"] == "autoyou_lite"
    assert relative["dx"] == 0.5
    assert relative["dy"] == -0.5
    assert button["phase"] == "double_click"


def test_scroll_accepts_a_single_axis_and_still_rejects_an_empty_delta():
    horizontal = normalize_remote_desktop_input_payload({
        "control_id": "control-1",
        "input_type": "scroll",
        "dx": 3.5,
    })
    vertical = normalize_remote_desktop_input_payload({
        "control_id": "control-1",
        "input_type": "scroll",
        "dy": -2.0,
    })

    assert horizontal == {
        "event": "remote_desktop_input",
        "control_id": "control-1",
        "input_type": "scroll",
        "dx": 3.5,
        "dy": 0.0,
    }
    assert vertical["dy"] == -2.0
    assert vertical["dx"] == 0.0
    assert normalize_remote_desktop_input_payload({
        "control_id": "control-1",
        "input_type": "scroll",
    }) is None


def test_input_backend_probe_is_cached_and_reports_an_unusable_host(monkeypatch):
    import shared.remote_desktop_input as module

    calls = []

    class _HeadlessBackend:
        def moveTo(self, *_args, **_kwargs):
            pass

        def moveRel(self, *_args, **_kwargs):
            pass

        def click(self, *_args, **_kwargs):
            pass

        def mouseDown(self, *_args, **_kwargs):
            pass

        def mouseUp(self, *_args, **_kwargs):
            pass

        def position(self):
            raise RuntimeError("no display")

    def fake_backend(pyautogui=None):
        calls.append(pyautogui)
        return _HeadlessBackend()

    monkeypatch.setattr(module, "_BACKEND_PROBE", {"probed": False, "available": False})
    monkeypatch.setattr(module, "_control_backend", fake_backend)

    assert module.remote_desktop_input_backend_probed() is None
    assert module.remote_desktop_input_backend_available() is False
    assert module.remote_desktop_input_backend_available() is False
    assert module.remote_desktop_input_backend_probed() is False
    assert len(calls) == 1

    monkeypatch.setattr(module, "_control_backend", lambda pyautogui=None: _FakeInputBackend())
    assert module.remote_desktop_input_backend_available(refresh=True) is True
    assert module.remote_desktop_input_backend_probed() is True


def test_pyautogui_loader_tolerates_a_namespace_only_cv2(monkeypatch):
    import shared.remote_desktop_keyboard as module

    namespace_only_cv2 = ModuleType("cv2")
    fake_pyautogui = ModuleType("pyautogui")
    monkeypatch.setattr(module, "ensure_x11_authority_exists", lambda: None)
    monkeypatch.setattr(module, "hide_macos_dock_icon", lambda: None)
    monkeypatch.setitem(sys.modules, "cv2", namespace_only_cv2)
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    assert module.load_pyautogui() is fake_pyautogui
    assert namespace_only_cv2.__version__ == "0.0.0"


def test_stitched_desktop_mapping_rejects_other_video_panes_and_maps_desktop_cell():
    desktop = _FakeDesktopTrack()
    camera = SimpleNamespace(has_live_content=True, output_size=(1280, 720))
    composite = CompositeVideoStreamTrack(
        [("remote_desktop", desktop), ("camera", camera)],
        fps=12,
        max_width=1280,
    )

    mapping = composite.remote_desktop_mapping()

    assert mapping["source_count"] == 2
    assert mapping["monitor_bounds"] == desktop.desktop_bounds()
    assert composite.map_output_point_to_desktop(0.75, 0.5) is None
    mapped = composite.map_output_point_to_desktop(0.25, 0.5)
    assert mapped is not None
    assert 100 <= mapped[0] < 1700
    assert 200 <= mapped[1] < 1100


def test_input_executor_maps_absolute_and_relative_motion_and_releases_host_state():
    backend = _FakeInputBackend()
    track = _FakeDesktopTrack()

    assert execute_remote_desktop_input({
        "control_id": "control-1",
        "input_type": "button",
        "button": "left",
        "phase": "click",
        "x": 0.5,
        "y": 0.5,
    }, track=track, pyautogui=backend)
    assert execute_remote_desktop_input({
        "control_id": "control-1",
        "input_type": "move",
        "coordinate_mode": "relative",
        "dx": 0.1,
        "dy": -0.2,
    }, track=track, pyautogui=backend)
    release_remote_desktop_inputs({"left", "right"}, held_keys={"w", "arrowleft"}, pyautogui=backend)

    assert backend.calls[:2] == [("moveTo", 900, 650), ("click", "left")]
    assert ("moveRel", 160, -180) in backend.calls
    assert ("mouseUp", "left") in backend.calls
    assert ("mouseUp", "right") in backend.calls
    assert ("keyUp", "ctrl") in backend.calls
    assert ("keyUp", "w") in backend.calls
    assert ("keyUp", "left") in backend.calls


def test_macos_motion_uses_native_drag_events_while_button_held(monkeypatch):
    from shared import remote_desktop_input

    monkeypatch.setattr(remote_desktop_input, "sys", SimpleNamespace(platform="darwin"))
    backend = _FakeInputBackend()
    backend.dragTo = lambda x, y, **opts: backend.calls.append(("dragTo", x, y, opts))
    backend.dragRel = lambda x, y, **opts: backend.calls.append(("dragRel", x, y, opts))
    options = {"track": _FakeDesktopTrack(), "pyautogui": backend, "drag_button": "left"}
    assert execute_remote_desktop_input({
        "control_id": "synthetic", "input_type": "move", "x": 0.5, "y": 0.5,
    }, **options)
    assert execute_remote_desktop_input({
        "control_id": "synthetic", "input_type": "move", "coordinate_mode": "relative", "dx": 0.1, "dy": -0.2,
    }, **options)
    assert backend.calls == [
        ("dragTo", 900, 650, {"button": "left", "mouseDownUp": False}),
        ("dragRel", 160, -180, {"button": "left", "mouseDownUp": False}),
    ]


def test_relative_input_preserves_movement_smaller_than_one_host_pixel():
    backend = _FakeInputBackend()
    track = _FakeDesktopTrack()
    remainder = {}
    payload = {
        "control_id": "control-1",
        "input_type": "move",
        "coordinate_mode": "relative",
        "dx": 0.25 / 1600,
        "dy": -0.25 / 900,
    }

    for _ in range(4):
        assert execute_remote_desktop_input(
            payload, track=track, pyautogui=backend, relative_remainder=remainder
        )

    assert backend.calls == [("moveRel", 1, -1)]
    assert remainder == {"x": 0, "y": 0}


def test_wake_display_throttles_high_rate_non_settling_calls(monkeypatch):
    from shared import remote_desktop_keyboard

    monkeypatch.setattr(remote_desktop_keyboard, "sys", SimpleNamespace(platform="darwin"))
    clock = {"now": 500.0}
    spawns = []

    class _FakeProc:
        def poll(self):
            return 0

    monkeypatch.setattr(remote_desktop_keyboard.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        remote_desktop_keyboard.subprocess,
        "Popen",
        lambda *args, **kwargs: spawns.append(clock["now"]) or _FakeProc(),
    )
    monkeypatch.setitem(remote_desktop_keyboard._DISPLAY_WAKE_STATE, "asserted_at", float("-inf"))
    monkeypatch.setitem(remote_desktop_keyboard._DISPLAY_WAKE_STATE, "input_at", float("-inf"))
    monkeypatch.setitem(remote_desktop_keyboard._DISPLAY_WAKE_STATE, "proc", None)
    monkeypatch.setattr(remote_desktop_keyboard, "_DISPLAY_WAKE_FAST_AT", float("-inf"))

    for step in range(50):
        clock["now"] = 500.0 + step * 0.016
        remote_desktop_keyboard.wake_display_for_remote_input(settle=False)

    # First move wakes once; high-rate subsequent moves within 1.5s skip subprocess/lock work.
    assert len(spawns) == 1

    clock["now"] = 506.0
    remote_desktop_keyboard.wake_display_for_remote_input(settle=False)
    assert len(spawns) == 2


def test_webrtc_input_coalesces_queued_relative_moves_and_flushes_before_click(monkeypatch):
    import asyncio
    import threading
    from unittest.mock import AsyncMock
    import pytest
    import server

    async def run() -> None:
        session_id = "synthetic-coalesce-session"
        cfg = server._default_config()
        cfg["video_call"]["remote_desktop"]["control_enabled"] = True
        original_config = server.STATE.config
        server.STATE.config = cfg
        try:
            webrtc = server.WebRTCManager()
            track = _FakeDesktopTrack()
            track.is_enabled = True
            webrtc.desktop_video_tracks[session_id] = track
            webrtc.datachannel_managers[session_id] = SimpleNamespace(
                send_message=AsyncMock(return_value=True)
            )
            webrtc._set_voice_call_client_active(session_id, True)
            webrtc._store_remote_desktop_control_lease(
                session_id,
                control_id="ctrl-coalesce",
                touch_mode="relative",
                track=track,
            )

            first_started = threading.Event()
            allow_first = threading.Event()
            recorded = []

            def fake_execute(payload, **_kwargs):
                recorded.append(dict(payload))
                if len(recorded) == 1:
                    first_started.set()
                    assert allow_first.wait(2.0)
                return True

            monkeypatch.setattr(server, "remote_desktop_input_backend_probed", lambda: True)
            monkeypatch.setattr(server, "execute_remote_desktop_input", fake_execute)

            first_task = asyncio.create_task(webrtc._handle_remote_desktop_input(session_id, {
                "event": "remote_desktop_input",
                "control_id": "ctrl-coalesce", "source": "autoyou_lite", "platform": "ios",
                "input_type": "move", "coordinate_mode": "relative", "dx": 0.01, "dy": -0.02,
            }))
            assert await asyncio.to_thread(first_started.wait, 1.0)

            burst_tasks = [
                asyncio.create_task(webrtc._handle_remote_desktop_input(session_id, {
                    "event": "remote_desktop_input",
                    "control_id": "ctrl-coalesce", "source": "autoyou_lite", "platform": "ios",
                    "input_type": "move", "coordinate_mode": "relative", "dx": 0.01, "dy": -0.02,
                }))
                for _ in range(5)
            ]
            await asyncio.sleep(0)
            click_task = asyncio.create_task(webrtc._handle_remote_desktop_input(session_id, {
                "event": "remote_desktop_input",
                "control_id": "ctrl-coalesce", "source": "autoyou_lite", "platform": "ios",
                "input_type": "button", "button": "left", "phase": "click",
            }))

            allow_first.set()
            await asyncio.gather(first_task, *burst_tasks, click_task)

            assert [item["input_type"] for item in recorded] == ["move", "move", "button"]
            assert recorded[0]["dx"] == 0.01
            assert recorded[1]["dx"] == pytest.approx(0.05)
            assert recorded[1]["dy"] == pytest.approx(-0.10)
            assert recorded[2]["phase"] == "click"
        finally:
            server.STATE.config = original_config

    asyncio.run(run())
