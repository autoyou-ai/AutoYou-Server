# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Best-effort LAN address discovery for Local Pair connect-info surfaces.

Local Pair clients (iOS / Android / desktop Connect) need the server's LAN
IPv4 address and admin port to connect directly over the local network. This
module discovers the host's private addresses WITHOUT emitting any discovery
traffic: a UDP ``connect()`` only selects a route locally (no packet leaves
the machine), and hostname resolution stays inside the local resolver. That
deliberately avoids mDNS/Bonjour-style broadcasts, which would announce the
server to every device on the network.

Works on Windows, macOS, and Linux, under both source checkouts
(``scripts/bootstrap_autoyou.py``) and compiled AutoYou builds - only the
standard library is used.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any, Dict, List, Optional

def _primary_outbound_ipv4() -> Optional[str]:
    """IPv4 the OS would route external traffic through. Sends no packets."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # TEST-NET-2 documentation address; UDP connect() merely picks the
            # local interface/route - nothing is transmitted.
            probe.connect(("198.51.100.1", 9))
            return str(probe.getsockname()[0])
    except Exception:
        return None

def _hostname_ipv4s() -> List[str]:
    """Additional local IPv4s from hostname resolution (Windows-friendly)."""
    addresses: List[str] = []
    hostname = ""
    try:
        hostname = socket.gethostname()
    except Exception:
        return addresses
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM):
            addresses.append(str(info[4][0]))
    except Exception:
        pass
    try:
        addresses.extend(str(item) for item in socket.gethostbyname_ex(hostname)[2])
    except Exception:
        pass
    return addresses

def _usable_lan_ipv4(candidate: Optional[str]) -> Optional[str]:
    normalized = str(candidate or "").strip()
    if not normalized:
        return None
    try:
        parsed = ipaddress.ip_address(normalized)
    except ValueError:
        return None
    if parsed.version != 4:
        return None
    if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified or parsed.is_multicast:
        return None
    # Local Pair is a same-network feature; only advertise private-range
    # addresses (public IPs would be misleading and leak more than needed).
    if not parsed.is_private:
        return None
    return normalized

def _configured_advertised_ipv4() -> Optional[str]:
    """Return an explicit host address for NAT/WSL port-forwarded servers."""
    return _usable_lan_ipv4(os.getenv("AUTOYOU_ADVERTISED_HOST"))

def get_lan_ipv4_addresses() -> List[str]:
    """Private IPv4 addresses of this host, primary route first, deduped."""
    ordered: List[str] = []
    seen: set[str] = set()
    candidates = [
        _configured_advertised_ipv4(),
        _primary_outbound_ipv4(),
        *_hostname_ipv4s(),
    ]
    for candidate in candidates:
        usable = _usable_lan_ipv4(candidate)
        if usable and usable not in seen:
            seen.add(usable)
            ordered.append(usable)
    return ordered

def is_loopback_only_bind(bind_host: Optional[str]) -> bool:
    """True when the server only listens on loopback, so LAN devices cannot
    reach it regardless of the advertised addresses."""
    normalized = str(bind_host or "").strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1", "loopback"}

def build_local_pair_info(
    *,
    port: int,
    bind_host: Optional[str] = None,
    server_name: str = "",
) -> Dict[str, Any]:
    """Payload consumed by the admin Home / Live View Local Pair cards."""
    addresses = get_lan_ipv4_addresses()
    loopback_only = is_loopback_only_bind(bind_host)
    return {
        "addresses": addresses,
        "primary_address": addresses[0] if addresses else "",
        "port": int(port),
        "bind_host": str(bind_host or "").strip(),
        "loopback_only": loopback_only,
        "lan_reachable": bool(addresses) and not loopback_only,
        "server_name": str(server_name or "").strip(),
    }
