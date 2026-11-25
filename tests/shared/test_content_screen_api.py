"""The My Content screen's API: index, changes, groups, previews, removal and roles.

Every test runs in a throwaway AUTOYOU_TEST_ROOT with synthetic content only.
"""
import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared import content_agents, content_files, content_index, content_thumbs, secure_storage
from shared.content_library import ContentLibrary, output_dir

PREFIX = "/api/admin/content"
REMOTE = {"X-AutoYou-Remote-Browser": "webrtc"}


def remote(role):
    return {**REMOTE, "X-AutoYou-Remote-Access-Role": role}


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    secure_storage.disable_secure_storage()
    content_thumbs.clear()
    yield tmp_path
    secure_storage.disable_secure_storage()


@pytest.fixture
def client(sandbox):
    from shared.content_library_api import register_content_routes
    app = FastAPI()
    register_content_routes(app, lambda request: None)
    with TestClient(app) as test_client:
        yield test_client


def png(width=64, height=48, mode="RGB"):
    from PIL import Image
    data = io.BytesIO()
    Image.new(mode, (width, height), (80, 60, 100) if mode == "RGB" else (80, 60, 100, 128)).save(data, "PNG")
    return data.getvalue()


def add_file(client, name, data, source="files_agent"):
    response = client.post(f"{PREFIX}/import?filename={name}&source={source}", content=data)
    assert response.status_code == 201, response.text
    return response.json()


def native_record(identifier, **extra):
    record = {"id": identifier, "kind": "custom_garden_a1b2c3", "base_kind": "notes", "title": "Seed list", "body": "Tomatoes, basil, mint.",
              "updated_at": 100, "attachments": []}
    return {**record, **extra}


# ---- the schema is prepared once, not on every request ------------------------------------------
def test_schema_runs_once_per_catalog_and_again_after_it_is_deleted(sandbox, monkeypatch):
    calls = []
    original = ContentLibrary._migrate
    monkeypatch.setattr(ContentLibrary, "_migrate", lambda self: (calls.append(1), original(self))[1])
    ContentLibrary()
    ContentLibrary()
    ContentLibrary()
    assert len(calls) == 1
    ContentLibrary().db_path.unlink()
    ContentLibrary()
    assert len(calls) == 2


def test_live_indexes_exist(sandbox):
    library = ContentLibrary()
    with library.connect() as db:
        names = {row[1] for row in db.execute("PRAGMA index_list(content)")}
    assert {"content_live_updated", "content_live_source", "content_live_kind"} <= names


# ---- summary -------------------------------------------------------------------------------------
def test_summary_names_every_agent_and_is_conditional(client):
    library = ContentLibrary()
    library.publish("notes_agent", "1", "Synthetic note", "note", href="/agent/notes_agent/")
    library.publish("page_agent", "2", "Synthetic page", "page", href="/agent/page_agent/", source_collection="References")
    assert client.post(f"{PREFIX}/native", json=native_record("00000000-0000-4000-8000-000000000001",
                       agent_name="Garden Planner", agent_handle="@garden", agent_icon="leaf", agent_accent="#22AA66")).status_code == 200
    first = client.get(f"{PREFIX}/summary")
    assert first.status_code == 200
    body = first.json()
    agents = {agent["source"]: agent for agent in body["agents"]}
    assert agents["notes_agent"]["name"] == "Notes" and agents["notes_agent"]["removal"] == "agent"
    assert agents["page_agent"]["name"] == "Pages"
    garden = agents["custom_garden_a1b2c3"]
    assert (garden["name"], garden["handle"], garden["icon"], garden["accent"]) == ("Garden Planner", "garden", "note", "#22AA66")
    assert garden["native"] and garden["removal"] == "catalog"
    assert body["total"] == 3 and body["kinds"] == {"note": 2, "page": 1}
    assert body["collections"] == [{"name": "References", "count": 1}]
    assert body["abilities"]["role"] == "owner" and body["abilities"]["delete"]
    # Asking again with the validator costs no body.
    tag = first.headers["etag"]
    again = client.get(f"{PREFIX}/summary", headers={"If-None-Match": tag})
    assert again.status_code == 304 and not again.content
    # A write moves the revision, so the old validator no longer matches.
    library.publish("notes_agent", "3", "Another synthetic note", "note")
    assert client.get(f"{PREFIX}/summary", headers={"If-None-Match": tag}).status_code == 200


