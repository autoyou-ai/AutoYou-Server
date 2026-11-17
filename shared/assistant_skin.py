# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Assistant Agent Skin and Customization Pattern Manager.

Handles the packaging, validation, persistence, import, export, and preset
catalog for the customizable Assistant robot. Both the browser Assistant
website and the native iOS and Android clients share this specification and
wire contract.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

LOGGER = logging.getLogger(__name__)

HEX_COLOR_REGEX = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
SAFE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{1,64}$")

DEFAULT_SPRINGS: Dict[str, Dict[str, float]] = {
    "lean": {"k": 38.0, "c": 0.72},
    "bodyY": {"k": 95.0, "c": 0.55},
    "squash": {"k": 240.0, "c": 0.30},
    "headX": {"k": 110.0, "c": 0.62},
    "headY": {"k": 120.0, "c": 0.50},
    "tilt": {"k": 55.0, "c": 0.60},
    "armL": {"k": 80.0, "c": 0.55},
    "armR": {"k": 80.0, "c": 0.55},
    "foreL": {"k": 150.0, "c": 0.38},
    "foreR": {"k": 150.0, "c": 0.38},
    "browLY": {"k": 110.0, "c": 0.60},
    "browRY": {"k": 110.0, "c": 0.60},
    "browLTilt": {"k": 85.0, "c": 0.60},
    "browRTilt": {"k": 85.0, "c": 0.60},
}

DEFAULT_PALETTE: Dict[str, Any] = {
    "shell_gradient": ["#ffffff", "#eef1f8", "#c7cfe3"],
    "shell_head_gradient": ["#ffffff", "#eef1f8", "#c3cbe0"],
    "visor_gradient": ["#16244f", "#0a1331", "#04081a"],
    "accent_gradient": ["#5b86ff", "#2c4fd1", "#1b2f8c"],
    "glow_primary": "#3fd8ff",
    "glow_secondary": "#25b0ff",
    "core_color": "#ffffff",
    "halo_color": "#4c75ff",
}

DEFAULT_PHYSICS: Dict[str, Any] = {
    "springs": DEFAULT_SPRINGS,
    "squash_impulse": -2.6,
    "gaze_sensitivity": 1.0,
    "breath_speed": 1.6,
    "breath_amplitude": 2.2,
}

DEFAULT_EMOTES: Dict[str, Any] = {
    "eye_style": "ring",
    "mouth_style": "glow_line",
    "blink_interval_seconds": 3.8,
    "greeting_wave_enabled": True,
}

DEFAULT_AUDIO_REACTIVITY: Dict[str, Any] = {
    "mic_equalizer_bands": 7,
    "speaker_mouth_tracking": True,
    "ear_glow_enabled": True,
}

DEFAULT_SKIN: Dict[str, Any] = {
    "id": "autoyou_classic",
    "name": "AutoYou Classic",
    "author": "AutoYou",
    "version": "1.0.0",
    "description": "The classic pearl white and cobalt blue AutoYou assistant robot.",
    "palette": DEFAULT_PALETTE,
    "physics": DEFAULT_PHYSICS,
    "emotes": DEFAULT_EMOTES,
    "audio_reactivity": DEFAULT_AUDIO_REACTIVITY,
    "assets": {
        "custom_svg_paths": {},
    },
}

