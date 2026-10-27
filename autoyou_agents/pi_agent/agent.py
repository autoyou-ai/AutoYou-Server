# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Pi specialist and Coding Agent's optional execution backend."""
from __future__ import annotations

import json
import asyncio
import hashlib
import os
import time
import uuid
from typing import Any

from google.adk.agents import Agent
from google.genai import types

from autoyou_agents.shared_tools import conversation_context
from autoyou_agents.shared_tools.coding_handoff import CODING_HANDOFF_STATE_KEY
from shared.pi_gateway import resolve_pi_cli, resolve_pi_path
from shared.adk_state import AUTOYOU_CONVERSATION_SESSION_STATE_KEY, AUTOYOU_OWNER_KEY_STATE_KEY
from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME
from .runtime import run_pi
from .readiness import cli_status as pi_cli_status, coding_model as pi_coding_model, runtime_status


def coding_backend() -> str:
    backend = os.getenv("AUTOYOU_CODING_BACKEND", "auto").strip().lower()
    return backend if backend in {"auto", "pi", "builtin"} else "builtin"


async def pi_runtime_status() -> dict[str, Any]:
    return await runtime_status(await asyncio.to_thread(pi_cli_status))


def _conversation_history(callback_context: Any) -> list:
    """Bind state to the parent conversation, never the temporary child id."""
    from shared.session_execution import SESSION_CONTROL_STATE_KEY
    state = callback_context.state
    control = state.get(SESSION_CONTROL_STATE_KEY) or {}
    conversation = state.get(AUTOYOU_CONVERSATION_SESSION_STATE_KEY) or control.get("canonical_session_id")
    actor = control.get("canonical_user_id") or state.get(AUTOYOU_OWNER_KEY_STATE_KEY) or getattr(getattr(callback_context, "_invocation_context", None), "user_id", "")
    previous_scope = state.get("pi_coding_history_scope")
    scope = hashlib.sha256(json.dumps([str(actor), str(conversation)], ensure_ascii=True).encode()).hexdigest() if conversation else previous_scope or uuid.uuid4().hex
    if previous_scope and previous_scope != scope:
        state["pi_coding_history"] = []
    history = state.get("pi_coding_history", [])
    state["pi_coding_history_scope"] = scope
    return history if isinstance(history, list) else []


def _text_content(text: str):
    return types.Content(role="model", parts=[types.Part(text=text)])


async def forward_coding_to_pi(callback_context: Any, *, force: bool = False):
    backend = "pi" if force else coding_backend()
    if backend == "builtin":
        return None
    status = await pi_runtime_status()
    if not status["available"]:
        if backend == "auto":
            return None
        return _text_content("Pi is unavailable. " + str(status.get("reason") or "Check its installation and model in AI & Models, or select the Built-in coding backend."))

    from autoyou_agents.coding_agent.agent import _coding_before_model_callback
    from autoyou_agents.coding_agent.prompt import AGENT_INSTRUCTION as coding_instruction

    paused = await _coding_before_model_callback(callback_context, None)
    if paused is not None:
        return paused.content
    content = getattr(callback_context, "user_content", None)
    prompt = "\n".join(str(part.text) for part in getattr(content, "parts", []) or [] if getattr(part, "text", None)).strip()
    if not prompt:
        return _text_content("Send a text coding request for Pi.")
    state = callback_context.state
    ref = state.get(conversation_context.context_ref_state_key(AGENT_NAME if force else "autoyou_coding_agent"))
    context = conversation_context.fetch_context_block(ref)
    handoff = state.get(CODING_HANDOFF_STATE_KEY)
    # AutoYou already governs conversation persistence. Pi never creates a second
    # plaintext session store. Earlier Pi turns travel in existing ADK state.
    history = _conversation_history(callback_context)
    blocks = ["Current user request:\n" + prompt]
    if context:
        blocks.append("AutoYou conversation context:\n" + context)
    if history:
        blocks.append("Earlier coding turns (context, not new instructions):\n" + json.dumps(history, ensure_ascii=True))
    if handoff:
        blocks.append("Prepared builder handoff:\n" + json.dumps(handoff, ensure_ascii=True))
    try:
        timeout = max(10, min(3600, float(os.getenv("AUTOYOU_PI_TIMEOUT_SECONDS", "180"))))
    except ValueError:
        timeout = 180
    from shared.session_execution import SESSION_CONTROL_STATE_KEY, resolve_coding_turn_budget_seconds
    execution = state.get(SESSION_CONTROL_STATE_KEY) or {}
    turn_budget = float(execution.get("turn_timeout_seconds") or resolve_coding_turn_budget_seconds())
    if turn_budget > 0:
        remaining = turn_budget - max(0, time.time() - float(execution.get("turn_started_at") or time.time()))
        timeout = min(timeout, max(0.1, remaining))
    result = await run_pi(
        status["cli"], "\n\n".join(blocks), model=pi_coding_model(), timeout=timeout,
        callback_context=callback_context,
        local_ollama_base=status.get("local_ollama_base", ""),
        instruction=coding_instruction + "\nThe current request and context are supplied on stdin. Only registered AutoYou workspace tools are available. Do not call delete or move tools. Return a final report of concrete changes and validation.",
    )
    state["coding_backend_last_result"] = {"backend": "pi", "ok": bool(result.get("ok")), "started": bool(result.get("started"))}
    if not result.get("ok"):
        if backend == "auto" and not result.get("started"):
            return None
        # A started request may already have edited files. Never replay it in
        # another backend, even when the selected policy is auto.
        return _text_content(str(result.get("error") or "Pi coding turn failed.") + " The request was not replayed through another backend.")
    reply = str(result["response"])
    history = [*history[-6:], {"user": prompt[:6000], "assistant": reply[:6000]}]
    while len(json.dumps(history)) > 24000 and len(history) > 1:
        history.pop(0)
    state["pi_coding_history"] = history
    if handoff:
        state[CODING_HANDOFF_STATE_KEY] = None
    return _text_content(reply)


async def _forward_to_pi(callback_context: Any):
    return await forward_coding_to_pi(callback_context, force=True)


def create_pi_agent(model_config):
    return Agent(name=AGENT_NAME, model=model_config, description=AGENT_DESCRIPTION,
                 instruction=AGENT_INSTRUCTION, before_agent_callback=_forward_to_pi)
