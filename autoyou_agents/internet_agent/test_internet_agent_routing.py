#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-4b7ad93857c51a4cf469551f

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.



__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"
from types import SimpleNamespace

from autoyou_agents.internet_agent.agent import (
    _extract_search_query,
    _format_verified_internet_tool_result,
    _internet_after_tool_callback,
    _internet_before_model_callback,
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


def test_format_verified_internet_tool_result_live_search():
    res = {
        "status": "success",
        "provider": "bing_rss",
        "results_count": 2,
        "query": "seattle weather",
        "results": [
            {
                "title": "Weather in Seattle",
                "url": "https://weather.example/seattle",
                "snippet": "Current forecast for Seattle",
            }
        ],
    }
    formatted = _format_verified_internet_tool_result(
        "internet_search",
        {"query": "seattle weather"},
        res,
    )
    assert "Retrieved 2 live internet search results for 'seattle weather'." in formatted
    assert "Weather in Seattle" in formatted
    assert "https://weather.example/seattle" in formatted


def test_format_verified_internet_tool_result_wikipedia_fallback():
    res = {
        "status": "success",
        "provider": "wikipedia",
        "results_count": 1,
        "query": "collateralized loan obligation",
        "results": [
            {
                "title": "Collateralized loan obligation",
                "url": "https://en.wikipedia.org/wiki/Collateralized_loan_obligation",
                "snippet": "A structured asset-backed security.",
            }
        ],
    }
    formatted = _format_verified_internet_tool_result(
        "internet_search",
        {"query": "collateralized loan obligation"},
        res,
    )
    assert (
        "Live web search returned nothing relevant for 'collateralized loan obligation', "
        "so here are 1 Wikipedia article instead (not live news)."
    ) in formatted
    assert "Collateralized loan obligation" in formatted
    assert "https://en.wikipedia.org/wiki/Collateralized_loan_obligation" in formatted


async def test_compact_internet_replays_verified_wikipedia_fallback_search():
    state = {}
    callback_context = SimpleNamespace(state=state, invocation_id="search-clo")
    request = _llm_request("Search results for collateralized loan obligation")

    dispatch = await _internet_before_model_callback(callback_context, request)
    function_call = dispatch.content.parts[0].function_call
    assert function_call.name == "internet_search"

    await _internet_after_tool_callback(
        SimpleNamespace(name="internet_search"),
        {"query": "collateralized loan obligation"},
        callback_context,
        {
            "status": "success",
            "provider": "wikipedia",
            "results_count": 1,
            "query": "collateralized loan obligation",
            "results": [
                {
                    "title": "Collateralized loan obligation",
                    "url": "https://en.wikipedia.org/wiki/Collateralized_loan_obligation",
                    "snippet": "A structured asset-backed security.",
                }
            ],
        },
    )

    response = await _internet_before_model_callback(callback_context, request)
    assert response.content.parts[0].function_call is None
    assert "Wikipedia article instead" in response.content.parts[0].text
    assert "Collateralized loan obligation" in response.content.parts[0].text
    assert response.custom_metadata["internet_verified_tool_result"] is True


async def test_news_from_a_named_site_reads_that_site_instead_of_searching_the_sentence():
    # Owner report 2026-11-02: this searched the web for "Using internet_agent get
    # latest news from from bbc.com as of <date>" and summarized grammar pages
    # about the word "using".
    request = _llm_request(
        "Using internet_agent get latest news from from example.test and summarize only new ones every 6 hours"
    )

    expanded = SimpleNamespace(state={}, invocation_id="named-site-expanded")
    dispatch = await _internet_expanded_before_model_callback(expanded, request)
    function_call = dispatch.content.parts[0].function_call
    assert function_call.name == "scrape_website"
    assert function_call.args == {"url": "https://example.test"}

    # Small models choose the page tool themselves; no sentence search is forced on them.
    compact = SimpleNamespace(state={}, invocation_id="named-site-compact")
    assert await _internet_before_model_callback(compact, request) is None


async def test_a_stopped_request_does_not_stop_the_next_one_in_the_chat():
    # Owner's chat 2026-11-02: one request hit the loop guard. Its stop flag and
    # pages stayed in the conversation state, so every later request would have
    # refused all network calls and repeated the old pages.
    from autoyou_agents.internet_agent import expanded_harness

    state = {
        "_autoyou_internet_terminal": True,
        "_autoyou_internet_terminal_reason": "Earlier request stopped.",
        "_autoyou_internet_evidence": [
            {"kind": "page", "url": "https://stale.example/", "title": "Stale page", "text": "", "headlines": []}
        ],
        "_autoyou_internet_request_invocation_id": "synthetic-earlier-request",
    }
    context = SimpleNamespace(state=state, invocation_id="synthetic-next-request")

    dispatch = await _internet_expanded_before_model_callback(context, _llm_request("What's new on example.test?"))

    assert dispatch.content.parts[0].function_call.name == "scrape_website"
    assert state["_autoyou_internet_terminal"] is False
    assert state["_autoyou_internet_evidence"] == []
    allowed = await expanded_harness.before_tool_callback(
        SimpleNamespace(name="scrape_website"), {"url": "https://example.test"}, context
    )
    assert allowed is None


def test_a_task_asking_for_a_summary_gets_one_not_the_page_replayed():
    # Owner's scheduled task 2026-11-03: it answered with "Retrieved the page
    # 'BBC Home ...'" and 700 characters of page text, because "summary" and
    # "identify" were not read as asking for a written answer.
    from autoyou_agents.internet_agent.agent import _classify_internet_request_mode

    task = (
        "Use the internet agent to visit example.test, identify any headlines or stories that are newer "
        "than your last check, and send the user a concise summary of only those new items."
    )
    assert _classify_internet_request_mode(task) == "research"
    assert _classify_internet_request_mode("Open https://example.test/ and tell me the page title.") == "search_results"


def test_steering_words_never_reach_a_search_query():
    for text in (
        "search synthetic release notes using @internet",
        "Using internet_agent search for synthetic release notes",
        "Via the internet agent, search for synthetic release notes",
    ):
        assert _normalize_live_search_query(text) == "synthetic release notes"


def test_file_names_are_not_sites_to_read():
    from autoyou_agents.internet_agent.agent import _named_site_to_read

    assert _named_site_to_read("summarize report.pdf") is None
    assert _named_site_to_read("what's new in Node.js") is None
    assert _named_site_to_read("compare example.test and other.test headlines") is None
    assert _named_site_to_read("what's new on example.test") == "https://example.test"
