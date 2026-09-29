# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-307bcfdcb2317ef9a869452b


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import base64
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient
import autoyou_agents.notes_agent.website.backend.app as notes_frontend_backend
from autoyou_agents.notes_agent.notes_tool import NotesTool
from autoyou_agents.shared_tools import scheduler_mission_control

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-307bcfdcb2317ef9a869452b"


_ADTS_AAC = b"\xff\xf1\x50\x80\x00\xff\xfc"


@pytest.fixture(autouse=True)
def _authenticated_notes_website(monkeypatch):
    monkeypatch.setattr(
        scheduler_mission_control,
        "_describe_auth_state",
        lambda request, agent_name: {"required": True, "authenticated": True},
    )


class _FakeNotesTool:
    def __init__(self):
        self._notes = {
            2: {
                "id": 2,
                "title": "Newest note",
                "content": "A" * 240,
                "tags": ["recent", "mobile"],
                "category": "journal",
                "metadata": {"source": "test"},
                "created_at": "2026-03-11 10:00:00",
                "updated_at": "2026-03-11 10:30:00",
            },
            1: {
                "id": 1,
                "title": "Older note",
                "content": "Short body",
                "tags": ["older"],
                "category": "archive",
                "metadata": {},
                "created_at": "2026-03-10 09:00:00",
                "updated_at": "2026-03-10 09:15:00",
            },
        }

    def list_notes(
        self,
        limit=200,
        include_content=True,
        include_metadata=True,
        query=None,
        category=None,
        cursor_updated_at=None,
        cursor_note_id=None,
        descending=True,
    ):
        notes = [self._notes[2], self._notes[1]]
        if category:
            notes = [note for note in notes if note["category"] == category]
        return (notes if descending else notes[::-1])[:limit]

    def count_notes(self, query=None, category=None):
        return len([note for note in self._notes.values() if not category or note["category"] == category])

    def list_categories(self, limit=50):
        return [{"name": "archive", "count": 1}, {"name": "journal", "count": 1}][:limit]

    def get_note(self, note_id):
        return self._notes.get(int(note_id))


def test_notes_frontend_listing_and_detail(monkeypatch):
    monkeypatch.setattr(
        notes_frontend_backend,
        "load_agent_install_registry",
        lambda: {"installed_agents": ["notes_agent"]},
    )
    monkeypatch.setattr(notes_frontend_backend, "get_notes_tool", lambda: _FakeNotesTool())

    listing = notes_frontend_backend.load_notes_listing_payload(limit=50)

    assert listing["success"] is True
    assert listing["returned_count"] == 2
    assert listing["total_count"] == 2
    assert [note["id"] for note in listing["notes"]] == [2, 1]
    assert listing["notes"][0]["preview"].endswith("...")
    assert listing["notes"][0]["tags"] == ["recent", "mobile"]

    detail = notes_frontend_backend.load_note_detail_payload(2)

    assert detail["success"] is True
    assert detail["note"]["title"] == "Newest note"
    assert detail["note"]["category"] == "journal"


def test_notes_frontend_listing_requires_installed_agent(monkeypatch):
    monkeypatch.setattr(
        notes_frontend_backend,
        "load_agent_install_registry",
        lambda: {"installed_agents": []},
    )
    monkeypatch.setattr(notes_frontend_backend, "get_notes_tool", lambda: _FakeNotesTool())
    payload = notes_frontend_backend.load_notes_listing_payload(limit=10)

    assert payload["success"] is False
    assert "uninstalled" in payload["error"]


def test_notes_media_endpoint_supports_byte_ranges_for_m4a(monkeypatch, tmp_path):
    tool = NotesTool(db_path=str(tmp_path / "autoyou_notes.db"))
    media_bytes = b"synthetic-m4a-container-bytes"
    saved = tool.save_media_attachment(
        filename="voice-message-2026-06-27T00-52-55Z.m4a",
        data_base64=base64.b64encode(media_bytes).decode("ascii"),
        mimetype="audio/x-m4a",
        source="test",
    )
    monkeypatch.setattr(notes_frontend_backend, "get_notes_tool", lambda: tool)
    client = TestClient(notes_frontend_backend.app)

    ranged = client.get(f"/api/media/{saved['id']}", headers={"Range": "bytes=0-8"})

    assert ranged.status_code == 206
    assert ranged.content == media_bytes[:9]
    assert ranged.headers["accept-ranges"] == "bytes"
    assert ranged.headers["content-range"] == f"bytes 0-8/{len(media_bytes)}"
    assert ranged.headers["content-length"] == "9"
    assert ranged.headers["content-type"].startswith("audio/mp4")
    assert "inline" in ranged.headers["content-disposition"]
    assert ranged.headers["etag"].startswith('W/"')
    assert ranged.headers["last-modified"]

    full = client.get(f"/api/media/{saved['id']}")

    assert full.status_code == 200
    assert full.content == media_bytes
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-length"] == str(len(media_bytes))
    assert full.headers["content-type"].startswith("audio/mp4")


