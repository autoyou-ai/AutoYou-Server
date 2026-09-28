# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

from __future__ import annotations

from typing import Any

from google.adk.agents import Agent

from .build_prompt_tool import (
    build_prompt,
    configure_prompt,
    delete_prompt,
    execute_prompt,
    get_prompt,
    new_prompt,
    result_prompt,
    status_prompt,
    stop_prompt,
)
from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME


def create_build_prompt_agent(model_config: Any) -> Any:
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=[
            build_prompt,
            get_prompt,
            status_prompt,
            result_prompt,
            delete_prompt,
            new_prompt,
            execute_prompt,
            stop_prompt,
            configure_prompt,
        ],
    )
