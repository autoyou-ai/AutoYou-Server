# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""The one unprompted thing the Computer may say, and everything it may not.

A room chat reply has to correlate to a turn the host sent, and the client
enforces that - it is what stops anything on the bridge injecting into a room.
A call listener has no such turn to answer, so it speaks through `note` instead.

That makes this payload the whole of the new surface, and these tests are the
whole of its boundary: what authorises it, what it carries, and what it refuses.
"""

from __future__ import annotations

import dataclasses

import pytest

from shared.room_bridge import (
    NOTE_KINDS,
    ROOM_BRIDGE_MAX_MESSAGE_CHARS,
    ROOM_BRIDGE_PROTOCOL,
    RoomBridgeError,
    RoomBridgeGrantStore,
    note_control_payload,
)

pytestmark = pytest.mark.server

ROOM_ID = "rooma1b2c3d4"
ROOM_EPOCH = "A" * 22
CONVERSATION_EPOCH = "C" * 22


def grant(permissions=("chat",)):
    store = RoomBridgeGrantStore()
    return store.issue(
        trusted_transport_id="transport-a",
        host_owner_key="owner-a",
        server_identity_key="identity",
        server_name="Alice's Mac",
        room_id=ROOM_ID,
        room_epoch=ROOM_EPOCH,
        conversation_epoch=CONVERSATION_EPOCH,
        permissions=list(permissions),
    ).grant


# --------------------------------------------------------------------------
# What it carries
# --------------------------------------------------------------------------

def test_a_note_is_scoped_exactly_like_every_other_control_payload():
    """A client binds it to the grant it holds; anything else is ignored."""
    live = grant()
    payload = note_control_payload(live, "You agreed on Friday.")

    assert payload["protocol"] == ROOM_BRIDGE_PROTOCOL
    assert payload["event"] == "note"
    assert payload["grant_id"] == live.grant_id
    assert payload["grant_revision"] == live.grant_revision
    assert payload["room_id"] == ROOM_ID
    assert payload["room_epoch"] == ROOM_EPOCH
    assert payload["conversation_epoch"] == CONVERSATION_EPOCH
    assert payload["message"] == "You agreed on Friday."


def test_a_note_says_which_computer_spoke():
    """The client checks this against the grant's own member."""
    live = grant()
    payload = note_control_payload(live, "hello")
    assert payload["computer_member"] == live.computer_member
    assert payload["computer_member"]["role"] == "computer"


def test_a_note_never_carries_the_grant_token():
    payload = note_control_payload(grant(), "hello")
    assert "grant_token" not in payload
    assert "token_digest" not in payload


@pytest.mark.parametrize("kind", NOTE_KINDS)
def test_both_kinds_are_accepted(kind):
    assert note_control_payload(grant(), "text", kind=kind)["kind"] == kind


def test_the_kinds_are_a_closed_set():
    """A listener speaks for two reasons. Leaving this open-ended would make
    `note` a general-purpose channel, which is the thing to avoid."""
    assert set(NOTE_KINDS) == {"reply", "summary"}


# --------------------------------------------------------------------------
# What authorises it
# --------------------------------------------------------------------------

def test_the_store_will_not_even_issue_a_grant_without_chat():
    """The first line of defence: chat is the only permission a grant may hold."""
    for permissions in ([], ["peer_media"], ["chat", "peer_media"]):
        with pytest.raises(RoomBridgeError):
            grant(permissions)


@pytest.mark.parametrize("permissions", [(), ("peer_media",)])
def test_a_note_needs_the_chat_permission(permissions):
    """Defence in depth, and reachable only by hand.

    The store refuses to issue a grant without chat, so this can only be
    produced by constructing one directly. The check stays because the grant is
    what authorises an unprompted message - if that ever became reachable, a
    note must not be the thing that discovers it.
    """
    weakened = dataclasses.replace(grant(), permissions=tuple(permissions))
    with pytest.raises(RoomBridgeError) as caught:
        note_control_payload(weakened, "hello")
    assert caught.value.code == "permission_denied"


# --------------------------------------------------------------------------
# What it refuses
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["", "   ", "command", "action", "tool_call", None])
def test_an_unknown_kind_is_refused(kind):
    with pytest.raises(RoomBridgeError) as caught:
        note_control_payload(grant(), "hello", kind=kind)
    assert caught.value.code == "invalid_note"


@pytest.mark.parametrize("text", ["", "   ", "\n\t ", None])
def test_an_empty_note_is_refused(text):
    """An empty bubble in someone's call is worse than silence."""
    with pytest.raises(RoomBridgeError) as caught:
        note_control_payload(grant(), text)
    assert caught.value.code == "invalid_note"


def test_a_long_note_is_bounded_rather_than_refused():
    """Losing the tail of a summary is recoverable; a room that rejects the
    whole message shows nothing at all."""
    payload = note_control_payload(grant(), "x" * (ROOM_BRIDGE_MAX_MESSAGE_CHARS * 3))
    assert len(payload["message"]) <= ROOM_BRIDGE_MAX_MESSAGE_CHARS


def test_the_kind_is_normalised_rather_than_trusted():
    assert note_control_payload(grant(), "hi", kind="  REPLY ")["kind"] == "reply"


def test_a_note_is_a_plain_payload_with_no_execution_surface():
    """Nothing here should ever be read as an instruction by a client."""
    payload = note_control_payload(grant(), "You agreed on Friday.")
    assert set(payload) == {
        "protocol", "event", "kind", "grant_id", "grant_revision",
        "room_id", "room_epoch", "conversation_epoch",
        "computer_member", "message",
    }
    assert isinstance(payload["message"], str)
