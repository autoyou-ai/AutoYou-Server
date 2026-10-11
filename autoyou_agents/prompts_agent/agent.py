# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""ADK Agent and tools for prompts_agent ("My Prompt" app)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent

from .collector import get_global_collector
from .db import PromptsDatabase
from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME

logger = logging.getLogger(__name__)


def search_prompts(
    query: str,
    software: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = 15,
) -> Dict[str, Any]:
    """Search collected prompts across Codex, Claude, Cursor, Antigravity, and VS Code.

    Args:
        query: Full-text search keywords, phrases, or topics.
        software: Optional filter ('codex', 'claude', 'vscode', 'cursor', 'antigravity', 'custom_hook').
        project: Optional project name filter.
        limit: Maximum number of prompt results to return (default: 15).

    Returns:
        Dict with status, total_matches, and matched prompt snippets.
    """
    try:
        collector = get_global_collector()
        prompts, total = collector.db.get_prompts(
            query=query,
            software=software,
            project=project,
            limit=limit,
        )
        results = []
        for p in prompts:
            results.append({
                "prompt_id": p["id"],
                "software": p["software"],
                "project": p["project_name"],
                "title": p.get("session_title"),
                "snippet": (p["prompt_text"] or "")[:300],
                "has_output": bool(p.get("output_text")),
                "timestamp": p["timestamp"],
            })
        return {
            "status": "success",
            "total_matches": total,
            "count": len(results),
            "prompts": results,
        }
    except Exception as exc:
        logger.exception("search_prompts failed")
        return {"status": "error", "message": str(exc)}


def get_prompt_details(prompt_id: str) -> Dict[str, Any]:
    """Retrieve full prompt text, assistant output, attachments, and turn metadata.

    Args:
        prompt_id: Unique prompt identifier.

    Returns:
        Dict with full prompt details and session turn context.
    """
    try:
        collector = get_global_collector()
        prompt = collector.db.get_prompt_by_id(prompt_id)
        if not prompt:
            return {"status": "error", "message": f"Prompt '{prompt_id}' not found."}

        return {
            "status": "success",
            "prompt": prompt,
        }
    except Exception as exc:
        logger.exception("get_prompt_details failed")
        return {"status": "error", "message": str(exc)}


def list_prompt_projects() -> Dict[str, Any]:
    """List all detected projects and worktrees with prompt and session counts.

    Returns:
        Dict with list of projects, software platforms, and last activity timestamps.
    """
    try:
        collector = get_global_collector()
        projects = collector.db.get_projects()
        return {
            "status": "success",
            "count": len(projects),
            "projects": projects,
        }
    except Exception as exc:
        logger.exception("list_prompt_projects failed")
        return {"status": "error", "message": str(exc)}


def trigger_sync(full_resync: bool = False) -> Dict[str, Any]:
    """Trigger an immediate harvest across all detected AI tools.

    Args:
        full_resync: If True, re-scans all historical files regardless of cache.

    Returns:
        Dict with sync result summary (sources scanned, new prompts, updated sessions).
    """
    try:
        collector = get_global_collector()
        res = collector.scan_and_collect(full_resync=full_resync)
        return {
            "status": "success",
            **res,
        }
    except Exception as exc:
        logger.exception("trigger_sync failed")
        return {"status": "error", "message": str(exc)}


def get_sync_status() -> Dict[str, Any]:
    """Check whether a background collection sync is actively running and review the last run.

    Returns:
        Dict with is_syncing boolean and last run outcome.
    """
    try:
        collector = get_global_collector()
        stats = collector.db.get_stats()
        return {
            "status": "success",
            "is_syncing": collector.is_syncing,
            "last_sync": stats.get("last_sync"),
        }
    except Exception as exc:
        logger.exception("get_sync_status failed")
        return {"status": "error", "message": str(exc)}


def get_prompt_stats() -> Dict[str, Any]:
    """Return platform-wide prompt collection statistics and software breakdown.

    Returns:
        Dict with total prompts, sessions, projects, breakdown by tool, and DB size.
    """
    try:
        collector = get_global_collector()
        stats = collector.db.get_stats()
        return {
            "status": "success",
            **stats,
        }
    except Exception as exc:
        logger.exception("get_prompt_stats failed")
        return {"status": "error", "message": str(exc)}


def create_prompts_agent(model_config: Any) -> Agent:
    """Create and configure the prompts_agent ADK agent."""
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=[
            search_prompts,
            get_prompt_details,
            list_prompt_projects,
            trigger_sync,
            get_sync_status,
            get_prompt_stats,
        ],
    )
