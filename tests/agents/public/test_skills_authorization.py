"""Exercise skill mutation and execution guards through their real HTTP routes."""

import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def skill_app(tmp_path, monkeypatch):
    module = importlib.import_module("autoyou_agents.skills_agent.website.backend.app")
    monkeypatch.setattr(module, "_describe_chat_auth_state", lambda *_: {"authenticated": True})
    monkeypatch.setattr(module, "get_skills_root", lambda: tmp_path)
    from autoyou_agents.skills_agent import agent
    monkeypatch.setattr(agent, "get_skills_root", lambda: tmp_path)
    script = tmp_path / "synthetic-skill" / "scripts" / "run.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('synthetic skill completed')\n", encoding="utf-8")
    app = FastAPI()
    module._extra_routes(app, "skills_agent")
    return module, app, tmp_path


@pytest.mark.parametrize("role", ["viewer", "editor", "", "invalid"])
@pytest.mark.parametrize("method,path,payload", [
    ("POST", "/api/skills", {"name": "injected-skill", "description": "synthetic", "instructions": "synthetic"}),
    ("DELETE", "/api/skills/synthetic-skill", {}),
    ("POST", "/api/skills/synthetic-skill/run", {}),
])
def test_non_admin_cannot_mutate_or_run(skill_app, role, method, path, payload):
    _, app, root = skill_app
    headers = {"X-AutoYou-Remote-Browser": "webrtc", "X-AutoYou-Remote-Access-Role": role}
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.request(method, path, json=payload, headers=headers).status_code == 403
    assert (root / "synthetic-skill" / "scripts" / "run.py").exists()
    assert not (root / "injected-skill").exists()


@pytest.mark.parametrize("headers", [{}, {"X-AutoYou-Remote-Browser": "webrtc", "X-AutoYou-Remote-Access-Role": "admin"}])
def test_authenticated_local_and_remote_admin_can_run(skill_app, headers):
    _, app, _ = skill_app
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/skills/synthetic-skill/run", json={}, headers=headers)
    assert response.status_code == 200
    assert response.json()["exit_code"] == 0
    assert response.json()["stdout"].strip() == "synthetic skill completed"


def test_network_client_cannot_self_assert_admin(skill_app):
    _, app, _ = skill_app
    with TestClient(app, client=("198.51.100.20", 50000)) as client:
        response = client.post("/api/skills/synthetic-skill/run", json={}, headers={
            "X-AutoYou-Remote-Browser": "webrtc", "X-AutoYou-Remote-Access-Role": "admin",
        })
    assert response.status_code == 403


def test_unauthenticated_admin_marker_is_insufficient(skill_app, monkeypatch):
    module, app, _ = skill_app
    monkeypatch.setattr(module, "_describe_chat_auth_state", lambda *_: {"authenticated": False})
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.post("/api/skills/synthetic-skill/run", json={}).status_code == 401


@pytest.mark.parametrize("script", ["../scripts/run.py", "/scripts/run.py", "scripts/../run.py", "C:\\scripts\\run.py"])
def test_script_paths_are_rejected_instead_of_reinterpreted(skill_app, script):
    _, app, _ = skill_app
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.post("/api/skills/synthetic-skill/run", json={"script": script}).status_code == 400


@pytest.mark.parametrize("role", ["viewer", "editor", "", None])
@pytest.mark.parametrize("tool", ["create_or_save_skill", "delete_skill", "run_skill_script"])
def test_chat_tools_require_backend_authorized_admin(role, tool):
    from autoyou_agents.skills_agent.agent import _skills_before_tool_callback

    context = SimpleNamespace(state={"autoyou_authenticated_actor_role": role})
    result = _skills_before_tool_callback(SimpleNamespace(name=tool), {}, context)
    assert result["status"] == "error"


def test_chat_admin_and_readonly_tools_remain_available():
    from autoyou_agents.skills_agent.agent import _skills_before_tool_callback

    assert _skills_before_tool_callback(SimpleNamespace(name="create_or_save_skill"), {},
        SimpleNamespace(state={"autoyou_authenticated_actor_role": "admin"})) is None
    assert _skills_before_tool_callback(SimpleNamespace(name="list_skills"), {}, SimpleNamespace(state={})) is None
