# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-534454206164647265737320-29b9aebb45144b1db5ba8e2d

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-534454206164647265737320-29b9aebb45144b1db5ba8e2d"


import argparse
import contextlib
import gc
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from shared.custom_voice_tts import (
    BASE_VITS_MODEL,
    custom_voice_model_ready,
    custom_voice_status,
    load_vits_model_components,
    package_versions,
    set_active_custom_voice_model_dir,
    write_custom_voice_metadata,
)
from shared.voice_training_quality import analyze_wav_file, voice_training_rejection_reasons
from shared.secure_storage import (
    SecureStorageError,
    append_secure_file,
    enable_secure_storage_from_environment,
    load_secure_json,
    materialize_secure_file,
    read_secure_file,
    save_secure_json,
    secure_storage_enabled,
    write_secure_file,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_TRAINING_EPOCHS = 200
TRAINING_STRATEGY_INTERNAL = "mms_vits_waveform_reconstruction_finetune"
TRAINING_STRATEGY_EXTERNAL = "external_custom_voice_trainer"
TRAINING_STRATEGY_PREPARE_ONLY = "base_vits_provider_prepare"

_STATUS_LOCK = threading.Lock()
_TRAINING_THREAD: Optional[threading.Thread] = None

@dataclass(frozen=True)
class VoiceTrainingExample:
    sample_id: str
    text: str
    audio_path: Path
    source: str
    duration_seconds: float = 0.0
    quality: Dict[str, Any] = field(default_factory=dict)

def _get_voice_training_dirs():
    from shared.voice_training_storage import get_voice_training_dir

    vt_dir = get_voice_training_dir()
    model_dir = vt_dir / "models" / "custom_voice"
    status_file = vt_dir / "training_status.json"
    transcripts_file = vt_dir / "transcripts.json"
    return vt_dir, model_dir, status_file, transcripts_file

def get_status() -> Dict[str, Any]:
    _, _, status_file, _ = _get_voice_training_dirs()
    if status_file.exists():
        try:
            status = load_secure_json(status_file, default={})
            if isinstance(status, dict):
                status["custom_voice"] = custom_voice_status()
                if _training_status_is_stale(status):
                    stale_status = dict(status)
                    stale_status["status"] = "interrupted"
                    stale_status["training_stale"] = True
                    stale_status["message"] = (
                        "Voice training was interrupted before completion. "
                        "Start training again to resume from the current dataset."
                    )
                    stale_status["error"] = stale_status.get("error") or (
                        "The training worker process is no longer running."
                    )
                    return stale_status
                status["training_active"] = bool(status.get("status") == "training")
                return status
        except SecureStorageError:
            raise
        except Exception:
            pass
    return {
        "status": "idle",
        "progress": 0.0,
        "current_epoch": 0,
        "total_epochs": 0,
        "message": "No training in progress.",
        "error": "",
        "custom_voice": custom_voice_status(),
    }

def _training_stale_after_seconds() -> float:
    try:
        return max(1.0, float(os.environ.get("AUTOYOU_VOICE_TRAINING_STALE_AFTER_SECONDS", "600")))
    except Exception:
        return 600.0

def _pid_exists(pid_value: Any) -> bool:
    try:
        pid = int(pid_value)
    except Exception:
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True

    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid) and psutil.Process(pid).is_running())
    except Exception:
        pass

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_process.restype = wintypes.HANDLE
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            handle = open_process(process_query_limited_information, False, pid)
            if handle:
                close_handle(handle)
                return True
        except Exception:
            return False
        return False

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False

def _training_thread_alive() -> bool:
    thread = _TRAINING_THREAD
    return bool(thread is not None and thread.is_alive())

def _training_status_is_stale(status: Dict[str, Any], *, now: Optional[float] = None) -> bool:
    if status.get("status") != "training":
        return False

    worker_pid = status.get("worker_pid") or status.get("owner_pid")
    if worker_pid:
        return not _pid_exists(worker_pid)
    if _training_thread_alive():
        return False

    timestamp = status.get("heartbeat_at") or status.get("timestamp")
    try:
        last_update = float(timestamp)
    except Exception:
        return bool(worker_pid)
    if last_update <= 0:
        return bool(worker_pid)

    current_time = time.time() if now is None else float(now)
    return (current_time - last_update) > _training_stale_after_seconds()

def _update_status(
    status: str,
    progress: float,
    current_epoch: int,
    total_epochs: int,
    message: str,
    error: str = "",
    **extra: Any,
) -> None:
    _, _, status_file, _ = _get_voice_training_dirs()
    status_data = {
        "status": status,
        "progress": progress,
        "current_epoch": current_epoch,
        "total_epochs": total_epochs,
        "message": message,
        "error": error,
        "custom_voice": custom_voice_status(),
        "timestamp": time.time(),
        "heartbeat_at": time.time(),
        "worker_pid": os.getpid(),
    }
    status_data.update(extra)
    try:
        status_file.parent.mkdir(parents=True, exist_ok=True)
        with _STATUS_LOCK:
            save_secure_json(status_file, status_data)
    except SecureStorageError:
        raise
    except Exception as exc:
        LOGGER.error("Failed to write voice training status: %s", exc)

def _load_transcripts(transcripts_file: Path) -> List[Dict[str, Any]]:
    if not transcripts_file.exists():
        return []
    try:
        loaded = load_secure_json(transcripts_file, default=[])
        return loaded if isinstance(loaded, list) else []
    except SecureStorageError:
        raise
    except Exception as exc:
        LOGGER.warning("Could not read voice transcripts from %s: %s", transcripts_file, exc)
        return []

def _clean_training_text(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)

def _safe_sample_id(value: Any, fallback_index: int) -> str:
    raw = str(value or "").strip() or f"sample-{fallback_index:04d}"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
    return safe[:80] or f"sample-{fallback_index:04d}"

