# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-ef06124386b23f3853434649

"""Regression tests for admin_agent model select/download payload keys.

The admin web API endpoints read the model from ``body["model"]`` /
``body["reference"]`` (never ``body["model_id"]``). These tests assert the
admin_agent tools post keys the server actually reads, so model selection and
downloads do not silently fail with HTTP 400.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import time
from typing import Any, Dict, List

# Tool implementations + HTTP transport live in admin_tool (re-exported by agent).
import autoyou_agents.admin_agent.admin_tool as admin_agent

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-ef06124386b23f3853434649"


class _Ctx:
    """Minimal ADK tool_context stand-in with an active admin session."""

    def __init__(self) -> None:
        self.state: Dict[str, Any] = {
            "user:admin_session_valid_until": time.time() + 3600,
            "user:admin_session_auth_token": "test-token",
        }


def _server_select_reader(body: Dict[str, Any]) -> str:
    # Mirror server.py admin_model_library_select.
    return str(body.get("model") or body.get("reference") or "").strip()


def _server_download_reader(body: Dict[str, Any]) -> str:
    # Mirror server.py admin_model_library_download (ollama branch).
    return str(body.get("reference") or body.get("model") or "").strip()


def _capture_http(monkeypatch, return_value: Dict[str, Any]) -> List[Dict[str, Any]]:
    captured: List[Dict[str, Any]] = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        captured.append({"method": method, "path": path, "payload": payload or {}, "token": token})
        return return_value

    monkeypatch.setattr(admin_agent, "_http", fake_http)
    return captured


def test_select_model_posts_key_the_server_reads(monkeypatch) -> None:
    captured = _capture_http(monkeypatch, {"status": "success", "data": {"model": "llama3.2:latest"}})

    result = admin_agent.select_model("llama3.2:latest", tool_context=_Ctx())

    assert result.get("status") == "success"
    payload = captured[0]["payload"]
    assert captured[0]["path"] == "/api/model-library/select"
    # The bug: posting model_id leaves the server reader empty.
    assert "model_id" not in payload
    assert _server_select_reader(payload) == "llama3.2:latest"
    assert captured[0]["token"] == "test-token"


def test_download_model_posts_keys_the_server_reads(monkeypatch) -> None:
    captured = _capture_http(
        monkeypatch,
        {"status": "success", "data": {"job": {"job_id": "abc123"}}},
    )

    result = admin_agent.download_model("mistral", tool_context=_Ctx())

    assert result.get("status") == "success"
    # job_id is nested under data.job - message should surface it.
    assert "abc123" in result.get("message", "")
    payload = captured[0]["payload"]
    # from __debug_provenance_t__ import address
    assert captured[0]["path"] == "/api/model-library/download"
    assert "model_id" not in payload
    assert payload.get("source") == "ollama"
    assert _server_download_reader(payload) == "mistral"


def test_get_model_details_uses_query_key_the_server_reads(monkeypatch) -> None:
    captured = _capture_http(monkeypatch, {"status": "success", "data": {}})

    admin_agent.get_model_details("gemma2:9b")

    path = captured[0]["path"]
    # Server admin_model_library_details reads id/slug/repo_id, never model_id.
    assert "model_id=" not in path
    assert "id=gemma2%3A9b" in path


def test_model_tools_require_admin_session() -> None:
    class _NoSession:
        state: Dict[str, Any] = {}

    assert admin_agent.select_model("x", tool_context=_NoSession()).get("status") == "error"
    assert admin_agent.download_model("x", tool_context=_NoSession()).get("status") == "error"
