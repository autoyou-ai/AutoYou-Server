# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""
aiortc_turn - make aiortc's single TURN choice a usable one.

aiortc (1.14.0) builds its ICE transport from ``connection_kwargs()``, which
keeps the FIRST STUN URI and the FIRST usable ``turn:``/``turns:`` URI across
every RTCIceServer and silently drops the rest. Browsers and libwebrtc try all
of them. Every TURN issuer AutoYou talks to (autoyou-core cred-issuer, the
provider prober, Twilio, Metered) lists UDP first, so a Python peer on a
network that blocks outbound UDP only ever tries the UDP relay and never the
TCP/TLS one that would have worked.

``prime_turn_udp_probe()`` sends one STUN binding request to each UDP TURN
host (TURN servers answer STUN) and caches whether UDP got through.
``order_ice_servers_for_aiortc()`` then returns the same helpers with the TURN
URIs ordered so aiortc's pick is reachable: the listed order when UDP works or
has not been probed, TLS/TCP (port 443 first) when UDP is known to be blocked.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs

LOGGER = logging.getLogger("autoyou.aiortc_turn")

PROBE_TIMEOUT_SECONDS = 1.0
PROBE_ATTEMPTS = 3
VERDICT_TTL_SECONDS = 600.0

_verdicts: Dict[Tuple[str, int], Tuple[bool, float]] = {}
_logged_choices: set = set()


def parse_turn_uri(uri: str) -> Optional[Dict[str, Any]]:
    """Parse a stun/turn/turns URI into scheme, host, port and transport.

    Mirrors aiortc's defaults: turn is udp on 3478, turns is tcp on 5349.
    Returns None for anything aiortc would not parse.
    """
    if not isinstance(uri, str) or ":" not in uri:
        return None
    scheme, _, rest = uri.strip().partition(":")
    scheme = scheme.lower()
    if scheme not in ("stun", "stuns", "turn", "turns"):
        return None
    rest, _, query = rest.partition("?")
    if rest.startswith("["):
        host, _, tail = rest[1:].partition("]")
        port_text = tail[1:] if tail.startswith(":") else ""
    else:
        host, _, port_text = rest.rpartition(":") if rest.count(":") == 1 else (rest, "", "")
    if not host:
        return None
    default_port = 5349 if scheme in ("stuns", "turns") else 3478
    try:
        port = int(port_text) if port_text else default_port
    except ValueError:
        return None
    transports = parse_qs(query).get("transport") or []
    transport = transports[0].lower() if transports else ("tcp" if scheme == "turns" else "udp")
    return {"scheme": scheme, "host": host, "port": port, "transport": transport}


def _usable_by_aiortc(parsed: Dict[str, Any]) -> bool:
    if parsed["scheme"] == "turn":
        return parsed["transport"] in ("udp", "tcp")
    if parsed["scheme"] == "turns":
        return parsed["transport"] == "tcp"
    return False


def _normalise(servers: Iterable[Any]) -> List[Dict[str, Any]]:
    """Accept dict entries (urls or url) and bare URI strings, like the engine does."""
    out: List[Dict[str, Any]] = []
    for server in servers or []:
        if isinstance(server, str):
            if server.strip():
                out.append({"urls": [server.strip()]})
            continue
        if not isinstance(server, dict):
            continue
        urls = server.get("urls") or server.get("url")
        if isinstance(urls, str):
            urls = [urls]
        if not urls:
            continue
        entry = dict(server)
        entry.pop("url", None)
        entry["urls"] = [u for u in urls if isinstance(u, str) and u.strip()]
        if entry["urls"]:
            out.append(entry)
    return out


def udp_turn_targets(servers: Iterable[Any]) -> List[Tuple[str, int]]:
    """UDP TURN endpoints worth probing: only when a TCP/TLS alternative exists."""
    udp: List[Tuple[str, int]] = []
    has_stream_alternative = False
    for server in _normalise(servers):
        for uri in server["urls"]:
            parsed = parse_turn_uri(uri)
            if not parsed or not _usable_by_aiortc(parsed):
                continue
            if parsed["transport"] == "udp":
                target = (parsed["host"], parsed["port"])
                if target not in udp:
                    udp.append(target)
            else:
                has_stream_alternative = True
    return udp if has_stream_alternative else []


def _verdict(target: Tuple[str, int], now: float) -> Optional[bool]:
    cached = _verdicts.get(target)
    if cached is None or now - cached[1] > VERDICT_TTL_SECONDS:
        return None
    return cached[0]


