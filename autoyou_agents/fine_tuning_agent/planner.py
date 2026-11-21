# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Training plans, and what to change when one fails.

A plan answers: which executor, which precision, how big a micro-batch, whether
to recompute activations, which attention kernel, how many loader workers, and
which environment variables. It is chosen from the hardware report and the
model's size, never from a fixed table of "profiles".

Estimates here are lower bounds, not promises. Measured peaks on a ROCm unified
memory machine ran two to three times what arithmetic on weights and
activations predicts, because the caching allocator holds blocks across
variable-length batches. The estimate carries a per-family calibration factor
for that, but the real safeguard is the loop around it: a plan is tried, a
failure is classified, and :func:`repair_plan` returns the next plan to try.
That is the same shape as the goal loop used to test AutoYou's agents -
attempt, verify, repair - applied to training.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .hardware import GIB, HardwareReport
from .model_registry import BaseModelSpec, infer_params_b, lookup

# Runner exit codes. The runner and the job supervisor must agree on these.
EXIT_DEPENDENCY = 2
EXIT_DATA = 3
EXIT_CPU_OPT_IN = 4
EXIT_SECURITY = 5
EXIT_VISION_SETUP = 6
EXIT_NON_FINITE_LOSS = 7
EXIT_OUT_OF_MEMORY = 8

FAILURE_KINDS = ("out_of_memory", "non_finite_loss", "killed", "dependency", "config", "unknown")

_BYTES_PER_PARAM = {"bf16": 2.0, "fp16": 2.0, "fp32": 4.0, "nf4": 0.56}

# How far the measured peak runs above the arithmetic, by hardware family.
_CALIBRATION = {
    "strix_halo": 2.2, "amd_radeon": 1.6, "amd_instinct": 1.4, "nvidia_consumer": 1.3,
    "nvidia_datacenter": 1.25, "dgx_spark": 1.4, "jetson": 1.5, "apple_silicon": 1.5,
    "intel_arc": 1.6, "intel_igpu": 1.8, "intel_datacenter": 1.4, "cpu": 1.2,
}

_OOM_RE = re.compile(
    r"out of memory|outofmemoryerror|hiperroroutofmemory|bad_alloc|unable to allocate|"
    r"out_of_device_memory|ur_result_error_out_of_resources|MPS backend out of memory|"
    r"failed to allocate|CUDA error: out of memory",
    re.IGNORECASE,
)
_NON_FINITE_RE = re.compile(r"non[-_ ]finite|\bnan\b.*loss|loss.*\bnan\b|loss.*\binf\b", re.IGNORECASE)
_DEPENDENCY_RE = re.compile(r"DEPENDENCY_ERROR|ModuleNotFoundError|ImportError|No module named", re.IGNORECASE)
_CONFIG_RE = re.compile(r"CPU_TRAINING_OPT_IN_REQUIRED|DATA_ERROR|VISION_SETUP_ERROR|SECURITY_ERROR|VISION_BASE_MODEL_ERROR")
_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\)|^\s*(?:\w+Error|\w+Exception):", re.MULTILINE)
_KILLED_CODES = frozenset({1, -9, -15, 137, 143, 3221225786, 3221225477, -1073741510, -1073741819})


@dataclass
class ModelDims:
    params_b: float
    hidden: Optional[int] = None
    layers: Optional[int] = None
    vocab: Optional[int] = None


@dataclass
class TrainingPlan:
    executor: str                       # "torch" | "mlx"
    backend: str                        # cuda | rocm | xpu | mps | cpu
    family: str
    precision: str                      # bf16 | fp16 | fp32 | nf4
    attn_implementation: str            # eager | sdpa
    batch_size: int
    gradient_accumulation_steps: int
    gradient_checkpointing: bool
    max_seq_length: int
    lora_rank: int
    lora_alpha: int
    dataloader_workers: int
    cpu_threads: int
    learning_rate: float = 2e-4
    max_grad_norm: float = 1.0
    resume: bool = False
    env: Dict[str, str] = field(default_factory=dict)
    estimated_gib: float = 0.0
    budget_gib: float = 0.0
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    history: List[str] = field(default_factory=list)

    @property
    def fits(self) -> bool:
        return self.estimated_gib <= self.budget_gib or self.budget_gib <= 0

    def to_job_config(self) -> Dict[str, Any]:
        """Keys the job configuration and runner understand."""
        return {
            "executor": self.executor,
            "backend": self.backend,
            "precision": self.precision,
            "attn_implementation": self.attn_implementation,
            "batch_size": self.batch_size,
            "gradient_accumulation_steps": self.gradient_accumulation_steps,
            "gradient_checkpointing": self.gradient_checkpointing,
            "max_seq_length": self.max_seq_length,
            "lora_rank": self.lora_rank,
            "lora_alpha": self.lora_alpha,
            "dataloader_workers": self.dataloader_workers,
            "cpu_threads": self.cpu_threads,
            "learning_rate": self.learning_rate,
            "max_grad_norm": self.max_grad_norm,
            "resume": self.resume,
        }

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["fits"] = self.fits
        return data


