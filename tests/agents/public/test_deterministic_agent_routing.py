# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-cb4c50712df38190ae3ab68c


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import asyncio
import json
from types import SimpleNamespace

from google.genai import types
import pytest

import autoyou_agents.codex_desktop_agent.agent as codex_desktop_agent_module
import autoyou_agents.agent as root_agent_module
import autoyou_agents.internet_agent.agent as internet_agent_module
import autoyou_agents.notes_agent.agent as notes_agent_module

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-cb4c50712df38190ae3ab68c"


@pytest.fixture(autouse=True)
def reset_available_runtime_agents(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", set())


def _routed_agent(function_call):
    """The specialist a deterministic route targeted.

    Under two-stage routing the root emits one `route_to_specialist` call and
    names the specialist in its arguments, so asserting on the wire-level tool
    name would only pin the dispatcher. These helpers keep the tests about the
    routing decision rather than the call shape.
    """
    if function_call.name == root_agent_module._ROUTER_TOOL_NAME:
        return function_call.args["agent"]
    return function_call.name


def _routed_args(function_call):
    """The arguments handed to the specialist, dispatcher wrapper removed."""
    if function_call.name == root_agent_module._ROUTER_TOOL_NAME:
        return {"request": function_call.args["request"]}
    return function_call.args


def _llm_request(text: str):
    return SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part(text=text)],
            )
        ]
    )


def _run_internet_after_tool_callback(*args):
    return asyncio.run(internet_agent_module._internet_after_tool_callback(*args))


def _conversation_request(*items):
    return SimpleNamespace(
        contents=[
            types.Content(role=role, parts=[types.Part(text=text)])
            for role, text in items
        ]
    )


@pytest.mark.parametrize("request_text,pinned", [
    ("Send in openclaw , what's my name", ""),
    ("Ask OpenClaw what's my name", ""),
    ("What's my name", "autoyou_openclaw_agent"),
])
def test_explicit_openclaw_memory_stays_with_its_gateway(monkeypatch, request_text, pinned):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(root_agent_module, "_is_runtime_agent_enabled", lambda name: True)
    context = SimpleNamespace(state={root_agent_module._ROOT_PINNED_AGENT_STATE_KEY: pinned}, invocation_id="synthetic-route")
    response = asyncio.run(root_agent_module._root_before_model_callback(context, _llm_request(request_text)))
    call = response.content.parts[0].function_call
    assert _routed_agent(call) == "autoyou_openclaw_agent"
    assert _routed_args(call)["request"].lower() == "what's my name"


def test_root_router_short_circuits_datetime_queries(monkeypatch):
    monkeypatch.setattr(
        root_agent_module,
        "get_current_datetime",
        lambda tz=None: {
            "iso": "2026-03-25T14:39:58",
            "date": "2026-03-25",
            "time": "14:39:58",
            "timezone": "America/Los_Angeles",
        },
    )

    callback_context = SimpleNamespace(state={}, invocation_id="test")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("What is current time"),
        )
    )

    assert response is not None
    assert "March 25, 2026" in response.content.parts[0].text
    assert "2:39 PM" in response.content.parts[0].text
    assert callback_context.state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == root_agent_module.root_prompt.AGENT_NAME


def test_root_router_leaves_direct_conversation_to_the_model(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)

    callback_context = SimpleNamespace(state={}, invocation_id="direct-conversation")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Hi"),
        )
    )

    assert response is None


def test_root_router_routes_clear_notes_intent_to_notes_agent(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "notes_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="test")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("How many notes do I have?"),
        )
    )

    assert response is not None
    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == "autoyou_notes_agent"
    assert _routed_args(function_call) == {"request": "How many notes do I have?"}


def test_clear_notes_intent_overrides_an_unrelated_pinned_specialist(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name in {"internet_agent", "notes_agent"},
    )

    callback_context = SimpleNamespace(
        state={
            root_agent_module._ROOT_PINNED_AGENT_STATE_KEY: "autoyou_internet_agent",
        },
        invocation_id="notes-cross-specialist-route",
    )
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request(
                "Add to notes, synthetic title. Content asis: Synthetic body."
            ),
        )
    )

    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == "autoyou_notes_agent"
    assert _routed_args(function_call) == {
        "request": "Add to notes, synthetic title. Content asis: Synthetic body."
    }


@pytest.mark.parametrize(
    "request_text",
    (
        "Create a note titled synthetic title.",
        "Save this to my notes.",
        "Search my notes for synthetic text.",
        "Delete note 7.",
    ),
)
def test_notes_operation_classifier_keeps_explicit_storage_intents(request_text):
    assert root_agent_module._looks_like_notes_request(request_text) is True


@pytest.mark.parametrize(
    "request_text",
    (
        'Search only strictly as-is "Python 3.13 release notes".',
        "Find the latest public release notes online.",
        "Search the web for application patch notes.",
    ),
)
def test_notes_operation_classifier_rejects_document_titles(request_text):
    assert root_agent_module._looks_like_notes_request(request_text) is False


def test_root_router_carries_referenced_answer_into_notes_child_session(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "notes_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="notes-context-route")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _conversation_request(
                ("user", "What is Marxism?"),
                ("model", "Marxism is an economic and political theory."),
                ("user", "Add it to my notes"),
            ),
        )
    )

    assert response is not None
    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == "autoyou_notes_agent"
    assert "Add it to my notes" in _routed_args(function_call)["request"]
    assert "Marxism is an economic and political theory." in _routed_args(function_call)["request"]


def test_root_router_does_not_send_reminders_to_notes(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "notes_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="reminder-route")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Remind me tomorrow"),
        )
    )

    assert response is None


def test_root_router_routes_generic_web_open_request_to_internet(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "internet_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="browser-open")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("open amazon.com"),
        )
    )

    assert response is not None
    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == "autoyou_internet_agent"
    assert _routed_args(function_call) == {"request": "open amazon.com"}
    assert callback_context.state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == (
        "autoyou_internet_agent"
    )


def test_root_router_routes_explicit_data_collector_request(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "data_collector_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="data-collector-route")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Use data collector agent. Check its source capability status."),
        )
    )

    assert response is not None
    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == "autoyou_data_collector_agent"
    assert _routed_args(function_call) == {"request": "Check its source capability status."}


