# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-304232623937526530302920-74f242b8c540df0da138687e


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-304232623937526530302920-74f242b8c540df0da138687e"

import json

from fastapi.testclient import TestClient
from types import SimpleNamespace
from unittest.mock import MagicMock

from autoyou_agents.earnings_agent.agent import (
    describe_funding_os_path,
    describe_usdc_readiness,
    get_earnings_agent_status,
    prepare_earnings_action,
)
from autoyou_agents.shared_tools.agent_install_registry import DEFAULT_AGENT_INSTALL_STATES


def test_earnings_agent_status_links_to_funding_os(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "https://www.autoyou.test")
    monkeypatch.setenv("AUTOYOU_CREDITS_LEDGER_ENABLED", "true")
    monkeypatch.setenv("AUTOYOU_FUNDING_CONTRIBUTOR_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("AUTOYOU_SOV_WALLET_REGISTRY_ENABLED", "true")
    monkeypatch.setenv("AUTOYOU_SOV_WALLET_PROVISIONING_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("AUTOYOU_SOV_SETTLEMENT_REQUESTS_ENABLED", "true")

    payload = get_earnings_agent_status()

    assert payload["account_dashboard"] == "https://app.autoyou.test/dashboard?section=funding"
    assert payload["public_donate_page"] == "https://www.autoyou.test/donate/"
    assert payload["public_ledger"] == "https://app.autoyou.test/v1/funding/public-ledger"
    assert payload["credits_endpoint"] == "https://app.autoyou.test/v1/funding/credits"
    assert payload["sov_status_endpoint"] == "https://app.autoyou.test/v1/funding/sov/status"
    assert payload["sov_wallet_endpoint"] == "https://app.autoyou.test/v1/funding/sov/wallet"
    assert payload["sov_wallet_provisioning_requests_endpoint"] == "https://app.autoyou.test/v1/funding/sov/wallet-provisioning-requests"
    assert payload["sov_settlement_requests_endpoint"] == "https://app.autoyou.test/v1/funding/sov/settlement-requests"
    assert payload["admin_records_endpoint"] == "https://app.autoyou.test/v1/funding/admin/records"
    assert payload["admin_money_sources_endpoint"] == "https://app.autoyou.test/v1/funding/admin/money-sources"
    assert payload["admin_expenses_endpoint"] == "https://app.autoyou.test/v1/funding/admin/expenses"
    assert payload["admin_contributor_requests_endpoint"] == "https://app.autoyou.test/v1/funding/admin/contributor-requests"
    assert payload["runtime_flags"]["credits_ledger"] == "enabled"
    assert payload["runtime_flags"]["contributor_requests"] == "enabled"
    assert payload["runtime_flags"]["sov_wallet_registry"] == "enabled"
    assert payload["runtime_flags"]["sov_wallet_provisioning_requests"] == "enabled"
    assert payload["runtime_flags"]["sov_settlement_requests"] == "enabled"
    assert payload["transferable"] is False
    assert payload["sov"]["token_symbol"] == "USDC"
    assert payload["sov"]["wallet_registry"] == "enabled"
    assert payload["sov"]["wallet_provisioning_requests"] == "enabled"
    assert payload["sov"]["settlement"] == "request_records_enabled_no_transfer"
    assert payload["sov"]["future_scope"]["coins_to_usdc_swap"] == "planned"
    assert payload["sov"]["future_scope"]["usdc_transfer_out_with_kyc"] == "planned"
    assert payload["usdc"]["token_symbol"] == "USDC"
    assert payload["usdc"]["future_scope"]["credits_monitoring_only"] == "current"
    assert (
        payload["sov"]["settlement_accounting"]["available_request_credits_field"]
        == "settlement_accounting.available_request_credits"
    )
    assert (
        payload["sov"]["settlement_accounting"]["committed_request_credits_field"]
        == "settlement_accounting.committed_request_credits"
    )
    assert "settlement_request_records[].settlement_outcome" in payload["sov"]["settlement_accounting"]["settlement_outcome_fields"]
    assert "without debiting credit_balance" in payload["sov"]["settlement_accounting"]["capacity_rule"]
    assert payload["safety_limits"]["sov_outcome_recording"] == "account_admin_external_artifacts_only"
    assert payload["safety_limits"]["can_record_sov_outcome"] is False


def test_earnings_agent_sanitizes_unsafe_base_urls(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://operator:secret@app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "javascript:alert(1)")

    status = get_earnings_agent_status()
    action = prepare_earnings_action("funding-dashboard")
    serialized = json.dumps({"status": status, "action": action})

    assert status["account_dashboard"] == "https://app.autoyou.me/dashboard?section=funding"
    assert status["public_donate_page"] == "https://www.autoyou.me/donate/"
    assert status["public_ledger"] == "https://app.autoyou.me/v1/funding/public-ledger"
    assert action["api_url"] == "https://app.autoyou.me/v1/funding/manifest"
    assert action["handoff_url"] == "https://app.autoyou.me/dashboard?section=funding"
    assert "operator:secret" not in serialized
    assert "javascript:" not in serialized


def test_earnings_agent_rejects_remote_http_base_urls(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "http://app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "http://www.autoyou.test")

    status = get_earnings_agent_status()
    action = prepare_earnings_action("funding-dashboard")

    assert status["account_dashboard"] == "https://app.autoyou.me/dashboard?section=funding"
    assert status["public_donate_page"] == "https://www.autoyou.me/donate/"
    assert action["handoff_url"] == "https://app.autoyou.me/dashboard?section=funding"


def test_earnings_agent_allows_local_http_base_urls(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "http://localhost:8060")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "http://127.0.0.1:8050")

    status = get_earnings_agent_status()
    action = prepare_earnings_action("funding-dashboard")

    assert status["account_dashboard"] == "http://localhost:8060/dashboard?section=funding"
    assert status["public_donate_page"] == "http://127.0.0.1:8050/donate/"
    assert action["handoff_url"] == "http://localhost:8060/dashboard?section=funding"


def test_earnings_agent_describes_separate_funding_and_sov_tracks():
    funding = describe_funding_os_path()
    sov = describe_usdc_readiness()

    assert "donations" in funding
    assert "contributors" in funding
    assert "earnings" in funding
    assert any("Ads Watching default native ads" in item for item in funding["earnings"])
    assert any("non-transferable" in item for item in funding["earnings"])
    assert sov["network"] == "The USDC settlement"
    assert sov["token_symbol"] == "USDC"
    assert sov["credit_settlement"] == "disabled_until_compliance"
    assert sov["future_scope"]["coins_to_usdc_swap"] == "planned"
    assert sov["future_scope"]["usdc_transfer_out_with_kyc"] == "planned"
    assert "GET /v1/funding/sov/status" in sov["implemented_routes"]
    assert "POST /v1/funding/sov/wallet-provisioning-requests" in sov["implemented_routes"]
    assert "POST /v1/funding/admin/sov/settlement-requests/{request_id}/outcome" in sov["implemented_routes"]
    assert "settlement_accounting" in sov["authenticated_status_fields"]
    assert "settlement_request_records[].settlement_outcome" in sov["authenticated_status_fields"]
    assert "sov_ledger_entries" in sov["authenticated_status_fields"]
    assert any("never server-generated keys" in item for item in sov["implemented_route_limits"])
    assert any("available_request_credits" in item for item in sov["implemented_route_limits"])
    assert any("does not debit credits or transfer USDC" in item for item in sov["implemented_route_limits"])
    assert any("rejects transaction hashes" in item for item in sov["implemented_route_limits"])
    assert "synthetic end-to-end tests with no live wallet mutation" in sov["required_gates"]


def test_earnings_action_prepares_sov_wallet_reference_when_enabled(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("AUTOYOU_SOV_WALLET_REGISTRY_ENABLED", "true")

    payload = prepare_earnings_action("sov-wallet")

    assert payload["success"] is True
    assert payload["ready"] is True
    assert payload["action"] == "register-wallet"
    assert payload["method"] == "POST"
    assert payload["api_url"] == "https://app.autoyou.test/v1/funding/sov/wallet"
    assert payload["handoff_url"] == "https://app.autoyou.test/dashboard?section=funding"
    assert payload["requires_oauth"] is True
    assert payload["runtime_flags"]["sov_wallet_registry"] == "enabled"
    assert payload["safety_limits"]["local_agent_session_scope"] == "ui_only_not_account_service_bearer"
    assert payload["safety_limits"]["can_create_wallet_keys"] is False
    assert "publicAddress" in payload["request_fields"]
    assert "moonpayCustomerId" in payload["forbidden_fields"]
    assert "privateKey" in payload["forbidden_fields"]
    assert any("provider customer ids" in item for item in payload["instructions"])
    assert any("does not generate or custody wallet keys" in item for item in payload["instructions"])


def test_earnings_action_prepares_provider_wallet_provisioning_when_enabled(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("AUTOYOU_SOV_WALLET_PROVISIONING_REQUESTS_ENABLED", "true")

    payload = prepare_earnings_action("generate-wallet")

    assert payload["success"] is True
    assert payload["ready"] is True
    assert payload["action"] == "provision-wallet"
    assert payload["method"] == "POST"
    assert payload["api_url"] == "https://app.autoyou.test/v1/funding/sov/wallet-provisioning-requests"
    assert payload["runtime_flags"]["sov_wallet_provisioning_requests"] == "enabled"
    assert "provider" in payload["request_fields"]
    assert "moonpayCustomerId" in payload["forbidden_fields"]
    assert "privateKey" in payload["forbidden_fields"]
    assert payload["safety_limits"]["can_create_wallet_keys"] is False
    assert any("does not generate, custody, or store wallet private keys" in item for item in payload["instructions"])


def test_earnings_action_requires_credits_for_sov_settlement(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("AUTOYOU_SOV_SETTLEMENT_REQUESTS_ENABLED", "true")
    monkeypatch.delenv("AUTOYOU_CREDITS_LEDGER_ENABLED", raising=False)

    payload = prepare_earnings_action("settlement")

    assert payload["success"] is False
    assert payload["ready"] is False
    assert payload["status"] == "setup_required"
    assert payload["action"] == "request-settlement"
    assert payload["runtime_flags"]["sov_settlement_requests"] == "disabled"
    assert payload["safety_limits"]["can_debit_credits"] is False
    assert payload["safety_limits"]["can_transfer_sov"] is False
    assert any("does not debit credits" in item for item in payload["instructions"])


def test_earnings_action_prepares_settlement_record_when_gated_flags_enabled(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("AUTOYOU_CREDITS_LEDGER_ENABLED", "true")
    monkeypatch.setenv("AUTOYOU_SOV_SETTLEMENT_REQUESTS_ENABLED", "true")

    payload = prepare_earnings_action("request-settlement")
    one_shot_payload = prepare_earnings_action("all-credits-to-sov")

    assert payload["success"] is True
    assert payload["api_url"] == "https://app.autoyou.test/v1/funding/sov/settlement-requests"
    assert payload["runtime_flags"]["credits_ledger"] == "enabled"
    assert payload["runtime_flags"]["sov_settlement_requests"] == "enabled"
    assert "amountCredits" in payload["request_fields"]
    assert "allAvailable" in payload["request_fields"]
    assert "walletId" in payload["request_fields"]
    assert "publicArtifacts" in payload["request_fields"]
    assert "note" in payload["request_fields"]
    assert payload["amount_modes"] == ["fixed_amount", "all_available"]
    assert ["amountCredits", "allAvailable=true"] in payload["mutually_exclusive_fields"]
    assert "publicNote" not in payload["request_fields"]
    assert "registered user-owned USDC wallet reference" in payload["prerequisites"]
    assert "settlement_accounting.available_request_credits" in payload["status_fields"]
    assert "settlement_accounting.committed_request_credits" in payload["status_fields"]
    assert "settlement_request_records[].settlement_outcome" in payload["status_fields"]
    assert any("available settlement request capacity" in item for item in payload["prerequisites"])
    assert any("allAvailable=true" in item for item in payload["instructions"])
    assert any("available_request_credits" in item for item in payload["instructions"])
    assert any("external public artifacts" in item for item in payload["instructions"])
    assert any("public review artifacts" in item for item in payload["instructions"])
    assert one_shot_payload["success"] is True
    assert one_shot_payload["action"] == "request-settlement"
    assert one_shot_payload["label"] == "Create one-shot all-available USDC settlement request record"
    assert one_shot_payload["request_mode"] == "all_available"
    assert one_shot_payload["recommended_request_body"]["allAvailable"] is True
    assert one_shot_payload["recommended_request_body"]["amountCredits"] == 0
    assert "allAvailable" in one_shot_payload["request_fields"]
    assert any("server-side" in item for item in one_shot_payload["instructions"])


def test_earnings_action_sanitizes_untrusted_moonpay_url(monkeypatch):
    monkeypatch.setenv("AUTOYOU_MOONPAY_ONRAMP_URL", "javascript:alert(1)")

    payload = prepare_earnings_action("moonpay")

    assert payload["success"] is False
    assert payload["status"] == "setup_required"
    assert payload["handoff_url"] == ""
    assert payload["runtime_flags"]["moonpay_onramp"] == "provider_review_required"
    assert payload["safety_limits"]["payment_execution"] == "disabled"

    monkeypatch.setenv("AUTOYOU_MOONPAY_ONRAMP_URL", "https://token@example.test/onramp")
    userinfo_payload = prepare_earnings_action("moonpay")

    assert userinfo_payload["success"] is False
    assert userinfo_payload["handoff_url"] == ""


def test_earnings_action_rejects_remote_http_moonpay_url(monkeypatch):
    monkeypatch.setenv("AUTOYOU_MOONPAY_ONRAMP_URL", "http://moonpay.autoyou.test/onramp")

    payload = prepare_earnings_action("moonpay")

    assert payload["success"] is False
    assert payload["status"] == "setup_required"
    assert payload["handoff_url"] == ""
    assert payload["runtime_flags"]["moonpay_onramp"] == "provider_review_required"


def test_earnings_action_allows_local_http_moonpay_development_url(monkeypatch):
    monkeypatch.setenv("AUTOYOU_MOONPAY_ONRAMP_URL", "http://localhost:8060/onramp")

    payload = prepare_earnings_action("moonpay")

    assert payload["success"] is True
    assert payload["status"] == "ready"
    assert payload["handoff_url"] == "http://localhost:8060/onramp"


def test_earnings_action_prepares_contributor_request(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("AUTOYOU_FUNDING_CONTRIBUTOR_REQUESTS_ENABLED", "true")

    payload = prepare_earnings_action("funding-request")

    assert payload["success"] is True
    assert payload["action"] == "contributor-request"
    assert payload["api_url"] == "https://app.autoyou.test/v1/funding/contributor-requests"
    assert "requestedPercent" in payload["request_fields"]
    assert "requestTitle" in payload["request_fields"]
    assert "requestPurpose" in payload["request_fields"]
    assert "sourceBucket" in payload["request_fields"]
    assert "publicArtifacts" in payload["request_fields"]
    assert payload["amount_modes"] == ["requested_percent", "fixed_usd"]
    assert ["requestedPercent", "requestedAmountUsd"] in payload["mutually_exclusive_fields"]
    assert payload["percentage"]["preferred_wire_unit"] == "fraction_0_to_1"
    assert payload["percentage"]["accepted_wire_units"] == ["fraction_0_to_1", "human_percent_0_to_100"]
    assert "divided by 100" in payload["percentage"]["normalization_rule"]
    assert payload["fixed_amount"]["wire_field"] == "requestedAmountUsd"
    assert payload["fixed_amount"]["maximum_source"] == "public_ledger.summary.available_pool_usd"
    assert payload["campaign_evidence"]["required"] is True
    assert payload["campaign_evidence"]["accepted_trust_surfaces"] == [
        "social_campaign",
        "community_thread",
        "code_host",
        "funding_provider",
    ]
    assert any("public request title and purpose" in item for item in payload["instructions"])
    assert any("requested percentage" in item for item in payload["instructions"])
    assert any("0-1 fraction" in item for item in payload["instructions"])
    assert any("requestedPercent and requestedAmountUsd" in item for item in payload["instructions"])
    assert any("cannot exceed public_ledger.summary.available_pool_usd" in item for item in payload["instructions"])
    assert any("campaign-grade public artifact links" in item for item in payload["instructions"])
    assert any("GitHub provider-id" in item for item in payload["prerequisites"])
    assert any("admin and OpenStorey LLC board review" in item for item in payload["prerequisites"])


def test_earnings_action_rejects_unknown_action():
    payload = prepare_earnings_action("mint-live-sov")

    assert payload["success"] is False
    assert payload["status"] == "unsupported_action"
    assert "request-settlement" in payload["allowed_actions"]
    assert payload["safety_limits"]["can_transfer_sov"] is False


def test_earnings_agent_is_builtin_and_installed_by_default():
    assert DEFAULT_AGENT_INSTALL_STATES["earnings_agent"] is True


def test_earnings_agent_ui_status_and_plan(monkeypatch):
    from autoyou_agents.earnings_agent.website.backend import app as earnings_ui_backend

    monkeypatch.setattr(
        earnings_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {"authenticated": False, "agent_name": agent_name},
    )
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "https://www.autoyou.test")

    client = TestClient(earnings_ui_backend.app)
    status_response = client.get("/api/status")
    plan_response = client.get("/api/plan")

    assert status_response.status_code == 200
    status = status_response.json()
    assert status["success"] is True
    assert status["account"]["funding_manifest_url"] == "https://app.autoyou.test/v1/funding/manifest"
    assert status["account"]["public_ledger_url"] == "https://app.autoyou.test/v1/funding/public-ledger"
    assert status["account"]["sov_status_url"] == "https://app.autoyou.test/v1/funding/sov/status"
    assert status["account"]["sov_wallet_url"] == "https://app.autoyou.test/v1/funding/sov/wallet"
    assert status["account"]["sov_wallet_provisioning_requests_url"] == "https://app.autoyou.test/v1/funding/sov/wallet-provisioning-requests"
    assert status["account"]["sov_settlement_requests_url"] == "https://app.autoyou.test/v1/funding/sov/settlement-requests"
    assert status["account"]["admin_records_url"] == "https://app.autoyou.test/v1/funding/admin/records"
    assert status["account"]["admin_contributor_requests_url"] == "https://app.autoyou.test/v1/funding/admin/contributor-requests"
    assert status["website"]["donate_url"] == "https://www.autoyou.test/donate/"
    assert status["status"]["sov"]["token_symbol"] == "USDC"

    assert plan_response.status_code == 200
    plan = plan_response.json()
    assert plan["success"] is True
    assert "donations" in plan["funding_os"]
    assert plan["sov"]["credit_settlement"] == "disabled_until_compliance"
    assert "POST /v1/funding/sov/wallet" in plan["sov"]["implemented_routes"]
    assert "POST /v1/funding/sov/wallet-provisioning-requests" in plan["sov"]["implemented_routes"]


def test_earnings_agent_ui_sanitizes_unsafe_base_urls(monkeypatch):
    from autoyou_agents.earnings_agent.website.backend import app as earnings_ui_backend

    monkeypatch.setattr(
        earnings_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {"authenticated": False, "agent_name": agent_name},
    )
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://operator:secret@app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "file:///tmp/not-official")

    client = TestClient(earnings_ui_backend.app)
    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload)
    assert payload["account"]["dashboard_url"] == "https://app.autoyou.me/dashboard?section=funding"
    assert payload["account"]["public_ledger_url"] == "https://app.autoyou.me/v1/funding/public-ledger"
    assert payload["website"]["donate_url"] == "https://www.autoyou.me/donate/"
    assert "operator:secret" not in serialized
    assert "file:///tmp/not-official" not in serialized


def test_earnings_agent_ui_rejects_remote_http_base_urls(monkeypatch):
    from autoyou_agents.earnings_agent.website.backend import app as earnings_ui_backend

    monkeypatch.setattr(
        earnings_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {"authenticated": False, "agent_name": agent_name},
    )
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "http://app.autoyou.test")
    monkeypatch.setenv("WEBSITE_PUBLIC_URL", "http://www.autoyou.test")

    client = TestClient(earnings_ui_backend.app)
    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["account"]["dashboard_url"] == "https://app.autoyou.me/dashboard?section=funding"
    assert payload["account"]["public_ledger_url"] == "https://app.autoyou.me/v1/funding/public-ledger"
    assert payload["website"]["donate_url"] == "https://www.autoyou.me/donate/"


def test_earnings_agent_ui_prepares_action(monkeypatch):
    from autoyou_agents.earnings_agent.website.backend import app as earnings_ui_backend

    monkeypatch.setattr(
        earnings_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {"authenticated": False, "agent_name": agent_name},
    )
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("AUTOYOU_SOV_WALLET_PROVISIONING_REQUESTS_ENABLED", "true")

    client = TestClient(earnings_ui_backend.app)
    response = client.get("/api/action", params={"action": "provision-wallet"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["action"]["success"] is True
    assert payload["action"]["action"] == "provision-wallet"
    assert payload["action"]["api_url"] == "https://app.autoyou.test/v1/funding/sov/wallet-provisioning-requests"
    assert payload["action"]["safety_limits"]["private_key_collection"] == "disabled"


def test_earnings_agent_ui_requires_local_auth_for_action(monkeypatch):
    from autoyou_agents.earnings_agent.website.backend import app as earnings_ui_backend

    monkeypatch.setattr(
        earnings_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {
            "authenticated": False,
            "required": True,
            "via": "none",
            "agent_name": agent_name,
        },
    )
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")
    monkeypatch.setenv("AUTOYOU_SOV_WALLET_PROVISIONING_REQUESTS_ENABLED", "true")

    client = TestClient(earnings_ui_backend.app)
    locked = client.get("/api/action", params={"action": "provision-wallet"})

    assert locked.status_code == 401
    locked_payload = locked.json()
    assert locked_payload["success"] is False
    assert locked_payload["action"]["status"] == "local_auth_required"
    assert locked_payload["action"]["requested_action"] == "provision-wallet"

    monkeypatch.setattr(
        earnings_ui_backend,
        "_describe_chat_auth_state",
        lambda request, agent_name: {
            "authenticated": True,
            "required": True,
            "via": "chat_session",
            "agent_name": agent_name,
        },
    )
    unlocked = client.get("/api/action", params={"action": "provision-wallet"})

    assert unlocked.status_code == 200
    unlocked_payload = unlocked.json()
    assert unlocked_payload["success"] is True
    assert unlocked_payload["action"]["success"] is True
    assert unlocked_payload["action"]["action"] == "provision-wallet"


def test_earnings_agent_ui_auth_status_exposes_totp_state(monkeypatch):
    from autoyou_agents.earnings_agent.website.backend import app as earnings_ui_backend

    monkeypatch.setattr(
        earnings_ui_backend._smc,
        "_describe_chat_auth_state",
        lambda request, agent_name: {
            "authenticated": False,
            "required": True,
            "via": "none",
            "agent_name": agent_name,
        },
    )
    monkeypatch.setattr(
        earnings_ui_backend._smc,
        "_totp_capabilities",
        lambda: {"totp_configured": True, "issuer": "AutoYou"},
    )

    client = TestClient(earnings_ui_backend.app)
    response = client.get("/api/auth/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["auth"]["authenticated"] is False
    assert payload["totp"]["totp_configured"] is True


def test_earnings_agent_ui_auth_login_issues_test_scoped_token(monkeypatch, tmp_path):
    from autoyou_agents.earnings_agent.website.backend import app as earnings_ui_backend

    sessions_path = tmp_path / "agent_ui_sessions.json"
    mock_server = SimpleNamespace(
        STATE=SimpleNamespace(config={}),
        _default_config=lambda: {},
        _is_logged_in=lambda request: False,
        _get_pairing_totp_secret=lambda cfg: "TESTSECRET",
        _verify_totp_secret=lambda secret, code: secret == "TESTSECRET" and code == "123456",
    )
    monkeypatch.setattr(earnings_ui_backend._smc, "_AGENT_UI_SESSIONS_FILE", str(sessions_path))
    earnings_ui_backend._smc._AGENT_UI_SESSIONS.clear()
    monkeypatch.setattr(earnings_ui_backend._smc, "_runtime_server", lambda: mock_server)
    monkeypatch.setattr(
        earnings_ui_backend._smc,
        "_totp_capabilities",
        lambda: {"totp_configured": True, "issuer": "AutoYou"},
    )

    client = TestClient(earnings_ui_backend.app)
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


def test_earnings_agent_ui_auth_login_rejects_missing_totp(monkeypatch):
    from autoyou_agents.earnings_agent.website.backend import app as earnings_ui_backend

    mock_server = SimpleNamespace(
        STATE=SimpleNamespace(config={}),
        _default_config=lambda: {},
    )
    monkeypatch.setattr(earnings_ui_backend._smc, "_runtime_server", lambda: mock_server)
    monkeypatch.setattr(
        earnings_ui_backend._smc,
        "_totp_capabilities",
        lambda: {"totp_configured": False},
    )

    client = TestClient(earnings_ui_backend.app)
    response = client.post("/api/auth/login", json={"code": "123456"})

    assert response.status_code == 400
    assert "not configured" in response.json()["error"].lower()


def test_earnings_agent_frontend_renders_coming_soon_credits():
    from autoyou_agents.earnings_agent.website.backend import app as earnings_ui_backend

    html = earnings_ui_backend._FRONTEND_DIR.joinpath("index.html").read_text(encoding="utf-8")
    js = earnings_ui_backend._FRONTEND_DIR.joinpath("assets", "app.js").read_text(encoding="utf-8")

    # Extremely simple coming-soon surface: pending credits only, no planner UI.
    assert "Coming" in html and "Soon" in html
    assert 'id="credit-count"' in html
    assert 'id="refresh-btn"' in html
    assert 'id="action-form"' not in html
    assert 'id="auth-form"' not in html

    # One local fetch, no polling, no cloud beaming from the page.
    assert "./api/pending-credits" in js
    assert js.count("fetch(") == 1
    assert "setInterval(load" not in js
    assert "autoyou.me" not in js

    # Compliance copy: pending credits are not cash/crypto/confirmed balances.
    assert "non-transferable" in html
    assert "not cash, crypto, gift cards, or confirmed balances" in html


def test_earnings_agent_pending_credits_route_reports_local_tally(monkeypatch, tmp_path):
    from autoyou_agents.earnings_agent.website.backend import app as earnings_ui_backend
    from shared import pending_ad_credits

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(
        earnings_ui_backend,
        "_runtime_server",
        lambda: SimpleNamespace(STATE=SimpleNamespace(config={"cloud": {"server_token": "tok"}})),
    )

    first = pending_ad_credits.record_rewarded_ad_completion(
        {
            "session_id": "session-a",
            "platform": "ios",
            "timestamp_ms": 1750000000000,
            "control_id": "ctl-1",
            "source": "ads_watching_agent",
            "watched_seconds": 31.5,
        }
    )
    duplicate = pending_ad_credits.record_rewarded_ad_completion(
        {
            "session_id": "session-a",
            "platform": "ios",
            "timestamp_ms": 1750000000000,
            "control_id": "ctl-1",
            "source": "ads_watching_agent",
            "watched_seconds": 31.5,
        }
    )
    second = pending_ad_credits.record_rewarded_ad_completion(
        {
            "session_id": "session-b",
            "platform": "android",
            "timestamp_ms": 1750000005000,
            "control_id": "ctl-2",
            "source": "ads_watching_agent",
            "watched_seconds": 30.0,
        }
    )

    assert first["pending_credits"] == 315
    assert duplicate["pending_credits"] == 315
    assert second["pending_credits"] == 615
    assert second["watched_seconds"] == 61.5
    assert second["credit_multiplier"] == 10
    assert second["last_event"]["pending_credits"] == 300
    assert second["ledger_effect"] == "none"
    assert second["cloud_hot_path_write"] is False

    client = TestClient(earnings_ui_backend.app)
    response = client.get("/api/pending-credits")

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-store"
    payload = response.json()
    assert payload["success"] is True
    assert payload["credits"]["pending_credits"] == 615
    assert payload["credits"]["watched_seconds"] == 61.5
    assert payload["credits"]["credit_multiplier"] == 10
    assert payload["credits"]["last_event"]["platform"] == "android"
    assert payload["cloud"]["signed_in"] is True
    # The route reports state only; it must not leak tokens or account ids.
    assert "tok" not in response.text
    assert "server_token" not in response.text


def test_earnings_agent_migrates_legacy_completion_count_to_seconds_formula(monkeypatch, tmp_path):
    from shared import pending_ad_credits

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    path = pending_ad_credits._store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pending_credits": 2,
                "events": 2,
                "last_event": {
                    "platform": "ios",
                    "watched_seconds": 54.4,
                },
                "seen_keys": ["control:legacy-1", "control:legacy-2"],
            }
        ),
        encoding="utf-8",
    )

    legacy = pending_ad_credits.get_pending_ad_credit_summary()
    assert legacy["pending_credits"] == 2
    assert legacy["watched_seconds"] == 0.2
    assert legacy["credit_multiplier"] == 10

    updated = pending_ad_credits.record_rewarded_ad_completion(
        {
            "control_id": "control:new-1",
            "platform": "web",
            "watched_seconds": 37.0,
        }
    )
    assert updated["pending_credits"] == 372
    assert updated["watched_seconds"] == 37.2


def test_earnings_agent_pending_credits_route_without_runtime(monkeypatch, tmp_path):
    from autoyou_agents.earnings_agent.website.backend import app as earnings_ui_backend

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(
        earnings_ui_backend,
        "_runtime_server",
        lambda: (_ for _ in ()).throw(RuntimeError("no runtime server")),
    )

    client = TestClient(earnings_ui_backend.app)
    response = client.get("/api/pending-credits")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["credits"]["pending_credits"] == 0
    assert payload["cloud"]["signed_in"] is False
    assert payload["cloud"]["sign_in_url"].startswith("https://app.autoyou.me/login")
