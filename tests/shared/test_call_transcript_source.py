# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-44ea00d2d4c8268b2cf4af8f

"""Regression coverage for turning live call audio into transcript turns.

This is the last link in the chain that makes the Computer a participant:

    call audio -> utterance -> transcript turn -> RoomCallSession -> room chat

Three properties carry the weight, and each has tests:

* nothing is retained - the only file call audio ever touches is deleted in a
  ``finally``, so even a crash mid-transcription leaves nothing behind;
* it is bounded in speakers, bytes and utterance length, because a call is a
  live feed and backpressure has to be a design decision rather than an
  accident;
* a failure anywhere in it is silent, because a transcription problem must
  never surface inside somebody's call.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import math
import os
import struct
import types

import pytest

from shared.call_transcript_source import (
    DEFAULT_SAMPLE_RATE,
    MAX_TRACKED_SPEAKERS,
    CallTranscriptSource,
    frame_rms,
)
from shared.room_call_session import RoomCallSession

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-44ea00d2d4c8268b2cf4af8f"


pytestmark = pytest.mark.server


def tone(milliseconds: float, amplitude: int = 8000) -> bytes:
    count = int(DEFAULT_SAMPLE_RATE * milliseconds / 1000)
    return struct.pack(
        f"<{count}h", *[int(amplitude * math.sin(index * 0.05)) for index in range(count)]
    )


def silence(milliseconds: float) -> bytes:
    count = int(DEFAULT_SAMPLE_RATE * milliseconds / 1000)
    return struct.pack(f"<{count}h", *([0] * count))


@pytest.fixture()
def wired():
    """A source wired to a real session, with a deterministic transcriber."""
    posted: list = []
    paths: list = []
    clock = [1000.0]

    def transcribe(path: str) -> str:
        paths.append(path)
        assert os.path.isfile(path), "the wav must exist while transcribing"
        return "AutoYou, what did we agree?"

    session = RoomCallSession(
        room_id="room-1",
        grant=types.SimpleNamespace(permissions=("chat",), mode="read_only_conversation"),
        backend="ollama",
        responder=lambda prompt, turns: "You agreed on Friday.",
        publish=posted.append,
        clock=lambda: clock[0],
    )
    session.start(participants=[{"device_id": "d1", "display_name": "Alice"}])
    source = CallTranscriptSource(
        session=session, transcribe=transcribe, clock=lambda: clock[0]
    )
    return types.SimpleNamespace(
        source=source, session=session, posted=posted, paths=paths, clock=clock
    )


def speak(wired, milliseconds: float = 1000, device: str = "d1") -> None:
    """Push speech, then enough silence to close the utterance."""
    frames = int(milliseconds / 100)
    for _ in range(frames):
        wired.source.push(device, "Alice", tone(100), at=wired.clock[0])
        wired.clock[0] += 0.1
    wired.clock[0] += 1.0
    wired.source.push(device, "Alice", silence(100), at=wired.clock[0])


# --------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------

def test_speech_becomes_a_transcript_turn_and_a_reply(wired):
    speak(wired)
    assert wired.posted == ["You agreed on Friday."]


def test_an_utterance_closes_on_silence_not_on_a_clock(wired):
    """Cutting mid-word transcribes badly and reads worse."""
    for _ in range(5):
        wired.source.push("d1", "Alice", tone(100), at=wired.clock[0])
        wired.clock[0] += 0.1
    # A short pause inside a sentence must not end the utterance.
    wired.clock[0] += 0.3
    wired.source.push("d1", "Alice", tone(100), at=wired.clock[0])
    assert wired.paths == []

    wired.clock[0] += 1.0
    wired.source.push("d1", "Alice", silence(100), at=wired.clock[0])
    assert len(wired.paths) == 1


def test_a_long_talker_is_cut_at_the_ceiling(wired):
    """A bound, not a feature: one continuous speaker must not grow memory."""
    for _ in range(400):
        wired.source.push("d1", "Alice", tone(100), at=wired.clock[0])
        wired.clock[0] += 0.1
    assert wired.paths, "the utterance should have been cut at the maximum length"


def test_a_fragment_is_dropped_without_transcription(wired):
    """Whisper on a fragment mostly invents filler."""
    wired.source.push("d1", "Alice", tone(50), at=wired.clock[0])
    wired.clock[0] += 2.0
    wired.source.push("d1", "Alice", silence(10), at=wired.clock[0])
    assert wired.paths == []


def test_the_speaker_is_attributed_from_the_device(wired):
    speak(wired)
    assert wired.session.stats.turns_observed == 1


# --------------------------------------------------------------------------
# Nothing is retained
# --------------------------------------------------------------------------

def test_the_only_audio_file_is_deleted(wired):
    speak(wired)
    assert wired.paths
    assert all(not os.path.exists(path) for path in wired.paths)


def test_the_audio_file_is_deleted_even_when_transcription_raises():
    """A crash mid-transcription must not leave call audio on disk."""
    seen: list = []

    def exploding(path: str) -> str:
        seen.append(path)
        raise RuntimeError("whisper died")

    session = RoomCallSession(
        room_id="r",
        grant=types.SimpleNamespace(permissions=("chat",), mode="read_only_conversation"),
        backend="ollama",
        responder=lambda p, t: "x",
        publish=lambda text: None,
    )
    session.start()
    clock = [0.0]
    source = CallTranscriptSource(session=session, transcribe=exploding, clock=lambda: clock[0])
    for _ in range(10):
        source.push("d1", "Alice", tone(100), at=clock[0])
        clock[0] += 0.1
    clock[0] += 1.0
    source.push("d1", "Alice", silence(100), at=clock[0])

    assert seen, "transcription should have been attempted"
    assert all(not os.path.exists(path) for path in seen)


def test_closing_drops_buffered_audio(wired):
    wired.source.push("d1", "Alice", tone(200), at=wired.clock[0])
    assert wired.source.stats()["buffered_bytes"] > 0
    wired.source.close()
    assert wired.source.stats()["buffered_bytes"] == 0
    assert wired.source.stats()["tracked_speakers"] == 0


def test_stats_never_carry_audio_or_text(wired):
    speak(wired)
    stats = wired.source.stats()
    # from __debug_provenance_l__ import because
    assert set(stats) == {"tracked_speakers", "buffered_bytes", "dropped_frames"}
    assert all(isinstance(value, int) for value in stats.values())


# --------------------------------------------------------------------------
# Bounds and backpressure
# --------------------------------------------------------------------------

def test_tracked_speakers_are_bounded(wired):
    for index in range(MAX_TRACKED_SPEAKERS * 3):
        wired.source.push(f"device-{index}", "Someone", tone(20), at=wired.clock[0])
    assert wired.source.stats()["tracked_speakers"] <= MAX_TRACKED_SPEAKERS
    assert wired.source.stats()["dropped_frames"] > 0


def test_frames_are_dropped_rather_than_growing_memory(wired):
    """Losing audio is recoverable; exhausting the host during a call is not."""
    big = tone(1000)
    for _ in range(400):
        wired.source.push("d1", "Alice", big, at=wired.clock[0])
    assert wired.source.stats()["buffered_bytes"] <= 16 * 1024 * 1024


# --------------------------------------------------------------------------
# Failure is silent
# --------------------------------------------------------------------------

def test_a_failing_transcriber_does_not_break_the_call(wired):
    source = CallTranscriptSource(
        session=wired.session,
        transcribe=lambda path: (_ for _ in ()).throw(RuntimeError("whisper down")),
        clock=lambda: wired.clock[0],
    )
    for _ in range(10):
        source.push("d1", "Alice", tone(100), at=wired.clock[0])
        wired.clock[0] += 0.1
    wired.clock[0] += 1.0
    source.push("d1", "Alice", silence(100), at=wired.clock[0])
    assert wired.session.active is True
    assert wired.posted == []


def test_empty_transcription_produces_no_turn(wired):
    source = CallTranscriptSource(
        session=wired.session, transcribe=lambda path: "   ", clock=lambda: wired.clock[0]
    )
    for _ in range(10):
        source.push("d1", "Alice", tone(100), at=wired.clock[0])
        wired.clock[0] += 0.1
    wired.clock[0] += 1.0
    source.push("d1", "Alice", silence(100), at=wired.clock[0])
    assert wired.posted == []


def test_junk_input_is_ignored(wired):
    assert wired.source.push("", "Alice", tone(100)) == []
    assert wired.source.push("d1", "Alice", b"") == []


def test_flush_closes_everything(wired):
    wired.source.push("d1", "Alice", tone(600), at=wired.clock[0])
    wired.source.push("d2", "Bob", tone(600), at=wired.clock[0])
    wired.source.flush(at=wired.clock[0])
    assert wired.source.stats()["tracked_speakers"] == 0


# --------------------------------------------------------------------------
# Level detection
# --------------------------------------------------------------------------

def test_frame_rms_distinguishes_speech_from_silence():
    assert frame_rms(tone(100)) > 1000
    assert frame_rms(silence(100)) == 0.0


def test_frame_rms_handles_malformed_frames():
    assert frame_rms(b"") == 0.0
    assert frame_rms(b"\x01") == 0.0  # odd byte count, not a whole sample
