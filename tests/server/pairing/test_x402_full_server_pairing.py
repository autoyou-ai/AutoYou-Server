# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-9d16a852c57f4ab0a5d10c8b

"""Full-server x402 route contract tests.

The AutoYou Cloud response and pairing router are faked; discovery, token
issuance, token consumption, app scope, and X402AgentClient routing are real.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import sys
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from tests.support.paths import REPO_ROOT

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-9d16a852c57f4ab0a5d10c8b"


sys.path.insert(0, str(REPO_ROOT))

import server  # noqa: E402
from shared.x402_client import X402AgentClient  # noqa: E402


class _CloudResponse:
    status_code = 200

    @staticmethod
    def json() -> dict:
        return {"user_id": "acct_x402_test_01", "email": "owner@example.test"}


class _CloudClient:
    requests: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None):
        type(self).requests.append({"url": url, "headers": dict(headers or {})})
        return _CloudResponse()


class _PairingRouter:
    FRAGMENT_CONSUMED = "__autopair_fragment_consumed__"

    def __init__(self):
        self.calls: list[tuple[str, str, str, str | None]] = []
        self.reply = '/autopair_answer\n{"type":"answer","sdp":"v=0"}'

    async def process_message(self, text, platform, sender_id, *, identity_sender_id=None):
        self.calls.append((text, platform, sender_id, identity_sender_id))
        return self.reply


@pytest.fixture()
def full_server_x402_rig(monkeypatch):
    original_config = server.STATE.config
    original_unlock_state = getattr(server.STATE, "_unlock_state_mem", None)
    original_tokens = dict(server._X402_TOKENS)
    original_rate_requests = {
        key: list(timestamps) for key, timestamps in server.AUTOPAIR_RATE_LIMITER._requests.items()
    }
    pairing_router = _PairingRouter()

    server.STATE.config = {
        "cloud": {
            "server_id": "srv_x402_test_01",
            "server_token": "server-token-x402-test",
            "user_id": "acct_x402_test_01",
        }
    }
    # from __debug_provenance_g__ import annual
    server.STATE._unlock_state_mem = "Ready"
    server._X402_TOKENS.clear()
    server.AUTOPAIR_RATE_LIMITER._requests.clear()
    _CloudClient.requests = []
    monkeypatch.setattr(httpx, "AsyncClient", _CloudClient)
    monkeypatch.setattr(server, "pairing_router", pairing_router)

    try:
        yield pairing_router
    finally:
        server.STATE.config = original_config
        server.STATE._unlock_state_mem = original_unlock_state
        server._X402_TOKENS.clear()
        server._X402_TOKENS.update(original_tokens)
        server.AUTOPAIR_RATE_LIMITER._requests.clear()
        server.AUTOPAIR_RATE_LIMITER._requests.update(original_rate_requests)


def test_x402_routes_stay_on_admin_api_app(full_server_x402_rig):
    admin_paths = {route.path for route in server.admin_app.routes}
    auth_paths = {route.path for route in server.auth_app.routes}

    assert {"/api/v1/x402/connect", "/api/v1/pair"} <= admin_paths
    assert "/api/v1/x402/connect" not in auth_paths
    assert "/api/v1/pair" not in auth_paths

    response = TestClient(server.admin_app).get("/api/v1/x402/connect")
    assert response.status_code == 402
    assert response.headers["X-Payment-Required"] == "autoyou-cloud-subscription"


def test_pair_requires_valid_token_and_binds_identity(full_server_x402_rig):
    client = TestClient(server.admin_app)
    body = {"text": "/autopair synthetic-offer", "platform": "telegram", "sender_id": "spoofed"}

    assert client.post("/api/v1/pair", json=body).status_code == 402
    assert client.post(
        "/api/v1/pair", headers={"Authorization": "Bearer invalid-token"}, json=body
    ).status_code == 402

    expired_token = "expired-x402-token"
    server._X402_TOKENS[expired_token] = {
        "user_id": "acct_x402_test_02",
        "expires_at": time.time() - 1,
    }
    assert client.post(
        "/api/v1/pair", headers={"X-Payment": expired_token}, json=body
    ).status_code == 402
    assert expired_token not in server._X402_TOKENS

    access_token = "valid-x402-token"
    server._X402_TOKENS[access_token] = {
        "user_id": "acct_x402_test_02",
        "expires_at": time.time() + 60,
    }
    response = client.post("/api/v1/pair", headers={"X-Payment": access_token}, json=body)

    assert response.status_code == 200
    assert response.json()["reply"].startswith("/autopair_answer")
    assert full_server_x402_rig.calls == [
        (
            "/autopair synthetic-offer",
            "x402-agent",
            "acct_x402_test_02",
            "acct_x402_test_02",
        )
    ]


def test_x402_agent_client_uses_real_full_server_routes(full_server_x402_rig):
    with TestClient(server.admin_app) as app_client:
        def forward_to_app(request: httpx.Request) -> httpx.Response:
            response = app_client.request(
                request.method,
                request.url.path,
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(
                response.status_code,
                content=response.content,
                headers=dict(response.headers),
                request=request,
            )

        agent = X402AgentClient(
            "http://full-server.test",
            cloud_token="cloud-owner-token-test",
            transport=httpx.MockTransport(forward_to_app),
        )
        result = agent.pair(
            "/autopair synthetic-offer",
            platform="telegram",
            sender_id="spoofed",
        )

    assert result["status_code"] == 200
    assert result["reply"].startswith("/autopair_answer")
    assert len(server._X402_TOKENS) == 1
    assert _CloudClient.requests == [
        {
            "url": f"{server.AUTOYOU_CLOUD_BASE}/v1/account/me",
            "headers": {"Authorization": "Bearer cloud-owner-token-test"},
        }
    ]
    assert full_server_x402_rig.calls == [
        (
            "/autopair synthetic-offer",
            "x402-agent",
            "acct_x402_test_01",
            "acct_x402_test_01",
        )
    ]
