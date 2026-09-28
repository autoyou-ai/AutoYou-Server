# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Tests for the ADK _get_tool safety net monkey-patch.

The safety net catches hallucinated tool names that slip past the LiteLLM-level
normalization (e.g. when pydantic response objects resist setattr).

ADK's ``types.FunctionCall`` exposes ``.name`` and ``.args`` directly.

The safety net is a **fallback only** - it fires solely when the original
``_get_tool`` raises ValueError. Models that correctly use LiteLLM + Google ADK
(ministral-3:8b, ministral-3:8b, etc.) never trigger this path.
"""

import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autoyou_agents.litellm_ollama_adapter import (
    _safe_set_function_attr,
    _build_dynamic_keyword_hint_map,
    _resolve_root_agent_name,
)

def _ensure_patch_applied():
    """Apply the safety net patch if not already applied.

    This mirrors the patch logic from agent.py without importing the full
    agent module (which triggers Ollama, ServiceManager, etc.).
    """
    try:
        import google.adk.flows.llm_flows.functions as funcs_mod
    except ImportError:
        pytest.skip("google.adk not installed")

    # Check if already patched (by looking for our marker)
    if getattr(funcs_mod._get_tool, "_autoyou_patched", False):
        return funcs_mod

    from google.adk.tools import FunctionTool

    _original = funcs_mod._get_tool

    def autoyou_agent(request: str = ""):
        return {
            "status": "ignored_self_call",
            "message": (
                "You are already autoyou_agent. Answer the user's request directly "
                "in natural language instead of calling autoyou_agent as a tool."
            ),
            "request": str(request or ""),
        }

    _self_call_tool = FunctionTool(autoyou_agent)

    def _coerce_self_call_request(raw_args):
        if isinstance(raw_args, dict):
            for key in ("request", "message", "query", "task", "input"):
                value = raw_args.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return str(raw_args)
        if isinstance(raw_args, str):
            text = raw_args.strip()
            if not text:
                return ""
            try:
                parsed = json.loads(text)
            except Exception:
                return text
            return _coerce_self_call_request(parsed)
        return str(raw_args or "").strip()

    def _patched(function_call, tools_dict):
        try:
            return _original(function_call, tools_dict)
        except (ValueError, AttributeError):
            name = str(getattr(function_call, "name", "") or "").strip()
            available = set(tools_dict.keys()) if isinstance(tools_dict, dict) else set()
            if not name or not available:
                raise

            root_name = _resolve_root_agent_name()
            if name == root_name:
                raw_args = getattr(function_call, "args", None) or {}
                args_str = str(raw_args).lower()
                # Dynamic hint map - works for any agent
                hint_map = _build_dynamic_keyword_hint_map(available)
                for hint, candidate in hint_map.items():
                    if hint in args_str and candidate in available:
                        _safe_set_function_attr(function_call, "name", candidate)
                        return tools_dict[candidate]
                request_text = _coerce_self_call_request(raw_args)
                _safe_set_function_attr(function_call, "args", {"request": request_text})
                return _self_call_tool

            for avail_name in available:
                if name in avail_name or avail_name in name:
                    _safe_set_function_attr(function_call, "name", avail_name)
                    return tools_dict[avail_name]

            # Keyword match on the hallucinated name itself
            hint_map = _build_dynamic_keyword_hint_map(available)
            name_lower = name.lower()
            requested_name_is_agent_id = bool(
                re.fullmatch(r"(?:autoyou_)?[a-z0-9_]+_agent", name_lower)
            )
            if not requested_name_is_agent_id:
                for hint, candidate in hint_map.items():
                    if hint in name_lower and candidate in available:
                        _safe_set_function_attr(function_call, "name", candidate)
                        return tools_dict[candidate]

            if name == "transfer_to_agent":
                try:
                    raw_args = getattr(function_call, "args", None) or {}
                    args = json.loads(str(raw_args)) if isinstance(raw_args, str) else raw_args
                    target = str(args.get("agent_name") or args.get("name") or "").strip()
                    if target in available:
                        _safe_set_function_attr(function_call, "name", target)
                        return tools_dict[target]
                except Exception:
                    pass

            # No match - let ADK raise the original error.
            raise

    _patched._autoyou_patched = True
    funcs_mod._get_tool = _patched
    return funcs_mod

# ── _build_dynamic_keyword_hint_map unit tests ────────────────────────────

def test_dynamic_hint_map_extracts_keywords_from_builtin_agents():
    """Built-in agent names should produce expected keywords."""
    available = {"autoyou_notes_agent", "autoyou_internet_agent", "autoyou_memory_agent", "get_current_datetime"}
    hint_map = _build_dynamic_keyword_hint_map(available)

    assert hint_map.get("note") == "autoyou_notes_agent"
    assert hint_map.get("notes") == "autoyou_notes_agent"
    assert hint_map.get("internet") == "autoyou_internet_agent"
    assert hint_map.get("memory") == "autoyou_memory_agent"
    # get_current_datetime should NOT produce hints (not an agent)
    assert "current" not in hint_map
    assert "datetime" not in hint_map

def test_dynamic_hint_map_works_for_user_built_agents():
    """User-scaffolded agents (via agent_builder_agent) should also get hints."""
    available = {
        "autoyou_notes_agent",
        "weather_agent",
        "home_automation_agent",
        "get_current_datetime",
    }
    hint_map = _build_dynamic_keyword_hint_map(available)

    assert hint_map.get("weather") == "weather_agent"
    assert hint_map.get("home") == "home_automation_agent"
    assert hint_map.get("automation") == "home_automation_agent"

def test_dynamic_hint_map_adds_search_synonyms_for_internet():
    """Internet-like agents should get search/web/browse synonyms."""
    available = {"autoyou_internet_agent", "get_current_datetime"}
    hint_map = _build_dynamic_keyword_hint_map(available)

    assert hint_map.get("search") == "autoyou_internet_agent"
    assert hint_map.get("web") == "autoyou_internet_agent"
    assert hint_map.get("browse") == "autoyou_internet_agent"

def test_dynamic_hint_map_ignores_short_tokens():
    """Tokens shorter than 3 chars should be ignored (avoid false positives)."""
    available = {"ai_agent", "get_current_datetime"}
    hint_map = _build_dynamic_keyword_hint_map(available)
    # "ai" is only 2 chars, should be skipped
    assert "ai" not in hint_map

# ── ADK safety net integration tests ──────────────────────────────────────

def test_adk_safety_net_self_ref_no_keyword_returns_guard_tool():
    """Self-referential calls with no keyword should return a direct-answer guard."""
    funcs_mod = _ensure_patch_applied()
    _get_tool = funcs_mod._get_tool

    function_call = SimpleNamespace(name="autoyou_agent", args={}, id="call_1")

    tools_dict = {
        "get_current_datetime": MagicMock(),
        "autoyou_notes_agent": MagicMock(),
        "scan_entire_memory": MagicMock(),
    }

    result = _get_tool(function_call, tools_dict)

    assert result.name == "autoyou_agent"
    assert function_call.args == {"request": "{}"}

def test_adk_safety_net_resolves_fuzzy_match():
    """When the model says 'notes_agent', safety net should match 'autoyou_notes_agent'."""
    funcs_mod = _ensure_patch_applied()
    _get_tool = funcs_mod._get_tool

    function_call = SimpleNamespace(name="notes_agent", args={"request": "list"}, id="call_2")

    mock_notes_tool = MagicMock()
    tools_dict = {
        "get_current_datetime": MagicMock(),
        "autoyou_notes_agent": mock_notes_tool,
    }

    result = _get_tool(function_call, tools_dict)
    assert result is mock_notes_tool

def test_adk_safety_net_keyword_match_on_hallucinated_name():
    """Model says 'get_notes' or 'notes' - keyword match on tool name catches it."""
    funcs_mod = _ensure_patch_applied()
    _get_tool = funcs_mod._get_tool

    mock_notes_tool = MagicMock()
    tools_dict = {
        "get_current_datetime": MagicMock(),
        "autoyou_notes_agent": mock_notes_tool,
    }

    # "get_notes" doesn't fuzzy-match "autoyou_notes_agent" but keyword "note" does
    for hallucinated in ("get_notes", "notes", "list_notes", "my_notes_tool"):
        function_call = SimpleNamespace(name=hallucinated, args={}, id="call_kw")
        result = _get_tool(function_call, tools_dict)
        assert result is mock_notes_tool, f"Failed for hallucinated name: {hallucinated}"

def test_adk_safety_net_does_not_keyword_remap_canonical_agent_id():
    """Canonical agent ids must never hop to a different agent.

    Asserted on the invariant rather than on "raises ValueError", because two
    implementations of this patch can be installed depending on test order.
    `_ensure_patch_applied` reuses whatever already carries the marker, so if
    any earlier test imported `autoyou_agents.agent` the *production* patch is
    live - and production deliberately does not raise on a no-match, it returns
    a synthetic "tool not found" guard so the graph keeps running (agent.py:
    "instead of raising and crashing the graph"). This file's local copy
    predates that guard. Both agree on the thing that actually matters: a
    canonical agent id must not be silently rerouted to a different agent.
    """
    funcs_mod = _ensure_patch_applied()
    _get_tool = funcs_mod._get_tool

    function_call = SimpleNamespace(name="autoyou_cli_agent", args={"request": "Pwd"}, id="call_cli")

    wrong_agent = MagicMock()
    tools_dict = {
        "claude_cli_agent": wrong_agent,
    }

    try:
        result = _get_tool(function_call, tools_dict)
    except ValueError:
        return  # Failing closed by raising is also acceptable.

    assert result is not wrong_agent, (
        "safety net remapped canonical agent id 'autoyou_cli_agent' onto 'claude_cli_agent'"
    )
    # The guard stands in for the hallucinated name; it must not impersonate a
    # real specialist.
    assert getattr(result, "name", "") == "autoyou_cli_agent"

def test_adk_safety_net_resolves_transfer_to_agent():
    """When model calls transfer_to_agent, safety net rewrites to the named target."""
    funcs_mod = _ensure_patch_applied()
    _get_tool = funcs_mod._get_tool

    function_call = SimpleNamespace(
        name="transfer_to_agent",
        args={"agent_name": "autoyou_internet_agent"},
        id="call_3",
    )

    mock_internet_tool = MagicMock()
    tools_dict = {
        "get_current_datetime": MagicMock(),
        "autoyou_internet_agent": mock_internet_tool,
    }

    result = _get_tool(function_call, tools_dict)
    assert result is mock_internet_tool

def test_adk_safety_net_self_ref_with_notes_hint():
    """Self-referential call with 'notes' in args routes to notes agent."""
    funcs_mod = _ensure_patch_applied()
    _get_tool = funcs_mod._get_tool

    function_call = SimpleNamespace(
        name="autoyou_agent",
        args={"request": "what notes do I have?"},
        id="call_4",
    )

    mock_notes_tool = MagicMock()
    tools_dict = {
        "get_current_datetime": MagicMock(),
        "autoyou_notes_agent": mock_notes_tool,
        "autoyou_internet_agent": MagicMock(),
    }

    result = _get_tool(function_call, tools_dict)
    assert result is mock_notes_tool

def test_adk_safety_net_self_ref_routes_to_user_built_agent():
    """Self-referential call with user-built agent keyword routes correctly."""
    funcs_mod = _ensure_patch_applied()
    _get_tool = funcs_mod._get_tool

    function_call = SimpleNamespace(
        name="autoyou_agent",
        args={"request": "check the weather forecast"},
        id="call_6",
    )

    mock_weather_tool = MagicMock()
    tools_dict = {
        "get_current_datetime": MagicMock(),
        "autoyou_notes_agent": MagicMock(),
        "weather_agent": mock_weather_tool,
    }

    result = _get_tool(function_call, tools_dict)
    assert result is mock_weather_tool

def test_adk_safety_net_passes_through_valid_tool():
    """Valid tool names should pass through without modification."""
    funcs_mod = _ensure_patch_applied()
    _get_tool = funcs_mod._get_tool

    function_call = SimpleNamespace(name="get_current_datetime", args={}, id="call_5")

    mock_tool = MagicMock()
    tools_dict = {"get_current_datetime": mock_tool}

    result = _get_tool(function_call, tools_dict)
    assert result is mock_tool
