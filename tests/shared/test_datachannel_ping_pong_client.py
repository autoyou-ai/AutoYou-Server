"""Client-role keepalive RTT + ping-pong game contract.

Every AutoYou client (Python GUI/TUI/CLI via DataChannelManager, Android,
iOS, Chrome) must measure its own PING->PONG round trip on a single monotonic
clock and report it as ``rtt_ms`` on the next PING.  The server scores the
ping-pong rally with that number; it must never derive RTT by subtracting the
client's wall-clock ``header.timestamp`` from its own ``time.time()``, which
yields one-way delay plus NTP skew.
"""

import asyncio
import json
import time

import pytest

from shared.datachannel_manager import (
    DataChannelManager,
    DataChannelMessage,
    MessageHeader,
    MessageType,
)


class _FakeChannel:
    """Minimal datachannel that records everything the manager sends."""

    readyState = "open"

    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(json.loads(data) if isinstance(data, str) else data)

    def sent_of_type(self, message_type):
        return [m for m in self.sent if m.get("header", {}).get("message_type") == message_type]


def _client_manager():
    manager = DataChannelManager(role="client")
    channel = _FakeChannel()
    manager.set_datachannel(channel)
    manager.set_session_id("session-under-test")
    return manager, channel


def _pong_for(ping_message: dict, game: dict | None = None) -> str:
    payload = {"ping_id": ping_message["header"]["message_id"]}
    if game is not None:
        payload["game"] = game
    return DataChannelMessage(
        header=MessageHeader(
            message_id="pong-1",
            message_type=MessageType.PONG,
            timestamp=time.time(),
            session_id="session-under-test",
            user_id="server",
        ),
        payload=payload,
    ).to_json()


@pytest.mark.asyncio
async def test_first_ping_has_no_rtt_and_carries_ping_id():
    manager, channel = _client_manager()
    assert await manager.send_ping()

    pings = channel.sent_of_type("ping")
    assert len(pings) == 1
    payload = pings[0]["payload"]
    assert payload["ping_id"] == pings[0]["header"]["message_id"]
    # Nothing measured yet, so nothing to report.
    assert "rtt_ms" not in payload


@pytest.mark.asyncio
async def test_client_receives_server_location_policy_in_pong():
    manager, channel = _client_manager()
    policies = []

    async def observe(message):
        policies.append(message.payload["location_recording_enabled"])

    manager.register_handler(MessageType.PONG, observe)
    await manager.send_ping()
    pong = json.loads(_pong_for(channel.sent_of_type("ping")[0]))
    pong["payload"]["location_recording_enabled"] = False
    await manager.handle_received_message(json.dumps(pong))
    assert policies == [False]


@pytest.mark.asyncio
async def test_second_ping_reports_measured_round_trip():
    manager, channel = _client_manager()
    await manager.send_ping()
    first_ping = channel.sent_of_type("ping")[0]

    await asyncio.sleep(0.02)
    await manager.handle_received_message(_pong_for(first_ping))

    assert manager._last_measured_rtt_ms is not None
    assert manager._last_measured_rtt_ms >= 15

    await manager.send_ping()
    second_ping = channel.sent_of_type("ping")[1]
    assert second_ping["payload"]["rtt_ms"] == manager._last_measured_rtt_ms


@pytest.mark.asyncio
async def test_rtt_is_not_derived_from_peer_wall_clock():
    """A peer clock skewed by hours must not corrupt our measurement."""
    manager, channel = _client_manager()
    await manager.send_ping()
    ping = channel.sent_of_type("ping")[0]

    skewed = json.loads(_pong_for(ping))
    skewed["header"]["timestamp"] = time.time() - 7200  # peer clock 2h behind
    await manager.handle_received_message(json.dumps(skewed))

    # Measured locally, so the bogus peer timestamp is irrelevant.
    assert 0 <= manager._last_measured_rtt_ms < 5000


@pytest.mark.asyncio
async def test_unmatched_pong_does_not_produce_a_sample():
    manager, _ = _client_manager()
    stray = DataChannelMessage(
        header=MessageHeader(
            message_id="pong-x",
            message_type=MessageType.PONG,
            timestamp=time.time(),
            session_id="session-under-test",
            user_id="server",
        ),
        payload={"ping_id": "never-sent"},
    ).to_json()
    await manager.handle_received_message(stray)
    assert manager._last_measured_rtt_ms is None


@pytest.mark.asyncio
async def test_in_flight_ping_tracking_is_bounded():
    manager, _ = _client_manager()
    for _ in range(200):
        await manager.send_ping()
    assert len(manager._ping_sent_at_monotonic) <= 32


@pytest.mark.asyncio
async def test_disconnect_clears_in_flight_pings():
    manager, _ = _client_manager()
    await manager.send_ping()
    assert manager._ping_sent_at_monotonic
    manager.disconnect()
    assert not manager._ping_sent_at_monotonic


@pytest.mark.asyncio
async def test_game_state_reaches_the_ui_callback():
    manager, channel = _client_manager()
    events = []
    manager.on_ping_pong_event = lambda key, game: events.append((key, game))

    await manager.send_ping()
    ping = channel.sent_of_type("ping")[0]
    game = {"t": "🔥 Backhand winner!", "p": "c", "mc": 2, "ms": 1, "gc": 0, "gs": 0, "r": 31, "v": 7}
    await manager.handle_received_message(_pong_for(ping, game))

    assert ("keepalive_ping_sent", None) in events
    assert ("pong_received", game) in events


@pytest.mark.asyncio
async def test_server_initiated_ping_is_answered_and_surfaces_game():
    manager, channel = _client_manager()
    events = []
    manager.on_ping_pong_event = lambda key, game: events.append((key, game))

    game = {"t": "🏓 Top spin shot!", "p": None, "mc": 0, "ms": 0, "gc": 0, "gs": 0, "r": 12, "v": 3}
    server_ping = DataChannelMessage(
        header=MessageHeader(
            message_id="server-ping-1",
            message_type=MessageType.PING,
            timestamp=time.time(),
            session_id="session-under-test",
            user_id="server",
        ),
        payload={"keepalive": True, "game": game},
    ).to_json()
    await manager.handle_received_message(server_ping)

    pongs = channel.sent_of_type("pong")
    assert len(pongs) == 1
    assert pongs[0]["payload"]["ping_id"] == "server-ping-1"
    assert ("ping_received", game) in events
    assert ("pong_sent", None) in events


@pytest.mark.asyncio
async def test_server_role_does_not_report_client_rtt():
    """Only clients self-measure; the server embeds game state instead."""
    manager = DataChannelManager(role="server")
    channel = _FakeChannel()
    manager.set_datachannel(channel)
    manager.set_session_id("server-session")

    await manager.send_ping()
    payload = channel.sent_of_type("ping")[0]["payload"]
    assert "rtt_ms" not in payload
