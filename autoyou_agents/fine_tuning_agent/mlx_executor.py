# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""MLX LoRA training for Apple silicon, joined to the Torch-to-Ollama install path.

MLX was only ever *detected*: the capability report said "available" and then
declared its adapter format incompatible with the installer. This module closes
that gap in three steps that need no Apple hardware to test:

1. :func:`export_mlx_dataset` writes the prepared chat rows in the layout
   ``mlx_lm lora`` reads.
2. :func:`build_mlx_lora_command` drives ``mlx_lm lora``; its log lines are
   translated to the shared progress protocol by :mod:`.progress`.
3. :func:`mlx_adapter_to_peft` converts the finished adapter to the Hugging
   Face PEFT layout, so the existing GGUF-adapter conversion and Ollama
   ``ADAPTER`` install treat an MLX job exactly like a Torch one.

The conversion is a transpose, not an approximation. MLX stores ``lora_a`` as
``(in, r)`` and ``lora_b`` as ``(r, out)`` and computes ``scale * (x @ a) @ b``;
PEFT stores ``lora_A`` as ``(r, in)`` and ``lora_B`` as ``(out, r)`` and computes
``(alpha / r) * B(A x)``. With ``A = a.T``, ``B = b.T`` and ``alpha = scale * r``
the two are the same function.

Honest limit: this path has not run on real Apple silicon in this repository's
test machine (a Windows/AMD workstation). The command construction, the data
layout, the log parsing and the weight conversion are unit-tested; the first run
on a Mac is the integration test.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import importlib.util
import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .progress import format_progress, from_mlx_log_line

#: ``mlx_lm lora`` expects this adapter weight file name.
MLX_ADAPTER_FILE = "adapters.safetensors"

_LORA_KEY_RE = re.compile(r"^(?P<module>.+)\.lora_(?P<which>[ab])$")


def mlx_status() -> Dict[str, Any]:
    native = platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}
    installed = bool(importlib.util.find_spec("mlx") and importlib.util.find_spec("mlx_lm"))
    return {
        "native_apple_silicon": native, "installed": installed, "available": native and installed,
        "install_hint": 'pip install "mlx-lm[train]"' if native and not installed else None,
    }


