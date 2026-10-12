# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-65c2c51ab2fcb2ad3e4b222c

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import importlib.metadata
import logging
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-65c2c51ab2fcb2ad3e4b222c"


LOGGER = logging.getLogger("autoyou.custom_voice_tts")

BASE_VITS_MODEL = "facebook/mms-tts-eng"
CUSTOM_VOICE_PROVIDER = "custom"
CUSTOM_VOICE_SYSTEM_VOICE_ID = "custom_voice"
CUSTOM_VOICE_DISPLAY_NAME = "Custom trained voice"
CUSTOM_VOICE_METADATA_FILENAME = "custom_voice_metadata.json"
CUSTOM_VOICE_ACTIVE_MODEL_FILENAME = "active_custom_voice_model.json"

_MODEL_CACHE_LOCK = threading.Lock()
_MODEL_CACHE: Dict[Tuple[str, str, float], Tuple[Any, Any]] = {}
_TTS_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_TTS_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s*")
_TTS_BULLET_RE = re.compile(r"(?m)^\s*[-*+]\s+")
_TTS_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def get_voice_training_dir() -> Path:
    from shared.voice_training_storage import get_voice_training_dir as _get_voice_training_dir

    return _get_voice_training_dir()


def get_custom_voice_model_dir() -> Path:
    return get_voice_training_dir() / "models" / "custom_voice"


def _custom_voice_models_dir() -> Path:
    return get_voice_training_dir() / "models"


def _model_weight_files(model_dir: Path) -> list[Path]:
    candidates = [
        model_dir / "model.safetensors",
        model_dir / "pytorch_model.bin",
        model_dir / "model.bin",
    ]
    return [candidate for candidate in candidates if candidate.exists() and candidate.is_file()]


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return str(left) == str(right)


def _custom_voice_model_ready_at(model_dir: Path) -> bool:
    return bool(
        model_dir.exists()
        and (model_dir / "config.json").exists()
        and _model_weight_files(model_dir)
    )


def _active_custom_voice_pointer_path() -> Path:
    return _custom_voice_models_dir() / CUSTOM_VOICE_ACTIVE_MODEL_FILENAME


def _read_active_custom_voice_model_dir() -> Optional[Path]:
    pointer_path = _active_custom_voice_pointer_path()
    if not pointer_path.exists():
        return None
    try:
        loaded = load_secure_json(pointer_path, default={})
        if not isinstance(loaded, dict):
            return None
        raw = str(loaded.get("model_dir") or "").strip()
        if not raw:
            return None
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = _custom_voice_models_dir() / candidate
        candidate = candidate.resolve()
        if _custom_voice_model_ready_at(candidate):
            return candidate
    except SecureStorageError:
        raise
    except Exception as exc:
        LOGGER.debug("Failed to read active custom voice pointer %s: %s", pointer_path, exc)
    return None


def resolve_custom_voice_model_dir(model_dir: Optional[Path] = None) -> Path:
    requested_dir = Path(model_dir) if model_dir is not None else get_custom_voice_model_dir()
    if _same_path(requested_dir, get_custom_voice_model_dir()):
        active_dir = _read_active_custom_voice_model_dir()
        if active_dir is not None:
            return active_dir
    return requested_dir


def set_active_custom_voice_model_dir(model_dir: Path) -> Path:
    resolved_dir = Path(model_dir).resolve()
    if not _custom_voice_model_ready_at(resolved_dir):
        raise FileNotFoundError(f"Custom voice model is not ready at {resolved_dir}")
    pointer_path = _active_custom_voice_pointer_path()
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    save_secure_json(
        pointer_path,
        {
            "model_dir": str(resolved_dir),
            "updated_at": time.time(),
        },
    )
    return pointer_path


def _custom_voice_id_for_dir(model_dir: Path, metadata: Dict[str, Any]) -> str:
    configured = str(metadata.get("system_voice_id") or metadata.get("voice_id") or "").strip()
    if configured:
        return configured
    if model_dir.name == CUSTOM_VOICE_SYSTEM_VOICE_ID:
        return CUSTOM_VOICE_SYSTEM_VOICE_ID
    return model_dir.name


def _custom_voice_display_name_for_dir(model_dir: Path, metadata: Dict[str, Any]) -> str:
    configured = str(metadata.get("display_name") or metadata.get("name") or "").strip()
    if configured:
        return configured
    if model_dir.name == CUSTOM_VOICE_SYSTEM_VOICE_ID:
        return CUSTOM_VOICE_DISPLAY_NAME
    return model_dir.name.replace("_", " ").replace("-", " ").strip().title() or CUSTOM_VOICE_DISPLAY_NAME


def custom_voice_model_ready(model_dir: Optional[Path] = None) -> bool:
    resolved_dir = resolve_custom_voice_model_dir(model_dir)
    return _custom_voice_model_ready_at(resolved_dir)


