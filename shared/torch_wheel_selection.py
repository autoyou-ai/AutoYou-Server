# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Choose the PyTorch wheel that can train on this machine's GPU.

PyPI's torch wheel is CPU-only on Windows and a CUDA build on Linux, so a plain
``pip install -r requirements/tuning.txt`` leaves AMD machines, and NVIDIA
machines on Windows, training on the CPU. This module finds the GPU with tools
the operating system already ships (``nvidia-smi``, Windows CIM, Linux sysfs),
picks the vendor's wheel index, and describes the pip step.

It uses only the standard library: bootstrap imports it under the system
Python before the server environment has any packages, and the Fine Tuning
agent imports it inside compiled packages.

Overrides:
    AUTOYOU_TORCH_BACKEND    auto (default), cpu, cuda, cu126, cu130, cu132,
                             rocm, or default (keep PyPI's wheel).
    AUTOYOU_TORCH_ROCM_ARCH  AMD gfx target(s), e.g. ``gfx1151`` or
                             ``gfx1100,gfx1036``, when detection cannot read it.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import functools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Collection, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

TORCH_BACKEND_ENV = "AUTOYOU_TORCH_BACKEND"
ROCM_ARCH_ENV = "AUTOYOU_TORCH_ROCM_ARCH"

#: Used when requirements/locked.txt cannot be read.
DEFAULT_TORCH_VERSION = "2.13.0"

PYTORCH_INDEX_ROOT = "https://download.pytorch.org/whl"
#: AMD's ROCm torch index for Windows and Linux. Each GPU's kernels ship as an
#: ``amd-torch-device-<gfx>`` wheel selected with a ``device-<gfx>`` extra. The
#: sibling repo.amd.com/rocm/whl-multi-arch index stops at torch 2.12 on Windows.
AMD_ROCM_INDEX = "https://stable.repo.amd.com/rocm/whl-next/"
#: PyTorch's own ROCm wheels carry kernels for a fixed list of GPUs and need no
#: device extra. Used on Linux when the gfx target cannot be read.
PYTORCH_ROCM_VARIANT = "rocm7.2"

#: gfx targets with torch 2.13 device wheels on AMD_ROCM_INDEX (checked 2026-10-22).
AMD_ROCM_ARCHS_WINDOWS = frozenset(
    {
        "gfx908", "gfx90a",
        "gfx1010", "gfx1011", "gfx1012",
        "gfx1030", "gfx1031", "gfx1032", "gfx1033", "gfx1034", "gfx1035", "gfx1036",
        "gfx1100", "gfx1101", "gfx1102", "gfx1103",
        "gfx1150", "gfx1151", "gfx1152", "gfx1153",
        "gfx1200", "gfx1201",
    }
)
AMD_ROCM_ARCHS_LINUX = AMD_ROCM_ARCHS_WINDOWS | {"gfx942", "gfx950", "gfx1250"}

#: CUDA wheel variants, newest first: (variant, minimum Windows driver,
#: minimum Linux driver, minimum compute capability). CUDA 13 dropped GPUs
#: older than Turing (compute capability 7.5).
CUDA_VARIANTS: Tuple[Tuple[str, Tuple[int, int], Tuple[int, int], Tuple[int, int]], ...] = (
    ("cu130", (580, 0), (580, 0), (7, 5)),
    ("cu126", (528, 33), (525, 60), (5, 0)),
)
CUDA_VARIANT_NAMES = ("cu126", "cu130", "cu132")

#: Packages the GPU index owns. Their files differ from PyPI's, so they are
#: never checked against the PyPI hashes in requirements/locked.txt.
GPU_TORCH_PACKAGES = ("torch", "torchvision", "torchaudio")
#: Runtime packages a GPU torch build pulls in (ROCm SDK, AMD device kernels,
#: NVIDIA libraries, Triton). An installed GPU build keeps these versions.
GPU_RUNTIME_PREFIXES = ("rocm", "amd-torch", "nvidia-", "cuda-", "triton", "pytorch-triton")

_PCI_VENDORS = {"10de": "nvidia", "1002": "amd", "8086": "intel"}

#: AMD PCI device ids to gfx target. Not exhaustive; the marketing-name rules
#: and AUTOYOU_TORCH_ROCM_ARCH cover the rest.
_AMD_PCI_ARCH_RANGES: Tuple[Tuple[int, int, str], ...] = (
    (0x7310, 0x731F, "gfx1010"),  # Navi 10: RX 5600/5700
    (0x7360, 0x7362, "gfx1011"),  # Navi 12
    (0x7340, 0x734F, "gfx1012"),  # Navi 14: RX 5300/5500
    (0x73A0, 0x73AF, "gfx1030"),  # Navi 21: RX 6800/6900/6950
    (0x73BF, 0x73BF, "gfx1030"),
    (0x73C0, 0x73DF, "gfx1031"),  # Navi 22: RX 6700/6750
    (0x73E0, 0x73FF, "gfx1032"),  # Navi 23: RX 6600/6650
    (0x163F, 0x163F, "gfx1033"),  # Van Gogh
    (0x7420, 0x743F, "gfx1034"),  # Navi 24: RX 6400/6500
    (0x1681, 0x1681, "gfx1035"),  # Rembrandt: Radeon 660M/680M
    (0x164E, 0x164E, "gfx1036"),  # Raphael / Granite Ridge integrated GPU
    (0x7440, 0x745F, "gfx1100"),  # Navi 31: RX 7900, Radeon Pro W7800/W7900
    (0x7460, 0x747F, "gfx1101"),  # Navi 32: RX 7700 XT/7800 XT, Radeon Pro W7700
    (0x7480, 0x749F, "gfx1102"),  # Navi 33: RX 7600, RX 7600S/7700S
    (0x15BF, 0x15BF, "gfx1103"),  # Phoenix / Hawk Point: Radeon 760M/780M
    (0x15C8, 0x15C8, "gfx1103"),  # Phoenix 2: Radeon 740M
    (0x150E, 0x150E, "gfx1150"),  # Strix Point: Radeon 880M/890M
    (0x1586, 0x1586, "gfx1151"),  # Strix Halo: Radeon 8040S/8050S/8060S
    (0x1114, 0x1114, "gfx1152"),  # Krackan Point: Radeon 840M/860M
    (0x7590, 0x759F, "gfx1200"),  # Navi 44: RX 9060
    (0x7550, 0x755F, "gfx1201"),  # Navi 48: RX 9070, Radeon AI PRO R9700
    (0x738C, 0x738E, "gfx908"),  # Instinct MI100
    (0x7408, 0x7410, "gfx90a"),  # Instinct MI200
)

#: Marketing-name rules for AMD GPUs whose PCI id is not in the table above.
#: Desktop "RX nnnn" names only: mobile M/S parts reuse numbers across chips.
_AMD_NAME_ARCH_RULES: Tuple[Tuple[str, str], ...] = (
    (r"\b80[456]0s\b", "gfx1151"),
    (r"\b8[89]0m\b", "gfx1150"),
    (r"\b8[246]0m\b", "gfx1152"),
    (r"\b7[468]0m\b", "gfx1103"),
    (r"\b6[68]0m\b", "gfx1035"),
    (r"\bai pro r9700\b", "gfx1201"),
    (r"\bpro w7[89]00\b", "gfx1100"),
    (r"\bpro w7700\b", "gfx1101"),
    (r"\bpro w7[56]00\b", "gfx1102"),
    (r"\bpro w6800\b", "gfx1030"),
    (r"\bpro w6600\b", "gfx1032"),
)
_AMD_DESKTOP_RX_ARCH = {
    "5500": "gfx1012", "5600": "gfx1010", "5700": "gfx1010",
    "6400": "gfx1034", "6500": "gfx1034",
    "6600": "gfx1032", "6650": "gfx1032",
    "6700": "gfx1031", "6750": "gfx1031",
    "6800": "gfx1030", "6900": "gfx1030", "6950": "gfx1030",
    "7600": "gfx1102", "7650": "gfx1102",
    "7700": "gfx1101", "7800": "gfx1101",
    "7900": "gfx1100",
    "9060": "gfx1200", "9070": "gfx1201",
}

_WINDOWS_GPU_QUERY = (
    "Get-CimInstance -ClassName Win32_VideoController | "
    "Select-Object Name,PNPDeviceID,AdapterCompatibility | ConvertTo-Json -Compress"
)
_WSL_NVIDIA_SMI = "usr/lib/wsl/lib/nvidia-smi"

RunFn = Callable[[Sequence[str]], Optional["subprocess.CompletedProcess[str]"]]
WhichFn = Callable[[str], Optional[str]]


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "")).strip().lower()


