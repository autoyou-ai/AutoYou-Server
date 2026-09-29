# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-ce54b6a59ac1e139690f6cc9


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import asyncio
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import server

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-ce54b6a59ac1e139690f6cc9"


class _FakePopen:
    def __init__(
        self,
        cmd,
        cwd=None,
        env=None,
        creationflags=0,
        start_new_session=False,
        stdout=None,
        stderr=None,
        **kwargs,
    ):
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.creationflags = creationflags
        self.start_new_session = start_new_session
        self.stdout = stdout
        self.stderr = stderr
        self.extra_kwargs = kwargs
        self.pid = 43210
        self._returncode = None

    def poll(self):
        return self._returncode

    def terminate(self):
        self._returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._returncode = 1


def test_start_ai_agent_server_background_uses_compiled_subprocess(monkeypatch, tmp_path):
    original_process = server.STATE.agent_process
    # from __debug_provenance_w__ import stripe
    original_task = server.STATE.ai_agent_start_task
    captured = {}
    user_data_dir = tmp_path / "AutoYou"

    async def fake_sync_managed_frontends():
        return {}

    def fake_popen(cmd, cwd=None, env=None, creationflags=0, start_new_session=False, stdout=None, stderr=None, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        captured["creationflags"] = creationflags
        captured["start_new_session"] = start_new_session
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        return _FakePopen(
            cmd,
            cwd=cwd,
            env=env,
            creationflags=creationflags,
            start_new_session=start_new_session,
            stdout=stdout,
            stderr=stderr,
            **kwargs,
        )

    monkeypatch.setattr(server, "is_compiled", lambda: True)
    monkeypatch.setattr(server, "sync_managed_frontend_backends", fake_sync_managed_frontends)
    monkeypatch.setattr(server, "is_port_in_use", lambda port, host: False)
    monkeypatch.setattr(server, "_is_ai_agent_server_healthy", lambda host, port: True)
    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "AI_AGENT_SERVER_PORT", 8081)
    monkeypatch.setattr(server, "AGENT_DIR", r"C:\runtime\AutoYou")
    monkeypatch.setattr(server, "APP_ROOT", Path(r"C:\runtime\AutoYou"))
    monkeypatch.setattr(server.sys, "executable", r"C:\runtime\AutoYou\AutoYou.exe")
    monkeypatch.setattr(server, "get_user_data_dir", lambda app_name="AutoYou": user_data_dir)
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "internal-ai-token")

    server.STATE.agent_process = None
    server.STATE.ai_agent_start_task = None
    try:
        started = asyncio.run(server.start_ai_agent_server_background())
        assert started is True
        assert server.STATE.agent_process.pid == 43210
        assert captured["cmd"] == [
            r"C:\runtime\AutoYou\AutoYou.exe",
            "--run-ai-agent-server",
            "--host",
            "127.0.0.1",
            "--port",
            "8081",
            "--agent-dir",
            r"C:\runtime\AutoYou",
        ]
        assert captured["cwd"] == r"C:\runtime\AutoYou"
        assert captured["env"]["LITELLM_MODE"] == "PRODUCTION"
        assert captured["env"]["ADK_DISABLE_LOAD_DOTENV"] == "1"
        assert captured["env"]["PYTHON_DOTENV_DISABLED"] == "1"
        assert captured["env"][server.AI_AGENT_SESSION_SERVICE_URI_ENV] == server._build_sqlite_service_uri(
            user_data_dir / "autoyou_agents" / ".adk" / "session.db"
        )
        assert captured["env"][server.AI_AGENT_ARTIFACT_SERVICE_URI_ENV] == (
            server._build_file_service_uri(user_data_dir / ".adk" / "artifacts")
        )
        assert captured["env"][server.AI_AGENT_INTERNAL_API_TOKEN_ENV] == "internal-ai-token"
        assert captured["env"][server.AUTOYOU_PARENT_PID_ENV] == str(os.getpid())
        if os.name == "nt":
            assert captured["start_new_session"] is False
            assert captured["creationflags"] & server.subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            assert captured["start_new_session"] is True
    finally:
        server.STATE.agent_process = original_process
        server.STATE.ai_agent_start_task = original_task


