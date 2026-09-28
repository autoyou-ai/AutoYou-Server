# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""AI worker and interpreter process lifecycle helpers."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import re
import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional

from shared.platform_runtime import (
    get_logs_dir,
    normalize_local_filesystem_path,
    strip_windows_extended_path_prefix,
)
from shared.process_lifecycle import add_parent_pid_environment, process_spawn_kwargs
from shared.secure_storage import append_secure_file, secure_storage_enabled

_runtime_module: Optional[ModuleType] = None


def bind_runtime(module: ModuleType) -> None:
    """Bind the live server module so tests and runtime overrides stay visible."""
    global _runtime_module
    _runtime_module = module


def _runtime() -> ModuleType:
    if _runtime_module is None:
        import server

        bind_runtime(server)
    return _runtime_module


def _is_agent_process_running(process: Any) -> bool:
    if process is None:
        return False
    poll = getattr(process, "poll", None)
    if callable(poll):
        try:
            return poll() is None
        except Exception:
            return False
    is_alive = getattr(process, "is_alive", None)
    if callable(is_alive):
        try:
            return bool(is_alive())
        except Exception:
            return False
    return False


def _get_agent_process_pid(process: Any) -> Optional[int]:
    try:
        pid_value = getattr(process, "pid", None)
        return int(pid_value) if pid_value is not None else None
    except Exception:
        return None


def _looks_like_python_executable(executable: str) -> bool:
    try:
        executable_name = Path(str(executable or "")).name.lower()
    except Exception:
        executable_name = str(executable or "").lower()
    return executable_name in {
        "python",
        "python.exe",
        "python3",
        "python3.exe",
        "python3.11",
        "python3.12",
        "python3.13",
    } or executable_name.startswith("python3.")


def _compiled_runtime_cwd(value: Any) -> str:
    raw_value = strip_windows_extended_path_prefix(str(value or "").strip())
    if re.match(r"^[A-Za-z]:[\\/]", raw_value) or raw_value.startswith("\\\\"):
        return raw_value
    return str(normalize_local_filesystem_path(Path(raw_value)))


def _build_compiled_ai_agent_process_command(host: str, port: int, agent_dir: str) -> List[str]:
    runtime = _runtime()
    cmd = [sys.executable]
    if _looks_like_python_executable(sys.executable):
        cmd.append(str(normalize_local_filesystem_path(Path(runtime.__file__).resolve())))
    cmd.extend(
        [
            "--run-ai-agent-server",
            "--host",
            str(host),
            "--port",
            str(port),
            "--agent-dir",
            strip_windows_extended_path_prefix(agent_dir),
        ]
    )
    return cmd


def _start_compiled_ai_agent_process(
    host: str,
    port: int,
    agent_dir: str,
    env_vars: Dict[str, str],
) -> subprocess.Popen:
    runtime = _runtime()
    cmd = _build_compiled_ai_agent_process_command(host, port, agent_dir)
    log_handle = None
    log_path = None
    try:
        log_path = get_logs_dir("AutoYou", anchor=runtime.__file__) / f"packaged_ai_{int(port)}.log"
        if not secure_storage_enabled():
            log_handle = open(log_path, "ab", buffering=0)
    except Exception:
        log_handle = None

    popen_kwargs = {
        "cwd": _compiled_runtime_cwd(runtime.APP_ROOT),
        "env": add_parent_pid_environment(env_vars),
        **process_spawn_kwargs(hide_window=True),
    }
    if secure_storage_enabled() and log_path is not None:
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.STDOUT
    elif log_handle is not None:
        popen_kwargs["stdout"] = log_handle
        popen_kwargs["stderr"] = log_handle

    try:
        process = subprocess.Popen(cmd, **popen_kwargs)
        if secure_storage_enabled() and log_path is not None and hasattr(process.stdout, "readline"):
            def _capture_secure_ai_log() -> None:
                stream = process.stdout
                try:
                    for line in iter(stream.readline, b""):
                        payload = line.encode("utf-8", errors="replace") if isinstance(line, str) else bytes(line)
                        append_secure_file(log_path, payload)
                except Exception as exc:
                    runtime.LOGGER.warning("Protected AI worker log capture stopped: %s", exc)
                finally:
                    with suppress(Exception):
                        stream.close()

            threading.Thread(
                target=_capture_secure_ai_log,
                daemon=True,
                name="autoyou-secure-ai-log",
            ).start()
        return process
    finally:
        if log_handle is not None:
            with suppress(Exception):
                log_handle.close()


def _wait_for_agent_process_exit(process: Any, timeout: float) -> None:
    wait = getattr(process, "wait", None)
    if callable(wait):
        wait(timeout=timeout)
        return
    join = getattr(process, "join", None)
    if callable(join):
        join(timeout=timeout)


def _kill_agent_process(process: Any) -> None:
    kill = getattr(process, "kill", None)
    if callable(kill):
        kill()
        return
    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        terminate()


def _collect_process_tree_pids(process_pid: Optional[int]) -> List[int]:
    if process_pid is None:
        return []
    try:
        pid = int(process_pid)
    except Exception:
        return []
    if pid <= 0:
        return []

    pids = [pid]
    try:
        import psutil
        pids.extend(int(child.pid) for child in psutil.Process(pid).children(recursive=True))
    except Exception:
        pass
    return list(dict.fromkeys(pids))


def _close_process_handle(process: Any) -> None:
    close = getattr(process, "close", None)
    if callable(close):
        with suppress(Exception):
            close()


def _looks_like_lingering_ai_agent_process(proc: Any, expected_port: int) -> bool:
    runtime = _runtime()
    try:
        pid = int(getattr(proc, "pid", 0) or 0)
        if pid <= 0 or pid == os.getpid():
            return False
        cmdline = [str(part or "") for part in (proc.cmdline() or [])]
    except Exception:
        return False

    lowered = [part.strip().lower() for part in cmdline if part]
    if "--run-ai-agent-server" not in lowered:
        return runtime._looks_like_orphaned_source_ai_agent_process(proc, cmdline, expected_port)
    if "--port" in lowered:
        try:
            return int(str(cmdline[lowered.index("--port") + 1]).strip()) == int(expected_port)
        except Exception:
            return False
    return True


def _pid_is_running(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        return False


def _process_listens_on_port(proc: Any, expected_port: int) -> bool:
    conn_getter = getattr(proc, "net_connections", None) or getattr(proc, "connections", None)
    if not callable(conn_getter):
        return False
    try:
        connections = conn_getter(kind="inet")
    except TypeError:
        try:
            connections = conn_getter()
        except Exception:
            return False
    except Exception:
        return False

    for conn in connections or []:
        try:
            status = str(getattr(conn, "status", "") or "").upper()
            laddr = getattr(conn, "laddr", None)
            port = getattr(laddr, "port", None)
            if port is None and isinstance(laddr, (tuple, list)) and len(laddr) >= 2:
                port = laddr[1]
            if int(port) == int(expected_port) and status in {"LISTEN", "CONN_LISTEN"}:
                return True
        except Exception:
            continue
    return False


def _process_cwd_is_under_app_root(proc: Any) -> bool:
    cwd_getter = getattr(proc, "cwd", None)
    if not callable(cwd_getter):
        return False
    try:
        cwd = Path(str(cwd_getter())).resolve()
        root = Path(str(_runtime().APP_ROOT)).resolve()
    except Exception:
        return False
    return cwd == root or root in cwd.parents


def _looks_like_orphaned_source_ai_agent_process(
    proc: Any,
    cmdline: List[str],
    expected_port: int,
) -> bool:
    runtime = _runtime()
    command_text = " ".join(cmdline).lower()
    if "multiprocessing.spawn" not in command_text or "spawn_main" not in command_text:
        return False
    parent_match = re.search(r"parent_pid=(\d+)", command_text)
    if parent_match and runtime._pid_is_running(int(parent_match.group(1))):
        return False
    return runtime._process_cwd_is_under_app_root(proc) and runtime._process_listens_on_port(proc, expected_port)


def _cleanup_lingering_ai_agent_processes(expected_port: int) -> int:
    runtime = _runtime()
    try:
        import psutil
    except ImportError:
        return 0

    cleaned = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        if not runtime._looks_like_lingering_ai_agent_process(proc, expected_port):
            continue
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                runtime.LOGGER.debug("Failed to stop lingering AI Agent process PID %s", getattr(proc, "pid", "?"))
                continue
        cleaned += 1
    return cleaned


def _is_ai_agent_server_healthy(host: str, port: int, timeout: float = 0.75) -> bool:
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=timeout) as response:  # nosec B310
            return int(getattr(response, "status", 0)) == 200
    except (urllib.error.URLError, ValueError, OSError):
        return False


def _get_ai_agent_startup_timeout_seconds() -> float:
    try:
        parsed = float(str(os.getenv("AUTOYOU_AI_AGENT_STARTUP_TIMEOUT_SECONDS", "60")).strip())
        if parsed > 0:
            return parsed
    except Exception:
        pass
    return 60.0


async def _wait_for_ai_agent_server_ready(
    host: str,
    port: int,
    *,
    timeout_seconds: Optional[float] = None,
    process: Any = None,
) -> bool:
    runtime = _runtime()
    deadline = time.monotonic() + (
        runtime._get_ai_agent_startup_timeout_seconds() if timeout_seconds is None else float(timeout_seconds)
    )
    while time.monotonic() < deadline:
        if await asyncio.to_thread(runtime._is_ai_agent_server_healthy, host, port):
            return True
        if process is not None and not runtime._is_agent_process_running(process):
            return False
        await asyncio.sleep(0.5)
    return False


def _list_non_daemon_runtime_threads() -> List[str]:
    current = threading.current_thread()
    main = threading.main_thread()
    return [
        str(getattr(thread, "name", "") or f"Thread-{getattr(thread, 'ident', 'unknown')}")
        for thread in list(threading.enumerate())
        if thread is not current
        and thread is not main
        and thread.is_alive()
        and not getattr(thread, "daemon", False)
    ]


def _terminate_active_multiprocessing_children(timeout: float = 5.0) -> bool:
    runtime = _runtime()
    clean = True
    try:
        children = list(multiprocessing.active_children())
    except Exception as exc:
        runtime.LOGGER.debug("Could not inspect multiprocessing children during exit: %s", exc)
        return True

    for child in children:
        try:
            child_pid = runtime._get_agent_process_pid(child)
            tracked_pids = runtime._collect_process_tree_pids(child_pid)
            if runtime._is_agent_process_running(child):
                with suppress(Exception):
                    child.terminate()
                runtime._wait_for_agent_process_exit(child, timeout)
            if runtime._is_agent_process_running(child):
                clean = False
                runtime._kill_agent_process(child)
                runtime._wait_for_agent_process_exit(child, min(timeout, 2.0))
            if runtime._is_agent_process_running(child):
                clean = False
                runtime._force_kill_process_tree(child_pid, tracked_pids)
            runtime._close_process_handle(child)
        except Exception as exc:
            clean = False
            runtime.LOGGER.warning("Failed to clean multiprocessing child during exit: %s", exc)
    return clean


def _flush_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):
            if stream is not None:
                stream.flush()


def _cleanup_loky_executor() -> None:
    try:
        from joblib.externals.loky import get_reusable_executor
        executor = get_reusable_executor()
    except Exception:
        return

    done = threading.Event()

    def _shutdown_executor() -> None:
        try:
            executor.shutdown(wait=True, kill_workers=True)
        except Exception:
            with suppress(Exception):
                executor.shutdown(wait=False, kill_workers=True)
        finally:
            done.set()

    shutdown_thread = threading.Thread(target=_shutdown_executor, name="loky-shutdown", daemon=True)
    shutdown_thread.start()
    shutdown_thread.join(timeout=3.0)
    if not done.is_set():
        _runtime().LOGGER.debug("Timed out waiting for loky reusable executor shutdown")


def _finalize_runtime_process_exit(exit_code: int = 0) -> int:
    runtime = _runtime()
    runtime._cleanup_loky_executor()
    children_clean = runtime._terminate_active_multiprocessing_children(timeout=3.0)
    lingering_threads = runtime._list_non_daemon_runtime_threads()
    if children_clean and not lingering_threads:
        return int(exit_code)
    if lingering_threads:
        runtime.LOGGER.warning(
            "Lingering non-daemon runtime threads detected during process exit: %s",
            ", ".join(lingering_threads),
        )
    if not children_clean:
        runtime.LOGGER.warning("Lingering multiprocessing children detected during process exit; forcing runtime exit")
    runtime._flush_standard_streams()
    runtime.os._exit(int(exit_code))


def _mark_runtime_exit_complete() -> None:
    runtime = _runtime()
    runtime._RUNTIME_EXIT_COMPLETE.set()
    with runtime._SHUTDOWN_WATCHDOG_LOCK:
        watchdog = runtime._SHUTDOWN_WATCHDOG
        runtime._SHUTDOWN_WATCHDOG = None
    if watchdog is not None:
        watchdog.cancel()


async def _stop_cloud_sse_listener() -> bool:
    runtime = _runtime()
    task = runtime.STATE.cloud_sse_task
    runtime.STATE.cloud_sse_task = None
    runtime.STATE.cloud_connected = False
    runtime.STATE.cloud_last_sse_activity_at = 0.0
    return await runtime._cancel_asyncio_task(task, "AutoYou Cloud SSE listener", timeout=5.0)


async def _stop_scheduler_service() -> bool:
    """Cancel the background scheduler task gracefully."""
    runtime = _runtime()
    task = runtime.STATE.scheduler_task
    runtime.STATE.scheduler_task = None
    return await runtime._cancel_asyncio_task(task, "Scheduler Service", timeout=5.0)


def run_agent_server(host: str, port: int, agent_dir: str, env_vars: dict):
    """
    This function runs in a separate process and hosts the AI agent's FastAPI app.

    Serves two listeners against the SAME app instance, mirroring the admin
    server's plain-HTTP/HTTPS pattern (this module's admin 8001/8443 wiring):
    the plain-HTTP listener on ``host``:``port`` (today's 8081 behavior --
    loopback by default, unauthenticated, unchanged), plus an additive
    HTTPS+OTP listener bound to the admin bind host when
    ``AUTOYOU_AI_AGENT_LAN_ACCESS`` was explicitly opted into. The vendored
    ADK app itself is never modified -- the OTP gate is middleware layered
    on in ``attach_ai_agent_endpoints`` (``routers/ai_agent.py``), and only
    enforces on the LAN/HTTPS listener; the plain loopback listener stays
    exactly as it always has.
    """
    runtime = _runtime()
    import os
    import asyncio
    import logging
    import uvicorn
    from google.adk.cli.fast_api import get_fast_api_app

    # Isolate and explicitly set environment variables for this process
    os.environ.clear()
    os.environ.update(env_vars)
    os.environ["AUTOYOU_AI_AGENT_WORKER"] = "1"
    runtime.start_parent_process_watchdog()

    if os.getenv("AUTOYOU_SECURE_STORAGE_MODE") == runtime.SECURE_PROFESSIONAL_MAXIMUS_MODE:
        runtime.enable_secure_storage(
            app_name=os.getenv("AUTOYOU_SECURE_STORAGE_APP") or "AutoYou",
            root=os.getenv("AUTOYOU_SECURE_STORAGE_ROOT") or runtime._CONFIG_DIR,
            password=os.getenv("AUTOYOU_SECURE_STORAGE_PASSWORD")
            or os.getenv("AUTOYOU_SERVER_PASSWORD"),
            operation_timeout_seconds=runtime._macos_keychain_bootstrap_timeout_seconds(),
            allow_key_creation=False,
        )
        if str(os.getenv("AUTOYOU_MEMORY_BACKEND") or "legacy").strip().lower() == "cognee":
            raise runtime.SecureStorageError(
                "Secure Professional Maximus does not allow unsealed Cognee filesystem state"
            )

    if runtime.is_compiled():
        os.environ["LITELLM_MODE"] = "PRODUCTION"
        os.environ["ADK_DISABLE_LOAD_DOTENV"] = "1"
        os.environ["PYTHON_DOTENV_DISABLED"] = "1"

    logging.basicConfig(level=logging.INFO)
    agent_logger = logging.getLogger("autoyou.agent_server")
    agent_logger.info(f"AI Agent process started, preparing to run on {host}:{port}")
    resolved_agent_dir = str(runtime.normalize_local_filesystem_path(runtime.resolve_adk_agents_base_dir(runtime.__file__, agent_dir)))
    if resolved_agent_dir != str(agent_dir):
        agent_logger.info(
            "Normalized ADK agents_dir from %s to %s",
            agent_dir,
            resolved_agent_dir,
        )
    else:
        agent_logger.info("Using ADK agents_dir=%s", resolved_agent_dir)

    try:
        # Import endpoints locally to avoid circular import loops when module executes
        from server import attach_ai_agent_endpoints

        agent_app = runtime._create_ai_agent_fastapi_app(
            get_fast_api_app,
            agent_dir=resolved_agent_dir,
            agent_logger=agent_logger,
        )
        attach_ai_agent_endpoints(agent_app)

        lan_access_enabled = os.getenv("AUTOYOU_AI_AGENT_LAN_ACCESS") == "1"

        async def _serve() -> None:
            config = uvicorn.Config(agent_app, host=host, port=port, log_level="info", access_log=False)
            server = uvicorn.Server(config)

            # Opt-in additive HTTPS+OTP listener, run in PARALLEL with the plain
            # listener above -- enabling it only ADDS a LAN-reachable surface and
            # never changes the existing loopback-only 8081 behavior.
            https_task = None
            if lan_access_enabled:
                try:
                    from shared import local_tls

                    tls_material = local_tls.ensure_enabled(runtime._CONFIG_DIR)
                    lan_https_port = int(os.getenv("AUTOYOU_AI_AGENT_LAN_HTTPS_PORT", "8481"))
                    https_config = uvicorn.Config(
                        agent_app,
                        host=runtime.SERVER_BIND_HOST,
                        port=lan_https_port,
                        log_level="warning",
                        access_log=False,
                        ssl_certfile=str(tls_material.server_cert_path),
                        ssl_keyfile=str(tls_material.server_key_path),
                    )
                    https_server = uvicorn.Server(https_config)
                    https_task = asyncio.ensure_future(https_server.serve())
                    agent_logger.info(
                        "Opt-in HTTPS+OTP AI Agent listener started on %s:%s",
                        runtime.SERVER_BIND_HOST, lan_https_port,
                    )
                except Exception as exc:
                    agent_logger.error(
                        "Failed to start opt-in HTTPS AI Agent listener: %s", exc, exc_info=True
                    )

            try:
                agent_logger.info(f"Starting AI Agent server on {host}:{port}")
                await server.serve()
            finally:
                if https_task is not None:
                    https_task.cancel()
                    try:
                        await https_task
                    except (asyncio.CancelledError, Exception):
                        pass

        asyncio.run(_serve())
        agent_logger.info("AI Agent server has stopped.")

    except Exception as e:
        agent_logger.error(f"An error occurred in the AI Agent server process: {e}", exc_info=True)


def _force_kill_process_tree(process_pid: Optional[int], extra_pids: Optional[List[int]] = None) -> None:
    runtime = _runtime()
    runtime.force_kill_process_tree(
        process_pid,
        extra_pids=extra_pids or (),
        process_group=runtime.is_compiled(),
    )


