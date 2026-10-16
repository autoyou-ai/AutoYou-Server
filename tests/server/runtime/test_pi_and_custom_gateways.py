# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Tests for Pi and Custom API gateways and endpoints."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared import custom_gateway, pi_gateway
from routers.agents import register_routes as register_agent_routes


@pytest.fixture
def mock_app():
    import server
    app = FastAPI()
    register_agent_routes(app, app, server)
    return app


@pytest.mark.asyncio
async def test_probe_pi_returns_status(monkeypatch):
    monkeypatch.setenv("PI_PORT", "8650")
    monkeypatch.setenv("PI_PATH", "/synthetic/pi/path")

    # Mock successful probe
    with patch("shared.pi_gateway.probe_pi", new_callable=AsyncMock) as mock_probe:
        mock_probe.return_value = {
            "available": True,
            "running": True,
            "port": 8650,
            "models": ["pi-agent"],
            "path": "/synthetic/pi/path",
        }
        res = await pi_gateway.probe_pi(port=8650, path="/synthetic/pi/path")
        assert res["running"] is True
        assert "pi-agent" in res["models"]


@pytest.mark.asyncio
async def test_call_pi_gateway_http_success():
    fake_reply = {
        "choices": [{"message": {"content": "synthetic Pi response", "role": "assistant"}}],
        "model": "pi-agent",
        "usage": {"total_tokens": 42},
    }

    class MockResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return fake_reply

        async def text(self):
            return "synthetic text"

    class MockSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return MockResponse()

    with patch("aiohttp.ClientSession", MockSession):
        res = await pi_gateway.call_pi_gateway(
            message="Hello Pi",
            session_id="synthetic-session-123",
            api_base="http://127.0.0.1:8650",
        )
        assert res is not None
        assert res["response"] == "synthetic Pi response"
        assert res["model"] == "pi-agent"


@pytest.mark.asyncio
async def test_call_pi_gateway_cli_fallback(monkeypatch, tmp_path):
    cli_file = tmp_path / "packages" / "coding-agent" / "dist" / "bundle" / "cli.js"
    cli_file.parent.mkdir(parents=True)
    cli_file.write_text("// Synthetic CLI fixture", encoding="utf-8")
    class FailingSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise Exception("Connection refused")

        async def __aexit__(self, *args):
            return False

    with patch("aiohttp.ClientSession", FailingSession):
        with patch("shared.pi_gateway._run_pi_cli_direct", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "CLI synthetic output"
            res = await pi_gateway.call_pi_gateway(
                message="Test CLI",
                session_id="synthetic-session-cli",
                api_base="http://127.0.0.1:8650",
                pi_path=str(tmp_path),
            )
            assert res is not None
            assert res["response"] == "CLI synthetic output"
            mock_cli.assert_awaited_once_with(tmp_path, cli_file, "Test CLI", timeout=120.0, model="pi-agent")


@pytest.mark.asyncio
async def test_call_custom_gateway_multi_channel():
    fake_reply = {
        "choices": [
            {
                "message": {
                    "content": 'Here is the result.\n```json\n{"command": "navigate", "target": "dashboard"}\n```',
                    "role": "assistant",
                }
            }
        ],
        "model": "custom-agent-v1",
    }

    class MockResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return fake_reply

        async def text(self):
            return ""

    class MockSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            payload = kwargs.get("json", {})
            # Verify multi-channel context is forwarded
            assert "channels" in payload
            assert "chat" in payload["channels"]
            assert "audio" in payload["channels"]
            assert "video" in payload["channels"]
            assert "control" in payload["channels"]
            return MockResponse()

    context = [
        {"type": "image", "data": "syntheticframe", "mime_type": "image/jpeg"},
        {"type": "audio", "data": "syntheticaudio", "mime_type": "audio/wav"},
    ]

    with patch("aiohttp.ClientSession", MockSession):
        res = await custom_gateway.call_custom_gateway(
            message="Check video and audio",
            session_id="synthetic-session-custom",
            api_base="http://127.0.0.1:8000",
            context=context,
        )
        assert res is not None
        assert "Here is the result" in res["response"]
        assert len(res["commands"]) == 1
        assert res["commands"][0]["command"] == "navigate"


def test_agent_routes_status_endpoints(mock_app, monkeypatch):
    import server
    monkeypatch.setattr(server, "_require_api_login_json", lambda _req: None)
    client = TestClient(mock_app)

    with patch("shared.pi_gateway.probe_pi", new_callable=AsyncMock) as mock_pi:
        mock_pi.return_value = {"available": True, "running": True, "port": 8650, "models": ["pi-agent"]}
        r = client.get("/api/ai/pi/status")
        assert r.status_code == 200
        assert r.json()["running"] is True

    with patch("shared.custom_gateway.probe_custom", new_callable=AsyncMock) as mock_custom:
        mock_custom.return_value = {"available": True, "api_base": "http://127.0.0.1:8000", "models": ["custom"]}
        r = client.get("/api/ai/custom/status")
        assert r.status_code == 200
        assert r.json()["available"] is True


def test_custom_relay_endpoint(mock_app, monkeypatch):
    import server
    monkeypatch.setattr(server, "_require_api_login_json", lambda _req: None)
    client = TestClient(mock_app)

    payload = {
        "text": "relayed text speech",
        "command": {"type": "pan_tilt", "x": 10, "y": 20},
        "target_session": "synthetic-target-session",
    }

    r = client.post("/api/custom/relay", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