def test_start_ai_agent_server_background_uses_non_daemon_dev_worker(monkeypatch, tmp_path):
    original_process = server.STATE.agent_process
    original_task = server.STATE.ai_agent_start_task
    captured = {}
    user_data_dir = tmp_path / "AutoYou"

    class _FakeProcess:
        def __init__(self, target=None, args=(), daemon=None):
            captured["target"] = target
            captured["args"] = args
            captured["daemon"] = daemon
            self.pid = 54321
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

    async def fake_sync_managed_frontends():
        return {}

    monkeypatch.setattr(server, "is_compiled", lambda: False)
    monkeypatch.setattr(server, "sync_managed_frontend_backends", fake_sync_managed_frontends)
    monkeypatch.setattr(server, "_ensure_local_ollama_runtime_ready", lambda: None)
    monkeypatch.setattr(server, "is_port_in_use", lambda port, host: False)
    monkeypatch.setattr(server, "_is_ai_agent_server_healthy", lambda host, port: True)
    monkeypatch.setattr(server.multiprocessing, "Process", _FakeProcess)
    monkeypatch.setattr(server.multiprocessing, "set_executable", lambda value: None)
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "AI_AGENT_SERVER_PORT", 8081)
    monkeypatch.setattr(server, "AGENT_DIR", str(tmp_path / "backend"))
    monkeypatch.setattr(server, "get_config_dir", lambda app_name="AutoYou", anchor=None: user_data_dir)
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "internal-ai-token")

    server.STATE.agent_process = None
    server.STATE.ai_agent_start_task = None
    try:
        started = asyncio.run(server.start_ai_agent_server_background())
        assert started is True
        assert captured["target"] is server.run_agent_server
        assert captured["args"][0:3] == ("127.0.0.1", 8081, str(tmp_path / "backend"))
        assert captured["args"][3][server.AI_AGENT_INTERNAL_API_TOKEN_ENV] == "internal-ai-token"
        assert captured["args"][3][server.AUTOYOU_PARENT_PID_ENV] == str(os.getpid())
        assert captured["daemon"] is False
        assert server.STATE.agent_process.started is True
    finally:
        server.STATE.agent_process = original_process
        server.STATE.ai_agent_start_task = original_task


@pytest.mark.asyncio
async def test_stop_ai_agent_server_cleans_descendants_after_root_exits(monkeypatch):
    original_process = server.STATE.agent_process
    original_task = server.STATE.ai_agent_start_task
    forced = []

    class _ExitedRoot:
        pid = 12345

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return None

        def close(self):
            return None

    root = _ExitedRoot()
    server.STATE.agent_process = root
    server.STATE.ai_agent_start_task = None
    monkeypatch.setattr(server, "_collect_process_tree_pids", lambda pid: [12345, 23456])
    monkeypatch.setattr(server, "_is_agent_process_running", lambda process: False)
    monkeypatch.setattr(server, "live_pids", lambda pids: [23456])
    monkeypatch.setattr(
        server,
        "_force_kill_process_tree",
        lambda process_pid, extra_pids=None: forced.append((process_pid, extra_pids)),
    )

    try:
        await server.stop_ai_agent_server()
        assert forced == [(12345, [12345, 23456])]
        assert server.STATE.agent_process is None
    finally:
        server.STATE.agent_process = original_process
        server.STATE.ai_agent_start_task = original_task


def test_start_compiled_ai_agent_process_uses_server_script_for_python_runtime(monkeypatch, tmp_path):
    captured = {}
    server_script = tmp_path / "server.py"

    def fake_popen(cmd, cwd=None, env=None, creationflags=0, start_new_session=False, stdout=None, stderr=None, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        captured["creationflags"] = creationflags
        captured["start_new_session"] = start_new_session
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        return _FakePopen(
            cmd,
            cwd=cwd,
            env=env,
            creationflags=creationflags,
            start_new_session=start_new_session,
            stdout=stdout,
            stderr=stderr,
            **kwargs,
        )

    monkeypatch.setattr(server, "APP_ROOT", tmp_path)
    monkeypatch.setattr(server, "__file__", str(server_script))
    monkeypatch.setattr(server.sys, "executable", "/usr/local/bin/python")
    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)

    process = server._start_compiled_ai_agent_process(
        "0.0.0.0",
        8081,
        "/app",
        {"AUTOYOU_PACKAGED_RUNTIME": "1"},
    )

    assert process.pid == 43210
    assert captured["cmd"] == [
        "/usr/local/bin/python",
        str(server_script.resolve()),
        "--run-ai-agent-server",
        "--host",
        "0.0.0.0",
        "--port",
        "8081",
        "--agent-dir",
        "/app",
    ]
    assert captured["cwd"] == str(tmp_path)
    assert captured["env"]["AUTOYOU_PACKAGED_RUNTIME"] == "1"
    assert captured["env"][server.AUTOYOU_PARENT_PID_ENV] == str(os.getpid())
    if os.name == "nt":
        assert captured["start_new_session"] is False
        assert captured["creationflags"] & server.subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert captured["start_new_session"] is True


