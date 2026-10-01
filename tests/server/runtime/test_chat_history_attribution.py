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


def _client(manager, monkeypatch, *, captured=None, device_names=None):
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
        WEBRTC=SimpleNamespace(client_display_name_snapshot=lambda session_id: {"client_display_name": names.get(session_id, "")}),
        LOGGER=server_module.LOGGER,
    )
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