def test_unnamed_custom_agents_get_a_readable_name_and_bad_identity_is_dropped(client):
    assert content_agents.fallback_name("custom_garden_planner_a1b2c3") == "Garden Planner"
    assert content_agents.fallback_name("custom_x") == "X"
    clean = content_agents.clean_identity({"agent_name": "  A   B  " + "x" * 80, "agent_handle": "@Not Valid!", "agent_icon": "<img src=x>",
                                           "agent_accent": "red", "base_kind": "notes"})
    assert clean["name"].startswith("A B x") and len(clean["name"]) <= 48
    assert (clean["handle"], clean["icon"], clean["accent"]) == ("", "", "")
    assert client.post(f"{PREFIX}/native", json=native_record("00000000-0000-4000-8000-000000000002",
                       agent_icon="<script>", agent_accent="url(x)")).status_code == 200
    agent = next(a for a in client.get(f"{PREFIX}/summary").json()["agents"] if a["source"] == "custom_garden_a1b2c3")
    assert agent["name"] == "Garden" and agent["accent"] == "" and agent["icon"] == "note"
    # A later record keeps the better identity it already has and updates what changed.
    assert client.post(f"{PREFIX}/native", json=native_record("00000000-0000-4000-8000-000000000003", agent_name="Seeds", agent_accent="#112233")).status_code == 200
    agent = next(a for a in client.get(f"{PREFIX}/summary").json()["agents"] if a["source"] == "custom_garden_a1b2c3")
    assert (agent["name"], agent["accent"]) == ("Seeds", "#112233")


# ---- changes: build the index once, then only ask for what moved --------------------------------
def test_changes_walk_pages_without_gaps_and_never_leak_private_fields(client):
    library = ContentLibrary()
    for number in range(7):
        library.publish("notes_agent", str(number), f"Synthetic note {number}", "note", source_text="private body " + str(number))
    seen, cursor = [], 0
    for _ in range(10):
        page = client.get(f"{PREFIX}/changes", params={"after": cursor, "limit": 3}).json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["cursor"]
        if not page["has_more"]:
            break
    assert len(seen) == len(set(seen)) == 7
    sample = page["items"][0]
    assert not {"path", "managed_path", "digest", "source_text", "file_stamp", "suggested_labels", "organization_basis"} & set(sample)
    assert sample["title"].startswith("Synthetic note") and "private body" not in json.dumps(page)


def test_changes_report_removals_as_tombstones_after_the_first_walk(client):
    library = ContentLibrary()
    keep = library.publish("notes_agent", "1", "Keep", "note")
    gone = library.publish("notes_agent", "2", "Gone", "note")
    library.forget("notes_agent", "2")
    first = client.get(f"{PREFIX}/changes", params={"after": 0}).json()
    assert [item["id"] for item in first["items"]] == [keep["id"]]
    later = client.get(f"{PREFIX}/changes", params={"after": keep["revision"]}).json()
    assert later["items"] == [{"id": gone["id"], "source": "notes_agent", "deleted": 1, "revision": later["items"][0]["revision"]}]
    assert client.get(f"{PREFIX}/changes", params={"after": later["cursor"]}).json()["items"] == []