def _audio_duration_seconds(audio_path: Path) -> float:
    try:
        with materialize_secure_file(audio_path) as readable_path:
            with wave.open(str(readable_path), "rb") as wf:
                frames = wf.getnframes()
                sample_rate = wf.getframerate() or 0
                if frames > 0 and sample_rate > 0:
                    return float(frames) / float(sample_rate)
    except SecureStorageError:
        raise
    except Exception:
        pass
    return 0.0

def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False

def _recording_candidates(vt_dir: Path, recordings_dir: Path, item: Dict[str, Any]) -> Iterable[Path]:
    values = (
        item.get("filename"),
        item.get("audio_filename"),
        item.get("audio_path"),
        item.get("file_path"),
        item.get("path"),
    )
    for value in values:
        if not value:
            continue
        raw = Path(str(value))
        if raw.is_absolute():
            yield raw
            continue
        if raw.name:
            yield recordings_dir / raw.name
        yield vt_dir / raw

def _resolve_recording_path(vt_dir: Path, recordings_dir: Path, item: Dict[str, Any]) -> Optional[Path]:
    for candidate in _recording_candidates(vt_dir, recordings_dir, item):
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if not _is_relative_to(resolved, vt_dir):
            continue
        if resolved.exists() and resolved.is_file():
            return resolved
    return None

def _build_training_examples(vt_dir: Path, transcripts: List[Dict[str, Any]]) -> List[VoiceTrainingExample]:
    recordings_dir = vt_dir / "recordings"
    examples: List[VoiceTrainingExample] = []
    seen_paths: set[str] = set()
    for index, item in enumerate(transcripts, start=1):
        if not isinstance(item, dict):
            continue
        text = _clean_training_text(item.get("transcript") or item.get("text") or item.get("label"))
        if not text:
            continue
        audio_path = _resolve_recording_path(vt_dir, recordings_dir, item)
        if audio_path is None:
            continue
        try:
            path_key = str(audio_path.resolve())
        except Exception:
            path_key = str(audio_path)
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        quality: Dict[str, Any] = {}
        duration_seconds = _audio_duration_seconds(audio_path)
        try:
            with materialize_secure_file(audio_path) as readable_path:
                quality = analyze_wav_file(readable_path).to_dict()
            duration_seconds = float(quality.get("duration_seconds") or duration_seconds)
        except SecureStorageError:
            raise
        except Exception:
            quality = {}
        examples.append(
            VoiceTrainingExample(
                sample_id=_safe_sample_id(item.get("id"), index),
                text=text,
                audio_path=audio_path,
                source=str(item.get("source") or "unknown"),
                duration_seconds=duration_seconds,
                quality=quality,
            )
        )
    return examples

def _export_training_dataset(vt_dir: Path, examples: List[VoiceTrainingExample]) -> Dict[str, str]:
    dataset_dir = vt_dir / "dataset"
    wavs_dir = dataset_dir / "wavs"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    wavs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = dataset_dir / "manifest.jsonl"
    metadata_csv_path = dataset_dir / "metadata.csv"

    manifest_lines: list[str] = []
    metadata_lines: list[str] = []
    for index, example in enumerate(examples, start=1):
        target_name = f"{index:05d}_{example.sample_id}.wav"
        target_path = wavs_dir / target_name
        if not target_path.exists() or target_path.stat().st_mtime < example.audio_path.stat().st_mtime:
            with materialize_secure_file(example.audio_path) as readable_path:
                write_secure_file(target_path, readable_path.read_bytes())
        manifest_lines.append(
            json.dumps(
                {
                    "id": example.sample_id,
                    "audio_filepath": str(target_path),
                    "text": example.text,
                    "source": example.source,
                    "duration_seconds": example.duration_seconds,
                    "quality": example.quality,
                },
                ensure_ascii=False,
            )
        )
        metadata_lines.append(f"wavs/{target_name}|{example.text.replace('|', ' ')}")

    write_secure_file(manifest_path, ("\n".join(manifest_lines) + "\n").encode("utf-8"))
    write_secure_file(metadata_csv_path, ("\n".join(metadata_lines) + "\n").encode("utf-8"))

    return {
        "dataset_dir": str(dataset_dir),
        "manifest_jsonl": str(manifest_path),
        "metadata_csv": str(metadata_csv_path),
    }

def _training_strategy() -> str:
    strategy = os.environ.get("AUTOYOU_VOICE_TRAINING_STRATEGY", "").strip().lower()
    if strategy in {"external", "command"}:
        return TRAINING_STRATEGY_EXTERNAL
    if strategy in {"prepare", "prepare_only", "provider_prepare", "provider-prep"}:
        return TRAINING_STRATEGY_PREPARE_ONLY
    prepare_only = os.environ.get("AUTOYOU_VOICE_TRAINING_PREPARE_ONLY", "").strip().lower()
    if prepare_only in {"1", "true", "yes"}:
        return TRAINING_STRATEGY_PREPARE_ONLY
    if os.environ.get("AUTOYOU_VOICE_TRAINING_COMMAND", "").strip():
        return TRAINING_STRATEGY_EXTERNAL
    return TRAINING_STRATEGY_INTERNAL

def _save_base_provider_artifact(model_dir: Path) -> None:
    VitsModel, AutoTokenizer = load_vits_model_components()
    tokenizer = AutoTokenizer.from_pretrained(BASE_VITS_MODEL)
    model = VitsModel.from_pretrained(BASE_VITS_MODEL)
    model_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(model_dir))
    tokenizer.save_pretrained(str(model_dir))

