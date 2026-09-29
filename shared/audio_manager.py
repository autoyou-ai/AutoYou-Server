# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-363546313441613439306132-41ac0c005d359401c1b92ed5

"""
Audio Manager for AutoYou Server.

Handles real-time speech-to-text (STT) and text-to-speech (TTS)
for voice calls using WebRTC.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-363546313441613439306132-41ac0c005d359401c1b92ed5"


import asyncio
import array
from contextlib import contextmanager
import fractions
import html
import importlib
import json
import logging
import math
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Tuple

from aiortc.mediastreams import MediaStreamTrack
from av import AudioFrame

try:
    from shared.custom_voice_tts import (
        CUSTOM_VOICE_PROVIDER,
        CUSTOM_VOICE_SYSTEM_VOICE_ID,
        custom_voice_model_ready,
        synthesize_custom_voice_to_file,
    )
except Exception as exc:
    _CUSTOM_VOICE_IMPORT_ERROR = exc
    CUSTOM_VOICE_PROVIDER = "custom"
    CUSTOM_VOICE_SYSTEM_VOICE_ID = "custom_voice"

    def custom_voice_model_ready(*args, **kwargs):  # type: ignore[no-redef]
        return False

    def synthesize_custom_voice_to_file(*args, **kwargs):  # type: ignore[no-redef]
        raise RuntimeError(f"Custom voice runtime is unavailable: {_CUSTOM_VOICE_IMPORT_ERROR}")

from shared.speech_config import deepcopy_speech_config, normalize_speech_config
from shared.emotivoice_tts import status as emotivoice_status, synthesize as synthesize_emotivoice
from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json, write_secure_file

_REALTIMESTT_IMPORT_ERROR: Optional[Exception] = None
_PYTTSX3_IMPORT_ERROR: Optional[Exception] = None

# Attempt to import RealtimeSTT
try:
    from RealtimeSTT import AudioToTextRecorder
    from RealtimeSTT import audio_recorder as _realtimestt_audio_recorder
except Exception as exc:
    AudioToTextRecorder = None
    _realtimestt_audio_recorder = None
    _REALTIMESTT_IMPORT_ERROR = exc

# macOS uses the native `say` binary directly for system TTS. Avoid importing
# pyttsx3 there because its NSSpeechSynthesizer/PyObjC backend pulls AppKit /
# Foundation into the backend compile graph and forces Nuitka app-bundle mode.
if sys.platform == "darwin":
    pyttsx3 = None
    _PYTTSX3_IMPORT_ERROR = RuntimeError("pyttsx3 is intentionally disabled on macOS; use `say`")
else:
    try:
        import pyttsx3
    except Exception as exc:
        pyttsx3 = None
        _PYTTSX3_IMPORT_ERROR = exc

try:
    import httpx
except ImportError:
    httpx = None

try:
    import azure.cognitiveservices.speech as speechsdk
except ImportError:
    speechsdk = None

LOGGER = logging.getLogger("autoyou.audio")
TTS_SUBPROCESS_TIMEOUT_SECONDS = float(os.getenv("AUTOYOU_TTS_TIMEOUT_SECONDS", "60"))
OPENAI_TTS_TIMEOUT_SECONDS = float(os.getenv("AUTOYOU_OPENAI_TTS_TIMEOUT_SECONDS", "90"))
SYSTEM_TTS_BASE_RATE = 150
_SYSTEM_TTS_VOICE_CACHE: Optional[List[Dict[str, str]]] = None
_SYSTEM_TTS_VOICE_CACHE_LOCK = threading.Lock()
_THREAD_JOIN_TIMEOUT_SECONDS = float(os.getenv("AUTOYOU_AUDIO_THREAD_JOIN_TIMEOUT_SECONDS", "2.0"))
_TQDM_ENSURE_LOCK_PATCHED = False
_TTS_MIN_AUDIO_BYTES = int(os.getenv("AUTOYOU_TTS_MIN_AUDIO_BYTES", "128"))
_TTS_OUTPUT_WAIT_SECONDS = float(os.getenv("AUTOYOU_TTS_OUTPUT_WAIT_SECONDS", "2.0"))
# A WebRTC audio frame is normally about 20 ms after resampling. Keep a short,
# bounded backlog so a call that starts while Whisper is warming does not lose
# its first utterance or grow memory without limit if initialization is slow.
_STT_WARMUP_AUDIO_QUEUE_MAX_CHUNKS = max(
    128,
    int(os.getenv("AUTOYOU_STT_WARMUP_AUDIO_QUEUE_MAX_CHUNKS", "750")),
)
_TTS_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_TTS_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s*")
_TTS_BULLET_RE = re.compile(r"(?m)^\s*[-*+]\s+")
_TTS_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_VOICE_TRAINING_CAPTURE_ENABLED_BY_ENV = os.getenv("AUTOYOU_VOICE_TRAINING_AUTO_CAPTURE_ENABLED", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
_VOICE_TRAINING_TRANSCRIPTS_LOCK = threading.Lock()
_PLAYBACK_FRAME_BUFFER_SIZE = max(8, int(os.getenv("AUTOYOU_PLAYBACK_FRAME_BUFFER_SIZE", "120")))
_MACOS_SAY_VOICE_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+(?P<language>[a-z]{2,3}(?:_[A-Za-z0-9]{2,})?)\b"
)
_IGNORED_STT_TRANSCRIPTS = frozenset({"[BLANK_AUDIO]", "[MUSIC PLAYING]"})

# WUIFT ("Wait Until I Finish Talking") segmentation hold. While held, VAD
# silence never finalizes an utterance; only an explicit flush_utterance()
# (client button press / mute) or the safety cap below does.
WUIFT_HOLD_SILENCE_SECONDS = 3600.0
# Hard ceiling on one held segment so a client that engages the hold and walks
# away cannot grow recorder memory unbounded (~19 MB PCM at 16 kHz mono s16).
WUIFT_MAX_SEGMENT_SECONDS = 600.0

def _is_compiled_runtime() -> bool:
    try:
        from shared.platform_runtime import is_compiled

        return bool(is_compiled())
    except Exception:
        return False

def _win32_stdio_redirected() -> bool:
    if sys.platform != "win32":
        return False
    for stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
        try:
            if stream is not None and not bool(stream.isatty()):
                return True
        except Exception:
            return True
    return False

class _ThreadBackedProcessHandle:
    """Provide the small Process API surface RealtimeSTT shutdown expects."""

    def __init__(self, thread_obj: threading.Thread):
        self._thread = thread_obj

    def join(self, timeout: Optional[float] = None) -> None:
        self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def terminate(self) -> None:
        return None

class _NoopProcessHandle:
    """Process-shaped placeholder for disabled RealtimeSTT input workers."""

    def start(self) -> None:
        return None

    def join(self, timeout: Optional[float] = None) -> None:
        return None

    def is_alive(self) -> bool:
        return False

    def terminate(self) -> None:
        return None

@dataclass(frozen=True)
class _UtteranceFlushRequest:
    source: str
    timestamp_ms: Optional[int] = None

def _normalize_transcription_text(text: Any) -> str:
    return str(text or "").strip()

def _should_ignore_transcription_text(text: Any) -> bool:
    normalized = _normalize_transcription_text(text)
    return normalized in _IGNORED_STT_TRANSCRIPTS

def _coerce_enabled_flag(raw_value: Any, default: bool = True) -> bool:
    if raw_value is None:
        return bool(default)
    if isinstance(raw_value, str):
        return raw_value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(raw_value)

def _voice_training_capture_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    if not _VOICE_TRAINING_CAPTURE_ENABLED_BY_ENV:
        return False
    if not isinstance(settings, dict):
        return False
    voice_training_cfg = settings.get("voice_training")
    if not isinstance(voice_training_cfg, dict):
        return False
    return _coerce_enabled_flag(voice_training_cfg.get("capture_enabled"), False)

def _safe_realtimestt_transcription_worker(*args, **kwargs) -> None:
    """Wrap RealtimeSTT's worker so pipe teardown ends the polling loop cleanly."""
    if _realtimestt_audio_recorder is None:
        raise RuntimeError("RealtimeSTT audio_recorder module is unavailable")

    worker_cls = getattr(_realtimestt_audio_recorder, "TranscriptionWorker", None)
    worker_module = _realtimestt_audio_recorder
    if worker_cls is None:
        try:
            worker_module = importlib.import_module("RealtimeSTT.core.transcription")
            worker_cls = getattr(worker_module, "TranscriptionWorker", None)
        except Exception:
            worker_cls = None
    if worker_cls is None:
        raise RuntimeError("RealtimeSTT TranscriptionWorker is unavailable")

    worker = worker_cls(*args, **kwargs)
    poll_sleep = float(getattr(worker_module, "TIME_SLEEP", 0.02) or 0.02)

    def _safe_poll_connection() -> None:
        while not worker.shutdown_event.is_set():
            try:
                if worker.conn.poll(0.01):
                    data = worker.conn.recv()
                    worker.queue.put(data)
                else:
                    time.sleep(poll_sleep)
            except (BrokenPipeError, EOFError, OSError) as exc:
                logging.getLogger("realtimestt").info(
                    "RealtimeSTT transcription pipe closed; exiting polling loop: %s",
                    exc,
                )
                try:
                    worker.shutdown_event.set()
                except Exception:
                    pass
                break
            except Exception as exc:
                logging.error("Error receiving data from connection: %s", exc, exc_info=True)
                time.sleep(poll_sleep)

    worker.poll_connection = _safe_poll_connection
    worker.run()

def _callable_name(target: Any) -> str:
    return str(getattr(target, "__name__", "") or "")

def _callable_module(target: Any) -> str:
    return str(getattr(target, "__module__", "") or "")

def _is_realtimestt_transcription_target(target: Any) -> bool:
    legacy_target = getattr(AudioToTextRecorder, "_transcription_worker", None) if AudioToTextRecorder is not None else None
    if legacy_target is not None and target == legacy_target:
        return True
    return _callable_name(target) == "run_transcription_worker" and _callable_module(target).startswith("RealtimeSTT.")

def _is_realtimestt_audio_input_target(target: Any) -> bool:
    legacy_target = getattr(AudioToTextRecorder, "_audio_data_worker", None) if AudioToTextRecorder is not None else None
    if legacy_target is not None and target == legacy_target:
        return True
    return _callable_name(target) == "run_audio_data_worker" and _callable_module(target).startswith("RealtimeSTT.")

@contextmanager
def _patch_realtimestt_worker_starter():
    """Patch RealtimeSTT 1.x module-level worker startup during construction."""
    try:
        initialization_module = importlib.import_module("RealtimeSTT.core.initialization")
    except Exception:
        yield
        return

    original_start_worker = getattr(initialization_module, "start_recorder_worker", None)
    if not callable(original_start_worker):
        yield
        return

    def _autoyou_start_worker(target=None, args=()):
        if _is_realtimestt_transcription_target(target):
            LOGGER.info("Headless mode: using safe RealtimeSTT transcription worker")
            target = _safe_realtimestt_transcription_worker
            if _should_use_threaded_realtimestt_worker():
                LOGGER.info(
                    "Headless mode: running RealtimeSTT transcription worker in-process to avoid multiprocessing startup failures (platform=%s)",
                    sys.platform,
                )
                return _start_process_like_thread(
                    target,
                    tuple(args),
                    name="RealtimeSTT-TranscriptionWorker",
                )
        elif _is_realtimestt_audio_input_target(target):
            LOGGER.info("Headless mode: blocking audio data worker start")
            return _NoopProcessHandle()

        _guard_child_sys_path()
        return original_start_worker(target=target, args=args)

    initialization_module.start_recorder_worker = _autoyou_start_worker
    try:
        yield
    finally:
        initialization_module.start_recorder_worker = original_start_worker

def _join_thread(thread_obj: Optional[threading.Thread], label: str) -> None:
    if thread_obj is None or thread_obj is threading.current_thread():
        return
    if not thread_obj.is_alive():
        return
    try:
        thread_obj.join(timeout=_THREAD_JOIN_TIMEOUT_SECONDS)
    except Exception as exc:
        LOGGER.debug("Failed to join %s thread during shutdown: %s", label, exc)

def _normalize_voice_language(value: Any) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="ignore")
        except Exception:
            value = repr(value)
    text = str(value or "").strip()
    if text.startswith("\\x05"):
        text = text[4:]
    return text

def _resolve_stt_device(requested_device: str) -> str:
    normalized = str(requested_device or "").strip().lower()
    if normalized == "cpu":
        return "cpu"
    if normalized == "cuda":
        return "cuda"
    if normalized == "auto":
        try:
            import torch

            return "cuda" if bool(torch.cuda.is_available()) else "cpu"
        except Exception:
            return "cpu"
    return "cpu"

def _ensure_torch_hub_repo_trusted(repo_name: str) -> None:
    try:
        import torch.hub

        hub_dir = torch.hub.get_dir()
        os.makedirs(hub_dir, exist_ok=True)
        trusted_path = os.path.join(hub_dir, "trusted_list")
        if not os.path.exists(trusted_path):
            with open(trusted_path, "w", encoding="utf-8"):
                pass
        with open(trusted_path, "r", encoding="utf-8") as handle:
            trusted = {line.strip() for line in handle if line.strip()}
        if repo_name in trusted:
            return
        with open(trusted_path, "a", encoding="utf-8") as handle:
            handle.write(repo_name + "\n")
        LOGGER.info("Trusted Torch Hub repo added for headless STT startup: %s", repo_name)
    except Exception as exc:
        LOGGER.debug("Failed to pre-trust Torch Hub repo %s: %s", repo_name, exc)

class _SileroFallbackProbability:
    def __init__(self, value: float):
        self._value = float(value)

    def item(self) -> float:
        return self._value

