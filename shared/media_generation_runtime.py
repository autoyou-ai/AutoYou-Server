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
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from shared.platform_runtime import get_application_root
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
result = {"ready": False, "backend": "unknown", "machine": platform.machine().lower()}
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
        cc = [int(getattr(props, "major", 0) or 0), int(getattr(props, "minor", 0) or 0)]
        result.update(backend="amd" if torch.version.hip else "nvidia",
                      device=torch.cuda.get_device_name(device),
                      architecture=getattr(props, "gcnArchName", "") or f"sm_{cc[0]}{cc[1]}",
                      compute_capability=cc,
                      device_count=int(torch.cuda.device_count()),
                      memory_bytes=int(props.total_memory))
    elif device == "mps" and torch.backends.mps.is_available():
        result.update(backend="apple", device="Apple GPU", architecture="mps", unified_memory=True)
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


def _group_has_live_members(group_id: int) -> bool:
    import psutil

    for pid in psutil.pids():
        try:
            if os.getpgid(pid) == group_id and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                return True
        except (ProcessLookupError, psutil.NoSuchProcess):
            continue
    return False


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
        except PermissionError:
            # macOS returns EPERM for a group whose members have all exited.
            if sys.platform != "darwin" or _group_has_live_members(process.pid):
                raise
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


_UNIFIED_AMD_ARCHS = frozenset({"gfx1103", "gfx1150", "gfx1151", "gfx1152", "gfx1153"})
_VALID_WAN_MEMORY_PROFILES = frozenset({"1", "2", "3", "3.5", "4", "4.5", "5"})


def system_memory_bytes() -> tuple[int, int]:
    """Return ``(total_bytes, available_bytes)`` for host system RAM."""
    try:
        import psutil  # type: ignore

        mem = psutil.virtual_memory()
        return int(mem.total), int(mem.available)
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MemoryStatusEx()
            stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys), int(stat.ullAvailPhys)
        except Exception:
            pass
    else:
        try:
            meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
            total_kb = 0
            avail_kb = 0
            for line in meminfo.splitlines():
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail_kb = int(line.split()[1])
            if total_kb:
                return total_kb * 1024, (avail_kb or total_kb) * 1024
        except Exception:
            pass
    return 0, 0


def is_dgx_spark_runtime(runtime: dict[str, Any]) -> bool:
    """Detect NVIDIA DGX Spark / Grace Blackwell GB10 unified-memory hosts."""
    backend = str(runtime.get("backend") or "").lower()
    if backend != "nvidia":
        return False
    device = str(runtime.get("device") or "").lower()
    machine = str(runtime.get("machine") or platform.machine()).lower()
    if "gb10" in device or "dgx spark" in device or "grace blackwell" in device:
        return True
    cc = runtime.get("compute_capability")
    if machine in {"aarch64", "arm64"} and isinstance(cc, (list, tuple)) and len(cc) >= 2:
        try:
            if int(cc[0]) >= 10:
                return True
        except Exception:
            pass
    return False


def is_unified_memory_gpu(runtime: dict[str, Any]) -> bool:
    """Return True when CPU RAM and GPU VRAM share a unified memory pool."""
    if runtime.get("unified_memory") is True:
        return True
    backend = str(runtime.get("backend") or "").lower()
    if backend == "apple":
        return True
    if is_dgx_spark_runtime(runtime):
        return True
    arch = str(runtime.get("architecture") or "").lower().split(":", 1)[0].strip()
    if arch in _UNIFIED_AMD_ARCHS:
        return True
    device = str(runtime.get("device") or "").lower()
    if backend == "amd" and any(marker in device for marker in ("8060s", "8050s", "8040s", "890m", "880m", "780m", "strix")):
        return True
    return False


