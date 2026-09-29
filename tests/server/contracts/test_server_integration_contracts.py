# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-4562c1c7988626bf91809efc

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import base64
import copy
import json
import zlib

import pyotp
import pytest
from fastapi.testclient import TestClient

from pairing_router import PairingRouter

import server
from shared.pairing_cpace import (
    build_cpace_hello,
    complete_cpace_handshake,
    decrypt_cpace_message,
    encrypt_cpace_message,
)

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-4562c1c7988626bf91809efc"


def _decode_maybe_compressed_payload(payload: str) -> str:
    if not payload.startswith("z:"):
        return payload
    encoded = payload[2:]
    encoded += "=" * ((4 - len(encoded) % 4) % 4)
    return zlib.decompress(base64.urlsafe_b64decode(encoded)).decode("utf-8")


def _decrypt_server_payload(payload: str, *, security_mode: str, password: str, totp_secret: str | None = None) -> str:
    if security_mode == "normal":
        return payload
    # secure_professional's /otp response is encrypted with the plain
    # password, same as secure - the trailing TOTP code on /pair is verified
    # online before this is generated and never enters the encryption key
    # (see the B-Tier /pair fix; totp_secret is unused here now).
    return server.aead_decrypt(payload, password)


def _parse_otp_reply(reply_text: str, *, security_mode: str, password: str, totp_secret: str | None = None) -> dict:
    prefix, payload = reply_text.split("\n", 1)
    assert prefix == "/otp"
    decrypted = _decrypt_server_payload(
        payload,
        security_mode=security_mode,
        password=password,
        totp_secret=totp_secret,
    )
    return json.loads(decrypted)


def _parse_autopair_reply(reply_text: str, *, security_mode: str, session=None) -> dict:
    prefix, payload = reply_text.split("\n", 1)
    assert prefix == "/autopair_answer"
    if security_mode == "normal":
        decrypted = payload
    else:
        assert session is not None
        decrypted = decrypt_cpace_message(payload, session)
    return json.loads(_decode_maybe_compressed_payload(decrypted))


def _build_autopair_command(
    *,
    security_mode: str,
    password: str,
    offer: dict,
    session=None,
) -> str:
    payload = {
        "hash": server._server_generate_hash(password),
        "offer": offer,
        "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}],
    }
    payload_text = PairingRouter._maybe_compress_autopair_payload(json.dumps(payload, separators=(",", ":")))
    if security_mode == "normal":
        encrypted = payload_text
    else:
        assert session is not None
        encrypted = encrypt_cpace_message(payload_text, session)
    return f"/autopair\n{encrypted}"


class _FakeServerWebRTC:
    def __init__(self) -> None:
        self.offer_calls: list[tuple[str, dict]] = []
        self.candidate_calls: list[tuple[str, dict]] = []
        self.autopair_calls: list[tuple[str, dict]] = []
        self.remembered_client_names: list[tuple[str, str]] = []
        self._poll_messages: dict[str, list[dict]] = {}

    def remember_client_display_name(self, identity, value) -> str:
        client_name = str(value or "")
        self.remembered_client_names.append(
            (str(getattr(identity, "owner_key", "") or ""), client_name)
        )
        return client_name

    async def handle_session_offer(self, session_id: str, signal_data: dict) -> dict:
        self.offer_calls.append((session_id, dict(signal_data)))
        self._poll_messages[session_id] = [
            {
                "candidate": "candidate:server 1 udp 2130706431 127.0.0.1 5000 typ host",
                "sdpMid": "0",
                "sdpMLineIndex": 0,
            }
        ]
        return {"type": "answer", "sdp": "v=0\r\no=autoyou 0 0 IN IP4 127.0.0.1\r\n"}

    async def handle_session_candidate(self, session_id: str, candidate_payload: dict) -> bool:
        self.candidate_calls.append((session_id, dict(candidate_payload)))
        return True

    def pop_outgoing_trickle_candidates(self, session_id: str) -> list[dict]:
        return list(self._poll_messages.pop(session_id, []))

    async def handle_autopair_offer(self, relay_id: str, payload: dict) -> dict:
        self.autopair_calls.append((relay_id, copy.deepcopy(payload)))
        return {
            "type": "answer",
            "sdp": "v=0\r\no=autoyou 0 0 IN IP4 127.0.0.1\r\n",
            "server_name": "Integration AutoYou",
            "owner_key": "cloud:device-session-789",
            "canonical_user_id": "user::cloud:device-session-789",
            "canonical_session_id": "session::cloud:device-session-789",
        }


