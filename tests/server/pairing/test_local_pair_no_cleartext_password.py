# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Local Pair must never need the server password sent over the network.

Clients used to POST the password to /login over plain HTTP before CPace ran.
The pairing endpoints now accept a session-less caller only for a completed
CPace exchange from this computer or the private network, in Secure mode, and
not while the public factory password is active.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import itertools

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server
from shared.pairing_cpace import (
    build_cpace_hello,
    complete_cpace_handshake,
    decrypt_cpace_message,
    encrypt_cpace_message,
)
from shared.pairing_pake import build_pake_request

LAN_PEER = "192.168.50.21"  # RFC 1918, synthetic
# Python's ipaddress treats the documentation ranges (TEST-NET) as private, so a
# public caller needs a routable address; this is a public DNS resolver, not a person.
PUBLIC_PEER = "8.8.8.8"
SYNTHETIC_PASSWORD = "synthetic-local-pair-Password-7"
_SENDER_IDS = (f"synthetic-local-pair-client-{index}" for index in itertools.count())
SENDER_ID = "synthetic-local-pair-client"


@pytest.fixture(autouse=True)
def _fresh_sender_id():
    # The pairing router keeps CPace sessions per sender; a fresh id per test
    # keeps one test's handshake from changing how the next is parsed.
    global SENDER_ID
    SENDER_ID = next(_SENDER_IDS)


class _FakeRequest:
    def __init__(self, body, *, host=LAN_PEER, headers=None):
        self.client = SimpleNamespace(host=host)
        raw = {"X-AutoYou-Platform": "local", "X-AutoYou-Session-Id": SENDER_ID, **(headers or {})}
        self.headers = Headers(raw)
        self.query_params = {"session_id": SENDER_ID}
        self.cookies = {}
        self.url = SimpleNamespace(path="/api/autopair", scheme="http")
        self._body = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")

    async def body(self) -> bytes:
        return self._body


@pytest.fixture
def secure_server(monkeypatch):
    """A Secure-mode server with an owner-chosen password and no admin session."""
    original = (
        server.STATE.config,
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )
    cfg = server._default_config()
    cfg.setdefault("security", {})["mode"] = "secure"
    server.STATE.config = cfg
    server._set_config_session(
        config_store=server.CONFIG_STORE_NONE,
        server_password=SYNTHETIC_PASSWORD,
        config_unlock_password=None,
    )
    monkeypatch.setattr(server.STATE, "used_default_password", False, raising=False)
    monkeypatch.setattr(server, "AUTOPAIR_RATE_LIMITER", server.RateLimiter(max_requests=50, window_seconds=60))
    monkeypatch.setattr(
        server, "LOCAL_PAIR_NETWORK_GLOBAL_RATE_LIMITER", server.RateLimiter(max_requests=50, window_seconds=60)
    )
    captured = {}

    async def fake_handle_autopair_offer(sender_id, payload):
        captured["sender_id"] = sender_id
        captured["payload"] = dict(payload)
        return {"type": "answer", "sdp": "v=0\r\n", "server_name": "Synthetic Server"}

    monkeypatch.setattr(server.WEBRTC, "handle_autopair_offer", fake_handle_autopair_offer)
    try:
        yield captured
    finally:
        server.STATE.config = original[0]
        server._set_config_session(
            config_store=original[3],
            server_password=original[1],
            config_unlock_password=original[2],
        )


def _hello(password=SYNTHETIC_PASSWORD, **request_kwargs):
    envelope, handshake = build_cpace_hello(password, purpose="autopair")
    response = asyncio.run(server.autopair_hello_api(_FakeRequest(envelope, **request_kwargs)))
    return response, handshake


def _offer_payload():
    return {
        "hash": server._server_generate_hash(SYNTHETIC_PASSWORD),
        "offer": {"type": "offer", "sdp": "v=0"},
        "iceServers": [],
        "_autoyou_pairing_platform": "local",
        "_autoyou_sender_id": SENDER_ID,
    }


def test_lan_local_pair_completes_with_cpace_and_no_session(secure_server):
    response, handshake = _hello()
    assert response.status_code == 200, response.body
    session = complete_cpace_handshake(response.body.decode("utf-8").split("\n", 1)[1], handshake)

    encrypted = encrypt_cpace_message(json.dumps(_offer_payload()), session)
    answer = asyncio.run(server.autopair_api(_FakeRequest(encrypted)))

    assert answer.status_code == 200, answer.body
    text = answer.body.decode("utf-8")
    assert text.startswith("/autopair_answer\n")
    decrypted = json.loads(decrypt_cpace_message(text.split("\n", 1)[1], session))
    assert decrypted["answer"]["type"] == "answer"
    # Pairing from another device is shared, never the owner's own device.
    assert secure_server["payload"]["_autoyou_device_ownership"] == server.DEVICE_SHARED


