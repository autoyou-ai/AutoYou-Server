"""Phone media publishing and real compatibility conversion use isolated media."""
import base64
import asyncio
import subprocess
import time

from fastapi.testclient import TestClient
import pytest

from autoyou_agents.tv_agent.website.backend.app import create_app
from autoyou_agents.tv_agent.website.backend.jobs import executable
from autoyou_agents.tv_agent.website.backend import playback
from autoyou_agents.tv_agent.website.backend.jobs import Jobs
from autoyou_agents.tv_agent.website.backend.store import Store


@pytest.mark.parametrize("suffix,mime,kind", [("pdf", "application/pdf", "document"), ("epub", "application/epub+zip", "document"), ("wav", "audio/wav", "audio")])
@pytest.mark.parametrize("resumable", [False, True])
def test_documents_and_audio_round_trip_without_video_preparation(tmp_path, monkeypatch, suffix, mime, kind, resumable):
    import io
    import wave
    import zipfile
    import autoyou_agents.tv_agent.website.backend.app as app_module
    import autoyou_agents.tv_agent.website.backend.jobs as jobs_module

    def no_video_probe(*_):
        raise AssertionError("Documents and audio must not be prepared as videos")

    monkeypatch.setattr(app_module, "probe", no_video_probe)
    monkeypatch.setattr(jobs_module, "probe", no_video_probe)
    content = b"%PDF-1.4\n% synthetic transport fixture\n%%EOF"
    if suffix == "epub":
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("chapter.xhtml", "<html><head></head><body>Synthetic book</body></html>")
        content = output.getvalue()
    elif suffix == "wav":
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b"\0\0" * 80)
        content = output.getvalue()
    with TestClient(create_app(tmp_path)) as client:
        auth = {"Authorization": "Bearer " + client.app.state.auth._new_session("editor")}
        filename = "synthetic.S01E02." + suffix
        if resumable:
            response = client.post("/api/upload-sessions", headers=auth, json={"filename": filename, "size": len(content), "kind": "video"})
            assert response.status_code == 200, response.text
            route = "/api/upload-sessions/" + response.json()["id"]
            assert client.put(route + "?offset=0", headers=auth, content=content).status_code == 200
            uploaded = client.post(route + "/complete", headers=auth)
        else:
            uploaded = client.post("/api/uploads?kind=video", headers={**auth, "X-File-Name": filename}, content=content)
        assert uploaded.status_code == 200, uploaded.text
        item = uploaded.json()["item"]
        assert item["kind"] == kind
        assert client.get("/api/library?kind=" + kind).json()["items"][0]["id"] == item["id"]
        playback_info = client.get(f"/api/items/{item['id']}/playback?compatible=true").json()
        assert playback_info["ready"] and playback_info["mime_type"] == mime and playback_info["preparation"] is None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(j["status"] in {"queued", "running"} for j in client.app.state.store.jobs()):
            time.sleep(.01)
        jobs = client.app.state.store.jobs()
        assert jobs and all(j["kind"] == "prepare" and j["status"] == "completed" for j in jobs), jobs
        for route in ["stream", "download"]:
            served = client.get(f"/api/{route}/{item['id']}", headers={"Range": "bytes=0-15"})
            assert served.status_code == 206 and served.content == content[:16]
            assert served.headers["Content-Type"].startswith(mime)
        assert client.get(f"/api/download/{item['id']}").content == content
        assert client.post(f"/api/items/{item['id']}/transcode", headers=auth).status_code == 400
        assert client.post(f"/api/items/{item['id']}/streaming", headers=auth).status_code == 400


