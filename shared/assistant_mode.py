# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Assistant mode: a wake word decides when speech is transcribed and answered.

A connected device that keeps its microphone streaming in the background
(Safety Recording, or the lighter "assistant listening" stream that saves
nothing) is listened to only for the owner's wake word. Until the word is
heard nothing is transcribed and no model is called; Safety Recording, when it
is on, keeps writing its files exactly as before. After the word, the next
utterance is transcribed locally and handed to the same path a voice call uses,
so the reply lands in that device's conversation.

The wake word is chosen by the owner in one of three ways, mirroring the
``confirm`` setting:

``voice``
    A few recordings of the word train a personal acoustic model
    (:mod:`shared.wake_word`). Nothing is transcribed until it matches.
``text``
    Only the typed phrase. Short bursts of speech are transcribed locally and
    thrown away unless they contain the phrase. Costs more CPU and transcribes
    speech that is then discarded, so the admin page recommends recording.
``both``
    The acoustic model triggers, and the transcript must also contain the
    phrase before anything reaches the model. The fewest false wakes.

Everything persistent lives under ``assistant_mode/`` in the mutable data
directory, which honours ``AUTOYOU_TEST_ROOT``; files are written through
:mod:`shared.secure_storage` like Voice Training recordings.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import base64
import json
import logging
import math
import os
import queue
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from shared import wake_word as ww
from shared.secure_storage import (
    SecureStorageError,
    load_secure_json,
    read_secure_file,
    save_secure_json,
    write_secure_file,
)

LOGGER = logging.getLogger(__name__)

ASSISTANT_MODE_DIR_ENV = "AUTOYOU_ASSISTANT_MODE_DIR"
SETTINGS_FILENAME = "settings.json"
MODEL_FILENAME = "wake_word_model.json"
SAMPLES_INDEX_FILENAME = "samples.json"
SAMPLES_DIRNAME = "samples"

CONFIRM_MODES = ("voice", "text", "both")
MAX_STORED_SAMPLES = 12
MAX_SAMPLE_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_PHRASE_CHARS = 48
MAX_NEGATIVE_CLIPS = 6
MAX_NEGATIVE_SECONDS = 8.0
MAX_MINED_TEMPLATES = 3

DEFAULT_SETTINGS: Dict[str, Any] = {
    # On by default. Nothing listens until a device or the Assistant website
    # is told to, and a voice wake word still needs recordings and training.
    "enabled": True,
    "wake_phrase": "",
    "confirm": "voice",
    "sensitivity": ww.DEFAULT_SENSITIVITY,
    "listen_seconds": 8,
    "listen_during_safety_recording": True,
    "use_voice_training_clips": True,
    # Calls normally answer everything said. With this on, a call (and its
    # video/audio recording) carries on as usual but AI answers only after the
    # wake word.
    "wake_word_in_calls": False,
    # During a video call the turn after the wake word also carries the
    # latest camera frame, so "what am I holding?" can be answered. The frame
    # is handled like a picture sent in chat.
    "use_live_video": True,
    # Permissions. Only an admin on this computer (localhost) changes settings;
    # these decide what a connected device may do on its own. A device on a
    # call may switch that call to wake-word answers (assistant_call_mode), and
    # may record and train the wake word from its microphone.
    "allow_call_toggle": True,
    "allow_device_teaching": True,
    # The Assistant website (assistant_agent). Auto start opens the microphone
    # and waits for the wake word as soon as the page loads (kiosks, thin and
    # headless clients). Replies are spoken with this server's voice, and for
    # a few seconds after a reply the next question needs no wake word.
    "web_auto_start": True,
    "web_speak_replies": True,
    "web_follow_up": True,
    "updated_at": 0.0,
}

#: Settings for the Assistant website only.
WEB_KEYS = ("web_auto_start", "web_speak_replies", "web_follow_up")

#: Settings that decide what devices may do. Shown on the Permissions screen too.
PERMISSION_KEYS = ("enabled", "allow_call_toggle", "allow_device_teaching", "wake_word_in_calls", "use_live_video")

# Listening/endpointing, in seconds of audio. Driven by audio time rather than
# the wall clock so behaviour is the same however fast frames arrive.
SPEECH_START_SECONDS = 0.06
END_SILENCE_SECONDS = 1.0
MAX_COMMAND_SECONDS = 20.0
TEXT_BURST_END_SILENCE_SECONDS = 0.6
TEXT_BURST_MAX_SECONDS = 6.0
ENROLL_BURST_END_SILENCE_SECONDS = 0.45
ENROLL_BURST_MAX_SECONDS = 3.0
ENROLL_TIMEOUT_SECONDS = 60.0
PRE_ROLL_MARGIN_SECONDS = 0.3
#: A second device hearing the same wake word within this window stays quiet.
CROSS_DEVICE_WAKE_WINDOW_SECONDS = 1.5
#: Live wakes that reach the server within this long of the first one are the
#: same word heard by several devices: the one that heard it best answers.
WAKE_DECISION_SECONDS = 0.3
#: After a spoken answer ends, the room gets this long before a wake counts.
SPEAKING_TAIL_SECONDS = 0.8
#: The longest one "speaking" report keeps the home quiet without renewal.
MAX_SPEAKING_SECONDS = 60.0
#: An Assistant page counts as listening this long after its last report.
PAGE_PRESENCE_SECONDS = 40.0
#: Bounds the arbiter's memory of pages and speakers; the oldest go first.
MAX_TRACKED_LISTENERS = 256
# A device that heard the word itself uploads it with what followed, seconds
# later. Its wake is dated back by the audio's length; this covers the word's
# own length and the upload's latency between that estimate and a live wake.
ESTIMATED_WAKE_SLACK_SECONDS = 1.5
# A small wake announcement precedes an on-device audio upload. Keep its
# decision independently of upload duration, retries and DataChannel chunking.
DEVICE_WAKE_RESERVATION_SECONDS = 120.0
#: Longest utterance a device may upload after its own detector woke.
MAX_DEVICE_UTTERANCE_SECONDS = 30.0
FEED_QUEUE_MAX_ITEMS = 1500
MIN_SPEECH_DBFS = -50.0
SPEECH_ABOVE_NOISE_DB = 10.0


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _repo_anchor() -> Path:
    return Path(__file__).resolve().parents[1] / "server.py"


_STORAGE_ROOT: Optional[Callable[[], Path]] = None


def set_storage_root(resolver: Optional[Callable[[], Path]]) -> None:
    """Let a host keep Assistant mode under its own data dir (AutoYou Lite does).

    The environment override still wins, so tests stay inside their root.
    """
    global _STORAGE_ROOT
    _STORAGE_ROOT = resolver


def assistant_mode_dir() -> Path:
    override = str(os.environ.get(ASSISTANT_MODE_DIR_ENV) or "").strip()
    if override:
        root = Path(override).expanduser()
    elif _STORAGE_ROOT is not None:
        root = Path(_STORAGE_ROOT()) / "assistant_mode"
    else:
        from shared.platform_runtime import get_mutable_data_dir

        root = get_mutable_data_dir("AutoYou", anchor=_repo_anchor()) / "assistant_mode"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_json(name: str, default: Any) -> Any:
    path = assistant_mode_dir() / name
    if not path.exists():
        return default
    try:
        return load_secure_json(path, default=default)
    except SecureStorageError:
        raise
    except Exception as exc:
        LOGGER.warning("Assistant mode could not read %s: %s", name, type(exc).__name__)
        return default


def _write_json(name: str, payload: Any) -> None:
    save_secure_json(assistant_mode_dir() / name, payload)


