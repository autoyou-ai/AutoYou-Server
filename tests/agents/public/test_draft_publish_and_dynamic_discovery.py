# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-13bffc91510b83e0877ce161

"""Tests for Fix A (dynamic agent discovery in _managed_frontend_runtime_specs)
and Fix B (Draft→Live publish endpoint + builder UI two-step publish flow).

Imports real server.py (same pattern as test_agent_websites_security_routes.py).
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import importlib.machinery
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import server

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-13bffc91510b83e0877ce161"


# ---------------------------------------------------------------------------
# Fix A - dynamic agent discovery in _managed_frontend_runtime_specs
# ---------------------------------------------------------------------------

def test_dynamic_agent_specs_added_when_installed_and_has_website_backend(tmp_path, monkeypatch):
    """An installed agent with website/manifest.json + backend/app.py gets auto-discovered."""
    agents_root = tmp_path / "autoyou_agents"
    custom_dir = agents_root / "custom_agent" / "website"
    custom_dir.mkdir(parents=True)
    (agents_root / "custom_agent" / "website" / "backend").mkdir()
    (agents_root / "custom_agent" / "website" / "backend" / "app.py").write_text("app = None\n", encoding="utf-8")
    manifest = {"agent_name": "custom_agent", "requires_proxy_registration": True, "recommended_port": 8099}
    (agents_root / "custom_agent" / "website" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def fake_load_registry(agents_root=None, **_):
        return {"installed_agents": ["custom_agent"]}

    monkeypatch.setattr(server, "load_agent_install_registry", fake_load_registry)
    monkeypatch.setattr(server, "_workspace_agents_root", lambda: agents_root)
    # Patch load_frontend_manifest to use the real file
    import autoyou_agents.shared_tools.frontend_manifest as fm
    monkeypatch.setattr(server, "load_frontend_manifest", fm.load_frontend_manifest)

    specs = server._managed_frontend_runtime_specs()

    assert "custom_agent" in specs
    assert specs["custom_agent"]["app_import"] == "autoyou_agents.custom_agent.website.backend.app:app"
    assert specs["custom_agent"]["recommended_port"] == 8099


def test_dynamic_agent_specs_added_for_compiled_extension_backend(tmp_path, monkeypatch):
    """Compiled built-in agents may ship website/backend/app as a native extension."""
    agents_root = tmp_path / "autoyou_agents"
    backend_dir = agents_root / "compiled_agent" / "website" / "backend"
    backend_dir.mkdir(parents=True)
    extension_suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
    (backend_dir / f"app{extension_suffix}").write_bytes(b"")
    manifest = {"agent_name": "compiled_agent", "requires_proxy_registration": True, "recommended_port": 8098}
    (agents_root / "compiled_agent" / "website" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(server, "load_agent_install_registry", lambda agents_root=None, **_: {"installed_agents": ["compiled_agent"]})
    monkeypatch.setattr(server, "_workspace_agents_root", lambda: agents_root)
    import autoyou_agents.shared_tools.frontend_manifest as fm
    monkeypatch.setattr(server, "load_frontend_manifest", fm.load_frontend_manifest)

    specs = server._managed_frontend_runtime_specs()

    assert "compiled_agent" in specs
    assert specs["compiled_agent"]["app_import"] == "autoyou_agents.compiled_agent.website.backend.app:app"
    assert specs["compiled_agent"]["recommended_port"] == 8098


def test_dynamic_agent_without_backend_not_added(tmp_path, monkeypatch):
    """An installed agent without website/backend/app.py is NOT added (no proxy needed)."""
    agents_root = tmp_path / "autoyou_agents"
    (agents_root / "headless_agent" / "website").mkdir(parents=True)
    manifest = {"agent_name": "headless_agent", "requires_proxy_registration": True}
    (agents_root / "headless_agent" / "website" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(server, "load_agent_install_registry", lambda agents_root=None, **_: {"installed_agents": ["headless_agent"]})
    monkeypatch.setattr(server, "_workspace_agents_root", lambda: agents_root)
    import autoyou_agents.shared_tools.frontend_manifest as fm
    monkeypatch.setattr(server, "load_frontend_manifest", fm.load_frontend_manifest)

    specs = server._managed_frontend_runtime_specs()
    assert "headless_agent" not in specs


def test_dynamic_agent_without_proxy_flag_not_added(tmp_path, monkeypatch):
    """An installed agent with requires_proxy_registration=false is NOT added."""
    agents_root = tmp_path / "autoyou_agents"
    backend = agents_root / "quiet_agent" / "website" / "backend"
    backend.mkdir(parents=True)
    (backend / "app.py").write_text("app = None\n", encoding="utf-8")
    manifest = {"agent_name": "quiet_agent", "requires_proxy_registration": False}
    (agents_root / "quiet_agent" / "website" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(server, "load_agent_install_registry", lambda agents_root=None, **_: {"installed_agents": ["quiet_agent"]})
    monkeypatch.setattr(server, "_workspace_agents_root", lambda: agents_root)
    import autoyou_agents.shared_tools.frontend_manifest as fm
    monkeypatch.setattr(server, "load_frontend_manifest", fm.load_frontend_manifest)

    specs = server._managed_frontend_runtime_specs()
    assert "quiet_agent" not in specs


def test_dynamic_agent_already_in_managed_apps_not_duplicated(monkeypatch):
    """An agent already in MANAGED_FRONTEND_APPS is NOT double-added."""
    monkeypatch.setattr(server, "load_agent_install_registry", lambda agents_root=None, **_: {"installed_agents": ["notes_agent"]})
    monkeypatch.setattr(server, "_workspace_agents_root", lambda: Path("/nonexistent"))

    specs = server._managed_frontend_runtime_specs()
    notes_keys = [k for k in specs if k == "notes_agent"]
    assert len(notes_keys) <= 1


def test_dynamic_discovery_error_does_not_crash(monkeypatch):
    """If registry load raises, _managed_frontend_runtime_specs returns MANAGED_FRONTEND_APPS specs."""
    monkeypatch.setattr(server, "load_agent_install_registry", lambda **_: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(server, "_workspace_agents_root", lambda: Path("/nonexistent"))

    specs = server._managed_frontend_runtime_specs()
    assert isinstance(specs, dict)


# ---------------------------------------------------------------------------
# Fix B - POST /api/agents/draft/{agent_name}/publish admin endpoint
# ---------------------------------------------------------------------------

def test_draft_publish_endpoint_registered():
    """The /api/agents/draft/{agent_name}/publish POST route exists on admin_app."""
    routes = [getattr(r, "path", "") for r in server.admin_app.routes]
    assert any("/api/agents/draft/{agent_name}/publish" in r for r in routes)


def test_draft_publish_returns_403_when_compiled(monkeypatch):
    monkeypatch.setattr(server, "_require_loopback_or_token", lambda req: None)
    monkeypatch.setattr(server, "publish_agent_draft",
                        lambda name, agents_root=None: (_ for _ in ()).throw(
                            PermissionError("Workspace drafts cannot be published into a packaged runtime.")))

    req = MagicMock()
    result = asyncio.run(server.admin_publish_agent_draft("my_agent", req))
    body = json.loads(result.body)
    assert result.status_code == 403
    assert body["success"] is False
    assert body.get("compiled_block") is True


def test_draft_publish_returns_404_when_no_draft(monkeypatch):
    monkeypatch.setattr(server, "_require_loopback_or_token", lambda req: None)
    monkeypatch.setattr(server, "publish_agent_draft",
                        lambda name, agents_root=None: (_ for _ in ()).throw(
                            FileNotFoundError("No workspace draft exists.")))

    req = MagicMock()
    result = asyncio.run(server.admin_publish_agent_draft("ghost_agent", req))
    body = json.loads(result.body)
    assert result.status_code == 404
    assert body["success"] is False


def test_draft_publish_returns_422_when_not_ready(monkeypatch):
    monkeypatch.setattr(server, "_require_loopback_or_token", lambda req: None)
    monkeypatch.setattr(server, "publish_agent_draft",
                        lambda name, agents_root=None: (_ for _ in ()).throw(
                            ValueError("Draft is not publish-ready.")))

    req = MagicMock()
    result = asyncio.run(server.admin_publish_agent_draft("bad_agent", req))
    body = json.loads(result.body)
    assert result.status_code == 422
    assert body["success"] is False


def test_draft_publish_returns_200_on_success(monkeypatch):
    monkeypatch.setattr(server, "_require_loopback_or_token", lambda req: None)
    monkeypatch.setattr(server, "publish_agent_draft",
                        lambda name, agents_root=None: {"agent_name": name, "published_files": ["agent.py"]})
    monkeypatch.setattr(server, "_build_agent_builder_listing_payload",
                        lambda: {"status": "success", "agent_details": {}})
    monkeypatch.setattr(server, "_sync_frontend_registry_from_builder_payload", lambda p: None)

    req = MagicMock()
    result = asyncio.run(server.admin_publish_agent_draft("my_agent", req))
    body = json.loads(result.body)
    assert result.status_code == 200
    assert body["success"] is True
    assert body["agent_name"] == "my_agent"
    assert "agent.py" in body["published_files"]


def test_draft_publish_returns_500_on_unexpected_error(monkeypatch):
    monkeypatch.setattr(server, "_require_loopback_or_token", lambda req: None)
    monkeypatch.setattr(server, "publish_agent_draft",
                        lambda name, agents_root=None: (_ for _ in ()).throw(RuntimeError("unexpected")))

    req = MagicMock()
    result = asyncio.run(server.admin_publish_agent_draft("my_agent", req))
    body = json.loads(result.body)
    assert result.status_code == 500
    assert body["success"] is False


# ---------------------------------------------------------------------------
# Fix B - builder UI backend two-step publish
# ---------------------------------------------------------------------------

def test_builder_publish_route_registered():
    """The /api/agents/{name}/publish POST route exists on the builder app."""
    import sys
    import types

    # Ensure the server stub is in place before importing scheduler_mission_control
    if "server" not in sys.modules or not hasattr(sys.modules["server"], "_is_logged_in"):
        stub = types.ModuleType("server")
        stub.STATE = type("S", (), {"config": {"security": {"totp_secret": ""}, "ui_security": {}, "agent_websites": {"require_otp": False}}, "admin_session_token": "tok"})()
        stub._default_config = lambda: {"security": {"totp_secret": ""}, "ui_security": {}, "agent_websites": {"require_otp": False}}
        stub._is_logged_in = lambda req: False
        stub._verify_totp_secret = lambda s, c: False
        stub._persist_state_config = lambda cfg: None
        stub._describe_totp_capabilities = lambda cfg: {"totp_configured": False, "totp_required": False}
        sys.modules["server"] = stub

    import autoyou_agents.agent_builder_agent.website.backend.app as builder_app
    route_paths = [getattr(r, "path", "") for r in builder_app.app.routes]
    # from __debug_provenance_a__ import schedule
    assert any("/api/agents/{name}/publish" in p for p in route_paths)


def test_builder_publish_propagates_compiled_block_403():
    """builder_publish_agent propagates 403 compiled_block without calling install."""
    import sys
    import types

    if "server" not in sys.modules or not hasattr(sys.modules["server"], "_is_logged_in"):
        stub = types.ModuleType("server")
        stub.STATE = type("S", (), {"config": {"security": {"totp_secret": ""}, "ui_security": {}, "agent_websites": {"require_otp": False}}, "admin_session_token": "tok"})()
        stub._default_config = lambda: {"security": {"totp_secret": ""}, "ui_security": {}, "agent_websites": {"require_otp": False}}
        stub._is_logged_in = lambda req: False
        stub._verify_totp_secret = lambda s, c: False
        stub._persist_state_config = lambda cfg: None
        stub._describe_totp_capabilities = lambda cfg: {"totp_configured": False, "totp_required": False}
        sys.modules["server"] = stub

    import autoyou_agents.agent_builder_agent.website.backend.app as builder_app
    import autoyou_agents.shared_tools.scheduler_mission_control as smc

    install_called = []

    class _FakeCtx:
        def __init__(self, status, body):
            self.status = status
            self._body = body

        async def json(self, content_type=None):
            return self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class FakeSession:
        def post(self, url, json=None, headers=None, timeout=None):
            if "/draft/" in url and "/publish" in url:
                return _FakeCtx(403, {"success": False, "error": "Packaged runtime.", "compiled_block": True})
            install_called.append(url)
            return _FakeCtx(200, {"success": True})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    from fastapi.testclient import TestClient
    with patch.object(builder_app, "_describe_chat_auth_state", return_value={"authenticated": True}):
        with patch("aiohttp.ClientSession", return_value=FakeSession()):
            client = TestClient(builder_app.app, raise_server_exceptions=True)
            resp = client.post("/api/agents/custom_agent/publish")
            assert resp.status_code == 403
            assert resp.json()["compiled_block"] is True
            assert not install_called


def test_builder_publish_proceeds_to_install_when_no_draft():
    """builder_publish_agent proceeds to install when draft publish returns 404."""
    import sys
    import types

    if "server" not in sys.modules or not hasattr(sys.modules["server"], "_is_logged_in"):
        stub = types.ModuleType("server")
        stub.STATE = type("S", (), {"config": {"security": {"totp_secret": ""}, "ui_security": {}, "agent_websites": {"require_otp": False}}, "admin_session_token": "tok"})()
        stub._default_config = lambda: {"security": {"totp_secret": ""}, "ui_security": {}, "agent_websites": {"require_otp": False}}
        stub._is_logged_in = lambda req: False
        stub._verify_totp_secret = lambda s, c: False
        stub._persist_state_config = lambda cfg: None
        stub._describe_totp_capabilities = lambda cfg: {"totp_configured": False, "totp_required": False}
        sys.modules["server"] = stub

    import autoyou_agents.agent_builder_agent.website.backend.app as builder_app
    import autoyou_agents.shared_tools.scheduler_mission_control as smc

    install_called = []

    class _FakeCtx:
        def __init__(self, status, body):
            self.status = status
            self._body = body

        async def json(self, content_type=None):
            return self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class FakeSession:
        def post(self, url, json=None, headers=None, timeout=None):
            if "/draft/" in url and "/publish" in url:
                return _FakeCtx(404, {"success": False, "error": "No draft exists."})
            install_called.append(url)
            return _FakeCtx(200, {"success": True, "agent_name": "live_agent", "requires_restart": True})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    from fastapi.testclient import TestClient
    with patch.object(builder_app, "_describe_chat_auth_state", return_value={"authenticated": True}):
        with patch("aiohttp.ClientSession", return_value=FakeSession()):
            client = TestClient(builder_app.app, raise_server_exceptions=True)
            resp = client.post("/api/agents/live_agent/publish")
            assert resp.status_code == 200
            assert resp.json()["success"] is True
            assert any("/agents/install" in u for u in install_called)
