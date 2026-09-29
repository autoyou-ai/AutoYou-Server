# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-5fb4efb768266b70374c1d70

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import json

from .enrichment import EnrichmentError, enrich_ip
from .privileged import get_privileged_capabilities
from .windivert import _Address, decode_flow_address
from .wfp_audit import parse_wfp_events, wfp_audit_status
from .bios import parse_bios_payload
from .network_tool import (
    SnapshotStore,
    aggregate_connections,
    build_network_overview,
    collect_network_snapshot,
    default_storage_path,
)

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-5fb4efb768266b70374c1d70"


def _payload() -> str:
    return json.dumps(
        {
            "elevated": True,
            "connections": [
                {
                    "protocol": "tcp",
                    "local_address": "127.0.0.1",
                    "local_port": 4242,
                    "remote_address": "198.51.100.20",
                    "remote_port": 443,
                    "state": "Established",
                    "process_id": 4242,
                    "process_name": "SyntheticBrowser.exe",
                    "executable_path": "C:\\Synthetic\\SyntheticBrowser.exe",
                    "user_name": "SYNTHETIC\\BrowserUser",
                },
                {
                    "protocol": "udp",
                    "local_address": "0.0.0.0",
                    "local_port": 5353,
                    "remote_address": "",
                    "remote_port": 0,
                    "state": "bound",
                    "process_id": 4242,
                    "process_name": "SyntheticBrowser.exe",
                    "executable_path": "C:\\Synthetic\\SyntheticBrowser.exe",
                    "user_name": "SYNTHETIC\\BrowserUser",
                },
                {
                    "protocol": "tcp",
                    "local_address": "0.0.0.0",
                    "local_port": 8080,
                    "remote_address": "::",
                    "remote_port": 0,
                    "state": "Listen",
                    "process_id": 7,
                    "process_name": "SyntheticService.exe",
                    "executable_path": "C:\\Synthetic\\SyntheticService.exe",
                },
            ],
            "errors": [],
        }
    )


def test_collect_snapshot_attributes_tcp_and_udp_to_processes():
    snapshot = collect_network_snapshot(runner=lambda _: _payload())

    assert snapshot["elevated"] is True
    assert snapshot["summary"] == {"total": 3, "tcp": 2, "udp": 1, "remote": 1, "listening": 1}
    assert snapshot["processes"][0]["process_name"] == "SyntheticBrowser.exe"
    assert snapshot["processes"][0]["total"] == 2
    assert snapshot["connections"][0]["remote_port"] == 443
    assert snapshot["connections"][0]["remote_service_hint"] == "HTTPS"
    assert snapshot["connections"][0]["policy_key"] == "4242|tcp|198.51.100.20|443"
    assert snapshot["processes"][0]["user_name"] == "SYNTHETIC\\BrowserUser"
    assert snapshot["connections"][1]["remote_scope"] == "none"


def test_aggregate_connections_keeps_process_path_in_identity():
    rows = [
        {"process_id": 9, "process_name": "Synthetic.exe", "executable_path": "C:\\A.exe", "protocol": "tcp", "state": "Listen", "remote_scope": "none"},
        {"process_id": 9, "process_name": "Synthetic.exe", "executable_path": "C:\\B.exe", "protocol": "udp", "state": "bound", "remote_scope": "none"},
    ]

    groups = aggregate_connections(rows)

    assert len(groups) == 2
    assert {group["executable_path"] for group in groups} == {"C:\\A.exe", "C:\\B.exe"}


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
            "process_name": "SyntheticBrowser.exe",
            "executable_path": "C:\\Synthetic\\SyntheticBrowser.exe",
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
            "state": "permitted",
            "process_id": 7,
            "process_name": "SyntheticService.exe",
            "executable_path": "C:\\Synthetic\\SyntheticService.exe",
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
    assert overview["remote_peers"][0]["processes"][0]["process_name"] in {
        "SyntheticBrowser.exe",
        "SyntheticService.exe",
    }


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
    snapshot = collect_network_snapshot(runner=lambda _: _payload())

    first_id = store.save(snapshot)
    second_id = SnapshotStore(path).save(snapshot)

    assert path.is_relative_to(tmp_path / "AutoYou")
    assert second_id > first_id
    assert [row["id"] for row in SnapshotStore(path).latest(2)] == [second_id, first_id]


