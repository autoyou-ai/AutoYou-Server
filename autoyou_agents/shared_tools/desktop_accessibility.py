# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Cross-platform snapshot-bound desktop accessibility and background UI control.

Implements the snapshot -> inspect -> act-by-token -> verify-fresh-state
computer-use pattern natively inside AutoYou-Server so small local models such
as ``qwen3.5:4b`` can control desktop apps reliably across:

- **macOS**: optional ``cua-driver`` background accelerator, or native
  Accessibility (``AXUIElement`` / ``CGWindowListCopyWindowInfo``) via Swift.
- **Windows**: optional ``cua-driver``, or native PowerShell + .NET
  ``System.Windows.Automation`` (UIA) + Win32 window enumeration.
- **WSL2**: Linux display inspection when present plus seamless Windows-host
  UIA bridging via ``powershell.exe`` when targeting host desktop apps.
- **Linux**: optional ``cua-driver``, AT-SPI2 (``pyatspi``) + ``wmctrl`` /
  ``xdotool`` when an X11/Wayland session is active.
- **Docker / headless**: graceful capability reporting (never crashes on
  missing ``$DISPLAY``), directing web workflows to headless Playwright.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import json
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ._subprocess_env import scrubbed_subprocess_env as _scrubbed_subprocess_env

LOGGER = logging.getLogger(__name__)

_SNAPSHOT_CACHE_LOCK = threading.Lock()
_SNAPSHOT_CACHE: Dict[str, Dict[str, Any]] = {}
_SNAPSHOT_TTL_SECONDS = 300.0
_MAX_CACHED_SNAPSHOTS = 24
_CUA_SESSION_ID = "autoyou-server-cua"


def _run_cmd(
    args: Sequence[str],
    *,
    timeout_seconds: float = 12.0,
    stdin_text: Optional[str] = None,
) -> subprocess.CompletedProcess[str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        return subprocess.run(
            list(args),
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_seconds)),
            env=_scrubbed_subprocess_env(),
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=124,
            stdout="",
            stderr=f"Timed out after {timeout_seconds:.1f}s",
        )
    except OSError as exc:
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=127,
            stdout="",
            stderr=str(exc),
        )


def _is_docker_container() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore").lower()
        return any(token in cgroup for token in ("docker", "containerd", "kubepods", "podman"))
    except OSError:
        return False


def _is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    if platform.system().lower() != "linux":
        return False
    try:
        version = Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
        return "microsoft" in version or "wsl" in version
    except OSError:
        return "microsoft" in platform.release().lower()


def find_cua_driver_binary() -> Optional[str]:
    """Return path to an installed ``cua-driver`` CLI binary, if enabled and present."""
    if str(os.getenv("AUTOYOU_USE_CUA_DRIVER", "1") or "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    explicit = str(os.getenv("AUTOYOU_CUA_DRIVER_PATH", "") or "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("cua-driver")
    if found:
        return found
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / "cua-driver",
        Path("/usr/local/bin/cua-driver"),
        Path("/opt/homebrew/bin/cua-driver"),
        Path("/Applications/CuaDriver.app/Contents/MacOS/cua-driver"),
    ]
    if os.name == "nt":
        local_app = Path(os.environ.get("LOCALAPPDATA") or home)
        candidates.extend([
            local_app / "cua-driver" / "cua-driver.exe",
            local_app / "Programs" / "cua-driver" / "cua-driver.exe",
        ])
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _windows_powershell_binary(*, allow_wsl_host: bool = True) -> Optional[str]:
    system = platform.system().lower()
    if system.startswith("win"):
        return shutil.which("powershell") or shutil.which("pwsh")
    if allow_wsl_host and _is_wsl():
        return shutil.which("powershell.exe") or (
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            if Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe").is_file()
            else None
        )
    return None


def detect_desktop_environment() -> Dict[str, Any]:
    """Inspect host OS, container/WSL state, display availability, and active CUA backend."""
    system = platform.system().lower()
    is_docker = _is_docker_container()
    is_wsl = _is_wsl()
    cua_bin = find_cua_driver_binary()
    headless_env = str(os.getenv("AUTOYOU_HEADLESS", "") or "").strip().lower() in {"1", "true", "yes"}

    if system == "darwin":
        platform_tag = "macos"
        display_available = not headless_env
    elif system.startswith("win"):
        platform_tag = "windows"
        display_available = not headless_env
    elif is_wsl:
        platform_tag = "wsl2"
        has_linux_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        has_win_bridge = bool(_windows_powershell_binary(allow_wsl_host=True))
        display_available = (has_linux_display or has_win_bridge) and not headless_env
    elif system == "linux":
        platform_tag = "docker" if is_docker else "linux"
        display_available = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and not headless_env
    else:
        platform_tag = system or "unknown"
        display_available = False

    if not display_available:
        backend = "headless-docker" if is_docker else "headless"
        background_supported = False
        hint = (
            "Headless environment without a desktop display. Use internet_agent Playwright "
            "browser tools for web tasks; native desktop UI automation requires a desktop session."
        )
    elif cua_bin:
        backend = "cua-driver"
        background_supported = True
        hint = "Using installed cua-driver for background snapshot-bound desktop control without stealing cursor focus."
    elif platform_tag == "macos":
        backend = "macos-ax"
        background_supported = True
        hint = (
            "Using native macOS Accessibility (AXUIElement) + CoreGraphics window inspection. "
            "Grant Accessibility in System Settings -> Privacy & Security -> Accessibility for full background UI actions."
        )
    elif platform_tag == "windows":
        backend = "windows-uia"
        background_supported = True
        hint = "Using native Windows UI Automation (System.Windows.Automation) for semantic window and element control."
    elif platform_tag == "wsl2":
        backend = "wsl-windows-uia" if _windows_powershell_binary(allow_wsl_host=True) else "linux-x11"
        background_supported = backend == "wsl-windows-uia"
        hint = "Using WSL2 -> Windows host UI Automation bridge for desktop windows."
    else:
        has_atspi = False
        try:
            import pyatspi  # type: ignore[import-not-found]  # noqa: F401
            has_atspi = True
        except Exception:
            has_atspi = False
        backend = "linux-atspi" if has_atspi else "linux-x11"
        background_supported = has_atspi
        hint = (
            "Using Linux AT-SPI2 accessibility tree."
            if has_atspi
            else "Using Linux X11 window tools (wmctrl/xdotool). Install python3-pyatspi or cua-driver for semantic UI trees."
        )

    return {
        "status": "success",
        "platform": platform_tag,
        "is_docker": is_docker,
        "is_wsl": is_wsl,
        "display_available": display_available,
        "backend": backend,
        "cua_driver_path": cua_bin or "",
        "background_control_supported": background_supported,
        "browser_automation": "playwright",
        "hint": hint,
    }


