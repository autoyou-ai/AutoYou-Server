# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-5b22f47fb8e45be944c72bef


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import asyncio
import importlib
import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.connected_device import OpenChannel, connect_device
from tests.support.paths import ensure_repo_on_path

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-5b22f47fb8e45be944c72bef"


ensure_repo_on_path()

import server
from shared import scheduler_service
from shared.session_execution import build_canonical_session_id, build_canonical_user_id, build_owner_key


def test_scheduler_files_honor_autoyou_test_root(monkeypatch, tmp_path):
    original_test_root = os.environ.get("AUTOYOU_TEST_ROOT")
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    reloaded = importlib.reload(scheduler_service)
    try:
        expected_root = (tmp_path / "AutoYou").resolve()
        paths = [
            Path(reloaded.REMINDERS_FILE).resolve(),
            Path(reloaded.TASKS_FILE).resolve(),
            Path(reloaded.OUTBOUND_NOTIFICATION_QUEUE_FILE).resolve(),
            Path(reloaded.SCHEDULER_ACTIVITY_LOG_FILE).resolve(),
        ]
        assert paths == [
            expected_root / "reminders.json",
            expected_root / "cron_tasks.json",
            expected_root / "output" / "scheduled_notification_queue.json",
            expected_root / "output" / "scheduler_activity_log.jsonl",
        ]
    finally:
        if original_test_root is None:
            monkeypatch.delenv("AUTOYOU_TEST_ROOT", raising=False)
        else:
            monkeypatch.setenv("AUTOYOU_TEST_ROOT", original_test_root)
        importlib.reload(scheduler_service)


def test_scheduler_preserves_sealed_legacy_store_after_maximus_downgrade(monkeypatch, tmp_path):
    from shared.secure_storage import FILE_HEADER

    legacy_file = tmp_path / "reminders.json"
    legacy_bytes = FILE_HEADER + b"ciphertext-must-not-be-read"
    legacy_file.write_bytes(legacy_bytes)
    fresh_file = legacy_file.with_name("reminders.secure-professional.json")
    # from __debug_provenance_g__ import annual

    monkeypatch.setattr(scheduler_service, "secure_storage_enabled", lambda: False)

    def load_json(path, *, default=None):
        candidate = Path(path)
        return json.loads(candidate.read_text(encoding="utf-8")) if candidate.exists() else default

    def save_json(path, payload):
        Path(path).write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(scheduler_service, "load_secure_json", load_json)
    monkeypatch.setattr(scheduler_service, "save_secure_json", save_json)

    scheduler_service.save_json(str(legacy_file), [{"id": "synthetic-reminder"}])

    assert legacy_file.read_bytes() == legacy_bytes
    assert json.loads(fresh_file.read_text(encoding="utf-8")) == [{"id": "synthetic-reminder"}]
    assert scheduler_service.load_json(str(legacy_file)) == [{"id": "synthetic-reminder"}]

    # Keep the fresh scheduler data selected after Maximus is enabled again.
    monkeypatch.setattr(scheduler_service, "secure_storage_enabled", lambda: True)
    assert scheduler_service._scheduler_store_path(str(legacy_file)) == str(fresh_file)

    # A second downgrade after Maximus has sealed that sibling starts a new
    # plaintext store without opening or replacing the protected one.
    fresh_file.write_bytes(FILE_HEADER + b"second-generation-ciphertext")
    monkeypatch.setattr(scheduler_service, "secure_storage_enabled", lambda: False)
    second_store = legacy_file.with_name("reminders.secure-professional-2.json")
    scheduler_service.save_json(str(legacy_file), [{"id": "next-reminder"}])
    assert fresh_file.read_bytes() == FILE_HEADER + b"second-generation-ciphertext"
    assert json.loads(second_store.read_text(encoding="utf-8")) == [{"id": "next-reminder"}]


class _FakeWhatsAppService:
    def __init__(self):
        self.calls = []

    async def send_message(self, to: str, message: str) -> bool:
        self.calls.append((to, message))
        return True


class _FakeTelegramBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.calls.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})
        return True


class _FakeTelegramUserService:
    def __init__(self):
        self.calls = []

    async def send_message(self, message: str) -> bool:
        self.calls.append(message)
        return True


class _FakeTelegramUserSavedMessagesService:
    def __init__(self):
        self.calls = []

    async def send_saved_message(self, message: str) -> bool:
        self.calls.append(message)
        return True


class _FakeUnavailableWebRTCManager:
    def __init__(self):
        self.calls = []
        self.datachannel_managers = {}

    async def send_chat_to_reply_target(self, reply_target, message, *, metadata=None, context=None, user_id=None):
        self.calls.append(
            {
                "reply_target": dict(reply_target),
                "message": message,
                "metadata": dict(metadata or {}),
                "user_id": user_id,
            }
        )
        return False


class _FakeLiveDataChannelManager:
    async def send_message(self, message):
        return True


class _FakeOwnerAwareWebRTCManager:
    def __init__(self, *, owner_key: str, canonical_user_id: str, session_ids):
        self.owner_key = owner_key
        self.canonical_user_id = canonical_user_id
        self.calls = []
        self.datachannel_managers = {
            session_id: _FakeLiveDataChannelManager() for session_id in session_ids
        }

    def _resolve_chat_identity(self, session_id: str):
        return SimpleNamespace(
            owner_key=self.owner_key,
            canonical_user_id=self.canonical_user_id,
        )

    async def send_chat_to_reply_target(self, reply_target, message, *, metadata=None, context=None, user_id=None):
        self.calls.append(
            {
                "reply_target": dict(reply_target),
                "message": message,
                "metadata": dict(metadata or {}),
                "user_id": user_id,
            }
        )
        return True


class _FakeAliasAwareDataChannelManager(_FakeLiveDataChannelManager):
    def __init__(self, session_id: str):
        self.session_id = session_id


class _FakeAliasAwareWebRTCManager:
    def __init__(self, *, owner_key: str, canonical_user_id: str):
        self.owner_key = owner_key
        self.canonical_user_id = canonical_user_id
        self.calls = []
        manager = _FakeAliasAwareDataChannelManager("client-session")
        self.datachannel_managers = {
            "relay-session": manager,
            "client-session": manager,
        }

    def _resolve_chat_identity(self, session_id: str):
        return SimpleNamespace(
            owner_key=self.owner_key,
            canonical_user_id=self.canonical_user_id,
        )

    async def send_chat_to_reply_target(self, reply_target, message, *, metadata=None, context=None, user_id=None):
        self.calls.append(
            {
                "reply_target": dict(reply_target),
                "message": message,
                "metadata": dict(metadata or {}),
                "user_id": user_id,
            }
        )
        return True


