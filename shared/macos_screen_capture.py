# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Resilient macOS display enumeration, wake, diagnostics, and screen capture.

On modern macOS (Sonoma / Sequoia / Tahoe+), ``mss`` relies on
``CGGetActiveDisplayList`` and deprecated ``CGWindowListCreateImage``.
When a Mac's display is asleep (common when connecting remotely from a phone),
``CGGetActiveDisplayList`` returns zero displays, causing ``mss`` to collapse
to a ``0x0`` region and stream solid-black frames. On macOS 15+,
``CGWindowListCreateImage`` can also return blank frames unless
``ScreenCaptureKit`` is used.

This module provides:
- Online display discovery via ``CGGetOnlineDisplayList`` + ``AppKit.NSScreen``
- Non-blocking display wake via ``caffeinate -u``
- Honest Screen Recording TCC & sleep diagnostics
- ``ScreenCaptureKit`` (``SCScreenshotManager``) capture with CoreGraphics and
  ``screencapture`` fallbacks
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import logging
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)

_MIN_DIMENSION = 10
_BLANK_MAX_MEAN = 2.0
_AUTO_WAKE_COOLDOWN_SECONDS = 8.0
_SC_PERMISSION_DENIED_COOLDOWN_SECONDS = 5.0

_WAKE_LOCK = threading.Lock()
_LAST_AUTO_WAKE_AT = 0.0
_LAST_SC_PERMISSION_DENIED_AT = 0.0


def is_macos() -> bool:
    return platform.system().lower() == "darwin"


def _is_probably_blank(image: Optional[Image.Image]) -> bool:
    if image is None or not hasattr(image, "width") or not hasattr(image, "height"):
        return True
    try:
        if int(image.width) < _MIN_DIMENSION or int(image.height) < _MIN_DIMENSION:
            return True
        sample = image.convert("RGB")
        sample.thumbnail((64, 64), Image.Resampling.BILINEAR)
        mean = sample.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        return max(float(channel) for channel in mean) <= _BLANK_MAX_MEAN
    except Exception:
        return False


def _screen_names_by_display_id() -> Dict[int, Tuple[str, float]]:
    names: Dict[int, Tuple[str, float]] = {}
    if not is_macos():
        return names
    try:
        import AppKit  # type: ignore[import-not-found]

        for screen in AppKit.NSScreen.screens() or []:
            try:
                desc = screen.deviceDescription() or {}
                screen_num = desc.get("NSScreenNumber")
                if screen_num is None:
                    continue
                display_id = int(screen_num)
                localized = ""
                if hasattr(screen, "localizedName"):
                    localized = str(screen.localizedName() or "").strip()
                scale = float(screen.backingScaleFactor() or 1.0)
                names[display_id] = (localized, scale)
            except Exception:
                continue
    except Exception as exc:
        logger.debug("NSScreen name lookup unavailable: %s", exc)
    return names


