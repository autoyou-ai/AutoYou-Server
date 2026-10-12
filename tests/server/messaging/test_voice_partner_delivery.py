# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-f68b7413796ca5d035415b7a


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import base64
import json
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-f68b7413796ca5d035415b7a"


ensure_repo_on_path()


@pytest.mark.asyncio
async def test_whatsapp_voice_reply_uses_send_audio_command(tmp_path):
    from whatsapp_service import WhatsAppService

    class FakeWebSocket:
        closed = False
        close_code = None

        def __init__(self, service=None):
            self.service = service
            self.sent = []

        async def send(self, payload):
            command = json.loads(payload)
            self.sent.append(command)
            command_id = ((command.get("data") or {}).get("commandId") or "").strip()
            if self.service is not None and command.get("action") == "send_media" and command_id:
                await self.service._handle_websocket_message(
                    {
                        "event": "command_result",
                        "data": {
                            "action": "send_media",
                            "commandId": command_id,
                            "success": True,
                            "messageId": "synthetic-whatsapp-message",
                        },
                    }
                )

    audio_path = tmp_path / "voice-reply.ogg"
    audio_bytes = b"OggS synthetic whatsapp voice reply"
    audio_path.write_bytes(audio_bytes)

    service = WhatsAppService()
    service.phone_number = "+12125550100"
    # from __debug_provenance_c__ import subtask
    service.last_self_chat_id = "12125550100@c.us"
    service.websocket = FakeWebSocket(service)

    assert await service._send_audio_to_self(str(audio_path)) is True

    command = service.websocket.sent[-1]
    assert command["action"] == "send_audio"
    assert command["data"]["to"] == "+12125550100"
    assert command["data"]["chatId"] == "12125550100@c.us"
    assert command["data"]["asVoice"] is True
    assert command["data"]["mimetype"] == "audio/ogg; codecs=opus"
    assert base64.b64decode(command["data"]["data"]) == audio_bytes


@pytest.mark.asyncio
async def test_whatsapp_media_reply_uses_send_media_command(tmp_path):
    from whatsapp_service import WhatsAppService

    class FakeWebSocket:
        closed = False
        close_code = None

        def __init__(self, service=None):
            self.service = service
            self.sent = []

        async def send(self, payload):
            command = json.loads(payload)
            self.sent.append(command)
            command_id = ((command.get("data") or {}).get("commandId") or "").strip()
            if self.service is not None and command.get("action") == "send_media" and command_id:
                await self.service._handle_websocket_message(
                    {
                        "event": "command_result",
                        "data": {
                            "action": "send_media",
                            "commandId": command_id,
                            "success": True,
                            "messageId": "synthetic-whatsapp-message",
                        },
                    }
                )

    image_path = tmp_path / "synthetic-photo.jpg"
    image_bytes = b"\xff\xd8synthetic whatsapp image\xff\xd9"
    image_path.write_bytes(image_bytes)

    service = WhatsAppService()
    service.phone_number = "+12125550100"
    service.last_self_chat_id = "12125550100@c.us"
    service.websocket = FakeWebSocket(service)

    assert await service._send_media_attachments_to_self(
        [
            {
                "filename": "synthetic-photo.jpg",
                "mimetype": "image/jpeg",
                "path": str(image_path),
                "size_bytes": len(image_bytes),
                "meta": {"kind": "image"},
            }
        ]
    ) is True

    command = service.websocket.sent[-1]
    assert command["action"] == "send_media"
    assert command["data"]["to"] == "+12125550100"
    assert command["data"]["chatId"] == "12125550100@c.us"
    assert command["data"]["mimetype"] == "image/jpeg"
    assert command["data"]["filename"] == "synthetic-photo.jpg"
    assert command["data"]["hd"] is True
    assert base64.b64decode(command["data"]["data"]) == image_bytes


