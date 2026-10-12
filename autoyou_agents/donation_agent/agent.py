# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-to-a690734bb760546e4a03a4b7

"""Donation Agent implementation."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import json
import os
from typing import Any, Dict, List
from urllib.parse import urlparse

from google.adk.agents import Agent

from autoyou_agents.donation_agent.prompt import (
    AGENT_DESCRIPTION,
    AGENT_INSTRUCTION,
    AGENT_NAME,
)
from autoyou_agents.shared_tools.datetime_tool import (
    get_current_datetime,
    inject_realtime_datetime_into_request,
)

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-to-a690734bb760546e4a03a4b7"


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


def _safe_url(value: str) -> str:
    text = str(value or "").strip()
    # from __debug_provenance_d__ import to
    if not text or any(ch.isspace() for ch in text):
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
        return ""
    try:
        hostname = parsed.hostname
    except ValueError:
        return ""
    if parsed.scheme == "http" and not _is_local_development_host(hostname):
        return ""
    return text


def _donation_channel(channel_id: str, label: str, channel_type: str, url: str, note: str) -> dict[str, Any]:
    safe_url = _safe_url(url)
    return {
        "id": channel_id,
        "label": label,
        "type": channel_type,
        "status": "active" if safe_url else "setup_required",
        "url": safe_url,
        "note": note,
    }


def _voice_handoff_mode(channel: dict[str, Any] | None = None) -> dict[str, Any]:
    safe_url = _safe_url(str((channel or {}).get("url", "")))
    parsed = urlparse(safe_url) if safe_url else None
    return {
        "otp_supported": True,
        "safe_action": "share_official_link",
        "collect_payment_details": False,
        "local_otp_scope": "agent_ui_only_not_payment_execution",
        "handoff_url_can_be_read_aloud": bool(safe_url),
        "official_host": parsed.netloc if parsed else "",
        "payment_execution": "external_provider_checkout_only",
        "permission_prompt": "Confirm the supporter wants the official route before reading or opening it.",
        "read_aloud": [
            "This is an official AutoYou Funding OS donation route.",
            "Open or read only the returned handoff URL.",
            "Complete payment only on the external provider page or app.autoyou.me.",
        ],
        "never_collect": [
            "card numbers",
            "bank details",
            "wallet addresses",
            "seed phrases or private keys",
            "passwords or OTP codes",
            "email, account, or device identifiers",
        ],
    }


def _safe_text(value: Any, max_length: int = 120) -> str:
    return " ".join(str(value or "").strip().split())[:max_length]


def _json_env_list(name: str) -> list[dict[str, Any]]:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _safe_crypto_route_status(value: Any) -> str:
    status = str(value or "").strip().lower().replace("-", "_")
    if status in {"active", "setup_required", "disabled", "manual_review", "provider_review_required"}:
        return status
    return "manual_review"


def _crypto_route_has_private_fields(raw: dict[str, Any]) -> bool:
    private_fields = {
        "walletAddress",
        "wallet_address",
        "publicAddress",
        "public_address",
        "transactionHash",
        "transaction_hash",
        "paymentId",
        "payment_id",
        "customerId",
        "customer_id",
        "providerCustomerId",
        "provider_customer_id",
        "externalReference",
        "external_reference",
        "privateKey",
        "private_key",
        "seedPhrase",
        "seed_phrase",
        "recoveryPhrase",
        "recovery_phrase",
        "secret",
        "rawPayload",
        "raw_payload",
    }
    return any(raw.get(field) for field in private_fields)


def _crypto_donation_routes_from_env() -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for raw in _json_env_list("AUTOYOU_FUNDING_CRYPTO_DONATION_ROUTES_JSON"):
        if _crypto_route_has_private_fields(raw):
            continue
        public_url = _safe_url(raw.get("public_url") or raw.get("publicUrl") or raw.get("url") or "")
        if not public_url or public_url in seen_urls:
            continue
        seen_urls.add(public_url)
        asset_symbol = _safe_text(
            raw.get("asset_symbol") or raw.get("assetSymbol") or raw.get("token_symbol"),
            16,
        )
        network = _safe_text(raw.get("network"), 64)
        # Never publish a hosted checkout with an inferred asset or network.
        # A mistaken default could send supporters to the wrong payment rail.
        if not asset_symbol or not network:
            continue
        status = _safe_crypto_route_status(raw.get("status"))
        routes.append(
            {
                "id": _safe_text(raw.get("id"), 80) or f"crypto-route-{len(routes) + 1}",
                "label": _safe_text(raw.get("label"), 100) or "Hosted crypto donation",
                "provider": _safe_text(raw.get("provider"), 80) or "hosted_crypto",
                "type": "crypto",
                "asset_symbol": asset_symbol.upper(),
                "token_symbol": asset_symbol.upper(),
                "network": network,
                "status": status,
                "public_url": public_url,
                "custody_model": _safe_text(raw.get("custody_model") or raw.get("custodyModel"), 80)
                or "hosted_provider_user_donation",
                "wallet_generation_status": _safe_text(
                    raw.get("wallet_generation_status") or raw.get("walletGenerationStatus"),
                    80,
                )
                or "hosted_provider_no_autoyou_private_keys",
                "note": _safe_text(raw.get("note"), 180),
                "rule": "Hosted public route only; AutoYou does not publish raw wallet addresses or custody donor funds.",
            }
        )
    return sorted(routes, key=lambda item: (item.get("status") != "active", item.get("label", "")))


def _crypto_donation_channel() -> dict[str, Any]:
    hosted_routes = _crypto_donation_routes_from_env()
    active_route = next((route for route in hosted_routes if route.get("status") == "active"), None)
    if active_route:
        return {
            "id": "crypto",
            "label": active_route["label"],
            "type": "crypto",
            "status": "active",
            "url": active_route["public_url"],
            "note": (
                f"Reviewed hosted {active_route['asset_symbol']} route via {active_route['provider']}. "
                "Raw wallet addresses are not official."
            ),
            "hosted_routes": hosted_routes,
        }

    fallback = _donation_channel(
        "crypto",
        os.getenv("AUTOYOU_CRYPTO_DONATION_LABEL", "Crypto donation link"),
        "crypto",
        os.getenv("AUTOYOU_CRYPTO_DONATION_URL", ""),
        "Hosted crypto route only after wallet/provider review. Raw wallet addresses are not official.",
    )
    if hosted_routes:
        fallback["status"] = hosted_routes[0]["status"]
        fallback["url"] = ""
        fallback["label"] = hosted_routes[0]["label"]
        fallback["hosted_routes"] = hosted_routes
        fallback["note"] = "Hosted crypto route exists but is not active for handoff yet."
    return fallback


def _permission_model(account_url: str) -> dict[str, Any]:
    manifest_url = f"{account_url}/v1/funding/manifest"
    public_ledger_url = f"{account_url}/v1/funding/public-ledger"
    dashboard_url = f"{account_url}/dashboard?section=funding"
    return {
        "payment_execution": "external_provider_checkout_only",
        "credential_collection": "disabled",
        "session_takeover": "disabled",
        "official_routes_source": "funding_manifest",
        "public_ledger_contract": {
            "manifest_url": manifest_url,
            "public_ledger_url": public_ledger_url,
            "crypto_donation_routes": "hosted_public_urls_only",
            "raw_payment_identifiers_public": False,
        },
        "connected_account_modes": {
            "account_dashboard": {
                "auth": "oauth_required",
                "url": dashboard_url,
                "allowed_action": "view_funding_os_and_account_owned_settings",
            },
            "client_context": {
                "auth": "user_permission_required",
                "transport": "existing_client_https_session",
                "stores_payment_credentials": False,
            },
            "server_context": {
                "auth": "user_permission_required",
                "transport": "existing_server_connection_flow",
                "stores_payment_credentials": False,
            },
            "provider_checkout": {
                "auth": "provider_owned",
                "execution": "external_provider_only",
                "stores_payment_credentials": False,
            },
        },
        "voice_mode": _voice_handoff_mode(),
    }


def _normalize_key(value: str) -> str:
    return str(value or "").strip().lower().replace("_", "-").replace(" ", "-")


_CHANNEL_ALIASES = {
    "public": "public-donate",
    "public-donate": "public-donate",
    "public-donate-page": "public-donate",
    "donate": "public-donate",
    "donate-page": "public-donate",
    "funding": "funding-dashboard",
    "funding-tab": "funding-dashboard",
    "funding-dashboard": "funding-dashboard",
    "dashboard": "funding-dashboard",
    "card": "stripe",
    "credit-card": "stripe",
    "stripe": "stripe",
    "bmc": "buymeacoffee",
    "buy-me-a-coffee": "buymeacoffee",
    "buymeacoffee": "buymeacoffee",
    "thanks": "thanks-dev",
    "thanksdev": "thanks-dev",
    "thanks-dev": "thanks-dev",
    "crypto": "crypto",
    "hosted-crypto": "crypto",
    "sov": "crypto",  # Legacy fallback alias for USDC/sovereign-token donation flow
}


_HANDOFF_CONTEXTS = {
    "provider-checkout": {
        "id": "provider_checkout",
        "label": "Provider checkout",
        "auth": "provider_owned",
        "transport": "external_provider_page",
        "permission_required": False,
        "allowed_action": "open_official_provider_route",
    },
    "voice": {
        "id": "voice",
        "label": "Voice guidance",
        "auth": "otp_supported_for_local_agent_surface",
        "transport": "voice_or_screen_share_guidance",
        "permission_required": True,
        "allowed_action": "read_or_open_official_route_only",
    },
    "client-context": {
        "id": "client_context",
        "label": "Client context",
        "auth": "user_permission_required",
        "transport": "existing_client_https_session",
        "permission_required": True,
        "allowed_action": "reference_client_context_only_after_user_allows",
    },
    "server-context": {
        "id": "server_context",
        "label": "Server context",
        "auth": "user_permission_required",
        "transport": "existing_server_connection_flow",
        "permission_required": True,
        "allowed_action": "reference_server_context_only_after_user_allows",
    },
    "account-dashboard": {
        "id": "account_dashboard",
        "label": "Account dashboard",
        "auth": "oauth_required",
        "transport": "app_autoyou_me_oauth_session",
        "permission_required": True,
        "allowed_action": "open_funding_tab",
    },
}


_CONTEXT_ALIASES = {
    "provider": "provider-checkout",
    "provider-checkout": "provider-checkout",
    "checkout": "provider-checkout",
    "external-checkout": "provider-checkout",
    "voice": "voice",
    "voice-call": "voice",
    "call": "voice",
    "client": "client-context",
    "client-context": "client-context",
    "server": "server-context",
    "server-context": "server-context",
    "account": "account-dashboard",
    "account-dashboard": "account-dashboard",
    "dashboard": "account-dashboard",
}


def _handoff_policy() -> dict[str, Any]:
    return {
        "payment_execution": "external_provider_checkout_only",
        "credential_collection": "disabled",
        "session_takeover": "disabled",
        "server_storage": "disabled",
        "wallet_generation": "disabled",
        "can_execute_payment": False,
        "can_collect_credentials": False,
        "can_take_over_session": False,
    }


def _resolve_handoff_context(context: str) -> dict[str, Any] | None:
    key = _CONTEXT_ALIASES.get(_normalize_key(context), _normalize_key(context))
    return _HANDOFF_CONTEXTS.get(key)


def _resolve_handoff_channel(channel_id: str, status_payload: dict[str, Any]) -> dict[str, Any] | None:
    key = _CHANNEL_ALIASES.get(_normalize_key(channel_id), _normalize_key(channel_id))
    if key == "public-donate":
        url = _safe_url(status_payload.get("public_donate_page", ""))
        return {
            "id": "public-donate",
            "label": "AutoYou donate page",
            "type": "public",
            "status": "active" if url else "setup_required",
            "url": url,
            "note": "Public website surface that renders official Funding OS donation routes.",
        }
    if key == "funding-dashboard":
        url = _safe_url(status_payload.get("account_dashboard", ""))
        return {
            "id": "funding-dashboard",
            "label": "AutoYou Funding tab",
            "type": "account",
            "status": "active" if url else "setup_required",
            "url": url,
            "note": "OAuth-only app.autoyou.me dashboard surface for account-owned Funding OS settings.",
        }

    channels = {channel.get("id"): channel for channel in status_payload.get("channels", [])}
    channel = channels.get(key)
    return dict(channel) if channel else None


def _handoff_instructions(channel: dict[str, Any], context_policy: dict[str, Any]) -> list[str]:
    instructions = [
        "Use only the returned handoff_url or the account-service funding manifest.",
        "Complete any donation only on the external provider page or app.autoyou.me Funding tab.",
        "Do not type, store, repeat, or relay card, bank, wallet, email, account, or device identifiers.",
    ]
    if context_policy["permission_required"]:
        instructions.append("Confirm explicit user permission before using this context.")
    if context_policy["id"] == "voice":
        instructions.append("In voice mode, read or open the official route only; never ask for payment details or OTP codes aloud.")
    if channel.get("id") == "crypto":
        instructions.append("Use the hosted crypto route only; raw wallet addresses or transaction hashes are not accepted.")
    return instructions


def get_donation_agent_status() -> Dict[str, Any]:
    """Return official donation routes and Funding OS links."""
    account_url = _account_url()
    website_url = _website_url()
    crypto_channel = _crypto_donation_channel()
    return {
        "status": "ready",
        "account_dashboard": f"{account_url}/dashboard?section=funding",
        "oauth_url": f"{account_url}/login?next=%2Fdashboard%3Fsection%3Dfunding",
        "public_donate_page": f"{website_url}/donate/",
        "funding_manifest": f"{account_url}/v1/funding/manifest",
        "public_ledger": f"{account_url}/v1/funding/public-ledger",
        "local_otp_auth": "supported_by_agent_website_security",
        "app_auth": "oauth_only_no_email_signup",
        "permission_model": _permission_model(account_url),
        "funding_rails": [
            {
                "id": "donations",
                "label": "Transparent donations",
                "agent": "donation_agent",
                "scope": "donated_pool",
                "public_visibility": "public_ledger_sanitized_sources_and_expenses",
                "purpose": "Supporter donations fund AutoYou development, Codex, AWS, relays, and approved public requests.",
            },
            {
                "id": "autoyou-operating-income",
                "label": "AutoYou operating income",
                "agent": "ads_watching_agent",
                "scope": "autoyou_operating_income",
                "public_visibility": "aggregate_or_operator_recorded_rows_without_provider_private_ids",
                "purpose": "Ads, app-store, billing, and subscription revenue help keep AutoYou running.",
            },
            {
                "id": "earnings",
                "label": "Consent-gated earnings",
                "agent": "earnings_agent",
                "scope": "authenticated_service_credit_planning",
                "public_visibility": "policy_and_readiness_only_until_opt_in",
                "purpose": "Future personal credits require OAuth, ads-credit consent, verification, and compliance-gated USDC planning.",
            },
        ],
        "crypto_donation_routes": crypto_channel.get("hosted_routes", []),
        "channels": [
            _donation_channel(
                "stripe",
                "Donate with card",
                "fiat",
                os.getenv("AUTOYOU_STRIPE_DONATION_URL", ""),
                "Card or bank-backed donation route when configured by Funding OS.",
            ),
            _donation_channel(
                "buymeacoffee",
                "Buy Me a Coffee",
                "fiat",
                os.getenv("AUTOYOU_BUYMEACOFFEE_URL", "https://buymeacoffee.com/autoyou"),
                "Existing public supporter route.",
            ),
            _donation_channel(
                "thanks-dev",
                "Thanks.dev",
                "open_source",
                os.getenv("AUTOYOU_THANKS_DEV_URL", ""),
                "Open-source dependency appreciation route when configured.",
            ),
            crypto_channel,
        ],
    }


def prepare_donation_handoff(
    channel_id: str = "public-donate",
    context: str = "provider_checkout",
    permission_acknowledged: bool = False,
) -> Dict[str, Any]:
    """Prepare a bounded donation handoff for an official Funding OS route."""
    status_payload = get_donation_agent_status()
    policy = _handoff_policy()
    context_policy = _resolve_handoff_context(context)
    if context_policy is None:
        return {
            "success": False,
            "status": "unsupported_context",
            "handoff_url": "",
            "requested_context": context,
            "allowed_contexts": [value["id"] for value in _HANDOFF_CONTEXTS.values()],
            **policy,
        }

    channel = _resolve_handoff_channel(channel_id, status_payload)
    base_payload = {
        "context": context_policy,
        "permission_required": context_policy["permission_required"],
        "permission_acknowledged": bool(permission_acknowledged),
        "official_routes": {
            "funding_manifest": status_payload["funding_manifest"],
            "public_ledger": status_payload["public_ledger"],
            "public_donate_page": status_payload["public_donate_page"],
            "account_dashboard": status_payload["account_dashboard"],
        },
        **policy,
    }

    if channel is None:
        return {
            "success": False,
            "status": "unsupported_channel",
            "handoff_url": "",
            "requested_channel": channel_id,
            "allowed_channels": ["public-donate", "funding-dashboard"]
            + [item["id"] for item in status_payload.get("channels", [])],
            **base_payload,
        }

    if context_policy["id"] in {"client_context", "server_context"} and not permission_acknowledged:
        return {
            "success": False,
            "status": "user_permission_required",
            "handoff_url": "",
            "channel": channel,
            "instructions": _handoff_instructions(channel, context_policy),
            "reason": "Explicit user permission is required before using connected client or server context.",
            **base_payload,
        }

    if channel.get("status") != "active" or not channel.get("url"):
        return {
            "success": False,
            "status": "setup_required",
            "handoff_url": "",
            "channel": channel,
            "instructions": _handoff_instructions(channel, context_policy),
            "reason": "This route is not configured as an official HTTP(S) Funding OS handoff.",
            **base_payload,
        }

    return {
        "success": True,
        "status": "ready",
        "handoff_url": channel["url"],
        "channel": channel,
        "instructions": _handoff_instructions(channel, context_policy),
        "voice_mode": _voice_handoff_mode(channel),
        **base_payload,
    }


def describe_donation_flow() -> Dict[str, Any]:
    """Describe the safe donation flow for chat or voice guidance."""
    return {
        "supporter_flow": [
            "Open the public donate page or the app.autoyou.me Funding tab.",
            "Use OAuth sign-in on app.autoyou.me if the route needs account context.",
            "Choose an official route such as AutoYou Creator, Substack, Buy Me a Coffee, Thanks.dev, Stripe, or an approved hosted donation link.",
            "Complete payment only on the provider's official checkout page.",
            "Return to Funding OS for public ledger totals and opt-in recognition.",
        ],
        "funding_os_roles": [
            "Donation Agent collects official support routes and public ledger context for transparent supporter donations.",
            "Ads Watching Agent opens optional support ads without creating account value.",
            "Earnings Agent owns future account-action planning when it is reviewed and enabled.",
        ],
        "voice_mode": [
            "Give the supporter the official donate page or Funding tab link.",
            "Do not ask the supporter to dictate card, bank, wallet, email, or account details.",
            "For local agent websites, rely on AutoYou OTP/TOTP web auth when configured.",
            "If the supporter is authenticated, only help them open official routes; do not execute payment steps for them.",
        ],
        "connected_accounts": [
            "Donation guidance can point signed-in users to their account dashboard.",
            "Server/client device access stays permissioned through existing AutoYou account and connection flows.",
            "The donation agent does not take over provider sessions or store provider credentials.",
            "Client and server contexts require explicit user permission before the agent references connected account state.",
        ],
    }


def describe_donation_guardrails() -> Dict[str, Any]:
    """Describe donation safety and crypto boundaries."""
    return {
        "official_source": "Funding OS manifest at app.autoyou.me is the source of truth.",
        "never_collect": [
            "card numbers",
            "bank details",
            "private keys",
            "raw wallet addresses from chat",
            "payment identifiers",
            "emails or account ids for public display",
        ],
        "crypto": [
            "Use only hosted crypto donation links published by Funding OS.",
            "USDC wallet generation and MoonPay/on-ramp support are separate compliance-gated tracks.",
            "AutoYou credits are not cryptocurrency and cannot be swapped to USDC until compliance gates are live.",
        ],
    }


async def _donation_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)
    return None


def create_donation_agent(model_config: Any) -> Agent:
    """Create the Donation Agent."""
    tools: List[Any] = [
        get_current_datetime,
        get_donation_agent_status,
        prepare_donation_handoff,
        describe_donation_flow,
        describe_donation_guardrails,
    ]
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
        before_model_callback=_donation_before_model_callback,
    )
