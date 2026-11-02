# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-72b764d86cb8bed144abf59a

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

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import ipaddress
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-72b764d86cb8bed144abf59a"


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
    # from __debug_provenance_r__ import via
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

def _is_virtual_subnet(address: ipaddress.IPv4Address) -> bool:
    """Return True if the address belongs to known virtual/container subnets."""
    if address in ipaddress.ip_network("172.16.0.0/12"):
        return True
    if address in ipaddress.ip_network("192.168.56.0/24") or address in ipaddress.ip_network("192.168.99.0/24"):
        return True
    if address in ipaddress.ip_network("10.255.0.0/16"):
        return True
    return False

def _adapter_ipv4_classification() -> Dict[str, str]:
    """Inspect local network adapters and classify IPv4 addresses into
    'wifi', 'ethernet', or 'virtual'. Works on Windows, macOS, and Linux.
    """
    classified: Dict[str, str] = {}
    if os.name == "nt":
        import subprocess
        try:
            out = subprocess.check_output(
                ["netsh", "interface", "ipv4", "show", "addresses"],
                text=True, timeout=2.0, stderr=subprocess.DEVNULL
            )
            current_iface = ""
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Configuration for interface"):
                    m = re.search(r'"([^"]+)"', line)
                    current_iface = (m.group(1).lower() if m else "").strip()
                elif current_iface and line.startswith("IP Address:"):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        ip_cand = parts[1].strip()
                        if _usable_lan_ipv4(ip_cand):
                            if any(k in current_iface for k in ("vethernet", "virtual", "vmware", "vbox", "docker", "hyper-v", "wsl", "bluetooth", "loopback", "tailscale", "zerotier", "nord", "openvpn", "wireguard")):
                                classified[ip_cand] = "virtual"
                            elif any(k in current_iface for k in ("wi-fi", "wifi", "wireless", "wlan")):
                                classified[ip_cand] = "wifi"
                            elif any(k in current_iface for k in ("ethernet", "local area connection", "lan")):
                                classified[ip_cand] = "ethernet"
        except Exception:
            pass
    elif sys.platform == "darwin":
        import subprocess
        try:
            out = subprocess.check_output(["ifconfig"], text=True, timeout=2.0, stderr=subprocess.DEVNULL)
            current_iface = ""
            for line in out.splitlines():
                if line and not line[0].isspace() and ":" in line:
                    current_iface = line.split(":", 1)[0].strip().lower()
                elif current_iface and "inet " in line:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        ip_cand = parts[1].strip()
                        if _usable_lan_ipv4(ip_cand):
                            if current_iface.startswith(("utun", "bridge", "vbox", "vmnet", "docker")):
                                classified[ip_cand] = "virtual"
                            elif current_iface == "en0":
                                classified[ip_cand] = "wifi"
                            elif current_iface.startswith("en"):
                                classified[ip_cand] = "ethernet"
        except Exception:
            pass
    else:
        try:
            net_dir = Path("/sys/class/net")
            if net_dir.is_dir():
                for iface_path in net_dir.iterdir():
                    iface_name = iface_path.name.lower()
                    if iface_name == "lo" or iface_name.startswith(("docker", "veth", "br-", "virbr", "vmnet", "vbox")):
                        continue
        except Exception:
            pass
    return classified

def _score_lan_ipv4(candidate: str, classified_adapters: Dict[str, str], primary_ip: Optional[str]) -> int:
    try:
        parsed = ipaddress.ip_address(candidate)
    except Exception:
        return 0
    adapter_kind = classified_adapters.get(candidate)
    if adapter_kind == "wifi":
        return 1000 + (100 if candidate == primary_ip else 0)
    if adapter_kind == "ethernet":
        return 800 + (100 if candidate == primary_ip else 0)
    if adapter_kind == "virtual" or _is_virtual_subnet(parsed):
        return 50
    if parsed in ipaddress.ip_network("192.168.0.0/16"):
        return 400 + (100 if candidate == primary_ip else 0)
    if parsed in ipaddress.ip_network("10.0.0.0/8"):
        return 300 + (100 if candidate == primary_ip else 0)
    return 100 + (100 if candidate == primary_ip else 0)

def get_lan_ipv4_addresses() -> List[str]:
    """Private IPv4 addresses of this host, prioritizing native Wi-Fi and LAN
    adapters over virtual switches and adapters (WSL, Hyper-V, Docker).
    """
    configured = _configured_advertised_ipv4()
    primary = _primary_outbound_ipv4()
    adapters = _adapter_ipv4_classification()
    raw_candidates = [
        *adapters.keys(),
        primary,
        *_hostname_ipv4s(),
    ]
    seen: set[str] = set()
    usable_candidates: List[str] = []
    for candidate in raw_candidates:
        usable = _usable_lan_ipv4(candidate)
        if usable and usable not in seen:
            seen.add(usable)
            usable_candidates.append(usable)

    usable_candidates.sort(
        key=lambda ip: _score_lan_ipv4(ip, adapters, primary),
        reverse=True,
    )
    ordered: List[str] = []
    if configured:
        ordered.append(configured)
    for ip in usable_candidates:
        if ip not in ordered:
            ordered.append(ip)
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
