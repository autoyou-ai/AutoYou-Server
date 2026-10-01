"""The paired phone downloads game HTML from the computer over its data channel."""

import base64
import asyncio
import gzip
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import server
from core_server import webrtc_engine
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType


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

    async def request(path="/api/v1/games", method="GET"):
        message = DataChannelMessage(
            header=MessageHeader("synthetic-message", MessageType.HTTP_REQUEST, 0.0,
                                 "synthetic-session", "synthetic-user"),
            payload={"request_id": "synthetic-request", "method": method,
                     "url": path, "headers": {}},
        )
        await webrtc._handle_http_request(message)
        return channel.send_message.call_args.args[0].payload

    assert json.loads((await request())["body"]) == {
        "games": [{"id": "neon-horizon", "title": "Neon Horizon"}],
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
