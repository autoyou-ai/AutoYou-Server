# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-746f20706179203130252061-b081080e73867413a012cb4d

"""aiortc keeps one TURN URI; make sure it is one the network can reach."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-746f20706179203130252061-b081080e73867413a012cb4d"


import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

from aiortc import RTCConfiguration, RTCIceServer  # noqa: E402
from aiortc.rtcicetransport import connection_kwargs  # noqa: E402

from shared import aiortc_turn  # noqa: E402

ISSUED = [
    {"urls": ["stun:turn.example.test:3478"]},
    {
        "urls": [
            "turn:turn.example.test:3478?transport=udp",
            "turn:turn.example.test:3478?transport=tcp",
            "turns:turn.example.test:443?transport=tcp",
        ],
        "username": "user",
        "credential": "secret",
    },
]


def _aiortc_pick(servers):
    ice = [RTCIceServer(urls=s["urls"], username=s.get("username"), credential=s.get("credential")) for s in servers]
    return connection_kwargs(RTCConfiguration(iceServers=ice).iceServers)


@pytest.fixture(autouse=True)
def _fresh_cache():
    aiortc_turn.clear_turn_udp_verdicts()
    yield
    aiortc_turn.clear_turn_udp_verdicts()


def test_aiortc_itself_only_keeps_the_first_turn_uri():
    kwargs = _aiortc_pick(ISSUED)
    assert kwargs["turn_transport"] == "udp"
    assert kwargs["turn_server"] == ("turn.example.test", 3478)


def test_listed_order_is_kept_when_udp_is_not_known_to_be_blocked():
    ordered = aiortc_turn.order_ice_servers_for_aiortc(ISSUED)
    kwargs = _aiortc_pick(ordered)
    assert kwargs["stun_server"] == ("turn.example.test", 3478)
    assert kwargs["turn_transport"] == "udp"
    assert kwargs["turn_username"] == "user"
    assert kwargs["turn_password"] == "secret"


async def test_blocked_udp_makes_aiortc_pick_tls_on_443(monkeypatch):
    async def _blocked(target, timeout):
        return False

    monkeypatch.setattr(aiortc_turn, "_probe_udp", _blocked)
    await aiortc_turn.prime_turn_udp_probe(ISSUED)
    kwargs = _aiortc_pick(aiortc_turn.order_ice_servers_for_aiortc(ISSUED))
    assert kwargs["turn_server"] == ("turn.example.test", 443)
    assert kwargs["turn_ssl"] is True
    assert kwargs["turn_transport"] == "tcp"
    assert kwargs["turn_password"] == "secret"


async def test_reachable_udp_keeps_the_udp_relay(monkeypatch):
    async def _reachable(target, timeout):
        return True

    monkeypatch.setattr(aiortc_turn, "_probe_udp", _reachable)
    await aiortc_turn.prime_turn_udp_probe(ISSUED)
    assert _aiortc_pick(aiortc_turn.order_ice_servers_for_aiortc(ISSUED))["turn_transport"] == "udp"


async def test_probe_is_skipped_without_a_tcp_alternative(monkeypatch):
    calls = []

    async def _record(target, timeout):
        calls.append(target)
        return False

    monkeypatch.setattr(aiortc_turn, "_probe_udp", _record)
    udp_only = [{"urls": ["turn:relay.example.test:3478"], "username": "u", "credential": "p"}]
    await aiortc_turn.prime_turn_udp_probe(udp_only)
    assert calls == []
    assert _aiortc_pick(aiortc_turn.order_ice_servers_for_aiortc(udp_only))["turn_transport"] == "udp"


async def test_verdict_expires(monkeypatch):
    async def _blocked(target, timeout):
        return False

    monkeypatch.setattr(aiortc_turn, "_probe_udp", _blocked)
    await aiortc_turn.prime_turn_udp_probe(ISSUED)
    later = aiortc_turn.time.monotonic() + aiortc_turn.VERDICT_TTL_SECONDS + 1
    ordered = aiortc_turn.order_ice_servers_for_aiortc(ISSUED, now=later)
    assert _aiortc_pick(ordered)["turn_transport"] == "udp"


async def test_one_lost_datagram_does_not_mark_udp_blocked(monkeypatch):
    """Packet loss is normal on UDP; only a run of silent attempts counts as blocked."""
    replies = [None, ("203.0.113.9", 1234)]

    async def _flaky(address, bind, timeout):
        return replies.pop(0)

    monkeypatch.setattr(aiortc_turn, "_stun_one_request", _flaky, raising=False)
    import shared.stun_classifier as sc

    monkeypatch.setattr(sc, "_stun_one_request", _flaky)
    assert await aiortc_turn._probe_udp(("127.0.0.1", 3478), 0.2) is True
    assert replies == []


async def test_a_blocked_host_does_not_reorder_a_healthy_host(monkeypatch):
    servers = [
        {"urls": ["turn:dead.example.test:3478?transport=udp", "turns:dead.example.test:443?transport=tcp"],
         "username": "u", "credential": "p"},
        {"urls": ["turn:live.example.test:3478?transport=udp", "turns:live.example.test:443?transport=tcp"],
         "username": "u2", "credential": "p2"},
    ]

    async def _only_dead_is_blocked(target, timeout):
        return target[0] != "dead.example.test"

    monkeypatch.setattr(aiortc_turn, "_probe_udp", _only_dead_is_blocked)
    await aiortc_turn.prime_turn_udp_probe(servers)
    kwargs = _aiortc_pick(aiortc_turn.order_ice_servers_for_aiortc(servers))
    assert kwargs["turn_server"] == ("live.example.test", 3478)
    assert kwargs["turn_transport"] == "udp"

    async def _both_blocked(target, timeout):
        return False

    aiortc_turn.clear_turn_udp_verdicts()
    monkeypatch.setattr(aiortc_turn, "_probe_udp", _both_blocked)
    await aiortc_turn.prime_turn_udp_probe(servers)
    kwargs = _aiortc_pick(aiortc_turn.order_ice_servers_for_aiortc(servers))
    assert kwargs["turn_server"] == ("dead.example.test", 443)
    assert kwargs["turn_ssl"] is True


async def test_probe_uses_a_real_stun_exchange():
    import asyncio
    import socket
    import struct

    responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    responder.bind(("127.0.0.1", 0))
    responder.setblocking(False)
    port = responder.getsockname()[1]
    loop = asyncio.get_running_loop()

    async def _answer_once():
        data, addr = await loop.sock_recvfrom(responder, 1024)
        cookie, txid = data[4:8], data[8:20]
        xport = addr[1] ^ 0x2112
        xaddr = struct.unpack("!I", socket.inet_aton(addr[0]))[0] ^ 0x2112A442
        attr = struct.pack("!HHBBHI", 0x0020, 8, 0, 0x01, xport, xaddr)
        await loop.sock_sendto(responder, struct.pack("!HH", 0x0101, len(attr)) + cookie + txid + attr, addr)

    try:
        server = asyncio.ensure_future(_answer_once())
        assert await aiortc_turn._probe_udp(("127.0.0.1", port), 1.0) is True
        await server
        silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        silent.bind(("127.0.0.1", 0))
        try:
            assert await aiortc_turn._probe_udp(("127.0.0.1", silent.getsockname()[1]), 0.3) is False
        finally:
            silent.close()
    finally:
        responder.close()


def test_string_and_url_entries_are_accepted():
    ordered = aiortc_turn.order_ice_servers_for_aiortc(
        ["stun:stun.example.test:19302", {"url": "turn:relay.example.test:80", "username": "u", "credential": "p"}]
    )
    assert ordered[0]["urls"] == ["stun:stun.example.test:19302"]
    assert ordered[1]["urls"] == ["turn:relay.example.test:80"]
    assert ordered[1]["credential"] == "p"


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("turn:h:3478", ("turn", "h", 3478, "udp")),
        ("turns:h", ("turns", "h", 5349, "tcp")),
        ("turn:[2001:db8::1]:3478?transport=tcp", ("turn", "2001:db8::1", 3478, "tcp")),
        ("stun:stun.l.google.com:19302", ("stun", "stun.l.google.com", 19302, "udp")),
    ],
)
def test_parse_turn_uri(uri, expected):
    parsed = aiortc_turn.parse_turn_uri(uri)
    assert (parsed["scheme"], parsed["host"], parsed["port"], parsed["transport"]) == expected
