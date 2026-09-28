# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-0a734e5cbe11ae77ef35c143

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-0a734e5cbe11ae77ef35c143"


import argparse
import inspect
import json
import os
import os.path
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.secure_storage import (
    SecureStorageError,
    enable_secure_storage_from_environment,
    materialize_secure_file,
    read_secure_file,
    write_secure_file,
)

DEFAULT_TRAINING_MODEL = os.getenv(
    "AUTOYOU_FINE_TUNING_TRAINING_MODEL",
    "mistralai/Ministral-3-8B-Instruct-2512-BF16",
)
DEFAULT_OLLAMA_BASE_MODEL = os.getenv(
    "AUTOYOU_FINE_TUNING_OLLAMA_BASE_MODEL",
    "ministral-3:8b",
)
DEFAULT_SYSTEM_PROMPT = (
    "You are chatting on WhatsApp. Reply in a natural, casual, friendly style. "
    "Keep messages concise. Use common abbreviations where natural. Match the "
    "energy of the conversation while protecting private identifiers."
)
DEFAULT_VISION_MIN_PIXELS = int(os.getenv("AUTOYOU_FINE_TUNING_VISION_MIN_PIXELS", str(256 * 28 * 28)))
DEFAULT_VISION_MAX_PIXELS = int(os.getenv("AUTOYOU_FINE_TUNING_VISION_MAX_PIXELS", str(384 * 28 * 28)))

def _log(message: str) -> None:
    print(message, flush=True)

def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    text = read_secure_file(path).decode("utf-8-sig")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    write_secure_file(path, json.dumps(payload, indent=2).encode("utf-8"))

def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _accelerator_backend(torch_module: Any) -> str:
    """Name the accelerator exposed by this interpreter, not by the host."""
    if bool(torch_module.cuda.is_available()):
        return "rocm" if getattr(torch_module.version, "hip", None) else "cuda"
    mps = getattr(getattr(torch_module, "backends", None), "mps", None)
    if mps is not None and bool(mps.is_available()):
        return "mps"
    return "cpu"