def public_version(version: str) -> Tuple[int, ...]:
    """``"2.13.0+rocm10.1.0"`` -> ``(2, 13, 0)``; pre/dev suffixes are dropped."""
    parts: List[int] = []
    for piece in str(version or "").split("+", 1)[0].split("."):
        match = re.match(r"\d+", piece)
        if not match:
            break
        parts.append(int(match.group(0)))
    return tuple(parts)


def _version_at_least(version: str, minimum: str) -> bool:
    have, want = public_version(version), public_version(minimum)
    width = max(len(have), len(want))
    return have + (0,) * (width - len(have)) >= want + (0,) * (width - len(want))


def torchvision_series(torch_version: str) -> str:
    """torchvision's minor version tracks torch's: torch 2.13 pairs with 0.28."""
    numbers = public_version(torch_version)
    if len(numbers) >= 2 and numbers[0] == 2:
        return f"0.{numbers[1] + 15}.*"
    return ""


# --------------------------------------------------------------------------
# What is installed


@dataclass(frozen=True)
class InstalledTorch:
    """The torch build in a Python environment, read without importing torch."""

    version: str
    cuda: str = ""
    hip: str = ""
    packages: Mapping[str, str] = field(default_factory=dict)

    @property
    def build(self) -> str:
        if self.hip:
            return "rocm"
        if self.cuda:
            return "cuda"
        return "cpu"

    @property
    def is_gpu_build(self) -> bool:
        return self.build in {"cuda", "rocm"}

    @property
    def label(self) -> str:
        if self.build == "rocm":
            return f"torch {self.version} (ROCm {self.hip} build)"
        if self.build == "cuda":
            return f"torch {self.version} (CUDA {self.cuda} build)"
        return f"torch {self.version} (CPU-only build)"

    def pins(self) -> Dict[str, str]:
        """Exact versions that keep this GPU build in place during later installs."""
        pins = {"torch": self.version}
        for name, version in sorted(self.packages.items()):
            name = canonical_name(name)
            if name == "torch":
                continue
            if "+" in str(version) or name.startswith(GPU_RUNTIME_PREFIXES):
                pins[name] = str(version)
        return pins


