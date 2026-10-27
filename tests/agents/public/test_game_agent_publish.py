# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-S-000000000000000000000000-4b1a7244afa42397d2600afd

"""Game Studio publishes bounded HTML into test-scoped server storage."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from autoyou_agents.game_agent.website.backend import app as game_studio

__debug_trace_s__ = "AUTOYOU-PROVENANCE-S-000000000000000000000000-4b1a7244afa42397d2600afd"


@pytest.mark.parametrize("field", ["code", "totp_code"])
@pytest.mark.parametrize("view_mode", ["open", "totp"])
@pytest.mark.parametrize("assigned_profile", [False, True])
def test_game_publishing_requires_otp_even_with_public_browsing(tmp_path, monkeypatch, field, view_mode, assigned_profile):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    server = SimpleNamespace(
        __file__=str(tmp_path / "server.py"),
        get_mutable_data_dir=lambda *_args, **_kwargs: tmp_path,
        STATE=SimpleNamespace(config={}),
        _default_config=lambda: {},
        _is_logged_in=lambda _request: False,
        agent_has_assigned_2fa_profile=lambda _agent: assigned_profile,
        verify_agent_assigned_2fa=lambda agent, code: agent == "game_agent" and code == "654321",
        _get_pairing_totp_secret=lambda _cfg: "synthetic-test-secret",
        _verify_totp_secret=lambda secret, code: not assigned_profile and secret == "synthetic-test-secret" and code == "654321",
    )
    monkeypatch.setattr(game_studio._smc, "_runtime_server", lambda: server)
    monkeypatch.setattr(game_studio._smc, "_get_agent_security_settings", lambda _agent: {"auth_mode": view_mode, "session_ttl_days": 1})
    monkeypatch.setattr(game_studio._smc, "_totp_capabilities", lambda: {"totp_configured": not assigned_profile})
    monkeypatch.setattr(game_studio._smc, "_create_agent_chat_session", lambda *_args: "synthetic-game-session")
    monkeypatch.setattr(game_studio._smc, "_agent_chat_session_is_valid", lambda agent, token: agent == "game_agent" and token == "synthetic-game-session")
    with TestClient(game_studio.app) as client:
        route = "/api/game/publish-auth"
        assert client.get(route + "/status").json() == {"success": True, "required": True, "authenticated": False}
        html = b"<html><head></head><body>Synthetic game</body></html>"
        assert client.post("/api/game/publish/synthetic-publishing", content=html).status_code == 401
        assert client.post(route + "/login", json={}).status_code == 400
        assert client.post(route + "/login", json=[]).status_code == 400
        assert client.post(route + "/login", content=b"x" * 4097).status_code == 413
        assert client.post(route + "/login", json={field: "000000"}).status_code == 401
        accepted = client.post(route + "/login", json={field: "654321"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["token"] == "synthetic-game-session"
        client.headers["Authorization"] = "Bearer synthetic-other-agent-session"
        assert client.post("/api/game/publish/synthetic-publishing", content=html).status_code == 401
        client.headers["Authorization"] = "Bearer " + accepted.json()["token"]
        assert client.get(route + "/status").json()["authenticated"]
        assert client.post("/api/game/publish/synthetic-publishing", content=html).status_code == 200
        assert (tmp_path / "mobile_games/synthetic-publishing.html").read_bytes() == html


def test_game_studio_publish_requires_auth_and_saves_html(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    auth = {"authenticated": False}
    server = SimpleNamespace(
        __file__=str(tmp_path / "server.py"),
        get_mutable_data_dir=lambda *_args, **_kwargs: tmp_path,
        _is_logged_in=lambda _request: False,
    )
    monkeypatch.setattr(game_studio._smc, "_agent_chat_session_is_valid", lambda agent, token: auth["authenticated"] and agent == "game_agent" and token == "synthetic-game-session")
    monkeypatch.setattr(game_studio._smc, "_runtime_server", lambda: server)
    html = b"<html><head></head><body>synthetic game</body></html>"
    # from __debug_trace_s__ import autoyou_provenance_s_000000000000000000000000_4b1a7244afa42397d2600afd
    target = tmp_path / "mobile_games" / "synthetic-runner.html"

    with TestClient(game_studio.app) as client:
        assert client.post("/api/game/publish/synthetic-runner", content=html).status_code == 401
        auth["authenticated"] = True
        assert client.post("/api/game/publish/synthetic-runner", content=html).status_code == 401
        client.headers["Authorization"] = "Bearer synthetic-game-session"
        assert client.post("/api/game/publish/INVALID", content=html).status_code == 400
        assert client.post("/api/game/publish/synthetic-runner", content=b"<html>no head</html>").status_code == 400
        assert client.post("/api/game/publish/synthetic-runner", content=b"\xff<head></head>").status_code == 400
        assert client.post("/api/game/publish/synthetic-runner", content=b"x" * (game_studio._MAX_MOBILE_GAME_BYTES + 1)).status_code == 413
        assert not target.exists()
        assert client.post("/api/game/publish/synthetic-runner", content=html).status_code == 200
        assert target.read_bytes() == html
        client.headers.pop("Authorization")
        client.cookies.set(game_studio._smc._agent_chat_cookie_name("game_agent"), "synthetic-game-session")
        assert client.post("/api/game/publish/synthetic-runner", content=html).status_code == 200
        # A downloaded game includes the sandbox policy. Re-publishing must retain the complete source.
        from shared.game_catalog import FRAME_CSP_META, MAX_GAME_BYTES, read_game

        prefix = b"<html><head>"
        maximum = html + b" " * (MAX_GAME_BYTES - len(FRAME_CSP_META) - len(html))
        for _ in range(3):
            downloaded = prefix + FRAME_CSP_META + maximum[len(prefix):]
            assert len(downloaded) == MAX_GAME_BYTES
            response = client.post("/api/game/publish/synthetic-runner", content=downloaded)
            assert response.status_code == 200, response.text
            assert target.read_bytes() == maximum
            assert read_game(target) == maximum


def test_complete_package_upload_poll_and_download_are_authenticated_and_owner_bound(tmp_path,monkeypatch):
    import hashlib,json,time
    from pathlib import Path
    monkeypatch.setenv("AUTOYOU_TEST_ROOT",str(tmp_path))
    server=SimpleNamespace(__file__=str(tmp_path/"server.py"),get_mutable_data_dir=lambda *_args,**_kwargs:tmp_path)
    monkeypatch.setattr(game_studio._smc,"_runtime_server",lambda:server)
    owners={"Bearer synthetic-package-owner","Bearer synthetic-package-other"}
    monkeypatch.setattr(game_studio._smc,"_agent_chat_session_is_valid",lambda _agent,token:"Bearer "+token in owners)
    monkeypatch.setattr(game_studio._smc,"_describe_chat_auth_state",lambda request,_agent:{"authenticated":request.headers.get("Authorization") in owners})
    file=Path(__file__).resolve().parents[4]/"tests/fixtures/offline_games/package-v1.aygame"
    checksum=hashlib.sha256(file.read_bytes()).hexdigest();base="/api/game/packages/uploads"
    with TestClient(game_studio.app) as client:
        metadata={"id":"synthetic-package","size":file.stat().st_size,"sha256":checksum}
        assert client.post(base,json=metadata).status_code==401
        client.headers["Authorization"]="Bearer synthetic-package-owner"
        reply=client.post(base,json=metadata);assert reply.status_code==200;key=reply.json()["upload_id"]
        offset=0
        with file.open("rb") as stream:
            while chunk:=stream.read(1024*1024):
                reply=client.put(base+"/"+key+"?offset="+str(offset),content=chunk);assert reply.status_code==200;offset=reply.json()["offset"]
        client.headers["Authorization"]="Bearer synthetic-package-other"
        assert client.post(base+"/"+key+"/complete").status_code==400
        client.headers["Authorization"]="Bearer synthetic-package-owner"
        deadline=time.monotonic()+10
        while True:
            reply=client.post(base+"/"+key+"/complete");assert reply.status_code==200,reply.text
            if not reply.json().get("pending"):break
            assert time.monotonic()<deadline;time.sleep(.01)
        assert reply.json()["sha256"]==checksum
        assert client.get("/api/game/packages").json()["games"][0]["id"]=="synthetic-package"
        assert client.get("/api/game/packages/synthetic-package/download").content==file.read_bytes()
