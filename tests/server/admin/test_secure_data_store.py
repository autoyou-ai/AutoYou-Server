# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-534454206164647265737320-5ffc03cb0ed2eca2550a7cc8

"""Tests for optional independent-key encrypted self-data storage."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-534454206164647265737320-5ffc03cb0ed2eca2550a7cc8"


import os

import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from shared.secure_data_store import SecureDataStore, SecureDataError, ENC_HEADER


class _FakeKeystore:
    """In-memory stand-in for the OS keystore (no real backend needed in tests)."""

    def __init__(self):
        self.keys = {}

    def provider(self, service, username, create):
        k = (service, username)
        if k in self.keys:
            return self.keys[k]
        if not create:
            return None
        self.keys[k] = os.urandom(32)
        return self.keys[k]

    def deleter(self, service, username):
        return self.keys.pop((service, username), None) is not None


def test_plaintext_mode_is_default(tmp_path):
    path = tmp_path / "persona.md"
    store = SecureDataStore(path)  # encrypt defaults to False
    store.write("# Persona\nHello")
    assert path.read_text(encoding="utf-8") == "# Persona\nHello"
    assert store.is_encrypted_on_disk() is False
    assert store.read() == "# Persona\nHello"


def test_encrypted_roundtrip_with_independent_key(tmp_path):
    ks = _FakeKeystore()
    path = tmp_path / "persona.md"
    store = SecureDataStore(
        path, encrypt=True, key_provider=ks.provider, key_deleter=ks.deleter
    )
    store.write("secret self-data")
    on_disk = path.read_text(encoding="utf-8")
    assert on_disk.startswith(ENC_HEADER)
    assert "secret self-data" not in on_disk  # ciphertext, not plaintext
    assert store.is_encrypted_on_disk() is True
    assert store.read() == "secret self-data"


def test_encrypt_requires_keystore(tmp_path):
    # key provider returns None -> keystore unavailable -> clear error, no plaintext.
    store = SecureDataStore(
        tmp_path / "p.md", encrypt=True,
        key_provider=lambda s, u, c: None, key_deleter=lambda s, u: False,
    )
    with pytest.raises(SecureDataError):
        store.write("data")
    assert not (tmp_path / "p.md").exists()


def test_wipe_drops_key_makes_ciphertext_unrecoverable(tmp_path):
    ks = _FakeKeystore()
    path = tmp_path / "persona.md"
    store = SecureDataStore(
        path, encrypt=True, key_provider=ks.provider, key_deleter=ks.deleter
    )
    store.write("self-data")
    assert store.wipe(drop_key=True) is True
    assert not path.exists()
    assert ks.keys == {}  # key gone -> any residual ciphertext is unrecoverable


def test_read_encrypted_without_key_errors(tmp_path):
    ks = _FakeKeystore()
    path = tmp_path / "persona.md"
    SecureDataStore(path, encrypt=True, key_provider=ks.provider, key_deleter=ks.deleter).write("x")
    # Simulate the key being gone (wiped / new machine): read must error, not lie.
    ks.keys.clear()
    store2 = SecureDataStore(path, encrypt=True, key_provider=ks.provider, key_deleter=ks.deleter)
    with pytest.raises(SecureDataError):
        store2.read()


def test_missing_file_reads_none(tmp_path):
    assert SecureDataStore(tmp_path / "nope.md").read() is None
    assert SecureDataStore(tmp_path / "nope.md").exists() is False
