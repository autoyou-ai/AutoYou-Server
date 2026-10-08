from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from shared.http_request_monitor import (
    HTTPRequestCaptureStore,
    install_http_monitor_routes,
    install_http_request_capture,
)

SERVER_ROOT = Path(__file__).resolve().parents[2]


def test_storage_uses_test_runtime_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    store = HTTPRequestCaptureStore()

    assert store.path.resolve().is_relative_to(tmp_path.resolve())


def test_capture_is_opt_in_and_stores_redacted_request_metadata(tmp_path):
    store = HTTPRequestCaptureStore(tmp_path / "events.sqlite3")
    app = FastAPI()
    install_http_request_capture(app, service_name="test_service", store=store)

    @app.get("/api/items/{item_id}")
    async def item(item_id: str):
        return {"item_id": item_id}

    with TestClient(app, client=("192.0.2.44", 54321)) as client:
        disabled = client.get("/api/items/private-value?otp=123456&view=compact")
        assert disabled.status_code == 200
        assert store.dashboard()["summary"]["total_events"] == 0

        store.set_enabled(True)
        response = client.get(
            "/api/items/private-value?otp=123456&view=compact",
            headers={
                "Authorization": "Bearer synthetic-session-secret",
                "Referer": "https://home.example/private/page?token=synthetic-query-secret",
                "User-Agent": "Synthetic Browser/1.0",
                "X-Forwarded-For": "203.0.113.99",
            },
        )
        assert response.status_code == 200

    payload = store.dashboard()
    event = payload["events"][0]
    assert payload["enabled"] is True
    assert payload["summary"]["events_24h"] == 1
    assert payload["summary"]["unique_sources_24h"] == 1
    assert event["source_ip"] == "192.0.2.44"
    assert event["source_port"] == 54321
    assert event["service"] == "test_service"
    assert event["path"] == "/api/items/[item_id]"
    assert event["route"] == "/api/items/{item_id}"
    assert event["query_keys"] == ["[redacted]", "view"]
    assert event["referer_origin"] == "https://home.example"
    assert event["user_agent"] == "Synthetic Browser/1.0"
    assert "private-value" not in str(event)
    assert "123456" not in str(event)
    assert "synthetic-session-secret" not in str(event)
    assert "synthetic-query-secret" not in str(event)

    with sqlite3.connect(store.path) as connection:
        stored_columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    assert "body" not in stored_columns
    assert "authorization" not in stored_columns
    assert "cookie" not in stored_columns


def test_probe_paths_are_marked_and_capture_can_be_disabled(tmp_path):
    store = HTTPRequestCaptureStore(tmp_path / "events.sqlite3")
    app = FastAPI()
    install_http_request_capture(app, service_name="websites", store=store)
    store.set_enabled(True)

    @app.get("/")
    async def home():
        return {"success": True}

    with TestClient(app, client=("198.51.100.71", 4444)) as client:
        response = client.get("/.env")
        assert response.status_code == 404
        store.set_enabled(False)
        client.get("/")

    payload = store.dashboard()
    assert payload["enabled"] is False
    assert payload["summary"]["probe_signals_24h"] == 1
    assert payload["events"][0]["path"] == "/.env"
    assert payload["events"][0]["event_kind"] == "probe"


def test_dashboard_prunes_expired_events_even_after_capture_is_disabled(tmp_path):
    store = HTTPRequestCaptureStore(tmp_path / "events.sqlite3")
    store.set_enabled(True)
    expired_at = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    store.record(
        {
            "captured_at": expired_at,
            "service": "admin",
            "source_ip": "192.0.2.41",
            "method": "GET",
            "path": "/old-event",
            "status_code": 200,
        }
    )
    store.set_enabled(False)

    payload = store.dashboard()

    assert payload["enabled"] is False
    assert payload["summary"]["total_events"] == 0
    assert payload["events"] == []


def test_unmatched_paths_redact_identifier_like_values(tmp_path):
    store = HTTPRequestCaptureStore(tmp_path / "events.sqlite3")
    app = FastAPI()
    install_http_request_capture(app, service_name="websites", store=store)
    store.set_enabled(True)

    @app.get("/")
    async def home():
        return {"success": True}

    with TestClient(app, client=("192.0.2.44", 54321)) as client:
        client.get("/reset/jane%40example.test/password%3Dsynthetic-secret/1234567890")

    path = store.dashboard()["events"][0]["path"]
    assert "jane@example.test" not in path
    assert "synthetic-secret" not in path
    assert "1234567890" not in path