class _WebRTCSileroVADShim:
    """Fallback Silero-like interface backed by local WebRTC-gated energy checks."""

    def __init__(self, activity_threshold: float = 0.001):
        self._activity_threshold = max(0.0, float(activity_threshold))

    def reset_states(self) -> None:
        return None

    def __call__(self, audio_chunk: Any, sample_rate: int) -> _SileroFallbackProbability:
        del sample_rate
        energy = 0.0
        try:
            if hasattr(audio_chunk, "detach"):
                detached = audio_chunk.detach()
                if hasattr(detached, "abs") and hasattr(detached, "mean"):
                    energy = float(detached.abs().mean().item())
                else:
                    values = [abs(float(value)) for value in detached]
                    energy = (sum(values) / len(values)) if values else 0.0
            else:
                values = [abs(float(value)) for value in audio_chunk]
                energy = (sum(values) / len(values)) if values else 0.0
        except Exception:
            energy = 0.0

        probability = 0.95 if energy > self._activity_threshold else 0.0
        return _SileroFallbackProbability(probability)

def _is_realtimestt_silero_load_error(exc: Exception) -> bool:
    lowered_message = str(exc or "").strip().lower()
    if any(marker in lowered_message for marker in ("silero", "silero-vad", "snakers4/silero-vad")):
        return True

    try:
        formatted = "".join(traceback.format_exception(exc)).lower()
    except Exception:
        return False

    if "snakers4/silero-vad" in formatted or "silero_vad" in formatted or "silero vad" in formatted:
        return True

    return (
        "torch.hub.load" in formatted
        and any(marker in formatted for marker in ("http error", "bad gateway", "proxy error", "github.com"))
    )

@contextmanager
def _patch_torch_hub_silero_fallback():
    try:
        import torch
    except Exception:
        yield {"used": False}
        return

    hub = getattr(torch, "hub", None)
    original_load = getattr(hub, "load", None)
    if hub is None or not callable(original_load):
        yield {"used": False}
        return

    state = {"used": False}

    def _patched_load(*args, **kwargs):
        repo_or_dir = kwargs.get("repo_or_dir") or (args[0] if args else "")
        model = kwargs.get("model") or (args[1] if len(args) > 1 else "")
        if str(repo_or_dir).strip() == "snakers4/silero-vad" and str(model).strip() == "silero_vad":
            state["used"] = True
            LOGGER.warning(
                "Silero VAD download is unavailable; using a local WebRTC-backed fallback so voice mode can still start."
            )
            return _WebRTCSileroVADShim(), None
        return original_load(*args, **kwargs)

    hub.load = _patched_load
    try:
        yield state
    finally:
        hub.load = original_load

def _instantiate_realtimestt_recorder(
    recorder_cls: Callable[..., Any],
    recorder_kwargs: Dict[str, Any],
) -> Tuple[Any, bool]:
    try:
        with _patch_realtimestt_worker_starter():
            return recorder_cls(**recorder_kwargs), False
    except Exception as exc:
        if not _is_realtimestt_silero_load_error(exc):
            raise

        with _patch_torch_hub_silero_fallback() as fallback_state:
            with _patch_realtimestt_worker_starter():
                recorder = recorder_cls(**recorder_kwargs)
        if not fallback_state.get("used"):
            raise
        return recorder, True

def _patch_tqdm_ensure_lock() -> None:
    """Work around tqdm/huggingface disabled progress bar lock teardown bugs.

    Some faster_whisper + huggingface_hub combinations trigger
    ``AttributeError: type object 'disabled_tqdm' has no attribute '_lock'``
    during model download. The failure happens on cleanup in
    ``tqdm.contrib.concurrent.ensure_lock`` when progress bars are disabled.

    Patching the helper once is safer than trying to guess which disabled tqdm
    class instance a particular dependency stack will use.
    """

    global _TQDM_ENSURE_LOCK_PATCHED
    if _TQDM_ENSURE_LOCK_PATCHED:
        return

    try:
        import tqdm.contrib.concurrent as tqdm_concurrent
    except Exception as exc:
        LOGGER.debug("Could not import tqdm.contrib.concurrent for compatibility patch: %s", exc)
        return

    original = getattr(tqdm_concurrent, "ensure_lock", None)
    if original is None:
        return
    if getattr(original, "_autoyou_safe_patch", False):
        _TQDM_ENSURE_LOCK_PATCHED = True
        return

    @contextmanager
    def _safe_ensure_lock(tqdm_class, lock_name=""):
        old_lock = getattr(tqdm_class, "_lock", None)
        lock = old_lock or tqdm_class.get_lock()
        lock = getattr(lock, lock_name, lock)
        tqdm_class.set_lock(lock)
        try:
            yield lock
        finally:
            if old_lock is None:
                try:
                    del tqdm_class._lock
                except AttributeError:
                    pass
            else:
                try:
                    tqdm_class.set_lock(old_lock)
                except Exception:
                    try:
                        setattr(tqdm_class, "_lock", old_lock)
                    except Exception:
                        pass

    _safe_ensure_lock._autoyou_safe_patch = True  # type: ignore[attr-defined]
    tqdm_concurrent.ensure_lock = _safe_ensure_lock
    _TQDM_ENSURE_LOCK_PATCHED = True
    LOGGER.debug("Applied tqdm ensure_lock compatibility patch for RealtimeSTT downloads")

def _realtimestt_thread_worker_override() -> Optional[bool]:
    override = str(os.getenv("AUTOYOU_REALTIMESTT_THREAD_WORKER", "")).strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    return None

def _multiprocessing_default_start_method() -> str:
    """Best-effort name of the start method children will be created with.

    Inspects with ``allow_none=True`` so that merely asking never fixes the
    default context, and falls back to the platform default (documented as the
    first entry of ``get_all_start_methods()``). Returns ``""`` when the method
    cannot be determined; callers treat that as "not fork", i.e. the cautious
    answer.
    """
    try:
        import multiprocessing

        method = multiprocessing.get_start_method(allow_none=True)
        if method:
            return str(method)
        methods = multiprocessing.get_all_start_methods()
        return str(methods[0]) if methods else ""
    except Exception:
        return ""

def _should_use_threaded_realtimestt_worker() -> bool:
    # RealtimeSTT's process-based worker is only safe when the child inherits an
    # already-initialized interpreter. Under ``spawn`` (and ``forkserver``) the
    # child re-runs interpreter bootstrap, which can die importing numpy/cv2
    # before the worker ever starts -- see shared.mp_syspath_guard. So anywhere
    # children are not forked, the transcription worker runs in-process.
    #
    #   darwin - defaults to spawn; always threaded.
    #   win32  - defaults to spawn; always threaded. This used to be opt-in
    #            (compiled runtime or redirected stdio), which left ordinary
    #            source launches from a console on the fragile spawn path.
    #   other  - fork today (safe to keep using a real process), but Python
    #            3.14 switches Linux to forkserver, which re-imports like spawn.
    #            Key off the actual start method rather than the platform name.
    if sys.platform == "darwin":
        return True
    override = _realtimestt_thread_worker_override()
    if override is not None:
        return override
    if sys.platform == "win32":
        return True
    return _multiprocessing_default_start_method() != "fork"

def _should_disable_realtimestt_for_source_redirected_stdio() -> bool:
    if sys.platform != "win32" or _is_compiled_runtime() or not _win32_stdio_redirected():
        return False
    # An explicit AUTOYOU_REALTIMESTT_THREAD_WORKER=1 means the operator opted
    # into the in-process worker (the same machinery compiled Windows builds
    # use), so honor it instead of hard-disabling STT for this launch mode.
    return _realtimestt_thread_worker_override() is not True

def should_disable_live_audio_for_source_redirected_stdio() -> bool:
    if _coerce_enabled_flag(os.getenv("AUTOYOU_ALLOW_REDIRECTED_STDIO_LIVE_AUDIO"), False):
        return False
    return _should_disable_realtimestt_for_source_redirected_stdio()

def _guard_child_sys_path() -> None:
    """Install the multiprocessing sys.path guard, ignoring any failure.

    Imported lazily and defensively (like _is_compiled_runtime) so a packaged
    build that lays the module out differently degrades to the old behaviour
    instead of failing to start the voice pipeline.
    """
    try:
        from shared.mp_syspath_guard import install_multiprocessing_syspath_guard

        install_multiprocessing_syspath_guard()
    except Exception as exc:
        LOGGER.debug("multiprocessing sys.path guard not installed: %s", exc)

def _start_process_like_thread(
    target: Callable[..., Any],
    args: Tuple[Any, ...] = (),
    *,
    name: str,
) -> _ThreadBackedProcessHandle:
    thread_obj = threading.Thread(target=target, args=args, daemon=True, name=name)
    thread_obj.start()
    return _ThreadBackedProcessHandle(thread_obj)

def _should_use_python_tts_subprocess() -> bool:
    try:
        from shared.platform_runtime import is_compiled

        return not is_compiled()
    except Exception:
        return True

def _enumerate_system_tts_voices_in_process() -> List[Dict[str, str]]:
    if pyttsx3 is None:
        return []

    engine = pyttsx3.init()
    try:
        voices: List[Dict[str, str]] = []
        for voice in engine.getProperty("voices") or []:
            languages = getattr(voice, "languages", []) or []
            language_text = ", ".join(
                [
                    _normalize_voice_language(item)
                    for item in languages
                    if _normalize_voice_language(item)
                ]
            )
            voices.append(
                {
                    "id": str(getattr(voice, "id", "") or "").strip(),
                    "name": str(getattr(voice, "name", "") or "").strip(),
                    "gender": str(getattr(voice, "gender", "") or "").strip(),
                    "languages": language_text,
                }
            )
        return voices
    finally:
        try:
            engine.stop()
        except Exception:
            pass

def _enumerate_macos_say_voices() -> List[Dict[str, str]]:
    if sys.platform != "darwin":
        return []

    result = subprocess.run(
        ["say", "-v", "?"],
        check=True,
        capture_output=True,
        timeout=TTS_SUBPROCESS_TIMEOUT_SECONDS,
        text=True,
    )

    voices: List[Dict[str, str]] = []
    for raw_line in (result.stdout or "").splitlines():
        match = _MACOS_SAY_VOICE_RE.match(raw_line.rstrip())
        if match is None:
            continue
        voice_name = match.group("name").strip()
        language = match.group("language").strip()
        if not voice_name:
            continue
        voices.append(
            {
                "id": voice_name,
                "name": voice_name,
                "gender": "",
                "languages": language,
            }
        )
    return voices

def list_system_tts_voices(force_refresh: bool = False) -> List[Dict[str, str]]:
    global _SYSTEM_TTS_VOICE_CACHE

    with _SYSTEM_TTS_VOICE_CACHE_LOCK:
        if _SYSTEM_TTS_VOICE_CACHE is not None and not force_refresh:
            return list(_SYSTEM_TTS_VOICE_CACHE)

        if sys.platform == "darwin":
            try:
                macos_voices = _enumerate_macos_say_voices()
            except Exception as exc:
                LOGGER.warning("Failed to enumerate system TTS voices via macOS say: %s", exc)
            else:
                if macos_voices:
                    _SYSTEM_TTS_VOICE_CACHE = macos_voices
                    return list(_SYSTEM_TTS_VOICE_CACHE)

        if pyttsx3 is None:
            _SYSTEM_TTS_VOICE_CACHE = []
            return []

        script = r"""
import json
import pyttsx3

def normalize_language(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    text = str(value or "").strip()
    if text.startswith("\\x05"):
        text = text[4:]
    return text

engine = pyttsx3.init()
voices = []
for voice in engine.getProperty("voices") or []:
    languages = getattr(voice, "languages", []) or []
    language_text = ", ".join(
        [normalize_language(item) for item in languages if normalize_language(item)]
    )
    voices.append(
        {
            "id": str(getattr(voice, "id", "") or ""),
            "name": str(getattr(voice, "name", "") or ""),
            "gender": str(getattr(voice, "gender", "") or ""),
            "languages": language_text,
        }
    )
print(json.dumps(voices))
"""

        try:
            if _should_use_python_tts_subprocess():
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    check=True,
                    capture_output=True,
                    timeout=TTS_SUBPROCESS_TIMEOUT_SECONDS,
                    text=True,
                )
                parsed = json.loads(result.stdout or "[]")
                voices: List[Dict[str, str]] = []
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    voices.append(
                        {
                            "id": str(item.get("id") or "").strip(),
                            "name": str(item.get("name") or "").strip(),
                            "gender": str(item.get("gender") or "").strip(),
                            "languages": str(item.get("languages") or "").strip(),
                        }
                    )
                _SYSTEM_TTS_VOICE_CACHE = voices
            else:
                _SYSTEM_TTS_VOICE_CACHE = _enumerate_system_tts_voices_in_process()
        except Exception as exc:
            LOGGER.warning("Failed to enumerate system TTS voices: %s", exc)
            try:
                _SYSTEM_TTS_VOICE_CACHE = _enumerate_system_tts_voices_in_process()
            except Exception:
                _SYSTEM_TTS_VOICE_CACHE = []

        voices_list = list(_SYSTEM_TTS_VOICE_CACHE)
        has_custom = any(v.get("id") == CUSTOM_VOICE_SYSTEM_VOICE_ID for v in voices_list)
        if not has_custom and custom_voice_model_ready():
            voices_list.append({
                "id": CUSTOM_VOICE_SYSTEM_VOICE_ID,
                "name": "Custom Trained Voice (Local Cloned)",
                "gender": "user",
                "languages": "en",
            })
        return voices_list

