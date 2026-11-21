# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Base-model catalog, licence policy and hardware-based recommendation.

Two facts drive this module.

**A fine-tune inherits its base's licence.** AutoYou hosts and distributes tuned
weights, so a base released under a non-commercial licence cannot sit under
them. That already happened once: Qwen released the Qwen2.5 3B (and 72B) under
the Qwen Research License while every other Qwen2.5 size is Apache 2.0, and a
3B base shipped under a paid product before anyone looked. The catalog records
each base's licence from its model card, and ``license_tier`` is the gate.

**The right size depends on the machine.** Recommending an 8B model to a laptop
produces a job that either fails or crawls; recommending a 0.8B model to a
96 GB workstation wastes it. ``recommend_base`` picks by usable memory.

The catalog is deliberately short and curated rather than scraped. Models not in
it still work (``license_status`` reports ``unknown`` until their card is
checked); they just get no recommendation.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Optional

from .hardware import GIB, HardwareReport

PERMISSIVE_LICENSES = frozenset({"apache-2.0", "mit", "bsd-2-clause", "bsd-3-clause", "isc", "unlicense", "cc0-1.0"})
# Licences whose terms forbid or restrict commercial hosting and redistribution.
NON_COMMERCIAL_LICENSES = frozenset({
    "qwen-research", "cc-by-nc-4.0", "cc-by-nc-sa-4.0", "cc-by-nc-nd-4.0", "cc-by-nc-2.0",
    "cc-by-nc-3.0", "cc-by-nc-sa-3.0", "mistral-research", "mnpl", "tongyi-qianwen-research",
})
# Licences that are usable but come with bespoke terms a person must read.
CUSTOM_TERMS_LICENSES = frozenset({
    "other", "llama2", "llama3", "llama3.1", "llama3.2", "llama3.3", "llama4", "gemma",
    "openrail", "openrail++", "bigscience-openrail-m", "creativeml-openrail-m", "qwen", "tongyi-qianwen",
})


@dataclass(frozen=True)
class BaseModelSpec:
    id: str
    family: str
    params_b: float
    license: str
    modality: str                       # "text" or "vision" (reads images)
    ollama_tag: Optional[str]
    layers: Optional[int] = None
    hidden: Optional[int] = None
    vocab: Optional[int] = None
    hybrid_linear_attention: bool = False
    thinking_template: bool = False
    min_train_gib_bf16: float = 0.0     # LoRA, bf16 weights, checkpointing, seq 2048, batch 1
    min_train_gib_4bit: float = 0.0     # QLoRA (NVIDIA CUDA with bitsandbytes)
    mlx_repo: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["license_tier"] = license_tier(self.license)
        return data


def _spec(model_id: str, family: str, params_b: float, ollama_tag: Optional[str], **kwargs: Any) -> BaseModelSpec:
    modality = kwargs.pop("modality", "vision")
    license_id = kwargs.pop("license", "apache-2.0")
    # Calibrated against a measured Qwen3.5-9B LoRA run (batch 1, checkpointing,
    # 20.7 GiB peak allocated): about 2.5 GiB fixed plus 2.4 GiB per billion
    # parameters in bf16, and 0.9 per billion when the weights are 4-bit.
    bf16 = kwargs.pop("min_train_gib_bf16", round(2.5 + params_b * 2.4, 1))
    four = kwargs.pop("min_train_gib_4bit", round(2.5 + params_b * 0.9, 1))
    return BaseModelSpec(model_id, family, params_b, license_id, modality, ollama_tag,
                         min_train_gib_bf16=bf16, min_train_gib_4bit=four, **kwargs)


