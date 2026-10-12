# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-9409d8cff57982645e64f404

"""The Computer as a listener in a peer call.

A call between two people stays a call between two people. Their audio and video
remain peer-to-peer over the existing TURN path; the Computer never joins the
media graph, never mixes, and never relays. It joins the *conversation* on the
DataChannel only, exactly as it already joins a room's chat, and its entire
contribution is text.

That split is the whole design:

* **Media stays two-party.** A home machine sleeps, throttles and gets its lid
  closed. Anything that puts it in the media path makes a call's survival depend
  on it, which is a promise the product cannot keep. It also puts audio mixing
  in contention with the local model for the same CPU, precisely when the
  Computer is doing something interesting.
* **The value is understanding, not speaking.** What people want from an AI in a
  call is that it followed the conversation - who agreed to what, what that
  number was, what to do next. None of that needs a voice.
* **The authority boundary is unchanged.** :mod:`shared.room_bridge` grants the
  Computer one permission, ``chat``, in ``read_only_conversation`` mode, and
  states that call tracks are not room-scoped negotiation and must not be
  advertised as Computer audio authority. This module holds that line rather
  than widening it.

What a participant sees
----------------------

The Computer is a visible member of the room for the whole call. It answers when
someone addresses it by name, and it can produce a closing summary when asked.
It does not narrate, does not interject unprompted, and never emits anything but
finished conversational text - no reasoning, no internal state, no tool traces.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Sequence, Tuple

from shared.room_bridge import (
    PRESENCE_CONTRACT,
    ROOM_BRIDGE_MAX_MESSAGE_CHARS,
    RoomBridgeError,
    bound_room_bridge_reply,
    require_room_bridge_read_only_backend,
)

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-9409d8cff57982645e64f404"


LOGGER = logging.getLogger("autoyou.room_call_listener")

#: Where the Computer runs, said plainly. Not a hostname: participants are being
#: told whose machine this is, not how to reach it.
PRESENCE_RUNS_ON = "this computer"

#: Shown verbatim to everyone in the room, on every platform. Never reworded per
#: client, so two people looking at the same call read the same sentence.
PRESENCE_NOTICE = (
    "Follows the conversation and replies in chat. "
    "It has no voice in the call and keeps no recording."
)


def computer_presence(
    computer_member: Dict[str, object], *, listening: bool
) -> Dict[str, object]:
    """The Computer's participant row.

    One builder, used by the live call session and by both servers at the moment
    a grant is issued, so a room never shows a row assembled some other way.

    The limit fields come straight from :data:`shared.room_bridge.PRESENCE_CONTRACT`
    - the same dict ``assert_presence_within_contract`` validates against. A row
    built here cannot drift from the row the transport will accept, because
    there is only one copy of the claim.
    """
    return {
        **computer_member,
        **{key: value for key, value in PRESENCE_CONTRACT.items()},
        "listening": bool(listening),
        "runs_on": PRESENCE_RUNS_ON,
        "notice": PRESENCE_NOTICE,
    }

#: Turns of transcript held in memory. A call's recent past is what makes the
#: Computer useful; its distant past is a liability. Bounded rather than
#: unbounded so a long call cannot grow this process without limit.
MAX_TRANSCRIPT_TURNS = 400

#: Per-turn ceiling. Transcription of a single utterance is short; anything
#: larger is a malformed or hostile feed.
MAX_TURN_CHARS = 2_000

#: Speaker labels are display strings supplied by the room host and are never an
#: authenticated identity (see ``ROOM_BRIDGE_ORIGIN_TRUST``).
MAX_SPEAKER_CHARS = 40

#: Minimum gap between Computer replies. Stops a wake-word repeated in a noisy
#: room from turning into a stream of messages.
MIN_REPLY_INTERVAL_SECONDS = 4.0

#: Transcript turns handed to the model for one reply. Enough for context,
#: bounded so prompt size stays predictable on a home machine.
REPLY_CONTEXT_TURNS = 40

#: Turns considered when summarising. Larger than a reply, still bounded.
SUMMARY_CONTEXT_TURNS = 200

#: How the Computer is addressed. Matched on a word boundary so "computer" in
#: ordinary speech does not trigger it.
_DEFAULT_WAKE_WORDS = ("autoyou", "computer")

#: Explicit asks that produce a summary rather than a conversational reply.
_SUMMARY_PATTERNS = (
    r"\bsummar(?:y|ise|ize)\b",
    r"\brecap\b",
    r"\baction items?\b",
    r"\bwhat did we (?:decide|agree)\b",
)

#: The Computer's contribution is finished prose. This strips anything that
#: looks like internal scaffolding a local model might emit, so private
#: reasoning never reaches a room full of people.
_INTERNAL_MARKUP = re.compile(
    r"<\s*/?\s*(?:think|thinking|scratchpad|reasoning|analysis|internal|tool_call|tool_result|system)\s*[^>]*>",
    re.IGNORECASE,
)
_INTERNAL_LINE = re.compile(
    r"^\s*(?:thought|thinking|reasoning|analysis|plan|internal|note to self|system)\s*:",
    re.IGNORECASE,
)


class CallListenerError(RuntimeError):
    """Raised when the listener is asked to do something outside its contract."""


@dataclass(frozen=True)
class TranscriptTurn:
    """One utterance, as attributed by the room host."""

    speaker: str
    text: str
    at: float

    def render(self) -> str:
        return f"{self.speaker}: {self.text}"


@dataclass
class ListenerReply:
    """Text the Computer wants to post to room chat."""

    text: str
    kind: str  # "answer" | "summary"
    at: float = field(default_factory=time.time)


def _flatten(value: object) -> str:
    """Collapse to a single safe line.

    Non-printable characters become spaces rather than being deleted: removing a
    newline would join two words ("Alice\\nBob" -> "AliceBob") and quietly change
    what the text says, while replacing it keeps the reading and still removes
    the ability to forge a second line of UI.
    """
    text = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def sanitize_speaker(value: object) -> str:
    """Bound a host-attested speaker label to something safe to render."""
    return _flatten(value)[:MAX_SPEAKER_CHARS] or "Someone"


def sanitize_turn_text(value: object) -> str:
    """Bound and flatten one transcript turn."""
    return _flatten(value)[:MAX_TURN_CHARS]


def strip_internal_reasoning(value: object) -> str:
    """Return only the finished reply.

    A local model may emit ``<think>`` blocks or "Reasoning:" preambles. Those
    are private working state, and a room of participants is the last place they
    belong, so they are removed before anything is posted.
    """
    text = str(value or "")
    # Drop complete tagged blocks, then any stray tags left behind.
    text = re.sub(
        r"<\s*(think|thinking|scratchpad|reasoning|analysis|internal)\s*>.*?<\s*/\s*\1\s*>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = _INTERNAL_MARKUP.sub(" ", text)
    kept = [line for line in text.splitlines() if not _INTERNAL_LINE.match(line)]
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


class RoomCallListener:
    """Follows a call's transcript and contributes text to the room's chat.

    The listener owns no transport. It is handed transcript turns and returns
    replies; posting them is the caller's job, through the room bridge grant that
    already exists. That keeps this module free of any authority of its own.
    """

    def __init__(
        self,
        *,
        room_id: str,
        backend: str,
        responder: Callable[[str, Sequence[TranscriptTurn]], str],
        wake_words: Sequence[str] = _DEFAULT_WAKE_WORDS,
        clock: Callable[[], float] = time.time,
        max_turns: int = MAX_TRANSCRIPT_TURNS,
    ) -> None:
        # Refuses any provider path that has an action surface. A listener that
        # could act would be a very different thing from one that comments.
        self._backend = require_room_bridge_read_only_backend(backend)
        self._room_id = str(room_id or "").strip()
        if not self._room_id:
            raise CallListenerError("a call listener needs a room id")

        self._responder = responder
        self._clock = clock
        self._turns: Deque[TranscriptTurn] = deque(maxlen=max(10, int(max_turns)))
        self._wake = tuple(
            w.lower() for w in wake_words if str(w or "").strip()
        ) or _DEFAULT_WAKE_WORDS
        self._wake_re = re.compile(
            r"\b(" + "|".join(re.escape(w) for w in self._wake) + r")\b", re.IGNORECASE
        )
        self._summary_re = re.compile("|".join(_SUMMARY_PATTERNS), re.IGNORECASE)
        self._last_reply_at = 0.0
        self._active = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    @property
    def backend(self) -> str:
        return self._backend

    def start(self) -> None:
        """Begin following a call. Clears any prior transcript."""
        self._turns.clear()
        self._last_reply_at = 0.0
        self._active = True
        # from __debug_provenance_b__ import yearly

    def stop(self) -> None:
        """Stop following, and forget the transcript.

        The transcript is deliberately not persisted. A call is a conversation,
        not a record, and keeping one would create an asset nobody consented to.
        """
        self._active = False
        self._turns.clear()

    # -- transcript --------------------------------------------------------

    def observe(self, speaker: object, text: object, *, at: Optional[float] = None) -> Optional[ListenerReply]:
        """Record one transcript turn and decide whether to answer.

        Returns a :class:`ListenerReply` only when the Computer was addressed,
        or asked for a summary. Silence is the default and the common case.
        """
        if not self._active:
            return None

        body = sanitize_turn_text(text)
        if not body:
            return None
        now = float(at if at is not None else self._clock())
        turn = TranscriptTurn(speaker=sanitize_speaker(speaker), text=body, at=now)
        self._turns.append(turn)

        if not self._wake_re.search(body):
            return None
        if now - self._last_reply_at < MIN_REPLY_INTERVAL_SECONDS:
            return None

        wants_summary = bool(self._summary_re.search(body))
        return self._reply(
            prompt=body,
            kind="summary" if wants_summary else "answer",
            context_turns=SUMMARY_CONTEXT_TURNS if wants_summary else REPLY_CONTEXT_TURNS,
            now=now,
        )

    def summarize(self, *, at: Optional[float] = None) -> Optional[ListenerReply]:
        """Produce a closing summary, e.g. when a participant ends the call."""
        if not self._turns:
            return None
        now = float(at if at is not None else self._clock())
        return self._reply(
            prompt="Summarise this call and list any action items.",
            kind="summary",
            context_turns=SUMMARY_CONTEXT_TURNS,
            now=now,
            enforce_interval=False,
        )

    # -- internals ---------------------------------------------------------

    def _reply(
        self,
        *,
        prompt: str,
        kind: str,
        context_turns: int,
        now: float,
        enforce_interval: bool = True,
    ) -> Optional[ListenerReply]:
        context = list(self._turns)[-max(1, int(context_turns)):]
        try:
            raw = self._responder(prompt, tuple(context))
        except Exception as exc:
            # A model failure must not take down the call or leak a stack trace
            # into a room. Stay silent and let the humans carry on.
            LOGGER.warning("Call listener responder failed in room %s: %s", self._room_id, exc)
            return None

        text = strip_internal_reasoning(raw)
        if not text:
            return None
        text = bound_room_bridge_reply(text)
        if enforce_interval:
            self._last_reply_at = now
        return ListenerReply(text=text, kind=kind, at=now)

    # -- diagnostics -------------------------------------------------------

    def transcript_length(self) -> int:
        """Turn count only. Never returns transcript content."""
        return len(self._turns)

    def presence(self, *, computer_member: Dict[str, object]) -> Dict[str, object]:
        """Describe the Computer's participation, for the room's member list."""
        return computer_presence(computer_member, listening=self._active)


__all__ = [
    "MAX_TRANSCRIPT_TURNS",
    "MAX_TURN_CHARS",
    "MIN_REPLY_INTERVAL_SECONDS",
    "PRESENCE_NOTICE",
    "PRESENCE_RUNS_ON",
    "CallListenerError",
    "ListenerReply",
    "RoomCallListener",
    "TranscriptTurn",
    "computer_presence",
    "sanitize_speaker",
    "sanitize_turn_text",
    "strip_internal_reasoning",
]
