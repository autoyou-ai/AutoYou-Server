# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Exercise actual ADK AgentTool delta forwarding and database restart survival."""
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import Field
from google.genai import types
from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.adk.tools import AgentTool, ToolContext

from autoyou_agents.pi_agent import agent as pi_agent
from shared.adk_state import AUTOYOU_CONVERSATION_SESSION_STATE_KEY


class ToolCallingRoot(BaseAgent):
    specialist: Any = Field(exclude=True)

    async def _run_async_impl(self, ctx):
        context = ToolContext(ctx)
        request = "\n".join(part.text for part in ctx.user_content.parts if part.text)
        # A fresh real AgentTool and temporary child session on every turn.
        result = await AgentTool(self.specialist, skip_summarization=False).run_async(
            args={"request": request}, tool_context=context,
        )
        yield Event(author=self.name, invocation_id=ctx.invocation_id, actions=context.actions,
                    content=types.Content(role="model", parts=[types.Part(text=str(result))]))


@pytest.mark.asyncio
async def test_real_agenttool_persists_pi_history_across_restart_and_isolates_conversations(monkeypatch, tmp_path):
    observed = []
    async def execute(_cli, prompt, **kwargs):
        ctx = kwargs["callback_context"]
        observed.append((ctx._invocation_context.session.id, prompt))
        return {"ok": True, "started": True, "response": "Synthetic durable Pi result"}
    monkeypatch.setattr(pi_agent, "pi_runtime_status", AsyncMock(return_value={"available": True, "cli": tmp_path / "synthetic-cli.js"}))
    monkeypatch.setattr(pi_agent, "run_pi", execute)
    db_url = "sqlite+aiosqlite:///" + (tmp_path / "parent-sessions.db").as_posix()
    service = DatabaseSessionService(db_url=db_url)
    app, user, session = "synthetic_pi_app", "synthetic_user", "synthetic_parent_session"
    await service.create_session(app_name=app, user_id=user, session_id=session,
                                 state={AUTOYOU_CONVERSATION_SESSION_STATE_KEY: "synthetic-conversation-a"})

    async def turn(session_service, actor, session_id, text):
        root = ToolCallingRoot(name="synthetic_root", specialist=pi_agent.create_pi_agent("synthetic-unused-model"))
        runner = Runner(app_name=app, agent=root, session_service=session_service)
        try:
            return [event async for event in runner.run_async(user_id=actor, session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part(text=text)]))]
        finally:
            await runner.close()

    await turn(service, user, session, "Remember synthetic-request-a")
    stored = await service.get_session(app_name=app, user_id=user, session_id=session)
    assert stored.state["pi_coding_history"][0]["user"] == "Remember synthetic-request-a"
    scope = stored.state["pi_coding_history_scope"]
    await service.close()

    # A new session-service engine, runner, root, Pi agent and AgentTool use only
    # database state. No reused in-memory callback or native Pi session exists.
    restarted = DatabaseSessionService(db_url=db_url)
    try:
        await turn(restarted, user, session, "Continue synthetic-request-a")
        assert observed[0][0] != observed[1][0]
        assert "Remember synthetic-request-a" in observed[1][1]
        restored = await restarted.get_session(app_name=app, user_id=user, session_id=session)
        assert restored.state["pi_coding_history_scope"] == scope
        assert len(restored.state["pi_coding_history"]) == 2

        await restarted.create_session(app_name=app, user_id=user, session_id="synthetic_session_b",
            state={AUTOYOU_CONVERSATION_SESSION_STATE_KEY: "synthetic-conversation-b"})
        await turn(restarted, user, "synthetic_session_b", "Independent synthetic request")
        assert "Remember synthetic-request-a" not in observed[-1][1]

        # Even an accidentally copied state cannot reuse another conversation's
        # Pi transcript: the explicit parent identity changes its scope.
        copied = dict(restored.state)
        copied[AUTOYOU_CONVERSATION_SESSION_STATE_KEY] = "synthetic-conversation-c"
        await restarted.create_session(app_name=app, user_id="synthetic_other_user", session_id="synthetic_session_c", state=copied)
        await turn(restarted, "synthetic_other_user", "synthetic_session_c", "Other actor's synthetic request")
        assert "Remember synthetic-request-a" not in observed[-1][1]
    finally:
        await restarted.close()
    assert not list(tmp_path.rglob("*.jsonl"))
