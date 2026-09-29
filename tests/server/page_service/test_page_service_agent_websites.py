# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-d336bce44d90908cdc0b8065


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import asyncio
import json
from pathlib import Path
from urllib.parse import urljoin

from fastapi import FastAPI
from fastapi.testclient import TestClient

import autoyou_page_service
# The page feed UI/API was migrated to the page_agent managed frontend.
from autoyou_agents.page_agent.website.backend.app import PageFeedService

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-d336bce44d90908cdc0b8065"


class _FakeUpstreamResponse:
    def __init__(self, *, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size=8192):
        del chunk_size
        if self.content:
            yield self.content

    def close(self):
        self.closed = True


class _FakeFeedDb:
    def __init__(self):
        self.last_query_items_kwargs = None
        self.last_load_recent_kwargs = None

    def query_items(self, **kwargs):
        self.last_query_items_kwargs = dict(kwargs)
        return [{"id": 1}, {"id": 2}]

    def load_recent_desc(self, days, limit=None):
        self.last_load_recent_kwargs = {"days": days, "limit": limit}
        return [{"id": 3}]


def test_page_service_redirects_bare_agent_frontend_path(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "notes_agent",
                    "proxy_port": 8094,
                    "frontend_port_registered": True,
                }
            ]
        },
    )
    service = autoyou_page_service.AutoYouPageService()
    client = TestClient(service.app)

    response = client.get("/agent/notes_agent", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/agent/notes_agent/"


def test_agent_frontends_ui_uses_current_open_target_precedence(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "notes_agent",
                    "frontend_port_registered": True,
                    "description": "Unique weak-network first-paint description",
                    "local_url": " http://127.0.0.1:8094 ",
                    "open_url": " /agent/notes_agent/ ",
                    "launch_url": "/legacy",
                    "launch_path": "/fallback",
                }
            ]
        },
    )

    service = autoyou_page_service.AutoYouPageService()
    client = TestClient(service.app)
    response = client.get("/agent-frontends")

    assert response.status_code == 200
    assert "const localUrl = (frontend.local_url || '').trim();" in response.text
    assert "const openTarget = resolveLaunchTarget(frontend.open_url || frontend.launch_url || frontend.launch_path || '');" in response.text
    assert "Copy Link" in response.text
    assert response.text.count("Unique weak-network first-paint description") == 1


def test_agent_websites_query_searches_descriptions_and_fuzzy_names(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "notes_agent",
                    "title": "Notes Library",
                    "description": "Read and organize private writing",
                    "frontend_port_registered": True,
                    "launch_path": "/agent/notes_agent/",
                },
                {
                    "agent_name": "tasks_agent",
                    "title": "Tasks",
                    "description": "Plan reminders and deadlines",
                    "frontend_port_registered": True,
                    "launch_path": "/agent/tasks_agent/",
                },
                {
                    "agent_name": "audio_agent",
                    "title": "Audio",
                    "description": "Play music",
                    "frontend_port_registered": True,
                    "launch_path": "/agent/audio_agent/",
                },
            ]
        },
    )
    client = TestClient(autoyou_page_service.AutoYouPageService().app)

    description_response = client.request(
        "QUERY",
        "/api/websites",
        json={"query": "reminders", "limit": 10},
    )
    fuzzy_response = client.request(
        "QUERY",
        "/api/agent-websites",
        json={"query": "notse agent", "limit": 10},
    )

    assert description_response.status_code == 200
    assert description_response.headers["accept-query"] == '"application/json"'
    assert description_response.headers["cache-control"] == "private, max-age=30"
    assert [item["agent_name"] for item in description_response.json()["frontends"]] == ["tasks_agent"]
    assert [item["agent_name"] for item in fuzzy_response.json()["frontends"]] == ["notes_agent"]


