# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Hardware discovery, licence policy and training plans for the Fine Tuning agent.

Every host family is exercised from a fake torch module, so none of these tests
needs a GPU, and none touches the operator's workspace.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from autoyou_agents.fine_tuning_agent import hardware, model_registry, planner

GIB = hardware.GIB


def _fake_torch(*, cuda=None, hip=None, xpu=None, mps=None, version="2.13.0"):
    """A torch stand-in. ``cuda`` is a list of device dicts, as is ``xpu``."""

    def props(entry):
        return SimpleNamespace(
            name=entry["name"], total_memory=int(entry["total_gib"] * GIB),
            major=entry.get("major", 0), minor=entry.get("minor", 0),
            gcnArchName=entry.get("arch", ""), architecture=entry.get("architecture", ""),
        )

    cuda_devices = cuda or []
    cuda_ns = SimpleNamespace(
        is_available=lambda: bool(cuda_devices),
        device_count=lambda: len(cuda_devices),
        get_device_properties=lambda i: props(cuda_devices[i]),
        mem_get_info=lambda i=0: (int(cuda_devices[i].get("free_gib", cuda_devices[i]["total_gib"]) * GIB),
                                  int(cuda_devices[i]["total_gib"] * GIB)),
        is_bf16_supported=lambda: cuda_devices[0].get("bf16", True),
    )
    xpu_devices = xpu or []
    xpu_ns = SimpleNamespace(
        is_available=lambda: bool(xpu_devices),
        device_count=lambda: len(xpu_devices),
        get_device_properties=lambda i: props(xpu_devices[i]),
        mem_get_info=lambda i=0: (int(xpu_devices[i]["total_gib"] * GIB), int(xpu_devices[i]["total_gib"] * GIB)),
        is_bf16_supported=lambda: True,
    )
    mps_ns = SimpleNamespace(is_available=lambda: mps is not None)
    return SimpleNamespace(
        __version__=version,
        cuda=cuda_ns,
        xpu=xpu_ns,
        version=SimpleNamespace(hip=hip, cuda=None if hip else "12.8"),
        backends=SimpleNamespace(mps=mps_ns),
        mps=SimpleNamespace(recommended_max_memory=lambda: int((mps or {}).get("recommended_gib", 0) * GIB)),
    )


def _probe(torch_module, *, system="Linux", machine="x86_64", ram_gib=64, modules=(), threads=16):
    return hardware.probe_hardware(
        torch_module, system=system, machine=machine, cpu_threads=threads,
        ram_probe=lambda: (int(ram_gib * GIB), int(ram_gib * 0.8 * GIB)),
        module_probe=lambda name: name in modules,
        run=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""),
    )


# ---------------------------------------------------------------- families
@pytest.mark.parametrize(
    "backend,name,arch,capability,expected",
    [
        ("cuda", "NVIDIA GB10", "sm_121", (12, 1), "dgx_spark"),
        ("cuda", "Orin (nvgpu)", "sm_87", (8, 7), "jetson"),
        ("cuda", "NVIDIA H100 80GB HBM3", "sm_90", (9, 0), "nvidia_datacenter"),
        ("cuda", "NVIDIA GeForce RTX 4090", "sm_89", (8, 9), "nvidia_consumer"),
        ("rocm", "AMD Radeon(TM) 8060S Graphics", "gfx1151", (11, 5), "strix_halo"),
        ("rocm", "AMD Instinct MI300X", "gfx942", (9, 4), "amd_instinct"),
        ("rocm", "AMD Radeon RX 7900 XTX", "gfx1100", (11, 0), "amd_radeon"),
        ("xpu", "Intel(R) Arc(TM) B580 Graphics", None, None, "intel_arc"),
        ("xpu", "Intel(R) Arc(TM) A770 Graphics", None, None, "intel_arc"),
        ("xpu", "Intel(R) Arc(TM) Graphics", None, None, "intel_igpu"),
        ("xpu", "Intel(R) Data Center GPU Max 1550", None, None, "intel_datacenter"),
        ("mps", "Apple M3 Max", None, None, "apple_silicon"),
        ("cpu", "", None, None, "cpu"),
    ],
)
def test_family_classification(backend, name, arch, capability, expected):
    assert hardware.classify_family(backend, name, arch, capability) == expected


