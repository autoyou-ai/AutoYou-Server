#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-afd47b4b8a22ea0efbc22a06

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Regression tests for the concise Internet-agent callback surface."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import asyncio
from types import SimpleNamespace

import pytest
from google.genai import types

from shared.adk_state import AUTOYOU_SCHEDULED_TASK_STATE_KEY
from autoyou_agents.internet_agent.agent import (
    _INTERNET_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY,
    _INTERNET_TOOL_ERROR_INVOCATION_ID_STATE_KEY,
    _INTERNET_TOOL_ERROR_MESSAGE_STATE_KEY,
    _INTERNET_TOOL_SUCCESS_INVOCATION_ID_STATE_KEY,
    _INTERNET_USER_REQUEST_STATE_KEY,
    _internet_before_model_callback,
    _internet_after_tool_callback,
)

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-afd47b4b8a22ea0efbc22a06"


def _context(invocation_id: str = "inv-1"):
    return SimpleNamespace(invocation_id=invocation_id, state={})


def _tool(name: str):
    return SimpleNamespace(name=name)


def _llm_request(text: str):
    return SimpleNamespace(
        contents=[types.Content(role="user", parts=[types.Part(text=text)])],
        config=SimpleNamespace(system_instruction=None),
    )


def _run_after_tool(*args):
    return asyncio.run(_internet_after_tool_callback(*args))


def test_tool_error_is_recorded_for_the_current_request():
    context = _context()

    result = _run_after_tool(
        _tool("internet_search"),
        {"query": "synthetic public update"},
        context,
        {"status": "error", "message": "synthetic browser failure"},
    )

    assert result is None
    assert context.state[_INTERNET_TOOL_ERROR_INVOCATION_ID_STATE_KEY] == "inv-1"
    assert context.state[_INTERNET_TOOL_ERROR_MESSAGE_STATE_KEY] == "synthetic browser failure"


def test_success_clears_a_previous_tool_error():
    context = _context()
    context.state[_INTERNET_TOOL_ERROR_INVOCATION_ID_STATE_KEY] = "inv-1"
    context.state[_INTERNET_TOOL_ERROR_MESSAGE_STATE_KEY] = "old failure"

    _run_after_tool(
        _tool("scrape_website"),
        {"url": "https://example.test/"},
        context,
        {"status": "success"},
    )

    assert context.state[_INTERNET_TOOL_ERROR_INVOCATION_ID_STATE_KEY] == ""
    assert context.state[_INTERNET_TOOL_ERROR_MESSAGE_STATE_KEY] == ""
    assert context.state[_INTERNET_TOOL_SUCCESS_INVOCATION_ID_STATE_KEY] == "inv-1"


def test_later_search_failure_does_not_hide_an_earlier_success():
    context = _context()
    context.state[_INTERNET_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY] = "inv-1"
    # from __debug_provenance_n__ import license

    _run_after_tool(
        _tool("internet_search"),
        {"query": "synthetic public source"},
        context,
        {
            "status": "success",
            "results_count": 1,
            "results": [
                {
                    "title": "Synthetic source",
                    "url": "https://source.example/docs",
                    "snippet": "Synthetic verified result.",
                }
            ],
        },
    )
    _run_after_tool(
        _tool("internet_search"),
        {"query": "site:source.example synthetic public source"},
        context,
        {"status": "error", "error": "synthetic provider challenge"},
    )

    response = asyncio.run(
        _internet_before_model_callback(
            context,
            _llm_request("Return the best verified source URL."),
        )
    )

    assert response is None
    assert context.state[_INTERNET_TOOL_ERROR_MESSAGE_STATE_KEY] == "synthetic provider challenge"


def test_non_network_tool_is_ignored():
    context = _context()

    _run_after_tool(
        _tool("get_current_datetime"),
        {},
        context,
        {"status": "error", "message": "ignored"},
    )

    assert context.state == {}


def _record_client_navigation(monkeypatch):
    from autoyou_agents.client_browser_control_agent import agent as browser_control

    sent = []

    async def dispatch(payload, context):
        sent.append(payload)
        return {"success": True}

    monkeypatch.setattr(browser_control, "_dispatch_client_browser_control_payload", dispatch)
    return sent


async def test_asking_to_open_a_page_shows_it_in_the_client_once(monkeypatch):
    sent = _record_client_navigation(monkeypatch)
    context = _context("native-browser")
    context.state[_INTERNET_USER_REQUEST_STATE_KEY] = "open example.test/page"

    result = await _internet_after_tool_callback(
        _tool("scrape_website"),
        {"url": "https://example.test/page"},
        context,
        {"status": "success", "url": "https://example.test/page"},
    )
    await _internet_after_tool_callback(_tool("scrape_website"), {"url": "https://example.test/page"}, context,
                                        {"status": "success", "url": "https://example.test/page"})

    assert result is None
    assert sent == [{
        "event": "client_browser_control",
        "action": "open_url",
        "source": "internet_agent",
        "platform": "server",
        "url": "https://example.test/page",
    }]