def recommend_wan_memory_profile(
    runtime: dict[str, Any],
    *,
    system_ram_bytes: int | None = None,
) -> dict[str, Any]:
    """Choose a CPU/RAM-efficient Wan2GP profile that maximizes available GPU VRAM.

    On high-VRAM / lower-RAM hosts (e.g. 96+ GB GPU with 32 GB system RAM) and
    unified-memory systems (AMD Strix Halo APUs, NVIDIA DGX Spark GB10),
    Profile ``3.5`` (VeryLowRAM_HighVRAM) loads whole models directly onto GPU
    memory with ``pinnedMemory=False`` (zero reserved host RAM), avoiding host
    RAM pressure and PCIe/bus weight streaming overhead.
    """
    raw_vram = runtime.get("memory_bytes")
    try:
        vram_bytes = int(raw_vram or 0)
    except (TypeError, ValueError):
        vram_bytes = 0
    if system_ram_bytes is None:
        total_ram, avail_ram = system_memory_bytes()
    else:
        total_ram, avail_ram = int(system_ram_bytes), int(system_ram_bytes)

    vram_gb = round(vram_bytes / (1024 ** 3), 2) if vram_bytes > 0 else 0.0
    ram_gb = round(total_ram / (1024 ** 3), 2) if total_ram > 0 else 0.0
    avail_ram_gb = round(avail_ram / (1024 ** 3), 2) if avail_ram > 0 else 0.0
    unified = is_unified_memory_gpu(runtime)
    dgx_spark = is_dgx_spark_runtime(runtime)
    cc = runtime.get("compute_capability")
    blackwell = (
        str(runtime.get("backend") or "").lower() == "nvidia"
        and isinstance(cc, (list, tuple))
        and len(cc) >= 1
        and int(cc[0] or 0) >= 10
    )

    if vram_bytes <= 0:
        return {
            "profile": None,
            "label": "Wan2GP default",
            "reason": "GPU memory capacity was not reported; deferring to Wan2GP defaults.",
            "vram_gb": vram_gb,
            "ram_gb": ram_gb,
            "available_ram_gb": avail_ram_gb,
            "unified_memory": unified,
            "dgx_spark": dgx_spark,
            "vram_safety_coefficient": None,
            "prefer_bf16": blackwell,
            "prefer_nvfp4": blackwell or dgx_spark,
        }

    # Maximize GPU residency without reserving scarce CPU RAM.
    if vram_gb >= 32.0:
        if unified or ram_gb <= 48.0 or vram_gb >= ram_gb:
            return {
                "profile": "3.5",
                "label": "Profile 3.5 — VeryLowRAM_HighVRAM (GPU-resident, zero reserved CPU RAM)",
                "reason": (
                    f"{vram_gb:.1f} GB GPU memory with {ram_gb:.1f} GB host RAM"
                    + (" (unified memory)" if unified else "")
                    + ": keeping whole models on GPU without pinning host RAM maximizes GPU speed and minimizes CPU/RAM load."
                ),
                "vram_gb": vram_gb,
                "ram_gb": ram_gb,
                "available_ram_gb": avail_ram_gb,
                "unified_memory": unified,
                "dgx_spark": dgx_spark,
                "vram_safety_coefficient": 0.85 if vram_gb >= 48.0 else 0.80,
                "prefer_bf16": blackwell,
                "prefer_nvfp4": blackwell or dgx_spark,
            }
        return {
            "profile": "3",
            "label": "Profile 3 — LowRAM_HighVRAM (GPU-resident)",
            "reason": f"{vram_gb:.1f} GB GPU VRAM with {ram_gb:.1f} GB host RAM supports full GPU-resident models.",
            "vram_gb": vram_gb,
            "ram_gb": ram_gb,
            "available_ram_gb": avail_ram_gb,
            "unified_memory": unified,
            "dgx_spark": dgx_spark,
            "vram_safety_coefficient": 0.85 if vram_gb >= 48.0 else 0.80,
            "prefer_bf16": blackwell,
            "prefer_nvfp4": blackwell or dgx_spark,
        }
    if vram_gb >= 20.0:
        profile = "3.5" if (unified or ram_gb <= 40.0) else "3"
        return {
            "profile": profile,
            "label": (
                "Profile 3.5 — VeryLowRAM_HighVRAM"
                if profile == "3.5" else
                "Profile 3 — LowRAM_HighVRAM"
            ),
            "reason": f"{vram_gb:.1f} GB VRAM fits most video/image transformers directly on GPU.",
            "vram_gb": vram_gb,
            "ram_gb": ram_gb,
            "available_ram_gb": avail_ram_gb,
            "unified_memory": unified,
            "dgx_spark": dgx_spark,
            "vram_safety_coefficient": 0.80,
            "prefer_bf16": blackwell,
            "prefer_nvfp4": blackwell or dgx_spark,
        }
    if vram_gb >= 12.0:
        return {
            "profile": "4",
            "label": "Profile 4 — Balanced (LowRAM_LowVRAM)",
            "reason": f"{vram_gb:.1f} GB VRAM works best with balanced streaming offload.",
            "vram_gb": vram_gb,
            "ram_gb": ram_gb,
            "available_ram_gb": avail_ram_gb,
            "unified_memory": unified,
            "dgx_spark": dgx_spark,
            "vram_safety_coefficient": None,
            "prefer_bf16": blackwell,
            "prefer_nvfp4": blackwell or dgx_spark,
        }
    return {
        "profile": "5",
        "label": "Profile 5 — Fail-safe Low Memory Streaming",
        "reason": f"{vram_gb:.1f} GB VRAM requires weight streaming with minimal reserved RAM.",
        "vram_gb": vram_gb,
        "ram_gb": ram_gb,
        "available_ram_gb": avail_ram_gb,
        "unified_memory": unified,
        "dgx_spark": dgx_spark,
        "vram_safety_coefficient": None,
        "prefer_bf16": blackwell,
        "prefer_nvfp4": blackwell or dgx_spark,
    }


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
    auto_rec: dict[str, Any] = {}
    if config.get("_low_memory_retry"):
        profile = "5"
    elif profile == "auto":
        auto_rec = recommend_wan_memory_profile(runtime)
        recommended = auto_rec.get("profile")
        if recommended and wan_supports_option(config, "--profile"):
            profile = str(recommended)

    if profile != "auto":
        if profile not in _VALID_WAN_MEMORY_PROFILES:
            raise ValueError("Wan2GP memory profile must be auto, 1, 2, 3, 3.5, 4, 4.5, or 5.")
        if wan_supports_option(config, "--profile"):
            command += ["--profile", profile]
        elif not config.get("_low_memory_retry"):
            raise ValueError("This Wan2GP installation does not support selecting a memory profile.")

    # On high-VRAM systems (e.g., 96GB GPU / 32GB RAM or DGX Spark 128GB),
    # maximize GPU model residency and keep reserved host RAM near zero.
    if not config.get("_low_memory_retry"):
        vram_coeff = config.get("vram_safety_coefficient")
        if vram_coeff is None:
            vram_coeff = auto_rec.get("vram_safety_coefficient")
        if vram_coeff is not None and wan_supports_option(config, "--vram-safety-coefficient"):
            try:
                coeff_val = max(0.5, min(0.95, float(vram_coeff)))
                command += ["--vram-safety-coefficient", f"{coeff_val:.2f}"]
            except (TypeError, ValueError):
                pass
        if profile == "3.5" and wan_supports_option(config, "--perc-reserved-mem-max"):
            command += ["--perc-reserved-mem-max", "0.1"]
        if (
            str(config.get("precision") or "").strip().lower() == "bf16"
            and wan_supports_option(config, "--bf16")
        ):
            command += ["--bf16"]

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


