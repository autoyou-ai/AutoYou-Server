# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-a443407d4cf8d82748bc0a3a


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-a443407d4cf8d82748bc0a3a"

import os
import sys
import time
import asyncio
from types import SimpleNamespace

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

# Tool implementations, HTTP transport, session helpers, and state-key constants
# live in admin_tool; the ADK callbacks live in agent.
from autoyou_agents.admin_agent import admin_tool as admin_agent
from autoyou_agents.admin_agent import agent as admin_agent_callbacks
from shared.adk_state import AUTOYOU_REPLY_TARGET_USER_STATE_KEY


class _FakeToolContext:
    def __init__(self, state=None):
        self.state = dict(state or {})


def _fake_user_llm_request(text: str):
    return SimpleNamespace(
        config=SimpleNamespace(system_instruction=None),
        contents=[SimpleNamespace(role="user", parts=[SimpleNamespace(text=text)])],
    )


def test_verify_admin_totp_stores_user_scoped_session(monkeypatch):
    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        assert method == "POST"
        assert path == "/api/admin/session/verify"
        assert payload == {"totp_code": "123456"}
        return {
            "status": "success",
            "data": {"valid": True, "client_id": "ios", "token": "token-123"},
        }

    monkeypatch.setattr(admin_agent, "_http", fake_http)
    tool_context = _FakeToolContext()

    result = admin_agent.verify_admin_totp("123456", tool_context=tool_context)

    assert result["valid"] is True
    assert tool_context.state["user:admin_session_client_id"] == "ios"
    assert tool_context.state["user:admin_session_auth_token"] == "token-123"
    assert tool_context.state["admin_session_auth_token"] == "token-123"
    assert tool_context.state["user:admin_session_valid_until"] > time.time()


def test_check_admin_session_reports_missing_totp_configuration(monkeypatch):
    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        assert method == "GET"
        assert path == "/api/admin/session/capabilities"
        return {
            "status": "success",
            "data": {
                "totp_configured": False,
                "usable_totp_client_count": 0,
                "security_mode": "normal",
            },
        }

    monkeypatch.setattr(admin_agent, "_http", fake_http)

    result = admin_agent.check_admin_session()

    assert result["active"] is False
    assert result["totp_configured"] is False
    assert result["usable_totp_client_count"] == 0
    assert "no usable admin 2FA secret configured" in result["message"]


