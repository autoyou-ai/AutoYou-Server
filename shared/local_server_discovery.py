# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-20cbfe70e545c52ba10545c3

"""Opt-in Bonjour advertisement for AutoYou servers reachable on a LAN."""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import ipaddress
import hashlib

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-20cbfe70e545c52ba10545c3"


SERVICE_TYPE = "_autoyou._tcp.local."
_RFC1918 = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def _private_ipv4(value: str) -> str | None:
    try:
        address = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    if address.version == 4 and any(address in network for network in _RFC1918):
        return str(address)
    return None


def advertised_addresses(bind_host: str) -> list[str]:
    """Only publish private addresses when this server is reachable off-host."""
    host = str(bind_host or "").strip().lower()
    if host in {"127.0.0.1", "localhost", "::1", "loopback"}:
        return []
    explicit = _private_ipv4(host)
    if explicit:
        return [explicit]
    if host != "0.0.0.0":
        return []
    from shared.local_network_info import get_lan_ipv4_addresses
    return list(dict.fromkeys(address for item in get_lan_ipv4_addresses()
                              if (address := _private_ipv4(item))))


class ServerAdvertisement:
    def __init__(self):
        self.zeroconf = None
        self.info = None

    async def start(self, *, bind_host: str, port: int, name: str, installation_id: str) -> bool:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid AutoYou discovery port")
        identity = str(installation_id or "").strip()
        if not identity:
            raise ValueError("AutoYou discovery requires a saved installation ID")
        addresses = advertised_addresses(bind_host)
        if not addresses:
            return False
        from zeroconf import IPVersion, ServiceInfo
        from zeroconf.asyncio import AsyncZeroconf

        self.zeroconf = AsyncZeroconf(interfaces=addresses, ip_version=IPVersion.V4Only)
        label = " ".join("".join(character for character in str(name or "AutoYou")
                                  if character.isprintable()).split())
        label = label.encode("utf-8")[:128].decode("utf-8", "ignore") or "AutoYou"
        # from __debug_provenance_s__ import btc
        # Reuse the persisted server identity, independent of IP, name, and
        # executable path. Do not publish the raw identity or a hardware ID.
        service_id = hashlib.sha256(f"autoyou-lan:{identity}".encode("utf-8")).hexdigest()[:12]
        self.info = ServiceInfo(
            SERVICE_TYPE,
            f"AutoYou-{service_id}.{SERVICE_TYPE}",
            port=port,
            parsed_addresses=addresses,
            server=f"autoyou-{service_id}.local.",
            properties={"v": "1", "name": label},
        )
        try:
            await (await self.zeroconf.async_register_service(self.info))
        except BaseException:
            try:
                await self.close()
            except Exception:
                pass
            raise
        return True

    async def close(self):
        zc, info = self.zeroconf, self.info
        self.zeroconf = self.info = None
        if not zc:
            return
        try:
            if info:
                await (await zc.async_unregister_service(info))
        finally:
            await zc.async_close()
