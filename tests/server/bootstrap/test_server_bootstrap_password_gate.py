# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-3c5d23553df7f911667f867b


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
import os
import sys

import pytest
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-3c5d23553df7f911667f867b"


ensure_repo_on_path()

import server
from shared import first_run

_REAL_GET_SERVER_KEYSTORE = server._get_server_keystore


class _UnavailableKeystoreStore:
    def __init__(self, path):
        self.path = path

    def is_available(self):
        return False

    def exists(self):
        return self.path.exists()

    def load(self, *, operation_timeout_seconds=None):
        return None

    def save(self, payload):  # pragma: no cover - should never be called
        raise AssertionError("Unavailable keystore store should not be used for save()")


class _UnreadableKeystoreStore(_UnavailableKeystoreStore):
    def is_available(self):
        return True


class _ReadableKeystoreStore(_UnavailableKeystoreStore):
    def __init__(self, path, payload):
        super().__init__(path)
        self.payload = payload
        self.saved_payloads = []

    def is_available(self):
        return True

    def load(self, *, operation_timeout_seconds=None):
        return self.payload

    def save(self, payload):
        self.saved_payloads.append(payload)
        self.payload = payload
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"synthetic-keystore-config")


@pytest.fixture(autouse=True)
def _isolate_bootstrap_state(tmp_path, monkeypatch):
    original_config = server.STATE.config
    original_session = (
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )
    original_initialized = server.STATE.initialized_services_on_startup
    original_used_default = server.STATE.used_default_password
    original_decrypted_via_env = server.STATE.decrypted_via_env
    original_startup_status = dict(server.STATE.startup_status)
    original_sessions = dict(server.ADMIN_SESSIONS)
    test_config_dir = tmp_path / "AutoYou"
    test_config_dir.mkdir()

    monkeypatch.setattr(server, "CONFIG_FILE_PATH", str(tmp_path / "config.encrypted"))
    monkeypatch.setattr(server, "CONFIG_BAK_PATH", str(tmp_path / "config.encrypted.bak"))
    monkeypatch.setattr(server, "_CONFIG_DIR", test_config_dir)
    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", test_config_dir / "config.keystore.enc")
    monkeypatch.setattr(server, "_get_server_keystore", lambda: None)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: False)
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("AUTOYOU_SERVER_PASSWORD", raising=False)

    server.STATE.config = {}
    server._set_config_session(config_store=server.CONFIG_STORE_NONE)
    server.STATE.initialized_services_on_startup = False
    server.STATE.used_default_password = False
    server.STATE.decrypted_via_env = False
    server.ADMIN_SESSIONS.clear()

    yield

    server.STATE.config = original_config
    server._set_config_session(
        config_store=original_session[2],
        server_password=original_session[0],
        config_unlock_password=original_session[1],
    )
    server.STATE.initialized_services_on_startup = original_initialized
    server.STATE.used_default_password = original_used_default
    server.STATE.decrypted_via_env = original_decrypted_via_env
    server.STATE.startup_status = original_startup_status
    server.ADMIN_SESSIONS.clear()
    server.ADMIN_SESSIONS.update(original_sessions)


def _mark_startup_complete() -> None:
    server.STATE.initialized_services_on_startup = True
    server._update_startup_status(
        status="complete",
        headline="Initialization complete",
        detail="Services are ready.",
        step=8,
        total_steps=8,
    )


def _login_data(password: str) -> dict[str, str]:
    return {"password": password, "terms_accepted": "1"}


def test_product_runtime_keystore_namespace_is_separate_and_test_root_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOYOU_TEST_ROOT", raising=False)
    monkeypatch.delenv("AUTOYOU_RUNTIME_ROOT", raising=False)
    assert server._server_keystore_service_name() == server._KS_SERVICE_NAME
    monkeypatch.setenv("AUTOYOU_RUNTIME_ROOT", str(tmp_path / "v2"))
    runtime_service = server._server_keystore_service_name()
    assert runtime_service.startswith(f"{server._KS_SERVICE_NAME}-runtime-")
    assert runtime_service != server._KS_SERVICE_NAME
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "test"))
    assert server._server_keystore_service_name().startswith(f"{server._KS_SERVICE_NAME}-test-")
    assert server._server_keystore_service_name() != runtime_service


def test_non_maximus_config_clears_stale_secure_storage_environment(monkeypatch):
    server.disable_secure_storage()
    variables = (
        "AUTOYOU_SECURE_STORAGE_MODE",
        "AUTOYOU_SECURE_STORAGE_ROOT",
        "AUTOYOU_SECURE_STORAGE_APP",
        "AUTOYOU_SECURE_STORAGE_PASSWORD",
    )
    for variable in variables:
        monkeypatch.setenv(variable, "synthetic-live-state")

    status = server._configure_secure_storage_for_config({"security": {"mode": "secure"}})

    assert status["enabled"] is False
    assert all(variable not in os.environ for variable in variables)


