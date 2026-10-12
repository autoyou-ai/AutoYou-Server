"""The downloadable engine adapter keeps each player's held state separate."""

import importlib.util
from pathlib import Path


def test_engine_adapter_releases_all_players_after_backlog_reset():
    path = (Path(__file__).resolve().parents[3] / "autoyou_agents") / "game_agent/website/frontend/assets/game_input_client.py"
    spec = importlib.util.spec_from_file_location("game_input_client_example", path)
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    updates = []
    client.apply_to_game = lambda session_id, state, frame: updates.append((session_id, frame["input_type"]))
    for session_id in ("synthetic-player-one", "synthetic-player-two"):
        client.handle_frame({"event": "game_input", "session_id": session_id,
                             "input_type": "button", "button": "action_a", "phase": "down"})
    assert all("action_a" in state["buttons"] for state in client.players.values())
    client.handle_frame({"event": "game_input", "input_type": "state_reset",
                         "session_id": "*", "all_sessions": True})
    assert client.players == {}
    assert updates[-2:] == [
        ("synthetic-player-one", "session_end"),
        ("synthetic-player-two", "session_end"),
    ]
