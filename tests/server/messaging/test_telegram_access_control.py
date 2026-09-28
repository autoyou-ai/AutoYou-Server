# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import pairing_router as pairing_router_module
import server
from pairing_router import PairingRouter
from shared.pairing_cpace import (
    build_cpace_hello,
    complete_cpace_handshake,
    decrypt_cpace_message,
    encrypt_cpace_message,
)


@pytest.fixture(autouse=True)
def _isolate_config_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_FILE_PATH", str(tmp_path / "config.encrypted"))
    monkeypatch.setattr(server, "CONFIG_BAK_PATH", str(tmp_path / "config.encrypted.bak"))
    monkeypatch.setattr(server, "_get_server_keystore", lambda: None)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: False)


def _auth_client():
    client = TestClient(server.admin_app)
    server.ADMIN_SESSIONS["test-admin-session"] = True
    client.cookies.set("admin_session", "test-admin-session")
    client.headers.update(
        {
            "Origin": "http://testserver",
            "Referer": "http://testserver/admin",
        }
    )
    return client


def _capture_server_state():
    return {
        "config": server.STATE.config,
        "tokens": dict(server.STATE.telegram_allow_tokens),
        "seen_senders": dict(getattr(server.STATE, "telegram_seen_senders", {}) or {}),
        "chat_bindings": dict(getattr(server.STATE, "telegram_chat_bindings", {}) or {}),
        "pending_media_replies": list(getattr(server.STATE, "telegram_pending_media_replies", []) or []),
        "server_password": server.STATE.server_password,
        "config_unlock_password": server.STATE.config_unlock_password,
        "config_store": server.STATE.config_store,
        "admin_sessions": dict(server.ADMIN_SESSIONS),
    }


def _restore_server_state(snapshot):
    server.STATE.config = snapshot["config"]
    server.STATE.telegram_allow_tokens = snapshot["tokens"]
    server.STATE.telegram_seen_senders = snapshot["seen_senders"]
    server.STATE.telegram_chat_bindings = snapshot["chat_bindings"]
    server.STATE.telegram_pending_media_replies = snapshot["pending_media_replies"]
    server._set_config_session(
        config_store=snapshot["config_store"],
        server_password=snapshot["server_password"],
        config_unlock_password=snapshot["config_unlock_password"],
    )
    server.ADMIN_SESSIONS.clear()
    server.ADMIN_SESSIONS.update(snapshot["admin_sessions"])


