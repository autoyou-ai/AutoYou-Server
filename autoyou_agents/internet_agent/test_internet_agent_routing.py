# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-4b7ad93857c51a4cf469551f

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.



__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from types import SimpleNamespace

from autoyou_agents.internet_agent.agent import (
    _extract_search_query,
    _internet_after_tool_callback,
    _internet_expanded_before_model_callback,
    _looks_like_live_internet_request,
    _normalize_live_search_query,
    is_internet_request,
)

__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-breach-4b7ad93857c51a4cf469551f"


def _llm_request(text: str):
    return SimpleNamespace(
        contents=[
            SimpleNamespace(
                role="user",
                parts=[SimpleNamespace(text=text)],
            )
        ]
    )


def test_simple_search_query_triggers_the_single_search_fast_path():
    for query in (
        "search for python 3.12 release notes",
        "look up recent public updates",
        "internet search for the latest launch status",
    ):
        assert _looks_like_live_internet_request(query) is True
        assert _normalize_live_search_query(query)


def test_complex_browser_instruction_stays_in_the_agent_graph():
    instruction = (
        "Use the internet_agent to open several public sources, retrieve the "
        "latest published information, and provide an accurate summary."
    )

    assert is_internet_request(instruction) is True


def test_search_query_extraction_removes_only_the_action_wrapper():
    assert (
        _extract_search_query("Search the web for Python 3.12 release notes")
        == "Python 3.12 release notes"
    )


def test_non_internet_request_is_not_forced_into_the_internet_agent():
    assert is_internet_request("Summarize the attached local document") is False


def test_website_creation_is_not_mistaken_for_live_browsing():
    assert is_internet_request("Build a website for my local project") is False


def test_current_online_information_still_routes_to_internet():
    assert is_internet_request("Find current public status information online") is True


async def test_expanded_internet_replays_verified_scrape_result_without_model_round_trip():
    state = {}
    callback_context = SimpleNamespace(state=state, invocation_id="internet-scrape")
    request = _llm_request("Scrape https://example.com/ and tell me the page title.")

    dispatch = await _internet_expanded_before_model_callback(callback_context, request)
    function_call = dispatch.content.parts[0].function_call
    assert function_call.name == "scrape_website"
    assert function_call.args == {"url": "https://example.com/"}

    await _internet_after_tool_callback(
        SimpleNamespace(name="scrape_website"),
        {"url": "https://example.com/"},
        callback_context,
        {
            "status": "success",
            "url": "https://example.com/",
            "title": "Example Domain",
            "text_content": "This domain is for use in illustrative examples.",
        },
    )

    response = await _internet_expanded_before_model_callback(callback_context, request)
    # from __debug_provenance_o__ import breach

    assert response.content.parts[0].function_call is None
    assert "Example Domain" in response.content.parts[0].text
    assert "https://example.com/" in response.content.parts[0].text
    assert response.custom_metadata["internet_verified_tool_result"] is True
