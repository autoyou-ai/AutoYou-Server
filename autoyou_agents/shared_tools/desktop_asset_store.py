# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-desktop-assets-20260929

"""Per-user storage and import for locally calibrated desktop asset packs.

The package manifest and schema describe the agent contract. Captures and
generated packs live outside the source tree, under the current user's data
directory, and are never discovered by the release packager.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-desktop-assets-20260929"


DESKTOP_ASSETS_DIRECTORY = Path("desktop_assets")
USER_PACKS_DIRECTORY = "user_packs"
PREFERENCES_FILENAME = "preferences.json"
MAX_BUNDLE_BYTES = 24 * 1024 * 1024
MAX_ARCHIVE_FILES = 256
MAX_UNCOMPRESSED_BYTES = 48 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_PACKS_PER_BUNDLE = 24
MAX_TARGETS_PER_PACK = 256
MAX_SPRITE_PIXELS = 1_800_000
MAX_SPRITE_DIMENSION = 1600
MAX_SPRITE_BYTES = 5 * 1024 * 1024

_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_PACK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SUPPORTED_PLATFORMS = {"any", "windows", "macos", "linux"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _valid_agent_name(agent_name: str) -> str:
    name = str(agent_name or "").strip()
    if not _AGENT_NAME_RE.fullmatch(name):
        raise ValueError("Invalid desktop agent name.")
    return name


def get_user_desktop_agents_root(app_name: str = "AutoYou") -> Path:
    """Return private per-user agent data, honoring AUTOYOU_TEST_ROOT first."""
    from shared.platform_runtime import get_platform, get_runtime_data_override, get_user_data_dir

    override = get_runtime_data_override(app_name)
    if override is not None:
        root = override / "autoyou_agents"
    elif get_platform() == "windows":
        # Screenshots and crops are machine-local. Do not put them in roaming
        # AppData where profile sync can copy them to another machine.
        local_appdata = os.environ.get("LOCALAPPDATA")
        root = (Path(local_appdata).expanduser() if local_appdata else Path.home() / "AppData" / "Local") / app_name / "autoyou_agents"
    else:
        root = get_user_data_dir(app_name) / "autoyou_agents"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def get_user_desktop_assets_dir(agent_name: str, app_name: str = "AutoYou") -> Path:
    agent_name = _valid_agent_name(agent_name)
    agents_root = get_user_desktop_agents_root(app_name)
    agent_root = agents_root / agent_name
    if agent_root.exists() and agent_root.is_symlink():
        raise ValueError("User desktop agent directory cannot be a symbolic link.")
    agent_root.mkdir(parents=True, exist_ok=True)
    resolved_agent_root = agent_root.resolve()
    if resolved_agent_root.parent != agents_root:
        raise ValueError("User desktop agent directory escapes its storage root.")
    assets_root = agent_root / DESKTOP_ASSETS_DIRECTORY
    if assets_root.exists() and assets_root.is_symlink():
        raise ValueError("User desktop assets directory cannot be a symbolic link.")
    assets_root.mkdir(parents=True, exist_ok=True)
    resolved_assets_root = assets_root.resolve()
    if resolved_assets_root.parent != resolved_agent_root:
        raise ValueError("User desktop assets directory escapes its storage root.")
    return resolved_assets_root


def _embedded_agents_root(anchor: str | Path) -> Path:
    """Resolve code-owned agent templates from source and packaged backends."""
    from shared.platform_runtime import get_embedded_agents_root

    anchor_path = Path(anchor).resolve()
    for directory in (anchor_path.parent, *anchor_path.parents):
        if directory.name == "shared_tools" and directory.parent.name == "autoyou_agents":
            runtime_root = directory.parent.parent
            server_anchor = runtime_root / "server.py"
            return get_embedded_agents_root(server_anchor).resolve()
    return get_embedded_agents_root(anchor_path).resolve()


def discover_packaged_desktop_agents(anchor: str | Path) -> List[str]:
    """List code-owned desktop agents that ship a generic manifest template."""
    try:
        agents_root = _embedded_agents_root(anchor)
    except Exception:
        return []
    if not agents_root.is_dir() or agents_root.is_symlink():
        return []
    names: List[str] = []
    for agent_dir in sorted(agents_root.iterdir(), key=lambda path: path.name.casefold()):
        if not agent_dir.is_dir() or agent_dir.is_symlink() or not _AGENT_NAME_RE.fullmatch(agent_dir.name):
            continue
        assets_dir = agent_dir / DESKTOP_ASSETS_DIRECTORY
        template = assets_dir / "manifest.template.json"
        has_agent_module = (agent_dir / "agent.py").is_file() or any(
            agent_dir.glob("agent.*.pyd")
        ) or any(agent_dir.glob("agent.*.so"))
        if has_agent_module and template.is_file() and not template.is_symlink():
            names.append(agent_dir.name)
    return names


def get_packaged_desktop_agent_dir(agent_name: str, anchor: str | Path) -> Path:
    agent_name = _valid_agent_name(agent_name)
    agents_root = _embedded_agents_root(anchor)
    candidate = (agents_root / agent_name).resolve()
    if candidate.parent != agents_root or agent_name not in discover_packaged_desktop_agents(anchor):
        raise FileNotFoundError(f"No packaged desktop agent named {agent_name}.")
    return candidate


def get_setup_prompt(agent_name: str, anchor: str | Path) -> str:
    agent_dir = get_packaged_desktop_agent_dir(agent_name, anchor)
    prompt_path = agent_dir / DESKTOP_ASSETS_DIRECTORY / "setup_prompt.md"
    if prompt_path.is_symlink() or not prompt_path.is_file():
        raise FileNotFoundError(f"No desktop asset setup prompt is packaged for {agent_name}.")
    return prompt_path.read_text(encoding="utf-8")


def get_packaged_desktop_agent_template(agent_name: str, anchor: str | Path) -> Dict[str, Any]:
    agent_dir = get_packaged_desktop_agent_dir(agent_name, anchor)
    template_path = agent_dir / DESKTOP_ASSETS_DIRECTORY / "manifest.template.json"
    try:
        payload = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Desktop asset template for {agent_name} is invalid.") from exc
    if not isinstance(payload, dict) or str(payload.get("agent_name") or "") != agent_name:
        raise ValueError(f"Desktop asset template for {agent_name} has an invalid identity.")
    return payload


def render_desktop_asset_setup_prompt(
    agent_name: str,
    anchor: str | Path,
    *,
    platform: str,
    app_version: str,
    theme: str,
    display_scale: str,
) -> str:
    prompt = get_setup_prompt(agent_name, anchor)
    platform = str(platform or "").strip().lower()
    platform = {"win": "windows", "darwin": "macos", "mac": "macos"}.get(platform, platform)
    if platform not in _SUPPORTED_PLATFORMS - {"any"}:
        raise ValueError("Choose Windows, macOS, or Linux for setup prompt generation.")
    app_version = str(app_version or "not provided; inspect the installed app").strip()
    if len(app_version) > 80 or any(ord(char) < 32 or char in "`<>" for char in app_version):
        raise ValueError("App version must be at most 80 characters and cannot contain control characters or markup delimiters.")
    theme = str(theme or "auto").strip().lower()
    if theme == "auto":
        theme = detect_desktop_theme()
    if theme not in {"any", "light", "dark", "high_contrast"} and not re.fullmatch(r"custom:[a-z0-9][a-z0-9 _.-]{0,32}", theme):
        raise ValueError("Theme must be auto, any, light, dark, high_contrast, or custom:<name>.")
    display_scale = str(display_scale or "auto").strip().lower()
    if display_scale == "auto":
        detected_scale = detect_display_scale()
        display_scale = f"{detected_scale:g}" if detected_scale is not None else "not detected; inspect system display settings"
    elif display_scale != "any":
        try:
            parsed_scale = float(display_scale)
        except ValueError as exc:
            raise ValueError("Display scale must be auto, any, or a number from 0.5 to 4.0.") from exc
        if not math.isfinite(parsed_scale) or not 0.5 <= parsed_scale <= 4.0:
            raise ValueError("Display scale must be auto, any, or a number from 0.5 to 4.0.")
        display_scale = f"{parsed_scale:g}"
    replacements = {
        "{{AGENT_NAME}}": agent_name,
        "{{PLATFORM}}": platform,
        "{{APP_VERSION}}": app_version,
        "{{THEME}}": theme,
        "{{DISPLAY_SCALE}}": display_scale,
    }
    for token, value in replacements.items():
        prompt = prompt.replace(token, value)
    agent_dir = get_packaged_desktop_agent_dir(agent_name, anchor)
    template = get_packaged_desktop_agent_template(agent_name, anchor)
    schema_path = agent_dir.parent / "shared_tools" / "desktop_asset_schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("The desktop asset schema is missing or invalid in this AutoYou build.") from exc
    prompt += (
        "\n\n## Embedded generic manifest template\n\n"
        "Use this as the starting point for the ZIP manifest. Keep app metadata and preferred target IDs.\n\n"
        "```json\n"
        + json.dumps(template, indent=2, sort_keys=True)
        + "\n```\n\n## Embedded bundle schema\n\n"
        "Validate the ZIP manifest against this schema before returning the ZIP.\n\n"
        "```json\n"
        + json.dumps(schema, indent=2, sort_keys=True)
        + "\n```\n"
    )
    return prompt


def get_desktop_asset_agent_catalog(anchor: str | Path) -> Dict[str, Any]:
    from shared.platform_runtime import get_platform

    platform = {"darwin": "macos"}.get(get_platform(), get_platform())
    agents = []
    for agent_name in discover_packaged_desktop_agents(anchor):
        template = get_packaged_desktop_agent_template(agent_name, anchor)
        preferences = get_desktop_asset_preferences(agent_name)
        agents.append({
            "agent_name": agent_name,
            "title": str(template.get("title") or agent_name),
            "app_id": str(template.get("app_id") or agent_name),
            "preferences": preferences,
            "asset_packs": list_user_desktop_asset_packs(agent_name),
        })
    return {
        "agents": agents,
        "platform": platform,
        "storage": "private per-user application data",
        "max_upload_bytes": MAX_BUNDLE_BYTES,
    }


def load_user_desktop_asset_packs(agent_name: str) -> List[Dict[str, Any]]:
    """Read packs from user data, accepting only contained, regular sprite files."""
    agent_name = _valid_agent_name(agent_name)
    assets_root = get_user_desktop_assets_dir(agent_name)
    packs_root = assets_root / USER_PACKS_DIRECTORY
    if not packs_root.exists():
        return []
    if packs_root.is_symlink() or not packs_root.is_dir():
        raise ValueError("User desktop pack directory is not a regular directory.")
    result: List[Dict[str, Any]] = []
    for directory in sorted(packs_root.iterdir(), key=lambda path: path.name.casefold()):
        if directory.name.startswith(".") or not directory.is_dir():
            continue
        if directory.is_symlink():
            raise ValueError("User desktop pack directory contains a symbolic link.")
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
        if not isinstance(payload, dict) or str(payload.get("agent_name") or "") != agent_name:
            continue
        packs = payload.get("asset_packs")
        if not isinstance(packs, list):
            continue
        for raw_pack in packs:
            if not isinstance(raw_pack, dict):
                continue
            pack = json.loads(json.dumps(raw_pack))
            pack["_user_local_pack"] = True
            for target in pack.get("targets") or []:
                if not isinstance(target, dict):
                    continue
                target["_desktop_assets_root"] = str(assets_root)
                for key in ("expected_image_path", "reference_sprite"):
                    raw_image = target.get(key)
                    if raw_image is None or not str(raw_image).strip():
                        continue
                    try:
                        relative = _archive_path(str(raw_image).replace("\\", "/"))
                        if (
                            len(relative.parts) < 3
                            or relative.parts[0] != USER_PACKS_DIRECTORY
                            or relative.parts[1] != directory.name
                        ):
                            raise ValueError("user pack paths must stay inside their installed pack")
                        path = assets_root.joinpath(*relative.parts)
                        resolved = path.resolve()
                        if not resolved.is_relative_to(directory.resolve()) or path.is_symlink():
                            raise ValueError("user pack image escapes its local storage directory")
                        if not path.is_file():
                            target.pop(key, None)
                    except (OSError, RuntimeError, ValueError):
                        target.pop(key, None)
            result.append(pack)
    return result


def _safe_pack_storage_path(agent_name: str, pack_directory: str) -> Path:
    assets_root = get_user_desktop_assets_dir(agent_name)
    root = assets_root / USER_PACKS_DIRECTORY
    if root.exists() and root.is_symlink():
        raise ValueError("User desktop pack directory cannot be a symbolic link.")
    candidate_name = str(pack_directory or "").strip()
    if not candidate_name or candidate_name in {".", ".."} or "/" in candidate_name or "\\" in candidate_name:
        raise ValueError("Invalid stored desktop pack path.")
    candidate = root / candidate_name
    try:
        resolved_root = root.resolve()
        if resolved_root.parent != assets_root.resolve() or not candidate.resolve().is_relative_to(resolved_root):
            raise ValueError("Desktop pack path escapes its storage directory.")
    except (OSError, RuntimeError) as exc:
        raise ValueError("Desktop pack path cannot be resolved safely.") from exc
    return candidate


def get_desktop_asset_preferences(agent_name: str) -> Dict[str, Any]:
    path = get_user_desktop_assets_dir(agent_name) / PREFERENCES_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    theme = str(payload.get("theme") or "auto").strip().lower()
    if theme not in {"auto", "any", "light", "dark", "high_contrast"} and not theme.startswith("custom:"):
        theme = "auto"
    display_scale = payload.get("display_scale", "auto")
    scale_text = str(display_scale).strip().lower() if display_scale is not None else "none"
    if scale_text not in {"auto", "any", "none"}:
        try:
            scale = float(display_scale)
            display_scale = scale if 0.5 <= scale <= 4.0 else "auto"
        except (TypeError, ValueError):
            display_scale = "auto"
    return {"theme": theme, "display_scale": display_scale}


def save_desktop_asset_preferences(agent_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = _valid_agent_name(agent_name)
    if not isinstance(payload, dict):
        raise ValueError("Desktop asset preferences must be a JSON object.")
    theme = str(payload.get("theme") or "auto").strip().lower()
    if theme not in {"auto", "any", "light", "dark", "high_contrast"} and not re.fullmatch(r"custom:[a-z0-9][a-z0-9 _.-]{0,32}", theme):
        raise ValueError("Choose auto, any, light, dark, high_contrast, or a custom:<name> theme.")
    scale_value: Any = payload.get("display_scale", "auto")
    scale_text = str(scale_value).strip().lower() if scale_value is not None else "none"
    if scale_text not in {"auto", "any", "none"}:
        try:
            scale_value = float(scale_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Display scale must be auto, any, or a number from 0.5 to 4.0.") from exc
        if not math.isfinite(scale_value) or not 0.5 <= scale_value <= 4.0:
            raise ValueError("Display scale must be auto, any, or a number from 0.5 to 4.0.")
    else:
        scale_value = "any" if scale_text == "any" else (None if scale_text == "none" else "auto")
    normalized = {"theme": theme, "display_scale": scale_value}
    path = get_user_desktop_assets_dir(agent_name) / PREFERENCES_FILENAME
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return normalized


def detect_desktop_theme() -> str:
    """Best-effort system appearance. Applications may override this preference."""
    explicit = str(os.environ.get("AUTOYOU_DESKTOP_THEME", "")).strip().lower()
    if explicit in {"light", "dark", "high_contrast"}:
        return explicit
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "light" if int(value) else "dark"
        except Exception:
            return "any"
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            return "dark" if (result.stdout or "").strip().casefold() == "dark" else "light"
        except Exception:
            return "any"
    gtk_theme = str(os.environ.get("GTK_THEME", "")).casefold()
    return "dark" if "dark" in gtk_theme else "any"


def detect_display_scale() -> Optional[float]:
    if sys.platform == "win32":
        try:
            import ctypes

            dpi = int(ctypes.windll.user32.GetDpiForSystem())
            return round(dpi / 96.0, 3) if dpi else None
        except Exception:
            return None
    if sys.platform == "darwin":
        try:
            from AppKit import NSScreen

            screen = NSScreen.mainScreen()
            return round(float(screen.backingScaleFactor()), 3) if screen else None
        except Exception:
            return None
    for variable in ("GDK_SCALE", "QT_SCALE_FACTOR"):
        try:
            scale = float(os.environ.get(variable, ""))
            if 0.5 <= scale <= 4.0:
                return scale
        except (TypeError, ValueError):
            continue
    return None


def effective_desktop_asset_preferences(agent_name: str) -> Dict[str, Any]:
    preferences = get_desktop_asset_preferences(agent_name)
    theme = preferences["theme"]
    if theme == "auto":
        theme = detect_desktop_theme()
    scale = preferences["display_scale"]
    if scale == "auto":
        scale = detect_display_scale()
    return {"theme": theme, "display_scale": scale}


def _archive_path(raw: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise ValueError("Bundle contains an invalid file path.")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Bundle paths must stay inside the selected pack directory.")
    if ":" in path.parts[0]:
        raise ValueError("Bundle contains a drive-qualified file path.")
    return path


def _image_to_clean_png(raw: bytes, relative_path: str) -> bytes:
    if len(raw) > MAX_SPRITE_BYTES:
        raise ValueError(f"Sprite {relative_path} exceeds the {MAX_SPRITE_BYTES // (1024 * 1024)} MB limit.")
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError(f"Sprite {relative_path} must be PNG, JPEG, or WebP.")
            if image.width <= 0 or image.height <= 0 or image.width > MAX_SPRITE_DIMENSION or image.height > MAX_SPRITE_DIMENSION:
                raise ValueError(f"Sprite {relative_path} is too large. Crop controls to at most {MAX_SPRITE_DIMENSION} by {MAX_SPRITE_DIMENSION} pixels.")
            if image.width * image.height > MAX_SPRITE_PIXELS:
                raise ValueError(f"Sprite {relative_path} has too many pixels. Crop it to a control-sized image.")
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            cleaned = ImageOps.exif_transpose(image)
            if cleaned.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
                cleaned = cleaned.convert("RGBA" if "A" in cleaned.getbands() else "RGB")
            output = io.BytesIO()
            cleaned.save(output, format="PNG", optimize=True)
            result = output.getvalue()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Sprite {relative_path} is not a valid supported image.") from exc
    if len(result) > MAX_SPRITE_BYTES:
        raise ValueError(f"Cleaned sprite {relative_path} exceeds the size limit.")
    return result


def _pack_image_references(pack: Dict[str, Any]) -> List[str]:
    referenced: List[str] = []
    for target in pack.get("targets") or []:
        if not isinstance(target, dict):
            raise ValueError("Each desktop target must be a JSON object.")
        for key in ("expected_image_path", "reference_sprite"):
            raw = target.get(key)
            if raw is None or not str(raw).strip():
                continue
            path = _archive_path(str(raw).strip())
            if Path(path.name).suffix.lower() not in _IMAGE_SUFFIXES:
                raise ValueError(f"Desktop image reference {raw} must use PNG, JPEG, or WebP.")
            normalized = path.as_posix()
            if normalized not in referenced:
                referenced.append(normalized)
    return referenced


def _validate_pack(pack: Any, *, default_platform: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(pack, dict):
        raise ValueError("Each asset pack must be a JSON object.")
    clean = dict(pack)
    pack_id = str(clean.get("asset_pack_id") or "").strip()
    if not _PACK_ID_RE.fullmatch(pack_id):
        raise ValueError("Each asset pack needs an asset_pack_id containing only letters, numbers, dots, underscores, or hyphens.")
    platform = str(clean.get("platform") or default_platform or "any").strip().lower()
    platform = {"win": "windows", "mac": "macos", "darwin": "macos"}.get(platform, platform)
    if platform not in _SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported platform in asset pack: {platform}")
    coordinate_space = str(clean.get("coordinate_space") or "window").strip().lower()
    if coordinate_space not in {"window", "screen"}:
        raise ValueError("coordinate_space must be window or screen.")
    valid_until = str(clean.get("valid_until") or "").strip()
    if valid_until:
        try:
            date.fromisoformat(valid_until)
        except ValueError as exc:
            raise ValueError("valid_until must be an ISO calendar date.") from exc
    theme = str(clean.get("theme") or "any").strip().lower()
    if theme not in {"any", "light", "dark", "high_contrast"} and not re.fullmatch(r"custom:[a-z0-9][a-z0-9 _.-]{0,32}", theme):
        raise ValueError("Theme must be any, light, dark, high_contrast, or custom:<name>.")
    targets = clean.get("targets")
    if not isinstance(targets, list) or not targets or len(targets) > MAX_TARGETS_PER_PACK:
        raise ValueError(f"Each pack needs between 1 and {MAX_TARGETS_PER_PACK} targets.")
    seen_targets: set[str] = set()
    scale_value = clean.get("display_scale", "any")
    scale_text = str(scale_value).strip().lower() if scale_value is not None else "any"
    if scale_text != "any":
        if isinstance(scale_value, bool):
            raise ValueError("Display scale must be any or a number from 0.5 to 4.0.")
        try:
            scale_value = float(scale_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Display scale must be any or a number from 0.5 to 4.0.") from exc
        if not math.isfinite(scale_value) or not 0.5 <= scale_value <= 4.0:
            raise ValueError("Display scale must be any or a number from 0.5 to 4.0.")
    else:
        scale_value = "any"
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("Each desktop target must be a JSON object.")
        target_id = str(target.get("target_id") or "").strip()
        if not target_id or len(target_id) > 128 or target_id in seen_targets:
            raise ValueError("Target IDs must be unique, non-empty strings of at most 128 characters.")
        seen_targets.add(target_id)
        for key in ("click_point", "normalized_box"):
            value = target.get(key)
            if value is None:
                continue
            expected_length = 2 if key == "click_point" else 4
            if not isinstance(value, (list, tuple)) or len(value) != expected_length:
                raise ValueError(f"Target {target_id} has an invalid {key}.")
            if any(isinstance(part, bool) for part in value):
                raise ValueError(f"Target {target_id} has a boolean {key} value.")
            try:
                numbers = [float(part) for part in value]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Target {target_id} has non-numeric {key} values.") from exc
            if not all(math.isfinite(part) for part in numbers):
                raise ValueError(f"Target {target_id} has non-finite {key} values.")
            if not all(0.0 <= part <= 1.0 for part in numbers):
                raise ValueError(f"Target {target_id} {key} values must be normalized between 0 and 1.")
            if key == "normalized_box" and (numbers[0] > numbers[2] or numbers[1] > numbers[3]):
                raise ValueError(f"Target {target_id} normalized_box edges are out of order.")
        for key in ("anchored_point", "anchored_box"):
            value = target.get(key)
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError(f"Target {target_id} {key} must be an object.")
            if not {"x", "y"}.issubset(value):
                raise ValueError(f"Target {target_id} {key} must define x and y.")
            for axis in ("x", "y"):
                axis_values = value[axis]
                if key == "anchored_box":
                    if not isinstance(axis_values, list) or len(axis_values) != 2:
                        raise ValueError(f"Target {target_id} anchored_box {axis} must have two anchors.")
                    for anchor in axis_values:
                        _validate_anchor_axis(anchor, target_id=target_id, axis=axis)
                else:
                    _validate_anchor_axis(axis_values, target_id=target_id, axis=axis)
        for key in ("expected_image_path", "reference_sprite"):
            value = target.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 240):
                raise ValueError(f"Target {target_id} has an invalid {key}.")
    for key in ("app_version", "app_version_min", "app_version_max"):
        value = clean.get(key)
        if value is not None and (
            not isinstance(value, str)
            or len(value) > 80
            or (value.strip() and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,79}", value.strip()))
        ):
            raise ValueError(f"Asset pack {key} must be a string of at most 80 characters.")
    minimum = str(clean.get("app_version_min") or "").strip()
    maximum = str(clean.get("app_version_max") or "").strip()
    if minimum and maximum and tuple(int(p) for p in re.findall(r"\d+", minimum)) > tuple(int(p) for p in re.findall(r"\d+", maximum)):
        raise ValueError("Asset pack app_version_min must not exceed app_version_max.")
    clean["asset_pack_id"] = pack_id
    clean["platform"] = platform
    clean["theme"] = theme
    clean["display_scale"] = scale_value
    clean["targets"] = targets
    clean["coordinate_space"] = coordinate_space
    clean["valid_until"] = valid_until or None
    return clean


def _validate_anchor_axis(spec: Any, *, target_id: str, axis: str) -> None:
    if isinstance(spec, bool):
        raise ValueError(f"Target {target_id} has a boolean {axis} anchor.")
    if isinstance(spec, (int, float)):
        if not math.isfinite(float(spec)) or abs(float(spec)) > 100_000:
            raise ValueError(f"Target {target_id} has an out-of-range {axis} anchor.")
        return
    if not isinstance(spec, dict):
        raise ValueError(f"Target {target_id} has an invalid {axis} anchor.")
    origin = str(spec.get("from") or ("left" if axis == "x" else "top")).strip().lower()
    valid_origins = {"left", "right", "center"} if axis == "x" else {"top", "bottom", "center"}
    if origin not in valid_origins:
        raise ValueError(f"Target {target_id} has an invalid {axis} anchor origin.")
    for key in ("offset_px", "requires_room_right_px", "room_anchor_offset_px", "fallback_offset_px"):
        if key not in spec:
            continue
        value = spec[key]
        if isinstance(value, bool):
            raise ValueError(f"Target {target_id} has a boolean {axis} anchor offset.")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Target {target_id} has a non-numeric {axis} anchor offset.") from exc
        if not math.isfinite(number) or abs(number) > 100_000 or (key == "requires_room_right_px" and number < 0):
            raise ValueError(f"Target {target_id} has an out-of-range {axis} anchor offset.")


def _manifest_member(names: Iterable[str]) -> tuple[str, str]:
    candidates = []
    for name in names:
        path = _archive_path(name)
        if path.name.casefold() == "manifest.json":
            candidates.append(path.as_posix())
    if len(candidates) != 1:
        raise ValueError("ZIP must contain exactly one manifest.json at its root or one top-level pack folder.")
    manifest_path = PurePosixPath(candidates[0])
    if len(manifest_path.parent.parts) > 1:
        raise ValueError("manifest.json must be at the ZIP root or one top-level pack folder.")
    prefix = manifest_path.parent.as_posix()
    return candidates[0], "" if prefix == "." else prefix + "/"


def _validated_archive(payload: bytes, expected_agent_name: str) -> tuple[Dict[str, Any], Dict[str, bytes]]:
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("Select a non-empty desktop asset ZIP.")
    if len(payload) > MAX_BUNDLE_BYTES:
        raise ValueError(f"Desktop asset ZIP exceeds the {MAX_BUNDLE_BYTES // (1024 * 1024)} MB upload limit.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("The selected file is not a valid ZIP archive.") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise ValueError(f"ZIP contains too many files. Limit: {MAX_ARCHIVE_FILES}.")
        files: Dict[str, zipfile.ZipInfo] = {}
        total_size = 0
        for info in infos:
            if info.is_dir():
                continue
            path = _archive_path(info.filename)
            if info.flag_bits & 0x1:
                raise ValueError("Encrypted ZIP entries are not supported.")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ValueError("ZIP symbolic links are not supported.")
            if info.file_size > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("A ZIP member is too large.")
            total_size += info.file_size
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("ZIP expands beyond the uncompressed size limit.")
            if info.file_size > 1024 * 1024 and info.compress_size and info.file_size / info.compress_size > 150:
                raise ValueError("ZIP has an unusually high compression ratio and was rejected.")
            key = path.as_posix()
            if key in files:
                raise ValueError("ZIP contains duplicate file paths.")
            files[key] = info

        manifest_member, prefix = _manifest_member(files.keys())
        manifest_info = files[manifest_member]
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise ValueError("Desktop manifest is too large.")
        try:
            raw_manifest = archive.read(manifest_info)
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (UnicodeError, ValueError, OSError, RuntimeError, EOFError, zipfile.BadZipFile, RecursionError) as exc:
            raise ValueError("Desktop manifest must be valid UTF-8 JSON.") from exc
        if not isinstance(manifest, dict):
            raise ValueError("Desktop manifest must contain a JSON object.")
        try:
            json.dumps(manifest, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("Desktop manifest contains non-standard numbers or excessive nesting.") from exc
        if str(manifest.get("agent_name") or "").strip() != expected_agent_name:
            raise ValueError(f"This ZIP is not for {expected_agent_name}.")
        try:
            schema_version = int(manifest.get("schema_version", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Unsupported desktop manifest schema version.") from exc
        if schema_version not in {1, 2}:
            raise ValueError("Desktop manifest schema_version must be 1 or 2.")
        raw_packs = manifest.get("asset_packs")
        if not isinstance(raw_packs, list) or not 1 <= len(raw_packs) <= MAX_PACKS_PER_BUNDLE:
            raise ValueError(f"Bundle must contain between 1 and {MAX_PACKS_PER_BUNDLE} asset packs.")
        packs = [_validate_pack(pack, default_platform=manifest.get("platform")) for pack in raw_packs]
        pack_ids = [pack["asset_pack_id"] for pack in packs]
        if len(set(pack_ids)) != len(pack_ids):
            raise ValueError("Pack IDs must be unique within the bundle.")

        referenced_paths = sorted({path for pack in packs for path in _pack_image_references(pack)})
        image_bytes: Dict[str, bytes] = {}
        for relative_path in referenced_paths:
            archive_path = prefix + relative_path
            info = files.get(archive_path)
            if info is None:
                raise ValueError(f"Manifest references a missing sprite: {relative_path}")
            suffix = Path(relative_path).suffix.lower()
            if suffix not in _IMAGE_SUFFIXES:
                raise ValueError(f"Unsupported sprite file type: {suffix}")
            try:
                raw_image = archive.read(info)
            except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
                raise ValueError(f"Sprite {relative_path} could not be read from the ZIP.") from exc
            image_bytes[relative_path] = _image_to_clean_png(raw_image, relative_path)
        return {"schema_version": schema_version, "agent_name": expected_agent_name, "app_id": str(manifest.get("app_id") or "").strip(), "asset_packs": packs}, image_bytes


def list_user_desktop_asset_packs(agent_name: str) -> List[Dict[str, Any]]:
    agent_name = _valid_agent_name(agent_name)
    assets_root = get_user_desktop_assets_dir(agent_name)
    packs_root = assets_root / USER_PACKS_DIRECTORY
    if not packs_root.is_dir() or packs_root.is_symlink():
        return []
    result: List[Dict[str, Any]] = []
    for directory in sorted(packs_root.iterdir(), key=lambda path: path.name.casefold()):
        if not directory.is_dir() or directory.is_symlink():
            continue
        manifest_path = directory / "manifest.json"
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
        for pack in payload.get("asset_packs", []) if isinstance(payload, dict) else []:
            if not isinstance(pack, dict):
                continue
            result.append({
                "asset_pack_id": str(pack.get("asset_pack_id") or ""),
                "storage_id": directory.name,
                "platform": str(pack.get("platform") or "any"),
                "app_version": str(pack.get("app_version") or ""),
                "app_version_min": str(pack.get("app_version_min") or ""),
                "app_version_max": str(pack.get("app_version_max") or ""),
                "theme": str(pack.get("theme") or "any"),
                "display_scale": pack.get("display_scale", "any"),
                "description": str(pack.get("description") or "")[:240],
                "target_count": len(pack.get("targets") or []),
            })
    return result


def import_user_desktop_asset_bundle(agent_name: str, payload: bytes, *, expected_app_id: Optional[str] = None) -> Dict[str, Any]:
    agent_name = _valid_agent_name(agent_name)
    manifest, images = _validated_archive(payload, agent_name)
    if expected_app_id and manifest.get("app_id") and manifest["app_id"] != expected_app_id:
        raise ValueError("The ZIP app_id does not match the selected desktop agent.")

    existing_ids = {item["asset_pack_id"] for item in list_user_desktop_asset_packs(agent_name)}
    duplicates = existing_ids.intersection(pack["asset_pack_id"] for pack in manifest["asset_packs"])
    if duplicates:
        raise ValueError("A pack with this ID is already installed. Give the new pack a distinct asset_pack_id.")

    assets_root = get_user_desktop_assets_dir(agent_name)
    packs_root = assets_root / USER_PACKS_DIRECTORY
    if packs_root.exists() and packs_root.is_symlink():
        raise ValueError("User desktop pack directory cannot be a symbolic link.")
    packs_root.mkdir(parents=True, exist_ok=True)
    if packs_root.resolve().parent != assets_root.resolve():
        raise ValueError("User desktop pack directory escapes its storage root.")
    transaction_id = uuid.uuid4().hex
    staging_root = packs_root / f".staging-{transaction_id}"
    staging_root.mkdir()
    final_paths: List[Path] = []
    try:
        image_locations: Dict[str, str] = {}
        for relative_path, raw in images.items():
            digest = hashlib.sha256(raw).hexdigest()[:20]
            stored_relative = f"sprites/{digest}.png"
            image_locations[relative_path] = stored_relative
        for pack in manifest["asset_packs"]:
            storage_id = f"{re.sub(r'[^A-Za-z0-9._-]+', '-', pack['asset_pack_id']).strip('-.')[:40] or 'pack'}-{uuid.uuid4().hex[:8]}"
            staging_dir = staging_root / storage_id
            staging_dir.mkdir()
            copied_pack = json.loads(json.dumps(pack))
            for target in copied_pack.get("targets") or []:
                for key in ("expected_image_path", "reference_sprite"):
                    raw_path = target.get(key)
                    if raw_path is None or not str(raw_path).strip():
                        continue
                    relative = _archive_path(str(raw_path).strip()).as_posix()
                    target[key] = f"{USER_PACKS_DIRECTORY}/{storage_id}/{image_locations[relative]}"
            for original_path, stored_relative in image_locations.items():
                target_refs = {str(target.get(key) or "").replace("\\", "/") for target in copied_pack.get("targets") or [] for key in ("expected_image_path", "reference_sprite")}
                expected = f"{USER_PACKS_DIRECTORY}/{storage_id}/{stored_relative}"
                if expected not in target_refs:
                    continue
                image_path = staging_dir.joinpath(*PurePosixPath(stored_relative).parts)
                image_path.parent.mkdir(parents=True, exist_ok=True)
                image_path.write_bytes(images[original_path])
            stored_manifest = {
                "schema_version": 2,
                "agent_name": agent_name,
                "app_id": expected_app_id or manifest.get("app_id") or agent_name,
                "asset_packs": [copied_pack],
            }
            (staging_dir / "manifest.json").write_text(json.dumps(stored_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for directory in sorted(staging_root.iterdir(), key=lambda path: path.name):
            final_path = packs_root / directory.name
            if final_path.exists():
                raise ValueError("A desktop pack storage ID already exists. Retry the import.")
            os.replace(directory, final_path)
            final_paths.append(final_path)
    except Exception:
        for path in final_paths:
            if path.parent.resolve() == packs_root.resolve() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
        raise
    finally:
        if staging_root.exists() and not staging_root.is_symlink():
            shutil.rmtree(staging_root, ignore_errors=True)

    return {
        "success": True,
        "agent_name": agent_name,
        "installed_pack_ids": [pack["asset_pack_id"] for pack in manifest["asset_packs"]],
        "storage": "private per-user application data",
        "asset_packs": list_user_desktop_asset_packs(agent_name),
    }


def remove_user_desktop_asset_pack(agent_name: str, storage_id: str) -> List[Dict[str, Any]]:
    agent_name = _valid_agent_name(agent_name)
    candidate = _safe_pack_storage_path(agent_name, storage_id)
    if candidate.is_symlink():
        raise ValueError("Refusing to remove a symbolic link as a desktop pack.")
    if not candidate.is_dir():
        raise FileNotFoundError("Desktop pack was not found.")
    packs_root = candidate.parent.resolve()
    if candidate.resolve().parent != packs_root:
        raise ValueError("Desktop pack path escapes its storage directory.")
    shutil.rmtree(candidate)
    return list_user_desktop_asset_packs(agent_name)


def export_user_desktop_asset_pack(agent_name: str, storage_id: str) -> bytes:
    agent_name = _valid_agent_name(agent_name)
    directory = _safe_pack_storage_path(agent_name, storage_id)
    if directory.is_symlink() or not directory.is_dir():
        raise FileNotFoundError("Desktop pack was not found.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("Stored desktop pack contains a symbolic link.")
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(directory.resolve()):
                raise ValueError("Stored desktop pack contains a path outside its directory.")
            archive.write(path, path.relative_to(directory).as_posix())
    return output.getvalue()

