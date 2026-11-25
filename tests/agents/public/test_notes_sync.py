"""Synthetic notes exercise the actual agent, its website middleware and SQLite storage."""
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from autoyou_agents.notes_agent import sync
from autoyou_agents.notes_agent.notes_tool import NotesTool
from autoyou_agents.notes_agent.website.backend import app as site
from autoyou_agents.shared_tools import scheduler_mission_control as auth
from shared.content_library import ContentLibrary

KEY = "0123456789abcdef" * 2
PATH = "/api/sync/notes/" + KEY
HEADERS = {"X-AutoYou-Remote-Browser": "webrtc", "X-AutoYou-Agent-Frontend": "notes_agent",
           "X-AutoYou-WebRTC-Session-Id": "synthetic-session", "X-AutoYou-Remote-Access-Role": "admin"}


def edit(**changes):
    return {"title": "Field log", "content": "A synthetic observation", "category": "Garden",
            "tags": ["Basil", "Seeds"], "base_revision": 0, "mutation": "synthetic-edit", "media": [], **changes}


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    tool = NotesTool(str(tmp_path / "notes.db"))
    monkeypatch.setattr(site, "notes_agent_installed", lambda: True)
    monkeypatch.setattr(site, "get_notes_tool", lambda: tool)
    monkeypatch.setattr(auth, "_describe_auth_state", lambda *_: {"authenticated": True, "via": "open"})
    with TestClient(site.app, client=("127.0.0.1", 1234), headers=HEADERS) as client:
        yield client, tool


