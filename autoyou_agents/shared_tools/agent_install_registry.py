# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Disk-backed install state for AutoYou sub-agents."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .agent_identity import resolve_runtime_agent_name
from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json


def _get_repo_root() -> Path:
    """Get repo root, accounting for both dev and Nuitka-compiled contexts."""
    file_path = Path(__file__).resolve()
    
    # In compiled context, the file structure is:
    # AutoYouServer/autoyou_agents/shared_tools/agent_install_registry.py
    # So we need to traverse up to find the root with autoyou_agents as a direct child
    
    for parent in file_path.parents:
        agents_dir = parent / "autoyou_agents"
        # Check if this autoyou_agents is a valid agents directory (has agent.py files)
        if agents_dir.is_dir():
            # Verify it's the right one by checking for known agent subdirectories
            if (agents_dir / "shared_tools" / "agent_install_registry.py").exists():
                return parent
    
    # Fallback: try get_application_root from platform_runtime
    try:
        from shared.platform_runtime import get_application_root
        app_root = Path(get_application_root(__file__))
        # In compiled form, check if autoyou_agents exists at this level
        if (app_root / "autoyou_agents").is_dir():
            return app_root
        # If not, it might be one level up (backend -> AutoYouServer direction)
        if (app_root.parent / "autoyou_agents").is_dir():
            return app_root.parent
    except (ImportError, Exception):
        pass
    
    # Final fallback: traverse up from this file
    for parent in file_path.parents:
        if (parent / "autoyou_agents").is_dir() and (parent / "server.py").exists():
            return parent
    
    return Path(__file__).resolve().parents[2]


def _get_agent_install_registry_path_default() -> Path:
    """Get default agent install registry path."""
    from shared.platform_runtime import get_config_dir
    return get_config_dir("AutoYou", anchor=__file__) / "agent_install_registry.json"


# Don't set DEFAULT_AGENT_INSTALL_REGISTRY_PATH as a module-level constant
# Instead, we'll use the function below

DEFAULT_AGENT_INSTALL_STATES: Dict[str, bool] = {
    "admin_agent": True,
    "ads_watching_agent": True,
    "agent_builder_agent": False,    # Opt-in: advanced scaffolding tool
    "audio_agent": True,
    "backup_agent": False,          # Opt-in: stores files supplied by paired browsers
    "build_prompt_agent": False,     # Opt-in: Telegram/website prompt assembly
    "browser_agent": False,          # Opt-in: browser automation package
    "client_browser_control_agent": True,
    "claude_cli_agent": False,       # Opt-in: Claude Code CLI bridge
    "claude_desktop_agent": False,   # Opt-in: Claude Desktop bridge
    "cli_agent": False,              # Opt-in: command-line interface
    "cloudflare_agent": False,       # Opt-in: public Cloudflare Tunnel publishing
    "codex_desktop_agent": False,    # Opt-in: Codex Desktop bridge
    "coding_agent": False,           # Opt-in: repo-aware coding assistant
    "data_collector_agent": False,   # Opt-in: consented local conversation collection
    "donation_agent": True,
    "earnings_agent": True,
    "files_agent": False,            # Opt-in: authenticated local filesystem access
    "fine_tuning_agent": False,      # Opt-in: local model training
    "hermes_agent": False,           # Opt-in: install manually when Hermes Agent gateway is running locally
    "hosting_agent": False,          # Opt-in: public URL publishing
    "ionos_agent": False,            # Opt-in: persistent IONOS hosting and deployment
    "ionos_cloudflare_agent": False, # Opt-in: registrar and Cloudflare DNS handoff
    "internet_agent": True,
    "location_agent": False,          # Opt-in: consented native device location timeline
    "mail_agent": False,             # Opt-in: domain email and self-hosted mail controls
    "media_generation_agent": False, # Opt-in: heavy model dependency
    "memory_agent": True,
    "model_picker_agent": False,     # Opt-in: hardware-fit model advice
    "notes_agent": True,
    "notify_agent": True,
    "openclaw_agent": False,         # Opt-in: install manually when OpenClaw Gateway is running locally
    "page_agent": True,
    "persona_agent": True,
    "remote_desktop_agent": False,   # Opt-in: desktop control capability
    "robinhood_agent": False,        # Source-only private trading integration
    "skills_agent": False,           # Opt-in: reusable skill scripts
    "trading_agent": False,          # Source-only private trading integration
    "education_agent": False,        # Opt-in: learning workspace
    "tasks_agent": True,
    "voice_training_agent": False,   # Opt-in: local voice datasets and TTS training
    "website_agent": False,          # Opt-in: agent website scaffolding
    "win_security_agent": False,     # Opt-in: Windows-only local security telemetry
    "mac_security_agent": False,     # Opt-in: macOS-only local security telemetry
}

