# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""A turn keeps going until it has an answer (owner report 2026-11-03).

The second of two messages was answered with "The agent returned only a
progress update..." and the owner had to send "?" to get the answer. The model
had in fact answered; a first-words check discarded it. A reply that really
only announces a step now continues on the same AI session instead.

The AI worker is the one stand-in here: `send_message_to_ai_agent` is replaced
by a scripted model, and everything else in the turn is the real code.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import asyncio
from datetime import datetime

import pytest

import rest_api
from shared.adk_state import AUTOYOU_TURN_CONTINUATION_STATE_KEY

ANSWER = "37 × 52 − 3 ÷ 3 = 1923."


class _SessionManager:
    """The conversation store: one external conversation mapped to an AI session."""

    def __init__(self):
        self.mapping = {"synthetic-conversation": "synthetic-ai-session"}
        self.event_writes = []
        self.sessions = {("synthetic-user", "synthetic-ai-session"): {"events": [], "message_count": 0}}

    def get_mapped_session_id(self, external_session_id, user_id=None):
        return self.mapping.get(external_session_id)

    def set_session_mapping(self, external_session_id, internal_session_id, user_id=None):
        self.mapping[external_session_id] = internal_session_id

    async def create_user_session(self, user_id, session_id, initial_state, external_session_id=None):
        self.sessions[(user_id, session_id)] = {**dict(initial_state or {}), "events": [], "message_count": 0}
        return self.sessions[(user_id, session_id)]

    async def update_user_session(self, user_id, session_id, session_data):
        current = dict(self.sessions.get((user_id, session_id), {}))
        current.update(dict(session_data or {}))
        self.sessions[(user_id, session_id)] = current
        return current

    async def add_session_event(self, user_id, session_id, event_type, event_data, external_session_id=None):
        self.event_writes.append((user_id, session_id, event_type, dict(event_data or {})))
        current = dict(self.sessions.get((user_id, session_id), {}))
        current["events"] = list(current.get("events", [])) + [
            {"timestamp": datetime.now().isoformat(), "type": event_type, "data": dict(event_data or {})}
        ]
        self.sessions[(user_id, session_id)] = current
        return True

    async def get_user_session(self, user_id, session_id):
        data = self.sessions.get((user_id, session_id))
        return dict(data) if isinstance(data, dict) else None


def _reply(text, index):
    return {
        "message": {"parts": [{"text": text}]},
        "author": "autoyou",
        "id": f"synthetic-reply-{index}",
        "invocationId": f"synthetic-invocation-{index}",
        "usageMetadata": {},
    }