def test_webrtc_notification_metadata_preserves_saved_conversation_session_id():
    notification = {
        "id": "notif-conversation",
        "owner_key": "cloud:synthetic-device",
        "canonical_user_id": "user::cloud:synthetic-device",
        "external_session_id": "session::cloud:synthetic-device",
        "conversation_session_id": (
            "session::dest::server::local%3Amachine-2::owner::cloud%3Asynthetic-device"
            "::target::synthetic-device"
        ),
    }
    reply_target = {
        "transport": "webrtc",
        "owner_key": "cloud:synthetic-device",
        "session_id": "live-session-2",
    }

    metadata = scheduler_service._build_webrtc_notification_metadata(
        notification,
        reply_target,
    )

    assert metadata["conversation_session_id"] == (
        "session::dest::server::local%3Amachine-2::owner::cloud%3Asynthetic-device"
        "::target::synthetic-device"
    )
    assert metadata["conversation_force_target"] is True
    assert metadata["canonical_owner_key"] == "cloud:synthetic-device"


@pytest.mark.asyncio
async def test_launch_scheduled_task_chat_request_preserves_client_memory_metadata(monkeypatch):
    import rest_api

    captured = {}

    async def fake_direct_task_action(task_id, task):
        return None

    async def fake_process_chat_message(chat_request, ai_agent_url=None, on_chunk=None):
        captured["request"] = chat_request
        captured["ai_agent_url"] = ai_agent_url
        return SimpleNamespace(response="")

    monkeypatch.setattr(scheduler_service, "_execute_direct_task_action", fake_direct_task_action)
    monkeypatch.setattr(scheduler_service, "append_scheduler_activity_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)
    monkeypatch.setattr(rest_api, "get_ai_agent_server_url", lambda: "http://127.0.0.1:8081")

    task = {
        "id": "task-webrtc-memory",
        "instruction": "summarize yesterday's synthetic notes",
        "run_counter": 2,
        "creator_owner_key": "webrtc:device-synthetic",
        "creator_user_id": "user::webrtc:device-synthetic",
        "creator_external_session_id": "session::webrtc:device-synthetic",
        "creator_conversation_session_id": (
            "session::dest::server::name%3AAutoYou%20Test%20Server"
            "::owner::webrtc%3Adevice-synthetic::target::device-synthetic"
        ),
        "delivery_target": {
            "transport": "webrtc",
            "session_id": "device-synthetic",
            "owner_key": "webrtc:device-synthetic",
        },
    }

    scheduler_service._launch_scheduled_task(task)
    handle = scheduler_service._ACTIVE_TASK_HANDLES["task-webrtc-memory"]
    await handle

    request = captured["request"]
    metadata = request.metadata

    assert captured["ai_agent_url"] == "http://127.0.0.1:8081"
    assert request.user_id == "user::webrtc:device-synthetic"
    assert request.session_id == "scheduled-task::task-webrtc-memory::run::2"
    assert metadata["source"] == "scheduler"
    assert metadata["client"] == "scheduler"
    assert metadata["scheduled_task_id"] == "task-webrtc-memory"
    assert metadata["scheduled_task_instruction"] == "summarize yesterday's synthetic notes"
    assert metadata["canonical_owner_key"] == "webrtc:device-synthetic"
    assert metadata["canonical_user_id"] == "user::webrtc:device-synthetic"
    assert metadata["canonical_session_id"] == "session::webrtc:device-synthetic"
    assert metadata["conversation_force_target"] is True
    assert metadata["pairing_mode"] == "local_pair"
    assert metadata["reply_target"] == task["delivery_target"]


def _make_runtime_server(
    *, webrtc, telegram_bot=None, telegram_user_service=None, notify_cloud_client=None, session_manager=None
):
    return SimpleNamespace(
        WEBRTC=webrtc,
        STATE=SimpleNamespace(
            telegram_app=SimpleNamespace(bot=telegram_bot) if telegram_bot is not None else None,
            telegram_user_service=telegram_user_service,
            whatsapp_service=None,
            signal_service=None,
        ),
        get_configured_server_name=lambda: "AutoYou Test Server",
        _build_conversation_metadata=server._build_conversation_metadata,
        _get_active_telegram_bot=lambda: telegram_bot,
        _send_telegram_text_via_bot=server._send_telegram_text_via_bot,
        _notify_cloud_client=notify_cloud_client,
        _get_conversation_session_manager=lambda: session_manager,
    )


@pytest.mark.asyncio
async def test_execute_direct_task_action_sends_to_saved_reply_target_without_admin_state():
    original_whatsapp = server.STATE.whatsapp_service
    try:
        service = _FakeWhatsAppService()
        server.STATE.whatsapp_service = service

        result = await scheduler_service._execute_direct_task_action(
            "task-1",
            {
                "id": "task-1",
                "delivery_target": {"transport": "whatsapp", "to": "+15551234567"},
                "action": {"type": "send_message", "message": "scheduled hello"},
            },
        )

        assert result == {"status": "success", "message": "Sent message via whatsapp."}
        assert service.calls == [("+15551234567", "scheduled hello")]
    finally:
        server.STATE.whatsapp_service = original_whatsapp


@pytest.mark.asyncio
async def test_telegram_user_reply_target_delivers_to_saved_messages_only(monkeypatch):
    service = _FakeTelegramUserService()
    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(
            webrtc=_FakeUnavailableWebRTCManager(),
            telegram_user_service=service,
        ),
    )

    assert scheduler_service.get_runtime_messaging_partner_reply_targets() == [
        {"transport": "telegram_user"}
    ]
    result = await scheduler_service._deliver_reply_target_message(
        {"transport": "telegram_user", "to": "synthetic-recipient"},
        "scheduled hello",
    )

    assert result == {"status": "success", "message": "Sent message via Telegram Saved Messages."}
    assert service.calls == ["scheduled hello"]


@pytest.mark.asyncio
async def test_telegram_user_reply_target_uses_saved_message_fallback(monkeypatch):
    service = _FakeTelegramUserSavedMessagesService()
    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(
            webrtc=_FakeUnavailableWebRTCManager(),
            telegram_user_service=service,
        ),
    )

    result = await scheduler_service._deliver_reply_target_message(
        {"transport": "telegram_user"},
        "scheduled fallback",
    )

    assert result == {"status": "success", "message": "Sent message via Telegram Saved Messages."}
    assert service.calls == ["scheduled fallback"]