# Source-only agents that are never included in compiled/packaged builds. Keep
# a False entry in DEFAULT_AGENT_INSTALL_STATES for every name so source runs
# can still install them explicitly while compiled builds reject them.
PRIVATE_AGENT_PACKAGE_NAMES: frozenset[str] = frozenset(
    {
        "cloudflare_agent",
        "ionos_agent",
        "ionos_cloudflare_agent",
        "mail_agent",
        "robinhood_agent",
        "trading_agent",
    }
)
BUILTIN_AGENT_PACKAGE_NAMES = frozenset(DEFAULT_AGENT_INSTALL_STATES.keys()) - PRIVATE_AGENT_PACKAGE_NAMES


def runtime_install_block_reason(raw_name: Optional[str]) -> str:
    normalized = normalize_agent_package_name(raw_name)
    if normalized in PRIVATE_AGENT_PACKAGE_NAMES:
        return f"{normalized} is a private agent package and is not included in this packaged build."
    return (
        "Workspace draft only. Installed AutoYou only loads packaged built-in agents, "
        "so this draft stays editable here but cannot be installed or tested in this app."
    )



def normalize_agent_package_name(raw_name: Optional[str]) -> str:
    """Normalize a runtime or package-facing agent name to its package directory."""
    value = re.sub(r"[^a-zA-Z0-9_]", "_", str(raw_name or "")).lower().strip("_")
    value = re.sub(r"_+", "_", value)
    value = resolve_runtime_agent_name(value)
    if value.startswith("autoyou_") and value.endswith("_agent"):
        value = value[len("autoyou_") :]
    elif value and not value.endswith("_agent"):
        value = f"{value}_agent"
    if value in {"frontend_proxy_agent", "agent_website_builder_agent"}:
        value = "website_agent"
    return value


def is_builtin_agent_name(raw_name: Optional[str]) -> bool:
    """Return True when an agent is part of the trusted packaged runtime set."""
    return normalize_agent_package_name(raw_name) in BUILTIN_AGENT_PACKAGE_NAMES


def can_install_agent_in_runtime(
    raw_name: Optional[str],
    *,
    compiled: Optional[bool] = None,
) -> bool:
    """Return True when the current runtime is allowed to execute this agent."""
    if compiled is None:
        try:
            from shared.platform_runtime import is_compiled

            compiled = bool(is_compiled())
        except Exception:
            compiled = False

    normalized = normalize_agent_package_name(raw_name)
    if not normalized:
        return False
    if not compiled:
        return True
    return normalized in BUILTIN_AGENT_PACKAGE_NAMES


