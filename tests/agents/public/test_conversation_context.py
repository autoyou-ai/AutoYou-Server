# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
"""Conversation continuity across the root agent and its specialists.

Pins two contracts drawn from a real failure (content here is synthetic):

* No agent's request may outgrow the model's context window. The session that
  failed sent "request (8345 tokens) exceeds the available context size (8192
  tokens)" after a page-feed listing and a long answer sat in its history.
* A follow-up keeps its context across agents: research from the Internet
  agent, then "@page add this" or "@notes save this", must reach Page/Notes with
  the research attached, even though each specialist runs in a fresh child
  session that only sees its request string.
"""

import asyncio
from types import SimpleNamespace

import litellm
import pytest
from google.adk.events.event import Event
from google.genai import types

import autoyou_agents.agent as root_agent_module
import autoyou_agents.page_agent.agent as page_agent_module
from autoyou_agents import prompt as root_prompt
from autoyou_agents.litellm_ollama_adapter import (
    CONTEXT_TRIM_NOTE_MARKER,
    context_window_budget,
    fit_messages_to_context_window,
    is_context_overflow_error,
    parse_context_overflow_error,
)
from autoyou_agents.shared_tools import conversation_context as cc

MODEL = "ollama_chat/example-model:8b"
# The wire form seen in the server log, escaping included.
OVERFLOW_ERROR = (
    'litellm.APIConnectionError: Ollama_chatException - {"error":"{\\"error\\":{\\"code\\":400,'
    '\\"message\\":\\"request (8345 tokens) exceeds the available context size (8192 tokens), try increasing it\\",'
    '\\"type\\":\\"exceed_context_size_error\\",\\"n_prompt_tokens\\":8345,\\"n_ctx\\":8192}}"}'
)
RESEARCH = (
    "A collateralized loan obligation (CLO) is a security backed by a pool of leveraged corporate loans. "
    "Its cash flows are split into tranches: senior tranches are paid first and carry the highest ratings, "
    "mezzanine tranches sit in the middle, and the equity tranche absorbs the first losses in exchange for "
    "the residual return. Managers actively trade the loan pool within limits set by coverage tests. "
    "Sources: https://example.com/clo-primer and https://example.org/structured-credit"
)
FEED_LISTING = "The AutoYou Page feed has 40 items.\n\nTop items:\n" + "\n".join(
    f"- #{index}: Example item {index} (image, Local)" for index in range(40)
)


@pytest.fixture(autouse=True)
def _fresh_calibration():
    cc.reset_calibration()
    yield
    cc.reset_calibration()


def _system(text=None):
    return {"role": "system", "content": text if text is not None else root_prompt.AGENT_INSTRUCTION}