PRESET_SKINS: Dict[str, Dict[str, Any]] = {
    "autoyou_classic": DEFAULT_SKIN,
    "neon_cyber": {
        "id": "neon_cyber",
        "name": "Neon Cyber",
        "author": "AutoYou",
        "version": "1.0.0",
        "description": "High-contrast dark synthwave cyberpunk aesthetic with electric cyan and hot magenta.",
        "palette": {
            "shell_gradient": ["#1e2230", "#141724", "#0c0e17"],
            "shell_head_gradient": ["#1e2230", "#141724", "#0b0d15"],
            "visor_gradient": ["#1f0a29", "#0e0317", "#05010a"],
            "accent_gradient": ["#ff2a85", "#d9146c", "#850541"],
            "glow_primary": "#00f0ff",
            "glow_secondary": "#ff2a85",
            "core_color": "#00f0ff",
            "halo_color": "#ff2a85",
        },
        "physics": {
            **DEFAULT_PHYSICS,
            "breath_speed": 1.9,
            "breath_amplitude": 2.5,
        },
        "emotes": {
            **DEFAULT_EMOTES,
            "eye_style": "neon",
        },
        "audio_reactivity": DEFAULT_AUDIO_REACTIVITY,
        "assets": {"custom_svg_paths": {}},
    },
    "solar_amber": {
        "id": "solar_amber",
        "name": "Solar Amber",
        "author": "AutoYou",
        "version": "1.0.0",
        "description": "Warm golden titanium chassis with solar flare amber optics.",
        "palette": {
            "shell_gradient": ["#fffdf0", "#f8f2de", "#dfd2ac"],
            "shell_head_gradient": ["#fffdf0", "#f7f0d8", "#dbcfa6"],
            "visor_gradient": ["#2e1d08", "#170e03", "#080401"],
            "accent_gradient": ["#ffaa00", "#e67300", "#993d00"],
            "glow_primary": "#ffd24d",
            "glow_secondary": "#ff9900",
            "core_color": "#fff2cc",
            "halo_color": "#ffaa00",
        },
        "physics": {
            **DEFAULT_PHYSICS,
            "breath_speed": 1.4,
            "breath_amplitude": 2.0,
        },
        "emotes": DEFAULT_EMOTES,
        "audio_reactivity": DEFAULT_AUDIO_REACTIVITY,
        "assets": {"custom_svg_paths": {}},
    },
    "emerald_tech": {
        "id": "emerald_tech",
        "name": "Emerald Tech",
        "author": "AutoYou",
        "version": "1.0.0",
        "description": "Brushed titanium finish with glowing matrix emerald optics.",
        "palette": {
            "shell_gradient": ["#f3f6f5", "#e1e8e5", "#b9c7c2"],
            "shell_head_gradient": ["#f3f6f5", "#e1e8e5", "#b4c3bd"],
            "visor_gradient": ["#082417", "#03140c", "#010704"],
            "accent_gradient": ["#10b981", "#059669", "#047857"],
            "glow_primary": "#34d399",
            "glow_secondary": "#10b981",
            "core_color": "#d1fae5",
            "halo_color": "#10b981",
        },
        "physics": {
            **DEFAULT_PHYSICS,
            "breath_speed": 1.5,
            "breath_amplitude": 2.1,
        },
        "emotes": DEFAULT_EMOTES,
        "audio_reactivity": DEFAULT_AUDIO_REACTIVITY,
        "assets": {"custom_svg_paths": {}},
    },
    "cosmic_violet": {
        "id": "cosmic_violet",
        "name": "Cosmic Violet",
        "author": "AutoYou",
        "version": "1.0.0",
        "description": "Deep astral violet chassis with luminous ultraviolet core energy.",
        "palette": {
            "shell_gradient": ["#f7f2ff", "#e8dcfa", "#c7b2ea"],
            "shell_head_gradient": ["#f7f2ff", "#e8dcfa", "#c4ade8"],
            "visor_gradient": ["#1b0c30", "#0f051c", "#05010b"],
            "accent_gradient": ["#a855f7", "#7e22ce", "#581c87"],
            "glow_primary": "#c084fc",
            "glow_secondary": "#e879f9",
            "core_color": "#fae8ff",
            "halo_color": "#a855f7",
        },
        "physics": {
            **DEFAULT_PHYSICS,
            "breath_speed": 1.7,
            "breath_amplitude": 2.4,
        },
        "emotes": DEFAULT_EMOTES,
        "audio_reactivity": DEFAULT_AUDIO_REACTIVITY,
        "assets": {"custom_svg_paths": {}},
    },
    "stealth_dark": {
        "id": "stealth_dark",
        "name": "Stealth Dark",
        "author": "AutoYou",
        "version": "1.0.0",
        "description": "Matte graphite composite shell with tactical crimson indicator lighting.",
        "palette": {
            "shell_gradient": ["#2d313a", "#21252e", "#15181e"],
            "shell_head_gradient": ["#2d313a", "#21252e", "#13161c"],
            "visor_gradient": ["#1c0a0c", "#0f0305", "#050102"],
            "accent_gradient": ["#ef4444", "#dc2626", "#991b1b"],
            "glow_primary": "#f87171",
            "glow_secondary": "#ef4444",
            "core_color": "#fee2e2",
            "halo_color": "#dc2626",
        },
        "physics": {
            **DEFAULT_PHYSICS,
            "breath_speed": 1.3,
            "breath_amplitude": 1.8,
        },
        "emotes": DEFAULT_EMOTES,
        "audio_reactivity": DEFAULT_AUDIO_REACTIVITY,
        "assets": {"custom_svg_paths": {}},
    },
}