def get_agent_install_registry_path(
    registry_path: Optional[Path] = None,
) -> Path:
    if registry_path is not None:
        return Path(registry_path)
    env_path = os.environ.get("AUTOYOU_AGENT_INSTALL_REGISTRY_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    if os.environ.get("AUTOYOU_TEST_ROOT", "").strip():
        return _get_agent_install_registry_path_default()
    # Dev/runtime parity: keep registry in the workspace repo when running from
    # source so install/uninstall state is visible and shared across tools.
    # Compiled builds still persist under the user config directory.
    try:
        from shared.platform_runtime import is_compiled

        if not bool(is_compiled()):
            return (_get_repo_root() / "agent_install_registry.json").resolve()
    except Exception:
        pass
    return _get_agent_install_registry_path_default()


def _iter_agent_discovery_roots(agents_root: Path) -> list[Path]:
    roots: list[Path] = []
    try:
        from shared.platform_runtime import get_dynamic_agents_root, is_compiled, iter_agent_roots

        if is_compiled():
            for candidate in iter_agent_roots(__file__):
                if candidate not in roots:
                    roots.append(candidate)
        else:
            roots.append(get_dynamic_agents_root(anchor=__file__).resolve())
    except Exception:
        pass

    resolved_agents_root = Path(agents_root).resolve()
    if resolved_agents_root not in roots:
        roots.append(resolved_agents_root)
    for root in tuple(roots):
        private_root = root / "private"
        if private_root.is_dir() and private_root not in roots:
            roots.append(private_root)
    return roots


def discover_agent_directories(agents_root: Path) -> list[str]:
    """Return a list of agent package names.

    In compiled (Nuitka) mode the on-disk ``agent.py`` files do not exist
    because the agent modules are compiled into the binary.  Fall back to the
    hardcoded ``DEFAULT_AGENT_INSTALL_STATES`` keys so that the install
    registry still reflects the correct agent list.

    In dev mode, scan the filesystem for ``agent.py`` files as before.
    """
    discovered: set[str] = set()
    try:
        from shared.platform_runtime import is_compiled
        if is_compiled():
            discovered.update(BUILTIN_AGENT_PACKAGE_NAMES)
    except Exception:
        pass

    for root in _iter_agent_discovery_roots(Path(agents_root)):
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if not path.is_dir() or path.name.startswith("_"):
                continue
            if (path / "agent.py").exists():
                discovered.add(path.name)
    return sorted(discovered)


def _normalize_registry_payload(
    payload: Optional[Dict[str, Any]],
    *,
    agents_root: Path,
) -> Dict[str, Any]:
    payload = payload or {}
    discovered_agents = discover_agent_directories(agents_root)
    stored_agents = payload.get("agents")
    if not isinstance(stored_agents, dict):
        stored_agents = {}
    normalized_stored_agents: Dict[str, Dict[str, Any]] = {}
    for raw_agent_name, raw_entry in stored_agents.items():
        normalized_agent_name = normalize_agent_package_name(raw_agent_name)
        if not normalized_agent_name or not isinstance(raw_entry, dict):
            continue
        existing_entry = normalized_stored_agents.get(normalized_agent_name)
        if existing_entry is None:
            normalized_stored_agents[normalized_agent_name] = dict(raw_entry)
            continue
        merged_entry = dict(existing_entry)
        for key in ("installed", "description", "source", "updated_at"):
            if merged_entry.get(key) in (None, "") and raw_entry.get(key) not in (None, ""):
                merged_entry[key] = raw_entry.get(key)
        normalized_stored_agents[normalized_agent_name] = merged_entry
    stored_agents = normalized_stored_agents

    compiled_runtime = False
    try:
        from shared.platform_runtime import is_compiled

        compiled_runtime = bool(is_compiled())
    except Exception:
        compiled_runtime = False

    agents: Dict[str, Dict[str, Any]] = {}
    for agent_name in discovered_agents:
        stored_entry = stored_agents.get(agent_name)
        if not isinstance(stored_entry, dict):
            stored_entry = {}
        installed = stored_entry.get("installed")
        if installed is None:
            installed = DEFAULT_AGENT_INSTALL_STATES.get(agent_name, False)
        if compiled_runtime and bool(installed) and not can_install_agent_in_runtime(agent_name, compiled=True):
            installed = False
        agents[agent_name] = {
            "installed": bool(installed),
            "description": str(stored_entry.get("description") or "").strip() or None,
            "source": str(stored_entry.get("source") or "").strip() or None,
            "updated_at": str(stored_entry.get("updated_at") or "").strip() or None,
        }

    installed_agents = sorted(
        agent_name
        for agent_name, entry in agents.items()
        if bool(entry.get("installed"))
    )
    available_agents = sorted(
        agent_name
        for agent_name in agents
        if agent_name not in installed_agents
    )

    return {
        "schema_version": 1,
        "updated_at": payload.get("updated_at"),
        "agents": agents,
        "installed_agents": installed_agents,
        "available_agents": available_agents,
    }


def load_agent_install_registry(
    *,
    agents_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    agents_root = Path(agents_root or (_get_repo_root() / "autoyou_agents"))
    registry_path = get_agent_install_registry_path(registry_path)
    if not registry_path.is_file():
        return _normalize_registry_payload({}, agents_root=agents_root)

    try:
        payload = load_secure_json(registry_path, default={})
    except SecureStorageError:
        raise
    except (OSError, TypeError, ValueError):
        payload = {}
    return _normalize_registry_payload(payload, agents_root=agents_root)


def write_agent_install_registry(
    registry: Dict[str, Any],
    *,
    registry_path: Optional[Path] = None,
) -> Path:
    registry_path = get_agent_install_registry_path(registry_path)
    save_secure_json(registry_path, registry)
    return registry_path


def refresh_agent_install_registry(
    *,
    agents_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    registry = load_agent_install_registry(
        agents_root=agents_root,
        registry_path=registry_path,
    )
    registry["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    write_agent_install_registry(registry, registry_path=registry_path)
    return registry


def set_agent_installed(
    agent_name: str,
    installed: bool,
    *,
    description: Optional[str] = None,
    source: Optional[str] = None,
    agents_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    registry = refresh_agent_install_registry(
        agents_root=agents_root,
        registry_path=registry_path,
    )
    agents = registry.setdefault("agents", {})
    if agent_name not in agents:
        raise KeyError(f"Agent '{agent_name}' does not exist on disk.")
    if bool(installed) and not can_install_agent_in_runtime(agent_name):
        raise PermissionError(runtime_install_block_reason(agent_name))

    entry = dict(agents.get(agent_name) or {})
    entry["installed"] = bool(installed)
    if description is not None:
        cleaned_description = str(description).strip()
        entry["description"] = cleaned_description or None
    if source is not None:
        cleaned_source = str(source).strip()
        entry["source"] = cleaned_source or None
    entry["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    agents[agent_name] = entry

    normalized = _normalize_registry_payload(registry, agents_root=Path(agents_root or (_get_repo_root() / "autoyou_agents")))
    normalized["updated_at"] = entry["updated_at"]
    write_agent_install_registry(normalized, registry_path=registry_path)
    return normalized


def get_installed_agent_names(
    *,
    agents_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
) -> list[str]:
    registry = load_agent_install_registry(
        agents_root=agents_root,
        registry_path=registry_path,
    )
    return list(registry.get("installed_agents", []))


def is_agent_installed(
    agent_name: str,
    *,
    agents_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
) -> bool:
    return agent_name in set(
        get_installed_agent_names(
            agents_root=agents_root,
            registry_path=registry_path,
        )
    )