def _configure_server_router(fake_webrtc: _FakeServerWebRTC) -> PairingRouter:
    router = PairingRouter()

    async def _noop_extend(_: int | None) -> None:
        return None

    async def _noop_start() -> bool:
        return True

    async def _noop_auth() -> bool:
        return True

    router.configure(
        generate_hash=server._server_generate_hash,
        aead_encrypt=server.aead_encrypt,
        aead_decrypt=server.aead_decrypt,
        get_current_password=server.get_current_password,
        get_security_mode=server.get_security_mode,
        get_totp_secret_for_sender=server.get_totp_secret_for_sender,
        get_all_totp_secrets=server.get_all_totp_secrets,
        get_tunnelmole_status=lambda: {"status": "running", "public_url": "https://pair.autoyou.test"},
        extend_tunnelmole_timer=_noop_extend,
        start_tunnelmole_service_with_timer=_noop_start,
        ensure_auth_server_running=_noop_auth,
        generate_otp_hash_and_cache=server.generate_otp_hash_and_cache,
        handle_autopair_offer=fake_webrtc.handle_autopair_offer,
        start_new_conversation=server._start_new_conversation_for_owner,
        apply_remote_ice_candidates=lambda _session_id, _candidates: asyncio.sleep(0),
        get_trickle_candidates=lambda _session_id: asyncio.sleep(0, result=[]),
        is_totp_pair_mode=server._is_totp_pair_mode,
        is_tunnelmole_unmanaged_mode=server._is_tunnelmole_unmanaged_mode,
        generate_totp_pair_otp_and_cache=server.generate_totp_pair_otp_and_cache,
        start_tunnelmole_service_no_timer=_noop_start,
        verify_totp_code=server._verify_totp_secret,
        totp_hello_rate_limit_allowed=lambda _key: True,
    )
    return router


async def _perform_server_autopair_hello(
    router: PairingRouter,
    *,
    sender_id: str,
    password: str,
    totp_secret: str | None = None,
    identity_sender_id: str | None = None,
):
    """Drive a real /autopair_hello round trip against a configured PairingRouter
    and return the completed CPace session, exactly as a real client would."""
    totp_code = pyotp.TOTP(totp_secret).now() if totp_secret else None
    hello_env, handshake = build_cpace_hello(password, purpose="autopair", totp_code=totp_code)
    response = await router.process_message(
        f"/autopair_hello\n{hello_env}",
        platform="cloud",
        sender_id=sender_id,
        identity_sender_id=identity_sender_id,
    )
    assert response is not None and response.startswith("/autopair_hello_answer\n"), response
    answer_env = response.split("\n", 1)[1]
    return complete_cpace_handshake(answer_env, handshake)


