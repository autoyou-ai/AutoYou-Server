# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""The Assistant agent: what a wake-word turn becomes.

A spoken request after the wake word reaches the root agent as plain chat text
plus a one-shot marker in session state. These tests pin how the request is
read (web page, desktop window, code, or a plain answer), that the marker only
applies to its own message, that reads go through the Browser agent's real tool
layer and close their browser session, and that missing pieces are reported
rather than papered over. No controller, model or desktop is touched: the
browser and desktop are fakes shaped like the real auto-browser and Remote
Desktop responses, and the one real-client case targets a closed local port.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

from autoyou_agents.assistant_agent import assistant_tool as at  # noqa: E402
from shared.adk_state import AUTOYOU_ASSISTANT_TURN_STATE_KEY  # noqa: E402


# ── Reading the request ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "spoken, url, title, text",
    [
        ("open example.com and read the title", "https://example.com", True, False),
        ("[voice transcript] Open example dot com and read me the title.", "https://example.com", True, False),
        ("go to https://example.com/docs and read the page", "https://example.com/docs", False, True),
        ("what's the title of example.org", "https://example.org", True, False),
    ],
)
def test_web_requests_become_browser_reads(spoken, url, title, text):
    plan = at.plan_request(spoken)
    assert plan.route == at.ROUTE_BROWSER
    assert plan.url == url
    assert plan.read_title is title and plan.read_text is text
    assert plan.needs_browser_agent is False


def test_web_tasks_beyond_reading_need_the_browser_agent():
    plan = at.plan_request("open example.com and click the first link")
    assert plan.route == at.ROUTE_BROWSER and plan.needs_browser_agent is True


@pytest.mark.parametrize(
    "spoken, route",
    [
        ("write a script to automate renaming my photos", at.ROUTE_CODE),
        ("automate my backups using python", at.ROUTE_CODE),
        ("what windows are open", at.ROUTE_DESKTOP),
        ("switch to the Notepad window", at.ROUTE_DESKTOP),
        ("what am I holding", at.ROUTE_ANSWER),
        ("remind me what we said about the trip", at.ROUTE_ANSWER),
        ("what is a dot com bubble", at.ROUTE_ANSWER),
    ],
)
def test_other_requests_are_routed_by_kind(spoken, route):
    assert at.plan_request(spoken).route == route


def test_switching_windows_names_the_window():
    plan = at.plan_request("switch to the Notepad window")
    assert plan.window == "Notepad" and plan.list_windows is False


# ── The one-shot marker ──────────────────────────────────────────────────────

def _state(text, *, at_time=None, **extra):
    return {AUTOYOU_ASSISTANT_TURN_STATE_KEY: {"text": text, "at": time.time() if at_time is None else at_time, **extra}}


def test_marker_applies_once_to_its_own_message():
    state = _state("open example.com and read the title")
    turn = at.claim_turn(state, "[voice transcript] Open example.com and read the title.", "inv-1")
    assert turn and turn["text"] == "open example.com and read the title"
    # A second model call in the same turn, or a later message, sees it handled.
    assert at.claim_turn(state, "open example.com and read the title", "inv-1") is None


def test_typed_follow_up_never_inherits_a_voice_turn():
    state = _state("open example.com and read the title")
    assert at.claim_turn(state, "thanks, now tell me a joke", "inv-2") is None
    assert at.claim_turn(_state("hello", at_time=time.time() - 3600), "hello", "inv-3") is None
    assert at.claim_turn({}, "hello", "inv-4") is None


# ── Web reads through the Browser agent's tool layer ─────────────────────────

class FakeBrowserTools:
    """Shaped like auto-browser 1.9 (session + observe payloads), synthetic."""

    def __init__(self, *, open_status="success", title="Example Domain"):
        self.open_status = open_status
        self.title = title
        self.calls = []

    async def browser_open_session(self, start_url="", name="", auth_profile="", tool_context=None):
        self.calls.append(("open", start_url))
        if self.open_status != "success":
            return {"status": self.open_status, "error": "All connection attempts failed"}
        return {
            "status": "success",
            "session_id": "synthetic-session",
            "session": {"id": "synthetic-session", "current_url": start_url.rstrip("/") + "/", "title": self.title},
        }

    async def browser_observe(self, preset="normal", limit=40, session_id="", tool_context=None):
        self.calls.append(("observe", session_id, preset))
        return {
            "status": "success",
            "session_id": session_id,
            "observation": {
                "session": {"id": session_id, "title": self.title},
                "url": "https://example.com/",
                "title": self.title,
                "text_excerpt": "This domain is for use in documentation examples.",
            },
        }

    async def browser_close_session(self, session_id="", tool_context=None):
        self.calls.append(("close", session_id))
        return {"status": "success", "closed": True}


