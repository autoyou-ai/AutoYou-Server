# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Shared protocol and host dispatch helpers for native remote keyboards.

The browser only requests the keyboard.  Native clients send the resulting
text/key events back through the authenticated WebRTC voice-control channel.
Keeping the small protocol here prevents the server and remote-desktop agent
from drifting while leaving the existing HTTP input endpoint untouched.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def ensure_x11_authority_exists() -> None:
    """Best-effort fix for python-xlib hard-failing when no Xauthority file
    exists at all, even on X servers (WSLg, most Xvfb setups) that don't
    actually enforce authentication for local connections.

    ``pyautogui``/``mouseinfo`` use python-xlib, which raises
    ``Xlib.error.XauthError`` and aborts the whole import if
    ``$XAUTHORITY``/``~/.Xauthority`` is missing -- unlike ``mss`` or plain
    libX11, which fall back to no-auth gracefully. A fresh Linux/WSL host
    with no display manager typically has no Xauthority file at all, so this
    silently breaks every pyautogui call (caught by the callers' broad
    ``except Exception`` and reported as "not applied", not as this specific
    cause). Never overwrites an existing file. Linux only; a no-op elsewhere.
    """

    if sys.platform != "linux":
        return
    try:
        target = os.environ.get("XAUTHORITY") or str(Path.home() / ".Xauthority")
        path = Path(target)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600, exist_ok=True)
    except Exception:
        pass


def load_pyautogui() -> Any:
    """Import PyAutoGUI without letting an incomplete OpenCV install block input."""

    ensure_x11_authority_exists()
    try:
        import cv2  # type: ignore[import-not-found]
        if not getattr(cv2, "__version__", None):
            # PyScreeze selects its Pillow matcher for OpenCV versions below 3.
            # A namespace-only cv2 package has no usable matcher at all.
            cv2.__version__ = "0.0.0"
    except Exception:
        pass
    import pyautogui  # type: ignore[import-not-found]

    return pyautogui


REMOTE_DESKTOP_KEYBOARD_EVENT = "remote_desktop_keyboard"
REMOTE_DESKTOP_KEYBOARD_SOURCE = "remote_desktop_agent"
MAX_CONTROL_ID_LENGTH = 128
MAX_TEXT_LENGTH = 256
MAX_KEY_LENGTH = 64
ALLOWED_ACTIONS = frozenset({"show", "hide", "input", "key", "state"})
ALLOWED_KEY_PHASES = frozenset({"down", "up", "press"})
ALLOWED_KEYBOARD_STATES = frozenset({"visible", "hidden"})

# Keep this map aligned with the browser/HTTP remote-desktop path.
KEY_MAP = {
    "backspace": "backspace",
    "tab": "tab",
    "enter": "enter",
    "shift": "shift",
    "command": "command",
    "cmd": "command",
    "windows": "win",
    "win": "win",
    "fn": "fn",
    "control": "ctrl",
    "alt": "alt",
    "pause": "pause",
    "capslock": "capslock",
    "escape": "esc",
    "space": "space",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "end": "end",
    "home": "home",
    "arrowleft": "left",
    "arrowup": "up",
    "arrowright": "right",
    "arrowdown": "down",
    "insert": "insert",
    "delete": "delete",
}


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


# iOS/Android autocorrect routinely substitutes these for plain ASCII while
# typing normally (e.g. "don't" -> "don't" with a curly apostrophe). Normalize
# them to ASCII instead of letting them fall through to the ord()>127 strip
# below, so ordinary typing isn't degraded by autocorrect.
_SMART_PUNCTUATION_MAP = {
    "‘": "'", "’": "'", "‚": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "−": "-",
    "…": "...",
}


def _normalize_typed_text(text: str) -> str:
    for smart, plain in _SMART_PUNCTUATION_MAP.items():
        if smart in text:
            text = text.replace(smart, plain)
    # pyautogui.write() can only reliably type ASCII; drop anything else
    # (emoji, accented letters, CJK, ...) instead of failing the whole batch -
    # a partial type-through beats one bad character silently discarding an
    # entire sentence.
    return "".join(char for char in text if char in "\n\t\b" or ord(char) <= 127)


