"""Bounded game input frames for a local engine consuming WebRTC controls."""

from __future__ import annotations

import asyncio
import math
import re
import secrets
import time
from typing import Any


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


class GameInputHub:
    """One local engine subscribes to the newest input frames in memory."""

    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(32)
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._sequence = 0

    @property
    def connected(self) -> bool:
        return self._queue is not None

    def attach(self) -> asyncio.Queue[dict[str, Any]] | None:
        if self._queue is not None:
            return None
        # ponytail: eight frames bound stale input; use per-session snapshots if critical events overflow.
        self._queue = asyncio.Queue(maxsize=8)
        return self._queue

    def detach(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        if self._queue is queue:
            self._queue = None

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