# ── Cross-platform FFmpeg discovery, probing, frame extraction, and assembly ──


def find_ffmpeg_executables(app_dir: Path | None = None) -> dict[str, str]:
    """Locate ``ffmpeg`` and ``ffprobe`` in source, compiled, WSL, macOS, and Docker runtimes."""
    suffix = ".exe" if os.name == "nt" else ""
    found: dict[str, str] = {"ffmpeg": "", "ffprobe": ""}
    search_dirs: list[Path] = []
    if app_dir:
        search_dirs.append(Path(app_dir) / "ffmpeg_bins")
    try:
        app_root = Path(get_application_root(__file__))
        search_dirs.extend([
            app_root / "runtime" / "ffmpeg",
            app_root / "ffmpeg",
            app_root.parent / "runtime" / "ffmpeg",
        ])
    except Exception:
        pass
    exe_dir = Path(sys.executable).resolve().parent
    search_dirs.extend([exe_dir / "ffmpeg", exe_dir.parent / "runtime" / "ffmpeg"])
    if os.name != "nt":
        search_dirs.extend([Path("/usr/bin"), Path("/opt/homebrew/bin"), Path("/usr/local/bin")])

    for tool_name, env_key in (("ffmpeg", "AUTOYOU_TV_FFMPEG"), ("ffprobe", "AUTOYOU_TV_FFPROBE")):
        candidates: list[str] = []
        env_val = str(os.getenv(env_key) or "").strip()
        if env_val:
            candidates.append(env_val)
        on_path = shutil.which(tool_name)
        if on_path:
            candidates.append(on_path)
        for directory in search_dirs:
            candidates.append(str(directory / f"{tool_name}{suffix}"))
        for candidate in candidates:
            path = Path(candidate).expanduser()
            if path.is_file():
                found[tool_name] = str(path)
                break
    return found


