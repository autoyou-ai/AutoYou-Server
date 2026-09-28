# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-8f8c0eaec58888df5b5b46ec


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-8f8c0eaec58888df5b5b46ec"

import base64
import json
import os
import sys
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import server


class _FakeWhatsAppService:
    def __init__(self):
        self.calls = []
        self.media_calls = []

    async def send_message(self, to, message):
        self.calls.append((to, message))
        return True

    async def send_media_attachments(self, to, attachments):
        self.media_calls.append((to, attachments))
        return True


class _FakeSignalService:
    def __init__(self):
        self.calls = []
        self.media_calls = []

    async def send_message(self, to, message):
        self.calls.append((to, message))
        return True

    async def send_media_attachments(self, to, attachments):
        self.media_calls.append((to, attachments))
        return True


class _FakeTelegramUserService:
    def __init__(self):
        self.calls = []

    async def send_message(self, message):
        self.calls.append(message)
        return True

    async def get_status(self):
        return {
            "status": "connected",
            "connected": True,
            "authorized": True,
            "session": "synthetic-session-token",
            "api_hash": "synthetic-api-hash",
        }

    def get_message_log(self, limit):
        return [
            {
                "timestamp": "2026-01-02T03:04:05Z",
                "direction": "inbound",
                "message": "private synthetic message body",
                "attachments": [],
            },
            {
                "timestamp": "2026-01-02T03:04:06Z",
                "direction": "outbound",
                "message": "private synthetic reply body",
                "attachments": [{"filename": "synthetic-note.ogg"}],
            },
        ][:limit]


class _FakeWebRTC:
    def __init__(self):
        self.calls = []

    async def send_chat_to_reply_target(self, reply_target, message, *, metadata=None, context=None, user_id=None):
        call = {
            "reply_target": reply_target,
            "message": message,
            "metadata": metadata,
            "user_id": user_id,
        }
        if context is not None:
            call["context"] = context
        self.calls.append(call)
        return True

    async def send_rewarded_ad_control_to_reply_target(
        self,
        reply_target,
        payload,
        *,
        allow_single_live_fallback=False,
    ):
        self.calls.append(
            {
                "action": "rewarded_ad",
                "reply_target": reply_target,
                "payload": payload,
                "allow_single_live_fallback": allow_single_live_fallback,
            }
        )
        return True, {
            "success": True,
            "triggered_count": 1,
            "target_session_id": reply_target.get("session_id") or "single-live-session",
            "owner_key": reply_target.get("owner_key") or "cloud:single-live-client",
            "resolution": "single_live_datachannel" if allow_single_live_fallback else "reply_target",
        }

    async def play_audio_file_to_reply_target(self, reply_target, file_path):
        self.calls.append(
            {
                "action": "play",
                "reply_target": reply_target,
                "file_path": file_path,
            }
        )
        return True, {"event": "playback", "state": "playing", "detail": "queued"}

    async def pause_audio_playback_for_reply_target(self, reply_target):
        self.calls.append({"action": "pause", "reply_target": reply_target})
        return True, {"event": "playback", "state": "paused", "detail": "paused"}

    async def resume_audio_playback_for_reply_target(self, reply_target):
        self.calls.append({"action": "resume", "reply_target": reply_target})
        return True, {"event": "playback", "state": "playing", "detail": "resumed"}

    async def stop_audio_playback_for_reply_target(self, reply_target):
        self.calls.append({"action": "stop", "reply_target": reply_target})
        return True, {"event": "playback", "state": "stopped", "detail": "stopped"}

    async def get_audio_playback_status_for_reply_target(self, reply_target):
        self.calls.append({"action": "status", "reply_target": reply_target})
        return {"event": "playback", "state": "paused", "detail": "paused"}


def _authorized_headers():
    server.ADMIN_API_TOKENS["test-token"] = time.time() + 300
    return {
        "Authorization": "Bearer test-token",
        "Origin": "http://testserver",
        "Referer": "http://testserver/admin",
    }


def _same_origin_headers():
    return {
        "Origin": "http://testserver",
        "Referer": "http://testserver/admin",
    }


