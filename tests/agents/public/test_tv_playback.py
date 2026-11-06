"""Synthetic HLS files, URLs and optional real FFmpeg encoding."""
import asyncio
from pathlib import Path
import subprocess
import json

from fastapi.testclient import TestClient
import pytest

from autoyou_agents.tv_agent.website.backend.app import create_app
from autoyou_agents.tv_agent.website.backend import playback
from autoyou_agents.tv_agent.website.backend.jobs import executable, Jobs


def seed(client):
    store = client.app.state.store
    store.set_setting("adaptive_streaming", False)
    path = store.media_dir / "Synthetic.mp4"; path.write_bytes(b"synthetic original")
    item, _ = store.add_item(source_key="synthetic", title="Synthetic film", file_path=str(path))
    version = "b" * 24
    root = playback.rendition_root(store, item["id"], version)
    (root / "360p").mkdir(parents=True)
    (root / "master.m3u8").write_text("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=700000\n360p/index.m3u8\n")
    (root / "360p/index.m3u8").write_text("#EXTM3U\n#EXTINF:2,\nsegment-00000.ts\n#EXT-X-ENDLIST\n")
    (root / "360p/segment-00000.ts").write_bytes(bytes(range(256)) * 40)
    (root / "published.json").write_text(json.dumps({"source": playback.fingerprint(path)}))
    store.update_item(item["id"], {"technical": {"hls_version": version, "hls_source": playback.fingerprint(path)}})
    return item, path, root, version


