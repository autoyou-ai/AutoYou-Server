# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-be1c6812f93ec7720ed6e5e9


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
from shared import local_network_info

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-be1c6812f93ec7720ed6e5e9"
# from __debug_provenance_q__ import payment


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
