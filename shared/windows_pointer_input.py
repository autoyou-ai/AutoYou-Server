# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Windows pointer injection through ``SendInput``.

pyautogui moves the Windows cursor with ``SetCursorPos``. That call teleports
the pointer without putting a mouse event into the input stream, so:

* applications that read raw or low-level mouse input - TeamViewer, AnyDesk,
  RDP and VNC viewers, games - never see the movement, which is why a remote
  desktop running inside the controlled computer ignored phone touches;
* a click sent right after a jump can be routed to whatever was hovered
  before it, because no ``WM_MOUSEMOVE`` was queued ahead of the button.

``SendInput`` with absolute virtual-desktop coordinates queues real mouse
events, in order, through the same path as a physical mouse. This backend
wraps the pyautogui module so keyboard calls and anything else keep using
pyautogui unchanged; only the pointer calls are replaced. If Windows refuses
an injection (``SendInput`` returns 0, e.g. UIPI against an elevated window),
the call falls back to the pyautogui implementation it replaced.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import os
import sys
import threading
from typing import Any, List, Optional, Sequence, Tuple

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
WHEEL_DELTA = 120

_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

# (flags, dx, dy, mouse_data)
MouseEvent = Tuple[int, int, int, int]

ENV_DISABLE = "AUTOYOU_REMOTE_INPUT_SENDINPUT"


def sendinput_enabled() -> bool:
    """Whether the SendInput pointer path is allowed (default on, Windows only)."""
    if sys.platform != "win32":
        return False
    return str(os.environ.get(ENV_DISABLE, "1")).strip().lower() not in {"0", "false", "no", "off"}