def list_macos_monitors() -> List[Dict[str, Any]]:
    """Return AutoYou monitor list (index 0 = virtual union, 1..N = displays).

    Uses ``CGGetOnlineDisplayList`` so displays remain discoverable even while
    asleep, falling back to ``CGMainDisplayID`` and ``NSScreen``.
    """
    if not is_macos():
        return []

    try:
        import Quartz  # type: ignore[import-not-found]
    except Exception as exc:
        logger.debug("Quartz unavailable for macOS display listing: %s", exc)
        return []

    screen_meta = _screen_names_by_display_id()
    display_ids: List[int] = []
    try:
        err, online, count = Quartz.CGGetOnlineDisplayList(32, None, None)
        if err == 0 and online and count > 0:
            display_ids = [int(d) for d in online[: int(count)]]
    except Exception as exc:
        logger.debug("CGGetOnlineDisplayList failed: %s", exc)

    if not display_ids:
        try:
            err, active, count = Quartz.CGGetActiveDisplayList(32, None, None)
            if err == 0 and active and count > 0:
                display_ids = [int(d) for d in active[: int(count)]]
        except Exception:
            pass

    if not display_ids:
        try:
            main_id = int(Quartz.CGMainDisplayID())
            if main_id:
                display_ids = [main_id]
        except Exception:
            pass

    if not display_ids:
        return []

    per_display: List[Dict[str, Any]] = []
    min_x: Optional[int] = None
    min_y: Optional[int] = None
    max_x: Optional[int] = None
    max_y: Optional[int] = None

    for idx, display_id in enumerate(display_ids, start=1):
        try:
            rect = Quartz.CGDisplayBounds(display_id)
            left = int(round(float(rect.origin.x)))
            top = int(round(float(rect.origin.y)))
            width = int(round(float(rect.size.width)))
            height = int(round(float(rect.size.height)))
        except Exception:
            continue

        if width < _MIN_DIMENSION or height < _MIN_DIMENSION:
            try:
                pw = int(Quartz.CGDisplayPixelsWide(display_id) or 0)
                ph = int(Quartz.CGDisplayPixelsHigh(display_id) or 0)
                if pw >= _MIN_DIMENSION and ph >= _MIN_DIMENSION:
                    width, height = pw, ph
            except Exception:
                pass
        if width < _MIN_DIMENSION or height < _MIN_DIMENSION:
            continue

        is_asleep = False
        is_active = True
        is_main = idx == 1
        try:
            is_asleep = bool(Quartz.CGDisplayIsAsleep(display_id))
        except Exception:
            pass
        try:
            is_active = bool(Quartz.CGDisplayIsActive(display_id))
        except Exception:
            pass
        try:
            is_main = bool(Quartz.CGDisplayIsMain(display_id))
        except Exception:
            pass

        localized, scale = screen_meta.get(display_id, ("", 1.0))
        label = localized or f"Monitor {idx}"
        suffix = " · Asleep" if is_asleep else ""
        per_display.append({
            "id": idx,
            "display_id": display_id,
            "name": f"{label} ({width}x{height}){suffix}",
            "label": label,
            "width": width,
            "height": height,
            "left": left,
            "top": top,
            "scale": scale,
            "is_asleep": is_asleep,
            "is_active": is_active,
            "is_main": is_main,
        })

        min_x = left if min_x is None else min(min_x, left)
        min_y = top if min_y is None else min(min_y, top)
        max_x = left + width if max_x is None else max(max_x, left + width)
        max_y = top + height if max_y is None else max(max_y, top + height)

    if not per_display or min_x is None or min_y is None or max_x is None or max_y is None:
        return []

    union_w = max(0, max_x - min_x)
    union_h = max(0, max_y - min_y)
    any_asleep = any(d["is_asleep"] for d in per_display)
    all_asleep = all(d["is_asleep"] for d in per_display)
    virtual_suffix = " · Asleep" if all_asleep else ""
    virtual = {
        "id": 0,
        "display_id": per_display[0]["display_id"],
        "name": f"All Displays ({union_w}x{union_h}){virtual_suffix}",
        "label": "All Displays",
        "width": union_w,
        "height": union_h,
        "left": min_x,
        "top": min_y,
        "scale": per_display[0]["scale"],
        "is_asleep": all_asleep,
        "any_asleep": any_asleep,
        "is_active": any(d["is_active"] for d in per_display),
        "is_main": True,
    }
    return [virtual, *per_display]


def has_screen_recording_permission() -> Optional[bool]:
    if not is_macos():
        return None
    try:
        import Quartz  # type: ignore[import-not-found]

        if hasattr(Quartz, "CGPreflightScreenCaptureAccess"):
            return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception as exc:
        logger.debug("CGPreflightScreenCaptureAccess unavailable: %s", exc)
    return None


def screencapturekit_available() -> bool:
    if not is_macos():
        return False
    try:
        import ScreenCaptureKit as SCK  # type: ignore[import-not-found]

        return hasattr(SCK, "SCShareableContent") and hasattr(SCK, "SCScreenshotManager")
    except Exception:
        return False


def get_macos_capture_diagnostics() -> Dict[str, Any]:
    """Return structured diagnostics explaining macOS display & TCC capture state."""
    if not is_macos():
        return {"platform": platform.system().lower(), "ok": True, "reason": "ok", "message": ""}

    monitors = list_macos_monitors()
    displays = monitors[1:] if len(monitors) > 1 else []
    online_count = len(displays)
    active_count = sum(1 for d in displays if d.get("is_active"))
    asleep_count = sum(1 for d in displays if d.get("is_asleep"))
    all_asleep = online_count > 0 and asleep_count == online_count
    any_asleep = asleep_count > 0
    permission = has_screen_recording_permission()
    sck_ready = screencapturekit_available()

    reason = "ok"
    message = ""
    if permission is False:
        reason = "screen_recording_permission"
        message = (
            "macOS Screen Recording permission is not granted for AutoYou / Python. "
            "Open System Settings → Privacy & Security → Screen & System Audio Recording, "
            "enable AutoYou (or Python), then restart AutoYou."
        )
    elif online_count == 0:
        reason = "no_displays"
        message = "No macOS displays were detected. Connect a display or virtual display to stream."
    elif all_asleep:
        reason = "display_asleep"
        message = (
            "Your Mac display is asleep, so macOS paused screen compositing. "
            "Tap Wake Display to turn on the screen and resume streaming."
        )

    return {
        "platform": "macos",
        "ok": reason == "ok",
        "reason": reason,
        "message": message,
        "screen_recording_granted": permission,
        "permission_required": permission is False,
        "online_display_count": online_count,
        "active_display_count": active_count,
        "any_display_asleep": any_asleep,
        "all_displays_asleep": all_asleep,
        "can_wake": all_asleep or any_asleep,
        "screencapturekit_available": sck_ready,
    }


