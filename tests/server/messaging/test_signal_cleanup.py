# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-534454206164647265737320-c7fca0f6f0092ed5174f79d3


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-534454206164647265737320-c7fca0f6f0092ed5174f79d3"

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import server
from signal_service import SignalService


@pytest.fixture(autouse=True)
def _disable_keystore(monkeypatch):
    monkeypatch.setattr(server, "_get_server_keystore", lambda: None)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: False)


def _capture_config_session():
    return (
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )


def _restore_config_session(snapshot):
    server._set_config_session(
        config_store=snapshot[2],
        server_password=snapshot[0],
        config_unlock_password=snapshot[1],
    )


def _set_encrypted_session(password: str):
    server._set_config_session(
        config_store=server.CONFIG_STORE_ENCRYPTED,
        server_password=password,
        config_unlock_password=password,
    )


def _same_origin_headers() -> dict[str, str]:
    return {
        "Origin": "http://testserver",
        "Referer": "http://testserver/admin",
    }


def _admin_test_client() -> TestClient:
    """Return a TestClient carrying an admin session.

    These endpoints expose pairing QR codes, message logs and destructive
    service controls, so they require authentication just like their
    ``/api/*/send`` siblings. The admin UI supplies the session cookie on every
    call; tests have to do the same.
    """
    client = TestClient(server.admin_app)
    server.ADMIN_SESSIONS["test-admin-session"] = True
    client.cookies.set("admin_session", "test-admin-session")
    return client


@pytest.mark.asyncio
async def test_signal_websocket_cleanup_log_redacts_phone_number(caplog):
    service = SignalService(port=8082, device_name="test-signal")
    phone_number = "+11234567890"

    class DummyTask:
        def done(self):
            return False

        def cancel(self):
            return None

    async def never_finishes(_task, timeout=None):
        raise TimeoutError()

    service.websocket_connections[phone_number] = None
    service.websocket_tasks[phone_number] = DummyTask()

    import signal_service

    with caplog.at_level("INFO", logger="autoyou.signal_service"):
        original_wait_for = signal_service.asyncio.wait_for
        signal_service.asyncio.wait_for = never_finishes
        try:
            await service._stop_message_polling()
        finally:
            signal_service.asyncio.wait_for = original_wait_for

    rendered = caplog.text
    assert phone_number not in rendered
    assert "***7890" in rendered


@pytest.mark.asyncio
async def test_signal_service_cleanup_removes_data_and_resets_state(tmp_path):
    service = SignalService(port=8082, device_name="test-signal")
    service.data_dir = str(tmp_path / "signal_data")
    os.makedirs(service.data_dir, exist_ok=True)
    marker = tmp_path / "signal_data" / "account.json"
    marker.write_text("paired", encoding="utf-8")

    service.registered_numbers = {"+11234567890"}
    service.signal_client = object()
    service.message_log = [{"message": "hello"}]

    stop_calls = []

    async def fake_stop(shutdown_docker: bool = False):
        stop_calls.append(shutdown_docker)
        service.container = None
        service.signal_client = None

    service.stop = fake_stop  # type: ignore[assignment]

    assert await service.cleanup(shutdown_docker=True) is True
    assert stop_calls == [True]
    assert not os.path.exists(service.data_dir)
    assert service.registered_numbers == set()
    assert service.signal_client is None
    assert service.message_log == []


def test_signal_service_detects_missing_container_errors():
    assert SignalService._is_missing_container_error("No such container: signal-cli-rest-api-8082")
    assert SignalService._is_missing_container_error("Error response from daemon: No such container")
    assert not SignalService._is_missing_container_error("permission denied")


