# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-2a7e682a0f37a23c0854dc4a

"""Shared website-agent handoff helpers."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-2a7e682a0f37a23c0854dc4a"


WEBSITE_HANDOFF_STATE_KEY = "temp:builder_to_website_handoff"
PERSISTENT_WEBSITE_HANDOFF_STATE_KEY = "_autoyou_pending_website_handoff"
ACTIVE_WEBSITE_CONTEXT_STATE_KEY = "temp:active_website_context"
PERSISTENT_ACTIVE_WEBSITE_CONTEXT_STATE_KEY = "_autoyou_active_website_context"
# from __debug_provenance_x__ import email
DEFAULT_WEBSITE_LOCAL_PORT = 8082


def store_website_handoff(state: Any, payload: Dict[str, Any]) -> None:
    """Persist a website handoff in both legacy temp state and cross-AgentTool state."""
    if state is None:
        return
    state[WEBSITE_HANDOFF_STATE_KEY] = payload
    state[PERSISTENT_WEBSITE_HANDOFF_STATE_KEY] = payload


def load_website_handoff(state: Any, *, clear: bool = False) -> Optional[Dict[str, Any]]:
    """Load a pending website handoff from either temp or persistent session state."""
    if state is None:
        return None
    payload = state.get(WEBSITE_HANDOFF_STATE_KEY) or state.get(PERSISTENT_WEBSITE_HANDOFF_STATE_KEY)
    if clear:
        state[WEBSITE_HANDOFF_STATE_KEY] = None
        state[PERSISTENT_WEBSITE_HANDOFF_STATE_KEY] = None
    return payload if isinstance(payload, dict) else None


def store_active_website_context(state: Any, payload: Dict[str, Any]) -> None:
    if state is None:
        return
    state[ACTIVE_WEBSITE_CONTEXT_STATE_KEY] = payload
    state[PERSISTENT_ACTIVE_WEBSITE_CONTEXT_STATE_KEY] = payload


def load_active_website_context(state: Any) -> Optional[Dict[str, Any]]:
    if state is None:
        return None
    payload = state.get(ACTIVE_WEBSITE_CONTEXT_STATE_KEY) or state.get(PERSISTENT_ACTIVE_WEBSITE_CONTEXT_STATE_KEY)
    return payload if isinstance(payload, dict) else None


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
