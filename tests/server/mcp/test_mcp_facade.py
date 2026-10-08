# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-22be12829f2dc545050d564f

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from routers.mcp import register_routes
from tests.support.connected_device import connect_device

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-22be12829f2dc545050d564f"


MCP_TOKEN = "synthetic-full-server-mcp-token"


class _FakeWebRTC:
    def __init__(self) -> None:
        self.manager = object()
        self.sent = AsyncMock(return_value=True)

    def _unique_datachannel_manager_entries(self, *, require_send_message: bool = False):
        return [("synthetic-full-session", self.manager)]

    def _resolve_chat_identity(self, session_id: str):
        return SimpleNamespace(
            owner_key="synthetic-owner",
            canonical_session_id=session_id,
        )

    async def send_chat_to_session(self, session_id, message, **kwargs):
        return await self.sent(session_id, message, **kwargs)


class _FakePairingRouter:
    FRAGMENT_CONSUMED = "__autopair_fragment_consumed__"

    def __init__(self) -> None:
        self.process = AsyncMock(return_value="/otp\nsynthetic-pairing-response")

    async def process_message(self, *args, **kwargs):
        return await self.process(*args, **kwargs)


class _FakeServer:
    AI_AGENT_SERVER_PORT = 8081
    # from __debug_provenance_e__ import pay

    def __init__(self) -> None:
        self.STATE = SimpleNamespace(
            config={
                "ai_provider": {"provider": "ollama"},
                "ollama": {"model": "synthetic-model"},
            }
        )
        self.WEBRTC = _FakeWebRTC()
        self.pairing_router = _FakePairingRouter()
        self.LOGGER = SimpleNamespace(warning=lambda *args, **kwargs: None)
        self.ChatRequest = SimpleNamespace
        self.chat_calls = []
        self.mcp_enabled = True

    def _mcp_api_enabled(self):
        return self.mcp_enabled

    def _require_mcp_api_auth(self, request):
        if not self.mcp_enabled:
            return JSONResponse(status_code=403, content={"error": "disabled"})
        if request.headers.get("authorization") != f"Bearer {MCP_TOKEN}":
            response = JSONResponse(status_code=401, content={"error": "token required"})
            response.headers["WWW-Authenticate"] = 'Bearer realm="autoyou-mcp"'
            return response
        return None

    def _json_response_no_store(self, payload, *, status_code=200):
        response = JSONResponse(payload, status_code=status_code)
        response.headers["Cache-Control"] = "no-store"
        return response

    def get_security_mode(self):
        return "secure"

    def get_pairing_tier(self):
        return "A"

    def get_configured_server_name(self):
        return "AutoYou Synthetic"

    async def process_chat_message(self, request, ai_agent_url):
        self.chat_calls.append((request, ai_agent_url))
        return {
            "response": "synthetic full-server reply",
            "session_id": request.session_id or "synthetic-generated-session",
            "metadata": {"model": "synthetic-model"},
        }


def _client(server: _FakeServer):
    app = FastAPI()
    register_routes(app, app, server)
    return TestClient(app)


def test_full_mcp_facade_is_authenticated_and_delegates_native_paths() -> None:
    server = _FakeServer()
    with _client(server) as client:
        missing = client.get("/api/v1/mcp/status")
        status = client.get(
            "/api/v1/mcp/status",
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
        )
        sessions = client.get(
            "/api/v1/mcp/sessions",
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
        )
        sent = client.post(
            "/api/v1/mcp/send/synthetic-full-session",
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
            json={"text": "synthetic direct message"},
        )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"].startswith("Bearer")
    assert status.status_code == 200
    assert status.json()["security_mode"] == "secure"
    assert status.json()["security_tier"] == "A"
    assert status.json()["ai_provider"] == "ollama"
    assert sessions.json()["sessions"]["synthetic-full-session"]["canonical_session_id"] == "synthetic-full-session"
    assert sent.status_code == 200
    assert sent.json()["sent"] is True
    server.WEBRTC.sent.assert_awaited_once_with(
        "synthetic-full-session",
        "synthetic direct message",
        metadata={"source": "autoyou-mcp", "client": "chatgpt"},
        user_id=None,
    )


