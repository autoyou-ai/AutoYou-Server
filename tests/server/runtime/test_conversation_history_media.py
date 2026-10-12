# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Chat & History: server-side conversation names, kept media, and filters."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import asyncio
import base64
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server as server_module
from session_utils import MemoryIntegratedSessionManager
from shared import conversation_media_store as media_store


@pytest.fixture
def isolated_media(tmp_path, monkeypatch):
    media_dir = tmp_path / "conversation_media"
    monkeypatch.setenv(media_store.CONVERSATION_MEDIA_DIR_ENV, str(media_dir))
    return media_dir


def _manager(tmp_path):
    return MemoryIntegratedSessionManager(db_path=str(tmp_path / "sessions.db"), adk_session_service=object())


def test_conversation_title_round_trip_and_clear(tmp_path):
    manager = _manager(tmp_path)

    stored = manager.set_conversation_title("user::local:Phone-1", "session::local:Phone-1::2", "  Trip   planning  ")

    assert stored == "Trip planning"
    titles = manager.get_conversation_titles()
    assert titles[("user::local:Phone-1", "session::local:Phone-1::2")]["title"] == "Trip planning"
    assert manager.set_conversation_title("user::local:Phone-1", "session::local:Phone-1::2", "x" * 500) == "x" * 120

    assert manager.set_conversation_title("user::local:Phone-1", "session::local:Phone-1::2", "   ") == ""
    assert manager.get_conversation_titles() == {}
    assert manager.set_conversation_title("", "session", "Name") is None


def test_memory_record_flags_voice_calls_and_keeps_voice_notes_out_of_files():
    call = MemoryIntegratedSessionManager._build_memory_search_record(
        event_id="e1",
        user_id="u",
        session_id="s",
        event_type="chat_interaction",
        event_data={"user_message": "hi", "agent_response": "hello", "memory_metadata": {"source": "voice_call"}},
    )
    note = MemoryIntegratedSessionManager._build_memory_search_record(
        event_id="e2",
        user_id="u",
        session_id="s",
        event_type="chat_interaction",
        event_data={
            "user_message": "transcript",
            "agent_response": "reply",
            "attachments": [{"filename": "voice.ogg", "mimetype": "audio/ogg", "source": "voice_note"}],
        },
    )
    upload = MemoryIntegratedSessionManager._build_memory_search_record(
        event_id="e3",
        user_id="u",
        session_id="s",
        event_type="chat_interaction",
        event_data={"user_message": "see", "agent_response": "ok", "attachments": [{"filename": "a.pdf", "mimetype": "application/pdf"}]},
    )

    assert call["metadata"]["has_voice"] is True and call["metadata"]["has_files"] is False
    assert note["metadata"]["has_voice"] is True and note["metadata"]["has_files"] is False
    assert upload["metadata"]["has_voice"] is False and upload["metadata"]["has_files"] is True


def test_backfill_flags_older_voice_and_file_turns_once(tmp_path):
    db_path = tmp_path / "sessions.db"
    _manager(tmp_path)  # creates the schema, then simulate turns indexed by an older build
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM autoyou_schema_migrations")
        conn.execute(
            "CREATE TABLE events (id TEXT, app_name TEXT, user_id TEXT, session_id TEXT, invocation_id TEXT, timestamp TEXT, event_data TEXT)"
        )
        rows = [
            ("call-1", json.dumps({"source": "voice_call", "client": "autoyou-datachannel"})),
            ("file-1", json.dumps({"client": "ios"})),
            ("text-1", json.dumps({"client": "ios"})),
        ]
        for event_id, metadata_json in rows:
            conn.execute(
                "INSERT INTO memory_search (event_id, user_id, session_id, external_session_id, event_type, timestamp_text, sort_timestamp, user_message, agent_response, content, normalized_content, metadata_json) "
                "VALUES (?, 'u', 's', 'x', 'chat_interaction', '2026-09-01', 1, 'm', 'r', 'm r', 'm r', ?)",
                (event_id, metadata_json),
            )
        event = {"actions": {"state_delta": {"event_data_raw": {"attachments": [{"filename": "a.png", "mimetype": "image/png"}]}}}}
        conn.execute(
            "INSERT INTO events VALUES ('file-1', 'autoyou_agents', 'u', 'internal', '', '2026-09-01', ?)",
            (json.dumps(event),),
        )
        conn.commit()

    _manager(tmp_path)

    with sqlite3.connect(db_path) as conn:
        flags = {
            row[0]: json.loads(row[1])
            for row in conn.execute("SELECT event_id, metadata_json FROM memory_search")
        }
        assert conn.execute("SELECT COUNT(*) FROM autoyou_schema_migrations").fetchone()[0] == 1
    assert flags["call-1"]["has_voice"] is True and flags["call-1"]["has_files"] is False
    assert flags["file-1"]["has_files"] is True and flags["file-1"]["has_voice"] is False
    assert not flags["text-1"].get("has_voice") and not flags["text-1"].get("has_files")


