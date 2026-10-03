# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-9a12e988376416a7ae1123ee

"""Reusable desktop automation helpers for bridge agents.

The goal is to provide a thin, publishable framework that can:
- inspect whether a target desktop app appears to be running
- choose an OS-specific screenshot/coordinate pack
- focus or launch the app when possible
- click/type/submit prompts using cross-platform automation backends
- capture screenshots so packs can be crowd-sourced and refreshed later

The implementation prefers native OS probes for window metadata and uses
PyAutoGUI + MSS for input/screenshot control when available.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import base64
import json
import logging
import math
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import psutil

from ._subprocess_env import scrubbed_subprocess_env as _scrubbed_subprocess_env
from shared.remote_desktop_keyboard import hide_macos_dock_icon
from .desktop_app_manifest import (
    load_desktop_app_manifest,
    normalize_platform_tag,
    write_desktop_agent_llm_reference,
)

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-9a12e988376416a7ae1123ee"


LOGGER = logging.getLogger(__name__)
_SCREENSHOT_NAME_TOKENS = ("screenshot", "screen shot")
_SCREENSHOT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".heic"}
_DRAFT_PROMPT_BY_AGENT: Dict[str, str] = {}
_SUBMITTED_PROMPT_BY_AGENT: Dict[str, str] = {}
_OCR_BUSY_MARKERS = (
    "thinking",
    "stop generating",
    "generating",
    "usage limit",
    "usage limit reached",
    "rate limit",
    "out of codex messages",
    "try again later",
)
_OCR_COMPOSER_MARKERS = (
    "ask for follow-up changes",
    "describe a task or ask a question",
    "type / for commands",
)


def _missing_desktop_manifest_message(agent_dir: Path) -> str:
    expected_path = Path(agent_dir) / "desktop_assets" / "manifest.template.json"
    return (
        f"No desktop agent manifest template found for {agent_dir}. Expected "
        f"{expected_path}; rebuild or reinstall AutoYou with the desktop agent templates. "
        "User-calibrated packs are imported separately from Agents setup."
    )


def _ensure_interactive_desktop() -> None:
    """Ensure the current thread is attached to the interactive user desktop (WinSta0\\Default) on Windows.
    This resolves issues where GUI automation commands run under background service accounts
    or virtual desktops (e.g. agy-...) and fail to interact with visible user windows. This helper
    is also used for read-only probes, so it must not reposition the user's pointer.
    """
    if platform.system().lower() != "windows":
        return
    try:
        import ctypes
        # Standard Win32 access rights
        WINSTA_ALL_ACCESS = 0x37F
        DESKTOP_ALL_ACCESS = 0x1FF
        
        # 1. Open the interactive window station
        h_winsta = ctypes.windll.user32.OpenWindowStationW("WinSta0", False, WINSTA_ALL_ACCESS)
        if h_winsta:
            if ctypes.windll.user32.SetProcessWindowStation(h_winsta):
                # 2. Open the default desktop
                h_desk = ctypes.windll.user32.OpenDesktopW("Default", 0, False, DESKTOP_ALL_ACCESS)
                if h_desk:
                    if ctypes.windll.user32.SetThreadDesktop(h_desk):
                        LOGGER.debug("Successfully switched current thread to WinSta0\\Default desktop.")
                    else:
                        LOGGER.warning("SetThreadDesktop to Default failed.")
                else:
                    LOGGER.warning("OpenDesktopW Default failed.")
            else:
                LOGGER.warning("SetProcessWindowStation failed.")
        else:
            # Fallback: try opening default desktop directly
            h_desk = ctypes.windll.user32.OpenDesktopW("Default", 0, False, DESKTOP_ALL_ACCESS)
            if h_desk:
                if ctypes.windll.user32.SetThreadDesktop(h_desk):
                    LOGGER.debug("Successfully switched current thread to default desktop via fallback.")
                else:
                    LOGGER.warning("Fallback SetThreadDesktop to Default failed.")
    except Exception as e:
        LOGGER.warning(f"Error during Windows desktop thread switching: {e}", exc_info=True)


def _get_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _get_artifacts_root() -> Path:
    try:
        from .desktop_asset_store import get_user_desktop_agents_root

        root = get_user_desktop_agents_root().parent / "desktop_automation"
        root.mkdir(parents=True, exist_ok=True)
        return root
    except Exception:
        from shared.platform_runtime import get_user_data_dir

        root = get_user_data_dir("AutoYou") / "desktop_automation"
        root.mkdir(parents=True, exist_ok=True)
        return root


def _safe_json_loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return None


def _agent_prompt_key(agent_name: Any) -> str:
    return str(agent_name or "").strip()


def _remember_draft_prompt(
    agent_name: Any,
    prompt: str,
    *,
    append: bool = False,
    preserve_text: bool = False,
) -> None:
    key = _agent_prompt_key(agent_name)
    if not key:
        return
    cleaned = str(prompt or "") if preserve_text else str(prompt or "").strip()
    if not cleaned:
        return
    if append and _DRAFT_PROMPT_BY_AGENT.get(key):
        prior = _DRAFT_PROMPT_BY_AGENT[key]
        _DRAFT_PROMPT_BY_AGENT[key] = (
            prior + "\n" + cleaned
            if preserve_text
            else (prior.rstrip() + "\n" + cleaned).strip()
        )
    else:
        _DRAFT_PROMPT_BY_AGENT[key] = cleaned


def _remember_submitted_prompt(agent_name: Any, prompt: Optional[str] = None) -> None:
    key = _agent_prompt_key(agent_name)
    if not key:
        return
    cleaned = str(prompt or _DRAFT_PROMPT_BY_AGENT.get(key) or "").strip()
    if cleaned:
        _SUBMITTED_PROMPT_BY_AGENT[key] = cleaned


def _last_submitted_prompt(agent_name: Any) -> str:
    return _SUBMITTED_PROMPT_BY_AGENT.get(_agent_prompt_key(agent_name), "")


def _run_command(args: List[str], *, timeout_seconds: int = 10, check: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
            check=check,
            env=_scrubbed_subprocess_env(),
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as e:
        if check:
            raise
        return subprocess.CompletedProcess(args=args, returncode=124, stdout="", stderr=f"Timed out after {timeout_seconds}s")


def current_platform_details() -> Dict[str, Any]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    platform_tag = normalize_platform_tag(system)
    if os.environ.get("WSL_DISTRO_NAME"):
        variant = "wsl2"
    elif platform_tag == "linux" and "microsoft" in platform.release().lower():
        variant = "wsl2"
    elif platform_tag == "linux" and any(token in platform.platform().lower() for token in ("raspbian", "armv7", "aarch64", "raspberry")):
        variant = "pi"
    elif platform_tag == "linux":
        variant = "ubuntu" if "ubuntu" in platform.platform().lower() else "linux"
    else:
        variant = platform_tag
    return {
        "platform": platform_tag,
        "platform_variant": variant,
        "architecture": machine or "unknown",
        "platform_version": platform.release(),
        "hostname": platform.node(),
    }


def _normalize_process_names(manifest: Dict[str, Any], pack: Optional[Dict[str, Any]], platform_tag: str) -> List[str]:
    names: List[str] = []
    for source in (
        ((manifest.get("process_names") or {}).get(platform_tag) or []),
        ((manifest.get("process_names") or {}).get("any") or []),
        ((pack or {}).get("process_names") or []),
    ):
        for item in source:
            cleaned = str(item or "").strip()
            if cleaned and cleaned not in names:
                names.append(cleaned)
    return names


def _normalize_window_title_hints(manifest: Dict[str, Any], pack: Optional[Dict[str, Any]], platform_tag: str) -> List[str]:
    hints: List[str] = []
    for source in (
        ((manifest.get("window_title_hints") or {}).get(platform_tag) or []),
        ((manifest.get("window_title_hints") or {}).get("any") or []),
        ((pack or {}).get("window_title_hints") or []),
    ):
        for item in source:
            cleaned = str(item or "").strip()
            if cleaned and cleaned not in hints:
                hints.append(cleaned)
    return hints


def _version_tuple(value: Any) -> Tuple[int, ...]:
    """Parse a version string like '26.623.61825' into a comparable int tuple."""
    parts: List[int] = []
    for chunk in re.split(r"[._\-+]", str(value or "").strip()):
        match = re.match(r"^(\d+)", chunk)
        if match:
            parts.append(int(match.group(1)))
        elif chunk:
            break
    return tuple(parts)


def _desktop_scale_factor(value: Any) -> Optional[float]:
    if value is None or str(value).strip().lower() in {"", "any", "auto", "none"}:
        return None
    try:
        text = str(value).strip()
        percent = text.endswith("%")
        scale = float(text[:-1] if percent else text)
        if percent or scale > 4.0:
            scale /= 100.0
        return scale if math.isfinite(scale) and 0.5 <= scale <= 4.0 else None
    except (TypeError, ValueError):
        return None


def _macos_app_version_from_bundle(bundle_path: Path) -> Optional[str]:
    plist_path = bundle_path / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return None
    try:
        import plistlib

        with plist_path.open("rb") as handle:
            data = plistlib.load(handle)
        version = str(data.get("CFBundleShortVersionString") or data.get("CFBundleVersion") or "").strip()
        return version or None
    except Exception:
        return None


def _find_macos_app_bundle(exe_path: str) -> Optional[Path]:
    candidate = Path(str(exe_path or ""))
    for parent in [candidate, *candidate.parents]:
        if parent.suffix == ".app":
            return parent
    return None


def _windows_msix_package_version(exe_path: str) -> Optional[str]:
    """Extract the package version from an MSIX install path, if the exe lives in one.
    """
    try:
        parts = Path(exe_path).resolve().parts
    except Exception:
        return None
    for index, part in enumerate(parts):
        if part.lower() != "windowsapps" or index + 1 >= len(parts):
            continue
        segments = parts[index + 1].split("_")
        if len(segments) >= 2 and re.match(r"^\d+(\.\d+)+$", segments[1]):
            return segments[1]
    return None


def detect_installed_app_version(
    manifest: Dict[str, Any],
    platform_tag: str,
    process_hints: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """Best-effort detection of the installed target app version (for version-aware pack selection)."""
    hints = list(process_hints or _normalize_process_names(manifest, None, platform_tag))
    if platform_tag == "macos":
        for proc in _list_running_processes(hints):
            bundle = _find_macos_app_bundle(str(proc.get("exe") or ""))
            if bundle:
                version = _macos_app_version_from_bundle(bundle)
                if version:
                    return version
        app_id = str(manifest.get("app_id") or "").strip()
        if app_id and "." in app_id:
            try:
                completed = _run_command(["mdfind", f"kMDItemCFBundleIdentifier == '{app_id}'"], timeout_seconds=5)
                for line in (completed.stdout or "").splitlines():
                    bundle = Path(line.strip())
                    if bundle.suffix == ".app":
                        version = _macos_app_version_from_bundle(bundle)
                        if version:
                            return version
            except Exception:
                return None
        return None
    if platform_tag == "windows":
        for proc in _list_running_processes(hints):
            exe = str(proc.get("exe") or "")
            if not exe:
                continue
            # MSIX-packaged apps (ChatGPT Codex ships as OpenAI.Codex) live under
            # WindowsApps\<Name>_<Version>_<arch>__<publisher>, and the package version there is
            # the real app version. Their exe's ProductVersion is the bundled Chromium version
            # (e.g. 150.0.7871.182), which would satisfy every pack's app_version_min and make
            # version-aware pack selection meaningless - so prefer the package identity.
            package_version = _windows_msix_package_version(exe)
            if package_version:
                return package_version
            try:
                script = f"(Get-Item -LiteralPath {json.dumps(exe)}).VersionInfo.ProductVersion"
                completed = _run_command(["powershell", "-NoProfile", "-Command", script], timeout_seconds=6)
                version = (completed.stdout or "").strip()
                if version:
                    return version
            except Exception:
                continue
        return None
    return None


def select_desktop_asset_pack(
    manifest: Dict[str, Any],
    *,
    platform_tag: Optional[str] = None,
    architecture: Optional[str] = None,
    on_date: Optional[str] = None,
    app_version: Optional[str] = None,
    theme: Optional[str] = None,
    display_scale: Optional[float | str] = None,
) -> Optional[Dict[str, Any]]:
    details = current_platform_details()
    desired_platform = normalize_platform_tag(platform_tag or details["platform"])
    desired_architecture = str(architecture or details["architecture"] or "").strip().lower()
    reference_date = str(on_date or date.today().isoformat()).strip()

    # Version-aware selection: when the installed version is known, prefer the pack whose
    # app_version_min is the highest value still <= it. If detection fails, preserve the
    # existing score-only fallback instead of guessing a newer UI pack.
    if app_version is None:
        app_version = detect_installed_app_version(manifest, desired_platform)
    installed_version = _version_tuple(app_version) if app_version else tuple()

    try:
        from .desktop_asset_store import effective_desktop_asset_preferences

        preferences = effective_desktop_asset_preferences(str(manifest.get("agent_name") or ""))
    except Exception:
        preferences = {"theme": "any", "display_scale": None}
    desired_theme = str(theme or preferences.get("theme") or "any").strip().lower()
    if desired_theme == "auto":
        desired_theme = str(preferences.get("theme") or "any").strip().lower()
    if not desired_theme:
        desired_theme = "any"
    desired_scale = _desktop_scale_factor(display_scale)
    if display_scale is None or str(display_scale).strip().lower() == "auto":
        desired_scale = _desktop_scale_factor(preferences.get("display_scale"))

    candidates: List[Tuple[bool, Tuple[int, ...], int, Dict[str, Any]]] = []
    for pack in manifest.get("asset_packs") or []:
        pack_platform = normalize_platform_tag(pack.get("platform"))
        if pack_platform not in {"any", desired_platform}:
            continue
        pack_architectures = [str(item).strip().lower() for item in (pack.get("architectures") or []) if str(item).strip()]
        if pack_architectures and desired_architecture and desired_architecture not in pack_architectures:
            continue
        valid_until = str(pack.get("valid_until") or "").strip()
        if valid_until and reference_date and valid_until < reference_date:
            continue
        pack_min_version = _version_tuple(pack.get("app_version_min"))
        pack_max_version = _version_tuple(pack.get("app_version_max"))
        pack_exact_version = str(pack.get("app_version") or "").strip()
        # Skip packs that require a newer app than what is installed.
        if installed_version and pack_min_version and pack_min_version > installed_version:
            continue
        if installed_version and pack_max_version and installed_version > pack_max_version:
            continue
        if pack_exact_version and not app_version:
            continue
        if app_version and pack_exact_version and pack_exact_version.casefold() != str(app_version).strip().casefold():
            continue
        pack_theme = str(pack.get("theme") or "any").strip().lower() or "any"
        if desired_theme not in {"", "any"} and pack_theme not in {"", "any", desired_theme}:
            continue
        pack_scale = _desktop_scale_factor(pack.get("display_scale"))
        if desired_scale is not None and pack_scale is not None and abs(desired_scale - pack_scale) > 0.05:
            continue
        score = 0
        if pack_platform == desired_platform:
            score += 4
        if pack_architectures and desired_architecture in pack_architectures:
            score += 2
        if pack_exact_version and app_version and pack_exact_version.casefold() == str(app_version).strip().casefold():
            score += 6
        if not pack.get("bootstrap_only"):
            score += 1
        if pack_theme == desired_theme and desired_theme not in {"", "any"}:
            score += 4
        elif pack_theme in {"", "any"}:
            score += 1
        if desired_scale is not None and pack_scale is not None:
            score += 2
        elif pack_scale is None:
            score += 1
        if pack.get("_user_local_pack"):
            score += 8
        exact_version_match = bool(
            pack_exact_version
            and app_version
            and pack_exact_version.casefold() == str(app_version).strip().casefold()
        )
        version_rank = pack_min_version if installed_version and not exact_version_match else tuple()
        candidates.append((exact_version_match, version_rank, score, pack))

    if not candidates:
        return None
    # Exact installed-version packs win first. Among compatible version ranges,
    # prefer the highest qualifying minimum, then platform/theme/scale matches.
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return candidates[0][3]


def _release_action_coverage(manifest: Dict[str, Any], pack: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not pack:
        return {"release_ready": False, "actions": {}, "missing_actions": ["asset_pack"]}
    target_ids = {str(target.get("target_id") or "").strip() for target in (pack.get("targets") or []) if isinstance(target, dict)}
    prompt_target_id = str(manifest.get("preferred_prompt_target_id") or "composer_box")
    copy_target_id = str(manifest.get("preferred_copy_response_target_id") or "copy_response_button")
    copy_target = _resolve_target(pack, copy_target_id)
    selection_controls = pack.get("selection_controls") or {}
    usage_controls = pack.get("usage_controls") or {}
    copy_calibration = str((copy_target or {}).get("calibration_status") or "").lower()
    actions = {
        "get_current_state": True,
        "build_prompt": prompt_target_id in target_ids or "composer_box" in target_ids,
        "attach_media": prompt_target_id in target_ids or "composer_box" in target_ids,
        "send_prompt": bool(pack.get("submit_actions")),
        "get_final_response": copy_target_id in target_ids and "best-effort" not in copy_calibration,
        "get_usage": bool(usage_controls) and bool(usage_controls.get("release_ready")),
        "select_model": isinstance(selection_controls.get("model"), dict),
    }
    missing = [name for name, supported in actions.items() if not supported]
    return {
        "release_ready": not missing and not bool(pack.get("bootstrap_only")),
        "actions": actions,
        "missing_actions": missing,
        "bootstrap_only": bool(pack.get("bootstrap_only")),
    }


def _with_release_action_coverage(manifest: Dict[str, Any], pack: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(pack)
    enriched["release_action_coverage"] = _release_action_coverage(manifest, pack)
    return enriched


def list_desktop_asset_packs(agent_dir: Path, *, platform_tag: Optional[str] = None) -> Dict[str, Any]:
    manifest = load_desktop_app_manifest(Path(agent_dir))
    if not manifest:
        return {"status": "error", "message": _missing_desktop_manifest_message(agent_dir)}
    desired_platform = normalize_platform_tag(platform_tag)
    packs = []
    for pack in manifest.get("asset_packs") or []:
        if desired_platform != "any" and normalize_platform_tag(pack.get("platform")) not in {"any", desired_platform}:
            continue
        packs.append(_with_release_action_coverage(manifest, pack))
    return {
        "status": "success",
        "agent_name": manifest.get("agent_name"),
        "app_id": manifest.get("app_id"),
        "platform": desired_platform,
        "asset_packs": packs,
        "count": len(packs),
    }


def _list_running_processes(process_names: Iterable[str]) -> List[Dict[str, Any]]:
    desired = {str(name).strip().lower() for name in process_names if str(name).strip()}
    matches: List[Dict[str, Any]] = []
    if not desired:
        return matches
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            info = proc.info
            raw_name = str(info.get("name") or "").strip()
            raw_exe = str(info.get("exe") or "").strip()
            haystacks = {raw_name.lower(), Path(raw_exe).name.lower() if raw_exe else ""}
            if any(name in haystacks for name in desired):
                matches.append(
                    {
                        "pid": int(info.get("pid") or 0),
                        "name": raw_name or Path(raw_exe).name,
                        "exe": raw_exe or None,
                        "cmdline": list(info.get("cmdline") or []),
                    }
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches


def _build_artifact_path(agent_name: str, label: str) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    safe_label = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(label or "artifact"))
    return _get_artifacts_root() / str(agent_name).strip() / f"{timestamp}-{safe_label}.png"


# How long desktop automation stays disabled after the user grabs the pointer back.
_USER_ABORT_COOLDOWN_SECONDS = 120.0
_USER_ABORT_STATE: Dict[str, float] = {"at": 0.0}


def _lazy_import_pyautogui():
    hide_macos_dock_icon()
    try:
        import cv2  # type: ignore[import-not-found]
        if not hasattr(cv2, "__version__"):
            setattr(cv2, "__version__", getattr(cv2, "version", None) or "4.0.0")
    except Exception:
        pass
    import pyautogui  # type: ignore[import-not-found]

    # PyAutoGUI's fail-safe is the ONLY way a human can wrest back a pointer that automation is
    # driving: slamming it into a screen corner raises FailSafeException and stops the action.
    # This module drives the user's own local mouse, so disabling it left no escape hatch at all
    # - a misbehaving run could hold the cursor indefinitely. (The remote-desktop backends
    # legitimately keep it off: there the "corner" belongs to a remote screen, not the user's.)
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    return pyautogui


def _pointer_in_failsafe_corner(pyautogui: Any) -> bool:
    """True when the pointer is parked in a screen corner (the human's stop signal)."""
    try:
        x, y = pyautogui.position()
        width, height = pyautogui.size()
    except Exception:
        return False
    margin = 8
    return (x <= margin or x >= width - 1 - margin) and (y <= margin or y >= height - 1 - margin)


def note_user_abort() -> None:
    """Latch a user abort so in-flight retries stop instead of re-grabbing the pointer."""
    _USER_ABORT_STATE["at"] = time.time()


def clear_user_abort() -> None:
    _USER_ABORT_STATE["at"] = 0.0


def user_abort_active() -> bool:
    """True while a recent abort should keep desktop automation switched off.

    Without this latch the fail-safe accomplishes little: the raised exception aborts one
    action, the caller reports an error, the agent retries, and the pointer is seized again.
    """
    return (time.time() - float(_USER_ABORT_STATE.get("at") or 0.0)) < _USER_ABORT_COOLDOWN_SECONDS


def _lazy_import_mss():
    hide_macos_dock_icon()
    import mss  # type: ignore[import-not-found]

    return mss


def _screen_size() -> Tuple[int, int]:
    try:
        pyautogui = _lazy_import_pyautogui()
        width, height = pyautogui.size()
        return int(width), int(height)
    except Exception:
        return (0, 0)


def _monitor_count() -> int:
    try:
        mss = _lazy_import_mss()
        with mss.mss() as capture:
            return max(0, len(capture.monitors) - 1)
    except Exception:
        return 0


def _virtual_screen_rect() -> Tuple[int, int, int, int]:
    """(left, top, width, height) of the whole virtual desktop, in logical pixels.

    Window rects are virtual-desktop coordinates, so a monitor placed left of or above the
    primary one yields negative origins. On a single-monitor setup this is just (0, 0, w, h).
    """
    if platform.system().lower() == "windows":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
            SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
            rect = (
                int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
                int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
                int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
                int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
            )
            if rect[2] > 0 and rect[3] > 0:
                return rect
        except Exception:
            pass
    width, height = _screen_size()
    return (0, 0, int(width), int(height))


def _capture_desktop_screenshot(destination: Path) -> Dict[str, Any]:
    """Capture the entire virtual desktop, not just the primary monitor.

    The target window is frequently dragged onto a second display, and a primary-only capture
    simply does not contain it - every OCR/visual read then silently works on blank pixels.
    The returned ``origin`` is the virtual-desktop coordinate of the image's top-left corner,
    which callers need to translate window rects into image space.
    """
    _ensure_interactive_desktop()
    destination.parent.mkdir(parents=True, exist_ok=True)
    origin_x, origin_y, virtual_w, virtual_h = _virtual_screen_rect()
    errors: List[str] = []

    try:
        from PIL import ImageGrab

        image = ImageGrab.grab(all_screens=True)
        image.save(str(destination))
        return {
            "status": "success",
            "path": str(destination),
            "origin": [origin_x, origin_y],
            "virtual_size": [virtual_w, virtual_h],
            "engine": "pillow_all_screens",
        }
    except Exception as exc:
        errors.append(f"pillow: {exc}")

    try:
        mss = _lazy_import_mss()
        with mss.mss() as capture:
            # monitors[0] is the union of every display; monitors[1] is only the primary.
            monitor = capture.monitors[0]
            shot = capture.grab(monitor)
            from PIL import Image

            Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").save(str(destination))
        return {
            "status": "success",
            "path": str(destination),
            "origin": [int(monitor.get("left", 0)), int(monitor.get("top", 0))],
            "virtual_size": [int(monitor.get("width", 0)), int(monitor.get("height", 0))],
            "engine": "mss_all_monitors",
        }
    except Exception as exc:
        errors.append(f"mss: {exc}")

    try:
        pyautogui = _lazy_import_pyautogui()
        pyautogui.screenshot().save(str(destination))
        return {
            "status": "success",
            "path": str(destination),
            "origin": [0, 0],
            "virtual_size": list(_screen_size()),
            "engine": "pyautogui_primary",
            "warning": "primary-monitor capture only; windows on other displays are not included",
            "errors": errors,
        }
    except Exception as exc:
        errors.append(f"pyautogui: {exc}")
        return {"status": "error", "message": "; ".join(errors), "path": str(destination)}


def _clipboard_copy(text: str, platform_tag: str) -> bool:
    value = str(text or "")
    try:
        if platform_tag == "windows":
            # Send an ASCII base64 envelope to PowerShell so the subprocess
            # console encoding cannot reinterpret quotes, smart punctuation,
            # or non-ASCII keyboard input before it reaches the clipboard.
            encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
            command = (
                "$encoded = [Console]::In.ReadToEnd().Trim(); "
                "$bytes = [Convert]::FromBase64String($encoded); "
                "$value = [Text.Encoding]::UTF8.GetString($bytes); "
                "Set-Clipboard -Value $value"
            )
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    command,
                ],
                input=encoded,
                text=True,
                capture_output=True,
                timeout=8,
                env=_scrubbed_subprocess_env(),
                creationflags=creationflags,
            )
            return completed.returncode == 0
        if platform_tag == "macos":
            completed = subprocess.run(
                ["pbcopy"],
                input=value.encode("utf-8"),
                text=False,
                capture_output=True,
                timeout=8,
                env=_scrubbed_subprocess_env(),
            )
            return completed.returncode == 0
        if platform_tag == "linux":
            if shutil.which("wl-copy"):
                completed = subprocess.run(
                    ["wl-copy"],
                    input=value.encode("utf-8"),
                    text=False,
                    capture_output=True,
                    timeout=8,
                    env=_scrubbed_subprocess_env(),
                )
                return completed.returncode == 0
            if shutil.which("xclip"):
                completed = subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=value.encode("utf-8"),
                    text=False,
                    capture_output=True,
                    timeout=8,
                    env=_scrubbed_subprocess_env(),
                )
                return completed.returncode == 0
    except Exception:
        return False
    return False


