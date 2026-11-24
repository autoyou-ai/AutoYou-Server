"""A title stays playable across the content-folder move and server restarts.

These use the live layout: the catalog under ``<data>/tv_agent`` and new media in the shared
content folder, both inside a per-test ``AUTOYOU_TEST_ROOT``. Before agent content moved to the
shared folder, browser versions were written to ``<data>/tv_agent/media``.
"""
import asyncio
import json
import subprocess
import time

from fastapi.testclient import TestClient
import pytest

import autoyou_agents.tv_agent.website.backend.app as app_module
from autoyou_agents.tv_agent.website.backend import playback
from autoyou_agents.tv_agent.website.backend.app import create_app
from autoyou_agents.tv_agent.website.backend.jobs import INTERRUPTED, Jobs, executable, probe
from autoyou_agents.tv_agent.website.backend.store import Store


@pytest.fixture
def live_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.delenv("AUTOYOU_TV_DATA_DIR", raising=False)
    return tmp_path


def episode(store, name="Synthetic.Show.S04E03.720p.mkv", content=b"synthetic episode"):
    source = store.media_dir / name
    if content is not None:
        source.write_bytes(content)
    item, _ = store.add_item(source_key="synthetic:" + name, title="Synthetic Show - S04 E03", kind="episode", file_path=str(source))
    return item, source


def failed_attempt(store, item, source, message):
    job = store.add_job("transcode", {"item_id": item["id"], "source_version": playback.fingerprint(source)})
    store.update_job(job["id"], status="failed", progress=.25, message=message)
    return job


def hold_jobs(monkeypatch):
    """Keep enqueued jobs queued, so a test sees the decision without running an encoder."""
    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr(Jobs, "worker", idle)
    monkeypatch.setattr(app_module, "executable", lambda name: "synthetic-" + name)


def test_browser_version_made_before_the_content_move_still_plays(live_layout, monkeypatch):
    monkeypatch.setattr(app_module, "executable", lambda _: None)  # No encoder: nothing may be re-made.
    with TestClient(create_app()) as client:
        store = client.app.state.store
        legacy = store.root / "media"
        assert store.media_dir.resolve() != legacy.resolve()
        legacy.mkdir()
        assert store.root.is_relative_to(live_layout) and store.media_dir.is_relative_to(live_layout)
        item, source = episode(store)
        browser = legacy / (item["id"] + "-browser.mp4")
        browser.write_bytes(b"browser version made in October")
        store.update_item(item["id"], {"technical": {"extension": ".mkv", "probe_error": True, "browser_path": str(browser),
                                                     "browser_ready": True, "browser_source": playback.fingerprint(source)}})
        failed_attempt(store, item, source, INTERRUPTED)

        info = client.get(f"/api/items/{item['id']}/playback?compatible=true").json()
        assert info["ready"] is True and info["preparation"] is None and info["message"] == ""
        assert info["mime_type"] == "video/mp4"
        served = client.get("/" + info["url"], headers={"Range": "bytes=0-6"})
        assert served.status_code == 206 and served.content == b"browser"
        assert [job["status"] for job in store.jobs()] == ["failed"]


def test_browser_version_outside_the_catalogs_own_folders_is_not_served(live_layout, monkeypatch):
    monkeypatch.setattr(app_module, "executable", lambda _: None)
    with TestClient(create_app()) as client:
        store = client.app.state.store
        item, source = episode(store)
        elsewhere = live_layout / "elsewhere-browser.mp4"
        elsewhere.write_bytes(b"not the catalog's file")
        store.update_item(item["id"], {"technical": {"browser_path": str(elsewhere), "browser_ready": True,
                                                     "browser_source": playback.fingerprint(source)}})
        assert not playback.browser_ready(store, store.item(item["id"]))
        assert client.get(f"/api/items/{item['id']}/playback?compatible=true").json()["ready"] is False


def test_an_attempt_cut_off_by_a_restart_starts_again_instead_of_failing(live_layout, monkeypatch):
    hold_jobs(monkeypatch)
    with TestClient(create_app()) as client:
        store = client.app.state.store
        item, source = episode(store)
        interrupted = failed_attempt(store, item, source, INTERRUPTED)
        info = client.get(f"/api/items/{item['id']}/playback?compatible=true").json()
        assert info["ready"] is False and info["preparation"]["status"] == "queued"
        assert info["message"] == "Preparing a compatible video…"
        newest = store.jobs()[0]
        assert newest["id"] != interrupted["id"] and newest["kind"] == "transcode" and newest["status"] == "queued"
        assert newest["payload"] == interrupted["payload"]
        # Polling while it runs does not start a second one.
        client.get(f"/api/items/{item['id']}/playback?compatible=true")
        assert sum(job["status"] == "queued" for job in store.jobs()) == 1


