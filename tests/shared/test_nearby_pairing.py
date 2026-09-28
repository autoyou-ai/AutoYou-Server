# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-2dac4a0c97a492caae7df474

"""Regression coverage for the Nearby pairing flow.

Nearby is the high-trust tier, and the reason is one rule: a link exists only
when *both* people confirmed the rows matched. Everything else here protects
that rule.

The tests are written against both devices at once, because the properties that
matter are properties of the pair - that they see the same row, that one side
alone cannot complete a link, and that a mismatch stops everything.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-2dac4a0c97a492caae7df474"


import pytest

from shared.nearby_pairing import (
    MAX_DISCOVERED_PEERS,
    MAX_PEER_NAME_CHARS,
    NearbyPairingError,
    NearbyPairingSession,
    NearbyState,
    sanitize_peer_name,
)

pytestmark = pytest.mark.server


def sdp(byte: str) -> str:
    return (
        "v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\n"
        "a=fingerprint:sha-256 " + ":".join([byte] * 32) + "\r\n"
    )


ALICE_SDP = sdp("AB")
BOB_SDP = sdp("11")
MALLORY_SDP = sdp("FF")


@pytest.fixture()
def session():
    sent: list = []
    pairing = NearbyPairingSession(send=lambda peer, payload: sent.append((peer, payload)))
    pairing.begin_discovery()
    pairing.peer_discovered("peer-b", "Bob's Pixel", "android")
    pairing.sent = sent  # type: ignore[attr-defined]
    return pairing


def bring_to_verifying(session) -> list:
    session.invite("peer-b", "/peerpair\nv3.offer", ALICE_SDP)
    return session.answer_received(BOB_SDP)


# --------------------------------------------------------------------------
# The happy path, from both sides
# --------------------------------------------------------------------------

def test_initiator_reaches_a_link_only_after_both_confirmations(session):
    code = bring_to_verifying(session)
    assert session.state is NearbyState.VERIFYING
    assert len(code) == 6

    assert session.confirm_local(True) is NearbyState.VERIFYING, "one side is not enough"
    assert session.confirm_remote(True) is NearbyState.LINKED


def test_responder_can_show_the_code_immediately():
    """The responder holds both descriptions as soon as it answers."""
    sent: list = []
    responder = NearbyPairingSession(send=lambda peer, payload: sent.append((peer, payload)))
    responder.begin_discovery()
    code = responder.offer_received("peer-a", ALICE_SDP, "/peerpair_answer\nv3.answer", BOB_SDP)
    assert responder.state is NearbyState.VERIFYING
    assert len(code) == 6
    assert sent == [("peer-a", "/peerpair_answer\nv3.answer")]


def test_both_devices_see_the_same_row():
    """The whole comparison is worthless if the two rows can differ."""
    initiator = NearbyPairingSession(send=lambda peer, payload: None)
    initiator.begin_discovery()
    initiator.peer_discovered("peer-b", "Bob")
    initiator.invite("peer-b", "/peerpair\nv3.offer", ALICE_SDP)
    initiator_code = initiator.answer_received(BOB_SDP)

    responder = NearbyPairingSession(send=lambda peer, payload: None)
    responder.begin_discovery()
    responder_code = responder.offer_received("peer-a", ALICE_SDP, "/peerpair_answer\nv3.a", BOB_SDP)

    assert initiator_code == responder_code


# --------------------------------------------------------------------------
# One side alone cannot establish a link
# --------------------------------------------------------------------------

def test_a_single_confirmation_never_links(session):
    """An attacker in the middle can always confirm its own half."""
    bring_to_verifying(session)
    assert session.confirm_remote(True) is NearbyState.VERIFYING
    assert session.state is not NearbyState.LINKED


def test_a_local_mismatch_aborts_everything(session):
    bring_to_verifying(session)
    assert session.confirm_local(False) is NearbyState.FAILED
    assert "did not match" in session.failure_reason
    assert session.code == []


def test_a_remote_mismatch_aborts_everything(session):
    bring_to_verifying(session)
    assert session.confirm_remote(False) is NearbyState.FAILED


def test_a_mismatch_after_one_confirmation_still_aborts(session):
    """The failing report wins regardless of ordering."""
    bring_to_verifying(session)
    session.confirm_local(True)
    assert session.confirm_remote(False) is NearbyState.FAILED


def test_confirmations_are_refused_before_a_code_exists(session):
    """Otherwise a replayed confirmation could stand in for someone looking."""
    session.invite("peer-b", "/peerpair\nv3.offer", ALICE_SDP)
    with pytest.raises(NearbyPairingError):
        session.confirm_local(True)
    with pytest.raises(NearbyPairingError):
        session.confirm_remote(True)


def test_confirming_with_no_attempt_is_refused(session):
    with pytest.raises(NearbyPairingError):
        session.confirm_local(True)


# --------------------------------------------------------------------------
# Interception changes the row
# --------------------------------------------------------------------------

def test_a_substituted_description_produces_a_different_row(session):
    honest = bring_to_verifying(session)

    intercepted_session = NearbyPairingSession(send=lambda peer, payload: None)
    intercepted_session.begin_discovery()
    intercepted_session.peer_discovered("peer-b", "Bob")
    intercepted_session.invite("peer-b", "/peerpair\nv3.offer", ALICE_SDP)
    intercepted = intercepted_session.answer_received(MALLORY_SDP)

    assert honest != intercepted


# --------------------------------------------------------------------------
# Ordering and material handling
# --------------------------------------------------------------------------

def test_inviting_an_unknown_device_is_refused(session):
    with pytest.raises(NearbyPairingError):
        session.invite("peer-gone", "/peerpair\nv3.offer", ALICE_SDP)


def test_an_answer_without_an_invite_is_refused(session):
    with pytest.raises(NearbyPairingError):
        session.answer_received(BOB_SDP)


def test_a_responder_cannot_receive_an_answer():
    responder = NearbyPairingSession(send=lambda peer, payload: None)
    responder.begin_discovery()
    responder.offer_received("peer-a", ALICE_SDP, "/peerpair_answer\nv3.a", BOB_SDP)
    with pytest.raises(NearbyPairingError):
        responder.answer_received(ALICE_SDP)


@pytest.mark.parametrize("bad", ["", "v=0\r\nno fingerprint\r\n", "not an sdp"])
def test_descriptions_without_a_fingerprint_are_refused(session, bad):
    with pytest.raises(NearbyPairingError):
        session.invite("peer-b", "/peerpair\nv3.offer", bad)


def test_a_failed_attempt_discards_its_material(session):
    """A later attempt must not be verifiable against an earlier one's keys."""
    bring_to_verifying(session)
    session.confirm_local(False)
    assert session.peer is None
    assert session.code == []


