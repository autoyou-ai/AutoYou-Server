"""Example local consumer for autoyou_game_v1 input (websockets >= 15)."""

import asyncio
import getpass
import json
import os

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus


players = {}


def apply_to_game(session_id, state, frame):
    """Replace this callback with your engine's player input API."""
    print(session_id, frame.get("event"), frame.get("input_type") or frame.get("action"))


def release_players(session_id=None):
    targets = list(players) if session_id is None else [session_id]
    for target in targets:
        state = players.pop(target, None)
        if state is not None:
            apply_to_game(target, {"touches": [], "axes": {}, "buttons": set(), "keys": set()},
                          {"event": "game_input", "input_type": "session_end"})


def handle_frame(frame):
    """Keep held controls per player; pass validated frames into an engine."""
    session_id = frame.get("session_id")
    kind = frame.get("input_type")
    if kind == "state_reset" and frame.get("all_sessions") is True:
        release_players()
        return
    if kind == "session_end":
        release_players(session_id)
        return
    if not session_id or frame.get("event") == "heartbeat":
        return
    state = players.setdefault(session_id, {"touches": [], "axes": {}, "buttons": set(), "keys": set()})
    if frame.get("event") == "game_input":
        if kind == "touch":
            state["touches"] = frame["points"]
        elif kind == "axis":
            state["axes"][frame["axis"]] = frame["value"]
        elif kind == "button":
            (state["buttons"].add if frame["phase"] == "down" else state["buttons"].discard)(frame["button"])
    elif frame.get("event") == "remote_desktop_keyboard":
        if frame.get("action") == "hide":
            state["keys"].clear()
        elif frame.get("action") == "key":
            (state["keys"].add if frame["phase"] == "down" else state["keys"].discard)(frame["key"])
    apply_to_game(session_id, state, frame)


async def main():
    token = os.environ.get("AUTOYOU_GAME_STREAM_TOKEN") or getpass.getpass("Game stream token: ")
    if not token:
        raise SystemExit("A stream token is required")
    url = os.environ.get("AUTOYOU_GAME_STREAM_URL", "ws://127.0.0.1:8001/api/webrtc/game-input/stream")
    delay = 0.5
    while True:
        try:
            async with connect(url, additional_headers={"Authorization": f"Bearer {token}"}) as stream:
                delay = 0.5
                async for message in stream:
                    frame = json.loads(message)
                    if isinstance(frame, dict):
                        handle_frame(frame)
        except (OSError, ValueError, ConnectionError, ConnectionClosed, InvalidStatus) as exc:
            print(f"Game stream interrupted ({type(exc).__name__}); reconnecting")
        release_players()
        await asyncio.sleep(delay)
        delay = min(delay * 2, 5)


if __name__ == "__main__":
    asyncio.run(main())
