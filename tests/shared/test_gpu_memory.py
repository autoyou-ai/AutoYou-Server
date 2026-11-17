# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""GPU memory detection and the speech-to-text device choice.

No GPU, driver tool or speech model is touched: every source is a stand-in.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import importlib.util
from dataclasses import astuple

import pytest

from shared import gpu_memory
from shared.gpu_memory import GIB, GpuMemory


def _fresh_module():
    """A separate copy of the module, with its real sources (the shared import has them stubbed)."""
    spec = importlib.util.find_spec("shared.gpu_memory")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _no_real_sources(monkeypatch):
    monkeypatch.delenv(gpu_memory.GPU_MEMORY_ENV, raising=False)
    for name in ("_from_nvidia_smi", "_from_amd_sysfs", "_from_apple_unified", "_from_windows_registry"):
        monkeypatch.setattr(gpu_memory, name, lambda: None)
    gpu_memory._detected.clear()
    yield
    gpu_memory._detected.clear()


def test_the_operators_figure_wins_and_the_answer_is_kept(monkeypatch):
    monkeypatch.setenv(gpu_memory.GPU_MEMORY_ENV, "24")

    found = gpu_memory.detect_gpu_memory()

    assert found == GpuMemory(24 * GIB, None, "configured", "env")
    monkeypatch.setenv(gpu_memory.GPU_MEMORY_ENV, "8")
    assert gpu_memory.detect_gpu_memory() is found  # kept for the process
    assert gpu_memory.detect_gpu_memory(refresh=True).total_bytes == 8 * GIB


def test_nvidia_smi_output_is_read_and_the_largest_gpu_counts(monkeypatch):
    module = _fresh_module()
    monkeypatch.setattr(module.shutil, "which", lambda name: "nvidia-smi" if name == "nvidia-smi" else None)
    monkeypatch.setattr(module, "_run", lambda command: "8192, 6000\n24576, 20480\n")

    found = module._from_nvidia_smi()

    assert astuple(found) == (24576 * 1024 ** 2, 20480 * 1024 ** 2, "nvidia", "nvidia-smi", False, None)


def test_amd_sysfs_counters_give_total_and_free(monkeypatch):
    module = _fresh_module()
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.glob, "glob", lambda pattern: ["/sys/class/drm/card1/device/mem_info_vram_total"])
    values = {
        "/sys/class/drm/card1/device/mem_info_vram_total": 16 * GIB,
        "/sys/class/drm/card1/device/mem_info_vram_used": 4 * GIB,
    }
    monkeypatch.setattr(module, "_read_int", lambda path: values.get(path))

    assert astuple(module._from_amd_sysfs()) == (16 * GIB, 12 * GIB, "amd", "amdgpu-sysfs", False, None)


def test_apple_silicon_shares_its_ram_with_the_gpu(monkeypatch):
    module = _fresh_module()
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(module, "_system_ram", lambda: (64 * GIB, 40 * GIB))

    found = module._from_apple_unified()

    assert found.unified and found.vendor == "apple"
    assert found.total_bytes == int(64 * GIB * 0.75) and found.free_bytes == 40 * GIB
    monkeypatch.setattr(module, "_system_ram", lambda: (16 * GIB, 9 * GIB))
    assert module._from_apple_unified().total_bytes == int(16 * GIB * 0.67)


def test_a_gpu_with_under_2_gb_of_its_own_memory_counts_as_none(monkeypatch):
    module = _fresh_module()
    monkeypatch.setattr(module.shutil, "which", lambda name: "nvidia-smi")
    monkeypatch.setattr(module, "_run", lambda command: "1024, 900\n")
    assert module._from_nvidia_smi() is None


def test_no_source_means_no_answer():
    assert gpu_memory.detect_gpu_memory(refresh=True) is None
    assert gpu_memory.free_gpu_memory_bytes() is None


def test_whisper_model_sizes_match_their_names():
    assert gpu_memory.whisper_gpu_bytes("distil-large-v3") == 3 * GIB
    assert gpu_memory.whisper_gpu_bytes("large-v3") == 4 * GIB
    assert gpu_memory.whisper_gpu_bytes("base") == 1 * GIB
    assert gpu_memory.whisper_gpu_bytes("small.en") == 2 * GIB
    assert gpu_memory.whisper_gpu_bytes("something-new") == 2 * GIB


def test_speech_to_text_uses_cuda_only_when_ctranslate2_can_and_there_is_room(monkeypatch):
    # An AMD or Apple machine: PyTorch may say CUDA, CTranslate2 does not.
    monkeypatch.setattr(gpu_memory, "ctranslate2_cuda_available", lambda: False)
    assert gpu_memory.whisper_device("auto", "base") == "cpu"
    assert gpu_memory.whisper_device("cuda", "base") == "cpu"
    assert gpu_memory.whisper_device("cpu", "base") == "cpu"

    monkeypatch.setattr(gpu_memory, "ctranslate2_cuda_available", lambda: True)
    monkeypatch.setattr(gpu_memory, "free_gpu_memory_bytes", lambda: 10 * GIB)
    assert gpu_memory.whisper_device("auto", "large-v3") == "cuda"
    # A chat model fills the GPU: auto moves Whisper to the CPU, an explicit cuda stays.
    monkeypatch.setattr(gpu_memory, "free_gpu_memory_bytes", lambda: int(1.5 * GIB))
    assert gpu_memory.whisper_device("auto", "large-v3") == "cpu"
    assert gpu_memory.whisper_device("auto", "base") == "cuda"
    assert gpu_memory.whisper_device("cuda", "large-v3") == "cuda"
    # Free memory unknown: the GPU is used.
    monkeypatch.setattr(gpu_memory, "free_gpu_memory_bytes", lambda: None)
    assert gpu_memory.whisper_device("auto", "large-v3") == "cuda"


