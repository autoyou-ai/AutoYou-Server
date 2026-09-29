# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-2ecf38a3b24017a10d0f138a


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import builtins
import os
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-2ecf38a3b24017a10d0f138a"


ensure_repo_on_path()

from autoyou_agents.shared_tools import scheduler_mission_control as mission_control
from shared import scheduler_service


def test_agent_ui_sessions_path_respects_autoyou_test_root(autoyou_test_root):
    expected = autoyou_test_root / "AutoYou" / "agent_ui_sessions.json"

    assert os.path.abspath(mission_control._AGENT_UI_SESSIONS_FILE) == str(expected.resolve())


def test_agent_ui_sessions_path_uses_user_data_for_packaged_runtime_without_platform_runtime(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOYOU_AGENT_UI_SESSIONS_PATH", raising=False)
    monkeypatch.delenv("AUTOYOU_TEST_ROOT", raising=False)
    runtime_module_root = tmp_path / "bundle" / "runtime_modules" / "autoyou_agents" / "shared_tools"
    runtime_module_root.mkdir(parents=True)
    fallback_file = tmp_path / "user-data" / "agent_ui_sessions.json"

    monkeypatch.setattr(mission_control, "MODULE_ROOT", runtime_module_root)
    monkeypatch.setattr(mission_control, "_fallback_agent_ui_sessions_file", lambda: fallback_file)

    real_import = builtins.__import__

    def blocked_platform_runtime_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "shared.platform_runtime":
            raise ImportError("platform runtime unavailable during early packaged startup")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_platform_runtime_import)

    assert mission_control._resolve_agent_ui_sessions_file() == str(fallback_file.resolve())


def _enable_open_mode(monkeypatch):
    monkeypatch.setattr(
        mission_control,
        "_get_agent_security_settings",
        lambda agent_name: {"auth_mode": "open", "session_ttl_days": 30},
    )
    monkeypatch.setattr(
        mission_control,
        "_totp_capabilities",
        lambda: {"totp_configured": False},
    )
    monkeypatch.setattr(
        mission_control,
        "_build_target_catalog",
        lambda: {
            "count": 1,
            "options": [
                {
                    "id": "telegram:12345",
                    "transport": "telegram",
                    "label": "Telegram",
                    "detail": "12345",
                    "connected": True,
                    "owner_key": "telegram:12345",
                    "canonical_user_id": "user::telegram:12345",
                    "reply_target": {"transport": "telegram", "chat_id": 12345},
                }
            ],
        },
    )


