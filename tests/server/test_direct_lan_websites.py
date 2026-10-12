# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Direct home-network websites for Local Pair devices.

A Local Pair device gets, over its authenticated data channel, the page
service's HTTPS key to pin and a one-time device-pass code. Other browsers get
a code by signing in to the admin page. Everything else on the network gets
nothing.
"""

from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server
from shared import local_tls
from shared.lan_direct_access import DevicePassStore, safe_next_path, spki_sha256_b64

LAN_PEER = ("192.168.50.21", 51000)  # RFC 1918, synthetic
EPOCH = "synthetic-epoch"


# ── Pass store ────────────────────────────────────────────────────────────────

def test_codes_work_once_and_passes_expire():
    now = [1000.0]
    store = DevicePassStore(pass_ttl_seconds=100, code_ttl_seconds=10, clock=lambda: now[0])
    code = store.issue_code("device-a", epoch=EPOCH)

    token, issued = store.redeem(code, epoch=EPOCH)
    assert issued.device_key == "device-a"
    assert store.redeem(code, epoch=EPOCH) is None
    assert store.validate(token, epoch=EPOCH).device_key == "device-a"

    now[0] += 101
    assert store.validate(token, epoch=EPOCH) is None


def test_stale_codes_and_other_epochs_are_refused():
    now = [1000.0]
    store = DevicePassStore(code_ttl_seconds=10, clock=lambda: now[0])
    late = store.issue_code("device-a", epoch=EPOCH)
    now[0] += 11
    assert store.redeem(late, epoch=EPOCH) is None

    token, _ = store.redeem(store.issue_code("device-a", epoch=EPOCH), epoch=EPOCH)
    assert store.validate(token, epoch="another-epoch") is None
    assert store.validate("", epoch=EPOCH) is None


def test_revoking_a_device_ends_its_passes():
    store = DevicePassStore()
    token, _ = store.redeem(store.issue_code("device-a", epoch=EPOCH), epoch=EPOCH)
    other, _ = store.redeem(store.issue_code("device-b", epoch=EPOCH), epoch=EPOCH)
    store.revoke_device("device-a")
    assert store.validate(token, epoch=EPOCH) is None
    assert store.validate(other, epoch=EPOCH) is not None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("/agent/notes_agent/?view=all", "/agent/notes_agent/?view=all"),
        ("https://example.test/", "/websites"),
        ("//example.test/", "/websites"),
        ("/\\example.test", "/websites"),
        ("", "/websites"),
    ],
)
def test_redeem_only_continues_on_the_same_site(value, expected):
    assert safe_next_path(value) == expected


def test_pin_is_the_certificates_public_key(tmp_path):
    material = local_tls.ensure_enabled(tmp_path)
    cert = x509.load_pem_x509_certificate(material.server_cert_path.read_bytes())
    spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert spki_sha256_b64(material.server_cert_path) == base64.b64encode(hashlib.sha256(spki).digest()).decode()


def test_vpn_addresses_join_the_certificate_only_when_chosen(monkeypatch):
    monkeypatch.setattr(local_tls.socket, "getaddrinfo", lambda host, port, family: (
        [(family, None, None, "", ("192.168.50.20", 0)), (family, None, None, "", ("100.101.102.103", 0))]
        if family == local_tls.socket.AF_INET else []
    ))
    assert "100.101.102.103" not in local_tls.san_entries()[1]
    assert "100.101.102.103" in local_tls.san_entries(include_vpn=True)[1]
    assert local_tls.is_vpn_address("100.64.0.1") and not local_tls.is_vpn_address("192.168.1.2")


# ── Offer over the data channel ──────────────────────────────────────────────

@pytest.fixture
def offer_setup(monkeypatch, tmp_path):
    material = local_tls.ensure_enabled(tmp_path)
    service = SimpleNamespace(ssl_certfile=str(material.server_cert_path))
    identity = SimpleNamespace(transport="local", owner_key="local:synthetic-phone")
    monkeypatch.setattr(server.WEBRTC, "_resolve_chat_identity", lambda session_id: identity)
    monkeypatch.setattr(server.WEBRTC, "_same_machine_audio_session", lambda session_id: False)
    monkeypatch.setattr(server.WEBRTC, "device_ownership_for_session", lambda session_id: server.DEVICE_SHARED)
    monkeypatch.setattr(server, "_direct_lan_websites_target", lambda: (service, 18367, ""))
    monkeypatch.setattr(server, "_direct_lan_epoch", lambda: EPOCH)
    monkeypatch.setattr(server, "DIRECT_LAN_PASSES", DevicePassStore())
    return SimpleNamespace(identity=identity, material=material)


def test_local_pair_device_gets_the_pin_and_a_code(offer_setup):
    offer = server._direct_lan_offer("synthetic-session")

    assert offer["available"] is True
    assert offer["https_port"] == 18367
    assert offer["spki_sha256"] == spki_sha256_b64(offer_setup.material.server_cert_path)
    assert offer["redeem_path"] == "/__autoyou/device-pass"
    token, issued = server.DIRECT_LAN_PASSES.redeem(offer["code"], epoch=EPOCH)
    assert issued.device_key == "local:synthetic-phone"


@pytest.mark.parametrize("transport", ["cloud", "telegram", "bluetooth", "tunnelmole"])
def test_other_transports_keep_using_the_data_channel(offer_setup, transport):
    offer_setup.identity.transport = transport
    offer = server._direct_lan_offer("synthetic-session")
    assert offer == {"available": False, "reason": "Direct websites are for devices connected with Local Pair."}


def test_the_computer_itself_keeps_localhost(offer_setup, monkeypatch):
    monkeypatch.setattr(server.WEBRTC, "_same_machine_audio_session", lambda session_id: True)
    assert server._direct_lan_offer("synthetic-session")["available"] is False


def test_no_offer_without_home_network_https(offer_setup, monkeypatch):
    monkeypatch.setattr(server, "_direct_lan_websites_target", lambda: (None, None, "not served"))
    assert server._direct_lan_offer("synthetic-session") == {"available": False, "reason": "not served"}


# ── Browsers on other devices: sign in, then a pass ──────────────────────────

def test_signed_in_browser_is_sent_to_redeem_a_code(offer_setup, monkeypatch):
    monkeypatch.setitem(server.ADMIN_SESSIONS, "synthetic-admin", True)
    lan = TestClient(
        server.admin_app,
        base_url="https://192.168.50.20:8443",
        client=LAN_PEER,
        cookies={"admin_session": "synthetic-admin"},
        follow_redirects=False,
    )
    response = lan.get("/home-network/websites")

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("https://192.168.50.20:18367/__autoyou/device-pass?code=")
    code = location.split("code=", 1)[1].split("&", 1)[0]
    assert server.DIRECT_LAN_PASSES.redeem(code, epoch=EPOCH) is not None


def test_not_signed_in_or_plain_http_gets_no_code(offer_setup, monkeypatch):
    anonymous = TestClient(server.admin_app, base_url="https://192.168.50.20:8443", client=LAN_PEER, follow_redirects=False)
    assert anonymous.get("/home-network/websites").status_code in (302, 303, 401)

    monkeypatch.setitem(server.ADMIN_SESSIONS, "synthetic-admin", True)
    plain = TestClient(server.admin_app, base_url="http://192.168.50.20:8001", client=LAN_PEER,
                       cookies={"admin_session": "synthetic-admin"}, follow_redirects=False)
    assert plain.get("/home-network/websites").status_code == 403


def test_this_computer_opens_websites_on_localhost(monkeypatch):
    monkeypatch.setitem(server.ADMIN_SESSIONS, "synthetic-admin", True)
    local = TestClient(server.admin_app, client=("127.0.0.1", 50000),
                       cookies={"admin_session": "synthetic-admin"}, follow_redirects=False)
    response = local.get("/home-network/websites")
    assert response.status_code == 303
    assert response.headers["location"].startswith("http://127.0.0.1:")


# ── Settings ─────────────────────────────────────────────────────────────────

def test_websites_default_to_the_direct_port_with_device_passes():
    assert server._home_network_websites_mode({"server": {"bind_host": "0.0.0.0"}}) == "direct_forward"
    assert server._home_network_websites_mode({"server": {"home_network_websites": "path_proxy"}}) == "path_proxy"


def test_vpn_peers_reach_local_pair_only_when_allowed(monkeypatch):
    monkeypatch.setattr(server.STATE, "config", {"server": {}})
    assert server._peer_on_home_network("192.168.50.21") is True
    assert server._peer_on_home_network("100.101.102.103") is False
    server.STATE.config["server"]["vpn_addresses"] = True
    assert server._peer_on_home_network("100.101.102.103") is True
    assert server._peer_on_home_network("8.8.8.8") is False


# ── The data channel answers it in-process ───────────────────────────────────

@pytest.mark.asyncio
async def test_data_channel_serves_the_offer_for_its_own_session(monkeypatch):
    import json
    from unittest.mock import AsyncMock

    from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType

    seen = []
    monkeypatch.setattr(server, "_direct_lan_offer", lambda session_id: seen.append(session_id) or {"available": False, "reason": "synthetic"})
    monkeypatch.setattr(server.STATE, "config", server._default_config(), raising=False)
    webrtc = server.WebRTCManager()
    channel = SimpleNamespace(send_message=AsyncMock(return_value=True))
    webrtc.datachannel_managers["synthetic-session"] = channel
    message = DataChannelMessage(
        header=MessageHeader("synthetic-message", MessageType.HTTP_REQUEST, 0.0, "synthetic-session", "synthetic-user"),
        payload={"request_id": "synthetic-request", "method": "GET", "url": "/api/v1/direct-lan", "headers": {},
                 "direct_lan_offer": True},
    )

    await webrtc._handle_http_request(message)

    reply = channel.send_message.call_args.args[0].payload
    assert reply["status_code"] == 200
    assert json.loads(reply["body"]) == {"available": False, "reason": "synthetic"}
    assert seen == ["synthetic-session"]

    # A page shown through the channel reaches the same path, but its proxied request
    # cannot carry the app's message field: it gets no code to leak.
    del message.payload["direct_lan_offer"]
    await webrtc._handle_http_request(message)
    reply = channel.send_message.call_args.args[0].payload
    assert json.loads(reply["body"])["available"] is False
    assert seen == ["synthetic-session"]
