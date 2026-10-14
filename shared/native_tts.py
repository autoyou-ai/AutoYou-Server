# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Bundled native ONNX inference with verified, data-only runtime model installs.

The SDK and native libraries ship at build time. No pip, downloaded Python,
shell scripts, or executable plugins are run while installing a voice model.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any
import urllib.request
import uuid

from shared.platform_runtime import get_user_data_dir
from shared.speech_config import speech_language

LOGGER = logging.getLogger("autoyou.native_tts")
LANGUAGES = tuple("ar bg hr cs da nl en et fi fr de el hi hu id it ja ko lv lt pl pt ro ru sk sl es sv tr uk vi".split())
VOICES = tuple(f"{gender}{i}" for gender in "FM" for i in range(1, 6))
# Pin both the public repository revision and every file's SHA-256. Extend this
# curated pool only with models whose runtime, languages and terms are verified.
CATALOG = {
    "supertonic-3": {
        "label": "Supertonic 3",
        "repo": "supertone-oss-archive/supertonic-3",
        "revision": "aafc6e32416a594460b32413efc49d7fe4ce6d46",
        "languages": LANGUAGES,
        "license": "OpenRAIL-M (model); MIT (SDK)",
        "license_url": "https://huggingface.co/supertone-oss-archive/supertonic-3/blob/aafc6e32416a594460b32413efc49d7fe4ce6d46/LICENSE",
        "source_url": "https://github.com/supertone-oss-archive/supertonic",
        "files": {
            "LICENSE": (15007, "0d944a9110fed9a9602d60e0423a272903e7bd21ab060490774efc77c2275e9f"),
            "onnx/duration_predictor.onnx": (3700147, "c3eb91414d5ff8a7a239b7fe9e34e7e2bf8a8140d8375ffb14718b1c639325db"),
            "onnx/text_encoder.onnx": (36416150, "c7befd5ea8c3119769e8a6c1486c4edc6a3bc8365c67621c881bbb774b9902ff"),
            "onnx/tts.json": (8253, "42078d3aef1cd43ab43021f3c54f47d2d75ceb4e75f627f118890128b06a0d09"),
            "onnx/unicode_indexer.json": (277676, "9bf7346e43883a81f8645c81224f786d43c5b57f3641f6e7671a7d6c493cb24f"),
            "onnx/vector_estimator.onnx": (256534781, "883ac868ea0275ef0e991524dc64f16b3c0376efd7c320af6b53f5b780d7c61c"),
            "onnx/vocoder.onnx": (101424195, "085de76dd8e8d5836d6ca66826601f615939218f90e519f70ee8a36ed2a4c4ba"),
            "voice_styles/F1.json": (292046, "bbdec6ee00231c2c742ad05483df5334cab3b52fda3ba38e6a07059c4563dbc2"),
            "voice_styles/F2.json": (292423, "7c722c6a72707b1a77f035d67f0d1351ba187738e06f7683e8c72b1df3477fc6"),
            "voice_styles/F3.json": (290794, "12f6ef2573baa2defa1128069cb59f203e3ab67c92af77b42df8a0e3a2f7c6ab"),
            "voice_styles/F4.json": (291808, "c2fa764c1225a76dfc3e2c73e8aa4f70d9ee48793860eb34c295fff01c2e032b"),
            "voice_styles/F5.json": (291479, "45966e73316415626cf41a7d1c6f3b4c70dbc1ba2bee5c1978ef0ce33244fc8d"),
            "voice_styles/M1.json": (291748, "e35604687f5d23694b8e91593a93eec0e4eca6c0b02bb8ed69139ab2ea6b0a5b"),
            "voice_styles/M2.json": (292055, "b76cbf62bac707c710cf0ae5aba5e31eea1a6339a9734bfae33ab98499534a50"),
            "voice_styles/M3.json": (290198, "ea1ac35ccb91b0d7ecad533a2fbd0eec10c91513d8951e3b25fbba99954e159b"),
            "voice_styles/M4.json": (291522, "ca8eefad4fcd989c9379032ff3e50738adc547eeb5e221b82593a6d7b3bac303"),
            "voice_styles/M5.json": (291469, "dd22b92740314321f8ae11c5e87f8dd60d060f15dd3a632b5adf77f471f77af2"),
        },
    },
}
_STATE_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_ENGINES: dict[str, Any] = {}
_ENGINE_LOCK = threading.Lock()


def model_root() -> Path:
    # Test isolation wins even over an operator's explicit cache override.
    override = "" if os.getenv("AUTOYOU_TEST_ROOT") else os.getenv("AUTOYOU_TTS_MODEL_DIR", "")
    return Path(override).expanduser().resolve() if override else get_user_data_dir("AutoYou") / "tts_models"


def runtime_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("supertonic", "onnxruntime", "numpy", "soundfile"))


def _model(model: str) -> dict[str, Any]:
    if model not in CATALOG:
        raise ValueError(f"Unknown native TTS model: {model}")
    return CATALOG[model]


def model_installed(model: str = "supertonic-3") -> bool:
    spec = _model(model)
    root = model_root() / model
    try:
        if json.loads((root / "installed.json").read_text(encoding="utf-8")) != {"revision": spec["revision"], "files": {name: digest for name, (_, digest) in spec["files"].items()}}:
            return False
        return all((root / name).stat().st_size == size for name, (size, _) in spec["files"].items())
    except (OSError, ValueError):
        return False


