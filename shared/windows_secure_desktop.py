# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Remote Desktop input and capture on the Windows lock screen and UAC prompts.

Why this exists: AutoYou Server runs as the signed-in user. When Windows
locks, or shows a UAC prompt, the screen switches to the "Winlogon" secure
desktop. Ordinary user processes cannot draw from or send input to that
desktop, so capture returns no frame and typed keys and taps go nowhere.
TeamViewer and similar tools solve this with a SYSTEM service; this module
does the same thing in three parts:

* ``service``: a Windows service running as LocalSystem in session 0. It keeps
  one ``helper`` process alive in the active console session.
* ``helper``: runs as SYSTEM inside the user's session, so it may attach to
  whichever desktop currently receives input (Default, Winlogon, or the UAC
  secure desktop). It serves a small authenticated protocol on 127.0.0.1 that
  performs pyautogui calls and screen captures on that desktop.
* client (used by the server): while the session is locked or the input
  desktop is out of reach, Remote Desktop input and capture are routed
  through the helper; otherwise nothing changes.

Access to the helper needs a random token stored in
``%ProgramData%\\AutoYou\\SecureDesktop\\helper.json``, which is readable only
by SYSTEM, Administrators, and the one user account chosen at install time.

Install from an elevated prompt on the host (one time):
``AutoYou.exe --secure-desktop install`` (packaged build) or
``python autoyou_app.py --secure-desktop install`` (source checkout).
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import hmac
import json
import logging
import os
import platform
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

LOGGER = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
SERVICE_NAME = "AutoYouSecureDesktop"
SERVICE_DISPLAY_NAME = "AutoYou Secure Desktop Helper"
SERVICE_DESCRIPTION = (
    "Lets AutoYou Remote Desktop see and control the Windows sign-in screen, "
    "lock screen, and UAC prompts for the paired owner of this computer."
)
PROTOCOL_VERSION = 1
MAX_JSON_BYTES = 64 * 1024
MAX_BLOB_BYTES = 256 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 5.0
STATE_CACHE_SECONDS = 0.5
UNAVAILABLE_RETRY_SECONDS = 5.0

# The pyautogui surface Remote Desktop uses. Nothing else can be called.
ALLOWED_CALLS = frozenset(
    {
        "moveTo",
        "moveRel",
        "click",
        "doubleClick",
        "mouseDown",
        "mouseUp",
        "scroll",
        "hscroll",
        "keyDown",
        "keyUp",
        "press",
        "write",
        "position",
        "size",
    }
)
ALLOWED_CALL_KWARGS = frozenset({"button"})
MAX_CALL_ARGS = 4
MAX_CALL_STRING = 64
_SAS_TRIGGER_KEYS = frozenset({"delete", "del"})


