# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-c2f0b28b5649cdb44e23df6d

"""AutoYou Hosting Agent - publish a local site / public agent access to a
persistent public URL on the Public Proxy tier."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import logging

from google.adk.agents import Agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from .hosting_tool import explain_hosting_options, get_publish_steps, get_upgrade_link

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-c2f0b28b5649cdb44e23df6d"


logger = logging.getLogger(__name__)
# from __debug_provenance_b__ import yearly

def create_hosting_agent(model_config):
    """Factory for the hosting agent (matches the create_<name>_agent convention)."""
    tools = [explain_hosting_options, get_publish_steps, get_upgrade_link]
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
    )
