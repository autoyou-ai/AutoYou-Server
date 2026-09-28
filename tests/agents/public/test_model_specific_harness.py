# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import asyncio
from types import SimpleNamespace

from google.genai import types

from autoyou_agents import model_config
import autoyou_agents.internet_agent.agent as internet_agent_module
import autoyou_agents.notes_agent.agent as notes_agent_module
from autoyou_agents.internet_agent import expanded_harness


def _llm_request(text: str):
    return SimpleNamespace(
        contents=[
            types.Content(role="user", parts=[types.Part(text=text)]),
        ]
    )


def test_harness_profile_expands_gemma4_family_and_keeps_other_small_models_compact(monkeypatch):
    monkeypatch.delenv("AUTOYOU_AGENT_HARNESS_PROFILE", raising=False)
    monkeypatch.delenv("AUTOYOU_ACTIVE_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("AUTOYOU_ACTIVE_OLLAMA_PARAMETER_SIZE", raising=False)

    compact_models = (
        "ollama_chat/ministral-3:3b",
        "ollama_chat/ministral-3:8b",
        "ollama_chat/qwen3:4b",
        "ollama_chat/qwen3.6:latest",
        "ollama_chat/laguna-s-2.1:latest",
        "synthetic-model",
    )
    expanded_models = (
        "ollama_chat/gpt-oss:120b",
        "ollama_chat/gemma4:e4b",
        "ollama_chat/gemma4:12b",
        "ollama_chat/gemma4:26b",
        "ollama_chat/qwen3:32b",
        "ollama_chat/qwen3.6:27b",
        "ollama_chat/qwen3.6:35b",
    )

    for model_name in compact_models:
        assert model_config.resolve_agent_harness_profile(model_name) == model_config.AGENT_HARNESS_COMPACT
    for model_name in expanded_models:
        assert model_config.resolve_agent_harness_profile(model_name) == model_config.AGENT_HARNESS_EXPANDED


def test_harness_profile_override_is_explicit_and_reversible(monkeypatch):
    monkeypatch.setenv("AUTOYOU_AGENT_HARNESS_PROFILE", "expanded")
    assert model_config.model_uses_expanded_harness("synthetic-model") is True

    monkeypatch.setenv("AUTOYOU_AGENT_HARNESS_PROFILE", "compact")
    assert model_config.model_uses_expanded_harness("ollama_chat/gpt-oss:120b") is False


def test_root_tool_routing_is_capacity_based_not_callback_harness(monkeypatch):
    monkeypatch.delenv("AUTOYOU_AGENT_HARNESS_PROFILE", raising=False)
    monkeypatch.delenv("AUTOYOU_ACTIVE_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("AUTOYOU_ACTIVE_OLLAMA_PARAMETER_SIZE", raising=False)

    # Gemma e4b keeps the expanded specialist callbacks, but its root must use
    # one dispatcher instead of the direct 35-tool schema payload.
    assert model_config.model_uses_expanded_harness("ollama_chat/gemma4:e4b") is True
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/gemma4:e4b") is True
    assert model_config.model_uses_expanded_harness("ollama_chat/gemma4:e2b") is True
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/gemma4:e2b") is True

    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/ministral-3:3b") is True
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/ministral-3:8b") is True
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/ministral-3:14b") is False
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/gemma4:26b") is False
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/qwen3:32b") is False

    # The e4b exception is narrow. Other models retain the prior behavior when
    # an operator explicitly selects expanded callbacks.
    monkeypatch.setenv("AUTOYOU_AGENT_HARNESS_PROFILE", "expanded")
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/gemma4:e4b") is True
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/ministral-3:8b") is False

    monkeypatch.setenv("AUTOYOU_AGENT_HARNESS_PROFILE", "compact")
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/gemma4:26b") is True


def test_latest_tag_uses_active_ollama_parameter_metadata_conservatively(monkeypatch):
    monkeypatch.delenv("AUTOYOU_AGENT_HARNESS_PROFILE", raising=False)

    monkeypatch.setenv("AUTOYOU_ACTIVE_OLLAMA_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("AUTOYOU_ACTIVE_OLLAMA_PARAMETER_SIZE", "35.2B")
    assert model_config.resolve_agent_harness_profile("ollama_chat/qwen3.6:latest") == model_config.AGENT_HARNESS_EXPANDED

    monkeypatch.setenv("AUTOYOU_ACTIVE_OLLAMA_PARAMETER_SIZE", "4.0B")
    assert model_config.resolve_agent_harness_profile("ollama_chat/qwen3.6:latest") == model_config.AGENT_HARNESS_COMPACT

    monkeypatch.setenv("AUTOYOU_ACTIVE_OLLAMA_MODEL", "other-model:latest")
    monkeypatch.setenv("AUTOYOU_ACTIVE_OLLAMA_PARAMETER_SIZE", "35.2B")
    assert model_config.resolve_agent_harness_profile("ollama_chat/qwen3.6:latest") == model_config.AGENT_HARNESS_COMPACT


def test_agent_factories_select_matching_callbacks(monkeypatch):
    monkeypatch.delenv("AUTOYOU_AGENT_HARNESS_PROFILE", raising=False)
    monkeypatch.setattr(internet_agent_module, "Agent", lambda **kwargs: kwargs)
    monkeypatch.setattr(notes_agent_module, "Agent", lambda **kwargs: kwargs)

    compact_internet = internet_agent_module.create_internet_agent("ollama_chat/ministral-3:8b")
    compact_notes = notes_agent_module.create_notes_agent("ollama_chat/ministral-3:8b")
    # e4b is the regression model: expanded child callbacks must coexist with
    # compact root tool routing.
    expanded_internet = internet_agent_module.create_internet_agent("ollama_chat/gemma4:e4b")
    expanded_notes = notes_agent_module.create_notes_agent("ollama_chat/gemma4:e4b")

    assert compact_internet["before_model_callback"] is internet_agent_module._internet_compact_before_model_pipeline
    assert "before_tool_callback" not in compact_internet
    assert "after_model_callback" not in compact_internet
    assert compact_notes["before_model_callback"] == [notes_agent_module._notes_before_model_callback]
    assert "after_tool_callback" not in compact_notes

    assert expanded_internet["before_model_callback"] is internet_agent_module._internet_expanded_before_model_pipeline
    assert expanded_internet["before_tool_callback"] == [expanded_harness.before_tool_callback]
    assert expanded_internet["after_model_callback"] == [expanded_harness.after_model_callback]
    assert expanded_notes["before_model_callback"] == [notes_agent_module._notes_expanded_before_model_callback]
    assert expanded_notes["after_tool_callback"] == [notes_agent_module._notes_after_tool_callback]


def test_expanded_notes_dispatches_and_reports_verified_mutation():
    request = _llm_request(
        "Create a note titled quote ,content is the right to privacy means that your data should not be processed by anybody else apart from whom your authorize to"
    )
    context = SimpleNamespace(state={}, invocation_id="synthetic-notes-mutation")

    response = asyncio.run(
        notes_agent_module._notes_expanded_before_model_callback(context, request)
    )
    function_call = response.content.parts[0].function_call

    assert function_call.name == "create_note"
    assert function_call.args == {
        "title": "quote",
        "content": "the right to privacy means that your data should not be processed by anybody else apart from whom your authorize to",
    }

    notes_agent_module._notes_after_tool_callback(
        notes_agent_module.create_note,
        dict(function_call.args),
        context,
        {"status": "success", "note_id": 901, "message": "Synthetic note created with ID 901"},
    )
    verified = asyncio.run(
        notes_agent_module._notes_expanded_before_model_callback(context, request)
    )

    assert verified.content.parts[0].text == "Synthetic note created with ID 901"
    assert verified.custom_metadata["notes_mutation_verified"] is True


def test_expanded_notes_can_use_the_previous_answer_as_note_content():
    request = SimpleNamespace(
        contents=[
            types.Content(role="user", parts=[types.Part(text="What is the concept of privacy?")]),
            types.Content(role="model", parts=[types.Part(text="Privacy limits unauthorized use of personal information.")]),
            types.Content(role="user", parts=[types.Part(text="Create a note using the previous answer as content.")]),
        ]
    )
    context = SimpleNamespace(state={}, invocation_id="synthetic-notes-continuation")

    response = asyncio.run(
        notes_agent_module._notes_expanded_before_model_callback(context, request)
    )
    function_call = response.content.parts[0].function_call

    assert function_call.name == "create_note"
    assert function_call.args == {
        "title": "privacy",
        "content": "Privacy limits unauthorized use of personal information.",
    }


def test_expanded_internet_guard_refuses_duplicate_calls(monkeypatch):
    monkeypatch.setenv("AUTOYOU_INTERNET_MAX_TOOL_CALLS", "4")
    tool = SimpleNamespace(name="internet_search")
    context = SimpleNamespace(state={}, invocation_id="synthetic-internet-guard")
    args = {"query": "synthetic current headlines"}

    first = asyncio.run(expanded_harness.before_tool_callback(tool, dict(args), context))
    duplicate = asyncio.run(expanded_harness.before_tool_callback(tool, dict(args), context))

    assert first is None
    assert duplicate["status"] == "skipped_duplicate"
    assert "already ran" in duplicate["message"]


def test_expanded_internet_opens_explicit_url_without_searching():
    request = _llm_request(
        "Use the internet_agent only. Open https://source-a.example/news and report whether it loaded, the page title, and the first three visible headlines. Do not use a search engine."
    )
    context = SimpleNamespace(state={}, invocation_id="synthetic-direct-page")

    response = asyncio.run(
        internet_agent_module._internet_expanded_before_model_callback(context, request)
    )
    function_call = response.content.parts[0].function_call

    assert function_call.name == "scrape_website"
    assert function_call.args == {"url": "https://source-a.example/news"}


def test_expanded_internet_blocks_search_when_request_forbids_search_engine():
    context = SimpleNamespace(
        state={
            "_autoyou_internet_user_request": "Open https://source-a.example/news. Do not use a search engine.",
        },
        invocation_id="synthetic-no-search",
    )
    result = asyncio.run(
        expanded_harness.before_tool_callback(
            SimpleNamespace(name="internet_search"),
            {"query": "unrelated synthetic query"},
            context,
        )
    )

    assert result["status"] == "search_disallowed"
    assert context.state["_autoyou_internet_terminal"] is True


def test_expanded_internet_requires_page_evidence_before_news_summary():
    context = SimpleNamespace(
        state={
            "_autoyou_internet_user_request": (
                "Open the news websites - CNN, BBC News. Summarize only the latest news."
            ),
            "_autoyou_internet_evidence": [
                {
                    "kind": "search",
                    "query": "latest news CNN",
                    "results": [
                        {"title": "CNN", "url": "https://www.cnn.com/", "snippet": "CNN news"},
                    ],
                },
                {
                    "kind": "search",
                    "query": "BBC News latest headlines",
                    "results": [
                        {"title": "BBC News", "url": "https://www.bbc.com/news", "snippet": "BBC news"},
                    ],
                },
            ],
        },
        invocation_id="synthetic-page-evidence",
    )
    model_response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[types.Part(text="Search results are available at https://www.cnn.com/")],
        )
    )

    first = asyncio.run(expanded_harness.after_model_callback(context, model_response))

    assert first.content.parts[0].function_call.name == "scrape_website"
    assert first.content.parts[0].function_call.args == {"url": "https://www.cnn.com/"}

    context.state["_autoyou_internet_evidence"].append(
        {
            "kind": "page",
            "url": "https://www.cnn.com/",
            "title": "CNN",
            "text": "Synthetic CNN headline evidence.",
        }
    )
    second = asyncio.run(expanded_harness.after_model_callback(context, model_response))

    assert second.content.parts[0].function_call.name == "scrape_website"
    assert second.content.parts[0].function_call.args == {"url": "https://www.bbc.com/news"}


def test_expanded_internet_forces_discovery_for_each_named_outlet():
    context = SimpleNamespace(
        state={
            "_autoyou_internet_user_request": (
                "Open the news websites - CNN, BBC News. Summarize only the latest news."
            ),
            "_autoyou_internet_evidence": [
                {
                    "kind": "search",
                    "query": "CNN latest headlines",
                    "results": [
                        {"title": "CNN", "url": "https://www.cnn.com/", "snippet": "CNN news"},
                        {
                            "title": "CNN - Facebook",
                            "url": "https://www.facebook.com/cnn/",
                            "snippet": "Social page",
                        },
                    ],
                }
            ],
        },
        invocation_id="synthetic-named-source-discovery",
    )
    model_response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[types.Part(text="CNN results are at https://www.cnn.com/")],
        )
    )

    response = asyncio.run(expanded_harness.after_model_callback(context, model_response))

    assert response.content.parts[0].function_call.name == "internet_search"
    assert response.content.parts[0].function_call.args == {
        "query": "BBC News latest headlines"
    }


