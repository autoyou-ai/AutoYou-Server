from pathlib import Path

import pytest

from shared import secure_storage
from shared.content_library import ContentLibrary, configure_output_root, output_dir, settings
from shared import content_files


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    secure_storage.disable_secure_storage()
    yield tmp_path
    secure_storage.disable_secure_storage()


def test_catalog_roundtrip_and_conflict(sandbox):
    library = ContentLibrary()
    first = library.publish("custom_travel", "device-item-1", "Winter trip", "page", href="/agent/page_agent/")
    assert library.publish("custom_travel", "device-item-1", "Winter trip", "page", href="/agent/page_agent/")["revision"] == first["revision"]
    edited = library.annotate(first["id"], labels=["holiday"], annotation="Check dates", collection="Travel", expected_revision=first["revision"])
    with pytest.raises(FileExistsError):
        library.annotate(first["id"], labels=[], annotation="stale", collection="", expected_revision=first["revision"])
    updated = library.publish("custom_travel", "device-item-1", "Winter trip updated", "page")
    assert updated["annotation"] == "Check dates"
    assert library.list(q="holiday")["total"] == 1
    library.forget("custom_travel", "device-item-1")
    assert library.list()["total"] == 0
    assert library.list(after=edited["revision"])["items"][0]["deleted"] == 1


def test_incremental_cursor_does_not_skip_pages(sandbox):
    library = ContentLibrary()
    for n in range(5):
        library.publish("notes_agent", str(n), "Synthetic note", "note")
    first = library.list(after=0, limit=2)
    second = library.list(after=first["cursor"], limit=2)
    third = library.list(after=second["cursor"], limit=2)
    assert len({row["id"] for page in [first, second, third] for row in page["items"]}) == 5


def test_agent_collection_is_filterable_until_the_owner_organizes_it(sandbox):
    from shared.content_worker import activity

    library = ContentLibrary()
    note = library.publish("notes_agent", "7", "Synthetic note", "note",
                           source_collection="Studio", source_text="Microphone checklist")
    library.publish("page_agent", "3", "Synthetic page", "page", source_collection="References")
    assert activity()["collections"] == ["References", "Studio"]
    result = library.list(source="notes_agent", collection="Studio", q="microphone")
    assert [item["id"] for item in result["items"]] == [note["id"]]
    assert "source_text" not in result["items"][0]
    library.annotate(note["id"], labels=[], annotation="", collection="Production", expected_revision=note["revision"])
    assert library.list(collection="Studio")["total"] == 0
    assert activity()["collections"] == ["Production", "References"]


def test_test_root_dominates_custom_destination(sandbox):
    with pytest.raises(ValueError):
        configure_output_root(str(sandbox.parent / "operator-content"))
    assert output_dir("audio_agent").is_relative_to(sandbox)
    with pytest.raises(ValueError):
        output_dir("../escape")


def test_maximus_catalog_and_media_never_write_plaintext(sandbox):
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox, password="synthetic-library-password")
    library = ContentLibrary()
    library.publish("notes_agent", "synthetic-id", "private synthetic title", "note")
    assert library.db_path.read_bytes().startswith(secure_storage.SQLITE_HEADER)
    assert b"private synthetic title" not in library.db_path.read_bytes()
    file = sandbox / "pending.part"
    content_files.create(file)
    payload = b"synthetic-private-audio" * 100000
    content_files.append(file, 0, payload)
    assert content_files.size(file) == len(payload)
    assert b"".join(content_files.read_range(file, 12, 1048599)) == payload[12:1048600]
    assert content_files.append(file, 0, payload) == len(payload)
    with pytest.raises(ValueError):
        content_files.append(file, 0, b"different")
    assert file.read_bytes().startswith(secure_storage.FILE_HEADER)
    for chunk in content_files.parts(file).iterdir():
        assert chunk.read_bytes().startswith(secure_storage.FILE_HEADER)
        assert b"synthetic-private-audio" not in chunk.read_bytes()
    target = sandbox / "library" / "synthetic.mp3"
    content_files.promote(file, target)
    assert b"".join(content_files.read_range(target)) == payload
    with content_files.materialized(target) as plain:
        assert plain.read_bytes() == payload
    assert not plain.exists()
    secure_storage.disable_secure_storage()
    with pytest.raises(secure_storage.SecureStorageError):
        list(content_files.read_range(target))


