# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-b2f445e36a4c902f9a11041c

"""Bounded, owned external inference processes and Wan2GP GPU diagnostics."""

from __future__ import annotations

import codecs
import json
import os
import platform
import queue
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from shared.process_lifecycle import OwnedProcessJob, process_spawn_kwargs
from shared.secure_storage import append_secure_file, write_secure_file

LOG_LIMIT_BYTES = 4 * 1024 * 1024
TAIL_LIMIT_CHARS = 8192
RUNTIME_MARKER = "AUTOYOU_MEDIA_RUNTIME="
_PROBE_LOCK = threading.Lock()
_PROBE_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}

# This runs in the selected external environment, not AutoYou's interpreter.
# os._exit follows an explicit GPU synchronization and flushed output. It avoids
# third-party non-daemon threads and atexit handlers keeping a CLI job alive.
WAN_WORKER_BOOTSTRAP = r'''
import os, runpy, sys, traceback
code = 1
try:
    import torch
    if torch.version.hip:
        # The portable baseline does not depend on experimental AOTriton kernels.
        torch.backends.cuda.enable_flash_sdp(False)
        torch.backends.cuda.enable_mem_efficient_sdp(False)
        torch.backends.cuda.enable_math_sdp(True)
        if hasattr(torch.backends.cuda, "enable_cudnn_sdp"):
            torch.backends.cuda.enable_cudnn_sdp(False)
    script = sys.argv[1]
    sys.argv = sys.argv[1:]
    sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
    try:
        runpy.run_path(script, run_name="__main__")
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        torch.mps.synchronize()
except BaseException:
    traceback.print_exc()
    code = 1
finally:
    sys.stdout.flush()
    sys.stderr.flush()
os._exit(code)
'''

GPU_PROBE_BOOTSTRAP = r'''
import importlib.metadata, json, os, platform, sys, traceback
result = {"ready": False, "backend": "unknown"}
try:
    import torch
    result["torch_version"] = torch.__version__
    result["hip_version"] = torch.version.hip
    result["cuda_version"] = torch.version.cuda
    for package in ("rocm-sdk-core", "rocm"):
        try:
            result["rocm_version"] = importlib.metadata.version(package)
            break
        except importlib.metadata.PackageNotFoundError:
            pass
    device = sys.argv[1]
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(device)
        result.update(backend="amd" if torch.version.hip else "nvidia",
                      device=torch.cuda.get_device_name(device),
                      architecture=getattr(props, "gcnArchName", ""),
                      memory_bytes=props.total_memory)
    elif device == "mps" and torch.backends.mps.is_available():
        result.update(backend="apple", device="Apple GPU", architecture="mps")
    else:
        raise RuntimeError("The selected Python environment cannot use a GPU. Install its matching GPU PyTorch packages and driver.")
    # Verify actual GPU work, not just the existence of an executable or driver.
    a = torch.ones((8, 8), device=device)
    value = (a @ a).float().cpu()
    if not bool(torch.isfinite(value).all()) or not bool(torch.all(value == 8)):
        raise RuntimeError("The GPU numerical self-check failed.")
    if result["backend"] in ("amd", "nvidia"):
        torch.cuda.synchronize(device)
    else:
        torch.mps.synchronize()
    result["ready"] = True
except BaseException as exc:
    result["error"] = str(exc)
print("AUTOYOU_MEDIA_RUNTIME=" + json.dumps(result), flush=True)
sys.stderr.flush()
os._exit(0 if result["ready"] else 1)
'''


def _stop_owned_process(process: subprocess.Popen, job: OwnedProcessJob | None) -> bool:
    """Kill only this launch and its children, and never wait indefinitely."""
    if job is not None:
        job.terminate_remaining()
    elif os.name != "nt":
        # The POSIX runner keeps the root as an unreaped zombie until this
        # point. Its PID cannot be recycled while we stop its owned group,
        # including helpers still holding the output pipes after root exit.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        if os.name == "nt":
            try:
                import psutil

                children = psutil.Process(process.pid).children(recursive=True)
                for child in reversed(children):
                    child.kill()
            except Exception:
                pass
        if process.poll() is None:
            process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    if job is not None:
        deadline = time.monotonic() + 2
        while job.active_processes() and time.monotonic() < deadline:
            time.sleep(0.05)
    return job is None or job.active_processes() == 0