@pytest.fixture
def isolated_server_state(monkeypatch):
    monkeypatch.setattr(server, "_get_unlock_state", lambda: "Ready")
    original_config = copy.deepcopy(server.STATE.config)
    original_otp_cache = copy.deepcopy(server.STATE.otp_cache)
    original_session_cache = copy.deepcopy(server.STATE.session_cache)
    original_server_password = server.STATE.server_password
    original_unlock_password = server.STATE.config_unlock_password
    original_config_store = server.STATE.config_store
    original_auth_limiter = dict(server.AUTH_RATE_LIMITER._requests)
    original_login_limiter = dict(server.LOGIN_RATE_LIMITER._requests)
    original_offer_limiter = dict(server.SIGNAL_OFFER_RATE_LIMITER._requests)
    original_candidate_limiter = dict(server.SIGNAL_CANDIDATE_RATE_LIMITER._requests)
    original_unauth_limiter = dict(server.UNAUTHENTICATED_SIGNAL_RATE_LIMITER._requests)

    try:
        yield
    finally:
        server.STATE.config = original_config
        server.STATE.otp_cache = original_otp_cache
        server.STATE.session_cache = original_session_cache
        server._set_config_session(
            config_store=original_config_store,
            server_password=original_server_password,
            config_unlock_password=original_unlock_password,
        )
        server.AUTH_RATE_LIMITER._requests.clear()
        server.AUTH_RATE_LIMITER._requests.update(original_auth_limiter)
        server.LOGIN_RATE_LIMITER._requests.clear()
        server.LOGIN_RATE_LIMITER._requests.update(original_login_limiter)
        server.SIGNAL_OFFER_RATE_LIMITER._requests.clear()
        server.SIGNAL_OFFER_RATE_LIMITER._requests.update(original_offer_limiter)
        server.SIGNAL_CANDIDATE_RATE_LIMITER._requests.clear()
        server.SIGNAL_CANDIDATE_RATE_LIMITER._requests.update(original_candidate_limiter)
        server.UNAUTHENTICATED_SIGNAL_RATE_LIMITER._requests.clear()
        server.UNAUTHENTICATED_SIGNAL_RATE_LIMITER._requests.update(original_unauth_limiter)


