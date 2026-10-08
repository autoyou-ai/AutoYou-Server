# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See the LICENSE file in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Own external HTTP backends declared by installed agent website manifests."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO, Dict, Optional

from shared.process_lifecycle import (
    OwnedProcessJob,
    add_parent_pid_environment,
    force_kill_process_tree,
    live_pids,
    process_spawn_kwargs,
    process_tree_pids,
)
from autoyou_agents.shared_tools._subprocess_env import scrubbed_subprocess_env


LOGGER = logging.getLogger(__name__)
_MANAGED_RUNTIME_KINDS = {"process", "docker-compose"}
_MAX_STARTUP_SECONDS = 600.0
_MAX_SHUTDOWN_SECONDS = 120.0


def _safe_relative_path(value: Any) -> Optional[str]:
    raw = str(value or ".").strip().replace("\\", "/")
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if not raw or posix.is_absolute() or windows.is_absolute() or windows.drive:
        return None
    if any(part in {"..", ""} for part in posix.parts):
        return None
    return "." if raw == "." else posix.as_posix()


def _seconds(value: Any, default: float, maximum: float) -> Optional[float]:
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 1 or parsed > maximum:
        return None
    return parsed


def normalize_managed_runtime(value: Any) -> Optional[Dict[str, Any]]:
    """Validate the small manifest contract for an owned process or Compose stack."""
    if not isinstance(value, dict):
        return None
    kind = str(value.get("type") or "").strip().lower()
    if kind not in _MANAGED_RUNTIME_KINDS:
        return None

    health_path = value.get("health_path")
    if health_path not in (None, ""):
        health_path = str(health_path).strip()
        if (
            not health_path.startswith("/")
            or len(health_path) > 512
            or any(ord(character) < 32 for character in health_path)
            or "#" in health_path
        ):
            return None
    else:
        health_path = None

    startup_timeout = _seconds(value.get("startup_timeout_seconds"), 90.0, _MAX_STARTUP_SECONDS)
    shutdown_timeout = _seconds(value.get("shutdown_timeout_seconds"), 10.0, _MAX_SHUTDOWN_SECONDS)
    if startup_timeout is None or shutdown_timeout is None:
        return None

    normalized: Dict[str, Any] = {
        "type": kind,
        "health_path": health_path,
        "startup_timeout_seconds": startup_timeout,
        "shutdown_timeout_seconds": shutdown_timeout,
    }
    if kind == "process":
        command = value.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part or "\x00" in part for part in command)
        ):
            return None
        working_directory = _safe_relative_path(value.get("working_directory", "."))
        if working_directory is None:
            return None
        normalized.update(command=list(command), working_directory=working_directory)
    else:
        compose_file = _safe_relative_path(value.get("compose_file"))
        if compose_file is None or compose_file == "." or Path(compose_file).suffix.lower() not in {".yml", ".yaml"}:
            return None
        service = value.get("service")
        if service not in (None, ""):
            service = str(service).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", service):
                return None
        build = value.get("build", True)
        if not isinstance(build, bool):
            return None
        normalized.update(compose_file=compose_file, service=service or None, build=build)
    return normalized


def _agent_path(agent_dir: Path, relative_path: str) -> Path:
    root = agent_dir.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError("managed runtime paths must stay inside the agent package") from None
    return candidate


def _project_name(agent_name: str, agent_dir: Path) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(agent_name or "agent").lower()).strip("-_") or "agent"
    instance = hashlib.sha256(os.fsencode(Path(agent_dir).resolve())).hexdigest()[:8]
    return f"autoyou-{slug[:24]}-{instance}"


def _service_environment(port: int) -> Dict[str, str]:
    environment = scrubbed_subprocess_env()
    environment.update(
        HOST="127.0.0.1",
        PORT=str(int(port)),
        AUTOYOU_HOST="127.0.0.1",
        AUTOYOU_PORT=str(int(port)),
    )
    return add_parent_pid_environment(environment)