def test_set_ai_agent_multiprocessing_executable_uses_current_python(monkeypatch):
    captured = []
    monkeypatch.setattr(server.sys, "executable", r"C:\runtime\AutoYou\.venv\Scripts\python.exe")
    monkeypatch.setattr(server.multiprocessing, "set_executable", lambda value: captured.append(value))

    server._set_ai_agent_multiprocessing_executable()

    assert captured == [r"C:\runtime\AutoYou\.venv\Scripts\python.exe"]


def test_lingering_ai_agent_detector_matches_orphaned_source_worker(monkeypatch, tmp_path):
    worker_cmd = [
        r"C:\Python313\python.exe",
        "-c",
        "from multiprocessing.spawn import spawn_main; spawn_main(parent_pid=13579, pipe_handle=1)",
        "--multiprocessing-fork",
    ]

    class FakeSourceWorker:
        pid = 24680

        def __init__(self, *, cwd=tmp_path, port=8081):
            self._cwd = cwd
            self._port = port

        def cmdline(self):
            return worker_cmd

        def cwd(self):
            return str(self._cwd)

        def net_connections(self, kind="inet"):
            assert kind == "inet"
            return [SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(port=self._port))]

    monkeypatch.setattr(server, "APP_ROOT", tmp_path)
    monkeypatch.setattr(server, "_pid_is_running", lambda pid: False)

    assert server._looks_like_lingering_ai_agent_process(FakeSourceWorker(), 8081) is True
    assert server._looks_like_lingering_ai_agent_process(FakeSourceWorker(port=8090), 8081) is False
    assert server._looks_like_lingering_ai_agent_process(FakeSourceWorker(cwd=tmp_path.parent), 8081) is False

    monkeypatch.setattr(server, "_pid_is_running", lambda pid: True)
    assert server._looks_like_lingering_ai_agent_process(FakeSourceWorker(), 8081) is False


def test_run_ai_agent_server_cli_if_requested_dispatches_worker(monkeypatch):
    captured = {}
    monkeypatch.setenv("AUTOYOU_TEST_ENV_MARKER", "1")

    def fake_run_agent_server(host, port, agent_dir, env_vars):
        captured["host"] = host
        captured["port"] = port
        captured["agent_dir"] = agent_dir
        captured["env_marker"] = env_vars.get("AUTOYOU_TEST_ENV_MARKER")

    monkeypatch.setattr(server, "run_agent_server", fake_run_agent_server)

    handled = server._run_ai_agent_server_cli_if_requested(
        [
            "--run-ai-agent-server",
            "--host",
            "0.0.0.0",
            "--port",
            "8081",
            "--agent-dir",
            "/app",
        ]
    )

    assert handled is True
    assert captured == {
        "host": "0.0.0.0",
        "port": 8081,
        "agent_dir": "/app",
        "env_marker": "1",
    }
    assert server._run_ai_agent_server_cli_if_requested(["--admin", "8001"]) is False


def test_start_ai_agent_server_background_syncs_managed_frontends_when_compiled_worker_fails(monkeypatch, tmp_path):
    original_process = server.STATE.agent_process
    original_task = server.STATE.ai_agent_start_task
    user_data_dir = tmp_path / "AutoYou"
    synced = []

    class _ExitedPopen(_FakePopen):
        def __init__(self):
            super().__init__(cmd=[])
            self._returncode = 1

    async def fake_sync_managed_frontends():
        synced.append(True)
        return {}

    monkeypatch.setattr(server, "is_compiled", lambda: True)
    monkeypatch.setattr(server, "sync_managed_frontend_backends", fake_sync_managed_frontends)
    monkeypatch.setattr(server, "is_port_in_use", lambda port, host: False)
    monkeypatch.setattr(server, "_is_ai_agent_server_healthy", lambda host, port: False)
    monkeypatch.setattr(server, "_start_compiled_ai_agent_process", lambda *args, **kwargs: _ExitedPopen())
    monkeypatch.setattr(server, "_close_process_handle", lambda process: None)
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "AI_AGENT_SERVER_PORT", 8081)
    monkeypatch.setattr(server, "AGENT_DIR", str(tmp_path / "backend"))
    monkeypatch.setattr(server, "get_user_data_dir", lambda app_name="AutoYou": user_data_dir)

    server.STATE.agent_process = None
    server.STATE.ai_agent_start_task = None
    try:
        started = asyncio.run(server.start_ai_agent_server_background())
        assert started is False
        assert synced == [True]
        assert server.STATE.agent_process is None
    finally:
        server.STATE.agent_process = original_process
        server.STATE.ai_agent_start_task = original_task