def _run_external_training_command(
    *,
    vt_dir: Path,
    model_dir: Path,
    dataset_artifacts: Dict[str, str],
    epochs: int,
) -> Dict[str, Any]:
    command_template = os.environ.get("AUTOYOU_VOICE_TRAINING_COMMAND", "").strip()
    if not command_template:
        raise RuntimeError(
            "AUTOYOU_VOICE_TRAINING_COMMAND is required when AUTOYOU_VOICE_TRAINING_STRATEGY=external. "
            "The command must write a Hugging Face VITS-compatible model into the output directory."
        )
    model_dir.mkdir(parents=True, exist_ok=True)
    command = command_template.format(
        base_model=BASE_VITS_MODEL,
        dataset_dir=dataset_artifacts["dataset_dir"],
        manifest=dataset_artifacts["manifest_jsonl"],
        metadata_csv=dataset_artifacts["metadata_csv"],
        output_dir=str(model_dir),
        epochs=str(epochs),
        voice_training_dir=str(vt_dir),
    )
    LOGGER.info("Starting external custom voice trainer command.")
    completed = subprocess.run(
        command,
        shell=True,
        cwd=str(vt_dir),
        capture_output=True,
        text=True,
        timeout=None,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(f"External custom voice trainer failed: {detail[-4000:]}")
    if not custom_voice_model_ready(model_dir):
        raise RuntimeError("External custom voice trainer completed but did not write loadable model weights.")
    return {
        "training_strategy": TRAINING_STRATEGY_EXTERNAL,
        "fine_tuning_applied": True,
        "external_command": command_template,
        "trainer_stdout_tail": (completed.stdout or "")[-4000:],
    }

def _audio_to_tensor(audio_path: Path, sample_rate: int, device: str, *, max_seconds: Optional[float] = None):
    import numpy as np
    import soundfile as sf
    import torch

    with materialize_secure_file(audio_path) as readable_path:
        frames = -1
        try:
            if max_seconds is not None and float(max_seconds) > 0:
                info = sf.info(str(readable_path))
                source_rate_hint = int(getattr(info, "samplerate", 0) or sample_rate)
                frames = max(1, int(math.ceil(float(max_seconds) * float(source_rate_hint))))
        except Exception:
            frames = -1

        audio_np, source_rate = sf.read(str(readable_path), dtype="float32", always_2d=False, frames=frames)
    if getattr(audio_np, "ndim", 1) > 1:
        audio_np = np.mean(audio_np, axis=1)
    if audio_np.size == 0:
        raise ValueError(f"Audio sample is empty: {audio_path.name}")

    audio = torch.tensor(audio_np, dtype=torch.float32, device=device)
    if int(source_rate or sample_rate) != sample_rate:
        audio = _resample_1d(audio, int(source_rate), sample_rate)
    peak = torch.max(torch.abs(audio)).clamp_min(1e-5)
    return (audio / peak).clamp(-1.0, 1.0) * 0.95

def _resample_1d(audio, source_rate: int, target_rate: int):
    import torch.nn.functional as F

    if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate:
        return audio
    try:
        import torchaudio.functional as audio_functional

        return audio_functional.resample(audio.detach().cpu(), source_rate, target_rate).to(audio.device)
    except Exception:
        target_len = max(1, int(round(audio.shape[-1] * float(target_rate) / float(source_rate))))
        return F.interpolate(audio.view(1, 1, -1), size=target_len, mode="linear", align_corners=False).view(-1)

def _match_target_length(target, output_length: int):
    import torch.nn.functional as F

    if target.shape[-1] == output_length:
        return target
    if output_length <= 0:
        return target[:0]
    return F.interpolate(target.view(1, 1, -1), size=output_length, mode="linear", align_corners=False).view(-1)

def _multi_resolution_stft_loss(predicted, target):
    import torch
    import torch.nn.functional as F

    length = int(min(predicted.shape[-1], target.shape[-1]))
    if length <= 16:
        return F.l1_loss(predicted, target)
    predicted = predicted[:length]
    target = target[:length]
    losses = []
    for n_fft, hop_length, win_length in ((256, 64, 256), (512, 128, 512), (1024, 256, 1024)):
        if length < n_fft:
            continue
        window = torch.hann_window(win_length, device=predicted.device)
        pred_stft = torch.stft(
            predicted,
            n_fft=n_fft,
            hop_length=hop_length,
            win_length=win_length,
            window=window,
            return_complex=True,
        )
        target_stft = torch.stft(
            target,
            n_fft=n_fft,
            hop_length=hop_length,
            win_length=win_length,
            window=window,
            return_complex=True,
        )
        losses.append(F.l1_loss(torch.log1p(torch.abs(pred_stft)), torch.log1p(torch.abs(target_stft))))
    if not losses:
        return F.l1_loss(predicted, target)
    return sum(losses) / float(len(losses))

def _trainable_parameter_count(model: Any) -> int:
    try:
        return int(sum(param.numel() for param in model.parameters() if param.requires_grad))
    except Exception:
        return 0

def _prepare_resume_model_source(model_dir: Path) -> Path:
    """Copy an existing checkpoint before loading so Windows can overwrite output files.

    Safetensors may memory-map ``model.safetensors``. Loading directly from the
    output directory and later saving into that same directory can fail on
    Windows with "user-mapped section open".
    """
    resume_root = model_dir.parent / "_resume_sources"
    resume_root.mkdir(parents=True, exist_ok=True)
    destination = resume_root / f"custom_voice_{os.getpid()}_{int(time.time())}"
    shutil.copytree(model_dir, destination, dirs_exist_ok=False)
    return destination

def _prepare_training_output_dir(model_dir: Path) -> Path:
    output_root = model_dir.parent / "_training_outputs"
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / f"custom_voice_{os.getpid()}_{int(time.time())}"
    suffix = 1
    while destination.exists():
        suffix += 1
        destination = output_root / f"custom_voice_{os.getpid()}_{int(time.time())}_{suffix}"
    destination.mkdir(parents=True)
    return destination

def _configure_torch_training_runtime(torch_module: Any, device: str) -> Dict[str, Any]:
    runtime: Dict[str, Any] = {"device": device}
    if device != "cpu":
        return runtime

    disable_mkldnn = os.environ.get("AUTOYOU_VOICE_TRAINING_DISABLE_MKLDNN", "1").strip().lower()
    if disable_mkldnn not in {"0", "false", "no", "off"}:
        try:
            mkldnn_backend = getattr(getattr(torch_module, "backends", None), "mkldnn", None)
            if mkldnn_backend is not None and hasattr(mkldnn_backend, "enabled"):
                runtime["mkldnn_before"] = bool(mkldnn_backend.enabled)
                mkldnn_backend.enabled = False
                runtime["mkldnn_enabled"] = bool(mkldnn_backend.enabled)
        except Exception as exc:
            runtime["mkldnn_error"] = str(exc)

    thread_value = os.environ.get("AUTOYOU_VOICE_TRAINING_TORCH_THREADS", "").strip()
    if thread_value:
        try:
            threads = max(1, int(thread_value))
            if hasattr(torch_module, "set_num_threads"):
                torch_module.set_num_threads(threads)
            if hasattr(torch_module, "set_num_interop_threads"):
                try:
                    torch_module.set_num_interop_threads(max(1, min(threads, 4)))
                except RuntimeError:
                    pass
        except Exception as exc:
            runtime["thread_config_error"] = str(exc)
    try:
        if hasattr(torch_module, "get_num_threads"):
            runtime["torch_threads"] = int(torch_module.get_num_threads())
    except Exception:
        pass
    return runtime

def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except Exception:
        return max(minimum, int(default))

def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.environ.get(name, str(default))))
    except Exception:
        return max(minimum, float(default))