def test_changes_are_conditional_and_large_answers_are_gzipped_for_callers_that_accept_it(client):
    library = ContentLibrary()
    for number in range(40):
        library.publish("notes_agent", str(number), f"Synthetic note with a long enough title to compress {number}", "note")
    response = client.get(f"{PREFIX}/changes", params={"after": 0, "limit": 100}, headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip" and len(response.json()["items"]) == 40
    plain = client.get(f"{PREFIX}/changes", params={"after": 0, "limit": 100}, headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    assert client.get(f"{PREFIX}/changes", params={"after": 0, "limit": 100}, headers={"If-None-Match": response.headers["etag"]}).status_code == 304


# ---- groups and the filtered index ---------------------------------------------------------------
def test_groups_return_the_newest_items_per_agent_in_one_call(client):
    library = ContentLibrary()
    for number in range(5):
        library.publish("notes_agent", str(number), f"Note {number}", "note", source_collection="Studio")
    for number in range(2):
        library.publish("page_agent", str(number), f"Page {number}", "page")
    result = client.get(f"{PREFIX}/groups", params={"per": 3}).json()
    by_source = {group["source"]: group for group in result["groups"]}
    assert by_source["notes_agent"]["count"] == 5 and len(by_source["notes_agent"]["items"]) == 3
    assert by_source["page_agent"]["count"] == 2 and len(by_source["page_agent"]["items"]) == 2
    updated = [item["updated"] for item in by_source["notes_agent"]["items"]]
    assert updated == sorted(updated, reverse=True)
    studio = client.get(f"{PREFIX}/groups", params={"collection": "Studio"}).json()
    assert [group["source"] for group in studio["groups"]] == ["notes_agent"]
    searched = client.get(f"{PREFIX}/groups", params={"q": "page 1"}).json()
    assert searched["groups"][0]["count"] == 1


def test_index_listing_sorts_and_filters(client):
    library = ContentLibrary()
    library.publish("notes_agent", "1", "Charlie", "note")
    library.publish("notes_agent", "2", "alpha", "note")
    library.publish("page_agent", "3", "Bravo", "page")
    titles = [item["title"] for item in client.get(f"{PREFIX}/index", params={"sort": "title"}).json()["items"]]
    assert titles == ["alpha", "Bravo", "Charlie"]
    only_pages = client.get(f"{PREFIX}/index", params={"kind": "page"}).json()
    assert only_pages["total"] == 1 and only_pages["items"][0]["title"] == "Bravo"


# ---- preview and thumbnails ---------------------------------------------------------------------
def test_preview_bounds_text_and_describes_the_agent_and_removal(client):
    library = ContentLibrary()
    note = library.publish("notes_agent", "1", "Long note", "note", source_text="x" * 9000)
    body = client.get(f"{PREFIX}/items/{note['id']}/preview").json()
    assert len(body["text"]) == 4000 and body["truncated"] is True
    assert body["agent"]["name"] == "Notes" and body["removal"]["mode"] == "agent" and body["media"] is None
    record = native_record("00000000-0000-4000-8000-000000000004", body="Native record body")
    saved = client.post(f"{PREFIX}/native", json=record).json()["item"]
    custom = client.get(f"{PREFIX}/items/{saved['id']}/preview").json()
    assert custom["text"] == "Native record body" and custom["removal"]["mode"] == "catalog"
    assert client.get(f"{PREFIX}/items/missing/preview").status_code == 404


def test_thumbnails_are_small_cached_by_revision_and_only_for_images(client):
    image = add_file(client, "photo.png", png(1200, 900))
    thumb = client.get(f"{PREFIX}/items/{image['id']}/thumbnail", params={"size": 150})
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    from PIL import Image
    shrunk = Image.open(io.BytesIO(thumb.content))
    assert max(shrunk.size) == 192 and len(thumb.content) < 20000
    assert thumb.headers["cache-control"] == "private, no-store"
    assert client.get(f"{PREFIX}/items/{image['id']}/thumbnail", params={"size": 150}, headers={"If-None-Match": thumb.headers["etag"]}).status_code == 304
    assert client.get(f"{PREFIX}/items/{add_file(client, 'doc.txt', b'plain')['id']}/thumbnail").status_code == 404
    alpha = add_file(client, "badge.png", png(40, 40, "RGBA"))
    assert client.get(f"{PREFIX}/items/{alpha['id']}/thumbnail").headers["content-type"] == "image/png"


def test_thumbnails_of_a_sealed_library_are_made_in_memory_only(client, sandbox):
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=sandbox, password="synthetic-library-password")
    image = add_file(client, "private.png", png(500, 300))
    stored = next((sandbox).rglob("*private*")) if list(sandbox.rglob("*private*")) else None
    thumb = client.get(f"{PREFIX}/items/{image['id']}/thumbnail", params={"size": 96})
    assert thumb.status_code == 200 and thumb.content[:2] == b"\xff\xd8"
    # Nothing readable was written next to the sealed file.
    assert not [path for path in sandbox.rglob("*") if path.is_file() and path.suffix in {".jpg", ".jpeg"} and "private" in path.name]
    assert stored is None or not stored.read_bytes().startswith(b"\x89PNG")


def test_oversized_images_and_corrupt_files_have_no_preview(client, monkeypatch):
    monkeypatch.setattr(content_thumbs, "MAX_PIXELS", 100)
    image = add_file(client, "huge.png", png(64, 64))
    assert client.get(f"{PREFIX}/items/{image['id']}/thumbnail").status_code == 404
    monkeypatch.setattr(content_thumbs, "MAX_PIXELS", 80_000_000)
    broken = add_file(client, "broken.png", b"not really a png")
    assert client.get(f"{PREFIX}/items/{broken['id']}/thumbnail").status_code == 404


def test_download_flag_forces_a_save_for_media_types(client):
    audio = add_file(client, "song.mp3", b"0123456789", source="custom_music")
    inline = client.get(f"{PREFIX}/items/{audio['id']}/content")
    assert "content-disposition" not in inline.headers
    saved = client.get(f"{PREFIX}/items/{audio['id']}/content", params={"download": 1})
    assert saved.headers["content-disposition"].startswith("attachment;") and saved.content == b"0123456789"


# ---- removal ------------------------------------------------------------------------------------
def test_removing_a_catalog_owned_file_deletes_the_file_once_and_is_conditional_on_revision(client, sandbox):
    item = add_file(client, "notes.txt", b"synthetic file")
    assert len(list(output_dir("files_agent").glob("*.txt"))) == 1
    stale = client.delete(f"{PREFIX}/items/{item['id']}", params={"expected_revision": item["revision"] + 5})
    assert stale.status_code == 409 and stale.json()["stale"] is True
    done = client.delete(f"{PREFIX}/items/{item['id']}", params={"expected_revision": item["revision"]})
    assert done.status_code == 200 and done.json()["mode"] == "catalog" and done.json()["files"] == 1
    assert not list(output_dir("files_agent").glob("*.txt"))
    assert client.get(f"{PREFIX}/items/{item['id']}").status_code == 404
    assert client.delete(f"{PREFIX}/items/{item['id']}", params={"expected_revision": item["revision"]}).status_code == 404
    assert client.get(f"{PREFIX}/changes", params={"after": item["revision"]}).json()["items"][0]["deleted"] == 1


def test_a_file_shared_with_another_item_stays_until_the_last_item_goes(client, sandbox):
    first = add_file(client, "one.txt", b"shared bytes")
    path = next(output_dir("files_agent").glob("*.txt"))
    library = ContentLibrary()
    twin = library.publish("files_agent", "twin", "Same file again", "file", path=str(path))
    client.delete(f"{PREFIX}/items/{first['id']}", params={"expected_revision": first["revision"]})
    assert path.exists(), "another item still uses this file"
    client.delete(f"{PREFIX}/items/{twin['id']}", params={"expected_revision": twin["revision"]})
    assert not path.exists()


def test_an_original_outside_the_managed_folders_is_never_deleted(client, sandbox):
    original = sandbox / "elsewhere" / "original.mp3"
    original.parent.mkdir()
    original.write_bytes(b"the owner's own file")
    row = ContentLibrary().publish("files_agent", "linked", "Linked original", "audio", path=str(original))
    done = client.delete(f"{PREFIX}/items/{row['id']}", params={"expected_revision": row["revision"]})
    assert done.status_code == 200 and done.json()["files"] == 0
    assert original.read_bytes() == b"the owner's own file"
    link = sandbox / "content_library" / "content" / "files_agent" / "link.mp3"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(original)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable here")
    row = ContentLibrary().publish("files_agent", "symlink", "Symlinked", "audio", path=str(link))
    client.delete(f"{PREFIX}/items/{row['id']}", params={"expected_revision": row["revision"]})
    assert original.exists()


def test_a_record_from_a_phone_is_removed_with_its_attachments(client, sandbox):
    session = {"X-AutoYou-WebRTC-Session-Id": "synthetic-session"}
    pending = client.post(f"{PREFIX}/upload-sessions", headers=session,
                          json={"filename": "photo.png", "size": 3, "source": "custom_garden_a1b2c3", "source_id": "att-1"}).json()
    client.put(f"{PREFIX}/upload-sessions/{pending['id']}?offset=0", content=b"abc", headers=session)
    attachment = client.post(f"{PREFIX}/upload-sessions/{pending['id']}/complete", headers=session).json()["item"]
    record = native_record("00000000-0000-4000-8000-000000000005", attachments=[attachment["id"]])
    saved = client.post(f"{PREFIX}/native", json=record).json()["item"]
    stored = next(output_dir("custom_garden_a1b2c3").glob("*.png"))
    assert client.get(f"{PREFIX}/summary").json()["total"] == 2
    done = client.delete(f"{PREFIX}/items/{saved['id']}", params={"expected_revision": saved["revision"]}).json()
    assert set(done["removed"]) == {saved["id"], attachment["id"]} and done["files"] == 1
    assert not stored.exists()
    assert client.get(f"{PREFIX}/native/{saved['source']}/{record['id']}").status_code == 404
    assert client.get(f"{PREFIX}/summary").json()["total"] == 0


def test_an_agent_owned_note_is_removed_through_the_notes_agent(client):
    from autoyou_agents.notes_agent.notes_tool import NotesTool
    notes = NotesTool()
    note_id = notes.create_note(title="Synthetic note", content="Body")["note_id"]
    row = ContentLibrary().list(source="notes_agent")["items"][0]
    assert row["source_id"] == str(note_id)
    done = client.delete(f"{PREFIX}/items/{row['id']}", params={"expected_revision": row["revision"]})
    assert done.status_code == 200 and done.json()["mode"] == "agent"
    assert notes.delete_note(note_id) is False, "the Notes agent no longer has it"
    assert ContentLibrary().list(source="notes_agent")["total"] == 0


def test_an_agent_without_a_safe_removal_sends_the_person_to_its_app(client):
    row = ContentLibrary().publish("location_agent", "2026-11-20", "Locations on 2026-11-20", "location", href="/agent/location_agent/")
    refused = client.delete(f"{PREFIX}/items/{row['id']}", params={"expected_revision": row["revision"]})
    assert refused.status_code == 409
    assert refused.json()["mode"] == "open" and refused.json()["app"] == "/agent/location_agent/"
    assert ContentLibrary().list(source="location_agent")["total"] == 1
    attachment = ContentLibrary().publish("notes_agent", "attachment:7", "Photo.png", "image", path="")
    assert client.delete(f"{PREFIX}/items/{attachment['id']}", params={"expected_revision": attachment["revision"]}).json()["mode"] == "open"


def test_batch_removal_reports_each_item_and_continues_past_failures(client):
    one = add_file(client, "one.txt", b"1")
    two = add_file(client, "two.txt", b"2")
    location = ContentLibrary().publish("location_agent", "d", "Locations", "location")
    result = client.post(f"{PREFIX}/items/delete", json={"items": [
        {"id": one["id"], "revision": one["revision"]},
        {"id": two["id"], "revision": two["revision"] + 9},
        {"id": location["id"], "revision": location["revision"]},
        {"id": "nothing-here", "revision": 1},
    ]}).json()
    outcomes = {entry["id"]: entry for entry in result["results"]}
    assert result["removed"] == 1 and outcomes[one["id"]]["ok"]
    assert outcomes[two["id"]]["stale"] and outcomes[location["id"]]["mode"] == "open" and outcomes["nothing-here"]["missing"]
    assert client.post(f"{PREFIX}/items/delete", json={"items": []}).status_code == 422
    assert client.post(f"{PREFIX}/items/delete", json={"items": [{"id": str(n), "revision": 1} for n in range(101)]}).status_code == 422


def test_batch_organize_merges_labels_and_reports_stale_items(client):
    library = ContentLibrary()
    one = library.publish("notes_agent", "1", "One", "note")
    two = library.publish("notes_agent", "2", "Two", "note")
    result = client.post(f"{PREFIX}/items/organize", json={
        "items": [{"id": one["id"], "revision": one["revision"]}, {"id": two["id"], "revision": two["revision"] + 3}],
        "collection": "Project", "add_labels": ["Draft", "draft", "urgent"], "remove_labels": ["NOTE"]}).json()
    assert result["updated"] == 1
    changed = next(entry for entry in result["results"] if entry["ok"])["item"]
    assert changed["collection"] == "Project" and changed["labels"] == ["notes", "Draft", "draft", "urgent"] or "Draft" in changed["labels"]
    assert "note" not in [label.lower() for label in changed["labels"]]
    assert next(entry for entry in result["results"] if not entry["ok"])["stale"] is True
    assert client.post(f"{PREFIX}/items/organize", json={"items": [{"id": one["id"], "revision": 1}]}).status_code == 400


# ---- roles ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("role,edit,upload,delete,manage", [
    ("viewer", False, False, False, False),
    ("editor", True, True, False, False),
    ("admin", True, True, True, True),
])
def test_remote_roles_are_enforced_by_the_server_not_only_the_screen(client, role, edit, upload, delete, manage):
    row = add_file(client, "role.txt", b"role")
    headers = {**remote(role), "X-AutoYou-WebRTC-Session-Id": "synthetic-session"}
    abilities = client.get(f"{PREFIX}/summary", headers=headers).json()["abilities"]
    assert abilities == {"role": role, "read": True, "edit": edit, "upload": upload, "delete": delete, "manage": manage}
    assert client.get(f"{PREFIX}/changes", headers=headers).status_code == 200
    annotate = {"expected_revision": row["revision"], "labels": [], "annotation": "", "collection": "X"}
    assert (client.patch(f"{PREFIX}/items/{row['id']}", json=annotate, headers=headers).status_code == 200) is edit
    assert (client.post(f"{PREFIX}/upload-sessions", json={"filename": "a.txt", "size": 1}, headers=headers).status_code == 200) is upload
    assert (client.put(f"{PREFIX}/organization", json={"auto_organize": True, "semantic_labels": True}, headers=headers).status_code == 200) is manage
    current = client.get(f"{PREFIX}/items/{row['id']}").json()
    outcome = client.delete(f"{PREFIX}/items/{row['id']}", params={"expected_revision": current["revision"]}, headers=headers)
    assert (outcome.status_code == 200) is delete
    if not delete:
        assert outcome.status_code == 403 and role in outcome.json()["error"]


