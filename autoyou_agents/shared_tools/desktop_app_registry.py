# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-646472657373202d20334163-8099c651c504fc8287866a81

"""Disk-backed registry for desktop bridge agent manifests."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-646472657373202d20334163-8099c651c504fc8287866a81"


import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .desktop_app_manifest import discover_desktop_app_manifests
from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json


DESKTOP_APP_REGISTRY_FILENAME = "agent_desktop_apps_registry.json"


def get_desktop_app_registry_path(registry_path: Optional[Path] = None) -> Path:
    if registry_path is not None:
        return Path(registry_path)
    env_path = os.environ.get("AUTOYOU_DESKTOP_APP_REGISTRY_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    from shared.platform_runtime import get_config_dir

    return get_config_dir("AutoYou", anchor=__file__) / DESKTOP_APP_REGISTRY_FILENAME


def build_desktop_app_registry(
    *,
    agents_root: Path,
    agent_names: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    manifests = discover_desktop_app_manifests(agents_root=agents_root, agent_names=agent_names)
    entries = []
    for manifest in manifests:
        packs = manifest.get("asset_packs") or []
        platforms = sorted({str(pack.get("platform") or "any").strip() or "any" for pack in packs})
        valid_until = sorted(
            {
                str(pack.get("valid_until") or "").strip()
                for pack in packs
                if str(pack.get("valid_until") or "").strip()
            }
        )
        entries.append(
            {
                "agent_name": manifest.get("agent_name"),
                "title": manifest.get("title"),
                "description": manifest.get("description"),
                "app_id": manifest.get("app_id"),
                "transport": manifest.get("transport"),
                "platforms": platforms,
                "valid_until_dates": valid_until,
                "asset_pack_count": len(packs),
                "manifest_path": manifest.get("manifest_path"),
            }
        )
    return {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "desktop_agents": entries,
    }


def write_desktop_app_registry(
    registry: Dict[str, Any],
    *,
    registry_path: Optional[Path] = None,
) -> Path:
    registry_path = get_desktop_app_registry_path(registry_path)
    save_secure_json(registry_path, registry)
    return registry_path


def refresh_desktop_app_registry(
    *,
    agents_root: Path,
    agent_names: Optional[Iterable[str]] = None,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    registry = build_desktop_app_registry(agents_root=agents_root, agent_names=agent_names)
    write_desktop_app_registry(registry, registry_path=registry_path)
    return registry


def load_desktop_app_registry(
    *,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    registry_path = get_desktop_app_registry_path(registry_path)
    if not registry_path.is_file():
        return {
            "schema_version": 1,
            "updated_at": None,
            "desktop_agents": [],
        }
    try:
        payload = load_secure_json(registry_path, default={})
    except SecureStorageError:
        raise
    except (OSError, ValueError, TypeError):
        payload = {}
    entries = payload.get("desktop_agents")
    if not isinstance(entries, list):
        entries = []
    return {
        "schema_version": int(payload.get("schema_version", 1)),
        "updated_at": payload.get("updated_at"),
        "desktop_agents": entries,
    }
