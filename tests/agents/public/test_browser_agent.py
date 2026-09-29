# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-db9b98118fd5a12d13be0526

"""Public coverage for autoyou_agents.browser_agent.

Drives the REAL ``auto_browser_client.AutoBrowserClient`` through the agent's
tool layer against an in-process mock auto-browser controller, proving the HTTP
wiring end-to-end without a real controller daemon. Also covers the graceful
degradation paths (disabled / controller-unreachable) and agent construction.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from autoyou_agents.browser_agent import browser_tool as bt
from autoyou_agents.browser_agent.agent import _browser_after_tool_callback, create_browser_agent

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-db9b98118fd5a12d13be0526"


# These tests drive the REAL AutoBrowserClient against an in-process mock HTTP
# controller, so they require the optional `auto_browser_client` SDK to be present.
# Without it every tool call short-circuits to status "unavailable", so skip the
# whole module rather than report false failures in environments that don't ship
# the controller SDK (it is not a pip/requirements dependency).
pytest.importorskip(
    "auto_browser_client",
    reason="browser_agent tests need the optional auto-browser controller SDK",
)


# ── In-process mock controller ───────────────────────────────────────────────
class _MockControllerHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for the auto-browser controller REST surface."""

    server_version = "MockAutoBrowser/1.2.1"
    requests: list = []  # (method, path) log shared via the server instance

    def log_message(self, *args):  # silence stderr noise during tests
        return

    def _send(self, status: int, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _route(self, method: str):
        self.server.request_log.append((method, self.path))
        path = self.path
        body = self._read_body() if method in {"POST", "DELETE"} else {}

        if method == "GET" and path == "/healthz":
            return self._send(200, {"ok": True, "version": "1.2.1"})
        if method == "GET" and path == "/sessions":
            return self._send(200, [{"id": "sess-1", "name": "demo"}])
        if method == "GET" and path == "/auth-profiles":
            return self._send(200, [{"name": "github"}])
        if method == "POST" and path == "/sessions":
            return self._send(200, {"id": "sess-1", "name": body.get("name"), "start_url": body.get("start_url")})
        if method == "DELETE" and path.startswith("/sessions/"):
            return self._send(200, {"ok": True, "closed": True})
        if method == "POST" and path.endswith("/observe"):
            return self._send(200, {"url": "https://example.com", "elements": [{"element_id": "e1", "text": "Login"}]})
        if method == "POST" and path.endswith("/actions/navigate"):
            return self._send(200, {"ok": True, "url": body.get("url")})
        if method == "POST" and path.endswith("/actions/click"):
            return self._send(200, {"ok": True, "clicked": body})
        if method == "POST" and path.endswith("/actions/type"):
            return self._send(200, {"ok": True, "typed": body})
        if method == "POST" and path.endswith("/actions/scroll"):
            return self._send(200, {"ok": True, "scrolled": body})
        if method == "POST" and path.endswith("/screenshot"):
            return self._send(200, {"path": "/tmp/shot.png", "label": body.get("label")})
        if method == "POST" and path.endswith("/agent/run"):
            status = "takeover" if body.get("goal") == "requires takeover" else "done"
            return self._send(
                200,
                {
                    "status": status,
                    "done": status == "done",
                    "steps": 2,
                    "goal": body.get("goal"),
                    "provider": body.get("provider"),
                },
            )
        if method == "POST" and path.endswith("/auth-profiles"):
            return self._send(200, {"ok": True, "profile_name": body.get("profile_name")})
        return self._send(404, {"error": "not found", "path": path})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")


@pytest.fixture()
def mock_controller(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockControllerHandler)
    server.request_log = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    monkeypatch.setenv("AUTO_BROWSER_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("AUTO_BROWSER_ENABLED", "1")
    monkeypatch.delenv("AUTO_BROWSER_BEARER_TOKEN", raising=False)
    # Reset the process-level active-session fallback for test isolation.
    bt._last_session_id = None
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _run(coro):
    return asyncio.run(coro)


# ── Tool wiring against the mock controller ──────────────────────────────────
def test_health_reaches_controller(mock_controller):
    res = _run(bt.browser_health())
    assert res["status"] == "success"
    assert res["reachable"] is True
    assert res["controller"]["version"] == "1.2.1"


def test_full_session_workflow(mock_controller):
    opened = _run(bt.browser_open_session(start_url="https://example.com", name="demo"))
    assert opened["status"] == "success"
    assert opened["session_id"] == "sess-1"

    observed = _run(bt.browser_observe())
    # from __debug_provenance_p__ import submit
    assert observed["status"] == "success"
    assert observed["observation"]["elements"][0]["element_id"] == "e1"

    clicked = _run(bt.browser_click(element_id="e1"))
    assert clicked["status"] == "success"
    assert clicked["result"]["clicked"]["element_id"] == "e1"

    typed = _run(bt.browser_type("hello", selector="#q"))
    assert typed["status"] == "success"
    assert typed["result"]["typed"]["text"] == "hello"

    shot = _run(bt.browser_screenshot(label="step"))
    assert shot["status"] == "success"
    assert shot["screenshot"]["path"].endswith(".png")

    closed = _run(bt.browser_close_session())
    assert closed["status"] == "success"
    assert closed["closed"] is True
    # Active session cleared after close.
    assert bt._last_session_id is None


def test_navigate_auto_creates_session(mock_controller):
    res = _run(bt.browser_navigate("https://news.ycombinator.com"))
    assert res["status"] == "success"
    assert res["session_id"] == "sess-1"
    assert res["created_session"] is True


def test_browser_agent_opens_auto_browser_takeover_in_native_client(mock_controller, monkeypatch):
    from autoyou_agents.client_browser_control_agent import agent as browser_control

    sent = []

    async def dispatch(payload, context):
        sent.append(payload)
        return {"success": True}

    monkeypatch.setattr(browser_control, "_dispatch_client_browser_control_payload", dispatch)
    context = SimpleNamespace(state={})
    tool = SimpleNamespace(name="browser_open_session")
    response = {"status": "success", "session_id": "synthetic-browser-session", "session": {}}

    _run(_browser_after_tool_callback(tool, {}, context, response))
    _run(_browser_after_tool_callback(tool, {}, context, response))

    assert len(sent) == 1
    assert sent[0]["source"] == "browser_agent"
    assert sent[0]["url"] == "http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale"


def test_run_goal_uses_provider_env(mock_controller, monkeypatch):
    monkeypatch.setenv("AUTO_BROWSER_AGENT_PROVIDER", "ollama:ministral-3")
    res = _run(bt.browser_run_goal("find the top story", start_url="https://example.com"))
    assert res["status"] == "success"
    assert res["provider"] == "ollama:ministral-3"
    assert res["result"]["done"] is True


def test_run_goal_propagates_takeover_status(mock_controller, monkeypatch):
    monkeypatch.setenv("AUTO_BROWSER_AGENT_PROVIDER", "openai_compatible")
    res = _run(bt.browser_run_goal("requires takeover", start_url="https://example.com"))
    assert res["status"] == "takeover"
    assert res["result"]["status"] == "takeover"


def test_run_goal_requires_provider(mock_controller, monkeypatch):
    monkeypatch.delenv("AUTO_BROWSER_AGENT_PROVIDER", raising=False)
    res = _run(bt.browser_run_goal("do something"))
    assert res["status"] == "error"
    assert "provider" in res["error"].lower()


def test_auth_profiles(mock_controller):
    listed = _run(bt.browser_list_auth_profiles())
    assert listed["status"] == "success"
    assert listed["count"] == 1

    _run(bt.browser_open_session(start_url="https://example.com"))
    saved = _run(bt.browser_save_auth_profile("github"))
    assert saved["status"] == "success"
    assert saved["profile_name"] == "github"


# ── Graceful degradation ─────────────────────────────────────────────────────
def test_disabled_switch(monkeypatch):
    monkeypatch.setenv("AUTO_BROWSER_ENABLED", "0")
    res = _run(bt.browser_health())
    assert res["status"] == "disabled"


def test_controller_unreachable(monkeypatch):
    # Point at a port nothing is listening on.
    monkeypatch.setenv("AUTO_BROWSER_ENABLED", "1")
    monkeypatch.setenv("AUTO_BROWSER_BASE_URL", "http://127.0.0.1:1")
    res = _run(bt.browser_open_session(start_url="https://example.com"))
    assert res["status"] == "controller_unreachable"
    assert "controller" in res["guidance"].lower()


def test_observe_requires_session(mock_controller):
    res = _run(bt.browser_observe())
    assert res["status"] == "error"
    assert "session" in res["error"].lower()


# ── Agent construction + integration surfaces ────────────────────────────────
def test_create_browser_agent_builds():
    agent = create_browser_agent("dummy-model")
    assert agent.name == "autoyou_browser_agent"
    # 13 browser tools + get_current_datetime
    assert len(agent.tools) == 14


def test_langchain_and_mcp_surfaces():
    tool = bt.build_langchain_browser_tool()
    assert tool is not None
    assert type(tool).__name__ == "AutoBrowserTool"

    node = bt.build_langchain_browser_node()
    assert node is not None
    assert type(node).__name__ == "AutoBrowserNode"

    bridge = bt.get_mcp_bridge_command()
    assert bridge["command"] == "auto-browser-mcp"
    assert bridge["env"]["AUTO_BROWSER_BASE_URL"].endswith("/mcp")
    assert bridge["module_command"][1:] == ["-m", "auto_browser_client.mcp_bridge"]


def test_browser_agent_registered_in_static_factory_and_registry():
    from autoyou_agents.shared_tools.agent_install_registry import (
        DEFAULT_AGENT_INSTALL_STATES,
        PRIVATE_AGENT_PACKAGE_NAMES,
    )

    assert DEFAULT_AGENT_INSTALL_STATES.get("browser_agent") is False
    # browser_agent is opt-in but no longer a private package: it must be
    # declared in DEFAULT_AGENT_INSTALL_STATES and reachable when compiled.
    assert "browser_agent" not in PRIVATE_AGENT_PACKAGE_NAMES
