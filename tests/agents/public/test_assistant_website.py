# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The Assistant website: gated by default, device links, listening and replies."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from autoyou_agents.shared_tools import scheduler_mission_control as smc
from shared import assistant_device_access as access
from shared import assistant_mode as am
from shared.remote_access_policy import remote_http_request_allowed

ROOT = Path(__file__).resolve().parents[3]
ADMIN_UI_JS = ROOT / "assets" / "admin-ui.js"
WEBSITE = ROOT / "autoyou_agents" / "assistant_agent" / "website"
ORIGIN = {"Origin": "http://testserver"}


def _server(config):
    return SimpleNamespace(
        STATE=SimpleNamespace(config=config),
        _default_config=lambda: config,
        _is_logged_in=lambda request: False,
        _persist_state_config=lambda cfg: None,
        _describe_totp_capabilities=lambda cfg: {"totp_configured": True},
        _get_pairing_totp_secret=lambda cfg=None: "JBSWY3DPEHPK3PXP",
        _verify_totp_secret=lambda secret, code: False,
        agent_has_assigned_2fa_profile=lambda name: False,
        get_configured_server_name=lambda: "Jarvis",
        _admin_ui_js_file=lambda: ADMIN_UI_JS,
        WEBRTC=None,
    )


@pytest.fixture
def site(tmp_path, monkeypatch):
    config = {"agent_ui_security": {}, "agent_websites": {}}
    monkeypatch.setenv(am.ASSISTANT_MODE_DIR_ENV, str(tmp_path / "assistant_mode"))
    monkeypatch.setattr(smc, "_AGENT_UI_SESSIONS_FILE", str(tmp_path / "agent_ui_sessions.json"))
    monkeypatch.setattr(smc, "_AGENT_UI_SESSIONS", {})
    monkeypatch.setattr(smc, "_runtime_server", lambda: _server(config))
    from autoyou_agents.assistant_agent.website.backend import app as backend

    monkeypatch.setattr(backend, "_FALLBACK_ARBITER", None)
    # Never load real speech models in unit tests.
    monkeypatch.setattr(backend, "_WARM_UP_STARTED", False)
    monkeypatch.setattr(backend, "warm_up_speech", lambda speak: None)
    client = TestClient(backend.app)
    return SimpleNamespace(client=client, backend=backend, config=config)


def _open(site):
    site.config["agent_ui_security"]["assistant_agent"] = {"auth_mode": "open"}


