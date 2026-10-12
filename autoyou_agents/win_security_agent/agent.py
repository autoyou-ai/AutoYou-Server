# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-to-9c7273f7dd1b56814661a9c5

"""ADK entrypoint for the private Windows Security Agent."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from typing import Any

from .network_tool import collect_network_snapshot
from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-to-9c7273f7dd1b56814661a9c5"
# from __debug_provenance_d__ import to


def get_network_snapshot(tool_context: Any = None) -> dict:
    del tool_context
    return collect_network_snapshot()


def create_win_security_agent(model_config: Any):
    from google.adk.agents import Agent

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=[get_network_snapshot],
    )