@pytest.mark.parametrize(
    ("security_mode", "client_platform"),
    [
        pytest.param("normal", "signal", id="none"),
        pytest.param("secure", "signal", id="secure"),
        pytest.param("secure_professional", "signal", id="secure-professional"),
    ],
)
def test_pair_auth_signal_flow_across_security_modes(
    security_mode: str,
    client_platform: str,
    isolated_server_state,
    monkeypatch,
):
    password = "integration-password"
    sender_id = "+15551234567"
    totp_secret = pyotp.random_base32() if security_mode == "secure_professional" else None

    config = {
        "server": {"name": "Integration AutoYou"},
        "rtc": {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
        "security": {"mode": security_mode, "totp_secret": ""},
        "tunnelmole": {"enabled": True, "otp_timeout_minutes": 5, "otp_multiuse": True},
    }
    if totp_secret:
        config["security"]["totp_secret"] = totp_secret

    server.STATE.config = config
    server.STATE.otp_cache = {}
    server.STATE.session_cache = {}
    server._set_config_session(
        config_store=server.CONFIG_STORE_ENCRYPTED,
        server_password=password,
        config_unlock_password=password,
    )

    fake_webrtc = _FakeServerWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    router = _configure_server_router(fake_webrtc)

    pair_command = "/pair"
    if totp_secret:
        pair_command = f"/pair {pyotp.TOTP(totp_secret).now()}"
    pair_reply = asyncio.run(router.process_message(pair_command, platform=client_platform, sender_id=sender_id))
    assert pair_reply is not None
    otp_payload = _parse_otp_reply(
        pair_reply,
        security_mode=security_mode,
        password=password,
        totp_secret=totp_secret,
    )

    assert otp_payload["url"] == "https://pair.autoyou.test"
    if totp_secret:
        assert otp_payload["totp_secret"] == totp_secret

    auth_hash = server._server_generate_hash(f"{otp_payload['otp']}:{password}")

    with TestClient(server.auth_app) as client:
        auth_response = client.post(
            "/auth",
            json={"hash": auth_hash, "clientId": "desktop-client-1"},
        )

        assert auth_response.status_code == 200
        auth_body = auth_response.json()
        session_id = auth_body["session_id"]
        assert auth_body["serverName"] == "Integration AutoYou"
        assert auth_body["iceServers"] == config["rtc"]["iceServers"]
        assert auth_body["session"]["owner_key"]
        assert auth_body["session"]["canonical_user_id"]
        assert auth_body["session"]["canonical_session_id"]
        assert auth_body["session"]["conversation_session_id"]

        session_payload = server.STATE.session_cache[session_id]
        assert session_payload["stable_client_id"] == "desktop-client-1"
        assert session_payload["owner_key"]
        assert session_payload["canonical_user_id"]
        assert session_payload["canonical_session_id"]

        offer_response = client.post(
            f"/signal/{session_id}",
            json={"type": "offer", "sdp": "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\n"},
        )
        assert offer_response.status_code == 200
        assert offer_response.json()["type"] == "answer"

        candidate_response = client.post(
            f"/signal/{session_id}",
            json={
                "type": "candidate",
                "sdp": "candidate:1 1 udp 2130706431 127.0.0.1 5001 typ host",
                "sdpMid": "0",
                "sdpMLineIndex": 0,
            },
        )
        assert candidate_response.status_code == 200
        assert candidate_response.json()["success"] is True

        poll_response = client.get(f"/signal/{session_id}")
        assert poll_response.status_code == 200
        assert poll_response.json()["messages"][0]["candidate"].startswith("candidate:server")

    assert fake_webrtc.offer_calls[0][0] == session_id
    assert fake_webrtc.candidate_calls[0][0] == session_id


@pytest.mark.parametrize(
    "security_mode",
    [
        pytest.param("normal", id="none"),
        pytest.param("secure", id="secure"),
        pytest.param("secure_professional", id="secure-professional"),
    ],
)
def test_cloud_autopair_contract_preserves_stable_identity(
    security_mode: str,
    isolated_server_state,
    monkeypatch,
):
    password = "integration-password"
    relay_session_id = "relay-session-123"
    device_session_id = "device-session-789"
    totp_secret = pyotp.random_base32() if security_mode == "secure_professional" else None

    config = {
        "security": {"mode": security_mode, "totp_secret": ""},
        "tunnelmole": {"enabled": True},
    }
    if totp_secret:
        config["security"]["totp_secret"] = totp_secret

    server.STATE.config = config
    server.STATE.otp_cache = {}
    server.STATE.session_cache = {}
    server._set_config_session(
        config_store=server.CONFIG_STORE_ENCRYPTED,
        server_password=password,
        config_unlock_password=password,
    )

    fake_webrtc = _FakeServerWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    router = _configure_server_router(fake_webrtc)

    session = None
    if security_mode != "normal":
        session = asyncio.run(
            _perform_server_autopair_hello(
                router,
                sender_id=relay_session_id,
                identity_sender_id=device_session_id,
                password=password,
                totp_secret=totp_secret,
            )
        )

    autopair_command = _build_autopair_command(
        security_mode=security_mode,
        password=password,
        offer={"type": "offer", "sdp": "v=0\r\na=group:BUNDLE 0\r\n"},
        session=session,
    )

    reply = asyncio.run(
        router.process_message(
            autopair_command,
            platform="cloud",
            sender_id=relay_session_id,
            identity_sender_id=device_session_id,
        )
    )

    assert reply is not None
    answer_payload = _parse_autopair_reply(
        reply,
        security_mode=security_mode,
        session=session,
    )

    assert answer_payload["session_id"] == device_session_id
    assert answer_payload["answer"]["type"] == "answer"
    assert answer_payload["server_name"] == "Integration AutoYou"
    assert answer_payload["owner_key"] == "cloud:device-session-789"
    assert answer_payload["canonical_user_id"] == "user::cloud:device-session-789"
    assert answer_payload["canonical_session_id"] == "session::cloud:device-session-789"
    assert fake_webrtc.autopair_calls[0][0] == relay_session_id
    assert fake_webrtc.autopair_calls[0][1]["_autoyou_pairing_platform"] == "cloud"
    assert fake_webrtc.autopair_calls[0][1]["_autoyou_sender_id"] == device_session_id


def test_cloud_relay_refuses_pairing_while_default_password_is_active(isolated_server_state, monkeypatch):
    import httpx

    router_calls: list[tuple] = []
    posts: list[dict] = []

    class _FakeRouter:
        FRAGMENT_CONSUMED = "__fragment__"

        async def process_message(self, *args, **kwargs):
            router_calls.append((args, kwargs))
            return "/autopair_answer\n{}"

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            posts.append({"url": url, "json": json})
            return object()

    monkeypatch.setattr(server.STATE, "used_default_password", True)
    monkeypatch.setattr(server, "pairing_router", _FakeRouter())
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    event = {
        "relay_id": "relay-session-123",
        "command": "/autopair",
        "payload": "/autopair\n{}",
        "client_device_id": "synthetic-client-device",
    }

    asyncio.run(
        server._handle_cloud_relay_event(
            "relay",
            json.dumps(event),
            "synthetic-server-token",
        )
    )

    assert router_calls == []
    assert len(posts) == 1
    response_payload = json.loads(posts[0]["json"]["response_payload"])
    assert "Cloud Pair is disabled" in response_payload["error"]


def test_factory_cloud_pairing_uses_device_key_and_stops_after_password_change(isolated_server_state, monkeypatch):
    import httpx
    from unittest.mock import AsyncMock
    from shared.shared_device_pairing import generate_key_material, credential_invitation_id, derive_authenticator

    key, phone = generate_key_material(), generate_key_material()
    metadata = {"grant_id": "account00000001", "server_device_id": "serverdevice001",
                "client_device_id": "clientdevice001", "server_public_key": key.public_key,
                "client_public_key": phone.public_key, "purpose": "bootstrap"}
    password = derive_authenticator(phone.private_key, phone.public_key, key.public_key,
        credential_invitation_id(metadata["grant_id"], metadata["server_device_id"], metadata["client_device_id"]))
    server.STATE.config = {"security": {"mode": "secure"}, "cloud": {"server_id": "serverdevice001"}}
    server._set_config_session(config_store=server.CONFIG_STORE_ENCRYPTED,
        server_password=server.DEFAULT_SERVER_PASSWORD, config_unlock_password=server.DEFAULT_SERVER_PASSWORD)
    monkeypatch.setattr(server.STATE, "used_default_password", True)
    monkeypatch.setattr(server, "_shared_device_server_key_material", lambda: (key.private_key, key.public_key))
    webrtc = _FakeServerWebRTC()
    monkeypatch.setattr(server, "pairing_router", _configure_server_router(webrtc))
    monkeypatch.setattr(server, "_drain_cloud_server_ice", AsyncMock())
    posts = []

    class HTTP:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, json):
            posts.append(json["response_payload"])
            return object()

    monkeypatch.setattr(httpx, "AsyncClient", HTTP)

    async def send(command, payload):
        await server._handle_cloud_relay_event("relay", json.dumps({
            "relay_id": "relay-bootstrap", "server_id": "serverdevice001",
            "client_device_id": "clientdevice001", "command": command,
            "payload": payload, "shared_device_auth": metadata,
        }), "synthetic-server-token")
        return posts[-1]

    async def run():
        hello, handshake = build_cpace_hello(password, purpose="autopair")
        answer = await send("/autopair_hello", hello)
        session = complete_cpace_handshake(answer.split("\n", 1)[1], handshake)
        # from __debug_provenance_s__ import btc
        offer = _build_autopair_command(security_mode="secure", password=password,
            offer={"type": "offer", "sdp": "v=0\r\n"}, session=session)
        response = await send("/autopair", offer)
        assert _parse_autopair_reply(response, security_mode="secure", session=session)["answer"]["type"] == "answer"
        assert len(webrtc.autopair_calls) == 1
        server.STATE.server_password = "changed-computer-password"
        assert "error" in json.loads(await send("/autopair", offer))
        assert len(webrtc.autopair_calls) == 1
        server.STATE.server_password = server.DEFAULT_SERVER_PASSWORD
        for used_default, mode in ((False, "secure"), (True, "secure_professional")):
            server.STATE.used_default_password = used_default
            server.STATE.config["security"]["mode"] = mode
            assert "error" in json.loads(await send("/autopair", offer))
            assert len(webrtc.autopair_calls) == 1

    asyncio.run(run())


