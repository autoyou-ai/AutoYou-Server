# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Chat & History says who each conversation is with, and who said each turn."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import rest_api
import server as server_module
from session_utils import MemoryIntegratedSessionManager
from shared import session_execution
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType
from shared.session_execution import SessionExecutionManager

PHONE = "user::local:Phone-1"
PEER = "user::peer:" + "0" * 64


def _manager(tmp_path):
    return MemoryIntegratedSessionManager(db_path=str(tmp_path / "sessions.db"), adk_session_service=object())


def _index_turn(manager, event_id, user_id, session_id, metadata, *, when, message="hello"):
    record = MemoryIntegratedSessionManager._build_memory_search_record(
        event_id=event_id,
        user_id=user_id,
        session_id=f"internal-{session_id}",
        event_type="chat_interaction",
        external_session_id=session_id,
        event_data={
            "user_message": message,
            "agent_response": "reply",
            "timestamp": f"2026-09-30T10:00:{when:02d}",
            "memory_metadata": metadata,
        },
    )
    manager._upsert_memory_search_record(record)


async def _async_value(value):
    return value


class _FakeAdk:
    def __init__(self, events):
        self._events = events

    async def get_session(self, **kwargs):
        return SimpleNamespace(state={}, events=self._events)


def _adk_turn(event_id, data):
    return SimpleNamespace(id=event_id, timestamp=1_790_000_000.0, actions=SimpleNamespace(state_delta={"event_data_raw": data}))


def _client(manager, monkeypatch, *, captured=None, device_names=None, webrtc=None, conversations=False):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from routers import admin as admin_routes

    async def fake_process_chat_message(chat_request, *_args, **_kwargs):
        if captured is not None:
            captured["request"] = chat_request
        return SimpleNamespace(response="ok", session_id=chat_request.session_id, metadata={})

    monkeypatch.setattr(rest_api, "get_session_manager", lambda: manager)
    names = device_names or {}
    fake_server = SimpleNamespace(
        _require_api_login=lambda request: None,
        _json_response_no_store=server_module._json_response_no_store,
        _request_via_remote_browser_proxy=server_module._request_via_remote_browser_proxy,
        _is_loopback_client_host=server_module._is_loopback_client_host,
        get_configured_server_name=lambda: "Test Server",
        get_session_info=lambda user_id, session_id: _async_value({"session_id": session_id}),
        _speech_config=lambda: {},
        ChatRequest=rest_api.ChatRequest,
        process_chat_message=fake_process_chat_message,
        AI_AGENT_SERVER_PORT=8081,
        WEBRTC=webrtc or SimpleNamespace(client_display_name_snapshot=lambda session_id: {"client_display_name": names.get(session_id, "")}),
        _resolve_conversation_identity=lambda identity: identity,
        LOGGER=server_module.LOGGER,
    )
    if conversations:
        # The server's own conversation threads and client-facing ids.
        fake_server._resolve_conversation_identity = server_module._resolve_conversation_identity
        fake_server._build_conversation_metadata = server_module._build_conversation_metadata
    admin_app = fastapi.FastAPI()
    admin_routes.register_routes(admin_app, fastapi.FastAPI(), fake_server)
    return TestClient(admin_app)


def test_history_attributes_every_conversation_and_names_the_relaying_device(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "root"))
    manager = _manager(tmp_path)
    _index_turn(manager, "e1", "admin-web-user", "admin-chat-1", {"client": "admin-web", "source": "admin_web", "admin_surface": "this_computer"}, when=1)
    _index_turn(manager, "e2", PHONE, "session::local:Phone-1", {"client": "ios", "client_display_name": "Kitchen iPhone"}, when=2)
    # The owner continued the phone's conversation from this page: still the phone's.
    _index_turn(manager, "e3", PHONE, "session::local:Phone-1", {"client": "admin-web", "source": "admin_web", "admin_surface": "this_computer"}, when=3)
    _index_turn(manager, "e4", PEER, "session::peer:" + "0" * 64, {"client": "android", "peer_relay": {"platform": "android", "hop": 1, "via_user_id": PHONE}}, when=4)
    _index_turn(manager, "e5", "autoyou-mcp", "mcp-1", {"client": "chatgpt", "source": "autoyou-mcp"}, when=5)
    _index_turn(manager, "e6", "autoyou-mcp", "mcp-1", {"client": "admin-web", "source": "admin_web", "admin_surface": "this_computer"}, when=6)

    listing = _client(manager, monkeypatch).get("/api/chat/sessions").json()
    rows = {row["user_id"]: row for row in listing["sessions"]}

    assert rows["admin-web-user"]["identity"] == {"kind": "self", "name": "Test Server", "detail": "You · This computer", "is_self": True}
    assert rows[PHONE]["identity"]["kind"] == "device"
    assert rows[PHONE]["identity"]["name"] == "Kitchen iPhone"
    assert rows[PHONE]["origin"] == "Kitchen iPhone · Local pair"
    assert rows[PEER]["identity"] == {
        "kind": "peer",
        "name": "Peer Relay guest",
        "detail": "Android · via Kitchen iPhone",
        "is_self": False,
    }
    assert rows["autoyou-mcp"]["identity"]["kind"] == "connector"
    assert listing["viewer"]["surface"] == "this_computer"
    assert listing["viewer"]["name"] == "Test Server"
    assert not any(key.startswith("_") for row in listing["sessions"] for key in row)


