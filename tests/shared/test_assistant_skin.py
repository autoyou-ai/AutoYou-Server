# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from shared import assistant_skin


def test_default_skin_valid():
    skin = assistant_skin.get_active_skin()
    assert skin["id"] == "autoyou_classic"
    assert "palette" in skin
    assert "physics" in skin
    assert "emotes" in skin
    assert "audio_reactivity" in skin
    assert "springs" in skin["physics"]
    assert "lean" in skin["physics"]["springs"]


def test_presets_list_and_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_ASSISTANT_MODE_DIR", str(tmp_path / "assistant_mode"))

    presets = assistant_skin.list_presets()
    assert len(presets) >= 6
    preset_ids = {p["id"] for p in presets}
    assert "autoyou_classic" in preset_ids
    assert "neon_cyber" in preset_ids
    assert "solar_amber" in preset_ids
    assert "emerald_tech" in preset_ids
    assert "cosmic_violet" in preset_ids
    assert "stealth_dark" in preset_ids

    # Apply neon_cyber preset
    applied = assistant_skin.apply_preset("neon_cyber")
    assert applied["id"] == "neon_cyber"
    assert applied["palette"]["glow_primary"] == "#00f0ff"

    active = assistant_skin.get_active_skin()
    assert active["id"] == "neon_cyber"

    # Reset
    reset = assistant_skin.reset_active_skin()
    assert reset["id"] == "autoyou_classic"


def test_validation_clamps_and_normalizes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_ASSISTANT_MODE_DIR", str(tmp_path / "assistant_mode"))

    malformed: Dict[str, Any] = {
        "id": "bad/id/with/slashes",
        "name": "A" * 200,
        "palette": {
            "glow_primary": "not_a_color",
            "shell_gradient": ["#123", "invalid"],
        },
        "physics": {
            "springs": {
                "lean": {"k": 999999.0, "c": -50.0},
            },
            "squash_impulse": -100.0,
            "breath_speed": 100.0,
        },
        "emotes": {
            "eye_style": "unknown_style",
        },
    }

    validated = assistant_skin.validate_skin(malformed)
    assert validated["id"] == "custom"  # fallback for bad ID characters
    assert len(validated["name"]) <= 64
    assert validated["palette"]["glow_primary"] == assistant_skin.DEFAULT_PALETTE["glow_primary"]
    assert validated["palette"]["shell_gradient"][0] == "#123"
    # Physics springs clamped to safe ranges
    assert validated["physics"]["springs"]["lean"]["k"] == 500.0  # max clamp
    assert validated["physics"]["springs"]["lean"]["c"] == 0.05   # min clamp
    assert validated["physics"]["squash_impulse"] == -10.0        # min clamp
    assert validated["physics"]["breath_speed"] == 5.0            # max clamp
    assert validated["emotes"]["eye_style"] == "ring"             # fallback style


def test_export_and_import_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_ASSISTANT_MODE_DIR", str(tmp_path / "assistant_mode"))

    custom_skin = {
        "id": "my_custom_bot",
        "name": "My Custom Bot",
        "author": "Tester",
        "version": "1.0.1",
        "palette": {
            "glow_primary": "#ff00ff",
        },
    }
    assistant_skin.save_active_skin(custom_skin)

    exported_str = assistant_skin.export_skin_pack()
    exported_data = json.loads(exported_str)
    assert exported_data["format"] == "autoyou-assistant-skin-v1"
    assert exported_data["skin"]["id"] == "my_custom_bot"
    assert exported_data["skin"]["palette"]["glow_primary"] == "#ff00ff"

    # Reset
    assistant_skin.reset_active_skin()
    assert assistant_skin.get_active_skin()["id"] == "autoyou_classic"

    # Import
    imported = assistant_skin.import_skin_pack(exported_str)
    assert imported["id"] == "my_custom_bot"
    assert imported["palette"]["glow_primary"] == "#ff00ff"
    assert assistant_skin.get_active_skin()["id"] == "my_custom_bot"


