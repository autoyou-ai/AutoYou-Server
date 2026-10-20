# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-J-7920636f6e74726962757469-006c28ebb6e4f1d8abed29b9

"""Bounded game input frames for a local engine consuming WebRTC controls."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
# from __debug_trace_j__ import autoyou_provenance_j_7920636f6e74726962757469_006c28ebb6e4f1d8abed29b9
__license__ = "AutoYou Source-Available License (see LICENSE)"

import asyncio
import math
import re
import secrets
import time
from typing import Any

__debug_trace_j__ = "AUTOYOU-PROVENANCE-J-7920636f6e74726962757469-006c28ebb6e4f1d8abed29b9"


_NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,31}\Z")


def _number(value: Any, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and minimum <= result <= maximum else None


def normalize_game_input(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    control_id = payload.get("control_id")
    kind = payload.get("input_type")
    if not isinstance(control_id, str) or not 1 <= len(control_id) <= 128:
        return None
    frame: dict[str, Any] = {"event": "game_input", "control_id": control_id}
    if kind == "touch":
        points = payload.get("points")
        if not isinstance(points, list) or len(points) > 10:
            return None
        clean = []
        seen = set()
        for point in points:
            if not isinstance(point, dict) or type(point.get("id")) is not int:
                return None
            identifier = point["id"]
            x = _number(point.get("x"), 0, 1)
            y = _number(point.get("y"), 0, 1)
            if not 0 <= identifier <= 65535 or identifier in seen or x is None or y is None:
                return None
            clean.append({"id": identifier, "x": x, "y": y})
            seen.add(identifier)
        frame.update(input_type="touch", points=clean)
    elif kind == "sensor":
        sensor = payload.get("sensor")
        if sensor not in {"accelerometer", "gyroscope"}:
            return None
        values = [_number(payload.get(axis), -100, 100) for axis in ("x", "y", "z")]
        if any(value is None for value in values):
            return None
        frame.update(input_type="sensor", sensor=sensor, x=values[0], y=values[1], z=values[2])
    elif kind == "axis":
        axis = payload.get("axis")
        value = _number(payload.get("value"), -1, 1)
        if not isinstance(axis, str) or not _NAME.fullmatch(axis) or value is None:
            return None
        frame.update(input_type="axis", axis=axis, value=value)
    elif kind == "button":
        button = payload.get("button")
        phase = payload.get("phase")
        if not isinstance(button, str) or not _NAME.fullmatch(button) or phase not in {"down", "up"}:
            return None
        frame.update(input_type="button", button=button, phase=phase)
    elif kind == "heartbeat":
        frame["input_type"] = "heartbeat"
    else:
        return None
    return frame


_BUTTON_NAMES = {
    "action_a": "A", "action_b": "B", "action_x": "X", "action_y": "Y", "jump": "Jump",
    "left": "Left", "right": "Right", "up": "Up", "down": "Down", "start": "Start",
    "select": "Select", "menu": "Menu", "back": "Back", "left_shoulder": "LB", "right_shoulder": "RB",
}
_ARROWS = {"left": "←", "right": "→", "up": "↑", "down": "↓"}
_KEY_NAMES = {
    "arrowleft": "← Left", "arrowright": "→ Right", "arrowup": "↑ Up", "arrowdown": "↓ Down",
    "left": "← Left", "right": "→ Right", "up": "↑ Up", "down": "↓ Down",
    "space": "Space", " ": "Space", "enter": "Enter", "return": "Enter", "escape": "Esc", "esc": "Esc",
    "tab": "Tab", "backspace": "Backspace", "shift": "Shift", "control": "Ctrl", "ctrl": "Ctrl",
    "alt": "Alt", "meta": "Meta",
}
_STICKS = {
    "left_x": ("Left stick", "left", "right"), "left_y": ("Left stick", "up", "down"),
    "right_x": ("Right stick", "left", "right"), "right_y": ("Right stick", "up", "down"),
}


def describe_game_input(
    frame: Any, memory: dict[str, Any] | None = None, labels: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """One readable record for an input a player just made, or None for motion, holds, and releases.

    Each record keeps the machine-readable control (``control``, ``input_type``, ``device``) beside the
    words a person reads (``value``), so the Interact page, an agent, and any other program read the
    same entry. ``memory`` is per player: a stick reports a new direction once, a touch reports a new
    finger once, instead of every movement sample.
    """
    if not isinstance(frame, dict):
        return None
    memory = memory if isinstance(memory, dict) else {}
    event, kind = frame.get("event"), frame.get("input_type")
    if event == "game_input" and kind == "button":
        if frame.get("phase") != "down":
            return None
        name = str(frame.get("button") or "")
        label = (labels or {}).get(name) or _BUTTON_NAMES.get(name.lower()) or name.replace("_", " ").title()
        return {"device": "controller", "input_type": "button", "control": name, "phase": "down", "value": label}
    if event == "game_input" and kind == "axis":
        axis, value = str(frame.get("axis") or ""), frame.get("value")
        direction = 0
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            direction = -1 if value < -0.55 else 1 if value > 0.55 else 0
        if memory.get("axis:" + axis, 0) == direction:
            return None
        memory["axis:" + axis] = direction
        if not direction:
            return None
        stick, negative, positive = _STICKS.get(axis, (axis.replace("_", " ").title(), "minus", "plus"))
        way = negative if direction < 0 else positive
        return {"device": "controller", "input_type": "axis", "control": axis, "direction": way,
                "value": f"{_ARROWS.get(way, '')} {stick}".strip()}
    if event == "game_input" and kind == "touch":
        count = len(frame.get("points") or [])
        previous, memory["touches"] = memory.get("touches", 0), count
        if count <= previous:
            return None
        return {"device": "touch", "input_type": "touch", "control": "touch", "points": count,
                "value": "Tap" if count == 1 else f"{count}-finger touch"}
    if event == "remote_desktop_keyboard":
        action = frame.get("action")
        if action == "key" and frame.get("phase") in {"down", "press"}:
            key = str(frame.get("key") or "")
            readable = _KEY_NAMES.get(key.lower()) or (key.upper() if len(key) == 1 else key.replace("_", " ").title())
            return {"device": "keyboard", "input_type": "key", "control": key, "phase": "down", "value": readable}
        text = frame.get("text")
        if action == "input" and isinstance(text, str) and text.strip():
            # Typed text may be anything; the record says that text arrived, never what it was.
            return {"device": "keyboard", "input_type": "text", "control": "text", "characters": len(text),
                    "value": "Typed text"}
        return None
    if event == "remote_desktop_input" and kind == "button" and frame.get("phase") in {"down", "click"}:
        button = str(frame.get("button") or "left")
        return {"device": "mouse", "input_type": "click", "control": button, "phase": "down",
                "value": f"{button.title()} click"}
    return None


def _motion_key(frame: dict[str, Any]) -> tuple[Any, ...] | None:
    kind = frame.get("input_type")
    if frame.get("event") == "game_input" and kind == "touch":
        return (frame.get("session_id"), frame.get("control_id"), kind,
                tuple(sorted(point["id"] for point in frame["points"])))
    if frame.get("event") == "game_input" and kind in {"axis", "sensor"}:
        return (frame.get("session_id"), frame.get("control_id"), kind,
                frame.get("axis") if kind == "axis" else frame.get("sensor"))
    if frame.get("event") == "remote_desktop_input" and kind == "move" and frame.get("coordinate_mode") == "absolute":
        return (frame.get("session_id"), frame.get("control_id"), kind)
    return None


_OBSERVER_LIMIT = 16


class GameInputHub:
    """One local engine subscribes to the newest input frames in memory.

    Any number of readers, up to a small cap, may also observe: they receive a read-only copy of
    every input a player makes, with its readable record, whether or not an engine is listening.
    Observing never takes input away from the engine or the computer.
    """

    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(32)
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self.owner: str | None = None
        self._sequence = 0
        self._observers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._observed = 0

    @property
    def connected(self) -> bool:
        return self._queue is not None

    @property
    def observer_count(self) -> int:
        return len(self._observers)

    def observe(self, maxsize: int = 64) -> asyncio.Queue[dict[str, Any]] | None:
        if len(self._observers) >= _OBSERVER_LIMIT:
            return None
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._observers.add(queue)
        return queue

    def unobserve(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._observers.discard(queue)

    def notify(self, message: dict[str, Any]) -> None:
        """Copy one observed input to every reader; a slow reader loses its oldest entries."""
        if not self._observers:
            return
        self._observed += 1
        stamped = {**message, "sequence": self._observed, "timestamp_ms": int(time.time() * 1000)}
        for queue in list(self._observers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(stamped)

    def attach(self, owner: str = "engine") -> asyncio.Queue[dict[str, Any]] | None:
        if self._queue is not None:
            return None
        # ponytail: eight frames bound stale input; use per-session snapshots if critical events overflow.
        self._queue = asyncio.Queue(maxsize=8)
        self.owner = owner
        return self._queue

    def detach(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        if self._queue is queue:
            self._queue = None
            self.owner = None

    def publish(self, session_id: str, frame: dict[str, Any]) -> None:
        queue = self._queue
        if queue is None:
            return
        self._sequence += 1
        message = {**frame, "session_id": session_id, "sequence": self._sequence,
                   "timestamp_ms": int(time.time() * 1000)}
        if queue.full():
            pending = []
            while not queue.empty():
                pending.append(queue.get_nowait())
            pending.append(message)
            seen: set[tuple[Any, ...]] = set()
            latest_touch_key = None
            compact = []
            for candidate in reversed(pending):
                key = _motion_key(candidate)
                if key is None:
                    seen.clear()
                    latest_touch_key = None
                elif key[2] == "touch":
                    if latest_touch_key is not None and key != latest_touch_key:
                        seen.clear()
                    latest_touch_key = key
                if key is None or key not in seen:
                    compact.append(candidate)
                    if key is not None:
                        seen.add(key)
            if len(compact) <= queue.maxsize:
                for candidate in reversed(compact):
                    queue.put_nowait(candidate)
                return
            # A stalled engine must clear held controls before newer input.
            self._sequence += 1
            queue.put_nowait({"event": "game_input", "input_type": "state_reset",
                              "session_id": "*", "all_sessions": True, "sequence": self._sequence,
                              "timestamp_ms": int(time.time() * 1000)})
            self._sequence += 1
            message["sequence"] = self._sequence
        queue.put_nowait(message)
