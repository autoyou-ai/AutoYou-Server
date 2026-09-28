"""Own the included macOS Signal processes without a Docker installation."""
from __future__ import annotations

import base64
import contextlib
import json
import os
from pathlib import Path
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile

from shared.macos_runtime_support import find_app_bundle_resource, is_app_store_build
from shared.platform_runtime import get_runtime_root


def bundled_signal_root() -> Path | None:
    if sys.platform != "darwin":
        return None
    root = (find_app_bundle_resource("runtime/signal") if is_app_store_build()
            else get_runtime_root(__file__) / "signal")
    if root is None or not root.exists():
        return None
    root = root.resolve()
    for relative in ("signal-cli/bin/signal-cli", "java/bin/java", "rest-api",
                     "native/libsignal_jni.dylib", "native/libsqlitejdbc.dylib"):
        path = root / relative
        if not path.resolve().is_relative_to(root) or not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError("Signal is unavailable. Update or reinstall AutoYou.")
    manifest = root / "release.json"
    if not manifest.resolve().is_relative_to(root) or json.loads(manifest.read_text()).get("transport") != "unix-basic-fd-v1":
        raise RuntimeError("Signal is unavailable. Update or reinstall AutoYou.")
    return root


class NativeSignalRuntime:
    def __init__(self, root: Path, data_dir: Path, port: int):
        if not 0 < int(port) < 65536:
            raise ValueError("Signal port must be between 1 and 65535.")
        self.root = root.resolve()
        self.data_dir = data_dir.resolve()
        self.port = int(port)
        self.password = secrets.token_urlsafe(32)
        self.processes: list[subprocess.Popen] = []
        self._temporary: tempfile.TemporaryDirectory | None = None

    @property
    def headers(self) -> dict[str, str]:
        credential = base64.b64encode(("autoyou:" + self.password).encode()).decode()
        return {"Authorization": "Basic " + credential}

    @property
    def running(self) -> bool:
        return len(self.processes) == 2 and all(process.poll() is None for process in self.processes)

    def start(self) -> None:
        self.stop()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._temporary = tempfile.TemporaryDirectory(prefix=".signal-", dir=self.data_dir)
        transient = Path(self._temporary.name)
        # Relative socket names also work when Application Support exceeds the
        # macOS Unix socket path limit. An explicit parent avoids a CLI bug.
        socket_path = f"./{transient.name}/signal.sock"
        rpc_config = transient / "jsonrpc2.yml"
        rpc_config.write_text(json.dumps({"config": {"<multi-account>": {"tcp_port": 0}}}))
        environment = os.environ.copy()
        for name in ("JAVA_OPTS", "SIGNAL_CLI_OPTS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH",
                     "AUTO_RECEIVE_SCHEDULE", "SIGNAL_CLI_CMD_TIMEOUT", "RECEIVE_WEBHOOK_URL", "USE_NATIVE"):
            environment.pop(name, None)
        environment.update(JAVA_HOME=str(self.root / "java"),
            JAVA_OPTS=shlex.join([f"-Djava.library.path={self.root / 'native'}",
                                 f"-Dorg.sqlite.lib.path={self.root / 'native'}",
                                 f"-Djava.io.tmpdir={transient}"]),
            PATH=str(self.root / "signal-cli/bin") + os.pathsep + "/usr/bin:/bin",
            TMPDIR=str(transient), TMP=str(transient), TEMP=str(transient),
            XDG_DATA_HOME=str(self.data_dir), MODE="json-rpc", PORT=str(self.port),
            ENABLE_PLUGINS="false", GIN_MODE="release", LOG_LEVEL="warn",
            AUTOYOU_SIGNAL_PASSWORD=self.password, AUTOYOU_SIGNAL_RPC_SOCKET=socket_path,
            AUTOYOU_SIGNAL_RPC_CONFIG=str(rpc_config))
        commands = [
            [str(self.root / "signal-cli/bin/signal-cli"), "--scrub-log", "--config", str(self.data_dir),
             "--output=json", "daemon", "--socket", socket_path],
            [str(self.root / "rest-api"), "-signal-cli-config", str(self.data_dir),
             "-attachment-tmp-dir", str(transient), "-avatar-tmp-dir", str(transient)],
        ]
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Own the endpoint before launch; a different service cannot satisfy
            # readiness or receive account requests while our helper initializes.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", self.port))
            listener.listen()
            environment["AUTOYOU_SIGNAL_LISTEN_FD"] = str(listener.fileno())
            for index, command in enumerate(commands):
                # Keep the parent's process group so owned-server shutdown also
                # reaches these children if the server exits unexpectedly.
                self.processes.append(subprocess.Popen(command, cwd=self.data_dir, env=environment,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    pass_fds=(listener.fileno(),) if index == 1 else ()))
        except BaseException:
            self.stop()
            raise
        finally:
            listener.close()

    def stop(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
        for process in reversed(self.processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                process.wait(timeout=5)
        self.processes.clear()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
