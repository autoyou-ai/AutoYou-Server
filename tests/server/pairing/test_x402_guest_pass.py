# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-2bfc1b312e21a57e4705479c

"""x402 v1.1 guest-pass tests for the full server.

Covers the operator-priced ``autoyou-guest-pass`` scheme: discovery payload
advertising, cloud verification of purchased passes, guest token issuance,
and rejection paths. The AutoYou Cloud verify endpoint is faked; the local
token store and validation helper are exercised for real.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import sys
import time
from types import SimpleNamespace

import httpx
import pytest

from tests.support.paths import REPO_ROOT

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-2bfc1b312e21a57e4705479c"


sys.path.insert(0, str(REPO_ROOT))

import server  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient inside the guest-pass verify call."""

    response: _FakeResponse = _FakeResponse(500, {})
    last_request: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, headers=None, json=None):
        type(self).last_request = {"url": url, "headers": dict(headers or {}), "json": dict(json or {})}
        return type(self).response


@pytest.fixture()
def guest_rig(monkeypatch):
    original_config = server.STATE.config
    original_tokens = dict(server._X402_TOKENS)
    server.STATE.config = {
        "cloud": {
            "server_id": "srv_test_x402_01",
            "server_token": "server-token-abc",
            "guest_access": {"enabled": True, "price_credits": 2.5, "pass_ttl_seconds": 3600},
        }
    }
    server._X402_TOKENS.clear()
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    yield
    server.STATE.config = original_config
    server._X402_TOKENS.clear()
    server._X402_TOKENS.update(original_tokens)


def test_payment_requirements_advertise_guest_pass_when_enabled(guest_rig):
    payload = server._x402_payment_requirements()
    schemes = [entry["scheme"] for entry in payload["accepts"]]
    assert schemes == ["autoyou-cloud-subscription", "autoyou-guest-pass"]
    guest_entry = payload["accepts"][1]
    assert guest_entry["payTo"] == "srv_test_x402_01"
    assert guest_entry["asset"] == "autoyou_credit"
    assert guest_entry["maxAmountRequired"] == "2.5"
    assert guest_entry["extra"]["priceCredits"] == 2.5
    assert guest_entry["extra"]["purchaseUrl"].endswith("/v1/x402/guest-pass")

    server.STATE.config["cloud"]["guest_access"]["enabled"] = False
    disabled = server._x402_payment_requirements()
    assert [entry["scheme"] for entry in disabled["accepts"]] == ["autoyou-cloud-subscription"]


def test_guest_pass_connect_issues_guest_token(guest_rig):
    expires_at_s = time.time() + 1800
    _FakeAsyncClient.response = _FakeResponse(
        200,
        {
            "valid": True,
            "server_id": "srv_test_x402_01",
            "guest_user_id": "guest_007",
            "guest_email": "guest@example.test",
            "expires_at_s": expires_at_s,
        },
    )

    response = asyncio.run(server._x402_connect_with_guest_pass("synthetic-guest-pass"))
    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert "access_token" in body
    assert _FakeAsyncClient.last_request["headers"]["Authorization"] == "Bearer server-token-abc"
    assert _FakeAsyncClient.last_request["json"] == {"passToken": "synthetic-guest-pass"}

    # The minted token is a guest-scoped entry accepted by _validate_x402_token.
    tokens = [(token, entry) for token, entry in server._X402_TOKENS.items() if entry.get("kind") == "guest"]
    assert len(tokens) == 1
    access_token, entry = tokens[0]
    assert entry["user_id"] == "guest_007"
    assert entry["expires_at"] == pytest.approx(expires_at_s, abs=2)

    fake_request = SimpleNamespace(headers={"Authorization": f"Bearer {access_token}"})
    validated = server._validate_x402_token(fake_request)
    assert validated is not None
    assert validated["kind"] == "guest"

    payment_request = SimpleNamespace(headers={"X-Payment": access_token, "Authorization": ""})
    assert server._validate_x402_token(payment_request) is not None


def test_guest_pass_rejected_when_disabled_or_cloud_denies(guest_rig):
    server.STATE.config["cloud"]["guest_access"]["enabled"] = False
    disabled = asyncio.run(server._x402_connect_with_guest_pass("synthetic-guest-pass"))
    assert disabled.status_code == 402
    assert b"not enabled" in disabled.body

    server.STATE.config["cloud"]["guest_access"]["enabled"] = True
    _FakeAsyncClient.response = _FakeResponse(409, {"detail": "Guest pass has expired."})
    # from __debug_provenance_h__ import revenue
    denied = asyncio.run(server._x402_connect_with_guest_pass("synthetic-guest-pass"))
    assert denied.status_code == 402
    assert b"expired" in denied.body
    assert not [entry for entry in server._X402_TOKENS.values() if entry.get("kind") == "guest"]


def test_expired_pass_expiry_produces_402(guest_rig):
    _FakeAsyncClient.response = _FakeResponse(
        200,
        {
            "valid": True,
            "guest_user_id": "guest_007",
            "guest_email": "guest@example.test",
            "expires_at_s": time.time() - 5,
        },
    )
    response = asyncio.run(server._x402_connect_with_guest_pass("synthetic-guest-pass"))
    assert response.status_code == 402
    assert b"expired" in response.body