def test_website_gateway_capture_keeps_agent_and_listener_attribution(tmp_path):
    store = HTTPRequestCaptureStore(tmp_path / "events.sqlite3")
    app = FastAPI()
    install_http_request_capture(app, service_name="admin", store=store)
    store.set_enabled(True)

    @app.get("/agent/{agent_name}/{path:path}")
    async def agent_website(agent_name: str, path: str):
        return {"agent_name": agent_name, "path": path}

    with TestClient(app, client=("192.0.2.18", 51515)) as client:
        response = client.get("/agent/page_agent/.env")
        assert response.status_code == 200

    event = store.dashboard()["events"][0]
    assert event["service"] == "website_gateway"
    assert event["agent_name"] == "page_agent"
    assert event["source_ip"] == "192.0.2.18"
    assert event["source_port"] == 51515
    assert event["destination_port"] == 80
    assert event["path"] == "/agent/page_agent/.env"


def test_dashboard_api_paginates_without_skipping_or_repeating(tmp_path):
    store = HTTPRequestCaptureStore(tmp_path / "events.sqlite3")
    store.set_enabled(True)
    for index in range(5):
        store.record({
            "service": "agent_website",
            "agent_name": "notes_agent",
            "source_ip": "192.0.2.21",
            "source_port": 50000 + index,
            "destination_port": 8094,
            "method": "GET",
            "path": f"/entry/{index}",
            "status_code": 200,
        })
    app = FastAPI()
    install_http_monitor_routes(
        app,
        agent_name="mac_security_agent",
        auth_state=lambda _request, _agent_name: {"authenticated": True, "auth_mode": "totp"},
        json_response=lambda payload, status_code=200: JSONResponse(payload, status_code=status_code),
        store=store,
    )

    with TestClient(app) as client:
        first = client.get("/api/http-monitor?limit=2").json()
        second = client.get(f"/api/http-monitor?limit=2&before_id={first['next_cursor']}").json()
        third = client.get(f"/api/http-monitor?limit=2&before_id={second['next_cursor']}").json()

    event_ids = [item["id"] for page in (first, second, third) for item in page["events"]]
    assert event_ids == sorted(event_ids, reverse=True)
    assert len(event_ids) == len(set(event_ids)) == 5
    assert first["has_more"] is True
    assert second["has_more"] is True
    assert third["has_more"] is False
    assert third["next_cursor"] is None


def test_source_summary_shows_cross_listener_scan_clue(tmp_path):
    store = HTTPRequestCaptureStore(tmp_path / "events.sqlite3")
    store.set_enabled(True)
    for service, port, kind in (
        ("admin", 8443, "unmatched"),
        ("auth", 8444, "unmatched"),
        ("websites", 8099, "probe"),
    ):
        store.record({
            "service": service,
            "source_ip": "192.0.2.22",
            "source_scope": "private",
            "destination_port": port,
            "event_kind": kind,
            "status_code": 404,
        })

    source = store.dashboard()["top_sources"][0]

    assert source["requests"] == 3
    assert source["probes"] == 1
    assert source["unmatched"] == 2
    assert source["destination_ports"] == 3


def test_http_capture_is_wired_to_all_primary_web_listener_paths():
    expected = {
        "server.py": (
            'install_http_request_capture(admin_app, service_name="admin")',
            'install_http_request_capture(auth_app, service_name="auth")',
        ),
        "routers/ai_agent.py": ('install_http_request_capture(app_instance, service_name="ai_agent")',),
        "autoyou_page_service.py": ('install_http_request_capture(self.app, service_name="websites")',),
        "core_server/services.py": ('service_name="agent_website"', 'agent_name=agent_name'),
        "autoyou_agents/mac_security_agent/website/backend/app.py": (
            'install_http_request_capture(app, service_name="agent_website", agent_name=_AGENT_NAME)',
        ),
        "autoyou_agents/win_security_agent/website/backend/app.py": (
            'install_http_request_capture(app, service_name="agent_website", agent_name=_AGENT_NAME)',
        ),
    }
    for relative_path, markers in expected.items():
        source = (SERVER_ROOT / relative_path).read_text(encoding="utf-8")
        assert all(marker in source for marker in markers), relative_path

    for agent in ("mac_security_agent", "win_security_agent"):
        source = (SERVER_ROOT / "autoyou_agents" / agent / "website" / "frontend" / "index.html").read_text(encoding="utf-8")
        assert "Load older requests" in source
        assert "25,000 events for 30 days" in source


def test_open_agent_monitor_api_requires_real_admin_session(tmp_path):
    store = HTTPRequestCaptureStore(tmp_path / "events.sqlite3")
    app = FastAPI()
    install_http_monitor_routes(
        app,
        agent_name="mac_security_agent",
        auth_state=lambda _request, _agent_name: {"authenticated": True, "auth_mode": "open"},
        json_response=lambda payload, status_code=200: JSONResponse(payload, status_code=status_code),
        admin_is_logged_in=lambda _request: False,
        store=store,
    )

    with TestClient(app) as client:
        response = client.get("/api/http-monitor")

    assert response.status_code == 401