def test_tasks_mission_control_open_mode_crud(monkeypatch, tmp_path):
    tasks_path = tmp_path / "cron_tasks.json"
    queue_path = tmp_path / "scheduled_notification_queue.json"

    monkeypatch.setattr(scheduler_service, "TASKS_FILE", str(tasks_path))
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    monkeypatch.setattr(mission_control, "TASKS_FILE", str(tasks_path))
    monkeypatch.setattr(mission_control, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    _enable_open_mode(monkeypatch)

    client = TestClient(mission_control.create_scheduler_mission_control_app("tasks"))

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["auth"]["authenticated"] is True

    created = client.post(
        "/api/items",
        json={
            "instruction": "Generate a fresh joke and send it to the user.",
            "interval_minutes": 5,
            "enabled": True,
            "owner_key": "telegram:12345",
            "delivery_target": {"transport": "telegram", "chat_id": 12345},
        },
    )
    assert created.status_code == 200
    created_item = created.json()["item"]
    assert created_item["lock_reply_target"] is True

    updated = client.patch(
        f"/api/items/{created_item['id']}",
        json={"enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["item"]["enabled"] is False

    cleared = client.post("/api/items/delete-all")
    assert cleared.status_code == 200
    assert cleared.json()["removed_count"] == 1


def test_notify_mission_control_open_mode_crud(monkeypatch, tmp_path):
    reminders_path = tmp_path / "reminders.json"
    queue_path = tmp_path / "scheduled_notification_queue.json"

    monkeypatch.setattr(scheduler_service, "REMINDERS_FILE", str(reminders_path))
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    monkeypatch.setattr(mission_control, "REMINDERS_FILE", str(reminders_path))
    monkeypatch.setattr(mission_control, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    _enable_open_mode(monkeypatch)

    client = TestClient(mission_control.create_scheduler_mission_control_app("notify"))

    created = client.post(
        "/api/items",
        json={
            "message": "Stretch for two minutes.",
            "target_time_iso": "2026-05-01T15:30:00Z",
            "owner_key": "telegram:12345",
            "delivery_target": {"transport": "telegram", "chat_id": 12345},
        },
    )
    assert created.status_code == 200
    created_item = created.json()["item"]
    assert created_item["delivery_target_label"] == "Telegram 12345"

    updated = client.patch(
        f"/api/items/{created_item['id']}",
        json={"message": "Stretch and hydrate."},
    )
    assert updated.status_code == 200
    assert updated.json()["item"]["message"] == "Stretch and hydrate."

    deleted = client.delete(f"/api/items/{created_item['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_tasks_dashboard_exposes_last_result_timestamp(monkeypatch, tmp_path):
    tasks_path = tmp_path / "cron_tasks.json"
    queue_path = tmp_path / "scheduled_notification_queue.json"

    monkeypatch.setattr(scheduler_service, "TASKS_FILE", str(tasks_path))
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    monkeypatch.setattr(mission_control, "TASKS_FILE", str(tasks_path))
    monkeypatch.setattr(mission_control, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    _enable_open_mode(monkeypatch)

    scheduler_service.save_json(
        str(tasks_path),
        [
            {
                "id": "task-1",
                "instruction": "Summarize breaking news.",
                "interval_minutes": 60,
                "enabled": True,
                "last_run_s": 100.0,
                "created_at_s": 50.0,
                "creator_owner_key": "telegram:12345",
                "delivery_target": {"transport": "telegram", "chat_id": 12345},
                "last_result_preview": "Normalized summary preview",
                "last_result_at_s": 123.0,
            }
        ],
    )

    client = TestClient(mission_control.create_scheduler_mission_control_app("tasks"))
    dashboard = client.get("/api/dashboard")
    # from __debug_provenance_a__ import schedule

    assert dashboard.status_code == 200
    item = dashboard.json()["items"][0]
    assert item["last_result_preview"] == "Normalized summary preview"
    assert item["last_result_at_s"] == 123.0
    assert item["delivery_target_label"] == "Telegram 12345"


def test_tasks_live_summary_empty_state(monkeypatch, tmp_path):
    tasks_path = tmp_path / "cron_tasks.json"
    queue_path = tmp_path / "scheduled_notification_queue.json"

    monkeypatch.setattr(mission_control, "TASKS_FILE", str(tasks_path))
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    scheduler_service.save_json(str(tasks_path), [])
    scheduler_service.save_json(str(queue_path), [])

    summary = mission_control.build_tasks_live_summary(now=1000.0)

    assert summary["success"] is True
    assert summary["tasks"] == {
        "total_count": 0,
        "enabled_count": 0,
        "paused_count": 0,
        "due_soon_count": 0,
        "pinned_count": 0,
        "next_run_at_s": None,
        "next_run_task_id": "",
        "next_run_preview": "",
        "last_result_at_s": None,
        "last_result_task_id": "",
        "last_result_preview": "",
    }
    assert summary["queue"]["pending_count"] == 0
    assert summary["queue"]["ready_count"] == 0


def test_target_catalog_dedupes_webrtc_aliases_with_server_helper(monkeypatch):
    class FakeDataChannelManager:
        session_id = "client-device-abc"

        async def send_message(self, message):
            return True

    class FakeWebRTCManager:
        def __init__(self):
            self.manager = FakeDataChannelManager()
            self.datachannel_managers = {
                "relay-123": self.manager,
                "client-device-abc": self.manager,
            }

        def _unique_datachannel_manager_entries(self, *, require_send_message=False):
            assert require_send_message is True
            return [("client-device-abc", self.manager)]

        def _resolve_chat_identity(self, session_id):
            return SimpleNamespace(
                owner_key="cloud:client-device-abc",
                canonical_user_id="user::cloud:client-device-abc",
            )

    monkeypatch.setattr(
        mission_control,
        "_runtime_server",
        lambda: SimpleNamespace(WEBRTC=FakeWebRTCManager()),
    )

    targets = mission_control._collect_live_webrtc_targets()

    assert len(targets) == 1
    assert targets[0]["reply_target"] == {
        "transport": "webrtc",
        "session_id": "client-device-abc",
        "owner_key": "cloud:client-device-abc",
    }


def test_tasks_live_summary_mixed_tasks_and_queue_counts(monkeypatch, tmp_path):
    tasks_path = tmp_path / "cron_tasks.json"
    queue_path = tmp_path / "scheduled_notification_queue.json"

    monkeypatch.setattr(mission_control, "TASKS_FILE", str(tasks_path))
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    scheduler_service.save_json(
        str(tasks_path),
        [
            {
                "id": "task-alpha",
                "instruction": "Summarize synthetic release notes.",
                "interval_minutes": 5,
                "enabled": True,
                "last_run_s": 900.0,
                "created_at_s": 800.0,
                "creator_owner_key": "telegram:5550100",
                "delivery_target": {"transport": "telegram", "chat_id": 5550100},
                "lock_reply_target": True,
                "last_result_preview": "Earlier synthetic task output",
                "last_result_at_s": 960.0,
            },
            {
                "id": "task-paused",
                "instruction": "Paused synthetic task.",
                "interval_minutes": 60,
                "enabled": False,
                "last_run_s": 850.0,
                "created_at_s": 750.0,
                "last_result_preview": "Latest synthetic task output",
                "last_result_at_s": 990.0,
            },
        ],
    )
    scheduler_service.save_json(
        str(queue_path),
        [
            {
                "id": "queued-ready",
                "message": "Synthetic scheduled task result.",
                "created_at_s": 970.0,
                "next_attempt_at_s": 995.0,
                "attempt_count": 0,
                "source": "scheduled-task-result:task-alpha",
                "owner_key": "telegram:5550100",
                "canonical_user_id": "user::telegram:5550100",
                "reply_target": {"transport": "telegram", "chat_id": 5550100},
            },
            {
                "id": "queued-retry",
                "message": "Synthetic retry payload.",
                "created_at_s": 930.0,
                "next_attempt_at_s": 1100.0,
                "attempt_count": 1,
                "source": "scheduled-task-result:task-paused",
                "owner_key": "telegram:5550101",
            },
        ],
    )

    summary = mission_control.build_tasks_live_summary(queue_limit=6, now=1000.0)

    assert summary["tasks"]["total_count"] == 2
    assert summary["tasks"]["enabled_count"] == 1
    assert summary["tasks"]["paused_count"] == 1
    assert summary["tasks"]["due_soon_count"] == 1
    assert summary["tasks"]["pinned_count"] == 1
    assert summary["tasks"]["next_run_at_s"] == 1200.0
    assert summary["tasks"]["next_run_task_id"] == "task-alpha"
    assert summary["tasks"]["last_result_at_s"] == 990.0
    assert summary["tasks"]["last_result_task_id"] == "task-paused"
    assert summary["tasks"]["last_result_preview"] == "Latest synthetic task output"
    assert summary["queue"]["pending_count"] == 2
    assert summary["queue"]["ready_count"] == 1
    assert summary["queue"]["retrying_count"] == 1
    assert summary["queue"]["waiting_count"] == 1


def test_target_catalog_deduplicates_alias_webrtc_session_and_adds_live_transport_fallback(monkeypatch):
    manager = SimpleNamespace(session_id="client-session", send_message=lambda message: True)
    fake_webrtc = SimpleNamespace(
        datachannel_managers={
            "relay-session": manager,
            "client-session": manager,
        },
        _resolve_chat_identity=lambda session_id: SimpleNamespace(
            owner_key="telegram:5550001001",
            canonical_user_id="user::telegram:5550001001",
        ),
    )

    monkeypatch.setattr(
        mission_control,
        "_runtime_server",
        lambda: SimpleNamespace(
            WEBRTC=fake_webrtc,
            _get_active_telegram_bot=lambda: object(),
            STATE=SimpleNamespace(whatsapp_service=None, signal_service=None),
        ),
    )
    monkeypatch.setattr(mission_control, "load_json", lambda path: [])
    monkeypatch.setattr(
        mission_control,
        "get_session_execution_manager",
        lambda: SimpleNamespace(_owner_aliases={}),
    )

    catalog = mission_control._build_target_catalog()

    assert catalog["count"] == 2
    options_by_id = {option["id"]: option for option in catalog["options"]}
    assert set(options_by_id) == {"telegram:5550001001", "webrtc:telegram:5550001001"}
    assert options_by_id["webrtc:telegram:5550001001"]["reply_target"] == {
        "transport": "webrtc",
        "session_id": "client-session",
        "owner_key": "telegram:5550001001",
    }
    assert options_by_id["webrtc:telegram:5550001001"]["detail"] == "telegram:5550001001"