def test_eager_attention_is_preferred_only_on_the_amd_apu_generation():
    """SDPA raises hipErrorInvalidValue on these APUs; a job with no plan must still avoid it."""
    assert hardware.prefers_eager_attention("rocm", "AMD Radeon(TM) 8060S Graphics", "gfx1151:sramecc-:xnack-")
    assert hardware.prefers_eager_attention("rocm", "", "gfx1150")
    assert not hardware.prefers_eager_attention("rocm", "AMD Radeon RX 7900 XTX", "gfx1100")
    assert not hardware.prefers_eager_attention("rocm", "AMD Instinct MI300X", "gfx942")
    assert not hardware.prefers_eager_attention("cuda", "NVIDIA GeForce RTX 4090", None)
    assert not hardware.prefers_eager_attention("cpu")


def test_unified_memory_is_a_property_of_the_family():
    assert hardware.HardwareReport("Linux", "aarch64", 20, 128 * GIB, None, "cuda", (), "dgx_spark").unified_memory
    assert hardware.HardwareReport("Darwin", "arm64", 12, 64 * GIB, None, "mps", (), "apple_silicon").unified_memory
    assert not hardware.HardwareReport("Linux", "x86_64", 32, 64 * GIB, None, "cuda", (), "nvidia_consumer").unified_memory


# ------------------------------------------------------------------ probes
def test_probe_nvidia_discrete_card():
    report = _probe(_fake_torch(cuda=[{"name": "NVIDIA GeForce RTX 4090", "total_gib": 24, "free_gib": 22, "major": 8, "minor": 9}]),
                    modules={"bitsandbytes"})
    assert (report.backend, report.family) == ("cuda", "nvidia_consumer")
    assert report.bitsandbytes_available
    assert report.primary.arch == "sm_89"
    # discrete cards plan from the free figure with a margin
    assert report.usable_bytes() == int(22 * GIB * 0.92)


def test_probe_rocm_strix_halo_uses_a_fraction_of_the_reported_total():
    report = _probe(
        _fake_torch(cuda=[{"name": "AMD Radeon(TM) 8060S Graphics", "total_gib": 107.9, "arch": "gfx1151", "major": 11, "minor": 5}], hip="7.14.60850"),
        system="Windows", ram_gib=32,
    )
    assert (report.backend, report.family) == ("rocm", "strix_halo")
    assert report.unified_memory
    assert not report.bitsandbytes_available  # bitsandbytes has no gfx115x build
    assert report.usable_bytes() == int(report.primary.total_bytes * 0.75)


def test_probe_dgx_spark_plans_from_free_memory():
    report = _probe(_fake_torch(cuda=[{"name": "NVIDIA GB10", "total_gib": 121, "free_gib": 100, "major": 12, "minor": 1}]),
                    machine="aarch64", ram_gib=128)
    assert report.family == "dgx_spark" and report.unified_memory
    assert report.usable_bytes() == int(100 * GIB * 0.85)


def test_probe_intel_xpu_card_and_igpu():
    arc = _probe(_fake_torch(xpu=[{"name": "Intel(R) Arc(TM) B580 Graphics", "total_gib": 12}]))
    assert (arc.backend, arc.family) == ("xpu", "intel_arc")
    igpu = _probe(_fake_torch(xpu=[{"name": "Intel(R) Arc(TM) Graphics", "total_gib": 16}]), ram_gib=32)
    assert igpu.family == "intel_igpu" and igpu.unified_memory
    assert igpu.usable_bytes() == int(16 * GIB * 0.5)


def test_probe_apple_silicon_caps_at_the_metal_working_set():
    report = _probe(_fake_torch(mps={"recommended_gib": 40}), system="Darwin", machine="arm64", ram_gib=64,
                    modules={"mlx", "mlx_lm"})
    assert (report.backend, report.family) == ("mps", "apple_silicon")
    assert report.mlx_available
    assert report.primary.total_bytes == int(40 * GIB)
    assert report.usable_bytes() == int(40 * GIB * 0.9)


def test_probe_apple_silicon_without_recommended_max_uses_two_thirds_of_ram():
    report = _probe(_fake_torch(mps={}), system="Darwin", machine="arm64", ram_gib=48)
    assert report.primary.total_bytes == int(48 * GIB * 0.66)