def test_self_card_reflects_secure_remote_web_and_the_live_device(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "root"))
    client = _client(_manager(tmp_path), monkeypatch, device_names={"synthetic-session": "Kitchen iPhone"})

    remote = client.get(
        "/api/chat/sessions",
        headers={"X-AutoYou-Remote-Browser": "webrtc", "X-AutoYou-WebRTC-Session-Id": "synthetic-session"},
    ).json()["viewer"]
    home = client.get("/api/chat/sessions", headers={"X-AutoYou-Remote-Browser": "home_network"}).json()["viewer"]
    tunnel = client.get("/api/chat/sessions", headers={"X-AutoYou-Tunnel-Client-Ip": "203.0.113.9"}).json()["viewer"]

    assert remote == {
        "user_id": "admin-web-user",
        "name": "Test Server",
        "surface": "secure_remote_web",
        "surface_label": "Secure Remote Web",
        "device_name": "Kitchen iPhone",
    }
    assert home["surface"] == "home_network" and "device_name" not in home
    assert tunnel["surface"] == "network"


def test_admin_chat_stamps_where_the_owner_typed_and_ignores_the_page(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "root"))
    captured = {}
    client = _client(_manager(tmp_path), monkeypatch, captured=captured)

    response = client.post(
        "/api/chat",
        json={"message": "hi", "session_id": "admin-chat-2", "metadata": {"admin_surface": "this_computer"}},
        headers={"X-AutoYou-Remote-Browser": "webrtc"},
    )

    assert response.status_code == 200
    metadata = captured["request"].metadata
    assert metadata["admin_surface"] == "secure_remote_web"
    assert captured["request"].user_id == "admin-web-user"
    assert rest_api._build_memory_metadata(captured["request"], ai_agent_session_id="a", response_metadata={})["admin_surface"] == "secure_remote_web"


def test_transcript_marks_the_owners_turns_inside_someone_elses_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "root"))
    manager = _manager(tmp_path)
    manager.adk_session_service = _FakeAdk([
        _adk_turn("chat_interaction_1", {"user_message": "from the phone", "agent_response": "a", "memory_metadata": {"client": "ios"}}),
        _adk_turn("chat_interaction_2", {"user_message": "from the owner", "agent_response": "b", "memory_metadata": {"client": "admin-web", "source": "admin_web", "admin_surface": "secure_remote_web"}}),
    ])

    detail = _client(manager, monkeypatch).get("/api/chat/session", params={"user_id": PHONE, "session_id": "session::local:Phone-1"}).json()

    assert detail["identity"]["kind"] == "device"
    assert detail["identity"]["name"] == "iPhone / iPad"
    assert [(item["role"], item.get("author")) for item in detail["messages"]] == [
        ("user", "counterpart"),
        ("assistant", None),
        ("user", "self"),
        ("assistant", None),
    ]


class _LiveDevices:
    """A stand-in for the devices connected right now."""

    def __init__(self, sessions, *, accepts=True):
        self._sessions = sessions
        self._accepts = accepts
        self.sent = []

    def _unique_datachannel_manager_entries(self, *, require_send_message=False):
        return [(session_id, object()) for session_id in self._sessions]

    def _resolve_chat_identity(self, session_id):
        user_id, conversation = self._sessions[session_id]
        return SimpleNamespace(canonical_user_id=user_id, canonical_session_id=conversation)

    async def send_chat_to_session(self, session_id, message, *, metadata=None, user_id=None, queue_if_undelivered=True):
        self.sent.append((session_id, message, dict(metadata or {}), user_id, queue_if_undelivered))
        return self._accepts