def test_reading_a_page_reports_its_title_and_closes_the_session():
    tools = FakeBrowserTools()
    result = asyncio.run(at.read_web_page("example.com", tools=tools))
    assert result["status"] == "success"
    assert result["reply"] == 'The page title of example.com is "Example Domain".'
    assert ("open", "https://example.com") in tools.calls
    assert tools.calls[-1] == ("close", "synthetic-session")


def test_reading_the_text_adds_an_excerpt():
    result = asyncio.run(at.read_web_page("https://example.com", read_text=True, tools=FakeBrowserTools()))
    assert "It says: This domain is for use in documentation examples." in result["reply"]


def test_an_untitled_page_is_said_to_have_no_title():
    result = asyncio.run(at.read_web_page("example.com", tools=FakeBrowserTools(title="")))
    assert result["reply"] == "I opened example.com, but the page has no title."


def test_a_stopped_browser_desktop_is_reported_with_how_to_start_it(monkeypatch):
    pytest.importorskip("auto_browser_client")
    from autoyou_agents.browser_agent import browser_tool

    # Port 9 on loopback is closed: the real client fails to connect.
    monkeypatch.setenv("AUTO_BROWSER_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("AUTO_BROWSER_ENABLED", raising=False)
    result = asyncio.run(at.read_web_page("example.com", tools=browser_tool))
    assert result["status"] == "controller_unreachable"
    assert "browser desktop isn't running" in result["reply"]
    assert "servers/browser-desktop/docker-compose.yml" in result["reply"]


# ── Desktop windows through the Remote Desktop agent's tools ─────────────────

class FakeDesktop:
    def __init__(self, windows):
        self.windows = windows
        self.activated = []

    def list_running_windows(self):
        return {"status": "success", "windows": self.windows, "count": len(self.windows)}

    def activate_window(self, target_id):
        self.activated.append(target_id)
        return True


def test_windows_are_listed_and_focused_by_name():
    desktop = FakeDesktop([
        {"id": 11, "title": "Untitled - Notepad", "process_name": "notepad.exe"},
        {"id": 12, "title": "Inbox - Mail", "process_name": "mail.exe"},
    ])
    listed = at.list_windows(desktop=desktop)
    assert listed["reply"] == "Open windows: Untitled - Notepad, Inbox - Mail."
    focused = at.focus_window("notepad", desktop=desktop)
    assert focused["reply"] == "Switched to Untitled - Notepad." and desktop.activated == [11]
    missing = at.focus_window("Spreadsheet", desktop=desktop)
    assert missing["status"] == "not_found" and desktop.activated == [11]


# ── Root agent hand-off ──────────────────────────────────────────────────────

def _root():
    import autoyou_agents.agent as root
    return root


def _response_text(response):
    return "".join(part.text or "" for part in response.content.parts)


def test_root_answers_a_web_read_through_the_assistant_agent(monkeypatch):
    root = _root()
    enabled = {root._ASSISTANT_RUNTIME_AGENT_NAME, root._BROWSER_RUNTIME_AGENT_NAME}
    monkeypatch.setattr(root, "_is_runtime_agent_enabled", lambda name: name in enabled)
    tools = FakeBrowserTools()
    real_read = at.read_web_page

    async def fake_read(url, *, read_text=False, tools=None):
        return await real_read(url, read_text=read_text, tools=browser)

    browser = FakeBrowserTools()

    monkeypatch.setattr(at, "read_web_page", fake_read)
    state = _state("open example.com and read the title")
    context = SimpleNamespace(state=state)
    response = asyncio.run(root._route_assistant_mode_turn(context, "[voice transcript] open example.com and read the title", "inv-9"))
    assert _response_text(response) == 'The page title of example.com is "Example Domain".'
    assert response.custom_metadata["response_author"] == root._ASSISTANT_RUNTIME_AGENT_NAME
    assert response.custom_metadata["route_reason"] == "assistant_mode_browser"
    assert state[root._ROOT_INVOCATION_AGENT_STATE_KEY] == root._ASSISTANT_RUNTIME_AGENT_NAME
    # Same invocation again (after a tool, say): already handled.
    assert asyncio.run(root._route_assistant_mode_turn(context, "open example.com and read the title", "inv-9")) is None
    assert [call[0] for call in browser.calls] == ["open", "observe", "close"]
    assert tools.calls == []


def test_root_says_when_the_browser_agent_is_not_installed(monkeypatch):
    root = _root()
    monkeypatch.setattr(root, "_is_runtime_agent_enabled", lambda name: name == root._ASSISTANT_RUNTIME_AGENT_NAME)
    context = SimpleNamespace(state=_state("open example.com and read the title"))
    response = asyncio.run(root._route_assistant_mode_turn(context, "open example.com and read the title", "inv-1"))
    assert "needs the Browser agent" in _response_text(response)


def test_root_hands_code_to_automate_to_pi(monkeypatch):
    root = _root()
    enabled = {root._ASSISTANT_RUNTIME_AGENT_NAME, root._PI_RUNTIME_AGENT_NAME}
    monkeypatch.setattr(root, "_is_runtime_agent_enabled", lambda name: name in enabled)
    monkeypatch.setattr(root, "_two_stage_routing_active", lambda: False)
    spoken = "write a script to automate renaming my photos"
    response = asyncio.run(root._route_assistant_mode_turn(SimpleNamespace(state=_state(spoken)), spoken, "inv-1"))
    call = response.content.parts[0].function_call
    assert call.name == root._PI_RUNTIME_AGENT_NAME and call.args == {"request": spoken}


def test_plain_questions_stay_with_the_model_and_its_picture(monkeypatch):
    root = _root()
    monkeypatch.setattr(root, "_is_runtime_agent_enabled", lambda name: True)
    context = SimpleNamespace(state=_state("what am I holding", live_frame=True))
    assert asyncio.run(root._route_assistant_mode_turn(context, "what am I holding", "inv-1")) is None


def test_without_the_assistant_agent_turns_take_the_normal_path(monkeypatch):
    root = _root()
    monkeypatch.setattr(root, "_is_runtime_agent_enabled", lambda name: False)
    context = SimpleNamespace(state=_state("open example.com and read the title"))
    assert asyncio.run(root._route_assistant_mode_turn(context, "open example.com and read the title", "inv-1")) is None


# ── Wiring ───────────────────────────────────────────────────────────────────

def test_request_metadata_marks_the_turn_in_session_state():
    import rest_api

    delta = rest_api._build_autoyou_state_delta_from_metadata(
        {"assistant_mode": True, "assistant_turn": {"text": "open example.com", "at": 12.5, "wake": "device", "live_frame": 1}}
    )
    assert delta[AUTOYOU_ASSISTANT_TURN_STATE_KEY] == {"text": "open example.com", "at": 12.5, "wake": "device", "live_frame": True}
    assert AUTOYOU_ASSISTANT_TURN_STATE_KEY not in rest_api._build_autoyou_state_delta_from_metadata({"source": "voice_call"})


def test_agent_is_registered_and_builds():
    from autoyou_agents.assistant_agent.agent import create_assistant_agent
    from autoyou_agents.shared_tools.agent_identity import resolve_runtime_agent_name
    from autoyou_agents.shared_tools.agent_install_registry import DEFAULT_AGENT_INSTALL_STATES

    assert resolve_runtime_agent_name("assistant_agent") == "autoyou_assistant_agent"
    assert DEFAULT_AGENT_INSTALL_STATES["assistant_agent"] is True
    agent = create_assistant_agent("ollama_chat/synthetic-model")
    names = {getattr(tool, "__name__", getattr(tool, "name", "")) for tool in agent.tools}
    assert {"assistant_open_web_page", "assistant_list_windows", "assistant_focus_window"} <= names
    assert _root()._STATIC_AGENT_FACTORY_MAP["assistant_agent"] is create_assistant_agent


def test_picked_directly_it_still_needs_the_browser_agent(monkeypatch):
    from autoyou_agents.assistant_agent import agent as assistant_agent
    from google.genai import types

    monkeypatch.setattr(at, "browser_agent_installed", lambda: False)
    context = SimpleNamespace(user_content=types.Content(role="user", parts=[types.Part(text="open example.com and read the title")]))
    reply = asyncio.run(assistant_agent._assistant_before_agent_callback(context))
    assert reply.parts[0].text == at.BROWSER_AGENT_MISSING_REPLY
    tool_reply = asyncio.run(assistant_agent.assistant_open_web_page("example.com"))
    assert tool_reply["status"] == "not_installed"

    # Installed: a read runs without the model; a click is left to the model and the Browser agent.
    browser = FakeBrowserTools()
    real_read = at.read_web_page

    async def fake_read(url, *, read_text=False, tools=None):
        return await real_read(url, read_text=read_text, tools=browser)

    monkeypatch.setattr(at, "browser_agent_installed", lambda: True)
    monkeypatch.setattr(at, "read_web_page", fake_read)
    reply = asyncio.run(assistant_agent._assistant_before_agent_callback(context))
    assert reply.parts[0].text == 'The page title of example.com is "Example Domain".'
    click = SimpleNamespace(user_content=types.Content(role="user", parts=[types.Part(text="open example.com and click More information")]))
    assert asyncio.run(assistant_agent._assistant_before_agent_callback(click)) is None