def test_probe_cpu_only_and_a_broken_torch_never_raise():
    cpu = _probe(_fake_torch())
    assert (cpu.backend, cpu.family) == ("cpu", "cpu")
    assert any("no CUDA" in note for note in cpu.notes)
    assert _probe(None).backend == "cpu"

    class Broken:
        __version__ = "x"

        class cuda:  # noqa: N801
            @staticmethod
            def is_available():
                raise RuntimeError("driver exploded")

    report = _probe(Broken())
    assert report.backend == "cpu" and any("driver exploded" in note for note in report.notes)


def test_memory_override_wins(monkeypatch):
    report = _probe(_fake_torch(cuda=[{"name": "NVIDIA GeForce RTX 4090", "total_gib": 24}]))
    assert report.usable_bytes({hardware.MEMORY_OVERRIDE_ENV: "10"}) == 10 * GIB
    assert report.usable_bytes({hardware.MEMORY_OVERRIDE_ENV: "nonsense"}) == report.usable_bytes({})


def test_report_serialises_without_torch_objects():
    payload = _probe(_fake_torch(cuda=[{"name": "NVIDIA GeForce RTX 4090", "total_gib": 24}])).to_dict()
    assert payload["devices"][0]["total_gib"] == 24.0
    assert payload["usable_gib"] > 0
    import json

    json.dumps(payload)


# ---------------------------------------------------------------- licences
@pytest.mark.parametrize(
    "license_id,tier",
    [("apache-2.0", "permissive"), ("MIT", "permissive"), ("qwen-research", "non_commercial"),
     ("cc-by-nc-4.0", "non_commercial"), ("cc-by-nc-sa-4.0", "non_commercial"), ("llama3.1", "custom"),
     ("gemma", "custom"), ("other", "custom"), (None, "unknown"), ("brand-new-licence", "unknown")],
)
def test_license_tiers(license_id, tier):
    assert model_registry.license_tier(license_id) == tier


def test_the_qwen_research_bases_are_refused_for_distribution():
    for model_id in ("Qwen/Qwen2.5-3B-Instruct", "Qwen/Qwen2.5-VL-3B-Instruct"):
        status = model_registry.license_status(model_id)
        assert status["tier"] == "non_commercial" and status["ok_to_distribute"] is False
    assert model_registry.license_status("Qwen/Qwen3.5-9B")["ok_to_distribute"] is True


def test_unknown_models_are_not_looked_up_over_the_network_unless_asked():
    def boom(_model_id):
        raise AssertionError("the network must not be used silently")

    status = model_registry.license_status("someone/mystery-7b", fetch=boom)
    assert status["tier"] == "unknown" and status["ok_to_distribute"] is False
    fetched = model_registry.license_status("someone/mystery-7b", allow_network=True, fetch=lambda _m: "mit")
    assert fetched["tier"] == "permissive" and fetched["source"] == "model card"
    failed = model_registry.license_status("someone/mystery-7b", allow_network=True,
                                           fetch=lambda _m: (_ for _ in ()).throw(OSError("offline")))
    assert failed["tier"] == "unknown" and "offline" in failed["message"]


def test_catalog_entries_are_internally_consistent():
    ids = [spec.id for spec in model_registry.CATALOG]
    assert len(ids) == len(set(ids))
    for spec in model_registry.CATALOG:
        assert spec.params_b > 0 and spec.min_train_gib_bf16 > spec.min_train_gib_4bit > 0
        if spec.ollama_tag:
            assert ":" in spec.ollama_tag
    assert model_registry.lookup("qwen/qwen3.5-9b").id == "Qwen/Qwen3.5-9B"
    assert model_registry.infer_params_b("acme/widget-13B-chat") == 13.0
    assert model_registry.infer_params_b("Qwen/Qwen3.5-4B") == 4.7


@pytest.mark.parametrize(
    "usable_gib,expected",
    [(2, "Qwen/Qwen3.5-0.8B"), (9, "Qwen/Qwen3.5-2B"), (20, "Qwen/Qwen3.5-4B"), (60, "Qwen/Qwen3.5-9B")],
)
def test_recommendation_follows_memory_and_is_always_permissive(usable_gib, expected):
    report = _probe(_fake_torch(cuda=[{"name": "NVIDIA GeForce RTX 4090", "total_gib": usable_gib / 0.92 / 0.92}]))
    spec = model_registry.recommend_base(report)
    assert spec.id == expected
    assert model_registry.license_tier(spec.license) == "permissive"


