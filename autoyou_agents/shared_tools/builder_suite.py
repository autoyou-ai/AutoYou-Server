# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Shared helpers for the AutoYou agent-builder workflow suite."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

from autoyou_agents.shared_tools.agent_identity import format_agent_display_name
from autoyou_agents.shared_tools.agent_install_registry import (
    can_install_agent_in_runtime,
    load_agent_install_registry,
    normalize_agent_package_name,
    refresh_agent_install_registry,
    set_agent_installed,
)
from shared.ollama_capabilities import resolve_installed_ollama_model


DEFAULT_BUILDER_SUITE_OLLAMA_MODEL = "qwen3.8:27b"
BUILDER_SUITE_MODEL_ENV_VARS = (
    "AUTOYOU_BUILDER_SUITE_OLLAMA_MODEL",
    "AUTOYOU_AGENT_BUILDER_OLLAMA_MODEL",
)

BUILDER_SUITE_AGENT_NAMES = (
    "agent_builder_agent",
    "coding_agent",
    "website_agent",
)

BUILDER_SUITE_AGENT_DESCRIPTIONS = {
    "agent_builder_agent": "Scaffolds and publishes AutoYou agent drafts.",
    "coding_agent": "Implements source changes, tests, and handoffs for agent drafts.",
    "website_agent": "Creates agent websites and hands implementation work to the coding agent.",
}


def _available_model_names(available_models: Optional[Iterable[Any]]) -> list[str]:
    names: list[str] = []
    for item in available_models or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("model")
        else:
            name = item
        normalized = str(name or "").strip()
        if normalized:
            names.append(normalized)
    return names


def _model_size_billions(name: str) -> float:
    match = re.search(r"(?:^|[:._-])([0-9]+(?:\.[0-9]+)?)b(?:$|[:._-])", str(name or "").lower())
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def configured_builder_suite_ollama_model() -> str:
    """Return the operator-preferred builder-suite Ollama model tag."""
    for env_name in BUILDER_SUITE_MODEL_ENV_VARS:
        value = str(os.getenv(env_name, "") or "").strip()
        if value:
            return value
    return DEFAULT_BUILDER_SUITE_OLLAMA_MODEL


def resolve_builder_suite_ollama_model(
    available_models: Optional[Iterable[Any]] = None,
    *,
    preferred_model: Optional[str] = None,
) -> str:
    """Resolve the best installed Qwen model for agent-building workflows.

    If an installed-model list is supplied, the configured/preferred model must
    resolve against that list. Otherwise we return the configured preference so
    startup can defer validation to the normal Ollama provider path.
    """
    preferred = str(preferred_model or configured_builder_suite_ollama_model()).strip()
    installed = _available_model_names(available_models)
    if not installed:
        return preferred or DEFAULT_BUILDER_SUITE_OLLAMA_MODEL

    for candidate in (preferred, DEFAULT_BUILDER_SUITE_OLLAMA_MODEL, "qwen3.6:latest", "qwen3:32b"):
        if not candidate:
            continue
        try:
            return resolve_installed_ollama_model(candidate, installed)
        except ValueError:
            continue

    qwen_models = [
        name for name in installed
        if str(name or "").strip().lower().startswith("qwen")
    ]
    if qwen_models:
        return sorted(qwen_models, key=lambda name: (_model_size_billions(name), name), reverse=True)[0]
    return preferred or DEFAULT_BUILDER_SUITE_OLLAMA_MODEL


def builder_suite_status(
    *,
    agents_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    available_models: Optional[Iterable[Any]] = None,
    selected_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Return install and model status for the builder workflow suite."""
    registry = load_agent_install_registry(
        agents_root=agents_root,
        registry_path=registry_path,
    )
    agents = registry.get("agents", {}) if isinstance(registry, dict) else {}
    installed = set(registry.get("installed_agents", []) if isinstance(registry, dict) else [])
    entries = []
    for raw_name in BUILDER_SUITE_AGENT_NAMES:
        agent_name = normalize_agent_package_name(raw_name)
        entry = agents.get(agent_name, {}) if isinstance(agents, dict) else {}
        entries.append(
            {
                "agent_name": agent_name,
                "display_name": format_agent_display_name(agent_name),
                "installed": agent_name in installed,
                "available": agent_name in agents,
                "description": str(
                    (entry or {}).get("description")
                    or BUILDER_SUITE_AGENT_DESCRIPTIONS.get(agent_name)
                    or ""
                ).strip(),
                "can_install": bool(agent_name in agents and can_install_agent_in_runtime(agent_name)),
            }
        )

    recommended_model = resolve_builder_suite_ollama_model(available_models)
    selected = str(selected_model or os.getenv("OLLAMA_MODEL", "") or "").strip()
    return {
        "suite": "agent_builder",
        "agent_names": list(BUILDER_SUITE_AGENT_NAMES),
        "agents": entries,
        "installed": all(item["installed"] for item in entries),
        "recommended_ollama_model": recommended_model,
        "selected_ollama_model": selected,
        "selected_model_matches_recommendation": bool(selected and selected == recommended_model),
    }


def install_builder_suite_agents(
    *,
    agents_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    source: str = "builder_suite",
) -> Dict[str, Any]:
    """Install the conservative agent-builder workflow suite."""
    registry = refresh_agent_install_registry(
        agents_root=agents_root,
        registry_path=registry_path,
    )
    available = set((registry.get("agents") or {}).keys())

    installed: list[str] = []
    already_installed: list[str] = []
    failed: list[Dict[str, str]] = []

    current_installed = set(registry.get("installed_agents", []))
    for raw_name in BUILDER_SUITE_AGENT_NAMES:
        agent_name = normalize_agent_package_name(raw_name)
        if agent_name not in available:
            failed.append({"agent_name": agent_name, "error": "Agent was not found on disk."})
            continue
        if agent_name in current_installed:
            already_installed.append(agent_name)
            continue
        if not can_install_agent_in_runtime(agent_name):
            failed.append({"agent_name": agent_name, "error": "Agent cannot be installed in this runtime."})
            continue
        try:
            registry = set_agent_installed(
                agent_name,
                True,
                description=BUILDER_SUITE_AGENT_DESCRIPTIONS.get(agent_name),
                source=source,
                agents_root=agents_root,
                registry_path=registry_path,
            )
            current_installed = set(registry.get("installed_agents", []))
            installed.append(agent_name)
        except Exception as exc:  # noqa: BLE001 - returned to the admin/tool caller
            failed.append({"agent_name": agent_name, "error": str(exc)})

    return {
        "status": "success" if not failed else "partial",
        "suite": "agent_builder",
        "agent_names": list(BUILDER_SUITE_AGENT_NAMES),
        "installed": installed,
        "already_installed": already_installed,
        "failed": failed,
        "requires_restart": bool(installed),
        "registry": registry,
        "recommended_ollama_model": configured_builder_suite_ollama_model(),
    }