def custom_voice_status(model_dir: Optional[Path] = None) -> Dict[str, Any]:
    requested_dir = Path(model_dir) if model_dir is not None else get_custom_voice_model_dir()
    resolved_dir = resolve_custom_voice_model_dir(requested_dir)
    metadata_path = resolved_dir / CUSTOM_VOICE_METADATA_FILENAME
    metadata: Dict[str, Any] = {}
    if metadata_path.exists():
        try:
            loaded = load_secure_json(metadata_path, default={})
            if isinstance(loaded, dict):
                metadata = loaded
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.debug("Failed to read custom voice metadata %s: %s", metadata_path, exc)

    weight_files = _model_weight_files(resolved_dir)
    voice_id = _custom_voice_id_for_dir(resolved_dir, metadata)
    display_name = _custom_voice_display_name_for_dir(resolved_dir, metadata)
    return {
        "ready": custom_voice_model_ready(resolved_dir),
        "provider": CUSTOM_VOICE_PROVIDER,
        "id": voice_id,
        "name": display_name,
        "system_voice_id": voice_id,
        "display_name": display_name,
        "model_dir": str(resolved_dir),
        "requested_model_dir": str(requested_dir),
        "active_model_pointer": str(_active_custom_voice_pointer_path()),
        "active_model_resolved": not _same_path(requested_dir, resolved_dir),
        "base_model": metadata.get("base_model") or BASE_VITS_MODEL,
        "metadata": metadata,
        "weight_files": [path.name for path in weight_files],
        "fine_tuning_applied": bool(metadata.get("fine_tuning_applied", False)),
        "training_strategy": str(metadata.get("training_strategy") or "").strip(),
    }


def list_custom_voice_statuses(*, include_unready: bool = False) -> List[Dict[str, Any]]:
    """Return installed custom voice artifacts discovered in the shared voice store."""
    models_dir = get_voice_training_dir() / "models"
    if not models_dir.is_dir():
        return []

    statuses: List[Dict[str, Any]] = []
    seen_model_dirs: set[str] = set()
    for model_dir in sorted(path for path in models_dir.iterdir() if path.is_dir()):
        status = custom_voice_status(model_dir)
        resolved_key = str(status.get("model_dir") or model_dir)
        if resolved_key in seen_model_dirs:
            continue
        seen_model_dirs.add(resolved_key)
        if include_unready or status.get("ready"):
            statuses.append(status)
    return statuses


def write_custom_voice_metadata(model_dir: Path, metadata: Dict[str, Any]) -> Path:
    model_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(metadata or {})
    payload.setdefault("base_model", BASE_VITS_MODEL)
    payload.setdefault("updated_at", time.time())
    metadata_path = model_dir / CUSTOM_VOICE_METADATA_FILENAME
    save_secure_json(metadata_path, payload)
    return metadata_path


def package_versions() -> Dict[str, str]:
    versions: Dict[str, str] = {}
    for package_name in ("torch", "torchaudio", "torchvision", "transformers", "soundfile", "numpy"):
        try:
            versions[package_name] = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            versions[package_name] = ""
    return versions


def _disable_transformers_torchvision_for_audio_only() -> None:
    """Prevent audio-only VITS imports from loading an incompatible torchvision."""

    try:
        import transformers.utils.import_utils as import_utils

        setattr(import_utils, "_torchvision_available", False)
        setattr(import_utils, "_torchvision_version", "N/A")
    except Exception as exc:
        LOGGER.debug("Could not mark torchvision unavailable for Transformers audio import: %s", exc)


def load_vits_model_components() -> Tuple[Any, Any]:
    _disable_transformers_torchvision_for_audio_only()
    from transformers import AutoTokenizer
    from transformers.models.vits.modeling_vits import VitsModel

    return VitsModel, AutoTokenizer


def _artifact_stamp(model_dir: Path) -> float:
    stamp = 0.0
    candidates = [
        model_dir / "config.json",
        model_dir / CUSTOM_VOICE_METADATA_FILENAME,
        *_model_weight_files(model_dir),
    ]
    for candidate in candidates:
        try:
            stamp = max(stamp, candidate.stat().st_mtime)
        except OSError:
            pass
    return stamp


def _load_cached_model(model_dir: Path, device: str) -> Tuple[Any, Any]:
    model_dir = resolve_custom_voice_model_dir(model_dir)
    cache_key = (str(model_dir.resolve()), device, _artifact_stamp(model_dir))
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached

    VitsModel, AutoTokenizer = load_vits_model_components()
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
    model = VitsModel.from_pretrained(str(model_dir)).to(device)
    try:
        model.eval()
    except Exception:
        pass

    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()
        _MODEL_CACHE[cache_key] = (tokenizer, model)
    return tokenizer, model


