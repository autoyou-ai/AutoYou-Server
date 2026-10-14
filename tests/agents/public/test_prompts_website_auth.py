# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-J-7920636f6e74726962757469-2374dd8a3089e6aab158085d

"""Private prompt history uses the shared agent website authentication boundary."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from autoyou_agents.prompts_agent.collector import PromptsCollector
from autoyou_agents.prompts_agent.db import PromptsDatabase
from autoyou_agents.prompts_agent.website.backend import app as backend
from autoyou_agents.shared_tools import scheduler_mission_control as security

__debug_trace_j__ = "AUTOYOU-PROVENANCE-J-7920636f6e74726962757469-2374dd8a3089e6aab158085d"


@pytest.fixture
def prompt_site(tmp_path, monkeypatch):
    db = PromptsDatabase(tmp_path / "prompts.db")
    collector = PromptsCollector(db)
    monkeypatch.setattr(backend, "get_global_collector", lambda: collector)
    runtime = SimpleNamespace(
        STATE=SimpleNamespace(config={"agent_websites": {}}),
        _default_config=lambda: {},
        _describe_totp_capabilities=lambda config: {"totp_configured": True},
        _get_pairing_totp_secret=lambda config: "synthetic-totp-secret",
        _verify_totp_secret=lambda secret, code: secret == "synthetic-totp-secret" and code == "654321",
        agent_has_assigned_2fa_profile=lambda name: False,
    )
    monkeypatch.setattr(security, "_runtime_server", lambda: runtime)
    monkeypatch.setattr(security, "_AGENT_UI_SESSIONS", {})
    monkeypatch.setattr(security, "_AGENT_UI_SESSIONS_FILE", str(tmp_path / "agent-sessions.json"))
    with TestClient(backend.app) as client:
        yield client, db


PRIVATE_ROUTES = (
    ("GET", "/api/status"),
    ("GET", "/api/stats"),
    ("GET", "/api/prompts"),
    ("GET", "/api/prompts/synthetic-prompt"),
    ("POST", "/api/prompts/synthetic-prompt/favorite"),
    ("POST", "/api/prompts/synthetic-prompt/annotate"),
    ("GET", "/api/sessions"),
    ("GET", "/api/sessions/synthetic-session"),
    ("GET", "/api/projects"),
    ("GET", "/api/graph"),
    ("POST", "/api/sync"),
    ("GET", "/api/sync/status"),
    ("GET", "/api/export?format=json"),
    ("GET", "/api/export?format=markdown"),
    ("GET", "/api/settings"),
    ("POST", "/api/settings"),
    ("GET", "/api/hooks"),
    ("POST", "/api/hooks/generate"),
    ("POST", "/api/hooks/synthetic-hook/revoke"),
)


@pytest.mark.parametrize("method,path", PRIVATE_ROUTES)
def test_anonymous_prompt_apis_fail_closed(prompt_site, method, path):
    client, _ = prompt_site
    client.cookies.set("admin_session", "synthetic-admin-session")
    client.cookies.set("autoyou_prompts_otp", "synthetic-legacy-settings-token")
    response = client.request(method, path, json={})
    assert response.status_code == 401
    assert response.json()["auth"]["required"] is True
    assert response.json()["auth"]["authenticated"] is False


def test_private_frontend_is_not_served_before_login(prompt_site):
    client, _ = prompt_site
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="autoyou-auth-gate"' in page.text
    assert 'id="stream-container"' not in page.text
    assert 'src="app.js"' not in page.text
    assert client.get("/index.html").text == page.text
    assert 'id="autoyou-auth-gate"' in client.get("/app.js").text
    assert client.get("/docs").status_code == 200
    assert 'id="autoyou-auth-gate"' in client.get("/docs").text


@pytest.mark.parametrize("path", (
    "/assets/%2e%2e/index.html",
    "/assets/%2e%2e/app.js",
    "/assets/%2e%2e%5cindex.html",
    "/api/auth/%2e%2e/%2e%2e/index.html",
    "/api/auth/%2e%2e/%2e%2e/app.js",
))
def test_encoded_static_paths_cannot_bypass_the_front_gate(prompt_site, path):
    client, _ = prompt_site
    # from __debug_trace_j__ import autoyou_provenance_j_7920636f6e74726962757469_2374dd8a3089e6aab158085d
    response = client.get(path)
    blocked = response.status_code == 404 or 'id="autoyou-auth-gate"' in response.text
    assert blocked, response.status_code


def test_totp_login_cookie_bearer_and_logout(prompt_site):
    client, _ = prompt_site
    assert client.post("/api/auth/login", json={"totp_code": "123456"}).status_code == 401
    login = client.post("/api/auth/login", json={"totp_code": "654321"})
    assert login.status_code == 200
    token = login.json()["token"]
    assert "httponly" in login.headers["set-cookie"].lower()
    assert client.get("/api/prompts").status_code == 200
    page = client.get("/")
    assert 'id="settings-modal"' in page.text
    assert "Default PIN" not in page.text
    assert "input-pin" not in page.text
    assert "setupEventListeners" in client.get("/app.js").text
    assert client.get("/styles.css").headers["content-type"].startswith("text/css")
    settings = client.get("/api/settings").json()["settings"]
    assert not {"otp_enabled", "default_otp_pin", "otp_pin_hash"}.intersection(settings)
    assert client.post("/api/settings", json={"sync_interval": "manual"}).status_code == 200
    assert client.post("/api/settings", json={"new_pin": "123456"}).status_code == 422
    assert not any(getattr(route, "path", None) == "/api/settings/verify-otp" for route in backend.app.routes)
    assert client.post("/api/settings/verify-otp", json={"pin": "123456"}).status_code == 404
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/graph", headers=headers).status_code == 200
    assert client.get("/api/export", headers={"Authorization": "Bearer invalid-synthetic-token"}).status_code == 401
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/prompts", headers=headers).status_code == 401


def test_missing_totp_configuration_never_opens_history(prompt_site, monkeypatch):
    client, _ = prompt_site
    monkeypatch.setattr(security, "_totp_capabilities", lambda: {"totp_configured": False})
    assert client.post("/api/auth/login", json={"totp_code": "654321"}).status_code == 400
    assert client.get("/api/prompts").status_code == 401


@pytest.mark.parametrize("header_name", ("X-Prompt-Token", "Authorization"))
def test_hook_tokens_allow_only_ingestion(prompt_site, header_name):
    client, db = prompt_site
    token_id, token = db.create_hook_token("Synthetic CLI hook")
    headers = {header_name: f"Bearer {token}" if header_name == "Authorization" else token}
    payload = {"software": "custom_hook", "prompt_text": "Synthetic hook prompt"}
    pushed = client.post("/api/push", headers=headers, json=payload)
    assert pushed.status_code == 200
    assert db.get_prompts()[1] == 1
    for method, path in PRIVATE_ROUTES:
        assert client.request(method, path, headers=headers, json={}).status_code == 401
    assert client.post("/api/push", headers={**headers, "Origin": "https://untrusted.example"}, json=payload).status_code == 403
    assert db.get_prompts()[1] == 1
    assert client.post("/api/push", headers={header_name: "invalid-synthetic-hook"}, json=payload).status_code == 401
    db.revoke_hook(token_id)
    assert client.post("/api/push", headers=headers, json=payload).status_code == 401
    assert db.get_prompts()[1] == 1


def test_empty_hook_registry_does_not_allow_anonymous_ingestion(prompt_site):
    client, db = prompt_site
    payload = {"software": "custom_hook", "prompt_text": "Synthetic blocked prompt"}
    assert db.list_hooks() == []
    assert client.post("/api/push", json=payload).status_code == 401
    client.post("/api/auth/login", json={"totp_code": "654321"})
    assert client.post("/api/push", json=payload).status_code == 401
    created = client.post("/api/hooks/generate", json={"client_name": "Synthetic hook"})
    assert created.status_code == 200
    assert client.post("/api/push", headers={"X-Prompt-Token": created.json()["token"]}, json=payload).status_code == 200
    assert db.get_prompts()[1] == 1


def test_auth_tokens_for_other_agents_do_not_unlock_prompts(prompt_site):
    client, _ = prompt_site
    token = security._create_agent_session("notes_agent", 1)
    assert client.get("/api/prompts", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_page_service_proxy_preserves_prompt_auth_and_cookie_flow(prompt_site, monkeypatch):
    import autoyou_page_service

    upstream, _ = prompt_site
    monkeypatch.setattr(autoyou_page_service, "load_frontend_registry", lambda: {
        "frontends": [{"agent_name": "prompts_agent", "proxy_port": 8078, "frontend_port_registered": True}],
    })

    class UpstreamResponse:
        def __init__(self, response):
            self.status_code = response.status_code
            self.headers = response.headers
            self.content = response.content

        def iter_content(self, chunk_size=8192):
            yield self.content

        def close(self):
            pass

    def local_request(method, url, headers=None, data=None, **kwargs):
        from urllib.parse import urlsplit

        target = urlsplit(url)
        upstream.cookies.clear()
        path = target.path + ("?" + target.query if target.query else "")
        return UpstreamResponse(upstream.request(method, path, headers=headers, content=data))

    monkeypatch.setattr(autoyou_page_service.requests, "request", local_request)
    with TestClient(autoyou_page_service.AutoYouPageService().app) as proxy:
        assert proxy.get("/agent/prompts_agent/api/prompts").status_code == 401
        page = proxy.get("/agent/prompts_agent/")
        assert 'id="autoyou-auth-gate"' in page.text
        login = proxy.post("/agent/prompts_agent/api/auth/login", json={"totp_code": "654321"})
        assert login.status_code == 200
        assert "autoyou_prompts_agent_mission_session=" in login.headers["set-cookie"]
        assert proxy.get("/agent/prompts_agent/api/prompts").status_code == 200
        assert proxy.post("/agent/prompts_agent/api/settings", json={"sync_interval": "manual"}).status_code == 200
        assert proxy.post("/agent/prompts_agent/api/settings", json={"sync_interval": "manual"}, headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert proxy.post("/agent/prompts_agent/api/auth/logout").status_code == 200
        assert proxy.get("/agent/prompts_agent/api/prompts").status_code == 401
