# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-8130e36b83c5c3e9792e9fc0

"""Remote Desktop Agent implementation module."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import base64
import json
import logging
import os
import platform
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-8130e36b83c5c3e9792e9fc0"


logger = logging.getLogger(__name__)

try:
    from autoyou_agents.shared_tools._subprocess_env import scrubbed_subprocess_env as _scrubbed_subprocess_env
except Exception:  # pragma: no cover - defensive fallback for partial runtimes.
    def _scrubbed_subprocess_env() -> Dict[str, str]:
        return dict(os.environ)


class RemoteDesktopRuntimeUnavailable(RuntimeError):
    """Raised when optional desktop automation runtime dependencies are absent."""


def _dependency_unavailable(module_name: str, exc: BaseException) -> RemoteDesktopRuntimeUnavailable:
    return RemoteDesktopRuntimeUnavailable(
        f"Desktop automation dependency '{module_name}' is unavailable. "
        "Install requirements/desktop-automation.txt, or rebuild the packaged app "
        "with the runtime_site_packages desktop automation overlay. "
        f"Original error: {exc}"
    )


def _lazy_import_mss() -> Any:
    try:
        from shared.remote_desktop_keyboard import hide_macos_dock_icon

        hide_macos_dock_icon()
        import mss  # type: ignore[import-not-found]

        return mss
    except Exception as exc:
        raise _dependency_unavailable("mss", exc) from exc


def _lazy_import_pyautogui() -> Any:
    try:
        from shared.remote_desktop_keyboard import load_pyautogui

        return load_pyautogui()
    except Exception as exc:
        raise _dependency_unavailable("pyautogui", exc) from exc


def _platform_tag() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system.startswith("win"):
        return "windows"
    if system == "linux":
        return "linux"
    return system or "unknown"


def _list_active_monitors_imagegrab() -> Optional[Dict[str, Any]]:
    platform_tag = _platform_tag()
    if platform_tag not in {"windows", "macos"}:
        return None
    try:
        from PIL import ImageGrab

        kwargs = {"all_screens": True} if platform_tag == "windows" else {}
        image = ImageGrab.grab(**kwargs)
        width = max(1, int(image.width))
        height = max(1, int(image.height))
        return {
            "status": "success",
            "platform": platform_tag,
            "capture_backend": "imagegrab",
            "monitors": [
                {
                    "id": 0,
                    "name": f"Display Capture ({width}x{height})",
                    "width": width,
                    "height": height,
                    "left": 0,
                    "top": 0,
                }
            ],
            "count": 1,
        }
    except Exception as exc:
        logger.debug("ImageGrab monitor listing fallback unavailable: %s", exc)
        return None


def _run_command(args: List[str], *, timeout_seconds: int = 8) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout_seconds)),
        env=_scrubbed_subprocess_env(),
    )


def _safe_json_loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return None


def _encode_window_id(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "win:" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_window_id(value: Any) -> Optional[Dict[str, Any]]:
    raw = str(value or "").strip()
    if not raw.startswith("win:"):
        return None
    encoded = raw[4:]
    padding = "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _window_entry(
    *,
    title: str,
    left: int,
    top: int,
    width: int,
    height: int,
    handle: Optional[Any] = None,
    xid: Optional[str] = None,
    pid: Optional[int] = None,
    process_name: Optional[str] = None,
    platform_tag: Optional[str] = None,
    index: Optional[int] = None,
) -> Dict[str, Any]:
    cleaned_title = str(title or "").strip()
    cleaned_process = str(process_name or "").strip() or None
    handle_int = _coerce_int(handle)

    if handle_int is not None:
        window_id: Any = handle_int
    else:
        payload = {
            "platform": platform_tag or _platform_tag(),
            "title": cleaned_title,
        }
        if xid:
            payload["xid"] = str(xid)
        if pid:
            payload["pid"] = int(pid)
        if cleaned_process:
            payload["process_name"] = cleaned_process
        if index is not None:
            payload["index"] = int(index)
        window_id = _encode_window_id(payload)

    return {
        "id": window_id,
        "title": cleaned_title,
        "left": int(left),
        "top": int(top),
        "width": max(1, int(width)),
        "height": max(1, int(height)),
        "process_name": cleaned_process,
        "pid": int(pid) if pid else None,
        "platform": platform_tag or _platform_tag(),
    }


def _bounds_from_entry(entry: Dict[str, Any]) -> Optional[Dict[str, int]]:
    try:
        return {
            "left": int(entry.get("left") or 0),
            "top": int(entry.get("top") or 0),
            "width": max(1, int(entry.get("width") or 0)),
            "height": max(1, int(entry.get("height") or 0)),
        }
    except Exception:
        return None


def _list_windows_pygetwindow() -> List[Dict[str, Any]]:
    try:
        from shared.remote_desktop_keyboard import ensure_x11_authority_exists

        ensure_x11_authority_exists()
        import pygetwindow as gw  # type: ignore[import-not-found]
    except Exception:
        return []

    try:
        all_windows = gw.getAllWindows()
    except Exception:
        return []

    windows: List[Dict[str, Any]] = []
    for index, window in enumerate(all_windows):
        try:
            title = str(getattr(window, "title", "") or "").strip()
            width = int(getattr(window, "width", 0) or 0)
            height = int(getattr(window, "height", 0) or 0)
            if not title or width <= 50 or height <= 50:
                continue
            windows.append(
                _window_entry(
                    title=title,
                    left=int(getattr(window, "left", 0) or 0),
                    top=int(getattr(window, "top", 0) or 0),
                    width=width,
                    height=height,
                    handle=getattr(window, "_hWnd", None),
                    platform_tag=_platform_tag(),
                    index=index,
                )
            )
        except Exception:
            continue
    return windows


def _list_windows_windows() -> List[Dict[str, Any]]:
    if not shutil.which("powershell") and not shutil.which("pwsh"):
        return []
    shell = "powershell" if shutil.which("powershell") else "pwsh"
    # from __debug_provenance_y__ import legal
    script = r"""
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class Win32Window {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
'@;
$items = New-Object System.Collections.Generic.List[object]
[Win32Window]::EnumWindows({ param($h,$l)
  if (-not [Win32Window]::IsWindowVisible($h)) { return $true }
  $len = [Win32Window]::GetWindowTextLength($h)
  if ($len -le 0) { return $true }
  $sb = New-Object System.Text.StringBuilder ($len + 1)
  [void][Win32Window]::GetWindowText($h, $sb, $sb.Capacity)
  $title = $sb.ToString()
  if ([string]::IsNullOrWhiteSpace($title)) { return $true }
  $rect = New-Object 'Win32Window+RECT'
  [void][Win32Window]::GetWindowRect($h, [ref]$rect)
  $width = $rect.Right - $rect.Left
  $height = $rect.Bottom - $rect.Top
  if ($width -le 50 -or $height -le 50) { return $true }
  $pid = 0
  [void][Win32Window]::GetWindowThreadProcessId($h, [ref]$pid)
  $processName = $null
  try { $processName = (Get-Process -Id $pid -ErrorAction Stop).ProcessName } catch {}
  $items.Add([PSCustomObject]@{
    Handle = $h.ToInt64()
    Title = $title
    Left = $rect.Left
    Top = $rect.Top
    Width = $width
    Height = $height
    Pid = $pid
    ProcessName = $processName
  }) | Out-Null
  return $true
}, [IntPtr]::Zero) | Out-Null
$items | ConvertTo-Json -Depth 3
"""
    completed = _run_command([shell, "-NoProfile", "-Command", script], timeout_seconds=8)
    if completed.returncode != 0:
        return []
    payload = _safe_json_loads(completed.stdout)
    if payload is None:
        return []
    items = payload if isinstance(payload, list) else [payload]
    windows: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("Title") or "").strip()
        if not title:
            continue
        windows.append(
            _window_entry(
                title=title,
                left=int(item.get("Left") or 0),
                top=int(item.get("Top") or 0),
                width=int(item.get("Width") or 0),
                height=int(item.get("Height") or 0),
                handle=item.get("Handle"),
                pid=_coerce_int(item.get("Pid")),
                process_name=str(item.get("ProcessName") or "").strip() or None,
                platform_tag="windows",
            )
        )
    return windows


def _list_windows_macos() -> List[Dict[str, Any]]:
    try:
        from shared.remote_desktop_keyboard import hide_macos_dock_icon

        hide_macos_dock_icon()
    except Exception as exc:
        logger.debug("Could not hide the macOS Python Dock icon: %s", exc)
    try:
        import Quartz  # type: ignore[import-not-found]

        window_info = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
        )
        windows: List[Dict[str, Any]] = []
        for item in window_info or []:
            try:
                if int(item.get("kCGWindowLayer", 0) or 0) != 0:
                    continue
                bounds = item.get("kCGWindowBounds") or {}
                width = int(float(bounds.get("Width") or 0))
                height = int(float(bounds.get("Height") or 0))
                if width <= 50 or height <= 50:
                    continue
                title = str(item.get("kCGWindowName") or item.get("kCGWindowOwnerName") or "").strip()
                if not title:
                    continue
                windows.append(
                    _window_entry(
                        title=title,
                        left=int(float(bounds.get("X") or 0)),
                        top=int(float(bounds.get("Y") or 0)),
                        width=width,
                        height=height,
                        handle=item.get("kCGWindowNumber"),
                        pid=_coerce_int(item.get("kCGWindowOwnerPID")),
                        process_name=str(item.get("kCGWindowOwnerName") or "").strip() or None,
                        platform_tag="macos",
                    )
                )
            except Exception:
                continue
        if windows:
            return windows
    except Exception as exc:
        logger.debug("Quartz window listing unavailable: %s", exc)

    if not shutil.which("osascript"):
        return []
    script = r'''
set output to ""
tell application "System Events"
  repeat with proc in (application processes whose visible is true)
    set procName to name of proc as text
    try
      set windowCount to count of windows of proc
      repeat with windowIndex from 1 to windowCount
        try
          tell window windowIndex of proc
            set windowTitle to name as text
            set windowPos to position
            set windowSize to size
          end tell
          if windowTitle is not "" then
            set output to output & procName & tab & (windowIndex as text) & tab & windowTitle & tab & ((item 1 of windowPos) as text) & tab & ((item 2 of windowPos) as text) & tab & ((item 1 of windowSize) as text) & tab & ((item 2 of windowSize) as text) & linefeed
          end if
        end try
      end repeat
    end try
  end repeat
end tell
return output
'''
    completed = _run_command(["osascript", "-e", script], timeout_seconds=2)
    if completed.returncode != 0:
        return []

    windows: List[Dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        process_name, index_s, title, left_s, top_s, width_s, height_s = parts[:7]
        try:
            width = int(float(width_s))
            height = int(float(height_s))
            if not title.strip() or width <= 50 or height <= 50:
                continue
            windows.append(
                _window_entry(
                    title=title,
                    left=int(float(left_s)),
                    top=int(float(top_s)),
                    width=width,
                    height=height,
                    process_name=process_name,
                    platform_tag="macos",
                    index=int(index_s),
                )
            )
        except Exception:
            continue
    return windows


def _list_windows_linux() -> List[Dict[str, Any]]:
    if not shutil.which("wmctrl"):
        return []
    completed = _run_command(["wmctrl", "-lG", "-p"], timeout_seconds=8)
    if completed.returncode != 0:
        return []

    windows: List[Dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        xid, _desktop, pid_s, left_s, top_s, width_s, height_s, _host, title = parts
        try:
            width = int(width_s)
            height = int(height_s)
            if not title.strip() or width <= 50 or height <= 50:
                continue
            pid = _coerce_int(pid_s)
            process_name = None
            if pid:
                try:
                    import psutil

                    process_name = psutil.Process(pid).name()
                except Exception:
                    process_name = None
            windows.append(
                _window_entry(
                    title=title,
                    left=int(left_s),
                    top=int(top_s),
                    width=width,
                    height=height,
                    xid=xid,
                    pid=pid,
                    process_name=process_name,
                    platform_tag="linux",
                )
            )
        except Exception:
            continue
    return windows


def _list_windows_native() -> List[Dict[str, Any]]:
    try:
        current = _platform_tag()
        if current == "windows":
            return _list_windows_windows()
        if current == "macos":
            return _list_windows_macos()
        if current == "linux":
            return _list_windows_linux()
    except Exception as exc:
        logger.debug("Native window listing unavailable: %s", exc)
    return []


def _matches_target_id(entry: Dict[str, Any], target_id: Any) -> bool:
    raw = str(target_id or "").strip()
    if not raw:
        return False
    if str(entry.get("id")) == raw:
        return True
    target_int = _coerce_int(target_id)
    entry_int = _coerce_int(entry.get("id"))
    return target_int is not None and entry_int is not None and target_int == entry_int


def _find_window_entry(target_id: Any) -> Optional[Dict[str, Any]]:
    for entry in _list_windows_pygetwindow() + _list_windows_native():
        if _matches_target_id(entry, target_id):
            return entry

    payload = _decode_window_id(target_id)
    if not payload:
        return None
    desired_title = str(payload.get("title") or "").strip().lower()
    desired_process = str(payload.get("process_name") or "").strip().lower()
    desired_xid = str(payload.get("xid") or "").strip().lower()
    desired_pid = _coerce_int(payload.get("pid"))
    for entry in _list_windows_pygetwindow() + _list_windows_native():
        if desired_xid:
            entry_payload = _decode_window_id(entry.get("id"))
            if entry_payload and str(entry_payload.get("xid") or "").strip().lower() == desired_xid:
                return entry
        if desired_pid and _coerce_int(entry.get("pid")) == desired_pid:
            return entry
        if desired_title and desired_title == str(entry.get("title") or "").strip().lower():
            if not desired_process or desired_process == str(entry.get("process_name") or "").strip().lower():
                return entry
    return None


def get_window_bounds(target_id: Any) -> Optional[Dict[str, int]]:
    """Return MSS-compatible bounds for a selected window target."""
    entry = _find_window_entry(target_id)
    return _bounds_from_entry(entry) if entry else None


def activate_window(target_id: Any) -> bool:
    """Best-effort focus for a selected remote desktop window target."""
    entry = _find_window_entry(target_id)
    if not entry:
        return False

    current = str(entry.get("platform") or _platform_tag())
    title = str(entry.get("title") or "")
    process_name = str(entry.get("process_name") or "")
    entry_id = entry.get("id")

    try:
        if current == "windows":
            handle = _coerce_int(entry_id)
            if handle is None:
                return False
            shell = "powershell" if shutil.which("powershell") else "pwsh"
            if not shutil.which(shell):
                return False
            script = (
                "Add-Type @'\n"
                "using System;\n"
                "using System.Runtime.InteropServices;\n"
                "public static class Win32Focus { [DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr hWnd); }\n"
                "'@;\n"
                f"[void][Win32Focus]::SetForegroundWindow([IntPtr]{handle})"
            )
            return _run_command([shell, "-NoProfile", "-Command", script], timeout_seconds=5).returncode == 0

        if current == "macos" and process_name and shutil.which("osascript"):
            script = (
                'tell application "System Events"\n'
                f"  if exists process {json.dumps(process_name)} then set frontmost of process {json.dumps(process_name)} to true\n"
                "end tell"
            )
            return _run_command(["osascript", "-e", script], timeout_seconds=5).returncode == 0

        if current == "linux" and shutil.which("wmctrl"):
            payload = _decode_window_id(entry_id)
            xid = str((payload or {}).get("xid") or "").strip()
            if xid:
                return _run_command(["wmctrl", "-ia", xid], timeout_seconds=5).returncode == 0
            if title:
                return _run_command(["wmctrl", "-a", title], timeout_seconds=5).returncode == 0
    except Exception:
        return False

    try:
        from shared.remote_desktop_keyboard import ensure_x11_authority_exists

        ensure_x11_authority_exists()
        import pygetwindow as gw  # type: ignore[import-not-found]

        for window in gw.getAllWindows():
            if str(getattr(window, "title", "") or "").strip() != title:
                continue
            try:
                if getattr(window, "isMinimized", False):
                    window.restore()
                window.activate()
                return True
            except Exception:
                return False
    except Exception:
        return False
    return False


# Agent custom diagnostic tools

def list_active_monitors() -> Dict[str, Any]:
    """List all attached display monitors, their resolutions, and positioning offsets.
    
    Returns:
        Dict[str, Any]: Status and list of monitor configurations.
    """
    if _platform_tag() == "macos":
        try:
            from shared.macos_screen_capture import (
                get_macos_capture_diagnostics,
                list_macos_monitors,
            )

            mac_monitors = list_macos_monitors()
            diagnostics = get_macos_capture_diagnostics()
            if mac_monitors:
                return {
                    "status": "success",
                    "platform": "macos",
                    "monitors": mac_monitors,
                    "count": len(mac_monitors),
                    "diagnostics": diagnostics,
                }
        except Exception as exc:
            logger.debug("macOS online display enumeration unavailable: %s", exc)

    try:
        mss = _lazy_import_mss()
        with mss.mss() as sct:
            monitors = []
            for i, m in enumerate(sct.monitors):
                if int(m.get("width") or 0) <= 0 or int(m.get("height") or 0) <= 0:
                    continue
                # monitor[0] is the virtual union of all screens.
                name = "Virtual Screen (All Displays)" if i == 0 else f"Monitor {i}"
                monitors.append({
                    "id": i,
                    "name": f"{name} ({m['width']}x{m['height']})",
                    "width": m["width"],
                    "height": m["height"],
                    "left": m["left"],
                    "top": m["top"]
                })
            if monitors:
                return {"status": "success", "platform": _platform_tag(), "monitors": monitors, "count": len(monitors)}
    except Exception as e:
        fallback = _list_active_monitors_imagegrab()
        if fallback is not None:
            logger.warning("MSS monitor listing unavailable; using ImageGrab fallback: %s", e)
            return fallback
        logger.error("Failed to list active monitors: %s", e)
        return {
            "status": "error",
            "message": f"Failed to list monitors: {e}",
            "monitors": [],
            "count": 0,
        }

    fallback = _list_active_monitors_imagegrab()
    if fallback is not None:
        return fallback
    return {
        "status": "error",
        "platform": _platform_tag(),
        "message": "No active displays with valid bounds were detected.",
        "monitors": [],
        "count": 0,
    }


def list_running_windows() -> Dict[str, Any]:
    """List all visible application window titles, handles, and dimensions on the desktop.
    
    Returns:
        Dict[str, Any]: Status and list of active application windows.
    """
    try:
        from autoyou_agents.shared_tools.desktop_accessibility import list_accessible_windows

        accessible = list_accessible_windows()
        if accessible.get("status") == "headless":
            return accessible
        if accessible.get("status") == "success" and accessible.get("windows"):
            normalized = []
            for entry in accessible["windows"]:
                b = entry.get("bounds") or {}
                normalized.append(
                    _window_entry(
                        title=str(entry.get("title") or ""),
                        left=int(float(b.get("x") or 0)),
                        top=int(float(b.get("y") or 0)),
                        width=max(1, int(float(b.get("width") or 1))),
                        height=max(1, int(float(b.get("height") or 1))),
                        handle=entry.get("window_id") if isinstance(entry.get("window_id"), int) else None,
                        xid=str(entry.get("window_id")) if isinstance(entry.get("window_id"), str) else None,
                        pid=entry.get("pid"),
                        process_name=entry.get("app_name") or entry.get("process_name"),
                        platform_tag=entry.get("platform") or _platform_tag(),
                    )
                )
            return {
                "status": "success",
                "platform": accessible.get("platform") or _platform_tag(),
                "backend": accessible.get("backend"),
                "windows": normalized,
                "count": len(normalized),
            }
    except Exception as exc:
        logger.debug("Accessible window listing fell back to legacy enumerator: %s", exc)

    try:
        seen: set[str] = set()
        windows: List[Dict[str, Any]] = []
        discovered = _list_windows_pygetwindow()
        if not discovered:
            discovered = _list_windows_native()
        for entry in discovered:
            key = str(entry.get("id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            windows.append(entry)
        return {
            "status": "success",
            "platform": _platform_tag(),
            "windows": windows,
            "count": len(windows),
        }
    except Exception as e:
        logger.error("Failed to list running windows: %s", e)
        return {
            "status": "error",
            "message": f"Failed to list running windows: {e}",
            "windows": [],
            "count": 0,
        }


def desktop_automation_status() -> Dict[str, Any]:
    """Inspect the host OS (macOS, Windows, Linux, WSL2, or Docker) and active background UI automation backend.

    Returns:
        Dict[str, Any]: Platform capabilities, active accessibility backend, and display status.
    """
    from autoyou_agents.shared_tools.desktop_accessibility import detect_desktop_environment

    return detect_desktop_environment()


def snapshot_window_ui(
    app_name: str = "",
    window_title: str = "",
    max_elements: int = 80,
) -> Dict[str, Any]:
    """Capture a compact accessibility UI tree with snapshot-bound element tokens for a desktop window.

    Args:
        app_name: Optional application or process name to match (for example ``Safari``, ``Notes``, ``Code``).
        window_title: Optional substring of the window title to match.
        max_elements: Maximum number of interactive UI elements to return (10-200).

    Returns:
        Dict[str, Any]: Snapshot metadata, Markdown accessibility tree, and element tokens.
    """
    from autoyou_agents.shared_tools.desktop_accessibility import snapshot_window_accessibility

    return snapshot_window_accessibility(
        app_name=app_name,
        window_title=window_title,
        max_elements=max_elements,
    )


def click_ui_element(
    element_token: str,
    double_click: bool = False,
    right_click: bool = False,
) -> Dict[str, Any]:
    """Click or invoke a snapshot-bound UI element token returned by ``snapshot_window_ui``.

    Args:
        element_token: Snapshot-bound token in ``<snapshot_id>:<index>`` form.
        double_click: Whether to double-click the element.
        right_click: Whether to secondary/right-click the element.

    Returns:
        Dict[str, Any]: Action outcome and backend used.
    """
    from autoyou_agents.shared_tools.desktop_accessibility import click_accessible_element

    return click_accessible_element(
        element_token,
        double_click=double_click,
        right_click=right_click,
    )


def type_into_ui_element(
    element_token: str,
    text: str,
    submit: bool = False,
) -> Dict[str, Any]:
    """Enter text into a snapshot-bound UI input token returned by ``snapshot_window_ui``.

    Args:
        element_token: Snapshot-bound token in ``<snapshot_id>:<index>`` form.
        text: Text to write into the control.
        submit: Whether to press Enter/Return after writing text.

    Returns:
        Dict[str, Any]: Outcome of the text entry operation.
    """
    from autoyou_agents.shared_tools.desktop_accessibility import type_into_accessible_element

    return type_into_accessible_element(element_token, text, submit=submit)


def press_ui_hotkey(
    keys: List[str],
    app_name: str = "",
) -> Dict[str, Any]:
    """Press a keyboard shortcut (for example ``["cmd", "l"]`` or ``["ctrl", "s"]``).

    Args:
        keys: Sequence of key names to press together (or a single key such as ``["return"]``).
        app_name: Optional application name hint.

    Returns:
        Dict[str, Any]: Outcome of the hotkey press.
    """
    from autoyou_agents.shared_tools.desktop_accessibility import press_accessible_hotkey

    return press_accessible_hotkey(keys, app_name=app_name)


# ─── Factory and Callbacks ───────────────────────────────────────────────────

def get_tools() -> List[Any]:
    """Get list of custom tools for the Remote Desktop agent."""
    return [
        get_current_datetime,
        desktop_automation_status,
        list_active_monitors,
        list_running_windows,
        snapshot_window_ui,
        click_ui_element,
        type_into_ui_element,
        press_ui_hotkey,
    ]


async def _rd_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    """Inject host clock into model turns."""
    from autoyou_agents.shared_tools.datetime_tool import inject_realtime_datetime_into_request
    inject_realtime_datetime_into_request(llm_request)
    return None


def create_remote_desktop_agent(model_config: Any) -> Any:
    """Factory function to build the Remote Desktop Agent."""
    from google.adk.agents import Agent
    from autoyou_agents.remote_desktop_agent.prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=get_tools(),
        before_model_callback=_rd_before_model_callback,
    )