def order_ice_servers_for_aiortc(servers: Iterable[Any], *, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Return ICE helpers ordered so aiortc's single TURN pick is reachable.

    STUN entries keep their order and come first. Each TURN URI becomes its
    own entry (aiortc reads one anyway) carrying its server's credentials.
    Without a cached "UDP blocked" verdict the listed order is kept.
    """
    now = time.monotonic() if now is None else now
    stun_entries: List[Dict[str, Any]] = []
    turn_entries: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    for server in _normalise(servers):
        stun_urls: List[str] = []
        for uri in server["urls"]:
            parsed = parse_turn_uri(uri)
            if not parsed or parsed["scheme"] in ("stun", "stuns"):
                stun_urls.append(uri)
                continue
            entry = {k: v for k, v in server.items() if k != "urls"}
            entry["urls"] = [uri]
            turn_entries.append((len(turn_entries), entry, parsed))
        if stun_urls:
            stun_entries.append({**{k: v for k, v in server.items() if k != "urls"}, "urls": stun_urls})

    # Per host: one unreachable relay must not reorder a host whose UDP works.
    blocked_hosts = {
        parsed["host"]
        for _, _, parsed in turn_entries
        if parsed["transport"] == "udp" and _verdict((parsed["host"], parsed["port"]), now) is False
    }
    blocked = bool(blocked_hosts)
    ranked = []
    for index, entry, parsed in turn_entries:
        if parsed["host"] in blocked_hosts:
            # Hosts we could not reach over UDP: keep them, but behind any host
            # we did not find blocked, and prefer TLS on 443 among their URIs.
            key = (
                1,
                0 if parsed["transport"] == "tcp" else 1,
                0 if parsed["port"] == 443 else 1,
                0 if parsed["scheme"] == "turns" else 1,
                index,
            )
        else:
            key = (0, 0, 0, 0, index)
        ranked.append((key, entry, parsed))
    ranked.sort(key=lambda item: item[0])

    usable = [parsed for _, _, parsed in ranked if _usable_by_aiortc(parsed)]
    if len(usable) > 1:
        chosen = usable[0]
        label = f"{chosen['scheme']}:{chosen['host']}:{chosen['port']}?transport={chosen['transport']}"
        if (label, blocked) not in _logged_choices:
            _logged_choices.add((label, blocked))
            LOGGER.info(
                "aiortc uses one TURN server: selected %s (UDP to TURN %s); %d other TURN URI(s) unused",
                label,
                "blocked" if blocked else "not known to be blocked",
                len(usable) - 1,
            )
    return stun_entries + [entry for _, entry, _ in ranked]


async def _probe_udp(target: Tuple[str, int], timeout: float) -> bool:
    """True unless a STUN binding request to a resolved IPv4 TURN address times out."""
    try:
        from shared.stun_classifier import _stun_one_request
    except Exception:
        return True
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(target[0], target[1], family=socket.AF_INET, type=socket.SOCK_DGRAM),
            timeout=timeout,
        )
    except Exception:
        return True  # DNS trouble says nothing about UDP; keep the listed order.
    if not infos:
        return True
    address = infos[0][4][:2]
    # One lost datagram is normal on UDP; only a run of silent attempts means blocked.
    for _ in range(max(1, PROBE_ATTEMPTS)):
        try:
            mapped = await _stun_one_request(address, ("0.0.0.0", 0), timeout=timeout)
        except Exception:
            return True
        if mapped is not None:
            return True
    return False


async def prime_turn_udp_probe(
    servers: Iterable[Any],
    *,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    now: Optional[float] = None,
) -> None:
    """Probe UDP reachability of TURN hosts that have a TCP/TLS alternative.

    Cached for VERDICT_TTL_SECONDS. Never raises; a failed import or socket
    error counts as reachable so behaviour stays as before.
    """
    now = time.monotonic() if now is None else now
    targets = [t for t in udp_turn_targets(servers) if _verdict(t, now) is None]
    if not targets:
        return
    try:
        results = await asyncio.gather(*(_probe_udp(t, timeout) for t in targets))
    except Exception:
        return
    stamp = time.monotonic()
    for target, reachable in zip(targets, results):
        _verdicts[target] = (bool(reachable), stamp)
        if not reachable:
            LOGGER.info("UDP to TURN %s:%s timed out; preferring TCP/TLS relays for aiortc", target[0], target[1])


def clear_turn_udp_verdicts() -> None:
    """Forget cached probe results (tests, network changes)."""
    _verdicts.clear()
    _logged_choices.clear()