@pytest.mark.asyncio
async def test_signal_forward_to_chat_api_attaches_conversation_metadata(monkeypatch):
    import rest_api
    import signal_service

    service = SignalService(port=8082, device_name="test-signal")
    captured = []
    sent_messages = []

    class DummyExecutionManager:
        def bind_transport_owner(self, transport: str, sender_id: str, *, raw_session_id=None):
            return SimpleNamespace(
                transport=transport,
                sender_id=sender_id,
                raw_session_id=raw_session_id,
                canonical_session_id=f"session::{transport}:{sender_id}",
                canonical_user_id=f"user::{transport}:{sender_id}",
                owner_key=f"{transport}:{sender_id}",
                thread_id=2,
            )

        async def submit_turn(self, identity, handler, *, on_status=None, timeout_seconds=None, label="chat"):
            if on_status:
                await on_status(SimpleNamespace(status="queued", queue_position=1))
            return await handler()

    async def fake_process_chat_message(chat_request, ai_agent_url=None, on_chunk=None, on_media_reply=None):
        del ai_agent_url, on_chunk, on_media_reply
        captured.append(chat_request)
        return SimpleNamespace(response="stored", agent_name="AutoYou", media_reply_attachments=[])

    async def fake_send_signal_message(phone_number, message):
        sent_messages.append((phone_number, message))
        return True

    monkeypatch.setattr(signal_service, "get_session_execution_manager", lambda: DummyExecutionManager())
    monkeypatch.setattr(signal_service, "_resolve_conversation_identity", lambda identity, start_new_thread=False: identity)
    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)
    service.send_typing = lambda *args, **kwargs: asyncio.sleep(0)  # type: ignore[assignment]
    service.stop_typing = lambda *args, **kwargs: asyncio.sleep(0)  # type: ignore[assignment]
    service._send_signal_message = fake_send_signal_message  # type: ignore[assignment]
    service._log_message = lambda *args, **kwargs: asyncio.sleep(0)  # type: ignore[assignment]

    await service._forward_to_chat_api(
        "+12125550101",
        "remember synthetic Signal metadata",
        1234567890,
        "+12125550101",
    )
    await asyncio.gather(*list(service.chat_tasks))

    metadata = captured[0].metadata
    assert sent_messages
    assert metadata["client"] == "signal"
    assert metadata["canonical_owner_key"] == "signal:+12125550101"
    assert metadata["canonical_user_id"] == "user::signal:+12125550101"
    assert metadata["canonical_session_id"] == "session::signal:+12125550101"
    assert metadata["destination_session_id"] == "+12125550101"
    assert metadata["raw_session_id"] == "+12125550101"
    assert metadata["conversation_thread_id"] == 2
    assert metadata["source"] == "notes_to_self"


def test_admin_cleanup_signal_restarts_after_clearing_state(monkeypatch):
    original_service = server.STATE.signal_service
    original_config = server.STATE.config
    original_session = _capture_config_session()

    class DummySignalService:
        def __init__(self):
            self.cleanup_calls = []

        async def cleanup(self, shutdown_docker: bool = False):
            self.cleanup_calls.append(shutdown_docker)
            return True

    cleanup_service = DummySignalService()
    restart_states = []
    saved_configs = []

    async def fake_restart():
        restart_states.append(server.STATE.signal_service)

    def fake_save(config, password):
        saved_configs.append((config["signal"]["paired"], config["signal"]["phone_number"], password))

    try:
        server.STATE.signal_service = cleanup_service
        server.STATE.config = {
            "signal": {
                "enabled": True,
                "port": 8082,
                "device_name": "AutoYou-Signal",
                "shutdown_docker_on_exit": True,
                "paired": True,
                "phone_number": "+11234567890",
            }
        }
        _set_encrypted_session("secret")

        monkeypatch.setattr(server, "start_or_restart_signal", fake_restart)
        monkeypatch.setattr(server, "save_encrypted_config", fake_save)

        with _admin_test_client() as client:
            response = client.post("/api/signal/cleanup", headers=_same_origin_headers())

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert cleanup_service.cleanup_calls == [True]
        assert restart_states == [None]
        assert saved_configs == [(False, "", "secret")]
        assert server.STATE.config["signal"]["paired"] is False
        assert server.STATE.config["signal"]["phone_number"] == ""
    finally:
        server.STATE.signal_service = original_service
        server.STATE.config = original_config
        _restore_config_session(original_session)