def _env_enabled(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}

def _select_fine_tune_examples(
    examples: List[VoiceTrainingExample],
    *,
    max_examples: int,
    min_example_seconds: float,
    max_example_seconds: float,
    min_text_chars: int,
    max_text_chars: int,
    min_speech_frame_ratio: float,
    min_rms_dbfs: float,
) -> tuple[List[VoiceTrainingExample], Dict[str, Any]]:
    skipped_short = 0
    skipped_duration = 0
    skipped_quiet = 0
    skipped_silent = 0
    skipped_text_short = 0
    skipped_text = 0
    eligible: List[VoiceTrainingExample] = []
    for example in examples:
        duration = float(example.duration_seconds or 0.0)
        quality = example.quality if isinstance(example.quality, dict) else {}
        speech_ratio = float(quality.get("speech_frame_ratio", 1.0))
        rms_dbfs = float(quality.get("rms_dbfs", 0.0))
        if min_example_seconds > 0 and duration < min_example_seconds:
            skipped_short += 1
            continue
        if max_example_seconds > 0 and duration > max_example_seconds:
            skipped_duration += 1
            continue
        if min_speech_frame_ratio > 0 and speech_ratio < min_speech_frame_ratio:
            skipped_silent += 1
            continue
        if rms_dbfs < min_rms_dbfs:
            skipped_quiet += 1
            continue
        if min_text_chars > 0 and len(example.text) < min_text_chars:
            skipped_text_short += 1
            continue
        if max_text_chars > 0 and len(example.text) > max_text_chars:
            skipped_text += 1
            continue
        eligible.append(example)

    selection_fallback = ""
    candidates = eligible
    if not candidates:
        allow_low_quality = _env_enabled("AUTOYOU_VOICE_TRAINING_ALLOW_LOW_QUALITY_SAMPLES", False)
        if not allow_low_quality:
            selection = {
                "full_dataset_sample_count": len(examples),
                "fine_tune_sample_count": 0,
                "skipped_short_count": skipped_short,
                "skipped_duration_count": skipped_duration,
                "skipped_quiet_count": skipped_quiet,
                "skipped_silent_count": skipped_silent,
                "skipped_text_short_count": skipped_text_short,
                "skipped_text_count": skipped_text,
                "min_example_seconds": min_example_seconds,
                "max_example_seconds": max_example_seconds,
                "min_text_chars": min_text_chars,
                "max_text_chars": max_text_chars,
                "min_speech_frame_ratio": min_speech_frame_ratio,
                "min_rms_dbfs": min_rms_dbfs,
                "max_examples": max_examples,
                "selection_fallback": "",
            }
            return [], selection
        selection_fallback = "No examples matched the quality limits; low-quality fallback was explicitly enabled."
        candidates = list(examples)

    def _score(item: VoiceTrainingExample) -> float:
        quality = item.quality if isinstance(item.quality, dict) else {}
        duration = float(item.duration_seconds or 0.0)
        speech_ratio = max(0.0, min(1.0, float(quality.get("speech_frame_ratio", 0.5))))
        rms_dbfs = float(quality.get("rms_dbfs", -60.0))
        text_len = len(item.text)
        target_seconds = 8.0
        duration_score = 1.0 - min(abs(duration - target_seconds) / target_seconds, 1.0)
        text_score = min(text_len / 120.0, 1.0)
        loudness_score = max(0.0, min((rms_dbfs + 45.0) / 25.0, 1.0))
        curated_score = 1.0 if item.source.lower() in {"upload", "manual", "curated"} else 0.0
        return curated_score * 2.0 + speech_ratio * 2.0 + duration_score + text_score + loudness_score

    ordered = sorted(candidates, key=lambda item: (-_score(item), item.sample_id))
    selected = ordered[:max_examples] if max_examples > 0 else ordered
    selection = {
        "full_dataset_sample_count": len(examples),
        "fine_tune_sample_count": len(selected),
        "skipped_short_count": skipped_short,
        "skipped_duration_count": skipped_duration,
        "skipped_quiet_count": skipped_quiet,
        "skipped_silent_count": skipped_silent,
        "skipped_text_short_count": skipped_text_short,
        "skipped_text_count": skipped_text,
        "min_example_seconds": min_example_seconds,
        "max_examples": max_examples,
        "max_example_seconds": max_example_seconds,
        "min_text_chars": min_text_chars,
        "max_text_chars": max_text_chars,
        "min_speech_frame_ratio": min_speech_frame_ratio,
        "min_rms_dbfs": min_rms_dbfs,
        "selected_duration_seconds": sum(float(example.duration_seconds or 0.0) for example in selected),
        "selection_fallback": selection_fallback,
    }
    return selected, selection

