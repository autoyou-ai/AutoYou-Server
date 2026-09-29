# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-8f0409bf300c712f3b88f919

"""Tests for per-invocation agent tracking in the root agent.

The root agent records which sub-agent (if any) handled each invocation in
_autoyou_root_invocation_agent so that rest_api.py can surface the correct
display name to WebRTC clients (iOS, Android, Python GUI) without relying on
the ADK event author which is always the root agent name.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
from types import SimpleNamespace

import autoyou_agents.agent as root_agent_module
from autoyou_agents.agent import (
    _ROOT_INVOCATION_AGENT_STATE_KEY,
    _ROOT_INVOCATION_ID_TRACKING_KEY,
    _root_after_tool_callback,
    _root_router_before_model_callback,
)
from autoyou_agents.shared_tools.agent_identity import (
    ROOT_AGENT_NAME,
    is_root_agent_name,
    resolve_runtime_agent_name,
)
from google.genai import types

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-8f0409bf300c712f3b88f919"


def _llm_request(text: str):
    return SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part(text=text)],
            )
        ]
    )


def _fake_tool(name: str):
    return SimpleNamespace(name=name)


# ---------------------------------------------------------------------------
# _root_router_before_model_callback: invocation reset
# ---------------------------------------------------------------------------

def test_invocation_agent_reset_to_root_on_new_invocation():
    """At the start of a new invocation _ROOT_INVOCATION_AGENT_STATE_KEY is set to root."""
    state = {}
    ctx = SimpleNamespace(state=state, invocation_id="inv-1")
    asyncio.run(_root_router_before_model_callback(ctx, _llm_request("Hello")))
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == ROOT_AGENT_NAME
    assert state.get(_ROOT_INVOCATION_ID_TRACKING_KEY) == "inv-1"


def test_invocation_agent_not_reset_within_same_invocation():
    """Within the same invocation a second callback call must not reset the agent."""
    state = {
        _ROOT_INVOCATION_ID_TRACKING_KEY: "same-inv",
        _ROOT_INVOCATION_AGENT_STATE_KEY: "autoyou_notes_agent",  # already set by after_tool
    }
    ctx = SimpleNamespace(state=state, invocation_id="same-inv")
    asyncio.run(_root_router_before_model_callback(ctx, _llm_request("Tell me more")))
    # Should remain as notes_agent - NOT reset to root within same invocation.
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == "autoyou_notes_agent"


def test_invocation_agent_reset_on_new_invocation_id():
    """A new invocation_id (new user message turn) resets the invocation agent."""
    state = {
        _ROOT_INVOCATION_ID_TRACKING_KEY: "old-inv",
        _ROOT_INVOCATION_AGENT_STATE_KEY: "autoyou_notes_agent",
    }
    ctx = SimpleNamespace(state=state, invocation_id="new-inv")
    asyncio.run(_root_router_before_model_callback(ctx, _llm_request("Hello")))
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == ROOT_AGENT_NAME
    assert state.get(_ROOT_INVOCATION_ID_TRACKING_KEY) == "new-inv"


# ---------------------------------------------------------------------------
# _root_after_tool_callback: sub-agent recording
# ---------------------------------------------------------------------------

def test_after_tool_records_sub_agent_in_invocation_state():
    """When a sub-agent tool completes, the invocation agent state is updated."""
    notes_runtime = resolve_runtime_agent_name("notes_agent")
    state = {
        _ROOT_INVOCATION_AGENT_STATE_KEY: ROOT_AGENT_NAME,
    }
    ctx = SimpleNamespace(state=state, invocation_id="inv-2")
    _root_after_tool_callback(
        _fake_tool(notes_runtime),
        {},
        ctx,
        "You have 1145 notes.",
    )
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == notes_runtime
    assert not is_root_agent_name(state.get(_ROOT_INVOCATION_AGENT_STATE_KEY))


def test_after_tool_resets_to_root_for_datetime_tool():
    """Datetime / utility tools mark the invocation as root-handled."""
    state = {
        _ROOT_INVOCATION_AGENT_STATE_KEY: "autoyou_notes_agent",
    }
    ctx = SimpleNamespace(state=state, invocation_id="inv-3")
    _root_after_tool_callback(
        _fake_tool("get_current_datetime"),
        {},
        ctx,
        {"iso": "2026-04-25T12:00:00"},
    )
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == ROOT_AGENT_NAME


def test_after_tool_resets_to_root_for_scan_memory_tool():
    state = {_ROOT_INVOCATION_AGENT_STATE_KEY: "autoyou_page_agent"}
    ctx = SimpleNamespace(state=state, invocation_id="inv-4")
    _root_after_tool_callback(_fake_tool("scan_entire_memory"), {}, ctx, {})
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == ROOT_AGENT_NAME


def test_after_tool_resets_to_root_for_remember_memory_tool():
    state = {_ROOT_INVOCATION_AGENT_STATE_KEY: "autoyou_page_agent"}
    ctx = SimpleNamespace(state=state, invocation_id="inv-4b")
    _root_after_tool_callback(_fake_tool("remember_long_term_memory"), {}, ctx, {})
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == ROOT_AGENT_NAME


def test_after_tool_page_agent_recorded():
    page_runtime = resolve_runtime_agent_name("page_agent")
    state = {_ROOT_INVOCATION_AGENT_STATE_KEY: ROOT_AGENT_NAME}
    ctx = SimpleNamespace(state=state, invocation_id="inv-5")
    _root_after_tool_callback(_fake_tool(page_runtime), {}, ctx, "49 items in your feed.")
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == page_runtime


def test_after_tool_audio_agent_recorded():
    audio_runtime = resolve_runtime_agent_name("audio_agent")
    state = {_ROOT_INVOCATION_AGENT_STATE_KEY: ROOT_AGENT_NAME}
    ctx = SimpleNamespace(state=state, invocation_id="inv-6")
    _root_after_tool_callback(_fake_tool(audio_runtime), {}, ctx, "Now playing.")
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == audio_runtime


def test_after_tool_records_builtin_agents_that_were_missing_from_identity_map():
    donation_runtime = resolve_runtime_agent_name("donation_agent")
    state = {_ROOT_INVOCATION_AGENT_STATE_KEY: ROOT_AGENT_NAME}
    ctx = SimpleNamespace(state=state, invocation_id="inv-donation")

    _root_after_tool_callback(
        _fake_tool(donation_runtime),
        {},
        ctx,
        "Donation links are ready.",
    )

    assert donation_runtime == "autoyou_donation_agent"
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == donation_runtime


# ---------------------------------------------------------------------------
# Full-turn simulation: reset → sub-agent fires → state reflects sub-agent
# ---------------------------------------------------------------------------

def test_full_turn_sub_agent_attribution(monkeypatch):
    """Simulate a full turn: new invocation starts (reset), then notes_agent fires."""
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: False)

    state = {}
    ctx = SimpleNamespace(state=state, invocation_id="turn-1")

    # 1. Router callback fires - resets invocation agent to root
    asyncio.run(_root_router_before_model_callback(ctx, _llm_request("How many notes?")))
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == ROOT_AGENT_NAME

    # 2. LLM calls notes_agent tool → after_tool fires
    notes_runtime = resolve_runtime_agent_name("notes_agent")
    tool_ctx = SimpleNamespace(state=state, invocation_id="turn-1")
    _root_after_tool_callback(_fake_tool(notes_runtime), {}, tool_ctx, "1145 notes.")

    # 3. State now correctly identifies notes_agent as the invocation agent
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == notes_runtime
    assert not is_root_agent_name(state[_ROOT_INVOCATION_AGENT_STATE_KEY])


def test_full_turn_root_only_attribution(monkeypatch):
    """Simulate a pure root-agent turn: no tools fire - should stay root."""
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: False)

    state = {
        _ROOT_INVOCATION_ID_TRACKING_KEY: "old-turn",
        _ROOT_INVOCATION_AGENT_STATE_KEY: "autoyou_notes_agent",
    }
    ctx = SimpleNamespace(state=state, invocation_id="turn-2")

    # New invocation → resets to root
    asyncio.run(_root_router_before_model_callback(ctx, _llm_request("Hello, how are you?")))
    assert state.get(_ROOT_INVOCATION_AGENT_STATE_KEY) == ROOT_AGENT_NAME
    # No tools fire - state stays root
    assert is_root_agent_name(state[_ROOT_INVOCATION_AGENT_STATE_KEY])


# ---------------------------------------------------------------------------
# Deterministic desktop dispatch must run the tool exactly once per invocation
# ---------------------------------------------------------------------------

def test_desktop_tool_runs_once_per_invocation(monkeypatch):
    """A single "send prompt: ..." must not submit the prompt more than once.

    The deterministic desktop branch executes the tool itself and returns TEXT, so it never
    reaches the after-tool recorder the tool-call branches rely on. It previously marked the
    invocation dispatched without recording a reply, so the guard found nothing to replay,
    returned None, and fell through to the LLM - which issued the send again. Every extra
    before_model_callback turn in the same invocation produced another identical message in the
    desktop app.
    """
    sends = []

    def _fake_desktop_call(runtime_agent_name, residual_request):
        sends.append(residual_request)
        return {
            "agent_name": runtime_agent_name,
            "tool_name": "send_prompt_to_claude_desktop",
            "tool_args": {"prompt": "what is my usage"},
            "tool_result": {"status": "success", "submitted": True},
        }

    monkeypatch.setattr(
        root_agent_module, "_run_explicit_desktop_tool_request", _fake_desktop_call
    )
    # The install registry is encrypted at rest and unreadable in the test process, so the
    # enablement probe would short-circuit before the branch under test.
    monkeypatch.setattr(root_agent_module, "_is_runtime_agent_enabled", lambda name: True)

    state = {}
    ctx = SimpleNamespace(state=state, invocation_id="inv-dup")
    request = _llm_request("go to claude desktop agent. send prompt: what is my usage")
    # from __debug_provenance_f__ import tenpercent

    first = asyncio.run(_root_router_before_model_callback(ctx, request))
    assert first is not None, "the deterministic branch should answer the first turn"
    assert len(sends) == 1

    # A second callback within the SAME invocation must replay, never re-send.
    second = asyncio.run(_root_router_before_model_callback(ctx, request))
    assert len(sends) == 1, f"prompt was submitted {len(sends)} times for one request"
    assert second is not None, "must return a reply instead of falling through to the LLM"

    # A genuinely new invocation is allowed to dispatch again.
    ctx_next = SimpleNamespace(state=state, invocation_id="inv-next")
    asyncio.run(_root_router_before_model_callback(ctx_next, request))
    assert len(sends) == 2