def _materialize_media_path(stack: Any, path: Path) -> Path:
    """Resolve content-referenced or Maximus-encrypted AutoYou media into a readable temp file for codecs."""
    try:
        from shared import content_files

        if content_files.protected(path):
            return Path(stack.enter_context(content_files.materialized(path)))
    except Exception:
        pass
    return path


def probe_media_file(file_path: Path, *, app_dir: Path | None = None) -> dict[str, Any]:
    """Validate an image, video, or audio file and return deterministic metadata."""
    import contextlib

    orig_path = Path(file_path)
    if not orig_path.is_file():
        return {"valid": False, "error": "File does not exist.", "file_size_bytes": 0}
    with contextlib.ExitStack() as stack:
        path = _materialize_media_path(stack, orig_path)
        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            return {"valid": False, "error": "Output file is empty.", "file_size_bytes": 0}
        return _probe_materialized_media_file(path, size_bytes=size_bytes, app_dir=app_dir)


def _probe_materialized_media_file(path: Path, *, size_bytes: int, app_dir: Path | None = None) -> dict[str, Any]:

    ext = path.suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
        try:
            from PIL import Image

            with Image.open(path) as img:
                width, height = img.size
                img.verify()
            return {
                "valid": width > 0 and height > 0,
                "kind": "image",
                "width": int(width),
                "height": int(height),
                "duration_seconds": 0.0,
                "fps": 0.0,
                "has_audio": False,
                "file_size_bytes": size_bytes,
            }
        except Exception as exc:
            return {"valid": False, "kind": "image", "error": str(exc), "file_size_bytes": size_bytes}

    tools = find_ffmpeg_executables(app_dir)
    ffprobe = tools.get("ffprobe")
    if ffprobe:
        try:
            completed = subprocess.run(
                [
                    ffprobe, "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", str(path),
                ],
                capture_output=True, text=True, timeout=30,
                **process_spawn_kwargs(hide_window=True),
            )
            if completed.returncode == 0 and completed.stdout.strip():
                info = json.loads(completed.stdout)
                streams = info.get("streams") or []
                vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
                astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
                fmt = info.get("format") or {}
                duration = float(fmt.get("duration") or (vstream or astream or {}).get("duration") or 0.0)
                fps = 0.0
                if vstream:
                    rate = str(vstream.get("avg_frame_rate") or vstream.get("r_frame_rate") or "0/1")
                    if "/" in rate:
                        num, den = rate.split("/", 1)
                        fps = float(num) / float(den) if float(den or 0) else 0.0
                    else:
                        fps = float(rate or 0.0)
                return {
                    "valid": bool(vstream or astream),
                    "kind": "video" if vstream else ("audio" if astream else "unknown"),
                    "width": int(vstream.get("width") or 0) if vstream else 0,
                    "height": int(vstream.get("height") or 0) if vstream else 0,
                    "duration_seconds": round(duration, 3),
                    "fps": round(fps, 3),
                    "has_audio": bool(astream),
                    "file_size_bytes": size_bytes,
                }
        except Exception:
            pass

    # Fallback to PyAV if ffprobe is absent.
    try:
        import av  # type: ignore

        with av.open(str(path)) as container:
            vstream = container.streams.video[0] if container.streams.video else None
            astream = container.streams.audio[0] if container.streams.audio else None
            duration = float(container.duration / av.time_base) if container.duration else 0.0
            fps = float(vstream.average_rate) if (vstream and vstream.average_rate) else 0.0
            return {
                "valid": bool(vstream or astream),
                "kind": "video" if vstream else ("audio" if astream else "unknown"),
                "width": int(vstream.width) if vstream else 0,
                "height": int(vstream.height) if vstream else 0,
                "duration_seconds": round(duration, 3),
                "fps": round(fps, 3),
                "has_audio": bool(astream),
                "file_size_bytes": size_bytes,
            }
    except Exception as exc:
        return {"valid": False, "kind": "video", "error": str(exc), "file_size_bytes": size_bytes}


