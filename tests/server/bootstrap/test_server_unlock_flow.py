# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-2c5ef3cb151a820eb464bb4a


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import os
import sys
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-2c5ef3cb151a820eb464bb4a"


ensure_repo_on_path()

import server
from shared import first_run

@pytest.fixture(autouse=True)
def setup_test_unlock(tmp_path, monkeypatch):
    # Disable CSRF origin checks for testclient
    monkeypatch.setenv("AUTOYOU_DISABLE_ORIGIN_CSRF", "1")
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    
    # Reset memory unlock state
    if hasattr(server.STATE, "_unlock_state_mem"):
        delattr(server.STATE, "_unlock_state_mem")
        
    # Redirect unlock file path to tmp_path
    monkeypatch.setattr(server, "_get_unlock_file_path", lambda: tmp_path / "server_unlock.json")
    
    # Mock dynamic service startup so it doesn't trigger real network loops
    async def mock_init_services():
        pass
    monkeypatch.setattr(server, "_initialize_services_on_startup", mock_init_services)


def test_unlock_status_initially_setup_pending():
    client = TestClient(server.admin_app)
    
    # Check status
    response = client.get("/v1/unlock/status")
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "SetupPending"
    assert data["terms_accepted"] is False
    assert data["license_type"] is None
    assert data["agreement_version"] is None
    
    # Verify that standard APIs are blocked with 451
    response = client.get("/api/status")
    assert response.status_code == 451
    assert "Setup pending" in response.json()["error"]
    assert "Terms of Use (EULA), License, responsibility terms, warranty disclaimer, and liability limits" in response.json()["error"]


@pytest.mark.parametrize("route", ["/login", "/v1/unlock/verify"])
def test_password_endpoints_reject_plain_http_from_non_loopback(monkeypatch, route):
    monkeypatch.setattr(server, "_is_loopback_client_host", lambda _host: False)

    with TestClient(server.admin_app) as client:
        if route == "/login":
            page_response = client.get(route)
            assert page_response.status_code == 403
        if route == "/login":
            response = client.post(route, data={"password": "synthetic", "terms_accepted": "1"})
        else:
            response = client.post(route, json={"password": "synthetic", "terms_accepted": True})
    assert response.status_code == 403

    with TestClient(server.admin_app, base_url="https://remote.example") as client:
        if route == "/login":
            page_response = client.get(route)
            assert page_response.status_code == 200
        if route == "/login":
            response = client.post(route, data={"password": "", "terms_accepted": "1"})
        else:
            response = client.post(route, json={"password": "synthetic", "terms_accepted": True})
    assert response.status_code != 403


def test_local_pair_login_allows_explicit_private_http_request(monkeypatch):
    monkeypatch.setattr(server, "_is_loopback_client_host", lambda _host: False)
    monkeypatch.setattr(server, "_csrf_peer_is_private_or_loopback", lambda _host: True)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/login",
            data={"password": "", "terms_accepted": "1"},
            headers={"X-AutoYou-Local-Pair": "1"},
        )

    assert response.status_code != 403


def test_local_pair_helper_allows_private_http_without_exposing_login(monkeypatch):
    monkeypatch.setattr(server, "_is_loopback_client_host", lambda _host: False)
    monkeypatch.setattr(server, "_csrf_peer_is_private_or_loopback", lambda _host: True)

    with TestClient(server.admin_app) as client:
        response = client.get("/api/local-pair-helper")
        login_response = client.get("/login")

    assert response.status_code == 200
    assert 'name="autoyou-local-pair-helper"' in response.text
    assert response.headers["cache-control"] == "no-store"
    assert login_response.status_code == 403


def test_local_pair_helper_rejects_public_plain_http(monkeypatch):
    monkeypatch.setattr(server, "_is_loopback_client_host", lambda _host: False)
    monkeypatch.setattr(server, "_csrf_peer_is_private_or_loopback", lambda _host: False)

    with TestClient(server.admin_app) as client:
        response = client.get("/api/local-pair-helper")

    assert response.status_code == 403


