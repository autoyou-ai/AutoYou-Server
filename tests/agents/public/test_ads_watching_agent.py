# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-5d4e99034257b1c48f49c673


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import autoyou_agents.ads_watching_agent.agent as ads_agent
from autoyou_agents.ads_watching_agent.agent import (
    describe_ads_monetization_path,
    get_ads_watching_status,
    _is_rewarded_ad_trigger_request,
    trigger_agent_website_rewarded_ad,
    trigger_client_rewarded_ad,
)
from autoyou_agents.shared_tools.agent_install_registry import DEFAULT_AGENT_INSTALL_STATES

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-5d4e99034257b1c48f49c673"


def test_ads_watching_status_exposes_support_ad_boundaries(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)

    payload = get_ads_watching_status()
    serialized = json.dumps(payload)
    # from __debug_provenance_v__ import wallet

    assert payload["status"] == "configured"
    assert payload["runtime_flags"]["native_mobile_ad_trigger"] == "enabled"
    assert payload["live_ad_units"]["ios_rewarded"] is True
    assert payload["live_ad_units"]["android_rewarded"] is True
    assert payload["support_ad_boundary"]["native_agent_trigger"] == "client_native_ad_trigger_no_account_required"
    assert payload["support_ad_boundary"]["agent_website_targeting"] == (
        "webrtc_headers_or_single_live_client_when_available"
    )
    assert payload["support_ad_boundary"]["native_settings_trigger"] == "direct_ios_android_no_webrtc_required"
    assert payload["support_ad_boundary"]["webrtc_liveness"] == (
        "advisory_for_remote_browser_readback_not_native_ad_availability"
    )
    assert payload["support_ad_boundary"]["native_ad_unit_source"] == "baked_into_client_builds"
    assert payload["support_ad_boundary"]["server_ad_unit_dependency"] == "none_for_native_mobile_trigger"
    assert payload["support_ad_boundary"]["web_page_fallback"] == "approved_gpt_rewarded_only"
    assert payload["desktop_connect_web_policy"]["configured_by"] == "signed_client_private_release_env"
    assert payload["desktop_connect_web_policy"]["server_web_ad_unit_override"] == "approved_gpt_web_fallback_only"
    assert payload["desktop_connect_web_policy"]["completion_proof"] == "not_emitted"
    assert payload["desktop_connect_web_policy"]["cash_crypto_or_transferable_value"] == "not_available"
    assert "app.autoyou" not in serialized
    assert "oauth" not in serialized.lower()
    assert "pending reward credits" not in serialized.lower()


def test_ads_watching_status_uses_baked_mobile_units_without_server_admob_env(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    monkeypatch.delenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", raising=False)
    monkeypatch.delenv("AUTOYOU_ADMOB_IOS_REWARDED_AD_UNIT_ID", raising=False)
    monkeypatch.delenv("AUTOYOU_ADMOB_ANDROID_REWARDED_AD_UNIT_ID", raising=False)

    payload = get_ads_watching_status()

    assert payload["status"] == "configured"
    assert payload["live_ad_units"]["ios_rewarded"] is True
    assert payload["live_ad_units"]["android_rewarded"] is True
    assert payload["support_ad_boundary"]["server_ad_unit_dependency"] == "none_for_native_mobile_trigger"
    assert payload["safety_limits"]["can_trigger_native_mobile_ad"] is True


def test_ads_watching_status_can_disable_native_mobile_trigger(monkeypatch):
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_AD_PAGE_FALLBACK_ENABLED", "false")
    monkeypatch.delenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", raising=False)

    payload = get_ads_watching_status()

    assert payload["status"] == "disabled"
    assert payload["runtime_flags"]["native_mobile_ad_trigger"] == "disabled"
    assert payload["runtime_flags"]["web_rewarded_ad_unit"] == "disabled"
    assert payload["live_ad_units"]["ios_rewarded"] is False
    assert payload["live_ad_units"]["android_rewarded"] is False
    assert payload["live_ad_units"]["web_rewarded"] is False
    assert payload["safety_limits"]["can_trigger_web_rewarded_ad"] is False
    assert payload["safety_limits"]["can_trigger_native_mobile_ad"] is False

    for off_value in ("0", "off", "no"):
        monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", off_value)
        monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", off_value)
        disabled_payload = get_ads_watching_status()
        assert disabled_payload["runtime_flags"]["native_mobile_ad_trigger"] == "disabled"
        assert disabled_payload["live_ad_units"]["ios_rewarded"] is False
        assert disabled_payload["live_ad_units"]["android_rewarded"] is False
        assert disabled_payload["live_ad_units"]["web_rewarded"] is False
        assert disabled_payload["safety_limits"]["can_trigger_web_rewarded_ad"] is False
        assert disabled_payload["safety_limits"]["can_trigger_native_mobile_ad"] is False


def test_ads_watching_status_requires_client_web_hint_when_native_mobile_trigger_disabled(monkeypatch):
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "true")
    monkeypatch.setenv("AUTOYOU_WEB_AD_PAGE_FALLBACK_ENABLED", "false")
    monkeypatch.delenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", raising=False)

    payload = get_ads_watching_status()

    assert payload["status"] == "disabled"
    assert payload["runtime_flags"]["web_rewarded_ad_unit"] == "disabled"
    assert payload["live_ad_units"]["web_rewarded"] is False
    assert payload["web_rewarded_ad_unit"] == ""
    assert payload["safety_limits"]["can_trigger_web_rewarded_ad"] is False


