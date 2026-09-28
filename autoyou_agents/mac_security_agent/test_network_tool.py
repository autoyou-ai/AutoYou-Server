# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-304232623937526530302920-e05f7dbdbef538a8c261c78e

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-304232623937526530302920-e05f7dbdbef538a8c261c78e"


import json

from .bios import parse_bios_payload
from .enrichment import EnrichmentError, enrich_ip
from .network_tool import (
    SnapshotStore,
    aggregate_connections,
    build_network_overview,
    collect_network_snapshot,
    default_storage_path,
    parse_lsof_rows,
)
from .privileged import get_privileged_capabilities


def _lsof_payload() -> str:
    return """p4242
cSyntheticBrowser
usynthetic
f10
tIPv4
PTCP
n192.0.2.10:50000->198.51.100.20:443
TST=ESTABLISHED
f11
tIPv4
PUDP
n*:5353
p7
cSyntheticService
usynthetic
f12
tIPv6
PTCP
n*:8080
TST=LISTEN
"""


def test_collect_snapshot_attributes_tcp_and_udp_to_processes():
    snapshot = collect_network_snapshot(runner=lambda _: _lsof_payload())

    assert snapshot["platform"] == "macos"
    assert snapshot["summary"] == {"total": 3, "tcp": 2, "udp": 1, "remote": 1, "listening": 1}
    assert snapshot["processes"][0]["process_name"] == "SyntheticBrowser"
    assert snapshot["processes"][0]["total"] == 2
    assert snapshot["connections"][0]["remote_port"] == 443
    assert snapshot["connections"][0]["remote_service_hint"] == "HTTPS"
    assert snapshot["connections"][0]["policy_key"] == "4242|tcp|198.51.100.20|443"
    assert snapshot["connections"][1]["remote_scope"] == "none"


def test_lsof_parser_resolves_ipv6_and_process_image_path():
    rows = parse_lsof_rows(
        _lsof_payload(),
        process_path_resolver=lambda pid: "/Synthetic/Safari" if pid == 4242 else "",
    )

    assert rows[0]["executable_path"] == "/Synthetic/Safari"
    assert rows[0]["remote_address"] == "198.51.100.20"
    assert rows[1]["local_address"] == "0.0.0.0"
    assert rows[2]["local_address"] == "::"
    assert rows[2]["state"] == "Listen"


def test_aggregate_connections_keeps_process_path_in_identity():
    rows = [
        {"process_id": 9, "process_name": "Synthetic", "executable_path": "/Synthetic/A", "protocol": "tcp", "state": "Listen", "remote_scope": "none"},
        {"process_id": 9, "process_name": "Synthetic", "executable_path": "/Synthetic/B", "protocol": "udp", "state": "bound", "remote_scope": "none"},
    ]

    groups = aggregate_connections(rows)

    assert len(groups) == 2
    assert {group["executable_path"] for group in groups} == {"/Synthetic/A", "/Synthetic/B"}


def test_network_overview_groups_processes_peers_and_cached_geo():
    rows = [
        {
            "protocol": "tcp",
            "local_address": "192.0.2.10",
            "local_port": 50000,
            "remote_address": "8.8.8.8",
            "remote_port": 443,
            "state": "Established",
            "process_id": 4242,
            "process_name": "SyntheticBrowser",
            "executable_path": "/Synthetic/Browser",
            "remote_scope": "public",
            "remote_service_hint": "HTTPS",
            "source": "synthetic",
        },
        {
            "protocol": "udp",
            "local_address": "192.0.2.10",
            "local_port": 50001,
            "remote_address": "8.8.8.8",
            "remote_port": 443,
            "state": "connected",
            "process_id": 7,
            "process_name": "SyntheticService",
            "executable_path": "/Synthetic/Service",
            "remote_scope": "public",
            "remote_service_hint": "HTTPS",
            "source": "synthetic",
        },
    ]
    overview = build_network_overview(
        rows,
        geo_by_ip={"8.8.8.8": {"location": {"latitude": 1, "longitude": 2}}},
    )

    assert len(overview["process_groups"]) == 2
    assert len(overview["remote_peers"]) == 1
    assert overview["remote_peers"][0]["mapped"] is True
    assert "public peer" in overview["remote_peers"][0]["correlations"]
    assert "TCP + UDP" in overview["remote_peers"][0]["correlations"]


