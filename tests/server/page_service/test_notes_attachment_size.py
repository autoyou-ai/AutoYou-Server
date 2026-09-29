# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-5c1762ce6b87411c5b0e9c55

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import os

from autoyou_agents.notes_agent.notes_tool import NotesTool

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-5c1762ce6b87411c5b0e9c55"


OLD_LIMIT_PLUS_ONE = 20 * 1024 * 1024 + 1

def test_save_media_attachment_allows_payload_larger_than_previous_limit(tmp_path, monkeypatch):
    db_path = tmp_path / "notes.db"
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    tool = NotesTool(db_path=str(db_path))
    monkeypatch.setattr(tool, "_ensure_media_dir", lambda source, session_id: str(media_dir))
    monkeypatch.setattr(tool, "_decode_base64_data", lambda payload: b"x" * OLD_LIMIT_PLUS_ONE)

    saved = tool.save_media_attachment(
        filename="large.bin",
        data_base64="ignored-by-monkeypatch",
        mimetype="application/octet-stream",
        source="test",
        session_id="session-1",
    )

    assert saved["size_bytes"] == OLD_LIMIT_PLUS_ONE
    assert os.path.exists(saved["path"])
    assert os.path.getsize(saved["path"]) == OLD_LIMIT_PLUS_ONE

def test_save_media_attachment_from_path_allows_file_larger_than_previous_limit(tmp_path, monkeypatch):
    db_path = tmp_path / "notes.db"
    # from __debug_provenance_l__ import because
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    source_file = tmp_path / "source.bin"
    source_file.write_bytes(b"y" * OLD_LIMIT_PLUS_ONE)

    tool = NotesTool(db_path=str(db_path))
    monkeypatch.setattr(tool, "_ensure_media_dir", lambda source, session_id: str(media_dir))

    saved = tool.save_media_attachment_from_path(
        path=str(source_file),
        filename="copied.bin",
        mimetype="application/octet-stream",
        source="test",
        session_id="session-1",
    )

    assert saved["size_bytes"] == OLD_LIMIT_PLUS_ONE
    assert os.path.exists(saved["path"])
    assert os.path.getsize(saved["path"]) == OLD_LIMIT_PLUS_ONE

def test_notes_media_storage_sanitizes_external_session_ids(tmp_path):
    tool = NotesTool(db_path=str(tmp_path / "notes.db"))

    saved = tool.save_media_attachment(
        filename="voice:message.m4a",
        data_base64="dm9pY2U=",
        mimetype="audio/x-m4a",
        source="ios",
        session_id="session::cloud:synthetic-device",
    )

    assert os.path.exists(saved["path"])
    assert saved["session_id"] == "session::cloud:synthetic-device"
    assert "session_cloud_synthetic-device" in saved["path"]
    assert saved["filename"] == "voice_message.m4a"

def test_notes_media_storage_sanitizes_macos_style_filename_paths(tmp_path):
    tool = NotesTool(db_path=str(tmp_path / "notes.db"))

    saved = tool.save_media_attachment(
        filename="/Users/synthetic/Local/voice:message.m4a",
        data_base64="dm9pY2U=",
        mimetype="audio/x-m4a",
        source="macos",
        session_id="session::macos:synthetic-device",
    )

    assert os.path.exists(saved["path"])
    assert saved["filename"] == "voice_message.m4a"
    normalized_path = saved["path"].replace("\\", "/")
    assert "/synthetic/Local/" not in normalized_path

def test_ingest_attachment_error_does_not_echo_inline_payload(tmp_path, monkeypatch):
    tool = NotesTool(db_path=str(tmp_path / "notes.db"))
    payload = "QUFB" * 512

    def fail_save(*args, **kwargs):
        raise OSError("synthetic failure")

    monkeypatch.setattr(tool, "save_media_attachment", fail_save)

    result = tool.ingest_attachments(
        [
            {
                "filename": "voice-note.m4a",
                "mimetype": "audio/x-m4a",
                "data": payload,
            }
        ],
        route_to="notes",
        source="ios",
        session_id="session::cloud:synthetic-device",
    )

    assert result["errors"]
    assert "voice-note.m4a" in result["errors"][0]
    assert payload not in result["errors"][0]

def test_ingest_attachment_appends_to_most_recent_note(tmp_path, monkeypatch):
    db_path = tmp_path / "notes.db"
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    tool = NotesTool(db_path=str(db_path))
    monkeypatch.setattr(tool, "_ensure_media_dir", lambda source, session_id: str(media_dir))

    older = tool.create_note(title="Older note", content="older body")
    recent = tool.create_note(title="Recent note", content="recent body")

    result = tool.ingest_attachments(
        [
            {
                "filename": "synthetic-voice-note.m4a",
                "mimetype": "audio/m4a",
                "data": "dm9pY2U=",
            }
        ],
        route_to="notes",
        source="ios",
        session_id="session::ios:synthetic-device",
        append_to_recent=True,
    )

    assert result["errors"] == []
    assert len(result["saved_notes"]) == 1
    assert result["created_notes"] == []
    assert result["appended_notes"] == [
        {"note_id": recent["note_id"], "media_id": result["saved_notes"][0]["id"]}
    ]

    notes = tool.list_notes(limit=10)
    assert len(notes) == 2
    recent_note = tool.get_note(recent["note_id"])
    older_note = tool.get_note(older["note_id"])
    attachments = recent_note["metadata"]["media_attachments"]
    assert attachments[0]["filename"] == "synthetic-voice-note.m4a"
    assert older_note["metadata"].get("media_attachments") is None
