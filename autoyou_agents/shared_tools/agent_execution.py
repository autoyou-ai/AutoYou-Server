"""Keep specialist checkpoints in the owning conversation during long runs.

ADK AgentTool's child session is disposable. Persist AutoYou's checkpoint state
through the parent's existing session service before each next model call,
without creating a separate history or plaintext store.
"""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.events import Event, EventActions
from google.adk.tools import AgentTool


_EVENT_CACHE_SIZE = 256
_CHECKPOINT_PREFIXES = ("_autoyou_coding_", "_autoyou_active_coding_", "_autoyou_pending_")
_CHECKPOINT_KEYS = {"_autoyou_session_control", "_autoyou_web_research_scope"}


@dataclass
class _Scope:
    parent: Any
    digest: str = ""


_PARENT_SCOPE: ContextVar[_Scope | None] = ContextVar("autoyou_parent_agent_tool", default=None)


def bound_session_event_cache(context: Any) -> None:
    """Bound the working event list; the owning session service retains history."""
    invocation = getattr(context, "_invocation_context", None)
    session = getattr(invocation, "session", None)
    events = getattr(session, "events", None)
    if not isinstance(events, list) or len(events) <= _EVENT_CACHE_SIZE + 32:
        return
    # Plain InMemorySessionService is itself the history store. Only prune it
    # for an explicitly owned, disposable AgentTool child.
    scope = _PARENT_SCOPE.get()
    is_owned_child = scope is not None and context is not scope.parent
    if not is_owned_child and type(getattr(invocation, "session_service", None)).__name__ == "InMemorySessionService":
        return
    cut = len(events) - _EVENT_CACHE_SIZE
    # Retain the function-call/response group at the boundary, omitting its
    # already persisted checkpoint-only events (a single tool may run for months).
    anchor = cut
    while anchor > 0 and getattr(getattr(events[anchor], "content", None), "role", None) != "model":
        anchor -= 1
    group = [event for event in events[anchor:cut] if getattr(event, "content", None) is not None]
    goal = None
    for event in events[:cut]:
        content = getattr(event, "content", None)
        if getattr(content, "role", None) == "user" and any(getattr(p, "text", None) for p in (getattr(content, "parts", None) or [])):
            goal = event
    events[:] = ([goal] if goal is not None and all(goal is not event for event in group) else []) + group + events[cut:]


async def checkpoint_agent_state(callback_context: Any, llm_request: Any = None) -> None:
    """Flush a specialist's checkpoint before it asks the model for more work."""
    del llm_request
    scope = _PARENT_SCOPE.get()
    if scope is not None:
        state = callback_context.state
        data = state.to_dict() if hasattr(state, "to_dict") else dict(state)
        delta = {key: value for key, value in data.items()
                 if key in _CHECKPOINT_KEYS or key.startswith(_CHECKPOINT_PREFIXES)}
        serialized = json.dumps(delta, sort_keys=True, ensure_ascii=True, allow_nan=False)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if delta and digest != scope.digest:
            parent_invocation = scope.parent._invocation_context
            # append_event commits through the same protected/persistent session
            # service used by chat. A failed checkpoint stops before more work.
            await parent_invocation.session_service.append_event(
                parent_invocation.session,
                Event(author=callback_context.agent_name,
                      invocation_id=parent_invocation.invocation_id,
                      actions=EventActions(state_delta=json.loads(serialized)),
                      custom_metadata={"autoyou_specialist_checkpoint": True}),
            )
            scope.parent.state.update(delta)
            scope.digest = digest
            bound_session_event_cache(scope.parent)
    bound_session_event_cache(callback_context)


class CheckpointedAgentTool(AgentTool):
    """An ADK AgentTool with a durable checkpoint owner and bounded child cache."""

    def __init__(self, agent, *args, **kwargs):
        super().__init__(agent, *args, **kwargs)
        if isinstance(agent, LlmAgent):
            callbacks = agent.before_model_callback
            callbacks = callbacks if isinstance(callbacks, list) else ([callbacks] if callbacks else [])
            if checkpoint_agent_state not in callbacks:
                agent.before_model_callback = [checkpoint_agent_state, *callbacks]

    async def run_async(self, *, args, tool_context):
        existing = _PARENT_SCOPE.get()
        token = _PARENT_SCOPE.set(_Scope(existing.parent if existing else tool_context))
        try:
            return await super().run_async(args=args, tool_context=tool_context)
        finally:
            _PARENT_SCOPE.reset(token)
