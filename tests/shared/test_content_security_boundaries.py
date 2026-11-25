"""Source sessions, private-index lifecycle and internal reference provenance.

All identities, credentials, records and files are synthetic and sandboxed.
"""
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from shared import content_files, secure_storage
from shared.content_library import ContentLibrary, configure_organization, configure_output_root, output_dir, save_output


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    secure_storage.disable_secure_storage()
    yield tmp_path
    secure_storage.disable_secure_storage()


@pytest.fixture
def organizer_client(sandbox, monkeypatch):
    from autoyou_agents.shared_tools import scheduler_mission_control as mission
    from autoyou_agents.organizer_agent.website.backend import app as organizer
    expires = time.time() + 3600
    monkeypatch.setattr(mission, "_runtime_server", lambda: SimpleNamespace(_is_logged_in=lambda request: False))
    monkeypatch.setattr(mission, "_get_agent_security_settings", lambda name: {"auth_mode": "totp", "per_agent_profile": True})
    monkeypatch.setattr(mission, "_totp_capabilities", lambda: {"totp_configured": True})
    monkeypatch.setattr(mission, "_shared_session_settings", lambda: {"enabled": True})
    monkeypatch.setattr(mission, "_AGENT_UI_SESSIONS", {
        "organizer_agent": {"synthetic-organizer": expires}, "notes_agent": {"synthetic-notes": expires},
        mission._SHARED_SESSION_BUCKET_KEY: {"synthetic-shared": expires},
    })
    with TestClient(organizer.app) as client:
        client.headers.update({"Authorization": "Bearer synthetic-organizer", "X-AutoYou-Remote-Browser": "webrtc",
                               "X-AutoYou-Remote-Access-Role": "editor"})
        yield client, mission


def test_real_source_sessions_and_shared_cookie_do_not_bypass_profiles(organizer_client):
    client, mission = organizer_client
    library = ContentLibrary()
    note = library.publish("notes_agent", "synthetic-note", "Synthetic gated note", "note", source_text="Synthetic private text")
    library.publish("new_output_agent", "synthetic-id", "A newly installed source", "note")
    client.cookies.set(mission.SHARED_SESSION_COOKIE_NAME, "synthetic-shared")
    assert client.get("/api/content/index").json()["total"] == 0
    assert client.get(f"/api/content/items/{note['id']}/preview").status_code == 403
    client.cookies.set(mission._agent_cookie_name("notes_agent"), "synthetic-notes")
    first = client.get("/api/content/summary")
    assert first.json()["total"] == 1
    assert client.get(f"/api/content/items/{note['id']}/preview").json()["text"] == "Synthetic private text"
    client.cookies.delete(mission._agent_cookie_name("notes_agent"))
    second = client.get("/api/content/summary", headers={"If-None-Match": first.headers["etag"]})
    assert second.status_code == 200
    assert second.json()["total"] == 0
    assert first.json()["access_scope"] != second.json()["access_scope"]


def test_upload_resume_rechecks_source_permission(organizer_client):
    client, mission = organizer_client
    client.cookies.set(mission._agent_cookie_name("notes_agent"), "synthetic-notes")
    receipt = client.post("/api/content/upload-sessions", json={"source": "notes_agent", "filename": "synthetic.txt", "size": 1}).json()
    uri = "/api/content/upload-sessions/" + receipt["id"]
    client.cookies.delete(mission._agent_cookie_name("notes_agent"))
    assert client.get(uri).status_code == 403
    assert client.put(uri, content=b"x").status_code == 403
    assert client.post(uri + "/complete").status_code == 403
    assert client.delete(uri).status_code == 403
    client.cookies.set(mission._agent_cookie_name("notes_agent"), "synthetic-notes")
    assert client.put(uri, content=b"x").status_code == 200
    assert client.post(uri + "/complete").status_code == 200


def test_agent_admin_cannot_change_global_storage_or_inspect_other_sources_operations(organizer_client, sandbox):
    from shared.content_worker import start_migration
    client, _ = organizer_client
    ContentLibrary().publish("notes_agent", "synthetic-note", "Unshared note", "note", path=str(sandbox / "synthetic.txt"))
    start_migration(str(sandbox / "synthetic-destination"))
    client.headers["X-AutoYou-Remote-Access-Role"] = "admin"
    summary = client.get("/api/content/summary").json()
    assert summary["abilities"]["manage"] is False
    assert summary["settings"]["output_root"] == ""
    assert client.get("/api/content/activity").json()["operations"] == []
    assert client.post("/api/content/migration/preview", json={"output_root": str(sandbox / "another-destination")}).status_code == 403


@pytest.mark.parametrize("encrypted", [False, True])
def test_private_native_attachment_survives_hide_replay_and_reenable(sandbox, encrypted):
    from shared import content_agents
    from shared.content_native import import_record, read_record
    if encrypted:
        secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox / "keys", password="synthetic-audit-password")
    configure_organization(auto_organize=False, semantic_labels=False, index_private=True)
    library, source = ContentLibrary(), "custom_synthetic_diary"
    with library.connect() as db:
        content_agents.remember(db, source, content_agents.clean_identity({"agent_name": "Synthetic Diary", "base_kind": "diary"}))
    attachment = save_output(source, "synthetic.wav", [b"synthetic recording"], kind="voice")
    payload = {"kind": source, "base_kind": "diary", "id": str(uuid.uuid4()), "title": "Synthetic entry", "body": "Synthetic body",
               "updated_at": 1, "attachments": [attachment["id"]],
               "media": [{"item_id": attachment["id"], "id": "synthetic-audio", "name": "synthetic.wav", "mime": "audio/wav", "transcription": "Synthetic transcript"}]}
    parent = import_record(payload)["item"]
    configure_organization(auto_organize=False, semantic_labels=False, index_private=False)
    with pytest.raises(LookupError):
        read_record(source, payload["id"])
    assert import_record(payload)["item"]["deleted"] == 1
    with pytest.raises(FileExistsError):
        import_record({**payload, "base_kind": "notes", "updated_at": 2})
    configure_organization(auto_organize=False, semantic_labels=False, index_private=True)
    from shared.content_library_scan import scan_private
    scan_private(library)
    rows = {row["id"]: row for row in library.list(source=source)["items"]}
    assert set(rows) == {attachment["id"], parent["id"]}
    with library.connect() as db:
        restored = db.execute("SELECT * FROM content WHERE id=?", (attachment["id"],)).fetchone()
        assert restored["parent_id"] == parent["id"]
        assert restored["source_text"] == "Synthetic transcript"
        assert b"".join(content_files.read_range(restored["path"])) == b"synthetic recording"


