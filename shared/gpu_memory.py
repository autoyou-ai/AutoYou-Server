# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""How much memory the GPU has for models, without loading anything.

Used to size the Ollama context window and to decide whether a voice model
fits on the GPU. Every source is optional and cheap; when none applies the
answer is None and callers keep their previous behaviour.

- ``AUTOYOU_GPU_MEMORY_GB``: the operator's figure wins (total memory for models).
- NVIDIA: ``nvidia-smi`` (total and free).
- AMD on Linux: the amdgpu driver's VRAM counters in sysfs (total and used).
- Apple silicon: memory is unified; Metal lets the GPU use about two thirds
  of RAM, three quarters above 36 GB.
- Windows: the display driver's dedicated memory size in the registry (no
  free figure). On AMD APUs this is the Variable Graphics Memory share.

GPUs with less than 2 GB of their own memory are treated as having none.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import glob
import logging
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

GPU_MEMORY_ENV = "AUTOYOU_GPU_MEMORY_GB"
GIB = 1024 ** 3
_MIB = 1024 ** 2
_MIN_GPU_BYTES = 2 * GIB
_WINDOWS_DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


@dataclass(frozen=True)
class GpuMemory:
    total_bytes: int
    free_bytes: Optional[int]
    vendor: str
    source: str
    unified: bool = False


_detected: dict = {}


def _from_env() -> Optional[GpuMemory]:
    raw = str(os.environ.get(GPU_MEMORY_ENV, "") or "").strip()
    if not raw:
        return None
    try:
        total = int(float(raw) * GIB)
    except ValueError:
        logger.warning("Ignoring %s=%r: not a number of gigabytes", GPU_MEMORY_ENV, raw)
        return None
    return GpuMemory(total, None, "configured", "env") if total > 0 else None


def _run(command: List[str]) -> str:
    kwargs: dict = {"capture_output": True, "text": True, "timeout": 5, "check": False}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, **kwargs)
    return result.stdout if result.returncode == 0 else ""


def _from_nvidia_smi() -> Optional[GpuMemory]:
    tool = shutil.which("nvidia-smi")
    if not tool:
        return None
    try:
        output = _run([tool, "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"])
    except Exception as exc:
        logger.debug("nvidia-smi did not answer: %s", exc)
        return None
    best: Optional[GpuMemory] = None
    for line in output.splitlines():
        try:
            total_mib, free_mib = (float(value) for value in line.split(",")[:2])
        except ValueError:
            continue
        candidate = GpuMemory(int(total_mib * _MIB), int(free_mib * _MIB), "nvidia", "nvidia-smi")
        if best is None or candidate.total_bytes > best.total_bytes:
            best = candidate
    return best if best and best.total_bytes >= _MIN_GPU_BYTES else None


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, encoding="ascii") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def _from_amd_sysfs() -> Optional[GpuMemory]:
    if not sys.platform.startswith("linux"):
        return None
    best: Optional[GpuMemory] = None
    for total_path in glob.glob("/sys/class/drm/card*/device/mem_info_vram_total"):
        total = _read_int(total_path)
        if not total:
            continue
        used = _read_int(total_path.replace("vram_total", "vram_used"))
        free = max(0, total - used) if used is not None else None
        candidate = GpuMemory(total, free, "amd", "amdgpu-sysfs")
        if best is None or candidate.total_bytes > best.total_bytes:
            best = candidate
    return best if best and best.total_bytes >= _MIN_GPU_BYTES else None


def _system_ram() -> tuple[Optional[int], Optional[int]]:
    try:
        import psutil

        memory = psutil.virtual_memory()
        return int(memory.total), int(memory.available)
    except Exception:
        return None, None


def _from_apple_unified() -> Optional[GpuMemory]:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return None
    total_ram, available = _system_ram()
    if not total_ram:
        return None
    share = 0.75 if total_ram > 36 * GIB else 0.67
    total = int(total_ram * share)
    free = min(total, available) if available is not None else None
    return GpuMemory(total, free, "apple", "unified-memory", unified=True)