def _scripted_model(monkeypatch, replies):
    """The AI worker, answering each /run_sse call with the next scripted reply.

    A reply is its text, or {"text", "calls": [(tool, args)], "calls_in_body"}:
    tool calls stream as events before the text, as ADK streams them, or appear
    only in the returned body (the parser's whole-body fallback).
    """
    session_manager = _SessionManager()
    calls = []

    async def fake_send_message_to_ai_agent(
        user_id, session_id, message, ai_agent_url=None, *, context=None, metadata=None, state_delta=None, on_chunk=None
    ):
        calls.append({
            "session_id": session_id,
            "message": message,
            "context": context,
            "metadata": dict(metadata or {}),
            "state_delta": dict(state_delta or {}),
        })
        spec = replies[min(len(calls), len(replies)) - 1]
        spec = {"text": spec} if isinstance(spec, str) else spec
        tool_parts = [{"functionCall": {"name": name, "args": args}} for name, args in spec.get("calls", [])]
        if tool_parts and on_chunk and not spec.get("calls_in_body"):
            await on_chunk({"content": {"parts": tool_parts}, "author": "autoyou"})
        reply = _reply(spec["text"], len(calls))
        if spec.get("calls_in_body"):
            reply["message"]["parts"] = tool_parts + reply["message"]["parts"]
        return reply

    async def fake_get_ai_agent_session(user_id, session_id, ai_agent_url=None):
        return {"id": session_id}

    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setattr(rest_api, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(rest_api, "get_session_metrics", lambda: None)
    monkeypatch.setattr(rest_api, "_active_ai_provider", lambda: "ollama")
    monkeypatch.setattr(rest_api, "send_message_to_ai_agent", fake_send_message_to_ai_agent)
    monkeypatch.setattr(rest_api, "get_ai_agent_session", fake_get_ai_agent_session)
    monkeypatch.setattr(
        rest_api, "create_ai_agent_session", lambda user_id, ai_agent_url=None: asyncio.sleep(0, result="synthetic-ai-session")
    )
    return session_manager, calls


def _request(message="What is 37x52-3/3", metadata=None):
    return rest_api.ChatRequest(
        message=message,
        user_id="synthetic-user",
        session_id="synthetic-conversation",
        context=[{"attachments": []}],
        metadata={"canonical_owner_key": "cloud:synthetic-device", **(metadata or {})},
    )


@pytest.mark.asyncio
async def test_a_reply_that_only_announces_a_step_is_continued_until_answered(monkeypatch):
    session_manager, calls = _scripted_model(monkeypatch, ["Let me calculate that.", ANSWER])
    chunks = []

    async def on_chunk(chunk):
        chunks.append(chunk)

    response = await rest_api.process_chat_message(_request(), ai_agent_url="http://127.0.0.1:8081", on_chunk=on_chunk)

    assert response.response == ANSWER
    assert response.metadata["turn_continuations"] == 1
    assert "progress_only_response_filtered" not in response.metadata
    # The same AI session continues with the user's own words (no English put
    # in their mouth, nothing re-attached); session state says it is a continuation.
    assert [call["session_id"] for call in calls] == ["synthetic-ai-session", "synthetic-ai-session"]
    assert [call["message"] for call in calls] == ["What is 37x52-3/3", "What is 37x52-3/3"]
    assert [call["state_delta"][AUTOYOU_TURN_CONTINUATION_STATE_KEY] for call in calls] == [0, 1]
    assert calls[1]["context"] == []
    assert calls[1]["metadata"]["turn_continuation"] == 1
    # The streamed announcement is replaced by the answer, not joined to it.
    assert {"_autoyou_stream_reset": True} in chunks
    # The conversation keeps the user's message and the answer.
    turn = session_manager.event_writes[-1][3]
    assert (turn["user_message"], turn["agent_response"]) == ("What is 37x52-3/3", ANSWER)


@pytest.mark.asyncio
async def test_an_answer_that_opens_with_let_me_is_delivered_as_it_is(monkeypatch):
    answer = "Let me calculate that:\n\n37 × 52 = 1,924\n1,924 - 1 = 1,923\n\nThe answer is **1923**."
    _, calls = _scripted_model(monkeypatch, [answer])

    response = await rest_api.process_chat_message(_request(), ai_agent_url="http://127.0.0.1:8081")

    assert response.response == answer
    assert len(calls) == 1
    assert "turn_continuations" not in response.metadata


@pytest.mark.asyncio
async def test_continuations_stop_after_the_limit_and_say_so(monkeypatch):
    _, calls = _scripted_model(monkeypatch, ["Let me check that."])

    response = await rest_api.process_chat_message(_request("Check my backups"), ai_agent_url="http://127.0.0.1:8081")

    assert len(calls) == 1 + rest_api._DEFAULT_TURN_CONTINUATIONS
    assert response.response.startswith("I couldn't finish that")
    assert response.metadata["incomplete_agent_response"] is True
    assert response.metadata["turn_continuations"] == rest_api._DEFAULT_TURN_CONTINUATIONS


@pytest.mark.asyncio
async def test_continuations_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("AUTOYOU_TURN_MAX_CONTINUATIONS", "0")
    _, calls = _scripted_model(monkeypatch, ["Let me check that.", ANSWER])

    response = await rest_api.process_chat_message(_request("Check my backups"), ai_agent_url="http://127.0.0.1:8081")

    assert len(calls) == 1
    assert response.response.startswith("I couldn't finish that")


@pytest.mark.asyncio
async def test_a_spoken_question_continues_with_the_spoken_words(monkeypatch):
    _, calls = _scripted_model(monkeypatch, ["Déjame calcularlo.", ANSWER])

    response = await rest_api.process_chat_message(
        _request("¿Cuánto es 37x52-3/3?", metadata={"source": "voice_call", "is_transcription": True}),
        ai_agent_url="http://127.0.0.1:8081",
    )

    assert response.response == ANSWER
    # The same spoken words go again, still marked as spoken, so the reply
    # stays conversational and in Spanish.
    assert [call["message"] for call in calls] == ["¿Cuánto es 37x52-3/3?", "¿Cuánto es 37x52-3/3?"]
    assert [call["metadata"]["source"] for call in calls] == ["voice_call", "voice_call"]


@pytest.mark.asyncio
async def test_a_turn_that_started_something_is_not_continued(monkeypatch):
    """Continuing could start the image twice; the model's own words stay."""
    started = {
        "text": "Let me make that image for you.",
        "calls": [("route_to_specialist", {"agent": "autoyou_media_generation_agent", "request": "a cat"})],
    }
    _, calls = _scripted_model(monkeypatch, [started, ANSWER])

    response = await rest_api.process_chat_message(_request("Make an image of a cat"), ai_agent_url="http://127.0.0.1:8081")

    assert len(calls) == 1
    assert response.response == "Let me make that image for you."
    assert response.metadata["incomplete_agent_response"] is True
    assert response.metadata["progress_only_after_action"] is True
    assert "turn_continuations" not in response.metadata
    assert "progress_only_response_filtered" not in response.metadata


@pytest.mark.asyncio
async def test_a_turn_that_only_looked_things_up_is_continued(monkeypatch):
    looked_up = {
        "text": "Let me search again.",
        "calls": [("route_to_specialist", {"agent": "autoyou_internet_agent", "request": "today's news"})],
    }
    _, calls = _scripted_model(monkeypatch, [looked_up, "Here are today's three top stories."])

    response = await rest_api.process_chat_message(_request("What's in the news?"), ai_agent_url="http://127.0.0.1:8081")

    assert len(calls) == 2
    assert response.response == "Here are today's three top stories."
    assert response.metadata["turn_continuations"] == 1


@pytest.mark.asyncio
async def test_an_action_during_a_continuation_stops_the_next_one(monkeypatch):
    saved = {"text": "Let me check that for you.", "calls": [("remember_long_term_memory", {"text": "synthetic fact"})]}
    _, calls = _scripted_model(monkeypatch, ["Let me check that.", saved, ANSWER])

    response = await rest_api.process_chat_message(_request("Remember my tea order"), ai_agent_url="http://127.0.0.1:8081")

    # The continuation stored something, so there is no second continuation.
    assert len(calls) == 2
    assert response.response == "Let me check that for you."
    assert response.metadata["turn_continuations"] == 1
    assert response.metadata["progress_only_after_action"] is True


@pytest.mark.asyncio
async def test_tool_calls_in_a_whole_response_body_count_too(monkeypatch):
    saved = {"text": "Let me save that.", "calls": [("append_persona", {"text": "Example"})], "calls_in_body": True}
    _, calls = _scripted_model(monkeypatch, [saved, ANSWER])

    response = await rest_api.process_chat_message(_request("My name is Example"), ai_agent_url="http://127.0.0.1:8081")

    assert len(calls) == 1
    assert response.response == "Let me save that."


@pytest.mark.asyncio
async def test_a_calculation_does_not_stop_the_turn_from_continuing(monkeypatch):
    calculated = {"text": "Let me check that.", "calls": [("calculate", {"expression": "37*52-3/3"})]}
    _, calls = _scripted_model(monkeypatch, [calculated, ANSWER])

    response = await rest_api.process_chat_message(_request(), ai_agent_url="http://127.0.0.1:8081")

    assert len(calls) == 2
    assert response.response == ANSWER
