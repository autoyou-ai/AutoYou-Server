# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-d6f1ed96b999baccdc1e223a


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from autoyou_agents.donation_agent.agent import (
    describe_donation_flow,
    describe_donation_guardrails,
    get_donation_agent_status,
    prepare_donation_handoff,
)
from autoyou_agents.donation_agent import donation_links
from autoyou_agents.donation_agent.donation_links import get_public_donation_links
from autoyou_agents.shared_tools.agent_install_registry import DEFAULT_AGENT_INSTALL_STATES

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-d6f1ed96b999baccdc1e223a"


def test_donation_agent_status_exposes_official_routes(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "https://www.autoyou.test")
    monkeypatch.setenv("AUTOYOU_STRIPE_DONATION_URL", "https://pay.autoyou.test/donate")
    monkeypatch.setenv("AUTOYOU_THANKS_DEV_URL", "https://thanks.dev/u/gh/autoyou")
    monkeypatch.setenv("AUTOYOU_CRYPTO_DONATION_URL", "https://crypto.autoyou.test/pay")
    monkeypatch.setenv("AUTOYOU_CRYPTO_DONATION_LABEL", "Hosted crypto")

    payload = get_donation_agent_status()

    assert payload["account_dashboard"] == "https://app.autoyou.test/dashboard?section=funding"
    assert payload["oauth_url"] == "https://app.autoyou.test/login?next=%2Fdashboard%3Fsection%3Dfunding"
    assert payload["public_donate_page"] == "https://www.autoyou.test/donate/"
    assert payload["funding_manifest"] == "https://app.autoyou.test/v1/funding/manifest"
    assert payload["public_ledger"] == "https://app.autoyou.test/v1/funding/public-ledger"
    assert payload["local_otp_auth"] == "supported_by_agent_website_security"
    assert payload["app_auth"] == "oauth_only_no_email_signup"
    assert payload["permission_model"]["payment_execution"] == "external_provider_checkout_only"
    assert payload["permission_model"]["credential_collection"] == "disabled"
    assert payload["permission_model"]["session_takeover"] == "disabled"
    assert payload["permission_model"]["official_routes_source"] == "funding_manifest"
    assert payload["permission_model"]["public_ledger_contract"]["manifest_url"] == (
        "https://app.autoyou.test/v1/funding/manifest"
    )
    assert payload["permission_model"]["public_ledger_contract"]["crypto_donation_routes"] == (
        "hosted_public_urls_only"
    )
    assert payload["permission_model"]["public_ledger_contract"]["raw_payment_identifiers_public"] is False
    assert payload["permission_model"]["connected_account_modes"]["client_context"]["auth"] == (
        "user_permission_required"
    )
    assert payload["permission_model"]["connected_account_modes"]["server_context"]["auth"] == (
        "user_permission_required"
    )
    assert payload["permission_model"]["connected_account_modes"]["provider_checkout"]["execution"] == (
        "external_provider_only"
    )
    assert payload["permission_model"]["voice_mode"]["otp_supported"] is True
    assert payload["permission_model"]["voice_mode"]["collect_payment_details"] is False
    rails = {rail["id"]: rail for rail in payload["funding_rails"]}
    assert rails["donations"]["scope"] == "donated_pool"
    assert rails["autoyou-operating-income"]["agent"] == "ads_watching_agent"
    assert "help keep AutoYou running" in rails["autoyou-operating-income"]["purpose"]
    assert rails["earnings"]["agent"] == "earnings_agent"
    assert "ads-credit consent" in rails["earnings"]["purpose"]

    channels = {channel["id"]: channel for channel in payload["channels"]}
    assert channels["stripe"]["status"] == "active"
    assert channels["stripe"]["url"] == "https://pay.autoyou.test/donate"
    assert channels["buymeacoffee"]["status"] == "active"
    assert channels["buymeacoffee"]["url"] == "https://buymeacoffee.com/autoyou"
    assert channels["thanks-dev"]["status"] == "active"
    assert channels["crypto"]["label"] == "Hosted crypto"
    assert channels["crypto"]["url"] == "https://crypto.autoyou.test/pay"
    assert payload["crypto_donation_routes"] == []