def _configure_trainable_parameters(model: Any) -> Dict[str, Any]:
    pattern = os.environ.get(
        "AUTOYOU_VOICE_TRAINING_TRAINABLE_PATTERN",
        "decoder|flow|duration_predictor",
    ).strip()
    if pattern.lower() in {"", "all", "*", "none"}:
        return {"trainable_scope": "all", "trainable_pattern": pattern or "all"}

    try:
        named_parameters = list(model.named_parameters())
    except Exception as exc:
        return {
            "trainable_scope": "all",
            "trainable_pattern": pattern,
            "trainable_scope_error": str(exc),
        }
    if not named_parameters:
        return {"trainable_scope": "all", "trainable_pattern": pattern, "trainable_parameter_names": 0}

    compiled = re.compile(pattern, re.IGNORECASE)
    trainable_names = 0
    frozen_names = 0
    trainable_values = 0
    frozen_values = 0
    for name, param in named_parameters:
        is_trainable = bool(compiled.search(name))
        try:
            param.requires_grad = is_trainable
        except Exception:
            pass
        count = int(param.numel()) if hasattr(param, "numel") else 0
        if is_trainable:
            trainable_names += 1
            trainable_values += count
        else:
            frozen_names += 1
            frozen_values += count

    if trainable_names <= 0:
        for _name, param in named_parameters:
            try:
                param.requires_grad = True
            except Exception:
                pass
        return {
            "trainable_scope": "all",
            "trainable_pattern": pattern,
            "trainable_scope_fallback": "Pattern matched no parameters.",
        }

    return {
        "trainable_scope": "pattern",
        "trainable_pattern": pattern,
        "trainable_parameter_names": trainable_names,
        "frozen_parameter_names": frozen_names,
        "trainable_parameter_values": trainable_values,
        "frozen_parameter_values": frozen_values,
    }