# ------------------------------------------------------------------ estimate
def estimate_memory_gib(
    dims: ModelDims, *, precision: str, batch_size: int, seq_len: int, lora_rank: int,
    gradient_checkpointing: bool, attn_implementation: str = "sdpa", calibration: float = 1.0,
) -> float:
    """Peak training memory in GiB for a LoRA job. A lower bound; see module doc."""
    params = dims.params_b * 1e9
    layers = dims.layers or 32
    hidden = dims.hidden or max(1024, int((params / (12 * layers)) ** 0.5))
    vocab = dims.vocab or 150_000
    weights = params * _BYTES_PER_PARAM.get(precision, 2.0)
    # Adapter weights, gradients and Adam moments are kept in fp32.
    lora = layers * 7 * 2 * lora_rank * hidden * 16
    tokens = batch_size * seq_len
    act_bytes = 2 if precision != "fp32" else 4
    retained = tokens * layers * hidden * act_bytes * (2 if gradient_checkpointing else 24)
    live_layer = tokens * hidden * 34 * act_bytes
    logits = tokens * vocab * (act_bytes + 4 + 4)
    heads = max(8, hidden // 128)
    eager_attention = batch_size * heads * seq_len * seq_len * act_bytes * 2 if attn_implementation == "eager" else 0
    total = weights + lora + retained + live_layer + logits + eager_attention + 1.5 * GIB
    return round(total / GIB * calibration, 2)


def dims_for(model_id: Optional[str], spec: Optional[BaseModelSpec] = None,
             params_b: Optional[float] = None) -> ModelDims:
    spec = spec or (lookup(model_id) if model_id else None)
    if spec:
        return ModelDims(spec.params_b, spec.hidden, spec.layers, spec.vocab)
    return ModelDims(params_b or infer_params_b(model_id or "") or 7.0)


# ---------------------------------------------------------------------- plan
def _backend_env(report: HardwareReport) -> Dict[str, str]:
    env: Dict[str, str] = {}
    backend, family = report.backend, report.family
    if backend == "cuda":
        # Expandable segments keep NVIDIA's allocator from fragmenting on
        # variable-length batches. They are NOT set for ROCm: on a unified AMD
        # APU the same setting stopped runs at a fixed 30 GiB.
        env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    elif backend == "rocm":
        env["TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL"] = "1"
        if family == "strix_halo":
            env["HSA_ENABLE_SDMA"] = "0"
    elif backend == "mps":
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    env["TOKENIZERS_PARALLELISM"] = "true"
    return env


def plan_training(
    report: HardwareReport,
    model: Optional[BaseModelSpec] = None,
    *,
    model_id: Optional[str] = None,
    params_b: Optional[float] = None,
    vision: bool = False,
    max_seq_length: int = 2048,
    effective_batch: int = 16,
    lora_rank: Optional[int] = None,
    requested: Optional[Mapping[str, Any]] = None,
    prefer_executor: str = "auto",
) -> TrainingPlan:
    """Choose how to train this model on this machine."""
    requested = dict(requested or {})
    spec = model or (lookup(model_id) if model_id else None)
    dims = dims_for(model_id, spec, params_b)
    backend, family = report.backend, report.family
    budget = report.usable_bytes() / GIB
    calibration = _CALIBRATION.get(family, 1.4)
    reasons: List[str] = []
    warnings: List[str] = []

    # --- executor: MLX is the efficient path for text LoRA on Apple silicon.
    executor = "torch"
    if prefer_executor in ("auto", "mlx") and backend in ("mps", "cpu") and report.mlx_available and not vision:
        executor = "mlx"
        reasons.append("MLX LoRA is selected: it trains text adapters on Apple silicon faster and in less "
                       "memory than Torch's MPS backend.")
    elif prefer_executor == "mlx" and not report.mlx_available:
        warnings.append("MLX was requested but mlx and mlx-lm are not installed; using Torch.")

    # --- precision
    device = report.primary
    bf16_ok = bool(device.bf16) if device else False
    if backend == "cpu":
        precision = "fp32"
        warnings.append("No accelerator: LoRA runs in fp32 on the CPU, which is slow. Prefer a model under 2B.")
    elif backend == "cuda" and report.bitsandbytes_available and dims.params_b * 2.0 * 1.35 + 4 > budget:
        # 4-bit is strictly better than a bf16 job that cannot fit at all.
        precision = "nf4"
        reasons.append("Weights in bf16 would not fit the free VRAM, so 4-bit QLoRA is used.")
    else:
        precision = "bf16" if (bf16_ok or backend in ("rocm", "xpu", "mps")) else "fp16"
    precision = str(requested.get("precision") or precision)

    # --- attention kernel
    attn = "sdpa"
    if backend == "rocm" and family == "strix_halo":
        attn = "eager"
        reasons.append("Eager attention: SDPA kernels were unreliable on this AMD APU generation.")
    attn = str(requested.get("attn_implementation") or attn)

    rank = int(requested.get("lora_rank") or lora_rank or (32 if budget >= 24 else 16 if budget >= 12 else 8))
    seq = int(requested.get("max_seq_length") or max_seq_length)
    target_eff = max(1, int(requested.get("effective_batch") or effective_batch))

    # --- batch / checkpointing: prefer the cheapest-compute option that fits.
    chosen = None
    candidates = [(4, False), (2, False), (1, False), (2, True), (1, True)]
    if "batch_size" in requested or "gradient_checkpointing" in requested:
        # An explicit request is honoured; if it will not fit, say so rather
        # than quietly substituting a different configuration.
        batch = int(requested.get("batch_size") or 1)
        ckpt = bool(requested.get("gradient_checkpointing", True))
        estimate = estimate_memory_gib(dims, precision=precision, batch_size=batch, seq_len=seq, lora_rank=rank,
                                       gradient_checkpointing=ckpt, attn_implementation=attn, calibration=calibration)
        chosen = (batch, ckpt, estimate)
        if estimate > budget:
            warnings.append(f"The requested batch {batch}{' with checkpointing' if ckpt else ''} is estimated at "
                            f"{estimate:.1f} GiB against a {budget:.1f} GiB budget.")
        candidates = []
    for batch, ckpt in candidates:
        estimate = estimate_memory_gib(dims, precision=precision, batch_size=batch, seq_len=seq, lora_rank=rank,
                                       gradient_checkpointing=ckpt, attn_implementation=attn, calibration=calibration)
        if estimate <= budget * 0.9:
            chosen = (batch, ckpt, estimate)
            break
    if chosen is None:
        batch, ckpt = 1, True
        while seq > 256:
            estimate = estimate_memory_gib(dims, precision=precision, batch_size=1, seq_len=seq, lora_rank=rank,
                                           gradient_checkpointing=True, attn_implementation=attn, calibration=calibration)
            if estimate <= budget * 0.9:
                break
            seq //= 2
        estimate = estimate_memory_gib(dims, precision=precision, batch_size=1, seq_len=seq, lora_rank=rank,
                                       gradient_checkpointing=True, attn_implementation=attn, calibration=calibration)
        chosen = (1, True, estimate)
        warnings.append(f"Even batch 1 with checkpointing is estimated near the memory budget; sequence length "
                        f"was cut to {seq}. Prefer a smaller base model.")
    batch, ckpt, estimate = chosen
    accumulation = max(1, int(requested.get("gradient_accumulation_steps") or -(-target_eff // batch)))
    reasons.append(f"Micro-batch {batch}{' with activation checkpointing' if ckpt else ''}, accumulation "
                   f"{accumulation}: estimated {estimate:.1f} GiB against a {budget:.1f} GiB budget.")

    # --- loader workers and CPU threads. Windows spawns worker processes and
    # re-imports everything in each, which costs RAM a 32 GB host cannot spare.
    threads = max(1, report.cpu_threads - 2)
    workers = 0 if report.system == "Windows" or backend == "mps" else max(0, min(4, report.cpu_threads // 8))
    workers = int(requested.get("dataloader_workers", workers))

    plan = TrainingPlan(
        executor=executor, backend=("mlx" if executor == "mlx" else backend), family=family, precision=precision,
        attn_implementation=attn, batch_size=batch, gradient_accumulation_steps=accumulation,
        gradient_checkpointing=ckpt, max_seq_length=seq, lora_rank=rank, lora_alpha=rank * 2,
        dataloader_workers=workers, cpu_threads=threads,
        learning_rate=float(requested.get("learning_rate") or 2e-4),
        max_grad_norm=float(requested.get("max_grad_norm") or 1.0),
        env={**_backend_env(report), "OMP_NUM_THREADS": str(threads)},
        estimated_gib=estimate, budget_gib=round(budget, 1), reasons=reasons, warnings=warnings,
    )
    if spec and spec.hybrid_linear_attention and backend != "cuda":
        plan.warnings.append(
            "This base uses gated linear attention. Without the fla/causal-conv1d kernels (CUDA only) Torch "
            "runs a slower reference path; expect lower throughput than a dense model of the same size."
        )
    return plan


# -------------------------------------------------------------------- repair
def classify_failure(return_code: Optional[int], log_tail: str = "") -> str:
    """Name why a job died, so the right repair is tried.

    A trainer that vanishes with no traceback is not a bug in the job. On the
    AMD workstation this was built on, a server-restart routine killed
    python.exe and took two multi-hour runs with it: exit code 1, nothing logged.
    Telling that apart from a real failure is what lets a run resume instead of
    being "repaired" into a different, worse configuration.
    """
    text = str(log_tail or "")
    code = None if return_code is None else int(return_code)
    if code == EXIT_OUT_OF_MEMORY or _OOM_RE.search(text):
        return "out_of_memory"
    if code == EXIT_NON_FINITE_LOSS or _NON_FINITE_RE.search(text):
        return "non_finite_loss"
    if code in (EXIT_DEPENDENCY,) or _DEPENDENCY_RE.search(text):
        return "dependency"
    if code in (EXIT_DATA, EXIT_CPU_OPT_IN, EXIT_SECURITY, EXIT_VISION_SETUP) or _CONFIG_RE.search(text):
        return "config"
    if code in _KILLED_CODES and not _TRACEBACK_RE.search(text):
        return "killed"
    return "unknown"


def repair_plan(plan: TrainingPlan, failure: str, *, attempt: int = 1,
                report: Optional[HardwareReport] = None) -> Optional[TrainingPlan]:
    """The next plan to try after ``failure``, or None when nothing is left to change."""
    if failure == "killed":
        if attempt > 3:
            return None
        return dataclasses.replace(
            plan, resume=True, history=[*plan.history, f"attempt {attempt}: process vanished; resuming the same plan from its newest checkpoint"],
        )

    if failure == "out_of_memory":
        changes: Dict[str, Any] = {}
        note = ""
        if not plan.gradient_checkpointing:
            changes, note = {"gradient_checkpointing": True}, "enabled activation checkpointing"
        elif plan.batch_size > 1:
            half = max(1, plan.batch_size // 2)
            changes = {"batch_size": half, "gradient_accumulation_steps": plan.gradient_accumulation_steps * (plan.batch_size // half)}
            note = f"halved the micro-batch to {half}"
        elif plan.max_seq_length > 512:
            changes, note = {"max_seq_length": max(512, plan.max_seq_length // 2)}, "halved the sequence length"
        elif plan.precision in ("bf16", "fp16") and report is not None and report.bitsandbytes_available:
            changes, note = {"precision": "nf4"}, "switched to 4-bit QLoRA"
        elif plan.lora_rank > 8:
            rank = max(8, plan.lora_rank // 2)
            changes, note = {"lora_rank": rank, "lora_alpha": rank * 2}, f"cut the LoRA rank to {rank}"
        else:
            return None
        # Restart from the newest checkpoint only when the step shape is
        # unchanged; a different micro-batch or length invalidates optimizer
        # state alignment, so those restart clean.
        changes["resume"] = False
        return dataclasses.replace(plan, **changes, history=[*plan.history, f"attempt {attempt}: out of memory -> {note}"])

    if failure == "non_finite_loss":
        if attempt == 1:
            return dataclasses.replace(
                plan, learning_rate=plan.learning_rate * 0.5, max_grad_norm=min(plan.max_grad_norm, 0.3),
                attn_implementation="eager", resume=False,
                history=[*plan.history, f"attempt {attempt}: non-finite loss -> halved the learning rate, tightened clipping, eager attention"],
            )
        if attempt == 2 and plan.precision in ("bf16", "fp16"):
            return dataclasses.replace(
                plan, precision="fp32", learning_rate=plan.learning_rate * 0.5, resume=False,
                history=[*plan.history, f"attempt {attempt}: non-finite loss -> fp32"],
            )
        return None
    return None
