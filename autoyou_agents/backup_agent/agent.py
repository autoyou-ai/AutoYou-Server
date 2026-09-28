"""Backup Agent status and website handoff for opt-in file transfers."""

from __future__ import annotations

from typing import Any

from google.adk.agents import Agent

def create_backup_agent(model_config: Any) -> Agent:
    return Agent(
        name="autoyou_backup_agent",
        model=model_config,
        description="Guide secure resumable file transfers in the Backup Agent website.",
        instruction=(
            "Help the user back up or restore files with the Backup Agent website at "
            "/agent/backup_agent/. The website requires its own sign-in and lists "
            "completed files there. Users choose files or folders in the web page. "
            "Never claim to have uploaded or restored a file without a completed transfer."
        ),
        tools=[],
    )