def _cua_call(tool: str, arguments: Dict[str, Any], *, timeout_seconds: float = 15.0) -> Optional[Dict[str, Any]]:
    cua_bin = find_cua_driver_binary()
    if not cua_bin:
        return None
    payload = dict(arguments)
    if tool not in {"list_windows", "list_apps", "check_permissions", "health_report", "get_screen_size"}:
        payload.setdefault("session", _CUA_SESSION_ID)
    completed = _run_cmd([cua_bin, "call", tool, json.dumps(payload)], timeout_seconds=timeout_seconds)
    if completed.returncode != 0:
        LOGGER.debug("cua-driver call %s failed (%s): %s", tool, completed.returncode, (completed.stderr or completed.stdout)[:240])
        return None
    try:
        parsed = json.loads(completed.stdout)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return None


def _store_snapshot(snapshot_id: str, payload: Dict[str, Any]) -> None:
    now = time.monotonic()
    entry = dict(payload)
    entry["_created_at"] = now
    with _SNAPSHOT_CACHE_LOCK:
        stale = [k for k, v in _SNAPSHOT_CACHE.items() if now - float(v.get("_created_at") or 0) > _SNAPSHOT_TTL_SECONDS]
        for key in stale:
            _SNAPSHOT_CACHE.pop(key, None)
        if len(_SNAPSHOT_CACHE) >= _MAX_CACHED_SNAPSHOTS:
            oldest = min(_SNAPSHOT_CACHE.items(), key=lambda item: float(item[1].get("_created_at") or 0))[0]
            _SNAPSHOT_CACHE.pop(oldest, None)
        _SNAPSHOT_CACHE[snapshot_id] = entry