def _dataset_quality_summary(
    examples: List[VoiceTrainingExample],
    selected_examples: List[VoiceTrainingExample],
    selection: Dict[str, Any],
) -> Dict[str, Any]:
    source_counts: Dict[str, int] = {}
    for example in examples:
        source = str(example.source or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

    curated_sources = {"upload", "manual", "curated"}
    curated_selected = [
        example for example in selected_examples if str(example.source or "").strip().lower() in curated_sources
    ]
    selected_duration = float(sum(float(example.duration_seconds or 0.0) for example in selected_examples))
    min_install_samples = _env_int("AUTOYOU_VOICE_TRAINING_INSTALL_MIN_SAMPLES", 20, minimum=1)
    min_install_seconds = _env_float("AUTOYOU_VOICE_TRAINING_INSTALL_MIN_SECONDS", 180.0, minimum=0.0)
    min_curated_samples = _env_int("AUTOYOU_VOICE_TRAINING_INSTALL_MIN_CURATED_SAMPLES", 5, minimum=0)

    reasons: List[str] = []
    if len(selected_examples) < min_install_samples:
        reasons.append("not_enough_selected_samples")
    if selected_duration < min_install_seconds:
        reasons.append("not_enough_selected_speech_duration")
    if len(curated_selected) < min_curated_samples:
        reasons.append("not_enough_curated_samples")
    if selection.get("selection_fallback"):
        reasons.append("low_quality_fallback_used")

    return {
        "source_counts": source_counts,
        "selected_sample_count": len(selected_examples),
        "selected_duration_seconds": selected_duration,
        "curated_selected_sample_count": len(curated_selected),
        "install_min_samples": min_install_samples,
        "install_min_seconds": min_install_seconds,
        "install_min_curated_samples": min_curated_samples,
        "install_recommended": not reasons,
        "install_blocking_reasons": reasons,
    }

def _fine_tune_vits_model(
    *,
    model_dir: Path,
    examples: List[VoiceTrainingExample],
    epochs: int,
) -> Dict[str, Any]:
    import torch
    import torch.nn.functional as F

    VitsModel, AutoTokenizer = load_vits_model_components()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch_runtime = _configure_torch_training_runtime(torch, device)
    resume_enabled = os.environ.get("AUTOYOU_VOICE_TRAINING_RESUME_FROM_EXISTING", "1").strip().lower()
    resume_from_existing = resume_enabled not in {"0", "false", "no", "off"}
    resume_source_dir: Optional[Path] = None
    if resume_from_existing and custom_voice_model_ready(model_dir):
        resume_source_dir = _prepare_resume_model_source(model_dir)
        model_source = str(resume_source_dir)
    else:
        model_source = BASE_VITS_MODEL
    output_model_dir = _prepare_training_output_dir(model_dir)

    tokenizer = AutoTokenizer.from_pretrained(model_source)
    model = VitsModel.from_pretrained(model_source).to(device)
    model.train()

    sample_rate = int(getattr(getattr(model, "config", None), "sample_rate", 16000) or 16000)
    max_seconds = _env_float("AUTOYOU_VOICE_TRAINING_MAX_SECONDS_PER_SAMPLE", 8.0, minimum=0.1)
    max_samples = max(1, int(sample_rate * max_seconds))
    max_examples = _env_int("AUTOYOU_VOICE_TRAINING_MAX_EXAMPLES", 32, minimum=0)
    min_example_seconds = _env_float("AUTOYOU_VOICE_TRAINING_MIN_EXAMPLE_SECONDS", 2.5, minimum=0.0)
    max_example_seconds = _env_float("AUTOYOU_VOICE_TRAINING_MAX_EXAMPLE_SECONDS", 20.0, minimum=0.0)
    min_text_chars = _env_int("AUTOYOU_VOICE_TRAINING_MIN_TEXT_CHARS", 18, minimum=0)
    max_text_chars = _env_int("AUTOYOU_VOICE_TRAINING_MAX_TEXT_CHARS", 320, minimum=0)
    min_speech_frame_ratio = _env_float("AUTOYOU_VOICE_TRAINING_MIN_SPEECH_RATIO", 0.25, minimum=0.0)
    min_rms_dbfs = _env_float("AUTOYOU_VOICE_TRAINING_MIN_RMS_DBFS", -38.0, minimum=-240.0)
    memory_safe = _env_enabled("AUTOYOU_VOICE_TRAINING_MEMORY_SAFE", True)
    selected_examples, selection = _select_fine_tune_examples(
        examples,
        max_examples=max_examples,
        min_example_seconds=min_example_seconds,
        max_example_seconds=max_example_seconds,
        min_text_chars=min_text_chars,
        max_text_chars=max_text_chars,
        min_speech_frame_ratio=min_speech_frame_ratio,
        min_rms_dbfs=min_rms_dbfs,
    )
    if not selected_examples:
        raise RuntimeError(
            "No usable voice training examples remained after applying fine-tune quality limits. "
            "Collect or upload clearer clips with exact transcripts before training."
        )
    dataset_quality = _dataset_quality_summary(examples, selected_examples, selection)

    learning_rate = float(os.environ.get("AUTOYOU_VOICE_TRAINING_LR", "1e-5"))
    grad_clip = float(os.environ.get("AUTOYOU_VOICE_TRAINING_GRAD_CLIP", "1.0"))
    torch.manual_seed(int(os.environ.get("AUTOYOU_VOICE_TRAINING_SEED", "1337")))
    trainable_runtime = _configure_trainable_parameters(model)

    trainable_parameters = [param for param in model.parameters() if getattr(param, "requires_grad", True)]
    if not trainable_parameters:
        raise RuntimeError("No trainable VITS parameters are available after applying trainable scope limits.")

    optimizer = torch.optim.AdamW(trainable_parameters, lr=learning_rate, weight_decay=1e-4)
    scaler = torch.amp.GradScaler("cuda", enabled=device == "cuda")
    loss_history: List[Dict[str, Any]] = []
    best_loss = math.inf

    for epoch in range(1, epochs + 1):
        epoch_loss = 0.0
        example_count = 0
        generator = torch.Generator()
        generator.manual_seed(1337 + epoch)
        order = torch.randperm(len(selected_examples), generator=generator).tolist()
        for item_index, example_index in enumerate(order, start=1):
            example = selected_examples[example_index]
            target = _audio_to_tensor(example.audio_path, sample_rate, device, max_seconds=max_seconds)
            if target.shape[-1] > max_samples:
                target = target[:max_samples]
            tokenized = tokenizer(example.text, return_tensors="pt")
            tokenized = {key: value.to(device) for key, value in tokenized.items()}

            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=device == "cuda"):
                output = model(**tokenized)
                predicted = output.waveform.squeeze()[:max_samples]
                target_matched = _match_target_length(target, int(predicted.shape[-1]))
                waveform_loss = F.l1_loss(predicted, target_matched)
                spectral_loss = _multi_resolution_stft_loss(predicted, target_matched)
                loss = waveform_loss + 0.35 * spectral_loss

            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
            scaler.step(optimizer)
            scaler.update()

            loss_value = float(loss.detach().cpu())
            epoch_loss += loss_value
            example_count += 1
            step_progress = (float(epoch - 1) + float(item_index) / float(len(selected_examples))) / float(epochs)
            _update_status(
                "training",
                15.0 + step_progress * 75.0,
                epoch,
                epochs,
                (
                    f"Fine-tuning cloned voice epoch {epoch}/{epochs}; "
                    f"sample {item_index}/{len(selected_examples)}; loss {loss_value:.4f}."
                ),
                training_strategy=TRAINING_STRATEGY_INTERNAL,
                dataset_sample_count=len(examples),
                **selection,
            )
            del target, tokenized, output, predicted, target_matched, waveform_loss, spectral_loss, loss
            if memory_safe:
                if device == "cuda":
                    torch.cuda.empty_cache()
                elif item_index % 4 == 0:
                    gc.collect()

        avg_loss = epoch_loss / max(1, example_count)
        loss_history.append({"epoch": epoch, "loss": avg_loss})
        if avg_loss < best_loss:
            best_loss = avg_loss
            model.save_pretrained(str(output_model_dir))
            tokenizer.save_pretrained(str(output_model_dir))

    if not (output_model_dir / "config.json").exists():
        model.save_pretrained(str(output_model_dir))
        tokenizer.save_pretrained(str(output_model_dir))

    result = {
        "training_strategy": TRAINING_STRATEGY_INTERNAL,
        "fine_tuning_applied": True,
        "device": device,
        "torch_runtime": torch_runtime,
        "started_from_checkpoint": model_source != BASE_VITS_MODEL,
        "starting_model": str(resume_source_dir) if model_source != BASE_VITS_MODEL else model_source,
        "resume_source_dir": str(resume_source_dir) if resume_source_dir is not None else "",
        "trained_model_dir": str(output_model_dir),
        "sample_rate": sample_rate,
        "learning_rate": learning_rate,
        "max_seconds_per_sample": max_seconds,
        "memory_safe": memory_safe,
        **selection,
        "dataset_quality": dataset_quality,
        **trainable_runtime,
        "trainable_parameters": _trainable_parameter_count(model),
        "loss_history": loss_history,
        "best_loss": best_loss if math.isfinite(best_loss) else None,
    }
    if resume_source_dir is not None:
        try:
            shutil.rmtree(resume_source_dir, ignore_errors=True)
        except Exception:
            pass
    return result