# Ordered smallest to largest within a family. Facts are from each model's card
# and config.json (checked November 2026): Qwen3.5 is natively multimodal and
# runs three of every four layers as gated linear attention, which keeps the
# serving KV cache small; its template has a thinking mode.
CATALOG: tuple = (
    _spec("Qwen/Qwen3.5-0.8B", "qwen3.5", 0.9, "qwen3.5:0.8b", hybrid_linear_attention=True, thinking_template=True,
          mlx_repo="mlx-community/Qwen3.5-0.8B-bf16"),
    _spec("Qwen/Qwen3.5-2B", "qwen3.5", 2.3, "qwen3.5:2b", layers=24, hidden=2048, vocab=248320,
          hybrid_linear_attention=True, thinking_template=True, mlx_repo="mlx-community/Qwen3.5-2B-bf16"),
    _spec("Qwen/Qwen3.5-4B", "qwen3.5", 4.7, "qwen3.5:4b", layers=32, hidden=2560, vocab=248320,
          hybrid_linear_attention=True, thinking_template=True, mlx_repo="mlx-community/Qwen3.5-4B-bf16"),
    _spec("Qwen/Qwen3.5-9B", "qwen3.5", 9.7, "qwen3.5:9b", layers=32, hidden=4096, vocab=248320,
          hybrid_linear_attention=True, thinking_template=True, mlx_repo="mlx-community/Qwen3.5-9B-bf16"),
    _spec("Qwen/Qwen3.5-27B", "qwen3.5", 27.8, "qwen3.5:27b", layers=64, hidden=5120, vocab=248320,
          hybrid_linear_attention=True, thinking_template=True),
    _spec("Qwen/Qwen3.6-27B", "qwen3.6", 27.8, "qwen3.6:27b", layers=64, hidden=5120, vocab=248320,
          hybrid_linear_attention=True, thinking_template=True),
    _spec("Qwen/Qwen3.8-27B", "qwen3.8", 27.8, "qwen3.8:27b", layers=64, hidden=5120, vocab=248320,
          hybrid_linear_attention=True, thinking_template=True),
    # Classic dense stacks: no thinking template to mind, the safest drop-in when
    # a runtime's support for a hybrid architecture lags.
    _spec("Qwen/Qwen3-VL-4B-Instruct", "qwen3-vl", 4.4, "qwen3-vl:4b", layers=36, hidden=2560, vocab=151936),
    _spec("Qwen/Qwen3-VL-8B-Instruct", "qwen3-vl", 8.8, "qwen3-vl:8b", layers=36, hidden=4096, vocab=151936),
    _spec("Qwen/Qwen3-4B-Instruct-2507", "qwen3", 4.0, "qwen3:4b", modality="text", layers=36, hidden=2560, vocab=151936),
    _spec("mistralai/Ministral-3-8B-Instruct-2512-BF16", "ministral-3", 8.9, "ministral-3:8b",
          notes="The agent's historical default; vision-capable Mistral3 architecture."),
    # Kept in the catalog so the policy has something to refuse.
    _spec("Qwen/Qwen2.5-3B-Instruct", "qwen2.5", 3.1, "qwen2.5:3b", modality="text", license="qwen-research",
          layers=36, hidden=2048, vocab=151936, notes="Qwen Research License: non-commercial."),
    _spec("Qwen/Qwen2.5-VL-3B-Instruct", "qwen2.5-vl", 3.8, "qwen2.5vl:3b", license="qwen-research",
          notes="Qwen Research License: non-commercial."),
    _spec("Qwen/Qwen2.5-VL-7B-Instruct", "qwen2.5-vl", 8.3, "qwen2.5vl:7b", layers=28, hidden=3584, vocab=152064,
          notes="Apache 2.0; the previous support base."),
)

_BY_ID: Dict[str, BaseModelSpec] = {spec.id.lower(): spec for spec in CATALOG}

#: Recommendation ladder, largest first. A base is recommended when the usable
#: memory covers its measured training need; the smallest is the floor.
_LADDER = (
    "Qwen/Qwen3.5-9B",
    "Qwen/Qwen3.5-4B",
    "Qwen/Qwen3.5-2B",
    "Qwen/Qwen3.5-0.8B",
)


