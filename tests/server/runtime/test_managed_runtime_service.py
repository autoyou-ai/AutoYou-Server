# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See the LICENSE file in the project root for license information.

from __future__ import annotations

import asyncio
import socket
import sys
from types import SimpleNamespace

from shared import managed_runtime_service as managed_runtime


def test_manifest_contract_accepts_process_and_compose_and_rejects_escape_paths():
    process = managed_runtime.normalize_managed_runtime(
        {"type": "process", "command": ["./website/backend/server", "--port", "{port}"]}
    )
    compose = managed_runtime.normalize_managed_runtime(
        {"type": "docker-compose", "compose_file": "website/backend/compose.yaml", "service": "web"}
    )

    assert process["command"][-1] == "{port}"
    assert process["working_directory"] == "."
    assert compose["compose_file"] == "website/backend/compose.yaml"
    assert managed_runtime.normalize_managed_runtime(
        {"type": "process", "command": ["./server"], "working_directory": "../outside"}
    ) is None
    assert managed_runtime.normalize_managed_runtime(
        {"type": "docker-compose", "compose_file": "../../outside/compose.yaml"}
    ) is None


def test_managed_service_environment_scrubs_secrets_and_sets_loopback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("MANAGED_RUNTIME_TEST_SETTING", "kept")

    environment = managed_runtime._service_environment(8123)

    assert "OPENAI_API_KEY" not in environment
    assert environment["MANAGED_RUNTIME_TEST_SETTING"] == "kept"
    assert environment["HOST"] == environment["AUTOYOU_HOST"] == "127.0.0.1"
    assert environment["PORT"] == environment["AUTOYOU_PORT"] == "8123"


def test_process_runtime_binds_only_loopback_and_stops_gracefully(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    code = (
        "import os; from http.server import BaseHTTPRequestHandler, HTTPServer; "
        "H=type('H',(BaseHTTPRequestHandler,),{"
        "'do_GET':lambda self:(self.send_response(200),self.end_headers()), "
        "'log_message':lambda *args:None}); "
        "HTTPServer((os.environ['HOST'],int(os.environ['PORT'])),H).serve_forever()"
    )
    spec = {
        "type": "process",
        "command": [sys.executable, "-u", "-c", code],
        "health_path": "/healthz",
    }
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    handle = asyncio.run(
        managed_runtime.start_managed_runtime_service(
            spec,
            agent_name="rust_web_agent",
            agent_dir=agent_dir,
            port=port,
            log_path=tmp_path / "logs" / "rust_web_agent.log",
        )
    )
    try:
        assert handle.port == port
    finally:
        assert managed_runtime.stop_managed_runtime_service(handle)
        assert handle.process.poll() is not None


def test_compose_runtime_uses_a_private_project_and_removes_only_its_containers(tmp_path, monkeypatch):
    compose_file = tmp_path / "website" / "backend" / "compose.yaml"
    compose_file.parent.mkdir(parents=True)
    compose_file.write_text("services: {}\n", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs))
        return SimpleNamespace(returncode=0)

    async def ready(*_args, **_kwargs):
        return None

    monkeypatch.setattr(managed_runtime.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(managed_runtime.subprocess, "run", fake_run)
    monkeypatch.setattr(managed_runtime, "_wait_until_ready", ready)

    handle = asyncio.run(
        managed_runtime.start_managed_runtime_service(
            {"type": "docker-compose", "compose_file": "website/backend/compose.yaml"},
            agent_name="rust_web_agent",
            agent_dir=tmp_path,
            port=8123,
            log_path=tmp_path / "logs" / "rust_web_agent.log",
        )
    )
    try:
        assert calls[0][0][-3:] == ["up", "--detach", "--build"]
        assert "--project-name" in calls[0][0]
        assert calls[0][1]["env"]["HOST"] == "127.0.0.1"
        assert calls[0][1]["env"]["PORT"] == "8123"
    finally:
        assert managed_runtime.stop_managed_runtime_service(handle)

    down = calls[1][0]
    assert down[-4:-2] == ["down", "--remove-orphans"]
    assert down[down.index("--project-name") + 1] == handle.compose_project
    assert handle.compose_project == managed_runtime._project_name("rust_web_agent", tmp_path)
    assert "--volumes" not in down
    assert "--rmi" not in down