def test_published_playback_is_streamable_bounded_and_revoked_with_source(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        item, source, root, version = seed(client)
        descriptor = client.get(f"/api/items/{item['id']}/playback").json()
        assert descriptor["version"] == 1 and descriptor["optimized"] and descriptor["is_hls"]
        assert str(tmp_path) not in str(descriptor)
        master = client.get("/" + descriptor["url"])
        assert master.status_code == 200 and master.headers["Content-Type"].startswith("application/vnd.apple.mpegurl")
        assert "immutable" in master.headers["Cache-Control"]
        resource = f"/api/playback/{item['id']}/{version}/360p/segment-00000.ts"
        response = client.get(resource, headers={"Range": "bytes=12-400"})
        assert response.status_code == 206 and response.content == (bytes(range(256)) * 40)[12:401]
        head = client.head(resource, headers={"Range": "bytes=12-400"})
        assert head.content == b"" and head.headers["Content-Length"] == "389"
        assert client.options(resource).headers["Access-Control-Allow-Methods"] == "GET, HEAD, OPTIONS"
        assert client.get(resource.replace(version, "c" * 24)).status_code == 404
        # A newly published rendition must not interrupt a viewer of the old
        # immutable version while the source itself remains authorized.
        client.app.state.store.update_item(item["id"], {"technical": {"hls_version": "c" * 24, "hls_source": playback.fingerprint(source)}})
        assert client.get(resource).status_code == 200
        assert client.get(f"/api/playback/{item['id']}/{version}/360p/%2e%2e%2fmaster.m3u8").status_code == 404
        source.write_bytes(b"changed")
        assert client.get(resource).status_code == 404
        descriptor = client.get(f"/api/items/{item['id']}/playback").json()
        assert not descriptor["optimized"] and descriptor["url"] == f"api/stream/{item['id']}"


def test_optimize_requires_editor_and_profiles_never_upscale(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        item, _, _, _ = seed(client)
        assert client.post(f"/api/items/{item['id']}/streaming").status_code == 403
    assert playback.profiles(240) == [(240, 550000)]
    assert [size for size, _ in playback.profiles(1080)] == [360, 480, 720]


@pytest.mark.skipif(not executable("ffmpeg") or not executable("ffprobe"), reason="Optional host FFmpeg tools are unavailable")
@pytest.mark.asyncio
async def test_real_hls_encoding_publishes_only_complete_versions(tmp_path):
    from autoyou_agents.tv_agent.website.backend.store import Store
    store = Store(tmp_path); source = store.media_dir / "Synthetic.mp4"
    subprocess.run([executable("ffmpeg"), "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "5", "-c:v", "libx264", "-c:a", "aac", str(source)], check=True, timeout=30)
    item, _ = store.add_item(source_key="synthetic", title="Synthetic film", file_path=str(source))
    jobs = Jobs(store); job = store.add_job("streaming", {"item_id": item["id"]})
    await jobs.streaming(job)
    item = store.item(item["id"]); assert playback.ready(store, item)
    root = playback.rendition_root(store, item["id"], item["technical"]["hls_version"])
    playlist = (root / "360p/index.m3u8").read_text()
    assert "#EXT-X-ENDLIST" in playlist and playlist.count("#EXTINF:") == 3
    assert len(list((root / "360p").glob("*.ts"))) == 3
    assert source.is_file() and not list(root.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_computer_cast_receiver_reads_one_title_without_mobile_or_browser_credentials(tmp_path, monkeypatch):
    import socket
    from urllib.parse import urljoin
    import aiohttp
    import uvicorn
    from shared import tv_cast

    # A real TV Agent and independent HTTP receiver; all sockets stay on loopback in CI.
    with TestClient(create_app(tmp_path)) as client:
        item, file, root, version = seed(client)
    host = "127.0.0.1"
    monkeypatch.setattr(tv_cast, "lan_ipv4", lambda addr: addr == host)
    sock = socket.socket()
    sock.bind((host, 0))
    backend = uvicorn.Server(uvicorn.Config(create_app(tmp_path), log_level="error", access_log=False))
    runner = asyncio.create_task(backend.serve(sockets=[sock]))
    leases = tv_cast.TVCastLeases()
    sid, rid = "synthetic-session", "00000000-0000-4000-8000-000000000001"
    ident = item["id"]
    try:
        for _ in range(1000):
            if backend.started: break
            if runner.done(): await runner
            await asyncio.sleep(.01)
        assert backend.started
        upstream = f"http://{host}:{sock.getsockname()[1]}/api"
        assert not await leases.offer(sid, rid, f"stream/{ident}/../settings", host, upstream)
        async with aiohttp.ClientSession() as receiver:
            url = (await leases.offer(sid, rid, "stream/" + ident, host, upstream))["url"]
            async with receiver.head(url) as resp:
                assert resp.status == 200 and int(resp.headers["Content-Length"]) == file.stat().st_size
                assert resp.headers["X-AutoYou-TV-Title"] == ident
            async with receiver.get(url, headers={"Range": "bytes=-5"}) as resp:
                assert resp.status == 206 and await resp.read() == file.read_bytes()[-5:]
            for target in (url[:-24] + "c" * 24, url.replace("/tv/", "/other/"), url.replace("/stream/", "/settings/")):
                async with receiver.get(target) as resp:
                    assert resp.status == 404
            async with receiver.post(url) as resp:
                assert resp.status == 404
            new_id = rid[:-1] + "2"
            master = (await leases.offer(sid, new_id, f"playback/{ident}/{version}/master.m3u8", host, upstream))["url"]
            await leases.release(sid, rid)  # A late dismissal from the previous player cannot revoke this one.
            async with receiver.get(master) as resp:
                assert resp.status == 200
                variant = urljoin(master, (await resp.text()).splitlines()[-1])
            async with receiver.get(variant) as resp:
                assert resp.status == 200
                segment = urljoin(variant, next(line for line in (await resp.text()).splitlines() if line.endswith(".ts")))
            async with receiver.get(segment) as resp:
                assert resp.status == 200 and await resp.read() == (root / "360p/segment-00000.ts").read_bytes()
            await leases.release(sid, new_id)
            with pytest.raises(aiohttp.ClientConnectionError):
                await receiver.get(master)
    finally:
        await leases.release(sid)
        backend.should_exit = True
        await runner
        sock.close()


def test_photo_previews_and_receiver_images_are_bounded_and_keep_originals(tmp_path):
    from io import BytesIO
    from PIL import Image
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.store
        path = store.media_dir / "Synthetic-photo.jpg"
        Image.new("RGB", (3600, 2400), "purple").save(path)
        original = path.read_bytes()
        item, _ = store.add_item(source_key="synthetic-photo", title="Synthetic photo", kind="photo", file_path=str(path))
        public = client.get(f"/api/items/{item['id']}").json()
        assert public["artwork_url"] == f"api/artwork/{item['id']}"
        preview = client.get("/" + public["artwork_url"])
        assert preview.status_code == 200
        assert max(Image.open(BytesIO(preview.content)).size) <= 640
        receiver = client.get("/" + public["stream_url"], headers={"X-AutoYou-TV-Presentation": "1", "Range": "bytes=0-31"})
        assert receiver.status_code == 206 and len(receiver.content) == 32
        full_receiver = client.get("/" + public["stream_url"], headers={"X-AutoYou-TV-Presentation": "1"})
        assert max(Image.open(BytesIO(full_receiver.content)).size) <= 1280
        assert client.get("/" + public["stream_url"]).content == original == path.read_bytes()
        assert client.get("/" + public["download_url"]).content == original
        # Source edits invalidate the on-disk derivative, and deleting a title denies a cached preview.
        Image.new("RGB", (1800, 3000), "green").save(path)
        changed = client.get("/" + public["artwork_url"])
        assert changed.content != preview.content
        assert Image.open(BytesIO(changed.content)).height == 640
        store.update_item(item["id"], {"status": "deleted"})
        assert client.get("/" + public["artwork_url"]).status_code == 404


def test_photo_and_ebook_native_descriptors_remain_title_scoped(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.store
        for extension, kind, mime in [("gif", "photo", "image/gif"), ("pdf", "document", "application/pdf"), ("epub", "document", "application/epub+zip")]:
            path = store.media_dir / ("Synthetic." + extension); path.write_bytes(b"synthetic descriptor fixture")
            item, _ = store.add_item(source_key="synthetic-" + extension, title="Synthetic", kind=kind, file_path=str(path))
            descriptor = client.get(f"/api/items/{item['id']}/playback?compatible=true").json()
            assert descriptor["ready"] and descriptor["mime_type"] == mime
            assert descriptor["url"] == f"api/stream/{item['id']}"
            assert str(tmp_path) not in str(descriptor)
