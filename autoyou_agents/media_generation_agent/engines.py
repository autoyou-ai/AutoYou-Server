# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Cross-platform local media engine discovery, verification, installation, and execution.

Supports five local accelerators/backends across Windows, macOS, Linux, WSL,
Docker, NVIDIA CUDA, NVIDIA DGX Spark (Grace Blackwell GB10), AMD ROCm
(including RDNA 3.5 Strix Halo ``gfx1151``), and Apple Silicon (MLX / Metal):

- ``wan2gp``      : Wan2GP (Wan 2.1/2.2, LTX-2/2.3/2.5, Flux, Flux Kontext, Qwen Image Edit, Z-Image)
- ``comfyui``     : ComfyUI headless REST API (customizable node workflows across CUDA/ROCm/MPS/DGX Spark)
- ``mlx``         : Apple Silicon pure MLX (``mflux`` for Flux/edit images, ``mlx-video`` for LTX-2/Wan video)
- ``draw_things`` : Apple Silicon native Metal CLI (offline local inference)
- ``sdcpp``       : ``stable-diffusion.cpp`` / ``sd-cli`` C/C++ GGML backend (CUDA/ROCm/Vulkan/Metal/CPU)
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import json
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from shared.media_generation_runtime import (
    find_ffmpeg_executables,
    is_dgx_spark_runtime,
    is_unified_memory_gpu,
    probe_media_file,
    probe_wan_runtime,
    recommend_wan_memory_profile,
    run_owned_media_process,
    system_memory_bytes,
)
from shared.platform_runtime import get_service_data_dir, is_compiled
from shared.process_lifecycle import process_spawn_kwargs
from shared.secure_storage import SecureStorageError, append_secure_file, read_secure_file, write_secure_file

VALID_ENGINES = ("wan2gp", "comfyui", "mlx", "draw_things", "sdcpp")

COMFYUI_GIT_URL = "https://github.com/comfyanonymous/ComfyUI.git"
MLX_VIDEO_PIP_SPEC = "git+https://github.com/Blaizzy/mlx-video.git"
MFLUX_PIP_SPEC = "mflux"

_ENGINE_INSTALL_LOCK = threading.Lock()
_ENGINE_INSTALL_STATES: Dict[str, Dict[str, Any]] = {}


def _media_data_dir() -> Path:
    return get_service_data_dir("media_generation_agent", anchor=__file__)


def _clean_external_env() -> Dict[str, str]:
    env = os.environ.copy()
    for key in (
        "AUTOYOU_PACKAGED_RUNTIME",
        "AUTOYOU_PACKAGED_RESOURCES_ROOT",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONPLATLIBDIR",
        "PYTHONEXECUTABLE",
        "PYTHONUSERBASE",
        "VIRTUAL_ENV",
        "__PYVENV_LAUNCHER__",
    ):
        env.pop(key, None)
    return env


def _is_wsl() -> bool:
    if platform.system() != "Linux":
        return False
    try:
        release = platform.release().lower()
        if "microsoft" in release or "wsl" in release:
            return True
        proc_ver = Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
        return "microsoft" in proc_ver or "wsl" in proc_ver
    except OSError:
        return False


def _is_docker() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore").lower()
        return any(marker in cgroup for marker in ("docker", "containerd", "kubepods"))
    except OSError:
        return False


