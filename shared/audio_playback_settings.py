# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-3436423233373332206f7220-96e0189a1d8f9180b4792c7d

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-3436423233373332206f7220-96e0189a1d8f9180b4792c7d"


import os
from pathlib import Path
from typing import Any, Iterable, List, Optional

from shared.platform_runtime import get_application_root, is_compiled


AUDIO_PLAYBACK_ENABLED_ENV = "AUTOYOU_AUDIO_PLAYBACK_ENABLED"
MUSIC_LIBRARY_DIRS_ENV = "AUTOYOU_MUSIC_LIBRARY_DIRS"
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def _dedupe_existing_dirs(candidates: Iterable[Path]) -> List[str]:
    normalized_dirs: List[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve(strict=False)
        except Exception:
            continue
        if not resolved.is_dir():
            continue
        dedupe_key = os.path.normcase(os.path.normpath(str(resolved)))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized_dirs.append(str(resolved))
    return normalized_dirs


def _get_compiled_music_library_dirs() -> List[str]:
    home = Path.home()
    user_profile = Path(os.environ.get("USERPROFILE") or home)
    candidates = (
        user_profile / "Music",
        user_profile / "OneDrive" / "Music",
    )
    return _dedupe_existing_dirs(candidates)


def _should_use_music_library_env_override(prefer_env: Optional[bool]) -> bool:
    if prefer_env is not None:
        return bool(prefer_env)
    # Packaged desktop builds should not trust inherited shell overrides by
    # default; they should use persisted config or user-library discovery.
    return not is_compiled()


def audio_playback_enabled_from_env(default: bool = True) -> bool:
    raw_value = os.getenv(AUDIO_PLAYBACK_ENABLED_ENV)
    if raw_value is None:
        return bool(default)
    return str(raw_value).strip().lower() in _TRUTHY_ENV_VALUES


def set_audio_playback_enabled_env(enabled: bool) -> None:
    os.environ[AUDIO_PLAYBACK_ENABLED_ENV] = "1" if bool(enabled) else "0"


def get_default_music_library_dirs(anchor: Optional[str | Path] = None) -> List[str]:
    if is_compiled():
        music_dirs = _get_compiled_music_library_dirs()
        if music_dirs:
            return music_dirs
        resolved_anchor = anchor or (Path.cwd() / "server.py")
        base_dir = get_application_root(resolved_anchor)
    else:
        base_dir = Path.cwd()
    return [str(base_dir.resolve())]


def format_music_library_dirs(directories: Iterable[str]) -> str:
    values = [str(item).strip() for item in directories if str(item).strip()]
    return os.pathsep.join(values)


def _iter_music_dir_candidates(raw_value: Any) -> Iterable[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple, set)):
        return [str(item) for item in raw_value]

    text = str(raw_value or "").replace("\r", "\n")
    candidates: List[str] = []
    for line in text.split("\n"):
        if not line:
            continue
        if os.pathsep and os.pathsep in line:
            candidates.extend(line.split(os.pathsep))
        else:
            candidates.append(line)
    return candidates


def _normalize_music_dir_candidates(
    raw_value: Any,
    *,
    allow_missing: bool = False,
) -> List[str]:
    normalized_dirs: List[str] = []
    seen: set[str] = set()
    for candidate in _iter_music_dir_candidates(raw_value):
        value = str(candidate or "").strip().strip('"').strip("'")
        if not value:
            continue
        expanded = Path(os.path.expanduser(value))
        try:
            resolved = expanded.resolve(strict=False)
        except Exception:
            resolved = Path(os.path.abspath(os.path.expanduser(value)))
        if not allow_missing and not resolved.is_dir():
            continue
        dedupe_key = os.path.normcase(os.path.normpath(str(resolved)))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized_dirs.append(str(resolved))
    return normalized_dirs


def _include_active_voice_training_recordings(
    directories: List[str],
    *,
    allow_missing: bool,
) -> List[str]:
    """Include saved call recordings when the active training folder has audio to play."""
    try:
        from shared.voice_training_storage import get_voice_training_dir

        recordings_dir = get_voice_training_dir() / "recordings"
        if not recordings_dir.is_dir() or not any(item.is_file() for item in recordings_dir.iterdir()):
            return directories
    except Exception:
        return directories
    return _normalize_music_dir_candidates(
        [*directories, str(recordings_dir)],
        allow_missing=allow_missing,
    )


def resolve_music_library_dirs(
    configured_dirs: Any = None,
    *,
    anchor: Optional[str | Path] = None,
    fallback_to_default: bool = True,
    allow_missing: bool = False,
    prefer_env: Optional[bool] = None,
    include_voice_training_recordings: bool = True,
) -> List[str]:
    raw_value = configured_dirs
    if raw_value in (None, "", [], (), set()):
        raw_value = (
            os.getenv(MUSIC_LIBRARY_DIRS_ENV, "")
            if _should_use_music_library_env_override(prefer_env)
            else ""
        )

    resolved = _normalize_music_dir_candidates(raw_value, allow_missing=allow_missing)
    if resolved or not fallback_to_default:
        base_dirs = resolved
    else:
        base_dirs = _normalize_music_dir_candidates(
            get_default_music_library_dirs(anchor),
            allow_missing=allow_missing,
        )
    if not include_voice_training_recordings:
        return base_dirs
    return _include_active_voice_training_recordings(
        base_dirs,
        allow_missing=allow_missing,
    )


def ensure_music_library_dirs(
    anchor: Optional[str | Path] = None,
    *,
    configured_dirs: Any = None,
    allow_missing: bool = False,
    prefer_env: Optional[bool] = None,
    include_voice_training_recordings: bool = True,
) -> List[str]:
    resolved = resolve_music_library_dirs(
        configured_dirs,
        anchor=anchor,
        fallback_to_default=True,
        allow_missing=allow_missing,
        prefer_env=prefer_env,
        include_voice_training_recordings=include_voice_training_recordings,
    )
    if resolved:
        os.environ[MUSIC_LIBRARY_DIRS_ENV] = format_music_library_dirs(resolved)
    return resolved