def normalize_remote_desktop_keyboard_payload(payload: Any) -> Optional[Dict[str, Any]]:
    """Return a bounded protocol payload, or ``None`` when it is invalid."""

    if not isinstance(payload, dict):
        return None
    action = _bounded_text(payload.get("action"), 16).lower()
    if action not in ALLOWED_ACTIONS:
        return None

    normalized: Dict[str, Any] = {
        "event": REMOTE_DESKTOP_KEYBOARD_EVENT,
        "action": action,
    }
    control_id = _bounded_text(payload.get("control_id"), MAX_CONTROL_ID_LENGTH)
    if control_id:
        normalized["control_id"] = control_id

    for field, limit in (("source", 64), ("platform", 32)):
        value = _bounded_text(payload.get(field), limit)
        if value:
            normalized[field] = value

    if action == "input":
        text = payload.get("text")
        if not isinstance(text, str) or not text or len(text) > MAX_TEXT_LENGTH:
            return None
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if any(ord(char) < 32 and char not in "\n\t\b" for char in text):
            return None
        text = _normalize_typed_text(text)[:MAX_TEXT_LENGTH]
        if not text:
            return None
        normalized["text"] = text
    elif action == "key":
        key = _bounded_text(payload.get("key"), MAX_KEY_LENGTH).lower()
        phase = _bounded_text(payload.get("phase") or "press", 8).lower()
        if not key or phase not in ALLOWED_KEY_PHASES:
            return None
        normalized["key"] = key
        normalized["phase"] = phase
    elif action == "state":
        keyboard_state = _bounded_text(payload.get("keyboard_state"), 16).lower()
        if keyboard_state not in ALLOWED_KEYBOARD_STATES:
            return None
        normalized["keyboard_state"] = keyboard_state

    return normalized


def _mapped_key(key: str) -> str:
    normalized = str(key or "").strip().lower()
    if len(normalized) >= 2 and normalized.startswith("f") and normalized[1:].isdigit():
        return normalized
    return KEY_MAP.get(normalized, normalized)


STICKY_MODIFIER_KEYS = ("ctrl", "alt", "shift", "command", "win", "fn")


def release_stuck_modifiers(pyautogui: Any = None) -> None:
    """Best-effort keyUp for every sticky modifier the on-screen toolbar can hold down.

    Modifier chips in the native-keyboard toolbar send a keyDown when toggled on
    and a matching keyUp when a combo fires or the overlay closes.
    If a "hide" is processed by the server (lease cleared) before that client
    keyUp arrives - e.g. the server-initiated hide from the website races the
    client's own cleanup - the keyUp gets rejected for lacking a valid lease
    and the modifier is left held down on the real host. Call this whenever a
    keyboard lease is cleared so the host never ends up in that state. Safe to
    call unconditionally (releasing a modifier that isn't down is a no-op) and
    never raises.
    """

    if pyautogui is None:
        try:
            pyautogui_module = load_pyautogui()
        except Exception:
            return
        pyautogui = pyautogui_module

    try:
        pyautogui.FAILSAFE = False
    except Exception:
        pass
    for key in STICKY_MODIFIER_KEYS:
        try:
            pyautogui.keyUp(key)
        except Exception:
            pass


def execute_remote_desktop_keyboard(payload: Any, pyautogui: Any = None) -> bool:
    """Apply one validated native keyboard event to the local desktop.

    ``pyautogui`` is intentionally imported lazily so importing the shared
    protocol remains safe on server builds without desktop input packages.
    """

    normalized = normalize_remote_desktop_keyboard_payload(payload)
    if normalized is None or normalized["action"] in {"show", "hide", "state"}:
        return False

    if pyautogui is None:
        try:
            pyautogui_module = load_pyautogui()
        except Exception:
            return False
        pyautogui = pyautogui_module

    try:
        pyautogui.FAILSAFE = False
        if normalized["action"] == "input":
            text = normalized["text"]
            if any(
                char not in "\n\t\b" and (not char.isprintable() or ord(char) > 127)
                for char in text
            ):
                return False
            write = getattr(pyautogui, "write", None)
            if not callable(write):
                return False
            # Ordinary typed text is only ever sent through the invisible text
            # field, never through the toolbar's Ctrl/Alt/Shift combo buttons.
            # If a chip was armed (keyDown) and the user types instead of
            # pressing a toolbar key, release it first so a normal letter
            # can't land as an unintended host shortcut (e.g. armed Ctrl + "w").
            release_stuck_modifiers(pyautogui)
            for char in text:
                if char == "\n":
                    pyautogui.press("enter")
                elif char == "\t":
                    pyautogui.press("tab")
                elif char == "\b":
                    pyautogui.press("backspace")
                else:
                    write(char)
            return True

        mapped_key = _mapped_key(normalized["key"])
        phase = normalized["phase"]
        if phase == "down":
            pyautogui.keyDown(mapped_key)
        elif phase == "up":
            pyautogui.keyUp(mapped_key)
        else:
            pyautogui.press(mapped_key)
        return True
    except Exception:
        return False
