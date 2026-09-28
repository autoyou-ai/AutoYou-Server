# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

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