def test_legacy_clock_incremental_union_and_competing_edits(pair):
    client, tool = pair
    a = tool.create_note("Field log", "From computer")
    page = client.get("/api/sync/changes", params={"limit": 1}).json()
    first = page["changes"][0]
    assert first["id"] == a["note_id"]
    assert first["key"] != KEY
    created = client.post(PATH, json=edit()).json()
    assert client.post(PATH, json=edit()).json() == created
    assert client.get("/api/sync/changes", params={"after": first["revision"]}).json()["changes"][0]["key"] == KEY
    assert len(tool.list_notes()) == 2
    assert client.get("/api/sync/changes", params={"limit": 1}).json()["has_more"]
    def save(n):
        return client.post(PATH, json=edit(content=f"revision {n}", mutation=f"m{n}", base_revision=created["revision"]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    current = next(r.json() for r in results if r.status_code == 200)
    assert client.post(f"/api/notes/{created['id']}/update", json={"category": "Herbs", "tags": ["Mint"]}).status_code == 200
    revised = client.get(PATH).json()
    assert revised["revision"] > current["revision"] and revised["tags"] == ["Mint"] and revised["category"] == "Herbs"
    assert client.delete(PATH, params={"revision": current["revision"]}).status_code == 409
    assert client.delete(PATH, params={"revision": revised["revision"]}).status_code == 200
    assert client.delete(PATH, params={"revision": revised["revision"]}).status_code == 200
    assert client.get("/api/sync/changes", params={"after": revised["revision"]}).json()["changes"][0]["deleted"] == 1
    assert client.post(PATH, json=edit()).status_code == 409
    assert tool.get_note(first["id"])["content"] == "From computer"
    assert len(ContentLibrary().list(source="notes_agent")["items"]) == 1


def test_partial_attachment_retry_atomic_note_and_scoped_references(pair):
    client, tool = pair
    data = b"a" * sync.CHUNK + b"end"
    digest = hashlib.sha256(data).hexdigest()
    path = f"{PATH}/media/{digest}"
    params = {"total": len(data), "filename": "example.txt", "offset": 0}
    body = edit(media=[{"digest": digest, "client_media_id": "synthetic-media", "filename": "example.txt", "mimetype": "text/plain"}])
    for _ in range(2):
        assert client.put(path, params=params, content=data[:sync.CHUNK]).json() == {"offset": sync.CHUNK}
    assert client.post(PATH, json=body).status_code == 400
    assert tool.list_notes() == []
    assert client.put(path, params={**params, "offset": sync.CHUNK}, content=b"bad").status_code == 400
    assert client.put(path, params=params, content=data[:sync.CHUNK]).status_code == 200
    assert client.put(path, params={**params, "offset": sync.CHUNK}, content=b"end").json() == {"offset": len(data)}
    created = client.post(PATH, json=body).json()
    assert len(created["media"]) == 1 and "path" not in created["media"][0]
    attachment = created["media"][0]["id"]
    assert client.get(f"/api/media/{attachment}", headers={"Range": f"bytes={sync.CHUNK}-"}).content == b"end"
    assert client.post(PATH, json=body).json() == created
    assert client.post(PATH[:-1] + "0", json=edit(media=[{"id": attachment}])).status_code == 400
    updated = client.post(PATH, json=edit(title="Edited", media=[{"id": attachment}], mutation="edit-2", base_revision=created["revision"])).json()
    assert updated["media"] == created["media"]
    empty = client.post(PATH, json=edit(media=[], mutation="edit-3", base_revision=updated["revision"])).json()
    assert empty["media"] == []
    assert client.get(f"/api/notes/{empty['id']}").json()["note"]["attachments"]["total"] == 0


def test_auth_roles_install_and_paired_only(pair, monkeypatch):
    client, tool = pair
    with TestClient(site.app, client=("192.0.2.10", 1234), headers=HEADERS) as unrelated:
        assert unrelated.get("/api/sync/changes").status_code == 403
    assert client.get("/api/sync/changes", headers={"X-AutoYou-WebRTC-Session-Id": ""}).status_code == 403
    assert client.get("/api/sync/changes", headers={"X-AutoYou-Remote-Access-Role": "viewer"}).status_code == 200
    assert client.post(PATH, json=edit(), headers={"X-AutoYou-Remote-Access-Role": "viewer"}).status_code == 403
    assert client.delete(PATH, params={"revision": 1}, headers={"X-AutoYou-Remote-Access-Role": "editor"}).status_code == 403
    assert client.post(PATH, json=edit(), headers={"Origin": "https://unrelated.example"}).status_code == 403
    monkeypatch.setattr(site, "notes_agent_installed", lambda: False)
    assert client.get("/api/sync/changes").status_code == 409
    monkeypatch.setattr(auth, "_describe_auth_state", lambda *_: {"authenticated": False})
    assert client.get("/api/sync/changes").status_code == 401
    assert tool.list_notes() == []


def test_migrate_existing_database_preserves_ids_and_stable_keys(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,content TEXT,tags TEXT,category TEXT,created_at TEXT,updated_at TEXT)")
        db.executemany("INSERT INTO notes(id,title,content) VALUES(?,?,?)", [(2, "Same", "A"), (7, "Same", "B")])
    tool = NotesTool(str(path))
    before = sync.changes(tool, 0, 10)
    assert [row["id"] for row in before["changes"]] == [2, 7]
    assert sync.changes(NotesTool(str(path)), 0, 10) == before


def test_offline_delete_survives_delayed_create_and_distinguishes_a_new_editor(pair):
    client, tool = pair
    assert client.delete(PATH, params={"revision": 0, "pending_mutation": "synthetic-edit"}).status_code == 200
    assert client.post(PATH, json=edit()).status_code == 409
    assert client.get(PATH).json()["deleted"] == 1
    assert not tool.list_notes()
    other = "/api/sync/notes/" + "b" * 32
    note = client.post(other, json=edit()).json()
    assert client.delete(other, params={"revision": 0, "pending_mutation": "someone-else"}).status_code == 409
    assert client.delete(other, params={"revision": 0, "pending_mutation": note["mutation"]}).status_code == 200


def test_sealed_notes_media_catalog_links_and_native_metadata(pair, tmp_path, monkeypatch):
    from shared import secure_storage
    client, tool = pair
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=tmp_path, password="synthetic-notes-password")
    try:
        data = b"Example attachment in a synthetic garden"
        digest = hashlib.sha256(data).hexdigest()
        assert client.put(PATH + "/media/" + digest, params={"offset": 0, "total": len(data), "filename": "mint.txt"}, content=data).status_code == 200
        saved = client.post(PATH, json=edit(created_ms=1710000000123, updated_ms=1710000000456, url="https://example.test/mint",
            media=[{"digest": digest, "client_media_id": "mint", "filename": "mint.txt", "mimetype": "text/plain", "transcription": "Mint"}])).json()
        assert saved["created_ms"] == 1710000000123 and saved["updated_ms"] == 1710000000456
        media, = saved["media"]
        assert media["digest"] == digest and media["transcription"] == "Mint" and saved["url"].endswith("/mint")
        rows = ContentLibrary().list(source="notes_agent")["items"]
        parent = next(r for r in rows if r["kind"] == "note")
        child = next(r for r in rows if r["parent_id"] == parent["id"])
        assert child["source_labels"] == ["Basil", "Seeds"] and child["source_collection"] == "Garden"
        with sqlite3.connect(tool._db_path) as conn:
            path, = conn.execute("SELECT path FROM media_attachments WHERE id=?", (media["id"],)).fetchone()
        from pathlib import Path
        assert data not in Path(path).read_bytes()
        assert client.get("/api/media/" + str(media["id"]), headers={"Range": "bytes=0-6"}).content == data[:7]
    finally:
        secure_storage.disable_secure_storage()


def test_computer_owner_can_pause_paired_replication(pair):
    from shared.content_library import configure_organization
    client, tool = pair
    configure_organization(auto_organize=True, semantic_labels=True, notes_sync_enabled=False)
    assert client.get("/api/sync/changes").status_code == 403
    assert client.post(PATH, json=edit()).status_code == 403
    assert not tool.list_notes()
    configure_organization(auto_organize=True, semantic_labels=True, notes_sync_enabled=True)
    assert client.post(PATH, json=edit()).status_code == 200