# ---------------------------------------------------------------------------
# Wire format: 8-byte header (JSON length, blob length), JSON, optional blob.
# ---------------------------------------------------------------------------
def encode_frame(payload: Dict[str, Any], blob: bytes = b"") -> bytes:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_JSON_BYTES:
        raise ValueError("Secure desktop frame is too large")
    return struct.pack(">II", len(data), len(blob)) + data + bytes(blob)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: List[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(min(remaining, 1024 * 1024))
        if not chunk:
            raise ConnectionError("Secure desktop connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket, *, max_blob: int = MAX_BLOB_BYTES) -> Tuple[Dict[str, Any], bytes]:
    json_len, blob_len = struct.unpack(">II", _recv_exact(sock, 8))
    if json_len > MAX_JSON_BYTES or blob_len > max_blob:
        raise ValueError("Secure desktop frame exceeds limits")
    payload = json.loads(_recv_exact(sock, json_len).decode("utf-8")) if json_len else {}
    if not isinstance(payload, dict):
        raise ValueError("Secure desktop frame must be a JSON object")
    blob = _recv_exact(sock, blob_len) if blob_len else b""
    return payload, blob


def validate_call(name: Any, args: Any, kwargs: Any) -> Tuple[str, List[Any], Dict[str, Any]]:
    """Reject anything that is not a plain, bounded pyautogui call."""
    if not isinstance(name, str) or name not in ALLOWED_CALLS:
        raise ValueError("Call is not allowed")
    if args is None:
        args = []
    if kwargs is None:
        kwargs = {}
    if not isinstance(args, list) or len(args) > MAX_CALL_ARGS:
        raise ValueError("Invalid call arguments")
    if not isinstance(kwargs, dict) or any(key not in ALLOWED_CALL_KWARGS for key in kwargs):
        raise ValueError("Invalid call keyword arguments")
    for value in list(args) + list(kwargs.values()):
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            if value != value or abs(float(value)) > 1_000_000:
                raise ValueError("Invalid numeric argument")
            continue
        if isinstance(value, str) and len(value) <= MAX_CALL_STRING:
            continue
        raise ValueError("Invalid call argument type")
    return name, list(args), dict(kwargs)


# ---------------------------------------------------------------------------
# Paths and launcher command
# ---------------------------------------------------------------------------
def rendezvous_dir() -> Path:
    base = os.environ.get("ProgramData") or os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(base) / "AutoYou" / "SecureDesktop"


def rendezvous_path() -> Path:
    return rendezvous_dir() / "helper.json"


def log_path() -> Path:
    return rendezvous_dir() / "secure-desktop.log"


def _is_compiled_launcher() -> bool:
    import builtins

    if getattr(sys, "frozen", False) or getattr(builtins, "__compiled__", None) is not None:
        return True
    main_module = sys.modules.get("__main__")
    return bool(main_module is not None and "__compiled__" in vars(main_module))


def launcher_command() -> List[str]:
    """Command that re-enters this AutoYou install's launcher."""
    if _is_compiled_launcher():
        return [sys.executable]
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", "") if main_module is not None else ""
    if main_file and Path(main_file).name == "autoyou_app.py":
        script = Path(main_file).resolve()
    else:
        script = Path(__file__).resolve().parents[1] / "autoyou_app.py"
    python = sys.executable
    # Prefer python.exe over pythonw.exe so service logs stay attached.
    if python.lower().endswith("pythonw.exe"):
        candidate = Path(python).with_name("python.exe")
        if candidate.exists():
            python = str(candidate)
    return [python, str(script)]


# ---------------------------------------------------------------------------
# Windows API helpers (ctypes only, so no pywin32 dependency)
# ---------------------------------------------------------------------------
_DESKTOP_READOBJECTS = 0x0001
_DESKTOP_SWITCHDESKTOP = 0x0100
_MAXIMUM_ALLOWED = 0x02000000
_UOI_NAME = 2
_WTS_CURRENT_SESSION = 0xFFFFFFFF
_WTS_SESSION_INFO_EX = 25
_INVALID_SESSION = 0xFFFFFFFF


def _win():  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    user32.SetThreadDesktop.restype = wintypes.BOOL
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetUserObjectInformationW.restype = wintypes.BOOL
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
    kernel32.WTSGetActiveConsoleSessionId.argtypes = []
    kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
    return ctypes, wintypes, user32, kernel32, wtsapi32, advapi32


def _desktop_name(handle: Any) -> Optional[str]:  # pragma: no cover - Windows only
    ctypes, wintypes, user32, *_ = _win()
    buffer = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD(0)
    if not user32.GetUserObjectInformationW(handle, _UOI_NAME, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
        return None
    return buffer.value or None


def input_desktop_name() -> Optional[str]:
    """Name of the desktop receiving input, or None if this process can't open it."""
    if not IS_WINDOWS:
        return None
    try:  # pragma: no cover - Windows only
        _ctypes, _wintypes, user32, *_ = _win()
        handle = user32.OpenInputDesktop(0, False, _DESKTOP_READOBJECTS)
        if not handle:
            return None
        try:
            return _desktop_name(handle)
        finally:
            user32.CloseDesktop(handle)
    except Exception:
        return None


def session_locked(session_id: int = _WTS_CURRENT_SESSION) -> Optional[bool]:
    """True when the Windows session is locked; None when unknown."""
    if not IS_WINDOWS:
        return None
    try:  # pragma: no cover - Windows only
        ctypes, wintypes, _user32, _kernel32, wtsapi32, _advapi32 = _win()
        buffer = ctypes.c_void_p()
        returned = wintypes.DWORD(0)
        if not wtsapi32.WTSQuerySessionInformationW(
            None, session_id, _WTS_SESSION_INFO_EX, ctypes.byref(buffer), ctypes.byref(returned)
        ):
            return None
        try:
            if not buffer.value or returned.value < 20:
                return None
            level = ctypes.c_uint32.from_address(buffer.value).value
            if level != 1:
                return None
            # WTSINFOEXW: DWORD Level, then an 8-byte aligned union whose
            # WTSINFOEX_LEVEL1_W starts SessionId, SessionState, SessionFlags.
            flags = ctypes.c_int32.from_address(buffer.value + 16).value
        finally:
            wtsapi32.WTSFreeMemory(buffer)
        if flags not in (0, 1):
            return None
        locked = flags == 0  # WTS_SESSIONSTATE_LOCK
        # Windows 7 / Server 2008 R2 report these two values inverted.
        if platform.version().startswith("6.1"):
            locked = not locked
        return locked
    except Exception:
        return None


def active_console_session_id() -> Optional[int]:
    if not IS_WINDOWS:
        return None
    try:  # pragma: no cover - Windows only
        _ctypes, _wintypes, _user32, kernel32, *_ = _win()
        session = int(kernel32.WTSGetActiveConsoleSessionId())
        return None if session == _INVALID_SESSION else session
    except Exception:
        return None


def _apply_sddl(path: Path, sddl: str) -> None:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi32.SetFileSecurityW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        owner_group_dacl_protected = 0x1 | 0x2 | 0x4 | 0x80000000
        if not advapi32.SetFileSecurityW(str(path), owner_group_dacl_protected, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.LocalFree(descriptor)


def _is_valid_sid(value: Any) -> bool:
    text = str(value or "")
    if not text.startswith("S-1-") or len(text) > 184:
        return False
    return all(part.isdigit() for part in text[4:].split("-")) and "--" not in text


def _secure_sddl(allow_sid: Optional[str], *, inherit: bool) -> str:
    flags = "OICI" if inherit else ""
    sddl = f"O:SYG:SYD:P(A;{flags};FA;;;SY)(A;{flags};FA;;;BA)"
    if allow_sid:
        sddl += f"(A;{flags};FR;;;{allow_sid})"
    return sddl


def current_user_sid() -> Optional[str]:
    if not IS_WINDOWS:
        return None
    try:  # pragma: no cover - Windows only
        output = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        sid = output.strip().rsplit(",", 1)[-1].strip().strip('"')
        return sid if _is_valid_sid(sid) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Locked / secure desktop detection for the server process
# ---------------------------------------------------------------------------
_STATE_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}
_STATE_LOCK = threading.Lock()


def secure_desktop_state(*, refresh: bool = False) -> Dict[str, Any]:
    """Whether the normal user-session input/capture path can reach the screen."""
    if not IS_WINDOWS:
        return {"platform_supported": False, "secure": False, "locked": None, "input_desktop": None}
    now = time.monotonic()
    with _STATE_LOCK:
        cached = _STATE_CACHE.get("value")
        if not refresh and cached is not None and now - float(_STATE_CACHE["at"]) < STATE_CACHE_SECONDS:
            return dict(cached)
    name = input_desktop_name()
    locked = session_locked()
    secure = bool(locked is True or name is None or name.lower() != "default")
    value = {
        "platform_supported": True,
        "secure": secure,
        "locked": locked,
        "input_desktop": name,
        "input_desktop_accessible": name is not None,
    }
    with _STATE_LOCK:
        _STATE_CACHE.update({"at": now, "value": value})
    return dict(value)


# ---------------------------------------------------------------------------
# Client used by the server process
# ---------------------------------------------------------------------------
class SecureDesktopError(RuntimeError):
    pass


class SecureDesktopClient:
    """Authenticated loopback client. Separate channels keep video frames from
    delaying keystrokes."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path
        self._locks: Dict[str, threading.Lock] = {"input": threading.Lock(), "capture": threading.Lock()}
        self._sockets: Dict[str, Optional[socket.socket]] = {"input": None, "capture": None}
        self._unavailable_until = 0.0
        self._last_error = ""
        self._last_ok_at = 0.0

    def recently_reachable(self) -> Optional[bool]:
        if self._last_ok_at and time.monotonic() - self._last_ok_at < 60.0:
            return True
        if self._last_error:
            return False
        return None

    @property
    def path(self) -> Path:
        return self._path or rendezvous_path()

    @property
    def last_error(self) -> str:
        return self._last_error

    def _read_info(self) -> Optional[Dict[str, Any]]:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._last_error = "not_installed"
            return None
        except PermissionError:
            self._last_error = "access_denied"
            return None
        except Exception:
            self._last_error = "unreadable"
            return None
        if not isinstance(info, dict):
            return None
        try:
            port = int(info.get("port") or 0)
        except Exception:
            return None
        token = str(info.get("token") or "")
        if not (0 < port < 65536) or len(token) < 32:
            return None
        return {"port": port, "token": token, "pid": info.get("pid"), "session_id": info.get("session_id")}

    def installed(self) -> bool:
        try:
            return self.path.exists()
        except Exception:
            return False

    def _connect(self) -> socket.socket:
        info = self._read_info()
        if info is None:
            raise SecureDesktopError(self._last_error or "helper unavailable")
        sock = socket.create_connection(("127.0.0.1", info["port"]), timeout=CONNECT_TIMEOUT_SECONDS)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(REQUEST_TIMEOUT_SECONDS)
            sock.sendall(encode_frame({"op": "hello", "token": info["token"], "version": PROTOCOL_VERSION}))
            reply, _ = read_frame(sock, max_blob=0)
            if not reply.get("ok"):
                raise SecureDesktopError(str(reply.get("error") or "helper refused the connection"))
        except Exception:
            sock.close()
            raise
        return sock

    def _drop(self, channel: str) -> None:
        sock = self._sockets.get(channel)
        self._sockets[channel] = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def request(self, channel: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], bytes]:
        if time.monotonic() < self._unavailable_until:
            raise SecureDesktopError(self._last_error or "helper unavailable")
        with self._locks[channel]:
            last_exc: Optional[BaseException] = None
            # A cached connection may be stale (the helper restarts with a new
            # port and token when the console session changes), so retry once
            # on a fresh connection. A fresh connection is never retried, so
            # an input event is not applied twice.
            for _attempt in range(2):
                fresh = self._sockets.get(channel) is None
                try:
                    if fresh:
                        self._last_error = ""
                        self._sockets[channel] = self._connect()
                    sock = self._sockets[channel]
                    assert sock is not None
                    sock.sendall(encode_frame(payload))
                    reply, blob = read_frame(sock)
                except Exception as exc:
                    self._drop(channel)
                    last_exc = exc
                    if not self._last_error:
                        self._last_error = "unreachable"
                    if fresh:
                        break
                    continue
                if not reply.get("ok"):
                    # Delivered but refused (e.g. invalid call): not an outage.
                    raise SecureDesktopError(str(reply.get("error") or "helper request failed"))
                self._last_error = ""
                self._last_ok_at = time.monotonic()
                return reply, blob
            self._unavailable_until = time.monotonic() + UNAVAILABLE_RETRY_SECONDS
            raise SecureDesktopError(str(last_exc or "helper unavailable"))

    def available(self) -> bool:
        try:
            self.request("input", {"op": "ping"})
            return True
        except Exception:
            return False

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        name, call_args, call_kwargs = validate_call(name, list(args), dict(kwargs))
        reply, _ = self.request("input", {"op": "call", "name": name, "args": call_args, "kwargs": call_kwargs})
        return reply.get("result")

    def status(self) -> Dict[str, Any]:
        reply, _ = self.request("input", {"op": "status"})
        return reply

    def capture(self, monitor_id: int = 0, max_width: Optional[int] = None) -> Any:
        payload: Dict[str, Any] = {"op": "capture", "monitor_id": int(monitor_id)}
        if max_width:
            payload["max_width"] = int(max_width)
        reply, blob = self.request("capture", payload)
        from PIL import Image  # type: ignore

        width, height = int(reply.get("width") or 0), int(reply.get("height") or 0)
        mode = str(reply.get("mode") or "RGB")
        if width <= 0 or height <= 0:
            raise SecureDesktopError("helper returned an empty frame")
        if mode == "BGRX":
            return Image.frombytes("RGB", (width, height), blob, "raw", "BGRX")
        return Image.frombytes("RGB", (width, height), blob)


class SecureDesktopInputBackend:
    """Duck-typed pyautogui stand-in that forwards calls to the helper."""

    FAILSAFE = False
    PAUSE = 0

    def __init__(self, client: SecureDesktopClient) -> None:
        object.__setattr__(self, "_client", client)

    def __setattr__(self, name: str, value: Any) -> None:
        # pyautogui settings like FAILSAFE/PAUSE are fixed in the helper.
        if name in {"FAILSAFE", "PAUSE"}:
            return
        object.__setattr__(self, name, value)

    def __getattr__(self, name: str) -> Any:
        if name not in ALLOWED_CALLS:
            raise AttributeError(name)
        client = self._client

        def _call(*args: Any, **kwargs: Any) -> Any:
            result = client.call(name, *args, **kwargs)
            if name in {"position", "size"} and isinstance(result, list) and len(result) == 2:
                return int(result[0]), int(result[1])
            return result

        return _call


_CLIENT: Optional[SecureDesktopClient] = None
_CLIENT_LOCK = threading.Lock()


def get_secure_desktop_client() -> SecureDesktopClient:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = SecureDesktopClient()
        return _CLIENT


def secure_input_backend_if_needed() -> Optional[Any]:
    """The helper-backed backend while the screen is locked, else None.

    Raises ``SecureDesktopError`` when the screen is locked but the helper is
    not available, so callers report a failure instead of silently dropping
    input on a desktop they cannot reach.
    """
    if not IS_WINDOWS:
        return None
    state = secure_desktop_state()
    if not state.get("secure"):
        return None
    client = get_secure_desktop_client()
    if not client.available():
        raise SecureDesktopError(secure_desktop_unavailable_hint() or "secure desktop unavailable")
    return SecureDesktopInputBackend(client)


def capture_secure_desktop_image_if_needed(monitor_id: int = 0, max_width: Optional[int] = None) -> Any:
    """A frame captured by the helper while the screen is locked, else None."""
    if not IS_WINDOWS:
        return None
    if not secure_desktop_state().get("secure"):
        return None
    try:
        return get_secure_desktop_client().capture(monitor_id, max_width=max_width)
    except Exception as exc:
        LOGGER.debug("Secure desktop capture unavailable: %s", exc)
        return None


def secure_desktop_unavailable_hint() -> str:
    """Operator-facing reason when the locked screen can't be reached."""
    if not IS_WINDOWS:
        return ""
    state = secure_desktop_state()
    if not state.get("secure"):
        return ""
    client = get_secure_desktop_client()
    error = client.last_error
    if error == "access_denied":
        return (
            "This computer is locked. The AutoYou Secure Desktop Helper is installed for a "
            "different Windows account; reinstall it while signed in as this account."
        )
    if error in {"unreachable", "unreadable"} or (client.installed() and error):
        return (
            "This computer is locked and the AutoYou Secure Desktop Helper is not responding. "
            "Restart the AutoYouSecureDesktop service on this computer."
        )
    return (
        "This computer is locked, and Windows blocks normal apps from the lock screen. "
        "Install the AutoYou Secure Desktop Helper on this computer (Video & Calls > "
        "Remote Desktop) to unlock it remotely."
    )


def secure_desktop_status(*, probe: bool = False) -> Dict[str, Any]:
    """Status block for capabilities and the admin UI. Never raises.

    Without ``probe`` this never touches the network (a refused loopback
    connect can take seconds on Windows), and ``reachable`` reflects the last
    request made to the helper.
    """
    if not IS_WINDOWS:
        return {"supported": False, "installed": False, "reachable": False, "locked": None, "secure": False}
    try:
        state = secure_desktop_state()
        client = get_secure_desktop_client()
        installed = client.installed()
        if not installed:
            reachable: Optional[bool] = False
        elif probe:
            reachable = client.available()
        else:
            reachable = client.recently_reachable()
        return {
            "supported": True,
            "installed": bool(installed),
            "reachable": bool(reachable),
            "locked": state.get("locked"),
            "secure": bool(state.get("secure")),
            "input_desktop": state.get("input_desktop"),
            "error": "" if reachable is not False else client.last_error,
            "service_name": SERVICE_NAME,
        }
    except Exception as exc:
        return {"supported": True, "installed": False, "reachable": False, "error": str(exc)[:200]}


# ---------------------------------------------------------------------------
# Helper (SYSTEM, in the console session)
# ---------------------------------------------------------------------------
class _ThreadDesktop(threading.local):
    handle: Any = None
    name: Optional[str] = None


_THREAD_DESKTOP = _ThreadDesktop()


def attach_thread_to_input_desktop() -> Optional[str]:  # pragma: no cover - Windows only
    """Move the calling thread onto the desktop that currently receives input."""
    _ctypes, _wintypes, user32, *_ = _win()
    handle = user32.OpenInputDesktop(0, False, _MAXIMUM_ALLOWED)
    if not handle:
        return None
    name = _desktop_name(handle)
    if _THREAD_DESKTOP.handle is not None and name and name == _THREAD_DESKTOP.name:
        user32.CloseDesktop(handle)
        return name
    if not user32.SetThreadDesktop(handle):
        user32.CloseDesktop(handle)
        return None
    previous = _THREAD_DESKTOP.handle
    _THREAD_DESKTOP.handle = handle
    _THREAD_DESKTOP.name = name
    if previous is not None:
        user32.CloseDesktop(previous)
    return name


def _set_helper_dpi_awareness() -> None:  # pragma: no cover - Windows only
    import ctypes

    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _send_sas() -> bool:  # pragma: no cover - Windows only
    import ctypes

    try:
        sas = ctypes.WinDLL("sas")
        sas.SendSAS(False)
        return True
    except Exception as exc:
        LOGGER.warning("SendSAS failed: %s", exc)
        return False


class SecureDesktopHelper:
    def __init__(self, allow_sid: Optional[str]) -> None:
        self.allow_sid = allow_sid if _is_valid_sid(allow_sid) else None
        self.token = secrets.token_hex(32)
        self._pyautogui: Any = None
        self._pyautogui_lock = threading.Lock()
        self._input_lock = threading.Lock()

    def _backend(self) -> Any:
        with self._pyautogui_lock:
            if self._pyautogui is None:
                from shared.remote_desktop_keyboard import load_pyautogui

                backend = load_pyautogui()
                backend.FAILSAFE = False
                backend.PAUSE = 0
                self._pyautogui = backend
            return self._pyautogui

    def write_rendezvous(self, port: int) -> None:  # pragma: no cover - Windows only
        directory = rendezvous_dir()
        directory.mkdir(parents=True, exist_ok=True)
        if os.path.islink(directory):
            raise RuntimeError("Refusing to use a linked secure desktop folder")
        _apply_sddl(directory, _secure_sddl(self.allow_sid, inherit=True))
        target = rendezvous_path()
        temp = directory / f"helper.{os.getpid()}.tmp"
        payload = {
            "port": int(port),
            "token": self.token,
            "pid": os.getpid(),
            "session_id": active_console_session_id(),
            "version": PROTOCOL_VERSION,
        }
        temp.write_text(json.dumps(payload), encoding="utf-8")
        _apply_sddl(temp, _secure_sddl(self.allow_sid, inherit=False))
        os.replace(temp, target)

    def serve_forever(self) -> None:  # pragma: no cover - Windows only
        _set_helper_dpi_awareness()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        self.write_rendezvous(listener.getsockname()[1])
        LOGGER.info("Secure desktop helper listening (pid=%s).", os.getpid())
        while True:
            conn, _addr = listener.accept()
            threading.Thread(target=self.handle_connection, args=(conn,), daemon=True).start()

    def handle_connection(self, conn: socket.socket) -> None:
        held: set = set()
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.settimeout(10.0)
            hello, _ = read_frame(conn, max_blob=0)
            if hello.get("op") != "hello" or not hmac.compare_digest(
                str(hello.get("token") or "").encode("utf-8"), self.token.encode("utf-8")
            ):
                conn.sendall(encode_frame({"ok": False, "error": "unauthorized"}))
                return
            conn.sendall(encode_frame({"ok": True, "version": PROTOCOL_VERSION}))
            conn.settimeout(None)
            while True:
                request, _ = read_frame(conn, max_blob=0)
                try:
                    reply, blob = self.handle_request(request, held)
                except Exception as exc:
                    reply, blob = {"ok": False, "error": str(exc)[:200]}, b""
                conn.sendall(encode_frame(reply, blob))
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def handle_request(self, request: Dict[str, Any], held: set) -> Tuple[Dict[str, Any], bytes]:
        op = request.get("op")
        if op == "ping":
            return {"ok": True}, b""
        if op == "status":
            return {
                "ok": True,
                "desktop": attach_thread_to_input_desktop() if IS_WINDOWS else None,
                "locked": session_locked(),
                "session_id": active_console_session_id(),
            }, b""
        if op == "call":
            name, args, kwargs = validate_call(request.get("name"), request.get("args"), request.get("kwargs"))
            return {"ok": True, "result": self._perform_call(name, args, kwargs, held)}, b""
        if op == "capture":
            return self._capture(request)
        raise ValueError("Unknown operation")

    def _perform_call(self, name: str, args: List[Any], kwargs: Dict[str, Any], held: set) -> Any:
        key = str(args[0]).lower() if args and isinstance(args[0], str) else ""
        if name == "keyDown" and key:
            held.add(key)
        elif name == "keyUp" and key:
            held.discard(key)
        # Windows never accepts a synthesized Ctrl+Alt+Del; ask Winlogon for
        # the secure attention sequence instead.
        if name in {"press", "keyDown"} and key in _SAS_TRIGGER_KEYS and {"ctrl", "alt"} <= held:
            return bool(_send_sas()) if IS_WINDOWS else False
        if name == "keyUp" and key in _SAS_TRIGGER_KEYS and {"ctrl", "alt"} <= held:
            return None
        with self._input_lock:
            if IS_WINDOWS and attach_thread_to_input_desktop() is None:
                raise RuntimeError("Could not attach to the input desktop")
            result = getattr(self._backend(), name)(*args, **kwargs)
        if name in {"position", "size"} and result is not None:
            return [int(result[0]), int(result[1])]
        return None

    def _capture(self, request: Dict[str, Any]) -> Tuple[Dict[str, Any], bytes]:
        try:
            monitor_id = max(0, min(64, int(request.get("monitor_id") or 0)))
        except Exception:
            monitor_id = 0
        try:
            max_width = max(0, min(7680, int(request.get("max_width") or 0)))
        except Exception:
            max_width = 0
        if IS_WINDOWS and attach_thread_to_input_desktop() is None:
            raise RuntimeError("Could not attach to the input desktop")
        import mss  # type: ignore

        with mss.mss() as sct:
            if not (0 <= monitor_id < len(sct.monitors)):
                monitor_id = 0
            shot = sct.grab(sct.monitors[monitor_id])
        width, height = int(shot.size[0]), int(shot.size[1])
        try:
            from PIL import Image  # type: ignore

            image = Image.frombytes("RGB", (width, height), shot.bgra, "raw", "BGRX")
            if max_width and image.width > max_width:
                ratio = max_width / float(image.width)
                image = image.resize((max_width, max(1, int(image.height * ratio))), Image.Resampling.BILINEAR)
            return {"ok": True, "width": image.width, "height": image.height, "mode": "RGB"}, image.tobytes()
        except ImportError:
            return {"ok": True, "width": width, "height": height, "mode": "BGRX"}, bytes(shot.bgra)


# ---------------------------------------------------------------------------
# Service (LocalSystem, session 0): keeps a helper in the console session
# ---------------------------------------------------------------------------
def _launch_helper_in_session(session_id: int, command: Sequence[str]) -> Any:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.DuplicateTokenEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.DuplicateTokenEx.restype = wintypes.BOOL
    advapi32.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    advapi32.SetTokenInformation.restype = wintypes.BOOL
    advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    advapi32.CreateProcessAsUserW.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    duplicate = wintypes.HANDLE()
    token_all_access = 0xF01FF
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), token_all_access, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # SecurityImpersonation=2, TokenPrimary=1
        if not advapi32.DuplicateTokenEx(token, _MAXIMUM_ALLOWED, None, 2, 1, ctypes.byref(duplicate)):
            raise ctypes.WinError(ctypes.get_last_error())
        session = wintypes.DWORD(int(session_id))
        if not advapi32.SetTokenInformation(duplicate, 12, ctypes.byref(session), ctypes.sizeof(session)):
            raise ctypes.WinError(ctypes.get_last_error())
        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(STARTUPINFOW)
        desktop = ctypes.create_unicode_buffer("winsta0\\default")
        startup.lpDesktop = ctypes.cast(desktop, wintypes.LPWSTR)
        info = PROCESS_INFORMATION()
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(command)))
        create_no_window = 0x08000000
        create_unicode_environment = 0x00000400
        cwd = str(Path(command[0]).parent) if command else None
        if not advapi32.CreateProcessAsUserW(
            duplicate,
            None,
            command_line,
            None,
            None,
            False,
            create_no_window | create_unicode_environment,
            None,
            cwd,
            ctypes.byref(startup),
            ctypes.byref(info),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.CloseHandle(info.hThread)
        return info.hProcess, int(info.dwProcessId)
    finally:
        if duplicate:
            kernel32.CloseHandle(duplicate)
        kernel32.CloseHandle(token)


class _HelperSupervisor:  # pragma: no cover - Windows only
    def __init__(self, allow_sid: Optional[str]) -> None:
        self.allow_sid = allow_sid
        self.stop_event = threading.Event()
        self.process: Any = None
        self.pid = 0
        self.session: Optional[int] = None
        self.failures = 0

    def _alive(self) -> bool:
        if not self.process:
            return False
        import ctypes

        return ctypes.windll.kernel32.WaitForSingleObject(self.process, 0) == 0x102  # WAIT_TIMEOUT

    def _stop_helper(self) -> None:
        if not self.process:
            return
        import ctypes

        try:
            ctypes.windll.kernel32.TerminateProcess(self.process, 0)
            ctypes.windll.kernel32.CloseHandle(self.process)
        except Exception:
            pass
        self.process, self.pid, self.session = None, 0, None

    def run(self) -> None:
        command = launcher_command() + ["--secure-desktop", "helper"]
        if self.allow_sid:
            command += ["--allow-sid", self.allow_sid]
        next_attempt = 0.0
        while not self.stop_event.wait(2.0):
            session = active_console_session_id()
            if session is None:
                self._stop_helper()
                continue
            if self._alive() and self.session == session:
                self.failures = 0
                continue
            if self.process and not self._alive():
                self.failures += 1
                LOGGER.warning("Secure desktop helper exited; restarting.")
            self._stop_helper()
            if time.monotonic() < next_attempt:
                continue
            try:
                self.process, self.pid = _launch_helper_in_session(session, command)
                self.session = session
                LOGGER.info("Started secure desktop helper pid=%s in session %s.", self.pid, session)
            except Exception as exc:
                self.failures += 1
                LOGGER.error("Could not start secure desktop helper: %s", exc)
            next_attempt = time.monotonic() + min(60.0, 2.0 * (2 ** min(self.failures, 5)))
        self._stop_helper()


def run_service(allow_sid: Optional[str]) -> int:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    class SERVICE_STATUS(ctypes.Structure):
        _fields_ = [
            ("dwServiceType", wintypes.DWORD),
            ("dwCurrentState", wintypes.DWORD),
            ("dwControlsAccepted", wintypes.DWORD),
            ("dwWin32ExitCode", wintypes.DWORD),
            ("dwServiceSpecificExitCode", wintypes.DWORD),
            ("dwCheckPoint", wintypes.DWORD),
            ("dwWaitHint", wintypes.DWORD),
        ]

    service_main_type = ctypes.WINFUNCTYPE(None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))
    handler_type = ctypes.WINFUNCTYPE(wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p)

    class SERVICE_TABLE_ENTRYW(ctypes.Structure):
        _fields_ = [("lpServiceName", wintypes.LPWSTR), ("lpServiceProc", service_main_type)]

    advapi32.RegisterServiceCtrlHandlerExW.argtypes = [wintypes.LPCWSTR, handler_type, ctypes.c_void_p]
    advapi32.RegisterServiceCtrlHandlerExW.restype = ctypes.c_void_p
    advapi32.SetServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(SERVICE_STATUS)]
    advapi32.SetServiceStatus.restype = wintypes.BOOL
    advapi32.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(SERVICE_TABLE_ENTRYW)]
    advapi32.StartServiceCtrlDispatcherW.restype = wintypes.BOOL

    supervisor = _HelperSupervisor(allow_sid)
    state: Dict[str, Any] = {"handle": None}
    status = SERVICE_STATUS()
    status.dwServiceType = 0x10  # SERVICE_WIN32_OWN_PROCESS

    def set_state(current: int, accepted: int = 0, wait_hint: int = 0) -> None:
        status.dwCurrentState = current
        status.dwControlsAccepted = accepted
        status.dwWaitHint = wait_hint
        if state["handle"]:
            advapi32.SetServiceStatus(state["handle"], ctypes.byref(status))

    def handler(control: int, _event_type: int, _event_data: Any, _context: Any) -> int:
        if control in (1, 5):  # SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN
            set_state(3, 0, 10000)  # SERVICE_STOP_PENDING
            supervisor.stop_event.set()
            return 0
        if control == 4:  # SERVICE_CONTROL_INTERROGATE
            return 0
        return 120  # ERROR_CALL_NOT_IMPLEMENTED

    handler_ref = handler_type(handler)

    def service_main(_argc: int, _argv: Any) -> None:
        state["handle"] = advapi32.RegisterServiceCtrlHandlerExW(SERVICE_NAME, handler_ref, None)
        set_state(4, 0x1 | 0x4)  # SERVICE_RUNNING; accept STOP and SHUTDOWN
        try:
            supervisor.run()
        except Exception as exc:
            LOGGER.exception("Secure desktop service failed: %s", exc)
            status.dwWin32ExitCode = 1066  # ERROR_SERVICE_SPECIFIC_ERROR
            status.dwServiceSpecificExitCode = 1
        set_state(1)  # SERVICE_STOPPED

    main_ref = service_main_type(service_main)
    table = (SERVICE_TABLE_ENTRYW * 2)()
    table[0].lpServiceName = SERVICE_NAME
    table[0].lpServiceProc = main_ref
    if not advapi32.StartServiceCtrlDispatcherW(table):
        error = ctypes.get_last_error()
        if error == 1063:  # ERROR_FAILED_SERVICE_CONTROLLER_CONNECT
            LOGGER.error("'--secure-desktop service' must be started by the Windows Service Control Manager.")
        else:
            LOGGER.error("StartServiceCtrlDispatcher failed: %s", error)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Install / uninstall / status (run elevated on the host)
