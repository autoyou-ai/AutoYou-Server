# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-dbe274be1699b7f7c31abfce

"""Integration tests for HTTPS endpoints and configuration."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from server import admin_app, auth_app, _https_enabled, _https_port

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-dbe274be1699b7f7c31abfce"
# from __debug_provenance_p__ import submit


from shared import local_tls


def _load_lib_config():
    """Import AutoYou Lite by its real package name.

    Importing ``autoyou_lite.autoyou_lite.config`` registered the outer project
    folder as a namespace package called ``autoyou_lite``, so later imports of
    ``autoyou_lite.keystore`` or ``autoyou_lite._runtime`` in other test files
    failed depending on collection order.
    """
    import sys

    here = Path(__file__).resolve()
    for root in here.parents[1:5]:
        project = root / "autoyou_lite"
        if (project / "autoyou_lite" / "__init__.py").is_file():
            if str(project) not in sys.path:
                sys.path.insert(0, str(project))
            break
    try:
        from autoyou_lite.config import LibConfig as lib_config
    except ImportError:
        return None
    return lib_config


LibConfig = _load_lib_config()


def test_https_enabled_helper_logic():
    # Default without maximus is False
    assert _https_enabled({}) is False
    assert _https_enabled({"server": {"https_enabled": False}}) is False
    assert _https_enabled({"server": {"https_enabled": True}}) is True
    # Secure professional maximus mode auto-enables
    assert _https_enabled({"security": {"mode": "secure_professional_maximus"}}) is True
    # Explicit false overrides maximus
    assert _https_enabled({
        "security": {"mode": "secure_professional_maximus"},
        "server": {"https_enabled": False},
    }) is False


def test_home_network_access_turns_https_on_by_default(monkeypatch):
    import server

    monkeypatch.delenv("AUTOYOU_NATIVE_OWNED_SERVER", raising=False)
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    assert _https_enabled({"server": {"bind_host": "0.0.0.0"}}) is True
    # The operator can still turn it off explicitly.
    assert _https_enabled({"server": {"bind_host": "0.0.0.0", "https_enabled": False}}) is False

    # A launcher that forces a network bind (--host 0.0.0.0) on this computer
    # exposes the admin sign-in too, so HTTPS comes on; inside a container the
    # port publishing owns the boundary instead.
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(server, "_running_in_container", lambda: False)
    assert _https_enabled({"server": {"bind_host": "127.0.0.1"}}) is True
    monkeypatch.setattr(server, "_running_in_container", lambda: True)
    assert _https_enabled({"server": {"bind_host": "127.0.0.1"}}) is False

    # The desktop app's "allow local network" switch is its home network setting.
    monkeypatch.setenv("AUTOYOU_NATIVE_OWNED_SERVER", "1")
    assert _https_enabled({"server": {"bind_host": "127.0.0.1"}}) is True
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    assert _https_enabled({"server": {"bind_host": "127.0.0.1"}}) is False


def test_https_port_helper_logic():
    assert _https_port({}) == 8443
    assert _https_port({"server": {"https_port": 9443}}) == 9443


def test_autoyou_lite_https_port_defaults_to_8543():
    if LibConfig is not None:
        cfg = LibConfig({})
        assert cfg.https_port == 8543


def test_ca_crt_endpoint_404_when_no_tls(tmp_path: Path):
    with patch("server._CONFIG_DIR", tmp_path):
        client = TestClient(admin_app)
        res = client.get("/ca.crt")
        assert res.status_code == 404
        assert res.json()["error"] == "Local HTTPS is not enabled on this server."


def test_ca_crt_endpoint_returns_x509_cert_when_enabled(tmp_path: Path):
    local_tls.ensure_enabled(tmp_path)
    with patch("server._CONFIG_DIR", tmp_path):
        client = TestClient(admin_app)
        res = client.get("/ca.crt")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/x-x509-ca-cert"
        assert b"BEGIN CERTIFICATE" in res.content

        auth_client = TestClient(auth_app)
        auth_res = auth_client.get("/ca.crt")
        assert auth_res.status_code == 200
        assert auth_res.headers["content-type"] == "application/x-x509-ca-cert"