def test_test_runtime_scopes_server_keystore_credentials(monkeypatch, tmp_path):
    class _FakeServerKeyring:
        def __init__(self):
            self.passwords = {}
            self.set_calls = []
            self.delete_calls = []

        def get_password(self, service, credential):
            return self.passwords.get((service, credential))

        def set_password(self, service, credential, value):
            self.set_calls.append((service, credential, value))
            self.passwords[(service, credential)] = value

        def delete_password(self, service, credential):
            self.delete_calls.append((service, credential))
            self.passwords.pop((service, credential), None)

    keystore_path = tmp_path / "runtime" / "AutoYou" / "config.keystore.enc"
    delete_key_calls = []
    fake_keyring = _FakeServerKeyring()

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    monkeypatch.setattr(server, "_get_server_keystore", _REAL_GET_SERVER_KEYSTORE)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: True)
    monkeypatch.setattr(server, "_server_keyring", fake_keyring)
    monkeypatch.setattr(server, "_ks_available", lambda: True)
    monkeypatch.setattr(
        server,
        "_ks_get_password",
        lambda service, credential, **_kwargs: fake_keyring.get_password(service, credential),
    )
    monkeypatch.setattr(
        server,
        "_ks_delete_key",
        lambda service, credential: delete_key_calls.append((service, credential)) or True,
    )

    scoped_service = server._server_keystore_service_name()
    assert scoped_service.startswith(f"{server._KS_SERVICE_NAME}-test-")
    assert scoped_service != server._KS_SERVICE_NAME

    ks = server._get_server_keystore()
    assert getattr(ks, "_service_name") == scoped_service

    server._persist_server_password("1234")
    assert fake_keyring.set_calls == [
        (scoped_service, server._KS_SERVER_PASSWORD_CRED_NAME, "1234")
    ]
    assert server._load_keystore_server_password() == "1234"

    server._clear_persisted_server_password()
    assert fake_keyring.delete_calls == [
        (scoped_service, server._KS_SERVER_PASSWORD_CRED_NAME)
    ]

    keystore_path.parent.mkdir(parents=True, exist_ok=True)
    keystore_path.write_bytes(b"placeholder")
    server._clear_main_keystore_config()
    assert not keystore_path.exists()
    assert delete_key_calls == [(scoped_service, server._KS_CRED_NAME)]


def test_persist_server_password_skips_redundant_keychain_replacement(monkeypatch):
    set_calls = []

    class _Keyring:
        def set_password(self, *args):
            set_calls.append(args)

    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: True)
    monkeypatch.setattr(server, "_server_keyring", _Keyring())
    monkeypatch.setattr(server, "_load_keystore_server_password", lambda: "same-secret")

    server._persist_server_password("same-secret")

    assert set_calls == []
    assert server.STATE.server_password == "same-secret"


@pytest.mark.asyncio
async def test_bootstrap_loads_default_password_config_without_initializing_services(monkeypatch):
    server.save_encrypted_config(server._default_config(), server.DEFAULT_SERVER_PASSWORD)

    init_calls = []

    async def fake_initialize():
        init_calls.append(True)

    monkeypatch.setattr(server, "_initialize_services_on_startup", fake_initialize)

    await server.bootstrap_password_and_config()

    assert init_calls == []
    assert server.STATE.config != {}
    assert server.STATE.config_store == server.CONFIG_STORE_ENCRYPTED
    assert server.STATE.initialized_services_on_startup is False
    assert server.STATE.used_default_password is True
    assert server.STATE.server_password == server.DEFAULT_SERVER_PASSWORD


@pytest.mark.asyncio
async def test_bootstrap_first_run_creates_default_config_without_initializing_services(monkeypatch):
    init_calls = []

    async def fake_initialize():
        init_calls.append(True)

    monkeypatch.setattr(server, "_initialize_services_on_startup", fake_initialize)

    await server.bootstrap_password_and_config()

    assert init_calls == []
    assert os.path.exists(server.CONFIG_FILE_PATH)
    assert server.STATE.config != {}
    assert server.STATE.config_store == server.CONFIG_STORE_ENCRYPTED
    assert server.STATE.server_password == server.DEFAULT_SERVER_PASSWORD
    assert server.STATE.initialized_services_on_startup is False
    assert server.STATE.used_default_password is True


@pytest.mark.asyncio
async def test_bootstrap_first_run_uses_env_password_when_provided(monkeypatch):
    monkeypatch.setenv("AUTOYOU_SERVER_PASSWORD", "1234")
    init_calls = []

    async def fake_initialize():
        init_calls.append(True)

    monkeypatch.setattr(server, "_initialize_services_on_startup", fake_initialize)

    await server.bootstrap_password_and_config()

    assert init_calls == []
    assert os.path.exists(server.CONFIG_FILE_PATH)
    assert server.STATE.config != {}
    assert server.STATE.config_store == server.CONFIG_STORE_ENCRYPTED
    assert server.STATE.server_password == "1234"
    assert server.STATE.initialized_services_on_startup is False
    assert server.STATE.used_default_password is False
    assert server.STATE.decrypted_via_env is True
    assert server.try_decrypt_config_with("1234") is not None
    assert server.try_decrypt_config_with(server.DEFAULT_SERVER_PASSWORD) is None


