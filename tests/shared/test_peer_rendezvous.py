# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-746f20706179203130252061-154dbafec6f507e0c9a6f5f0

"""Regression coverage for the Peer Link blind rendezvous.

The rendezvous carries the half of a pairing handshake that used to travel
through a person's clipboard. Because it sits on an unauthenticated surface,
its security rests entirely on four properties, and each has a test here:

* it cannot read what it carries;
* it cannot be enumerated;
* an answer can be delivered once and substituted never;
* it is bounded in size, count and lifetime.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-746f20706179203130252061-154dbafec6f507e0c9a6f5f0"


import pytest

from shared.peer_rendezvous import (
    MAX_ANSWER_ATTEMPTS,
    MAX_ENVELOPE_BYTES,
    MAX_OFFER_FETCHES,
    PeerRendezvous,
    RendezvousError,
    SlotState,
    mint_invitation_id,
)

pytestmark = pytest.mark.server

OFFER = "/peerpair\nv3.abc.tag.body"
ANSWER = "/peerpair_answer\nv3.abc.tag.answerbody"


@pytest.fixture()
def rendezvous():
    return PeerRendezvous()


@pytest.fixture()
def invitation(rendezvous):
    iid = mint_invitation_id()
    rendezvous.open(iid, OFFER)
    return iid


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------

def test_full_exchange(rendezvous, invitation):
    assert rendezvous.fetch_offer(invitation) == OFFER
    assert rendezvous.collect(invitation) == (SlotState.PENDING, "")
    rendezvous.post_answer(invitation, ANSWER)
    assert rendezvous.collect(invitation) == (SlotState.READY, ANSWER)


def test_decline_is_reported_so_the_inviter_stops_waiting(rendezvous, invitation):
    rendezvous.post_decline(invitation)
    state, envelope = rendezvous.collect(invitation)
    assert state is SlotState.DECLINED
    assert envelope == ""


def test_open_is_idempotent_and_preserves_a_waiting_answer(rendezvous, invitation):
    rendezvous.post_answer(invitation, ANSWER)
    rendezvous.open(invitation, OFFER)  # a client retrying its publish
    assert rendezvous.collect(invitation) == (SlotState.READY, ANSWER)


# --------------------------------------------------------------------------
# One-shot delivery and substitution
# --------------------------------------------------------------------------

def test_answer_is_delivered_exactly_once(rendezvous, invitation):
    rendezvous.post_answer(invitation, ANSWER)
    assert rendezvous.collect(invitation)[0] is SlotState.READY
    assert rendezvous.collect(invitation) == (SlotState.PENDING, "")


def test_a_second_answer_cannot_replace_the_first(rendezvous, invitation):
    """The substitution attack: overwrite an answer before it is collected."""
    rendezvous.post_answer(invitation, ANSWER)
    with pytest.raises(RendezvousError):
        rendezvous.post_answer(invitation, "/peerpair_answer\nv3.abc.tag.attacker")
    assert rendezvous.collect(invitation) == (SlotState.READY, ANSWER)


def test_a_decline_cannot_overwrite_a_real_answer(rendezvous, invitation):
    rendezvous.post_answer(invitation, ANSWER)
    with pytest.raises(RendezvousError):
        rendezvous.post_decline(invitation)


def test_answer_attempts_are_capped(rendezvous, invitation):
    rendezvous.post_answer(invitation, ANSWER)
    for _ in range(MAX_ANSWER_ATTEMPTS + 2):
        with pytest.raises(RendezvousError):
            rendezvous.post_answer(invitation, ANSWER)
    # Past the cap the slot is dropped entirely rather than kept under attack.
    assert rendezvous.collect(invitation) == (SlotState.PENDING, "")


# --------------------------------------------------------------------------
# Non-enumeration
# --------------------------------------------------------------------------

def test_unknown_expired_and_unanswered_are_indistinguishable(rendezvous, invitation):
    """A collector must not be able to tell which invitation ids exist."""
    unknown = rendezvous.collect(mint_invitation_id())
    unanswered = rendezvous.collect(invitation)
    assert unknown == unanswered == (SlotState.PENDING, "")


def test_fetching_an_unknown_offer_returns_nothing_rather_than_raising(rendezvous):
    assert rendezvous.fetch_offer(mint_invitation_id()) == ""


def test_expired_invitations_disappear(rendezvous):
    now = [1000.0]
    r = PeerRendezvous(ttl_seconds=60, clock=lambda: now[0])
    iid = mint_invitation_id()
    r.open(iid, OFFER)
    r.post_answer(iid, ANSWER)
    now[0] += 61
    assert r.collect(iid) == (SlotState.PENDING, "")
    assert r.fetch_offer(iid) == ""


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    ["", "  ", "short", "has spaces", "has/slash", "a" * 200, None, 12345],
)
def test_malformed_invitation_ids_are_refused(rendezvous, bad):
    with pytest.raises(RendezvousError):
        rendezvous.open(bad, OFFER)


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("", id="empty"),
        pytest.param("no-leading-slash\nv3.body", id="missing-command-line"),
        pytest.param("/peerpair\nline2\nline3", id="too-many-lines"),
        pytest.param("/peerpair\nv3\x00nul", id="control-character"),
    ],
)
def test_malformed_envelopes_are_refused(rendezvous, bad):
    iid = mint_invitation_id()
    with pytest.raises(RendezvousError):
        rendezvous.open(iid, bad)


def test_oversized_envelopes_are_refused(rendezvous):
    # Built here rather than in a parametrize id: a multi-hundred-kilobyte test
    # id overflows the environment variable pytest records it in.
    oversized = "/peerpair\n" + "a" * (MAX_ENVELOPE_BYTES + 10)
    with pytest.raises(RendezvousError):
        rendezvous.open(mint_invitation_id(), oversized)


def test_answer_envelope_is_validated_too(rendezvous, invitation):
    with pytest.raises(RendezvousError):
        rendezvous.post_answer(invitation, "not an envelope")


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------

def test_offer_fetches_are_capped(rendezvous, invitation):
    for _ in range(MAX_OFFER_FETCHES):
        assert rendezvous.fetch_offer(invitation) == OFFER
    assert rendezvous.fetch_offer(invitation) == ""


def test_slot_count_is_bounded(rendezvous):
    r = PeerRendezvous(max_slots=8)
    ids = [mint_invitation_id() for _ in range(40)]
    for iid in ids:
        r.open(iid, OFFER)
    assert r.stats()["open_slots"] <= 8


def test_stats_never_leak_ids_or_payloads(rendezvous, invitation):
    rendezvous.post_answer(invitation, ANSWER)
    stats = rendezvous.stats()
    assert set(stats) == {"open_slots", "answered_slots", "max_slots"}
    assert all(isinstance(v, int) for v in stats.values())


def test_close_removes_a_slot(rendezvous, invitation):
    rendezvous.close(invitation)
    assert rendezvous.collect(invitation) == (SlotState.PENDING, "")
    rendezvous.close("not-a-valid-id")  # must not raise


def test_constant_time_id_comparison(rendezvous):
    a = mint_invitation_id()
    assert rendezvous.constant_time_matches(a, a) is True
    assert rendezvous.constant_time_matches(a, mint_invitation_id()) is False
    assert rendezvous.constant_time_matches(a, "bad id") is False