def test_archived_media_is_listed_readable_and_deleted_with_history(tmp_path, isolated_media):
    manager = _manager(tmp_path)
    user_id, session_id = "user::local:Phone-1", "session::local:Phone-1"
    manager.set_conversation_title(user_id, session_id, "Named")

    record = media_store.archive_attachment(
        manager,
        {"filename": "voice-note.ogg", "mimetype": "audio/ogg", "data": base64.b64encode(b"OggS-voice").decode()},
        user_id=user_id,
        session_id=session_id,
        turn_id="turn-1",
        role="user",
        source="voice_note",
        transcript="hello there",
    )

    assert record and record["kind"] == "audio"
    rows = manager.list_conversation_media(user_id, session_id)
    assert [row["media_id"] for row in rows] == [record["media_id"]]
    assert rows[0]["transcript"] == "hello there"
    assert media_store.read_media_bytes(rows[0]["storage_path"]) == b"OggS-voice"
    assert manager.conversation_media_summary()[(user_id, session_id)] == {"voice": 1, "files": 0}

    result = asyncio.run(manager.delete_conversation_history(user_id, session_id))

    assert "conversation_media" in result["components"]
    assert manager.list_conversation_media(user_id, session_id) == []
    assert manager.get_conversation_titles() == {}
    assert not list(isolated_media.rglob("*.ogg"))


def test_media_archive_respects_keep_media_setting(tmp_path, isolated_media):
    manager = _manager(tmp_path)

    record = media_store.archive_attachment(
        manager,
        {"filename": "a.png", "mimetype": "image/png", "data": base64.b64encode(b"png").decode()},
        user_id="u",
        session_id="s",
        turn_id="t",
        role="user",
        source="attachment",
        cfg={"chat_history": {"keep_media": False}},
    )

    assert record is None
    assert manager.list_conversation_media("u", "s") == []


def test_client_supplied_paths_are_never_read_back(tmp_path, isolated_media):
    secret = tmp_path / "server-secret.txt"
    secret.write_text("do not serve", encoding="utf-8")
    manager = _manager(tmp_path)

    assert media_store.read_attachment_bytes({"filename": "x.txt", "path": str(secret)}) is None
    assert media_store.is_servable_media_path(str(secret)) is False
    assert media_store.archive_attachment(
        manager,
        {"filename": "x.txt", "path": str(secret)},
        user_id="u",
        session_id="s",
        turn_id="t",
        role="user",
        source="attachment",
    ) is None
    assert media_store.read_attachment_bytes({"filename": "x.txt", "data": base64.b64encode(b"inline").decode()}) == b"inline"


def test_event_attachment_matches_stored_context(tmp_path):
    raw = {
        "attachments": [{"filename": "b.png", "mimetype": "image/png", "size_bytes": 3}],
        "context": [{"attachments": [{"filename": "a.txt", "data": "YQ=="}, {"filename": "b.png", "data": "YmJi"}]}],
    }

    matched = MemoryIntegratedSessionManager.match_event_attachment(raw, 0)

    assert matched["data"] == "YmJi" and matched["mimetype"] == "image/png"
    assert MemoryIntegratedSessionManager.match_event_attachment(raw, 3) is None


