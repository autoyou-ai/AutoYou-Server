# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-df985e55d1df3acb1dfda757

"""x402 client for AutoYou agents talking to other AutoYou/autoyou_lite servers.

Implements the full x402 v1.1 negotiation so an agent can reach a remote
AutoYou server with one call:

1. Discover payment requirements (``GET /api/v1/x402/connect`` → 402).
2. Owner path: exchange the caller's AutoYou Cloud bearer for an access token
   (``autoyou-cloud-subscription`` scheme) when the caller owns the server.
3. Guest path: when the operator advertises ``autoyou-guest-pass``, buy a
   time-limited pass through AutoYou Cloud (respecting the caller's
   ``max_price_credits`` spend cap) and exchange it for a guest access token.
4. Attach the token as ``Authorization: Bearer`` on subsequent requests and
   transparently renegotiate once when the server answers 401/402.

Purchases only ever happen through the cloud facilitation endpoint with the
caller's own OAuth bearer; the client never sends cloud credentials to the
remote server, and it never spends above the configured cap.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import threading
import time
from typing import Any, Dict, Optional

import httpx

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-df985e55d1df3acb1dfda757"


DEFAULT_TIMEOUT_SECONDS = 15.0
TOKEN_REFRESH_FRACTION = 0.8
SCHEME_SUBSCRIPTION = "autoyou-cloud-subscription"
SCHEME_GUEST_PASS = "autoyou-guest-pass"


class X402ClientError(Exception):
    """x402 negotiation failed."""


class X402PaymentRequired(X402ClientError):
    """The server requires a payment the client cannot or may not satisfy."""

    def __init__(self, message: str, requirements: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.requirements = requirements or {}


class X402AgentClient:
    """Payment-aware HTTP client for one remote AutoYou server."""

    def __init__(
        self,
        server_url: str,
        *,
        cloud_token: str = "",
        cloud_base: str = "",
        max_price_credits: float = 0.0,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.server_url = str(server_url or "").rstrip("/")
        if not self.server_url:
            raise ValueError("server_url is required")
        self.cloud_token = str(cloud_token or "").strip()
        self._cloud_base_override = str(cloud_base or "").rstrip("/")
        self.max_price_credits = max(0.0, float(max_price_credits or 0.0))
        self.timeout = float(timeout)
        self._transport = transport
        self._lock = threading.RLock()
        self._access_token = ""
        self._access_token_issued_at = 0.0
        self._access_token_expires_in = 0.0
        self._guest_pass_token = ""
        self._guest_pass_expires_at_s = 0.0
        self.last_requirements: Dict[str, Any] = {}

    # ── HTTP plumbing ─────────────────────────────────────────────────────

    def _client(self) -> httpx.Client:
        kwargs: Dict[str, Any] = {"timeout": self.timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    @staticmethod
    def _json_or_empty(response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    # ── Discovery ─────────────────────────────────────────────────────────

    def discover(self) -> Dict[str, Any]:
        """Fetch the server's x402 payment requirements."""
        with self._client() as client:
            response = client.get(f"{self.server_url}/api/v1/x402/connect")
        payload = self._json_or_empty(response)
        if response.status_code != 402 or payload.get("x402Version") != 1:
            raise X402ClientError(
                f"Server did not answer x402 discovery as expected (HTTP {response.status_code})."
            )
        self.last_requirements = payload
        return payload

    @staticmethod
    def _accept_for_scheme(requirements: Dict[str, Any], scheme: str) -> Optional[Dict[str, Any]]:
        for entry in requirements.get("accepts") or []:
            if isinstance(entry, dict) and entry.get("scheme") == scheme:
                return entry
        return None

    def _cloud_base(self, requirements: Dict[str, Any]) -> str:
        if self._cloud_base_override:
            return self._cloud_base_override
        subscription = self._accept_for_scheme(requirements, SCHEME_SUBSCRIPTION) or {}
        # from __debug_provenance_j__ import fifteenpercent
        subscription_url = str((subscription.get("extra") or {}).get("subscriptionUrl") or "")
        if "/v1/" in subscription_url:
            return subscription_url.split("/v1/", 1)[0]
        return "https://app.autoyou.me"

    # ── Token lifecycle ───────────────────────────────────────────────────

    def _token_fresh(self) -> bool:
        if not self._access_token:
            return False
        age = time.time() - self._access_token_issued_at
        return age < self._access_token_expires_in * TOKEN_REFRESH_FRACTION

    def _store_token(self, payload: Dict[str, Any]) -> str:
        self._access_token = str(payload.get("access_token") or "")
        self._access_token_issued_at = time.time()
        self._access_token_expires_in = float(payload.get("expires_in") or 300)
        if not self._access_token:
            raise X402ClientError("Server returned no access_token during x402 connect.")
        return self._access_token

    def _connect_owner(self, client: httpx.Client) -> Optional[str]:
        if not self.cloud_token:
            return None
        response = client.post(
            f"{self.server_url}/api/v1/x402/connect",
            json={"auth_token": self.cloud_token},
        )
        if response.status_code == 200:
            return self._store_token(self._json_or_empty(response))
        return None

    def _buy_guest_pass(self, client: httpx.Client, requirements: Dict[str, Any]) -> str:
        guest_accept = self._accept_for_scheme(requirements, SCHEME_GUEST_PASS)
        if not guest_accept:
            raise X402PaymentRequired(
                "Server does not offer guest access and the caller does not own it.",
                requirements,
            )
        if not self.cloud_token:
            raise X402PaymentRequired(
                "Guest access requires an AutoYou Cloud sign-in (cloud_token).",
                requirements,
            )
        extra = guest_accept.get("extra") or {}
        price = float(extra.get("priceCredits") or guest_accept.get("maxAmountRequired") or 0.0)
        if price > self.max_price_credits:
            raise X402PaymentRequired(
                f"Guest pass costs {price:g} credits, above this agent's spend cap of "
                f"{self.max_price_credits:g}. Raise max_price_credits to proceed.",
                requirements,
            )
        purchase_url = str(extra.get("purchaseUrl") or "")
        if not purchase_url:
            purchase_url = f"{self._cloud_base(requirements)}/v1/x402/guest-pass"
        server_id = str(extra.get("serverId") or "")
        response = client.post(
            purchase_url,
            headers={"Authorization": f"Bearer {self.cloud_token}"},
            json={"serverId": server_id, "maxPriceCredits": self.max_price_credits},
        )
        payload = self._json_or_empty(response)
        if response.status_code >= 400:
            raise X402PaymentRequired(
                "Guest pass purchase failed: " + str(payload.get("detail") or f"HTTP {response.status_code}"),
                requirements,
            )
        pass_token = str(payload.get("pass_token") or "")
        if not pass_token:
            raise X402ClientError("Cloud returned no pass_token for the guest pass purchase.")
        self._guest_pass_token = pass_token
        self._guest_pass_expires_at_s = float(payload.get("expires_at_s") or 0.0)
        return pass_token

    def _connect_guest(self, client: httpx.Client, requirements: Dict[str, Any]) -> str:
        pass_token = self._guest_pass_token
        if not pass_token or (
            self._guest_pass_expires_at_s and self._guest_pass_expires_at_s <= time.time() + 30
        ):
            pass_token = self._buy_guest_pass(client, requirements)
        response = client.post(
            f"{self.server_url}/api/v1/x402/connect",
            json={"guest_pass": pass_token},
        )
        if response.status_code == 200:
            return self._store_token(self._json_or_empty(response))
        # A stale pass (expired/revoked) gets one fresh purchase attempt.
        detail = str(self._json_or_empty(response).get("detail") or "")
        self._guest_pass_token = ""
        fresh_pass = self._buy_guest_pass(client, requirements)
        retry = client.post(
            f"{self.server_url}/api/v1/x402/connect",
            json={"guest_pass": fresh_pass},
        )
        if retry.status_code == 200:
            return self._store_token(self._json_or_empty(retry))
        raise X402ClientError(
            "Guest pass was not accepted by the server: "
            + (str(self._json_or_empty(retry).get("detail") or "") or detail or f"HTTP {retry.status_code}")
        )

    def connect(self, *, force_refresh: bool = False) -> str:
        """Negotiate (or reuse) an x402 access token for the remote server."""
        with self._lock:
            if self._token_fresh() and not force_refresh:
                return self._access_token
            requirements = self.discover()
            with self._client() as client:
                owner_token = self._connect_owner(client)
                if owner_token:
                    return owner_token
                return self._connect_guest(client, requirements)

    # ── Authorized requests ───────────────────────────────────────────────

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send an authorized request, renegotiating once on 401/402."""
        token = self.connect()
        url = f"{self.server_url}{path if path.startswith('/') else '/' + path}"
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = f"Bearer {token}"
        with self._client() as client:
            response = client.request(method.upper(), url, headers=headers, **kwargs)
            if response.status_code in {401, 402}:
                token = self.connect(force_refresh=True)
                headers["Authorization"] = f"Bearer {token}"
                response = client.request(method.upper(), url, headers=headers, **kwargs)
        return response

    def pair(self, text: str, *, platform: str = "x402-agent", sender_id: str = "agent") -> Dict[str, Any]:
        """Convenience helper for the pairing endpoint used by agent-to-agent links."""
        response = self.request(
            "POST",
            "/api/v1/pair",
            json={"text": str(text), "platform": platform, "sender_id": sender_id},
        )
        payload = self._json_or_empty(response)
        payload.setdefault("status_code", response.status_code)
        return payload