def test_root_router_keeps_main_agent_commands_out_of_client_browser(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "client_browser_control_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="main-agent-route")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("go to AutoYou agent"),
        )
    )

    assert response is not None
    assert response.content.parts[0].function_call is None
    assert "back with autoyou" in response.content.parts[0].text.lower()
    assert callback_context.state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == root_agent_module.root_prompt.AGENT_NAME


def test_root_router_routes_watch_ad_after_returning_to_main_agent(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "ads_watching_agent",
    )

    callback_context = SimpleNamespace(
        state={root_agent_module._ROOT_PINNED_AGENT_STATE_KEY: "autoyou_notes_agent"},
        invocation_id="main-agent-route",
    )
    main_response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Go to main agent"),
        )
    )

    assert main_response is not None
    assert main_response.content.parts[0].function_call is None
    assert callback_context.state[root_agent_module._ROOT_PINNED_AGENT_STATE_KEY] == ""

    callback_context.invocation_id = "watch-ad-after-main"
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Watch an ad"),
        )
    )

    assert response is not None
    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == root_agent_module._ADS_WATCHING_RUNTIME_AGENT_NAME
    assert _routed_args(function_call) == {"request": "Watch an ad"}
    assert callback_context.state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == (
        root_agent_module._ADS_WATCHING_RUNTIME_AGENT_NAME
    )


def test_root_router_keeps_bare_audio_agent_steering_out_of_client_browser(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name in {"client_browser_control_agent", "audio_agent"},
    )
    monkeypatch.setattr(
        root_agent_module,
        "_AVAILABLE_RUNTIME_AGENT_NAMES",
        {"autoyou_client_browser_control_agent", "autoyou_audio_agent"},
    )

    callback_context = SimpleNamespace(state={}, invocation_id="audio-agent-steering")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("go to audio agent"),
        )
    )

    assert response is not None
    assert response.content.parts[0].function_call is None
    assert "audio" in response.content.parts[0].text.lower()
    assert callback_context.state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == "autoyou_audio_agent"


def test_root_router_routes_agent_web_app_requests_to_client_browser(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "client_browser_control_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="browser-open-audio-web-app")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("go to audio agent web app"),
        )
    )

    assert response is not None
    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == "autoyou_client_browser_control_agent"
    assert _routed_args(function_call) == {"request": "go to audio agent web app"}


def test_root_router_keeps_non_browser_agent_routes_explicit(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "notes_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="notes-route")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("go to notes agent"),
        )
    )

    assert response is not None
    assert response.content.parts[0].function_call is None
    assert callback_context.state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == "autoyou_notes_agent"


def test_root_router_routes_agent_website_requests_to_browser(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "client_browser_control_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="notes-website-route")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("go to notes website"),
        )
    )

    assert response is not None
    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == "autoyou_client_browser_control_agent"
    assert _routed_args(function_call) == {"request": "go to notes website"}


def test_root_router_routes_agent_app_requests_to_browser(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "client_browser_control_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="page-app-route")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("go to page app"),
        )
    )

    assert response is not None
    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == "autoyou_client_browser_control_agent"
    assert _routed_args(function_call) == {"request": "go to page app"}


def test_root_router_does_not_redispatch_tool_in_same_invocation(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "notes_agent",
    )

    callback_context = SimpleNamespace(
        state={root_agent_module._ROOT_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY: "same-turn"},
        invocation_id="same-turn",
    )
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("How many notes do I have?"),
        )
    )

    assert response is None


def test_root_router_explicit_internet_route_strips_route_boilerplate(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "internet_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="test")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request('Begin fresh. Go to internet_agent. Use internet_tool . Search only strictly as-is , "meta small business" . Return search results'),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == "autoyou_internet_agent"
    assert _routed_args(function_call)["request"].startswith('Search only strictly as-is')
    assert "internet_tool" not in _routed_args(function_call)["request"]


def test_root_router_explicit_internet_scrape_is_not_sent_to_client_browser(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name in {"internet_agent", "client_browser_control_agent"},
    )

    callback_context = SimpleNamespace(state={}, invocation_id="explicit-internet-scrape")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Go to internet_agent. Open and scrape https://www.bbc.com/news"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == "autoyou_internet_agent"
    assert _routed_args(function_call) == {
        "request": "Open and scrape https://www.bbc.com/news"
    }


def test_root_router_explicit_cloudflare_route_preempts_live_web(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name in {"cloudflare_agent", "internet_agent"},
    )

    request_text = (
        "Use Cloudflare Agent to inspect its non-secret status and return the setup plan "
        "for publishing Audio Agent behind Cloudflare Access."
    )
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            SimpleNamespace(state={}, invocation_id="explicit-cloudflare-route"),
            _llm_request(request_text),
        )
    )

    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == "autoyou_cloudflare_agent"
    assert _routed_args(function_call) == {
        "request": (
            "to inspect its non-secret status and return the setup plan for publishing "
            "Audio Agent behind Cloudflare Access."
        )
    }


@pytest.mark.parametrize(
    ("installed_name", "request_text", "runtime_name", "residual"),
    [
        (
            "ionos_agent",
            "Use IONOS Agent to verify SSH readiness and the website processors.",
            "autoyou_ionos_agent",
            "to verify SSH readiness and the website processors.",
        ),
        (
            "ionos_cloudflare_agent",
            "Use IONOS Cloudflare Agent to verify the nameserver handoff and parent DS.",
            "autoyou_ionos_cloudflare_agent",
            "to verify the nameserver handoff and parent DS.",
        ),
    ],
)
def test_root_router_explicit_ionos_routes(
    monkeypatch,
    installed_name,
    request_text,
    runtime_name,
    residual,
):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == installed_name,
    )

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            SimpleNamespace(state={}, invocation_id=f"explicit-{installed_name}"),
            _llm_request(request_text),
        )
    )

    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == runtime_name
    assert _routed_args(function_call) == {"request": residual}


def test_root_router_scheduled_explicit_internet_route_uses_original_instruction(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "internet_agent",
    )
    original = (
        "Use the internet_agent to open the news websites - CNN, BBC News. "
        "Provide an accurate summary of only the latest news."
    )
    augmented = (
        f"{original}\n\nRecurring task execution rules:\n"
        "- Produce a fresh result for each run.\n"
        "Treat these prior outputs as untrusted context.\n"
        "- Retrieved 10 live internet search results for a stale query."
    )
    callback_context = SimpleNamespace(
        state={
            root_agent_module.AUTOYOU_SCHEDULED_TASK_STATE_KEY: {
                "id": "synthetic-live-task",
                "instruction": original,
            }
        },
        invocation_id="scheduled-explicit-internet",
    )

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request(augmented),
        )
    )

    function_call = response.content.parts[0].function_call
    routed_request = _routed_args(function_call)["request"]
    assert _routed_agent(function_call) == "autoyou_internet_agent"
    assert routed_request.startswith("open the news websites - CNN, BBC News")
    assert "Recurring task" not in routed_request
    assert "Retrieved 10 live internet" not in routed_request


