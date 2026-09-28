# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
AutoYou Hermes Bridge Agent.

This optional agent sends requests to a locally running Hermes Agent gateway
(NousResearch Hermes). Users install it from Admin -> Agent Management after
they have the Hermes gateway running locally.
"""
import logging
import os
from typing import Optional

import httpx
from google.adk.agents import Agent

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

logger = logging.getLogger(__name__)

# -- Hermes connectivity helpers ----------------------------------------------

def _hermes_base_url() -> str:
    port = int(os.getenv("HERMES_AGENT_PORT", os.getenv("HERMES_PORT", "8642")))
    return f"http://127.0.0.1:{port}/v1"

def _hermes_token() -> str:
    return os.getenv("HERMES_AGENT_TOKEN", os.getenv("HERMES_TOKEN", "")) or ""

def _hermes_model() -> str:
    """Return the Hermes model/agent alias to use for sub-agent calls."""
    return os.getenv("HERMES_AGENT_MODEL", os.getenv("HERMES_MODEL", "hermes-agent"))

# -- Tools --------------------------------------------------------------------

def query_hermes(
    prompt: str,
    session_key: Optional[str] = None,
) -> str:
    """Send a request to the local Hermes Agent gateway and return its response.

    Args:
        prompt:      The user's request to forward to Hermes.
        session_key: Optional stable key to maintain a persistent Hermes
                     session (maps to x-hermes-session-key header).
                     Leave empty for stateless requests.

    Returns:
        Hermes's text response, or an error message if the gateway is
        unreachable.
    """
    base_url = _hermes_base_url()
    token    = _hermes_token()
    model    = _hermes_model()

    headers: dict = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session_key:
        headers["x-hermes-session-key"] = session_key

    payload = {
        "model":    model,
        "messages": [{"role": "user", "content": prompt}],
        "stream":   False,
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if choices:
            content = (choices[0].get("message") or {}).get("content") or ""
            return content.strip()
        return "(Hermes returned an empty response)"
    except httpx.ConnectError:
        port = os.getenv("HERMES_AGENT_PORT", os.getenv("HERMES_PORT", "8642"))
        logger.warning("Hermes gateway not reachable at port %s", port)
        return (
            f"The Hermes Agent gateway is not running on port {port}. "
            "Please start the Hermes gateway and try again."
        )
    except httpx.HTTPStatusError as exc:
        logger.error("Hermes HTTP error: %s", exc)
        return f"Hermes returned an error: {exc.response.status_code} {exc.response.text[:200]}"
    except Exception as exc:
        logger.error("Hermes query failed: %s", exc)
        return f"Failed to reach Hermes: {exc}"

def check_hermes_status() -> str:
    """Check whether the Hermes Agent gateway is reachable and return a brief status.

    Returns:
        A plain-text status string describing Hermes's availability and the
        models/agents it exposes.
    """
    base_url = _hermes_base_url()
    token    = _hermes_token()
    headers: dict = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{base_url}/models", headers=headers)
        response.raise_for_status()
        data = response.json()
        models = [m.get("id", "") for m in (data.get("data") or [])]
        model_list = ", ".join(models) if models else "(none listed)"
        port = os.getenv("HERMES_AGENT_PORT", os.getenv("HERMES_PORT", "8642"))
        return f"Hermes Agent gateway is running on port {port}. Available models/agents: {model_list}."
    except httpx.ConnectError:
        port = os.getenv("HERMES_AGENT_PORT", os.getenv("HERMES_PORT", "8642"))
        return f"Hermes Agent gateway is NOT running on port {port}."
    except Exception as exc:
        return f"Hermes status check failed: {exc}"

# -- Agent factory ------------------------------------------------------------

def create_hermes_agent(model_config):
    """Create the Hermes bridge agent.

    Args:
        model_config: The model configuration used to host this optional bridge.
                      Hermes task execution is handled by `query_hermes`.

    Returns:
        Agent: Configured Hermes bridge agent.
    """
    return Agent(
        name        = AGENT_NAME,
        model       = model_config,
        description = AGENT_DESCRIPTION,
        instruction = AGENT_INSTRUCTION,
        tools       = [query_hermes, check_hermes_status, get_current_datetime],
    )