def test_admin_telegram_user_send_and_activity_are_owner_scoped(monkeypatch):
    original_service = server.STATE.telegram_user_service
    original_config = server.STATE.config
    original_tokens = dict(server.ADMIN_API_TOKENS)
    try:
        service = _FakeTelegramUserService()
        server.STATE.telegram_user_service = service
        server.STATE.config = {
            "telegram_user": {
                "enabled": True,
                "training_export_consent": False,
            }
        }
        server._invalidate_admin_status_cache("telegram_user_status")
        monkeypatch.setattr(server, "_messaging_partner_feature_enabled", lambda _partner: True)

        with TestClient(server.admin_app) as client:
            send_response = client.post(
                "/api/telegram-user/send",
                headers=_authorized_headers(),
                json={"message": "synthetic owner message"},
            )
            status_response = client.get(
                "/api/telegram-user/status",
                headers=_authorized_headers(),
            )
            messages_response = client.get(
                "/api/telegram-user/messages?limit=12",
                headers=_authorized_headers(),
            )

        assert send_response.status_code == 200
        assert send_response.json()["success"] is True
        assert service.calls == ["synthetic owner message"]

        status = status_response.json()
        assert status_response.status_code == 200
        assert status["owner_scoped"] is True
        assert status["saved_messages_only"] is True
        assert "session" not in status
        assert "api_hash" not in status

        activity = messages_response.json()
        assert messages_response.status_code == 200
        assert activity["content_included"] is False
        assert activity["messages"] == [
            {
                "timestamp": "2026-01-02T03:04:05Z",
                "direction": "inbound",
                "kind": "message",
                "has_media": False,
            },
            {
                "timestamp": "2026-01-02T03:04:06Z",
                "direction": "outbound",
                "kind": "media",
                "has_media": True,
            },
        ]
        assert "private synthetic message body" not in messages_response.text
        assert "private synthetic reply body" not in messages_response.text
    finally:
        server.STATE.telegram_user_service = original_service
        server.STATE.config = original_config
        server._invalidate_admin_status_cache("telegram_user_status")
        server.ADMIN_API_TOKENS.clear()
        server.ADMIN_API_TOKENS.update(original_tokens)


def test_telegram_user_config_patch_requires_qr_session_and_records_consent():
    base = server._default_config()
    base["telegram_user"].update(
        {
            "session": "synthetic-existing-session",
            "training_export_consent": False,
            "training_export_consent_at": "",
        }
    )

    updated, touched, _ = server._apply_admin_ui_config_patch(
        base,
        {
            "telegram_user": {
                "enabled": True,
                "api_id": "123456",
                "api_hash": "synthetic-api-hash",
                "session": "browser-supplied-session-must-be-ignored",
                "training_export_consent": True,
                "prompt_builder": {
                    "enabled": True,
                    "application_agent": "claude",
                },
            }
        },
    )

    telegram_user = updated["telegram_user"]
    assert "telegram_user" in touched
    assert telegram_user["api_id"] == 123456
    assert telegram_user["session"] == "synthetic-existing-session"
    assert telegram_user["training_export_consent"] is True
    assert telegram_user["training_export_consent_at"]
    assert telegram_user["prompt_builder"] == {
        "enabled": True,
        "application_agent": "claude_desktop_agent",
    }

    revoked, _, _ = server._apply_admin_ui_config_patch(
        updated,
        {"telegram_user": {"training_export_consent": False}},
    )
    assert revoked["telegram_user"]["training_export_consent"] is False
    assert revoked["telegram_user"]["training_export_consent_at"] == ""


def test_ai_agent_config_patch_persists_internet_search_switch():
    base = server._default_config()

    updated, touched, _ = server._apply_admin_ui_config_patch(
        base,
        {
            "ai_agent": {
                "internet_search_enabled": False,
                "lan_access_enabled": True,
            }
        },
    )

    assert "ai_agent" in touched
    assert updated["ai_agent"]["internet_search_enabled"] is False
    assert updated["ai_agent"]["lan_access_enabled"] is True


def test_admin_whatsapp_send_requires_authentication():
    original_whatsapp = server.STATE.whatsapp_service
    original_tokens = dict(server.ADMIN_API_TOKENS)
    try:
        server.STATE.whatsapp_service = _FakeWhatsAppService()
        with TestClient(server.admin_app) as client:
            response = client.post(
                "/api/whatsapp/send",
                headers=_same_origin_headers(),
                json={"to": "+15551234567", "message": "hi"},
            )

        assert response.status_code == 401
        assert response.json()["error"] == "Not authenticated"
    finally:
        server.STATE.whatsapp_service = original_whatsapp
        server.ADMIN_API_TOKENS.clear()
        server.ADMIN_API_TOKENS.update(original_tokens)


def test_admin_whatsapp_send_accepts_bearer_token():
    original_whatsapp = server.STATE.whatsapp_service
    original_tokens = dict(server.ADMIN_API_TOKENS)
    try:
        service = _FakeWhatsAppService()
        server.STATE.whatsapp_service = service
        with TestClient(server.admin_app) as client:
            response = client.post(
                "/api/whatsapp/send",
                headers=_authorized_headers(),
                json={"to": "+15551234567", "message": "hi"},
            )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert service.calls == [("+15551234567", "hi")]
    finally:
        server.STATE.whatsapp_service = original_whatsapp
        server.ADMIN_API_TOKENS.clear()
        server.ADMIN_API_TOKENS.update(original_tokens)


