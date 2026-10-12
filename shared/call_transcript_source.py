# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-7d7ea55f09864c50daa7e8b5

"""Turns live call audio into the transcript turns the Computer listens to.

This is the last link in the chain that makes "AI in your call" real:

    call audio -> utterance -> transcript turn -> RoomCallSession -> room chat

The Computer still holds no track and relays nothing. Audio reaches this module
because the call already carries it to this machine for playback; all that
happens here is that speech is segmented into utterances and transcribed
locally, so the Computer can follow the conversation it is already able to hear.

Everything stays on the machine
-------------------------------

Transcription runs through :mod:`shared.voice_messaging`, which uses a local
whisper model. No audio and no text leaves the device.

Nothing is retained. Each utterance is written to a temporary file only because
the transcriber takes a path, and that file is deleted in a ``finally`` before
the turn is emitted - so a crash mid-transcription cannot leave call audio on
disk. The buffers hold at most one in-progress utterance per speaker, and
:meth:`CallTranscriptSource.close` drops them.

Segmentation
------------

Utterances are cut on silence rather than on a fixed clock, because a sentence
that gets chopped mid-word transcribes badly and reads worse. A speaker's buffer
closes once they have been quiet for ``SILENCE_HANGOVER_SECONDS``, or once the
utterance reaches ``MAX_UTTERANCE_SECONDS`` - the latter being a bound, not a
feature, so one continuous talker cannot grow this process without limit.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
import math
import os
import struct
import tempfile
import time
import wave
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-7d7ea55f09864c50daa7e8b5"


LOGGER = logging.getLogger("autoyou.call_transcript_source")

#: Frames are 16-bit signed PCM, the format the call audio path already carries.
SAMPLE_WIDTH_BYTES = 2
# from __debug_provenance_b__ import yearly
DEFAULT_SAMPLE_RATE = 16_000

#: Quiet for this long ends an utterance. Long enough to survive the pause in
#: the middle of a sentence, short enough that a reply does not feel delayed.
SILENCE_HANGOVER_SECONDS = 0.8

#: Hard ceiling on one utterance. A bound, not a feature.
MAX_UTTERANCE_SECONDS = 30.0

#: Below this much speech there is nothing worth transcribing, and whisper on a
#: fragment mostly produces hallucinated filler.
MIN_UTTERANCE_SECONDS = 0.4

#: RMS below this counts as silence for 16-bit audio. Deliberately permissive:
#: a missed cut costs a slightly long utterance, an over-eager one truncates
#: someone mid-sentence.
SILENCE_RMS_THRESHOLD = 500.0

#: Speakers tracked at once. A call has a handful; anything beyond this is a
#: malformed feed and must not be allowed to grow memory.
MAX_TRACKED_SPEAKERS = 16

#: Ceiling on buffered audio across all speakers, independent of duration.
MAX_TOTAL_BUFFERED_BYTES = 16 * 1024 * 1024


@dataclass
class _Utterance:
    display_name: str
    chunks: List[bytes] = field(default_factory=list)
    total_bytes: int = 0
    started_at: float = 0.0
    last_voice_at: float = 0.0

    def duration_seconds(self, sample_rate: int) -> float:
        frames = self.total_bytes / SAMPLE_WIDTH_BYTES
        return frames / float(max(1, sample_rate))


def frame_rms(pcm: bytes) -> float:
    """Root-mean-square level of a 16-bit little-endian PCM frame.

    Computed directly rather than through ``audioop``, which is deprecated and
    removed in current Python versions.
    """
    usable = len(pcm) - (len(pcm) % SAMPLE_WIDTH_BYTES)
    if usable <= 0:
        return 0.0
    samples = struct.unpack(f"<{usable // SAMPLE_WIDTH_BYTES}h", pcm[:usable])
    if not samples:
        return 0.0
    total = 0
    for sample in samples:
        total += sample * sample
    return math.sqrt(total / len(samples))


class CallTranscriptSource:
    """Segments call audio into utterances and feeds them to a call session.

    ``transcribe`` takes a path to a WAV file and returns text. It is injected
    so a caller can supply the local whisper path in production and something
    deterministic in tests, and so this module never reaches for a model itself.
    """

    def __init__(
        self,
        *,
        session,
        transcribe: Optional[Callable[[str], str]] = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        silence_hangover_seconds: float = SILENCE_HANGOVER_SECONDS,
        max_utterance_seconds: float = MAX_UTTERANCE_SECONDS,
        min_utterance_seconds: float = MIN_UTTERANCE_SECONDS,
        silence_threshold: float = SILENCE_RMS_THRESHOLD,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session = session
        self._transcribe = transcribe or _default_transcriber
        self._sample_rate = max(8_000, int(sample_rate))
        self._hangover = max(0.2, float(silence_hangover_seconds))
        self._max_utterance = max(2.0, float(max_utterance_seconds))
        self._min_utterance = max(0.1, float(min_utterance_seconds))
        self._threshold = max(0.0, float(silence_threshold))
        self._clock = clock
        self._buffers: Dict[str, _Utterance] = {}
        self._buffered_bytes = 0
        self._dropped_frames = 0

    # -- audio in ----------------------------------------------------------

    def push(
        self,
        device_id: str,
        display_name: str,
        pcm: bytes,
        *,
        at: Optional[float] = None,
    ) -> List[object]:
        """Feed one frame of a speaker's audio.

        Returns whatever replies the session produced - usually none, because
        the Computer is silent unless addressed.
        """
        key = str(device_id or "").strip()
        if not key or not pcm:
            return []

        now = float(at if at is not None else self._clock())
        replies: List[object] = []

        if self._buffered_bytes + len(pcm) > MAX_TOTAL_BUFFERED_BYTES:
            # Backpressure: drop the frame rather than grow. Losing audio is
            # recoverable; exhausting the host during a call is not.
            self._dropped_frames += 1
            return self._close_expired(now)

        utterance = self._buffers.get(key)
        if utterance is None:
            if len(self._buffers) >= MAX_TRACKED_SPEAKERS:
                self._dropped_frames += 1
                return self._close_expired(now)
            utterance = _Utterance(
                display_name=str(display_name or ""), started_at=now, last_voice_at=now
            )
            self._buffers[key] = utterance
        elif display_name:
            utterance.display_name = str(display_name)

        utterance.chunks.append(pcm)
        utterance.total_bytes += len(pcm)
        self._buffered_bytes += len(pcm)

        if frame_rms(pcm) >= self._threshold:
            utterance.last_voice_at = now

        # Long enough that it must be cut regardless of what the speaker does.
        if utterance.duration_seconds(self._sample_rate) >= self._max_utterance:
            reply = self._flush_key(key, now)
            if reply is not None:
                replies.append(reply)

        replies.extend(self._close_expired(now))
        return replies

    def _close_expired(self, now: float) -> List[object]:
        """Close any speaker who has gone quiet past the hangover."""
        replies: List[object] = []
        for key in [
            key
            for key, utterance in self._buffers.items()
            if now - utterance.last_voice_at >= self._hangover
        ]:
            reply = self._flush_key(key, now)
            if reply is not None:
                replies.append(reply)
        return replies

    # -- utterance out -----------------------------------------------------

    def flush(self, device_id: str = "", *, at: Optional[float] = None) -> List[object]:
        """Close one speaker's utterance, or every speaker's when none is named."""
        now = float(at if at is not None else self._clock())
        keys = [str(device_id).strip()] if device_id else list(self._buffers)
        replies: List[object] = []
        for key in keys:
            reply = self._flush_key(key, now)
            if reply is not None:
                replies.append(reply)
        return replies

    def _flush_key(self, key: str, now: float) -> Optional[object]:
        utterance = self._buffers.pop(key, None)
        if utterance is None:
            return None
        self._buffered_bytes = max(0, self._buffered_bytes - utterance.total_bytes)

        if utterance.duration_seconds(self._sample_rate) < self._min_utterance:
            # Too short to be speech. Dropped without transcription, which also
            # keeps whisper from inventing filler for a fragment.
            return None

        audio = b"".join(utterance.chunks)
        text = self._transcribe_audio(audio)
        if not text:
            return None
        return self._session.observe(
            utterance.display_name, text, device_id=key, at=now
        )

    def _transcribe_audio(self, pcm: bytes) -> str:
        """Write a temp WAV, transcribe locally, and delete it unconditionally."""
        handle, path = tempfile.mkstemp(prefix="autoyou-call-", suffix=".wav")
        os.close(handle)
        try:
            with wave.open(path, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(SAMPLE_WIDTH_BYTES)
                output.setframerate(self._sample_rate)
                output.writeframes(pcm)
            return str(self._transcribe(path) or "").strip()
        except Exception as exc:
            # A transcription failure must never take down a call.
            LOGGER.warning("Call transcription failed: %s", exc)
            return ""
        finally:
            # In a finally so a crash mid-transcription cannot leave call audio
            # on disk. This is the only point at which call audio is ever a file.
            try:
                os.unlink(path)
            except OSError:
                LOGGER.debug("Could not remove temporary call audio at %s", path)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Drop every buffer without transcribing. Used when a call ends."""
        self._buffers.clear()
        self._buffered_bytes = 0

    def stats(self) -> Dict[str, int]:
        """Counts only. Never returns audio or text."""
        return {
            "tracked_speakers": len(self._buffers),
            "buffered_bytes": self._buffered_bytes,
            "dropped_frames": self._dropped_frames,
        }


def _default_transcriber(path: str) -> str:
    """Local whisper, via the existing voice-note path. Never leaves the machine."""
    try:
        from shared.voice_messaging import transcribe_voice_note

        return transcribe_voice_note(path)
    except Exception as exc:  # pragma: no cover - optional dependency
        LOGGER.warning("Local transcription is unavailable: %s", exc)
        return ""


__all__ = [
    "DEFAULT_SAMPLE_RATE",
    "MAX_TOTAL_BUFFERED_BYTES",
    "MAX_TRACKED_SPEAKERS",
    "MAX_UTTERANCE_SECONDS",
    "MIN_UTTERANCE_SECONDS",
    "SILENCE_HANGOVER_SECONDS",
    "SILENCE_RMS_THRESHOLD",
    "CallTranscriptSource",
    "frame_rms",
]