def test_a_real_failure_is_tried_once_more_then_shown(live_layout, monkeypatch):
    hold_jobs(monkeypatch)
    with TestClient(create_app()) as client:
        store = client.app.state.store
        item, source = episode(store)
        route = f"/api/items/{item['id']}/playback?compatible=true"
        first = failed_attempt(store, item, source, "Import failed. Check source availability and retry.")
        retried = client.get(route).json()
        assert retried["preparation"]["status"] == "queued"
        second = store.jobs()[0]
        assert second["id"] != first["id"]
        store.update_job(second["id"], status="failed", message="Import failed. Check source availability and retry.")
        shown = client.get(route).json()
        assert shown["ready"] is False and shown["preparation"]["status"] == "failed"
        assert shown["message"] == "Video preparation failed. An editor can retry from Studio."
        assert len(store.jobs()) == 2
        # An editor's retry from Studio that finishes makes the title play; old failures no longer count.
        browser = store.media_dir / (item["id"] + "-browser.mp4")
        browser.write_bytes(b"browser")
        store.update_item(item["id"], {"technical": {"browser_path": str(browser), "browser_ready": True,
                                                     "browser_source": playback.fingerprint(source)}})
        assert client.get(route).json()["ready"] is True


def test_an_editors_cancel_is_kept(live_layout, monkeypatch):
    hold_jobs(monkeypatch)
    with TestClient(create_app()) as client:
        store = client.app.state.store
        item, source = episode(store)
        job = store.add_job("transcode", {"item_id": item["id"], "source_version": playback.fingerprint(source)})
        store.update_job(job["id"], status="cancelled", message="Cancelled")
        info = client.get(f"/api/items/{item['id']}/playback?compatible=true").json()
        assert info["preparation"]["status"] == "cancelled" and "cancelled" in info["message"]
        assert len(store.jobs()) == 1


def test_restart_resumes_local_preparation_and_clears_half_written_versions(live_layout, monkeypatch):
    async def check():
        store = Store()
        item, _ = episode(store)
        rows = {}
        for kind in ("transcode", "streaming", "download"):
            rows[kind] = store.add_job(kind, {"item_id": item["id"]})
            store.update_job(rows[kind]["id"], status="running", progress=.5, message="Working")
        abandoned = store.add_job("transcode", {"item_id": item["id"]})
        store.update_job(abandoned["id"], status="failed", message=INTERRUPTED)
        resumed_partial = store.media_dir / (rows["transcode"]["id"] + ".partial.mp4")
        resumed_partial.write_bytes(b"half")
        stale = [store.media_dir / (abandoned["id"] + ".partial.mp4"),
                 store.media_dir / (abandoned["id"] + ".partial.mp4.sealed"),
                 store.root / "media" / (abandoned["id"] + ".partial.mp4")]
        for path in stale:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"half")
        unrelated = store.media_dir / "holiday.partial.mp4"
        unrelated.write_bytes(b"someone's own file")

        jobs = Jobs(store)
        await jobs.start()
        try:
            assert store.job(rows["transcode"]["id"])["status"] == "queued"
            assert store.job(rows["streaming"]["id"])["status"] == "queued"
            download = store.job(rows["download"]["id"])
            assert download["status"] == "failed" and download["message"] == INTERRUPTED
            assert not any(path.exists() for path in stale)
            assert resumed_partial.exists() and unrelated.exists()
        finally:
            await jobs.stop()

    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr(Jobs, "worker", idle)
    asyncio.run(check())


def test_hdtv_episode_keeps_its_picture_and_converts_only_the_sound(live_layout, monkeypatch):
    ffmpeg, ffprobe = executable("ffmpeg"), executable("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("Requires optional host FFmpeg and FFprobe")
    commands = []
    original_run = Jobs.run_process

    async def recording(self, job, args, *, duration=0):
        commands.append(list(args))
        return await original_run(self, job, args, duration=duration)

    monkeypatch.setattr(Jobs, "run_process", recording)
    with TestClient(create_app()) as client:
        store = client.app.state.store
        item, source = episode(store, content=None)
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "ac3", str(source)],
                       check=True, capture_output=True, timeout=30)
        store.update_item(item["id"], {"technical": {"extension": ".mkv", "probe_error": True}})
        failed_attempt(store, item, source, INTERRUPTED)
        route = f"/api/items/{item['id']}/playback?compatible=true"
        assert client.get(route).json()["preparation"]["status"] in {"queued", "running"}
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            ready = client.get(route).json()
            if ready["ready"]:
                break
            time.sleep(.05)
        assert ready["ready"], ready
        command = commands[-1]
        assert command[command.index("-c:v") + 1] == "copy" and command[command.index("-c:a") + 1] == "aac"
        held = store.item(item["id"])
        assert held["technical"]["video_codec"] == "h264" and held["technical"]["audio_codec"] == "ac3"
        assert "probe_error" not in held["technical"]
        browser = held["technical"]["browser_path"]
        assert store.owns_media(browser)
        from shared import content_files
        with content_files.materialized(browser) as readable:
            streams = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(readable)]))["streams"]
        assert {s["codec_name"] for s in streams} == {"h264", "aac"}
        served = client.get("/" + ready["url"], headers={"Range": "bytes=0-31"})
        assert served.status_code == 206 and served.headers["Content-Type"].startswith("video/mp4")
