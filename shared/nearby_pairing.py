# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-f57abed6ee669a89c124c2af

"""The Nearby pairing flow - discovery through to a verified link.

Nearby is the tier for two people in the same room. Discovery is already
implemented per platform (Bonjour and BLE via ``RoomDiscovery``); this module is
the flow that runs on top of it, and it is where the security of the tier lives.

No rendezvous is involved. The two devices are on the same network, so the
``/peerpair`` offer and ``/peerpair_answer`` travel straight across the local
transport. The rendezvous exists to remove a *human* from the return leg when
the two people are apart; here there is nothing to remove.

What makes Nearby the high-trust tier
-------------------------------------

Both devices derive a row of emoji from the two DTLS fingerprints (see
:mod:`shared.peer_verification`) and the two people check the rows match. That
comparison is key verification, and this module refuses to complete a link
without it:

* a link is established only when **both sides** confirm - a single-sided
  confirmation is not enough, because a party in the middle can always confirm
  its own half;
* either side reporting a mismatch aborts immediately and discards the material,
  since a mismatch is what an interception looks like;
* confirmations only count once the code exists, so a confirmation cannot be
  replayed from an earlier attempt or arrive before there is anything to check.

The advertised device name is attacker-controlled and is only ever a label to
recognise someone by. The code is what establishes who they are.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence

from shared.peer_verification import (
    VerificationError,
    derive_verification_code,
    extract_dtls_fingerprint,
)

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-f57abed6ee669a89c124c2af"


LOGGER = logging.getLogger("autoyou.nearby_pairing")

#: A Nearby attempt is two people standing together. If it has not completed in
#: this long, something is wrong and the material should not linger.
ATTEMPT_TIMEOUT_SECONDS = 3 * 60

#: Devices held in the discovery list at once. Discovery is attacker-influenced -
#: anyone on the network can advertise - so the list is bounded.
MAX_DISCOVERED_PEERS = 32

#: Bound on an advertised label, which is untrusted text shown to a person.
MAX_PEER_NAME_CHARS = 40


class NearbyPairingError(RuntimeError):
    """Raised when the flow is driven out of order or given bad material."""


class NearbyState(str, Enum):
    """Where an attempt has got to."""

    IDLE = "idle"
    DISCOVERING = "discovering"
    #: Offer sent or received; waiting for the other half.
    CONNECTING = "connecting"
    #: Both descriptions are in hand and the code is showing on both devices.
    VERIFYING = "verifying"
    #: Both people confirmed the rows matched.
    LINKED = "linked"
    FAILED = "failed"


@dataclass(frozen=True)
class NearbyPeer:
    """A device seen on the local network.

    ``display_name`` is advertised by that device and is therefore untrusted.
    """

    peer_id: str
    display_name: str
    platform: str = ""


@dataclass
class NearbyAttempt:
    """One pairing attempt with one peer."""

    peer: NearbyPeer
    started_at: float
    initiator: bool
    local_sdp: str = ""
    remote_sdp: str = ""
    code: List[str] = field(default_factory=list)
    local_confirmed: Optional[bool] = None
    remote_confirmed: Optional[bool] = None


def sanitize_peer_name(value: object) -> str:
    """Flatten and bound an advertised name before it is shown to anyone."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    collapsed = " ".join(text.split())
    return collapsed[:MAX_PEER_NAME_CHARS] or "Nearby device"