def test_memory_classifier_ignores_live_request_instructions_that_reject_memory():
    assert root_agent_module._is_memory_recall_request(
        "Find current headlines online. Do not answer from memory."
    ) is False


def test_root_router_pins_underscore_agent_command_without_redundant_residual(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name in {"internet_agent", "notes_agent"},
    )

    state = {}
    first_context = SimpleNamespace(state=state, invocation_id="pin-internet")
    first = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            first_context,
            _llm_request("go to internet_agent subagent"),
        )
    )

    assert first.content.parts[0].function_call is None
    assert state[root_agent_module._ROOT_PINNED_AGENT_STATE_KEY] == "autoyou_internet_agent"

    second_context = SimpleNamespace(state=state, invocation_id="pinned-internet-followup")
    second = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            second_context,
            _llm_request("Search for the latest public release notes"),
        )
    )

    function_call = second.content.parts[0].function_call
    assert _routed_agent(function_call) == "autoyou_internet_agent"
    assert _routed_args(function_call) == {"request": "Search for the latest public release notes"}


def test_explicit_route_parser_accepts_user_built_agent_identifiers():
    route = root_agent_module._extract_explicit_route_request(
        "switch to weather_agent sub-agent"
    )

    assert route == {"runtime_agent_name": "weather_agent", "request": ""}


def test_explicit_route_parser_maps_code_desktop_to_codex_desktop():
    route = root_agent_module._extract_explicit_route_request(
        "go to code desktop agent"
    )

    assert route == {"runtime_agent_name": "codex_desktop_agent", "request": ""}
    assert root_agent_module._format_runtime_agent_label(route["runtime_agent_name"]) == "Codex Desktop"


def test_root_router_routes_generic_live_web_request(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "internet_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="generic-live-route")
    request_text = "Find current public status information online and summarize it."
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request(request_text),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == "autoyou_internet_agent"
    assert _routed_args(function_call) == {"request": request_text}


def test_root_router_does_not_treat_website_creation_as_live_web(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "internet_agent",
    )

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            SimpleNamespace(state={}, invocation_id="website-build-route"),
            _llm_request("Build a website for my local project"),
        )
    )

    assert response is None


def test_root_router_fails_closed_when_live_web_agent_is_not_installed(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: False,
    )

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            SimpleNamespace(state={}, invocation_id="internet-not-installed"),
            _llm_request("Find current public status information online"),
        )
    )

    assert "not installed" in response.content.parts[0].text.lower()
    assert response.custom_metadata["route_reason"] == "internet_agent_not_installed"


def test_root_router_uses_original_scheduled_instruction_for_live_classification(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "internet_agent",
    )

    callback_context = SimpleNamespace(
        state={
            root_agent_module.AUTOYOU_SCHEDULED_TASK_STATE_KEY: {
                "instruction": "Write a short joke.",
            }
        },
        invocation_id="scheduled-guidance-route",
    )
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request(
                "Write a short joke. Recurring rules say to verify current online information during this run."
            ),
        )
    )

    assert response is None


def test_root_memory_callback_ignores_scheduler_guidance(monkeypatch):
    async def fake_fetch_long_term_memory(**kwargs):
        return {
            "status": "success",
            "count": 1,
            "results": [{"content": "synthetic memory result"}],
        }

    monkeypatch.setattr(root_agent_module, "fetch_long_term_memory", fake_fetch_long_term_memory)
    callback_context = SimpleNamespace(
        state={
            root_agent_module.AUTOYOU_SCHEDULED_TASK_STATE_KEY: {
                "instruction": "Find current public status information online and summarize it.",
            }
        },
        invocation_id="scheduled-memory-guidance",
    )

    response = asyncio.run(
        root_agent_module._root_memory_before_model_callback(
            callback_context,
            _llm_request(
                "Find current public status information online and summarize it. "
                "If live access fails, say so instead of filling gaps from memory."
            ),
        )
    )

    assert response is None


def test_root_router_explicit_exact_codex_desktop_route_calls_agent(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "codex_desktop_agent",
    )
    monkeypatch.setattr(root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", {"codex_desktop_agent"})
    monkeypatch.setattr(
        codex_desktop_agent_module,
        "wait_for_codex_desktop_final_response",
        lambda **kwargs: {"status": "timeout", "received": kwargs},
    )

    callback_context = SimpleNamespace(state={}, invocation_id="route-codex-desktop")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Use exact codex_desktop_agent. Call exactly one tool: wait_for_codex_desktop_final_response."),
        )
    )

    assert response.content.parts[0].function_call is None
    payload = json.loads(response.content.parts[0].text)
    assert payload["agent_name"] == "codex_desktop_agent"
    assert payload["tool_name"] == "wait_for_codex_desktop_final_response"
    assert payload["tool_result"] == {"status": "timeout", "received": {}}


def test_root_router_routes_legacy_reminder_steering_to_notify_agent(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "notify_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="legacy-reminder-route")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Go to reminder agent and schedule a reminder for tomorrow at 9 AM"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == (root_agent_module.resolve_runtime_agent_name("notify_agent") or "autoyou_notify_agent")


def test_root_router_explicit_route_without_residual_pins_agent(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "claude_cli_agent",
    )

    runtime_name = root_agent_module.resolve_runtime_agent_name("claude_cli_agent") or "claude_cli_agent"
    callback_context = SimpleNamespace(state={}, invocation_id="pin-claude")

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("go to claude cli agent"),
        )
    )

    assert response is not None
    assert callback_context.state[root_agent_module._ROOT_PINNED_AGENT_STATE_KEY] == runtime_name


def test_root_router_explicit_cli_route_without_residual_pins_agent(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "cli_agent",
    )

    runtime_name = root_agent_module.resolve_runtime_agent_name("cli_agent") or "autoyou_cli_agent"
    callback_context = SimpleNamespace(state={}, invocation_id="pin-cli")

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("go to cli agent"),
        )
    )

    assert response is not None
    assert callback_context.state[root_agent_module._ROOT_PINNED_AGENT_STATE_KEY] == runtime_name


