# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-65c2c51ab2fcb2ad3e4b222c

"""The custom voice TTS provider: the owner's own voice, cloned with Chatterbox.

A cloned voice is a short reference recording chosen from the owner's cleanest
clips, plus metadata. It is not a trained model: Chatterbox (Resemble AI, MIT)
clones zero-shot from the reference. Chatterbox and its pinned dependencies live
in a separate Python environment (the voice-clone runtime), so this module never
imports them; it drives ``shared/voice_clone_worker.py`` as a subprocess.

A voice is usable only while all of these hold:

- the owner's consent record exists for the current consent statement;
- the reference recording exists;
- the watermark was detected in a test synthesis when the voice was built.

Every waveform Chatterbox generates carries Resemble's Perth watermark, and the
worker refuses to run without the watermarker.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import atexit
import collections
import hashlib
import importlib.util
import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from shared.secure_storage import (
    SecureStorageError,
    load_secure_json,
    materialize_secure_file,
    read_secure_file,
    save_secure_json,
)

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-65c2c51ab2fcb2ad3e4b222c"


LOGGER = logging.getLogger("autoyou.custom_voice_tts")

CUSTOM_VOICE_PROVIDER = "custom"
CUSTOM_VOICE_SYSTEM_VOICE_ID = "custom_voice"
CUSTOM_VOICE_DISPLAY_NAME = "My cloned voice"
CUSTOM_VOICE_METADATA_FILENAME = "custom_voice_metadata.json"
CUSTOM_VOICE_ACTIVE_MODEL_FILENAME = "active_custom_voice_model.json"

VOICE_CLONE_ENGINE = "chatterbox"
VOICE_CLONE_MODEL_REPO = "ResembleAI/chatterbox"
# Must match MODEL_REVISION in voice_clone_worker.py (a test checks this).
VOICE_CLONE_MODEL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
VOICE_CLONE_STRATEGY = "chatterbox_zero_shot_reference"
VOICE_CLONE_REFERENCE_FILENAME = "reference.wav"
VOICE_CLONE_PREVIEW_FILENAME = "preview.wav"
VOICE_CLONE_OUTPUTS_DIRNAME = "_voice_clone_outputs"
VOICE_CLONE_PYTHON_ENV = "AUTOYOU_VOICE_CLONE_PYTHON"
VOICE_CLONE_RUNTIME_DIRNAME = "voice_clone_runtime"
VOICE_CLONE_WORKER_FILENAME = "voice_clone_worker.py"
WATERMARK_METHOD = "perth_implicit"

VOICE_CLONE_CONSENT_FILENAME = "voice_clone_consent.json"
VOICE_CLONE_CONSENT_VERSION = "2026-10-23"
VOICE_CLONE_CONSENT_PHRASE = "I consent to cloning my own voice"
VOICE_CLONE_CONSENT_STATEMENT = (
    "I confirm that the recordings AutoYou uses to build this cloned voice are of my own voice, "
    "and I allow AutoYou to create a synthetic copy of my voice on this device. Speech made with "
    "it carries an inaudible watermark that marks it as AI-generated. I will not use it to "
    "impersonate anyone else or to mislead people about who is speaking. I can withdraw this "
    "consent at any time, which deletes the cloned voice."
)

_REQUIRED_CONSENT_KEYS = ("version", "accepted_at", "accepted_via")
# Neither group may contain "[", so a failed match never rescans the text after it.
_TTS_MARKDOWN_LINK_RE = re.compile(r"\[([^\[\]]+)\]\(([^)\[]+)\)")
_TTS_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s*")
_TTS_BULLET_RE = re.compile(r"(?m)^\s*[-*+]\s+")
_TTS_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_SECRET_ENV_RE = re.compile(r"(PASSWORD|PASSPHRASE|SECRET|TOKEN|API_KEY|PRIVATE_KEY|CREDENTIAL)", re.IGNORECASE)


class VoiceCloneEngineError(RuntimeError):
    """The voice-clone runtime is missing, failed, or refused a request."""


# ── Paths ──────────────────────────────────────────────────────────────────


def get_voice_training_dir() -> Path:
    from shared.voice_training_storage import get_voice_training_dir as _get_voice_training_dir

    return _get_voice_training_dir()


def get_custom_voice_model_dir() -> Path:
    return get_voice_training_dir() / "models" / "custom_voice"


def _custom_voice_models_dir() -> Path:
    return get_voice_training_dir() / "models"


def voice_clone_outputs_dir() -> Path:
    return _custom_voice_models_dir() / VOICE_CLONE_OUTPUTS_DIRNAME


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return str(left) == str(right)


def _read_metadata(model_dir: Path) -> Dict[str, Any]:
    metadata_path = model_dir / CUSTOM_VOICE_METADATA_FILENAME
    if not metadata_path.exists():
        return {}
    try:
        loaded = load_secure_json(metadata_path, default={})
        return loaded if isinstance(loaded, dict) else {}
    except SecureStorageError:
        raise
    except Exception as exc:
        LOGGER.debug("Failed to read custom voice metadata %s: %s", metadata_path, exc)
        return {}


# ── Consent ────────────────────────────────────────────────────────────────


def voice_clone_consent_path() -> Path:
    return get_voice_training_dir() / VOICE_CLONE_CONSENT_FILENAME


def voice_clone_consent_terms() -> Dict[str, str]:
    """The statement the owner accepts, and the phrase the chat agent asks them to type."""
    return {
        "version": VOICE_CLONE_CONSENT_VERSION,
        "statement": VOICE_CLONE_CONSENT_STATEMENT,
        "confirmation_phrase": VOICE_CLONE_CONSENT_PHRASE,
    }


def _statement_digest() -> str:
    return hashlib.sha256(VOICE_CLONE_CONSENT_STATEMENT.encode("utf-8")).hexdigest()


def _normalize_phrase(value: str) -> str:
    return " ".join(re.sub(r"[^a-z ]+", " ", str(value or "").lower()).split())


def load_voice_clone_consent() -> Optional[Dict[str, Any]]:
    """Return the owner's consent record if it covers the current statement."""
    path = voice_clone_consent_path()
    if not path.exists():
        return None
    try:
        record = load_secure_json(path, default=None)
    except SecureStorageError:
        raise
    except Exception as exc:
        LOGGER.warning("Voice clone consent record is unreadable: %s", exc)
        return None
    if not isinstance(record, dict) or any(not record.get(key) for key in _REQUIRED_CONSENT_KEYS):
        return None
    if record.get("version") != VOICE_CLONE_CONSENT_VERSION:
        return None
    if record.get("statement_sha256") != _statement_digest():
        return None
    return record