def test_map_boundaries_route_serves_natural_earth_features(monkeypatch):
    from fastapi.testclient import TestClient

    from .website.backend import app as backend_module

    monkeypatch.setattr(
        backend_module,
        "_describe_chat_auth_state",
        lambda request, agent_name: {"authenticated": True},
    )
    with TestClient(backend_module.app) as client:
        response = client.get("/api/map/boundaries")

    assert response.status_code == 200
    data = response.json()
    assert len(data["features"]) == 177
    assert {feature["geometry"]["type"] for feature in data["features"]} == {"Polygon", "MultiPolygon"}


def test_snapshot_store_is_persistent_and_test_root_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    path = default_storage_path()
    store = SnapshotStore(path)
    snapshot = collect_network_snapshot(runner=lambda _: _lsof_payload())

    first_id = store.save(snapshot)
    second_id = SnapshotStore(path).save(snapshot)

    assert path.is_relative_to(tmp_path / "AutoYou")
    assert second_id > first_id
    assert [row["id"] for row in SnapshotStore(path).latest(2)] == [second_id, first_id]


def test_snapshot_store_process_history_is_bounded_and_grouped(tmp_path):
    store = SnapshotStore(tmp_path / "network.sqlite3")
    snapshot = collect_network_snapshot(runner=lambda _: _lsof_payload())
    store.save(snapshot)
    store.save(snapshot)

    history = store.process_history(2)

    assert history[0]["process_name"] == "SyntheticBrowser"
    assert len(history[0]["samples"]) == 2
    assert history[0]["current_total"] == 2


def test_public_ip_enrichment_merges_ipquery_and_ripestat_and_caches(tmp_path):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if "ipquery.io" in url:
            return {
                "isp": {"asn": "AS15169", "org": "Synthetic Search", "isp": "Synthetic Search", "domain": "example.test"},
                "location": {"country": "United States", "country_code": "US", "state": "Synthetic", "city": "Example", "zipcode": "00000", "timezone": "UTC", "latitude": 1, "longitude": 2},
                "risk": {"risk_score": 0, "is_mobile": False, "is_vpn": False, "is_tor": False, "is_proxy": False, "is_datacenter": True},
            }
        return {"data": {"prefix": "8.8.8.0/24", "asns": [15169], "holder": "Synthetic Search", "resource": "8.8.8.8"}}

    store = SnapshotStore(tmp_path / "network.sqlite3")
    first = enrich_ip("8.8.8.8", store=store, fetch_json=fake_fetch)
    second = enrich_ip(
        "8.8.8.8",
        store=store,
        fetch_json=lambda _: (_ for _ in ()).throw(AssertionError("cache miss")),
    )

    assert first["success"] is True
    assert first["network"]["asn"] == "AS15169"
    assert first["routing"]["prefix"] == "8.8.8.0/24"
    assert second["cached"] is True
    assert len(calls) == 2


def test_public_ip_enrichment_rejects_non_global_addresses():
    try:
        enrich_ip("192.0.2.10", fetch_json=lambda _: {})
    except EnrichmentError as exc:
        assert "globally routable" in str(exc)
    else:
        raise AssertionError("non-global IP must not be sent to a public provider")


def test_privileged_capability_contract_is_probe_only():
    capabilities = get_privileged_capabilities()

    assert capabilities["lsof"]["capture_started"] is False
    assert capabilities["udp_remote_process_attribution"] == "lsof_connected_sockets_only"
    assert capabilities["kernel_telemetry"] == "requires_signed_network_extension"
    assert capabilities["bios_telemetry"]["write_operations"] is False


def test_bios_parser_is_read_only_and_excludes_machine_secrets():
    inventory = parse_bios_payload(
        json.dumps(
            {
                "hardware": {
                    "manufacturer": "Synthetic Apple",
                    "machine_model": "SyntheticMac1,1",
                    "boot_rom_version": "SYN-1",
                    "serial_number": "SYNTHETIC-SERIAL",
                },
                "software": {"system_version": "Synthetic macOS"},
            }
        )
    )

    assert inventory["read_only"] is True
    assert inventory["bios"]["SMBIOSBIOSVersion"] == "SYN-1"
    assert inventory["system"]["Version"] == "Synthetic macOS"
    assert "serial_number" not in json.dumps(inventory).lower()
