"""Owned worker shutdown RPC and request cleanup before process termination."""

import asyncio
import json
import logging
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import server
from core_server import services
from shared.session_execution import SessionExecutionManager, SessionTurnCancelledError


def test_shutdown_endpoint_requires_loopback_and_internal_token(monkeypatch):
    app = FastAPI()
    called = []
    app.state.autoyou_request_shutdown = lambda: called.append("shutdown")
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "synthetic-internal-token")
    monkeypatch.setattr(server, "install_route_aware_request_logging", lambda *args, **kwargs: None)
    server.attach_ai_agent_endpoints(app)
    headers = {"Authorization": "Bearer synthetic-internal-token"}
    with TestClient(app, client=("192.0.2.1", 54321)) as remote:
        assert remote.post("/api/internal/shutdown", headers=headers).status_code == 403
    with TestClient(app, client=("127.0.0.1", 54321)) as local:
        assert local.post("/api/internal/shutdown").status_code == 403
        assert called == []
        assert local.post("/api/internal/shutdown", headers=headers).json() == {"success": True}
        assert called == ["shutdown"]
        del app.state.autoyou_request_shutdown
        assert local.post("/api/internal/shutdown", headers=headers).status_code == 503


def test_default_tests_never_send_shutdown_to_live_worker(monkeypatch, tmp_path):
    called = []
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(services, "_post_ai_agent_shutdown_request", lambda *args: called.append(args))
    assert services._request_ai_agent_graceful_shutdown() is False
    assert called == []


def test_worker_shutdown_transport_uses_private_bearer_and_bounded_timeout(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            assert limit == 16_384
            return json.dumps({"success": True}).encode()

    def fake_open(request, timeout):
        captured.update(url=request.full_url, timeout=timeout, auth=request.get_header("Authorization"))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    assert services._post_ai_agent_shutdown_request("http://127.0.0.1:12345/api/internal/shutdown", "synthetic-token")
    assert captured == {"url": "http://127.0.0.1:12345/api/internal/shutdown", "timeout": 1.5, "auth": "Bearer synthetic-token"}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["graceful", "timeout", "unsupported"])
async def test_stop_worker_drains_then_uses_bounded_termination_fallback(outcome, monkeypatch):
    calls = []
    process = SimpleNamespace(pid=12345, alive=True)

    def terminate():
        calls.append("terminate")
        process.alive = False

    process.terminate = terminate

    def wait(proc, timeout):
        calls.append(("wait", timeout))
        if outcome == "timeout" and timeout == 7:
            raise subprocess.TimeoutExpired("synthetic-worker", timeout)
        proc.alive = False

    runtime = SimpleNamespace(
        asyncio=asyncio, subprocess=subprocess, LOGGER=logging.getLogger("synthetic-shutdown"),
        STATE=SimpleNamespace(agent_process=process, ai_agent_start_task=None),
        _invalidate_admin_status_cache=lambda *args: None,
        _get_agent_process_pid=lambda proc: proc.pid,
        _collect_process_tree_pids=lambda pid: [pid],
        _is_agent_process_running=lambda proc: proc.alive,
        _wait_for_agent_process_exit=wait,
        _kill_agent_process=lambda proc: calls.append("kill"),
        live_pids=lambda pids: [],
        _close_process_handle=lambda proc: calls.append("close"),
    )
    monkeypatch.setattr(services, "_runtime_module", runtime)
    monkeypatch.setattr(services, "_request_ai_agent_graceful_shutdown", lambda: outcome != "unsupported")
    await services.stop_ai_agent_server()
    assert ("terminate" in calls) is (outcome != "graceful")
    assert "kill" not in calls
    assert calls[-1] == "close"
    assert runtime.STATE.agent_process is None


@pytest.mark.asyncio
async def test_core_shutdown_cancels_chat_before_stopping_adk_worker(monkeypatch):
    labels = []
    closed = asyncio.Event()

    async def noop():
        return True

    async def stop_turns():
        closed.set()

    async def stop_worker():
        assert closed.is_set()

    async def run_step(label, awaitable, timeout):
        labels.append(label)
        await awaitable
        return True

    runtime = SimpleNamespace(
        LOGGER=logging.getLogger("synthetic-shutdown"), _run_shutdown_step=run_step,
        STATE=SimpleNamespace(iroh_service=SimpleNamespace(stop=noop)),
        _managed_frontend_shutdown_timeout=lambda: 1,
        stop_tunnelmole_service=lambda **kwargs: noop(),
        stop_bluetooth_pairing_service=lambda *args: noop(),
        WEBRTC=SimpleNamespace(shutdown=noop),
        get_session_execution_manager=lambda: SimpleNamespace(shutdown=stop_turns),
        stop_ai_agent_server=stop_worker,
    )
    for name in ("stop_auth_server", "stop_managed_frontend_backends", "stop_autoyou_page_service_background",
                 "stop_signal", "stop_whatsapp", "stop_telegram_user", "stop_telegram", "_stop_cloud_sse_listener",
                 "_stop_scheduler_service", "_cancel_tracked_background_tasks"):
        setattr(runtime, name, noop)
    monkeypatch.setattr(services, "_runtime_module", runtime)
    await services._stop_runtime_services_for_shutdown()
    assert runtime.STATE.iroh_service is None
    assert labels.index("Iroh session service") < labels.index("Session execution manager")
    assert labels.index("WebRTC manager") < labels.index("Session execution manager") < labels.index("AI Agent Server")