def _probe_ollama_runtime(api_base: str, *, timeout_seconds: float = 1.5) -> tuple[bool, Set[str]]:
    runtime = _runtime()
    import urllib.error
    import urllib.request

    normalized_base = str(api_base or "http://localhost:11434").rstrip("/")
    if not normalized_base.startswith(("http://", "https://")):
        return False, set()
    request = urllib.request.Request(
        f"{normalized_base}/api/tags",
        headers={"User-Agent": "AutoYou-Ollama-Healthcheck"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
            payload = runtime.json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return False, set()

    installed_models: runtime.Set[str] = set()
    for item in payload.get("models", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        model_name = str(item.get("model") or item.get("name") or "").strip()
        if model_name:
            installed_models.add(model_name)
    return True, installed_models


def _log_missing_ollama_model(api_base: str, configured_model: str, installed_models: Set[str]) -> None:
    runtime = _runtime()
    if not configured_model or configured_model in installed_models:
        return
    if installed_models:
        runtime.LOGGER.warning(
            "Configured Ollama model %s is not installed at %s. Installed models: %s",
            configured_model,
            api_base,
            sorted(installed_models),
        )
        return
    runtime.LOGGER.warning(
        "Ollama runtime is reachable at %s but no local models are installed. "
        "Pull %s or choose another provider in the Admin Dashboard.",
        api_base,
        configured_model,
    )


def _launch_ollama_background(ollama_executable: str) -> None:
    runtime = _runtime()
    popen_kwargs: runtime.Dict[str, runtime.Any] = {
        "stdout": runtime.subprocess.DEVNULL,
        "stderr": runtime.subprocess.DEVNULL,
    }
    if runtime.os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(runtime.subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(runtime.subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True
    runtime.subprocess.Popen([ollama_executable, "serve"], **popen_kwargs)


def _ensure_local_ollama_runtime_ready() -> None:
    runtime = _runtime()

    provider = str(runtime.os.getenv("AI_PROVIDER", "") or "").strip().lower()
    if provider not in {"ollama", "ollama_gateway"}:
        return

    api_base = str(runtime.os.getenv("OLLAMA_API_BASE", "http://localhost:11434") or "http://localhost:11434").strip()
    if not runtime._is_local_ollama_api_base(api_base):
        runtime.LOGGER.info("Skipping Ollama auto-start because api base is remote: %s", api_base)
        return

    configured_model = runtime._normalize_ollama_model_reference(
        runtime.os.getenv("OLLAMA_MODEL", runtime.DEFAULT_WIZARD_MODEL)
    )
    runtime_available, installed_models = runtime._probe_ollama_runtime(api_base)
    if runtime_available:
        runtime._log_missing_ollama_model(api_base, configured_model, installed_models)
        return

    now = runtime.time.monotonic()
    if (now - runtime._OLLAMA_AUTOSTART_LAST_ATTEMPT_AT) < runtime._OLLAMA_AUTOSTART_THROTTLE_SECONDS:
        runtime.LOGGER.info("Skipping duplicate Ollama auto-start attempt for %s", api_base)
        return
    runtime._OLLAMA_AUTOSTART_LAST_ATTEMPT_AT = now

    ollama_executable = runtime.shutil.which("ollama")
    if not ollama_executable:
        bundled_ollama = runtime.find_bundled_ollama_executable(runtime.__file__)
        if bundled_ollama is not None:
            ollama_executable = str(bundled_ollama)
    if not ollama_executable:
        runtime.LOGGER.warning(
            "Ollama provider is selected but the Ollama CLI was not found. "
            "Install Ollama from https://ollama.com/download, bundle runtime/ollama/ollama.exe, "
            "or choose another provider."
        )
        return

    runtime.LOGGER.info(
        "Ollama provider selected and runtime is not reachable at %s. Launching %s serve in the background.",
        api_base,
        ollama_executable,
    )
    try:
        runtime._launch_ollama_background(ollama_executable)
    except Exception as exc:
        runtime.LOGGER.warning("Failed to launch Ollama automatically: %s", exc)
        return

    for _ in range(5):
        runtime.time.sleep(1.0)
        runtime_available, installed_models = runtime._probe_ollama_runtime(api_base)
        if runtime_available:
            runtime.LOGGER.info("Ollama runtime responded after auto-start attempt at %s", api_base)
            runtime._log_missing_ollama_model(api_base, configured_model, installed_models)
            return

    runtime.LOGGER.warning(
        "Ollama was launched but is still not responding at %s. Open the Ollama app or run 'ollama serve' manually.",
        api_base,
    )


async def _cloud_sse_listener_loop():
    """Persistent SSE listener that receives relay messages from AutoYou Cloud.

    ══════════════════════════════════════════════════════════════════════════════════
    PRIVACY GUARANTEE: This function is ONLY executed if server_token exists in config.
    server_token is ONLY populated when the user explicitly completes the Cloud Pair
    link flow via /api/cloud/link-start. No automatic cloud polling occurs during
    startup or normal operation without explicit user enrollment in Cloud Pair.
    ══════════════════════════════════════════════════════════════════════════════════

    On a 401 (token rejected / revoked) the loop backs off exponentially and
    exits after 3 consecutive rejections so that the logs are not flooded when
    a server's token is intentionally invalidated by registering a new machine.
    The user must re-run the Cloud Pair link flow to obtain a fresh token.
    """
    runtime = _runtime()
    import ssl
    import aiohttp

    try:
        import certifi as _certifi
        _ssl_ctx = ssl.create_default_context(cafile=_certifi.where())
    except ImportError:
        _ssl_ctx = ssl.create_default_context()

    _consecutive_401s = 0
    _MAX_401s = 3
    _401_backoff = 60  # seconds; doubles each time
    _read_timeout = max(1.0, float(runtime.AUTOYOU_CLOUD_SSE_SOCK_READ_TIMEOUT_SECONDS))
    _reconnect_initial = max(0.1, float(runtime.AUTOYOU_CLOUD_SSE_RECONNECT_INITIAL_SECONDS))
    _reconnect_max = max(_reconnect_initial, float(runtime.AUTOYOU_CLOUD_SSE_RECONNECT_MAX_SECONDS))
    _reconnect_delay = _reconnect_initial
    _shared_key_registered = False
    # Expected long-poll churn (cloud restarts, idle proxies cutting the chunked
    # stream, read timeouts). Resolved via getattr so a reduced aiohttp test
    # surface cannot break the listener.
    _transient_stream_errors = tuple(
        exc
        for exc in (
            getattr(aiohttp, "ClientPayloadError", None),
            getattr(aiohttp, "ClientConnectionError", None),
            runtime.asyncio.TimeoutError,
            ConnectionError,
        )
        if isinstance(exc, type) and issubclass(exc, BaseException)
    )

    while True:
        cloud_cfg = (runtime.STATE.config or {}).get("cloud", {})
        server_token = cloud_cfg.get("server_token", "")
        if not server_token:
            await runtime.asyncio.sleep(30)
            continue
        if cloud_cfg.get("pair_enabled") is False:
            runtime.STATE.cloud_connected = False
            runtime.STATE.cloud_last_sse_activity_at = 0.0
            return
        if runtime._cloud_server_token_expired(cloud_cfg):
            runtime.LOGGER.warning(
                "AutoYou Cloud: saved server token is older than the local max age; re-link Cloud Pair to continue."
            )
            runtime.STATE.cloud_connected = False
            runtime.STATE.cloud_last_sse_activity_at = 0.0
            runtime.STATE.cloud_token_rejected = True
            return
        if await runtime._rotate_cloud_server_token_if_due(cloud_cfg):
            cloud_cfg = (runtime.STATE.config or {}).get("cloud", {})
            server_token = cloud_cfg.get("server_token", "")
            if not server_token:
                await runtime.asyncio.sleep(30)
                continue
        if not _shared_key_registered:
            _shared_key_registered = await runtime._register_shared_device_public_key()

        url = f"{runtime.AUTOYOU_CLOUD_BASE}/v1/server/events"
        headers = {"Authorization": f"Bearer {server_token}", "Accept": "text/event-stream"}

        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_ssl_ctx)) as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=_read_timeout),
                ) as resp:
                    if resp.status == 401:
                        server_id = str(cloud_cfg.get("server_id", "") or "").strip()
                        if server_id:
                            info, status_code = await runtime._fetch_cloud_server_info_from_cloud(
                                server_id,
                                server_token,
                            )
                            if info is not None and info.get("is_active") is False:
                                runtime.LOGGER.info(
                                    "AutoYou Cloud: SSE stopped because another linked server is active (server_id=%s).",
                                    server_id,
                                )
                                runtime.STATE.cloud_connected = False
                                runtime.STATE.cloud_last_sse_activity_at = 0.0
                                runtime.STATE.cloud_token_rejected = False
                                return
                            if status_code == 200:
                                runtime.LOGGER.warning(
                                    "AutoYou Cloud: SSE rejected a token that still resolves in server info; retrying.",
                                )
                                runtime.STATE.cloud_connected = False
                                runtime.STATE.cloud_last_sse_activity_at = 0.0
                                await runtime.asyncio.sleep(_reconnect_delay)
                                _reconnect_delay = min(_reconnect_delay * 2, _reconnect_max)
                                continue
                        _consecutive_401s += 1
                        if _consecutive_401s == 1:
                            # Log once, clearly
                            runtime.LOGGER.warning(
                                "AutoYou Cloud: server token rejected (attempt %d/%d). "
                                "The saved cloud session may have expired or been replaced. "
                                "The admin Cloud card will show whether to activate this server or link again.",
                                _consecutive_401s, _MAX_401s,
                            )
                        if _consecutive_401s >= _MAX_401s:
                            runtime.LOGGER.warning(
                                "AutoYou Cloud: token rejected %d times consecutively - "
                                "stopping SSE listener. Re-register this server to resume.",
                                _consecutive_401s,
                            )
                            runtime.STATE.cloud_connected = False
                            runtime.STATE.cloud_last_sse_activity_at = 0.0
                            runtime.STATE.cloud_token_rejected = True
                            return  # Exit the loop entirely; task will be garbage-collected
                        await runtime.asyncio.sleep(_401_backoff)
                        _401_backoff = min(_401_backoff * 2, 600)  # Cap at 10 minutes
                        continue

                    if resp.status != 200:
                        runtime.LOGGER.warning(
                            "AutoYou Cloud: SSE connect failed with HTTP %s. Reconnecting in %gs...",
                            resp.status,
                            _reconnect_delay,
                        )
                        runtime.STATE.cloud_connected = False
                        runtime.STATE.cloud_last_sse_activity_at = 0.0
                        await runtime.asyncio.sleep(_reconnect_delay)
                        _reconnect_delay = min(_reconnect_delay * 2, _reconnect_max)
                        continue

                    # Successful connection: reset auth counters and rejection flag.
                    # The network reconnect delay resets once SSE activity arrives.
                    _consecutive_401s = 0
                    _401_backoff = 60
                    runtime.STATE.cloud_connected = True
                    runtime.STATE.cloud_token_rejected = False
                    runtime.STATE.cloud_last_sse_activity_at = runtime.time.time()
                    runtime.LOGGER.info(f"AutoYou Cloud: SSE connected to {url}")

                    # Fire one privacy-minimal diagnostics heartbeat once we
                    # know the cloud is reachable. The helper de-dupes by
                    # last-reported NAT class so reconnects after a brief
                    # network blip don't generate spurious heartbeats.
                    runtime.asyncio.create_task(runtime._maybe_send_diagnostics_heartbeat())

                    event_type = None
                    data_lines = []

                    async for line_bytes in resp.content:
                        runtime.STATE.cloud_last_sse_activity_at = runtime.time.time()
                        _reconnect_delay = _reconnect_initial
                        line = line_bytes.decode("utf-8").rstrip("\n\r")
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].strip())
                        elif line == "":
                            # End of SSE event
                            if event_type and data_lines:
                                data_str = "\n".join(data_lines)
                                runtime.asyncio.create_task(runtime._handle_cloud_relay_event(event_type, data_str, server_token))
                            event_type = None
                            data_lines = []

                    # Stream ended normally (server closed connection) - back off before reconnecting
                    runtime.LOGGER.info(
                        "AutoYou Cloud: SSE stream ended. Reconnecting in %gs...",
                        _reconnect_delay,
                    )
                    runtime.STATE.cloud_connected = False
                    runtime.STATE.cloud_last_sse_activity_at = 0.0
                    await runtime.asyncio.sleep(_reconnect_delay)
                    _reconnect_delay = min(_reconnect_delay * 2, _reconnect_max)
        except runtime.asyncio.CancelledError:
            runtime.LOGGER.info("AutoYou Cloud: SSE listener cancelled.")
            runtime.STATE.cloud_connected = False
            runtime.STATE.cloud_last_sse_activity_at = 0.0
            return
        except _transient_stream_errors as e:
            # Long-lived SSE streams are routinely cut mid-chunk by cloud
            # restarts, idle proxies, or NAT timeouts (seen as
            # "Response payload is not completed" / TransferEncodingError).
            # This is expected churn, not an error: reconnect quietly.
            runtime.LOGGER.info(
                "AutoYou Cloud: SSE stream interrupted (%s). Reconnecting in %gs...",
                e,
                _reconnect_delay,
            )
            runtime.STATE.cloud_connected = False
            runtime.STATE.cloud_last_sse_activity_at = 0.0
            await runtime.asyncio.sleep(_reconnect_delay)
            _reconnect_delay = min(_reconnect_delay * 2, _reconnect_max)
        except Exception as e:
            runtime.LOGGER.warning(
                "AutoYou Cloud: SSE connection lost: %s. Reconnecting in %gs...",
                e,
                _reconnect_delay,
            )
            runtime.STATE.cloud_connected = False
            runtime.STATE.cloud_last_sse_activity_at = 0.0
            await runtime.asyncio.sleep(_reconnect_delay)
            _reconnect_delay = min(_reconnect_delay * 2, _reconnect_max)


async def _start_cloud_sse_listener():
    """Start or restart the AutoYou Cloud SSE listener task."""
    runtime = _runtime()
    if runtime.STATE.cloud_sse_task and not runtime.STATE.cloud_sse_task.done():
        runtime.STATE.cloud_sse_task.cancel()
        try:
            await runtime.STATE.cloud_sse_task
        except runtime.asyncio.CancelledError:
            pass
    cloud_cfg = (runtime.STATE.config or {}).get("cloud", {})
    if cloud_cfg.get("pair_enabled") is False:
        runtime.STATE.cloud_sse_task = None
        runtime.STATE.cloud_connected = False
        runtime.STATE.cloud_last_sse_activity_at = 0.0
        runtime.LOGGER.info("AutoYou account linked for updates; paid Cloud Pair listener is disabled.")
        return
    runtime.STATE.cloud_sse_task = runtime.asyncio.create_task(runtime._cloud_sse_listener_loop())
    runtime.LOGGER.info("AutoYou Cloud: SSE listener task started.")


def _startup_services_are_skipped() -> bool:
    runtime = _runtime()
    return runtime.os.getenv("AUTOYOU_SKIP_STARTUP_SERVICES", "").strip().lower() in {"1", "true", "yes", "on"}


def _start_ai_when_startup_services_are_skipped() -> bool:
    runtime = _runtime()
    return (_startup_services_are_skipped()
            and runtime.os.getenv("AUTOYOU_START_AI_WHEN_STARTUP_SERVICES_SKIPPED", "").strip().lower()
            in {"1", "true", "yes", "on"})