def test_manifest_is_gated_by_default_and_registered():
    manifest = json.loads((WEBSITE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["agent_name"] == "assistant_agent"
    assert manifest["auth_default"] == "gated"
    assert manifest["bypass_global_otp"] is False
    server_source = (ROOT / "server.py").read_text(encoding="utf-8")
    assert '"app_import": "autoyou_agents.assistant_agent.website.backend.app:app"' in server_source


def test_page_and_api_need_the_code_by_default(site):
    page = site.client.get("/")
    assert page.status_code == 200
    assert 'id="autoyou-auth-gate"' in page.text
    assert '"required": true' in page.text and '"authenticated": false' in page.text
    assert site.client.get("/api/assistant/status").status_code == 401
    assert site.client.post("/api/assistant/turn", json={"text": "hi"}, headers=ORIGIN).status_code == 401
    assert site.client.get("/engine/wake-word.js").status_code == 401


def test_status_with_otp_turned_off_reports_defaults(site):
    _open(site)
    data = site.client.get("/api/assistant/status").json()
    assert data["success"] is True
    assert data["enabled"] is True  # on by default
    assert data["auto_start"] is True and data["speak_replies"] is True and data["follow_up"] is True
    assert data["wake_phrase"] == "Jarvis"
    assert data["listening"] == "needs_training"
    assert data["access"] == "open"


def test_web_settings_normalise_and_save(site):
    saved = am.save_settings({"web_auto_start": "off", "web_speak_replies": 0, "web_follow_up": "yes"})
    assert saved["web_auto_start"] is False and saved["web_speak_replies"] is False and saved["web_follow_up"] is True
    _open(site)
    data = site.client.get("/api/assistant/status").json()
    assert data["auto_start"] is False and data["speak_replies"] is False


def test_device_link_becomes_the_session_cookie_and_leaves_the_address(site):
    token = access.create_device_token(7)
    response = site.client.get("/?device_token=" + token + "&headless=1", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "./?headless=1"
    assert token in response.headers.get("set-cookie", "")
    status = site.client.get("/api/assistant/status")
    assert status.status_code == 200

    other = TestClient(site.backend.app)
    bad = other.get("/?device_token=not-a-real-token", follow_redirects=False)
    assert bad.status_code == 303 and "set-cookie" not in bad.headers
    assert other.get("/api/assistant/status").status_code == 401


def test_device_links_open_only_the_assistant_website():
    token = "x" * 43
    cookie = {"autoyou_assistant_agent_chat_session": token}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(access, "device_token_valid", lambda value: value == token)
        assert access.admits("/agent/assistant_agent/", cookie, {}, "GET")
        assert access.admits("/agent/assistant_agent/api/assistant/turn", cookie, {}, "POST")
        assert access.admits("/agent/assistant_agent/", {}, {"device_token": token}, "GET")
        assert not access.admits("/agent/assistant_agent/api/assistant/turn", {}, {"device_token": token}, "POST")
        assert not access.admits("/agent/notes_agent/", cookie, {}, "GET")
        assert not access.admits("/agent/assistant_agent_evil/", cookie, {}, "GET")
        assert not access.admits("/agent/assistant_agent/", {}, {}, "GET")


def test_viewer_role_may_ask_but_nothing_else_is_widened():
    for name in ("wake", "transcribe", "turn", "speak"):
        assert remote_http_request_allowed("viewer", "POST", "/agent/assistant_agent/api/assistant/" + name)
    assert not remote_http_request_allowed("viewer", "POST", "/agent/assistant_agent/api/chat")
    assert not remote_http_request_allowed("viewer", "POST", "/agent/assistant_agent/api/security")
    assert not remote_http_request_allowed("viewer", "POST", "/agent/notes_agent/api/assistant/turn")


def test_turn_asks_the_assistant_agent_as_an_assistant_mode_turn(site, monkeypatch):
    _open(site)
    seen = {}

    async def fake_ask(text, *, session_id, wake):
        seen.update(text=text, session_id=session_id, wake=wake)
        return {"response": "It is **sunny**.", "agent_name": "assistant_agent"}

    monkeypatch.setattr(site.backend, "ask_assistant", fake_ask)
    ok = site.client.post("/api/assistant/turn", json={"text": "  what's the   weather ", "session_id": "s0123456789", "wake": "voice"}, headers=ORIGIN)
    assert ok.status_code == 200
    assert ok.json()["reply"] == "It is **sunny**."
    assert seen == {"text": "what's the weather", "session_id": "assistant-web-s0123456789", "wake": "voice"}
    assert site.client.post("/api/assistant/turn", json={"text": "hi", "session_id": "../x"}, headers=ORIGIN).status_code == 400
    assert site.client.post("/api/assistant/turn", json={"text": "", "session_id": "s0123456789"}, headers=ORIGIN).status_code == 400


def test_turn_payload_marks_the_assistant_turn(monkeypatch):
    from autoyou_agents.assistant_agent.website.backend import app as backend

    captured = {}

    class FakeResponse:
        status = 200

        async def json(self, content_type=None):
            return {"response": "ok"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, url, json=None, headers=None, timeout=None):
            captured.update(url=url, json=json)
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    import aiohttp
    import asyncio

    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    asyncio.run(backend.ask_assistant("open example.com", session_id="assistant-web-abc12345", wake="voice"))
    metadata = captured["json"]["metadata"]
    assert captured["url"].endswith("/api/chat")
    assert metadata["assistant_mode"] is True
    assert metadata["assistant_turn"]["text"] == "open example.com"
    assert metadata["assistant_turn"]["wake"] == "voice"


def test_speak_returns_wav_or_asks_for_the_browser_voice(site, monkeypatch):
    _open(site)
    monkeypatch.setattr(site.backend, "synthesize_speech", lambda text: (b"RIFF0000WAVE", "audio/wav"))
    spoken = site.client.post("/api/assistant/speak", json={"text": "Hello there."}, headers=ORIGIN)
    assert spoken.status_code == 200 and spoken.headers["content-type"] == "audio/wav"
    assert spoken.content.startswith(b"RIFF")
    monkeypatch.setattr(site.backend, "synthesize_speech", lambda text: None)
    off = site.client.post("/api/assistant/speak", json={"text": "Hello there."}, headers=ORIGIN)
    assert off.status_code == 503 and off.json()["fallback"] == "browser"


def test_voice_wake_word_never_falls_back_to_text_spotting(site):
    _open(site)
    import base64
    from shared import wake_word as ww

    audio = base64.b64encode(ww.pcm16_to_wav_bytes(b"\0\0" * 8000)).decode()
    refused = site.client.post("/api/assistant/transcribe", json={"audio_base64": audio, "spot_wake": True, "listener_id": "abc"}, headers=ORIGIN)
    assert refused.status_code == 409


def test_wake_claims_are_shared_and_need_a_listener(site):
    _open(site)
    first = site.client.post("/api/assistant/wake", json={"listener_id": "kitchen"}, headers=ORIGIN).json()
    second = site.client.post("/api/assistant/wake", json={"listener_id": "hallway"}, headers=ORIGIN).json()
    assert first["accepted"] is True and second["accepted"] is False
    assert site.client.post("/api/assistant/wake", json={"listener_id": "../bad"}, headers=ORIGIN).status_code == 400


def test_the_page_reuses_the_admin_wake_word_engine(site):
    _open(site)
    response = site.client.get("/engine/wake-word.js")
    assert response.status_code == 200
    assert "var AutoYouWakeWord = (function" in response.text
    assert response.text.rstrip().endswith("window.AutoYouWakeWord = AutoYouWakeWord;\n})();")
    node = shutil.which("node")
    if node:
        script = response.text + "\nif (typeof window.AutoYouWakeWord.Detector !== 'function') process.exit(3);"
        result = subprocess.run([node, "-e", "var window = {};" + script], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr


def test_frontend_never_loads_anything_from_the_internet():
    for path in (WEBSITE / "frontend").rglob("*"):
        if path.suffix in {".html", ".js", ".css"}:
            text = path.read_text(encoding="utf-8")
            assert "http://" not in text and "https://" not in text, path


def test_the_first_page_load_warms_speech_once(site, monkeypatch):
    import time as _time

    _open(site)
    calls = []
    monkeypatch.setattr(site.backend, "warm_up_speech", lambda speak: calls.append(speak))
    assert site.client.get("/api/assistant/status").status_code == 200
    assert site.client.get("/api/assistant/status").status_code == 200
    deadline = _time.time() + 5
    while not calls and _time.time() < deadline:
        _time.sleep(0.05)
    assert calls == [True]
