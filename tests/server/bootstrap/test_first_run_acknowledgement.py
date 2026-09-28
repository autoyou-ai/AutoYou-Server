# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Tests for first-run detection + LICENSE acknowledgement (cross-environment)."""

import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from shared import first_run
from shared import platform_runtime


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    # AUTOYOU_TEST_ROOT makes get_mutable_data_dir deterministic in every env.
    monkeypatch.setenv(platform_runtime.TEST_RUNTIME_ROOT_ENV, str(tmp_path))
    return tmp_path / "AutoYou"


def test_clean_run_is_not_acknowledged(data_root):
    assert first_run.is_license_acknowledged() is False
    assert first_run.read_license_acknowledgement() is None


def test_record_then_acknowledged(data_root):
    path = first_run.record_license_acknowledgement(accepted_by="tester")
    assert path.exists()
    assert first_run.is_license_acknowledged() is True
    record = first_run.read_license_acknowledgement()
    assert record["accepted_by"] == "tester"
    assert record["agreement_version"] == first_run.CURRENT_AGREEMENT_VERSION
    assert "accepted_at_iso" in record


def test_version_mismatch_requires_reacceptance(data_root):
    first_run.record_license_acknowledgement(agreement_version="0")
    # Acknowledged for v0, but current requires v1 -> not acknowledged.
    assert first_run.is_license_acknowledged(required_version="1") is False
    # required_version=None accepts any prior acknowledgement.
    assert first_run.is_license_acknowledged(required_version=None) is True


def test_clear_acknowledgement(data_root):
    first_run.record_license_acknowledgement()
    assert first_run.is_license_acknowledged() is True
    assert first_run.clear_license_acknowledgement() is True
    assert first_run.is_license_acknowledged() is False
    # Clearing again is a no-op.
    assert first_run.clear_license_acknowledgement() is False


def test_is_first_run_combines_config_and_ack(data_root):
    # No config + no ack -> first run.
    assert first_run.is_first_run(config_exists=False) is True
    # Config present -> never first run regardless of ack.
    assert first_run.is_first_run(config_exists=True) is False
    # No config but acknowledged -> not a clean first run.
    first_run.record_license_acknowledgement()
    assert first_run.is_first_run(config_exists=False) is False


def test_legacy_unparseable_marker(data_root):
    path = first_run.license_ack_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("accepted", encoding="utf-8")  # not JSON
    record = first_run.read_license_acknowledgement()
    assert record["legacy"] is True
    # Legacy marker predates versioning -> require re-acceptance for current version.
    assert first_run.is_license_acknowledged(required_version="1") is False
    assert first_run.is_license_acknowledged(required_version=None) is True


def test_sealed_marker_without_active_storage_requires_reacceptance(data_root):
    """A locked Maximus marker must not crash login or count as accepted."""
    path = first_run.license_ack_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(first_run.SECURE_FILE_HEADER + b"synthetic-ciphertext")

    assert first_run.is_license_acknowledgement_pending_unlock() is True
    assert first_run.read_license_acknowledgement() is None
    assert first_run.is_license_acknowledged() is False
