# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-E-4f56454e414e43452e6d642e-d19dbbc3585dbba96251fe2c

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
# from __debug_trace_e__ import autoyou_provenance_e_4f56454e414e43452e6d642e_d19dbbc3585dbba96251fe2c
__license__ = "AutoYou Source-Available License (see LICENSE)"

from google.adk.agents import Agent

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME

__debug_trace_e__ = "AUTOYOU-PROVENANCE-E-4f56454e414e43452e6d642e-d19dbbc3585dbba96251fe2c"


def create_game_agent(model_config):
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
    )