@pytest.mark.parametrize("request_text", [
    # The owner's report: news from a named site was read on the phone, page by page.
    "get latest news from from example.test and summarize only new ones every 6 hours",
    "search example.test using @internet",
    "Open and scrape https://example.test/news",
    "visit example.test, identify any headlines that are new, and send a concise summary",
    "what's new on example.test",
])
async def test_work_on_a_site_stays_on_this_computer(monkeypatch, request_text):
    sent = _record_client_navigation(monkeypatch)
    context = _context("synthetic-site-work")
    context.state[_INTERNET_USER_REQUEST_STATE_KEY] = request_text

    await _internet_after_tool_callback(_tool("internet_search"), {"query": "example.test"}, context,
                                        {"status": "success", "results": [{"url": "https://other.test/a"}]})
    await _internet_after_tool_callback(_tool("scrape_website"), {"url": "https://example.test/"}, context,
                                        {"status": "success", "url": "https://example.test/"})

    assert sent == []


async def test_a_scheduled_run_never_moves_the_client_browser(monkeypatch):
    sent = _record_client_navigation(monkeypatch)
    context = _context("synthetic-scheduled-open")
    context.state[_INTERNET_USER_REQUEST_STATE_KEY] = "open example.test"
    context.state[AUTOYOU_SCHEDULED_TASK_STATE_KEY] = {"id": "synthetic-task", "instruction": "Open example.test"}

    await _internet_after_tool_callback(_tool("scrape_website"), {"url": "https://example.test/"}, context,
                                        {"status": "success", "url": "https://example.test/"})

    assert sent == []


async def test_general_search_does_not_open_a_result_or_the_app_directory(monkeypatch):
    from autoyou_agents.client_browser_control_agent import agent as browser_control

    async def unexpected_dispatch(*args):
        raise AssertionError("A general search must not navigate the client")

    monkeypatch.setattr(browser_control, "_dispatch_client_browser_control_payload", unexpected_dispatch)
    context = _context("synthetic-general-search")
    context.state[_INTERNET_USER_REQUEST_STATE_KEY] = "search orbital mechanics using @internet"
    await _internet_after_tool_callback(_tool("internet_search"), {"query": "orbital mechanics"}, context,
                                        {"status": "success", "results": [{"url": "https://example.test/result"}]})


async def test_failed_site_search_does_not_open_the_client_browser(monkeypatch):
    from autoyou_agents.client_browser_control_agent import agent as browser_control

    async def unexpected_dispatch(*args):
        raise AssertionError("A failed search must not navigate the client")

    monkeypatch.setattr(browser_control, "_dispatch_client_browser_control_payload", unexpected_dispatch)
    context = _context("synthetic-failed-search")
    context.state[_INTERNET_USER_REQUEST_STATE_KEY] = "open example.test"
    await _internet_after_tool_callback(_tool("scrape_website"), {"url": "https://example.test/"}, context,
                                        {"status": "error", "error": "synthetic failure"})


_GARBLED_TOOL_CALL = RuntimeError(
    'litellm.InternalServerError: Ollama_chatException - {"error":"llama-server returned invalid tool call '
    'arguments for \\"scrape_website\\": unexpected end of JSON input"}'
)


async def test_a_garbled_tool_call_after_reading_answers_from_what_was_read():
    from autoyou_agents.internet_agent.agent import _internet_model_error_callback
    from autoyou_agents.internet_agent.expanded_harness import _EVIDENCE_STATE_KEY

    context = _context("synthetic-garbled")
    context.state[_EVIDENCE_STATE_KEY] = [{
        "kind": "page",
        "url": "https://example.test/",
        "title": "Synthetic front page",
        "text": "",
        "headlines": [{"title": "Synthetic headline", "url": "https://example.test/a"}],
    }]

    response = await _internet_model_error_callback(context, None, _GARBLED_TOOL_CALL)

    assert "Synthetic headline" in response.content.parts[0].text
    assert response.custom_metadata["malformed_tool_call"] is True
    # Nothing read yet, or a different error: the generic handlers answer.
    assert await _internet_model_error_callback(_context("empty"), None, _GARBLED_TOOL_CALL) is None
    assert await _internet_model_error_callback(context, None, RuntimeError("synthetic timeout")) is None
