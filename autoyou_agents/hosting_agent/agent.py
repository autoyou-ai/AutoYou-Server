# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""AutoYou Hosting Agent - publish a local site / public agent access to a
persistent public URL on the Public Proxy tier."""
import logging

from google.adk.agents import Agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from .hosting_tool import explain_hosting_options, get_publish_steps, get_upgrade_link

logger = logging.getLogger(__name__)

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