@pytest.mark.asyncio
async def test_telegram_user_reply_target_is_graceful_when_unavailable(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(webrtc=_FakeUnavailableWebRTCManager()),
    )

    result = await scheduler_service._deliver_reply_target_message(
        {"transport": "telegram_user"},
        "scheduled unavailable",
    )

    assert result == {
        "status": "error",
        "message": "Telegram Saved Messages service not available.",
    }


class _FakeWebRTCManager:
    def __init__(self):
        self.calls = []
        self.datachannel_managers = {"live-session": object()}

    async def send_chat_to_reply_target(self, reply_target, message, *, metadata=None, context=None, user_id=None):
        self.calls.append(
            {
                "reply_target": dict(reply_target),
                "message": message,
                "metadata": dict(metadata or {}),
                "user_id": user_id,
            }
        )
        return True


@pytest.mark.asyncio
async def test_deliver_reply_target_message_prefers_live_main_server_module(monkeypatch):
    fake_webrtc = _FakeWebRTCManager()
    fake_main_server = SimpleNamespace(
        WEBRTC=fake_webrtc,
        STATE=SimpleNamespace(
            telegram_app=None,
            whatsapp_service=None,
            signal_service=None,
        ),
        get_configured_server_name=lambda: "AutoYou Test Server",
    )

    monkeypatch.setitem(sys.modules, "__main__", fake_main_server)

    result = await scheduler_service._deliver_reply_target_message(
        {
            "transport": "webrtc",
            "session_id": "live-session",
            "owner_key": "cloud:live-session",
        },
        "scheduled hello",
    )

    assert result == {"status": "success", "message": "Sent message via webrtc."}
    assert fake_webrtc.calls == [
        {
            "reply_target": {
                "transport": "webrtc",
                "session_id": "live-session",
                "owner_key": "cloud:live-session",
            },
            "message": "scheduled hello",
            "metadata": {"source": "scheduler", "is_notification": True},
            "user_id": "AutoYou Test Server",
        }
    ]


@pytest.mark.asyncio
async def test_execute_direct_task_action_queues_when_webrtc_owner_is_offline(monkeypatch, tmp_path):
    queue_path = tmp_path / "scheduled_notification_queue.json"
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))

    fake_bot = _FakeTelegramBot()
    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(
            webrtc=_FakeUnavailableWebRTCManager(),
            telegram_bot=fake_bot,
        ),
    )

    result = await scheduler_service._execute_direct_task_action(
        "task-fallback",
        {
            "id": "task-fallback",
            "creator_owner_key": "telegram:12345",
            "creator_user_id": "user::telegram:12345",
            "delivery_target": {
                "transport": "webrtc",
                "owner_key": "telegram:12345",
                "session_id": "offline-session",
            },
            "action": {"type": "send_message", "message": "scheduled hello"},
        },
    )

    assert result["status"] == "success"
    assert result["queued"] is True
    assert fake_bot.calls == []
    queued_notifications = scheduler_service.load_json(str(queue_path))
    assert len(queued_notifications) == 1
    assert queued_notifications[0]["prefer_live_webrtc_only"] is True


@pytest.mark.asyncio
async def test_execute_direct_task_action_uses_cloud_notification_for_cloud_webrtc_owner(monkeypatch, tmp_path):
    queue_path = tmp_path / "scheduled_notification_queue.json"
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))

    cloud_calls = []

    async def _fake_notify_cloud_client(**kwargs):
        cloud_calls.append(kwargs)
        return {
            "success": True,
            "sent": True,
            "sse_delivered": False,
            "offline_allowed": True,
            "devices": 1,
            "apns_sent": 1,
            "fcm_sent": 0,
        }

    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(
            webrtc=_FakeUnavailableWebRTCManager(),
            notify_cloud_client=_fake_notify_cloud_client,
        ),
    )

    result = await scheduler_service._execute_direct_task_action(
        "task-cloud-fallback",
        {
            "id": "task-cloud-fallback",
            "creator_owner_key": "cloud:device-session-123",
            "creator_user_id": "user::cloud:device-session-123",
            "delivery_target": {
                "transport": "webrtc",
                "owner_key": "cloud:device-session-123",
                "session_id": "offline-session",
            },
            "action": {"type": "send_message", "message": "scheduled hello"},
        },
    )

    assert result["status"] == "success"
    assert result["message"] == "Sent message via AutoYou Cloud notification."
    assert scheduler_service.load_json(str(queue_path)) == []
    assert cloud_calls == [
        {
            "title": "AutoYou notification",
            "body": "scheduled hello",
            "category": "scheduled_notification",
            "data": {
                "source": "scheduled-task:task-cloud-fallback",
                "notification_id": cloud_calls[0]["data"]["notification_id"],
                "delivery": "offline_notification",
            },
        }
    ]


@pytest.mark.asyncio
async def test_execute_direct_task_action_prefers_live_webrtc_sessions_for_same_owner(monkeypatch, tmp_path):
    queue_path = tmp_path / "scheduled_notification_queue.json"
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))

    fake_bot = _FakeTelegramBot()
    fake_webrtc = _FakeOwnerAwareWebRTCManager(
        owner_key="telegram:12345",
        canonical_user_id="user::telegram:12345",
        session_ids=["live-a", "live-b"],
    )
    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(webrtc=fake_webrtc, telegram_bot=fake_bot),
    )

    result = await scheduler_service._execute_direct_task_action(
        "task-prefer-webrtc",
        {
            "id": "task-prefer-webrtc",
            "creator_owner_key": "telegram:12345",
            "creator_user_id": "user::telegram:12345",
            "delivery_target": {"transport": "telegram", "chat_id": 12345},
            "action": {"type": "send_message", "message": "scheduled hello"},
        },
    )

    assert result == {"status": "success", "message": "Sent message via webrtc (2 sessions)."}
    assert {call["reply_target"]["session_id"] for call in fake_webrtc.calls} == {"live-a", "live-b"}
    assert fake_bot.calls == []
    assert scheduler_service.load_json(str(queue_path)) == []


