# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-af531a615c3744f1084a9cd3


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-af531a615c3744f1084a9cd3"

import time

from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server
from autoyou_agents.shared_tools import scheduler_mission_control as mission_control
from shared import scheduler_service


def test_scheduler_live_summary_requires_admin_auth():
    with TestClient(server.admin_app) as client:
        response = client.get("/api/scheduler/live-summary")

    assert response.status_code == 401
    assert response.json()["success"] is False


def test_scheduler_live_summary_returns_authenticated_task_counts(monkeypatch, tmp_path):
    tasks_path = tmp_path / "cron_tasks.json"
    queue_path = tmp_path / "scheduled_notification_queue.json"

    monkeypatch.setattr(mission_control, "TASKS_FILE", str(tasks_path))
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(queue_path))
    scheduler_service.save_json(
        str(tasks_path),
        [
            {
                "id": "task-alpha",
                "instruction": "Summarize synthetic status updates.",
                "interval_minutes": 30,
                "enabled": True,
                "last_run_s": time.time() - 300.0,
                "created_at_s": time.time() - 900.0,
                "creator_owner_key": "telegram:5550100",
                "last_result_preview": "Synthetic status summary",
                "last_result_at_s": time.time() - 120.0,
            },
            {
                "id": "task-paused",
                "instruction": "Paused synthetic status task.",
                "interval_minutes": 60,
                "enabled": False,
                "last_run_s": time.time() - 600.0,
                "created_at_s": time.time() - 1200.0,
            },
        ],
    )
    scheduler_service.save_json(
        str(queue_path),
        [
            {
                "id": "queued-ready",
                "message": "Synthetic queued task result.",
                "created_at_s": time.time() - 60.0,
                "next_attempt_at_s": time.time() - 1.0,
                "attempt_count": 0,
                "source": "scheduled-task-result:task-alpha",
                "owner_key": "telegram:5550100",
                "canonical_user_id": "user::telegram:5550100",
            }
        ],
    )
    token = "scheduler-live-summary-test"
    monkeypatch.setitem(server.ADMIN_API_TOKENS, token, time.time() + 60.0)

    with TestClient(server.admin_app) as client:
        response = client.get(
            "/api/scheduler/live-summary",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["tasks"]["total_count"] == 2
    assert payload["tasks"]["enabled_count"] == 1
    assert payload["tasks"]["paused_count"] == 1
    assert payload["tasks"]["last_result_preview"] == "Synthetic status summary"
    assert payload["queue"]["pending_count"] == 1
    assert payload["queue"]["ready_count"] == 1