def test_cpu_hosts_get_the_smallest_base():
    assert model_registry.recommend_base(_probe(_fake_torch())).id == "Qwen/Qwen3.5-0.8B"


def test_qlora_lowers_the_bar_on_nvidia_with_bitsandbytes():
    cards = [{"name": "NVIDIA GeForce RTX 4070", "total_gib": 12, "free_gib": 12}]
    plain = model_registry.recommend_base(_probe(_fake_torch(cuda=cards)))
    quantized = model_registry.recommend_base(_probe(_fake_torch(cuda=cards), modules={"bitsandbytes"}))
    assert quantized.params_b >= plain.params_b


# ------------------------------------------------------------------- plans
def _strix():
    return _probe(
        _fake_torch(cuda=[{"name": "AMD Radeon(TM) 8060S Graphics", "total_gib": 107.9, "arch": "gfx1151", "major": 11, "minor": 5}], hip="7.14"),
        system="Windows", ram_gib=32, threads=32,
    )


def test_memory_estimate_moves_the_right_way():
    dims = planner.ModelDims(9.7, 4096, 32, 248320)
    base = dict(precision="bf16", lora_rank=32)
    small = planner.estimate_memory_gib(dims, batch_size=1, seq_len=1024, gradient_checkpointing=True, **base)
    bigger_batch = planner.estimate_memory_gib(dims, batch_size=4, seq_len=1024, gradient_checkpointing=True, **base)
    longer = planner.estimate_memory_gib(dims, batch_size=1, seq_len=4096, gradient_checkpointing=True, **base)
    no_ckpt = planner.estimate_memory_gib(dims, batch_size=1, seq_len=1024, gradient_checkpointing=False, **base)
    quantized = planner.estimate_memory_gib(dims, batch_size=1, seq_len=1024, gradient_checkpointing=True,
                                            precision="nf4", lora_rank=32)
    assert bigger_batch > small and longer > small and no_ckpt > small and quantized < small
    assert small > 9.7 * 2 * 0.9  # weights alone


def test_strix_halo_plan_keeps_the_allocator_and_attention_that_work_there():
    plan = planner.plan_training(_strix(), model_registry.lookup("Qwen/Qwen3.5-9B"))
    assert plan.executor == "torch" and plan.backend == "rocm" and plan.precision == "bf16"
    assert plan.attn_implementation == "eager"
    assert plan.fits
    assert plan.env["TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL"] == "1" and plan.env["HSA_ENABLE_SDMA"] == "0"
    assert "PYTORCH_CUDA_ALLOC_CONF" not in plan.env and "PYTORCH_HIP_ALLOC_CONF" not in plan.env
    assert plan.dataloader_workers == 0  # Windows spawns loader processes
    assert plan.batch_size * plan.gradient_accumulation_steps >= 16
    assert any("linear attention" in w for w in plan.warnings)  # hybrid base off CUDA


def test_nvidia_plan_uses_expandable_segments_and_sdpa():
    report = _probe(_fake_torch(cuda=[{"name": "NVIDIA GeForce RTX 4090", "total_gib": 24, "major": 8, "minor": 9}]))
    plan = planner.plan_training(report, model_registry.lookup("Qwen/Qwen3.5-4B"))
    assert plan.attn_implementation == "sdpa" and plan.env["PYTORCH_CUDA_ALLOC_CONF"] == "expandable_segments:True"
    assert plan.dataloader_workers >= 0


def test_small_cuda_card_with_bitsandbytes_falls_back_to_4bit():
    report = _probe(_fake_torch(cuda=[{"name": "NVIDIA GeForce RTX 4060", "total_gib": 8, "major": 8, "minor": 9}]),
                    modules={"bitsandbytes"})
    plan = planner.plan_training(report, model_registry.lookup("Qwen/Qwen3.5-9B"))
    assert plan.precision == "nf4"
    assert any("QLoRA" in reason for reason in plan.reasons)


def test_cpu_plan_is_fp32_with_a_warning():
    plan = planner.plan_training(_probe(_fake_torch()), model_registry.lookup("Qwen/Qwen3.5-0.8B"))
    assert plan.backend == "cpu" and plan.precision == "fp32" and plan.warnings


