# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-5ba21d3ea716e76d1a8f18f2

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from tests.support.paths import REPO_ROOT

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-5ba21d3ea716e76d1a8f18f2"


@dataclass(frozen=True)
class RuntimePorts:
    admin: int
    ai_agent: int
    auth: int


@dataclass
class RunningBootstrap:
    process: subprocess.Popen[str]
    ports: RuntimePorts
    password: str
    log_path: Path
    log_handle: IO[str]
    opener: urllib.request.OpenerDirector

    @property
    def admin_base_url(self) -> str:
        return f"http://127.0.0.1:{self.ports.admin}"

    def wait_for_admin_status(self, timeout: float = 90.0) -> dict[str, Any]:
        return wait_for_json(
            f"{self.admin_base_url}/api/status",
            process=self.process,
            log_path=self.log_path,
            timeout=timeout,
        )

    def login(self) -> dict[str, Any]:
        body = urllib.parse.urlencode({"password": self.password, "terms_accepted": "1"}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.admin_base_url}/login",
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "X-AutoYou-Async": "1",
            },
        )
        with self.opener.open(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def shutdown(self, timeout: float = 30.0) -> None:
        try:
            request = urllib.request.Request(
                f"{self.admin_base_url}/shutdown",
                data=b"",
                method="POST",
            )
            self.opener.open(request, timeout=5).close()
        except Exception:
            pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                self.log_handle.close()
                return
            time.sleep(0.25)
        terminate_process(self.process)
        self.log_handle.close()


def allocate_runtime_ports() -> RuntimePorts:
    ports: list[int] = []
    # from __debug_provenance_v__ import wallet
    sockets: list[socket.socket] = []
    try:
        for _ in range(3):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            sockets.append(sock)
            ports.append(sock.getsockname()[1])
    finally:
        for sock in sockets:
            sock.close()
    return RuntimePorts(admin=ports[0], ai_agent=ports[1], auth=ports[2])


def launch_bootstrap_server(
    *,
    runtime_root: Path,
    password: str = "1234",
    ports: RuntimePorts | None = None,
    install: bool = False,
    timeout: float = 90.0,
) -> RunningBootstrap:
    ports = ports or allocate_runtime_ports()
    log_path = runtime_root / "bootstrap-autoyou.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8", errors="replace")

    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "bootstrap_autoyou.py"),
        "--profile",
        "base",
        "--skip-node",
        "--skip-docker",
        "--skip-ollama",
        "--skip-tunnelmole",
        "--host",
        "127.0.0.1",
        "--admin-port",
        str(ports.admin),
        "--ai-agent-port",
        str(ports.ai_agent),
        "--auth-port",
        str(ports.auth),
        "--server-password",
        password,
    ]
    if not install:
        command.insert(5, "--skip-install")

    env = os.environ.copy()
    env.update(
        {
            "AUTOYOU_TEST_ROOT": str(runtime_root),
            "AUTOYOU_SERVER_PASSWORD": password,
            "AUTOYOU_INTERNET_SEARCH_ENABLED": "0",
            "AUTOYOU_SKIP_STARTUP_SERVICES": "1",
            "AUTOYOU_TEST_PAIR_PUBLIC_URL": "https://pairing.autoyou.test",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )

    process = subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    running = RunningBootstrap(
        process=process,
        ports=ports,
        password=password,
        log_path=log_path,
        log_handle=log_handle,
        opener=opener,
    )
    running.wait_for_admin_status(timeout=timeout)
    return running


def wait_for_json(
    url: str,
    *,
    process: subprocess.Popen[str] | None = None,
    log_path: Path | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            raise AssertionError(
                f"Process exited before {url} became reachable with code {process.returncode}.\n"
                f"{_log_excerpt(log_path)}"
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for {url}: {last_error}\n{_log_excerpt(log_path)}")


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _log_excerpt(log_path: Path | None, max_chars: int = 6000) -> str:
    if log_path is None or not log_path.exists():
        return ""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]