@pytest.mark.asyncio
async def test_whatsapp_media_only_voice_note_is_forwarded_to_chat_api():
    from whatsapp_service import WhatsAppService

    captured = []
    service = WhatsAppService()
    service.phone_number = "12125550100"
    service.last_self_chat_id = "12125550100@c.us"
    service._is_verified_inbound_self_message = lambda _message: True  # type: ignore[assignment]

    async def fake_forward(message_text, context=None, reply_chat_id=None):
        captured.append(
            {
                "message_text": message_text,
                "context": context,
                "reply_chat_id": reply_chat_id,
            }
        )

    service._forward_to_chat_api = fake_forward  # type: ignore[assignment]

    await service._handle_incoming_message(
        {
            "id": "synthetic-whatsapp-voice-1",
            "body": "",
            "hasMedia": True,
            "fromMe": True,
            "selfChat": True,
            "from": "12125550100@c.us",
            "to": "12125550100@c.us",
            "remoteChatId": "12125550100@c.us",
            "media": {
                "filename": "voice-note.ogg",
                "mimetype": "audio/ogg; codecs=opus",
                "data": base64.b64encode(b"OggS synthetic inbound whatsapp voice").decode("ascii"),
                "size": 36,
            },
        }
    )

    assert len(captured) == 1
    assert captured[0]["message_text"] == ""
    assert captured[0]["reply_chat_id"] == "12125550100@c.us"
    attachment = captured[0]["context"][0]["attachments"][0]
    assert attachment["filename"] == "voice-note.ogg"
    assert attachment["mimetype"] == "audio/ogg; codecs=opus"
    assert base64.b64decode(attachment["data"]) == b"OggS synthetic inbound whatsapp voice"


@pytest.mark.asyncio
async def test_whatsapp_voice_reply_queues_when_control_channel_offline(tmp_path):
    from whatsapp_service import WhatsAppService

    class FakeWebSocket:
        closed = False
        close_code = None

        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(json.loads(payload))

    audio_path = tmp_path / "voice-reply.ogg"
    audio_bytes = b"OggS queued whatsapp voice reply"
    audio_path.write_bytes(audio_bytes)

    service = WhatsAppService()
    service.state_dir = tmp_path / "whatsapp"
    service._pending_voice_reply_file = service.state_dir / "pending_voice_replies.json"
    service._pending_voice_replies = service._load_pending_voice_replies()
    service.phone_number = "+12125550100"
    service.last_self_chat_id = "12125550100@c.us"
    service._schedule_control_channel_recovery = lambda _reason: None  # type: ignore[assignment]

    assert await service._send_audio_to_self(str(audio_path)) is True
    assert len(service._pending_voice_replies) == 1
    assert service._pending_voice_reply_file.exists()

    restarted = WhatsAppService()
    restarted.state_dir = service.state_dir
    restarted._pending_voice_reply_file = service._pending_voice_reply_file
    restarted._pending_voice_replies = restarted._load_pending_voice_replies()
    restarted.phone_number = service.phone_number
    restarted.last_self_chat_id = service.last_self_chat_id
    restarted.websocket = FakeWebSocket()
    await restarted._flush_pending_voice_replies()

    assert restarted._pending_voice_replies == []
    assert not restarted._pending_voice_reply_file.exists()
    command = restarted.websocket.sent[-1]
    assert command["action"] == "send_audio"
    assert base64.b64decode(command["data"]["data"]) == audio_bytes


