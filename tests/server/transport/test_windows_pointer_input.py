"""SendInput pointer backend: event batches, coordinates, fallbacks.

The ctypes binding only exists on Windows, so these tests drive the backend
through a fake API that records the SendInput batches it would have sent.
"""

from types import SimpleNamespace

import pytest

import shared.windows_pointer_input as wpi
from shared.remote_desktop_input import execute_remote_desktop_input, normalize_remote_desktop_control_payload


class _FakeApi:
    def __init__(self, *, cursor=(100, 200), screen=(0, 0, 1920, 1080), accept=True):
        self.cursor = list(cursor)
        self.screen = screen
        self.accept = accept
        self.batches = []

    def cursor_pos(self):
        return tuple(self.cursor)

    def virtual_screen(self):
        return self.screen

    def send(self, events):
        self.batches.append(list(events))
        if not self.accept:
            return 0
        left, top, width, height = self.screen
        for flags, dx, dy, _data in events:
            if flags & wpi.MOUSEEVENTF_ABSOLUTE:
                # Windows: pixel = normalized * extent / 65536, truncated.
                self.cursor = [left + dx * width // 65536, top + dy * height // 65536]
        return len(events)


class _FakePyAutoGUI:
    def __init__(self):
        self.calls = []
        self.FAILSAFE = True
        self.PAUSE = 0.1

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return record


def _backend(api=None, base=None):
    return wpi.WindowsSendInputPointer(base or _FakePyAutoGUI(), api=api or _FakeApi())


@pytest.mark.parametrize("extent", [1920, 2560, 3840, 1366, 1081, 7])
def test_absolute_normalization_lands_on_the_exact_pixel(extent):
    for pixel in {0, 1, extent // 3, extent // 2, extent - 2, extent - 1}:
        if pixel < 0:
            continue
        normalized = wpi.normalized_absolute(pixel, 0, extent)
        assert 0 <= normalized <= 65535
        assert normalized * extent // 65536 == pixel


def test_absolute_normalization_honours_negative_virtual_desktop_origin():
    # A monitor to the left of the primary gives the virtual desktop a negative origin.
    left, width = -1920, 3840
    normalized = wpi.normalized_absolute(-1920 + 500, left, width)
    assert left + normalized * width // 65536 == -1420
    assert wpi.normalized_absolute(-5000, left, width) == 0
    # Past the right edge clamps to the last pixel, never off the desktop.
    assert left + wpi.normalized_absolute(10_000, left, width) * width // 65536 == left + width - 1


def test_relative_move_is_an_absolute_sendinput_event_from_the_current_cursor():
    api = _FakeApi(cursor=(100, 200))
    backend = _backend(api)
    backend.moveRel(15, -7)
    assert len(api.batches) == 1
    (flags, _dx, _dy, _data), = api.batches[0]
    assert flags == wpi.MOUSEEVENTF_MOVE | wpi.MOUSEEVENTF_ABSOLUTE | wpi.MOUSEEVENTF_VIRTUALDESK
    assert api.cursor == [115, 193]
    backend.moveRel(0, 0)
    assert len(api.batches) == 1


def test_click_at_a_point_moves_then_presses_in_one_ordered_batch():
    api = _FakeApi()
    backend = _backend(api)
    backend.click(640, 360, button="left")
    (move, down, up), = api.batches
    assert move[0] & wpi.MOUSEEVENTF_ABSOLUTE
    assert down[0] == wpi.MOUSEEVENTF_LEFTDOWN
    assert up[0] == wpi.MOUSEEVENTF_LEFTUP
    assert api.cursor == [640, 360]


def test_click_without_coordinates_never_moves_the_pointer():
    api = _FakeApi()
    backend = _backend(api)
    backend.click(button="right")
    backend.doubleClick(button="left")
    assert [event[0] for event in api.batches[0]] == [wpi.MOUSEEVENTF_RIGHTDOWN, wpi.MOUSEEVENTF_RIGHTUP]
    assert [event[0] for event in api.batches[1]] == [
        wpi.MOUSEEVENTF_LEFTDOWN, wpi.MOUSEEVENTF_LEFTUP, wpi.MOUSEEVENTF_LEFTDOWN, wpi.MOUSEEVENTF_LEFTUP,
    ]
    assert api.cursor == [100, 200]


def test_wheel_uses_signed_wheel_deltas():
    api = _FakeApi()
    backend = _backend(api)
    backend.scroll(-3)
    backend.hscroll(2)
    assert api.batches[0] == [(wpi.MOUSEEVENTF_WHEEL, 0, 0, -3 * wpi.WHEEL_DELTA)]
    assert api.batches[1] == [(wpi.MOUSEEVENTF_HWHEEL, 0, 0, 2 * wpi.WHEEL_DELTA)]


def test_refused_injection_falls_back_to_pyautogui():
    api = _FakeApi(accept=False)
    base = _FakePyAutoGUI()
    backend = _backend(api, base)
    backend.mouseDown(button="left")
    backend.moveTo(10, 20)
    assert [name for name, _args, _kwargs in base.calls] == ["mouseDown", "moveTo"]


def test_keyboard_calls_and_settings_reach_pyautogui():
    base = _FakePyAutoGUI()
    backend = _backend(base=base)
    backend.FAILSAFE = False
    backend.PAUSE = 0
    backend.keyDown("shift")
    assert base.FAILSAFE is False and base.PAUSE == 0
    assert base.calls == [("keyDown", ("shift",), {})]


def test_execute_remote_desktop_input_drives_the_sendinput_backend():
    api = _FakeApi(cursor=(960, 540))
    backend = _backend(api)
    track = SimpleNamespace(
        remote_desktop_mapping=lambda: {"monitor_bounds": {"left": 0, "top": 0, "width": 1920, "height": 1080}},
        map_output_point_to_desktop=lambda x, y: (round(x * 1919), round(y * 1079)),
    )
    remainder = {}
    assert execute_remote_desktop_input(
        {"control_id": "c", "input_type": "move", "coordinate_mode": "relative", "dx": 0.01, "dy": -0.02},
        track=track, pyautogui=backend, relative_remainder=remainder,
    )
    assert api.cursor == [979, 518]
    assert execute_remote_desktop_input(
        {"control_id": "c", "input_type": "button", "button": "left", "phase": "click"},
        track=track, pyautogui=backend,
    )
    assert [event[0] for event in api.batches[-1]] == [wpi.MOUSEEVENTF_LEFTDOWN, wpi.MOUSEEVENTF_LEFTUP]
    assert api.cursor == [979, 518]


def test_wrapper_is_only_installed_on_windows(monkeypatch):
    base = _FakePyAutoGUI()
    monkeypatch.setattr(wpi, "sys", SimpleNamespace(platform="darwin"))
    assert wpi.windows_pointer_backend(base) is base
    monkeypatch.setattr(wpi, "sys", SimpleNamespace(platform="win32"))
    wrapped = wpi.windows_pointer_backend(base)
    assert isinstance(wrapped, wpi.WindowsSendInputPointer)
    assert wpi.windows_pointer_backend(base) is wrapped
    monkeypatch.setenv(wpi.ENV_DISABLE, "0")
    assert wpi.windows_pointer_backend(base) is base


def test_phones_default_to_trackpad_control_and_mice_keep_direct():
    start = {"action": "start", "control_id": "c", "fullscreen": True}
    assert normalize_remote_desktop_control_payload({**start, "platform": "ios"})["touch_mode"] == "relative"
    assert normalize_remote_desktop_control_payload({**start, "platform": "android"})["touch_mode"] == "relative"
    assert "touch_mode" not in normalize_remote_desktop_control_payload({**start, "platform": "chrome"})
    explicit = normalize_remote_desktop_control_payload({**start, "platform": "ios", "touch_mode": "direct"})
    assert explicit["touch_mode"] == "direct"
