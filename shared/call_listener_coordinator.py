# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-304232623937526530302920-950c0cf8b8cb725e950fde08

"""Binds a room grant, a live call's audio, and the Computer's chat replies.

The pieces of "AI in your call" were each written and tested on their own, and
nothing joined them up:

    audio -> CallTranscriptSource -> RoomCallSession -> room chat
                                          |
                                          +-> presence row

This is the join. It exists as its own module, rather than inside a server, for
the reason the rest of this feature is structured that way: the decisions worth
getting right - when the Computer may listen, when it must stop, what happens
when a grant is revoked mid-call - are testable here, and what remains in the
server is a call into this and nothing else.

What it does not do
-------------------

It holds no track, opens no socket and mixes nothing. Audio is handed to it
frame by frame *because the call already carries that audio to this machine for
playback* - the Computer is a listener on a stream that exists anyway, not a
participant in the media graph. A↔B audio and video stay peer-to-peer.

Everything stays on the machine: segmentation and transcription are local, and
:class:`CallTranscriptSource` deletes each utterance file before the turn is
emitted.

Failure posture
---------------

A call must never break because the listener did. Every entry point swallows
its own errors and records them in :attr:`CallListenerCoordinator.stats`: a
Computer that stops following a conversation is a worse call, but a Computer
that takes the call down with it is a broken product.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-304232623937526530302920-950c0cf8b8cb725e950fde08"


import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from shared.call_transcript_source import CallTranscriptSource
from shared.room_call_session import (
    CallSessionError,
    RoomCallSession,
    build_call_session,
)

LOGGER = logging.getLogger("autoyou.call_listener_coordinator")

#: Rooms that may be listening at once. A home machine runs one household, not a
#: conference service, and each active room holds a transcript buffer and
#: competes with the model for the same CPU.
MAX_CONCURRENT_ROOMS = 4


@dataclass
class CoordinatorStats:
    """Counters only. Never transcript content, never speaker names."""

    rooms_attached: int = 0
    rooms_detached: int = 0
    attach_refusals: int = 0
    audio_frames: int = 0
    audio_errors: int = 0
    replies_published: int = 0


class CallListenerCoordinator:
    """Tracks which rooms the Computer is listening to, and feeds them audio.

    One instance per server. Keyed by room id, because a grant is scoped to a
    room and the Computer's participation begins and ends with that grant.
    """

    def __init__(
        self,
        *,
        transcribe: Optional[Callable[[str], str]] = None,
        max_rooms: int = MAX_CONCURRENT_ROOMS,
    ) -> None:
        self._transcribe = transcribe
        self._max_rooms = max(1, int(max_rooms))
        self._sessions: Dict[str, RoomCallSession] = {}
        self._sources: Dict[str, CallTranscriptSource] = {}
        self._stats = CoordinatorStats()
        # Transcription is blocking and must not run on the callback that
        # delivers call audio, so a server hands frames to `on_audio` from a
        # worker. That makes concurrent entry real, and the buffers are shared
        # state, so every entry point takes this.
        self._lock = threading.RLock()

    @property
    def stats(self) -> CoordinatorStats:
        return self._stats

    def is_listening(self, room_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(self._key(room_id))
            return bool(session is not None and session.active)

    def listening_rooms(self) -> List[str]:
        with self._lock:
            return sorted(key for key, s in self._sessions.items() if s.active)

    # -- lifecycle ---------------------------------------------------------

    def attach(
        self,
        *,
        room_id: str,
        grant: Any,
        backend: str,
        responder: Callable[[str, Sequence[Any]], str],
        publish: Callable[[str], None],
        announce: Optional[Callable[[Dict[str, Any]], None]] = None,
        participants: Optional[Sequence[Dict[str, Any]]] = None,
        **session_kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Start listening to one room's call. Returns the presence row.

        Returns ``None`` when the Computer may not join - an ineligible grant, a
        provider with an action surface, or too many rooms already listening.
        Refusing is not an error condition: the call continues without it, which
        is exactly what should happen when the Computer is not allowed in.
        """
        key = self._key(room_id)
        with self._lock:
            if not key:
                self._stats.attach_refusals += 1
                return None

            existing = self._sessions.get(key)
            if existing is not None:
                # Re-attaching an active room is a no-op rather than a second
                # session: two listeners on one call would double every reply.
                return existing.presence()

            if len(self._sessions) >= self._max_rooms:
                self._stats.attach_refusals += 1
                LOGGER.info("Refusing to listen to room %s: already at capacity", key)
                return None
            return self._attach_locked(
                key,
                grant=grant,
                backend=backend,
                responder=responder,
                publish=publish,
                announce=announce,
                participants=participants,
                **session_kwargs,
            )

    def _attach_locked(
        self,
        key: str,
        *,
        grant: Any,
        backend: str,
        responder: Callable[[str, Sequence[Any]], str],
        publish: Callable[[str], None],
        announce: Optional[Callable[[Dict[str, Any]], None]],
        participants: Optional[Sequence[Dict[str, Any]]],
        **session_kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Called with the lock held."""
        try:
            session = build_call_session(
                room_id=key,
                grant=grant,
                backend=backend,
                responder=responder,
                publish=publish,
                announce=announce,
                **session_kwargs,
            )
        except CallSessionError as exc:
            # The expected path when a grant is chat-less or the provider can
            # act. Logged at info because it is a policy outcome, not a fault.
            self._stats.attach_refusals += 1
            LOGGER.info("Computer will not join the call in room %s: %s", key, exc)
            return None
        except Exception as exc:  # pragma: no cover - defensive
            self._stats.attach_refusals += 1
            LOGGER.warning("Could not build a call session for room %s: %s", key, exc)
            return None

        self._sessions[key] = session
        self._sources[key] = CallTranscriptSource(
            session=session, transcribe=self._transcribe
        )
        self._stats.rooms_attached += 1
        return session.start(participants=participants)

    def detach(self, room_id: str, *, summarize: bool = True) -> None:
        """Stop listening, close the buffers, and let the room know.

        Called when a call ends and when a grant is revoked. Safe to call for a
        room that was never attached, because both of those can arrive twice.
        """
        key = self._key(room_id)
        with self._lock:
            source = self._sources.pop(key, None)
            session = self._sessions.pop(key, None)
        if source is None and session is None:
            return

        if source is not None:
            try:
                # Anything still buffered is dropped rather than transcribed:
                # the call is over, and a late reply into a finished
                # conversation is worse than a missing one.
                source.close()
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.debug("Transcript source close failed for room %s: %s", key, exc)

        if session is not None:
            try:
                session.end(summarize=summarize)
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.warning("Could not close the call session in room %s: %s", key, exc)
        self._stats.rooms_detached += 1

    def detach_all(self, *, summarize: bool = False) -> None:
        """Shutdown path. No summaries: nobody is left to read them."""
        for key in list(self._sessions):
            self.detach(key, summarize=summarize)

    # -- audio in ----------------------------------------------------------

    def on_audio(
        self,
        room_id: str,
        *,
        device_id: str,
        display_name: str,
        pcm: bytes,
        at: Optional[float] = None,
    ) -> int:
        """Feed one frame of a speaker's audio. Returns replies published.

        This is on the call's audio path, so it must be cheap and must never
        raise: a listener fault has to cost the transcript, not the call.
        """
        key = self._key(room_id)
        # The whole push is under the lock, including the transcription an
        # utterance boundary triggers. That is deliberate: the buffers are
        # per-room and a second frame for the same room must not interleave
        # with a flush of it. Different rooms are rare enough on a home machine
        # that one lock is simpler than one per room, and correctness here
        # matters more than parallelism.
        with self._lock:
            source = self._sources.get(key)
            if source is None or not pcm:
                return 0

            session = self._sessions.get(key)
            if session is not None and not session.active:
                # Timed out or ended itself; stop feeding a dead session.
                stale_source = self._sources.pop(key, None)
                self._sessions.pop(key, None)
                self._stats.rooms_detached += 1
                if stale_source is not None:
                    try:
                        stale_source.close()
                    except Exception:  # pragma: no cover - defensive
                        pass
                return 0

            try:
                replies = source.push(device_id, display_name, pcm, at=at)
            except Exception as exc:
                self._stats.audio_errors += 1
                LOGGER.debug("Dropped a call audio frame for room %s: %s", key, exc)
                return 0

            self._stats.audio_frames += 1
            published = len([reply for reply in replies if reply is not None])
            self._stats.replies_published += published
            return published

    def presence(self, room_id: str) -> Optional[Dict[str, Any]]:
        """The Computer's participant row for one room, if it is in that room."""
        with self._lock:
            session = self._sessions.get(self._key(room_id))
        return session.presence() if session is not None else None

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _key(room_id: Any) -> str:
        return str(room_id or "").strip()


__all__ = [
    "MAX_CONCURRENT_ROOMS",
    "CallListenerCoordinator",
    "CoordinatorStats",
]