@pytest.mark.asyncio
async def test_whatsapp_media_reply_queues_when_control_channel_offline(tmp_path):
    from whatsapp_service import WhatsAppService

    class FakeWebSocket:
        closed = False
        close_code = None

        def __init__(self, service=None):
            self.service = service
            self.sent = []

        async def send(self, payload):
            command = json.loads(payload)
            self.sent.append(command)
            command_id = ((command.get("data") or {}).get("commandId") or "").strip()
            if self.service is not None and command.get("action") == "send_media" and command_id:
                await self.service._handle_websocket_message(
                    {
                        "event": "command_result",
                        "data": {
                            "action": "send_media",
                            "commandId": command_id,
                            "success": True,
                            "messageId": "synthetic-whatsapp-message",
                        },
                    }
                )

    video_path = tmp_path / "synthetic-video.mp4"
    video_bytes = b"\x00\x00\x00\x18ftypmp42queued whatsapp video"
    video_path.write_bytes(video_bytes)

    service = WhatsAppService()
    service.state_dir = tmp_path / "whatsapp"
    service._pending_media_reply_file = service.state_dir / "pending_media_replies.json"
    service._pending_media_replies = service._load_pending_media_replies()
    service.phone_number = "+12125550100"
    service.last_self_chat_id = "12125550100@c.us"
    service._schedule_control_channel_recovery = lambda _reason: None  # type: ignore[assignment]

    assert await service._send_media_attachments_to_self(
        [
            {
                "filename": "synthetic-video.mp4",
                "mimetype": "video/mp4",
                "path": str(video_path),
                "size_bytes": len(video_bytes),
                "meta": {"kind": "video"},
            }
        ]
    ) is True
    assert len(service._pending_media_replies) == 1
    assert service._pending_media_reply_file.exists()
    queued_json = json.loads(service._pending_media_reply_file.read_text(encoding="utf-8"))
    queued_command = queued_json[0]["command_data"]
    assert "data" not in queued_command
    assert queued_command["media_path"]

    restarted = WhatsAppService()
    restarted.state_dir = service.state_dir
    restarted._pending_media_reply_file = service._pending_media_reply_file
    restarted._pending_media_replies = restarted._load_pending_media_replies()
    restarted.phone_number = service.phone_number
    restarted.last_self_chat_id = service.last_self_chat_id
    restarted.websocket = FakeWebSocket(restarted)
    await restarted._flush_pending_media_replies()

    assert restarted._pending_media_replies == []
    assert not restarted._pending_media_reply_file.exists()
    command = restarted.websocket.sent[-1]
    assert command["action"] == "send_media"
    assert command["data"]["mimetype"] == "video/mp4"
    assert base64.b64decode(command["data"]["data"]) == video_bytes


@pytest.mark.asyncio
async def test_telegram_voice_reply_uses_send_voice(tmp_path, monkeypatch):
    import server

    class FakeTelegramBot:
        def __init__(self):
            self.calls = []

        async def send_voice(self, **kwargs):
            self.calls.append(
                {
                    **kwargs,
                    "voice_bytes": kwargs["voice"].read(),
                }
            )

    audio_path = tmp_path / "voice-reply.ogg"
    audio_bytes = b"OggS synthetic telegram voice reply"
    audio_path.write_bytes(audio_bytes)
    bot = FakeTelegramBot()
    session_id = "session::telegram:5550001001"
    original_bindings = dict(getattr(server.STATE, "telegram_chat_bindings", {}) or {})

    try:
        server.STATE.telegram_chat_bindings = {
            session_id: {
                "chat_id": 5550001001,
                "reply_to_message_id": 42,
            }
        }
        monkeypatch.setattr(server, "_get_active_telegram_bot", lambda: bot)

        assert await server._send_telegram_voice_to_session(session_id, str(audio_path)) is True

        call = bot.calls[-1]
        assert call["chat_id"] == 5550001001
        assert call["reply_to_message_id"] == 42
        assert call["voice_bytes"] == audio_bytes
    finally:
        server.STATE.telegram_chat_bindings = original_bindings


