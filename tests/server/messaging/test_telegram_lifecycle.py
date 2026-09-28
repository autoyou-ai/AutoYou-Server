# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-3436423233373332206f7220-2bc2f6aa7e3d5945a849f490


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-3436423233373332206f7220-2bc2f6aa7e3d5945a849f490"

from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import server


@pytest.mark.asyncio
async def test_telegram_chat_delivery_marks_reset_metadata(monkeypatch):
    import rest_api

    captured = {}

    class FakeExecutionManager:
        async def submit_turn(self, identity, handler, *, on_status=None, timeout_seconds=None, label="chat"):
            if on_status is not None:
                await on_status(SimpleNamespace(queue_position=2))
            return await handler()

    async def noop(*args, **kwargs):
        return None

    async def fake_media_send(*args, **kwargs):
        return True

    async def fake_voice_send(*args, **kwargs):
        return False

    async def fake_process_chat_message(chat_request, **kwargs):
        captured["metadata"] = dict(chat_request.metadata or {})
        captured["message"] = chat_request.message
        captured["user_id"] = chat_request.user_id
        captured["session_id"] = chat_request.session_id
        return SimpleNamespace(
            response="ok",
            session_id=chat_request.session_id,
            message_id="test-message",
            agent_name=server.ROOT_AGENT_NAME,
            metadata={},
            media_reply_attachments=[],
            voice_reply_audio_path=None,
        )

    monkeypatch.setattr(server, "get_session_execution_manager", lambda: FakeExecutionManager())
    monkeypatch.setattr(server, "_get_stable_server_id", lambda cfg=None: "server-test")
    monkeypatch.setattr(server, "_get_server_identity_key", lambda cfg=None: "name:server-test")
    monkeypatch.setattr(server, "_send_telegram_chat_action_to_session", noop)
    monkeypatch.setattr(server, "_telegram_typing_heartbeat", noop)
    monkeypatch.setattr(server, "_send_telegram_text_to_session", noop)
    monkeypatch.setattr(server, "_send_telegram_media_attachments_to_session", fake_media_send)
    monkeypatch.setattr(server, "_send_telegram_voice_to_session", fake_voice_send)
    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)

    identity = SimpleNamespace(
        transport="telegram",
        sender_id="synthetic-sender",
        raw_session_id="synthetic-chat",
        owner_key="telegram:synthetic-sender",
        canonical_user_id="telegram:synthetic-sender",
        canonical_session_id="session::synthetic-chat::thread::2",
        thread_id=2,
    )

    task = server._schedule_telegram_chat_delivery(
        message="hello",
        identity=identity,
        chat_id=12345,
        metadata={"client": "telegram", "telegram_user_id": "synthetic-user"},
        context=[],
        reply_to_message_id=67890,
        reset=True,
    )

    await task

    assert captured["message"] == "hello"
    assert captured["user_id"] == identity.canonical_user_id
    assert captured["session_id"] == identity.canonical_session_id
    metadata = captured["metadata"]
    assert metadata["conversation_reset"] is True
    assert metadata["conversation_thread_id"] == 2
    assert metadata["server_id"] == "server-test"
    assert metadata["server_identity_key"] == "name:server-test"
    assert metadata["reply_target"] == {
        "transport": "telegram",
        "chat_id": 12345,
        "reply_to_message_id": 67890,
    }
    assert metadata["session_execution"]["queue_position"] == 2


@pytest.mark.asyncio
async def test_telegram_start_configures_get_updates_pool_for_clean_shutdown(monkeypatch):
    calls = []
    start_polling_kwargs = {}

    class FakeBuilder:
        def token(self, value):
            calls.append(("token", value))
            return self

        def connect_timeout(self, value):
            calls.append(("connect_timeout", value))
            return self

        def read_timeout(self, value):
            calls.append(("read_timeout", value))
            return self

        def write_timeout(self, value):
            calls.append(("write_timeout", value))
            return self

        def pool_timeout(self, value):
            calls.append(("pool_timeout", value))
            return self

        def media_write_timeout(self, value):
            calls.append(("media_write_timeout", value))
            return self

        def get_updates_pool_timeout(self, value):
            calls.append(("get_updates_pool_timeout", value))
            return self

        def get_updates_connection_pool_size(self, value):
            calls.append(("get_updates_connection_pool_size", value))
            return self

        def get_updates_connect_timeout(self, value):
            calls.append(("get_updates_connect_timeout", value))
            return self

        def get_updates_read_timeout(self, value):
            calls.append(("get_updates_read_timeout", value))
            return self

        def get_updates_write_timeout(self, value):
            calls.append(("get_updates_write_timeout", value))
            return self

        def build(self):
            return fake_app

    class FakeUpdater:
        async def start_polling(self, **kwargs):
            start_polling_kwargs.update(kwargs)

    class FakeBot:
        async def get_webhook_info(self):
            return SimpleNamespace(url="")

        async def delete_webhook(self, drop_pending_updates=False):
            calls.append(("delete_webhook", drop_pending_updates))

    class FakeApp:
        def __init__(self):
            self.updater = FakeUpdater()
            self.bot = FakeBot()
            self.handlers = []

        def add_handler(self, handler):
            self.handlers.append(handler)

        def add_error_handler(self, handler):
            self.error_handler = handler

        async def initialize(self):
            calls.append(("initialize", None))

        async def start(self):
            calls.append(("start", None))

    fake_app = FakeApp()
    original_config = server.STATE.config
    original_telegram_app = server.STATE.telegram_app
    original_last_token = server.STATE.last_telegram_token

    monkeypatch.setattr(server, "Application", SimpleNamespace(builder=lambda: FakeBuilder()))
    monkeypatch.setattr(server, "ConversationHandler", lambda *args, **kwargs: ("conversation", args, kwargs))
    monkeypatch.setattr(server, "CommandHandler", lambda *args, **kwargs: ("command", args, kwargs))
    monkeypatch.setattr(server, "filters", None)
    monkeypatch.setattr(server, "_flush_telegram_pending_voice_replies", lambda: _noop_async())
    monkeypatch.setattr(server, "_flush_telegram_pending_media_replies", lambda: _noop_async())

    async def _noop_async():
        return None

    try:
        server.STATE.config = {"telegram": {"bot_token": "test-token"}}
        server.STATE.telegram_app = None
        server.STATE.last_telegram_token = None

        await server.start_or_restart_telegram()

        assert ("get_updates_pool_timeout", server.TELEGRAM_GET_UPDATES_POOL_TIMEOUT_SECONDS) in calls
        assert (
            "get_updates_connection_pool_size",
            server.TELEGRAM_GET_UPDATES_CONNECTION_POOL_SIZE,
        ) in calls
        assert start_polling_kwargs["pool_timeout"] == server.TELEGRAM_GET_UPDATES_POOL_TIMEOUT_SECONDS
        assert start_polling_kwargs["connect_timeout"] == server.TELEGRAM_CONNECT_TIMEOUT_SECONDS
        assert server.STATE.telegram_app is fake_app
    finally:
        server.STATE.config = original_config
        server.STATE.telegram_app = original_telegram_app
        server.STATE.last_telegram_token = original_last_token
