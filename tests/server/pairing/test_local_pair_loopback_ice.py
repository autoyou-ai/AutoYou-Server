# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-f6b1ccaaca017ba32f4e2c29


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-f6b1ccaaca017ba32f4e2c29"

import asyncio
import json
from types import SimpleNamespace

import pyotp
import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server
from shared.pairing_cpace import (
    build_cpace_hello,
    complete_cpace_handshake,
    decrypt_cpace_message,
    encrypt_cpace_message,
)


def test_server_defaults_to_secure_b_tier_without_legacy_env(monkeypatch):
    config = server._default_config()
    monkeypatch.setattr(server.STATE, "config", config)

    assert config["security"]["mode"] == "secure"
    assert config["security"]["tier"] == "B"
    assert server.get_pairing_tier() == "B"


class _FakeRequest:
    def __init__(
        self,
        payload: dict,
        host: str = "127.0.0.1",
        headers: dict | None = None,
        query_params: dict | None = None,
    ):
        self.client = SimpleNamespace(host=host)
        self.headers = headers or {}
        self.query_params = query_params or {}
        if isinstance(payload, bytes):
            self._body = payload
        elif isinstance(payload, str):
            self._body = payload.encode("utf-8")
        else:
            self._body = json.dumps(payload).encode("utf-8")

    async def body(self) -> bytes:
        return self._body


async def _perform_local_pair_hello(
    password: str,
    *,
    platform: str,
    sender_id: str,
    totp_code: str | None = None,
):
    """Drive a real /api/autopair_hello round trip and return the completed
    CPace session key, exactly as a real Local/Bluetooth Pair client would."""
    hello_env, handshake = build_cpace_hello(password, purpose="autopair", totp_code=totp_code)
    response = await server.autopair_hello_api(
        _FakeRequest(
            hello_env,
            headers={"X-AutoYou-Platform": platform, "X-AutoYou-Session-Id": sender_id},
            query_params={"session_id": sender_id},
        )
    )
    assert response.status_code == 200, response.body
    answer_text = response.body.decode("utf-8")
    assert answer_text.startswith("/autopair_hello_answer\n"), answer_text
    answer_env = answer_text.split("\n", 1)[1]
    return complete_cpace_handshake(answer_env, handshake)


def test_local_pair_endpoint_marks_loopback_requests(monkeypatch):
    captured = {}
    original_config = server.STATE.config
    original_session = (
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )

    async def fake_handle_autopair_offer(sender_id, payload):
        captured["sender_id"] = sender_id
        captured["payload"] = dict(payload)
        large_sdp = "v=0\r\n" + "".join(f"a=candidate:{idx} 1 udp 1 192.0.2.{idx % 250 + 1} 5000 typ host\r\n" for idx in range(90))
        return {
            "type": "answer",
            "sdp": large_sdp,
            "server_name": "Synthetic Server",
            "pairing_mode": "local_pair",
        }

    try:
        cfg = server._default_config()
        cfg.setdefault("security", {})["mode"] = "normal"
        server.STATE.config = cfg
        server._set_config_session(
            config_store=server.CONFIG_STORE_NONE,
            server_password=server.DEFAULT_SERVER_PASSWORD,
            config_unlock_password=None,
        )
        monkeypatch.setattr(server, "_require_login", lambda request: None)
        monkeypatch.setattr(server, "AUTOPAIR_RATE_LIMITER", server.RateLimiter(max_requests=12, window_seconds=60))
        monkeypatch.setattr(server.WEBRTC, "handle_autopair_offer", fake_handle_autopair_offer)

        payload = {
            "hash": server._server_generate_hash(server.DEFAULT_SERVER_PASSWORD),
            "offer": {"type": "offer", "sdp": "v=0"},
            "iceServers": [],
            "_autoyou_pairing_platform": "local",
            "_autoyou_sender_id": "python-synthetic-local-client",
        }
        response = asyncio.run(server.autopair_api(_FakeRequest(payload)))

        assert response.status_code == 200
        response_text = response.body.decode("utf-8")
        assert response_text.startswith("/autopair_answer\n")
        answer_payload = json.loads(response_text.split("\n", 1)[1])
        assert answer_payload["answer"]["type"] == "answer"
        assert answer_payload["answer"]["sdp"].count("a=candidate:") == 90
        assert answer_payload["server_name"] == "Synthetic Server"
        assert answer_payload["pairing_mode"] == "local_pair"
        assert "server_name" not in answer_payload["answer"]
        assert captured["sender_id"] == "python-synthetic-local-client"
        assert captured["payload"]["_autoyou_loopback_pairing"] is True
    finally:
        server.STATE.config = original_config
        server._set_config_session(
            config_store=original_session[2],
            server_password=original_session[0],
            config_unlock_password=original_session[1],
        )