@pytest.mark.asyncio
async def test_log_any_text_uses_typing_not_wait_message(monkeypatch):
    snapshot = _capture_server_state()
    captured = {"typing_calls": 0, "replies": [], "scheduled": None}

    identity = SimpleNamespace(
        canonical_session_id="session::telegram:5550001001",
        canonical_user_id="user::telegram:5550001001",
        owner_key="telegram:5550001001",
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username="owner"),
        effective_chat=SimpleNamespace(id=5550001001),
        message=SimpleNamespace(text="hello", message_id=99, chat_id=5550001001),
    )

    async def fake_authorized(_update):
        return True

    async def fake_typing(_update):
        captured["typing_calls"] += 1

    async def fake_reply(_update, text, *, split_text=True):
        captured["replies"].append((text, split_text))

    def fake_schedule(**kwargs):
        captured["scheduled"] = kwargs

    try:
        monkeypatch.setattr(server, "_ensure_telegram_update_authorized", fake_authorized)
        monkeypatch.setattr(server, "_resolve_telegram_execution_identity", lambda _update: (identity, "telegram_5550001001", 5550001001))
        monkeypatch.setattr(server, "_remember_telegram_chat_binding", lambda *args, **kwargs: None)
        monkeypatch.setattr(server, "_extract_conversation_request", lambda text, metadata: (False, text, False, None))
        monkeypatch.setattr(server, "_send_telegram_typing_for_update", fake_typing)
        monkeypatch.setattr(server, "_reply_telegram_text", fake_reply)
        monkeypatch.setattr(server, "_schedule_telegram_chat_delivery", fake_schedule)

        await server.log_any_text(update, SimpleNamespace())

        assert captured["typing_calls"] == 1
        assert captured["replies"] == []
        assert captured["scheduled"] is not None
        assert captured["scheduled"]["message"] == "hello"
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_log_video_reports_telegram_cloud_download_limit(monkeypatch):
    snapshot = _capture_server_state()
    captured = {"typing_calls": 0, "replies": [], "scheduled": None, "get_file_called": False}

    identity = SimpleNamespace(
        canonical_session_id="session::telegram:5550001001",
        canonical_user_id="user::telegram:5550001001",
        owner_key="telegram:5550001001",
    )
    video = SimpleNamespace(
        file_id="synthetic-video-file-id",
        file_name="synthetic-video.mp4",
        mime_type="video/mp4",
        file_size=server.TELEGRAM_BOT_API_GET_FILE_DOWNLOAD_LIMIT_BYTES + 1,
        width=1280,
        height=720,
        duration=3,
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username="owner"),
        effective_chat=SimpleNamespace(id=5550001001),
        message=SimpleNamespace(
            video=video,
            caption="synthetic caption",
            message_id=101,
            chat_id=5550001001,
        ),
    )

    async def fake_authorized(_update):
        return True

    async def fake_typing(_update):
        captured["typing_calls"] += 1

    async def fake_reply(_update, text, *, split_text=True):
        captured["replies"].append((text, split_text))

    def fake_schedule(**kwargs):
        captured["scheduled"] = kwargs

    async def fake_get_file(_file_id):
        captured["get_file_called"] = True
        raise AssertionError("large Telegram cloud video should not call get_file")

    try:
        monkeypatch.setattr(server, "_ensure_telegram_update_authorized", fake_authorized)
        monkeypatch.setattr(server, "_resolve_telegram_execution_identity", lambda _update: (identity, "telegram_5550001001", 5550001001))
        monkeypatch.setattr(server, "_remember_telegram_chat_binding", lambda *args, **kwargs: None)
        monkeypatch.setattr(server, "_send_telegram_typing_for_update", fake_typing)
        monkeypatch.setattr(server, "_reply_telegram_text", fake_reply)
        monkeypatch.setattr(server, "_schedule_telegram_chat_delivery", fake_schedule)

        await server.log_video(update, SimpleNamespace(bot=SimpleNamespace(get_file=fake_get_file)))

        assert captured["typing_calls"] == 1
        assert captured["get_file_called"] is False
        assert captured["scheduled"]["message"].startswith("synthetic caption")
        assert "20 MB" in captured["scheduled"]["message"]
        assert captured["scheduled"]["metadata"]["telegram_media_unavailable"] is True
        assert "20 MB" in captured["replies"][0][0]
        assert captured["replies"][0][1] is False
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_log_any_text_replies_to_completed_autopair_fragment(monkeypatch):
    snapshot = _capture_server_state()
    original_pairing_router = server.pairing_router
    replies = []
    scheduled = []
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username="owner"),
        effective_chat=SimpleNamespace(id=5550001001),
        message=SimpleNamespace(text="payload-continuation", message_id=100, chat_id=5550001001),
    )

    class FakePairingRouter:
        FRAGMENT_CONSUMED = "__fragment__"

        def has_pending_autopair_buffer(self, platform, sender_id):
            assert (platform, sender_id) == ("telegram", "5550001001")
            return True

        async def process_message(self, text, platform, sender_id):
            assert text == "payload-continuation"
            assert (platform, sender_id) == ("telegram", "5550001001")
            return "/autopair_answer\n{}"

    async def fake_authorized(_update):
        return True

    async def fake_reply(_update, text, *, split_text=True):
        replies.append((text, split_text))

    def fake_schedule(**kwargs):
        scheduled.append(kwargs)

    try:
        monkeypatch.setattr(server, "_ensure_telegram_update_authorized", fake_authorized)
        monkeypatch.setattr(server, "_reply_telegram_text", fake_reply)
        monkeypatch.setattr(server, "_schedule_telegram_chat_delivery", fake_schedule)
        monkeypatch.setattr(server, "pairing_router", FakePairingRouter())

        await server.log_any_text(update, SimpleNamespace())

        assert replies == [("/autopair_answer\n{}", False)]
        assert scheduled == []
    finally:
        monkeypatch.setattr(server, "pairing_router", original_pairing_router)
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_log_any_text_keeps_oversized_autopair_fragment_response_atomic(monkeypatch):
    snapshot = _capture_server_state()
    original_pairing_router = server.pairing_router
    replies = []
    scheduled = []
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username="owner"),
        effective_chat=SimpleNamespace(id=5550001001),
        message=SimpleNamespace(text="payload-continuation", message_id=100, chat_id=5550001001),
    )
    oversized = "/autopair_answer\n" + ("A" * (server._telegram_reply_limit() + 1))

    class FakePairingRouter:
        FRAGMENT_CONSUMED = "__fragment__"

        def has_pending_autopair_buffer(self, platform, sender_id):
            assert (platform, sender_id) == ("telegram", "5550001001")
            return True

        async def process_message(self, text, platform, sender_id):
            assert text == "payload-continuation"
            assert (platform, sender_id) == ("telegram", "5550001001")
            return oversized

    async def fake_authorized(_update):
        return True

    async def fake_reply(_update, text, *, split_text=True):
        replies.append((text, split_text))

    def fake_schedule(**kwargs):
        scheduled.append(kwargs)

    try:
        monkeypatch.setattr(server, "_ensure_telegram_update_authorized", fake_authorized)
        monkeypatch.setattr(server, "_reply_telegram_text", fake_reply)
        monkeypatch.setattr(server, "_schedule_telegram_chat_delivery", fake_schedule)
        monkeypatch.setattr(server, "pairing_router", FakePairingRouter())

        await server.log_any_text(update, SimpleNamespace())

        assert replies == [(oversized, False)]
        assert scheduled == []
    finally:
        monkeypatch.setattr(server, "pairing_router", original_pairing_router)
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_autopair_command_keeps_oversized_response_atomic(monkeypatch):
    snapshot = _capture_server_state()
    original_pairing_router = server.pairing_router
    replies = []
    oversized = "/autopair_answer\n" + ("A" * (server._telegram_reply_limit() + 1))
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username="owner"),
        effective_chat=SimpleNamespace(id=5550001001),
        message=SimpleNamespace(text="/autopair\npayload-head", message_id=101, chat_id=5550001001),
    )

    class FakePairingRouter:
        FRAGMENT_CONSUMED = "__fragment__"

        async def process_message(self, text, platform, sender_id):
            assert text == "/autopair\npayload-head"
            assert (platform, sender_id) == ("telegram", "5550001001")
            return oversized

    async def fake_authorized(_update):
        return True

    async def fake_reply(_update, text, *, split_text=True):
        replies.append((text, split_text))

    try:
        monkeypatch.setattr(server, "_ensure_telegram_update_authorized", fake_authorized)
        monkeypatch.setattr(server, "_reply_telegram_text", fake_reply)
        monkeypatch.setattr(server, "pairing_router", FakePairingRouter())

        await server.autopair_command(update, SimpleNamespace())

        assert replies == [(oversized, False)]
    finally:
        monkeypatch.setattr(server, "pairing_router", original_pairing_router)
        _restore_server_state(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_text", "response_prefix"),
    [
        ("/pair", "/otp\n"),
        ("/pair_hello\npayload-head", "/pair_hello_answer\n"),
    ],
)
async def test_pair_command_keeps_oversized_pairing_responses_atomic(monkeypatch, command_text, response_prefix):
    snapshot = _capture_server_state()
    original_pairing_router = server.pairing_router
    replies = []
    oversized = response_prefix + ("A" * (server._telegram_reply_limit() + 1))
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username="owner"),
        effective_chat=SimpleNamespace(id=5550001001),
        message=SimpleNamespace(text=command_text, message_id=101, chat_id=5550001001),
    )

    class FakePairingRouter:
        FRAGMENT_CONSUMED = "__fragment__"

        async def process_message(self, text, platform, sender_id):
            assert text == command_text
            assert (platform, sender_id) == ("telegram", "5550001001")
            return oversized

    async def fake_authorized(_update):
        return True

    async def fake_reply(_update, text, *, split_text=True):
        replies.append((text, split_text))

    try:
        monkeypatch.setattr(server, "_ensure_telegram_update_authorized", fake_authorized)
        monkeypatch.setattr(server, "_reply_telegram_text", fake_reply)
        monkeypatch.setattr(server, "pairing_router", FakePairingRouter())

        await server.pair_command(update, SimpleNamespace())

        assert replies == [(oversized, False)]
    finally:
        monkeypatch.setattr(server, "pairing_router", original_pairing_router)
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_reply_telegram_text_sends_oversized_pairing_payload_as_document_when_rich_unavailable():
    snapshot = _capture_server_state()
    sent_documents = []
    sent_messages = []

    class FakeBot:
        async def send_document(self, **kwargs):
            sent_documents.append(kwargs)

        async def send_message(self, **kwargs):
            sent_messages.append(kwargs)

    bot = FakeBot()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username="owner"),
        effective_chat=SimpleNamespace(id=5550001001),
        message=SimpleNamespace(
            text="/autopair\npayload",
            message_id=101,
            chat_id=5550001001,
            get_bot=lambda: bot,
        ),
    )
    oversized = "/autopair_answer\n" + ("A" * (server._telegram_reply_limit() + 1))

    try:
        await server._reply_telegram_text(update, oversized, split_text=False)

        assert sent_messages == []
        assert len(sent_documents) == 1
        document = sent_documents[0]["document"]
        assert document.name == "autoyou-pairing-response.txt"
        assert document.getvalue().decode("utf-8") == oversized
        assert sent_documents[0]["caption"] == "AutoYou pairing response attached as one text file."
        assert sent_documents[0]["reply_to_message_id"] == 101
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_reply_telegram_text_prefers_rich_message_for_oversized_pairing_payload(monkeypatch):
    snapshot = _capture_server_state()
    sent_documents = []
    sent_messages = []
    raw_requests = []

    class FakeBot:
        base_url = "https://api.telegram.test/botTOKEN"

        async def send_document(self, **kwargs):
            sent_documents.append(kwargs)

        async def send_message(self, **kwargs):
            sent_messages.append(kwargs)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": 202}}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json):
            raw_requests.append((url, json))
            return FakeResponse()

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username="owner"),
        effective_chat=SimpleNamespace(id=5550001001),
        message=SimpleNamespace(
            text="/autopair\npayload",
            message_id=101,
            chat_id=5550001001,
            get_bot=lambda: FakeBot(),
        ),
    )
    oversized = "/autopair_answer\n<&>" + ("A" * (server._telegram_reply_limit() + 1))

    try:
        monkeypatch.setattr(
            server,
            "httpx",
            SimpleNamespace(
                AsyncClient=FakeAsyncClient,
                Timeout=lambda **kwargs: kwargs,
            ),
        )

        await server._reply_telegram_text(update, oversized, split_text=False)

        assert sent_messages == []
        assert sent_documents == []
        assert len(raw_requests) == 1
        url, payload = raw_requests[0]
        assert url == "https://api.telegram.test/botTOKEN/sendRichMessage"
        assert payload["reply_parameters"] == {"message_id": 101}
        assert payload["rich_message"]["skip_entity_detection"] is True
        assert payload["rich_message"]["html"].startswith("<pre><code>")
        assert payload["rich_message"]["html"].endswith("</code></pre>")
        assert "&lt;&amp;&gt;" in payload["rich_message"]["html"]
        assert "/autopair_answer" in payload["rich_message"]["html"]
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_telegram_session_reply_uses_rich_draft_then_rich_message(monkeypatch):
    snapshot = _capture_server_state()
    raw_requests = []
    sent_messages = []

    class FakeBot:
        base_url = "https://api.telegram.test/botTOKEN"

        async def send_message(self, **kwargs):
            sent_messages.append(kwargs)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": 202}}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json):
            raw_requests.append((url, json))
            return FakeResponse()

    try:
        server.STATE.telegram_chat_bindings = {
            "telegram-session": {
                "chat_id": 5550001001,
                "user_id": "5550001001",
                "reply_to_message_id": 101,
            }
        }
        monkeypatch.setattr(server, "_get_active_telegram_bot", lambda: FakeBot())
        monkeypatch.setattr(
            server,
            "httpx",
            SimpleNamespace(
                AsyncClient=FakeAsyncClient,
                Timeout=lambda **kwargs: kwargs,
            ),
        )

        await server._send_telegram_text_to_session(
            "telegram-session",
            "Hello <world>\n\n~ AutoYou Agent",
        )

        assert sent_messages == []
        assert [url.rsplit("/", 1)[-1] for url, _ in raw_requests] == [
            "sendRichMessageDraft",
            "sendRichMessage",
        ]
        draft_payload = raw_requests[0][1]
        final_payload = raw_requests[1][1]
        assert draft_payload["draft_id"] > 0
        assert draft_payload["rich_message"] == final_payload["rich_message"]
        assert final_payload["reply_parameters"] == {"message_id": 101}
        assert "Hello &lt;world&gt;<br><br>~ AutoYou Agent" in final_payload["rich_message"]["html"]
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_telegram_session_reply_falls_back_to_send_message_when_rich_draft_fails(monkeypatch):
    snapshot = _capture_server_state()
    raw_requests = []
    sent_messages = []

    class FakeBot:
        base_url = "https://api.telegram.test/botTOKEN"

        async def send_message(self, **kwargs):
            sent_messages.append(kwargs)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": False, "description": "rich draft disabled"}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json):
            raw_requests.append((url, json))
            return FakeResponse()

    try:
        server.STATE.telegram_chat_bindings = {
            "telegram-session": {
                "chat_id": 5550001001,
                "user_id": "5550001001",
                "reply_to_message_id": 101,
            }
        }
        monkeypatch.setattr(server, "_get_active_telegram_bot", lambda: FakeBot())
        monkeypatch.setattr(server, "TELEGRAM_SEND_RETRY_ATTEMPTS", 1)
        monkeypatch.setattr(
            server,
            "httpx",
            SimpleNamespace(
                AsyncClient=FakeAsyncClient,
                Timeout=lambda **kwargs: kwargs,
            ),
        )

        await server._send_telegram_text_to_session("telegram-session", "fallback text")

        assert [url.rsplit("/", 1)[-1] for url, _ in raw_requests] == ["sendRichMessageDraft"]
        assert sent_messages == [
            {
                "chat_id": 5550001001,
                "text": "fallback text",
                "reply_to_message_id": 101,
                "connect_timeout": server.TELEGRAM_CONNECT_TIMEOUT_SECONDS,
                "read_timeout": server.TELEGRAM_READ_TIMEOUT_SECONDS,
                "write_timeout": server.TELEGRAM_WRITE_TIMEOUT_SECONDS,
                "pool_timeout": server.TELEGRAM_POOL_TIMEOUT_SECONDS,
            }
        ]
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_telegram_session_reply_env_disables_rich_draft(monkeypatch):
    snapshot = _capture_server_state()
    sent_messages = []

    class FakeBot:
        base_url = "https://api.telegram.test/botTOKEN"

        async def send_message(self, **kwargs):
            sent_messages.append(kwargs)

    try:
        server.STATE.telegram_chat_bindings = {
            "telegram-session": {
                "chat_id": 5550001001,
                "user_id": "5550001001",
                "reply_to_message_id": 101,
            }
        }
        monkeypatch.setenv(server.TELEGRAM_RICH_DRAFT_ENV, "0")
        monkeypatch.setattr(server, "_get_active_telegram_bot", lambda: FakeBot())
        monkeypatch.setattr(server, "TELEGRAM_SEND_RETRY_ATTEMPTS", 1)

        await server._send_telegram_text_to_session("telegram-session", "env fallback")

        assert sent_messages[0]["text"] == "env fallback"
        assert sent_messages[0]["reply_to_message_id"] == 101
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_reply_telegram_text_falls_back_to_document_when_rich_message_is_rejected(monkeypatch):
    snapshot = _capture_server_state()
    sent_documents = []
    raw_requests = []

    class FakeBot:
        base_url = "https://api.telegram.test/botTOKEN"

        async def send_document(self, **kwargs):
            sent_documents.append(kwargs)

        async def send_message(self, **_kwargs):
            raise AssertionError("oversized pairing payload should use the document fallback")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": False, "description": "rich messages disabled"}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json):
            raw_requests.append((url, json))
            return FakeResponse()

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username="owner"),
        effective_chat=SimpleNamespace(id=5550001001),
        message=SimpleNamespace(
            text="/autopair\npayload",
            message_id=101,
            chat_id=5550001001,
            get_bot=lambda: FakeBot(),
        ),
    )
    oversized = "/autopair_answer\n" + ("A" * (server._telegram_reply_limit() + 1))

    try:
        monkeypatch.setattr(
            server,
            "httpx",
            SimpleNamespace(
                AsyncClient=FakeAsyncClient,
                Timeout=lambda **kwargs: kwargs,
            ),
        )

        await server._reply_telegram_text(update, oversized, split_text=False)

        assert len(raw_requests) == 1
        assert len(sent_documents) == 1
        assert sent_documents[0]["document"].getvalue().decode("utf-8") == oversized
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_reply_telegram_text_falls_back_to_document_when_pairing_payload_exceeds_rich_limit(monkeypatch):
    snapshot = _capture_server_state()
    sent_documents = []
    raw_requests = []

    class FakeBot:
        base_url = "https://api.telegram.test/botTOKEN"

        async def send_document(self, **kwargs):
            sent_documents.append(kwargs)

        async def send_message(self, **kwargs):
            raise AssertionError("oversized pairing payload should not fall back to send_message")

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json):
            raw_requests.append((url, json))
            raise AssertionError("rich-message API should not be called past its text limit")

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username="owner"),
        effective_chat=SimpleNamespace(id=5550001001),
        message=SimpleNamespace(
            text="/autopair\npayload",
            message_id=101,
            chat_id=5550001001,
            get_bot=lambda: FakeBot(),
        ),
    )
    oversized = "/autopair_answer\n" + ("A" * (server.TELEGRAM_RICH_MESSAGE_TEXT_LIMIT + 1))

    try:
        monkeypatch.setattr(
            server,
            "httpx",
            SimpleNamespace(
                AsyncClient=FakeAsyncClient,
                Timeout=lambda **kwargs: kwargs,
            ),
        )

        await server._reply_telegram_text(update, oversized, split_text=False)

        assert raw_requests == []
        assert len(sent_documents) == 1
        assert sent_documents[0]["document"].getvalue().decode("utf-8") == oversized
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_failed_pairing_document_fallback_does_not_queue_payload(tmp_path, monkeypatch):
    snapshot = _capture_server_state()
    sent_messages = []

    class FakeBot:
        async def send_document(self, **_kwargs):
            raise RuntimeError("upload refused")

        async def send_message(self, **kwargs):
            sent_messages.append(kwargs)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username="owner"),
        effective_chat=SimpleNamespace(id=5550001001),
        message=SimpleNamespace(
            text="/autopair\npayload",
            message_id=101,
            chat_id=5550001001,
            get_bot=lambda: FakeBot(),
        ),
    )
    oversized = "/autopair_answer\n" + ("A" * (server._telegram_reply_limit() + 1))
    pending_path = tmp_path / "pending_media_replies.json"

    try:
        server.STATE.telegram_pending_media_replies = []
        monkeypatch.setattr(server, "_telegram_pending_media_reply_path", lambda: pending_path)
        monkeypatch.setattr(server, "TELEGRAM_SEND_RETRY_ATTEMPTS", 1)

        await server._reply_telegram_text(update, oversized, split_text=False)

        assert sent_messages
        assert "Telegram refused rich-message and file fallbacks" in sent_messages[0]["text"]
        assert server.STATE.telegram_pending_media_replies == []
        assert not pending_path.exists()
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_autopair_command_sends_real_tier_a_answer_as_rich_message(monkeypatch):
    snapshot = _capture_server_state()
    original_pairing_router = server.pairing_router
    sent_documents = []
    sent_messages = []
    raw_requests = []

    class FakeBot:
        base_url = "https://api.telegram.test/botTOKEN"

        async def send_document(self, **kwargs):
            sent_documents.append(kwargs)

        async def send_message(self, **kwargs):
            sent_messages.append(kwargs)

    async def large_answer(_sender_id, _payload):
        candidate_lines = []
        for index in range(80):
            token = hashlib.sha256(f"candidate-{index}".encode("ascii")).hexdigest()[:24]
            candidate_lines.append(
                f"a=candidate:{token} 1 udp {2113937151 - index} 192.0.2.{index % 250 + 1} {50000 + index} typ host\r\n"
            )
        return {"type": "answer", "sdp": "v=0\r\n" + "".join(candidate_lines)}

    async def noop_async(*_args, **_kwargs):
        return True

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": 202}}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json):
            raw_requests.append((url, json))
            return FakeResponse()

    router = PairingRouter()
    router.configure(
        generate_hash=lambda password: f"hash::{password}",
        aead_encrypt=lambda payload, password: payload,
        aead_decrypt=lambda payload, password: payload,
        get_current_password=lambda: "autoyou123",
        get_security_mode=lambda: "secure",
        get_totp_secret_for_sender=lambda *_: None,
        get_all_totp_secrets=lambda: {},
        get_tunnelmole_status=lambda: {"status": "running", "public_url": "https://example.test"},
        extend_tunnelmole_timer=noop_async,
        start_tunnelmole_service_with_timer=noop_async,
        generate_otp_hash_and_cache=lambda *_: "otp-hash",
        handle_autopair_offer=large_answer,
        get_pairing_tier=lambda: "B",
    )

    async def fake_authorized(_update):
        return True

    try:
        monkeypatch.setattr(server, "pairing_router", router)
        monkeypatch.setattr(server, "_ensure_telegram_update_authorized", fake_authorized)
        monkeypatch.setitem(pairing_router_module.PLATFORM_REPLY_LIMITS, "telegram", 512)
        monkeypatch.setattr(
            server,
            "httpx",
            SimpleNamespace(
                AsyncClient=FakeAsyncClient,
                Timeout=lambda **kwargs: kwargs,
            ),
        )

        hello_env, handshake = build_cpace_hello("autoyou123", purpose="autopair")
        hello_answer = await router.process_message(
            f"/autopair_hello\n{hello_env}",
            platform="telegram",
            sender_id="5550001001",
        )
        assert hello_answer.startswith("/autopair_hello_answer\n")
        session = complete_cpace_handshake(hello_answer.split("\n", 1)[1], handshake)
        offer_payload = {
            "hash": "hash::autoyou123",
            "offer": {"type": "offer", "sdp": "v=0\r\n"},
            "iceServers": [],
        }
        encrypted_offer = encrypt_cpace_message(
            json.dumps(offer_payload, separators=(",", ":")),
            session,
        )
        bot = FakeBot()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=5550001001, username="owner"),
            effective_chat=SimpleNamespace(id=5550001001),
            message=SimpleNamespace(
                text=f"/autopair\n{encrypted_offer}",
                message_id=101,
                chat_id=5550001001,
                get_bot=lambda: bot,
            ),
        )

        await server.autopair_command(update, SimpleNamespace())

        assert sent_messages == []
        assert sent_documents == []
        assert len(raw_requests) == 1
        reply_html = raw_requests[0][1]["rich_message"]["html"]
        assert reply_html.startswith("<pre><code>/autopair_answer\ncpace1:")
        reply_text = reply_html.removeprefix("<pre><code>").removesuffix("</code></pre>")
        assert reply_text.startswith("/autopair_answer\ncpace1:")
        assert len(reply_text) > server._telegram_reply_limit()
        plaintext = decrypt_cpace_message(reply_text.split("\n", 1)[1], session)
        decoded = json.loads(PairingRouter._decode_compressed_payload_if_needed(plaintext))
        assert decoded["answer"]["sdp"].count("a=candidate:") == 80
    finally:
        monkeypatch.setattr(server, "pairing_router", original_pairing_router)
        _restore_server_state(snapshot)


