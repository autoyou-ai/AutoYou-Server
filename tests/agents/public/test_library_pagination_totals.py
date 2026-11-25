"""Regression coverage for 100+ item library pagination and totals across agent backends."""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import autoyou_agents.music_agent.website.backend.app as audio_backend
import autoyou_agents.notes_agent.website.backend.app as notes_backend
import autoyou_agents.diary_agent.website.backend.app as diary_backend
from autoyou_agents.notes_agent.notes_tool import NotesTool
from autoyou_agents.page_agent.website.backend.app import PageFeedService
from autoyou_agents.shared_tools import scheduler_mission_control as mission_control
from autoyou_agents.media_agent.website.backend.app import create_app as create_tv_app
from page_feed_db import PageFeedDB
from shared import scheduler_service


def test_audio_library_reports_total_for_165_tracks(monkeypatch, tmp_path):
    tracks = [
        {
            "id": f"track-{idx:03d}",
            "title": f"Track {idx:03d}",
            "artist": "Synthetic Artist",
            "file_name": f"track-{idx:03d}.mp3",
            "folder": "Album",
        }
        for idx in range(165)
    ]
    by_id = {row["id"]: str(tmp_path / row["file_name"]) for row in tracks}
    monkeypatch.setattr(audio_backend, "_normalize_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(
        audio_backend,
        "_scan_tracks",
        lambda roots, limit: {
            "tracks": tracks[:limit],
            "by_id": by_id,
            "truncated": len(tracks) > limit,
        },
    )
    audio_backend._library_cache.update(
        {"roots": [], "tracks": [], "by_id": {}, "limit": 0, "truncated": False, "scanned": False}
    )

    client = TestClient(audio_backend.app)
    page1 = client.get("/api/library?offset=0&limit=100").json()
    page2 = client.get("/api/library?offset=100&limit=100").json()
    page1_cached = client.get("/api/library?offset=0&limit=100").json()

    assert page1["track_count"] == 100
    assert page1["total"] is None
    assert page1["has_more"] is True
    assert page1["next_offset"] == 100
    assert page2["track_count"] == 65
    assert page2["total"] == 165
    assert page2["has_more"] is False
    assert page2["next_offset"] is None
    assert page1_cached["total"] == 165
    assert page1_cached["has_more"] is True


def test_tv_library_pages_deterministically_beyond_100_items(tmp_path):
    app = create_tv_app(tmp_path / "tv", otp_verifier=lambda code: code == "123456")
    with TestClient(app) as client:
        store = client.app.state.store
        for idx in range(125):
            store.add_item(source_key=f"tv-item-{idx:03d}", title=f"Episode {idx:03d}")
        with store.connect() as db:
            db.execute("UPDATE items SET created = 1700000000.0, updated = 1700000000.0")

        p1 = client.get("/api/library?offset=0&limit=100").json()
        p2 = client.get(f"/api/library?offset={p1['next_offset']}&limit=100").json()

    assert p1["total"] == 125
    assert p1["has_more"] is True
    assert p1["next_offset"] == 100
    assert p2["total"] == 125
    assert p2["has_more"] is False
    assert p2["next_offset"] is None
    combined_ids = [row["id"] for row in p1["items"] + p2["items"]]
    assert len(combined_ids) == 125
    assert len(set(combined_ids)) == 125


def test_notes_listing_reports_total_and_cursor_pages(monkeypatch, tmp_path):
    tool = NotesTool(db_path=str(tmp_path / "notes.db"))
    for idx in range(125):
        tool.create_note(title=f"Note {idx:03d}", content=f"Synthetic body {idx:03d}", category="qa")

    monkeypatch.setattr(notes_backend, "load_agent_install_registry", lambda: {"installed_agents": ["notes_agent"]})
    monkeypatch.setattr(notes_backend, "get_notes_tool", lambda: tool)

    p1 = notes_backend.load_notes_listing_payload(limit=100, include_content=False)
    p2 = notes_backend.load_notes_listing_payload(limit=100, cursor=p1["next_cursor"], include_content=False)

    assert p1["success"] is True
    assert p1["total"] == 125
    assert p1["total_count"] == 125
    assert p1["has_more"] is True
    assert len(p1["notes"]) == 100
    assert p2["total"] == 125
    assert p2["has_more"] is False
    assert len(p2["notes"]) == 25
    ids = [row["id"] for row in p1["notes"] + p2["notes"]]
    assert len(set(ids)) == 125


