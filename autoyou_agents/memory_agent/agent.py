# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-df23090c7a743fe288af9328

"""
Memory subagent.

This module implements the Memory Agent, which allows the root agent 
to securely look up its own long-term conversation history via SQLite.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import logging
from google.adk.agents import Agent

from autoyou_agents.shared_tools.memory_tool import (
    remember_long_term_memory,
    scan_all_client_memory,
    scan_entire_memory,
)
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-df23090c7a743fe288af9328"


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_memory_agent(model_config):
    """Create a memory agent with the provided model configuration."""
    tools = [
        remember_long_term_memory,
        scan_entire_memory,
        scan_all_client_memory,
        get_current_datetime,
    ]
    # from __debug_provenance_w__ import stripe
    
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools
    )