@pytest.mark.asyncio
async def test_session_manager_rejects_new_turns_after_shutdown_starts():
    manager = SessionExecutionManager()
    identity = manager.bind_transport_owner("local", "synthetic-shutdown-device")
    calls = []

    async def handler():
        calls.append("started")

    await manager.shutdown()
    with pytest.raises(SessionTurnCancelledError) as error:
        await manager.submit_turn(identity, handler)
    assert error.value.status.metadata["reason"] == "server_shutdown"
    assert calls == []
    assert manager._contexts == {}


@pytest.mark.asyncio
async def test_session_shutdown_does_not_resume_queue_after_cancelling_active_handler():
    manager = SessionExecutionManager(default_turn_timeout_seconds=10)
    identity = manager.bind_transport_owner("local", "synthetic-active-device")
    started = asyncio.Event()
    closed = asyncio.Event()

    async def handler():
        started.set()
        try:
            await asyncio.Future()
        finally:
            closed.set()

    turn = asyncio.create_task(manager.submit_turn(identity, handler))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(manager.shutdown(), 1)
        with pytest.raises(asyncio.CancelledError):
            await turn
        assert closed.is_set()
        assert manager._contexts == {}
    finally:
        await manager.shutdown()
        await asyncio.gather(turn, return_exceptions=True)


@pytest.mark.parametrize("lan", [False, True])
def test_worker_exit_stops_both_listeners_and_flushes_protected_storage(lan, monkeypatch, tmp_path):
    import os
    import sqlite3
    import uvicorn
    from shared import local_tls, secure_storage

    secure_storage.disable_secure_storage()
    # Replace only this test process's mapping; run_agent_server clears it.
    env = {"AUTOYOU_TEST_ROOT": str(tmp_path),
           "AUTOYOU_SECURE_STORAGE_MODE": "secure_professional_maximus",
           "AUTOYOU_SECURE_STORAGE_ROOT": str(tmp_path),
           "AUTOYOU_SECURE_STORAGE_APP": "AutoYou-test",
           "AUTOYOU_SECURE_STORAGE_PASSWORD": "synthetic-worker-password",
           "AUTOYOU_AI_AGENT_LAN_ACCESS": "1" if lan else "0"}
    monkeypatch.setattr(os, "environ", dict(env))
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    secure_storage.enable_secure_storage(app_name="AutoYou-test", root=tmp_path, password="synthetic-worker-password")
    app = FastAPI()
    instances = []
    database = tmp_path / "synthetic-worker.db"

    def create_app(*args, **kwargs):
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE synthetic_records (value TEXT)")
            db.execute("INSERT INTO synthetic_records VALUES ('synthetic-worker-data')")
        return app

    class FakeServer:
        def __init__(self, config):
            self.config = config
            self.should_exit = False
            instances.append(self)

        async def serve(self):
            app.state.autoyou_request_shutdown()
            assert self.should_exit is True

    runtime = SimpleNamespace(
        __file__=str(tmp_path / "server.py"), _CONFIG_DIR=tmp_path,
        SERVER_BIND_HOST="127.0.0.1", SECURE_PROFESSIONAL_MAXIMUS_MODE="secure_professional_maximus",
        start_parent_process_watchdog=lambda: None,
        enable_secure_storage=secure_storage.enable_secure_storage,
        secure_storage_enabled=secure_storage.secure_storage_enabled,
        disable_secure_storage=secure_storage.disable_secure_storage,
        _macos_keychain_bootstrap_timeout_seconds=lambda: 0.1,
        is_compiled=lambda: False, _vpn_addresses_enabled=lambda: False,
        normalize_local_filesystem_path=lambda value: value,
        resolve_adk_agents_base_dir=lambda *args: str(tmp_path / "agents"),
        _create_ai_agent_fastapi_app=create_app,
    )
    monkeypatch.setattr(services, "_runtime_module", runtime)
    monkeypatch.setattr(server, "attach_ai_agent_endpoints", lambda app: None)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(local_tls, "ensure_enabled", lambda *args, **kwargs: SimpleNamespace(
        server_cert_path=tmp_path / "synthetic.crt", server_key_path=tmp_path / "synthetic.key",
    ))
    try:
        services.run_agent_server("127.0.0.1", 12345, str(tmp_path / "agents"), env)
        assert len(instances) == (2 if lan else 1)
        assert all(instance.should_exit and instance.config.timeout_graceful_shutdown == 5.0 for instance in instances)
        assert secure_storage.secure_storage_enabled() is False
        assert database.read_bytes().startswith(secure_storage.SQLITE_HEADER)
        assert b"synthetic-worker-data" not in database.read_bytes()
    finally:
        secure_storage.disable_secure_storage()
