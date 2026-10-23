# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-186baf8826570343b3b3eb48

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


"""Build the owner's cloned voice for the custom voice TTS provider.

"Training" is kept as the user-facing word, but nothing is fine-tuned:
Chatterbox clones zero-shot from a short reference. This module checks the
owner's consent, picks their clearest clips, joins them into a reference,
has the voice-clone runtime speak a test sentence, confirms the Perth
watermark is detectable in it, and only then switches the voice on.
"""

import argparse
import contextlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from shared.custom_voice_tts import (
    CUSTOM_VOICE_DISPLAY_NAME,
    CUSTOM_VOICE_SYSTEM_VOICE_ID,
    VOICE_CLONE_ENGINE,
    VOICE_CLONE_MODEL_REPO,
    VOICE_CLONE_MODEL_REVISION,
    VOICE_CLONE_PREVIEW_FILENAME,
    VOICE_CLONE_REFERENCE_FILENAME,
    VOICE_CLONE_STRATEGY,
    WATERMARK_METHOD,
    VoiceCloneEngineError,
    custom_voice_status,
    load_voice_clone_consent,
    open_voice_clone_worker,
    reference_key,
    resolve_voice_clone_python,
    set_active_custom_voice_model_dir,
    voice_clone_outputs_dir,
    write_custom_voice_metadata,
)
from shared.secure_storage import (
    SecureStorageError,
    append_secure_file,
    enable_secure_storage_from_environment,
    load_secure_json,
    materialize_secure_file,
    save_secure_json,
    secure_storage_enabled,
    write_secure_file,
)

from autoyou_agents.voice_training_agent.reference_selection import (
    REFERENCE_SAMPLE_RATE,
    ReferenceCandidate,
    ReferenceThresholds,
    build_reference_audio,
    describe_selection_problem,
    load_wav_mono,
    measure_clip,
    select_reference_clips,
    wav_bytes,
)

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-186baf8826570343b3b3eb48"


LOGGER = logging.getLogger(__name__)

PREPARE_STEPS = 5
PREVIEW_TEXT = "Hi, this is my AutoYou voice. It runs on my own computer, so my recordings stay with me."
TRAINING_STRATEGY = VOICE_CLONE_STRATEGY

_STATUS_LOCK = threading.Lock()
# from __debug_provenance_q__ import payment
_TRAINING_THREAD: Optional[threading.Thread] = None


class VoiceClonePreparationError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

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

def _safe_sample_id(value: Any, fallback_index: int) -> str:
    raw = str(value or "").strip() or f"sample-{fallback_index:04d}"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
    return safe[:80] or f"sample-{fallback_index:04d}"

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