async def _initialize_services_on_startup() -> None:
    """Initialize services (Telegram, Signal, WhatsApp, AI Agent) when config is available at startup."""
    runtime = _runtime()
    total_steps = 8
    try:
        # Pairing callbacks are process-local and must be wired even when the
        # rest of startup was already completed by an earlier bootstrap path.
        # Keep this before the initialized fast-path so Local/Auto Pair cannot
        # silently lose the shared router after a warm unlock.
        try:
            runtime._configure_pairing_router_helpers()
            runtime.LOGGER.info("Pairing router configured.")
        except Exception as e:
            runtime.LOGGER.warning(f"Failed to configure pairing router: {e}")

        if runtime.STATE.initialized_services_on_startup:
            runtime.LOGGER.info("Services already initialized on startup. Skipping.")
            runtime._update_startup_status(
                status="complete",
                headline="Initialization already complete",
                detail="Services are already running. Opening the dashboard.",
                step=total_steps,
                total_steps=total_steps,
            )
            return

        if _startup_services_are_skipped():
            try:
                runtime._configure_pairing_router_helpers()
                runtime.LOGGER.info("Pairing router configured.")
            except Exception as e:
                runtime.LOGGER.warning(f"Failed to configure pairing router: {e}")
            runtime.STATE.initialized_services_on_startup = True
            runtime._update_startup_status(
                status="complete",
                headline="Initialization skipped",
                detail="Runtime service startup was skipped by AUTOYOU_SKIP_STARTUP_SERVICES.",
                step=total_steps,
                total_steps=total_steps,
            )
            runtime.LOGGER.info("Runtime service startup skipped by AUTOYOU_SKIP_STARTUP_SERVICES.")
            return

        # Force enable Tunnelmole if CLI flag is present
        runtime._update_startup_status(
            status="initializing",
            headline="Applying runtime configuration",
            detail="Loading provider settings, tunnel configuration, and service preferences.",
            step=2,
            total_steps=total_steps,
        )
        if "--tunnelmole" in runtime.sys.argv:
            if not runtime.STATE.config: runtime.STATE.config = {}
            if "tunnelmole" not in runtime.STATE.config: runtime.STATE.config["tunnelmole"] = {}
            runtime.STATE.config["tunnelmole"]["enabled"] = True
            runtime.LOGGER.info("Tunnelmole enabled via CLI flag")

        # Apply tunnelmole timeout from config if set
        tm_timeout = (runtime.STATE.config.get("tunnelmole", {}) or {}).get("timeout_minutes")
        if tm_timeout is not None:
            runtime.STATE.tunnelmole_timeout_minutes = int(tm_timeout)
            runtime.LOGGER.info(f"Tunnelmole timeout set to {tm_timeout} minutes from config")

        # Apply Google API configuration from config to environment variables
        runtime._apply_google_api_config_to_env()
        if str(runtime.os.getenv("AI_PROVIDER", "") or "").strip().lower() == "ollama_gateway":
            await runtime.asyncio.to_thread(runtime._ensure_local_ollama_runtime_ready)

        # Initialize service manager with proper configuration
        from service_manager import initialize_services, ServiceConfig, update_service_config

        # Get AI Agent configuration for record_messages_in_database setting
        ai_agent_config = runtime.STATE.config.get("ai_agent", {}) if runtime.STATE.config else {}
        ai_agent_record_messages = ai_agent_config.get("record_messages_in_database", True)
        ai_agent_port = runtime._get_ai_agent_api_port()
        ai_agent_internet_enabled = ai_agent_config.get("internet_search_enabled", True)
        audio_playback_enabled = runtime._get_audio_playback_enabled(cfg=(runtime.STATE.config or {}))
        runtime._resolve_audio_playback_music_library_dirs(cfg=(runtime.STATE.config or {}))
        runtime.set_audio_playback_enabled_env(bool(audio_playback_enabled))

        # Configure services based on server settings
        service_config = ServiceConfig(
            db_path=str(runtime._resolve_ai_agent_memory_db_path()),
            ai_agent_server_port=ai_agent_port,
            record_messages=ai_agent_record_messages,  # Use the record_messages_in_database setting
            internet_search_enabled=bool(ai_agent_internet_enabled),
            audio_playback_enabled=bool(audio_playback_enabled),
            memory_backend=ai_agent_config.get("memory_backend", "legacy"),
            adk_db_path=runtime._resolve_ai_agent_storage_uris().get("session_service_uri"),
        )

        # Initialize or update service manager
        if runtime.STATE.service_manager is None:
            runtime.STATE.service_manager = initialize_services(service_config)
            runtime.LOGGER.info(
                f"Service manager initialized with memory enabled={ai_agent_record_messages}, internet_search_enabled={ai_agent_internet_enabled}, audio_playback_enabled={audio_playback_enabled}"
            )
        else:
            # Update existing service manager with new configuration
            update_service_config(service_config)
            runtime.LOGGER.info(
                f"Service manager configuration updated with memory enabled={ai_agent_record_messages}, internet_search_enabled={ai_agent_internet_enabled}, audio_playback_enabled={audio_playback_enabled}"
            )

        # Configure pairing router helpers before any messaging services start
        runtime._update_startup_status(
            status="initializing",
            headline="Preparing session and pairing services",
            detail="Initializing storage, session mapping, and secure pairing routes.",
            step=3,
            total_steps=total_steps,
        )
        if runtime._is_bluetooth_pairing_enabled():
            await runtime.start_or_restart_bluetooth_pairing_service()
        else:
            await runtime.stop_bluetooth_pairing_service("Bluetooth Pair disabled at startup")

        # Initialize Telegram bot and the optional owner-only Saved Messages transport.
        runtime._update_startup_status(
            status="initializing",
            headline="Starting Telegram messaging",
            detail="Booting Telegram messaging services and owner sign-in when configured.",
            step=4,
            total_steps=total_steps,
        )
        await runtime.start_or_restart_telegram()
        await runtime.start_or_restart_telegram_user()

        # Initialize Signal service if enabled
        runtime._update_startup_status(
            status="initializing",
            headline="Starting Signal transport",
            detail="Connecting Signal polling and websocket listeners.",
            step=5,
            total_steps=total_steps,
        )
        await runtime.start_or_restart_signal()

        # Initialize WhatsApp service if enabled
        runtime._update_startup_status(
            status="initializing",
            headline="Starting WhatsApp transport",
            detail="Launching the WhatsApp bridge and synchronizing status.",
            step=6,
            total_steps=total_steps,
        )
        await runtime.start_or_restart_whatsapp()

        # Initialize AI Agent Server if enabled and auto-start is on
        runtime._update_startup_status(
            status="initializing",
            headline="Starting AI agent runtime",
            detail="Warming the ADK app and preparing the selected model.",
            step=7,
            total_steps=total_steps,
        )
        runtime.refresh_agent_install_registry(agents_root=runtime._AUTOYOU_AGENTS_ROOT)
        # Register admin frontend proxy early so the registry is accurate for all
        # subsequent frontend registry refreshes that happen during startup.
        runtime._register_admin_frontend_proxy()
        if runtime.should_start_ai_agent_server():
            if await runtime.start_ai_agent_server_background():
                runtime.LOGGER.info("AI Agent Server started automatically on startup")
            else:
                runtime.LOGGER.warning("AI Agent Server failed to start automatically on startup")
        else:
            await runtime.sync_managed_frontend_backends()

        runtime._update_startup_status(
            status="initializing",
            headline="Starting AutoYou Page services",
            detail="Bringing up the page feed service and final dashboard dependencies.",
            step=8,
            total_steps=total_steps,
        )
        await _start_autoyou_page_service_if_enabled()

        # Start AutoYou Cloud SSE listener if already registered
        # ══════════════════════════════════════════════════════════════════════════════════
        # PRIVACY GUARANTEE: No HTTP requests to AUTOYOU_CLOUD_BASE domains are made unless
        # server_token is explicitly configured by the user's account-link action.
        # Paid relay startup additionally requires pair_enabled; free account links are used
        # only for user-initiated signed update checks.
        # ══════════════════════════════════════════════════════════════════════════════════
        cloud_cfg_startup = (runtime.STATE.config or {}).get("cloud", {})
        if cloud_cfg_startup.get("server_token") and cloud_cfg_startup.get("pair_enabled") is not False:
            runtime.asyncio.create_task(runtime._start_cloud_sse_listener())
            runtime.LOGGER.info("AutoYou Cloud: SSE listener task queued on startup (server token present).")
            if cloud_cfg_startup.get("auto_push_on_startup", False):
                async def _auto_push_on_startup():
                    await runtime.asyncio.sleep(5)
                    result = await runtime._push_to_client("/request_autopair")
                    runtime.LOGGER.info("AutoYou Cloud: auto_push_on_startup result: %s", result)
                runtime.asyncio.create_task(_auto_push_on_startup())

        # Auto-connect the public reverse-proxy URL on startup for paid
        # `tm` entitlement) users - the long-running-tunnel analogue of the Cloud
        # SSE auto-reconnect above. The toggle is visible to everyone, but the
        # tunnel only auto-starts when the paid entitlement is active; free users
        # are nudged toward the plan instead of silently spinning ephemeral tunnels.
        tunnelmole_cfg_startup = (runtime.STATE.config or {}).get("tunnelmole", {})
        if tunnelmole_cfg_startup.get("enabled") and tunnelmole_cfg_startup.get("auto_start_on_boot"):
            async def _auto_start_tunnelmole_on_boot():
                try:
                    await runtime._maybe_apply_paid_tunnelmole_env()
                    if runtime.os.environ.get("AUTOYOU_TUNNELMOLE_TIER") != "paid":
                        runtime.LOGGER.info(
                            "Tunnelmole auto-start on boot is enabled but the "
                            "Public Proxy entitlement is not active; skipping."
                        )
                        return
                    started = await runtime.start_tunnelmole_service_for_current_mode(force=True)
                    if started:
                        runtime.LOGGER.info("Tunnelmole auto-started on boot (paid persistent URL restored).")
                    else:
                        runtime.LOGGER.warning("Tunnelmole auto-start on boot failed to start the service.")
                except Exception as exc:
                    runtime.LOGGER.warning("Tunnelmole auto-start on boot error: %s", exc)
            runtime.asyncio.create_task(_auto_start_tunnelmole_on_boot())

        # Start the background scheduler service for reminders & cron tasks
        try:
            from shared import scheduler_service as _scheduler_svc
            runtime.STATE.scheduler_task = runtime.asyncio.create_task(_scheduler_svc.start_scheduler())
            runtime.LOGGER.info("Scheduler Service started automatically on startup")
        except Exception as _sched_exc:
            runtime.LOGGER.warning("Failed to start Scheduler Service: %s", _sched_exc)

        runtime.STATE.initialized_services_on_startup = True
        runtime._update_startup_status(
            status="complete",
            headline="Initialization complete",
            detail="Configuration decrypted and services are ready.",
            step=total_steps,
            total_steps=total_steps,
        )
        runtime.LOGGER.info("Services initialized on startup")
    except Exception as e:
        runtime._update_startup_status(
            status="error",
            headline="Initialization hit an error",
            detail="Some services did not finish starting. You can still inspect the dashboard logs.",
            step=runtime.STATE.startup_status.get("step") or 0,
            total_steps=total_steps,
            error=str(e),
        )
        runtime.LOGGER.error(f"Error initializing services on startup: {e}")


async def _start_autoyou_page_service_if_enabled() -> None:
    runtime = _runtime()
    if not runtime.AUTOYOU_PAGE_SERVICE_AVAILABLE:
        return
    autoyou_config = runtime.STATE.config.get("autoyou_page", {}) if runtime.STATE.config else {}
    if autoyou_config.get("auto_start", True):
        await runtime.start_autoyou_page_service_background()
        runtime.LOGGER.info("AutoYou Page Service started automatically on startup")


async def _telegram_status() -> tuple[str, str]:
    runtime = _runtime()
    try:
        token = runtime.STATE.config.get("telegram", {}).get("bot_token") if runtime.STATE.config else None
        if not token:
            return "Not configured", "-"
        if runtime.Application is None:
            return "Unavailable (lib missing)", "-"
        app = runtime.Application.builder().token(token).build()
        await app.initialize()
        me = await app.bot.get_me()
        await app.shutdown()
        bot_name = f"@{me.username}" if getattr(me, 'username', None) else me.first_name
        # Cache the bot username so QR export doesn't need a live API call every time
        if runtime.STATE.config is not None:
            runtime.STATE.config.setdefault("telegram", {})["bot_username"] = bot_name
        return "Connected", bot_name
    except Exception as e:

        runtime.LOGGER.warning(f"Telegram status check failed: {e}")

        return "Error", "-"


async def _call_telegram_user_service_method(
    service: Any,
    method_names: Iterable[str],
    *args: Any,
) -> Tuple[bool, Any]:
    """Call the first supported Telegram User service method.

    The service is optional in packaged builds, so this small adapter keeps the
    server compatible with a missing or older private transport module without
    coupling the existing Telegram Bot API path to it.
    """
    runtime = _runtime()
    for method_name in method_names:
        method = getattr(service, method_name, None)
        if not callable(method):
            continue
        result = method(*args)
        if runtime.inspect.isawaitable(result):
            result = await result
        return True, result
    return False, None


def _redact_telegram_user_status_payload(value: Any) -> Any:
    """Remove user-session credentials and sign-in tokens from API status."""
    runtime = _runtime()
    if isinstance(value, dict):
        return {
            str(key): runtime._redact_telegram_user_status_payload(item)
            for key, item in value.items()
            if str(key).strip().lower() not in runtime._TELEGRAM_USER_PRIVATE_STATUS_KEYS
        }
    if isinstance(value, list):
        return [runtime._redact_telegram_user_status_payload(item) for item in value]
    if isinstance(value, tuple):
        return [runtime._redact_telegram_user_status_payload(item) for item in value]
    return value