def test_local_pair_endpoint_refuses_when_pairing_router_missing(monkeypatch):
    original_config = server.STATE.config
    original_router = server.pairing_router
    original_session = (
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )

    async def fail_handle_autopair_offer(_sender_id, _payload):
        raise AssertionError("WebRTC should not start when the pairing subsystem is missing")

    try:
        cfg = server._default_config()
        cfg.setdefault("security", {})["mode"] = "normal"
        server.STATE.config = cfg
        server._set_config_session(
            config_store=server.CONFIG_STORE_NONE,
            server_password=server.DEFAULT_SERVER_PASSWORD,
            config_unlock_password=None,
        )
        server.pairing_router = None
        monkeypatch.setattr(server, "_require_login", lambda request: None)
        monkeypatch.setattr(server, "AUTOPAIR_RATE_LIMITER", server.RateLimiter(max_requests=12, window_seconds=60))
        monkeypatch.setattr(server.WEBRTC, "handle_autopair_offer", fail_handle_autopair_offer)

        response = asyncio.run(
            server.autopair_api(
                _FakeRequest(
                    {
                        "hash": server._server_generate_hash(server.DEFAULT_SERVER_PASSWORD),
                        "offer": {"type": "offer", "sdp": "v=0"},
                        "iceServers": [],
                    }
                )
            )
        )

        assert response.status_code == 503
    finally:
        server.pairing_router = original_router
        server.STATE.config = original_config
        server._set_config_session(
            config_store=original_session[2],
            server_password=original_session[0],
            config_unlock_password=original_session[1],
        )


def test_secure_local_pair_endpoint_configures_router_when_startup_is_skipped(monkeypatch):
    captured = {}
    original_config = server.STATE.config
    original_router = server.pairing_router
    original_session = (
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )

    async def fake_handle_autopair_offer(sender_id, payload):
        captured["sender_id"] = sender_id
        captured["payload"] = dict(payload)
        return {"type": "answer", "sdp": "v=0", "pairing_mode": payload.get("_autoyou_pairing_mode")}

    try:
        cfg = server._default_config()
        cfg.setdefault("security", {})["mode"] = "secure"
        server.STATE.config = cfg
        server._set_config_session(
            config_store=server.CONFIG_STORE_NONE,
            server_password=server.DEFAULT_SERVER_PASSWORD,
            config_unlock_password=None,
        )
        server.pairing_router = original_router.__class__()
        monkeypatch.setattr(server, "_require_login", lambda request: None)
        monkeypatch.setattr(server, "AUTOPAIR_RATE_LIMITER", server.RateLimiter(max_requests=12, window_seconds=60))
        monkeypatch.setattr(server.WEBRTC, "handle_autopair_offer", fake_handle_autopair_offer)

        sender_id = "python-synthetic-secure-local-client"
        session = asyncio.run(
            _perform_local_pair_hello(server.DEFAULT_SERVER_PASSWORD, platform="local", sender_id=sender_id)
        )

        payload = {
            "hash": server._server_generate_hash(server.DEFAULT_SERVER_PASSWORD),
            "offer": {"type": "offer", "sdp": "v=0"},
            "iceServers": [],
            "_autoyou_pairing_platform": "local",
            "_autoyou_sender_id": sender_id,
        }
        encrypted = encrypt_cpace_message(json.dumps(payload, separators=(",", ":")), session)

        response = asyncio.run(
            server.autopair_api(
                _FakeRequest(
                    encrypted,
                    headers={
                        "X-AutoYou-Platform": "local",
                        "X-AutoYou-Session-Id": sender_id,
                    },
                    query_params={"session_id": sender_id},
                )
            )
        )

        assert response.status_code == 200
        assert response.body.startswith(b"/autopair_answer\ncpace1:")
        assert captured["sender_id"] == sender_id
        assert captured["payload"]["_autoyou_pairing_mode"] == "secure_pair"
        assert captured["payload"]["_autoyou_loopback_pairing"] is True

        answer_env = response.body.decode("utf-8").split("\n", 1)[1]
        decrypted = json.loads(decrypt_cpace_message(answer_env, session))
        assert decrypted["pairing_mode"] == "secure_pair"
    finally:
        server.pairing_router = original_router
        server.STATE.config = original_config
        server._set_config_session(
            config_store=original_session[2],
            server_password=original_session[0],
            config_unlock_password=original_session[1],
        )