def status() -> dict[str, Any]:
    return {
        "runtime_available": runtime_available(),
        "model_dir": str(model_root()),
        "voices": list(VOICES),
        "models": [{"model": name, "label": spec["label"], "languages": list(spec["languages"]), "license": spec["license"], "license_url": spec["license_url"], "source_url": spec["source_url"], "size_bytes": sum(size for size, _ in spec["files"].values()), "installed": model_installed(name)} for name, spec in CATALOG.items()],
    }


def _job(model: str) -> dict[str, Any]:
    key = str(model_root() / model)
    with _STATE_LOCK:
        if key not in _JOBS or _JOBS[key]["status"] in {"completed", "failed"}:
            _JOBS[key] = {"job_id": uuid.uuid4().hex, "model": model, "kind": "tts", "status": "queued", "message": "Preparing native TTS model", "error": "", "progress_percent": 0, "updated_at": time.time()}
        return dict(_JOBS[key])


def _progress(model: str, **updates: Any) -> None:
    key = str(model_root() / model)
    with _STATE_LOCK:
        _JOBS[key].update(updates, updated_at=time.time())


def download_jobs() -> list[dict[str, Any]]:
    root = str(model_root())
    with _STATE_LOCK:
        return sorted([dict(job) for key, job in _JOBS.items() if str(Path(key).parent) == root], key=lambda job: job["updated_at"], reverse=True)[:10]


def _hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def ensure_model(model: str = "supertonic-3", *, download: bool = True) -> Path:
    spec = _model(model)
    root = model_root() / model
    if model_installed(model):
        return root
    if not download:
        raise RuntimeError(f"Native TTS model {model} is missing; enable automatic downloads or install it in Speech settings")
    from filelock import FileLock

    root.mkdir(parents=True, exist_ok=True)
    _job(model)
    # Calls, voice notes and admin downloads share one installation, including
    # across server processes. An incomplete download is never marked ready.
    with FileLock(str(root / "install.lock"), timeout=900):
        if model_installed(model):
            _progress(model, status="completed", progress_percent=100, message="Native TTS model installed")
            return root
        _progress(model, status="running", message="Downloading native TTS model data")
        total = sum(size for size, _ in spec["files"].values())
        completed = 0
        try:
            for name, (expected_size, digest) in spec["files"].items():
                target = root / name
                if target.is_file() and target.stat().st_size == expected_size and _hash(target) == digest:
                    completed += expected_size
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                part = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
                try:
                    url = f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{name}"
                    request = urllib.request.Request(url, headers={"User-Agent": "AutoYou-native-tts/1"})
                    for attempt in range(3):
                        try:
                            hasher = hashlib.sha256()
                            written = 0
                            started = time.monotonic()
                            with urllib.request.urlopen(request, timeout=30) as response, part.open("wb") as output:
                                while chunk := response.read(1024 * 1024):
                                    written += len(chunk)
                                    if written > expected_size or time.monotonic() - started > 600:
                                        raise ValueError(f"TTS model file exceeds its download limit: {name}")
                                    output.write(chunk)
                                    hasher.update(chunk)
                                    _progress(model, message=f"Downloading {name}", progress_percent=round(100 * (completed + written) / total, 1))
                            if written != expected_size or hasher.hexdigest() != digest:
                                raise ValueError(f"TTS model checksum mismatch: {name}")
                            break
                        except (OSError, TimeoutError):
                            if attempt == 2:
                                raise
                    os.replace(part, target)
                finally:
                    part.unlink(missing_ok=True)
                completed += expected_size
            marker = {"revision": spec["revision"], "files": {name: digest for name, (_, digest) in spec["files"].items()}}
            marker_part = root / "installed.json.part"
            marker_part.write_text(json.dumps(marker), encoding="utf-8")
            os.replace(marker_part, root / "installed.json")
            _progress(model, status="completed", message="Native TTS model installed", progress_percent=100)
        except Exception as exc:
            _progress(model, status="failed", message="Native TTS model installation failed", error=str(exc))
            raise
    return root


def start_download_job(model: str) -> dict[str, Any]:
    _model(model)
    if not runtime_available():
        raise RuntimeError("Native TTS is missing from this runtime; install the current default server profile")
    job = _job(model)
    def run() -> None:
        try:
            ensure_model(model)
            _progress(model, status="completed", progress_percent=100, message="Native TTS model installed")
        except Exception:
            LOGGER.exception("Native TTS model installation failed")
    threading.Thread(target=run, name="AutoYou-TTS-Download", daemon=True).start()
    return job


def synthesize(text: str, output_path: str, settings: dict[str, Any], language: str = "") -> None:
    tts = settings["tts"]
    config = tts["native"]
    name = config["model"]
    spec = _model(name)
    language = speech_language(language or tts.get("language"))
    if language and language not in spec["languages"]:
        raise RuntimeError(f"Native TTS model {name} does not support language {language}; install a matching system voice or select another speech provider")
    root = ensure_model(name, download=config["auto_download"])
    # The SDK's session and random latent state are shared. Serialize inference
    # for callers from multiple calls/voice-note workers, with a bounded cache.
    with _ENGINE_LOCK:
        from supertonic import TTS

        key = str(root)
        if key not in _ENGINES:
            _ENGINES.clear()
            _ENGINES[key] = TTS(model=name, model_dir=root, auto_download=False, intra_op_num_threads=max(1, min(4, os.cpu_count() or 1)), inter_op_num_threads=1)
        engine = _ENGINES[key]
        waveform, _ = engine.synthesize(text, voice_style=engine.get_voice_style(config["voice"]), lang=language or "na", speed=min(2.0, max(0.7, float(tts["rate"]))), verbose=False)
        engine.save_audio(waveform, output_path)