def test_an_owner_reply_reaches_the_connected_device_and_is_kept_as_the_owners(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "root"))
    manager = _manager(tmp_path)
    saved = []

    async def add_session_event(**kwargs):
        saved.append(kwargs)
        return True

    monkeypatch.setattr(manager, "add_session_event", add_session_event)
    monkeypatch.setattr(manager, "get_mapped_session_id", lambda session_id, user_id=None: "internal-" + session_id)
    devices = _LiveDevices({"synthetic-live": (PHONE, "session::local:Phone-1::2")})
    captured = {}
    client = _client(manager, monkeypatch, captured=captured, webrtc=devices)

    response = client.post("/api/chat/session/reply", json={"user_id": PHONE, "message": "  On my way  "}).json()

    assert response == {"success": True, "delivered": True, "user_id": PHONE, "session_id": "session::local:Phone-1::2"}
    # Sent to the device as the owner, never put to the AI, and never left
    # to arrive later if it cannot be sent now.
    assert devices.sent == [(
        "synthetic-live",
        "On my way",
        {"source": "owner_reply", "human_reply": True, "agent_display_name": "Test Server"},
        "Test Server",
        False,
    )]
    assert "request" not in captured
    assert len(saved) == 1
    assert (saved[0]["user_id"], saved[0]["session_id"], saved[0]["external_session_id"]) == (
        PHONE, "internal-session::local:Phone-1::2", "session::local:Phone-1::2")
    event = saved[0]["event_data"]
    assert (event["user_message"], event["agent_response"]) == ("", "On my way")
    assert event["memory_metadata"] == {"source": "owner_reply", "human_reply": True, "admin_surface": "this_computer"}


def test_an_owner_reply_nobody_received_is_neither_claimed_nor_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "root"))
    manager = _manager(tmp_path)
    saved = []

    async def add_session_event(**kwargs):
        saved.append(kwargs)
        return True

    monkeypatch.setattr(manager, "add_session_event", add_session_event)

    away = _client(manager, monkeypatch, webrtc=_LiveDevices({"synthetic-live": ("user::local:Someone-else", "s")}))
    missed = away.post("/api/chat/session/reply", json={"user_id": PHONE, "message": "hello"}).json()
    assert (missed["success"], missed["delivered"]) == (True, False)
    assert "not connected" in missed["reason"]

    dropped_devices = _LiveDevices({"synthetic-live": (PHONE, "session::local:Phone-1")}, accepts=False)
    dropped = _client(manager, monkeypatch, webrtc=dropped_devices).post(
        "/api/chat/session/reply", json={"user_id": PHONE, "message": "hello"}).json()
    assert dropped["delivered"] is False and len(dropped_devices.sent) == 1

    # A relayed guest has no connection of its own; the owner's own chat is not a device.
    assert away.post("/api/chat/session/reply", json={"user_id": PEER, "message": "hello"}).json()["delivered"] is False
    assert away.post("/api/chat/session/reply", json={"user_id": "admin-web-user", "message": "hello"}).status_code == 400
    assert away.post("/api/chat/session/reply", json={"user_id": PHONE, "message": "   "}).status_code == 400
    assert away.post("/api/chat/session/reply", json={"user_id": PHONE, "message": "x" * 4001}).status_code == 413
    assert saved == []