def test_apple_silicon_prefers_mlx_for_text_but_not_vision():
    report = _probe(_fake_torch(mps={"recommended_gib": 40}), system="Darwin", machine="arm64", ram_gib=64,
                    modules={"mlx", "mlx_lm"})
    text = planner.plan_training(report, model_registry.lookup("Qwen/Qwen3.5-4B"))
    vision = planner.plan_training(report, model_registry.lookup("Qwen/Qwen3.5-4B"), vision=True)
    assert text.executor == "mlx" and text.backend == "mlx"
    assert vision.executor == "torch" and vision.backend == "mps"
    no_mlx = planner.plan_training(_probe(_fake_torch(mps={"recommended_gib": 40}), system="Darwin", machine="arm64"),
                                   model_registry.lookup("Qwen/Qwen3.5-4B"))
    assert no_mlx.executor == "torch"


def test_a_model_too_big_for_the_budget_gets_batch_one_and_a_warning():
    report = _probe(_fake_torch(cuda=[{"name": "NVIDIA GeForce RTX 3060", "total_gib": 12, "major": 8, "minor": 6}]))
    plan = planner.plan_training(report, model_registry.lookup("Qwen/Qwen3.5-27B"))
    assert plan.batch_size == 1 and plan.gradient_checkpointing and plan.warnings


def test_explicit_requests_override_the_plan():
    plan = planner.plan_training(_strix(), model_registry.lookup("Qwen/Qwen3.5-9B"),
                                 requested={"batch_size": 2, "gradient_checkpointing": False, "lora_rank": 64,
                                            "max_seq_length": 1024, "precision": "fp16"})
    assert (plan.batch_size, plan.gradient_checkpointing, plan.lora_rank, plan.max_seq_length, plan.precision) \
        == (2, False, 64, 1024, "fp16")


def test_plan_serialises_to_job_config():
    plan = planner.plan_training(_strix(), model_registry.lookup("Qwen/Qwen3.5-9B"))
    config = plan.to_job_config()
    assert {"batch_size", "gradient_accumulation_steps", "lora_rank", "lora_alpha", "precision"} <= set(config)
    import json

    json.dumps(plan.to_dict())


# ----------------------------------------------------------------- failures
@pytest.mark.parametrize(
    "code,log,kind",
    [
        (planner.EXIT_OUT_OF_MEMORY, "", "out_of_memory"),
        (1, "torch.OutOfMemoryError: HIP out of memory. Tried to allocate 4.00 GiB", "out_of_memory"),
        (1, "RuntimeError: MPS backend out of memory", "out_of_memory"),
        (planner.EXIT_NON_FINITE_LOSS, "", "non_finite_loss"),
        (1, "[train] NON-FINITE loss=nan at step 12", "non_finite_loss"),
        (planner.EXIT_DEPENDENCY, "DEPENDENCY_ERROR No module named 'peft'", "dependency"),
        (4, "CPU_TRAINING_OPT_IN_REQUIRED", "config"),
        (1, "step 10 loss 1.2\nstep 20 loss 1.1\n", "killed"),            # vanished: no traceback
        (-9, "", "killed"),
        (3221225786, "", "killed"),                                       # Windows CTRL_C_EVENT / taskkill
        (1, "OSError: We couldn't connect to 'https://huggingface.co' to load the files, and couldn't find them in the cached files.", "model_unavailable"),
        (1, "huggingface_hub.errors.LocalEntryNotFoundError: Cannot find the requested files in the disk cache and outgoing traffic has been disabled.", "model_unavailable"),
        (1, "Traceback (most recent call last):\n  File x\nValueError: bad", "unknown"),
        (None, "", "unknown"),
        # Host resource exhaustion, seen live on Windows while other models were resident.
        (1, "Traceback (most recent call last):\nOSError: Insufficient system resources exist to complete the requested service. (os error 1450)", "system_resources"),
        (1, "OSError: [WinError 1455] The paging file is too small for this operation to complete", "system_resources"),
        (1, "RuntimeError: [enforce fail at alloc_cpu.cpp:117] data. DefaultCPUAllocator: not enough memory: you tried to allocate 3221225472 bytes.", "system_resources"),
        # ...and not for a real GPU out-of-memory, which the runner reports by its own exit code.
        (planner.EXIT_OUT_OF_MEMORY, "OSError: (os error 1450)", "out_of_memory"),
        (1, "torch.OutOfMemoryError: HIP out of memory. Tried to allocate 4.00 GiB", "out_of_memory"),
    ],
)
def test_failure_classification(code, log, kind):
    assert planner.classify_failure(code, log) == kind


