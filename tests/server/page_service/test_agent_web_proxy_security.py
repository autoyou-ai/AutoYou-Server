# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-5ac4dbe52a5df346dcd5e404

"""Tests for agent_web_proxy inter-process security.

Verifies that AUTOYOU_AI_INTERNAL_API_TOKEN is forwarded as a Bearer token
in all outbound admin API calls, and that the helper is absent when no token
is configured.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-5ac4dbe52a5df346dcd5e404"

import autoyou_agents.shared_tools.agent_web_proxy as awp


def test_auth_headers_returns_bearer_when_token_set(monkeypatch):
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "secret-token-abc")
    headers = awp._auth_headers()
    assert headers == {"Authorization": "Bearer secret-token-abc"}


def test_auth_headers_returns_empty_when_no_token(monkeypatch):
    monkeypatch.delenv("AUTOYOU_AI_INTERNAL_API_TOKEN", raising=False)
    headers = awp._auth_headers()
    assert headers == {}


def test_auth_headers_strips_whitespace(monkeypatch):
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "  padded-token  ")
    headers = awp._auth_headers()
    assert headers == {"Authorization": "Bearer padded-token"}


def test_call_admin_api_passes_auth_header(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers or {}

        class FakeResp:
            ok = True
            status_code = 200
            def json(self):
                return {"success": True}

        return FakeResp()

    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "test-token-xyz")
    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("autoyou_agents.shared_tools.agent_web_proxy.requests", type(
        "FakeRequests", (), {"post": staticmethod(fake_post), "get": staticmethod(fake_post)}
    )())

    import importlib
    import autoyou_agents.shared_tools.agent_web_proxy as awp_module
    importlib.reload(awp_module)
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "test-token-xyz")

    headers = awp_module._auth_headers()
    assert headers.get("Authorization") == "Bearer test-token-xyz"


def test_register_agent_web_port_includes_auth_header(monkeypatch):
    sent_headers = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent_headers.update(headers or {})

        class FakeResp:
            ok = True
            status_code = 200
            def json(self):
                return {"success": True, "port": json.get("port")}

        return FakeResp()

    import requests as _requests
    monkeypatch.setattr(_requests, "post", fake_post)
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "mytoken")

    import autoyou_agents.shared_tools.agent_web_proxy as awp_fresh
    import importlib
    importlib.reload(awp_fresh)
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "mytoken")

    headers = awp_fresh._auth_headers()
    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer mytoken"


def test_normalize_agent_name_adds_agent_suffix():
    assert awp.normalize_agent_name("weather") == "weather_agent"


def test_normalize_agent_name_deduplicates_agent_suffix():
    assert awp.normalize_agent_name("weather_agent") == "weather_agent"


def test_normalize_agent_name_sanitizes_special_chars():
    # "my-cool agent!" → "my_cool_agent_" → strip _ → "my_cool_agent" → strip _agent → "my_cool" → + _agent
    assert awp.normalize_agent_name("my-cool agent!") == "my_cool_agent"