def test_admin_signal_and_telegram_send_accept_bearer_token(monkeypatch):
    original_signal = server.STATE.signal_service
    original_tokens = dict(server.ADMIN_API_TOKENS)
    telegram_calls = []

    async def fake_send_telegram(bot, chat_id, text, *, split_text=True, reply_to_message_id=None):
        telegram_calls.append(
            {
                "bot": bot,
                "chat_id": chat_id,
                "text": text,
                "split_text": split_text,
                "reply_to_message_id": reply_to_message_id,
            }
        )

    try:
        signal_service = _FakeSignalService()
        server.STATE.signal_service = signal_service
        monkeypatch.setattr(server, "_get_active_telegram_bot", lambda: object())
        monkeypatch.setattr(server, "_send_telegram_text_via_bot", fake_send_telegram)

        with TestClient(server.admin_app) as client:
            signal_response = client.post(
                "/api/signal/send",
                headers=_authorized_headers(),
                json={"to": "+15550000000", "message": "signal hi"},
            )
            telegram_response = client.post(
                "/api/telegram/send",
                headers=_authorized_headers(),
                json={"chat_id": 12345, "message": "telegram hi", "reply_to_message_id": 77},
            )

        assert signal_response.status_code == 200
        assert signal_response.json()["success"] is True
        assert signal_service.calls == [("+15550000000", "signal hi")]

        assert telegram_response.status_code == 200
        assert telegram_response.json()["success"] is True
        assert telegram_calls[0]["chat_id"] == 12345
        assert telegram_calls[0]["text"] == "telegram hi"
        assert telegram_calls[0]["reply_to_message_id"] == 77
    finally:
        server.STATE.signal_service = original_signal
        server.ADMIN_API_TOKENS.clear()
        server.ADMIN_API_TOKENS.update(original_tokens)


