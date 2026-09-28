# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Regression coverage for the Computer's participation in a live call.

This is the object that turns the listener into a feature, so it is also where
the authority boundary has to hold. A call session must be impossible to create
with anything other than a chat-only grant on a local model, and impossible to
break by a room that has gone away.
"""

from __future__ import annotations

import types

import pytest

from shared.room_call_session import (
    CallSessionError,
    RoomCallSession,
    build_call_session,
)

pytestmark = pytest.mark.server


def chat_grant():
    return types.SimpleNamespace(permissions=("chat",), mode="read_only_conversation")


def make_session(reply="You agreed on Friday.", publish=None, **kwargs):
    posted: list = []
    session = RoomCallSession(
        room_id="room-1",
        grant=chat_grant(),
        backend="ollama",
        responder=lambda prompt, turns: reply,
        publish=publish if publish is not None else posted.append,
        server_identity_key="synthetic-key",
        server_name="Alice's Mac",
        **kwargs,
    )
    return session, posted


# --------------------------------------------------------------------------
# The authority boundary
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "grant,label",
    [
        (types.SimpleNamespace(permissions=("chat", "peer_media"), mode="read_only_conversation"), "extra permission"),
        (types.SimpleNamespace(permissions=(), mode="read_only_conversation"), "no permission"),
        (types.SimpleNamespace(permissions=("peer_media",), mode="read_only_conversation"), "wrong permission"),
        (types.SimpleNamespace(permissions=("chat",), mode="full"), "wrong mode"),
        (types.SimpleNamespace(permissions=("chat",) * 99, mode="read_only_conversation"), "permission flood"),
    ],
)
def test_only_a_chat_only_grant_can_join_a_call(grant, label):
    with pytest.raises(CallSessionError):
        RoomCallSession(
            room_id="r",
            grant=grant,
            backend="ollama",
            responder=lambda p, t: "x",
            publish=lambda text: None,
        )


@pytest.mark.parametrize("backend", ["openai", "anthropic", "hermes", "odysseus", ""])
def test_only_a_local_read_only_backend_can_join(backend):
    """A provider with an action surface must not be able to sit in a call."""
    with pytest.raises(CallSessionError):
        RoomCallSession(
            room_id="r",
            grant=chat_grant(),
            backend=backend,
            responder=lambda p, t: "x",
            publish=lambda text: None,
        )


def test_ineligibility_always_raises_one_error_type():
    """The factory and the constructor must not disagree about the error."""
    for constructor in (RoomCallSession, build_call_session):
        with pytest.raises(CallSessionError):
            constructor(
                room_id="r",
                grant=chat_grant(),
                backend="openai",
                responder=lambda p, t: "x",
                publish=lambda text: None,
            )


def test_the_factory_error_is_written_for_a_person():
    with pytest.raises(CallSessionError, match="local"):
        build_call_session(
            room_id="r",
            grant=chat_grant(),
            backend="openai",
            responder=lambda p, t: "x",
            publish=lambda text: None,
        )


def test_a_room_id_is_required():
    with pytest.raises(CallSessionError):
        RoomCallSession(
            room_id="   ",
            grant=chat_grant(),
            backend="ollama",
            responder=lambda p, t: "x",
            publish=lambda text: None,
        )


# --------------------------------------------------------------------------
# Behaviour during a call
# --------------------------------------------------------------------------

def test_it_is_silent_until_addressed():
    session, posted = make_session()
    session.start()
    session.observe("Alice", "Shall we ship Friday?")
    session.observe("Bob", "Works for me.")
    assert posted == []


def test_it_publishes_when_addressed():
    session, posted = make_session()
    session.start()
    assert session.observe("Alice", "AutoYou, what did we agree?") is not None
    assert posted == ["You agreed on Friday."]
    assert session.stats.replies_published == 1


def test_a_turn_cannot_claim_to_come_from_another_participant():
    """The roster is the authority on who spoke, not the label on the turn."""
    session, _ = make_session()
    session.start(participants=[{"device_id": "d1", "display_name": "Alice"}])
    session.observe("Definitely Bob", "hello", device_id="d1")
    # The Computer saw Alice, because d1 is Alice.
    assert session.participant_names() == ["Alice"]


def test_nothing_happens_before_start_or_after_end():
    session, posted = make_session()
    assert session.observe("Alice", "AutoYou?") is None
    session.start()
    session.end(summarize=False)
    assert session.observe("Alice", "AutoYou?") is None
    assert posted == []


def test_ending_publishes_a_summary_and_forgets_the_call():
    session, posted = make_session()
    session.start()
    session.observe("Alice", "We ship Friday.")
    summary = session.end()
    assert summary is not None
    assert posted == ["You agreed on Friday."]
    assert session.active is False


def test_ending_twice_is_harmless():
    session, _ = make_session()
    session.start()
    session.end()
    assert session.end() is None


def test_starting_twice_returns_the_same_presence():
    session, _ = make_session()
    first = session.start()
    assert session.start() == first


# --------------------------------------------------------------------------
# Resilience
# --------------------------------------------------------------------------

def test_a_dead_publisher_does_not_break_the_call():
    """A room that moved on must not surface as an error in someone's call."""
    def boom(text):
        raise RuntimeError("transport gone")

    session, _ = make_session(publish=boom)
    session.start()
    session.observe("Alice", "AutoYou?")
    assert session.active is True
    assert session.stats.publish_failures == 1
    assert session.stats.replies_published == 0


