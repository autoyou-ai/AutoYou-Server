# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Zero-Touch Provisioning QR export payload contract."""

import json

from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import server


def _export_qr_with_config(monkeypatch, config: dict) -> dict:
    original_config = server.STATE.config
    try:
        server.STATE.config = config
        monkeypatch.setattr(server, "_require_login", lambda request: None)
        monkeypatch.setattr(server, "_has_loaded_config_session", lambda: True)
        monkeypatch.setattr(server, "get_server_password", lambda: "test-password")
        rendered = []
        render_qr = server._build_local_qr_data_url

        def local_qr(payload):
            rendered.append(json.loads(payload))
            return render_qr(payload)

        def hosted_qr(*args, **kwargs):
            raise AssertionError("Computer credentials must never reach a hosted QR service")

        monkeypatch.setattr(server, "_build_local_qr_data_url", local_qr)
        monkeypatch.setattr(server, "_build_qr_code_url", hosted_qr)

        with TestClient(server.admin_app) as client:
            response = client.get("/api/settings/export-qr")

        assert response.status_code == 200
        body = response.json()
        assert body["qr_url"].startswith("data:image/png;base64,")
        assert response.headers["cache-control"] == "no-store"
        body["decoded_payload"] = rendered[0]
        return body
    finally:
        server.STATE.config = original_config


def test_export_settings_qr_includes_call_feature_flags(monkeypatch):
    config = server._default_config()
    config["video_call"] = {
        "enabled": True,
        "audio_enabled": True,
        "background_mode_enabled": True,
        "silent_recording_enabled": True,
        "record_my_video": True,
        "capture_audio": True,
        "audio_sources": ["speaker_loopback"],
    }

    body = _export_qr_with_config(monkeypatch, config)
    payload = body["decoded_payload"]

    assert payload["v"] == 3
    assert payload["pw"] == "test-password"
    assert payload["vc"] is True
    assert payload["bg"] is True
    assert payload["rec"] is True
    assert payload["cam"] is True
    assert payload["caud"] is True

    features = body["features"]
    assert features["video_calls"] is True
    assert features["background_mode"] is True
    assert features["safety_recording"] is True
    assert features["auto_self_video"] is True
    assert features["auto_computer_audio"] is True


def test_maximus_export_qr_contains_mobile_credentials_and_supported_pairing_mode(monkeypatch):
    import shared.local_network_info as network

    # The fixture must provide an available transport; loopback-only test
    # hosts correctly export no LAN profile, regardless of security mode.
    monkeypatch.setattr(network, "build_local_pair_info", lambda **kwargs: {
        "lan_reachable": True, "primary_address": "192.0.2.10", "port": 8001,
    })
    config = server._default_config()
    config["security"].update({"mode": "secure_professional_maximus", "totp_secret": "JBSWY3DPEHPK3PXP"})
    payload = _export_qr_with_config(monkeypatch, config)["decoded_payload"]

    assert payload["v"] == 3
    assert payload["pw"] == "test-password"
    assert payload["mode"] == "secure_professional"
    assert payload["totp"] == "JBSWY3DPEHPK3PXP"
    assert payload["profiles"]
    assert payload["preferred"] in payload["profiles"]


def test_export_settings_qr_reports_disabled_call_features(monkeypatch):
    config = server._default_config()
    config["video_call"] = {
        "enabled": False,
        "audio_enabled": False,
        "background_mode_enabled": True,
        "silent_recording_enabled": True,
        "record_my_video": True,
        "capture_audio": True,
        "audio_sources": ["speaker_loopback"],
    }

    body = _export_qr_with_config(monkeypatch, config)
    payload = body["decoded_payload"]

    # Everything call-related collapses to False when video calls / call audio
    # are disabled, so a scan mirrors the true server state onto the client.
    assert payload["vc"] is False
    assert payload["bg"] is False
    assert payload["rec"] is False
    assert payload["cam"] is False
    assert payload["caud"] is False


def test_export_settings_qr_microphone_capture_is_not_computer_audio(monkeypatch):
    config = server._default_config()
    config["video_call"] = {
        "enabled": True,
        "audio_enabled": True,
        "capture_audio": True,
        "audio_sources": ["microphone"],
    }

    body = _export_qr_with_config(monkeypatch, config)
    payload = body["decoded_payload"]

    # The server captures its microphone, not the computer's own audio, so the
    # client must not auto-start the computer-audio listen mode.
    assert payload["vc"] is True
    assert payload["caud"] is False


def test_one_scan_carries_all_available_profiles_without_peer_link(monkeypatch):
    import shared.local_network_info as network

    config = server._default_config()
    config["cloud"]["server_token"] = "synthetic-server-token"
    config["telegram"]["bot_username"] = "@synthetic_test_bot"
    config["tunnelmole"]["enabled"] = True
    config["security"]["tier"] = "B"
    monkeypatch.setattr(server, "_is_bluetooth_pairing_enabled", lambda: True)
    monkeypatch.setattr(network, "build_local_pair_info", lambda **kwargs: {
        "lan_reachable": True, "primary_address": "192.0.2.10", "port": 8001,
    })
    payload = _export_qr_with_config(monkeypatch, config)["decoded_payload"]
    assert payload["preferred"] == "cloud_pair"
    assert set(payload["profiles"]) == {"cloud_pair", "local_pair", "bluetooth_pair", "auto_pair", "otp"}
    assert payload["profiles"]["local_pair"] == {"tier": "A", "host": "192.0.2.10", "port": 8001}
    assert payload["profiles"]["auto_pair"] == {"tier": "B"}
    assert payload["totp"] == ""  # Clears a previous computer's authenticator.


def test_profile_export_omits_unavailable_transports():
    from shared.mobile_provisioning import mobile_pairing_profiles

    result = mobile_pairing_profiles(
        cloud=False, autopair=False, otp=False, bluetooth=True,
        local={"lan_reachable": False}, security_tier="B", bluetooth_name="Test Bluetooth Pair",
    )
    assert result == {"profiles": {"bluetooth_pair": {"tier": "A", "host": "Test Bluetooth Pair"}},
                      "preferred": "bluetooth_pair"}