def _from_windows_registry() -> Optional[GpuMemory]:
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    best: Optional[GpuMemory] = None
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _WINDOWS_DISPLAY_CLASS)
    except OSError:
        return None
    with root:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(root, name) as adapter:
                    size = _registry_value(adapter, "HardwareInformation.qwMemorySize")
                    if size is None:
                        size = _registry_value(adapter, "HardwareInformation.MemorySize")
                    provider = str(_registry_value(adapter, "ProviderName") or "")
            except OSError:
                continue
            if not isinstance(size, int) or size <= 0:
                continue
            lowered = provider.lower()
            vendor = "nvidia" if "nvidia" in lowered else "amd" if "advanced micro" in lowered or "amd" in lowered else "intel" if "intel" in lowered else "unknown"
            candidate = GpuMemory(size, None, vendor, "windows-registry")
            if best is None or candidate.total_bytes > best.total_bytes:
                best = candidate
    return best if best and best.total_bytes >= _MIN_GPU_BYTES else None


def _registry_value(key, name: str):
    import winreg

    try:
        value, kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    if kind == winreg.REG_BINARY and isinstance(value, (bytes, bytearray)):
        return int.from_bytes(bytes(value[:8]), "little")
    return value


def detect_gpu_memory(*, refresh: bool = False) -> Optional[GpuMemory]:
    """The GPU memory available to models, or None when it cannot be told.

    The answer is kept for the process; ``refresh=True`` asks again.
    """
    if "value" in _detected and not refresh:
        return _detected["value"]
    found: Optional[GpuMemory] = None
    for source in (_from_env, _from_nvidia_smi, _from_amd_sysfs, _from_apple_unified, _from_windows_registry):
        try:
            found = source()
        except Exception as exc:
            logger.debug("GPU memory source %s failed: %s", source.__name__, exc)
            found = None
        if found is not None:
            break
    _detected["value"] = found
    return found


def free_gpu_memory_bytes() -> Optional[int]:
    """Free GPU memory right now, where the platform reports it (NVIDIA, AMD on Linux, Apple)."""
    for source in (_from_nvidia_smi, _from_amd_sysfs, _from_apple_unified):
        try:
            found = source()
        except Exception:
            found = None
        if found is not None and found.free_bytes is not None:
            return found.free_bytes
    return None


def ctranslate2_cuda_available() -> bool:
    """Whether faster-whisper's engine (CTranslate2) can use a CUDA GPU.

    PyTorch's ROCm build answers yes to ``torch.cuda.is_available()`` on AMD
    GPUs, but CTranslate2's standard wheels run on NVIDIA CUDA only, so this
    asks CTranslate2 itself.
    """
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:
        return False


# Approximate GPU memory a Whisper model needs in faster-whisper (int8 or
# float16 weights plus working buffers), by model name prefix.
_WHISPER_GPU_BYTES = (
    ("distil-large", 3 * GIB),
    ("large", 4 * GIB),
    ("turbo", 3 * GIB),
    ("medium", 3 * GIB),
    ("small", 2 * GIB),
    ("base", 1 * GIB),
    ("tiny", 1 * GIB),
)


def whisper_gpu_bytes(model_name: str) -> int:
    name = str(model_name or "").strip().lower().replace("faster-whisper-", "")
    for prefix, needed in _WHISPER_GPU_BYTES:
        if name.startswith(prefix) or f"-{prefix}" in name:
            return needed
    return 2 * GIB


def whisper_device(requested: str, model_name: str = "base") -> str:
    """The device faster-whisper should use: ``cuda`` only when it can, and there is room.

    ``auto`` falls back to the CPU when CTranslate2 sees no CUDA GPU or the GPU
    reports less free memory than the model needs (an LLM already loaded there
    would otherwise be pushed partly onto the CPU). An explicit ``cuda`` is kept
    when CTranslate2 can use it, even if memory looks short.
    """
    normalized = str(requested or "").strip().lower()
    if normalized not in {"auto", "cuda"}:
        return "cpu"
    if not ctranslate2_cuda_available():
        if normalized == "cuda":
            logger.warning("Speech-to-text was set to cuda, but CTranslate2 sees no CUDA GPU; using the CPU")
        return "cpu"
    if normalized == "auto":
        free = free_gpu_memory_bytes()
        needed = whisper_gpu_bytes(model_name)
        if free is not None and free < needed:
            logger.info(
                "Speech-to-text runs on the CPU: the GPU has %.1f GB free and Whisper %s needs about %.1f GB",
                free / GIB, model_name, needed / GIB,
            )
            return "cpu"
    return "cuda"


__all__ = [
    "GPU_MEMORY_ENV",
    "GpuMemory",
    "ctranslate2_cuda_available",
    "detect_gpu_memory",
    "free_gpu_memory_bytes",
    "whisper_device",
    "whisper_gpu_bytes",
]
