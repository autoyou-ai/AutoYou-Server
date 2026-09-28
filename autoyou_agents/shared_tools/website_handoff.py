# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-363546313441613439306132-ecba3e0a8ee18e0b5048d2a8

"""Shared website-agent handoff helpers."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-363546313441613439306132-ecba3e0a8ee18e0b5048d2a8"


from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


WEBSITE_HANDOFF_STATE_KEY = "temp:builder_to_website_handoff"
ACTIVE_WEBSITE_CONTEXT_STATE_KEY = "temp:active_website_context"
DEFAULT_WEBSITE_LOCAL_PORT = 8082


def build_website_handoff_payload(
    *,
    agent_name: str,
    description: str,
    tool_name: str,
    tool_description: str,
    implementation_brief: str,
    constraints: Optional[str] = None,
    testing_requirements: Optional[str] = None,
    agent_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create the structured builder -> website agent payload."""
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
        "agent_dir": agent_dir_str,
        "scaffold_files": files,
        "recommended_ui_stack": "fastapi_static_split",
        "recommended_local_port": DEFAULT_WEBSITE_LOCAL_PORT,
        "dynamic_proxy_phase": "Phase 3",
    }


WEBSITE_BUILDER_HANDOFF_STATE_KEY = WEBSITE_HANDOFF_STATE_KEY
ACTIVE_WEBSITE_BUILDER_CONTEXT_STATE_KEY = ACTIVE_WEBSITE_CONTEXT_STATE_KEY
DEFAULT_AGENT_WEBSITE_LOCAL_PORT = DEFAULT_WEBSITE_LOCAL_PORT
FRONTEND_PROXY_HANDOFF_STATE_KEY = WEBSITE_HANDOFF_STATE_KEY
ACTIVE_FRONTEND_PROXY_CONTEXT_STATE_KEY = ACTIVE_WEBSITE_CONTEXT_STATE_KEY
DEFAULT_FRONTEND_LOCAL_PORT = DEFAULT_WEBSITE_LOCAL_PORT


def build_frontend_proxy_handoff_payload(
    *,
    agent_name: str,
    description: str,
    tool_name: str,
    tool_description: str,
    implementation_brief: str,
    constraints: Optional[str] = None,
    testing_requirements: Optional[str] = None,
    agent_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    return build_website_handoff_payload(
        agent_name=agent_name,
        description=description,
        tool_name=tool_name,
        tool_description=tool_description,
        implementation_brief=implementation_brief,
        constraints=constraints,
        testing_requirements=testing_requirements,
        agent_dir=agent_dir,
    )


def build_agent_website_builder_handoff_payload(
    *,
    agent_name: str,
    description: str,
    tool_name: str,
    tool_description: str,
    implementation_brief: str,
    constraints: Optional[str] = None,
    testing_requirements: Optional[str] = None,
    agent_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    return build_website_handoff_payload(
        agent_name=agent_name,
        description=description,
        tool_name=tool_name,
        tool_description=tool_description,
        implementation_brief=implementation_brief,
        constraints=constraints,
        testing_requirements=testing_requirements,
        agent_dir=agent_dir,
    )