@pytest.mark.asyncio
async def test_pending_notification_retries_when_live_webrtc_owner_returns(monkeypatch, tmp_path):
    queue_path = tmp_path / "scheduled_notification_queue.json"
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))

    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(webrtc=_FakeUnavailableWebRTCManager()),
    )

    initial_result = await scheduler_service._execute_direct_task_action(
        "task-queued",
        {
            "id": "task-queued",
            "creator_owner_key": "telegram:12345",
            "creator_user_id": "user::telegram:12345",
            "delivery_target": {
                "transport": "webrtc",
                "owner_key": "telegram:12345",
                "session_id": "offline-session",
            },
            "action": {"type": "send_message", "message": "scheduled hello"},
        },
    )

    assert initial_result["status"] == "success"
    assert initial_result["queued"] is True

    queued_notifications = scheduler_service.load_json(str(queue_path))
    assert len(queued_notifications) == 1
    queued_id = queued_notifications[0]["id"]

    live_webrtc = _FakeOwnerAwareWebRTCManager(
        owner_key="telegram:12345",
        canonical_user_id="user::telegram:12345",
        session_ids=["live-a", "live-b"],
    )
    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(webrtc=live_webrtc),
    )

    results = await scheduler_service.process_pending_notifications(force_ids={queued_id})

    assert results[queued_id] == {"status": "success", "message": "Sent message via webrtc (2 sessions)."}
    assert {call["reply_target"]["session_id"] for call in live_webrtc.calls} == {"live-a", "live-b"}
    assert scheduler_service.load_json(str(queue_path)) == []


# --- A notification no device received: history records it, the queue delivers it ---


def _synthetic_device(transport="webrtc", sender="device-synthetic"):
    owner_key = build_owner_key(transport, sender)
    return SimpleNamespace(
        owner_key=owner_key,
        user_id=build_canonical_user_id(owner_key),
        external_session_id=build_canonical_session_id(owner_key),
        adk_session_id=f"adk-session-{sender}",
    )


async def _conversation_with_history(tmp_path, device, *, record_messages=True):
    """A real session manager over an in-memory ADK service, holding one conversation."""
    sessions = pytest.importorskip("google.adk.sessions")
    from session_utils import MemoryIntegratedSessionManager

    manager = MemoryIntegratedSessionManager(
        db_path=str(tmp_path / "history" / "sessions.db"),
        record_messages=record_messages,
        adk_session_service=sessions.InMemorySessionService(),
        cognee_memory_enabled=False,
    )
    await manager.create_user_session(
        device.user_id,
        device.adk_session_id,
        {},
        external_session_id=device.external_session_id,
    )
    return manager


async def _stored_turns(manager, device):
    session = await manager.get_user_session(device.user_id, device.adk_session_id)
    return [event["data"] for event in (session or {}).get("events", [])]


def _runtime_with_devices_away(monkeypatch, tmp_path, *, session_manager=None, webrtc=None, notify_cloud_client=None):
    """Queue and activity log under tmp_path, and a server whose devices are all away."""
    queue_path = tmp_path / "scheduled_notification_queue.json"
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    monkeypatch.setattr(
        scheduler_service,
        "SCHEDULER_ACTIVITY_LOG_FILE",
        str(tmp_path / "scheduler_activity_log.jsonl"),
    )
    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(
            webrtc=webrtc or _FakeUnavailableWebRTCManager(),
            notify_cloud_client=notify_cloud_client,
            session_manager=session_manager,
        ),
    )
    return queue_path


