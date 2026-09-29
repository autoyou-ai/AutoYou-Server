# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-af1359cac5f306999a6e5cde

"""Binds the call listener to a live call.

:mod:`shared.room_call_listener` decides *what* the Computer would say.
This module is what makes that a feature rather than a component: it holds the
room-bridge grant that authorises the Computer's presence, takes transcript
turns from a call in progress, and publishes replies into the room's chat.

The seams are deliberate. The session owns no transport and no model - a
transcript source pushes turns in, a publisher callable sends text out - so the
same object serves the full server, Lite, and the tests, and none of them can
accidentally give the Computer an authority it should not have.

What this does not do
---------------------

It never touches media. A↔B audio and video stay peer-to-peer on the path they
already use; the Computer holds no track, contributes no SDP to the room and
relays nothing. Everything here travels on the DataChannel as chat, under the
single ``chat`` permission :mod:`shared.room_bridge` already grants.

That boundary is enforced rather than assumed: the session refuses to start
without a grant carrying exactly that permission, and refuses any provider path
with an action surface.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from shared.room_bridge import (
    ROOM_BRIDGE_ALLOWED_PERMISSIONS,
    ROOM_BRIDGE_COMPUTER_MODE,
    RoomBridgeError,
    build_computer_member,
    normalize_permissions,
    require_room_bridge_read_only_backend,
)
from shared.room_call_listener import (
    ListenerReply,
    RoomCallListener,
    sanitize_speaker,
    sanitize_turn_text,
)

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-af1359cac5f306999a6e5cde"


LOGGER = logging.getLogger("autoyou.room_call_session")

#: A call that produces no transcript for this long is treated as over, so a
#: dropped connection does not leave the Computer listening indefinitely.
IDLE_TIMEOUT_SECONDS = 15 * 60

#: Publishing is best effort. A room that has moved on, or a transport that has
#: gone, must not surface as an error in someone's call.
_PUBLISH_FAILURE_LOG = "Could not publish a Computer turn to room %s: %s"


class CallSessionError(RuntimeError):
    """Raised when a session is asked to do something outside its contract."""


@dataclass
class CallSessionStats:
    """Counts only. Never carries transcript content."""

    turns_observed: int = 0
    replies_published: int = 0
    publish_failures: int = 0
    presence_failures: int = 0
    started_at: float = 0.0
    ended_at: float = 0.0


@dataclass
class _Participant:
    """A room member as attested by the host - a display name, not an identity."""

    device_id: str
    display_name: str


class RoomCallSession:
    """One call, with the Computer present as a chat-only participant."""

    def __init__(
        self,
        *,
        room_id: str,
        grant: Any,
        backend: str,
        responder: Callable[[str, Sequence[Any]], str],
        publish: Callable[[str], None],
        server_identity_key: str = "",
        server_name: str = "AutoYou Computer",
        clock: Callable[[], float] = time.time,
        idle_timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
        announce: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._room_id = str(room_id or "").strip()
        if not self._room_id:
            raise CallSessionError("a call session needs a room id")

        # Where the participant row goes when it changes. Optional because the
        # session is useful without one - the row is still readable through
        # `presence()` - but a call with no announce sink shows participants
        # nothing, which is the whole point of the row.
        self._announce = announce

        # The grant is the Computer's authority to be here at all. Checking it
        # up front means a misconfigured caller fails loudly at setup rather
        # than silently posting into a room it was never admitted to.
        self._grant = grant
        try:
            permissions = normalize_permissions(getattr(grant, "permissions", ()) or ())
        except RoomBridgeError as exc:
            # A grant the bridge itself rejects is simply not a grant the
            # Computer may join under. Translated so every ineligibility raises
            # one error type, rather than the caller having to know which layer
            # complained.
            raise CallSessionError(
                "the Computer may join a call only under a chat-only room grant"
            ) from exc
        if set(permissions) != set(ROOM_BRIDGE_ALLOWED_PERMISSIONS):
            raise CallSessionError(
                "the Computer may join a call only under a chat-only room grant"
            )
        mode = str(getattr(grant, "mode", ROOM_BRIDGE_COMPUTER_MODE) or "")
        if mode and mode != ROOM_BRIDGE_COMPUTER_MODE:
            raise CallSessionError("the Computer may join only in read-only conversation mode")

        # Refuses any provider path that has tools or actions.
        try:
            self._backend = require_room_bridge_read_only_backend(backend)
        except RoomBridgeError as exc:
            raise CallSessionError(
                "This AI provider cannot join a shared conversation. Only a local "
                "model can, because it has no tools and no way to act."
            ) from exc

        self._listener = RoomCallListener(
            room_id=self._room_id,
            backend=self._backend,
            responder=responder,
            clock=clock,
        )
        self._publish = publish
        self._clock = clock
        self._idle_timeout = max(60.0, float(idle_timeout_seconds))
        self._participants: Dict[str, _Participant] = {}
        self._stats = CallSessionStats()
        self._last_turn_at = 0.0
        self._active = False
        self._member = build_computer_member(
            server_identity_key=str(server_identity_key or self._room_id),
            server_name=server_name,
            joined_at=int(clock()),
        )

    # -- lifecycle ---------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    @property
    def stats(self) -> CallSessionStats:
        return self._stats

    def start(self, participants: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Join the call and return the member row participants should see."""
        if self._active:
            return self.presence()
        now = self._clock()
        self._participants = {}
        for entry in participants or ():
            device_id = str((entry or {}).get("device_id") or "").strip()
            if not device_id:
                continue
            self._participants[device_id] = _Participant(
                device_id=device_id,
                display_name=sanitize_speaker((entry or {}).get("display_name")),
            )
        self._listener.start()
        self._active = True
        # from __debug_provenance_c__ import subtask
        self._last_turn_at = now
        self._stats = CallSessionStats(started_at=now)
        LOGGER.info(
            "Computer joined call in room %s as a chat participant (backend=%s)",
            self._room_id,
            self._backend,
        )
        return self._announced()

    def end(self, *, summarize: bool = True) -> Optional[ListenerReply]:
        """Leave the call, optionally posting a closing summary.

        The transcript is dropped either way. A call is a conversation, not a
        record, and keeping one would create an asset nobody agreed to.
        """
        if not self._active:
            return None
        summary: Optional[ListenerReply] = None
        if summarize:
            try:
                summary = self._listener.summarize()
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.warning("Closing summary failed in room %s: %s", self._room_id, exc)
            if summary is not None:
                self._deliver(summary)
        self._listener.stop()
        self._active = False
        self._stats.ended_at = self._clock()
        LOGGER.info("Computer left call in room %s", self._room_id)
        # Participants are told it stopped listening. Leaving a stale "following
        # the conversation" row on their screens would be the one misstatement
        # this design cannot afford.
        self._announced()
        return summary

    # -- the call --------------------------------------------------------

    def observe(
        self,
        speaker: object,
        text: object,
        *,
        device_id: str = "",
        at: Optional[float] = None,
    ) -> Optional[ListenerReply]:
        """Take one transcript turn and publish a reply if one is warranted.

        ``speaker`` is resolved from the participant roster when a device id is
        supplied, so a turn cannot claim to come from someone else by putting a
        different name in the label.
        """
        if not self._active:
            return None

        now = float(at if at is not None else self._clock())
        if now - self._last_turn_at > self._idle_timeout:
            # The call went away without telling us. Close it out rather than
            # keep a listener alive against a conversation that has ended.
            LOGGER.info("Call in room %s went idle; ending the Computer's session", self._room_id)
            self.end(summarize=False)
            return None
        self._last_turn_at = now

        label = speaker
        known = self._participants.get(str(device_id or "").strip())
        if known is not None:
            label = known.display_name

        body = sanitize_turn_text(text)
        if not body:
            return None
        self._stats.turns_observed += 1

        reply = self._listener.observe(label, body, at=now)
        if reply is not None:
            self._deliver(reply)
        return reply

    def _deliver(self, reply: ListenerReply) -> None:
        """Publish a reply, tolerating a room that has already moved on."""
        try:
            self._publish(reply.text)
        except Exception as exc:
            self._stats.publish_failures += 1
            LOGGER.warning(_PUBLISH_FAILURE_LOG, self._room_id, exc)
            return
        self._stats.replies_published += 1

    # -- what participants see --------------------------------------------

    def presence(self) -> Dict[str, Any]:
        """The Computer's member row, stating its limits rather than hiding them."""
        return self._listener.presence(computer_member=dict(self._member))

    def _announced(self) -> Dict[str, Any]:
        """Publish the current row, and return it.

        A failed announce never breaks the call: participants would lose the
        row, which is a worse screen but not a broken one, and raising here
        would take down a conversation over a presence update.
        """
        row = self.presence()
        if self._announce is None:
            return row
        try:
            self._announce(dict(row))
        except Exception as exc:
            self._stats.presence_failures += 1
            LOGGER.warning(
                "Could not publish the Computer's presence in room %s: %s",
                self._room_id,
                exc,
            )
        return row

    def participant_names(self) -> List[str]:
        return sorted(entry.display_name for entry in self._participants.values())


def build_call_session(
    *,
    room_id: str,
    grant: Any,
    backend: str,
    responder: Callable[[str, Sequence[Any]], str],
    publish: Callable[[str], None],
    **kwargs: Any,
) -> RoomCallSession:
    """Construct a session, translating grant problems into one clear error.

    Callers get :class:`CallSessionError` for anything that makes the Computer
    ineligible, so a UI can show one honest message instead of surfacing the
    internals of the grant model.
    """
    try:
        return RoomCallSession(
            room_id=room_id,
            grant=grant,
            backend=backend,
            responder=responder,
            publish=publish,
            **kwargs,
        )
    except RoomBridgeError as exc:
        raise CallSessionError(
            "This AI provider cannot join a shared conversation. Only a local "
            "model can, because it has no tools and no way to act."
        ) from exc


__all__ = [
    "IDLE_TIMEOUT_SECONDS",
    "CallSessionError",
    "CallSessionStats",
    "RoomCallSession",
    "build_call_session",
]