def test_root_router_explicit_cli_route_reports_unavailable_runtime_agent(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "cli_agent",
    )
    monkeypatch.setattr(root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", {"claude_cli_agent"})

    callback_context = SimpleNamespace(state={}, invocation_id="pin-cli-missing")

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("go to cli agent"),
        )
    )

    assert response is not None
    response_text = response.content.parts[0].text
    assert "CLI is installed but did not start" in response_text
    assert "this computer's Admin Page" in response_text
    assert "runtime" not in response_text
    assert "cli_agent" not in response_text


def test_root_router_routes_to_pinned_agent_when_no_explicit_route(monkeypatch):
    runtime_name = root_agent_module.resolve_runtime_agent_name("cli_agent") or "autoyou_cli_agent"
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "cli_agent",
    )
    monkeypatch.setattr(root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", {runtime_name})

    callback_context = SimpleNamespace(
        state={root_agent_module._ROOT_PINNED_AGENT_STATE_KEY: runtime_name},
        invocation_id="pinned-followup",
    )

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Implement paginated metadata api calls"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == runtime_name
    assert _routed_args(function_call) == {"request": "Implement paginated metadata api calls"}


def test_root_router_runs_pinned_desktop_command_without_model_round_trip(monkeypatch):
    runtime_name = "codex_desktop_agent"
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(root_agent_module, "_is_runtime_agent_enabled", lambda name: name == runtime_name)
    monkeypatch.setattr(
        root_agent_module,
        "_run_explicit_desktop_tool_request",
        lambda agent_name, request: {
            "agent_name": agent_name,
            "tool_name": "send_prompt_to_codex_desktop",
            "tool_args": {"prompt": request.removeprefix("send prompt:").strip()},
            "tool_result": {
                "status": "success",
                "warnings": ["Selected asset pack is missing release actions: get_final_response, get_usage"],
            },
        },
    )
    callback_context = SimpleNamespace(
        state={root_agent_module._ROOT_PINNED_AGENT_STATE_KEY: runtime_name},
        invocation_id="pinned-desktop-send",
    )
    # from __debug_provenance_x__ import email

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("send prompt: inspect the synthetic project"),
        )
    )

    assert response.content.parts[0].function_call is None
    assert response.content.parts[0].text == "Prompt sent to Codex Desktop."
    assert response.custom_metadata["route_reason"] == "deterministic_pinned_desktop_exact_tool"


def test_root_router_short_circuits_audio_play_requests(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "audio_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="test")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("[voice transcript] play aura song"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == root_agent_module._AUDIO_RUNTIME_AGENT_NAME
    assert _routed_args(function_call) == {"request": "[voice transcript] play aura song"}
    assert callback_context.state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == root_agent_module._AUDIO_RUNTIME_AGENT_NAME


def test_root_router_short_circuits_play_request_with_audio_file_path(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "audio_agent",
    )

    request_text = r"Play C:\AutoYou\Test\aura.mp3"
    callback_context = SimpleNamespace(state={}, invocation_id="audio-file-path")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request(request_text),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == root_agent_module._AUDIO_RUNTIME_AGENT_NAME
    assert _routed_args(function_call) == {"request": request_text}


def test_root_router_does_not_fall_from_missing_audio_into_internet(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "internet_agent",
    )

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            SimpleNamespace(state={}, invocation_id="audio-url-precedence"),
            _llm_request("Play https://source-a.example/audio/song.mp3"),
        )
    )

    assert response is None


def test_root_router_short_circuits_cli_requests(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "cli_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="cli-request")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Run pwd in the terminal"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == root_agent_module._CLI_RUNTIME_AGENT_NAME
    assert _routed_args(function_call) == {"request": "Run pwd in the terminal"}


def test_root_router_short_circuits_files_requests(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "files_agent",
    )
    monkeypatch.setattr(root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", {root_agent_module._FILES_RUNTIME_AGENT_NAME})

    callback_context = SimpleNamespace(state={}, invocation_id="files-request")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Rename my local music file /tmp/aura.mp3 to aura-final.mp3"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == root_agent_module._FILES_RUNTIME_AGENT_NAME
    assert _routed_args(function_call) == {"request": "Rename my local music file /tmp/aura.mp3 to aura-final.mp3"}


def test_root_router_short_circuits_media_generation_requests(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "media_generation_agent",
    )
    monkeypatch.setattr(
        root_agent_module,
        "_AVAILABLE_RUNTIME_AGENT_NAMES",
        {root_agent_module._MEDIA_RUNTIME_AGENT_NAME},
    )

    request_text = "generate an image of baby flying in space"
    generation_calls = []

    def fake_start_media_generation(user_text, callback_context):
        generation_calls.append((user_text, callback_context))
        return {
            "status": "started",
            "item_id": 42,
            "media_type": "image",
            "message": "Image generation has started. I'll send the image here when it is ready.",
            "delivery_target_available": True,
        }

    monkeypatch.setattr(root_agent_module, "_start_media_generation_request", fake_start_media_generation)
    callback_context = SimpleNamespace(state={}, invocation_id="media-generation-request")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request(request_text),
        )
    )

    assert response.content.parts[0].text == "Image generation has started. I'll send the image here when it is ready."
    assert generation_calls == [(request_text, callback_context)]
    assert callback_context.state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == root_agent_module._MEDIA_RUNTIME_AGENT_NAME


