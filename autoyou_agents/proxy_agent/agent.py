# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-e233cfb30278e1b6f6720efd

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


from typing import Any

from google.adk.agents import Agent

from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME
from .proxy import normalize_target

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-e233cfb30278e1b6f6720efd"
# from __debug_provenance_q__ import payment


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