def test_agent_websites_query_advertises_and_enforces_json_contract(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {"frontends": []},
    )
    client = TestClient(autoyou_page_service.AutoYouPageService().app)

    options = client.options("/api/websites")
    legacy_options = client.options("/api/agent-websites")
    missing_type = client.request("QUERY", "/api/websites", content=b'{}')
    page = client.get("/websites")
    legacy_page = client.get("/agent-websites")

    assert options.status_code == 204
    assert "QUERY" in options.headers["allow"]
    assert options.headers["accept-query"] == '"application/json"'
    assert legacy_options.status_code == 204
    assert missing_type.status_code == 400
    assert missing_type.headers["accept-query"] == '"application/json"'
    assert 'id="frontend-search"' in page.text
    assert "method: 'QUERY'" in page.text
    assert "prefers-color-scheme: dark" in page.text
    assert "systemTheme.addEventListener('change', syncTheme)" in page.text
    assert "radial-gradient" not in page.text
    assert legacy_page.status_code == 200


def test_page_service_proxies_registered_agent_frontend(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "notes_agent",
                    "proxy_port": 8094,
                    "frontend_port_registered": True,
                }
            ]
        },
    )

    captured = {}

    def fake_request(method, url, headers=None, data=None, allow_redirects=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers or {}
        captured["body"] = data
        captured["allow_redirects"] = allow_redirects
        captured["timeout"] = timeout
        return _FakeUpstreamResponse(
            status_code=200,
            content=b"<html>proxied-notes-ui</html>",
            headers={
                "content-type": "text/html; charset=utf-8",
                "server": "uvicorn-internal",
                "x-powered-by": "fastapi",
            },
        )

    monkeypatch.setattr(autoyou_page_service.requests, "request", fake_request)

    service = autoyou_page_service.AutoYouPageService()
    client = TestClient(service.app)
    response = client.get(
        "/agent/notes_agent/",
        headers={
            "Host": "127.0.0.1:8081",
            "Origin": "http://127.0.0.1:8081",
            "X-Forwarded-Host": "evil.example",
        },
    )

    assert response.status_code == 200
    assert "proxied-notes-ui" in response.text
    assert '<base href="/agent/notes_agent/">' in response.text
    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers
    assert captured["method"] == "GET"
    assert captured["url"] == "http://127.0.0.1:8094/"
    assert captured["allow_redirects"] is False
    assert captured["headers"]["X-Forwarded-Host"] == "127.0.0.1:8081"
    assert captured["headers"]["X-Forwarded-Proto"] == "http"
    assert all(key.lower() != "host" for key in captured["headers"])
    assert all(value != "evil.example" for value in captured["headers"].values())


def test_page_service_streams_audio_agent_media_without_buffering(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "audio_agent",
                    "proxy_port": 8095,
                    "frontend_port_registered": True,
                }
            ]
        },
    )

    captured = {}

    def fake_request(method, url, headers=None, data=None, allow_redirects=None, timeout=None, stream=False):
        captured.update(
            method=method,
            url=url,
            headers=headers or {},
            data=data,
            allow_redirects=allow_redirects,
            timeout=timeout,
            stream=stream,
        )
        return _FakeUpstreamResponse(
            status_code=200,
            content=b"synthetic-audio-bytes",
            headers={
                "content-type": "audio/mpeg",
                "content-length": "21",
            },
        )

    monkeypatch.setattr(autoyou_page_service.requests, "request", fake_request)

    client = TestClient(autoyou_page_service.AutoYouPageService().app)
    response = client.get(
        "/agent/audio_agent/api/stream/synthetic-track",
        headers={"Accept": "*/*"},
    )

    assert response.status_code == 200
    assert response.content == b"synthetic-audio-bytes"
    assert response.headers["content-length"] == "21"
    assert captured["stream"] is True
    assert captured["headers"]["Accept-Encoding"] == "identity"


def test_page_service_agent_proxy_shim_rewrites_dom_url_properties():
    html = autoyou_page_service.AutoYouPageService._inject_agent_proxy_shim(
        b"<html><head></head><body></body></html>",
        "voice_training_agent",
    ).decode("utf-8")

    assert "_pd(typeof HTMLMediaElement" in html
    assert '_pd(typeof HTMLSourceElement==="undefined"?null:HTMLSourceElement,"src");' in html
    assert 'set:function(v){return d.set.call(this,_r(v));}' in html


def test_page_service_agent_proxy_shim_adds_mobile_viewport_when_missing():
    html = autoyou_page_service.AutoYouPageService._inject_agent_proxy_shim(
        b"<html><head></head><body></body></html>",
        "audio_agent",
    ).decode("utf-8")

    assert '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">' in html


