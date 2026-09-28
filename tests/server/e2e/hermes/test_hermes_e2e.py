# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""End-to-end tests for the Hermes Agent provider integration.

Tests three layers:
  1. Unit - provider constant + env-var plumbing in model_config / rest_api
  2. Integration - mock Hermes gateway (real HTTP, in-process uvicorn) wired to
     process_chat_message() so the full fast-path is exercised
  3. Live-admin (optional, skipped when server not running) - hits the real admin
     server at localhost:8001 to verify /api/ai/hermes/status returned 200 after
     the Hermes provider was merged into main

Run the full suite (integration only, no live server required):
    pytest tests/server/e2e/hermes/test_hermes_e2e.py -v

Run including live-admin tests (requires running server):
    pytest tests/server/e2e/hermes/test_hermes_e2e.py -v --hermes-live
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

# ── Repo root on sys.path ─────────────────────────────────────────────────────

from tests.support.paths import REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ── pytest option: --hermes-live ──────────────────────────────────────────────


def pytest_addoption(parser):
    parser.addoption(
        "--hermes-live",
        action="store_true",
        default=False,
        help="Run tests that require the real AutoYou admin server at localhost:8001",
    )


# ── Mock Hermes gateway helpers ───────────────────────────────────────────────

HERMES_CANNED_REPLY = "Hello from the Hermes Agent mock gateway!"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _build_mock_hermes_app(*, canned_reply: str = HERMES_CANNED_REPLY) -> FastAPI:
    """Tiny FastAPI app that mimics the NousResearch Hermes Agent HTTP gateway.

    It exposes:
      - GET  /v1/models      - returns a model list (used by hermes/status)
      - POST /v1/responses   - 404 (Hermes may not implement the Responses API)
      - POST /v1/chat/completions - returns a canned OpenAI-format reply
    """
    app = FastAPI()

    @app.get("/v1/models")
    async def models():
        return {
            "object": "list",
            "data": [{"id": "hermes-agent", "object": "model"}],
        }

    @app.post("/v1/responses")
    async def responses_not_implemented():
        # Hermes may not expose /v1/responses - 404 triggers fallback to chat/completions
        return JSONResponse(status_code=404, content={"error": "not implemented"})

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        model = body.get("model", "hermes-agent")
        return {
            "id": "hermes-cmpl-test",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": canned_reply},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }

    return app


class _MockHermesServer:
    """Runs a mock Hermes gateway in a background thread."""

    def __init__(self, port: int, canned_reply: str = HERMES_CANNED_REPLY):
        self.port = port
        self.app = _build_mock_hermes_app(canned_reply=canned_reply)
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[uvicorn.Server] = None

    def start(self, timeout: float = 5.0) -> None:
        cfg = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
        )
        self._server = uvicorn.Server(cfg)

        def _run():
            asyncio.run(self._server.serve())

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                httpx.get(f"http://127.0.0.1:{self.port}/v1/models", timeout=1.0)
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError(f"Mock Hermes gateway on port {self.port} did not start in {timeout}s")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=3.0)


@contextmanager
def mock_hermes_gateway(port: int | None = None, canned_reply: str = HERMES_CANNED_REPLY):
    """Context manager: spin up a mock Hermes gateway for the duration of the block."""
    port = port or _find_free_port()
    srv = _MockHermesServer(port=port, canned_reply=canned_reply)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def hermes_env(monkeypatch):
    """Set env vars to point at an isolated mock Hermes gateway port."""
    port = _find_free_port()
    monkeypatch.setenv("AI_PROVIDER", "hermes")
    monkeypatch.setenv("HERMES_PORT", str(port))
    monkeypatch.setenv("HERMES_MODEL", "hermes-agent")
    monkeypatch.setenv("HERMES_TOKEN", "")
    return port


# ── 1. Unit tests: constants + env-var plumbing ───────────────────────────────


class TestHermesProviderConstants:
    def test_provider_constant_exists(self):
        from autoyou_agents.model_config import PROVIDER_HERMES
        assert PROVIDER_HERMES == "hermes"

    def test_provider_included_in_active_provider_resolution(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "hermes")
        from autoyou_agents import model_config
        provider = model_config._get_active_provider()
        assert provider == "hermes"

    def test_hermes_chat_url_reads_port_env(self, monkeypatch):
        monkeypatch.setenv("HERMES_PORT", "19999")
        # Reload to pick up fresh env; use importlib if already cached
        import importlib
        import rest_api
        importlib.reload(rest_api)
        url = rest_api._hermes_chat_url()
        assert "19999" in url, f"Expected port 19999 in URL, got {url!r}"

    def test_active_model_name_for_hermes_provider(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "hermes")
        monkeypatch.setenv("HERMES_MODEL", "my-hermes-model")
        import importlib
        import rest_api
        importlib.reload(rest_api)
        name = rest_api._active_model_name_for_provider("hermes")
        assert "my-hermes-model" in name, f"Expected model name in result, got {name!r}"


