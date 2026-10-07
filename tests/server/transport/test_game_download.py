"""The paired phone downloads game HTML from the computer over its data channel."""

import base64
import asyncio
import gzip
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from io import BytesIO
import threading

import pytest

import server
from core_server import webrtc_engine
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType


def test_game_loader_is_bounded_even_if_a_selected_file_grows_after_stat(tmp_path, monkeypatch):
    asset = tmp_path / "synthetic-growing.html"
    asset.write_text("<head></head>")
    metadata = asset.stat()
    class Source(BytesIO):
        def fileno(self): return 79
        def read(self, maximum):
            assert maximum == webrtc_engine._MOBILE_GAME_MAX_BYTES-len(webrtc_engine._MOBILE_GAME_CSP)+1
            return super().read(maximum)
    with monkeypatch.context() as scope:
        scope.setattr(webrtc_engine.os, "open", lambda *args: 79)
        scope.setattr(webrtc_engine.os, "fstat", lambda *args: metadata)
        scope.setattr(webrtc_engine.os, "fdopen", lambda *args: Source(b"x"*(512*1024+1)))
        with pytest.raises(ValueError, match="limit"):
            webrtc_engine._load_mobile_game_html(asset)


def test_game_loader_rejects_opened_file_identity_changes(tmp_path, monkeypatch):
    asset = tmp_path / "synthetic-selected.html"
    asset.write_text("<head></head>")
    metadata = asset.stat()
    with monkeypatch.context() as scope:
        scope.setattr(webrtc_engine.os, "fstat", lambda *args: SimpleNamespace(st_mode=metadata.st_mode,st_size=metadata.st_size,
            st_dev=metadata.st_dev,st_ino=metadata.st_ino+1))
        with pytest.raises(ValueError, match="changed"):
            webrtc_engine._load_mobile_game_html(asset)


@pytest.mark.asyncio
async def test_cancelled_game_catalog_joins_the_selected_file_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(server, "APP_ROOT", tmp_path)
    asset = tmp_path / "assets/game/synthetic-game.html"
    asset.parent.mkdir(parents=True); asset.write_text("<head></head>")
    config = server._default_config(); config["autoyou_page"]["remote_access_role"] = "admin"
    monkeypatch.setattr(server.STATE, "config", config, raising=False)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    capacity = asyncio.Semaphore(1)
    monkeypatch.setattr(webrtc_engine, "_MOBILE_GAME_READERS", capacity)
    def load(path):
        assert path == asset
        entered.set()
        try:
            assert release.wait(5)
            return b"<head></head>"
        finally:
            exited.set()
    monkeypatch.setattr(webrtc_engine, "_load_mobile_game_html", load)
    manager = server.WebRTCManager(); channel = SimpleNamespace(send_message=AsyncMock(return_value=True))
    manager.datachannel_managers["synthetic-session"] = channel
    message = DataChannelMessage(MessageHeader("synthetic-request",MessageType.HTTP_REQUEST,0.0,"synthetic-session","synthetic-user"),
        {"request_id":"synthetic-request","method":"GET","url":"/api/v1/games","headers":{}})
    task = asyncio.create_task(manager._handle_http_request(message))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel(); await asyncio.sleep(0.01)
        assert not task.done() and not exited.is_set() and capacity.locked()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
    assert exited.is_set() and channel.send_message.await_count == 0 and not capacity.locked()


