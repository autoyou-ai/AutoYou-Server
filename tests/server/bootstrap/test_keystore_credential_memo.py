# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""One Keychain approval must cover one unlock, and a refusal must be one refusal.

macOS authorizes a legacy Keychain item one access at a time; a plain "Allow" covers a single
access. An admin unlock reads the same items several times, so the person used to be asked
again for every read (eight sheets for one unlock) and a declined sheet was re-raised by each
internal retry. These tests use an in-memory keyring and never touch the real Keychain.
"""

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import base64

import pytest

import shared.keystore as keystore

SERVICE = "autoyou-synthetic-memo-check"
KEY = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


class _FakeKeyring:
    def __init__(self, passwords=None):
        self.passwords = dict(passwords or {})
        self.get_calls = []
        self.fail_with = None

    def get_keyring(self):
        return self

    def get_password(self, service, username):
        self.get_calls.append((service, username))
        if self.fail_with is not None:
            raise self.fail_with
        return self.passwords.get((service, username))

    def set_password(self, service, username, value):
        self.passwords[(service, username)] = value

    def delete_password(self, service, username):
        self.passwords.pop((service, username), None)


def _install(monkeypatch, fake):
    monkeypatch.setattr(keystore, "_keyring_mod", fake)
    monkeypatch.setattr(keystore, "_HAS_KEYRING", True)
    monkeypatch.setattr(keystore, "keyring_available", lambda: True)
    return fake


def _denied():
    return keystore._KeyringError("Can't get password from keychain: (-128, 'Keychain Access Denied')")


def test_one_unlocks_worth_of_reads_asks_for_each_item_once(monkeypatch):
    fake = _install(monkeypatch, _FakeKeyring({
        (SERVICE, "config-encryption-key"): KEY,
        (SERVICE, "pairing-password"): "synthetic-pairing-secret",
    }))
    for _ in range(4):
        assert keystore.get_or_create_key(SERVICE, "config-encryption-key", create=False) == b"k" * 32
        assert keystore.get_keyring_password(SERVICE, "pairing-password") == "synthetic-pairing-secret"
        assert keystore.read_keyring_password_strict(SERVICE, "pairing-password") == "synthetic-pairing-secret"
    keystore.get_keystore_status(SERVICE, "config-encryption-key")
    assert fake.get_calls == [(SERVICE, "config-encryption-key"), (SERVICE, "pairing-password")]


def test_a_missing_item_is_never_remembered_as_present(monkeypatch):
    fake = _install(monkeypatch, _FakeKeyring())
    assert keystore.get_keyring_password(SERVICE, "pairing-password") is None
    fake.passwords[(SERVICE, "pairing-password")] = "now-present"
    assert keystore.get_keyring_password(SERVICE, "pairing-password") == "now-present"
    assert len(fake.get_calls) == 2


def test_a_changed_or_removed_credential_is_never_served_stale(monkeypatch):
    fake = _install(monkeypatch, _FakeKeyring({(SERVICE, "pairing-password"): "old"}))
    assert keystore.get_keyring_password(SERVICE, "pairing-password") == "old"

    keystore.write_keyring_password(SERVICE, "pairing-password", "new")
    assert keystore.get_keyring_password(SERVICE, "pairing-password") == "new"
    assert keystore.replace_key(SERVICE, "config-encryption-key", b"r" * 32)
    assert keystore.get_or_create_key(SERVICE, "config-encryption-key", create=False) == b"r" * 32

    assert keystore.delete_key(SERVICE, "config-encryption-key")
    assert keystore.get_or_create_key(SERVICE, "config-encryption-key", create=False) is None
    assert keystore.delete_keyring_password(SERVICE, "pairing-password")
    assert keystore.get_keyring_password(SERVICE, "pairing-password") is None
    assert fake.passwords == {}


def test_a_created_key_is_remembered_so_the_next_read_does_not_ask_again(monkeypatch):
    fake = _install(monkeypatch, _FakeKeyring())
    created = keystore.get_or_create_key(SERVICE, "config-encryption-key", create=True)
    reads = len(fake.get_calls)
    assert keystore.get_or_create_key(SERVICE, "config-encryption-key", create=False) == created
    assert len(fake.get_calls) == reads


def test_a_declined_sheet_is_replayed_briefly_and_asked_again_only_after_it_expires(monkeypatch):
    fake = _install(monkeypatch, _FakeKeyring({(SERVICE, "pairing-password"): "secret"}))
    clock = [1000.0]
    monkeypatch.setattr(keystore, "_now", lambda: clock[0])
    fake.fail_with = _denied()

    # One unlock attempt makes several internal reads; only the first may raise the sheet.
    for _ in range(5):
        assert keystore.get_keyring_password(SERVICE, "pairing-password") is None
    assert len(fake.get_calls) == 1

    clock[0] += keystore._DENIAL_MEMO_SECONDS + 1
    fake.fail_with = None
    assert keystore.get_keyring_password(SERVICE, "pairing-password") == "secret"
    assert len(fake.get_calls) == 2, "a new attempt after the memo expires asks again"
    assert keystore.get_keyring_password(SERVICE, "pairing-password") == "secret"
    assert len(fake.get_calls) == 2


def test_a_denial_never_grants_anything_and_other_errors_are_not_treated_as_one(monkeypatch):
    fake = _install(monkeypatch, _FakeKeyring())
    fake.fail_with = keystore._KeyringError("Keychain is unavailable for another reason")
    assert keystore.get_keyring_password(SERVICE, "pairing-password") is None
    assert keystore.get_keyring_password(SERVICE, "pairing-password") is None
    assert len(fake.get_calls) == 2, "only a refusal is replayed, never an ordinary fault"


def test_the_strict_read_reports_a_just_declined_sheet_without_asking_again(monkeypatch):
    fake = _install(monkeypatch, _FakeKeyring())
    fake.fail_with = _denied()
    with pytest.raises(keystore.KeyringAccessError):
        keystore.read_keyring_password_strict(SERVICE, "index")
    with pytest.raises(keystore.KeyringAccessError, match="just declined"):
        keystore.read_keyring_password_strict(SERVICE, "index")
    assert len(fake.get_calls) == 1


def test_the_memo_never_crosses_keyring_backends(monkeypatch):
    first = _install(monkeypatch, _FakeKeyring({(SERVICE, "pairing-password"): "from-first"}))
    assert keystore.get_keyring_password(SERVICE, "pairing-password") == "from-first"
    second = _install(monkeypatch, _FakeKeyring({(SERVICE, "pairing-password"): "from-second"}))
    assert keystore.get_keyring_password(SERVICE, "pairing-password") == "from-second"
    assert len(first.get_calls) == 1 and len(second.get_calls) == 1


def test_forget_can_drop_one_item_or_everything(monkeypatch):
    fake = _install(monkeypatch, _FakeKeyring({
        (SERVICE, "a"): "1", (SERVICE, "b"): "2", ("other-service", "a"): "3",
    }))
    for service, username in ((SERVICE, "a"), (SERVICE, "b"), ("other-service", "a")):
        keystore.get_keyring_password(service, username)
    assert len(fake.get_calls) == 3
    keystore.forget_credential(SERVICE, "a")
    keystore.get_keyring_password(SERVICE, "a")
    keystore.get_keyring_password(SERVICE, "b")
    assert len(fake.get_calls) == 4
    keystore.forget_credential()
    for service, username in ((SERVICE, "a"), (SERVICE, "b"), ("other-service", "a")):
        keystore.get_keyring_password(service, username)
    assert len(fake.get_calls) == 7


def test_a_changed_pairing_password_is_not_masked_by_what_this_process_read_earlier(monkeypatch):
    import server

    fake = _install(monkeypatch, _FakeKeyring({(SERVICE, "pairing-password"): "old-secret"}))
    monkeypatch.setattr(server.STATE, "server_password", None)
    monkeypatch.setattr(server, "_server_keyring", fake)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: True)
    monkeypatch.setattr(server, "_server_keystore_service_name", lambda: SERVICE)

    assert server._load_keystore_server_password() == "old-secret"
    server._persist_server_password("new-secret")
    assert fake.passwords[(SERVICE, "pairing-password")] == "new-secret"
    assert server._load_keystore_server_password() == "new-secret", "the memo follows the write"
    reads = len(fake.get_calls)

    server._clear_persisted_server_password()
    assert fake.passwords == {}
    assert server._load_keystore_server_password() is None, "a cleared password is not served from memory"
    assert len(fake.get_calls) == reads + 1