def test_start_ai_agent_server_background_ensures_local_ollama_runtime(monkeypatch, tmp_path):
    original_process = server.STATE.agent_process
    original_task = server.STATE.ai_agent_start_task
    calls = []
    user_data_dir = tmp_path / "AutoYou"

    async def fake_sync_managed_frontends():
        return {}

    monkeypatch.setattr(server, "sync_managed_frontend_backends", fake_sync_managed_frontends)
    monkeypatch.setattr(server, "_ensure_local_ollama_runtime_ready", lambda: calls.append("ollama"))
    monkeypatch.setattr(server, "is_port_in_use", lambda port, host: False)
    monkeypatch.setattr(server, "_is_ai_agent_server_healthy", lambda host, port: True)
    monkeypatch.setattr(server, "is_compiled", lambda: True)
    monkeypatch.setattr(server, "_start_compiled_ai_agent_process", lambda *args, **kwargs: _FakePopen(cmd=[]))
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "internal-ai-token")
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "AI_AGENT_SERVER_PORT", 8081)
    monkeypatch.setattr(server, "AGENT_DIR", r"C:\runtime\AutoYou")
    monkeypatch.setattr(server, "get_user_data_dir", lambda app_name="AutoYou": user_data_dir)

    server.STATE.agent_process = None
    server.STATE.ai_agent_start_task = None
    try:
        started = asyncio.run(server.start_ai_agent_server_background())
        assert started is True
        assert calls == ["ollama"]
    finally:
        server.STATE.agent_process = original_process
        server.STATE.ai_agent_start_task = original_task


def test_start_ai_agent_server_background_waits_for_existing_worker_health(monkeypatch):
    original_process = server.STATE.agent_process
    original_task = server.STATE.ai_agent_start_task
    existing_process = _FakePopen(cmd=["python", "worker"])
    health_calls = []
    ollama_calls = []

    async def fake_sync_managed_frontends():
        return {}

    def fake_health(host, port, timeout=0.75):
        del host, port, timeout
        health_calls.append(True)
        return len(health_calls) >= 2

    monkeypatch.setattr(server, "sync_managed_frontend_backends", fake_sync_managed_frontends)
    monkeypatch.setattr(server, "_ensure_local_ollama_runtime_ready", lambda: ollama_calls.append("ollama"))
    monkeypatch.setattr(server, "_is_ai_agent_server_healthy", fake_health)
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "AI_AGENT_SERVER_PORT", 8081)

    server.STATE.agent_process = existing_process
    server.STATE.ai_agent_start_task = None
    try:
        started = asyncio.run(server.start_ai_agent_server_background())
        assert started is True
        assert server.STATE.agent_process is existing_process
        assert len(health_calls) >= 2
        assert ollama_calls == []
    finally:
        server.STATE.agent_process = original_process
        server.STATE.ai_agent_start_task = original_task


def test_start_ai_agent_server_background_checks_ollama_for_healthy_existing_worker(monkeypatch):
    original_process = server.STATE.agent_process
    original_task = server.STATE.ai_agent_start_task
    existing_process = _FakePopen(cmd=["python", "worker"])
    ollama_calls = []

    async def fake_sync_managed_frontends():
        return {}

    monkeypatch.setattr(server, "sync_managed_frontend_backends", fake_sync_managed_frontends)
    monkeypatch.setattr(server, "_is_ai_agent_server_healthy", lambda host, port: True)
    monkeypatch.setattr(server, "_ensure_local_ollama_runtime_ready", lambda: ollama_calls.append("ollama"))
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "AI_AGENT_SERVER_PORT", 8081)

    server.STATE.agent_process = existing_process
    server.STATE.ai_agent_start_task = None
    try:
        started = asyncio.run(server.start_ai_agent_server_background())
        assert started is True
        assert server.STATE.agent_process is existing_process
        assert ollama_calls == ["ollama"]
    finally:
        server.STATE.agent_process = original_process
        server.STATE.ai_agent_start_task = original_task