def test_plain_external_file_is_not_mutated(sandbox):
    file = sandbox / "source.mp3"
    file.write_bytes(b"synthetic-audio")
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox, password="synthetic-library-password")
    assert b"".join(content_files.read_range(file, 2, 7)) == b"ntheti"
    assert file.read_bytes() == b"synthetic-audio"


def test_rotation_discovers_old_and_new_output_destinations(sandbox, monkeypatch):
    from shared.content_library import save_output
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox / "keys", password="synthetic-library-password")
    configure_output_root(str(sandbox / "first-destination"))
    saved = save_output("custom_project", "demo.mp3", [b"synthetic private music"])
    target = next((sandbox / "first-destination" / "custom_project").glob("*.mp3"))
    configure_output_root(str(sandbox / "second-destination"))
    assert output_dir("custom_project").parent == sandbox / "second-destination"
    monkeypatch.setattr(secure_storage, "_PROTECTED_FILES", set())
    secure_storage.rotate_secure_storage(password="synthetic-library-password")
    assert b"".join(content_files.read_range(target)) == b"synthetic private music"
    assert ContentLibrary().list()["items"][0]["id"] == saved["id"]


def test_interrupted_promotion_and_uncommitted_append_recover(sandbox, monkeypatch):
    import shutil
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox, password="synthetic-library-password")
    source, target = sandbox / "pending.part", sandbox / "final.mp3"
    content_files.create(source)
    content_files.append(source, 0, b"committed")
    content_files.append(source, 9, b"uncommitted")
    content_files.truncate(source, 9)
    assert b"".join(content_files.read_range(source)) == b"committed"
    # Simulate a crash after chunks moved, before the source manifest cleanup.
    shutil.copyfile(source, target)
    content_files.parts(source).replace(content_files.parts(target))
    content_files.finish_upload(source, target, 9)
    assert not source.exists()
    assert b"".join(content_files.read_range(target)) == b"committed"


def test_owner_api_auth_ranges_and_untrusted_download(sandbox):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from shared.content_library_api import register_content_routes
    from shared.content_library import state_root
    app = FastAPI()
    register_content_routes(app, lambda request: None if request.headers.get("Authorization") == "Bearer synthetic-owner" else JSONResponse({"error": "unauthorized"}, status_code=401))
    with TestClient(app) as client:
        assert client.get("/api/admin/content").status_code == 401
        assert client.post("/api/admin/content/import?filename=test.html", content=b"<script>test</script>").status_code == 401
        assert not state_root().exists()
        secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox, password="synthetic-library-password")
        client.headers["Authorization"] = "Bearer synthetic-owner"
        row = client.post("/api/admin/content/import?filename=demo.mp3&source=custom_music", content=b"0123456789").json()
        stream = "/api/admin/content/items/" + row["id"] + "/content"
        assert client.get(stream, headers={"Range": "bytes=2-5"}).content == b"2345"
        assert client.get(stream, headers={"Range": "bytes=11-"}).status_code == 416
        assert client.head(stream).headers["content-length"] == "10"
        annotation = {"expected_revision": row["revision"], "labels": ["synthetic"], "collection": "Test", "annotation": "A fixture"}
        assert client.patch("/api/admin/content/items/" + row["id"], json=annotation).status_code == 200
        assert client.patch("/api/admin/content/items/" + row["id"], json=annotation).status_code == 409
        assert "path" not in client.get("/api/admin/content").json()["items"][0]
        html = client.post("/api/admin/content/import?filename=demo.html", content=b"<script>test</script>").json()
        response = client.get("/api/admin/content/items/" + html["id"] + "/content")
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["content-security-policy"] == "sandbox"
        assert client.post("/api/admin/content/import?source=../escape", content=b"x").status_code == 400
        pending = client.post("/api/admin/content/upload-sessions", json={"filename": "draft.txt", "size": 3, "source": "custom_notes", "source_id": "synthetic-attachment"}).json()
        url = "/api/admin/content/upload-sessions/" + pending["id"]
        assert client.put(url + "?offset=0", content=b"abc").json()["received"] == 3
        item = client.post(url + "/complete").json()["item"]
        record = {"id": "00000000-0000-4000-8000-000000000002", "kind": "custom_notes", "base_kind": "notes", "title": "<script>synthetic</script>", "body": "A saved native note", "updated_at": 1, "attachments": [item["id"]]}
        assert client.post("/api/admin/content/native", json=record).status_code == 200
        view = client.get("/api/admin/content/native/custom_notes/" + record["id"])
        assert "<script>synthetic</script>" not in view.text
        assert "&lt;script&gt;synthetic&lt;/script&gt;" in view.text