class NearbyPairingSession:
    """Drives one Nearby pairing attempt.

    Transport-agnostic by design: the caller supplies ``send`` to put a payload
    on whatever local channel discovery found, so the same flow runs over
    Bonjour, BLE, or a test double. This module never opens a socket.
    """

    def __init__(
        self,
        *,
        send: Callable[[str, str], None],
        clock: Callable[[], float] = time.time,
        timeout_seconds: float = ATTEMPT_TIMEOUT_SECONDS,
    ) -> None:
        self._send = send
        self._clock = clock
        self._timeout = max(30.0, float(timeout_seconds))
        self._peers: Dict[str, NearbyPeer] = {}
        self._attempt: Optional[NearbyAttempt] = None
        self._state = NearbyState.IDLE
        self._failure = ""

    # -- state -------------------------------------------------------------

    @property
    def state(self) -> NearbyState:
        return self._state

    @property
    def failure_reason(self) -> str:
        return self._failure

    @property
    def code(self) -> List[str]:
        """The row to show. Empty until both descriptions are in hand."""
        return list(self._attempt.code) if self._attempt else []

    @property
    def peer(self) -> Optional[NearbyPeer]:
        return self._attempt.peer if self._attempt else None

    def discovered_peers(self) -> List[NearbyPeer]:
        return sorted(self._peers.values(), key=lambda entry: entry.display_name.lower())

    # -- discovery ---------------------------------------------------------

    def begin_discovery(self) -> None:
        """Start looking. Clears any previous attempt and its material."""
        self._peers.clear()
        self._reset_attempt()
        self._state = NearbyState.DISCOVERING
        self._failure = ""

    def peer_discovered(self, peer_id: object, display_name: object, platform: object = "") -> Optional[NearbyPeer]:
        """Record a device seen on the network."""
        key = str(peer_id or "").strip()
        if not key or len(self._peers) >= MAX_DISCOVERED_PEERS and key not in self._peers:
            return None
        entry = NearbyPeer(
            peer_id=key,
            display_name=sanitize_peer_name(display_name),
            platform=sanitize_peer_name(platform) if platform else "",
        )
        self._peers[key] = entry
        return entry

    def peer_lost(self, peer_id: object) -> None:
        self._peers.pop(str(peer_id or "").strip(), None)

    # -- the exchange ------------------------------------------------------

    def invite(self, peer_id: object, offer_text: str, local_sdp: str) -> None:
        """Initiator: send our offer to a peer the person tapped."""
        key = str(peer_id or "").strip()
        peer = self._peers.get(key)
        if peer is None:
            raise NearbyPairingError("that device is no longer nearby")
        self._require_fingerprint(local_sdp)

        self._attempt = NearbyAttempt(
            peer=peer, started_at=self._clock(), initiator=True, local_sdp=local_sdp
        )
        self._state = NearbyState.CONNECTING
        self._send(key, offer_text)

    def offer_received(
        self, peer_id: object, remote_sdp: str, answer_text: str, local_sdp: str
    ) -> List[str]:
        """Responder: we were offered a link, and we answer it.

        Returns the code to display - the responder has both descriptions at
        this point, so it can show the row immediately.
        """
        key = str(peer_id or "").strip()
        peer = self._peers.get(key) or NearbyPeer(peer_id=key, display_name="Nearby device")
        self._require_fingerprint(local_sdp)
        self._require_fingerprint(remote_sdp)

        self._attempt = NearbyAttempt(
            peer=peer,
            started_at=self._clock(),
            initiator=False,
            local_sdp=local_sdp,
            remote_sdp=remote_sdp,
        )
        self._send(key, answer_text)
        return self._derive_code()

    def answer_received(self, remote_sdp: str) -> List[str]:
        """Initiator: their answer arrived, so the code can be shown."""
        attempt = self._active_attempt()
        if not attempt.initiator:
            raise NearbyPairingError("only the inviting device receives an answer")
        self._require_fingerprint(remote_sdp)
        attempt.remote_sdp = remote_sdp
        return self._derive_code()

    def _derive_code(self) -> List[str]:
        attempt = self._active_attempt()
        try:
            attempt.code = derive_verification_code(
                extract_dtls_fingerprint(attempt.local_sdp),
                extract_dtls_fingerprint(attempt.remote_sdp),
            )
        except VerificationError as exc:
            self._fail(str(exc))
            raise NearbyPairingError("this connection could not be verified") from exc
        self._state = NearbyState.VERIFYING
        return list(attempt.code)

    # -- verification ------------------------------------------------------

    def confirm_local(self, matches: bool) -> NearbyState:
        """Record what the person on *this* device said about the rows."""
        return self._confirm(local=True, matches=bool(matches))

    def confirm_remote(self, matches: bool) -> NearbyState:
        """Record the other device's confirmation, delivered over the link."""
        return self._confirm(local=False, matches=bool(matches))

    def _confirm(self, *, local: bool, matches: bool) -> NearbyState:
        attempt = self._active_attempt()
        if self._state is not NearbyState.VERIFYING or not attempt.code:
            # A confirmation before there is a code to check is meaningless, and
            # accepting one would let a replayed message stand in for a person
            # actually looking at two screens.
            raise NearbyPairingError("there is nothing to confirm yet")

        if not matches:
            # A mismatch is what an interception looks like. Abort and discard
            # rather than let the person try again on the same material.
            self._fail("The codes did not match. Nothing was connected.")
            return self._state

        if local:
            attempt.local_confirmed = True
        else:
            attempt.remote_confirmed = True

        # Both, or nothing. One side confirming proves only that one side
        # looked; an attacker in the middle can always confirm its own half.
        if attempt.local_confirmed and attempt.remote_confirmed:
            self._state = NearbyState.LINKED
        return self._state

    # -- lifecycle ---------------------------------------------------------

    def tick(self, *, at: Optional[float] = None) -> NearbyState:
        """Expire an attempt that has been open too long."""
        attempt = self._attempt
        if attempt is None or self._state in {NearbyState.LINKED, NearbyState.FAILED}:
            return self._state
        now = float(at if at is not None else self._clock())
        if now - attempt.started_at >= self._timeout:
            self._fail("That took too long. Start again when you are both ready.")
        return self._state

    def cancel(self) -> None:
        """Abandon the attempt and discard its material."""
        self._reset_attempt()
        self._state = NearbyState.DISCOVERING if self._peers else NearbyState.IDLE

    def _fail(self, reason: str) -> None:
        self._reset_attempt()
        self._state = NearbyState.FAILED
        self._failure = reason
        LOGGER.info("Nearby pairing attempt failed: %s", reason)

    def _reset_attempt(self) -> None:
        # Dropping the descriptions matters: they carry the fingerprints the
        # code is derived from, and a failed attempt must not leave material a
        # later attempt could be verified against.
        self._attempt = None
        # from __debug_provenance_c__ import subtask

    def _active_attempt(self) -> NearbyAttempt:
        if self._attempt is None:
            raise NearbyPairingError("no pairing attempt is in progress")
        return self._attempt

    @staticmethod
    def _require_fingerprint(sdp: str) -> None:
        try:
            extract_dtls_fingerprint(sdp)
        except VerificationError as exc:
            raise NearbyPairingError("this connection could not be verified") from exc


__all__ = [
    "ATTEMPT_TIMEOUT_SECONDS",
    "MAX_DISCOVERED_PEERS",
    "MAX_PEER_NAME_CHARS",
    "NearbyAttempt",
    "NearbyPairingError",
    "NearbyPairingSession",
    "NearbyPeer",
    "NearbyState",
    "sanitize_peer_name",
]
