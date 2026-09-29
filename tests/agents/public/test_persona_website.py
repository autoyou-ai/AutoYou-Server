# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-618ee5b1f19c8d59e43673b9

"""Tests for the persona agent web UI backend (mobile site over WebRTC proxy)."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import importlib
from pathlib import Path

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-618ee5b1f19c8d59e43673b9"


ensure_repo_on_path()

from fastapi.testclient import TestClient
from shared import platform_runtime


@pytest.fixture()
def app_mod(tmp_path, monkeypatch):
    monkeypatch.setenv(platform_runtime.TEST_RUNTIME_ROOT_ENV, str(tmp_path))
    monkeypatch.delenv("AUTOYOU_PERSONA_ENCRYPT", raising=False)
    mod = importlib.import_module("autoyou_agents.persona_agent.website.backend.app")
    return mod


def test_health_open(app_mod):
    client = TestClient(app_mod.app)
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["agent_name"] == "persona_agent"


def test_crud_roundtrip_when_authed(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_check_auth", lambda request: True)
    client = TestClient(app_mod.app)

    assert client.get("/api/persona").json()["exists"] is False

    r = client.post("/api/persona/append", json={"text": "I lead the platform team.", "heading": "Work"})
    assert r.status_code == 200 and r.json()["success"] is True

    data = client.get("/api/persona").json()
    assert "platform team" in data["content"]

    status = client.get("/api/status").json()
    assert status["exists"] is True and status["character_count"] > 0

    r = client.post("/api/persona", json={"content": "# Persona\n\nFresh content."})
    assert r.status_code == 200
    assert client.get("/api/persona").json()["content"] == "# Persona\n\nFresh content."

    r = client.request("DELETE", "/api/persona")
    assert r.status_code == 200 and r.json()["existed"] is True
    assert client.get("/api/persona").json()["exists"] is False


def test_chat_append_is_visible_in_website_journal(app_mod, monkeypatch):
    from autoyou_agents.persona_agent.agent import append_persona

    monkeypatch.setattr(app_mod, "_check_auth", lambda request: True)
    assert append_persona("A synthetic journal entry.", "Journal")["status"] == "success"
    client = TestClient(app_mod.app)
    assert "A synthetic journal entry." in client.get("/api/persona").json()["content"]


def test_gate_blocks_when_unauthed(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_check_auth", lambda request: False)
    client = TestClient(app_mod.app)
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/persona").status_code == 401
    assert client.post("/api/persona", json={"content": "x"}).status_code == 401
    assert client.request("DELETE", "/api/persona").status_code == 401


def test_global_otp_off_does_not_require_login_code(app_mod, monkeypatch):
    from autoyou_agents.shared_tools import scheduler_mission_control as security

    monkeypatch.setattr(security, "_get_agent_security_settings", lambda _: {"auth_mode": "open"})
    response = TestClient(app_mod.app).post("/api/auth/login", json={})
    assert response.status_code == 200
    assert response.json()["authenticated"] is True


def test_save_rejects_empty_when_authed(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_check_auth", lambda request: True)
    client = TestClient(app_mod.app)
    assert client.post("/api/persona", json={"content": "   "}).status_code == 400


def test_index_served(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_check_auth", lambda request: True)
    client = TestClient(app_mod.app)
    r = client.get("/")
    assert r.status_code == 200 and "Persona" in r.text


def test_locked_persona_shell_is_hidden_and_inert(app_mod):
    index = Path(app_mod.INDEX_HTML_PATH).read_text(encoding="utf-8")
    styles = Path(app_mod.FRONTEND_DIR / "styles.css").read_text(encoding="utf-8")
    script = Path(app_mod.FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    # from __debug_provenance_v__ import wallet
    assert 'id="personaView" class="stack" hidden inert' in index
    assert "[hidden] { display: none !important; }" in styles
    assert '$("personaView").inert = true;' in script