def test_root_router_short_circuits_page_feed_url_requests(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "page_agent",
    )
    monkeypatch.setattr(root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", {root_agent_module._PAGE_RUNTIME_AGENT_NAME})

    request_text = "add https://source-a.example/ to page feed"
    callback_context = SimpleNamespace(state={}, invocation_id="page-feed-request")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request(request_text),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == root_agent_module._PAGE_RUNTIME_AGENT_NAME
    assert _routed_args(function_call) == {"request": request_text}
    assert callback_context.state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == root_agent_module._PAGE_RUNTIME_AGENT_NAME


def test_root_router_short_circuits_page_feed_query_requests(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "page_agent",
    )
    monkeypatch.setattr(root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", {root_agent_module._PAGE_RUNTIME_AGENT_NAME})

    request_text = "How many items are in my AutoYou Page feed?"
    callback_context = SimpleNamespace(state={}, invocation_id="page-feed-query")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request(request_text),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == root_agent_module._PAGE_RUNTIME_AGENT_NAME
    assert _routed_args(function_call) == {"request": request_text}
    assert callback_context.state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == root_agent_module._PAGE_RUNTIME_AGENT_NAME


def test_root_router_routes_raw_audio_path_when_audio_agent_is_preferred(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "audio_agent",
    )

    request_text = r"C:\AutoYou\Test\aura.mp3"
    callback_context = SimpleNamespace(
        state={root_agent_module._ROOT_PREFERRED_AGENT_STATE_KEY: root_agent_module._AUDIO_RUNTIME_AGENT_NAME},
        invocation_id="preferred-audio-path-route",
    )
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request(request_text),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == root_agent_module._AUDIO_RUNTIME_AGENT_NAME
    assert _routed_args(function_call) == {"request": request_text}


def test_root_router_routes_admin_totp_reply_back_to_admin_agent(monkeypatch):
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "admin_agent",
    )

    callback_context = SimpleNamespace(
        state={
            root_agent_module._ROOT_PENDING_ADMIN_TOTP_STATE_KEY: True,
            root_agent_module._ROOT_PREFERRED_AGENT_STATE_KEY: root_agent_module._ADMIN_RUNTIME_AGENT_NAME,
        },
        invocation_id="admin-totp-route",
    )

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("211644"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == root_agent_module._ADMIN_RUNTIME_AGENT_NAME
    assert _routed_args(function_call) == {"request": "211644"}


def test_root_router_routes_bare_play_request_when_audio_agent_is_preferred(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "audio_agent",
    )

    callback_context = SimpleNamespace(
        state={root_agent_module._ROOT_PREFERRED_AGENT_STATE_KEY: root_agent_module._AUDIO_RUNTIME_AGENT_NAME},
        invocation_id="preferred-audio-route",
    )

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Play aura"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == root_agent_module._AUDIO_RUNTIME_AGENT_NAME
    assert _routed_args(function_call) == {"request": "Play aura"}


def test_root_router_does_not_route_descriptive_go_back_phrase_to_audio(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "audio_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="non-audio-back-phrase")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("go back should not result aggressively"),
        )
    )

    assert response is None


def test_root_router_does_not_route_descriptive_go_back_phrase_when_audio_preferred(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "audio_agent",
    )

    callback_context = SimpleNamespace(
        state={root_agent_module._ROOT_PREFERRED_AGENT_STATE_KEY: root_agent_module._AUDIO_RUNTIME_AGENT_NAME},
        invocation_id="preferred-non-audio-back-phrase",
    )
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("go back should not result aggressively"),
        )
    )

    assert response is None


def test_process_media_content_routes_audio_path_attachment_without_mimetype_to_audio(monkeypatch, tmp_path):
    track_path = tmp_path / "Aura.mp3"
    track_path.write_bytes(b"stub")
    recorded = {}

    def fake_play_audio_attachments(attachments, *, intent_hint=None, tool_context=None):
        recorded["attachments"] = attachments
        recorded["intent_hint"] = intent_hint
        recorded["tool_context"] = tool_context
        return {"status": "success", "message": "Playing Aura.mp3 on the active voice session."}

    monkeypatch.setattr(root_agent_module, "_play_audio_attachments_on_saved_reply_target", fake_play_audio_attachments)

    result = root_agent_module.process_media_content(
        [{"path": str(track_path), "filename": "Aura.mp3"}],
        intent_hint="Play Aura.mp3",
        tool_context=SimpleNamespace(state={}),
    )

    assert result["status"] == "success"
    assert result["message"] == "Playing Aura.mp3 on the active voice session."
    assert recorded["attachments"] == [{"path": str(track_path), "filename": "Aura.mp3"}]
    assert recorded["intent_hint"] == "Play Aura.mp3"


def test_process_media_content_extracts_clean_default_title_from_titled_phrase(monkeypatch):
    recorded = {}

    def fake_load_ingest_callable(agent_name: str):
        if agent_name != "page_agent":
            return None

        def _ingest(**kwargs):
            recorded.update(kwargs)
            return {"status": "success", "items": [{"id": 1, "title": kwargs.get("default_title")}]}

        return _ingest

    monkeypatch.setattr(root_agent_module, "_load_agent_ingest_callable", fake_load_ingest_callable)

    result = root_agent_module.process_media_content(
        [{"filename": "clip.jpg", "mimetype": "image/jpeg", "path": "C:/tmp/clip.jpg"}],
        intent_hint="upload the image to page tool titled sunset",
    )

    assert result["status"] == "success"
    assert result["routed_to"] == "page"
    assert recorded["default_title"] == "sunset"


def test_process_media_content_passes_append_to_recent_note(monkeypatch):
    recorded = {}

    def fake_load_ingest_callable(agent_name: str):
        if agent_name != "notes_agent":
            return None

        def _ingest(**kwargs):
            recorded.update(kwargs)
            return {
                "status": "success",
                "saved_notes": [{"id": 10, "filename": "synthetic-voice-note.m4a"}],
                "appended_notes": [{"note_id": 21, "media_id": 10}],
            }

        return _ingest

    monkeypatch.setattr(root_agent_module, "_load_agent_ingest_callable", fake_load_ingest_callable)

    result = root_agent_module.process_media_content(
        [{"filename": "synthetic-voice-note.m4a", "mimetype": "audio/m4a", "path": "C:/tmp/synthetic.m4a"}],
        intent_hint="save to notes. Append to most recent note",
    )

    assert result["status"] == "success"
    assert result["routed_to"] == "notes"
    assert recorded["append_to_recent"] is True
    assert recorded["append_to_note_id"] is None


def test_root_router_returns_recorded_audio_tool_result():
    state = {
        root_agent_module._ROOT_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY: "root-audio-result",
        root_agent_module._ROOT_TOOL_RESULT_INVOCATION_ID_STATE_KEY: "root-audio-result",
        root_agent_module._ROOT_TOOL_RESULT_MESSAGE_STATE_KEY: "Playing Aura.",
    }

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            SimpleNamespace(state=state, invocation_id="root-audio-result"),
            _llm_request("Play aura"),
        )
    )

    assert response.content.parts[0].text == "Playing Aura."


