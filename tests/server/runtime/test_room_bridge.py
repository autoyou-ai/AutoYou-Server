# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-458990457bb7cd1e7ade0f6d


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
import json
from pathlib import Path

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-458990457bb7cd1e7ade0f6d"


ensure_repo_on_path()

import rest_api
import server
from shared.chat_session_identity import bind_transport_chat_owner
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType
from shared.room_bridge import (
    ROOM_BRIDGE_GRANT_RATE_LIMIT,
    ROOM_BRIDGE_ORIGIN_TRUST,
    ROOM_BRIDGE_READ_ONLY_SYSTEM_PROMPT,
)
from shared.session_execution import (
    SessionExecutionStatus,
    SessionTurnCancelledError,
    get_session_execution_manager as get_real_execution_manager,
)


ROOM_ID = "rooma1b2c3d4"
ROOM_EPOCH = "AAAAAAAAAAAAAAAAAAAAAA"
CONVERSATION_EPOCH = "CCCCCCCCCCCCCCCCCCCCCC"
TRANSPORT_ID = "synthetic-trusted-transport"
RECONNECTED_TRANSPORT_ID = "synthetic-reconnected-transport"
FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "room_bridge" / "v1.json"


@pytest.fixture(autouse=True)
def _configure_read_only_room_backend(monkeypatch):
    config = dict(server.STATE.config or {})
    provider = dict(config.get("ai_provider") or {})
    provider["provider"] = "ollama"
    config["ai_provider"] = provider
    monkeypatch.setattr(server.STATE, "config", config)
    monkeypatch.setenv("AI_PROVIDER", "ollama")


class _DataChannel:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, message):
        self.messages.append(message)
        return True


class _ExecutionManager:
    def __init__(self, *, cancelled: bool = False) -> None:
        self.cancelled = cancelled
        self.submit_count = 0
        self._real = get_real_execution_manager()

    def resolve_transport_identity(self, transport, sender_id):
        return self._real.resolve_transport_identity(transport, sender_id)

    async def submit_turn(self, identity, handler, **_kwargs):
        self.submit_count += 1
        if self.cancelled:
            raise SessionTurnCancelledError(
                SessionExecutionStatus(
                    status="cancelled",
                    message="cancelled",
                    canonical_user_id=identity.canonical_user_id,
                    canonical_session_id=identity.canonical_session_id,
                )
            )
        return await handler()


def _issue(manager, *, transport_id=TRANSPORT_ID, conversation_epoch=CONVERSATION_EPOCH):
    trusted_owner = manager._room_bridge_trusted_owner(transport_id)
    return manager.room_bridge_grants.issue(
        trusted_transport_id=transport_id,
        host_owner_key=trusted_owner.owner_key,
        server_identity_key=server._get_server_identity_key(),
        server_name=server.get_configured_server_name(),
        room_id=ROOM_ID,
        room_epoch=ROOM_EPOCH,
        conversation_epoch=conversation_epoch,
        permissions=["chat"],
    )


def _bind_reconnect_owner() -> None:
    for transport_id in (TRANSPORT_ID, RECONNECTED_TRANSPORT_ID):
        bind_transport_chat_owner(
            "webrtc",
            "synthetic-room-owner",
            raw_session_id=transport_id,
        )


def _chat(issue, *, header_session_id="forged-client-header"):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    envelope = next(
        item["envelope"] for item in fixture["messages"] if item["name"] == "room_chat"
    )
    message = DataChannelMessage.from_json(json.dumps(envelope))
    message.header.session_id = header_session_id
    message.payload["message"] = "synthetic room prompt"
    bridge = message.payload["metadata"]["room_bridge"]
    bridge["grant_id"] = issue.grant.grant_id
    bridge["grant_token"] = issue.grant_token
    bridge["grant_revision"] = issue.grant.grant_revision
    bridge["sequence"] = 1
    return message


