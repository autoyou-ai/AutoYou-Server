"""Exercise TV media through a real, test-owned managed-website HTTP listener."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
import uvicorn

from autoyou_agents.tv_agent.website.backend.app import create_app
from autoyou_agents.tv_agent.website.backend.jobs import Jobs


@pytest.fixture
def tv_proxy(tmp_path, monkeypatch):
    import autoyou_page_service
    async def metadata(self, item_id, imdb_id=""):
        return {"status": "missing_key", "message": "Synthetic test has no provider credentials"}
    monkeypatch.setattr(Jobs, "enrich_item", metadata)
    tv = create_app(tmp_path / "tv", otp_verifier=lambda code: code == "123456")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    tv.state.test_listener_port = port
    server = uvicorn.Server(uvicorn.Config(tv, host="127.0.0.1", port=port, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started, "Test-owned TV listener did not start"
        monkeypatch.setattr(autoyou_page_service, "load_frontend_registry", lambda: {
            "frontends": [{"agent_name": "tv_agent", "proxy_port": port, "frontend_port_registered": True}]})
        service = autoyou_page_service.AutoYouPageService()
        # Use a loopback test caller; no operator device pass or live registry.
        monkeypatch.setattr(service, "_is_home_network_browser", lambda peer: False)
        with TestClient(service.app) as client:
            yield client, tv
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), "Test listener did not shut down"


def test_tv_media_ranges_head_assets_and_chunked_uploads_through_path_proxy(tv_proxy):
    client, tv = tv_proxy
    prefix = "/agent/tv_agent/"
    index = client.get(prefix)
    assert index.status_code == 200 and "AutoYou TV" in index.text
    assert '<base href="/agent/tv_agent/"' in index.text
    import re
    script = re.search(r'src="(\./assets/[^\"]+\.js)"', index.text)
    assert script and client.get(prefix + script[1].removeprefix("./")).status_code == 200
    token = client.post(prefix + "api/auth/login", json={"code": "123456"}).json()["token"]
    headers = {"Authorization": "Bearer " + token, "Origin": "http://testserver"}
    body = bytes(range(256)) * 1200
    upload = client.post(prefix + "api/upload-sessions", json={"filename": "Synthetic.2024.mp4", "size": len(body)}, headers=headers).json()
    url = prefix + "api/upload-sessions/" + upload["id"]
    assert client.put(url, content=body[:200000], headers=headers).status_code == 200
    assert client.put(url, params={"offset": 200000}, content=body[200000:], headers=headers).status_code == 200
    item = client.post(url + "/complete", headers=headers).json()["item"]
    stream = prefix + item["stream_url"]
    response = client.get(stream, headers={"Range": "bytes=12345-256789"})
    assert response.status_code == 206 and response.content == body[12345:256790]
    assert response.headers["Content-Range"] == f"bytes 12345-256789/{len(body)}"
    assert response.headers["Content-Length"] == str(256790 - 12345)
    head = client.head(stream, headers={"Range": "bytes=-100"})
    assert head.status_code == 206 and head.content == b""
    assert head.headers["Content-Length"] == "100"
    assert head.headers["Content-Range"] == f"bytes {len(body)-100}-{len(body)-1}/{len(body)}"
    assert client.get(stream, headers={"If-None-Match": response.headers["ETag"]}).status_code == 304
    assert client.get(stream, headers={"Range": "bytes=99999999-"}).status_code == 416
    assert client.get(prefix + "api/settings", headers={"Authorization": "Bearer invalid-synthetic"}).status_code == 401
    assert Path(tv.state.store.item(item["id"])["file_path"]).is_relative_to(tv.state.store.root)


def test_tv_runtime_plan_includes_native_modules_and_built_frontend():
    from scripts.build_packaged_runtime_modules import build_runtime_module_plan
    root = Path(__file__).resolve().parents[3]
    plan = build_runtime_module_plan(root)
    prefix = "autoyou_agents/tv_agent/"
    modules = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}
    assert {prefix + "website/backend/" + name + ".py" for name in ("app", "auth", "store", "media", "jobs", "uploads", "playback")} <= modules
    assets = {path.as_posix() for path in plan.asset_files}
    assert prefix + "website/frontend/web/index.html" in assets
    assert any(path.startswith(prefix + "website/frontend/web/assets/") and path.endswith(".js") for path in assets)
    assert not any(path.startswith(prefix) and "node_modules" in path for path in assets)


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_tv_range_streams_as_bounded_datachannel_frames(tv_proxy, monkeypatch, native):
    import asyncio
    import base64
    from types import SimpleNamespace
    import httpx
    import server
    from shared.datachannel_manager import DEFAULT_MAX_CHUNK_SIZE, DataChannelMessage, MessageHeader, MessageType
    _, tv = tv_proxy
    body = bytes(range(256)) * 2048
    path = tv.state.store.media_dir / "synthetic-transport.mp4"
    path.write_bytes(body)
    item, _ = tv.state.store.add_item(source_key="transport", title="Synthetic transport", file_path=str(path))
    config = server._default_config()
    config["autoyou_page"]["remote_access_role"] = "viewer"
    monkeypatch.setattr(server.STATE, "config", config, raising=False)
    manager = server.WebRTCManager()
    monkeypatch.setattr(manager, "_get_autoyou_forward_target_port", lambda: tv.state.test_listener_port)
    frames = []
    raw_frames = []
    class Channel:
        chunker = SimpleNamespace(max_chunk_size=DEFAULT_MAX_CHUNK_SIZE)
        def reliable_binary_http(self):
            return True
        async def send_binary_http(self, request_id, sequence, data):
            from shared.binary_http import encode_frame, RESPONSE_BODY
            raw_frames.append(encode_frame(RESPONSE_BODY, request_id, sequence, data))
            return True
        async def send_message(self, message):
            frames.append(message)
            return True
    manager.datachannel_managers["synthetic-tv-session"] = Channel()
    request = DataChannelMessage(
        header=MessageHeader("synthetic-tv-message", MessageType.HTTP_REQUEST, 0.0, "synthetic-tv-session", "synthetic-viewer"),
        payload={"request_id": "synthetic-tv-request", "method": "GET",
                 "url": f"http://127.0.0.1:{tv.state.test_listener_port}/api/stream/{item['id']}",
                 "headers": {"Range": "bytes=123-400123"}, "body": ""})
    if native:
        request.payload["binary_http"] = 1
    async with httpx.AsyncClient(timeout=10, trust_env=False) as http:
        manager._http_client = http
        await asyncio.wait_for(manager._handle_http_request(request), timeout=10)
    kinds = [frame.header.message_type for frame in frames]
    assert kinds[0] == MessageType.HTTP_STREAM_OPEN and kinds[-1] == MessageType.HTTP_STREAM_END
    assert MessageType.HTTP_RESPONSE not in kinds
    assert frames[0].payload["status_code"] == 206
    headers = {key.lower(): value for key, value in frames[0].payload["raw_headers"]}
    assert headers["content-length"] == "400001"
    chunks = [frame for frame in frames if frame.header.message_type == MessageType.HTTP_STREAM_DATA]
    if native:
        from shared.binary_http import decode_frame, MAX_FRAME_BYTES
        assert frames[0].payload["headers"]["X-AutoYou-Binary-HTTP"] == "1"
        assert not chunks and len(raw_frames) > 1
        assert b"".join(decode_frame(frame)[3] for frame in raw_frames) == body[123:400124]
        assert all(len(frame) <= MAX_FRAME_BYTES for frame in raw_frames)
        return
    assert len(chunks) > 1
    assert b"".join(base64.b64decode(frame.payload["data"].removeprefix("base64:")) for frame in chunks) == body[123:400124]
    assert all(len(frame.to_json().encode()) <= DEFAULT_MAX_CHUNK_SIZE for frame in chunks)