def extract_video_frame(
    video_path: Path,
    output_image_path: Path,
    *,
    position: str = "last",
    app_dir: Path | None = None,
) -> Path:
    """Extract the first or last frame of a rendered scene for visual continuity chaining."""
    import contextlib

    orig_source = Path(video_path)
    dest = Path(output_image_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        source = _materialize_media_path(stack, orig_source)
        tools = find_ffmpeg_executables(app_dir)
        ffmpeg = tools.get("ffmpeg")
        if ffmpeg:
            if position == "last":
                cmd = [
                    ffmpeg, "-y", "-sseof", "-0.15", "-i", str(source),
                    "-update", "1", "-frames:v", "1", str(dest),
                ]
            else:
                cmd = [
                    ffmpeg, "-y", "-i", str(source),
                    "-frames:v", "1", str(dest),
                ]
            completed = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
                **process_spawn_kwargs(hide_window=True),
            )
            if completed.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
                return dest

        # Fallback via PyAV
        import av  # type: ignore

        with av.open(str(source)) as container:
            if not container.streams.video:
                raise RuntimeError(f"No video stream in {source.name}")
            stream = container.streams.video[0]
            chosen = None
            for frame in container.decode(stream):
                chosen = frame
                if position == "first":
                    break
            if chosen is None:
                raise RuntimeError(f"Could not decode any frame from {source.name}")
            chosen.to_image().save(dest)
        return dest