def test_enhanced_autopair_hello_is_rejected_in_normal_mode(isolated_server_state, monkeypatch):
    password = "integration-password"
    server.STATE.config = {"security": {"mode": "normal"}}
    server.STATE.otp_cache = {}
    server.STATE.session_cache = {}
    server._set_config_session(
        config_store=server.CONFIG_STORE_ENCRYPTED,
        server_password=password,
        config_unlock_password=password,
    )

    router = _configure_server_router(_FakeServerWebRTC())
    hello_env, _handshake = build_cpace_hello(password, purpose="autopair")
    reply = asyncio.run(
        router.process_message(
            f"/autopair_hello\n{hello_env}",
            platform="cloud",
            sender_id="synthetic-relay-session",
        )
    )

    assert reply == (
        "Enhanced /autopair_hello requires Secure or Secure Professional mode. "
        "Use /pair or the normal/plaintext AutoPair flow in Normal mode."
    )


def test_cloud_relay_dispatches_bare_mcp_continuation_fragment(
    isolated_server_state,
    monkeypatch,
):
    import httpx

    router_calls: list[tuple] = []
    posts: list[dict] = []
    fragment_sentinel = "__autopair_fragment_consumed__"

    class _FakeRouter:
        FRAGMENT_CONSUMED = fragment_sentinel

        async def process_message(self, *args, **kwargs):
            router_calls.append((args, kwargs))
            return fragment_sentinel

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            posts.append({"url": url, "json": json})
            return object()

    fragment = "A" * 100
    monkeypatch.setattr(server.STATE, "used_default_password", False)
    monkeypatch.setattr(server, "pairing_router", _FakeRouter())
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    asyncio.run(
        server._handle_cloud_relay_event(
            "relay",
            json.dumps(
                {
                    "relay_id": "relay-fragment-123",
                    "command": "/autopair_fragment",
                    "payload": fragment,
                    "client_device_id": "synthetic-client-device",
                }
            ),
            "synthetic-server-token",
        )
    )

    assert router_calls[0][0][0] == fragment
    assert router_calls[0][1]["sender_id"] == "synthetic-client-device"
    assert posts[0]["json"]["response_payload"] == fragment_sentinel