def test_encrypted_audio_and_tv_uploads_publish_playable_content(sandbox):
    from autoyou_agents.audio_agent.website.backend.uploads import Uploads as AudioUploads
    from autoyou_agents.tv_agent.website.backend.uploads import Uploads as TVUploads
    from autoyou_agents.tv_agent.website.backend.store import Store
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox, password="synthetic-library-password")
    audio = AudioUploads()
    row = audio.begin("synthetic-owner", "sample.mp3", 10, "Synthetic music")
    audio.append_bytes(row["id"], "synthetic-owner", 0, b"0123456789")
    path = audio.complete(row["id"], "synthetic-owner")
    assert b"".join(content_files.read_range(path, 3, 7)) == b"34567"
    assert audio.complete(row["id"], "synthetic-owner") == path
    tv = TVUploads(Store(sandbox / "tv"))
    row = tv.begin("synthetic-owner", "sample.mp4", 10, "Synthetic video", "video", None)
    tv._append(row["id"], "synthetic-owner", 0, b"0123456789")
    item = tv.complete(row["id"], "synthetic-owner")
    assert b"".join(content_files.read_range(item["file_path"])) == b"0123456789"
    assert {row["source"] for row in ContentLibrary().list()["items"]} == {"audio_agent", "tv_agent"}
    tv.store.update_item(item["id"], {"status": "deleted"})
    assert ContentLibrary().list(source="tv_agent")["total"] == 0


def test_custom_agent_transfer_and_record_sync(sandbox):
    from shared.content_transfers import ContentTransfers
    from shared.content_native import import_record, read_record
    from fastapi import HTTPException
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox, password="synthetic-library-password")
    transfers = ContentTransfers()
    pending = transfers.begin("synthetic-owner", "document.txt", 10, "custom_project", "attachment-1")
    with pytest.raises(HTTPException) as forbidden:
        transfers.status(pending["id"], "another-synthetic-owner")
    assert forbidden.value.status_code == 404
    transfers.append(pending["id"], "synthetic-owner", 0, b"01234")
    # A new instance reads committed offsets and accepts identical retries.
    transfers = ContentTransfers()
    assert transfers.append(pending["id"], "synthetic-owner", 0, b"01234")["received"] == 5
    transfers.append(pending["id"], "synthetic-owner", 5, b"56789")
    attached = transfers.complete(pending["id"], "synthetic-owner")["item"]
    record = {"id": "00000000-0000-4000-8000-000000000001", "kind": "custom_project", "base_kind": "notes", "title": "Synthetic project", "body": "Private synthetic text", "updated_at": 100, "attachments": [attached["id"]]}
    first = import_record(record)
    assert import_record(record)["item"]["revision"] == first["item"]["revision"]
    record.update(updated_at=101, body="New synthetic revision")
    second = import_record(record)
    assert second["item"]["revision"] > first["item"]["revision"]
    assert read_record("custom_project", record["id"])["body"] == record["body"]
    with pytest.raises(FileExistsError):
        import_record({**record, "updated_at": 100})
    with pytest.raises(ValueError):
        import_record({**record, "kind": "custom_other", "updated_at": 102})
    assert b"New synthetic revision" not in ContentLibrary().db_path.read_bytes()


def test_existing_index_preserves_labels_and_external_sources(sandbox):
    import sqlite3
    from shared.content_library_scan import refresh_existing
    from shared.platform_runtime import get_mutable_data_dir
    root = get_mutable_data_dir("AutoYou", anchor=__file__)
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / "page_feed.db") as db:
        db.execute("CREATE TABLE feed_items(id INTEGER,title TEXT)")
        db.execute("INSERT INTO feed_items VALUES(1,'Synthetic saved page')")
    first = refresh_existing()
    assert first["indexed"] == 1 and not first["errors"]
    library = ContentLibrary()
    row = library.list()["items"][0]
    library.annotate(row["id"], labels=["keep"], annotation="Personal annotation", collection="Project", expected_revision=row["revision"])
    refresh_existing()
    assert library.list()["items"][0]["labels"] == ["keep"]