@pytest.mark.parametrize("security_mode", ["secure", "secure_professional"])
@pytest.mark.parametrize(
    ("platform", "sender_id"),
    [
        ("local", "python-synthetic-secure-local-client"),
        ("bluetooth", "bluetooth-synthetic-secure-client"),
    ],
)
def test_secure_autopair_endpoint_real_router_keeps_transport_identity(
    security_mode,
    platform,
    sender_id,
    monkeypatch,
):
    captured = {}
    original_config = server.STATE.config
    original_router = server.pairing_router
    original_session = (
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )

    async def fake_handle_autopair_offer(captured_sender_id, payload):
        captured["sender_id"] = captured_sender_id
        captured["payload"] = dict(payload)
        return {
            "type": "answer",
            "sdp": "v=0",
            "pairing_mode": payload.get("_autoyou_pairing_mode"),
        }

    try:
        secret = pyotp.random_base32()
        cfg = server._default_config()
        cfg.setdefault("security", {})["mode"] = security_mode
        cfg.setdefault("security", {})["tier"] = "B"
        if security_mode == "secure_professional":
            cfg.setdefault("security", {})["totp_secret"] = secret
        cfg.setdefault("bluetooth_pairing", {})["enabled"] = platform == "bluetooth"
        server.STATE.config = cfg
        server._set_config_session(
            config_store=server.CONFIG_STORE_NONE,
            server_password=server.DEFAULT_SERVER_PASSWORD,
            config_unlock_password=None,
        )
        server.pairing_router = original_router.__class__()
        monkeypatch.setattr(server, "_require_login", lambda request: None)
        monkeypatch.setattr(server, "AUTOPAIR_RATE_LIMITER", server.RateLimiter(max_requests=12, window_seconds=60))
        monkeypatch.setattr(server.WEBRTC, "handle_autopair_offer", fake_handle_autopair_offer)
        assert server.get_pairing_tier() == "B"

        payload = {
            "hash": server._server_generate_hash(server.DEFAULT_SERVER_PASSWORD),
            "offer": {"type": "offer", "sdp": "v=0"},
            "iceServers": [],
            "_autoyou_pairing_platform": platform,
            "_autoyou_sender_id": sender_id,
        }
        expected_pairing_mode = "secure_pair" if security_mode == "secure" else "totp_pair"
        session = asyncio.run(
            _perform_local_pair_hello(
                server.DEFAULT_SERVER_PASSWORD,
                platform=platform,
                sender_id=sender_id,
                totp_code=pyotp.TOTP(secret).now() if security_mode == "secure_professional" else None,
            )
        )
        if security_mode == "secure_professional":
            assert session is not None
        encrypted = encrypt_cpace_message(json.dumps(payload, separators=(",", ":")), session)

        response = asyncio.run(
            server.autopair_api(
                _FakeRequest(
                    encrypted,
                    headers={
                        "X-AutoYou-Platform": platform,
                        "X-AutoYou-Session-Id": sender_id,
                    },
                    query_params={"session_id": sender_id},
                )
            )
        )

        assert response.status_code == 200
        response_text = response.body.decode("utf-8")
        assert response_text.startswith("/autopair_answer\ncpace1:")
        decrypted_answer = decrypt_cpace_message(response_text.split("\n", 1)[1], session)
        decrypted_answer = server.pairing_router._decode_compressed_payload_if_needed(decrypted_answer)
        answer_payload = json.loads(decrypted_answer)
        assert answer_payload["session_id"] == sender_id
        assert answer_payload["pairing_mode"] == expected_pairing_mode
        assert captured["sender_id"] == sender_id
        assert captured["payload"]["_autoyou_pairing_platform"] == platform
        assert captured["payload"]["_autoyou_sender_id"] == sender_id
        assert captured["payload"]["_autoyou_pairing_mode"] == expected_pairing_mode
        assert captured["payload"]["_autoyou_loopback_pairing"] is True
    finally:
        server.pairing_router = original_router
        server.STATE.config = original_config
        server._set_config_session(
            config_store=original_session[2],
            server_password=original_session[0],
            config_unlock_password=original_session[1],
        )