def run_owned_media_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    work_dir: Path | None = None,
    on_output: Callable[[str, str], None] | None = None,
    on_started: Callable[[int], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Drain both pipes, retain bounded tails, and persist secure logs live."""
    if os.name != "nt":
        import psutil
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        **process_spawn_kwargs(hide_window=True),
    )
    job = OwnedProcessJob.for_process(process)
    identity = psutil.Process(process.pid) if os.name != "nt" else None
    output: queue.Queue[tuple[str, bytes | None]] = queue.Queue(maxsize=64)
    tails = {"stdout": "", "stderr": ""}
    sizes = {"stdout": 0, "stderr": 0}
    decoders = {name: codecs.getincrementaldecoder("utf-8")("replace") for name in tails}
    finished_streams: set[str] = set()
    readers: list[threading.Thread] = []
    started = time.monotonic()
    exit_seen: float | None = None
    timed_out = False
    cleanup_ok = True

    def has_exited() -> bool:
        if identity is not None:
            # Popen.poll() reaps on POSIX. Delay that until group cleanup so a
            # reused PID can never identify another service's process group.
            try:
                return not identity.is_running() or identity.status() == psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                return True
        return process.poll() is not None

    def drain(name: str, pipe: Any) -> None:
        try:
            while chunk := pipe.read(4096):
                output.put((name, chunk))
        finally:
            output.put((name, None))

    def consume(name: str, chunk: bytes | None) -> None:
        if chunk is None:
            finished_streams.add(name)
        text = decoders[name].decode(chunk or b"", final=chunk is None)
        tails[name] = (tails[name] + text)[-TAIL_LIMIT_CHARS:]
        if chunk and work_dir and sizes[name] < LOG_LIMIT_BYTES:
            retained = chunk[:LOG_LIMIT_BYTES - sizes[name]]
            append_secure_file(work_dir / f"{name}.txt", retained)
            sizes[name] += len(retained)
            if sizes[name] == LOG_LIMIT_BYTES:
                append_secure_file(work_dir / f"{name}.txt", b"\n[Log limit reached; remaining output is drained.]\n")
        if text and on_output:
            on_output(name, text)

    try:
        if work_dir:
            write_secure_file(work_dir / "stdout.txt", b"")
            write_secure_file(work_dir / "stderr.txt", b"")
        if on_started:
            on_started(process.pid)
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            thread = threading.Thread(target=drain, args=(name, pipe), daemon=True, name=f"media-{name}")
            thread.start()
            readers.append(thread)
        while True:
            now = time.monotonic()
            if now - started >= timeout and not has_exited():
                timed_out = True
                break
            if has_exited():
                exit_seen = exit_seen or now
                if len(finished_streams) == 2 or now - exit_seen >= 2:
                    break
            try:
                consume(*output.get(timeout=0.1))
            except queue.Empty:
                pass
    finally:
        try:
            cleanup_ok = _stop_owned_process(process, job)
        finally:
            if job is not None:
                job.close()
        for reader in readers:
            reader.join(timeout=0.25)
        while not output.empty():
            consume(*output.get_nowait())
        for pipe, reader in zip((process.stdout, process.stderr), readers):
            if pipe and not reader.is_alive():
                pipe.close()

    returncode = process.returncode
    if timed_out:
        returncode = 124
        tails["stderr"] += f"\nGeneration timed out after {timeout:g} seconds; its owned worker was stopped."
    if not cleanup_ok:
        returncode = 125
        tails["stderr"] += "\nWorker cleanup did not complete. Check the GPU driver before starting another job."
    return subprocess.CompletedProcess(command, returncode if returncode is not None else 125, **tails)


def _known_old_windows_rocm(result: dict[str, Any]) -> bool:
    if platform.system() != "Windows" or result.get("backend") != "amd":
        return False
    version = str(result.get("rocm_version") or "")
    match = re.match(r"(\d+)\.(\d+)", version)
    if match:
        major, minor = map(int, match.groups())
        if major < 7 or (major == 7 and minor < 13):
            return True
        date = re.search(r"(20\d{6})", version)
        return major == 7 and minor == 13 and bool(date and date.group(1) < "20260312")
    return "rocm7.0.0rc2025" in str(result.get("torch_version") or "")


def clear_wan_runtime_cache() -> None:
    with _PROBE_LOCK:
        _PROBE_CACHE.clear()


def probe_wan_runtime(config: dict[str, Any], env: dict[str, str], *, refresh: bool = False) -> dict[str, Any]:
    app_dir = Path(str(config.get("app_dir") or ""))
    python = Path(str(config.get("python") or ""))
    device = str(config.get("gpu") or "cuda:0")
    missing = None
    if not (app_dir / "wgp.py").is_file():
        missing = "Wan2GP's wgp.py was not found. Detect or install Wan2GP in Configuration."
    elif not python.is_file():
        missing = "Wan2GP's Python executable was not found. Complete its environment setup."
    if missing:
        return {"ready": False, "backend": "unknown", "error": missing}
    key = (str(python.resolve()), python.stat().st_mtime_ns, str(app_dir.resolve()), device)
    with _PROBE_LOCK:
        cached = _PROBE_CACHE.get(key)
        if not refresh and cached and time.monotonic() - cached[0] < 120:
            return dict(cached[1])
        completed = run_owned_media_process(
            [str(python), "-u", "-c", GPU_PROBE_BOOTSTRAP, device],
            cwd=app_dir, env=env, timeout=30,
        )
        result: dict[str, Any] = {"ready": False, "backend": "unknown"}
        for line in completed.stdout.splitlines():
            if line.startswith(RUNTIME_MARKER):
                try:
                    decoded = json.loads(line[len(RUNTIME_MARKER):])
                    if isinstance(decoded, dict):
                        result = decoded
                except (ValueError, TypeError):
                    pass
        if completed.returncode != 0:
            result["ready"] = False
            result.setdefault("error", "GPU diagnostics failed or timed out. " + completed.stderr[-1000:])
        elif result.get("ready") is not True or result.get("backend") not in {"amd", "nvidia", "apple"}:
            result["ready"] = False
            result.setdefault("error", "The GPU diagnostics did not confirm a usable GPU.")
        elif _known_old_windows_rocm(result):
            result["ready"] = False
            result["error"] = (
                "This Windows ROCm environment predates the process-shutdown fix. "
                "Use a current Wan2GP AMD environment with matching device packages "
                "(ROCm Core 7.13 or newer), then detect it in Configuration."
            )
        if len(_PROBE_CACHE) >= 8:
            _PROBE_CACHE.clear()
        _PROBE_CACHE[key] = (time.monotonic(), dict(result))
        return result


def wan_supports_option(config: dict[str, Any], option: str) -> bool:
    """Check the selected installation before passing newer CLI options."""
    app_dir = Path(str(config.get("app_dir") or ""))
    for source in (app_dir / "shared" / "cli_args.py", app_dir / "wgp.py"):
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if re.search(r"[\"']" + re.escape(option) + r"[\"']", text):
            return True
    return False


def wan_generation_command(python: Path, settings: Path, output: Path, runtime: dict[str, Any], config: dict[str, Any]) -> list[str]:
    command = [
        str(python), "-u", "-c", WAN_WORKER_BOOTSTRAP, "wgp.py",
        "--process", str(settings), "--output-dir", str(output),
        "--gpu", str(config.get("gpu") or "cuda:0"),
    ]
    if runtime.get("backend") == "amd":
        command += ["--attention", "sdpa"]
        # MMGP's native CPU allocator can crash in alloc_cpu on Windows ROCm.
        # Use the supported PyTorch allocator without editing the external app.
        if platform.system() == "Windows" and wan_supports_option(config, "--ram-allocator"):
            command += ["--ram-allocator", "default"]
    profile = str(config.get("memory_profile") or "auto")
    if config.get("_low_memory_retry"):
        profile = "5"
    if profile != "auto":
        if profile not in {"1", "2", "3", "3.5", "4", "4.5", "5"}:
            raise ValueError("Wan2GP memory profile must be auto, 1, 2, 3, 3.5, 4, 4.5, or 5.")
        if wan_supports_option(config, "--profile"):
            command += ["--profile", profile]
        elif not config.get("_low_memory_retry"):
            raise ValueError("This Wan2GP installation does not support selecting a memory profile.")
    if config.get("_low_memory_retry"):
        # Lowering pixel count cannot fix weight loading or pinned host memory.
        # Profile 5 streams weights instead of keeping the transformer pinned.
        for option, value in (
            ("--preload", "0"),
            ("--perc-reserved-mem-max", "0.1"), ("--ram-allocator", "default"),
        ):
            if option not in command and wan_supports_option(config, option):
                command += [option, value]
    return command