def _control_fixture(name):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    envelope = next(item["envelope"] for item in fixture["messages"] if item["name"] == name)
    return DataChannelMessage.from_json(json.dumps(envelope))


@pytest.mark.asyncio
async def test_full_room_bridge_ack_final_identity_and_no_token_or_streaming(monkeypatch):
    manager = server.WebRTCManager()
    _bind_reconnect_owner()
    issue = _issue(manager)
    channel = _DataChannel()
    execution = _ExecutionManager()
    captured_calls = []

    async def fake_native_ollama(message, session_id, **kwargs):
        captured_calls.append((message, session_id, kwargs))
        return {"response": "synthetic final", "model": "synthetic-model"}

    async def unexpected_agent_graph(*_args, **_kwargs):
        raise AssertionError("room bridge must not use process_chat_message")

    monkeypatch.setattr(server, "get_session_execution_manager", lambda: execution)
    monkeypatch.setattr(rest_api, "send_message_to_native_ollama", fake_native_ollama)
    monkeypatch.setattr(rest_api, "process_chat_message", unexpected_agent_graph)

    await manager._handle_room_bridge_chat(
        _chat(issue),
        trusted_transport_id=TRANSPORT_ID,
        datachannel_manager=channel,
    )

    assert [item.header.message_type for item in channel.messages] == [
        MessageType.ROOM_BRIDGE_CONTROL,
        MessageType.CHAT,
    ]
    ack, final = channel.messages
    assert ack.payload["event"] == "room_chat_ack"
    assert ack.payload["origin_trust"] == ROOM_BRIDGE_ORIGIN_TRUST
    assert final.payload["message"] == "synthetic final"
    bridge = final.payload["metadata"]["room_bridge"]
    assert final.header.message_id == bridge["room_event_id"]
    assert final.payload["metadata"]["original_message_id"] == "room-event-00000001"
    assert bridge["host_sequence_required"] is True
    assert "sequence" not in bridge
    assert "grant_token" not in final.to_json()
    assert final.payload["metadata"]["is_streaming"] is False
    message, canonical_session_id, call_kwargs = captured_calls[0]
    assert message == "synthetic room prompt"
    assert canonical_session_id.startswith("session::room:")
    assert call_kwargs["system_prompt"] == ROOM_BRIDGE_READ_ONLY_SYSTEM_PROMPT
    request_metadata = call_kwargs["metadata"]
    assert request_metadata["client_prompt_id"] == "room-event-00000001"
    assert "room_bridge" not in request_metadata
    assert "origin" not in request_metadata

    manager._suspend_room_bridge_transport(TRANSPORT_ID)
    await manager._handle_room_bridge_chat(
        _chat(issue),
        trusted_transport_id=RECONNECTED_TRANSPORT_ID,
        datachannel_manager=channel,
    )
    assert execution.submit_count == 1
    assert channel.messages[-2].payload["event"] == "room_chat_ack"
    assert channel.messages[-2].payload["duplicate"] is True
    assert channel.messages[-1].header.message_id == final.header.message_id

    replacement = _issue(manager, transport_id=RECONNECTED_TRANSPORT_ID)
    await manager._handle_room_bridge_chat(
        _chat(replacement),
        trusted_transport_id=RECONNECTED_TRANSPORT_ID,
        datachannel_manager=channel,
    )
    assert execution.submit_count == 1
    rebound_ack, rebound_final = channel.messages[-2:]
    assert rebound_ack.payload["duplicate"] is True
    assert rebound_ack.payload["grant_id"] == replacement.grant.grant_id
    assert rebound_ack.payload["conversation_epoch"] == CONVERSATION_EPOCH
    rebound_bridge = rebound_final.payload["metadata"]["room_bridge"]
    # from __debug_provenance_f__ import tenpercent
    assert rebound_final.header.message_id == final.header.message_id
    assert rebound_final.payload["message"] == final.payload["message"]
    assert rebound_bridge["grant_id"] == replacement.grant.grant_id
    assert rebound_bridge["grant_revision"] == replacement.grant.grant_revision
    assert rebound_bridge["conversation_epoch"] == CONVERSATION_EPOCH