def test_page_feed_catalog_reports_total_and_cursor_pages(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    service = PageFeedService()
    service.db = PageFeedDB(str(tmp_path / "feed.db"))
    for idx in range(120):
        service.db.insert("article", f"https://example.test/{idx:03d}", title=f"Feed Item {idx:03d}")

    app = FastAPI()
    service.register_routes(app)
    client = TestClient(app)

    p1 = client.get("/api/feed/catalog?limit=100&order=desc").json()
    p2 = client.get(f"/api/feed/catalog?limit=100&order=desc&cursor={p1['next_cursor']}").json()

    assert p1["total"] == 120
    assert p1["has_more"] is True
    assert len(p1["items"]) == 100
    assert p2["total"] == 120
    assert p2["has_more"] is False
    assert len(p2["items"]) == 20
    assert len({row["id"] for row in p1["items"] + p2["items"]}) == 120


def test_diary_supports_optional_offset_pagination_and_unpaginated_default(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(diary_backend, "_check_auth", lambda request: True)

    blocks = []
    for idx in range(120):
        entry_id = str(uuid.UUID(int=idx + 1))
        blocks.append(
            f"<!-- autoyou-diary:{entry_id} -->\n"
            "### 2026-11-14\n\n"
            "#### 10:00:00 UTC\n\n"
            f"##### Entry {idx:03d}\n\n"
            f"Body {idx:03d}\n"
            f"<!-- /autoyou-diary:{entry_id} -->"
        )
    client = TestClient(diary_backend.app)
    from autoyou_agents.diary_agent.agent import _store
    _store().write("# Diary\n\n" + "\n\n".join(blocks))

    unpaged = client.get("/api/diary/entries?order=desc").json()
    assert unpaged["total"] == 120
    assert unpaged["has_more"] is False
    assert len(unpaged["entries"]) == 120

    p1 = client.get("/api/diary/entries?order=desc&offset=0&limit=100").json()
    p2 = client.get(f"/api/diary/entries?order=desc&offset={p1['next_offset']}&limit=100").json()
    assert p1["total"] == 120
    assert p1["has_more"] is True
    assert p1["next_offset"] == 100
    assert len(p1["entries"]) == 100
    assert p2["total"] == 120
    assert p2["has_more"] is False
    assert p2["next_offset"] is None
    assert len(p2["entries"]) == 20
    assert len({row["id"] for row in p1["entries"] + p2["entries"]}) == 120


def test_scheduler_mission_control_dashboard_includes_total(monkeypatch, tmp_path):
    tasks_path = tmp_path / "cron_tasks.json"
    queue_path = tmp_path / "scheduled_notification_queue.json"
    monkeypatch.setattr(scheduler_service, "TASKS_FILE", str(tasks_path))
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    monkeypatch.setattr(mission_control, "TASKS_FILE", str(tasks_path))
    monkeypatch.setattr(mission_control, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    monkeypatch.setattr(mission_control, "_get_agent_security_settings", lambda _name: {"auth_mode": "open"})
    monkeypatch.setattr(mission_control, "_totp_capabilities", lambda: {"totp_configured": False})
    monkeypatch.setattr(mission_control, "_build_target_catalog", lambda: {"count": 0, "options": []})

    client = TestClient(mission_control.create_scheduler_mission_control_app("tasks"))
    for idx in range(5):
        created = client.post(
            "/api/items",
            json={
                "instruction": f"Synthetic scheduled check {idx}",
                "interval_minutes": 15,
                "enabled": True,
                "owner_key": "telegram:12345",
                "delivery_target": {"transport": "telegram", "chat_id": 12345},
            },
        )
        assert created.status_code == 200
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["total"] == 5
    assert len(dashboard["items"]) == 5
