# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-95b7f610bb495a70bbd0db54

"""Shared WebRTC video-call helpers.

This module keeps the iOS video-call POC on real WebRTC media tracks while
also exposing the latest inbound camera frame to managed agent websites.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import contextlib
import json
import logging
import os
import platform
import queue
import re
import select
import subprocess
import textwrap
import threading
import time
from dataclasses import dataclass, replace
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from shared.secure_storage import (
    FILE_HEADER as SPM_FILE_HEADER,
    materialize_secure_file,
    read_secure_file,
    secure_storage_enabled,
    write_secure_file,
)

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-95b7f610bb495a70bbd0db54"


try:  # pragma: no cover - dependency availability varies by runtime package.
    from aiortc import VideoStreamTrack as _AiortcVideoStreamTrack  # type: ignore
    from aiortc.mediastreams import MediaStreamError, MediaStreamTrack  # type: ignore
except Exception:  # pragma: no cover
    _AiortcVideoStreamTrack = object  # type: ignore[assignment]
    class MediaStreamTrack:
        kind = "audio"
    class MediaStreamError(Exception):
        pass

try:  # pragma: no cover - imported in live media runtimes.
    import av as _av  # type: ignore
    from av import VideoFrame, AudioFrame  # type: ignore
except Exception:  # pragma: no cover
    _av = None  # type: ignore[assignment]
    VideoFrame = None  # type: ignore[assignment]
    AudioFrame = None  # type: ignore[assignment]

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]

try:  # pragma: no cover - availability varies by Pillow/runtime build.
    from PIL import ImageGrab, ImageStat
except Exception:  # pragma: no cover
    ImageGrab = None  # type: ignore[assignment]
    ImageStat = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def find_macos_system_audio_helper() -> Optional[Path]:
    """Find the bundled ScreenCaptureKit capture binary on supported Macs."""
    if platform.system() != "Darwin":
        return None
    try:
        if int(platform.mac_ver()[0].split(".")[0]) < 13:
            return None
    except (ValueError, IndexError):
        return None
    from shared.macos_runtime_support import get_runtime_root

    candidates = [get_runtime_root(__file__) / "macos" / "AutoYouAudioCapture"]
    if override := os.getenv("AUTOYOU_MACOS_AUDIO_HELPER"):
        candidates.insert(0, Path(override))
    return next((path for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)
INBOUND_VIDEO_RECORDING_FORMAT = "mp4_video" if _av is not None and VideoFrame is not None else "jpeg_frames"
INBOUND_VIDEO_RECORDING_MODE_VIDEO = "video"
# from __debug_provenance_a__ import schedule
INBOUND_VIDEO_RECORDING_MODE_IMAGES = "images"
DEFAULT_INBOUND_VIDEO_MAX_FPS = 30.0
DEFAULT_INBOUND_VIDEO_IMAGE_INTERVAL_SECONDS = 5.0
_DESKTOP_CAPTURE_MIN_DIMENSION = 10
_DESKTOP_BLANK_CAPTURE_MAX_MEAN = 2.0
_DESKTOP_IMAGEGRAB_FALLBACK_NOTICE_LOGGED = False
_DESKTOP_CAPTURE_THREAD_STATE = threading.local()


def normalize_inbound_video_recording_mode(raw_value: Any, default: str = INBOUND_VIDEO_RECORDING_MODE_VIDEO) -> str:
    normalized = str(raw_value or default or "").strip().lower().replace("-", "_")
    aliases = {
        "mp4": INBOUND_VIDEO_RECORDING_MODE_VIDEO,
        "movie": INBOUND_VIDEO_RECORDING_MODE_VIDEO,
        "movies": INBOUND_VIDEO_RECORDING_MODE_VIDEO,
        "image": INBOUND_VIDEO_RECORDING_MODE_IMAGES,
        "jpeg": INBOUND_VIDEO_RECORDING_MODE_IMAGES,
        "jpg": INBOUND_VIDEO_RECORDING_MODE_IMAGES,
        "photo": INBOUND_VIDEO_RECORDING_MODE_IMAGES,
        "photos": INBOUND_VIDEO_RECORDING_MODE_IMAGES,
        "snapshots": INBOUND_VIDEO_RECORDING_MODE_IMAGES,
        "snapshot": INBOUND_VIDEO_RECORDING_MODE_IMAGES,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {INBOUND_VIDEO_RECORDING_MODE_VIDEO, INBOUND_VIDEO_RECORDING_MODE_IMAGES}:
        return default
    return normalized


def normalize_inbound_video_image_interval_seconds(raw_value: Any, default: float = DEFAULT_INBOUND_VIDEO_IMAGE_INTERVAL_SECONDS) -> float:
    try:
        value = float(raw_value)
    except Exception:
        value = float(default)
    if value <= 0:
        value = float(default)
    return max(1.0, min(3600.0, value))


def inbound_video_recording_format_for_mode(recording_mode: str) -> str:
    mode = normalize_inbound_video_recording_mode(recording_mode)
    if mode == INBOUND_VIDEO_RECORDING_MODE_IMAGES:
        return "jpeg_images"
    return INBOUND_VIDEO_RECORDING_FORMAT


@dataclass(frozen=True)
class LatestVideoFrame:
    session_id: str
    sequence: int
    timestamp_ms: int
    jpeg_bytes: bytes
    width: int
    height: int
    source: str = "ios"


class LatestVideoFrameRegistry:
    """Thread-safe latest-frame registry shared by server and agent websites."""

    def __init__(self, *, max_age_seconds: float = 3.0) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._latest_by_session: Dict[str, LatestVideoFrame] = {}
        self._latest_session_id: Optional[str] = None
        self._sequence = 0
        self.max_age_seconds = max(0.1, float(max_age_seconds or 3.0))

    def _prune_expired_locked(self) -> None:
        cutoff_ms = int(time.time() * 1000) - int(self.max_age_seconds * 1000)
        expired = [
            session_id
            for session_id, frame in self._latest_by_session.items()
            if frame.timestamp_ms < cutoff_ms
        ]
        for session_id in expired:
            self._latest_by_session.pop(session_id, None)
        if self._latest_session_id not in self._latest_by_session:
            self._latest_session_id = next(reversed(self._latest_by_session), None)

    def publish_jpeg(
        self,
        *,
        session_id: str,
        jpeg_bytes: bytes,
        width: int,
        height: int,
        source: str = "ios",
    ) -> LatestVideoFrame:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            normalized_session_id = "unknown"
        with self._condition:
            self._sequence += 1
            frame = LatestVideoFrame(
                session_id=normalized_session_id,
                sequence=self._sequence,
                timestamp_ms=int(time.time() * 1000),
                jpeg_bytes=bytes(jpeg_bytes),
                width=max(1, int(width or 1)),
                height=max(1, int(height or 1)),
                source=str(source or "ios"),
            )
            self._latest_by_session[normalized_session_id] = frame
            self._latest_session_id = normalized_session_id
            self._condition.notify_all()
            return frame

    def alias_session(self, old_session_id: str, new_session_id: str) -> None:
        old_id = str(old_session_id or "").strip()
        new_id = str(new_session_id or "").strip()
        if not old_id or not new_id or old_id == new_id:
            return
        with self._condition:
            frame = self._latest_by_session.get(old_id)
            if frame is not None:
                self._latest_by_session[new_id] = replace(frame, session_id=new_id)
                self._latest_session_id = new_id
                self._condition.notify_all()

    def clear_session(self, session_id: str) -> None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return
        with self._condition:
            self._latest_by_session.pop(normalized_session_id, None)
            if self._latest_session_id == normalized_session_id:
                self._latest_session_id = next(iter(self._latest_by_session.keys()), None)
            self._condition.notify_all()

    def latest(self, session_id: Optional[str] = None) -> Optional[LatestVideoFrame]:
        with self._condition:
            self._prune_expired_locked()
            if session_id:
                return self._latest_by_session.get(str(session_id).strip())
            if self._latest_session_id:
                return self._latest_by_session.get(self._latest_session_id)
            return None

    def status(self) -> Dict[str, Any]:
        with self._condition:
            self._prune_expired_locked()
            latest = self.latest()
            return {
                "active": latest is not None,
                "active_sessions": len(self._latest_by_session),
                "latest_session_id": latest.session_id if latest else None,
                "latest_sequence": latest.sequence if latest else 0,
                "latest_timestamp_ms": latest.timestamp_ms if latest else None,
                "latest_width": latest.width if latest else None,
                "latest_height": latest.height if latest else None,
            }

    def wait_for_frame(
        self,
        *,
        last_sequence: int = 0,
        session_id: Optional[str] = None,
        timeout: float = 1.0,
    ) -> Optional[LatestVideoFrame]:
        deadline = time.monotonic() + max(0.05, float(timeout or 1.0))
        with self._condition:
            while True:
                frame = self.latest(session_id=session_id)
                if frame is not None and frame.sequence > int(last_sequence or 0):
                    return frame
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)


VIDEO_FRAME_REGISTRY = LatestVideoFrameRegistry()


def _sanitize_video_telemetry_detail(detail: Any, max_length: int = 240) -> str:
    text = str(detail or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        text = lines[-1]
    text = re.sub(r"[A-Za-z]:\\[^\s]+", "<path>", text)
    text = re.sub(r"(?<!\w)/(?:Users|home|var|tmp|private|opt)/[^\s]+", "<path>", text)
    if len(text) > max_length:
        text = text[: max_length - 3].rstrip() + "..."
    return text


class OutboundVideoTelemetryRegistry:
    """Memory-only outbound video health counters with sanitized error detail."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._frame_count = 0
        self._error_count = 0
        self._latest_frame: Optional[Dict[str, Any]] = None
        self._latest_error: Optional[Dict[str, Any]] = None

    def record_frame(self, *, source: str, width: int, height: int) -> None:
        with self._lock:
            self._frame_count += 1
            self._latest_frame = {
                "source": str(source or "unknown").strip() or "unknown",
                "width": max(0, int(width or 0)),
                "height": max(0, int(height or 0)),
                "timestamp_ms": int(time.time() * 1000),
                "frame_count": self._frame_count,
            }

    def record_error(self, *, source: str = "unknown", stage: str = "encode", detail: Any = "") -> None:
        with self._lock:
            self._error_count += 1
            self._latest_error = {
                "source": str(source or "unknown").strip() or "unknown",
                "stage": str(stage or "encode").strip() or "encode",
                "detail": _sanitize_video_telemetry_detail(detail),
                "timestamp_ms": int(time.time() * 1000),
                "error_count": self._error_count,
            }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "frame_count": self._frame_count,
                "error_count": self._error_count,
                "latest_frame": dict(self._latest_frame) if self._latest_frame else None,
                "latest_error": dict(self._latest_error) if self._latest_error else None,
            }

    def reset(self) -> None:
        with self._lock:
            self._frame_count = 0
            self._error_count = 0
            self._latest_frame = None
            self._latest_error = None


OUTBOUND_VIDEO_TELEMETRY = OutboundVideoTelemetryRegistry()


class _AiortcVideoSenderTelemetryHandler(logging.Handler):
    _autoyou_outbound_video_telemetry = True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < logging.WARNING:
                return
            message = record.getMessage()
            normalized = message.lower()
            if "rtcrtpsender(video)" not in normalized:
                return
            OUTBOUND_VIDEO_TELEMETRY.record_error(
                source="negotiated",
                stage="rtp_encode",
                detail=message,
            )
        except Exception:
            return


def install_outbound_video_telemetry_logging() -> bool:
    """Attach a non-invasive aiortc video sender warning observer once."""
    sender_logger = logging.getLogger("aiortc.rtcrtpsender")
    for handler in tuple(sender_logger.handlers):
        if not getattr(handler, "_autoyou_outbound_video_telemetry", False):
            continue
        if isinstance(handler, _AiortcVideoSenderTelemetryHandler):
            return False
        sender_logger.removeHandler(handler)
    handler = _AiortcVideoSenderTelemetryHandler(level=logging.WARNING)
    sender_logger.addHandler(handler)
    return True


def outbound_video_telemetry_status() -> Dict[str, Any]:
    return OUTBOUND_VIDEO_TELEMETRY.status()


def _ensure_even_rgb_image(image: Any) -> Any:
    """Return an RGB image with dimensions valid for yuv420p/H.264 encoders."""
    if Image is None:
        raise RuntimeError("Pillow is required for WebRTC video frames")
    if getattr(image, "mode", "RGB") != "RGB":
        image = image.convert("RGB")

    width = max(2, int(getattr(image, "width", 0) or 0))
    height = max(2, int(getattr(image, "height", 0) or 0))
    if width % 2:
        width = width - 1 if width > 2 else width + 1
    if height % 2:
        height = height - 1 if height > 2 else height + 1

    if image.size == (width, height):
        return image
    if width <= image.width and height <= image.height:
        return image.crop((0, 0, width, height))
    return image.resize((width, height), Image.Resampling.BILINEAR)


def _desktop_capture_platform_tag() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system.startswith("win"):
        return "windows"
    return system or "unknown"


def _ensure_windows_interactive_desktop_for_capture() -> None:
    if _desktop_capture_platform_tag() != "windows":
        return
    if getattr(_DESKTOP_CAPTURE_THREAD_STATE, "interactive_desktop_checked", False):
        return
    _DESKTOP_CAPTURE_THREAD_STATE.interactive_desktop_checked = True
    try:
        import ctypes

        user32 = ctypes.windll.user32
        h_winsta = user32.OpenWindowStationW("WinSta0", False, 0x37F)
        if h_winsta:
            user32.SetProcessWindowStation(h_winsta)
        h_desktop = user32.OpenDesktopW("Default", 0, False, 0x1FF)
        if h_desktop and not user32.SetThreadDesktop(h_desktop):
            logger.debug("SetThreadDesktop to WinSta0\\Default failed for desktop video capture.")
    except Exception as exc:
        logger.debug("Windows interactive desktop attach failed for desktop video capture: %s", exc)


_WINDOWS_WTS_CONNECT_STATES = {
    0: "active",
    1: "connected",
    2: "connect_query",
    3: "shadow",
    4: "disconnected",
    5: "idle",
    6: "listen",
    7: "reset",
    8: "down",
    9: "init",
}


def _get_windows_desktop_session_status() -> Optional[Dict[str, Any]]:
    if _desktop_capture_platform_tag() != "windows":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

        process_session_id = wintypes.DWORD(0)
        if not kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(process_session_id)):
            return None

        state_code = None
        buffer = ctypes.c_void_p()
        bytes_returned = wintypes.DWORD(0)
        wtsapi32.WTSQuerySessionInformationW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
        wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        wtsapi32.WTSFreeMemory.restype = None
        if wtsapi32.WTSQuerySessionInformationW(
            None,
            process_session_id.value,
            8,  # WTSConnectState
            ctypes.byref(buffer),
            ctypes.byref(bytes_returned),
        ):
            try:
                state_code = int(ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value)
            finally:
                wtsapi32.WTSFreeMemory(buffer)

        active_console_session_id = int(kernel32.WTSGetActiveConsoleSessionId())
        state = _WINDOWS_WTS_CONNECT_STATES.get(state_code, "unknown")
        return {
            "process_session_id": int(process_session_id.value),
            "active_console_session_id": active_console_session_id,
            "state_code": state_code,
            "state": state,
            "active": state_code == 0,
        }
    except Exception:
        return None


def _describe_desktop_capture_blank_frame(
    monitor_id: Any = 0,
    *,
    attempted_monitor_ids: Optional[list[int]] = None,
) -> str:
    attempted = attempted_monitor_ids or _iter_desktop_capture_monitor_ids(monitor_id)
    if len(attempted) > 1:
        detail = f"Desktop capture returned blank frames for monitors {', '.join(str(item) for item in attempted)}."
    else:
        detail = f"Desktop capture returned a blank frame for monitor {_normalize_desktop_monitor_id(monitor_id)}."

    session_status = _get_windows_desktop_session_status()
    if session_status:
        process_session_id = session_status.get("process_session_id")
        active_console_session_id = session_status.get("active_console_session_id")
        state = str(session_status.get("state") or "unknown")
        if state == "disconnected":
            detail += f" Windows session {process_session_id} is disconnected, so Windows screen capture APIs can return black frames."
        elif not session_status.get("active"):
            detail += f" Windows session {process_session_id} is {state}, so screen capture may be unavailable."
        if (
            active_console_session_id is not None
            and process_session_id is not None
            and int(active_console_session_id) != int(process_session_id)
        ):
            detail += f" Active console session is {active_console_session_id}."
    return detail


def _normalize_desktop_monitor_id(value: Any) -> int:
    try:
        monitor_id = int(value)
    except Exception:
        return 0
    return max(0, min(64, monitor_id))