def _sanitize_text_for_tts(text: str) -> str:
    cleaned = html.unescape(str(text or ""))
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("```", "\n")
    cleaned = _TTS_MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    cleaned = _TTS_HEADING_RE.sub("", cleaned)
    cleaned = _TTS_BULLET_RE.sub("", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    cleaned = cleaned.replace("\u2014", ", ").replace("\u2013", ", ")

    sanitized_chars: List[str] = []
    for char in cleaned:
        if char == "\n":
            sanitized_chars.append(". ")
            continue
        if char == "\t":
            sanitized_chars.append(" ")
            continue
        category = unicodedata.category(char)
        if category.startswith("C"):
            continue
        if ord(char) > 0xFFFF or category == "So":
            sanitized_chars.append(" ")
            continue
        sanitized_chars.append(char)

    cleaned = "".join(sanitized_chars)
    cleaned = _TTS_MULTI_SPACE_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned.strip()

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return float(default)

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return int(default)

def _tts_output_ready(file_path: str) -> bool:
    try:
        return os.path.exists(file_path) and os.path.getsize(file_path) >= _TTS_MIN_AUDIO_BYTES
    except OSError:
        return False

def _wait_for_tts_output(file_path: str, timeout_seconds: float = _TTS_OUTPUT_WAIT_SECONDS) -> bool:
    deadline = time.time() + max(0.0, timeout_seconds)
    while True:
        if _tts_output_ready(file_path):
            return True
        if time.time() >= deadline:
            return _tts_output_ready(file_path)
        time.sleep(0.05)

def _macos_tts_has_audio(file_path: str) -> bool:
    # `say` can exit successfully with a header and silent samples when the
    # selected system voice is unavailable. File size alone cannot detect it.
    import av

    try:
        with av.open(file_path) as container:
            return any(frame.to_ndarray().any() for frame in container.decode(audio=0))
    except Exception:
        return False

def _windows_sapi_rate(speech_rate: int) -> int:
    return max(-10, min(10, int(round((speech_rate - SYSTEM_TTS_BASE_RATE) / 15.0))))

def voice_runtime_available(settings: Dict[str, Any]) -> bool:
    """Return True when the configured voice provider can actually run."""
    normalized = normalize_speech_config(settings or {})
    tts_settings = normalized.get("tts") or {}
    provider = str(tts_settings.get("provider") or "system").strip().lower()

    if provider == "off":
        return False

    if provider == CUSTOM_VOICE_PROVIDER:
        return custom_voice_model_ready()

    if provider == "system":
        if str(tts_settings.get("system_voice") or "").strip() == CUSTOM_VOICE_SYSTEM_VOICE_ID:
            return custom_voice_model_ready()
        if sys.platform == "darwin":
            return True
        if pyttsx3 is None:
            return False
        try:
            return bool(list_system_tts_voices())
        except Exception:
            return False

    if provider == "openai":
        openai_cfg = tts_settings.get("openai") or {}
        return bool(httpx and str(openai_cfg.get("api_key") or "").strip())

    if provider == "azure":
        azure_cfg = tts_settings.get("azure") or {}
        return bool(
            speechsdk
            and str(azure_cfg.get("speech_key") or "").strip()
            and str(azure_cfg.get("speech_region") or "").strip()
        )

    if provider == "emotivoice":
        return bool(emotivoice_status()["ready"])

    return False

class BackgroundAudioHeartbeatTrack(MediaStreamTrack):
    """Paced audio frames for iOS recvonly background sessions."""

    kind = "audio"
    DEFAULT_AMPLITUDE = 96
    DEFAULT_FREQUENCY_HZ = 18_000.0

    def __init__(
        self,
        *,
        sample_rate: int = 48000,
        frame_size: int = 960,
        amplitude: Optional[int] = None,
        frequency_hz: Optional[float] = None,
    ):
        super().__init__()
        self.sample_rate = max(8000, int(sample_rate or 48000))
        self.frame_size = max(1, int(frame_size or 960))
        self.time_base = fractions.Fraction(1, self.sample_rate)
        self.pts = 0
        self._start_time: Optional[float] = None
        requested_amplitude = int(
            amplitude
            if amplitude is not None
            else _env_int(
                "AUTOYOU_BACKGROUND_AUDIO_HEARTBEAT_AMPLITUDE",
                self.DEFAULT_AMPLITUDE,
            )
        )
        minimum_amplitude = max(
            0,
            min(
                32767,
                _env_int(
                    "AUTOYOU_BACKGROUND_AUDIO_HEARTBEAT_MIN_AMPLITUDE",
                    self.DEFAULT_AMPLITUDE,
                ),
            ),
        )
        if requested_amplitude <= 0:
            effective_amplitude = 0
        else:
            effective_amplitude = max(requested_amplitude, minimum_amplitude)
        self.amplitude = max(0, min(32767, effective_amplitude))
        self.frequency_hz = max(
            1.0,
            float(
                frequency_hz
                if frequency_hz is not None
                else _env_float(
                    "AUTOYOU_BACKGROUND_AUDIO_HEARTBEAT_FREQUENCY_HZ",
                    self.DEFAULT_FREQUENCY_HZ,
                )
            ),
        )
        self._phase = 0.0
        self._phase_step = 2.0 * math.pi * self.frequency_hz / self.sample_rate
        self._silence_payload = bytes(self.frame_size * 2)

    def _payload(self) -> bytes:
        if self.amplitude <= 0:
            return self._silence_payload
        samples = array.array("h")
        phase = self._phase
        for _ in range(self.frame_size):
            samples.append(int(round(self.amplitude * math.sin(phase))))
            phase += self._phase_step
            if phase >= 2.0 * math.pi:
                phase -= 2.0 * math.pi
        self._phase = phase
        return samples.tobytes()

    async def _pace(self) -> None:
        if self._start_time is None:
            self._start_time = time.time()
        expected_time = self.pts / self.sample_rate
        sleep_time = expected_time - (time.time() - self._start_time)
        if sleep_time < -0.2:
            gap_seconds = -sleep_time
            self.pts += int(gap_seconds * self.sample_rate)
            self._start_time = time.time() - (self.pts / self.sample_rate)
            sleep_time = 0
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
        else:
            await asyncio.sleep(0)

    async def recv(self):
        frame = AudioFrame(format="s16", layout="mono", samples=self.frame_size)
        frame.planes[0].update(self._payload())
        frame.sample_rate = self.sample_rate
        frame.pts = self.pts
        frame.time_base = self.time_base
        self.pts += self.frame_size
        await self._pace()
        return frame

def _derive_realtime_model_type(model_name: str) -> str:
    name = (model_name or "").strip().lower()
    if not name:
        return "tiny"
    if name.endswith(".en"):
        name = name[:-3]
    if name.startswith("distil-"):
        return "small"
    if name.startswith("large"):
        return "small"
    return name

class TTSAudioStreamTrack(MediaStreamTrack):
    """
    A MediaStreamTrack that yields audio frames from a queue (populated by TTS).
    """

    kind = "audio"

    def __init__(self):
        super().__init__()
        self.q = queue.Queue(maxsize=_PLAYBACK_FRAME_BUFFER_SIZE)
        self.pts = 0
        self.sample_rate = 48000
        self.time_base = fractions.Fraction(1, self.sample_rate)
        self.frame_size = 960  # 20ms at 48kHz
        self._playback_lock = threading.Lock()
        self._playback_status_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._playback_status: Dict[str, Any] = {
            "event": "playback",
            "state": "idle",
            "source": "",
            "playback_id": "",
            "file_path": "",
            "file_name": "",
            "position_ms": 0,
            "detail": "",
            "error": "",
            "timestamp_ms": int(time.time() * 1000),
            "buffered_frames": 0,
        }
        self._playback_token = 0
        self._played_samples = 0
        self._producer_done = True
        self._completion_pending = False
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()

    def set_playback_status_callback(
        self,
        callback: Optional[Callable[[Dict[str, Any]], None]],
    ) -> None:
        self._playback_status_callback = callback

    def _buffered_frame_count(self) -> int:
        try:
            return int(self.q.qsize())
        except Exception:
            return 0

    def _snapshot_playback_status(self) -> Dict[str, Any]:
        with self._playback_lock:
            snapshot = dict(self._playback_status)
        snapshot["buffered_frames"] = self._buffered_frame_count()
        return snapshot

    def get_playback_status(self) -> Dict[str, Any]:
        return self._snapshot_playback_status()

    def is_rendering_media(self) -> bool:
        """True while this track is carrying file/media playback (not TTS speech).

        ``AudioManager`` falls back to the TTS lane when no dedicated media lane
        was registered, so the two roles can share one track. Callers that stop
        *speech* must not cancel music that happens to be sharing this queue.
        """
        with self._playback_lock:
            state = str(self._playback_status.get("state") or "")
            source = str(self._playback_status.get("source") or "").strip().lower()
        return state in {"playing", "paused"} and source not in {"", "tts"}

    def is_rendering_speech(self) -> bool:
        """True while this track is carrying a synthesized TTS reply."""
        with self._playback_lock:
            state = str(self._playback_status.get("state") or "")
            source = str(self._playback_status.get("source") or "").strip().lower()
        return state in {"playing", "paused"} and source == "tts"

    def _emit_playback_status(self, **updates: Any) -> Dict[str, Any]:
        callback = None
        with self._playback_lock:
            status = dict(self._playback_status)
            status.update({k: v for k, v in updates.items() if v is not None})
            status.setdefault("event", "playback")
            status["position_ms"] = int(self._played_samples * 1000 / self.sample_rate)
            status["timestamp_ms"] = int(time.time() * 1000)
            status["buffered_frames"] = self._buffered_frame_count()
            self._playback_status = status
            snapshot = dict(status)
            callback = self._playback_status_callback

        if callback is not None:
            try:
                callback(snapshot)
            except Exception as exc:
                LOGGER.debug("Playback status callback failed: %s", exc)
        return snapshot

    def _clear_buffered_frames(self) -> int:
        cleared = 0
        while not self.q.empty():
            try:
                self.q.get_nowait()
                cleared += 1
            except Exception:
                break
        return cleared

    def _mark_playback_completed_if_ready(self) -> None:
        should_complete = False
        with self._playback_lock:
            state = str(self._playback_status.get("state") or "")
            should_complete = (
                self._completion_pending
                and self._producer_done
                and self.q.empty()
                and state in {"playing", "paused"}
            )
            if should_complete:
                self._completion_pending = False
        if should_complete:
            self._emit_playback_status(
                state="completed",
                detail="Playback completed.",
                error="",
            )

    def _begin_playback(
        self,
        file_path: str,
        *,
        source: str,
    ) -> Tuple[int, threading.Event]:
        normalized_path = os.path.abspath(os.path.expanduser(str(file_path or "").strip()))
        had_active_playback = False
        previous_stop_event = None

        with self._playback_lock:
            had_active_playback = self._playback_token > 0
            previous_stop_event = self._stop_event
            self._playback_token += 1
            token = self._playback_token
            self._stop_event = threading.Event()
            self._pause_event = threading.Event()
            self._pause_event.set()
            self._producer_done = False
            self._completion_pending = False
            self._played_samples = 0
            self._playback_status = {
                "event": "playback",
                "state": "playing",
                "source": str(source or "audio_file"),
                "playback_id": uuid.uuid4().hex,
                "file_path": normalized_path,
                "file_name": os.path.basename(normalized_path),
                "position_ms": 0,
                "detail": "Playback started.",
                "error": "",
                "timestamp_ms": int(time.time() * 1000),
                "buffered_frames": 0,
            }

        if had_active_playback and previous_stop_event is not None:
            previous_stop_event.set()
        cleared = self._clear_buffered_frames()
        if cleared:
            LOGGER.info("Cleared %d buffered playback frames before starting a new audio file", cleared)
        self._emit_playback_status(state="playing", detail="Playback started.", error="")
        return token, self._stop_event

    def _playback_should_abort(
        self,
        token: int,
        stop_event: threading.Event,
        should_abort: Optional[Callable[[], bool]],
    ) -> bool:
        if stop_event.is_set():
            return True
        with self._playback_lock:
            if token != self._playback_token:
                return True
        if should_abort is None:
            return False
        try:
            return bool(should_abort())
        except Exception as exc:
            LOGGER.debug("Playback abort callback failed: %s", exc)
            return False

    def _enqueue_audio_frame(
        self,
        token: int,
        stop_event: threading.Event,
        frame: AudioFrame,
        should_abort: Optional[Callable[[], bool]],
    ) -> bool:
        while True:
            if self._playback_should_abort(token, stop_event, should_abort):
                return False
            try:
                self.q.put((token, frame), timeout=0.25)
                return True
            except queue.Full:
                continue

    def _stream_audio_file_to_queue(
        self,
        file_path: str,
        *,
        token: int,
        stop_event: threading.Event,
        should_abort: Optional[Callable[[], bool]] = None,
        loop: bool = False,
    ) -> None:
        import av

        try:
            while True:
                if self._playback_should_abort(token, stop_event, should_abort):
                    return
                with av.open(file_path) as container:
                    stream = container.streams.audio[0]
                    resampler = av.AudioResampler(format="s16", layout="mono", rate=self.sample_rate)
                    pending_bytes = bytearray()
                    bytes_per_sample = 2
                    chunk_bytes = self.frame_size * bytes_per_sample

                    def _enqueue_pending_frames(*, flush: bool = False) -> bool:
                        while len(pending_bytes) >= chunk_bytes or (flush and pending_bytes):
                            if self._playback_should_abort(token, stop_event, should_abort):
                                LOGGER.info("Playback queue aborted while enqueuing audio frames")
                                return False

                            if len(pending_bytes) >= chunk_bytes:
                                chunk = bytes(pending_bytes[:chunk_bytes])
                                del pending_bytes[:chunk_bytes]
                            else:
                                chunk = bytes(pending_bytes)
                                chunk += bytes(chunk_bytes - len(pending_bytes))
                                pending_bytes.clear()

                            new_frame = AudioFrame(format="s16", layout="mono", samples=self.frame_size)
                            new_frame.planes[0].update(chunk)
                            new_frame.sample_rate = self.sample_rate
                            new_frame.time_base = self.time_base
                            if not self._enqueue_audio_frame(token, stop_event, new_frame, should_abort):
                                return False

                        return True

                    for frame in container.decode(stream):
                        if self._playback_should_abort(token, stop_event, should_abort):
                            LOGGER.info("Playback queue aborted while decoding audio file")
                            return
                        for resampled in resampler.resample(frame):
                            if self._playback_should_abort(token, stop_event, should_abort):
                                LOGGER.info("Playback queue aborted while resampling audio file")
                                return
                            pending_bytes.extend(resampled.to_ndarray().tobytes())
                            if not _enqueue_pending_frames():
                                return

                    for resampled in resampler.resample(None):
                        if self._playback_should_abort(token, stop_event, should_abort):
                            LOGGER.info("Playback queue aborted during final audio flush")
                            return
                        pending_bytes.extend(resampled.to_ndarray().tobytes())
                        if not _enqueue_pending_frames():
                            return

                    _enqueue_pending_frames(flush=True)
                
                if not loop:
                    break
        except Exception as exc:
            self._emit_playback_status(
                state="error",
                detail=f"Playback failed: {exc}",
                error=str(exc),
            )
            LOGGER.error("Error queuing audio file: %s", exc)
        finally:
            with self._playback_lock:
                if token != self._playback_token:
                    return
                self._producer_done = True
                if not stop_event.is_set() and str(self._playback_status.get("state") or "") != "error":
                    self._completion_pending = True

    def play_audio_file(
        self,
        file_path: str,
        *,
        source: str = "audio_file",
        should_abort: Optional[Callable[[], bool]] = None,
        loop: bool = False,
    ) -> Dict[str, Any]:
        token, stop_event = self._begin_playback(file_path, source=source)
        threading.Thread(
            target=self._stream_audio_file_to_queue,
            args=(file_path,),
            kwargs={
                "token": token,
                "stop_event": stop_event,
                "should_abort": should_abort,
                "loop": loop,
            },
            daemon=True,
            name="Audio-Playback-Producer",
        ).start()
        return self.get_playback_status()

    async def recv(self):
        if not hasattr(self, "_start_time"):
            self._start_time = time.time()

        if not self._pause_event.is_set():
            self._mark_playback_completed_if_ready()
            frame = AudioFrame(format="s16", layout="mono", samples=self.frame_size)
            for plane in frame.planes:
                plane.update(bytes(frame.samples * 2))
            frame.pts = self.pts
            frame.sample_rate = self.sample_rate
            frame.time_base = self.time_base
            self.pts += self.frame_size

            expected_time = self.pts / self.sample_rate
            sleep_time = expected_time - (time.time() - self._start_time)

            if sleep_time < -0.2:
                gap_seconds = -sleep_time
                gap_samples = int(gap_seconds * self.sample_rate)
                self.pts += gap_samples
                frame.pts += gap_samples
                expected_time = self.pts / self.sample_rate
                self._start_time = time.time() - expected_time
                sleep_time = 0

            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            else:
                await asyncio.sleep(0)
            return frame

        next_frame: Optional[AudioFrame] = None
        while not self.q.empty():
            try:
                queued_item = self.q.get_nowait()
            except Exception:
                break

            if isinstance(queued_item, tuple) and len(queued_item) == 2:
                token, frame_candidate = queued_item
            else:
                token, frame_candidate = self._playback_token, queued_item

            if token != self._playback_token:
                continue
            next_frame = frame_candidate
            break

        if next_frame is None:
            self._mark_playback_completed_if_ready()
            frame = AudioFrame(format="s16", layout="mono", samples=self.frame_size)
            for plane in frame.planes:
                plane.update(bytes(frame.samples * 2))
            frame.pts = self.pts
            frame.sample_rate = self.sample_rate
            frame.time_base = self.time_base
            self.pts += self.frame_size

            expected_time = self.pts / self.sample_rate
            sleep_time = expected_time - (time.time() - self._start_time)

            if sleep_time < -0.2:
                gap_seconds = -sleep_time
                gap_samples = int(gap_seconds * self.sample_rate)
                self.pts += gap_samples
                frame.pts += gap_samples
                expected_time = self.pts / self.sample_rate
                self._start_time = time.time() - expected_time
                sleep_time = 0

            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            else:
                await asyncio.sleep(0)
            return frame

        frame = next_frame
        frame.pts = self.pts
        frame.time_base = self.time_base
        self.pts += frame.samples
        self._played_samples += frame.samples

        expected_time = self.pts / self.sample_rate
        sleep_time = expected_time - (time.time() - self._start_time)
        if sleep_time < -0.2:
            LOGGER.warning(
                "Audio pacing fell behind by %.2fs due to event loop blocking; rebasing clock.",
                -sleep_time,
            )
            gap_seconds = -sleep_time
            gap_samples = int(gap_seconds * self.sample_rate)
            self.pts += gap_samples
            frame.pts += gap_samples
            expected_time = self.pts / self.sample_rate
            self._start_time = time.time() - expected_time
            sleep_time = 0

        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
        else:
            await asyncio.sleep(0)
        self._mark_playback_completed_if_ready()
        return frame

    def pause_playback(self) -> bool:
        with self._playback_lock:
            state = str(self._playback_status.get("state") or "")
            if state not in {"playing", "paused"}:
                return False
            already_paused = state == "paused"
            self._pause_event.clear()
        if not already_paused:
            self._emit_playback_status(state="paused", detail="Playback paused.", error="")
        return True

    def resume_playback(self) -> bool:
        with self._playback_lock:
            state = str(self._playback_status.get("state") or "")
            if state != "paused":
                return False
            self._pause_event.set()
        self._emit_playback_status(state="playing", detail="Playback resumed.", error="")
        return True

    def stop_playback(self, reason: str = "Playback stopped."):
        """Immediately clear all queued TTS or media playback frames."""
        with self._playback_lock:
            self._playback_token += 1
            stop_event = self._stop_event
            self._stop_event = threading.Event()
            self._pause_event = threading.Event()
            self._pause_event.set()
            self._producer_done = True
            self._completion_pending = False
        stop_event.set()
        cleared = self._clear_buffered_frames()
        self._emit_playback_status(state="stopped", detail=reason, error="")
        if cleared:
            LOGGER.info("TTS playback interrupted: cleared %d queued audio frames", cleared)
        return bool(cleared)

    def queue_audio_file(
        self,
        file_path: str,
        *,
        should_abort: Optional[Callable[[], bool]] = None,
    ):
        """Read audio file and queue frames uniformly resampled to 48kHz for WebRTC."""
        token, stop_event = self._begin_playback(file_path, source="tts")
        self._stream_audio_file_to_queue(
            file_path,
            token=token,
            stop_event=stop_event,
            should_abort=should_abort,
        )

class MixedAudioStreamTrack(MediaStreamTrack):
    """Mix multiple outbound 48 kHz mono audio tracks into one WebRTC track."""

    kind = "audio"

    def __init__(
        self,
        sources: Optional[List[Tuple[str, Any]]] = None,
        *,
        sample_rate: int = 48000,
        frame_size: int = 960,
    ):
        super().__init__()
        self.sample_rate = int(sample_rate or 48000)
        self.frame_size = int(frame_size or 960)
        self.time_base = fractions.Fraction(1, self.sample_rate)
        self.pts = 0
        self._start_time: Optional[float] = None
        self._sources_lock = threading.Lock()
        self._sources: List[Tuple[str, Any]] = []
        self._resamplers: Dict[str, Any] = {}
        for name, track in sources or []:
            self.add_source(name, track)

    def add_source(self, name: str, track: Any) -> None:
        if track is None:
            return
        normalized = str(name or type(track).__name__ or "source").strip() or "source"
        with self._sources_lock:
            self._sources.append((normalized, track))

    def source_names(self) -> List[str]:
        with self._sources_lock:
            return [name for name, _track in self._sources]

    def _remove_source(self, name: str, track: Any) -> None:
        with self._sources_lock:
            self._sources = [
                (source_name, source_track)
                for source_name, source_track in self._sources
                if source_name != name or source_track is not track
            ]
            self._resamplers.pop(name, None)

    def _silence_frame(self) -> AudioFrame:
        frame = AudioFrame(format="s16", layout="mono", samples=self.frame_size)
        frame.planes[0].update(bytes(self.frame_size * 2))
        frame.sample_rate = self.sample_rate
        frame.pts = self.pts
        frame.time_base = self.time_base
        self.pts += self.frame_size
        return frame

    async def _pace_if_needed(self) -> None:
        if self._start_time is None:
            self._start_time = time.time()
        expected_time = self.pts / self.sample_rate
        sleep_time = expected_time - (time.time() - self._start_time)
        if sleep_time < -0.2:
            gap_seconds = -sleep_time
            self.pts += int(gap_seconds * self.sample_rate)
            self._start_time = time.time() - (self.pts / self.sample_rate)
            sleep_time = 0
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
        else:
            await asyncio.sleep(0)

    def _frame_to_samples(self, source_name: str, frame: AudioFrame) -> Any:
        frame_sample_rate = int(getattr(frame, "sample_rate", 0) or self.sample_rate)
        frame_layout = str(getattr(getattr(frame, "layout", None), "name", "") or "").lower()
        frame_format = str(getattr(getattr(frame, "format", None), "name", "") or "").lower()

        frames = [frame]
        if frame_sample_rate != self.sample_rate or frame_layout != "mono" or frame_format != "s16":
            import av

            resampler = self._resamplers.get(source_name)
            if resampler is None:
                resampler = av.AudioResampler(format="s16", layout="mono", rate=self.sample_rate)
                self._resamplers[source_name] = resampler
            frames = list(resampler.resample(frame))
            if not frames:
                return None

        chunks = []
        for normalized_frame in frames:
            array = normalized_frame.to_ndarray().astype("int32").reshape(-1)
            if array.size:
                chunks.append(array)
        if not chunks:
            return None

        import numpy as np

        samples = np.concatenate(chunks)
        if samples.size < self.frame_size:
            samples = np.pad(samples, (0, self.frame_size - samples.size), mode="constant")
        elif samples.size > self.frame_size:
            samples = samples[: self.frame_size]
        return samples

    def _mixed_frame(self, sample_arrays: List[Any]) -> AudioFrame:
        if not sample_arrays:
            return self._silence_frame()

        import numpy as np

        mixed = np.zeros(self.frame_size, dtype=np.int32)
        for samples in sample_arrays:
            mixed += samples.astype(np.int32, copy=False)
        # Apply a cheap frame limiter instead of hard-clipping every overloaded
        # sample. This preserves the mic/loopback/TTS balance when two loud
        # sources overlap and remains a single vectorized pass on low-end CPUs.
        peak = int(np.max(np.abs(mixed))) if mixed.size else 0
        if peak > 32767:
            mixed = np.rint(mixed.astype(np.float32) * (32767.0 / peak)).astype(np.int32)
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)

        frame = AudioFrame(format="s16", layout="mono", samples=self.frame_size)
        frame.planes[0].update(mixed.tobytes())
        frame.sample_rate = self.sample_rate
        frame.pts = self.pts
        frame.time_base = self.time_base
        self.pts += self.frame_size
        return frame

    async def recv(self):
        with self._sources_lock:
            sources = list(self._sources)

        if not sources:
            frame = self._silence_frame()
            await self._pace_if_needed()
            return frame

        results = await asyncio.gather(
            *(track.recv() for _name, track in sources),
            return_exceptions=True,
        )

        sample_arrays: List[Any] = []
        for (source_name, source_track), result in zip(sources, results):
            if isinstance(result, Exception):
                LOGGER.info("Removing audio mixer source %s after recv failure: %s", source_name, result)
                self._remove_source(source_name, source_track)
                continue
            try:
                samples = self._frame_to_samples(source_name, result)
            except Exception as exc:
                LOGGER.debug("Skipping audio mixer source %s frame: %s", source_name, exc)
                continue
            if samples is not None:
                sample_arrays.append(samples)

        return self._mixed_frame(sample_arrays)

