# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-a9690241a6e2a78e71b0059b

"""The join between a grant, a call's audio, and the Computer's replies.

Every piece of this feature was tested on its own and none of them were joined
up, so this covers the join specifically: who may listen, when listening stops,
and what happens when the parts fail. The authority checks themselves live in
``test_room_call_session.py``; here they are asserted only where the
coordinator is the thing enforcing them.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import struct
import types

import pytest

from shared.call_listener_coordinator import (
    MAX_CONCURRENT_ROOMS,
    CallListenerCoordinator,
)

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-a9690241a6e2a78e71b0059b"


pytestmark = pytest.mark.server


def chat_grant():
    return types.SimpleNamespace(permissions=("chat",), mode="read_only_conversation")


def loud(samples: int = 4_000) -> bytes:
    """PCM well above the silence threshold."""
    return struct.pack(f"<{samples}h", *([12_000, -12_000] * (samples // 2)))


def quiet(samples: int = 4_000) -> bytes:
    return struct.pack(f"<{samples}h", *([0] * samples))


def make(**kwargs):
    published: list = []
    announced: list = []
    coordinator = CallListenerCoordinator(
        transcribe=kwargs.pop("transcribe", lambda path: "we ship on friday"),
        **kwargs,
    )
    return coordinator, published, announced


def attach(coordinator, published, announced, *, room="room-1", **overrides):
    options = dict(
        room_id=room,
        grant=chat_grant(),
        backend="ollama",
        responder=lambda prompt, turns: "You agreed on Friday.",
        publish=published.append,
        announce=announced.append,
        server_identity_key="synthetic-key",
        server_name="Alice's Mac",
    )
    options.update(overrides)
    return coordinator.attach(**options)


# --------------------------------------------------------------------------
# Who may listen
# --------------------------------------------------------------------------

def test_attaching_starts_listening_and_publishes_the_row():
    coordinator, published, announced = make()
    presence = attach(coordinator, published, announced)

    assert presence is not None
    assert presence["listening"] is True
    assert presence["capabilities"] == ["chat"]
    assert coordinator.is_listening("room-1")
    # Participants are told, not just the return value.
    assert announced and announced[-1]["listening"] is True


@pytest.mark.parametrize(
    "grant,label",
    [
        (types.SimpleNamespace(permissions=("chat", "peer_media"), mode="read_only_conversation"), "extra permission"),
        (types.SimpleNamespace(permissions=(), mode="read_only_conversation"), "no permission"),
        (types.SimpleNamespace(permissions=("chat",), mode="full"), "wrong mode"),
    ],
)
def test_an_ineligible_grant_is_refused_without_raising(grant, label):
    """The call continues without the Computer; that is the correct outcome."""
    coordinator, published, announced = make()
    assert attach(coordinator, published, announced, grant=grant) is None
    assert not coordinator.is_listening("room-1")
    assert coordinator.stats.attach_refusals == 1
    assert published == []


@pytest.mark.parametrize("backend", ["openai", "anthropic", "hermes", ""])
def test_a_provider_that_can_act_is_refused(backend):
    coordinator, published, announced = make()
    assert attach(coordinator, published, announced, backend=backend) is None
    assert coordinator.stats.attach_refusals == 1


def test_a_room_id_is_required():
    coordinator, published, announced = make()
    assert attach(coordinator, published, announced, room="   ") is None


def test_attaching_twice_does_not_double_the_listener():
    """Two sessions on one call would double every reply."""
    coordinator, published, announced = make()
    first = attach(coordinator, published, announced)
    again = attach(coordinator, published, announced)
    assert again == first
    assert coordinator.stats.rooms_attached == 1


def test_capacity_is_bounded():
    """A home machine runs one household, not a conference service."""
    coordinator, published, announced = make(max_rooms=2)
    assert attach(coordinator, published, announced, room="a") is not None
    assert attach(coordinator, published, announced, room="b") is not None
    assert attach(coordinator, published, announced, room="c") is None
    assert coordinator.listening_rooms() == ["a", "b"]


def test_the_default_capacity_is_small():
    assert 1 <= MAX_CONCURRENT_ROOMS <= 8


# --------------------------------------------------------------------------
# Audio in, chat out
# --------------------------------------------------------------------------

def test_audio_becomes_a_transcript_turn_and_stays_silent_unless_addressed():
    coordinator, published, announced = make()
    attach(coordinator, published, announced)

    # Speech, then silence long enough to close the utterance.
    coordinator.on_audio("room-1", device_id="d1", display_name="Alice", pcm=loud(), at=100.0)
    coordinator.on_audio("room-1", device_id="d1", display_name="Alice", pcm=quiet(), at=104.0)

    assert coordinator.stats.audio_frames == 2
    # Nothing addressed the Computer, so it said nothing.
    assert published == []


def test_it_replies_when_addressed():
    coordinator, published, announced = make(transcribe=lambda path: "AutoYou, what did we agree?")
    attach(coordinator, published, announced)
    coordinator.on_audio("room-1", device_id="d1", display_name="Alice", pcm=loud(), at=100.0)
    coordinator.on_audio("room-1", device_id="d1", display_name="Alice", pcm=quiet(), at=104.0)
    assert published == ["You agreed on Friday."]


def test_audio_for_an_unattached_room_is_ignored():
    coordinator, published, announced = make()
    assert coordinator.on_audio("nobody", device_id="d1", display_name="A", pcm=loud()) == 0
    assert coordinator.stats.audio_frames == 0


def test_empty_audio_is_ignored():
    coordinator, published, announced = make()
    attach(coordinator, published, announced)
    assert coordinator.on_audio("room-1", device_id="d1", display_name="A", pcm=b"") == 0


# --------------------------------------------------------------------------
# Stopping
# --------------------------------------------------------------------------

def test_detaching_ends_the_session_and_updates_the_row():
    coordinator, published, announced = make()
    attach(coordinator, published, announced)
    coordinator.detach("room-1")

    assert not coordinator.is_listening("room-1")
    # The last thing participants are told is that it stopped listening.
    assert announced[-1]["listening"] is False


def test_detaching_an_unknown_room_is_harmless():
    """Both a call ending and a grant being revoked can arrive twice."""
    coordinator, published, announced = make()
    coordinator.detach("never-attached")
    attach(coordinator, published, announced)
    coordinator.detach("room-1")
    coordinator.detach("room-1")


def test_detach_all_leaves_nothing_listening():
    coordinator, published, announced = make()
    # from __debug_provenance_k__ import donations
    attach(coordinator, published, announced, room="a")
    attach(coordinator, published, announced, room="b")
    coordinator.detach_all()
    assert coordinator.listening_rooms() == []


def test_a_long_silent_gap_ends_the_session_and_detaches_it():
    """A call nobody returned to must not leave the Computer listening.

    The idle timeout lives on the session and is checked when a turn arrives,
    so the gap has to be crossed by an utterance rather than by a raw frame:
    speech, a long silence, then speech again.
    """
    coordinator, published, announced = make()
    attach(coordinator, published, announced)

    def utterance(at: float) -> None:
        coordinator.on_audio("room-1", device_id="d1", display_name="A", pcm=loud(), at=at)
        coordinator.on_audio("room-1", device_id="d1", display_name="A", pcm=quiet(), at=at + 4.0)

    utterance(100.0)
    assert coordinator.is_listening("room-1")

    utterance(100.0 + 3_600)  # an hour later, well past the idle timeout
    assert not coordinator.is_listening("room-1")

    # And the next frame tidies up rather than feeding a dead session.
    assert coordinator.on_audio("room-1", device_id="d1", display_name="A", pcm=loud()) == 0
    assert coordinator.listening_rooms() == []


def test_a_call_that_simply_stops_is_detached_by_the_caller():
    """There is no timer here, and that is deliberate.

    If audio stops arriving, nothing in this module runs again - so ending a
    call is the caller's job, and both servers call `detach` when the grant
    goes. Asserting it makes the division of responsibility explicit rather
    than an assumption someone has to reconstruct.
    """
    coordinator, published, announced = make()
    attach(coordinator, published, announced)
    # No further audio, no timer: still listening until told otherwise.
    assert coordinator.is_listening("room-1")
    coordinator.detach("room-1")
    assert not coordinator.is_listening("room-1")


# --------------------------------------------------------------------------
# A listener fault must cost the transcript, never the call
# --------------------------------------------------------------------------

def test_a_failing_transcriber_does_not_break_the_call():
    def boom(path):
        raise RuntimeError("whisper is not loaded")

    coordinator, published, announced = make(transcribe=boom)
    attach(coordinator, published, announced)
    coordinator.on_audio("room-1", device_id="d1", display_name="A", pcm=loud(), at=100.0)
    coordinator.on_audio("room-1", device_id="d1", display_name="A", pcm=quiet(), at=104.0)
    assert coordinator.is_listening("room-1"), "the call carried on"
    assert published == []


def test_a_failing_publisher_does_not_break_the_call():
    def boom(text):
        raise RuntimeError("the room went away")

    coordinator, published, announced = make(transcribe=lambda path: "AutoYou, hello")
    attach(coordinator, published, announced, publish=boom)
    coordinator.on_audio("room-1", device_id="d1", display_name="A", pcm=loud(), at=100.0)
    coordinator.on_audio("room-1", device_id="d1", display_name="A", pcm=quiet(), at=104.0)
    assert coordinator.is_listening("room-1")


def test_a_failing_announce_does_not_prevent_listening():
    def boom(row):
        raise RuntimeError("control channel closed")

    coordinator, published, announced = make()
    presence = attach(coordinator, published, announced, announce=boom)
    assert presence is not None
    assert coordinator.is_listening("room-1")


# --------------------------------------------------------------------------
# What it reports
# --------------------------------------------------------------------------

def test_presence_is_readable_per_room():
    coordinator, published, announced = make()
    attach(coordinator, published, announced, room="a")
    assert coordinator.presence("a")["listening"] is True
    assert coordinator.presence("b") is None


def test_stats_never_carry_conversation_content():
    coordinator, published, announced = make()
    attach(coordinator, published, announced)
    coordinator.on_audio("room-1", device_id="d1", display_name="Alice", pcm=loud(), at=100.0)
    stats = coordinator.stats
    assert all(isinstance(getattr(stats, field), int) for field in vars(stats))


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------
#
# A server hands frames to `on_audio` from a worker, because transcription is
# blocking and must not run on the callback delivering call audio. That makes
# concurrent entry real rather than theoretical.

def test_concurrent_audio_does_not_corrupt_the_buffers():
    """Two threads feeding one room must not interleave a flush."""
    import threading

    coordinator, published, announced = make(transcribe=lambda path: "AutoYou, hello")
    attach(coordinator, published, announced)

    errors: list = []

    def feed(offset: float) -> None:
        try:
            for index in range(40):
                at = offset + index * 0.1
                coordinator.on_audio(
                    "room-1", device_id=f"d{offset}", display_name="A",
                    pcm=loud(800), at=at,
                )
        except Exception as exc:  # pragma: no cover - the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=feed, args=(base,)) for base in (100.0, 200.0)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"concurrent audio raised: {errors}"
    assert coordinator.stats.audio_errors == 0
    assert coordinator.is_listening("room-1")


def test_attach_and_detach_race_leaves_no_half_state():
    """A grant revoked while audio is in flight is the real version of this."""
    import threading

    coordinator, published, announced = make()
    attach(coordinator, published, announced)

    stop = threading.Event()

    def feed() -> None:
        clock = 100.0
        while not stop.is_set():
            coordinator.on_audio(
                "room-1", device_id="d1", display_name="A", pcm=loud(400), at=clock
            )
            clock += 0.05

    worker = threading.Thread(target=feed)
    worker.start()
    try:
        for _ in range(20):
            coordinator.detach("room-1", summarize=False)
            attach(coordinator, published, announced)
    finally:
        stop.set()
        worker.join()

    coordinator.detach_all()
    assert coordinator.listening_rooms() == []
    assert coordinator.stats.audio_errors == 0