# ── Platforms whose GPU memory is not a plain VRAM figure (owner, 2026-11-05) ──


def test_dgx_spark_shares_its_ram_when_nvidia_smi_reports_no_memory(monkeypatch):
    module = _fresh_module()
    monkeypatch.setattr(module.shutil, "which", lambda name: "nvidia-smi")
    # DGX Spark's GB10 answers "[N/A]" for memory: the GPU uses the 128 GB of system RAM.
    monkeypatch.setattr(module, "_run", lambda command: "[N/A], [N/A], 12.1\n")
    monkeypatch.setattr(module, "_system_ram", lambda: (128 * GIB, 100 * GIB))

    found = module._from_nvidia_smi()

    assert found.unified and found.vendor == "nvidia" and found.source == "nvidia-smi-unified"
    assert found.total_bytes == int(128 * GIB * 0.85) and found.free_bytes == 100 * GIB
    assert found.compute_capability == 12.1


def test_an_older_nvidia_smi_without_compute_cap_is_asked_again(monkeypatch):
    module = _fresh_module()
    monkeypatch.setattr(module.shutil, "which", lambda name: "nvidia-smi")
    answers = {"memory.total,memory.free,compute_cap": "", "memory.total,memory.free": "11264, 10000\n"}
    monkeypatch.setattr(module, "_run", lambda command: answers[command[1].split("=", 1)[1]])

    found = module._from_nvidia_smi()

    assert found.total_bytes == 11264 * 1024 ** 2 and found.compute_capability is None


def test_nvidia_smi_is_found_at_its_wsl_path(monkeypatch):
    module = _fresh_module()
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module.os.path, "isfile", lambda path: path == "/usr/lib/wsl/lib/nvidia-smi")
    seen = []
    monkeypatch.setattr(module, "_run", lambda command: seen.append(command[0]) or "24576, 20000, 8.9\n")

    assert module._from_nvidia_smi().total_bytes == 24576 * 1024 ** 2
    assert seen[0] == "/usr/lib/wsl/lib/nvidia-smi"


def test_a_jetson_without_nvidia_smi_shares_its_ram(monkeypatch):
    module = _fresh_module()
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(module.os.path, "exists", lambda path: path == "/etc/nv_tegra_release")
    monkeypatch.setattr(module, "_system_ram", lambda: (64 * GIB, 50 * GIB))

    found = module._from_jetson()

    assert found.unified and found.source == "jetson" and found.total_bytes == int(64 * GIB * 0.85)
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    assert module._from_jetson() is None


def test_apple_silicon_follows_a_raised_gpu_memory_limit(monkeypatch):
    module = _fresh_module()
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(module, "_system_ram", lambda: (64 * GIB, 40 * GIB))
    monkeypatch.setattr(module, "_run", lambda command: "57344\n")

    found = module._from_apple_unified()

    assert found.total_bytes == 56 * GIB and found.source == "iogpu.wired_limit_mb"
    monkeypatch.setattr(module, "_run", lambda command: "0\n")  # the default limit
    assert module._from_apple_unified().total_bytes == int(64 * GIB * 0.75)


def test_windows_on_arm_counts_as_a_cpu_machine(monkeypatch):
    module = _fresh_module()
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module.platform, "machine", lambda: "ARM64")
    assert module._from_windows_registry() is None


def test_an_amd_gpu_counts_from_1_gb_of_vram_like_ollama(monkeypatch):
    module = _fresh_module()
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.glob, "glob", lambda pattern: ["/sys/class/drm/card0/device/mem_info_vram_total"])
    sizes = {"/sys/class/drm/card0/device/mem_info_vram_total": int(1.5 * GIB)}
    monkeypatch.setattr(module, "_read_int", lambda path: sizes.get(path))
    assert module._from_amd_sysfs().total_bytes == int(1.5 * GIB)
    sizes["/sys/class/drm/card0/device/mem_info_vram_total"] = 512 * 1024 ** 2  # an iGPU carve-out
    assert module._from_amd_sysfs() is None


def test_on_unified_memory_whisper_is_not_moved_to_the_cpu_for_room(monkeypatch):
    monkeypatch.setattr(gpu_memory, "ctranslate2_cuda_available", lambda: True)
    monkeypatch.setattr(gpu_memory, "free_gpu_memory_bytes", lambda: int(0.5 * GIB))
    monkeypatch.setattr(
        gpu_memory, "detect_gpu_memory",
        lambda refresh=False: GpuMemory(108 * GIB, int(0.5 * GIB), "nvidia", "nvidia-smi-unified", unified=True),
    )
    assert gpu_memory.whisper_device("auto", "large-v3") == "cuda"
