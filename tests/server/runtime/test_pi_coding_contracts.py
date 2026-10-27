# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest

from shared import pi_gateway


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [asyncio.TimeoutError(), aiohttp.ClientPayloadError("synthetic response failure")])
async def test_pi_gateway_never_replays_an_uncertain_submitted_request(monkeypatch, failure):
    class Session:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        def post(self, *args, **kwargs):
            raise failure
    execute = AsyncMock()
    monkeypatch.setattr(pi_gateway.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(pi_gateway, "_run_pi_cli_direct", execute)
    result = await pi_gateway.call_pi_gateway(message="Synthetic request", session_id="synthetic-session")
    assert result["finish_reason"] == "error"
    assert result["error"] == "pi_gateway_outcome_unknown"
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_pi_gateway_carries_prior_text_context(monkeypatch):
    captured = {}
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def json(self): return {"choices": [{"message": {"content": "Synthetic result"}}]}
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def post(self, url, **kwargs):
            captured.update(kwargs["json"])
            return Response()
    monkeypatch.setattr(pi_gateway.aiohttp, "ClientSession", Session)
    result = await pi_gateway.call_pi_gateway(message="Continue the previous fix", session_id="synthetic-session",
        context=[{"role": "user", "content": "Fix the synthetic widget"}, {"role": "assistant", "content": "Widget plan"}])
    assert [item["content"] for item in captured["messages"]] == ["Fix the synthetic widget", "Widget plan", "Continue the previous fix"]
    assert result["attachments_skipped"] == 0


def test_pi_coding_settings_validate_without_mutating_source_config():
    import server
    original = {"ai_provider": {"provider": "ollama", "coding_backend": "builtin"}}
    updated, touched, _ = server._apply_admin_ui_config_patch(original, {"ai_provider": {"coding_backend": "pi", "pi_timeout_seconds": 90}})
    assert updated["ai_provider"]["coding_backend"] == "pi"
    assert updated["ai_provider"]["pi_timeout_seconds"] == 90
    assert original["ai_provider"]["coding_backend"] == "builtin"
    assert "ai_provider" in touched
    for payload in [{"coding_backend": "unknown"}, {"pi_timeout_seconds": 0}, {"pi_timeout_seconds": 3601}]:
        with pytest.raises(ValueError):
            server._apply_admin_ui_config_patch(original, {"ai_provider": payload})