def test_telegram_pairing_payload_detection_uses_exact_command_prefixes():
    long_payload = "A" * (server._telegram_reply_limit() + 1)

    assert server._telegram_pairing_payload_needs_document(f"/otp\n{long_payload}") is True
    assert server._telegram_pairing_payload_needs_document(f"/otp_pair\n{long_payload}") is False
    assert server._telegram_pairing_payload_needs_document(f"/autopair_answered\n{long_payload}") is False


def test_split_text_for_transport_keeps_autopair_header_with_payload():
    text = "/autopair_answer\n" + ("A" * 80)

    chunks = server._split_text_for_transport(text, 32)

    assert len(chunks) > 1
    assert chunks[0].startswith("/autopair_answer\nA")
    assert all(len(chunk) <= 32 for chunk in chunks)


@pytest.mark.asyncio
async def test_unauthorized_telegram_message_gets_allow_guidance(monkeypatch):
    snapshot = _capture_server_state()
    replies = []
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username=None),
        message=SimpleNamespace(text="hello", message_id=1, chat_id=5550001001),
    )

    async def fake_reply(_update, text, *, split_text=True):
        replies.append((text, split_text))

    try:
        server.STATE.config = {"telegram": {"access_gate_enabled": True, "acl_sender_ids": [], "acl_usernames": []}}
        monkeypatch.setattr(server, "_reply_telegram_text", fake_reply)

        allowed = await server._ensure_telegram_update_authorized(update)

        assert allowed is False
        assert replies
        assert "Your Telegram user id: 5550001001" in replies[0][0]
        assert "/allow XXXXXXXX" in replies[0][0]
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_unauthorized_telegram_message_is_recorded_for_sender_discovery(monkeypatch):
    snapshot = _capture_server_state()
    replies = []
    body = "synthetic setup message body"
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001002, username="setup_owner"),
        effective_chat=SimpleNamespace(id=5550001002, type="private"),
        message=SimpleNamespace(text=body, message_id=2, chat_id=5550001002),
    )

    async def fake_reply(_update, text, *, split_text=True):
        replies.append((text, split_text))

    try:
        server.STATE.config = {
            "telegram": {
                "bot_token": "123:abc",
                "access_gate_enabled": True,
                "acl_sender_ids": [],
                "acl_usernames": [],
            }
        }
        server.STATE.telegram_seen_senders = {}
        monkeypatch.setattr(server, "_reply_telegram_text", fake_reply)

        allowed = await server._ensure_telegram_update_authorized(update)
        payload = server._telegram_sender_discovery_payload()

        assert allowed is False
        assert replies
        assert payload["configured"] is True
        assert payload["count"] == 1
        assert payload["senders"][0]["sender_id"] == "5550001002"
        assert payload["senders"][0]["username_label"] == "@setup_owner"
        assert payload["senders"][0]["approved"] is False
        assert body not in repr(payload)
    finally:
        _restore_server_state(snapshot)


