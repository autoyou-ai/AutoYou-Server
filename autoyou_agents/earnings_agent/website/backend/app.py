# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-b053ac31043d7515a288ae8d

"""Earnings Agent UI backend."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-b053ac31043d7515a288ae8d"


import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Request
from shared.runtime_module_loader import import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module

from autoyou_agents.earnings_agent.agent import (
    describe_funding_os_path,
    describe_usdc_readiness,
    get_earnings_agent_status,
    prepare_earnings_action,
)

_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"
_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
create_agent_chat_app = _smc.create_agent_chat_app
_describe_chat_auth_state = _smc._describe_chat_auth_state
_json_response = _smc._json_response
_runtime_server = _smc._runtime_server

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_AGENT_NAME = "earnings_agent"


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


def _cloud_signed_in() -> bool:
    """Local-only check: is a cloud OAuth session saved in server config?

    The cloud token is single-sourced in the encrypted server config
    (``cloud.server_token``, written by the admin UI OAuth sign-in). Agents
    read derived booleans through the runtime server; they never keep their
    own token copies and this check never calls autoyou.me.
    """
    try:
        runtime = _runtime_server()
        cfg = getattr(getattr(runtime, "STATE", None), "config", None) or {}
        cloud_cfg = cfg.get("cloud", {}) if isinstance(cfg, dict) else {}
        return bool(
            str(
                cloud_cfg.get("server_token")
                or cloud_cfg.get("user_id")
                or cloud_cfg.get("email")
                or ""
            ).strip()
        )
    except Exception:
        return False


def _extra_routes(app, agent_name: str) -> None:
    @app.get("/api/pending-credits")
    async def api_pending_credits():
        """Public local pending ad-credit readback. No cloud calls, no auth.

        Pending credits are collected on client devices (Watch Ads) and
        synced to this server over the connected WebRTC session. Cloud pickup
        for signed-in OAuth users is a future server-side process; this route
        only reports the local tally plus the locally saved sign-in state.
        """
        try:
            from shared.pending_ad_credits import get_pending_ad_credit_summary

            summary = get_pending_ad_credit_summary()
        except Exception:
            summary = {
                "pending_credits": 0,
                "watched_seconds": 0.0,
                "credit_multiplier": 10,
                "events": 0,
                "last_event": None,
                "updated_at": None,
                "ledger_effect": "none",
                "cloud_hot_path_write": False,
            }
        account_url = _account_url()
        response = _json_response(
            {
                "success": True,
                "credits": summary,
                "cloud": {
                    "signed_in": _cloud_signed_in(),
                    "sign_in_url": f"{account_url}/login?next=%2Fdashboard%3Fsection%3Dfunding",
                },
                "sync": {
                    "transport": "webrtc_datachannel",
                    "cloud_writes": "disabled_until_verified_settlement",
                },
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/status")
    async def api_status(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        status = get_earnings_agent_status()
        account_url = _account_url()
        website_url = _website_url()
        return _json_response(
            {
                "success": True,
                "auth": auth,
                "status": status,
                "account": {
                    "base_url": account_url,
                    "dashboard_url": f"{account_url}/dashboard?section=funding",
                    "oauth_url": f"{account_url}/login?next=%2Fdashboard%3Fsection%3Dfunding",
                    "funding_manifest_url": f"{account_url}/v1/funding/manifest",
                    "public_ledger_url": f"{account_url}/v1/funding/public-ledger",
                    "credits_url": f"{account_url}/v1/funding/credits",
                    "contributor_request_url": f"{account_url}/v1/funding/contributor-requests",
                    "sov_status_url": f"{account_url}/v1/funding/sov/status",
                    "sov_wallet_url": f"{account_url}/v1/funding/sov/wallet",
                    "sov_wallet_provisioning_requests_url": f"{account_url}/v1/funding/sov/wallet-provisioning-requests",
                    "sov_settlement_requests_url": f"{account_url}/v1/funding/sov/settlement-requests",
                    "admin_records_url": f"{account_url}/v1/funding/admin/records",
                    "admin_money_sources_url": f"{account_url}/v1/funding/admin/money-sources",
                    "admin_expenses_url": f"{account_url}/v1/funding/admin/expenses",
                    "admin_contributor_requests_url": f"{account_url}/v1/funding/admin/contributor-requests",
                },
                "website": {
                    "donate_url": f"{website_url}/donate/",
                },
                "pc": {
                    "connected": True,
                    "reason": "This web app is being served by the local AutoYou agent runtime.",
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
                "funding_os": describe_funding_os_path(),
                "sov": describe_usdc_readiness(),
            }
        )

    @app.get("/api/action")
    async def api_action(request: Request, action: str = "view-credits"):
        auth = _describe_chat_auth_state(request, agent_name)
        if _local_auth_required(auth):
            return _json_response(
                {
                    "success": False,
                    "error": "Local one-time-code session required for earnings action planning.",
                    "auth": auth,
                    "action": {
                        "status": "local_auth_required",
                        "requested_action": action,
                    },
                },
                status_code=401,
            )
        return _json_response(
            {
                "success": True,
                "auth": auth,
                "action": prepare_earnings_action(action),
            }
        )


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Earnings",
    description="Review AutoYou credits, contributor requests, and payout readiness.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