def test_history_shows_an_owner_reply_as_theirs_and_says_which_devices_are_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "root"))
    manager = _manager(tmp_path)
    manager.adk_session_service = _FakeAdk([
        _adk_turn("chat_interaction_1", {"user_message": "are you there?", "agent_response": "The assistant answered.", "memory_metadata": {"client": "ios"}}),
        _adk_turn("chat_interaction_2", {"user_message": "", "agent_response": "On my way", "memory_metadata": {"source": "owner_reply", "human_reply": True, "admin_surface": "this_computer"}}),
        # A device can write human_reply, but never the surface only this server stamps.
        _adk_turn("chat_interaction_3", {"user_message": "forged", "agent_response": "pretend", "memory_metadata": {"client": "ios", "human_reply": True}}),
    ])
    _index_turn(manager, "e1", PHONE, "session::local:Phone-1", {"client": "ios"}, when=1)
    _index_turn(manager, "e2", "user::local:Tablet-2", "session::local:Tablet-2", {"client": "android"}, when=2)
    client = _client(manager, monkeypatch, webrtc=_LiveDevices({"synthetic-live": (PHONE, "session::local:Phone-1")}))

    detail = client.get("/api/chat/session", params={"user_id": PHONE, "session_id": "session::local:Phone-1"}).json()
    rows = {row["user_id"]: row for row in client.get("/api/chat/sessions").json()["sessions"]}

    assert [(item["role"], item.get("author"), item.get("human")) for item in detail["messages"]] == [
        ("user", "counterpart", None),
        ("assistant", None, None),
        ("assistant", "self", True),
        ("user", "counterpart", None),
        ("assistant", None, None),
    ]
    assert detail["identity"]["kind"] == "device"
    assert "truncated" not in detail
    assert rows[PHONE]["live"] is True
    assert rows["user::local:Tablet-2"]["live"] is False

    # A reader may ask for only the newest turns; the title still comes from the first.
    for limit in ("2", "0", "many", "99"):
        latest = client.get("/api/chat/session", params={"user_id": PHONE, "session_id": "session::local:Phone-1", "limit": limit}).json()
        expected = 2 if limit == "2" else 5
        assert (len(latest["messages"]), latest.get("truncated", False)) == (expected, limit == "2"), limit
        assert latest["title"] == "are you there?"
    assert [item["content"] for item in client.get(
        "/api/chat/session", params={"user_id": PHONE, "session_id": "session::local:Phone-1", "limit": "2"},
    ).json()["messages"]] == ["forged", "pretend"]


class _OpenChannel:
    """A connected device's data channel."""

    def __init__(self):
        self.accepts = True
        self.sent = []

    async def send_message(self, message):
        self.sent.append(message)
        return self.accepts


def _connected_phone(tmp_path, monkeypatch):
    """The real engine, identity registry and history store, with one phone paired and connected."""
    sessions = pytest.importorskip("google.adk.sessions")
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "root"))
    monkeypatch.setattr(session_execution, "_GLOBAL_SESSION_EXECUTION_MANAGER", SessionExecutionManager())
    manager = MemoryIntegratedSessionManager(
        db_path=str(tmp_path / "sessions.db"),
        adk_session_service=sessions.InMemorySessionService(),
        cognee_memory_enabled=False,
    )
    monkeypatch.setattr(server_module, "_get_conversation_session_manager", lambda: manager)
    webrtc = server_module.WebRTCManager()
    identity = server_module.bind_transport_chat_owner("local", "Phone-1", raw_session_id="synthetic-live")
    channel = _OpenChannel()
    webrtc.datachannel_managers["synthetic-live"] = channel
    return manager, webrtc, identity, channel


async def _store_device_turn(manager, session_id):
    manager.set_session_mapping(session_id, "internal-" + session_id, PHONE)
    assert await manager.add_session_event(
        user_id=PHONE,
        session_id="internal-" + session_id,
        event_type="chat_interaction",
        event_data={
            "user_message": "are you there?",
            "agent_response": "The assistant answered.",
            "timestamp": "2026-10-01T10:00:00",
            "memory_metadata": {"client": "ios"},
        },
        external_session_id=session_id,
    )


@pytest.mark.asyncio
async def test_an_owner_reply_follows_the_device_into_its_current_conversation(tmp_path, monkeypatch):
    manager, webrtc, identity, channel = _connected_phone(tmp_path, monkeypatch)
    assert identity.canonical_user_id == PHONE
    await _store_device_turn(manager, "session::local:Phone-1")
    # The phone has since started a new conversation.
    manager.advance_conversation_thread(identity.owner_key)
    current = server_module._resolve_conversation_identity(webrtc._resolve_chat_identity("synthetic-live"))
    as_an_ai_reply = server_module._build_conversation_metadata(current)

    import anyio

    client = _client(manager, monkeypatch, webrtc=webrtc, conversations=True)
    response = await anyio.to_thread.run_sync(
        lambda: client.post("/api/chat/session/reply", json={"user_id": PHONE, "message": "On my way"}).json())

    assert response == {"success": True, "delivered": True, "user_id": PHONE, "session_id": "session::local:Phone-1::2"}
    [message] = channel.sent
    metadata = message.payload["metadata"]
    assert (message.payload["message"], message.header.user_id) == ("On my way", "Test Server")
    assert (metadata["source"], metadata["human_reply"], metadata["agent_display_name"]) == ("owner_reply", True, "Test Server")
    # Addressed exactly as an AI reply to that conversation is. A device drops
    # a message addressed to a conversation it has moved on from.
    assert metadata["conversation_session_id"] == as_an_ai_reply["conversation_session_id"]
    assert metadata["conversation_session_id"].endswith("::thread::2")
    assert metadata["conversation_thread_id"] == 2
    assert metadata["conversation_session_id"] != current.canonical_session_id

    listing, detail = await anyio.to_thread.run_sync(lambda: (
        client.get("/api/chat/sessions").json(),
        client.get("/api/chat/session", params={"user_id": PHONE, "session_id": "session::local:Phone-1::2"}).json(),
    ))
    rows = {row["session_id"]: row for row in listing["sessions"]}
    assert set(rows) == {"session::local:Phone-1", "session::local:Phone-1::2"}
    assert rows["session::local:Phone-1::2"]["preview"] == "On my way"
    assert all(row["live"] for row in rows.values())
    assert [(item["role"], item.get("author"), item.get("human"), item["content"]) for item in detail["messages"]] == [
        ("assistant", "self", True, "On my way"),
    ]


