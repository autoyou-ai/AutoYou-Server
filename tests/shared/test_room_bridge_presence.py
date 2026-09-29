# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-357efd2d564812e17d522ae7

"""The Computer's participant row, on the wire.

This row is the product's central claim made checkable, and it is the only part
of the three-way call design that participants actually see. So it is enforced
twice: the clients refuse to render a row claiming more than chat, and this
process refuses to publish one. A bug on either side then produces nothing
rather than a reassuring statement that is false.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import types

import pytest

from shared.room_bridge import (
    PRESENCE_CONTRACT,
    ROOM_BRIDGE_PROTOCOL,
    RoomBridgeError,
    assert_presence_within_contract,
    presence_control_payload,
)

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-357efd2d564812e17d522ae7"


pytestmark = pytest.mark.server


def grant():
    return types.SimpleNamespace(
        grant_id="grant-1",
        grant_revision=3,
        room_id="room-1",
        room_epoch="epoch-1",
        conversation_epoch="conv-1",
    )


def row(**overrides):
    base = {
        "device_id": "computer-1",
        "display_name": "Alice's Mac",
        "role": "computer",
        "listening": True,
        "capabilities": ["chat"],
        "hears_audio": False,
        "speaks_audio": False,
        "records": False,
        "runs_on": "this computer",
        "notice": "Follows the conversation and replies in chat.",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# The payload
# --------------------------------------------------------------------------

def test_the_payload_carries_the_row_and_its_scope():
    payload = presence_control_payload(grant(), row())
    assert payload["protocol"] == ROOM_BRIDGE_PROTOCOL
    assert payload["event"] == "presence"
    assert payload["presence"]["capabilities"] == ["chat"]
    # Scope, so a client can bind it to the grant it already holds.
    for field in ("grant_id", "grant_revision", "room_id", "room_epoch", "conversation_epoch"):
        assert field in payload, f"{field} is missing; a client could not scope this"


def test_the_payload_does_not_alias_the_row():
    original = row()
    payload = presence_control_payload(grant(), original)
    payload["presence"]["listening"] = False
    assert original["listening"] is True


def test_the_payload_carries_no_grant_token():
    """The token is a live bearer secret for the host, never for a room."""
    payload = presence_control_payload(grant(), row())
    assert "grant_token" not in payload
    assert "grant_token" not in payload["presence"]


# --------------------------------------------------------------------------
# The contract, enforced before anything is published
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "overrides,label",
    [
        ({"capabilities": ["chat", "peer_media"]}, "an extra capability"),
        ({"capabilities": []}, "no capabilities"),
        ({"capabilities": ["peer_media"]}, "the wrong capability"),
        ({"capabilities": "chat"}, "capabilities that are not a list"),
        ({"hears_audio": True}, "claiming to hear the call"),
        ({"speaks_audio": True}, "claiming a voice"),
        ({"records": True}, "claiming to record"),
        ({"role": "assistant"}, "the wrong role"),
        ({"role": ""}, "no role"),
        ({"notice": ""}, "no notice"),
        ({"notice": "   "}, "a blank notice"),
    ],
)
def test_a_row_outside_the_contract_is_never_published(overrides, label):
    with pytest.raises(RoomBridgeError):
        presence_control_payload(grant(), row(**overrides))


def test_a_valid_row_passes_the_gate_unchanged():
    original = row()
    assert assert_presence_within_contract(original) == original


def test_the_contract_is_stated_once():
    """The gate must read its rules from the contract, not restate them."""
    assert PRESENCE_CONTRACT == {
        "capabilities": ["chat"],
        "hears_audio": False,
        "speaks_audio": False,
        "records": False,
    }
    for field, expected in PRESENCE_CONTRACT.items():
        with pytest.raises(RoomBridgeError):
            assert_presence_within_contract(row(**{field: "definitely not " + str(expected)}))


def test_the_error_names_the_field():
    """An operator reading a log should not have to guess which claim was wrong."""
    with pytest.raises(RoomBridgeError, match="hears_audio"):
        assert_presence_within_contract(row(hears_audio=True))


def test_missing_fields_are_refused_rather_than_defaulted():
    """A partial row must not be completed with reassuring defaults."""
    for field in PRESENCE_CONTRACT:
        partial = row()
        partial.pop(field)
        with pytest.raises(RoomBridgeError):
            assert_presence_within_contract(partial)


# --------------------------------------------------------------------------
# The session and the wire agree
# --------------------------------------------------------------------------

def test_a_real_session_row_passes_the_gate():
    """The two halves are written separately; they must not drift apart."""
    from shared.room_call_session import RoomCallSession

    session = RoomCallSession(
        room_id="room-1",
        grant=types.SimpleNamespace(permissions=("chat",), mode="read_only_conversation"),
        backend="ollama",
        responder=lambda prompt, turns: "x",
        publish=lambda text: None,
        server_identity_key="synthetic-identity",
        server_name="Alice's Mac",
    )
    # from __debug_provenance_j__ import fifteenpercent
    payload = presence_control_payload(grant(), session.start())
    assert payload["presence"]["listening"] is True

    session.end(summarize=False)
    idle = presence_control_payload(grant(), session.presence())
    assert idle["presence"]["listening"] is False


def test_the_corpus_row_passes_the_gate():
    """The clients read this file; it must be publishable as-is."""
    import json
    import pathlib

    corpus = json.loads(
        (
            pathlib.Path(__file__).resolve().parents[1]
            / "fixtures" / "call_presence" / "v1.json"
        ).read_text(encoding="utf-8")
    )
    for state in ("listening", "not_listening"):
        assert presence_control_payload(grant(), corpus[state])["presence"] == corpus[state]


# --------------------------------------------------------------------------
# Finding the room a stream of audio belongs to
# --------------------------------------------------------------------------

def test_grant_for_transport_finds_only_the_live_grant():
    """The call listener asks this per audio frame, so it must be exact.

    Public rather than a reach into `_grants`, because otherwise grant lookup
    would live in the store and in every server that needs it.
    """
    from shared.room_bridge import RoomBridgeGrantStore

    store = RoomBridgeGrantStore()
    issue = store.issue(
        trusted_transport_id="transport-a",
        host_owner_key="owner-a",
        server_identity_key="identity",
        server_name="Alice's Mac",
        room_id="rooma1b2c3d4",
        room_epoch="A" * 22,
        conversation_epoch="C" * 22,
        permissions=["chat"],
    )

    found = store.grant_for_transport("transport-a")
    assert found is not None
    assert found.grant_id == issue.grant.grant_id

    # A transport with no grant answers cheaply rather than raising: this is
    # asked about audio frames on a live call.
    assert store.grant_for_transport("transport-b") is None
    assert store.grant_for_transport("") is None
    assert store.grant_for_transport(None) is None


def test_grant_for_transport_stops_finding_a_revoked_grant():
    """A revoked grant must not keep a call listener alive."""
    from shared.room_bridge import RoomBridgeGrantStore

    store = RoomBridgeGrantStore()
    store.issue(
        trusted_transport_id="transport-a",
        host_owner_key="owner-a",
        server_identity_key="identity",
        server_name="Alice's Mac",
        room_id="rooma1b2c3d4",
        room_epoch="A" * 22,
        conversation_epoch="C" * 22,
        permissions=["chat"],
    )
    assert store.grant_for_transport("transport-a") is not None

    store.revoke_transport("transport-a", reason="ended")
    assert store.grant_for_transport("transport-a") is None