def test_cancelling_returns_to_discovery(session):
    bring_to_verifying(session)
    session.cancel()
    assert session.state is NearbyState.DISCOVERING
    assert session.code == []


def test_beginning_discovery_clears_a_previous_attempt(session):
    bring_to_verifying(session)
    session.begin_discovery()
    assert session.state is NearbyState.DISCOVERING
    assert session.peer is None


def test_an_attempt_times_out(session):
    now = [1000.0]
    pairing = NearbyPairingSession(send=lambda peer, payload: None, clock=lambda: now[0])
    pairing.begin_discovery()
    pairing.peer_discovered("peer-b", "Bob")
    pairing.invite("peer-b", "/peerpair\nv3.offer", ALICE_SDP)
    now[0] += 10 * 60
    assert pairing.tick(at=now[0]) is NearbyState.FAILED


def test_a_linked_attempt_is_not_expired(session):
    bring_to_verifying(session)
    session.confirm_local(True)
    session.confirm_remote(True)
    assert session.tick(at=1_000_000.0) is NearbyState.LINKED


# --------------------------------------------------------------------------
# Discovery is attacker-influenced
# --------------------------------------------------------------------------

def test_the_discovery_list_is_bounded(session):
    for index in range(MAX_DISCOVERED_PEERS * 3):
        session.peer_discovered(f"peer-{index}", f"Device {index}")
    assert len(session.discovered_peers()) <= MAX_DISCOVERED_PEERS


def test_advertised_names_cannot_forge_extra_ui_lines():
    assert sanitize_peer_name("Bob\nVerified: yes") == "Bob Verified: yes"
    assert sanitize_peer_name("") == "Nearby device"
    assert len(sanitize_peer_name("x" * 200)) <= MAX_PEER_NAME_CHARS


def test_a_lost_peer_disappears(session):
    session.peer_lost("peer-b")
    assert session.discovered_peers() == []


def test_peers_without_an_id_are_ignored(session):
    assert session.peer_discovered("", "Nameless") is None