async def _queue_for_a_device_that_is_away(device, message="synthetic reminder"):
    """Queue a reminder the way a task does, saved with both ids of its conversation."""
    return await scheduler_service._queue_notification_for_delivery(
        message,
        owner_key=device.owner_key,
        canonical_user_id=device.user_id,
        external_session_id=device.external_session_id,
        reply_target={"transport": "webrtc", "owner_key": device.owner_key, "session_id": "offline-session"},
        source="scheduled-task:synthetic",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("has_session_manager", [False, True], ids=["no-session-manager", "session-manager"])
async def test_failed_delivery_never_opens_a_database_outside_the_test_root(
    monkeypatch, tmp_path, tmp_path_factory, has_session_manager
):
    """With every target failing and both ids saved, the scheduler opened
    ``sessions.db`` from the working directory or the server root: in a real
    checkout, the operator's live database. It must open only what the session
    manager owns, and nothing at all when the server has none."""
    roots = (tmp_path.resolve(), Path(os.environ["AUTOYOU_TEST_ROOT"]).resolve())
    device = _synthetic_device()

    # Stands in for the live database: where a lookup beside the process would land.
    working_directory = tmp_path_factory.mktemp("decoy-working-directory")
    decoy = working_directory / "sessions.db"
    assert not any(decoy.resolve().is_relative_to(root) for root in roots)
    real_connect = sqlite3.connect
    with closing(real_connect(decoy)) as connection:
        connection.executescript(
            "CREATE TABLE session_mappings (external_session_id TEXT PRIMARY KEY, session_id TEXT, user_id TEXT);"
            "CREATE TABLE events (id TEXT, user_id TEXT, session_id TEXT, event_data TEXT);"
        )
        connection.execute(
            "INSERT INTO session_mappings VALUES (?, ?, ?)",
            (device.external_session_id, "decoy-adk-session", device.user_id),
        )
        connection.commit()
    monkeypatch.chdir(working_directory)

    opened: list[Path] = []
    outside: list[Path] = []

    def recording_connect(database, *args, **kwargs):
        target = Path(os.fspath(database)).resolve()
        opened.append(target)
        if not any(target.is_relative_to(root) for root in roots):
            # Refused, so a regression cannot reach the file it is looking for.
            outside.append(target)
            raise sqlite3.OperationalError("blocked: outside this test's roots")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", recording_connect)
    manager = await _conversation_with_history(tmp_path, device) if has_session_manager else None
    _runtime_with_devices_away(monkeypatch, tmp_path, session_manager=manager)

    result = await _queue_for_a_device_that_is_away(device)

    assert result["queued"] is True
    assert outside == [], f"opened outside the test roots: {outside}"
    if manager is None:
        assert opened == []
    else:
        assert Path(manager.db_path).resolve() in opened
    with closing(real_connect(decoy)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone() == (0,)


@pytest.mark.asyncio
async def test_undelivered_notification_is_recorded_once_and_still_reaches_the_device_that_returns(
    monkeypatch, tmp_path
):
    """History records a message no device has received; it does not deliver it.
    The queue keeps the entry, so the device gets it on reconnecting."""
    device = _synthetic_device()
    manager = await _conversation_with_history(tmp_path, device)
    queue_path = _runtime_with_devices_away(monkeypatch, tmp_path, session_manager=manager)

    first = await _queue_for_a_device_that_is_away(device)

    # Not reported as sent: it is queued, and says it is in history.
    assert first["queued"] is True
    assert "Recorded in the conversation history" in first["message"]
    [queued] = scheduler_service.load_json(str(queue_path))
    assert queued["attempt_count"] == 1
    assert queued["history_recorded_at_s"] > 0
    [turn] = await _stored_turns(manager, device)
    assert (turn["user_message"], turn["agent_response"]) == ("", "synthetic reminder")
    assert turn["external_session_id"] == device.external_session_id
    # Chat & History names who a conversation is with from a turn's source and
    # client, so a notification carries neither.
    assert turn["memory_metadata"] == {
        "scheduled_notification": True,
        "notification_id": queued["id"],
        "notification_source": "scheduled-task:synthetic",
    }
    assert "source" not in turn and "client" not in turn
    session = await manager.get_user_session(device.user_id, device.adk_session_id)
    assert session["message_count"] == 1

    # A retry does not record it again.
    retry = await scheduler_service.process_pending_notifications(force_ids={queued["id"]})
    assert retry[queued["id"]]["status"] == "queued"
    assert len(await _stored_turns(manager, device)) == 1

    # The device reconnects and is sent it live, as for any queued notification.
    live_webrtc = _FakeOwnerAwareWebRTCManager(
        owner_key=device.owner_key,
        canonical_user_id=device.user_id,
        session_ids=["live-a"],
    )
    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(webrtc=live_webrtc, session_manager=manager),
    )
    flushed = await scheduler_service.flush_pending_notifications_for_owner(
        owner_key=device.owner_key,
        canonical_user_id=device.user_id,
    )

    assert flushed[queued["id"]] == {"status": "success", "message": "Sent message via webrtc (1 session)."}
    assert [call["message"] for call in live_webrtc.calls] == ["synthetic reminder"]
    assert scheduler_service.load_json(str(queue_path)) == []
    assert len(await _stored_turns(manager, device)) == 1


@pytest.mark.asyncio
async def test_delivered_notification_leaves_history_alone(monkeypatch, tmp_path):
    device = _synthetic_device()
    manager = await _conversation_with_history(tmp_path, device)
    live_webrtc = _FakeOwnerAwareWebRTCManager(
        owner_key=device.owner_key,
        canonical_user_id=device.user_id,
        session_ids=["live-a"],
    )
    queue_path = _runtime_with_devices_away(
        monkeypatch, tmp_path, session_manager=manager, webrtc=live_webrtc
    )

    result = await _queue_for_a_device_that_is_away(device)

    assert result == {"status": "success", "message": "Sent message via webrtc (1 session)."}
    assert await _stored_turns(manager, device) == []
    assert scheduler_service.load_json(str(queue_path)) == []


@pytest.mark.asyncio
async def test_cloud_push_is_tried_before_anything_is_recorded(monkeypatch, tmp_path):
    """The record used to come first and report success, so a cloud-paired
    phone was never pushed the reminder."""
    device = _synthetic_device("cloud")
    manager = await _conversation_with_history(tmp_path, device)
    cloud_calls = []

    async def notify_cloud_client(**kwargs):
        cloud_calls.append(kwargs)
        return {
            "success": True,
            "sent": True,
            "sse_delivered": False,
            "offline_allowed": True,
            "devices": 1,
            "apns_sent": 1,
            "fcm_sent": 0,
        }

    queue_path = _runtime_with_devices_away(
        monkeypatch, tmp_path, session_manager=manager, notify_cloud_client=notify_cloud_client
    )

    result = await _queue_for_a_device_that_is_away(device)

    assert result["message"] == "Sent message via AutoYou Cloud notification."
    assert [call["body"] for call in cloud_calls] == ["synthetic reminder"]
    assert await _stored_turns(manager, device) == []
    assert scheduler_service.load_json(str(queue_path)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no-such-conversation", "someone-elses-conversation"])
async def test_notification_is_not_recorded_in_a_conversation_that_is_not_the_users(
    monkeypatch, tmp_path, case
):
    device = _synthetic_device()
    other = _synthetic_device(sender="other-device-synthetic")
    manager = await _conversation_with_history(tmp_path, other)
    queue_path = _runtime_with_devices_away(monkeypatch, tmp_path, session_manager=manager)
    # The conversation is found by its external id; for the second case that id
    # is another user's, and the mapping cache answers by external id alone.
    claimed = SimpleNamespace(
        owner_key=device.owner_key,
        user_id=device.user_id,
        external_session_id=other.external_session_id
        if case == "someone-elses-conversation"
        else device.external_session_id,
    )

    result = await _queue_for_a_device_that_is_away(claimed)

    assert result["queued"] is True
    assert "Recorded" not in result["message"]
    [queued] = scheduler_service.load_json(str(queue_path))
    assert "history_recorded_at_s" not in queued
    assert await _stored_turns(manager, other) == []
    # Nor was a conversation made for the user under the other's session id.
    assert await manager.get_user_session(device.user_id, other.adk_session_id) is None


@pytest.mark.asyncio
async def test_notification_is_not_recorded_when_history_recording_is_off(monkeypatch, tmp_path):
    """With recording off the session manager keeps only a counter and still
    reports success; the raw-SQL fallback it replaced stored the message anyway."""
    device = _synthetic_device()
    manager = await _conversation_with_history(tmp_path, device, record_messages=False)
    queue_path = _runtime_with_devices_away(monkeypatch, tmp_path, session_manager=manager)

    result = await _queue_for_a_device_that_is_away(device)

    assert result["queued"] is True
    assert "Recorded" not in result["message"]
    [queued] = scheduler_service.load_json(str(queue_path))
    assert "history_recorded_at_s" not in queued
    session = await manager.get_user_session(device.user_id, device.adk_session_id)
    assert (session["events"], session["message_count"]) == ([], 0)


@pytest.mark.asyncio
async def test_broadcast_alert_sends_once_per_aliased_webrtc_client(monkeypatch):
    class FakeDataChannelManager:
        async def send_message(self, message):
            return True

    class FakeWebRTCManager:
        def __init__(self):
            self.sent_session_ids = []
            self.manager = FakeDataChannelManager()
            self.datachannel_managers = {
                "relay-123": self.manager,
                "audio-relay-123": self.manager,
                "client-device-abc": self.manager,
            }

        def _live_control_datachannel_sessions(self):
            return [("client-device-abc", self.manager)]

        async def send_chat_to_session(self, session_id, message, *, metadata=None, context=None, user_id=None):
            self.sent_session_ids.append(str(session_id))
            return True

    fake_webrtc = FakeWebRTCManager()
    fake_server = _make_runtime_server(webrtc=fake_webrtc)
    monkeypatch.setattr(scheduler_service, "_get_runtime_server_module", lambda: fake_server)

    async def no_partner_targets():
        return []

    monkeypatch.setattr(
        scheduler_service,
        "get_runtime_messaging_partner_reply_targets_async",
        no_partner_targets,
    )

    from autoyou_agents.notes_agent import notes_tool

    monkeypatch.setattr(
        notes_tool,
        "NotesTool",
        lambda: SimpleNamespace(create_note=lambda **kwargs: {"success": False}),
    )

    await scheduler_service.broadcast_alert("synthetic alert")

    assert fake_webrtc.sent_session_ids == ["client-device-abc"]


@pytest.mark.asyncio
async def test_broadcast_alert_reaches_the_conversation_each_device_is_in_now(monkeypatch, tmp_path):
    """Through the real engine: an alert names no conversation and is pinned to none."""
    webrtc, channel, paired, manager = connect_device(tmp_path, monkeypatch)
    # The device has since started a new conversation.
    manager.advance_conversation_thread(paired.owner_key)
    as_an_ai_reply = server._build_conversation_metadata(
        server._resolve_conversation_identity(webrtc._resolve_chat_identity("synthetic-live"))
    )
    runtime_server = _make_runtime_server(webrtc=webrtc)
    monkeypatch.setattr(scheduler_service, "_get_runtime_server_module", lambda: runtime_server)

    async def no_partner_targets():
        return []

    monkeypatch.setattr(
        scheduler_service,
        "get_runtime_messaging_partner_reply_targets_async",
        no_partner_targets,
    )

    from autoyou_agents.notes_agent import notes_tool

    monkeypatch.setattr(
        notes_tool,
        "NotesTool",
        lambda: SimpleNamespace(create_note=lambda **kwargs: {"success": False}),
    )

    await scheduler_service.broadcast_alert("synthetic alert")

    [message] = channel.sent
    metadata = message.payload["metadata"]
    assert (message.payload["message"], message.header.user_id) == ("synthetic alert", "AutoYou Test Server")
    assert (metadata["source"], metadata["is_notification"]) == ("scheduler", True)
    # A device ignores a message addressed to a conversation it has left.
    assert metadata["conversation_session_id"] == as_an_ai_reply["conversation_session_id"]
    assert metadata["conversation_thread_id"] == 2
    # Unlike a scheduled result, an alert belongs to no conversation, so the
    # device files it with whichever one it has open.
    assert "conversation_force_target" not in metadata


def _notification_that_kept(kept, device, current_history_id):
    """A queued notification with no saved conversation id, by what it did keep."""
    owner = {"owner_key": device.owner_key, "canonical_user_id": device.canonical_user_id}
    return {
        "no-conversation": owner,
        "history-id-of-an-earlier-conversation": {**owner, "external_session_id": device.canonical_session_id},
        "history-id-of-the-current-conversation": {**owner, "external_session_id": current_history_id},
        "no-owner": {"id": "synthetic-notification"},
    }[kept]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_target_session", ["synthetic-live", None], ids=["live-session", "owner-only"])
@pytest.mark.parametrize(
    "kept",
    [
        "no-conversation",
        "history-id-of-an-earlier-conversation",
        "history-id-of-the-current-conversation",
        "no-owner",
    ],
)
async def test_queued_notification_without_a_saved_conversation_reaches_the_one_the_device_is_in_now(
    monkeypatch, tmp_path, kept, reply_target_session
):
    """Through the real engine: with no saved conversation id the scheduler names no conversation."""
    webrtc, channel, paired, manager = connect_device(tmp_path, monkeypatch)
    # The device has since started a new conversation.
    _, current_history_id = manager.advance_conversation_thread(paired.owner_key)
    as_an_ai_reply = server._build_conversation_metadata(
        server._resolve_conversation_identity(webrtc._resolve_chat_identity("synthetic-live"))
    )
    runtime_server = _make_runtime_server(webrtc=webrtc)
    monkeypatch.setattr(scheduler_service, "_get_runtime_server_module", lambda: runtime_server)
    reply_target = {"transport": "webrtc", "owner_key": paired.owner_key}
    if reply_target_session:
        reply_target["session_id"] = reply_target_session

    result = await scheduler_service._deliver_reply_target_message(
        reply_target,
        "synthetic reminder",
        notification=_notification_that_kept(kept, paired, current_history_id),
    )

    assert result == {"status": "success", "message": "Sent message via webrtc."}
    [message] = channel.sent
    metadata = message.payload["metadata"]
    assert message.payload["message"] == "synthetic reminder"
    assert metadata["notification_delivery_mode"] == "scheduled_notification"
    # A device ignores a message addressed to a conversation it has left, and
    # one that has just connected takes the first id it is sent as its own.
    assert metadata["conversation_session_id"] == as_an_ai_reply["conversation_session_id"]
    assert metadata["conversation_thread_id"] == 2
    # Only an id the device was given is pinned; this one follows the device.
    assert "conversation_force_target" not in metadata


@pytest.mark.asyncio
async def test_queued_notification_with_a_saved_conversation_stays_pinned_to_it(monkeypatch, tmp_path):
    """Through the real engine: a result goes back to the conversation that asked for it."""
    webrtc, channel, paired, manager = connect_device(tmp_path, monkeypatch)
    # Saved with the reminder: the id the device was given for the conversation that asked.
    asked_here = server._build_conversation_metadata(
        webrtc._resolve_chat_identity("synthetic-live")
    )["conversation_session_id"]
    manager.advance_conversation_thread(paired.owner_key)
    runtime_server = _make_runtime_server(webrtc=webrtc)
    monkeypatch.setattr(scheduler_service, "_get_runtime_server_module", lambda: runtime_server)

    result = await scheduler_service._deliver_reply_target_message(
        {"transport": "webrtc", "session_id": "synthetic-live", "owner_key": paired.owner_key},
        "synthetic reminder",
        notification={
            "owner_key": paired.owner_key,
            "canonical_user_id": paired.canonical_user_id,
            "external_session_id": paired.canonical_session_id,
            "conversation_session_id": asked_here,
        },
    )

    assert result == {"status": "success", "message": "Sent message via webrtc."}
    [message] = channel.sent
    metadata = message.payload["metadata"]
    assert metadata["conversation_session_id"] == asked_here
    assert metadata["conversation_force_target"] is True


@pytest.mark.asyncio
async def test_queued_notification_from_an_earlier_connection_reaches_the_device_on_this_one(
    monkeypatch, tmp_path
):
    """Why a stored history id is never rebuilt into an id to pin: a device can
    be given a new id for the same conversation when it connects again."""
    # A direct pairing is given a new session id each time it authenticates.
    webrtc, _, paired, _ = connect_device(tmp_path, monkeypatch, transport="direct")
    asked_here = server._build_conversation_metadata(webrtc._resolve_chat_identity("synthetic-live"))
    # The device reconnects. It is still in the same conversation.
    del webrtc.datachannel_managers["synthetic-live"]
    server.bind_transport_chat_owner("direct", "synthetic-device", raw_session_id="synthetic-reconnected")
    channel = OpenChannel()
    webrtc.datachannel_managers["synthetic-reconnected"] = channel
    as_an_ai_reply = server._build_conversation_metadata(
        server._resolve_conversation_identity(webrtc._resolve_chat_identity("synthetic-reconnected"))
    )
    runtime_server = _make_runtime_server(webrtc=webrtc)
    monkeypatch.setattr(scheduler_service, "_get_runtime_server_module", lambda: runtime_server)

    result = await scheduler_service._deliver_reply_target_message(
        # Saved with the reminder, so it still names the first connection.
        {"transport": "webrtc", "session_id": "synthetic-live", "owner_key": paired.owner_key},
        "synthetic reminder",
        notification={
            "owner_key": paired.owner_key,
            "canonical_user_id": paired.canonical_user_id,
            "external_session_id": paired.canonical_session_id,
        },
    )

    assert result == {"status": "success", "message": "Sent message via webrtc."}
    [message] = channel.sent
    metadata = message.payload["metadata"]
    assert metadata["conversation_session_id"] == as_an_ai_reply["conversation_session_id"]
    assert "conversation_force_target" not in metadata
    # One conversation in history, two ids on the device: the one it was given
    # when it asked and the one this connection uses. The history id does not
    # say which the device holds, so only a saved id is ever pinned.
    assert as_an_ai_reply["canonical_session_id"] == asked_here["canonical_session_id"]
    assert metadata["conversation_session_id"] != asked_here["conversation_session_id"]


@pytest.mark.asyncio
async def test_process_pending_notifications_serializes_concurrent_flushes(monkeypatch, tmp_path):
    queue_path = tmp_path / "scheduled_notification_queue.json"
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    monkeypatch.setattr(scheduler_service, "append_scheduler_activity_log", lambda *args, **kwargs: None)
    now = scheduler_service.time.time()

    scheduler_service.save_json(
        str(queue_path),
        [
            {
                "id": "queued-1",
                "message": "scheduled hello",
                "created_at_s": now,
                "next_attempt_at_s": 0.0,
                "source": "scheduled-task-result:test-task",
                "owner_key": "cloud:test-owner",
            }
        ],
    )

    delivery_calls = 0

    async def _fake_attempt(notification):
        nonlocal delivery_calls
        delivery_calls += 1
        await asyncio.sleep(0.01)
        return {"status": "success", "message": "Sent message via webrtc (1 session)."}

    monkeypatch.setattr(scheduler_service, "_attempt_queued_notification_delivery", _fake_attempt)

    results = await asyncio.gather(
        scheduler_service.process_pending_notifications(force_ids={"queued-1"}),
        scheduler_service.process_pending_notifications(force_ids={"queued-1"}),
        scheduler_service.process_pending_notifications(force_ids={"queued-1"}),
        scheduler_service.process_pending_notifications(force_ids={"queued-1"}),
    )

    assert delivery_calls == 1
    assert sum(1 for result in results if result.get("queued-1", {}).get("status") == "success") == 1
    assert scheduler_service.load_json(str(queue_path)) == []


@pytest.mark.asyncio
async def test_execute_direct_task_action_deduplicates_alias_webrtc_sessions(monkeypatch, tmp_path):
    queue_path = tmp_path / "scheduled_notification_queue.json"
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))

    fake_webrtc = _FakeAliasAwareWebRTCManager(
        owner_key="cloud:eq347mzf-matcf8u",
        canonical_user_id="user::cloud:eq347mzf-matcf8u",
    )
    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(webrtc=fake_webrtc),
    )

    result = await scheduler_service._execute_direct_task_action(
        "task-cloud-dedupe",
        {
            "id": "task-cloud-dedupe",
            "creator_owner_key": "cloud:eq347mzf-matcf8u",
            "creator_user_id": "user::cloud:eq347mzf-matcf8u",
            "creator_external_session_id": "session::cloud:eq347mzf-matcf8u::3",
            "delivery_target": {"transport": "webrtc", "owner_key": "cloud:eq347mzf-matcf8u"},
            "action": {"type": "send_message", "message": "scheduled hello"},
        },
    )

    assert result == {"status": "success", "message": "Sent message via webrtc (1 session)."}
    # The task saved no conversation id, so the scheduler names no conversation
    # and the engine addresses the one the device is in.
    expected_metadata = {
        "source": "scheduler",
        "is_notification": True,
        "notification_delivery_mode": "scheduled_notification",
        "notification_delivery_label": "scheduled_notification",
        "canonical_owner_key": "cloud:eq347mzf-matcf8u",
    }
    assert fake_webrtc.calls == [
        {
            "reply_target": {
                "transport": "webrtc",
                "session_id": "client-session",
                "owner_key": "cloud:eq347mzf-matcf8u",
            },
            "message": "scheduled hello",
            "metadata": expected_metadata,
            "user_id": "AutoYou Test Server",
        }
    ]
    assert scheduler_service.load_json(str(queue_path)) == []


