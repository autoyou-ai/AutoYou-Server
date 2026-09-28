# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-3436423233373332206f7220-74d18a58c2b31f14898dd744


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-3436423233373332206f7220-74d18a58c2b31f14898dd744"

import asyncio

import pytest

from shared.local_server_discovery import ServerAdvertisement, advertised_addresses


def test_loopback_servers_are_not_discoverable():
    assert advertised_addresses("127.0.0.1") == []


def test_private_bind_address_is_advertised_without_other_interfaces():
    assert advertised_addresses("192.168.10.20") == ["192.168.10.20"]


def test_named_or_ipv6_bind_addresses_are_not_guessed():
    assert advertised_addresses("server.local") == []
    assert advertised_addresses("::") == []


def test_wildcard_advertises_only_private_ipv4_addresses(monkeypatch):
    monkeypatch.setattr("shared.local_network_info.get_lan_ipv4_addresses", lambda: [
        "192.168.10.20", "127.0.0.1", "169.254.10.20", "203.0.113.20", "192.168.10.20",
    ])
    assert advertised_addresses("0.0.0.0") == ["192.168.10.20"]


@pytest.mark.asyncio
async def test_invalid_port_is_rejected_before_advertising():
    with pytest.raises(ValueError, match="discovery port"):
        await ServerAdvertisement().start(bind_host="192.168.10.20", port=65536, name="test", installation_id="synthetic-installation")


@pytest.mark.asyncio
async def test_discovery_identity_survives_restart_rename_and_network_change(monkeypatch):
    class FakeZeroconf:
        def __init__(self, **kwargs):
            pass

        async def async_register_service(self, info):
            finished = asyncio.get_running_loop().create_future()
            finished.set_result(None)
            return finished

        async_unregister_service = async_register_service

        async def async_close(self):
            pass

    monkeypatch.setattr("zeroconf.asyncio.AsyncZeroconf", FakeZeroconf)
    records = []
    for name, host, port, identity in [
        ("Synthetic-PC", "192.168.10.20", 8001, "synthetic-installation"),
        ("Renamed PC", "192.168.20.30", 9001, "synthetic-installation"),
        ("Synthetic-PC", "192.168.10.20", 8001, "different-installation"),
    ]:
        advertisement = ServerAdvertisement()
        assert await advertisement.start(bind_host=host, port=port, name=name, installation_id=identity)
        records.append(advertisement.info)
        await advertisement.close()
    assert records[0].name == records[1].name != records[2].name
    assert records[0].server == records[1].server != records[2].server
    assert records[1].parsed_addresses() == ["192.168.20.30"]
    assert records[1].port == 9001
    assert records[1].decoded_properties == {"v": "1", "name": "Renamed PC"}
    assert "synthetic-installation" not in records[0].server
    with pytest.raises(ValueError, match="saved installation ID"):
        await ServerAdvertisement().start(bind_host="192.168.10.20", port=8001, name="test", installation_id="")
