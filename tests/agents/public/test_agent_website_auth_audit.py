from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoyou_agents.shared_tools import scheduler_mission_control


ROOT = Path(__file__).resolve().parents[3]
BACKENDS = ROOT / "autoyou_agents"
SHARED_AUTH_MARKERS = (
    "create_agent_chat_app",
    "create_agent_website_app",
    "create_scheduler_mission_control_app",
    "install_agent_website_auth",
)


def _backend_apps():
    return sorted(BACKENDS.rglob("*_agent/website/backend/app.py"))


def test_every_agent_website_backend_uses_the_shared_auth_boundary():
    missing = []
    for path in _backend_apps():
        source = path.read_text(encoding="utf-8")
        if not any(marker in source for marker in SHARED_AUTH_MARKERS):
            missing.append(path.relative_to(ROOT).as_posix())

    assert missing == []


def test_agent_websites_never_accept_the_admin_ui_session_as_agent_auth():
    offenders = []
    for path in _backend_apps():
        source = path.read_text(encoding="utf-8")
        if "._is_logged_in(request)" in source:
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []


def test_every_agent_frontend_declares_a_mobile_viewport():
    missing = []
    for path in sorted(
        path
        for path in BACKENDS.rglob("index.html")
        if "website" in path.parts and "frontend" in path.parts
    ):
        source = path.read_text(encoding="utf-8")
        if 'name="viewport"' not in source.lower() and "name='viewport'" not in source.lower():
            missing.append(path.relative_to(ROOT).as_posix())

    assert missing == []


@pytest.mark.parametrize(
    ("module_name", "private_path"),
    (
        ("autoyou_agents.audio_agent.website.backend.app", "/api/library"),
    ),
)
def test_bespoke_sites_with_gated_defaults_fail_closed(monkeypatch, module_name, private_path):
    import importlib

    monkeypatch.setattr(
        scheduler_mission_control,
        "_describe_auth_state",
        lambda request, agent_name: {"required": True, "authenticated": False, "via": "none"},
    )
    backend = importlib.import_module(module_name)
    if hasattr(backend, "_check_auth"):
        monkeypatch.setattr(backend, "_check_auth", lambda request: False)

    client = TestClient(backend.app)
    client.cookies.set("admin_session", "synthetic-admin-session")

    assert client.get(private_path).status_code == 401
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="autoyou-auth-gate"' in page.text


@pytest.mark.parametrize("agent_name", ("ads_watching_agent", "notes_agent", "page_agent"))
def test_public_default_agent_manifests_stay_open_under_global_otp(agent_name):
    settings = scheduler_mission_control._resolve_manifest_auth_settings(agent_name)

    assert settings["auth_default"] == "open"
    assert settings["bypass_global_otp"] is True


def test_audio_default_is_open_but_global_otp_can_still_gate_it():
    settings = scheduler_mission_control._resolve_manifest_auth_settings("audio_agent")

    assert settings["auth_default"] == "open"
    assert settings["bypass_global_otp"] is False


def test_page_tool_has_a_loopback_only_non_browser_service_path(monkeypatch):
    from autoyou_agents.page_agent.website.backend import app as page_backend

    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "synthetic-page-tool-token")
    monkeypatch.setattr(
        scheduler_mission_control,
        "_describe_auth_state",
        lambda request, agent_name: {"required": True, "authenticated": False, "via": "none"},
    )
    client = TestClient(page_backend.app)
    headers = {"Authorization": "Bearer synthetic-page-tool-token"}

    assert client.get("/api/feed").status_code == 401
    assert client.get("/api/feed", headers=headers).status_code == 200
    assert client.get(
        "/api/feed",
        headers={"Authorization": "Bearer wrong-page-tool-token"},
    ).status_code == 401
    assert client.get(
        "/api/feed",
        headers={**headers, "Origin": "https://untrusted.example"},
    ).status_code != 200
