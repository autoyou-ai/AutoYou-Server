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
