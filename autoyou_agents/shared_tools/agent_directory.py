# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-4700ccda53e2378c50dbf4af

"""Shared helpers for shaping user-facing AutoYou agent directory entries."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import importlib
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .agent_identity import ROOT_AGENT_NAME, format_agent_display_name, resolve_runtime_agent_name
from .agent_install_registry import discover_agent_directories, is_builtin_agent_name, load_agent_install_registry
from .frontend_manifest import discover_frontend_manifests
from .frontend_registry import load_frontend_registry

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-4700ccda53e2378c50dbf4af"


def _default_agents_root() -> Path:
    return Path(__file__).resolve().parents[1]


def package_agent_name(raw_name: Optional[str]) -> str:
    """Map a runtime or raw agent id to the on-disk package name."""
    normalized = resolve_runtime_agent_name(raw_name)
    if not normalized:
        return ""
    if normalized == ROOT_AGENT_NAME:
        return normalized
    if normalized.startswith("autoyou_") and normalized.endswith("_agent"):
        return normalized[len("autoyou_") :]
    return normalized


def command_name_for_agent(raw_name: Optional[str]) -> str:
    """Return the human command token used in direct steering messages."""
    normalized = resolve_runtime_agent_name(raw_name)
    if not normalized:
        return ""
    if normalized == ROOT_AGENT_NAME:
        return "root"

    value = normalized
    if value.startswith("autoyou_"):
        value = value[len("autoyou_") :]
    if value.endswith("_agent"):
        value = value[: -len("_agent")]
    return value.replace("_", " ").replace("-", " ").strip().lower()


def concise_agent_description(description: Optional[str], *, fallback_display_name: str) -> str:
    """Reduce long prompt copy to a short picker-friendly sentence."""
    text = str(description or "").strip()
    if not text:
        return f"{fallback_display_name} agent."

    first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    candidate = first_sentence or text
    if len(candidate) <= 120:
        return candidate

    shortened = candidate[:117].rsplit(" ", 1)[0].strip()
    return f"{shortened or candidate[:117].strip()}..."


def load_agent_prompt_description(
    agent_name: str,
    *,
    frontend_entry: Optional[Dict[str, Any]] = None,
    agents_root: Optional[Path] = None,
) -> str:
    """Load prompt-first description text for an agent with frontend fallback."""
    package_name = package_agent_name(agent_name)
    compiled_runtime = False
    try:
        from shared.platform_runtime import is_compiled

        compiled_runtime = bool(is_compiled())
    except Exception:
        compiled_runtime = False

    if (
        package_name
        and package_name != ROOT_AGENT_NAME
        and (not compiled_runtime or is_builtin_agent_name(package_name))
    ):
        try:
            prompt_module = importlib.import_module(f"autoyou_agents.{package_name}.prompt")
            description = str(getattr(prompt_module, "AGENT_DESCRIPTION", "") or "").strip()
            if description:
                return description
        except Exception:
            pass

    if isinstance(frontend_entry, dict):
        description = str(frontend_entry.get("description") or "").strip()
        if description:
            return description

    if package_name and agents_root is not None:
        try:
            registry = load_agent_install_registry(agents_root=Path(agents_root))
            description = str(
                (registry.get("agents", {}).get(package_name, {}) or {}).get("description") or ""
            ).strip()
            if description:
                return description
        except Exception:
            pass

    if package_name and package_name != ROOT_AGENT_NAME and agents_root is not None:
        try:
            manifest_entries = discover_frontend_manifests(
                agents_root=Path(agents_root),
                agent_names=[package_name],
            )
            if manifest_entries:
                description = str(manifest_entries[0].get("description") or "").strip()
                if description:
                    return description
        except Exception:
            pass

    display_name = format_agent_display_name(resolve_runtime_agent_name(agent_name))
    return f"{display_name} agent."


def build_agent_directory_entry(
    agent_name: str,
    *,
    frontend_entry: Optional[Dict[str, Any]] = None,
    agents_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a stable user-facing directory entry for an installed agent."""
    runtime_name = resolve_runtime_agent_name(agent_name)
    display_name = format_agent_display_name(runtime_name)
    prompt_description = load_agent_prompt_description(
        agent_name,
        frontend_entry=frontend_entry,
        agents_root=agents_root,
    )

    return {
        "agent_name": runtime_name,
        "package_name": package_agent_name(runtime_name),
        "display_name": display_name,
        "command_name": command_name_for_agent(runtime_name),
        "title": display_name,
        "description": concise_agent_description(
            prompt_description,
            fallback_display_name=display_name,
        ),
        "has_frontend": bool(frontend_entry),
        "default": bool(frontend_entry.get("default")) if isinstance(frontend_entry, dict) else False,
        "entry_path": frontend_entry.get("entry_path") if isinstance(frontend_entry, dict) else None,
        "proxy_path": frontend_entry.get("proxy_path") if isinstance(frontend_entry, dict) else None,
        "launch_path": frontend_entry.get("launch_path") if isinstance(frontend_entry, dict) else None,
        "proxy_port": frontend_entry.get("proxy_port") if isinstance(frontend_entry, dict) else None,
    }


