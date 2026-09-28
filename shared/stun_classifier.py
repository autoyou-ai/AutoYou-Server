# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-d2fe757aa18772bc56e99c2f

"""
stun_classifier - minimal NAT-class detection via two STUN binding requests
to two different servers, classifying the result into the 5 buckets the
device_diagnostics collection accepts.

Reference: RFC 5780 §4 NAT Behavior Discovery (we implement only the
"mapping behavior" portion - the part that determines whether we'll need
TURN). RFC 3489's full classification taxonomy ("Cone NAT" et al.) is no
longer guaranteed accurate on real-world double-NAT setups, but the
reduced 5-class space here is enough for the Phase 3 use case: deciding
whether a user benefits from the TURN tier.

Classes returned:
  full-cone           One mapping per local socket; remote endpoints don't
                      need to be on the original 5-tuple.
  address-restricted  Mapping reused per local socket, but only peers that
                      we've spoken to first can reach us.
  port-restricted     Like address-restricted but port-pinned.
  symmetric           Different external port per remote target - TURN
                      almost always needed for direct WebRTC.
  unknown             STUN servers unreachable, ambiguous, or socket
                      errors. Treated as "probably needs TURN" by callers.

The implementation here uses the `aiortc` STUN agent stack when
available (because both server.py and autoyou_lite already import aiortc),
and falls back to a plain UDP STUN binding request when not.

Network privacy: this module never persists or logs the user's external
IP. It only returns the *classification*. Callers should pass the result
to the diagnostics heartbeat without ever serialising the raw IP.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-d2fe757aa18772bc56e99c2f"


import asyncio
import logging
import os
import secrets
import socket
import struct
from typing import Optional

LOGGER = logging.getLogger("autoyou.stun_classifier")


# STUN servers used for behaviour discovery. Two from different operators
# so we can compare the external mapping each one observes. We pick free,
# widely-deployed servers; environment can override.
DEFAULT_STUN_SERVERS = (
    ("stun.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
)


_STUN_MAGIC_COOKIE = 0x2112A442
_STUN_BINDING_REQUEST = 0x0001
_STUN_BINDING_RESPONSE = 0x0101
_STUN_ATTR_MAPPED_ADDRESS = 0x0001
_STUN_ATTR_XOR_MAPPED_ADDRESS = 0x0020
_STUN_FAMILY_IPV4 = 0x01
_STUN_FAMILY_IPV6 = 0x02


def _build_stun_request(transaction_id: bytes) -> bytes:
    """Encode an empty STUN binding request - no attributes, just the header."""
    return struct.pack(
        "!HHI12s",
        _STUN_BINDING_REQUEST,
        0,                     # length: zero attributes
        _STUN_MAGIC_COOKIE,
        transaction_id,
    )


def _parse_mapped_address(data: bytes, transaction_id: bytes) -> Optional[tuple[str, int]]:
    """Extract the external (IP, port) the STUN server observed for us.

    Returns None on any malformed response. We accept either MAPPED-ADDRESS
    or XOR-MAPPED-ADDRESS; modern servers prefer the latter.
    """
    if len(data) < 20:
        return None
    msg_type, msg_len, magic, txid = struct.unpack("!HHI12s", data[:20])
    if msg_type != _STUN_BINDING_RESPONSE:
        return None
    if magic != _STUN_MAGIC_COOKIE:
        return None
    if txid != transaction_id:
        return None
    body = data[20 : 20 + msg_len]
    pos = 0
    while pos + 4 <= len(body):
        attr_type, attr_len = struct.unpack("!HH", body[pos : pos + 4])
        pos += 4
        attr_value = body[pos : pos + attr_len]
        pos += attr_len + ((4 - (attr_len % 4)) % 4)  # padding to 4 bytes

        if attr_type == _STUN_ATTR_XOR_MAPPED_ADDRESS:
            if len(attr_value) < 8:
                continue
            family = attr_value[1]
            xor_port = struct.unpack("!H", attr_value[2:4])[0]
            port = xor_port ^ (_STUN_MAGIC_COOKIE >> 16)
            if family == _STUN_FAMILY_IPV4:
                if len(attr_value) < 8:
                    continue
                xor_addr = attr_value[4:8]
                magic_bytes = struct.pack("!I", _STUN_MAGIC_COOKIE)
                ip_bytes = bytes(b ^ m for b, m in zip(xor_addr, magic_bytes))
                ip = socket.inet_ntop(socket.AF_INET, ip_bytes)
                return ip, port
            if family == _STUN_FAMILY_IPV6:
                if len(attr_value) < 20:
                    continue
                xor_addr = attr_value[4:20]
                magic_bytes = struct.pack("!I", _STUN_MAGIC_COOKIE) + transaction_id
                ip_bytes = bytes(b ^ m for b, m in zip(xor_addr, magic_bytes))
                ip = socket.inet_ntop(socket.AF_INET6, ip_bytes)
                return ip, port
        elif attr_type == _STUN_ATTR_MAPPED_ADDRESS:
            if len(attr_value) < 8:
                continue
            family = attr_value[1]
            port = struct.unpack("!H", attr_value[2:4])[0]
            if family == _STUN_FAMILY_IPV4 and len(attr_value) >= 8:
                ip = socket.inet_ntop(socket.AF_INET, attr_value[4:8])
                return ip, port
            if family == _STUN_FAMILY_IPV6 and len(attr_value) >= 20:
                ip = socket.inet_ntop(socket.AF_INET6, attr_value[4:20])
                return ip, port
    return None


async def _stun_one_request(
    server: tuple[str, int],
    bind_addr: tuple[str, int],
    timeout: float = 2.0,
) -> Optional[tuple[str, int]]:
    """Send one STUN binding request; return external (IP, port) or None.

    Caller controls the local bind socket so two requests from the same
    socket can be compared - the symmetric-NAT detection relies on
    sharing a local source port across the two probes.
    """
    transaction_id = secrets.token_bytes(12)
    request = _build_stun_request(transaction_id)

    loop = asyncio.get_event_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        try:
            sock.bind(bind_addr)
        except OSError as exc:
            LOGGER.debug("stun_classifier: bind failed: %s", exc)
            return None
        try:
            await loop.sock_sendto(sock, request, server)
        except (OSError, socket.gaierror) as exc:
            LOGGER.debug("stun_classifier: sendto %s failed: %s", server, exc)
            return None
        try:
            data = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout=timeout)
        except (asyncio.TimeoutError, OSError):
            return None
        return _parse_mapped_address(data, transaction_id)
    finally:
        try:
            sock.close()
        except Exception:
            pass


async def classify_nat(
    *,
    servers: Optional[tuple[tuple[str, int], ...]] = None,
    timeout: float = 2.0,
) -> str:
    """Classify the NAT this host is behind into one of the 5 buckets.

    Sends two STUN binding requests from the same local socket to two
    different servers. Compares the external (IP, port) each saw:
      same IP, same port  → mapping is consistent → not symmetric
      same IP, diff port  → port-restricted (or stricter)
      diff IP            → symmetric (or worse)
      either probe fails  → unknown

    This is a coarse classification; for fine-grained discrimination
    between full-cone / address-restricted / port-restricted we'd need
    follow-up RFC 5780 tests. The Phase 3 use case (provisioning hint)
    only really cares about symmetric vs. not, so the reduced taxonomy
    is fine.
    """
    if servers is None:
        # Allow env override for self-hosted deployments / tests.
        env_servers = (os.environ.get("AUTOYOU_STUN_PROBE_SERVERS") or "").strip()
        if env_servers:
            parsed: list[tuple[str, int]] = []
            for entry in env_servers.split(","):
                host, _, port = entry.strip().partition(":")
                if host:
                    try:
                        parsed.append((host, int(port or 3478)))
                    except ValueError:
                        continue
            servers = tuple(parsed) if parsed else DEFAULT_STUN_SERVERS
        else:
            servers = DEFAULT_STUN_SERVERS

    if len(servers) < 2:
        # Need two servers to compare.
        return "unknown"

    # Bind to ephemeral port - both probes share one local socket so the
    # external mapping comparison is meaningful.
    bind_addr = ("0.0.0.0", 0)

    # Run both probes from the same local port. We use a small wrapper
    # that opens one persistent socket and reuses it.
    transaction_id_a = secrets.token_bytes(12)
    transaction_id_b = secrets.token_bytes(12)
    request_a = _build_stun_request(transaction_id_a)
    request_b = _build_stun_request(transaction_id_b)

    loop = asyncio.get_event_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    mapped_a: Optional[tuple[str, int]] = None
    mapped_b: Optional[tuple[str, int]] = None
    try:
        try:
            sock.bind(bind_addr)
        except OSError as exc:
            LOGGER.debug("stun_classifier: bind failed: %s", exc)
            return "unknown"

        try:
            await loop.sock_sendto(sock, request_a, servers[0])
        except (OSError, socket.gaierror) as exc:
            LOGGER.debug("stun_classifier: probe-a sendto failed: %s", exc)
            return "unknown"
        try:
            data_a = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout=timeout)
            mapped_a = _parse_mapped_address(data_a, transaction_id_a)
        except (asyncio.TimeoutError, OSError):
            return "unknown"

        try:
            await loop.sock_sendto(sock, request_b, servers[1])
        except (OSError, socket.gaierror) as exc:
            LOGGER.debug("stun_classifier: probe-b sendto failed: %s", exc)
            return "unknown"
        try:
            data_b = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout=timeout)
            mapped_b = _parse_mapped_address(data_b, transaction_id_b)
        except (asyncio.TimeoutError, OSError):
            return "unknown"
    finally:
        try:
            sock.close()
        except Exception:
            pass

    if mapped_a is None or mapped_b is None:
        return "unknown"

    ip_a, port_a = mapped_a
    ip_b, port_b = mapped_b

    if ip_a != ip_b:
        return "symmetric"
    if port_a != port_b:
        return "symmetric"
    # Same external (IP, port) seen by both → at least port-restricted.
    # Distinguishing port-restricted vs. address-restricted vs. full-cone
    # requires RFC 5780 §4.4 follow-up probes (changing the source IP
    # the server replies from). For Phase 3 we report port-restricted as
    # the conservative non-symmetric bucket - STUN is sufficient for it.
    return "port-restricted"


def parse_nat_class(value: str) -> str:
    """Normalise a string into the canonical enum, or 'unknown' on mismatch."""
    normalized = (value or "").strip().lower().replace("_", "-")
    if normalized in {
        "full-cone",
        "address-restricted",
        "port-restricted",
        "symmetric",
        "unknown",
    }:
        return normalized
    return "unknown"