def test_macos_bootstrap_keystore_read_is_bounded(monkeypatch):
    captured = []

    class _Store:
        def load(self, *, operation_timeout_seconds=None):
            captured.append(operation_timeout_seconds)
            return {"server": {"name": "Synthetic Server"}}

    monkeypatch.setattr(server.sys, "platform", "darwin")
    monkeypatch.delenv(server._MACOS_KEYCHAIN_BOOTSTRAP_TIMEOUT_ENV, raising=False)

    assert server._load_keystore_config_during_bootstrap(_Store()) == {
        "server": {"name": "Synthetic Server"}
    }
    assert captured == [server._DEFAULT_MACOS_KEYCHAIN_BOOTSTRAP_TIMEOUT_SECONDS]


@pytest.mark.asyncio
async def test_macos_existing_keystore_bootstrap_is_read_only(monkeypatch, tmp_path):
    class _TrackingStore:
        def __init__(self, path, payload):
            self.path = path
            self.payload = payload
            self.load_calls = []
            self.save_calls = []

        def is_available(self):
            return True

        def exists(self):
            return True

        def load(self, *, operation_timeout_seconds=None):
            self.load_calls.append(operation_timeout_seconds)
            return self.payload

        def save(self, payload):
            self.save_calls.append(payload)
            raise AssertionError("automatic macOS bootstrap must not save the existing config")

    keystore_path = tmp_path / "config.keystore.enc"
    keystore_path.write_bytes(b"synthetic-keystore-config")
    config = server._build_initial_server_config()
    store = _TrackingStore(keystore_path, config)
    pairing_reads = []

    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    monkeypatch.setattr(server, "_get_server_keystore", lambda: store)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: True)
    monkeypatch.setattr(server, "_macos_keychain_bootstrap_timeout_seconds", lambda: 1.0)
    monkeypatch.setattr(
        server,
        "_ks_get_password",
        lambda service, credential, **kwargs: pairing_reads.append(
            (service, credential, kwargs.get("operation_timeout_seconds"))
        ) or "synthetic-password",
    )
    monkeypatch.setattr(
        server,
        "_persist_state_config",
        lambda *_args, **_kwargs: pytest.fail("automatic macOS bootstrap must not persist config"),
    )
    monkeypatch.setattr(
        server,
        "_persist_server_password",
        lambda *_args, **_kwargs: pytest.fail("automatic macOS bootstrap must not persist password"),
    )
    monkeypatch.setattr(
        server,
        "_clear_persisted_server_password",
        lambda *_args, **_kwargs: pytest.fail("automatic macOS bootstrap must not clear password"),
    )

    await server.bootstrap_password_and_config()

    assert store.load_calls == [1.0]
    assert pairing_reads == [
        (
            server._server_keystore_service_name(),
            server._KS_SERVER_PASSWORD_CRED_NAME,
            1.0,
        )
    ]
    assert store.save_calls == []
    assert server.STATE.config == config
    assert server.STATE.config_store == server.CONFIG_STORE_KEYSTORE
    assert server.STATE._unlock_state_mem == "Ready"


@pytest.mark.asyncio
async def test_macos_bootstrap_locks_when_existing_maximus_key_is_unavailable(monkeypatch):
    config = server._default_config()
    config.setdefault("security", {})["mode"] = server.SECURE_PROFESSIONAL_MAXIMUS_MODE
    server.save_encrypted_config(config, "synthetic-password")
    monkeypatch.setattr(server.sys, "platform", "darwin")
    monkeypatch.setenv("AUTOYOU_SERVER_PASSWORD", "synthetic-password")
    monkeypatch.setattr(
        server,
        "_persist_state_config",
        lambda *_args, **kwargs: (_ for _ in ()).throw(
            server.SecureStorageError("synthetic keychain denial")
        ),
    )

    await server.bootstrap_password_and_config()

    assert server.STATE.config == {}
    assert server.STATE.config_store == server.CONFIG_STORE_NONE
    assert server.STATE._unlock_state_mem == "Locked"


@pytest.mark.asyncio
async def test_bootstrap_preserves_unreadable_keystore_only_config(monkeypatch, tmp_path):
    keystore_path = tmp_path / "config.keystore.enc"
    keystore_path.write_bytes(b"placeholder")

    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    monkeypatch.setattr(server, "_get_server_keystore", lambda: _UnreadableKeystoreStore(keystore_path))

    await server.bootstrap_password_and_config()

    assert keystore_path.exists()
    assert not os.path.exists(server.CONFIG_FILE_PATH)
    assert server.STATE.config == {}
    assert server.STATE.config_store == server.CONFIG_STORE_NONE
    assert server.STATE.server_password is None