@pytest.mark.parametrize(
    ("command", "reply_prefix"),
    [
        ("/autopair_hello", "/autopair_hello_answer\n"),
        ("/autopair", "/autopair_answer\n"),
        ("/pair_hello", "/pair_hello_answer\n"),
        ("/otp_pair", "/otp\n"),
    ],
)
def test_cloud_relay_routes_enhanced_pairing_hello_commands(
    isolated_server_state,
    monkeypatch,
    command,
    reply_prefix,
):
    import httpx

    router_calls: list[tuple] = []
    posts: list[dict] = []

    class _FakeRouter:
        FRAGMENT_CONSUMED = "__fragment__"

        async def process_message(self, *args, **kwargs):
            router_calls.append((args, kwargs))
            return f"{reply_prefix}synthetic-envelope"

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            posts.append({"url": url, "json": json})
            return object()

    monkeypatch.setattr(server.STATE, "used_default_password", False)
    monkeypatch.setattr(server, "pairing_router", _FakeRouter())
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    event = {
        "relay_id": "relay-session-123",
        "command": command,
        # Account-service stores the command and body separately.  The server
        # must reconstruct the wire message before invoking PairingRouter.
        "payload": "synthetic-envelope\nopaque-sdp-line",
        "client_device_id": "synthetic-client-device",
    }

    asyncio.run(
        server._handle_cloud_relay_event(
            "relay",
            json.dumps(event),
            "synthetic-server-token",
        )
    )

    assert len(router_calls) == 1
    args, kwargs = router_calls[0]
    assert args[0] == f"{command}\nsynthetic-envelope\nopaque-sdp-line"
    assert kwargs["platform"] == "cloud"
    assert kwargs["sender_id"] == event["client_device_id"]
    assert kwargs["identity_sender_id"] == event["client_device_id"]
    assert posts[0]["json"]["response_payload"] == f"{reply_prefix}synthetic-envelope"