def _desktop_imagegrab_supported() -> bool:
    return Image is not None and ImageGrab is not None and _desktop_capture_platform_tag() in {"windows", "macos"}


def _normalize_desktop_capture_bounds(bounds: Any) -> Optional[Dict[str, int]]:
    if not isinstance(bounds, dict):
        return None
    try:
        return {
            "left": int(bounds.get("left") or 0),
            "top": int(bounds.get("top") or 0),
            "width": max(_DESKTOP_CAPTURE_MIN_DIMENSION, int(bounds.get("width") or 0)),
            "height": max(_DESKTOP_CAPTURE_MIN_DIMENSION, int(bounds.get("height") or 0)),
        }
    except Exception:
        return None


def _is_probably_blank_desktop_capture(image: Any) -> bool:
    if Image is None or ImageStat is None or image is None or not hasattr(image, "width") or not hasattr(image, "height"):
        return False
    try:
        if int(image.width) <= 0 or int(image.height) <= 0:
            return False
        sample = image.convert("RGB")
        sample.thumbnail((64, 64), Image.Resampling.BILINEAR)
        stat = ImageStat.Stat(sample)
        return max(float(mean) for mean in stat.mean) <= _DESKTOP_BLANK_CAPTURE_MAX_MEAN
    except Exception:
        return False


def _get_mss_monitor_bounds(monitor_id: Any = 0) -> Optional[Dict[str, int]]:
    try:
        import mss  # type: ignore

        _ensure_windows_interactive_desktop_for_capture()
        normalized_monitor_id = _normalize_desktop_monitor_id(monitor_id)
        with mss.mss() as sct:
            if 0 <= normalized_monitor_id < len(sct.monitors):
                return _normalize_desktop_capture_bounds(sct.monitors[normalized_monitor_id])
    except Exception:
        return None
    return None


def _iter_desktop_capture_monitor_ids(monitor_id: Any = 0) -> list[int]:
    return [_normalize_desktop_monitor_id(monitor_id)]


def _capture_mss_desktop_image(monitor_id: Any = 0) -> Any:
    if Image is None:
        raise RuntimeError("Pillow is required for desktop video capture")
    import mss  # type: ignore

    _ensure_windows_interactive_desktop_for_capture()
    normalized_monitor_id = _normalize_desktop_monitor_id(monitor_id)
    with mss.mss() as sct:
        if not (0 <= normalized_monitor_id < len(sct.monitors)):
            raise RuntimeError(f"Configured desktop monitor {normalized_monitor_id} is unavailable")
        monitor = sct.monitors[normalized_monitor_id]
        sct_img = sct.grab(monitor)
    return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")


def _crop_imagegrab_desktop_image(
    image: Any,
    bounds: Optional[Dict[str, int]],
    virtual_bounds: Optional[Dict[str, int]],
) -> Any:
    if Image is None:
        return image
    if getattr(image, "mode", "RGB") != "RGB":
        image = image.convert("RGB")
    normalized_bounds = _normalize_desktop_capture_bounds(bounds)
    if normalized_bounds is None:
        return image

    origin_left = int((virtual_bounds or {}).get("left") or 0)
    origin_top = int((virtual_bounds or {}).get("top") or 0)
    if virtual_bounds:
        virtual_width = int(virtual_bounds.get("width") or 0)
        virtual_height = int(virtual_bounds.get("height") or 0)
        if abs(int(image.width) - virtual_width) > 2 or abs(int(image.height) - virtual_height) > 2:
            if normalized_bounds["width"] >= int(image.width) or normalized_bounds["height"] >= int(image.height):
                return image

    left = normalized_bounds["left"] - origin_left
    top = normalized_bounds["top"] - origin_top
    right = left + normalized_bounds["width"]
    bottom = top + normalized_bounds["height"]
    if right <= 0 or bottom <= 0 or left >= image.width or top >= image.height:
        return image

    clamped_left = max(0, int(left))
    clamped_top = max(0, int(top))
    clamped_right = min(int(image.width), int(right))
    clamped_bottom = min(int(image.height), int(bottom))
    if (
        clamped_right - clamped_left < _DESKTOP_CAPTURE_MIN_DIMENSION
        or clamped_bottom - clamped_top < _DESKTOP_CAPTURE_MIN_DIMENSION
    ):
        return image
    if (
        clamped_left == 0
        and clamped_top == 0
        and clamped_right == image.width
        and clamped_bottom == image.height
    ):
        return image
    return image.crop((clamped_left, clamped_top, clamped_right, clamped_bottom))


def _capture_imagegrab_desktop_image(monitor_id: Any = 0) -> Any:
    if not _desktop_imagegrab_supported():
        return None
    try:
        _ensure_windows_interactive_desktop_for_capture()
        monitor_bounds = _get_mss_monitor_bounds(monitor_id)
        if _normalize_desktop_monitor_id(monitor_id) != 0 and monitor_bounds is None:
            return None
        kwargs = {"all_screens": True} if _desktop_capture_platform_tag() == "windows" else {}
        image = ImageGrab.grab(**kwargs)  # type: ignore[union-attr]
        return _crop_imagegrab_desktop_image(
            image,
            monitor_bounds,
            _get_mss_monitor_bounds(0),
        )
    except Exception as exc:
        logger.debug("ImageGrab desktop capture unavailable for WebRTC track: %s", exc)
        return None


def _log_desktop_imagegrab_fallback_once(reason: str) -> None:
    global _DESKTOP_IMAGEGRAB_FALLBACK_NOTICE_LOGGED
    if _DESKTOP_IMAGEGRAB_FALLBACK_NOTICE_LOGGED:
        return
    _DESKTOP_IMAGEGRAB_FALLBACK_NOTICE_LOGGED = True
    logger.info("Remote Desktop WebRTC track using ImageGrab fallback after %s.", reason)


def _capture_desktop_image_for_monitor(monitor_id: Any = 0) -> Any:
    mss_image = None
    try:
        mss_image = _capture_mss_desktop_image(monitor_id)
        if not _is_probably_blank_desktop_capture(mss_image):
            return mss_image
    except Exception as exc:
        logger.debug("MSS desktop capture unavailable for WebRTC track: %s", exc)

    fallback_image = _capture_imagegrab_desktop_image(monitor_id)
    if fallback_image is not None and not _is_probably_blank_desktop_capture(fallback_image):
        _log_desktop_imagegrab_fallback_once("blank or unavailable MSS capture")
        return fallback_image
    return mss_image


def _capture_desktop_image_with_fallback(monitor_id: Any = 0) -> Any:
    return _capture_desktop_image_for_monitor(_normalize_desktop_monitor_id(monitor_id))