@pytest.mark.asyncio
async def test_full_room_bridge_cancelled_turn_retry_returns_ack_and_error(monkeypatch):
    manager = server.WebRTCManager()
    _bind_reconnect_owner()
    issue = _issue(manager)
    channel = _DataChannel()
    execution = _ExecutionManager(cancelled=True)
    monkeypatch.setattr(server, "get_session_execution_manager", lambda: execution)

    message = _chat(issue)
    await manager._handle_room_bridge_chat(
        message,
        trusted_transport_id=TRANSPORT_ID,
        datachannel_manager=channel,
    )
    await manager._handle_room_bridge_chat(
        message,
        trusted_transport_id=RECONNECTED_TRANSPORT_ID,
        datachannel_manager=channel,
    )

    assert execution.submit_count == 1
    assert [item.payload["event"] for item in channel.messages] == [
        "room_chat_ack",
        "room_chat_ack",
        "error",
    ]
    assert channel.messages[-1].payload["code"] == "turn_cancelled"
    error = channel.messages[-1].payload
    assert error["request_message_id"] == message.header.message_id
    assert error["grant_id"] == issue.grant.grant_id
    assert error["grant_revision"] == issue.grant.grant_revision
    assert error["room_id"] == ROOM_ID
    assert error["room_epoch"] == ROOM_EPOCH
    assert error["conversation_epoch"] == CONVERSATION_EPOCH
    assert "grant_token" not in error


@pytest.mark.asyncio
async def test_full_post_grant_chat_and_control_errors_echo_safe_grant_tuple():
    manager = server.WebRTCManager()
    _bind_reconnect_owner()
    issue = _issue(manager)
    channel = _DataChannel()

    chat = _chat(issue)
    chat.payload["message"] = "   "
    await manager._handle_room_bridge_chat(
        chat,
        trusted_transport_id=TRANSPORT_ID,
        datachannel_manager=channel,
    )
    chat_error = channel.messages[-1].payload
    assert chat_error["code"] == "empty_message"
    assert chat_error["request_message_id"] == chat.header.message_id
    assert chat_error["grant_id"] == issue.grant.grant_id
    assert chat_error["grant_revision"] == issue.grant.grant_revision
    assert chat_error["room_id"] == ROOM_ID
    assert chat_error["room_epoch"] == ROOM_EPOCH
    assert chat_error["conversation_epoch"] == CONVERSATION_EPOCH
    assert "grant_token" not in chat_error

    revoke = _control_fixture("revoke")
    revoke.payload.update(
        {
            "grant_id": issue.grant.grant_id,
            "grant_token": issue.grant_token,
            "grant_revision": issue.grant.grant_revision + 1,
            "room_id": ROOM_ID,
            "room_epoch": ROOM_EPOCH,
            "conversation_epoch": CONVERSATION_EPOCH,
        }
    )
    await manager._handle_room_bridge_control(
        revoke,
        trusted_transport_id=TRANSPORT_ID,
        datachannel_manager=channel,
    )
    control_error = channel.messages[-1].payload
    assert control_error["code"] == "grant_revision_mismatch"
    assert control_error["request_message_id"] == revoke.header.message_id
    assert control_error["grant_id"] == issue.grant.grant_id
    assert control_error["grant_revision"] == issue.grant.grant_revision
    assert control_error["room_id"] == ROOM_ID
    assert control_error["room_epoch"] == ROOM_EPOCH
    assert control_error["conversation_epoch"] == CONVERSATION_EPOCH
    assert "grant_token" not in control_error