def test_bluetooth_pair_endpoint_rejects_when_disabled(monkeypatch):
    original_config = server.STATE.config
    original_session = (
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )
    called = False

    async def fake_handle_autopair_offer(sender_id, payload):
        nonlocal called
        called = True
        return {"type": "answer", "sdp": "v=0"}

    try:
        cfg = server._default_config()
        cfg.setdefault("security", {})["mode"] = "normal"
        cfg.setdefault("bluetooth_pairing", {})["enabled"] = False
        server.STATE.config = cfg
        server._set_config_session(
            config_store=server.CONFIG_STORE_NONE,
            server_password=server.DEFAULT_SERVER_PASSWORD,
            config_unlock_password=None,
        )
        monkeypatch.setattr(server, "AUTOPAIR_RATE_LIMITER", server.RateLimiter(max_requests=12, window_seconds=60))
        monkeypatch.setattr(server, "_require_login", lambda request: None)
        monkeypatch.setattr(server.WEBRTC, "handle_autopair_offer", fake_handle_autopair_offer)

        payload = {
            "hash": server._server_generate_hash(server.DEFAULT_SERVER_PASSWORD),
            "offer": {"type": "offer", "sdp": "v=0"},
            "iceServers": [],
            "_autoyou_pairing_platform": "bluetooth",
            "_autoyou_sender_id": "bluetooth-synthetic-client",
        }
        response = asyncio.run(
            server.autopair_api(
                _FakeRequest(payload, headers={"X-AutoYou-Platform": "bluetooth"})
            )
        )

        assert response.status_code == 403
        assert b"Bluetooth Pair is off on this server" in response.body
        assert called is False
    finally:
        server.STATE.config = original_config
        server._set_config_session(
            config_store=original_session[2],
            server_password=original_session[0],
            config_unlock_password=original_session[1],
        )


def test_bluetooth_pair_endpoint_uses_stable_client_identity_when_enabled(monkeypatch):
    captured = {}
    original_config = server.STATE.config
    original_session = (
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )

    async def fake_handle_autopair_offer(sender_id, payload):
        captured["sender_id"] = sender_id
        captured["payload"] = dict(payload)
        return {"type": "answer", "sdp": "v=0", "pairing_mode": "bluetooth_pair"}

    try:
        cfg = server._default_config()
        cfg.setdefault("security", {})["mode"] = "normal"
        cfg.setdefault("bluetooth_pairing", {})["enabled"] = True
        server.STATE.config = cfg
        server._set_config_session(
            config_store=server.CONFIG_STORE_NONE,
            server_password=server.DEFAULT_SERVER_PASSWORD,
            config_unlock_password=None,
        )
        monkeypatch.setattr(server, "AUTOPAIR_RATE_LIMITER", server.RateLimiter(max_requests=12, window_seconds=60))
        monkeypatch.setattr(server, "_require_login", lambda request: None)
        monkeypatch.setattr(server.WEBRTC, "handle_autopair_offer", fake_handle_autopair_offer)

        payload = {
            "hash": server._server_generate_hash(server.DEFAULT_SERVER_PASSWORD),
            "offer": {"type": "offer", "sdp": "v=0"},
            "iceServers": [],
            "_autoyou_pairing_platform": "bluetooth",
            "_autoyou_sender_id": "bluetooth-synthetic-client",
            "client_display_name": "Synthetic Bluetooth Class",
        }
        response = asyncio.run(
            server.autopair_api(
                _FakeRequest(payload, headers={"X-AutoYou-Platform": "bluetooth"})
            )
        )

        assert response.status_code == 200
        assert captured["sender_id"] == "bluetooth-synthetic-client"
        assert captured["payload"]["_autoyou_pairing_platform"] == "bluetooth"
        assert captured["payload"]["client_display_name"] == "Synthetic Bluetooth Class"
        assert b"bluetooth_pair" in response.body
    finally:
        server.STATE.config = original_config
        server._set_config_session(
            config_store=original_session[2],
            server_password=original_session[0],
            config_unlock_password=original_session[1],
        )