def test_ads_watching_status_uses_approved_server_web_rewarded_unit_when_native_mobile_trigger_disabled(monkeypatch):
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_AD_PAGE_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", "/synthetic/rewarded")

    payload = get_ads_watching_status()

    assert payload["status"] == "configured"
    assert payload["live_ad_units"]["web_rewarded"] is True
    assert payload["live_ad_units"]["ios_rewarded"] is False
    assert payload["live_ad_units"]["android_rewarded"] is False
    assert payload["runtime_flags"]["web_rewarded_ad_unit"] == "enabled"
    assert payload["safety_limits"]["can_trigger_web_rewarded_ad"] is True
    assert payload["web_rewarded_ad_unit"] == "/synthetic/rewarded"


def test_ads_watching_status_accepts_desktop_release_web_rewarded_unit_alias(monkeypatch):
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_AD_PAGE_FALLBACK_ENABLED", "false")
    monkeypatch.delenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", raising=False)
    monkeypatch.setenv(
        "AUTOYOU_DESKTOP_REWARDED_AD_WEB_REWARDED_AD_UNIT_PATH",
        "/synthetic/desktop-rewarded",
    )

    payload = get_ads_watching_status()

    assert payload["status"] == "configured"
    assert payload["live_ad_units"]["web_rewarded"] is True
    assert payload["web_rewarded_ad_unit"] == "/synthetic/desktop-rewarded"