@pytest.mark.asyncio
async def test_an_owner_reply_that_cannot_be_sent_never_arrives_later(tmp_path, monkeypatch):
    manager, webrtc, _identity, channel = _connected_phone(tmp_path, monkeypatch)
    await _store_device_turn(manager, "session::local:Phone-1")

    import anyio

    client = _client(manager, monkeypatch, webrtc=webrtc, conversations=True)

    def reply():
        return client.post("/api/chat/session/reply", json={"user_id": PHONE, "message": "On my way"}).json()

    def history():
        return client.get("/api/chat/sessions").json()["sessions"]

    # The channel refuses the message: the owner is told, and it is dropped.
    channel.accepts = False
    refused = await anyio.to_thread.run_sync(reply)
    assert refused["delivered"] is False and "could not be delivered" in refused["reason"]
    assert len(channel.sent) == 1
    assert webrtc._offline_pending_messages == {}

    # A channel that has closed is not a connected device at all.
    channel.connection_active = False
    closed = await anyio.to_thread.run_sync(reply)
    assert closed["delivered"] is False and "not connected" in closed["reason"]
    assert len(channel.sent) == 1
    rows = await anyio.to_thread.run_sync(history)
    assert [(row["live"], row["message_count"], row["preview"]) for row in rows] == [(False, 1, "The assistant answered.")]

    # Every other sender keeps the engine's default: an undelivered message waits.
    channel.connection_active = True
    assert await webrtc.send_chat_to_session("synthetic-live", "A reminder") is False
    assert [len(waiting) for waiting in webrtc._offline_pending_messages.values()] == [1]


def test_the_pairing_answer_names_the_owner_history_lists_a_device_by(monkeypatch):
    import pairing_router

    monkeypatch.setattr(session_execution, "_GLOBAL_SESSION_EXECUTION_MANAGER", SessionExecutionManager())
    monkeypatch.setattr(server_module, "_get_conversation_session_manager", lambda: None)
    identity = server_module.bind_transport_chat_owner("local", "Desktop-1", raw_session_id="synthetic-live")
    stated = server_module._build_client_session_identity_payload(identity, pairing_mode="local_pair")
    answer = pairing_router.PairingRouter._build_autopair_answer_payload(
        {"type": "answer", "sdp": "v=0", **stated}, "synthetic-live")

    # What a chat turn from that connection is stored under, and so what
    # Chat & History lists it by.
    stored_as = server_module.WebRTCManager._resolve_chat_identity("synthetic-live")
    assert answer["canonical_user_id"] == stored_as.canonical_user_id == "user::local:Desktop-1"
    # The conversation id a client is given is its own, and is not the one
    # history files the conversation under.
    assert answer["conversation_session_id"] != stored_as.canonical_session_id
    assert answer["canonical_session_id"] == stored_as.canonical_session_id


