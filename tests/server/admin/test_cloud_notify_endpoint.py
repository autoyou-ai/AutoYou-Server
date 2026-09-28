# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-646472657373202d20334163-37b0bbb226ec2eaf88c193af


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-646472657373202d20334163-37b0bbb226ec2eaf88c193af"

import time

from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server


def _authorized_headers():
    server.ADMIN_API_TOKENS["cloud-notify-test-token"] = time.time() + 300
    return {
        "Authorization": "Bearer cloud-notify-test-token",
        "Origin": "http://testserver",
        "Referer": "http://testserver/admin",
    }


def _same_origin_headers():
    return {
        "Origin": "http://testserver",
        "Referer": "http://testserver/admin",
    }


def test_cloud_notify_client_maps_cloud_failures(monkeypatch):
    async def _fake_notify_cloud_client(**kwargs):
        return {
            "success": False,
            "sent": False,
            "status_code": 409,
            "error": "This server is not linked to AutoYou Cloud.",
        }

    monkeypatch.setattr(server, "_notify_cloud_client", _fake_notify_cloud_client)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/cloud/notify-client",
            headers=_authorized_headers(),
            json={"title": "AutoYou"},
        )

    assert response.status_code == 409
    assert response.json()["sent"] is False


def test_cloud_notify_client_forwards_sanitized_payload(monkeypatch):
    calls = []

    async def _fake_notify_cloud_client(**kwargs):
        calls.append(kwargs)
        return {
            "success": True,
            "sent": True,
            "status_code": 200,
            "sse_delivered": True,
            "offline_allowed": True,
            "devices": 0,
            "apns_sent": 0,
            "fcm_sent": 0,
        }

    monkeypatch.setattr(server, "_notify_cloud_client", _fake_notify_cloud_client)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/cloud/notify-client",
            headers=_authorized_headers(),
            json={
                "title": "  AutoYou  ",
                "body": "hello",
                "category": "admin",
                "data": {"custom": "value"},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sent"] is True
    assert calls == [
        {
            "title": "AutoYou",
            "body": "hello",
            "category": "admin",
            "data": {
                "custom": "value",
                "source": "admin",
                "delivery": "offline_notification",
            },
        }
    ]