def test_client_rename_is_scoped_to_its_own_owner(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    monkeypatch.setattr(server_module, "_get_conversation_session_manager", lambda: manager)
    identity = SimpleNamespace(
        canonical_user_id="user::local:Desk-1",
        canonical_session_id="session::local:Desk-1::3",
        owner_key="local:Desk-1",
    )

    current = server_module._rename_server_conversation(identity, {"conversation_title": "Current", "client": "autoyou-v2-native"})
    earlier = server_module._rename_server_conversation(identity, {"conversation_title": "First", "conversation_thread_id": 1})
    invalid = server_module._rename_server_conversation(identity, {"conversation_title": "x", "conversation_thread_id": "../other"})

    titles = manager.get_conversation_titles()
    assert current == {"renamed": True, "title": "Current", "cleared": False}
    assert titles[("user::local:Desk-1", "session::local:Desk-1::3")]["source"] == "autoyou-v2-native"
    assert earlier["renamed"] is True
    assert titles[("user::local:Desk-1", "session::local:Desk-1")]["title"] == "First"
    assert invalid == {"renamed": False, "reason": "invalid_thread"}


def test_capabilities_advertise_one_way_rename():
    assert server_module._build_webrtc_capabilities(cfg={})["conversation"] == {"rename": True}


class _FakeAdk:
    def __init__(self, events):
        self._events = events

    async def get_session(self, **kwargs):
        return SimpleNamespace(state={}, events=self._events)


def _admin_client(manager, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import rest_api
    from routers import admin as admin_routes

    monkeypatch.setattr(rest_api, "get_session_manager", lambda: manager)
    fake_server = SimpleNamespace(
        _require_api_login=lambda request: None,
        _json_response_no_store=server_module._json_response_no_store,
        get_session_info=lambda user_id, session_id: _async_value({"session_id": session_id}),
        _speech_config=lambda: {"voice_training": {"capture_enabled": True}},
        LOGGER=server_module.LOGGER,
    )
    admin_app = fastapi.FastAPI()
    admin_routes.register_routes(admin_app, fastapi.FastAPI(), fake_server)
    return TestClient(admin_app)


async def _async_value(value):
    return value


def _adk_turn(event_id, data):
    return SimpleNamespace(
        id=event_id,
        timestamp=1_790_000_000.0,
        actions=SimpleNamespace(state_delta={"event_data_raw": data}),
    )


def test_admin_history_lists_names_media_and_serves_files_safely(tmp_path, isolated_media, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "root"))
    user_id, session_id = "user::local:Phone-1", "session::local:Phone-1"
    manager = _manager(tmp_path)
    voice = media_store.archive_attachment(
        manager,
        {"filename": "note.ogg", "mimetype": "audio/ogg", "data": base64.b64encode(b"0123456789").decode()},
        user_id=user_id,
        session_id=session_id,
        turn_id="turn-1",
        role="user",
        source="voice_note",
        transcript="play my note",
    )
    html = media_store.archive_attachment(
        manager,
        {"filename": "page.html", "mimetype": "text/html", "data": base64.b64encode(b"<script>x</script>").decode()},
        user_id=user_id,
        session_id=session_id,
        turn_id="turn-1",
        role="assistant",
        source="media_reply",
    )
    manager.adk_session_service = _FakeAdk([
        _adk_turn("chat_interaction_1", {
            "user_message": "play my note",
            "agent_response": "Here you go",
            "media_turn_id": "turn-1",
            "attachments": [{"filename": "note.ogg", "mimetype": "audio/ogg", "media_id": voice["media_id"], "source": "voice_note"}],
        }),
    ])
    record =MemoryIntegratedSessionManager._build_memory_search_record(
        event_id="chat_interaction_1",
        user_id=user_id,
        session_id="internal",
        event_type="chat_interaction",
        external_session_id=session_id,
        event_data={
            "user_message": "play my note",
            "agent_response": "Here you go",
            "attachments": [{"filename": "note.ogg", "mimetype": "audio/ogg", "source": "voice_note"}],
            "memory_metadata": {"client": "ios", "client_display_name": "Kitchen iPhone"},
        },
    )
    manager._upsert_memory_search_record(record)
    client = _admin_client(manager, monkeypatch)

    renamed = client.post("/api/chat/session/title", json={"user_id": user_id, "session_id": session_id, "title": "Voice test"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Voice test"

    listing = client.get("/api/chat/sessions").json()
    row = listing["sessions"][0]
    assert row["title"] == "Voice test" and row["custom_title"] is True
    assert row["auto_title"] == "play my note"
    assert row["has_voice"] is True and row["has_files"] is True
    assert row["origin"] == "Kitchen iPhone · Local pair"

    detail = client.get("/api/chat/session", params={"user_id": user_id, "session_id": session_id}).json()
    assert detail["title"] == "Voice test"
    user_message, reply = detail["messages"]
    assert user_message["attachments"][0]["url"] == f"/api/chat/media/{voice['media_id']}"
    assert reply["attachments"][0]["media_id"] == html["media_id"]
    assert {item["media_id"] for item in detail["files"]} == {voice["media_id"], html["media_id"]}

    audio = client.get(f"/api/chat/media/{voice['media_id']}", headers={"Range": "bytes=2-5"})
    assert audio.status_code == 206 and audio.content == b"2345"
    assert audio.headers["content-range"] == "bytes 2-5/10"

    page = client.get(f"/api/chat/media/{html['media_id']}")
    assert page.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"

    cleared = client.post("/api/chat/session/title", json={"user_id": user_id, "session_id": session_id, "title": ""})
    assert cleared.json()["custom_title"] is False
    assert client.get("/api/chat/sessions").json()["sessions"][0]["title"] == "play my note"


def test_voice_training_clip_remembers_its_conversation(tmp_path, monkeypatch):
    from array import array

    from shared.audio_manager import AudioManager
    from shared.speech_config import normalize_speech_config
    from shared.voice_training_storage import get_voice_training_dir

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    manager = AudioManager(
        on_text_callback=lambda text: None,
        settings_provider=lambda: normalize_speech_config({"tts": {"provider": "off"}, "voice_training": {"capture_enabled": True}}),
        enable_stt=False,
    )
    pcm = array("h", (4000 if index % 40 < 20 else -4000 for index in range(16000 * 4))).tobytes()
    try:
        manager._save_captured_voice_thread(
            pcm,
            "A synthetic sentence long enough for voice training capture checks.",
            {"user_id": "user::local:Phone-1", "session_id": "session::local:Phone-1::2", "ignored": "x"},
        )
    finally:
        manager.close()

    entries = json.loads((get_voice_training_dir() / "transcripts.json").read_text(encoding="utf-8"))
    assert entries[0]["user_id"] == "user::local:Phone-1"
    assert entries[0]["session_id"] == "session::local:Phone-1::2"
    assert "ignored" not in entries[0]