def test_saved_config_exists_detects_keystore_file_without_backend(monkeypatch, tmp_path):
    keystore_path = tmp_path / "config.keystore.enc"
    keystore_path.write_bytes(b"placeholder")

    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    monkeypatch.setattr(server, "_get_server_keystore", lambda: None)

    assert server._saved_config_exists() is True


def test_admin_login_creates_initial_encrypted_config_on_first_run(monkeypatch):
    init_calls = []

    async def fake_initialize():
        init_calls.append(True)
        _mark_startup_complete()

    monkeypatch.setattr(server, "_initialize_services_on_startup", fake_initialize)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/login",
            data=_login_data("secret123"),
            headers={"X-AutoYou-Async": "1", "Accept": "application/json"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert init_calls == [True]
    assert os.path.exists(server.CONFIG_FILE_PATH)
    assert server.STATE.config_store == server.CONFIG_STORE_ENCRYPTED
    assert server.STATE.server_password == "secret123"
    ack = first_run.read_license_acknowledgement()
    assert ack is not None
    assert ack["accepted_by"] == "admin_login_first_run"

    decrypted_cfg = server.try_decrypt_config_with("secret123")
    assert decrypted_cfg is not None
    assert server.STATE.config == decrypted_cfg


def test_admin_login_still_allows_manual_unlock_of_legacy_default_password_config(monkeypatch):
    server.save_encrypted_config(server._default_config(), server.DEFAULT_SERVER_PASSWORD)

    init_calls = []

    async def fake_initialize():
        init_calls.append(True)
        _mark_startup_complete()

    monkeypatch.setattr(server, "_initialize_services_on_startup", fake_initialize)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/login",
            data=_login_data(server.DEFAULT_SERVER_PASSWORD),
            headers={"X-AutoYou-Async": "1", "Accept": "application/json"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert init_calls == [True]
    assert server.STATE.used_default_password is True
    assert server.STATE.server_password == server.DEFAULT_SERVER_PASSWORD


def test_admin_login_rotates_default_password_config_with_new_password(monkeypatch):
    server.save_encrypted_config(server._default_config(), server.DEFAULT_SERVER_PASSWORD)
    server.STATE.used_default_password = True
    new_password = "synthetic-rotated-password-47"

    init_calls = []

    async def fake_initialize():
        init_calls.append(True)
        _mark_startup_complete()

    monkeypatch.setattr(server, "_initialize_services_on_startup", fake_initialize)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/login",
            data=_login_data(new_password),
            headers={"X-AutoYou-Async": "1", "Accept": "application/json"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert init_calls == [True]
    assert server.STATE.used_default_password is False
    assert server.STATE.server_password == new_password
    assert server.try_decrypt_config_with(new_password) is not None
    assert server.try_decrypt_config_with(server.DEFAULT_SERVER_PASSWORD) is None


def test_login_page_exposes_password_generator_when_default_password_is_active():
    server.save_encrypted_config(server._default_config(), server.DEFAULT_SERVER_PASSWORD)
    server.STATE.used_default_password = True

    with TestClient(server.admin_app) as client:
        response = client.get("/login")

    assert response.status_code == 200
    assert "Generate random password" in response.text
    assert "toggle-password" in response.text
    assert "Secure Cloud Pair works with the same paid account" in response.text
    assert "Public links require a custom password" in response.text


def test_login_page_stays_available_when_maximus_acknowledgement_is_locked():
    """Regression for a sealed acknowledgement turning every /login request into 500."""
    server.save_encrypted_config(server._default_config(), server.DEFAULT_SERVER_PASSWORD)
    path = first_run.license_ack_path(anchor=server.__file__)
    path.write_bytes(first_run.SECURE_FILE_HEADER + b"synthetic-locked-ciphertext")
    original = path.read_bytes()

    with TestClient(server.admin_app) as client:
        response = client.get("/login")

    assert response.status_code == 200
    assert "name='terms_accepted'" not in response.text
    assert path.read_bytes() == original


def test_locked_status_does_not_open_sealed_maximus_unlock_metadata(monkeypatch, tmp_path):
    """Status polling must not touch protected storage before recovery unlocks it."""
    unlock_path = tmp_path / "server_unlock.json"
    unlock_path.write_bytes(server.SPM_FILE_HEADER + b"synthetic-locked-ciphertext")
    protected_reads = []

    monkeypatch.setattr(server, "_get_unlock_file_path", lambda: unlock_path)
    monkeypatch.setattr(server, "secure_storage_enabled", lambda: False)
    monkeypatch.setattr(server.STATE, "_unlock_state_mem", "Locked", raising=False)
    monkeypatch.setattr(
        server,
        "load_secure_json",
        lambda *_args, **_kwargs: protected_reads.append(True) or pytest.fail("locked status opened protected metadata"),
    )

    with TestClient(server.admin_app) as client:
        response = client.get("/v1/unlock/status")

    assert response.status_code == 200
    assert response.json()["state"] == "Locked"
    assert response.json()["terms_accepted"] is False
    assert response.json()["agreement_required"] is True
    assert response.json()["agreement_pending_unlock"] is False
    assert protected_reads == []


def test_locked_status_defers_a_sealed_existing_agreement(monkeypatch):
    server.save_encrypted_config(server._default_config(), "synthetic-password")
    path = first_run.license_ack_path(anchor=server.__file__)
    path.write_bytes(first_run.SECURE_FILE_HEADER + b"synthetic-locked-ciphertext")
    if hasattr(server.STATE, "_unlock_state_mem"):
        delattr(server.STATE, "_unlock_state_mem")

    with TestClient(server.admin_app) as client:
        response = client.get("/v1/unlock/status")

    assert response.status_code == 200
    assert response.json()["state"] == "Locked"
    assert response.json()["agreement_required"] is False
    assert response.json()["agreement_pending_unlock"] is True


def test_native_unlock_allows_a_sealed_existing_agreement(monkeypatch):
    """A sealed acknowledgement must not block the foreground Keychain unlock action."""
    server.save_encrypted_config(server._default_config(), "synthetic-password")
    path = first_run.license_ack_path(anchor=server.__file__)
    path.write_bytes(first_run.SECURE_FILE_HEADER + b"synthetic-locked-ciphertext")
    captured = []

    async def fake_admin_login(_request, *, password):
        captured.append(password)
        return server.JSONResponse({"success": True})

    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: True)
    monkeypatch.setattr(server, "_load_keystore_server_password", lambda: "stored-password")
    monkeypatch.setattr(server, "admin_login", fake_admin_login)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/login/native-unlock",
            headers={
                "X-AutoYou-Async": "1",
                "Accept": "application/json",
                "Origin": "http://testserver",
            },
        )

    assert response.status_code == 200
    assert captured == ["stored-password"]


def test_unlock_verify_does_not_write_metadata_before_maximus_storage_is_ready(monkeypatch):
    config = server._default_config()
    config.setdefault("security", {})["mode"] = server.SECURE_PROFESSIONAL_MAXIMUS_MODE
    server.save_encrypted_config(config, "synthetic-password")
    path = first_run.license_ack_path(anchor=server.__file__)
    path.write_bytes(first_run.SECURE_FILE_HEADER + b"synthetic-locked-ciphertext")
    original = path.read_bytes()
    acknowledgements = []

    def storage_unavailable(*_args, **_kwargs):
        raise server.SecureStorageError("synthetic credential unavailable")

    monkeypatch.setattr(server, "_configure_secure_storage_for_config", storage_unavailable)
    monkeypatch.setattr(
        server,
        "record_license_acknowledgement",
        lambda **kwargs: acknowledgements.append(kwargs),
    )

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/v1/unlock/verify",
            json={"password": "synthetic-password", "terms_accepted": True},
        )

    assert response.status_code == 503
    assert acknowledgements == []
    assert path.read_bytes() == original