@pytest.mark.asyncio
async def test_full_room_bridge_rejects_unbound_transport_and_forged_bound_header():
    manager = server.WebRTCManager()
    _bind_reconnect_owner()
    channel = _DataChannel()
    request = _control_fixture("grant_request")
    request.header.session_id = TRANSPORT_ID

    await manager._handle_room_bridge_control(
        request,
        trusted_transport_id="synthetic-unbound-full-transport",
        datachannel_manager=channel,
    )

    assert len(manager.room_bridge_grants._grants) == 0
    assert channel.messages[-1].payload["event"] == "error"
    assert channel.messages[-1].payload["code"] == "unauthorized_transport"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "litellm", "openclaw", "hermes", "odysseus"])
async def test_full_room_bridge_rejects_action_capable_backend_before_grant(
    monkeypatch,
    provider,
):
    manager = server.WebRTCManager()
    _bind_reconnect_owner()
    channel = _DataChannel()
    config = dict(server.STATE.config or {})
    config["ai_provider"] = {"provider": provider}
    monkeypatch.setattr(server.STATE, "config", config)

    await manager._handle_room_bridge_control(
        _control_fixture("grant_request"),
        trusted_transport_id=TRANSPORT_ID,
        datachannel_manager=channel,
    )

    assert not manager.room_bridge_grants._grants
    assert channel.messages[-1].payload["event"] == "error"
    assert channel.messages[-1].payload["code"] == "unsupported_backend"


@pytest.mark.asyncio
async def test_full_control_limits_one_active_grant_and_rate_limits_owner():
    manager = server.WebRTCManager()
    _bind_reconnect_owner()
    channel = _DataChannel()
    manager.datachannel_managers[TRANSPORT_ID] = channel
    try:
        for index in range(ROOM_BRIDGE_GRANT_RATE_LIMIT + 1):
            request = DataChannelMessage(
                header=MessageHeader(
                    message_id=f"grant-request-{index:04d}",
                    message_type=MessageType.ROOM_BRIDGE_CONTROL,
                    timestamp=1_700_000_000.0,
                    session_id="forged-client-header",
                    user_id="forged-client-user",
                ),
                payload={
                    "protocol": "autoyou.room-bridge/1",
                    "event": "grant_request",
                    "room_id": f"full{index:08d}",
                    "room_epoch": f"F{index:021d}",
                    "conversation_epoch": f"C{index:021d}",
                    "permissions": ["chat"],
                },
            )
            await manager._handle_room_bridge_control(
                request,
                trusted_transport_id=TRANSPORT_ID,
                datachannel_manager=channel,
            )
        assert len(manager.room_bridge_grants._grants) == 1
        assert any(
            item.payload.get("event") == "revoked" and item.payload.get("reason") == "superseded"
            for item in channel.messages
        )
        # Every grant is immediately followed by the Computer's participant
        # row, so a room never holds an admitted Computer it cannot show.
        assert [item.payload.get("event") for item in channel.messages[:5]] == [
            "granted",
            "presence",
            "revoked",
            "granted",
            "presence",
        ]
        events = [item.payload.get("event") for item in channel.messages]
        for index, event in enumerate(events):
            if event == "granted":
                assert events[index + 1 : index + 2] == ["presence"], (
                    "a grant was issued without publishing the participant row"
                )
        assert channel.messages[-1].payload["event"] == "error"
        assert channel.messages[-1].payload["code"] == "grant_rate_limited"
    finally:
        expiry_tasks = list(manager.room_bridge_expiry_tasks.values())
        for task in expiry_tasks:
            task.cancel()
        await asyncio.gather(*expiry_tasks, return_exceptions=True)
        manager.room_bridge_expiry_tasks.clear()