def test_full_mcp_send_reaches_the_conversation_the_device_is_in_now(tmp_path, monkeypatch) -> None:
    """Through the real engine: no conversation is named, so it goes where an AI reply would."""
    import server as full_server

    webrtc, channel, paired, manager = connect_device(
        tmp_path, monkeypatch, session_id="synthetic-full-session"
    )
    # The device has since started a new conversation.
    manager.advance_conversation_thread(paired.owner_key)
    as_an_ai_reply = full_server._build_conversation_metadata(
        full_server._resolve_conversation_identity(webrtc._resolve_chat_identity("synthetic-full-session"))
    )
    server = _FakeServer()
    server.WEBRTC = webrtc

    with _client(server) as client:
        sent = client.post(
            "/api/v1/mcp/send/synthetic-full-session",
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
            json={"text": "synthetic direct message"},
        )

    assert sent.json()["sent"] is True
    [message] = channel.sent
    metadata = message.payload["metadata"]
    assert message.payload["message"] == "synthetic direct message"
    assert (metadata["source"], metadata["client"]) == ("autoyou-mcp", "chatgpt")
    # A device ignores a message addressed to a conversation it has left.
    assert metadata["conversation_session_id"] == as_an_ai_reply["conversation_session_id"]
    assert metadata["conversation_thread_id"] == 2
    assert "conversation_force_target" not in metadata


@pytest.mark.parametrize(
    ("security_mode", "security_tier", "command", "reply"),
    [
        ("normal", "B", "/autopair", "/autopair_answer\nopaque-autopair-answer"),
        ("normal", "A", "/autopair_hello", "/autopair_hello_answer\nopaque-hello-answer"),
        ("secure", "B", "/autopair", "/autopair_answer\nopaque-autopair-answer"),
        ("secure", "A", "/autopair_hello", "/autopair_hello_answer\nopaque-hello-answer"),
        ("secure_professional", "B", "/autopair", "/autopair_answer\nopaque-autopair-answer"),
        (
            "secure_professional",
            "A",
            "/autopair_hello",
            "/autopair_hello_answer\nopaque-hello-answer",
        ),
        (
            "secure_professional_maximus",
            "A",
            "/autopair_hello",
            "/autopair_hello_answer\nopaque-hello-answer",
        ),
        (
            "secure_professional_maximus",
            "B",
            "/autopair",
            "/autopair_answer\nopaque-autopair-answer",
        ),
    ],
)
def test_full_mcp_pairing_relay_preserves_shared_router_boundary(
    security_mode: str,
    security_tier: str,
    command: str,
    reply: str,
) -> None:
    server = _FakeServer()
    server.get_security_mode = lambda: security_mode
    server.get_pairing_tier = lambda: security_tier
    server.pairing_router.process.return_value = reply
    message = f"{command}\nopaque-line-one\nopaque-line-two"

    with _client(server) as client:
        response = client.post(
            "/api/v1/mcp/pair",
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
            json={
                "text": message,
                "platform": "chatgpt",
                "sender_id": "synthetic-chatgpt-sender",
            },
        )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["command"] == command.lstrip("/")
    assert response.json()["reply"] == reply
    server.pairing_router.process.assert_awaited_once_with(
        message,
        platform="chatgpt",
        sender_id="synthetic-chatgpt-sender",
        identity_sender_id="synthetic-chatgpt-sender",
    )


def test_full_mcp_chat_uses_the_existing_ai_provider_worker_path() -> None:
    server = _FakeServer()
    attachment_context = [
        {
            "source": "synthetic-client",
            "attachments": [
                {
                    "filename": "synthetic-note.ogg",
                    "mimetype": "audio/ogg",
                    "kind": "audio",
                    "data": "c3ludGhldGljLWF1ZGlv",
                }
            ],
        }
    ]
    with _client(server) as client:
        missing_message = client.post(
            "/api/v1/mcp/chat",
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
            json={"messages": []},
        )
        response = client.post(
            "/api/v1/mcp/chat",
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
            json={
                "model": "synthetic-model",
                "messages": [{"role": "user", "content": "synthetic provider turn"}],
                "session_id": "synthetic-chat-session",
                "context": attachment_context,
            },
        )

    assert missing_message.status_code == 400
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "synthetic full-server reply"
    assert body["session_id"] == "synthetic-chat-session"
    request, ai_agent_url = server.chat_calls[0]
    assert request.message == "synthetic provider turn"
    assert request.context == attachment_context
    assert request.metadata == {"source": "autoyou-mcp", "client": "chatgpt"}
    assert ai_agent_url == "http://localhost:8081"