def test_photo_upload_is_resumable_owned_and_readable(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        auth = client.app.state.auth
        headers = {"Authorization": "Bearer " + auth._new_session("editor")}
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
        content = png + b"\0" * (2 * 1024 * 1024)
        payload = {"filename": "synthetic-photo.png", "size": len(content), "kind": "photo", "title": "Synthetic photo"}
        assert client.post("/api/upload-sessions", json=payload).status_code == 403
        session = client.post("/api/upload-sessions", json=payload, headers=headers).json()
        path = "/api/upload-sessions/" + session["id"]
        chunk = content[:session["chunk_bytes"]]
        first = client.put(path + "?offset=0", content=chunk, headers=headers)
        assert first.status_code == 200 and first.json()["received"] == len(chunk)
        assert client.put(path + "?offset=0", content=chunk, headers=headers).json()["received"] == len(chunk)
        other = {"Authorization": "Bearer " + auth._new_session("editor")}
        assert client.get(path, headers=other).status_code == 404
        offset = client.get(path, headers=headers).json()["received"]
        assert client.put(path + f"?offset={offset}", content=content[offset:], headers=headers).status_code == 200
        item = client.post(path + "/complete", headers=headers).json()["item"]
        assert client.post(path + "/complete", headers=headers).json()["item"]["id"] == item["id"]
        assert item["kind"] == "photo" and item["artwork_url"].endswith(item["id"])
        assert client.get("/api/library?kind=photo").json()["items"][0]["id"] == item["id"]
        stream = "/api/stream/" + item["id"]
        response = client.get(stream, headers={"Range": "bytes=0-15"})
        assert response.status_code == 206 and response.content == content[:16]
        assert response.headers["Content-Type"].startswith("image/png")
        assert client.head(stream).headers["Content-Length"] == str(len(content))
        bad = {**payload, "filename": "synthetic.svg"}
        assert client.post("/api/upload-sessions", json=bad, headers=headers).status_code == 415


@pytest.mark.parametrize("suffix,codec", [("mkv", "libx264"), ("mkv", "mpeg4"), ("mp4", "mpeg4")])
def test_real_incompatible_video_prepares_h264_aac_without_changing_original(tmp_path, suffix, codec):
    ffmpeg, ffprobe = executable("ffmpeg"), executable("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("Requires optional host FFmpeg and FFprobe")
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.store
        source = store.media_dir / ("synthetic." + suffix)
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1", "-c:v", codec,
                        "-c:a", "aac", str(source)], check=True, capture_output=True, timeout=30)
        original = source.read_bytes()
        item, _ = store.add_item(source_key="synthetic", title="Synthetic video", file_path=str(source))
        # Codec metadata is normally filled during upload/scan preparation.
        from autoyou_agents.tv_agent.website.backend.jobs import probe
        if suffix != "mp4":
            store.update_item(item["id"], {"technical": probe(source)})
        route = f"/api/items/{item['id']}/playback?compatible=true"
        first = client.get(route).json()
        assert first["ready"] is False and first["preparation"]["status"] in {"queued", "running"}
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            ready = client.get(route).json()
            if ready["ready"]:
                break
            time.sleep(.05)
        assert ready["ready"], ready
        assert ready["mime_type"] == "video/mp4" and source.read_bytes() == original
        held = store.item(item["id"])
        assert playback.browser_ready(store, held)
        served = client.get("/" + ready["url"], headers={"Range": "bytes=0-31"})
        assert served.status_code == 206 and served.headers["Content-Type"].startswith("video/mp4")
        import json
        streams = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-of", "json", held["technical"]["browser_path"]]))["streams"]
        assert {s["codec_name"] for s in streams} == {"h264", "aac"}
        source.write_bytes(original + b"changed")
        assert not playback.browser_ready(store, held)
        if suffix == "mkv":
            assert client.get(f"/api/items/{item['id']}/playback").json()["mime_type"] == "video/x-matroska"


def test_missing_encoder_reports_actionable_failure_not_playable_mkv(tmp_path, monkeypatch):
    import autoyou_agents.tv_agent.website.backend.app as module
    monkeypatch.setattr(module, "executable", lambda _: None)
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.store
        source = store.media_dir / "synthetic.mkv"
        source.write_bytes(b"synthetic")
        item, _ = store.add_item(source_key="synthetic", title="Synthetic", file_path=str(source))
        result = client.get(f"/api/items/{item['id']}/playback?compatible=true").json()
        assert not result["ready"] and result["preparation"]["status"] == "failed"
        assert "FFmpeg" in result["message"] and not store.jobs()


def test_encoder_exit_race_does_not_swallow_worker_cancellation(tmp_path, monkeypatch):
    async def check():
        started = asyncio.Event()

        class Output:
            def __aiter__(self): return self
            async def __anext__(self):
                started.set()
                await asyncio.Event().wait()
                raise StopAsyncIteration

        class Process:
            returncode = None
            stdout = Output()
            calls = 0
            def terminate(self):
                self.calls += 1
                if self.calls > 1: raise ProcessLookupError
            async def wait(self): return 0

        async def subprocess_stub(*args, **kwargs): return Process()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", subprocess_stub)
        jobs = Jobs(Store(tmp_path))
        async def conversion(job): await jobs.run_process(job, ["synthetic-encoder"])
        monkeypatch.setattr(jobs, "transcode", conversion)
        await jobs.enqueue("transcode", {"item_id": "synthetic"})
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(jobs.stop(), 1)
        assert not jobs.workers and not jobs.processes
    asyncio.run(check())