@pytest.mark.asyncio
async def test_telegram_voice_reply_queues_when_bot_unavailable(tmp_path, monkeypatch):
    import server

    class FakeTelegramBot:
        def __init__(self):
            self.calls = []

        async def send_voice(self, **kwargs):
            self.calls.append(
                {
                    **kwargs,
                    "voice_bytes": kwargs["voice"].read(),
                }
            )

    audio_path = tmp_path / "voice-reply.ogg"
    audio_bytes = b"OggS queued telegram voice reply"
    audio_path.write_bytes(audio_bytes)
    bot = FakeTelegramBot()
    session_id = "session::telegram:5550001002"
    original_bindings = dict(getattr(server.STATE, "telegram_chat_bindings", {}) or {})
    original_pending = list(getattr(server.STATE, "telegram_pending_voice_replies", []) or [])
    pending_path = tmp_path / "telegram" / "pending_voice_replies.json"

    try:
        monkeypatch.setattr(server, "_telegram_pending_voice_reply_path", lambda: pending_path)
        server.STATE.telegram_chat_bindings = {
            session_id: {
                "chat_id": 5550001002,
                "reply_to_message_id": 43,
            }
        }
        server.STATE.telegram_pending_voice_replies = []
        monkeypatch.setattr(server, "_get_active_telegram_bot", lambda: None)

        assert await server._send_telegram_voice_to_session(session_id, str(audio_path)) is True
        assert len(server.STATE.telegram_pending_voice_replies) == 1
        assert pending_path.exists()

        server.STATE.telegram_pending_voice_replies = None
        monkeypatch.setattr(server, "_get_active_telegram_bot", lambda: bot)
        await server._flush_telegram_pending_voice_replies()

        assert server.STATE.telegram_pending_voice_replies == []
        assert not pending_path.exists()
        call = bot.calls[-1]
        assert call["chat_id"] == 5550001002
        assert call["reply_to_message_id"] == 43
        assert call["voice_bytes"] == audio_bytes
    finally:
        server.STATE.telegram_chat_bindings = original_bindings
        server.STATE.telegram_pending_voice_replies = original_pending


@pytest.mark.asyncio
async def test_telegram_media_reply_uses_photo_and_video(tmp_path, monkeypatch):
    import server

    class FakeTelegramBot:
        def __init__(self):
            self.calls = []

        async def send_photo(self, **kwargs):
            self.calls.append({"type": "photo", **kwargs, "media_bytes": kwargs["photo"].read()})

        async def send_video(self, **kwargs):
            self.calls.append({"type": "video", **kwargs, "media_bytes": kwargs["video"].read()})

    image_path = tmp_path / "synthetic-photo.jpg"
    video_path = tmp_path / "synthetic-video.mp4"
    image_bytes = b"\xff\xd8synthetic telegram image\xff\xd9"
    video_bytes = b"\x00\x00\x00\x18ftypmp42synthetic telegram video"
    image_path.write_bytes(image_bytes)
    video_path.write_bytes(video_bytes)
    bot = FakeTelegramBot()
    session_id = "session::telegram:5550001003"
    original_bindings = dict(getattr(server.STATE, "telegram_chat_bindings", {}) or {})

    try:
        server.STATE.telegram_chat_bindings = {
            session_id: {
                "chat_id": 5550001003,
                "reply_to_message_id": 44,
            }
        }
        monkeypatch.setattr(server, "_get_active_telegram_bot", lambda: bot)

        assert await server._send_telegram_media_attachments_to_session(
            session_id,
            [
                {"filename": "synthetic-photo.jpg", "mimetype": "image/jpeg", "path": str(image_path)},
                {"filename": "synthetic-video.mp4", "mimetype": "video/mp4", "path": str(video_path)},
            ],
        ) is True

        assert bot.calls[0]["type"] == "photo"
        assert bot.calls[0]["chat_id"] == 5550001003
        assert bot.calls[0]["reply_to_message_id"] == 44
        assert bot.calls[0]["media_bytes"] == image_bytes
        assert bot.calls[1]["type"] == "video"
        assert bot.calls[1]["supports_streaming"] is True
        assert bot.calls[1]["media_bytes"] == video_bytes
    finally:
        server.STATE.telegram_chat_bindings = original_bindings