def record_voice_clone_consent(
    *,
    accepted_via: str,
    confirmation: str = "",
    statement_version: str = "",
) -> Dict[str, Any]:
    """Store the owner's consent to clone their own voice.

    The chat agent must pass the confirmation phrase the owner typed. The
    signed-in Voice Training website may instead pass the statement version
    the owner ticked. Anything else is refused.
    """
    via = str(accepted_via or "").strip().lower()
    phrase_ok = _normalize_phrase(confirmation) == _normalize_phrase(VOICE_CLONE_CONSENT_PHRASE)
    website_ok = via == "website" and str(statement_version or "").strip() == VOICE_CLONE_CONSENT_VERSION
    if not (phrase_ok or website_ok):
        raise ValueError(
            "Consent was not recorded. The owner must accept the current voice cloning statement "
            f'or type exactly: "{VOICE_CLONE_CONSENT_PHRASE}".'
        )
    record = {
        "consent_id": uuid.uuid4().hex[:16],
        "version": VOICE_CLONE_CONSENT_VERSION,
        "statement_sha256": _statement_digest(),
        "accepted_at": time.time(),
        "accepted_via": via or "unknown",
        "scope": "owner_voice_only",
    }
    path = voice_clone_consent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    save_secure_json(path, record)
    return record


def _is_clone_artifact_dir(model_dir: Path) -> bool:
    metadata = _read_metadata(model_dir)
    return metadata.get("engine") == VOICE_CLONE_ENGINE or (model_dir / VOICE_CLONE_REFERENCE_FILENAME).exists()