def _clean_color(val: Any, default: str) -> str:
    if isinstance(val, str):
        trimmed = val.strip()
        if HEX_COLOR_REGEX.match(trimmed):
            return trimmed.lower()
    return default


def _clean_gradient(val: Any, default: List[str]) -> List[str]:
    if isinstance(val, (list, tuple)) and len(val) >= 2:
        return [_clean_color(v, default[min(i, len(default) - 1)]) for i, v in enumerate(val[:4])]
    return list(default)


def _clean_float(val: Any, default: float, low: float, high: float) -> float:
    try:
        num = float(val)
        if num != num:  # NaN
            return default
        return max(low, min(high, num))
    except (TypeError, ValueError):
        return default


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def validate_skin(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Validate and normalize a skin dictionary against schema and safe ranges."""
    source = raw if isinstance(raw, Mapping) else {}
    skin_id = str(source.get("id") or "custom").strip()
    if skin_id == "classic":
        skin_id = "autoyou_classic"
    if not SAFE_ID_REGEX.match(skin_id):
        skin_id = "custom"

    name = str(source.get("name") or "Custom Skin").strip()[:64]
    author = str(source.get("author") or "Designer").strip()[:64]
    version = str(source.get("version") or "1.0.0").strip()[:16]
    description = str(source.get("description") or "").strip()[:256]

    raw_pal = source.get("palette") if isinstance(source.get("palette"), Mapping) else {}
    raw_shell = _first_present(raw_pal, "shell_gradient", "shellGradient")
    raw_shell_head = _first_present(raw_pal, "shell_head_gradient", "shellHeadGradient") or raw_shell
    raw_visor = _first_present(raw_pal, "visor_gradient", "visorGradient")
    if raw_visor is None and isinstance(raw_pal.get("background"), str):
        bg = _clean_color(raw_pal.get("background"), DEFAULT_PALETTE["visor_gradient"][0])
        raw_visor = [bg, bg, bg]

    raw_accent = _first_present(raw_pal, "accent_gradient", "accentGradient")
    if raw_accent is None and isinstance(raw_pal.get("accent"), str):
        acc = _clean_color(raw_pal.get("accent"), DEFAULT_PALETTE["accent_gradient"][0])
        raw_accent = [acc, acc, acc]

    raw_glow_primary = _first_present(raw_pal, "glow_primary", "glowPrimary", "glow")
    raw_glow_secondary = _first_present(raw_pal, "glow_secondary", "glowSecondary", "accent", "glow")
    raw_core = _first_present(raw_pal, "core_color", "coreColor", "core")
    raw_halo = _first_present(raw_pal, "halo_color", "haloColor", "accent")

    palette = {
        "shell_gradient": _clean_gradient(raw_shell, DEFAULT_PALETTE["shell_gradient"]),
        "shell_head_gradient": _clean_gradient(raw_shell_head, DEFAULT_PALETTE["shell_head_gradient"]),
        "visor_gradient": _clean_gradient(raw_visor, DEFAULT_PALETTE["visor_gradient"]),
        "accent_gradient": _clean_gradient(raw_accent, DEFAULT_PALETTE["accent_gradient"]),
        "glow_primary": _clean_color(raw_glow_primary, DEFAULT_PALETTE["glow_primary"]),
        "glow_secondary": _clean_color(raw_glow_secondary, DEFAULT_PALETTE["glow_secondary"]),
        "core_color": _clean_color(raw_core, DEFAULT_PALETTE["core_color"]),
        "halo_color": _clean_color(raw_halo, DEFAULT_PALETTE["halo_color"]),
    }

    raw_phy = source.get("physics") if isinstance(source.get("physics"), Mapping) else {}
    springs: Dict[str, Dict[str, float]] = {}
    raw_springs = raw_phy.get("springs") if isinstance(raw_phy.get("springs"), Mapping) else {}
    stiffness_scale = 1.0
    direct_stiffness_k: Optional[float] = None
    raw_stiffness = _first_present(raw_phy, "joint_stiffness", "jointStiffness")
    if not raw_springs and raw_stiffness is not None:
        try:
            numeric_stiff = float(raw_stiffness)
        except (TypeError, ValueError):
            numeric_stiff = 0.22
        if numeric_stiff > 1.0:
            direct_stiffness_k = _clean_float(numeric_stiff, 38.0, 5.0, 500.0)
        else:
            stiff_val = _clean_float(numeric_stiff, 0.22, 0.05, 0.6)
            stiffness_scale = stiff_val / 0.22

    for joint, def_vals in DEFAULT_SPRINGS.items():
        user_joint = raw_springs.get(joint) if isinstance(raw_springs.get(joint), Mapping) else {}
        default_k = direct_stiffness_k if direct_stiffness_k is not None else (def_vals["k"] * stiffness_scale)
        springs[joint] = {
            "k": _clean_float(user_joint.get("k"), default_k, 5.0, 500.0),
            "c": _clean_float(user_joint.get("c"), def_vals["c"], 0.05, 3.0),
        }

    raw_squash = _first_present(raw_phy, "squash_impulse", "squashImpulse")
    raw_gaze = _first_present(raw_phy, "gaze_sensitivity", "gazeSensitivity")
    if raw_gaze is None:
        raw_gaze_lag = _first_present(raw_phy, "gaze_lag", "gazeLag")
        if raw_gaze_lag is not None:
            lag_val = _clean_float(raw_gaze_lag, 0.12, 0.02, 0.4)
            raw_gaze = round(lag_val / 0.12, 2)
    raw_breath_speed = _first_present(raw_phy, "breath_speed", "breathSpeed")
    raw_breath_amp = _first_present(raw_phy, "breath_amplitude", "breathAmplitude")

    physics = {
        "springs": springs,
        "squash_impulse": _clean_float(raw_squash, DEFAULT_PHYSICS["squash_impulse"], -10.0, 0.0),
        "gaze_sensitivity": _clean_float(raw_gaze, DEFAULT_PHYSICS["gaze_sensitivity"], 0.1, 3.0),
        "breath_speed": _clean_float(raw_breath_speed, DEFAULT_PHYSICS["breath_speed"], 0.2, 5.0),
        "breath_amplitude": _clean_float(raw_breath_amp, DEFAULT_PHYSICS["breath_amplitude"], 0.0, 10.0),
    }

    raw_emo = source.get("emotes") if isinstance(source.get("emotes"), Mapping) else {}
    raw_eye = (
        _first_present(raw_emo, "eye_style", "eyeStyle")
        or _first_present(raw_pal, "eye_optics", "eyeOptics")
        or _first_present(source, "eye_optics", "eyeOptics", "eye_style", "eyeStyle")
    )
    eye_style_map = {
        "digital_circles": "ring",
        "warm_dots": "ring",
        "neon_slits": "neon",
        "cyber_cross": "visor_bar",
    }
    eye_style = str(raw_eye or DEFAULT_EMOTES["eye_style"]).strip().lower()
    eye_style = eye_style_map.get(eye_style, eye_style)
    if eye_style not in {"ring", "neon", "happy", "sleep", "visor_bar"}:
        eye_style = "ring"
    raw_mouth = _first_present(raw_emo, "mouth_style", "mouthStyle")
    mouth_style = str(raw_mouth or DEFAULT_EMOTES["mouth_style"]).strip().lower()
    if mouth_style not in {"glow_line", "classic_curve", "equalizer_only"}:
        mouth_style = "glow_line"

    raw_blink = _first_present(raw_emo, "blink_interval_seconds", "blinkIntervalSeconds")
    raw_wave = _first_present(raw_emo, "greeting_wave_enabled", "greetingWaveEnabled")
    emotes = {
        "eye_style": eye_style,
        "mouth_style": mouth_style,
        "blink_interval_seconds": _clean_float(raw_blink, DEFAULT_EMOTES["blink_interval_seconds"], 1.0, 15.0),
        "greeting_wave_enabled": bool(True if raw_wave is None else raw_wave),
    }

    raw_aud_obj = _first_present(source, "audio_reactivity", "audioReactivity")
    raw_aud = raw_aud_obj if isinstance(raw_aud_obj, Mapping) else {}
    raw_bands = _first_present(raw_aud, "mic_equalizer_bands", "micEqualizerBands")
    raw_mouth_track = _first_present(raw_aud, "speaker_mouth_tracking", "speakerMouthTracking")
    raw_ear_glow = _first_present(raw_aud, "ear_glow_enabled", "earGlowEnabled")
    audio_reactivity = {
        "mic_equalizer_bands": int(_clean_float(raw_bands, 7, 3, 16)),
        "speaker_mouth_tracking": bool(True if raw_mouth_track is None else raw_mouth_track),
        "ear_glow_enabled": bool(True if raw_ear_glow is None else raw_ear_glow),
    }

    raw_ast = source.get("assets") if isinstance(source.get("assets"), Mapping) else {}
    custom_paths: Dict[str, str] = {}
    raw_custom_obj = _first_present(raw_ast, "custom_svg_paths", "customSvgPaths")
    raw_custom_paths = raw_custom_obj if isinstance(raw_custom_obj, Mapping) else {}
    for part_name, path_str in raw_custom_paths.items():
        if isinstance(part_name, str) and isinstance(path_str, str):
            clean_part = part_name.strip()[:32]
            clean_path = path_str.strip()[:2048]
            if clean_part and clean_path:
                custom_paths[clean_part] = clean_path

    from shared.assistant_world import validate_world
    return {
        "world": validate_world(source.get("world")),
        "id": skin_id,
        "name": name,
        "author": author,
        "version": version,
        "description": description,
        "palette": palette,
        "physics": physics,
        "emotes": emotes,
        "audio_reactivity": audio_reactivity,
        "assets": {
            "custom_svg_paths": custom_paths,
        },
    }


def _skin_file_path() -> Path:
    from shared.assistant_mode import assistant_mode_dir

    return assistant_mode_dir() / "active_skin.json"


def get_active_skin() -> Dict[str, Any]:
    """Retrieve the currently active skin, or DEFAULT_SKIN if unconfigured."""
    path = _skin_file_path()
    if not path.is_file():
        return copy.deepcopy(DEFAULT_SKIN)
    try:
        content = path.read_text(encoding="utf-8")
        parsed = json.loads(content)
        return validate_skin(parsed)
    except Exception as exc:
        LOGGER.warning("Could not read active assistant skin: %s", exc)
        return copy.deepcopy(DEFAULT_SKIN)


def save_active_skin(skin_data: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and persist the active assistant skin."""
    validated = validate_skin(skin_data)
    path = _skin_file_path()
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps(validated, indent=2, sort_keys=True))
        temporary.replace(path)
    except Exception as exc:
        LOGGER.error("Failed to save active assistant skin: %s", exc)
        raise RuntimeError(f"Could not save assistant skin: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return validated


def reset_active_skin() -> Dict[str, Any]:
    """Reset active skin to the default AutoYou Classic preset."""
    return save_active_skin({**DEFAULT_SKIN, "world": get_active_skin().get("world")})


def list_presets() -> List[Dict[str, Any]]:
    """Return the list of built-in presets with summary metadata."""
    presets = []
    for preset_id, skin in PRESET_SKINS.items():
        presets.append({
            "id": skin["id"],
            "name": skin["name"],
            "author": skin.get("author", "AutoYou"),
            "description": skin.get("description", ""),
            "preview": {
                "shell": skin["palette"]["shell_gradient"],
                "accent": skin["palette"]["accent_gradient"],
                "glow": skin["palette"]["glow_primary"],
            },
        })
    return presets


def apply_preset(preset_id: str) -> Dict[str, Any]:
    """Apply one of the preset skins by ID."""
    resolved_id = "autoyou_classic" if preset_id == "classic" else preset_id
    preset = PRESET_SKINS.get(resolved_id)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_id}")
    return save_active_skin({**preset, "world": get_active_skin().get("world")})


def export_skin_pack(skin_data: Optional[Mapping[str, Any]] = None) -> str:
    """Export the skin as a formatted JSON pack string."""
    skin = validate_skin(skin_data) if skin_data else get_active_skin()
    pack = {
        "format": "autoyou-assistant-skin-v1",
        "exported_at": str(Path(__file__).name),
        "skin": skin,
    }
    return json.dumps(pack, indent=2)


def import_skin_pack(pack_str_or_bytes: Any) -> Dict[str, Any]:
    """Import and validate a skin pack from raw JSON string or bytes."""
    if isinstance(pack_str_or_bytes, bytes):
        raw_text = pack_str_or_bytes.decode("utf-8", errors="replace")
    elif isinstance(pack_str_or_bytes, str):
        raw_text = pack_str_or_bytes
    else:
        raise ValueError("Invalid pack format: must be string or bytes.")

    try:
        data = json.loads(raw_text)
    except Exception as exc:
        raise ValueError(f"Invalid JSON in skin pack: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Skin pack must be a JSON object.")

    if "skin" in data and isinstance(data["skin"], dict):
        skin_dict = data["skin"]
    else:
        skin_dict = data

    validated = validate_skin(skin_dict)
    return save_active_skin(validated)