def test_redeem_telegram_allow_code_locks_sender_id(monkeypatch):
    snapshot = _capture_server_state()
    persisted_configs = []

    def fake_persist(cfg, *, server_password=None, preferred_store=None):
        persisted_configs.append(cfg)
        server.STATE.config = cfg
        return server.CONFIG_STORE_ENCRYPTED

    try:
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "telegram": {
                "bot_token": "123:abc",
                "access_gate_enabled": True,
                "acl_sender_ids": [],
                "acl_usernames": [],
            },
        }
        server.STATE.telegram_allow_tokens = {}
        server._set_config_session(
            config_store=server.CONFIG_STORE_ENCRYPTED,
            server_password="1234",
            config_unlock_password="1234",
        )
        monkeypatch.setattr(server, "_can_persist_config", lambda: True)
        monkeypatch.setattr(server, "_persist_state_config", fake_persist)

        code = server._issue_telegram_allow_code()
        success, message = server._redeem_telegram_allow_code(code, "5550001001")

        assert success is True
        assert "Telegram access approved" in message
        assert persisted_configs
        assert server.STATE.config["telegram"]["acl_sender_ids"] == ["5550001001"]
        assert server.STATE.config["telegram"]["access_gate_enabled"] is True
        assert server.STATE.telegram_allow_tokens == {}
    finally:
        _restore_server_state(snapshot)