def test_admin_cleanup_signal_works_without_running_service(monkeypatch):
    original_service = server.STATE.signal_service
    original_config = server.STATE.config
    original_session = _capture_config_session()
    original_signal_service_cls = server.SignalService

    created_services = []
    restart_calls = []

    class DummySignalService:
        def __init__(self, port: int = 8082, device_name: str = "signal-api"):
            self.port = port
            self.device_name = device_name
            self.cleanup_calls = []
            created_services.append(self)

        async def cleanup(self, shutdown_docker: bool = False):
            self.cleanup_calls.append(shutdown_docker)
            return True

    async def fake_restart():
        restart_calls.append(True)

    try:
        server.STATE.signal_service = None
        server.STATE.config = {
            "signal": {
                "enabled": True,
                "port": 9001,
                "device_name": "RePair",
                "shutdown_docker_on_exit": False,
                "paired": True,
                "phone_number": "+11234567890",
            }
        }
        server._set_config_session(config_store=server.CONFIG_STORE_NONE)

        monkeypatch.setattr(server, "SignalService", DummySignalService)
        monkeypatch.setattr(server, "start_or_restart_signal", fake_restart)

        with _admin_test_client() as client:
            response = client.post("/api/signal/cleanup", headers=_same_origin_headers())

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert len(created_services) == 1
        assert created_services[0].port == 9001
        assert created_services[0].device_name == "RePair"
        assert created_services[0].cleanup_calls == [True]
        assert restart_calls == [True]
        assert server.STATE.config["signal"]["paired"] is False
        assert server.STATE.config["signal"]["phone_number"] == ""
    finally:
        server.STATE.signal_service = original_service
        server.STATE.config = original_config
        _restore_config_session(original_session)
        server.SignalService = original_signal_service_cls


def test_signal_pruning_preserves_unverified_accounts_and_external_files(tmp_path):
    service = SignalService(port=8082, device_name="test-signal")
    service.data_dir = str(tmp_path / "signal_data")
    data = tmp_path / "signal_data" / "data"
    data.mkdir(parents=True)
    corrupt, unknown = data / "corrupt", data / "unknown"
    corrupt.write_text('{"registered":', encoding="utf-8")
    unknown.write_text('{}', encoding="utf-8")
    outside = data.parent / "outside"
    outside.write_text('{"registered": false}', encoding="utf-8")
    try:
        (data / "linked").symlink_to(outside)
    except OSError:
        if os.name != "nt":
            raise  # Windows may require privileges to create symlinks.
    pending = data / "missing.d" / "account.db"
    pending.parent.mkdir()
    pending.write_text("synthetic account data", encoding="utf-8")
    stub = data / "stub"
    stub.write_text('{"registered": false}', encoding="utf-8")
    retained = [{"path": name} for name in
                ("corrupt", "unknown", "missing", "../outside", str(outside))]
    if (data / "linked").is_symlink():
        retained.append({"path": "linked"})
    retained.append({"legacy": "synthetic account"})
    accounts = data / "accounts.json"
    accounts.write_text(json.dumps({"accounts": retained + [{"path": "stub"}]}), encoding="utf-8")
    protected = {path: path.read_bytes() for path in (corrupt, unknown, outside, pending)}

    service._prune_unregistered_accounts()

    for path, content in protected.items():
        assert path.exists() and path.read_bytes() == content
    if {"path": "linked"} in retained:
        assert (data / "linked").is_symlink()
    assert not stub.exists()
    assert json.loads(accounts.read_text(encoding="utf-8"))["accounts"] == retained