def test_admin_telegram_send_accepts_internal_token_and_media_context(monkeypatch):
    telegram_text_calls = []
    telegram_media_calls = []

    async def fake_send_telegram(bot, chat_id, text, *, split_text=True, reply_to_message_id=None):
        telegram_text_calls.append(
            {
                "bot": bot,
                "chat_id": chat_id,
                "text": text,
                "split_text": split_text,
                "reply_to_message_id": reply_to_message_id,
            }
        )

    async def fake_send_media(
        bot,
        *,
        chat_id,
        media_bytes,
        filename,
        mimetype,
        caption="",
        reply_to_message_id=None,
        queue_on_failure=True,
    ):
        telegram_media_calls.append(
            {
                "bot": bot,
                "chat_id": chat_id,
                "media_bytes": media_bytes,
                "filename": filename,
                "mimetype": mimetype,
                "caption": caption,
                "reply_to_message_id": reply_to_message_id,
                "queue_on_failure": queue_on_failure,
            }
        )
        return True

    monkeypatch.setattr(server, "_get_active_telegram_bot", lambda: object())
    monkeypatch.setattr(server, "_send_telegram_text_via_bot", fake_send_telegram)
    monkeypatch.setattr(server, "_send_telegram_media_payload", fake_send_media)
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)

    image_bytes = b"\x89PNG\r\n\x1a\nsynthetic image bytes"
    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/telegram/send",
            headers={
                "Authorization": "Bearer internal-ai-agent-token",
                **_same_origin_headers(),
            },
            json={
                "chat_id": 12345,
                "message": "Your image is ready.",
                "reply_to_message_id": 77,
                "context": [
                    {
                        "attachments": [
                            {
                                "filename": "synthetic-result.png",
                                "mimetype": "image/png",
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                                "size_bytes": len(image_bytes),
                            }
                        ]
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert telegram_text_calls == []
    assert telegram_media_calls == [
        {
            "bot": telegram_media_calls[0]["bot"],
            "chat_id": 12345,
            "media_bytes": image_bytes,
            "filename": "synthetic-result.png",
            "mimetype": "image/png",
            "caption": "Your image is ready.",
            "reply_to_message_id": 77,
            "queue_on_failure": True,
        }
    ]


def test_admin_whatsapp_and_signal_send_accept_internal_token_and_media_context(monkeypatch):
    original_whatsapp = server.STATE.whatsapp_service
    original_signal = server.STATE.signal_service
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)

    image_bytes = b"\x89PNG\r\n\x1a\nsynthetic whatsapp image"
    video_bytes = b"\x00\x00\x00\x18ftypmp42synthetic signal video"
    try:
        whatsapp_service = _FakeWhatsAppService()
        signal_service = _FakeSignalService()
        server.STATE.whatsapp_service = whatsapp_service
        server.STATE.signal_service = signal_service

        with TestClient(server.admin_app) as client:
            whatsapp_response = client.post(
                "/api/whatsapp/send",
                headers={"Authorization": "Bearer internal-ai-agent-token", **_same_origin_headers()},
                json={
                    "to": "+15551230001",
                    "message": "Your image is ready.",
                    "context": [
                        {
                            "attachments": [
                                {
                                    "filename": "synthetic-whatsapp-result.png",
                                    "mimetype": "image/png",
                                    "data": base64.b64encode(image_bytes).decode("ascii"),
                                    "size_bytes": len(image_bytes),
                                }
                            ]
                        }
                    ],
                },
            )
            signal_response = client.post(
                "/api/signal/send",
                headers={"Authorization": "Bearer internal-ai-agent-token", **_same_origin_headers()},
                json={
                    "to": "+15551230002",
                    "message": "Your video is ready.",
                    "attachments": [
                        {
                            "filename": "synthetic-signal-result.mp4",
                            "mimetype": "video/mp4",
                            "data": base64.b64encode(video_bytes).decode("ascii"),
                            "size_bytes": len(video_bytes),
                        }
                    ],
                },
            )

        assert whatsapp_response.status_code == 200
        assert whatsapp_response.json()["success"] is True
        assert whatsapp_service.calls == []
        assert whatsapp_service.media_calls[0][0] == "+15551230001"
        whatsapp_attachment = whatsapp_service.media_calls[0][1][0]
        assert whatsapp_attachment["filename"] == "synthetic-whatsapp-result.png"
        assert whatsapp_attachment["mimetype"] == "image/png"
        assert base64.b64decode(whatsapp_attachment["data"]) == image_bytes
        assert whatsapp_attachment["caption"] == "Your image is ready."

        assert signal_response.status_code == 200
        assert signal_response.json()["success"] is True
        assert signal_service.calls == []
        assert signal_service.media_calls[0][0] == "+15551230002"
        signal_attachment = signal_service.media_calls[0][1][0]
        assert signal_attachment["filename"] == "synthetic-signal-result.mp4"
        assert signal_attachment["mimetype"] == "video/mp4"
        assert base64.b64decode(signal_attachment["data"]) == video_bytes
        assert signal_attachment["caption"] == "Your video is ready."
    finally:
        server.STATE.whatsapp_service = original_whatsapp
        server.STATE.signal_service = original_signal


def test_admin_webrtc_send_accepts_bearer_token(monkeypatch):
    original_tokens = dict(server.ADMIN_API_TOKENS)
    fake_webrtc = _FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)

    try:
        with TestClient(server.admin_app) as client:
            response = client.post(
                "/api/webrtc/send",
                headers=_authorized_headers(),
                json={
                    "owner_key": "cloud:client-device-abc",
                    "session_id": "relay-123",
                    "message": "hello client",
                },
            )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert fake_webrtc.calls == [
            {
                "reply_target": {
                    "transport": "webrtc",
                    "owner_key": "cloud:client-device-abc",
                    "session_id": "relay-123",
                },
                "message": "hello client",
                "metadata": {"source": "admin_api"},
                "user_id": server.get_configured_server_name(),
            }
        ]
    finally:
        server.ADMIN_API_TOKENS.clear()
        server.ADMIN_API_TOKENS.update(original_tokens)


def test_admin_webrtc_send_accepts_internal_ai_agent_token_and_metadata(monkeypatch):
    fake_webrtc = _FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/webrtc/send",
            headers={
                "Authorization": "Bearer internal-ai-token",
                "Origin": "http://testserver",
                "Referer": "http://testserver/admin",
            },
            json={
                "owner_key": "cloud:client-device-abc",
                "message": "terminal update",
                "metadata": {
                    "source": "cli_agent",
                    "original_message_id": "cli-stream-1",
                    "is_streaming": True,
                    "response_author": "autoyou_cli_agent",
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert fake_webrtc.calls == [
        {
            "reply_target": {
                "transport": "webrtc",
                "owner_key": "cloud:client-device-abc",
            },
            "message": "terminal update",
            "metadata": {
                "source": "cli_agent",
                "original_message_id": "cli-stream-1",
                "is_streaming": True,
                "response_author": "autoyou_cli_agent",
            },
            "user_id": server.get_configured_server_name(),
        }
    ]


def test_admin_webrtc_rewarded_ad_accepts_internal_ai_agent_token(monkeypatch):
    fake_webrtc = _FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)

    assert server._native_mobile_ad_trigger_enabled() is True

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/webrtc/rewarded-ad",
            headers={
                "Authorization": "Bearer synthetic-internal-token",
                "Origin": "http://testserver",
                "Referer": "http://testserver/admin",
            },
            json={
                "owner_key": "telegram:5550001001",
                "allow_single_live_fallback": True,
                "payload": {
                    "expected_watch_seconds": 45,
                    "ad_units": {
                        "ios_rewarded": "ca-app-pub-0000000000000000/1111111111",
                        "android_rewarded": "ca-app-pub-0000000000000000/2222222222",
                    },
                    "reward_intent_endpoint": "https://attacker.example/reward",
                    "watch_progress_endpoint": "https://attacker.example/progress",
                    "status_endpoint": "https://attacker.example/status",
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["resolution"] == "single_live_datachannel"
    assert fake_webrtc.calls == [
        {
            "action": "rewarded_ad",
            "reply_target": {
                "transport": "webrtc",
                "owner_key": "telegram:5550001001",
            },
            "payload": {
                "expected_watch_seconds": 45,
                "event": "show_rewarded_ad",
                "platform": "server",
                "source": "ads_watching_agent",
                "native_ad_unit_source": "client_baked_autoyou_build",
                "desktop_web_ad_config_source": "signed_client_baked_release_env",
                "ad_unit_ids_in_payload": False,
            },
            "allow_single_live_fallback": True,
        }
    ]


def test_admin_webrtc_rewarded_ad_requires_target_without_single_live_fallback(monkeypatch):
    fake_webrtc = _FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/webrtc/rewarded-ad",
            headers={
                "Authorization": "Bearer synthetic-internal-token",
                "Origin": "http://testserver",
                "Referer": "http://testserver/admin",
            },
            json={"payload": {}},
        )

    assert response.status_code == 400
    assert "session_id" in response.json()["error"]
    assert fake_webrtc.calls == []


def test_admin_webrtc_rewarded_ad_can_be_disabled(monkeypatch):
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)
    monkeypatch.delenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", raising=False)
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "false")

    for off_value in ("false", "0", "off", "no"):
        fake_webrtc = _FakeWebRTC()
        monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
        monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", off_value)

        with TestClient(server.admin_app) as client:
            response = client.post(
                "/api/webrtc/rewarded-ad",
                headers={
                    "Authorization": "Bearer synthetic-internal-token",
                    "Origin": "http://testserver",
                    "Referer": "http://testserver/admin",
                },
                json={
                    "owner_key": "cloud:client-device-abc",
                    "payload": {"expected_watch_seconds": 30},
                },
            )

        assert response.status_code == 403
        payload = response.json()
        assert payload["success"] is False
        assert payload["triggered_count"] == 0
        assert "disabled" in payload["reason"]
        assert fake_webrtc.calls == []


def test_admin_webrtc_rewarded_ad_allows_native_mobile_trigger_when_web_rewarded_toggle_disabled(monkeypatch):
    fake_webrtc = _FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)
    monkeypatch.delenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", raising=False)
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", "")

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/webrtc/rewarded-ad",
            headers={
                "Authorization": "Bearer synthetic-internal-token",
                "Origin": "http://testserver",
                "Referer": "http://testserver/admin",
            },
            json={
                "owner_key": "cloud:client-device-abc",
                "payload": {"expected_watch_seconds": 30},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["triggered_count"] == 1
    assert payload["resolution"] == "reply_target"
    assert fake_webrtc.calls == [
        {
            "action": "rewarded_ad",
            "reply_target": {
                "transport": "webrtc",
                "owner_key": "cloud:client-device-abc",
            },
            "payload": {
                "expected_watch_seconds": 30,
                "event": "show_rewarded_ad",
                "platform": "server",
                "source": "ads_watching_agent",
                "native_ad_unit_source": "client_baked_autoyou_build",
                "desktop_web_ad_config_source": "signed_client_baked_release_env",
                "ad_unit_ids_in_payload": False,
            },
            "allow_single_live_fallback": False,
        }
    ]


def test_admin_webrtc_rewarded_ad_stays_disabled_when_native_mobile_trigger_off(monkeypatch):
    fake_webrtc = _FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "false")
    monkeypatch.delenv("AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", raising=False)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/webrtc/rewarded-ad",
            headers={
                "Authorization": "Bearer synthetic-internal-token",
                "Origin": "http://testserver",
                "Referer": "http://testserver/admin",
            },
            json={
                "owner_key": "cloud:client-device-abc",
                "payload": {"expected_watch_seconds": 30},
                "allow_single_live_fallback": True,
            },
        )

    assert response.status_code == 403
    payload = response.json()
    assert payload["success"] is False
    assert payload["triggered_count"] == 0
    assert "disabled" in payload["reason"]
    assert fake_webrtc.calls == []


