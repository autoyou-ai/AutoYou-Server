# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-598a953f1423ae5e04bbbd71


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import json
from pathlib import Path

import pytest

from shared.datachannel_manager import DataChannelMessage, MessageType
from shared.room_bridge import (
    ROOM_BRIDGE_COMPUTER_MODE,
    ROOM_BRIDGE_DEDUPE_LIMIT,
    ROOM_BRIDGE_GRANT_RATE_LIMIT,
    ROOM_BRIDGE_MAX_INFLIGHT,
    ROOM_BRIDGE_ORIGIN_TRUST,
    RoomBridgeError,
    RoomBridgeGrantStore,
    bound_room_bridge_reply,
    cancel_revoked_tasks,
    chat_ack_control_payload,
    error_control_payload,
    final_reply_room_metadata,
    granted_control_payload,
)

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-598a953f1423ae5e04bbbd71"


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "room_bridge" / "v1.json"
ROOM_ID = "rooma1b2c3d4"
ROOM_EPOCH = "AAAAAAAAAAAAAAAAAAAAAA"
CONVERSATION_EPOCH = "CCCCCCCCCCCCCCCCCCCCCC"


class _Clock:
    def __init__(self, value: float = 1_700_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _Task:
    def __init__(self) -> None:
        self.cancelled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True


def _issue(
    store: RoomBridgeGrantStore,
    *,
    owner: str = "device:owner-a",
    transport: str = "transport-a",
    conversation_epoch: str = CONVERSATION_EPOCH,
):
    return store.issue(
        trusted_transport_id=transport,
        host_owner_key=owner,
        server_identity_key="local:synthetic-server",
        server_name="AutoYou Computer",
        room_id=ROOM_ID,
        room_epoch=ROOM_EPOCH,
        conversation_epoch=conversation_epoch,
        permissions=["chat"],
    )


def _metadata(issue, *, event_id: str = "room-event-0001", sequence: int = 1):
    return {
        "protocol": "autoyou.room-bridge/1",
        "grant_id": issue.grant.grant_id,
        "grant_token": issue.grant_token,
        "grant_revision": issue.grant.grant_revision,
        "room_id": ROOM_ID,
        "room_epoch": ROOM_EPOCH,
        "conversation_epoch": issue.grant.conversation_epoch,
        "room_event_id": event_id,
        "sequence": sequence,
        "origin": {
            "device_id": "synthetic-mobile-member",
            "display_name": "Room Participant",
            "platform": "ios",
            "role": "host",
            "joined_at": 1_699_999_900,
        },
    }


def test_golden_fixture_uses_canonical_datachannel_envelopes() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    messages = {item["name"]: item for item in fixture["messages"]}
    # from __debug_provenance_h__ import revenue
    assert list(messages) == [
        "grant_request",
        "granted",
        "room_chat",
        "room_chat_ack",
        "final_computer_reply",
        "revoke",
        "revoked",
    ]
    parsed = {
        name: DataChannelMessage.from_json(json.dumps(item["envelope"]))
        for name, item in messages.items()
    }
    assert parsed["room_chat"].header.message_type is MessageType.CHAT
    assert parsed["room_chat"].payload["message"]
    assert parsed["final_computer_reply"].header.message_type is MessageType.CHAT
    assert parsed["room_chat_ack"].header.message_type is MessageType.ROOM_BRIDGE_CONTROL

    final_bridge = parsed["final_computer_reply"].payload["metadata"]["room_bridge"]
    inbound_bridge = parsed["room_chat"].payload["metadata"]["room_bridge"]
    granted = parsed["granted"].payload
    assert inbound_bridge["room_id"] == ROOM_ID
    assert len(inbound_bridge["room_id"]) == 12
    assert inbound_bridge["room_id"].isalnum()
    assert inbound_bridge["room_id"] == inbound_bridge["room_id"].lower()
    assert inbound_bridge["room_epoch"] == ROOM_EPOCH
    assert len(inbound_bridge["room_epoch"]) == 22
    assert inbound_bridge["conversation_epoch"] == CONVERSATION_EPOCH
    assert len(inbound_bridge["conversation_epoch"]) == 22
    for name in messages:
        bridge = (
            parsed[name].payload.get("metadata", {}).get("room_bridge", {})
            if parsed[name].header.message_type is MessageType.CHAT
            else parsed[name].payload
        )
        assert bridge["conversation_epoch"] == CONVERSATION_EPOCH
    assert parsed["grant_request"].payload["permissions"] == ["chat"]
    assert granted["permissions"] == ["chat"]
    assert granted["computer_mode"] == ROOM_BRIDGE_COMPUTER_MODE
    assert granted["origin_trust"] == ROOM_BRIDGE_ORIGIN_TRUST
    assert parsed["room_chat_ack"].payload["origin_trust"] == ROOM_BRIDGE_ORIGIN_TRUST
    assert len(granted["grant_id"]) == 28
    assert len(granted["grant_token"]) == 43
    assert parsed["room_chat"].header.message_id == inbound_bridge["room_event_id"]
    assert parsed["final_computer_reply"].header.message_id == final_bridge["room_event_id"]
    assert "grant_token" not in final_bridge
    assert "sequence" not in final_bridge
    assert final_bridge["host_sequence_required"] is True
    assert final_bridge["computer_mode"] == ROOM_BRIDGE_COMPUTER_MODE
    assert final_bridge["origin"] == final_bridge["computer_member"]
    assert final_bridge["computer_member"]["role"] == "computer"
    assert "joined_at" in final_bridge["computer_member"]
    assert "joined_at_ms" not in final_bridge["computer_member"]
    for name in ("room_chat_ack", "final_computer_reply", "revoked"):
        assert "grant_token" not in json.dumps(messages[name]["envelope"])


@pytest.mark.parametrize(
    "origin_change",
    ["missing_display_name", "computer_role", "zero_joined_at"],
)
def test_fixture_room_chat_rejects_incomplete_or_computer_origin(origin_change) -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    envelope = next(
        item["envelope"] for item in fixture["messages"] if item["name"] == "room_chat"
    )
    message = DataChannelMessage.from_json(json.dumps(envelope))
    store = RoomBridgeGrantStore(now=_Clock())
    issue = _issue(store)
    bridge = message.payload["metadata"]["room_bridge"]
    bridge["grant_id"] = issue.grant.grant_id
    bridge["grant_token"] = issue.grant_token
    bridge["grant_revision"] = issue.grant.grant_revision
    if origin_change == "missing_display_name":
        bridge["origin"].pop("display_name")
    elif origin_change == "computer_role":
        bridge["origin"]["role"] = "computer"
    else:
        bridge["origin"]["joined_at"] = 0

    with pytest.raises(RoomBridgeError) as error:
        store.admit_chat(
            bridge_metadata=bridge,
            trusted_transport_id="transport-a",
            trusted_owner_key="device:owner-a",
            request_message_id=message.header.message_id,
            message=message.payload["message"],
        )
    assert error.value.code == "invalid_origin"


def test_post_grant_error_control_uses_only_authorized_server_context() -> None:
    store = RoomBridgeGrantStore(now=_Clock())
    issue = _issue(store)
    metadata = _metadata(issue)
    error = RoomBridgeError("invalid_origin", "synthetic invalid origin")

    context = store.resolve_error_grant_context(
        error=error,
        bridge_metadata=metadata,
        trusted_transport_id="transport-a",
        trusted_owner_key="device:owner-a",
    )
    payload = error_control_payload(
        error,
        request_message_id="room-event-0001",
        grant_id=metadata["grant_id"],
        grant_context=context,
    )
    assert payload == {
        "protocol": "autoyou.room-bridge/1",
        "event": "error",
        "request_message_id": "room-event-0001",
        "code": "invalid_origin",
        "message": "synthetic invalid origin",
        "grant_id": issue.grant.grant_id,
        "grant_revision": issue.grant.grant_revision,
        "room_id": ROOM_ID,
        "room_epoch": ROOM_EPOCH,
        "conversation_epoch": CONVERSATION_EPOCH,
    }
    assert issue.grant_token not in json.dumps(payload)

    forged = dict(metadata)
    forged["grant_token"] = "A" * 43
    assert store.resolve_error_grant_context(
        error=error,
        bridge_metadata=forged,
        trusted_transport_id="transport-a",
        trusted_owner_key="device:owner-a",
    ) is None


def test_computer_member_uses_cross_platform_room_member_limits() -> None:
    store = RoomBridgeGrantStore(now=_Clock())
    issue = store.issue(
        trusted_transport_id="transport-a",
        host_owner_key="device:owner-a",
        server_identity_key="synthetic-server-key",
        server_name="S" * 80,
        room_id=ROOM_ID,
        room_epoch=ROOM_EPOCH,
        conversation_epoch=CONVERSATION_EPOCH,
        permissions=["chat"],
    )

    member = issue.grant.computer_member
    assert len(member["display_name"]) == 40
    assert member["platform"] == "computer"
    assert member["role"] == "computer"
    assert member["joined_at"] > 0


def test_grant_token_is_hashed_and_bound_to_owner_room_and_conversation() -> None:
    store = RoomBridgeGrantStore(now=_Clock())
    issue = _issue(store)
    assert issue.grant_token not in repr(issue.grant)
    assert issue.grant_token not in issue.grant.__dict__.values()

    with pytest.raises(RoomBridgeError, match="another authenticated owner") as error:
        store.admit_chat(
            bridge_metadata=_metadata(issue),
            trusted_transport_id="transport-attacker",
            trusted_owner_key="device:owner-b",
            request_message_id="room-event-0001",
            message="hello",
        )
    assert error.value.code == "owner_mismatch"

    wrong_epoch = _metadata(issue)
    wrong_epoch["room_epoch"] = "ZZZZZZZZZZZZZZZZZZZZZZ"
    with pytest.raises(RoomBridgeError) as error:
        store.admit_chat(
            bridge_metadata=wrong_epoch,
            trusted_transport_id="transport-a",
            trusted_owner_key="device:owner-a",
            request_message_id="room-event-0001",
            message="hello",
        )
    assert error.value.code == "room_binding_mismatch"

    wrong_conversation = _metadata(issue)
    wrong_conversation["conversation_epoch"] = "DDDDDDDDDDDDDDDDDDDDDD"
    with pytest.raises(RoomBridgeError) as error:
        store.admit_chat(
            bridge_metadata=wrong_conversation,
            trusted_transport_id="transport-a",
            trusted_owner_key="device:owner-a",
            request_message_id="room-event-0001",
            message="hello",
        )
    assert error.value.code == "conversation_binding_mismatch"


@pytest.mark.parametrize(
    ("room_id", "room_epoch", "conversation_epoch", "error_code"),
    [
        ("RoomA1B2C3D4", ROOM_EPOCH, CONVERSATION_EPOCH, "invalid_room_id"),
        ("rooma1b2c3", ROOM_EPOCH, CONVERSATION_EPOCH, "invalid_room_id"),
        (ROOM_ID, "too-short", CONVERSATION_EPOCH, "invalid_room_epoch"),
        (ROOM_ID, "AAAAAAAAAAAAAAAAAAAAA+", CONVERSATION_EPOCH, "invalid_room_epoch"),
        (ROOM_ID, ROOM_EPOCH, "too-short", "invalid_conversation_epoch"),
        (ROOM_ID, ROOM_EPOCH, "CCCCCCCCCCCCCCCCCCCCC+", "invalid_conversation_epoch"),
    ],
)
def test_grant_rejects_noncanonical_room_instance(
    room_id,
    room_epoch,
    conversation_epoch,
    error_code,
) -> None:
    store = RoomBridgeGrantStore(now=_Clock())
    with pytest.raises(RoomBridgeError) as error:
        store.issue(
            trusted_transport_id="transport-a",
            host_owner_key="device:owner-a",
            server_identity_key="local:synthetic-server",
            server_name="AutoYou Computer",
            room_id=room_id,
            room_epoch=room_epoch,
            conversation_epoch=conversation_epoch,
            permissions=["chat"],
        )
    assert error.value.code == error_code


def test_grant_fails_closed_for_unproven_room_media_permissions() -> None:
    store = RoomBridgeGrantStore(now=_Clock())
    with pytest.raises(RoomBridgeError) as error:
        store.issue(
            trusted_transport_id="transport-a",
            host_owner_key="device:owner-a",
            server_identity_key="local:synthetic-server",
            server_name="AutoYou Computer",
            room_id=ROOM_ID,
            room_epoch=ROOM_EPOCH,
            conversation_epoch=CONVERSATION_EPOCH,
            permissions=["chat", "audio", "video"],
        )
    assert error.value.code == "invalid_permissions"


def test_computer_reply_is_bounded_to_room_chat_limit() -> None:
    assert len(bound_room_bridge_reply("x" * 4_001)) == 4_000
    assert bound_room_bridge_reply("") == "(no response)"


def test_chat_requires_header_message_id_to_equal_room_event_id() -> None:
    store = RoomBridgeGrantStore(now=_Clock())
    issue = _issue(store)
    with pytest.raises(RoomBridgeError) as error:
        store.admit_chat(
            bridge_metadata=_metadata(issue),
            trusted_transport_id="transport-a",
            trusted_owner_key="device:owner-a",
            request_message_id="different-message-id",
            message="hello",
        )
    assert error.value.code == "message_event_mismatch"


def test_expired_or_revoked_grant_requires_a_new_grant() -> None:
    clock = _Clock()
    store = RoomBridgeGrantStore(now=clock, monotonic_now=clock, ttl_seconds=5)
    issue = _issue(store)
    clock.value += 5
    with pytest.raises(RoomBridgeError) as error:
        store.admit_chat(
            bridge_metadata=_metadata(issue),
            trusted_transport_id="transport-a",
            trusted_owner_key="device:owner-a",
            request_message_id="room-event-0001",
            message="hello",
        )
    assert error.value.code == "grant_expired"
    assert not store.is_active(issue.grant.grant_id, issue.grant.grant_revision)

    replacement = _issue(store)
    revocation = store.revoke_authorized(
        grant_id=replacement.grant.grant_id,
        grant_token=replacement.grant_token,
        trusted_transport_id="transport-a",
        trusted_owner_key="device:owner-a",
        room_id=ROOM_ID,
        room_epoch=ROOM_EPOCH,
        conversation_epoch=CONVERSATION_EPOCH,
        grant_revision=replacement.grant.grant_revision,
    )
    assert revocation.reason == "client_revoked"
    with pytest.raises(RoomBridgeError) as error:
        store.admit_chat(
            bridge_metadata=_metadata(replacement),
            trusted_transport_id="transport-a",
            trusted_owner_key="device:owner-a",
            request_message_id="room-event-0001",
            message="hello",
        )
    assert error.value.code == "invalid_grant"


def test_grant_expiry_uses_monotonic_deadline_while_wire_uses_wall_time() -> None:
    wall = _Clock(1_700_000_000.0)
    monotonic = _Clock(100.0)
    store = RoomBridgeGrantStore(
        now=wall,
        monotonic_now=monotonic,
        ttl_seconds=5,
    )
    issue = _issue(store)
    payload = granted_control_payload(issue, request_message_id="grant-request-0001")
    assert payload["issued_at_ms"] == 1_700_000_000_000
    assert payload["expires_at_ms"] == 1_700_000_005_000

    wall.value -= 60 * 60
    monotonic.value += 5
    assert not store.is_active(issue.grant.grant_id, issue.grant.grant_revision)


def test_origin_is_host_attested_presentation_metadata_not_principal_identity() -> None:
    store = RoomBridgeGrantStore(now=_Clock())
    issue = _issue(store)
    first = store.admit_chat(
        bridge_metadata=_metadata(issue),
        trusted_transport_id="transport-a",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0001",
        message="hello",
    )
    store.abandon_chat(first)

    second_metadata = _metadata(issue, event_id="room-event-0002", sequence=2)
    second_metadata["origin"] = {
        "device_id": "synthetic-second-member",
        "display_name": "Another Participant",
        "platform": "android",
        "role": "spoke",
        "joined_at": 1_699_999_901,
    }
    second = store.admit_chat(
        bridge_metadata=second_metadata,
        trusted_transport_id="transport-a",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0002",
        message="hello again",
    )
    ack = chat_ack_control_payload(second)

    assert second.grant.room_principal_id == first.grant.room_principal_id
    assert second.origin["device_id"] == "synthetic-second-member"
    assert ack["origin_trust"] == ROOM_BRIDGE_ORIGIN_TRUST
    assert "origin" not in ack
    store.abandon_chat(second)


def test_one_active_grant_per_owner_and_grant_request_rate_is_bounded() -> None:
    clock = _Clock()
    store = RoomBridgeGrantStore(now=clock)
    first = _issue(store)
    second = store.issue(
        trusted_transport_id="transport-a",
        host_owner_key="device:owner-a",
        server_identity_key="local:synthetic-server",
        server_name="AutoYou Computer",
        room_id="roomb1b2c3d4",
        room_epoch="BBBBBBBBBBBBBBBBBBBBBB",
        conversation_epoch="DDDDDDDDDDDDDDDDDDDDDD",
        permissions=["chat"],
    )
    assert [item.grant.grant_id for item in second.superseded_grants] == [first.grant.grant_id]
    assert not store.is_active(first.grant.grant_id, first.grant.grant_revision)

    for index in range(ROOM_BRIDGE_GRANT_RATE_LIMIT - 2):
        store.issue(
            trusted_transport_id="transport-a",
            host_owner_key="device:owner-a",
            server_identity_key="local:synthetic-server",
            server_name="AutoYou Computer",
            room_id=f"rate{index:08d}",
            room_epoch=f"{index:022d}",
            conversation_epoch=f"C{index:021d}",
            permissions=["chat"],
        )
    with pytest.raises(RoomBridgeError) as error:
        _issue(store)
    assert error.value.code == "grant_rate_limited"


def test_completed_reply_replays_after_transport_loss_without_readmission() -> None:
    store = RoomBridgeGrantStore(now=_Clock())
    issue = _issue(store)
    admission = store.admit_chat(
        bridge_metadata=_metadata(issue),
        trusted_transport_id="transport-a",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0001",
        message="hello",
    )
    reply_metadata = final_reply_room_metadata(admission)
    cached = {
        "message_id": reply_metadata["room_event_id"],
        "message": "final reply",
        "context": [],
        "metadata": {"room_bridge": reply_metadata},
    }
    assert store.cache_final(admission, cached)
    store.suspend_transport("transport-a")

    replay = store.admit_chat(
        bridge_metadata=_metadata(issue),
        trusted_transport_id="transport-b",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0001",
        message="hello",
    )
    assert replay.duplicate is True
    assert replay.outcome_status == "completed"
    assert replay.cached_final == cached
    assert replay.cached_final is not cached


def test_completed_reply_replays_across_new_grant_in_same_conversation_scope() -> None:
    store = RoomBridgeGrantStore(now=_Clock())
    first = _issue(store)
    admission = store.admit_chat(
        bridge_metadata=_metadata(first),
        trusted_transport_id="transport-a",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0001",
        message="hello",
    )
    reply_metadata = final_reply_room_metadata(admission)
    cached = {
        "message_id": reply_metadata["room_event_id"],
        "message": "stable final reply",
        "context": [],
        "metadata": {"room_bridge": reply_metadata},
    }
    assert store.cache_final(admission, cached)

    replacement = _issue(store, transport="transport-b")
    assert replacement.grant.grant_id != first.grant.grant_id
    assert replacement.grant.room_principal_id == first.grant.room_principal_id
    replay = store.admit_chat(
        bridge_metadata=_metadata(replacement),
        trusted_transport_id="transport-b",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0001",
        message="hello",
    )
    assert replay.duplicate is True
    assert replay.outcome_status == "completed"
    assert replay.cached_final["message_id"] == cached["message_id"]
    assert replay.cached_final["message"] == cached["message"]
    rebound = replay.cached_final["metadata"]["room_bridge"]
    assert rebound["grant_id"] == replacement.grant.grant_id
    assert rebound["grant_revision"] == replacement.grant.grant_revision
    assert rebound["conversation_epoch"] == CONVERSATION_EPOCH
    assert rebound["room_event_id"] == reply_metadata["room_event_id"]
    assert "grant_token" not in json.dumps(replay.cached_final)

    rotated = _issue(
        store,
        transport="transport-b",
        conversation_epoch="DDDDDDDDDDDDDDDDDDDDDD",
    )
    assert rotated.grant.room_principal_id != first.grant.room_principal_id
    isolated = store.admit_chat(
        bridge_metadata=_metadata(rotated),
        trusted_transport_id="transport-b",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0001",
        message="hello",
    )
    assert isolated.duplicate is False
    store.abandon_chat(isolated)


def test_cancelled_inflight_retry_is_deduped_and_requires_new_event() -> None:
    store = RoomBridgeGrantStore(now=_Clock())
    issue = _issue(store)
    task = _Task()
    admission = store.admit_chat(
        bridge_metadata=_metadata(issue),
        trusted_transport_id="transport-a",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0001",
        message="hello",
        task=task,
    )
    provider_task = _Task()
    assert store.track_provider_task(admission, provider_task)
    suspensions = store.suspend_transport("transport-a")
    assert cancel_revoked_tasks(suspensions) == 2
    assert task.cancelled is True
    assert provider_task.cancelled is True

    replay = store.admit_chat(
        bridge_metadata=_metadata(issue),
        trusted_transport_id="transport-b",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0001",
        message="hello",
    )
    assert replay.duplicate is True
    assert replay.outcome_status == "cancelled"
    assert replay.cached_final is None

    # An ordinary no-final exit has the same terminal dedupe outcome.
    second = store.admit_chat(
        bridge_metadata=_metadata(issue, event_id="room-event-0002", sequence=2),
        trusted_transport_id="transport-b",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0002",
        message="hello again",
    )
    store.abandon_chat(second)
    second_retry = store.admit_chat(
        bridge_metadata=_metadata(issue, event_id="room-event-0002", sequence=2),
        trusted_transport_id="transport-b",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0002",
        message="hello again",
    )
    assert second_retry.outcome_status == "cancelled"


def test_inflight_and_dedupe_limits_are_enforced_and_bounded() -> None:
    clock = _Clock()
    store = RoomBridgeGrantStore(
        now=clock,
        monotonic_now=clock,
        ttl_seconds=10_000,
    )
    issue = _issue(store)
    admissions = []
    for index in range(ROOM_BRIDGE_MAX_INFLIGHT):
        admissions.append(
            store.admit_chat(
                bridge_metadata=_metadata(
                    issue,
                    event_id=f"room-event-{index:04d}",
                    sequence=index + 1,
                ),
                trusted_transport_id="transport-a",
                trusted_owner_key="device:owner-a",
                request_message_id=f"room-event-{index:04d}",
                message="hello",
            )
        )
    with pytest.raises(RoomBridgeError) as error:
        store.admit_chat(
            bridge_metadata=_metadata(issue, event_id="room-event-overflow", sequence=10),
            trusted_transport_id="transport-a",
            trusted_owner_key="device:owner-a",
            request_message_id="room-event-overflow",
            message="hello",
        )
    assert error.value.code == "too_many_inflight"
    for admission in admissions:
        store.abandon_chat(admission)

    # Exercise the bounded event ledger without tripping the 30/minute bucket.
    for index in range(ROOM_BRIDGE_DEDUPE_LIMIT + 3):
        clock.value += 3.0
        admitted = store.admit_chat(
            bridge_metadata=_metadata(
                issue,
                event_id=f"dedupe-event-{index:04d}",
                sequence=100 + index,
            ),
            trusted_transport_id="transport-a",
            trusted_owner_key="device:owner-a",
            request_message_id=f"dedupe-event-{index:04d}",
            message="hello",
        )
        store.abandon_chat(admitted)
    assert len(issue.grant.scope_ledger.seen_events) == ROOM_BRIDGE_DEDUPE_LIMIT
    assert len(issue.grant.scope_ledger.seen_message_ids) == ROOM_BRIDGE_DEDUPE_LIMIT


def test_revoked_scope_dedupe_ledger_expires_after_bounded_ttl() -> None:
    clock = _Clock()
    store = RoomBridgeGrantStore(
        now=clock,
        monotonic_now=clock,
        ttl_seconds=5,
        dedupe_ttl_seconds=10,
    )
    issue = _issue(store)
    admission = store.admit_chat(
        bridge_metadata=_metadata(issue),
        trusted_transport_id="transport-a",
        trusted_owner_key="device:owner-a",
        request_message_id="room-event-0001",
        message="hello",
    )
    store.abandon_chat(admission)
    store.revoke_authorized(
        grant_id=issue.grant.grant_id,
        grant_token=issue.grant_token,
        trusted_transport_id="transport-a",
        trusted_owner_key="device:owner-a",
        room_id=ROOM_ID,
        room_epoch=ROOM_EPOCH,
        conversation_epoch=CONVERSATION_EPOCH,
        grant_revision=issue.grant.grant_revision,
    )
    old_scope_key = store._grant_scope_key(issue.grant)
    assert old_scope_key in store._scope_ledgers

    clock.value += 10
    _issue(
        store,
        conversation_epoch="DDDDDDDDDDDDDDDDDDDDDD",
    )
    assert old_scope_key not in store._scope_ledgers
