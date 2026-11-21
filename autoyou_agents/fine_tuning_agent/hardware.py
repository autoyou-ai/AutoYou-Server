# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Accelerator discovery and memory budgeting for the Fine Tuning agent.

The question this module answers is "how much can a LoRA job safely use on THIS
machine", and it has to answer it for hardware that does not agree on what
memory means:

* a discrete NVIDIA/AMD/Intel card has its own VRAM and a free figure;
* Apple silicon, NVIDIA GB10 (DGX Spark), Jetson, AMD Strix Halo and Intel
  integrated graphics share one pool with the operating system, so "total" is
  not what a job may take and "free" moves under it;
* a CPU-only host has only system RAM.

The previous capability code read ``nvidia-smi`` for free memory and nothing
else, so every ROCm, MPS and unified-memory host fell through to the most
conservative profile no matter how much memory it had.

Nothing here imports torch at import time, and every probe takes its inputs as
arguments so the whole module is testable without a GPU.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

GIB = 1024 ** 3

#: Backends this module can name, in the order a host is preferred when more
#: than one is present.
BACKENDS = ("cuda", "rocm", "xpu", "mps", "cpu")

#: Families that share memory with the operating system.
UNIFIED_FAMILIES = frozenset({"apple_silicon", "dgx_spark", "jetson", "strix_halo", "intel_igpu"})

MEMORY_OVERRIDE_ENV = "AUTOYOU_FINE_TUNING_MEMORY_GB"

_DATACENTER_NVIDIA = re.compile(r"\b(A100|A800|H100|H200|H800|B100|B200|GB200|L40S?|L4|A10G?|A30|A40|V100|T4|P100|Tesla)\b", re.I)
_AMD_APU_ARCHES = frozenset({"gfx1150", "gfx1151", "gfx1152", "gfx1153"})
_AMD_INSTINCT_ARCHES = ("gfx908", "gfx90a", "gfx942", "gfx950", "gfx1250")


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    backend: str
    total_bytes: int
    free_bytes: Optional[int] = None
    arch: Optional[str] = None
    capability: Optional[tuple] = None
    bf16: bool = False
    fp16: bool = True

    def to_dict(self) -> dict:
        data = asdict(self)
        data["total_gib"] = round(self.total_bytes / GIB, 1)
        data["free_gib"] = round(self.free_bytes / GIB, 1) if self.free_bytes is not None else None
        return data


@dataclass(frozen=True)
class HardwareReport:
    system: str
    machine: str
    cpu_threads: int
    system_ram_bytes: int
    system_ram_free_bytes: Optional[int]
    backend: str
    devices: tuple
    family: str
    mlx_available: bool = False
    bitsandbytes_available: bool = False
    torch_version: Optional[str] = None
    notes: tuple = ()

    @property
    def unified_memory(self) -> bool:
        return self.family in UNIFIED_FAMILIES

    @property
    def primary(self) -> Optional[DeviceInfo]:
        return self.devices[0] if self.devices else None

    @property
    def accelerated(self) -> bool:
        return self.backend != "cpu"

    def usable_bytes(self, environ: Optional[Mapping[str, str]] = None) -> int:
        """Bytes a training job may plan to use, with a safety margin applied.

        ``AUTOYOU_FINE_TUNING_MEMORY_GB`` overrides everything: the operator
        knows things the probe cannot, such as how much of a unified pool the
        firmware reserved for the GPU.
        """
        env = os.environ if environ is None else environ
        raw = str(env.get(MEMORY_OVERRIDE_ENV, "")).strip()
        if raw:
            try:
                value = float(raw)
                if value > 0:
                    return int(value * GIB)
            except ValueError:
                pass
        device = self.primary
        if device is None or self.backend == "cpu":
            base = self.system_ram_free_bytes or int(self.system_ram_bytes * 0.6)
            return int(base * 0.6)
        total = device.total_bytes
        free = device.free_bytes
        family = self.family
        if family in {"dgx_spark", "jetson"}:
            # CUDA's free figure already reflects what the OS and desktop hold.
            return int((free or total) * 0.85)
        if family == "strix_halo":
            # torch reports the dedicated carve-out plus the shared window. A
            # process runs out well below that sum: a measured 96 GB split ran
            # out of room for one multi-GiB tensor far under the reported total.
            return int(total * 0.75)
        if family == "apple_silicon":
            # total is already capped at Metal's recommended working set.
            return int(total * 0.9)
        if family == "intel_igpu":
            return int(total * 0.5)
        return int((free or int(total * 0.9)) * 0.92)

    def to_dict(self) -> dict:
        return {
            "system": self.system,
            "machine": self.machine,
            "cpu_threads": self.cpu_threads,
            "system_ram_gib": round(self.system_ram_bytes / GIB, 1),
            "system_ram_free_gib": round(self.system_ram_free_bytes / GIB, 1) if self.system_ram_free_bytes else None,
            "backend": self.backend,
            "family": self.family,
            "unified_memory": self.unified_memory,
            "devices": [device.to_dict() for device in self.devices],
            "usable_gib": round(self.usable_bytes() / GIB, 1),
            "mlx_available": self.mlx_available,
            "bitsandbytes_available": self.bitsandbytes_available,
            "torch_version": self.torch_version,
            "notes": list(self.notes),
        }


