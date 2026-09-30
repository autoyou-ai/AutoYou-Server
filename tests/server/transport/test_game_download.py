"""The paired phone downloads game HTML from the computer over its data channel."""

import base64
import gzip
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import server
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