def normalized_absolute(value: int, origin: int, extent: int) -> int:
    """Map a virtual-desktop pixel to SendInput's 0..65535 absolute scale.

    Windows converts back with ``pixel = normalized * extent / 65536``
    (truncating), so rounding up here lands on exactly the requested pixel
    instead of one pixel short on large or odd-sized desktops.
    """
    extent = max(1, int(extent))
    offset = min(max(int(value) - int(origin), 0), extent - 1)
    return min(65535, (offset * 65536 + extent - 1) // extent)


class _Win32Api:  # pragma: no cover - exercised on Windows only
    """Thin ctypes binding; kept separate so the event logic is testable."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class _INPUTUNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._INPUT = INPUT
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
        self._user32.SendInput.restype = wintypes.UINT
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self._user32.GetCursorPos.restype = wintypes.BOOL
        self._user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self._user32.GetSystemMetrics.restype = ctypes.c_int
        self._user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self._user32.SetCursorPos.restype = wintypes.BOOL

    def cursor_pos(self) -> Tuple[int, int]:
        point = self._wintypes.POINT()
        if not self._user32.GetCursorPos(self._ctypes.byref(point)):
            raise OSError("GetCursorPos failed")
        return int(point.x), int(point.y)

    def virtual_screen(self) -> Tuple[int, int, int, int]:
        metrics = self._user32.GetSystemMetrics
        return int(metrics(76)), int(metrics(77)), max(1, int(metrics(78))), max(1, int(metrics(79)))

    def set_cursor_pos(self, x: int, y: int) -> bool:
        return bool(self._user32.SetCursorPos(int(x), int(y)))

    def send(self, events: Sequence[MouseEvent]) -> int:
        if not events:
            return 0
        array = (self._INPUT * len(events))()
        for index, (flags, dx, dy, data) in enumerate(events):
            item = array[index]
            item.type = 0  # INPUT_MOUSE
            item.mi.dx = int(dx)
            item.mi.dy = int(dy)
            item.mi.mouseData = int(data) & 0xFFFFFFFF
            item.mi.dwFlags = int(flags)
            item.mi.time = 0
            item.mi.dwExtraInfo = 0
        return int(self._user32.SendInput(len(events), array, self._ctypes.sizeof(self._INPUT)))


class WindowsSendInputPointer:
    """pyautogui-compatible pointer backend that injects through SendInput.

    Attribute reads that are not pointer calls fall through to the wrapped
    pyautogui module, and ``FAILSAFE`` / ``PAUSE`` writes are forwarded to it,
    so keyboard dispatch keeps its existing behaviour.
    """

    _FORWARDED_SETTINGS = frozenset({"FAILSAFE", "PAUSE", "MINIMUM_DURATION", "MINIMUM_SLEEP"})

    def __init__(self, base: Any, *, api: Any = None) -> None:
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_api", api)
        object.__setattr__(self, "_lock", threading.Lock())

    # -- plumbing ---------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_base"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._FORWARDED_SETTINGS:
            try:
                setattr(self._base, name, value)
            except Exception:
                pass
            return
        object.__setattr__(self, name, value)

    def _win(self) -> Any:
        api = object.__getattribute__(self, "_api")
        if api is None:
            api = _Win32Api()
            object.__setattr__(self, "_api", api)
        return api

    def _absolute_move(self, x: float, y: float) -> MouseEvent:
        left, top, width, height = self._win().virtual_screen()
        return (
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            normalized_absolute(round(x), left, width),
            normalized_absolute(round(y), top, height),
            0,
        )

    def _target(self, x: Optional[float], y: Optional[float]) -> Optional[Tuple[float, float]]:
        if x is None and y is None:
            return None
        current_x, current_y = self._win().cursor_pos()
        return (current_x if x is None else float(x), current_y if y is None else float(y))

    def _send(self, events: List[MouseEvent]) -> bool:
        with object.__getattribute__(self, "_lock"):
            return self._win().send(events) == len(events)

    @staticmethod
    def _button_flags(button: Any) -> Tuple[int, int]:
        name = str(button or "left").strip().lower()
        if name in {"primary", "1"}:
            name = "left"
        elif name in {"secondary", "3"}:
            name = "right"
        elif name == "2":
            name = "middle"
        return _BUTTON_FLAGS.get(name, _BUTTON_FLAGS["left"])

    # -- pyautogui-compatible pointer API ----------------------------------
    def position(self) -> Tuple[int, int]:
        try:
            return self._win().cursor_pos()
        except Exception:
            return self._base.position()

    def size(self) -> Tuple[int, int]:
        return self._base.size()

    def moveTo(self, x: Any = None, y: Any = None, duration: float = 0.0, *args: Any, **kwargs: Any) -> None:
        target = self._target(x, y)
        if target is None:
            return
        if not self._send([self._absolute_move(*target)]):
            self._base.moveTo(round(target[0]), round(target[1]))

    def moveRel(self, xOffset: Any = None, yOffset: Any = None, duration: float = 0.0, *args: Any, **kwargs: Any) -> None:
        dx = float(xOffset or 0)
        dy = float(yOffset or 0)
        if not dx and not dy:
            return
        current_x, current_y = self._win().cursor_pos()
        self.moveTo(current_x + dx, current_y + dy)

    move = moveRel

    def dragTo(self, x: Any = None, y: Any = None, duration: float = 0.0, *args: Any, **kwargs: Any) -> None:
        # Buttons are already held by an explicit mouseDown; moving with
        # SendInput is what makes Windows deliver it as a drag.
        self.moveTo(x, y)

    def dragRel(self, xOffset: Any = 0, yOffset: Any = 0, duration: float = 0.0, *args: Any, **kwargs: Any) -> None:
        self.moveRel(xOffset, yOffset)

    def _button_events(self, x: Any, y: Any, flags: List[int]) -> List[MouseEvent]:
        events: List[MouseEvent] = []
        target = self._target(x, y)
        if target is not None:
            events.append(self._absolute_move(*target))
        events.extend((flag, 0, 0, 0) for flag in flags)
        return events

    def mouseDown(self, x: Any = None, y: Any = None, button: Any = "left", *args: Any, **kwargs: Any) -> None:
        down, _up = self._button_flags(button)
        if not self._send(self._button_events(x, y, [down])):
            self._base.mouseDown(x=x, y=y, button=button)

    def mouseUp(self, x: Any = None, y: Any = None, button: Any = "left", *args: Any, **kwargs: Any) -> None:
        _down, up = self._button_flags(button)
        if not self._send(self._button_events(x, y, [up])):
            self._base.mouseUp(x=x, y=y, button=button)

    def click(
        self, x: Any = None, y: Any = None, clicks: int = 1, interval: float = 0.0,
        button: Any = "left", *args: Any, **kwargs: Any,
    ) -> None:
        down, up = self._button_flags(button)
        count = max(1, min(3, int(clicks or 1)))
        # One SendInput batch keeps the move and every press/release adjacent
        # in the input queue, so nothing can interleave and split a double click.
        if not self._send(self._button_events(x, y, [down, up] * count)):
            self._base.click(x=x, y=y, clicks=count, button=button)

    def doubleClick(self, x: Any = None, y: Any = None, interval: float = 0.0, button: Any = "left", *args: Any, **kwargs: Any) -> None:
        self.click(x, y, clicks=2, button=button)

    def tripleClick(self, x: Any = None, y: Any = None, interval: float = 0.0, button: Any = "left", *args: Any, **kwargs: Any) -> None:
        self.click(x, y, clicks=3, button=button)

    def rightClick(self, x: Any = None, y: Any = None, *args: Any, **kwargs: Any) -> None:
        self.click(x, y, button="right")

    def middleClick(self, x: Any = None, y: Any = None, *args: Any, **kwargs: Any) -> None:
        self.click(x, y, button="middle")

    def scroll(self, clicks: Any, x: Any = None, y: Any = None, *args: Any, **kwargs: Any) -> None:
        amount = int(clicks or 0)
        if not amount:
            return
        # pyautogui and Windows agree: positive wheel data scrolls up.
        if not self._send(self._button_events(x, y, []) + [(MOUSEEVENTF_WHEEL, 0, 0, amount * WHEEL_DELTA)]):
            self._base.scroll(amount)

    def hscroll(self, clicks: Any, x: Any = None, y: Any = None, *args: Any, **kwargs: Any) -> None:
        amount = int(clicks or 0)
        if not amount:
            return
        if not self._send(self._button_events(x, y, []) + [(MOUSEEVENTF_HWHEEL, 0, 0, amount * WHEEL_DELTA)]):
            hscroll = getattr(self._base, "hscroll", None)
            if callable(hscroll):
                hscroll(amount)


_WRAPPED: dict = {}
_WRAPPED_LOCK = threading.Lock()


def windows_pointer_backend(base: Any) -> Any:
    """Return ``base`` wrapped with SendInput pointer injection on Windows."""
    if not sendinput_enabled() or base is None:
        return base
    key = id(base)
    with _WRAPPED_LOCK:
        wrapped = _WRAPPED.get(key)
        if wrapped is None or object.__getattribute__(wrapped, "_base") is not base:
            wrapped = WindowsSendInputPointer(base)
            _WRAPPED[key] = wrapped
        return wrapped