def test_ads_watching_status_has_no_account_managed_ad_fields(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://operator:secret@app.autoyou.test")
    monkeypatch.setenv("AUTOYOU_CREDITS_LEDGER_ENABLED", "true")
    monkeypatch.setenv("AUTOYOU_ADS_DONATE_ENABLED", "true")
    monkeypatch.setenv("AUTOYOU_SOV_SETTLEMENT_REQUESTS_ENABLED", "true")

    payload = get_ads_watching_status()
    serialized = json.dumps(payload).lower()

    for forbidden in (
        "account_dashboard",
        "funding_manifest",
        "credits_endpoint",
        "reward_intent_endpoint",
        "ssv_callback",
        "sov_status_endpoint",
        "sov_settlement_requests_endpoint",
        "future_credit_confirmation",
        "pending_credit_preview",
        "earnings_agent",
        "oauth",
        "app.autoyou",
        "operator:secret",
    ):
        assert forbidden not in serialized


def test_ads_monetization_path_names_support_ad_boundaries():
    payload = describe_ads_monetization_path()

    assert any("without account sign-in or WebRTC" in item for item in payload["support_ads"])
    assert any("private release environment" in item for item in payload["support_ads"])
    assert any("pending reward credits" in item for item in payload["user_value_boundary"])
    assert any("future AutoYou Cloud time and subscription gifts" in item for item in payload["user_value_boundary"])
    assert any("cash" in item for item in payload["user_value_boundary"])
    assert any("approved Google Ad Manager rewarded slot path" in item for item in payload["user_value_boundary"])
    assert any("https://autoyou.me/rewards/watch" in item for item in payload["desktop_web_route"])
    assert any("Do not use app.autoyou.me" in item for item in payload["desktop_web_route"])


def test_ads_watching_agent_ui_status_uses_sanitized_minimal_urls(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "http://app.autoyou.test")

    client = TestClient(ads_ui_backend.app)
    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "account" not in payload
    assert payload["status"] == {
        "status": "ready",
        "live_ad_units": {
            "ios_rewarded": True,
            "android_rewarded": True,
            "web_rewarded": False,
            "web_page": False,
        },
        "web_rewarded_ad_unit": "",
        "web_fallback": {
            "available": False,
            "mode": "web_page",
            "url": "",
        },
        "connection_proof": {
            "connected": False,
            "resolution": "unavailable",
            "heartbeat_recent": False,
            "heartbeat_age_seconds": None,
        },
    }
    serialized = json.dumps(payload)
    assert "auth_mode" not in serialized
    assert "session_ttl_days" not in serialized
    assert "pc_orchestrator_available" not in serialized
    assert "credits_endpoint" not in serialized
    assert "funding_manifest" not in serialized
    assert "reward_intent" not in serialized
    assert "admob" not in serialized.lower()
    assert "sov" not in serialized
    assert "oauth" not in serialized.lower()
    assert "app.autoyou" not in serialized.lower()


def test_ads_watching_agent_ui_status_ignores_web_rewarded_query_hint(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    calls = []

    class FakeWebRTC:
        def rewarded_ad_connection_proof(self, *, session_id="", owner_key="", allow_single_live_fallback=False):
            calls.append({"session_id": session_id, "owner_key": owner_key})
            return {
                "connected": False,
                "resolution": "unavailable",
                "heartbeat_recent": False,
                "heartbeat_age_seconds": None,
            }

        def latest_rewarded_ad_completion(self, *, session_id="", owner_key=""):
            return None

    monkeypatch.setattr(ads_ui_backend, "_runtime_server", lambda: SimpleNamespace(WEBRTC=FakeWebRTC()))

    client = TestClient(ads_ui_backend.app)
    response = client.get(
        "/api/status?session_id=ios-session-004&owner_key=cloud:ios-device-004&web_rewarded_ad_unit=%2Frewarded%2Fad%3Fplan%3Dautoyou"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"]["live_ad_units"]["web_rewarded"] is False
    assert payload["status"]["web_rewarded_ad_unit"] == ""
    assert payload["status"]["status"] == "ready"
    assert payload["status"]["connection_proof"] == {
        "connected": False,
        "resolution": "unavailable",
        "heartbeat_recent": False,
        "heartbeat_age_seconds": None,
    }
    assert calls == [
        {
            "session_id": "ios-session-004",
            "owner_key": "cloud:ios-device-004",
        }
    ]


def test_ads_watching_agent_ui_status_exposes_approved_server_web_rewarded_hint(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    calls = []

    class FakeWebRTC:
        def rewarded_ad_connection_proof(self, *, session_id="", owner_key="", allow_single_live_fallback=False):
            calls.append({"session_id": session_id, "owner_key": owner_key})
            return {
                "connected": False,
                "resolution": "unavailable",
                "heartbeat_recent": False,
                "heartbeat_age_seconds": None,
            }

        def latest_rewarded_ad_completion(self, *, session_id="", owner_key=""):
            return None

    monkeypatch.setenv(
        "AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", "/rewarded/server-default"
    )
    monkeypatch.setattr(ads_ui_backend, "_runtime_server", lambda: SimpleNamespace(WEBRTC=FakeWebRTC()))

    client = TestClient(ads_ui_backend.app)
    response = client.get("/api/status?session_id=ios-session-006&owner_key=cloud:ios-device-006")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"]["live_ad_units"]["web_rewarded"] is True
    assert payload["status"]["web_rewarded_ad_unit"] == "/rewarded/server-default"
    assert payload["status"]["status"] == "ready"
    assert calls == [
        {
            "session_id": "ios-session-006",
            "owner_key": "cloud:ios-device-006",
        }
    ]


def test_ads_watching_agent_ui_status_ignores_account_auth_inputs(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.test")

    client = TestClient(ads_ui_backend.app)
    response = client.get(
        "/api/status",
        headers={
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "account" not in payload
    serialized = json.dumps(payload)
    assert "app.autoyou" not in serialized.lower()
    assert "oauth" not in serialized.lower()
    assert "credit" not in serialized.lower()


def test_ads_watching_agent_ui_status_returns_latest_local_completion(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    calls = []

    class FakeWebRTC:
        def rewarded_ad_connection_proof(self, *, session_id="", owner_key="", allow_single_live_fallback=False):
            return {
                "connected": True,
                "resolution": "reply_target",
                "heartbeat_recent": True,
                "heartbeat_age_seconds": 1.2,
                "session_id": "must-not-leak",
                "owner_key": "cloud:must-not-leak",
            }

        def latest_rewarded_ad_completion(self, *, session_id="", owner_key=""):
            calls.append({"session_id": session_id, "owner_key": owner_key})
            return {
                "event": "rewarded_ad_completed",
                "session_id": session_id,
                "platform": "ios",
                "timestamp_ms": 123456789,
                "control_id": "rewarded-ad-control-001",
                "source": "ads_watching_agent",
                "watched_seconds": 37.0,
            }

    monkeypatch.setattr(ads_ui_backend, "_runtime_server", lambda: SimpleNamespace(WEBRTC=FakeWebRTC()))

    client = TestClient(ads_ui_backend.app)
    response = client.get(
        "/api/status",
        headers={
            "X-AutoYou-WebRTC-Session-Id": "ios-session-001",
            "X-AutoYou-WebRTC-Owner-Key": "cloud:ios-device-001",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "account" not in payload
    assert payload["status"]["status"] == "ready"
    assert payload["status"]["connection_proof"] == {
        "connected": True,
        "resolution": "reply_target",
        "heartbeat_recent": True,
        "heartbeat_age_seconds": 1.2,
    }
    serialized = json.dumps(payload)
    assert "must-not-leak" not in serialized
    assert "cloud:must-not-leak" not in serialized
    assert calls == [
        {
            "session_id": "ios-session-001",
            "owner_key": "cloud:ios-device-001",
        }
    ]


def test_ads_watching_agent_ui_status_targets_requesting_session_from_query(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    calls = []

    class FakeWebRTC:
        def rewarded_ad_connection_proof(self, *, session_id="", owner_key="", allow_single_live_fallback=False):
            calls.append({"session_id": session_id, "owner_key": owner_key})
            return {
                "connected": True,
                "resolution": "reply_target",
                "heartbeat_recent": True,
                "heartbeat_age_seconds": 1.2,
            }

        def latest_rewarded_ad_completion(self, *, session_id="", owner_key=""):
            return None

    monkeypatch.setattr(ads_ui_backend, "_runtime_server", lambda: SimpleNamespace(WEBRTC=FakeWebRTC()))

    client = TestClient(ads_ui_backend.app)
    response = client.get("/api/status?session_id=ios-session-003&owner_key=cloud:ios-device-003")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["status"]["connection_proof"] == {
        "connected": True,
        "resolution": "reply_target",
        "heartbeat_recent": True,
        "heartbeat_age_seconds": 1.2,
    }
    assert calls == [
        {
            "session_id": "ios-session-003",
            "owner_key": "cloud:ios-device-003",
        }
    ]


def test_ads_watching_agent_ui_does_not_publish_plan_endpoint():
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    client = TestClient(ads_ui_backend.app)
    response = client.get("/api/plan")

    assert response.status_code == 404


def test_ads_watching_agent_ui_favicon_is_quiet():
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    client = TestClient(ads_ui_backend.app)
    response = client.get("/favicon.ico")

    assert response.status_code == 204


def test_ads_watching_agent_ui_watch_ad_targets_requesting_webrtc_client(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    calls = []

    async def fake_trigger(*, session_id="", owner_key="", control_id="", web_rewarded_ad_unit=""):
        calls.append(
            {
                "session_id": session_id,
                "owner_key": owner_key,
                "web_rewarded_ad_unit": web_rewarded_ad_unit,
                "control_id": control_id,
            }
        )
        return {
            "success": True,
            "triggered_count": 1,
            "target_session_id": session_id,
            "owner_key": owner_key,
        }

    monkeypatch.setattr(ads_ui_backend, "trigger_agent_website_rewarded_ad", fake_trigger)

    client = TestClient(ads_ui_backend.app)
    response = client.post(
        "/api/watch-ad",
        headers={
            "X-AutoYou-WebRTC-Session-Id": "ios-session-001",
            "X-AutoYou-WebRTC-Owner-Key": "cloud:ios-device-001",
        },
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["result"] == {
        "triggered_count": 1,
        "status": "sent",
    }
    serialized = json.dumps(payload)
    assert "owner_key" not in serialized
    assert "target_session_id" not in serialized
    assert "owner_revenue_only" not in serialized
    assert calls == [
        {
            "session_id": "ios-session-001",
            "owner_key": "cloud:ios-device-001",
            "control_id": "",
            "web_rewarded_ad_unit": "",
        }
    ]


def test_ads_watching_agent_ui_falls_back_to_approved_web_page(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    async def fake_trigger(*, session_id="", owner_key="", control_id="", web_rewarded_ad_unit=""):
        return {
            "success": False,
            "reason": "No live native client",
            "triggered_count": 0,
        }

    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", "/synthetic/rewarded")
    monkeypatch.setattr(ads_ui_backend, "trigger_agent_website_rewarded_ad", fake_trigger)

    client = TestClient(ads_ui_backend.app)
    response = client.post(
        "/api/watch-ad?session_id=synthetic-session&owner_key=synthetic-owner",
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["result"]["status"] == "web_fallback"
    assert payload["result"]["mode"] == "gpt_rewarded"
    assert "web_rewarded_ad_unit=%2Fsynthetic%2Frewarded" in payload["result"]["watch_url"]
    assert "control_id=web-" in payload["result"]["watch_url"]
    assert "synthetic-owner" not in response.text


def test_ads_watching_agent_ui_does_not_fall_back_to_adsense(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    async def fake_trigger(*, session_id="", owner_key="", control_id="", web_rewarded_ad_unit=""):
        return {
            "success": False,
            "reason": "No live native client",
            "triggered_count": 0,
        }

    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.delenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", raising=False)
    monkeypatch.delenv("AUTOYOU_DESKTOP_REWARDED_AD_WEB_REWARDED_AD_UNIT_PATH", raising=False)
    monkeypatch.setenv(
        "AUTOYOU_DESKTOP_REWARDED_AD_ADSENSE_CLIENT_ID",
        "pub-0000000000000000",
    )
    monkeypatch.setattr(ads_ui_backend, "trigger_agent_website_rewarded_ad", fake_trigger)

    client = TestClient(ads_ui_backend.app)
    response = client.post("/api/watch-ad", json={})

    assert response.status_code == 404
    payload = response.json()
    assert payload == {
        "success": False,
        "result": {"triggered_count": 0, "status": "unavailable"},
    }


def test_ads_watching_agent_records_web_rewarded_completion_locally(monkeypatch, tmp_path):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend
    from shared import pending_ad_credits

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", "/synthetic/rewarded")

    async def fake_trigger(*, session_id="", owner_key="", control_id="", web_rewarded_ad_unit=""):
        return {"success": False, "reason": "No live native client", "triggered_count": 0}

    monkeypatch.setattr(ads_ui_backend, "trigger_agent_website_rewarded_ad", fake_trigger)
    client = TestClient(ads_ui_backend.app)
    fallback = client.post("/api/watch-ad").json()
    control_id = parse_qs(urlparse(fallback["result"]["watch_url"]).query)["control_id"][0]
    response = client.post(
        "/api/web-rewarded-completed",
        json={
            "control_id": control_id,
            "watched_seconds": 37.0,
            "timestamp_ms": 1750000000000,
        },
    )

    assert response.status_code == 200
    assert response.json()["reward"] == {
        "pending_credits": 370,
        "watched_seconds": 37.0,
        "ledger_effect": "none",
        "cloud_hot_path_write": False,
    }
    summary = pending_ad_credits.get_pending_ad_credit_summary()
    assert summary["pending_credits"] == 370
    assert summary["watched_seconds"] == 37.0
    assert summary["events"] == 1


def test_ads_watching_agent_rejects_unissued_web_rewarded_completion(monkeypatch, tmp_path):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    client = TestClient(ads_ui_backend.app)
    response = client.post(
        "/api/web-rewarded-completed",
        json={"control_id": "web-not-issued", "watched_seconds": 37.0},
    )

    assert response.status_code == 409
    assert response.json()["success"] is False


def test_ads_watching_agent_ui_watch_ad_targets_requesting_webrtc_client_by_query(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    calls = []

    async def fake_trigger(*, session_id="", owner_key="", control_id="", web_rewarded_ad_unit=""):
        calls.append({
            "session_id": session_id,
            "owner_key": owner_key,
            "web_rewarded_ad_unit": web_rewarded_ad_unit,
            "control_id": control_id,
        })
        return {
            "success": True,
            "triggered_count": 1,
            "target_session_id": session_id,
            "owner_key": owner_key,
        }

    monkeypatch.setattr(ads_ui_backend, "trigger_agent_website_rewarded_ad", fake_trigger)

    client = TestClient(ads_ui_backend.app)
    response = client.post(
        "/api/watch-ad?session_id=ios-session-002&owner_key=cloud:ios-device-002",
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["result"] == {
        "triggered_count": 1,
        "status": "sent",
    }
    assert calls == [
        {
            "session_id": "ios-session-002",
            "owner_key": "cloud:ios-device-002",
            "web_rewarded_ad_unit": "",
            "control_id": "",
        }
    ]


def test_ads_watching_agent_ui_watch_ad_does_not_inject_server_web_rewarded_hint(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    calls = []

    async def fake_trigger(*, session_id="", owner_key="", control_id="", web_rewarded_ad_unit=""):
        calls.append(
            {
                "session_id": session_id,
                "owner_key": owner_key,
                "web_rewarded_ad_unit": web_rewarded_ad_unit,
                "control_id": control_id,
            }
        )
        return {
            "success": True,
            "triggered_count": 1,
            "target_session_id": session_id,
            "owner_key": owner_key,
        }

    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", "/rewarded/server-default")
    monkeypatch.setattr(ads_ui_backend, "trigger_agent_website_rewarded_ad", fake_trigger)

    client = TestClient(ads_ui_backend.app)
    response = client.post("/api/watch-ad?session_id=ios-session-007&owner_key=cloud:ios-device-007", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["result"] == {
        "triggered_count": 1,
        "status": "sent",
    }
    assert calls == [
        {
            "session_id": "ios-session-007",
            "owner_key": "cloud:ios-device-007",
            "control_id": "",
            "web_rewarded_ad_unit": "",
        }
    ]


def test_ads_watching_agent_ui_watch_ad_forwards_web_rewarded_hint(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    calls = []

    async def fake_trigger(
        *,
        session_id="",
        owner_key="",
        control_id="",
        web_rewarded_ad_unit="",
    ):
        calls.append(
            {
                "session_id": session_id,
                "owner_key": owner_key,
                "control_id": control_id,
                "web_rewarded_ad_unit": web_rewarded_ad_unit,
            }
        )
        return {
            "success": True,
            "triggered_count": 1,
            "target_session_id": session_id,
            "owner_key": owner_key,
        }

    monkeypatch.setattr(ads_ui_backend, "trigger_agent_website_rewarded_ad", fake_trigger)

    client = TestClient(ads_ui_backend.app)
    response = client.post(
        "/api/watch-ad?session_id=ios-session-005&owner_key=cloud:ios-device-005&control_id=ctl-001&web_rewarded_ad_unit=/rewarded/ad?plan=autoyou",
        json={"control_id": "body-ctl", "web_rewarded_ad_unit": "/payload/rewarded"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "result": {"triggered_count": 1, "status": "sent"},
    }
    assert calls == [
        {
            "session_id": "ios-session-005",
            "owner_key": "cloud:ios-device-005",
            "control_id": "ctl-001",
            "web_rewarded_ad_unit": "",
        }
    ]


def test_ads_watching_agent_ui_roundtrip_targets_webrtc_and_reads_completion(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import server
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    class FakeDataChannelManager:
        def __init__(self):
            self.sent = []
            self.last_ping_time = time.time()

        async def send_message(self, message):
            self.sent.append(message)
            return True

    datachannel = FakeDataChannelManager()
    webrtc = server.WebRTCManager()
    webrtc.datachannel_managers["ios-session-001"] = datachannel
    monkeypatch.setattr(
        webrtc,
        "_resolve_chat_identity",
        lambda session_id: SimpleNamespace(owner_key="cloud:ios-device-001"),
    )
    monkeypatch.setattr(server, "WEBRTC", webrtc)
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "synthetic-internal-token")
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    monkeypatch.setattr(
        ads_agent,
        "_http",
        lambda *args, **kwargs: {
            "status": "error",
            "code": 404,
            "data": {"detail": "Not Found"},
        },
    )
    monkeypatch.setattr(ads_ui_backend, "_runtime_server", lambda: SimpleNamespace(WEBRTC=webrtc))

    client = TestClient(ads_ui_backend.app)
    headers = {
        "X-AutoYou-WebRTC-Session-Id": "ios-session-001",
        "X-AutoYou-WebRTC-Owner-Key": "cloud:ios-device-001",
    }
    watch_response = client.post("/api/watch-ad", headers=headers, json={})

    assert watch_response.status_code == 200
    assert watch_response.json() == {
        "success": True,
        "result": {
            "triggered_count": 1,
            "status": "sent",
        },
    }
    assert len(datachannel.sent) == 1
    sent = datachannel.sent[0]
    assert sent.header.message_type.value == "voice_call_control"
    assert sent.payload["event"] == "show_rewarded_ad"
    assert sent.payload["session_id"] == "ios-session-001"
    assert sent.payload["source"] == "ads_watching_agent"
    assert sent.payload["native_ad_unit_source"] == "client_baked_autoyou_build"
    assert sent.payload["desktop_web_ad_config_source"] == "signed_client_baked_release_env"
    assert sent.payload["ad_unit_ids_in_payload"] is False
    assert "ad_unit_id" not in sent.payload
    assert "reward_intent_endpoint" not in sent.payload

    duplicate_watch_response = client.post("/api/watch-ad", headers=headers, json={})
    assert duplicate_watch_response.status_code == 200
    assert duplicate_watch_response.json() == {
        "success": True,
        "result": {
            "triggered_count": 0,
            "status": "already_active",
        },
    }
    assert len(datachannel.sent) == 1

    completion = server.create_voice_call_control_message(
        payload={
            "event": "rewarded_ad_completed",
            "platform": "ios",
            "timestamp_ms": 123456789,
            "control_id": sent.payload["control_id"],
            "source": "ads_watching_agent",
            "watched_seconds": 37.0,
            "transaction_id": "txn_should_not_leak",
            "ad_unit": "ca-app-pub-0000000000000000/1111111111",
        },
        session_id="ios-session-001",
        user_id="AutoYou-ios",
    )
    asyncio.run(webrtc._handle_voice_call_control_message(completion))

    duplicate_completion = server.create_voice_call_control_message(
        payload={
            "event": "rewarded_ad_completed",
            "platform": "ios",
            "timestamp_ms": 123456790,
            "control_id": sent.payload["control_id"],
            "source": "ads_watching_agent",
            "watched_seconds": 37.0,
        },
        session_id="ios-session-001",
        user_id="AutoYou-ios",
    )
    asyncio.run(webrtc._handle_voice_call_control_message(duplicate_completion))

    status_response = client.get("/api/status", headers=headers)

    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"]["status"] == "ready"
    assert status_payload["status"]["connection_proof"]["connected"] is True
    assert status_payload["status"]["connection_proof"]["resolution"] == "reply_target"
    assert status_payload["status"]["connection_proof"]["heartbeat_recent"] is True
    assert status_payload["status"]["connection_proof"]["heartbeat_age_seconds"] >= 0
    rewarded = status_payload["rewarded_ad_completion"]
    assert rewarded["event"] == "rewarded_ad_completed"
    assert rewarded["session_id"] == "ios-session-001"
    assert rewarded["platform"] == "ios"
    assert rewarded["watched_seconds"] == 37.0
    assert rewarded["timestamp_ms"] == 123456789
    serialized = json.dumps(status_payload)
    assert "txn_should_not_leak" not in serialized
    assert "ca-app-pub" not in serialized
    assert "owner_key" not in serialized


def test_ads_watching_agent_frontend_renders_watch_button_with_safe_pending_credit_copy():
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_ui_backend

    html = ads_ui_backend._FRONTEND_DIR.joinpath("index.html").read_text(encoding="utf-8")
    js = ads_ui_backend._FRONTEND_DIR.joinpath("assets", "app.js").read_text(encoding="utf-8")
    css = ads_ui_backend._FRONTEND_DIR.joinpath("assets", "styles.css").read_text(encoding="utf-8")

    assert 'id="credits-link"' not in html
    assert 'id="watch-ad-btn"' in html
    assert 'id="watch-ad-btn" class="watch-button" type="button" aria-describedby="watch-status" disabled' in html
    assert 'id="account-strip"' not in html
    assert 'id="signed-in-summary"' not in html
    assert 'id="web-rewarded-ad-shell"' not in html
    assert 'id="open-web-rewarded-ad-link"' not in html
    assert "Watch Ad" in html
    assert "Want to earn credits for your time contributed? Sign in?" not in html
    assert "Sign in?" not in html
    assert "https://app.autoyou.me/v1/auth/oauth/google/start" not in html
    assert "/login?next" not in html
    assert "Pending reward credits stay in AutoYou" in html
    assert "confirmed credits" not in html.lower()
    assert "Coming soon. Help support AutoYou now." not in html
    assert "./api/watch-ad" in js
    assert "renderAccountDetail" not in js
    assert "Opening ad..." in js
    assert "Ad opened." in js
    assert "Thanks for watching." in js
    assert "Ad unavailable right now." in js
    assert "showUnavailable" in js
    assert "startCompletionPolling" in js
    assert "watchRequestPending" in js
    assert 'status === "already_active"' in js
    assert 'return `control:${controlId}`' in js
    assert "rewarded_ad_completion" in js
    assert "connection.connected" not in js
    assert "function renderAdReadiness(status)" not in js
    assert "Connect your iOS or Android AutoYou app" not in html
    assert "Ready on connected mobile clients" not in js
    assert "How ads watching works" not in html
    assert "How ads credits work" not in html
    assert "ads-credit agreement" not in js
    assert "api/plan" not in js
    assert "ad_unit_id" not in js
    assert "ca-app-pub" not in js
    assert "transaction_id" not in js
    assert "owner_revenue_boundary" not in js
    assert "place-items: center" in css
    assert "width: min(100%, 520px)" in css
    assert "min-height: clamp(170px, 38dvh, 320px)" in css
    assert "border-radius: 8px" in css
    assert "font-size: 4.5rem" in css
    assert "web-rewarded-ad-shell" not in css
    assert "web-rewarded-ad-button" not in css
    assert "outline: 3px solid var(--focus)" in css
    assert "@media (max-width: 420px)" in css
    assert "min-height: 66dvh" in css
    assert "font-size: 3rem" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "transition: none" in css
    assert "border-radius: 999px" not in css
    assert "gradient" in css.lower()
    assert "orb" not in css.lower()
    assert "sov" not in js.lower()


def test_ads_watching_agent_is_builtin_and_installed_by_default():
    assert DEFAULT_AGENT_INSTALL_STATES["ads_watching_agent"] is True


def _tool_context_for_webrtc(owner_key="cloud:account-user-001"):
    return SimpleNamespace(
        state={
            "autoyou_reply_target": {
                "transport": "webrtc",
                "owner_key": owner_key,
            }
        }
    )


def _patch_rewarded_ad_http(monkeypatch, response=None):
    calls = []
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "synthetic-internal-token")

    def fake_http(method, path, payload=None, **kwargs):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "kwargs": kwargs,
            }
        )
        if response is not None:
            return response
        return {
            "status": "success",
            "code": 200,
            "data": {
                "success": True,
                "triggered_count": 1,
                "target_session_id": "session-001",
                "owner_key": "cloud:account-user-001",
                "resolution": "reply_target",
            },
        }

    monkeypatch.setattr(ads_agent, "_http", fake_http)
    return calls


def test_trigger_client_rewarded_ad_requires_webrtc_tool_context(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)

    result = asyncio_run(trigger_client_rewarded_ad())

    assert result["success"] is False
    assert "WebRTC-capable client" in result["reason"]
    assert "Settings" not in result["reason"]


def test_ads_watching_description_treats_webrtc_liveness_as_advisory():
    payload = describe_ads_monetization_path()
    serialized = json.dumps(payload)

    assert "native iOS/Android Settings can start the same support ad directly" in serialized
    assert "without account sign-in or WebRTC" in serialized
    assert "Require only the active WebRTC client session" not in serialized


def test_trigger_client_rewarded_ad_respects_disabled_native_mobile_trigger(monkeypatch):
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "false")
    calls = _patch_rewarded_ad_http(monkeypatch)

    result = asyncio_run(trigger_client_rewarded_ad(_tool_context_for_webrtc()))

    assert result["success"] is False
    assert result["triggered_count"] == 0
    assert "disabled" in result["reason"]
    assert calls == []


def test_trigger_client_rewarded_ad_prefers_native_trigger_when_web_rewarded_toggle_disabled(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "false")
    calls = _patch_rewarded_ad_http(monkeypatch)

    result = asyncio_run(trigger_client_rewarded_ad(_tool_context_for_webrtc()))

    assert result["success"] is True
    assert result["triggered_count"] == 1
    assert calls[0]["payload"]["payload"]["event"] == "show_rewarded_ad"
    assert "web_rewarded_ad_unit" not in calls[0]["payload"]["payload"]


def test_trigger_client_rewarded_ad_ignores_server_web_rewarded_unit_when_native_mobile_trigger_disabled(monkeypatch):
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "true")
    monkeypatch.setenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", "/synthetic/rewarded")
    calls = _patch_rewarded_ad_http(monkeypatch)

    result = asyncio_run(trigger_client_rewarded_ad(_tool_context_for_webrtc()))

    assert result["success"] is False
    assert result["triggered_count"] == 0
    assert calls == []


def test_ads_watching_detects_direct_native_ad_trigger_phrases():
    assert _is_rewarded_ad_trigger_request("Start native ad") is True
    assert _is_rewarded_ad_trigger_request("Start native add") is True
    assert _is_rewarded_ad_trigger_request("Trigger ad") is True
    assert _is_rewarded_ad_trigger_request("Start") is True
    assert _is_rewarded_ad_trigger_request("go to ads watching agent") is False
    assert _is_rewarded_ad_trigger_request("Skip") is False


def test_trigger_client_rewarded_ad_targets_current_cloud_webrtc_session(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    calls = _patch_rewarded_ad_http(monkeypatch)

    result = asyncio_run(trigger_client_rewarded_ad(_tool_context_for_webrtc()))

    assert result["success"] is True
    assert result["triggered_count"] == 1
    assert result["owner_key"] == "cloud:account-user-001"
    assert calls[0]["path"] == "/api/webrtc/rewarded-ad"
    assert calls[0]["payload"]["owner_key"] == "cloud:account-user-001"
    assert calls[0]["payload"]["allow_single_live_fallback"] is False
    assert calls[0]["payload"]["payload"]["event"] == "show_rewarded_ad"
    assert calls[0]["payload"]["payload"]["native_ad_unit_source"] == "client_baked_autoyou_build"
    assert calls[0]["payload"]["payload"]["desktop_web_ad_config_source"] == "signed_client_baked_release_env"
    assert calls[0]["payload"]["payload"]["ad_unit_ids_in_payload"] is False
    assert "ad_units" not in calls[0]["payload"]["payload"]


def test_trigger_client_rewarded_ad_reports_missing_server_route(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "synthetic-internal-token")

    monkeypatch.setattr(
        ads_agent,
        "_http",
        lambda *args, **kwargs: {
            "status": "error",
            "code": 404,
            "data": {"detail": "Not Found"},
            "url": "http://localhost:8001/api/webrtc/rewarded-ad",
        },
    )

    async def no_in_process_fallback(*args, **kwargs):
        return None

    monkeypatch.setattr(ads_agent, "_try_in_process_rewarded_ad", no_in_process_fallback)

    result = asyncio_run(trigger_client_rewarded_ad(_tool_context_for_webrtc()))

    assert result["success"] is False
    assert result["triggered_count"] == 0
    assert result["http_status"] == 404
    assert result["failure_kind"] == "server_route_missing"
    assert "/api/webrtc/rewarded-ad" in result["reason"]
    assert "WebRTC connection" not in result["reason"]


def test_trigger_client_rewarded_ad_uses_in_process_fallback_when_route_is_missing(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "synthetic-internal-token")

    monkeypatch.setattr(
        ads_agent,
        "_http",
        lambda *args, **kwargs: {
            "status": "error",
            "code": 404,
            "data": {"detail": "Not Found"},
            "url": "http://localhost:8001/api/webrtc/rewarded-ad",
        },
    )

    fallback_calls = []

    async def fake_in_process_fallback(reply_target, request_payload, *, allow_single_live_fallback):
        fallback_calls.append(
            {
                "reply_target": reply_target,
                "request_payload": request_payload,
                "allow_single_live_fallback": allow_single_live_fallback,
            }
        )
        return {
            "success": True,
            "triggered_count": 1,
            "target_session_id": "session-001",
            "owner_key": "cloud:account-user-001",
            "fallback": "in_process_webrtc_sender",
        }

    monkeypatch.setattr(ads_agent, "_try_in_process_rewarded_ad", fake_in_process_fallback)

    result = asyncio_run(trigger_client_rewarded_ad(_tool_context_for_webrtc()))

    assert result["success"] is True
    assert result["triggered_count"] == 1
    assert result["fallback"] == "in_process_webrtc_sender"
    assert result["http_failure_kind"] == "server_route_missing"
    assert fallback_calls[0]["reply_target"]["owner_key"] == "cloud:account-user-001"
    assert fallback_calls[0]["request_payload"]["payload"]["event"] == "show_rewarded_ad"


def test_trigger_agent_website_rewarded_ad_targets_web_proxy_session(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    calls = _patch_rewarded_ad_http(monkeypatch)

    result = asyncio_run(
        trigger_agent_website_rewarded_ad(
            session_id="ios-session-001",
            owner_key="cloud:ios-device-001",
        )
    )

    assert result["success"] is True
    assert calls[0]["path"] == "/api/webrtc/rewarded-ad"
    assert calls[0]["payload"]["session_id"] == "ios-session-001"
    assert calls[0]["payload"]["owner_key"] == "cloud:ios-device-001"
    assert calls[0]["payload"]["allow_single_live_fallback"] is False
    assert calls[0]["payload"]["payload"]["source"] == "ads_watching_agent"
    assert calls[0]["payload"]["payload"]["ad_unit_ids_in_payload"] is False


def test_trigger_agent_website_rewarded_ad_allows_single_live_fallback_only_without_request_target(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    calls = _patch_rewarded_ad_http(monkeypatch)

    result = asyncio_run(trigger_agent_website_rewarded_ad())

    assert result["success"] is True
    assert "session_id" not in calls[0]["payload"]
    assert "owner_key" not in calls[0]["payload"]
    assert calls[0]["payload"]["allow_single_live_fallback"] is True


def test_trigger_agent_website_rewarded_ad_respects_disabled_native_mobile_trigger(monkeypatch):
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "false")
    calls = _patch_rewarded_ad_http(monkeypatch)

    result = asyncio_run(trigger_agent_website_rewarded_ad(session_id="ios-session-001"))

    assert result["success"] is False
    assert result["triggered_count"] == 0
    assert "disabled" in result["reason"]
    assert calls == []


def test_trigger_agent_website_rewarded_ad_stays_disabled_when_native_mobile_trigger_disabled(monkeypatch):
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "false")
    calls = _patch_rewarded_ad_http(monkeypatch)

    result = asyncio_run(trigger_agent_website_rewarded_ad(session_id="ios-session-001"))

    assert result["success"] is False
    assert result["triggered_count"] == 0
    assert "disabled" in result["reason"]
    assert calls == []


def test_trigger_agent_website_rewarded_ad_rejects_web_rewarded_unit_when_native_mobile_trigger_disabled(monkeypatch):
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "true")
    calls = _patch_rewarded_ad_http(monkeypatch)

    result = asyncio_run(
        trigger_agent_website_rewarded_ad(
            session_id="ios-session-001",
            web_rewarded_ad_unit="/rewarded/autoyou?plan=connect",
        )
    )

    assert result["success"] is False
    assert result["triggered_count"] == 0
    assert "disabled" in result["reason"]
    assert calls == []


def test_trigger_client_rewarded_ad_allows_non_oauth_webrtc_session(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    calls = _patch_rewarded_ad_http(
        monkeypatch,
        response={
            "status": "success",
            "code": 200,
            "data": {
                "success": True,
                "triggered_count": 1,
                "target_session_id": "session-001",
                "owner_key": "guest:session-001",
            },
        },
    )

    result = asyncio_run(trigger_client_rewarded_ad(_tool_context_for_webrtc("guest:session-001")))

    assert result["success"] is True
    assert result["owner_key"] == "guest:session-001"
    assert calls[0]["payload"]["owner_key"] == "guest:session-001"


def test_trigger_client_rewarded_ad_relays_session_id_only_webrtc_target(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    calls = _patch_rewarded_ad_http(monkeypatch)
    context = SimpleNamespace(
        state={
            "autoyou_reply_target": {
                "transport": "webrtc",
                "session_id": "session-001",
            }
        }
    )

    result = asyncio_run(trigger_client_rewarded_ad(context))

    assert result["success"] is True
    assert calls[0]["payload"]["session_id"] == "session-001"
    assert "owner_key" not in calls[0]["payload"]


def test_trigger_client_rewarded_ad_surfaces_server_owner_mismatch(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    _patch_rewarded_ad_http(
        monkeypatch,
        response={
            "status": "error",
            "code": 404,
            "data": {
                "success": False,
                "reason": "The resolved WebRTC client does not match the requesting account.",
                "triggered_count": 0,
                "owner_key": "cloud:actual-account",
                "resolution": "owner_mismatch",
            },
        },
    )

    context = SimpleNamespace(
        state={
            "autoyou_reply_target": {
                "transport": "webrtc",
                "session_id": "session-001",
                "owner_key": "cloud:other-account",
            }
        }
    )
    result = asyncio_run(trigger_client_rewarded_ad(context))

    assert result["success"] is False
    assert "does not match the requesting account" in result["reason"]
    assert result["owner_key"] == "cloud:actual-account"


def test_trigger_client_rewarded_ad_surfaces_single_live_owner_mismatch(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    calls = _patch_rewarded_ad_http(
        monkeypatch,
        response={
            "status": "error",
            "code": 404,
            "data": {
                "success": False,
                "triggered_count": 0,
                "target_session_id": "ios-session-001",
                "owner_key": "cloud:ios-device-001",
                "reason": "The single live WebRTC client does not match the requesting account.",
                "resolution": "owner_mismatch",
            },
        },
    )
    context = SimpleNamespace(
        state={
            "autoyou_owner_key": "telegram:5550001001",
            "autoyou_reply_target": {
                "transport": "telegram",
                "chat_id": 5550001001,
            },
        }
    )

    result = asyncio_run(trigger_client_rewarded_ad(context))

    assert result["success"] is False
    assert result["resolution"] == "owner_mismatch"
    assert "does not match" in result["reason"]
    assert calls[0]["payload"]["owner_key"] == "telegram:5550001001"
    assert calls[0]["payload"]["allow_single_live_fallback"] is True


def asyncio_run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