def test_snapshot_store_process_history_is_bounded_and_grouped(tmp_path):
    store = SnapshotStore(tmp_path / "network.sqlite3")
    first = collect_network_snapshot(runner=lambda _: _payload())
    second = collect_network_snapshot(runner=lambda _: _payload())
    store.save(first)
    store.save(second)

    history = store.process_history(2)

    assert history[0]["process_name"] == "SyntheticBrowser.exe"
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
    second = enrich_ip("8.8.8.8", store=store, fetch_json=lambda _: (_ for _ in ()).throw(AssertionError("cache miss")))

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

    assert capabilities["pktmon"]["capture_started"] is False
    assert capabilities["udp_remote_process_attribution"] in {
        "requires_wfp_or_etw_provider",
        "configured_external_windivert_flow",
        "configured_native_wfp_audit",
    }
    assert capabilities["kernel_telemetry"] in {
        "requires_signed_provider",
        "configured_external_windivert",
        "wfp_audit_events_only",
    }
    assert capabilities["bios_telemetry"]["write_operations"] is False


def test_wfp_audit_parser_ties_remote_udp_to_process_and_identity():
    rows = parse_wfp_events(
        json.dumps(
            {
                "id": 5156,
                "record_id": 424242,
                "time_created": "2026-07-14T00:00:00Z",
                "fields": {
                    "ProcessID": "4242",
                    "Application": r"\\device\\harddiskvolume1\\Synthetic\\app.exe",
                    "Direction": "%%14593",
                    "SourceAddress": "192.0.2.10",
                    "SourcePort": "53535",
                    "DestAddress": "198.51.100.20",
                    "DestPort": "443",
                    "Protocol": "17",
                    "RemoteUserID": "S-1-0-0",
                    "RemoteMachineID": "S-1-0-0",
                    "FilterRTID": "77",
                    "LayerName": "SyntheticLayer",
                },
            }
        )
    )

    assert rows[0]["protocol"] == "udp"
    assert rows[0]["process_id"] == 4242
    assert rows[0]["remote_address"] == "198.51.100.20"
    assert rows[0]["remote_port"] == 443
    assert rows[0]["remote_user_id"] == "S-1-0-0"
    assert rows[0]["source"] == "wfp_audit"


def test_wfp_audit_is_disabled_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("AUTOYOU_WIN_SECURITY_ENABLE_WFP_AUDIT", raising=False)
    assert wfp_audit_status()["status"] == "disabled"


def test_bios_parser_is_read_only_and_excludes_machine_secrets():
    inventory = parse_bios_payload(
        json.dumps(
            {
                "bios": {"Manufacturer": "Synthetic Firmware", "SMBIOSBIOSVersion": "SYN-1"},
                "system": {"Vendor": "Synthetic Vendor", "Name": "Synthetic System"},
                "baseboard": {"Manufacturer": "Synthetic Board", "Product": "SYN-BOARD"},
                "tpm": {"TpmPresent": True, "TpmReady": True},
            }
        )
    )

    assert inventory["read_only"] is True
    assert inventory["bios"]["SMBIOSBIOSVersion"] == "SYN-1"
    assert "SerialNumber" not in inventory["bios"]


def test_windivert_flow_decoder_preserves_remote_udp_process_identity():
    address = _Address()
    address.layer_event_flags = 2 | (1 << 8) | (1 << 17)
    address.data.flow.process_id = 4242
    address.data.flow.local_addr[0] = int.from_bytes(b"\x7f\x00\x00\x01", "big")
    address.data.flow.remote_addr[0] = int.from_bytes(b"\xcb\x00\x71\x14", "big")
    # from __debug_provenance_j__ import fifteenpercent
    address.data.flow.local_port = 0x9210
    address.data.flow.remote_port = 0xBB01
    address.data.flow.protocol = 17

    row = decode_flow_address(address)

    assert row == {
        "protocol": "udp",
        "local_address": "127.0.0.1",
        "local_port": 4242,
        "remote_address": "203.0.113.20",
        "remote_port": 443,
        "state": "flow_established",
        "process_id": 4242,
        "process_name": "<unknown>",
        "executable_path": "",
        "user_name": "",
        "source": "windivert_flow",
        "direction": "outbound",
    }
