"""Game Studio serves a complete Unreal source plugin only to signed-in users."""

import asyncio
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import socket
from threading import Thread
import time
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest
import uvicorn

def test_unreal_plugin_download_is_authenticated_and_complete(monkeypatch):
    agents_root = (Path(__file__).resolve().parents[3] / "autoyou_agents")
    monkeypatch.syspath_prepend(str(agents_root.parent))
    spec = importlib.util.spec_from_file_location(
        "game_agent_website_app_test", agents_root / "game_agent/website/backend/app.py"
    )
    game_app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(game_app)
    client = TestClient(game_app.app)
    monkeypatch.setattr(game_app._smc, "_describe_chat_auth_state", lambda *_: {"authenticated": False})
    assert client.get("/api/game/unreal-plugin").status_code == 401
    assert client.get("/play").status_code == 401

    monkeypatch.setattr(game_app._smc, "_describe_chat_auth_state", lambda *_: {"authenticated": True})
    game = client.get("/play")
    assert game.status_code == 200 and b'<canvas id="game"' in game.content
    assert "session_start" in client.get("/api/game/protocol").json()["events"]
    response = client.get("/api/game/unreal-plugin")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    with ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        prefix = "AutoYouGameInput/"
        assert {prefix + name for name in (
            "AutoYouGameInput.uplugin",
            "README.md",
            "Source/AutoYouGameInput/AutoYouGameInput.Build.cs",
            "Source/AutoYouGameInput/Public/AutoYouGameInputSubsystem.h",
            "Source/AutoYouGameInput/Private/AutoYouGameInputSubsystem.cpp",
        )} == names
        header = archive.read(prefix + "Source/AutoYouGameInput/Public/AutoYouGameInputSubsystem.h").decode()
        source = archive.read(prefix + "Source/AutoYouGameInput/Private/AutoYouGameInputSubsystem.cpp").decode()
        assert "FString CoordinateMode" in header and "bool bHasMousePosition" in header
        assert 'TryGetStringField(TEXT("coordinate_mode"), Frame.CoordinateMode)' in source
        assert 'Frame.bHasMousePosition = Event == TEXT("remote_desktop_input") && bHasX && bHasY' in source


def test_host_game_relay_uses_local_stream_without_exposing_credential(monkeypatch):
    agents_root = (Path(__file__).resolve().parents[3] / "autoyou_agents")
    assert json.loads((agents_root / "game_agent/website/manifest.json").read_text())["websocket_enabled"] is True
    monkeypatch.syspath_prepend(str(agents_root.parent))
    spec = importlib.util.spec_from_file_location(
        "game_agent_native_relay_test", agents_root / "game_agent/website/backend/app.py"
    )
    game_app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(game_app)
    monkeypatch.setattr(game_app._smc, "_describe_chat_auth_state", lambda *_: {"authenticated": True})
    hub = SimpleNamespace(token="synthetic-game-token", connected=False)
    server = SimpleNamespace(
        STATE=SimpleNamespace(config={}), WEBRTC=SimpleNamespace(game_input_hub=hub),
        ADMIN_WEB_SERVICE_PORT=8001, _get_game_mode_available=lambda **_: True,
    )
    monkeypatch.setattr(game_app._smc, "_runtime_server", lambda: server)
    upstream_calls = []

    class FakeStream:
        def __init__(self):
            self.sent = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def recv(self):
            if not self.sent:
                self.sent = True
                return '{"event":"game_input","input_type":"session_start","session_id":"synthetic-player"}'
            await asyncio.Event().wait()

    def fake_connect(url, **kwargs):
        upstream_calls.append((url, kwargs))
        return FakeStream()

    monkeypatch.setattr(game_app, "connect", fake_connect)
    client = TestClient(game_app.app, base_url="http://127.0.0.1:8112", client=("127.0.0.1", 50000))
    status = client.get("/api/game/native-input/status")
    assert status.json() == {"available": True, "engine_connected": False}
    assert "synthetic-game-token" not in status.text

    monkeypatch.setattr(game_app._smc, "_describe_chat_auth_state", lambda *_: {"authenticated": False})
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://127.0.0.1:8112/api/game/native-input", headers={"origin": "http://127.0.0.1:8112"}):
            pass
    monkeypatch.setattr(game_app._smc, "_describe_chat_auth_state", lambda *_: {"authenticated": True})
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://127.0.0.1:8112/api/game/native-input", headers={"origin": "https://remote.example"}):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://127.0.0.1:8112/api/game/native-input", headers={
            "origin": "http://127.0.0.1:8112", "x-autoyou-remote-browser": "webrtc"
        }):
            pass
    assert not upstream_calls

    with client.websocket_connect("ws://127.0.0.1:8112/api/game/native-input", headers={"origin": "http://127.0.0.1:8112"}) as ws:
        assert ws.receive_json() == {
            "event": "game_input", "input_type": "session_start", "session_id": "synthetic-player"
        }
    assert upstream_calls == [(
        "ws://127.0.0.1:8001/api/webrtc/game-input/stream",
        {"additional_headers": {"Authorization": "Bearer synthetic-game-token"}, "open_timeout": 5},
    )]