def test_a_role_header_can_only_narrow_access_and_the_owner_needs_neither_header(client):
    row = add_file(client, "owner.txt", b"owner")
    assert client.get(f"{PREFIX}/summary").json()["abilities"]["role"] == "owner"
    forged = {"X-AutoYou-Remote-Access-Role": "viewer"}
    assert client.get(f"{PREFIX}/summary", headers=forged).json()["abilities"]["delete"] is False
    unknown = {**REMOTE, "X-AutoYou-Remote-Access-Role": "superuser"}
    assert client.get(f"{PREFIX}/summary", headers=unknown).json()["abilities"]["delete"] is False
    assert client.delete(f"{PREFIX}/items/{row['id']}", params={"expected_revision": row["revision"]}, headers=forged).status_code == 403
    assert client.post(f"{PREFIX}/items/delete", json={"items": [{"id": row["id"], "revision": row["revision"]}]}, headers=remote("editor")).status_code == 403


def test_authorization_runs_before_anything_is_read(sandbox):
    from fastapi.responses import JSONResponse
    from shared.content_library import state_root
    from shared.content_library_api import register_content_routes
    app = FastAPI()
    register_content_routes(app, lambda request: JSONResponse({"error": "unauthorized"}, status_code=401))
    with TestClient(app) as locked:
        for path in ("summary", "changes", "groups", "index", "items/x/preview", "items/x/thumbnail"):
            assert locked.get(f"{PREFIX}/{path}").status_code == 401, path
        assert locked.delete(f"{PREFIX}/items/x", params={"expected_revision": 1}).status_code == 401
        assert locked.post(f"{PREFIX}/items/delete", json={"items": [{"id": "x", "revision": 1}]}).status_code == 401
        assert locked.post(f"{PREFIX}/items/organize", json={"items": [{"id": "x", "revision": 1}], "collection": "c"}).status_code == 401
    assert not state_root().exists()