def _ollama_base_supports_vision(model_name: str) -> Optional[bool]:
    """Return a known Ollama vision capability, without requiring Ollama."""
    command = shutil.which("ollama")
    if not command or not str(model_name or "").strip():
        return None
    try:
        completed = subprocess.run(
            [command, "show", str(model_name).strip()],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return bool(re.search(r"(?mi)^\s*vision\s*$", completed.stdout or ""))

def _is_ministral_chat_template(tokenizer: Any) -> bool:
    template = str(getattr(tokenizer, "chat_template", "") or "")
    return "[INST]" in template and "[SYSTEM_PROMPT]" in template

def _build_ministral_sample(
    messages: List[Dict[str, Any]],
    tokenizer: Any,
    max_length: int,
) -> Optional[Dict[str, List[int]]]:
    input_ids: List[int] = []
    labels: List[int] = []
    bos_token = str(getattr(tokenizer, "bos_token", "") or "")
    eos_token = str(getattr(tokenizer, "eos_token", "") or "</s>")
    if bos_token:
        bos_tokens = tokenizer.encode(bos_token, add_special_tokens=False)
        input_ids.extend(bos_tokens)
        labels.extend([-100] * len(bos_tokens))

    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "system":
            text = f"[SYSTEM_PROMPT]{content}[/SYSTEM_PROMPT]"
            tokens = tokenizer.encode(text, add_special_tokens=False)
            input_ids.extend(tokens)
            labels.extend([-100] * len(tokens))
        elif role == "user":
            text = f"[INST]{content}[/INST]"
            tokens = tokenizer.encode(text, add_special_tokens=False)
            input_ids.extend(tokens)
            labels.extend([-100] * len(tokens))
        elif role == "assistant":
            tokens = tokenizer.encode(f"{content}{eos_token}", add_special_tokens=False)
            input_ids.extend(tokens)
            labels.extend(tokens)

    input_ids = input_ids[:max_length]
    labels = labels[:max_length]
    if not input_ids or all(label == -100 for label in labels):
        return None
    return {
        "input_ids": input_ids,
        "attention_mask": [1] * len(input_ids),
        "labels": labels,
    }

def _build_masked_sample(messages: List[Dict[str, Any]], tokenizer: Any, max_length: int) -> Optional[Dict[str, List[int]]]:
    """Tokenize chat samples with loss only on assistant/owner replies.

    This mirrors the core contract in tuning/whatsapp_finetune.py: system and
    user context are prompt tokens, while assistant tokens are the supervised
    target. It prevents the model from learning to imitate the other speaker.
    """
    if _is_ministral_chat_template(tokenizer):
        return _build_ministral_sample(messages, tokenizer, max_length)

    input_ids: List[int] = []
    labels: List[int] = []

    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role in {"system", "user"}:
            text = f"<|im_start|>{role}\n{content}<|im_end|>\n"
            tokens = tokenizer.encode(text, add_special_tokens=False)
            input_ids.extend(tokens)
            labels.extend([-100] * len(tokens))
        elif role == "assistant":
            header = "<|im_start|>assistant\n"
            body = f"{content}<|im_end|>\n"
            header_tokens = tokenizer.encode(header, add_special_tokens=False)
            body_tokens = tokenizer.encode(body, add_special_tokens=False)
            input_ids.extend(header_tokens + body_tokens)
            labels.extend([-100] * len(header_tokens) + body_tokens)

    input_ids = input_ids[:max_length]
    labels = labels[:max_length]
    if not input_ids or all(label == -100 for label in labels):
        return None
    return {
        "input_ids": input_ids,
        "attention_mask": [1] * len(input_ids),
        "labels": labels,
    }

@dataclass
class ChatCollator:
    pad_token_id: int
    pad_to_multiple_of: int = 8

    def __call__(self, features: List[Dict[str, Any]]) -> Dict[str, Any]:
        import torch

        max_len = max(len(feature["input_ids"]) for feature in features)
        if self.pad_to_multiple_of:
            max_len = ((max_len + self.pad_to_multiple_of - 1) // self.pad_to_multiple_of) * self.pad_to_multiple_of
        batch_input = []
        batch_mask = []
        batch_labels = []
        for feature in features:
            pad = max_len - len(feature["input_ids"])
            batch_input.append(feature["input_ids"] + [self.pad_token_id] * pad)
            batch_mask.append(feature["attention_mask"] + [0] * pad)
            batch_labels.append(feature["labels"] + [-100] * pad)
        return {
            "input_ids": torch.tensor(batch_input, dtype=torch.long),
            "attention_mask": torch.tensor(batch_mask, dtype=torch.long),
            "labels": torch.tensor(batch_labels, dtype=torch.long),
        }


def _has_images(rows: List[Dict[str, Any]]) -> bool:
    return any(bool(row.get("images")) for row in rows)


def _image_sample_count(rows: List[Dict[str, Any]]) -> int:
    return sum(1 for row in rows if row.get("images"))


def _dataset_image_paths(row: Dict[str, Any], dataset_dir: Path) -> List[Path]:
    root = dataset_dir.resolve()
    paths: List[Path] = []
    for raw_path in row.get("images") or []:
        image_path = (root / str(raw_path or "")).resolve()
        if root not in image_path.parents or not image_path.is_file():
            raise ValueError("A referenced image is missing from this private dataset bundle.")
        paths.append(image_path)
    return paths


class VisionDataset:
    """Lazy private image loading following the proven support-training format."""

    def __init__(self, rows: List[Dict[str, Any]], processor: Any, dataset_dir: Path):
        self.rows = rows
        self.processor = processor
        self.dataset_dir = dataset_dir

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> Dict[str, Any]:
        from PIL import Image

        row = self.rows[idx]
        images = []
        for image_path in _dataset_image_paths(row, self.dataset_dir):
            with materialize_secure_file(image_path) as readable_path:
                with Image.open(readable_path) as source:
                    images.append(source.convert("RGB").copy())

        conversation = []
        image_blocks = [{"type": "image"} for _ in images]
        attached_images = False
        for message in row.get("messages") or []:
            role = str(message.get("role") or "").strip().lower()
            content = str(message.get("content") or "").strip()
            if role not in {"system", "user", "assistant"} or not content:
                continue
            blocks = [{"type": "text", "text": content}]
            if role == "user" and image_blocks and not attached_images:
                blocks = image_blocks + blocks
                attached_images = True
            conversation.append({"role": role, "content": blocks})
        if image_blocks and not attached_images:
            raise ValueError("An image training sample needs a user message to ground the image.")
        if not conversation or conversation[-1]["role"] != "assistant":
            raise ValueError("Vision training samples must end with an assistant response.")
        text = self.processor.apply_chat_template(conversation, tokenize=False)
        prompt = self.processor.apply_chat_template(
            conversation[:-1], tokenize=False, add_generation_prompt=True
        )
        return {"text": text, "prompt": prompt, "images": images or None}


@dataclass
class VisionCollator:
    processor: Any
    max_seq_length: int
    _pad_id: int = 0
    _special_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        tokenizer = self.processor.tokenizer
        self._pad_id = tokenizer.pad_token_id or 0
        tokenizer.padding_side = "right"
        image_ids = []
        for source, name in (
            (self.processor, "image_token_id"),
            (self.processor, "video_token_id"),
            (tokenizer, "image_token_id"),
        ):
            token_id = getattr(source, name, None)
            if isinstance(token_id, int):
                image_ids.append(token_id)
        for literal in ("<|image_pad|>", "<|video_pad|>"):
            token_id = tokenizer.convert_tokens_to_ids(literal)
            if isinstance(token_id, int) and token_id >= 0:
                image_ids.append(token_id)
        self._special_ids = tuple(sorted(set(image_ids)))

    def _encode(self, texts: List[str], images: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.processor(
            text=texts,
            images=images or None,
            return_tensors="pt",
            truncation=True,
            max_length=self.max_seq_length,
            **kwargs,
        )

    def __call__(self, features: List[Dict[str, Any]]) -> Dict[str, Any]:
        texts = [feature["text"] for feature in features]
        flat_images = [image for feature in features if feature["images"] for image in feature["images"]]
        batch = self._encode(texts, flat_images, padding=True)
        labels = batch["input_ids"].clone()
        labels[labels == self._pad_id] = -100
        for token_id in self._special_ids:
            labels[labels == token_id] = -100
        for index, feature in enumerate(features):
            prompt = self._encode([feature["prompt"]], feature["images"], padding=False)
            labels[index, : prompt["input_ids"].shape[1]] = -100
        batch["labels"] = labels
        return batch

def _write_modelfile(
    *,
    output_dir: Path,
    ollama_dir: Path,
    ollama_model_name: str,
    ollama_base_model: str,
    adapter_dir: Optional[Path],
) -> Path:
    ollama_dir.mkdir(parents=True, exist_ok=True)
    modelfile_path = ollama_dir / "Modelfile"
    lines = [
        f"FROM {ollama_base_model}",
        "PARAMETER temperature 0.7",
        "PARAMETER top_p 0.9",
        "PARAMETER repeat_penalty 1.08",
        "PARAMETER num_ctx 8192",
        f'SYSTEM """{DEFAULT_SYSTEM_PROMPT}"""',
    ]
    adapter_path: Optional[Path] = None
    if adapter_dir is not None:
        existing_gguf = ollama_dir / "adapter.gguf"
        if existing_gguf.is_file():
            adapter_path = existing_gguf
            adapter_ref = "./adapter.gguf"
        else:
            adapter_path = adapter_dir
        if adapter_dir.is_dir() and not existing_gguf.is_file():
            safetensors_path = adapter_dir / "adapter_model.safetensors"
            config_path = adapter_dir / "adapter_config.json"
            if config_path.is_file() and safetensors_path.is_file():
                shutil.copy2(config_path, ollama_dir / "adapter_config.json")
                shutil.copy2(safetensors_path, ollama_dir / "adapter_model.safetensors")
                adapter_ref = "./adapter_model.safetensors"
            else:
                try:
                    adapter_ref = os.path.relpath(adapter_path.resolve(), ollama_dir.resolve()).replace(os.sep, "/")
                except Exception:
                    adapter_ref = adapter_path.resolve().as_posix()
        else:
            try:
                adapter_ref = os.path.relpath(adapter_path.resolve(), ollama_dir.resolve()).replace(os.sep, "/")
            except Exception:
                adapter_ref = adapter_path.resolve().as_posix()
        lines.insert(1, f"ADAPTER {adapter_ref}")
    modelfile_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        "ollama_model_name": ollama_model_name,
        "ollama_base_model": ollama_base_model,
        "adapter_dir": str(adapter_dir.resolve()) if adapter_dir else None,
        "adapter_path": str(adapter_path.resolve()) if adapter_dir else None,
        "output_dir": str(output_dir.resolve()),
        "modelfile_path": str(modelfile_path.resolve()),
    }
    (ollama_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return modelfile_path

def _normalized_model_config(model_id: str) -> Any:
    from transformers import AutoConfig

    try:
        return AutoConfig.from_pretrained(model_id, trust_remote_code=True)
    except KeyError as exc:
        if str(exc).strip("'\"") != "ministral3":
            raise
        from huggingface_hub import hf_hub_download
        from transformers.models.mistral3 import Mistral3Config

        config_path = hf_hub_download(model_id, "config.json")
        config_data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        text_config = config_data.get("text_config")
        if isinstance(text_config, dict) and text_config.get("model_type") == "ministral3":
            # Transformers 4.56 knows Mistral3, but not the nested Ministral3
            # text-config alias used by this model card. MistralConfig carries
            # the same text decoder shape needed by the Mistral3 wrapper.
            text_config["model_type"] = "mistral"
        return Mistral3Config(**config_data)

def _load_training_model(model_id: str, model_kwargs: Dict[str, Any]) -> Any:
    from transformers import AutoModelForCausalLM

    config = _normalized_model_config(model_id)
    if getattr(config, "model_type", None) == "mistral3":
        from transformers.models.mistral3 import Mistral3ForConditionalGeneration

        return Mistral3ForConditionalGeneration.from_pretrained(model_id, config=config, **model_kwargs)
    return AutoModelForCausalLM.from_pretrained(model_id, config=config, **model_kwargs)


def _load_vision_training_model(model_id: str, model_kwargs: Dict[str, Any]) -> Any:
    config = _normalized_model_config(model_id)
    if getattr(config, "model_type", None) == "mistral3":
        return _load_training_model(model_id, model_kwargs)
    from transformers import AutoModelForImageTextToText

    return AutoModelForImageTextToText.from_pretrained(model_id, config=config, **model_kwargs)


def _load_vision_processor(model_id: str, args: argparse.Namespace) -> Any:
    from transformers import AutoProcessor

    # Qwen accepts these controls directly; Pixtral/Mistral processors can keep
    # their own defaults when an older transformers release does not expose them.
    kwargs = {
        "trust_remote_code": True,
        "min_pixels": int(args.vision_min_pixels),
        "max_pixels": int(args.vision_max_pixels),
    }
    try:
        return AutoProcessor.from_pretrained(model_id, **kwargs)
    except TypeError:
        kwargs.pop("min_pixels", None)
        kwargs.pop("max_pixels", None)
        return AutoProcessor.from_pretrained(model_id, **kwargs)

def _target_modules_for_model(model: Any) -> List[str]:
    target_leaf_names = {
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    }
    text_targets = [
        name
        for name, _module in model.named_modules()
        if name.rsplit(".", 1)[-1] in target_leaf_names and "vision" not in name and "visual" not in name
    ]
    return text_targets or sorted(target_leaf_names)

def prepare_only(args: argparse.Namespace) -> int:
    train_rows = _read_jsonl(Path(args.train_data))
    eval_rows = _read_jsonl(Path(args.eval_data)) if args.eval_data and Path(args.eval_data).is_file() else []
    vision = _has_images(train_rows) or _has_images(eval_rows)
    if vision:
        try:
            _load_vision_processor(str(args.training_model_id or DEFAULT_TRAINING_MODEL), args)
            for row in train_rows + eval_rows:
                _dataset_image_paths(row, Path(args.train_data).resolve().parent)
        except Exception as exc:
            _log(f"VISION_SETUP_ERROR {exc}")
            return 6
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "status": "prepared",
        "train_samples": len(train_rows),
        "eval_samples": len(eval_rows),
        "training_model_id": args.training_model_id,
        "ollama_base_model": args.ollama_base_model,
        "modality": "vision" if vision else "text",
        "image_samples": _image_sample_count(train_rows) + _image_sample_count(eval_rows),
    }
    _write_json(output_dir / "training_summary.json", summary)
    _write_modelfile(
        output_dir=output_dir,
        ollama_dir=Path(args.ollama_dir),
        ollama_model_name=args.ollama_model_name,
        ollama_base_model=args.ollama_base_model,
        adapter_dir=None,
    )
    _log("PREPARED_ONLY completed without model training.")
    return 0

def train_adapter(args: argparse.Namespace) -> int:
    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import BitsAndBytesConfig, Trainer, TrainingArguments
    except Exception as exc:
        _log(f"DEPENDENCY_ERROR {exc}")
        return 2

    train_rows = _read_jsonl(Path(args.train_data))
    eval_rows = _read_jsonl(Path(args.eval_data)) if args.eval_data and Path(args.eval_data).is_file() else []
    if not train_rows:
        _log("DATA_ERROR no training samples found")
        return 3

    model_id = str(args.training_model_id or DEFAULT_TRAINING_MODEL)
    vision = _has_images(train_rows) or _has_images(eval_rows)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    accelerator = _accelerator_backend(torch)
    cuda_available = accelerator in {"cuda", "rocm"}
    cuda_build = str(getattr(torch.version, "cuda", "") or "")
    allow_cpu_training = bool(args.allow_cpu_training or _truthy(os.getenv("AUTOYOU_FINE_TUNING_ALLOW_CPU_TRAINING")))
    _log(
        "START model={model} train_samples={train} eval_samples={eval} accelerator={accelerator} torch_cuda={torch_cuda}".format(
            model=model_id,
            train=len(train_rows),
            eval=len(eval_rows),
            accelerator=accelerator,
            torch_cuda=cuda_build or "none",
        )
    )
    if accelerator == "cpu" and not allow_cpu_training:
        message = (
            "CPU_TRAINING_OPT_IN_REQUIRED this Python environment exposes no CUDA, ROCm, or Apple MPS accelerator. "
            "CPU LoRA training is supported but can be very slow; use a small local model and pass --allow-cpu-training "
            "(or set AUTOYOU_FINE_TUNING_ALLOW_CPU_TRAINING=1)."
        )
        _write_json(
            output_dir / "training_summary.json",
            {
                "status": "failed",
                "reason": "cpu_training_opt_in_required",
                "training_model_id": model_id,
                "ollama_base_model": args.ollama_base_model,
                "torch_cuda": cuda_build or None,
                "cuda_available": False,
                "accelerator": accelerator,
                "message": message,
            },
        )
        _log(message)
        return 4

    tokenizer = None
    processor = None
    if vision:
        base_supports_vision = _ollama_base_supports_vision(args.ollama_base_model)
        if base_supports_vision is False:
            message = (
                f"VISION_BASE_MODEL_ERROR {args.ollama_base_model!r} does not advertise Ollama vision support. "
                "Choose a vision-capable base model before installing an image-trained adapter."
            )
            _write_json(
                output_dir / "training_summary.json",
                {"status": "failed", "reason": "ollama_base_model_not_vision_capable", "message": message},
            )
            _log(message)
            return 6
        if base_supports_vision is None:
            _log(
                "VISION_BASE_MODEL_WARNING could not verify Ollama vision support locally; "
                "the selected base model must support images before adapter installation."
            )
        try:
            processor = _load_vision_processor(model_id, args)
        except Exception as exc:
            message = f"VISION_SETUP_ERROR {exc}"
            _write_json(output_dir / "training_summary.json", {"status": "failed", "reason": "vision_processor_unavailable", "message": message})
            _log(message)
            return 6
    else:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

    model_kwargs: Dict[str, Any] = {
        "trust_remote_code": True,
        "device_map": "auto" if accelerator in {"cuda", "rocm"} else None,
    }
    if accelerator == "cuda":
        compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        try:
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=compute_dtype,
            )
        except Exception as exc:
            _log(f"QUANTIZATION_WARNING {exc}")
    elif accelerator == "rocm":
        # ROCm exposes its GPU through torch.cuda, but bitsandbytes wheels are
        # not portable across AMD installations. Load in the native dtype.
        model_kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    elif accelerator == "mps":
        model_kwargs["torch_dtype"] = torch.float16
    else:
        model_kwargs["torch_dtype"] = torch.float32
        model_kwargs["low_cpu_mem_usage"] = True

    model_kwargs = {key: value for key, value in model_kwargs.items() if value is not None}
    model = _load_vision_training_model(model_id, model_kwargs) if vision else _load_training_model(model_id, model_kwargs)
    if accelerator == "cuda":
        try:
            model = prepare_model_for_kbit_training(model)
        except Exception as exc:
            _log(f"PREPARE_KBIT_WARNING {exc}")
    elif accelerator == "mps":
        model.to("mps")

    if vision:
        for name, parameter in model.named_parameters():
            if "vision" in name or "visual" in name:
                parameter.requires_grad = False

    target_modules = _target_modules_for_model(model)
    _log(f"LORA_TARGETS count={len(target_modules)}")
    lora_config = LoraConfig(
        r=int(args.lora_rank),
        lora_alpha=int(args.lora_alpha),
        lora_dropout=float(args.lora_dropout),
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=target_modules,
    )
    model = get_peft_model(model, lora_config)

    def build_dataset(rows: List[Dict[str, Any]], split_name: str) -> Dataset:
        processed = []
        skipped = 0
        for row in rows:
            sample = _build_masked_sample(list(row.get("messages") or []), tokenizer, int(args.max_seq_length))
            if sample is None:
                skipped += 1
            else:
                processed.append(sample)
        _log(f"TOKENIZED split={split_name} samples={len(processed)} skipped={skipped}")
        if not processed:
            raise ValueError(f"No trainable assistant labels found in {split_name} split.")
        return Dataset.from_list(processed)

    if vision:
        try:
            for row in train_rows + eval_rows:
                _dataset_image_paths(row, Path(args.train_data).resolve().parent)
        except Exception as exc:
            _log(f"VISION_DATA_ERROR {exc}")
            return 6
        train_dataset = VisionDataset(train_rows, processor, Path(args.train_data).resolve().parent)
        eval_dataset = VisionDataset(eval_rows, processor, Path(args.train_data).resolve().parent) if eval_rows else None
    else:
        train_dataset = build_dataset(train_rows, "train")
        eval_dataset = build_dataset(eval_rows, "eval") if eval_rows else None

    max_steps = int(args.max_steps or 0)
    eval_keyword = (
        "eval_strategy"
        if "eval_strategy" in inspect.signature(TrainingArguments.__init__).parameters
        else "evaluation_strategy"
    )
    training_kwargs: Dict[str, Any] = {
        "output_dir": str(output_dir),
        "num_train_epochs": float(args.epochs),
        "max_steps": max_steps if max_steps > 0 else -1,
        "per_device_train_batch_size": int(args.batch_size),
        "gradient_accumulation_steps": int(args.gradient_accumulation_steps),
        "learning_rate": float(args.learning_rate),
        "logging_steps": 1,
        "save_strategy": "epoch",
        eval_keyword: "steps" if eval_dataset is not None else "no",
        "eval_steps": max(5, int(args.eval_steps)),
        "save_total_limit": 2,
        "report_to": [],
        "fp16": bool(accelerator == "cuda" and not torch.cuda.is_bf16_supported()),
        "bf16": bool(accelerator in {"cuda", "rocm"} and torch.cuda.is_bf16_supported()),
    }
    training_args = TrainingArguments(**training_kwargs)
    collator = (
        VisionCollator(processor=processor, max_seq_length=int(args.max_seq_length))
        if vision
        else ChatCollator(pad_token_id=int(tokenizer.pad_token_id or tokenizer.eos_token_id or 0))
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=collator,
        processing_class=processor if vision else tokenizer,
    )
    train_result = trainer.train()
    model.save_pretrained(output_dir)
    if vision:
        processor.save_pretrained(output_dir / "processor")
    else:
        tokenizer.save_pretrained(output_dir / "tokenizer")
    _write_json(
        output_dir / "training_summary.json",
        {
            "status": "completed",
            "train_samples": len(train_rows),
            "eval_samples": len(eval_rows),
            "training_model_id": model_id,
            "ollama_base_model": args.ollama_base_model,
            "modality": "vision" if vision else "text",
            "image_samples": _image_sample_count(train_rows) + _image_sample_count(eval_rows),
            "metrics": train_result.metrics,
        },
    )
    modelfile = _write_modelfile(
        output_dir=output_dir,
        ollama_dir=Path(args.ollama_dir),
        ollama_model_name=args.ollama_model_name,
        ollama_base_model=args.ollama_base_model,
        adapter_dir=output_dir,
    )
    _log(f"COMPLETED adapter={output_dir.resolve()} modelfile={modelfile.resolve()}")
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AutoYou WhatsApp persona fine-tuning.")
    parser.add_argument("--train-data", required=True)
    parser.add_argument("--eval-data", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ollama-dir", required=True)
    parser.add_argument("--ollama-model-name", default="autoyou-whatsapp-persona")
    parser.add_argument("--training-model-id", default=DEFAULT_TRAINING_MODEL)
    parser.add_argument("--ollama-base-model", default=DEFAULT_OLLAMA_BASE_MODEL)
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--eval-steps", type=int, default=20)
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--vision-min-pixels", type=int, default=DEFAULT_VISION_MIN_PIXELS)
    parser.add_argument("--vision-max-pixels", type=int, default=DEFAULT_VISION_MAX_PIXELS)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--allow-cpu-training", action="store_true")
    return parser

def main(argv: Optional[List[str]] = None) -> int:
    try:
        enable_secure_storage_from_environment()
    except SecureStorageError as exc:
        _log(f"SECURITY_ERROR {exc}")
        return 5
    args = build_parser().parse_args(argv)
    if args.prepare_only:
        return prepare_only(args)
    return train_adapter(args)

if __name__ == "__main__":
    raise SystemExit(main())