def test_admin_login_page_requires_first_run_terms_acceptance():
    client = TestClient(server.admin_app)

    response = client.get("/login")

    assert response.status_code == 200
    assert "name='terms_accepted'" in response.text
    assert "#ayu-login-root .ayu-legal-acceptance input[type='checkbox']" in response.text
    assert "appearance:auto" in response.text
    assert "width:18px" in response.text
    assert "height:18px" in response.text
    assert "min-height:18px" in response.text
    assert "max-height:18px" in response.text
    assert "padding:0" in response.text
    assert "I accept the current Terms of Use (EULA), License, responsibility terms" in response.text
    assert "href='https://www.autoyou.me/terms/'" in response.text
    assert "href='https://www.autoyou.me/privacy/'" in response.text
    assert "href='/NOTICE.txt'" in response.text
    assert "href='/THIRD-PARTY-NOTICES.md'" in response.text
    assert "href='/sbom.cdx.json'" in response.text


def test_admin_legal_routes_serve_packaged_notice_bundle(tmp_path, monkeypatch):
    bundle_root = tmp_path / "packaged"
    legal_dir = bundle_root / "Legal"
    # from __debug_provenance_g__ import annual
    legal_dir.mkdir(parents=True)
    expected = {
        "/LICENSE": "synthetic license terms",
        "/NOTICE.txt": "synthetic notice",
        "/THIRD-PARTY-NOTICES.md": "synthetic third-party notices",
        "/sbom.cdx.json": '{"bomFormat":"CycloneDX"}',
    }
    for route, text in expected.items():
        (legal_dir / Path(route).name).write_text(text, encoding="utf-8")
    monkeypatch.setenv("AUTOYOU_PACKAGED_RESOURCES_ROOT", str(bundle_root))

    with TestClient(server.admin_app) as admin_client, TestClient(server.auth_app) as auth_client:
        for route, text in expected.items():
            admin_response = admin_client.get(route)
            auth_response = auth_client.get(route)

            assert admin_response.status_code == 200
            assert auth_response.status_code == 200
            assert text in admin_response.text
            assert text in auth_response.text


def test_unlock_setup_validation():
    client = TestClient(server.admin_app)
    
    # Missing password
    response = client.post("/v1/unlock/setup", json={
        "terms_accepted": True,
        "license_type": "individual"
    }, headers={"origin": "http://localhost:8001"})
    assert response.status_code == 400
    assert "Password is required" in response.json()["error"]
    
    # Terms not accepted
    response = client.post("/v1/unlock/setup", json={
        "password": "mypassword",
        "terms_accepted": False,
        "license_type": "individual"
    }, headers={"origin": "http://localhost:8001"})
    assert response.status_code == 400
    assert "You must accept the current AutoYou Terms of Use (EULA), License, responsibility terms, warranty disclaimer, and liability limits" in response.json()["error"]
    
    # Invalid license type
    response = client.post("/v1/unlock/setup", json={
        "password": "mypassword",
        "terms_accepted": True,
        "license_type": "invalid"
    }, headers={"origin": "http://localhost:8001"})
    assert response.status_code == 400
    assert "license_type must be" in response.json()["error"]


def test_unlock_state_is_locked_when_saved_config_exists_without_unlock_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", tmp_path / "config.keystore.enc")
    monkeypatch.setattr(server, "CONFIG_FILE_PATH", str(tmp_path / "config.encrypted"))
    server.CONFIG_KEYSTORE_PATH.write_bytes(b"synthetic-keystore-marker")

    client = TestClient(server.admin_app)

    status = client.get("/v1/unlock/status")
    assert status.status_code == 200
    assert status.json()["state"] == "Locked"
    assert status.json()["terms_accepted"] is False

    api_response = client.get("/api/status")
    assert api_response.status_code == 401
    assert "Server is locked" in api_response.json()["error"]

    setup_response = client.post(
        "/v1/unlock/setup",
        json={
            "password": "newpassword",
            "terms_accepted": True,
            "license_type": "individual",
        },
        headers={"origin": "http://localhost:8001"},
    )
    assert setup_response.status_code == 400
    assert "Setup is already completed" in setup_response.json()["error"]