def _detect_nvidia_details() -> Dict[str, Any]:
    smi = shutil.which("nvidia-smi")
    if not smi and Path("/usr/lib/wsl/lib/nvidia-smi").is_file():
        smi = "/usr/lib/wsl/lib/nvidia-smi"
    if not smi:
        return {"present": False, "gpus": [], "driver": "", "dgx_spark": False}
    try:
        completed = subprocess.run(
            [
                smi,
                "--query-gpu=name,memory.total,driver_version,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            **process_spawn_kwargs(hide_window=True),
        )
        gpus: List[Dict[str, Any]] = []
        driver = ""
        dgx_spark = False
        machine = platform.machine().lower()
        for line in (completed.stdout or "").splitlines():
            parts = [p.strip() for p in line.split(",")]
            if not parts or not parts[0]:
                continue
            name = parts[0]
            mem_mb = 0.0
            try:
                mem_mb = float(parts[1]) if len(parts) > 1 and parts[1] not in {"[N/A]", "N/A", ""} else 0.0
            except ValueError:
                mem_mb = 0.0
            if len(parts) > 2 and parts[2]:
                driver = parts[2]
            cc_str = parts[3] if len(parts) > 3 else ""
            if "gb10" in name.lower() or "dgx spark" in name.lower() or (
                machine in {"aarch64", "arm64"} and cc_str.startswith(("10.", "12."))
            ):
                dgx_spark = True
            gpus.append(
                {
                    "name": name,
                    "vram_gb": round(mem_mb / 1024.0, 2) if mem_mb > 0 else 0.0,
                    "compute_cap": cc_str,
                }
            )
        return {"present": True, "gpus": gpus, "driver": driver, "dgx_spark": dgx_spark}
    except Exception:
        return {"present": True, "gpus": [], "driver": "", "dgx_spark": False}


def _detect_amd_details() -> Dict[str, Any]:
    targets: List[str] = []
    gpu_names: List[str] = []
    vram_bytes = 0
    clinfo = shutil.which("clinfo")
    if clinfo:
        try:
            completed = subprocess.run(
                [clinfo],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
                **process_spawn_kwargs(hide_window=True),
            )
            stdout = completed.stdout or ""
            targets.extend(re.findall(r"\bgfx[0-9a-f]+\b", stdout, flags=re.IGNORECASE))
            for m in re.finditer(r"Board Name[^\r\n:]*:\s*(.+)", stdout, flags=re.IGNORECASE):
                val = m.group(1).strip()
                if val and val not in gpu_names:
                    gpu_names.append(val)
            for m in re.finditer(r"Global memory size[^\r\n:]*:\s*(\d+)", stdout, flags=re.IGNORECASE):
                val = int(m.group(1))
                if val > vram_bytes:
                    vram_bytes = val
        except Exception:
            pass
    try:
        from shared import torch_wheel_selection as tws

        detected_gpu = tws.detect_gpu()
        if getattr(detected_gpu, "vendor", "") == "amd":
            for arch in getattr(detected_gpu, "rocm_archs", ()) or ():
                if arch and arch not in targets:
                    targets.append(arch)
            name = str(getattr(detected_gpu, "name", "") or "").strip()
            if name and name not in gpu_names:
                gpu_names.append(name)
    except Exception:
        pass
    unique_targets = sorted({t.lower() for t in targets if t})
    return {
        "present": bool(unique_targets or gpu_names),
        "targets": unique_targets,
        "names": gpu_names,
        "vram_gb": round(vram_bytes / (1024 ** 3), 2) if vram_bytes > 0 else 0.0,
    }


def detect_hardware_capabilities(wan_runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return full cross-platform hardware telemetry and optimal media defaults."""
    system = platform.system()
    machine = platform.machine().lower()
    apple_silicon = system == "Darwin" and machine in {"arm64", "aarch64"}
    total_ram, avail_ram = system_memory_bytes()
    total_ram_gb = round(total_ram / (1024 ** 3), 2) if total_ram else 0.0
    avail_ram_gb = round(avail_ram / (1024 ** 3), 2) if avail_ram else 0.0
    nvidia = _detect_nvidia_details() if not apple_silicon else {"present": False, "gpus": [], "driver": "", "dgx_spark": False}
    amd = _detect_amd_details() if not apple_silicon else {"present": False, "targets": [], "names": [], "vram_gb": 0.0}
    ffmpeg = find_ffmpeg_executables()

    runtime_snapshot: Dict[str, Any] = dict(wan_runtime or {})
    if not runtime_snapshot.get("memory_bytes"):
        if apple_silicon and total_ram:
            runtime_snapshot.setdefault("backend", "apple")
            runtime_snapshot["memory_bytes"] = int(total_ram * 0.75)
            runtime_snapshot["unified_memory"] = True
            runtime_snapshot.setdefault("device", "Apple Silicon Unified GPU")
        elif nvidia["present"] and nvidia["gpus"]:
            vram_gb = nvidia["gpus"][0].get("vram_gb") or 0.0
            runtime_snapshot.setdefault("backend", "nvidia")
            runtime_snapshot.setdefault("device", nvidia["gpus"][0].get("name") or "NVIDIA GPU")
            if nvidia["dgx_spark"] and total_ram and vram_gb <= 0:
                runtime_snapshot["memory_bytes"] = int(total_ram)
                runtime_snapshot["unified_memory"] = True
            elif vram_gb > 0:
                runtime_snapshot["memory_bytes"] = int(vram_gb * (1024 ** 3))
        elif amd["present"]:
            runtime_snapshot.setdefault("backend", "amd")
            if amd["names"]:
                runtime_snapshot.setdefault("device", amd["names"][0])
            if amd["targets"]:
                runtime_snapshot.setdefault("architecture", amd["targets"][0])
            if amd["vram_gb"] > 0:
                runtime_snapshot["memory_bytes"] = int(amd["vram_gb"] * (1024 ** 3))

    profile_rec = recommend_wan_memory_profile(runtime_snapshot, system_ram_bytes=total_ram or None)
    dgx_spark = bool(nvidia.get("dgx_spark") or is_dgx_spark_runtime(runtime_snapshot))
    unified_memory = bool(is_unified_memory_gpu(runtime_snapshot) or apple_silicon or dgx_spark)

    if apple_silicon:
        recommended_engine = "mlx"
        engine_reason = (
            "Apple Silicon detected: MLX (mflux / mlx-video) or Draw Things CLI uses unified Metal memory directly without PyTorch overhead."
        )
    elif dgx_spark:
        recommended_engine = "wan2gp"
        engine_reason = (
            "NVIDIA DGX Spark (Blackwell GB10) detected: Wan2GP or ComfyUI with CUDA 13 / NVFP4 + Profile 3.5 utilizes unified memory at full Tensor Core speed."
        )
    elif nvidia["present"]:
        recommended_engine = "wan2gp"
        engine_reason = "NVIDIA CUDA GPU detected: Wan2GP (or ComfyUI headless) delivers full CUDA acceleration."
    elif amd["present"]:
        recommended_engine = "wan2gp"
        engine_reason = (
            f"AMD ROCm GPU detected ({', '.join(amd['targets']) or 'AMD'}): Wan2GP with matching ROCm device wheels "
            f"and {profile_rec['label']} maximizes GPU memory while minimizing CPU/RAM usage."
        )
    else:
        recommended_engine = "sdcpp"
        engine_reason = (
            "No discrete CUDA/ROCm/Apple GPU detected: stable-diffusion.cpp (sd-cli) offers lightweight C/C++ CPU/Vulkan inference."
        )

    return {
        "os": system,
        "machine": machine,
        "cpu_cores": os.cpu_count() or 1,
        "compiled_runtime": is_compiled(),
        "is_wsl": _is_wsl(),
        "is_docker": _is_docker(),
        "apple_silicon": apple_silicon,
        "dgx_spark": dgx_spark,
        "unified_memory": unified_memory,
        "system_ram_gb": total_ram_gb,
        "available_ram_gb": avail_ram_gb,
        "nvidia": nvidia,
        "amd": amd,
        "gpu_vram_gb": profile_rec["vram_gb"],
        "gpu_name": str(runtime_snapshot.get("device") or (nvidia["gpus"][0]["name"] if nvidia["gpus"] else (amd["names"][0] if amd["names"] else ("Apple Silicon GPU" if apple_silicon else "CPU")))),
        "gpu_backend": str(runtime_snapshot.get("backend") or ("apple" if apple_silicon else ("nvidia" if nvidia["present"] else ("amd" if amd["present"] else "cpu")))),
        "ffmpeg_available": bool(ffmpeg.get("ffmpeg")),
        "ffprobe_available": bool(ffmpeg.get("ffprobe")),
        "ffmpeg_path": ffmpeg.get("ffmpeg") or "",
        "recommended_engine": recommended_engine,
        "recommended_engine_reason": engine_reason,
        "recommended_memory_profile": profile_rec,
    }


def build_gpu_torch_install_command(venv_python: Path, *, prefer_rocm_targets: Optional[List[str]] = None) -> List[str]:
    """Build the best PyTorch pip install command for CUDA (including DGX Spark/Blackwell) or AMD ROCm."""
    try:
        from shared import torch_wheel_selection as tws

        plan = tws.plan_torch_install(
            target_version=tws.DEFAULT_TORCH_VERSION,
            include_audio_vision=True,
        )
        if plan and getattr(plan, "pip_args", None):
            return [str(venv_python), "-m", "pip", "install", *list(plan.pip_args)]
    except Exception:
        pass

    system = platform.system()
    machine = platform.machine().lower()
    nvidia = _detect_nvidia_details()
    amd_targets = list(prefer_rocm_targets or _detect_amd_details().get("targets") or [])

    if amd_targets and not nvidia["present"]:
        targets = ",".join("device-" + t for t in amd_targets)
        return [
            str(venv_python), "-m", "pip", "install",
            f"torch[{targets}]==2.13.0+rocm10.0.0",
            f"torchvision[{targets}]==0.28.0+rocm10.0.0",
            "torchaudio==2.11.0.2+rocm10.0.0",
            "--index-url", "https://stable.repo.amd.com/rocm/whl-next/",
        ]
    if nvidia["present"]:
        # CUDA 13.0 for Blackwell / DGX Spark ARM64 and modern drivers; fallback cu126.
        variant = "cu130" if (nvidia.get("dgx_spark") or machine in {"aarch64", "arm64"}) else "cu126"
        return [
            str(venv_python), "-m", "pip", "install",
            "torch", "torchvision", "torchaudio",
            "--index-url", f"https://download.pytorch.org/whl/{variant}",
        ]
    return [str(venv_python), "-m", "pip", "install", "torch", "torchvision", "torchaudio"]


# ── Engine Discovery & Status ───────────────────────────────────────────────


def _http_json(url: str, *, method: str = "GET", payload: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": "AutoYou-MediaFactory/1"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return int(getattr(resp, "status", 200)), json.loads(raw) if raw else {}
            except ValueError:
                return int(getattr(resp, "status", 200)), raw
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, str(exc)


def discover_comfyui_installation(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = config or {}
    base_url = str(cfg.get("comfyui_url") or os.getenv("COMFYUI_URL") or "http://127.0.0.1:8188").strip().rstrip("/")
    status_code, stats = _http_json(f"{base_url}/system_stats", timeout=2.5)
    reachable = 200 <= status_code < 300 and isinstance(stats, dict)

    managed_dir = _media_data_dir() / "comfyui" / "app"
    candidates = [
        Path(str(cfg.get("comfyui_dir") or "")).expanduser() if cfg.get("comfyui_dir") else None,
        managed_dir,
        Path.home() / "ComfyUI",
        Path.home() / "comfyui",
    ]
    if os.name == "nt":
        for drive in ("C", "D", "E"):
            candidates.append(Path(f"{drive}:/ComfyUI"))
            candidates.append(Path(f"{drive}:/pinokio/api/comfyui.git/app"))
    else:
        candidates.append(Path.home() / "pinokio" / "api" / "comfyui.git" / "app")

    app_dir = ""
    for candidate in candidates:
        if candidate and (candidate / "main.py").is_file():
            app_dir = str(candidate)
            break

    devices = []
    if reachable and isinstance(stats.get("devices"), list):
        devices = [d.get("name", "") for d in stats["devices"] if isinstance(d, dict)]

    return {
        "engine": "comfyui",
        "supported": True,
        "ready": reachable,
        "url": base_url,
        "app_dir": app_dir,
        "installed": bool(app_dir) or reachable,
        "devices": devices,
        "install_supported": True,
        "guidance": (
            f"ComfyUI headless API is connected at {base_url}."
            if reachable else
            f"ComfyUI is installed at {app_dir} but not listening on {base_url}. Start ComfyUI or configure its URL."
            if app_dir else
            "Install ComfyUI (CUDA, ROCm, Apple MPS, DGX Spark, Docker) or enter an existing ComfyUI server URL."
        ),
    }


def discover_mlx_installation(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = config or {}
    system = platform.system()
    machine = platform.machine().lower()
    supported = system == "Darwin" and machine in {"arm64", "aarch64"}

    managed_venv = _media_data_dir() / "mlx" / "env"
    managed_bin = managed_venv / "bin"

    def _find_bin(name: str, configured: str = "") -> str:
        for candidate in (
            configured,
            str(managed_bin / name),
            shutil.which(name) or "",
            f"/opt/homebrew/bin/{name}",
            f"/usr/local/bin/{name}",
            str(Path.home() / ".local" / "bin" / name),
        ):
            if candidate and Path(candidate).expanduser().is_file():
                return str(Path(candidate).expanduser())
        return ""

    mflux_bin = _find_bin("mflux-generate", str(cfg.get("mflux_cli") or ""))
    mflux_edit_bin = _find_bin("mflux-generate-flux2-edit")
    mlx_python = _find_bin("python", str(cfg.get("mlx_python") or ""))
    mlx_video_available = False
    if supported and mlx_python:
        try:
            probe = subprocess.run(
                [mlx_python, "-c", "import mlx, mlx_video; print('ok')"],
                env=_clean_external_env(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            mlx_video_available = probe.returncode == 0 and "ok" in (probe.stdout or "")
        except Exception:
            mlx_video_available = False

    ready = supported and (bool(mflux_bin) or mlx_video_available)
    return {
        "engine": "mlx",
        "supported": supported,
        "ready": ready,
        "mflux_cli": mflux_bin,
        "mflux_edit_cli": mflux_edit_bin,
        "mlx_python": mlx_python,
        "mlx_video_available": mlx_video_available,
        "install_supported": supported,
        "guidance": (
            "Apple Silicon MLX ready (" + ", ".join(p for p in ("mflux" if mflux_bin else "", "mlx-video" if mlx_video_available else "") if p) + ")."
            if ready else
            "Apple Silicon detected. Click Install MLX to set up native mflux (Flux image/edit) and mlx-video (LTX-2/Wan video) on Apple Unified Memory."
            if supported else
            "MLX native engine requires Apple Silicon macOS (arm64)."
        ),
    }


def discover_sdcpp_installation(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = config or {}
    exe_names = ("sd-cli.exe", "sd.exe") if os.name == "nt" else ("sd-cli", "sd")
    configured = str(cfg.get("sdcpp_cli") or os.getenv("SDCPP_CLI_PATH") or "").strip()
    found = ""
    candidates: List[str] = [configured] if configured else []
    for name in exe_names:
        hit = shutil.which(name)
        if hit:
            candidates.append(hit)
        candidates.append(str(_media_data_dir() / "sdcpp" / "bin" / name))
    for candidate in candidates:
        p = Path(candidate).expanduser() if candidate else None
        if p and p.is_file():
            found = str(p)
            break
    models_dir = str(cfg.get("sdcpp_models_dir") or _media_data_dir() / "sdcpp" / "models")
    return {
        "engine": "sdcpp",
        "supported": True,
        "ready": bool(found),
        "cli": found,
        "models_dir": models_dir,
        "install_supported": False,
        "guidance": (
            f"stable-diffusion.cpp ready at {found} (GGML C/C++ zero-PyTorch runtime)."
            if found else
            "Optional lightweight C/C++ GGML backend (CUDA/ROCm/Vulkan/Metal/CPU). Install sd-cli from stable-diffusion.cpp releases and set its path in Configuration."
        ),
    }


def all_engines_status(
    config: Dict[str, Any],
    *,
    wan_env: Optional[Dict[str, Any]] = None,
    draw_things_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return unified status for all five supported local media engines."""
    wan_runtime = (wan_env or {}).get("runtime") if isinstance(wan_env, dict) else {}
    if not wan_runtime and str(config.get("engine") or "wan2gp") == "wan2gp":
        wan_runtime = probe_wan_runtime(config, _clean_external_env())
    hardware = detect_hardware_capabilities(wan_runtime)
    comfy = discover_comfyui_installation(config)
    mlx = discover_mlx_installation(config)
    sdcpp = discover_sdcpp_installation(config)
    dt = dict(draw_things_status or {})
    wan_ready = bool((wan_runtime or {}).get("ready"))
    wan_summary = {
        "engine": "wan2gp",
        "supported": hardware["os"] in {"Windows", "Linux"} or bool(wan_ready),
        "ready": wan_ready,
        "backend": (wan_runtime or {}).get("backend", hardware["gpu_backend"]),
        "device": (wan_runtime or {}).get("device", hardware["gpu_name"]),
        "memory_bytes": (wan_runtime or {}).get("memory_bytes", 0),
        "recommended_profile": hardware["recommended_memory_profile"],
        "install_supported": bool((wan_env or {}).get("install_supported", hardware["os"] in {"Windows", "Linux"} and (hardware["nvidia"]["present"] or hardware["amd"]["present"]))),
        "guidance": (
            f"Wan2GP ready on {(wan_runtime or {}).get('device', 'GPU')} ({hardware['recommended_memory_profile']['label']})."
            if wan_ready else
            str((wan_runtime or {}).get("error") or (wan_env or {}).get("install_guidance") or "Configure or install Wan2GP.")
        ),
    }
    return {
        "active_engine": str(config.get("engine") or hardware["recommended_engine"]),
        "hardware": hardware,
        "engines": {
            "wan2gp": wan_summary,
            "comfyui": comfy,
            "mlx": mlx,
            "draw_things": {
                "engine": "draw_things",
                "supported": bool(dt.get("supported")),
                "ready": bool(dt.get("ready")),
                "cli": dt.get("cli", ""),
                "models_dir": dt.get("models_dir", ""),
                "install_supported": False,
                "guidance": dt.get("guidance", "Draw Things CLI requires Apple Silicon macOS."),
            },
            "sdcpp": sdcpp,
        },
    }


def verify_media_factory_installation(config: Dict[str, Any], *, wan_env: Optional[Dict[str, Any]] = None, draw_things_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run a comprehensive, distributable self-verification across OS/GPU/FFmpeg/storage."""
    snapshot = all_engines_status(config, wan_env=wan_env, draw_things_status=draw_things_status)
    hw = snapshot["hardware"]
    active = snapshot["active_engine"]
    active_status = snapshot["engines"].get(active, {})
    output_root = _media_data_dir()
    storage_writable = False
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        probe_file = output_root / f".write_check_{uuid.uuid4().hex[:8]}"
        probe_file.write_bytes(b"ok")
        probe_file.unlink()
        storage_writable = True
    except OSError:
        storage_writable = False

    checks = [
        {
            "name": "storage_writable",
            "ok": storage_writable,
            "detail": f"Writable runtime storage at {output_root}" if storage_writable else f"Cannot write to {output_root}",
        },
        {
            "name": "active_engine_ready",
            "ok": bool(active_status.get("ready")),
            "detail": str(active_status.get("guidance") or f"Engine '{active}' is not ready."),
        },
        {
            "name": "ffmpeg_assembly",
            "ok": bool(hw.get("ffmpeg_available")),
            "detail": (
                f"FFmpeg available at {hw['ffmpeg_path']} (enables multi-scene stitching, continuity frame chaining, and audio muxing)."
                if hw.get("ffmpeg_available") else
                "FFmpeg not found; single clips work, but multi-scene long video assembly needs FFmpeg."
            ),
        },
        {
            "name": "memory_optimization",
            "ok": True,
            "detail": (
                f"{hw['gpu_vram_gb']} GB GPU / {hw['system_ram_gb']} GB RAM -> "
                f"{hw['recommended_memory_profile']['label']}"
            ),
        },
    ]
    ready_engines = [name for name, info in snapshot["engines"].items() if info.get("ready")]
    return {
        "ok": bool(storage_writable and active_status.get("ready")),
        "long_video_ready": bool(storage_writable and active_status.get("ready") and hw.get("ffmpeg_available")),
        "active_engine": active,
        "ready_engines": ready_engines,
        "checks": checks,
        "hardware": hw,
        "engines": snapshot["engines"],
    }


# ── Managed Engine Installers (ComfyUI + Apple Silicon MLX) ─────────────────


def _engine_install_log_path(engine: str) -> Path:
    return _media_data_dir() / f"{engine}_install.log"


def _set_engine_install_state(engine: str, **updates: Any) -> None:
    with _ENGINE_INSTALL_LOCK:
        cur = _ENGINE_INSTALL_STATES.setdefault(
            engine,
            {
                "engine": engine,
                "status": "idle",
                "step": "",
                "error": "",
                "started_at": "",
                "finished_at": "",
                "log_path": str(_engine_install_log_path(engine)),
            },
        )
        cur.update(updates)


def _engine_install_log(engine: str, message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    try:
        append_secure_file(_engine_install_log_path(engine), (line + "\n").encode("utf-8"))
    except SecureStorageError:
        raise
    except OSError:
        pass


def get_engine_install_status(engine: str) -> Dict[str, Any]:
    with _ENGINE_INSTALL_LOCK:
        state = dict(
            _ENGINE_INSTALL_STATES.get(
                engine,
                {
                    "engine": engine,
                    "status": "idle",
                    "step": "",
                    "error": "",
                    "started_at": "",
                    "finished_at": "",
                    "log_path": str(_engine_install_log_path(engine)),
                },
            )
        )
    log_path = _engine_install_log_path(engine)
    tail = ""
    try:
        if log_path.exists():
            tail = read_secure_file(log_path).decode("utf-8", errors="replace")[-4000:]
    except Exception:
        pass
    state["log_tail"] = tail
    return state


def _run_managed_step(engine: str, step: str, command: List[str], cwd: Optional[Path] = None) -> None:
    _set_engine_install_state(engine, step=step)
    _engine_install_log(engine, f"{step}: {' '.join(command)}")
    completed = run_owned_media_process(
        command,
        cwd=cwd or _media_data_dir(),
        env=_clean_external_env(),
        timeout=7200,
        on_output=lambda _stream, text: _engine_install_log(engine, text.rstrip()),
    )
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "")[-1800:]
        raise RuntimeError(f"{step} failed (exit {completed.returncode}): {tail}")


def _find_python_for_engine() -> Optional[str]:
    if os.name == "nt" and shutil.which("py"):
        try:
            r = subprocess.run(
                [shutil.which("py"), "-3.12", "-c", "import sys; print(sys.executable)"],
                env=_clean_external_env(), capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and Path(r.stdout.strip()).is_file():
                return r.stdout.strip()
        except Exception:
            pass
    for candidate in ("python3.12", "python3.11", "python3", "python"):
        exe = shutil.which(candidate)
        if exe:
            return exe
    return None


def start_managed_engine_install(
    engine: str,
    *,
    on_config_update: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Start a background cross-platform install for ``comfyui`` or ``mlx``."""
    engine = str(engine or "").strip().lower()
    if engine not in {"comfyui", "mlx"}:
        return {"status": "unsupported", "message": f"Managed install for '{engine}' is handled separately or manually."}

    if engine == "mlx":
        if not (platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}):
            return {"status": "unsupported", "message": "MLX engine requires Apple Silicon macOS (arm64)."}

    with _ENGINE_INSTALL_LOCK:
        cur = _ENGINE_INSTALL_STATES.get(engine, {})
        if cur.get("status") == "running":
            return {"status": "already_running", "message": f"{engine} install is already running."}
        _ENGINE_INSTALL_STATES[engine] = {
            "engine": engine,
            "status": "running",
            "step": "starting",
            "error": "",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": "",
            "log_path": str(_engine_install_log_path(engine)),
        }
    try:
        write_secure_file(_engine_install_log_path(engine), b"")
    except OSError:
        pass

    def _worker() -> None:
        try:
            py = _find_python_for_engine()
            if not py:
                raise RuntimeError("Python 3.11/3.12 not found on PATH.")
            if engine == "mlx":
                mlx_root = _media_data_dir() / "mlx"
                env_dir = mlx_root / "env"
                mlx_root.mkdir(parents=True, exist_ok=True)
                venv_py = env_dir / "bin" / "python"
                if not venv_py.exists():
                    _run_managed_step("mlx", "Creating Apple MLX environment", [py, "-m", "venv", str(env_dir)], cwd=mlx_root)
                _run_managed_step("mlx", "Upgrading pip", [str(venv_py), "-m", "pip", "install", "--upgrade", "pip", "wheel"], cwd=mlx_root)
                _run_managed_step("mlx", "Installing mflux (Flux image & edit)", [str(venv_py), "-m", "pip", "install", MFLUX_PIP_SPEC], cwd=mlx_root)
                if shutil.which("git"):
                    _run_managed_step("mlx", "Installing mlx-video (LTX-2 / Wan video)", [str(venv_py), "-m", "pip", "install", MLX_VIDEO_PIP_SPEC], cwd=mlx_root)
                if on_config_update:
                    on_config_update(
                        {
                            "engine": "mlx",
                            "mlx_python": str(venv_py),
                            "mflux_cli": str(env_dir / "bin" / "mflux-generate"),
                        }
                    )
            elif engine == "comfyui":
                git_exe = shutil.which("git")
                if not git_exe:
                    raise RuntimeError("git is required to install ComfyUI.")
                comfy_root = _media_data_dir() / "comfyui"
                app_dir = comfy_root / "app"
                comfy_root.mkdir(parents=True, exist_ok=True)
                if (app_dir / ".git").exists():
                    _run_managed_step("comfyui", "Updating ComfyUI", [git_exe, "pull", "--ff-only"], cwd=app_dir)
                else:
                    _run_managed_step("comfyui", "Cloning ComfyUI", [git_exe, "clone", "--depth", "1", COMFYUI_GIT_URL, str(app_dir)], cwd=comfy_root)
                env_dir = app_dir / "env"
                venv_py = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                if not venv_py.exists():
                    _run_managed_step("comfyui", "Creating ComfyUI environment", [py, "-m", "venv", str(env_dir)], cwd=app_dir)
                _run_managed_step("comfyui", "Upgrading pip", [str(venv_py), "-m", "pip", "install", "--upgrade", "pip", "wheel"], cwd=app_dir)
                _run_managed_step("comfyui", "Installing GPU PyTorch", build_gpu_torch_install_command(venv_py), cwd=app_dir)
                req = app_dir / "requirements.txt"
                if req.exists():
                    _run_managed_step("comfyui", "Installing ComfyUI requirements", [str(venv_py), "-m", "pip", "install", "-r", str(req)], cwd=app_dir)
                if on_config_update:
                    on_config_update({"comfyui_dir": str(app_dir), "comfyui_python": str(venv_py)})

            _set_engine_install_state(engine, status="completed", step="done", finished_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            _engine_install_log(engine, f"ERROR: {exc}")
            _set_engine_install_state(engine, status="failed", error=str(exc), finished_at=datetime.now(timezone.utc).isoformat())

    threading.Thread(target=_worker, daemon=True, name=f"media-engine-install-{engine}").start()
    return {"status": "started", "message": f"Started background install for {engine}."}


# ── Engine Execution Adapters (ComfyUI, MLX, stable-diffusion.cpp) ──────────


def _parse_wh(resolution: str, default: Tuple[int, int] = (768, 512)) -> Tuple[int, int]:
    m = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", str(resolution or ""))
    if not m:
        return default
    return max(64, int(m.group(1))), max(64, int(m.group(2)))


def _comfyui_upload_image(base_url: str, image_path: Path) -> str:
    boundary = f"----AutoYouBoundary{uuid.uuid4().hex}"
    filename = image_path.name
    mimetype = mimetypes.guess_type(filename)[0] or "image/png"
    file_bytes = image_path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {mimetype}\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/upload/image",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        info = json.loads(resp.read().decode("utf-8", errors="replace"))
    return str(info.get("name") or filename)


def _default_comfyui_image_workflow(
    *,
    prompt: str,
    negative_prompt: str,
    checkpoint: str,
    width: int,
    height: int,
    steps: int,
    seed: int,
    uploaded_image: str = "",
    denoise: float = 0.65,
) -> Dict[str, Any]:
    effective_seed = seed if seed >= 0 else int(time.time() * 1000) % (2**31 - 1)
    workflow: Dict[str, Any] = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt or "watermark, blurry, deformed", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "autoyou_comfy", "images": ["8", 0]}},
    }
    if uploaded_image:
        workflow["10"] = {"class_type": "LoadImage", "inputs": {"image": uploaded_image}}
        workflow["11"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["4", 2]}}
        latent_ref = ["11", 0]
        denoise_val = max(0.15, min(1.0, float(denoise)))
    else:
        workflow["5"] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
        latent_ref = ["5", 0]
        denoise_val = 1.0
    workflow["3"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": effective_seed,
            "steps": max(1, int(steps)),
            "cfg": 6.5,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": denoise_val,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": latent_ref,
        },
    }
    return workflow