# ---------------------------------------------------------------------------
def _is_admin() -> bool:  # pragma: no cover - Windows only
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _sc(*args: str) -> subprocess.CompletedProcess:  # pragma: no cover - Windows only
    return subprocess.run(
        ["sc.exe", *args],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def service_command(allow_sid: str) -> str:
    return subprocess.list2cmdline(launcher_command() + ["--secure-desktop", "service", "--allow-sid", allow_sid])


def _report(message: Any) -> None:
    """Print for an interactive prompt and log for the packaged (no console) build."""
    text = str(message or "").strip()
    if not text:
        return
    print(text)
    LOGGER.info(text)


def _enable_software_sas() -> str:  # pragma: no cover - Windows only
    """Allow services to send Ctrl+Alt+Del (needed when sign-in requires it)."""
    import winreg

    key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, _ = winreg.QueryValueEx(key, "SoftwareSASGeneration")
            current = int(current)
        except FileNotFoundError:
            current = 0
        if current in (1, 3):
            return "already enabled"
        new_value = 3 if current == 2 else 1
        winreg.SetValueEx(key, "SoftwareSASGeneration", 0, winreg.REG_DWORD, new_value)
        return f"set to {new_value}"


def install_service(allow_sid: Optional[str]) -> int:  # pragma: no cover - Windows only
    if not IS_WINDOWS:
        _report("The secure desktop helper is only needed on Windows.")
        return 1
    if not _is_admin():
        _report("Run this from an elevated (Administrator) prompt.")
        return 1
    sid = allow_sid or current_user_sid()
    if not _is_valid_sid(sid):
        _report("Could not determine the Windows account SID. Pass --allow-sid S-1-5-21-...")
        return 1
    assert sid is not None
    bin_path = service_command(sid)
    exists = _sc("query", SERVICE_NAME).returncode == 0
    if exists:
        _sc("stop", SERVICE_NAME)
        result = _sc("config", SERVICE_NAME, "binPath=", bin_path, "start=", "auto", "obj=", "LocalSystem")
    else:
        result = _sc(
            "create",
            SERVICE_NAME,
            "binPath=",
            bin_path,
            "start=",
            "auto",
            "obj=",
            "LocalSystem",
            "DisplayName=",
            SERVICE_DISPLAY_NAME,
        )
    if result.returncode != 0:
        _report(result.stdout or result.stderr)
        return result.returncode or 1
    _sc("description", SERVICE_NAME, SERVICE_DESCRIPTION)
    _sc("failure", SERVICE_NAME, "reset=", "86400", "actions=", "restart/5000/restart/5000/restart/30000")
    try:
        sas = _enable_software_sas()
    except Exception as exc:
        sas = f"not changed ({exc})"
    started = _sc("start", SERVICE_NAME)
    _report(f"Installed {SERVICE_NAME} for account {sid}.")
    _report(f"Software Ctrl+Alt+Del policy: {sas}.")
    if started.returncode != 0:
        _report(started.stdout or started.stderr)
    return 0


def uninstall_service() -> int:  # pragma: no cover - Windows only
    if not _is_admin():
        _report("Run this from an elevated (Administrator) prompt.")
        return 1
    _sc("stop", SERVICE_NAME)
    result = _sc("delete", SERVICE_NAME)
    try:
        rendezvous_path().unlink()
    except Exception:
        pass
    _report(result.stdout or result.stderr)
    return 0 if result.returncode == 0 else result.returncode


def request_elevated_install() -> Dict[str, Any]:  # pragma: no cover - Windows only
    """Show a UAC prompt on the host that installs the helper for this user."""
    if not IS_WINDOWS:
        return {"ok": False, "error": "Only needed on Windows."}
    import ctypes

    sid = current_user_sid()
    if not _is_valid_sid(sid):
        return {"ok": False, "error": "Could not determine this Windows account."}
    command = launcher_command()
    params = subprocess.list2cmdline(command[1:] + ["--secure-desktop", "install", "--allow-sid", str(sid)])
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", command[0], params, None, 0)
    if int(result) <= 32:
        return {"ok": False, "error": "Windows did not start the installer (UAC prompt declined?)."}
    return {"ok": True, "detail": "Approve the Windows prompt on this computer to finish installing."}


def _configure_file_logging() -> None:
    try:
        directory = rendezvous_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = log_path()
        if path.exists() and path.stat().st_size > 1024 * 1024:
            path.unlink()
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    except Exception:
        pass


def main(argv: Sequence[str]) -> int:
    """``--secure-desktop {install,uninstall,status,service,helper} [--allow-sid SID]``."""
    import argparse

    parser = argparse.ArgumentParser(prog="AutoYou --secure-desktop")
    parser.add_argument("command", choices=["install", "uninstall", "status", "service", "helper"])
    parser.add_argument("--allow-sid", default=None)
    args = parser.parse_args(list(argv))
    if args.allow_sid and not _is_valid_sid(args.allow_sid):
        parser.error("--allow-sid must be a Windows SID like S-1-5-21-...")
    if args.command == "status":
        print(json.dumps(secure_desktop_status(), indent=2))
        return 0
    _configure_file_logging()
    if args.command == "install":
        return install_service(args.allow_sid)
    if args.command == "uninstall":
        return uninstall_service()
    if not IS_WINDOWS:
        LOGGER.error("The secure desktop service only runs on Windows.")
        return 1
    if args.command == "service":
        return run_service(args.allow_sid)
    SecureDesktopHelper(args.allow_sid).serve_forever()
    return 0