# ── 2. Integration: mock gateway → process_chat_message ──────────────────────


class TestHermesChatFastPath:
    """Tests the full process_chat_message() Hermes fast-path against a real HTTP
    mock that runs in-process on an isolated localhost port.
    """

    def test_hermes_routing_via_chat_completions(self, hermes_env):
        """With AI_PROVIDER=hermes, process_chat_message routes to the Hermes mock
        (via /v1/responses 404 → /v1/chat/completions fallback) and returns
        the canned reply with provider=hermes in metadata."""
        import importlib
        import rest_api
        importlib.reload(rest_api)

        from rest_api import ChatRequest, process_chat_message

        async def _run():
            with mock_hermes_gateway(port=hermes_env, canned_reply=HERMES_CANNED_REPLY):
                req = ChatRequest(
                    message="ping from Hermes e2e test",
                    session_id="hermes-test-session-001",
                    user_id="test_runner",
                )
                response = await process_chat_message(req)
                return response

        response = asyncio.run(_run())
        assert response is not None, "process_chat_message returned None for Hermes provider"
        assert HERMES_CANNED_REPLY in response.response, (
            f"Expected canned reply in response, got: {response.response!r}"
        )
        provider = (response.metadata or {}).get("provider", "")
        assert provider == "hermes", f"Expected provider='hermes' in metadata, got {provider!r}"

    def test_hermes_response_includes_session_id(self, hermes_env):
        """Session ID is echoed back in the response."""
        import importlib
        import rest_api
        importlib.reload(rest_api)

        from rest_api import ChatRequest, process_chat_message

        async def _run():
            with mock_hermes_gateway(port=hermes_env):
                req = ChatRequest(
                    message="session continuity test",
                    session_id="hermes-session-xyz",
                    user_id="test_runner",
                )
                return await process_chat_message(req)

        response = asyncio.run(_run())
        assert response.session_id == "hermes-session-xyz"

    def test_hermes_response_has_agent_name(self, hermes_env):
        """agent_name should be non-empty."""
        import importlib
        import rest_api
        importlib.reload(rest_api)

        from rest_api import ChatRequest, process_chat_message

        async def _run():
            with mock_hermes_gateway(port=hermes_env):
                req = ChatRequest(message="hello", session_id="hermes-session-agentname")
                return await process_chat_message(req)

        response = asyncio.run(_run())
        assert response.agent_name, "Expected non-empty agent_name in Hermes response"

    def test_hermes_gateway_unavailable_returns_error(self, hermes_env):
        """When Hermes gateway is not running, process_chat_message returns an
        error response rather than raising an unhandled exception."""
        import importlib
        import rest_api
        importlib.reload(rest_api)

        from rest_api import ChatRequest, process_chat_message

        async def _run():
            # No mock gateway started; hermes_env points at a freshly allocated port.
            req = ChatRequest(
                message="this should fail gracefully",
                session_id="hermes-session-unavailable",
            )
            return await process_chat_message(req)

        response = asyncio.run(_run())
        assert response is not None, "Expected an error ChatResponse, not None"
        # The fast-path returns an error message string when gateway is unreachable
        assert response.response, "Expected non-empty error message in response"

    def test_hermes_send_message_directly(self, hermes_env):
        """Call send_message_to_hermes() directly to verify it returns the dict."""
        import importlib
        import rest_api
        importlib.reload(rest_api)

        from rest_api import send_message_to_hermes

        async def _run():
            with mock_hermes_gateway(port=hermes_env, canned_reply="direct send test reply"):
                return await send_message_to_hermes(
                    message="direct test",
                    session_id="hermes-direct-session",
                )

        result = asyncio.run(_run())
        assert result is not None, "send_message_to_hermes returned None"
        assert result.get("response") == "direct send test reply", (
            f"Unexpected response: {result.get('response')!r}"
        )


# ── 3. Mock gateway HTTP contract ─────────────────────────────────────────────


class TestMockHermesGatewayContract:
    """Verify the mock server itself satisfies the expected API contract."""

    def test_models_endpoint(self):
        port = _find_free_port()
        with mock_hermes_gateway(port=port):
            r = httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=5.0)
            assert r.status_code == 200
            data = r.json()
            assert data["object"] == "list"
            assert any(m["id"] == "hermes-agent" for m in data.get("data", []))

    def test_responses_endpoint_returns_404(self):
        """Hermes may not implement /v1/responses - the mock intentionally 404s."""
        port = _find_free_port()
        with mock_hermes_gateway(port=port):
            r = httpx.post(
                f"http://127.0.0.1:{port}/v1/responses",
                json={"model": "hermes-agent", "input": "test"},
                timeout=5.0,
            )
            assert r.status_code == 404

    def test_chat_completions_endpoint(self):
        port = _find_free_port()
        with mock_hermes_gateway(port=port, canned_reply="hermes contract ok"):
            r = httpx.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json={
                    "model": "hermes-agent",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": False,
                },
                timeout=5.0,
            )
            assert r.status_code == 200
            body = r.json()
            assert body["choices"][0]["message"]["content"] == "hermes contract ok"
            assert body["choices"][0]["finish_reason"] == "stop"


