"""TV regression coverage uses only temporary media and synthetic credentials."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from autoyou_agents.tv_agent.website.backend.app import create_app
from autoyou_agents.tv_agent.website.backend.auth import Auth, verify_totp
from autoyou_agents.tv_agent.website.backend.jobs import Jobs
from autoyou_agents.tv_agent.website.backend import media
from autoyou_agents.tv_agent.website.backend.store import Store, data_root


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(Jobs, "enrich_item", _synthetic_metadata)
    app = create_app(tmp_path / "tv", otp_verifier=lambda code: code == "123456")
    with TestClient(app) as session:
        yield session


async def _synthetic_metadata(self, item_id, imdb_id=""):
    result = {"status": "matched", "imdb_id": imdb_id or "tt1234567", "plot": "Synthetic test film"}
    self.store.update_item(item_id, {"metadata": result})
    return result


def admin(client):
    result = client.post("/api/auth/login", json={"code": "123456"})
    assert result.status_code == 200
    return result.json()["token"]


def seed(client, name="Sample.2024.mp4", body=b"0123456789"):
    store = client.app.state.store
    path = store.media_dir / name
    path.write_bytes(body)
    item, _ = store.add_item(source_key="file:" + str(path), file_path=str(path), **media.parse_name(name))
    return item, path


def test_test_root_wins_over_live_tv_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "test"))
    monkeypatch.setenv("AUTOYOU_TV_DATA_DIR", str(tmp_path / "live"))
    store = Store()
    assert store.root == tmp_path / "test" / "tv_agent"
    assert not (tmp_path / "live").exists()


def test_open_viewing_never_initializes_server_auth(client, monkeypatch):
    from autoyou_agents.shared_tools import scheduler_mission_control
    def forbidden():
        raise AssertionError("Public viewing must not initialize operator authentication")
    monkeypatch.setattr(scheduler_mission_control, "_runtime_server", forbidden)
    assert client.get("/api/library").status_code == 200
    assert client.get("/api/auth/status").json()["role"] == "viewer"
    assert client.get("/api/settings").status_code == 401
    assert client.post("/api/folders", json={"name": "Movies"}).status_code == 403


def test_otp_unlocks_settings_and_wrong_code_is_rejected(client):
    assert client.post("/api/auth/login", json={"code": "999999"}).status_code == 401
    admin(client)
    assert client.get("/api/settings").status_code == 200
    status = client.get("/api/auth/status").json()
    assert status["role"] == "admin" and status["otp_unlocked"] is True
    assert client.post("/api/auth/login", json={"code": "123456"}).status_code == 401


def test_otp_attempts_are_rate_limited(client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"code": "999999"}).status_code == 401
    assert client.post("/api/auth/login", json={"code": "123456"}).status_code == 429


def test_editor_invitation_cannot_unlock_settings_and_revokes_sessions(client):
    admin(client)
    invitation = client.post("/api/settings/invitations", json={"label": "Synthetic editor", "role": "editor"}).json()
    client.post("/api/auth/logout")
    result = client.post("/api/auth/invite", json={"token": invitation["token"]})
    assert result.status_code == 200
    assert client.get("/api/auth/status").json()["role"] == "editor"
    assert client.get("/api/settings").status_code == 401
    assert client.post("/api/folders", json={"name": "Movies"}).status_code == 200
    # OTP admin token was logged out, so use a fresh synthetic OTP-verifier
    # session directly to exercise revocation without replaying an OTP.
    token = client.app.state.auth._new_session("admin", otp=True)
    assert client.delete("/api/settings/invitations/" + invitation["id"], headers={"Authorization": "Bearer " + token}).status_code == 200
    assert client.get("/api/auth/status").json()["role"] == "viewer"
    assert client.post("/api/auth/invite", json={"token": invitation["token"]}).status_code == 401


def test_admin_invitation_still_requires_otp_for_settings(client):
    admin(client)
    invite = client.post("/api/settings/invitations", json={"role": "admin"}).json()
    client.post("/api/auth/logout")
    assert client.post("/api/auth/invite", json={"token": invite["token"]}).status_code == 200
    assert client.get("/api/auth/status").json()["role"] == "admin"
    assert client.get("/api/settings").status_code == 401


def test_cross_origin_writes_are_blocked_even_with_editor_session(client):
    admin(client)
    assert client.post("/api/folders", json={"name": "Movies"}, headers={"Origin": "https://untrusted.example"}).status_code == 403


@pytest.mark.parametrize(("header", "status", "expected", "content_range"), [
    ("", 200, b"0123456789", None), ("bytes=2-5", 206, b"2345", "bytes 2-5/10"),
    ("bytes=7-", 206, b"789", "bytes 7-9/10"), ("bytes=-3", 206, b"789", "bytes 7-9/10"),
    ("bytes=0-9999", 206, b"0123456789", "bytes 0-9/10"),
    ("bytes=100-", 416, b"", "bytes */10"), ("bytes=7-2", 416, b"", "bytes */10"),
    ("bytes=0-2,5-8", 416, b"", "bytes */10"), ("bytes=-0", 416, b"", "bytes */10"),
])
def test_seekable_stream_returns_exact_bytes(client, header, status, expected, content_range):
    item, _ = seed(client)
    response = client.get("/api/stream/" + item["id"], headers={"Range": header} if header else {})
    assert response.status_code == status
    assert response.content == expected
    assert response.headers.get("Content-Range") == content_range
    if status != 416:
        assert int(response.headers["Content-Length"]) == len(expected)


def test_head_etag_and_if_range(client):
    item, _ = seed(client)
    url = "/api/stream/" + item["id"]
    response = client.head(url, headers={"Range": "bytes=3-5"})
    assert response.status_code == 206 and response.content == b""
    assert response.headers["Content-Length"] == "3"
    assert client.get(url, headers={"If-None-Match": response.headers["ETag"]}).status_code == 304
    assert client.get(url, headers={"Range": "bytes=3-5", "If-Range": '"stale"'}).content == b"0123456789"


def test_empty_media_and_public_payload_redaction(client):
    item, _ = seed(client, body=b"")
    assert client.get("/api/stream/" + item["id"]).headers["Content-Length"] == "0"
    assert client.get("/api/stream/" + item["id"], headers={"Range": "bytes=0-"}).status_code == 416
    data = client.get("/api/items/" + item["id"]).json()
    assert not {"file_path", "source_key", "source_url"} & data.keys()


def test_stream_symlink_cannot_escape_a_mounted_root(client, tmp_path):
    store = client.app.state.store
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"private")
    item, _ = store.add_item(source_key="forbidden", title="Forbidden", file_path=str(outside))
    assert client.get("/api/stream/" + item["id"]).status_code == 404


def test_filename_episode_and_year_parsing():
    assert media.parse_name("Example.Show.S02E03.1080p.mkv") == {
        "title": "Example Show - S02 E03", "series": "Example Show", "kind": "episode",
        "season": 2, "episode": 3, "year": None}
    assert media.parse_name("Example.Movie.2024.1080p.mp4")["title"] == "Example Movie"


def test_folder_scan_is_persistent_and_idempotent(tmp_path, monkeypatch):
    store = Store(tmp_path / "tv")
    source = tmp_path / "films"
    (source / "Season 1").mkdir(parents=True)
    (source / "Season 1" / "Example.S01E01.mp4").write_bytes(b"synthetic media")
    (source / "ignored.txt").write_text("not media")
    folder = store.add_folder("Shows", root_path=str(source))
    jobs = Jobs(store)
    monkeypatch.setattr(Jobs, "enrich_item", _synthetic_metadata)
    job = store.add_job("scan", {"folder_id": folder["id"]})
    first = asyncio.run(jobs.scan(job))
    second = asyncio.run(jobs.scan(job))
    assert first["added"] == 1 and second["added"] == 0
    assert Store(tmp_path / "tv").library()["count"] == 1
    assert len(store.folders(folder["id"])) == 1
    assert store.library()["items"][0]["metadata"]["status"] == "matched"


def test_library_search_folder_filters_and_next_episode(client):
    store = client.app.state.store
    folder = store.add_folder("Shows")
    items = [store.add_item(source_key=f"episode-{n}", title=f"Example {n}", kind="episode",
                            series="Example", season=1, episode=n, folder_id=folder["id"])[0] for n in (2, 1)]
    assert client.get("/api/library", params={"folder_id": folder["id"], "q": "Example"}).json()["count"] == 2
    assert client.get("/api/library", params={"q": "%"}).json()["count"] == 0
    detail = client.get("/api/items/" + items[1]["id"]).json()
    assert detail["next_episode"]["episode"] == 2


def test_upload_and_soft_remove_never_deletes_source(client):
    admin(client)
    response = client.post("/api/uploads", content=b"synthetic media", headers={"X-File-Name": "../../Sample.2024.mp4"})
    assert response.status_code == 200
    item = response.json()["item"]
    path = Path(client.app.state.store.item(item["id"])["file_path"])
    assert path.parent == client.app.state.store.media_dir
    assert client.delete("/api/items/" + item["id"]).status_code == 200
    assert path.exists()
    assert client.get("/api/stream/" + item["id"]).status_code == 404


def test_subtitles_are_served_as_webvtt(client):
    item, path = seed(client)
    path.with_suffix(".en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nSynthetic caption\n")
    detail = client.get("/api/items/" + item["id"]).json()
    track = detail["subtitles"][0]
    response = client.get(f"/api/subtitles/{item['id']}/{track['id']}")
    assert response.text.startswith("WEBVTT")
    assert "00:00:01.000" in response.text


def test_hls_manifest_rewrites_nested_playlists_keys_and_segments(tmp_path):
    store = Store(tmp_path / "tv")
    body = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\nsub/master.m3u8\nchunk.ts\n'
    result = media.rewrite_hls(store, "synthetic", body, "https://media.example/library/master.m3u8")
    assert "URI=\"synthetic?ticket=" in result
    lines = [line for line in result.splitlines() if line.startswith("synthetic?")]
    assert len(lines) == 2
    assert media.read_ticket(store, "synthetic", lines[0].split("ticket=")[1]) == "https://media.example/library/sub/master.m3u8"
    with pytest.raises(Exception):
        media.read_ticket(store, "different-item", lines[0].split("ticket=")[1])


@pytest.mark.parametrize("url", ["file:///private", "http://127.0.0.1/video.mp4", "http://[::1]/video", "http://localhost/x",
                                "http://169.254.169.254/latest/meta-data", "https://user:password@media.example/x"])
def test_remote_streams_reject_private_targets(url):
    with pytest.raises(ValueError):
        media.public_url(url)


def test_totp_rfc_vector():
    # RFC 6238 test secret, never an operator authenticator.
    assert verify_totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "287082", now=59)
    assert not verify_totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "000000", now=59)


def start_upload(client, size=10, **extra):
    response = client.post("/api/upload-sessions", json={"filename": "Sample.S01E02.mp4", "size": size, **extra})
    assert response.status_code == 200
    return response.json()["id"]


def test_chunked_upload_resumes_after_restart_and_retries_exact_chunks(client):
    from autoyou_agents.tv_agent.website.backend.uploads import Uploads
    admin(client)
    upload_id = start_upload(client)
    url = "/api/upload-sessions/" + upload_id
    assert client.put(url, params={"offset": 0}, content=b"01234").json()["received"] == 5
    assert client.post(url + "/complete").status_code == 409
    # Recreate the manager: received offsets and partial files survive restart.
    client.app.state.uploads = Uploads(client.app.state.store)
    assert client.get(url).json()["received"] == 5
    assert client.put(url, params={"offset": 0}, content=b"01234").json()["received"] == 5
    assert client.put(url, params={"offset": 0}, content=b"other").status_code == 409
    assert client.put(url, params={"offset": 6}, content=b"6789").status_code == 409
    assert client.put(url, params={"offset": 5}, content=b"56789").json()["received"] == 10
    item = client.post(url + "/complete").json()["item"]
    assert item["kind"] == "episode" and item["episode"] == 2
    assert client.post(url + "/complete").json()["item"]["id"] == item["id"]
    assert client.get("/api/stream/" + item["id"]).content == b"0123456789"
    assert client.get("/api/library").json()["count"] == 1
    assert client.delete(url).status_code == 409
    assert not list(client.app.state.uploads.directory.glob("*.part"))


def test_upload_chunks_are_bounded_and_owned_by_the_authenticated_session(client):
    from autoyou_agents.tv_agent.website.backend.uploads import CHUNK_BYTES
    assert client.post("/api/upload-sessions", json={"filename": "x.mp4", "size": 1}).status_code == 403
    admin(client)
    upload_id = start_upload(client, size=CHUNK_BYTES + 1)
    url = "/api/upload-sessions/" + upload_id
    assert client.put(url, content=b"x" * (CHUNK_BYTES + 1)).status_code == 413
    assert client.put(url, content=b"").status_code == 400
    assert client.get(url).json()["received"] == 0
    token = client.app.state.auth._new_session("editor")
    other = {"Authorization": "Bearer " + token}
    assert client.get(url, headers=other).status_code == 404
    assert client.put(url, content=b"x", headers=other).status_code == 404
    assert client.post(url + "/complete", headers=other).status_code == 404
    assert client.delete(url, headers=other).status_code == 404
    assert client.delete(url).status_code == 200
    assert client.get(url).status_code == 404
    assert not list(client.app.state.uploads.directory.iterdir())


def test_upload_recovers_uncommitted_disk_append(client):
    admin(client)
    upload_id = start_upload(client)
    url = "/api/upload-sessions/" + upload_id
    client.put(url, content=b"01234")
    partial = client.app.state.uploads.directory / (upload_id + ".part")
    with partial.open("ab") as handle:
        handle.write(b"uncommitted bytes")
    assert client.put(url, params={"offset": 5}, content=b"56789").status_code == 200
    item = client.post(url + "/complete").json()["item"]
    assert client.get("/api/stream/" + item["id"]).content == b"0123456789"


def test_upload_limits_and_expiry_cleanup_only_remove_partial_files(client):
    import time
    admin(client)
    assert client.post("/api/upload-sessions", json={"filename": "private.txt", "size": 1}).status_code == 415
    assert client.post("/api/upload-sessions", json={"filename": "x.mp4", "size": 21 * 1024 ** 3}).status_code == 422
    ids = [start_upload(client) for _ in range(4)]
    assert client.post("/api/upload-sessions", json={"filename": "x.mp4", "size": 1}).status_code == 429
    item, source = seed(client)
    with client.app.state.store.connect() as db:
        db.execute("UPDATE uploads SET expires=? WHERE id=?", (time.time() - 1, ids[0]))
    assert client.get("/api/upload-sessions/" + ids[0]).status_code == 404
    start_upload(client)
    assert not (client.app.state.uploads.directory / (ids[0] + ".part")).exists()
    assert source.exists() and client.get("/api/stream/" + item["id"]).status_code == 200


def test_library_saved_ids_and_folder_counts_do_not_depend_on_the_first_page(client):
    store = client.app.state.store
    folder = store.add_folder("Movies")
    store.add_folder("Series", folder["id"])
    items = [store.add_item(source_key=f"title-{i}", title=f"Title {i}", folder_id=folder["id"])[0] for i in range(105)]
    selected = client.get("/api/library", params={"ids": items[0]["id"] + "," + items[104]["id"]}).json()
    assert {item["id"] for item in selected["items"]} == {items[0]["id"], items[104]["id"]}
    store.update_item(items[0]["id"], {"status": "deleted"})
    counts = client.get("/api/folders").json()["folders"][0]
    assert counts["item_count"] == 104 and counts["folder_count"] == 1


def test_source_picker_is_otp_only_and_exposes_directories_not_files(client, tmp_path):
    child = tmp_path / "Synthetic media"
    child.mkdir()
    (tmp_path / "private.txt").write_text("synthetic private text")
    params = {"path": str(tmp_path)}
    assert client.get("/api/settings/browse", params=params).status_code == 401
    token = client.app.state.auth._new_session("admin")
    assert client.get("/api/settings/browse", params=params, headers={"Authorization": "Bearer " + token}).status_code == 401
    admin(client)
    listing = client.get("/api/settings/browse", params=params).json()
    assert child.name in {row["name"] for row in listing["directories"]}
    assert "private.txt" not in str(listing)
    assert client.get("/api/settings/browse", params={"path": str(tmp_path / "private.txt")}).status_code == 400


def test_artwork_obeys_catalog_removal(client):
    item, _ = seed(client)
    store = client.app.state.store
    path = store.artwork_dir / (item["id"] + ".jpg")
    path.write_bytes(b"synthetic jpeg")
    store.update_item(item["id"], {"technical": {"artwork": True}})
    detail = client.get("/api/items/" + item["id"]).json()
    assert detail["artwork_url"] == "api/artwork/" + item["id"]
    assert client.get("/" + detail["artwork_url"]).content == b"synthetic jpeg"
    store.update_item(item["id"], {"status": "deleted"})
    assert client.get("/" + detail["artwork_url"]).status_code == 404


def test_automatic_imdb_matching_uses_episode_fields_and_a_persistent_cache(tmp_path, monkeypatch):
    import json
    from contextlib import asynccontextmanager
    from urllib.parse import parse_qs, urlsplit
    from autoyou_agents.tv_agent.website.backend import jobs
    store = Store(tmp_path / "tv")
    monkeypatch.setattr(Store, "key", lambda self: "synthetic-key")
    item, _ = store.add_item(source_key="episode", title="Synthetic S01E02", kind="episode", series="Synthetic", year=2024, season=1, episode=2)
    requests, closed = [], []
    @asynccontextmanager
    async def session():
        yield object()
    class Body:
        async def read(self, limit):
            return json.dumps({"Response": "True", "imdbID": "tt1234567", "Title": "Synthetic", "imdbRating": "N/A", "Poster": "N/A", "Genre": "Drama"}).encode()
    class Upstream:
        status = 200
        content = Body()
        def close(self):
            closed.append(True)
    async def fetch(_session, url, **kwargs):
        requests.append(parse_qs(urlsplit(url).query))
        return Upstream()
    monkeypatch.setattr(jobs, "http_session", session)
    monkeypatch.setattr(jobs, "fetch_response", fetch)
    result = asyncio.run(Jobs(store).enrich_item(item["id"]))
    cached = asyncio.run(Jobs(Store(store.root)).enrich_item(item["id"]))
    assert result == cached and len(requests) == 1 and len(closed) == 1
    assert requests[0]["Season"] == ["1"] and requests[0]["Episode"] == ["2"]
    assert requests[0]["t"] == ["Synthetic"] and requests[0]["y"] == ["2024"]
    assert result["imdb_rating"] == "" and result["poster"] == ""
    assert store.item(item["id"])["metadata"]["imdb_id"] == "tt1234567"


def test_metadata_credentials_are_library_scoped_and_never_saved_in_plaintext(tmp_path, monkeypatch):
    from shared import keystore
    saved = {}
    monkeypatch.delenv("AUTOYOU_TV_OMDB_API_KEY", raising=False)
    monkeypatch.setattr(keystore, "keyring_available", lambda: True)
    monkeypatch.setattr(keystore, "write_keyring_password", lambda service, name, value: saved.update({(service, name): value}))
    monkeypatch.setattr(keystore, "read_keyring_password_strict", lambda service, name: saved.get((service, name)))
    first, second = Store(tmp_path / "one"), Store(tmp_path / "two")
    first.set_key("synthetic-metadata-key")
    assert first.key() == "synthetic-metadata-key" and second.key() == ""
    assert first.key_service() != second.key_service()
    assert not (first.root / "metadata-key.enc").exists()
    assert b"synthetic-metadata-key" not in first.db_path.read_bytes()
    monkeypatch.setattr(keystore, "keyring_available", lambda: False)
    with pytest.raises(ValueError, match="Secure credential storage"):
        second.set_key("synthetic-other-key")


def test_download_retains_provider_title_and_requested_year_for_imdb(tmp_path, monkeypatch):
    import json
    from autoyou_agents.tv_agent.website.backend import jobs as module
    store = Store(tmp_path / "tv")
    jobs = Jobs(store)
    monkeypatch.setattr(module, "public_url", lambda url: url)
    monkeypatch.setattr(module, "executable", lambda name: "synthetic-downloader" if name == "yt-dlp" else None)
    monkeypatch.setattr(Jobs, "enrich_item", _synthetic_metadata)
    job = store.add_job("download", {"url": "https://archive.org/download/synthetic/media.mp4", "kind": "movie", "year": 2024})
    commands = []
    async def process(_job, args, **kwargs):
        commands.append(args)
        directory = store.media_dir / job["id"]
        (directory / "media.mp4").write_bytes(b"synthetic media")
        (directory / "media.info.json").write_text(json.dumps({"title": "Synthetic Film", "release_year": 2023}))
    monkeypatch.setattr(jobs, "run_process", process)
    result = asyncio.run(jobs.download(job))
    item = store.item(result["item_id"])
    assert item["title"] == "Synthetic Film" and item["year"] == 2024
    assert item["metadata"]["status"] == "matched"
    assert {"--ignore-config", "--no-playlist", "--no-plugin-dirs", "--write-info-json"} <= set(commands[0])
    assert "b[ext=mp4]/b" in commands[0]