def test_admin_webrtc_rewarded_ad_rejects_web_rewarded_unit_hint_when_native_mobile_trigger_off(monkeypatch):
    fake_webrtc = _FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "true")

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/webrtc/rewarded-ad",
            headers={
                "Authorization": "Bearer synthetic-internal-token",
                "Origin": "http://testserver",
                "Referer": "http://testserver/admin",
            },
            json={
                "owner_key": "cloud:client-device-abc",
                "payload": {"expected_watch_seconds": 30, "web_rewarded_ad_unit": "/rewarded/autoyou"},
            },
        )

    assert response.status_code == 403
    payload = response.json()
    assert payload["success"] is False
    assert payload["triggered_count"] == 0
    assert "disabled" in payload["reason"]
    assert fake_webrtc.calls == []


def test_admin_webrtc_rewarded_ad_ignores_server_web_rewarded_unit_hint_when_native_mobile_trigger_off(monkeypatch):
    fake_webrtc = _FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)
    monkeypatch.setenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED", "true")
    monkeypatch.setenv(
        "AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH", "/rewarded/autoyou-admin"
    )

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/webrtc/rewarded-ad",
            headers={
                "Authorization": "Bearer synthetic-internal-token",
                "Origin": "http://testserver",
                "Referer": "http://testserver/admin",
            },
            json={
                "owner_key": "cloud:client-device-abc",
                "payload": {"expected_watch_seconds": 30},
            },
        )

    assert response.status_code == 403
    payload = response.json()
    assert payload["success"] is False
    assert payload["triggered_count"] == 0
    assert "disabled" in payload["reason"]
    assert fake_webrtc.calls == []