def test_sender_id_acl_takes_precedence_over_usernames():
    snapshot = _capture_server_state()

    try:
        server.STATE.config = {
            "telegram": {
                "access_gate_enabled": False,
                "acl_sender_ids": ["5550001001"],
                "acl_usernames": ["owner"],
            }
        }

        assert server._telegram_user_is_authorized("5550001001", None) is True
        assert server._telegram_user_is_authorized("999999999", "owner") is False
    finally:
        _restore_server_state(snapshot)


@pytest.mark.asyncio
async def test_unauthorized_telegram_message_can_be_silently_ignored(monkeypatch):
    snapshot = _capture_server_state()
    replies = []
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=5550001001, username=None),
        message=SimpleNamespace(text="hello", message_id=1, chat_id=5550001001),
    )

    async def fake_reply(_update, text, *, split_text=True):
        replies.append((text, split_text))

    try:
        server.STATE.config = {
            "telegram": {
                "access_gate_enabled": True,
                "acl_sender_ids": [],
                "acl_usernames": [],
                "silent_unapproved_messages": True,
            }
        }
        monkeypatch.setattr(server, "_reply_telegram_text", fake_reply)

        allowed = await server._ensure_telegram_update_authorized(update)

        assert allowed is False
        assert replies == []
    finally:
        _restore_server_state(snapshot)