@pytest.mark.asyncio
async def test_full_grant_publishes_a_participant_row_within_contract():
    """Admitting the Computer must also tell the room what it may do.

    Mirror of the Lite test. Both servers had `presence_control_payload` and
    `RoomCallSession` available and tested, and neither ever called them - the
    row existed only in the test suite, so no participant would have seen it.
    """
    manager = server.WebRTCManager()
    _bind_reconnect_owner()
    channel = _DataChannel()
    manager.datachannel_managers[TRANSPORT_ID] = channel
    try:
        request = _control_fixture("grant_request")
        request.payload.update(
            {
                "room_id": ROOM_ID,
                "room_epoch": ROOM_EPOCH,
                "conversation_epoch": CONVERSATION_EPOCH,
                "permissions": ["chat"],
            }
        )
        await manager._handle_room_bridge_control(
            request,
            trusted_transport_id=TRANSPORT_ID,
            datachannel_manager=channel,
        )

        assert [item.payload.get("event") for item in channel.messages] == [
            "granted",
            "presence",
        ]
        granted = channel.messages[0].payload
        published = channel.messages[1].payload
        row = published["presence"]

        for field in ("grant_id", "room_id", "room_epoch", "conversation_epoch"):
            assert published[field] == granted[field], f"{field} does not match the grant"

        assert row["capabilities"] == ["chat"]
        assert row["hears_audio"] is False
        assert row["speaks_audio"] is False
        assert row["records"] is False
        assert row["role"] == "computer"
        assert row["listening"] is False, "no call has started yet"
        assert row["notice"].strip()

        assert row["device_id"] == manager.room_bridge_grants._grants[
            granted["grant_id"]
        ].computer_member["device_id"]

        assert "grant_token" not in published
    finally:
        expiry_tasks = list(manager.room_bridge_expiry_tasks.values())
        for task in expiry_tasks:
            task.cancel()
        await asyncio.gather(*expiry_tasks, return_exceptions=True)
        manager.room_bridge_expiry_tasks.clear()


@pytest.mark.asyncio
async def test_the_call_listener_starts_and_stops_with_the_grant():
    """The grant admits the Computer, so the grant governs its listening.

    Before this, the coordinator existed and nothing attached it: the Computer
    could hold a grant and follow nothing. The revocation half matters more -
    a Computer still following a call after its admission ended is the one
    failure this design cannot have.
    """
    manager = server.WebRTCManager()
    _bind_reconnect_owner()
    channel = _DataChannel()
    manager.datachannel_managers[TRANSPORT_ID] = channel
    try:
        request = _control_fixture("grant_request")
        request.payload.update(
            {
                "room_id": ROOM_ID,
                "room_epoch": ROOM_EPOCH,
                "conversation_epoch": CONVERSATION_EPOCH,
                "permissions": ["chat"],
            }
        )
        await manager._handle_room_bridge_control(
            request,
            trusted_transport_id=TRANSPORT_ID,
            datachannel_manager=channel,
        )
        assert manager.call_listeners.is_listening(ROOM_ID), "the grant did not start it"

        granted = channel.messages[0].payload
        revoke = _control_fixture("revoke")
        revoke.payload.update(
            {
                "grant_id": granted["grant_id"],
                "grant_token": manager.room_bridge_grants._grants[granted["grant_id"]]
                and request.payload.get("grant_token"),
                "room_id": ROOM_ID,
                "room_epoch": ROOM_EPOCH,
                "conversation_epoch": CONVERSATION_EPOCH,
                "grant_revision": granted["grant_revision"],
            }
        )
        # A transport that simply goes is the commonest end of a call, and it
        # must stop the listening exactly like an explicit revoke.
        manager._suspend_room_bridge_transport(TRANSPORT_ID)
        assert not manager.call_listeners.is_listening(ROOM_ID), "it kept listening"
    finally:
        manager.call_listeners.detach_all()
        expiry_tasks = list(manager.room_bridge_expiry_tasks.values())
        for task in expiry_tasks:
            task.cancel()
        await asyncio.gather(*expiry_tasks, return_exceptions=True)
        manager.room_bridge_expiry_tasks.clear()
