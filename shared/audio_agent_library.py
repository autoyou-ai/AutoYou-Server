# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Persistent, server-owned audio library source selection.

The Audio Agent website is a separately hosted managed frontend, so it cannot
rely on the main server module's in-memory config.  This small boundary keeps
its source preferences in the normal mutable runtime directory and resolves
the actual directories on the server for every scan.  That makes source runs,
compiled builds, WSL, Docker, and both macOS layouts use the same path rules.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Optional

from shared.audio_playback_settings import resolve_music_library_dirs
from shared.platform_runtime import get_config_dir, get_mutable_data_dir
from shared.secure_storage import load_secure_json, save_secure_json
from shared.voice_training_storage import get_voice_training_dir


AUDIO_AGENT_SETTINGS_FILENAME = "library_settings.json"
AUDIO_AGENT_AD_HOC_PATHS_ENV = "AUTOYOU_AUDIO_AGENT_AD_HOC_PATHS"
AUDIO_SOURCE_KEYS = (
    "voice_transcriptions",
    "page_feed_audio",
    "notes_media_audio",
)
AUDIO_SOURCE_LABELS = {
    "voice_transcriptions": "Voice transcriptions",
    "page_feed_audio": "Page Agent feed audio",
    "notes_media_audio": "Notes Agent media audio",
}
DEFAULT_AUDIO_SOURCE_SETTINGS = {
    "voice_transcriptions": True,
    "page_feed_audio": True,
    "notes_media_audio": True,
}


def _runtime_anchor(anchor: Optional[str | Path] = None) -> Path:
    """Find the workspace/bundle anchor used by the shared path helpers."""
    candidate = Path(anchor or __file__).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        candidate = candidate.absolute()
    start = candidate if candidate.is_dir() else candidate.parent
    for parent in (start, *start.parents):
        if (parent / "server.py").is_file() and (parent / "autoyou_agents").is_dir():
            return parent
    return start


def _settings_path(*, anchor: Optional[str | Path] = None, create_parent: bool = False) -> Path:
    root = get_mutable_data_dir("AutoYou", anchor=_runtime_anchor(anchor))
    settings_dir = root / "audio_agent"
    if create_parent:
        settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir / AUDIO_AGENT_SETTINGS_FILENAME


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(default)


def _iter_path_values(raw_value: Any) -> Iterable[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple, set)):
        return [str(item) for item in raw_value]
    text = str(raw_value or "").replace("\r", "\n")
    values: list[str] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        if os.pathsep in line:
            values.extend(line.split(os.pathsep))
        else:
            values.append(line)
    return values


