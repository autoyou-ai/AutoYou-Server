from __future__ import annotations

import asyncio
import base64
import json
import os
import stat
from pathlib import Path

import pytest

import server
from shared import secure_storage
from shared.local_authenticator_vault import LocalAuthenticatorVault, VaultAuthenticatorCredentials
from shared.vault_crypto import (
    ARGON2_SALT_MAGIC,
    DEFAULT_BOOTSTRAP_PASSWORD,
    LocalMasterVault,
    VaultCryptoError,
    VaultInvalidPasswordError,
    aead_decrypt_v2,
    aead_encrypt_v2,
    derive_subkey,
    is_argon2_salt_blob,
    is_v2_aead_envelope,
    make_argon2_salt_blob,
)


def test_v2_aead_roundtrip_tamper_and_downgrade_rejection():
    password = "Synthetic-Strong-Passphrase!2026"
    payload = json.dumps({"secret": "synthetic-value", "mode": "secure"})
    envelope = aead_encrypt_v2(payload, password, purpose="config", meta={"uses_default_password": False})
    assert is_v2_aead_envelope(envelope)
    assert aead_decrypt_v2(envelope, password, purpose="config").decode("utf-8") == payload

    with pytest.raises(VaultInvalidPasswordError):
        aead_decrypt_v2(envelope, "wrong-password-123456", purpose="config")

    with pytest.raises(VaultCryptoError):
        aead_decrypt_v2(envelope, password, purpose="local-master-vault")

    # Tampering with KDF parameters (downgrade attempt) or authenticated metadata must fail closed.
    prefix, raw_json = envelope.split("\n", 1)
    parsed = json.loads(raw_json)
    tampered_meta = dict(parsed, meta={"uses_default_password": True})
    with pytest.raises(VaultInvalidPasswordError):
        aead_decrypt_v2(prefix + "\n" + json.dumps(tampered_meta), password, purpose="config")

    downgraded = dict(parsed, kdf_params={"m": 1024, "t": 1, "p": 1})
    with pytest.raises(VaultCryptoError):
        aead_decrypt_v2(prefix + "\n" + json.dumps(downgraded), password, purpose="config")


def test_local_master_vault_lifecycle_password_change_and_key_rotation(tmp_path):
    vault_path = tmp_path / "Native" / "local_vault.v2.enc"
    vault = LocalMasterVault(vault_path)
    assert not vault.exists()

    status = vault.create(DEFAULT_BOOTSTRAP_PASSWORD)
    assert status["unlocked"] is True
    assert status["uses_default_password"] is True
    assert status["cipher"] == "aes-256-gcm"
    assert vault_path.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(vault_path.stat().st_mode) == 0o600

    # Domain separation: distinct domains yield independent 32-byte subkeys.
    k_history = vault.subkey("history")
    k_cloud = vault.subkey("cloud")
    k_conn = vault.subkey("connections")
    assert len(k_history) == 32 and len(k_cloud) == 32 and len(k_conn) == 32
    assert len({k_history, k_cloud, k_conn}) == 3

    # Changing password re-wraps the same 256-bit Master DEK without invalidating subkeys.
    new_pw = "Quantum-Resistant-Vault-Password#2026"
    updated = vault.change_password(new_pw)
    assert updated["uses_default_password"] is False
    assert vault.subkey("history") == k_history
    assert vault.subkey("cloud") == k_cloud

    # Lock wipes in-memory keys; old default password fails, new password unlocks.
    vault.lock()
    assert not vault.unlocked
    with pytest.raises(VaultCryptoError):
        vault.subkey("history")
    with pytest.raises(VaultInvalidPasswordError):
        vault.unlock(DEFAULT_BOOTSTRAP_PASSWORD)

    reopened = LocalMasterVault(vault_path)
    assert reopened.inspect_metadata()["uses_default_password"] is False
    reopened.unlock(new_pw)
    assert reopened.subkey("history") == k_history

    # Rotating master key creates fresh subkeys and runs re-encryption callback atomically.
    secret_file = tmp_path / "cloud_credentials.v2.enc"
    reopened.cipher("cloud").save_json_file(secret_file, {"auth_token": "synthetic-token"})

    def reencrypt(old_c, new_c):
        data = old_c("cloud").load_json_file(secret_file)
        new_c("cloud").save_json_file(secret_file, data)

    reopened.rotate_master_key(on_rotate=reencrypt)
    assert reopened.subkey("history") != k_history
    assert reopened.cipher("cloud").load_json_file(secret_file) == {"auth_token": "synthetic-token"}


def test_server_aead_writes_v2_and_reads_legacy_v1(monkeypatch):
    from cryptography.fernet import Fernet

    password = "Synthetic-Server-Password!2026"
    plaintext = json.dumps({"server_name": "AutoYou-Synthetic"})
    v2_env = server.aead_encrypt(plaintext, password)
    assert is_v2_aead_envelope(v2_env)
    assert server.aead_decrypt(v2_env, password) == plaintext

    # Legacy V1 PBKDF2 + Fernet envelope stays 100% readable.
    salt = os.urandom(16)
    legacy_key = server._get_key_from_password(password, salt)
    legacy_token = Fernet(legacy_key).encrypt(plaintext.encode("utf-8"))
    legacy_env = base64.urlsafe_b64encode(salt + base64.urlsafe_b64decode(legacy_token)).decode("ascii").rstrip("=")
    assert not is_v2_aead_envelope(legacy_env)
    assert server.aead_decrypt(legacy_env, password) == plaintext