def test_the_screen_assets_are_served_by_the_content_api(client, sandbox, monkeypatch):
    from shared import content_library_api
    assets = sandbox / "bundle" / "assets"
    assets.mkdir(parents=True)
    for name in ("content-store.js", "content-screen.js", "content-screen.css"):
        (assets / name).write_text("/* " + name + " */")
    monkeypatch.setattr(content_library_api, "get_resources_root", lambda anchor: assets.parent)
    app = FastAPI()
    content_library_api.register_content_routes(app, lambda request: None)
    with TestClient(app) as served:
        for name in ("content-store.js", "content-screen.js", "content-screen.css"):
            assert served.get(f"{PREFIX}/assets/{name}").text == "/* " + name + " */"
        assert served.get(f"{PREFIX}/assets/admin-ui.js").status_code == 404


def test_diary_entries_are_listed_only_when_the_owner_opts_in_and_leave_when_it_is_turned_off(client, monkeypatch):
    from autoyou_agents.persona_agent import agent as persona
    from shared.content_library_scan import refresh_existing
    entry = {"id": "00000000-0000-4000-8000-0000000000d1", "date": "2026-11-20", "time": "08:00", "title": "A good day",
             "preview": "Went for a long synthetic walk", "has_media": False}
    monkeypatch.setattr(persona, "list_diary_entries", lambda order="desc": [entry])
    refresh_existing()
    assert ContentLibrary().list(source="persona_agent")["total"] == 0, "private self-data is never listed by default"
    assert client.get(f"{PREFIX}/summary").json()["settings"]["index_private"] is False
    on = {"auto_organize": True, "semantic_labels": True, "index_private": True}
    assert client.put(f"{PREFIX}/organization", json=on).status_code == 200
    refresh_existing()
    listed = ContentLibrary().list(source="persona_agent")["items"]
    assert [row["title"] for row in listed] == ["A good day"] and listed[0]["kind"] == "note"
    assert client.get(f"{PREFIX}/items/{listed[0]['id']}/preview").json()["removal"]["mode"] == "open"
    # An older screen that does not know the option must not switch it off.
    assert client.put(f"{PREFIX}/organization", json={"auto_organize": True, "semantic_labels": True}).json()["index_private"] is True
    assert client.put(f"{PREFIX}/organization", json={**on, "index_private": False}).json()["index_private"] is False
    refresh_existing()
    assert ContentLibrary().list(source="persona_agent")["total"] == 0


