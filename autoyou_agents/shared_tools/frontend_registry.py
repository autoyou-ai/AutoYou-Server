# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Shared disk-backed registry for frontend-enabled AutoYou sub-agents."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .frontend_manifest import discover_frontend_manifests
from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRONTEND_REGISTRY_PATH = _REPO_ROOT / "agent_frontends_registry.json"


def get_frontend_registry_path(
    registry_path: Optional[Path] = None,
) -> Path:
    if registry_path is not None:
        return Path(registry_path)
    env_path = os.environ.get("AUTOYOU_FRONTEND_REGISTRY_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    if os.environ.get("AUTOYOU_TEST_ROOT", "").strip():
        from shared.platform_runtime import get_config_dir

        return get_config_dir("AutoYou", anchor=__file__) / "agent_frontends_registry.json"
    # Dev/runtime parity: write registry into the workspace repo when running
    # from source so agent website visibility matches local file inspection.
    # Compiled builds continue to use user-config storage.
    try:
        from shared.platform_runtime import is_compiled

        if not bool(is_compiled()):
            return (_REPO_ROOT / "agent_frontends_registry.json").resolve()
    except Exception:
        pass
    from shared.platform_runtime import get_config_dir
    return get_config_dir("AutoYou", anchor=__file__) / "agent_frontends_registry.json"


def _normalize_proxy_ports(proxy_ports: Optional[Dict[str, Any]]) -> Dict[str, int]:
    normalized: Dict[str, int] = {}
    for key, value in (proxy_ports or {}).items():
        try:
            if value in (None, ""):
                continue
            normalized[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return normalized


# Sentinel default value meaning "land on the websites directory page
# (/websites)" rather than a specific agent website.
AGENT_WEBSITES_DIRECTORY = "agent_websites"


def _resolve_default_agent(frontends: list, default_agent: Optional[str]) -> Optional[str]:
    """Resolve the default/home agent website for the registry.

    Precedence: an explicit (config-driven) ``default_agent`` that is either the
    directory sentinel or a present website; else a manifest-flagged default;
    else ``page_agent`` if present; else None.
    """
    names = {str(fe.get("agent_name")) for fe in frontends if isinstance(fe, dict)}
    if default_agent:
        candidate = str(default_agent).strip()
        if candidate in {AGENT_WEBSITES_DIRECTORY, "directory", "websites"}:
            return AGENT_WEBSITES_DIRECTORY
        if candidate in names:
            return candidate
    for fe in frontends:
        if isinstance(fe, dict) and fe.get("default"):
            return str(fe.get("agent_name"))
    if "page_agent" in names:
        return "page_agent"
    return None


def build_frontend_registry(
    *,
    agents_root: Path,
    agent_names: Optional[Iterable[str]] = None,
    proxy_ports: Optional[Dict[str, Any]] = None,
    browser_base_url: Optional[str] = None,
    default_agent: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_proxy_ports = _normalize_proxy_ports(proxy_ports)
    frontends = discover_frontend_manifests(
        agents_root=agents_root,
        agent_names=agent_names,
        proxy_ports=normalized_proxy_ports,
        browser_base_url=browser_base_url,
    )
    resolved_default = _resolve_default_agent(frontends, default_agent)
    # Stamp the resolved default onto each entry so every reader (page-service
    # `/` redirect, server status routes, clients) agrees on the home website.
    for fe in frontends:
        if isinstance(fe, dict):
            fe["default"] = bool(resolved_default and fe.get("agent_name") == resolved_default)
    return {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "browser_base_url": browser_base_url,
        "default_agent": resolved_default,
        "frontends": frontends,
    }


def write_frontend_registry(
    registry: Dict[str, Any],
    *,
    registry_path: Optional[Path] = None,
) -> Path:
    registry_path = get_frontend_registry_path(registry_path)
    save_secure_json(registry_path, registry)
    return registry_path


def refresh_frontend_registry(
    *,
    agents_root: Path,
    agent_names: Optional[Iterable[str]] = None,
    proxy_ports: Optional[Dict[str, Any]] = None,
    browser_base_url: Optional[str] = None,
    default_agent: Optional[str] = None,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    registry_path = get_frontend_registry_path(registry_path)
    registry = build_frontend_registry(
        agents_root=agents_root,
        agent_names=agent_names,
        proxy_ports=proxy_ports,
        browser_base_url=browser_base_url,
        default_agent=default_agent,
    )
    write_frontend_registry(registry, registry_path=registry_path)
    return registry


def load_frontend_registry(
    *,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    registry_path = get_frontend_registry_path(registry_path)
    if not registry_path.is_file():
        return {
            "schema_version": 1,
            "updated_at": None,
            "browser_base_url": None,
            "default_agent": None,
            "frontends": [],
        }

    try:
        payload = load_secure_json(registry_path, default={})
    except SecureStorageError:
        raise
    except (OSError, ValueError, TypeError):
        payload = {}
    frontends = payload.get("frontends")
    if not isinstance(frontends, list):
        frontends = []
    return {
        "schema_version": int(payload.get("schema_version", 1)),
        "updated_at": payload.get("updated_at"),
        "browser_base_url": payload.get("browser_base_url"),
        "default_agent": payload.get("default_agent"),
        "frontends": frontends,
    }