def test_host_game_input_reaches_page_proxy_and_core_stream(monkeypatch):
    agents_root = (Path(__file__).resolve().parents[3] / "autoyou_agents")
    monkeypatch.syspath_prepend(str(agents_root.parent))
    import autoyou_page_service
    import server
    from autoyou_agents.shared_tools.frontend_registry import build_frontend_registry

    monkeypatch.setattr(server, "_get_game_mode_available", lambda **_: True)
    monkeypatch.setattr(server, "WEBRTC", server.WebRTCManager())
    game_app = server._load_managed_frontend_app(server._managed_frontend_runtime_specs()["game_agent"])
    import autoyou_agents.game_agent.website.backend.app as game_module
    monkeypatch.setattr(game_module._smc, "_describe_chat_auth_state", lambda *_: {"authenticated": True})

    running = []

    def serve(app):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        instance = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off"))
        ready = {}

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ready["loop"] = loop
            try:
                loop.run_until_complete(instance.serve(sockets=[listener]))
            finally:
                loop.close()

        thread = Thread(target=run, daemon=True)
        thread.start()
        running.append((instance, thread))
        deadline = time.monotonic() + 5
        while not instance.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert instance.started
        return port, ready["loop"]

    try:
        admin_port, admin_loop = serve(server.admin_app)
        monkeypatch.setattr(server, "ADMIN_WEB_SERVICE_PORT", admin_port)
        game_port, _ = serve(game_app)
        registry = build_frontend_registry(
            agents_root=Path(server._AUTOYOU_AGENTS_ROOT), agent_names=["game_agent"],
            proxy_ports={"game_agent": game_port},
        )
        assert registry["frontends"][0]["websocket_enabled"] is True
        monkeypatch.setattr(autoyou_page_service, "load_frontend_registry", lambda: registry)
        page = autoyou_page_service.AutoYouPageService()
        client = TestClient(page.app, base_url="http://127.0.0.1:8067", client=("127.0.0.1", 50000))
        with client.websocket_connect("/agent/game_agent/api/game/native-input", headers={
            "host": "127.0.0.1:8067", "origin": "http://127.0.0.1:8067"
        }) as stream:
            admin_loop.call_soon_threadsafe(server.WEBRTC.game_input_hub.publish, "synthetic-player", {
                "event": "game_input", "input_type": "touch", "points": [{"id": 0, "x": 0.5, "y": 0.25}],
            })
            frame = stream.receive_json()
            assert frame["session_id"] == "synthetic-player"
            assert frame["points"] == [{"id": 0, "x": 0.5, "y": 0.25}]
        deadline = time.monotonic() + 2
        while server.WEBRTC.game_input_hub.connected and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not server.WEBRTC.game_input_hub.connected
    finally:
        for instance, thread in reversed(running):
            instance.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()