def revoke_voice_clone_consent() -> Dict[str, Any]:
    """Withdraw consent and delete every cloned voice built from it.

    The owner's original recordings stay; only what was derived from them for
    cloning (reference, preview, metadata) is removed.
    """
    shutdown_voice_clone_workers()
    removed: List[str] = []
    consent_path = voice_clone_consent_path()
    had_consent = consent_path.exists()
    if had_consent:
        consent_path.unlink()

    outputs_dir = voice_clone_outputs_dir()
    if outputs_dir.is_dir():
        for candidate in sorted(outputs_dir.iterdir()):
            if candidate.is_dir() and _is_clone_artifact_dir(candidate):
                shutil.rmtree(candidate, ignore_errors=True)
                removed.append(candidate.name)
    models_dir = _custom_voice_models_dir()
    if models_dir.is_dir():
        for candidate in sorted(models_dir.iterdir()):
            if not candidate.is_dir() or candidate.name == VOICE_CLONE_OUTPUTS_DIRNAME:
                continue
            if not _is_clone_artifact_dir(candidate):
                continue
            for name in (VOICE_CLONE_REFERENCE_FILENAME, VOICE_CLONE_PREVIEW_FILENAME, CUSTOM_VOICE_METADATA_FILENAME):
                target = candidate / name
                if target.exists():
                    target.unlink()
            removed.append(candidate.name)
    pointer = _active_custom_voice_pointer_path()
    if pointer.exists() and _read_active_pointer_target(pointer) is None:
        pointer.unlink()
    return {"revoked": had_consent, "removed_voices": removed}


# ── Readiness and status ───────────────────────────────────────────────────