def wake_macos_display(*, force: bool = False) -> Dict[str, Any]:
    """Declare user display activity on macOS to wake a sleeping monitor."""
    global _LAST_AUTO_WAKE_AT
    if not is_macos():
        return {"success": False, "woke": False, "message": "Display wake is only supported on macOS."}

    now = time.monotonic()
    with _WAKE_LOCK:
        if not force and (now - _LAST_AUTO_WAKE_AT) < _AUTO_WAKE_COOLDOWN_SECONDS:
            return {"success": True, "woke": False, "throttled": True, "message": "Wake already attempted recently."}
        _LAST_AUTO_WAKE_AT = now

    caffeinate = shutil.which("caffeinate") or "/usr/bin/caffeinate"
    if not os.path.isfile(caffeinate):
        return {"success": False, "woke": False, "message": "macOS caffeinate utility is unavailable."}

    try:
        proc = subprocess.run(
            [caffeinate, "-u", "-t", "2"],
            capture_output=True,
            text=True,
            timeout=4.0,
            check=False,
        )
        # Brief settle so WindowServer brings displays online before next capture.
        time.sleep(0.25)
        diagnostics = get_macos_capture_diagnostics()
        return {
            "success": proc.returncode == 0,
            "woke": proc.returncode == 0,
            "diagnostics": diagnostics,
            "message": (
                "Display wake signal sent."
                if diagnostics.get("ok")
                else (diagnostics.get("message") or "Display wake signal sent.")
            ),
        }
    except Exception as exc:
        logger.debug("macOS caffeinate wake failed: %s", exc)
        return {"success": False, "woke": False, "message": f"Failed to wake display: {exc}"}


def _cgimage_to_pil(cg_image: Any) -> Optional[Image.Image]:
    if cg_image is None:
        return None
    try:
        import Quartz  # type: ignore[import-not-found]

        width = int(Quartz.CGImageGetWidth(cg_image))
        height = int(Quartz.CGImageGetHeight(cg_image))
        if width < _MIN_DIMENSION or height < _MIN_DIMENSION:
            return None
        bytes_per_row = int(Quartz.CGImageGetBytesPerRow(cg_image))
        bits_per_pixel = int(Quartz.CGImageGetBitsPerPixel(cg_image))
        provider = Quartz.CGImageGetDataProvider(cg_image)
        if provider is None:
            return None
        cf_data = Quartz.CGDataProviderCopyData(provider)
        if cf_data is None:
            return None
        raw = bytes(cf_data)
        if len(raw) < bytes_per_row * height:
            return None

        bitmap_info = int(Quartz.CGImageGetBitmapInfo(cg_image))
        byte_order = bitmap_info & int(getattr(Quartz, "kCGBitmapByteOrderMask", 0x7000))
        alpha_info = bitmap_info & int(getattr(Quartz, "kCGBitmapAlphaInfoMask", 0x1F))
        little_32 = int(getattr(Quartz, "kCGBitmapByteOrder32Little", 0x2000))

        if bits_per_pixel == 32:
            if byte_order == little_32:
                raw_mode = "BGRX" if alpha_info in (5, 6) else "BGRA"
            else:
                raw_mode = "RGBX" if alpha_info in (5, 6) else "RGBA"
            return Image.frombytes("RGBA", (width, height), raw, "raw", raw_mode, bytes_per_row, 1).convert("RGB")
        if bits_per_pixel == 24:
            return Image.frombytes("RGB", (width, height), raw, "raw", "RGB", bytes_per_row, 1)
    except Exception as exc:
        logger.debug("CGImage conversion failed: %s", exc)
    return None