def _is_port_ready(port: int, health_path: Optional[str]) -> bool:
    if not health_path:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                return True
        except OSError:
            return False

    request = urllib.request.Request(f"http://127.0.0.1:{int(port)}{health_path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=0.75) as response:
            return int(response.status) < 500
    except urllib.error.HTTPError as exc:
        return int(exc.code) < 500
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _process_group_alive(process_id: int) -> bool:
    if os.name == "nt":
        return False
    try:
        os.killpg(int(process_id), 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return False


@dataclass
class ManagedRuntimeHandle:
    """One process or Compose project started for a managed agent website."""

    kind: str
    agent_name: str
    port: int
    shutdown_timeout_seconds: float
    environment: Dict[str, str]
    log_file: Optional[BinaryIO] = None
    process: Optional[subprocess.Popen] = None
    process_job: Optional[OwnedProcessJob] = None
    compose_file: Optional[Path] = None
    compose_project: Optional[str] = None

    def is_alive(self) -> bool:
        if self.process is not None and self.process.poll() is not None:
            return False
        return _is_port_ready(self.port, None)


def is_managed_runtime_alive(handle: Any) -> bool:
    """Whether an owned process is running and its local HTTP port still answers."""
    return isinstance(handle, ManagedRuntimeHandle) and handle.is_alive()


async def _wait_until_ready(handle: ManagedRuntimeHandle, health_path: Optional[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if handle.process is not None and handle.process.poll() is not None:
            raise RuntimeError(
                f"managed runtime for {handle.agent_name} exited before listening on port {handle.port}"
            )
        if await asyncio.to_thread(_is_port_ready, handle.port, health_path):
            return
        await asyncio.sleep(0.25)
    raise TimeoutError(f"managed runtime for {handle.agent_name} did not become ready on port {handle.port}")


async def start_managed_runtime_service(
    spec: Dict[str, Any],
    *,
    agent_name: str,
    agent_dir: Path,
    port: int,
    log_path: Path,
    logger: logging.Logger = LOGGER,
) -> ManagedRuntimeHandle:
    """Start and health-check an installed agent's process or owned Compose project."""
    normalized = normalize_managed_runtime(spec)
    if normalized is None:
        raise ValueError(f"invalid managed runtime declaration for {agent_name}")
    root = Path(agent_dir).resolve()
    environment = _service_environment(port)
    log_file: Optional[BinaryIO] = None
    handle: Optional[ManagedRuntimeHandle] = None
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "ab", buffering=0)
    except OSError as exc:
        logger.warning("Could not open managed runtime log for %s: %s", agent_name, exc)

    try:
        if normalized["type"] == "process":
            working_directory = _agent_path(root, normalized["working_directory"])
            if not working_directory.is_dir():
                raise FileNotFoundError(f"managed runtime directory is missing for {agent_name}")
            command = [
                part.replace("{host}", "127.0.0.1").replace("{port}", str(int(port)))
                for part in normalized["command"]
            ]
            executable = command[0]
            if not Path(executable).is_absolute() and ("/" in executable or "\\" in executable or executable.startswith(".")):
                executable_path = _agent_path(root, executable)
                if not executable_path.is_file():
                    raise FileNotFoundError(f"managed runtime executable is missing for {agent_name}")
                command[0] = str(executable_path)
            process = subprocess.Popen(
                command,
                cwd=str(working_directory),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                **process_spawn_kwargs(hide_window=True),
            )
            handle = ManagedRuntimeHandle(
                kind="process",
                agent_name=agent_name,
                port=int(port),
                shutdown_timeout_seconds=normalized["shutdown_timeout_seconds"],
                environment=environment,
                log_file=log_file,
                process=process,
                process_job=OwnedProcessJob.for_process(process),
            )
        else:
            docker = shutil.which("docker")
            if not docker:
                raise FileNotFoundError("Docker Compose is unavailable")
            compose_file = _agent_path(root, normalized["compose_file"])
            if not compose_file.is_file():
                raise FileNotFoundError(f"managed Compose file is missing for {agent_name}")
            project = _project_name(agent_name, root)
            handle = ManagedRuntimeHandle(
                kind="docker-compose",
                agent_name=agent_name,
                port=int(port),
                shutdown_timeout_seconds=normalized["shutdown_timeout_seconds"],
                environment=environment,
                log_file=log_file,
                compose_file=compose_file,
                compose_project=project,
            )
            command = [docker, "compose", "--project-name", project, "--file", str(compose_file), "up", "--detach"]
            if normalized["build"]:
                command.append("--build")
            if normalized["service"]:
                command.append(normalized["service"])
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                cwd=str(root),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=normalized["startup_timeout_seconds"],
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Docker Compose failed to start {agent_name} (exit {result.returncode})")

        await _wait_until_ready(handle, normalized["health_path"], normalized["startup_timeout_seconds"])
        logger.info("Managed %s runtime started for %s on 127.0.0.1:%d", handle.kind, agent_name, port)
        return handle
    except BaseException:
        if handle is not None:
            await asyncio.to_thread(stop_managed_runtime_service, handle, logger)
        elif log_file is not None:
            log_file.close()
        raise


def _stop_process(handle: ManagedRuntimeHandle, logger: logging.Logger) -> bool:
    process = handle.process
    if process is None:
        return True
    descendants = process_tree_pids(process.pid)
    process_id = int(process.pid)
    deadline = time.monotonic() + handle.shutdown_timeout_seconds
    try:
        if process.poll() is None:
            if os.name == "nt":
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except (OSError, ValueError):
                    process.terminate()
            else:
                try:
                    os.killpg(process_id, signal.SIGTERM)
                except ProcessLookupError:
                    pass

        while time.monotonic() < deadline:
            root_alive = process.poll() is None
            children_alive = handle.process_job.active_processes() not in (None, 0) if handle.process_job else bool(live_pids(descendants))
            group_alive = _process_group_alive(process_id)
            if not root_alive and not children_alive and not group_alive:
                break
            time.sleep(0.1)

        root_alive = process.poll() is None
        if os.name == "nt" and handle.process_job is not None:
            remaining = handle.process_job.terminate_remaining(grace_seconds=0.0)
            handle.process_job.close()
            handle.process_job = None
            if root_alive:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            return remaining == 0 and process.poll() is not None

        if root_alive or _process_group_alive(process_id) or live_pids(descendants):
            force_kill_process_tree(process_id, extra_pids=descendants, process_group=(os.name != "nt"))
        if root_alive:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning("Managed runtime process for %s remained after forced cleanup", handle.agent_name)
        return process.poll() is not None and not live_pids(descendants) and not _process_group_alive(process_id)
    except Exception as exc:
        logger.warning("Could not stop managed runtime process for %s: %s", handle.agent_name, exc)
        if process.poll() is None:
            force_kill_process_tree(process_id, extra_pids=descendants, process_group=(os.name != "nt"))
        return process.poll() is not None


def _compose_command(handle: ManagedRuntimeHandle) -> list[str]:
    docker = shutil.which("docker")
    if not docker or handle.compose_file is None or not handle.compose_project:
        raise FileNotFoundError("Docker Compose is unavailable")
    return [
        docker,
        "compose",
        "--project-name",
        handle.compose_project,
        "--file",
        str(handle.compose_file),
        "down",
        "--remove-orphans",
        "--timeout",
        str(max(1, int(handle.shutdown_timeout_seconds))),
    ]


def stop_managed_runtime_service(
    handle: ManagedRuntimeHandle,
    logger: logging.Logger = LOGGER,
) -> bool:
    """Gracefully stop an owned process tree or only its namespaced Compose project."""
    try:
        if handle.kind == "docker-compose":
            try:
                result = subprocess.run(
                    _compose_command(handle),
                    cwd=str(handle.compose_file.parent if handle.compose_file else Path.cwd()),
                    env=handle.environment,
                    stdin=subprocess.DEVNULL,
                    stdout=handle.log_file,
                    stderr=subprocess.STDOUT,
                    timeout=handle.shutdown_timeout_seconds + 10,
                    check=False,
                )
                if result.returncode != 0:
                    logger.warning("Docker Compose did not fully stop %s (exit %d)", handle.agent_name, result.returncode)
                    return False
                logger.info("Stopped managed Docker Compose project for %s", handle.agent_name)
                return True
            except Exception as exc:
                logger.warning("Could not stop managed Docker Compose project for %s: %s", handle.agent_name, exc)
                return False
        stopped = _stop_process(handle, logger)
        if not stopped:
            logger.warning("Managed runtime process for %s required forced cleanup", handle.agent_name)
        else:
            logger.info("Stopped managed runtime process for %s", handle.agent_name)
        return stopped
    finally:
        if handle.log_file is not None:
            try:
                handle.log_file.close()
            except OSError:
                pass