def test_login_page_exposes_native_unlock_and_reset_controls_when_keystore_is_available(monkeypatch, tmp_path):
    keystore_path = tmp_path / "config.keystore.enc"
    keystore_path.write_bytes(b"synthetic-keystore-config")
    store = _ReadableKeystoreStore(keystore_path, server._build_initial_server_config())
    first_run.record_license_acknowledgement()

    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    monkeypatch.setattr(server, "_get_server_keystore", lambda: store)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: True)

    with TestClient(server.admin_app) as client:
        response = client.get("/login")

    assert response.status_code == 200
    assert "Unlock with" in response.text
    assert "/api/login/native-unlock" in response.text
    assert "Start over on this computer" in response.text
    assert "/api/login/factory-reset" in response.text


def test_login_page_hides_native_unlock_when_disabled(monkeypatch, tmp_path):
    keystore_path = tmp_path / "config.keystore.enc"
    keystore_path.write_bytes(b"synthetic-keystore-config")
    cfg = server._build_initial_server_config()
    cfg["security"]["native_unlock_enabled"] = False
    store = _ReadableKeystoreStore(keystore_path, cfg)
    first_run.record_license_acknowledgement()

    server.STATE.config = cfg
    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    monkeypatch.setattr(server, "_get_server_keystore", lambda: store)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: True)

    with TestClient(server.admin_app) as client:
        response = client.get("/login")

    assert response.status_code == 200
    assert "id='native-unlock'" not in response.text
    assert "Start over on this computer" in response.text