#: Run with the target environment's interpreter. It reads torch/version.py
#: instead of importing torch, which can take seconds and load GPU drivers.
INSTALLED_TORCH_PROBE = r"""
import importlib.metadata as md, json, re
prefixes = %r
out = {"packages": {}}
for dist in md.distributions():
    name = re.sub(r"[-_.]+", "-", str(dist.metadata["Name"] or "")).lower()
    if name in ("torch", "torchvision", "torchaudio") or name.startswith(prefixes):
        out["packages"][name] = dist.version
try:
    dist = md.distribution("torch")
except md.PackageNotFoundError:
    dist = None
if dist is not None:
    out["version"] = dist.version
    try:
        text = open(dist.locate_file("torch/version.py"), encoding="utf-8").read()
    except OSError:
        text = ""
    for key in ("cuda", "hip"):
        match = re.search(r"^%%s\s*(?::[^=\n]*)?=\s*['\"]([^'\"]+)['\"]" %% key, text, re.M)
        out[key] = match.group(1) if match else ""
print(json.dumps(out))
""" % (GPU_RUNTIME_PREFIXES,)


def parse_installed_torch(text: str) -> Optional[InstalledTorch]:
    """Parse INSTALLED_TORCH_PROBE output; None when torch is not installed."""
    try:
        data = json.loads(str(text or "").strip().splitlines()[-1])
    except (IndexError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("version"):
        return None
    packages = {canonical_name(k): str(v) for k, v in dict(data.get("packages") or {}).items()}
    return InstalledTorch(
        version=str(data["version"]),
        cuda=str(data.get("cuda") or ""),
        hip=str(data.get("hip") or ""),
        packages=packages,
    )


# --------------------------------------------------------------------------
# What the machine has


@dataclass(frozen=True)
class GpuAdapter:
    vendor: str  # nvidia | amd | intel | apple | other
    name: str = ""
    arch: str = ""  # AMD gfx target, e.g. gfx1151
    driver_version: str = ""  # NVIDIA driver, e.g. 581.57
    compute_capability: str = ""  # NVIDIA, e.g. 8.9
    pci_device: str = ""


@dataclass(frozen=True)
class GpuInventory:
    system: str
    machine: str
    adapters: Tuple[GpuAdapter, ...] = ()
    is_wsl: bool = False

    def of(self, vendor: str) -> Tuple[GpuAdapter, ...]:
        return tuple(adapter for adapter in self.adapters if adapter.vendor == vendor)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "system": self.system,
            "machine": self.machine,
            "is_wsl": self.is_wsl,
            "adapters": [
                {key: value for key, value in adapter.__dict__.items() if value}
                for adapter in self.adapters
            ],
        }


def gfx_from_target_version(value: int) -> str:
    """Linux kfd ``gfx_target_version`` 110501 -> ``gfx1151``; 90010 -> ``gfx90a``."""
    major, minor, stepping = value // 10000, (value // 100) % 100, value % 100
    return f"gfx{major}{minor}{stepping:x}"


def amd_arch_for(pci_device: str = "", name: str = "") -> str:
    """Best-known gfx target for an AMD GPU, or "" when it cannot be told."""
    try:
        device = int(str(pci_device or "").lower().removeprefix("0x"), 16)
    except ValueError:
        device = -1
    for start, end, arch in _AMD_PCI_ARCH_RANGES:
        if start <= device <= end:
            return arch
    lowered = str(name or "").lower()
    for pattern, arch in _AMD_NAME_ARCH_RULES:
        if re.search(pattern, lowered):
            return arch
    match = re.search(r"\brx\s*(\d{4})(?![0-9a-z])", lowered)
    if match:
        return _AMD_DESKTOP_RX_ARCH.get(match.group(1), "")
    return ""


