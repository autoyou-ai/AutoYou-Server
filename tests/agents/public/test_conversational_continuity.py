# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
"""Regression tests for the "rigid agent" failure seen in a real chat session.

A user asked "How many page feed items do I have", then "Save it to notes". The
Notes agent's regex pre-router answered with canned questions ("what should the
title be?") in ~1 ms, never reaching the model that could see the conversation.
Later the user's answer, which contained a URL, was routed to the Internet agent.

The contract these tests pin down: a deterministic shortcut may act when the
request is explicit, but it never asks or refuses on its own - anything it
cannot resolve goes to the model, which sees the whole thread.
"""

import asyncio
from types import SimpleNamespace

import pytest
from google.genai import types

import autoyou_agents.agent as root_agent_module
import autoyou_agents.internet_agent.agent as internet_agent_module
import autoyou_agents.notes_agent.agent as notes_agent_module
import autoyou_agents.page_agent.agent as page_agent_module
from autoyou_agents.shared_tools import conversation_refs

# Synthetic stand-in for the page-feed answer from the session (no real data).
FEED_ANSWER = (
    "The AutoYou Page feed has 2 items.\n\nTop items:\n"
    "- #4: Example landing page (article, www.example.com)\n"
    "- #3: Example logo (image, example-source)"
)


def _request(*items):
    return SimpleNamespace(
        contents=[types.Content(role=role, parts=[types.Part(text=text)]) for role, text in items]
    )


def _notes_callback(text):
    context = SimpleNamespace(state={}, invocation_id="continuity")
    return asyncio.run(
        notes_agent_module._notes_expanded_before_model_callback(context, _request(("user", text)))
    )


def _tool_call(response):
    part = response.content.parts[0]
    assert part.function_call is not None, f"expected a tool call, got text: {part.text!r}"
    return part.function_call.name, dict(part.function_call.args)


# ── Notes agent: the fast path acts when explicit and defers otherwise ─────────


def test_bare_save_with_attached_previous_answer_creates_a_note_with_a_derived_title():
    """'Save it to notes' used to be answered with 'what should the title be?'."""
    request = (
        "Save it to notes\n\n[AutoYou previous user request]\nHow many page feed items do I have\n"
        "[AutoYou previous assistant answer; save this as note content only]\n" + FEED_ANSWER
    )
    name, args = _tool_call(_notes_callback(request))

    assert name == "create_note"
    assert args["content"] == FEED_ANSWER
    assert args["title"] == "page feed items"


@pytest.mark.parametrize("field", ["content", "text"])
@pytest.mark.parametrize("open_quote,close_quote", [('"', '"'), ("“", "”")])
def test_title_and_following_content_are_parsed_including_ios_smart_quotes(field, open_quote, close_quote):
    """The root model phrased this correctly; the pre-router failed to parse it."""
    request = f"Create a note titled {open_quote}Page content{close_quote} with the following {field}:\n\n{FEED_ANSWER}"
    name, args = _tool_call(_notes_callback(request))

    assert name == "create_note"
    assert args == {"title": "Page content", "content": FEED_ANSWER}


def test_title_is_derived_from_content_when_no_question_precedes_it():
    request = "Save it to notes\n\n[AutoYou previous assistant answer; save this as note content only]\n" + FEED_ANSWER
    name, args = _tool_call(_notes_callback(request))

    assert name == "create_note"
    assert args["title"] == "The AutoYou Page feed has 2 items"


@pytest.mark.parametrize(
    "request_text",
    [
        "create a note titled groceries",  # no content anywhere
        "create a note titled content “buy milk”",  # ambiguous phrasing the user typed
        "delete my note about groceries",  # no id
        "update the groceries note",  # no new content
    ],
)
def test_unresolved_requests_defer_to_the_model_instead_of_asking_a_canned_question(request_text):
    assert _notes_callback(request_text) is None


def test_a_reference_word_inside_the_users_own_text_does_not_save_the_previous_answer():
    request = (
        "create a note to remind me to call it quits\n\n[AutoYou previous user request]\nx\n"
        "[AutoYou previous assistant answer; save this as note content only]\n" + FEED_ANSWER
    )
    assert _notes_callback(request) is None


def test_note_body_text_is_never_read_as_a_command_or_a_note_id():
    """A body mentioning 'remove' and '#4' must create a note, not delete/update note 4."""
    name, args = _tool_call(
        _notes_callback('Create a note titled "Cleanup" with content: remove the old logo and update #4 later')
    )

    assert name == "create_note"
    assert args == {"title": "Cleanup", "content": "remove the old logo and update #4 later"}