def test_wrong_password_cannot_finish_the_handshake(secure_server):
    response, handshake = _hello(password="synthetic-wrong-password-1")
    assert response.status_code == 200
    session = complete_cpace_handshake(response.body.decode("utf-8").split("\n", 1)[1], handshake)
    encrypted = encrypt_cpace_message(json.dumps(_offer_payload()), session)

    answer = asyncio.run(server.autopair_api(_FakeRequest(encrypted)))

    assert answer.status_code == 400
    assert "sender_id" not in secure_server


def test_session_less_plaintext_offer_is_refused(secure_server):
    answer = asyncio.run(server.autopair_api(_FakeRequest(_offer_payload())))
    assert answer.status_code in (400, 401)
    assert "sender_id" not in secure_server


def test_session_less_pake1_offer_is_refused_even_on_b_tier(secure_server):
    server.STATE.config["security"]["tier"] = "B"
    envelope, _ = build_pake_request(json.dumps(_offer_payload()), SYNTHETIC_PASSWORD)

    answer = asyncio.run(server.autopair_api(_FakeRequest(envelope)))

    assert answer.status_code == 401
    assert "autopair_hello" in json.loads(answer.body)["error"]
    assert "sender_id" not in secure_server


def test_normal_mode_refuses_session_less_network_pairing(secure_server):
    server.STATE.config["security"]["mode"] = "normal"
    response, _ = _hello()
    assert response.status_code == 403
    assert json.loads(response.body)["code"] == "local_pair_requires_secure_mode"


def test_factory_password_refuses_network_pairing_but_not_this_computer(secure_server, monkeypatch):
    monkeypatch.setattr(server.STATE, "used_default_password", True, raising=False)
    lan, _ = _hello()
    assert lan.status_code == 403
    assert json.loads(lan.body)["code"] == "local_pair_default_password"

    local, _ = _hello(host="127.0.0.1")
    assert local.status_code == 200


@pytest.mark.parametrize(
    "host, headers",
    [
        (PUBLIC_PEER, {}),
        ("127.0.0.1", {"X-AutoYou-Tunnel-Client-IP": PUBLIC_PEER}),
        ("127.0.0.1", {"X-Forwarded-For": PUBLIC_PEER}),
        ("127.0.0.1", {"X-AutoYou-WebRTC-Session-Id": "synthetic-webrtc"}),
    ],
)
def test_public_tunnel_and_proxy_callers_still_need_a_session(secure_server, host, headers):
    response, _ = _hello(host=host, headers=headers)
    assert response.status_code == 401


def test_global_limit_caps_rotating_lan_addresses(secure_server, monkeypatch):
    monkeypatch.setattr(
        server, "LOCAL_PAIR_NETWORK_GLOBAL_RATE_LIMITER", server.RateLimiter(max_requests=3, window_seconds=60)
    )
    statuses = [_hello(host=f"192.168.50.{30 + index}")[0].status_code for index in range(5)]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3:] == [429, 429]


def test_http_login_from_the_network_never_accepts_the_password():
    lan = TestClient(server.admin_app, client=(LAN_PEER, 50000))
    response = lan.post(
        "/login",
        data={"password": SYNTHETIC_PASSWORD},
        headers={"X-AutoYou-Local-Pair": "1", "Origin": "http://192.168.50.20:8001"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "local_pair_update_required"
    assert "admin_session" not in response.cookies

    plain = lan.post("/login", data={"password": SYNTHETIC_PASSWORD}, follow_redirects=False)
    assert plain.status_code == 403
    assert "admin_session" not in plain.cookies


def test_admin_sessions_expire_when_idle(monkeypatch):
    monkeypatch.setattr(server, "ADMIN_SESSION_IDLE_SECONDS", 60)
    monkeypatch.setitem(server.ADMIN_SESSIONS, "synthetic-fresh", time.time())
    monkeypatch.setitem(server.ADMIN_SESSIONS, "synthetic-stale", time.time() - 120)

    assert server._admin_session_active("synthetic-fresh") is True
    assert server._admin_session_active("synthetic-stale") is False
    assert "synthetic-stale" not in server.ADMIN_SESSIONS