def test_bluetooth_pair_secure_endpoint_parses_with_transport_identity(monkeypatch):
    captured = {}
    original_config = server.STATE.config
    original_session = (
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )

    fake_session = object()

    class _FakePairingRouter:
        async def _parse_autopair_payload(
            self,
            line,
            platform,
            sender_id,
            *,
            identity_sender_id=None,
        ):
            captured["parse"] = {
                "line": line,
                "platform": platform,
                "sender_id": sender_id,
                "identity_sender_id": identity_sender_id,
            }
            return {
                "hash": server._server_generate_hash(server.DEFAULT_SERVER_PASSWORD),
                "offer": {"type": "offer", "sdp": "v=0"},
                "iceServers": [],
            }, fake_session

        async def _format_autopair_answer(
            self,
            answer,
            platform,
            sender_id,
            session=None,
            *,
            identity_sender_id=None,
        ):
            captured["format"] = {
                "platform": platform,
                "sender_id": sender_id,
                "session": session,
            }
            return "/autopair_answer\n{}"

    async def fake_handle_autopair_offer(sender_id, payload):
        captured["webrtc"] = {"sender_id": sender_id, "payload": dict(payload)}
        return {"type": "answer", "sdp": "v=0", "pairing_mode": "bluetooth_pair"}

    try:
        cfg = server._default_config()
        cfg.setdefault("security", {})["mode"] = "secure_professional"
        cfg.setdefault("bluetooth_pairing", {})["enabled"] = True
        server.STATE.config = cfg
        server._set_config_session(
            config_store=server.CONFIG_STORE_NONE,
            server_password=server.DEFAULT_SERVER_PASSWORD,
            config_unlock_password=None,
        )
        monkeypatch.setattr(server, "pairing_router", _FakePairingRouter())
        monkeypatch.setattr(server, "AUTOPAIR_RATE_LIMITER", server.RateLimiter(max_requests=12, window_seconds=60))
        monkeypatch.setattr(server, "_require_login", lambda request: None)
        monkeypatch.setattr(server.WEBRTC, "handle_autopair_offer", fake_handle_autopair_offer)

        response = asyncio.run(
            server.autopair_api(
                _FakeRequest(
                    {"encrypted": "body"},
                    headers={
                        "X-AutoYou-Platform": "bluetooth",
                        "X-AutoYou-Session-Id": "bluetooth-synthetic-client",
                    },
                    query_params={"session_id": "bluetooth-synthetic-client"},
                )
            )
        )

        assert response.status_code == 200
        assert captured["parse"]["platform"] == "bluetooth"
        assert captured["parse"]["sender_id"] == "bluetooth-synthetic-client"
        assert captured["parse"]["identity_sender_id"] == "bluetooth-synthetic-client"
        assert captured["webrtc"]["sender_id"] == "bluetooth-synthetic-client"
        assert captured["webrtc"]["payload"]["_autoyou_pairing_platform"] == "bluetooth"
        assert captured["format"]["platform"] == "bluetooth"
        assert captured["format"]["sender_id"] == "bluetooth-synthetic-client"
        assert captured["format"]["session"] is fake_session
    finally:
        server.STATE.config = original_config
        server._set_config_session(
            config_store=original_session[2],
            server_password=original_session[0],
            config_unlock_password=original_session[1],
        )


def test_local_pair_endpoint_rate_limits_repeated_autopair_attempts(monkeypatch):
    original_config = server.STATE.config
    original_session = (
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )

    async def fake_handle_autopair_offer(sender_id, payload):
        return {"type": "answer", "sdp": "v=0"}

    try:
        cfg = server._default_config()
        cfg.setdefault("security", {})["mode"] = "normal"
        server.STATE.config = cfg
        server._set_config_session(
            config_store=server.CONFIG_STORE_NONE,
            server_password=server.DEFAULT_SERVER_PASSWORD,
            config_unlock_password=None,
        )
        monkeypatch.setattr(server, "AUTOPAIR_RATE_LIMITER", server.RateLimiter(max_requests=1, window_seconds=60))
        monkeypatch.setattr(server, "_require_login", lambda request: None)
        monkeypatch.setattr(server.WEBRTC, "handle_autopair_offer", fake_handle_autopair_offer)

        payload = {
            "hash": server._server_generate_hash(server.DEFAULT_SERVER_PASSWORD),
            "offer": {"type": "offer", "sdp": "v=0"},
            "iceServers": [],
            "_autoyou_pairing_platform": "local",
            "_autoyou_sender_id": "python-synthetic-local-client",
        }

        first_response = asyncio.run(server.autopair_api(_FakeRequest(payload, host="198.51.100.10")))
        second_response = asyncio.run(server.autopair_api(_FakeRequest(payload, host="198.51.100.10")))

        assert first_response.status_code == 200
        assert second_response.status_code == 429
        assert b"Too many pairing attempts" in second_response.body
    finally:
        server.STATE.config = original_config
        server._set_config_session(
            config_store=original_session[2],
            server_password=original_session[0],
            config_unlock_password=original_session[1],
        )


def test_server_loopback_ice_patch_adds_localhost_candidate(monkeypatch):
    aioice_ice = __import__("aioice.ice", fromlist=["ice"])

    def fake_get_host_addresses(use_ipv4, use_ipv6):
        return ["192.0.2.20"] if use_ipv4 else []

    monkeypatch.setattr(aioice_ice, "get_host_addresses", fake_get_host_addresses)
    monkeypatch.setattr(server, "_LOOPBACK_ICE_INSTALLED", False)

    assert server._install_loopback_ice_candidates()
    assert aioice_ice.get_host_addresses(True, False) == ["127.0.0.1", "192.0.2.20"]
