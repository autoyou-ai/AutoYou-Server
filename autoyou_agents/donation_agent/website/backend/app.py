# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-ef3840d420f78ed6d0b1209b

"""Donation Agent UI backend."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import os
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import Request
from fastapi.responses import Response
from shared.runtime_module_loader import import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module

from autoyou_agents.donation_agent.agent import (
    describe_donation_flow,
    describe_donation_guardrails,
    get_donation_agent_status,
    prepare_donation_handoff,
)
from autoyou_agents.donation_agent.donation_links import (
    get_public_donation_links,
    sanitize_crypto_address,
)

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-ef3840d420f78ed6d0b1209b"


try:
    import qrcode
except ImportError:  # pragma: no cover - dependency is declared in requirements/base.txt
    qrcode = None

_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"
_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
create_agent_chat_app = _smc.create_agent_chat_app
_describe_chat_auth_state = _smc._describe_chat_auth_state
_json_response = _smc._json_response

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_AGENT_NAME = "donation_agent"
_LOCAL_AUTH_HANDOFF_CONTEXTS = {"account_dashboard", "client_context", "server_context"}
_LOCAL_AUTH_HANDOFF_CHANNELS = {"funding-dashboard"}
_PUBLIC_LEDGER_CACHE: dict[str, dict[str, Any]] = {}
_FUNDING_MANIFEST_CACHE: dict[str, dict[str, Any]] = {}


def _is_local_development_host(hostname: str | None) -> bool:
    host = str(hostname or "").strip().lower().strip("[]")
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")


def _safe_base_url(value: str, default: str) -> str:
    text = str(value or default).strip().rstrip("/")
    if not text or any(ch.isspace() for ch in text):
        return default
    try:
        parsed = urlparse(text)
    except ValueError:
        return default
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return default
    try:
        hostname = parsed.hostname
    except ValueError:
        return default
    if parsed.scheme == "http" and not _is_local_development_host(hostname):
        return default
    return text


def _account_url() -> str:
    return _safe_base_url(os.getenv("ACCOUNT_PUBLIC_URL", ""), "https://app.autoyou.me")


def _website_url() -> str:
    return _safe_base_url(os.getenv("WEBSITE_PUBLIC_URL", ""), "https://www.autoyou.me")


def _local_auth_required(auth: dict) -> bool:
    return bool(auth.get("required")) and not bool(auth.get("authenticated"))


def _public_ledger_cache_ttl_seconds() -> float:
    raw_value = os.getenv("AUTOYOU_DONATION_AGENT_LEDGER_CACHE_SECONDS", "30").strip()
    try:
        value = float(raw_value)
    except ValueError:
        value = 30.0
    return max(0.0, min(value, 300.0))


def _funding_manifest_cache_ttl_seconds() -> float:
    default_value = os.getenv("AUTOYOU_DONATION_AGENT_LEDGER_CACHE_SECONDS", "30")
    raw_value = os.getenv("AUTOYOU_DONATION_AGENT_MANIFEST_CACHE_SECONDS", default_value).strip()
    try:
        value = float(raw_value)
    except ValueError:
        value = 30.0
    return max(0.0, min(value, 300.0))


async def _fetch_public_ledger(account_url: str, *, etag: str = "") -> tuple[int, str, dict[str, Any] | None]:
    public_ledger_url = f"{account_url}/v1/funding/public-ledger"
    headers = {"Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    async with httpx.AsyncClient(timeout=5.0, headers=headers) as client:
        response = await client.get(public_ledger_url)
    if response.status_code == 304:
        return 304, response.headers.get("ETag", "") or etag, None
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Public support records did not return a JSON object.")
    return response.status_code, response.headers.get("ETag", ""), payload


async def _fetch_funding_manifest(account_url: str, *, etag: str = "") -> tuple[int, str, dict[str, Any] | None]:
    funding_manifest_url = f"{account_url}/v1/funding/manifest"
    headers = {"Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    async with httpx.AsyncClient(timeout=5.0, headers=headers) as client:
        response = await client.get(funding_manifest_url)
    if response.status_code == 304:
        return 304, response.headers.get("ETag", "") or etag, None
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Support route manifest did not return a JSON object.")
    return response.status_code, response.headers.get("ETag", ""), payload


async def _get_public_ledger_snapshot(account_url: str) -> dict[str, Any]:
    public_ledger_url = f"{account_url}/v1/funding/public-ledger"
    ttl_seconds = _public_ledger_cache_ttl_seconds()
    now = time.monotonic()
    cached = _PUBLIC_LEDGER_CACHE.get(public_ledger_url)
    if cached and ttl_seconds > 0 and float(cached.get("expires_at") or 0) > now:
        return {
            "ledger": cached.get("ledger") or {},
            "cache": {
                "status": "hit",
                "etag": cached.get("etag") or "",
                "ttl_seconds": ttl_seconds,
            },
        }

    status_code, etag, ledger = await _fetch_public_ledger(
        account_url,
        etag=str(cached.get("etag") or "") if cached else "",
    )
    if status_code == 304:
        if not cached:
            raise ValueError("Public support records returned 304 without a cached snapshot.")
        cached["expires_at"] = now + ttl_seconds
        if etag:
            cached["etag"] = etag
        return {
            "ledger": cached.get("ledger") or {},
            "cache": {
                "status": "not_modified",
                "etag": cached.get("etag") or "",
                "ttl_seconds": ttl_seconds,
            },
        }

    if ledger is None:
        raise ValueError("Public support records response did not include a snapshot.")
    _PUBLIC_LEDGER_CACHE[public_ledger_url] = {
        "ledger": ledger,
        "etag": etag,
        "expires_at": now + ttl_seconds,
        "fetched_at": time.time(),
    }
    # from __debug_provenance_i__ import or
    return {
        "ledger": ledger,
        "cache": {
            "status": "fresh",
            "etag": etag,
            "ttl_seconds": ttl_seconds,
        },
    }


async def _get_funding_manifest_snapshot(account_url: str) -> dict[str, Any]:
    funding_manifest_url = f"{account_url}/v1/funding/manifest"
    ttl_seconds = _funding_manifest_cache_ttl_seconds()
    now = time.monotonic()
    cached = _FUNDING_MANIFEST_CACHE.get(funding_manifest_url)
    if cached and ttl_seconds > 0 and float(cached.get("expires_at") or 0) > now:
        return {
            "manifest": cached.get("manifest") or {},
            "cache": {
                "status": "hit",
                "etag": cached.get("etag") or "",
                "ttl_seconds": ttl_seconds,
            },
        }

    status_code, etag, manifest = await _fetch_funding_manifest(
        account_url,
        etag=str(cached.get("etag") or "") if cached else "",
    )
    if status_code == 304:
        if not cached:
            raise ValueError("Support route manifest returned 304 without a cached snapshot.")
        cached["expires_at"] = now + ttl_seconds
        if etag:
            cached["etag"] = etag
        return {
            "manifest": cached.get("manifest") or {},
            "cache": {
                "status": "not_modified",
                "etag": cached.get("etag") or "",
                "ttl_seconds": ttl_seconds,
            },
        }

    if manifest is None:
        raise ValueError("Support route manifest response did not include a snapshot.")
    _FUNDING_MANIFEST_CACHE[funding_manifest_url] = {
        "manifest": manifest,
        "etag": etag,
        "expires_at": now + ttl_seconds,
        "fetched_at": time.time(),
    }
    return {
        "manifest": manifest,
        "cache": {
            "status": "fresh",
            "etag": etag,
            "ttl_seconds": ttl_seconds,
        },
    }


def _handoff_requires_local_auth(handoff: dict) -> bool:
    context = handoff.get("context") if isinstance(handoff.get("context"), dict) else {}
    channel = handoff.get("channel") if isinstance(handoff.get("channel"), dict) else {}
    context_id = str(context.get("id") or "").strip()
    channel_id = str(channel.get("id") or "").strip()
    return context_id in _LOCAL_AUTH_HANDOFF_CONTEXTS or channel_id in _LOCAL_AUTH_HANDOFF_CHANNELS


def _merged_public_providers(links: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge config-file providers with active env-configured Funding OS channels.

    The donations config file wins on id collisions; only channels that are
    active with a sanitized URL are added, so unconfigured routes never render.
    """
    providers = [dict(item) for item in links.get("providers") or []]
    seen = {str(item.get("id") or "") for item in providers}
    try:
        channels = get_donation_agent_status().get("channels") or []
    except Exception:
        channels = []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        # Hosted crypto checkouts are rendered in the dedicated crypto section,
        # where the exact asset and network are shown alongside the provider.
        if str(channel.get("type") or "").strip().lower() == "crypto":
            continue
        channel_id = str(channel.get("id") or "").strip()
        url = str(channel.get("url") or "").strip()
        if not channel_id or channel_id in seen or channel.get("status") != "active" or not url:
            continue
        providers.append(
            {
                "id": channel_id,
                "label": str(channel.get("label") or channel_id)[:48],
                "url": url,
                "description": str(channel.get("description") or "")[:140],
            }
        )
        seen.add(channel_id)
    return providers


