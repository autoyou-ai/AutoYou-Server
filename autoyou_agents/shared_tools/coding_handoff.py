# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-7363686564756c6520796561-73a8f6af5ece0751ffcc00a4

"""Shared coding handoff helpers for builder -> coding-agent workflows."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-7363686564756c6520796561-73a8f6af5ece0751ffcc00a4"


from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


CODING_HANDOFF_STATE_KEY = "temp:builder_to_coding_handoff"


def normalize_frontend_requirement(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"yes", "y", "true", "1", "required"}:
        return "yes"
    if normalized in {"no", "n", "false", "0", "not_needed"}:
        return "no"
    return "unknown"


def build_coding_handoff_payload(
    *,
    agent_name: str,
    description: str,
    tool_name: str,
    tool_description: str,
    implementation_brief: str,
    constraints: Optional[str] = None,
    testing_requirements: Optional[str] = None,
    frontend_requirement: Optional[str] = None,
    agent_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create a structured handoff payload for coding_agent."""
    files = []
    agent_dir_str = None
    if agent_dir and agent_dir.exists():
        agent_dir_str = str(agent_dir)
        files = sorted(str(path) for path in agent_dir.iterdir() if path.is_file())

    return {
        "source_agent": "agent_builder_agent",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "agent_name": agent_name,
        "description": description,
        "tool_name": tool_name,
        "tool_description": tool_description,
        "implementation_brief": implementation_brief.strip(),
        "constraints": (constraints or "").strip() or None,
        "testing_requirements": (testing_requirements or "").strip() or None,
        "frontend_requirement": normalize_frontend_requirement(frontend_requirement),
        "agent_dir": agent_dir_str,
        "scaffold_files": files,
        "dynamic_proxy_phase": "Phase 3",
    }