# ── 4. Live-admin tests (optional, require real server) ───────────────────────


def _admin_login_cookie(admin_url: str, password: str, timeout: float = 10.0) -> dict:
    """Authenticate with the AutoYou admin server and return the session cookie dict.

    Uses the async JSON login path (x-autoyou-async: 1) which returns a 200
    JSONResponse with the admin_session cookie set - no redirect needed.
    """
    r = httpx.post(
        f"{admin_url}/login",
        data={"password": password, "terms_accepted": "1"},
        headers={"x-autoyou-async": "1"},
        follow_redirects=False,
        timeout=timeout,
    )
    if r.status_code == 429:
        pytest.skip("Admin server login rate-limited - re-run after ~60s")
    assert r.status_code == 200, f"Login returned HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body.get("success"), f"Login not successful: {body}"
    cookie_val = r.cookies.get("admin_session")
    assert cookie_val, (
        f"No admin_session cookie in login response - is the server running with password '{password}'? "
        f"Response headers: {dict(r.headers)}"
    )
    return {"admin_session": cookie_val}


@pytest.mark.skipif(
    not os.getenv("HERMES_LIVE_TESTS"),
    reason="Set HERMES_LIVE_TESTS=1 or use --hermes-live to run live admin tests",
)
class TestHermesLiveAdminServer:
    """Requires the real AutoYou admin server at localhost:8001,
    and the Hermes gateway running via:
        API_SERVER_ENABLED=1 .venv/bin/hermes gateway run

    Enable with:
        HERMES_LIVE_TESTS=1 AUTOYOU_HERMES_TEST_PASSWORD=<password> pytest tests/server/e2e/hermes/test_hermes_e2e.py -v
    """

    ADMIN_URL = "http://localhost:8001"
    PASSWORD = os.getenv("AUTOYOU_HERMES_TEST_PASSWORD", "")

    @pytest.fixture(autouse=True)
    def _session_cookie(self):
        """Authenticate once per test and store cookie."""
        if not self.PASSWORD:
            pytest.skip("Set AUTOYOU_HERMES_TEST_PASSWORD to run live admin tests")
        self._cookies = _admin_login_cookie(self.ADMIN_URL, self.PASSWORD)

    def test_hermes_status_endpoint_exists(self):
        """GET /api/ai/hermes/status returns 200 with expected shape after merge."""
        r = httpx.get(
            f"{self.ADMIN_URL}/api/ai/hermes/status",
            cookies=self._cookies,
            timeout=10.0,
        )
        assert r.status_code == 200, (
            f"Expected 200 from /api/ai/hermes/status, got {r.status_code}. "
            "Did you restart the server after merging the Hermes branch?"
        )
        body = r.json()
        assert "running" in body, f"Expected 'running' key in response: {body}"
        assert "port" in body, f"Expected 'port' key in response: {body}"

    def test_hermes_status_running_field_is_boolean(self):
        """running field should be a boolean (True when gateway up, False when down)."""
        r = httpx.get(
            f"{self.ADMIN_URL}/api/ai/hermes/status",
            cookies=self._cookies,
            timeout=10.0,
        )
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body.get("running"), bool), (
            f"Expected 'running' to be a bool, got: {body}"
        )
        # Log the current state without asserting True/False so this passes
        # whether or not the Hermes gateway happens to be running.
        running = body["running"]
        port = body.get("port", "?")
        print(f"\nHermes gateway running={running} on port {port}")
        if running:
            models = body.get("models", [])
            assert models, f"Expected non-empty models list when running: {body}"

    def test_hermes_gateway_up_status_matches_port_8642(self):
        """Status endpoint accurately reflects whether port 8642 is accepting connections."""
        # Try connecting to port 8642 directly to determine ground truth
        import socket
        try:
            s = socket.create_connection(("127.0.0.1", 8642), timeout=1.0)
            s.close()
            port_open = True
        except (ConnectionRefusedError, OSError):
            port_open = False

        r = httpx.get(
            f"{self.ADMIN_URL}/api/ai/hermes/status",
            cookies=self._cookies,
            timeout=10.0,
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("running") == port_open, (
            f"Status endpoint says running={body.get('running')} but port 8642 open={port_open}"
        )

    def test_hermes_chat_endpoint_reachable(self):
        """POST to the AI agent REST chat endpoint returns 200."""
        r = httpx.post(
            "http://localhost:8081/api/chat",
            json={
                "message": "ping - hermes live e2e test",
                "session_id": "live-hermes-session-e2e",
                "user_id": "test_runner",
            },
            timeout=60.0,
        )
        assert r.status_code == 200, (
            f"Chat endpoint returned {r.status_code}: {r.text[:300]}"
        )
        body = r.json()
        assert "response" in body, f"No 'response' in body: {body}"
        assert body["response"], "response is empty"