def prefers_eager_attention(backend: str, name: str = "", arch: Optional[str] = None) -> bool:
    """True where SDPA's kernels are known to fail and eager attention is the reliable path.

    The AMD APUs (Strix Halo and its siblings, gfx1150-1153) raise
    ``hipErrorInvalidValue`` in an elementwise kernel right after the SDPA call, on
    the first forward. The planner chooses eager for them; a job started without a
    plan must get the same answer.
    """
    return backend == "rocm" and classify_family(backend, name=name, arch=arch) == "strix_halo"


# ----------------------------------------------------------------- classify
def classify_family(backend: str, name: str = "", arch: Optional[str] = None,
                    capability: Optional[tuple] = None, machine: str = "") -> str:
    """Name the hardware family, which decides memory policy and defaults."""
    label = str(name or "")
    arch = str(arch or "").split(":")[0].lower()
    if backend == "mps":
        return "apple_silicon"
    if backend == "xpu":
        if re.search(r"Data Center|Max\s?\d|Ponte|Flex", label, re.I):
            return "intel_datacenter"
        if re.search(r"\bArc\b.*\b(Graphics)$|Iris|Core\(?TM?\)? Ultra|UHD", label, re.I) and not re.search(r"\b[AB]\d{3}\b", label):
            return "intel_igpu"
        return "intel_arc"
    if backend == "cuda":
        if "GB10" in label or capability == (12, 1):
            return "dgx_spark"
        if re.search(r"Jetson|Orin|Thor|Tegra", label, re.I) or capability in {(8, 7), (11, 0)}:
            return "jetson"
        if _DATACENTER_NVIDIA.search(label):
            return "nvidia_datacenter"
        return "nvidia_consumer"
    if backend == "rocm":
        if arch in _AMD_APU_ARCHES or re.search(r"8060S|8050S|Ryzen AI", label, re.I):
            return "strix_halo"
        if arch.startswith(_AMD_INSTINCT_ARCHES) or re.search(r"Instinct|\bMI\d{2,3}", label, re.I):
            return "amd_instinct"
        return "amd_radeon"
    return "cpu"


# ------------------------------------------------------------------ probes
def probe_system_ram() -> tuple:
    """``(total_bytes, free_bytes_or_None)`` without requiring psutil."""
    try:
        import psutil  # type: ignore[import]

        memory = psutil.virtual_memory()
        return int(memory.total), int(memory.available)
    except Exception:
        pass
    try:
        system = platform.system()
        if system == "Windows":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            return int(status.ullTotalPhys), int(status.ullAvailPhys)
        if system == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return int(out.stdout.strip()), None
        with open("/proc/meminfo", encoding="utf-8") as handle:
            values = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in handle if ":" in line and len(line.split()) > 1}
        return values.get("MemTotal", 0), values.get("MemAvailable")
    except Exception:
        return 0, None


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _cuda_like_devices(torch_module: Any, backend: str) -> list:
    devices = []
    count = int(torch_module.cuda.device_count())
    for index in range(count):
        props = torch_module.cuda.get_device_properties(index)
        total = int(getattr(props, "total_memory", 0) or 0)
        free: Optional[int] = None
        try:
            free = int(torch_module.cuda.mem_get_info(index)[0])
        except Exception:
            pass
        capability = (int(props.major), int(props.minor)) if hasattr(props, "major") else None
        arch = getattr(props, "gcnArchName", None) if backend == "rocm" else (
            f"sm_{capability[0]}{capability[1]}" if capability else None)
        try:
            bf16 = bool(torch_module.cuda.is_bf16_supported())
        except Exception:
            bf16 = bool(capability and capability[0] >= 8) if backend == "cuda" else backend == "rocm"
        devices.append(DeviceInfo(
            index=index, name=str(getattr(props, "name", f"{backend} device {index}")), backend=backend,
            total_bytes=total, free_bytes=free, arch=str(arch) if arch else None, capability=capability,
            bf16=bf16, fp16=True,
        ))
    return devices