def test_server_bootstrap_prefer_local_vault_preserves_unreadable_keystore_backup(tmp_path, monkeypatch):
    cfg_enc = tmp_path / "config.encrypted"
    cfg_bak = tmp_path / "config.encrypted.bak"
    cfg_ks = tmp_path / "config.keystore.enc"
    legacy_bytes = b"synthetic-legacy-keychain-ciphertext"
    cfg_ks.write_bytes(legacy_bytes)

    monkeypatch.setenv("AUTOYOU_PREFER_LOCAL_VAULT", "1")
    monkeypatch.delenv("AUTOYOU_SERVER_PASSWORD", raising=False)
    monkeypatch.setattr(server, "CONFIG_FILE_PATH", str(cfg_enc))
    monkeypatch.setattr(server, "CONFIG_BAK_PATH", str(cfg_bak))
    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", cfg_ks)
    monkeypatch.setattr(server, "_load_keystore_config_during_bootstrap", lambda ks: None)

    class _LockedKeystore:
        def is_available(self):
            return True

        def exists(self):
            return cfg_ks.is_file()

        def load(self, **kwargs):
            return None

    monkeypatch.setattr(server, "_get_server_keystore", lambda: _LockedKeystore())
    monkeypatch.setattr(server, "_persist_server_password", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not touch OS Keychain")))
    monkeypatch.setattr(server, "_clear_main_keystore_config", lambda: (_ for _ in ()).throw(AssertionError("should not delete legacy keystore")))

    asyncio.run(server.bootstrap_password_and_config())

    backup_path = tmp_path / "config.keystore.enc.keychain-backup"
    assert backup_path.is_file()
    assert backup_path.read_bytes() == legacy_bytes
    assert cfg_enc.is_file()
    assert is_v2_aead_envelope(cfg_enc.read_text(encoding="utf-8"))
    assert server.STATE.used_default_password is True
    assert server.STATE.config_store == server.CONFIG_STORE_ENCRYPTED


def test_secure_professional_maximus_uses_argon2_salt_and_reads_legacy_pbkdf2(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_PREFER_LOCAL_VAULT", "1")
    root = tmp_path / "spm"
    try:
        status = secure_storage.enable_secure_storage(
            app_name="AutoYouTest",
            root=root,
            password="Synthetic-Maximus-Password!2026",
        )
        assert status["enabled"] is True
        assert status["key_source"] == "password_fallback"
        salt_blob = (root / secure_storage.KEY_SALT_FILE).read_bytes()
        assert is_argon2_salt_blob(salt_blob)
        assert salt_blob.startswith(ARGON2_SALT_MAGIC)

        target = root / "owned.json"
        secure_storage.save_secure_json(target, {"ok": True})
        assert secure_storage.load_secure_json(target) == {"ok": True}

        rot = secure_storage.rotate_secure_storage(password="Synthetic-Rotated-Password!2026")
        assert rot["files_rekeyed"] >= 1
        assert is_argon2_salt_blob((root / secure_storage.KEY_SALT_FILE).read_bytes())
        assert secure_storage.load_secure_json(target) == {"ok": True}
    finally:
        secure_storage.disable_secure_storage()

    # Legacy 16-byte PBKDF2 salt blob remains readable.
    legacy_root = tmp_path / "spm-legacy"
    legacy_root.mkdir()
    (legacy_root / secure_storage.KEY_MODE_FILE).write_text("password\n", encoding="ascii")
    (legacy_root / secure_storage.KEY_SALT_FILE).write_bytes(os.urandom(16))
    try:
        status = secure_storage.enable_secure_storage(
            app_name="AutoYouTest",
            root=legacy_root,
            password="Synthetic-Legacy-Password!2026",
        )
        assert status["enabled"] is True
    finally:
        secure_storage.disable_secure_storage()


def test_vault_authenticator_credentials_roundtrip_without_keyring(tmp_path):
    vault = LocalMasterVault(tmp_path / "Native" / "local_vault.v2.enc")
    vault.create("Synthetic-Auth-Password!2026")
    creds = VaultAuthenticatorCredentials(
        tmp_path / "Native" / "authenticators.secrets.v2.enc",
        lambda: vault.cipher("authenticators"),
    )
    auth_vault = LocalAuthenticatorVault(tmp_path, credentials=creds)
    entry = auth_vault.save("JBSWY3DPEHPK3PXP", name="Synthetic Mac", issuer="AutoYou")
    assert len(auth_vault.list_entries()) == 1
    code_info = auth_vault.current_code(entry["id"])
    assert len(code_info["code"]) == 6 and code_info["code"].isdigit()
    # Secret must not appear in plaintext on disk.
    raw_file = (tmp_path / "Native" / "authenticators.secrets.v2.enc").read_bytes()
    assert b"JBSWY3DPEHPK3PXP" not in raw_file
    assert auth_vault.delete(entry["id"]) is True
    assert auth_vault.list_entries() == []