def test_native_keystore_unlock_uses_saved_password(monkeypatch, tmp_path):
    keystore_path = tmp_path / "config.keystore.enc"
    keystore_path.write_bytes(b"synthetic-keystore-config")
    store = _ReadableKeystoreStore(keystore_path, server._build_initial_server_config())
    # from __debug_provenance_f__ import tenpercent
    init_calls = []
    first_run.record_license_acknowledgement()

    async def fake_initialize():
        init_calls.append(True)
        _mark_startup_complete()

    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    monkeypatch.setattr(server, "_get_server_keystore", lambda: store)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: True)
    monkeypatch.setattr(server, "_load_keystore_server_password", lambda: "stored-secret")
    monkeypatch.setattr(server, "_persist_server_password", lambda _password: None)
    monkeypatch.setattr(server, "_initialize_services_on_startup", fake_initialize)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/login/native-unlock",
            headers={
                "X-AutoYou-Async": "1",
                "Accept": "application/json",
                "Origin": "http://testserver",
            },
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert init_calls == [True]
    assert server.STATE.config_store == server.CONFIG_STORE_KEYSTORE
    assert server.STATE.server_password == "stored-secret"
    assert "admin_session" in response.cookies


def test_native_keystore_unlock_returns_forbidden_when_disabled(monkeypatch, tmp_path):
    keystore_path = tmp_path / "config.keystore.enc"
    keystore_path.write_bytes(b"synthetic-keystore-config")
    cfg = server._build_initial_server_config()
    cfg["security"]["native_unlock_enabled"] = False
    store = _ReadableKeystoreStore(keystore_path, cfg)
    first_run.record_license_acknowledgement()

    server.STATE.config = cfg
    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    monkeypatch.setattr(server, "_get_server_keystore", lambda: store)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: True)
    monkeypatch.setattr(server, "_load_keystore_server_password", lambda: pytest.fail("disabled native unlock should not read the saved password"))

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/login/native-unlock",
            headers={
                "X-AutoYou-Async": "1",
                "Accept": "application/json",
                "Origin": "http://testserver",
            },
        )

    assert response.status_code == 403
    assert response.json()["error_message"] == "System credential unlock is disabled for this AutoYou server."


def test_native_keystore_unlock_requires_current_terms(monkeypatch, tmp_path):
    keystore_path = tmp_path / "config.keystore.enc"
    keystore_path.write_bytes(b"synthetic-keystore-config")
    store = _ReadableKeystoreStore(keystore_path, server._build_initial_server_config())
    first_run.record_license_acknowledgement(agreement_version="old")

    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    monkeypatch.setattr(server, "_get_server_keystore", lambda: store)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: True)
    monkeypatch.setattr(server, "_load_keystore_server_password", lambda: pytest.fail("native password should stay unused"))

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/login/native-unlock",
            headers={
                "X-AutoYou-Async": "1",
                "Accept": "application/json",
                "Origin": "http://testserver",
            },
        )

    assert response.status_code == 428
    body = response.json()
    assert body["agreement_required"] is True
    assert body["current_agreement_version"] == first_run.CURRENT_AGREEMENT_VERSION


def test_admin_config_patch_persists_next_boot_access_and_native_unlock_flag():
    updated, touched, _theme = server._apply_admin_ui_config_patch(
        server._build_initial_server_config(),
        {
            "server": {"bind_host": "0.0.0.0"},
            "security": {"native_unlock_enabled": False},
        },
    )

    assert updated["server"]["bind_host"] == "0.0.0.0"
    assert updated["security"]["native_unlock_enabled"] is False
    assert {"server", "security"} <= touched


def test_page_service_start_skips_busy_port(monkeypatch):
    server.STATE.config = {"autoyou_page": {"port": 8067, "timeline_days": 7}}
    monkeypatch.setattr(server, "AUTOYOU_PAGE_SERVICE_AVAILABLE", True)
    monkeypatch.setattr(server, "is_port_in_use", lambda port, host="127.0.0.1": True)

    async def fake_page_running():
        return False

    async def fail_start(*_args, **_kwargs):
        raise AssertionError("busy page service port should not bind")

    monkeypatch.setattr(server, "is_autoyou_page_service_running", fake_page_running)
    monkeypatch.setattr(server, "start_autoyou_page_service", fail_start)

    assert asyncio.run(server.start_autoyou_page_service_background()) is None