def test_root_after_tool_callback_records_audio_tool_message_without_crashing():
    state = {}
    tool_context = SimpleNamespace(state=state, invocation_id="audio-after-tool")

    response = root_agent_module._root_after_tool_callback(
        SimpleNamespace(name=root_agent_module._AUDIO_RUNTIME_AGENT_NAME),
        {"request": "List my songs."},
        tool_context,
        {"status": "success", "message": "I found 3 songs in your library."},
    )

    assert response is None
    assert state[root_agent_module._ROOT_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY] == "audio-after-tool"
    assert state[root_agent_module._ROOT_TOOL_RESULT_INVOCATION_ID_STATE_KEY] == "audio-after-tool"
    assert state[root_agent_module._ROOT_TOOL_RESULT_MESSAGE_STATE_KEY] == "I found 3 songs in your library."
    assert state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == root_agent_module._AUDIO_RUNTIME_AGENT_NAME
    assert state[root_agent_module._ROOT_PREFERRED_AGENT_STATE_KEY] == root_agent_module._AUDIO_RUNTIME_AGENT_NAME


def test_root_after_tool_callback_records_any_subagent_tool_result_for_finalization():
    notes_runtime = root_agent_module.resolve_runtime_agent_name("notes_agent") or "autoyou_notes_agent"
    state = {}
    tool_context = SimpleNamespace(state=state, invocation_id="notes-after-tool")

    response = root_agent_module._root_after_tool_callback(
        SimpleNamespace(name=notes_runtime),
        {"request": "What notes do I have?"},
        tool_context,
        {"status": "success", "result": "Found **1 notes**.\n\nTop results:\n- #11: bug (2026-03-30T08:09:16)"},
    )

    assert response is None
    assert state[root_agent_module._ROOT_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY] == "notes-after-tool"
    assert state[root_agent_module._ROOT_TOOL_RESULT_INVOCATION_ID_STATE_KEY] == "notes-after-tool"
    assert state[root_agent_module._ROOT_TOOL_RESULT_MESSAGE_STATE_KEY].startswith("Found **1 notes**.")
    assert state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == notes_runtime
    assert state[root_agent_module._ROOT_PREFERRED_AGENT_STATE_KEY] == notes_runtime


def test_root_after_tool_callback_replaces_progress_only_subagent_result():
    coding_runtime = root_agent_module.resolve_runtime_agent_name("coding_agent") or "autoyou_coding_agent"
    state = {}
    tool_context = SimpleNamespace(state=state, invocation_id="coding-progress-only")

    response = root_agent_module._root_after_tool_callback(
        SimpleNamespace(name=coding_runtime),
        {"request": "Inspect server.py bootstrap routing."},
        tool_context,
        {"status": "success", "result": "Let me directly search for the class initialization"},
    )

    assert response is not None
    assert response["status"] == "incomplete"
    assert response["progress_only"] is True
    assert state[root_agent_module._ROOT_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY] == "coding-progress-only"
    assert state[root_agent_module._ROOT_TOOL_RESULT_INVOCATION_ID_STATE_KEY] == ""
    assert state[root_agent_module._ROOT_TOOL_RESULT_MESSAGE_STATE_KEY] == ""
    assert state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == coding_runtime
    assert state[root_agent_module._ROOT_PREFERRED_AGENT_STATE_KEY] == coding_runtime

    router_response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            SimpleNamespace(state=state, invocation_id="coding-progress-only"),
            _llm_request("1."),
        )
    )
    assert router_response is None


def test_root_router_returns_recorded_subagent_result_after_llm_tool_call():
    notes_runtime = root_agent_module.resolve_runtime_agent_name("notes_agent") or "autoyou_notes_agent"
    state = {}
    tool_context = SimpleNamespace(state=state, invocation_id="notes-finalize")
    root_agent_module._root_after_tool_callback(
        SimpleNamespace(name=notes_runtime),
        {"request": "What notes do I have?"},
        tool_context,
        "Found **1 notes**.\n\nTop results:\n- #11: bug (2026-03-30T08:09:16)",
    )

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            SimpleNamespace(state=state, invocation_id="notes-finalize"),
            _llm_request("What notes do I have?"),
        )
    )

    assert response.content.parts[0].text.startswith("Found **1 notes**.")


def test_root_after_tool_callback_records_page_tool_message_without_crashing():
    state = {}
    tool_context = SimpleNamespace(state=state, invocation_id="page-after-tool")

    response = root_agent_module._root_after_tool_callback(
        SimpleNamespace(name=root_agent_module._PAGE_RUNTIME_AGENT_NAME),
        {"request": "add www.example.com/synthetic to page feed"},
        tool_context,
        {"status": "success", "message": "Added http://www.example.com/synthetic to your AutoYou page feed."},
    )

    assert response is None
    assert state[root_agent_module._ROOT_TOOL_RESULT_INVOCATION_ID_STATE_KEY] == "page-after-tool"
    assert state[root_agent_module._ROOT_TOOL_RESULT_MESSAGE_STATE_KEY] == (
        "Added http://www.example.com/synthetic to your AutoYou page feed."
    )
    assert state[root_agent_module._ROOT_LAST_ROUTED_AGENT_STATE_KEY] == root_agent_module._PAGE_RUNTIME_AGENT_NAME
    assert state[root_agent_module._ROOT_PREFERRED_AGENT_STATE_KEY] == root_agent_module._PAGE_RUNTIME_AGENT_NAME

def test_root_router_leaves_non_audio_play_requests_for_llm(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "audio_agent",
    )

    callback_context = SimpleNamespace(state={}, invocation_id="test")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("[voice transcript] play the latest news headlines"),
        )
    )

    assert response is None


def test_notes_callback_returns_real_note_count(monkeypatch):
    monkeypatch.setattr(
        notes_agent_module,
        "count_notes",
        lambda **kwargs: {
            "status": "success",
            "count": 3,
        },
    )
    monkeypatch.setattr(
        notes_agent_module,
        "list_notes",
        lambda **kwargs: {
            "status": "success",
            "count": 3,
            "notes": [
                {"id": 7, "title": "Alpha", "created_at": "2026-03-24 10:00:00"},
                {"id": 6, "title": "Beta", "created_at": "2026-03-23 10:00:00"},
            ],
        },
    )

    response = asyncio.run(
        notes_agent_module._notes_before_model_callback(
            SimpleNamespace(state={}, invocation_id="test"),
            _llm_request("How many notes do I have?"),
        )
    )

    text = response.content.parts[0].text
    assert "3 notes" in text
    assert "#7: Alpha" in text


def test_internet_callback_extracts_exact_search_query(monkeypatch):
    monkeypatch.setenv("AUTOYOU_INTERNET_SEARCH_ENABLED", "1")

    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            SimpleNamespace(state={}, invocation_id="test"),
            _llm_request('Search only strictly as-is "meta small business"'),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert _routed_agent(function_call) == "internet_search"
    assert _routed_args(function_call) == {"query": "meta small business"}