def _public_hosted_crypto_routes() -> list[dict[str, str]]:
    """Return only active, public HTTPS checkout routes with exact metadata."""
    try:
        routes = get_donation_agent_status().get("crypto_donation_routes") or []
    except Exception:
        return []

    public_routes: list[dict[str, str]] = []
    for route in routes:
        if not isinstance(route, dict) or route.get("status") != "active":
            continue
        public_url = str(route.get("public_url") or "").strip()
        try:
            parsed = urlparse(public_url)
            hostname = parsed.hostname
        except ValueError:
            continue
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            continue
        asset_symbol = str(route.get("asset_symbol") or "").strip()
        network = str(route.get("network") or "").strip()
        if not asset_symbol or not network:
            continue
        # Whitelist public display fields. In particular, no raw registry
        # payload, addresses, payment IDs, or credentials cross this boundary.
        public_routes.append(
            {
                "id": str(route.get("id") or "")[:80],
                "label": str(route.get("label") or "Hosted crypto checkout")[:100],
                "provider": str(route.get("provider") or "Hosted provider")[:80],
                "asset_symbol": asset_symbol[:16],
                "network": network[:64],
                "public_url": public_url,
                "status": "active",
            }
        )
    return public_routes


def _extra_routes(app, agent_name: str) -> None:
    @app.get("/api/donation-links")
    async def api_donation_links():
        """Public, cache-friendly payload for the donation page. No local auth."""
        links = get_public_donation_links()
        hosted_crypto_routes = _public_hosted_crypto_routes()
        links = {
            **links,
            "crypto_configured": bool(links.get("crypto_configured") or hosted_crypto_routes),
            "hosted_crypto_routes": hosted_crypto_routes,
        }
        response = _json_response(
            {
                "success": True,
                "links": {**links, "providers": _merged_public_providers(links)},
            }
        )
        response.headers["Cache-Control"] = "public, max-age=120"
        return response

    @app.get("/api/crypto-qr")
    async def api_crypto_qr(address: str = ""):
        """Render a public crypto address locally so QR previews do not depend on a third party."""
        safe_address = sanitize_crypto_address(address)
        if not safe_address:
            return Response(status_code=400)
        if qrcode is None:
            return Response(status_code=503)
        try:
            qr = qrcode.QRCode(
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=2,
            )
            qr.add_data(safe_address)
            qr.make(fit=True)
            image = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            return Response(
                content=buffer.getvalue(),
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=300"},
            )
        except Exception:
            return Response(status_code=503)

    @app.get("/api/status")
    async def api_status(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        account_url = _account_url()
        website_url = _website_url()
        return _json_response(
            {
                "success": True,
                "auth": auth,
                "status": get_donation_agent_status(),
                "account": {
                    "base_url": account_url,
                    "dashboard_url": f"{account_url}/dashboard?section=funding",
                    "oauth_url": f"{account_url}/login?next=%2Fdashboard%3Fsection%3Dfunding",
                    "funding_manifest_url": f"{account_url}/v1/funding/manifest",
                    "public_ledger_url": f"{account_url}/v1/funding/public-ledger",
                },
                "website": {
                    "donate_url": f"{website_url}/donate/",
                },
                "pc": {
                    "connected": True,
                    "reason": "This web app is served by the local AutoYou agent runtime and can use agent website OTP when configured.",
                },
            }
        )

    @app.get("/api/plan")
    async def api_plan(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        return _json_response(
            {
                "success": True,
                "auth": auth,
                "flow": describe_donation_flow(),
                "guardrails": describe_donation_guardrails(),
            }
        )

    @app.get("/api/public-ledger")
    async def api_public_ledger(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        account_url = _account_url()
        public_ledger_url = f"{account_url}/v1/funding/public-ledger"
        funding_manifest_url = f"{account_url}/v1/funding/manifest"
        try:
            snapshot = await _get_public_ledger_snapshot(account_url)
        except Exception:
            return _json_response(
                {
                    "success": False,
                    "auth": auth,
                    "public_ledger_url": public_ledger_url,
                    "funding_manifest_url": funding_manifest_url,
                    "ledger": {},
                    "public_url_policy": {},
                    "request_contracts": {},
                    "cache": {"status": "unavailable"},
                    "manifest_cache": {"status": "unavailable"},
                    "error": "Public support records are unavailable.",
                }
            )
        manifest_snapshot = {"manifest": {}, "cache": {"status": "unavailable"}}
        try:
            manifest_snapshot = await _get_funding_manifest_snapshot(account_url)
        except Exception:
            pass
        manifest = manifest_snapshot.get("manifest") or {}
        return _json_response(
            {
                "success": True,
                "auth": auth,
                "public_ledger_url": public_ledger_url,
                "funding_manifest_url": funding_manifest_url,
                "ledger": snapshot["ledger"],
                "public_url_policy": manifest.get("public_url_policy") or {},
                "request_contracts": manifest.get("request_contracts") or {},
                "cache": snapshot["cache"],
                "manifest_cache": manifest_snapshot.get("cache") or {"status": "unavailable"},
            }
        )

    @app.get("/api/handoff")
    async def api_handoff(
        request: Request,
        channel: str = "public-donate",
        context: str = "provider_checkout",
        permissionAcknowledged: bool = False,
    ):
        auth = _describe_chat_auth_state(request, agent_name)
        handoff = prepare_donation_handoff(
            channel_id=channel,
            context=context,
            permission_acknowledged=permissionAcknowledged,
        )
        if _handoff_requires_local_auth(handoff) and _local_auth_required(auth):
            return _json_response(
                {
                    "success": False,
                    "error": "Local one-time-code session required for connected account or Support tab handoffs.",
                    "auth": auth,
                    "handoff": {
                        "status": "local_auth_required",
                        "requested_channel": channel,
                        "requested_context": context,
                    },
                },
                status_code=401,
            )
        return _json_response(
            {
                "success": True,
                "auth": auth,
                "handoff": handoff,
            }
        )


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Donation",
    description="Guide supporters to official AutoYou donation routes.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
