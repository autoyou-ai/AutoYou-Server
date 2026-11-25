"""Remote guessing shares a cap without consuming the local operator's budget."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import server


@pytest.fixture
def unlock_app(monkeypatch):
    monkeypatch.setattr(server, "LOGIN_RATE_LIMITER", server.RateLimiter(max_requests=2, window_seconds=60))
    monkeypatch.setattr(server, "_get_unlock_state", lambda: "Locked")
    monkeypatch.setattr(server, "_load_unlock_metadata", lambda: {"salt_hex": "00", "hash_hex": "00"})
    monkeypatch.setattr(server, "_agreement_metadata_is_current", lambda _: True)
    monkeypatch.setattr(server, "is_license_acknowledgement_pending_unlock", lambda **_: False)
    monkeypatch.setattr(server, "_verify_password", lambda *_: False)
    app = FastAPI()
    app.router.routes.extend(route for route in server.admin_app.routes if getattr(route, "path", "") == "/v1/unlock/verify")
    return app


def test_remote_unlock_exhaustion_does_not_block_local_operator(unlock_app):
    with TestClient(unlock_app, client=("127.0.0.1", 50000)) as client:
        remote = {"X-AutoYou-Remote-Browser": "webrtc"}
        for _ in range(2):
            assert client.post("/v1/unlock/verify", json={"password": "synthetic-wrong"}, headers=remote).status_code == 401
        assert client.post("/v1/unlock/verify", json={"password": "synthetic-wrong"}, headers=remote).status_code == 429
        assert client.post("/v1/unlock/verify", json={"password": "synthetic-wrong"}).status_code == 401


def test_rotating_remote_session_and_bridge_ip_cannot_multiply_guesses(unlock_app):
    with TestClient(unlock_app, client=("127.0.0.1", 50000)) as client:
        results = [client.post("/v1/unlock/verify", json={"password": "synthetic-wrong"}, headers={
            "X-AutoYou-Remote-Browser": "webrtc", "X-AutoYou-WebRTC-Session-Id": f"untrusted-{index}",
            "X-AutoYou-Tunnel-Client-IP": f"198.51.100.{index + 1}",
        }).status_code for index in range(4)]
    assert results == [401, 401, 429, 429]


def test_remote_cannot_claim_local_bucket_with_headers(unlock_app):
    with TestClient(unlock_app, client=("198.51.100.20", 50000), base_url="https://testserver") as remote:
        for _ in range(2):
            assert remote.post("/v1/unlock/verify", json={"password": "synthetic-wrong"}).status_code == 401
        assert remote.post("/v1/unlock/verify", json={"password": "synthetic-wrong"}, headers={
            "X-AutoYou-Local-Loopback": "1", "X-AutoYou-Tunnel-Client-IP": "127.0.0.1",
        }).status_code == 429
    with TestClient(unlock_app, client=("::1", 50000)) as local:
        assert local.post("/v1/unlock/verify", json={"password": "synthetic-wrong"}).status_code == 401