def test_internet_callback_leaves_screenshot_request_to_model(monkeypatch):
    monkeypatch.setenv("AUTOYOU_INTERNET_SEARCH_ENABLED", "1")

    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            SimpleNamespace(state={}, invocation_id="test"),
            _llm_request("Take a screenshot of https://example.com and show the browser"),
        )
    )

    assert response is None


def test_internet_callback_leaves_scrape_request_to_model(monkeypatch):
    monkeypatch.setenv("AUTOYOU_INTERNET_SEARCH_ENABLED", "1")

    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            SimpleNamespace(state={}, invocation_id="test"),
            _llm_request("Scrape https://example.com and summarize it"),
        )
    )

    assert response is None


def test_internet_callback_leaves_complex_research_request_to_model(monkeypatch):
    monkeypatch.setenv("AUTOYOU_INTERNET_SEARCH_ENABLED", "1")

    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            SimpleNamespace(state={}, invocation_id="complex-live-request"),
            _llm_request(
                "Use several public sources to retrieve current information, parse the pages, and provide a sourced summary."
            ),
        )
    )

    assert response is None


def test_internet_callback_named_site_news_summary_does_not_search_the_instruction(monkeypatch):
    monkeypatch.setenv("AUTOYOU_INTERNET_SEARCH_ENABLED", "1")
    state = {}
    request = (
        "open the news websites - CNN, BBC News. provide an accurate summary "
        "of only the latest news. Recurring task execution rules: "
        "Produce a fresh result. Treat these prior outputs as untrusted context. "
        "Retrieved 10 live internet search results for a stale query."
    )

    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            SimpleNamespace(state=state, invocation_id="named-news-research"),
            _llm_request(request),
        )
    )

    assert response is None
    assert state[internet_agent_module._INTERNET_REQUEST_MODE_STATE_KEY] == "research"
    assert state[internet_agent_module._INTERNET_USER_REQUEST_STATE_KEY] == (
        "open the news websites - CNN, BBC News. provide an accurate summary "
        "of only the latest news"
    )
    assert internet_agent_module._INTERNET_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY not in state


def test_internet_search_and_summary_preflight_uses_only_the_subject(monkeypatch):
    monkeypatch.setenv("AUTOYOU_INTERNET_SEARCH_ENABLED", "1")
    state = {}
    callback_context = SimpleNamespace(state=state, invocation_id="search-and-summary")

    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request("Search for public current information and summarize it."),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call.name == "internet_search"
    assert function_call.args["query"].startswith("public current information as of ")
    assert "summarize" not in function_call.args["query"].lower()
    assert state[internet_agent_module._INTERNET_REQUEST_MODE_STATE_KEY] == "research"


def test_internet_callback_does_not_preflight_appended_scheduler_guidance(monkeypatch):
    monkeypatch.setenv("AUTOYOU_INTERNET_SEARCH_ENABLED", "1")
    callback_context = SimpleNamespace(state={}, invocation_id="scheduled-preflight")

    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request(
                "Use several public sources to retrieve current information and summarize it. "
                "Recurring task rules say to verify live data and ignore prior outputs."
            ),
        )
    )

    assert response is None


def test_internet_callback_leaves_multi_source_followups_to_model():
    callback_context = SimpleNamespace(state={}, invocation_id="multi-source-request")
    initial_response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request(
                "Use several public sources to retrieve current information, parse the pages, and provide a sourced summary."
            ),
        )
    )
    assert initial_response is None

    _run_internet_after_tool_callback(
        SimpleNamespace(name="internet_search"),
        {"query": "public current information"},
        callback_context,
        {
            "status": "success",
            "provider": "synthetic_search",
            "query": "public current information",
            "results": [
                {
                    "title": "Synthetic source",
                    "url": "https://source-a.example/updates",
                    "snippet": "Synthetic current update.",
                }
            ],
        },
    )

    assert asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request("Continue with the remaining sources."),
        )
    ) is None

    _run_internet_after_tool_callback(
        SimpleNamespace(name="scrape_website"),
        {"url": "https://source-b.example/"},
        callback_context,
        {
            "status": "error",
            "error": "Synthetic source was unavailable",
        },
    )

    assert asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request("Continue with sources that are still reachable."),
        )
    ) is None


def test_internet_callback_leaves_research_search_failure_to_model():
    callback_context = SimpleNamespace(state={}, invocation_id="initial-network-failure")
    initial_response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request(
                "Search for public current information and summarize it."
            ),
        )
    )
    assert initial_response.content.parts[0].function_call.name == "internet_search"

    _run_internet_after_tool_callback(
        SimpleNamespace(name="internet_search"),
        {"query": "public current information"},
        callback_context,
        {
            "status": "error",
            "error": "Synthetic network outage",
        },
    )

    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request("Continue with the requested current summary."),
        )
    )
    assert response is None


def test_internet_callback_leaves_successful_research_results_to_model():
    callback_context = SimpleNamespace(state={}, invocation_id="network-budget-exhausted")
    initial_response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request(
                "Search for public current information and summarize it."
            ),
        )
    )
    assert initial_response.content.parts[0].function_call.name == "internet_search"

    _run_internet_after_tool_callback(
        SimpleNamespace(name="internet_search"),
        {"query": "public current information"},
        callback_context,
        {
            "status": "success",
            "provider": "synthetic_search",
            "query": "public current information",
            "results": [
                {
                    "title": "Synthetic verified result",
                    "url": "https://source-a.example/updates",
                    "snippet": "Synthetic evidence retrieved during this run.",
                }
            ],
        },
    )
    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request("Continue with the remaining sources."),
        )
    )
    assert response is None


def test_internet_callback_surfaces_failure_for_result_only_search():
    callback_context = SimpleNamespace(state={}, invocation_id="result-only-network-failure")
    initial_response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request("Search for public current information."),
        )
    )
    assert initial_response.content.parts[0].function_call.name == "internet_search"

    _run_internet_after_tool_callback(
        SimpleNamespace(name="internet_search"),
        {"query": "public current information"},
        callback_context,
        {"status": "error", "error": "Synthetic network outage"},
    )

    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request("Continue."),
        )
    )
    assert response is not None
    assert "Synthetic network outage" in response.content.parts[0].text