def test_webrtc_rewarded_ad_single_live_fallback_sends_control(monkeypatch):
    class FakeDataChannelManager:
        def __init__(self):
            self.sent = []
            self.last_ping_time = time.time()

        async def send_message(self, message):
            self.sent.append(message)
            return True

    manager = FakeDataChannelManager()
    webrtc = server.WebRTCManager()
    webrtc.datachannel_managers["ios-session-001"] = manager
    monkeypatch.setattr(
        webrtc,
        "_resolve_chat_identity",
        lambda session_id: SimpleNamespace(owner_key="cloud:ios-device-001"),
    )

    import asyncio

    success, status = asyncio.run(
        webrtc.send_rewarded_ad_control_to_reply_target(
            {"transport": "webrtc"},
            {
                "expected_watch_seconds": 45,
                "event": "malicious_event",
                "ad_units": {"ios_rewarded": "ca-app-pub-0000000000000000/1111111111"},
                "ad_unit_id": "ca-app-pub-0000000000000000/3333333333",
                "reward_intent_endpoint": "https://attacker.example/reward",
            },
            allow_single_live_fallback=True,
        )
    )

    assert success is True
    assert status["resolution"] == "single_live_datachannel"
    assert status["target_session_id"] == "ios-session-001"
    assert manager.sent[0].header.message_type.value == "voice_call_control"
    assert manager.sent[0].payload["event"] == "show_rewarded_ad"
    assert manager.sent[0].payload["session_id"] == "ios-session-001"
    assert manager.sent[0].payload["expected_watch_seconds"] == 45
    assert manager.sent[0].payload["native_ad_unit_source"] == "client_baked_autoyou_build"
    assert manager.sent[0].payload["desktop_web_ad_config_source"] == "signed_client_baked_release_env"
    assert manager.sent[0].payload["ad_unit_ids_in_payload"] is False
    assert "ad_units" not in manager.sent[0].payload
    assert "ad_unit_id" not in manager.sent[0].payload
    assert "reward_intent_endpoint" not in manager.sent[0].payload

    proof = webrtc.rewarded_ad_connection_proof(
        session_id="ios-session-001",
        owner_key="cloud:ios-device-001",
    )
    assert proof["connected"] is True
    assert proof["resolution"] == "reply_target"
    assert proof["heartbeat_recent"] is True
    assert proof["heartbeat_age_seconds"] >= 0
    serialized_proof = json.dumps(proof)
    assert "ios-session-001" not in serialized_proof
    assert "cloud:ios-device-001" not in serialized_proof


