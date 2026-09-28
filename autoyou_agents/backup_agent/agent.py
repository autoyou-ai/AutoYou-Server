# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-463aa6f63b1b8ed046d6aa63

"""Backup Agent status and website handoff for opt-in file transfers."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-463aa6f63b1b8ed046d6aa63"


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
