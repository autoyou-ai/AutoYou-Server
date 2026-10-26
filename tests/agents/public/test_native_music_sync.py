"""Native Music imports and TV downloads use isolated synthetic media only."""
import hashlib

from fastapi.testclient import TestClient
import pytest

from autoyou_agents.audio_agent.website.backend import app as audio
from autoyou_agents.audio_agent.website.backend.uploads import CHUNK_BYTES, Uploads, import_root
from autoyou_agents.tv_agent.website.backend.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(audio, "_check_auth", lambda request: True)
    monkeypatch.setattr(audio, "resolve_audio_library_roots", lambda *args, **kwargs: [])
    with audio._library_lock:
        audio._library_cache["scanned"] = False
    with TestClient(audio.app) as client:
        yield client
    with audio._library_lock:
        audio._library_cache["scanned"] = False


def test_upload_resume_owner_boundary_idempotency_and_offline_download(client, tmp_path):
    content = bytes(range(256)) * 9000
    headers = {"Authorization": "Bearer synthetic-audio-session"}
    payload = {"filename": "synthetic.wav", "title": "Synthetic music", "size": len(content)}
    session = client.post("/api/upload-sessions", json=payload, headers=headers).json()
    route = "/api/upload-sessions/" + session["id"]
    assert import_root().is_relative_to(tmp_path)
    assert client.get(route, headers={"Authorization": "Bearer synthetic-other-session"}).status_code == 404
    assert client.put(route + "?offset=1", content=b"x", headers=headers).status_code == 409
    first = content[:CHUNK_BYTES]
    assert client.put(route + "?offset=0", content=first, headers=headers).json()["received"] == len(first)
    assert client.put(route + "?offset=0", content=first, headers=headers).json()["received"] == len(first)
    assert client.put(route + "?offset=0", content=b"conflict", headers=headers).status_code == 409
    assert client.post(route + "/complete", headers=headers).status_code == 409
    # New manager instances read the acknowledged offset from disk after a restart.
    assert Uploads().status(session["id"], hashlib.sha256(headers["Authorization"].encode()).hexdigest())["received"] == len(first)
    for offset in range(CHUNK_BYTES, len(content), CHUNK_BYTES):
        chunk = content[offset:offset + CHUNK_BYTES]
        assert client.put(route + f"?offset={offset}", content=chunk, headers=headers).json()["received"] == offset + len(chunk)
    item = client.post(route + "/complete", headers=headers).json()["item"]
    assert client.post(route + "/complete", headers=headers).json()["item"]["id"] == item["id"]
    assert item["title"] == "Synthetic music" and item["file_name"] == "synthetic.wav"
    assert client.get("/api/items/" + item["id"]).json()["id"] == item["id"]
    assert any(track["id"] == item["id"] for track in client.get("/api/library").json()["tracks"])
    received = bytearray()
    tag = None
    for offset in range(0, len(content), CHUNK_BYTES):
        response = client.get("/api/stream/" + item["id"], headers={"Range": f"bytes={offset}-{offset + CHUNK_BYTES - 1}", **({"If-Range": tag} if tag else {})})
        assert response.status_code == 206 and response.headers["Content-Type"].startswith("audio/wav")
        tag = response.headers["ETag"]
        received.extend(response.content)
    assert bytes(received) == content
    assert client.delete(route, headers=headers).status_code == 409


def test_music_upload_rejects_unauthenticated_unsafe_and_oversized_requests(client, monkeypatch):
    payload = {"filename": "synthetic.mp3", "title": "Synthetic", "size": 10}
    monkeypatch.setattr(audio, "_check_auth", lambda request: False)
    assert client.post("/api/upload-sessions", json=payload).status_code in {401, 403}
    monkeypatch.setattr(audio, "_check_auth", lambda request: True)
    assert client.post("/api/upload-sessions", json={**payload, "filename": "unsafe.html"}).status_code == 415
    assert client.post("/api/upload-sessions", json={**payload, "size": 1024 ** 3 + 1}).status_code == 422
    session = client.post("/api/upload-sessions", json={**payload, "size": CHUNK_BYTES + 1}).json()
    route = "/api/upload-sessions/" + session["id"]
    assert client.put(route + "?offset=0", content=b"a" * (CHUNK_BYTES + 1)).status_code == 413
    assert client.get(route).json()["received"] == 0
    assert client.delete(route).status_code == 200
    assert client.get(route).status_code == 404


def test_tv_download_matches_photo_and_refuses_nonlocal_streams(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.store
        path = store.media_dir / "synthetic.png"
        path.write_bytes(b"synthetic-photo-data")
        item, _ = store.add_item(source_key="synthetic:photo", title="Synthetic photo", kind="photo", file_path=str(path))
        public = client.get("/api/items/" + item["id"]).json()
        route = "/" + public["download_url"]
        response = client.get(route, headers={"Range": "bytes=0-8"})
        assert response.status_code == 206 and response.content == path.read_bytes()[:9]
        assert response.headers["Content-Type"].startswith("image/png")
        assert response.headers["ETag"] and response.headers["Content-Disposition"] == "attachment"
        assert client.head(route).headers["Content-Length"] == str(path.stat().st_size)
        remote, _ = store.add_item(source_key="synthetic:remote", title="Synthetic stream", source_url="https://example.com/movie.m3u8")
        assert client.get("/api/download/" + remote["id"]).status_code == 404