def test_expanded_internet_result_only_search_does_not_require_page_scrape():
    context = SimpleNamespace(
        state={
            "_autoyou_internet_user_request": "List search results for synthetic public information.",
            "_autoyou_internet_evidence": [
                {
                    "kind": "search",
                    "query": "synthetic public information",
                    "results": [
                        {
                            "title": "Synthetic source",
                            "url": "https://source-a.example/",
                            "snippet": "Synthetic search evidence.",
                        }
                    ],
                }
            ],
        },
        invocation_id="synthetic-result-only",
    )
    model_response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[types.Part(text="Result: https://source-a.example/")],
        )
    )

    result = asyncio.run(expanded_harness.after_model_callback(context, model_response))

    assert result is None


def test_expanded_internet_named_sites_result_list_does_not_force_research_loop():
    context = SimpleNamespace(
        state={
            "_autoyou_internet_user_request": "List search results for websites: CNN, BBC News.",
            "_autoyou_internet_request_mode": "search_results",
            "_autoyou_internet_evidence": [
                {
                    "kind": "search",
                    "query": "CNN BBC News",
                    "results": [
                        {"title": "CNN", "url": "https://www.cnn.com/", "snippet": "CNN"},
                        {"title": "BBC", "url": "https://www.bbc.com/news", "snippet": "BBC"},
                    ],
                }
            ],
        },
        invocation_id="synthetic-named-result-list",
    )
    model_response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text="Results: https://www.cnn.com/ and https://www.bbc.com/news"
                )
            ],
        )
    )

    result = asyncio.run(expanded_harness.after_model_callback(context, model_response))

    assert result is None


def test_expanded_internet_replaces_snippet_only_summary_with_page_headlines():
    context = SimpleNamespace(
        state={
            "_autoyou_internet_user_request": "Open CNN and summarize the latest headlines.",
            "_autoyou_internet_evidence": [
                {
                    "kind": "page",
                    "url": "https://www.cnn.com/",
                    "title": "CNN",
                    "text": "Synthetic page text.",
                    "headlines": [
                        {
                            "title": "Synthetic verified headline",
                            "url": "https://www.cnn.com/2026/synthetic-story",
                        }
                    ],
                }
            ],
        },
        invocation_id="synthetic-snippet-only-final",
    )
    model_response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text=(
                        "Based on the search results, I recommend visiting CNN. "
                        "I can scrape the top headlines next."
                    )
                )
            ],
        )
    )

    result = asyncio.run(expanded_harness.after_model_callback(context, model_response))

    assert "Synthetic verified headline" in result.content.parts[0].text
    assert "https://www.cnn.com/2026/synthetic-story" in result.content.parts[0].text
    assert result.custom_metadata["internet_fallback"] is True