def _failing_session_messages():
    """The shape of the request that failed: a long instruction, a feed listing, an earlier answer."""
    return [
        _system(),
        {"role": "user", "content": "How many page feed items do I have"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "route_to_specialist", "arguments": '{"agent": "autoyou_page_agent"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": FEED_LISTING * 4},
        {"role": "assistant", "content": FEED_LISTING * 4},
        {"role": "user", "content": "search the web about CLOs"},
        {"role": "assistant", "content": RESEARCH * 12},
        {"role": "user", "content": "@main."},
        {"role": "assistant", "content": "You're back with AutoYou."},
        {"role": "user", "content": "Tell me about clo in financial world"},
    ]


def _tools(count=8):
    return [
        {
            "type": "function",
            "function": {
                "name": f"tool_{index}",
                "description": "Example tool description. " * 20,
                "parameters": {"type": "object", "properties": {"request": {"type": "string"}}},
            },
        }
        for index in range(count)
    ]


def _estimated(messages, tools):
    return cc.estimate_request_tokens(messages, tools)


# ── Context window fitting ─────────────────────────────────────────────────────


def test_the_failing_request_is_fitted_under_the_window_with_the_question_intact():
    messages, tools = _failing_session_messages(), _tools()
    assert _estimated(messages, tools) > 8192  # the failure reproduces without fitting

    fitted, report = fit_messages_to_context_window(
        messages, tools=tools, num_ctx=8192, num_predict=1024, model=MODEL
    )

    assert report["fitted"] is True and report["over_budget"] is False
    assert _estimated(fitted, tools) <= context_window_budget(8192, 1024)
    assert fitted[-1] == {"role": "user", "content": "Tell me about clo in financial world"}
    assert CONTEXT_TRIM_NOTE_MARKER in fitted[0]["content"]
    assert fitted[0]["content"].startswith(root_prompt.AGENT_INSTRUCTION)  # instruction prefix kept for the prompt cache


def test_relevant_older_turns_outrank_unrelated_ones_when_trimming():
    fitted, report = fit_messages_to_context_window(
        _failing_session_messages(), tools=_tools(), num_ctx=8192, num_predict=1024, model=MODEL
    )
    text = "\n".join(str(message.get("content") or "") for message in fitted)

    assert report["dropped_messages"] >= 3
    assert "collateralized loan obligation" in text  # the CLO answer the question builds on stays
    assert "Example item 39" not in text  # the unrelated feed listing goes first


def test_a_dropped_tool_call_never_leaves_its_result_behind():
    fitted, _ = fit_messages_to_context_window(
        _failing_session_messages(), tools=_tools(), num_ctx=8192, num_predict=1024, model=MODEL
    )
    call_ids = {
        call["id"] for message in fitted for call in (message.get("tool_calls") or [])
    }
    result_ids = {message["tool_call_id"] for message in fitted if message.get("role") == "tool"}

    assert result_ids <= call_ids


def test_a_request_that_fits_is_returned_untouched():
    messages = [_system("Short instruction."), {"role": "user", "content": "hi"}]

    fitted, report = fit_messages_to_context_window(messages, num_ctx=8192, num_predict=1024, model=MODEL)

    assert report["fitted"] is False
    assert fitted == messages and fitted[1] is messages[1]


def test_an_oversized_current_turn_shortens_tool_results_but_keeps_the_question():
    question = "Summarize the page you just fetched."
    messages = [
        _system("Short instruction."),
        {"role": "user", "content": question},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "fetch", "type": "function", "function": {"name": "fetch_url", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "fetch", "content": "Fetched page text. " * 6000},
    ]

    fitted, report = fit_messages_to_context_window(messages, num_ctx=4096, num_predict=512, model=MODEL)

    assert report["over_budget"] is False
    assert fitted[1]["content"] == question
    assert "characters omitted" in fitted[3]["content"]
    assert _estimated(fitted, None) <= context_window_budget(4096, 512)


def test_overflow_errors_are_recognized_and_parsed_from_the_wire_form():
    assert is_context_overflow_error(OVERFLOW_ERROR)
    assert parse_context_overflow_error(OVERFLOW_ERROR) == {"prompt_tokens": 8345, "context_tokens": 8192}
    assert not is_context_overflow_error("Ollama_chatException - model not found")


def test_an_overflow_teaches_the_calibration_so_the_next_fit_is_tighter():
    messages, tools = _failing_session_messages(), _tools()
    loose, _ = fit_messages_to_context_window(messages, tools=tools, num_ctx=8192, num_predict=1024, model=MODEL)

    cc.observe_prompt_tokens(MODEL, _estimated(loose, tools), int(_estimated(loose, tools) * 1.4), overflow=True)
    tight, report = fit_messages_to_context_window(messages, tools=tools, num_ctx=8192, num_predict=1024, model=MODEL)

    assert cc.calibration_factor(MODEL) > 1.4
    assert _estimated(tight, tools) < _estimated(loose, tools)
    assert report["factor"] > 1.4


def test_successful_usage_moves_calibration_gradually_and_within_bounds():
    cc.observe_prompt_tokens(MODEL, 1000, 800)
    first = cc.calibration_factor(MODEL)
    cc.observe_prompt_tokens(MODEL, 1000, 1200)

    assert first == pytest.approx(0.8)
    assert first < cc.calibration_factor(MODEL) < 1.2
    cc.observe_prompt_tokens(MODEL, 10, 10_000)
    assert cc.calibration_factor(MODEL) <= 3.0


def _model_response(text="ok", prompt_tokens=None):
    response = litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": text}}])
    if prompt_tokens is not None:
        response.usage = litellm.Usage(prompt_tokens=prompt_tokens, completion_tokens=1, total_tokens=prompt_tokens + 1)
    return response


def test_a_count_near_the_window_is_not_used_for_calibration():
    """Ollama may silently truncate an oversized prompt and report the truncated count."""
    messages = [_system("instruction " * 2000), {"role": "user", "content": "hi"}]

    root_agent_module._learn_from_response_usage(MODEL, messages, None, _model_response(prompt_tokens=7900), False, 8192)
    assert cc.calibration_factor(MODEL) == 1.0

    root_agent_module._learn_from_response_usage(MODEL, messages, None, _model_response(prompt_tokens=6000), False, 8192)
    assert cc.calibration_factor(MODEL) != 1.0


def test_the_adk_client_patch_refits_and_retries_when_ollama_still_reports_an_overflow(monkeypatch):
    sent = []

    async def fake_original(self, model, messages, tools, **kwargs):
        sent.append(list(messages))
        if len(sent) == 1:
            raise RuntimeError(OVERFLOW_ERROR)
        return _model_response()

    monkeypatch.setattr(root_agent_module, "_original_litellm_client_acompletion", fake_original)
    messages = [_system("Short instruction.")] + [
        message for pair in (
            ({"role": "user", "content": f"question {index} " + "words " * 400},
             {"role": "assistant", "content": "answer " * 400})
            for index in range(8)
        ) for message in pair
    ] + [{"role": "user", "content": "and the last one?"}]

    response = asyncio.run(
        root_agent_module._patched_litellm_client_acompletion(
            None, MODEL, messages, None, num_ctx=8192, num_predict=1024
        )
    )

    assert response.choices[0].message.content == "ok"
    assert len(sent) == 2
    assert len(sent[1]) < len(sent[0])  # the retry carried a smaller prompt
    assert sent[1][-1]["content"] == "and the last one?"


# ── Follow-up classification ───────────────────────────────────────────────────


def _turn(role, text, at, author=""):
    return cc.ConversationTurn(role=role, text=text, author=author or ("user" if role == "user" else "autoyou_agent"), timestamp=at)


HISTORY = [
    _turn("user", "search the web about CLOs", 1000.0),
    _turn("assistant", RESEARCH, 1020.0, "autoyou_internet_agent"),
]


@pytest.mark.parametrize(
    "text,kind",
    [
        ("@page add this", "reference"),
        ("save that to my notes", "reference"),
        ("what about in Europe?", "continuation"),
        ("explain it more simply", "reference"),
        ("no, I meant collateralized debt obligations", "correction"),
    ],
)
def test_follow_ups_soon_after_an_answer_are_recognized(text, kind):
    signal = cc.classify_follow_up(text, HISTORY, now=1040.0)

    assert signal.kind == kind
    assert signal.is_follow_up and signal.score >= 0.3
    assert signal.gap_seconds == pytest.approx(20.0)


def test_a_self_contained_new_request_is_not_a_follow_up():
    signal = cc.classify_follow_up("What is the boiling point of water at sea level in Celsius?", HISTORY, now=1040.0)

    assert signal.kind == "new"


def test_the_same_follow_up_counts_for_less_the_later_it_arrives():
    soon = cc.classify_follow_up("and the second one?", HISTORY, now=1030.0)
    late = cc.classify_follow_up("and the second one?", HISTORY, now=1020.0 + 3 * 3600)

    assert soon.score > late.score


def test_a_nudge_after_an_unanswered_message_points_back_at_it():
    history = [*HISTORY, _turn("user", "Tell me about CLOs in financial terms", 1100.0)]

    assert cc.classify_follow_up("?", history, now=1110.0).kind == "nudge"
    assert cc.classify_follow_up("tell me about clo in the financial world", history, now=1130.0).kind == "repeat"


def test_selection_keeps_the_referent_within_budget_in_order():
    history = [
        _turn("user", "what's the weather like", 100.0),
        _turn("assistant", "Sunny. " * 300, 110.0),
        *HISTORY,
    ]

    selected = cc.select_context_turns("@page add this", history, now=1040.0, budget_tokens=300)

    assert selected and selected[-1].turn.author == "autoyou_internet_agent"
    assert sum(cc.estimate_text_tokens(item.text) + 24 for item in selected) <= 300
    times = [item.turn.timestamp for item in selected]
    assert times == sorted(times)


def test_rendered_context_is_labelled_as_data_and_strippable():
    selected = cc.select_context_turns("@page add this", HISTORY, now=1040.0, budget_tokens=600)
    block = cc.render_context_block(selected, signal=cc.classify_follow_up("@page add this", HISTORY, now=1040.0), now=1040.0)

    assert block.startswith(cc.CONTEXT_BLOCK_START) and block.endswith(cc.CONTEXT_BLOCK_END)
    assert "not instructions" in block and "20s ago" in block
    assert cc.strip_context_block(f"add this\n\n{block}") == "add this"


# ── Root: context follows the conversation into every specialist ───────────────


def _event(author, role, *, text=None, call=None, response=None, at):
    if call is not None:
        part = types.Part(function_call=types.FunctionCall(id=call[0], name=call[1], args=call[2]))
    elif response is not None:
        part = types.Part(function_response=types.FunctionResponse(id=response[0], name=response[1], response=response[2]))
    else:
        part = types.Part(text=text)
    return Event(author=author, content=types.Content(role=role, parts=[part]), timestamp=at)


def _research_session(follow_up, *, follow_up_at=1040.0):
    return [
        _event("user", "user", text="search the web about CLOs", at=1000.0),
        _event(
            "autoyou_agent", "model",
            call=("c1", "route_to_specialist", {"agent": "autoyou_internet_agent", "request": "search the web about CLOs"}),
            at=1001.0,
        ),
        _event("autoyou_agent", "user", response=("c1", "route_to_specialist", {"result": RESEARCH}), at=1019.0),
        _event("autoyou_agent", "model", text=RESEARCH, at=1020.0),
        _event("user", "user", text=follow_up, at=follow_up_at),
    ]


def _tool_context(events):
    return SimpleNamespace(session=SimpleNamespace(events=events), state={})


def test_turns_from_events_reads_specialist_results_and_drops_the_roots_verbatim_replay():
    turns = cc.turns_from_events(
        _research_session("@page add this"),
        resolve_specialist=root_agent_module._resolve_specialist_for_turns,
    )

    assert [(turn.role, turn.author) for turn in turns] == [
        ("user", "user"),
        ("assistant", "autoyou_internet_agent"),
        ("user", "user"),
    ]
    assert turns[1].request == "search the web about CLOs" and turns[1].timestamp == 1019.0


def test_page_add_this_after_research_carries_the_research_to_be_saved():
    context = _tool_context(_research_session("@page add this"))
    args = {"agent": "autoyou_page_agent", "request": "add this"}

    root_agent_module._root_before_tool_callback(SimpleNamespace(name=root_agent_module._ROUTER_TOOL_NAME), args, context)

    assert "[AutoYou previous answer to save on the page feed; title: search the web about CLOs]" in args["request"]
    assert RESEARCH in args["request"]
    assert "[AutoYou previous referenced URL" not in args["request"]  # not reduced to a source link


def test_notes_save_this_after_research_carries_the_research_as_note_content():
    context = _tool_context(_research_session("@notes save this"))
    args = {"request": "save this"}

    root_agent_module._root_before_tool_callback(SimpleNamespace(name="autoyou_notes_agent"), args, context)

    assert "[AutoYou previous assistant answer; save this as note content only]" in args["request"]
    assert RESEARCH in args["request"]


def test_any_specialist_gets_the_selected_turns_through_its_instruction(monkeypatch):
    monkeypatch.setattr(root_agent_module, "_active_num_ctx", lambda: 8192)
    context = _tool_context(_research_session("remind me about this tomorrow at 9"))
    args = {"request": "remind me about this tomorrow at 9"}

    root_agent_module._root_before_tool_callback(SimpleNamespace(name="autoyou_notify_agent"), args, context)

    assert args == {"request": "remind me about this tomorrow at 9"}  # the request its fast paths read is untouched
    ref = context.state[cc.context_ref_state_key("autoyou_notify_agent")]
    llm_request = SimpleNamespace(config=SimpleNamespace(system_instruction="You are the Notify agent."))
    callback = root_agent_module._make_specialist_conversation_context_callback("autoyou_notify_agent")
    child_context = SimpleNamespace(state=dict(context.state))  # AgentTool copies the parent's state

    assert asyncio.run(callback(child_context, llm_request)) is None
    instruction = llm_request.config.system_instruction
    assert instruction.startswith("You are the Notify agent.")
    assert cc.CONTEXT_BLOCK_START in instruction and "collateralized loan obligation" in instruction
    asyncio.run(callback(child_context, llm_request))
    assert instruction == llm_request.config.system_instruction  # attached once


def test_a_self_contained_request_goes_out_alone_and_clears_a_stale_reference():
    context = _tool_context(
        _research_session("What is the boiling point of water at sea level in Celsius?", follow_up_at=9000.0)
    )
    context.state[cc.context_ref_state_key("autoyou_internet_agent")] = "stale"
    args = {"request": "What is the boiling point of water at sea level in Celsius?"}

    root_agent_module._root_before_tool_callback(SimpleNamespace(name="autoyou_internet_agent"), args, context)

    assert context.state[cc.context_ref_state_key("autoyou_internet_agent")] == ""


def test_continuity_callbacks_are_installed_once_and_run_last():
    def own_callback(callback_context, llm_request):
        return None

    agent = SimpleNamespace(name="autoyou_example_agent", before_model_callback=own_callback, on_model_error_callback=None)

    root_agent_module._install_conversation_continuity_callbacks(agent)
    root_agent_module._install_conversation_continuity_callbacks(agent)

    assert len(agent.before_model_callback) == 2 and agent.before_model_callback[0] is own_callback
    assert len(agent.on_model_error_callback) == 1


def test_an_overflow_that_survives_refitting_becomes_a_clear_reply():
    callback = root_agent_module._make_context_overflow_error_callback(root_prompt.AGENT_NAME)

    response = asyncio.run(callback(None, None, RuntimeError(OVERFLOW_ERROR)))
    unrelated = asyncio.run(callback(None, None, RuntimeError("connection refused")))

    assert "8192-token context window" in response.content.parts[0].text
    assert "Context override" in response.content.parts[0].text
    assert unrelated is None


# ── Root: a bare switch or a correction re-processes the previous message ───────


@pytest.fixture
def explicit_tools(monkeypatch):
    monkeypatch.setenv("AUTOYOU_LOCAL_ROUTING", "0")
    monkeypatch.setattr(root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", set())
    monkeypatch.setattr(root_agent_module, "_RUNTIME_AGENT_TOOL_NAMES_BY_INSTALL_NAME", {})
    monkeypatch.setattr(root_agent_module, "_provider_requires_explicit_agent_tools", lambda: True)
    monkeypatch.setattr(
        root_agent_module,
        "is_agent_installed",
        lambda agent_name, agents_root=None: agent_name in {"internet_agent", "notes_agent", "page_agent"},
    )


def _router(events, user_text):
    callback_context = SimpleNamespace(
        state={}, invocation_id=f"inv-{len(events)}", session=SimpleNamespace(events=events)
    )
    llm_request = SimpleNamespace(
        contents=[event.content for event in events],
        config=SimpleNamespace(system_instruction="Root instruction."),
    )
    response = asyncio.run(root_agent_module._root_router_before_model_callback(callback_context, llm_request))
    return response, llm_request, callback_context


def test_a_bare_switch_back_to_main_answers_the_message_that_failed(explicit_tools):
    events = [
        _event("user", "user", text="How many page feed items do I have", at=900.0),
        _event("autoyou_agent", "model", text=FEED_LISTING, at=905.0),
        _event("user", "user", text="Tell me about CLOs in financial terms", at=1000.0),  # its turn failed
        _event("user", "user", text="@main.", at=1015.0),
    ]

    response, llm_request, context = _router(events, "@main.")

    assert response is None  # the model answers, instead of a bare "You're back with AutoYou."
    assert 'Answer it now: "Tell me about CLOs in financial terms"' in llm_request.config.system_instruction
    assert context.state[root_agent_module._ROOT_PREFERRED_AGENT_STATE_KEY] == root_prompt.AGENT_NAME


def test_a_bare_switch_after_an_answered_message_still_just_switches(explicit_tools):
    events = [
        _event("user", "user", text="hello", at=900.0),
        _event("autoyou_agent", "model", text="Hi! How can I help?", at=901.0),
        _event("user", "user", text="@main.", at=920.0),
    ]

    response, _llm_request, _context = _router(events, "@main.")

    assert response.content.parts[0].text == "You're back with AutoYou."


def test_a_correction_to_web_research_is_sent_back_to_the_web_agent_refined(explicit_tools):
    events = _research_session("no, I meant collateralized debt obligations", follow_up_at=1045.0)

    response, _llm_request, _context = _router(events, "no, I meant collateralized debt obligations")

    call = response.content.parts[0].function_call
    agent = call.args["agent"] if call.name == root_agent_module._ROUTER_TOOL_NAME else call.name
    assert agent == "autoyou_internet_agent"
    assert "previous request: search the web about CLOs" in call.args["request"]


def test_a_late_correction_is_left_to_the_model(explicit_tools):
    events = _research_session("no, I meant collateralized debt obligations", follow_up_at=1020.0 + 3600)

    response, _llm_request, _context = _router(events, "no, I meant collateralized debt obligations")

    assert response is None


def test_the_root_model_is_told_how_a_follow_up_relates_to_the_chat():
    context = SimpleNamespace(state={}, session=SimpleNamespace(events=_research_session("explain it more simply")))

    note = root_agent_module._root_conversation_note(context)

    assert note.startswith("[Conversation state]")
    assert "Internet" in note and "search the web about CLOs" in note
    assert root_agent_module._root_conversation_note(
        SimpleNamespace(state={}, session=SimpleNamespace(events=_research_session("What is 2+2?", follow_up_at=9000.0)))
    ) == ""


# ── Page agent: saving a carried answer ────────────────────────────────────────


class _FakePageTool:
    def __init__(self):
        self.links = []
        self.blobs = []

    def add_link(self, *, url, title=None, source=None, item_type=None):
        self.links.append(url)
        return {"success": True, "item": {"id": 1, "url": url}, "message": f"Added {url}"}

    def add_blob(self, *, filename, data_base64, mimetype=None, source=None, user_id=None,
                 session_id=None, message_id=None, title=None, metadata=None):
        self.blobs.append({"filename": filename, "data": data_base64, "mimetype": mimetype, "title": title})
        return {"success": True, "item": {"id": 7, "title": title}, "message": f"Saved {title} to your AutoYou page feed."}


def test_the_page_agent_saves_a_carried_answer_as_text_instead_of_adding_its_first_link(monkeypatch):
    import base64

    fake = _FakePageTool()
    monkeypatch.setattr(page_agent_module, "page_tool", fake)
    request_text = (
        "add this\n\n[AutoYou previous answer to save on the page feed; title: search the web about CLOs]\n" + RESEARCH
    )
    llm_request = SimpleNamespace(contents=[SimpleNamespace(parts=[SimpleNamespace(text=request_text)])])

    response = asyncio.run(page_agent_module._page_agent_before_model_callback(None, llm_request))

    assert fake.links == []
    assert len(fake.blobs) == 1
    saved = fake.blobs[0]
    assert saved["mimetype"] == "text/markdown" and saved["filename"] == "search-the-web-about-clos.md"
    assert base64.b64decode(saved["data"]).decode("utf-8") == f"# search the web about CLOs\n\n{RESEARCH}\n"
    assert response.custom_metadata["route_reason"] == "deterministic_page_feed_save_text"


def test_asking_for_the_link_adds_the_link_even_after_a_long_answer():
    request = SimpleNamespace(
        contents=[
            types.Content(role="user", parts=[types.Part(text="search the web about CLOs")]),
            types.Content(role="model", parts=[types.Part(text=RESEARCH)]),
            types.Content(role="user", parts=[types.Part(text="add that link to my page")]),
        ]
    )

    routed = root_agent_module._build_page_agent_request("add that link to my page", request)

    assert "[AutoYou previous referenced URL: https://example.com/clo-primer]" in routed
    assert "previous answer to save" not in routed


def test_a_correction_finds_the_specialist_even_when_the_root_reworded_its_answer(explicit_tools):
    events = _research_session("no, I meant collateralized debt obligations", follow_up_at=1045.0)
    events[3] = _event("autoyou_agent", "model", text="In short: CLOs pool leveraged loans into tranches.", at=1020.0)

    response, _llm_request, _context = _router(events, "no, I meant collateralized debt obligations")

    call = response.content.parts[0].function_call
    agent = call.args["agent"] if call.name == root_agent_module._ROUTER_TOOL_NAME else call.name
    assert agent == "autoyou_internet_agent"


def test_a_specialist_turn_remembers_its_request_without_carried_markers():
    events = _research_session("thanks")
    events[1] = _event(
        "autoyou_agent", "model",
        call=("c1", "autoyou_internet_agent", {"request": "search CLOs\n\n[AutoYou previous user request]\nx"}),
        at=1001.0,
    )
    events[2] = _event("autoyou_agent", "user", response=("c1", "autoyou_internet_agent", {"result": RESEARCH}), at=1019.0)

    turns = cc.turns_from_events(events, resolve_specialist=root_agent_module._resolve_specialist_for_turns)

    assert turns[1].author == "autoyou_internet_agent" and turns[1].request == "search CLOs"


def test_ordinary_tools_with_a_request_argument_get_no_conversation_context():
    context = _tool_context(_research_session("@page add this"))
    args = {"request": "add this"}

    root_agent_module._root_before_tool_callback(SimpleNamespace(name="process_media_content"), args, context)

    assert args == {"request": "add this"} and context.state == {}


def test_small_models_save_a_carried_answer_without_retyping_it_into_a_tool_call():
    """Live failure: ministral-3:8b copied research into create_note as invalid JSON and Ollama rejected the turn."""
    import autoyou_agents.notes_agent.agent as notes_agent_module

    request_text = (
        "save this\n\n[AutoYou previous user request]\nsearch the web about CLOs\n"
        "[AutoYou previous assistant answer; save this as note content only]\n" + RESEARCH
    )
    context = SimpleNamespace(state={}, invocation_id="compact-save")
    llm_request = SimpleNamespace(
        contents=[types.Content(role="user", parts=[types.Part(text=request_text)])],
        config=SimpleNamespace(system_instruction="Notes."),
    )

    response = asyncio.run(notes_agent_module._notes_before_model_callback(context, llm_request))

    call = response.content.parts[0].function_call
    assert call.name == "create_note" and call.args["content"] == RESEARCH
    assert call.args["title"]

    # Next step of the same turn: the write's own result is the reply, and nothing is written twice.
    llm_request.contents += [
        types.Content(role="model", parts=[types.Part(function_call=call)]),
        types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(
                name="create_note", response={"status": "success", "message": "Created note #12."}
            ))],
        ),
    ]
    followup = asyncio.run(notes_agent_module._notes_before_model_callback(context, llm_request))

    assert followup.content.parts[0].text == "Created note #12."
    assert followup.custom_metadata["notes_mutation_verified"] is True