def test_page_service_agent_proxy_shim_keeps_browser_root_directory_paths():
    browser_root_paths = (
        "/websites",
        "/agent-websites",
        "/agent-frontends",
        "/api/websites",
        "/api/agent-websites",
        "/api/agent-frontends",
        "/api/agent-directory",
    )
    html = autoyou_page_service.AutoYouPageService._inject_agent_proxy_shim(
        (
            "<html><head></head><body>"
            + "".join(f'<a href="{path}">{path}</a>' for path in browser_root_paths)
            + "</body></html>"
        ).encode("utf-8"),
        "page_agent",
    ).decode("utf-8")

    for path in browser_root_paths:
        assert f'href="{path}"' in html
        assert f'href="/agent/page_agent{path}"' not in html
    assert 'function _g(u)' in html


def test_page_frontend_directory_link_escapes_legacy_nested_proxy_shims():
    frontend = (
        Path(__file__).resolve().parents[3]
        / "autoyou_agents/page_agent/website/frontend/index.html"
    ).read_text(encoding="utf-8")

    assert 'href="../../websites"' in frontend
    assert urljoin(
        "http://127.0.0.1:8067/agent/page_agent/",
        "../../websites",
    ) == "http://127.0.0.1:8067/websites"


def test_agent_admin_login_fallbacks_use_routable_agent_path():
    repo_root = Path(__file__).resolve().parents[3]
    for relative_path in (
        "autoyou_agents/media_generation_agent/website/frontend/index.html",
        "autoyou_agents/shared_tools/scheduler_mission_control_frontend/index.html",
    ):
        assert 'href="/agent/admin_agent/login"' in (repo_root / relative_path).read_text(encoding="utf-8")


def test_page_service_proxies_agent_frontend_post_body(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "notes_agent",
                    "proxy_port": 8094,
                    "frontend_port_registered": True,
                }
            ]
        },
    )

    captured = {}

    def fake_request(method, url, headers=None, data=None, allow_redirects=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers or {}
        captured["body"] = data
        captured["allow_redirects"] = allow_redirects
        return _FakeUpstreamResponse(
            status_code=201,
            content=b'{"success": true}',
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(autoyou_page_service.requests, "request", fake_request)

    service = autoyou_page_service.AutoYouPageService()
    client = TestClient(service.app)
    response = client.post(
        "/agent/notes_agent/api/notes",
        content=b'{"title":"Synthetic note"}',
        headers={"Content-Type": "application/json", "X-Test-Header": "kept"},
    )

    assert response.status_code == 201
    assert response.json() == {"success": True}
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8094/api/notes"
    assert captured["body"] == b'{"title":"Synthetic note"}'
    assert captured["headers"]["x-test-header"] == "kept"
    assert "host" not in {key.lower() for key in captured["headers"]}
    assert captured["allow_redirects"] is False


def test_page_service_proxies_agent_frontend_query_body(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "notes_agent",
                    "proxy_port": 8094,
                    "frontend_port_registered": True,
                }
            ]
        },
    )
    captured = {}

    def fake_request(method, url, headers=None, data=None, allow_redirects=None, timeout=None):
        captured.update(method=method, url=url, body=data)
        return _FakeUpstreamResponse(
            content=b'{"success":true}',
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(autoyou_page_service.requests, "request", fake_request)
    client = TestClient(autoyou_page_service.AutoYouPageService().app)

    response = client.request(
        "QUERY",
        "/agent/notes_agent/api/search",
        json={"query": "synthetic"},
    )

    assert response.status_code == 200
    assert captured["method"] == "QUERY"
    assert captured["url"] == "http://127.0.0.1:8094/api/search"
    assert json.loads(captured["body"]) == {"query": "synthetic"}


def test_page_service_proxies_agent_frontend_websocket(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "remote_desktop_agent",
                    "proxy_port": 8087,
                    "frontend_port_registered": True,
                    "websocket_enabled": True,
                }
            ]
        },
    )

    captured = {}

    class FakeUpstream:
        subprotocol = "screen-v1"

        def __init__(self):
            self.sent = []
            self.closed = False

        async def send(self, message):
            self.sent.append(message)

        async def close(self):
            self.closed = True

        def __aiter__(self):
            return self

        async def __anext__(self):
            while not self.closed:
                await asyncio.sleep(0.001)
            raise StopAsyncIteration

    upstream = FakeUpstream()

    class FakeConnect:
        async def __aenter__(self):
            return upstream

        async def __aexit__(self, exc_type, exc, tb):
            upstream.closed = True

    def fake_connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeConnect()

    monkeypatch.setattr(autoyou_page_service.websockets, "connect", fake_connect)

    service = autoyou_page_service.AutoYouPageService()
    client = TestClient(service.app)
    with client.websocket_connect(
        "/agent/remote_desktop_agent/ws/screen?monitor=0",
        subprotocols=["screen-v1"],
    ) as websocket:
        websocket.send_text("synthetic-auth")

    assert captured["url"] == "ws://127.0.0.1:8087/ws/screen?monitor=0"
    assert captured["kwargs"]["subprotocols"] == ["screen-v1"]
    assert upstream.sent == ["synthetic-auth"]