def run_training_sync(epochs: int = DEFAULT_TRAINING_EPOCHS) -> None:
    vt_dir, model_dir, _status_file, transcripts_file = _get_voice_training_dirs()
    requested_epochs = max(1, int(epochs or DEFAULT_TRAINING_EPOCHS))
    started_at = time.time()
    LOGGER.info("Starting local custom voice fine-tuning...")
    _update_status("training", 0.0, 0, requested_epochs, "Loading voice recordings and transcript pairs...")

    try:
        transcripts = _load_transcripts(transcripts_file)
        examples = _build_training_examples(vt_dir, transcripts)
        if not examples:
            raise RuntimeError(
                "No usable voice training samples were found. Upload WAV recordings with exact transcripts before training."
            )
        total_duration = sum(example.duration_seconds for example in examples)
        dataset_artifacts = _export_training_dataset(vt_dir, examples)
        strategy = _training_strategy()

        LOGGER.info("Voice dataset contains %d usable samples", len(examples))
        _update_status(
            "training",
            5.0,
            0,
            requested_epochs,
            f"Prepared {len(examples)} usable voice samples ({total_duration:.1f}s) for fine-tuning.",
            training_strategy=strategy,
            dataset_sample_count=len(examples),
            dataset_duration_seconds=total_duration,
        )

        if strategy == TRAINING_STRATEGY_EXTERNAL:
            _update_status(
                "training",
                10.0,
                0,
                requested_epochs,
                "Running configured external custom voice trainer...",
                training_strategy=strategy,
            )
            training_result = _run_external_training_command(
                vt_dir=vt_dir,
                model_dir=model_dir,
                dataset_artifacts=dataset_artifacts,
                epochs=requested_epochs,
            )
        elif strategy == TRAINING_STRATEGY_PREPARE_ONLY:
            _update_status(
                "training",
                15.0,
                0,
                requested_epochs,
                "Prepare-only compatibility mode is enabled; saving the base VITS provider without cloning.",
                training_strategy=strategy,
            )
            _save_base_provider_artifact(model_dir)
            training_result = {
                "training_strategy": TRAINING_STRATEGY_PREPARE_ONLY,
                "fine_tuning_applied": False,
            }
        else:
            _update_status(
                "training",
                10.0,
                0,
                requested_epochs,
                "Loading base MMS VITS model and starting cloned-voice fine-tuning...",
                training_strategy=strategy,
                dataset_sample_count=len(examples),
            )
            training_result = _fine_tune_vits_model(model_dir=model_dir, examples=examples, epochs=requested_epochs)

        artifact_model_dir = Path(str(training_result.get("trained_model_dir") or model_dir))
        if not custom_voice_model_ready(artifact_model_dir):
            raise RuntimeError("Voice training completed but did not produce loadable model artifacts.")

        _update_status("training", 95.0, requested_epochs, requested_epochs, "Writing custom voice metadata...")
        metadata = {
            "base_model": BASE_VITS_MODEL,
            "dataset_sample_count": len(examples),
            "dataset_duration_seconds": total_duration,
            "requested_epochs": requested_epochs,
            "completed_epochs": requested_epochs,
            "training_strategy": training_result.get("training_strategy", strategy),
            "fine_tuning_applied": bool(training_result.get("fine_tuning_applied")),
            "dataset_artifacts": dataset_artifacts,
            "package_versions": package_versions(),
            "training_started_at": started_at,
            "training_duration_seconds": time.time() - started_at,
            "created_at": time.time(),
            "display_name": "Custom trained voice",
            "system_voice_id": "custom_voice",
            "default_model_alias": str(model_dir),
            "artifact_model_dir": str(artifact_model_dir),
        }
        metadata.update({key: value for key, value in training_result.items() if key not in metadata})
        write_custom_voice_metadata(artifact_model_dir, metadata)
        dataset_quality = metadata.get("dataset_quality") if isinstance(metadata.get("dataset_quality"), dict) else {}
        install_recommended = bool(dataset_quality.get("install_recommended", False))
        allow_unrecommended_install = _env_enabled("AUTOYOU_VOICE_TRAINING_ALLOW_UNRECOMMENDED_INSTALL", False)
        active_pointer = None
        if artifact_model_dir != model_dir and (install_recommended or allow_unrecommended_install):
            active_pointer = set_active_custom_voice_model_dir(artifact_model_dir)
            metadata["active_model_pointer"] = str(active_pointer)
            write_custom_voice_metadata(artifact_model_dir, metadata)

        if metadata["fine_tuning_applied"] and install_recommended:
            completed_message = (
                "Custom cloned voice fine-tuning completed. Select 'Custom cloned voice' in Speech settings "
                "or install it from the Voice Training agent."
            )
        elif metadata["fine_tuning_applied"]:
            reasons = dataset_quality.get("install_blocking_reasons") or ["dataset_quality_gate_failed"]
            completed_message = (
                "Custom voice fine-tuning completed, but the model was not activated because the dataset did not "
                f"pass quality gates: {', '.join(str(reason) for reason in reasons)}."
            )
        else:
            completed_message = "Base VITS provider artifacts were prepared in compatibility mode; this is not a cloned voice."
        _update_status(
            "completed",
            100.0,
            requested_epochs,
            requested_epochs,
            completed_message,
            training_strategy=metadata["training_strategy"],
            dataset_sample_count=len(examples),
            fine_tune_sample_count=metadata.get("fine_tune_sample_count"),
            fine_tuning_applied=metadata["fine_tuning_applied"],
            install_recommended=install_recommended,
            active_model_pointer=str(active_pointer) if active_pointer is not None else "",
            dataset_quality=dataset_quality,
            artifact_model_dir=str(artifact_model_dir),
        )
        LOGGER.info("Custom voice training completed. Model saved to %s", artifact_model_dir)

    except Exception as exc:
        LOGGER.error("Voice training failed: %s", exc, exc_info=True)
        dependency_hint = ""
        versions = package_versions()
        if versions.get("torchvision"):
            dependency_hint = (
                " Transformers VITS was loaded through the audio-only compatibility path; "
                f"installed package versions: {versions}."
            )
        if "Training of VITS is not supported" in str(exc):
            dependency_hint += (
                " The Hugging Face MMS VITS labels path is not trainable; "
                "AutoYou uses waveform reconstruction fine-tuning or an external trainer command instead."
            )
        if "could not execute a primitive" in str(exc):
            dependency_hint += (
                " CPU Torch backend primitive failed; AutoYou disables MKLDNN by default for this worker. "
                "If this persists, set AUTOYOU_VOICE_TRAINING_TORCH_THREADS=1 or use an external trainer."
            )
        if "user-mapped section open" in str(exc) or "os error 1224" in str(exc):
            dependency_hint += (
                " Windows blocked overwriting a model file that is still memory-mapped by another process; "
                "AutoYou now writes fine-tuned checkpoints into versioned output directories and switches "
                "the active custom voice pointer after completion."
            )
        if "bad allocation" in str(exc):
            dependency_hint += (
                " The training worker ran out of allocatable memory; reduce "
                "AUTOYOU_VOICE_TRAINING_MAX_EXAMPLES, AUTOYOU_VOICE_TRAINING_MAX_SECONDS_PER_SAMPLE, "
                "or AUTOYOU_VOICE_TRAINING_TORCH_THREADS."
            )
        _update_status(
            "failed",
            0.0,
            0,
            requested_epochs,
            "Custom voice fine-tuning encountered an error.",
            error=f"{exc}{dependency_hint}",
        )

