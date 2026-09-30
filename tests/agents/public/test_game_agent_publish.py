"""Game Studio publishes bounded HTML into test-scoped server storage."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from autoyou_agents.game_agent.website.backend import app as game_studio


def test_game_studio_publish_requires_auth_and_saves_html(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    auth = {"authenticated": False, "auth_mode": "totp"}
    server = SimpleNamespace(
        __file__=str(tmp_path / "server.py"),
        get_mutable_data_dir=lambda *_args, **_kwargs: tmp_path,
        _is_logged_in=lambda _request: False,
    )
    monkeypatch.setattr(game_studio._smc, "_describe_chat_auth_state", lambda *_args: auth)
    monkeypatch.setattr(game_studio._smc, "_runtime_server", lambda: server)
    html = b"<html><head></head><body>synthetic game</body></html>"
    target = tmp_path / "mobile_games" / "synthetic-runner.html"

    with TestClient(game_studio.app) as client:
        assert client.post("/api/game/publish/synthetic-runner", content=html).status_code == 401
        auth.update(authenticated=True, auth_mode="open")
        assert client.post("/api/game/publish/synthetic-runner", content=html).status_code == 401
        auth["auth_mode"] = "totp"
        assert client.post("/api/game/publish/INVALID", content=html).status_code == 400
        assert client.post("/api/game/publish/synthetic-runner", content=b"<html>no head</html>").status_code == 400
        assert client.post("/api/game/publish/synthetic-runner", content=b"\xff<head></head>").status_code == 400
        assert client.post("/api/game/publish/synthetic-runner", content=b"x" * (511 * 1024 + 1)).status_code == 413
        assert not target.exists()
        assert client.post("/api/game/publish/synthetic-runner", content=html).status_code == 200
        assert target.read_bytes() == html
