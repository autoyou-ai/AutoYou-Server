"""Isolated native post, attachment and editing regression coverage."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from autoyou_agents.page_agent.website.backend.app import PageFeedService
from page_feed_db import PageFeedDB


@pytest.fixture
def feed(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    service = PageFeedService()
    app = FastAPI()
    service.register_routes(app)
    return service, TestClient(app)


def photo(client, name="synthetic.png"):
    data = BytesIO()
    Image.new("RGB", (32, 24), "#446688").save(data, "PNG")
    response = client.post("/api/blob", files={"file": (name, data.getvalue(), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def post_payload(blob_ids):
    return {"client_post_id": "synthetic-native-post", "title": "A single post", "content": "Synthetic transcript",
            "attachments": [{"blob_id": blob, "client_media_id": f"synthetic-media-{index}"} for index, blob in enumerate(blob_ids)]}


def test_transcript_and_attachments_form_one_retryable_post(feed):
    service, client = feed
    blobs = [photo(client, f"photo-{index}.png") for index in range(3)]
    payload = post_payload(blobs)
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: client.post("/api/feed", json=payload), range(3)))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    ids = {r.json()["item"]["id"] for r in responses}
    assert len(ids) == 1
    item_id = ids.pop()
    assert len(service.db.load_all()) == 1
    item = client.get("/api/feed/page").json()["items"][0]
    assert item["content"] == payload["content"]
    assert [a["blob_id"] for a in item["attachments"]] == blobs
    assert all(a["type"] == "image" for a in item["attachments"])
    assert client.get(f"/api/feed/item/{item_id}").json()["item"]["attachments"] == item["attachments"]

    service.db.set_favourite(item_id, True)
    payload.update(title="Edited post", attachments=list(reversed(payload["attachments"])))
    edited = client.post("/api/feed", json=payload).json()["item"]
    assert edited["id"] == item_id and edited["favourite"]
    assert [a["blob_id"] for a in edited["attachments"]] == blobs[::-1]
    restarted = PageFeedDB(service.db.db_path)
    assert len(restarted.load_all()) == 1
    assert restarted.post_details(restarted.load_all())[0]["attachments"][0]["blob_id"] == blobs[-1]


def test_missing_or_duplicate_attachment_cannot_partially_publish(feed):
    service, client = feed
    blob = photo(client)
    payload = post_payload([blob])
    item_id = client.post("/api/feed", json=payload).json()["item"]["id"]
    payload["title"] = "Must roll back"
    payload["attachments"].append({"blob_id": "missing-synthetic-blob", "client_media_id": "synthetic-missing"})
    assert client.post("/api/feed", json=payload).status_code == 400
    kept = client.get(f"/api/feed/item/{item_id}").json()["item"]
    assert kept["title"] == "A single post" and len(kept["attachments"]) == 1
    payload["attachments"] = [payload["attachments"][0]] * 2
    assert client.post("/api/feed", json=payload).status_code == 400
    assert len(service.db.load_all()) == 1


def test_voice_note_and_transcript_share_one_audio_post(feed):
    _, client = feed
    data = b"\xff\xf1\x50\x80\x01\x7f\xfc" + bytes(64)
    response = client.post("/api/blob", files={"file": ("synthetic.aac", data, "audio/aac")})
    blob = response.json()["id"]
    posted = client.post("/api/feed", json=post_payload([blob]))
    assert posted.status_code == 200, posted.text
    items = client.get("/api/feed/page?tab=audio").json()["items"]
    assert len(items) == 1 and items[0]["content"] == "Synthetic transcript"
    assert items[0]["attachments"][0]["type"] == "audio"


def test_deleting_gallery_preserves_shared_blobs_and_cleans_the_rest(feed):
    service, client = feed
    blobs = [photo(client, f"synthetic-{i}.png") for i in range(2)]
    first = client.post("/api/feed", json=post_payload(blobs)).json()["item"]
    second_payload = post_payload([blobs[1]])
    second_payload["client_post_id"] = "synthetic-second-post"
    second = client.post("/api/feed", json=second_payload).json()["item"]
    assert client.delete(f"/api/feed/{first['id']}").status_code == 200
    assert service.db.get_blob_meta(blobs[0]) is None
    assert client.get(f"/api/blob/{blobs[1]}", headers={"Range": "bytes=0-0"}).status_code == 206
    assert client.delete(f"/api/feed/{second['id']}").status_code == 200
    assert service.db.get_blob_meta(blobs[1]) is None


def test_link_text_edit_reclassifies_platform_and_enforces_roles(feed):
    _, client = feed
    item_id = client.post("/api/feed", json={"url": "https://example.test/original", "title": "Original"}).json()["item"]["id"]
    payload = {"title": "Updated", "url": "https://www.youtube.com/watch?v=abc123DEF45", "content": "A caption"}
    route = f"/api/item/{item_id}/update"
    viewer = {"X-AutoYou-WebRTC-Session-Id": "synthetic-session", "X-AutoYou-Remote-Access-Role": "viewer"}
    assert client.post(route, json=payload, headers=viewer).status_code == 403
    updated = client.post(route, json=payload, headers={**viewer, "X-AutoYou-Remote-Access-Role": "editor"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["item"]["type"] == "youtube"
    assert updated.json()["item"]["content"] == "A caption"
    assert client.post(route, json={**payload, "url": "javascript:alert(1)"}).status_code == 400
    assert client.get(f"/api/feed/item/{item_id}").json()["item"]["url"] == payload["url"]


def test_migration_preserves_old_posts(tmp_path):
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE feed_items (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, url TEXT NOT NULL, title TEXT, source TEXT, added_at TEXT NOT NULL)")
        connection.execute("INSERT INTO feed_items(type,url,title,added_at) VALUES ('text','','Synthetic legacy post','2026-01-01T12:00:00')")
    db = PageFeedDB(path)
    assert db.load_all()[0]["title"] == "Synthetic legacy post"
    assert db.post_details(db.load_all())[0]["attachments"] == []
