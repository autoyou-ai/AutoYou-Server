# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.

"""Any library game plays from the same host page, and every player's input reads as words.

Play game opens a game from the computer's library (Neon Horizon unless another is chosen). The
game runs in a sandboxed frame with no network; its host page passes players' frames in. Each
input a player makes is recorded for Interact in words beside its machine-readable parts, and
any number of programs may observe the same feed without taking input from the game.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import server
from shared import game_catalog
from shared.game_input import GameInputHub, describe_game_input

LOCAL = "http://127.0.0.1:8001"


def test_inputs_read_as_words_once_per_press_and_never_quote_typed_text():
    memory = {}
    button = {"event": "game_input", "input_type": "button", "button": "action_a", "phase": "down"}
    assert describe_game_input(button, memory)["value"] == "A"
    assert describe_game_input({**button, "phase": "up"}, memory) is None
    # A configured layout names its own buttons.
    assert describe_game_input({**button, "button": "boost"}, memory, {"boost": "Boost"})["value"] == "Boost"
    stick = {"event": "game_input", "input_type": "axis", "axis": "left_x", "value": -0.9}
    first = describe_game_input(stick, memory)
    assert first["value"] == "← Left stick" and first["direction"] == "left" and first["device"] == "controller"
    # Holding the stick in the same direction is not another input; returning to the middle is not one either.
    assert describe_game_input({**stick, "value": -0.7}, memory) is None
    assert describe_game_input({**stick, "value": 0.1}, memory) is None
    assert describe_game_input({**stick, "value": 0.9}, memory)["value"] == "→ Left stick"
    touch = {"event": "game_input", "input_type": "touch", "points": [{"id": 1, "x": 0.2, "y": 0.4}]}
    assert describe_game_input(touch, memory)["value"] == "Tap"
    assert describe_game_input(touch, memory) is None
    key = {"event": "remote_desktop_keyboard", "action": "key", "key": "arrowleft", "phase": "down"}
    assert describe_game_input(key, memory)["value"] == "← Left"
    typed = describe_game_input({"event": "remote_desktop_keyboard", "action": "input", "text": "synthetic secret"})
    assert typed["value"] == "Typed text" and "synthetic" not in str(typed)
    click = {"event": "remote_desktop_input", "input_type": "button", "button": "left", "phase": "down", "x": 0.5, "y": 0.5}
    assert describe_game_input(click, memory)["value"] == "Left click"
    assert describe_game_input({"event": "game_input", "input_type": "heartbeat"}, memory) is None


def test_any_number_of_observers_read_every_input_without_taking_it_from_the_engine():
    hub = GameInputHub()
    engine = hub.attach()
    readers = [hub.observe(maxsize=2) for _ in range(3)]
    assert hub.observer_count == 3 and hub.connected
    for value in ("A", "B", "X"):
        hub.notify({"event": "game_input_observed", "input": {"value": value}})
    for reader in readers:
        # A slow reader keeps the newest entries.
        assert [reader.get_nowait()["input"]["value"] for _ in range(2)] == ["B", "X"]
    assert engine.empty()
    hub.unobserve(readers[0])
    assert hub.observer_count == 2
    assert all(hub.observe() is not None for _ in range(14))
    assert hub.observe() is None, "the observer cap protects the computer"


@pytest.mark.asyncio
async def test_game_inputs_reach_interact_in_words_and_players_are_listed(monkeypatch):
    webrtc = server.WebRTCManager()
    reader = webrtc.game_input_hub.observe()
    lease = {"mode": "game", "control_id": "synthetic-control"}
    webrtc.remote_desktop_control_leases_by_session["synthetic-session"] = lease
    webrtc.voice_call_client_active_by_session["synthetic-session"] = True
    monkeypatch.setattr(webrtc, "client_display_name_snapshot",
                        lambda session_id: {"client_display_name": "Synthetic Phone"})
    frame = {"event": "game_input", "input_type": "button", "button": "action_a", "phase": "down",
             "control_id": "synthetic-control"}
    assert await webrtc._forward_game_engine_input_locked("synthetic-session", lease, frame) is True
    entry = list(webrtc.screen_inputs)[-1]
    assert entry["name"] == "Synthetic Phone" and entry["kind"] == "game"
    assert entry["value"] == "A" and entry["control"] == "action_a" and entry["device"] == "controller"
    observed = reader.get_nowait()
    assert observed["event"] == "game_input_observed" and observed["player"] == "Synthetic Phone"
    assert observed["input"]["value"] == "A" and "control_id" not in observed["frame"]
    snapshot = webrtc.screen_listen_snapshot()
    player = next(person for person in snapshot["participants"] if person["mode"] == "game")
    assert player["name"] == "Synthetic Phone" and player["last_input"] == "A"
    assert snapshot["inputs"][-1]["value"] == "A"


def test_observe_feed_needs_a_signed_in_session_or_the_local_engine_token(monkeypatch):
    monkeypatch.setattr(server, "_get_game_mode_available", lambda cfg=None: True)
    with TestClient(server.admin_app, base_url=LOCAL) as client:
        try:
            with client.websocket_connect("/api/webrtc/game-input/observe"):
                raise AssertionError("An unauthenticated reader observed game input")
        except WebSocketDisconnect as exc:
            assert exc.code == 1008
        try:
            with client.websocket_connect("/api/webrtc/game-input/observe",
                                          headers={"Authorization": "Bearer synthetic-wrong-token"}):
                raise AssertionError("A wrong token observed game input")
        except WebSocketDisconnect as exc:
            assert exc.code == 1008
        hub = server.WEBRTC.game_input_hub
        with client.websocket_connect("/api/webrtc/game-input/observe",
                                      headers={"Authorization": f"Bearer {hub.token}"}) as feed:
            assert feed.receive_json()["event"] == "observe_ready"
            client.portal.call(hub.notify, {"event": "game_input_observed", "input": {"value": "Space"}})
            assert feed.receive_json()["input"]["value"] == "Space"
            feed.close()
        assert hub.observer_count == 0


def test_library_games_play_from_one_host_page_in_a_sandboxed_frame(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_get_game_mode_available", lambda cfg=None: True)
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    published = server.get_mutable_data_dir("AutoYou", anchor=server.__file__) / "mobile_games"
    published.mkdir(parents=True, exist_ok=True)
    (published / "synthetic-runner.html").write_text(
        "<html><head><title>runner</title></head><body>synthetic runner</body></html>", encoding="utf-8")
    with TestClient(server.admin_app, base_url=LOCAL) as client:
        default = client.get("/api/webrtc/hosted-game/play")
        assert default.status_code == 200 and "Neon Horizon" in default.text
        assert 'sandbox="allow-scripts allow-pointer-lock"' in default.text and "./frame/neon-horizon" in default.text
        chosen = client.get("/api/webrtc/hosted-game/play", params={"game": "synthetic-runner"})
        assert chosen.status_code == 200 and "./frame/synthetic-runner" in chosen.text
        assert client.get("/api/webrtc/hosted-game/play", params={"game": "synthetic-missing"}).status_code == 404
        assert client.get("/api/webrtc/hosted-game/play", params={"game": "../secret"}).status_code == 404
        frame = client.get("/api/webrtc/hosted-game/frame/synthetic-runner")
        assert frame.status_code == 200 and "synthetic runner" in frame.text
        # Even opened on its own, the game stays in an opaque, sandboxed origin with no network.
        assert frame.headers["content-security-policy"].startswith("sandbox allow-scripts")
        assert "frame-ancestors 'self'" in frame.headers["content-security-policy"]
        assert "connect-src 'none'" in frame.text and "autoyouGameInput" in frame.text
        assert frame.text.index("autoyouGameInput") < frame.text.index("synthetic runner")
        # Neon Horizon receives frames through its own protocol hook.
        neon = client.get("/api/webrtc/hosted-game/frame/neon-horizon")
        assert neon.status_code == 200 and "window.autoYouGameInput = handleRemoteFrame" in neon.text
        assert client.get("/api/webrtc/hosted-game/frame/synthetic-runner", headers={"Host": "example.test"}).status_code == 403
        assert client.get("/api/webrtc/hosted-game/frame/synthetic-missing").status_code == 404


def test_the_library_lists_the_default_game_first_and_published_games_replace_built_in_ones(tmp_path):
    built_in, published = tmp_path / "built-in", tmp_path / "published"
    built_in.mkdir(), published.mkdir()
    (built_in / "neon-horizon.html").write_text("<html><head></head><body>built in</body></html>", encoding="utf-8")
    (built_in / "zeta-run.html").write_text("<html><head></head><body>zeta</body></html>", encoding="utf-8")
    (published / "alpha-race.html").write_text("<html><head></head><body>alpha</body></html>", encoding="utf-8")
    (published / "zeta-run.html").write_text("<html><head></head><body>published zeta</body></html>", encoding="utf-8")
    (published / "Bad_Name.html").write_text("<html><head></head></html>", encoding="utf-8")
    (published / "headless.html").write_text("<html>no head</html>", encoding="utf-8")
    games = game_catalog.listing((built_in, published))
    assert [game["id"] for game in games] == ["neon-horizon", "alpha-race", "zeta-run"]
    assert games[0]["default"] is True and games[0]["title"] == "Neon Horizon"
    assert game_catalog.catalog((built_in, published))["zeta-run"].parent == published