def normalize_settings(raw: Optional[Mapping[str, Any]], *, previous: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    base = dict(DEFAULT_SETTINGS)
    if isinstance(previous, Mapping):
        base.update({key: previous[key] for key in DEFAULT_SETTINGS if key in previous})
    source = raw if isinstance(raw, Mapping) else {}
    result = dict(base)
    if "enabled" in source:
        result["enabled"] = _as_bool(source.get("enabled"))
    if "wake_phrase" in source:
        result["wake_phrase"] = clean_phrase_text(source.get("wake_phrase"))
    if "confirm" in source:
        confirm = str(source.get("confirm") or "").strip().lower()
        result["confirm"] = confirm if confirm in CONFIRM_MODES else base["confirm"]
    if "sensitivity" in source:
        result["sensitivity"] = ww.normalize_sensitivity(source.get("sensitivity"))
    if "listen_seconds" in source:
        try:
            seconds = int(float(source.get("listen_seconds")))
        except (TypeError, ValueError):
            seconds = int(base["listen_seconds"])
        result["listen_seconds"] = max(3, min(30, seconds))
    for key in (
        "listen_during_safety_recording",
        "use_voice_training_clips",
        "wake_word_in_calls",
        "use_live_video",
        "allow_call_toggle",
        "allow_device_teaching",
        *WEB_KEYS,
    ):
        if key in source:
            result[key] = _as_bool(source.get(key))
    result["enabled"] = bool(result["enabled"])
    result["confirm"] = result["confirm"] if result["confirm"] in CONFIRM_MODES else "voice"
    result["sensitivity"] = ww.normalize_sensitivity(result["sensitivity"])
    result["wake_phrase"] = clean_phrase_text(result["wake_phrase"])
    return result


def clean_phrase_text(value: Any) -> str:
    """Keep the owner's spelling for display; strip controls and bound length."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:MAX_PHRASE_CHARS]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Dict[str, Any]:
    stored = _read_json(SETTINGS_FILENAME, {})
    return normalize_settings(stored if isinstance(stored, Mapping) else {})


def save_settings(changes: Mapping[str, Any]) -> Dict[str, Any]:
    current = load_settings()
    updated = normalize_settings(changes, previous=current)
    updated["updated_at"] = time.time()
    _write_json(SETTINGS_FILENAME, updated)
    model = load_model()
    if model is not None and abs(model.sensitivity - updated["sensitivity"]) > 1e-9:
        model.sensitivity = updated["sensitivity"]
        _write_json(MODEL_FILENAME, model.to_dict())
    return updated


def effective_phrase(settings: Mapping[str, Any], server_name: str) -> str:
    """What the owner says: the configured phrase, else the server's own name."""
    phrase = clean_phrase_text(settings.get("wake_phrase"))
    if phrase:
        return phrase
    name = clean_phrase_text(str(server_name or "").replace("-", " ").replace("_", " "))
    return name or "AutoYou"


def load_model() -> Optional[ww.WakeWordModel]:
    payload = _read_json(MODEL_FILENAME, None)
    if not isinstance(payload, Mapping):
        return None
    try:
        return ww.WakeWordModel.from_dict(payload)
    except ValueError as exc:
        LOGGER.warning("Ignoring an unusable wake word model: %s", exc)
        return None


def import_model(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Import bounded acoustic templates; never import executable code or credentials."""
    if not isinstance(payload, Mapping) or len(json.dumps(payload)) > 1024 * 1024:
        raise ValueError("Wake word model must be at most 1 MiB")
    templates = payload.get("templates")
    if not isinstance(templates, list) or not 1 <= len(templates) <= 8:
        raise ValueError("Wake word model needs 1 to 8 templates")
    def finite(value):
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False
    for key in ("base_threshold", "sensitivity", "listen_seconds", "created_at"):
        value = payload.get(key)
        if value is not None and not finite(value):
            raise ValueError("Invalid wake word settings")
    for item in templates:
        rows = item.get("mfcc") if isinstance(item, Mapping) else None
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise ValueError("Invalid wake word template length")
        if item.get("duration_seconds") is not None and not finite(item["duration_seconds"]):
            raise ValueError("Invalid wake word template duration")
        for row in rows:
            if not isinstance(row, list) or len(row) != 12 or not all(finite(v) for v in row):
                raise ValueError("Invalid wake word acoustic features")
    model = ww.WakeWordModel.from_dict(payload)
    phrase = clean_phrase_text(payload.get("wake_phrase") or model.phrase)
    if not phrase:
        raise ValueError("Wake phrase is required")
    model.phrase = phrase
    _write_json(MODEL_FILENAME, model.to_dict())
    save_settings({"wake_phrase": phrase})
    return model.to_dict()


def delete_model() -> None:
    path = assistant_mode_dir() / MODEL_FILENAME
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def list_samples() -> List[Dict[str, Any]]:
    index = _read_json(SAMPLES_INDEX_FILENAME, [])
    if not isinstance(index, list):
        return []
    samples_dir = assistant_mode_dir() / SAMPLES_DIRNAME
    return [
        dict(item)
        for item in index
        if isinstance(item, Mapping) and (samples_dir / f"{item.get('id')}.wav").is_file()
    ]


def _sample_id_ok(sample_id: str) -> bool:
    return bool(re.fullmatch(r"[a-f0-9]{8,32}", str(sample_id or "")))


def add_sample(pcm: bytes, *, source: str = "recording", phrase: str = "") -> Dict[str, Any]:
    """Store one recording of the wake word (mono 16 kHz s16 PCM)."""
    check = ww.check_sample(pcm)
    sample_id = uuid.uuid4().hex[:16]
    entry = {
        "id": sample_id,
        "source": str(source or "recording")[:32],
        "phrase": clean_phrase_text(phrase),
        "created_at": time.time(),
        **check.to_dict(),
    }
    if not check.ok:
        return {"stored": False, **entry}
    samples_dir = assistant_mode_dir() / SAMPLES_DIRNAME
    samples_dir.mkdir(parents=True, exist_ok=True)
    write_secure_file(samples_dir / f"{sample_id}.wav", ww.pcm16_to_wav_bytes(ww.float_to_pcm16(check.samples)))
    index = list_samples()
    index.append(entry)
    # Keep the newest recordings; the oldest are dropped with their audio.
    while len(index) > MAX_STORED_SAMPLES:
        dropped = index.pop(0)
        _remove_sample_file(str(dropped.get("id") or ""))
    _write_json(SAMPLES_INDEX_FILENAME, index)
    return {"stored": True, **entry}


def _remove_sample_file(sample_id: str) -> None:
    if not _sample_id_ok(sample_id):
        return
    try:
        (assistant_mode_dir() / SAMPLES_DIRNAME / f"{sample_id}.wav").unlink()
    except FileNotFoundError:
        pass


def delete_sample(sample_id: str) -> bool:
    if not _sample_id_ok(sample_id):
        return False
    index = list_samples()
    kept = [item for item in index if item.get("id") != sample_id]
    if len(kept) == len(index):
        return False
    _remove_sample_file(sample_id)
    _write_json(SAMPLES_INDEX_FILENAME, kept)
    return True


def clear_samples() -> int:
    index = list_samples()
    for item in index:
        _remove_sample_file(str(item.get("id") or ""))
    _write_json(SAMPLES_INDEX_FILENAME, [])
    return len(index)


def read_sample_pcm(sample_id: str) -> Optional[bytes]:
    if not _sample_id_ok(sample_id):
        return None
    path = assistant_mode_dir() / SAMPLES_DIRNAME / f"{sample_id}.wav"
    if not path.is_file():
        return None
    try:
        return ww.wav_bytes_to_pcm16(read_secure_file(path))
    except (ValueError, OSError, SecureStorageError) as exc:
        LOGGER.warning("Wake word sample %s is unreadable: %s", sample_id, type(exc).__name__)
        return None


# ---------------------------------------------------------------------------
# Voice Training library: negatives, and extra templates mined from clips
# ---------------------------------------------------------------------------


@dataclass
class TrainingClip:
    id: str
    transcript: str
    path: Path


def voice_training_clips(limit: int = 200) -> List[TrainingClip]:
    try:
        from shared.voice_training_storage import get_voice_training_dir

        root = get_voice_training_dir()
    except Exception as exc:
        LOGGER.debug("Voice Training library unavailable: %s", exc)
        return []
    index_path = root / "transcripts.json"
    if not index_path.exists():
        return []
    try:
        entries = load_secure_json(index_path, default=[])
    except Exception as exc:
        LOGGER.debug("Voice Training transcripts unreadable: %s", type(exc).__name__)
        return []
    clips: List[TrainingClip] = []
    for entry in reversed(entries if isinstance(entries, list) else []):
        if not isinstance(entry, Mapping):
            continue
        filename = str(entry.get("filename") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.wav", filename):
            continue
        path = root / "recordings" / filename
        if not path.is_file():
            continue
        clips.append(TrainingClip(str(entry.get("id") or filename), str(entry.get("transcript") or ""), path))
        if len(clips) >= limit:
            break
    return clips


def _clip_pcm(clip: TrainingClip, *, max_seconds: Optional[float] = None) -> Optional[bytes]:
    try:
        pcm = ww.wav_bytes_to_pcm16(read_secure_file(clip.path))
    except (ValueError, OSError, SecureStorageError):
        return None
    if max_seconds is not None:
        pcm = pcm[: int(max_seconds * ww.SAMPLE_RATE) * 2]
    return pcm


def voice_training_candidates(phrase: str, *, limit: int = 200) -> Dict[str, Any]:
    """Clips whose transcript contains the phrase, and how many do not."""
    matching: List[Dict[str, Any]] = []
    others = 0
    for clip in voice_training_clips(limit=limit):
        match = ww.match_phrase(phrase, clip.transcript)
        if match.matched:
            matching.append({"id": clip.id, "transcript": clip.transcript[:160], "score": round(match.score, 3)})
        else:
            others += 1
    return {"matching": matching, "other_clips": others}


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train(*, phrase: str, sensitivity: Optional[float] = None, use_voice_training: bool = True) -> Dict[str, Any]:
    """Build and save the wake word model from stored recordings.

    When ``use_voice_training`` is set, the Voice Training library supplies
    ordinary speech that does *not* contain the phrase (so the threshold rejects
    it), and clips whose transcript *does* contain it contribute extra
    templates cut from where the first model finds the word.
    """
    settings = load_settings()
    sensitivity = ww.normalize_sensitivity(settings["sensitivity"] if sensitivity is None else sensitivity)
    recordings: List[bytes] = []
    sources: List[str] = []
    for entry in list_samples():
        pcm = read_sample_pcm(str(entry.get("id") or ""))
        if pcm:
            recordings.append(pcm)
            sources.append(str(entry.get("source") or "recording"))
    negatives: List[bytes] = []
    positive_clips: List[TrainingClip] = []
    if use_voice_training:
        for clip in voice_training_clips():
            if ww.match_phrase(phrase, clip.transcript).matched:
                positive_clips.append(clip)
            elif len(negatives) < MAX_NEGATIVE_CLIPS:
                pcm = _clip_pcm(clip, max_seconds=MAX_NEGATIVE_SECONDS)
                if pcm:
                    negatives.append(pcm)
    model, report = ww.build_model(phrase, recordings, negatives=negatives, sensitivity=sensitivity, sources=sources)
    mined = 0
    if positive_clips:
        for clip in positive_clips:
            if mined >= MAX_MINED_TEMPLATES or len(model.templates) >= ww.MAX_TEMPLATES:
                break
            pcm = _clip_pcm(clip, max_seconds=20.0)
            if not pcm:
                continue
            found = ww.evaluate_clip(model, pcm)
            distance = found.get("distance")
            if distance is None or distance > model.distance_threshold:
                continue
            end = int(float(found["at_seconds"]) * ww.SAMPLE_RATE)
            length = int(max(t.duration_seconds for t in model.templates) * ww.SAMPLE_RATE)
            segment = ww.pcm16_to_float(pcm)[max(0, end - length) : end]
            check = ww.check_sample(segment)
            if not check.ok:
                continue
            model.templates.append(
                ww.WakeWordTemplate(
                    id=uuid.uuid4().hex[:12],
                    features=ww.mfcc(check.samples, normalize=True),
                    duration_seconds=check.duration_seconds,
                    source="voice_training",
                )
            )
            mined += 1
    report["voice_training_templates"] = mined
    report["voice_training_negatives"] = len(negatives)
    _write_json(MODEL_FILENAME, model.to_dict())
    return {"ok": True, "model": model.to_dict(include_templates=False), "report": report}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def public_status(server_name: str, *, include_samples: bool = False) -> Dict[str, Any]:
    settings = load_settings()
    model = load_model()
    phrase = effective_phrase(settings, server_name)
    model_phrase_matches = bool(model and ww.normalize_phrase(model.phrase) == ww.normalize_phrase(phrase))
    voice_ready = bool(model and model_phrase_matches)
    confirm = settings["confirm"]
    ready = bool(voice_ready if confirm in {"voice", "both"} else phrase)
    payload: Dict[str, Any] = {
        "enabled": bool(settings["enabled"]),
        "ready": ready,
        "assistant_name": clean_phrase_text(server_name) or "AutoYou",
        "wake_phrase": phrase,
        "wake_phrase_configured": bool(settings["wake_phrase"]),
        "confirm": confirm,
        "sensitivity": settings["sensitivity"],
        "listen_seconds": settings["listen_seconds"],
        "listen_during_safety_recording": settings["listen_during_safety_recording"],
        "use_voice_training_clips": settings["use_voice_training_clips"],
        "wake_word_in_calls": settings["wake_word_in_calls"],
        "use_live_video": settings["use_live_video"],
        "allow_call_toggle": settings["allow_call_toggle"],
        "allow_device_teaching": settings["allow_device_teaching"],
        "web_auto_start": settings["web_auto_start"],
        "web_speak_replies": settings["web_speak_replies"],
        "web_follow_up": settings["web_follow_up"],
        "model": None,
        "sample_count": len(list_samples()),
        "min_samples": ww.MIN_TEMPLATES,
    }
    if model is not None:
        payload["model"] = {
            "phrase": model.phrase,
            "matches_phrase": model_phrase_matches,
            "templates": len(model.templates),
            "distance_threshold": round(model.distance_threshold, 5),
            "created_at": model.created_at,
            "calibration": model.calibration,
        }
    if include_samples:
        payload["samples"] = list_samples()
    return payload


def device_model_payload(server_name: str) -> Optional[Dict[str, Any]]:
    """The trained model for a device's own detector, or None when there is none.

    Only the acoustic templates (MFCC frames) leave the server, never the
    recordings. Offered only while Assistant mode is on and the model matches
    the current wake phrase.
    """
    settings = load_settings()
    if not settings["enabled"] or settings["confirm"] == "text":
        return None
    model = load_model()
    phrase = effective_phrase(settings, server_name)
    if model is None or ww.normalize_phrase(model.phrase) != ww.normalize_phrase(phrase):
        return None
    model.sensitivity = settings["sensitivity"]
    payload = model.to_dict()
    payload["wake_phrase"] = phrase
    payload["confirm"] = settings["confirm"]
    payload["listen_seconds"] = settings["listen_seconds"]
    return payload


def decode_device_utterance(payload: Mapping[str, Any]) -> Optional[bytes]:
    """PCM16 16 kHz mono from a device's ``assistant_utterance`` control.

    Accepts ``audio_base64`` as WAV, or ``pcm16_base64`` raw little-endian
    samples at 16 kHz. Bounded to ``MAX_DEVICE_UTTERANCE_SECONDS``.
    """
    import base64
    import binascii

    try:
        if payload.get("audio_base64"):
            pcm = ww.wav_bytes_to_pcm16(base64.b64decode(str(payload.get("audio_base64")), validate=True))
        elif payload.get("pcm16_base64"):
            pcm = base64.b64decode(str(payload.get("pcm16_base64")), validate=True)
        else:
            return None
    except (binascii.Error, ValueError):
        return None
    pcm = pcm[: len(pcm) - (len(pcm) % 2)]
    return pcm[: int(MAX_DEVICE_UTTERANCE_SECONDS * ww.SAMPLE_RATE) * 2] or None


def model_revision(status: Mapping[str, Any]) -> str:
    """A short fingerprint of the model as a device would receive it; empty without one."""
    model = status.get("model") or {}
    if not status.get("enabled") or not model or not model.get("matches_phrase", False):
        return ""
    import hashlib

    basis = json.dumps([
        model.get("created_at", 0),
        ww.normalize_phrase(str(status.get("wake_phrase") or "")),
        round(float(status.get("sensitivity") or 0.0), 4),
        status.get("listen_seconds"),
        status.get("confirm"),
    ], sort_keys=True)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def capability_payload(server_name: str, *, available: bool) -> Dict[str, Any]:
    """What clients read from ``server_capabilities.assistant_mode``."""
    try:
        status = public_status(server_name)
    except Exception as exc:  # pragma: no cover - storage failure must not break capabilities
        LOGGER.warning("Assistant mode status unavailable: %s", type(exc).__name__)
        return {"enabled": False, "available": False}
    return {
        "enabled": bool(status["enabled"] and available),
        "available": bool(available),
        "ready": bool(status["ready"]),
        "assistant_name": status["assistant_name"],
        "wake_phrase": status["wake_phrase"],
        "confirm": status["confirm"],
        "listen_seconds": status["listen_seconds"],
        # A device may stream audio just for listening (nothing saved) by
        # sending background_audio_state.assistant_listening.
        "stream_listening": True,
        "listen_during_safety_recording": bool(status["listen_during_safety_recording"]),
        "wake_word_in_calls": bool(status["wake_word_in_calls"]),
        # A device on a call may turn Assistant mode on for that call only
        # (voice_call_control assistant_call_mode). With wake_word_in_calls on,
        # every call already answers only after the wake word.
        "call_toggle": bool(status["enabled"] and available and status["allow_call_toggle"]),
        "call_mode_forced": bool(status["wake_word_in_calls"]),
        "use_live_video": bool(status["use_live_video"]),
        # Devices announce their own wake with assistant_wake + wake_id before
        # assistant_utterance uploads. Neither control needs a microphone stream.
        # They fetch the detector model with assistant_model_request.
        "client_wake": True,
        # On-device detection: devices fetch the model (assistant_model_request),
        # upload only what was said after their own wake (assistant_utterance),
        # and can teach the word without streaming (assistant_sample).
        "client_detection": True,
        "device_samples": bool(status["allow_device_teaching"]),
        # Devices can teach the word over their stream (assistant_enroll).
        "enrollment": bool(status["allow_device_teaching"]),
        "device_teaching": bool(status["allow_device_teaching"]),
        "model_version": ww.MODEL_VERSION if status["model"] else 0,
        "model_created_at": (status["model"] or {}).get("created_at", 0),
        # Changes with retraining and with anything the device's detector uses
        # (word, sensitivity, listening time, confirmation), so devices refetch.
        "model_revision": model_revision(status),
    }


# ---------------------------------------------------------------------------
# Live listening
# ---------------------------------------------------------------------------


Emit = Callable[[str, Dict[str, Any]], None]
Submit = Callable[[str, str, Dict[str, Any]], None]
Transcribe = Callable[[bytes], str]


@dataclass
class _Config:
    enabled: bool
    phrase: str
    confirm: str
    listen_seconds: float
    listen_during_safety_recording: bool
    model: Optional[ww.WakeWordModel]
    wake_word_in_calls: bool = False
    allow_call_toggle: bool = True
    allow_device_teaching: bool = True

    @property
    def acoustic(self) -> bool:
        return self.model is not None and self.confirm in {"voice", "both"}

    @property
    def text_spotting(self) -> bool:
        # Only "Typed text only" transcribes speech to look for the word. The
        # voice choices promise nothing is transcribed before the recorded word
        # matches, so without a usable model they do not listen at all.
        return self.confirm == "text"

    @property
    def needs_training(self) -> bool:
        return self.enabled and self.confirm in {"voice", "both"} and self.model is None


class _Endpointer:
    """Energy-based speech start/end tracking on audio time."""

    def __init__(self) -> None:
        self.noise_floor = -60.0
        self.speech_seconds = 0.0
        self.silence_seconds = 0.0
        self.started = False

    def reset(self) -> None:
        self.speech_seconds = 0.0
        self.silence_seconds = 0.0
        self.started = False

    def observe(self, samples: np.ndarray) -> Tuple[bool, float]:
        seconds = samples.size / float(ww.SAMPLE_RATE)
        level = ww.rms_dbfs(samples)
        speech = level >= max(MIN_SPEECH_DBFS, self.noise_floor + SPEECH_ABOVE_NOISE_DB)
        if not speech:
            # Follow the room's background level slowly upward, quickly downward.
            weight = 0.3 if level < self.noise_floor else 0.02
            self.noise_floor = (1.0 - weight) * self.noise_floor + weight * level
            self.silence_seconds += seconds
            if not self.started:
                self.speech_seconds = 0.0
        else:
            self.silence_seconds = 0.0
            self.speech_seconds += seconds
            if self.speech_seconds >= SPEECH_START_SECONDS:
                self.started = True
        return speech, seconds


class _SessionAssistant:
    def __init__(self, hub: "AssistantModeHub", session_id: str) -> None:
        self.hub = hub
        self.session_id = session_id
        self.state = "idle"
        self.detector: Optional[ww.StreamingWakeWordDetector] = None
        self.detector_model: Optional[ww.WakeWordModel] = None
        self.endpointer = _Endpointer()
        self.buffer = bytearray()
        self.elapsed = 0.0
        self.state_started = 0.0
        self.wake_source = ""
        self.wake_score: Optional[float] = None
        self.text_check_pending = False
        # Bumped by every cancel: a transcription carries the value it started
        # with, and a result whose ticket is stale is dropped unread.
        self.ticket = 0
        self.spoken_at: Optional[float] = None
        self.device_wake: Optional[Tuple[str, bool, float]] = None
        self.device_wake_consumed = False
        self.after_training: Optional[Callable[[], None]] = None
        self.enroll_needed = 0
        self.enroll_captured = 0
        self.enroll_phrase = ""
        self.stats = {"wakes": 0, "commands": 0, "not_confirmed": 0, "no_speech": 0, "text_checks": 0}

    # -- helpers -------------------------------------------------------------

    def _ensure_detector(self, config: _Config) -> None:
        if not config.acoustic:
            self.detector = None
            self.detector_model = None
            return
        if self.detector is None or self.detector_model is not config.model:
            assert config.model is not None
            margin = PRE_ROLL_MARGIN_SECONDS + config.model.max_template_frames * ww.HOP_LENGTH / ww.SAMPLE_RATE
            self.detector = ww.StreamingWakeWordDetector(config.model, pre_roll_seconds=margin)
            self.detector_model = config.model

    def _set_state(self, state: str, reason: str = "", **extra: Any) -> None:
        self.state = state
        self.state_started = self.elapsed
        payload: Dict[str, Any] = {"event": "assistant_state", "state": state}
        if reason:
            payload["reason"] = reason
        payload.update(extra)
        self.hub._emit(self.session_id, payload)

    # -- entry points (worker thread) ---------------------------------------

    def feed(self, pcm: bytes, config: _Config) -> None:
        samples = ww.pcm16_to_float(pcm)
        if samples.size == 0:
            return
        speech, seconds = self.endpointer.observe(samples)
        self.elapsed += seconds
        if self.state == "enrolling":
            self._feed_enrollment(pcm, speech, config)
            return
        if self.state == "listening" and self.device_wake is None:
            self._feed_listening(pcm, speech, config)
            return
        # If capture moves back to a microphone stream, detect its next wake
        # normally instead of holding that stream behind an abandoned upload.
        if not config.enabled:
            return
        self._ensure_detector(config)
        if self.detector is not None:
            hit = self.detector.feed(pcm)
            if hit is not None:
                self.wake(config, source="voice", score=hit.score, pre_roll=hit.pre_roll)
            return
        if config.text_spotting:
            self._feed_text_spotting(pcm, speech, config)

    def wake(
        self,
        config: _Config,
        *,
        source: str,
        score: Optional[float] = None,
        pre_roll: bytes = b"",
        spoken_at: Optional[float] = None,
    ) -> None:
        claim = self.hub._contend_wake(self.session_id, spoken_at=spoken_at, score=score)
        if not claim:
            self._refused(claim)
            return
        self.ticket += 1
        self.text_check_pending = False
        self.device_wake = None
        self.stats["wakes"] += 1
        self.wake_source = source
        self.wake_score = score
        # Keep the word itself only when the transcript must confirm it.
        self.buffer = bytearray(pre_roll if config.confirm in {"both", "text"} and source == "voice" else b"")
        self.endpointer.reset()
        extra: Dict[str, Any] = {"wake_phrase": config.phrase, "listen_seconds": config.listen_seconds, "source": source}
        if score is not None:
            extra["score"] = round(float(score), 3)
        self._set_state("listening", "wake_word", **extra)

    def _feed_listening(self, pcm: bytes, speech: bool, config: _Config) -> None:
        self.buffer.extend(pcm)
        waited = self.elapsed - self.state_started
        if not self.endpointer.started:
            if waited >= config.listen_seconds:
                self.stats["no_speech"] += 1
                self.buffer = bytearray()
                self._reset_detector()
                self._set_state("idle", "no_speech")
            return
        too_long = len(self.buffer) / (2.0 * ww.SAMPLE_RATE) >= MAX_COMMAND_SECONDS
        if self.endpointer.silence_seconds >= END_SILENCE_SECONDS or too_long:
            audio = bytes(self.buffer)
            self.buffer = bytearray()
            self.endpointer.reset()
            self._reset_detector()
            self._set_state("thinking", "transcribing")
            self.hub._transcribe_async(self, audio, purpose="command", config=config)

    def _feed_text_spotting(self, pcm: bytes, speech: bool, config: _Config) -> None:
        if self.text_check_pending:
            return
        if speech or self.endpointer.started:
            self.buffer.extend(pcm)
        seconds = len(self.buffer) / (2.0 * ww.SAMPLE_RATE)
        if not self.endpointer.started:
            if not speech:
                self.buffer = bytearray()
            return
        if self.endpointer.silence_seconds >= TEXT_BURST_END_SILENCE_SECONDS or seconds >= TEXT_BURST_MAX_SECONDS:
            audio = bytes(self.buffer)
            self.buffer = bytearray()
            self.endpointer.reset()
            self.text_check_pending = True
            self.stats["text_checks"] += 1
            self.hub._transcribe_async(self, audio, purpose="text_spot", config=config)

    def _feed_enrollment(self, pcm: bytes, speech: bool, config: _Config) -> None:
        if self.elapsed - self.state_started >= ENROLL_TIMEOUT_SECONDS:
            self.buffer = bytearray()
            self.state = "idle"
            self.hub._emit(self.session_id, {
                "event": "assistant_enroll", "state": "failed", "reason": "timeout",
                "captured": self.enroll_captured, "needed": self.enroll_needed,
            })
            return
        if speech or self.endpointer.started:
            self.buffer.extend(pcm)
        if not self.endpointer.started:
            if not speech:
                self.buffer = bytearray()
            return
        seconds = len(self.buffer) / (2.0 * ww.SAMPLE_RATE)
        if self.endpointer.silence_seconds < ENROLL_BURST_END_SILENCE_SECONDS and seconds < ENROLL_BURST_MAX_SECONDS:
            return
        audio = bytes(self.buffer)
        self.buffer = bytearray()
        self.endpointer.reset()
        try:
            result = add_sample(audio, source="device", phrase=self.enroll_phrase)
        except Exception as exc:
            LOGGER.warning("Wake word sample could not be stored: %s", type(exc).__name__)
            result = {"stored": False, "problems": ["storage_error"]}
        if result.get("stored"):
            self.enroll_captured += 1
        self.hub._emit(self.session_id, {
            "event": "assistant_enroll",
            "state": "captured" if result.get("stored") else "rejected",
            "captured": self.enroll_captured,
            "needed": self.enroll_needed,
            "problems": list(result.get("problems") or []),
            "duration_seconds": result.get("duration_seconds"),
        })
        if self.enroll_captured >= self.enroll_needed:
            self.state = "idle"
            self.hub._train_async(self, phrase=self.enroll_phrase, after_training=self.after_training)
            self.after_training = None

    def reserve_device_wake(self, wake_id: str, config: _Config, score: Optional[float]) -> None:
        now = time.monotonic()
        if self.device_wake is not None and self.device_wake[0] == wake_id:
            return  # Retrying an announcement must not change its decision.
        claim = self.hub._contend_wake(self.session_id, score=score)
        accepted = claim.accepted
        self.device_wake = (wake_id, accepted, now + DEVICE_WAKE_RESERVATION_SECONDS)
        self.device_wake_consumed = False
        if not accepted:
            self.hub._emit(self.session_id, _refusal_event(claim))
            return
        self.ticket += 1
        self.text_check_pending = False
        self.buffer = bytearray()
        self.endpointer.reset()
        self.stats["wakes"] += 1
        self.wake_source = "device"
        self.wake_score = score
        self._set_state("listening", "wake_word", wake_id=wake_id)

    def device_utterance(self, pcm: bytes, config: _Config, score: Optional[float], wake_id: str = "") -> None:
        """A device heard the word itself and sent what was said; no stream needed."""
        # The upload arrives after the command ended: date the wake back by the
        # recording's length, which starts just before the word.
        if wake_id:
            reservation = self.device_wake
            if (reservation is None or reservation[0] != wake_id or self.device_wake_consumed
                    or time.monotonic() > reservation[2]):
                self.hub._emit(self.session_id, {"event": "assistant_state", "state": "idle", "reason": "expired_wake"})
                return
            if not reservation[1]:
                self.hub._emit(self.session_id, {"event": "assistant_state", "state": "idle", "reason": "answered_elsewhere"})
                return
            self.device_wake_consumed = True
        else:
            # Compatibility for older clients without wake announcements.
            spoken_at = time.monotonic() - len(pcm) / (2.0 * ww.SAMPLE_RATE)
            claim = self.hub._contend_wake(self.session_id, spoken_at=spoken_at, score=score)
            if not claim:
                self._refused(claim)
                return
            self.ticket += 1
            self.stats["wakes"] += 1
        self.wake_source = "device"
        self.wake_score = score
        self.buffer = bytearray()
        self.endpointer.reset()
        self._set_state("thinking", "transcribing", source="device")
        self.hub._transcribe_async(self, pcm, purpose="command", config=config)

    def start_enrollment(self, phrase: str, count: int, after_training: Optional[Callable[[], None]] = None) -> None:
        self.ticket += 1
        self.text_check_pending = False
        self.device_wake = None
        self.after_training = after_training
        self.enroll_phrase = phrase
        self.enroll_needed = max(ww.MIN_TEMPLATES, min(ww.MAX_TEMPLATES, int(count)))
        self.enroll_captured = 0
        self.buffer = bytearray()
        self.endpointer.reset()
        self.state = "enrolling"
        self.state_started = self.elapsed
        self.hub._emit(self.session_id, {
            "event": "assistant_enroll", "state": "recording", "captured": 0,
            "needed": self.enroll_needed, "wake_phrase": phrase,
        })

    def cancel(self, reason: str) -> None:
        was = self.state
        self.ticket += 1
        self.text_check_pending = False
        self.device_wake = None
        self.after_training = None
        self.buffer = bytearray()
        self.endpointer.reset()
        self.state = "idle"
        if was == "enrolling":
            self.hub._emit(self.session_id, {"event": "assistant_enroll", "state": "cancelled", "reason": reason})
        elif was in {"listening", "thinking"}:
            self._set_state("idle", reason)

    def _reset_detector(self) -> None:
        if self.detector is not None:
            self.detector.reset()

    def _refused(self, claim: "WakeClaim") -> None:
        self.state = "idle"
        self.state_started = self.elapsed
        self.hub._emit(self.session_id, _refusal_event(claim))

    def revoke(self, reason: str) -> None:
        """Another listener heard the same word better: give the wake back.

        Only happens within the short decision window, before a command could
        have ended. An announced device wake keeps its id but is now refused,
        so the upload that follows is turned away like any other duplicate.
        """
        if self.device_wake is not None and not self.device_wake_consumed:
            wake_id, _accepted, expires = self.device_wake
            self.device_wake = (wake_id, False, expires)
        elif self.state not in {"listening", "thinking"}:
            return
        self.ticket += 1
        self.text_check_pending = False
        self.buffer = bytearray()
        self.endpointer.reset()
        self._reset_detector()
        self.wake_score = None
        self.stats["wakes"] = max(0, self.stats["wakes"] - 1)
        self._set_state("idle", reason)

    # -- results (executor thread, re-entered on the worker) ------------------

    def on_transcript(self, purpose: str, text: str, config: _Config) -> None:
        text = str(text or "").strip()
        if purpose == "text_spot":
            self.text_check_pending = False
            if not config.enabled or self.state != "idle":
                return
            match = ww.match_phrase(config.phrase, text)
            if not match.matched:
                return  # Ordinary speech: dropped, never stored, never sent.
            if match.remainder:
                if not self.hub._contend_wake(self.session_id, spoken_at=self.spoken_at, score=match.score):
                    return
                self.stats["wakes"] += 1
                self.wake_source = "text"
                self._submit(match.remainder, config, phrase_score=match.score)
                return
            self.wake(config, source="text", score=match.score, spoken_at=self.spoken_at)
            return
        # purpose == "command": only while this command is still awaited.
        if not config.enabled or self.state != "thinking":
            return
        if not text:
            self._set_state("idle", "no_speech")
            return
        match = ww.match_phrase(config.phrase, text)
        # A device's upload starts with the word itself, like the server's own
        # pre-roll, so "both"/"text" confirm it the same way.
        must_confirm = config.confirm in {"both", "text"} and self.wake_source in {"voice", "device"}
        if must_confirm and not match.matched:
            self.stats["not_confirmed"] += 1
            self._set_state("idle", "not_confirmed")
            return
        command = match.remainder if match.matched else text
        if not command:
            self._set_state("idle", "no_command")
            return
        self._submit(command, config, phrase_score=match.score if match.matched else None)

    def _submit(self, command: str, config: _Config, *, phrase_score: Optional[float]) -> None:
        self.stats["commands"] += 1
        meta: Dict[str, Any] = {"source": "assistant_mode", "wake": self.wake_source}
        if self.wake_score is not None:
            meta["wake_score"] = round(float(self.wake_score), 3)
        if phrase_score is not None:
            meta["phrase_score"] = round(float(phrase_score), 3)
        self.wake_score = None
        try:
            self.hub._submit(self.session_id, command, meta)
        except Exception as exc:
            LOGGER.warning("Assistant mode could not hand over a command: %s", exc)
            self._set_state("idle", "submit_failed")
            return
        self._set_state("idle", "submitted")


#: Kinds of listener, for deciding which one answers.
LISTENER_PAGE = "page"  # The Assistant website, in any browser or the v2 Assistant section.
LISTENER_ADMIN = "admin"  # The Admin page's Chat & History listener.
LISTENER_DEVICE = "device"  # A paired device: its own detector, or its stream heard here.
LISTENER_CALL = "call"  # A call that answers only after the wake word.
LISTENER_KINDS = (LISTENER_PAGE, LISTENER_ADMIN, LISTENER_DEVICE, LISTENER_CALL)

#: The place every listener shares unless it is known to be somewhere else.
HOME = "home"


@dataclass(frozen=True)
class WakeListener:
    """Who claims a wake: which listener, on which device, and where it is.

    ``device`` groups listeners that share one microphone (this computer, or a
    paired device and the pages it opens); ``place`` groups listeners that can
    hear the same spoken word. A device away from home has a place of its own.
    """

    key: str
    kind: str = LISTENER_DEVICE
    device: str = ""
    place: str = HOME
    in_call: bool = False

    @property
    def device_key(self) -> str:
        return self.device or self.key


@dataclass(frozen=True)
class WakeClaim:
    """A wake decision. ``reason`` says why a claim was refused.

    A live claim is provisional until ``decided_at`` (monotonic): until then a
    device that heard the same word better can still take it. Listeners that
    cannot be told later (web pages) wait until then and ask :meth:`WakeArbiter.holds`.
    """

    accepted: bool
    reason: str
    round_id: int = 0
    decided_at: float = 0.0

    def __bool__(self) -> bool:
        return self.accepted


@dataclass
class _Candidate:
    listener: WakeListener
    score: Optional[float]
    on_revoke: Optional[Callable[[str], None]] = None


@dataclass
class _WakeRound:
    round_id: int
    place: str
    opened_at: float  # When the first claim reached the server.
    wake_at: float  # When the word was spoken.
    estimated: bool
    leader: _Candidate

    @property
    def decided_at(self) -> float:
        return self.opened_at if self.estimated else self.opened_at + WAKE_DECISION_SECONDS

    def covers(self, when: float, estimated: bool) -> bool:
        window = CROSS_DEVICE_WAKE_WINDOW_SECONDS
        if estimated or self.estimated:
            window += ESTIMATED_WAKE_SLACK_SECONDS
        return abs(when - self.wake_at) < window


def _beats(challenger: _Candidate, leader: _Candidate) -> bool:
    """Whether a later claim in the same round takes the wake from the leader.

    On one device the Assistant page wins. Between devices the clearer match
    wins, which is usually the nearest device; without both scores the first
    claim keeps it.
    """
    if challenger.listener.device_key == leader.listener.device_key:
        return challenger.listener.kind == LISTENER_PAGE and leader.listener.kind != LISTENER_PAGE
    if challenger.score is None or leader.score is None:
        return False
    return challenger.score > leader.score


class WakeArbiter:
    """One wake decision across every listener: streams, devices, pages and calls.

    The rules, in order:

    1. A call owns its device's microphone: while a device is in a call only
       the call may answer a wake there.
    2. One listener per device, and the Assistant page wins: while a page
       listens on a device, that device's other listeners stand down.
    3. One answer per home: listeners in the same place share each wake. The
       one that heard it best within ``WAKE_DECISION_SECONDS`` answers, and no
       wake counts there while an answer is being spoken.

    ``claim`` keeps the original yes/no form for callers that only need that.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rounds: Dict[str, _WakeRound] = {}
        self._decided: Dict[int, str] = {}
        self._next_round = 0
        self._pages: Dict[str, Tuple[str, float]] = {}
        self._speakers: Dict[str, Tuple[str, float]] = {}

    @staticmethod
    def _listener(listener: Any) -> WakeListener:
        if isinstance(listener, WakeListener):
            return listener
        return WakeListener(key=str(listener or ""))

    def claim(self, listener: Any, *, spoken_at: Optional[float] = None, score: Optional[float] = None) -> bool:
        return self.contend(listener, spoken_at=spoken_at, score=score).accepted

    def contend(
        self,
        listener: Any,
        *,
        score: Optional[float] = None,
        spoken_at: Optional[float] = None,
        on_revoke: Optional[Callable[[str], None]] = None,
    ) -> WakeClaim:
        """Decide one wake. ``spoken_at`` dates a wake reported after the fact.

        ``on_revoke`` is called (outside the lock) if a device that heard the
        same word better takes the wake back during the decision window.
        """
        who = self._listener(listener)
        now = time.monotonic()
        estimated = spoken_at is not None
        when = float(spoken_at) if estimated else now
        candidate = _Candidate(who, score if score is None else float(score), on_revoke)
        revoked: Optional[_Candidate] = None
        with self._lock:
            self._forget_expired(now)
            refused = self._standing_rule(who, now)
            if refused:
                return WakeClaim(False, refused, decided_at=now)
            current = self._rounds.get(who.place)
            if current is not None and current.covers(when, estimated):
                if current.leader.listener.key == who.key:
                    # Its own wake again (a retry or the next question): a new
                    # round from this wake, so others can still contest it.
                    current = None
                elif estimated or current.estimated or now >= current.decided_at:
                    return WakeClaim(False, "answered_elsewhere", current.round_id, now)
                elif _beats(candidate, current.leader):
                    revoked = current.leader
                    current.leader = candidate
                    result = WakeClaim(True, "wake_word", current.round_id, current.decided_at)
                else:
                    return WakeClaim(False, "answered_elsewhere", current.round_id, now)
            else:
                current = None
            if current is None:
                self._next_round += 1
                current = _WakeRound(self._next_round, who.place, now, when, estimated, candidate)
                self._rounds[who.place] = current
                result = WakeClaim(True, "wake_word", current.round_id, current.decided_at)
            self._decided[current.round_id] = current.leader.listener.key
            while len(self._decided) > MAX_TRACKED_LISTENERS:
                self._decided.pop(next(iter(self._decided)))
        if revoked is not None and revoked.on_revoke is not None:
            try:
                revoked.on_revoke("answered_elsewhere")
            except Exception as exc:  # pragma: no cover - a listener must not break the decision
                LOGGER.debug("Assistant wake revocation was not delivered: %s", exc)
        return result

    def holds(self, listener: Any, round_id: int) -> bool:
        """Whether this listener still has the wake from round ``round_id``."""
        with self._lock:
            return bool(round_id) and self._decided.get(int(round_id)) == self._listener(listener).key

    async def settle(self, claim: WakeClaim, listener: Any) -> WakeClaim:
        """Wait out the decision window, then return the final decision."""
        import asyncio

        if not claim.accepted:
            return claim
        wait = claim.decided_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(min(wait, WAKE_DECISION_SECONDS))
        if self.holds(listener, claim.round_id):
            return claim
        return WakeClaim(False, "answered_elsewhere", claim.round_id, claim.decided_at)

    # -- what listeners report --------------------------------------------------

    def page_listening(self, listener: Any, listening: bool, *, seconds: float = PAGE_PRESENCE_SECONDS) -> None:
        """An Assistant page says it is (or stopped) listening on its device."""
        who = self._listener(listener)
        now = time.monotonic()
        with self._lock:
            self._pages.pop(who.key, None)
            if listening:
                self._pages[who.key] = (who.device_key, now + max(1.0, min(PAGE_PRESENCE_SECONDS, float(seconds))))
                while len(self._pages) > MAX_TRACKED_LISTENERS:
                    self._pages.pop(next(iter(self._pages)))

    def speaking(self, listener: Any, seconds: float) -> None:
        """A listener speaks an answer for about ``seconds``; 0 means it finished."""
        who = self._listener(listener)
        now = time.monotonic()
        try:
            length = max(0.0, min(MAX_SPEAKING_SECONDS, float(seconds)))
        except (TypeError, ValueError):
            length = 0.0
        with self._lock:
            previous = self._speakers.pop(who.key, None)
            if length > 0:
                self._speakers[who.key] = (who.place, now + length + SPEAKING_TAIL_SECONDS)
            elif previous is not None and previous[1] > now:
                # Finished: the room still gets the short tail.
                self._speakers[who.key] = (who.place, min(previous[1], now + SPEAKING_TAIL_SECONDS))
            while len(self._speakers) > MAX_TRACKED_LISTENERS:
                self._speakers.pop(next(iter(self._speakers)))

    def page_listening_on(self, device: str, *, exclude: str = "") -> bool:
        with self._lock:
            return self._page_on(str(device or ""), time.monotonic(), exclude=exclude)

    def snapshot(self) -> Dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            self._forget_expired(now)
            return {
                "pages_listening": len(self._pages),
                "speaking": sorted({place for place, _until in self._speakers.values()}),
            }

    # -- internals -----------------------------------------------------------

    def _standing_rule(self, who: WakeListener, now: float) -> str:
        if who.in_call and who.kind != LISTENER_CALL:
            return "in_call"
        if who.kind not in {LISTENER_PAGE, LISTENER_CALL} and self._page_on(who.device_key, now, exclude=who.key):
            return "assistant_page"
        if any(place == who.place and until > now for key, (place, until) in self._speakers.items() if key != who.key):
            return "speaking"
        return ""

    def _page_on(self, device: str, now: float, *, exclude: str = "") -> bool:
        return any(
            key != exclude and page_device == device and until > now
            for key, (page_device, until) in self._pages.items()
        )

    def _forget_expired(self, now: float) -> None:
        # A device may upload up to MAX_DEVICE_UTTERANCE_SECONDS after its own
        # wake, dated back by the audio's length: keep each place's latest
        # round at least that long so the late copy still matches it.
        horizon = MAX_DEVICE_UTTERANCE_SECONDS + CROSS_DEVICE_WAKE_WINDOW_SECONDS + ESTIMATED_WAKE_SLACK_SECONDS + 5.0
        for place in [p for p, item in self._rounds.items() if now - item.opened_at > horizon]:
            self._rounds.pop(place, None)
        while len(self._rounds) > MAX_TRACKED_LISTENERS:
            self._rounds.pop(next(iter(self._rounds)))
        for table in (self._pages, self._speakers):
            for key in [k for k, (_v, until) in table.items() if until <= now]:
                table.pop(key, None)


class AssistantModeHub:
    """Owns every device's assistant listener. Thread-safe entry points.

    ``feed`` is called from audio threads and never blocks or raises: frames go
    onto one bounded queue processed by a single worker thread, so detection for
    any number of connected devices costs one thread. Transcription runs on a
    separate one-thread executor so a slow model never stalls detection.
    """

    def __init__(
        self,
        *,
        emit: Emit,
        submit: Submit,
        transcribe: Transcribe,
        server_name: Callable[[], str] = lambda: "AutoYou",
        describe_listener: Optional[Callable[[str], WakeListener]] = None,
    ) -> None:
        self._emit_cb = emit
        self._submit_cb = submit
        self._transcribe_cb = transcribe
        self._server_name = server_name
        # Which device a session is, where it is, and whether it is in a call.
        # Without one every session is its own device at home.
        self._describe_listener = describe_listener
        self._queue: "queue.Queue[Tuple[str, str, Any]]" = queue.Queue(maxsize=FEED_QUEUE_MAX_ITEMS)
        self._sessions: Dict[str, _SessionAssistant] = {}
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._config: Optional[_Config] = None
        self._config_lock = threading.Lock()
        self.wake_arbiter = WakeArbiter()
        # Calls a device switched to wake-word answers (assistant_call_mode).
        self._call_mode_sessions: set = set()
        self._closed = False
        self.dropped_frames = 0

    # -- configuration -------------------------------------------------------

    def config(self) -> _Config:
        with self._config_lock:
            if self._config is None:
                self._config = self._load_config()
            return self._config

    def _load_config(self) -> _Config:
        try:
            settings = load_settings()
            model = load_model()
        except Exception as exc:
            LOGGER.warning("Assistant mode settings unavailable: %s", type(exc).__name__)
            return _Config(False, "", "voice", 8.0, False, None, False)
        phrase = effective_phrase(settings, self._server_name())
        if model is not None and ww.normalize_phrase(model.phrase) != ww.normalize_phrase(phrase):
            model = None  # Trained for a different phrase; fall back to text spotting.
        if model is not None:
            model.sensitivity = settings["sensitivity"]
        return _Config(
            enabled=bool(settings["enabled"]),
            phrase=phrase,
            confirm=settings["confirm"],
            listen_seconds=float(settings["listen_seconds"]),
            listen_during_safety_recording=bool(settings["listen_during_safety_recording"]),
            model=model,
            wake_word_in_calls=bool(settings["wake_word_in_calls"]),
            allow_call_toggle=bool(settings["allow_call_toggle"]),
            allow_device_teaching=bool(settings["allow_device_teaching"]),
        )

    def reload(self) -> None:
        with self._config_lock:
            self._config = None
        self._post("", "reload", None)

    @property
    def enabled(self) -> bool:
        return self.config().enabled

    @property
    def gates_calls(self) -> bool:
        """Calls keep recording, but only the wake word starts an AI turn."""
        config = self.config()
        return bool(config.enabled and config.wake_word_in_calls)

    def gates_call(self, session_id: str) -> bool:
        """Whether this call answers only after the wake word.

        True for every call when "Wake word in calls" is on, and for a call
        whose device turned Assistant mode on (while devices may do that).
        """
        config = self.config()
        if not config.enabled:
            return False
        if config.wake_word_in_calls:
            return True
        with self._lock:
            chosen = str(session_id) in self._call_mode_sessions
        return bool(chosen and config.allow_call_toggle)

    def set_call_mode(self, session_id: str, enabled: bool) -> Dict[str, Any]:
        """A device turns Assistant mode on or off for its own call.

        Returns what the device should show: ``enabled`` (this call answers
        only after the wake word), ``forced`` (every call does, so it cannot be
        turned off here), and ``reason`` when the request was refused.
        """
        key = str(session_id or "").strip()
        config = self.config()
        if not key:
            return {"enabled": False, "forced": False, "reason": "no_session"}
        if not config.enabled:
            self.clear_call_mode(key)
            return {"enabled": False, "forced": False, "reason": "disabled"}
        if config.wake_word_in_calls:
            return {"enabled": True, "forced": True, "reason": "every_call"}
        if not config.allow_call_toggle:
            self.clear_call_mode(key)
            return {"enabled": False, "forced": False, "reason": "not_allowed"}
        with self._lock:
            if enabled:
                self._call_mode_sessions.add(key)
            else:
                self._call_mode_sessions.discard(key)
        if not enabled:
            self.cancel(key, "call_mode_off")
        return {"enabled": bool(enabled), "forced": False, "reason": ""}

    def call_mode(self, session_id: str) -> bool:
        with self._lock:
            return str(session_id) in self._call_mode_sessions

    def has_call_mode_sessions(self) -> bool:
        """Cheap check for the audio path before it looks up call aliases."""
        return bool(self._call_mode_sessions)

    def clear_call_mode(self, session_id: str) -> None:
        with self._lock:
            self._call_mode_sessions.discard(str(session_id))

    def listens_to(self, *, assistant_listening: bool, silent_recording: bool) -> bool:
        config = self.config()
        if not config.enabled:
            return False
        return bool(assistant_listening or (silent_recording and config.listen_during_safety_recording))

    # -- thread-safe entry points -------------------------------------------

    def feed(self, session_id: str, chunk: bytes) -> None:
        if self._closed or not chunk:
            return
        try:
            self._ensure_worker()
            self._queue.put_nowait((str(session_id), "audio", bytes(chunk)))
        except queue.Full:
            self.dropped_frames += 1
        except Exception:  # pragma: no cover - never disturb the audio path
            self.dropped_frames += 1

    def client_wake(self, session_id: str, *, score: Optional[float] = None) -> None:
        self._post(session_id, "client_wake", score)

    def reserve_device_wake(self, session_id: str, wake_id: str, *, score: Optional[float] = None) -> None:
        self._post(session_id, "device_wake", (wake_id, score))

    def device_utterance(self, session_id: str, pcm: bytes, *, score: Optional[float] = None, wake_id: str = "") -> None:
        self._post(session_id, "device_utterance", (bytes(pcm), score, wake_id))

    def start_enrollment(self, session_id: str, *, phrase: str = "", count: int = ww.MIN_TEMPLATES,
                         after_training: Optional[Callable[[], None]] = None) -> None:
        self._post(session_id, "enroll", (clean_phrase_text(phrase), int(count or ww.MIN_TEMPLATES), after_training))

    def cancel(self, session_id: str, reason: str = "cancelled") -> None:
        self._post(session_id, "cancel", reason)

    def end_session(self, session_id: str) -> None:
        self.clear_call_mode(str(session_id))
        self._post(session_id, "end", None)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            sessions = {
                key: {"state": item.state, **item.stats}
                for key, item in self._sessions.items()
            }
            calls = sorted(self._call_mode_sessions)
        return {
            "sessions": sessions,
            "call_mode_sessions": calls,
            "dropped_frames": self.dropped_frames,
            "queued": self._queue.qsize(),
            **self.wake_arbiter.snapshot(),
        }

    def session_state(self, session_id: str) -> str:
        with self._lock:
            item = self._sessions.get(str(session_id))
            return item.state if item else "idle"

    def close(self) -> None:
        self._closed = True
        try:
            self._queue.put_nowait(("", "stop", None))
        except queue.Full:
            pass
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

    # -- internals -----------------------------------------------------------

    def _post(self, session_id: str, kind: str, value: Any) -> None:
        if self._closed:
            return
        self._ensure_worker()
        try:
            self._queue.put_nowait((str(session_id), kind, value))
        except queue.Full:
            # Control messages matter more than one audio frame; make room.
            try:
                self._queue.get_nowait()
                self.dropped_frames += 1
                self._queue.put_nowait((str(session_id), kind, value))
            except (queue.Empty, queue.Full):
                LOGGER.warning("Assistant mode queue full; dropped %s for a device", kind)

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, name="AssistantModeHub", daemon=True)
            self._worker.start()

    def _session(self, session_id: str) -> _SessionAssistant:
        with self._lock:
            item = self._sessions.get(session_id)
            if item is None:
                item = _SessionAssistant(self, session_id)
                self._sessions[session_id] = item
            return item

    def _run(self) -> None:
        while True:
            session_id, kind, value = self._queue.get()
            if kind == "stop":
                return
            try:
                self._dispatch(session_id, kind, value)
            except Exception as exc:  # pragma: no cover - keep listening for others
                LOGGER.warning("Assistant mode worker error (%s): %s", kind, exc)

    def _dispatch(self, session_id: str, kind: str, value: Any) -> None:
        if kind == "reload":
            config = self.config()
            with self._lock:
                items = list(self._sessions.values())
            for item in items:
                # Include idle text spotting: it can have STT in flight too.
                if item.state in {"listening", "thinking", "enrolling"} or item.text_check_pending:
                    item.cancel("disabled" if not config.enabled else "settings_changed")
            return
        if kind == "end":
            with self._lock:
                self._sessions.pop(session_id, None)
            return
        if kind == "result":
            item, purpose, text, ticket, spoken_at = value
            if self._sessions.get(item.session_id) is not item or ticket != item.ticket:
                return  # Cancelled, disabled or ended while it was transcribed.
            item.spoken_at = spoken_at
            item.on_transcript(purpose, text, self.config())
            return
        if kind == "trained":
            item, payload, after_training = value
            self._emit(item.session_id, payload)
            if after_training is not None and payload.get("state") == "done":
                after_training()
            return
        if kind == "revoke":
            with self._lock:
                existing = self._sessions.get(session_id)
            if existing is not None:
                existing.revoke(str(value or "answered_elsewhere"))
            return
        config = self.config()
        item = self._session(session_id)
        if kind == "audio":
            item.feed(value, config)
        elif kind == "client_wake":
            if config.enabled and item.state == "idle":
                item.wake(config, source="client", score=value)
        elif kind == "device_wake":
            wake_id, score = value
            if config.enabled and config.acoustic:
                item.reserve_device_wake(wake_id, config, score)
            else:
                item._set_state("idle", "needs_training" if config.needs_training else "disabled")
        elif kind == "device_utterance":
            pcm, score, wake_id = value
            if config.enabled and item.state in {"idle", "listening"} and pcm:
                item.device_utterance(pcm, config, score, wake_id)
        elif kind == "enroll":
            phrase, count, after_training = value
            item.start_enrollment(phrase or config.phrase, count, after_training)
        elif kind == "cancel":
            item.cancel(str(value or "cancelled"))

    def listener_for(self, session_id: str) -> WakeListener:
        """This session as the wake arbiter sees it; never raises."""
        key = str(session_id or "")
        if self._describe_listener is not None:
            try:
                described = self._describe_listener(key)
                if isinstance(described, WakeListener) and described.key:
                    return described
            except Exception as exc:  # pragma: no cover - a lookup must not stop a wake
                LOGGER.debug("Assistant listener lookup failed: %s", exc)
        return WakeListener(key=key)

    def _contend_wake(
        self,
        session_id: str,
        *,
        spoken_at: Optional[float] = None,
        score: Optional[float] = None,
    ) -> "WakeClaim":
        """Arbitrate a wake across every listener (see :class:`WakeArbiter`).

        ``spoken_at`` (monotonic) dates a wake that reaches the server after the
        fact: a legacy on-device upload or a transcribed burst, estimated from the
        audio's length. A live wake is dated now and may be taken back within
        the decision window by a device that heard the word better.
        """
        key = str(session_id)
        return self.wake_arbiter.contend(
            self.listener_for(key),
            spoken_at=spoken_at,
            score=score,
            on_revoke=lambda reason: self._post(key, "revoke", reason),
        )

    def _claim_wake(self, session_id: str, *, spoken_at: Optional[float] = None) -> bool:
        return self._contend_wake(session_id, spoken_at=spoken_at).accepted

    def _executor_ready(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="AssistantModeSTT")
        return self._executor

    def _transcribe_async(self, item: _SessionAssistant, audio: bytes, *, purpose: str, config: _Config) -> None:
        ticket = item.ticket
        # When the first of these words was spoken, for matching one wake heard
        # by several devices.
        spoken_at = time.monotonic() - len(audio) / (2.0 * ww.SAMPLE_RATE)

        def job() -> None:
            # Nothing said (a breath, a click, the room): nothing to transcribe,
            # and what Whisper invents for it never becomes a question.
            voiced = speech_seconds(audio)
            text = ""
            if voiced >= MIN_QUESTION_SPEECH_SECONDS:
                try:
                    text = self._transcribe_cb(audio)
                except Exception as exc:
                    LOGGER.warning("Assistant mode transcription failed: %s", exc)
                    text = ""
            if text and transcript_is_noise(text, voiced):
                LOGGER.info("Assistant mode dropped a transcript that is not speech (%.2f s voiced)", voiced)
                text = ""
            self._post(item.session_id, "result", (item, purpose, text, ticket, spoken_at))

        self._executor_ready().submit(job)

    def _train_async(self, item: _SessionAssistant, *, phrase: str,
                     after_training: Optional[Callable[[], None]] = None) -> None:
        def job() -> None:
            settings = load_settings()
            try:
                result = train(phrase=phrase, use_voice_training=bool(settings.get("use_voice_training_clips", True)))
                if not settings.get("wake_phrase"):
                    save_settings({"wake_phrase": phrase})
                payload = {"event": "assistant_enroll", "state": "done", "report": result["report"], "wake_phrase": phrase}
            except ValueError as exc:
                payload = {"event": "assistant_enroll", "state": "failed", "reason": str(exc)}
            except Exception as exc:
                LOGGER.warning("Wake word training failed: %s", exc)
                payload = {"event": "assistant_enroll", "state": "failed", "reason": "training_error"}
            self.reload()
            self._post(item.session_id, "trained", (item, payload, after_training))

        self._executor_ready().submit(job)

    def _emit(self, session_id: str, payload: Dict[str, Any]) -> None:
        try:
            self._emit_cb(session_id, payload)
        except Exception as exc:
            LOGGER.debug("Assistant mode event not delivered: %s", exc)

    def _submit(self, session_id: str, text: str, meta: Dict[str, Any]) -> None:
        self._submit_cb(session_id, text, meta)


def _refusal_event(claim: WakeClaim) -> Dict[str, Any]:
    """What a device is told when its wake is not answered.

    ``reason`` stays ``answered_elsewhere`` for clients that predate the finer
    causes; ``cause`` names the rule that decided it (``in_call``,
    ``assistant_page``, ``speaking``).
    """
    payload: Dict[str, Any] = {"event": "assistant_state", "state": "idle", "reason": "answered_elsewhere"}
    if claim.reason and claim.reason != "answered_elsewhere":
        payload["cause"] = claim.reason
    return payload


#: What a refused wake means, for pages and the Admin page.
REFUSAL_MESSAGES = {
    "answered_elsewhere": "Another device heard that more clearly and is answering.",
    "in_call": "This device is in a call, so the call answers.",
    "assistant_page": "The Assistant page on this device is answering.",
    "speaking": "Another device is speaking an answer.",
}


def _is_home_address(value: Any) -> bool:
    """Loopback, link-local and private addresses: this home's own network."""
    import ipaddress

    text = str(value or "").strip().strip("[]")
    if not text:
        return True
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return text.lower() in {"localhost", "testclient"}
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return bool(address.is_loopback or address.is_private or address.is_link_local)


def browser_listener(
    request: Any,
    listener_key: str,
    *,
    kind: str,
    hub: Any = None,
) -> WakeListener:
    """A listener running in a browser (an Assistant page or the Admin page).

    Who it is comes only from AutoYou's own stamps, which every entry point
    strips from what a browser sends: a page opened through a paired app's
    connection is that device (and shares its place and its calls); a browser
    on the home network is itself, at home; one reached through a public link
    is away; anything else is this computer.
    """
    from shared.remote_access_policy import REMOTE_BROWSER_HEADER, REMOTE_BROWSER_VIA_WEBRTC

    headers = getattr(request, "headers", None) or {}

    def header(name: str) -> str:
        try:
            return str(headers.get(name) or "").strip()
        except Exception:
            return ""

    via = header(REMOTE_BROWSER_HEADER).lower()
    session_id = header("X-AutoYou-WebRTC-Session-Id")
    if via == REMOTE_BROWSER_VIA_WEBRTC and session_id:
        describe = getattr(hub, "listener_for", None)
        device = callable(describe) and describe(session_id)
        if isinstance(device, WakeListener):
            return WakeListener(
                key=listener_key, kind=kind, device=device.device_key, place=device.place, in_call=device.in_call
            )
        owner = header("X-AutoYou-WebRTC-Owner-Key") or session_id
        return WakeListener(key=listener_key, kind=kind, device="webrtc:" + owner)
    client = str(getattr(getattr(request, "client", None), "host", "") or "")
    tunnel_client = header("X-AutoYou-Tunnel-Client-IP")
    if tunnel_client or not _is_home_address(client):
        # Reached through a public link: nobody at home hears this browser.
        return WakeListener(key=listener_key, kind=kind, device=listener_key, place="away:" + listener_key)
    if _is_this_computer(client):
        if via:
            # A home-network browser relayed by this computer's page service:
            # its own device, though its address reads as this computer's.
            return WakeListener(key=listener_key, kind=kind, device="lan:" + listener_key)
        # The page service sets X-Forwarded-Host from the address the browser
        # used. This computer's network address (an admin signed in on another
        # device through the admin port) is another device at home; only a
        # loopback address is this computer itself.
        forwarded_host = _host_only(header("X-Forwarded-Host"))
        if forwarded_host and not _is_this_computer(forwarded_host):
            return WakeListener(key=listener_key, kind=kind, device="lan:" + listener_key)
        return WakeListener(key=listener_key, kind=kind, device="this-computer")
    return WakeListener(key=listener_key, kind=kind, device="lan:" + client)


def _host_only(value: str) -> str:
    """``192.168.1.208`` from ``192.168.1.208:8443``, ``::1`` from ``[::1]:8443``."""
    text = str(value or "").strip().split(",", 1)[0].strip()
    if text.startswith("["):
        return text[1:].split("]", 1)[0]
    if text.count(":") == 1:
        return text.split(":", 1)[0]
    return text


def _is_this_computer(value: str) -> bool:
    import ipaddress

    text = str(value or "").strip().strip("[]")
    if text.lower() in {"", "localhost", "testclient"}:
        return True
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool((mapped or address).is_loopback)


# ---------------------------------------------------------------------------
# Telling a spoken question from Whisper's inventions
# ---------------------------------------------------------------------------

#: Below this much speech there is nothing to transcribe.
MIN_QUESTION_SPEECH_SECONDS = 0.2
#: Only a sound this short can be one of Whisper's stock phrases.
STOCK_PHRASE_MAX_SPEECH_SECONDS = 1.2
# Phrases Whisper writes for a breath, a click or the room, in the languages
# it most often invents them in. Spoken questions are never just these.
_STOCK_PHRASES = frozenset({
    "you", "thank you", "thanks", "thank you so much", "thanks for watching", "thank you for watching",
    "bye", "bye bye", "okay", "ok", "so", "oh", "uh", "um", "hmm", "mm",
    "merci", "merci beaucoup", "gracias", "danke", "obrigado", "obrigada", "grazie",
    "спасибо", "ありがとう", "ありがとうございました", "谢谢", "謝謝", "감사합니다",
})
# Captions from the subtitled video Whisper learned from; never something said.
_SUBTITLE_CREDIT_RE = re.compile(
    r"amara\.org|subtitles? by|captions? by|sous-titr|untertitel|subtítulos|subtitulado|字幕|ご視聴|"
    r"radio-canada|\bzdf\b|mbc ?뉴스",
    re.IGNORECASE,
)


def speech_seconds(pcm: bytes) -> float:
    """How much of a recording is speech: 20 ms frames well above its quiet floor.

    A frame loud enough to be speech counts even with no quieter moment to
    compare it with, so an utterance recorded without a pause is not "nothing".
    """
    samples = ww.pcm16_to_float(pcm or b"")
    frame = int(0.02 * ww.SAMPLE_RATE)
    if samples.size < frame:
        return 0.0
    levels = [ww.rms_dbfs(samples[start:start + frame]) for start in range(0, samples.size - frame + 1, frame)]
    floor = sorted(levels)[len(levels) // 5]
    threshold = min(max(-50.0, floor + 10.0), -32.0)
    return sum(1 for level in levels if level >= threshold) * 0.02


def _words(text: str) -> List[str]:
    return re.findall(r"\w+", str(text or "").casefold())


def _loops(words: List[str]) -> bool:
    """A Whisper repetition loop: a word or phrase over and over."""
    for n in (1, 2, 3):
        for start in range(0, len(words) - 3 * n + 1):
            unit = words[start:start + n]
            if words[start + n:start + 2 * n] == unit and words[start + 2 * n:start + 3 * n] == unit:
                return True
    if len(words) >= 6:
        for n in (2, 3):
            counts: Dict[Tuple[str, ...], int] = {}
            for start in range(0, len(words) - n + 1):
                key = tuple(words[start:start + n])
                counts[key] = counts.get(key, 0) + 1
            if any(count >= 3 and count * n * 2 >= len(words) for count in counts.values()):
                return True
    return False


def transcript_is_noise(text: str, voiced: Optional[float] = None) -> bool:
    """True when a transcript is Whisper's invention, not a question.

    The same in every language: no words, a stock phrase from a short sound,
    a subtitle credit, or a repetition loop ("since since since", "20g de
    quake 20g de quake 20g de quake").
    """
    words = _words(text)
    if not words:
        return True
    if _SUBTITLE_CREDIT_RE.search(str(text)):
        return True
    short = voiced is None or voiced < STOCK_PHRASE_MAX_SPEECH_SECONDS
    if short and " ".join(words) in _STOCK_PHRASES:
        return True
    return _loops(words)


def local_transcriber(settings_provider: Optional[Callable[[], Mapping[str, Any]]] = None) -> Transcribe:
    """Local Whisper via the voice-note path; audio is a temp file deleted at once.

    Strict: Whisper decodes only where it hears speech and drops segments that
    are probably silence or loop on themselves.
    """

    def transcribe(pcm: bytes) -> str:
        import tempfile

        from shared.voice_messaging import transcribe_voice_note

        handle, path = tempfile.mkstemp(prefix="autoyou-assistant-", suffix=".wav")
        os.close(handle)
        try:
            with open(path, "wb") as output:
                output.write(ww.pcm16_to_wav_bytes(pcm))
            settings = settings_provider() if settings_provider else None
            return str(transcribe_voice_note(path, settings=dict(settings) if settings else None, strict=True) or "").strip()
        finally:
            try:
                os.unlink(path)
            except OSError:
                LOGGER.debug("Could not remove temporary assistant audio")

    return transcribe


# Live camera frame for a wake-word turn (server process)
def live_frame_attachment(
    session_ids: List[str],
    *,
    registry: Any = None,
    max_age_seconds: float = 3.0,
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """The newest camera frame for a device as a chat image, if fresh.

    Runs in the server process, where the WebRTC engine publishes frames.
    """
    if registry is None:
        try:
            from shared.video_call_manager import VIDEO_FRAME_REGISTRY as registry
        except Exception:
            return None
    current = time.time() if now is None else float(now)
    for session_id in [s for s in session_ids if s]:
        try:
            frame = registry.latest(session_id)
        except Exception:
            frame = None
        if frame is None:
            continue
        published = float(getattr(frame, "timestamp_ms", 0) or 0) / 1000.0
        if published and current - published > max_age_seconds:
            continue
        jpeg = getattr(frame, "jpeg_bytes", None)
        if not jpeg:
            continue
        return {
            "filename": "assistant-live-camera.jpg",
            "mimetype": "image/jpeg",
            "data": base64.b64encode(bytes(jpeg)).decode("ascii"),
            "source": "assistant_live_frame",
            "width": int(getattr(frame, "width", 0) or 0),
            "height": int(getattr(frame, "height", 0) or 0),
        }
    return None


# ---------------------------------------------------------------------------
# Device control events (shared by the full server and AutoYou Lite)
# ---------------------------------------------------------------------------

DEVICE_EVENTS = frozenset({
    "assistant_call_mode",
    "assistant_wake",
    "assistant_enroll",
    "assistant_cancel",
    "assistant_utterance",
    "assistant_model_request",
    "assistant_sample",
})

Send = Callable[[Dict[str, Any]], Any]


def _allows_device_teaching(hub: Any) -> bool:
    config = getattr(hub, "config", None)
    if not callable(config):
        return True
    return bool(getattr(config(), "allow_device_teaching", True))


def _score(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


async def handle_device_control(
    hub: "AssistantModeHub",
    *,
    session_id: str,
    event_name: str,
    payload: Mapping[str, Any],
    server_name: str,
    streaming: bool,
    send: Send,
    after_training: Optional[Callable[[], Any]] = None,
) -> None:
    """Answer one ``assistant_*`` control from a paired device.

    ``streaming`` says whether this device's microphone currently reaches the
    server (assistant listening or Safety Recording). Wakes heard by the server
    and enrolment over the stream need it; on-device detection does not: the
    device asks for the model, and after its own wake uploads only the words it
    recorded. ``send`` delivers one event payload back to the device.
    """
    import asyncio

    if not session_id or event_name not in DEVICE_EVENTS:
        return
    if event_name == "assistant_call_mode":
        # A device on a call turns Assistant mode on for that call only.
        choice = hub.set_call_mode(session_id, _as_bool(payload.get("enabled", True)))
        body = {"event": "assistant_call_mode", **choice}
        if choice["enabled"]:
            body["wake_phrase"] = effective_phrase(load_settings(), server_name)
        await _maybe_await(send(body))
        return
    if not hub.enabled:
        reply = "assistant_model" if event_name == "assistant_model_request" else "assistant_state"
        body: Dict[str, Any] = {"event": reply, "state": "idle", "reason": "disabled"}
        if reply == "assistant_model":
            body.update({"available": False, "model": None})
        await _maybe_await(send(body))
        return
    if event_name == "assistant_cancel":
        hub.cancel(session_id, "cancelled")
        return
    if event_name == "assistant_model_request":
        model = await asyncio.to_thread(device_model_payload, server_name)
        await _maybe_await(send({"event": "assistant_model", "available": model is not None, "model": model}))
        return
    if event_name == "assistant_utterance":
        pcm = decode_device_utterance(payload)
        if not pcm:
            await _maybe_await(send({"event": "assistant_state", "state": "idle", "reason": "bad_audio"}))
            return
        hub.device_utterance(session_id, pcm, score=_score(payload.get("score")), wake_id=str(payload.get("wake_id") or ""))
        return
    if event_name == "assistant_wake" and payload.get("wake_id"):
        wake_id = str(payload["wake_id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", wake_id):
            await _maybe_await(send({"event": "assistant_state", "state": "idle", "reason": "bad_wake_id"}))
            return
        hub.reserve_device_wake(session_id, wake_id, score=_score(payload.get("score")))
        return
    if event_name in {"assistant_sample", "assistant_enroll"} and not _allows_device_teaching(hub):
        # Only an admin on this computer may then record or train the word.
        await _maybe_await(send({"event": "assistant_enroll", "state": "failed", "reason": "not_allowed"}))
        return
    if event_name == "assistant_sample":
        await _handle_device_sample(hub, payload=payload, server_name=server_name, send=send, after_training=after_training)
        return
    if not streaming:
        # Nothing to listen to: the device must stream (assistant listening
        # or Safety Recording) before a server-side wake or enrolment works.
        reply_event = "assistant_enroll" if event_name == "assistant_enroll" else "assistant_state"
        await _maybe_await(send({
            "event": reply_event,
            "state": "failed" if reply_event == "assistant_enroll" else "idle",
            "reason": "not_streaming",
        }))
        return
    if event_name == "assistant_wake":
        hub.client_wake(session_id, score=_score(payload.get("score")))
        return
    action = str(payload.get("action") or "start").strip().lower()
    if action == "cancel":
        hub.cancel(session_id, "cancelled")
        return
    try:
        count = int(payload.get("count") or ww.MIN_TEMPLATES)
    except (TypeError, ValueError):
        count = ww.MIN_TEMPLATES
    loop = asyncio.get_running_loop()

    def notify_trained() -> None:
        async def notify() -> None:
            try:
                if after_training is not None:
                    await _maybe_await(after_training())
            except Exception:
                LOGGER.exception("Could not publish the trained wake word")

        if not loop.is_closed():
            loop.call_soon_threadsafe(lambda: asyncio.create_task(notify()))

    hub.start_enrollment(session_id, phrase=str(payload.get("wake_phrase") or ""), count=count,
                         after_training=notify_trained if after_training is not None else None)


async def _handle_device_sample(
    hub: "AssistantModeHub",
    *,
    payload: Mapping[str, Any],
    server_name: str,
    send: Send,
    after_training: Optional[Callable[[], Any]],
) -> None:
    """One recording of the wake word from a device that does not stream.

    With ``train: true`` the model is rebuilt from every stored recording once
    at least ``MIN_TEMPLATES`` are usable.
    """
    import asyncio

    phrase_text = clean_phrase_text(payload.get("wake_phrase"))
    pcm = decode_device_utterance(payload)
    if pcm:
        result = await asyncio.to_thread(add_sample, pcm, source="device", phrase=phrase_text)
        stored = len(list_samples())
        await _maybe_await(send({
            "event": "assistant_enroll",
            "state": "captured" if result.get("stored") else "rejected",
            "captured": stored,
            "needed": ww.MIN_TEMPLATES,
            "problems": list(result.get("problems") or []),
            "duration_seconds": result.get("duration_seconds"),
        }))
    if not payload.get("train"):
        return
    settings = await asyncio.to_thread(load_settings)
    if phrase_text and not settings.get("wake_phrase"):
        settings = await asyncio.to_thread(save_settings, {"wake_phrase": phrase_text})
    phrase = phrase_text or effective_phrase(settings, server_name)
    try:
        result = await asyncio.to_thread(
            train, phrase=phrase, use_voice_training=bool(settings.get("use_voice_training_clips", True))
        )
        body = {"event": "assistant_enroll", "state": "done", "report": result["report"], "wake_phrase": phrase}
    except ValueError as exc:
        body = {"event": "assistant_enroll", "state": "failed", "reason": str(exc)}
    hub.reload()
    await _maybe_await(send(body))
    if after_training is not None and body["state"] == "done":
        await _maybe_await(after_training())


async def _maybe_await(value: Any) -> Any:
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value