class AudioTrackSink(MediaStreamTrack):
    """
    Consumes audio frames from WebRTC and feeds them to RealtimeSTT.
    """

    kind = "audio"

    def __init__(self, track, recorder_feed_callback):
        super().__init__()
        self.track = track
        self.recorder_feed_callback = recorder_feed_callback
        self.task = None
        self._received_audio_chunks = 0

        import av

        self.resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)

    async def recv(self):
        frame = AudioFrame(format="s16", layout="mono", samples=960)
        for plane in frame.planes:
            plane.update(bytes(frame.samples * 2))
        frame.pts = 0
        frame.sample_rate = 48000
        frame.time_base = fractions.Fraction(1, 48000)
        await asyncio.sleep(0.02)
        return frame

    async def start(self):
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _run(self):
        LOGGER.info("AudioTrackSink started")
        try:
            from aiortc.mediastreams import MediaStreamError
        except ImportError:
            MediaStreamError = Exception

        try:
            while True:
                try:
                    frame = await self.track.recv()
                except MediaStreamError:
                    LOGGER.info("AudioTrackSink: Media stream ended normally.")
                    break
                except asyncio.CancelledError:
                    LOGGER.info("AudioTrackSink: Task cancelled.")
                    break

                for resampled in self.resampler.resample(frame):
                    chunk = resampled.to_ndarray().tobytes()
                    self._received_audio_chunks += 1
                    if self._received_audio_chunks == 1:
                        LOGGER.info(
                            "AudioTrackSink: received first inbound audio frame "
                            "sample_rate=%s samples=%s",
                            getattr(resampled, "sample_rate", None),
                            getattr(resampled, "samples", None),
                        )
                    self.recorder_feed_callback(chunk)
        except Exception as exc:
            if "Track is closed" not in str(exc):
                LOGGER.error("AudioTrackSink error: %s", exc)

