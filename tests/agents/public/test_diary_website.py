"""Private Diary catalog, attachments and conditional edits."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_mod(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.delenv("AUTOYOU_DIARY_ENCRYPT", raising=False)
    return importlib.import_module("autoyou_agents.diary_agent.website.backend.app")


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
    for entry_id in (id1, id2):
        start = test_persona.index(f"<!-- autoyou-diary:{entry_id} -->")
        end = test_persona.index(f"<!-- /autoyou-diary:{entry_id} -->") + len(f"<!-- /autoyou-diary:{entry_id} -->")
        assert client.post("/api/diary/entries", json={"id": entry_id, "markdown": test_persona[start:end]}).status_code == 200

    # Test listing (default desc order)
    resp = client.get("/api/diary/entries")
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
    resp_asc = client.get("/api/diary/entries?order=asc")
    assert resp_asc.status_code == 200
    entries_asc = resp_asc.json()["entries"]
    assert entries_asc[0]["id"] == id1
    assert entries_asc[1]["id"] == id2

    # Test get single entry
    resp_single = client.get(f"/api/diary/entries/{id1}")
    assert resp_single.status_code == 200
    single = resp_single.json()["entry"]
    assert single["id"] == id1
    assert single["title"] == "Morning Coffee"
    assert single["body"] == "Espresso was great today."

    # Test get non-existent entry
    resp_404 = client.get("/api/diary/entries/33333333-3333-3333-3333-333333333333")
    assert resp_404.status_code == 404


def test_diary_media_range_reuses_decoded_cache_across_slices(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_check_auth", lambda request: True)
    app_mod._DIARY_MEDIA_CACHE.clear()
    entry_id = "11111111-1111-1111-1111-111111111111"
    media_id = "synthetic-clip"
    payload = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 400

    client = TestClient(app_mod.app)
    upload = client.post(
        f"/api/diary/entries/{entry_id}/media",
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
        f"/api/diary/entries/{entry_id}/media/{media_id}",
        headers={"Range": "bytes=0-99"},
    )
    r2 = client.get(
        f"/api/diary/entries/{entry_id}/media/{media_id}",
        headers={"Range": "bytes=100-199"},
    )

    assert r1.status_code == 206 and r1.content == payload[0:100]
    assert r2.status_code == 206 and r2.content == payload[100:200]
    assert len(reads) == 1