def test_a_request_carried_by_a_tunnel_is_not_an_app_on_this_computer():
    from starlette.datastructures import Headers

    def request(host, headers=None):
        return SimpleNamespace(client=SimpleNamespace(host=host), headers=Headers(headers or {}))

    assert server_module._is_same_machine_audio_client(request("127.0.0.1")) is True
    assert server_module._local_pair_device_ownership(request("127.0.0.1")) == "own"
    for header in ("x-autoyou-tunnel-client-ip", "x-forwarded-for", "cf-connecting-ip", "forwarded"):
        carried = request("127.0.0.1", {header: "203.0.113.9"})
        assert server_module._is_same_machine_audio_client(carried) is False, header
        assert server_module._local_pair_device_ownership(carried) == "shared", header
    assert server_module._is_same_machine_audio_client(request("127.0.0.1", {"x-autoyou-remote-browser": "webrtc"})) is False
    assert server_module._local_pair_device_ownership(request("203.0.113.9")) == "shared"


def test_memory_metadata_keeps_only_bounded_attribution():
    request = rest_api.ChatRequest(
        message="hi",
        user_id=PEER,
        session_id="s",
        metadata={
            "peer_relay": {"name": "Sam's Pixel", "platform": "android", "hop": 1, "relay_token": "dropped"},
            "admin_surface": "root",
        },
    )

    kept = rest_api._build_memory_metadata(request, ai_agent_session_id="a", response_metadata={})

    assert kept["peer_relay"] == {"hop": 1, "platform": "android", "name": "Sam's Pixel"}
    assert "admin_surface" not in kept


class _ExecutingSessionManager(SessionExecutionManager):
    async def submit_turn(self, identity, factory, **_kwargs):
        return await factory()


async def _run_chat(monkeypatch, metadata, *, names_in_history):
    webrtc = server_module.WebRTCManager()
    captured = {}
    relay_owner = SimpleNamespace(
        canonical_session_id="session::local:Phone-1",
        canonical_user_id=PHONE,
        owner_key="local:Phone-1",
        raw_session_id="synthetic-relay",
    )

    class _Channel:
        async def send_message(self, _message):
            return True

    async def fake_process_chat_message(chat_request, **_kwargs):
        captured["request"] = chat_request
        return SimpleNamespace(response="synthetic reply", metadata={})

    monkeypatch.setattr(
        server_module.STATE,
        "config",
        {
            "ai_agent": {"record_messages_in_database": True},
            "client_identity": {"store_client_names_in_history": names_in_history, "name_overrides": {}},
        },
    )
    monkeypatch.setattr(webrtc, "_resolve_chat_identity", lambda _session_id: relay_owner)
    monkeypatch.setattr(webrtc, "client_name_history_metadata", lambda _identity: {"client_display_name": "Kitchen iPhone"} if names_in_history else {})
    monkeypatch.setattr(server_module, "get_session_execution_manager", lambda: _ExecutingSessionManager())
    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)
    webrtc.datachannel_managers["synthetic-relay"] = _Channel()

    await webrtc._handle_chat_message(
        DataChannelMessage(
            header=MessageHeader(
                message_id="p1~synthetic-message",
                message_type=MessageType.CHAT,
                timestamp=0.0,
                session_id="synthetic-relay",
                user_id="peer:synthetic-guest",
            ),
            payload={"message": "hello", "context": [], "metadata": metadata},
        )
    )
    return captured["request"]


def _relayed(name="Sam's Pixel"):
    entry = {"peer_id": "synthetic-guest", "name": name, "platform": "android", "hop": 1}
    return {"client": "android", "relay_path": [entry], "relay_origin": entry}


@pytest.mark.asyncio
async def test_relayed_guest_turn_carries_its_relay_name_when_names_are_kept(monkeypatch):
    request = await _run_chat(monkeypatch, _relayed(), names_in_history=True)

    assert request.user_id.startswith("user::peer:")
    assert request.metadata["peer_relay"] == {
        "hop": 1,
        "platform": "android",
        "name": "Sam's Pixel",
        "via_name": "Kitchen iPhone",
        "via_user_id": PHONE,
    }


@pytest.mark.asyncio
async def test_relayed_guest_name_stays_live_only_by_default(monkeypatch):
    request = await _run_chat(monkeypatch, _relayed(), names_in_history=False)

    assert request.metadata["peer_relay"] == {"hop": 1, "platform": "android", "via_user_id": PHONE}


@pytest.mark.asyncio
async def test_a_direct_client_cannot_claim_relay_or_owner_attribution(monkeypatch):
    forged = {"client": "ios", "peer_relay": {"name": "Somebody Else", "hop": 1}, "admin_surface": "this_computer"}

    request = await _run_chat(monkeypatch, forged, names_in_history=True)

    assert request.user_id == PHONE
    assert "peer_relay" not in request.metadata
    assert "admin_surface" not in request.metadata
