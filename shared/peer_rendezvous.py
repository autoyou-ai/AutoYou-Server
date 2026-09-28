# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Blind rendezvous for the Peer Link answer leg.

Adding a contact is a WebRTC offer/answer exchange. Historically both legs
travelled through a human: the inviter copied a ``/peerpair`` offer out, and the
invitee copied a ``/peerpair_answer`` back. The return leg is what made adding
someone a four-step, two-person operation, and it is the only reason a person
was in the signalling path at all.

This module removes that leg. The invitee posts its answer here and the inviter
collects it. Nothing else about Peer Link changes: the same
:mod:`clients.python.peer_link.codec` envelopes cross the wire, byte for byte.

Blind by construction
---------------------

The rendezvous is deliberately incapable of reading what it carries:

* Envelopes arrive already encrypted under the invitation passphrase, which
  travels in the URL *fragment* of an invite link and therefore never reaches a
  server at all. This process stores ciphertext and returns ciphertext.
* A slot is addressed only by its invitation id - an opaque high-entropy token
  minted by the inviter. There is no account, no device id, and no contact
  identity anywhere in this module.
* Nothing here is retained past the invitation lifetime, and an answer is
  handed out exactly once.

So a compromised rendezvous host learns that *some* pairing happened and how
large the envelope was. It cannot learn who paired, cannot read the SDP, and
cannot substitute an answer of its own - the codec's keyed tag and the
invitation binding are verified by the client after collection.

Enumeration
-----------

Unknown, expired and not-yet-answered invitations are all reported as the same
"nothing waiting" outcome. A caller cannot use this endpoint to discover which
invitation ids exist.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple

LOGGER = logging.getLogger("autoyou.peer_rendezvous")

#: Invitation ids are opaque URL-safe tokens minted by the inviting client.
#: Bounded on both ends so a slot key can never be used to smuggle payload.
_INVITATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

#: Mirrors ``peer_link.protocol.PEER_INVITATION_LIFETIME_SECONDS``. Restated
#: rather than imported because ``shared`` must not depend on a client package.
DEFAULT_TTL_SECONDS = 10 * 60

#: Ceiling on a stored envelope. ``peer_link.protocol`` caps signalling at 1 MiB;
#: an answer is a fraction of that, and a tighter bound here keeps a single
#: invitation from occupying meaningful memory.
MAX_ENVELOPE_BYTES = 256 * 1024

#: Total slots held at once. Reached only under abuse, which is exactly when a
#: rendezvous must not become the thing that exhausts the host.
MAX_SLOTS = max(64, int(os.getenv("AUTOYOU_PEER_RENDEZVOUS_MAX_SLOTS", "4096") or 4096))

#: Answer attempts accepted for one invitation. An invitation is a one-to-one
#: introduction; repeated posts are either a retry or someone guessing.
MAX_ANSWER_ATTEMPTS = 5

#: Collection attempts before a slot is abandoned. Generous enough for a client
#: polling every two seconds across the full lifetime.
MAX_COLLECT_ATTEMPTS = 400


class RendezvousError(ValueError):
    """Raised when a request is malformed or outside policy."""


class SlotState(str, Enum):
    """What a collector should do next.

    ``PENDING`` deliberately covers "no such invitation", "expired" and "not
    answered yet" so that collection cannot be used to enumerate slots.
    """

    PENDING = "pending"
    READY = "ready"
    DECLINED = "declined"


#: Times an offer may be fetched. An invite is a one-to-one introduction, but a
#: real client retries across a flaky network and a link can be tapped twice, so
#: this is small rather than one.
MAX_OFFER_FETCHES = 20


@dataclass
class _Slot:
    invitation_id: str
    created_at: float
    expires_at: float
    #: The inviter's encrypted ``/peerpair`` envelope. Opaque here.
    offer: str = ""
    envelope: str = ""
    declined: bool = False
    offer_fetches: int = 0
    answer_attempts: int = 0
    collect_attempts: int = 0
    consumed: bool = False
    # Bound to the first successful post so a later poster cannot overwrite an
    # answer the inviter has not collected yet.
    sealed: bool = False


def normalize_invitation_id(value: object) -> str:
    """Validate and return an invitation id, or raise :class:`RendezvousError`."""
    text = str(value or "").strip()
    if not _INVITATION_ID_RE.fullmatch(text):
        raise RendezvousError("invitation id is not well formed")
    return text