def test_create_ai_agent_fastapi_app_requires_persistent_session_storage(monkeypatch):
    captured = []

    monkeypatch.setattr(server, "is_compiled", lambda: True)
    monkeypatch.setattr(
        server,
        "_get_ai_agent_fastapi_storage_kwargs",
        lambda: {
            "session_service_uri": "sqlite+aiosqlite:////tmp/autoyou-session.db",
            "artifact_service_uri": "file:///tmp/autoyou-artifacts",
        },
    )

    def fake_get_fast_api_app(*, agents_dir, allow_origins, web, **kwargs):
        captured.append(
            {
                "agents_dir": agents_dir,
                "allow_origins": allow_origins,
                "web": web,
                **kwargs,
            }
        )
        raise AssertionError("sqlalchemy bootstrap failed")

    with pytest.raises(AssertionError, match="sqlalchemy bootstrap failed"):
        server._create_ai_agent_fastapi_app(
            fake_get_fast_api_app,
            agent_dir="/tmp/autoyou-agents",
            agent_logger=logging.getLogger("test.ai_agent"),
        )

    assert len(captured) == 1
    assert captured[0]["session_service_uri"] == "sqlite+aiosqlite:////tmp/autoyou-session.db"
    assert "use_local_storage" not in captured[0]


def test_schedule_ai_agent_server_autostart_runs_once_until_completion(monkeypatch):
    original_task = server.STATE.ai_agent_start_task
    original_process = server.STATE.agent_process
    started = []

    async def exercise():
        gate = asyncio.Event()

        async def fake_start_ai_agent_server_background():
            started.append("started")
            await gate.wait()
            return True

        monkeypatch.setattr(server, "start_ai_agent_server_background", fake_start_ai_agent_server_background)
        server.STATE.ai_agent_start_task = None
        server.STATE.agent_process = None

        assert server.schedule_ai_agent_server_autostart() is True
        assert server.STATE.ai_agent_start_task is not None
        assert server.schedule_ai_agent_server_autostart() is False

        gate.set()
        await server.STATE.ai_agent_start_task

        assert started == ["started"]
        assert server.STATE.ai_agent_start_task is None

    try:
        asyncio.run(exercise())
    finally:
        server.STATE.ai_agent_start_task = original_task
        server.STATE.agent_process = original_process


def test_get_ai_agent_fastapi_storage_kwargs_uses_compiled_appdata(monkeypatch, tmp_path):
    user_data_dir = tmp_path / "AutoYou"
    monkeypatch.setattr(server, "is_compiled", lambda: True)
    monkeypatch.setattr(server, "get_user_data_dir", lambda app_name="AutoYou": user_data_dir)
    monkeypatch.delenv(server.AI_AGENT_SESSION_SERVICE_URI_ENV, raising=False)
    monkeypatch.delenv(server.AI_AGENT_ARTIFACT_SERVICE_URI_ENV, raising=False)

    kwargs = server._get_ai_agent_fastapi_storage_kwargs()

    assert kwargs == {
        "session_service_uri": server._build_sqlite_service_uri(
            user_data_dir / "autoyou_agents" / ".adk" / "session.db"
        ),
        "artifact_service_uri": server._build_file_service_uri(user_data_dir / ".adk" / "artifacts"),
    }


def test_get_ai_agent_fastapi_storage_kwargs_uses_dev_config_dir(monkeypatch, tmp_path):
    config_dir = tmp_path / "autoyou-config"
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    monkeypatch.setattr(server, "get_config_dir", lambda app_name="AutoYou", anchor=None: config_dir)
    monkeypatch.delenv(server.AI_AGENT_SESSION_SERVICE_URI_ENV, raising=False)
    monkeypatch.delenv(server.AI_AGENT_ARTIFACT_SERVICE_URI_ENV, raising=False)

    kwargs = server._get_ai_agent_fastapi_storage_kwargs()

    assert kwargs == {
        "session_service_uri": server._build_sqlite_service_uri(config_dir / "sessions.db"),
        "artifact_service_uri": server._build_file_service_uri(config_dir / ".adk" / "artifacts"),
    }


def test_prepare_ai_agent_process_env_sets_dev_storage_uris(monkeypatch, tmp_path):
    config_dir = tmp_path / "autoyou-config"
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    monkeypatch.setattr(server, "get_config_dir", lambda app_name="AutoYou", anchor=None: config_dir)
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "internal-ai-token")

    env = server._prepare_ai_agent_process_env({"EXISTING": "1"})

    assert env["EXISTING"] == "1"
    assert env[server.AI_AGENT_INTERNAL_API_TOKEN_ENV] == "internal-ai-token"
    assert env[server.AI_AGENT_SESSION_SERVICE_URI_ENV] == server._build_sqlite_service_uri(
        config_dir / "sessions.db"
    )
    assert env[server.AUTOYOU_SESSION_DB_PATH_ENV] == str((config_dir / "sessions.db").resolve())
    assert env[server.AI_AGENT_ARTIFACT_SERVICE_URI_ENV] == (
        server._build_file_service_uri(config_dir / ".adk" / "artifacts")
    )