def _telegram_user_qr_url(value: Any) -> str:
    runtime = _runtime()
    if isinstance(value, dict):
        for key in ("qr_url", "qr_data_url", "url", "data_url"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
        return ""
    return str(value or "").strip()


def _telegram_user_message_metadata(entries: Any, *, limit: int) -> List[Dict[str, Any]]:
    """Return a safe admin summary without message text, media, or identifiers."""
    runtime = _runtime()
    if isinstance(entries, dict):
        entries = entries.get("messages") or entries.get("entries") or []
    if not isinstance(entries, (list, tuple)):
        return []
    summaries: runtime.List[runtime.Dict[str, runtime.Any]] = []
    for entry in entries[:max(1, limit)]:
        if not isinstance(entry, dict):
            continue
        has_media = bool(entry.get("has_media") or entry.get("media") or entry.get("attachments"))
        kind = str(entry.get("kind") or entry.get("type") or "").strip().lower()
        summaries.append(
            {
                "timestamp": str(entry.get("timestamp") or entry.get("created_at") or ""),
                "direction": str(entry.get("direction") or "").strip().lower() or "unknown",
                "kind": kind or ("media" if has_media else "message"),
                "has_media": has_media,
            }
        )
    return summaries


def _telegram_user_call_succeeded(result: Any) -> bool:
    runtime = _runtime()
    if isinstance(result, dict):
        return result.get("success", result.get("ok", True)) is not False
    return result is not False


async def _telegram_user_status() -> tuple[str, str]:
    """Return a concise owner-facing status for the Saved Messages transport."""
    runtime = _runtime()
    config = runtime.STATE.config.get("telegram_user", {}) if runtime.STATE.config else {}
    enabled = bool(config.get("enabled", False))
    service = runtime.STATE.telegram_user_service
    if not runtime._messaging_partner_feature_enabled("telegram_user") and service is None:
        return "Disabled", "-"
    if service is None:
        if not enabled:
            return "Disabled", "-"
        if runtime.TelegramUserService is None:
            return "Unavailable", "-"
        return "Not started", "Saved Messages"

    try:
        found, status = await runtime._call_telegram_user_service_method(
            service,
            ("get_status", "status"),
        )
        if not found:
            return "Starting up", "Saved Messages"
        if isinstance(status, dict):
            if bool(status.get("connected") or status.get("authorized") or status.get("ready")):
                return "Connected", "Saved Messages"
            if bool(
                status.get("needs_password")
                or status.get("awaiting_2fa")
                or status.get("two_factor_required")
            ):
                return "Sign-in confirmation needed", "Saved Messages"
            raw_status = str(status.get("status") or "").strip()
            if bool(status.get("qr_available") or status.get("awaiting_qr")) or raw_status == "awaiting_qr":
                return "Ready to sign in", "Saved Messages"
            if raw_status:
                return raw_status.replace("_", " ").title(), "Saved Messages"
        if status is False:
            return "Disconnected", "Saved Messages"
        return "Starting up", "Saved Messages"
    except Exception as exc:
        runtime.LOGGER.warning("Telegram User status check failed: %s", exc)
        return "Error", "-"


def _bluetooth_pairing_runtime_status() -> Dict[str, Any]:
    server = _runtime()
    pairing_server = getattr(server.STATE, "bluetooth_pairing_server", None)
    if pairing_server is None:
        status = server.BluetoothPairingStatus(
            enabled=server._is_bluetooth_pairing_enabled(),
            running=False,
            transport="ble-gatt",
            error="Bluetooth Pair transport is not running.",
        )
    else:
        status = getattr(pairing_server, "status", None)
        if not isinstance(status, server.BluetoothPairingStatus):
            status = server.BluetoothPairingStatus(
                enabled=server._is_bluetooth_pairing_enabled(),
                running=False,
                transport="unknown",
                error="Bluetooth Pair runtime status unavailable.",
            )
    return {
        "enabled": status.enabled,
        "running": status.running,
        "transport": status.transport,
        "error": status.error,
        "service_uuid": status.service_uuid,
        "rx_uuid": status.rx_uuid,
        "tx_uuid": status.tx_uuid,
    }


async def stop_bluetooth_pairing_service(reason: str = "") -> None:
    server = _runtime()
    pairing_server = getattr(server.STATE, "bluetooth_pairing_server", None)
    server.STATE.bluetooth_pairing_server = None
    if pairing_server is not None:
        try:
            await pairing_server.stop()
            server.LOGGER.info("Bluetooth Pair signaling transport stopped%s", f": {reason}" if reason else "")
        except Exception as exc:
            server.LOGGER.warning("Bluetooth Pair signaling transport stop failed: %s", exc)


async def start_or_restart_bluetooth_pairing_service() -> bool:
    server = _runtime()
    await server.stop_bluetooth_pairing_service("restart requested")
    if not server._is_bluetooth_pairing_enabled():
        server.LOGGER.info("Bluetooth Pair signaling transport is disabled.")
        return False
    handler = server.BluetoothPairingCommandHandler(
        pairing_router=server.pairing_router,
        is_enabled=server._is_bluetooth_pairing_enabled,
    )
    server_name = server.get_configured_server_name()
    pairing_server = server.create_bluetooth_pairing_server(
        handler,
        name=f"{server_name} Bluetooth Pair",
    )
    server.STATE.bluetooth_pairing_server = pairing_server
    started = await pairing_server.start()
    if not started:
        status = server._bluetooth_pairing_runtime_status()
        server.LOGGER.warning(
            "Bluetooth Pair is enabled but the BLE signaling transport is not running: %s",
            status.get("error") or "unknown error",
        )
        return False
    server.LOGGER.info("Bluetooth Pair signaling transport started.")
    return True


def _persist_telegram_user_session(session_string: Any = None, **metadata: Any) -> bool:
    """Persist a freshly authorized Telegram User session in encrypted config."""
    runtime = _runtime()
    session_value = str(
        session_string
        or metadata.get("session_string")
        or metadata.get("session")
        or ""
    ).strip()
    if not session_value:
        return False
    try:
        cfg = runtime._loaded_config_for_update(copy_config=True)
        telegram_user_cfg = cfg.setdefault("telegram_user", {})
        if str(telegram_user_cfg.get("session") or "") == session_value:
            return True
        telegram_user_cfg["session"] = session_value
        runtime._persist_state_config(cfg)
        runtime._invalidate_admin_status_cache("telegram_user_status")
        runtime.LOGGER.info("Saved Telegram User sign-in state to the protected configuration store.")
        return True
    except Exception as exc:
        runtime.LOGGER.warning("Could not save Telegram User sign-in state: %s", exc)
        return False


def _clear_telegram_user_session() -> bool:
    """Remove the persisted user-session token after an explicit disconnect."""
    runtime = _runtime()
    try:
        cfg = runtime._loaded_config_for_update(copy_config=True)
        cfg.setdefault("telegram_user", {})["session"] = ""
        runtime._persist_state_config(cfg)
        runtime._invalidate_admin_status_cache("telegram_user_status")
        return True
    except Exception as exc:
        runtime.LOGGER.warning("Could not clear Telegram User sign-in state: %s", exc)
        return False


async def stop_telegram_user() -> None:
    """Stop the optional owner-only Telegram User transport."""
    runtime = _runtime()
    runtime._invalidate_admin_status_cache("telegram_user_status")
    service = runtime.STATE.telegram_user_service
    if service is None:
        return
    try:
        found, result = await runtime._call_telegram_user_service_method(service, ("stop", "close"))
        if found and result is False:
            runtime.LOGGER.warning("Telegram User service reported incomplete shutdown.")
    except Exception as exc:
        runtime.LOGGER.warning("Failed to stop Telegram User service: %s", exc)
    finally:
        runtime.STATE.telegram_user_service = None


async def start_or_restart_telegram_user() -> bool:
    """Start the optional Saved Messages transport when owner credentials exist."""
    runtime = _runtime()
    runtime._invalidate_admin_status_cache("telegram_user_status")
    if not runtime._messaging_partner_feature_enabled("telegram_user"):
        runtime.LOGGER.info(runtime._messaging_partner_disabled_message("telegram_user"))
        await runtime.stop_telegram_user()
        return False

    config = runtime.STATE.config.get("telegram_user", {}) if runtime.STATE.config else {}
    if not bool(config.get("enabled", False)):
        await runtime.stop_telegram_user()
        return False
    if runtime.TelegramUserService is None:
        runtime.LOGGER.warning("Telegram User service is not available in this runtime.")
        await runtime.stop_telegram_user()
        return False

    try:
        api_id = int(str(config.get("api_id") or "0").strip())
    except Exception:
        api_id = 0
    api_hash = str(config.get("api_hash") or "").strip()
    if api_id <= 0 or not api_hash:
        runtime.LOGGER.warning("Telegram User is enabled but its API credentials are incomplete.")
        await runtime.stop_telegram_user()
        return False

    await runtime.stop_telegram_user()
    try:
        server_name = str(
            (runtime.STATE.config or {}).get("server", {}).get("name") or "AutoYou Server"
        ).strip() or "AutoYou Server"
        service = runtime.TelegramUserService(
            api_id=api_id,
            api_hash=api_hash,
            session_string=str(config.get("session") or "").strip(),
            server_name=server_name,
            training_export_consent=bool(config.get("training_export_consent", False)),
            on_session_saved=runtime._persist_telegram_user_session,
        )
        runtime.STATE.telegram_user_service = service
        found, result = await runtime._call_telegram_user_service_method(service, ("start",))
        if not found:
            raise RuntimeError("Telegram User service does not provide start().")
        if result is False:
            raise RuntimeError("Telegram User service did not start.")
        runtime.LOGGER.info("Telegram User Saved Messages transport started.")
        return True
    except Exception as exc:
        runtime.LOGGER.warning("Failed to start Telegram User service: %s", exc)
        await runtime.stop_telegram_user()
        return False


async def stop_telegram() -> None:
    """Stop the running Telegram bot application if active and clear state.

    This function gracefully stops polling (if enabled), stops the application,
    and shuts it down to release resources. It then clears STATE.telegram_app
    to reflect that no bot instance is running.
    """
    runtime = _runtime()
    app = getattr(runtime.STATE, "telegram_app", None)
    if app is None:
        return

    runtime.LOGGER.info("Stopping Telegram bot...")

    # Stop polling if the updater exists
    try:
        updater = getattr(app, "updater", None)
        if updater is not None:
            try:
                await updater.stop()
                runtime.LOGGER.info("Telegram bot polling stopped.")
            except Exception as e:
                runtime.LOGGER.warning(f"Failed to stop polling: {e}")
    except Exception as e:
        runtime.LOGGER.warning(f"Updater stop check failed: {e}")

    # Stop the application
    try:
        await app.stop()
    except Exception as e:
        runtime.LOGGER.warning(f"Failed to stop Telegram app: {e}")

    # Shutdown the application
    try:
        await app.shutdown()
    except Exception as e:
        runtime.LOGGER.warning(f"Failed to shutdown Telegram app: {e}")

    # Clear state
    runtime.STATE.telegram_app = None
    runtime.LOGGER.info("Telegram bot stopped.")


async def start_or_restart_telegram():
    runtime = _runtime()
    if not runtime._messaging_partner_feature_enabled("telegram"):
        runtime.LOGGER.info("Telegram startup disabled by AUTOYOU_ENABLE_TELEGRAM_PARTNER.")
        await runtime.stop_telegram()
        return

    # Allow disabling Telegram auto-start to avoid conflicts with another running bot
    try:
        disable_env = str(runtime.os.getenv("AUTOYOU_DISABLE_TELEGRAM_ON_STARTUP", "")).strip().lower()
        if disable_env in ("1", "true", "yes", "on"):
            runtime.LOGGER.info("Telegram startup disabled by env AUTOYOU_DISABLE_TELEGRAM_ON_STARTUP.")
            return
    except Exception:
        pass

    token = runtime.STATE.config.get("telegram", {}).get("bot_token") if runtime.STATE.config else None
    if not token or runtime.Application is None:
        return
    # If running with same token, skip restart
    try:
        if runtime.STATE.telegram_app is not None and runtime.STATE.last_telegram_token == token:
            runtime.LOGGER.info("Telegram bot already running with current token; skipping restart.")
            return
    except Exception:
        pass
    # If running, stop it
    await runtime.stop_telegram()
    try:
        builder = (
            runtime.Application.builder()
            .token(token)
            .connect_timeout(runtime.TELEGRAM_CONNECT_TIMEOUT_SECONDS)
            .read_timeout(runtime.TELEGRAM_READ_TIMEOUT_SECONDS)
            .write_timeout(runtime.TELEGRAM_WRITE_TIMEOUT_SECONDS)
            .pool_timeout(runtime.TELEGRAM_POOL_TIMEOUT_SECONDS)
            .media_write_timeout(runtime.TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS)
        )
        if hasattr(builder, "get_updates_pool_timeout"):
            builder = builder.get_updates_pool_timeout(runtime.TELEGRAM_GET_UPDATES_POOL_TIMEOUT_SECONDS)
        if hasattr(builder, "get_updates_connection_pool_size"):
            builder = builder.get_updates_connection_pool_size(runtime.TELEGRAM_GET_UPDATES_CONNECTION_POOL_SIZE)
        if hasattr(builder, "get_updates_connect_timeout"):
            builder = builder.get_updates_connect_timeout(runtime.TELEGRAM_CONNECT_TIMEOUT_SECONDS)
        if hasattr(builder, "get_updates_read_timeout"):
            builder = builder.get_updates_read_timeout(runtime.TELEGRAM_READ_TIMEOUT_SECONDS)
        if hasattr(builder, "get_updates_write_timeout"):
            builder = builder.get_updates_write_timeout(runtime.TELEGRAM_WRITE_TIMEOUT_SECONDS)
        app = builder.build()
        conv = runtime.ConversationHandler(
            entry_points=[
                runtime.CommandHandler("pair", runtime.pair_command),
                # /pair_hello and /otp_pair are their own Telegram commands -
                # CommandHandler("pair") only matches the literal "/pair", so
                # each spelling needs an explicit registration to reach the
                # pairing router at all (filters.COMMAND excludes them from
                # the plain-text handler below).
                runtime.CommandHandler("pair_hello", runtime.pair_command),
                runtime.CommandHandler("otp_pair", runtime.pair_command),
                runtime.CommandHandler("new", runtime.new_conversation_command),
                runtime.CommandHandler("newchat", runtime.new_conversation_command),
                runtime.CommandHandler("newconversation", runtime.new_conversation_command),
                runtime.CommandHandler("autopair", runtime.autopair_command),
                runtime.CommandHandler("autopair_hello", runtime.autopair_command),
                runtime.CommandHandler("autopair_candidates", runtime.autopair_command),
            ],
            states={},
            fallbacks=[runtime.CommandHandler("cancel", runtime.cancel_command)],
            allow_reentry=True,
        )
        app.add_handler(conv)
        app.add_handler(runtime.CommandHandler("allow", runtime.allow_command))
        app.add_handler(runtime.CommandHandler("context", runtime.context_command))
        app.add_handler(runtime.CommandHandler("memory", runtime.context_command))
        # Apply ACL to text messages as well
        if runtime.filters is not None:
            base_msg_filter = (runtime.filters.TEXT & ~runtime.filters.COMMAND)
            app.add_handler(runtime.MessageHandler(base_msg_filter, runtime.log_any_text))
            app.add_handler(runtime.MessageHandler(runtime.filters.PHOTO, runtime.log_photo))
            app.add_handler(runtime.MessageHandler(runtime.filters.Document.ALL, runtime.log_document))
            app.add_handler(runtime.MessageHandler(runtime.filters.VIDEO, runtime.log_video))
            app.add_handler(runtime.MessageHandler(runtime.filters.AUDIO, runtime.log_audio))
            app.add_handler(runtime.MessageHandler(runtime.filters.VOICE, runtime.log_voice))
        else:
            runtime.LOGGER.info("telegram.filters unavailable; skipping text message handler.")
        app.add_error_handler(runtime._telegram_error_handler)

        await app.initialize()
        try:
            info = await app.bot.get_webhook_info()
            if info and getattr(info, 'url', ''):
                runtime.LOGGER.info("Existing webhook is set to: %s - deleting to enable polling", info.url)
        except Exception as _e:
            runtime.LOGGER.warning(f"get_webhook_info failed: {_e}")
        try:
            await app.bot.delete_webhook(drop_pending_updates=True)
            runtime.LOGGER.info("Deleted webhook (if any) to enable polling.")
        except Exception as _e:
            # If another bot instance is polling, Telegram may report a Conflict here
            if "Conflict" in str(_e):
                runtime.LOGGER.warning("Telegram Conflict detected during delete_webhook; another instance may be polling. Skipping startup.")
                runtime.STATE.telegram_app = None
                return
            runtime.LOGGER.warning(f"delete_webhook failed: {_e}")
        try:
            await app.start()
        except Exception as _e:
            if "Conflict" in str(_e):
                runtime.LOGGER.warning("Telegram Conflict detected on start; another instance is running. Aborting Telegram startup.")
                runtime.STATE.telegram_app = None
                return
            raise
        if getattr(app, "updater", None):
            # Start polling by default. Allow explicit disable via env.
            try:
                polling_env = str(runtime.os.getenv("AUTOYOU_TELEGRAM_POLLING", "")).strip().lower()
                # Default ON unless explicitly disabled
                enable_polling = polling_env not in ("0", "false", "no", "off")
            except Exception:
                enable_polling = True
            if enable_polling:
                await app.updater.start_polling(
                    connect_timeout=runtime.TELEGRAM_CONNECT_TIMEOUT_SECONDS,
                    read_timeout=runtime.TELEGRAM_READ_TIMEOUT_SECONDS,
                    write_timeout=runtime.TELEGRAM_WRITE_TIMEOUT_SECONDS,
                    pool_timeout=runtime.TELEGRAM_GET_UPDATES_POOL_TIMEOUT_SECONDS,
                )
                runtime.LOGGER.info("Telegram bot polling started.")
            else:
                runtime.LOGGER.info("Telegram polling explicitly disabled via AUTOYOU_TELEGRAM_POLLING.")

        runtime.STATE.telegram_app = app
        runtime.STATE.last_telegram_token = token
        runtime.LOGGER.info("Telegram bot started for signaling and logging.")
        await runtime._flush_telegram_pending_voice_replies()
        await runtime._flush_telegram_pending_media_replies()
    except Exception as e:
        runtime.LOGGER.warning(f"Failed to start Telegram bot: {e}")


async def stop_signal():
    """Stop the Signal CLI REST API service."""
    runtime = _runtime()
    runtime._invalidate_admin_status_cache("signal_status")
    if runtime.STATE.signal_service is None:
        return

    runtime.LOGGER.info("Stopping Signal service...")
    try:
        # Get the Docker shutdown configuration
        signal_config = runtime.STATE.config.get("signal", {})
        shutdown_docker = signal_config.get("shutdown_docker_on_exit", True)

        await runtime.STATE.signal_service.stop(shutdown_docker=shutdown_docker)
        runtime.STATE.signal_service = None
        runtime.LOGGER.info("Signal service stopped.")
    except Exception as e:
        runtime.LOGGER.warning(f"Failed to stop Signal service: {e}")


async def stop_whatsapp():
    """Stop the WhatsApp Web.js service."""
    runtime = _runtime()
    runtime._invalidate_admin_status_cache("whatsapp_status")
    if runtime.STATE.whatsapp_service is None:
        return

    runtime.LOGGER.info("Stopping WhatsApp service...")
    try:
        await runtime.STATE.whatsapp_service.stop()
        runtime.STATE.whatsapp_service = None
        runtime.LOGGER.info("WhatsApp service stopped.")
    except Exception as e:
        runtime.LOGGER.warning(f"Failed to stop WhatsApp service: {e}")


async def start_or_restart_signal():
    """Start or restart the Signal CLI REST API service based on configuration."""
    runtime = _runtime()
    runtime._invalidate_admin_status_cache("signal_status")
    if not runtime._messaging_partner_feature_enabled("signal"):
        runtime.LOGGER.info(runtime._messaging_partner_disabled_message("signal"))
        await runtime.stop_signal()
        return

    if runtime.SignalService is None:
        runtime.LOGGER.warning("Signal service not available (module not imported)")
        return

    signal_config = runtime.STATE.config.get("signal", {}) if runtime.STATE.config else {}
    enabled = signal_config.get("enabled", False)

    if not enabled:
        runtime.LOGGER.info("Signal service disabled in configuration")
        await runtime.stop_signal()
        return

    # Stop existing service if running
    await runtime.stop_signal()

    try:
        # Create and start new Signal service
        port = signal_config.get("port", 8082)
        device_name = signal_config.get("device_name", "AutoYou-Signal")

        runtime.STATE.signal_service = runtime.SignalService(port=port, device_name=device_name)
        runtime.STATE.signal_service.main_server_port = runtime.STATE.main_server_port

        success = await runtime.STATE.signal_service.start()

        if success:
            runtime.LOGGER.info("Signal service started successfully")

            # Check if device is already paired and update config
            await runtime._check_and_update_pairing_status()
        else:
            runtime.LOGGER.error("Failed to start Signal service")
            runtime.STATE.signal_service = None

    except Exception as e:
        runtime.LOGGER.warning(f"Failed to start Signal service: {e}")
        runtime.STATE.signal_service = None


async def start_or_restart_whatsapp():
    """Start or restart the WhatsApp Web.js service based on configuration."""
    runtime = _runtime()
    runtime._invalidate_admin_status_cache("whatsapp_status")
    if not runtime._messaging_partner_feature_enabled("whatsapp"):
        runtime.LOGGER.info(runtime._messaging_partner_disabled_message("whatsapp"))
        await runtime.stop_whatsapp()
        return

    if runtime.WhatsAppService is None:
        runtime.LOGGER.warning("WhatsApp service not available (module not imported)")
        return

    whatsapp_config = runtime.STATE.config.get("whatsapp", {}) if runtime.STATE.config else {}
    enabled = whatsapp_config.get("enabled", False)

    if not enabled:
        runtime.LOGGER.info("WhatsApp service disabled in configuration")
        await runtime.stop_whatsapp()
        return

    # Stop existing service if running
    await runtime.stop_whatsapp()

    try:
        # Create and start new WhatsApp service with subprocess management
        websocket_port = int(whatsapp_config.get("websocket_port", runtime.os.getenv("WHATSAPP_WS_PORT", "8083")))
        device_name = whatsapp_config.get("device_name", "AutoYou-WhatsApp")
        ai_api_url = whatsapp_config.get("ai_api_url", "http://localhost:8081/api/chat")

        runtime.STATE.whatsapp_service = runtime.WhatsAppService(
            websocket_port=websocket_port,
            device_name=device_name,
            ai_api_url=ai_api_url
        )

        success = await runtime.STATE.whatsapp_service.start()

        if success:
            runtime.LOGGER.info("WhatsApp service started successfully")

            # Check if device is already paired and update config
            await runtime._check_and_update_whatsapp_pairing_status()
        else:
            runtime.LOGGER.error("Failed to start WhatsApp service")
            runtime.STATE.whatsapp_service = None

    except Exception as e:
        runtime.LOGGER.warning(f"Failed to start WhatsApp service: {e}")
        runtime.STATE.whatsapp_service = None


async def _check_and_update_whatsapp_pairing_status(
    *,
    settle_delay_seconds: float = 3.0,
    persist_pairing: bool = True,
) -> dict:
    """Check WhatsApp pairing status, update persisted config, and return status.

    Returns a status dictionary compatible with the admin status endpoint.
    - If the WhatsApp service is not running, returns a stopped/disabled snapshot.
    - If running, returns the service status and persists pairing info when possible.
    """
    runtime = _runtime()
    def _stopped_snapshot() -> dict:
        whatsapp_config = runtime.STATE.config.get("whatsapp", {}) if runtime.STATE.config else {}
        enabled = whatsapp_config.get("enabled", False)
        return {
            "enabled": enabled,
            "status": "stopped",
            "paired": False,
            "phone_number": None,
            "error": "Service not started" if enabled else "Service disabled",
        }

    try:
        # If service not started, provide a consistent snapshot
        service = runtime.STATE.whatsapp_service
        if service is None:
            return _stopped_snapshot()

        # Startup and restart flows can opt into a brief settle window before
        # the first status probe. Polling endpoints should skip this.
        if settle_delay_seconds > 0:
            await runtime.asyncio.sleep(settle_delay_seconds)

        if service is not runtime.STATE.whatsapp_service or runtime.STATE.whatsapp_service is None:
            return _stopped_snapshot()

        status = await service.get_status()
        if isinstance(status, dict):
            whatsapp_config = runtime.STATE.config.get("whatsapp", {}) if runtime.STATE.config else {}
            status["enabled"] = True
            status.setdefault("configured_enabled", whatsapp_config.get("enabled", True))

        # Update persisted configuration only on explicit sync calls.
        if persist_pairing and runtime._can_persist_config():
            # Ensure config dict exists
            if runtime.STATE.config is None:
                runtime.STATE.config = {}

            whatsapp_config = runtime.STATE.config.setdefault("whatsapp", {})
            config_changed = False

            confirmed_pairing = bool(status.get("phone_number")) and (
                bool(status.get("connection_healthy")) or bool(status.get("ready")) or str(status.get("status") or "").strip().lower() == "connected"
            )
            if confirmed_pairing:
                phone_number = status.get("phone_number")

                if whatsapp_config.get("phone_number") != phone_number:
                    whatsapp_config["phone_number"] = phone_number or ""
                    config_changed = True
                    runtime.LOGGER.info("Updated WhatsApp phone number in config: %s", runtime.redact_identifier(phone_number))

                if not whatsapp_config.get("paired"):
                    whatsapp_config["paired"] = True
                    config_changed = True
                    runtime.LOGGER.info("Updated WhatsApp pairing status to paired")
            else:
                # Device not paired, ensure config reflects this
                if whatsapp_config.get("paired"):
                    whatsapp_config["paired"] = False
                    whatsapp_config["phone_number"] = ""
                    config_changed = True
                    runtime.LOGGER.info("Updated WhatsApp pairing status to not paired")

            # Save configuration if changed
            if config_changed:
              try:
                runtime._persist_state_config(runtime.STATE.config)
                runtime.LOGGER.info("WhatsApp pairing configuration saved successfully")
                runtime._invalidate_admin_status_cache("whatsapp_status")
              except Exception as e:
                runtime.LOGGER.error(f"Failed to save WhatsApp pairing configuration: {e}")

        return status
    except Exception as e:
        runtime.LOGGER.warning(f"Failed to check WhatsApp pairing status: {e}")
        return {
            "status": "error",
            "paired": False,
            "phone_number": None,
            "error": str(e),
        }


async def _check_and_update_pairing_status():
    """Check device pairing status and update configuration if paired."""
    runtime = _runtime()
    if runtime.STATE.signal_service is None or not runtime._can_persist_config():
        return

    try:
        await runtime.asyncio.sleep(2)
        pairing_status = await runtime.STATE.signal_service.check_device_pairing_status()

        if pairing_status.get("paired"):
            phone_number = pairing_status.get("phone_number")
            signal_config = runtime.STATE.config.setdefault("signal", {})
            config_changed = False

            if signal_config.get("phone_number") != phone_number:
                signal_config["phone_number"] = phone_number
                config_changed = True
                runtime.LOGGER.info("Updated Signal phone number in config: %s", runtime.redact_identifier(phone_number))

            if not signal_config.get("paired"):
                signal_config["paired"] = True
                config_changed = True
                runtime.LOGGER.info("Updated Signal pairing status to paired")

            if config_changed:
                try:
                    runtime._persist_state_config(runtime.STATE.config)
                    runtime.LOGGER.info("Signal pairing configuration saved successfully")
                except Exception as e:
                    runtime.LOGGER.error(f"Failed to save Signal pairing configuration: {e}")
        else:
            signal_config = runtime.STATE.config.setdefault("signal", {})
            config_changed = False

            if signal_config.get("paired"):
                signal_config["paired"] = False
                signal_config["phone_number"] = ""
                config_changed = True
                runtime.LOGGER.info("Updated Signal pairing status to not paired")

            if config_changed:
                try:
                    runtime._persist_state_config(runtime.STATE.config)
                except Exception as e:
                    runtime.LOGGER.error(f"Failed to save Signal pairing configuration: {e}")

    except Exception as e:
        runtime.LOGGER.error(f"Error checking Signal pairing status: {e}")


async def _signal_status() -> tuple[str, str]:
    """Get Signal service status for admin dashboard."""
    runtime = _runtime()
    try:
        signal_config = runtime.STATE.config.get("signal", {}) if runtime.STATE.config else {}
        enabled = signal_config.get("enabled", False)

        # If Signal is disabled in config, return disabled status
        if not enabled and runtime.STATE.signal_service is None:
            return "Disabled", "-"

        # If enabled but service not initialized, return not started
        if runtime.STATE.signal_service is None:
            return "Not started", "-"

        # Use the async detailed status method
        status = await runtime.STATE.signal_service.get_detailed_status_async()

        if status.get("paired"):
            phone = status.get("paired_phone_number", "Unknown")
            return "Connected", phone
        elif status.get("container_running"):
            return "Ready for pairing", "-"
        else:
            return "Starting up", "-"

    except Exception as e:
        runtime.LOGGER.warning(f"Signal status check failed: {e}")

        return "Error", str(e)[:50] + "..." if len(str(e)) > 50 else str(e)


async def start_tunnelmole_service_no_timer(force: bool = True) -> bool:
    """Start tunnelmole without starting an auto-timeout timer.

    Use this for tunnelmole.connection_mode == unmanaged. Any existing timer is
    cancelled so it cannot trigger an unexpected shutdown.
    """
    runtime = _runtime()
    try:
        service_started = await runtime.start_tunnelmole_service(force=force)
        if service_started:
            # Cancel any residual timer - unmanaged mode has no auto-timeout.
            await runtime.stop_tunnelmole_timer()
            runtime.LOGGER.info("Tunnelmole started in unmanaged mode with no auto-timeout timer")
        return service_started
    except Exception as e:
        runtime.LOGGER.error("Error starting tunnelmole service (unmanaged mode): %s", e)
        return False


def _get_tunnelmole_target_port() -> int:
    runtime = _runtime()
    return int(runtime.AUTH_SERVER_PORT)


def _default_password_on_tunnel_allowed() -> bool:
    runtime = _runtime()
    raw = str(runtime.os.environ.get(runtime._ALLOW_DEFAULT_PASSWORD_ON_TUNNEL_ENV, "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _tunnelmole_blocked_by_default_password() -> bool:
    """Return True if the current server password is still the default and
    the operator has not explicitly opted in to exposing it publicly."""
    runtime = _runtime()
    try:
        if not getattr(runtime.STATE, "used_default_password", False):
            return False
    except Exception:
        return False
    return not runtime._default_password_on_tunnel_allowed()


def _get_or_create_tunnelmole_service() -> TunnelmoleService:
    runtime = _runtime()
    service = runtime.STATE.tunnelmole_service
    if not isinstance(service, runtime.TunnelmoleService):
        service = runtime.TunnelmoleService(runtime._get_tunnelmole_target_port())
        runtime.STATE.tunnelmole_service = service
    else:
        service.set_port(runtime._get_tunnelmole_target_port())
    return service


async def start_tunnelmole_timer(timeout_minutes: int = None):
    """Start or restart the tunnelmole timeout timer"""
    runtime = _runtime()
    try:
        # Cancel existing timer if running
        if runtime.STATE.tunnelmole_timeout_task and not runtime.STATE.tunnelmole_timeout_task.done():
            runtime.STATE.tunnelmole_timeout_task.cancel()

        # Set timeout duration
        timeout_duration = (timeout_minutes or runtime.STATE.tunnelmole_timeout_minutes) * 60  # Convert to seconds
        runtime.STATE.tunnelmole_start_time = runtime.time.time()

        # Create new timeout task
        runtime.STATE.tunnelmole_timeout_task = runtime.asyncio.create_task(
            runtime.tunnelmole_timeout_handler(timeout_duration)
        )

        runtime.LOGGER.info(f"Started tunnelmole timer for {timeout_duration/60} minutes")
        return True

    except Exception as e:
        runtime.LOGGER.error(f"Error starting tunnelmole timer: {e}")
        return False


async def extend_tunnelmole_timer(additional_minutes: int = None):
    """Extend the current tunnelmole timer"""
    runtime = _runtime()
    try:
        runtime.LOGGER.info(f"Extending tunnelmole timer by {additional_minutes or runtime.STATE.tunnelmole_timeout_minutes} minutes")

        if not runtime.STATE.tunnelmole_timeout_task or runtime.STATE.tunnelmole_timeout_task.done():
            # No active timer, start a new one
            runtime.LOGGER.info("No active timer found, starting new tunnelmole timer")
            return await runtime.start_tunnelmole_timer(additional_minutes)

        # Cancel current timer and start new one with extended time
        runtime.STATE.tunnelmole_timeout_task.cancel()

        # Calculate remaining time and add extension
        current_time = runtime.time.time()
        elapsed_time = current_time - (runtime.STATE.tunnelmole_start_time or current_time)
        remaining_minutes = max(0, (runtime.STATE.tunnelmole_timeout_minutes * 60 - elapsed_time) / 60)

        extension = (additional_minutes or runtime.STATE.tunnelmole_timeout_minutes) - int(remaining_minutes)
        new_timeout = remaining_minutes + extension

        runtime.LOGGER.info(f"Extending timer: {remaining_minutes:.1f} minutes remaining + {extension} minutes extension = {new_timeout:.1f} total minutes")

        return await runtime.start_tunnelmole_timer(int(new_timeout))

    except Exception as e:
        runtime.LOGGER.error(f"Error extending tunnelmole timer: {e}")
        return False


async def stop_tunnelmole_timer():
    """Stop the tunnelmole timeout timer"""
    runtime = _runtime()
    try:
        if runtime.STATE.tunnelmole_timeout_task and not runtime.STATE.tunnelmole_timeout_task.done():
            runtime.STATE.tunnelmole_timeout_task.cancel()
            runtime.LOGGER.info("Stopped tunnelmole timer")

        runtime.STATE.tunnelmole_timeout_task = None
        runtime.STATE.tunnelmole_start_time = None
        return True

    except Exception as e:
        runtime.LOGGER.error(f"Error stopping tunnelmole timer: {e}")
        return False


async def tunnelmole_timeout_handler(timeout_seconds: int):
    """Handle tunnelmole timeout by gracefully shutting down the client"""
    runtime = _runtime()
    try:
        await runtime.asyncio.sleep(timeout_seconds)

        runtime.LOGGER.info("Tunnelmole timeout reached, initiating graceful shutdown")

        # Force stop tunnelmole service if still running
        await runtime.stop_tunnelmole_service()
        await runtime.stop_auth_server()

        # Clear caches
        runtime.STATE.otp_cache.clear()
        runtime.STATE.session_cache.clear()

        runtime.LOGGER.info("Tunnelmole timeout handling completed")

    except runtime.asyncio.CancelledError:
        runtime.LOGGER.info("Tunnelmole timeout handler cancelled")
    except Exception as e:
        runtime.LOGGER.error(f"Error in tunnelmole timeout handler: {e}")


async def start_tunnelmole_service_with_timer(force: bool = True) -> bool:
    """Start tunnelmole service with automatic timer management.

    Called on-demand (e.g. from the /pair command), so we force-start even
    when the persistent config has tunnelmole.enabled=False.  The enabled flag
    controls *auto-start at boot*, not whether the service can ever be used.
    """
    runtime = _runtime()
    try:
        # Start the tunnelmole service, bypassing the persistent enabled flag
        # because the user explicitly requested pairing via /pair.
        service_started = await runtime.start_tunnelmole_service(force=force)

        if service_started:
            # Get timeout from config
            tunnelmole_config = runtime.STATE.config.get("tunnelmole", {}) if runtime.STATE.config else {}
            timeout_minutes = tunnelmole_config.get("timeout_minutes", 5)
            # Start the timeout timer
            timer_started = await runtime.start_tunnelmole_timer(timeout_minutes)

            if not timer_started:
                runtime.LOGGER.warning("Tunnelmole service started but timer failed to start")

            return True

        return False

    except Exception as e:
        runtime.LOGGER.error(f"Error starting tunnelmole service with timer: {e}")
        return False


async def start_tunnelmole_service_for_current_mode(force: bool = True) -> bool:
    """Start or refresh tunnelmole according to the configured connection mode."""
    runtime = _runtime()
    status_info = runtime.get_tunnelmole_status()
    if status_info.get("status") == "running":
        if runtime._is_tunnelmole_unmanaged_mode():
            runtime.LOGGER.info("Tunnelmole already running in unmanaged mode")
            return True
        return bool(await runtime.extend_tunnelmole_timer(None))

    if runtime._is_tunnelmole_unmanaged_mode():
        return await runtime.start_tunnelmole_service_no_timer(force=force)
    return await runtime.start_tunnelmole_service_with_timer(force=force)


async def start_tunnelmole_service(force: bool = False) -> bool:
    """Start the direct Tunnelmole process against the auth/signaling app.

    Args:
        force: When True, start even if tunnelmole.enabled is False in the
               persisted configuration.  Use this for on-demand launches
               (e.g. the /pair command) where the user has explicitly requested
               pairing.  When False (the default), respect the config flag so
               that auto-start at boot can be suppressed.
    """
    runtime = _runtime()
    try:
        tunnelmole_config = runtime.STATE.config.get("tunnelmole", {}) if runtime.STATE.config else {}
        enabled = tunnelmole_config.get("enabled", True)

        if not enabled and not force:
            runtime.LOGGER.info("Tunnelmole service disabled in configuration")
            return False

        # C-1: block public exposure while default bootstrap password is in use
        if runtime._tunnelmole_blocked_by_default_password():
            runtime.LOGGER.warning(
                "Refusing to start Tunnelmole: the server is still using the "
                "default bootstrap password. Change the password in the admin "
                "UI before exposing /auth publicly, or set "
                "%s=1 to override (not recommended).",
                runtime._ALLOW_DEFAULT_PASSWORD_ON_TUNNEL_ENV,
            )
            return False

        auth_thread = await runtime.start_auth_server_background()
        if auth_thread is False:
            runtime.LOGGER.warning(
                "Refusing to start Tunnelmole because auth/signaling server is unavailable on port %s",
                runtime._get_tunnelmole_target_port(),
            )
            return False

        await runtime.stop_tunnelmole_service(reason="Restarting public pairing tunnel")

        service = runtime._get_or_create_tunnelmole_service()
        service.set_port(runtime._get_tunnelmole_target_port())
        website_port = runtime._get_tunnelmole_hosted_website_port(runtime.STATE.config or {})
        if hasattr(service, "set_website_port"):
            service.set_website_port(website_port)
        if website_port and runtime.AUTOYOU_PAGE_SERVICE_AVAILABLE:
            try:
                if not await runtime.is_autoyou_page_service_running():
                    await runtime.start_autoyou_page_service_background()
            except Exception as exc:
                runtime.LOGGER.warning("Public website page service readiness check failed: %s", exc)
        # When the user holds the `tm` entitlement, mint a worker JWT and stash
        # it in the env vars TunnelmoleService reads to provision a paid
        # persistent URL via tm.autoyou.me. No-op for free-tier users.
        await runtime._maybe_apply_paid_tunnelmole_env()
        success = await service.start()
        status_info = service.get_status()

        if success:
            _raw_public_url = status_info.get("public_url") or ""
            _display_public_url = (
                _raw_public_url
                if runtime._should_log_tunnelmole_url_plain()
                else runtime._redact_tunnelmole_url_for_log(_raw_public_url)
            )
            runtime.LOGGER.info(
                "Tunnelmole public auth tunnel started: %s -> localhost:%s",
                _display_public_url,
                runtime._get_tunnelmole_target_port(),
            )
            return True

        await runtime.stop_auth_server()
        runtime.LOGGER.warning("Failed to start Tunnelmole public auth tunnel on port %s", runtime._get_tunnelmole_target_port())
        return False

    except Exception as e:
        runtime.LOGGER.error(f"Failed to start Tunnelmole service: {e}")
        return False


async def stop_tunnelmole_service(reason: str = "Server initiated shutdown"):
    """Stop the Tunnelmole service and cleanup resources."""
    runtime = _runtime()
    success = True
    try:
        timer_stopped = await runtime.stop_tunnelmole_timer()
        if timer_stopped:
            runtime.LOGGER.info("Tunnelmole timer stopped")
        else:
            success = False

        service = runtime.STATE.tunnelmole_service
        if isinstance(service, runtime.TunnelmoleService):
            await runtime.asyncio.to_thread(service.stop)

        runtime.LOGGER.info("Tunnelmole service stopped (%s)", reason)
        return success

    except Exception as e:
        runtime.LOGGER.error(f"Error stopping Tunnelmole service: {e}")
        return False


def get_tunnelmole_status() -> dict:
    """Get current Tunnelmole service status."""
    runtime = _runtime()
    try:
        tunnelmole_config = runtime.STATE.config.get("tunnelmole", {}) if runtime.STATE.config else {}
        enabled = tunnelmole_config.get("enabled", True)
        target_port = runtime._get_tunnelmole_target_port()
        service = runtime.STATE.tunnelmole_service

        if isinstance(service, runtime.TunnelmoleService):
            service.set_port(target_port)
            if hasattr(service, "set_website_port"):
                service.set_website_port(runtime._get_tunnelmole_hosted_website_port(runtime.STATE.config or {}))
            status_info = service.get_status()
            if str(status_info.get("status") or "").strip().lower() in {"running", "starting", "error"}:
                return status_info

        if not enabled:
            return {"status": "disabled", "public_url": None, "port": target_port}
        return {"status": "stopped", "public_url": None, "port": target_port}

    except Exception as e:
        runtime.LOGGER.error(f"Error getting Tunnelmole status: {e}")
        return {"status": "error", "error": str(e), "port": runtime._get_tunnelmole_target_port()}


async def _whatsapp_status() -> tuple[str, str]:
    """Get WhatsApp service status for admin dashboard."""
    runtime = _runtime()
    try:
        whatsapp_config = runtime.STATE.config.get("whatsapp", {}) if runtime.STATE.config else {}
        enabled = whatsapp_config.get("enabled", False)

        # If WhatsApp is disabled in config, return disabled status
        if not enabled and runtime.STATE.whatsapp_service is None:
            return "Disabled", "-"

        # If enabled but service not initialized, return not started
        service = runtime.STATE.whatsapp_service
        if service is None:
            return "Not started", "-"

        # Get WhatsApp service status
        status = await service.get_status()

        transport_ready = bool(status.get("connection_healthy")) or (
            bool(status.get("ready")) and bool(status.get("phone_number"))
        )
        if status.get("paired") or transport_ready:
            phone = status.get("phone_number", "Unknown")
            return "Connected", phone
        elif status.get("ready"):
            return "Ready for pairing", "-"
        else:
            return "Starting up", "-"

    except Exception as e:
        runtime.LOGGER.warning(f"WhatsApp status check failed: {e}")
        return "Error", str(e)[:50] + "..." if len(str(e)) > 50 else str(e)


def _autostart_allowed_on_default_password() -> bool:
    """Opt-in escape hatch for unattended startup on the default password."""
    runtime = _runtime()
    return str(runtime.os.getenv("AUTOYOU_AUTOSTART_ON_DEFAULT_PASSWORD", "")).strip().lower() in {"1", "true", "yes", "on"}


def should_start_ai_agent_server():
    """Check if AI Agent Server should be started automatically."""
    runtime = _runtime()
    try:
        if not runtime._has_loaded_config_session() or not runtime.STATE.config:
            runtime.LOGGER.info("Config not loaded from a secure store, AI Agent Server will not start automatically")
            return False

        # Default-password security gate: when the server is protected only by the
        # well-known default bootstrap password, do NOT auto-warm AutoYou AI
        # (nor the managed frontends it syncs) at boot. Require an explicit human
        # unlock - "Start"/"Unlock" -> POST /login -> _initialize_services_on_startup -
        # so that on a fresh default-password install literally nothing runs until a
        # human acts. Unattended deployments set a non-default password (keystore /
        # AUTOYOU_SERVER_PASSWORD) or opt in via AUTOYOU_AUTOSTART_ON_DEFAULT_PASSWORD=1.
        if getattr(runtime.STATE, "used_default_password", False) and not runtime._autostart_allowed_on_default_password():
            runtime.LOGGER.info(
                "Default bootstrap password in effect; AI Agent Server will NOT auto-start. "
                "Unlock at the admin UI (Start/Unlock) to launch services, or set a non-default "
                "password / AUTOYOU_AUTOSTART_ON_DEFAULT_PASSWORD=1 for unattended startup."
            )
            return False

        ai_agent_config = runtime.STATE.config.get("ai_agent", {})
        if runtime._is_native_gateway_provider():
            runtime.LOGGER.info("Native gateway selected; AI Agent Server will not start automatically")
            return False
        if not ai_agent_config.get("enabled", True):
            runtime.LOGGER.info("AI Agent Server not enabled in config, will not start automatically")
            return False

        if not ai_agent_config.get("auto_start", True):
            runtime.LOGGER.info("AI Agent Server auto-start disabled in config")
            return False

        return True
    except Exception as e:
        runtime.LOGGER.error(f"Error checking AI Agent Server startup conditions: {e}")
        return False


def _set_ai_agent_multiprocessing_executable() -> None:
    runtime = _runtime()
    executable = str(getattr(runtime.sys, "executable", "") or "").strip()
    if not executable:
        return
    try:
        runtime.multiprocessing.set_executable(executable)
    except Exception as exc:
        runtime.LOGGER.debug("Could not set AI Agent multiprocessing executable to %s: %s", executable, exc)


async def start_ai_agent_server_background() -> bool:
    """Start AI Agent Server in background process."""
    runtime = _runtime()
    runtime._invalidate_admin_status_cache("ai_agent_status")
    runtime.STATE.main_server_port = runtime.AI_AGENT_SERVER_PORT
    # Loopback-only by default regardless of the admin UI's own bind host --
    # the AI Agent server (raw ADK dev-ui/API) has no authentication of its
    # own unless LAN access has been explicitly opted into.
    ai_agent_host = runtime._configured_ai_agent_bind_host(runtime.STATE.config)
    probe_host = runtime._normalize_probe_host(ai_agent_host)
    runtime.LOGGER.info("Preparing AI Agent Server startup on %s:%s", probe_host, runtime.AI_AGENT_SERVER_PORT)

    async def _sync_frontends_after_ai_attempt() -> None:
        try:
            await runtime.sync_managed_frontend_backends()
        except FileNotFoundError as exc:
            runtime.LOGGER.warning("Could not sync managed frontend backends (agents path issue): %s", exc)
            runtime.LOGGER.info("Server will continue running with limited agent management")

    # A live worker PID is not enough: the admin startup path can call this
    # while an earlier autostart task is still warming the ADK app. Treat the
    # HTTP health endpoint as the source of truth so first-use callers do not
    # see a false "running" state on port 8081.
    if runtime._is_agent_process_running(runtime.STATE.agent_process):
        if await runtime.asyncio.to_thread(
            runtime._is_ai_agent_server_healthy,
            probe_host,
            runtime.AI_AGENT_SERVER_PORT,
        ):
            runtime.LOGGER.info("AI Agent Server is already running and healthy")
            await runtime.asyncio.to_thread(runtime._ensure_local_ollama_runtime_ready)
            await _sync_frontends_after_ai_attempt()
            return True

        runtime.LOGGER.info(
            "AI Agent worker process is running but %s:%s is not healthy yet; waiting for readiness",
            probe_host,
            runtime.AI_AGENT_SERVER_PORT,
        )
        if await runtime._wait_for_ai_agent_server_ready(
            probe_host,
            runtime.AI_AGENT_SERVER_PORT,
            process=runtime.STATE.agent_process,
        ):
            runtime.LOGGER.info("AI Agent Server became healthy on %s:%s", probe_host, runtime.AI_AGENT_SERVER_PORT)
            await _sync_frontends_after_ai_attempt()
            return True

        if runtime._is_agent_process_running(runtime.STATE.agent_process):
            runtime.LOGGER.warning(
                "AI Agent worker stayed unhealthy on %s:%s; restarting it",
                probe_host,
                runtime.AI_AGENT_SERVER_PORT,
            )
            await runtime.stop_ai_agent_server()
        else:
            runtime.LOGGER.warning(
                "AI Agent worker exited before %s:%s became healthy",
                probe_host,
                runtime.AI_AGENT_SERVER_PORT,
            )
            try:
                runtime._close_process_handle(runtime.STATE.agent_process)
            except Exception:
                pass
            runtime.STATE.agent_process = None

    await runtime.asyncio.to_thread(runtime._ensure_local_ollama_runtime_ready)

    port_in_use = await runtime.asyncio.to_thread(runtime.is_port_in_use, runtime.AI_AGENT_SERVER_PORT, probe_host)
    if port_in_use and runtime.os.environ.get("AUTOYOU_NATIVE_OWNED_SERVER") == "1":
        # An owned desktop server must never adopt or clean up another app's
        # worker, even when that listener presents a compatible health route.
        runtime.LOGGER.error("Owned AI Agent port %s is occupied by another process", runtime.AI_AGENT_SERVER_PORT)
        await _sync_frontends_after_ai_attempt()
        return False
    if port_in_use:
        cleaned_count = await runtime.asyncio.to_thread(
            runtime._cleanup_lingering_ai_agent_processes,
            runtime.AI_AGENT_SERVER_PORT,
        )
        if cleaned_count:
            runtime.LOGGER.info(
                "Stopped %d lingering AI Agent worker(s) on port %s before restart",
                cleaned_count,
                runtime.AI_AGENT_SERVER_PORT,
            )
            for _ in range(10):
                port_in_use = await runtime.asyncio.to_thread(
                    runtime.is_port_in_use,
                    runtime.AI_AGENT_SERVER_PORT,
                    probe_host,
                )
                if not port_in_use:
                    break
                await runtime.asyncio.sleep(0.2)

    if port_in_use:
        if await runtime.asyncio.to_thread(
            runtime._is_ai_agent_server_healthy,
            probe_host,
            runtime.AI_AGENT_SERVER_PORT,
        ):
            runtime.LOGGER.info(
                "Reusing existing AI Agent Server already listening on %s:%s",
                probe_host,
                runtime.AI_AGENT_SERVER_PORT,
            )
            await _sync_frontends_after_ai_attempt()
            return True

        runtime.LOGGER.error(
            "AI Agent Server port %s is already in use on %s by another process.",
            runtime.AI_AGENT_SERVER_PORT,
            probe_host,
        )
        await _sync_frontends_after_ai_attempt()
        return False

    env_vars = runtime.add_parent_pid_environment(runtime._prepare_ai_agent_process_env(runtime.os.environ.copy()))

    if runtime.is_compiled():
        runtime.LOGGER.info(
            "Launching compiled AI Agent worker via %s on %s:%s",
            runtime.sys.executable,
            ai_agent_host,
            runtime.AI_AGENT_SERVER_PORT,
        )
        runtime.STATE.agent_process = await runtime.asyncio.to_thread(
            runtime._start_compiled_ai_agent_process,
            ai_agent_host,
            runtime.AI_AGENT_SERVER_PORT,
            runtime.AGENT_DIR,
            env_vars,
        )
    else:
        runtime._set_ai_agent_multiprocessing_executable()
        runtime.STATE.agent_process = runtime.multiprocessing.Process(
            target=runtime.run_agent_server,
            args=(ai_agent_host, runtime.AI_AGENT_SERVER_PORT, runtime.AGENT_DIR, env_vars),
            daemon=False,
        )
        runtime.STATE.agent_process.start()
    runtime.LOGGER.info(
        "AI Agent process started on port %s (PID %s)",
        runtime.AI_AGENT_SERVER_PORT,
        runtime._get_agent_process_pid(runtime.STATE.agent_process),
    )

    # Wait briefly for startup and require the HTTP health endpoint to respond.
    # This avoids false positives when a stale process already occupies the port.
    became_ready = await runtime._wait_for_ai_agent_server_ready(
        probe_host,
        runtime.AI_AGENT_SERVER_PORT,
        process=runtime.STATE.agent_process,
    )
    if became_ready:
        runtime.LOGGER.info(f"AI Agent Server is accepting connections on {probe_host}:{runtime.AI_AGENT_SERVER_PORT}")

    if not became_ready:
        runtime.LOGGER.warning(f"AI Agent Server did not become ready within timeout on {probe_host}:{runtime.AI_AGENT_SERVER_PORT}")
        proc = runtime.STATE.agent_process
        if proc is not None:
            if runtime._is_agent_process_running(proc):
                runtime.LOGGER.warning("Stopping unhealthy AI Agent worker after startup timeout")
                process_pid = runtime._get_agent_process_pid(proc)
                tracked_pids = runtime._collect_process_tree_pids(process_pid)
                try:
                    proc.terminate()
                    await runtime.asyncio.to_thread(runtime._wait_for_agent_process_exit, proc, 5)
                except Exception:
                    pass
                if runtime.live_pids(tracked_pids):
                    await runtime.asyncio.to_thread(runtime._force_kill_process_tree, process_pid, tracked_pids)
            try:
                runtime._close_process_handle(proc)
            except Exception:
                pass
        runtime.STATE.agent_process = None
        await _sync_frontends_after_ai_attempt()
        return False

    await _sync_frontends_after_ai_attempt()
    return True


def schedule_ai_agent_server_autostart() -> bool:
    """Schedule AI Agent startup without blocking admin server binding."""
    runtime = _runtime()
    if runtime._is_agent_process_running(runtime.STATE.agent_process):
        runtime.LOGGER.info("AI Agent Server is already running; skipping background autostart")
        return False

    existing_task = runtime.STATE.ai_agent_start_task
    if existing_task is not None and not existing_task.done():
        runtime.LOGGER.info("AI Agent Server autostart is already in progress")
        return False

    async def _run_autostart() -> None:
        try:
            await runtime.start_ai_agent_server_background()
        except Exception as exc:
            runtime.LOGGER.error("Background AI Agent autostart failed: %s", exc, exc_info=True)
        finally:
            runtime.STATE.ai_agent_start_task = None

    runtime.STATE.ai_agent_start_task = runtime.asyncio.create_task(_run_autostart())
    runtime.LOGGER.info(
        "Scheduled AI Agent Server autostart in background on %s:%s",
        runtime._normalize_probe_host(runtime._configured_ai_agent_bind_host(runtime.STATE.config)),
        runtime.AI_AGENT_SERVER_PORT,
    )
    return True


async def start_auth_server_background():
    """Start Auth Server in background thread."""
    runtime = _runtime()
    if runtime.auth_server_thread and runtime.auth_server_thread.is_alive():
        runtime.LOGGER.info("Auth Server is already running")
        return runtime.auth_server_thread

    # Capture the main event loop so cross-thread callbacks (auth server runs
    # on its own loop) can schedule coroutines here via run_coroutine_threadsafe.
    try:
        runtime.STATE._main_loop = runtime.asyncio.get_running_loop()
    except RuntimeError:
        pass

    def _run_auth():
        loop = None
        try:
            loop = runtime.asyncio.new_event_loop()
            runtime.asyncio.set_event_loop(loop)
            config = runtime.uvicorn.Config(
                runtime.auth_app,
                host=runtime.SERVER_BIND_HOST,
                port=runtime.AUTH_SERVER_PORT,
                log_level="info",
                access_log=False,
            )
            runtime.auth_server_instance = runtime.uvicorn.Server(config)
            runtime.LOGGER.info(f"Starting Auth Server on {runtime.SERVER_BIND_HOST}:{runtime.AUTH_SERVER_PORT}")
            loop.run_until_complete(runtime.auth_server_instance.serve())
        except Exception as e:
            runtime.LOGGER.error(f"Error running Auth Server: {e}")
        finally:
            if loop is not None:
                loop.close()

    runtime.auth_server_thread = runtime.threading.Thread(target=_run_auth, daemon=True)
    runtime.auth_server_thread.start()
    runtime.LOGGER.info(f"Auth Server thread started on port {runtime.AUTH_SERVER_PORT}")

    # Wait briefly for socket readiness to reduce /pair -> /auth race conditions.
    # is_port_in_use() is synchronous - run in thread to keep event loop free.
    probe_host = runtime._normalize_probe_host(runtime.SERVER_BIND_HOST)
    for _ in range(25):  # ~5 seconds max
        in_use = await runtime.asyncio.to_thread(runtime.is_port_in_use, runtime.AUTH_SERVER_PORT, probe_host)
        if in_use:
            runtime.LOGGER.info(f"Auth Server is accepting connections on {probe_host}:{runtime.AUTH_SERVER_PORT}")
            break
        await runtime.asyncio.sleep(0.2)
    else:
        runtime.LOGGER.warning(f"Auth Server did not become ready within timeout on {probe_host}:{runtime.AUTH_SERVER_PORT}")

    return runtime.auth_server_thread


async def stop_ai_agent_server():
    """Stop AI Agent Server process."""
    runtime = _runtime()
    runtime._invalidate_admin_status_cache("ai_agent_status")
    start_task = runtime.STATE.ai_agent_start_task
    current_task = runtime.asyncio.current_task()
    if start_task is not None and not start_task.done() and start_task is not current_task:
        start_task.cancel()
        try:
            await start_task
        except runtime.asyncio.CancelledError:
            pass
        except Exception as exc:
            runtime.LOGGER.debug("AI Agent autostart task ended during shutdown: %s", exc)
        finally:
            runtime.STATE.ai_agent_start_task = None
    elif start_task is current_task:
        runtime.STATE.ai_agent_start_task = None

    if runtime.STATE.agent_process:
        try:
            runtime.LOGGER.info("Shutting down AI Agent Server process...")
            proc = runtime.STATE.agent_process  # capture ref before finally clears it
            process_pid = runtime._get_agent_process_pid(proc)
            tracked_pids = runtime._collect_process_tree_pids(process_pid)
            proc.terminate()
            runtime.LOGGER.info("Waiting for AI Agent Server process to terminate...")
            await runtime.asyncio.to_thread(runtime._wait_for_agent_process_exit, proc, 10)
            if runtime._is_agent_process_running(proc):
                runtime.LOGGER.warning("AI Agent Server process did not stop gracefully, forcing kill...")
                runtime._kill_agent_process(proc)
                await runtime.asyncio.to_thread(runtime._wait_for_agent_process_exit, proc, 5)
            if runtime.live_pids(tracked_pids):
                runtime.LOGGER.warning("AI Agent Server process still appears alive, forcing process tree cleanup...")
                await runtime.asyncio.to_thread(runtime._force_kill_process_tree, process_pid, tracked_pids)
            runtime.LOGGER.info("AI Agent Server process stopped successfully")
        except runtime.subprocess.TimeoutExpired:
            runtime.LOGGER.warning("AI Agent Server process wait timed out; forcing kill...")
            try:
                runtime._kill_agent_process(proc)
            except Exception:
                pass
        except Exception as e:
            runtime.LOGGER.error(f"Error terminating AI Agent Server process: {e}")
        finally:
            try:
                runtime._close_process_handle(proc)
            except Exception:
                pass
            runtime.STATE.agent_process = None


async def stop_auth_server():
    """Stop Auth Server."""
    runtime = _runtime()

    if runtime.auth_server_instance:
        try:
            runtime.LOGGER.info("Shutting down Auth Server...")
            runtime.auth_server_instance.should_exit = True
            runtime.LOGGER.info("Auth Server shutdown signal sent")
        except Exception as e:
            runtime.LOGGER.error(f"Error signaling Auth Server shutdown: {e}")
        finally:
            runtime.auth_server_instance = None

    if runtime.auth_server_thread and runtime.auth_server_thread.is_alive():
        runtime.LOGGER.info("Waiting for Auth Server thread to stop...")
        _thread_ref = runtime.auth_server_thread
        try:
            await runtime.asyncio.to_thread(_thread_ref.join, 10)
        except Exception as e:
            runtime.LOGGER.warning(f"Error joining Auth Server thread: {e}")
        if _thread_ref.is_alive():
            runtime.LOGGER.warning("Auth Server thread did not stop gracefully within timeout")
        else:
            runtime.LOGGER.info("Auth Server thread stopped successfully")
        runtime.auth_server_thread = None


def validate_ai_agent_port_change(port: int) -> None:
    """Check a requested port before saving settings or stopping the old worker."""
    runtime = _runtime()
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("AI Agent port must be an integer from 1 through 65535")
    if port == runtime.AI_AGENT_SERVER_PORT:
        return
    host = runtime._configured_ai_agent_bind_host(runtime.STATE.config)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            raise ValueError("The requested AI Agent port is unavailable. Choose a different port.") from exc


def set_ai_agent_runtime_port(port: int) -> None:
    """Keep HTTP routing and future worker launches on the same live port."""
    runtime = _runtime()
    runtime.AI_AGENT_SERVER_PORT = port
    runtime.STATE.main_server_port = port
    runtime.os.environ["AUTOYOU_AI_PORT"] = str(port)
    runtime.os.environ["AI_AGENT_SERVER_PORT"] = str(port)
    if runtime.STATE.service_manager is not None:
        runtime.STATE.service_manager.config.ai_agent_server_port = port


def apply_native_saved_ai_port() -> None:
    """Restore the encrypted setting after unlock, avoiding other local services."""
    runtime = _runtime()
    env = runtime.os.environ
    if env.get("AUTOYOU_NATIVE_OWNED_SERVER") != "1" or env.get("AUTOYOU_NATIVE_AI_PORT_AUTOMATIC") != "1":
        return
    saved = (runtime.STATE.config or {}).get("ai_agent", {}).get("port")
    if type(saved) is not int or not 1024 <= saved <= 65535:
        return
    reserved = {int(env[key]) for key in ("AUTOYOU_ADMIN_PORT", "AUTOYOU_AUTH_PORT", "AUTOYOU_PAGE_PORT")
                if str(env.get(key, "")).isdigit()}
    host = runtime._configured_ai_agent_bind_host(runtime.STATE.config)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            if saved in reserved:
                raise OSError("Port belongs to another service in this instance")
            probe.bind((host, saved))
        except OSError:
            probe.bind((host, 0))
        selected = probe.getsockname()[1]
        if selected in reserved:
            raise ValueError("No separate AI port was allocated. Retry starting AutoYou.")
    if selected != saved:
        runtime.LOGGER.warning("Saved AI Agent port %s is unavailable; this launch uses %s", saved, selected)
    set_ai_agent_runtime_port(selected)


async def restart_ai_agent_server(*, port: Optional[int] = None):
    """Restart AI Agent Server."""
    runtime = _runtime()
    if port is not None:
        validate_ai_agent_port_change(port)
    runtime.LOGGER.info("Restarting AI Agent Server...")
    await runtime.stop_ai_agent_server()
    if port is not None:
        set_ai_agent_runtime_port(port)
    await runtime.asyncio.sleep(1)  # Brief pause before restart
    return await runtime.start_ai_agent_server_background()


async def _ai_agent_server_status() -> str:
    """Get AI Agent Server status."""
    runtime = _runtime()
    try:
        probe_host = runtime._normalize_probe_host(runtime.SERVER_BIND_HOST)
        if runtime._is_agent_process_running(runtime.STATE.agent_process) or await runtime.asyncio.to_thread(
            runtime._is_ai_agent_server_healthy,
            probe_host,
            runtime.AI_AGENT_SERVER_PORT,
            1.5,
        ):
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://{probe_host}:{runtime.AI_AGENT_SERVER_PORT}/health",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as response:
                        if response.status == 200:
                            return "Running"
                        return f"Running (HTTP {response.status})"
            except Exception:
                return "Starting"
        return "Stopped"
    except Exception as e:
        runtime.LOGGER.error(f"Error checking AI Agent Server status: {e}")
        return "Unknown"


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is currently in use on the given host.

    Tries to establish a connection to the host/port. If connection succeeds,
    the port is considered in use; otherwise it is available.
    """
    runtime = _runtime()
    try:
        with runtime.socket.socket(runtime.socket.AF_INET, runtime.socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            return sock.connect_ex((host, port)) == 0
    except Exception:
        # If any error occurs during check, conservatively assume in use
        return True


def _normalize_probe_host(host: str) -> str:
    runtime = _runtime()
    return "127.0.0.1" if host in ("0.0.0.0", "::") else host  # nosec B104 - comparison only, not a bind call


def _find_available_local_port(
    preferred_port: int,
    *,
    host: str = "127.0.0.1",
    max_tries: int = 25,
) -> int:
    runtime = _runtime()
    if preferred_port > 0 and not runtime.is_port_in_use(preferred_port, host=host):
        return preferred_port

    start_port = max(1024, int(preferred_port or 0) + 1)
    for candidate in range(start_port, start_port + max_tries):
        if not runtime.is_port_in_use(candidate, host=host):
            return candidate

    with runtime.socket.socket(runtime.socket.AF_INET, runtime.socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _agent_frontend_backend_app_exists(agent_dir: Path) -> bool:
    runtime = _runtime()
    backend_dir = agent_dir / "website" / "backend"
    if (backend_dir / "app.py").is_file():
        return True
    for suffix in runtime.importlib.machinery.EXTENSION_SUFFIXES:
        if (backend_dir / f"app{suffix}").is_file():
            return True
    return False


def _managed_frontend_runtime_specs() -> Dict[str, Dict[str, Any]]:
    runtime = _runtime()
    specs: runtime.Dict[str, runtime.Dict[str, runtime.Any]] = {}
    for agent_name, config in runtime.MANAGED_FRONTEND_APPS.items():
        manifest = runtime.load_frontend_manifest(runtime._AUTOYOU_AGENTS_ROOT / agent_name)
        if not manifest or not manifest.get("requires_proxy_registration", True):
            continue
        recommended_port = manifest.get("recommended_port") or config.get("default_port")
        specs[agent_name] = {
            **config,
            "agent_name": agent_name,
            "recommended_port": int(recommended_port) if recommended_port else None,
        }

    # Also discover dynamically installed agents (not in MANAGED_FRONTEND_APPS) that
    # ship a website backend so their proxy gets started after `install` via sync.
    try:
        workspace_root = runtime._workspace_agents_root()
        installed_names = set(
            runtime.load_agent_install_registry(agents_root=workspace_root).get("installed_agents", [])
        )
        for agent_name in sorted(installed_names):
            if agent_name in specs:
                continue
            for search_root in (workspace_root, runtime._AUTOYOU_AGENTS_ROOT):
                agent_dir = search_root / agent_name
                manifest = runtime.load_frontend_manifest(agent_dir)
                if not manifest or not manifest.get("requires_proxy_registration", False):
                    continue
                if not runtime._agent_frontend_backend_app_exists(agent_dir):
                    continue
                recommended_port = manifest.get("recommended_port")
                specs[agent_name] = {
                    "agent_name": agent_name,
                    "app_import": f"autoyou_agents.{agent_name}.website.backend.app:app",
                    "recommended_port": int(recommended_port) if recommended_port else None,
                }
                break
    except Exception:
        pass

    return specs


def _find_managed_frontend_module_path(module_name: str) -> Optional[Path]:
    runtime = _runtime()
    module_parts = str(module_name or "").split(".")
    if len(module_parts) < 2 or module_parts[0] != "autoyou_agents":
        return None

    module_dir = runtime._AUTOYOU_AGENTS_ROOT.joinpath(*module_parts[1:-1])
    module_stem = module_parts[-1]
    if not module_dir.is_dir():
        return None

    for suffix in runtime.importlib.machinery.EXTENSION_SUFFIXES:
        candidate = module_dir / f"{module_stem}{suffix}"
        if candidate.is_file():
            return candidate

    source_candidate = module_dir / f"{module_stem}.py"
    if source_candidate.is_file():
        return source_candidate

    return None


def _prepend_import_path(path: Path) -> None:
    runtime = _runtime()
    try:
        resolved = str(path.resolve())
    except Exception:
        resolved = str(path)
    if resolved not in runtime.sys.path:
        runtime.sys.path.insert(0, resolved)


def _append_package_search_path(package_module: types.ModuleType, package_dir: Path) -> None:
    runtime = _runtime()
    if not package_dir.is_dir():
        return
    try:
        resolved = str(package_dir.resolve())
    except Exception:
        resolved = str(package_dir)

    existing_path = getattr(package_module, "__path__", None)
    existing_entries = list(existing_path) if existing_path is not None else []
    if resolved not in existing_entries:
        package_module.__path__ = [*existing_entries, resolved]


def _ensure_managed_frontend_runtime_import_paths() -> None:
    """Expose compiled runtime module roots to fallback-loaded frontend apps."""
    runtime = _runtime()
    try:
        runtime_modules_root = runtime._AUTOYOU_AGENTS_ROOT.resolve().parent
        if runtime_modules_root.is_dir():
            runtime._prepend_import_path(runtime_modules_root)
            runtime._ensure_runtime_package_module("shared", runtime_modules_root / "shared")
            runtime._ensure_runtime_package_module("autoyou_agents", runtime._AUTOYOU_AGENTS_ROOT)
    except Exception as exc:
        runtime.LOGGER.debug("Could not configure managed frontend runtime_modules path: %s", exc)

    try:
        runtime_site_packages = runtime.get_resources_root(runtime.__file__).resolve() / "runtime_site_packages"
        if runtime_site_packages.is_dir():
            runtime._prepend_import_path(runtime_site_packages)
    except Exception as exc:
        runtime.LOGGER.debug("Could not configure managed frontend runtime_site_packages path: %s", exc)


def _ensure_runtime_package_module(package_name: str, package_dir: Path) -> None:
    runtime = _runtime()
    existing_module = runtime.sys.modules.get(package_name)
    if existing_module is not None:
        runtime._append_package_search_path(existing_module, package_dir)
        return

    package_module = runtime.types.ModuleType(package_name)
    package_module.__file__ = str(package_dir)
    package_module.__package__ = package_name
    package_module.__path__ = [str(package_dir)]
    runtime.sys.modules[package_name] = package_module


def _load_managed_frontend_app(spec: Dict[str, Any]) -> Any:
    runtime = _runtime()
    app_import = str(spec.get("app_import") or "").strip()
    module_name, separator, attribute_name = app_import.partition(":")
    if not module_name or not separator or not attribute_name:
        raise ValueError(f"Invalid managed frontend app_import: {app_import!r}")

    runtime._ensure_managed_frontend_runtime_import_paths()

    try:
        module = runtime.importlib.import_module(module_name)
    except ModuleNotFoundError as import_error:
        module_path = runtime._find_managed_frontend_module_path(module_name)
        if module_path is None:
            raise

        package_parts = module_name.split(".")[:-1]
        if package_parts and package_parts[0] == "autoyou_agents":
            if "autoyou_agents" not in runtime.sys.modules:
                runtime.importlib.import_module("autoyou_agents")
            for depth in range(1, len(package_parts)):
                package_name = ".".join(package_parts[: depth + 1])
                package_dir = runtime._AUTOYOU_AGENTS_ROOT.joinpath(*package_parts[1 : depth + 1])
                runtime._ensure_runtime_package_module(package_name, package_dir)
            runtime._ensure_runtime_package_module(
                "autoyou_agents.shared_tools",
                runtime._AUTOYOU_AGENTS_ROOT / "shared_tools",
            )

        module_spec = runtime.importlib.util.spec_from_file_location(module_name, module_path)
        if module_spec is None or module_spec.loader is None:
            raise ImportError(
                f"Could not create managed frontend module spec for {module_name!r} at {module_path}"
            ) from import_error

        module = runtime.importlib.util.module_from_spec(module_spec)
        runtime.sys.modules[module_name] = module
        module_spec.loader.exec_module(module)

    return getattr(module, attribute_name)


async def _stop_managed_frontend_backend(agent_name: str) -> None:
    runtime = _runtime()
    handle = dict((runtime.STATE.managed_frontend_servers or {}).pop(agent_name, {}) or {})
    runtime.STATE.dynamic_agent_proxy_ports.pop(agent_name, None)
    server_instance = handle.get("server")
    thread = handle.get("thread")

    if server_instance is not None:
        try:
            server_instance.should_exit = True
        except Exception as exc:
            runtime.LOGGER.warning("Failed to signal managed frontend shutdown for %s: %s", agent_name, exc)

    if thread and thread.is_alive():
        await runtime.asyncio.to_thread(thread.join, 10)
        if thread.is_alive():
            runtime.LOGGER.warning("Managed frontend thread for %s did not stop within timeout", agent_name)


async def _start_managed_frontend_backend(agent_name: str) -> Optional[int]:
    runtime = _runtime()
    specs = runtime._managed_frontend_runtime_specs()
    spec = specs.get(agent_name)
    if not spec:
        await runtime._stop_managed_frontend_backend(agent_name)
        return None

    existing = (runtime.STATE.managed_frontend_servers or {}).get(agent_name) or {}
    existing_thread = existing.get("thread")
    existing_port = existing.get("port")
    if (
        existing_thread
        and existing_thread.is_alive()
        and existing_port
        and runtime.is_port_in_use(int(existing_port), host="127.0.0.1")
    ):
        runtime.STATE.dynamic_agent_proxy_ports[agent_name] = int(existing_port)
        return int(existing_port)

    if existing:
        await runtime._stop_managed_frontend_backend(agent_name)

    preferred_port = int(spec.get("recommended_port") or spec.get("default_port") or 0)
    target_port = runtime._find_available_local_port(preferred_port, host="127.0.0.1")
    try:
        app_target = runtime._load_managed_frontend_app(spec)
    except Exception as exc:
        runtime.LOGGER.error(
            "Managed frontend backend import failed for %s: %s",
            agent_name,
            exc,
            exc_info=True,
        )
        await runtime._stop_managed_frontend_backend(agent_name)
        return None
    config = runtime.uvicorn.Config(
        app_target,
        host="127.0.0.1",
        port=target_port,
        log_level="warning",
        access_log=False,
    )
    server_instance = runtime.uvicorn.Server(config)

    def _run_frontend_server() -> None:
        loop = None
        try:
            loop = runtime.asyncio.new_event_loop()
            runtime.asyncio.set_event_loop(loop)
            loop.run_until_complete(server_instance.serve())
        except Exception as exc:
            runtime.LOGGER.error("Managed frontend backend for %s crashed: %s", agent_name, exc, exc_info=True)
        finally:
            if loop is not None:
                loop.close()

    thread = runtime.threading.Thread(
        target=_run_frontend_server,
        daemon=True,
        name=f"{agent_name}-frontend",
    )
    runtime.STATE.managed_frontend_servers[agent_name] = {
        "agent_name": agent_name,
        "port": target_port,
        "server": server_instance,
        "thread": thread,
        "app_import": spec["app_import"],
    }
    thread.start()

    for _ in range(40):
        if runtime.is_port_in_use(target_port, host="127.0.0.1"):
            runtime.STATE.dynamic_agent_proxy_ports[agent_name] = int(target_port)
            runtime.LOGGER.info(
                "Managed frontend backend started: agent=%s port=%d import=%s",
                agent_name,
                target_port,
                spec["app_import"],
            )
            return int(target_port)
        await runtime.asyncio.sleep(0.25)

    runtime.LOGGER.warning(
        "Managed frontend backend for %s did not become ready on port %d",
        agent_name,
        target_port,
    )
    await runtime._stop_managed_frontend_backend(agent_name)
    return None


def _register_admin_frontend_proxy() -> None:
    """Register or deregister the admin_agent → admin web service port proxy.

    When enabled in config, WebRTC browser clients can reach the admin web UI
    at /agent/admin_agent/ which the datachannel manager forwards to ADMIN_WEB_SERVICE_PORT.
    No additional server is started - the existing admin server handles the requests.
    """
    runtime = _runtime()
    cfg = runtime.STATE.config or {}
    enabled = runtime._get_agent_frontend_enabled("admin_agent", cfg=cfg)
    if enabled:
        runtime.STATE.dynamic_agent_proxy_ports["admin_agent"] = runtime.ADMIN_WEB_SERVICE_PORT
        runtime.LOGGER.info(
            "Admin frontend proxy registered: /agent/admin_agent/ → port %d",
            runtime.ADMIN_WEB_SERVICE_PORT,
        )
    else:
        runtime.STATE.dynamic_agent_proxy_ports.pop("admin_agent", None)
        runtime.LOGGER.debug("Admin frontend proxy deregistered.")
    try:
        runtime._sync_frontend_registry_from_builder_payload(runtime._build_agent_builder_listing_payload())
    except Exception as exc:
        runtime.LOGGER.warning("Failed to refresh frontend registry after admin proxy change: %s", exc)


async def sync_managed_frontend_backends() -> Dict[str, Optional[int]]:
    runtime = _runtime()
    install_registry = runtime.refresh_agent_install_registry(agents_root=runtime._AUTOYOU_AGENTS_ROOT)
    installed_agents = set(install_registry.get("installed_agents", []))
    specs = runtime._managed_frontend_runtime_specs()
    desired_agents = {
        agent_name
        for agent_name in specs
        if agent_name in installed_agents and runtime._get_agent_frontend_enabled(agent_name, cfg=(runtime.STATE.config or {}))
    }
    active_agents = set((runtime.STATE.managed_frontend_servers or {}).keys())

    for agent_name in sorted(active_agents - desired_agents):
        await runtime._stop_managed_frontend_backend(agent_name)

    started_ports: runtime.Dict[str, runtime.Optional[int]] = {}
    for agent_name in sorted(desired_agents):
        started_ports[agent_name] = await runtime._start_managed_frontend_backend(agent_name)

    runtime._sync_frontend_registry_from_builder_payload(runtime._build_agent_builder_listing_payload())
    return started_ports


async def stop_managed_frontend_backends() -> None:
    runtime = _runtime()
    for agent_name in list((runtime.STATE.managed_frontend_servers or {}).keys()):
        await runtime._stop_managed_frontend_backend(agent_name)
    runtime._sync_frontend_registry_from_builder_payload(runtime._build_agent_builder_listing_payload())


async def start_autoyou_page_service_background():
    """Start For AutoYou Page Service in background."""
    runtime = _runtime()
    runtime._invalidate_admin_status_cache("autoyou_page_status")
    if not runtime.AUTOYOU_PAGE_SERVICE_AVAILABLE:
        runtime.LOGGER.error("For AutoYou Page Service not available")
        return None

    try:
        autoyou_config = runtime.STATE.config.get("autoyou_page", {}) if runtime.STATE.config else {}
        port = runtime._get_autoyou_page_service_port()
        timeline_days = max(0, int(autoyou_config.get("timeline_days", 7)))
        if await runtime.is_autoyou_page_service_running():
            return runtime.get_autoyou_page_service()
        if runtime.is_port_in_use(port, host="127.0.0.1"):
            runtime.LOGGER.warning("AutoYou Page port %s is already in use; skipping page service start", port)
            return None
        # Additive opt-in HTTPS mirror for the page service (path_proxy agent-site
        # browsing over TLS). Plain HTTP on `port` is unaffected.
        https_kwargs: runtime.Dict[str, runtime.Any] = {}
        if runtime._https_enabled(runtime.STATE.config):
            try:
                from shared import local_tls
                _tls_material = local_tls.ensure_enabled(runtime._CONFIG_DIR)
                page_https_port = int(autoyou_config.get("page_https_port") or autoyou_config.get("https_port_override") or (int(port) + 300))
                if runtime.is_port_in_use(page_https_port, host="127.0.0.1"):
                    runtime.LOGGER.warning(
                        "Page HTTPS port %s already in use; serving page service over HTTP only",
                        page_https_port,
                    )
                else:
                    https_kwargs = {
                        "https_port": page_https_port,
                        "ssl_certfile": str(_tls_material.server_cert_path),
                        "ssl_keyfile": str(_tls_material.server_key_path),
                    }
            except Exception as exc:
                runtime.LOGGER.error("Could not prepare TLS for the page service HTTPS mirror: %s", exc)
        # With home network access the websites port opens beyond loopback
        # only in direct_forward mode; otherwise website apps are reached
        # through the admin port behind its sign-in.
        service = await runtime.start_autoyou_page_service(
            port=port, host=runtime._page_service_bind_host(), timeline_days=timeline_days, **https_kwargs
        )
        runtime.LOGGER.info(f"For AutoYou Page Service started successfully on port {port}")
        return service
    except Exception as e:
        runtime.LOGGER.error(f"Failed to start For AutoYou Page Service: {e}")
        raise


async def sync_server_advertisement() -> bool:
    """Announce this computer to nearby AutoYou apps, or stop, to match its settings.

    Bonjour is emitted only while the process listens beyond loopback and
    discovery is on. Tailscale and other routed VPN addresses remain manually
    connectable. Safe to call after any server setting change: an unchanged
    announcement is left alone, a renamed server is announced again.
    """
    runtime = _runtime()
    desired = (
        runtime._discovery_advertising_enabled(runtime.STATE.config)
        and not runtime._bind_host_is_loopback(runtime.SERVER_BIND_HOST)
    )
    installation_id = str(((runtime.STATE.config or {}).get("server") or {}).get("installation_id") or "").strip()
    identity = (runtime.SERVER_BIND_HOST, int(runtime.ADMIN_WEB_SERVICE_PORT), runtime.get_configured_server_name(), installation_id)
    current = getattr(runtime.STATE, "server_advertisement", None)
    if current is not None and (not desired or getattr(runtime.STATE, "server_advertisement_identity", None) != identity):
        await stop_server_advertisement()
        current = None
    if not desired or current is not None:
        return current is not None
    advertisement = None
    try:
        from shared.local_server_discovery import ServerAdvertisement

        advertisement = ServerAdvertisement()
        started = await advertisement.start(bind_host=identity[0], port=identity[1], name=identity[2], installation_id=identity[3])
    except Exception as exc:
        runtime.LOGGER.info("Local AutoYou discovery is unavailable: %s", type(exc).__name__)
        started = False
    if not started:
        if advertisement is not None:
            try:
                await advertisement.close()
            except Exception:
                pass
        return False
    runtime.STATE.server_advertisement = advertisement
    runtime.STATE.server_advertisement_identity = identity
    return True


async def stop_server_advertisement() -> None:
    runtime = _runtime()
    advertisement = getattr(runtime.STATE, "server_advertisement", None)
    runtime.STATE.server_advertisement = None
    runtime.STATE.server_advertisement_identity = None
    if advertisement is None:
        return
    try:
        await advertisement.close()
    except Exception as exc:
        runtime.LOGGER.debug("Local AutoYou discovery shutdown failed: %s", type(exc).__name__)


async def stop_autoyou_page_service_background():
    """Stop For AutoYou Page Service."""
    runtime = _runtime()
    runtime._invalidate_admin_status_cache("autoyou_page_status")
    if not runtime.AUTOYOU_PAGE_SERVICE_AVAILABLE:
        runtime.LOGGER.warning("For AutoYou Page Service not available")
        return

    try:
        await runtime.stop_autoyou_page_service()
        runtime.LOGGER.info("For AutoYou Page Service stopped successfully")
    except Exception as e:
        runtime.LOGGER.error(f"Error stopping For AutoYou Page Service: {e}")


async def start_or_restart_autoyou_page_service():
    """Start or restart the AutoYou Page service based on configuration.

    Mirrors the WhatsApp lifecycle pattern: uses `auto_start` as the enable flag,
    stops any existing instance, waits briefly to release the port on Windows,
    checks port availability, and starts the service when appropriate.
    """
    runtime = _runtime()
    if not runtime.AUTOYOU_PAGE_SERVICE_AVAILABLE:
        runtime.LOGGER.warning("For AutoYou Page Service not available")
        return

    autoyou_config = runtime.STATE.config.get("autoyou_page", {}) if runtime.STATE.config else {}
    auto_start = autoyou_config.get("auto_start", True)

    if not auto_start:
        runtime.LOGGER.info("AutoYou Page service auto_start disabled in configuration")
        await runtime.stop_autoyou_page_service_background()
        return

    # Stop existing service first to apply any port change
    await runtime.stop_autoyou_page_service_background()
    await runtime.asyncio.sleep(2)  # Small delay to mitigate Windows TIME_WAIT reuse

    try:
        port = runtime._get_autoyou_page_service_port()
        if runtime.is_port_in_use(port, host="127.0.0.1"):
            runtime.LOGGER.warning(f"AutoYou Page port {port} is already in use; skipping start to avoid bind conflict")
            return
        await runtime.start_autoyou_page_service_background()
        runtime.LOGGER.info("AutoYou Page service started successfully")
    except Exception as e:
        runtime.LOGGER.warning(f"Failed to start AutoYou Page service: {e}")


async def restart_autoyou_page_service():
    """Restart For AutoYou Page Service."""
    runtime = _runtime()
    if not runtime.AUTOYOU_PAGE_SERVICE_AVAILABLE:
        runtime.LOGGER.error("For AutoYou Page Service not available")
        return

    try:
        await runtime.stop_autoyou_page_service_background()
        await runtime.asyncio.sleep(2)  # Brief pause (increase to reduce Windows port reuse issues)
        # Avoid bind error if the configured port is busy
        port = runtime._get_autoyou_page_service_port()
        if runtime.is_port_in_use(port, host="127.0.0.1"):
            runtime.LOGGER.warning(f"AutoYou Page restart skipped start: port {port} still in use")
            return
        await runtime.start_autoyou_page_service_background()
        runtime.LOGGER.info("For AutoYou Page Service restarted successfully")
    except Exception as e:
        runtime.LOGGER.error(f"Error restarting For AutoYou Page Service: {e}")
        raise


async def _autoyou_page_service_status():
    """Get For AutoYou Page Service status."""
    runtime = _runtime()
    if not runtime.AUTOYOU_PAGE_SERVICE_AVAILABLE:
        return "Not Available"

    try:
        if await runtime.is_autoyou_page_service_running():
            # Try to make a simple health check
            try:
                import aiohttp
                port = runtime._get_autoyou_page_service_port()
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"http://127.0.0.1:{port}/health", timeout=aiohttp.ClientTimeout(total=2)) as response:
                        if response.status == 200:
                            return "Running"
                        else:
                            return f"Running (HTTP {response.status})"
            except Exception:
                # Service is running but not responding
                return "Starting"
        else:
            return "Stopped"
    except Exception as e:
        runtime.LOGGER.error(f"Error checking For AutoYou Page Service status: {e}")
        return "Unknown"


def _request_admin_server_exit() -> None:
    """Ask the main admin Uvicorn server (and the opt-in HTTPS mirror) to exit cleanly."""
    runtime = _runtime()
    https_server = getattr(runtime.STATE, "https_admin_server", None)
    if https_server is not None:
        try:
            https_server.should_exit = True
        except Exception as e:
            runtime.LOGGER.warning(f"Failed to request HTTPS admin server exit: {e}")
    admin_server = getattr(runtime.STATE, "admin_server", None)
    if admin_server is None:
        return
    try:
        admin_server.should_exit = True
        runtime.LOGGER.info("Admin server graceful exit requested")
    except Exception as e:
        runtime.LOGGER.warning(f"Failed to request admin server exit: {e}")


async def _run_shutdown_step(label: str, awaitable: Awaitable[Any], timeout: float) -> bool:
    runtime = _runtime()
    try:
        result = await runtime.asyncio.wait_for(awaitable, timeout=timeout)
        if result is False:
            runtime.LOGGER.warning("%s shutdown reported incomplete cleanup", label)
            return False
        return True
    except runtime.asyncio.TimeoutError:
        runtime.LOGGER.warning("%s shutdown timed out after %.1fs", label, timeout)
        return False
    except Exception as e:
        runtime.LOGGER.warning("%s shutdown failed: %s", label, e)
        return False


async def _stop_runtime_services_for_shutdown() -> None:
    runtime = _runtime()
    runtime.LOGGER.info("Initiating graceful shutdown...")
    all_clean = True
    all_clean &= await runtime._run_shutdown_step("Auth Server", runtime.stop_auth_server(), 10.0)
    all_clean &= await runtime._run_shutdown_step("AI Agent Server", runtime.stop_ai_agent_server(), 15.0)
    all_clean &= await runtime._run_shutdown_step("Managed frontend backends", runtime.stop_managed_frontend_backends(), 10.0)
    all_clean &= await runtime._run_shutdown_step("For AutoYou Page Service", runtime.stop_autoyou_page_service_background(), 15.0)
    all_clean &= await runtime._run_shutdown_step(
        "Public reverse proxy service",
        runtime.stop_tunnelmole_service(reason="Server initiated shutdown"),
        15.0,
    )
    all_clean &= await runtime._run_shutdown_step("Bluetooth Pair signaling", runtime.stop_bluetooth_pairing_service("Server initiated shutdown"), 6.0)
    all_clean &= await runtime._run_shutdown_step("Signal service", runtime.stop_signal(), 15.0)
    all_clean &= await runtime._run_shutdown_step("WhatsApp service", runtime.stop_whatsapp(), 15.0)
    all_clean &= await runtime._run_shutdown_step("Telegram User service", runtime.stop_telegram_user(), 10.0)
    all_clean &= await runtime._run_shutdown_step("Telegram service", runtime.stop_telegram(), 10.0)
    all_clean &= await runtime._run_shutdown_step("WebRTC manager", runtime.WEBRTC.shutdown(), 15.0)
    all_clean &= await runtime._run_shutdown_step(
        "Session execution manager",
        runtime.get_session_execution_manager().shutdown(),
        10.0,
    )
    all_clean &= await runtime._run_shutdown_step("AutoYou Cloud SSE listener", runtime._stop_cloud_sse_listener(), 6.0)
    all_clean &= await runtime._run_shutdown_step("Scheduler Service", runtime._stop_scheduler_service(), 6.0)
    all_clean &= await runtime._run_shutdown_step("Tracked background tasks", runtime._cancel_tracked_background_tasks(), 6.0)

    if all_clean:
        runtime.LOGGER.info("All services stopped gracefully")
    else:
        runtime.LOGGER.warning("Shutdown completed with partial cleanup; check preceding warnings for lingering services or tasks")


async def graceful_shutdown():
    """Handle graceful shutdown of all services (idempotent - runs at most once)."""
    runtime = _runtime()
    if runtime._GRACEFUL_SHUTDOWN_STARTED:
        runtime.LOGGER.info("Graceful shutdown already in progress; skipping duplicate invocation")
        return
    runtime._GRACEFUL_SHUTDOWN_STARTED = True
    runtime._arm_shutdown_watchdog()
    try:
        await runtime._stop_runtime_services_for_shutdown()
    except Exception as e:
        runtime.LOGGER.error(f"Error during graceful shutdown: {e}")
    finally:
        runtime._request_admin_server_exit()


def _install_aiortc_exception_filter() -> None:
    """Suppress known benign aiortc/aioice teardown errors from the asyncio log.

    Two classes of noisy-but-harmless errors are silenced at DEBUG level:

    1. aiortc SCTP flush: ``RTCSctpTransport._data_channel_flush`` raises
       ``ConnectionError('Cannot send encrypted data, not connected')`` after
       the DTLS transport has already torn down on peer disconnect.

    2. aioice TURN CHANNEL_BIND: ``TurnClientMixin.send_data()`` raises
       ``TransactionFailed`` when the TURN channel binding fails because the
       remote allocation expired or the peer disconnected mid-teardown.

    Both are expected during normal WebRTC connection teardown and are not
    actionable.  Without this handler asyncio logs them as
    "Task exception was never retrieved", which is noisy and misleading.
    """
    runtime = _runtime()
    try:
        loop = runtime.asyncio.get_running_loop()
    except RuntimeError:
        return

    _default_handler = loop.get_exception_handler()

    def _handler(loop_: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        if isinstance(exc, ConnectionError):
            msg = str(exc).lower()
            if "not connected" in msg or "encrypted data" in msg:
                runtime.LOGGER.debug("Suppressed expected aiortc teardown error: %s", exc)
                return
        # aioice TURN teardown: TransactionFailed is not a standard Python
        # exception so match by type name to avoid importing aioice internals.
        if exc is not None and type(exc).__name__ == "TransactionFailed":
            runtime.LOGGER.debug("Suppressed expected aioice TURN teardown error: %s", exc)
            return
        if _default_handler is not None:
            _default_handler(loop_, context)
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(_handler)


async def main():
    """Main async function to handle server startup."""
    runtime = _runtime()

    for stream in (runtime.sys.stdout, runtime.sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass

    # Keep every runtime child owned by this server, and make the server itself
    # exit if bootstrap/tray/native host disappears without running shutdown.
    runtime.start_parent_process_watchdog()
    runtime.os.environ[runtime.AUTOYOU_PARENT_PID_ENV] = str(runtime.os.getpid())

    # Parse command line arguments
    parser = runtime.argparse.ArgumentParser(description="AutoYou Admin Web Server")
    parser.add_argument("--admin", type=int, default=runtime.ADMIN_WEB_SERVICE_PORT,
                        help="Port for the Admin Web Server (default: 8001)")
    parser.add_argument("--ai-agent", type=int, default=runtime.AI_AGENT_SERVER_PORT,
                        help="Port for the AI Agent Server (default: 8081)")
    parser.add_argument("--auth", type=int, default=runtime.AUTH_SERVER_PORT,
                        help="Port for the Auth Server (/auth,/signal) (default: 8002)")
    parser.add_argument(
        "--host",
        type=str,
        default=runtime.os.getenv("AUTOYOU_BIND_HOST", "127.0.0.1"),
        help=(
            "Host to bind servers to ('localhost' or '127.0.0.1'). "
            "Can be set via AUTOYOU_BIND_HOST env; default: 127.0.0.1"
        ),
    )
    parser.add_argument("--tunnelmole", action="store_true", help="Enable the public reverse proxy")
    bind_host_explicit = bool(runtime.os.getenv("AUTOYOU_BIND_HOST")) or any(
        arg == "--host" or str(arg).startswith("--host=")
        for arg in runtime.sys.argv[1:]
    )
    args = parser.parse_args()

    # Update global port variables
    runtime.ADMIN_WEB_SERVICE_PORT = args.admin
    runtime.AI_AGENT_SERVER_PORT = args.ai_agent
    runtime.AUTH_SERVER_PORT = args.auth
    runtime.SERVER_INSTANCE_NAME = runtime._get_instance_name_env()
    # Update environment variables so other modules can access them
    runtime.os.environ["AUTOYOU_ADMIN_PORT"] = str(args.admin)
    runtime.os.environ["AUTOYOU_AI_PORT"] = str(args.ai_agent)
    runtime.os.environ["AUTOYOU_AUTH_PORT"] = str(args.auth)
    runtime.os.environ["ADMIN_WEB_SERVICE_PORT"] = str(args.admin)
    runtime.os.environ["AI_AGENT_SERVER_PORT"] = str(args.ai_agent)
    runtime.os.environ["AUTH_SERVER_PORT"] = str(args.auth)
    # Mirror the startup host into environment/global before bootstrap. If the
    # operator did not explicitly choose a host, the saved next-boot config is
    # applied after config unlock below.
    runtime._set_runtime_bind_host(args.host)
    runtime.LOGGER.info("Runtime dependency versions: %s", runtime.get_runtime_dependency_versions())

    # Install event-loop exception handler to suppress noisy-but-harmless
    # aiortc/aioice teardown errors (ConnectionError, TransactionFailed).
    runtime._install_aiortc_exception_filter()

    # Bootstrap config/password
    await runtime.bootstrap_password_and_config()
    if runtime.STATE.config:
        try:
            runtime._configure_secure_storage_for_config(
                runtime.STATE.config,
                operation_timeout_seconds=runtime._macos_keychain_bootstrap_timeout_seconds(),
                allow_key_creation=False,
            )
        except runtime.SecureStorageError as exc:
            runtime.LOGGER.error("Secure Professional Maximus storage could not start: %s", exc)
            # A missing/denied credential must never be treated as a new key or
            # a failed process that a desktop host should restart forever. Keep
            # the encrypted config untouched and serve only the locked recovery
            # UI so the operator can grant access to the original credential.
            try:
                if runtime.secure_storage_enabled():
                    runtime.disable_secure_storage()
            except Exception as cleanup_exc:  # pragma: no cover - defensive cleanup.
                runtime.LOGGER.warning(
                    "Could not fully reset Secure Professional Maximus after startup failure: %s",
                    cleanup_exc,
                )
            runtime._set_config_session(config_store=runtime.CONFIG_STORE_NONE)
            runtime.STATE.config = {}
            runtime.STATE.used_default_password = False
            runtime.STATE.decrypted_via_env = False
            runtime.STATE.initialized_services_on_startup = False
            runtime.STATE._unlock_state_mem = "Locked"
            runtime._update_startup_status(
                status="error",
                headline="Secure storage needs credential access",
                detail=(
                    "Saved configuration remains protected. Allow AutoYou access to its existing "
                    "system credential, then unlock again."
                ),
                step=0,
                total_steps=8,
                error="secure_storage_credential_unavailable",
            )

    if not bind_host_explicit:
        args.host = runtime._configured_server_bind_host(runtime.STATE.config)
        runtime._set_runtime_bind_host(args.host)

    apply_native_saved_ai_port()
    args.ai_agent = runtime.AI_AGENT_SERVER_PORT

    display_host = runtime._normalize_probe_host(args.host)
    bind_note = f" (bound to {args.host})" if display_host != args.host else ""
    print("Starting AutoYou Admin Web Server...")
    print(f"Instance name: {runtime.SERVER_INSTANCE_NAME}")
    print(f"Agent directory: {runtime.AGENT_DIR}")
    print(f"Admin Web UI at: http://{display_host}:{args.admin}/{bind_note}")
    print(f"AI Agent Server will be available at: http://{display_host}:{args.ai_agent}/{bind_note}")
    print(f"Auth Server is on-demand via /pair at: http://{display_host}:{args.auth}/{bind_note}")
    runtime.LOGGER.info(
        "Starting AutoYou instance '%s' on %s with ports admin=%s ai=%s auth=%s",
        runtime.SERVER_INSTANCE_NAME,
        args.host,
        args.admin,
        args.ai_agent,
        args.auth,
    )

    probe_host = runtime._normalize_probe_host(args.host)
    if runtime.is_port_in_use(args.admin, host=probe_host):
        runtime.LOGGER.error(
            "Admin UI port %s is already in use on %s. Refusing to start a second AutoYou instance.",
            args.admin,
            probe_host,
        )
        raise SystemExit(1)

    # Apply config model/provider settings to env before spawning the AI agent
    # process. Without this, the subprocess inherits the default OLLAMA_MODEL
    # (ministral-3:8b) instead of the user-configured model, because the lifespan
    # handler (which normally calls _apply_google_api_config_to_env) runs after
    # the subprocess is already started.
    runtime._apply_google_api_config_to_env()

    # The native runtime defers optional services until the local listener is up.
    if _startup_services_are_skipped():
        runtime.LOGGER.info("Optional startup services deferred by AUTOYOU_SKIP_STARTUP_SERVICES.")
        if _start_ai_when_startup_services_are_skipped() and runtime.should_start_ai_agent_server():
            runtime.schedule_ai_agent_server_autostart()
        if runtime.os.getenv("AUTOYOU_NATIVE_OWNED_SERVER") == "1":
            # Websites and the command directory are part of the native app,
            # including after a server restart with messaging services deferred.
            await _start_autoyou_page_service_if_enabled()
    else:
        if runtime.should_start_ai_agent_server():
            runtime.schedule_ai_agent_server_autostart()
        await _start_autoyou_page_service_if_enabled()

    # Setup signal handlers for graceful shutdown.
    #
    # Key design: the signal handler ONLY tells uvicorn to exit cleanly
    # (server.should_exit = True via _request_admin_server_exit).  It does NOT
    # call graceful_shutdown() itself.  Instead, graceful_shutdown() is always
    # awaited explicitly AFTER server.serve() returns.  This guarantees the AI
    # Agent process, Auth server, and all other children are terminated before
    # asyncio.run() finishes - preventing the "multiple zombie processes" issue.
    shutdown_initiated = False

    def signal_handler(signum, frame):
        nonlocal shutdown_initiated
        if shutdown_initiated:
            runtime.LOGGER.info(f"Shutdown already in progress, ignoring signal {signum}")
            return

        shutdown_initiated = True
        runtime.LOGGER.info(f"Received signal {signum}, initiating graceful shutdown...")
        # Tell uvicorn to drain connections and exit serve().
        runtime._request_admin_server_exit()

    runtime.signal.signal(runtime.signal.SIGINT, signal_handler)
    runtime.signal.signal(runtime.signal.SIGTERM, signal_handler)

    # Start the Admin Web Server as the main server
    config = runtime.uvicorn.Config(
        runtime.admin_app,
        host=args.host,
        port=args.admin,
        log_level="info",
        access_log=False,
    )
    server = runtime.uvicorn.Server(config)
    runtime.STATE.admin_server = server

    # Opt-in additive HTTPS listener for the admin app (login / unlock / admin UI
    # over TLS). It runs in PARALLEL with the plain-HTTP listener above, so turning
    # HTTPS on only ADDS an https:// surface and never breaks already-paired HTTP
    # clients. Composes with _require_loopback_or_https_request: a LAN client can
    # now log in over https:// even though plain-HTTP /login stays loopback-only.
    https_task = None
    if runtime._https_enabled(runtime.STATE.config):
        try:
            from shared import local_tls
            tls_material = local_tls.ensure_enabled(runtime._CONFIG_DIR)
            https_port = runtime._https_port(runtime.STATE.config)
            if runtime.is_port_in_use(https_port, host=probe_host):
                runtime.LOGGER.warning(
                    "HTTPS admin port %s already in use on %s; skipping TLS listener",
                    https_port, probe_host,
                )
            else:
                https_config = runtime.uvicorn.Config(
                    runtime.admin_app,
                    host=args.host,
                    port=https_port,
                    log_level="warning",
                    access_log=False,
                    ssl_certfile=str(tls_material.server_cert_path),
                    ssl_keyfile=str(tls_material.server_key_path),
                )
                https_server = runtime.uvicorn.Server(https_config)
                runtime.STATE.https_admin_server = https_server
                https_task = runtime.asyncio.ensure_future(https_server.serve())
                print(
                    f"Admin Web UI (HTTPS) at: https://{display_host}:{https_port}/"
                    f"  - trust the CA first from /ca.crt"
                )
                runtime.LOGGER.info(
                    "Opt-in HTTPS admin listener started on %s:%s (leaf expires %s)",
                    args.host, https_port, tls_material.not_valid_after,
                )
        except Exception as exc:
            runtime.LOGGER.error("Failed to start opt-in HTTPS admin listener: %s", exc, exc_info=True)
            runtime.STATE.https_admin_server = None
            https_task = None

    try:
        await sync_server_advertisement()
        await server.serve()
    except KeyboardInterrupt:
        runtime.LOGGER.info("Keyboard interrupt received, shutting down...")
    except Exception as e:
        runtime.LOGGER.error(f"Server error: {e}")
        raise
    finally:
        await stop_server_advertisement()
        runtime.STATE.admin_server = None
        # Drain the parallel HTTPS listener (if any) so it never outlives the
        # main admin server or leaks a bound port on shutdown.
        https_server_ref = getattr(runtime.STATE, "https_admin_server", None)
        if https_server_ref is not None:
            try:
                https_server_ref.should_exit = True
            except Exception:
                pass
        if https_task is not None:
            try:
                await runtime.asyncio.wait_for(https_task, timeout=5)
            except runtime.asyncio.TimeoutError:
                https_task.cancel()
            except Exception:
                pass
        runtime.STATE.https_admin_server = None

    # Always perform full graceful shutdown after the admin server exits.
    # This ensures the AI Agent process (port 8081), Auth server, and all
    # other child processes are terminated even when the server exits cleanly
    # (SIGTERM / tray-app stop) - not just on KeyboardInterrupt.
    runtime.LOGGER.info("Admin server exited - running graceful shutdown of all services...")
    await runtime.graceful_shutdown()
    await runtime._run_shutdown_step(
        "Remaining asyncio tasks",
        runtime._cancel_remaining_asyncio_tasks(),
        6.0,
    )


def _run_ai_agent_server_cli_if_requested(argv: Optional[List[str]] = None) -> bool:
    runtime = _runtime()
    effective_argv = list(runtime.sys.argv[1:] if argv is None else argv)
    if "--run-ai-agent-server" not in effective_argv:
        return False

    parser = runtime.argparse.ArgumentParser(add_help=True, allow_abbrev=False)
    parser.add_argument("--run-ai-agent-server", action="store_true")
    parser.add_argument("--host", default=runtime._configured_ai_agent_bind_host())
    parser.add_argument("--port", type=int, default=runtime.AI_AGENT_SERVER_PORT)
    parser.add_argument("--agent-dir", default=runtime.AGENT_DIR)
    args = parser.parse_args(effective_argv)

    if not args.run_ai_agent_server:
        return False

    if runtime.os.getenv("AUTOYOU_SECURE_STORAGE_MODE") == runtime.SECURE_PROFESSIONAL_MAXIMUS_MODE:
        runtime.enable_secure_storage(
            app_name=runtime.os.getenv("AUTOYOU_SECURE_STORAGE_APP") or "AutoYou",
            root=runtime.os.getenv("AUTOYOU_SECURE_STORAGE_ROOT") or runtime._CONFIG_DIR,
            password=runtime.os.getenv("AUTOYOU_SECURE_STORAGE_PASSWORD")
            or runtime.os.getenv("AUTOYOU_SERVER_PASSWORD"),
            operation_timeout_seconds=runtime._macos_keychain_bootstrap_timeout_seconds(),
            allow_key_creation=False,
        )
        if str(runtime.os.getenv("AUTOYOU_MEMORY_BACKEND") or "legacy").strip().lower() == "cognee":
            raise runtime.SecureStorageError(
                "Secure Professional Maximus does not allow unsealed Cognee filesystem state"
            )

    runtime.run_agent_server(args.host, int(args.port), args.agent_dir, runtime.os.environ.copy())
    return True
