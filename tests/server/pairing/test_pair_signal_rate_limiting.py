# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-17d69f458af0ed967b8a95fa


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-17d69f458af0ed967b8a95fa"


def _candidate_payload(index: int) -> dict:
    return {
        "type": "candidate",
        "candidate": f"candidate:1 1 UDP 2122260223 192.0.2.{index} {5000 + index} typ host",
        "sdpMid": "0",
        "sdpMLineIndex": 0,
    }


@pytest.fixture(autouse=True)
def _clear_rate_limiters(monkeypatch):
    monkeypatch.setattr(server, "_get_unlock_state", lambda: "Ready")
    for limiter_name in (
        "AUTH_RATE_LIMITER",
        "AUTH_GLOBAL_RATE_LIMITER",
        "LOGIN_RATE_LIMITER",
        "SIGNAL_OFFER_RATE_LIMITER",
        "SIGNAL_CANDIDATE_RATE_LIMITER",
        "UNAUTHENTICATED_SIGNAL_RATE_LIMITER",
    ):
        getattr(server, limiter_name)._requests.clear()
    yield
    for limiter_name in (
        "AUTH_RATE_LIMITER",
        "AUTH_GLOBAL_RATE_LIMITER",
        "LOGIN_RATE_LIMITER",
        "SIGNAL_OFFER_RATE_LIMITER",
        "SIGNAL_CANDIDATE_RATE_LIMITER",
        "UNAUTHENTICATED_SIGNAL_RATE_LIMITER",
    ):
        getattr(server, limiter_name)._requests.clear()


def test_auth_uses_trusted_bridge_client_ip_for_rate_limit(monkeypatch):
    monkeypatch.setattr(server, "AUTH_RATE_LIMITER", server.RateLimiter(max_requests=1, window_seconds=60))

    async def fake_auth_request(_payload):
        return {"success": True, "session_id": "session-1", "session": {}}

    async def fake_ice_servers():
        return []

    monkeypatch.setattr(server, "handle_auth_request", fake_auth_request)
    monkeypatch.setattr(server, "_get_pairing_ice_servers_async", fake_ice_servers)
    monkeypatch.setattr(server, "_get_stable_server_id", lambda: "server-1")
    monkeypatch.setattr(server, "_get_server_identity_key", lambda: "server-key")
    monkeypatch.setattr(server, "get_configured_server_name", lambda: "AutoYou")

    client = TestClient(server.auth_app)

    first = client.post(
        "/auth",
        json={"hash": "hash-one"},
        headers={"X-AutoYou-Tunnel-Client-IP": "198.51.100.10"},
    )
    second = client.post(
        "/auth",
        json={"hash": "hash-two"},
        headers={"X-AutoYou-Tunnel-Client-IP": "198.51.100.11"},
    )

    assert first.status_code == 200
    assert second.status_code == 200


