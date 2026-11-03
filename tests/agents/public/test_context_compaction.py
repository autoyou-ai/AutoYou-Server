# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Local conversations are fitted to the window, never rewritten by model summaries."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions, EventCompaction
from google.adk.flows.llm_flows.context import _contents as adk_contents
from google.genai import types

import autoyou_agents.agent as root_agent


def _turn(author, text, timestamp):
    return Event(
        author=author,
        content=types.Content(role="user" if author == "user" else "model", parts=[types.Part(text=text)]),
        timestamp=timestamp,
    )


def _conversation():
    # A summary written by the local model on 2026-11-02 in the owner's chat:
    # it answered an old question instead of summarising, and replaced the turns.
    summary = types.Content(role="model", parts=[types.Part(
        text="I don't have access to your personal information like your name unless you've shared it."
    )])
    return [
        _turn("user", "Synthetic first request: track the example.test headlines.", 1.0),
        _turn("autoyou_agent", "Synthetic first answer.", 2.0),
        Event(
            author="user",
            actions=EventActions(compaction=EventCompaction(
                start_timestamp=1.0, end_timestamp=2.0, compacted_content=summary,
            )),
            timestamp=2.5,
        ),
        _turn("user", "Synthetic follow-up.", 3.0),
    ]


def _texts(events):
    return [part.text for event in events if event.content for part in event.content.parts or [] if part.text]


def test_local_conversations_are_not_compacted_by_the_model(monkeypatch):
    monkeypatch.delenv("AUTOYOU_ADK_COMPACTION", raising=False)
    monkeypatch.setattr(root_agent, "_active_ai_provider_name", lambda: "ollama")

    assert root_agent._build_events_compaction_config() is None


def test_an_operator_can_still_opt_in_to_model_compaction(monkeypatch):
    monkeypatch.setenv("AUTOYOU_ADK_COMPACTION", "1")
    monkeypatch.setattr(root_agent, "_active_ai_provider_name", lambda: "ollama")
    monkeypatch.setattr(root_agent, "resolve_ollama_num_ctx", lambda *args, **kwargs: (12288, "test"))

    config = root_agent._build_events_compaction_config()

    assert config is not None and config.token_threshold > 0


def test_stored_summaries_give_way_to_the_original_turns(monkeypatch):
    monkeypatch.delenv("AUTOYOU_ADK_COMPACTION", raising=False)

    texts = _texts(adk_contents._process_compaction_events(_conversation(), "autoyou_agent"))

    assert texts == [
        "Synthetic first request: track the example.test headlines.",
        "Synthetic first answer.",
        "Synthetic follow-up.",
    ]


def test_stored_summaries_apply_again_when_compaction_is_opted_in(monkeypatch):
    monkeypatch.setenv("AUTOYOU_ADK_COMPACTION", "1")

    texts = _texts(adk_contents._process_compaction_events(_conversation(), "autoyou_agent"))

    assert "Synthetic first request: track the example.test headlines." not in texts
    assert any("personal information" in text for text in texts)
