from google.adk.agents import Agent

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME


def create_game_agent(model_config):
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
    )