def test_skin_fastapi_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_ASSISTANT_MODE_DIR", str(tmp_path / "assistant_mode"))

    from fastapi.testclient import TestClient
    import autoyou_agents.assistant_agent.website.backend.app as backend_app

    monkeypatch.setattr(backend_app, "_require_session", lambda request: None)
    client = TestClient(backend_app.app)

    # GET /api/assistant/skin/presets
    res = client.get("/api/assistant/skin/presets")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert len(data["presets"]) >= 6

    # GET /api/assistant/skin
    res = client.get("/api/assistant/skin")
    assert res.status_code == 200
    data = res.json()
    assert data["skin"]["id"] == "autoyou_classic"

    # POST /api/assistant/skin/preset/solar_amber
    res = client.post("/api/assistant/skin/preset/solar_amber")
    assert res.status_code == 200
    data = res.json()
    assert data["skin"]["id"] == "solar_amber"

    # GET /api/assistant/skin/export
    res = client.get("/api/assistant/skin/export")
    assert res.status_code == 200
    pack = res.json()
    assert pack["skin"]["id"] == "solar_amber"

    # POST /api/assistant/skin/reset
    res = client.post("/api/assistant/skin/reset")
    assert res.status_code == 200
    assert res.json()["skin"]["id"] == "autoyou_classic"

    # POST /api/assistant/skin/import
    res = client.post("/api/assistant/skin/import", json=pack)
    assert res.status_code == 200
    assert res.json()["skin"]["id"] == "solar_amber"


def test_camelcase_and_android_shorthand_skin_normalization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_ASSISTANT_MODE_DIR", str(tmp_path / "assistant_mode"))

    ios_camel_payload = {
        "id": "neon_cyber",
        "name": "Neon Cyber iOS",
        "palette": {
            "shellGradient": ["#1b2242", "#0e1328", "#070a18"],
            "visorGradient": ["#050712", "#0a0f24"],
            "accentGradient": ["#70ffff", "#00f0ff", "#bd00ff"],
            "glowPrimary": "#00f0ff",
            "glowSecondary": "#bd00ff",
            "coreColor": "#ffffff",
            "haloColor": "rgba(0, 240, 255, 0.28)",
        },
        "physics": {
            "squashImpulse": -0.5,
            "gazeSensitivity": 1.35,
            "breathSpeed": 2.1,
            "breathAmplitude": 2.6,
        },
        "emotes": {
            "eyeStyle": "neon",
            "mouthStyle": "curve",
            "blinkIntervalSeconds": [2.2, 5.5],
            "greetingWaveEnabled": True,
        },
        "audioReactivity": {
            "micEqualizerBands": 5,
            "speakerMouthTracking": True,
            "earGlowEnabled": True,
        },
    }
    validated_ios = assistant_skin.validate_skin(ios_camel_payload)
    assert validated_ios["palette"]["glow_primary"] == "#00f0ff"
    assert validated_ios["palette"]["glow_secondary"] == "#bd00ff"
    assert validated_ios["physics"]["gaze_sensitivity"] == 1.35
    assert validated_ios["physics"]["breath_speed"] == 2.1
    assert validated_ios["emotes"]["eye_style"] == "neon"
    assert validated_ios["audio_reactivity"]["mic_equalizer_bands"] == 5

    android_shorthand = {
        "id": "classic",
        "name": "AutoYou Classic",
        "palette": {
            "glow": "#ffaa00",
            "accent": "#ff5500",
            "core": "#fff5cc",
            "background": "#120c05",
        },
        "physics": {
            "breath_speed": 1.4,
            "joint_stiffness": 180.0,
            "gaze_lag": 0.14,
        },
        "eye_optics": "visor_bar",
    }
    validated_android = assistant_skin.validate_skin(android_shorthand)
    assert validated_android["id"] == "autoyou_classic"
    assert validated_android["palette"]["glow_primary"] == "#ffaa00"
    assert validated_android["palette"]["glow_secondary"] == "#ff5500"
    assert validated_android["palette"]["core_color"] == "#fff5cc"
    assert validated_android["emotes"]["eye_style"] == "visor_bar"
    assert validated_android["physics"]["springs"]["lean"]["k"] == 180.0


