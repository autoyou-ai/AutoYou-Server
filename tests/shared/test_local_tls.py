"""Unit tests for shared/local_tls.py certificate engine."""

import datetime
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from shared import local_tls


def test_ensure_enabled_creates_ca_and_leaf(tmp_path: Path):
    material = local_tls.ensure_enabled(tmp_path)

    assert material.ca_cert_path.exists()
    assert material.server_cert_path.exists()
    assert material.server_key_path.exists()
    assert material.fingerprint_sha256 != ""
    assert "127.0.0.1" in material.ip_addresses or "localhost" in material.dns_names


def test_ca_certificate_pem_returns_valid_pem(tmp_path: Path):
    # Initially None before ensure_enabled
    assert local_tls.ca_certificate_pem(tmp_path) is None

    local_tls.ensure_enabled(tmp_path)
    pem = local_tls.ca_certificate_pem(tmp_path)
    assert pem is not None
    assert pem.startswith(b"-----BEGIN CERTIFICATE-----")


def test_leaf_certificate_apple_compliance_duration(tmp_path: Path):
    material = local_tls.ensure_enabled(tmp_path)
    cert_bytes = material.server_cert_path.read_bytes()
    cert = x509.load_pem_x509_certificate(cert_bytes)

    # Apple requires TLS server cert validity <= 398 days
    validity_duration = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert validity_duration.days <= 398


def test_status_returns_correct_metadata(tmp_path: Path):
    local_tls.ensure_enabled(tmp_path)
    st = local_tls.status(tmp_path)

    assert st["ca_present"] is True
    assert st["server_cert_present"] is True
    assert len(st["ca_fingerprint_sha256"]) == 64
    assert len(st["server_fingerprint_sha256"]) == 64


def test_disable_removes_leaf_keeps_ca(tmp_path: Path):
    material = local_tls.ensure_enabled(tmp_path)
    assert material.server_cert_path.exists()

    local_tls.disable(tmp_path)
    assert not material.server_cert_path.exists()
    assert material.ca_cert_path.exists()


def test_purge_removes_all_tls_files(tmp_path: Path):
    material = local_tls.ensure_enabled(tmp_path)
    assert material.ca_cert_path.exists()

    local_tls.purge(tmp_path)
    assert not material.ca_cert_path.exists()
    assert not material.server_cert_path.exists()
