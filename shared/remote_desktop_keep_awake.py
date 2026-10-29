# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Keep the host display awake so an idle timeout does not lock it mid-session.

This is the fallback for hosts where the secure desktop helper is not
installed: it cannot unlock a computer that is already locked, but it stops
Windows and macOS from sleeping the display, starting the screen saver, or
locking for inactivity while Remote Desktop is in use (or always, if chosen).

Windows: ``SetThreadExecutionState`` (display + system required) held by one
long-lived thread, plus a zero-distance mouse event when the idle timer gets
close to a typical lock timeout, which resets the "machine inactivity limit".
macOS: a ``caffeinate -d -i`` child that dies with the server, plus a periodic
``caffeinate -u`` user-activity assertion that resets the idle timer.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

LOGGER = logging.getLogger(__name__)

KEEP_AWAKE_MODES = ("off", "in_use", "always")
DEFAULT_KEEP_AWAKE_MODE = "off"
# "In use" stays active this long after the last captured frame or input.
IN_USE_GRACE_SECONDS = 120.0
_TICK_SECONDS = 20.0
# Reset the idle timer once the host has been idle this long. Windows' lowest
# lock and screen-saver choices are one minute, so stay under that.
_IDLE_RESET_AFTER_SECONDS = 45.0

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


def normalize_keep_awake_mode(raw_value: Any, default: str = DEFAULT_KEEP_AWAKE_MODE) -> str:
    if raw_value is True:
        return "in_use"
    if raw_value is False or raw_value is None:
        return default if raw_value is None else "off"
    value = str(raw_value).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": default,
        "0": "off",
        "false": "off",
        "never": "off",
        "disabled": "off",
        "1": "in_use",
        "true": "in_use",
        "on": "in_use",
        "while_in_use": "in_use",
        "during_control": "in_use",
        "session": "in_use",
        "always_on": "always",
    }
    value = aliases.get(value, value)
    return value if value in KEEP_AWAKE_MODES else default


def keep_awake_supported(platform_name: Optional[str] = None) -> bool:
    name = platform_name or sys.platform
    if name == "win32":
        return True
    if name == "darwin":
        return bool(shutil.which("caffeinate"))
    return False


class RemoteDesktopKeepAwake:
    """Process-wide keep-awake controller. Thread-safe; never raises."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._mode = DEFAULT_KEEP_AWAKE_MODE
        self._last_use = float("-inf")
        self._thread: Optional[threading.Thread] = None
        self._active = False
        self._caffeinate: Optional[subprocess.Popen] = None
        self._last_error = ""

    # -- public API ---------------------------------------------------------
    def configure(self, mode: Any) -> None:
        normalized = normalize_keep_awake_mode(mode)
        with self._lock:
            changed = normalized != self._mode
            self._mode = normalized
            if normalized != "off":
                self._ensure_thread_locked()
        if changed:
            self._wake.set()

    def touch(self) -> None:
        """Record Remote Desktop activity (a captured frame or an input event)."""
        with self._lock:
            self._last_use = self._clock()
            if self._mode == "in_use":
                self._ensure_thread_locked()
                if not self._active:
                    self._wake.set()

    def should_be_active(self) -> bool:
        with self._lock:
            return self._should_be_active_locked()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "mode": self._mode,
                "active": bool(self._active),
                "supported": keep_awake_supported(),
                "error": self._last_error,
            }

    # -- internals ----------------------------------------------------------
    def _should_be_active_locked(self) -> bool:
        if self._mode == "always":
            return True
        if self._mode == "in_use":
            return (self._clock() - self._last_use) <= IN_USE_GRACE_SECONDS
        return False

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not keep_awake_supported():
            return
        self._thread = threading.Thread(target=self._run, name="autoyou-keep-awake", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                want = self._should_be_active_locked()
                mode = self._mode
            try:
                if want:
                    self._hold()
                elif self._active:
                    self._release()
            except Exception as exc:  # pragma: no cover - platform specific
                with self._lock:
                    self._last_error = str(exc)[:200]
                LOGGER.debug("Keep-awake tick failed: %s", exc)
            if mode == "off" and not self._active:
                with self._lock:
                    if self._mode == "off":
                        self._thread = None
                        return
            self._wake.wait(_TICK_SECONDS)
            self._wake.clear()

    def _hold(self) -> None:
        if sys.platform == "win32":
            _windows_hold_awake()
            _windows_reset_idle_if_needed()
        elif sys.platform == "darwin":
            self._macos_hold_awake()
        with self._lock:
            if not self._active:
                LOGGER.info("Keeping this computer awake for Remote Desktop (mode=%s).", self._mode)
            self._active = True
            self._last_error = ""

    def _release(self) -> None:
        if sys.platform == "win32":
            _windows_release_awake()
        elif sys.platform == "darwin":
            self._macos_release_awake()
        with self._lock:
            self._active = False
        LOGGER.info("Remote Desktop keep-awake released.")

    def _macos_hold_awake(self) -> None:
        proc = self._caffeinate
        if proc is None or proc.poll() is not None:
            # -w ties the assertion to this server process, so a crash never
            # leaves the Mac unable to sleep.
            self._caffeinate = subprocess.Popen(
                ["caffeinate", "-d", "-i", "-w", str(os.getpid())],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        # A short user-activity assertion resets the idle timer, which is what
        # starts the screen saver and its "require password" lock.
        subprocess.run(
            ["caffeinate", "-u", "-t", "1"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )

    def _macos_release_awake(self) -> None:
        proc, self._caffeinate = self._caffeinate, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


def _windows_hold_awake() -> None:
    import ctypes

    ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
        _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
    )


def _windows_release_awake() -> None:
    import ctypes

    ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)  # type: ignore[attr-defined]


def _windows_idle_seconds() -> Optional[float]:
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):  # type: ignore[attr-defined]
        return None
    now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF  # type: ignore[attr-defined]
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


def _windows_reset_idle_if_needed() -> None:
    idle = _windows_idle_seconds()
    if idle is None or idle < _IDLE_RESET_AFTER_SECONDS:
        return
    import ctypes

    # A relative mouse move of zero pixels counts as input for the idle timer
    # without moving the pointer or typing anything into the focused app.
    mouse_event = ctypes.windll.user32.mouse_event  # type: ignore[attr-defined]
    mouse_event(0x0001, 0, 0, 0, 0)  # MOUSEEVENTF_MOVE


REMOTE_DESKTOP_KEEP_AWAKE = RemoteDesktopKeepAwake()
