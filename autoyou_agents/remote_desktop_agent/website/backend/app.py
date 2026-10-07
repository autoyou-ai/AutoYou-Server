# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-9da7f362fb165b7f369cfc5f

"""Remote Desktop Agent UI backend.

Serves the remote desktop interface, allowing real-time screen streaming
over binary WebSockets and interactive mouse/keyboard control.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import logging
import os
import platform
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Request, WebSocket, WebSocketDisconnect
from PIL import Image

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-9da7f362fb165b7f369cfc5f"


try:  # pragma: no cover - availability varies by Pillow/runtime build.
    from PIL import ImageGrab
except Exception:  # pragma: no cover
    ImageGrab = None  # type: ignore[assignment]

from shared.video_call_manager import VIDEO_FRAME_REGISTRY
from shared.remote_desktop_keyboard import KEY_MAP as REMOTE_DESKTOP_KEY_MAP
from shared.iroh_website_input import NativeWebsiteView, website_view, in_view
from shared.iroh_media import _join_owned
from shared.session_transport import SessionDenied, TransportKind
from shared.runtime_module_loader import import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module

_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"
_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
create_agent_chat_app = _smc.create_agent_chat_app
_describe_chat_auth_state = _smc._describe_chat_auth_state
_agent_chat_session_is_valid = _smc._agent_chat_session_is_valid
_json_response = _smc._json_response
_runtime_server = _smc._runtime_server

from autoyou_agents.remote_desktop_agent.agent import (
    _lazy_import_mss,
    _lazy_import_pyautogui,
    activate_window,
    get_window_bounds,
    list_active_monitors,
    list_running_windows,
)

logger = logging.getLogger(__name__)

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_AGENT_NAME = "remote_desktop_agent"
_SCREEN_WS_AUTH_TIMEOUT_SECONDS = 5.0
_SCREEN_WS_POLICY_VIOLATION = 1008
_DEFAULT_STREAM_SCALE = 0.35
_DEFAULT_STREAM_QUALITY = 35
_DEFAULT_STREAM_FPS = 4
_MAX_STREAM_FPS = 12
_DEFAULT_MOBILE_VIDEO_FPS = 12
_MAX_MOBILE_VIDEO_FPS = 24
_MIN_CAPTURE_DIMENSION = 10
_BLANK_CAPTURE_MAX_MEAN = 2.0
_IMAGEGRAB_FALLBACK_NOTICE_LOGGED = False


# ─── Key Mapping for PyAutoGUI ───────────────────────────────────────────────

KEY_MAP = REMOTE_DESKTOP_KEY_MAP


def _control_backend():
    pyautogui = _lazy_import_pyautogui()
    # Remote control intentionally maps to screen edges, so the PyAutoGUI
    # corner failsafe would make legitimate client input crash.
    pyautogui.FAILSAFE = False
    return pyautogui


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _current_platform_tag() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system.startswith("win"):
        return "windows"
    return system or "unknown"


def _imagegrab_capture_supported() -> bool:
    return ImageGrab is not None and _current_platform_tag() in {"windows", "macos"}


def _normalize_capture_bounds(bounds: Any) -> Optional[Dict[str, int]]:
    if not isinstance(bounds, dict):
        return None
    try:
        return {
            "left": int(bounds.get("left") or 0),
            "top": int(bounds.get("top") or 0),
            "width": max(_MIN_CAPTURE_DIMENSION, int(bounds.get("width") or 0)),
            "height": max(_MIN_CAPTURE_DIMENSION, int(bounds.get("height") or 0)),
        }
    except Exception:
        return None


def _is_probably_blank_capture(image: Any) -> bool:
    if image is None or not hasattr(image, "width") or not hasattr(image, "height"):
        return False
    try:
        if int(image.width) <= 0 or int(image.height) <= 0:
            return False
        sample = image.convert("RGB")
        sample.thumbnail((64, 64), Image.Resampling.BILINEAR)
        mean = sample.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        return max(float(channel) for channel in mean) <= _BLANK_CAPTURE_MAX_MEAN
    except Exception:
        return False


def _get_mss_virtual_bounds_sync() -> Optional[Dict[str, int]]:
    try:
        mss = _lazy_import_mss()
        with mss.mss() as sct:
            if getattr(sct, "monitors", None):
                return _normalize_capture_bounds(sct.monitors[0])
    except Exception:
        return None
    return None


def _get_imagegrab_bounds_sync(target_id: Any = 0) -> Optional[Dict[str, int]]:
    if not _imagegrab_capture_supported():
        return None
    try:
        int(target_id)
    except Exception:
        return None
    try:
        kwargs = {"all_screens": True} if _current_platform_tag() == "windows" else {}
        image = ImageGrab.grab(**kwargs)  # type: ignore[union-attr]
        return {
            "left": 0,
            "top": 0,
            "width": max(_MIN_CAPTURE_DIMENSION, int(image.width)),
            "height": max(_MIN_CAPTURE_DIMENSION, int(image.height)),
        }
    except Exception as exc:
        logger.debug("ImageGrab monitor bounds unavailable: %s", exc)
        return None


def _get_monitor_bounds_sync(target_id: Any) -> Optional[Dict[str, int]]:
    try:
        normalized_target_id = int(target_id)
    except Exception:
        normalized_target_id = 0
    try:
        mss = _lazy_import_mss()
        with mss.mss() as sct:
            if 0 <= normalized_target_id < len(sct.monitors):
                bounds = _normalize_capture_bounds(sct.monitors[normalized_target_id])
                if bounds is not None:
                    return bounds
    except Exception as exc:
        logger.debug("MSS monitor bounds unavailable: %s", exc)
    return _get_imagegrab_bounds_sync(normalized_target_id)


def _capture_mss_image_sync(bounds: Dict[str, int]) -> Image.Image:
    mss = _lazy_import_mss()
    with mss.mss() as sct:
        sct_img = sct.grab(bounds)
    return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")


def _crop_imagegrab_to_bounds(
    image: Image.Image,
    bounds: Optional[Dict[str, int]],
    virtual_bounds: Optional[Dict[str, int]],
) -> Image.Image:
    if image.mode != "RGB":
        image = image.convert("RGB")
    normalized_bounds = _normalize_capture_bounds(bounds)
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
    if clamped_right - clamped_left < _MIN_CAPTURE_DIMENSION or clamped_bottom - clamped_top < _MIN_CAPTURE_DIMENSION:
        return image
    if (
        clamped_left == 0
        and clamped_top == 0
        and clamped_right == image.width
        and clamped_bottom == image.height
    ):
        return image
    return image.crop((clamped_left, clamped_top, clamped_right, clamped_bottom))


def _capture_imagegrab_image_sync(bounds: Optional[Dict[str, int]]) -> Optional[Image.Image]:
    if not _imagegrab_capture_supported():
        return None
    try:
        kwargs = {"all_screens": True} if _current_platform_tag() == "windows" else {}
        image = ImageGrab.grab(**kwargs)  # type: ignore[union-attr]
        return _crop_imagegrab_to_bounds(image, bounds, _get_mss_virtual_bounds_sync())
    except Exception as exc:
        logger.debug("ImageGrab desktop capture unavailable: %s", exc)
        return None


def _log_imagegrab_fallback_once(reason: str) -> None:
    global _IMAGEGRAB_FALLBACK_NOTICE_LOGGED
    if _IMAGEGRAB_FALLBACK_NOTICE_LOGGED:
        return
    _IMAGEGRAB_FALLBACK_NOTICE_LOGGED = True
    logger.info("Remote Desktop stream using ImageGrab fallback after %s.", reason)


def _capture_desktop_image_sync(bounds: Dict[str, int]) -> Optional[Image.Image]:
    normalized_bounds = _normalize_capture_bounds(bounds)
    if normalized_bounds is None:
        return None

    mss_image: Optional[Image.Image] = None
    try:
        mss_image = _capture_mss_image_sync(normalized_bounds)
        if not _is_probably_blank_capture(mss_image):
            return mss_image
    except Exception as exc:
        logger.debug("MSS desktop capture unavailable: %s", exc)

    fallback_image = _capture_imagegrab_image_sync(normalized_bounds)
    if fallback_image is not None and not _is_probably_blank_capture(fallback_image):
        _log_imagegrab_fallback_once("blank or unavailable MSS capture")
        return fallback_image
    return mss_image


def _capture_and_encode_sync(bounds: Dict[str, int], scale: Any, quality: Any) -> Optional[bytes]:
    try:
        img = _capture_desktop_image_sync(bounds)
        if img is None:
            return None
        normalized_scale = max(0.1, min(1.0, float(scale or _DEFAULT_STREAM_SCALE)))
        normalized_quality = max(5, min(100, int(quality or _DEFAULT_STREAM_QUALITY)))
        if normalized_scale < 1.0:
            img = img.resize(
                (max(1, int(img.width * normalized_scale)), max(1, int(img.height * normalized_scale))),
                Image.Resampling.BILINEAR,
            )
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=normalized_quality)
        return buf.getvalue()
    except Exception as e:
        logger.error("Capture or stream error in thread: %s", e)
        return None


def _simulate_input(payload: Dict[str, Any]) -> bool:
    """Executes a single cursor or keyboard event on the host system."""
    barrier = None
    try:
        from shared.iroh_input import LegacyInputCleanupBarrier
        barrier = LegacyInputCleanupBarrier(payload, admit=True)
        event_type = payload.get("type")
        pyautogui = _control_backend()
        
        # 1. Coordinate Mapping (if present)
        # Frontend transmits ratio (0.0 to 1.0) and display bounds
        x_ratio = payload.get("x")
        y_ratio = payload.get("y")
        target_type = payload.get("target_type", "monitor")
        target_id = payload.get("target_id", 0)
        monitor_target_id = int(target_id) if target_type == "monitor" else 0
        
        abs_x, abs_y = None, None
        
        if x_ratio is not None and y_ratio is not None:
            # Map based on monitor or window
            if target_type == "monitor":
                mon = _get_monitor_bounds_sync(monitor_target_id)
                if mon:
                    abs_x = int(mon["left"] + mon["width"] * x_ratio)
                    abs_y = int(mon["top"] + mon["height"] * y_ratio)
            elif target_type == "window":
                bounds = get_window_bounds(target_id)
                if bounds:
                    abs_x = int(bounds["left"] + bounds["width"] * x_ratio)
                    abs_y = int(bounds["top"] + bounds["height"] * y_ratio)
            
            # Final fallback to standard primary monitor
            if abs_x is None or abs_y is None:
                mon = _get_monitor_bounds_sync(0)
                if mon:
                    abs_x = int(mon["left"] + mon["width"] * x_ratio)
                    abs_y = int(mon["top"] + mon["height"] * y_ratio)

        # 2. PyAutoGUI Execution
        if event_type == "mousemove" and abs_x is not None and abs_y is not None:
            pyautogui.moveTo(abs_x, abs_y)
            return True
            
        elif event_type in ("mousedown", "mouseup", "click", "doubleclick"):
            if abs_x is not None and abs_y is not None:
                pyautogui.moveTo(abs_x, abs_y)
                
            btn = payload.get("button", "left")
            if btn not in ("left", "right", "middle"):
                btn = "left"
                
            if event_type == "mousedown":
                pyautogui.mouseDown(button=btn)
            elif event_type == "mouseup":
                pyautogui.mouseUp(button=btn)
            elif event_type == "click":
                pyautogui.click(button=btn)
            elif event_type == "doubleclick":
                pyautogui.doubleClick(button=btn)
            return True
            
        elif event_type == "scroll":
            dy = int(payload.get("dy", 0))
            if dy != 0:
                # scroll dy counts (positive up, negative down)
                # PyAutoGUI handles positive up, negative down on Windows/Mac
                pyautogui.scroll(dy)
            return True
            
        elif event_type in ("keydown", "keyup", "keypress"):
            key = str(payload.get("key", "")).lower()
            if not key:
                return False
                
            # Resolve special key mappings
            mapped_key = KEY_MAP.get(key, key)
            
            # Map function keys F1-F12
            if len(key) >= 2 and key[0] == "f" and key[1:].isdigit():
                mapped_key = key
                
            if event_type == "keydown":
                pyautogui.keyDown(mapped_key)
            elif event_type == "keyup":
                pyautogui.keyUp(mapped_key)
            elif event_type == "keypress":
                pyautogui.press(mapped_key)
            return True
            
        return False
    except Exception as e:
        logger.error("Error executing remote input: %s", e)
        return False
    finally:
        if barrier is not None: barrier.release()


async def _authenticate_screen_websocket(websocket: WebSocket, agent_name: str) -> Dict[str, Any]:
    """Authenticate a screen stream WebSocket without URL-carried tokens.

    Browsers cannot attach custom Authorization headers to WebSockets. For the
    tunnel/localStorage-token case, the client must send a first JSON message:
    {"type": "auth", "token": "..."} before any stream configuration. Cookie
    and admin-session auth still work without this message.
    """
    auth = _describe_chat_auth_state(websocket, agent_name)
    if auth.get("authenticated"):
        return auth

    try:
        payload = await asyncio.wait_for(
            websocket.receive_json(),
            timeout=_SCREEN_WS_AUTH_TIMEOUT_SECONDS,
        )
    except WebSocketDisconnect:
        raise
    except asyncio.TimeoutError:
        await websocket.close(code=_SCREEN_WS_POLICY_VIOLATION)
        return auth
    except Exception:
        await websocket.close(code=_SCREEN_WS_POLICY_VIOLATION)
        return auth

    if not isinstance(payload, dict) or payload.get("type") != "auth":
        await websocket.close(code=_SCREEN_WS_POLICY_VIOLATION)
        return auth

    token = str(payload.get("token") or "").strip()
    if token and _agent_chat_session_is_valid(agent_name, token):
        return {**auth, "authenticated": True, "via": "chat_session"}

    await websocket.close(code=_SCREEN_WS_POLICY_VIOLATION)
    return auth


def _request_header(request: Request, name: str) -> str:
    return str(request.headers.get(name) or "").strip()


def _request_query_param(request: Request, name: str) -> str:
    try:
        return str(request.query_params.get(name) or "").strip()
    except Exception:
        return ""


def _coalesce_session_identity(request: Request) -> tuple[str, str]:
    if _request_header(request, "X-AutoYou-Transport") == "iroh":
        return (_request_header(request, "X-AutoYou-WebRTC-Session-Id"),
                _request_header(request, "X-AutoYou-WebRTC-Owner-Key"))
    session_id = _request_query_param(request, "session_id")
    owner_key = _request_query_param(request, "owner_key")
    if not session_id:
        session_id = _request_header(request, "X-AutoYou-WebRTC-Session-Id")
    if not owner_key:
        owner_key = _request_header(request, "X-AutoYou-WebRTC-Owner-Key")
    return session_id, owner_key


def _native_channel(request):
    session_id = _request_header(request, "X-AutoYou-WebRTC-Session-Id")
    marker = _request_header(request, "X-AutoYou-Transport")
    if not session_id and not marker: return None
    engine = getattr(_runtime_server(), "WEBRTC", None)
    channel = getattr(engine, "datachannel_managers", {}).get(session_id)
    if getattr(channel, "transport_kind", None) != TransportKind.IROH:
        if marker: raise SessionDenied("native website session is unavailable")
        return None
    binding = channel.binding
    if marker != "iroh" or not channel.is_ready or binding.owner_key != _request_header(request, "X-AutoYou-WebRTC-Owner-Key"):
        raise SessionDenied("native website owner changed")
    channel.registry.check(binding, scope="browser")
    return channel


def _native_view(channel, payload):
    if not isinstance(payload,dict): raise SessionDenied("invalid website view identity")
    owner = website_view(channel.binding, payload.get("view_id"))
    return owner


async def _website_keyboard(owner, action, grant):
    from shared.datachannel_manager import create_voice_call_control_message
    async def send():
        owner._check()
        owner.channel.registry.check(owner.binding, scope="control")
        message = create_voice_call_control_message({
            "event": "remote_desktop_keyboard", "action": action, "source": "remote_desktop_agent",
            "native_website": {"version": 1, "view_id": owner.source.call_id}, **grant,
        }, session_id=owner.binding.transport_id)
        if not await owner.channel.send_message(message): raise SessionDenied("native keyboard transport is full")
    loop = getattr(owner.channel, "application_loop", None)
    if loop is None or loop.is_closed() or not loop.is_running(): raise SessionDenied("native application loop is unavailable")
    if loop is asyncio.get_running_loop(): return await send()
    posted = asyncio.run_coroutine_threadsafe(send(), loop)
    async def joined(): return await asyncio.wrap_future(posted)
    await _join_owned(asyncio.create_task(joined(), name="iroh-website-keyboard-send"))


def _native_bounds(target_type, target_id):
    if os.environ.get("AUTOYOU_TEST_ROOT"): raise SessionDenied("native capture SDK is forbidden in hermetic tests")
    if target_type == "window": return get_window_bounds(target_id)
    # No fallback may silently replace an explicitly selected monitor.
    mss = _lazy_import_mss()
    with mss.mss() as capture:
        if type(target_id) is not int or not 0 <= target_id < len(capture.monitors): return None
        return {key: int(capture.monitors[target_id][key]) for key in ("left", "top", "width", "height")}


def _bounded_native_capture(bounds, scale, quality, *, capture=None):
    if os.environ.get("AUTOYOU_TEST_ROOT") and capture is None: raise SessionDenied("native capture SDK is forbidden in hermetic tests")
    captured = time.time_ns() // 1_000_000
    if capture is not None: image = capture(bounds)
    else:
        try: image = _capture_mss_image_sync(bounds)
        except Exception:
            if not _imagegrab_capture_supported(): raise SessionDenied("selected website capture is unavailable") from None
            box = bounds["left"],bounds["top"],bounds["left"]+bounds["width"],bounds["top"]+bounds["height"]
            image = ImageGrab.grab(bbox=box, **({"all_screens":True} if _current_platform_tag()=="windows" else {}))
    if image is None: raise SessionDenied("selected website capture is unavailable")
    try:
        if (image.width,image.height) != (bounds["width"],bounds["height"]): raise SessionDenied("selected website capture geometry changed")
        if image.mode != "RGB":
            converted = image.convert("RGB"); image.close(); image = converted
        size = max(1, int(image.width * scale)), max(1, int(image.height * scale))
        if size != image.size:
            resized = image.resize(size, Image.Resampling.BILINEAR)
            image.close(); image = resized
        class BoundedJPEG(BytesIO):
            def write(self, data):
                if self.tell() + len(data) > 8 * 1024**2: raise SessionDenied("website JPEG exceeds its buffer allowance")
                return super().write(data)
        with BoundedJPEG() as output:
            image.save(output, format="JPEG", quality=quality)
            return output.getvalue(), captured, image.width, image.height
    finally: image.close()


async def _stream_native_screen(websocket, channel):
    owner = None
    config = None
    try:
        while True:
            try:
                update = await asyncio.wait_for(websocket.receive_json(), timeout=.001 if config else 5)
                if isinstance(update, dict) and update.get("type") == "auth": continue
                if not isinstance(update, dict) or set(update) - {"target_type","target_id","scale","quality","fps","focus","paused","config_token"}:
                    raise SessionDenied("invalid native website configuration")
                if update != config:
                    if owner is not None:
                        await owner.close(); owner = None
                    config = update
            except asyncio.TimeoutError:
                if config is None: raise SessionDenied("website did not select a source")
            channel.registry.check(channel.binding, scope="browser")
            if config.get("paused") is True:
                await asyncio.sleep(.05); continue
            target_type, target_id = config.get("target_type"), config.get("target_id")
            scale, quality, fps = config.get("scale"), config.get("quality"), config.get("fps")
            token = config.get("config_token")
            if type(scale) not in {int,float} or not .1 <= scale <= 1 or type(quality) is not int or not 5 <= quality <= 100 or \
                    type(fps) is not int or not 1 <= fps <= _MAX_STREAM_FPS or not isinstance(token,str) or not 0 < len(token) <= 64 or config.get("focus") is not False:
                raise SessionDenied("invalid bounded native website configuration")
            if owner is None: owner = NativeWebsiteView(channel=channel,target_type=target_type,target_id=target_id,fps=fps)
            owner._check()
            bounds = await owner.worker(_native_bounds,target_type,target_id)
            if not isinstance(bounds,dict) or set(bounds) != {"left","top","width","height"} or any(type(v) is not int for v in bounds.values()) or \
                    any(not 0 < bounds[key] <= 4096 for key in ("width","height")) or bounds["width"]*bounds["height"]*4 > 32*1024**2:
                raise SessionDenied("selected website target is unavailable or exceeds capture limits")
            jpeg, captured, width, height = await owner.worker(_bounded_native_capture,bounds,scale,quality)
            if not isinstance(jpeg,bytes) or not 0 < len(jpeg) <= 8*1024**2: raise SessionDenied("invalid website JPEG receipt")
            receipt = owner.publish(bounds=bounds,captured_at_ms=captured,width=width,height=height)
            await websocket.send_json({**receipt,"config_token":token})
            owner._check()
            await websocket.send_bytes(jpeg)
            await asyncio.sleep(1/fps)
    except WebSocketDisconnect:
        pass
    except SessionDenied as error:
        await websocket.send_json({"event":"native_website_denied","reason":str(error)})
        await websocket.close(code=_SCREEN_WS_POLICY_VIOLATION)
    finally:
        if owner is not None: await owner.close()


def _native_keyboard_connection_proof(session_id: str = "", owner_key: str = "") -> Dict[str, Any]:
    fallback = {
        "connected": False,
        "resolution": "unavailable",
        "heartbeat_recent": False,
        "heartbeat_age_seconds": None,
        "keyboard_state": "inactive",
    }
    try:
        runtime = _runtime_server()
        webrtc = getattr(runtime, "WEBRTC", None)
        reader = getattr(webrtc, "remote_desktop_keyboard_connection_proof", None)
        if reader is None:
            return fallback
        proof = reader(
            session_id=session_id,
            owner_key=owner_key,
            allow_single_live_fallback=not bool(session_id or owner_key),
        )
    except Exception:
        return fallback
    if not isinstance(proof, dict):
        return fallback
    resolution = str(proof.get("resolution") or "unavailable").strip()[:48] or "unavailable"
    allowed_resolutions = {
        "reply_target",
        "single_live_datachannel",
        "unavailable",
        "ambiguous",
        "owner_mismatch",
    }
    if resolution not in allowed_resolutions:
        resolution = "unavailable"
    raw_age = proof.get("heartbeat_age_seconds")
    try:
        heartbeat_age_seconds = max(0.0, round(float(raw_age), 1)) if raw_age is not None else None
    except (TypeError, ValueError):
        heartbeat_age_seconds = None
    keyboard_state = str(proof.get("keyboard_state") or "inactive").strip().lower()
    # from __debug_provenance_b__ import yearly
    if keyboard_state not in {"inactive", "visible", "hidden"}:
        keyboard_state = "inactive"
    return {
        "connected": bool(proof.get("connected")),
        "resolution": resolution,
        "heartbeat_recent": bool(proof.get("heartbeat_recent")),
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "keyboard_state": keyboard_state,
    }


def _extra_routes(app, agent_name: str) -> None:
    @app.get("/api/remote_desktop/monitors")
    async def get_monitors(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        res = list_active_monitors()
        success = res.get("status") == "success"
        return _json_response({"success": success, **res}, status_code=200 if success else 503)

    @app.get("/api/remote_desktop/windows")
    async def get_windows(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        res = list_running_windows()
        success = res.get("status") == "success"
        return _json_response({"success": success, **res}, status_code=200 if success else 503)

    @app.post("/api/remote_desktop/input")
    async def execute_input(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON payload"}, status_code=400)
            
        try:
            channel = _native_channel(request)
            if channel is not None:
                if not isinstance(payload, dict): raise SessionDenied("invalid native input")
                owner = _native_view(channel, payload.get("native_website", {}))
                await in_view(owner, owner.receive, payload)
                return _json_response({"success": True})
            if isinstance(payload, dict) and "native_website" in payload: raise SessionDenied("native website input lost its session")
        except SessionDenied as error:
            return _json_response({"success": False, "error": str(error)}, status_code=403)
        success = _simulate_input(payload)
        return _json_response({"success": success})

    @app.post("/api/remote_desktop/control")
    async def control_website(request: Request):
        if not _describe_chat_auth_state(request, agent_name).get("authenticated"):
            return _json_response({"success": False}, status_code=401)
        try:
            payload = await request.json()
            channel = _native_channel(request)
            if channel is None or not isinstance(payload,dict): raise SessionDenied("native website control is unavailable")
            owner = _native_view(channel,payload)
            if payload.get("action") == "approve":
                grant = await in_view(owner,owner.approve,control_id=payload.get("control_id"),source=payload.get("source"),
                    frame_sequence=payload.get("frame_sequence"),keyboard_only=False)
                return _json_response({"success":True,**grant})
            if payload.get("action") == "stop":
                owner.cleanup_grant(payload.get("control_id"))
                await in_view(owner,owner.stop_control)
                return _json_response({"success":True})
            raise SessionDenied("invalid website control action")
        except (SessionDenied, ValueError) as error:
            return _json_response({"success":False,"error":str(error)},status_code=403)

    @app.get("/api/remote_desktop/native-keyboard/status")
    async def get_native_keyboard_status(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            channel = _native_channel(request)
            if channel is not None:
                visible = False
                try:
                    owner = website_view(channel.binding, _request_query_param(request,"view_id"))
                    owner._check()
                    if owner._lease is not None: owner._lease._check()
                    visible = owner._lease is not None and owner._keyboard_only is True
                except SessionDenied: pass
                return _json_response({"success":True,"native_transport":True,"native_keyboard":{
                    "connected":True,"keyboard_state":"visible" if visible else "inactive"}})
        except SessionDenied as error:
            return _json_response({"success":False,"error":str(error)},status_code=403)
        session_id, owner_key = _coalesce_session_identity(request)
        proof = _native_keyboard_connection_proof(session_id=session_id, owner_key=owner_key)
        return _json_response({"success": True, "native_keyboard": proof})

    @app.post("/api/remote_desktop/native-keyboard")
    async def control_native_keyboard(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON payload"}, status_code=400)
        if not isinstance(payload, dict):
            return _json_response({"success": False, "error": "Invalid JSON payload"}, status_code=400)

        action = str(payload.get("action") or "").strip().lower()
        if action not in {"show", "hide"}:
            return _json_response({"success": False, "error": "Only show and hide are supported here."}, status_code=400)

        try:
            channel = _native_channel(request)
            if channel is not None:
                owner = _native_view(channel,payload)
                if action == "show":
                    # Browser pointer and native IME have distinct input sequences.
                    # Changing the writer joins the old owner before new approval.
                    if owner._lease is not None and (owner._keyboard_only is not True or owner._lease.authority.control_id != payload.get("control_id")):
                        await in_view(owner,owner.stop_control)
                    grant = await in_view(owner,owner.approve,control_id=payload.get("control_id"),source=payload.get("source"),
                        frame_sequence=payload.get("frame_sequence"),keyboard_only=True)
                    try: await _website_keyboard(owner,"show",grant)
                    except BaseException:
                        await in_view(owner,owner.stop_control); raise
                else:
                    grant = owner.cleanup_grant(payload.get("control_id"))
                    try: await _website_keyboard(owner,"hide",grant)
                    finally: await in_view(owner,owner.stop_control)
                return _json_response({"success":True,**grant})
        except SessionDenied as error:
            return _json_response({"success":False,"error":str(error)},status_code=403)

        session_id, owner_key = _coalesce_session_identity(request)
        reply_target: Dict[str, Any] = {"transport": "webrtc"}
        if session_id:
            reply_target["session_id"] = session_id
        if owner_key:
            reply_target["owner_key"] = owner_key

        runtime = _runtime_server()
        webrtc = getattr(runtime, "WEBRTC", None)
        sender = getattr(webrtc, "send_remote_desktop_keyboard_control_to_reply_target", None)
        if sender is None:
            return _json_response({"success": False, "reason": "WebRTC keyboard control is unavailable."}, status_code=503)

        success, result = await sender(
            reply_target,
            {"action": action, "control_id": str(payload.get("control_id") or "").strip()},
            allow_single_live_fallback=not bool(session_id or owner_key),
        )
        if success:
            return _json_response(result)
        resolution = str(result.get("resolution") or "").strip().lower()
        status_code = 409 if resolution == "ambiguous" else 503
        return _json_response(result, status_code=status_code)

    @app.get("/api/video_call/status")
    async def get_video_call_status(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            channel = _native_channel(request)
            if channel is not None:
                channel.registry.check(channel.binding,scope="media")
                frame = VIDEO_FRAME_REGISTRY.latest(channel.binding.transport_id)
                return _json_response(dict(success=True,active=frame is not None,active_sessions=int(frame is not None),
                    latest_session_id=frame.session_id if frame else None,latest_sequence=frame.sequence if frame else 0,
                    latest_timestamp_ms=frame.timestamp_ms if frame else None,latest_width=frame.width if frame else None,latest_height=frame.height if frame else None))
        except SessionDenied as error:
            return _json_response({"success":False,"error":str(error)},status_code=403)
        return _json_response({"success": True, **VIDEO_FRAME_REGISTRY.status()})

    # High-Performance Binary JPEG streaming WebSocket
    @app.websocket("/ws/screen")
    async def stream_screen(websocket: WebSocket):
        await websocket.accept()
        try:
            auth = await _authenticate_screen_websocket(websocket, agent_name)
        except WebSocketDisconnect:
            return
        if not auth.get("authenticated"):
            return

        try:
            channel = _native_channel(websocket)
            if channel is not None:
                await _stream_native_screen(websocket,channel)
                return
        except SessionDenied:
            await websocket.close(code=_SCREEN_WS_POLICY_VIOLATION)
            return

        logger.info("Remote Desktop screen WebSocket connected.")
        
        # Default stream parameters
        config = {
            "target_type": "monitor",
            "target_id": 0,
            "scale": _DEFAULT_STREAM_SCALE,
            "quality": _DEFAULT_STREAM_QUALITY,
            "fps": _DEFAULT_STREAM_FPS,
            "focus": False,
            "paused": False,
        }
        
        # Thread-offloaded synchronous helper for window activation
        def _activate_window_sync(target_id: Any):
            try:
                if activate_window(target_id):
                    logger.info("Activated selected remote desktop window target: %s", target_id)
            except Exception as e:
                logger.error("Error focusing selected window: %s", e)

        # Thread-offloaded synchronous helper for window bounds retrieval
        def _get_window_bounds_sync(target_id: int):
            try:
                bounds = get_window_bounds(target_id)
                if bounds:
                    return {
                        "left": int(bounds["left"]),
                        "top": int(bounds["top"]),
                        "width": max(10, int(bounds["width"])),
                        "height": max(10, int(bounds["height"])),
                    }
            except Exception:
                pass
            return None

        async def read_config():
            try:
                # Non-blocking check for WebSocket text messages updating configuration
                data = await asyncio.wait_for(websocket.receive_json(), timeout=0.001)
                if isinstance(data, dict) and data.get("type") == "auth":
                    return
                if not isinstance(data, dict):
                    return
                
                # Track target state changes to bring windows to the front
                old_target_type = config.get("target_type")
                old_target_id = config.get("target_id")
                
                config.update(data)
                logger.info("Updated stream configuration: %s", config)
                
                new_target_type = config.get("target_type")
                new_target_id = config.get("target_id")
                
                if (
                    _coerce_bool(config.get("focus"))
                    and new_target_type == "window"
                    and new_target_id not in (None, "", 0, "0")
                    and (new_target_type != old_target_type or new_target_id != old_target_id)
                ):
                    await asyncio.to_thread(_activate_window_sync, new_target_id)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                raise
            except Exception:
                pass

        try:
            while True:
                await read_config()
                
                target_type = config.get("target_type", "monitor")
                target_id = config.get("target_id", 0)
                scale = max(0.1, min(1.0, float(config.get("scale", _DEFAULT_STREAM_SCALE))))
                quality = max(5, min(100, int(config.get("quality", _DEFAULT_STREAM_QUALITY))))
                fps = max(1, min(_MAX_STREAM_FPS, int(config.get("fps", _DEFAULT_STREAM_FPS))))
                paused = _coerce_bool(config.get("paused"))
                if paused:
                    await asyncio.sleep(1.0 / fps)
                    continue
                
                # Capture bounds determination
                bounds = None
                if target_type == "monitor":
                    bounds = await asyncio.to_thread(_get_monitor_bounds_sync, target_id)
                elif target_type == "window":
                    bounds = await asyncio.to_thread(_get_window_bounds_sync, target_id)
                        
                if bounds is None and target_type != "window":
                    # Fallback to absolute virtual union screen
                    bounds = await asyncio.to_thread(_get_monitor_bounds_sync, 0)
                    
                if bounds is not None:
                    # Perform screenshot capture and pil encoding on threadpool
                    jpeg_bytes = await asyncio.to_thread(_capture_and_encode_sync, bounds, scale, quality)
                    
                    if jpeg_bytes is not None:
                        # Stream frame down binary WebSocket channel
                        await websocket.send_bytes(jpeg_bytes)
                    
                # Dynamic FPS pacing
                await asyncio.sleep(1.0 / fps)
                
        except WebSocketDisconnect:
            logger.info("Remote Desktop WebSocket disconnected cleanly.")
        except Exception as e:
            logger.error("WebSocket loop error: %s", e)

    @app.websocket("/ws/mobile-video")
    async def stream_mobile_video(websocket: WebSocket):
        await websocket.accept()
        try:
            auth = await _authenticate_screen_websocket(websocket, agent_name)
        except WebSocketDisconnect:
            return
        if not auth.get("authenticated"):
            return

        try:
            native = _native_channel(websocket)
            if native is not None: native.registry.check(native.binding,scope="media")
        except SessionDenied:
            await websocket.close(code=_SCREEN_WS_POLICY_VIOLATION);return

        logger.info("Remote Desktop mobile video WebSocket connected.")
        config = {
            "fps": _DEFAULT_MOBILE_VIDEO_FPS,
            "session_id": native.binding.transport_id if native is not None else None,
            "paused": False,
        }
        last_sequence = 0
        feed_active = False

        async def read_config():
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=0.001)
                if isinstance(data, dict) and data.get("type") == "auth":
                    return
                if isinstance(data, dict):
                    if native is not None and (set(data)-{"fps","paused","session_id"} or
                            ("session_id" in data and data["session_id"] != native.binding.transport_id) or
                            ("fps" in data and (type(data["fps"]) is not int or not 1 <= data["fps"] <= _MAX_MOBILE_VIDEO_FPS)) or
                            ("paused" in data and type(data["paused"]) is not bool)):
                        raise SessionDenied("native camera website cannot change its admitted source")
                    config.update(data)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                raise
            except SessionDenied:
                raise
            except Exception:
                pass

        try:
            while True:
                await read_config()
                if native is not None:
                    native.registry.check(native.binding,scope="browser")
                    native.registry.check(native.binding,scope="media")
                fps = max(1, min(_MAX_MOBILE_VIDEO_FPS, int(config.get("fps", _DEFAULT_MOBILE_VIDEO_FPS))))
                if _coerce_bool(config.get("paused")):
                    await asyncio.sleep(1.0 / fps)
                    continue

                session_id = str(config.get("session_id") or "").strip() or None
                work = asyncio.create_task(asyncio.to_thread(
                    VIDEO_FRAME_REGISTRY.wait_for_frame,
                    last_sequence=last_sequence,
                    session_id=session_id,
                    timeout=max(0.05, 1.0 / fps),
                ),name="website-camera-frame-wait")
                frame = await _join_owned(work)
                if frame is not None:
                    if native is not None:
                        native.registry.check(native.binding,scope="browser")
                        native.registry.check(native.binding,scope="media")
                        if frame.session_id != native.binding.transport_id or len(frame.jpeg_bytes)>8*1024**2:
                            raise SessionDenied("camera website frame changed its owner or buffer limit")
                    last_sequence = frame.sequence
                    feed_active = True
                    await websocket.send_bytes(frame.jpeg_bytes)
                else:
                    # The registry drops frames when the phone stops its camera
                    # (or they expire); tell the viewer so it can blank the
                    # stale frame instead of freezing on it.
                    if feed_active and VIDEO_FRAME_REGISTRY.latest(session_id) is None:
                        feed_active = False
                        await websocket.send_json({"type": "feed_state", "active": False})
                    await asyncio.sleep(1.0 / fps)
        except WebSocketDisconnect:
            logger.info("Remote Desktop mobile video WebSocket disconnected cleanly.")
        except SessionDenied:
            await websocket.close(code=_SCREEN_WS_POLICY_VIOLATION)
        except Exception as e:
            logger.error("Mobile video WebSocket loop error: %s", e)


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Remote Desktop",
    description="Real-time loopback display caster and secure keyboard/cursor mapped remote mouse operator.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