def _crop_image_to_bounds(
    image: Image.Image,
    bounds: Optional[Dict[str, int]],
    display_bounds: Optional[Dict[str, int]],
) -> Image.Image:
    if not bounds or not display_bounds:
        return image
    disp_w = int(display_bounds.get("width") or 0)
    disp_h = int(display_bounds.get("height") or 0)
    if disp_w <= 0 or disp_h <= 0:
        return image

    req_l = int(bounds.get("left") or 0)
    req_t = int(bounds.get("top") or 0)
    req_w = int(bounds.get("width") or 0)
    req_h = int(bounds.get("height") or 0)
    disp_l = int(display_bounds.get("left") or 0)
    disp_t = int(display_bounds.get("top") or 0)

    if req_l == disp_l and req_t == disp_t and req_w == disp_w and req_h == disp_h:
        return image

    scale_x = float(image.width) / float(disp_w)
    scale_y = float(image.height) / float(disp_h)
    x0 = max(0, min(image.width, int(round((req_l - disp_l) * scale_x))))
    y0 = max(0, min(image.height, int(round((req_t - disp_t) * scale_y))))
    x1 = max(0, min(image.width, int(round((req_l + req_w - disp_l) * scale_x))))
    y1 = max(0, min(image.height, int(round((req_t + req_h - disp_t) * scale_y))))
    if x1 - x0 < _MIN_DIMENSION or y1 - y0 < _MIN_DIMENSION:
        return image
    if x0 == 0 and y0 == 0 and x1 == image.width and y1 == image.height:
        return image
    return image.crop((x0, y0, x1, y1))


def capture_screencapturekit_image(
    bounds: Optional[Dict[str, int]] = None,
    *,
    display_id: Optional[int] = None,
    scale: float = 1.0,
    timeout_seconds: float = 2.5,
) -> Optional[Image.Image]:
    """Capture a macOS display frame via ScreenCaptureKit SCScreenshotManager."""
    global _LAST_SC_PERMISSION_DENIED_AT
    if not is_macos():
        return None
    if (time.monotonic() - _LAST_SC_PERMISSION_DENIED_AT) < _SC_PERMISSION_DENIED_COOLDOWN_SECONDS:
        return None
    if has_screen_recording_permission() is False:
        _LAST_SC_PERMISSION_DENIED_AT = time.monotonic()
        return None

    try:
        import ScreenCaptureKit as SCK  # type: ignore[import-not-found]
    except Exception:
        return None

    content_event = threading.Event()
    content_result: Dict[str, Any] = {}

    def _on_content(content: Any, error: Any) -> None:
        content_result["content"] = content
        content_result["error"] = error
        content_event.set()

    try:
        SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            False,
            True,
            _on_content,
        )
    except Exception as exc:
        logger.debug("SCShareableContent request failed: %s", exc)
        return None

    if not content_event.wait(timeout_seconds):
        logger.debug("SCShareableContent timed out.")
        return None

    err = content_result.get("error")
    if err is not None:
        err_text = str(err)
        if "-3801" in err_text or "declined TCC" in err_text:
            _LAST_SC_PERMISSION_DENIED_AT = time.monotonic()
        logger.debug("SCShareableContent error: %s", err_text)
        return None

    content = content_result.get("content")
    displays = list(content.displays() or []) if content is not None else []
    if not displays:
        return None

    chosen = displays[0]
    if display_id is not None:
        for candidate in displays:
            try:
                if int(candidate.displayID()) == int(display_id):
                    chosen = candidate
                    break
            except Exception:
                continue
    elif bounds:
        cx = int(bounds.get("left") or 0) + int(bounds.get("width") or 0) // 2
        cy = int(bounds.get("top") or 0) + int(bounds.get("height") or 0) // 2
        for candidate in displays:
            try:
                frame = candidate.frame()
                if (
                    frame.origin.x <= cx <= frame.origin.x + frame.size.width
                    and frame.origin.y <= cy <= frame.origin.y + frame.size.height
                ):
                    chosen = candidate
                    break
            except Exception:
                continue

    try:
        disp_frame = chosen.frame()
        disp_bounds = {
            "left": int(round(float(disp_frame.origin.x))),
            "top": int(round(float(disp_frame.origin.y))),
            "width": max(_MIN_DIMENSION, int(round(float(disp_frame.size.width)))),
            "height": max(_MIN_DIMENSION, int(round(float(disp_frame.size.height)))),
        }
        norm_scale = max(0.15, min(1.0, float(scale or 1.0)))
        out_w = max(_MIN_DIMENSION, int(round(float(chosen.width()) * norm_scale)))
        out_h = max(_MIN_DIMENSION, int(round(float(chosen.height()) * norm_scale)))

        content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(chosen, [])
        cfg = SCK.SCStreamConfiguration.alloc().init()
        cfg.setWidth_(out_w)
        cfg.setHeight_(out_h)
        if hasattr(cfg, "setShowsCursor_"):
            cfg.setShowsCursor_(True)
    except Exception as exc:
        logger.debug("ScreenCaptureKit configuration failed: %s", exc)
        return None

    shot_event = threading.Event()
    shot_result: Dict[str, Any] = {}

    def _on_shot(cg_image: Any, error: Any) -> None:
        shot_result["image"] = cg_image
        shot_result["error"] = error
        shot_event.set()

    try:
        SCK.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
            content_filter,
            cfg,
            _on_shot,
        )
    except Exception as exc:
        logger.debug("SCScreenshotManager capture failed: %s", exc)
        return None

    if not shot_event.wait(timeout_seconds):
        logger.debug("SCScreenshotManager timed out.")
        return None

    if shot_result.get("error") is not None:
        logger.debug("SCScreenshotManager error: %s", shot_result.get("error"))
        return None

    pil_img = _cgimage_to_pil(shot_result.get("image"))
    if pil_img is None:
        return None
    return _crop_image_to_bounds(pil_img, bounds, disp_bounds)