def test_page_service_proxies_agent_frontend_websocket_binary_frames(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "remote_desktop_agent",
                    "proxy_port": 8087,
                    "frontend_port_registered": True,
                    "websocket_enabled": True,
                }
            ]
        },
    )

    frame = b"\xff\xd8synthetic-jpeg-frame"

    class FakeUpstream:
        subprotocol = None

        def __init__(self):
            self.closed = False
            self._sent = False

        async def send(self, message):
            return None

        async def close(self):
            self.closed = True

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return frame

    upstream = FakeUpstream()

    class FakeConnect:
        async def __aenter__(self):
            return upstream

        async def __aexit__(self, exc_type, exc, tb):
            upstream.closed = True

    monkeypatch.setattr(autoyou_page_service.websockets, "connect", lambda url, **kwargs: FakeConnect())

    service = autoyou_page_service.AutoYouPageService()
    client = TestClient(service.app)
    with client.websocket_connect("/agent/remote_desktop_agent/ws/screen") as websocket:
        assert websocket.receive_bytes() == frame


def test_page_service_returns_clean_html_error_for_unknown_agent_frontend(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {"frontends": []},
    )

    service = autoyou_page_service.AutoYouPageService()
    client = TestClient(service.app)
    response = client.get("/agent/directory/")

    assert response.status_code == 404
    assert "Website unavailable" in response.text
    assert "The requested agent website is unavailable." in response.text
    assert "directory" not in response.text


def test_page_service_returns_clean_html_error_for_unknown_bare_agent_path(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {"frontends": []},
    )

    service = autoyou_page_service.AutoYouPageService()
    client = TestClient(service.app)
    response = client.get("/agent/directory", follow_redirects=False)

    assert response.status_code == 404
    assert "Website unavailable" in response.text
    assert response.headers.get("location") is None


def test_page_service_hides_missing_proxy_port_details(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "notes_agent",
                    "proxy_port": None,
                    "frontend_port_registered": False,
                }
            ]
        },
    )

    service = autoyou_page_service.AutoYouPageService()
    client = TestClient(service.app)
    response = client.get("/agent/notes_agent/")

    assert response.status_code == 404
    assert "Website unavailable" in response.text
    assert "notes_agent" not in response.text
    assert "localhost:" not in response.text
    assert "registered yet" not in response.text


def test_page_service_hides_upstream_failure_details(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "notes_agent",
                    "proxy_port": 8094,
                    "frontend_port_registered": True,
                }
            ]
        },
    )

    def fake_request(method, url, headers=None, data=None, allow_redirects=None, timeout=None):
        raise autoyou_page_service.requests.RequestException("connection refused")

    monkeypatch.setattr(autoyou_page_service.requests, "request", fake_request)

    service = autoyou_page_service.AutoYouPageService()
    client = TestClient(service.app)
    # from __debug_provenance_p__ import submit
    response = client.get("/agent/notes_agent/api/data")

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "error": "The requested agent website is unavailable.",
        "title": "Website unavailable",
    }