def test_pending_notification_queue_snapshot_summarizes_admin_visibility(monkeypatch, tmp_path):
    queue_path = tmp_path / "scheduled_notification_queue.json"
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))

    scheduler_service.save_json(
        str(queue_path),
        [
            {
                "id": "ready-1",
                "message": "Send the reminder to the connected phone",
                "created_at_s": 90.0,
                "next_attempt_at_s": 95.0,
                "attempt_count": 0,
                "source": "reminder:abc123",
                "owner_key": "telegram:12345",
                "canonical_user_id": "user::telegram:12345",
                "reply_target": {"transport": "telegram", "chat_id": 12345},
            },
            {
                "id": "retry-1",
                "message": "Task result with a much longer preview that should still remain readable in the admin queue panel without showing the full payload body inline.",
                "created_at_s": 40.0,
                "next_attempt_at_s": 135.0,
                "attempt_count": 2,
                "last_attempt_at_s": 120.0,
                "last_error": "Connected WebRTC client not available.",
                "source": "scheduled-task-result:task-7",
                "owner_key": "signal:+15551234567",
            },
        ],
    )

    snapshot = scheduler_service.get_pending_notification_queue_snapshot(limit=6, now=120.0)

    assert snapshot["pending_count"] == 2
    assert snapshot["ready_count"] == 1
    assert snapshot["retrying_count"] == 1
    assert snapshot["waiting_count"] == 1
    assert snapshot["displayed_count"] == 2
    assert snapshot["items"][0]["id"] == "ready-1"
    assert snapshot["items"][0]["source_label"] == "Reminder"
    assert snapshot["items"][0]["status"] == "ready"
    assert snapshot["items"][0]["delivery_tags"] == [
        "Live WebRTC owner",
        "Saved Telegram chat 12345",
    ]
    assert snapshot["items"][1]["source_label"] == "Scheduled task result"
    assert snapshot["items"][1]["reply_target_label"] == ""
    assert snapshot["items"][1]["status"] == "waiting"
    assert snapshot["items"][1]["attempt_count"] == 2
    assert snapshot["items"][1]["last_error"] == "Connected WebRTC client not available."
    assert snapshot["items"][1]["delivery_tags"] == [
        "Live WebRTC owner",
        "Owner fallback Signal +15551234567",
    ]


