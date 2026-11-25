"""Removing from My Content goes through the agent that owns the item, so both stay in step.

Each test builds a real item with the agent's own code in a throwaway AUTOYOU_TEST_ROOT,
removes it through the content API, and checks the agent no longer has it either.
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared import secure_storage
from shared.content_library import ContentLibrary

PREFIX = "/api/admin/content"


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    secure_storage.disable_secure_storage()
    yield tmp_path
    secure_storage.disable_secure_storage()


@pytest.fixture
def client(sandbox):
    from shared.content_library_api import register_content_routes
    app = FastAPI()
    register_content_routes(app, lambda request: None)
    with TestClient(app) as test_client:
        yield test_client


def remove(client, source):
    row = ContentLibrary().list(source=source)["items"][0]
    return row, client.delete(f"{PREFIX}/items/{row['id']}", params={"expected_revision": row["revision"]})


def test_a_saved_page_is_removed_from_the_page_feed(client, sandbox):
    from page_feed_db import PageFeedDB
    from shared.platform_runtime import get_config_dir
    config = get_config_dir("AutoYou", anchor=Path(__file__).resolve().parents[2] / "server.py")
    feed = PageFeedDB(db_path=str(Path(config) / "page_feed.db"))
    saved = feed.insert("article", "https://example.test/synthetic", title="Synthetic page")
    row, response = remove(client, "page_agent")
    assert response.status_code == 200 and response.json()["mode"] == "agent"
    assert ContentLibrary().list(source="page_agent")["total"] == 0
    # The page agent really lost it: removing again finds nothing there.
    again = feed.delete(saved["id"])
    assert again is False


def test_a_generated_image_and_its_file_are_removed_by_media_generation(client, sandbox, monkeypatch):
    from autoyou_agents.media_generation_agent import media_generation_tool as media
    monkeypatch.setattr(media, "DB_PATH", sandbox / "media_history.db")
    media.init_db()
    from shared.content_library import output_dir
    stored = output_dir("media_generation_agent") / "poster.png"
    stored.write_bytes(b"\x89PNG synthetic")
    identifier = media.save_history_item("image", "a quiet harbor", "", str(stored), "poster.png", {}, "completed")
    row, response = remove(client, "media_generation_agent")
    assert response.status_code == 200 and response.json()["mode"] == "agent"
    assert media.get_history_item(identifier) is None
    assert not stored.exists()
    assert ContentLibrary().list(source="media_generation_agent")["total"] == 0


def test_a_tv_title_is_marked_deleted_but_its_media_folder_is_untouched(client, sandbox):
    from autoyou_agents.media_agent.website.backend.store import Store
    folder = sandbox / "library-folder"
    folder.mkdir()
    movie = folder / "film.mp4"
    movie.write_bytes(b"synthetic film")
    item, created = Store().add_item(source_key="synthetic-film", title="Synthetic film", kind="movie", file_path=str(movie))
    assert created
    # Opening the item publishes it, the way the TV app does when it is shown.
    Store().item(item["id"])
    row, response = remove(client, "media_agent")
    assert response.status_code == 200 and response.json()["mode"] == "agent"
    assert Store().item(item["id"])["status"] == "deleted"
    assert movie.read_bytes() == b"synthetic film", "the owner's own media file is never deleted"
    assert ContentLibrary().list(source="media_agent")["total"] == 0


def test_a_voice_recording_is_removed_with_its_audio(client, sandbox):
    from autoyou_agents.voice_training_agent.agent import _get_paths
    from shared.secure_storage import save_secure_json
    _, transcripts, recordings = _get_paths()
    recordings.mkdir(parents=True, exist_ok=True)
    audio = recordings / "t1.wav"
    audio.write_bytes(b"synthetic audio")
    save_secure_json(transcripts, [{"id": "t1", "filename": "t1.wav", "transcript": "synthetic words"}])
    library = ContentLibrary()
    library.publish("voice_training_agent", "transcript:t1", "Synthetic words", "note")
    library.publish("voice_training_agent", "recording:t1", "t1.wav", "voice")
    row = next(item for item in library.list(source="voice_training_agent")["items"] if item["source_id"] == "transcript:t1")
    response = client.delete(f"{PREFIX}/items/{row['id']}", params={"expected_revision": row["revision"]})
    assert response.status_code == 200 and response.json()["mode"] == "agent"
    assert not audio.exists()
    assert library.list(source="voice_training_agent")["total"] == 0


def test_an_agent_that_cannot_remove_something_says_so_and_nothing_changes(client, monkeypatch):
    from shared import content_removal

    def broken(row):
        raise RuntimeError("synthetic failure with /private/path details")
    monkeypatch.setitem(content_removal.ADAPTERS, "notes_agent", broken)
    row = ContentLibrary().publish("notes_agent", "5", "Synthetic note", "note", href="/agent/notes_agent/")
    response = client.delete(f"{PREFIX}/items/{row['id']}", params={"expected_revision": row["revision"]})
    assert response.status_code == 409
    body = response.json()
    assert "Open Notes to remove it there" in body["error"] and body["app"] == "/agent/notes_agent/"
    assert "private" not in response.text, "internal details never reach the screen"
    assert ContentLibrary().list(source="notes_agent")["total"] == 1


def test_an_item_the_agent_no_longer_has_is_dropped_from_the_index(client, monkeypatch):
    from shared import content_removal

    def gone(row):
        raise LookupError("not found")
    monkeypatch.setitem(content_removal.ADAPTERS, "notes_agent", gone)
    row = ContentLibrary().publish("notes_agent", "6", "Stale entry", "note", href="/agent/notes_agent/")
    assert client.delete(f"{PREFIX}/items/{row['id']}", params={"expected_revision": row["revision"]}).status_code == 200
    assert ContentLibrary().list(source="notes_agent")["total"] == 0


def test_every_removal_adapter_is_for_an_agent_that_the_index_knows(sandbox):
    from shared import content_agents, content_removal
    assert set(content_removal.ADAPTERS) <= set(content_agents.BUILTIN)
    assert all(content_agents.owns_data(source) for source in content_removal.ADAPTERS)