def test_notes_media_endpoint_serves_legacy_raw_aac_with_aac_mime(monkeypatch, tmp_path):
    tool = NotesTool(db_path=str(tmp_path / "autoyou_notes.db"))
    saved = tool.save_media_attachment(
        filename="synthetic-voice-message.m4a",
        data_base64=base64.b64encode(_ADTS_AAC).decode("ascii"),
        mimetype="audio/m4a",
        source="test",
    )
    monkeypatch.setattr(notes_frontend_backend, "get_notes_tool", lambda: tool)
    client = TestClient(notes_frontend_backend.app)

    response = client.get(f"/api/media/{saved['id']}", headers={"Range": "bytes=0-6"})

    assert response.status_code == 206
    assert response.content == _ADTS_AAC
    assert response.headers["content-type"].startswith("audio/aac")


def test_notes_media_endpoint_caps_open_ended_ranges(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_NOTES_MEDIA_RANGE_WINDOW_BYTES", str(64 * 1024))
    tool = NotesTool(db_path=str(tmp_path / "autoyou_notes.db"))
    media_bytes = b"A" * (70 * 1024)
    saved = tool.save_media_attachment(
        filename="synthetic-voice-message.m4a",
        data_base64=base64.b64encode(media_bytes).decode("ascii"),
        mimetype="audio/mp4",
        source="test",
    )
    monkeypatch.setattr(notes_frontend_backend, "get_notes_tool", lambda: tool)
    client = TestClient(notes_frontend_backend.app)

    response = client.get(f"/api/media/{saved['id']}", headers={"Range": "bytes=0-"})

    assert response.status_code == 206
    assert response.content == media_bytes[: 64 * 1024]
    assert response.headers["content-length"] == str(64 * 1024)
    assert response.headers["content-range"] == f"bytes 0-{64 * 1024 - 1}/{len(media_bytes)}"
    assert response.headers["cache-control"] == "private, max-age=3600"


def test_notes_tool_migrates_legacy_repo_database(tmp_path, monkeypatch):
    primary_dir = tmp_path / "canonical"
    legacy_dir = tmp_path / "legacy"
    primary_dir.mkdir()
    legacy_dir.mkdir()

    legacy_db = legacy_dir / "autoyou_notes.db"
    legacy_tool = NotesTool(db_path=str(legacy_db))
    created = legacy_tool.create_note(
        title="Migrated note",
        content="Copied from a legacy database location.",
        tags=["legacy"],
        category="archive",
        metadata={"source": "legacy-test"},
    )
    assert created["success"] is True

    monkeypatch.setattr(NotesTool, "_resolve_default_storage_dir", classmethod(lambda cls: primary_dir))
    monkeypatch.setattr(
        NotesTool,
        "_legacy_db_candidates",
        classmethod(lambda cls, primary_db_path: [legacy_db]),
    )

    migrated_tool = NotesTool()
    notes = migrated_tool.list_notes(limit=10)

    assert migrated_tool._db_path == str(primary_dir / "autoyou_notes.db")
    assert any(note["title"] == "Migrated note" for note in notes)


def test_notes_tool_lists_most_recent_updates_first(tmp_path):
    db_path = tmp_path / "autoyou_notes.db"
    tool = NotesTool(db_path=str(db_path))

    edited_later = tool.create_note(title="Edited later", content="first body")
    created_later = tool.create_note(title="Created later", content="second body")

    assert edited_later["success"] is True
    assert created_later["success"] is True

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE notes SET created_at = ?, updated_at = ? WHERE id = ?",
            ("2026-03-01 08:00:00", "2026-03-11 09:00:00", edited_later["note_id"]),
        )
        conn.execute(
            "UPDATE notes SET created_at = ?, updated_at = ? WHERE id = ?",
            ("2026-03-10 08:00:00", "2026-03-10 08:00:00", created_later["note_id"]),
        )

    notes = tool.list_notes(limit=10)

    assert [note["id"] for note in notes[:2]] == [edited_later["note_id"], created_later["note_id"]]