def test_a_failing_model_stays_silent():
    session = RoomCallSession(
        room_id="r",
        grant=chat_grant(),
        backend="ollama",
        responder=lambda p, t: (_ for _ in ()).throw(RuntimeError("model down")),
        publish=lambda text: None,
    )
    session.start()
    assert session.observe("Alice", "AutoYou?") is None
    assert session.active is True


def test_an_idle_call_ends_itself():
    """A dropped connection must not leave the Computer listening forever."""
    now = [1000.0]
    session, _ = make_session(clock=lambda: now[0])
    session.start()
    session.observe("Alice", "hi", at=now[0])
    now[0] += 20 * 60
    session.observe("Alice", "still there?", at=now[0])
    assert session.active is False


def test_empty_turns_are_ignored():
    session, _ = make_session()
    session.start()
    assert session.observe("Alice", "   ") is None
    assert session.stats.turns_observed == 0


# --------------------------------------------------------------------------
# What participants see
# --------------------------------------------------------------------------

def test_presence_states_the_limits_rather_than_hiding_them():
    session, _ = make_session()
    presence = session.start()
    assert presence["capabilities"] == ["chat"]
    assert presence["hears_audio"] is False
    assert presence["speaks_audio"] is False
    assert presence["records"] is False
    assert presence["role"] == "computer"
    assert presence["display_name"] == "Alice's Mac"


def test_stats_never_carry_transcript_content():
    session, _ = make_session()
    session.start()
    session.observe("Alice", "something private")
    stats = session.stats
    assert stats.turns_observed == 1
    assert all(isinstance(getattr(stats, field), (int, float)) for field in vars(stats))


# --------------------------------------------------------------------------
# The presence contract shared with the clients
# --------------------------------------------------------------------------

def test_presence_matches_the_shared_contract():
    """The participant row is the product's central claim made checkable.

    A client renders these fields directly to everyone in the call, so the
    reference and the corpus the clients read must not drift. See
    ``clients/ios/Packages/AutoYouPeerLink/Tests/.../CallPresenceTests.swift``.
    """
    import json
    import pathlib

    corpus_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "fixtures" / "call_presence" / "v1.json"
    )
    assert corpus_path.is_file(), "the call presence corpus is missing"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    assert corpus["format"] == "autoyou.call-presence/1"

    # Built directly rather than through make_session: the corpus is generated
    # with a fixed identity and clock so the row is byte-stable across runs.
    session = RoomCallSession(
        room_id="room-1",
        grant=chat_grant(),
        backend="ollama",
        responder=lambda prompt, turns: "x",
        publish=lambda text: None,
        server_identity_key="synthetic-identity",
        server_name="Alice's Mac",
        clock=lambda: 1_700_000_000.0,
    )
    assert session.start() == corpus["listening"], "presence drifted from the corpus"

    session.end(summarize=False)
    assert session.presence() == corpus["not_listening"]


def test_the_contract_fields_never_widen():
    """Anything beyond chat would mean the Computer held media authority."""
    session, _ = make_session()
    presence = session.start()
    assert presence["capabilities"] == ["chat"]
    assert presence["hears_audio"] is False
    assert presence["speaks_audio"] is False
    assert presence["records"] is False
    assert presence["role"] == "computer"
    assert presence["notice"].strip()


# --------------------------------------------------------------------------
# Announcing the row to participants
# --------------------------------------------------------------------------
#
# A row nobody receives is not a participant list entry. These cover the other
# half of the contract: the row actually leaving the session, and doing so on
# both edges, because a stale "following the conversation" left on somebody's
# screen after the Computer stopped is the one misstatement this design cannot
# afford.

def test_joining_announces_the_row():
    announced: list = []
    session, _ = make_session(announce=announced.append)
    presence = session.start()
    assert announced == [presence]
    assert announced[0]["listening"] is True


def test_leaving_announces_that_it_stopped():
    announced: list = []
    session, _ = make_session(announce=announced.append)
    session.start()
    session.end(summarize=False)
    assert len(announced) == 2
    assert announced[0]["listening"] is True
    assert announced[1]["listening"] is False


def test_the_announced_row_is_a_copy():
    """A caller mutating what it was handed must not corrupt the next one."""
    announced: list = []
    session, _ = make_session(announce=announced.append)
    session.start()
    announced[0]["capabilities"] = ["chat", "peer_media"]
    assert session.presence()["capabilities"] == ["chat"]


def test_a_failing_announce_does_not_break_the_call():
    def boom(_row):
        raise RuntimeError("transport gone")

    session, posted = make_session(announce=boom)
    session.start()
    assert session.active is True
    assert session.stats.presence_failures == 1
    session.observe("Alice", "AutoYou, what did we agree?")
    assert posted == ["You agreed on Friday."]


def test_a_session_without_an_announce_sink_still_works():
    session, _ = make_session()
    assert session.start()["listening"] is True
    assert session.stats.presence_failures == 0


def test_starting_twice_announces_once():
    announced: list = []
    session, _ = make_session(announce=announced.append)
    session.start()
    session.start()
    assert len(announced) == 1