def parse_reference_clip_ids(value: Any) -> List[str]:
    """Accept a list or a comma/space separated string of clip IDs."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"[\s,;]+", str(value))
    return [_safe_sample_id(item, index) for index, item in enumerate(items, start=1) if str(item or "").strip()]


def collect_reference_candidates(
    vt_dir: Path,
    transcripts: List[Dict[str, Any]],
    thresholds: Optional[ReferenceThresholds] = None,
) -> List[ReferenceCandidate]:
    """Measure every recording in the dataset. Transcripts are not needed for cloning."""
    recordings_dir = vt_dir / "recordings"
    candidates: List[ReferenceCandidate] = []
    seen_paths: set[str] = set()
    for index, item in enumerate(transcripts, start=1):
        if not isinstance(item, dict):
            continue
        audio_path = _resolve_recording_path(vt_dir, recordings_dir, item)
        if audio_path is None:
            continue
        key = str(audio_path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        sample_id = _safe_sample_id(item.get("id"), index)
        try:
            with materialize_secure_file(audio_path) as readable:
                audio, sample_rate = load_wav_mono(readable)
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.info("Skipping unreadable voice clip %s: %s", sample_id, exc)
            continue
        candidates.append(
            measure_clip(
                audio,
                sample_rate,
                sample_id=sample_id,
                audio_path=audio_path,
                source=str(item.get("source") or "unknown"),
                user_id=str(item.get("user_id") or ""),
                thresholds=thresholds,
            )
        )
    return candidates


def _load_clip(candidate: ReferenceCandidate):
    with materialize_secure_file(candidate.audio_path) as readable:
        return load_wav_mono(readable)


def _new_output_dir() -> Path:
    root = voice_clone_outputs_dir()
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    output_dir = root / f"custom_voice_{stamp}_{uuid.uuid4().hex[:8]}"
    output_dir.mkdir(parents=True, exist_ok=False)
    return output_dir


def _remove_superseded_outputs(keep: Path) -> List[str]:
    """Delete references from earlier builds; only the active one is kept."""
    removed: List[str] = []
    root = voice_clone_outputs_dir()
    if not root.is_dir():
        return removed
    for candidate in root.iterdir():
        if candidate.is_dir() and candidate.resolve() != keep.resolve():
            shutil.rmtree(candidate, ignore_errors=True)
            removed.append(candidate.name)
    return removed


def _watermark_min_score() -> float:
    try:
        return min(1.0, max(0.05, float(os.environ.get("AUTOYOU_VOICE_CLONE_WATERMARK_MIN_SCORE", "0.5"))))
    except ValueError:
        return 0.5


def run_training_sync(reference_clip_ids: Optional[Sequence[str]] = None) -> None:
    """Build the cloned voice. Progress and the outcome go to the status file."""
    vt_dir, model_dir, _status_file, transcripts_file = _get_voice_training_dirs()
    pinned = parse_reference_clip_ids(reference_clip_ids)
    started_at = time.time()
    steps = PREPARE_STEPS
    output_dir: Optional[Path] = None
    activated = False
    worker = None
    LOGGER.info("Building the cloned voice with Chatterbox.")
    _update_status("training", 2.0, 0, steps, "Checking consent...", training_strategy=TRAINING_STRATEGY)

    try:
        consent = load_voice_clone_consent()
        if consent is None:
            raise VoiceClonePreparationError(
                "consent_required",
                "Record your consent to clone your own voice before building it.",
            )

        _update_status("training", 8.0, 1, steps, "Measuring your recordings...", training_strategy=TRAINING_STRATEGY)
        thresholds = ReferenceThresholds.from_environment()
        candidates = collect_reference_candidates(vt_dir, _load_transcripts(transcripts_file), thresholds)
        if not candidates:
            raise VoiceClonePreparationError(
                "no_recordings",
                "No recordings were found. Upload a 10-20 second WAV of you reading aloud, "
                "or turn on voice call capture.",
            )
        selected, selection = select_reference_clips(candidates, pinned_ids=pinned, thresholds=thresholds)
        if not selection.get("ok"):
            raise VoiceClonePreparationError(
                str(selection.get("problem") or "selection_failed"),
                describe_selection_problem(selection),
                reference_selection=selection,
            )

        clips = [_load_clip(candidate) for candidate in selected]
        reference_audio = build_reference_audio(clips, max_seconds=thresholds.max_reference_seconds)
        reference_seconds = float(reference_audio.size) / float(REFERENCE_SAMPLE_RATE)
        _update_status(
            "training",
            18.0,
            2,
            steps,
            f"Joined {len(selected)} of your clearest clips into a {reference_seconds:.1f}s reference.",
            training_strategy=TRAINING_STRATEGY,
            reference_selection=selection,
        )
        output_dir = _new_output_dir()
        reference_path = output_dir / VOICE_CLONE_REFERENCE_FILENAME
        write_secure_file(reference_path, wav_bytes(reference_audio, REFERENCE_SAMPLE_RATE))

        _update_status(
            "training",
            25.0,
            3,
            steps,
            "Starting the voice-clone runtime. The first build downloads the 3 GB Chatterbox model.",
            training_strategy=TRAINING_STRATEGY,
        )
        worker = open_voice_clone_worker(idle_seconds=3600.0)
        loaded = worker.ensure_loaded(allow_download=True)
        accelerator = str(loaded.get("accelerator") or loaded.get("device") or "this computer")

        _update_status(
            "training",
            70.0,
            4,
            steps,
            f"Speaking a test sentence in your voice on {accelerator}...",
            training_strategy=TRAINING_STRATEGY,
        )
        worker.use_reference(reference_path, key=reference_key(reference_path))
        with tempfile.TemporaryDirectory(prefix="autoyou-voice-clone-") as scratch:
            preview_plain = Path(scratch) / VOICE_CLONE_PREVIEW_FILENAME
            spoken = worker.synthesize([PREVIEW_TEXT], preview_plain, options={"seed": 7})
            preview_mark = worker.detect_watermark(preview_plain)
            reference_mark = worker.detect_watermark(reference_path)
            write_secure_file(output_dir / VOICE_CLONE_PREVIEW_FILENAME, preview_plain.read_bytes())

        min_score = _watermark_min_score()
        preview_score = float(preview_mark.get("score") or 0.0)
        reference_score = float(reference_mark.get("score") or 0.0)
        if preview_score < min_score or preview_score <= reference_score:
            raise VoiceClonePreparationError(
                "watermark_not_detected",
                "The AI-speech watermark could not be detected in the test sentence, so the cloned voice "
                f"was not turned on (score {preview_score:.2f}, needed {min_score:.2f}).",
            )

        _update_status("training", 92.0, 5, steps, "Saving your cloned voice...", training_strategy=TRAINING_STRATEGY)
        _python, python_source = resolve_voice_clone_python()
        metadata: Dict[str, Any] = {
            "engine": VOICE_CLONE_ENGINE,
            "model_repo": VOICE_CLONE_MODEL_REPO,
            "model_revision": VOICE_CLONE_MODEL_REVISION,
            "training_strategy": TRAINING_STRATEGY,
            "voice_cloned": True,
            "fine_tuning_applied": False,
            "display_name": CUSTOM_VOICE_DISPLAY_NAME,
            "system_voice_id": CUSTOM_VOICE_SYSTEM_VOICE_ID,
            "default_model_alias": str(model_dir),
            "artifact_model_dir": str(output_dir),
            "created_at": time.time(),
            "preparation_seconds": round(time.time() - started_at, 1),
            "consent": {
                key: consent.get(key) for key in ("consent_id", "version", "accepted_at", "accepted_via")
            },
            "reference": {
                "seconds": round(reference_seconds, 2),
                "sample_rate": REFERENCE_SAMPLE_RATE,
                "pinned": bool(pinned),
                "clips": [candidate.summary() for candidate in selected],
                "selection": selection,
            },
            "watermark": {
                "method": WATERMARK_METHOD,
                "enabled": True,
                "verified": True,
                "preview_score": round(preview_score, 4),
                "reference_score": round(reference_score, 4),
                "min_score": min_score,
            },
            "runtime": {
                "python_source": python_source,
                "device": loaded.get("device"),
                "accelerator": accelerator,
                "chatterbox_version": loaded.get("chatterbox_version"),
                "torch_version": loaded.get("torch_version"),
                "load_seconds": loaded.get("load_seconds"),
            },
            "verification": {
                "text_chars": len(PREVIEW_TEXT),
                "audio_seconds": spoken.get("audio_seconds"),
                "wall_seconds": spoken.get("wall_seconds"),
                "realtime_factor": spoken.get("realtime_factor"),
            },
            "synthesis": {"exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8},
            "install_recommended": True,
            "install_warnings": list(selection.get("warnings") or []),
        }
        write_custom_voice_metadata(output_dir, metadata)
        model_dir.mkdir(parents=True, exist_ok=True)
        pointer = set_active_custom_voice_model_dir(output_dir)
        activated = True
        superseded = _remove_superseded_outputs(keep=output_dir)

        warnings = list(selection.get("warnings") or [])
        message = (
            f"Your cloned voice is ready ({reference_seconds:.1f}s reference from {len(selected)} clips, "
            f"watermark verified). Install it from the Voice Training agent to use it for speech."
        )
        if "phone_quality_reference" in warnings:
            message += " It was built from phone-quality audio; a clear 10-20 second upload will sound better."
        if "clips_from_more_than_one_caller" in warnings:
            message += " The chosen clips came from more than one caller ID: listen to the preview and check it is you."
        _update_status(
            "completed",
            100.0,
            steps,
            steps,
            message,
            training_strategy=TRAINING_STRATEGY,
            voice_cloned=True,
            fine_tuning_applied=False,
            install_recommended=True,
            install_warnings=warnings,
            reference_selection=selection,
            watermark=metadata["watermark"],
            runtime=metadata["runtime"],
            verification=metadata["verification"],
            active_model_pointer=str(pointer),
            artifact_model_dir=str(output_dir),
            superseded_outputs=superseded,
        )
        LOGGER.info("Cloned voice ready: %s", output_dir)

    except VoiceClonePreparationError as exc:
        LOGGER.warning("Cloned voice was not built: %s", exc.message)
        _update_status(
            "failed",
            0.0,
            0,
            steps,
            exc.message,
            error=exc.message,
            failure_reason=exc.code,
            training_strategy=TRAINING_STRATEGY,
            **exc.details,
        )
    except VoiceCloneEngineError as exc:
        LOGGER.error("Voice-clone runtime failed: %s", exc)
        _update_status(
            "failed",
            0.0,
            0,
            steps,
            "The voice-clone runtime could not build your voice.",
            error=str(exc),
            failure_reason="engine_error",
            training_strategy=TRAINING_STRATEGY,
        )
    except Exception as exc:
        LOGGER.error("Building the cloned voice failed: %s", exc, exc_info=True)
        _update_status(
            "failed",
            0.0,
            0,
            steps,
            "Building the cloned voice failed.",
            error=str(exc),
            failure_reason="unexpected_error",
            training_strategy=TRAINING_STRATEGY,
        )
    finally:
        if worker is not None:
            worker.close()
        if output_dir is not None and not activated:
            shutil.rmtree(output_dir, ignore_errors=True)

def start_training_async(reference_clip_ids: Optional[Sequence[str]] = None) -> bool:
    """Build the cloned voice in the background. False when a build is already running."""
    global _TRAINING_THREAD

    current = get_status()
    if current.get("status") == "training":
        LOGGER.warning("A cloned voice build is already in progress.")
        return False

    pinned = parse_reference_clip_ids(reference_clip_ids)
    backend = _default_async_backend()
    if backend not in {"thread", "inprocess", "in-process"}:
        try:
            process = _launch_training_subprocess(pinned)
            _update_status(
                "training",
                0.0,
                0,
                PREPARE_STEPS,
                "Voice cloning worker process started.",
                async_backend="process",
                worker_pid=getattr(process, "pid", 0),
                training_strategy=TRAINING_STRATEGY,
            )
            return True
        except Exception as exc:
            LOGGER.warning(
                "Failed to launch voice training worker process; falling back to in-process thread: %s",
                exc,
            )

    t = threading.Thread(
        target=run_training_sync,
        args=(pinned,),
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

def _launch_training_subprocess(reference_clip_ids: Optional[Sequence[str]] = None) -> subprocess.Popen:
    vt_dir, _model_dir, _status_file, _transcripts_file = _get_voice_training_dirs()
    vt_dir.mkdir(parents=True, exist_ok=True)
    log_path = vt_dir / "training_worker.log"
    repo_root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-m",
        "autoyou_agents.voice_training_agent.train_model",
    ]
    pinned = parse_reference_clip_ids(reference_clip_ids)
    if pinned:
        command += ["--reference-clips", ",".join(pinned)]
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
    parser = argparse.ArgumentParser(description="Build the owner's cloned voice for AutoYou.")
    parser.add_argument(
        "--reference-clips",
        default="",
        help="Comma-separated clip IDs to use as the reference instead of choosing automatically.",
    )
    return parser.parse_args(argv)

def main(argv: Optional[List[str]] = None) -> int:
    try:
        enable_secure_storage_from_environment()
    except SecureStorageError as exc:
        LOGGER.error("Voice training worker could not attach to protected storage: %s", exc)
        return 5
    args = _parse_args(argv)
    run_training_sync(parse_reference_clip_ids(args.reference_clips))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