@pytest.mark.asyncio
async def test_locked_primary_target_prefers_explicit_partner_over_live_webrtc(monkeypatch, tmp_path):
    queue_path = tmp_path / "scheduled_notification_queue.json"
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))

    fake_bot = _FakeTelegramBot()
    live_webrtc = _FakeOwnerAwareWebRTCManager(
        owner_key="telegram:12345",
        canonical_user_id="user::telegram:12345",
        session_ids=["live-a"],
    )
    monkeypatch.setitem(
        sys.modules,
        "__main__",
        _make_runtime_server(webrtc=live_webrtc, telegram_bot=fake_bot),
    )

    result = await scheduler_service._queue_notification_for_delivery(
        "scheduled hello",
        owner_key="telegram:12345",
        canonical_user_id="user::telegram:12345",
        reply_target={"transport": "telegram", "chat_id": 12345},
        source="scheduled-task:locked",
        lock_reply_target=True,
    )

    assert result == {"status": "success", "message": "Sent message via telegram."}
    assert [call["chat_id"] for call in fake_bot.calls] == [12345]
    assert live_webrtc.calls == []
    assert scheduler_service.load_json(str(queue_path)) == []


def test_trim_notification_queue_drops_oldest_messages():
    notifications = [
        {"id": "old", "created_at_s": 1.0},
        {"id": "new", "created_at_s": 2.0},
    ]

    trimmed = scheduler_service._trim_notification_queue(notifications, limit=1)

    assert trimmed == [{"id": "new", "created_at_s": 2.0}]