def _xpu_devices(torch_module: Any) -> list:
    devices = []
    for index in range(int(torch_module.xpu.device_count())):
        props = torch_module.xpu.get_device_properties(index)
        free: Optional[int] = None
        try:
            free = int(torch_module.xpu.mem_get_info(index)[0])
        except Exception:
            pass
        try:
            bf16 = bool(torch_module.xpu.is_bf16_supported())
        except Exception:
            bf16 = True
        devices.append(DeviceInfo(
            index=index, name=str(getattr(props, "name", f"xpu {index}")), backend="xpu",
            total_bytes=int(getattr(props, "total_memory", 0) or 0), free_bytes=free,
            arch=str(getattr(props, "architecture", "") or "") or None, bf16=bf16, fp16=True,
        ))
    return devices


def _apple_device(torch_module: Any, ram_total: int, run: Callable[..., Any]) -> DeviceInfo:
    name = "Apple silicon"
    try:
        out = run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5)
        if getattr(out, "returncode", 1) == 0 and str(out.stdout).strip():
            name = str(out.stdout).strip()
    except Exception:
        pass
    # Metal will not hand a process the whole of unified memory.
    total = int(ram_total * 0.66)
    try:
        recommended = int(torch_module.mps.recommended_max_memory())
        if recommended > 0:
            total = recommended
    except Exception:
        pass
    return DeviceInfo(index=0, name=name, backend="mps", total_bytes=total, free_bytes=None,
                      arch="apple-silicon", bf16=True, fp16=True)


def probe_hardware(
    torch_module: Any = None,
    *,
    system: Optional[str] = None,
    machine: Optional[str] = None,
    cpu_threads: Optional[int] = None,
    ram_probe: Optional[Callable[[], tuple]] = None,
    module_probe: Optional[Callable[[str], bool]] = None,
    run: Optional[Callable[..., Any]] = None,
) -> HardwareReport:
    """Describe the accelerator this interpreter can actually train on.

    ``torch_module`` is whatever torch the caller already imported; when omitted
    torch is imported if it is installed. Everything is injectable so each host
    family can be tested from a plain fake.
    """
    system = system or platform.system()
    machine = (machine or platform.machine() or "").lower()
    has = module_probe or _module_available
    runner = run or subprocess.run
    ram_total, ram_free = (ram_probe or probe_system_ram)()
    threads = int(cpu_threads or os.cpu_count() or 1)
    notes: list = []

    if torch_module is None and has("torch"):
        try:
            import torch as torch_module  # type: ignore[import]
        except Exception as exc:  # a broken install must not break the status call
            notes.append(f"torch is installed but failed to import: {exc}")
            torch_module = None

    backend = "cpu"
    devices: list = []
    torch_version = None
    if torch_module is not None:
        torch_version = str(getattr(torch_module, "__version__", "") or "") or None
        try:
            if torch_module.cuda.is_available():
                backend = "rocm" if getattr(torch_module.version, "hip", None) else "cuda"
                devices = _cuda_like_devices(torch_module, backend)
            elif hasattr(torch_module, "xpu") and torch_module.xpu.is_available():
                backend = "xpu"
                devices = _xpu_devices(torch_module)
            elif getattr(getattr(torch_module, "backends", None), "mps", None) is not None \
                    and torch_module.backends.mps.is_available():
                backend = "mps"
                devices = [_apple_device(torch_module, ram_total, runner)]
        except Exception as exc:
            notes.append(f"accelerator probe failed: {exc}")
            backend, devices = "cpu", []
        if backend == "cpu" and not notes:
            notes.append("This torch build exposes no CUDA, ROCm, XPU or MPS device.")

    primary = devices[0] if devices else None
    family = classify_family(
        backend, primary.name if primary else "", primary.arch if primary else None,
        primary.capability if primary else None, machine,
    )
    native_apple = system == "Darwin" and machine in {"arm64", "aarch64"}
    mlx = bool(native_apple and has("mlx") and has("mlx_lm"))
    if native_apple and backend == "cpu" and not devices:
        family = "apple_silicon"
    bnb = bool(backend == "cuda" and has("bitsandbytes"))
    return HardwareReport(
        system=system, machine=machine, cpu_threads=threads,
        system_ram_bytes=int(ram_total or 0), system_ram_free_bytes=ram_free,
        backend=backend, devices=tuple(devices), family=family,
        mlx_available=mlx, bitsandbytes_available=bnb, torch_version=torch_version, notes=tuple(notes),
    )
