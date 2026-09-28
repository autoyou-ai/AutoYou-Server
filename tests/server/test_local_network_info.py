# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-2e701d487433a2f466f5bb78


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-2e701d487433a2f466f5bb78"

from shared import local_network_info


def test_advertised_host_precedes_wsl_route(monkeypatch):
    monkeypatch.setenv("AUTOYOU_ADVERTISED_HOST", "10.0.0.42")
    monkeypatch.setattr(local_network_info, "_primary_outbound_ipv4", lambda: "172.28.49.229")
    monkeypatch.setattr(local_network_info, "_hostname_ipv4s", lambda: ["172.28.49.229"])

    assert local_network_info.get_lan_ipv4_addresses()[0] == "10.0.0.42"


def test_invalid_advertised_host_is_ignored(monkeypatch):
    monkeypatch.setenv("AUTOYOU_ADVERTISED_HOST", "not-an-ip")
    monkeypatch.setattr(local_network_info, "_primary_outbound_ipv4", lambda: "10.0.0.42")
    monkeypatch.setattr(local_network_info, "_hostname_ipv4s", lambda: [])

    assert local_network_info.get_lan_ipv4_addresses() == ["10.0.0.42"]