def test_owned_page_port_is_used_for_startup_and_browser_routes(monkeypatch):
    config = {"autoyou_page": {"port": 8067, "timeline_days": 7}}
    server.STATE.config = config
    monkeypatch.setenv("AUTOYOU_PAGE_PORT", "18067")
    monkeypatch.setattr(server, "AUTOYOU_PAGE_SERVICE_AVAILABLE", True)
    checked, started = [], []
    monkeypatch.setattr(server, "is_port_in_use", lambda port, **_kw: checked.append(port) or False)

    async def running():
        return False

    async def start(**kwargs):
        started.append(kwargs["port"])
        return "synthetic-service"

    monkeypatch.setattr(server, "is_autoyou_page_service_running", running)
    monkeypatch.setattr(server, "start_autoyou_page_service", start)
    assert asyncio.run(server.start_autoyou_page_service_background()) == "synthetic-service"
    assert checked == started == [18067]
    assert server._get_autoyou_browser_forward_port() == 18067
    assert server._get_autoyou_page_service_base_url() == "http://127.0.0.1:18067"
    assert config["autoyou_page"]["port"] == 8067
    for invalid in ("0", "65536", "not-a-port"):
        monkeypatch.setenv("AUTOYOU_PAGE_PORT", invalid)
        with pytest.raises(ValueError):
            server._get_autoyou_page_service_port()
    monkeypatch.delenv("AUTOYOU_PAGE_PORT")
    assert server._get_autoyou_page_service_port() == 8067


def test_encrypted_config_save_persists_password_to_system_keystore_when_available(monkeypatch):
    persisted_passwords = []

    monkeypatch.setattr(server, "_clear_main_keystore_config", lambda: None)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: True)
    monkeypatch.setattr(server, "_persist_server_password", lambda password: persisted_passwords.append(password))

    server._persist_state_config(
        server._build_initial_server_config(),
        server_password="stored-even-for-encrypted",
        preferred_store=server.CONFIG_STORE_ENCRYPTED,
    )

    assert persisted_passwords == ["stored-even-for-encrypted"]
    assert server.STATE.config_store == server.CONFIG_STORE_ENCRYPTED
    assert server.STATE.server_password == "stored-even-for-encrypted"
    assert server.try_decrypt_config_with("stored-even-for-encrypted") is not None


def test_login_factory_reset_clears_test_runtime_and_schedules_shutdown(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    app_dir = runtime_root / "AutoYou"
    config_dir = app_dir / "config"
    config_dir.mkdir(parents=True)
    (app_dir / "config.encrypted").write_text("synthetic encrypted config", encoding="utf-8")
    (app_dir / "config.keystore.enc").write_text("synthetic keystore config", encoding="utf-8")
    (app_dir / "sessions.db").write_text("synthetic sessions", encoding="utf-8")
    (app_dir / "agent_install_registry.json").write_text("{}", encoding="utf-8")
    unlock_path = config_dir / "server_unlock.json"
    unlock_path.write_text("{}", encoding="utf-8")
    schedule_calls = []
    cleared_credentials = []

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(runtime_root))
    monkeypatch.setattr(server, "_CONFIG_DIR", app_dir)
    monkeypatch.setattr(server, "CONFIG_FILE_PATH", str(app_dir / "config.encrypted"))
    monkeypatch.setattr(server, "CONFIG_BAK_PATH", str(app_dir / "config.encrypted.bak"))
    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", app_dir / "config.keystore.enc")
    monkeypatch.setattr(server, "_get_unlock_file_path", lambda: unlock_path)
    monkeypatch.setattr(server, "_schedule_factory_reset_shutdown", lambda: schedule_calls.append(True))
    monkeypatch.setattr(server, "_clear_persisted_server_password", lambda: cleared_credentials.append("password"))
    monkeypatch.setattr(server, "_clear_main_keystore_config", lambda: cleared_credentials.append("config-key"))

    server.STATE.config = server._build_initial_server_config()
    server._set_config_session(
        config_store=server.CONFIG_STORE_ENCRYPTED,
        server_password="stored-secret",
        config_unlock_password="stored-secret",
    )
    server.ADMIN_SESSIONS["synthetic-session"] = True

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/api/login/factory-reset",
            json={"confirmation": "RESET"},
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert schedule_calls == [True]
    assert cleared_credentials == ["password", "config-key"]
    assert not (app_dir / "config.encrypted").exists()
    assert not (app_dir / "config.keystore.enc").exists()
    assert not (app_dir / "sessions.db").exists()
    assert server.ADMIN_SESSIONS == {}
    assert server.STATE.config == {}
    assert server.STATE.config_store == server.CONFIG_STORE_NONE


def test_admin_login_preserves_keystore_only_config_when_backend_is_unavailable(monkeypatch, tmp_path):
    keystore_path = tmp_path / "config.keystore.enc"
    keystore_path.write_bytes(b"placeholder")

    init_calls = []

    async def fake_initialize():
        init_calls.append(True)
        _mark_startup_complete()

    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    # Isolate the encrypted-config path too, so a config.encrypted left at the
    # shared session data dir by another test can't make these assertions flaky.
    monkeypatch.setattr(server, "CONFIG_FILE_PATH", str(tmp_path / "config.encrypted"))
    monkeypatch.setattr(server, "_get_server_keystore", lambda: _UnavailableKeystoreStore(keystore_path))
    monkeypatch.setattr(server, "_initialize_services_on_startup", fake_initialize)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/login",
            data=_login_data("secret123"),
            headers={"X-AutoYou-Async": "1", "Accept": "application/json"},
        )

    assert response.status_code == 503
    assert response.json()["success"] is False
    assert "left unchanged" in response.json()["error_message"]
    assert init_calls == []
    assert keystore_path.exists()
    assert not os.path.exists(server.CONFIG_FILE_PATH)
    assert server.STATE.config == {}
    assert server.STATE.config_store == server.CONFIG_STORE_NONE


