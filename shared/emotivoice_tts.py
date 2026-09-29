# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-3a11462fdabfbe95a7c7048e

"""Local EmotiVoice inference adapter. Model checkpoints remain user-managed data."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import importlib.machinery
import importlib.util
import logging
import os
import re
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-3a11462fdabfbe95a7c7048e"


LOGGER = logging.getLogger(__name__)
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
_MODEL_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()  # ponytail: one model/device serializes requests; per-device queues if throughput matters
_MODEL: tuple[Any, Any, Any, dict[str, int], dict[str, int], Any, Any] | None = None

_RUNTIME_MODULES = (
    "torch", "transformers", "yacs", "g2p_en", "jieba", "pypinyin",
    "pypinyin_dict", "cn2an", "numba", "soundfile", "nltk",
    "scipy",
)
_DOWNLOAD_MODULES = ("modelscope", "huggingface_hub", "nltk", "tqdm")
_EMOTION_WORDS = {
    "angry": ("angry", "anger", "furious", "mad", "annoyed", "frustrated", "生气", "愤怒", "烦死", "讨厌"),
    "sad": ("sad", "sorry", "grief", "lonely", "unhappy", "悲伤", "难过", "伤心", "遗憾", "抱歉"),
    "happy": ("happy", "glad", "great", "love", "thanks", "thank you", "wonderful", "开心", "高兴", "喜欢", "谢谢", "太棒"),
    "excited": ("excited", "surprised", "amazing", "wow", "awesome", "惊讶", "惊喜", "兴奋", "太好了"),
}
_EMOTION_PROMPTS = {"angry": "Angry", "sad": "Sad", "happy": "Happy", "excited": "Excited"}
# simbert-base-chinese has 512 position embeddings, so the content embedding of a
# long reply fails with a tensor size mismatch. Synthesize in chunks well under it
# ([CLS]/[SEP] included) and join the audio with a short pause.
_CHUNK_TOKEN_BUDGET = 256
_CHUNK_PAUSE_SECONDS = 0.2
_SAMPLE_RATE = 16_000
# Sentences, then words, then characters for anything that still does not fit.
_CHUNK_LEVELS = (
    (re.compile(r"(?<=[.!?;])\s+|(?<=[。！？；])").split, " "),
    (str.split, " "),
    (list, ""),
)


def vendor_root() -> Path:
    return Path(__file__).resolve().parents[1] / "vendor" / "emotivoice"


def model_root() -> Path:
    from shared.voice_training_storage import get_voice_training_dir

    return get_voice_training_dir() / "models" / "emotivoice"


def conversation_emotion_prompt(context: str) -> str:
    text = str(context or "").casefold()
    matches = {
        emotion: sum(text.count(word) for word in words)
        for emotion, words in _EMOTION_WORDS.items()
    }
    selected = max(matches, key=matches.get)
    return _EMOTION_PROMPTS[selected] if matches[selected] else "Neutral"


def split_for_synthesis(
    text: str,
    count_tokens: Callable[[str], int],
    budget: int = _CHUNK_TOKEN_BUDGET,
    level: int = 0,
) -> list[str]:
    """Greedily pack sentences into chunks whose token count stays within ``budget``."""
    split, joiner = _CHUNK_LEVELS[level]
    chunks: list[str] = []
    current = ""
    for unit in filter(None, (part.strip() for part in split(text))):
        if count_tokens(unit) > budget and level + 1 < len(_CHUNK_LEVELS):
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(split_for_synthesis(unit, count_tokens, budget, level + 1))
            continue
        candidate = f"{current}{joiner}{unit}" if current else unit
        if not current or count_tokens(candidate) <= budget:
            current = candidate
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def _torch_mps_available(torch_module: Any) -> bool:
    try:
        return bool(torch_module.backends.mps.is_available())
    except (AttributeError, RuntimeError):
        return False


def _torch_cuda_available(torch_module: Any) -> bool:
    try:
        return bool(torch_module.cuda.is_available())
    except (AttributeError, RuntimeError):
        return False


def _select_torch_device(torch_module: Any) -> tuple[Any, str]:
    requested = str(os.getenv("AUTOYOU_EMOTIVOICE_DEVICE", "auto") or "auto").strip().casefold()
    if requested not in {"auto", "cuda", "mps", "cpu"}:
        requested = "auto"

    if requested in {"auto", "cuda"} and _torch_cuda_available(torch_module):
        return torch_module.device("cuda"), "cuda"
    if requested in {"auto", "mps"} and _torch_mps_available(torch_module):
        return torch_module.device("mps"), "mps"
    return torch_module.device("cpu"), "cpu"


@lru_cache(maxsize=1)
def _acceleration_status() -> Dict[str, Any]:
    if importlib.util.find_spec("torch") is None:
        return {
            "selected_device": "unavailable",
            "torch_mps_available": False,
            "torch_cuda_available": False,
            "mlx_available": importlib.util.find_spec("mlx") is not None,
            "mlx_supported": False,
        }
    try:
        import torch
    except Exception:
        return {
            "selected_device": "unavailable",
            "torch_mps_available": False,
            "torch_cuda_available": False,
            "mlx_available": importlib.util.find_spec("mlx") is not None,
            "mlx_supported": False,
        }

    _device, device_name = _select_torch_device(torch)
    return {
        "selected_device": device_name,
        "torch_mps_available": _torch_mps_available(torch),
        "torch_cuda_available": _torch_cuda_available(torch),
        "mlx_available": importlib.util.find_spec("mlx") is not None,
        "mlx_supported": False,
    }


def _runtime_file_exists(relative_path: str) -> bool:
    path = vendor_root() / relative_path
    if path.is_file():
        return True
    module_path = path.with_suffix("")
    return any(
        module_path.with_name(module_path.name + suffix).is_file()
        for suffix in importlib.machinery.EXTENSION_SUFFIXES
    )


def status() -> Dict[str, Any]:
    root = model_root()
    required_runtime_files = (
        "frontend.py",
        "frontend_cn.py",
        "frontend_en.py",
        "models/prompt_tts_modified/jets.py",
        "models/prompt_tts_modified/model_open_source.py",
        "models/prompt_tts_modified/simbert.py",
        "models/prompt_tts_modified/modules/alignment.py",
        "models/prompt_tts_modified/modules/encoder.py",
        "models/prompt_tts_modified/modules/initialize.py",
        "models/prompt_tts_modified/modules/variance.py",
        "models/hifigan/models.py",
        "models/hifigan/get_random_segments.py",
        "config/joint/config.py",
        "config/joint/config.yaml",
        "lexicon/librispeech-lexicon.txt",
        "data/youdao/text/emotion",
        "data/youdao/text/energy",
        "data/youdao/text/pitch",
        "data/youdao/text/speaker2",
        "data/youdao/text/speed",
        "data/youdao/text/tokenlist",
    )
    runtime_available = all(_runtime_file_exists(path) for path in required_runtime_files)
    output = root / "outputs"
    bert = root / "simbert-base-chinese"
    # from __debug_provenance_i__ import or
    nltk_data = root / "nltk_data"
    weight_paths = (
        output / "prompt_tts_open_source_joint" / "ckpt" / "g_00140000",
        output / "style_encoder" / "ckpt" / "checkpoint_163431",
    )
    bert_ready = (bert / "config.json").is_file() and any(
        (bert / filename).is_file() for filename in ("model.safetensors", "pytorch_model.bin")
    ) and (bert / "vocab.txt").is_file()
    nltk_resources = (
        nltk_data / "taggers" / "averaged_perceptron_tagger",
        nltk_data / "taggers" / "averaged_perceptron_tagger_eng",
        nltk_data / "corpora" / "cmudict",
    )
    nltk_ready = all(
        path.is_dir() or path.with_suffix(path.suffix + ".zip").is_file()
        for path in nltk_resources
    )
    missing_modules = [name for name in _RUNTIME_MODULES if importlib.util.find_spec(name) is None]
    model_ready = all(path.is_file() for path in weight_paths) and bert_ready and nltk_ready
    speakers_path = vendor_root() / "data" / "youdao" / "text" / "speaker2"
    try:
        speakers = [line.strip() for line in speakers_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        speakers = []
    return {
        "ready": model_ready and not missing_modules and runtime_available,
        "models_ready": model_ready,
        "missing_dependencies": missing_modules,
        "runtime_available": runtime_available,
        "runtime_source_ready": runtime_available,
        "download_supported": all(importlib.util.find_spec(name) is not None for name in _DOWNLOAD_MODULES),
        "acceleration": _acceleration_status(),
        "speaker_ids": speakers,
        "model_dir": str(root),
        "model_sources": ["syq163/outputs (ModelScope)", "WangZeJun/simbert-base-chinese (Hugging Face)"],
        "model_license_note": "Checkpoint model cards do not clearly state a license; review upstream terms before downloading or using them.",
    }


def is_ready() -> bool:
    return bool(status()["ready"])


def _load_model():
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL

        root = vendor_root()
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)

        data_root = model_root()
        nltk_data = data_root / "nltk_data"
        os.environ["NLTK_DATA"] = str(nltk_data)
        import nltk

        if str(nltk_data) not in nltk.data.path:
            nltk.data.path.insert(0, str(nltk_data))

        # Upstream modules use top-level ``models`` and ``frontend`` imports.
        for name in ("models", "frontend", "frontend_cn", "frontend_en"):
            loaded = sys.modules.get(name)
            loaded_from = str(getattr(loaded, "__file__", "") or "")
            if loaded is not None and not loaded_from.startswith(root_text):
                raise RuntimeError(f"Cannot load EmotiVoice because another '{name}' module is already active")

        import numpy as np
        import soundfile as sf
        import torch
        from transformers import BertTokenizer
        from yacs import config as yacs_config
        from frontend import g2p_cn_en
        from frontend_en import G2p, read_lexicon
        from models.prompt_tts_modified.jets import JETSGenerator
        from models.prompt_tts_modified.simbert import StyleEncoder
        from vendor.emotivoice.config.joint.config import Config

        config = Config()
        config.bert_path = str(data_root / "simbert-base-chinese")
        config.style_encoder_ckpt = str(data_root / "outputs" / "style_encoder" / "ckpt" / "checkpoint_163431")
        model_config = root / "config" / "joint" / "config.yaml"
        with model_config.open("r", encoding="utf-8") as handle:
            model_config_data = yacs_config.load_cfg(handle)
        model_config_data.n_vocab = config.n_symbols
        model_config_data.n_speaker = config.speaker_n_labels

        device, _device_name = _select_torch_device(torch)
        style_encoder = StyleEncoder(config).to(device)
        style_checkpoint = torch.load(config.style_encoder_ckpt, map_location="cpu", weights_only=True)
        style_state = {key[7:]: value for key, value in style_checkpoint["model"].items() if key.startswith("module.")}
        style_encoder.load_state_dict(style_state, strict=False)
        style_encoder.eval()

        generator = JETSGenerator(model_config_data).to(device)
        generator_checkpoint = data_root / "outputs" / "prompt_tts_open_source_joint" / "ckpt" / "g_00140000"
        checkpoint = torch.load(generator_checkpoint, map_location="cpu", weights_only=True)
        generator.load_state_dict(checkpoint["generator"])
        generator.eval()

        tokenizer = BertTokenizer.from_pretrained(config.bert_path, local_files_only=True)
        token_list = (root / "data" / "youdao" / "text" / "tokenlist").read_text(encoding="utf-8").splitlines()
        token_to_id = {token.strip(): index for index, token in enumerate(token_list)}
        speaker_list = (root / "data" / "youdao" / "text" / "speaker2").read_text(encoding="utf-8").splitlines()
        speaker_to_id = {speaker.strip(): index for index, speaker in enumerate(speaker_list)}
        lexicon = read_lexicon(str(root / "lexicon" / "librispeech-lexicon.txt"))
        g2p = G2p()
        _MODEL = (torch, np, sf, token_to_id, speaker_to_id, (style_encoder, generator, tokenizer), (g2p_cn_en, g2p, lexicon, device))
        return _MODEL


def synthesize(text: str, output_path: str, settings: Dict[str, Any], context: str = "") -> None:
    voice_status = status()
    if not voice_status["runtime_available"]:
        raise RuntimeError("EmotiVoice is not included in this server build; install a full voice profile")
    if not voice_status["ready"]:
        raise RuntimeError("EmotiVoice is not ready; install its voice models and runtime dependencies first")
    torch, np, sf, token_to_id, speaker_to_id, models, frontend = _load_model()
    style_encoder, generator, tokenizer = models
    g2p_cn_en, g2p, lexicon, device = frontend
    tts_config = settings.get("tts", {}) if isinstance(settings, dict) else {}
    emo_config = tts_config.get("emotivoice", {}) if isinstance(tts_config, dict) else {}
    speaker_name = str(emo_config.get("speaker") or "8051").strip()
    if speaker_name not in speaker_to_id:
        raise ValueError("Unknown EmotiVoice speaker ID")
    prompt = conversation_emotion_prompt(context or text) if emo_config.get("conversation_emotion", True) else "Neutral"
    chunks = split_for_synthesis(str(text), lambda value: len(tokenizer.tokenize(value)) + 2)
    chunk_phonemes = [g2p_cn_en(chunk, g2p, lexicon).split() for chunk in chunks]
    unknown = [phone for phonemes in chunk_phonemes for phone in phonemes if phone not in token_to_id]
    if unknown:
        raise ValueError(f"EmotiVoice cannot encode phoneme token: {unknown[0]}")

    def style_embedding(batch):
        return style_encoder(
            input_ids=batch["input_ids"].to(device),
            token_type_ids=batch["token_type_ids"].to(device),
            attention_mask=batch["attention_mask"].to(device),
        )["pooled_output"]

    pieces = []
    pause = np.zeros(int(_SAMPLE_RATE * _CHUNK_PAUSE_SECONDS), dtype=np.float32)
    for chunk, phonemes in zip(chunks, chunk_phonemes):
        # Only <sos/eos> left means the chunk had nothing speakable (e.g. bare punctuation).
        if len(phonemes) <= 2:
            continue
        with _INFERENCE_LOCK, torch.inference_mode():
            prompt_embedding = style_embedding(tokenizer([prompt], return_tensors="pt"))
            content_embedding = style_embedding(tokenizer([chunk], return_tensors="pt"))
            sequence = torch.tensor([[token_to_id[phone] for phone in phonemes]], dtype=torch.long, device=device)
            lengths = torch.tensor([sequence.shape[1]], dtype=torch.long, device=device)
            speakers = torch.tensor([speaker_to_id[speaker_name]], dtype=torch.long, device=device)
            result = generator(
                inputs_ling=sequence,
                input_lengths=lengths,
                inputs_speaker=speakers,
                inputs_style_embedding=prompt_embedding,
                inputs_content_embedding=content_embedding,
                alpha=1.0,
            )["wav_predictions"]
            piece = result.detach().squeeze().float().cpu().numpy().reshape(-1)
        if pieces:
            pieces.append(pause)
        pieces.append(piece)
    audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    if audio.size == 0 or not np.isfinite(audio).all():
        raise RuntimeError("EmotiVoice produced invalid audio")
    samples = np.clip(audio * 32768.0, -32768, 32767).astype(np.int16)
    try:
        rate = float(tts_config.get("rate", 1.0))
    except (TypeError, ValueError):
        rate = 1.0
    if rate != 1.0:
        from fractions import Fraction
        from scipy.signal import resample_poly

        ratio = Fraction(1.0 / min(4.0, max(0.25, rate))).limit_denominator(1000)
        samples = np.clip(resample_poly(samples.astype(np.float32), ratio.numerator, ratio.denominator), -32768, 32767).astype(np.int16)
    sf.write(output_path, samples, _SAMPLE_RATE, format="WAV", subtype="PCM_16")
