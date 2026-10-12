# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-47e81dbb41383a3e22137bc4


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from tests.support.paths import ensure_repo_on_path

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-47e81dbb41383a3e22137bc4"


ensure_repo_on_path()

import threading
import base64
import hashlib
from types import SimpleNamespace

import pytest

import shared.keystore as keystore
from shared import secure_storage


class _FakeKeyring:
    def __init__(self):
        self.passwords = {}
        self.set_calls = []
        self.get_calls = []
        # from __debug_provenance_c__ import subtask

    def get_keyring(self):
        return self

    def get_password(self, service, username):
        self.get_calls.append((service, username))
        return self.passwords.get((service, username))

    def set_password(self, service, username, value):
        self.set_calls.append((service, username, value))
        self.passwords[(service, username)] = value


class _BlockingKeyring(_FakeKeyring):
    def __init__(self, *, block_get=False, block_set=False):
        super().__init__()
        self.block_get = block_get
        self.block_set = block_set
        self.release = threading.Event()

    def get_password(self, service, username):
        if self.block_get:
            self.release.wait(5)
        return super().get_password(service, username)

    def set_password(self, service, username, value):
        if self.block_set:
            self.release.wait(5)
        return super().set_password(service, username, value)


def _install_available_keyring(monkeypatch, fake_keyring):
    monkeypatch.setattr(keystore, "_keyring_mod", fake_keyring)
    monkeypatch.setattr(keystore, "_HAS_KEYRING", True)
    monkeypatch.setattr(keystore, "keyring_available", lambda: True)


def test_null_and_fail_keyring_backends_are_unavailable(monkeypatch):
    monkeypatch.setattr(keystore, "_HAS_KEYRING", True)
    monkeypatch.setattr(keystore, "_HAS_FERNET", True)

    for module_name in ("keyring.backends.null", "keyring.backends.fail"):
        backend_type = type("Keyring", (), {"__module__": module_name})
        monkeypatch.setattr(
            keystore,
            "_keyring_mod",
            SimpleNamespace(get_keyring=lambda: backend_type()),
        )
        assert keystore._backend_name() == f"{module_name}.Keyring"
        assert keystore.keyring_available() is False


def test_keystore_load_missing_key_does_not_create_replacement(monkeypatch, tmp_path):
    fake_keyring = _FakeKeyring()
    _install_available_keyring(monkeypatch, fake_keyring)

    store = keystore.KeystoreJsonStore(
        tmp_path / "config.keystore.enc",
        default_factory=dict,
        service_name="autoyou-test-service",
        username="config-encryption-key",
    )
    store.path.write_bytes(b"synthetic encrypted payload")

    assert store.load() is None
    assert fake_keyring.set_calls == []


def test_keystore_save_creates_key_and_load_reuses_existing_key(monkeypatch, tmp_path):
    fake_keyring = _FakeKeyring()
    _install_available_keyring(monkeypatch, fake_keyring)

    store = keystore.KeystoreJsonStore(
        tmp_path / "config.keystore.enc",
        default_factory=dict,
        service_name="autoyou-test-service",
        username="config-encryption-key",
    )

    store.save({"server": {"name": "Synthetic Server"}})

    assert len(fake_keyring.set_calls) == 1
    assert store.load() == {"server": {"name": "Synthetic Server"}}
    assert len(fake_keyring.set_calls) == 1


def test_keyring_read_timeout_returns_no_key(monkeypatch):
    fake_keyring = _BlockingKeyring(block_get=True)
    _install_available_keyring(monkeypatch, fake_keyring)
    monkeypatch.setenv("AUTOYOU_KEYRING_OPERATION_TIMEOUT_SECONDS", "0.01")

    try:
        assert keystore.get_or_create_key("autoyou-test-service", "config-encryption-key", create=False) is None
    finally:
        fake_keyring.release.set()


def test_real_macos_keychain_runs_once_on_the_caller_thread(monkeypatch):
    backend_type = type("Keyring", (), {"__module__": "keyring.backends.macOS"})
    monkeypatch.setattr(keystore, "_keyring_mod", SimpleNamespace(get_keyring=lambda: backend_type()))
    monkeypatch.setattr(keystore, "_HAS_KEYRING", True)
    monkeypatch.setattr(keystore.sys, "platform", "darwin")
    monkeypatch.delenv("AUTOYOU_KEYRING_OPERATION_TIMEOUT_SECONDS", raising=False)

    calls = []
    assert keystore.call_keyring_operation(lambda: calls.append("once") or "value") == "value"
    assert calls == ["once"]