class IncomingVideoTrackSink:
    """Consumes an inbound WebRTC video track and publishes JPEG previews."""

    def __init__(
        self,
        track: Any,
        *,
        session_id: str,
        registry: LatestVideoFrameRegistry = VIDEO_FRAME_REGISTRY,
        max_fps: float = DEFAULT_INBOUND_VIDEO_MAX_FPS,
        max_width: int = 960,
        jpeg_quality: int = 70,
        recording_enabled: bool = False,
        recording_dir: Optional[str] = None,
        recording_mode: str = INBOUND_VIDEO_RECORDING_MODE_VIDEO,
        image_interval_seconds: float = DEFAULT_INBOUND_VIDEO_IMAGE_INTERVAL_SECONDS,
    ) -> None:
        self.track = track
        self.session_ids = {str(session_id or "").strip() or "unknown"}
        self.registry = registry
        self.max_fps = max(1.0, float(max_fps or DEFAULT_INBOUND_VIDEO_MAX_FPS))
        self.max_width = max(160, int(max_width or 960))
        self.jpeg_quality = max(10, min(95, int(jpeg_quality or 70)))
        self.recording_enabled = bool(recording_enabled)
        self.recording_dir = str(recording_dir or "").strip()
        self.recording_mode = normalize_inbound_video_recording_mode(recording_mode)
        self.image_interval_seconds = normalize_inbound_video_image_interval_seconds(image_interval_seconds)
        self._recording_session_dir: Optional[Path] = None
        self._recording_manifest_path: Optional[Path] = None
        self._recording_sequence = 0
        self._recording_started_at_ms: Optional[int] = None
        self._recording_lock = threading.RLock()
        self._recording_container: Any = None
        self._recording_stream: Any = None
        self._recording_video_path: Optional[Path] = None
        self._recording_video_temp_path: Optional[Path] = None
        self._recording_video_buffer: Optional[BytesIO] = None
        self._recording_codec: str = ""
        self._recording_dimensions: Optional[tuple[int, int]] = None
        self._recording_video_fps = int(DEFAULT_INBOUND_VIDEO_MAX_FPS)
        self._recording_video_frame_count = 0
        self._recording_last_video_pts = -1
        self._recording_video_disabled = False
        self._recording_manifest_buffer: Optional[bytearray] = None
        self._recording_manifest_last_flushed: Optional[float] = None
        self._last_image_recorded_at_monotonic: Optional[float] = None
        self._stopped = False

    def add_alias(self, session_id: str) -> None:
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            self.session_ids.add(normalized_session_id)

    async def start(self) -> None:
        try:
            min_interval = 1.0 / self.max_fps
            next_encode_at = 0.0
            while not self._stopped:
                try:
                    frame = await self.track.recv()
                except MediaStreamError:
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Inbound video track receive failed: %s", exc)
                    break

                now = time.monotonic()
                if now < next_encode_at:
                    continue
                next_encode_at = now + min_interval

                try:
                    jpeg_bytes, width, height, image = await asyncio.to_thread(self._encode_frame, frame)
                except Exception as exc:
                    logger.debug("Skipping inbound video frame encode failure: %s", exc)
                    continue

                for session_id in tuple(self.session_ids):
                    self.registry.publish_jpeg(
                        session_id=session_id,
                        jpeg_bytes=jpeg_bytes,
                        width=width,
                        height=height,
                )

                if self.recording_enabled:
                    await asyncio.to_thread(self._record_frame, jpeg_bytes, width, height, image)
        finally:
            await asyncio.to_thread(self._close_recording)

    async def stop(self) -> None:
        self._stopped = True
        try:
            stop = getattr(self.track, "stop", None)
            if callable(stop):
                stop()
        except Exception:
            pass
        for session_id in tuple(self.session_ids):
            self.registry.clear_session(session_id)
        await asyncio.to_thread(self._close_recording)

    def _encode_frame(self, frame: Any) -> tuple[bytes, int, int, Any]:
        if Image is None:
            raise RuntimeError("Pillow is required for video frame preview encoding")
        img = frame.to_image()
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.width > self.max_width:
            ratio = self.max_width / float(img.width)
            img = img.resize((self.max_width, max(1, int(img.height * ratio))), Image.Resampling.BILINEAR)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=self.jpeg_quality, optimize=True)
        return buf.getvalue(), img.width, img.height, img

    @property
    def recording_path(self) -> Optional[str]:
        if self._recording_session_dir is None:
            return None
        return str(self._recording_session_dir)

    def _safe_recording_component(self, value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
        return safe.strip("._")[:80] or "unknown"

    def _ensure_recording_session_dir(self) -> Optional[Path]:
        if not self.recording_enabled:
            return None
        if self._recording_session_dir is not None:
            return self._recording_session_dir
        if not self.recording_dir:
            logger.warning("Inbound video recording requested without a recording directory")
            self.recording_enabled = False
            return None

        try:
            root = Path(self.recording_dir).expanduser()
            root.mkdir(parents=True, exist_ok=True)
            session_hint = sorted(self.session_ids)[0] if self.session_ids else "unknown"
            timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
            session_dir = root / f"{timestamp}-{self._safe_recording_component(session_hint)}"
            session_dir.mkdir(parents=True, exist_ok=True)
            self._recording_session_dir = session_dir
            self._recording_manifest_path = session_dir / "manifest.jsonl"
            self._recording_started_at_ms = int(time.time() * 1000)
            logger.info("Recording inbound video to %s", session_dir)
            return session_dir
        except Exception as exc:
            logger.warning("Failed to create inbound video recording directory: %s", exc)
            self.recording_enabled = False
            return None

    @property
    def recording_format(self) -> str:
        mode = normalize_inbound_video_recording_mode(self.recording_mode)
        if mode == INBOUND_VIDEO_RECORDING_MODE_IMAGES:
            return "jpeg_images"
        if self._recording_video_disabled:
            return "jpeg_images_fallback"
        return INBOUND_VIDEO_RECORDING_FORMAT

    def _record_frame(self, jpeg_bytes: bytes, width: int, height: int, image: Any = None) -> None:
        with self._recording_lock:
            session_dir = self._ensure_recording_session_dir()
            if session_dir is None:
                return

            mode = normalize_inbound_video_recording_mode(self.recording_mode)
            if mode == INBOUND_VIDEO_RECORDING_MODE_IMAGES:
                if not self._should_record_image_snapshot_locked():
                    return
                self._recording_sequence += 1
                try:
                    self._record_jpeg_snapshot_locked(
                        session_dir,
                        sequence=self._recording_sequence,
                        jpeg_bytes=jpeg_bytes,
                        width=width,
                        height=height,
                        format_name="jpeg_image",
                    )
                except Exception as exc:
                    logger.warning("Failed to record inbound video image: %s", exc)
                    self.recording_enabled = False
                return

            if self._recording_video_disabled:
                if not self._should_record_image_snapshot_locked():
                    return
                self._recording_sequence += 1
                try:
                    self._record_jpeg_snapshot_locked(
                        session_dir,
                        sequence=self._recording_sequence,
                        jpeg_bytes=jpeg_bytes,
                        width=width,
                        height=height,
                        format_name="jpeg_image_fallback",
                    )
                except Exception as exc:
                    logger.warning("Failed to record inbound video fallback image: %s", exc)
                    self.recording_enabled = False
                return

            self._recording_sequence += 1
            try:
                self._record_mp4_frame_locked(
                    session_dir,
                    sequence=self._recording_sequence,
                    jpeg_bytes=jpeg_bytes,
                    image=image,
                    width=width,
                    height=height,
                )
            except Exception as exc:
                self._recording_video_disabled = True
                self._close_video_recording_locked(discard=True)
                logger.warning("Failed to record inbound video frame to MP4; falling back to image snapshots: %s", exc)
                try:
                    self._record_jpeg_snapshot_locked(
                        session_dir,
                        sequence=self._recording_sequence,
                        jpeg_bytes=jpeg_bytes,
                        width=width,
                        height=height,
                        format_name="jpeg_image_fallback",
                        force_recorded_at=True,
                    )
                except Exception as fallback_exc:
                    logger.warning("Failed to record inbound video fallback image: %s", fallback_exc)
                    self.recording_enabled = False

    def _record_mp4_frame_locked(
        self,
        session_dir: Path,
        *,
        sequence: int,
        jpeg_bytes: bytes,
        image: Any = None,
        width: int,
        height: int,
    ) -> None:
        self._ensure_video_recording_stream_locked(session_dir, width, height)
        if self._recording_container is None or self._recording_stream is None:
            raise RuntimeError("Video recording stream is unavailable")
        recording_image = self._recording_image(image, jpeg_bytes)
        timestamp_ms = int(time.time() * 1000)
        fps = max(1, int(self._recording_video_fps or round(DEFAULT_INBOUND_VIDEO_MAX_FPS)))
        started_at_ms = self._recording_started_at_ms or timestamp_ms
        elapsed_seconds = max(0.0, (timestamp_ms - started_at_ms) / 1000.0)
        frame_pts = int(round(elapsed_seconds * fps))
        if frame_pts <= self._recording_last_video_pts:
            frame_pts = self._recording_last_video_pts + 1
        self._encode_recording_video_image_locked(
            recording_image,
            pts=frame_pts,
            time_base=Fraction(1, fps),
        )
        self._recording_last_video_pts = frame_pts
        self._write_recording_manifest_locked(
            {
                "sequence": sequence,
                "timestamp_ms": timestamp_ms,
                "started_at_ms": self._recording_started_at_ms,
                "format": "mp4_video",
                "file": self._recording_video_path.name if self._recording_video_path else "video.mp4",
                "width": int(width or 0),
                "height": int(height or 0),
                "sessions": sorted(self.session_ids),
                "source": "ios",
                "codec": self._recording_codec,
                "fps": fps,
                "encoded_frames": self._recording_video_frame_count,
                "frame_pts": frame_pts,
                "time_base": f"1/{fps}",
                "duplicated_frames": 0,
                "timing_mode": "received_frame_pts",
            }
        )

    def _encode_recording_video_image_locked(
        self,
        image: Any,
        *,
        pts: Optional[int] = None,
        time_base: Optional[Fraction] = None,
    ) -> None:
        if self._recording_container is None or self._recording_stream is None:
            raise RuntimeError("Video recording stream is unavailable")
        frame = VideoFrame.from_image(image)  # type: ignore[union-attr]
        if pts is not None:
            frame.pts = int(pts)
        if time_base is not None:
            frame.time_base = time_base
        for packet in self._recording_stream.encode(frame):
            self._recording_container.mux(packet)
        self._recording_video_frame_count += 1

    def _ensure_video_recording_stream_locked(self, session_dir: Path, width: int, height: int) -> None:
        if self._recording_container is not None and self._recording_stream is not None:
            return
        if _av is None:
            raise RuntimeError("PyAV is required for MP4 video recording")

        target_width = max(2, int(width or 2))
        target_height = max(2, int(height or 2))
        if target_width % 2:
            target_width += 1
        if target_height % 2:
            target_height += 1
        fps = max(1, min(60, int(round(self.max_fps or DEFAULT_INBOUND_VIDEO_MAX_FPS))))
        video_path = session_dir / "video.mp4"
        temp_video_path = session_dir / ".video.mp4.partial"
        try:
            video_target: Any = str(temp_video_path)
            if secure_storage_enabled():
                # Keep the live MP4 out of the filesystem.  PyAV writes to a
                # seekable in-memory target and the completed container is
                # encrypted only after the final trailer is present.
                self._recording_video_buffer = BytesIO()
                video_target = self._recording_video_buffer
            container = _av.open(  # type: ignore[union-attr]
                video_target,
                mode="w",
                format="mp4",
                # PyAV cannot rewrite a faststart MP4 trailer on a BytesIO
                # target.  The encrypted path remains a normal seekable MP4;
                # faststart is retained for the ordinary file-backed path.
                options={} if secure_storage_enabled() else {"movflags": "faststart"},
            )
        except Exception:
            self._recording_video_buffer = None
            with contextlib.suppress(OSError):
                temp_video_path.unlink()
            raise
        stream = None
        last_error: Optional[Exception] = None
        for codec in ("libx264", "h264", "mpeg4"):
            try:
                candidate = container.add_stream(codec, rate=fps)
                candidate.width = target_width
                candidate.height = target_height
                candidate.pix_fmt = "yuv420p"
                stream = candidate
                self._recording_codec = codec
                break
            except Exception as exc:
                last_error = exc
        if stream is None:
            container.close()
            self._recording_video_buffer = None
            with contextlib.suppress(OSError):
                temp_video_path.unlink()
            raise RuntimeError(f"No MP4 video encoder is available: {last_error}")
        self._recording_container = container
        self._recording_stream = stream
        self._recording_video_path = video_path
        self._recording_video_temp_path = None if secure_storage_enabled() else temp_video_path
        self._recording_dimensions = (target_width, target_height)
        self._recording_video_fps = fps

    def _recording_image(self, image: Any, jpeg_bytes: bytes) -> Any:
        if Image is None:
            raise RuntimeError("Pillow is required for video recording")
        if image is None:
            with Image.open(BytesIO(jpeg_bytes)) as img:
                recording_image = img.convert("RGB")
        else:
            recording_image = image.convert("RGB") if getattr(image, "mode", "RGB") != "RGB" else image
        if self._recording_dimensions and recording_image.size != self._recording_dimensions:
            recording_image = recording_image.resize(self._recording_dimensions, Image.Resampling.BILINEAR)
        return recording_image

    def _record_jpeg_snapshot_locked(
        self,
        session_dir: Path,
        *,
        sequence: int,
        jpeg_bytes: bytes,
        width: int,
        height: int,
        format_name: str = "jpeg_image",
        force_recorded_at: bool = False,
    ) -> None:
        filename = f"image-{sequence:06d}.jpg"
        write_secure_file(session_dir / filename, jpeg_bytes)
        if force_recorded_at:
            self._last_image_recorded_at_monotonic = time.monotonic()
        self._write_recording_manifest_locked(
            {
                "sequence": sequence,
                "timestamp_ms": int(time.time() * 1000),
                "started_at_ms": self._recording_started_at_ms,
                "format": format_name,
                "file": filename,
                "width": int(width or 0),
                "height": int(height or 0),
                "image_interval_seconds": self.image_interval_seconds,
                "sessions": sorted(self.session_ids),
                "source": "ios",
            }
        )

    def _should_record_image_snapshot_locked(self) -> bool:
        now = time.monotonic()
        if self._last_image_recorded_at_monotonic is None:
            self._last_image_recorded_at_monotonic = now
            return True
        if now - self._last_image_recorded_at_monotonic >= self.image_interval_seconds:
            self._last_image_recorded_at_monotonic = now
            return True
        return False

    def _write_recording_manifest_locked(self, manifest_entry: Dict[str, Any], *, force_flush: bool = False) -> None:
        session_dir = self._recording_session_dir
        if session_dir is None:
            return
        manifest_path = self._recording_manifest_path or (session_dir / "manifest.jsonl")
        line = (json.dumps(manifest_entry, separators=(",", ":")) + "\n").encode("utf-8")
        if secure_storage_enabled():
            if self._recording_manifest_buffer is None:
                self._recording_manifest_buffer = bytearray(
                    read_secure_file(manifest_path) if manifest_path.exists() else b""
                )
                self._recording_manifest_last_flushed = time.monotonic()
            self._recording_manifest_buffer.extend(line)
            now = time.monotonic()
            if force_flush or (now - (self._recording_manifest_last_flushed or 0.0)) >= 2.0:
                write_secure_file(manifest_path, bytes(self._recording_manifest_buffer))
                self._recording_manifest_last_flushed = now
            return
        with manifest_path.open("ab") as handle:
            handle.write(line)

    def _flush_recording_manifest_locked(self) -> None:
        if self._recording_manifest_buffer is not None:
            session_dir = self._recording_session_dir
            if session_dir is not None:
                manifest_path = self._recording_manifest_path or (session_dir / "manifest.jsonl")
                try:
                    write_secure_file(manifest_path, bytes(self._recording_manifest_buffer))
                except Exception as exc:
                    logger.warning("Failed to flush inbound video recording manifest: %s", exc)
            self._recording_manifest_buffer = None
            self._recording_manifest_last_flushed = None

    def _close_recording(self) -> None:
        with self._recording_lock:
            self._close_video_recording_locked()
            self._flush_recording_manifest_locked()

    def _close_video_recording_locked(self, *, discard: bool = False) -> None:
        container = self._recording_container
        stream = self._recording_stream
        video_path = self._recording_video_path
        temp_video_path = self._recording_video_temp_path
        video_buffer = self._recording_video_buffer
        self._recording_container = None
        self._recording_stream = None
        self._recording_video_fps = int(DEFAULT_INBOUND_VIDEO_MAX_FPS)
        self._recording_video_frame_count = 0
        self._recording_last_video_pts = -1
        self._recording_video_path = None
        self._recording_video_temp_path = None
        self._recording_video_buffer = None
        self._recording_dimensions = None
        self._recording_codec = ""

        finalize_error: Optional[Exception] = None
        if container is None:
            return
        try:
            if stream is not None:
                for packet in stream.encode(None):
                    container.mux(packet)
        except Exception as exc:
            finalize_error = exc
        finally:
            try:
                container.close()
            except Exception as exc:
                finalize_error = finalize_error or exc

        if video_buffer is not None:
            try:
                if not discard and finalize_error is None and video_path is not None:
                    write_secure_file(video_path, video_buffer.getvalue())
            except Exception as exc:
                logger.warning("Failed to publish encrypted inbound video recording: %s", exc)
            finally:
                video_buffer.close()
            if finalize_error is not None:
                logger.warning("Failed to finalize inbound video recording: %s", finalize_error)
            return

        if temp_video_path is None:
            return
        if discard or finalize_error is not None:
            with contextlib.suppress(OSError):
                temp_video_path.unlink()
            if finalize_error is not None:
                logger.warning("Failed to finalize inbound video recording: %s", finalize_error)
            return
        try:
            os.replace(temp_video_path, video_path)
        except Exception as exc:
            logger.warning("Failed to publish finalized inbound video recording: %s", exc)


@dataclass(frozen=True)
class RealtimeVideoInputFrame:
    source_id: str
    sequence: int
    timestamp_ms: int
    jpeg_bytes: bytes
    width: int
    height: int
    source: str = "api"


class RealtimeVideoInputRegistry:
    """Thread-safe frame bus for API-fed outbound WebRTC video sources."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._latest_by_source: Dict[str, RealtimeVideoInputFrame] = {}
        self._sequence = 0

    def publish_jpeg(
        self,
        *,
        source_id: str = "default",
        jpeg_bytes: bytes,
        source: str = "api",
    ) -> RealtimeVideoInputFrame:
        normalized_source_id = self._normalize_source_id(source_id)
        if Image is None:
            width = 0
            height = 0
        else:
            try:
                with Image.open(BytesIO(jpeg_bytes)) as img:
                    width = int(img.width)
                    height = int(img.height)
            except Exception as exc:
                raise ValueError(f"Invalid JPEG frame: {exc}") from exc
        with self._condition:
            self._sequence += 1
            frame = RealtimeVideoInputFrame(
                source_id=normalized_source_id,
                sequence=self._sequence,
                timestamp_ms=int(time.time() * 1000),
                jpeg_bytes=bytes(jpeg_bytes),
                width=max(0, width),
                height=max(0, height),
                source=str(source or "api"),
            )
            self._latest_by_source[normalized_source_id] = frame
            self._condition.notify_all()
            return frame

    def latest(self, source_id: str = "default") -> Optional[RealtimeVideoInputFrame]:
        with self._condition:
            return self._latest_by_source.get(self._normalize_source_id(source_id))

    def status(self, source_id: Optional[str] = None) -> Dict[str, Any]:
        with self._condition:
            if source_id:
                frame = self._latest_by_source.get(self._normalize_source_id(source_id))
                return self._frame_status(frame)
            return {
                "active_sources": len(self._latest_by_source),
                "sources": {
                    source: self._frame_status(frame)
                    for source, frame in sorted(self._latest_by_source.items())
                },
            }

    def wait_for_frame(
        self,
        *,
        source_id: str = "default",
        last_sequence: int = 0,
        timeout: float = 1.0,
    ) -> Optional[RealtimeVideoInputFrame]:
        normalized_source_id = self._normalize_source_id(source_id)
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
        with self._condition:
            while True:
                frame = self._latest_by_source.get(normalized_source_id)
                if frame is not None and frame.sequence > int(last_sequence or 0):
                    return frame
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)

    @staticmethod
    def _normalize_source_id(source_id: str) -> str:
        normalized = str(source_id or "").strip()
        return normalized or "default"

    @staticmethod
    def _frame_status(frame: Optional[RealtimeVideoInputFrame]) -> Dict[str, Any]:
        return {
            "active": frame is not None,
            "source_id": frame.source_id if frame else None,
            "sequence": frame.sequence if frame else 0,
            "timestamp_ms": frame.timestamp_ms if frame else None,
            "width": frame.width if frame else None,
            "height": frame.height if frame else None,
            "source": frame.source if frame else None,
        }


REALTIME_VIDEO_INPUTS = RealtimeVideoInputRegistry()


class RealtimeVideoInputStreamTrack(_AiortcVideoStreamTrack):  # type: ignore[misc, valid-type]
    """Outbound WebRTC track backed by frames pushed to REALTIME_VIDEO_INPUTS."""

    kind = "video"

    def __init__(
        self,
        *,
        source_id: str = "default",
        registry: RealtimeVideoInputRegistry = REALTIME_VIDEO_INPUTS,
        fps: float = 12.0,
        max_width: int = 1280,
    ) -> None:
        super().__init__()  # type: ignore[misc]
        self.source_id = str(source_id or "default").strip() or "default"
        self.registry = registry
        self.fps = max(1.0, min(30.0, float(fps or 12.0)))
        self.max_width = max(160, int(max_width or 1280))
        self._enabled = asyncio.Event()
        self._fallback_timestamp = 0
        self._time_base = Fraction(1, 90000)
        self._last_sequence = 0

    def enable(self) -> None:
        self._enabled.set()

    def disable(self) -> None:
        self._enabled.clear()

    @property
    def is_enabled(self) -> bool:
        return self._enabled.is_set()

    async def recv(self) -> Any:
        while not self._enabled.is_set():
            if getattr(self, "readyState", "live") != "live":
                raise MediaStreamError
            try:
                await asyncio.wait_for(self._enabled.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
        self._fallback_timestamp += int(90000 / self.fps)
        pts, time_base = self._fallback_timestamp, self._time_base

        frame = await asyncio.to_thread(
            self.registry.wait_for_frame,
            source_id=self.source_id,
            last_sequence=self._last_sequence,
            timeout=0.001,
        )
        if frame is None:
            frame = self.registry.latest(self.source_id)

        image = await asyncio.to_thread(self._image_from_frame, frame)
        if VideoFrame is None:
            raise RuntimeError("PyAV is required for WebRTC video frames")
        video_frame = VideoFrame.from_image(image)
        OUTBOUND_VIDEO_TELEMETRY.record_frame(
            source=f"api:{self.source_id}",
            width=video_frame.width,
            height=video_frame.height,
        )
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

    def _image_from_frame(self, frame: Optional[RealtimeVideoInputFrame]) -> Any:
        if Image is None:
            raise RuntimeError("Pillow is required for realtime video input")
        if frame is None:
            return self._placeholder_image()
        self._last_sequence = frame.sequence
        with Image.open(BytesIO(frame.jpeg_bytes)) as img:
            image = img.convert("RGB")
        if image.width > self.max_width:
            ratio = self.max_width / float(image.width)
            image = image.resize((self.max_width, max(1, int(image.height * ratio))), Image.Resampling.BILINEAR)
        return _ensure_even_rgb_image(image)

    def _placeholder_image(self) -> Any:
        image = Image.new("RGB", (960, 540), (15, 23, 42))
        if ImageDraw is not None:
            draw = ImageDraw.Draw(image)
            draw.text((32, 32), "AutoYou realtime video input", fill=(248, 250, 252))
            draw.text((32, 70), f"Waiting for API frames on source '{self.source_id}'.", fill=(148, 163, 184))
        return image


class VideoFilePlaybackState:
    """Thread-safe playback state for an outbound video-file source."""

    def __init__(self, source_id: str = "default") -> None:
        self.source_id = str(source_id or "default").strip() or "default"
        self._lock = threading.RLock()
        self.file_path = ""
        self.loop = False
        self.paused = False
        self.ended = False
        self.restart_counter = 0
        self.updated_at_ms: Optional[int] = None

    def configure(self, *, file_path: str = "", loop: bool = False, restart: bool = True) -> Dict[str, Any]:
        normalized_path = str(file_path or "").strip()
        with self._lock:
            path_changed = normalized_path != self.file_path
            loop_changed = bool(loop) != self.loop
            self.file_path = normalized_path
            self.loop = bool(loop)
            if restart or path_changed or loop_changed:
                self.paused = False
                self.ended = False
                self.restart_counter += 1
            self.updated_at_ms = int(time.time() * 1000)
            return self.status()

    def play(self, *, restart: bool = False) -> Dict[str, Any]:
        with self._lock:
            if restart or self.ended:
                self.restart_counter += 1
                self.ended = False
            self.paused = False
            self.updated_at_ms = int(time.time() * 1000)
            return self.status()

    def pause(self) -> Dict[str, Any]:
        with self._lock:
            self.paused = True
            self.updated_at_ms = int(time.time() * 1000)
            return self.status()

    def mark_ended(self) -> Dict[str, Any]:
        with self._lock:
            self.ended = True
            self.paused = False
            self.updated_at_ms = int(time.time() * 1000)
            return self.status()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            file_exists = bool(self.file_path and Path(self.file_path).expanduser().is_file())
            return {
                "source_id": self.source_id,
                "file_path": self.file_path,
                "file_exists": file_exists,
                "configured": bool(self.file_path),
                "loop": self.loop,
                "paused": self.paused,
                "ended": self.ended,
                "playing": bool(file_exists and not self.paused and not self.ended),
                "restart_counter": self.restart_counter,
                "updated_at_ms": self.updated_at_ms,
            }


class VideoFilePlaybackRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: Dict[str, VideoFilePlaybackState] = {}

    def state(self, source_id: str = "default") -> VideoFilePlaybackState:
        normalized = str(source_id or "default").strip() or "default"
        with self._lock:
            state = self._states.get(normalized)
            if state is None:
                state = VideoFilePlaybackState(normalized)
                self._states[normalized] = state
            return state

    def configure(self, *, source_id: str = "default", file_path: str = "", loop: bool = False, restart: bool = True) -> Dict[str, Any]:
        return self.state(source_id).configure(file_path=file_path, loop=loop, restart=restart)

    def play(self, *, source_id: str = "default", restart: bool = False) -> Dict[str, Any]:
        return self.state(source_id).play(restart=restart)

    def pause(self, *, source_id: str = "default") -> Dict[str, Any]:
        return self.state(source_id).pause()

    def status(self, source_id: str = "default") -> Dict[str, Any]:
        return self.state(source_id).status()


VIDEO_FILE_PLAYBACKS = VideoFilePlaybackRegistry()


class VideoFileStreamTrack(_AiortcVideoStreamTrack):  # type: ignore[misc, valid-type]
    """Outbound WebRTC track backed by a configured local video file."""

    kind = "video"

    def __init__(
        self,
        *,
        source_id: str = "default",
        registry: VideoFilePlaybackRegistry = VIDEO_FILE_PLAYBACKS,
        max_width: int = 1280,
        native_media: bool = False,
    ) -> None:
        super().__init__()  # type: ignore[misc]
        if type(native_media) is not bool:
            raise ValueError("invalid native video file ownership mode")
        self.source_id = str(source_id or "default").strip() or "default"
        self.registry = registry
        self.max_width = max(160, int(max_width or 1280))
        self._enabled = asyncio.Event()
        self._fallback_timestamp = 0
        self._time_base = Fraction(1, 90000)
        self._container: Any = None
        self._frame_iter: Any = None
        self._current_path = ""
        self._current_restart_counter = -1
        self._current_fps = 24.0
        self._last_image: Any = None
        self._playback_buffer: Optional[BytesIO] = None
        self.native_media = native_media
        self._materialized_file: Any = None
        self._native_closed = False
        self._native_close_error: Optional[BaseException] = None
        self._native_audio_clock = None
        self._native_clock_origin_us = None
        self._native_clock_position_us = 0
        self._native_clock_audio = False
        self._native_clock_audio_id = ""
        self._native_clock_restart = False
        self._native_pending_frame = None
        self._native_frame_position_us = -1
        self._native_stream = None
        self._native_duration_us = 0

    def set_native_audio_clock(self, clock) -> None:
        if not self.native_media or self._native_closed or not callable(clock):
            raise RuntimeError("native file clock requires a current owned source")
        self._native_audio_clock = clock

    def _native_position(self, file_path: str) -> int:
        now = time.monotonic_ns() // 1000
        audio = self._native_audio_clock(file_path) if self._native_audio_clock is not None else None
        if audio is not None:
            position, state, identity = audio
            if type(position) is not int or position < 0 or state not in {"playing", "paused", "completed", "stopped", "error"} or not identity:
                raise ValueError("invalid native file audio clock")
            if self._native_clock_audio_id != identity:
                # A newly approved playback has its own origin, including a
                # restart of the same selected file. Seek below discards old
                # decoder look-ahead; no previous playback can renew this one.
                self._native_clock_restart = bool(self._native_clock_audio_id)
                self._native_clock_audio_id = identity
            self._native_clock_audio = True
            self._native_clock_position_us = position
            self._native_clock_origin_us = now - position
            return position
        if self._native_clock_origin_us is None or self._native_clock_audio:
            self._native_clock_origin_us = now - self._native_clock_position_us
        self._native_clock_audio = False
        self._native_clock_position_us = max(0, now - self._native_clock_origin_us)
        return self._native_clock_position_us

    def _native_next_frame(self, target_us: int, *, loop: bool):
        if loop and self._native_duration_us:
            target_us %= self._native_duration_us
        stream = self._native_stream
        start = int(getattr(stream, "start_time", None) or 0)
        time_base = getattr(stream, "time_base", None)
        if time_base and (self._native_clock_restart or target_us < self._native_frame_position_us or target_us - self._native_frame_position_us > 500_000):
            self._container.seek(start + int(Fraction(target_us, 1_000_000) / time_base), stream=stream, backward=True)
            self._frame_iter = self._container.decode(video=0)
            self._native_pending_frame = None
            self._native_clock_restart = False
        selected = None
        # Decoder work per capture is bounded, including corrupt/high-rate
        # files and a large clock jump. Retain only one future decoded frame.
        for _ in range(32):
            frame = self._native_pending_frame
            self._native_pending_frame = None
            if frame is None:
                try:
                    frame = next(self._frame_iter)
                except StopIteration:
                    if loop and not self._native_duration_us:
                        self._native_duration_us = max(1, self._native_frame_position_us + int(1_000_000 / self._current_fps))
                    elif not loop and not self._native_clock_audio:
                        self.registry.state(self.source_id).mark_ended()
                    break
            if not 0 < frame.width <= 4096 or not 0 < frame.height <= 4096 or sum(plane.buffer_size for plane in frame.planes) > 32 * 1024 * 1024:
                raise ValueError("native video file frame exceeded its source budget")
            pts = getattr(frame, "pts", None)
            base = getattr(frame, "time_base", None)
            position = int((pts - start) * base * 1_000_000) if pts is not None and base else (0 if self._native_frame_position_us < 0 else self._native_frame_position_us + int(1_000_000 / self._current_fps))
            if position > target_us:
                self._native_pending_frame = frame
                break
            selected = frame
            self._native_frame_position_us = position
        return selected

    def enable(self) -> None:
        if self.native_media and self._native_closed:
            raise RuntimeError("native video file source is retired")
        self.registry.play(source_id=self.source_id, restart=True)
        self._enabled.set()

    def disable(self) -> None:
        self._enabled.clear()

    @property
    def is_enabled(self) -> bool:
        return self._enabled.is_set()

    async def recv(self) -> Any:
        while not self._enabled.is_set():
            if getattr(self, "readyState", "live") != "live":
                raise MediaStreamError
            try:
                await asyncio.wait_for(self._enabled.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

        if self.native_media:
            # The native capture owner paces the negotiated rate. File PTS
            # follows the consumed audio clock, not aiortc's 30 FPS timer.
            pts, time_base = int(self._native_clock_position_us * 90_000 / 1_000_000), self._time_base
        elif hasattr(self, "next_timestamp"):
            await asyncio.sleep(1.0 / max(1.0, min(60.0, self._current_fps)))
            pts, time_base = await self.next_timestamp()  # type: ignore[attr-defined]
        else:  # pragma: no cover - aiortc unavailable fallback.
            await asyncio.sleep(1.0 / max(1.0, min(60.0, self._current_fps)))
            self._fallback_timestamp += int(90000 / max(1.0, self._current_fps))
            pts, time_base = self._fallback_timestamp, self._time_base

        image = await asyncio.to_thread(self._next_image)
        if self.native_media:
            pts = self._native_clock_position_us * 90_000 // 1_000_000
        if VideoFrame is None:
            raise RuntimeError("PyAV is required for WebRTC video frames")
        video_frame = VideoFrame.from_image(image)
        OUTBOUND_VIDEO_TELEMETRY.record_frame(
            source=f"video_file:{self.source_id}",
            width=video_frame.width,
            height=video_frame.height,
        )
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

    def _next_image(self) -> Any:
        if Image is None:
            raise RuntimeError("Pillow is required for video file playback")
        if _av is None:
            return self._placeholder_image("PyAV is required for video file playback")

        status = self.registry.status(self.source_id)
        file_path = str(status.get("file_path") or "").strip()
        if not file_path:
            return self._placeholder_image("No video file selected")
        if not Path(file_path).expanduser().is_file():
            return self._placeholder_image("Selected video file is missing")
        if status.get("paused"):
            return self._last_image or self._placeholder_image("Video file paused")
        if status.get("ended") and not status.get("loop"):
            return self._last_image or self._placeholder_image("Video file ended")

        restart_counter = int(status.get("restart_counter") or 0)
        if file_path != self._current_path or restart_counter != self._current_restart_counter or self._container is None:
            self._open_file(file_path, restart_counter)

        if self.native_media:
            frame = self._native_next_frame(self._native_position(file_path), loop=bool(status.get("loop")))
            self._native_captured_at_us = time.monotonic_ns() // 1000
            if frame is None:
                return self._last_image or self._placeholder_image("Video file waiting for its playback clock")
            image = frame.to_image().convert("RGB")
            if image.width > self.max_width:
                ratio = self.max_width / float(image.width)
                image = image.resize((self.max_width, max(1, int(image.height * ratio))), Image.Resampling.BILINEAR)
            self._last_image = _ensure_even_rgb_image(image)
            return self._last_image

        try:
            frame = next(self._frame_iter)
        except StopIteration:
            self._close_file()
            if status.get("loop"):
                self._open_file(file_path, restart_counter)
                try:
                    frame = next(self._frame_iter)
                except StopIteration:
                    self.registry.state(self.source_id).mark_ended()
                    return self._last_image or self._placeholder_image("Video file ended")
            else:
                self.registry.state(self.source_id).mark_ended()
                return self._last_image or self._placeholder_image("Video file ended")

        if self.native_media and (not 0 < frame.width <= 4096 or not 0 < frame.height <= 4096 or
                sum(plane.buffer_size for plane in frame.planes) > 32 * 1024 * 1024):
            raise ValueError("native video file frame exceeded its source budget")
        self._native_captured_at_us = time.monotonic_ns() // 1000
        image = frame.to_image().convert("RGB")
        if image.width > self.max_width:
            ratio = self.max_width / float(image.width)
            image = image.resize((self.max_width, max(1, int(image.height * ratio))), Image.Resampling.BILINEAR)
        image = _ensure_even_rgb_image(image)
        self._last_image = image
        return image

    def _open_file(self, file_path: str, restart_counter: int) -> None:
        self._close_file()
        expanded_path = Path(file_path).expanduser()
        if self.native_media:
            if self._native_closed:
                raise RuntimeError("native video file source is retired")
            # Assign each handle before inspecting it. A partial open remains
            # owned, and its bounded plaintext lives until the decoder joins.
            context = materialize_secure_file(expanded_path, maximum_bytes=256 * 1024 * 1024)
            path = context.__enter__()
            self._materialized_file = context
            self._container = _av.open(str(path))  # type: ignore[union-attr]
            container = self._container
        else:
            container = None
        # Inspect only the envelope marker; selected movies can be much larger
        # than the process buffer budget.
        if not self.native_media:
            with expanded_path.open("rb") as source:
                protected = source.read(len(SPM_FILE_HEADER)).startswith(SPM_FILE_HEADER)
            if protected:
                self._playback_buffer = BytesIO(read_secure_file(expanded_path))
                container = _av.open(self._playback_buffer)  # type: ignore[union-attr]
            else:
                container = _av.open(str(expanded_path))  # type: ignore[union-attr]
        stream = next((stream for stream in container.streams if stream.type == "video"), None)
        if self.native_media and stream is not None:
            width, height = stream.codec_context.width, stream.codec_context.height
            if not 0 < width <= 4096 or not 0 < height <= 4096 or width * height * 4 > 32 * 1024 * 1024:
                raise ValueError("native video file geometry exceeded its source budget")
        if stream is not None and stream.average_rate:
            try:
                self._current_fps = max(1.0, min(60.0, float(stream.average_rate)))
            except Exception:
                self._current_fps = 24.0
        self._container = container
        self._frame_iter = container.decode(video=0)
        self._current_path = file_path
        self._current_restart_counter = restart_counter
        if self.native_media:
            self._native_stream = stream
            self._native_pending_frame = None
            self._native_frame_position_us = -1
            self._native_clock_origin_us = None
            self._native_clock_position_us = 0
            self._native_clock_audio = False
            self._native_clock_audio_id = ""
            self._native_clock_restart = False
            duration, base = getattr(stream, "duration", None), getattr(stream, "time_base", None)
            self._native_duration_us = max(0, int(duration * base * 1_000_000)) if duration and base else max(0, int(getattr(container, "duration", None) or 0))

    def _close_file(self) -> None:
        if self.native_media:
            if self._native_close_error is not None:
                raise self._native_close_error
            try:
                if self._container is not None:
                    self._container.close()
                    self._container = self._frame_iter = None
                    self._native_pending_frame = self._native_stream = None
                if self._materialized_file is not None:
                    self._materialized_file.__exit__(None, None, None)
                    self._materialized_file = None
            except BaseException as error:
                self._native_close_error = error
                raise
            return
        container = self._container
        self._container = None
        self._frame_iter = None
        if container is not None:
            with contextlib.suppress(Exception):
                container.close()
        playback_buffer = self._playback_buffer
        self._playback_buffer = None
        if playback_buffer is not None:
            with contextlib.suppress(Exception):
                playback_buffer.close()

    def _placeholder_image(self, detail: str = "") -> Any:
        image = Image.new("RGB", (960, 540), (15, 23, 42))
        if ImageDraw is not None:
            draw = ImageDraw.Draw(image)
            draw.text((32, 32), "AutoYou video file playback", fill=(248, 250, 252))
            if detail:
                draw.text((32, 70), detail[:120], fill=(148, 163, 184))
        return image

    def fence_native(self) -> None:
        self._native_closed = True
        self._enabled.clear()
        stop = getattr(super(), "stop", None)
        if stop is not None:
            stop()

    def close_native(self) -> None:
        self.fence_native()
        self._close_file()
        self._last_image = None
        self._native_audio_clock = None


class RemoteDesktopVideoStreamTrack(_AiortcVideoStreamTrack):  # type: ignore[misc, valid-type]
    """Outbound WebRTC video track backed by local desktop capture.

    WebRTC handles codec selection, pacing, and bitrate adaptation. This track
    only supplies frames to the negotiated video sender.
    """

    kind = "video"

    def __init__(self, *, fps: float = 8.0, max_width: int = 1280, monitor_id: int = 0) -> None:
        super().__init__()  # type: ignore[misc]
        self.fps = max(1.0, min(30.0, float(fps or 8.0)))
        self.max_width = max(320, int(max_width or 1280))
        self.monitor_id = _normalize_desktop_monitor_id(monitor_id)
        # NB: do NOT name this `_timestamp` - aiortc's VideoStreamTrack.next_timestamp
        # keys on `hasattr(self, "_timestamp")` and would then dereference `_start`
        # (unset) on the first frame. Keep a separate name for the no-aiortc fallback.
        self._fallback_timestamp = 0
        self._time_base = Fraction(1, 90000)
        self._outbound_timestamp = 0
        # Gated OFF until the client explicitly starts a video call. The track is
        # negotiated up front (no SDP renegotiation needed), but the desktop is
        # never captured or streamed while disabled.
        self._enabled = asyncio.Event()
        self._video_active = False
        self._capture_allowed = True
        self._latest_preview_image: Any = None
        self._latest_preview_at = 0.0
        # aiortc may initialize its encoder from an inactive frame before the
        # caller starts screen sharing. That frame must already use the
        # selected desktop profile or the encoder stays pinned to a stale
        # 960x540 stream for the rest of the call.
        self._output_size = self._profiled_idle_frame_size()
        self._latest_preview_lock = threading.Lock()
        # recv() is pulled serially by aiortc. Keep an absolute capture
        # deadline so expensive screen capture consumes part of the frame
        # interval rather than adding a second full interval afterwards.
        self._next_capture_due_at: Optional[float] = None

    def enable(self) -> None:
        """Start desktop capture/streaming - the client opened a video call."""
        self._video_active = True
        if self._capture_allowed:
            if not self._enabled.is_set():
                self._next_capture_due_at = None
            self._enabled.set()
        else:
            self._enabled.clear()

    def disable(self) -> None:
        """Pause desktop capture/streaming - the client stopped video."""
        self._video_active = False
        self._enabled.clear()
        self._next_capture_due_at = None
        with self._latest_preview_lock:
            self._latest_preview_image = None
            self._latest_preview_at = 0.0

    def set_remote_desktop_enabled(self, enabled: bool) -> None:
        """Apply capture authorization without losing the active-call state."""
        self._capture_allowed = bool(enabled)
        if self._capture_allowed and self._video_active:
            if not self._enabled.is_set():
                self._next_capture_due_at = None
            self._enabled.set()
        else:
            self._enabled.clear()
            self._next_capture_due_at = None
            with self._latest_preview_lock:
                self._latest_preview_image = None
                self._latest_preview_at = 0.0

    def apply_remote_desktop_profile(
        self,
        *,
        monitor_id: Any,
        fps: Any,
        max_width: Any,
    ) -> None:
        """Apply capture settings without renegotiating the WebRTC track."""
        self.monitor_id = _normalize_desktop_monitor_id(monitor_id)
        try:
            self.fps = max(1.0, min(30.0, float(fps or 8.0)))
        except Exception:
            self.fps = 8.0
        try:
            self.max_width = max(320, int(max_width or 1280))
        except Exception:
            self.max_width = 1280
        self._next_capture_due_at = None
        with self._latest_preview_lock:
            self._output_size = self._profiled_idle_frame_size()
            self._latest_preview_image = None
            self._latest_preview_at = 0.0

    @property
    def is_enabled(self) -> bool:
        return self._enabled.is_set()

    @property
    def output_size(self) -> Tuple[int, int]:
        with self._latest_preview_lock:
            width, height = self._output_size
        return max(1, int(width)), max(1, int(height))

    def desktop_bounds(self) -> Optional[Dict[str, int]]:
        bounds = _get_mss_monitor_bounds(self.monitor_id)
        if bounds is not None:
            return dict(bounds)
        width, height = self.output_size
        return {"left": 0, "top": 0, "width": width, "height": height}

    def remote_desktop_mapping(self) -> Dict[str, Any]:
        width, height = self.output_size
        return {
            "frame_width": width,
            "frame_height": height,
            "content_rect": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            "monitor_id": self.monitor_id,
            "monitor_bounds": self.desktop_bounds(),
        }

    def map_output_point_to_desktop(self, x: Any, y: Any) -> Optional[Tuple[int, int]]:
        try:
            x_ratio = float(x)
            y_ratio = float(y)
        except Exception:
            return None
        if not (0.0 <= x_ratio <= 1.0 and 0.0 <= y_ratio <= 1.0):
            return None
        bounds = self.desktop_bounds()
        if not bounds:
            return None
        width = max(1, int(bounds.get("width") or 1))
        height = max(1, int(bounds.get("height") or 1))
        absolute_x = int(bounds.get("left") or 0) + min(width - 1, int(round(x_ratio * (width - 1))))
        absolute_y = int(bounds.get("top") or 0) + min(height - 1, int(round(y_ratio * (height - 1))))
        return absolute_x, absolute_y

    async def _wait_for_capture_slot(self) -> float:
        """Wait only for the unused portion of the next desktop frame period."""
        now = time.monotonic()
        due_at = self._next_capture_due_at
        if due_at is not None:
            remaining = due_at - now
            if remaining > 0:
                await asyncio.sleep(remaining)
                now = time.monotonic()
        return now

    def _mark_capture_complete(self, started_at: float) -> None:
        interval = 1.0 / max(1.0, self.fps)
        self._next_capture_due_at = max(started_at + interval, time.monotonic())

    def _profiled_idle_frame_size(self) -> Tuple[int, int]:
        """Return a profile-sized, even fallback frame without reading pixels."""
        width = max(320, int(self.max_width or 1280))
        if width % 2:
            width -= 1
        height = max(2, int(round(width * 9.0 / 16.0)))
        if height % 2:
            height -= 1
        return width, height

    async def recv(self) -> Any:
        # While disabled, send low-rate black frames and never access the desktop.
        if not self._enabled.is_set():
            try:
                await asyncio.wait_for(self._enabled.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        capture_started_at: Optional[float] = None
        if self._enabled.is_set():
            capture_started_at = await self._wait_for_capture_slot()
        self._outbound_timestamp += max(1, int(round(90000 / self.fps)))
        pts, time_base = self._outbound_timestamp, self._time_base
        if self._enabled.is_set():
            self._native_captured_at_us = int(capture_started_at * 1_000_000) if capture_started_at is not None else time.monotonic_ns() // 1000
            image = await asyncio.to_thread(self._capture_image)
            self._remember_preview_image(image)
            if capture_started_at is not None:
                self._mark_capture_complete(capture_started_at)
        else:
            self._next_capture_due_at = None
            image = self._disabled_image()
        if VideoFrame is None:
            raise RuntimeError("PyAV is required for WebRTC video frames")
        video_frame = VideoFrame.from_image(image)
        OUTBOUND_VIDEO_TELEMETRY.record_frame(
            source="remote_desktop",
            width=video_frame.width,
            height=video_frame.height,
        )
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

    def capture_preview_image(self, *, max_width: int = 640) -> Any:
        """Capture a current desktop frame for a WebRTC/composite consumer.

        The desktop is mutable content, so every outbound preview request must
        read it again. Reusing the last frame, even briefly, lets a composite
        sender keep transmitting the first screenshot when its child track's
        ``recv()`` is not called directly. The latest-frame cache remains
        available through ``latest_preview_image`` for status UIs that do not
        own the outbound stream.
        """
        if not self._enabled.is_set():
            return self._resize_preview_image(self._disabled_image(), max_width=max_width)
        image = self._capture_image()
        self._remember_preview_image(image)
        return self._resize_preview_image(image, max_width=max_width)

    def latest_preview_image(self, *, max_width: int = 640) -> Any:
        with self._latest_preview_lock:
            image = self._latest_preview_image.copy() if self._latest_preview_image is not None else None
        if image is None:
            return None
        return self._resize_preview_image(image, max_width=max_width)

    def _remember_preview_image(self, image: Any) -> None:
        with self._latest_preview_lock:
            self._latest_preview_image = image.copy() if hasattr(image, "copy") else image
            self._latest_preview_at = time.monotonic()
            if hasattr(image, "size"):
                self._output_size = image.size

    def _disabled_image(self) -> Any:
        if Image is None:
            raise RuntimeError("Pillow is required for desktop video capture")
        with self._latest_preview_lock:
            size = self._output_size
        return Image.new("RGB", size, (0, 0, 0))

    @staticmethod
    def _resize_preview_image(image: Any, *, max_width: int = 640) -> Any:
        if image is None or Image is None or not hasattr(image, "width"):
            return image
        max_width = max(160, int(max_width or 640))
        if image.width <= max_width:
            return image.copy() if hasattr(image, "copy") else image
        ratio = max_width / float(image.width)
        return image.resize((max_width, max(1, int(image.height * ratio))), Image.Resampling.BILINEAR)

    def _capture_image(self) -> Any:
        if Image is None:
            raise RuntimeError("Pillow is required for desktop video capture")
        try:
            image = _capture_desktop_image_with_fallback(self.monitor_id)
            if image is None:
                raise RuntimeError("Desktop capture returned no frame")
            if image.width > self.max_width:
                ratio = self.max_width / float(image.width)
                image = image.resize((self.max_width, max(1, int(image.height * ratio))), Image.Resampling.BILINEAR)
            image = _ensure_even_rgb_image(image)
            if _is_probably_blank_desktop_capture(image):
                raise RuntimeError(_describe_desktop_capture_blank_frame(self.monitor_id))
            return image
        except Exception as exc:
            logger.debug("Desktop capture unavailable for video-call track: %s", exc)
            OUTBOUND_VIDEO_TELEMETRY.record_error(
                source="remote_desktop",
                stage="capture",
                detail=exc,
            )
            return self._placeholder_image(str(exc))

    def _placeholder_image(self, detail: str = "") -> Any:
        with self._latest_preview_lock:
            size = self._output_size
        image = Image.new("RGB", size, (15, 23, 42))
        if ImageDraw is not None:
            draw = ImageDraw.Draw(image)
            draw.text((32, 32), "AutoYou desktop video feed", fill=(248, 250, 252))
            draw.text((32, 70), "Waiting for screen capture permission or display access.", fill=(148, 163, 184))
            if detail:
                for index, line in enumerate(textwrap.wrap(str(detail), width=78)[:5]):
                    draw.text((32, 108 + (index * 26)), line, fill=(100, 116, 139))
        return image


class CameraVideoStreamTrack(_AiortcVideoStreamTrack):  # type: ignore[misc, valid-type]
    """Outbound WebRTC video track backed by webcam input via OpenCV."""

    kind = "video"

    def __init__(self, *, device_index: int = 0, fps: float = 24.0, max_width: int = 1280,
                 native_media: bool = False) -> None:
        super().__init__()  # type: ignore[misc]
        if type(native_media) is not bool:
            raise ValueError("invalid native camera ownership mode")
        self.device_index = int(device_index) if device_index is not None else 0
        self.fps = max(1.0, min(60.0, float(fps or 24.0)))
        self.max_width = max(320, int(max_width or 1280))
        if self.max_width % 2:
            self.max_width -= 1
        self._fallback_timestamp = 0
        self._time_base = Fraction(1, 90000)
        self._enabled = asyncio.Event()
        self._latest_image: Any = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._capture_stop = threading.Event()
        self._cap: Any = None
        self._failure_detail: str = ""
        self.native_media = native_media
        self._native_closed = False
        self._native_release_error: Optional[BaseException] = None
        self._native_captured_at_us = 0
        self._native_ready = threading.Event()
        default_width = min(640, self.max_width)
        self._output_size = (default_width, max(2, int(default_width * 3 / 4)))

    def enable(self) -> None:
        if self.native_media and (self._native_closed or self._native_release_error is not None):
            raise RuntimeError("native camera source is retired or failed cleanup")
        self._enabled.set()
        self._native_ready.clear()
        self._start_capture()

    def disable(self) -> None:
        self._enabled.clear()
        with self._lock:
            self._latest_image = None
        self._stop_capture()

    @property
    def is_enabled(self) -> bool:
        return self._enabled.is_set()

    @property
    def has_live_content(self) -> bool:
        """True only while real webcam frames are arriving.

        Composite tracks hide this source entirely when it is False, so a
        missing or broken webcam never adds a dead pane beside the screen feed.
        """
        if not self._enabled.is_set():
            return False
        with self._lock:
            return self._latest_image is not None

    def _start_capture(self) -> None:
        with self._lock:
            self._capture_stop.clear()
            if self._thread is not None and self._thread.is_alive():
                self._running = True
                return
            self._running = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True, name="Camera-Capture-Thread")
            self._thread.start()

    def _stop_capture(self) -> None:
        with self._lock:
            self._running = False
            self._capture_stop.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        with self._lock:
            if thread is self._thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def _set_failure(self, detail: str) -> None:
        with self._lock:
            normalized = str(detail or "")
            changed = normalized != self._failure_detail
            self._failure_detail = normalized
        if self.native_media:
            self._native_ready.set()
        if detail and changed:
            logger.error("Camera video source unavailable: %s", detail)
            OUTBOUND_VIDEO_TELEMETRY.record_error(source="camera", stage="capture", detail=detail)

    def _mark_capture_stopped(self) -> None:
        if self.native_media:
            self._native_ready.set()
        with self._lock:
            self._running = False
            if not self.native_media and self._thread is threading.current_thread():
                self._thread = None

    def fence_native(self) -> None:
        self._native_closed = True
        self._enabled.clear()
        self._native_ready.set()
        with self._lock:
            self._running = False
            self._latest_image = None
            self._capture_stop.set()
        stop = getattr(super(), "stop", None)
        if stop is not None:
            stop()

    def close_native(self) -> None:
        self.fence_native()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            # A timeout cannot transfer an unjoined camera into another source.
            thread.join()
        if self._native_release_error is not None:
            raise self._native_release_error
        if self._cap is not None:
            self._release_camera(self._cap)
        with self._lock:
            self._thread = None

    def ready_native(self) -> None:
        if not self._native_ready.wait(10):
            raise TimeoutError("native camera did not produce its initial frame")
        with self._lock:
            if self._native_closed or self._latest_image is None or self._native_release_error is not None:
                raise RuntimeError(self._failure_detail or "native camera source is unavailable")

    def _release_camera(self, cap: Any) -> None:
        if self.native_media and self._native_release_error is not None:
            raise self._native_release_error
        try:
            cap.release()
        except BaseException as error:
            if self.native_media:
                self._native_release_error = error
                self._capture_stop.set()
                raise
        else:
            if self._cap is cap:
                self._cap = None

    @staticmethod
    def _load_cv2() -> Any:
        import cv2

        return cv2

    def _open_camera(self, cv2_module: Any) -> Any:
        """Open only the configured camera; never substitute another capture device."""
        index = self.device_index
        backend = getattr(cv2_module, "CAP_DSHOW", None) if platform.system() == "Windows" else None
        attempts = [(index, backend), (index,)] if backend is not None else [(index,)]
        for args in attempts:
            try:
                cap = cv2_module.VideoCapture(*args)
            except Exception as err:
                logger.debug("Failed to open camera %s: %s", index, err)
                continue
            if self.native_media:
                self._cap = cap
            if cap is not None and cap.isOpened():
                return cap
            if cap is not None:
                self._release_camera(cap)
        return None

    def _capture_loop(self) -> None:
        try:
            cv2 = self._load_cv2()
        except ImportError:
            self._set_failure("OpenCV (cv2) is not installed in this AutoYou runtime.")
            self._mark_capture_stopped()
            return
        if Image is None:
            self._set_failure("Pillow is not installed in this AutoYou runtime.")
            self._mark_capture_stopped()
            return

        cap: Any = None
        try:
            while not self._capture_stop.is_set():
                cap = self._open_camera(cv2)
                if cap is None:
                    permission_hint = (
                        " On macOS, allow Camera access for AutoYou in System Settings > Privacy & Security."
                        if platform.system() == "Darwin"
                        else ""
                    )
                    with self._lock:
                        self._latest_image = None
                    self._set_failure(
                        f"Could not open camera {self.device_index}. Check that a webcam is connected and not in use."
                        + permission_hint
                    )
                    self._capture_stop.wait(1.0)
                    continue

                with self._lock:
                    self._cap = cap
                    self._failure_detail = ""
                with contextlib.suppress(Exception):
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

                consecutive_failures = 0
                failure_limit = max(3, int(self.fps))
                frame_interval = 1.0 / self.fps
                while not self._capture_stop.is_set():
                    started_at = time.monotonic()
                    try:
                        captured_at = time.monotonic_ns() // 1000
                        ret, frame = cap.read()
                    except Exception as err:
                        with self._lock:
                            self._latest_image = None
                        self._set_failure(f"Camera {self.device_index} read failed; reconnecting: {err}")
                        break
                    if not ret:
                        consecutive_failures += 1
                        if consecutive_failures >= failure_limit:
                            with self._lock:
                                self._latest_image = None
                            self._set_failure(
                                f"Camera {self.device_index} stopped delivering frames; reconnecting."
                            )
                            break
                        self._capture_stop.wait(min(0.05, frame_interval))
                        continue

                    consecutive_failures = 0
                    try:
                        if self.native_media and (len(frame.shape) != 3 or frame.shape[2] != 3 or
                                frame.shape[0] > 4096 or frame.shape[1] > 4096 or frame.nbytes > 32 * 1024 * 1024):
                            raise ValueError("native camera frame exceeded its source budget")
                        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        img = Image.fromarray(rgb_frame)
                        if img.width > self.max_width:
                            ratio = self.max_width / float(img.width)
                            img = img.resize(
                                (self.max_width, max(2, int(img.height * ratio))),
                                Image.Resampling.BILINEAR,
                            )
                        img = _ensure_even_rgb_image(img)
                        with self._lock:
                            self._latest_image = img
                            self._native_captured_at_us = captured_at
                            self._native_ready.set()
                            self._output_size = img.size
                            self._failure_detail = ""
                    except Exception as err:
                        logger.debug("Error converting camera frame: %s", err)
                        with self._lock:
                            self._latest_image = None
                        self._set_failure(f"Camera {self.device_index} frame conversion failed; reconnecting: {err}")
                        break

                    self._capture_stop.wait(max(0.0, frame_interval - (time.monotonic() - started_at)))

                self._release_camera(cap)
                cap = None
                with self._lock:
                    self._cap = None
                if not self._capture_stop.is_set():
                    self._capture_stop.wait(1.0)
        except Exception as exc:
            self._set_failure(f"Camera capture error: {exc}")
        finally:
            remaining = cap if cap is not None else self._cap
            if remaining is not None:
                with contextlib.suppress(Exception):
                    self._release_camera(remaining)
            with self._lock:
                if self._native_release_error is None:
                    self._cap = None
            self._mark_capture_stopped()

    def _placeholder_image(self, detail: str = "") -> Any:
        if Image is None or ImageDraw is None:
            raise RuntimeError("Pillow is required for fallback placeholders")
        with self._lock:
            size = self._output_size
        image = Image.new("RGB", size, (15, 23, 42))
        draw = ImageDraw.Draw(image)
        draw.text((32, 32), "AutoYou Camera video feed", fill=(248, 250, 252))
        draw.text((32, 70), "Camera stream inactive or not found.", fill=(148, 163, 184))
        if detail:
            for index, line in enumerate(textwrap.wrap(str(detail), width=78)[:5]):
                draw.text((32, 108 + (index * 26)), line, fill=(100, 116, 139))
        return image

    def _disabled_image(self) -> Any:
        if Image is None:
            raise RuntimeError("Pillow is required for camera video")
        with self._lock:
            size = self._output_size
        return Image.new("RGB", size, (0, 0, 0))

    def capture_preview_image(self, *, max_width: int = 640) -> Any:
        if not self._enabled.is_set():
            image = self._disabled_image()
        else:
            with self._lock:
                image = self._latest_image.copy() if self._latest_image is not None else None
                failure_detail = self._failure_detail
            if image is None:
                image = self._placeholder_image(failure_detail or "Waiting for camera frames...")
        max_width = max(160, int(max_width or 640))
        if image.width <= max_width:
            return image
        ratio = max_width / float(image.width)
        return image.resize(
            (max_width, max(2, int(image.height * ratio))),
            Image.Resampling.BILINEAR,
        )

    async def recv(self) -> Any:
        if not self._enabled.is_set():
            try:
                await asyncio.wait_for(self._enabled.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

        if self._enabled.is_set():
            await asyncio.sleep(1.0 / self.fps)
        if hasattr(self, "next_timestamp"):
            pts, time_base = await self.next_timestamp()  # type: ignore[attr-defined]
        else:
            self._fallback_timestamp += int(90000 / self.fps)
            pts, time_base = self._fallback_timestamp, self._time_base

        img = self.capture_preview_image(max_width=self.max_width)

        if VideoFrame is None:
            raise RuntimeError("PyAV is required for WebRTC video frames")
        video_frame = VideoFrame.from_image(img)
        OUTBOUND_VIDEO_TELEMETRY.record_frame(
            source="camera",
            width=video_frame.width,
            height=video_frame.height,
        )
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

    def __del__(self) -> None:
        try:
            self.disable()
        except Exception:
            pass


class CompositeVideoStreamTrack(_AiortcVideoStreamTrack):  # type: ignore[misc, valid-type]
    """One outbound track that places local video sources side by side."""

    kind = "video"

    def __init__(
        self,
        sources: List[Tuple[str, Any]],
        *,
        fps: float = 8.0,
        max_width: int = 1280,
    ) -> None:
        super().__init__()  # type: ignore[misc]
        self.sources = [(str(name or "video").strip() or "video", track) for name, track in sources if track is not None]
        self.fps = max(1.0, min(30.0, float(fps or 8.0)))
        self.max_width = max(320, int(max_width or 1280))
        if self.max_width % 2:
            self.max_width -= 1
        self.output_height = max(180, int(round(self.max_width * 9 / 16)))
        if self.output_height % 2:
            self.output_height -= 1
        self._fallback_timestamp = 0
        self._time_base = Fraction(1, 90000)
        self._outbound_timestamp = 0
        self._enabled = asyncio.Event()
        self._disabled_source_names: set[str] = set()
        self._game_mode = False

    @property
    def monitor_id(self) -> int:
        for name, track in self.sources:
            if name == "remote_desktop" and hasattr(track, "monitor_id"):
                return _normalize_desktop_monitor_id(getattr(track, "monitor_id", 0))
        return 0

    @monitor_id.setter
    def monitor_id(self, value: Any) -> None:
        normalized = _normalize_desktop_monitor_id(value)
        for name, track in self.sources:
            if name == "remote_desktop" and hasattr(track, "monitor_id"):
                setattr(track, "monitor_id", normalized)

    def apply_remote_desktop_profile(
        self,
        *,
        monitor_id: Any,
        fps: Any,
        max_width: Any,
    ) -> None:
        """Apply desktop capture and composite sizing changes in place."""
        try:
            normalized_fps = max(1.0, min(30.0, float(fps or 8.0)))
        except Exception:
            normalized_fps = 8.0
        try:
            normalized_width = max(320, int(max_width or 1280))
        except Exception:
            normalized_width = 1280
        if normalized_width % 2:
            normalized_width -= 1
        self.fps = normalized_fps
        self.max_width = normalized_width
        self.output_height = max(180, int(round(normalized_width * 9 / 16)))
        if self.output_height % 2:
            self.output_height -= 1
        self.monitor_id = monitor_id
        desktop_track = self._remote_desktop_track()
        apply_profile = getattr(desktop_track, "apply_remote_desktop_profile", None)
        if callable(apply_profile):
            apply_profile(
                monitor_id=monitor_id,
                fps=normalized_fps,
                max_width=normalized_width,
            )

    def source_names(self) -> List[str]:
        return [name for name, _track in self.sources]

    def set_remote_desktop_enabled(self, enabled: bool) -> None:
        """Enable or suppress only the desktop child of a stitched video track."""
        if enabled:
            self._disabled_source_names.discard("remote_desktop")
        else:
            self._disabled_source_names.add("remote_desktop")
        for name, track in self.sources:
            if name != "remote_desktop":
                continue
            action = "enable" if enabled and self._enabled.is_set() else "disable"
            callback = getattr(track, action, None)
            if callable(callback):
                callback()

    def set_game_mode(self, enabled: bool) -> None:
        self._game_mode = bool(enabled)
        if not self._enabled.is_set():
            return
        for name, track in self.sources:
            if name == "remote_desktop":
                continue
            action = "disable" if self._game_mode or name in self._disabled_source_names else "enable"
            callback = getattr(track, action, None)
            if callable(callback):
                callback()

    @property
    def output_size(self) -> Tuple[int, int]:
        return self.max_width, self.output_height

    def _remote_desktop_track(self) -> Any:
        for name, track in self.sources:
            if name == "remote_desktop":
                return track
        return None

    def remote_desktop_mapping(self) -> Dict[str, Any]:
        desktop_track = self._remote_desktop_track()
        if desktop_track is None:
            return {}

        live_sources = self._live_sources()
        source_index = next(
            (index for index, (name, _track) in enumerate(live_sources) if name == "remote_desktop"),
            None,
        )
        if source_index is None:
            return {}

        count = len(live_sources)
        columns = 1 if count == 1 else 2
        rows = (count + columns - 1) // columns
        gap = 4 if count > 1 else 0
        cell_width = self.max_width // columns
        cell_height = self.output_height // rows
        column = source_index % columns
        row = source_index // columns
        left = column * cell_width
        top = row * cell_height
        content_width = (self.max_width - left if column == columns - 1 else cell_width) - gap
        content_height = (self.output_height - top if row == rows - 1 else cell_height) - gap

        source_size = getattr(desktop_track, "output_size", (self.max_width, self.output_height))
        try:
            source_width = max(1, int(source_size[0]))
            source_height = max(1, int(source_size[1]))
        except Exception:
            source_width, source_height = self.max_width, self.output_height
        scale = min(content_width / float(source_width), content_height / float(source_height))
        fitted_width = max(1, int(source_width * scale))
        fitted_height = max(1, int(source_height * scale))
        fitted_left = left + max(0, (content_width - fitted_width) // 2)
        fitted_top = top + max(0, (content_height - fitted_height) // 2)
        mapping = {
            "frame_width": self.max_width,
            "frame_height": self.output_height,
            "content_rect": {
                "x": fitted_left / float(self.max_width),
                "y": fitted_top / float(self.output_height),
                "width": fitted_width / float(self.max_width),
                "height": fitted_height / float(self.output_height),
            },
            "source_index": source_index,
            "source_count": count,
            "monitor_id": self.monitor_id,
        }
        desktop_bounds = getattr(desktop_track, "desktop_bounds", None)
        if callable(desktop_bounds):
            mapping["monitor_bounds"] = desktop_bounds()
        return mapping

    def map_output_point_to_desktop(self, x: Any, y: Any) -> Optional[Tuple[int, int]]:
        try:
            x_ratio = float(x)
            y_ratio = float(y)
        except Exception:
            return None
        mapping = self.remote_desktop_mapping()
        rect = mapping.get("content_rect") if isinstance(mapping, dict) else None
        if not isinstance(rect, dict):
            return None
        left = float(rect.get("x") or 0.0)
        top = float(rect.get("y") or 0.0)
        width = float(rect.get("width") or 0.0)
        height = float(rect.get("height") or 0.0)
        if width <= 0.0 or height <= 0.0:
            return None
        if not (left <= x_ratio <= left + width and top <= y_ratio <= top + height):
            return None
        desktop_track = self._remote_desktop_track()
        mapper = getattr(desktop_track, "map_output_point_to_desktop", None)
        if not callable(mapper):
            return None
        return mapper((x_ratio - left) / width, (y_ratio - top) / height)

    def _live_sources(self) -> List[Tuple[str, Any]]:
        """Configured sources minus the ones reporting no live content.

        Sources without a has_live_content signal (for example the screen
        feed) are always composed; an optional webcam that is missing or has
        stopped delivering frames is dropped so the remaining feed fills the
        whole frame instead of sitting beside a dead pane.
        """
        allowed = [
            (name, track)
            for name, track in self.sources
            if name not in self._disabled_source_names and (not self._game_mode or name == "remote_desktop")
        ]
        live = [
            (name, track)
            for name, track in allowed
            if getattr(track, "has_live_content", True) is not False
        ]
        return live or allowed

    def enable(self) -> None:
        self._enabled.set()
        for name, track in self.sources:
            action = "disable" if name in self._disabled_source_names or (self._game_mode and name != "remote_desktop") else "enable"
            callback = getattr(track, action, None)
            if callable(callback):
                callback()

    def disable(self) -> None:
        self._enabled.clear()
        for _name, track in self.sources:
            disable = getattr(track, "disable", None)
            if callable(disable):
                disable()

    @property
    def is_enabled(self) -> bool:
        return self._enabled.is_set()

    async def _source_image(self, track: Any) -> Any:
        preview = getattr(track, "capture_preview_image", None)
        if callable(preview):
            return await asyncio.to_thread(preview, max_width=self.max_width)
        frame = await track.recv()
        return frame.to_image().convert("RGB")

    def capture_preview_image(self, *, max_width: int = 640) -> Any:
        """Compose a synchronous preview without advancing child WebRTC tracks."""
        width = max(2, min(self.max_width, int(max_width or 640)))
        if width % 2:
            width -= 1
        height = max(2, int(round(width * 9 / 16)))
        if height % 2:
            height -= 1
        if not self._enabled.is_set():
            if Image is None:
                raise RuntimeError("Pillow is required for video compositing")
            return Image.new("RGB", (width, height), (0, 0, 0))

        images = []
        for name, track in self._live_sources():
            preview = getattr(track, "capture_preview_image", None)
            if not callable(preview):
                images.append(self._placeholder_image(f"{name} unavailable"))
                continue
            try:
                images.append(preview(max_width=width).convert("RGB"))
            except Exception as exc:
                OUTBOUND_VIDEO_TELEMETRY.record_error(source=name, stage="composite_preview", detail=exc)
                images.append(self._placeholder_image(f"{name} unavailable"))
        return self._compose_images(images, canvas_width=width)

    async def recv(self) -> Any:
        if not self._enabled.is_set():
            try:
                await asyncio.wait_for(self._enabled.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

        if self._enabled.is_set():
            await asyncio.sleep(1.0 / self.fps)
        self._outbound_timestamp += max(1, int(round(90000 / self.fps)))
        pts, time_base = self._outbound_timestamp, self._time_base

        if not self._enabled.is_set():
            image = self._disabled_image()
        else:
            self._native_captured_at_us = time.monotonic_ns() // 1000
            live_sources = self._live_sources()
            results = await asyncio.gather(
                *(self._source_image(track) for _name, track in live_sources),
                return_exceptions=True,
            )
            images = []
            for (name, _track), result in zip(live_sources, results):
                if isinstance(result, Exception):
                    OUTBOUND_VIDEO_TELEMETRY.record_error(source=name, stage="composite", detail=result)
                    images.append(self._placeholder_image(f"{name} unavailable"))
                    continue
                try:
                    images.append(result.convert("RGB"))
                except Exception as exc:
                    OUTBOUND_VIDEO_TELEMETRY.record_error(source=name, stage="composite", detail=exc)
                    images.append(self._placeholder_image(f"{name} unavailable"))
            image = await asyncio.to_thread(self._compose_images, images)
        if VideoFrame is None:
            raise RuntimeError("PyAV is required for WebRTC video frames")
        video_frame = VideoFrame.from_image(image)
        OUTBOUND_VIDEO_TELEMETRY.record_frame(
            source="composite:" + "+".join(self.source_names()),
            width=video_frame.width,
            height=video_frame.height,
        )
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

    def _compose_images(self, images: List[Any], *, canvas_width: Optional[int] = None) -> Any:
        if Image is None:
            raise RuntimeError("Pillow is required for video compositing")
        width = max(2, int(canvas_width or self.max_width))
        if width % 2:
            width -= 1
        output_height = max(2, int(round(width * 9 / 16)))
        if output_height % 2:
            output_height -= 1
        if not images:
            return self._fit_inside(self._placeholder_image("Waiting for selected video sources..."), width, output_height)
        count = len(images)
        columns = 1 if count == 1 else 2
        rows = (count + columns - 1) // columns
        canvas = Image.new("RGB", (width, output_height), (0, 0, 0))
        gap = 4 if count > 1 else 0
        cell_width = width // columns
        cell_height = output_height // rows
        for index, image in enumerate(images):
            column = index % columns
            row = index // columns
            left = column * cell_width
            top = row * cell_height
            cell_content_width = (width - left if column == columns - 1 else cell_width) - gap
            cell_content_height = (output_height - top if row == rows - 1 else cell_height) - gap
            fitted = self._fit_inside(image, cell_content_width, cell_content_height)
            canvas.paste(
                fitted,
                (
                    left + max(0, (cell_content_width - fitted.width) // 2),
                    top + max(0, (cell_content_height - fitted.height) // 2),
                ),
            )
        return canvas

    @staticmethod
    def _fit_inside(image: Any, width: int, height: int) -> Any:
        image = image.convert("RGB")
        scale = min(width / float(max(1, image.width)), height / float(max(1, image.height)))
        size = (max(2, int(image.width * scale)), max(2, int(image.height * scale)))
        if image.size == size:
            return image
        return image.resize(size, Image.Resampling.BILINEAR)

    def _disabled_image(self) -> Any:
        if Image is None:
            raise RuntimeError("Pillow is required for video compositing")
        return Image.new("RGB", (self.max_width, self.output_height), (0, 0, 0))

    def _placeholder_image(self, detail: str = "") -> Any:
        image = Image.new("RGB", (self.max_width, self.output_height), (15, 23, 42))
        if ImageDraw is not None:
            draw = ImageDraw.Draw(image)
            draw.text((32, 32), "AutoYou video feed", fill=(248, 250, 252))
            if detail:
                draw.text((32, 70), detail[:120], fill=(148, 163, 184))
        return image


class _PyAudioRuntime:
    """Share one native PortAudio lifetime across local capture tracks."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._native_lock = threading.RLock()
        self._instance: Any = None
        self._users = 0
        self._generation = 0
        self._refresh_pending = False
        self._poisoned_reason = ""

    @contextlib.contextmanager
    def serialized(self, *, timeout: float = 2.0):
        """Serialize native PortAudio calls without blocking runtime state reads."""
        acquired = self._native_lock.acquire(timeout=max(0.05, float(timeout)))
        if not acquired:
            raise RuntimeError("Timed out waiting for the native PortAudio operation lock")
        try:
            yield
        finally:
            self._native_lock.release()

    def acquire(
        self,
        pyaudio_module: Any,
        stop_event: Optional[threading.Event] = None,
        *,
        wait_timeout: float = 2.0,
    ) -> Any:
        deadline = time.monotonic() + max(0.05, float(wait_timeout))
        with self._condition:
            while self._refresh_pending and self._users:
                if stop_event is not None and stop_event.is_set():
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Timed out waiting for existing PortAudio captures to refresh")
                self._condition.wait(min(0.05, remaining))
            if self._poisoned_reason:
                raise RuntimeError(
                    "PortAudio runtime is quarantined until AutoYou restarts: "
                    + self._poisoned_reason
                )
            if self._refresh_pending and not self._users:
                self._terminate_locked()
            if self._poisoned_reason:
                raise RuntimeError(
                    "PortAudio runtime is quarantined until AutoYou restarts: "
                    + self._poisoned_reason
                )
            if self._instance is None:
                with self.serialized():
                    self._instance = pyaudio_module.PyAudio()
                self._generation += 1
            self._users += 1
            return self._instance

    def generation(self, instance: Any) -> int:
        with self._condition:
            return self._generation if instance is self._instance else -1

    def request_refresh(self, instance: Any) -> None:
        with self._condition:
            if instance is not self._instance or self._poisoned_reason:
                return
            if not self._refresh_pending:
                self._refresh_pending = True
                self._generation += 1
            self._condition.notify_all()

    def needs_refresh(self, instance: Any, generation: int) -> bool:
        with self._condition:
            return (
                instance is not self._instance
                or bool(self._poisoned_reason)
                or self._refresh_pending
                or generation != self._generation
            )

    def quarantine(self, instance: Any, detail: Any) -> None:
        """Fail closed after uncertain native cleanup; never layer a new manager over it."""
        with self._condition:
            if instance is not self._instance:
                return
            self._poisoned_reason = str(detail or "native PortAudio cleanup failed")
            self._refresh_pending = False
            self._generation += 1
            self._condition.notify_all()

    def release(self, instance: Any) -> None:
        with self._condition:
            if instance is not self._instance or self._users <= 0:
                return
            self._users -= 1
            if self._users:
                self._condition.notify_all()
                return
            if self._poisoned_reason:
                self._condition.notify_all()
                return
            self._terminate_locked()

    def _terminate_locked(self) -> bool:
        instance = self._instance
        if instance is None:
            self._refresh_pending = False
            self._condition.notify_all()
            return True

        completed = threading.Event()
        termination_error: list[BaseException] = []

        def terminate_native() -> None:
            try:
                with self.serialized():
                    instance.terminate()
            except BaseException as exc:
                termination_error.append(exc)
            finally:
                completed.set()

        threading.Thread(
            target=terminate_native,
            daemon=True,
            name="PortAudio-Terminate-Thread",
        ).start()
        if not completed.wait(timeout=2.5):
            self._poisoned_reason = "native PortAudio termination did not finish"
            self._refresh_pending = False
            self._generation += 1
            self._condition.notify_all()
            return False
        if termination_error:
            self._poisoned_reason = f"native PortAudio termination failed: {termination_error[0]}"
            self._refresh_pending = False
            self._generation += 1
            self._condition.notify_all()
            return False

        self._instance = None
        self._refresh_pending = False
        self._condition.notify_all()
        return True

    def close_stream(self, stream: Any) -> bool:
        if stream is None:
            return True
        with self.serialized():
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception as exc:
                logger.warning("Native PortAudio stream close failed: %s", exc)
                return False
        return True


_PYAUDIO_RUNTIME = _PyAudioRuntime()


def _load_pyaudio_module(system_name: Optional[str] = None) -> Any:
    if (system_name or platform.system()) == "Windows":
        try:
            import pyaudiowpatch

            return pyaudiowpatch
        except ImportError:
            pass
    import pyaudio

    return pyaudio


@contextlib.contextmanager
def shared_pyaudio_instance(pyaudio_module: Any = None, *, refresh: bool = False):
    """Borrow PortAudio for short enumeration calls; never terminate or retain it."""
    if pyaudio_module is None:
        pyaudio_module = _load_pyaudio_module()

    if refresh:
        stale_instance = _PYAUDIO_RUNTIME.acquire(pyaudio_module)
        _PYAUDIO_RUNTIME.request_refresh(stale_instance)
        _PYAUDIO_RUNTIME.release(stale_instance)
    instance = _PYAUDIO_RUNTIME.acquire(pyaudio_module)
    try:
        with _PYAUDIO_RUNTIME.serialized():
            yield instance
    finally:
        _PYAUDIO_RUNTIME.release(instance)


def _pyaudio_host_api_rank(system_name: str, host_api_name: str) -> int:
    normalized = str(host_api_name or "").strip().lower().replace("_", " ").replace("-", " ")
    priorities = {
        "Windows": (
            ("wasapi",),
            ("wdm ks", "wdmks", "kernel streaming"),
            ("directsound", "direct sound"),
            ("mme",),
        ),
        "Darwin": (("core audio", "coreaudio"),),
        "Linux": (("pipewire", "pulse"), ("alsa",), ("jack",), ("oss",)),
    }.get(system_name, ())
    for rank, markers in enumerate(priorities):
        if any(marker in normalized for marker in markers):
            return rank
    return len(priorities) + 1


def enumerate_pyaudio_input_devices(
    pyaudio_module: Any = None,
    system_name: Optional[str] = None,
    *,
    refresh: bool = False,
) -> List[Dict[str, Any]]:
    """Return one logical input per exact name, preferring the native host API."""
    effective_system = system_name or platform.system()
    generic_windows_aliases = {
        "microsoft sound mapper - input",
        "primary sound capture driver",
    }
    selected: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    with shared_pyaudio_instance(pyaudio_module, refresh=refresh) as instance:
        host_apis: Dict[int, str] = {}
        for index in range(instance.get_host_api_count()):
            try:
                host_apis[index] = str(instance.get_host_api_info_by_index(index).get("name", ""))
            except Exception:
                continue
        try:
            default_index = int(instance.get_default_input_device_info()["index"])
        except Exception:
            default_index = -1

        for index in range(instance.get_device_count()):
            try:
                info = instance.get_device_info_by_index(index)
                channels = int(info.get("maxInputChannels", 0) or 0)
                name = str(info.get("name", "") or "").strip()
            except Exception:
                continue
            if channels <= 0 or not name:
                continue
            logical_name = name.casefold()
            if effective_system == "Windows" and logical_name in generic_windows_aliases:
                continue
            try:
                host_api_index = int(info.get("hostApi", -1))
            except (TypeError, ValueError):
                host_api_index = -1
            host_api = host_apis.get(host_api_index, "Unknown")
            rank = _pyaudio_host_api_rank(effective_system, host_api)
            candidate = {
                "id": index,
                "selector": name,
                "name": name,
                "host_api": host_api,
                "channels": channels,
                "sample_rate": int(float(info.get("defaultSampleRate", 0) or 0)),
                "is_default": index == default_index,
            }
            previous = selected.get(logical_name)
            if previous is None:
                selected[logical_name] = (rank, candidate)
                continue
            previous_rank, previous_device = previous
            candidate["is_default"] = bool(candidate["is_default"] or previous_device["is_default"])
            if rank < previous_rank:
                selected[logical_name] = (rank, candidate)
            else:
                previous_device["is_default"] = bool(
                    previous_device["is_default"] or candidate["is_default"]
                )

    return sorted(
        (device for _rank, device in selected.values()),
        key=lambda item: (not item["is_default"], item["name"].casefold()),
    )


class LocalAudioInputTrack(MediaStreamTrack):
    """Outbound WebRTC audio track backed by local microphone/speaker capture via PyAudio."""

    kind = "audio"

    def __init__(
        self,
        *,
        device_index_or_name: Any = "default",
        capture_loopback: bool = False,
        sample_rate: int = 48000,
        native_media: bool = False,
    ) -> None:
        if type(native_media) is not bool:
            raise ValueError("Native audio mode must be explicit")
        super().__init__()
        import queue
        self.device_index_or_name = device_index_or_name
        self.capture_loopback = bool(capture_loopback)
        self.sample_rate = int(sample_rate or 48000)
        self.time_base = Fraction(1, self.sample_rate)
        self.frame_size = max(1, int(self.sample_rate * 0.02))
        self.pts = 0
        self._fallback_start_time = 0
        self._enabled = asyncio.Event()
        self._lock = threading.Lock()
        self._running = False
        self._stop_event: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._pyaudio_instance: Any = None
        self._stream: Any = None
        self._native_process: Optional[subprocess.Popen] = None
        self._capture_device_name = ""
        self._capture_rate = 0
        self._capture_channels = 0
        self._capture_error = ""
        self._native_media = native_media
        self._native_drops = 0
        self._observed_native_drops = 0
        self._audio_queue: queue.Queue = queue.Queue(maxsize=8 if native_media else 100)

    def enable(self) -> None:
        self._clear_audio_queue()
        self._enabled.set()
        self._start_capture()

    def disable(self) -> None:
        self._enabled.clear()
        self._stop_capture()
        self._clear_audio_queue()

    def stop(self) -> None:
        """Honor the standard MediaStreamTrack lifecycle for native capture."""
        self.disable()
        super().stop()

    @property
    def is_enabled(self) -> bool:
        return self._enabled.is_set()

    def capture_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "device_open": self._stream is not None or (
                    self._native_process is not None and self._native_process.poll() is None
                ),
                "loopback": self.capture_loopback,
                "device_name": self._capture_device_name,
                "capture_rate": self._capture_rate,
                "channels": self._capture_channels,
                "last_error": self._capture_error,
            }

    def _set_capture_error(self, detail: Any) -> None:
        with self._lock:
            self._capture_error = str(detail or "")

    def _clear_audio_queue(self) -> None:
        while True:
            try:
                self._audio_queue.get_nowait()
            except Exception:
                return

    def _start_capture(self) -> None:
        with self._lock:
            if not self._enabled.is_set():
                return
            if self._thread is not None and self._thread.is_alive():
                return
            stop_event = threading.Event()
            self._running = True
            self._stop_event = stop_event
            self._thread = threading.Thread(
                target=self._capture_loop,
                args=(stop_event,),
                daemon=True,
                name="Audio-Loopback-Capture-Thread" if self.capture_loopback else "Audio-Input-Capture-Thread",
            )
            self._thread.start()

    def _stop_capture(self) -> None:
        with self._lock:
            self._running = False
            stop_event = self._stop_event
            thread = self._thread
            process = self._native_process
        if stop_event is not None:
            stop_event.set()
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
            if thread.is_alive():
                detail = "native audio cleanup did not finish; restart AutoYou before capturing audio again"
                logger.warning("Local audio capture thread did not stop promptly; %s.", detail)
                with self._lock:
                    instance = self._pyaudio_instance
                    self._stream = None
                    self._capture_error = detail
                if instance is not None:
                    _PYAUDIO_RUNTIME.quarantine(instance, detail)
            else:
                with self._lock:
                    if self._thread is thread:
                        self._thread = None
                        self._stop_event = None

    def _resolve_capture_device_index(self) -> Optional[int]:
        device_index: Optional[int] = None
        if self.capture_loopback:
            loopback_markers = (
                ("loopback", "speaker", "stereo mix", "what u hear")
                if platform.system() == "Windows"
                else ("blackhole", "loopback", "soundflower", "monitor")
            )
            try:
                if platform.system() == "Windows":
                    get_default_loopback = getattr(
                        self._pyaudio_instance,
                        "get_default_wasapi_loopback",
                        None,
                    )
                    if callable(get_default_loopback):
                        try:
                            loopback_info = get_default_loopback()
                            if int(loopback_info.get("maxInputChannels", 0) or 0) > 0:
                                return int(loopback_info["index"])
                        except Exception as err:
                            logger.debug("Default WASAPI loopback device unavailable: %s", err)
                    get_loopbacks = getattr(self._pyaudio_instance, "get_loopback_device_info_generator", None)
                    if callable(get_loopbacks):
                        try:
                            for loopback_info in get_loopbacks():
                                if int(loopback_info.get("maxInputChannels", 0) or 0) > 0:
                                    return int(loopback_info["index"])
                        except Exception as err:
                            logger.debug("Failed enumerating WASAPI loopback devices: %s", err)
                wasapi_idx = -1
                if platform.system() == "Windows":
                    for i in range(self._pyaudio_instance.get_host_api_count()):
                        api_info = self._pyaudio_instance.get_host_api_info_by_index(i)
                        if "wasapi" in str(api_info.get("name", "")).lower():
                            wasapi_idx = i
                            break
                for i in range(self._pyaudio_instance.get_device_count()):
                    dev_info = self._pyaudio_instance.get_device_info_by_index(i)
                    if wasapi_idx != -1 and dev_info.get("hostApi") != wasapi_idx:
                        continue
                    if int(dev_info.get("maxInputChannels", 0) or 0) <= 0:
                        continue
                    device_name = str(dev_info.get("name", "")).lower()
                    if any(marker in device_name for marker in loopback_markers):
                        return i
            except Exception as err:
                logger.debug("Failed finding speaker loopback device: %s", err)
            logger.debug(
                "No speaker-loopback capture device found; computer-sound capture will serve silence. "
                "On macOS/Linux this needs a virtual loopback driver such as BlackHole."
            )
            return None

        if self.device_index_or_name != "default":
            try:
                num_devices = self._pyaudio_instance.get_device_count()
                device_str = str(self.device_index_or_name).strip().lower()
                input_devices: list[tuple[int, Dict[str, Any]]] = []
                for i in range(num_devices):
                    info = self._pyaudio_instance.get_device_info_by_index(i)
                    if int(info.get("maxInputChannels", 0) or 0) <= 0:
                        continue
                    input_devices.append((i, info))
                    if device_str == str(i):
                        return i
                preferred_host_api = self._preferred_audio_host_api_index()
                preferred_devices = [
                    item for item in input_devices if item[1].get("hostApi") == preferred_host_api
                ]
                for candidates in (preferred_devices, input_devices):
                    for i, info in candidates:
                        if device_str == str(info.get("name", "")).strip().lower():
                            return i
                for candidates in (preferred_devices, input_devices):
                    for i, info in candidates:
                        if device_str in str(info.get("name", "")).lower():
                            return i
                logger.warning(
                    "Requested audio input device %r not found; falling back to the default input device.",
                    self.device_index_or_name,
                )
            except Exception as err:
                logger.debug("Failed matching audio device: %s", err)

        try:
            device_index = self._pyaudio_instance.get_default_input_device_info()["index"]
        except Exception as err:
            logger.debug("No default input audio device available: %s", err)

        if device_index is None:
            try:
                num_devices = self._pyaudio_instance.get_device_count()
                for i in range(num_devices):
                    info = self._pyaudio_instance.get_device_info_by_index(i)
                    if int(info.get("maxInputChannels", 0) or 0) > 0:
                        device_index = i
                        logger.info(
                            "Using first available local audio input device for outbound WebRTC audio: %s",
                            info.get("name", i),
                        )
                        break
            except Exception as err:
                logger.debug("Failed scanning local audio input devices: %s", err)
        return device_index

    def _preferred_audio_host_api_index(self) -> Optional[int]:
        marker = {"Windows": "wasapi", "Darwin": "core audio", "Linux": "alsa"}.get(platform.system())
        if not marker:
            return None
        try:
            for i in range(self._pyaudio_instance.get_host_api_count()):
                info = self._pyaudio_instance.get_host_api_info_by_index(i)
                if marker in str(info.get("name", "")).lower():
                    return i
        except Exception:
            pass
        return None

    def _open_capture_stream(self, pyaudio_module: Any, device_index: int) -> tuple[Any, int, int]:
        """Open the input stream, falling back to the device's native rate/channels."""
        device_rate = 0
        max_channels = 1
        try:
            info = self._pyaudio_instance.get_device_info_by_index(device_index)
            device_rate = int(float(info.get("defaultSampleRate") or 0) or 0)
            max_channels = max(1, int(info.get("maxInputChannels", 1) or 1))
        except Exception:
            pass
        attempts: list[tuple[int, int]] = []
        for channels in (1, 2) if max_channels >= 2 else (1,):
            for rate in (self.sample_rate, device_rate, 44100, 16000):
                if rate and (rate, channels) not in attempts:
                    attempts.append((rate, channels))
        last_error: Optional[Exception] = None
        for rate, channels in attempts:
            try:
                stream = self._pyaudio_instance.open(
                    format=pyaudio_module.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=device_index,
                    frames_per_buffer=max(1, int(rate * 0.02)),
                )
                return stream, rate, channels
            except Exception as err:
                last_error = err
        raise RuntimeError(f"Unable to open audio input device {device_index}: {last_error}")

    @staticmethod
    def _read_capture_chunk(
        stream: Any,
        read_frames: int,
        stop_event: threading.Event,
        pyaudio_instance: Any,
        runtime_generation: int,
        *,
        capture_rate: Optional[int] = None,
    ) -> Optional[Any]:
        """Poll then read immediately under the process-wide PortAudio lock."""
        get_read_available = getattr(stream, "get_read_available", None)
        if not callable(get_read_available):
            raise RuntimeError("PyAudio stream does not expose non-blocking read availability")
        while not stop_event.is_set():
            if _PYAUDIO_RUNTIME.needs_refresh(pyaudio_instance, runtime_generation):
                return None
            with _PYAUDIO_RUNTIME.serialized():
                available = int(get_read_available() or 0)
                if available >= read_frames:
                    if capture_rate is not None:
                        if type(capture_rate) is not int or not 8000 <= capture_rate <= 192000:
                            raise ValueError("Unsupported native input clock")
                        captured_at_us = time.monotonic_ns()//1000-available*1_000_000//capture_rate
                        return stream.read(read_frames, exception_on_overflow=False),captured_at_us
                    return stream.read(read_frames, exception_on_overflow=False)
            stop_event.wait(0.01)
        return None

    def _normalize_capture_chunk(
        self,
        data: bytes,
        *,
        capture_rate: int,
        capture_channels: int,
        numpy_module: Any,
    ) -> bytes:
        if capture_channels == 2:
            if numpy_module is None:
                data = b"".join(data[offset : offset + 2] for offset in range(0, len(data), 4))
            else:
                samples = numpy_module.frombuffer(data, dtype=numpy_module.int16).reshape(-1, 2)
                data = (
                    (samples.astype(numpy_module.int32).sum(axis=1) // 2)
                    .astype(numpy_module.int16)
                    .tobytes()
                )
        if capture_rate != self.sample_rate:
            if numpy_module is None:
                raise RuntimeError("NumPy is required to resample local audio capture")
            samples = numpy_module.frombuffer(data, dtype=numpy_module.int16)
            if samples.size:
                positions = numpy_module.arange(self.frame_size) * (capture_rate / self.sample_rate)
                data = (
                    numpy_module.interp(positions, numpy_module.arange(samples.size), samples)
                    .clip(-32768, 32767)
                    .astype(numpy_module.int16)
                    .tobytes()
                )
        target_bytes = self.frame_size * 2
        return data[:target_bytes].ljust(target_bytes, b"\0")

    def _queue_audio_chunk(self, data: bytes, *, captured_at_us: Optional[int] = None) -> None:
        item: Any = data
        if self._native_media:
            if not isinstance(data,bytes) or not 0 < len(data) <= self.frame_size*2 or len(data)%2:
                raise ValueError("Unsupported bounded native capture chunk")
            now_us = time.monotonic_ns()//1000
            if captured_at_us is None:
                captured_at_us = now_us-len(data)*1_000_000//(2*self.sample_rate)
            if type(captured_at_us) is not int or not 0 <= captured_at_us <= now_us:
                raise ValueError("Invalid native capture clock")
            item = (data,captured_at_us)
        try:
            self._audio_queue.put_nowait(item)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
                if self._native_media: self._native_drops += 1
            except queue.Empty:
                pass
            try:
                self._audio_queue.put_nowait(item)
            except queue.Full:
                pass

    def _capture_macos_system_audio(self, helper: Path, stop_event: threading.Event) -> None:
        """Read signed 16-bit mono 48 kHz PCM from the native capture helper."""
        process = subprocess.Popen(
            [str(helper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL
        )
        with self._lock:
            self._native_process = process
            self._capture_device_name = "macOS ScreenCaptureKit"
            self._capture_rate = 48000
            self._capture_channels = 1
            self._capture_error = ""
        frame_bytes = self.frame_size * 2
        pending = bytearray()
        pending_at_us: Optional[int] = None
        try:
            assert process.stdout is not None
            while not stop_event.is_set():
                if process.poll() is not None:
                    detail = process.stderr.read(1024).decode("utf-8", "replace").strip() if process.stderr else ""
                    raise RuntimeError(detail or f"ScreenCaptureKit exited ({process.returncode})")
                readable, _, _ = select.select([process.stdout], [], [], 0.1)
                if not readable:
                    continue
                data = os.read(process.stdout.fileno(), frame_bytes * 10)
                if not data:
                    raise RuntimeError("ScreenCaptureKit audio stream closed")
                if not pending:
                    pending_at_us = time.monotonic_ns()//1000-len(data)*1_000_000//(2*self.sample_rate)
                pending.extend(data)
                while len(pending) >= frame_bytes:
                    self._queue_audio_chunk(bytes(pending[:frame_bytes]),captured_at_us=pending_at_us)
                    del pending[:frame_bytes]
                    if pending_at_us is not None:
                        pending_at_us += self.frame_size*1_000_000//self.sample_rate
        finally:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            with self._lock:
                if self._native_process is process:
                    self._native_process = None

    def _capture_loop(self, stop_event: Optional[threading.Event] = None) -> None:
        try:
            import queue
            pyaudio = _load_pyaudio_module()
        except ImportError:
            detail = "Local audio capture is not installed; use PyAudioWPatch on Windows or PyAudio elsewhere."
            logger.error(detail)
            with self._lock:
                self._capture_error = detail
                self._running = False
                if self._thread is threading.current_thread():
                    self._thread = None
                    self._stop_event = None
            return

        stop_event = stop_event or threading.Event()
        pyaudio_instance: Any = None
        runtime_generation = -1
        retry_delay = 0.25
        last_unavailable_detail = ""
        last_refresh_request_time = 0.0
        native_capture_error = ""

        try:
            if self.capture_loopback and self.sample_rate == 48000 and (helper := find_macos_system_audio_helper()):
                try:
                    self._capture_macos_system_audio(helper, stop_event)
                except Exception as exc:
                    if not stop_event.is_set():
                        logger.warning("Built-in Mac sound capture unavailable; trying a routed loopback input: %s", exc)
                        native_capture_error = str(exc)
                        self._set_capture_error(native_capture_error)
            while not stop_event.is_set():
                if pyaudio_instance is None:
                    try:
                        pyaudio_instance = _PYAUDIO_RUNTIME.acquire(pyaudio, stop_event)
                        if pyaudio_instance is None:
                            break
                        runtime_generation = _PYAUDIO_RUNTIME.generation(pyaudio_instance)
                        self._pyaudio_instance = pyaudio_instance
                    except Exception as exc:
                        detail = str(exc)
                        self._set_capture_error(detail)
                        if detail != last_unavailable_detail:
                            logger.warning("Local audio runtime unavailable; retrying: %s", exc)
                            last_unavailable_detail = detail
                        stop_event.wait(retry_delay)
                        retry_delay = min(2.0, retry_delay * 2)
                        continue

                stream: Any = None
                device_index: Optional[int] = None
                refresh_requested = False
                try:
                    with _PYAUDIO_RUNTIME.serialized():
                        device_index = self._resolve_capture_device_index()
                        if device_index is None:
                            source_name = "speaker-loopback" if self.capture_loopback else "local audio input"
                            raise RuntimeError(f"No usable {source_name} device")
                        stream, capture_rate, capture_channels = self._open_capture_stream(pyaudio, device_index)
                        try:
                            device_name = pyaudio_instance.get_device_info_by_index(device_index).get(
                                "name", device_index
                            )
                        except Exception:
                            device_name = device_index

                    with self._lock:
                        self._stream = stream
                        self._capture_device_name = str(device_name)
                        self._capture_rate = capture_rate
                        self._capture_channels = capture_channels
                        self._capture_error = ""

                    logger.info(
                        "Local audio capture running: device=%s native_rate=%s output_rate=%s "
                        "channels=%s loopback=%s",
                        device_name,
                        capture_rate,
                        self.sample_rate,
                        capture_channels,
                        self.capture_loopback,
                    )
                    retry_delay = 0.25
                    last_unavailable_detail = ""
                    read_frames = max(1, int(capture_rate * 0.02))
                    downmix_np: Any = None
                    if capture_channels == 2 or capture_rate != self.sample_rate:
                        try:
                            import numpy

                            downmix_np = numpy
                        except ImportError:
                            downmix_np = None
                    silent_chunks = 0
                    silence_warning_logged = False
                    while True:
                        try:
                            self._audio_queue.get_nowait()
                        except queue.Empty:
                            break

                    while not stop_event.is_set():
                        try:
                            data = self._read_capture_chunk(
                                stream,
                                read_frames,
                                stop_event,
                                pyaudio_instance,
                                runtime_generation,
                                **({"capture_rate":capture_rate} if self._native_media else {}),
                            )
                        except Exception as exc:
                            if not stop_event.is_set():
                                logger.info("Local audio device changed or became unavailable; reopening: %s", exc)
                                self._set_capture_error(exc)
                                _PYAUDIO_RUNTIME.request_refresh(pyaudio_instance)
                                last_refresh_request_time = time.monotonic()
                                refresh_requested = True
                            break
                        if stop_event.is_set():
                            break
                        if data is None:
                            break
                        captured_at_us = None
                        if self._native_media:
                            data,captured_at_us = data
                        if not data:
                            stop_event.wait(0.02)
                            continue
                        data = self._normalize_capture_chunk(
                            data,
                            capture_rate=capture_rate,
                            capture_channels=capture_channels,
                            numpy_module=downmix_np,
                        )
                        if not silence_warning_logged:
                            if data.count(0) >= len(data) - 4:
                                silent_chunks += 1
                                if silent_chunks >= 250:  # ~5 seconds of pure silence
                                    silence_warning_logged = True
                                    logger.warning(
                                        "Local audio capture from %s has produced only silence; "
                                        "check the OS microphone permission for AutoYou and the selected input device.",
                                        device_name,
                                    )
                            else:
                                silent_chunks = 0
                                silence_warning_logged = True
                        self._queue_audio_chunk(data,captured_at_us=captured_at_us)
                except Exception as exc:
                    detail = (
                        f"ScreenCaptureKit: {native_capture_error}; routed loopback: {exc}"
                        if native_capture_error else str(exc)
                    )
                    self._set_capture_error(detail)
                    if detail != last_unavailable_detail:
                        logger.warning(
                            "%s; serving silence and retrying for hot-plug: %s",
                            "Computer sound unavailable" if self.capture_loopback else "Local audio capture unavailable",
                            exc,
                        )
                        last_unavailable_detail = detail
                    can_refresh_missing_loopback = bool(
                        not self.capture_loopback
                        or callable(getattr(pyaudio_instance, "get_loopback_device_info_generator", None))
                    )
                    now = time.monotonic()
                    if (
                        not stop_event.is_set()
                        and (device_index is not None or can_refresh_missing_loopback)
                        and now - last_refresh_request_time >= 5.0
                    ):
                        _PYAUDIO_RUNTIME.request_refresh(pyaudio_instance)
                        last_refresh_request_time = now
                        refresh_requested = True
                finally:
                    if stream is not None:
                        try:
                            stream_closed = _PYAUDIO_RUNTIME.close_stream(stream)
                        except Exception as exc:
                            stream_closed = False
                            self._set_capture_error(exc)
                        if not stream_closed:
                            detail = "native audio stream cleanup failed; restart AutoYou before capturing audio again"
                            self._set_capture_error(detail)
                            _PYAUDIO_RUNTIME.quarantine(pyaudio_instance, detail)
                            refresh_requested = False
                    with self._lock:
                        if self._stream is stream:
                            self._stream = None

                if refresh_requested or _PYAUDIO_RUNTIME.needs_refresh(
                    pyaudio_instance,
                    runtime_generation,
                ):
                    _PYAUDIO_RUNTIME.release(pyaudio_instance)
                    pyaudio_instance = None
                    runtime_generation = -1
                    self._pyaudio_instance = None

                if not stop_event.is_set():
                    stop_event.wait(retry_delay)
                    retry_delay = min(2.0, retry_delay * 2)
        except Exception as exc:
            self._set_capture_error(exc)
            logger.warning(
                "%s; outbound WebRTC audio track will serve silence: %s",
                "Computer sound unavailable" if self.capture_loopback else "Local audio capture unavailable",
                exc,
            )
        finally:
            with self._lock:
                self._stream = None
            if pyaudio_instance is not None:
                _PYAUDIO_RUNTIME.release(pyaudio_instance)
            self._pyaudio_instance = None
            restart = False
            with self._lock:
                self._running = False
                if self._thread is threading.current_thread():
                    self._thread = None
                    self._stop_event = None
                    restart = self._enabled.is_set()
            if restart:
                self._start_capture()

    def take_native_audio(self) -> Any:
        """Poll genuine capture without stalling other sources in a native mix."""
        if not self._native_media:
            raise RuntimeError("Native capture requires its explicit device owner")
        if getattr(self,"readyState","live") != "live" or not self._enabled.is_set():
            raise MediaStreamError
        from shared.iroh_media_codec import CapturedMedia
        while True:
            try: chunk,captured_at_us = self._audio_queue.get_nowait()
            except queue.Empty: return None
            if time.monotonic_ns()//1000-captured_at_us >= 200000:
                self._native_drops += 1
                continue
            if AudioFrame is None:
                raise RuntimeError("PyAV is required for native audio frames")
            frame = AudioFrame(format="s16",layout="mono",samples=len(chunk)//2)
            frame.planes[0].update(chunk)
            frame.sample_rate,frame.pts,frame.time_base = self.sample_rate,self.pts,self.time_base
            self.pts += frame.samples
            drops = self._native_drops
            discontinuity = drops != self._observed_native_drops
            self._observed_native_drops = drops
            return CapturedMedia(frame,captured_at_us,discontinuity)

    async def capture_native(self) -> Any:
        while True:
            captured = self.take_native_audio()
            if captured is not None: return captured
            await asyncio.sleep(0.005)

    async def recv(self) -> Any:
        if self._native_media:
            return (await self.capture_native()).frame
        while not self._enabled.is_set():
            if getattr(self, "readyState", "live") != "live":
                raise MediaStreamError
            try:
                await asyncio.wait_for(self._enabled.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

        if not self._fallback_start_time:
            self._fallback_start_time = time.time()

        try:
            chunk = self._audio_queue.get_nowait()
        except Exception:
            chunk = bytes(self.frame_size * 2)

        if AudioFrame is None:
            raise RuntimeError("PyAV is required for WebRTC audio frames")

        sample_count = max(1, len(chunk) // 2)
        frame = AudioFrame(format="s16", layout="mono", samples=sample_count)
        frame.planes[0].update(chunk[: sample_count * 2])
        frame.pts = self.pts
        frame.sample_rate = self.sample_rate
        frame.time_base = self.time_base
        self.pts += sample_count

        expected_time = self.pts / self.sample_rate
        sleep_time = expected_time - (time.time() - self._fallback_start_time)
        if sleep_time < -0.2:
            gap_seconds = -sleep_time
            gap_samples = int(gap_seconds * self.sample_rate)
            self.pts += gap_samples
            frame.pts += gap_samples
            expected_time = self.pts / self.sample_rate
            self._fallback_start_time = time.time() - expected_time
            sleep_time = 0

        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
        else:
            await asyncio.sleep(0)

        return frame

    def __del__(self) -> None:
        self._stop_capture()


OutboundVideoTrackFactory = Callable[..., Any]
_OUTBOUND_VIDEO_TRACK_FACTORIES: Dict[str, OutboundVideoTrackFactory] = {}


def register_outbound_video_track_factory(kind: str, factory: OutboundVideoTrackFactory) -> None:
    normalized = str(kind or "").strip().lower()
    if not normalized:
        raise ValueError("Outbound video source kind is required")
    _OUTBOUND_VIDEO_TRACK_FACTORIES[normalized] = factory


def available_outbound_video_source_kinds() -> list[str]:
    return sorted(_OUTBOUND_VIDEO_TRACK_FACTORIES)


def create_outbound_video_track(kind: str, **kwargs: Any) -> Any:
    normalized = str(kind or "").strip().lower()
    factory = _OUTBOUND_VIDEO_TRACK_FACTORIES.get(normalized)
    if factory is None:
        raise ValueError(f"Unknown outbound video source: {kind}")
    return factory(**kwargs)


def publish_realtime_video_jpeg(
    *,
    source_id: str = "default",
    jpeg_bytes: bytes,
    source: str = "api",
) -> RealtimeVideoInputFrame:
    return REALTIME_VIDEO_INPUTS.publish_jpeg(
        source_id=source_id,
        jpeg_bytes=jpeg_bytes,
        source=source,
    )


def configure_video_file_playback(
    *,
    source_id: str = "default",
    file_path: str = "",
    loop: bool = False,
    restart: bool = True,
) -> Dict[str, Any]:
    return VIDEO_FILE_PLAYBACKS.configure(
        source_id=source_id,
        file_path=file_path,
        loop=loop,
        restart=restart,
    )


def play_video_file_playback(*, source_id: str = "default", restart: bool = False) -> Dict[str, Any]:
    return VIDEO_FILE_PLAYBACKS.play(source_id=source_id, restart=restart)


def pause_video_file_playback(*, source_id: str = "default") -> Dict[str, Any]:
    return VIDEO_FILE_PLAYBACKS.pause(source_id=source_id)


def video_file_playback_status(*, source_id: str = "default") -> Dict[str, Any]:
    return VIDEO_FILE_PLAYBACKS.status(source_id)


register_outbound_video_track_factory(
    "api",
    lambda source_id="default", **_: RealtimeVideoInputStreamTrack(source_id=source_id),
)
register_outbound_video_track_factory(
    "realtime_api",
    lambda source_id="default", **_: RealtimeVideoInputStreamTrack(source_id=source_id),
)
register_outbound_video_track_factory(
    "remote_desktop",
    lambda fps=8.0, max_width=1280, monitor_id=0, **_: RemoteDesktopVideoStreamTrack(
        fps=fps,
        max_width=max_width,
        monitor_id=monitor_id,
    ),
)
register_outbound_video_track_factory(
    "video_file",
    lambda source_id="default", registry=VIDEO_FILE_PLAYBACKS, max_width=1280, native_media=False, **_: VideoFileStreamTrack(
        source_id=source_id, registry=registry, max_width=max_width, native_media=native_media),
)
register_outbound_video_track_factory(
    "file",
    lambda source_id="default", registry=VIDEO_FILE_PLAYBACKS, max_width=1280, native_media=False, **_: VideoFileStreamTrack(
        source_id=source_id, registry=registry, max_width=max_width, native_media=native_media),
)
register_outbound_video_track_factory(
    "camera",
    lambda device_index=0, fps=24.0, max_width=1280, native_media=False, **_: CameraVideoStreamTrack(
        device_index=device_index, fps=fps, max_width=max_width, native_media=native_media),
)
register_outbound_video_track_factory(
    "composite",
    lambda sources=None, **kwargs: CompositeVideoStreamTrack(list(sources or []), **kwargs),
)