class AudioManager:
    """
    Manages STT and TTS for a session.
    """

    def __init__(
        self,
        on_text_callback: Callable[[str], None],
        settings_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        enable_stt: bool = True,
    ):
        self.on_text_callback = on_text_callback
        self.settings_provider = settings_provider or deepcopy_speech_config
        self.status_callback = status_callback
        self._stt_enabled = bool(enable_stt)
        self._stt_disabled_reason: Optional[str] = None
        self.recorder = None
        self.tts_available = False
        self.tts_track: Optional[TTSAudioStreamTrack] = None
        self.playback_track: Optional[TTSAudioStreamTrack] = None
        self.input_queue = queue.Queue(maxsize=_STT_WARMUP_AUDIO_QUEUE_MAX_CHUNKS)
        self._stt_initialization_event = threading.Event()
        self._warmup_audio_logged = False
        self._warmup_audio_dropped_chunks = 0
        self._closed = False
        self._settings_lock = threading.Lock()
        self._stt_generation = 0
        self._stt_init_retry_count = 0
        # Serializes the retry decision so the watchdog and the init-failure
        # handler cannot both schedule a retry for the same init generation.
        self._stt_init_retry_lock = threading.Lock()
        self._stt_init_retry_scheduled_generation = -1
        self._stt_init_retry_max = max(0, _env_int("AUTOYOU_STT_INIT_RETRY_MAX", 1))
        self._stt_init_retry_delay_seconds = max(
            0.0,
            _env_float("AUTOYOU_STT_INIT_RETRY_DELAY_SECONDS", 2.0),
        )
        self._stt_init_timeout_seconds = max(
            0.0,
            _env_float("AUTOYOU_STT_INIT_TIMEOUT_SECONDS", 180.0),
        )
        self._tts_generation = 0
        self._tts_generation_lock = threading.Lock()
        self._stop_tts_event = threading.Event()  # set to cancel in-flight TTS synthesis
        self._settings_snapshot = normalize_speech_config(self.settings_provider())
        self._last_status_signature: Optional[Tuple[str, str]] = None
        self._last_readiness_status: Optional[Dict[str, Any]] = None
        self._feed_audio_thread: Optional[threading.Thread] = None
        self._voice_capture_enabled = True
        self._voice_capture_buffer = bytearray()
        self._is_capturing_voice = False
        self._voice_capture_lock = threading.Lock()
        self._segmentation_hold = False
        self._pre_hold_silence_duration: Optional[float] = None
        self._held_recording_started_at: Optional[float] = None
        if self._stt_enabled:
            self._feed_audio_thread = threading.Thread(
                target=self._feed_audio_loop,
                daemon=True,
                name="STT-Audio-Feeder",
            )
            self._feed_audio_thread.start()
        self._init_thread: Optional[threading.Thread] = None
        self._transcription_thread: Optional[threading.Thread] = None
        self.reload_settings(initial=True)
        if self._stt_disabled_reason:
            self._emit_status("unavailable", self._stt_disabled_reason)

    def set_tts_track(self, track: TTSAudioStreamTrack):
        self.tts_track = track

    def set_playback_track(self, track: TTSAudioStreamTrack):
        self.playback_track = track
        if hasattr(track, "set_playback_status_callback"):
            track.set_playback_status_callback(self._handle_playback_status)

    def _get_playback_track(self) -> Optional[TTSAudioStreamTrack]:
        # ponytail: remove the TTS fallback once every server path registers playback_track.
        return getattr(self, "playback_track", None) or getattr(self, "tts_track", None)

    def _handle_playback_status(self, payload: Dict[str, Any]) -> None:
        callback = self.status_callback
        if callback is None:
            return

        normalized = dict(payload or {})
        file_path = str(normalized.pop("file_path", "") or "").strip()
        if file_path and not normalized.get("file_name"):
            normalized["file_name"] = os.path.basename(file_path)
        normalized.setdefault("event", "playback")
        normalized.setdefault("platform", "server")
        normalized.setdefault("timestamp_ms", int(time.time() * 1000))

        try:
            callback(normalized)
        except Exception as exc:
            LOGGER.debug("Playback status callback failed: %s", exc)

    def _emit_status(self, state: str, detail: str) -> None:
        signature = (state, detail)
        if signature == self._last_status_signature and self._last_readiness_status is not None:
            return
        self._last_status_signature = signature

        payload = {
            "event": "readiness",
            "state": state,
            "detail": detail,
            "timestamp_ms": int(time.time() * 1000),
            "platform": "server",
        }
        self._last_readiness_status = dict(payload)

        callback = self.status_callback
        if callback is None:
            return

        try:
            callback(payload)
        except Exception as exc:
            LOGGER.debug("Voice status callback failed: %s", exc)

    def get_readiness_status(self) -> Optional[Dict[str, Any]]:
        if self._closed:
            return {
                "event": "readiness",
                "state": "unavailable",
                "detail": "Voice pipeline is closed.",
                "timestamp_ms": int(time.time() * 1000),
                "platform": "server",
            }
        if self.recorder is not None:
            return {
                "event": "readiness",
                "state": "ready",
                "detail": "Voice pipeline ready.",
                "timestamp_ms": int(time.time() * 1000),
                "platform": "server",
            }
        payload = self._last_readiness_status
        if isinstance(payload, dict):
            return dict(payload)
        if self._stt_enabled:
            return {
                "event": "readiness",
                "state": "warming",
                "detail": "Preparing voice pipeline on server...",
                "timestamp_ms": int(time.time() * 1000),
                "platform": "server",
            }
        return None

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stt_generation += 1
        tts_generation_lock = getattr(self, "_tts_generation_lock", None)
        if tts_generation_lock is not None:
            with tts_generation_lock:
                self._tts_generation += 1
        else:
            self._tts_generation = getattr(self, "_tts_generation", 0) + 1
        stop_tts_event = getattr(self, "_stop_tts_event", None)
        if stop_tts_event is not None:
            stop_tts_event.set()
        tracks = [getattr(self, "tts_track", None), getattr(self, "playback_track", None)]
        if tracks[0] is tracks[1]:
            tracks.pop()
        for track in tracks:
            if track is None:
                continue
            try:
                track.stop_playback("Audio manager closed.")
            except TypeError:
                track.stop_playback()
            except Exception as exc:
                LOGGER.debug("Failed to stop outbound playback during close: %s", exc)
        recorder = self.recorder
        self.recorder = None
        self._stt_initialization_event.set()
        try:
            self.input_queue.put_nowait(None)
        except queue.Full:
            # A slow STT startup may have filled the bounded warm-up backlog.
            # Drop one audio chunk so the feeder always receives its shutdown
            # sentinel.
            try:
                self.input_queue.get_nowait()
                self.input_queue.put_nowait(None)
            except Exception:
                pass
        except Exception:
            pass
        self._shutdown_recorder(recorder)
        _join_thread(self._transcription_thread, "STT transcription")
        _join_thread(self._init_thread, "STT init")
        _join_thread(self._feed_audio_thread, "STT feeder")

    def reload_settings(self, initial: bool = False):
        settings = normalize_speech_config(self.settings_provider())
        with self._settings_lock:
            old_settings = self._settings_snapshot
            self._settings_snapshot = settings

        tts_changed = not initial and old_settings.get("tts") != settings.get("tts")
        stt_changed = initial or old_settings.get("stt") != settings.get("stt")

        if tts_changed:
            # On Linux/WSL in Python mode, pyttsx3 synthesis often runs in a
            # short-lived subprocess. Cancel any in-flight speech first so the
            # next utterance picks up the new voice/rate immediately.
            self.stop_speaking(source="reload_settings")
            self._emit_status("warming", "Applying new voice settings…")
            LOGGER.info("TTS settings changed, reconfiguring (provider=%s rate=%s voice=%s)",
                        settings["tts"]["provider"], settings["tts"]["rate"],
                        settings["tts"].get("system_voice", ""))

        self._setup_tts(settings)

        if stt_changed and self._stt_enabled:
            self._restart_stt(settings, initial=initial)
        elif tts_changed:
            # Only TTS changed - STT is still running, emit ready immediately.
            self._emit_status("ready", "Voice pipeline ready.")

    def _current_settings(self) -> Dict[str, Any]:
        settings = normalize_speech_config(self.settings_provider())
        with self._settings_lock:
            self._settings_snapshot = settings
        return settings

    def _setup_tts(self, settings: Dict[str, Any]):
        provider = settings["tts"]["provider"]
        if provider == "off":
            self.tts_available = False
            LOGGER.debug("TTS disabled in speech settings")
            return
        if provider == CUSTOM_VOICE_PROVIDER:
            self.tts_available = custom_voice_model_ready()
            if self.tts_available:
                LOGGER.info("Custom voice TTS provider available")
            else:
                LOGGER.warning("Custom voice TTS selected but no prepared custom voice model was found")
            return
        if provider == "system":
            if settings["tts"].get("system_voice") == CUSTOM_VOICE_SYSTEM_VOICE_ID:
                self.tts_available = custom_voice_model_ready()
                if self.tts_available:
                    LOGGER.info("Custom voice TTS provider available via legacy system voice selection")
                else:
                    LOGGER.warning("Legacy custom system voice selected but no prepared custom voice model was found")
                return
            if sys.platform == "darwin":
                # macOS: prefer the native `say` binary in both source and compiled
                # runs. It avoids the pyttsx3 subprocess hangs seen in source mode.
                self.tts_available = True
                LOGGER.info("System TTS provider available (macOS say)")
                return
            self.tts_available = pyttsx3 is not None
            if self.tts_available:
                voices = list_system_tts_voices()
                self.tts_available = bool(voices)
            if self.tts_available:
                LOGGER.info("System TTS provider available")
            else:
                if _PYTTSX3_IMPORT_ERROR is not None:
                    LOGGER.warning(
                        "System TTS provider unavailable: pyttsx3 import failed: %s",
                        _PYTTSX3_IMPORT_ERROR,
                    )
                elif pyttsx3 is not None:
                    LOGGER.warning(
                        "System TTS provider unavailable: pyttsx3 backend is not runnable "
                        "(install espeak/espeak-ng on Linux if using system TTS)"
                    )
                else:
                    LOGGER.warning("System TTS provider unavailable: pyttsx3 not installed")
            return
        if provider == "openai":
            self.tts_available = bool(httpx and settings["tts"]["openai"]["api_key"])
            if not self.tts_available:
                LOGGER.warning("OpenAI TTS selected but API key/httpx is unavailable")
            return
        if provider == "azure":
            azure_cfg = settings["tts"]["azure"]
            self.tts_available = bool(
                speechsdk and azure_cfg["speech_key"] and azure_cfg["speech_region"]
            )
            if not self.tts_available:
                LOGGER.warning("Azure TTS selected but Speech SDK/key/region is unavailable")
            return
        if provider == "emotivoice":
            voice_status = emotivoice_status()
            self.tts_available = bool(voice_status["ready"])
            if not self.tts_available:
                LOGGER.warning("EmotiVoice TTS is not ready: %s", voice_status)
            return
        self.tts_available = False
        LOGGER.warning("Unknown TTS provider selected: %s", provider)

    def _restart_stt(self, settings: Dict[str, Any], initial: bool = False, retrying: bool = False):
        if self._closed:
            return
        if not retrying:
            self._stt_init_retry_count = 0
        self._stt_generation += 1
        generation = self._stt_generation
        old_recorder = self.recorder
        self.recorder = None
        self._stt_initialization_event.clear()
        self._warmup_audio_logged = False
        self._warmup_audio_dropped_chunks = 0
        if old_recorder is not None:
            self._shutdown_recorder(old_recorder)
        if not initial:
            LOGGER.info(
                "Reloading STT with model=%s language=%s compute_type=%s",
                settings["stt"]["model"],
                settings["stt"]["language"],
                settings["stt"]["compute_type"],
            )
        self._emit_status(
            "warming",
            f"Preparing voice pipeline on server ({settings['stt']['model']})...",
        )
        self._init_thread = threading.Thread(
            target=self._init_stt,
            args=(generation, settings),
            daemon=True,
            name=f"RealtimeSTT-Init-{generation}",
        )
        self._init_thread.start()
        self._start_stt_init_watchdog(generation, settings)

    def _start_stt_init_watchdog(self, generation: int, settings: Dict[str, Any]) -> None:
        timeout_seconds = float(getattr(self, "_stt_init_timeout_seconds", 0.0) or 0.0)
        if timeout_seconds <= 0:
            return

        def _watch() -> None:
            time.sleep(timeout_seconds)
            if self._closed or generation != self._stt_generation or self.recorder is not None:
                return
            init_thread = getattr(self, "_init_thread", None)
            if init_thread is not None and not init_thread.is_alive():
                return
            LOGGER.warning(
                "RealtimeSTT initialization generation %s did not finish after %.1fs",
                generation,
                timeout_seconds,
            )
            if self._schedule_stt_init_retry(
                settings,
                generation,
                f"timed out after {timeout_seconds:.0f}s while starting",
            ):
                return
            self._emit_status(
                "unavailable",
                f"Voice pipeline timed out after {timeout_seconds:.0f} seconds while starting.",
            )

        threading.Thread(
            target=_watch,
            daemon=True,
            name=f"RealtimeSTT-Watchdog-{generation}",
        ).start()

    def _schedule_stt_init_retry(self, settings: Dict[str, Any], generation: int, reason: str) -> bool:
        # Returns True when a retry is (or already was) scheduled for this init
        # generation, so callers suppress the terminal "unavailable" status.
        with self._stt_init_retry_lock:
            if self._closed or generation != self._stt_generation or self.recorder is not None:
                return False
            if self._stt_init_retry_scheduled_generation == generation:
                # The watchdog and the init-failure handler can both fire for the
                # same generation; only the first one schedules the retry.
                return True
            retry_max = int(getattr(self, "_stt_init_retry_max", 0) or 0)
            if self._stt_init_retry_count >= retry_max:
                return False
            self._stt_init_retry_count += 1
            self._stt_init_retry_scheduled_generation = generation
            retry_number = self._stt_init_retry_count
        delay_seconds = float(getattr(self, "_stt_init_retry_delay_seconds", 0.0) or 0.0)
        LOGGER.warning(
            "RealtimeSTT initialization %s; retrying voice pipeline (%d/%d) in %.1fs",
            reason,
            retry_number,
            retry_max,
            delay_seconds,
        )
        self._emit_status(
            "warming",
            f"Voice pipeline {reason}; retrying ({retry_number}/{retry_max})...",
        )

        def _retry() -> None:
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            if self._closed or generation != self._stt_generation or self.recorder is not None:
                return
            self._restart_stt(settings, initial=False, retrying=True)

        threading.Thread(
            target=_retry,
            daemon=True,
            name=f"RealtimeSTT-Retry-{generation}-{retry_number}",
        ).start()
        return True

    def _shutdown_recorder(self, recorder):
        if recorder is None:
            return
        for event_name in (
            "shutdown_event",
            "interrupt_stop_event",
            "start_recording_event",
            "stop_recording_event",
        ):
            try:
                event = getattr(recorder, event_name, None)
                if event is not None and hasattr(event, "set"):
                    event.set()
            except Exception as exc:
                LOGGER.debug("Recorder event %s failed during shutdown prep: %s", event_name, exc)
        for attr_name, value in (("is_running", False), ("is_recording", False)):
            try:
                if hasattr(recorder, attr_name):
                    setattr(recorder, attr_name, value)
            except Exception as exc:
                LOGGER.debug("Recorder attr %s failed during shutdown prep: %s", attr_name, exc)
        for method_name in ("stop", "shutdown"):
            try:
                method = getattr(recorder, method_name, None)
                if callable(method):
                    method()
            except Exception as exc:
                LOGGER.debug("Recorder %s failed during shutdown: %s", method_name, exc)

    def _set_recorder_ready(self, recorder) -> None:
        self.recorder = recorder
        if self._segmentation_hold:
            # A hold engaged before/during STT init (or across a recorder
            # restart) must land on the freshly configured recorder.
            self._pre_hold_silence_duration = None
            try:
                self._apply_segmentation_hold_to_recorder(recorder)
                LOGGER.info("Re-applied WUIFT segmentation hold to new recorder")
            except Exception as exc:
                LOGGER.warning("Failed to apply WUIFT segmentation hold to new recorder: %s", exc)
        try:
            pending_chunks = self.input_queue.qsize()
        except Exception:
            pending_chunks = 0
        if pending_chunks:
            LOGGER.info(
                "RealtimeSTT ready; replaying %s buffered inbound audio chunks",
                pending_chunks,
            )
        if self._warmup_audio_dropped_chunks:
            LOGGER.warning(
                "Dropped %s oldest buffered inbound audio chunks while STT warmed up",
                self._warmup_audio_dropped_chunks,
            )
        self._stt_initialization_event.set()

    def _mark_stt_initialization_unavailable(self) -> None:
        # Wake the feeder so it can discard any remaining warm-up backlog
        # instead of waiting forever on a failed initialization.
        self._stt_initialization_event.set()

    def _start_voice_capture(self):
        with self._voice_capture_lock:
            if not _voice_training_capture_enabled(self._current_settings()):
                self._voice_capture_buffer.clear()
                self._is_capturing_voice = False
                return
            self._voice_capture_buffer.clear()
            self._is_capturing_voice = True

    def _stop_voice_capture(self):
        with self._voice_capture_lock:
            self._is_capturing_voice = False

    def _save_captured_voice(self, transcript: str):
        if not _voice_training_capture_enabled(self._current_settings()):
            with self._voice_capture_lock:
                self._voice_capture_buffer.clear()
                self._is_capturing_voice = False
            return

        with self._voice_capture_lock:
            audio_data = bytes(self._voice_capture_buffer)
            self._voice_capture_buffer.clear()
            
        if not audio_data or not transcript.strip():
            return
            
        threading.Thread(
            target=self._save_captured_voice_thread,
            args=(audio_data, transcript),
            daemon=True,
            name="VoiceCaptureSaver",
        ).start()
        
    def _save_captured_voice_thread(self, audio_data: bytes, transcript: str):
        try:
            import wave
            import uuid
            import json
            import time
            from shared.voice_training_quality import analyze_pcm16_audio, voice_training_rejection_reasons
            from shared.voice_training_storage import get_voice_training_dir

            settings = self._current_settings()
            if not _voice_training_capture_enabled(settings):
                LOGGER.info("Skipped voice training capture because automatic capture is disabled")
                return

            normalized_transcript = str(transcript or "").strip()
            quality = analyze_pcm16_audio(audio_data, sample_rate=16000, channels=1)
            quality_warnings = voice_training_rejection_reasons(
                quality,
                normalized_transcript,
                min_duration_seconds=_env_float("AUTOYOU_VOICE_TRAINING_CAPTURE_MIN_SECONDS", 3.0),
                max_duration_seconds=_env_float("AUTOYOU_VOICE_TRAINING_CAPTURE_MAX_SECONDS", 20.0),
                min_speech_frame_ratio=_env_float("AUTOYOU_VOICE_TRAINING_CAPTURE_MIN_SPEECH_RATIO", 0.35),
                min_rms_dbfs=_env_float("AUTOYOU_VOICE_TRAINING_CAPTURE_MIN_RMS_DBFS", -35.0),
                min_transcript_chars=_env_int("AUTOYOU_VOICE_TRAINING_CAPTURE_MIN_TRANSCRIPT_CHARS", 20),
                max_transcript_chars=_env_int("AUTOYOU_VOICE_TRAINING_CAPTURE_MAX_TRANSCRIPT_CHARS", 280),
            )
            if quality_warnings:
                LOGGER.info(
                    "Saving voice training capture with quality warnings: reasons=%s duration=%.2fs rms=%.1fdB speech_ratio=%.2f transcript_chars=%d",
                    ",".join(quality_warnings),
                    quality.duration_seconds,
                    quality.rms_dbfs,
                    quality.speech_frame_ratio,
                    len(normalized_transcript),
                )
            
            vt_dir = get_voice_training_dir()
            recordings_dir = vt_dir / "recordings"
            recordings_dir.mkdir(parents=True, exist_ok=True)
            
            rec_id = str(uuid.uuid4())[:8]
            wav_filename = f"call_{rec_id}.wav"
            wav_path = recordings_dir / wav_filename
            
            wav_buffer = BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_data)
            write_secure_file(wav_path, wav_buffer.getvalue())
                
            entry = {
                "id": rec_id,
                "filename": wav_filename,
                "transcript": normalized_transcript,
                "timestamp": time.time(),
                "source": "voice_call",
                "quality": quality.to_dict(),
                "quality_warnings": list(quality_warnings),
                "training_eligible": not bool(quality_warnings),
            }

            metadata_file = vt_dir / "transcripts.json"
            metadata_file.parent.mkdir(parents=True, exist_ok=True)
            with _VOICE_TRAINING_TRANSCRIPTS_LOCK:
                transcripts = []
                if metadata_file.exists():
                    try:
                        loaded = load_secure_json(metadata_file, default=[])
                        transcripts = loaded if isinstance(loaded, list) else []
                    except SecureStorageError:
                        raise
                    except Exception:
                        transcripts = []
                transcripts.append(entry)
                save_secure_json(metadata_file, transcripts)
                
            LOGGER.info("Saved call recording call_%s.wav with transcript for voice training", rec_id)
        except Exception as exc:
            LOGGER.error("Failed to save captured voice training recording: %s", exc)

    def _init_stt(self, generation: int, settings: Dict[str, Any]):
        if self._closed or generation != self._stt_generation:
            return

        if os.environ.get("TEST_MODE") == "true" or "--tunnelmole" in sys.argv:
            LOGGER.info("TEST_MODE detected: using mock STT")

            class MockRecorder:
                def __init__(self):
                    self.returned = False

                def text(self):
                    if not self.returned:
                        time.sleep(2)
                        self.returned = True
                        return "AutoYou personal ai assistant"
                    time.sleep(10)
                    return ""

                def start(self):
                    return None

                def stop(self):
                    return None

                def shutdown(self):
                    return None

            self._set_recorder_ready(MockRecorder())
            self._stt_init_retry_count = 0
            self._transcription_thread = threading.Thread(
                target=self._transcription_loop,
                args=(self.recorder, generation),
                daemon=True,
                name=f"MockSTT-Loop-{generation}",
            )
            self._transcription_thread.start()
            self._emit_status("ready", "Voice pipeline ready.")
            return

        if AudioToTextRecorder is None:
            if _REALTIMESTT_IMPORT_ERROR is not None:
                LOGGER.warning("RealtimeSTT not available: %s", _REALTIMESTT_IMPORT_ERROR)
            else:
                LOGGER.warning("RealtimeSTT not available")

            # Fallback: try whisper.cpp binary-based STT
            try:
                from shared.whisper_downloader import (
                    WhisperCppRecorder,
                    get_whisper_cpp_binary,
                    download_whisper_model,
                )
                stt_settings = settings["stt"]
                model_name = stt_settings.get("model", "tiny.en")
                # Map faster_whisper model names to ggml names
                _model_map = {
                    "tiny": "tiny", "base": "base", "small": "small",
                    "medium": "medium", "large-v2": "large-v2",
                    "large-v3": "large-v3",
                }
                ggml_model = _model_map.get(model_name, "tiny.en")
                if not ggml_model.endswith(".en"):
                    lang = stt_settings.get("language", "en")
                    if lang == "en":
                        ggml_model = f"{ggml_model}.en"

                self._emit_status("warming", f"Downloading whisper.cpp ({ggml_model})…")
                binary = get_whisper_cpp_binary()
                if binary is None:
                    raise RuntimeError("whisper.cpp binary unavailable")
                model_path = download_whisper_model(ggml_model)
                if model_path is None:
                    raise RuntimeError(f"Could not download whisper model {ggml_model}")

                recorder = WhisperCppRecorder(
                    binary_path=binary,
                    model_path=model_path,
                    language=stt_settings.get("language", "en"),
                    post_speech_silence_duration=float(
                        stt_settings.get("post_speech_silence_duration", 1.5)
                    ),
                )
                recorder.start()
                if self._closed or generation != self._stt_generation:
                    recorder.shutdown()
                    return
                self._set_recorder_ready(recorder)
                self._stt_init_retry_count = 0
                LOGGER.info("WhisperCpp STT recorder initialised (model=%s)", ggml_model)
                self._emit_status("ready", "Voice pipeline ready (whisper.cpp).")
                self._transcription_thread = threading.Thread(
                    target=self._transcription_loop,
                    args=(self.recorder, generation),
                    daemon=True,
                    name=f"WhisperCpp-Loop-{generation}",
                )
                self._transcription_thread.start()
                return
            except Exception as _wexc:
                LOGGER.warning("whisper.cpp STT fallback failed: %s", _wexc)

            if self._closed or generation != self._stt_generation:
                return
            self._mark_stt_initialization_unavailable()
            self._emit_status("unavailable", "RealtimeSTT is unavailable on the server.")
            return

        LOGGER.info("Initializing RealtimeSTT in background")

        class HeadlessAudioToTextRecorder(AudioToTextRecorder):
            def _start_thread(self, target=None, args=()):
                if _is_realtimestt_transcription_target(target):
                    LOGGER.info("Headless mode: using safe RealtimeSTT transcription worker")
                    target = _safe_realtimestt_transcription_worker
                    if _should_use_threaded_realtimestt_worker():
                        LOGGER.info(
                            "Headless mode: running RealtimeSTT transcription worker in-process to avoid multiprocessing startup failures (platform=%s)",
                            sys.platform,
                        )
                        return _start_process_like_thread(
                            target,
                            tuple(args),
                            name="RealtimeSTT-TranscriptionWorker",
                        )
                if _is_realtimestt_audio_input_target(target):
                    LOGGER.info("Headless mode: blocking audio data worker start")
                    return _NoopProcessHandle()
                # Anything reaching here starts a real Process, which inherits a
                # snapshot of the parent's sys.path. Keep cv2's transient
                # bootstrap entry out of that snapshot so the child does not die
                # resolving stdlib `typing` to cv2/typing/__init__.py.
                _guard_child_sys_path()
                return super()._start_thread(target, args)

        stt_settings = settings["stt"]
        try:
            requested_device = stt_settings.get("device", "cpu")
            resolved_device = _resolve_stt_device(requested_device)
            _patch_tqdm_ensure_lock()
            stt_kwargs = {
                "spinner": False,
                "model": stt_settings["model"],
                "language": stt_settings["language"],
                "device": resolved_device,
                "compute_type": stt_settings["compute_type"],
                "enable_realtime_transcription": True,
                "realtime_model_type": _derive_realtime_model_type(stt_settings["model"]),
                "no_log_file": True,
                "silero_sensitivity": stt_settings["silero_sensitivity"],
                "post_speech_silence_duration": stt_settings["post_speech_silence_duration"],
                "on_recording_start": lambda *args, **kwargs: (
                    LOGGER.info("Voice detected, recording - auto-stopping TTS playback"),
                    self.stop_speaking(source="vad:on_recording_start"),
                    self._start_voice_capture(),
                ) and None,
                "on_recording_stop": lambda *args, **kwargs: (
                    LOGGER.info("Recording stopped, transcribing..."),
                    self._stop_voice_capture(),
                ) and None,
                "on_transcription_start": lambda *args, **kwargs: LOGGER.info("Transcription started..."),
                "input_device_index": None,
                "use_microphone": False,
            }
            LOGGER.info(
                "Initializing RealtimeSTT with model=%s language=%s device=%s compute_type=%s",
                stt_settings["model"],
                stt_settings["language"],
                resolved_device,
                stt_settings["compute_type"],
            )
            _ensure_torch_hub_repo_trusted("snakers4_silero-vad")
            used_local_vad_fallback = False
            try:
                recorder, used_local_vad_fallback = _instantiate_realtimestt_recorder(
                    HeadlessAudioToTextRecorder,
                    stt_kwargs,
                )
            except Exception as exc:
                needs_cpu_fallback = (
                    resolved_device != "cpu"
                    and any(
                        marker in str(exc).lower()
                        for marker in ("libcublas", "cuda", "cudnn", "ct2")
                    )
                )
                if not needs_cpu_fallback:
                    raise
                fallback_kwargs = dict(stt_kwargs)
                fallback_kwargs["device"] = "cpu"
                fallback_kwargs["compute_type"] = "float32"
                LOGGER.warning(
                    "RealtimeSTT CUDA initialization failed (%s); retrying on CPU",
                    exc,
                )
                recorder, used_local_vad_fallback = _instantiate_realtimestt_recorder(
                    HeadlessAudioToTextRecorder,
                    fallback_kwargs,
                )
            if self._closed or generation != self._stt_generation:
                self._shutdown_recorder(recorder)
                return
            self._set_recorder_ready(recorder)
            self._stt_init_retry_count = 0
            if used_local_vad_fallback:
                LOGGER.warning(
                    "RealtimeSTT started with a WebRTC-backed Silero fallback because the Silero model could not be loaded."
                )
            LOGGER.info("RealtimeSTT initialized successfully")
            self._emit_status("ready", "Voice pipeline ready.")
        except Exception as exc:
            if self._closed or generation != self._stt_generation:
                LOGGER.info("Ignoring stale RealtimeSTT initialization failure: %s", exc)
                return
            LOGGER.warning("RealtimeSTT initialization failed: %s", exc)
            if self._schedule_stt_init_retry(settings, generation, f"failed to start: {exc}"):
                return
            self._mark_stt_initialization_unavailable()
            self._emit_status("unavailable", f"Voice pipeline failed to start: {exc}")
            return

        self._transcription_thread = threading.Thread(
            target=self._transcription_loop,
            args=(self.recorder, generation),
            daemon=True,
            name=f"RealtimeSTT-Loop-{generation}",
        )
        self._transcription_thread.start()

    def _transcription_loop(self, recorder, generation: int):
        if not recorder:
            return

        while not self._closed and generation == self._stt_generation and self.recorder is recorder:
            try:
                text = recorder.text()
                normalized = _normalize_transcription_text(text)
                if not normalized:
                    continue
                if _should_ignore_transcription_text(normalized):
                    LOGGER.info("Ignoring placeholder STT transcript: %s", normalized)
                    continue
                LOGGER.info("Transcribed: %s", normalized)
                self._save_captured_voice(normalized)
                self.on_text_callback(normalized)
            except Exception as exc:
                if self._closed or generation != self._stt_generation:
                    break
                LOGGER.error("Transcription error: %s", exc)
                time.sleep(1)

    def process_audio_chunk(self, chunk: bytes):
        if self._closed or not self._stt_enabled:
            return
        try:
            if self.recorder is None and self._stt_initialization_event.is_set():
                # Initialization completed without a usable recorder. Do not
                # accumulate audio indefinitely in an unavailable pipeline.
                return
            if self.recorder is None and not self._warmup_audio_logged:
                LOGGER.info("Buffering inbound voice audio while STT initializes")
                self._warmup_audio_logged = True
            try:
                self.input_queue.put_nowait(chunk)
            except queue.Full:
                # Preserve the newest speech when initialization takes longer
                # than the bounded warm-up window.
                try:
                    self.input_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.input_queue.put_nowait(chunk)
                    self._warmup_audio_dropped_chunks += 1
                except queue.Full:
                    return
            with self._voice_capture_lock:
                if self._is_capturing_voice:
                    self._voice_capture_buffer.extend(chunk)
        except Exception as exc:
            LOGGER.error("Error queueing audio to STT: %s", exc)

    def flush_utterance(self, *, source: str = "unknown", timestamp_ms: Optional[int] = None) -> bool:
        """Finalize the currently accumulating utterance as one transcript.

        Used by client mute (deliberate end-of-speech) and by the WUIFT button
        (segment boundary press). Recording of the next utterance resumes
        automatically with the next voiced audio chunk.
        """
        if self._closed or not self.recorder:
            LOGGER.debug(
                "Skipping STT utterance flush from %s timestamp_ms=%s because recorder is unavailable",
                source,
                timestamp_ms,
            )
            return False
        try:
            pending_chunks = self.input_queue.qsize()
        except Exception:
            pending_chunks = -1
        try:
            self.input_queue.put_nowait(
                _UtteranceFlushRequest(source=source, timestamp_ms=timestamp_ms)
            )
            LOGGER.info(
                "Queued STT utterance flush from %s timestamp_ms=%s pending_audio_chunks=%s",
                source,
                timestamp_ms,
                pending_chunks,
            )
            return True
        except Exception as exc:
            LOGGER.warning(
                "Failed to queue STT utterance flush from %s timestamp_ms=%s: %s",
                source,
                timestamp_ms,
                exc,
            )
            return False

    def set_segmentation_hold(self, active: bool, *, source: str = "unknown") -> bool:
        """Engage/release the WUIFT segmentation hold for this session.

        While held, VAD silence never finalizes an utterance - speech spanning
        arbitrary pauses accumulates into one segment until flush_utterance()
        (or the WUIFT_MAX_SEGMENT_SECONDS safety cap) finalizes it.
        """
        active = bool(active)
        if self._closed:
            return False
        self._segmentation_hold = active
        if not active:
            self._held_recording_started_at = None
        recorder = self.recorder
        if not active and recorder is None:
            # Released while no recorder is attached (session churn): drop any
            # saved pre-hold duration so a later recorder re-captures its own
            # configured value instead of restoring a stale one.
            self._pre_hold_silence_duration = None
        if recorder is not None:
            try:
                self._apply_segmentation_hold_to_recorder(recorder)
            except Exception as exc:
                LOGGER.warning(
                    "Failed to %s WUIFT segmentation hold from %s: %s",
                    "engage" if active else "release",
                    source,
                    exc,
                )
                return False
        LOGGER.info(
            "WUIFT segmentation hold %s from %s (recorder=%s)",
            "engaged" if active else "released",
            source,
            "ready" if recorder is not None else "pending",
        )
        return True

    def _apply_segmentation_hold_to_recorder(self, recorder) -> None:
        hold = self._segmentation_hold
        native_hold = getattr(recorder, "set_segmentation_hold", None)
        if callable(native_hold):
            native_hold(hold)
            return
        # RealtimeSTT reads post_speech_silence_duration on every recording
        # loop pass, so a live attribute write takes effect immediately.
        if hold:
            if self._pre_hold_silence_duration is None:
                current = getattr(recorder, "post_speech_silence_duration", None)
                if isinstance(current, (int, float)):
                    self._pre_hold_silence_duration = float(current)
            recorder.post_speech_silence_duration = WUIFT_HOLD_SILENCE_SECONDS
        else:
            if self._pre_hold_silence_duration is not None:
                recorder.post_speech_silence_duration = self._pre_hold_silence_duration
                self._pre_hold_silence_duration = None

    def _enforce_held_segment_cap(self, recorder) -> None:
        """Auto-flush a held segment that exceeds WUIFT_MAX_SEGMENT_SECONDS.

        Runs on the feeder thread after each fed chunk; bounds the memory a
        client that engages the hold and never presses the button can consume.
        """
        if not self._segmentation_hold:
            self._held_recording_started_at = None
            return
        if not bool(getattr(recorder, "is_recording", False)):
            self._held_recording_started_at = None
            return
        now = time.monotonic()
        if self._held_recording_started_at is None:
            self._held_recording_started_at = now
            return
        if now - self._held_recording_started_at < WUIFT_MAX_SEGMENT_SECONDS:
            return
        self._held_recording_started_at = None
        LOGGER.info(
            "WUIFT safety cap reached (%.0fs); auto-flushing held segment",
            WUIFT_MAX_SEGMENT_SECONDS,
        )
        manual_flush = getattr(recorder, "request_flush", None)
        if callable(manual_flush):
            manual_flush()
        else:
            recorder.stop()

    def _feed_audio_loop(self):
        while True:
            try:
                chunk = self.input_queue.get()
                if chunk is None:
                    break
                recorder = self.recorder
                if recorder is None and not self._stt_initialization_event.is_set():
                    while not self._closed and not self._stt_initialization_event.wait(0.1):
                        pass
                    if self._closed:
                        break
                    recorder = self.recorder
                if recorder:
                    if isinstance(chunk, _UtteranceFlushRequest):
                        is_recording = bool(getattr(recorder, "is_recording", False))
                        manual_flush = getattr(recorder, "request_flush", None)
                        supports_manual_flush = callable(manual_flush)
                        LOGGER.info(
                            "Processing STT utterance flush from %s timestamp_ms=%s is_recording=%s supports_manual_flush=%s",
                            chunk.source,
                            chunk.timestamp_ms,
                            is_recording,
                            supports_manual_flush,
                        )
                        self._held_recording_started_at = None
                        if supports_manual_flush:
                            manual_flush()
                        elif is_recording:
                            recorder.stop()
                        continue
                    recorder.feed_audio(chunk)
                    self._enforce_held_segment_cap(recorder)
            except Exception as exc:
                if not self._closed:
                    LOGGER.error("Error feeding audio to STT: %s", exc)

    def stop_speaking(self, source: str = "unknown") -> None:
        """Cancel any in-progress TTS synthesis and clear queued audio immediately.

        Safe to call from any thread; used both by the client-sent stop_tts event
        and automatically by the VAD on_recording_start hook.
        """
        tts_generation_lock = getattr(self, "_tts_generation_lock", None)
        if tts_generation_lock is not None:
            with tts_generation_lock:
                self._tts_generation += 1
                active_generation = self._tts_generation
        else:
            self._tts_generation = getattr(self, "_tts_generation", 0) + 1
            active_generation = self._tts_generation
        LOGGER.info("stop_speaking requested by %s (tts_generation=%s)", source, active_generation)
        stop_tts_event = getattr(self, "_stop_tts_event", None)
        if stop_tts_event is not None:
            stop_tts_event.set()
        tts_track = getattr(self, "tts_track", None)
        if tts_track is None:
            return
        # The speech lane and the media lane are separate tracks whenever the
        # server registered both. When only one was registered they are the same
        # object, and clearing its queue here would cancel music that the user
        # explicitly started - VAD barge-in, background-audio suppression and the
        # client stop_tts event must only ever cancel spoken replies.
        if tts_track is self._get_playback_track() and self._track_is_rendering_media(tts_track):
            LOGGER.info(
                "Skipping stop_speaking from %s: the shared outbound track is rendering file playback",
                source,
            )
            return
        try:
            reason = (
                "Call sound stopped because the caller started speaking."
                if source == "vad:on_recording_start"
                else f"Playback stopped by {source}."
            )
            tts_track.stop_playback(reason)
        except TypeError:
            tts_track.stop_playback()

    @staticmethod
    def _track_is_rendering_media(track: Any) -> bool:
        checker = getattr(track, "is_rendering_media", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    @staticmethod
    def _track_is_rendering_speech(track: Any) -> bool:
        checker = getattr(track, "is_rendering_speech", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def play_audio_file(self, file_path: str, *, source: str = "audio_file") -> Dict[str, Any]:
        normalized_path = os.path.abspath(os.path.expanduser(str(file_path or "").strip()))
        if not normalized_path:
            raise ValueError("file_path is required")
        if not os.path.isfile(normalized_path):
            raise FileNotFoundError(normalized_path)
        playback_track = self._get_playback_track()
        if playback_track is None:
            raise RuntimeError("No outbound audio track is available")
        return playback_track.play_audio_file(normalized_path, source=source)

    def _media_control_track(self) -> Optional[TTSAudioStreamTrack]:
        """The track media controls may act on, or None when it holds speech.

        Mirrors the guard in :meth:`stop_speaking`: when one track serves both
        lanes, the media controls must not reach in and cancel a spoken reply.
        """
        playback_track = self._get_playback_track()
        if playback_track is None:
            return None
        if (
            playback_track is getattr(self, "tts_track", None)
            and self._track_is_rendering_speech(playback_track)
        ):
            return None
        return playback_track

    def pause_playback(self) -> bool:
        playback_track = self._media_control_track()
        if playback_track is None:
            return False
        return bool(playback_track.pause_playback())

    def resume_playback(self) -> bool:
        playback_track = self._media_control_track()
        if playback_track is None:
            return False
        return bool(playback_track.resume_playback())

    def stop_playback(self, source: str = "unknown") -> bool:
        playback_track = self._media_control_track()
        if playback_track is None:
            return False
        reason = "Playback stopped." if source == "unknown" else f"Playback stopped by {source}."
        try:
            playback_track.stop_playback(reason)
        except TypeError:
            playback_track.stop_playback()
        return True

    def get_playback_status(self) -> Dict[str, Any]:
        playback_track = self._get_playback_track()
        if playback_track is None:
            return {
                "event": "playback",
                "state": "unavailable",
                "source": "",
                "playback_id": "",
                "file_name": "",
                "position_ms": 0,
                "detail": "No outbound audio track is available.",
                "error": "",
                "timestamp_ms": int(time.time() * 1000),
                "buffered_frames": 0,
            }
        status = dict(playback_track.get_playback_status())
        status.pop("file_path", None)
        return status

    def speak(self, text: str, *, context: str = "") -> bool:
        settings = self._current_settings()
        self._setup_tts(settings)
        if settings["tts"]["provider"] == "off":
            LOGGER.debug("Skipping TTS because provider is off")
            return False
        if not self.tts_available:
            LOGGER.warning("Skipping TTS because provider %s is not ready", settings["tts"]["provider"])
            return False
        LOGGER.info("Speaking with provider=%s", settings["tts"]["provider"])
        with self._tts_generation_lock:
            self._tts_generation += 1
            generation = self._tts_generation
            self._stop_tts_event.clear()
        # Clear any previous reply still queued on the speech lane. When the
        # server only registered one outbound track the media lane shares it, so
        # leave a running song alone instead of cutting it off for a reply the
        # synthesizer has not even produced yet.
        if self.tts_track is not None and not (
            self.tts_track is self._get_playback_track()
            and self._track_is_rendering_media(self.tts_track)
        ):
            self.tts_track.stop_playback()
        threading.Thread(
            target=self._speak_thread,
            args=(text, settings, generation, context),
            daemon=True,
            name="TTS-Synthesizer",
        ).start()
        return True

    def synthesize_to_file(
        self,
        text: str,
        *,
        output_path: Optional[str] = None,
        context: str = "",
    ) -> str:
        """Synchronously synthesize text to an audio file and return its path.

        This uses the same provider-specific synthesis routines as ``speak()``
        but does not enqueue the result onto a WebRTC audio track. It is used
        for recorded voice-note replies that need to be sent as media.
        """
        settings = self._current_settings()
        self._setup_tts(settings)
        provider = settings["tts"]["provider"]
        if provider == "off":
            raise RuntimeError("TTS provider is off")
        if not self.tts_available:
            raise RuntimeError(f"TTS provider {provider} is not ready")

        tts_text = _sanitize_text_for_tts(text)
        if not tts_text:
            raise RuntimeError("Cannot synthesize empty speech text")

        temp_path = output_path
        if not temp_path:
            suffix = ".wav"
            if provider == "system" and sys.platform == "darwin":
                suffix = ".aiff"
            fd, temp_path = tempfile.mkstemp(prefix="autoyou_voicenote_", suffix=suffix)
            os.close(fd)
            try:
                os.remove(temp_path)
            except OSError:
                pass

        if provider == "system":
            self._synthesize_system_tts(tts_text, temp_path, settings)
        elif provider == CUSTOM_VOICE_PROVIDER:
            self._synthesize_custom_voice_tts(tts_text, temp_path, settings, fallback_to_system=False)
        elif provider == "openai":
            self._synthesize_openai_tts(tts_text, temp_path, settings)
        elif provider == "azure":
            self._synthesize_azure_tts(tts_text, temp_path, settings)
        elif provider == "emotivoice":
            synthesize_emotivoice(tts_text, temp_path, settings, context=context)
        else:
            raise RuntimeError(f"Unsupported TTS provider: {provider}")

        if not _wait_for_tts_output(temp_path):
            raise RuntimeError(f"TTS provider {provider} generated an empty audio file")
        return temp_path

    def _is_tts_generation_active(self, generation: int) -> bool:
        with self._tts_generation_lock:
            active_generation = self._tts_generation
        return (
            not self._closed
            and generation == active_generation
            and not self._stop_tts_event.is_set()
        )

    def _speak_thread(self, text: str, settings: Dict[str, Any], generation: int, context: str = ""):
        try:
            provider = settings["tts"]["provider"]
            temp_suffix = ".wav"
            if provider == "system" and sys.platform == "darwin":
                temp_suffix = ".aiff"

            fd, temp_path = tempfile.mkstemp(suffix=temp_suffix)
            os.close(fd)
            try:
                os.remove(temp_path)
            except OSError:
                pass

            # Abort immediately if a stop was requested before synthesis even started.
            if not self._is_tts_generation_active(generation):
                LOGGER.info("TTS synthesis aborted before start (generation=%s inactive)", generation)
                return

            tts_text = _sanitize_text_for_tts(text)
            if not tts_text:
                LOGGER.warning("Skipping TTS because sanitized speech text is empty")
                return

            if provider == "system":
                self._synthesize_system_tts(tts_text, temp_path, settings)
            elif provider == CUSTOM_VOICE_PROVIDER:
                self._synthesize_custom_voice_tts(tts_text, temp_path, settings, fallback_to_system=False)
            elif provider == "openai":
                self._synthesize_openai_tts(tts_text, temp_path, settings)
            elif provider == "azure":
                self._synthesize_azure_tts(tts_text, temp_path, settings)
            elif provider == "emotivoice":
                synthesize_emotivoice(tts_text, temp_path, settings, context=context or tts_text)
            else:
                raise RuntimeError(f"Unsupported TTS provider: {provider}")

            # Don't enqueue if a stop was requested while synthesis was running.
            if not self._is_tts_generation_active(generation):
                LOGGER.info(
                    "TTS synthesis completed but discarded (generation=%s inactive before enqueue)",
                    generation,
                )
                return

            if _wait_for_tts_output(temp_path):
                if self.tts_track:
                    self.tts_track.queue_audio_file(
                        temp_path,
                        should_abort=lambda: not self._is_tts_generation_active(generation),
                    )
                else:
                    LOGGER.warning("No TTS track available to send audio")
            else:
                LOGGER.error("TTS provider %s generated an empty or missing audio file", provider)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else str(exc)
            LOGGER.error("TTS subprocess failed: %s", stderr)
        except Exception as exc:
            LOGGER.error("TTS error: %s", exc)
        finally:
            try:
                if "temp_path" in locals() and os.path.exists(temp_path):
                    time.sleep(0.1)
                    os.remove(temp_path)
            except Exception as exc:
                LOGGER.warning("Could not remove temp TTS file %s: %s", temp_path, exc)

    def _synthesize_custom_voice_tts(
        self,
        text: str,
        temp_path: str,
        settings: Dict[str, Any],
        *,
        fallback_to_system: bool = True,
    ):
        try:
            LOGGER.info("Synthesizing custom voice using local VITS model")
            synthesize_custom_voice_to_file(text, temp_path)
            LOGGER.info("Custom voice synthesis completed successfully")
        except Exception as exc:
            if not fallback_to_system:
                raise
            LOGGER.error("Failed custom voice synthesis: %s. Falling back to system TTS.", exc)
            original_voice = settings["tts"]["system_voice"]
            settings["tts"]["system_voice"] = ""
            try:
                self._synthesize_system_tts(text, temp_path, settings)
            finally:
                settings["tts"]["system_voice"] = original_voice

    def _synthesize_system_tts(self, text: str, temp_path: str, settings: Dict[str, Any]):
        voice_id = settings["tts"]["system_voice"]
        if voice_id == CUSTOM_VOICE_SYSTEM_VOICE_ID:
            self._synthesize_custom_voice_tts(text, temp_path, settings)
            return
        rate_multiplier = float(settings["tts"]["rate"])
        speech_rate = max(50, min(300, int(SYSTEM_TTS_BASE_RATE * rate_multiplier)))
        if sys.platform == "darwin":
            # macOS: use the native `say` command in both source and compiled
            # runs. It is substantially more reliable here than pyttsx3.
            # Retry an installed English voice if the configured voice cannot
            # render speech. Never persist the fallback over the user's choice.
            for candidate in dict.fromkeys([voice_id, "Samantha", "Fred"]):
                cmd = ["say", "-o", temp_path, "-r", str(speech_rate)]
                if candidate and candidate != "default":
                    cmd.extend(["-v", candidate])
                cmd.extend(["--", text])
                try:
                    # Do not mistake output from an earlier attempt for success.
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    subprocess.run(
                        cmd,
                        check=True,
                        capture_output=True,
                        timeout=TTS_SUBPROCESS_TIMEOUT_SECONDS,
                    )
                    if _macos_tts_has_audio(temp_path):
                        return
                    LOGGER.warning("macOS voice %s returned no speech; trying another voice", candidate or "default")
                except Exception as _tts_err:
                    LOGGER.warning("macOS voice %s failed: %s", candidate or "default", _tts_err)
            raise RuntimeError("macOS system voices could not generate speech")

        if not _should_use_python_tts_subprocess():
            if pyttsx3 is None:
                raise RuntimeError("pyttsx3 is required for system TTS")
            engine = pyttsx3.init()
            try:
                engine.setProperty("rate", speech_rate)
                if voice_id:
                    try:
                        engine.setProperty("voice", voice_id)
                    except Exception as exc:
                        LOGGER.warning("Could not apply requested system voice %s: %s", voice_id, exc)
                engine.save_to_file(text, temp_path)
                engine.runAndWait()
            finally:
                try:
                    engine.stop()
                except Exception:
                    pass
            if _wait_for_tts_output(temp_path):
                return
            if sys.platform.startswith("win"):
                LOGGER.warning("pyttsx3 system TTS produced no usable audio file; retrying with Windows SAPI")
                self._synthesize_windows_system_tts(text, temp_path, voice_id, speech_rate)
                if _wait_for_tts_output(temp_path):
                    return
            raise RuntimeError("system TTS produced an empty audio file")

        script = r"""
import pyttsx3
import sys
import time

engine = pyttsx3.init()
engine.setProperty("rate", int(sys.argv[3]))
voice_id = sys.argv[4]
if voice_id:
    try:
        engine.setProperty("voice", voice_id)
    except Exception as exc:
        print(f"WARNING: could not apply requested voice: {exc}", file=sys.stderr)
engine.save_to_file(sys.argv[1], sys.argv[2])
engine.runAndWait()
try:
    engine.stop()
except Exception:
    pass
time.sleep(0.2)
"""
        subprocess.run(
            [sys.executable, "-c", script, text, temp_path, str(speech_rate), voice_id],
            check=True,
            capture_output=True,
            timeout=TTS_SUBPROCESS_TIMEOUT_SECONDS,
        )
        if _wait_for_tts_output(temp_path):
            return
        if sys.platform.startswith("win"):
            LOGGER.warning("pyttsx3 subprocess produced no usable audio file; retrying with Windows SAPI")
            self._synthesize_windows_system_tts(text, temp_path, voice_id, speech_rate)
            if _wait_for_tts_output(temp_path):
                return
        raise RuntimeError("system TTS produced an empty audio file")

    def _synthesize_windows_system_tts(
        self,
        text: str,
        temp_path: str,
        voice_id: str,
        speech_rate: int,
    ):
        env = os.environ.copy()
        env["AUTOYOU_TTS_TEXT"] = text
        env["AUTOYOU_TTS_PATH"] = temp_path
        env["AUTOYOU_TTS_VOICE"] = voice_id or ""
        env["AUTOYOU_TTS_RATE"] = str(_windows_sapi_rate(speech_rate))
        script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($env:AUTOYOU_TTS_VOICE -and $env:AUTOYOU_TTS_VOICE -ne 'default') {
        try {
            $synth.SelectVoice($env:AUTOYOU_TTS_VOICE)
        }
        catch {
            Write-Warning "Could not apply requested voice $($env:AUTOYOU_TTS_VOICE): $($_.Exception.Message)"
        }
    }
    $synth.Rate = [int]$env:AUTOYOU_TTS_RATE
    $synth.SetOutputToWaveFile($env:AUTOYOU_TTS_PATH)
    $synth.Speak($env:AUTOYOU_TTS_TEXT)
}
finally {
    if ($null -ne $synth) {
        $synth.Dispose()
    }
}
"""
        subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            check=True,
            capture_output=True,
            timeout=TTS_SUBPROCESS_TIMEOUT_SECONDS,
            text=True,
            env=env,
        )

    def _synthesize_openai_tts(self, text: str, temp_path: str, settings: Dict[str, Any]):
        if httpx is None:
            raise RuntimeError("httpx is required for OpenAI TTS")

        openai_cfg = settings["tts"]["openai"]
        api_key = openai_cfg["api_key"]
        if not api_key:
            raise RuntimeError("OpenAI API key is required for OpenAI TTS")

        truncated_text = (text or "").strip()
        if len(truncated_text) > 4000:
            LOGGER.warning("OpenAI TTS input exceeded 4000 chars; truncating")
            truncated_text = truncated_text[:4000]

        base_url = (openai_cfg["base_url"] or "https://api.openai.com/v1").rstrip("/")
        endpoint = f"{base_url}/audio/speech"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        rate = min(4.0, max(0.25, float(settings["tts"]["rate"])))
        voice_value: Any = openai_cfg["voice"]
        if isinstance(voice_value, str) and voice_value.startswith("voice_"):
            voice_value = {"id": voice_value}
        payload = {
            "model": openai_cfg["model"],
            "voice": voice_value,
            "input": truncated_text,
            "response_format": openai_cfg.get("response_format") or "wav",
            "speed": rate,
        }
        if openai_cfg.get("instructions"):
            payload["instructions"] = openai_cfg["instructions"]

        with httpx.Client(timeout=OPENAI_TTS_TIMEOUT_SECONDS) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            if response.status_code >= 400 and "response_format" in (response.text or ""):
                retry_payload = dict(payload)
                retry_payload.pop("response_format", None)
                retry_payload["format"] = openai_cfg.get("response_format") or "wav"
                response = client.post(endpoint, headers=headers, json=retry_payload)
            response.raise_for_status()
            with open(temp_path, "wb") as handle:
                handle.write(response.content)

    def _synthesize_azure_tts(self, text: str, temp_path: str, settings: Dict[str, Any]):
        if speechsdk is None:
            raise RuntimeError("azure-cognitiveservices-speech is required for Azure TTS")

        azure_cfg = settings["tts"]["azure"]
        if not azure_cfg["speech_key"] or not azure_cfg["speech_region"]:
            raise RuntimeError("Azure Speech key and region are required for Azure TTS")

        speech_config = speechsdk.SpeechConfig(
            subscription=azure_cfg["speech_key"],
            region=azure_cfg["speech_region"],
        )
        speech_config.speech_synthesis_voice_name = azure_cfg["voice"]
        if azure_cfg["endpoint_id"]:
            speech_config.endpoint_id = azure_cfg["endpoint_id"]
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff48Khz16BitMonoPcm
        )
        audio_config = speechsdk.audio.AudioOutputConfig(filename=temp_path)
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        rate_multiplier = float(settings["tts"]["rate"])
        if abs(rate_multiplier - 1.0) < 0.001:
            result = synthesizer.speak_text_async(text).get()
        else:
            rate_pct = int(round((rate_multiplier - 1.0) * 100))
            xml_lang = "-".join((azure_cfg["voice"] or "en-US").split("-")[:2]) or "en-US"
            safe_text = html.escape(text or "")
            ssml = (
                f"<speak version='1.0' xml:lang='{xml_lang}'>"
                f"<voice name='{html.escape(azure_cfg['voice'])}'>"
                f"<prosody rate='{rate_pct:+d}%'>{safe_text}</prosody>"
                f"</voice></speak>"
            )
            result = synthesizer.speak_ssml_async(ssml).get()

        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = ""
            if result.reason == speechsdk.ResultReason.Canceled:
                cancellation = speechsdk.SpeechSynthesisCancellationDetails(result)
                details = cancellation.error_details or cancellation.reason
            raise RuntimeError(f"Azure TTS failed: {details or result.reason}")
