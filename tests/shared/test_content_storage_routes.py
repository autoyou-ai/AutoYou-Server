"""Synthetic regression coverage for consolidated content and capture storage."""

from pathlib import Path

import pytest

from shared.content_library import ContentLibrary, configure_output_root, output_dir, output_roots, settings


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    from shared import secure_storage
    secure_storage.disable_secure_storage()
    yield tmp_path
    secure_storage.disable_secure_storage()


@pytest.mark.parametrize("old,current", [("audio_agent", "music_agent"), ("tv_agent", "media_agent")])
def test_new_outputs_use_current_names_and_keep_legacy_read_roots(old, current):
    legacy = Path(settings()["output_root"]) / old
    legacy.mkdir(parents=True)
    (legacy / "synthetic.txt").write_text("synthetic legacy content")
    destination = output_dir(current)
    assert destination.name == current
    assert destination != legacy
    assert output_dir(old) == destination
    assert legacy in output_roots(current)
    assert (legacy / "synthetic.txt").read_text() == "synthetic legacy content"
    item = ContentLibrary().publish(old, "synthetic-record", "Synthetic title", "file", href=f"/agent/{old}/")
    assert item["source"] == current
    assert item["href"] == f"/agent/{current}/"


def test_pages_website_and_chat_tool_share_new_destination(isolated_storage):
    from autoyou_agents.page_agent.page_tool import PageTool
    from autoyou_agents.page_agent.website.backend.app import PageFeedService

    tool = PageTool(base_url="http://127.0.0.1:18067")
    service = PageFeedService()
    assert Path(tool._uploads_dir) == Path(service.uploads_dir) == output_dir("page_agent")
    assert Path(tool._db_path) == Path(service.db.db_path)


def test_page_sync_reads_actual_database_after_restart():
    from page_feed_db import PageFeedDB
    from shared.media_paths import page_database_path
    from shared.content_library_scan import refresh_existing

    db = PageFeedDB(db_path=str(page_database_path()))
    identifier = db.insert("url", title="Synthetic saved page", content="Synthetic body", url="https://example.invalid/page", source="Local")["id"]
    library = ContentLibrary()
    library.forget("page_agent", str(identifier))
    result = refresh_existing()
    assert "page_agent" in result["sources"]
    assert any(row["title"] == "Synthetic saved page" for row in library.list(source="page_agent")["items"])


def test_legacy_tv_catalog_is_found_without_moving_it(isolated_storage):
    from autoyou_agents.media_agent.website.backend.store import data_root

    legacy = isolated_storage / "autoyou_agents/tv_agent/website/backend/tv_agent_data"
    legacy.mkdir(parents=True)
    (legacy / "library.sqlite3").write_bytes(b"synthetic marker")
    assert data_root() == legacy
    assert output_dir("media_agent").name == "media_agent"


def test_recording_defaults_follow_shared_destination_and_custom_paths(isolated_storage):
    import server

    root = isolated_storage / "synthetic-new-destination"
    configure_output_root(str(root))
    cfg = server._default_config()
    assert Path(server._resolve_video_recording_dir(cfg=cfg)) == root / "media_agent/recordings/video"
    assert Path(server._resolve_silent_recording_dir(cfg=cfg)) == root / "music_agent/recordings/safety"
    custom = isolated_storage / "synthetic-custom-recordings"
    cfg["video_call"]["recording_dir"] = str(custom)
    assert Path(server._resolve_video_recording_dir(cfg=cfg)) == custom


def test_wav_is_indexed_only_after_close_and_batches_do_not_overwrite():
    from shared.webrtc_audio_recorder import StreamingWavBatchRecorder

    directory = output_dir("music_agent") / "recordings/safety"
    first = StreamingWavBatchRecorder(session_id="synthetic-session", output_dir=directory)
    second = StreamingWavBatchRecorder(session_id="synthetic-session", output_dir=directory)
    first.write(b"\0\0" * 16)
    second.write(b"\0\0" * 16)
    assert first.current_path != second.current_path
    assert not first.current_path.exists()
    assert ContentLibrary().list(source="music_agent")["total"] == 0
    first.close()
    second.close()
    assert first.current_path.is_file() and second.current_path.is_file()
    rows = ContentLibrary().list(source="music_agent")["items"]
    assert len(rows) == 2
    assert all(row["source_collection"] == "Recordings / Safety" for row in rows)
    assert not list(directory.glob("*.partial"))


def test_finalized_video_is_in_media_catalog_and_sync_is_idempotent():
    from shared.recording_content import publish_recording, scan_recordings
    from autoyou_agents.media_agent.website.backend.store import Store

    folder = output_dir("media_agent") / "recordings/video/synthetic-session"
    folder.mkdir(parents=True)
    path = folder / "video.mp4"
    path.write_bytes(b"synthetic finalized video")
    (folder / "manifest.jsonl").write_text('{"format":"mp4"}\n')
    item = publish_recording(path, kind="video")
    assert item["source"] == "media_agent"
    assert Store().item(item["source_id"])["file_path"] == str(path)
    scan_recordings()
    assert ContentLibrary().list(source="media_agent")["total"] == 1


def test_location_history_keeps_its_store_and_publishes_only_day_metadata():
    from autoyou_agents.location_agent.store import LocationStore

    store = LocationStore()
    assert store.record_many([{"device_id": "synthetic-device", "platform": "synthetic", "timestamp": "2030-01-02T03:04:05Z",
        "latitude": 0.0, "longitude": 0.0}]) == 1
    rows = ContentLibrary().list(source="location_agent")["items"]
    assert len(rows) == 1 and rows[0]["source_id"] == "2030-01-02"
    assert "path" not in rows[0] and "latitude" not in rows[0]
    assert store.path.is_file()


@pytest.mark.parametrize("old,current", [("audio_agent", "music_agent"), ("tv_agent", "media_agent")])
def test_old_chat_and_ollama_tool_identifiers_resolve_to_current_names(old, current):
    from autoyou_agents.shared_tools.agent_identity import resolve_runtime_agent_name
    from autoyou_agents.litellm_ollama_adapter import _normalize_tool_name

    expected = resolve_runtime_agent_name(current)
    assert resolve_runtime_agent_name(old) == expected
    assert resolve_runtime_agent_name("autoyou_" + old) == expected
    assert _normalize_tool_name(old) == expected


def test_notes_default_media_uses_shared_content_and_legacy_remains_readable(isolated_storage):
    from autoyou_agents.notes_agent.notes_tool import NotesTool
    from shared.openclaw_gateway import is_allowed_attachment_path

    notes = NotesTool()
    folder = Path(notes._ensure_media_dir("synthetic", "synthetic-session"))
    assert folder.is_relative_to(output_dir("notes_agent"))
    old = NotesTool._resolve_default_storage_dir() / "media" / "synthetic.txt"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"synthetic legacy note attachment")
    assert is_allowed_attachment_path(str(old))