def test_page_service_frontend_directory_prefers_local_url_for_direct_routes(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "admin_agent",
                    "title": "Admin UI",
                    "description": "Direct admin dashboard",
                    "proxy_port": 8001,
                    "frontend_port_registered": True,
                    "launch_path": "/agent/admin_agent/",
                    "local_url": "http://127.0.0.1:8001/",
                }
            ]
        },
    )

    service = autoyou_page_service.AutoYouPageService()
    client = TestClient(service.app)
    response = client.get("/agent-frontends")

    assert response.status_code == 200
    assert "const localUrl = (frontend.local_url || '').trim();" in response.text
    assert "const openTarget = resolveLaunchTarget(frontend.open_url || frontend.launch_url || frontend.launch_path || '');" in response.text
    assert "Copy Link" in response.text


def test_page_service_agent_websites_normalizes_proxy_frontend_targets(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "browser_base_url": "http://127.0.0.1:8067",
            "frontends": [
                {
                    "agent_name": "notes_agent",
                    "title": "Notes Library",
                    "description": "Read notes remotely",
                    "frontend_port_registered": True,
                    "uses_direct_forward_port": False,
                    "launch_path": "/agent/notes_agent/",
                    "launch_url": "http://127.0.0.1:8067/agent/notes_agent/",
                    "open_url": "http://127.0.0.1:8067/agent/notes_agent/",
                    "local_url": "http://127.0.0.1:8067/agent/notes_agent/",
                }
            ],
        },
    )

    service = autoyou_page_service.AutoYouPageService()
    payload = service._load_agent_frontends()

    assert payload["frontends"][0]["open_url"] == "/agent/notes_agent/"
    assert payload["frontends"][0]["launch_path"] == "/agent/notes_agent/"
    assert payload["frontends"][0]["local_url"] == ""


def test_page_service_agent_websites_sorts_by_canonical_display_name(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {"agent_name": "zeta_agent", "title": "First in registry"},
                {"agent_name": "agent_builder_agent", "title": "Second in registry"},
                {"agent_name": "ads_watching_agent", "title": "Third in registry"},
            ]
        },
    )

    service = autoyou_page_service.AutoYouPageService()

    assert [item["agent_name"] for item in service._load_agent_frontends()["frontends"]] == [
        "ads_watching_agent",
        "agent_builder_agent",
        "zeta_agent",
    ]


def test_page_service_agent_websites_preserves_direct_route_targets(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "frontends": [
                {
                    "agent_name": "notes_agent",
                    "title": "Notes Library",
                    "description": "Read notes remotely",
                    "frontend_port_registered": True,
                    "route_mode": "direct_forward",
                    "direct_forward_port": 8094,
                    "launch_path": "/agent/notes_agent/",
                    "open_url": "http://127.0.0.1:8094/",
                }
            ],
        },
    )

    service = autoyou_page_service.AutoYouPageService()
    payload = service._load_agent_frontends()

    assert payload["frontends"][0]["route_mode"] == "direct_forward"
    assert payload["frontends"][0]["open_url"] == "http://127.0.0.1:8094/"
    assert payload["frontends"][0]["local_url"] == "http://127.0.0.1:8094/"


