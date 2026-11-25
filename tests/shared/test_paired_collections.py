"""Paired collection contracts exercised over HTTP with isolated agent stores."""
import hashlib
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from shared import content_native, paired_content, reminder_repeat
from shared.content_library import ContentLibrary, configure_organization
from shared.content_library_scan import scan_reminders
from shared.game_package_catalog import GamePackages


def headers(agent, role="admin"):
    return {"X-AutoYou-Remote-Browser": "webrtc", "X-AutoYou-Remote-Access-Role": role,
            "X-AutoYou-Agent-Frontend": agent, "X-AutoYou-WebRTC-Session-ID": "synthetic-collection-session",
            "X-AutoYou-Content-Sync": agent}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))


def test_custom_catalog_revision_and_access():
    app = FastAPI()
    content_native.register_collection(app, lambda request: None)
    paired_content.install_collection_sync(app, "organizer_agent")
    source = "custom_garden"
    url = "/api/sync/custom/" + source
    document = {"kind": source, "base_kind": "notes", "id": str(uuid.uuid4()), "title": "Garden",
                "updated_at": 1, "body": "Soil sample", "category": "Field work", "tags": ["Soil"], "base_revision": ""}
    with TestClient(app, client=("127.0.0.1", 41000)) as client:
        assert client.get(url).status_code == 403
        assert client.put(url + "/definition", headers=headers("organizer_agent", "viewer"), json={}).status_code == 403
        saved = content_native.import_record(document)
        with pytest.raises(ValueError, match="attachment"):
            content_native.import_record({**document, "id": str(uuid.uuid4()), "attachments": [saved["item"]["id"]]})
        remote = client.get(url + "/" + document["id"], headers=headers("organizer_agent", "viewer")).json()
        assert remote["record"]["tags"] == ["Soil"]
        assert remote["record"]["category"] == "Field work"
        assert remote["revision"] == saved["revision"]
        assert client.get(url, headers=headers("organizer_agent")).json()["items"] == [{"id": document["id"], "revision": saved["revision"]}]
        assert content_native.import_record(document)["revision"] == saved["revision"]
        updated = content_native.import_record({**document, "body": "Updated", "base_revision": saved["revision"]})
        with pytest.raises(FileExistsError):
            content_native.import_record({**document, "body": "Stale", "base_revision": saved["revision"]})
        record_url = url + "/" + document["id"]
        assert client.delete(record_url, params={"base_revision": saved["revision"]}, headers=headers("organizer_agent")).status_code == 409
        assert client.delete(record_url, params={"base_revision": updated["revision"]}, headers=headers("organizer_agent", "editor")).status_code == 403
        configure_organization(auto_organize=False, semantic_labels=False, content_sync_enabled=False)
        assert client.get(url, headers=headers("organizer_agent")).status_code == 403
        configure_organization(auto_organize=False, semantic_labels=False, content_sync_enabled=True)
        assert client.delete(record_url, params={"base_revision": updated["revision"]}, headers=headers("organizer_agent")).status_code == 200
        assert client.get(record_url, headers=headers("organizer_agent")).status_code == 404
        with pytest.raises(FileExistsError):
            content_native.import_record(document)


def test_reminder_tone_replay_scheduler_edits_and_privacy(tmp_path, monkeypatch):
    from autoyou_agents.reminders_agent.website.backend import collection
    from autoyou_agents.shared_tools import scheduler_mission_control as mission
    from shared import scheduler_service as scheduler
    store = tmp_path / "reminders.json"
    monkeypatch.setattr(scheduler, "REMINDERS_FILE", store)
    monkeypatch.setattr(mission, "REMINDERS_FILE", store)
    monkeypatch.setattr(mission, "_api_auth_error", lambda *args: None)
    app = FastAPI()
    collection.register(app)
    paired_content.install_collection_sync(app, "reminders_agent")
    hdr = headers("reminders_agent")
    raw = b"synthetic tone" * 90000
    digest = hashlib.sha256(raw).hexdigest()
    uri = "/api/sync/tones/" + digest
    reminder = "/api/sync/reminders/" + str(uuid.uuid4())
    with TestClient(app, client=("127.0.0.1", 41000)) as client:
        for offset in (0, 0, 1024 * 1024):
            result = client.put(uri, headers=hdr, params={"size": len(raw), "offset": offset}, content=raw[offset:offset+1024*1024])
            assert result.status_code == 200
        assert client.get(uri, headers={**hdr, "Range": "bytes=100-129"}).content == raw[100:130]
        data = {"title": "Walk", "body": "Along the river", "base_revision": "", "time_zone": "UTC",
                "reminder": {"due_at": 1893456000000, "critical": False, "completed": False, "tone_attachment_id": "tone"},
                "tone": {"digest": digest, "mime": "audio/wav", "name": "River.wav"}}
        saved = client.put(reminder, headers=hdr, json=data)
        assert saved.status_code == 200, saved.text
        assert saved.json()["scheduled"] is False
        assert scan_reminders(ContentLibrary()) == 0
        configure_organization(auto_organize=False, semantic_labels=False, index_reminders=True)
        assert scan_reminders(ContentLibrary()) == 1
        configure_organization(auto_organize=False, semantic_labels=False, index_reminders=False)
        with ContentLibrary().connect() as db:
            assert db.execute("SELECT title,source_text,deleted FROM content WHERE source='reminders_agent'").fetchone()[:] == ("", "", 1)
        identifier = saved.json()["id"]
        mission._update_reminder_from_payload(identifier, {"target_time_iso": "2030-01-02T00:00:00+00:00"})
        changed = client.get(reminder, headers=hdr).json()
        assert changed["reminder"]["due_at"] == 1893542400000
        assert changed["revision"] != saved.json()["revision"]
        assert client.put(reminder, headers=hdr, json={**data, "base_revision": saved.json()["revision"]}).status_code == 409
        assert client.delete(reminder, headers=hdr, params={"base_revision": changed["revision"]}).status_code == 200
        assert client.get(reminder, headers=hdr).status_code == 404


def test_last_snooze_after_repeat_ended():
    alarm = {"due_at": 2000, "completed": True, "snoozed_until": 5000, "repeat": {"count": 1, "fired": 1}}
    assert reminder_repeat.next_alert(alarm) == 5000
    fired = reminder_repeat.after_delivery({"native_reminder": alarm}, 5)["native_reminder"]
    assert fired["completed"] and fired["repeat"]["fired"] == 1
    assert reminder_repeat.next_alert(fired) is None


def test_games_catalog_pages_and_conditional_replace(tmp_path):
    root = tmp_path / "shipped"
    games = root / "assets/game"
    games.mkdir(parents=True)
    for i in range(68):
        (games / f"level-{i:02}.html").write_text("<html><head><title>Level</title></head><body><button onclick='AutoYouGame.quit()'>Quit</button></body></html>")
    catalog = GamePackages(root, tmp_path / "owned")
    first = catalog.listing()
    second = catalog.listing(first["next_after"])
    ids = [row["id"] for page in [first, second] for row in page["games"]]
    assert ids == [f"level-{i:02}" for i in range(68)]
    assert second["next_after"] is None
    version = first["games"][0]["revision"]
    catalog.check_revision(ids[0], version)
    with pytest.raises(FileExistsError):
        catalog.check_revision(ids[0], "")