def test_prepare_ai_agent_process_env_preserves_explicit_memory_db_path(monkeypatch, tmp_path):
    explicit_db = tmp_path / "memory.db"
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "internal-ai-token")

    env = server._prepare_ai_agent_process_env({
        server.AUTOYOU_SESSION_DB_PATH_ENV: str(explicit_db),
    })

    assert env[server.AUTOYOU_SESSION_DB_PATH_ENV] == str(explicit_db.resolve())


def test_prepare_ai_agent_process_env_derives_memory_db_from_custom_sqlite_uri(monkeypatch, tmp_path):
    custom_db = tmp_path / "custom-session.db"
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "internal-ai-token")

    env = server._prepare_ai_agent_process_env({
        server.AI_AGENT_SESSION_SERVICE_URI_ENV: server._build_sqlite_service_uri(custom_db),
    })

    assert env[server.AUTOYOU_SESSION_DB_PATH_ENV] == str(custom_db.resolve())


def test_prepare_ai_agent_process_env_propagates_configured_memory_backend(monkeypatch, tmp_path):
    config_dir = tmp_path / "autoyou-config"
    original_config = server.STATE.config
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    monkeypatch.setattr(server, "get_config_dir", lambda app_name="AutoYou", anchor=None: config_dir)
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "internal-ai-token")

    server.STATE.config = {"ai_agent": {"memory_backend": "cognee"}}
    try:
        env = server._prepare_ai_agent_process_env({"AUTOYOU_MEMORY_BACKEND": "legacy"})
    finally:
        server.STATE.config = original_config

    assert env["AUTOYOU_MEMORY_BACKEND"] == "cognee"
    assert env["AUTOYOU_COGNEE_MEMORY_ENABLED"] == "1"


def test_prepare_ai_agent_process_env_uses_persisted_internet_switch(monkeypatch, tmp_path):
    config_dir = tmp_path / "autoyou-config"
    original_config = server.STATE.config
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    monkeypatch.setattr(server, "get_config_dir", lambda app_name="AutoYou", anchor=None: config_dir)
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "internal-ai-token")

    server.STATE.config = {"ai_agent": {"internet_search_enabled": False}}
    try:
        env = server._prepare_ai_agent_process_env(
            {"AUTOYOU_INTERNET_SEARCH_ENABLED": "1"}
        )
    finally:
        server.STATE.config = original_config

    assert env["AUTOYOU_INTERNET_SEARCH_ENABLED"] == "0"


def test_service_config_uses_worker_internet_switch_from_environment(monkeypatch, tmp_path):
    from service_manager import ServiceConfig

    monkeypatch.setenv("AUTOYOU_INTERNET_SEARCH_ENABLED", "0")

    config = ServiceConfig(db_path=str(tmp_path / "sessions.db"))

    assert config.internet_search_enabled is False


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path prefix behavior")
def test_get_ai_agent_fastapi_storage_kwargs_strips_windows_extended_prefix(monkeypatch, tmp_path):
    config_dir = Path("\\\\?\\" + str(tmp_path / "autoyou-config"))
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    monkeypatch.setattr(server, "get_config_dir", lambda app_name="AutoYou", anchor=None: config_dir)
    monkeypatch.delenv(server.AI_AGENT_SESSION_SERVICE_URI_ENV, raising=False)
    monkeypatch.delenv(server.AI_AGENT_ARTIFACT_SERVICE_URI_ENV, raising=False)

    kwargs = server._get_ai_agent_fastapi_storage_kwargs()

    assert "/?/" not in kwargs["session_service_uri"]
    assert "%3F" not in kwargs["artifact_service_uri"]
    assert kwargs["session_service_uri"].startswith("sqlite+aiosqlite:///")
    assert kwargs["artifact_service_uri"].startswith("file:///")


def test_prepare_ai_agent_process_env_sets_compiled_workspace_root(monkeypatch, tmp_path):
    user_data_dir = tmp_path / "AutoYou"
    workspace_root = user_data_dir / "autoyou_agents"
    monkeypatch.setattr(server, "is_compiled", lambda: True)
    monkeypatch.setattr(server, "get_user_data_dir", lambda app_name="AutoYou": user_data_dir)
    monkeypatch.setattr(
        server,
        "get_dynamic_agents_root",
        lambda app_name="AutoYou", anchor=None: workspace_root,
    )
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "internal-ai-token")

    env = server._prepare_ai_agent_process_env({"EXISTING": "1"})

    assert env["EXISTING"] == "1"
    assert env["AUTOYOU_WORKSPACE_ROOT"] == str(workspace_root.resolve())
    assert env[server.AUTOYOU_SESSION_DB_PATH_ENV] == str(
        (user_data_dir / "autoyou_agents" / ".adk" / "session.db").resolve()
    )
    assert env["LITELLM_MODE"] == "PRODUCTION"
    assert env["ADK_DISABLE_LOAD_DOTENV"] == "1"