def test_webrtc_rewarded_ad_completion_records_sanitized_local_preview():
    import asyncio

    webrtc = server.WebRTCManager()
    message = server.create_voice_call_control_message(
        payload={
            "event": "rewarded_ad_completed",
            "platform": "ios",
            "timestamp_ms": 123456789,
            "control_id": "rewarded-ad-control-001",
            "source": "ads_watching_agent",
            "watched_seconds": 37.25,
            "transaction_id": "txn_should_not_persist",
            "ad_unit": "ca-app-pub-0000000000000000/1111111111",
        },
        session_id="ios-session-001",
        user_id="AutoYou-ios",
    )

    asyncio.run(webrtc._handle_voice_call_control_message(message))

    stored = webrtc.rewarded_ad_completion_by_session["ios-session-001"]
    assert stored["event"] == "rewarded_ad_completed"
    assert stored["platform"] == "ios"
    assert stored["control_id"] == "rewarded-ad-control-001"
    assert stored["watched_seconds"] == 37.25
    serialized = str(stored)
    assert "txn_should_not_persist" not in serialized
    assert "ca-app-pub" not in serialized

    latest = webrtc.latest_rewarded_ad_completion("ios-session-001")
    assert latest is not None
    assert latest["event"] == "rewarded_ad_completed"
    assert latest["platform"] == "ios"
    assert latest["watched_seconds"] == 37.25
    assert "txn_should_not_persist" not in str(latest)
    assert "ca-app-pub" not in str(latest)

    webrtc.rewarded_ad_completion_by_session["ios-session-bad"] = {
        "event": "rewarded_ad_completed",
        "timestamp_ms": "not-a-timestamp",
        "watched_seconds": "nan",
    }
    bad_latest = webrtc.latest_rewarded_ad_completion("ios-session-bad")
    assert bad_latest is not None
    assert bad_latest["timestamp_ms"] == 0
    assert bad_latest["watched_seconds"] == 0.0


def test_webrtc_internal_headers_are_not_forwarded_from_source():
    webrtc = server.WebRTCManager()
    source_headers = {
        "X-AutoYou-WebRTC-Owner-Key": "cloud:must-not-forward",
        "Accept": "application/json",
    }

    ads_headers = webrtc._agent_frontend_context_headers(
        "/agent/ads_watching_agent/api/status",
        "http://127.0.0.1:8067/agent/ads_watching_agent/api/status",
        "ios-session-001",
        source_headers=source_headers,
    )
    assert ads_headers.get("X-AutoYou-WebRTC-Owner-Key") != "cloud:must-not-forward"

    other_headers = webrtc._agent_frontend_context_headers(
        "/agent/notes_agent/api/status",
        "http://127.0.0.1:8067/agent/notes_agent/api/status",
        "ios-session-001",
        source_headers=source_headers,
    )
    assert other_headers.get("X-AutoYou-WebRTC-Owner-Key") != "cloud:must-not-forward"


def test_webrtc_rewarded_ad_single_live_fallback_rejects_owner_mismatch(monkeypatch):
    class FakeDataChannelManager:
        async def send_message(self, message):
            raise AssertionError("owner mismatch must not send a rewarded ad control")

    webrtc = server.WebRTCManager()
    webrtc.datachannel_managers["ios-session-001"] = FakeDataChannelManager()
    monkeypatch.setattr(
        webrtc,
        "_resolve_chat_identity",
        lambda session_id: SimpleNamespace(owner_key="cloud:ios-device-001"),
    )

    import asyncio

    success, status = asyncio.run(
        webrtc.send_rewarded_ad_control_to_reply_target(
            {"transport": "webrtc", "owner_key": "telegram:5550001001"},
            {"expected_watch_seconds": 30},
            allow_single_live_fallback=True,
        )
    )

    assert success is False
    assert status["resolution"] == "owner_mismatch"
    assert status["owner_key"] == "cloud:ios-device-001"
    assert status["triggered_count"] == 0
    assert "does not match" in status["reason"]


def test_admin_webrtc_send_accepts_internal_ai_agent_token_and_media_context(monkeypatch):
    fake_webrtc = _FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)

    image_bytes = b"\x89PNG\r\n\x1a\nsynthetic webrtc image"
    media_context = [
        {
            "source": "media_generation_agent",
            "attachments": [
                {
                    "filename": "synthetic-webrtc-result.png",
                    "mimetype": "image/png",
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                    "size_bytes": len(image_bytes),
                }
            ],
        }
    ]

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/webrtc/send",
            headers={
                "Authorization": "Bearer internal-ai-token",
                "Origin": "http://testserver",
                "Referer": "http://testserver/admin",
            },
            json={
                "owner_key": "cloud:client-device-abc",
                "session_id": "relay-123",
                "message": "Your image is ready.",
                "context": media_context,
            },
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert fake_webrtc.calls == [
        {
            "reply_target": {
                "transport": "webrtc",
                "owner_key": "cloud:client-device-abc",
                "session_id": "relay-123",
            },
            "message": "Your image is ready.",
            "metadata": {"source": "internal_ai_agent"},
            "user_id": server.get_configured_server_name(),
            "context": media_context,
        }
    ]


