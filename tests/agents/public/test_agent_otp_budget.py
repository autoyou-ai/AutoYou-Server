"""Synthetic callers share one guessing budget across agent login variants."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import sys

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from shared import agent_otp_attempts as attempts
from autoyou_agents.shared_tools import scheduler_mission_control as security


@pytest.fixture(autouse=True)
def isolated_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(attempts, "_database_path", lambda: tmp_path / "attempts.sqlite3")


def test_concurrent_failures_cannot_exceed_caller_budget():
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: attempts.reserve_attempt("synthetic-caller"), range(30)))
    assert sum(value is not None for value in results) == attempts.CLIENT_ATTEMPTS


def test_global_budget_is_not_divided_by_caller_or_agent():
    results = [attempts.reserve_attempt(f"synthetic-{index}") for index in range(50)]
    assert sum(value is not None for value in results) == attempts.GLOBAL_ATTEMPTS


def test_success_releases_only_its_own_attempt_and_windows_expire(monkeypatch):
    monkeypatch.setattr(attempts, "_clock", lambda: 1000.0)
    failed = [attempts.reserve_attempt("synthetic") for _ in range(4)]
    successful = attempts.reserve_attempt("synthetic")
    attempts.accept_attempt(successful)
    assert attempts.reserve_attempt("synthetic") is not None
    assert attempts.reserve_attempt("synthetic") is None
    assert len(set(failed)) == 4
    monkeypatch.setattr(attempts, "_clock", lambda: 1301.0)
    assert attempts.reserve_attempt("synthetic") is not None


def test_middleware_limits_alias_and_publishing_paths_before_verification(monkeypatch):
    monkeypatch.setattr(security, "_get_agent_security_settings", lambda _: {"auth_mode": "totp"})
    monkeypatch.setattr(security, "_runtime_server", lambda: SimpleNamespace())
    app = FastAPI()
    app.middleware("http")(security._agent_app_csrf_guard(agent_name="synthetic_agent"))
    seen = []

    async def login(request: Request):
        body = await request.json()
        seen.append(body)
        return JSONResponse({"success": body.get("code") == "123456"}, status_code=200 if body.get("code") == "123456" else 401)

    app.add_api_route("/api/auth/login", login, methods=["POST"])
    app.add_api_route("/api/game/publish-auth/login", login, methods=["POST"])
    with TestClient(app) as client:
        client.headers["Origin"] = "http://testserver"
        assert client.post("/api/auth/login", json={"code": "123456"}).status_code == 200
        for index in range(5):
            path = "/api/auth/login" if index % 2 else "/api/game/publish-auth/login"
            assert client.post(path, json={"code": "000000"}).status_code == 401
        response = client.post("/api/auth/login", json={"code": "123456"})
        assert response.status_code == 429
        assert response.headers["retry-after"] == "300"
        assert len(seen) == 6


def test_middleware_bounds_json_and_preserves_open_login(monkeypatch):
    monkeypatch.setattr(security, "_get_agent_security_settings", lambda _: {"auth_mode": "totp"})
    app = FastAPI()
    app.middleware("http")(security._agent_app_csrf_guard(agent_name="synthetic_agent"))

    @app.post("/api/auth/login")
    async def login():
        return {"success": True}

    with TestClient(app) as client:
        assert client.post("/api/auth/login", content=b"x" * 4097).status_code == 413
        assert client.post("/api/auth/login", json=[]).status_code == 400
        monkeypatch.setattr(security, "_get_agent_security_settings", lambda _: {"auth_mode": "open"})
        for _ in range(25):
            assert client.post("/api/auth/login", json={}).status_code == 200


def test_standalone_login_does_not_import_or_switch_to_core_auth(monkeypatch):
    monkeypatch.delitem(sys.modules, "server", raising=False)
    monkeypatch.setattr(security, "_runtime_server", lambda: pytest.fail("standalone login must not import server"))
    app = FastAPI()
    app.middleware("http")(security._agent_app_csrf_guard())

    @app.post("/api/auth/login")
    async def local_login(request: Request):
        return {"success": (await request.json()).get("code") == "123456"}

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"code": "123456"}).json()["success"] is True
    assert "server" not in sys.modules


def test_open_shell_cannot_expose_an_unlimited_otp_oracle(monkeypatch):
    monkeypatch.setattr(security, "_get_agent_security_settings", lambda _: {"auth_mode": "open"})
    app = FastAPI()
    app.middleware("http")(security._agent_app_csrf_guard(agent_name="synthetic_open_agent"))
    attempts_seen = []

    @app.post("/api/auth/login")
    async def otp_oracle(request: Request):
        attempts_seen.append(await request.json())
        return JSONResponse({"success": False}, status_code=401)

    with TestClient(app) as client:
        for _ in range(5):
            assert client.post("/api/auth/login", json={"code": "000000"}).status_code == 401
        assert client.post("/api/auth/login", json={"code": "000000"}).status_code == 429
    assert len(attempts_seen) == 5