def test_donation_agent_sanitizes_unsafe_base_urls(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://operator:secret@app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "javascript:alert(1)")

    payload = get_donation_agent_status()
    handoff = prepare_donation_handoff(channel_id="public-donate", context="provider_checkout")
    serialized = json.dumps({"status": payload, "handoff": handoff})

    assert payload["account_dashboard"] == "https://app.autoyou.me/dashboard?section=funding"
    assert payload["public_donate_page"] == "https://www.autoyou.me/donate/"
    assert handoff["handoff_url"] == "https://www.autoyou.me/donate/"
    assert "operator:secret" not in serialized
    assert "javascript:" not in serialized


def test_donation_agent_rejects_remote_http_base_urls(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "http://app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "http://www.autoyou.test")

    payload = get_donation_agent_status()
    handoff = prepare_donation_handoff(channel_id="public-donate", context="provider_checkout")

    assert payload["account_dashboard"] == "https://app.autoyou.me/dashboard?section=funding"
    assert payload["public_donate_page"] == "https://www.autoyou.me/donate/"
    assert handoff["handoff_url"] == "https://www.autoyou.me/donate/"


def test_donation_agent_allows_local_http_base_urls(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "http://localhost:8060")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "http://127.0.0.1:8050")

    payload = get_donation_agent_status()
    handoff = prepare_donation_handoff(channel_id="public-donate", context="provider_checkout")

    assert payload["account_dashboard"] == "http://localhost:8060/dashboard?section=funding"
    assert payload["public_donate_page"] == "http://127.0.0.1:8050/donate/"
    assert handoff["handoff_url"] == "http://127.0.0.1:8050/donate/"


def test_donation_agent_prefers_reviewed_active_crypto_route_registry(monkeypatch):
    monkeypatch.setenv("AUTOYOU_CRYPTO_DONATION_URL", "https://legacy-crypto.autoyou.test/pay")
    monkeypatch.setenv(
        "AUTOYOU_FUNDING_CRYPTO_DONATION_ROUTES_JSON",
        json.dumps(
            [
                {
                    "id": "manual-route",
                    "label": "USDC manual review route",
                    "provider": "Hosted provider",
                    "assetSymbol": "USDC",
                    "network": "sov",
                    "status": "manual_review",
                    "publicUrl": "https://crypto.autoyou.test/manual-review",
                },
                {
                    "id": "active-route",
                    "label": "USDC hosted checkout",
                    "provider": "MoonPay hosted checkout",
                    "assetSymbol": "USDC",
                    "network": "sov",
                    "status": "active",
                    "publicUrl": "https://crypto.autoyou.test/hosted-sov",
                },
            ]
        ),
    )

    status = get_donation_agent_status()
    channels = {channel["id"]: channel for channel in status["channels"]}
    handoff = prepare_donation_handoff(channel_id="sov", context="provider_checkout")

    assert len(status["crypto_donation_routes"]) == 2
    assert channels["crypto"]["status"] == "active"
    assert channels["crypto"]["label"] == "USDC hosted checkout"
    assert channels["crypto"]["url"] == "https://crypto.autoyou.test/hosted-sov"
    assert channels["crypto"]["hosted_routes"][0]["id"] == "active-route"
    assert handoff["success"] is True
    assert handoff["handoff_url"] == "https://crypto.autoyou.test/hosted-sov"
    assert "Raw wallet addresses are not official" in handoff["channel"]["note"]


def test_donation_agent_ignores_private_crypto_route_registry(monkeypatch):
    monkeypatch.delenv("AUTOYOU_CRYPTO_DONATION_URL", raising=False)
    monkeypatch.setenv(
        "AUTOYOU_FUNDING_CRYPTO_DONATION_ROUTES_JSON",
        json.dumps(
            [
                {
                    "label": "Unsafe wallet route",
                    "provider": "Hosted provider",
                    "status": "active",
                    "publicUrl": "https://crypto.autoyou.test/unsafe",
                    "walletAddress": "sov1syntheticwalletaddressagent001",
                },
                {
                    "label": "Unsafe private key route",
                    "provider": "Hosted provider",
                    "status": "active",
                    "publicUrl": "https://crypto.autoyou.test/private-key",
                    "privateKey": "synthetic-private-key",
                },
                {
                    "label": "Unsafe URL route",
                    "provider": "Hosted provider",
                    "status": "active",
                    "publicUrl": "wallet:sov1syntheticwalletaddressagent002",
                },
            ]
        ),
    )

    status = get_donation_agent_status()
    channels = {channel["id"]: channel for channel in status["channels"]}
    handoff = prepare_donation_handoff(channel_id="crypto", context="provider_checkout")

    assert status["crypto_donation_routes"] == []
    assert channels["crypto"]["status"] == "setup_required"
    assert channels["crypto"]["url"] == ""
    assert handoff["success"] is False
    assert handoff["status"] == "setup_required"


def test_donation_agent_rejects_untrusted_urls(monkeypatch):
    monkeypatch.setenv("AUTOYOU_STRIPE_DONATION_URL", "javascript:alert(1)")
    monkeypatch.setenv("AUTOYOU_THANKS_DEV_URL", "https://token@example.test/thanks")
    monkeypatch.setenv("AUTOYOU_CRYPTO_DONATION_URL", "wallet:synthetic-wallet")

    channels = {channel["id"]: channel for channel in get_donation_agent_status()["channels"]}

    assert channels["stripe"]["status"] == "setup_required"
    assert channels["stripe"]["url"] == ""
    assert channels["thanks-dev"]["status"] == "setup_required"
    assert channels["thanks-dev"]["url"] == ""
    assert channels["crypto"]["status"] == "setup_required"
    assert channels["crypto"]["url"] == ""


def test_donation_agent_rejects_remote_http_provider_urls(monkeypatch):
    monkeypatch.setenv("AUTOYOU_STRIPE_DONATION_URL", "http://pay.autoyou.test/donate")
    monkeypatch.setenv("AUTOYOU_BUYMEACOFFEE_URL", "http://coffee.autoyou.test/support")
    monkeypatch.setenv("AUTOYOU_THANKS_DEV_URL", "http://thanks.autoyou.test/support")
    monkeypatch.setenv("AUTOYOU_CRYPTO_DONATION_URL", "http://crypto.autoyou.test/pay")

    channels = {channel["id"]: channel for channel in get_donation_agent_status()["channels"]}
    handoff = prepare_donation_handoff(channel_id="stripe", context="provider_checkout")

    assert channels["stripe"]["status"] == "setup_required"
    assert channels["stripe"]["url"] == ""
    assert channels["buymeacoffee"]["status"] == "setup_required"
    assert channels["buymeacoffee"]["url"] == ""
    assert channels["thanks-dev"]["status"] == "setup_required"
    assert channels["thanks-dev"]["url"] == ""
    assert channels["crypto"]["status"] == "setup_required"
    assert channels["crypto"]["url"] == ""
    assert handoff["success"] is False
    assert handoff["handoff_url"] == ""
    assert "HTTP(S) Funding OS handoff" in handoff["reason"]


def test_donation_links_name_ripple_and_tether_and_preserve_xrp_memo(tmp_path, monkeypatch):
    config_path = tmp_path / "donations.json"
    config_path.write_text(
        json.dumps(
            {
                "crypto": [
                    {
                        "id": "xrp",
                        "name": "XRP",
                        "network": "XRP Ledger",
                        "symbol": "XRP",
                        "address": "rSyntheticDonationAddress001",
                        "memo": "123456789",
                    },
                    {
                        "id": "usdt-ethereum",
                        "name": "USDT",
                        "network": "Ethereum Network",
                        "symbol": "USDT",
                        "address": "0xSyntheticDonationAddress001",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOYOU_DONATIONS_CONFIG_PATH", str(config_path))

    links = get_public_donation_links(force_reload=True)
    routes = {route["id"]: route for route in links["crypto"]}

    assert routes["xrp"]["name"] == "Ripple (XRP)"
    assert routes["xrp"]["memo"] == "123456789"
    assert "deposit_url" not in routes["xrp"]
    assert routes["usdt-ethereum"]["name"] == "Tether (USDT) on Ethereum"


def test_donation_links_use_bundled_config_when_present(tmp_path, monkeypatch):
    bundle_root = tmp_path / "bundle"
    config_path = bundle_root / "config" / "donations.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("AUTOYOU_DONATIONS_CONFIG_PATH", raising=False)
    monkeypatch.setattr(donation_links, "get_resources_root", lambda anchor: bundle_root)

    assert donation_links._config_path() == config_path


def test_donation_agent_allows_local_http_development_provider_url(monkeypatch):
    monkeypatch.setenv("AUTOYOU_STRIPE_DONATION_URL", "http://localhost:8060/donate")

    channels = {channel["id"]: channel for channel in get_donation_agent_status()["channels"]}
    handoff = prepare_donation_handoff(channel_id="stripe", context="provider_checkout")

    assert channels["stripe"]["status"] == "active"
    assert channels["stripe"]["url"] == "http://localhost:8060/donate"
    assert handoff["success"] is True
    assert handoff["handoff_url"] == "http://localhost:8060/donate"


def test_donation_agent_rejects_remote_http_crypto_route_registry(monkeypatch):
    monkeypatch.delenv("AUTOYOU_CRYPTO_DONATION_URL", raising=False)
    monkeypatch.setenv(
        "AUTOYOU_FUNDING_CRYPTO_DONATION_ROUTES_JSON",
        json.dumps(
            [
                {
                    "label": "Remote HTTP checkout",
                    "provider": "Hosted provider",
                    "assetSymbol": "USDC",
                    "network": "sov",
                    "status": "active",
                    "publicUrl": "http://crypto.autoyou.test/hosted-sov",
                }
            ]
        ),
    )

    status = get_donation_agent_status()
    channels = {channel["id"]: channel for channel in status["channels"]}

    assert status["crypto_donation_routes"] == []
    assert channels["crypto"]["status"] == "setup_required"
    assert channels["crypto"]["url"] == ""


def test_donation_handoff_prepares_voice_safe_official_route(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "https://www.autoyou.test")
    monkeypatch.setenv("AUTOYOU_BUYMEACOFFEE_URL", "https://coffee.autoyou.test/support")

    payload = prepare_donation_handoff(channel_id="buy-me-a-coffee", context="voice")

    assert payload["success"] is True
    assert payload["status"] == "ready"
    assert payload["handoff_url"] == "https://coffee.autoyou.test/support"
    assert payload["channel"]["id"] == "buymeacoffee"
    assert payload["context"]["id"] == "voice"
    assert payload["permission_required"] is True
    assert payload["payment_execution"] == "external_provider_checkout_only"
    assert payload["credential_collection"] == "disabled"
    assert payload["session_takeover"] == "disabled"
    assert payload["can_execute_payment"] is False
    assert payload["can_collect_credentials"] is False
    assert payload["voice_mode"]["collect_payment_details"] is False
    assert payload["voice_mode"]["local_otp_scope"] == "agent_ui_only_not_payment_execution"
    assert payload["voice_mode"]["official_host"] == "coffee.autoyou.test"
    assert payload["voice_mode"]["payment_execution"] == "external_provider_checkout_only"
    assert "card numbers" in payload["voice_mode"]["never_collect"]
    assert any("official AutoYou Funding OS donation route" in item for item in payload["voice_mode"]["read_aloud"])
    assert any("payment details or OTP codes aloud" in item for item in payload["instructions"])


def test_donation_handoff_rejects_unconfigured_crypto_without_wallet_fallback(monkeypatch):
    monkeypatch.delenv("AUTOYOU_CRYPTO_DONATION_URL", raising=False)

    payload = prepare_donation_handoff(channel_id="crypto", context="provider_checkout")

    assert payload["success"] is False
    assert payload["status"] == "setup_required"
    assert payload["handoff_url"] == ""
    assert payload["channel"]["id"] == "crypto"
    assert payload["channel"]["url"] == ""
    assert payload["wallet_generation"] == "disabled"
    assert payload["can_execute_payment"] is False
    assert "HTTP(S) Funding OS handoff" in payload["reason"]
    assert any("raw wallet addresses" in item for item in payload["instructions"])


def test_donation_handoff_marks_client_and_server_contexts_permissioned(monkeypatch):
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "https://www.autoyou.test")

    client_payload = prepare_donation_handoff(channel_id="public-donate", context="client_context")
    server_payload = prepare_donation_handoff(channel_id="public-donate", context="server_context")
    allowed_client_payload = prepare_donation_handoff(
        channel_id="public-donate",
        context="client_context",
        permission_acknowledged=True,
    )
    # from __debug_provenance_y__ import legal

    assert client_payload["success"] is False
    assert client_payload["status"] == "user_permission_required"
    assert client_payload["handoff_url"] == ""
    assert client_payload["context"]["transport"] == "existing_client_https_session"
    assert client_payload["permission_required"] is True
    assert client_payload["permission_acknowledged"] is False
    assert "Explicit user permission" in client_payload["reason"]
    assert any("explicit user permission" in item for item in client_payload["instructions"])

    assert server_payload["success"] is False
    assert server_payload["status"] == "user_permission_required"
    assert server_payload["context"]["transport"] == "existing_server_connection_flow"
    assert server_payload["permission_required"] is True
    assert server_payload["server_storage"] == "disabled"

    assert allowed_client_payload["success"] is True
    assert allowed_client_payload["handoff_url"] == "https://www.autoyou.test/donate/"
    assert allowed_client_payload["permission_acknowledged"] is True


def test_donation_handoff_rejects_unknown_context():
    payload = prepare_donation_handoff(channel_id="public-donate", context="unsafe_delegate")

    assert payload["success"] is False
    assert payload["status"] == "unsupported_context"
    assert payload["handoff_url"] == ""
    assert "provider_checkout" in payload["allowed_contexts"]


def test_donation_agent_describes_oauth_otp_and_crypto_guardrails():
    flow = describe_donation_flow()
    guardrails = describe_donation_guardrails()

    assert any("OAuth sign-in" in item for item in flow["supporter_flow"])
    assert any("Donation Agent" in item for item in flow["funding_os_roles"])
    assert any("Ads Watching Agent" in item for item in flow["funding_os_roles"])
    assert any("Earnings Agent" in item for item in flow["funding_os_roles"])
    assert any("OTP/TOTP" in item for item in flow["voice_mode"])
    assert any("do not execute payment steps" in item for item in flow["voice_mode"])
    assert any("does not take over provider sessions" in item for item in flow["connected_accounts"])
    assert any("explicit user permission" in item for item in flow["connected_accounts"])
    assert guardrails["official_source"] == "Funding OS manifest at app.autoyou.me is the source of truth."
    assert "private keys" in guardrails["never_collect"]
    assert any("MoonPay/on-ramp" in item for item in guardrails["crypto"])
    assert any("cannot be swapped to USDC" in item for item in guardrails["crypto"])


def test_donation_agent_is_builtin_and_installed_by_default():
    assert DEFAULT_AGENT_INSTALL_STATES["donation_agent"] is True


def test_donation_agent_ui_status_and_plan(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    monkeypatch.setattr(
        donation_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {"authenticated": False, "agent_name": agent_name},
    )
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "https://www.autoyou.test")
    monkeypatch.setenv("AUTOYOU_STRIPE_DONATION_URL", "https://pay.autoyou.test/donate")

    client = TestClient(donation_ui_backend.app)
    status_response = client.get("/api/status")
    plan_response = client.get("/api/plan")

    assert status_response.status_code == 200
    status = status_response.json()
    assert status["success"] is True
    assert status["account"]["dashboard_url"] == "https://app.autoyou.test/dashboard?section=funding"
    assert status["account"]["funding_manifest_url"] == "https://app.autoyou.test/v1/funding/manifest"
    assert status["account"]["public_ledger_url"] == "https://app.autoyou.test/v1/funding/public-ledger"
    assert status["website"]["donate_url"] == "https://www.autoyou.test/donate/"
    assert status["status"]["app_auth"] == "oauth_only_no_email_signup"
    assert status["status"]["funding_rails"][1]["id"] == "autoyou-operating-income"
    assert status["status"]["permission_model"]["payment_execution"] == "external_provider_checkout_only"
    assert status["status"]["permission_model"]["connected_account_modes"]["server_context"]["auth"] == (
        "user_permission_required"
    )
    assert status["status"]["channels"][0]["status"] == "active"

    assert plan_response.status_code == 200
    plan = plan_response.json()
    assert plan["success"] is True
    assert any("public donate page" in item for item in plan["flow"]["supporter_flow"])
    assert "card numbers" in plan["guardrails"]["never_collect"]


def test_donation_agent_ui_sanitizes_unsafe_base_urls(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    monkeypatch.setattr(
        donation_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {"authenticated": False, "agent_name": agent_name},
    )
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://operator:secret@app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "file:///tmp/not-official")

    client = TestClient(donation_ui_backend.app)
    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload)
    assert payload["account"]["dashboard_url"] == "https://app.autoyou.me/dashboard?section=funding"
    assert payload["account"]["public_ledger_url"] == "https://app.autoyou.me/v1/funding/public-ledger"
    assert payload["website"]["donate_url"] == "https://www.autoyou.me/donate/"
    assert "operator:secret" not in serialized
    assert "file:///tmp/not-official" not in serialized


def test_donation_agent_ui_rejects_remote_http_base_urls(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    monkeypatch.setattr(
        donation_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {"authenticated": False, "agent_name": agent_name},
    )
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "http://app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "http://www.autoyou.test")

    client = TestClient(donation_ui_backend.app)
    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["account"]["dashboard_url"] == "https://app.autoyou.me/dashboard?section=funding"
    assert payload["account"]["public_ledger_url"] == "https://app.autoyou.me/v1/funding/public-ledger"
    assert payload["website"]["donate_url"] == "https://www.autoyou.me/donate/"


def test_donation_agent_ui_public_ledger_proxy_returns_snapshot(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    async def fake_get_public_ledger_snapshot(account_url):
        assert account_url == "https://app.autoyou.test"
        return {
            "ledger": {
                "summary": {
                    "available_pool_usd": 25,
                    "total_received_usd": 50,
                    "total_expenses_usd": 10,
                    "pending_contributor_requests_usd": 15,
                },
                "ledger_entries": [
                    {
                        "entry_type": "money_source",
                        "label": "Buy Me a Coffee import",
                        "status": "imported",
                        "amount_usd": 50,
                        "pool_effect_usd": 50,
                        "public_url": "https://buymeacoffee.com/autoyou/posts/synthetic-june",
                    }
                ],
                "ledger_totals_by_scope": [
                    {
                        "scope": "donation_pool",
                        "label": "Donation pool",
                        "entry_count": 1,
                        "pool_effect_usd": 50,
                        "inflow_usd": 50,
                    },
                    {
                        "scope": "operating_expense",
                        "label": "Operating expenses",
                        "entry_count": 1,
                        "pool_effect_usd": -10,
                        "outflow_usd": 10,
                    },
                ],
            },
            "cache": {"status": "fresh", "etag": "synthetic-etag", "ttl_seconds": 30},
        }

    async def fake_get_funding_manifest_snapshot(account_url):
        assert account_url == "https://app.autoyou.test"
        return {
            "manifest": {
                "public_url_policy": {
                    "accepted_public_schemes": ["https"],
                    "development_http_hosts": ["localhost", "127.0.0.1", "::1", "*.localhost"],
                    "rejected_public_url_features": ["remote_http", "url_userinfo"],
                },
                "request_contracts": {
                    "contributor_request": {
                        "amount_modes": ["requested_percent", "fixed_usd"],
                        "mutually_exclusive_fields": [["requestedPercent", "requestedAmountUsd"]],
                        "fixed_amount": {
                            "wire_field": "requestedAmountUsd",
                            "currency": "USD",
                            "maximum_source": "public_ledger.summary.available_pool_usd",
                        },
                    },
                    "sov_settlement_request": {
                        "amount_modes": ["fixed_amount", "all_available"],
                        "mutually_exclusive_fields": [["amountCredits", "allAvailable=true"]],
                    },
                }
            },
            "cache": {"status": "fresh", "etag": "synthetic-manifest-etag", "ttl_seconds": 30},
        }

    monkeypatch.setattr(
        donation_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {"authenticated": False, "agent_name": agent_name},
    )
    monkeypatch.setattr(donation_ui_backend, "_get_public_ledger_snapshot", fake_get_public_ledger_snapshot)
    monkeypatch.setattr(donation_ui_backend, "_get_funding_manifest_snapshot", fake_get_funding_manifest_snapshot)
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")

    client = TestClient(donation_ui_backend.app)
    response = client.get("/api/public-ledger")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["public_ledger_url"] == "https://app.autoyou.test/v1/funding/public-ledger"
    assert payload["funding_manifest_url"] == "https://app.autoyou.test/v1/funding/manifest"
    assert payload["ledger"]["summary"]["available_pool_usd"] == 25
    assert payload["ledger"]["ledger_entries"][0]["label"] == "Buy Me a Coffee import"
    assert payload["ledger"]["ledger_totals_by_scope"][0]["scope"] == "donation_pool"
    assert payload["ledger"]["ledger_totals_by_scope"][1]["pool_effect_usd"] == -10
    assert payload["public_url_policy"]["accepted_public_schemes"] == ["https"]
    assert "remote_http" in payload["public_url_policy"]["rejected_public_url_features"]
    assert payload["request_contracts"]["contributor_request"]["amount_modes"] == [
        "requested_percent",
        "fixed_usd",
    ]
    assert (
        payload["request_contracts"]["contributor_request"]["fixed_amount"]["maximum_source"]
        == "public_ledger.summary.available_pool_usd"
    )
    assert ["requestedPercent", "requestedAmountUsd"] in payload["request_contracts"]["contributor_request"][
        "mutually_exclusive_fields"
    ]
    assert ["amountCredits", "allAvailable=true"] in payload["request_contracts"]["sov_settlement_request"][
        "mutually_exclusive_fields"
    ]
    assert payload["cache"]["status"] == "fresh"
    assert payload["manifest_cache"]["status"] == "fresh"


def test_donation_agent_ui_public_ledger_proxy_degrades_without_breaking(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    async def fake_get_public_ledger_snapshot(account_url):
        raise RuntimeError("synthetic ledger outage")

    monkeypatch.setattr(
        donation_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {"authenticated": False, "agent_name": agent_name},
    )
    monkeypatch.setattr(donation_ui_backend, "_get_public_ledger_snapshot", fake_get_public_ledger_snapshot)
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")

    client = TestClient(donation_ui_backend.app)
    response = client.get("/api/public-ledger")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["public_ledger_url"] == "https://app.autoyou.test/v1/funding/public-ledger"
    assert payload["funding_manifest_url"] == "https://app.autoyou.test/v1/funding/manifest"
    assert payload["ledger"] == {}
    assert payload["public_url_policy"] == {}
    assert payload["request_contracts"] == {}
    assert payload["cache"]["status"] == "unavailable"
    assert payload["manifest_cache"]["status"] == "unavailable"
    assert "unavailable" in payload["error"].lower()


def test_donation_agent_public_ledger_snapshot_uses_ttl_cache(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    calls = []

    async def fake_fetch_public_ledger(account_url, *, etag=""):
        calls.append((account_url, etag))
        return 200, "synthetic-etag-1", {"summary": {"available_pool_usd": 25}}

    monkeypatch.setattr(donation_ui_backend, "_fetch_public_ledger", fake_fetch_public_ledger)
    monkeypatch.setenv("AUTOYOU_DONATION_AGENT_LEDGER_CACHE_SECONDS", "60")
    donation_ui_backend._PUBLIC_LEDGER_CACHE.clear()

    first = asyncio.run(donation_ui_backend._get_public_ledger_snapshot("https://app.autoyou.test"))
    second = asyncio.run(donation_ui_backend._get_public_ledger_snapshot("https://app.autoyou.test"))

    assert first["cache"]["status"] == "fresh"
    assert second["cache"]["status"] == "hit"
    assert second["ledger"]["summary"]["available_pool_usd"] == 25
    assert calls == [("https://app.autoyou.test", "")]


def test_donation_agent_public_ledger_snapshot_uses_conditional_refresh(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    calls = []

    async def fake_fetch_public_ledger(account_url, *, etag=""):
        calls.append((account_url, etag))
        if len(calls) == 1:
            return 200, "synthetic-etag-1", {"summary": {"available_pool_usd": 25}}
        return 304, "synthetic-etag-1", None

    monkeypatch.setattr(donation_ui_backend, "_fetch_public_ledger", fake_fetch_public_ledger)
    monkeypatch.setenv("AUTOYOU_DONATION_AGENT_LEDGER_CACHE_SECONDS", "0")
    donation_ui_backend._PUBLIC_LEDGER_CACHE.clear()

    first = asyncio.run(donation_ui_backend._get_public_ledger_snapshot("https://app.autoyou.test"))
    second = asyncio.run(donation_ui_backend._get_public_ledger_snapshot("https://app.autoyou.test"))

    assert first["cache"]["status"] == "fresh"
    assert second["cache"]["status"] == "not_modified"
    assert second["ledger"]["summary"]["available_pool_usd"] == 25
    assert calls == [
        ("https://app.autoyou.test", ""),
        ("https://app.autoyou.test", "synthetic-etag-1"),
    ]


def test_donation_agent_ui_prepares_handoff(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    monkeypatch.setattr(
        donation_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {"authenticated": False, "agent_name": agent_name},
    )
    monkeypatch.setenv("AUTOYOU_BUYMEACOFFEE_URL", "https://coffee.autoyou.test/support")

    client = TestClient(donation_ui_backend.app)
    response = client.get("/api/handoff", params={"channel": "buymeacoffee", "context": "voice"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["handoff"]["success"] is True
    assert payload["handoff"]["handoff_url"] == "https://coffee.autoyou.test/support"
    assert payload["handoff"]["context"]["id"] == "voice"
    assert payload["handoff"]["credential_collection"] == "disabled"
    assert payload["handoff"]["voice_mode"]["local_otp_scope"] == "agent_ui_only_not_payment_execution"
    assert payload["handoff"]["voice_mode"]["official_host"] == "coffee.autoyou.test"


def test_donation_agent_ui_requires_local_auth_for_connected_handoff(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    monkeypatch.setattr(
        donation_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {
            "authenticated": False,
            "required": True,
            "via": "none",
            "agent_name": agent_name,
        },
    )
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")

    client = TestClient(donation_ui_backend.app)
    locked = client.get("/api/handoff", params={"channel": "funding-dashboard", "context": "account_dashboard"})

    assert locked.status_code == 401
    locked_payload = locked.json()
    assert locked_payload["success"] is False
    assert locked_payload["handoff"]["status"] == "local_auth_required"
    assert locked_payload["handoff"]["requested_channel"] == "funding-dashboard"

    monkeypatch.setattr(
        donation_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {
            "authenticated": True,
            "required": True,
            "via": "chat_session",
            "agent_name": agent_name,
        },
    )
    unlocked = client.get("/api/handoff", params={"channel": "funding-dashboard", "context": "account_dashboard"})

    assert unlocked.status_code == 200
    unlocked_payload = unlocked.json()
    assert unlocked_payload["success"] is True
    assert unlocked_payload["handoff"]["handoff_url"] == "https://app.autoyou.test/dashboard?section=funding"
    assert unlocked_payload["handoff"]["context"]["id"] == "account_dashboard"


def test_donation_agent_ui_requires_explicit_permission_for_connected_context(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    monkeypatch.setattr(
        donation_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {
            "authenticated": True,
            "required": True,
            "via": "chat_session",
            "agent_name": agent_name,
        },
    )
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "https://www.autoyou.test")

    client = TestClient(donation_ui_backend.app)
    needs_permission = client.get(
        "/api/handoff",
        params={"channel": "public-donate", "context": "client_context"},
    )
    allowed = client.get(
        "/api/handoff",
        params={
            "channel": "public-donate",
            "context": "client_context",
            "permissionAcknowledged": "true",
        },
    )

    assert needs_permission.status_code == 200
    needs_payload = needs_permission.json()
    assert needs_payload["success"] is True
    assert needs_payload["handoff"]["success"] is False
    assert needs_payload["handoff"]["status"] == "user_permission_required"
    assert needs_payload["handoff"]["handoff_url"] == ""

    assert allowed.status_code == 200
    allowed_payload = allowed.json()
    assert allowed_payload["success"] is True
    assert allowed_payload["handoff"]["success"] is True
    assert allowed_payload["handoff"]["handoff_url"] == "https://www.autoyou.test/donate/"
    assert allowed_payload["handoff"]["permission_acknowledged"] is True


def test_donation_agent_ui_auth_status_exposes_totp_state(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    monkeypatch.setattr(
        donation_ui_backend._smc,
        "_describe_chat_auth_state",
        lambda request, agent_name: {
            "authenticated": False,
            "required": True,
            "via": "none",
            "agent_name": agent_name,
        },
    )
    monkeypatch.setattr(
        donation_ui_backend._smc,
        "_totp_capabilities",
        lambda: {"totp_configured": True, "issuer": "AutoYou"},
    )

    client = TestClient(donation_ui_backend.app)
    response = client.get("/api/auth/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["auth"]["authenticated"] is False
    assert payload["totp"]["totp_configured"] is True


def test_donation_agent_ui_auth_login_issues_test_scoped_token(monkeypatch, tmp_path):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    # donation_agent is "open" by manifest default (see
    # test_donation_agent_is_open_by_manifest_default_with_no_agent_ui_security_entry);
    # explicitly re-gate it here via the admin config override to exercise the
    # TOTP login path itself, proving the override still works for a
    # manifest-open agent.
    sessions_path = tmp_path / "agent_ui_sessions.json"
    mock_server = SimpleNamespace(
        STATE=SimpleNamespace(config={"agent_ui_security": {"donation_agent": {"auth_mode": "totp"}}}),
        _default_config=lambda: {},
        _is_logged_in=lambda request: False,
        _get_pairing_totp_secret=lambda cfg: "TESTSECRET",
        _verify_totp_secret=lambda secret, code: secret == "TESTSECRET" and code == "123456",
        agent_has_assigned_2fa_profile=lambda _agent: False,
    )
    monkeypatch.setattr(donation_ui_backend._smc, "_AGENT_UI_SESSIONS_FILE", str(sessions_path))
    donation_ui_backend._smc._AGENT_UI_SESSIONS.clear()
    monkeypatch.setattr(donation_ui_backend._smc, "_runtime_server", lambda: mock_server)
    monkeypatch.setattr(
        donation_ui_backend._smc,
        "_totp_capabilities",
        lambda: {"totp_configured": True, "issuer": "AutoYou"},
    )

    client = TestClient(donation_ui_backend.app)
    login = client.post("/api/auth/login", json={"code": "123456"})

    assert login.status_code == 200
    token = login.json()["token"]
    assert token
    assert sessions_path.exists()
    assert sessions_path.is_relative_to(tmp_path)

    status = client.get("/api/auth/status", headers={"Authorization": f"Bearer {token}"})
    assert status.status_code == 200
    assert status.json()["auth"]["authenticated"] is True
    assert status.json()["auth"]["via"] == "chat_session"


def test_donation_agent_ui_auth_login_rejects_missing_totp(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    # Explicitly re-gate donation_agent (see the manifest-default-open test
    # below) so this test still exercises the TOTP-required rejection path.
    mock_server = SimpleNamespace(
        STATE=SimpleNamespace(config={"agent_ui_security": {"donation_agent": {"auth_mode": "totp"}}}),
        _default_config=lambda: {},
        _is_logged_in=lambda request: False,
        agent_has_assigned_2fa_profile=lambda _agent: False,
    )
    monkeypatch.setattr(donation_ui_backend._smc, "_runtime_server", lambda: mock_server)
    monkeypatch.setattr(
        donation_ui_backend._smc,
        "_totp_capabilities",
        lambda: {"totp_configured": False},
    )

    client = TestClient(donation_ui_backend.app)
    response = client.post("/api/auth/login", json={"code": "123456"})

    assert response.status_code == 400
    assert "not configured" in response.json()["error"].lower()


def test_donation_agent_is_open_by_manifest_default_with_no_agent_ui_security_entry(monkeypatch):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    mock_server = SimpleNamespace(
        STATE=SimpleNamespace(config={"security": {"totp_secret": "TESTSECRET"}, "agent_websites": {"require_otp": False}}),
        _default_config=lambda: {},
        _is_logged_in=lambda request: False,
        agent_has_assigned_2fa_profile=lambda _agent: False,
    )
    monkeypatch.setattr(donation_ui_backend._smc, "_runtime_server", lambda: mock_server)
    monkeypatch.setattr(
        donation_ui_backend._smc,
        "_totp_capabilities",
        lambda: {"totp_configured": True, "issuer": "AutoYou"},
    )

    client = TestClient(donation_ui_backend.app)
    status = client.get("/api/auth/status")

    assert status.status_code == 200
    auth = status.json()["auth"]
    assert auth["authenticated"] is True
    assert auth["via"] == "open"
    assert auth["required"] is False


def test_donation_agent_frontend_renders_public_support_page():
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    html = donation_ui_backend._FRONTEND_DIR.joinpath("index.html").read_text(encoding="utf-8")
    js = donation_ui_backend._FRONTEND_DIR.joinpath("assets", "app.js").read_text(encoding="utf-8")

    # The public support page is intentionally OTP-free and fluff-free.
    assert 'id="auth-form"' not in html
    assert 'autocomplete="one-time-code"' not in html
    assert "./api/auth/login" not in js

    # One cache-friendly fetch drives the whole page.
    assert "./api/donation-links" in js
    assert js.count("fetch(") == 1

    # Crypto cards render operator-published addresses with copy support.
    assert 'id="crypto-list"' in html
    assert 'id="crypto-pending"' in html
    assert 'id="crypto-route-select"' in html
    assert 'id="crypto-route-qr"' in html
    assert "data-copy" in js
    assert "Copy Address" in js
    assert "./api/crypto-qr" in js
    assert "api.qrserver.com" not in js
    assert "seed phrase" in html

    # Providers, socials, and share support render when configured.
    assert 'id="provider-list"' in html
    assert 'id="social-list"' in html
    assert 'id="share-btn"' in html
    assert "navigator.share" in js
    assert "rel=\\\"noopener noreferrer\\\"" in js or 'rel="noopener noreferrer"' in js


def test_donation_agent_crypto_qr_route_renders_local_png():
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    client = TestClient(donation_ui_backend.app)
    response = client.get(
        "/api/crypto-qr",
        params={"address": "bc1qsyntheticbitcoinaddress00000000000000"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get("/api/crypto-qr", params={"address": "not a valid address"}).status_code == 400


def test_donation_links_sanitizes_operator_config(monkeypatch, tmp_path):
    from autoyou_agents.donation_agent import donation_links

    config_path = tmp_path / "donations.json"
    config_path.write_text(
        json.dumps(
            {
                "project_name": "AutoYou",
                "crypto": [
                    {
                        "id": "btc",
                        "name": "Bitcoin",
                        "network": "Bitcoin Network",
                        "symbol": "btc",
                        "address": "bc1qsyntheticbitcoinaddress00000000000000",
                        "tokens": ["BTC"],
                        "accent": "#f7931a",
                        "memo": "12345678",
                        "deposit_url": "https://example.test/deposit/xrp",
                        "note": "Use the destination tag.",
                    },
                    {
                        "id": "xrp",
                        "name": "XRP",
                        "network": "XRP Ledger",
                        "symbol": "xrp",
                        "address": "rSyntheticXRPAddress1234567890",
                        "tokens": ["XRP"],
                        "memo": "12345678",
                    },
                    {
                        "id": "bad",
                        "name": "Bad",
                        "network": "Bad Network",
                        "symbol": "BAD",
                        "address": "not a real address!!",
                        "tokens": [],
                        "accent": "javascript:alert(1)",
                    },
                ],
                "providers": [
                    {"id": "buymeacoffee", "label": "Buy Me a Coffee", "url": "https://buymeacoffee.com/autoyou"},
                    {"id": "evil", "label": "Evil", "url": "javascript:alert(1)"},
                    {"id": "userinfo", "label": "Userinfo", "url": "https://token@example.test/pay"},
                    {"id": "remote-http", "label": "Remote HTTP", "url": "http://pay.example.test/donate"},
                ],
                "socials": [
                    {"id": "github", "label": "GitHub", "url": "https://github.com/autoyou-ai", "handle": "autoyou-ai"},
                    {"id": "bad", "label": "Bad", "url": "ftp://example.test"},
                ],
                "share_url": "https://www.autoyou.me",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOYOU_DONATIONS_CONFIG_PATH", str(config_path))

    links = donation_links.get_public_donation_links(force_reload=True)

    crypto_by_id = {entry["id"]: entry for entry in links["crypto"]}
    assert crypto_by_id["btc"]["configured"] is True
    assert crypto_by_id["btc"]["address"].startswith("bc1q")
    assert crypto_by_id["btc"]["symbol"] == "BTC"
    assert "memo" not in crypto_by_id["btc"]
    assert crypto_by_id["btc"]["deposit_url"] == "https://example.test/deposit/xrp"
    assert crypto_by_id["btc"]["note"] == "Use the destination tag."
    assert crypto_by_id["xrp"]["memo"] == "12345678"
    assert crypto_by_id["bad"]["configured"] is False
    assert crypto_by_id["bad"]["address"] == ""
    assert crypto_by_id["bad"]["accent"] == ""
    assert links["crypto_configured"] is True

    provider_ids = [entry["id"] for entry in links["providers"]]
    assert provider_ids == ["buymeacoffee"]

    social_ids = [entry["id"] for entry in links["socials"]]
    assert social_ids == ["github"]


def test_donation_links_defaults_without_config(monkeypatch, tmp_path):
    from autoyou_agents.donation_agent import donation_links

    monkeypatch.setenv("AUTOYOU_DONATIONS_CONFIG_PATH", str(tmp_path / "missing.json"))

    links = donation_links.get_public_donation_links(force_reload=True)

    assert links["crypto_configured"] is False
    assert all(entry["configured"] is False for entry in links["crypto"])
    assert any(entry["id"] == "buymeacoffee" for entry in links["providers"])
    assert links["project_name"] == "AutoYou"


def test_donation_agent_ui_donation_links_route_is_public(monkeypatch, tmp_path):
    from autoyou_agents.donation_agent import donation_links
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    config_path = tmp_path / "donations.json"
    config_path.write_text(
        json.dumps(
            {
                "crypto": [
                    {
                        "id": "trc20",
                        "name": "TRC20",
                        "network": "Tron Network",
                        "symbol": "TRX",
                        "address": "TVsyntheticTronAddress000000000000",
                        "tokens": ["TRX", "USDT"],
                        "accent": "#ff5b5b",
                    }
                ],
                "providers": [],
                "socials": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOYOU_DONATIONS_CONFIG_PATH", str(config_path))
    donation_links.get_public_donation_links(force_reload=True)
    monkeypatch.setattr(
        donation_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {
            "authenticated": False,
            "required": True,
            "via": "none",
            "agent_name": agent_name,
        },
    )

    client = TestClient(donation_ui_backend.app)
    response = client.get("/api/donation-links")

    assert response.status_code == 200
    assert "max-age" in response.headers.get("Cache-Control", "")
    payload = response.json()
    assert payload["success"] is True
    crypto = payload["links"]["crypto"]
    assert crypto[0]["id"] == "trc20"
    assert crypto[0]["address"].startswith("TV")
    # Env-configured official channels merge in behind config-file providers.
    provider_ids = [entry["id"] for entry in payload["links"]["providers"]]
    assert "buymeacoffee" in provider_ids
    # The public payload never includes local paths or private fields.
    text = response.text.lower()
    assert "private" not in text
    assert "seed" not in text
    assert "c:\\" not in text
