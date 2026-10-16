# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Committed native text, separate from legacy ASCII keystroke emulation.

These ports own possible partial key-downs. They never use the clipboard,
replay a failed string, or release unrelated keys. X11 borrows only an unmapped,
unpressed keycode and restores its exact mapping before returning or retiring.
SDK loading is denied under AUTOYOU_TEST_ROOT; tests inject explicit ports.
"""
from __future__ import annotations

import ctypes
import os
import sys


def utf16_units(text):
    encoded = text.encode("utf-16-le", errors="strict")
    return tuple(int.from_bytes(encoded[i:i + 2], "little") for i in range(0, len(encoded), 2))


class _KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16),
               ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32),
               ("dwExtraInfo", ctypes.c_size_t)]


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32),
               ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
               ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16), ("wParamH", ctypes.c_uint16)]


class _InputValue(ctypes.Union):
    _fields_ = [("ki", _KeyboardInput), ("mi", _MouseInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("value", _InputValue)]


class WindowsNativeText:
    def __init__(self, send_input):
        self.send_input = send_input
        self.pending = set()
        self.release_error = None

    @staticmethod
    def _events(text):
        events = []
        for char in text:
            virtual = {"\n": 13, "\t": 9, "\b": 8}.get(char)
            identities = ((virtual, 0, 0),) if virtual is not None else tuple((0, unit, 4) for unit in utf16_units(char))
            for vk, scan, flags in identities:
                events.extend([(vk, scan, flags), (vk, scan, flags | 2)])
        return events

    def _send(self, events):
        inputs = (_Input * len(events))()
        for entry, (vk, scan, flags) in zip(inputs, events):
            entry.type = 1
            entry.value.ki = _KeyboardInput(vk, scan, flags, 0, 0)
        sent = self.send_input(len(inputs), inputs, ctypes.sizeof(_Input))
        if type(sent) is not int or not 0 <= sent <= len(inputs):
            raise RuntimeError("native text returned an invalid admission count")
        return sent

    def write(self, text, guard):
        if self.pending or self.release_error is not None:
            raise RuntimeError("native text has unjoined input")
        events = self._events(text)
        # A throwing/invalid SDK result cannot establish which downs occurred.
        self.pending = {(vk, scan, flags & ~2) for vk, scan, flags in events}
        try:
            guard()
        except BaseException:
            self.pending.clear()  # No physical call occurred.
            raise
        sent = self._send(events)
        self.pending.clear()
        for vk, scan, flags in events[:sent]:
            identity = (vk, scan, flags & ~2)
            if flags & 2:
                self.pending.discard(identity)
            else:
                self.pending.add(identity)
        if sent != len(events):
            raise RuntimeError("native text was only partially admitted")
        return True

    def release(self):
        if self.release_error is not None:
            raise self.release_error
        if not self.pending:
            return
        events = [(vk, scan, flags | 2) for vk, scan, flags in sorted(self.pending)]
        try:
            if self._send(events) != len(events):
                raise RuntimeError("native text release was only partially admitted")
        except BaseException as error:
            self.release_error = error
            raise
        self.pending.clear()


class QuartzNativeText:
    def __init__(self, quartz):
        self.quartz = quartz
        self.pending = None
        self.release_error = None

    def _event(self, keycode, down, char):
        q = self.quartz
        event = q.CGEventCreateKeyboardEvent(None, keycode, down)
        if event is None:
            raise RuntimeError("native text event allocation failed")
        q.CGEventSetFlags(event, 0)
        if char is not None:
            q.CGEventKeyboardSetUnicodeString(event, len(utf16_units(char)), char)
        return event

    def write(self, text, guard):
        if self.pending is not None or self.release_error is not None:
            raise RuntimeError("native text has unjoined input")
        q = self.quartz
        for char in text:
            guard()
            keycode = {"\n": 36, "\t": 48, "\b": 51}.get(char, 0)
            value = None if char in "\n\t\b" else char
            down = self._event(keycode, True, value)
            up = self._event(keycode, False, value)
            guard()
            self.pending = up  # Retain the exact release before any post.
            q.CGEventPost(q.kCGHIDEventTap, down)
            q.CGEventPost(q.kCGHIDEventTap, up)
            self.pending = None
        return True

    def release(self):
        if self.release_error is not None:
            raise self.release_error
        if self.pending is None:
            return
        try:
            self.quartz.CGEventPost(self.quartz.kCGHIDEventTap, self.pending)
        except BaseException as error:
            self.release_error = error
            raise
        self.pending = None


class X11NativeText:
    """Committed Unicode on the existing Linux/WSLg X11 input surface.

    The dedicated connection and possible key/map mutation remain owned on
    cleanup failure. No compose shortcut, clipboard, focus change or installed
    layout selection is used. Pure Wayland without X11 is outside this port.
    """
    def __init__(self, display, fake_input):
        self.display, self.fake_input = display, fake_input
        self.keycode = None
        self.original = None
        self.mapped = None
        self.pending = False
        self.closed = False
        self.release_error = None
        self._sdk_error = None
        self._handler_installed = False

    def _error(self, error, request):
        self._sdk_error = True

    def _request(self, operation):
        self._sdk_error = None
        operation()
        self.display.sync()
        if self._sdk_error:
            raise RuntimeError("X11 native text request failed")

    def _prepare(self):
        if not self._handler_installed:
            self.display.set_error_handler(self._error)
            self._handler_installed = True
        if self.keycode is not None:
            return
        if not self.display.has_extension("XTEST"):
            raise RuntimeError("X11 committed text requires XTEST")
        info = self.display.display.info
        first, last = info.min_keycode, info.max_keycode
        if not 8 <= first <= last <= 255:
            raise RuntimeError("invalid X11 keycode bounds")
        rows = self.display.get_keyboard_mapping(first, last - first + 1)
        pressed = self.display.query_keymap()
        if len(rows) != last - first + 1 or len(pressed) != 32:
            raise RuntimeError("invalid X11 keyboard state")
        for index in range(len(rows) - 1, -1, -1):
            row, code = tuple(rows[index]), first + index
            if 1 <= len(row) <= 32 and not any(row) and not pressed[code // 8] & (1 << (code % 8)):
                self.keycode, self.original = code, row
                return
        raise RuntimeError("X11 committed text has no unused keycode")

    def _restore(self):
        errors = []
        if self.pending:
            try:
                self._request(lambda: self.fake_input(self.display, 3, self.keycode))  # KeyRelease
                self.pending = False
            except BaseException as error:
                errors.append(error)
        if self.mapped is not None:
            try:
                row = tuple(self.display.get_keyboard_mapping(self.keycode, 1)[0])
                if row == self.mapped:
                    self._request(lambda: self.display.change_keyboard_mapping(self.keycode, [self.original]))
                    if tuple(self.display.get_keyboard_mapping(self.keycode, 1)[0]) != self.original:
                        raise RuntimeError("X11 text mapping restoration was not acknowledged")
                # Another actor replaced the borrowed mapping. Its current
                # mapping is authoritative; never overwrite it on cleanup.
                self.mapped = None
            except BaseException as error:
                errors.append(error)
        if errors:
            self.release_error = errors[0]
            raise self.release_error

    def write(self, text, guard):
        if self.closed or self.pending or self.mapped is not None or self.release_error is not None:
            raise RuntimeError("native text has unjoined input")
        guard()
        self._prepare()
        for char in text:
            guard()
            row = tuple(self.display.get_keyboard_mapping(self.keycode, 1)[0])
            pressed = self.display.query_keymap()
            if row != self.original or pressed[self.keycode // 8] & (1 << (self.keycode % 8)):
                raise RuntimeError("X11 text keycode is no longer unused")
            # Modifier state belongs to other physical/lease input. A committed
            # string must not turn into Ctrl/Alt shortcuts or alter that state.
            if self.display.screen().root.query_pointer().mask & (1 | 4 | 8 | 32 | 64 | 128):
                raise RuntimeError("release modifiers before committing X11 text")
            symbol = {"\n": 0xff0d, "\t": 0xff09, "\b": 0xff08}.get(char)
            if symbol is None:
                codepoint = ord(char)
                symbol = codepoint if codepoint <= 255 else 0x01000000 | codepoint
            self.mapped = (symbol,) * len(self.original)
            try:
                guard()
                self._request(lambda: self.display.change_keyboard_mapping(self.keycode, [self.mapped]))
                guard()
                self.pending = True
                self._request(lambda: self.fake_input(self.display, 2, self.keycode))  # KeyPress
                guard()
            finally:
                self._restore()
        return True

    def release(self):
        if self.release_error is not None:
            raise self.release_error
        if self.closed:
            return
        self._restore()
        try:
            self.display.close()
        except BaseException as error:
            self.release_error = error
            raise
        self.closed = True


def _load_x11_text_port():
    from Xlib import display
    from Xlib.ext import xtest
    return X11NativeText(display.Display(), xtest.fake_input)


class LegacyLayoutNativeText:
    """Retain Linux/WSL ASCII behavior; never silently strip Unicode text.

    Other unsupported desktops reject the entire unsupported string before
    writing its ASCII prefix. Legacy generations keep their own filter.
    """
    def __init__(self, backend):
        self.backend = backend
        self.pending = None
        self.release_error = None

    def write(self, text, guard):
        if any(ord(char) > 127 for char in text):
            raise RuntimeError("native Unicode text is unavailable on this desktop input backend")
        if self.pending is not None or self.release_error is not None:
            raise RuntimeError("native text has unjoined input")
        for char in text:
            guard()
            key = {"\n": "enter", "\t": "tab", "\b": "backspace"}.get(char, char)
            self.pending = key
            self.backend.press(key)
            self.pending = None
        return True

    def release(self):
        if self.release_error is not None:
            raise self.release_error
        if self.pending is None:
            return
        try:
            self.backend.keyUp(self.pending)
        except BaseException as error:
            self.release_error = error
            raise
        self.pending = None


def load_native_text_port(backend):
    if os.environ.get("AUTOYOU_TEST_ROOT"):
        raise RuntimeError("physical native text is disabled in a test root")
    if sys.platform == "win32":
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        send = user32.SendInput
        send.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Input), ctypes.c_int]
        send.restype = ctypes.c_uint32
        return WindowsNativeText(send)
    if sys.platform == "darwin":
        import Quartz
        return QuartzNativeText(Quartz)
    if sys.platform == "linux" and os.environ.get("DISPLAY"):
        return _load_x11_text_port()
    return LegacyLayoutNativeText(backend)