@pytest.mark.parametrize("stop_fails", [False, True])
def test_signal_pruning_waits_for_the_existing_container_to_stop(monkeypatch, stop_fails):
    service = SignalService(port=8082, device_name="test-signal")
    calls = []

    def stop(**kwargs):
        calls.append("stop")
        if stop_fails:
            raise RuntimeError("synthetic stop failure")

    container = SimpleNamespace(stop=stop, remove=lambda **kwargs: calls.append("remove"))
    service.docker_client = SimpleNamespace(
        images=SimpleNamespace(get=lambda *_: object()),
        containers=SimpleNamespace(get=lambda *_: container, run=lambda *args, **kwargs: calls.append("start")))
    monkeypatch.setattr(service, "_prune_unregistered_accounts", lambda: calls.append("prune"))
    assert service._start_container() is (not stop_fails)
    assert calls == (["stop"] if stop_fails else ["stop", "remove", "prune", "start"])


def test_signal_service_prunes_unregistered_account_stubs(tmp_path):
    service = SignalService(port=8082, device_name="test-signal")
    service.data_dir = str(tmp_path / "signal_data")
    data_path = tmp_path / "signal_data" / "data"
    data_path.mkdir(parents=True, exist_ok=True)

    # 1. Registered account stub
    reg_file = data_path / "acc_reg"
    reg_file.write_text(json.dumps({"registered": True, "number": "+15550000001"}), encoding="utf-8")
    reg_dir = data_path / "acc_reg.d"
    reg_dir.mkdir()
    (reg_dir / "account.db").write_text("db-data", encoding="utf-8")

    # 2. Unregistered account stub (failed linking attempt)
    unreg_file = data_path / "acc_unreg"
    unreg_file.write_text(json.dumps({"registered": False, "number": "+15550000002"}), encoding="utf-8")
    unreg_dir = data_path / "acc_unreg.d"
    unreg_dir.mkdir()
    (unreg_dir / "account.db").write_text("db-data-partial", encoding="utf-8")

    accounts_file = data_path / "accounts.json"
    accounts_file.write_text(
        json.dumps({
            "accounts": [
                {"path": "acc_reg", "number": "+15550000001"},
                {"path": "acc_unreg", "number": "+15550000002"},
            ],
            "version": 2
        }),
        encoding="utf-8"
    )

    service._prune_unregistered_accounts()

    # Verify registered account was preserved
    assert reg_file.exists()
    assert reg_dir.exists()

    # Verify unregistered account was pruned
    assert not unreg_file.exists()
    assert not unreg_dir.exists()

    # Verify accounts.json was updated
    updated_accounts = json.loads(accounts_file.read_text(encoding="utf-8"))
    assert len(updated_accounts["accounts"]) == 1
    assert updated_accounts["accounts"][0]["path"] == "acc_reg"


@pytest.mark.asyncio
async def test_signal_service_pairing_status_fallback_matching():
    service = SignalService(port=8082, device_name="AutoYou-Signal")

    class FakeResponse:
        def __init__(self, status_code, data):
            self.status_code = status_code
            self._data = data

        def json(self):
            return self._data

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def get(self, url, **kwargs):
            if "/v1/accounts" in url:
                return FakeResponse(200, [{"number": "+15550000001"}])
            if "/v1/devices/" in url:
                # Device was renamed by user on mobile to "Custom-Name"
                return FakeResponse(200, [
                    {"id": 1, "name": "Custom-Name", "creation_timestamp": 12345, "last_seen_timestamp": 67890}
                ])
            return FakeResponse(404, {})

    import httpx
    orig_async_client = httpx.AsyncClient
    httpx.AsyncClient = FakeAsyncClient
    try:
        status = await service.check_device_pairing_status()
        assert status["paired"] is True
        assert status["phone_number"] == "+15550000001"
        assert status["device_info"]["name"] == "Custom-Name"
    finally:
        httpx.AsyncClient = orig_async_client
