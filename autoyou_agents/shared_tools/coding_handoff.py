# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-0da9220bc8d0ccc1a7317e69

"""Shared coding handoff helpers for builder -> coding-agent workflows."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-0da9220bc8d0ccc1a7317e69"


CODING_HANDOFF_STATE_KEY = "temp:builder_to_coding_handoff"
PERSISTENT_CODING_HANDOFF_STATE_KEY = "_autoyou_pending_coding_handoff"
PENDING_AGENT_HANDOFF_TARGET_STATE_KEY = "_autoyou_pending_agent_handoff_target"
PENDING_AGENT_HANDOFF_REQUEST_STATE_KEY = "_autoyou_pending_agent_handoff_request"


def store_coding_handoff(state: Any, payload: Dict[str, Any]) -> None:
    """Persist a coding handoff in both legacy temp state and cross-AgentTool state."""
    if state is None:
        return
    state[CODING_HANDOFF_STATE_KEY] = payload
    state[PERSISTENT_CODING_HANDOFF_STATE_KEY] = payload


def load_coding_handoff(state: Any, *, clear: bool = False) -> Optional[Dict[str, Any]]:
    """Load a pending coding handoff from either temp or persistent session state."""
    if state is None:
        return None
    payload = state.get(CODING_HANDOFF_STATE_KEY) or state.get(PERSISTENT_CODING_HANDOFF_STATE_KEY)
    if clear:
        state[CODING_HANDOFF_STATE_KEY] = None
        state[PERSISTENT_CODING_HANDOFF_STATE_KEY] = None
    return payload if isinstance(payload, dict) else None


def can_transfer_directly_to_agent(tool_context: Any, target_agent: str) -> bool:
    """Return True when ADK transfer_to_agent can reach ``target_agent``.

    In native sub_agents mode, ``target_agent`` lives in the same ADK agent tree.
    In local-LLM AgentTool mode, each specialist runs in an isolated child runner
    whose root agent is only the specialist itself, so transfer_to_agent fails at
    runtime; we fall back to root-level state dispatch instead.

    When ``_invocation_context`` is absent (for example in unit-test stubs), keep
    returning True so legacy direct-transfer expectations remain intact.
    """
    invocation_ctx = getattr(tool_context, "_invocation_context", None)
    if invocation_ctx is None:
        return True
    agent = getattr(invocation_ctx, "agent", None)
    if agent is None:
        return True
    root = agent
    while getattr(root, "parent_agent", None) is not None:
        root = root.parent_agent
    find_agent = getattr(root, "find_agent", None)
    if callable(find_agent):
        try:
            return find_agent(target_agent) is not None
        except Exception:
            return False
    return False


def format_handoff_dispatch_request(target_agent: str, payload: Optional[Dict[str, Any]]) -> str:
    """Build a concise dispatch prompt for root-level AgentTool handoff execution."""
    data = payload if isinstance(payload, dict) else {}
    source = str(data.get("source_agent") or "builder").strip()
    name = str(data.get("agent_name") or data.get("game_id") or data.get("title") or "workspace task").strip()
    brief = str(data.get("implementation_brief") or data.get("description") or "").strip()
    agent_dir = str(data.get("agent_dir") or data.get("game_dir") or "").strip()
    parts = [f"Execute the prepared handoff from {source} for '{name}'."]
    if agent_dir:
        parts.append(f"Directory: {agent_dir}.")
    if brief:
        parts.append(f"Brief: {brief[:1200]}")
    if target_agent == "autoyou_coding_agent":
        parts.append("Call get_pending_builder_handoff first, implement the files, and verify them.")
    elif target_agent == "autoyou_website_agent":
        parts.append("Call get_pending_website_handoff first, scaffold/configure the website, and continue.")
    return " ".join(parts)


def dispatch_agent_handoff(
    *,
    target_agent: str,
    payload: Dict[str, Any],
    tool_context: Any,
    transfer_fn: Any,
) -> Dict[str, Any]:
    """Transfer directly when in the same ADK tree, else schedule root auto-dispatch."""
    if can_transfer_directly_to_agent(tool_context, target_agent):
        tool_context.actions.skip_summarization = True
        transfer_fn(target_agent, tool_context)
        return {
            "status": "success",
            "handoff_mode": "direct_transfer",
            "target_agent": target_agent,
            "agent_name": payload.get("agent_name") or payload.get("game_id"),
            "message": f"Transferred to {target_agent}.",
        }

    request_text = format_handoff_dispatch_request(target_agent, payload)
    tool_context.state[PENDING_AGENT_HANDOFF_TARGET_STATE_KEY] = target_agent
    tool_context.state[PENDING_AGENT_HANDOFF_REQUEST_STATE_KEY] = request_text
    return {
        "status": "success",
        "handoff_mode": "root_dispatch",
        "target_agent": target_agent,
        "agent_name": payload.get("agent_name") or payload.get("game_id"),
        "message": (
            f"Prepared handoff for {target_agent}. AutoYou's coordinator will invoke "
            f"{target_agent} automatically next with this prepared brief."
        ),
    }


def normalize_frontend_requirement(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    # from __debug_provenance_m__ import of
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