def test_document_previews_are_inert_and_keep_attachment_downloads(client):
    import zipfile
    pdf = add_file(client, "garden.pdf", b"%PDF-1.7\nsynthetic")["id"]
    route = f"{PREFIX}/items/{pdf}/content"
    assert client.get(route).headers["Content-Disposition"].startswith("attachment")
    preview = client.get(route, params={"preview": True}, headers={"Range": "bytes=0-4"})
    assert preview.status_code == 206 and preview.content == b"%PDF-"
    assert preview.headers["Content-Type"] == "application/pdf"
    assert preview.headers["Content-Security-Policy"] == "sandbox"
    assert preview.headers["X-Content-Type-Options"] == "nosniff"
    renamed = add_file(client, "garden-page.pdf", b"<html>plant list</html>")["id"]
    assert "attachment" in client.get(f"{PREFIX}/items/{renamed}/content?preview=true").headers["Content-Disposition"]
    book = io.BytesIO()
    with zipfile.ZipFile(book, "w") as archive:
        archive.writestr("OEBPS/field.xhtml", "<html><body><p>Field notes</p><script>hidden code</script></body></html>")
    item = add_file(client, "field.epub", book.getvalue())["id"]
    assert client.get(f"{PREFIX}/items/{item}/preview").json()["text"] == "Field notes"
    tagged = ContentLibrary().publish("notes_agent", "42", "Field log", "note", source_labels=["Rosemary"])
    listing = client.get(PREFIX + "/index", params={"q": "rosemary"}).json()
    assert [row["id"] for row in listing["items"]] == [tagged["id"]]
    assert listing["items"][0]["source_labels"] == ["Rosemary"]