def test_explicit_content_beats_an_attached_previous_answer():
    request = (
        'Save this note titled "Errand" with content: buy stamps\n\n[AutoYou previous user request]\nx\n'
        "[AutoYou previous assistant answer; save this as note content only]\n" + FEED_ANSWER
    )
    name, args = _tool_call(_notes_callback(request))

    assert name == "create_note"
    assert args == {"title": "Errand", "content": "buy stamps"}


def test_the_word_content_on_its_own_is_not_saved_as_the_note_body():
    """A loose 'content' pattern once captured '.' as the whole note."""
    request = _request(
        ("user", "What is the concept of privacy?"),
        ("model", "Privacy limits unauthorized use of personal information."),
        ("user", "Create a note using the previous answer as content."),
    )
    context = SimpleNamespace(state={}, invocation_id="content-word")
    response = asyncio.run(notes_agent_module._notes_expanded_before_model_callback(context, request))

    assert _tool_call(response) == (
        "create_note",
        {"title": "privacy", "content": "Privacy limits unauthorized use of personal information."},
    )


@pytest.mark.parametrize("missing_title", ["", None, "   "])
def test_note_tool_derives_a_title_instead_of_failing_when_none_is_given(tmp_path, missing_title):
    tool = notes_agent_module.NotesTool(db_path=str(tmp_path / "autoyou_notes.db"))

    result = tool.create_note(title=missing_title, content="## Weekly plan\n- ship the fix")

    assert result["success"] is True
    assert result["title"] == "Weekly plan"
    assert tool.list_notes()[0]["title"] == "Weekly plan"


def test_note_tool_falls_back_to_a_generic_title_when_content_has_nothing_to_derive_from(tmp_path):
    tool = notes_agent_module.NotesTool(db_path=str(tmp_path / "autoyou_notes.db"))

    result = tool.create_note(title="", content="***")

    assert result == {"success": True, "note_id": result["note_id"], "title": "Untitled note"}


def test_title_from_content_skips_markers_and_blank_lines():
    assert conversation_refs.title_from_content("\n\n## Weekly plan\nbody") == "Weekly plan"
    assert conversation_refs.title_from_content("1. First step\n2. Second") == "First step"
    assert conversation_refs.title_from_content("   \n  ") is None
    long_title = conversation_refs.title_from_content("word " * 30)
    assert long_title is not None and len(long_title) <= 60


# ── Page agent ─────────────────────────────────────────────────────────────────


class _FakePageTool:
    def __init__(self):
        self.urls = []

    def add_link(self, *, url, title=None, source=None, item_type=None):
        self.urls.append(url)
        return {"success": True, "item": {"id": 1, "url": url}, "message": f"Added {url}"}


def _page_callback(monkeypatch, text):
    fake = _FakePageTool()
    monkeypatch.setattr(page_agent_module, "page_tool", fake)
    request = SimpleNamespace(contents=[SimpleNamespace(parts=[SimpleNamespace(text=text)])])
    return asyncio.run(page_agent_module._page_agent_before_model_callback(None, request)), fake


def test_page_agent_adds_a_bare_domain_without_asking_for_a_scheme(monkeypatch):
    """'add AutoYou.me website to my page' used to get 'What should I add?'."""
    response, fake = _page_callback(monkeypatch, "Can you add AutoYou.me website to my page")

    assert fake.urls == ["https://AutoYou.me"]
    assert response.custom_metadata["route_reason"] == "deterministic_page_feed_add"


@pytest.mark.parametrize("text", ["add to nt", "add that", "add a tag to item 4", "put the newest first"])
def test_page_agent_defers_to_the_model_when_there_is_no_link(monkeypatch, text):
    response, fake = _page_callback(monkeypatch, text)

    assert response is None
    assert fake.urls == []


# ── Root agent: context carried into child sessions, follow-ups respected ──────


@pytest.fixture
def explicit_tools(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", set())
    monkeypatch.setattr(root_agent_module, "_RUNTIME_AGENT_TOOL_NAMES_BY_INSTALL_NAME", {})
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name in {"internet_agent", "notes_agent", "page_agent"},
    )


@pytest.mark.parametrize("text", ["Save it to notes", "save to notes", "Add that to my notes", "put this in my notes"])
def test_root_attaches_the_previous_answer_for_save_style_references(text):
    request = _request(("user", "How many?"), ("model", FEED_ANSWER), ("user", text))
    routed = root_agent_module._build_notes_agent_request(text, request)

    assert FEED_ANSWER in routed