def test_explicit_bootstrap_read_skips_macos_keychain_authentication_ui(monkeypatch):
    get_calls = []
    backend_type = type("Keyring", (), {"__module__": "keyring.backends.macOS"})
    fake_keyring = SimpleNamespace(
        get_keyring=lambda: backend_type(),
        get_password=lambda *args: get_calls.append(args) or pytest.fail(
            "automatic startup must not use the interactive keyring API"
        ),
    )
    _install_available_keyring(monkeypatch, fake_keyring)
    monkeypatch.setattr(keystore.sys, "platform", "darwin")
    queries = []
    macos_api = SimpleNamespace(
        k_=lambda name: name,
        create_query=lambda **kwargs: queries.append(kwargs) or kwargs,
        c_void_p=lambda: object(),
        byref=lambda value: value,
        SecItemCopyMatching=lambda _query, _data: 0,
        cfstr_to_str=lambda _data: "synthetic-key",
    )
    monkeypatch.setattr(
        keystore,
        "_macos_keychain_api",
        lambda: macos_api,
    )

    assert keystore.get_keyring_password(
        "autoyou-test-service",
        "config-encryption-key",
        operation_timeout_seconds=0.01,
    ) == "synthetic-key"
    assert get_calls == []
    assert queries == [
        {
            "kSecClass": "kSecClassGenericPassword",
            "kSecMatchLimit": "kSecMatchLimitOne",
            "kSecAttrService": "autoyou-test-service",
            "kSecAttrAccount": "config-encryption-key",
            "kSecReturnData": True,
            "kSecUseAuthenticationUI": "kSecUseAuthenticationUISkip",
        }
    ]


def test_keystore_status_can_skip_credential_probe(monkeypatch):
    fake_keyring = _FakeKeyring()
    _install_available_keyring(monkeypatch, fake_keyring)

    status = keystore.get_keystore_status("autoyou-test-service", include_has_key=False)

    assert status["has_key"] is None
    assert status["credential_lookup_deferred"] is True


def test_existing_maximus_key_is_read_once_without_status_probe(monkeypatch, tmp_path):
    fake_keyring = _FakeKeyring()
    _install_available_keyring(monkeypatch, fake_keyring)
    root = (tmp_path / "maximus").resolve()
    root.mkdir()
    app_name = "AutoYou-test"
    service_name = "autoyou-autoyou-test-spm-" + hashlib.sha256(
        str(root).encode("utf-8")
    ).hexdigest()[:20]
    (root / secure_storage.KEY_MODE_FILE).write_text("keychain\n", encoding="ascii")
    fake_keyring.passwords[(service_name, secure_storage.KEY_USERNAME)] = base64.urlsafe_b64encode(
        b"s" * 32
    ).decode("ascii")

    _, source, resolved_service, _ = secure_storage._load_or_create_key(
        app_name=app_name,
        root=root,
        password=None,
    )

    assert source == "os_keychain"
    assert resolved_service == service_name
    assert fake_keyring.get_calls == [(service_name, secure_storage.KEY_USERNAME)]


def test_bootstrap_never_enrols_a_missing_maximus_key(monkeypatch, tmp_path):
    fake_keyring = _FakeKeyring()
    _install_available_keyring(monkeypatch, fake_keyring)

    with pytest.raises(
        secure_storage.SecureStorageError,
        match="no enrolled protected-storage key",
    ):
        secure_storage.enable_secure_storage(
            app_name="AutoYou-test",
            root=tmp_path / "maximus",
            password="synthetic-password",
            operation_timeout_seconds=0.01,
            allow_key_creation=False,
        )

    assert fake_keyring.set_calls == []
    assert not (tmp_path / "maximus" / secure_storage.KEY_MODE_FILE).exists()


def test_worker_attachment_never_enrols_a_missing_maximus_key(monkeypatch, tmp_path):
    fake_keyring = _FakeKeyring()
    _install_available_keyring(monkeypatch, fake_keyring)
    monkeypatch.setenv("AUTOYOU_SECURE_STORAGE_MODE", secure_storage.SECURE_PROFESSIONAL_MAXIMUS_MODE)
    monkeypatch.setenv("AUTOYOU_SECURE_STORAGE_ROOT", str(tmp_path / "maximus"))
    monkeypatch.setenv("AUTOYOU_SECURE_STORAGE_APP", "AutoYou-test")

    with pytest.raises(
        secure_storage.SecureStorageError,
        match="no enrolled protected-storage key",
    ):
        secure_storage.enable_secure_storage_from_environment()

    assert fake_keyring.set_calls == []
    assert not (tmp_path / "maximus" / secure_storage.KEY_MODE_FILE).exists()


def test_keystore_save_times_out_instead_of_blocking_on_keyring_write(monkeypatch, tmp_path):
    fake_keyring = _BlockingKeyring(block_set=True)
    _install_available_keyring(monkeypatch, fake_keyring)
    monkeypatch.setenv("AUTOYOU_KEYRING_OPERATION_TIMEOUT_SECONDS", "0.01")

    store = keystore.KeystoreJsonStore(
        tmp_path / "config.keystore.enc",
        default_factory=dict,
        service_name="autoyou-test-service",
        username="config-encryption-key",
    )

    try:
        try:
            store.save({"server": {"name": "Synthetic Server"}})
            raise AssertionError("Expected keystore save to fail after keyring write timeout")
        except RuntimeError as exc:
            assert "OS keystore is not available" in str(exc)
    finally:
        fake_keyring.release.set()