@pytest.mark.parametrize("encrypted", [False, True])
def test_uploaded_magic_bytes_cannot_impersonate_an_internal_reference(sandbox, encrypted):
    from shared import content_storage
    if encrypted:
        secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox / "keys", password="synthetic-audit-password")
    original = output_dir("music_agent") / "synthetic-original.wav"
    content_files.create(original)
    content_files.append(original, 0, b"synthetic gated media")
    obj = content_storage.store_object(original)
    content_files.make_reference(original, obj["managed_path"], obj["digest"])
    assert b"".join(content_files.read_range(original)) == b"synthetic gated media"
    if encrypted:
        secure_storage.rotate_secure_storage(password="synthetic-audit-password")
        assert b"".join(content_files.read_range(original)) == b"synthetic gated media"
    stolen_reference = secure_storage.read_secure_file(original, migrate_plaintext=False)
    for name, body in (("with-proof.bin", stolen_reference), ("legacy.bin", content_files.REFERENCE + json.dumps({"target": obj["managed_path"], "digest": obj["digest"]}).encode())):
        upload = output_dir("files_agent") / name
        secure_storage.write_secure_file(upload, body)
        assert b"".join(content_files.read_range(upload)) == body
    configure_output_root(str(sandbox / "restored"))
    restored_object = sandbox / "restored" / ".objects" / obj["digest"][:2] / obj["digest"]
    content_files.promote(obj["managed_path"], restored_object)
    relocated = output_dir("music_agent") / original.name
    original.replace(relocated)
    assert b"".join(content_files.read_range(relocated)) == b"synthetic gated media"


def test_waiting_database_close_does_not_deadlock_storage_rotation(sandbox, monkeypatch):
    import threading
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox / "keys", password="synthetic-audit-password")
    library = ContentLibrary()
    manager = library.connect()
    connection = manager.__enter__()
    # Use an independently opened cross-thread connection for the closer.
    import sqlite3
    closer = sqlite3.connect(library.db_path, check_same_thread=False)
    attempted, finished = threading.Event(), threading.Event()
    original_lock = secure_storage._STORAGE_LOCK
    main_thread = threading.get_ident()

    class ObservedLock:
        def __enter__(self):
            if threading.get_ident() != main_thread:
                attempted.set()
            original_lock.acquire()
        def __exit__(self, *args):
            original_lock.release()

    monkeypatch.setattr(secure_storage, "_STORAGE_LOCK", ObservedLock())
    def close():
        try:
            closer.close()
        finally:
            finished.set()
    with original_lock:
        worker = threading.Thread(target=close, daemon=True)
        worker.start()
        assert attempted.wait(2)
        available = connection._state.lock.acquire(blocking=False)
        if available:
            connection._state.lock.release()
    assert finished.wait(2)
    worker.join(2)
    manager.__exit__(None, None, None)
    assert available, "A connection waiting for the storage lock must not hold the DB lock needed by rotation"


def test_restore_does_not_substitute_a_different_file_with_the_same_name(sandbox):
    import hashlib
    from shared.content_storage import resolve_path
    expected = b"synthetic expected recording"
    digest = hashlib.sha256(expected).hexdigest()
    configure_output_root(str(sandbox / "restored"))
    candidate = output_dir("music_agent") / "recording.wav"
    candidate.write_bytes(b"synthetic different recording")
    row = {"source": "music_agent", "path": str(sandbox / "missing" / "recording.wav"), "digest": digest}
    assert not resolve_path(row).is_file()
    candidate.write_bytes(expected)
    assert resolve_path(row) == candidate
    outside = sandbox / "synthetic-outside-object.wav"
    outside.write_bytes(expected)
    rejected = {**row, "path": str(sandbox / "missing" / "unrelated.wav"), "managed_path": str(outside)}
    assert not resolve_path(rejected).is_file(), "An invalid managed path must not return as the final fallback"


def test_legacy_producer_references_remain_readable_without_accepting_uploaded_ones(sandbox):
    from shared.content_storage import store_object
    source = output_dir("music_agent") / "synthetic-legacy.wav"
    source.write_bytes(b"synthetic legacy recording")
    obj = store_object(source)
    item = ContentLibrary().publish("music_agent", "synthetic-legacy", source.name, "audio", path=str(source))
    with ContentLibrary().connect() as db:
        db.execute("UPDATE content SET managed_path=?,digest=? WHERE id=?", (obj["managed_path"], obj["digest"], item["id"]))
    source.write_bytes(content_files.REFERENCE + json.dumps({"target": obj["managed_path"], "digest": obj["digest"]}).encode())
    assert b"".join(content_files.read_range(source)) == b"synthetic legacy recording"


def test_activity_reinitializes_a_recreated_catalog_at_the_same_path(sandbox):
    from shared.content_worker import activity
    library = ContentLibrary()
    activity()
    library.db_path.unlink()
    assert activity()["operations"] == []