def test_page_service_feed_endpoint_forwards_limit_to_db(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    service = PageFeedService()
    service.db = _FakeFeedDb()
    app = FastAPI()
    service.register_routes(app)
    client = TestClient(app)

    response = client.get("/api/feed?days=0&limit=3")

    assert response.status_code == 200
    assert response.json() == {"items": [{"id": 1}, {"id": 2}]}
    assert service.db.last_query_items_kwargs == {
        "order": "desc",
        "ignore_date_default": True,
        "limit": 3,
    }


def test_page_agent_status_endpoint_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    service = PageFeedService()
    app = FastAPI()
    service.register_routes(app)
    client = TestClient(app)

    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "autoyou_page_agent"
    assert payload["status"] == "active"
    assert "range_blob_streaming" in payload["capabilities"]


def test_page_agent_theme_api_persists_shared_theme(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    service = PageFeedService()
    app = FastAPI()
    service.register_routes(app)
    client = TestClient(app)

    response = client.post("/api/ui/theme", json={"theme": "light"})

    assert response.status_code == 200
    assert response.json() == {"success": True, "theme": "light"}
    assert client.get("/api/ui/theme").json()["theme"] == "light"


def test_page_service_query_endpoint_forwards_sources_limit_and_timeline_all(monkeypatch):
    service = PageFeedService()
    service.db = _FakeFeedDb()
    app = FastAPI()
    service.register_routes(app)
    client = TestClient(app)

    response = client.get("/api/feed/query?sources=Telegram,Local&limit=2&timeline_all=1")

    assert response.status_code == 200
    assert response.json() == {"items": [{"id": 1}, {"id": 2}]}
    assert service.db.last_query_items_kwargs == {
        "order": "desc",
        "types": None,
        "source": None,
        "sources": ["Telegram", "Local"],
        "date_from": None,
        "date_to": None,
        "favourites_only": False,
        "tag_search": None,
        "limit": 2,
        "ignore_date_default": True,
    }


def test_page_agent_remote_webrtc_feed_requests_follow_access_role(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    service = PageFeedService()
    service.db = None
    app = FastAPI()
    service.register_routes(app)
    client = TestClient(app)
    admin_headers = {
        "X-AutoYou-WebRTC-Session-Id": "synthetic-session",
        "X-AutoYou-Remote-Access-Role": "admin",
    }
    viewer_headers = {
        "X-AutoYou-WebRTC-Session-Id": "synthetic-session",
        "X-AutoYou-Remote-Access-Role": "viewer",
    }
    editor_headers = {
        "X-AutoYou-WebRTC-Session-Id": "synthetic-session",
        "X-AutoYou-Remote-Access-Role": "editor",
    }

    read_response = client.get("/api/feed", headers=viewer_headers)
    assert read_response.status_code == 200

    admin_add = client.post(
        "/api/feed",
        headers=admin_headers,
        json={"url": "https://example.com/admin-item", "type": "article", "title": "Synthetic admin"},
    )
    assert admin_add.status_code == 200

    blocked_add = client.post(
        "/api/feed",
        headers=viewer_headers,
        json={"url": "https://example.com/viewer-item", "type": "article", "title": "Synthetic viewer"},
    )
    assert blocked_add.status_code == 403
    assert blocked_add.json()["remote_access_role"] == "viewer"
    assert blocked_add.json()["error"] == "Page feed write actions are only available from the local owner browser."

    editor_add = client.post(
        "/api/feed",
        headers=editor_headers,
        json={"url": "https://example.com/editor-item", "type": "article", "title": "Synthetic editor"},
    )
    assert editor_add.status_code == 200
    editor_item_id = editor_add.json()["item"]["id"]

    blocked_delete = client.delete(f"/api/feed/{editor_item_id}", headers=editor_headers)
    assert blocked_delete.status_code == 403
    assert blocked_delete.json()["remote_access_role"] == "editor"

    blocked_delete_alias = client.post(f"/api/feed/{editor_item_id}/delete", headers=editor_headers)
    assert blocked_delete_alias.status_code == 403
    assert blocked_delete_alias.json()["remote_access_role"] == "editor"

    blocked_clear = client.get("/api/feed/clear", headers=editor_headers)
    assert blocked_clear.status_code == 403

    local_add = client.post(
        "/api/feed",
        json={"url": "https://example.com/item", "type": "article", "title": "Synthetic"},
    )
    assert local_add.status_code == 200
    assert local_add.json()["item"]["title"] == "Synthetic"


def test_page_service_root_redirects_to_registry_default(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {"default_agent": "notes_agent", "frontends": []},
    )
    service = autoyou_page_service.AutoYouPageService()
    assert service._default_browser_path() == "/agent/notes_agent/"
    client = TestClient(service.app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/agent/notes_agent/"


def test_page_service_root_redirects_to_directory(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {"default_agent": "agent_websites", "frontends": []},
    )
    service = autoyou_page_service.AutoYouPageService()
    assert service._default_browser_path() == "/websites"
    client = TestClient(service.app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/websites"
    directory = client.get("/websites")
    assert directory.status_code == 200
    legacy = client.get("/agent-websites")
    assert legacy.status_code == 200


def test_page_service_root_redirects_fallback_page_agent(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {"default_agent": None, "frontends": []},
    )
    service = autoyou_page_service.AutoYouPageService()
    assert service._default_browser_path() == "/agent/page_agent/"