def _normalize_path_values(raw_value: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in _iter_path_values(raw_value):
        value = str(raw_path or "").strip().strip('"').strip("'")
        if not value:
            continue
        try:
            resolved = Path(os.path.expanduser(value)).resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        key = os.path.normcase(os.path.normpath(str(resolved)))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(str(resolved))
    return normalized


def normalize_audio_agent_library_settings(
    payload: Any = None,
    *,
    base: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    current = dict(base or {})
    source_input = payload.get("audio_sources") if isinstance(payload, dict) else None
    if not isinstance(source_input, dict):
        source_input = payload if isinstance(payload, dict) else {}
    current_sources = current.get("audio_sources") if isinstance(current.get("audio_sources"), dict) else {}
    sources = {
        key: _coerce_bool(
            source_input.get(key, current_sources.get(key, DEFAULT_AUDIO_SOURCE_SETTINGS[key])),
            DEFAULT_AUDIO_SOURCE_SETTINGS[key],
        )
        for key in AUDIO_SOURCE_KEYS
    }

    if isinstance(payload, dict) and "ad_hoc_paths" in payload:
        ad_hoc_paths = _normalize_path_values(payload.get("ad_hoc_paths"))
    else:
        ad_hoc_paths = _normalize_path_values(current.get("ad_hoc_paths", []))

    return {
        "audio_sources": sources,
        "ad_hoc_paths": ad_hoc_paths,
    }


def default_audio_agent_library_settings() -> dict[str, Any]:
    return normalize_audio_agent_library_settings({})


def load_audio_agent_library_settings(*, anchor: Optional[str | Path] = None) -> dict[str, Any]:
    path = _settings_path(anchor=anchor)
    if not path.is_file():
        return default_audio_agent_library_settings()
    try:
        loaded = load_secure_json(path, default={})
    except Exception:
        return default_audio_agent_library_settings()
    return normalize_audio_agent_library_settings(loaded)


def save_audio_agent_library_settings(
    payload: Any,
    *,
    anchor: Optional[str | Path] = None,
) -> dict[str, Any]:
    settings = normalize_audio_agent_library_settings(payload)
    path = _settings_path(anchor=anchor, create_parent=True)
    save_secure_json(path, settings)
    return settings


def _page_feed_upload_dir(anchor: Optional[str | Path] = None) -> Path:
    return get_config_dir("AutoYou", anchor=_runtime_anchor(anchor)) / "uploads"


def _notes_media_dir(anchor: Optional[str | Path] = None) -> Path:
    return get_mutable_data_dir("AutoYou", anchor=_runtime_anchor(anchor)) / "autoyou_notes_agent" / "media"


def _configured_server_paths(
    settings: dict[str, Any],
    *,
    anchor: Optional[str | Path] = None,
) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    saved_paths = settings.get("ad_hoc_paths") if isinstance(settings, dict) else []
    for value in _normalize_path_values(saved_paths):
        values.append(("ad_hoc_paths", value))

    # Keep custom values from the existing music-library environment contract
    # as explicit server-owned ad-hoc sources.  The main server seeds that
    # variable with its legacy default (including the voice folder); filter
    # those defaults here so a disabled source cannot return through an
    # inherited environment variable.
    try:
        default_music_paths = {
            os.path.normcase(os.path.normpath(value))
            for value in resolve_music_library_dirs(
                None,
                anchor=_runtime_anchor(anchor),
                fallback_to_default=True,
                prefer_env=False,
                include_voice_training_recordings=True,
            )
        }
    except Exception:
        default_music_paths = set()
    for value in _normalize_path_values(os.getenv("AUTOYOU_MUSIC_LIBRARY_DIRS", "")):
        key = os.path.normcase(os.path.normpath(value))
        if key not in default_music_paths:
            values.append(("music_library_env", value))
    for value in _normalize_path_values(os.getenv(AUDIO_AGENT_AD_HOC_PATHS_ENV, "")):
        values.append(("ad_hoc_paths_env", value))
    return values


def resolve_audio_library_path_details(
    settings: Any = None,
    *,
    anchor: Optional[str | Path] = None,
) -> list[dict[str, Any]]:
    normalized = normalize_audio_agent_library_settings(settings)
    source_flags = normalized["audio_sources"]
    candidates: list[tuple[str, str, Path]] = []

    source_path_getters = {
        "voice_transcriptions": lambda: get_voice_training_dir() / "recordings",
        "page_feed_audio": lambda: _page_feed_upload_dir(anchor),
        "notes_media_audio": lambda: _notes_media_dir(anchor),
    }
    for key in AUDIO_SOURCE_KEYS:
        if not source_flags.get(key):
            continue
        try:
            source_path = source_path_getters[key]()
        except Exception:
            continue
        candidates.append((key, AUDIO_SOURCE_LABELS[key], source_path))

    for key, value in _configured_server_paths(normalized, anchor=anchor):
        candidates.append((key, "Server ad-hoc path", Path(value)))

    details: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_key, label, path in candidates:
        try:
            resolved = path.expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        dedupe_key = os.path.normcase(os.path.normpath(str(resolved)))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        details.append(
            {
                "source": source_key,
                "label": label,
                "path": str(resolved),
                "exists": resolved.is_dir(),
            }
        )
    return details


def resolve_audio_library_roots(
    settings: Any = None,
    *,
    anchor: Optional[str | Path] = None,
) -> list[str]:
    return [item["path"] for item in resolve_audio_library_path_details(settings, anchor=anchor) if item["exists"]]
