# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-0c72ad8986e477a1abb452ea

"""
cloud_entitlements_client - lazy, SSE-refreshable view of the user's
auth.autoyou.me entitlements.

Designed for AWS t3.tiny: no polling, no startup fetches. Only contacts the
cloud when something the server is doing actually needs an answer.

Trigger model:
1. **One-time fetch on first need** - `.get_services_manifest()` lazily mints
   a worker JWT and pulls `/v1/services` the first time anything calls
   `.get_iceservers_to_advertise()` or `.get_tunnelmole_provision()`.
2. **SSE-driven invalidation** - the existing relay SSE listener calls
   `.invalidate()` when it receives an `entitlement_changed` event. The next
   `.get_*()` call re-fetches; nothing happens in between.
3. **JWT refresh only on 401** - JWT TTL is 300s on the issuer side, but we
   never refresh proactively. Workers we rarely call (STUN, tunnelmole) will
   simply renew on demand; workers we never call generate zero traffic.

Public API is all async; the client is safe to share between coroutines (the
underlying caches are protected by an asyncio.Lock).

The two URLs the client needs:

  account_api_url  the user-facing app surface, e.g. https://app.autoyou.me
                   (what `cloud_api_url` already holds in autoyou_lite config)
  auth_api_url     the JWT issuer + worker manifest, e.g. https://auth.autoyou.me
                   (defaults to swapping `app.` -> `auth.` on account_api_url
                   if not provided explicitly)

`pb_token` is the PocketBase user token issued by account-service via
`/v1/auth/native/token` or `/v1/auth/oauth/.../callback`. The same token is
already stored on the server as `cloud_device_token`; the JWT issuer's
`/v1/token` endpoint accepts it because both auth.autoyou.me and
app.autoyou.me share one PocketBase instance.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import json
import re
import hashlib
import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-0c72ad8986e477a1abb452ea"


LOGGER = logging.getLogger("autoyou.cloud_entitlements")


# How long we trust a cached value before refetching even without an SSE push.
# Worst case for a healthy user: one services-manifest fetch per hour. The
# manifest itself is tiny (~1 KB) so the bandwidth cost is negligible.
_DEFAULT_MANIFEST_TTL_SECONDS = 3600

# Worker creds (TURN) live as long as the worker says they do. We just
# remember the absolute expiry so we never use a stale credential.
_DEFAULT_FALLBACK_CRED_TTL = 60 * 60

# Mint a JWT slightly before strict TTL to absorb clock skew across hosts.
_JWT_REFRESH_SAFETY_SECONDS = 30


def _derive_auth_url(account_api_url: str) -> str:
    """Return https://auth.autoyou.me when account is https://app.autoyou.me.

    Anything else falls back to the same host - supports localhost/dev where
    auth and app might both be served from the same listener.
    """
    if not account_api_url:
        return ""
    parts = urlsplit(account_api_url)
    host = parts.hostname or ""
    if host == "app.autoyou.me":
        return urlunsplit((parts.scheme, "auth.autoyou.me", "", "", ""))
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _empty_manifest() -> dict[str, Any]:
    """Manifest shape used when the cloud is unreachable or returns no data.

    Mirrors the auth-issuer's response so callers don't have to special-case
    the 'no manifest yet' state.
    """
    return {
        "user_id": "",
        "tier": "",
        "entitlements": [],
        "services": {},
        "_synthetic_empty": True,
    }


class CloudEntitlementsClient:
    """Lazy entitlement / service-discovery client. Thread- and coroutine-safe."""

    def __init__(
        self,
        *,
        account_api_url: str,
        pb_token: str,
        auth_api_url: Optional[str] = None,
        manifest_ttl_seconds: int = _DEFAULT_MANIFEST_TTL_SECONDS,
        http_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
    ) -> None:
        self._account_api_url = (account_api_url or "").rstrip("/")
        # Allow explicit env override (used by self-hosted dev / staging).
        env_override = (os.getenv("AUTOYOU_AUTH_API_URL") or "").strip().rstrip("/")
        explicit = (auth_api_url or "").strip().rstrip("/")
        self._auth_api_url = (
            explicit
            or env_override
            or _derive_auth_url(self._account_api_url)
        )
        self._pb_token = (pb_token or "").strip()
        self._manifest_ttl = max(60, int(manifest_ttl_seconds))
        # 4 s is short enough that /auth never blocks long on a slow cloud,
        # but long enough for a healthy round-trip across continents.
        self._http_factory = http_factory or (lambda: httpx.AsyncClient(timeout=4.0))

        self._lock = asyncio.Lock()
        # Cached worker JWT. tuple(token, exp_unix) - exp comes from the
        # `expires_in` value the issuer returns, NOT decoded from the JWT.
        self._jwt: Optional[tuple[str, float]] = None
        # Cached service manifest plus when we fetched it.
        self._manifest: Optional[dict[str, Any]] = None
        self._manifest_fetched_at: float = 0.0
        # Cached TURN creds. tuple(payload, exp_unix).
        self._turn_creds: Optional[tuple[dict[str, Any], float]] = None
        # Cached cloud STUN payload (rarely changes) + fetched_at.
        self._stun_payload: Optional[tuple[dict[str, Any], float]] = None
        self._diagnostics_heartbeat_supported: Optional[bool] = None
        # Mute state - set when the cloud returns 401 for the current token.
        # The hash of the rejected token is stashed so a fresh
        # update_credentials() with a different token un-mutes us; passing
        # the same dead token via repeated update_credentials() (which the
        # main server.py does on every SSE reconnect because it copies the
        # value out of `STATE.config["cloud"]["server_token"]`) keeps us
        # quiet instead of spamming 401 logs.
        self._muted_token_hash: str = ""
        self._cached_client: Optional[httpx.AsyncClient] = None

    # ── Configuration ────────────────────────────────────────────────────

    def _get_http_client(self) -> httpx.AsyncClient:
        """Return a cached httpx.AsyncClient or create a new one."""
        if self._cached_client is None or getattr(self._cached_client, "is_closed", False):
            self._cached_client = self._http_factory()
        return self._cached_client

    async def close(self) -> None:
        """Close the cached HTTP client if it was created."""
        async with self._lock:
            if self._cached_client is not None and not getattr(self._cached_client, "is_closed", False):
                aclose = getattr(self._cached_client, "aclose", None)
                if aclose is not None:
                    await aclose()

    @property
    def account_api_url(self) -> str:
        return self._account_api_url

    async def request_iroh(self, method: str, path: str, payload: dict | None, *, issuer: str,
                           token_fingerprint: str) -> dict:
        from shared.iroh_core import CoreAccessDenied
        if issuer.rstrip("/") != self._account_api_url or method not in {"GET", "POST"} or not re.fullmatch(
            r"/v1/iroh/(?:relay-credentials|endpoints/(?:challenge|associate|[a-z0-9]{15}(?:/(?:ownership|routing))?))", path
        ):
            raise ValueError("invalid native Core request")
        if not self.has_credentials() or token_fingerprint != self._token_fingerprint():
            raise CoreAccessDenied(401)
        client = self._get_http_client()
        try:
            async with client.stream(method, self._account_api_url + path, json=payload,
                                     headers={"Authorization": f"Bearer {self._pb_token}", "User-Agent": "AutoYou/iroh (Core transport)"}, follow_redirects=False) as response:
                if token_fingerprint != self._token_fingerprint():
                    raise CoreAccessDenied(401)
                if response.status_code in {401, 402, 403, 404}:
                    raise CoreAccessDenied(response.status_code)
                if response.status_code >= 300:
                    raise ConnectionError("Core routing request is unavailable")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > 64 * 1024:
                        raise ValueError("Core response exceeds its protocol limit")
                    raw.extend(chunk)
        except httpx.RequestError:
            raise ConnectionError("Core routing request is unavailable") from None
        if token_fingerprint != self._token_fingerprint():
            raise CoreAccessDenied(401)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("invalid Core response")
        return value

    @property
    def auth_api_url(self) -> str:
        return self._auth_api_url

    def has_credentials(self) -> bool:
        """Return True iff the client is configured AND not muted.

        From a caller's perspective this means "the next get_*() call will
        actually hit the cloud" - a muted client returns False here so
        callers (e.g. server.py's _get_pairing_ice_servers_async) cleanly
        fall back to local-only iceServers without making the call.
        """
        return (
            bool(self._account_api_url)
            and bool(self._pb_token)
            and bool(self._auth_api_url)
            and not self._is_muted_for_current_token()
        )

    def _is_muted_for_current_token(self) -> bool:
        """True when the current pb_token has already been rejected once.

        Until update_credentials() supplies a different token, every cloud
        call short-circuits to a synthetic empty manifest with no log
        output. This keeps the SSE-reconnect path quiet on servers that
        only have a server-relay token (not a PB user token).
        """
        if not self._muted_token_hash:
            return False
        return self._token_fingerprint() == self._muted_token_hash

    def _token_fingerprint(self) -> str:
        # We never log the token, so a non-cryptographic identifier is fine.
        # Hash so we don't accidentally surface the token via repr() / debug.
        if not self._pb_token:
            return ""
        import hashlib
        return hashlib.sha256(self._pb_token.encode("utf-8")).hexdigest()[:16]

    def update_credentials(
        self,
        *,
        account_api_url: Optional[str] = None,
        pb_token: Optional[str] = None,
        auth_api_url: Optional[str] = None,
    ) -> None:
        """Update creds at runtime (sign-in/out, re-link, etc.). Drops caches.

        If `pb_token` is supplied AND it differs from the previously-muted
        token, the mute is cleared so the next cloud call retries fresh.
        Passing the same token in (e.g. on every SSE reconnect because the
        caller pulls it out of unchanging config) keeps the mute in place
        and avoids retrying a token we already know fails.
        """
        old_account_api_url = self._account_api_url
        old_auth_api_url = self._auth_api_url
        old_pb_token = self._pb_token

        next_account_api_url = old_account_api_url
        next_auth_api_url = old_auth_api_url
        next_pb_token = old_pb_token

        if account_api_url is not None:
            next_account_api_url = (account_api_url or "").rstrip("/")
            if not auth_api_url:
                # Re-derive auth URL when account changes and no explicit override.
                env_override = (os.getenv("AUTOYOU_AUTH_API_URL") or "").strip().rstrip("/")
                next_auth_api_url = env_override or _derive_auth_url(next_account_api_url)
        if auth_api_url is not None:
            next_auth_api_url = (auth_api_url or "").strip().rstrip("/")
        if pb_token is not None:
            next_pb_token = (pb_token or "").strip()
            # If the caller handed in a genuinely different token, give the
            # cloud another shot. Otherwise leave the mute alone so we keep
            # short-circuiting until something changes.
            if next_pb_token and self._muted_token_hash:
                next_fingerprint = hashlib.sha256(next_pb_token.encode("utf-8")).hexdigest()[:16]
                if next_fingerprint != self._muted_token_hash:
                    self._muted_token_hash = ""

        unchanged = (
            next_account_api_url == old_account_api_url
            and next_auth_api_url == old_auth_api_url
            and next_pb_token == old_pb_token
        )
        self._account_api_url = next_account_api_url
        self._auth_api_url = next_auth_api_url
        self._pb_token = next_pb_token
        if unchanged:
            return

        # Reconfiguration invalidates derived credentials and manifests. A
        # repeated update from the SSE reconnect path returned above and keeps
        # the existing cache.
        self.invalidate_sync()

    def invalidate_sync(self) -> None:
        """Drop all caches without taking the lock - safe to call from sync code.

        Does NOT clear the mute flag. SSE-driven invalidations (the
        entitlement_changed event handler) rely on the next call going to
        the cloud, but the cloud is going to keep rejecting the same dead
        token until the operator supplies a different one - keep mute.
        """
        self._jwt = None
        self._manifest = None
        self._manifest_fetched_at = 0.0
        self._turn_creds = None
        self._stun_payload = None
        self._diagnostics_heartbeat_supported = None
        if self._cached_client is not None:
            aclose = getattr(self._cached_client, "aclose", None)
            if aclose is not None:
                try:
                    import asyncio
                    loop = asyncio.get_running_loop()
                    if loop.is_running():
                        loop.create_task(aclose())
                except Exception:
                    pass
            self._cached_client = None

    async def invalidate(self) -> None:
        async with self._lock:
            self.invalidate_sync()

    def _mute_current_token(self, *, reason: str = "", hint: str = "") -> None:
        """Stop trying the current token until update_credentials() supplies
        a different one.

        Operator-facing hints (the original reason for the 401, what the
        operator should change) belong in docs, NOT in runtime logs that
        end up in users' transcripts and shared error reports. A single
        terse `debug` line records the fingerprint for postmortem; the
        public-facing surface stays silent.
        """
        if not self._pb_token:
            return
        fingerprint = self._token_fingerprint()
        if fingerprint and fingerprint == self._muted_token_hash:
            return  # Already muted - silently no-op.
        self._muted_token_hash = fingerprint
        # `reason` and `hint` are intentionally not formatted into the user-
        # visible log - they are accepted for API back-compat with callers.
        del reason, hint
        LOGGER.debug("cloud_entitlements: muted (fingerprint=%s)", fingerprint)

    # ── JWT minting ──────────────────────────────────────────────────────

    async def _mint_jwt(self) -> Optional[str]:
        if not self.has_credentials() or self._is_muted_for_current_token():
            return None
        url = f"{self._auth_api_url}/v1/token"
        try:
            client = self._get_http_client()
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {self._pb_token}"},
            )
        except Exception as exc:
            LOGGER.warning("cloud_entitlements: JWT mint network error: %s", exc)
            return None

        if resp.status_code == 401:
            self._mute_current_token(
                reason="rejected by JWT issuer",
                hint=(
                    "the configured token isn't a PocketBase user token - most "
                    "commonly because the server is using its server-relay "
                    "token from /v1/server/register instead of a user "
                    "token. Pair the server to a user account to enable "
                    "per-tier entitlements."
                ),
            )
            return None
        if resp.status_code >= 400:
            LOGGER.warning(
                "cloud_entitlements: JWT mint failed with HTTP %d: %s",
                resp.status_code, resp.text[:300],
            )
            return None
        try:
            payload = resp.json()
        except Exception as exc:
            LOGGER.warning("cloud_entitlements: JWT mint returned non-JSON: %s", exc)
            return None
        token = str(payload.get("token") or "")
        ttl = int(payload.get("expires_in") or 0)
        if not token or ttl <= 0:
            return None
        self._jwt = (token, time.time() + max(60, ttl) - _JWT_REFRESH_SAFETY_SECONDS)
        return token

    async def _get_jwt(self, *, force_refresh: bool = False) -> Optional[str]:
        if not force_refresh and self._jwt:
            token, exp = self._jwt
            if time.time() < exp:
                return token
        return await self._mint_jwt()

    async def get_worker_jwt(self) -> Optional[str]:
        """Public: return a current worker JWT (mint if cache is empty/expired).

        Use this only when you need the raw token (e.g., to pass to a
        sub-process via env var). For HTTP calls to autoyou-stun /
        autoyou-turn / autoyou-tunnelmole, prefer the typed helpers
        (get_cloud_stun_iceservers, get_cloud_turn_iceservers,
        get_tunnelmole_provision) - they handle 401-retry and TTL bookkeeping.
        """
        return await self._get_jwt()

    # ── Service manifest ─────────────────────────────────────────────────

    async def get_services_manifest(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """Fetch the per-user service manifest (`auth.autoyou.me/v1/services`).

        Returns a manifest with entitlements + service URLs, or an empty
        manifest sentinel when the cloud is unreachable or the token isn't
        accepted. Callers should check `manifest.get('tier')` /
        `manifest.get('entitlements')`.
        """
        async with self._lock:
            now = time.time()
            cached = self._manifest
            if (
                not force_refresh
                and cached is not None
                and (now - self._manifest_fetched_at) < self._manifest_ttl
            ):
                return cached

            if not self.has_credentials() or self._is_muted_for_current_token():
                self._manifest = _empty_manifest()
                self._manifest_fetched_at = now
                return self._manifest

            manifest = await self._fetch_services_manifest_locked()
            self._manifest = manifest if manifest is not None else _empty_manifest()
            self._manifest_fetched_at = now
            return self._manifest

    async def _fetch_services_manifest_locked(self) -> Optional[dict[str, Any]]:
        """Caller holds self._lock. Returns parsed manifest dict, or None."""
        # The /v1/services endpoint takes the PB token, not the worker JWT
        # (it's the manifest of where the worker JWT can be used). Fetching
        # it directly avoids one round-trip.
        url = f"{self._auth_api_url}/v1/services"
        headers = {"Authorization": f"Bearer {self._pb_token}"}
        try:
            client = self._get_http_client()
            resp = await client.get(url, headers=headers)
        except Exception as exc:
            LOGGER.warning("cloud_entitlements: services fetch network error: %s", exc)
            return None
        if resp.status_code == 401:
            self._mute_current_token(
                reason="rejected for /v1/services",
                hint=(
                    "the configured token isn't a PocketBase user token. "
                    "Servers running with only a server-relay token (from "
                    "/v1/server/register) need to be paired to a user "
                    "account before paid-tier features can activate."
                ),
            )
            return None
        if resp.status_code >= 400:
            LOGGER.warning(
                "cloud_entitlements: services fetch HTTP %d: %s",
                resp.status_code, resp.text[:300],
            )
            return None
        try:
            return resp.json()
        except Exception as exc:
            LOGGER.warning("cloud_entitlements: services fetch non-JSON: %s", exc)
            return None

    async def get_tier(self) -> str:
        manifest = await self.get_services_manifest()
        return str(manifest.get("tier") or "")

    async def get_entitlements(self) -> tuple[str, ...]:
        manifest = await self.get_services_manifest()
        ents = manifest.get("entitlements") or []
        if not isinstance(ents, list):
            return ()
        return tuple(str(item) for item in ents)

    async def has_entitlement(self, key: str) -> bool:
        return key in await self.get_entitlements()

    # ── Cloud STUN config ────────────────────────────────────────────────

    async def get_cloud_stun_iceservers(self) -> list[dict[str, Any]]:
        """Fetch curated helper list when user holds the `stun` entitlement.

        Returns the iceServers array or [] if not entitled / failed.
        """
        manifest = await self.get_services_manifest()
        services = manifest.get("services") or {}
        stun = services.get("stun") if isinstance(services, dict) else None
        if not isinstance(stun, dict):
            return []
        config_url = str(stun.get("config_url") or "").strip()
        if not config_url:
            return []

        now = time.time()
        cached = self._stun_payload
        if cached is not None:
            payload, fetched_at = cached
            expiry = float(payload.get("expires_at") or 0)
            if expiry > now or (expiry == 0 and (now - fetched_at) < self._manifest_ttl):
                return list(payload.get("iceServers") or [])

        async with self._lock:
            cached = self._stun_payload
            if cached is not None:
                payload, fetched_at = cached
                expiry = float(payload.get("expires_at") or 0)
                if expiry > now or (expiry == 0 and (now - fetched_at) < self._manifest_ttl):
                    return list(payload.get("iceServers") or [])

            payload = await self._fetch_with_jwt_locked(config_url, method="GET")
            if not payload:
                return []
            self._stun_payload = (payload, now)
            servers = payload.get("iceServers") or []
            return list(servers) if isinstance(servers, list) else []

    # ── Cloud TURN credentials ───────────────────────────────────────────

    async def get_cloud_turn_iceservers(self) -> list[dict[str, Any]]:
        """Mint or reuse short-lived TURN creds for the `turn` entitlement.

        Returns the iceServers array (with username/credential) or [] if not
        entitled, over quota (HTTP 402), or fetch failed.
        """
        manifest = await self.get_services_manifest()
        services = manifest.get("services") or {}
        turn = services.get("turn") if isinstance(services, dict) else None
        if not isinstance(turn, dict):
            return []
        creds_url = str(turn.get("creds_url") or "").strip()
        if not creds_url:
            return []

        now = time.time()
        cached = self._turn_creds
        if cached is not None:
            payload, exp = cached
            if exp > (now + 60):
                return list(payload.get("iceServers") or [])

        async with self._lock:
            cached = self._turn_creds
            if cached is not None:
                payload, exp = cached
                if exp > (now + 60):
                    return list(payload.get("iceServers") or [])

            payload = await self._fetch_with_jwt_locked(creds_url, method="POST")
            if not payload:
                return []
            ttl = int(payload.get("ttl") or _DEFAULT_FALLBACK_CRED_TTL)
            self._turn_creds = (payload, now + max(60, ttl))
            servers = payload.get("iceServers") or []
            return list(servers) if isinstance(servers, list) else []

    # ── TURN roster (diagnostic, autoyou-distributed) ────────────────────

    async def get_turn_roster(self) -> list[dict[str, Any]]:
        """Return the deterministic-ordered roster of shared relay TURN providers.

        Driven by the same geo selector cred-issuer uses to mint creds, but
        without minting. Used by the dashboard / Settings UI ("which relays
        am I likely to be sent to?") and as a refresh trigger on the
        `roster_changed` SSE event. Returns [] when the user lacks `turn`
        or the cloud is unreachable.
        """
        manifest = await self.get_services_manifest()
        services = manifest.get("services") or {}
        turn = services.get("turn") if isinstance(services, dict) else None
        if not isinstance(turn, dict):
            return []
        roster_url = str(turn.get("roster_url") or "").strip()
        if not roster_url:
            return []
        async with self._lock:
            payload = await self._fetch_with_jwt_locked(roster_url, method="GET")
        if not payload or not isinstance(payload, dict):
            return []
        items = payload.get("items") or []
        return list(items) if isinstance(items, list) else []

    async def is_provider(self) -> bool:
        """True iff the user has been admin-approved as a TURN provider."""
        manifest = await self.get_services_manifest()
        services = manifest.get("services") or {}
        provider = services.get("provider") if isinstance(services, dict) else None
        if not isinstance(provider, dict):
            return False
        return bool(provider.get("is_provider"))

    async def get_provider_urls(self) -> dict[str, str]:
        """Return URLs the contribution UI calls to enroll a new provider.

        Empty dict when the cloud is unreachable / the user is signed out.
        """
        manifest = await self.get_services_manifest()
        services = manifest.get("services") or {}
        provider = services.get("provider") if isinstance(services, dict) else None
        if not isinstance(provider, dict):
            return {}
        return {
            "enroll_init_url": str(provider.get("enroll_init_url") or ""),
            "list_mine_url": str(provider.get("list_mine_url") or ""),
        }

    async def get_turn_usage(self) -> dict[str, Any]:
        """Fetch the user's monthly TURN usage + quota (community vs own scope).

        Returns {} when not entitled / cloud unreachable. Worker-JWT endpoint.
        """
        manifest = await self.get_services_manifest()
        services = manifest.get("services") or {}
        turn = services.get("turn") if isinstance(services, dict) else None
        if not isinstance(turn, dict):
            return {}
        usage_url = str(turn.get("usage_url") or "").strip()
        if not usage_url:
            return {}
        async with self._lock:
            payload = await self._fetch_with_jwt_locked(usage_url, method="GET")
        return payload or {}

    async def get_my_providers(self) -> dict[str, Any]:
        """Fetch the signed-in user's contributed TURN providers + earnings.

        Owner-scoped, so it uses the PB user token (not a worker JWT), mirroring
        get_services_manifest. Returns {items: [...], earnings: {...}} or {}.
        """
        urls = await self.get_provider_urls()
        list_url = str(urls.get("list_mine_url") or "").strip()
        if not list_url or not self.has_credentials() or self._is_muted_for_current_token():
            return {}
        headers = {"Authorization": f"Bearer {self._pb_token}"}
        # from __debug_provenance_e__ import pay
        try:
            client = self._get_http_client()
            resp = await client.get(list_url, headers=headers)
        except Exception as exc:
            LOGGER.warning("cloud_entitlements: providers/mine network error: %s", exc)
            return {}
        if resp.status_code == 401:
            self._mute_current_token(reason="rejected for providers/mine")
            return {}
        if resp.status_code >= 400:
            return {}
        try:
            return resp.json() or {}
        except Exception:
            return {}

    async def get_connection_status(self) -> dict[str, Any]:
        """One-shot snapshot for the desktop 'Connection & Relay' UI.

        Bundles tier + relay entitlements + the shared relay roster the user would
        be routed to + TURN usage + their contributed providers/earnings + the
        enroll/dashboard deep-links. Every field degrades to empty/false on any
        failure path; never raises.
        """
        manifest = await self.get_services_manifest()
        ents = await self.get_entitlements()
        services = manifest.get("services") or {}
        turn = services.get("turn") if isinstance(services, dict) else {}
        turn = turn if isinstance(turn, dict) else {}
        provider_urls = await self.get_provider_urls()
        out: dict[str, Any] = {
            "signed_in": self.has_credentials(),
            "tier": str(manifest.get("tier") or ""),
            "entitlements": list(ents),
            "has_stun": "stun" in ents,
            "has_community_turn": bool(turn.get("community_paid")) or ("turn" in ents),
            "has_own_turn": "turn" in ents,
            "is_provider": False,
            "enroll_init_url": provider_urls.get("enroll_init_url", ""),
            "dashboard_url": "",
            "roster": [],
            "usage": {},
            "providers": {},
        }
        ice = services.get("ice_override") if isinstance(services, dict) else None
        if isinstance(ice, dict):
            out["dashboard_url"] = str(ice.get("dashboard_url") or "")
        try:
            out["is_provider"] = await self.is_provider()
        except Exception:
            pass
        try:
            out["roster"] = await self.get_turn_roster()
        except Exception:
            pass
        try:
            out["usage"] = await self.get_turn_usage()
        except Exception:
            pass
        try:
            out["providers"] = await self.get_my_providers()
        except Exception:
            pass
        return out

    # ── Tunnelmole provisioning ──────────────────────────────────────────

    async def get_tunnelmole_provision(self) -> Optional[dict[str, Any]]:
        """Provision a paid Tunnelmole API key via the cloud apikey-bridge.

        Returns the provision payload (api_key, ws_endpoint, public_url, ...)
        or None when the user lacks the `tm` entitlement / the call failed.
        """
        manifest = await self.get_services_manifest()
        services = manifest.get("services") or {}
        tm = services.get("tunnelmole") if isinstance(services, dict) else None
        if not isinstance(tm, dict):
            return None
        provision_url = str(tm.get("paid_provision_url") or "").strip()
        if not provision_url:
            return None
        async with self._lock:
            payload = await self._fetch_with_jwt_locked(provision_url, method="POST")
            return payload or None

    # ── Diagnostics heartbeat (Phase 3) ──────────────────────────────────

    async def send_diagnostics_heartbeat(
        self,
        *,
        role: str,
        nat_class: str,
        ip_prefix: Optional[str] = None,
    ) -> bool:
        """Push a single privacy-minimal diagnostics heartbeat.

        Privacy contract (enforced by the cloud schema, not by this client):
          - No raw IP. The cloud truncates to /24 (IPv4) or /48 (IPv6) before
            persisting, regardless of what we send.
          - One row per (user, role) - overwriting on every call. No timeline.

        This method is event-driven: callers should invoke it once on
        bootstrap and again only when nat_class changes, NEVER periodically.

        Returns True on success (cloud accepted the heartbeat), False on
        any failure path. Failures are logged but never propagated since
        diagnostics are best-effort.
        """
        manifest = await self.get_services_manifest()
        services = manifest.get("services") or {}
        diag = services.get("diagnostics") if isinstance(services, dict) else None
        if isinstance(diag, dict):
            heartbeat_url = str(diag.get("heartbeat_url") or "").strip()
        else:
            # Older cloud deploys don't expose `diagnostics` in /v1/services
            # yet; fall back to the conventional path so this rolls out
            # without requiring a coordinated cloud upgrade.
            heartbeat_url = f"{self._auth_api_url.rstrip('/')}/v1/diagnostics/heartbeat"

        if (
            not heartbeat_url
            or not self.has_credentials()
            or self._is_muted_for_current_token()
            or self._diagnostics_heartbeat_supported is False
        ):
            return False

        body: dict[str, Any] = {"role": role, "nat_class": nat_class}
        if ip_prefix:
            body["ip_prefix"] = ip_prefix

        # The heartbeat endpoint takes the PB token (same as /v1/services),
        # not the worker JWT - the issuer service rather than a worker
        # accepts the call.
        try:
            client = self._get_http_client()
            resp = await client.post(
                heartbeat_url,
                headers={"Authorization": f"Bearer {self._pb_token}"},
                json=body,
            )
        except Exception as exc:
            LOGGER.warning("cloud_entitlements: diagnostics heartbeat network error: %s", exc)
            return False

        if resp.status_code == 401:
            self._mute_current_token(reason="rejected for diagnostics heartbeat")
            return False
        if resp.status_code == 404:
            self._diagnostics_heartbeat_supported = False
            LOGGER.info(
                "cloud_entitlements: diagnostics heartbeat unsupported at %s; suppressing future attempts for the current config",
                heartbeat_url,
            )
            return False
        if resp.status_code >= 400:
            LOGGER.warning(
                "cloud_entitlements: diagnostics heartbeat HTTP %d: %s",
                resp.status_code, resp.text[:200],
            )
            return False
        self._diagnostics_heartbeat_supported = True
        return True

    # ── Worker call helper (uses worker JWT, not PB token) ───────────────

    async def _fetch_with_jwt_locked(
        self,
        url: str,
        *,
        method: str = "GET",
        body: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Caller holds self._lock. Fetches `url` with a worker JWT, refreshes
        once on 401, returns parsed JSON or None.
        """
        token = await self._get_jwt()
        if not token:
            return None

        async def _do_call(jwt_token: str) -> tuple[int, dict[str, Any]]:
            try:
                client = self._get_http_client()
                headers = {"Authorization": f"Bearer {jwt_token}"}
                if method.upper() == "POST":
                    resp = await client.post(url, headers=headers, json=body or {})
                else:
                    resp = await client.get(url, headers=headers)
            except Exception as exc:
                LOGGER.warning("cloud_entitlements: %s %s network error: %s", method, url, exc)
                return -1, {}
            try:
                parsed = resp.json()
            except Exception:
                parsed = {}
            return resp.status_code, parsed

        status, payload = await _do_call(token)
        if status == 401:
            # JWT expired between cache hit and call - refresh once and retry.
            token = await self._get_jwt(force_refresh=True)
            if not token:
                return None
            status, payload = await _do_call(token)
        if status == 402:
            LOGGER.info("cloud_entitlements: %s %s returned 402 (over quota)", method, url)
            return None
        if status >= 400 or status < 0:
            LOGGER.warning("cloud_entitlements: %s %s HTTP %d", method, url, status)
            return None
        return payload

    # ── Public ICE merge helper ──────────────────────────────────────────

    async def get_iceservers_to_advertise(
        self,
        base: list[dict[str, Any]],
        *,
        merge_with: Optional[Callable[[list[dict[str, Any]], list[dict[str, Any]]], list[dict[str, Any]]]] = None,
    ) -> list[dict[str, Any]]:
        """Merge `base` (admin-configured iceServers) with cloud helpers.

        The cloud helper endpoints serve EVERY signed-in user:
        - Free tier → they return only the user's self-pushed
          `user_ice_override` entries (metered.ca / Twilio / own coturn).
        - `stun` entitlement → curated helper list is also included.
        - `turn` entitlement → shared relay roster + operator relay too.

        So we always call both endpoints - the cloud decides what to return
        based on tier. A free user with no override just gets [] back.

        - On any failure path → returns whatever subset succeeded; never throws.

        `merge_with` is the dedupe function (typically
        `shared.admin_onboarding.dedupe_ice_servers` curried with a list-cat).
        Defaults to a simple list concat with naive dedupe by url-string.
        """
        cloud_extra: list[dict[str, Any]] = []
        try:
            # Always attempt both - the cloud gates content by tier and by
            # the presence of a user_ice_override, not the client.
            cloud_extra.extend(await self.get_cloud_stun_iceservers())
            cloud_extra.extend(await self.get_cloud_turn_iceservers())
        except Exception as exc:
            # Defensive: never let a cloud hiccup break /auth.
            LOGGER.warning("cloud_entitlements: merge failed (returning base only): %s", exc)
            return list(base or [])

        if not cloud_extra:
            return list(base or [])

        if merge_with is not None:
            try:
                return merge_with(list(base or []), cloud_extra)
            except Exception as exc:
                LOGGER.warning("cloud_entitlements: merge_with raised: %s", exc)

        # Naive dedupe fallback: drop duplicates keyed by sorted url-string.
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for entry in (list(base or []) + cloud_extra):
            urls = entry.get("urls") if isinstance(entry, dict) else None
            if isinstance(urls, str):
                key = urls
            elif isinstance(urls, list):
                key = "|".join(sorted(str(u) for u in urls))
            else:
                key = repr(entry)
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
        return merged