def test_notes_listing_filters_by_category_and_order(monkeypatch):
    monkeypatch.setattr(
        notes_frontend_backend,
        "load_agent_install_registry",
        lambda: {"installed_agents": ["notes_agent"]},
    )
    monkeypatch.setattr(notes_frontend_backend, "get_notes_tool", lambda: _FakeNotesTool())
    client = TestClient(notes_frontend_backend.app)

    listing = client.get("/api/notes", params={"category": "journal", "order": "oldest"}).json()

    assert [note["id"] for note in listing["notes"]] == [2]
    assert listing["total_count"] == 1
    assert (listing["category"], listing["order"]) == ("journal", "oldest")
    assert (listing["notes"][0]["accent"], listing["notes"][0]["icon"]) == ("rose", "heart")
    archive_accent, archive_icon = notes_frontend_backend.category_appearance("archive")
    assert listing["categories"] == [
        {"name": "archive", "count": 1, "accent": archive_accent, "icon": archive_icon},
        {"name": "journal", "count": 1, "accent": "rose", "icon": "heart"},
    ]

    # Chips describe the whole library; later pages do not resend them.
    cursor = notes_frontend_backend._encode_cursor("2026-03-11 10:30:00", 2)
    next_page = notes_frontend_backend.load_notes_listing_payload(limit=10, cursor=cursor)
    assert "categories" not in next_page
    assert next_page["order"] == "newest"


def test_category_appearance_is_stable_for_known_and_unknown_categories():
    appearance = notes_frontend_backend.category_appearance

    assert appearance("Work") == ("blue", "briefcase")
    assert appearance("  personal ") == ("purple", "doc")
    assert appearance("To Do") == ("teal", "check")
    assert appearance(None) == ("slate", "doc")
    accent, icon = appearance("Garden Club")
    assert (accent, icon) == appearance("garden   club")
    assert accent in {"blue", "purple", "green", "amber", "teal", "rose"}
    assert icon == "doc"


def test_relative_day_labels_match_the_client():
    from datetime import datetime

    now = datetime(2026, 9, 24, 20, 0)
    label = notes_frontend_backend._format_relative_day
    # from __debug_provenance_n__ import license

    assert label("2026-09-24T08:15:00", now) == "Today"
    assert label("2026-09-23 23:59:00", now) == "Yesterday"
    assert label("2026-09-10T10:00:00", now) == "Sep 10"
    assert label("2025-12-31T10:00:00", now) == "Dec 31, 2025"
    assert label("not a date", now) == "not a date"


def test_notes_first_paint_renders_cards_chips_and_sync_state(monkeypatch):
    monkeypatch.setattr(
        notes_frontend_backend,
        "load_agent_install_registry",
        lambda: {"installed_agents": ["notes_agent"]},
    )
    monkeypatch.setattr(notes_frontend_backend, "get_notes_tool", lambda: _FakeNotesTool())

    page = notes_frontend_backend.render_index_html()
    unreplaced = re.compile(r"__INITIAL_(?!NOTES_BOOTSTRAP__)[A-Z_]+__")

    assert not unreplaced.search(page)
    assert 'class="sync-status" data-state="synced"' in page
    assert '<article class="note-card accent-rose" data-note-id="2">' in page
    assert '<i class="ic ic-heart"></i>' in page
    assert 'class="filter-chip filter-all is-active"' in page
    assert 'data-category="journal" aria-pressed="false">journal</button>' in page

    monkeypatch.setattr(notes_frontend_backend, "load_agent_install_registry", lambda: {"installed_agents": []})
    unavailable = notes_frontend_backend.render_index_html()

    assert not unreplaced.search(unavailable)
    assert 'data-state="error"' in unavailable
    assert "Not synced" in unavailable


def test_notes_tool_pages_oldest_first_and_counts_categories(tmp_path):
    db_path = tmp_path / "autoyou_notes.db"
    tool = NotesTool(db_path=str(db_path))
    ids = [
        tool.create_note(title=f"Note {index}", content="body", category=category)["note_id"]
        for index, category in enumerate(["Work", "Work", "Health", None])
    ]
    with sqlite3.connect(str(db_path)) as conn:
        for offset, note_id in enumerate(ids):
            conn.execute("UPDATE notes SET updated_at = ? WHERE id = ?", (f"2026-03-1{offset} 09:00:00", note_id))

    first_page = tool.list_notes(limit=2, descending=False)
    last = first_page[-1]
    second_page = tool.list_notes(
        limit=2,
        descending=False,
        cursor_updated_at=last["updated_at"],
        cursor_note_id=last["id"],
    )

    assert [note["id"] for note in first_page] == ids[:2]
    assert [note["id"] for note in second_page] == ids[2:]
    assert [note["id"] for note in tool.list_notes(limit=10)] == ids[::-1]
    assert tool.list_categories() == [{"name": "Work", "count": 2}, {"name": "Health", "count": 1}]