def test_admin_webrtc_playback_routes_accept_bearer_token(monkeypatch):
    original_tokens = dict(server.ADMIN_API_TOKENS)
    fake_webrtc = _FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)

    try:
        with TestClient(server.admin_app) as client:
            play_response = client.post(
                "/api/webrtc/playback/play",
                headers=_authorized_headers(),
                json={
                    "owner_key": "cloud:client-device-abc",
                    "session_id": "relay-123",
                    "file_path": "C:/music/demo-track.mp3",
                },
            )
            pause_response = client.post(
                "/api/webrtc/playback/pause",
                headers=_authorized_headers(),
                json={"owner_key": "cloud:client-device-abc"},
            )
            resume_response = client.post(
                "/api/webrtc/playback/resume",
                headers=_authorized_headers(),
                json={"session_id": "relay-123"},
            )
            stop_response = client.post(
                "/api/webrtc/playback/stop",
                headers=_authorized_headers(),
                json={"session_id": "relay-123"},
            )
            status_response = client.post(
                "/api/webrtc/playback/status",
                headers=_authorized_headers(),
                json={"owner_key": "cloud:client-device-abc"},
            )

        assert play_response.status_code == 200
        assert play_response.json()["status"]["state"] == "playing"
        assert pause_response.status_code == 200
        assert pause_response.json()["status"]["state"] == "paused"
        assert resume_response.status_code == 200
        assert resume_response.json()["status"]["state"] == "playing"
        assert stop_response.status_code == 200
        assert stop_response.json()["status"]["state"] == "stopped"
        assert status_response.status_code == 200
        assert status_response.json()["status"]["state"] == "paused"
        assert fake_webrtc.calls == [
            {
                "action": "play",
                "reply_target": {
                    "transport": "webrtc",
                    "owner_key": "cloud:client-device-abc",
                    "session_id": "relay-123",
                },
                "file_path": "C:/music/demo-track.mp3",
            },
            {
                "action": "pause",
                "reply_target": {
                    "transport": "webrtc",
                    "owner_key": "cloud:client-device-abc",
                },
            },
            {
                "action": "resume",
                "reply_target": {
                    "transport": "webrtc",
                    "session_id": "relay-123",
                },
            },
            {
                "action": "stop",
                "reply_target": {
                    "transport": "webrtc",
                    "session_id": "relay-123",
                },
            },
            {
                "action": "status",
                "reply_target": {
                    "transport": "webrtc",
                    "owner_key": "cloud:client-device-abc",
                },
            },
        ]
    finally:
        server.ADMIN_API_TOKENS.clear()
        server.ADMIN_API_TOKENS.update(original_tokens)


def test_admin_webrtc_playback_enabled_get_accepts_internal_ai_token(monkeypatch):
    monkeypatch.setattr(server, "_is_logged_in", lambda request: False)
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)
    monkeypatch.setattr(server, "_get_audio_playback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(server, "_resolve_audio_playback_music_library_dirs", lambda cfg=None: ["C:/music"])
    monkeypatch.setattr(server, "_is_audio_agent_installed", lambda: True)

    with TestClient(server.admin_app) as client:
        response = client.get(
            "/api/webrtc/playback/enabled",
            headers={"Authorization": "Bearer internal-token"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "enabled": True,
        "installed": True,
        "music_library_dirs": ["C:/music"],
    }


def test_request_uses_ai_agent_internal_token_accepts_loopback_bearer(monkeypatch):
    monkeypatch.setenv(server.AI_AGENT_INTERNAL_API_TOKEN_ENV, "internal-ai-token")

    request = SimpleNamespace(
        headers={"Authorization": "Bearer internal-ai-token"},
        client=SimpleNamespace(host="127.0.0.1"),
    )

    assert server._request_uses_ai_agent_internal_token(request) is True


def test_admin_webrtc_playback_routes_accept_internal_ai_agent_token(monkeypatch):
    fake_webrtc = _FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/webrtc/playback/play",
            headers={
                "Authorization": "Bearer internal-ai-token",
                "Origin": "http://testserver",
                "Referer": "http://testserver/admin",
            },
            json={
                "owner_key": "cloud:client-device-abc",
                "session_id": "relay-123",
                "file_path": "C:/music/demo-track.mp3",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"]["state"] == "playing"
    assert fake_webrtc.calls == [
        {
            "action": "play",
            "reply_target": {
                "transport": "webrtc",
                "owner_key": "cloud:client-device-abc",
                "session_id": "relay-123",
            },
            "file_path": "C:/music/demo-track.mp3",
        }
    ]