@pytest.mark.asyncio
async def test_telegram_media_reply_queues_when_bot_unavailable(tmp_path, monkeypatch):
    import server

    class FakeTelegramBot:
        def __init__(self):
            self.calls = []

        async def send_video(self, **kwargs):
            self.calls.append({"type": "video", **kwargs, "media_bytes": kwargs["video"].read()})

    video_path = tmp_path / "synthetic-video.mp4"
    video_bytes = b"\x00\x00\x00\x18ftypmp42queued telegram video"
    video_path.write_bytes(video_bytes)
    bot = FakeTelegramBot()
    session_id = "session::telegram:5550001004"
    original_bindings = dict(getattr(server.STATE, "telegram_chat_bindings", {}) or {})
    original_pending = list(getattr(server.STATE, "telegram_pending_media_replies", []) or [])
    pending_path = tmp_path / "telegram" / "pending_media_replies.json"

    try:
        monkeypatch.setattr(server, "_telegram_pending_media_reply_path", lambda: pending_path)
        server.STATE.telegram_chat_bindings = {
            session_id: {
                "chat_id": 5550001004,
                "reply_to_message_id": 45,
            }
        }
        server.STATE.telegram_pending_media_replies = []
        monkeypatch.setattr(server, "_get_active_telegram_bot", lambda: None)

        assert await server._send_telegram_media_attachments_to_session(
            session_id,
            [{"filename": "synthetic-video.mp4", "mimetype": "video/mp4", "path": str(video_path)}],
        ) is True
        assert len(server.STATE.telegram_pending_media_replies) == 1
        assert pending_path.exists()
        queued_json = json.loads(pending_path.read_text(encoding="utf-8"))
        assert "data" not in queued_json[0]
        assert queued_json[0]["media_path"]

        server.STATE.telegram_pending_media_replies = None
        monkeypatch.setattr(server, "_get_active_telegram_bot", lambda: bot)
        await server._flush_telegram_pending_media_replies()

        assert server.STATE.telegram_pending_media_replies == []
        assert not pending_path.exists()
        call = bot.calls[-1]
        assert call["type"] == "video"
        assert call["chat_id"] == 5550001004
        assert call["reply_to_message_id"] == 45
        assert call["media_bytes"] == video_bytes
    finally:
        server.STATE.telegram_chat_bindings = original_bindings
        server.STATE.telegram_pending_media_replies = original_pending


@pytest.mark.asyncio
async def test_signal_voice_reply_posts_base64_attachment(tmp_path, monkeypatch):
    import signal_service
    from signal_service import SignalService

    class FakeResponse:
        status_code = 201
        text = "created"

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            captured.append({"url": url, "json": json})
            return FakeResponse()

    captured = []
    audio_path = tmp_path / "voice-reply.ogg"
    audio_bytes = b"OggS synthetic signal voice reply"
    audio_path.write_bytes(audio_bytes)
    monkeypatch.setattr(signal_service, "httpx", SimpleNamespace(AsyncClient=FakeAsyncClient))

    service = SignalService(port=18082, device_name="test-signal")

    assert await service.send_audio("+12125550100", str(audio_path)) is True

    request = captured[-1]
    assert request["url"] == "http://127.0.0.1:18082/v2/send"
    payload = request["json"]
    assert payload["number"] == "+12125550100"
    assert payload["recipients"] == ["+12125550100"]
    assert payload["message"] == ""
    assert len(payload["base64_attachments"]) == 1
    prefix, encoded = payload["base64_attachments"][0].split(",", 1)
    assert prefix == "data:audio/ogg;filename=voice-reply.ogg;base64"
    assert base64.b64decode(encoded) == audio_bytes


@pytest.mark.asyncio
async def test_signal_media_reply_posts_base64_attachment(tmp_path, monkeypatch):
    import signal_service
    from signal_service import SignalService

    class FakeResponse:
        status_code = 201
        text = "created"

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            captured.append({"url": url, "json": json})
            return FakeResponse()

    captured = []
    image_path = tmp_path / "synthetic-photo.jpg"
    image_bytes = b"\xff\xd8synthetic signal image\xff\xd9"
    image_path.write_bytes(image_bytes)
    monkeypatch.setattr(signal_service, "httpx", SimpleNamespace(AsyncClient=FakeAsyncClient))

    service = SignalService(port=18082, device_name="test-signal")

    assert await service.send_media_attachments(
        "+12125550100",
        [{"filename": "synthetic-photo.jpg", "mimetype": "image/jpeg", "path": str(image_path)}],
    ) is True

    request = captured[-1]
    assert request["url"] == "http://127.0.0.1:18082/v2/send"
    payload = request["json"]
    assert payload["number"] == "+12125550100"
    assert payload["recipients"] == ["+12125550100"]
    assert payload["message"] == ""
    prefix, encoded = payload["base64_attachments"][0].split(",", 1)
    assert prefix == "data:image/jpeg;filename=synthetic-photo.jpg;base64"
    assert base64.b64decode(encoded) == image_bytes


