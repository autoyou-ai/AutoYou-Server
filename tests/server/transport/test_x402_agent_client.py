# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Tests for the x402 agent client used for agent-to-agent AutoYou links.

A httpx.MockTransport plays the remote AutoYou server AND the AutoYou Cloud
facilitation endpoint, so the full negotiation (discover → owner attempt →
guest-pass purchase with spend cap → guest token → authorized request) is
exercised without any network access.
"""

from __future__ import annotations

import json
import sys
import time

import httpx
import pytest

from tests.support.paths import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from shared.x402_client import (  # noqa: E402
    X402AgentClient,
    X402ClientError,
    X402PaymentRequired,
)

SERVER = "http://server.test"
CLOUD = "http://cloud.test"


class FakeWorld:
    """Simulated operator server + cloud facilitation state."""

    def __init__(self, *, price=2.0, guest_enabled=True, owner_token=""):
        self.price = price
        self.guest_enabled = guest_enabled
        self.owner_token = owner_token
        self.passes: dict[str, float] = {}
        self.tokens: dict[str, dict] = {}
        self.purchases = 0

    def requirements(self) -> dict:
        accepts = [
            {
                "scheme": "autoyou-cloud-subscription",
                "network": "autoyou",
                "maxAmountRequired": "0",
                "payTo": "autoyou-cloud",
                "asset": "subscription",
                "extra": {"subscriptionUrl": f"{CLOUD}/v1/server/link"},
            }
        ]
        if self.guest_enabled:
            accepts.append(
                {
                    "scheme": "autoyou-guest-pass",
                    "network": "autoyou",
                    "maxAmountRequired": f"{self.price:g}",
                    "payTo": "srv_fake_00001",
                    "asset": "autoyou_credit",
                    "extra": {
                        "purchaseUrl": f"{CLOUD}/v1/x402/guest-pass",
                        "serverId": "srv_fake_00001",
                        "priceCredits": self.price,
                        "passTtlSeconds": 3600,
                    },
                }
            )
        return {"x402Version": 1, "accepts": accepts, "error": "X-PAYMENT-REQUIRED"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = {}
        if request.content:
            try:
                body = json.loads(request.content.decode("utf-8"))
            except Exception:
                body = {}

        if url == f"{SERVER}/api/v1/x402/connect" and request.method == "GET":
            return httpx.Response(402, json=self.requirements())

        if url == f"{SERVER}/api/v1/x402/connect" and request.method == "POST":
            auth_token = str(body.get("auth_token") or "")
            guest_pass = str(body.get("guest_pass") or "")
            if auth_token and self.owner_token and auth_token == self.owner_token:
                token = f"owner-token-{len(self.tokens)}"
                self.tokens[token] = {"kind": "owner"}
                return httpx.Response(200, json={"access_token": token, "expires_in": 300})
            if guest_pass:
                expires = self.passes.get(guest_pass, 0.0)
                if expires > time.time():
                    token = f"guest-token-{len(self.tokens)}"
                    self.tokens[token] = {"kind": "guest"}
                    return httpx.Response(
                        200, json={"access_token": token, "expires_in": 600, "kind": "guest"}
                    )
                return httpx.Response(
                    402, json={**self.requirements(), "detail": "Guest pass has expired"}
                )
            return httpx.Response(402, json={**self.requirements(), "detail": "payment required"})

        if url == f"{CLOUD}/v1/x402/guest-pass" and request.method == "POST":
            if request.headers.get("Authorization") != "Bearer guest-cloud-token":
                return httpx.Response(401, json={"detail": "You must be signed in."})
            self.purchases += 1
            pass_token = f"gp_purchase{self.purchases:04d}abcdef"
            self.passes[pass_token] = time.time() + 3600
            return httpx.Response(
                200,
                json={
                    "pass_token": pass_token,
                    "server_id": "srv_fake_00001",
                    "price_credits": self.price,
                    "expires_at_s": self.passes[pass_token],
                },
            )

        if url == f"{SERVER}/api/agent/echo" and request.method == "POST":
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if token not in self.tokens:
                return httpx.Response(401, json={"detail": "unauthorized"})
            return httpx.Response(200, json={"echo": body, "kind": self.tokens[token]["kind"]})

        return httpx.Response(404, json={"detail": f"unrouted: {request.method} {url}"})


def _client(world: FakeWorld, **kwargs) -> X402AgentClient:
    defaults = {
        "cloud_token": "guest-cloud-token",
        "max_price_credits": 5.0,
        "transport": httpx.MockTransport(world.handler),
    }
    defaults.update(kwargs)
    return X402AgentClient(SERVER, **defaults)


def test_owner_path_wins_when_cloud_token_owns_server():
    world = FakeWorld(owner_token="guest-cloud-token")
    client = _client(world)
    token = client.connect()
    assert token.startswith("owner-token-")
    assert world.purchases == 0


def test_guest_negotiation_buys_pass_and_authorizes_requests():
    world = FakeWorld(price=2.0)
    client = _client(world)
    token = client.connect()
    assert token.startswith("guest-token-")
    assert world.purchases == 1

    response = client.request("POST", "/api/agent/echo", json={"hello": "agent"})
    assert response.status_code == 200
    assert response.json()["kind"] == "guest"

    # Cached token is reused: no additional purchase for a second call.
    client.request("POST", "/api/agent/echo", json={"again": True})
    assert world.purchases == 1


def test_spend_cap_blocks_purchase():
    world = FakeWorld(price=9.0)
    client = _client(world, max_price_credits=1.0)
    with pytest.raises(X402PaymentRequired) as excinfo:
        client.connect()
    assert "spend cap" in str(excinfo.value)
    assert world.purchases == 0


def test_no_guest_offer_raises_payment_required():
    world = FakeWorld(guest_enabled=False)
    client = _client(world)
    with pytest.raises(X402PaymentRequired):
        client.connect()


def test_expired_pass_triggers_one_fresh_purchase():
    world = FakeWorld(price=2.0)
    client = _client(world)
    client.connect()
    assert world.purchases == 1

    # Expire everything server-side and locally force a renegotiation.
    for pass_token in list(world.passes):
        world.passes[pass_token] = time.time() - 10
    world.tokens.clear()
    token = client.connect(force_refresh=True)
    assert token.startswith("guest-token-")
    assert world.purchases == 2


def test_missing_cloud_token_cannot_buy():
    world = FakeWorld(price=2.0)
    client = _client(world, cloud_token="")
    with pytest.raises(X402PaymentRequired) as excinfo:
        client.connect()
    assert "cloud" in str(excinfo.value).lower()
    assert world.purchases == 0


def test_discovery_failure_raises_client_error():
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    client = X402AgentClient(SERVER, transport=httpx.MockTransport(broken))
    with pytest.raises(X402ClientError):
        client.discover()
