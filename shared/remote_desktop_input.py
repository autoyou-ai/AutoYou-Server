# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-746f20706179203130252061-f75758e3b7c59d32ad427d14

"""Validated native mouse and touch dispatch for WebRTC remote desktop control.

The mobile clients send normalized coordinates over the authenticated WebRTC
voice-control channel. The active outbound video track owns the mapping from
the stitched video canvas back to the selected desktop monitor, so camera,
file, and realtime-video panes cannot skew click coordinates.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-746f20706179203130252061-f75758e3b7c59d32ad427d14"


from typing import Any, Dict, Iterable, Optional, Tuple

from .remote_desktop_keyboard import execute_remote_desktop_keyboard, load_pyautogui, release_stuck_modifiers


REMOTE_DESKTOP_CONTROL_EVENT = "remote_desktop_control"
REMOTE_DESKTOP_INPUT_EVENT = "remote_desktop_input"
MAX_CONTROL_ID_LENGTH = 128
ALLOWED_CONTROL_ACTIONS = frozenset({"start", "stop"})
ALLOWED_INPUT_TYPES = frozenset({"move", "button", "scroll"})
ALLOWED_COORDINATE_MODES = frozenset({"absolute", "relative"})
ALLOWED_BUTTONS = frozenset({"left", "right", "middle"})
ALLOWED_BUTTON_PHASES = frozenset({"click", "double_click", "down", "up"})


def _bounded_string(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _bounded_float(value: Any, minimum: float, maximum: float) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return max(minimum, min(maximum, number))


def normalize_remote_desktop_control_payload(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    action = _bounded_string(payload.get("action"), 16).lower()
    control_id = _bounded_string(payload.get("control_id"), MAX_CONTROL_ID_LENGTH)
    if action not in ALLOWED_CONTROL_ACTIONS or not control_id:
        return None
    fullscreen = payload.get("fullscreen")
    if action == "start" and fullscreen is not True:
        return None
    normalized: Dict[str, Any] = {
        "event": REMOTE_DESKTOP_CONTROL_EVENT,
        "action": action,
        "control_id": control_id,
    }
    platform = _bounded_string(payload.get("platform"), 32)
    if platform:
        normalized["platform"] = platform
    source = _bounded_string(payload.get("source"), 64)
    if source:
        normalized["source"] = source
    if action == "start":
        normalized["fullscreen"] = True
    touch_mode = _bounded_string(payload.get("touch_mode"), 32).lower()
    if touch_mode in {"direct", "relative"}:
        normalized["touch_mode"] = touch_mode
    return normalized


def normalize_remote_desktop_input_payload(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    control_id = _bounded_string(payload.get("control_id"), MAX_CONTROL_ID_LENGTH)
    input_type = _bounded_string(payload.get("input_type"), 20).lower()
    if not control_id or input_type not in ALLOWED_INPUT_TYPES:
        return None

    normalized: Dict[str, Any] = {
        "event": REMOTE_DESKTOP_INPUT_EVENT,
        "control_id": control_id,
        "input_type": input_type,
    }
    platform = _bounded_string(payload.get("platform"), 32)
    if platform:
        normalized["platform"] = platform
    source = _bounded_string(payload.get("source"), 64)
    if source:
        normalized["source"] = source

    if input_type == "move":
        coordinate_mode = _bounded_string(payload.get("coordinate_mode") or "absolute", 16).lower()
        if coordinate_mode not in ALLOWED_COORDINATE_MODES:
            return None
        normalized["coordinate_mode"] = coordinate_mode
        if coordinate_mode == "absolute":
            x = _bounded_float(payload.get("x"), 0.0, 1.0)
            y = _bounded_float(payload.get("y"), 0.0, 1.0)
            if x is None or y is None:
                return None
            normalized.update({"x": x, "y": y})
        else:
            dx = _bounded_float(payload.get("dx"), -0.5, 0.5)
            dy = _bounded_float(payload.get("dy"), -0.5, 0.5)
            if dx is None or dy is None:
                return None
            normalized.update({"dx": dx, "dy": dy})
        return normalized

    if input_type == "button":
        button = _bounded_string(payload.get("button") or "left", 12).lower()
        phase = _bounded_string(payload.get("phase") or "click", 20).lower().replace("-", "_")
        if button not in ALLOWED_BUTTONS or phase not in ALLOWED_BUTTON_PHASES:
            return None
        normalized.update({"button": button, "phase": phase})
        x = _bounded_float(payload.get("x"), 0.0, 1.0)
        y = _bounded_float(payload.get("y"), 0.0, 1.0)
        if (x is None) != (y is None):
            return None
        if x is not None and y is not None:
            normalized.update({"x": x, "y": y})
        return normalized

    dx = _bounded_float(payload.get("dx") or 0.0, -100.0, 100.0)
    dy = _bounded_float(payload.get("dy") or 0.0, -100.0, 100.0)
    if dx is None or dy is None or (abs(dx) < 0.001 and abs(dy) < 0.001):
        return None
    normalized.update({"dx": dx, "dy": dy})
    return normalized


def _control_backend(pyautogui: Any = None) -> Any:
    if pyautogui is not None:
        return pyautogui
    return load_pyautogui()


# Importing this module only proves the protocol is present. The backend that
# actually moves the host pointer (pyautogui plus a reachable display) is
# imported lazily, so a server can advertise control it cannot perform. The
# probe below resolves that once and is cached, because callers use it on the
# capability path where a repeated import would be wasteful.
_BACKEND_PROBE: Dict[str, Any] = {"probed": False, "available": False}
_REQUIRED_BACKEND_CALLS = ("moveTo", "moveRel", "click", "mouseDown", "mouseUp")


def remote_desktop_input_backend_available(*, refresh: bool = False) -> bool:
    """Return whether native pointer input can actually run on this host.

    Blocking: imports pyautogui on the first call. Callers on an event loop
    must dispatch it to a thread.
    """
    if refresh or not _BACKEND_PROBE["probed"]:
        available = False
        try:
            backend = _control_backend(None)
            available = all(
                callable(getattr(backend, name, None)) for name in _REQUIRED_BACKEND_CALLS
            )
            if available:
                # Reading the cursor is the cheapest call that still needs a
                # reachable display, so a headless host fails here instead of
                # on the operator's first tap.
                position = getattr(backend, "position", None)
                if callable(position):
                    position()
        except Exception:
            available = False
        _BACKEND_PROBE["available"] = bool(available)
        _BACKEND_PROBE["probed"] = True
    return bool(_BACKEND_PROBE["available"])


def remote_desktop_input_backend_probed() -> Optional[bool]:
    """Cached probe result, or ``None`` when the backend was never probed."""
    if not _BACKEND_PROBE["probed"]:
        return None
    return bool(_BACKEND_PROBE["available"])


def _track_bounds(track: Any) -> Optional[Dict[str, int]]:
    mapping_reader = getattr(track, "remote_desktop_mapping", None)
    if not callable(mapping_reader):
        return None
    try:
        mapping = mapping_reader()
    except Exception:
        return None
    bounds = mapping.get("monitor_bounds") if isinstance(mapping, dict) else None
    if not isinstance(bounds, dict):
        return None
    try:
        return {
            "left": int(bounds.get("left") or 0),
            "top": int(bounds.get("top") or 0),
            "width": max(1, int(bounds.get("width") or 1)),
            "height": max(1, int(bounds.get("height") or 1)),
        }
    except Exception:
        return None


def _absolute_point(track: Any, payload: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    if "x" not in payload or "y" not in payload:
        return None
    mapper = getattr(track, "map_output_point_to_desktop", None)
    if not callable(mapper):
        return None
    try:
        point = mapper(payload["x"], payload["y"])
    except Exception:
        return None
    if not isinstance(point, tuple) or len(point) != 2:
        return None
    return int(point[0]), int(point[1])


def execute_remote_desktop_input(payload: Any, *, track: Any, pyautogui: Any = None) -> bool:
    normalized = normalize_remote_desktop_input_payload(payload)
    if normalized is None or track is None:
        return False
    try:
        backend = _control_backend(pyautogui)
        try:
            backend.FAILSAFE = False
            backend.PAUSE = 0
        except Exception:
            pass

        input_type = normalized["input_type"]
        if input_type == "move":
            if normalized["coordinate_mode"] == "absolute":
                point = _absolute_point(track, normalized)
                if point is None:
                    return False
                backend.moveTo(point[0], point[1])
                return True
            bounds = _track_bounds(track)
            if bounds is None:
                return False
            delta_x = int(round(float(normalized["dx"]) * bounds["width"]))
            delta_y = int(round(float(normalized["dy"]) * bounds["height"]))
            if delta_x == 0 and delta_y == 0:
                return True
            backend.moveRel(delta_x, delta_y)
            return True

        if input_type == "button":
            point = _absolute_point(track, normalized)
            if ("x" in normalized or "y" in normalized) and point is None:
                return False
            if point is not None:
                backend.moveTo(point[0], point[1])
            button = normalized["button"]
            phase = normalized["phase"]
            if phase == "down":
                backend.mouseDown(button=button)
            elif phase == "up":
                backend.mouseUp(button=button)
            elif phase == "double_click":
                backend.doubleClick(button=button)
            else:
                backend.click(button=button)
            return True

        # Mobile deltas use the browser convention where positive Y means
        # scrolling down. pyautogui uses positive values for scrolling up.
        vertical = int(round(-float(normalized["dy"])))
        horizontal = int(round(float(normalized["dx"])))
        if vertical:
            backend.scroll(vertical)
        if horizontal and callable(getattr(backend, "hscroll", None)):
            backend.hscroll(horizontal)
        return True
    except Exception:
        return False


def release_remote_desktop_inputs(
    held_buttons: Iterable[str] = (),
    *,
    held_keys: Iterable[str] = (),
    pyautogui: Any = None,
) -> None:
    try:
        backend = _control_backend(pyautogui)
    except Exception:
        return
    try:
        backend.FAILSAFE = False
    except Exception:
        pass
    for button in set(held_buttons or ()):
        if button not in ALLOWED_BUTTONS:
            continue
        try:
            backend.mouseUp(button=button)
        except Exception:
            pass
    for key in set(held_keys or ()):
        execute_remote_desktop_keyboard({"action": "key", "key": key, "phase": "up"}, pyautogui=backend)
    release_stuck_modifiers(backend)