def test_pointing_at_the_answer_without_a_save_verb_is_left_to_the_model():
    import autoyou_agents.notes_agent.agent as notes_agent_module

    request_text = (
        "summarize the above\n\n[AutoYou previous user request]\nsearch the web about CLOs\n"
        "[AutoYou previous assistant answer; save this as note content only]\n" + RESEARCH
    )
    context = SimpleNamespace(state={}, invocation_id="compact-summarize")
    llm_request = SimpleNamespace(
        contents=[types.Content(role="user", parts=[types.Part(text=request_text)])],
        config=SimpleNamespace(system_instruction="Notes."),
    )

    assert asyncio.run(notes_agent_module._notes_before_model_callback(context, llm_request)) is None


def test_small_models_still_write_ordinary_notes_themselves():
    import autoyou_agents.notes_agent.agent as notes_agent_module

    context = SimpleNamespace(state={}, invocation_id="compact-plain")
    llm_request = SimpleNamespace(
        contents=[types.Content(role="user", parts=[types.Part(text='Create a note titled "Errand" with content: buy stamps')])],
        config=SimpleNamespace(system_instruction="Notes."),
    )

    assert asyncio.run(notes_agent_module._notes_before_model_callback(context, llm_request)) is None


def test_a_short_answer_pointing_at_a_link_still_adds_the_link():
    request = SimpleNamespace(
        contents=[
            types.Content(role="user", parts=[types.Part(text="What is a good site for this?")]),
            types.Content(role="model", parts=[types.Part(text="Try https://example.com/guide for a walkthrough.")]),
            types.Content(role="user", parts=[types.Part(text="add that to my page")]),
        ]
    )

    routed = root_agent_module._build_page_agent_request("add that to my page", request)

    assert "[AutoYou previous referenced URL: https://example.com/guide]" in routed
    assert "previous answer to save" not in routed
