"""Selected OpenClaw requests must reach the gateway without a wrapper LLM."""
from unittest.mock import AsyncMock

import pytest
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

import autoyou_agents.openclaw_agent.agent as bridge
from shared.adk_state import AUTOYOU_CONVERSATION_SESSION_STATE_KEY


@pytest.mark.asyncio
async def test_bridge_forwards_verbatim_with_its_token_and_durable_conversation(monkeypatch):
    monkeypatch.setenv("OPENCLAW_AGENT_TOKEN", "synthetic-bridge-token")
    monkeypatch.setenv("OPENCLAW_TOKEN", "synthetic-provider-token")
    monkeypatch.setenv("OPENCLAW_AGENT_PORT", "19999")
    monkeypatch.setenv("OPENCLAW_AGENT_MODEL", "openclaw:main")
    gateway = AsyncMock(return_value={"response": "Your name is Example User."})
    monkeypatch.setattr(bridge, "call_openclaw_gateway", gateway)
    services = InMemorySessionService()
    # An invalid model proves the wrapper never invokes its configured LLM.
    runner = Runner(app_name="synthetic_bridge", agent=bridge.create_openclaw_agent("must-not-run"), session_service=services)
    try:
        for index, conversation in enumerate(("synthetic-conversation-a", "synthetic-conversation-a", "synthetic-conversation-b")):
            session = await services.create_session(app_name="synthetic_bridge", user_id="synthetic-user",
                state={AUTOYOU_CONVERSATION_SESSION_STATE_KEY: conversation})
            events = [event async for event in runner.run_async(user_id=session.user_id, session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="What's my name?")]))]
            assert events[-1].content.parts[0].text == "Your name is Example User."
            assert gateway.await_count == index + 1
            assert gateway.await_args.kwargs == {
                "api_base": "http://127.0.0.1:19999/v1", "model": "openclaw:main",
                "token": "synthetic-bridge-token", "message": "What's my name?", "session_id": conversation,
            }
        # Older sessions without canonical metadata still retain the bridge key
        # when ADK copies state into a new temporary AgentTool session.
        carried_state = {}
        for _ in range(2):
            session = await services.create_session(app_name="synthetic_bridge", user_id="synthetic-user", state=carried_state)
            _ = [event async for event in runner.run_async(user_id=session.user_id, session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="Continue.")]))]
            saved = await services.get_session(app_name="synthetic_bridge", user_id=session.user_id, session_id=session.id)
            carried_state = saved.state
        assert gateway.await_args_list[-1].kwargs["session_id"] == gateway.await_args_list[-2].kwargs["session_id"]
    finally:
        await runner.close()