def _custom_voice_tts_max_chunk_chars() -> int:
    try:
        return max(40, int(os.environ.get("AUTOYOU_CUSTOM_VOICE_MAX_CHUNK_CHARS", "180")))
    except Exception:
        return 180


def _normalize_custom_voice_tts_text(text: str) -> str:
    cleaned = str(text or "")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("```", "\n")
    cleaned = _TTS_MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    cleaned = _TTS_HEADING_RE.sub("", cleaned)
    cleaned = _TTS_BULLET_RE.sub("", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    cleaned = cleaned.replace("\u2014", ", ").replace("\u2013", ", ")
    cleaned = cleaned.replace("&", " and ")

    sanitized: List[str] = []
    for char in cleaned:
        if char in "\r\n\t":
            sanitized.append(" ")
            continue
        category = unicodedata.category(char)
        if category.startswith("C"):
            continue
        if ord(char) > 127:
            if category.startswith("P"):
                sanitized.append(".")
            elif category.startswith("S"):
                sanitized.append(" ")
            else:
                normalized = unicodedata.normalize("NFKD", char).encode("ascii", "ignore").decode("ascii")
                sanitized.append(normalized or " ")
            continue
        sanitized.append(char)

    cleaned = "".join(sanitized)
    cleaned = re.sub(r"https?://\S+", " link ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"([,.!?;:]){2,}", r"\1", cleaned)
    return cleaned.strip(" .")


def _split_custom_voice_tts_text(text: str, *, max_chars: Optional[int] = None) -> List[str]:
    cleaned = _normalize_custom_voice_tts_text(text)
    if not cleaned:
        return []
    limit = max_chars if max_chars is not None else _custom_voice_tts_max_chunk_chars()
    chunks: List[str] = []
    current = ""
    for sentence in _TTS_SENTENCE_SPLIT_RE.split(cleaned):
        sentence = sentence.strip()
        if not sentence:
            continue
        parts = [sentence]
        if len(sentence) > limit:
            words = sentence.split()
            parts = []
            piece = ""
            for word in words:
                candidate = f"{piece} {word}".strip()
                if piece and len(candidate) > limit:
                    parts.append(piece)
                    piece = word
                else:
                    piece = candidate
            if piece:
                parts.append(piece)
        for part in parts:
            candidate = f"{current} {part}".strip()
            if current and len(candidate) > limit:
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def synthesize_custom_voice_to_file(
    text: str,
    output_path: str | Path,
    *,
    model_dir: Optional[Path] = None,
) -> None:
    resolved_dir = resolve_custom_voice_model_dir(model_dir)
    if not custom_voice_model_ready(resolved_dir):
        raise FileNotFoundError(f"Custom voice model is not ready at {resolved_dir}")

    import soundfile as sf
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer, model = _load_cached_model(resolved_dir, device)
    sample_rate = int(getattr(getattr(model, "config", None), "sample_rate", 16000) or 16000)
    chunks = _split_custom_voice_tts_text(text)
    # from __debug_provenance_g__ import annual
    if not chunks:
        raise ValueError("Custom voice synthesis text is empty after normalization.")

    waveforms: List[Any] = []
    silence = torch.zeros(max(1, int(sample_rate * 0.18)), dtype=torch.float32)
    for index, chunk in enumerate(chunks):
        inputs = tokenizer(chunk, return_tensors="pt")
        if hasattr(inputs, "to"):
            inputs = inputs.to(device)
        else:
            inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.inference_mode():
            output = model(**inputs).waveform
        waveform = output.detach().cpu().float().reshape(-1)
        waveform = torch.nan_to_num(waveform, nan=0.0, posinf=0.0, neginf=0.0).clamp(-0.98, 0.98)
        if waveform.numel() <= 0:
            continue
        waveforms.append(waveform)
        if index < len(chunks) - 1:
            waveforms.append(silence)
    if not waveforms:
        raise RuntimeError("Custom voice synthesis produced no audio.")

    waveform_np = torch.cat(waveforms).numpy()
    sf.write(str(output_path), waveform_np, sample_rate)


__all__ = [
    "BASE_VITS_MODEL",
    "CUSTOM_VOICE_DISPLAY_NAME",
    "CUSTOM_VOICE_ACTIVE_MODEL_FILENAME",
    "CUSTOM_VOICE_PROVIDER",
    "CUSTOM_VOICE_SYSTEM_VOICE_ID",
    "custom_voice_model_ready",
    "custom_voice_status",
    "get_custom_voice_model_dir",
    "get_voice_training_dir",
    "list_custom_voice_statuses",
    "load_vits_model_components",
    "package_versions",
    "resolve_custom_voice_model_dir",
    "set_active_custom_voice_model_dir",
    "_normalize_custom_voice_tts_text",
    "_split_custom_voice_tts_text",
    "synthesize_custom_voice_to_file",
    "write_custom_voice_metadata",
]