def _lookup_snapshot_element(element_token: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    token = str(element_token or "").strip()
    if ":" not in token:
        return None, None
    snap_id, raw_idx = token.rsplit(":", 1)
    try:
        idx = int(raw_idx)
    except ValueError:
        return None, None
    with _SNAPSHOT_CACHE_LOCK:
        snap = _SNAPSHOT_CACHE.get(snap_id)
    if not isinstance(snap, dict):
        return None, None
    for el in snap.get("elements") or []:
        if int(el.get("index", -1)) == idx:
            return snap, el
    return snap, None


def _format_elements_markdown(window_info: Dict[str, Any], elements: List[Dict[str, Any]]) -> str:
    app_name = str(window_info.get("app_name") or window_info.get("process_name") or "Window").strip()
    title = str(window_info.get("title") or "").strip()
    header = f'Window: {app_name} — "{title}"' if title else f"Window: {app_name}"
    bounds = window_info.get("bounds") or {}
    if isinstance(bounds, dict) and bounds.get("width"):
        header += f" ({int(bounds.get('width', 0))}x{int(bounds.get('height', 0))} at {int(bounds.get('x', 0))},{int(bounds.get('y', 0))})"
    lines = [header]
    for el in elements:
        idx = el.get("index")
        role = str(el.get("role") or "Element").strip()
        label = str(el.get("label") or el.get("title") or "").strip()
        value = str(el.get("value") or "").strip()
        token = str(el.get("token") or "").strip()
        parts = [f"- [{idx}] {role}"]
        if label:
            parts.append(f'"{label[:120]}"')
        if value and value != label:
            parts.append(f'= "{value[:100]}"')
        if token:
            parts.append(f"(token={token})")
        lines.append(" ".join(parts))
    return "\n".join(lines)


# ─── Native macOS Quartz + AXUIElement Helpers ───────────────────────────────

_MACOS_LIST_WINDOWS_SWIFT = r'''
import CoreGraphics
import Foundation

let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
let raw = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] ?? []
var out: [[String: Any]] = []
for w in raw {
    let layer = (w[kCGWindowLayer as String] as? Int) ?? 0
    if layer != 0 { continue }
    let owner = (w[kCGWindowOwnerName as String] as? String) ?? ""
    let title = (w[kCGWindowName as String] as? String) ?? ""
    let pid = (w[kCGWindowOwnerPID as String] as? Int) ?? 0
    let wid = (w[kCGWindowNumber as String] as? Int) ?? 0
    let b = (w[kCGWindowBounds as String] as? [String: Any]) ?? [:]
    let width = Int((b["Width"] as? Double) ?? 0)
    let height = Int((b["Height"] as? Double) ?? 0)
    if width < 60 || height < 40 { continue }
    out.append([
        "app_name": owner,
        "process_name": owner,
        "title": title,
        "pid": pid,
        "window_id": wid,
        "bounds": [
            "x": Int((b["X"] as? Double) ?? 0),
            "y": Int((b["Y"] as? Double) ?? 0),
            "width": width,
            "height": height,
        ],
        "platform": "macos",
    ])
}
let data = try JSONSerialization.data(withJSONObject: out)
print(String(data: data, encoding: .utf8)!)
'''

_MACOS_AX_SNAPSHOT_SWIFT = r'''
import ApplicationServices
import CoreGraphics
import Foundation

let args = CommandLine.arguments
let targetPid = Int32(args.count > 1 ? (Int(args[1]) ?? 0) : 0)
let maxElements = args.count > 2 ? (Int(args[2]) ?? 80) : 80
let actionMode = args.count > 3 ? args[3] : "snapshot"
let targetPath = args.count > 4 ? args[4] : ""
let textPayload = args.count > 5 ? args[5] : ""

if targetPid <= 0 {
    fputs("{\"status\":\"error\",\"message\":\"Invalid pid\"}\n", stderr)
    exit(1)
}

func axString(_ el: AXUIElement, _ attr: String) -> String {
    var val: CFTypeRef?
    let err = AXUIElementCopyAttributeValue(el, attr as CFString, &val)
    if err != .success || val == nil { return "" }
    if let s = val as? String { return s }
    if let n = val as? NSNumber { return n.stringValue }
    return ""
}

func axBounds(_ el: AXUIElement) -> [String: Int]? {
    var posVal: CFTypeRef?
    var sizeVal: CFTypeRef?
    guard AXUIElementCopyAttributeValue(el, kAXPositionAttribute as CFString, &posVal) == .success,
          AXUIElementCopyAttributeValue(el, kAXSizeAttribute as CFString, &sizeVal) == .success,
          let pRef = posVal, let sRef = sizeVal else { return nil }
    var pt = CGPoint.zero
    var sz = CGSize.zero
    AXValueGetValue(pRef as! AXValue, .cgPoint, &pt)
    AXValueGetValue(sRef as! AXValue, .cgSize, &sz)
    if sz.width <= 0 || sz.height <= 0 { return nil }
    return ["x": Int(pt.x), "y": Int(pt.y), "width": Int(sz.width), "height": Int(sz.height)]
}

func axChildren(_ el: AXUIElement) -> [AXUIElement] {
    var val: CFTypeRef?
    if AXUIElementCopyAttributeValue(el, kAXChildrenAttribute as CFString, &val) == .success,
       let arr = val as? [AXUIElement] {
        return arr
    }
    return []
}

let appEl = AXUIElementCreateApplication(targetPid)
var winRef: CFTypeRef?
var rootEl: AXUIElement = appEl
if AXUIElementCopyAttributeValue(appEl, kAXFocusedWindowAttribute as CFString, &winRef) == .success, let w = winRef {
    rootEl = w as! AXUIElement
} else if AXUIElementCopyAttributeValue(appEl, kAXWindowsAttribute as CFString, &winRef) == .success,
          let arr = winRef as? [AXUIElement], let first = arr.first {
    rootEl = first
}

if actionMode == "press" || actionMode == "set_value" {
    let steps = targetPath.split(separator: ".").compactMap { Int($0) }
    var cur = rootEl
    for step in steps {
        let kids = axChildren(cur)
        if step < 0 || step >= kids.count {
            print("{\"status\":\"error\",\"message\":\"Element path is stale; take a fresh snapshot\"}")
            exit(0)
        }
        cur = kids[step]
    }
    if actionMode == "press" {
        let err = AXUIElementPerformAction(cur, kAXPressAction as CFString)
        if err == .success {
            print("{\"status\":\"success\",\"action\":\"AXPress\"}")
        } else {
            let b = axBounds(cur)
            let obj: [String: Any] = ["status": "fallback_needed", "ax_error": Int(err.rawValue), "bounds": b ?? [:]]
            let data = try JSONSerialization.data(withJSONObject: obj)
            print(String(data: data, encoding: .utf8)!)
        }
    } else {
        AXUIElementSetAttributeValue(cur, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        let err = AXUIElementSetAttributeValue(cur, kAXValueAttribute as CFString, textPayload as CFString)
        if err == .success {
            print("{\"status\":\"success\",\"action\":\"AXSetValue\"}")
        } else {
            let b = axBounds(cur)
            let obj: [String: Any] = ["status": "fallback_needed", "ax_error": Int(err.rawValue), "bounds": b ?? [:]]
            let data = try JSONSerialization.data(withJSONObject: obj)
            print(String(data: data, encoding: .utf8)!)
        }
    }
    exit(0)
}

let interestingRoles: Set<String> = [
    "AXButton", "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox",
    "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXMenuItem",
    "AXLink", "AXTab", "AXRow", "AXStaticText", "AXHeading", "AXSlider", "AXSwitch"
]

var items: [[String: Any]] = []
var queue: [(AXUIElement, String, Int)] = [(rootEl, "", 0)]
var visited = 0

while !queue.isEmpty && items.count < maxElements && visited < 1200 {
    let (node, path, depth) = queue.removeFirst()
    visited += 1
    let role = axString(node, kAXRoleAttribute as String)
    let title = axString(node, kAXTitleAttribute as String)
    let desc = axString(node, kAXDescriptionAttribute as String)
    let val = axString(node, kAXValueAttribute as String)
    let label = !title.isEmpty ? title : (!desc.isEmpty ? desc : (role == "AXStaticText" ? val : ""))
    if interestingRoles.contains(role) && (!label.isEmpty || role == "AXTextField" || role == "AXTextArea" || role == "AXSearchField") {
        var entry: [String: Any] = [
            "index": items.count + 1,
            "role": role,
            "label": String(label.prefix(160)),
            "value": String(val.prefix(160)),
            "path": path,
        ]
        if let b = axBounds(node) { entry["bounds"] = b }
        items.append(entry)
    }
    if depth < 8 {
        let kids = axChildren(node)
        for (i, kid) in kids.prefix(60).enumerated() {
            let childPath = path.isEmpty ? "\(i)" : "\(path).\(i)"
            queue.append((kid, childPath, depth + 1))
        }
    }
}

let trusted = AXIsProcessTrusted()
let result: [String: Any] = [
    "status": "success",
    "trusted": trusted,
    "window_title": axString(rootEl, kAXTitleAttribute as String),
    "elements": items,
]
let data = try JSONSerialization.data(withJSONObject: result)
print(String(data: data, encoding: .utf8)!)
'''


def _list_windows_macos_native() -> List[Dict[str, Any]]:
    if not shutil.which("swift"):
        return []
    completed = _run_cmd(["swift", "-e", _MACOS_LIST_WINDOWS_SWIFT], timeout_seconds=12)
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        parsed = json.loads(completed.stdout)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


# ─── Native Windows / WSL2 UI Automation (UIA) Helpers ───────────────────────

_WINDOWS_LIST_WINDOWS_PS = r'''
Add-Type @"
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
public static class AutoYouWinList {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public static List<object> List() {
    var list = new List<object>();
    EnumWindows((h, l) => {
      if (!IsWindowVisible(h)) return true;
      var sb = new StringBuilder(512);
      GetWindowText(h, sb, 512);
      string title = sb.ToString().Trim();
      if (string.IsNullOrEmpty(title)) return true;
      RECT r;
      if (!GetWindowRect(h, out r)) return true;
      int w = r.Right - r.Left, ht = r.Bottom - r.Top;
      if (w < 60 || ht < 40) return true;
      uint pid = 0; GetWindowThreadProcessId(h, out pid);
      string proc = "";
      try { proc = Process.GetProcessById((int)pid).ProcessName; } catch {}
      list.Add(new {
        app_name = proc,
        process_name = proc,
        title = title,
        pid = (int)pid,
        window_id = (long)h,
        bounds = new { x = r.Left, y = r.Top, width = w, height = ht },
        platform = "windows"
      });
      return true;
    }, IntPtr.Zero);
    return list;
  }
}
"@
[AutoYouWinList]::List() | ConvertTo-Json -Depth 4 -Compress
'''


def _list_windows_windows_native() -> List[Dict[str, Any]]:
    ps = _windows_powershell_binary(allow_wsl_host=True)
    if not ps:
        return []
    completed = _run_cmd([ps, "-NoProfile", "-Command", _WINDOWS_LIST_WINDOWS_PS], timeout_seconds=12)
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        parsed = json.loads(completed.stdout)
        if isinstance(parsed, dict):
            return [parsed]
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _snapshot_windows_uia(hwnd: int, max_elements: int = 80) -> Dict[str, Any]:
    ps = _windows_powershell_binary(allow_wsl_host=True)
    if not ps:
        return {"status": "error", "message": "PowerShell UI Automation bridge is unavailable."}
    script = f'''
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{int(hwnd)})
if ($null -eq $root) {{ Write-Output '{{"status":"error","message":"Window handle not found"}}'; exit 0 }}
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$queue = New-Object System.Collections.Queue
$queue.Enqueue(@($root, 0))
$items = New-Object System.Collections.ArrayList
$visited = 0
$interesting = @("Button","Edit","Text","MenuItem","CheckBox","RadioButton","ComboBox","TabItem","Hyperlink","ListItem","DataItem","Document")
while ($queue.Count -gt 0 -and $items.Count -lt {int(max_elements)} -and $visited -lt 900) {{
  $pair = $queue.Dequeue()
  $node = $pair[0]; $depth = $pair[1]; $visited++
  try {{
    $ct = $node.Current.ControlType.ProgrammaticName -replace '^ControlType\\.', ''
    $name = [string]$node.Current.Name
    $aid = [string]$node.Current.AutomationId
    $rect = $node.Current.BoundingRectangle
    $val = ""
    try {{
      $vp = $null
      if ($node.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {{
        $val = [string]$vp.Current.Value
      }}
    }} catch {{}}
    if ($interesting -contains $ct -and ($name -ne "" -or $ct -eq "Edit" -or $ct -eq "Document")) {{
      $rtid = ($node.GetRuntimeId() -join ".")
      $b = @{{}}
      if (-not [double]::IsInfinity($rect.X) -and $rect.Width -gt 0 -and $rect.Height -gt 0) {{
        $b = @{{ x = [int]$rect.X; y = [int]$rect.Y; width = [int]$rect.Width; height = [int]$rect.Height }}
      }}
      [void]$items.Add(@{{
        index = $items.Count + 1
        role = $ct
        label = $name.Substring(0, [Math]::Min(160, $name.Length))
        value = $val.Substring(0, [Math]::Min(160, $val.Length))
        automation_id = $aid
        runtime_id = $rtid
        bounds = $b
      }})
    }}
    if ($depth -lt 7) {{
      $child = $walker.GetFirstChild($node)
      $count = 0
      while ($null -ne $child -and $count -lt 50) {{
        $queue.Enqueue(@($child, $depth + 1))
        $child = $walker.GetNextSibling($child)
        $count++
      }}
    }}
  }} catch {{}}
}}
@{{ status = "success"; window_title = [string]$root.Current.Name; elements = $items }} | ConvertTo-Json -Depth 5 -Compress
'''
    completed = _run_cmd([ps, "-NoProfile", "-Command", script], timeout_seconds=15)
    if completed.returncode != 0 or not completed.stdout.strip():
        return {"status": "error", "message": completed.stderr.strip() or "Windows UIA snapshot failed."}
    try:
        parsed = json.loads(completed.stdout)
        return parsed if isinstance(parsed, dict) else {"status": "error", "message": "Invalid UIA JSON output."}
    except Exception as exc:
        return {"status": "error", "message": f"Failed to parse UIA snapshot: {exc}"}


def _act_windows_uia(hwnd: int, runtime_id: str, action: str, text: str = "") -> Dict[str, Any]:
    ps = _windows_powershell_binary(allow_wsl_host=True)
    if not ps:
        return {"status": "error", "message": "PowerShell UIA unavailable."}
    escaped_text = text.replace("'", "''")
    script = f'''
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{int(hwnd)})
if ($null -eq $root) {{ Write-Output '{{"status":"error","message":"Window handle closed"}}'; exit 0 }}
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$queue = New-Object System.Collections.Queue
$queue.Enqueue($root)
$target = $null
$visited = 0
while ($queue.Count -gt 0 -and $visited -lt 900) {{
  $node = $queue.Dequeue(); $visited++
  try {{
    if (($node.GetRuntimeId() -join ".") -eq '{runtime_id}') {{ $target = $node; break }}
    $child = $walker.GetFirstChild($node)
    while ($null -ne $child) {{ $queue.Enqueue($child); $child = $walker.GetNextSibling($child) }}
  }} catch {{}}
}}
if ($null -eq $target) {{ Write-Output '{{"status":"error","message":"Element not found; refresh snapshot"}}'; exit 0 }}
if ('{action}' -eq 'invoke') {{
  $p = $null
  if ($target.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$p)) {{ $p.Invoke(); Write-Output '{{"status":"success","action":"InvokePattern"}}'; exit 0 }}
  if ($target.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$p)) {{ $p.Toggle(); Write-Output '{{"status":"success","action":"TogglePattern"}}'; exit 0 }}
  if ($target.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p)) {{ $p.Select(); Write-Output '{{"status":"success","action":"SelectionItemPattern"}}'; exit 0 }}
  Write-Output '{{"status":"fallback_needed"}}'; exit 0
}} elseif ('{action}' -eq 'set_value') {{
  $p = $null
  if ($target.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$p)) {{
    $p.SetValue('{escaped_text}')
    Write-Output '{{"status":"success","action":"ValuePattern"}}'; exit 0
  }}
  Write-Output '{{"status":"fallback_needed"}}'; exit 0
}}
'''
    completed = _run_cmd([ps, "-NoProfile", "-Command", script], timeout_seconds=12)
    try:
        parsed = json.loads(completed.stdout.strip() or "{}")
        return parsed if isinstance(parsed, dict) else {"status": "fallback_needed"}
    except Exception:
        return {"status": "fallback_needed"}


def _list_windows_linux_native() -> List[Dict[str, Any]]:
    if not shutil.which("wmctrl"):
        return []
    completed = _run_cmd(["wmctrl", "-lpG"], timeout_seconds=8)
    if completed.returncode != 0:
        return []
    windows: List[Dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        xid, _, pid, x, y, w, h, title = parts
        try:
            wi, hi = int(w), int(h)
            if wi < 60 or hi < 40:
                continue
            windows.append({
                "app_name": "",
                "process_name": "",
                "title": title.strip(),
                "pid": int(pid or 0),
                "window_id": xid,
                "bounds": {"x": int(x), "y": int(y), "width": wi, "height": hi},
                "platform": "linux",
            })
        except ValueError:
            continue
    return windows


# ─── Unified Public API ──────────────────────────────────────────────────────

def list_accessible_windows(app_filter: Optional[str] = None) -> Dict[str, Any]:
    """List visible desktop windows using the best available backend across OSes."""
    env = detect_desktop_environment()
    if not env["display_available"]:
        return {
            "status": "headless",
            "platform": env["platform"],
            "backend": env["backend"],
            "windows": [],
            "count": 0,
            "message": env["hint"],
        }

    windows: List[Dict[str, Any]] = []
    backend = env["backend"]

    if backend == "cua-driver":
        args = {"app": app_filter} if app_filter else {}
        res = _cua_call("list_windows", args)
        if isinstance(res, dict) and isinstance(res.get("windows"), list):
            for w in res["windows"]:
                if not isinstance(w, dict):
                    continue
                if w.get("is_on_screen") is False:
                    continue
                b = w.get("bounds") or {}
                if float(b.get("width") or 0) < 40 or float(b.get("height") or 0) < 40:
                    continue
                windows.append({
                    "app_name": str(w.get("app_name") or ""),
                    "process_name": str(w.get("app_name") or ""),
                    "title": str(w.get("title") or ""),
                    "pid": w.get("pid"),
                    "window_id": w.get("window_id"),
                    "bounds": b,
                    "platform": env["platform"],
                })
        else:
            backend = "macos-ax" if env["platform"] == "macos" else ("windows-uia" if env["platform"] in {"windows", "wsl2"} else "linux-x11")

    if not windows and backend == "macos-ax":
        windows = _list_windows_macos_native()
    elif not windows and backend in {"windows-uia", "wsl-windows-uia"}:
        windows = _list_windows_windows_native()
    elif not windows and backend in {"linux-atspi", "linux-x11"}:
        windows = _list_windows_linux_native()

    if app_filter:
        needle = str(app_filter).strip().lower()
        windows = [
            w for w in windows
            if needle in str(w.get("app_name") or "").lower()
            or needle in str(w.get("title") or "").lower()
        ]

    return {
        "status": "success",
        "platform": env["platform"],
        "backend": backend,
        "windows": windows,
        "count": len(windows),
    }


def _select_target_window(
    windows: List[Dict[str, Any]],
    *,
    app_name: str = "",
    window_title: str = "",
    pid: Optional[int] = None,
    window_id: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    if not windows:
        return None
    if window_id is not None:
        for w in windows:
            if str(w.get("window_id")) == str(window_id):
                return w
    if pid is not None:
        for w in windows:
            if w.get("pid") == pid and (not window_title or window_title.lower() in str(w.get("title") or "").lower()):
                return w
    app_l = str(app_name or "").strip().lower()
    title_l = str(window_title or "").strip().lower()
    for w in windows:
        a = str(w.get("app_name") or w.get("process_name") or "").lower()
        t = str(w.get("title") or "").lower()
        if app_l and title_l and app_l in a and title_l in t:
            return w
    for w in windows:
        a = str(w.get("app_name") or w.get("process_name") or "").lower()
        t = str(w.get("title") or "").lower()
        if title_l and title_l in t:
            return w
        if app_l and (app_l in a or app_l in t):
            return w
    return windows[0] if (not app_l and not title_l) else None


def snapshot_window_accessibility(
    *,
    app_name: str = "",
    window_title: str = "",
    pid: Optional[int] = None,
    window_id: Optional[Any] = None,
    max_elements: int = 80,
) -> Dict[str, Any]:
    """Capture a compact, snapshot-bound accessibility tree for a desktop window.

    Every interactive control receives a stable token (``<snapshot_id>:<index>``)
    that can be passed directly to ``click_accessible_element`` or
    ``type_into_accessible_element``.
    """
    env = detect_desktop_environment()
    if not env["display_available"]:
        return {
            "status": "headless",
            "platform": env["platform"],
            "backend": env["backend"],
            "message": env["hint"],
            "tree_markdown": "",
            "elements": [],
        }

    listed = list_accessible_windows(app_filter=app_name or None)
    windows = listed.get("windows") or []
    target = _select_target_window(windows, app_name=app_name, window_title=window_title, pid=pid, window_id=window_id)
    if target is None and app_name:
        listed = list_accessible_windows()
        target = _select_target_window(listed.get("windows") or [], app_name=app_name, window_title=window_title, pid=pid, window_id=window_id)
    if target is None:
        available = [f"{w.get('app_name')}: {w.get('title')}" for w in (listed.get("windows") or [])[:12]]
        return {
            "status": "not_found",
            "platform": env["platform"],
            "backend": listed.get("backend") or env["backend"],
            "message": f"No visible window matched app={app_name!r} title={window_title!r}.",
            "available_windows": available,
            "tree_markdown": "",
            "elements": [],
        }

    clamped_max = max(10, min(int(max_elements or 80), 200))
    backend = listed.get("backend") or env["backend"]

    if backend == "cua-driver" and target.get("pid") and target.get("window_id"):
        res = _cua_call("get_window_state", {
            "pid": target["pid"],
            "window_id": target["window_id"],
            "include_screenshot": False,
            "max_elements": clamped_max,
        })
        if isinstance(res, dict) and res.get("snapshot_id"):
            snap_id = str(res["snapshot_id"])
            tree = str(res.get("tree_markdown") or "")
            _store_snapshot(snap_id, {"backend": "cua-driver", "window": target, "elements": []})
            return {
                "status": "success",
                "platform": env["platform"],
                "backend": "cua-driver",
                "snapshot_id": snap_id,
                "window": target,
                "tree_markdown": tree[:10000],
                "token_hint": f"Use element_token '{snap_id}:<index>' from the [index] markers above.",
            }

    snap_id = f"ay-{uuid.uuid4().hex[:10]}"

    if env["platform"] == "macos" and target.get("pid") and shutil.which("swift"):
        completed = _run_cmd(
            ["swift", "-e", _MACOS_AX_SNAPSHOT_SWIFT, str(target["pid"]), str(clamped_max), "snapshot"],
            timeout_seconds=14,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            try:
                parsed = json.loads(completed.stdout)
                raw_elements = parsed.get("elements") or []
                elements = []
                for el in raw_elements:
                    idx = int(el.get("index") or (len(elements) + 1))
                    item = dict(el)
                    item["index"] = idx
                    item["token"] = f"{snap_id}:{idx}"
                    elements.append(item)
                _store_snapshot(snap_id, {"backend": "macos-ax", "window": target, "elements": elements})
                return {
                    "status": "success",
                    "platform": "macos",
                    "backend": "macos-ax",
                    "accessibility_trusted": bool(parsed.get("trusted", True)),
                    "snapshot_id": snap_id,
                    "window": target,
                    "element_count": len(elements),
                    "elements": elements,
                    "tree_markdown": _format_elements_markdown(target, elements)[:10000],
                }
            except Exception as exc:
                LOGGER.debug("Failed to parse macOS AX snapshot: %s", exc)

    if env["platform"] in {"windows", "wsl2"} and target.get("window_id"):
        try:
            hwnd = int(target["window_id"])
        except (TypeError, ValueError):
            hwnd = 0
        if hwnd:
            parsed = _snapshot_windows_uia(hwnd, max_elements=clamped_max)
            if parsed.get("status") == "success":
                raw_elements = parsed.get("elements") or []
                elements = []
                for el in raw_elements:
                    idx = int(el.get("index") or (len(elements) + 1))
                    item = dict(el)
                    item["index"] = idx
                    item["token"] = f"{snap_id}:{idx}"
                    elements.append(item)
                _store_snapshot(snap_id, {"backend": env["backend"], "window": target, "elements": elements})
                return {
                    "status": "success",
                    "platform": env["platform"],
                    "backend": env["backend"],
                    "snapshot_id": snap_id,
                    "window": target,
                    "element_count": len(elements),
                    "elements": elements,
                    "tree_markdown": _format_elements_markdown(target, elements)[:10000],
                }

    # Window metadata fallback when semantic tree is unavailable
    _store_snapshot(snap_id, {"backend": backend, "window": target, "elements": []})
    return {
        "status": "partial",
        "platform": env["platform"],
        "backend": backend,
        "snapshot_id": snap_id,
        "window": target,
        "element_count": 0,
        "elements": [],
        "tree_markdown": _format_elements_markdown(target, []),
        "message": env["hint"],
    }


def _fallback_click_bounds(bounds: Dict[str, Any], *, double_click: bool = False, right_click: bool = False) -> Dict[str, Any]:
    if not bounds or not bounds.get("width"):
        return {"status": "error", "message": "Element has no clickable bounds."}
    cx = int(bounds["x"]) + int(bounds["width"]) // 2
    cy = int(bounds["y"]) + int(bounds["height"]) // 2
    try:
        from shared.remote_desktop_keyboard import load_pyautogui

        pyautogui = load_pyautogui()
        if right_click:
            pyautogui.rightClick(cx, cy)
        elif double_click:
            pyautogui.doubleClick(cx, cy)
        else:
            pyautogui.click(cx, cy)
        return {"status": "success", "action": "coordinate_click", "x": cx, "y": cy}
    except Exception as exc:
        return {"status": "error", "message": f"Fallback click failed: {exc}"}


def click_accessible_element(
    element_token: str,
    *,
    double_click: bool = False,
    right_click: bool = False,
) -> Dict[str, Any]:
    """Click or invoke a snapshot-bound UI element token without focus theft when supported."""
    token = str(element_token or "").strip()
    if not token or ":" not in token:
        return {"status": "error", "message": "element_token must look like '<snapshot_id>:<index>' from snapshot_window_ui."}

    snap_id = token.rsplit(":", 1)[0]
    with _SNAPSHOT_CACHE_LOCK:
        cached = _SNAPSHOT_CACHE.get(snap_id)

    if (cached and cached.get("backend") == "cua-driver") or (not snap_id.startswith("ay-") and find_cua_driver_binary()):
        tool = "right_click" if right_click else ("double_click" if double_click else "click")
        res = _cua_call(tool, {"element_token": token})
        if isinstance(res, dict):
            return {"status": "success", "backend": "cua-driver", "action": tool, "element_token": token, "result": res}
        return {"status": "error", "backend": "cua-driver", "message": f"cua-driver {tool} failed for token {token}; take a fresh snapshot."}

    snap, element = _lookup_snapshot_element(token)
    if not snap or not element:
        return {"status": "error", "message": f"Unknown or expired element_token {token!r}. Call snapshot_window_ui first."}

    window = snap.get("window") or {}
    backend = snap.get("backend")

    if backend == "macos-ax" and not right_click and not double_click and window.get("pid") and element.get("path"):
        completed = _run_cmd(
            ["swift", "-e", _MACOS_AX_SNAPSHOT_SWIFT, str(window["pid"]), "1", "press", str(element["path"])],
            timeout_seconds=10,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            try:
                parsed = json.loads(completed.stdout)
                if parsed.get("status") == "success":
                    return {"status": "success", "backend": "macos-ax", "action": "AXPress", "element": element.get("label")}
            except Exception:
                pass

    if backend in {"windows-uia", "wsl-windows-uia"} and not right_click and not double_click and window.get("window_id") and element.get("runtime_id"):
        res = _act_windows_uia(int(window["window_id"]), str(element["runtime_id"]), "invoke")
        if res.get("status") == "success":
            return {"status": "success", "backend": backend, "action": res.get("action"), "element": element.get("label")}

    return _fallback_click_bounds(element.get("bounds") or {}, double_click=double_click, right_click=right_click)


def type_into_accessible_element(
    element_token: str,
    text: str,
    *,
    submit: bool = False,
) -> Dict[str, Any]:
    """Write text into a snapshot-bound UI input token, optionally pressing Return."""
    token = str(element_token or "").strip()
    if not token or ":" not in token:
        return {"status": "error", "message": "element_token must look like '<snapshot_id>:<index>' from snapshot_window_ui."}

    snap_id = token.rsplit(":", 1)[0]
    with _SNAPSHOT_CACHE_LOCK:
        cached = _SNAPSHOT_CACHE.get(snap_id)

    if (cached and cached.get("backend") == "cua-driver") or (not snap_id.startswith("ay-") and find_cua_driver_binary()):
        res = _cua_call("set_value", {"element_token": token, "value": str(text)})
        if res is None:
            _cua_call("click", {"element_token": token})
            res = _cua_call("type_text", {"text": str(text)})
        if submit and res is not None:
            _cua_call("press_key", {"key": "Return"})
        if res is not None:
            return {"status": "success", "backend": "cua-driver", "submitted": bool(submit), "element_token": token}
        return {"status": "error", "backend": "cua-driver", "message": f"Failed to type into {token}; take a fresh snapshot."}

    snap, element = _lookup_snapshot_element(token)
    if not snap or not element:
        return {"status": "error", "message": f"Unknown or expired element_token {token!r}. Call snapshot_window_ui first."}

    window = snap.get("window") or {}
    backend = snap.get("backend")

    if backend == "macos-ax" and window.get("pid") and element.get("path"):
        completed = _run_cmd(
            ["swift", "-e", _MACOS_AX_SNAPSHOT_SWIFT, str(window["pid"]), "1", "set_value", str(element["path"]), str(text)],
            timeout_seconds=10,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            try:
                parsed = json.loads(completed.stdout)
                if parsed.get("status") == "success":
                    if submit:
                        press_accessible_hotkey(["return"], app_name=str(window.get("app_name") or ""))
                    return {"status": "success", "backend": "macos-ax", "action": "AXSetValue", "submitted": bool(submit)}
            except Exception:
                pass

    if backend in {"windows-uia", "wsl-windows-uia"} and window.get("window_id") and element.get("runtime_id"):
        res = _act_windows_uia(int(window["window_id"]), str(element["runtime_id"]), "set_value", str(text))
        if res.get("status") == "success":
            if submit:
                press_accessible_hotkey(["enter"], app_name=str(window.get("app_name") or ""))
            return {"status": "success", "backend": backend, "action": "ValuePattern", "submitted": bool(submit)}

    clicked = _fallback_click_bounds(element.get("bounds") or {})
    if clicked.get("status") != "success":
        return clicked
    try:
        from shared.remote_desktop_keyboard import load_pyautogui

        pyautogui = load_pyautogui()
        pyautogui.write(str(text), interval=0.01)
        if submit:
            pyautogui.press("enter")
        return {"status": "success", "action": "coordinate_type", "submitted": bool(submit)}
    except Exception as exc:
        return {"status": "error", "message": f"Fallback typing failed: {exc}"}


def press_accessible_hotkey(keys: Sequence[str], *, app_name: str = "") -> Dict[str, Any]:
    """Send a keyboard shortcut cleanly via cua-driver or cross-platform fallback."""
    cleaned = [str(k).strip().lower() for k in (keys or []) if str(k).strip()]
    if not cleaned:
        return {"status": "error", "message": "At least one key is required."}
    env = detect_desktop_environment()
    if not env["display_available"]:
        return {"status": "headless", "message": env["hint"]}

    if env["backend"] == "cua-driver":
        if len(cleaned) == 1:
            res = _cua_call("press_key", {"key": cleaned[0]})
        else:
            res = _cua_call("hotkey", {"keys": cleaned})
        if res is not None:
            return {"status": "success", "backend": "cua-driver", "keys": cleaned}

    try:
        from shared.remote_desktop_keyboard import load_pyautogui

        pyautogui = load_pyautogui()
        key_map = {"cmd": "command", "meta": "command", "return": "enter", "esc": "escape"}
        mapped = [key_map.get(k, k) for k in cleaned]
        if len(mapped) == 1:
            pyautogui.press(mapped[0])
        else:
            pyautogui.hotkey(*mapped)
        return {"status": "success", "backend": env["backend"], "keys": mapped}
    except Exception as exc:
        return {"status": "error", "message": f"Hotkey failed: {exc}"}


def find_composer_element_token(snapshot: Dict[str, Any]) -> Optional[str]:
    """Locate the primary text composer token inside a window accessibility snapshot."""
    tree = str(snapshot.get("tree_markdown") or "")
    snap_id = str(snapshot.get("snapshot_id") or "").strip()
    elements = snapshot.get("elements") or []
    preferred_roles = {"axtextarea", "axtextfield", "axsearchfield", "edit", "document"}
    composer_hints = ("ask", "message", "prompt", "type", "follow-up", "describe", "chat", "input", "composer")

    best_token: Optional[str] = None
    for el in elements:
        role = str(el.get("role") or "").lower()
        if role not in preferred_roles:
            continue
        label = f"{el.get('label') or ''} {el.get('value') or ''}".lower()
        token = str(el.get("token") or "")
        if any(hint in label for hint in composer_hints):
            return token
        if best_token is None:
            best_token = token
    if best_token:
        return best_token

    if snap_id and tree:
        import re

        for match in re.finditer(r"\[(\d+)\]\s+(AXTextArea|AXTextField|Edit|Document)\b([^\n]*)", tree):
            idx, _, tail = match.groups()
            if any(hint in tail.lower() for hint in composer_hints):
                return f"{snap_id}:{idx}"
        fallback = re.search(r"\[(\d+)\]\s+(AXTextArea|AXTextField|Edit|Document)\b", tree)
        if fallback:
            return f"{snap_id}:{fallback.group(1)}"
    return None


__all__ = [
    "click_accessible_element",
    "detect_desktop_environment",
    "find_composer_element_token",
    "find_cua_driver_binary",
    "list_accessible_windows",
    "press_accessible_hotkey",
    "snapshot_window_accessibility",
    "type_into_accessible_element",
]
