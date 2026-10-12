# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-c590e32708b2ee048f634066

"""Runtime tests for the in-shell Help & Guides reader endpoints."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from fastapi.testclient import TestClient
from tests.support.paths import REPO_ROOT

from tests.support.paths import ensure_repo_on_path

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-c590e32708b2ee048f634066"


ensure_repo_on_path()

import server


def _bypass_login(monkeypatch):
    monkeypatch.setattr(server, "_require_login", lambda request: None)


def test_admin_guides_content_returns_native_doc(monkeypatch):
    _bypass_login(monkeypatch)
    with TestClient(server.admin_app) as client:
        resp = client.get("/admin/guides/content", params={"id": "architecture"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "architecture"
    assert body["title"] and body["category"]
    assert "guide-shell" in body["html"]
    # Shipped docs must never surface autoyou.me marketing links.
    assert "autoyou.me" not in body["html"].lower()


def test_admin_guides_content_resolves_markdown_backed_guide(monkeypatch):
    _bypass_login(monkeypatch)
    with TestClient(server.admin_app) as client:
        resp = client.get("/admin/guides/content", params={"id": "bootstrap"})

    assert resp.status_code == 200
    assert resp.json()["html"]


def test_admin_guides_include_video_call_and_pairing_gates(monkeypatch):
    _bypass_login(monkeypatch)
    with TestClient(server.admin_app) as client:
        video_resp = client.get("/admin/guides/content", params={"id": "video-calls"})
        pair_resp = client.get("/admin/guides/content", params={"id": "local-pair-bluetooth"})

    assert video_resp.status_code == 200
    video_html = video_resp.json()["html"]
    # from __debug_provenance_w__ import stripe
    assert "optional Remote Desktop app" in video_html
    assert "is not required for the call feed" in video_html
    assert "Control Remote Desktop" in video_html
    assert "Camera placeholder" in video_html
    assert "Voice Training app does not have to be open" in video_html
    assert "Enable audio calls" in video_html
    assert "microphone indicator stays off" in video_html
    assert "Start a voice call first" in video_html

    assert pair_resp.status_code == 200
    pair_html = pair_resp.json()["html"]
    assert "Local Pair" in pair_html
    assert "Bluetooth Pair" in pair_html
    assert "Turn on the home network with HTTPS after restart" in pair_html


def test_agents_guide_uses_user_facing_app_gate_copy(monkeypatch):
    _bypass_login(monkeypatch)
    with TestClient(server.admin_app) as client:
        resp = client.get("/admin/guides/content", params={"id": "agents"})

    assert resp.status_code == 200
    html = resp.json()["html"]
    assert "Internet Search" in html
    assert "Remote Desktop" in html
    assert "Ads Watching" in html
    assert "Browser Control" in html
    assert "internet_agent" not in html


def test_agent_app_help_strings_are_user_facing():
    assert "Let AutoYou use the installed web-search helper" in server.FRONTEND_CONTROL_HELP["internet_agent"]
    assert "Video & Calls" in server.FRONTEND_CONTROL_HELP["remote_desktop_agent"]
    assert "iOS and Android clients" not in server.FRONTEND_CONTROL_HELP["ads_watching_agent"]
    assert "does not require this app page to be open" in server.FRONTEND_CONTROL_HELP["voice_training_agent"]


def test_pairing_guides_hide_protocol_command_names(monkeypatch):
    _bypass_login(monkeypatch)
    with TestClient(server.admin_app) as client:
        security_resp = client.get("/admin/guides/content", params={"id": "security-modes"})
        pair_resp = client.get("/admin/guides/content", params={"id": "local-pair-bluetooth"})
        architecture_resp = client.get("/admin/guides/content", params={"id": "architecture"})

    assert security_resp.status_code == 200
    assert pair_resp.status_code == 200
    assert architecture_resp.status_code == 200
    combined = security_resp.json()["html"] + pair_resp.json()["html"]
    assert "2FA setup key" in security_resp.json()["html"]
    assert "Secure Professional Maximus" in security_resp.json()["html"]
    assert "saved sessions" in security_resp.json()["html"]
    assert "/autopair" not in combined
    assert "/autopair_answer" not in combined
    assert "/otp" not in combined
    assert "/pair" not in combined
    assert "admin HTTP" not in combined
    assert "TOTP" not in security_resp.json()["html"]
    assert "otpauth://" not in security_resp.json()["html"]
    assert "Auth surface" not in architecture_resp.json()["html"]


def test_admin_shell_bluetooth_pair_copy_is_user_facing():
    script = (REPO_ROOT / "assets" / "admin-ui.js").read_text(encoding="utf-8")

    assert "exchange /autopair" not in script
    assert "BLE GATT service" not in script
    assert "admin HTTP port" not in script
    assert "Encrypts /otp" not in script
    assert "SSE connected" not in script
    assert "Restart cloud listener" not in script
    assert "Cloud listener restarted" not in script
    assert "cloud_command" not in script
    assert "Cloud command" not in script
    assert "/request_autopair" not in script
    assert "scripts/bluetooth_pair_host_bridge.py" not in script
    assert "Do not use localhost" not in script
    assert 'rails.push("SSE")' not in script
    assert 'rails.push("APNs ' not in script
    assert 'rails.push("FCM ' not in script
    assert "Owner " not in script
    assert "Authenticator secret" not in script
    assert "authenticator secret" not in script
    assert "otpauth URI" not in script
    assert "Manual setup key" in script
    assert "Authenticator setup link" in script


def test_admin_guides_content_unknown_id_is_404(monkeypatch):
    _bypass_login(monkeypatch)
    with TestClient(server.admin_app) as client:
        resp = client.get("/admin/guides/content", params={"id": "does-not-exist"})

    assert resp.status_code == 404


def test_guides_doc_standalone_page_renders(monkeypatch):
    _bypass_login(monkeypatch)
    with TestClient(server.admin_app) as client:
        resp = client.get("/guides/doc/troubleshooting")

    assert resp.status_code == 200
    assert "Troubleshooting" in resp.text