def test_running_out_of_system_resources_retries_the_same_plan_twice_then_hands_over_with_a_hint():
    plan = planner.plan_training(_strix(), model_registry.lookup("Qwen/Qwen3.5-9B"))
    first = planner.repair_plan(plan, "system_resources", attempt=1)
    assert first.resume is True and first.history and "system resources" in first.history[-1]
    # Nothing but the resume flag changed: shrinking the batch cannot help a host that is out of commit.
    assert {**first.to_job_config(), "resume": False} == {**plan.to_job_config(), "resume": False}
    assert planner.repair_plan(first, "system_resources", attempt=2) is not None
    assert planner.repair_plan(first, "system_resources", attempt=3) is None
    assert "ollama stop" in planner.FAILURE_HINTS["system_resources"]


def test_a_missing_base_model_is_not_repaired_by_changing_the_plan_and_comes_with_a_hint():
    plan = planner.plan_training(_strix(), model_registry.lookup("Qwen/Qwen3.5-4B"))
    assert planner.repair_plan(plan, "model_unavailable") is None
    assert "HF_HOME" in planner.FAILURE_HINTS["model_unavailable"]


def test_oom_repair_walks_the_ladder_to_the_end():
    plan = planner.plan_training(_strix(), model_registry.lookup("Qwen/Qwen3.5-9B"),
                                 requested={"batch_size": 4, "gradient_checkpointing": False, "lora_rank": 32})
    seen = []
    for attempt in range(1, 12):
        nxt = planner.repair_plan(plan, "out_of_memory", attempt=attempt)
        if nxt is None:
            break
        seen.append((nxt.batch_size, nxt.gradient_checkpointing, nxt.max_seq_length, nxt.lora_rank))
        # the effective batch is preserved while the micro-batch shrinks
        assert nxt.batch_size * nxt.gradient_accumulation_steps >= plan.batch_size * plan.gradient_accumulation_steps or nxt.batch_size == plan.batch_size
        assert nxt.resume is False and nxt.history
        plan = nxt
    else:
        pytest.fail("the ladder never ended")
    assert seen[0][1] is True                     # checkpointing first
    assert seen[-1][0] == 1 and seen[-1][3] <= 8   # ends at batch 1, rank 8
    assert seen[-1][2] < 2048                      # and a shorter sequence


def test_oom_repair_reaches_4bit_on_nvidia_with_bitsandbytes():
    report = _probe(_fake_torch(cuda=[{"name": "NVIDIA GeForce RTX 4090", "total_gib": 24, "major": 8, "minor": 9}]),
                    modules={"bitsandbytes"})
    plan = planner.plan_training(report, model_registry.lookup("Qwen/Qwen3.5-4B"),
                                 requested={"batch_size": 1, "gradient_checkpointing": True, "max_seq_length": 512})
    assert plan.precision == "bf16"
    assert planner.repair_plan(plan, "out_of_memory", report=report).precision == "nf4"


def test_non_finite_loss_repair_is_two_steps_then_gives_up():
    plan = planner.plan_training(_strix(), model_registry.lookup("Qwen/Qwen3.5-9B"))
    first = planner.repair_plan(plan, "non_finite_loss", attempt=1)
    assert first.learning_rate == plan.learning_rate * 0.5 and first.max_grad_norm <= 0.3 and first.attn_implementation == "eager"
    second = planner.repair_plan(first, "non_finite_loss", attempt=2)
    assert second.precision == "fp32" and second.learning_rate == first.learning_rate * 0.5
    assert planner.repair_plan(second, "non_finite_loss", attempt=3) is None


def test_a_vanished_process_resumes_the_same_plan_and_changes_nothing_else():
    plan = planner.plan_training(_strix(), model_registry.lookup("Qwen/Qwen3.5-9B"))
    nxt = planner.repair_plan(plan, "killed", attempt=1)
    assert nxt.resume is True
    assert {**nxt.to_job_config(), "resume": False} == {**plan.to_job_config(), "resume": False}
    assert planner.repair_plan(plan, "killed", attempt=4) is None


@pytest.mark.parametrize("kind", ["dependency", "config", "unknown"])
def test_unrepairable_failures_stop_the_loop(kind):
    plan = planner.plan_training(_strix(), model_registry.lookup("Qwen/Qwen3.5-9B"))
    assert planner.repair_plan(plan, kind) is None