def test_get_account_oauth_sign_in_is_totp_free_and_sanitized(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PUBLIC_URL", "https://app.autoyou.me")
    assert admin_agent_callbacks.get_account_oauth_sign_in is admin_agent.get_account_oauth_sign_in

    result = admin_agent.get_account_oauth_sign_in(
        provider="github",
        next_path="/dashboard?section=funding",
    )

    assert result["status"] == "success"
    assert result["auth"] == "account_oauth"
    assert result["provider"] == "github"
    assert result["requires_totp"] is False
    assert result["starts_admin_session"] is False
    assert result["dashboard_url"] == "https://app.autoyou.me/dashboard?section=funding"
    assert (
        result["oauth_url"]
        == "https://app.autoyou.me/v1/auth/oauth/github/start?next=%2Fdashboard%3Fsection%3Dfunding"
    )
    assert "TOTP" in result["message"]

    malicious = admin_agent.get_account_oauth_sign_in(
        provider="unsupported",
        next_path="https://example.com/capture",
    )
    assert malicious["provider"] == "google"
    assert malicious["dashboard_url"] == "https://app.autoyou.me/dashboard?section=funding"
    assert (
        malicious["oauth_url"]
        == "https://app.autoyou.me/v1/auth/oauth/google/start?next=%2Fdashboard%3Fsection%3Dfunding"
    )


def test_admin_before_model_callback_routes_playback_toggle_to_session_check_when_not_verified():
    response = asyncio.run(
        admin_agent_callbacks._admin_before_model_callback(
            SimpleNamespace(state={}, invocation_id="admin-playback-check"),
            _fake_user_llm_request("Enable playback"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert function_call.name == "check_admin_session"


def test_admin_before_model_callback_routes_totp_reply_to_verify_tool():
    response = asyncio.run(
        admin_agent_callbacks._admin_before_model_callback(
            SimpleNamespace(
                state={admin_agent._ADMIN_TOTP_PENDING_STATE_KEY: True},
                invocation_id="admin-verify",
            ),
            _fake_user_llm_request("211644"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert function_call.name == "verify_admin_totp"
    assert function_call.args == {"totp_code": "211644"}


def test_admin_before_model_callback_returns_recorded_tool_result():
    state = {
        admin_agent._ADMIN_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY: "admin-result",
        admin_agent._ADMIN_TOOL_RESULT_INVOCATION_ID_STATE_KEY: "admin-result",
        admin_agent._ADMIN_TOOL_RESULT_MESSAGE_STATE_KEY: "Admin session granted for 1 hour (client: ios). You can now perform high-risk operations.",
    }

    response = asyncio.run(
        admin_agent_callbacks._admin_before_model_callback(
            SimpleNamespace(state=state, invocation_id="admin-result"),
            _fake_user_llm_request("211644"),
        )
    )

    assert response.content.parts[0].text == state[admin_agent._ADMIN_TOOL_RESULT_MESSAGE_STATE_KEY]


def test_admin_after_tool_callback_marks_totp_pending_after_inactive_session_check():
    state = {}
    context = SimpleNamespace(state=state, invocation_id="admin-check")

    admin_agent_callbacks._admin_after_tool_callback(
        SimpleNamespace(name="check_admin_session"),
        {},
        context,
        {
            "active": False,
            "totp_configured": True,
            "message": "No active admin session. Call verify_admin_totp() with your current 6-digit authenticator code to start a 1-hour elevated session.",
        },
    )

    assert state[admin_agent._ADMIN_TOTP_PENDING_STATE_KEY] is True
    assert state[admin_agent._ADMIN_TOOL_RESULT_MESSAGE_STATE_KEY].startswith("No active admin session")


def test_dispatch_saved_reply_target_message_uses_saved_transport(monkeypatch):
    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"success": True}}

    monkeypatch.setattr(admin_agent, "_http", fake_http)
    state = {
        "user:admin_session_valid_until": time.time() + 300,
        "user:admin_session_auth_token": "token-456",
        AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
            "transport": "telegram",
            "chat_id": 12345,
            "reply_to_message_id": 77,
        },
    }

    result = admin_agent.dispatch_saved_reply_target_message("hi", state)

    assert result["status"] == "success"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/telegram/send",
            "payload": {"chat_id": 12345, "message": "hi", "reply_to_message_id": 77},
            "token": "token-456",
            "timeout": 30,
        }
    ]


def test_dispatch_saved_reply_target_message_uses_telegram_saved_messages_only(monkeypatch):
    calls = []
    media_context = [{"attachments": [{"filename": "synthetic.png", "mimetype": "image/png"}]}]

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"success": True}}

    monkeypatch.setattr(admin_agent, "_http", fake_http)
    state = {
        "user:admin_session_valid_until": time.time() + 300,
        "user:admin_session_auth_token": "token-telegram-user",
        AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
            "transport": "telegram_user",
            "to": "synthetic-recipient",
            "chat_id": 12345,
        },
    }

    result = admin_agent.dispatch_saved_reply_target_message("hi", state)
    context_result = admin_agent._dispatch_reply_target_message(
        message="image ready",
        reply_target={"transport": "telegram_user"},
        context=media_context,
        token="token-telegram-user",
    )

    assert result["status"] == "success"
    assert context_result["status"] == "success"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/telegram-user/send",
            "payload": {"message": "hi"},
            "token": "token-telegram-user",
            "timeout": 30,
        },
        {
            "method": "POST",
            "path": "/api/telegram-user/send",
            "payload": {"message": "image ready", "context": media_context},
            "token": "token-telegram-user",
            "timeout": 30,
        },
    ]


def test_dispatch_reply_target_message_preserves_telegram_media_context(monkeypatch):
    calls = []
    media_context = [
        {
            "attachments": [
                {
                    "filename": "synthetic-result.png",
                    "mimetype": "image/png",
                    "data": "c3ludGhldGlj",
                    "size_bytes": 9,
                }
            ]
        }
    ]

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"success": True}}

    monkeypatch.setattr(admin_agent, "_http", fake_http)

    result = admin_agent._dispatch_reply_target_message(
        message="Your image is ready.",
        reply_target={"transport": "telegram", "chat_id": 12345, "reply_to_message_id": 77},
        context=media_context,
        token="internal-token",
    )

    assert result["status"] == "success"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/telegram/send",
            "payload": {
                "chat_id": 12345,
                "message": "Your image is ready.",
                "reply_to_message_id": 77,
                "context": media_context,
            },
            "token": "internal-token",
            "timeout": 30,
        }
    ]


