# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-363546313441613439306132-50774fc98aeb4a85f1eda49c

"""Earnings Agent implementation."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-363546313441613439306132-50774fc98aeb4a85f1eda49c"


import os
from typing import Any, Dict, List
from urllib.parse import urlparse

from google.adk.agents import Agent

from autoyou_agents.earnings_agent.prompt import (
    AGENT_DESCRIPTION,
    AGENT_INSTRUCTION,
    AGENT_NAME,
)
from autoyou_agents.shared_tools.datetime_tool import (
    get_current_datetime,
    inject_realtime_datetime_into_request,
)


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


def _env_flag_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_flag_status(name: str) -> str:
    return "enabled" if _env_flag_enabled(name) else "disabled"


def _normalize_action(value: str) -> str:
    return str(value or "").strip().lower().replace("_", "-").replace(" ", "-")


_ACTION_ALIASES = {
    "credits": "view-credits",
    "view-credits": "view-credits",
    "credit-balance": "view-credits",
    "wallet": "register-wallet",
    "sov-wallet": "register-wallet",
    "register-wallet": "register-wallet",
    "wallet-reference": "register-wallet",
    "provision-wallet": "provision-wallet",
    "wallet-provisioning": "provision-wallet",
    "generate-wallet": "provision-wallet",
    "create-wallet": "provision-wallet",
    "settlement": "request-settlement",
    "settlement-request": "request-settlement",
    "request-settlement": "request-settlement",
    "sov-settlement": "request-settlement",
    "one-shot-sov": "request-settlement",
    "all-credits-to-sov": "request-settlement",
    "all-available-sov": "request-settlement",
    "moonpay": "moonpay-onramp",
    "onramp": "moonpay-onramp",
    "moonpay-onramp": "moonpay-onramp",
    "contributor": "contributor-request",
    "contributor-request": "contributor-request",
    "funding-request": "contributor-request",
    "funding-dashboard": "funding-dashboard",
    "dashboard": "funding-dashboard",
}


def _earnings_action_limits() -> dict[str, Any]:
    return {
        "local_agent_session_scope": "ui_only_not_account_service_bearer",
        "account_auth": "oauth_required_on_app_autoyou_me",
        "credential_collection": "disabled",
        "private_key_collection": "disabled",
        "wallet_generation": "disabled_provider_required",
        "payment_execution": "disabled",
        "credit_debit": "disabled_until_compliance",
        "token_transfer": "disabled_until_compliance",
        "sov_outcome_recording": "account_admin_external_artifacts_only",
        "can_create_wallet_keys": False,
        "can_debit_credits": False,
        "can_transfer_sov": False,
        "can_record_sov_outcome": False,
    }


def get_pending_ad_credit_summary() -> Dict[str, Any]:
    """Read the local ad tally without touching account-service or cloud state."""
    try:
        from shared.pending_ad_credits import get_pending_ad_credit_summary as _read_summary

        return _read_summary()
    except Exception:
        return {
            "pending_credits": 0,
            "watched_seconds": 0.0,
            "credit_multiplier": 10,
            "events": 0,
            "last_event": None,
            "updated_at": None,
            "ledger_effect": "none",
            "cloud_hot_path_write": False,
        }



def get_earnings_agent_status() -> Dict[str, Any]:
    """Return the local Funding OS and earnings integration status."""
    account_url = _account_url()
    website_url = _website_url()
    credits_enabled = _env_flag_enabled("AUTOYOU_CREDITS_LEDGER_ENABLED")
    sov_wallet_registry_enabled = _env_flag_enabled("AUTOYOU_SOV_WALLET_REGISTRY_ENABLED")
    sov_wallet_provisioning_enabled = _env_flag_enabled("AUTOYOU_SOV_WALLET_PROVISIONING_REQUESTS_ENABLED")
    sov_settlement_requests_enabled = (
        _env_flag_enabled("AUTOYOU_SOV_SETTLEMENT_REQUESTS_ENABLED") and credits_enabled
    )
    return {
        "status": "ready",
        "account_dashboard": f"{account_url}/dashboard?section=funding",
        "oauth_url": f"{account_url}/login?next=%2Fdashboard%3Fsection%3Dfunding",
        "public_donate_page": f"{website_url}/donate/",
        "funding_manifest": f"{account_url}/v1/funding/manifest",
        "public_ledger": f"{account_url}/v1/funding/public-ledger",
        "credits_endpoint": f"{account_url}/v1/funding/credits",
        "contributor_request_endpoint": f"{account_url}/v1/funding/contributor-requests",
        "sov_status_endpoint": f"{account_url}/v1/funding/sov/status",
        "sov_wallet_endpoint": f"{account_url}/v1/funding/sov/wallet",
        "sov_wallet_provisioning_requests_endpoint": f"{account_url}/v1/funding/sov/wallet-provisioning-requests",
        "sov_settlement_requests_endpoint": f"{account_url}/v1/funding/sov/settlement-requests",
        "admin_records_endpoint": f"{account_url}/v1/funding/admin/records",
        "admin_money_sources_endpoint": f"{account_url}/v1/funding/admin/money-sources",
        "admin_expenses_endpoint": f"{account_url}/v1/funding/admin/expenses",
        "admin_contributor_requests_endpoint": f"{account_url}/v1/funding/admin/contributor-requests",
        "reward_intent_endpoint": f"{account_url}/v1/funding/ads/reward-intent",
        "runtime_flags": {
            "credits_ledger": "enabled" if credits_enabled else "disabled",
            "ads_donate": _env_flag_status("AUTOYOU_ADS_DONATE_ENABLED"),
            "contributor_requests": _env_flag_status("AUTOYOU_FUNDING_CONTRIBUTOR_REQUESTS_ENABLED"),
            "crypto_cashout": _env_flag_status("AUTOYOU_CRYPTO_CASHOUT_ENABLED"),
            "sov_wallet_registry": "enabled" if sov_wallet_registry_enabled else "disabled",
            "sov_wallet_provisioning_requests": "enabled" if sov_wallet_provisioning_enabled else "disabled",
            "sov_settlement_requests": "enabled" if sov_settlement_requests_enabled else "disabled",
        },
        "credit_unit": "autoyou_credit",
        "transferable": False,
        "cash_out": "disabled_until_compliance",
        "crypto_swap": "disabled_until_compliance",
        "safety_limits": _earnings_action_limits(),
        "sov": {
            "network": "The USDC settlement",
            "token_symbol": "USDC",
            "wallet_generation": (
                "provider_request_records_enabled_no_server_keys"
                if sov_wallet_provisioning_enabled
                else
                "user_owned_reference_registry_enabled" if sov_wallet_registry_enabled else "disabled_provider_required"
            ),
            "wallet_registry": "enabled" if sov_wallet_registry_enabled else "disabled",
            "wallet_provisioning_requests": "enabled" if sov_wallet_provisioning_enabled else "disabled",
            "moonpay_onramp": "provider_review_required",
            "settlement": (
                "request_records_enabled_no_transfer"
                if sov_settlement_requests_enabled
                else "disabled_until_compliance"
            ),
            "settlement_requests": "enabled" if sov_settlement_requests_enabled else "disabled",
            "settlement_accounting": {
                "status_field": "settlement_accounting",
                "pending_request_credits_field": "settlement_accounting.pending_request_credits",
                "committed_request_credits_field": "settlement_accounting.committed_request_credits",
                "externally_recorded_request_credits_field": "settlement_accounting.externally_recorded_request_credits",
                "available_request_credits_field": "settlement_accounting.available_request_credits",
                "settlement_outcome_fields": [
                    "settlement_request_records[].settlement_outcome",
                    "sov_ledger_entries[].settlement_outcome",
                ],
                "capacity_rule": "pending and externally recorded requests reduce new request capacity without debiting credit_balance or transferring USDC",
            },
            "future_scope": {
                "coins_to_usdc_swap": "planned",
                "usdc_transfer_out_with_kyc": "planned",
                "credits_monitoring_only": "current",
            },
        },
        "usdc": {
            "network": "The USDC settlement",
            "token_symbol": "USDC",
            "wallet_generation": (
                "provider_request_records_enabled_no_server_keys"
                if sov_wallet_provisioning_enabled
                else
                "user_owned_reference_registry_enabled" if sov_wallet_registry_enabled else "disabled_provider_required"
            ),
            "wallet_registry": "enabled" if sov_wallet_registry_enabled else "disabled",
            "wallet_provisioning_requests": "enabled" if sov_wallet_provisioning_enabled else "disabled",
            "moonpay_onramp": "provider_review_required",
            "settlement": (
                "request_records_enabled_no_transfer"
                if sov_settlement_requests_enabled
                else "disabled_until_compliance"
            ),
            "settlement_requests": "enabled" if sov_settlement_requests_enabled else "disabled",
            "settlement_accounting": {
                "status_field": "settlement_accounting",
                "pending_request_credits_field": "settlement_accounting.pending_request_credits",
                "committed_request_credits_field": "settlement_accounting.committed_request_credits",
                "externally_recorded_request_credits_field": "settlement_accounting.externally_recorded_request_credits",
                "available_request_credits_field": "settlement_accounting.available_request_credits",
                "settlement_outcome_fields": [
                    "settlement_request_records[].settlement_outcome",
                    "sov_ledger_entries[].settlement_outcome",
                ],
                "capacity_rule": "pending and externally recorded requests reduce new request capacity without debiting credit_balance or transferring USDC",
            },
            "future_scope": {
                "coins_to_usdc_swap": "planned",
                "usdc_transfer_out_with_kyc": "planned",
                "credits_monitoring_only": "current",
            },
        },
    }


def prepare_earnings_action(action: str = "view-credits") -> Dict[str, Any]:
    """Prepare a guarded OAuth handoff for credits, contributor, or USDC account actions."""
    account_url = _account_url()
    website_url = _website_url()
    requested_key = _normalize_action(action)
    normalized = _ACTION_ALIASES.get(requested_key, requested_key)
    all_available_settlement = normalized == "request-settlement" and requested_key in {
        "one-shot-sov",
        "all-credits-to-sov",
        "all-available-sov",
    }
    credits_enabled = _env_flag_enabled("AUTOYOU_CREDITS_LEDGER_ENABLED")
    contributor_requests_enabled = _env_flag_enabled("AUTOYOU_FUNDING_CONTRIBUTOR_REQUESTS_ENABLED")
    contributor_campaign_evidence_required = os.getenv(
        "AUTOYOU_FUNDING_CONTRIBUTOR_REQUESTS_REQUIRE_CAMPAIGN_EVIDENCE",
        "true",
    ).strip().lower() in {"1", "true", "yes", "on"}
    sov_wallet_registry_enabled = _env_flag_enabled("AUTOYOU_SOV_WALLET_REGISTRY_ENABLED")
    sov_wallet_provisioning_enabled = _env_flag_enabled("AUTOYOU_SOV_WALLET_PROVISIONING_REQUESTS_ENABLED")
    sov_settlement_requests_enabled = (
        _env_flag_enabled("AUTOYOU_SOV_SETTLEMENT_REQUESTS_ENABLED") and credits_enabled
    )
    moonpay_url = _safe_url(os.getenv("AUTOYOU_MOONPAY_ONRAMP_URL", ""))
    dashboard_url = f"{account_url}/dashboard?section=funding"
    limits = _earnings_action_limits()
    base = {
        "requested_action": action,
        "action": normalized,
        "account_dashboard": dashboard_url,
        "oauth_url": f"{account_url}/login?next=%2Fdashboard%3Fsection%3Dfunding",
        "public_donate_page": f"{website_url}/donate/",
        "runtime_flags": {
            "credits_ledger": "enabled" if credits_enabled else "disabled",
            "contributor_requests": "enabled" if contributor_requests_enabled else "disabled",
            "sov_wallet_registry": "enabled" if sov_wallet_registry_enabled else "disabled",
            "sov_wallet_provisioning_requests": "enabled" if sov_wallet_provisioning_enabled else "disabled",
            "sov_settlement_requests": "enabled" if sov_settlement_requests_enabled else "disabled",
            "moonpay_onramp": "configured" if moonpay_url else "provider_review_required",
        },
        "safety_limits": limits,
    }

    actions: dict[str, dict[str, Any]] = {
        "view-credits": {
            "label": "View AutoYou credits",
            "status": "ready",
            "method": "GET",
            "api_url": f"{account_url}/v1/funding/credits",
            "handoff_url": dashboard_url,
            "requires_oauth": True,
            "requires_runtime_flag": "AUTOYOU_CREDITS_LEDGER_ENABLED",
            "instructions": [
                "Open app.autoyou.me with OAuth to view account-owned credit status.",
                "Treat credits as internal, non-transferable AutoYou service credits.",
                "The local earnings-agent OTP session cannot read account credit records.",
            ],
        },
        "register-wallet": {
            "label": "Register user-owned USDC wallet reference",
            "status": "ready" if sov_wallet_registry_enabled else "setup_required",
            "method": "POST",
            "api_url": f"{account_url}/v1/funding/sov/wallet",
            "handoff_url": dashboard_url,
            "requires_oauth": True,
            "requires_runtime_flag": "AUTOYOU_SOV_WALLET_REGISTRY_ENABLED",
            "request_fields": ["publicAddress", "provider", "network", "note"],
            "forbidden_fields": [
                "moonpayCustomerId",
                "customerId",
                "privateKey",
                "seedPhrase",
                "recoveryPhrase",
                "secret",
                "rawPayload",
            ],
            "instructions": [
                "Register only a user-owned public wallet reference through the OAuth Funding tab.",
                "Do not paste provider customer ids, private keys, seed phrases, recovery phrases, or raw provider payloads.",
                "AutoYou does not generate or custody wallet keys in this route.",
            ],
        },
        "provision-wallet": {
            "label": "Request provider-mediated USDC wallet provisioning",
            "status": "ready" if sov_wallet_provisioning_enabled else "setup_required",
            "method": "POST",
            "api_url": f"{account_url}/v1/funding/sov/wallet-provisioning-requests",
            "handoff_url": dashboard_url,
            "requires_oauth": True,
            "requires_runtime_flag": "AUTOYOU_SOV_WALLET_PROVISIONING_REQUESTS_ENABLED",
            "request_fields": ["provider", "network", "publicArtifacts", "note"],
            "forbidden_fields": [
                "publicAddress",
                "walletAddress",
                "privateKey",
                "seedPhrase",
                "recoveryPhrase",
                "secret",
                "moonpayCustomerId",
                "customerId",
                "email",
                "phoneNumber",
                "rawPayload",
            ],
            "instructions": [
                "Create only a provider-mediated wallet provisioning request record through OAuth.",
                "AutoYou does not generate, custody, or store wallet private keys for this action.",
                "Do not send wallet addresses, provider customer ids, contact details, secrets, or raw provider payloads.",
            ],
        },
        "request-settlement": {
            "label": "Create USDC settlement request record",
            "status": "ready" if sov_settlement_requests_enabled else "setup_required",
            "method": "POST",
            "api_url": f"{account_url}/v1/funding/sov/settlement-requests",
            "handoff_url": dashboard_url,
            "requires_oauth": True,
            "requires_runtime_flag": "AUTOYOU_SOV_SETTLEMENT_REQUESTS_ENABLED and AUTOYOU_CREDITS_LEDGER_ENABLED",
            "request_fields": ["amountCredits", "allAvailable", "walletId", "publicArtifacts", "note"],
            "amount_modes": ["fixed_amount", "all_available"],
            "mutually_exclusive_fields": [["amountCredits", "allAvailable=true"]],
            "status_fields": [
                "credit_balance",
                "settlement_accounting.pending_request_credits",
                "settlement_accounting.committed_request_credits",
                "settlement_accounting.externally_recorded_request_credits",
                "settlement_accounting.available_request_credits",
                "settlement_request_records[].settlement_outcome",
                "settlement_request_records",
                "sov_ledger_entries",
            ],
            "prerequisites": [
                "credits ledger enabled",
                "registered user-owned USDC wallet reference",
                "available settlement request capacity after pending or externally recorded USDC requests",
                "provider, compliance, sanctions, tax, app-store, and board gates complete before transfer",
            ],
            "instructions": [
                "Create a pending compliance request record only after OAuth sign-in.",
                "This action does not debit credits, reserve funds, or transfer USDC.",
                "Use allAvailable=true for a one-shot request of all server-resolved available_request_credits, or set amountCredits for a fixed amount.",
                "Check settlement_accounting.available_request_credits before choosing amountCredits.",
                "Admin-recorded USDC outcomes may appear later as external public artifacts, not as AutoYou token transfers.",
                "Use walletId from the account wallet registry; do not submit raw wallet secrets.",
                "Attach only public review artifacts and a sanitized public note.",
            ],
        },
        "moonpay-onramp": {
            "label": "Open reviewed hosted MoonPay/on-ramp link",
            "status": "ready" if moonpay_url else "setup_required",
            "method": "GET",
            "api_url": f"{account_url}/v1/funding/sov/status",
            "handoff_url": moonpay_url,
            "requires_oauth": True,
            "requires_runtime_flag": "AUTOYOU_MOONPAY_ONRAMP_URL",
            "instructions": [
                "Use only the reviewed hosted on-ramp URL exposed by Funding OS.",
                "AutoYou does not custody funds or generate wallet keys for this link-out.",
                "If no URL is returned, the provider review is not complete.",
            ],
        },
        "contributor-request": {
            "label": "Submit contributor funding request",
            "status": "ready" if contributor_requests_enabled else "setup_required",
            "method": "POST",
            "api_url": f"{account_url}/v1/funding/contributor-requests",
            "handoff_url": dashboard_url,
            "requires_oauth": True,
            "requires_runtime_flag": "AUTOYOU_FUNDING_CONTRIBUTOR_REQUESTS_ENABLED",
            "amount_modes": ["requested_percent", "fixed_usd"],
            "mutually_exclusive_fields": [["requestedPercent", "requestedAmountUsd"]],
            "percentage": {
                "wire_field": "requestedPercent",
                "preferred_wire_unit": "fraction_0_to_1",
                "accepted_wire_units": ["fraction_0_to_1", "human_percent_0_to_100"],
                "normalization_rule": "Values greater than 1 and up to 100 are interpreted as human percent and divided by 100.",
                "dashboard_input_unit": "percent_0_to_100",
                "maximum_percent": 100,
            },
            "fixed_amount": {
                "wire_field": "requestedAmountUsd",
                "currency": "USD",
                "maximum_source": "public_ledger.summary.available_pool_usd",
            },
            "campaign_evidence": {
                "runtime_flag": "AUTOYOU_FUNDING_CONTRIBUTOR_REQUESTS_REQUIRE_CAMPAIGN_EVIDENCE",
                "required": contributor_campaign_evidence_required,
                "accepted_trust_surfaces": ["social_campaign", "community_thread", "code_host", "funding_provider"],
            },
            "request_fields": [
                "contributorDisplayName",
                "requestTitle",
                "requestPurpose",
                "requestedPercent",
                "requestedAmountUsd",
                "sourceBucket",
                "publicArtifacts",
                "note",
            ],
            "prerequisites": [
                "approved contributor account, private GitHub provider-id allowlist, or registry match",
                "public social, community, GitHub, or board artifact links",
                "admin and OpenStorey LLC board review before money movement",
            ],
            "instructions": [
                "Use a public request title and purpose such as development, maintenance, developer salary, or reimbursement.",
                "Choose either a requested percentage of the current donated pool or a fixed USD amount.",
                "Prefer requestedPercent as a 0-1 fraction; human 0-100 percent values are normalized by account-service.",
                "Do not send requestedPercent and requestedAmountUsd together; account-service rejects mixed amount modes.",
                "Fixed USD requests require a positive donated pool and cannot exceed public_ledger.summary.available_pool_usd.",
                "Attach campaign-grade public artifact links for social, community, code-host, or funding-provider accountability.",
                "The request is public and pending; it does not reserve or transfer donated funds.",
            ],
        },
        "funding-dashboard": {
            "label": "Open Funding OS dashboard",
            "status": "ready",
            "method": "GET",
            "api_url": f"{account_url}/v1/funding/manifest",
            "handoff_url": dashboard_url,
            "requires_oauth": True,
            "instructions": [
                "Open the app.autoyou.me Funding tab through OAuth.",
                "Use it to view public ledger, credits, USDC readiness, and contributor request controls.",
            ],
        },
    }

    selected = actions.get(normalized)
    if not selected:
        return {
            "success": False,
            "status": "unsupported_action",
            "allowed_actions": sorted(actions.keys()),
            **base,
        }

    response = {
        "success": selected["status"] == "ready",
        "ready": selected["status"] == "ready",
        **base,
        **selected,
    }
    if all_available_settlement:
        response.update(
            {
                "label": "Create one-shot all-available USDC settlement request record",
                "request_mode": "all_available",
                "recommended_request_body": {
                    "allAvailable": True,
                    "amountCredits": 0,
                    "walletId": "<registered-wallet-id>",
                    "publicArtifacts": [],
                    "note": "",
                },
                "instructions": [
                    "Use allAvailable=true to ask account-service to resolve the current available_request_credits server-side.",
                    *selected.get("instructions", []),
                ],
            }
        )
    return response


def describe_funding_os_path() -> Dict[str, Any]:
    """Describe the AutoYou Funding OS flow without making payout promises."""
    return {
        "donations": [
            "Publish official Stripe, Buy Me a Coffee, Thanks.dev, bank/fiscal-host, and crypto donation routes through the account-service manifest.",
            "Track sanitized public money-source records and opt-in supporter recognition in Funding OS.",
            "Keep raw bank details, private wallet keys, payment identifiers, emails, phone numbers, and account ids out of public views.",
        ],
        "expenses": [
            "Record public expense rows for AWS, development, AI tooling, relays, and operating costs.",
            "Compute available pool from received public sources minus recorded expenses and approved contributor requests.",
            "Render the same public ledger on app.autoyou.me and the public donate page.",
        ],
        "contributors": [
            "Approved contributors can submit a requested percent or USD amount from the public Funding tab when the operator enables the route.",
            "Each request must include public artifact links such as social campaign posts, approval threads, or board artifacts.",
            "Requests are pending records until admin and OpenStorey LLC board approval are recorded.",
        ],
        "earnings": [
            "Ads Watching default native ads are optional support ads and do not create account value.",
            "Credits stay server-maintained, non-transferable, and separate from donation pool accounting.",
            "USDC wallet references and settlement request records are account-authenticated and disabled unless the operator enables guarded flags.",
            "AutoYou operating income from subscriptions, app stores, and support ads stays separate from user account records.",
        ],
    }


def describe_usdc_readiness() -> Dict[str, Any]:
    """Describe the USDC integration readiness gates."""
    return {
        "network": "The USDC settlement",
        "token_symbol": "USDC",
        "current_status": "research_integration",
        "wallet_generation": "disabled_provider_required",
        "moonpay_onramp": "provider_review_required",
        "credit_settlement": "disabled_until_compliance",
        "future_scope": {
            "coins_to_usdc_swap": "planned",
            "usdc_transfer_out_with_kyc": "planned",
            "credits_monitoring_only": "current",
        },
        "implemented_routes": [
            "GET /v1/funding/sov/status",
            "POST /v1/funding/sov/wallet",
            "POST /v1/funding/sov/wallet-provisioning-requests",
            "POST /v1/funding/sov/settlement-requests",
            "POST /v1/funding/admin/sov/settlement-requests/{request_id}/outcome",
        ],
        "authenticated_status_fields": [
            "wallets",
            "settlement_request_records",
            "settlement_request_records[].settlement_outcome",
            "settlement_accounting",
            "sov_ledger_entries",
            "sov_ledger_entries[].settlement_outcome",
            "credit_balance",
        ],
        "implemented_route_limits": [
            "wallet route stores user-owned public wallet references only",
            "wallet provisioning route stores provider-mediated request records only and never server-generated keys",
            "USDC ledger entries are derived from settlement request records and do not create token transfers",
            "private keys, seed phrases, recovery phrases, and raw provider payloads are rejected",
            "pending and externally recorded USDC requests reduce settlement_accounting.available_request_credits without debiting credit_balance",
            "settlement request route creates a pending compliance record only and does not debit credits or transfer USDC",
            "admin USDC outcome route records external public artifacts only and rejects transaction hashes, wallet addresses, provider ids, and raw payloads",
        ],
        "required_gates": [
            "provider and wallet custody review",
            "KYC/AML and sanctions screening",
            "tax reporting and user statements",
            "jurisdiction and consumer-protection review",
            "Apple and Google app-store policy review",
            "OpenStorey LLC board approval",
            "synthetic end-to-end tests with no live wallet mutation",
        ],
    }


async def _earnings_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)
    return None


def create_earnings_agent(model_config: Any) -> Agent:
    """Create the Earnings Agent."""
    tools: List[Any] = [
        get_current_datetime,
        get_earnings_agent_status,
        get_pending_ad_credit_summary,
        prepare_earnings_action,
        describe_funding_os_path,
        describe_usdc_readiness,
    ]
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
        before_model_callback=_earnings_before_model_callback,
    )
