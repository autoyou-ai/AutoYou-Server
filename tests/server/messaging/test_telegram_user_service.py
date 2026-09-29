# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-16d82a019726089c6840fba0

"""Synthetic contract coverage for the owner-only Telegram Saved Messages adapter."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import telegram_user_service as telegram_user_module
from autoyou_agents.build_prompt_agent import build_prompt_tool
from telegram_user_service import TelegramUserService

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-16d82a019726089c6840fba0"


class _FakeSession:
    def save(self):
        return "synthetic-session-value"


class _FakeQrLogin:
    url = "tg://login?token=synthetic"

    async def wait(self):
        return None


class _FakeClient:
    def __init__(self, *, authorized=True, qr_login=None):
        self.authorized = authorized
        self.qr = qr_login or _FakeQrLogin()
        self.session = _FakeSession()
        self.handlers = []
        self.sent_text = []
        self.sent_files = []
        self.downloaded = b"synthetic-image"
        self.next_id = 700
        self.fail_files = False

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def log_out(self):
        self.authorized = False

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return SimpleNamespace(id=424242)

    def add_event_handler(self, handler, _builder=None):
        self.handlers.append(handler)

    def remove_event_handler(self, handler):
        self.handlers.remove(handler)

    async def qr_login(self):
        return self.qr

    async def sign_in(self, *, password):
        assert password == "synthetic-password"
        self.authorized = True

    async def send_message(self, target, body):
        assert target == "me"
        self.sent_text.append(body)
        self.next_id += 1
        return SimpleNamespace(id=self.next_id)

    async def send_file(self, target, file_value, **kwargs):
        assert target == "me"
        if self.fail_files:
            raise RuntimeError("synthetic temporary delivery failure")
        self.sent_files.append((file_value.read() if hasattr(file_value, "read") else file_value, kwargs))
        self.next_id += 1
        return SimpleNamespace(id=self.next_id)

    async def download_media(self, _message, _target):
        return self.downloaded


def _service(client, *, saved=None):
    return TelegramUserService(
        api_id=12345,
        api_hash="synthetic-api-hash",
        server_name="Synthetic Server",
        training_export_consent=True,
        on_session_saved=(saved.append if saved is not None else None),
        client_factory=lambda **_kwargs: client,
    )


def _message(*, peer_id=424242, sender_id=424242, text="Synthetic message", media=None, forwarded=False):
    file_info = SimpleNamespace(size=len(b"synthetic-image"), name="synthetic.jpg", mime_type="image/jpeg")
    return SimpleNamespace(
        id=100,
        peer_id=SimpleNamespace(user_id=peer_id),
        sender_id=sender_id,
        raw_text=text,
        message=text,
        media=media,
        file=file_info,
        photo=media,
        voice=None,
        audio=None,
        video=None,
        video_note=None,
        fwd_from=SimpleNamespace() if forwarded else None,
        out=True,
    )


@pytest.mark.asyncio
async def test_telegram_user_accepts_only_live_owner_saved_messages(monkeypatch):
    client = _FakeClient()
    saved = []
    service = _service(client, saved=saved)
    captured = []

    async def _capture(message, *, context=None):
        captured.append((message, context))

    async def _no_pairing(**_kwargs):
        return None

    monkeypatch.setattr(service, "_forward_to_chat_api", _capture)
    monkeypatch.setattr(service, "_configured_agent_label", lambda: "Synthetic Server")
    monkeypatch.setattr(telegram_user_module.pairing_router, "process_message", _no_pairing)

    assert await service.start() is True
    assert saved == ["synthetic-session-value"]
    assert service.get_status()["owner_scoped"] is True
    assert "session" not in service.get_status()

    await service._handle_new_message(_message(text="Synthetic Saved Messages request"))
    await service._handle_new_message(_message(peer_id=999, text="Synthetic other chat"))
    await service._handle_new_message(_message(forwarded=True, text="Synthetic forward"))
    await service._handle_new_message(_message(text="Synthetic reply\n~ Synthetic Server"))

    assert captured == [("Synthetic Saved Messages request", [])]
    log = service.get_message_log()
    assert len(log) == 1
    assert log[0]["saved_messages"] is True
    assert log[0]["owner_scoped"] is True
    assert log[0]["forwarded"] is False
    assert log[0]["direction"] == "user"

    async def _pairing_reply(**_kwargs):
        return "synthetic pairing response"

    monkeypatch.setattr(telegram_user_module.pairing_router, "process_message", _pairing_reply)
    await service._handle_new_message(_message(text="/pair synthetic-code"))
    assert len(service.get_message_log()) == 1
    assert client.sent_text[-1] == "synthetic pairing response"

    assert await service.send_message("Synthetic answer") is True
    assert client.sent_text[-1].endswith("~ Synthetic Server")


@pytest.mark.asyncio
async def test_telegram_user_handles_media_without_dialog_enumeration(monkeypatch):
    client = _FakeClient()
    service = _service(client)
    captured = []

    async def _capture(message, *, context=None):
        captured.append((message, context))

    async def _no_pairing(**_kwargs):
        return None

    monkeypatch.setattr(service, "_forward_to_chat_api", _capture)
    monkeypatch.setattr(telegram_user_module.pairing_router, "process_message", _no_pairing)
    assert await service.start() is True

    await service._handle_new_message(_message(text="", media=object()))

    assert captured[0][0] == "[Attachment: synthetic.jpg]"
    attachment = captured[0][1][0]["attachments"][0]
    assert attachment["kind"] == "image"
    assert base64.b64decode(attachment["data"]) == b"synthetic-image"

    media = base64.b64encode(b"synthetic-output").decode("ascii")
    assert await service.send_media_attachments(
        [{"filename": "synthetic.mp3", "mimetype": "audio/mpeg", "data": media, "as_voice": True}]
    ) is True
    assert client.sent_files[-1][1]["voice_note"] is True


@pytest.mark.asyncio
async def test_telegram_user_prompt_builder_keeps_raw_text_and_exit_returns_to_main(monkeypatch):
    client = _FakeClient()
    service = _service(client)
    captured = []
    tool_calls = []

    class _State:
        config = {
            "telegram_user": {
                "prompt_builder": {
                    "enabled": True,
                    "application_agent": "codex_desktop_agent",
                }
            }
        }

    async def _no_pairing(**_kwargs):
        return None

    async def _capture(message, *, context=None):
        captured.append((message, context))

    def _fake_tool(name, payload=None):
        tool_calls.append((name, payload))
        return {
            "success": True,
            "status": "draft",
            "characters": len(str((payload or {}).get("text") or "")),
            "images": len((payload or {}).get("attachments") or []),
            "words": 3,
        }

    monkeypatch.setitem(sys.modules, "server", type("SyntheticServer", (), {"STATE": _State()})())
    monkeypatch.setattr(build_prompt_tool, "run_tool", _fake_tool)
    monkeypatch.setattr(service, "_forward_to_chat_api", _capture)
    monkeypatch.setattr(telegram_user_module.pairing_router, "process_message", _no_pairing)
    assert await service.start() is True

    raw_text = "  what is time ?  "
    await service._handle_new_message(_message(text=raw_text, media=object()))
    assert captured == []
    assert tool_calls[0][0] == "build_prompt"
    assert tool_calls[0][1]["text"] == raw_text
    assert tool_calls[0][1]["attachments"][0]["kind"] == "image"

    for offset, (command, expected_tool) in enumerate(
        (
            ("Send_prompt", "execute_prompt"),
            ("Execute_prompt", "execute_prompt"),
            ("Status prompt", "status_prompt"),
        ),
        start=1,
    ):
        command_message = _message(text=command)
        command_message.id = 100 + offset
        await service._handle_new_message(command_message)
        assert tool_calls[-1][0] == expected_tool
        assert tool_calls[-1][1] == {"application_agent": "codex_desktop_agent"}

    exit_message = _message(text="exit confirm")
    exit_message.id = 104
    await service._handle_new_message(exit_message)
    assert service.get_status()["prompt_builder"]["active"] is False
    assert client.sent_text[-1].startswith("You are back in the main AutoYou agent")

    main_message = _message(text="synthetic main-agent message")
    main_message.id = 105
    await service._handle_new_message(main_message)
    assert captured == [("synthetic main-agent message", [])]


@pytest.mark.asyncio
async def test_telegram_user_qr_two_factor_flow_keeps_tokens_out_of_status(monkeypatch):
    class _SyntheticPasswordNeeded(Exception):
        pass

    class _PasswordQr(_FakeQrLogin):
        async def wait(self):
            raise _SyntheticPasswordNeeded()

    client = _FakeClient(authorized=False, qr_login=_PasswordQr())
    service = _service(client)
    monkeypatch.setattr(telegram_user_module, "SessionPasswordNeededError", _SyntheticPasswordNeeded)

    assert await service.start() is True
    result = await service.begin_qr_login()
    assert result["qr_url"].startswith("data:image/png;base64,")
    await asyncio.sleep(0)
    assert service.get_status()["needs_password"] is True
    assert await service.get_qr_code_data_url() == ""
    assert "tg://" not in str(service.get_status())

    completed = await service.complete_2fa("synthetic-password")
    assert completed["connected"] is True
    assert service.get_status()["training_export_consent"] is True


@pytest.mark.asyncio
async def test_telegram_user_recreates_expired_qr_tokens():
    class _ExpiringQr:
        def __init__(self, client):
            self.client = client
            self.generation = 0
            # from __debug_provenance_a__ import schedule
            self.url = "tg://login?token=synthetic-0"

        async def wait(self):
            # The first two tokens expire like Telegram's ~30 second QR codes;
            # the third scan finally authorizes the account.
            if self.generation < 2:
                raise asyncio.TimeoutError()
            self.client.authorized = True
            return None

        async def recreate(self):
            self.generation += 1
            self.url = f"tg://login?token=synthetic-{self.generation}"

    client = _FakeClient(authorized=False)
    expiring = _ExpiringQr(client)
    client.qr = expiring
    service = _service(client)

    assert await service.start() is True
    result = await service.begin_qr_login()
    assert result["success"] is True
    await service._qr_wait_task

    assert expiring.generation == 2
    assert service.get_status()["connected"] is True


@pytest.mark.asyncio
async def test_telegram_user_clears_stale_qr_after_fatal_wait_error():
    class _BrokenQr(_FakeQrLogin):
        async def wait(self):
            raise RuntimeError("synthetic transport drop")

    client = _FakeClient(authorized=False, qr_login=_BrokenQr())
    service = _service(client)

    assert await service.start() is True
    first = await service.begin_qr_login()
    assert first["qr_url"].startswith("data:image/png;base64,")
    await asyncio.sleep(0)

    # The stale token must be forgotten so the next poll starts a new sign-in.
    assert await service.get_qr_code_data_url() == ""
    assert service.get_status()["status"] == "awaiting_qr"

    try:
        second = await service.begin_qr_login()
        assert second["success"] is True
        assert second["qr_url"].startswith("data:image/png;base64,")
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_telegram_user_reuses_live_qr_login_between_polls():
    client = _FakeClient(authorized=False, qr_login=_FakeQrLogin())
    login_calls = {"count": 0}
    original_qr_login = client.qr_login

    async def _counting_qr_login():
        login_calls["count"] += 1
        return await original_qr_login()

    client.qr_login = _counting_qr_login

    class _NeverResolves(_FakeQrLogin):
        async def wait(self):
            await asyncio.sleep(3600)

    client.qr = _NeverResolves()
    service = _service(client)
    assert await service.start() is True
    try:
        first = await service.begin_qr_login()
        second = await service.begin_qr_login()
        assert first["qr_url"] == second["qr_url"]
        assert login_calls["count"] == 1
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_telegram_user_injected_client_keeps_optional_imports_testable(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(telegram_user_module, "TelegramClient", None)
    monkeypatch.setattr(telegram_user_module, "StringSession", None)

    service = _service(client)

    assert await service.start() is True
    assert service.get_status()["connected"] is True


@pytest.mark.asyncio
async def test_telegram_user_persists_media_reply_until_connection_recovers():
    client = _FakeClient()
    service = _service(client)
    assert await service.start() is True
    client.fail_files = True

    assert await service.send_media_attachments(
        [{"filename": "synthetic.jpg", "mimetype": "image/jpeg", "data": base64.b64encode(b"retry-me").decode("ascii")}]
    ) is True
    assert service.get_status()["pending_media_replies"] == 1
    queued_path = service._pending_media_replies[0]["media_path"]
    assert Path(queued_path).is_file()

    client.fail_files = False
    await service._flush_pending_media_replies()
    assert service.get_status()["pending_media_replies"] == 0
    assert not Path(queued_path).exists()