def test_attach_ai_agent_endpoints_wraps_build_graph_route_with_json_safe_response(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class _LiteLLMClientStub:
        def __str__(self):
            return "LiteLLMClientStub"

    app = FastAPI()

    @app.get("/dev/build_graph/{app_name}")
    async def broken_build_graph(app_name: str):
        return {
            "name": app_name,
            "root_agent": {
                "model": {
                    "llm_client": _LiteLLMClientStub(),
                },
            },
        }

    monkeypatch.setattr(server, "install_route_aware_request_logging", lambda *args, **kwargs: None)

    server.attach_ai_agent_endpoints(app)

    with TestClient(app) as client:
        response = client.get("/dev/build_graph/autoyou_agents")

    assert response.status_code == 200
    assert response.json() == {
        "name": "autoyou_agents",
        "root_agent": {
            "model": {
                "llm_client": "LiteLLMClientStub",
            },
        },
    }


def test_ai_agent_runtime_settings_endpoint_requires_token_and_applies_update(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    applied = []
    app = FastAPI()
    monkeypatch.setattr(server, "install_route_aware_request_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        server,
        "_request_uses_ai_agent_internal_token",
        lambda request: request.headers.get("Authorization") == "Bearer synthetic-internal-token",
    )
    monkeypatch.setattr(
        server,
        "_apply_ai_agent_runtime_settings",
        lambda payload: applied.append(payload) or {"success": True, "applied": payload},
    )
    server.attach_ai_agent_endpoints(app)

    with TestClient(app) as client:
        denied = client.post(
            "/api/internal/runtime-settings",
            json={"internet_search_enabled": False},
        )
        accepted = client.post(
            "/api/internal/runtime-settings",
            json={"internet_search_enabled": False},
            headers={"Authorization": "Bearer synthetic-internal-token"},
        )

    assert denied.status_code == 403
    assert accepted.status_code == 200
    assert applied == [{"internet_search_enabled": False}]


def test_attach_ai_agent_endpoints_adds_build_graph_image_compat_route(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from google.adk.cli import agent_graph as adk_agent_graph

    class _FakeAgent:
        def __init__(self, name, sub_agents=None):
            self.name = name
            self.sub_agents = list(sub_agents or [])

    class _FakeAdkWebServer:
        def __init__(self, root_agent):
            self.agent_loader = self
            self._root_agent = root_agent

        def load_agent(self, app_name):
            assert app_name == "autoyou_agents"
            return self._root_agent

        def _get_root_agent(self, agent_or_app):
            return agent_or_app

        def make_build_graph_endpoint(self):
            async def build_graph_info(app_name: str):
                _ = self.agent_loader
                return {"name": app_name}

            return build_graph_info

    async def fake_get_agent_graph(agent, highlight_pairs, image=False, dark_mode=True):
        del highlight_pairs, image, dark_mode
        return SimpleNamespace(source=f"digraph {agent.name} {{}}")

    root_agent = _FakeAgent(
        "root_agent",
        [
            _FakeAgent("worker"),
            _FakeAgent("planner", [_FakeAgent("reviewer")]),
        ],
    )
    fake_web_server = _FakeAdkWebServer(root_agent)
    app = FastAPI()
    app.add_api_route(
        "/dev/build_graph/{app_name}",
        fake_web_server.make_build_graph_endpoint(),
        methods=["GET"],
    )

    monkeypatch.setattr(server, "install_route_aware_request_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(adk_agent_graph, "get_agent_graph", fake_get_agent_graph)

    server.attach_ai_agent_endpoints(app)

    with TestClient(app) as client:
        response = client.get("/dev/build_graph_image/autoyou_agents")
        nested_response = client.get(
            "/dev/build_graph_image/autoyou_agents",
            params={"node": "planner/reviewer", "dark_mode": "true"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "autoyou_agents": {"dotSrc": "digraph root_agent {}"},
        "autoyou_agents/worker": {"dotSrc": "digraph worker {}"},
        "autoyou_agents/planner": {"dotSrc": "digraph planner {}"},
        "autoyou_agents/planner/reviewer": {"dotSrc": "digraph reviewer {}"},
    }
    assert nested_response.status_code == 200
    assert nested_response.json() == {"dotSrc": "digraph reviewer {}"}


def test_load_or_create_ai_agent_internal_api_token_handles_secure_storage_error(monkeypatch, tmp_path):
    user_data_dir = tmp_path / "AutoYou"
    monkeypatch.setattr(server, "get_user_data_dir", lambda app_name="AutoYou": user_data_dir)
    monkeypatch.delenv(server.AI_AGENT_INTERNAL_API_TOKEN_ENV, raising=False)

    token_path = server._get_ai_agent_internal_api_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_bytes(b"AYF1EncryptedTokenData")

    def fake_read_secure_file(path):
        raise server.SecureStorageError("Protected file requires Secure Professional Maximus storage: ai_agent_internal_api_token.txt")

    monkeypatch.setattr(server, "read_secure_file", fake_read_secure_file)
    monkeypatch.setattr(server, "write_secure_file", lambda path, data: path.write_bytes(data))

    token = server._load_or_create_ai_agent_internal_api_token()
    assert token and len(token) == 64
    assert os.getenv(server.AI_AGENT_INTERNAL_API_TOKEN_ENV) == token


def test_create_ai_agent_fastapi_app_redirects_adk_web_assets_out_of_bundle(monkeypatch, tmp_path):
    """A packaged launch must not rewrite runtime-config.json inside AutoYou.app.

    google-adk's ApiServer._setup_runtime_config() rewrites
    assets/config/runtime-config.json on every start. In the signed bundle that
    file is a sealed resource, so the write breaks the app's own Developer ID
    signature. The factory has to hand ADK a writable mirror instead.
    """
    import shared.platform_runtime as platform_runtime
    from google.adk.cli.api_server import ApiServer

    browser = tmp_path / "AutoYou.app" / "Contents" / "Resources" / "browser"
    (browser / "assets" / "config").mkdir(parents=True)
    (browser / "main.js").write_text("console.log(1)\n", encoding="utf-8")
    pristine = '{\n  "backendUrl": ""\n}\n'
    (browser / "assets" / "config" / "runtime-config.json").write_text(pristine, encoding="utf-8")

    user_data = (tmp_path / "appdata" / "AutoYou").resolve()
    user_data.mkdir(parents=True)
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(platform_runtime, "get_user_data_dir", lambda app_name="AutoYou": user_data)
    monkeypatch.setattr(
        server,
        "_get_ai_agent_fastapi_storage_kwargs",
        lambda: {
            "session_service_uri": "sqlite+aiosqlite:////tmp/autoyou-session.db",
            "artifact_service_uri": "file:///tmp/autoyou-artifacts",
        },
    )

    seen = {}

    def recorder(self, **kwargs):
        seen.update(kwargs)
        return "adk-app"

    monkeypatch.setattr(ApiServer, "get_fast_api_app", recorder)
    baseline = ApiServer.get_fast_api_app

    def fake_get_fast_api_app(*, agents_dir, allow_origins, web, **kwargs):
        # How ADK's fast_api.get_fast_api_app(web=True) hands the path over.
        return ApiServer.get_fast_api_app(object(), web_assets_dir=browser)

    result = server._create_ai_agent_fastapi_app(
        fake_get_fast_api_app,
        agent_dir=str(tmp_path / "agents"),
        agent_logger=logging.getLogger("test.ai_agent"),
    )

    assert result == "adk-app"
    assert Path(seen["web_assets_dir"]) == (
        user_data / platform_runtime.ADK_WEB_ASSETS_DIRNAME
    ).resolve()
    assert (browser / "assets" / "config" / "runtime-config.json").read_text(encoding="utf-8") == pristine
    assert ApiServer.get_fast_api_app is baseline


def test_adk_dev_server_delegates_web_assets_dir_to_api_server(monkeypatch):
    """web=True makes ADK use DevServer, and the redirect rides its super() call.

    fast_api.get_fast_api_app selects ``DevServer if web else ApiServer``, so the
    patch installed by _adk_web_assets_redirect only reaches the runtime-config
    write because DevServer.get_fast_api_app delegates to ApiServer's. If a
    future ADK stops delegating, this test fails loudly instead of the packaged
    app silently going back to writing into its own signed bundle.
    """
    from google.adk.cli.api_server import ApiServer
    from google.adk.cli.dev_server import DevServer

    assert issubclass(DevServer, ApiServer)

    seen = {}

    def recorder(self, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(ApiServer, "get_fast_api_app", recorder)

    instance = DevServer.__new__(DevServer)
    instance._trace_dict = {}
    instance._memory_exporter = None
    instance._register_dev_endpoints = lambda *args, **kwargs: None

    instance.get_fast_api_app(web_assets_dir="/packaged/browser")

    assert seen["web_assets_dir"] == "/packaged/browser"
