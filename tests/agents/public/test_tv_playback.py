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
