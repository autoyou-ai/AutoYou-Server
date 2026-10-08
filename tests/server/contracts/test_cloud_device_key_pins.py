# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""AutoYou Cloud must not be able to swap a phone's key for this computer.

The pairing secret for a Cloud Pair device is derived from the public key the
cloud delivers with each request. The computer pins each device's key on its
first successful pair and refuses a different key for that device; with
approval turned on, the cloud cannot introduce a new device at all.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

import server
from shared.pairing_cpace import build_cpace_hello, complete_cpace_handshake
from shared.shared_device_pairing import credential_invitation_id, derive_authenticator, generate_key_material
from tests.server.contracts.test_server_integration_contracts import (  # noqa: F401 - fixture
    _FakeServerWebRTC,
    _build_autopair_command,
    _configure_server_router,
    _parse_autopair_reply,
    isolated_server_state,
)

SERVER_ID = "serverdevice001"
CLIENT_ID = "clientdevice001"


class _Harness:
    def __init__(self, monkeypatch, *, approval=False):
        import httpx

        self.computer = generate_key_material()
        self.posts: list[str] = []
        server.STATE.config = {
            "security": {"mode": "secure"},
            "cloud": {"server_id": SERVER_ID, "require_device_approval": approval},
        }
        server._set_config_session(
            config_store=server.CONFIG_STORE_ENCRYPTED,
            server_password=server.DEFAULT_SERVER_PASSWORD,
            config_unlock_password=server.DEFAULT_SERVER_PASSWORD,
        )
        monkeypatch.setattr(server.STATE, "used_default_password", True)
        monkeypatch.setattr(
            server, "_shared_device_server_key_material", lambda *a, **k: (self.computer.private_key, self.computer.public_key)
        )

        def update_in_memory(mutate):
            mutate(server.STATE.config.setdefault("cloud", {}))
            return True

        monkeypatch.setattr(server, "_update_cloud_device_state", update_in_memory)
        self.webrtc = _FakeServerWebRTC()
        monkeypatch.setattr(server, "pairing_router", _configure_server_router(self.webrtc))
        monkeypatch.setattr(server, "_drain_cloud_server_ice", AsyncMock())
        posts = self.posts

        class HTTP:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json):
                posts.append(json["response_payload"])
                return object()

        monkeypatch.setattr(httpx, "AsyncClient", HTTP)

    def phone(self, key):
        metadata = {
            "grant_id": "account00000001", "server_device_id": SERVER_ID, "client_device_id": CLIENT_ID,
            "server_public_key": self.computer.public_key, "client_public_key": key.public_key,
            "purpose": "bootstrap", "client_device_name": "Synthetic Phone",
        }
        password = derive_authenticator(
            key.private_key, key.public_key, self.computer.public_key,
            credential_invitation_id(metadata["grant_id"], SERVER_ID, CLIENT_ID),
        )
        return metadata, password

    async def send(self, command, payload, metadata):
        await server._handle_cloud_relay_event("relay", json.dumps({
            "relay_id": "relay-pin", "server_id": SERVER_ID, "client_device_id": CLIENT_ID,
            "command": command, "payload": payload, "shared_device_auth": metadata,
        }), "synthetic-server-token")
        return self.posts[-1]

    async def pair(self, key):
        metadata, password = self.phone(key)
        hello, handshake = build_cpace_hello(password, purpose="autopair")
        answer = await self.send("/autopair_hello", hello, metadata)
        if not answer.startswith("/autopair_hello_answer"):
            return json.loads(answer)
        session = complete_cpace_handshake(answer.split("\n", 1)[1], handshake)
        offer = _build_autopair_command(
            security_mode="secure", password=password, offer={"type": "offer", "sdp": "v=0\r\n"}, session=session
        )
        response = await self.send("/autopair", offer, metadata)
        if response.lstrip().startswith("{"):
            return json.loads(response)
        return _parse_autopair_reply(response, security_mode="secure", session=session)


def test_first_pair_pins_the_key_and_a_swapped_key_is_refused(isolated_server_state, monkeypatch):
    harness = _Harness(monkeypatch)
    phone, impostor = generate_key_material(), generate_key_material()

    async def run():
        first = await harness.pair(phone)
        assert first["answer"]["type"] == "answer"
        assert server._cloud_device_pins()[CLIENT_ID]["public_key"] == phone.public_key

        swapped = await harness.pair(impostor)
        assert swapped["error"] == server.CLOUD_DEVICE_KEY_CHANGED_MESSAGE
        assert len(harness.webrtc.autopair_calls) == 1

        again = await harness.pair(phone)
        assert again["answer"]["type"] == "answer"

    asyncio.run(run())


def test_approval_mode_keeps_new_devices_out_until_the_owner_approves(isolated_server_state, monkeypatch):
    harness = _Harness(monkeypatch, approval=True)
    phone = generate_key_material()

    async def run():
        refused = await harness.pair(phone)
        assert refused["error"] == server.CLOUD_DEVICE_APPROVAL_REQUIRED_MESSAGE
        assert harness.webrtc.autopair_calls == []
        pending = server._cloud_device_pending()[CLIENT_ID]
        assert pending["public_key"] == phone.public_key and pending["name"] == "Synthetic Phone"

        server._pin_shared_device_client(
            {"client_public_key": pending["public_key"], "client_device_name": pending["name"]}, CLIENT_ID
        )
        assert CLIENT_ID not in server._cloud_device_pending()
        approved = await harness.pair(phone)
        assert approved["answer"]["type"] == "answer"

    asyncio.run(run())


def test_refused_device_cannot_feed_ice_candidates(isolated_server_state, monkeypatch):
    harness = _Harness(monkeypatch, approval=True)
    metadata, _ = harness.phone(generate_key_material())
    calls = []

    async def record(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(server.pairing_router, "process_message", record)
    asyncio.run(server._handle_cloud_relay_event("relay", json.dumps({
        "relay_id": "relay-pin", "server_id": SERVER_ID, "client_device_id": CLIENT_ID,
        "command": "/autopair_candidates", "payload": "[]", "shared_device_auth": metadata,
    }), "synthetic-server-token"))
    assert calls == []
    assert harness.posts == []


def test_owner_device_routes_need_a_session_and_refuse_connected_browsers(isolated_server_state, monkeypatch):
    from fastapi.testclient import TestClient

    server.STATE.config = {"cloud": {"shared_device_client_pins": {
        CLIENT_ID: {"public_key": "synthetic-public-key-value", "name": "Synthetic Phone", "pinned_at": 1}
    }}}
    local = TestClient(server.admin_app, client=("127.0.0.1", 50000))
    assert local.get("/api/cloud/devices").status_code == 401

    monkeypatch.setitem(server.ADMIN_SESSIONS, "synthetic-cloud-session", True)
    signed_in = TestClient(
        server.admin_app, client=("127.0.0.1", 50000), cookies={"admin_session": "synthetic-cloud-session"}
    )
    listing = signed_in.get("/api/cloud/devices").json()
    assert listing["devices"][0]["device_id"] == CLIENT_ID
    assert listing["devices"][0]["key_fingerprint"] == "synthetic-pu"

    proxied = signed_in.post(
        f"/api/cloud/devices/{CLIENT_ID}/forget",
        headers={"X-AutoYou-WebRTC-Session-Id": "synthetic-webrtc", "Origin": "http://testserver"},
    )
    assert proxied.status_code == 403
