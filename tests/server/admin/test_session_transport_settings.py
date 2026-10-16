from types import SimpleNamespace

import pytest

import server
from shared import iroh_release


@pytest.fixture
def native_release(monkeypatch):
    release = {"policy": {"local_only": True}, "core": {"issuer": "https://core.example.invalid", "public_key": "11" * 32}}
    monkeypatch.setattr(iroh_release, "load_release_config", lambda: release)
    monkeypatch.setattr(server.STATE, "iroh_service", None)
    return release


@pytest.mark.parametrize("mode,legacy_allowed", [("legacy", True), ("prefer_iroh", True), ("iroh_only", False)])
def test_transport_settings_preserve_security_and_release_pins(native_release, mode, legacy_allowed):
    cfg = server._default_config()
    before_security = dict(cfg["security"])
    cfg["session_transport"] = {"grant_seconds": 3600}
    updated, touched, _ = server._apply_admin_ui_config_patch(cfg, {"session_transport": {"mode": mode}})
    assert touched == {"session_transport"}
    assert updated["session_transport"] == {"grant_seconds": 3600, "mode": mode}
    assert updated["security"] == before_security
    assert cfg["session_transport"] == {"grant_seconds": 3600}
    status = server._session_transport_status(updated)
    assert status["mode"] == mode and status["legacy_allowed"] is legacy_allowed
    assert status["iroh_ready"] is False
    if mode != "legacy":
        assert iroh_release.server_transport_config(updated)["core"] == native_release["core"]


@pytest.mark.parametrize("payload", [None, "legacy", {}, {"mode": "future"}, {"mode": True},
    {"mode": "prefer_iroh", "core": {"issuer": "https://untrusted.example.invalid"}}])
def test_transport_settings_reject_invalid_modes_and_network_pin_changes(native_release, payload):
    with pytest.raises(ValueError):
        server._apply_admin_ui_config_patch(server._default_config(), {"session_transport": payload})


def test_server_without_native_sdk_remains_explicitly_legacy(monkeypatch):
    monkeypatch.setattr(iroh_release, "load_release_config", lambda: None)
    monkeypatch.setattr(server.STATE, "iroh_service", None)
    cfg = server._default_config()
    assert server._session_transport_status(cfg)["mode"] == "legacy"
    for mode in ("prefer_iroh", "iroh_only"):
        with pytest.raises(ValueError, match="Iroh is not installed"):
            server._apply_admin_ui_config_patch(cfg, {"session_transport": {"mode": mode}})


def test_saving_legacy_policy_reports_pending_native_endpoint_restart(native_release, monkeypatch):
    monkeypatch.setattr(server.STATE, "iroh_service", SimpleNamespace(endpoint=SimpleNamespace(_closing=False)))
    cfg = server._default_config()
    assert server._session_transport_status(cfg)["iroh_ready"] is True
    cfg["session_transport"] = {"mode": "legacy"}
    status = server._session_transport_status(cfg)
    assert status["restart_required"] and status["legacy_allowed"] and not status["iroh_allowed"]