# ------------------------------------------------------------------- data
def export_mlx_dataset(rows: Iterable[Mapping[str, Any]], out_dir: Path, *, valid_fraction: float = 0.05,
                       valid_rows: Optional[Iterable[Mapping[str, Any]]] = None) -> Dict[str, int]:
    """Write ``train.jsonl`` / ``valid.jsonl`` in MLX's chat layout.

    Image rows are skipped: MLX text LoRA cannot train them, and silently
    training a vision dataset on its text alone would produce a model that
    never saw what it was meant to read.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def clean(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        if row.get("images"):
            return None
        messages = [
            {"role": str(m.get("role")), "content": str(m.get("content") or "")}
            for m in (row.get("messages") or [])
            if str(m.get("role")) in {"system", "user", "assistant"} and str(m.get("content") or "").strip()
        ]
        if len(messages) < 2 or messages[-1]["role"] != "assistant":
            return None
        return {"messages": messages}

    train = [c for c in (clean(r) for r in rows) if c]
    skipped = 0
    if valid_rows is not None:
        valid = [c for c in (clean(r) for r in valid_rows) if c]
    else:
        keep = max(1, int(len(train) * valid_fraction)) if len(train) > 20 else 0
        valid, train = train[:keep], train[keep:]
    if not valid and train:
        valid = train[:1]  # mlx_lm refuses to train without a validation file
    for name, items in (("train.jsonl", train), ("valid.jsonl", valid)):
        with open(out_dir / name, "w", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return {"train": len(train), "valid": len(valid), "skipped_image_rows": skipped}


# ---------------------------------------------------------------- command
def build_mlx_lora_command(
    *, python: str, model: str, data_dir: Path, adapter_dir: Path, config_path: Path, iters: int,
    batch_size: int = 1, learning_rate: float = 2e-4, num_layers: int = 16, max_seq_length: int = 2048,
    grad_checkpoint: bool = True, save_every: int = 100, steps_per_report: int = 5,
    steps_per_eval: int = 50, resume_adapter_file: Optional[Path] = None, mask_prompt: bool = True,
) -> List[str]:
    """The argv for ``mlx_lm lora``. Rank, scale and dropout travel in the config
    file because the CLI has no flags for them."""
    command = [
        python, "-m", "mlx_lm", "lora", "--model", model, "--train", "--data", str(data_dir),
        "--adapter-path", str(adapter_dir), "--config", str(config_path),
        "--iters", str(int(iters)), "--batch-size", str(int(batch_size)),
        "--learning-rate", repr(float(learning_rate)), "--num-layers", str(int(num_layers)),
        "--max-seq-length", str(int(max_seq_length)), "--save-every", str(int(save_every)),
        "--steps-per-report", str(int(steps_per_report)), "--steps-per-eval", str(int(steps_per_eval)),
        "--fine-tune-type", "lora",
    ]
    if grad_checkpoint:
        command.append("--grad-checkpoint")
    if mask_prompt:
        command.append("--mask-prompt")
    if resume_adapter_file:
        command.extend(["--resume-adapter-file", str(resume_adapter_file)])
    return command


def write_lora_config(path: Path, *, rank: int, alpha: int, dropout: float = 0.0,
                      keys: Optional[Sequence[str]] = None) -> Path:
    """``mlx_lm`` reads YAML; JSON is valid YAML. ``scale`` is ``alpha / rank``."""
    parameters: Dict[str, Any] = {"rank": int(rank), "scale": float(alpha) / max(1, int(rank)), "dropout": float(dropout)}
    if keys:
        parameters["keys"] = list(keys)
    Path(path).write_text(json.dumps({"lora_parameters": parameters}, indent=2), encoding="utf-8")
    return Path(path)


# ------------------------------------------------------------- conversion
def _load_adapter_tensors(path: Path) -> Dict[str, Any]:
    from safetensors import safe_open  # type: ignore[import]

    try:
        handle = safe_open(str(path), framework="pt")
    except Exception:
        handle = safe_open(str(path), framework="np")
    with handle as f:
        return {key: f.get_tensor(key) for key in f.keys()}


def _transpose(tensor: Any) -> Any:
    return tensor.T.contiguous() if hasattr(tensor, "contiguous") else tensor.T.copy()


def _save_peft_tensors(tensors: Dict[str, Any], path: Path) -> None:
    sample = next(iter(tensors.values()))
    if hasattr(sample, "contiguous"):
        from safetensors.torch import save_file  # type: ignore[import]
    else:
        from safetensors.numpy import save_file  # type: ignore[import,no-redef]
    save_file(tensors, str(path), metadata={"format": "pt"})


def mlx_adapter_to_peft(adapter_dir: Path, out_dir: Path, *, base_model: str, rank: int, alpha: int,
                        dropout: float = 0.0) -> Dict[str, Any]:
    """Convert an ``mlx_lm`` adapter directory to a PEFT adapter directory."""
    adapter_dir, out_dir = Path(adapter_dir), Path(out_dir)
    source = adapter_dir / MLX_ADAPTER_FILE
    if not source.is_file():
        raise FileNotFoundError(f"No {MLX_ADAPTER_FILE} in {adapter_dir}")
    tensors = _load_adapter_tensors(source)
    converted: Dict[str, Any] = {}
    leaves: set = set()
    for key, tensor in tensors.items():
        match = _LORA_KEY_RE.match(key)
        if not match:
            continue
        module = match.group("module")
        # MLX wraps VLM text towers as ``language_model.model...``; PEFT names the
        # same module ``model...`` under the base_model.model root.
        module = module[len("language_model."):] if module.startswith("language_model.") else module
        which = "A" if match.group("which") == "a" else "B"
        converted[f"base_model.model.{module}.lora_{which}.weight"] = _transpose(tensor)
        leaves.add(module.rsplit(".", 1)[-1])
    if not converted:
        raise ValueError("The MLX adapter holds no lora_a/lora_b tensors; nothing to convert.")
    out_dir.mkdir(parents=True, exist_ok=True)
    _save_peft_tensors(converted, out_dir / "adapter_model.safetensors")
    config = {
        "peft_type": "LORA", "task_type": "CAUSAL_LM", "r": int(rank), "lora_alpha": int(alpha),
        "lora_dropout": float(dropout), "bias": "none", "target_modules": sorted(leaves),
        "base_model_name_or_path": base_model, "inference_mode": True, "fan_in_fan_out": False,
    }
    (out_dir / "adapter_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return {"tensors": len(converted), "target_modules": sorted(leaves), "out_dir": str(out_dir)}


# --------------------------------------------------------------- execution
def run_mlx_training(
    command: Sequence[str], *, total_steps: int, emit: Callable[[str], None],
    popen: Callable[..., Any] = subprocess.Popen,
) -> int:
    """Run ``mlx_lm lora``, echo its output and re-emit progress lines.

    Returns the process exit code. ``emit`` receives every output line plus the
    translated ``AUTOYOU_PROGRESS`` lines.
    """
    process = popen(list(command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace")
    for raw in process.stdout:  # type: ignore[union-attr]
        line = raw.rstrip("\r\n")
        emit(line)
        update = from_mlx_log_line(line, total_steps=total_steps)
        if update is not None:
            emit(format_progress(**{k: v for k, v in update.items() if k not in {"eta_seconds", "non_finite_loss"}}))
    return int(process.wait())