def parse_nvidia_smi(text: str) -> List[GpuAdapter]:
    """Parse ``nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader``."""
    adapters = []
    for line in str(text or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2 or not parts[0] or not re.match(r"\d", parts[1]):
            continue
        capability = parts[2] if len(parts) > 2 and re.fullmatch(r"\d+\.\d+", parts[2]) else ""
        adapters.append(GpuAdapter("nvidia", parts[0], driver_version=parts[1], compute_capability=capability))
    return adapters


def parse_windows_video_controllers(text: str) -> List[GpuAdapter]:
    """Parse the Win32_VideoController JSON from _WINDOWS_GPU_QUERY."""
    try:
        data = json.loads(str(text or "").strip() or "[]")
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    adapters = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        pnp = str(item.get("PNPDeviceID") or "")
        match = re.search(r"VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})", pnp, re.IGNORECASE)
        vendor_id = match.group(1).lower() if match else ""
        device_id = match.group(2).lower() if match else ""
        vendor = _PCI_VENDORS.get(vendor_id, "")
        if not vendor:
            owner = f"{item.get('AdapterCompatibility') or ''} {name}".lower()
            vendor = (
                "nvidia" if "nvidia" in owner
                else "amd" if "advanced micro devices" in owner or re.search(r"\b(amd|radeon)\b", owner)
                else "intel" if "intel" in owner
                else "other"
            )
        arch = amd_arch_for(device_id, name) if vendor == "amd" else ""
        adapters.append(GpuAdapter(vendor, name, arch=arch, pci_device=device_id))
    return adapters


def _run_quietly(command: Sequence[str], timeout: float = 20.0) -> Optional["subprocess.CompletedProcess[str]"]:
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _nvidia_adapters(run: RunFn, which: WhichFn, root: Path) -> List[GpuAdapter]:
    command = which("nvidia-smi")
    wsl_smi = root / _WSL_NVIDIA_SMI
    if not command and wsl_smi.exists():
        command = str(wsl_smi)
    if not command:
        return []
    for fields in ("name,driver_version,compute_cap", "name,driver_version"):
        result = run([command, f"--query-gpu={fields}", "--format=csv,noheader"])
        if result is not None and result.returncode == 0:
            adapters = parse_nvidia_smi(result.stdout)
            if adapters:
                return adapters
    return []


def _linux_adapters(root: Path) -> List[GpuAdapter]:
    adapters: List[GpuAdapter] = []
    nodes = root / "sys/class/kfd/kfd/topology/nodes"
    for properties in sorted(nodes.glob("*/properties")):
        match = re.search(r"^gfx_target_version\s+(\d+)", _read_text(properties), re.MULTILINE)
        if match and int(match.group(1)):
            name = _read_text(properties.parent / "name").strip()
            adapters.append(GpuAdapter("amd", name or "AMD GPU", arch=gfx_from_target_version(int(match.group(1)))))
    # Without the kfd interface (no amdgpu compute driver), fall back to PCI ids.
    amd_from_kfd = bool(adapters)
    drm = root / "sys/class/drm"
    for card in sorted(drm.glob("card*")):
        if not re.fullmatch(r"card\d+", card.name):
            continue
        vendor = _PCI_VENDORS.get(_read_text(card / "device/vendor").strip().lower().removeprefix("0x"), "")
        device = _read_text(card / "device/device").strip().lower().removeprefix("0x")
        if vendor == "amd" and not amd_from_kfd:
            adapters.append(GpuAdapter("amd", f"AMD GPU {device}", arch=amd_arch_for(device), pci_device=device))
        elif vendor == "nvidia":
            adapters.append(GpuAdapter("nvidia", f"NVIDIA GPU {device}", pci_device=device))
    return adapters


def _is_wsl(root: Path, environ: Mapping[str, str]) -> bool:
    if environ.get("WSL_DISTRO_NAME"):
        return True
    return "microsoft" in _read_text(root / "proc/version").lower()


def detect_gpus(
    system: Optional[str] = None,
    machine: Optional[str] = None,
    *,
    run: Optional[RunFn] = None,
    which: Optional[WhichFn] = None,
    root: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> GpuInventory:
    """List this machine's GPUs. Every probe is optional; failures mean "not found"."""
    system = system or platform.system()
    machine = (machine or platform.machine() or "").lower()
    run = run or _run_quietly
    which = which or shutil.which
    root = Path("/") if root is None else Path(root)
    environ = os.environ if environ is None else environ

    adapters: List[GpuAdapter] = []
    if system in {"Windows", "Linux"}:
        adapters.extend(_nvidia_adapters(run, which, root))
    has_smi_nvidia = bool(adapters)
    if system == "Windows":
        shell = which("powershell") or which("pwsh")
        result = run([shell, "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_GPU_QUERY]) if shell else None
        if result is not None and result.returncode == 0:
            for adapter in parse_windows_video_controllers(result.stdout):
                if adapter.vendor == "nvidia" and has_smi_nvidia:
                    continue
                adapters.append(adapter)
    elif system == "Linux":
        for adapter in _linux_adapters(root):
            if adapter.vendor == "nvidia" and has_smi_nvidia:
                continue
            adapters.append(adapter)
    elif system == "Darwin" and machine in {"arm64", "aarch64"}:
        adapters.append(GpuAdapter("apple", "Apple Silicon GPU"))
    is_wsl = system == "Linux" and _is_wsl(root, environ)
    return GpuInventory(system=system, machine=machine, adapters=tuple(adapters), is_wsl=is_wsl)


@functools.lru_cache(maxsize=1)
def detect_gpus_cached() -> GpuInventory:
    """detect_gpus() once per process, for status pages that ask repeatedly."""
    return detect_gpus()


# --------------------------------------------------------------------------
# What to install


@dataclass(frozen=True)
class TorchWheelPlan:
    backend: str  # cuda | rocm | mps | cpu
    action: str  # install | keep | default
    reason: str
    index_url: str = ""
    requirements: Tuple[str, ...] = ()
    variant: str = ""  # cu130, rocm-gfx1151, rocm7.2 ...
    hint: str = ""

    def pip_args(self) -> List[str]:
        if self.action != "install":
            return []
        return ["install", "--index-url", self.index_url, *self.requirements]

    def pip_command(self, python: str = "python") -> str:
        if self.action != "install":
            return ""
        quoted = [f'"{part}"' if re.search(r"[\s\[\]*<>]", part) else part for part in self.pip_args()]
        python_part = f'"{python}"' if " " in python else python
        return " ".join([python_part, "-m", "pip", *quoted])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "action": self.action,
            "reason": self.reason,
            "index_url": self.index_url,
            "requirements": list(self.requirements),
            "variant": self.variant,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class BackendRequest:
    backend: str  # auto | cpu | cuda | rocm | default
    variant: str = ""
    explicit: bool = False


def parse_backend_request(value: Optional[str]) -> BackendRequest:
    raw = str(value or "").strip().lower()
    if raw in {"", "auto"}:
        return BackendRequest("auto")
    if raw in {"cpu", "cuda", "rocm"}:
        return BackendRequest(raw, explicit=True)
    if raw in CUDA_VARIANT_NAMES:
        return BackendRequest("cuda", variant=raw, explicit=True)
    if raw in {"default", "pypi", "mps"}:
        return BackendRequest("default", explicit=True)
    raise ValueError(
        f"{TORCH_BACKEND_ENV}={value!r} is not recognized. Use auto, cpu, cuda, "
        f"{', '.join(CUDA_VARIANT_NAMES)}, rocm, or default."
    )


def parse_rocm_arch_override(value: Optional[str]) -> Tuple[str, ...]:
    archs = []
    for part in re.split(r"[\s,;]+", str(value or "").strip().lower()):
        if not part:
            continue
        if not re.fullmatch(r"gfx[0-9a-f]{3,4}", part):
            raise ValueError(f"{ROCM_ARCH_ENV} has {part!r}; expected a gfx target such as gfx1151.")
        if part not in archs:
            archs.append(part)
    return tuple(archs)


def _driver_tuple(driver: str) -> Tuple[int, int]:
    numbers = [int(piece) for piece in re.findall(r"\d+", str(driver or ""))[:2]]
    return (numbers + [0, 0])[0], (numbers + [0, 0])[1]


def _capability_tuple(capability: str) -> Tuple[int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)", str(capability or "").strip())
    return (int(match.group(1)), int(match.group(2))) if match else (99, 0)


def cuda_variant_for(system: str, adapters: Sequence[GpuAdapter]) -> Tuple[str, str]:
    """Pick the newest CUDA wheel the driver and every GPU support: (variant, problem)."""
    drivers = [adapter.driver_version for adapter in adapters if adapter.driver_version]
    if not drivers:
        return "", "the NVIDIA driver (nvidia-smi) was not found"
    driver = min(drivers, key=_driver_tuple)
    capability = min((_capability_tuple(a.compute_capability) for a in adapters), default=(99, 0))
    for variant, windows_min, linux_min, capability_min in CUDA_VARIANTS:
        minimum = windows_min if system == "Windows" else linux_min
        if _driver_tuple(driver) >= minimum and capability >= capability_min:
            return variant, ""
    if capability < CUDA_VARIANTS[-1][3]:
        return "", "the GPU is older than any CUDA build of current PyTorch supports"
    oldest = CUDA_VARIANTS[-1]
    minimum = oldest[1] if system == "Windows" else oldest[2]
    return "", f"NVIDIA driver {driver} is older than {minimum[0]}.{minimum[1]:02d}, the minimum for CUDA 12"


def _gpu_requirements(torch_version: str, extras: str = "") -> Tuple[str, ...]:
    suffix = f"[{extras}]" if extras else ""
    series = torchvision_series(torch_version)
    torchvision = f"torchvision{suffix}=={series}" if series else f"torchvision{suffix}"
    return (f"torch{suffix}=={torch_version}", torchvision)


def _default_plan(gpus: GpuInventory, reason: str, hint: str = "") -> TorchWheelPlan:
    backend = "mps" if gpus.system == "Darwin" and gpus.machine in {"arm64", "aarch64"} else "cpu"
    return TorchWheelPlan(backend=backend, action="default", reason=reason, hint=hint)


def _cuda_plan(gpus: GpuInventory, request: BackendRequest, torch_version: str) -> TorchWheelPlan:
    nvidia = gpus.of("nvidia")
    if gpus.system == "Windows" and gpus.machine not in {"amd64", "x86_64"}:
        return _default_plan(gpus, "CUDA PyTorch wheels are only published for 64-bit x86 Windows.")
    if request.variant:
        variant = request.variant
        reason = f"{TORCH_BACKEND_ENV}={variant} selects PyTorch's {variant} wheels"
    else:
        variant, problem = cuda_variant_for(gpus.system, nvidia)
        names = ", ".join(adapter.name for adapter in nvidia) or "NVIDIA GPU"
        if not variant:
            if request.explicit:
                variant = CUDA_VARIANTS[-1][0]
                reason = f"{TORCH_BACKEND_ENV}=cuda selects PyTorch's {variant} wheels ({problem})"
            else:
                return _default_plan(
                    gpus,
                    f"{names} found, but {problem}; keeping PyPI's torch wheel (CPU training).",
                    hint=f"Update the NVIDIA driver, or set {TORCH_BACKEND_ENV}=cu126 to try the CUDA 12 build.",
                )
        else:
            driver = min((a.driver_version for a in nvidia), key=_driver_tuple)
            reason = f"{names} (driver {driver}) can use PyTorch's {variant} wheels"
    return TorchWheelPlan(
        backend="cuda",
        action="install",
        reason=reason,
        index_url=f"{PYTORCH_INDEX_ROOT}/{variant}",
        requirements=_gpu_requirements(torch_version),
        variant=variant,
    )


def _rocm_plan(gpus: GpuInventory, request: BackendRequest, arch_override: Sequence[str], torch_version: str) -> TorchWheelPlan:
    amd = gpus.of("amd")
    names = ", ".join(adapter.name for adapter in amd) or "AMD GPU"
    arch_hint = f"Set {ROCM_ARCH_ENV} to the GPU's gfx target (for example gfx1151 for a Radeon 8060S) to choose its wheels."
    if gpus.system == "Windows":
        supported = AMD_ROCM_ARCHS_WINDOWS
        if gpus.machine not in {"amd64", "x86_64"}:
            return _default_plan(gpus, "ROCm PyTorch wheels are only published for 64-bit x86 Windows.")
    else:
        supported = AMD_ROCM_ARCHS_LINUX
        if gpus.machine not in {"x86_64", "amd64"}:
            return _default_plan(gpus, "ROCm PyTorch wheels are only published for x86_64 Linux.")
    archs: List[str] = list(arch_override)
    if not archs:
        archs = sorted({adapter.arch for adapter in amd if adapter.arch})
    known = [arch for arch in archs if arch in supported]
    if known:
        extras = ",".join(f"device-{arch}" for arch in known)
        source = f"{ROCM_ARCH_ENV}" if arch_override else names
        return TorchWheelPlan(
            backend="rocm",
            action="install",
            reason=f"{source} ({', '.join(known)}) can use AMD's ROCm wheels",
            index_url=AMD_ROCM_INDEX,
            requirements=_gpu_requirements(torch_version, extras),
            variant="rocm-" + "+".join(known),
        )
    if gpus.system == "Linux":
        # PyTorch's ROCm build carries kernels for the common GPUs itself.
        return TorchWheelPlan(
            backend="rocm",
            action="install",
            reason=(
                f"{names} has no AMD device wheel for "
                f"{', '.join(archs) if archs else 'an unknown gfx target'}; using PyTorch's {PYTORCH_ROCM_VARIANT} wheels"
            ),
            index_url=f"{PYTORCH_INDEX_ROOT}/{PYTORCH_ROCM_VARIANT}",
            requirements=_gpu_requirements(torch_version),
            variant=PYTORCH_ROCM_VARIANT,
            hint=arch_hint,
        )
    if archs:
        summary = f"{names}: gfx target {', '.join(archs)} has no Windows ROCm wheel"
    elif amd:
        summary = f"{names} found, but its gfx target could not be read"
    else:
        summary = "No AMD GPU was detected"
    return _default_plan(gpus, f"{summary}; keeping PyPI's torch wheel (CPU training).", hint=arch_hint)


def _plan_for_host(gpus: GpuInventory, request: BackendRequest, arch_override: Sequence[str], torch_version: str) -> TorchWheelPlan:
    if request.backend == "default":
        return _default_plan(gpus, f"{TORCH_BACKEND_ENV}=default keeps PyPI's torch wheel.")
    if request.backend == "cpu":
        return _default_plan(gpus, f"{TORCH_BACKEND_ENV}=cpu keeps the CPU wheel.")
    if gpus.system == "Darwin":
        if gpus.machine in {"arm64", "aarch64"}:
            return _default_plan(gpus, "PyPI's macOS arm64 wheel already includes Apple's MPS backend.")
        return _default_plan(gpus, "Intel Macs have no GPU build of current PyTorch.")
    if gpus.system not in {"Windows", "Linux"}:
        return _default_plan(gpus, f"No GPU PyTorch wheels are published for {gpus.system or 'this platform'}.")
    nvidia, amd = gpus.of("nvidia"), gpus.of("amd")
    if request.backend == "cuda" or (request.backend == "auto" and nvidia):
        return _cuda_plan(gpus, request, torch_version)
    if request.backend == "rocm" or (request.backend == "auto" and (amd or arch_override)):
        return _rocm_plan(gpus, request, arch_override, torch_version)
    others = ", ".join(adapter.name for adapter in gpus.adapters if adapter.name) or "none"
    hint = f"Set {TORCH_BACKEND_ENV}=cuda or rocm to install a GPU build anyway."
    if gpus.is_wsl:
        hint = (
            "Under WSL, NVIDIA GPUs need the Windows NVIDIA driver; AMD GPUs need AMD's ROCm-on-WSL "
            f"runtime, then {TORCH_BACKEND_ENV}=rocm and {ROCM_ARCH_ENV}=gfxNNNN."
        )
    return _default_plan(gpus, f"No NVIDIA or AMD GPU was found (adapters: {others}); keeping PyPI's torch wheel (CPU training).", hint=hint)


def select_torch_plan(
    gpus: GpuInventory,
    *,
    installed: Optional[InstalledTorch] = None,
    request: Optional[str] = None,
    rocm_arch: Optional[str] = None,
    torch_version: str = DEFAULT_TORCH_VERSION,
) -> TorchWheelPlan:
    """Decide which torch build to install. Raises ValueError for a bad override.

    An installed CUDA or ROCm build is never replaced by a CPU wheel. It is
    replaced only by a newer build of the same kind, or by the other GPU kind
    when AUTOYOU_TORCH_BACKEND asks for it.
    """
    parsed = parse_backend_request(request)
    arch_override = parse_rocm_arch_override(rocm_arch)
    target = _plan_for_host(gpus, parsed, arch_override, torch_version)
    if installed is None or not installed.is_gpu_build:
        return target
    kind = installed.build
    if target.action == "install" and target.backend == kind and not _version_at_least(installed.version, torch_version):
        return target
    if target.action == "install" and parsed.backend in {"cuda", "rocm"} and parsed.backend != kind:
        return target
    reason = f"Keeping the installed {installed.label}."
    hint = ""
    if parsed.backend in {"cpu", "default"}:
        hint = f"{TORCH_BACKEND_ENV}={parsed.backend} never removes a GPU build; uninstall torch first to switch."
    elif target.action == "install" and target.backend != kind:
        hint = f"This machine also suits {target.backend.upper()}; set {TORCH_BACKEND_ENV}={target.backend} to switch."
    return TorchWheelPlan(backend=kind, action="keep", reason=reason, hint=hint)


# --------------------------------------------------------------------------
# Install helpers shared by bootstrap and the platform build scripts


def read_locked_version(lockfile: Path, package: str = "torch") -> str:
    """The ``package==version`` pin in a pip-compile lockfile, or ""."""
    try:
        text = Path(lockfile).read_text(encoding="utf-8")
    except OSError:
        return ""
    wanted = canonical_name(package)
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;\\]+)", line)
        if match and canonical_name(match.group(1)) == wanted:
            return match.group(2)
    return ""


def apply_torch_pins(lock_specs: Iterable[str], pins: Mapping[str, str]) -> List[str]:
    """Replace lockfile ``name==version`` specs with the installed GPU build's pins."""
    pinned = {canonical_name(name): version for name, version in pins.items()}
    kept = [spec for spec in lock_specs if canonical_name(spec.split("==", 1)[0]) not in pinned]
    return sorted(set(kept) | {f"{name}=={version}" for name, version in pinned.items()})


def gpu_index_items(report: Mapping[str, Any], locked_names: Collection[str]) -> List[Dict[str, Any]]:
    """Pick the ``pip install --report`` items to take from a GPU wheel index.

    torch packages and packages the lockfile does not cover come from the GPU
    index. Shared dependencies the lockfile does cover (numpy, sympy, ...) are
    left for the normal install, which takes them from PyPI at audited hashes.
    """
    locked = {canonical_name(name) for name in locked_names}
    items = []
    for item in report.get("install") or []:
        name = canonical_name(((item or {}).get("metadata") or {}).get("name") or "")
        if name in GPU_TORCH_PACKAGES or name not in locked:
            items.append(item)
    return items


def describe_torch_module(torch_module: Any) -> Dict[str, Any]:
    """Name an imported torch's build for status pages."""
    version = str(getattr(torch_module, "__version__", "") or "")
    try:
        from importlib import metadata

        version = metadata.version("torch") or version
    except Exception:
        pass
    torch_version = getattr(torch_module, "version", None)
    installed = InstalledTorch(
        version=version,
        cuda=str(getattr(torch_version, "cuda", "") or ""),
        hip=str(getattr(torch_version, "hip", "") or ""),
    )
    return {"version": version, "build": installed.build, "label": installed.label}


def bootstrap_command(system: Optional[str] = None) -> str:
    if (system or platform.system()) == "Windows":
        return r"py -3 scripts\bootstrap_autoyou.py --profile training-full --install-only"
    return "python3 scripts/bootstrap_autoyou.py --profile training-full --install-only"


# --------------------------------------------------------------------------
# Command line for the platform build scripts


def _probe_python(python: str) -> Optional[InstalledTorch]:
    result = _run_quietly([python, "-I", "-c", INSTALLED_TORCH_PROBE], timeout=120)
    if result is None or result.returncode != 0:
        return None
    return parse_installed_torch(result.stdout)


def _lock_specs(lockfile: Path) -> List[str]:
    specs = set()
    try:
        lines = Path(lockfile).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(("#", "--")):
            continue
        spec = line.split("--hash", 1)[0].rstrip(" \\").split(";", 1)[0].strip()
        if "==" not in spec:
            continue
        name, _, version = spec.partition("==")
        name = name.split("[", 1)[0].strip()
        if name and version.strip():
            specs.add(f"{name}=={version.strip()}")
    return sorted(specs)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Put the GPU torch build in a build environment for a training-full package.

    Writes ``--constraints-out`` (the lockfile's pins with the installed GPU
    build replacing torch) and ``--gpu-requirements-out`` (that build's exact
    packages, so site-packages pruning keeps its device kernels). Each file is
    removed first and written only when it has content, so a build script can
    test for it. Like the rest of the platform builds, this uses plain pip;
    bootstrap_autoyou.py runs the hash-checked equivalent.
    """
    import argparse

    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--python", default=sys.executable, help="Interpreter of the environment to install into.")
    parser.add_argument("--lockfile", type=Path, default=repo_root / "requirements" / "locked.txt")
    parser.add_argument("--constraints-out", type=Path, help="Constraints for every later pip install.")
    parser.add_argument("--gpu-requirements-out", type=Path, help="Installed GPU packages, for site-packages pruning.")
    parser.add_argument("--plan-only", action="store_true", help="Print the plan as JSON and change nothing.")
    args = parser.parse_args(argv)

    def say(message: str) -> None:
        print(f"[torch] {message}", flush=True)

    installed = _probe_python(args.python)
    torch_version = read_locked_version(args.lockfile) or DEFAULT_TORCH_VERSION
    gpus = detect_gpus()
    try:
        plan = select_torch_plan(
            gpus,
            installed=installed,
            request=os.environ.get(TORCH_BACKEND_ENV),
            rocm_arch=os.environ.get(ROCM_ARCH_ENV),
            torch_version=torch_version,
        )
    except ValueError as exc:
        say(str(exc))
        return 2
    if args.plan_only:
        print(json.dumps({"gpus": gpus.to_dict(), "plan": plan.to_dict()}))
        return 0
    for output in (args.constraints_out, args.gpu_requirements_out):
        if output is not None:
            Path(output).unlink(missing_ok=True)
    say(f"GPUs: {json.dumps(gpus.to_dict()['adapters'])}")
    say(f"{plan.backend.upper()} ({plan.action}): {plan.reason}")
    if plan.hint:
        say(plan.hint)
    if plan.action == "install":
        say(f"Installing from {plan.index_url}: {' '.join(plan.requirements)} (large download)")
        completed = subprocess.run([args.python, "-m", "pip", *plan.pip_args()], check=False)
        if completed.returncode != 0:
            say(f"pip failed ({completed.returncode}) installing the {plan.backend.upper()} build.")
            return completed.returncode or 1
        installed = _probe_python(args.python)
    pins = installed.pins() if installed is not None and installed.is_gpu_build else {}
    if pins:
        say(f"Keeping {installed.label} for every later install.")
    if pins and args.gpu_requirements_out is not None:
        args.gpu_requirements_out.parent.mkdir(parents=True, exist_ok=True)
        args.gpu_requirements_out.write_text(
            "# AUTO-GENERATED by shared/torch_wheel_selection.py: the installed GPU torch build.\n"
            + "".join(f"{name}=={version}\n" for name, version in sorted(pins.items())),
            encoding="utf-8",
        )
    ignore_lock = str(os.environ.get("AUTOYOU_IGNORE_LOCKFILE", "")).strip().lower() in {"1", "true", "yes", "on"}
    specs = [] if ignore_lock else _lock_specs(args.lockfile)
    constraints = apply_torch_pins(specs, pins) if pins else specs
    if constraints and args.constraints_out is not None:
        args.constraints_out.parent.mkdir(parents=True, exist_ok=True)
        args.constraints_out.write_text(
            "# AUTO-GENERATED from requirements/locked.txt, with any installed GPU torch build pinned.\n"
            "# Version constraints only; no hash enforcement or advisory certification. Do not edit.\n"
            + "".join(f"{spec}\n" for spec in constraints),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