def start_training_async(epochs: int = DEFAULT_TRAINING_EPOCHS) -> bool:
    global _TRAINING_THREAD

    current = get_status()
    if current.get("status") == "training":
        LOGGER.warning("Training is already in progress.")
        return False

    requested_epochs = max(1, int(epochs or DEFAULT_TRAINING_EPOCHS))
    backend = _default_async_backend()
    if backend not in {"thread", "inprocess", "in-process"}:
        try:
            process = _launch_training_subprocess(requested_epochs)
            _update_status(
                "training",
                0.0,
                0,
                requested_epochs,
                "Voice training worker process started.",
                async_backend="process",
                worker_pid=getattr(process, "pid", 0),
            )
            return True
        except Exception as exc:
            LOGGER.warning(
                "Failed to launch voice training worker process; falling back to in-process thread: %s",
                exc,
            )

    t = threading.Thread(
        target=run_training_sync,
        args=(requested_epochs,),
        daemon=True,
        name="VoiceTrainingWorker",
    )
    _TRAINING_THREAD = t
    t.start()
    return True

def _default_async_backend() -> str:
    configured = os.environ.get("AUTOYOU_VOICE_TRAINING_ASYNC_BACKEND", "").strip().lower()
    if configured:
        return configured
    try:
        from shared.platform_runtime import is_compiled

        if is_compiled():
            return "thread"
    except Exception:
        pass
    return "process"

def _launch_training_subprocess(epochs: int) -> subprocess.Popen:
    vt_dir, _model_dir, _status_file, _transcripts_file = _get_voice_training_dirs()
    vt_dir.mkdir(parents=True, exist_ok=True)
    log_path = vt_dir / "training_worker.log"
    repo_root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-m",
        "autoyou_agents.voice_training_agent.train_model",
        "--epochs",
        str(max(1, int(epochs or DEFAULT_TRAINING_EPOCHS))),
    ]
    popen_kwargs: Dict[str, Any] = {
        "cwd": str(repo_root),
        "env": os.environ.copy(),
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    log_handle = None
    if secure_storage_enabled():
        # The parent owns this log file.  Capture the child output through a
        # pipe so no worker line is ever written in plaintext to disk.
        append_secure_file(log_path, b"")
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.STDOUT
    else:
        log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
    try:
        process = subprocess.Popen(
            command,
            **popen_kwargs,
        )
    except Exception:
        if log_handle is not None:
            log_handle.close()
        raise
    if log_handle is not None:
        log_handle.close()
    if secure_storage_enabled() and hasattr(process.stdout, "readline"):
        def _capture_secure_training_log() -> None:
            stream = process.stdout
            try:
                for line in iter(stream.readline, b""):
                    payload = line.encode("utf-8", errors="replace") if isinstance(line, str) else bytes(line)
                    append_secure_file(log_path, payload)
            except SecureStorageError as exc:
                LOGGER.error("Protected voice-training log capture stopped: %s", exc)
            except Exception as exc:
                LOGGER.warning("Voice-training log capture stopped: %s", exc)
            finally:
                with contextlib.suppress(Exception):
                    stream.close()

        threading.Thread(
            target=_capture_secure_training_log,
            daemon=True,
            name="VoiceTrainingLogCapture",
        ).start()
    LOGGER.info("Voice training worker process launched pid=%s log=%s", process.pid, log_path)
    return process

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AutoYou custom voice training.")
    parser.add_argument(
        "--epochs",
        type=int,
        default=DEFAULT_TRAINING_EPOCHS,
        help=f"Number of training epochs (default: {DEFAULT_TRAINING_EPOCHS}).",
    )
    return parser.parse_args(argv)

def main(argv: Optional[List[str]] = None) -> int:
    try:
        enable_secure_storage_from_environment()
    except SecureStorageError as exc:
        LOGGER.error("Voice training worker could not attach to protected storage: %s", exc)
        return 5
    args = _parse_args(argv)
    run_training_sync(epochs=args.epochs)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
