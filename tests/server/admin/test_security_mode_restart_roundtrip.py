# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Security mode saves must survive actual process kills without losing owned data."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from shared.platform_runtime import clear_test_runtime_state_overrides
from shared.process_lifecycle import process_spawn_kwargs


SERVER_ROOT = Path(__file__).resolve().parents[3]
WORKER_SOURCE = r'''
import asyncio
import gc
import json
import sqlite3
import sys
from pathlib import Path

output = sys.stdout
sys.stdout = sys.stderr
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, str(Path(sys.argv[1]).parent))

import server
from fastapi.testclient import TestClient
from shared import secure_storage
from shared.http_request_monitor import HTTPRequestCaptureStore

secure_storage.keyring_available = lambda: False
password = "synthetic-roundtrip-password"
stage, starting_mode = sys.argv[2:4]
server._require_api_login = lambda request: None
server._config_write_block_reason = lambda: None

async def bootstrap_payload():
    return {"config": {"security": {"mode": server.get_security_mode()}}}

server._build_admin_ui_bootstrap_payload = bootstrap_payload

if stage == "upgrade":
    config = server._default_config()
    config["security"]["mode"] = starting_mode
    config["ai_agent"]["memory_backend"] = "legacy"
    server._save_and_reload_state_config(
        config, server_password=password, preferred_store=server.CONFIG_STORE_ENCRYPTED,
    )
asyncio.run(server.bootstrap_password_and_config())
server._configure_secure_storage_for_config(
    server.STATE.config, password=password, allow_key_creation=False,
)

root = server._CONFIG_DIR
databases = [root / "sessions.db", root / "notes_agent" / "notes.db"]
registries = [server.get_agent_install_registry_path(), server.get_frontend_registry_path()]
artifact = root / ".adk" / "artifacts" / "synthetic.bin"
monitor = HTTPRequestCaptureStore()

if stage == "upgrade":
    monitor.set_enabled(True)
    monitor.record({"service": "admin", "path": "/synthetic-kept-event", "status_code": 200})
    for path in databases:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
            connection.execute("INSERT INTO records VALUES (?)", ("synthetic-kept-record",))
            connection.commit()
        finally:
            connection.close()
    for path in registries:
        secure_storage.save_secure_json(path, {"synthetic": "kept-registry"})
    secure_storage.write_secure_file(artifact, b"synthetic-kept-artifact")

def verify(expected_mode):
    assert server.get_security_mode() == expected_mode
    encrypted = expected_mode == server.SECURE_PROFESSIONAL_MAXIMUS_MODE
    assert secure_storage.secure_storage_enabled() is encrypted
    dashboard = monitor.dashboard()
    assert dashboard["enabled"] is True
    assert dashboard["summary"]["total_events"] >= 1
    assert any(event["path"] == "/synthetic-kept-event" for event in dashboard["events"])
    for path in databases:
        connection = sqlite3.connect(path)
        try:
            assert connection.execute("SELECT value FROM records").fetchall() == [("synthetic-kept-record",)]
        finally:
            connection.close()
    for path in registries:
        assert secure_storage.load_secure_json(path) == {"synthetic": "kept-registry"}
    assert secure_storage.read_secure_file(artifact) == b"synthetic-kept-artifact"
    for path in [monitor.path, *databases]:
        assert path.read_bytes().startswith(secure_storage.SQLITE_HEADER) is encrypted
    for path in [artifact, *registries]:
        assert path.read_bytes().startswith(secure_storage.FILE_HEADER) is encrypted
    gc.collect()

mode_before = starting_mode if stage == "upgrade" else (
    server.SECURE_PROFESSIONAL_MAXIMUS_MODE if stage == "downgrade" else "secure_professional"
)
verify(mode_before)
if stage != "restarted":
    target = server.SECURE_PROFESSIONAL_MAXIMUS_MODE if stage == "upgrade" else "secure_professional"
    with TestClient(server.admin_app) as client:
        response = client.post("/api/admin/security/mode", json={"mode": target})
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    verify(target)

output.write(json.dumps({"mode": server.get_security_mode(), "data_preserved": True}) + "\n")
output.flush()
# The parent terminates the real process after the successful save response,
# deliberately bypassing Python atexit and protected-storage shutdown flushes.
sys.stdin.read()
'''


@pytest.mark.parametrize("packaged", [False, True], ids=["source", "packaged-paths"])
@pytest.mark.parametrize("starting_mode", ["normal", "secure", "secure_professional"])
def test_security_upgrade_kill_restart_downgrade_kill_restart(tmp_path, packaged, starting_mode):
    worker = tmp_path / "mode_worker.py"
    worker.write_text(WORKER_SOURCE, encoding="utf-8")
    env = os.environ.copy()
    env["AUTOYOU_TEST_ROOT"] = str(tmp_path / "runtime")
    clear_test_runtime_state_overrides(env)
    env["AUTOYOU_SERVER_PASSWORD"] = "synthetic-roundtrip-password"
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    if packaged:
        env["AUTOYOU_PACKAGED_RUNTIME"] = "1"
    else:
        env.pop("AUTOYOU_PACKAGED_RUNTIME", None)

    for stage, expected_mode in (
        ("upgrade", "secure_professional_maximus"),
        ("downgrade", "secure_professional"),
        ("restarted", "secure_professional"),
    ):
        stderr_path = tmp_path / f"{stage}.stderr.log"
        with stderr_path.open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                [sys.executable, str(worker), str(SERVER_ROOT), stage, starting_mode],
                cwd=SERVER_ROOT,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                **process_spawn_kwargs(hide_window=True),
            )
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    ready = executor.submit(process.stdout.readline)
                    try:
                        result = ready.result(timeout=45)
                    finally:
                        process.kill()
                        process.wait(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                process.stdin.close()
                process.stdout.close()
        assert result, stderr_path.read_text(encoding="utf-8")
        assert json.loads(result) == {"mode": expected_mode, "data_preserved": True}