def test_dispatch_reply_target_message_preserves_partner_media_context(monkeypatch):
    calls = []
    media_context = [
        {
            "attachments": [
                {
                    "filename": "synthetic-result.mp4",
                    "mimetype": "video/mp4",
                    "data": "c3ludGhldGljLXZpZGVv",
                    "size_bytes": 15,
                }
            ]
        }
    ]

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"success": True}}

    monkeypatch.setattr(admin_agent, "_http", fake_http)

    targets = [
        ({"transport": "whatsapp", "to": "+15551230001"}, "/api/whatsapp/send"),
        ({"transport": "signal", "to": "+15551230002"}, "/api/signal/send"),
        (
            {
                "transport": "webrtc",
                "owner_key": "cloud:client-device-abc",
                "session_id": "relay-123",
            },
            "/api/webrtc/send",
        ),
    ]

    for reply_target, _path in targets:
        result = admin_agent._dispatch_reply_target_message(
            message="Your video is ready.",
            reply_target=reply_target,
            context=media_context,
            token="internal-token",
        )
        assert result["status"] == "success"

    assert calls == [
        {
            "method": "POST",
            "path": "/api/whatsapp/send",
            "payload": {
                "to": "+15551230001",
                "message": "Your video is ready.",
                "context": media_context,
            },
            "token": "internal-token",
            "timeout": 30,
        },
        {
            "method": "POST",
            "path": "/api/signal/send",
            "payload": {
                "to": "+15551230002",
                "message": "Your video is ready.",
                "context": media_context,
            },
            "token": "internal-token",
            "timeout": 30,
        },
        {
            "method": "POST",
            "path": "/api/webrtc/send",
            "payload": {
                "message": "Your video is ready.",
                "owner_key": "cloud:client-device-abc",
                "session_id": "relay-123",
                "context": media_context,
            },
            "token": "internal-token",
            "timeout": 30,
        },
    ]


def test_dispatch_saved_reply_target_message_supports_webrtc_targets(monkeypatch):
    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"success": True}}

    monkeypatch.setattr(admin_agent, "_http", fake_http)
    state = {
        "user:admin_session_valid_until": time.time() + 300,
        "user:admin_session_auth_token": "token-789",
        AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
            "transport": "webrtc",
            "owner_key": "cloud:client-device-abc",
            "session_id": "relay-123",
        },
    }

    result = admin_agent.dispatch_saved_reply_target_message("hi", state)

    assert result["status"] == "success"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/webrtc/send",
            "payload": {
                "message": "hi",
                "owner_key": "cloud:client-device-abc",
                "session_id": "relay-123",
            },
            "token": "token-789",
            "timeout": 30,
        }
    ]


def test_get_audio_playback_enabled_calls_backend(monkeypatch):
    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append((method, path, payload, token))
        return {"status": "success", "data": {"enabled": True, "installed": True}}

    monkeypatch.setattr(admin_agent, "_http", fake_http)
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "internal-playback-status-token")

    result = admin_agent.get_audio_playback_enabled()

    assert result["status"] == "success"
    assert result["data"]["enabled"] is True
    assert calls == [("GET", "/api/webrtc/playback/enabled", None, "internal-playback-status-token")]


def test_set_audio_playback_enabled_calls_backend(monkeypatch):
    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append((method, path, payload, token))
        return {"status": "success", "data": {"enabled": False, "installed": True}}

    monkeypatch.setattr(admin_agent, "_http", fake_http)
    tool_context = _FakeToolContext(
        {
            "user:admin_session_valid_until": time.time() + 300,
            "user:admin_session_auth_token": "token-audio-toggle",
        }
    )

    result = admin_agent.set_audio_playback_enabled(False, tool_context=tool_context)

    assert result["status"] == "success"
    assert result["data"]["enabled"] is False
    assert calls == [("POST", "/api/webrtc/playback/enabled", {"enabled": False}, "token-audio-toggle")]


def test_set_audio_playback_enabled_requires_admin_session(monkeypatch):
    monkeypatch.setattr(admin_agent, "_http", lambda *args, **kwargs: {"status": "success"})

    result = admin_agent.set_audio_playback_enabled(True)

    assert result == {
        "status": "error",
        "message": "Admin session required. Call verify_admin_totp() first.",
    }