def test_admin_generate_telegram_allow_code_redirects_and_enables_gate(monkeypatch):
    snapshot = _capture_server_state()

    def fake_persist(cfg, *, server_password=None, preferred_store=None):
        server.STATE.config = cfg
        return server.CONFIG_STORE_ENCRYPTED

    try:
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "telegram": {"bot_token": "123:abc", "access_gate_enabled": False},
        }
        monkeypatch.setattr(server, "_persist_state_config", fake_persist)
        monkeypatch.setattr(server, "_issue_telegram_allow_code", lambda: "WQZVH9MU")
        server._set_config_session(
            config_store=server.CONFIG_STORE_ENCRYPTED,
            server_password="secret",
            config_unlock_password="secret",
        )

        with _auth_client() as client:
            response = client.post("/admin/telegram/allow-code", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/?telegram_allow_code=WQZVH9MU"
        assert server.STATE.config["telegram"]["access_gate_enabled"] is True
    finally:
        _restore_server_state(snapshot)


def test_admin_telegram_sender_approval_endpoint_merges_acl(monkeypatch):
    snapshot = _capture_server_state()
    saved_configs = []

    def fake_save_and_reload(cfg, **_kwargs):
        saved_configs.append(cfg)
        server.STATE.config = cfg
        return cfg

    async def fake_bootstrap():
        return {"success": True, "config": server.STATE.config}

    try:
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "telegram": {
                "bot_token": "123:abc",
                "access_gate_enabled": False,
                "acl_sender_ids": ["5550001001"],
                "acl_usernames": [],
            },
        }
        server.STATE.telegram_seen_senders = {
            "5550001002": {
                "sender_key": "5550001002",
                "sender_id": "5550001002",
                "username": "setup_owner",
                "chat_id": "5550001002",
                "chat_type": "private",
                "message_count": 1,
                "first_seen_at_s": 1000.0,
                "last_seen_at_s": 1001.0,
                "last_message_kind": "message",
            }
        }
        server._set_config_session(
            config_store=server.CONFIG_STORE_ENCRYPTED,
            server_password="secret",
            config_unlock_password="secret",
        )
        monkeypatch.setattr(server, "_save_and_reload_state_config", fake_save_and_reload)
        monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", fake_bootstrap)

        with _auth_client() as client:
            response = client.post("/api/telegram/senders/approve", json={"sender_ids": ["5550001002"]})

        assert response.status_code == 200
        payload = response.json()
        assert saved_configs
        assert server.STATE.config["telegram"]["access_gate_enabled"] is True
        assert server.STATE.config["telegram"]["acl_sender_ids"] == ["5550001001", "5550001002"]
        assert payload["approved_sender_ids"] == ["5550001001", "5550001002"]
        assert payload["senders"][0]["approved"] is True
        assert payload["bootstrap"]["success"] is True
    finally:
        _restore_server_state(snapshot)