def test_unlock_verify_self_heals_legacy_login_password_without_unlock_metadata(tmp_path, monkeypatch):
    # Older /login installs may not have salt_hex/hash_hex metadata. Keep the
    # migration coverage by removing the metadata that current /login writes.
    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", tmp_path / "config.keystore.enc")
    monkeypatch.setattr(server, "CONFIG_FILE_PATH", str(tmp_path / "config.encrypted"))
    monkeypatch.setattr(server, "CONFIG_BAK_PATH", str(tmp_path / "config.encrypted.bak"))

    password = "legacy-login-password-47"

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/login",
            data={"password": password, "terms_accepted": "1"},
            headers={"X-AutoYou-Async": "1", "Accept": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    unlock_file = server._get_unlock_file_path()
    assert unlock_file.exists()
    unlock_file.unlink()

    # Simulate a fresh process restart: in-memory state is gone, the
    # encrypted config file remains.
    if hasattr(server.STATE, "_unlock_state_mem"):
        delattr(server.STATE, "_unlock_state_mem")
    server.STATE.config = {}
    server._set_config_session(config_store=server.CONFIG_STORE_NONE)

    with TestClient(server.admin_app) as client:
        status = client.get("/v1/unlock/status")
        assert status.json()["state"] == "Locked"

        wrong = client.post(
            "/v1/unlock/verify",
            json={"password": "not-it"},
            headers={"origin": "http://localhost:8001"},
        )
        assert wrong.status_code == 401
        assert not unlock_file.exists()

        response = client.post(
            "/v1/unlock/verify",
            json={"password": password, "terms_accepted": True},
            headers={"origin": "http://localhost:8001"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["state"] == "Ready"

    # Metadata is now self-healed so the fast local hash check works next time.
    assert unlock_file.exists()
    healed_meta = json.loads(unlock_file.read_text(encoding="utf-8"))
    assert server._verify_password(password, healed_meta["salt_hex"], healed_meta["hash_hex"])


def test_unlock_verify_loads_keystore_only_config_without_legacy_file(tmp_path, monkeypatch):
    password = "keystore-only-password-52"
    config = server._build_initial_server_config()
    keystore_path = tmp_path / "config.keystore.enc"
    keystore_path.write_bytes(b"synthetic-keystore-config")

    class _KeystoreOnlyStore:
        def is_available(self):
            return True

        def exists(self):
            return True

        def load(self):
            return config

    monkeypatch.setattr(server, "CONFIG_KEYSTORE_PATH", keystore_path)
    monkeypatch.setattr(server, "CONFIG_FILE_PATH", str(tmp_path / "config.encrypted"))
    monkeypatch.setattr(server, "_get_server_keystore", lambda: _KeystoreOnlyStore())
    monkeypatch.setattr(server, "_load_keystore_server_password", lambda: password)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: False)
    server.STATE.config = {}
    server._set_config_session(config_store=server.CONFIG_STORE_NONE)

    with TestClient(server.admin_app) as client:
        response = client.post(
            "/v1/unlock/verify",
            json={"password": password, "terms_accepted": True},
            headers={"origin": "http://localhost:8001"},
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert server.STATE.config_store == server.CONFIG_STORE_KEYSTORE
        assert server.STATE.config == config

        status = client.get("/v1/unlock/status")
        assert status.json()["terms_accepted"] is True
        assert status.json()["agreement_required"] is False


def test_unlock_setup_and_verify_flow(tmp_path):
    client = TestClient(server.admin_app)
    
    # Complete setup
    response = client.post("/v1/unlock/setup", json={
        "password": "securepassword",
        "terms_accepted": True,
        "license_type": "individual",
        "signtoross_token": "mytoken"
    }, headers={"origin": "http://localhost:8001"})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["state"] == "Ready"
    assert response.json()["agreement_version"] == first_run.CURRENT_AGREEMENT_VERSION
    
    # Check status is now Ready
    response = client.get("/v1/unlock/status")
    assert response.json()["state"] == "Ready"
    assert response.json()["terms_accepted"] is True
    assert response.json()["license_type"] == "individual"
    assert response.json()["agreement_version"] == first_run.CURRENT_AGREEMENT_VERSION
    ack = first_run.read_license_acknowledgement()
    assert ack is not None
    assert ack["accepted_by"] == "server_unlock_setup"
    unlock_meta = json.loads(server._get_unlock_file_path().read_text(encoding="utf-8"))
    assert unlock_meta["agreement_version"] == first_run.CURRENT_AGREEMENT_VERSION
    
    # Verify file permissions (should be 0o600 / read-write by owner only)
    unlock_file = server._get_unlock_file_path()
    assert unlock_file.exists()
    # On non-Windows platforms, we check permissions directly
    if os.name != "nt":
        mode = os.stat(str(unlock_file)).st_mode
        assert (mode & 0o777) == 0o600
        
    # Standard APIs should now be allowed (e.g. not return 451/401)
    response = client.get("/api/status")
    assert response.status_code != 451
    assert response.status_code != 401 or "Server is locked" not in response.text
    
    # Simulate server restart (reset memory state)
    if hasattr(server.STATE, "_unlock_state_mem"):
        delattr(server.STATE, "_unlock_state_mem")
        
    # Status should now be Locked
    response = client.get("/v1/unlock/status")
    assert response.json()["state"] == "Locked"
    
    # Standard APIs should be blocked with 401
    response = client.get("/api/status")
    assert response.status_code == 401
    assert "Server is locked" in response.json()["error"]
    
    # Verify with incorrect password
    response = client.post("/v1/unlock/verify", json={"password": "wrongpassword"}, headers={"origin": "http://localhost:8001"})
    assert response.status_code == 401
    assert "Incorrect password" in response.json()["error"]
    
    # Verify with correct password
    response = client.post("/v1/unlock/verify", json={"password": "securepassword"}, headers={"origin": "http://localhost:8001"})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["state"] == "Ready"
    assert response.json()["agreement_version"] == first_run.CURRENT_AGREEMENT_VERSION
    
    # Status should be Ready again
    response = client.get("/v1/unlock/status")
    assert response.json()["state"] == "Ready"


def test_unlock_verify_requires_reacceptance_for_old_agreement_version(tmp_path):
    client = TestClient(server.admin_app)
    password = "securepassword"
    salt_hex, hash_hex = server._hash_password(password)
    unlock_file = server._get_unlock_file_path()
    unlock_meta = {
        "salt_hex": salt_hex,
        "hash_hex": hash_hex,
        "terms_accepted": True,
        "license_type": "individual",
        "agreement_version": "1",
    }
    server._safe_write_unlock_json(unlock_file, unlock_meta)
    first_run.record_license_acknowledgement(agreement_version="1")
    if hasattr(server.STATE, "_unlock_state_mem"):
        delattr(server.STATE, "_unlock_state_mem")

    status = client.get("/v1/unlock/status")
    assert status.status_code == 200
    assert status.json()["state"] == "Locked"
    assert status.json()["terms_accepted"] is False
    assert status.json()["agreement_required"] is True

    missing_acceptance = client.post(
        "/v1/unlock/verify",
        json={"password": password},
        headers={"origin": "http://localhost:8001"},
    )
    assert missing_acceptance.status_code == 428
    assert missing_acceptance.json()["agreement_required"] is True

    accepted = client.post(
        "/v1/unlock/verify",
        json={"password": password, "terms_accepted": True},
        headers={"origin": "http://localhost:8001"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["agreement_version"] == first_run.CURRENT_AGREEMENT_VERSION
    updated_meta = json.loads(unlock_file.read_text(encoding="utf-8"))
    assert updated_meta["agreement_version"] == first_run.CURRENT_AGREEMENT_VERSION
    ack = first_run.read_license_acknowledgement()
    assert ack is not None
    assert ack["agreement_version"] == first_run.CURRENT_AGREEMENT_VERSION
    assert ack["accepted_by"] == "server_unlock_verify"