def _artifact_problem(model_dir: Path, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Return why ``model_dir`` is not a usable cloned voice, or "" when it is."""
    if not model_dir.is_dir():
        return "not_built"
    meta = metadata if metadata is not None else _read_metadata(model_dir)
    if meta.get("engine") != VOICE_CLONE_ENGINE:
        if (model_dir / "config.json").exists():
            return "legacy_vits_artifact"
        return "not_built" if not meta else "unknown_engine"
    if not (model_dir / VOICE_CLONE_REFERENCE_FILENAME).is_file():
        return "reference_missing"
    watermark = meta.get("watermark") if isinstance(meta.get("watermark"), dict) else {}
    if watermark.get("verified") is not True:
        return "watermark_not_verified"
    if load_voice_clone_consent() is None:
        return "consent_missing"
    return ""


def _custom_voice_model_ready_at(model_dir: Path) -> bool:
    return not _artifact_problem(model_dir)


def _active_custom_voice_pointer_path() -> Path:
    return _custom_voice_models_dir() / CUSTOM_VOICE_ACTIVE_MODEL_FILENAME


def _read_active_pointer_target(pointer_path: Path) -> Optional[Path]:
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
        return candidate if candidate.is_dir() else None
    except SecureStorageError:
        raise
    except Exception as exc:
        LOGGER.debug("Failed to read active custom voice pointer %s: %s", pointer_path, exc)
        return None


def _read_active_custom_voice_model_dir() -> Optional[Path]:
    """The cloned voice the pointer names, ready or not, so status can say what is missing."""
    pointer_path = _active_custom_voice_pointer_path()
    if not pointer_path.exists():
        return None
    candidate = _read_active_pointer_target(pointer_path)
    if candidate is not None and _read_metadata(candidate).get("engine") == VOICE_CLONE_ENGINE:
        return candidate
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
    problem = _artifact_problem(resolved_dir)
    if problem:
        raise FileNotFoundError(f"Cloned voice is not ready at {resolved_dir}: {problem}")
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
    metadata = _read_metadata(resolved_dir)
    problem = _artifact_problem(resolved_dir, metadata)
    voice_id = _custom_voice_id_for_dir(resolved_dir, metadata)
    display_name = _custom_voice_display_name_for_dir(resolved_dir, metadata)
    reference = metadata.get("reference") if isinstance(metadata.get("reference"), dict) else {}
    watermark = metadata.get("watermark") if isinstance(metadata.get("watermark"), dict) else {}
    return {
        "ready": not problem,
        "not_ready_reason": problem,
        "provider": CUSTOM_VOICE_PROVIDER,
        "id": voice_id,
        "name": display_name,
        "system_voice_id": voice_id,
        "display_name": display_name,
        "model_dir": str(resolved_dir),
        "requested_model_dir": str(requested_dir),
        "active_model_pointer": str(_active_custom_voice_pointer_path()),
        "active_model_resolved": not _same_path(requested_dir, resolved_dir),
        "engine": str(metadata.get("engine") or ""),
        "base_model": metadata.get("model_repo") or VOICE_CLONE_MODEL_REPO,
        "metadata": metadata,
        "weight_files": [],
        "voice_cloned": metadata.get("engine") == VOICE_CLONE_ENGINE and not problem,
        "cloning_method": "zero_shot_reference" if metadata.get("engine") == VOICE_CLONE_ENGINE else "",
        "legacy_artifact": problem == "legacy_vits_artifact",
        "consent_recorded": load_voice_clone_consent() is not None,
        "reference_clip_count": len(reference.get("clips") or []),
        "reference_seconds": reference.get("seconds"),
        "watermark": {
            "method": watermark.get("method") or WATERMARK_METHOD,
            "verified": watermark.get("verified") is True,
            "preview_score": watermark.get("preview_score"),
        },
        "fine_tuning_applied": bool(metadata.get("fine_tuning_applied", False)),
        "training_strategy": str(metadata.get("training_strategy") or "").strip(),
    }


def list_custom_voice_statuses(*, include_unready: bool = False) -> List[Dict[str, Any]]:
    """Return installed custom voice artifacts discovered in the shared voice store."""
    models_dir = _custom_voice_models_dir()
    if not models_dir.is_dir():
        return []

    statuses: List[Dict[str, Any]] = []
    seen_model_dirs: set[str] = set()
    for model_dir in sorted(path for path in models_dir.iterdir() if path.is_dir()):
        if model_dir.name.startswith("_"):
            continue
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
    payload.setdefault("engine", VOICE_CLONE_ENGINE)
    payload.setdefault("model_repo", VOICE_CLONE_MODEL_REPO)
    payload.setdefault("model_revision", VOICE_CLONE_MODEL_REVISION)
    payload.setdefault("updated_at", time.time())
    metadata_path = model_dir / CUSTOM_VOICE_METADATA_FILENAME
    save_secure_json(metadata_path, payload)
    return metadata_path


# ── The voice-clone runtime ────────────────────────────────────────────────


def _repo_anchor() -> Path:
    return Path(__file__).resolve().parents[1] / "server.py"


def voice_clone_runtime_dir() -> Path:
    """Default home of the voice-clone runtime (see scripts/install_voice_clone_runtime.py)."""
    from shared.platform_runtime import get_mutable_data_dir

    return get_mutable_data_dir("AutoYou", anchor=_repo_anchor()) / VOICE_CLONE_RUNTIME_DIRNAME


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _running_compiled() -> bool:
    try:
        from shared.platform_runtime import is_compiled

        return bool(is_compiled())
    except Exception:
        return False


def resolve_voice_clone_python() -> Tuple[Optional[str], str]:
    """Pick the interpreter that runs Chatterbox, and say where it came from.

    Order: ``AUTOYOU_VOICE_CLONE_PYTHON``; the default runtime folder; this
    interpreter when Chatterbox happens to be importable here.
    """
    configured = os.environ.get(VOICE_CLONE_PYTHON_ENV, "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate), "environment"
        return None, f"{VOICE_CLONE_PYTHON_ENV} points to a file that does not exist"
    runtime_python = _venv_python(voice_clone_runtime_dir() / ".venv")
    if runtime_python.is_file():
        return str(runtime_python), "runtime"
    if not _running_compiled() and importlib.util.find_spec("chatterbox") is not None:
        return sys.executable, "current"
    return None, "not_installed"


def voice_clone_worker_script() -> Optional[Path]:
    candidate = Path(__file__).resolve().with_name(VOICE_CLONE_WORKER_FILENAME)
    return candidate if candidate.is_file() else None


def _install_hint() -> str:
    return (
        "Install the voice-clone runtime with `python scripts/install_voice_clone_runtime.py` "
        f"from the AutoYou-Server folder, or set {VOICE_CLONE_PYTHON_ENV} to a Python that has "
        "chatterbox-tts installed."
    )


def _worker_environment() -> Dict[str, str]:
    env: Dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith("AUTOYOU_") and not key.startswith("AUTOYOU_VOICE_CLONE_"):
            continue
        if _SECRET_ENV_RE.search(key) and key != "HF_TOKEN":
            continue
        env[key] = value
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    return env


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


_PROBE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PROBE_CACHE_SECONDS = 300.0


def voice_clone_engine_status(*, probe: bool = False, timeout: float = 180.0) -> Dict[str, Any]:
    """Describe the voice-clone runtime. ``probe`` runs it once to check imports and the GPU."""
    python, source = resolve_voice_clone_python()
    script = voice_clone_worker_script()
    status: Dict[str, Any] = {
        "engine": VOICE_CLONE_ENGINE,
        "model_repo": VOICE_CLONE_MODEL_REPO,
        "model_revision": VOICE_CLONE_MODEL_REVISION,
        "python": python or "",
        "source": source,
        "installed": bool(python and script),
        "available": False,
        "probed": False,
        "reason": "",
    }
    if not python:
        status["reason"] = f"The voice-clone runtime is not installed ({source}). {_install_hint()}"
        return status
    if script is None:
        status["reason"] = f"{VOICE_CLONE_WORKER_FILENAME} is missing from this AutoYou build."
        return status
    if not probe:
        status["available"] = None
        return status

    cached = _PROBE_CACHE.get(python)
    if cached and time.monotonic() - cached[0] < _PROBE_CACHE_SECONDS:
        result = cached[1]
    else:
        try:
            completed = subprocess.run(
                [python, "-I", str(script), "--probe"],
                capture_output=True,
                timeout=timeout,
                env=_worker_environment(),
                creationflags=_creationflags(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            status["reason"] = f"The voice-clone runtime did not answer: {exc}"
            return status
        lines = completed.stdout.decode("utf-8", errors="replace").strip().splitlines()
        try:
            result = json.loads(lines[-1]) if lines else {}
        except json.JSONDecodeError:
            result = {}
        if not isinstance(result, dict) or not result:
            tail = completed.stderr.decode("utf-8", errors="replace").strip()[-600:]
            status["reason"] = f"The voice-clone runtime failed its self-check. {tail}".strip()
            return status
        _PROBE_CACHE[python] = (time.monotonic(), result)

    status["probed"] = True
    for key in (
        "device",
        "accelerator",
        "model_cached",
        "chatterbox_version",
        "torch_version",
        "perth_version",
        "watermark_available",
        "missing",
    ):
        if key in result:
            status[key] = result[key]
    status["available"] = bool(result.get("available"))
    if result.get("model_revision") and result.get("model_revision") != VOICE_CLONE_MODEL_REVISION:
        status["available"] = False
        status["reason"] = "The voice-clone worker pins a different Chatterbox revision than this server."
    elif result.get("missing"):
        status["reason"] = "The voice-clone runtime is missing packages: " + "; ".join(result["missing"])
    elif result.get("watermark_available") is False:
        status["reason"] = "The Perth watermarker is not available, so voice cloning is disabled."
    return status


def _float_env(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.environ.get(name, "") or default))
    except ValueError:
        return default


class VoiceCloneWorker:
    """One long-lived voice-clone worker process, spoken to over JSON lines.

    Requests are serialized. The process exits after it has been idle for
    ``AUTOYOU_VOICE_CLONE_IDLE_SECONDS`` (default ten minutes) so the model
    does not hold GPU memory between conversations.
    """

    def __init__(self, python: str, script: Path, *, idle_seconds: Optional[float] = None) -> None:
        self.python = python
        self.script = Path(script)
        self.idle_seconds = (
            idle_seconds if idle_seconds is not None else _float_env("AUTOYOU_VOICE_CLONE_IDLE_SECONDS", 600.0, 30.0)
        )
        self._lock = threading.RLock()
        self._state = threading.Condition()
        self._process: Optional[subprocess.Popen] = None
        self._responses: Dict[int, Dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self._ready = False
        self._dead = True
        self._stderr_tail: Deque[str] = collections.deque(maxlen=60)
        self._last_used = time.monotonic()
        self.loaded: Optional[Dict[str, Any]] = None
        self.condition_key = ""

    # process lifecycle

    @property
    def alive(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None and not self._dead

    def _start(self) -> None:
        self._close_process()
        with self._state:
            self._responses.clear()
            self._ready = False
            self._dead = False
        self.loaded = None
        self.condition_key = ""
        self._stderr_tail.clear()
        try:
            process = subprocess.Popen(
                [self.python, "-I", "-u", str(self.script), "--serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.script.parent),
                env=_worker_environment(),
                creationflags=_creationflags(),
            )
        except OSError as exc:
            self._dead = True
            raise VoiceCloneEngineError(f"Could not start the voice-clone runtime: {exc}") from exc
        self._process = process
        threading.Thread(target=self._read_stdout, args=(process,), daemon=True, name="VoiceCloneStdout").start()
        threading.Thread(target=self._read_stderr, args=(process,), daemon=True, name="VoiceCloneStderr").start()
        deadline = time.monotonic() + _float_env("AUTOYOU_VOICE_CLONE_START_TIMEOUT_SECONDS", 120.0, 5.0)
        with self._state:
            while not self._ready and not self._dead:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._state.wait(remaining)
            ready = self._ready
        if not ready:
            self._close_process()
            raise VoiceCloneEngineError(f"The voice-clone runtime did not start. {self.stderr_tail()}".strip())
        threading.Thread(target=self._reap_when_idle, args=(process,), daemon=True, name="VoiceCloneIdle").start()

    def _read_stdout(self, process: subprocess.Popen) -> None:
        stream = process.stdout
        try:
            for raw in iter(stream.readline, b""):
                try:
                    message = json.loads(raw.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    self._stderr_tail.append(raw.decode("utf-8", errors="replace").rstrip())
                    continue
                if not isinstance(message, dict):
                    continue
                with self._state:
                    if message.get("event") == "ready":
                        self._ready = True
                    elif isinstance(message.get("id"), int):
                        self._responses[message["id"]] = message
                    self._state.notify_all()
        except Exception as exc:
            LOGGER.debug("Voice-clone stdout reader stopped: %s", exc)
        finally:
            with self._state:
                if self._process is process:
                    self._dead = True
                self._state.notify_all()

    def _read_stderr(self, process: subprocess.Popen) -> None:
        try:
            for raw in iter(process.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._stderr_tail.append(line)
                    LOGGER.debug("voice-clone worker: %s", line[:400])
        except Exception:
            pass

    def _reap_when_idle(self, process: subprocess.Popen) -> None:
        interval = max(5.0, min(30.0, self.idle_seconds / 4.0))
        while True:
            time.sleep(interval)
            if self._process is not process or process.poll() is not None:
                return
            if time.monotonic() - self._last_used < self.idle_seconds:
                continue
            if self._lock.acquire(blocking=False):
                try:
                    if self._process is process and time.monotonic() - self._last_used >= self.idle_seconds:
                        LOGGER.info("Stopping the idle voice-clone runtime.")
                        self.close()
                        return
                finally:
                    self._lock.release()

    def stderr_tail(self, lines: int = 12) -> str:
        return " | ".join(list(self._stderr_tail)[-lines:])

    def _close_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.poll() is None and process.stdin is not None:
                process.stdin.write(b'{"id": 0, "op": "shutdown"}\n')
                process.stdin.flush()
                process.wait(timeout=10)
        except Exception:
            pass
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=10)
            except Exception:
                pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            self._close_process()
            with self._state:
                self._dead = True
                self._state.notify_all()
            self.loaded = None
            self.condition_key = ""

    # requests

    def request(self, op: str, *, timeout: float, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            if not self.alive:
                self._start()
            process = self._process
            request_id = next(self._ids)
            payload = dict(fields)
            payload.update({"id": request_id, "op": op})
            try:
                process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
                process.stdin.flush()
            except Exception as exc:
                self.close()
                raise VoiceCloneEngineError(f"The voice-clone runtime stopped. {self.stderr_tail()}".strip()) from exc
            deadline = time.monotonic() + timeout
            with self._state:
                while request_id not in self._responses and not self._dead:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._state.wait(min(remaining, 5.0))
                response = self._responses.pop(request_id, None)
            self._last_used = time.monotonic()
            if response is None:
                tail = self.stderr_tail()
                timed_out = not self._dead
                self.close()
                if timed_out:
                    raise VoiceCloneEngineError(f"The voice-clone runtime timed out after {timeout:.0f}s on {op}.")
                raise VoiceCloneEngineError(f"The voice-clone runtime exited during {op}. {tail}".strip())
            if not response.get("ok"):
                raise VoiceCloneEngineError(str(response.get("error") or f"{op} failed"))
            result = response.get("result")
            return result if isinstance(result, dict) else {}

    def ensure_loaded(self, *, allow_download: bool, timeout: Optional[float] = None) -> Dict[str, Any]:
        with self._lock:
            if self.loaded is not None and self.alive:
                return self.loaded
            default_timeout = 3600.0 if allow_download else 900.0
            result = self.request(
                "load",
                allow_download=bool(allow_download),
                timeout=timeout or _float_env("AUTOYOU_VOICE_CLONE_LOAD_TIMEOUT_SECONDS", default_timeout, 30.0),
            )
            self.loaded = result
            return result

    def use_reference(self, reference_path: Path, *, key: str, exaggeration: float = 0.5) -> Dict[str, Any]:
        with self._lock:
            if self.condition_key == key and self.alive:
                return {"key": key, "reused": True}
            with materialize_secure_file(reference_path) as readable:
                result = self.request(
                    "condition",
                    reference=str(readable),
                    key=key,
                    exaggeration=float(exaggeration),
                    timeout=300.0,
                )
            self.condition_key = key
            return result

    def synthesize(self, texts: List[str], output_path: Path, *, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        opts = dict(options or {})
        characters = sum(len(text) for text in texts)
        timeout = _float_env("AUTOYOU_VOICE_CLONE_SYNTH_TIMEOUT_SECONDS", max(180.0, characters * 2.5), 10.0)
        return self.request(
            "synthesize",
            texts=list(texts),
            output=str(Path(output_path).resolve()),
            gap_seconds=float(opts.get("gap_seconds", 0.18)),
            exaggeration=float(opts.get("exaggeration", 0.5)),
            cfg_weight=float(opts.get("cfg_weight", 0.5)),
            temperature=float(opts.get("temperature", 0.8)),
            seed=opts.get("seed"),
            timeout=timeout,
        )

    def detect_watermark(self, audio_path: Path) -> Dict[str, Any]:
        with materialize_secure_file(audio_path) as readable:
            return self.request("detect_watermark", path=str(readable), timeout=300.0)


_WORKER_LOCK = threading.Lock()
_WORKER: Optional[VoiceCloneWorker] = None


def open_voice_clone_worker(*, idle_seconds: Optional[float] = None) -> VoiceCloneWorker:
    """A worker of its own, for a caller that closes it when done (voice preparation)."""
    python, source = resolve_voice_clone_python()
    script = voice_clone_worker_script()
    if not python:
        raise VoiceCloneEngineError(f"The voice-clone runtime is not installed ({source}). {_install_hint()}")
    if script is None:
        raise VoiceCloneEngineError(f"{VOICE_CLONE_WORKER_FILENAME} is missing from this AutoYou build.")
    return VoiceCloneWorker(python, script, idle_seconds=idle_seconds)


def get_voice_clone_worker() -> VoiceCloneWorker:
    """The shared worker this process uses for speech."""
    global _WORKER
    python, source = resolve_voice_clone_python()
    script = voice_clone_worker_script()
    with _WORKER_LOCK:
        if _WORKER is not None and (_WORKER.python != python or _WORKER.script != script):
            _WORKER.close()
            _WORKER = None
        if _WORKER is None:
            _WORKER = open_voice_clone_worker()
        return _WORKER


def shutdown_voice_clone_workers() -> None:
    global _WORKER
    with _WORKER_LOCK:
        worker, _WORKER = _WORKER, None
    if worker is not None:
        worker.close()


atexit.register(shutdown_voice_clone_workers)


def reference_key(reference_path: Path) -> str:
    """Identify a reference by content, so a rebuilt voice is reloaded."""
    return hashlib.sha256(read_secure_file(reference_path)).hexdigest()[:32]


def synthesis_options(metadata: Dict[str, Any]) -> Dict[str, Any]:
    stored = metadata.get("synthesis") if isinstance(metadata.get("synthesis"), dict) else {}
    return {
        "exaggeration": float(stored.get("exaggeration", 0.5)),
        "cfg_weight": float(stored.get("cfg_weight", 0.5)),
        "temperature": float(stored.get("temperature", 0.8)),
        "gap_seconds": 0.18,
    }


# ── Text preparation ───────────────────────────────────────────────────────


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
    cleaned = cleaned.replace("—", ", ").replace("–", ", ")
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
    cleaned = re.sub(r" ([,.!?;:])", r"\1", cleaned)
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


# ── Synthesis ──────────────────────────────────────────────────────────────


def synthesize_custom_voice_to_file(
    text: str,
    output_path: str | Path,
    *,
    model_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Speak ``text`` in the owner's cloned voice into a watermarked WAV file."""
    resolved_dir = resolve_custom_voice_model_dir(model_dir)
    metadata = _read_metadata(resolved_dir)
    problem = _artifact_problem(resolved_dir, metadata)
    if problem:
        raise FileNotFoundError(f"Cloned voice is not ready at {resolved_dir}: {problem}")
    chunks = _split_custom_voice_tts_text(text)
    # from __debug_provenance_g__ import annual
    if not chunks:
        raise ValueError("Custom voice synthesis text is empty after normalization.")

    reference = resolved_dir / VOICE_CLONE_REFERENCE_FILENAME
    options = synthesis_options(metadata)
    worker = get_voice_clone_worker()
    worker.ensure_loaded(allow_download=False)
    worker.use_reference(reference, key=reference_key(reference), exaggeration=options["exaggeration"])
    return worker.synthesize(chunks, Path(output_path), options=options)


__all__ = [
    "CUSTOM_VOICE_ACTIVE_MODEL_FILENAME",
    "CUSTOM_VOICE_DISPLAY_NAME",
    "CUSTOM_VOICE_METADATA_FILENAME",
    "CUSTOM_VOICE_PROVIDER",
    "CUSTOM_VOICE_SYSTEM_VOICE_ID",
    "VOICE_CLONE_CONSENT_PHRASE",
    "VOICE_CLONE_CONSENT_VERSION",
    "VOICE_CLONE_ENGINE",
    "VOICE_CLONE_MODEL_REPO",
    "VOICE_CLONE_MODEL_REVISION",
    "VOICE_CLONE_PREVIEW_FILENAME",
    "VOICE_CLONE_REFERENCE_FILENAME",
    "VOICE_CLONE_STRATEGY",
    "VoiceCloneEngineError",
    "VoiceCloneWorker",
    "WATERMARK_METHOD",
    "custom_voice_model_ready",
    "custom_voice_status",
    "get_custom_voice_model_dir",
    "get_voice_clone_worker",
    "get_voice_training_dir",
    "list_custom_voice_statuses",
    "load_voice_clone_consent",
    "open_voice_clone_worker",
    "record_voice_clone_consent",
    "reference_key",
    "resolve_custom_voice_model_dir",
    "resolve_voice_clone_python",
    "revoke_voice_clone_consent",
    "set_active_custom_voice_model_dir",
    "shutdown_voice_clone_workers",
    "synthesis_options",
    "synthesize_custom_voice_to_file",
    "voice_clone_consent_terms",
    "voice_clone_engine_status",
    "voice_clone_outputs_dir",
    "voice_clone_runtime_dir",
    "voice_clone_worker_script",
    "write_custom_voice_metadata",
    "_normalize_custom_voice_tts_text",
    "_split_custom_voice_tts_text",
]