def _clipboard_read(platform_tag: str) -> Dict[str, Any]:
    try:
        if platform_tag == "windows":
            completed = _run_command(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"], timeout_seconds=8)
            if completed.returncode == 0:
                return {"status": "success", "text": completed.stdout}
            completed = _run_command(["powershell", "-NoProfile", "-Command", "Get-Clipboard | Out-String"], timeout_seconds=8)
            if completed.returncode == 0:
                return {"status": "success", "text": completed.stdout}
        elif platform_tag == "macos":
            completed = _run_command(["pbpaste"], timeout_seconds=8)
            if completed.returncode == 0:
                return {"status": "success", "text": completed.stdout}
        elif platform_tag == "linux":
            if shutil.which("wl-paste"):
                completed = _run_command(["wl-paste", "--no-newline"], timeout_seconds=8)
                if completed.returncode == 0:
                    return {"status": "success", "text": completed.stdout}
            if shutil.which("xclip"):
                completed = _run_command(["xclip", "-selection", "clipboard", "-o"], timeout_seconds=8)
                if completed.returncode == 0:
                    return {"status": "success", "text": completed.stdout}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "text": ""}
    return {"status": "error", "message": "Clipboard read is not available on this platform.", "text": ""}


def _clipboard_image_info(platform_tag: str) -> Dict[str, Any]:
    """Inspect clipboard formats after a composer selection is copied.

    Rich desktop composers commonly expose selected image chips through an HTML
    clipboard flavor and/or an image flavor.  Reading those formats gives the
    prompt builder a live attachment count without consulting its own cache.
    The operation is intentionally best-effort: text inspection remains useful
    when an OS clipboard backend does not expose image metadata.
    """
    tag = str(platform_tag or "").strip().lower()
    try:
        formats: List[str] = []
        html = ""
        has_image = False
        if tag == "windows":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$data=[System.Windows.Forms.Clipboard]::GetDataObject();"
                "$formats=@();$html='';$hasImage=$false;"
                "if($null -ne $data){"
                "$formats=@($data.GetFormats());"
                "$hasImage=[System.Windows.Forms.Clipboard]::ContainsImage();"
                "foreach($name in @('HTML Format','text/html','Html')){"
                "if($formats -contains $name){$value=$data.GetData($name);"
                "if($value -is [string]){$html=[string]$value};break}}}"
                "[pscustomobject]@{formats=$formats;html=$html;has_image=$hasImage} | ConvertTo-Json -Compress"
            )
            completed = _run_command(
                ["powershell", "-STA", "-NoProfile", "-Command", script],
                timeout_seconds=8,
            )
            if completed.returncode != 0:
                return {"status": "error", "image_count": 0, "source": "clipboard_formats", "message": completed.stderr.strip()}
            payload = _safe_json_loads(completed.stdout)
            if not isinstance(payload, dict):
                return {"status": "error", "image_count": 0, "source": "clipboard_formats", "message": "Clipboard format inspection returned invalid data."}
            raw_formats = payload.get("formats") or []
            formats = [str(item) for item in (raw_formats if isinstance(raw_formats, list) else [raw_formats])]
            html = str(payload.get("html") or "")
            has_image = bool(payload.get("has_image"))
        elif tag == "macos":
            html_result = _run_command(["pbpaste", "-Prefer", "html"], timeout_seconds=8)
            if html_result.returncode == 0:
                html = html_result.stdout or ""
                formats.append("text/html")
            if not html:
                png_result = _run_command(["pbpaste", "-Prefer", "png"], timeout_seconds=8)
                has_image = png_result.returncode == 0 and bool(png_result.stdout)
                if has_image:
                    formats.append("image/png")
        elif tag == "linux":
            type_result = None
            if shutil.which("wl-paste"):
                type_result = _run_command(["wl-paste", "--list-types"], timeout_seconds=8)
            elif shutil.which("xclip"):
                type_result = _run_command(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"], timeout_seconds=8)
            if type_result is not None and type_result.returncode == 0:
                formats = [line.strip() for line in type_result.stdout.splitlines() if line.strip()]
                has_image = any(item.lower().startswith("image/") for item in formats)
                if any(item.lower() in {"text/html", "html"} for item in formats):
                    if shutil.which("wl-paste"):
                        html_result = _run_command(["wl-paste", "--type", "text/html"], timeout_seconds=8)
                    else:
                        html_result = _run_command(["xclip", "-selection", "clipboard", "-t", "text/html", "-o"], timeout_seconds=8)
                    if html_result.returncode == 0:
                        html = html_result.stdout or ""
        else:
            return {"status": "error", "image_count": 0, "source": "clipboard_formats", "message": f"Unsupported platform: {tag}"}

        html_count = len(re.findall(r"<img\b", html, flags=re.IGNORECASE))
        image_format = any(
            token in item.lower()
            for item in formats
            for token in ("image/", "bitmap", "png", "jpeg", "jpg", "dib")
        )
        image_count = html_count or (1 if has_image or image_format else 0)
        return {
            "status": "success",
            "image_count": image_count,
            "source": "clipboard_html" if html_count else ("clipboard_image_format" if image_count else "clipboard_formats"),
            "formats": formats,
        }
    except Exception as exc:
        return {"status": "error", "image_count": 0, "source": "clipboard_formats", "message": str(exc)}


def _powershell_single_quoted(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _normalize_attachment_paths(paths: Optional[Sequence[str]]) -> Tuple[List[str], List[str]]:
    valid_paths: List[str] = []
    missing_paths: List[str] = []
    seen: set[str] = set()
    for raw_path in paths or []:
        cleaned = str(raw_path or "").strip().strip('"')
        if not cleaned:
            continue
        try:
            resolved = str(Path(cleaned).expanduser().resolve())
        except Exception:
            resolved = cleaned
        if resolved in seen:
            continue
        seen.add(resolved)
        if Path(resolved).is_file():
            valid_paths.append(resolved)
        else:
            missing_paths.append(resolved)
    return valid_paths, missing_paths


def list_recent_desktop_screenshot_paths(
    *,
    directory: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Return recent screenshot image paths from the user's Desktop for attachment tools.

    The files are not opened or inspected; this only returns local paths that the
    caller can pass to an ``attachment_paths`` argument.
    """
    root = Path(directory or "~/Desktop").expanduser()
    if not root.is_dir():
        return {"status": "error", "message": f"Directory not found: {root}", "attachment_paths": []}

    candidates: List[Tuple[float, int, Path]] = []
    for path in root.iterdir():
        if not path.is_file() or path.suffix.lower() not in _SCREENSHOT_EXTENSIONS:
            continue
        name = path.name.lower()
        if any(token in name for token in _SCREENSHOT_NAME_TOKENS):
            try:
                stat = path.stat()
            except OSError:
                continue
            candidates.append((stat.st_mtime, stat.st_size, path))

    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = candidates[: max(1, int(limit))]
    files = [
        {
            "path": str(path.resolve()),
            "name": path.name,
            "size_bytes": size_bytes,
            "modified_at_s": modified_at_s,
        }
        for modified_at_s, size_bytes, path in selected
    ]
    return {
        "status": "success",
        "directory": str(root.resolve()),
        "count": len(files),
        "attachment_paths": [item["path"] for item in files],
        "files": files,
    }


def get_desktop_app_release_status(
    agent_dir: Path,
    *,
    platform_tag: Optional[str] = None,
    screenshot_limit: int = 1,
) -> Dict[str, Any]:
    """Compact release gate payload for weak local models."""
    status = get_desktop_app_status(agent_dir, capture_screenshot=False)
    if status.get("status") != "success":
        return status

    selected_pack = status.get("asset_pack") or {}
    selected_pack_id = selected_pack.get("asset_pack_id")
    packs_payload = list_desktop_asset_packs(agent_dir, platform_tag=platform_tag or status.get("platform"))
    screenshots = list_recent_desktop_screenshot_paths(limit=screenshot_limit)
    return {
        "status": "success",
        "agent_name": status.get("agent_name"),
        "app_id": status.get("app_id"),
        "platform": status.get("platform"),
        "architecture": status.get("architecture"),
        "ready": status.get("ready"),
        "selected_asset_pack_id": selected_pack_id,
        "release_action_coverage": status.get("release_action_coverage"),
        "warnings": status.get("warnings") or [],
        "matching_asset_pack_count": packs_payload.get("count", 0),
        "desktop_screenshot_attachments": screenshots,
    }


def _clipboard_copy_files(paths: Sequence[str], platform_tag: str) -> Dict[str, Any]:
    valid_paths, missing_paths = _normalize_attachment_paths(paths)
    if not valid_paths:
        return {
            "status": "error",
            "message": "No existing local attachment files were provided.",
            "attached_paths": [],
            "missing_paths": missing_paths,
        }

    try:
        if platform_tag == "windows":
            add_lines = "\n".join(
                f"[void]$files.Add({_powershell_single_quoted(path)})"
                for path in valid_paths
            )
            script = (
                "Add-Type -AssemblyName System.Windows.Forms;\n"
                "$files = New-Object System.Collections.Specialized.StringCollection;\n"
                f"{add_lines}\n"
                "[System.Windows.Forms.Clipboard]::SetFileDropList($files);"
            )
            completed = _run_command(["powershell", "-STA", "-NoProfile", "-Command", script], timeout_seconds=10)
            if completed.returncode == 0:
                return {"status": "success", "attached_paths": valid_paths, "missing_paths": missing_paths}
            return {
                "status": "error",
                "message": completed.stderr.strip() or "Windows file clipboard operation failed.",
                "attached_paths": [],
                "missing_paths": missing_paths,
            }

        if platform_tag == "macos":
            file_refs = ", ".join(f"POSIX file {json.dumps(path, ensure_ascii=False)}" for path in valid_paths)
            script = f"set the clipboard to {{{file_refs}}}"
            completed = _run_command(["osascript", "-e", script], timeout_seconds=10)
            if completed.returncode == 0:
                return {"status": "success", "attached_paths": valid_paths, "missing_paths": missing_paths}
            return {
                "status": "error",
                "message": completed.stderr.strip() or "macOS file clipboard operation failed.",
                "attached_paths": [],
                "missing_paths": missing_paths,
            }

        if platform_tag == "linux":
            uri_list = "\n".join(Path(path).as_uri() for path in valid_paths) + "\n"
            if shutil.which("wl-copy"):
                completed = subprocess.run(
                    ["wl-copy", "--type", "text/uri-list"],
                    input=uri_list,
                    text=True,
                    capture_output=True,
                    timeout=10,
                    env=_scrubbed_subprocess_env(),
                )
                if completed.returncode == 0:
                    return {"status": "success", "attached_paths": valid_paths, "missing_paths": missing_paths}
            if shutil.which("xclip"):
                completed = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-t", "text/uri-list"],
                    input=uri_list,
                    text=True,
                    capture_output=True,
                    timeout=10,
                    env=_scrubbed_subprocess_env(),
                )
                if completed.returncode == 0:
                    return {"status": "success", "attached_paths": valid_paths, "missing_paths": missing_paths}
            return {
                "status": "error",
                "message": "Linux file clipboard needs wl-copy or xclip.",
                "attached_paths": [],
                "missing_paths": missing_paths,
            }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "attached_paths": [], "missing_paths": missing_paths}

    return {
        "status": "error",
        "message": "File clipboard attachment is not available on this platform.",
        "attached_paths": [],
        "missing_paths": missing_paths,
    }


def _macos_keystroke(
    *,
    key: Optional[str] = None,
    key_code: Optional[int] = None,
    text: Optional[str] = None,
    command: bool = False,
    modifiers: Optional[Sequence[str]] = None,
) -> bool:
    """Send a keystroke / typed text via macOS System Events.

    pyautogui's Command-modifier combos (Cmd+V, Cmd+A, Cmd+Enter, Cmd+Shift+G) are silently
    dropped on some macOS builds even when Accessibility is granted, so the keyboard-driven
    paste/select/dialog-navigation never reaches the target app. System Events keystrokes are
    reliable once Accessibility is granted to the responsible process, so we prefer them on
    macOS and keep pyautogui as a fallback. ``modifiers`` accepts any of
    command/shift/option/control.
    """
    mods = list(modifiers or [])
    if command and "command" not in mods:
        mods.append("command")
    if text is not None:
        action = f"keystroke {json.dumps(text)}"
    elif key is not None:
        action = f"keystroke {json.dumps(key)}"
    elif key_code is not None:
        action = f"key code {int(key_code)}"
    else:
        return False
    using = ""
    if mods:
        if len(mods) == 1:
            using = f" using {mods[0]} down"
        else:
            using = " using {" + ", ".join(f"{mod} down" for mod in mods) + "}"
    script = f"tell application \"System Events\" to {action}{using}"
    try:
        return _run_command(["osascript", "-e", script], timeout_seconds=8).returncode == 0
    except Exception:
        return False


def _paste_from_clipboard(pyautogui: Any, platform_tag: str) -> None:
    if platform_tag == "macos":
        if _macos_keystroke(key="v", command=True):
            return
        pyautogui.hotkey("command", "v")
    else:
        pyautogui.hotkey("ctrl", "v")


def _select_all(pyautogui: Any, platform_tag: str) -> None:
    if platform_tag == "macos":
        if _macos_keystroke(key="a", command=True):
            return
        pyautogui.hotkey("command", "a")
    else:
        pyautogui.hotkey("ctrl", "a")


def _move_caret_to_end(pyautogui: Any, platform_tag: str) -> None:
    """Put the caret after any existing composer text.

    Focusing the composer is a click, so the caret lands wherever that point happens to fall in
    the draft - which for a multi-chunk prompt is the middle of the text already there. Without
    this, appending a second chunk splices it into the first one.
    """
    if platform_tag == "macos":
        if _macos_keystroke(key="down", command=True):
            return
        pyautogui.hotkey("command", "down")
    else:
        pyautogui.hotkey("ctrl", "end")


def _copy_selected_prompt(pyautogui: Any, platform_tag: str) -> Dict[str, Any]:
    """Copy the focused composer selection without pasting it anywhere."""
    if platform_tag == "macos":
        if _macos_keystroke(key="c", command=True):
            return {"status": "success", "type": "hotkey", "keys": ["command", "c"]}
        pyautogui.hotkey("command", "c")
        return {"status": "success", "type": "hotkey", "keys": ["command", "c"], "fallback": True}
    pyautogui.hotkey("ctrl", "c")
    return {"status": "success", "type": "hotkey", "keys": ["ctrl", "c"]}


def _queue_prompt_hotkey(pyautogui: Any, platform_tag: str) -> List[str]:
    if platform_tag == "macos":
        if not _macos_keystroke(key_code=36, command=True):  # 36 = Return
            pyautogui.hotkey("command", "enter")
        return ["command", "enter"]
    pyautogui.hotkey("ctrl", "enter")
    return ["ctrl", "enter"]


def _normalize_selection_value(value: str) -> str:
    return " ".join(
        str(value or "")
        .strip()
        .lower()
        .replace("_", " ")
        .replace("-", " ")
        .split()
    )


def _list_windows_windows(process_hints: List[str]) -> List[Dict[str, Any]]:
    _ensure_interactive_desktop()
    normalized_hints = []
    for hint in process_hints:
        cleaned = str(hint or "").strip().lower()
        if not cleaned:
            continue
        normalized_hints.append(cleaned)
        if cleaned.endswith(".exe"):
            normalized_hints.append(cleaned[:-4])
    hint_array = ",".join(json.dumps(item) for item in sorted(set(normalized_hints)))
    # Primary: enumerate real top-level windows via EnumWindows and map each back to its owning
    # process. Get-Process.MainWindowTitle (the legacy path below) is empty for Electron/MSIX
    # apps like the ChatGPT Codex desktop app - their titled window is owned by a helper process
    # while the "main" process reports no window - so relying on it silently finds zero windows
    # and clicks then fall back to whole-screen scaling. EnumWindows + GetWindowThreadProcessId
    # is reliable for those apps. Cloaked (off-desktop / DWM-hidden) and tiny windows are skipped.
    enum_script = (
        "Add-Type @'\n"
        "using System;\n"
        "using System.Text;\n"
        "using System.Collections.Generic;\n"
        "using System.Runtime.InteropServices;\n"
        "public static class WinEnumAY {\n"
        "  [DllImport(\"user32.dll\")] static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);\n"
        "  delegate bool EnumWindowsProc(IntPtr h, IntPtr l);\n"
        "  [DllImport(\"user32.dll\")] static extern int GetWindowText(IntPtr h, StringBuilder s, int c);\n"
        "  [DllImport(\"user32.dll\")] static extern bool IsWindowVisible(IntPtr h);\n"
        "  [DllImport(\"user32.dll\")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);\n"
        "  [DllImport(\"user32.dll\")] static extern bool GetWindowRect(IntPtr h, out RECT r);\n"
        "  [DllImport(\"dwmapi.dll\")] static extern int DwmGetWindowAttribute(IntPtr h, int a, out int v, int s);\n"
        "  public struct RECT { public int Left, Top, Right, Bottom; }\n"
        "  static List<string> Rows;\n"
        "  static bool Cb(IntPtr h, IntPtr l) {\n"
        "    if (!IsWindowVisible(h)) return true;\n"
        "    var sb = new StringBuilder(512); GetWindowText(h, sb, 512);\n"
        "    string t = sb.ToString(); if (t.Length == 0) return true;\n"
        "    RECT r; GetWindowRect(h, out r);\n"
        "    if (r.Right-r.Left < 120 || r.Bottom-r.Top < 120) return true;\n"
        "    int cloaked = 0; try { DwmGetWindowAttribute(h, 14, out cloaked, 4); } catch {}\n"
        "    if (cloaked != 0) return true;\n"
        "    uint pid; GetWindowThreadProcessId(h, out pid);\n"
        "    Rows.Add(pid + \"\\u0001\" + r.Left + \"\\u0001\" + r.Top + \"\\u0001\" + r.Right + \"\\u0001\" + r.Bottom + \"\\u0001\" + t);\n"
        "    return true;\n"
        "  }\n"
        "  public static string[] Run() { Rows = new List<string>(); EnumWindows(Cb, IntPtr.Zero); return Rows.ToArray(); }\n"
        "}\n"
        "'@;\n"
        f"$names = @({hint_array});\n"
        "$procs = @{};\n"
        "Get-Process | ForEach-Object { $procs[[uint32]$_.Id] = $_.ProcessName };\n"
        "$out = @();\n"
        "foreach ($row in [WinEnumAY]::Run()) {\n"
        "  $p = $row.Split([char]1);\n"
        "  $wpid = [uint32]$p[0];\n"
        "  $pname = if ($procs.ContainsKey($wpid)) { $procs[$wpid] } else { '' };\n"
        "  $pl = $pname.ToLower();\n"
        "  if ($names.Count -eq 0 -or $names -contains $pl -or $names -contains ($pl + '.exe')) {\n"
        "    $out += [PSCustomObject]@{ ProcessName=$pname; MainWindowTitle=$p[5]; Id=$wpid; Left=[int]$p[1]; Top=[int]$p[2]; Right=[int]$p[3]; Bottom=[int]$p[4] }\n"
        "  }\n"
        "}\n"
        "$out | ConvertTo-Json -Depth 3"
    )
    legacy_script = (
        "Add-Type @'\n"
        "using System;\n"
        "using System.Runtime.InteropServices;\n"
        "public static class Win32Rect {\n"
        "  [DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);\n"
        "  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }\n"
        "}\n"
        "'@;\n"
        f"$names = @({hint_array});\n"
        "$windows = Get-Process | Where-Object {\n"
        "  $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -and (\n"
        "    $names.Count -eq 0 -or\n"
        "    $names -contains $_.ProcessName.ToLower() -or\n"
        "    $names -contains (($_.ProcessName + '.exe').ToLower())\n"
        "  )\n"
        "} | ForEach-Object {\n"
        "  $rect = New-Object 'Win32Rect+RECT'\n"
        "  [void][Win32Rect]::GetWindowRect($_.MainWindowHandle, [ref]$rect)\n"
        "  [PSCustomObject]@{\n"
        "    ProcessName = $_.ProcessName\n"
        "    MainWindowTitle = $_.MainWindowTitle\n"
        "    Id = $_.Id\n"
        "    Left = $rect.Left\n"
        "    Top = $rect.Top\n"
        "    Right = $rect.Right\n"
        "    Bottom = $rect.Bottom\n"
        "  }\n"
        "} | ConvertTo-Json -Depth 3"
    )
    windows: List[Dict[str, Any]] = []
    for script in (enum_script, legacy_script):
        completed = _run_command(["powershell", "-NoProfile", "-Command", script], timeout_seconds=10)
        if completed.returncode != 0:
            continue
        payload = _safe_json_loads(completed.stdout)
        if payload is None:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("MainWindowTitle") or "").strip()
            if not title:
                continue
            windows.append(
                {
                    "title": title,
                    "process_name": str(item.get("ProcessName") or "").strip(),
                    "pid": int(item.get("Id") or 0),
                    "bounds": [
                        int(item.get("Left") or 0),
                        int(item.get("Top") or 0),
                        int(item.get("Right") or 0),
                        int(item.get("Bottom") or 0),
                    ],
                }
            )
        if windows:
            return windows

    try:
        import pygetwindow as gw  # type: ignore[import-not-found]

        for window in gw.getAllWindows():
            title = str(getattr(window, "title", "") or "").strip()
            if not title:
                continue
            windows.append(
                {
                    "title": title,
                    "process_name": None,
                    "pid": None,
                    "bounds": [int(window.left), int(window.top), int(window.right), int(window.bottom)],
                }
            )
    except Exception:
        pass
    return windows


def _window_bounds_windows(title_hint: Optional[str], process_name: Optional[str]) -> Optional[Tuple[int, int, int, int]]:
    _ensure_interactive_desktop()
    windows = _list_windows_windows([process_name or ""])
    if process_name:
        for window in windows:
            if str(window.get("process_name") or "").strip().lower() in {process_name.lower(), process_name.lower().replace(".exe", "")}:
                bounds = window.get("bounds")
                if isinstance(bounds, list) and len(bounds) == 4:
                    return tuple(int(item) for item in bounds)
    if title_hint:
        for window in windows:
            if title_hint.lower() in str(window.get("title") or "").lower():
                bounds = window.get("bounds")
                if isinstance(bounds, list) and len(bounds) == 4:
                    return tuple(int(item) for item in bounds)
        try:
            import pygetwindow as gw  # type: ignore[import-not-found]

            matches = gw.getWindowsWithTitle(title_hint)
            if matches:
                window = matches[0]
                return (int(window.left), int(window.top), int(window.right), int(window.bottom))
        except Exception:
            pass
    script = (
        "Add-Type @'\n"
        "using System;\n"
        "using System.Runtime.InteropServices;\n"
        "using System.Text;\n"
        "public static class Win32 {\n"
        "  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);\n"
        "  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);\n"
        "  [DllImport(\"user32.dll\")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);\n"
        "  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr hWnd);\n"
        "  [DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);\n"
        "  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }\n"
        "}\n"
        "'@;\n"
        f"$needle = {json.dumps(title_hint or '')};\n"
        "$result = $null;\n"
        "[Win32]::EnumWindows({ param($h,$l)\n"
        "  if (-not [Win32]::IsWindowVisible($h)) { return $true }\n"
        "  $sb = New-Object System.Text.StringBuilder 512\n"
        "  [void][Win32]::GetWindowText($h, $sb, $sb.Capacity)\n"
        "  $title = $sb.ToString()\n"
        "  if ($title -and $title -like ('*' + $needle + '*')) {\n"
        "    $rect = New-Object 'Win32+RECT'\n"
        "    [void][Win32]::GetWindowRect($h, [ref]$rect)\n"
        "    $result = @{ left=$rect.Left; top=$rect.Top; right=$rect.Right; bottom=$rect.Bottom; title=$title }\n"
        "    return $false\n"
        "  }\n"
        "  return $true\n"
        "}, [IntPtr]::Zero) | Out-Null;\n"
        "$result | ConvertTo-Json -Depth 3"
    )
    completed = _run_command(["powershell", "-NoProfile", "-Command", script], timeout_seconds=8)
    if completed.returncode != 0:
        return None
    payload = _safe_json_loads(completed.stdout)
    if not isinstance(payload, dict):
        return None
    return (
        int(payload.get("left") or 0),
        int(payload.get("top") or 0),
        int(payload.get("right") or 0),
        int(payload.get("bottom") or 0),
    )


def _list_windows_macos(process_name: str) -> List[Dict[str, Any]]:
    hide_macos_dock_icon()
    script = (
        "tell application \"System Events\"\n"
        f"  if exists process {json.dumps(process_name)} then\n"
        f"    tell process {json.dumps(process_name)}\n"
        "      set windowTitles to {}\n"
        "      try\n"
        "        set windowTitles to name of every window\n"
        "      end try\n"
        "      return windowTitles\n"
        "    end tell\n"
        "  end if\n"
        "end tell"
    )
    # from __debug_provenance_p__ import submit
    completed = _run_command(["osascript", "-e", script], timeout_seconds=8)
    if completed.returncode != 0:
        return []
    stdout = completed.stdout.strip()
    if not stdout:
        return []
    return [{"title": title.strip(), "process_name": process_name, "pid": None, "bounds": None} for title in stdout.split(",") if title.strip()]


def _window_bounds_macos(process_name: str) -> Optional[Tuple[int, int, int, int]]:
    script = (
        "tell application \"System Events\"\n"
        f"  if exists process {json.dumps(process_name)} then\n"
        f"    tell process {json.dumps(process_name)}\n"
        "      if (count of windows) > 0 then\n"
        "        set p to position of front window\n"
        "        set s to size of front window\n"
        "        return (item 1 of p as text) & \",\" & (item 2 of p as text) & \",\" & (item 1 of s as text) & \",\" & (item 2 of s as text)\n"
        "      end if\n"
        "    end tell\n"
        "  end if\n"
        "end tell"
    )
    completed = _run_command(["osascript", "-e", script], timeout_seconds=8)
    if completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    if not raw:
        return None
    try:
        left_s, top_s, width_s, height_s = [item.strip() for item in raw.split(",", 3)]
        left = int(float(left_s))
        top = int(float(top_s))
        width = int(float(width_s))
        height = int(float(height_s))
        return (left, top, left + width, top + height)
    except Exception:
        return None


def _list_windows_linux() -> List[Dict[str, Any]]:
    if not shutil.which("wmctrl"):
        return []
    completed = _run_command(["wmctrl", "-lp"], timeout_seconds=8)
    if completed.returncode != 0:
        return []
    windows = []
    for line in completed.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        _, _, pid, _, title = parts
        windows.append(
            {
                "title": title.strip(),
                "process_name": None,
                "pid": int(pid or 0),
                "bounds": None,
            }
        )
    return windows


def _window_bounds_linux(title_hint: str) -> Optional[Tuple[int, int, int, int]]:
    if not shutil.which("wmctrl"):
        return None
    completed = _run_command(["wmctrl", "-lG"], timeout_seconds=8)
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        _, _, x, y, width, height, _, title = parts
        if title_hint.lower() in title.lower():
            try:
                left = int(x)
                top = int(y)
                w = int(width)
                h = int(height)
                return (left, top, left + w, top + h)
            except Exception:
                return None
    return None


def _list_windows(platform_tag: str, process_hints: List[str]) -> List[Dict[str, Any]]:
    if platform_tag == "windows":
        return _list_windows_windows(process_hints)
    if platform_tag == "macos":
        windows: List[Dict[str, Any]] = []
        for process_name in process_hints:
            windows.extend(_list_windows_macos(process_name))
        return windows
    if platform_tag == "linux":
        return _list_windows_linux()
    return []


def _find_best_window(windows: List[Dict[str, Any]], title_hints: List[str]) -> Optional[Dict[str, Any]]:
    if not windows:
        return None
    if not title_hints:
        return windows[0]
    for hint in title_hints:
        for window in windows:
            if hint.lower() in str(window.get("title") or "").lower():
                return window
    return windows[0]


def _focus_window(platform_tag: str, title_hints: List[str], process_hints: List[str]) -> bool:
    if platform_tag == "windows":
        _ensure_interactive_desktop()
        # Electron/MSIX apps can keep their only titled window minimized at
        # (-32000, -32000).  WScript.AppActivate reports success for that
        # window but does not reliably restore it, which makes every
        # coordinate action land off-screen.  Restore the matching top-level
        # window through Win32 first, then keep the older AppActivate fallback
        # for legacy desktop builds.
        focus_hints = title_hints + process_hints
        hint_array = ",".join(json.dumps(item) for item in focus_hints if str(item or "").strip())
        restore_script = (
            "Add-Type @'\n"
            "using System;\n"
            "using System.Text;\n"
            "using System.Runtime.InteropServices;\n"
            "public static class WinFocusAY {\n"
            "  [DllImport(\"user32.dll\")] static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);\n"
            "  delegate bool EnumWindowsProc(IntPtr h, IntPtr l);\n"
            "  [DllImport(\"user32.dll\")] static extern int GetWindowText(IntPtr h, StringBuilder s, int c);\n"
            "  [DllImport(\"user32.dll\")] static extern bool IsWindowVisible(IntPtr h);\n"
            "  [DllImport(\"user32.dll\")] static extern bool ShowWindowAsync(IntPtr h, int command);\n"
            "  [DllImport(\"user32.dll\")] static extern bool SetForegroundWindow(IntPtr h);\n"
            "  public static bool Activate(string[] hints) {\n"
            "    IntPtr found = IntPtr.Zero;\n"
            "    EnumWindows((h, l) => {\n"
            "      if (!IsWindowVisible(h)) return true;\n"
            "      var sb = new StringBuilder(512); GetWindowText(h, sb, 512);\n"
            "      string title = sb.ToString();\n"
            "      foreach (string hint in hints) {\n"
            "        if (!String.IsNullOrWhiteSpace(hint) && title.IndexOf(hint, StringComparison.OrdinalIgnoreCase) >= 0) { found = h; return false; }\n"
            "      }\n"
            "      return true;\n"
            "    }, IntPtr.Zero);\n"
            "    if (found == IntPtr.Zero) return false;\n"
            "    ShowWindowAsync(found, 9);\n"
            "    SetForegroundWindow(found);\n"
            "    return true;\n"
            "  }\n"
            "}\n"
            "'@;\n"
            f"$hints = @({hint_array});\n"
            "if ([WinFocusAY]::Activate([string[]]$hints)) { exit 0 }\n"
            "exit 1"
        )
        restored = _run_command(["powershell", "-NoProfile", "-Command", restore_script], timeout_seconds=8)
        if restored.returncode == 0:
            return True
        for process_name in process_hints:
            script = (
                f"$p = Get-Process | Where-Object {{ $_.MainWindowHandle -ne 0 -and $_.ProcessName -ieq {json.dumps(process_name.replace('.exe', ''))} }} | Select-Object -First 1; "
                "if ($p) { $ws = New-Object -ComObject WScript.Shell; [void]$ws.AppActivate($p.Id) }"
            )
            completed = _run_command(["powershell", "-NoProfile", "-Command", script], timeout_seconds=6)
            if completed.returncode == 0:
                return True
        for hint in title_hints + process_hints:
            script = f"$ws = New-Object -ComObject WScript.Shell; [void]$ws.AppActivate({json.dumps(hint)});"
            completed = _run_command(["powershell", "-NoProfile", "-Command", script], timeout_seconds=6)
            if completed.returncode == 0:
                return True
        return False
    if platform_tag == "macos":
        for hint in process_hints + title_hints:
            completed = _run_command(["open", "-a", hint], timeout_seconds=6)
            if completed.returncode == 0:
                script = f'''
tell application "System Events"
    repeat with p in (every process whose background only is false)
        if (name of p) contains {json.dumps(hint)} then
            set frontmost of p to true
            try
                perform action "AXRaise" of window 1 of p
            end try
            exit repeat
        end if
    end repeat
end tell
'''
                _run_command(["osascript", "-e", script], timeout_seconds=4)
                return True

        for hint in process_hints + title_hints:
            script = f'''
tell application "System Events"
    repeat with p in (every process whose background only is false)
        if (name of p) contains {json.dumps(hint)} then
            set frontmost of p to true
            try
                perform action "AXRaise" of window 1 of p
            end try
            return "ok"
        end if
    end repeat
end tell
'''
            completed = _run_command(["osascript", "-e", script], timeout_seconds=6)
            if completed.returncode == 0 and "ok" in str(completed.stdout or "").lower():
                return True

        for hint in process_hints + title_hints:
            completed = _run_command(["osascript", "-e", f"tell application {json.dumps(hint)} to activate"], timeout_seconds=6)
            if completed.returncode == 0:
                return True
        return False
    if platform_tag == "linux":
        if shutil.which("wmctrl"):
            for hint in title_hints + process_hints:
                completed = _run_command(["wmctrl", "-a", hint], timeout_seconds=6)
                if completed.returncode == 0:
                    return True
        if shutil.which("xdotool"):
            for hint in title_hints + process_hints:
                completed = _run_command(["xdotool", "search", "--name", hint, "windowactivate"], timeout_seconds=6)
                if completed.returncode == 0:
                    return True
        return False
    return False


def _launch_commands_for_platform(manifest: Dict[str, Any], platform_tag: str) -> List[Dict[str, Any]]:
    commands = (manifest.get("launch_commands") or {}).get(platform_tag) or []
    if commands:
        return list(commands)
    return list((manifest.get("launch_commands") or {}).get("any") or [])


def _expand_command_args(args: Iterable[str]) -> List[str]:
    expanded = []
    for arg in args:
        expanded.append(os.path.expandvars(str(arg)))
    return expanded


def _launch_application(manifest: Dict[str, Any], platform_tag: str) -> Dict[str, Any]:
    commands = _launch_commands_for_platform(manifest, platform_tag)
    for command in commands:
        args = _expand_command_args(command.get("args") or [])
        if not args:
            continue
        executable = args[0]
        if not os.path.isabs(executable) and not shutil.which(executable):
            continue
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            subprocess.Popen(args, env=_scrubbed_subprocess_env(), creationflags=creationflags)
            return {
                "status": "success",
                "launched": True,
                "command": args,
            }
        except Exception as exc:
            LOGGER.warning("Failed to launch %s via %s: %s", manifest.get("agent_name"), args, exc)
    return {
        "status": "error",
        "launched": False,
        "message": "No working launcher command succeeded for this platform.",
        "commands": commands,
    }


def _window_bounds_for_hint(platform_tag: str, title_hint: Optional[str], process_name: Optional[str]) -> Optional[Tuple[int, int, int, int]]:
    if platform_tag == "windows":
        return _window_bounds_windows(title_hint, process_name)
    if platform_tag == "macos" and process_name:
        return _window_bounds_macos(process_name)
    if platform_tag == "linux" and title_hint:
        return _window_bounds_linux(title_hint)
    return None


def _resolve_target(pack: Dict[str, Any], target_id: str) -> Optional[Dict[str, Any]]:
    for target in pack.get("targets") or []:
        if str(target.get("target_id") or "").strip() == str(target_id or "").strip():
            return target
    return None


def _target_image_path(manifest: Dict[str, Any], target: Dict[str, Any]) -> Optional[Path]:
    raw_path = str(target.get("expected_image_path") or target.get("reference_sprite") or "").strip()
    if not raw_path:
        return None
    candidate = Path(raw_path)
    assets_root_value = str(target.get("_desktop_assets_root") or manifest.get("assets_root") or "").strip()
    if not assets_root_value:
        return None
    assets_root = Path(assets_root_value).resolve()
    if not candidate.is_absolute():
        candidate = assets_root / candidate
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(assets_root):
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _target_match_image(manifest: Dict[str, Any], target: Dict[str, Any]) -> Any:
    image_path = _target_image_path(manifest, target)
    if image_path is None or not image_path.is_file():
        return None
    crop_box = target.get("image_template_crop_box")
    if isinstance(crop_box, list) and len(crop_box) == 4:
        try:
            from PIL import Image

            left, top, right, bottom = [int(float(item)) for item in crop_box]
            if right > left and bottom > top:
                with Image.open(image_path) as image:
                    return image.crop((left, top, right, bottom)).copy()
        except Exception as exc:
            LOGGER.debug("Could not crop target image template %s: %s", image_path, exc)
    return str(image_path)


def _missing_target_image_count(manifest: Dict[str, Any], pack: Optional[Dict[str, Any]]) -> int:
    if not isinstance(pack, dict):
        return 0
    missing = set()
    for target in pack.get("targets") or []:
        if not isinstance(target, dict):
            continue
        image_path = _target_image_path(manifest, target)
        if image_path is not None and not image_path.is_file():
            target_id = str(target.get("target_id") or image_path.as_posix())
            missing.add(target_id)
    return len(missing)


def _locate_target_by_image(
    pyautogui: Any,
    manifest: Dict[str, Any],
    target: Dict[str, Any],
    *,
    window_bounds: Optional[Tuple[int, int, int, int]],
) -> Optional[Tuple[int, int]]:
    image = _target_match_image(manifest, target)
    if image is None:
        return None

    try:
        from PIL import ImageGrab
        screenshot = ImageGrab.grab(all_screens=True)
        origin_x, origin_y, _vw, _vh = _virtual_screen_rect()
    except Exception as exc:
        LOGGER.debug("Could not grab all screens for image match: %s", exc)
        return None

    region = None
    if bool(target.get("image_match_within_window", True)) and window_bounds is not None:
        left, top, right, bottom = window_bounds
        width = max(1, right - left)
        height = max(1, bottom - top)

        # Narrow the search to the target's own declared box when it has one. Searching the
        # whole window lets a small, generic sprite match anywhere: a square "send" icon
        # happily matches the title-bar maximise button, and a magnifier matches any other
        # magnifier. The engine then clicks a confidently-located wrong control, which looks
        # exactly like random clicking. A generous margin keeps normal UI drift matchable.
        search_box = _target_normalized_box(target, pack=None, window_bounds=window_bounds)
        if search_box is None:
            # No declared box, but the target still knows roughly where it lives (anchored_point
            # or click_point). Search a neighbourhood around that instead of the whole window:
            # most packs describe targets as points, and leaving those unbounded is what let a
            # generic sprite match a lookalike control on the far side of the window.
            anchor = _compute_click_point(
                target,
                coordinate_space="window",
                window_bounds=window_bounds,
                screen_size=(0, 0),
                pack=None,
            )
            if anchor is not None:
                radius = float(target.get("image_search_radius") or 0.12)
                cx = (anchor[0] - window_bounds[0]) / float(width)
                cy = (anchor[1] - window_bounds[1]) / float(height)
                search_box = [cx - radius, cy - radius, cx + radius, cy + radius]
        if search_box is not None:
            margin = float(target.get("image_search_margin") or 0.06)
            x0 = min(max(search_box[0] - margin, 0.0), 1.0)
            y0 = min(max(search_box[1] - margin, 0.0), 1.0)
            x1 = min(max(search_box[2] + margin, 0.0), 1.0)
            y1 = min(max(search_box[3] + margin, 0.0), 1.0)
            if x1 > x0 and y1 > y0:
                left = window_bounds[0] + int(width * x0)
                top = window_bounds[1] + int(height * y0)
                width = max(1, int(width * (x1 - x0)))
                height = max(1, int(height * (y1 - y0)))

        # Adjust absolute window bounds to image-relative coordinates
        region = (int(left - origin_x), int(top - origin_y), int(width), int(height))

    confidence_raw = target.get("image_match_confidence")
    grayscale = bool(target.get("image_match_grayscale", True))
    locate_kwargs: Dict[str, Any] = {"grayscale": grayscale}
    if region is not None:
        locate_kwargs["region"] = region
    if confidence_raw is not None:
        try:
            locate_kwargs["confidence"] = float(confidence_raw)
        except Exception:
            pass

    try:
        found = pyautogui.locate(image, screenshot, **locate_kwargs)
    except Exception as exc:
        if "confidence" not in locate_kwargs:
            LOGGER.debug("Image match failed for %s: %s", target.get("target_id"), exc)
            return None
        locate_kwargs.pop("confidence", None)
        try:
            found = pyautogui.locate(image, screenshot, **locate_kwargs)
        except Exception as fallback_exc:
            LOGGER.debug("Image match fallback failed for %s: %s", target.get("target_id"), fallback_exc)
            return None
    if not found:
        return None
    return (int(found.left + found.width / 2 + origin_x), int(found.top + found.height / 2 + origin_y))


_ANCHOR_ORIGINS_X = ("left", "right", "center", "content_center")
_ANCHOR_ORIGINS_Y = ("top", "bottom", "center")


def _pack_layout(pack: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    layout = pack.get("layout") if isinstance(pack, dict) else None
    return layout if isinstance(layout, dict) else {}


def _anchor_base(origin: str, extent: float, content_inset: float, right_inset: float = 0.0) -> Tuple[float, float]:
    """Return (base_pixel, offset_sign) for an anchor origin along one axis."""
    if origin in ("right", "bottom"):
        return extent - right_inset, -1.0
    if origin == "center":
        return (extent - right_inset) / 2.0, 1.0
    if origin == "content_center":
        return (content_inset + (extent - right_inset)) / 2.0, 1.0
    return 0.0, 1.0  # left / top


def _detect_right_sidebar_inset_px(
    window_bounds: Optional[Tuple[int, int, int, int]],
    pack: Optional[Dict[str, Any]] = None,
) -> float:
    """Return the pixel width of any open right panel (e.g., Codex Environment / Pin Summary sidebar)."""
    if window_bounds is None:
        return 0.0
    left, top, right, bottom = window_bounds
    width = float(max(1, right - left))
    height = float(max(1, bottom - top))
    if width < 500:
        return 0.0

    # Opt-in per pack. This used to default to 310px for ANY pack, so every anchored
    # coordinate - in apps with no right panel at all, like Claude - was silently shifted
    # 310px left whenever the two probe pixels happened to differ, which is almost always in a
    # window with content. Only packs that declare the inset get the adjustment.
    layout = _pack_layout(pack)
    try:
        configured_inset = float(layout.get("content_right_inset_px") or 0.0)
    except (TypeError, ValueError):
        configured_inset = 0.0
    if configured_inset <= 0.0:
        return 0.0

    probe_x = width - 180.0
    probe_y = min(80.0, height * 0.1)
    samples = _sample_window_pixels(window_bounds, [(probe_x, probe_y), (width - 400.0, probe_y)])
    if samples and len(samples) == 2 and samples[0] is not None and samples[1] is not None:
        right_panel_rgb, chat_area_rgb = samples[0], samples[1]
        diff = sum(abs(right_panel_rgb[i] - chat_area_rgb[i]) for i in range(3))
        if diff > 15:
            return configured_inset

    return 0.0


def _resolve_anchor_axis(
    spec: Any,
    *,
    extent: float,
    content_inset: float,
    right_inset: float = 0.0,
    axis: str,
) -> Optional[float]:
    """Resolve one axis of an anchored spec to a pixel coordinate inside the window.

    ``spec`` is either a bare number (a pixel offset from the left/top edge) or a dict of
    ``{"from": <origin>, "offset_px": <n>}``. On the x axis a target may additionally declare
    a flyout mirror: the app flips a submenu to the left of its parent menu once it no longer
    fits on the right, so ``fallback_offset_px`` replaces ``offset_px`` as soon as the room
    remaining to the right of ``room_anchor_offset_px`` drops below ``requires_room_right_px``.
    """
    if isinstance(spec, bool):
        return None
    if isinstance(spec, (int, float)):
        return float(spec)
    if not isinstance(spec, dict):
        return None
    default_origin = "left" if axis == "x" else "top"
    origin = str(spec.get("from") or default_origin).strip().lower()
    if origin not in (_ANCHOR_ORIGINS_X if axis == "x" else _ANCHOR_ORIGINS_Y):
        origin = default_origin
    try:
        offset = float(spec.get("offset_px") or 0.0)
    except (TypeError, ValueError):
        return None

    base, sign = _anchor_base(origin, extent, content_inset, right_inset=right_inset)

    room_required = spec.get("requires_room_right_px")
    if axis == "x" and room_required is not None:
        try:
            parent_edge = base + sign * float(spec.get("room_anchor_offset_px") or 0.0)
            if (extent - parent_edge) < float(room_required):
                offset = float(spec.get("fallback_offset_px", offset))
        except (TypeError, ValueError):
            pass

    return base + sign * offset


def _anchored_window_point(
    target: Dict[str, Any],
    *,
    pack: Optional[Dict[str, Any]],
    window_bounds: Optional[Tuple[int, int, int, int]],
) -> Optional[Tuple[int, int]]:
    """Resolve an ``anchored_point`` target against the live window rect.

    Anchored points exist because this app's chrome does not scale with the window: the
    sidebar keeps a fixed pixel width and the composer row stays a fixed distance above the
    bottom edge. A plain top-left fraction therefore drifts onto the wrong control as soon as
    the window is a different size than the one the pack was captured at - which is how a
    composer click can land on the message action row above it instead.
    """
    anchored = target.get("anchored_point")
    if not isinstance(anchored, dict) or window_bounds is None:
        return None
    left, top, right, bottom = window_bounds
    width = float(max(1, right - left))
    height = float(max(1, bottom - top))
    try:
        inset = float(_pack_layout(pack).get("content_left_inset_px") or 0.0)
    except (TypeError, ValueError):
        inset = 0.0
    try:
        right_inset = _detect_right_sidebar_inset_px(window_bounds, pack=pack)
    except Exception:
        right_inset = 0.0
    x = _resolve_anchor_axis(anchored.get("x"), extent=width, content_inset=inset, right_inset=right_inset, axis="x")
    y = _resolve_anchor_axis(anchored.get("y"), extent=height, content_inset=0.0, right_inset=0.0, axis="y")
    if x is None or y is None:
        return None
    x = min(max(x, 1.0), width - 1.0)
    y = min(max(y, 1.0), height - 1.0)
    return (int(left + x), int(top + y))


def _target_normalized_box(
    target: Optional[Dict[str, Any]],
    *,
    pack: Optional[Dict[str, Any]] = None,
    window_bounds: Optional[Tuple[int, int, int, int]] = None,
) -> Optional[List[float]]:
    """Window-normalized box for a target, resolving ``anchored_box`` before ``normalized_box``."""
    if not isinstance(target, dict):
        return None
    anchored = target.get("anchored_box")
    if isinstance(anchored, dict) and window_bounds is not None:
        left, top, right, bottom = window_bounds
        width = float(max(1, right - left))
        height = float(max(1, bottom - top))
        try:
            inset = float(_pack_layout(pack).get("content_left_inset_px") or 0.0)
        except (TypeError, ValueError):
            inset = 0.0
        try:
            right_inset = _detect_right_sidebar_inset_px(window_bounds, pack=pack)
        except Exception:
            right_inset = 0.0
        xs = anchored.get("x")
        ys = anchored.get("y")
        if isinstance(xs, list) and len(xs) == 2 and isinstance(ys, list) and len(ys) == 2:
            resolved = [
                _resolve_anchor_axis(xs[0], extent=width, content_inset=inset, right_inset=right_inset, axis="x"),
                _resolve_anchor_axis(ys[0], extent=height, content_inset=0.0, right_inset=0.0, axis="y"),
                _resolve_anchor_axis(xs[1], extent=width, content_inset=inset, right_inset=right_inset, axis="x"),
                _resolve_anchor_axis(ys[1], extent=height, content_inset=0.0, right_inset=0.0, axis="y"),
            ]
            if all(value is not None for value in resolved):
                x0, y0, x1, y1 = [float(value) for value in resolved]  # type: ignore[arg-type]
                return [
                    min(max(x0 / width, 0.0), 1.0),
                    min(max(y0 / height, 0.0), 1.0),
                    min(max(x1 / width, 0.0), 1.0),
                    min(max(y1 / height, 0.0), 1.0),
                ]
    box = target.get("normalized_box")
    if isinstance(box, list) and len(box) == 4:
        return [float(item) for item in box]
    return None


def _compute_click_point(
    target: Dict[str, Any],
    *,
    coordinate_space: str,
    window_bounds: Optional[Tuple[int, int, int, int]],
    screen_size: Tuple[int, int],
    pack: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[int, int]]:
    if coordinate_space == "window":
        anchored_point = _anchored_window_point(target, pack=pack, window_bounds=window_bounds)
        if anchored_point is not None:
            return anchored_point

    click_point = target.get("click_point")
    if not isinstance(click_point, list) or len(click_point) != 2:
        normalized_box = _target_normalized_box(target, pack=pack, window_bounds=window_bounds)
        if normalized_box is not None:
            left, top, right, bottom = normalized_box
            click_point = [left + ((right - left) / 2.0), top + ((bottom - top) / 2.0)]
        else:
            return None

    x_norm = float(click_point[0])
    y_norm = float(click_point[1])
    if coordinate_space == "window" and window_bounds is not None:
        left, top, right, bottom = window_bounds
        width = max(1, right - left)
        height = max(1, bottom - top)
        return (int(left + (width * x_norm)), int(top + (height * y_norm)))
    width, height = screen_size
    if width <= 0 or height <= 0:
        return None
    return (int(width * x_norm), int(height * y_norm))


def _resolve_click_point_for_target(
    pyautogui: Any,
    manifest: Dict[str, Any],
    pack: Dict[str, Any],
    target: Dict[str, Any],
    *,
    coordinate_space: str,
    window_bounds: Optional[Tuple[int, int, int, int]],
    screen_size: Tuple[int, int],
    prefer_image_match: bool = True,
) -> Optional[Tuple[int, int]]:
    if prefer_image_match:
        image_point = _locate_target_by_image(pyautogui, manifest, target, window_bounds=window_bounds)
        if image_point is not None:
            return image_point
    return _compute_click_point(
        target,
        coordinate_space=coordinate_space,
        window_bounds=window_bounds,
        screen_size=screen_size,
        pack=pack,
    )


def _sample_window_pixels(
    window_bounds: Optional[Tuple[int, int, int, int]],
    points: Sequence[Tuple[float, float]],
) -> List[Optional[Tuple[int, int, int]]]:
    """Read RGB values at window-relative points without writing a screenshot to disk."""
    if window_bounds is None or not points:
        return [None for _ in points]
    try:
        from PIL import ImageGrab

        left, top, right, bottom = window_bounds
        origin_x, origin_y, _vw, _vh = _virtual_screen_rect()
        image = ImageGrab.grab(all_screens=True).convert("RGB")
        width, height = max(1, right - left), max(1, bottom - top)
        samples: List[Optional[Tuple[int, int, int]]] = []
        for px, py in points:
            ix = int(left + min(max(px, 0), width - 1) - origin_x)
            iy = int(top + min(max(py, 0), height - 1) - origin_y)
            if 0 <= ix < image.width and 0 <= iy < image.height:
                samples.append(tuple(image.getpixel((ix, iy))))  # type: ignore[arg-type]
            else:
                samples.append(None)
        return samples
    except Exception as exc:
        LOGGER.debug("Could not sample window pixels: %s", exc)
        return [None for _ in points]


def _anchored_window_offsets(
    spec: Any,
    *,
    pack: Optional[Dict[str, Any]],
    window_bounds: Optional[Tuple[int, int, int, int]],
) -> Optional[Tuple[float, float]]:
    """Resolve an anchored {x, y} spec into window-relative pixels."""
    point = _anchored_window_point({"anchored_point": spec}, pack=pack, window_bounds=window_bounds)
    if point is None or window_bounds is None:
        return None
    return (float(point[0] - window_bounds[0]), float(point[1] - window_bounds[1]))


def _modal_scrim_present(prepared: Dict[str, Any], controls: Dict[str, Any]) -> bool:
    """True when the app has dimmed its background behind a modal dialog.

    Codex greys the whole window (255 -> ~221) while a dialog is up, which is a cheap,
    dialog-agnostic way to notice one without OCR.
    """
    probes = controls.get("scrim_probes")
    if not isinstance(probes, list) or not probes:
        return False
    pack = prepared["pack"]
    window_bounds = prepared.get("window_bounds")
    points = []
    for probe in probes:
        offsets = _anchored_window_offsets(probe, pack=pack, window_bounds=window_bounds)
        if offsets is not None:
            points.append(offsets)
    if not points:
        return False
    try:
        threshold = float(controls.get("scrim_max_brightness") or 245.0)
    except (TypeError, ValueError):
        threshold = 245.0
    samples = _sample_window_pixels(window_bounds, points)
    readable = [s for s in samples if s is not None]
    if not readable:
        return False
    return all((sum(sample) / 3.0) < threshold for sample in readable)


def _dialog_signature_matches(
    prepared: Dict[str, Any],
    dialog: Dict[str, Any],
) -> bool:
    signature = dialog.get("signature")
    if not isinstance(signature, dict):
        return False
    offsets = _anchored_window_offsets(
        signature.get("point"),
        pack=prepared["pack"],
        window_bounds=prepared.get("window_bounds"),
    )
    if offsets is None:
        return False
    expected = signature.get("rgb")
    if not isinstance(expected, list) or len(expected) != 3:
        return False
    try:
        tolerance = float(signature.get("tolerance") or 40.0)
    except (TypeError, ValueError):
        tolerance = 40.0
    sample = _sample_window_pixels(prepared.get("window_bounds"), [offsets])[0]
    if sample is None:
        return False
    return all(abs(int(sample[i]) - int(expected[i])) <= tolerance for i in range(3))


def handle_desktop_modal_dialogs(
    prepared: Dict[str, Any],
    *,
    intent: Optional[str] = None,
) -> Dict[str, Any]:
    """Dismiss any modal dialog the app raised in response to the last action.

    Two of these interrupt normal operation: the "Turn on Full Access?" consent sheet, which
    blocks the approval-mode change until confirmed, and the "Introducing <model>" promo, whose
    default button would swap the model out from under a caller that just picked one.

    A dialog is only auto-confirmed when the caller's own request is what raised it (``intent``
    matching the dialog's ``requires_intent``) - so the Full Access consent is confirmed when
    full access was explicitly requested, and otherwise falls through to the default dismissal,
    which cancels. Anything unrecognised gets the default dismissal too, never a blind click on
    whatever button happens to be under a coordinate.
    """
    pack = prepared["pack"]
    controls = pack.get("dialog_controls")
    if not isinstance(controls, dict):
        return {"status": "skipped", "reason": "pack defines no dialog_controls"}

    pyautogui = prepared["pyautogui"]
    time.sleep(float(controls.get("post_action_delay_seconds") or 0.6))
    try:
        max_dismissals = int(controls.get("max_dismissals") or 3)
    except (TypeError, ValueError):
        max_dismissals = 3

    dialogs = [d for d in (controls.get("dialogs") or []) if isinstance(d, dict)]
    default_action = controls.get("default_action") or {"type": "key", "key": "escape"}
    handled: List[Dict[str, Any]] = []

    for _attempt in range(max(1, max_dismissals)):
        if not _modal_scrim_present(prepared, controls):
            break
        matched: Optional[Dict[str, Any]] = None
        for dialog in dialogs:
            if not _dialog_signature_matches(prepared, dialog):
                continue
            required_intent = str(dialog.get("requires_intent") or "").strip()
            if required_intent and required_intent != str(intent or "").strip():
                LOGGER.info(
                    "Modal '%s' matched but intent %r != %r; using the default dismissal.",
                    dialog.get("id"),
                    intent,
                    required_intent,
                )
                break
            matched = dialog
            break

        action = (matched or {}).get("action") if matched else default_action
        record: Dict[str, Any] = {"dialog_id": (matched or {}).get("id") or "unrecognised"}
        if isinstance(action, dict) and str(action.get("type") or "") == "click":
            offsets = _anchored_window_offsets(
                action.get("point"), pack=pack, window_bounds=prepared.get("window_bounds")
            )
            bounds = prepared.get("window_bounds")
            if offsets is None or bounds is None:
                record["action"] = "skipped"
            else:
                point = (int(bounds[0] + offsets[0]), int(bounds[1] + offsets[1]))
                _glide_pointer(pyautogui, point[0], point[1], horizontal_first=False)
                pyautogui.click(point[0], point[1])
                record.update({"action": "click", "point": list(point)})
        else:
            key = str((action or {}).get("key") or "escape")
            pyautogui.press(key)
            record.update({"action": "key", "key": key})
        handled.append(record)
        time.sleep(float(controls.get("post_dismiss_delay_seconds") or 0.7))

    return {
        "status": "success",
        "dialogs_handled": handled,
        "modal_remaining": _modal_scrim_present(prepared, controls),
    }


def _apply_working_directory_strategy(manifest: Dict[str, Any], prompt: str, working_directory: Optional[str]) -> str:
    cleaned_prompt = str(prompt or "").strip()
    if not working_directory:
        return cleaned_prompt
    strategy = manifest.get("working_directory_strategy") or {}
    mode = str(strategy.get("mode") or "").strip().lower()
    if mode != "prepend_to_prompt":
        return cleaned_prompt
    template = str(strategy.get("template") or "{prompt}").strip() or "{prompt}"
    return template.format(
        prompt=cleaned_prompt,
        working_directory=str(working_directory).strip(),
    )


def _normalized_process_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.endswith(".exe"):
        text = text[: -len(".exe")]
    return text


def _reject_foreign_window(
    active_window: Mapping[str, Any],
    process_hints: Sequence[str],
    *,
    focused: bool,
    manifest: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Refuse to drive the GUI unless the resolved window really belongs to the target app.

    ``_focus_window`` matches on a window-TITLE substring, so an unrelated window whose title
    merely contains a hint (an editor with "Claude" in its title, say) satisfies it. Previously
    focus failure was recorded and ignored, so keystrokes intended for the app were typed into
    whatever happened to own focus - which is how a submitted prompt ended up inside an open
    editor buffer. Verify the process behind the window instead of trusting the title, and fail
    closed so a caller gets an error rather than typing into a bystander application.
    """
    agent_name = str(manifest.get("agent_name") or "desktop app")
    expected = {key for key in (_normalized_process_key(hint) for hint in process_hints or ()) if key}
    observed = _normalized_process_key(active_window.get("process_name"))

    if expected and observed and observed not in expected:
        return {
            "status": "error",
            "ready": False,
            "focused": bool(focused),
            "message": (
                f"Refusing to interact with '{agent_name}': the focused window belongs to "
                f"'{active_window.get('process_name')}' (title {active_window.get('title')!r}), not to "
                f"{sorted(expected)}. Bring the app to the foreground and retry."
            ),
            "active_window": dict(active_window),
        }

    if not focused and not observed:
        return {
            "status": "error",
            "ready": False,
            "focused": False,
            "message": (
                f"Refusing to interact with '{agent_name}': its window could not be focused and no "
                "owning process could be identified, so input would be delivered to whichever "
                "application currently has focus."
            ),
            "active_window": dict(active_window),
        }
    return None


def _prepare_desktop_app_interaction(
    agent_dir: Path,
    *,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    _ensure_interactive_desktop()
    if user_abort_active():
        remaining = _USER_ABORT_COOLDOWN_SECONDS - (time.time() - float(_USER_ABORT_STATE["at"]))
        return {
            "status": "error",
            "aborted_by_user": True,
            "message": (
                "Desktop automation is stopped because the pointer was moved into a screen "
                f"corner (fail-safe). It stays disabled for another {max(0, int(remaining))}s. "
                "Do not retry - resolve what the run was doing wrong first."
            ),
        }
    manifest = load_desktop_app_manifest(Path(agent_dir))
    if not manifest:
        return {"status": "error", "message": _missing_desktop_manifest_message(agent_dir)}

    details = current_platform_details()
    pack = select_desktop_asset_pack(manifest, platform_tag=details["platform"], architecture=details["architecture"])
    if not pack:
        return {
            "status": "error",
            "message": (
                f"No user-local desktop asset pack matches platform={details['platform']} "
                f"architecture={details['architecture']}. Open Agents > Desktop app control assets, "
                "prepare a setup prompt with your own coding assistant, then import its ZIP bundle."
            ),
            "platform": details["platform"],
            "architecture": details["architecture"],
        }

    process_hints = _normalize_process_names(manifest, pack, details["platform"])
    title_hints = _normalize_window_title_hints(manifest, pack, details["platform"])
    status_before = get_desktop_app_status(agent_dir, capture_screenshot=False)
    launch_result = None
    if not status_before.get("ready"):
        if not launch_if_needed:
            return {
                "status": "error",
                "message": f"Desktop app '{manifest.get('title') or manifest.get('agent_name')}' is not running or no window was found, and launch_if_needed is False.",
                "ready": False,
            }
        launch_result = _launch_application(manifest, details["platform"])
        if launch_result.get("status") != "success":
            return {
                "status": "error",
                "message": (
                    f"Desktop app '{manifest.get('title') or manifest.get('agent_name')}' is not currently running, "
                    f"and launching it failed ({launch_result.get('message')}). Please install and launch the desktop application."
                ),
                "launch_result": launch_result,
                "ready": False,
            }
        time.sleep(1.5)

    focused = _focus_window(details["platform"], title_hints, process_hints)
    time.sleep(0.4)

    status_after_focus = get_desktop_app_status(agent_dir, capture_screenshot=False)
    active_window = status_after_focus.get("active_window") or {}
    bounds = status_after_focus.get("window_bounds")
    window_bounds = tuple(bounds) if isinstance(bounds, list) and len(bounds) == 4 else None

    identity_error = _reject_foreign_window(active_window, process_hints, focused=focused, manifest=manifest)
    if identity_error is not None:
        return identity_error

    try:
        pyautogui = _lazy_import_pyautogui()
    except Exception as exc:
        return {
            "status": "error",
            "message": f"PyAutoGUI is unavailable: {exc}",
            "asset_pack": pack,
        }

    # Check the corner directly rather than relying on FailSafeException reaching us: several
    # call sites wrap pyautogui in broad `except Exception`, which would swallow the signal and
    # let the retry seize the pointer again.
    if _pointer_in_failsafe_corner(pyautogui):
        note_user_abort()
        return {
            "status": "error",
            "aborted_by_user": True,
            "message": (
                "Pointer is parked in a screen corner (fail-safe), so the user is taking manual "
                "control. Desktop automation is disabled for "
                f"{int(_USER_ABORT_COOLDOWN_SECONDS)}s. Do not retry."
            ),
            "asset_pack": pack,
        }

    return {
        "status": "success",
        "manifest": manifest,
        "details": details,
        "pack": pack,
        "process_hints": process_hints,
        "title_hints": title_hints,
        "focused": focused,
        "launch_result": launch_result,
        "active_window": active_window,
        "window_bounds": window_bounds,
        "screen_size": _screen_size(),
        "coordinate_space": str(pack.get("coordinate_space") or "window").strip() or "window",
        "pyautogui": pyautogui,
        "warnings": status_after_focus.get("warnings") or [],
    }


def _glide_pointer(
    pyautogui: Any,
    x: int,
    y: int,
    *,
    steps: int = 10,
    pause: float = 0.03,
    horizontal_first: bool = True,
) -> None:
    """Walk the pointer to (x, y) instead of teleporting it.

    Menus that fly out on hover close again when the pointer "leaves" the parent row without
    ever entering the submenu - and a single absolute jump looks exactly like that, so the
    submenu is gone by the time the click lands. The click still reports success (the
    coordinates were valid), it just silently selects nothing.

    The path matters as much as the motion. A straight diagonal from the parent row down into
    a submenu row cuts across the *other* parent rows on its way out of the menu, and each one
    it touches re-targets the flyout - so aiming at Effort/Max can land on whatever Speed
    happens to be showing. Moving horizontally out of the parent menu first keeps the pointer
    on its original row until it is inside the submenu, and only then does it travel vertically.
    """
    try:
        start_x, start_y = pyautogui.position()
    except Exception:
        start_x, start_y = x, y
    legs = [(x, start_y), (x, y)] if horizontal_first else [(x, y)]
    for leg_x, leg_y in legs:
        from_x, from_y = (pyautogui.position() if hasattr(pyautogui, "position") else (start_x, start_y))
        if abs(leg_x - from_x) < 1 and abs(leg_y - from_y) < 1:
            continue
        for index in range(1, max(1, steps) + 1):
            try:
                pyautogui.moveTo(
                    from_x + (leg_x - from_x) * index / steps,
                    from_y + (leg_y - from_y) * index / steps,
                )
            except Exception:
                return
            time.sleep(pause)


def _click_target_id(
    prepared: Dict[str, Any],
    target_id: str,
    *,
    prefer_image_match: bool = True,
    glide: bool = False,
    hover_only: bool = False,
) -> Dict[str, Any]:
    manifest = prepared["manifest"]
    pack = prepared["pack"]
    target = _resolve_target(pack, target_id)
    if target is None:
        return {"status": "error", "message": f"Target '{target_id}' is not defined for this asset pack."}
    point = _resolve_click_point_for_target(
        prepared["pyautogui"],
        manifest,
        pack,
        target,
        coordinate_space=prepared["coordinate_space"],
        window_bounds=prepared["window_bounds"],
        screen_size=prepared["screen_size"],
        prefer_image_match=prefer_image_match,
    )
    if point is None:
        return {"status": "error", "message": f"Could not compute or locate target '{target_id}'."}
    pyautogui = prepared["pyautogui"]
    if glide:
        _glide_pointer(pyautogui, point[0], point[1])
    if hover_only:
        # Rows that own a fly-out submenu open it on hover; clicking them is unreliable (it
        # opens one submenu but not another on the same menu), so hover and let the caller
        # click the leaf.
        pyautogui.moveTo(point[0], point[1])
    else:
        pyautogui.click(point[0], point[1])
    return {
        "status": "success",
        "target_id": target_id,
        "point": list(point),
        "glide": bool(glide),
        "action": "hover" if hover_only else "click",
    }


def _focus_prompt_target(prepared: Dict[str, Any], *, prefer_image_match: bool = True) -> Dict[str, Any]:
    manifest = prepared["manifest"]
    pack = prepared["pack"]
    preferred_prompt_target_id = str(manifest.get("preferred_prompt_target_id") or "composer_box")
    for target_id in (preferred_prompt_target_id, "composer_box", "composer_placeholder"):
        if _resolve_target(pack, target_id):
            result = _click_target_id(prepared, target_id, prefer_image_match=prefer_image_match)
            if result.get("status") == "success":
                time.sleep(0.2)
                return result
    return {"status": "error", "message": "Manifest does not define a prompt/composer target for this asset pack."}


def _prompt_metrics(text: str, image_count: int, attachment_count: Optional[int] = None) -> Dict[str, Any]:
    exact_text = str(text or "")
    words = len(exact_text.split())
    images = max(0, int(image_count or 0))
    attachments = max(images, int(attachment_count if attachment_count is not None else images))
    return {
        "text": exact_text,
        "prompt_text": exact_text,
        "characters": len(exact_text),
        "total_characters": len(exact_text),
        "words": words,
        "tokens": words,
        "images": images,
        "image_count": images,
        "attachments": attachments,
        "attachment_count": attachments,
    }


def get_desktop_app_prompt(
    agent_dir: Path,
    *,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Read the live unsent composer by selecting and copying its contents.

    The selected content is read into this process only.  It is never pasted
    back into the desktop app, which avoids changing the prompt while measuring
    it and keeps result capture separate from prompt capture.
    """
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared

    focus_result = _focus_prompt_target(prepared)
    if focus_result.get("status") != "success":
        return focus_result

    platform_tag = prepared["details"]["platform"]
    previous_clipboard = _clipboard_read(platform_tag)
    _select_all(prepared["pyautogui"], platform_tag)
    time.sleep(0.1)
    copy_action = _copy_selected_prompt(prepared["pyautogui"], platform_tag)
    time.sleep(0.2)
    selected_clipboard = _clipboard_read(platform_tag)
    image_info = _clipboard_image_info(platform_tag)

    restored_clipboard = False
    if previous_clipboard.get("status") == "success":
        restored_clipboard = _clipboard_copy(str(previous_clipboard.get("text") or ""), platform_tag)

    if selected_clipboard.get("status") != "success":
        return {
            "status": "error",
            "message": selected_clipboard.get("message") or "Could not read the selected desktop composer contents.",
            "agent_name": prepared["manifest"].get("agent_name"),
            "app_id": prepared["manifest"].get("app_id"),
            "platform": platform_tag,
            "asset_pack_id": prepared["pack"].get("asset_pack_id"),
            "action": {"type": "select_all_and_copy", "copy": copy_action},
        }

    prompt_text = str(selected_clipboard.get("text") or "")
    image_count = max(0, int(image_info.get("image_count") or 0))
    prompt_status = "draft" if prompt_text or image_count else "empty"
    return {
        "status": "success",
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": platform_tag,
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "prompt_status": prompt_status,
        "processing": False,
        **_prompt_metrics(prompt_text, image_count),
        "action": {
            "type": "select_all_and_copy",
            "prompt_click": focus_result.get("point"),
            "copy": copy_action,
            "clipboard_restored": restored_clipboard,
        },
        "image_count_source": image_info.get("source"),
        "image_count_warning": image_info.get("message") if image_info.get("status") != "success" else None,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def _paste_text_into_focused_prompt(
    prepared: Dict[str, Any],
    text: str,
    *,
    replace: bool,
) -> Dict[str, Any]:
    pyautogui = prepared["pyautogui"]
    platform_tag = prepared["details"]["platform"]
    if replace:
        _select_all(pyautogui, platform_tag)
        time.sleep(0.1)
    else:
        _move_caret_to_end(pyautogui, platform_tag)
        time.sleep(0.1)
    used_clipboard = _clipboard_copy(str(text or ""), platform_tag)
    try:
        if used_clipboard:
            _paste_from_clipboard(pyautogui, platform_tag)
        else:
            # PyAutoGUI's fallback is layout-dependent and cannot guarantee
            # arbitrary Unicode/punctuation fidelity.  Use it only when its
            # runtime key list covers every character; otherwise fail instead
            # of silently dropping quotes or other prompt content.
            text_value = str(text or "")
            supported_keys = {
                str(item)
                for item in (getattr(pyautogui, "KEYBOARD_KEYS", ()) or ())
                if len(str(item)) == 1
            }
            unsupported = [char for char in text_value if char not in supported_keys and char not in {"\n", "\r", "\t"}]
            if unsupported:
                return {
                    "status": "error",
                    "message": "System clipboard is unavailable; prompt text was not typed to avoid character loss.",
                    "used_clipboard": False,
                }
            pyautogui.write(text_value, interval=0.01)
    except Exception as exc:
        return {"status": "error", "message": f"Failed to type/paste text: {exc}", "used_clipboard": used_clipboard}
    return {"status": "success", "used_clipboard": used_clipboard}


def _attach_files_via_dialog(
    prepared: Dict[str, Any],
    attachment_paths: Sequence[str],
) -> Dict[str, Any]:
    """Attach files through a native file-open dialog (the path some apps require instead of
    clipboard-image paste, e.g. Codex). Clicks the attach control, optionally walks a menu to
    reach the picker, then for each file uses Go-to-folder (Cmd+Shift+G) + the absolute path +
    Return. Configured by the pack's ``attach_controls`` block.
    """
    pack = prepared["pack"]
    platform_tag = prepared["details"]["platform"]
    attach = pack.get("attach_controls") or {}
    valid_paths, missing_paths = _normalize_attachment_paths(attachment_paths)
    if not valid_paths:
        return {
            "status": "error",
            "message": "No existing local attachment files were provided.",
            "attached_paths": [],
            "missing_paths": missing_paths,
        }
    if platform_tag != "macos":
        # File-dialog automation is only wired for macOS today; fall back to clipboard.
        return _clipboard_copy_files(valid_paths, platform_tag)

    open_target_id = str(attach.get("open_target_id") or "attach_button")
    menu_steps = [step for step in (attach.get("menu_steps") or []) if isinstance(step, dict)]

    def _open_picker() -> Dict[str, Any]:
        click = _click_target_id(prepared, open_target_id)
        if click.get("status") != "success":
            return click
        time.sleep(float(attach.get("post_open_delay_seconds") or 0.6))
        for step in menu_steps:
            if step.get("target_id"):
                _click_target_id(prepared, str(step["target_id"]))
                time.sleep(float(step.get("delay_after_seconds") or 0.4))
        time.sleep(float(attach.get("post_menu_delay_seconds") or 0.5))
        return {"status": "success"}

    attached: List[str] = []
    for index, path in enumerate(valid_paths):
        opened = _open_picker()
        if opened.get("status") != "success":
            if attached:
                break
            return {**opened, "attached_paths": attached, "missing_paths": missing_paths}
        # Go-to-folder, type the absolute path, confirm, then open the file.
        if not _macos_keystroke(key="g", modifiers=["command", "shift"]):
            return {"status": "error", "message": "Could not open the file dialog's Go-to-folder field.", "attached_paths": attached, "missing_paths": missing_paths}
        time.sleep(float(attach.get("pre_path_delay_seconds") or 0.5))
        _macos_keystroke(text=str(path))
        time.sleep(0.3)
        _macos_keystroke(key_code=36)  # Return: accept the path
        time.sleep(float(attach.get("post_path_delay_seconds") or 0.6))
        _macos_keystroke(key_code=36)  # Return: open the selected file
        time.sleep(float(attach.get("post_attach_delay_seconds") or 0.8))
        attached.append(path)

    return {"status": "success" if attached else "error", "attached_paths": attached, "missing_paths": missing_paths}


def _attach_files_to_focused_prompt(
    prepared: Dict[str, Any],
    attachment_paths: Optional[Sequence[str]],
) -> Dict[str, Any]:
    if not attachment_paths:
        return {"status": "success", "attached_paths": [], "missing_paths": []}
    pyautogui = prepared["pyautogui"]
    platform_tag = prepared["details"]["platform"]
    # Some apps (e.g. Codex) do not accept a pasted image from the clipboard and instead require
    # the attach (+) button + native file dialog. Packs opt into that via attach_controls.mode.
    attach_mode = str((prepared["pack"].get("attach_controls") or {}).get("mode") or "clipboard_paste").strip().lower()
    if attach_mode == "file_dialog" and platform_tag == "macos":
        result = _attach_files_via_dialog(prepared, attachment_paths)
        time.sleep(float(prepared["pack"].get("post_attachment_delay_seconds") or 0.3))
        return result
    copied = _clipboard_copy_files(attachment_paths, platform_tag)
    if copied.get("status") != "success":
        return copied
    try:
        _paste_from_clipboard(pyautogui, platform_tag)
        time.sleep(float(prepared["pack"].get("post_attachment_delay_seconds") or 0.7))
    except Exception as exc:
        return {
            **copied,
            "status": "error",
            "message": f"Attachment clipboard was prepared, but paste failed: {exc}",
        }
    return copied


def get_desktop_app_status(agent_dir: Path, *, capture_screenshot: bool = False) -> Dict[str, Any]:
    manifest = load_desktop_app_manifest(Path(agent_dir))
    if not manifest:
        return {"status": "error", "message": _missing_desktop_manifest_message(agent_dir)}

    details = current_platform_details()
    pack = select_desktop_asset_pack(manifest, platform_tag=details["platform"], architecture=details["architecture"])
    process_hints = _normalize_process_names(manifest, pack, details["platform"])
    title_hints = _normalize_window_title_hints(manifest, pack, details["platform"])
    running_processes = _list_running_processes(process_hints)
    windows = _list_windows(details["platform"], process_hints)
    active_window = _find_best_window(windows, title_hints)
    bounds = _window_bounds_for_hint(
        details["platform"],
        str(active_window.get("title") or "").strip() if active_window else (title_hints[0] if title_hints else None),
        str(active_window.get("process_name") or "").strip() if active_window else (process_hints[0] if process_hints else None),
    )
    warnings: List[str] = []
    screen_size = _screen_size()
    monitor_count = _monitor_count()

    if details["platform_variant"] == "wsl2":
        warnings.append("WSL2 detected. GUI automation only works when the Linux session has display access to the target desktop.")
    if monitor_count > 1:
        warnings.append(
            "Multiple monitors detected. PyAutoGUI's upstream project warns that pointer automation is primarily reliable on the primary monitor."
        )
    if pack is None:
        warnings.append(
            "No matching user-local asset pack is installed. Open Agents > Desktop app control assets "
            "to prepare and import one for this OS, app version, theme, and display scale."
        )
    if pack is not None and pack.get("bootstrap_only"):
        warnings.append("Selected asset pack is marked bootstrap-only and should be replaced with stronger screenshots before production use.")
    missing_image_count = _missing_target_image_count(manifest, pack)
    if missing_image_count:
        warnings.append(
            f"Selected asset pack references {missing_image_count} missing image template(s); "
            "coordinate fallbacks will be used where defined, and image-only targets may be unavailable."
        )
    release_action_coverage = _release_action_coverage(manifest, pack)

    screenshot_path = None
    if capture_screenshot:
        screenshot_path = _build_artifact_path(manifest["agent_name"], "status")
        _capture_desktop_screenshot(screenshot_path)

    return {
        "status": "success",
        "agent_name": manifest.get("agent_name"),
        "title": manifest.get("title"),
        "app_id": manifest.get("app_id"),
        "platform": details["platform"],
        "platform_variant": details["platform_variant"],
        "architecture": details["architecture"],
        "process_names": process_hints,
        "window_title_hints": title_hints,
        "running_processes": running_processes,
        "window_count": len(windows),
        "active_window": active_window,
        "window_bounds": list(bounds) if bounds else None,
        "asset_pack": pack,
        "release_action_coverage": release_action_coverage,
        "screen_size": list(screen_size),
        "monitor_count": monitor_count,
        "ready": bool(running_processes or active_window),
        "warnings": warnings,
        "screenshot_path": str(screenshot_path) if screenshot_path else None,
    }


def capture_desktop_app_screenshot(agent_dir: Path, *, label: str = "manual") -> Dict[str, Any]:
    manifest = load_desktop_app_manifest(Path(agent_dir))
    if not manifest:
        return {"status": "error", "message": _missing_desktop_manifest_message(agent_dir)}

    details = current_platform_details()
    pack = select_desktop_asset_pack(manifest, platform_tag=details["platform"], architecture=details["architecture"])
    
    # Focus the window before capturing the screenshot
    if pack:
        process_hints = _normalize_process_names(manifest, pack, details["platform"])
        title_hints = _normalize_window_title_hints(manifest, pack, details["platform"])
        _focus_window(details["platform"], title_hints, process_hints)
        time.sleep(0.4)

    destination = _build_artifact_path(manifest["agent_name"], label)
    result = _capture_desktop_screenshot(destination)
    return {
        **result,
        "agent_name": manifest.get("agent_name"),
        "app_id": manifest.get("app_id"),
    }


def refresh_desktop_agent_llm_reference(agent_dir: Path) -> Dict[str, Any]:
    manifest = load_desktop_app_manifest(Path(agent_dir))
    if not manifest:
        return {"status": "error", "message": _missing_desktop_manifest_message(agent_dir)}
    llm_path = write_desktop_agent_llm_reference(Path(agent_dir), manifest)
    return {
        "status": "success",
        "agent_name": manifest.get("agent_name"),
        "llm_path": str(llm_path),
    }


def send_prompt_to_desktop_app(
    agent_dir: Path,
    *,
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[Sequence[str]] = None,
    launch_if_needed: bool = True,
    capture_before_submit: bool = False,
    capture_after_submit: bool = True,
) -> Dict[str, Any]:
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared

    manifest = prepared["manifest"]
    details = prepared["details"]
    pack = prepared["pack"]
    pyautogui = prepared["pyautogui"]

    screenshots: Dict[str, str] = {}
    if capture_before_submit:
        before_path = _build_artifact_path(manifest["agent_name"], "before-submit")
        _capture_desktop_screenshot(before_path)
        screenshots["before_submit"] = str(before_path)

    prompt_focus = _focus_prompt_target(prepared)
    if prompt_focus.get("status") != "success":
        return {**prompt_focus, "asset_pack": pack}

    effective_prompt = _apply_working_directory_strategy(manifest, prompt, working_directory)
    paste_result = _paste_text_into_focused_prompt(prepared, effective_prompt, replace=True)
    if paste_result.get("status") != "success":
        return {
            **paste_result,
            "asset_pack": pack,
            "prompt_click": prompt_focus.get("point"),
            "focus_applied": prepared["focused"],
            "launch_result": prepared["launch_result"],
        }
    attach_result = _attach_files_to_focused_prompt(prepared, attachment_paths)
    if attach_result.get("status") != "success":
        return {
            **attach_result,
            "asset_pack": pack,
            "prompt_click": prompt_focus.get("point"),
            "focus_applied": prepared["focused"],
            "launch_result": prepared["launch_result"],
        }

    post_input_delay_seconds = float(pack.get("post_input_delay_seconds") or 0.35)
    if post_input_delay_seconds > 0:
        time.sleep(post_input_delay_seconds)

    performed_submit_actions: List[Dict[str, Any]] = []
    for action in pack.get("submit_actions") or []:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("type") or "").strip().lower()
        if action_type == "click":
            time.sleep(float(action.get("delay_before_seconds") or 0.0))
            target_id = str(action.get("target_id") or manifest.get("preferred_submit_target_id") or "send_button")
            click_result = _click_target_id(prepared, target_id)
            if click_result.get("status") != "success":
                continue
            performed_submit_actions.append({"type": "click", "target_id": target_id, "point": click_result.get("point")})
            time.sleep(float(action.get("delay_after_seconds") or 0.2))
        elif action_type == "press":
            key = str(action.get("key") or "").strip()
            if not key:
                continue
            time.sleep(float(action.get("delay_before_seconds") or 0.0))
            pyautogui.press(key)
            performed_submit_actions.append({"type": "press", "key": key})
            time.sleep(float(action.get("delay_after_seconds") or 0.2))
        elif action_type == "hotkey":
            keys = [str(item).strip() for item in (action.get("keys") or []) if str(item).strip()]
            if not keys:
                continue
            time.sleep(float(action.get("delay_before_seconds") or 0.0))
            pyautogui.hotkey(*keys)
            performed_submit_actions.append({"type": "hotkey", "keys": keys})
            time.sleep(float(action.get("delay_after_seconds") or 0.2))

    _remember_submitted_prompt(manifest.get("agent_name"), effective_prompt)

    if capture_after_submit:
        after_path = _build_artifact_path(manifest["agent_name"], "after-submit")
        _capture_desktop_screenshot(after_path)
        screenshots["after_submit"] = str(after_path)

    return {
        "status": "success",
        "agent_name": manifest.get("agent_name"),
        "app_id": manifest.get("app_id"),
        "platform": details["platform"],
        "platform_variant": details["platform_variant"],
        "asset_pack_id": pack.get("asset_pack_id"),
        "focused": prepared["focused"],
        "launch_result": prepared["launch_result"],
        "active_window": prepared["active_window"],
        "window_bounds": list(prepared["window_bounds"]) if prepared["window_bounds"] else None,
        "prompt_click": prompt_focus.get("point"),
        "used_clipboard": paste_result.get("used_clipboard"),
        "attachments": attach_result,
        "effective_prompt": effective_prompt,
        "performed_submit_actions": performed_submit_actions,
        "screenshots": screenshots,
        "warnings": prepared["warnings"],
    }


def _selection_controls(pack: Dict[str, Any]) -> Dict[str, Any]:
    controls = pack.get("selection_controls")
    return controls if isinstance(controls, dict) else {}


def _find_selection_option(control: Dict[str, Any], option_group: str, requested_value: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    requested = _normalize_selection_value(requested_value)
    if not requested:
        return None
    options = control.get(option_group)
    if not isinstance(options, dict):
        return None
    for option_name, raw_option in options.items():
        if not isinstance(raw_option, dict):
            continue
        names = [str(option_name)]
        names.extend(str(item) for item in (raw_option.get("aliases") or []) if str(item).strip())
        if requested in {_normalize_selection_value(name) for name in names}:
            return str(option_name), raw_option
    return None


def _perform_selection_option(
    prepared: Dict[str, Any],
    control_name: str,
    requested_value: str,
    *,
    option_group: str = "options",
) -> Dict[str, Any]:
    pack = prepared["pack"]
    controls = _selection_controls(pack)
    control = controls.get(control_name)
    if not isinstance(control, dict):
        return {"status": "error", "message": f"Selection control '{control_name}' is not defined in this asset pack."}
    option_match = _find_selection_option(control, option_group, requested_value)
    if option_match is None:
        available = sorted((control.get(option_group) or {}).keys()) if isinstance(control.get(option_group), dict) else []
        return {
            "status": "error",
            "message": f"Unsupported {control_name} value '{requested_value}'.",
            "available_options": available,
        }

    open_target_id = str(control.get("open_target_id") or "").strip()
    if open_target_id:
        # The opener is a toggle, so a menu still open from a previous selection would be closed
        # by it and every following step would click straight through to the page - which can
        # land on whatever is underneath. Reset to a known-closed state first.
        if bool(control.get("close_open_menus_first", True)):
            try:
                prepared["pyautogui"].press("escape")
            except Exception:
                pass
            time.sleep(float(control.get("pre_open_delay_seconds") or 0.35))
        open_result = _click_target_id(prepared, open_target_id)
        if open_result.get("status") != "success":
            return open_result
        time.sleep(float(control.get("post_open_delay_seconds") or 0.2))

    option_name, option = option_match
    pyautogui = prepared["pyautogui"]
    time.sleep(float(option.get("delay_before_seconds") or 0.0))
    steps = option.get("steps")
    if isinstance(steps, list) and any(isinstance(item, dict) for item in steps):
        # Multi-step option for nested menus (e.g. open a submenu, then pick a row).
        performed_steps: List[Dict[str, Any]] = []
        for step_index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                continue
            time.sleep(float(raw_step.get("delay_before_seconds") or 0.0))
            if raw_step.get("target_id"):
                # Steps after the first walk into a submenu that only stays open while the
                # pointer travels there, so glide unless the pack opts out.
                glide = bool(raw_step.get("glide", step_index > 0))
                hover_only = str(raw_step.get("action") or "").strip().lower() == "hover"
                step_click = _click_target_id(
                    prepared,
                    str(raw_step.get("target_id")),
                    glide=glide,
                    hover_only=hover_only,
                )
                if step_click.get("status") != "success":
                    return {
                        **step_click,
                        "control": control_name,
                        "selected": option_name,
                        "completed_steps": performed_steps,
                    }
                performed_steps.append(
                    {
                        "type": str(step_click.get("action") or "click"),
                        "target_id": str(raw_step.get("target_id")),
                        "point": step_click.get("point"),
                    }
                )
            elif raw_step.get("key"):
                pyautogui.press(str(raw_step.get("key")).strip())
                performed_steps.append({"type": "press", "key": str(raw_step.get("key")).strip()})
            elif raw_step.get("hotkey"):
                step_keys = [str(k).strip() for k in (raw_step.get("hotkey") or []) if str(k).strip()]
                if step_keys:
                    pyautogui.hotkey(*step_keys)
                    performed_steps.append({"type": "hotkey", "keys": step_keys})
            time.sleep(float(raw_step.get("delay_after_seconds") or 0.25))
        action = {"type": "steps", "steps": performed_steps}
    elif option.get("key"):
        pyautogui.press(str(option["key"]).strip())
        action = {"type": "press", "key": str(option["key"]).strip()}
    elif option.get("hotkey"):
        keys = [str(key).strip() for key in (option.get("hotkey") or []) if str(key).strip()]
        if not keys:
            return {"status": "error", "message": f"Selection option '{option_name}' has an empty hotkey."}
        pyautogui.hotkey(*keys)
        action = {"type": "hotkey", "keys": keys}
    elif option.get("target_id"):
        click_result = _click_target_id(prepared, str(option.get("target_id")))
        if click_result.get("status") != "success":
            return click_result
        action = {"type": "click", "target_id": str(option.get("target_id")), "point": click_result.get("point")}
    else:
        return {"status": "error", "message": f"Selection option '{option_name}' has no executable action."}

    time.sleep(float(option.get("delay_after_seconds") or control.get("post_select_delay_seconds") or 0.25))
    # Approval-mode and model changes can raise a modal that blocks the change (the Full Access
    # consent) or silently reverses it (the "try the new model" promo), so settle it here rather
    # than returning a success the app has not actually applied.
    dialogs = handle_desktop_modal_dialogs(prepared, intent=f"{control_name}:{option_name}")
    return {
        "status": "success",
        "control": control_name,
        "selected": option_name,
        "requested": requested_value,
        "action": action,
        "dialogs": dialogs,
    }


def select_desktop_app_project(
    agent_dir: Path,
    *,
    project_name: str,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared

    project = str(project_name or "").strip()
    if not project:
        return {"status": "error", "message": "project_name is required."}

    control = _selection_controls(prepared["pack"]).get("project")
    if not isinstance(control, dict):
        control = {"open_target_id": "sidebar_recents_search", "post_open_delay_seconds": 0.2}
    open_target_id = str(control.get("open_target_id") or "sidebar_recents_search")
    open_result = _click_target_id(prepared, open_target_id)
    if open_result.get("status") != "success" and open_target_id != "sidebar_recents":
        open_result = _click_target_id(prepared, "sidebar_recents")
    if open_result.get("status") != "success":
        return open_result

    pyautogui = prepared["pyautogui"]
    platform_tag = prepared["details"]["platform"]
    time.sleep(float(control.get("post_open_delay_seconds") or 0.2))
    if bool(control.get("replace_existing_text", True)):
        _select_all(pyautogui, platform_tag)
        time.sleep(0.05)
    used_clipboard = _clipboard_copy(project, platform_tag)
    if used_clipboard:
        _paste_from_clipboard(pyautogui, platform_tag)
    else:
        pyautogui.write(project, interval=0.01)
    time.sleep(float(control.get("post_input_delay_seconds") or 0.25))
    if bool(control.get("press_enter", True)):
        pyautogui.press("enter")
    return {
        "status": "success",
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": platform_tag,
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "project_name": project,
        "open_target": open_result,
        "used_clipboard": used_clipboard,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def select_desktop_app_permissions(
    agent_dir: Path,
    *,
    permissions: str,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared
    result = _perform_selection_option(prepared, "permissions", permissions)
    return {
        **result,
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": prepared["details"]["platform"],
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def select_desktop_app_model(
    agent_dir: Path,
    *,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    speed: Optional[str] = None,
    advanced: Optional[str] = None,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared

    def _error_payload(result: Dict[str, Any], actions: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            **result,
            "agent_name": prepared["manifest"].get("agent_name"),
            "app_id": prepared["manifest"].get("app_id"),
            "platform": prepared["details"]["platform"],
            "asset_pack_id": prepared["pack"].get("asset_pack_id"),
            "actions": actions,
        }

    # Each selection re-opens its own menu (open_target_id), so the controls can be applied in
    # sequence. ``speed`` and ``advanced`` are optional newer controls (e.g. the ChatGPT Codex
    # 26.707 menu) and are no-ops on packs that do not define them unless explicitly requested.
    actions: List[Dict[str, Any]] = []
    for control_name, requested, option_group in (
        ("model", model, "options"),
        ("model", effort, "effort_options"),
        ("speed", speed, "options"),
        ("advanced", advanced, "options"),
    ):
        if not (requested and str(requested).strip()):
            continue
        result = _perform_selection_option(prepared, control_name, str(requested), option_group=option_group)
        actions.append(result)
        if result.get("status") != "success":
            return _error_payload(result, actions)
    if not actions:
        return {"status": "error", "message": "Provide model, effort, speed, and/or advanced."}
    return {
        "status": "success",
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": prepared["details"]["platform"],
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "actions": actions,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def add_text_to_desktop_app_prompt(
    agent_dir: Path,
    *,
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[Sequence[str]] = None,
    prepend_newline: bool = True,
    launch_if_needed: bool = True,
    capture_after: bool = False,
    preserve_text: bool = False,
) -> Dict[str, Any]:
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared
    focus_result = _focus_prompt_target(prepared)
    if focus_result.get("status") != "success":
        return focus_result

    effective_prompt = (
        str(prompt or "")
        if preserve_text
        else _apply_working_directory_strategy(prepared["manifest"], prompt, working_directory)
    )
    text_to_insert = ("\n" if prepend_newline else "") + effective_prompt
    paste_result = _paste_text_into_focused_prompt(prepared, text_to_insert, replace=False)
    if paste_result.get("status") != "success":
        return paste_result
    _remember_draft_prompt(
        prepared["manifest"].get("agent_name"),
        effective_prompt,
        append=prepend_newline,
        preserve_text=preserve_text,
    )
    attach_result = _attach_files_to_focused_prompt(prepared, attachment_paths)
    screenshots: Dict[str, str] = {}
    if capture_after:
        after_path = _build_artifact_path(prepared["manifest"]["agent_name"], "after-add-prompt")
        _capture_desktop_screenshot(after_path)
        screenshots["after_add_prompt"] = str(after_path)
    return {
        "status": "success" if attach_result.get("status") == "success" else "partial_success",
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": prepared["details"]["platform"],
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "mode": "add_to_prompt",
        "submitted": False,
        "queued": False,
        "effective_prompt": effective_prompt,
        "prompt_click": focus_result.get("point"),
        "used_clipboard": paste_result.get("used_clipboard"),
        "attachments": attach_result,
        "screenshots": screenshots,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def replace_desktop_app_prompt(
    agent_dir: Path,
    *,
    prompt: str,
    working_directory: Optional[str] = None,
    attachment_paths: Optional[Sequence[str]] = None,
    launch_if_needed: bool = True,
    capture_after: bool = False,
) -> Dict[str, Any]:
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared
    focus_result = _focus_prompt_target(prepared)
    if focus_result.get("status") != "success":
        return focus_result

    effective_prompt = _apply_working_directory_strategy(prepared["manifest"], prompt, working_directory)
    paste_result = _paste_text_into_focused_prompt(prepared, effective_prompt, replace=True)
    if paste_result.get("status") != "success":
        return paste_result
    _remember_draft_prompt(prepared["manifest"].get("agent_name"), effective_prompt)
    attach_result = _attach_files_to_focused_prompt(prepared, attachment_paths)
    screenshots: Dict[str, str] = {}
    if capture_after:
        after_path = _build_artifact_path(prepared["manifest"]["agent_name"], "after-replace-prompt")
        _capture_desktop_screenshot(after_path)
        screenshots["after_replace_prompt"] = str(after_path)
    return {
        "status": "success" if attach_result.get("status") == "success" else "partial_success",
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": prepared["details"]["platform"],
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "mode": "replace_prompt",
        "submitted": False,
        "queued": False,
        "effective_prompt": effective_prompt,
        "prompt_click": focus_result.get("point"),
        "used_clipboard": paste_result.get("used_clipboard"),
        "attachments": attach_result,
        "screenshots": screenshots,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def attach_files_to_desktop_app_prompt(
    agent_dir: Path,
    *,
    attachment_paths: Sequence[str],
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared
    focus_result = _focus_prompt_target(prepared)
    if focus_result.get("status") != "success":
        return focus_result
    attach_result = _attach_files_to_focused_prompt(prepared, attachment_paths)
    return {
        **attach_result,
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": prepared["details"]["platform"],
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "prompt_click": focus_result.get("point"),
        "submitted": False,
        "queued": False,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def clear_desktop_app_prompt(
    agent_dir: Path,
    *,
    launch_if_needed: bool = True,
) -> Dict[str, Any]:
    """Clear the desktop composer, preferring the app's native new-chat action.

    Rich attachment chips are not reliably removed by Ctrl/Cmd+A followed by
    Backspace.  Versioned packs therefore expose a native new-task/new-chat
    target when it is known; keyboard deletion remains the compatibility path
    for older packs that do not have one.
    """
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared

    target_result: Dict[str, Any] = {"status": "error", "message": "No native new-prompt target is defined."}
    clicked_target_id = ""
    for candidate in ("new_prompt", "new_chat_button", "new_session"):
        target_result = _click_target_id(prepared, candidate, prefer_image_match=True)
        if target_result.get("status") == "success":
            clicked_target_id = candidate
            break

    # The native new-chat action is what removes attachment chips, but on ChatGPT Codex 26.727 it
    # carries the text draft over into the fresh chat - so a "cleared" composer still held the
    # previous prompt and the next append spliced into it. Always follow up with a keyboard
    # clear, which handles the text the native action leaves behind.
    if clicked_target_id:
        time.sleep(0.35)
        action = {"type": "click", "target_id": clicked_target_id, "point": target_result.get("point")}
    else:
        action = {"type": "select_all_and_press", "key": "backspace"}

    focus_result = _focus_prompt_target(prepared)
    if focus_result.get("status") != "success":
        if not clicked_target_id:
            return focus_result
    else:
        _select_all(prepared["pyautogui"], prepared["details"]["platform"])
        time.sleep(0.1)
        prepared["pyautogui"].press("backspace")
        action["text_cleared"] = True
    key = _agent_prompt_key(prepared["manifest"].get("agent_name"))
    _DRAFT_PROMPT_BY_AGENT.pop(key, None)
    return {
        "status": "success",
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": prepared["details"]["platform"],
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "action": action,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def stop_desktop_app_prompt(
    agent_dir: Path,
    *,
    launch_if_needed: bool = False,
) -> Dict[str, Any]:
    """Ask the desktop app to stop its current generation.

    Packs with a precise stop target use it; older packs fall back to Escape,
    which is the native stop/cancel action in both supported desktop bridges.
    """
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared
    stop_result = _click_target_id(prepared, "stop_button", prefer_image_match=True)
    if stop_result.get("status") == "success":
        action = {"type": "click", "target_id": "stop_button", "point": stop_result.get("point")}
    else:
        prepared["pyautogui"].press("escape")
        action = {"type": "press", "key": "escape"}
    return {
        "status": "success",
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": prepared["details"]["platform"],
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "action": action,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def send_current_desktop_app_prompt(
    agent_dir: Path,
    *,
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared
    focus_result = _focus_prompt_target(prepared)
    if focus_result.get("status") != "success":
        return focus_result
    submit_target_id = str(prepared["manifest"].get("preferred_submit_target_id") or "send_button").strip()
    submit_result = _click_target_id(prepared, submit_target_id, prefer_image_match=True)
    if submit_result.get("status") == "success":
        action = {"type": "click", "target_id": submit_target_id, "point": submit_result.get("point")}
    else:
        prepared["pyautogui"].press("enter")
        action = {"type": "press", "key": "enter"}
    _remember_submitted_prompt(prepared["manifest"].get("agent_name"))
    screenshots: Dict[str, str] = {}
    if capture_after:
        time.sleep(0.3)
        after_path = _build_artifact_path(prepared["manifest"]["agent_name"], "after-send-current")
        _capture_desktop_screenshot(after_path)
        screenshots["after_send_current"] = str(after_path)
    return {
        "status": "success",
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": prepared["details"]["platform"],
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "submitted": True,
        "queued": False,
        "action": action,
        "prompt_click": focus_result.get("point"),
        "screenshots": screenshots,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def queue_current_desktop_app_prompt(
    agent_dir: Path,
    *,
    launch_if_needed: bool = True,
    capture_after: bool = True,
) -> Dict[str, Any]:
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared
    focus_result = _focus_prompt_target(prepared)
    if focus_result.get("status") != "success":
        return focus_result
    keys = _queue_prompt_hotkey(prepared["pyautogui"], prepared["details"]["platform"])
    _remember_submitted_prompt(prepared["manifest"].get("agent_name"))
    screenshots: Dict[str, str] = {}
    if capture_after:
        time.sleep(0.3)
        after_path = _build_artifact_path(prepared["manifest"]["agent_name"], "after-queue-current")
        _capture_desktop_screenshot(after_path)
        screenshots["after_queue_current"] = str(after_path)
    return {
        "status": "success",
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": prepared["details"]["platform"],
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "submitted": False,
        "queued": True,
        "action": {"type": "hotkey", "keys": keys},
        "prompt_click": focus_result.get("point"),
        "screenshots": screenshots,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _detect_processing_from_send_control(prepared: Dict[str, Any], capture_path: Path) -> Dict[str, Any]:
    """Use the send/stop control as a no-OCR fallback for Codex-like UIs."""
    target = _resolve_target(prepared["pack"], "send_button")
    normalized_box = _target_normalized_box(
        target,
        pack=prepared["pack"],
        window_bounds=prepared.get("window_bounds"),
    )
    if not isinstance(normalized_box, list) or len(normalized_box) != 4:
        return {"processing": None, "source": "send_button_visual"}
    crop_path = _crop_region_from_capture(capture_path, prepared.get("window_bounds"), normalized_box)
    if crop_path is None:
        return {"processing": None, "source": "send_button_visual"}
    try:
        from PIL import Image

        with Image.open(str(crop_path)).convert("RGB") as image:
            width, height = image.size
            if width < 4 or height < 4:
                return {"processing": None, "source": "send_button_visual"}
            pixels = list(image.getdata())
            dark = [max(red, green, blue) < 100 for red, green, blue in pixels]
            visited = bytearray(width * height)
            largest: List[Tuple[int, int, int, int, int]] = []
            for start in range(width * height):
                if visited[start] or not dark[start]:
                    continue
                stack = [start]
                visited[start] = 1
                component: List[int] = []
                while stack:
                    index = stack.pop()
                    component.append(index)
                    x = index % width
                    y = index // width
                    for neighbor in (index - 1, index + 1, index - width, index + width):
                        if neighbor < 0 or neighbor >= width * height or visited[neighbor]:
                            continue
                        neighbor_x = neighbor % width
                        neighbor_y = neighbor // width
                        if abs(neighbor_x - x) + abs(neighbor_y - y) != 1 or not dark[neighbor]:
                            continue
                        visited[neighbor] = 1
                        stack.append(neighbor)
                if len(component) >= 20:
                    largest.append((
                        len(component),
                        min(item % width for item in component),
                        min(item // width for item in component),
                        max(item % width for item in component),
                        max(item // width for item in component),
                    ))
        if not largest:
            return {"processing": None, "source": "send_button_visual"}
        _, left, top, right, bottom = max(largest, key=lambda item: item[0])
        icon_pixels = []
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                icon_pixels.append(pixels[y * width + x])
        dark_ratio = sum(1 for red, green, blue in icon_pixels if max(red, green, blue) < 100) / len(icon_pixels)
        light_ratio = sum(1 for red, green, blue in icon_pixels if min(red, green, blue) > 190) / len(icon_pixels)
        # The active control is a dark circle with a filled bright square. The
        # idle up-arrow leaves more dark pixels and far less bright fill inside
        # the same circle (calibrated against the supplied Codex captures).
        processing = dark_ratio <= 0.70 and light_ratio >= 0.23
        return {
            "processing": processing,
            "source": "send_button_visual",
            "dark_ratio": round(dark_ratio, 4),
            "light_ratio": round(light_ratio, 4),
        }
    except Exception as exc:
        return {"processing": None, "source": "send_button_visual", "message": str(exc)}


def _detect_processing_from_stop_sprite(prepared: Dict[str, Any]) -> Dict[str, Any]:
    """Decide running vs finished from the send/stop control sprites alone.

    The composer button swaps between "send" and "stop" for the duration of a turn, so its
    identity IS the run state - no OCR, no pixel heuristics. This is the authoritative check
    because it is a direct observation of the control the app itself toggles:

      stop sprite present  -> the app is still working
      send sprite present  -> the turn is finished

    Both sprites are bounded to the composer's own box, so neither can match a lookalike
    elsewhere in the window (a bare square otherwise matches the title-bar maximise button).
    """
    pyautogui = prepared.get("pyautogui")
    manifest = prepared["manifest"]
    pack = prepared["pack"]
    window_bounds = prepared.get("window_bounds")
    if pyautogui is None:
        return {"processing": None, "source": "stop_sprite"}

    stop_target = _resolve_target(pack, "stop_button")
    if stop_target is not None and stop_target.get("expected_image_path"):
        if _locate_target_by_image(pyautogui, manifest, stop_target, window_bounds=window_bounds):
            return {"processing": True, "source": "stop_button_sprite"}

    send_target = _resolve_target(pack, "send_button")
    if send_target is not None and send_target.get("expected_image_path"):
        if _locate_target_by_image(pyautogui, manifest, send_target, window_bounds=window_bounds):
            return {"processing": False, "source": "send_button_sprite"}

    # Only claim "finished" when the stop sprite was actually checked for and absent; otherwise
    # stay undecided so callers do not treat an unknown state as a completed turn.
    if stop_target is not None and stop_target.get("expected_image_path"):
        return {"processing": False, "source": "stop_button_sprite_absent"}
    return {"processing": None, "source": "stop_sprite"}


def _inspect_desktop_prompt_status(prepared: Dict[str, Any]) -> Dict[str, Any]:
    capture_path = _build_artifact_path(prepared["manifest"]["agent_name"], "prompt-status")
    capture = _capture_desktop_screenshot(capture_path)
    if capture.get("status") != "success":
        return {**capture, "prompt_status": "unknown", "processing": None}

    # Sprite state first. OCR is not available on every platform (no Vision, no tesseract), and
    # when it is missing every text-based check silently returns empty - which previously left
    # the status "unknown" and invited callers to resend the prompt.
    sprite = _detect_processing_from_stop_sprite(prepared)
    if sprite.get("processing") is not None:
        return {
            "status": "success",
            "prompt_status": "processing" if sprite["processing"] else "idle",
            "processing": bool(sprite["processing"]),
            "detection_source": str(sprite.get("source")),
            "busy_markers": [],
            "ocr_engine": None,
            "screenshot": str(capture_path),
            "focused": prepared["focused"],
            "warnings": prepared["warnings"],
            "visual_metrics": sprite,
        }

    ocr_path = _crop_region_from_capture(
        capture_path,
        prepared.get("window_bounds"),
        [0, 0, 1, 1],
    ) or capture_path
    ocr = _ocr_image_file(ocr_path)
    ocr_text = str(ocr.get("text") or "")
    busy_markers: List[str] = []
    if ocr.get("status") == "success":
        for line in ocr_text.splitlines():
            compact_line = _compact_text(line)
            if not compact_line:
                continue
            for marker in _OCR_BUSY_MARKERS:
                if marker in compact_line and marker not in busy_markers:
                    busy_markers.append(marker)

    visual = None
    if ocr.get("status") == "success" and busy_markers:
        processing = True
        source = "desktop_ocr"
        detected = True
    else:
        visual = _detect_processing_from_send_control(prepared, capture_path)
        if visual.get("processing") is not None:
            processing = visual.get("processing")
            source = str(visual.get("source") or "send_button_visual")
            detected = True
        else:
            processing = False if ocr.get("status") == "success" else None
            source = (
                "desktop_ocr"
                if ocr.get("status") == "success"
                else str(visual.get("source") or "send_button_visual")
            )
            detected = ocr.get("status") == "success"

    prompt_status = "processing" if processing else ("idle" if detected else "unknown")
    return {
        "status": "success",
        "prompt_status": prompt_status,
        "processing": processing,
        "detection_source": source,
        "busy_markers": busy_markers,
        "ocr_engine": ocr.get("engine"),
        "screenshot": str(capture_path),
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
        "visual_metrics": visual,
    }


def get_desktop_app_prompt_status(
    agent_dir: Path,
    *,
    launch_if_needed: bool = False,
) -> Dict[str, Any]:
    """Inspect the active desktop prompt window for processing/idle state."""
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared
    # Deliberately does NOT click the composer. Reading state is an observation, and clicking
    # into the composer on every poll disturbs the very window being measured - it can dismiss
    # popovers, steal the caret mid-generation, and it made a plain status check indistinguishable
    # from a user interaction.
    result = _inspect_desktop_prompt_status(prepared)
    result["action"] = {"type": "inspect_only"}
    result["agent_name"] = prepared["manifest"].get("agent_name")
    result["app_id"] = prepared["manifest"].get("app_id")
    result["platform"] = prepared["details"]["platform"]
    result["asset_pack_id"] = prepared["pack"].get("asset_pack_id")
    return result


def _extract_visible_response_from_ocr_text(ocr_text: str, *, expected_prompt: str) -> Dict[str, Any]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(ocr_text or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return {"status": "error", "message": "OCR did not return visible text.", "response_text": ""}

    compact_prompt = _compact_text(expected_prompt)
    if not compact_prompt:
        return {"status": "error", "message": "No submitted prompt is available for OCR final-response matching.", "response_text": ""}

    prompt_end_index: Optional[int] = None
    for start_index in range(len(lines)):
        rolling: List[str] = []
        for end_index, line in enumerate(lines[start_index:], start=start_index):
            rolling.append(line)
            if compact_prompt in _compact_text(" ".join(rolling)):
                prompt_end_index = end_index
                break
    if prompt_end_index is None:
        return {"status": "error", "message": "OCR text did not include the submitted prompt.", "response_text": ""}

    answer_lines: List[str] = []
    for line in lines[prompt_end_index + 1:]:
        compact_line = _compact_text(line)
        for marker in _OCR_BUSY_MARKERS:
            if marker in compact_line:
                return {"status": "error", "message": f"Visible desktop text still contains busy/blocking marker: {marker}.", "response_text": ""}
        if any(marker in compact_line for marker in _OCR_COMPOSER_MARKERS):
            break
        answer_lines.append(line)

    response_text = "\n".join(answer_lines).strip()
    if not response_text:
        return {"status": "error", "message": "OCR found no completed response after the submitted prompt.", "response_text": ""}
    return {"status": "success", "response_text": response_text, "matched_prompt": expected_prompt}


def _copy_final_response_via_ocr(prepared: Dict[str, Any], *, expected_prompt: Optional[str] = None) -> Dict[str, Any]:
    expected_prompt = str(expected_prompt or _last_submitted_prompt(prepared["manifest"].get("agent_name")) or "").strip()
    capture_path = _build_artifact_path(prepared["manifest"]["agent_name"], "final-response-ocr")
    capture = _capture_desktop_screenshot(capture_path)
    if capture.get("status") != "success":
        return {**capture, "response_text": ""}
    crop_path = _crop_region_from_capture(capture_path, prepared.get("window_bounds"), [0, 0, 1, 1])
    ocr_path = crop_path or capture_path
    ocr = _ocr_image_file(ocr_path)
    extracted = _extract_visible_response_from_ocr_text(str(ocr.get("text") or ""), expected_prompt=expected_prompt)
    return {
        **extracted,
        "fallback": "ocr_after_copy_failure",
        "screenshot": str(capture_path),
        "crop": str(crop_path) if crop_path else None,
        "ocr_engine": ocr.get("engine"),
        "ocr_text": ocr.get("text") or "",
    }


def copy_final_response_from_desktop_app(
    agent_dir: Path,
    *,
    launch_if_needed: bool = True,
    expected_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared

    pyautogui = prepared["pyautogui"]
    copy_target_id = str(prepared["manifest"].get("preferred_copy_response_target_id") or "copy_response_button")
    # Clear the clipboard first so a miscalibrated/failed copy click cannot return stale
    # text (e.g. the prompt left over from an earlier paste). An empty clipboard then
    # deterministically routes to the OCR fallback below.
    _clipboard_copy("", prepared["details"]["platform"])
    try:
        pyautogui.scroll(-6)
        time.sleep(0.2)
    except Exception:
        pass

    try:
        right_inset = _detect_right_sidebar_inset_px(prepared.get("window_bounds"), pack=prepared.get("pack"))
    except Exception:
        right_inset = 0.0
    try:
        left_inset = float(_pack_layout(prepared.get("pack")).get("content_left_inset_px") or 0.0)
    except Exception:
        left_inset = 0.0

    hover_points: List[Tuple[int, int]] = []
    window_bounds = prepared["window_bounds"]
    if window_bounds:
        left, top, right, bottom = window_bounds
        width = float(max(1, right - left))
        height = float(max(1, bottom - top))
        center_x = (left_inset + (width - right_inset)) / 2.0 if (left_inset or right_inset) else width * 0.54
        for y_ratio in (0.78, 0.68, 0.58, 0.48, 0.38):
            hover_points.append((int(left + center_x), int(top + height * y_ratio)))
    else:
        width, height = prepared["screen_size"]
        w_float = float(width)
        center_x = (left_inset + (w_float - right_inset)) / 2.0 if (left_inset or right_inset) else w_float * 0.54
        for y_ratio in (0.78, 0.68, 0.58, 0.48, 0.38):
            hover_points.append((int(center_x), int(height * y_ratio)))

    click_result: Dict[str, Any] = {"status": "error", "message": "Copy response button was not found."}
    for point in hover_points:
        try:
            pyautogui.moveTo(point[0], point[1], duration=0.08)
            time.sleep(0.25)
        except Exception:
            pass
        click_result = _click_target_id(prepared, copy_target_id, prefer_image_match=True)
        if click_result.get("status") == "success":
            break

    if click_result.get("status") != "success":
        ocr_fallback = _copy_final_response_via_ocr(prepared, expected_prompt=expected_prompt)
        if ocr_fallback.get("status") == "success":
            return {
                "status": "success",
                "agent_name": prepared["manifest"].get("agent_name"),
                "app_id": prepared["manifest"].get("app_id"),
                "platform": prepared["details"]["platform"],
                "asset_pack_id": prepared["pack"].get("asset_pack_id"),
                "copy_click": click_result,
                "response_text": ocr_fallback.get("response_text") or "",
                "fallback": ocr_fallback,
                "focused": prepared["focused"],
                "warnings": prepared["warnings"],
            }
        return {
            **click_result,
            "agent_name": prepared["manifest"].get("agent_name"),
            "app_id": prepared["manifest"].get("app_id"),
            "platform": prepared["details"]["platform"],
            "asset_pack_id": prepared["pack"].get("asset_pack_id"),
            "fallback": ocr_fallback,
            "focused": prepared["focused"],
            "warnings": prepared["warnings"],
        }

    time.sleep(0.25)
    clipboard = _clipboard_read(prepared["details"]["platform"])
    response_text = str(clipboard.get("text") or "")
    if clipboard.get("status") == "success" and not response_text.strip():
        ocr_fallback = _copy_final_response_via_ocr(prepared, expected_prompt=expected_prompt)
        if ocr_fallback.get("status") == "success":
            return {
                "status": "success",
                "agent_name": prepared["manifest"].get("agent_name"),
                "app_id": prepared["manifest"].get("app_id"),
                "platform": prepared["details"]["platform"],
                "asset_pack_id": prepared["pack"].get("asset_pack_id"),
                "copy_click": click_result,
                "clipboard": {**clipboard, "status": "error", "message": "Clipboard was empty after clicking the copy-response target."},
                "response_text": ocr_fallback.get("response_text") or "",
                "fallback": ocr_fallback,
                "focused": prepared["focused"],
                "warnings": prepared["warnings"],
            }
        clipboard = {
            **clipboard,
            "status": "error",
            "message": "Clipboard was empty after clicking the copy-response target.",
            "fallback": ocr_fallback,
        }
    return {
        "status": "success" if clipboard.get("status") == "success" else "error",
        "agent_name": prepared["manifest"].get("agent_name"),
        "app_id": prepared["manifest"].get("app_id"),
        "platform": prepared["details"]["platform"],
        "asset_pack_id": prepared["pack"].get("asset_pack_id"),
        "copy_click": click_result,
        "clipboard": clipboard,
        "response_text": response_text,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }


def wait_until_idle_and_copy_desktop_response(
    agent_dir: Path,
    *,
    poll_interval_seconds: float = 12.0,
    stable_polls: int = 2,
    max_wait_seconds: float = 900.0,
    min_initial_wait_seconds: float = 0.0,
    launch_if_needed: bool = False,
    expected_prompt: Optional[str] = None,
    max_consecutive_failures: int = 3,
) -> Dict[str, Any]:
    """Poll-until-idle: copy the desktop app's latest response until the text stabilizes.

    The app (e.g. Codex) keeps streaming/working for a variable amount of time. Rather than
    reading a fragile "busy" UI indicator, this repeatedly copies the latest response; once the
    copied text is non-empty and unchanged across ``stable_polls`` consecutive reads, the run is
    treated as finished and the text is returned. This folds completion-detection and
    response-capture into one mechanism that depends only on a working copy control.

    Returns a dict with ``status`` (success | timeout | error), ``response_text``, ``polls`` and
    ``stabilized``.
    """
    poll_interval = max(1.0, float(poll_interval_seconds))
    required_stable = max(1, int(stable_polls))
    failure_budget = max(1, int(max_consecutive_failures))
    deadline = time.monotonic() + max(1.0, float(max_wait_seconds))

    if min_initial_wait_seconds and min_initial_wait_seconds > 0:
        time.sleep(min(float(min_initial_wait_seconds), max(0.0, deadline - time.monotonic())))

    last_text: Optional[str] = None
    stable_count = 0
    polls = 0
    consecutive_failures = 0
    last_copy: Dict[str, Any] = {}

    # Watch the run state, then read once. The previous approach inferred completion by copying
    # over and over until the text stopped changing, which meant every poll performed a
    # hover-and-click sweep across the window - visible as the pointer being repeatedly seized -
    # and it could never settle when the read mechanism itself was broken, because an unreadable
    # response and an empty one are indistinguishable. Observing the composer's stop/send control
    # answers "is it still working?" directly and without touching anything.
    idle_streak = 0
    while time.monotonic() < deadline:
        polls += 1
        state = get_desktop_app_prompt_status(agent_dir, launch_if_needed=launch_if_needed)
        if state.get("aborted_by_user"):
            return {
                "status": "error",
                "response_text": "",
                "polls": polls,
                "stabilized": False,
                "last_copy": {},
                "message": str(state.get("message") or "Aborted by user."),
            }

        prompt_status = str(state.get("prompt_status") or "unknown")
        if prompt_status == "processing":
            idle_streak = 0
            consecutive_failures = 0
        elif prompt_status == "idle":
            idle_streak += 1
            if idle_streak >= required_stable:
                last_copy = _copy_final_response_with_timeout(
                    agent_dir,
                    launch_if_needed=launch_if_needed,
                    timeout_seconds=max(0.1, deadline - time.monotonic()),
                    expected_prompt=expected_prompt,
                )
                text = str(last_copy.get("response_text") or "").strip()
                if text:
                    return {
                        "status": "success",
                        "response_text": text,
                        "polls": polls,
                        "stabilized": True,
                        "last_copy": last_copy,
                    }
                # The turn is finished but the text could not be read. Retrying cannot change
                # that, so spend only the failure budget on it rather than the whole window.
                consecutive_failures += 1
                if consecutive_failures >= failure_budget:
                    return {
                        "status": "error",
                        "response_text": "",
                        "polls": polls,
                        "stabilized": True,
                        "last_copy": last_copy,
                        "message": (
                            "The app finished, but its response could not be copied after "
                            f"{consecutive_failures} attempts: "
                            f"{last_copy.get('message') or 'no text was returned'}. "
                            "Read it in the app, or recalibrate copy_response_button."
                        ),
                    }
        else:
            # State could not be determined. Never guess "finished" here: treating unknown as
            # complete is what let callers conclude a turn was over and send the prompt again.
            idle_streak = 0
            consecutive_failures += 1
            if consecutive_failures >= failure_budget:
                return {
                    "status": "error",
                    "response_text": "",
                    "polls": polls,
                    "stabilized": False,
                    "last_copy": last_copy,
                    "message": (
                        f"Could not determine whether {agent_dir.name} is still working after "
                        f"{consecutive_failures} checks "
                        f"({state.get('message') or 'no stop/send sprite matched'}). "
                        "Do NOT resend the prompt - it may already be running."
                    ),
                }

        if time.monotonic() + poll_interval >= deadline:
            break
        time.sleep(poll_interval)

    return {
        "status": "timeout",
        "response_text": last_text or "",
        "polls": polls,
        "stabilized": False,
        "last_copy": last_copy,
        "message": (
            f"Timed out after {max_wait_seconds:.0f}s waiting for the app to finish. "
            "Do NOT resend the prompt - it may still be running."
        ),
    }


def _copy_final_response_with_timeout(
    agent_dir: Path,
    *,
    launch_if_needed: bool,
    timeout_seconds: float,
    expected_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    error: Dict[str, str] = {}

    def _worker() -> None:
        try:
            result.update(
                copy_final_response_from_desktop_app(
                    agent_dir,
                    launch_if_needed=launch_if_needed,
                    expected_prompt=expected_prompt,
                )
            )
        except Exception as exc:
            error["message"] = str(exc)

    # ponytail: daemon thread avoids wedging /api/chat forever if an OS GUI API hangs; use process isolation if this becomes common.
    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(max(0.1, float(timeout_seconds)))
    if worker.is_alive():
        return {
            "status": "timeout",
            "message": f"Copy response attempt timed out after {float(timeout_seconds):.1f}s.",
        }
    if error:
        return {"status": "error", "message": error["message"]}
    return result


# ---------------------------------------------------------------------------
# Usage panel reading (OCR) - for usage-aware scheduling
# ---------------------------------------------------------------------------


def _ocr_macos_vision(image_path: str) -> Optional[str]:
    """OCR an image using the macOS built-in Vision framework (pyobjc). No install needed."""
    import Quartz  # type: ignore
    import Vision  # type: ignore
    from Foundation import NSURL  # type: ignore

    url = NSURL.fileURLWithPath_(str(image_path))
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    if not source:
        return None
    cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if cg_image is None:
        return None
    request = Vision.VNRecognizeTextRequest.alloc().init()
    try:
        accurate = getattr(Vision, "VNRequestTextRecognitionLevelAccurate", 0)
        request.setRecognitionLevel_(accurate)
        request.setUsesLanguageCorrection_(False)
    except Exception:
        pass
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    handler.performRequests_error_([request], None)
    lines: List[str] = []
    for obs in (request.results() or []):
        try:
            candidates = obs.topCandidates_(1)
            if candidates and len(candidates) > 0:
                lines.append(str(candidates[0].string()))
        except Exception:
            continue
    return "\n".join(lines)


def _ocr_image_file(image_path: Path) -> Dict[str, Any]:
    """OCR an image. Tries macOS Vision (built-in) then pytesseract."""
    if platform.system().lower() == "darwin":
        try:
            text = _ocr_macos_vision(str(image_path))
            if text is not None:
                return {"status": "success", "engine": "macos_vision", "text": text}
        except Exception as exc:
            LOGGER.debug("macOS Vision OCR failed: %s", exc)
    try:
        import pytesseract  # type: ignore
        from PIL import Image

        text = pytesseract.image_to_string(Image.open(str(image_path)))
        return {"status": "success", "engine": "tesseract", "text": text}
    except Exception as exc:
        LOGGER.debug("tesseract OCR unavailable/failed: %s", exc)
    return {
        "status": "error",
        "engine": None,
        "text": "",
        "message": "No OCR backend available (macOS Vision or tesseract required).",
    }


def _crop_region_from_capture(
    capture_path: Path,
    window_bounds: Optional[Tuple[int, int, int, int]],
    normalized_box: Sequence[float],
) -> Optional[Path]:
    """Crop a window-normalized box out of a full-screen capture, accounting for HiDPI scale."""
    try:
        from PIL import Image

        with Image.open(str(capture_path)) as img:
            cap_w, cap_h = img.size
            # The capture spans the whole virtual desktop, whose origin is negative whenever a
            # display sits left of or above the primary one. Window rects share that coordinate
            # space, so subtract the origin to land in image space.
            origin_x, origin_y, virtual_w, virtual_h = _virtual_screen_rect()
            if virtual_w <= 0 or virtual_h <= 0:
                virtual_w, virtual_h = _screen_size()
                origin_x, origin_y = 0, 0
            scale_x = (cap_w / virtual_w) if virtual_w else 1.0
            scale_y = (cap_h / virtual_h) if virtual_h else 1.0
            if window_bounds:
                left, top, right, bottom = window_bounds
                ww = max(1, right - left)
                wh = max(1, bottom - top)
            else:
                left, top = origin_x, origin_y
                ww, wh = (virtual_w or cap_w), (virtual_h or cap_h)
            x0n, y0n, x1n, y1n = [float(v) for v in normalized_box]
            px0 = int((left + ww * x0n - origin_x) * scale_x)
            py0 = int((top + wh * y0n - origin_y) * scale_y)
            px1 = int((left + ww * x1n - origin_x) * scale_x)
            py1 = int((top + wh * y1n - origin_y) * scale_y)
            if px1 <= px0 or py1 <= py0:
                return None
            crop = img.crop((px0, py0, px1, py1)).copy()
        crop_path = Path(str(capture_path)[:-4] + "-usage-crop.png") if str(capture_path).endswith(".png") else Path(str(capture_path) + "-usage-crop.png")
        crop.save(str(crop_path))
        return crop_path
    except Exception as exc:
        LOGGER.debug("Could not crop usage region: %s", exc)
        return None


def _resolve_reset_time_label(label: str, now) -> Optional[str]:
    """'9:42 PM' -> today at that local time (next day if already past). Returns ISO-8601."""
    import re
    from datetime import timedelta

    match = re.search(r"(\d{1,2}):(\d{2})\s*([AaPp][Mm])", str(label or ""))
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == "pm":
        hour += 12
    minute = int(match.group(2))
    try:
        cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None
    if cand <= now:
        cand = cand + timedelta(days=1)
    return cand.isoformat()


_MONTH_ABBR = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _resolve_reset_date_label(label: str, now) -> Optional[str]:
    """'Jun 24' -> that date this year at local 00:00 (next year if well in the past). ISO-8601."""
    import re

    match = re.search(r"([A-Za-z]{3,})\.?\s+(\d{1,2})", str(label or ""))
    if not match:
        return None
    month = _MONTH_ABBR.get(match.group(1)[:3].lower())
    if not month:
        return None
    day = int(match.group(2))
    try:
        cand = now.replace(month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        return None
    if (now - cand).days > 2:
        cand = cand.replace(year=now.year + 1)
    return cand.isoformat()


def parse_desktop_usage_text(text: str, *, now=None) -> Dict[str, Any]:
    """Parse a Codex-style usage blob into structured windows with absolute local reset times.

    Recognizes a time-based window (e.g. '5h  96%  9:42 PM') and a date-based window
    (e.g. 'Weekly  18%  Jun 24'). All reset times are resolved against the SERVER machine's
    local timezone and current time.
    """
    import re
    from datetime import datetime

    now = now or datetime.now().astimezone()
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    blob = "\n".join(lines)
    result: Dict[str, Any] = {"captured_at_local": now.isoformat(), "raw_lines": lines}

    time_re = re.compile(r"(\d{1,2}:\d{2}\s*[AaPp][Mm])")
    date_re = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b", re.I)
    pct_re = re.compile(r"(\d{1,3})\s*%")

    def _pct_in(s: str) -> Optional[int]:
        m = pct_re.search(s)
        return int(m.group(1)) if m else None

    # The 5h window is the value line containing a clock time; weekly is the line with a month-date.
    five_hour: Optional[Dict[str, Any]] = None
    weekly: Optional[Dict[str, Any]] = None
    for ln in lines:
        tm = time_re.search(ln)
        if tm and five_hour is None:
            five_hour = {"label": "5h", "reset_label": tm.group(1)}
            p = _pct_in(ln)
            if p is not None:
                five_hour["remaining_percent"] = p
            iso = _resolve_reset_time_label(tm.group(1), now)
            if iso:
                five_hour["reset_at_local"] = iso
        dt = date_re.search(ln)
        if dt and weekly is None and "week" in blob.lower():
            weekly = {"label": "Weekly", "reset_label": dt.group(0)}
            p = _pct_in(ln)
            if p is not None:
                weekly["remaining_percent"] = p
            iso = _resolve_reset_date_label(dt.group(0), now)
            if iso:
                weekly["reset_at_local"] = iso

    # If OCR split the percentages onto their own lines, associate by reading order (5h then weekly).
    if (five_hour and "remaining_percent" not in five_hour) or (weekly and "remaining_percent" not in weekly):
        pcts = [int(x) for x in pct_re.findall(blob)]
        if five_hour and "remaining_percent" not in five_hour and len(pcts) >= 1:
            five_hour["remaining_percent"] = pcts[0]
        if weekly and "remaining_percent" not in weekly and len(pcts) >= 2:
            weekly["remaining_percent"] = pcts[1]

    lowered_blob = blob.lower()
    if any(marker in lowered_blob for marker in ("usage limit", "rate limit", "out of codex messages")):
        limit_state: Dict[str, Any] = {"limited": True, "remaining_percent": 0}
        reset_match = time_re.search(blob)
        if reset_match:
            reset_label = reset_match.group(1)
            limit_state["reset_label"] = reset_label
            iso = _resolve_reset_time_label(reset_label, now)
            if iso:
                limit_state["reset_at_local"] = iso
        result["limit_state"] = limit_state

    if five_hour:
        result["five_hour"] = five_hour
    if weekly:
        result["weekly"] = weekly

    for key in ("five_hour", "weekly", "limit_state"):
        entry = result.get(key)
        if entry and entry.get("reset_at_local"):
            try:
                rt = datetime.fromisoformat(entry["reset_at_local"])
                entry["seconds_until_reset"] = int((rt - now).total_seconds())
                entry["minutes_until_reset"] = round((rt - now).total_seconds() / 60.0, 1)
            except Exception:
                pass
    return result


def get_desktop_app_usage(agent_dir: Path, *, launch_if_needed: bool = True) -> Dict[str, Any]:
    """Read the desktop app's usage panel (e.g. Codex 5h + Weekly) via OCR.

    Opens the account/usage menu defined by the pack's ``usage_controls``, expands it, captures
    and crops the usage region, OCR-reads it, and returns structured windows with absolute
    local-timezone reset timestamps (for usage-aware scheduling).
    """
    prepared = _prepare_desktop_app_interaction(agent_dir, launch_if_needed=launch_if_needed)
    if prepared.get("status") != "success":
        return prepared

    manifest = prepared["manifest"]
    pack = prepared["pack"]
    pyautogui = prepared["pyautogui"]
    usage = pack.get("usage_controls") or {}
    if not usage:
        return {"status": "error", "message": "This asset pack does not define usage_controls."}

    open_tid = str(usage.get("open_target_id") or "usage_menu_button")
    open_res = _click_target_id(prepared, open_tid)
    if open_res.get("status") != "success":
        return {**open_res, "stage": "open_usage_menu", "asset_pack_id": pack.get("asset_pack_id")}
    time.sleep(float(usage.get("post_open_delay_seconds") or 0.35))

    toggle_tid = str(usage.get("toggle_target_id") or "")
    if toggle_tid and _resolve_target(pack, toggle_tid):
        _click_target_id(prepared, toggle_tid)  # best-effort expand
        time.sleep(float(usage.get("post_toggle_delay_seconds") or 0.35))

    capture_path = _build_artifact_path(manifest["agent_name"], "usage")
    _capture_desktop_screenshot(capture_path)

    crop_path: Optional[Path] = None
    region_tid = str(usage.get("region_target_id") or "usage_panel")
    region = _resolve_target(pack, region_tid)
    ocr: Dict[str, Any] = {"status": "error", "engine": None, "text": ""}
    region_box = _target_normalized_box(region, pack=pack, window_bounds=prepared["window_bounds"])
    if region_box is not None:
        crop_path = _crop_region_from_capture(capture_path, prepared["window_bounds"], region_box)
        if crop_path:
            ocr = _ocr_image_file(crop_path)
    if not str(ocr.get("text") or "").strip():
        ocr = _ocr_image_file(capture_path)  # fallback: OCR the whole capture

    try:
        pyautogui.press("escape")  # close the menu
    except Exception:
        pass

    parsed = parse_desktop_usage_text(str(ocr.get("text") or ""))
    has_window = bool(parsed.get("five_hour") or parsed.get("weekly") or parsed.get("limit_state"))
    if not has_window:
        window_crop_path = _crop_region_from_capture(capture_path, prepared["window_bounds"], [0, 0, 1, 1])
        if window_crop_path:
            window_ocr = _ocr_image_file(window_crop_path)
            window_parsed = parse_desktop_usage_text(str(window_ocr.get("text") or ""))
            if window_parsed.get("five_hour") or window_parsed.get("weekly") or window_parsed.get("limit_state"):
                crop_path = window_crop_path
                ocr = window_ocr
                parsed = window_parsed
                has_window = True
    return {
        "status": "success" if has_window else "partial_success",
        "agent_name": manifest.get("agent_name"),
        "app_id": manifest.get("app_id"),
        "platform": prepared["details"]["platform"],
        "asset_pack_id": pack.get("asset_pack_id"),
        "usage": parsed,
        "ocr_engine": ocr.get("engine"),
        "ocr_text": ocr.get("text") or "",
        "screenshot": str(capture_path),
        "crop": str(crop_path) if crop_path else None,
        "focused": prepared["focused"],
        "warnings": prepared["warnings"],
    }
