# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-618ee5b1f19c8d59e43673b9

"""Tests for the persona agent web UI backend (mobile site over WebRTC proxy)."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


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


def test_list_and_get_diary_entries_catalog(app_mod, monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_check_auth", lambda request: True)
    id1 = "11111111-1111-1111-1111-111111111111"
    id2 = "22222222-2222-2222-2222-222222222222"
    test_persona = (
        "# Persona Document\n\n"
        "Some general notes.\n\n"
        f"<!-- autoyou-diary:{id1} -->\n"
        "### 2026-10-31\n\n"
        "#### 10:15:00 UTC\n\n"
        "##### Morning Coffee\n\n"
        "Espresso was great today.\n"
        f"<!-- /autoyou-diary:{id1} -->\n\n"
        f"<!-- autoyou-diary:{id2} -->\n"
        "### 2026-11-01\n\n"
        "#### 18:30:00 UTC\n\n"
        "##### Evening Reflection\n\n"
        "Wrote some code.\n"
        f"<!-- /autoyou-diary:{id2} -->\n"
    )
    client = TestClient(app_mod.app)
    r = client.post("/api/persona", json={"content": test_persona})
    assert r.status_code == 200

    # Test listing (default desc order)
    resp = client.get("/api/persona/entries")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) == 2
    assert entries[0]["id"] == id2
    assert entries[0]["date"] == "2026-11-01"
    assert entries[0]["time"] == "18:30:00 UTC"
    assert entries[0]["title"] == "Evening Reflection"
    assert entries[0]["preview"] == "Wrote some code."
    assert entries[1]["id"] == id1

    # Test listing asc order
    resp_asc = client.get("/api/persona/entries?order=asc")
    assert resp_asc.status_code == 200
    entries_asc = resp_asc.json()["entries"]
    assert entries_asc[0]["id"] == id1
    assert entries_asc[1]["id"] == id2

    # Test get single entry
    resp_single = client.get(f"/api/persona/entries/{id1}")
    assert resp_single.status_code == 200
    single = resp_single.json()["entry"]
    assert single["id"] == id1
    assert single["title"] == "Morning Coffee"
    assert single["body"] == "Espresso was great today."

    # Test get non-existent entry
    resp_404 = client.get("/api/persona/entries/33333333-3333-3333-3333-333333333333")
    assert resp_404.status_code == 404


def test_diary_media_range_reuses_decoded_cache_across_slices(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_check_auth", lambda request: True)
    app_mod._DIARY_MEDIA_CACHE.clear()
    entry_id = "11111111-1111-1111-1111-111111111111"
    media_id = "synthetic-clip"
    payload = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 400

    client = TestClient(app_mod.app)
    upload = client.post(
        f"/api/persona/entries/{entry_id}/media",
        headers={"X-AutoYou-Media-ID": media_id},
        files={"file": ("clip.mp4", payload, "video/mp4")},
    )
    assert upload.status_code == 200

    reads = []
    real_store_fn = app_mod.diary_media_store

    def _counting_store(eid, mid):
        store = real_store_fn(eid, mid)
        orig_read = store.read

        def _read():
            reads.append((eid, mid))
            return orig_read()

        store.read = _read
        return store

    monkeypatch.setattr(app_mod, "diary_media_store", _counting_store)

    r1 = client.get(
        f"/api/persona/entries/{entry_id}/media/{media_id}",
        headers={"Range": "bytes=0-99"},
    )
    r2 = client.get(
        f"/api/persona/entries/{entry_id}/media/{media_id}",
        headers={"Range": "bytes=100-199"},
    )

    assert r1.status_code == 206 and r1.content == payload[0:100]
    assert r2.status_code == 206 and r2.content == payload[100:200]
    assert len(reads) == 1
