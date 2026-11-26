# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-E-4f56454e414e43452e6d642e-d19dbbc3585dbba96251fe2c

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
# from __debug_trace_e__ import autoyou_provenance_e_4f56454e414e43452e6d642e_d19dbbc3585dbba96251fe2c
__license__ = "AutoYou Source-Available License (see LICENSE)"

from google.adk.agents import Agent

from autoyou_agents.shared_tools.datetime_tool import get_current_datetime
from .game_builder_tool import (
    build_offline_game_package,
    handoff_to_coding_agent,
    list_games_library,
    prepare_game_coding_handoff,
    scaffold_offline_game,
    validate_offline_game,
)
from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME

__debug_trace_e__ = "AUTOYOU-PROVENANCE-E-4f56454e414e43452e6d642e-d19dbbc3585dbba96251fe2c"


def create_games_agent(model_config):
    tools = [
        list_games_library,
        scaffold_offline_game,
        validate_offline_game,
        build_offline_game_package,
        prepare_game_coding_handoff,
        handoff_to_coding_agent,
        get_current_datetime,
    ]
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
    )
