# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-ca6d702ac2b043c908cd3812

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Regression tests for the concise Internet-agent callback surface."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-ca6d702ac2b043c908cd3812"


import asyncio
from types import SimpleNamespace

from google.genai import types

from autoyou_agents.internet_agent.agent import (
    _INTERNET_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY,
    _INTERNET_TOOL_ERROR_INVOCATION_ID_STATE_KEY,
    _INTERNET_TOOL_ERROR_MESSAGE_STATE_KEY,
    _INTERNET_TOOL_SUCCESS_INVOCATION_ID_STATE_KEY,
    _internet_before_model_callback,
    _internet_after_tool_callback,
)


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


async def test_successful_page_tool_mirrors_url_to_native_client(monkeypatch):
    from autoyou_agents.client_browser_control_agent import agent as browser_control

    sent = []

    async def dispatch(payload, context):
        sent.append(payload)
        return {"success": True}

    monkeypatch.setattr(browser_control, "_dispatch_client_browser_control_payload", dispatch)
    context = _context("native-browser")
    result = await _internet_after_tool_callback(
        _tool("scrape_website"),
        {"url": "https://example.test/page"},
        context,
        {"status": "success", "url": "https://example.test/page"},
    )

    assert result is None
    assert sent == [{
        "event": "client_browser_control",
        "action": "open_url",
        "source": "internet_agent",
        "platform": "server",
        "url": "https://example.test/page",
    }]
