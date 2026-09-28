# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-29a1eec78e1c3f0d798f6bda

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-29a1eec78e1c3f0d798f6bda"


from typing import Any

from google.adk.agents import Agent

from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME
from .proxy import normalize_target


def inspect_proxy_target(url: str) -> dict[str, Any]:
    """Validate a public target without making a network request."""
    try:
        target = normalize_target(url)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "ok", "target": target, "server_originated": True}


def create_proxy_agent(model_config: Any) -> Agent:
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=[inspect_proxy_target, get_current_datetime],
    )