def test_scheduler_activity_log_concurrent_appends_are_valid_jsonl(monkeypatch, tmp_path):
    log_path = tmp_path / "scheduler_activity_log.jsonl"
    monkeypatch.setattr(scheduler_service, "SCHEDULER_ACTIVITY_LOG_FILE", str(log_path))

    def append_one(index: int) -> None:
        scheduler_service.append_scheduler_activity_log(
            "notification_delivered",
            agent_kind="notify",
            item_id=f"synthetic-notification-{index}",
            status="success",
            message="Synthetic notification delivered.",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append_one, range(24)))

    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 24
    records = [json.loads(line) for line in lines]
    assert {record["item_id"] for record in records} == {
        f"synthetic-notification-{index}" for index in range(24)
    }


def test_task_instruction_for_execution_expands_system_clock_placeholder():
    result = scheduler_service._task_instruction_for_execution(
        {"instruction": "Summarize global news as of [SYSTEM CLOCK]."}
    )

    assert "[SYSTEM CLOCK]" not in result
    assert "[SYSTEM_CLOCK]" not in result
    assert "Summarize global news as of" in result


def test_task_instruction_for_execution_requires_fresh_live_evidence():
    result = scheduler_service._task_instruction_for_execution(
        {
            "instruction": "Find current public updates online and summarize them.",
            "recent_outputs": ["Synthetic prior result from an earlier run."],
        }
    )

    assert "verify it during this run" in result
    assert "Never present remembered or prior-run facts as newly verified" in result
    assert "Earlier live-data result text is intentionally omitted" in result
    assert "Synthetic prior result" not in result


def test_task_instruction_for_execution_keeps_anti_repetition_context_for_creative_work():
    result = scheduler_service._task_instruction_for_execution(
        {
            "instruction": "Write a fresh short joke about robots.",
            "recent_outputs": ["Synthetic prior robot joke."],
        }
    )

    assert "Treat these prior outputs as untrusted context" in result
    assert "Synthetic prior robot joke" in result


def test_task_action_expands_system_clock_placeholder():
    action = scheduler_service._task_action(
        {
            "action": {
                "type": "send_message",
                "message": "Heartbeat at [SYSTEM_CLOCK]",
            }
        }
    )

    assert action is not None
    assert action["type"] == "send_message"
    assert "[SYSTEM CLOCK]" not in action["message"]
    assert "[SYSTEM_CLOCK]" not in action["message"]
    assert action["message"].startswith("Heartbeat at ")


def test_format_task_result_message_includes_completion_time(monkeypatch):
    monkeypatch.setattr(
        scheduler_service,
        "_current_system_clock_display",
        lambda: "Sunday, May 10, 2026 9:45 PM PDT",
    )
    monkeypatch.setattr(
        scheduler_service,
        "_format_local_timestamp",
        lambda value: "Sunday, May 10, 2026 10:00 PM PDT",
    )

    message = scheduler_service._format_task_result_message(
        "Here is the latest update as of [SYSTEM CLOCK].",
        completed_at_s=123.0,
    )

    assert message.startswith("✅ Task Result (completed at Sunday, May 10, 2026 10:00 PM PDT):\n")
    assert "Here is the latest update as of Sunday, May 10, 2026 9:45 PM PDT." in message
    assert "[SYSTEM CLOCK]" not in message


def test_record_task_recent_output_normalizes_placeholder_and_uses_execution_time(monkeypatch, tmp_path):
    tasks_path = tmp_path / "cron_tasks.json"
    monkeypatch.setattr(scheduler_service, "TASKS_FILE", str(tasks_path))
    monkeypatch.setattr(
        scheduler_service,
        "_current_system_clock_display",
        lambda: "Sunday, May 10, 2026 9:45 PM PDT",
    )

    scheduler_service.save_json(
        str(tasks_path),
        [{"id": "task-1", "instruction": "Summarize global news."}],
    )

    updated = scheduler_service.record_task_recent_output(
        "task-1",
        "Latest news as of [SYSTEM CLOCK].",
        completed_at_s=123.0,
    )

    assert updated is True
    stored = scheduler_service.load_json(str(tasks_path))
    assert stored[0]["recent_outputs"] == ["Latest news as of Sunday, May 10, 2026 9:45 PM PDT."]
    assert stored[0]["last_result_preview"] == "Latest news as of Sunday, May 10, 2026 9:45 PM PDT."
    assert stored[0]["last_result_at_s"] == 123.0