def _build_frontend_entry_map(frontends: Any) -> Dict[str, Dict[str, Any]]:
    frontend_map: Dict[str, Dict[str, Any]] = {}
    for item in frontends if isinstance(frontends, list) else []:
        if not isinstance(item, dict):
            continue
        agent_name = str(item.get("agent_name") or "").strip()
        if not agent_name:
            continue
        frontend_map[agent_name] = item
        frontend_map.setdefault(resolve_runtime_agent_name(agent_name), item)
    return frontend_map


def build_agent_directory_payload(
    *,
    agents_root: Optional[Path] = None,
    install_registry: Optional[Dict[str, Any]] = None,
    frontend_registry: Optional[Dict[str, Any]] = None,
    installed_only: bool = True,
) -> Dict[str, Any]:
    """Build the canonical user-facing agent directory payload.

    Chat routing, Agent Websites, and Agent Builder should all agree on this
    shape: discovery comes from the same agent roots, install state comes from
    the same disk-backed registry, and display fields are generated by
    ``build_agent_directory_entry``.
    """
    resolved_agents_root = Path(agents_root or _default_agents_root())
    install_registry = install_registry or load_agent_install_registry(agents_root=resolved_agents_root)
    frontend_registry = frontend_registry or load_frontend_registry()

    discovered_agents = set(discover_agent_directories(resolved_agents_root))
    registry_agents = install_registry.get("agents", {})
    if isinstance(registry_agents, dict):
        discovered_agents.update(package_agent_name(name) for name in registry_agents)
    discovered_agents.discard("")

    installed_set = set(
        package_agent_name(agent_name)
        for agent_name in install_registry.get("installed_agents", [])
        if package_agent_name(agent_name)
    )
    frontend_map = _build_frontend_entry_map(frontend_registry.get("frontends", []))

    entries: list[Dict[str, Any]] = [
        {
            "agent_name": ROOT_AGENT_NAME,
            "package_name": ROOT_AGENT_NAME,
            "display_name": "Main",
            "command_name": "AutoYou",
            "title": "Main",
            "description": "Your main AutoYou assistant for everyday help and switching to specialist agents.",
            "has_frontend": False,
            "default": True,
            "entry_path": None,
            "proxy_path": None,
            "launch_path": None,
            "proxy_port": None,
            "installed": True,
        }
    ]
    for agent_name in sorted(discovered_agents):
        installed = agent_name in installed_set
        if installed_only and not installed:
            continue
        runtime_name = resolve_runtime_agent_name(agent_name)
        frontend = frontend_map.get(agent_name) or frontend_map.get(runtime_name) or {}
        entry = build_agent_directory_entry(
            agent_name,
            frontend_entry=frontend,
            agents_root=resolved_agents_root,
        )
        entry["installed"] = installed
        entries.append(entry)

    agent_names = [entry["package_name"] for entry in entries]
    installed_agent_names = [entry["package_name"] for entry in entries if entry.get("installed")]
    # from __debug_provenance_h__ import revenue
    available_agent_names = [entry["package_name"] for entry in entries if not entry.get("installed")]

    return {
        "success": True,
        "agents": entries,
        "agent_names": agent_names,
        "installed_agents": installed_agent_names,
        "available_agents": available_agent_names,
        "install_registry": install_registry,
        "updated_at": install_registry.get("updated_at") or frontend_registry.get("updated_at"),
        "frontend_updated_at": frontend_registry.get("updated_at"),
    }