def test_paired_device_skin_and_settings_commute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_ASSISTANT_MODE_DIR", str(tmp_path / "assistant_mode"))

    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    import autoyou_agents.assistant_agent.website.backend.app as backend_app
    from autoyou_agents.shared_tools import scheduler_mission_control as smc

    fake_webrtc = SimpleNamespace(sessions={"sess-paired-1": SimpleNamespace(id="sess-paired-1")})
    fake_server = SimpleNamespace(
        STATE=SimpleNamespace(config={"agent_ui_security": {}, "agent_websites": {}}),
        _default_config=lambda: {},
        _is_logged_in=lambda request: False,
        _persist_state_config=lambda cfg: None,
        _describe_totp_capabilities=lambda cfg: {"totp_configured": True},
        _get_pairing_totp_secret=lambda cfg=None: "JBSWY3DPEHPK3PXP",
        _verify_totp_secret=lambda secret, code: False,
        agent_has_assigned_2fa_profile=lambda name: False,
        get_configured_server_name=lambda: "Jarvis",
        WEBRTC=fake_webrtc,
    )
    monkeypatch.setattr(smc, "_runtime_server", lambda: fake_server)
    client = TestClient(backend_app.app)

    device_headers = {
        "Origin": "http://testserver",
        "X-AutoYou-Agent-Frontend": "assistant_agent",
        "X-AutoYou-WebRTC-Session-Id": "sess-paired-1",
    }

    # Unauthenticated request without paired headers is rejected with 401
    assert client.get("/api/assistant/skin").status_code == 401

    # Paired native device can read skin + settings even when web UI is gated
    get_res = client.get("/api/assistant/skin", headers=device_headers)
    assert get_res.status_code == 200
    payload = get_res.json()
    assert payload["success"] is True
    assert "skin" in payload and "settings" in payload

    # Paired native device can update both skin and settings
    post_res = client.post(
        "/api/assistant/skin",
        headers=device_headers,
        json={
            "skin": {
                "id": "emerald_tech",
                "name": "Emerald Tech",
                "palette": {"glowPrimary": "#00ff88"},
                "emotes": {"eyeStyle": "neon"},
            },
            "settings": {
                "enabled": True,
                "wake_phrase": "Computer",
                "sensitivity": 0.65,
                "speak_replies": False,
                "follow_up": True,
            },
        },
    )
    assert post_res.status_code == 200
    updated = post_res.json()
    assert updated["skin"]["id"] == "emerald_tech"
    assert updated["skin"]["palette"]["glow_primary"] == "#00ff88"
    assert updated["skin"]["emotes"]["eye_style"] == "neon"
    assert updated["settings"]["wake_phrase"] == "Computer"
    assert abs(updated["settings"]["sensitivity"] - 0.65) < 1e-6
    assert updated["settings"]["speak_replies"] is False

    # Viewer role is forbidden from mutating skin/settings
    viewer_headers = dict(device_headers, **{"X-AutoYou-Remote-Role": "viewer"})
    assert client.get("/api/assistant/skin", headers=viewer_headers).status_code == 200
    assert client.post("/api/assistant/skin", headers=viewer_headers, json={"skin": {"id": "neon_cyber"}}).status_code == 403

    model = {"format": "autoyou-wake-word", "version": 1, "phrase": "Nova", "templates": [{"mfcc": [[0.1] * 12]}]}
    assert client.post("/api/assistant/model", json={"model": model}).status_code == 401
    assert client.post("/api/assistant/model", headers=viewer_headers, json={"model": model}).status_code == 403
    assert client.post("/api/assistant/model", headers=device_headers, json={"model": model}).status_code == 200
    assert client.get("/api/assistant/model", headers=device_headers).json()["model"]["wake_phrase"] == "Nova"
    assert client.post("/api/assistant/model", headers=device_headers, json={"model": {"templates": []}}).status_code == 400


