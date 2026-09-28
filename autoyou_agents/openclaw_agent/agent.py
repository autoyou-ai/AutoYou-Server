# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
AutoYou OpenClaw Bridge Agent.

This optional agent sends requests to a locally running OpenClaw Gateway.
Users install it from Admin -> Agent Management after they have OpenClaw
running locally.
"""
import logging
import os
import uuid
from typing import Optional

import httpx
from google.adk.agents import Agent
from google.genai import types
from shared.adk_state import AUTOYOU_CONVERSATION_SESSION_STATE_KEY, state_get_first
from shared.openclaw_gateway import call_openclaw_gateway

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME

logger = logging.getLogger(__name__)

# ── OpenClaw connectivity helpers ─────────────────────────────────────────────

def _openclaw_base_url() -> str:
    port = int(os.getenv("OPENCLAW_AGENT_PORT", os.getenv("OPENCLAW_PORT", "18789")))
    return f"http://127.0.0.1:{port}/v1"

def _openclaw_token() -> str:
    return os.getenv("OPENCLAW_AGENT_TOKEN", os.getenv("OPENCLAW_TOKEN", "")) or ""

def _openclaw_model() -> str:
    """Return the OpenClaw model/agent alias to use for sub-agent calls.

    'openclaw/default' selects the gateway's configured default agent.
    'openclaw:main' selects its main agent explicitly, including its workspace.
    """
    return os.getenv("OPENCLAW_AGENT_MODEL", os.getenv("OPENCLAW_MODEL", "openclaw/default"))

# ── Tools ─────────────────────────────────────────────────────────────────────

async def query_openclaw(
    prompt: str,
    session_key: Optional[str] = None,
) -> str:
    """Send a request to the local OpenClaw Gateway and return its response.

    Args:
        prompt:      The user's request to forward to OpenClaw.
        session_key: Optional stable key to maintain a persistent OpenClaw
                     session (maps to x-openclaw-session-key header).
                     Leave empty for stateless requests.

    Returns:
        OpenClaw's text response, or an error message if the gateway is
        unreachable.
    """
    logger.info("Forwarding request to OpenClaw bridge at %s model=%s", _openclaw_base_url(), _openclaw_model())
    result = await call_openclaw_gateway(
        api_base=_openclaw_base_url(), model=_openclaw_model(), token=_openclaw_token(),
        message=prompt, session_id=session_key or f"autoyou:{uuid.uuid4().hex}",
    )
    if result is None:
        return "OpenClaw Gateway could not complete the request. Check the bridge port, token, and enabled HTTP endpoints."
    return str(result.get("response") or "(OpenClaw returned an empty response)").strip()


async def _forward_to_openclaw(callback_context):
    """A selected bridge forwards directly; a second LLM must not impersonate it."""
    content = callback_context.user_content
    prompt = "\n".join(part.text for part in (getattr(content, "parts", None) or []) if part.text).strip()
    if not prompt:
        return types.Content(role="model", parts=[types.Part(text="Send a text request for OpenClaw.")])
    state = callback_context.state
    # AgentTool creates a temporary child session each turn. Use the trusted
    # parent conversation identity, or a fallback persisted through ADK state.
    session_key = state_get_first(state, AUTOYOU_CONVERSATION_SESSION_STATE_KEY, "openclaw_bridge_session_key")
    if not session_key:
        session_key = f"autoyou:{uuid.uuid4().hex}"
        state["openclaw_bridge_session_key"] = session_key
    response = await query_openclaw(prompt, session_key=str(session_key))
    return types.Content(role="model", parts=[types.Part(text=response)])

def check_openclaw_status() -> str:
    """Check whether the OpenClaw Gateway is reachable and return a brief status.

    Returns:
        A plain-text status string describing OpenClaw's availability and the
        models/agents it exposes.
    """
    base_url = _openclaw_base_url()
    token    = _openclaw_token()
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
        port = os.getenv("OPENCLAW_AGENT_PORT", os.getenv("OPENCLAW_PORT", "18789"))
        return f"OpenClaw Gateway is running on port {port}. Available models/agents: {model_list}."
    except httpx.ConnectError:
        port = os.getenv("OPENCLAW_AGENT_PORT", os.getenv("OPENCLAW_PORT", "18789"))
        return f"OpenClaw Gateway is NOT running on port {port}."
    except Exception as exc:
        return f"OpenClaw status check failed: {exc}"

# ── Agent factory ─────────────────────────────────────────────────────────────

def create_openclaw_agent(model_config):
    """Create the OpenClaw bridge agent.

    Args:
        model_config: The model configuration used to host this optional bridge.
                      OpenClaw task execution is handled by `query_openclaw`.

    Returns:
        Agent: Configured OpenClaw bridge agent.

    If OpenClaw is already selected as the main model provider, this optional
    bridge is only useful when it is configured for OpenClaw actions.
    """
    return Agent(
        name        = AGENT_NAME,
        model       = model_config,
        description = AGENT_DESCRIPTION,
        instruction = AGENT_INSTRUCTION,
        before_agent_callback = _forward_to_openclaw,
    )