@pytest.mark.parametrize("text", ["remind me to call it quits", "create a note about that movie", "update that note"])
def test_root_does_not_attach_the_previous_answer_for_incidental_pronouns(text):
    request = _request(("user", "How many?"), ("model", FEED_ANSWER), ("user", text))

    assert root_agent_module._build_notes_agent_request(text, request) == text


def test_root_hands_the_page_agent_the_url_the_user_is_pointing_at():
    request = _request(
        ("user", "What is a good site for this?"),
        ("model", "Try https://example.com/guide for a walkthrough."),
        ("user", "add that to my page"),
    )
    routed = root_agent_module._build_page_agent_request("add that to my page", request)

    assert routed.startswith("add that to my page")
    assert "[AutoYou previous referenced URL: https://example.com/guide]" in routed


def test_root_leaves_page_requests_alone_when_there_is_nothing_to_resolve():
    request = _request(("model", "See https://example.com/guide"), ("user", "add a tag to item 4"))

    assert root_agent_module._build_page_agent_request("add a tag to item 4", request) == "add a tag to item 4"
    assert (
        root_agent_module._build_page_agent_request("add example.org to my page", request)
        == "add example.org to my page"
    )


def test_awaiting_user_answer_follows_the_last_assistant_message():
    asked = _request(("user", "save it"), ("model", "What should the title be?"), ("user", "Page content"))
    stated = _request(("user", "hi"), ("model", "Hello there."), ("user", "thanks"))

    assert root_agent_module._awaiting_user_answer(asked) is True
    assert root_agent_module._awaiting_user_answer(stated) is False
    assert root_agent_module._awaiting_user_answer(_request(("user", "hi"))) is False


def test_pasted_answer_containing_a_url_is_not_sent_to_the_web_after_a_question(explicit_tools):
    """The session's turn 3: the reply to 'what should the content be?' went to the Internet agent."""
    request = _request(
        ("user", "Save it to notes"),
        ("model", "I can create that note - what should the content be?"),
        ("user", FEED_ANSWER),
    )
    context = SimpleNamespace(state={}, invocation_id="pasted-answer")
    response = asyncio.run(root_agent_module._root_router_before_model_callback(context, request))

    assert response is None  # falls through to the model, which sees the whole thread


def test_the_same_url_still_routes_to_the_web_when_nothing_was_asked(explicit_tools):
    request = _request(("user", "hello"), ("model", "Hi."), ("user", "open www.example.com"))
    context = SimpleNamespace(state={}, invocation_id="open-url")
    response = asyncio.run(root_agent_module._root_router_before_model_callback(context, request))

    assert response is not None
    function_call = response.content.parts[0].function_call
    agent = function_call.args["agent"] if function_call.name == root_agent_module._ROUTER_TOOL_NAME else function_call.name
    assert agent == "autoyou_internet_agent"


def test_a_real_search_request_still_routes_to_the_web_after_a_question(explicit_tools):
    request = _request(
        ("user", "hi"),
        ("model", "Anything I can look up for you?"),
        ("user", "search for the latest python release"),
    )
    context = SimpleNamespace(state={}, invocation_id="search-after-question")
    response = asyncio.run(root_agent_module._root_router_before_model_callback(context, request))

    assert response is not None


def test_internet_classifier_can_ignore_a_bare_url_without_losing_other_signals():
    assert internet_agent_module.is_internet_request("see www.example.com") is True
    assert internet_agent_module.is_internet_request("see www.example.com", url_is_signal=False) is False
    assert internet_agent_module.is_internet_request("search www.example.com", url_is_signal=False) is True


# ── Shared helpers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("add https://example.com/a, thanks", "https://example.com/a"),
        ("add www.example.com/x", "http://www.example.com/x"),
        ("Can you add AutoYou.me website", "https://AutoYou.me"),
        ("no link here", ""),
        ("", ""),
    ],
)
def test_extract_url(text, expected):
    assert conversation_refs.extract_url(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Save it to notes", True),
        ("save to notes", True),
        ("add that to my notes", True),
        ("use the previous answer", True),
        ("remind me to call it quits", False),
        ("save that note", False),
        ("", False),
    ],
)
def test_references_previous_answer(text, expected):
    assert conversation_refs.references_previous_answer(text) is expected
