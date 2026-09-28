# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Central AutoYou application version + release channel.

Single source of truth for the running app's version, used by the software
update feature (`shared/update_service.py`) to compare against the signed
update manifest published by the authorized update server.

Resolution order for the version string:
  1. ``AUTOYOU_VERSION`` env override (CI / packaging can stamp this).
  2. ``AUTOYOU_VERSION_FILE`` env path, if set.
  3. The repo-root / packaged-resources ``VERSION`` file.
  4. ``_DEFAULT_VERSION`` fallback.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_DEFAULT_VERSION = "81.0.0"
_VERSION_FILENAME = "VERSION"

# Release channels the update feed understands.
VALID_CHANNELS = ("stable", "beta", "dev")

def _candidate_version_files() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("AUTOYOU_VERSION_FILE", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    # shared/ is one level below the repo / bundle root.
    candidates.append(Path(__file__).resolve().parent.parent / _VERSION_FILENAME)
    try:
        from shared.platform_runtime import get_application_root

        candidates.append(Path(get_application_root(__file__)) / _VERSION_FILENAME)
    except Exception:
        pass
    return candidates

def _read_version_file() -> Optional[str]:
    for candidate in _candidate_version_files():
        try:
            if candidate and candidate.is_file():
                text = candidate.read_text(encoding="utf-8").strip()
                if text:
                    return text.splitlines()[0].strip()
        except Exception:
            continue
    return None

def get_version() -> str:
    """Return the running application version string (e.g. ``81.0.0``)."""
    env_version = os.environ.get("AUTOYOU_VERSION", "").strip()
    if env_version:
        return env_version
    return _read_version_file() or _DEFAULT_VERSION

def get_release_channel() -> str:
    """Return the configured release channel (``stable`` by default)."""
    channel = (os.environ.get("AUTOYOU_RELEASE_CHANNEL", "").strip() or "stable").lower()
    return channel if channel in VALID_CHANNELS else "stable"

__version__ = get_version()