def test_cloud_relay_preserves_large_autopair_answer_payload(isolated_server_state, monkeypatch):
    import httpx

    large_reply = "/autopair_answer\n" + ("A" * 5000)
    posts: list[dict] = []

    class _FakeRouter:
        FRAGMENT_CONSUMED = "__fragment__"

        async def process_message(self, *args, **kwargs):
            return large_reply

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            posts.append({"url": url, "json": json})
            return object()

    monkeypatch.setattr(server.STATE, "used_default_password", False)
    monkeypatch.setattr(server, "pairing_router", _FakeRouter())
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    asyncio.run(
        server._handle_cloud_relay_event(
            "relay",
            json.dumps(
                {
                    "relay_id": "relay-session-123",
                    "command": "/autopair",
                    "payload": "/autopair\n{}",
                    "client_device_id": "synthetic-client-device",
                }
            ),
            "synthetic-server-token",
        )
    )

    assert posts[0]["json"]["response_payload"] == large_reply
    assert len(posts[0]["json"]["response_payload"]) > 4096


def test_browser_forwarding_preserves_reserved_routes_agent_frontends_and_advertised_websites(isolated_server_state):
    original_dynamic_agent_proxy_ports = dict(getattr(server.STATE, "dynamic_agent_proxy_ports", {}) or {})
    original_main_server_port = getattr(server.STATE, "main_server_port", server.AI_AGENT_SERVER_PORT)

    try:
        server.STATE.main_server_port = 8081
        server.STATE.config = {
            "autoyou_page": {
                "port": 8067,
                "custom_forward_enabled": True,
                "custom_forward_port": 9000,
                "advertised_websites": [
                    {
                        "port": 3100,
                        "label": "Dashboard",
                        "description": "Remote dashboard",
                        "enabled": True,
                        "target_url": "http://127.0.0.1:3200",
                    }
                ],
            },
            "agent_frontends": {"notes_agent": True},
        }
        server.STATE.dynamic_agent_proxy_ports = {"notes_agent": 8094, "admin_agent": 8001}

        webrtc = server.WebRTCManager()

        assert webrtc._resolve_autoyou_forward_url("/agent-frontends") == "http://127.0.0.1:8067/agent-frontends"
        assert webrtc._resolve_autoyou_forward_url("/api/agent-directory") == "http://127.0.0.1:8067/api/agent-directory"
        assert webrtc._resolve_autoyou_forward_url("/api/chat") == "http://127.0.0.1:8081/api/chat"
        assert webrtc._resolve_autoyou_forward_url("/api/status") == "http://127.0.0.1:8081/api/status"
        assert (
            webrtc._resolve_autoyou_forward_url("/api/sessions/user-123/session-456")
            == "http://127.0.0.1:8081/api/sessions/user-123/session-456"
        )
        assert webrtc._resolve_autoyou_forward_url("/agent/notes_agent/") == "http://127.0.0.1:8067/agent/notes_agent/"
        assert (
            webrtc._resolve_autoyou_forward_url("http://127.0.0.1:9000/agent/notes_agent/api/notes?limit=5")
            == "http://127.0.0.1:8067/agent/notes_agent/api/notes?limit=5"
        )
        assert webrtc._resolve_autoyou_forward_url("/chat") == "http://127.0.0.1:9000/chat"

        server.STATE.config["agent_frontends"]["notes_agent"] = {
            "enabled": True,
            "route_mode": "direct_forward",
        }
        server.STATE.config["admin"] = {"frontend_proxy_enabled": True}
        assert webrtc._resolve_autoyou_forward_url("/agent/notes_agent/") == "http://127.0.0.1:8094/"
        assert (
            webrtc._resolve_autoyou_forward_url("http://127.0.0.1:8094/agent/admin_agent/login?next=browser")
            == "http://127.0.0.1:8001/login?next=browser"
        )
        assert (
            webrtc._resolve_autoyou_forward_url("http://127.0.0.1:3100/dashboard")
            == "http://127.0.0.1:3200/dashboard"
        )
    finally:
        server.STATE.dynamic_agent_proxy_ports = original_dynamic_agent_proxy_ports
        server.STATE.main_server_port = original_main_server_port


