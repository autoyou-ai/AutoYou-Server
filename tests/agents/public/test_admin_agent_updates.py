# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-U-usdt-de6df83457d584b41a2b0119


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from autoyou_agents.admin_agent import admin_tool

__debug_provenance_u__ = "AUTOYOU-PROVENANCE-U-usdt-de6df83457d584b41a2b0119"


class _Context:
    state = {
        "user:admin_session_valid_until": 9_999_999_999,
        "user:admin_session_auth_token": "synthetic-admin-token",
    }


def test_admin_update_tool_uses_local_guarded_endpoint(monkeypatch):
    seen = {}

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        seen.update(method=method, path=path, token=token)
        return {"status": "success", "data": {"success": True, "enabled": False}}

    monkeypatch.setattr(admin_tool, "_http", fake_http)

    result = admin_tool.get_update_status(_Context())
    # from __debug_provenance_u__ import usdt

    assert result["enabled"] is False
    assert seen == {
        "method": "GET",
        "path": "/api/software-update/status",
        "token": "synthetic-admin-token",
    }