def test_encrypted_tv_photo_preview_remains_viewable(sandbox):
    import io
    from PIL import Image
    from fastapi.testclient import TestClient
    from autoyou_agents.tv_agent.website.backend.app import create_app
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox, password="synthetic-library-password")
    data = io.BytesIO()
    Image.new("RGB", (8, 8), (80, 60, 100)).save(data, "PNG")
    app = create_app(sandbox / "tv", otp_verifier=lambda value: value == "123456")
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"code": "123456"}).status_code == 200
        result = client.post("/api/uploads", content=data.getvalue(), headers={"X-File-Name": "synthetic.png"})
        assert result.status_code == 200
        identifier = result.json()["item"]["id"]
        thumbnail = client.get("/api/artwork/" + identifier)
        assert thumbnail.status_code == 200 and thumbnail.content.startswith(b"\xff\xd8")
        assert all(path.read_bytes().startswith(secure_storage.FILE_HEADER) for path in (sandbox / "tv" / "artwork").rglob("*.jpg"))


@pytest.mark.parametrize("method", ["delete_item_and_cleanup", "clear", "clear_all_and_cleanup"])
def test_page_removal_publishes_catalog_tombstones(sandbox, method):
    from page_feed_db import PageFeedDB
    feed = PageFeedDB(str(sandbox / "pages.db"))
    row = feed.insert("article", "https://example.test/synthetic", title="Synthetic page")
    library = ContentLibrary()
    revision = library.list(source="page_agent")["items"][0]["revision"]
    if method == "delete_item_and_cleanup":
        feed.delete_item_and_cleanup(row["id"])
    else:
        getattr(feed, method)()
    assert library.list(source="page_agent")["total"] == 0
    assert library.list(after=revision)["items"][0]["deleted"] == 1


def test_organizer_reads_bounded_saved_page_content(sandbox):
    from page_feed_db import PageFeedDB
    from shared.platform_runtime import get_mutable_data_dir
    from autoyou_agents.organizer_agent.agent import read_content
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox, password="synthetic-library-password")
    database = get_mutable_data_dir("AutoYou", anchor=__file__) / "page_feed.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    feed = PageFeedDB(str(database))
    feed.insert("article", "https://example.test/long", title="Synthetic article", content="Synthetic content. " * 3000)
    row = ContentLibrary().list(source="page_agent")["items"][0]
    result = read_content(row["id"])
    assert result["untrusted_content"] and result["truncated"]
    assert len(result["text"]) == 32768
    assert b"Synthetic content." not in database.read_bytes()


def test_organizer_cannot_inherit_public_agent_mode(sandbox, monkeypatch):
    from starlette.requests import Request
    from autoyou_agents.organizer_agent.website.backend import app as organizer
    request = Request({"type": "http", "method": "GET", "path": "/api/content", "headers": []})
    monkeypatch.setattr(organizer, "_describe_auth_state", lambda request, agent: {"via": "open", "authenticated": True})
    monkeypatch.setattr(organizer, "_api_auth_error", lambda agent, request: None)
    assert organizer.authorize(request).status_code == 403
    monkeypatch.setattr(organizer, "_describe_auth_state", lambda request, agent: {"via": "mission_session", "authenticated": True})
    assert organizer.authorize(request) is None


def test_note_body_edits_are_visible_to_incremental_catalog_clients(sandbox):
    from autoyou_agents.notes_agent.notes_tool import NotesTool
    notes = NotesTool(db_path=str(sandbox / "notes.db"))
    note_id = notes.create_note(title="Synthetic note", content="First draft")["note_id"]
    first = ContentLibrary().list(source="notes_agent")["items"][0]
    notes.update_note(note_id=note_id, content="Second draft")
    changed = ContentLibrary().list(after=first["revision"])["items"]
    assert len(changed) == 1 and changed[0]["id"] == first["id"]