@pytest.mark.asyncio
async def test_mobile_game_download_uses_server_asset_and_stays_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    asset = tmp_path / "assets" / "game" / "neon-horizon.html"
    asset.parent.mkdir(parents=True)
    expected_html = "<html><head></head><body>" + "synthetic game " * 150 + "</body></html>"
    asset.write_text(expected_html, encoding="utf-8")
    monkeypatch.setattr(server, "APP_ROOT", tmp_path)
    config = server._default_config()
    config["autoyou_page"]["remote_access_role"] = "admin"
    monkeypatch.setattr(server.STATE, "config", config, raising=False)

    webrtc = server.WebRTCManager()
    channel = SimpleNamespace(send_message=AsyncMock(return_value=True))
    webrtc.datachannel_managers["synthetic-session"] = channel

    async def request(path="/api/v1/games", method="GET", expected_digest=None):
        message = DataChannelMessage(
            header=MessageHeader("synthetic-message", MessageType.HTTP_REQUEST, 0.0,
                                 "synthetic-session", "synthetic-user"),
            payload={"request_id": "synthetic-request", "method": method,
                      "url": path, "headers": {} if expected_digest is None else {"X-AutoYou-Game-SHA256": expected_digest}},
        )
        await webrtc._handle_http_request(message)
        return channel.send_message.call_args.args[0].payload

    assert json.loads((await request())["body"]) == {
        "games": [{"id": "neon-horizon", "title": "Neon Horizon",
                   "bytes": len(webrtc_engine._load_mobile_game_html(asset)),
                   "sha256": hashlib.sha256(webrtc_engine._load_mobile_game_html(asset)).hexdigest()}],
    }
    second = asset.parent / "synthetic-runner.html"
    second.write_text("<html><head></head><body>another game</body></html>", encoding="utf-8")
    (asset.parent / "headless.html").write_text("<html>unplayable</html>", encoding="utf-8")
    assert [game["id"] for game in json.loads((await request())["body"])["games"]] == [
        "neon-horizon", "synthetic-runner",
    ]
    second.unlink()
    published = tmp_path / "runtime" / "AutoYou" / "mobile_games" / "synthetic-runner.html"
    published.parent.mkdir(parents=True)
    published.write_text("<html><head></head><body>published game</body></html>", encoding="utf-8")
    assert [game["id"] for game in json.loads((await request())["body"])["games"]] == [
        "neon-horizon", "synthetic-runner",
    ]
    downloaded = await request("/api/v1/games/synthetic-runner")
    assert downloaded["status_code"] == 200
    assert "published game" in downloaded["body"]
    published.unlink()

    response = await request("/api/v1/games/neon-horizon")
    body = response["body"]
    assert response["compressed"] is True
    if response["compressed"]:
        body = gzip.decompress(base64.b64decode(body)).decode("utf-8")
    assert response["status_code"] == 200
    assert response["headers"]["Content-Type"].startswith("text/html")
    expected_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert response["headers"]["X-AutoYou-Game-SHA256"] == expected_digest
    assert (await request("/api/v1/games/neon-horizon", expected_digest=expected_digest))["status_code"] == 200
    changed = await request("/api/v1/games/neon-horizon", expected_digest="0" * 64)
    assert changed["status_code"] == 409 and changed["compressed"] is False
    assert "Game changed" in changed["body"] and "<html>" not in changed["body"]
    assert "connect-src 'none'" in body
    meta_start = body.index("<meta")
    meta_end = body.index(">", meta_start) + 1
    assert body[:meta_start] + body[meta_end:] == expected_html

    assert (await request(method="POST"))["status_code"] == 405
    assert (await request("/api/v1/games/../secret"))["status_code"] == 404
    asset.write_bytes(b"x" * (512 * 1024 + 1))
    assert (await request("/api/v1/games/neon-horizon"))["status_code"] == 404
    assert json.loads((await request())["body"]) == {"games": []}


@pytest.mark.asyncio
async def test_hosted_game_starts_only_for_editor_and_reports_launch_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    config = server._default_config()
    config["video_call"]["remote_desktop"].update(control_enabled=True, game_enabled=True)
    config["autoyou_page"]["remote_access_role"] = "editor"
    monkeypatch.setattr(server.STATE, "config", config, raising=False)
    webrtc = server.WebRTCManager()
    channel = SimpleNamespace(send_message=AsyncMock(return_value=True))
    webrtc.datachannel_managers["synthetic-session"] = channel
    opened = []
    monkeypatch.setattr(webrtc_engine.webbrowser, "open_new", lambda url: opened.append(url) or False)

    async def start():
        message = DataChannelMessage(
            header=MessageHeader("synthetic-message", MessageType.HTTP_REQUEST, 0.0,
                                 "synthetic-session", "synthetic-user"),
            payload={"request_id": "synthetic-request", "method": "POST",
                     "url": "/api/v1/games/hosted/start", "headers": {}},
        )
        await webrtc._handle_http_request(message)
        return channel.send_message.call_args.args[0].payload

    assert (await start())["status_code"] == 503
    assert opened == [f"http://127.0.0.1:{server.ADMIN_WEB_SERVICE_PORT}/api/webrtc/hosted-game/play"]
    loop = asyncio.get_running_loop()
    def open_game(url):
        opened.append(url)
        loop.call_soon_threadsafe(webrtc.game_input_hub.attach)
        return True
    monkeypatch.setattr(webrtc_engine.webbrowser, "open_new", open_game)
    try:
        assert (await start())["status_code"] == 200
        assert json.loads((await start())["body"]) == {"success": True}
        assert len(opened) == 2
    finally:
        webrtc.game_input_hub.detach(webrtc.game_input_hub._queue)
    config["autoyou_page"]["remote_access_role"] = "viewer"
    channel.send_message.reset_mock()
    await start()
    assert channel.send_message.call_args.args[0].payload["status_code"] == 403
    assert len(opened) == 2