def test_expanded_page_summary_continues_after_deterministic_scrape():
    state = {}
    callback_context = SimpleNamespace(state=state, invocation_id="page-summary")
    request = _llm_request("Open and scrape https://www.bbc.com/news and summarize the latest headlines.")

    initial_response = asyncio.run(
        internet_agent_module._internet_expanded_before_model_callback(
            callback_context,
            request,
        )
    )
    assert initial_response.content.parts[0].function_call.name == "scrape_website"

    _run_internet_after_tool_callback(
        SimpleNamespace(name="scrape_website"),
        {"url": "https://www.bbc.com/news"},
        callback_context,
        {
            "status": "success",
            "url": "https://www.bbc.com/news",
            "title": "BBC News",
            "text_content": "Synthetic current headline evidence.",
        },
    )

    followup = asyncio.run(
        internet_agent_module._internet_expanded_before_model_callback(
            callback_context,
            request,
        )
    )
    assert followup is None


def test_internet_agent_uses_only_the_june_callbacks(monkeypatch):
    monkeypatch.setattr(internet_agent_module, "Agent", lambda **kwargs: kwargs)

    config = internet_agent_module.create_internet_agent("synthetic-model")

    assert (
        config["before_model_callback"]
        is internet_agent_module._internet_compact_before_model_pipeline
    )
    assert config["after_tool_callback"] == [internet_agent_module._internet_after_tool_callback]
    assert "before_tool_callback" not in config
    assert "after_model_callback" not in config


def test_internet_prompt_keeps_the_june_instruction_shape():
    from autoyou_agents.internet_agent.prompt import AGENT_INSTRUCTION

    assert "Key capabilities:" in AGENT_INSTRUCTION
    assert "Tool-use discipline" not in AGENT_INSTRUCTION
    assert "network calls are capped" not in AGENT_INSTRUCTION


def test_internet_callback_surfaces_tool_failure_without_loop():
    callback_context = SimpleNamespace(
        state={
            internet_agent_module._INTERNET_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY: "turn-1",
            internet_agent_module._INTERNET_TOOL_ERROR_INVOCATION_ID_STATE_KEY: "turn-1",
            internet_agent_module._INTERNET_TOOL_ERROR_MESSAGE_STATE_KEY: "Browser.new_context: Connection closed while reading from the driver",
        },
        invocation_id="turn-1",
    )

    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            callback_context,
            _llm_request('Search only strictly as-is "meta small business"'),
        )
    )

    assert response is not None
    assert "internet tool failed" in response.content.parts[0].text.lower()
    assert "Connection closed while reading from the driver" in response.content.parts[0].text


def test_internet_callback_replays_successful_search_results_without_model_round_trip():
    state = {
        internet_agent_module._INTERNET_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY: "turn-1",
    }
    _run_internet_after_tool_callback(
        SimpleNamespace(name="internet_search"),
        {},
        SimpleNamespace(state=state, invocation_id="turn-1"),
        {
            "status": "success",
            "query": "latest fifa score",
            "provider": "bing_rss",
            "results": [
                {
                    "title": "FIFA World Cup 2026 scores",
                    "url": "https://www.fifa.com/scores-fixtures",
                    "snippet": "Final match, Sunday 19 July 2026.",
                }
            ],
        },
    )

    response = asyncio.run(
        internet_agent_module._internet_before_model_callback(
            SimpleNamespace(state=state, invocation_id="turn-1"),
            _llm_request("search latest fifa score"),
        )
    )

    assert response is not None
    assert "Retrieved 1 live internet search result" in response.content.parts[0].text
    assert "FIFA World Cup 2026 scores" in response.content.parts[0].text
    assert response.custom_metadata["internet_verified_tool_result"] is True


def test_explicit_browser_agent_route_does_not_stick_to_internet(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name in {"internet_agent", "browser_agent"},
    )

    callback_context = SimpleNamespace(
        state={root_agent_module._ROOT_PINNED_AGENT_STATE_KEY: "autoyou_internet_agent"},
        invocation_id="test",
    )
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Use browser agent open https://example.com and observe the page title"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert _routed_agent(function_call) == root_agent_module._BROWSER_RUNTIME_AGENT_NAME
    assert _routed_args(function_call) == {"request": "open https://example.com and observe the page title"}
    assert callback_context.state[root_agent_module._ROOT_PINNED_AGENT_STATE_KEY] == root_agent_module._BROWSER_RUNTIME_AGENT_NAME


def test_root_current_agent_query_uses_preferred_agent_state():
    callback_context = SimpleNamespace(
        state={root_agent_module._ROOT_PREFERRED_AGENT_STATE_KEY: "autoyou_internet_agent"},
        invocation_id="test",
    )

    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("What agent is this"),
        )
    )

    assert "Internet" in response.content.parts[0].text
    assert "autoyou_internet_agent" not in response.content.parts[0].text


def test_root_router_refuses_when_installed_agent_failed_to_load(monkeypatch):
    """An installed-but-broken agent must fail loudly, not fall through.

    Falling through hands the request to the bare model, which has none of that
    agent's tools and still answers as though it ran one - the path that had the
    assistant confirming notes it never wrote after the notes database was left
    sealed and its factory raised at startup.
    """
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name == "notes_agent",
    )
    # Installed, but absent from the live runtime: the factory raised at startup.
    monkeypatch.setattr(
        root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", {"autoyou_page_agent"}
    )

    callback_context = SimpleNamespace(state={}, invocation_id="notes-unavailable")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Create a note titled Groceries, content buy milk"),
        )
    )

    assert response is not None
    part = response.content.parts[0]
    assert part.function_call is None, "must not dispatch to a missing agent"
    text = part.text
    assert "Notes is installed but did not start" in text
    assert "nothing was sent or changed" in text
    assert "Admin Page" in text
    assert "runtime" not in text
    assert "autoyou_notes_agent" not in text
    assert response.custom_metadata.get("route_reason") == "agent_installed_but_unavailable"


def test_root_router_still_falls_through_when_agent_is_not_installed(monkeypatch):
    """Never-installed is not a fault: the model may answer normally."""
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module, "is_agent_installed", lambda agent_name, agents_root=None: False
    )
    monkeypatch.setattr(
        root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", {"autoyou_page_agent"}
    )

    callback_context = SimpleNamespace(state={}, invocation_id="notes-not-installed")
    response = asyncio.run(
        root_agent_module._root_router_before_model_callback(
            callback_context,
            _llm_request("Create a note titled Groceries, content buy milk"),
        )
    )

    assert response is None