@pytest.mark.asyncio
async def test_signal_voice_reply_queues_after_http_failure(tmp_path, monkeypatch):
    import signal_service
    from signal_service import SignalService

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code
            self.text = "synthetic"

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            captured.append({"url": url, "json": json})
            return FakeResponse(503 if len(captured) == 1 else 201)

    captured = []
    audio_path = tmp_path / "voice-reply.ogg"
    audio_bytes = b"OggS queued signal voice reply"
    audio_path.write_bytes(audio_bytes)
    monkeypatch.setattr(signal_service, "httpx", SimpleNamespace(AsyncClient=FakeAsyncClient))

    service = SignalService(port=18082, device_name="test-signal")
    service.data_dir = str(tmp_path / "signal_data")
    service._pending_voice_replies = service._load_pending_voice_replies()

    assert await service.send_audio("+12125550100", str(audio_path)) is True
    assert len(service._pending_voice_replies) == 1
    assert (tmp_path / "signal_data" / "pending_voice_replies.json").exists()

    restarted = SignalService(port=18082, device_name="test-signal")
    restarted.data_dir = service.data_dir
    restarted._pending_voice_replies = restarted._load_pending_voice_replies()
    await restarted._flush_pending_voice_replies()

    assert restarted._pending_voice_replies == []
    assert not (tmp_path / "signal_data" / "pending_voice_replies.json").exists()
    assert len(captured) == 2
    prefix, encoded = captured[-1]["json"]["base64_attachments"][0].split(",", 1)
    assert prefix == "data:audio/ogg;filename=voice-reply.ogg;base64"
    assert base64.b64decode(encoded) == audio_bytes


@pytest.mark.asyncio
async def test_signal_media_reply_queues_after_http_failure(tmp_path, monkeypatch):
    import signal_service
    from signal_service import SignalService

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code
            self.text = "synthetic"

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            captured.append({"url": url, "json": json})
            return FakeResponse(503 if len(captured) == 1 else 201)

    captured = []
    video_path = tmp_path / "synthetic-video.mp4"
    video_bytes = b"\x00\x00\x00\x18ftypmp42queued signal video"
    video_path.write_bytes(video_bytes)
    monkeypatch.setattr(signal_service, "httpx", SimpleNamespace(AsyncClient=FakeAsyncClient))

    service = SignalService(port=18082, device_name="test-signal")
    service.data_dir = str(tmp_path / "signal_data")
    service._pending_media_replies = service._load_pending_media_replies()

    assert await service.send_media_attachments(
        "+12125550100",
        [{"filename": "synthetic-video.mp4", "mimetype": "video/mp4", "path": str(video_path)}],
    ) is True
    assert len(service._pending_media_replies) == 1
    assert (tmp_path / "signal_data" / "pending_media_replies.json").exists()
    queued_json = json.loads((tmp_path / "signal_data" / "pending_media_replies.json").read_text(encoding="utf-8"))
    assert "base64_attachments" not in queued_json[0]["payload"]
    assert queued_json[0]["payload"]["queued_attachments"][0]["media_path"]

    restarted = SignalService(port=18082, device_name="test-signal")
    restarted.data_dir = service.data_dir
    restarted._pending_media_replies = restarted._load_pending_media_replies()
    await restarted._flush_pending_media_replies()

    assert restarted._pending_media_replies == []
    assert not (tmp_path / "signal_data" / "pending_media_replies.json").exists()
    assert len(captured) == 2
    prefix, encoded = captured[-1]["json"]["base64_attachments"][0].split(",", 1)
    assert prefix == "data:video/mp4;filename=synthetic-video.mp4;base64"
    assert base64.b64decode(encoded) == video_bytes