def mint_invitation_id() -> str:
    """Mint an invitation id with 256 bits of entropy."""
    return secrets.token_urlsafe(32)


#: Codec wire text is ``/<command>\n<v3 body>`` - a command line, one newline,
#: then a single line of printable ASCII. Only those two lines are ever valid.
_MAX_ENVELOPE_LINES = 2


def _looks_like_envelope(value: str) -> bool:
    """Cheap shape check.

    The rendezvous cannot decrypt, so this only rejects payloads that are
    obviously not a codec envelope: it bounds the size, requires the two-line
    ``/peerpair``-style framing, and refuses control characters that would let a
    payload smuggle extra structure into anything that later logs or renders it.
    Real validation happens on the client after collection, where the passphrase
    exists to verify the keyed tag.
    """
    if not value or len(value) > MAX_ENVELOPE_BYTES:
        return False
    lines = value.split("\n")
    if len(lines) > _MAX_ENVELOPE_LINES or not lines[0].startswith("/"):
        return False
    return all(32 <= ord(ch) <= 126 for line in lines for ch in line)


class PeerRendezvous:
    """In-memory, self-expiring mailbox for Peer Link answers.

    Thread-safe. Intended to be process-local: an inviter polls the same host it
    minted the invitation against, so no cross-process sharing is required.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_slots: int = MAX_SLOTS,
        clock=time.time,
    ) -> None:
        self._ttl = max(30.0, float(ttl_seconds))
        self._max_slots = max(1, int(max_slots))
        self._clock = clock
        self._lock = threading.Lock()
        self._slots: Dict[str, _Slot] = {}

    # -- lifecycle ---------------------------------------------------------

    def _sweep_locked(self, now: float) -> None:
        for key in [k for k, s in self._slots.items() if s.expires_at <= now]:
            self._slots.pop(key, None)

    def _evict_locked(self) -> None:
        """Drop the oldest slots once the ceiling is reached.

        Oldest-first: a slot near the end of its life has either been collected
        already or is not going to be.
        """
        while len(self._slots) > self._max_slots:
            oldest = min(self._slots.items(), key=lambda kv: kv[1].created_at)[0]
            self._slots.pop(oldest, None)

    def open(
        self,
        invitation_id: object,
        offer_envelope: str,
        *,
        ttl_seconds: Optional[float] = None,
    ) -> str:
        """Publish an invitation: its id and the inviter's encrypted offer.

        Keeping the offer here rather than in the invite link is what lets the
        link stay short enough to render as a QR code and survive being pasted
        into a messaging app. The rendezvous still learns nothing - the offer
        arrives already encrypted under a passphrase that travels only in the
        link fragment.

        Idempotent: re-opening an existing, unexpired slot leaves it untouched
        so a client that retries does not discard an answer already waiting.
        """
        key = normalize_invitation_id(invitation_id)
        offer = str(offer_envelope or "")
        if not _looks_like_envelope(offer):
            raise RendezvousError("offer envelope is not acceptable")

        now = self._clock()
        ttl = self._ttl if ttl_seconds is None else max(30.0, float(ttl_seconds))
        with self._lock:
            self._sweep_locked(now)
            existing = self._slots.get(key)
            if existing is not None and existing.expires_at > now:
                return key
            self._slots[key] = _Slot(
                invitation_id=key,
                created_at=now,
                expires_at=now + ttl,
                offer=offer,
            )
            self._evict_locked()
        return key

    def fetch_offer(self, invitation_id: object) -> str:
        """Return the inviter's encrypted offer, or "" when nothing is waiting.

        Unknown, expired and exhausted invitations all return "" so that this
        cannot be used to discover which invitation ids exist.
        """
        key = normalize_invitation_id(invitation_id)
        now = self._clock()
        with self._lock:
            self._sweep_locked(now)
            slot = self._slots.get(key)
            if slot is None or slot.expires_at <= now:
                return ""
            slot.offer_fetches += 1
            if slot.offer_fetches > MAX_OFFER_FETCHES:
                # An invite being fetched this often is not a person tapping a
                # link. Drop it rather than keep serving.
                self._slots.pop(key, None)
                return ""
            return slot.offer

    # -- the answer leg ----------------------------------------------------

    def post_answer(self, invitation_id: object, envelope: str) -> None:
        """Store the invitee's encrypted answer for this invitation.

        Raises :class:`RendezvousError` when the invitation is unknown, expired,
        already answered, or the envelope is unacceptable. The caller is
        expected to map every one of those to a single generic client-facing
        message; distinguishing them to a poster would leak slot existence.
        """
        key = normalize_invitation_id(invitation_id)
        text = str(envelope or "")
        if not _looks_like_envelope(text):
            raise RendezvousError("answer envelope is not acceptable")

        now = self._clock()
        with self._lock:
            self._sweep_locked(now)
            slot = self._slots.get(key)
            if slot is None or slot.expires_at <= now:
                raise RendezvousError("no invitation is waiting")
            slot.answer_attempts += 1
            if slot.answer_attempts > MAX_ANSWER_ATTEMPTS:
                self._slots.pop(key, None)
                raise RendezvousError("too many answers for this invitation")
            if slot.sealed:
                # First answer wins. Without this a second poster could replace
                # an answer the inviter has not collected, which is a
                # substitution attack even though the content stays opaque.
                raise RendezvousError("this invitation already has an answer")
            slot.envelope = text
            slot.declined = False
            slot.sealed = True

    def post_decline(self, invitation_id: object) -> None:
        """Record that the invitee declined, so the inviter stops waiting."""
        key = normalize_invitation_id(invitation_id)
        now = self._clock()
        with self._lock:
            self._sweep_locked(now)
            slot = self._slots.get(key)
            if slot is None or slot.expires_at <= now:
                raise RendezvousError("no invitation is waiting")
            slot.answer_attempts += 1
            if slot.answer_attempts > MAX_ANSWER_ATTEMPTS:
                self._slots.pop(key, None)
                raise RendezvousError("too many answers for this invitation")
            if slot.sealed:
                raise RendezvousError("this invitation already has an answer")
            slot.envelope = ""
            slot.declined = True
            slot.sealed = True

    def collect(self, invitation_id: object) -> Tuple[SlotState, str]:
        """Take the answer for this invitation, exactly once.

        Returns ``(state, envelope)``. ``envelope`` is empty unless the state is
        :attr:`SlotState.READY`. Unknown, expired and unanswered invitations are
        indistinguishable :attr:`SlotState.PENDING` results.
        """
        key = normalize_invitation_id(invitation_id)
        now = self._clock()
        with self._lock:
            self._sweep_locked(now)
            slot = self._slots.get(key)
            if slot is None or slot.expires_at <= now:
                return SlotState.PENDING, ""

            slot.collect_attempts += 1
            if slot.collect_attempts > MAX_COLLECT_ATTEMPTS:
                self._slots.pop(key, None)
                return SlotState.PENDING, ""

            if not slot.sealed or slot.consumed:
                return SlotState.PENDING, ""

            slot.consumed = True
            if slot.declined:
                self._slots.pop(key, None)
                return SlotState.DECLINED, ""

            envelope = slot.envelope
            # One-shot. Removing on collection means a racing collector cannot
            # obtain a second copy of the same answer.
            self._slots.pop(key, None)
            return SlotState.READY, envelope

    def close(self, invitation_id: object) -> None:
        """Drop a slot early, e.g. when the inviter cancels the invitation."""
        try:
            key = normalize_invitation_id(invitation_id)
        except RendezvousError:
            return
        with self._lock:
            self._slots.pop(key, None)

    # -- diagnostics -------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        """Counts only. Never returns ids or envelopes."""
        now = self._clock()
        with self._lock:
            self._sweep_locked(now)
            total = len(self._slots)
            answered = sum(1 for s in self._slots.values() if s.sealed)
        return {"open_slots": total, "answered_slots": answered, "max_slots": self._max_slots}

    def constant_time_matches(self, left: object, right: object) -> bool:
        """Compare two invitation ids without leaking a prefix through timing."""
        try:
            a = normalize_invitation_id(left)
            b = normalize_invitation_id(right)
        except RendezvousError:
            return False
        return hmac.compare_digest(a, b)


#: Process-wide instance used by the server routes.
RENDEZVOUS = PeerRendezvous()


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "MAX_ANSWER_ATTEMPTS",
    "MAX_COLLECT_ATTEMPTS",
    "MAX_ENVELOPE_BYTES",
    "MAX_OFFER_FETCHES",
    "MAX_SLOTS",
    "PeerRendezvous",
    "RENDEZVOUS",
    "RendezvousError",
    "SlotState",
    "mint_invitation_id",
    "normalize_invitation_id",
]