def assemble_video_clips(
    clip_paths: Sequence[Path],
    output_path: Path,
    *,
    transitions: Sequence[str] | None = None,
    transition_seconds: float = 0.35,
    audio_path: Path | None = None,
    resolution: str | None = None,
    fps: int = 24,
    app_dir: Path | None = None,
    timeout: float = 3600.0,
) -> dict[str, Any]:
    """Assemble arbitrarily many rendered scenes into a single final MP4 using FFmpeg.

    Normalizes every clip first in a temporary staging directory so mixed audio
    presence, codecs, or frame rates never break assembly — and keeps memory
    constant even when assembling hundreds of scenes for multi-hour videos.
    """
    clips = [Path(p) for p in clip_paths if Path(p).is_file()]
    if not clips:
        raise ValueError("At least one rendered scene clip is required to assemble a video.")
    tools = find_ffmpeg_executables(app_dir)
    ffmpeg = tools.get("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "FFmpeg was not found on this machine. Install FFmpeg (or set AUTOYOU_TV_FFMPEG) "
            "to assemble multi-scene videos."
        )

    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    target_w, target_h = 0, 0
    if resolution:
        match = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", str(resolution))
        if match:
            target_w, target_h = int(match.group(1)), int(match.group(2))
    if target_w <= 0 or target_h <= 0:
        first_probe = probe_media_file(clips[0], app_dir=app_dir)
        target_w = int(first_probe.get("width") or 848)
        target_h = int(first_probe.get("height") or 480)
    # H.264 requires even dimensions.
    target_w += target_w % 2
    target_h += target_h % 2
    target_fps = max(8, min(60, int(fps or 24)))

    import contextlib

    with contextlib.ExitStack() as mat_stack, tempfile.TemporaryDirectory(prefix="autoyou_assemble_") as tmp_name:
        clips = [_materialize_media_path(mat_stack, c) for c in clips]
        if audio_path and Path(audio_path).is_file():
            audio_path = _materialize_media_path(mat_stack, Path(audio_path))
        tmp_dir = Path(tmp_name)
        normalized_clips: list[Path] = []
        durations: list[float] = []
        for idx, clip in enumerate(clips):
            info = probe_media_file(clip, app_dir=app_dir)
            has_audio = bool(info.get("has_audio"))
            norm_path = tmp_dir / f"scene_{idx:05d}.mp4"
            vf = (
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={target_fps},format=yuv420p"
            )
            cmd = [ffmpeg, "-y", "-i", str(clip)]
            if not has_audio:
                cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
            cmd += [
                "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
            ]
            if not has_audio:
                cmd += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
            else:
                cmd += ["-map", "0:v:0", "-map", "0:a:0"]
            cmd += ["-movflags", "+faststart", str(norm_path)]
            completed = run_owned_media_process(
                cmd, cwd=tmp_dir, env=dict(os.environ), timeout=min(timeout, 900.0),
            )
            if completed.returncode != 0 or not norm_path.is_file():
                raise RuntimeError(f"FFmpeg normalization failed on scene {idx + 1}: {completed.stderr[-600:]}")
            normalized_clips.append(norm_path)
            norm_info = probe_media_file(norm_path, app_dir=app_dir)
            durations.append(float(norm_info.get("duration_seconds") or 2.0))

        use_xfade = (
            len(normalized_clips) > 1
            and len(normalized_clips) <= 16
            and bool(transitions)
            and any(str(t or "").lower() in {"crossfade", "fade", "dissolve"} for t in transitions)
            and min(durations) > (transition_seconds * 2.2)
        )

        stitched_path = tmp_dir / "stitched.mp4" if (audio_path and Path(audio_path).is_file()) else dest
        if len(normalized_clips) == 1:
            shutil.copy2(normalized_clips[0], stitched_path)
        elif use_xfade:
            cmd = [ffmpeg, "-y"]
            for clip in normalized_clips:
                cmd += ["-i", str(clip)]
            vfilters: list[str] = []
            afilters: list[str] = []
            v_prev = "[0:v]"
            a_prev = "[0:a]"
            running_offset = durations[0]
            for idx in range(1, len(normalized_clips)):
                d = max(0.1, min(float(transition_seconds), durations[idx - 1] * 0.4, durations[idx] * 0.4))
                offset = max(0.0, running_offset - d)
                v_out = f"[v{idx}]" if idx < len(normalized_clips) - 1 else "[vout]"
                a_out = f"[a{idx}]" if idx < len(normalized_clips) - 1 else "[aout]"
                vfilters.append(f"{v_prev}[{idx}:v]xfade=transition=fade:duration={d:.3f}:offset={offset:.3f}{v_out}")
                afilters.append(f"{a_prev}[{idx}:a]acrossfade=d={d:.3f}{a_out}")
                v_prev = v_out
                a_prev = a_out
                running_offset = offset + durations[idx]
            filter_complex = ";".join(vfilters + afilters)
            cmd += [
                "-filter_complex", filter_complex,
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
                "-movflags", "+faststart", str(stitched_path),
            ]
            completed = run_owned_media_process(cmd, cwd=tmp_dir, env=dict(os.environ), timeout=timeout)
            if completed.returncode != 0 or not stitched_path.is_file():
                use_xfade = False  # Fall back to concat demuxer cleanly.

        if len(normalized_clips) > 1 and not use_xfade:
            concat_list = tmp_dir / "concat.txt"
            concat_lines = [
                f"file '{str(clip).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"
                for clip in normalized_clips
            ]
            concat_list.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
            cmd = [
                ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c", "copy", "-movflags", "+faststart", str(stitched_path),
            ]
            completed = run_owned_media_process(cmd, cwd=tmp_dir, env=dict(os.environ), timeout=timeout)
            if completed.returncode != 0 or not stitched_path.is_file():
                raise RuntimeError(f"FFmpeg concat assembly failed: {completed.stderr[-600:]}")

        if audio_path and Path(audio_path).is_file():
            cmd = [
                ffmpeg, "-y", "-i", str(stitched_path), "-i", str(audio_path),
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-map", "0:v:0", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
                "-shortest", "-movflags", "+faststart", str(dest),
            ]
            completed = run_owned_media_process(cmd, cwd=tmp_dir, env=dict(os.environ), timeout=timeout)
            if completed.returncode != 0 or not dest.is_file():
                # Fall back to replacing audio track if mixing fails.
                cmd_fallback = [
                    ffmpeg, "-y", "-i", str(stitched_path), "-i", str(audio_path),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(dest),
                ]
                completed2 = run_owned_media_process(cmd_fallback, cwd=tmp_dir, env=dict(os.environ), timeout=timeout)
                if completed2.returncode != 0 or not dest.is_file():
                    shutil.copy2(stitched_path, dest)

    final_probe = probe_media_file(dest, app_dir=app_dir)
    return {
        "output_path": str(dest),
        "scenes_count": len(clips),
        "used_crossfade": bool(use_xfade),
        "probe": final_probe,
    }
