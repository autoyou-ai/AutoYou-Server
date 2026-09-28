# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Shared capture settings for full and Lite native remote desktop."""

from __future__ import annotations

from typing import Any, Dict


_QUALITY_PRESETS: Dict[str, Dict[str, int]] = {
    "low": {"max_width": 960, "fps": 8},
    "balanced": {"max_width": 1280, "fps": 12},
    "high": {"max_width": 1920, "fps": 20},
    "ultra": {"max_width": 2560, "fps": 24},
}


def normalize_remote_desktop_quality(raw_value: Any, default: str = "balanced") -> str:
    normalized = str(raw_value or default or "balanced").strip().lower().replace("-", "_")
    normalized = {
        "data_saver": "low",
        "medium": "balanced",
        "default": "balanced",
        "1080p": "high",
        "best": "ultra",
        "native": "ultra",
    }.get(normalized, normalized)
    return normalized if normalized in _QUALITY_PRESETS else default


def remote_desktop_quality_settings(raw_value: Any) -> Dict[str, int]:
    return dict(_QUALITY_PRESETS[normalize_remote_desktop_quality(raw_value)])


def normalize_remote_desktop_bitrate_kbps(raw_value: Any, default: int = 1500) -> int:
    try:
        value = int(float(str(raw_value).strip()))
    except Exception:
        value = int(default)
    # aiortc's bundled real-time H.264 encoder currently supports 3 Mbps.
    return max(250, min(3000, value))


def normalize_remote_desktop_monitor_id(raw_value: Any, default: int = 0) -> int:
    try:
        value = int(raw_value)
    except Exception:
        value = int(default)
    return max(0, min(64, value))
