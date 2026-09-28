# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-3436423233373332206f7220-fc7c2ec4e30a8f32586c4fc1

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-3436423233373332206f7220-fc7c2ec4e30a8f32586c4fc1"


import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json

VOICE_TRAINING_DIR_ENV = "AUTOYOU_VOICE_TRAINING_DIR"
VOICE_TRAINING_STORAGE_CONFIG_FILENAME = "voice_training_storage.json"


def _repo_anchor() -> Path:
    return Path(__file__).resolve().parents[1] / "server.py"


def _shared_anchor() -> Path:
    return Path(__file__).resolve().parent / "audio_manager.py"


def default_voice_training_dir() -> Path:
    from shared.platform_runtime import get_service_data_dir

    return get_service_data_dir("voice_training", anchor=_shared_anchor())


def voice_training_storage_config_path() -> Path:
    from shared.platform_runtime import get_mutable_data_dir

    root = get_mutable_data_dir("AutoYou", anchor=_repo_anchor())
    root.mkdir(parents=True, exist_ok=True)
    return root / VOICE_TRAINING_STORAGE_CONFIG_FILENAME


def _read_storage_config() -> Dict[str, Any]:
    path = voice_training_storage_config_path()
    if not path.exists():
        return {}
    try:
        loaded = load_secure_json(path, default={})
        return loaded if isinstance(loaded, dict) else {}
    except SecureStorageError:
        raise
    except Exception:
        return {}


def _write_storage_config(payload: Dict[str, Any]) -> None:
    path = voice_training_storage_config_path()
    save_secure_json(path, dict(payload or {}))


def _normalize_training_dir(path_value: str | Path, *, create: bool = True) -> Path:
    raw = str(path_value or "").strip()
    if not raw:
        raise ValueError("Voice training storage path is required.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    else:
        path = path.resolve()
    if path.exists() and not path.is_dir():
        raise ValueError(f"Voice training storage path is not a directory: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def configured_voice_training_dir() -> Optional[Path]:
    env_value = str(os.getenv(VOICE_TRAINING_DIR_ENV, "")).strip()
    if env_value:
        return _normalize_training_dir(env_value)

    config = _read_storage_config()
    custom_dir = str(config.get("custom_dir") or "").strip()
    if custom_dir:
        return _normalize_training_dir(custom_dir)
    return None


def get_voice_training_dir() -> Path:
    configured = configured_voice_training_dir()
    if configured is not None:
        configured.mkdir(parents=True, exist_ok=True)
        return configured
    return default_voice_training_dir()


def _disk_usage_for_path(path: Path) -> Optional[Dict[str, Any]]:
    candidate = path
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    try:
        usage = shutil.disk_usage(candidate)
    except Exception:
        return None
    return {
        "path": str(candidate),
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
        "total_gb": round(usage.total / (1024 ** 3), 3),
        "used_gb": round(usage.used / (1024 ** 3), 3),
        "free_gb": round(usage.free / (1024 ** 3), 3),
    }


def _directory_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += int(item.stat().st_size)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def get_voice_training_storage_info() -> Dict[str, Any]:
    active_dir = get_voice_training_dir()
    default_dir = default_voice_training_dir()
    config = _read_storage_config()
    env_value = str(os.getenv(VOICE_TRAINING_DIR_ENV, "")).strip()
    size_bytes = _directory_size_bytes(active_dir)
    return {
        "active_dir": str(active_dir),
        "default_dir": str(default_dir),
        "config_file": str(voice_training_storage_config_path()),
        "custom_dir": str(config.get("custom_dir") or ""),
        "env_override": env_value,
        "using_custom_dir": active_dir.resolve() != default_dir.resolve(),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 ** 2), 1),
        "disk": _disk_usage_for_path(active_dir),
    }


def set_voice_training_dir(path_value: str | Path) -> Dict[str, Any]:
    if str(os.getenv(VOICE_TRAINING_DIR_ENV, "")).strip():
        raise RuntimeError(f"{VOICE_TRAINING_DIR_ENV} is set; unset it before changing the persisted path.")
    target = _normalize_training_dir(path_value)
    _write_storage_config({"custom_dir": str(target), "updated_at": time.time()})
    return get_voice_training_storage_info()


def reset_voice_training_dir() -> Dict[str, Any]:
    if str(os.getenv(VOICE_TRAINING_DIR_ENV, "")).strip():
        raise RuntimeError(f"{VOICE_TRAINING_DIR_ENV} is set; unset it before resetting the persisted path.")
    _write_storage_config({"custom_dir": "", "updated_at": time.time()})
    return get_voice_training_storage_info()


def copy_voice_training_data(source_dir: str | Path, destination_dir: str | Path) -> Dict[str, Any]:
    source = Path(source_dir).expanduser().resolve()
    destination = _normalize_training_dir(destination_dir)
    if not source.exists() or not source.is_dir():
        return {"copied": False, "source_dir": str(source), "destination_dir": str(destination), "reason": "source_missing"}
    if source == destination:
        return {"copied": False, "source_dir": str(source), "destination_dir": str(destination), "reason": "same_directory"}
    try:
        destination.relative_to(source)
        raise ValueError("Destination cannot be inside the current voice training directory.")
    except ValueError as exc:
        if "Destination cannot" in str(exc):
            raise

    shutil.copytree(source, destination, dirs_exist_ok=True)
    size_bytes = _directory_size_bytes(destination)
    return {
        "copied": True,
        "source_dir": str(source),
        "destination_dir": str(destination),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 ** 2), 1),
    }