def test_auth_rate_limit_blocks_same_tunnel_client(monkeypatch):
    monkeypatch.setattr(server, "AUTH_RATE_LIMITER", server.RateLimiter(max_requests=1, window_seconds=60))

    async def fake_auth_request(_payload):
        return {"success": True, "session_id": "session-1", "session": {}}

    async def fake_ice_servers():
        return []

    monkeypatch.setattr(server, "handle_auth_request", fake_auth_request)
    monkeypatch.setattr(server, "_get_pairing_ice_servers_async", fake_ice_servers)
    monkeypatch.setattr(server, "_get_stable_server_id", lambda: "server-1")
    monkeypatch.setattr(server, "_get_server_identity_key", lambda: "server-key")
    monkeypatch.setattr(server, "get_configured_server_name", lambda: "AutoYou")

    client = TestClient(server.auth_app)
    headers = {"X-AutoYou-Tunnel-Client-IP": "198.51.100.10"}

    first = client.post("/auth", json={"hash": "hash-one"}, headers=headers)
    second = client.post("/auth", json={"hash": "hash-two"}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429


def test_auth_global_rate_limit_blocks_distributed_tunnel_clients(monkeypatch):
    monkeypatch.setattr(server, "AUTH_RATE_LIMITER", server.RateLimiter(max_requests=20, window_seconds=60))
    monkeypatch.setattr(server, "AUTH_GLOBAL_RATE_LIMITER", server.RateLimiter(max_requests=1, window_seconds=60))

    async def fake_auth_request(_payload):
        return {"success": True, "session_id": "session-1", "session": {}}

    async def fake_ice_servers():
        return []

    monkeypatch.setattr(server, "handle_auth_request", fake_auth_request)
    monkeypatch.setattr(server, "_get_pairing_ice_servers_async", fake_ice_servers)
    monkeypatch.setattr(server, "_get_stable_server_id", lambda: "server-1")
    monkeypatch.setattr(server, "_get_server_identity_key", lambda: "server-key")
    monkeypatch.setattr(server, "get_configured_server_name", lambda: "AutoYou")

    client = TestClient(server.auth_app)

    first = client.post(
        "/auth",
        json={"hash": "hash-one"},
        headers={"X-AutoYou-Tunnel-Client-IP": "198.51.100.10"},
    )
    second = client.post(
        "/auth",
        json={"hash": "hash-two"},
        headers={"X-AutoYou-Tunnel-Client-IP": "198.51.100.11"},
    )

    assert first.status_code == 200
    assert second.status_code == 429


def test_authenticated_candidate_burst_uses_candidate_limiter(monkeypatch):
    monkeypatch.setattr(server, "SIGNAL_OFFER_RATE_LIMITER", server.RateLimiter(max_requests=1, window_seconds=60))
    monkeypatch.setattr(server, "SIGNAL_RATE_LIMITER", server.SIGNAL_OFFER_RATE_LIMITER)
    monkeypatch.setattr(server, "SIGNAL_CANDIDATE_RATE_LIMITER", server.RateLimiter(max_requests=3, window_seconds=60))
    monkeypatch.setattr(server, "UNAUTHENTICATED_SIGNAL_RATE_LIMITER", server.RateLimiter(max_requests=1, window_seconds=60))
    monkeypatch.setattr(server, "_rate_limit_client_key", lambda request: "client-a")
    monkeypatch.setattr(server, "_is_authenticated_pair_session", lambda session_id: True)
    handle_candidate = AsyncMock(return_value=True)
    monkeypatch.setattr(server.WEBRTC, "handle_session_candidate", handle_candidate)

    client = TestClient(server.auth_app)

    for index in range(3):
        response = client.post("/signal/session-1", json=_candidate_payload(index))
        assert response.status_code == 200

    response = client.post("/signal/session-1", json=_candidate_payload(99))
    assert response.status_code == 429
    assert handle_candidate.await_count == 3


def test_authenticated_candidate_limit_isolated_per_session(monkeypatch):
    monkeypatch.setattr(server, "SIGNAL_CANDIDATE_RATE_LIMITER", server.RateLimiter(max_requests=1, window_seconds=60))
    monkeypatch.setattr(server, "SIGNAL_OFFER_RATE_LIMITER", server.RateLimiter(max_requests=1, window_seconds=60))
    monkeypatch.setattr(server, "SIGNAL_RATE_LIMITER", server.SIGNAL_OFFER_RATE_LIMITER)
    monkeypatch.setattr(server, "UNAUTHENTICATED_SIGNAL_RATE_LIMITER", server.RateLimiter(max_requests=1, window_seconds=60))
    monkeypatch.setattr(server, "_rate_limit_client_key", lambda request: "client-a")
    monkeypatch.setattr(server, "_is_authenticated_pair_session", lambda session_id: True)
    handle_candidate = AsyncMock(return_value=True)
    monkeypatch.setattr(server.WEBRTC, "handle_session_candidate", handle_candidate)

    client = TestClient(server.auth_app)

    response_a = client.post("/signal/session-a", json=_candidate_payload(1))
    response_b = client.post("/signal/session-b", json=_candidate_payload(2))

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert handle_candidate.await_count == 2


def test_authenticated_offer_uses_tighter_offer_limiter(monkeypatch):
    monkeypatch.setattr(server, "SIGNAL_OFFER_RATE_LIMITER", server.RateLimiter(max_requests=1, window_seconds=60))
    monkeypatch.setattr(server, "SIGNAL_RATE_LIMITER", server.SIGNAL_OFFER_RATE_LIMITER)
    monkeypatch.setattr(server, "SIGNAL_CANDIDATE_RATE_LIMITER", server.RateLimiter(max_requests=5, window_seconds=60))
    monkeypatch.setattr(server, "UNAUTHENTICATED_SIGNAL_RATE_LIMITER", server.RateLimiter(max_requests=1, window_seconds=60))
    monkeypatch.setattr(server, "_rate_limit_client_key", lambda request: "client-a")
    monkeypatch.setattr(server, "_is_authenticated_pair_session", lambda session_id: True)
    handle_offer = AsyncMock(return_value={"type": "answer", "sdp": "v=0"})
    monkeypatch.setattr(server.WEBRTC, "handle_session_offer", handle_offer)

    client = TestClient(server.auth_app)
    offer_payload = {"type": "offer", "sdp": "v=0"}

    first = client.post("/signal/session-1", json=offer_payload)
    second = client.post("/signal/session-1", json=offer_payload)

    assert first.status_code == 200
    assert second.status_code == 429
    assert handle_offer.await_count == 1


@pytest.mark.asyncio
async def test_handle_session_candidate_deduplicates_pending_candidates():
    manager = server.WebRTCManager()
    candidate = _candidate_payload(7)

    first = await manager.handle_session_candidate("session-1", dict(candidate))
    second = await manager.handle_session_candidate("session-1", dict(candidate))

    assert first is True
    assert second is True
    assert len(manager.pending_candidates["session-1"]) == 1


@pytest.mark.asyncio
async def test_cloud_server_ice_drain_stops_when_gathering_complete(monkeypatch):
    class Router:
        def __init__(self):
            self.calls = 0
            # from __debug_provenance_c__ import subtask

        async def get_trickle_candidates(self, session_id: str, platform: str = "") -> list[dict]:
            self.calls += 1
            return [_candidate_payload(8)]

        def format_autopair_candidates(self, session_id: str, candidates: list[dict], **_kwargs) -> str:
            return "/autopair_candidates\n{}"

    pushes: list[tuple[str, str]] = []

    async def push_to_client(command: str, payload: str, *, client_device_id: str = "") -> None:
        pushes.append((command, payload))
        assert client_device_id == "synthetic-client"

    router = Router()
    monkeypatch.setattr(
        server,
        "WEBRTC",
        SimpleNamespace(session_peers={"relay-done": SimpleNamespace(iceGatheringState="complete")}),
    )
    monkeypatch.setattr(server, "_push_to_client", push_to_client)

    await server._drain_cloud_server_ice("relay-done", router, identity_sender_id="synthetic-client")

    assert router.calls == 1
    assert pushes == [("/autopair_candidates", "/autopair_candidates\n{}")]