def capture_coregraphics_image(bounds: Optional[Dict[str, int]] = None) -> Optional[Image.Image]:
    """Capture a macOS region directly via Quartz.CGWindowListCreateImage."""
    if not is_macos():
        return None
    try:
        import Quartz  # type: ignore[import-not-found]

        if bounds and int(bounds.get("width") or 0) >= _MIN_DIMENSION and int(bounds.get("height") or 0) >= _MIN_DIMENSION:
            rect = Quartz.CGRectMake(
                float(bounds.get("left") or 0),
                float(bounds.get("top") or 0),
                float(bounds["width"]),
                float(bounds["height"]),
            )
        else:
            rect = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())

        cg_image = Quartz.CGWindowListCreateImage(
            rect,
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageDefault,
        )
        return _cgimage_to_pil(cg_image)
    except Exception as exc:
        logger.debug("CoreGraphics capture failed: %s", exc)
        return None


def capture_screencapture_cli_image(bounds: Optional[Dict[str, int]] = None) -> Optional[Image.Image]:
    """Fallback capture via macOS /usr/sbin/screencapture -x -t jpg."""
    if not is_macos():
        return None
    binary = shutil.which("screencapture") or "/usr/sbin/screencapture"
    if not os.path.isfile(binary):
        return None

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="autoyou-sc-", suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        cmd = [binary, "-x", "-C", "-t", "jpg"]
        if bounds and int(bounds.get("width") or 0) >= _MIN_DIMENSION and int(bounds.get("height") or 0) >= _MIN_DIMENSION:
            cmd.extend([
                "-R",
                f"{int(bounds.get('left') or 0)},{int(bounds.get('top') or 0)},{int(bounds['width'])},{int(bounds['height'])}",
            ])
        cmd.append(tmp_path)
        proc = subprocess.run(cmd, capture_output=True, timeout=3.0, check=False)
        if proc.returncode != 0 or not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) <= 0:
            return None
        with Image.open(tmp_path) as img:
            return img.convert("RGB")
    except Exception as exc:
        logger.debug("screencapture CLI fallback failed: %s", exc)
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def capture_macos_image(
    bounds: Optional[Dict[str, int]] = None,
    *,
    display_id: Optional[int] = None,
    scale: float = 1.0,
    auto_wake: bool = True,
) -> Optional[Image.Image]:
    """Capture a non-blank macOS desktop image across ScreenCaptureKit, Quartz, and CLI."""
    if not is_macos():
        return None

    if auto_wake:
        monitors = list_macos_monitors()
        if monitors and monitors[0].get("is_asleep"):
            wake_macos_display(force=False)

    # 1. ScreenCaptureKit (native modern macOS 14/15/26/27 GPU capture)
    sck_image = capture_screencapturekit_image(bounds, display_id=display_id, scale=scale)
    if sck_image is not None and not _is_probably_blank(sck_image):
        return sck_image

    # 2. CoreGraphics direct region capture
    cg_image = capture_coregraphics_image(bounds)
    if cg_image is not None and not _is_probably_blank(cg_image):
        return cg_image

    # 3. /usr/sbin/screencapture CLI fallback
    cli_image = capture_screencapture_cli_image(bounds)
    if cli_image is not None and not _is_probably_blank(cli_image):
        return cli_image

    return None