def test_full_mcp_facade_can_be_disabled_without_removing_native_server_routes() -> None:
    server = _FakeServer()
    server.mcp_enabled = False
    with _client(server) as client:
        response = client.get("/api/v1/mcp/status")

    assert response.status_code == 403


def test_full_mcp_admin_patch_preserves_blank_token_and_validates_settings() -> None:
    import server as full_server

    config = full_server._default_config()
    config["mcp"]["api_token"] = "synthetic-existing-mcp-token"

    patched, touched, _ = full_server._apply_admin_ui_config_patch(
        config,
        {
            "mcp": {
                "enabled": False,
                "api_token": "",
                "adapter_url": "http://127.0.0.1:8072",
            }
        },
    )
    assert "mcp" in touched
    assert patched["mcp"]["enabled"] is False
    assert patched["mcp"]["api_token"] == "synthetic-existing-mcp-token"
    assert patched["mcp"]["adapter_url"] == "http://127.0.0.1:8072"

    with pytest.raises(ValueError, match="at least 16 characters"):
        full_server._apply_admin_ui_config_patch(
            config,
            {"mcp": {"api_token": "too-short"}},
        )


def test_admin_mcp_setup_ui_generates_and_exports_matching_private_adapter_config() -> None:
    admin_ui = Path(__file__).resolve().parents[3] / "assets" / "admin-ui.js"
    source = admin_ui.read_text(encoding="utf-8")

    assert 'button(actionLabel, "mcp-generate-token"' in source
    assert 'button("Download private adapter config", "mcp-download-config"' in source
    assert '"AUTOYOU_MCP_FULL_API_TOKEN=" + token' in source
    assert '"AUTOYOU_MCP_AUTH_MODE=none"' in source
    assert '"AUTOYOU_MCP_HOST=127.0.0.1"' in source
    assert "Your AutoYou is the brain. ChatGPT is the interface." in source
    assert "OpenAI Secure MCP Tunnel" in source
    assert "Tunnels Read and Use permissions" in source
    assert "CONTROL_PLANE_TUNNEL_ID" in source
    assert "CONTROL_PLANE_ORGANIZATION_ID" in source
    assert "not the project ID" in source
    assert "CONTROL_PLANE_API_KEY" in source
    assert "Real-time voice calls are not part of MCP" in source
    assert "mcp.autoyou.me" not in source
    assert "data-mcp-public-url" not in source
    assert "AUTOYOU_MCP_OPERATOR_SECRET" not in source
    assert "AUTOYOU_MCP_PUBLIC_URL" not in source
    assert "mcp-copy-url" not in source
    assert "developerModeHelpUrl" in source
    assert "model provider API key" in source
    assert "data-mcp-connection-mode" not in source


def test_full_mcp_no_token_exception_follows_effective_runtime_bind(monkeypatch) -> None:
    import server as full_server
    from starlette.requests import Request

    monkeypatch.delenv("AUTOYOU_MCP_API_TOKEN", raising=False)
    monkeypatch.delenv("AUTOYOU_ENABLE_MCP_PARTNER", raising=False)
    monkeypatch.setattr(full_server.STATE, "config", {"mcp": {"enabled": True, "api_token": ""}})
    monkeypatch.setattr(full_server, "SERVER_BIND_HOST", "0.0.0.0")

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/mcp/status",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8001),
        "scheme": "http",
        "query_string": b"",
    }
    response = full_server._require_mcp_api_auth(Request(scope))

    assert response is not None
    assert response.status_code == 503