def test_websocket_route_denial_never_falls_back_to_http_only_loopback_target(monkeypatch):
    original_config = server.STATE.config
    original_dynamic_agent_proxy_ports = dict(getattr(server.STATE, "dynamic_agent_proxy_ports", {}) or {})
    original_main_server_port = getattr(server.STATE, "main_server_port", server.AI_AGENT_SERVER_PORT)
    try:
        server.STATE.config = {
            "autoyou_page": {
                "port": 8067,
                "custom_forward_enabled": True,
                "custom_forward_port": 8001,
            },
            "agent_frontends": {"admin_agent": True},
        }
        server.STATE.dynamic_agent_proxy_ports = {"admin_agent": 8001}
        server.STATE.main_server_port = 8081
        monkeypatch.setattr(
            server,
            "_frontend_registry_route_for_port",
            lambda port: {
                "route_id": "agent:admin_agent",
                "websocket_enabled": False,
            }
            if int(port) == 8001
            else None,
        )

        webrtc = server.WebRTCManager()

        assert webrtc._resolve_autoyou_forward_url(
            "http://127.0.0.1:8001/ws",
            websocket=True,
        ) == ""
        assert webrtc._resolve_autoyou_forward_url("/ws", websocket=True) == ""
        assert server._normalize_client_loopback_target(
            "ws://127.0.0.1:8067/ws",
            proxy_target="http://127.0.0.1:8067",
            websocket=True,
            advertised_websites=[],
        ) == ""
        assert server._normalize_client_loopback_target(
            "ws://127.0.0.1:8081/ws",
            proxy_target="http://127.0.0.1:8081",
            websocket=True,
            advertised_websites=[],
        ) == ""
        monkeypatch.setattr(server, "_frontend_registry_route_for_port", lambda port: None)
        assert server._normalize_client_loopback_target(
            "ws://127.0.0.1:8001/ws",
            proxy_target="http://127.0.0.1:8001",
            websocket=True,
            advertised_websites=[],
        ) == ""
    finally:
        server.STATE.config = original_config
        server.STATE.dynamic_agent_proxy_ports = original_dynamic_agent_proxy_ports
        server.STATE.main_server_port = original_main_server_port


def test_websocket_route_opt_in_still_allows_absolute_and_path_only_targets(monkeypatch):
    original_config = server.STATE.config
    original_dynamic_agent_proxy_ports = dict(getattr(server.STATE, "dynamic_agent_proxy_ports", {}) or {})
    original_main_server_port = getattr(server.STATE, "main_server_port", server.AI_AGENT_SERVER_PORT)
    try:
        server.STATE.config = {
            "autoyou_page": {
                "port": 8067,
                "custom_forward_enabled": True,
                "custom_forward_port": 8001,
            },
            "agent_frontends": {"admin_agent": True},
        }
        server.STATE.dynamic_agent_proxy_ports = {"admin_agent": 8001}
        server.STATE.main_server_port = 8081
        monkeypatch.setattr(
            server,
            "_frontend_registry_route_for_port",
            lambda port: {
                "route_id": "agent:admin_agent",
                "websocket_enabled": True,
            }
            if int(port) == 8001
            else None,
        )

        webrtc = server.WebRTCManager()

        assert webrtc._resolve_autoyou_forward_url(
            "http://127.0.0.1:8001/ws",
            websocket=True,
        ) == "ws://127.0.0.1:8001/ws"
        assert webrtc._resolve_autoyou_forward_url("/ws", websocket=True) == "ws://127.0.0.1:8001/ws"
    finally:
        server.STATE.config = original_config
        server.STATE.dynamic_agent_proxy_ports = original_dynamic_agent_proxy_ports
        server.STATE.main_server_port = original_main_server_port


def test_datachannel_forwarder_uses_long_timeout_for_ai_rest_paths(monkeypatch):
    monkeypatch.setenv("AUTOYOU_DATACHANNEL_AI_HTTP_TIMEOUT_SECONDS", "123")

    assert server._get_datachannel_http_request_timeout_seconds("/api/chat") == 123.0
    assert server._get_datachannel_http_request_timeout_seconds("/api/status") == 123.0
    assert server._get_datachannel_http_request_timeout_seconds("/chat") == 60.0