def execute_comfyui_generation(
    *,
    config: Dict[str, Any],
    prompt: str,
    media_type: str,
    model_type: str,
    resolution: str,
    steps: int,
    frames: Optional[int],
    seed: int,
    output_dir: Path,
    negative_prompt: str = "",
    reference_image_path: Optional[Path] = None,
    denoise_strength: float = 0.65,
    timeout: float = 3600.0,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Path:
    """Submit a headless generation workflow to ComfyUI and download the artifact."""
    base_url = str(config.get("comfyui_url") or os.getenv("COMFYUI_URL") or "http://127.0.0.1:8188").strip().rstrip("/")
    width, height = _parse_wh(resolution, (1024, 576) if media_type == "image" else (848, 480))
    uploaded_name = ""
    if reference_image_path and Path(reference_image_path).is_file():
        uploaded_name = _comfyui_upload_image(base_url, Path(reference_image_path))

    workflow_path_key = "comfyui_video_workflow" if media_type == "video" else "comfyui_image_workflow"
    custom_workflow_path = str(config.get(workflow_path_key) or "").strip()
    if custom_workflow_path and Path(custom_workflow_path).expanduser().is_file():
        raw = Path(custom_workflow_path).expanduser().read_text(encoding="utf-8")
        rendered = (
            raw.replace("{{PROMPT}}", json.dumps(prompt)[1:-1])
            .replace("{{NEGATIVE_PROMPT}}", json.dumps(negative_prompt)[1:-1])
            .replace("{{WIDTH}}", str(width))
            .replace("{{HEIGHT}}", str(height))
            .replace("{{STEPS}}", str(steps))
            .replace("{{FRAMES}}", str(frames or 49))
            .replace("{{SEED}}", str(seed if seed >= 0 else int(time.time() * 1000) % (2**31 - 1)))
            .replace("{{IMAGE}}", uploaded_name)
        )
        workflow = json.loads(rendered)
    else:
        if media_type == "video":
            raise RuntimeError(
                "ComfyUI video generation requires a ComfyUI API workflow JSON configured at "
                "'comfyui_video_workflow' (export with Save API Format in ComfyUI and use {{PROMPT}}/{{IMAGE}}/{{FRAMES}} placeholders), "
                "or switch engine to Wan2GP / MLX for built-in video generation."
            )
        checkpoint = model_type or str(config.get("comfyui_checkpoint") or "flux1-schnell-fp8.safetensors")
        workflow = _default_comfyui_image_workflow(
            prompt=prompt,
            negative_prompt=negative_prompt,
            checkpoint=checkpoint,
            width=width,
            height=height,
            steps=steps,
            seed=seed,
            uploaded_image=uploaded_name,
            denoise=denoise_strength,
        )

    client_id = f"autoyou-{uuid.uuid4().hex[:10]}"
    code, resp = _http_json(f"{base_url}/prompt", method="POST", payload={"prompt": workflow, "client_id": client_id}, timeout=30.0)
    if not (200 <= code < 300) or not isinstance(resp, dict) or not resp.get("prompt_id"):
        raise RuntimeError(f"ComfyUI rejected workflow (HTTP {code}): {resp}")
    prompt_id = str(resp["prompt_id"])
    if on_progress:
        on_progress(f"ComfyUI queued prompt {prompt_id[:8]}...")

    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        hcode, hist = _http_json(f"{base_url}/history/{urllib.parse.quote(prompt_id)}", timeout=15.0)
        if 200 <= hcode < 300 and isinstance(hist, dict) and prompt_id in hist:
            entry = hist[prompt_id]
            outputs = entry.get("outputs") if isinstance(entry, dict) else {}
            if isinstance(outputs, dict):
                for node_out in outputs.values():
                    if not isinstance(node_out, dict):
                        continue
                    items = list(node_out.get("videos") or []) + list(node_out.get("gifs") or []) + list(node_out.get("images") or [])
                    if items:
                        first = items[0]
                        qs = urllib.parse.urlencode(
                            {
                                "filename": first.get("filename", ""),
                                "subfolder": first.get("subfolder", ""),
                                "type": first.get("type", "output"),
                            }
                        )
                        out_name = Path(str(first.get("filename") or f"comfy_{prompt_id[:8]}.png")).name
                        out_path = output_dir / out_name
                        with urllib.request.urlopen(f"{base_url}/view?{qs}", timeout=120) as dl:
                            out_path.write_bytes(dl.read())
                        return out_path
            status_obj = entry.get("status") if isinstance(entry, dict) else {}
            if isinstance(status_obj, dict) and status_obj.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI workflow failed: {status_obj}")
        time.sleep(1.5)
    raise TimeoutError(f"ComfyUI generation timed out after {timeout:g}s.")


def execute_mlx_generation(
    *,
    config: Dict[str, Any],
    prompt: str,
    media_type: str,
    model_type: str,
    resolution: str,
    steps: int,
    frames: Optional[int],
    seed: int,
    output_dir: Path,
    work_dir: Path,
    reference_image_path: Optional[Path] = None,
    timeout: float = 3600.0,
    on_output: Optional[Callable[[str, str], None]] = None,
    on_started: Optional[Callable[[int], None]] = None,
) -> Path:
    """Run native Apple Silicon MLX generation via ``mflux`` (image/edit) or ``mlx-video`` (video)."""
    status = discover_mlx_installation(config)
    if not status["supported"]:
        raise RuntimeError("MLX engine requires Apple Silicon macOS (arm64).")
    width, height = _parse_wh(resolution, (1024, 576) if media_type == "image" else (768, 512))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if media_type == "image":
        use_edit = bool(reference_image_path and Path(reference_image_path).is_file() and status.get("mflux_edit_cli"))
        cli = status["mflux_edit_cli"] if use_edit else status.get("mflux_cli", "")
        if not cli:
            raise RuntimeError("mflux CLI is not installed. Click 'Install MLX' in Configuration.")
        out_path = output_dir / f"autoyou_mlx_{stamp}.png"
        model_arg = model_type or str(config.get("mlx_image_model") or "schnell")
        cmd = [
            cli,
            "--prompt", prompt,
            "--output", str(out_path),
            "--width", str(width),
            "--height", str(height),
            "--steps", str(max(1, int(steps))),
        ]
        if use_edit and reference_image_path:
            cmd += ["--image-paths", str(reference_image_path)]
        else:
            cmd += ["--model", model_arg, "-q", str(config.get("mlx_quantize") or 8)]
            if reference_image_path and Path(reference_image_path).is_file():
                cmd += ["--init-image-path", str(reference_image_path)]
        if seed >= 0:
            cmd += ["--seed", str(seed)]
    else:
        mlx_py = status.get("mlx_python", "")
        if not mlx_py or not status.get("mlx_video_available"):
            raise RuntimeError("mlx-video is not installed in the MLX environment. Click 'Install MLX' in Configuration.")
        out_path = output_dir / f"autoyou_mlx_{stamp}.mp4"
        module = str(config.get("mlx_video_module") or "mlx_video.ltx_2.generate")
        cmd = [
            mlx_py, "-m", module,
            "--prompt", prompt,
            "--width", str(width),
            "--height", str(height),
            "-n", str(int(frames or 97)),
            "--output", str(out_path),
        ]
        if reference_image_path and Path(reference_image_path).is_file():
            cmd += ["--image", str(reference_image_path)]
        if seed >= 0:
            cmd += ["--seed", str(seed)]

    completed = run_owned_media_process(
        cmd,
        cwd=work_dir,
        env=_clean_external_env(),
        timeout=timeout,
        work_dir=work_dir,
        on_output=on_output,
        on_started=on_started,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"MLX generation failed (exit {completed.returncode}): {(completed.stderr or completed.stdout)[-800:]}")
    if out_path.is_file():
        return out_path
    candidates = sorted(
        [p for p in output_dir.glob("*") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        raise RuntimeError("MLX exited cleanly but produced no output file.")
    return candidates[-1]


def execute_sdcpp_generation(
    *,
    config: Dict[str, Any],
    prompt: str,
    media_type: str,
    model_type: str,
    resolution: str,
    steps: int,
    frames: Optional[int],
    seed: int,
    output_dir: Path,
    work_dir: Path,
    negative_prompt: str = "",
    reference_image_path: Optional[Path] = None,
    timeout: float = 3600.0,
    on_output: Optional[Callable[[str, str], None]] = None,
    on_started: Optional[Callable[[int], None]] = None,
) -> Path:
    """Run lightweight C/C++ GGML inference using ``stable-diffusion.cpp`` (``sd-cli``)."""
    status = discover_sdcpp_installation(config)
    cli = status.get("cli", "")
    if not cli:
        raise RuntimeError("stable-diffusion.cpp (sd-cli) was not found. Configure its path in Settings.")
    model_path = Path(model_type).expanduser()
    if not model_path.is_file():
        models_dir = Path(status["models_dir"]).expanduser()
        candidate = models_dir / model_type
        if candidate.is_file():
            model_path = candidate
        else:
            raise RuntimeError(f"stable-diffusion.cpp model file not found: '{model_type}' (checked {models_dir}).")
    width, height = _parse_wh(resolution, (768, 512))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = ".mp4" if media_type == "video" else ".png"
    out_path = output_dir / f"autoyou_sdcpp_{stamp}{ext}"
    cmd = [
        cli,
        "--model", str(model_path),
        "-p", prompt,
        "-o", str(out_path),
        "-W", str(width),
        "-H", str(height),
        "--steps", str(max(1, int(steps))),
    ]
    if negative_prompt:
        cmd += ["-n", negative_prompt]
    if seed >= 0:
        cmd += ["-s", str(seed)]
    if reference_image_path and Path(reference_image_path).is_file():
        cmd += ["-i", str(reference_image_path)]
    if media_type == "video" and frames:
        cmd += ["--video-frames", str(int(frames))]
    completed = run_owned_media_process(
        cmd,
        cwd=work_dir,
        env=_clean_external_env(),
        timeout=timeout,
        work_dir=work_dir,
        on_output=on_output,
        on_started=on_started,
    )
    if completed.returncode != 0 or not out_path.is_file():
        raise RuntimeError(f"stable-diffusion.cpp failed (exit {completed.returncode}): {(completed.stderr or completed.stdout)[-800:]}")
    return out_path