def test_admin_login_preserves_unreadable_keystore_only_config_when_backend_is_available(monkeypatch, tmp_path):
    keystore_path = tmp_path / "config.keystore.enc"
    keystore_path.write_bytes(b"placeholder")

    init_calls = []

    async def fake_initialize():
        init_calls.append(True)
        _mark_startup_complete()

    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    # Isolate the encrypted-config path too (see sibling test) for deterministic
    # behavior regardless of config files other tests may leave in the data dir.
    monkeypatch.setattr(server, "CONFIG_FILE_PATH", str(tmp_path / "config.encrypted"))
    monkeypatch.setattr(server, "_get_server_keystore", lambda: _UnreadableKeystoreStore(keystore_path))
    monkeypatch.setattr(server, "_initialize_services_on_startup", fake_initialize)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/login",
            data=_login_data("1234"),
            headers={"X-AutoYou-Async": "1", "Accept": "application/json"},
        )

    assert response.status_code == 503
    assert response.json()["success"] is False
    assert "left unchanged" in response.json()["error_message"]
    assert init_calls == []
    assert keystore_path.exists()
    assert not os.path.exists(server.CONFIG_FILE_PATH)
    assert server.STATE.config == {}
    assert server.STATE.config_store == server.CONFIG_STORE_NONE


def test_wizard_completion_persists_to_config_and_disables_first_run_launcher():
    server.STATE.config = server._default_config()
    server._set_config_session(
        config_store=server.CONFIG_STORE_ENCRYPTED,
        server_password="secret123",
        config_unlock_password="secret123",
    )
    server.ADMIN_SESSIONS["test-admin-session"] = True

    with TestClient(server.admin_app) as client:
        client.cookies.set("admin_session", "test-admin-session")
        response = client.post(
            "/api/wizard/complete",
            json={"completed": True},
            headers={
                "Origin": "http://testserver",
                "Referer": "http://testserver/",
            },
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["wizard_completed"] is True

    decrypted_cfg = server.try_decrypt_config_with("secret123")
    assert decrypted_cfg is not None
    assert decrypted_cfg["onboarding"]["wizard_completed"] is True
    assert decrypted_cfg["onboarding"]["wizard_completed_at"]

    assert (
        server._should_show_onboarding_wizard(
            bot_status="Not configured",
            signal_status="Not configured",
            whatsapp_status="Not configured",
            runtime_status={},
            query_params={},
        )
        is False
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://127.0.0.1:8001",
        "http://[::1]:8080",
        "https://demo-pair.tunnelmole.net",
    ],
)
def test_auth_app_cors_allows_loopback_and_tunnelmole_origins(origin):
    with TestClient(server.auth_app) as client:
        response = client.options(
            "/auth",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
    assert "POST" in response.headers.get("access-control-allow-methods", "")


def test_auth_app_cors_rejects_non_loopback_non_tunnelmole_origins():
    with TestClient(server.auth_app) as client:
        response = client.options(
            "/auth",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )

    assert response.status_code == 400
    assert response.headers.get("access-control-allow-origin") is None


def test_csrf_allows_packaged_docker_local_autopair_without_origin(monkeypatch):
    monkeypatch.setenv("AUTOYOU_PACKAGED_RUNTIME", "1")

    assert server._csrf_allows_packaged_desktop_autopair_without_origin(
        "/api/autopair",
        "127.0.0.1:8001",
        "172.20.0.1",
    )


def test_csrf_docker_local_autopair_exception_stays_narrow(monkeypatch):
    monkeypatch.setenv("AUTOYOU_PACKAGED_RUNTIME", "1")

    assert not server._csrf_allows_packaged_desktop_autopair_without_origin(
        "/api/wizard/complete",
        "127.0.0.1:8001",
        "172.20.0.1",
    )
    assert not server._csrf_allows_packaged_desktop_autopair_without_origin(
        "/api/autopair",
        "example.com",
        "172.20.0.1",
    )

    monkeypatch.delenv("AUTOYOU_PACKAGED_RUNTIME", raising=False)
    assert not server._csrf_allows_packaged_desktop_autopair_without_origin(
        "/api/autopair",
        "127.0.0.1:8001",
        "172.20.0.1",
    )


def test_auth_app_rejects_malformed_host_header():
    with TestClient(server.auth_app) as client:
        response = client.get("/auth", headers={"Host": "localhost/login"})

    assert response.status_code == 400
    assert response.text == "Invalid Host header."
