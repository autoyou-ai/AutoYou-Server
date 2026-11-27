"""Private Diary catalog, attachments and conditional edits."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_mod(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.delenv("AUTOYOU_DIARY_ENCRYPT", raising=False)
    return importlib.import_module("autoyou_agents.diary_agent.website.backend.app")


def test_remote_diary_unlocks_with_the_existing_server_authenticator(app_mod, monkeypatch, tmp_path):
    import pyotp
    import server
    from autoyou_agents.shared_tools import scheduler_mission_control as security

    secret = pyotp.random_base32()
    monkeypatch.setattr(server.STATE, "config", {
        "security": {"mode": "secure_professional", "totp_secret": secret},
        "agent_ui_security": {"diary_agent": {"auth_mode": "totp"}},
    })
    monkeypatch.setattr(server, "agent_has_assigned_2fa_profile", lambda name: False)
    monkeypatch.setattr(security, "_runtime_server", lambda: server)
    monkeypatch.setattr(security, "_AGENT_UI_SESSIONS", {})
    monkeypatch.setattr(security, "_AGENT_UI_SESSIONS_FILE", str(tmp_path / "agent-sessions.json"))
    client = TestClient(app_mod.app, headers={
        "X-AutoYou-Remote-Browser": "1",
        "X-AutoYou-WebRTC-Session-Id": "synthetic-paired-session",
        "X-AutoYou-Remote-Access-Role": "admin",
    })

    auth = client.get("/api/auth/status").json()["auth"]
    assert auth["totp_configured"] is True
    assert auth["authenticated"] is False
    assert "Unlock Diary" in client.get("/").text
    assert client.get("/api/diary/entries").status_code == 401
    assert client.post("/api/auth/login", json={"totp_code": "invalid"}).status_code == 401

    login = client.post("/api/auth/login", json={"totp_code": server._totp_now(secret)})
    assert login.status_code == 200
    assert client.get("/api/diary/entries").status_code == 200
    client.cookies.clear()
    bearer = {"Authorization": f"Bearer {login.json()['token']}"}
    assert client.get("/api/diary/entries", headers=bearer).status_code == 200
    assert client.post("/api/auth/logout", headers=bearer).status_code == 200
    assert client.get("/api/diary/entries", headers=bearer).status_code == 401


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


def _synthetic_block(entry_id: str, date: str, title: str, body: str, time_label: str = "09:15:00 -08:00 (America/Los_Angeles)") -> str:
    return (
        f"<!-- autoyou-diary:{entry_id} -->\n"
        f"### {date}\n\n"
        f"#### {time_label}\n\n"
        f"##### {title}\n\n"
        f"{body}\n"
        f"<!-- /autoyou-diary:{entry_id} -->"
    )


def test_dayone_rich_metadata_roundtrip_and_legacy_mobile_preservation(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_check_auth", lambda request: True)
    client = TestClient(app_mod.app)
    entry_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    # Upload a synthetic photo and sketch first.
    assert client.post(
        f"/api/diary/entries/{entry_id}/media",
        headers={"X-AutoYou-Media-ID": "photo-1"},
        files={"file": ("trail.jpg", b"\xff\xd8\xff\xe0synthetic", "image/jpeg")},
    ).status_code == 200
    assert client.post(
        f"/api/diary/entries/{entry_id}/media",
        headers={"X-AutoYou-Media-ID": "sketch-1"},
        files={"file": ("sketch-2026.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png")},
    ).status_code == 200

    rich_details = {
        "captured_at": "2026-11-26T17:15:00Z",
        "time_zone": "America/Los_Angeles",
        "journal": "Travel",
        "tags": ["hiking", "coastal"],
        "favorite": True,
        "pinned": True,
        "mood": "Grateful",
        "activity": "Walking",
        "steps": 11240,
        "template": "Travel Log",
        "location": {"name": "Coastal Trail, Synthetic City", "city": "Synthetic City", "lat": 37.7749, "lng": -122.4194},
        "weather": {"condition": "Sunny", "temp_c": 19.5, "icon": "☀️", "humidity": 52},
        "media": [
            {"id": "photo-1", "name": "trail.jpg", "transcription": ""},
            {"id": "sketch-1", "name": "sketch-2026.png", "transcription": ""},
        ],
    }
    saved = client.post(
        "/api/diary/entries",
        json={
            "id": entry_id,
            "markdown": _synthetic_block(entry_id, "2026-11-26", "Coastal Walk", "- [x] Packed coffee\n- [ ] Sunset photos"),
            "details": rich_details,
            "base_revision": "",
        },
    )
    assert saved.status_code == 200, saved.text
    rev1 = saved.json()["revision"]

    # Catalog & single read expose rich Day One metadata.
    catalog_row = client.get("/api/diary/entries").json()["entries"][0]
    assert catalog_row["pinned"] is True
    assert catalog_row["mood"] == "Grateful"
    assert catalog_row["activity"] == "Walking"
    assert catalog_row["steps"] == 11240
    assert catalog_row["has_checklist"] is True
    assert catalog_row["media_counts"]["photo"] == 2
    assert catalog_row["media_counts"]["sketch"] == 1
    assert catalog_row["location"]["lat"] == 37.7749
    assert catalog_row["weather"]["temp_c"] == 19.5

    # Simulate an older mobile app updating body/title without knowing about location/weather/mood/pinned.
    legacy_mobile_details = {
        "captured_at": "2026-11-26T17:15:00Z",
        "time_zone": "America/Los_Angeles",
        "journal": "Travel",
        "tags": ["hiking", "coastal"],
        "favorite": True,
        "media": [
            {"id": "photo-1", "name": "trail.jpg", "transcription": ""},
            {"id": "sketch-1", "name": "sketch-2026.png", "transcription": ""},
        ],
    }
    updated = client.post(
        "/api/diary/entries",
        json={
            "id": entry_id,
            "markdown": _synthetic_block(entry_id, "2026-11-26", "Coastal Walk (Edited on Phone)", "Updated body from mobile."),
            "details": legacy_mobile_details,
            "base_revision": rev1,
        },
    )
    assert updated.status_code == 200, updated.text
    rev2 = updated.json()["revision"]

    fetched = client.get(f"/api/diary/entries/{entry_id}").json()["entry"]
    assert fetched["title"] == "Coastal Walk (Edited on Phone)"
    assert fetched["location"]["name"] == "Coastal Trail, Synthetic City"
    assert fetched["weather"]["condition"] == "Sunny"
    assert fetched["mood"] == "Grateful"
    assert fetched["activity"] == "Walking"
    assert fetched["steps"] == 11240
    assert fetched["pinned"] is True

    # Explicit null/empty clears metadata cleanly.
    cleared = client.post(
        "/api/diary/entries",
        json={
            "id": entry_id,
            "markdown": _synthetic_block(entry_id, "2026-11-26", "Coastal Walk (Edited on Phone)", "Cleared extras."),
            "details": {**legacy_mobile_details, "location": None, "weather": None, "mood": "", "pinned": False, "steps": None},
            "base_revision": rev2,
        },
    )
    assert cleared.status_code == 200, cleared.text
    after_clear = client.get(f"/api/diary/entries/{entry_id}").json()["entry"]
    assert after_clear["location"] is None
    assert after_clear["weather"] is None
    assert after_clear["mood"] == ""
    assert after_clear["pinned"] is False
    assert after_clear["steps"] is None

    # Deleting one attachment removes only that media file.
    del_media = client.delete(f"/api/diary/entries/{entry_id}/media/sketch-1")
    assert del_media.status_code == 200
    assert client.get(f"/api/diary/entries/{entry_id}/media/sketch-1").status_code == 404
    assert client.get(f"/api/diary/entries/{entry_id}/media/photo-1").status_code == 200


def test_dayone_workspace_journals_templates_preferences_streaks_and_export(app_mod, monkeypatch):
    import datetime as dt

    monkeypatch.setattr(app_mod, "_check_auth", lambda request: True)
    client = TestClient(app_mod.app)
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)
    last_year = today.replace(year=today.year - 1) if not (today.month == 2 and today.day == 29) else today - dt.timedelta(days=365)

    sync_before = client.get("/api/diary/sync/state").json()
    assert sync_before["total"] == 0

    # Publish 3 synthetic entries across journals (today, yesterday, and 1 year ago for On This Day).
    entries = [
        ("bbbbbbbb-bbbb-4bbb-8bbb-000000000001", today.isoformat(), "Today Reflection", "Great focus session today.", "Work", ["focus"]),
        ("bbbbbbbb-bbbb-4bbb-8bbb-000000000002", yesterday.isoformat(), "Yesterday Notes", "Walked by the park.", "", ["walk"]),
        ("bbbbbbbb-bbbb-4bbb-8bbb-000000000003", last_year.isoformat(), "A Year Ago", "Memorable synthetic anniversary.", "Work", ["milestone"]),
    ]
    for eid, day_iso, title, body, journal_name, tags in entries:
        res = client.post(
            "/api/diary/entries",
            json={
                "id": eid,
                "markdown": _synthetic_block(eid, day_iso, title, body),
                "details": {"journal": journal_name, "tags": tags, "favorite": False, "media": []},
            },
        )
        assert res.status_code == 200, res.text

    sync_after = client.get("/api/diary/sync/state").json()
    assert sync_after["total"] == 3
    assert sync_after["catalog_hash"] != sync_before["catalog_hash"]

    # Workspace auto-discovers "Work" journal and computes streak + On This Day.
    ws = client.get("/api/diary/workspace").json()
    journal_names = {j["name"] for j in ws["journals"]}
    assert {"Daily Journal", "Work"} <= journal_names
    assert ws["insights"]["current_streak"] >= 2
    assert ws["insights"]["longest_streak"] >= 2
    assert ws["insights"]["total_entries"] == 3
    if last_year.month == today.month and last_year.day == today.day:
        assert ws["insights"]["on_this_day_count"] == 1

    # Rename journal "Work" -> "Career" and exclude it from streaks.
    renamed = client.post(
        "/api/diary/journals",
        json={"name": "Career", "rename_from": "Work", "color": "#8B5CF6", "streak_included": False},
    )
    assert renamed.status_code == 200, renamed.text
    reloaded_entry = client.get("/api/diary/entries/bbbbbbbb-bbbb-4bbb-8bbb-000000000001").json()["entry"]
    assert reloaded_entry["journal"] == "Career"
    ws_after_excl = client.get("/api/diary/workspace").json()
    # Yesterday's Daily Journal entry still counts, today's Career entry is excluded from streak.
    assert ws_after_excl["insights"]["current_streak"] == 1

    # Custom templates CRUD; built-in template cannot be deleted.
    tpl = client.post(
        "/api/diary/templates",
        json={"name": "Synthetic Prompt", "title": "Prompt", "body": "### Prompt\n", "tags": ["prompt"], "color": "#2FB67C"},
    )
    assert tpl.status_code == 200, tpl.text
    tid = tpl.json()["template"]["id"]
    assert any(t["id"] == tid for t in client.get("/api/diary/templates").json()["templates"])
    assert client.delete("/api/diary/templates/five-minute-am").status_code == 400
    assert client.delete(f"/api/diary/templates/{tid}").status_code == 200

    # Preferences persist cleanly.
    pref_res = client.put("/api/diary/preferences", json={"temp_unit": "C", "font_family": "serif", "daily_word_goal": 500})
    assert pref_res.status_code == 200
    assert client.get("/api/diary/preferences").json()["preferences"]["temp_unit"] == "C"

    # Exports in Day One JSON, Markdown, and Plain Text formats.
    exp_json = client.get("/api/diary/export?format=dayone_json")
    assert exp_json.status_code == 200 and exp_json.json()["metadata"]["version"] == "1.0"
    assert len(exp_json.json()["entries"]) == 3
    exp_md = client.get("/api/diary/export?format=markdown&journal=Career")
    assert exp_md.status_code == 200 and "Today Reflection" in exp_md.text and "Yesterday Notes" not in exp_md.text
    exp_txt = client.get("/api/diary/export?format=text")
    assert exp_txt.status_code == 200 and "Yesterday Notes" in exp_txt.text

    # Delete journal moves its entries back to Daily Journal without losing them.
    del_j = client.delete("/api/diary/journals/Career")
    assert del_j.status_code == 200
    assert client.get("/api/diary/entries/bbbbbbbb-bbbb-4bbb-8bbb-000000000001").json()["entry"]["journal"] == ""


def test_dayone_write_endpoints_enforce_role_and_auth_boundaries(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_check_auth", lambda request: False)
    client = TestClient(app_mod.app)
    for method, path in (
        ("GET", "/api/diary/workspace"),
        ("GET", "/api/diary/sync/state"),
        ("GET", "/api/diary/journals"),
        ("GET", "/api/diary/templates"),
        ("GET", "/api/diary/preferences"),
        ("GET", "/api/diary/export"),
    ):
        assert client.request(method, path).status_code == 401

    # Paired viewer cannot mutate journals, templates, or preferences.
    monkeypatch.setattr(app_mod, "_check_auth", lambda request: True)
    viewer = TestClient(app_mod.app, headers={"X-AutoYou-Remote-Browser": "1", "X-AutoYou-Remote-Access-Role": "viewer"})
    assert viewer.post("/api/diary/journals", json={"name": "Nope"}).status_code == 403
    assert viewer.post("/api/diary/templates", json={"name": "Nope"}).status_code == 403
    assert viewer.put("/api/diary/preferences", json={"temp_unit": "C"}).status_code == 403
    assert viewer.get("/api/diary/workspace").json()["permissions"]["can_edit"] is False