def lookup(model_id: str) -> Optional[BaseModelSpec]:
    return _BY_ID.get(str(model_id or "").strip().lower())


def license_tier(license_id: Optional[str]) -> str:
    """``permissive`` | ``non_commercial`` | ``custom`` | ``unknown``."""
    value = str(license_id or "").strip().lower()
    if value in PERMISSIVE_LICENSES:
        return "permissive"
    if value in NON_COMMERCIAL_LICENSES or value.startswith("cc-by-nc"):
        return "non_commercial"
    if value in CUSTOM_TERMS_LICENSES or value.startswith(("llama", "gemma", "openrail")):
        return "custom"
    return "unknown"


def infer_params_b(model_id: str) -> Optional[float]:
    """Parameter count in billions from a name such as ``...-9B-...`` or ``7b``."""
    spec = lookup(model_id)
    if spec:
        return spec.params_b
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])", str(model_id or ""))
    return float(match.group(1)) if match else None


def _fetch_hf_license(model_id: str, timeout: float = 8.0) -> Optional[str]:
    request = urllib.request.Request(
        f"https://huggingface.co/api/models/{model_id}", headers={"User-Agent": "autoyou-fine-tuning"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed https host
        payload = json.loads(response.read().decode("utf-8"))
    card = payload.get("cardData") or {}
    if card.get("license"):
        return str(card["license"])
    for tag in payload.get("tags") or []:
        if str(tag).startswith("license:"):
            return str(tag).split(":", 1)[1]
    return None


def license_status(model_id: str, *, allow_network: bool = False,
                   fetch: Optional[Callable[[str], Optional[str]]] = None) -> Dict[str, Any]:
    """What is known about a base model's licence, and what that permits.

    The catalog answers offline. Anything else is ``unknown`` unless
    ``allow_network`` lets the model card be read, which is never done silently.
    """
    spec = lookup(model_id)
    license_id: Optional[str] = spec.license if spec else None
    source = "catalog" if spec else "unknown"
    if license_id is None and allow_network:
        try:
            license_id = (fetch or _fetch_hf_license)(model_id)
            source = "model card"
        except Exception as exc:  # advice only; never fail a job over a lookup
            return {"model_id": model_id, "license": None, "tier": "unknown", "source": "unavailable",
                    "ok_to_distribute": False, "message": f"Could not read the model card: {exc}"}
    tier = license_tier(license_id)
    messages = {
        "permissive": f"{license_id}: tuned weights may be hosted and redistributed with the licence text and attribution.",
        "non_commercial": f"{license_id}: non-commercial. Tuned weights must not be hosted or redistributed commercially.",
        "custom": f"{license_id}: bespoke terms. Read them before hosting or redistributing tuned weights.",
        "unknown": "Licence not verified. Check the model card before hosting or redistributing tuned weights.",
    }
    return {
        "model_id": model_id, "license": license_id, "tier": tier, "source": source,
        "ok_to_distribute": tier == "permissive", "message": messages[tier],
    }


def recommend_base(report: HardwareReport, *, vision: bool = False, require_permissive: bool = True) -> BaseModelSpec:
    """The base to suggest for this machine. Never returns a restricted licence
    when ``require_permissive`` is set (the default)."""
    floor = lookup(_LADDER[-1])
    if report.backend == "cpu":
        # Nothing larger is practical to train without an accelerator.
        return floor  # type: ignore[return-value]
    usable = report.usable_bytes() / GIB
    for model_id in _LADDER:
        spec = lookup(model_id)
        if spec is None:
            continue
        if require_permissive and license_tier(spec.license) != "permissive":
            continue
        if vision and spec.modality != "vision":
            continue
        need = spec.min_train_gib_4bit if report.bitsandbytes_available else spec.min_train_gib_bf16
        if usable >= need:
            return spec
    return floor  # type: ignore[return-value]
