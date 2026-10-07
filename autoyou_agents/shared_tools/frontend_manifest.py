# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-6881e0bbc8d9d66f3d117bb5

"""Shared helpers for generated sub-agent website manifests."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import importlib.resources as importlib_resources
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .website_scaffold import (
    DEFAULT_BACKEND_STACK,
    DEFAULT_FRONTEND_STACK,
    normalize_backend_stack,
    normalize_frontend_stack,
)

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-6881e0bbc8d9d66f3d117bb5"


FRONTEND_MANIFEST_RELATIVE_PATH = Path("website") / "manifest.json"
DEFAULT_ENTRY_PATH = "/"
LOGGER = logging.getLogger(__name__)

# Ports reserved for core AutoYou services. Only the agent whose name matches
# the port's canonical owner (admin_agent -> 8001) may advertise that port as
# direct_forward_port. Any other agent that claims a reserved port is silently
# cleared so it cannot shadow the admin UI or signal/auth infrastructure.
_RESERVED_PORTS: frozenset = frozenset(
    {
        8001,  # main admin server (admin_agent only)
        8002,  # auth server
        8067,  # AutoYou page service
        8081,  # AI-agent server
        8082,  # signal server
        8083,  # WhatsApp WS listener
    }
)
# from __debug_provenance_s__ import btc
# Maps a reserved port to the only agent_name that is allowed to claim it.
_PORT_OWNER: dict = {8001: "admin_agent"}


def normalize_entry_path(entry_path: Optional[str]) -> str:
    raw = str(entry_path or "").strip()
    if not raw or raw == ".":
        return DEFAULT_ENTRY_PATH
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw


# Optional Agent Apps presentation hints. ``icon`` names a glyph from the
# built-in set, ``accent`` is a ``#rrggbb`` colour for the icon gradient,
# ``category`` is one of the store sections and ``keywords`` extend search.
# They are passed through as trimmed text; shared.agent_apps validates them
# when it draws the store, so a bad value falls back to an inferred one.
_PRESENTATION_TEXT_FIELDS = ("icon", "accent", "category")


def _normalize_presentation(payload: Dict[str, Any]) -> Dict[str, Any]:
    hints: Dict[str, Any] = {}
    for field in _PRESENTATION_TEXT_FIELDS:
        value = str(payload.get(field) or "").strip()[:32]
        if value:
            hints[field] = value
    keywords = payload.get("keywords")
    if isinstance(keywords, str):
        keywords = [part for part in keywords.replace(";", ",").split(",")]
    if isinstance(keywords, (list, tuple)):
        cleaned = [str(word).strip()[:40] for word in keywords if str(word).strip()]
        if cleaned:
            hints["keywords"] = cleaned[:12]
    return hints


# Valid declared auth defaults for an agent website. "inherit" (the default
# when the field is absent) reproduces the platform's existing gated-by-default
# fallback, so agents that don't touch this field are completely unaffected.
_VALID_AUTH_DEFAULTS: frozenset = frozenset({"open", "gated", "inherit"})


def _normalize_auth_default(value: Optional[str]) -> str:
    normalized = str(value or "inherit").strip().lower()
    return normalized if normalized in _VALID_AUTH_DEFAULTS else "inherit"


def build_frontend_manifest(
    *,
    agent_name: str,
    title: str,
    description: str,
    entry_path: str = DEFAULT_ENTRY_PATH,
    recommended_port: Optional[int] = None,
    requires_proxy_registration: bool = True,
    websocket_enabled: bool = False,
    default: bool = False,
    frontend_stack: str = DEFAULT_FRONTEND_STACK,
    auth_default: str = "inherit",
    shared_session_eligible: bool = True,
    bypass_global_otp: bool = False,
    backend_stack: str = DEFAULT_BACKEND_STACK,
    icon: Optional[str] = None,
    accent: Optional[str] = None,
    category: Optional[str] = None,
    keywords: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {
        "agent_name": str(agent_name).strip(),
        "title": str(title).strip() or str(agent_name).strip(),
        "description": str(description).strip(),
        "entry_path": normalize_entry_path(entry_path),
        "recommended_port": int(recommended_port) if recommended_port else None,
        "requires_proxy_registration": bool(requires_proxy_registration),
        "websocket_enabled": bool(websocket_enabled),
        "default": bool(default),
        "frontend_stack": normalize_frontend_stack(frontend_stack),
        "backend_stack": normalize_backend_stack(backend_stack),
        # Declared default OTP posture for this agent website ("open" / "gated"
        # / "inherit"). Read by scheduler_mission_control._get_agent_security_settings()
        # as the fallback when no explicit per-agent admin config override exists.
        "auth_default": _normalize_auth_default(auth_default),
        # Whether this agent should accept the opt-in shared cross-agent OTP
        # session cookie when an admin has enabled that global toggle.
        "shared_session_eligible": bool(shared_session_eligible),
        # True means this agent stays open even when the admin turns on the
        # global "require OTP on all agent websites" toggle. Use this for
        # deliberately public-by-default sites such as Page, Notes, and Ads
        # Watching. An explicit per-agent admin override still wins.
        "bypass_global_otp": bool(bypass_global_otp),
    }
    manifest.update(
        _normalize_presentation(
            {"icon": icon, "accent": accent, "category": category, "keywords": list(keywords or [])}
        )
    )
    return manifest


def write_frontend_manifest(agent_dir: Path, manifest: Dict[str, Any]) -> Path:
    manifest_path = agent_dir / FRONTEND_MANIFEST_RELATIVE_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _normalize_frontend_manifest_payload(
    payload: Dict[str, Any],
    *,
    agent_name: str,
    manifest_path: str,
    website_root: str,
) -> Dict[str, Any]:
    normalized_agent_name = str(payload.get("agent_name") or agent_name).strip() or agent_name
    title = str(payload.get("title") or normalized_agent_name).strip() or normalized_agent_name
    description = str(payload.get("description") or "").strip()
    entry_path = normalize_entry_path(payload.get("entry_path"))
    recommended_port = payload.get("recommended_port")
    try:
        recommended_port = int(recommended_port) if recommended_port not in (None, "") else None
    except (TypeError, ValueError):
        recommended_port = None
    direct_forward_port = payload.get("direct_forward_port")
    try:
        direct_forward_port = (
            int(direct_forward_port)
            if direct_forward_port not in (None, "")
            else None
        )
    except (TypeError, ValueError):
        direct_forward_port = None

    # Enforce reserved-port protection: only the canonical owner may claim a core port.
    if direct_forward_port is not None and direct_forward_port in _RESERVED_PORTS:
        allowed_owner = _PORT_OWNER.get(direct_forward_port)
        if allowed_owner is None or normalized_agent_name != allowed_owner:
            LOGGER.warning(
                "Agent '%s' tried to claim reserved port %d as direct_forward_port - ignoring.",
                normalized_agent_name,
                direct_forward_port,
            )
            direct_forward_port = None

    return {
        **_normalize_presentation(payload),
        "agent_name": normalized_agent_name,
        "title": title,
        "description": description,
        "entry_path": entry_path,
        "recommended_port": recommended_port,
        "direct_forward_port": direct_forward_port,
        "requires_proxy_registration": bool(payload.get("requires_proxy_registration", True)),
        "websocket_enabled": bool(payload.get("websocket_enabled", False)),
        # Marks the default/home agent website (the AutoYou Page). Clients open
        # this website as the browser home.
        "default": bool(payload.get("default", False)),
        "frontend_stack": normalize_frontend_stack(payload.get("frontend_stack")),
        "backend_stack": normalize_backend_stack(payload.get("backend_stack")),
        "auth_default": _normalize_auth_default(payload.get("auth_default")),
        "shared_session_eligible": bool(payload.get("shared_session_eligible", True)),
        "bypass_global_otp": bool(payload.get("bypass_global_otp", False)),
        "manifest_path": str(manifest_path),
        "website_root": str(website_root),
    }


def _load_frontend_manifest_from_package(agent_name: str) -> Optional[Dict[str, Any]]:
    try:
        from shared.platform_runtime import iter_agent_roots

        for agents_root in iter_agent_roots(__file__):
            manifest_path = Path(agents_root) / agent_name / FRONTEND_MANIFEST_RELATIVE_PATH
            if not manifest_path.is_file():
                continue
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                LOGGER.warning("Ignoring invalid embedded frontend manifest at %s: %s", manifest_path, exc)
                return None
            return _normalize_frontend_manifest_payload(
                payload,
                agent_name=agent_name,
                manifest_path=str(manifest_path),
                website_root=str(manifest_path.parent),
            )
    except Exception:
        pass

    try:
        website_root = importlib_resources.files(f"autoyou_agents.{agent_name}.website")
        manifest_resource = website_root.joinpath("manifest.json")
        if not manifest_resource.is_file():
            return None
        with manifest_resource.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return _normalize_frontend_manifest_payload(
            payload,
            agent_name=agent_name,
            manifest_path=str(manifest_resource),
            website_root=str(website_root),
        )
    except ModuleNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        LOGGER.warning(
            "Ignoring invalid packaged frontend manifest for %s: %s",
            agent_name,
            exc,
        )
        return None


def load_frontend_manifest(agent_dir: Path) -> Optional[Dict[str, Any]]:
    manifest_path = agent_dir / FRONTEND_MANIFEST_RELATIVE_PATH
    if manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            LOGGER.warning("Ignoring invalid frontend manifest at %s: %s", manifest_path, exc)
            return None
        return _normalize_frontend_manifest_payload(
            payload,
            agent_name=agent_dir.name,
            manifest_path=str(manifest_path),
            website_root=str(manifest_path.parent),
        )

    return _load_frontend_manifest_from_package(agent_dir.name)


def _iter_frontend_manifest_roots(agents_root: Path) -> List[Path]:
    roots: List[Path] = []
    try:
        from shared.platform_runtime import is_compiled, iter_agent_roots

        if is_compiled():
            for candidate in iter_agent_roots(__file__):
                if candidate not in roots:
                    roots.append(candidate)
    except Exception:
        pass

    resolved_agents_root = Path(agents_root).resolve()
    if resolved_agents_root not in roots:
        roots.append(resolved_agents_root)
    return roots


def _find_agent_dir(agent_name: str, roots: Iterable[Path]) -> Optional[Path]:
    for root in roots:
        candidate = root / agent_name
        if candidate.is_dir():
            return candidate
    return None


def build_frontend_discovery_entry(
    *,
    manifest: Dict[str, Any],
    proxy_port: Optional[int] = None,
    browser_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    agent_name = manifest["agent_name"]
    entry_path = normalize_entry_path(manifest.get("entry_path"))
    direct_forward_port = manifest.get("direct_forward_port")
    try:
        direct_forward_port = int(direct_forward_port) if direct_forward_port not in (None, "") else None
    except (TypeError, ValueError):
        direct_forward_port = None
    proxy_path = f"/agent/{agent_name}{entry_path if entry_path != '/' else '/'}"
    normalized_base = (browser_base_url or "").rstrip("/")
    requires_proxy_registration = bool(manifest.get("requires_proxy_registration", True))
    launch_url = f"{normalized_base}{proxy_path}" if normalized_base else None
    local_url = None
    if direct_forward_port:
        local_url = f"http://127.0.0.1:{int(direct_forward_port)}{entry_path if entry_path != '/' else '/'}"
    elif proxy_port:
        local_url = f"http://127.0.0.1:{int(proxy_port)}{entry_path if entry_path != '/' else '/'}"
    elif not requires_proxy_registration:
        local_url = launch_url
    open_url = local_url if direct_forward_port else (launch_url or local_url or proxy_path)
    return {
        **manifest,
        "has_frontend": True,
        "proxy_port": int(proxy_port) if proxy_port else None,
        "frontend_port_registered": (
            (proxy_port is not None)
            or (direct_forward_port is not None)
            or not requires_proxy_registration
        ),
        "proxy_path": proxy_path,
        "launch_path": proxy_path,
        "launch_url": launch_url,
        "local_url": local_url,
        "open_url": open_url,
        "uses_direct_forward_port": bool(direct_forward_port),
    }


def discover_frontend_manifests(
    *,
    agents_root: Path,
    agent_names: Optional[Iterable[str]] = None,
    proxy_ports: Optional[Dict[str, int]] = None,
    browser_base_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    proxy_ports = proxy_ports or {}
    roots = _iter_frontend_manifest_roots(agents_root)
    if agent_names is not None:
        names = list(agent_names)
    else:
        discovered_names: set[str] = set()
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.iterdir():
                if not path.is_dir():
                    continue
                if (path / "agent.py").exists() or (path / FRONTEND_MANIFEST_RELATIVE_PATH).is_file():
                    discovered_names.add(path.name)
        names = sorted(discovered_names)

    results: List[Dict[str, Any]] = []
    for agent_name in sorted(set(names)):
        agent_dir = _find_agent_dir(agent_name, roots)
        manifest = (
            load_frontend_manifest(agent_dir)
            if agent_dir is not None
            else _load_frontend_manifest_from_package(agent_name)
        )
        if not manifest:
            continue
        results.append(
            build_frontend_discovery_entry(
                manifest=manifest,
                proxy_port=proxy_ports.get(agent_name),
                browser_base_url=browser_base_url,
            )
        )
    return results


def resolve_agent_auth_manifest(agent_name: str) -> Dict[str, Any]:
    """Live (uncached) auth-relevant manifest lookup for a single agent.

    Used by scheduler_mission_control._get_agent_security_settings(), which
    must not depend on the agent_frontends_registry.json cache being fresh
    (that cache only refreshes on boot/install/publish). Falls back to the
    safe defaults ("inherit", True) when no manifest is found or any lookup
    step raises, so an unknown or misconfigured agent never silently opens.
    """
    try:
        manifest = _load_frontend_manifest_from_package(agent_name)
    except Exception:
        manifest = None
    if not manifest:
        return {"auth_default": "inherit", "shared_session_eligible": True, "bypass_global_otp": False}
    return {
        "auth_default": _normalize_auth_default(manifest.get("auth_default")),
        "shared_session_eligible": bool(manifest.get("shared_session_eligible", True)),
        "bypass_global_otp": bool(manifest.get("bypass_global_otp", False)),
    }
