# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-e881dea4f811a773d4ecad83

"""Recorded voice-note pipeline shared by ``server.py`` and ``autoyou_lite/server.py``.

A *pure* voice note (an audio attachment with **no** accompanying text caption)
sent over a messaging partner - WhatsApp, Telegram, Signal, or a connected
WebRTC client - should be answered with a spoken reply rather than filed away as
a note. The flow, reusing the *same* local STT/LLM/TTS path the live voice call
uses, is:

    inbound audio attachment
        -> transcribe locally (faster-whisper, same Whisper backend as calls)
        -> run the transcript through the partner's normal chat turn (LiteLLM/ADK)
        -> synthesize the reply to an audio file (AudioManager TTS providers)
        -> transcode WAV -> OGG/Opus (PyAV) for a real "voice note" bubble
        -> send the audio back over the originating partner (queued if offline)

This module owns only the partner-agnostic local primitives: detection, file
transcription, TTS-to-file, and transcoding. The transcript turn and the audio
send stay with each partner/service so the LLM turn uses each partner's existing
code path (agent routing, ADK session, owner identity) unchanged.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import base64
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from shared.secure_storage import FILE_HEADER as SPM_FILE_HEADER, materialize_secure_file, read_secure_file

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-e881dea4f811a773d4ecad83"


LOGGER = logging.getLogger("autoyou.voice_messaging")

# PyAV is already a dependency of shared.audio_manager (WebRTC frame handling); it
# bundles ffmpeg/libopus so we can transcode in-process without an external binary.
try:  # pragma: no cover - optional dependency
    import av  # type: ignore
except Exception:  # pragma: no cover
    av = None  # type: ignore[assignment]

# faster-whisper is bundled with RealtimeSTT (the live-call STT backend). Importing
# it directly lets us transcribe a *file* without spinning up a live recorder.
try:  # pragma: no cover - optional dependency
    from faster_whisper import WhisperModel as _WhisperModel  # type: ignore
except Exception:  # pragma: no cover
    _WhisperModel = None  # type: ignore[assignment]

_AUDIO_EXTENSIONS = (
    ".m4a",
    ".mp3",
    ".ogg",
    ".oga",
    ".opus",
    ".wav",
    ".aac",
    ".amr",
    ".flac",
    ".webm",
    ".aiff",
    ".aif",
)

# Temp files this module creates are tagged so callers (and a best-effort sweep)
# can recognise them. Callers should ``cleanup_paths(...)`` once delivery is done.
_TMP_PREFIX = "autoyou_voicenote_"

def faster_whisper_available() -> bool:
    return _WhisperModel is not None

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_speech_settings_provider: Optional[Callable[[], Dict[str, Any]]] = None

def set_speech_settings_provider(provider: Optional[Callable[[], Dict[str, Any]]]) -> None:
    """Provide the live server speech config used by voice-note STT/TTS."""
    global _speech_settings_provider
    _speech_settings_provider = provider
    # from __debug_provenance_b__ import yearly

def _normalized_speech_settings() -> Dict[str, Any]:
    """Return the live, normalised ``speech`` config (same shape AudioManager uses)."""
    try:
        from shared.audio_manager import deepcopy_speech_config, normalize_speech_config

        if _speech_settings_provider is not None:
            try:
                provided = _speech_settings_provider()
                if isinstance(provided, dict):
                    return normalize_speech_config(provided)
            except Exception as provider_exc:
                LOGGER.warning("Live speech settings provider failed; using defaults: %s", provider_exc)

        return normalize_speech_config(deepcopy_speech_config())
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.debug("Falling back to default speech settings: %s", exc)
        return {
            "tts": {"provider": "system"},
            "stt": {
                "model": "base",
                "language": "",
                "compute_type": "int8",
                "device": "auto",
                "beam_size": None,
            },
        }

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _looks_like_audio_mimetype(mimetype: Any) -> bool:
    return str(mimetype or "").strip().lower().startswith("audio/")

def _looks_like_audio_path(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    # Strip a possible data-URL / query suffix before checking the extension.
    base = text.split("?", 1)[0]
    return base.endswith(_AUDIO_EXTENSIONS)

def attachment_is_audio(attachment: Dict[str, Any]) -> bool:
    if not isinstance(attachment, dict):
        return False
    if _looks_like_audio_mimetype(attachment.get("mimetype")):
        return True
    for key in ("filename", "path", "url", "name"):
        if _looks_like_audio_path(attachment.get(key)):
            return True
    return False

def _flatten_attachments(context_or_attachments: Any) -> List[Dict[str, Any]]:
    """Accept either a list of context items (each with an ``attachments`` list) or a
    flat list of attachment dicts, and return the flat list of attachment dicts."""
    out: List[Dict[str, Any]] = []
    if not context_or_attachments:
        return out
    items = context_or_attachments
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        nested = item.get("attachments")
        if isinstance(nested, list):
            out.extend(a for a in nested if isinstance(a, dict))
        elif item.get("mimetype") or item.get("filename") or item.get("path") or item.get("data") or item.get("url"):
            out.append(item)
    return out

def _is_placeholder_text(message_text: Any) -> bool:
    """True when there is no real user instruction - empty or the synthetic
    ``[Attachment: ...]`` placeholder partners synthesise for caption-less media."""
    text = str(message_text or "").strip()
    if not text:
        return True
    normalized = " ".join(text.lower().rstrip(".").split())
    if normalized in {
        "(attachment)",
        "please review the attached file",
        "please review the attached files",
        "please review attached file",
        "please review attached files",
    }:
        return True
    if normalized.startswith("please review the ") and normalized.endswith(" attached files"):
        middle = normalized[len("please review the "):-len(" attached files")].strip()
        if middle.isdigit():
            return True
    if text.startswith("[Attachment:") and text.endswith("]"):
        return True
    if text.startswith("[Voice") and text.endswith("]"):
        return True
    return False

def is_voice_note_only(
    message_text: Any,
    context_or_attachments: Any,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Return ``(True, audio_attachment)`` when the message is a pure voice note:
    exactly one attachment, that attachment is audio, and there is no real text
    instruction. Otherwise ``(False, None)`` so callers keep today's behavior."""
    attachments = _flatten_attachments(context_or_attachments)
    if not attachments:
        return False, None
    audio_attachments = [a for a in attachments if attachment_is_audio(a)]
    if len(audio_attachments) != 1:
        return False, None
    # A caption-bearing voice note keeps today's behavior, so the text must be empty
    # or the synthetic attachment placeholder, and there must be no *other* media.
    if len(attachments) != 1:
        return False, None
    if not _is_placeholder_text(message_text):
        return False, None
    return True, audio_attachments[0]

_PRIVATE_VOICE_NOTE_METADATA_KEYS = {
    "audio_path",
    "inbound_audio_path",
    "local_audio_path",
    "ogg_path",
    "reply_audio_path",
    "saved_audio_path",
    "wav_path",
}

def public_voice_note_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return voice-note metadata safe to echo to clients.

    The recorded voice-note pipeline needs local paths internally for STT, TTS,
    and cleanup, but WebRTC/mobile/desktop clients should only see portable
    attachment metadata and embedded audio bytes.
    """
    if not isinstance(metadata, dict):
        return {}
    cleaned: Dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if key_text in _PRIVATE_VOICE_NOTE_METADATA_KEYS or key_text.endswith("_path"):
            continue
        cleaned[key] = value
    return cleaned

def strip_private_voice_note_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Sanitize the ``voice_note`` section inside a larger metadata dict."""
    if not isinstance(metadata, dict):
        return {}
    cleaned = dict(metadata)
    voice_note = cleaned.get("voice_note")
    if isinstance(voice_note, dict):
        cleaned["voice_note"] = public_voice_note_metadata(voice_note)
    return cleaned

# ---------------------------------------------------------------------------
# Materialise an attachment to a local file path
# ---------------------------------------------------------------------------

def _decode_base64_payload(data: Any) -> Optional[bytes]:
    if not data:
        return None
    raw = str(data)
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=False)
    except Exception:
        return None

def resolve_audio_to_path(attachment: Dict[str, Any]) -> Optional[str]:
    """Return a local filesystem path to the attachment audio, materialising base64
    / data-URL payloads to a temp file when there is no usable ``path`` yet.

    The returned path is owned by the caller; pass it to :func:`cleanup_paths` when
    delivery completes (only temp files we created are removed)."""
    if not isinstance(attachment, dict):
        return None

    existing = str(attachment.get("path") or "").strip()
    if existing and os.path.isfile(existing):
        return existing

    payload = _decode_base64_payload(attachment.get("data"))
    if payload is None:
        url = str(attachment.get("url") or "")
        if url.startswith("data:"):
            payload = _decode_base64_payload(url)
    if payload is None:
        return None

    suffix = ".ogg"
    name = str(attachment.get("filename") or attachment.get("name") or "")
    for ext in _AUDIO_EXTENSIONS:
        if name.lower().endswith(ext):
            suffix = ext
            break
    else:
        mimetype = str(attachment.get("mimetype") or "").lower()
        if "mp3" in mimetype or "mpeg" in mimetype:
            suffix = ".mp3"
        elif "wav" in mimetype:
            suffix = ".wav"
        elif "mp4" in mimetype or "m4a" in mimetype or "aac" in mimetype:
            suffix = ".m4a"
        elif "webm" in mimetype:
            suffix = ".webm"

    try:
        fd, path = tempfile.mkstemp(prefix=_TMP_PREFIX, suffix=suffix)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        return path
    except Exception as exc:
        LOGGER.warning("Failed to materialise inbound voice note to disk: %s", exc)
        return None

# ---------------------------------------------------------------------------
# Speech-to-text (file)
# ---------------------------------------------------------------------------

_whisper_lock = threading.Lock()
_whisper_model: Any = None
_whisper_model_key: Optional[Tuple[str, str, str]] = None

def _get_whisper_model(model_name: str, device: str, compute_type: str) -> Any:
    global _whisper_model, _whisper_model_key
    if _WhisperModel is None:
        return None
    key = (model_name, device, compute_type)
    with _whisper_lock:
        if _whisper_model is not None and _whisper_model_key == key:
            return _whisper_model
        try:
            from shared.audio_manager import configure_whisper_cache_environment

            configure_whisper_cache_environment()
        except Exception:
            pass
        try:
            _whisper_model = _WhisperModel(model_name, device=device, compute_type=compute_type)
        except Exception as exc:
            LOGGER.warning(
                "Whisper model load failed for (%s, %s, %s): %s; retrying on cpu/int8",
                model_name,
                device,
                compute_type,
                exc,
            )
            try:
                _whisper_model = _WhisperModel(model_name, device="cpu", compute_type="int8")
                key = (model_name, "cpu", "int8")
            except Exception as exc2:
                LOGGER.error("Whisper model unavailable: %s", exc2)
                _whisper_model = None
                _whisper_model_key = None
                return None
        _whisper_model_key = key
        return _whisper_model

def transcribe_voice_note(path: str, *, settings: Optional[Dict[str, Any]] = None) -> str:
    """Transcribe an audio *file* locally using faster-whisper. Returns ``""`` when
    transcription is unavailable (caller should then fall back to today's behavior)."""
    if not path or not os.path.isfile(path):
        return ""
    if _WhisperModel is None:
        LOGGER.warning("faster-whisper not installed; cannot transcribe recorded voice note")
        return ""
    cfg = settings or _normalized_speech_settings()
    stt = cfg.get("stt", {}) if isinstance(cfg, dict) else {}
    model = _get_whisper_model(
        str(stt.get("model") or "base"),
        str(stt.get("device") or "auto"),
        str(stt.get("compute_type") or "int8"),
    )
    if model is None:
        return ""
    language = (str(stt.get("language") or "").strip() or None)
    try:
        beam_size = int(stt.get("beam_size") or 5)
    except Exception:
        beam_size = 5
    try:
        with materialize_secure_file(path) as readable_path:
            segments, _info = model.transcribe(str(readable_path), language=language, beam_size=beam_size)
            # Segment iterators may read/decode lazily. Keep the owned plaintext
            # materialization alive until the provider finishes consuming it.
            text = " ".join((segment.text or "").strip() for segment in segments).strip()
        return text
    except Exception as exc:
        LOGGER.error("Voice note transcription failed for %s: %s", path, exc)
        return ""

async def transcribe_voice_note_async(path: str, *, settings: Optional[Dict[str, Any]] = None) -> str:
    return await asyncio.to_thread(transcribe_voice_note, path, settings=settings)

# ---------------------------------------------------------------------------
# Text-to-speech (file) - reuse a process-wide TTS-only AudioManager
# ---------------------------------------------------------------------------

_tts_lock = threading.Lock()
_tts_manager: Any = None

def _get_tts_manager() -> Any:
    global _tts_manager
    with _tts_lock:
        if _tts_manager is None:
            from shared.audio_manager import AudioManager

            # enable_stt=False -> no STT recorder / feeder thread; TTS providers only.
            _tts_manager = AudioManager(
                on_text_callback=lambda *_: None,
                settings_provider=_normalized_speech_settings,
                enable_stt=False,
            )
        return _tts_manager

def synthesize_voice_reply(text: str, *, context: str = "") -> Optional[str]:
    """Synthesize ``text`` to a WAV file and return its path, or ``None`` when TTS is
    off/unavailable (caller then sends a text reply so the user is never left silent)."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    try:
        manager = _get_tts_manager()
        return manager.synthesize_to_file(cleaned, context=context) if context else manager.synthesize_to_file(cleaned)
    except Exception as exc:
        LOGGER.warning("Voice reply synthesis unavailable: %s", exc)
        return None

async def synthesize_voice_reply_async(text: str, *, context: str = "") -> Optional[str]:
    return await asyncio.to_thread(synthesize_voice_reply, text, context=context)

# ---------------------------------------------------------------------------
# Transcode WAV -> OGG/Opus (for a real "voice note" bubble on WhatsApp/Telegram)
# ---------------------------------------------------------------------------

def to_voice_note_ogg(source_path: Optional[str]) -> Optional[str]:
    """Transcode an audio file to mono OGG/Opus at 48 kHz (the format WhatsApp/Telegram
    voice notes require). Returns the OGG path, or the original path when PyAV/libopus
    is unavailable (callers can still send WAV as a plain audio attachment)."""
    if not source_path or not os.path.isfile(source_path):
        return source_path
    if av is None:
        return source_path
    if source_path.lower().endswith((".ogg", ".oga", ".opus")):
        return source_path

    out_path = source_path.rsplit(".", 1)[0] + ".ogg"
    if not out_path.startswith(tempfile.gettempdir()) and _TMP_PREFIX not in os.path.basename(out_path):
        # Keep transcodes in temp space so cleanup_paths can reclaim them.
        fd, out_path = tempfile.mkstemp(prefix=_TMP_PREFIX, suffix=".ogg")
        os.close(fd)

    in_container = None
    out_container = None
    try:
        with materialize_secure_file(source_path) as readable_path:
            in_container = av.open(str(readable_path))
            out_container = av.open(out_path, mode="w", format="ogg")
            in_stream = in_container.streams.audio[0]
            out_stream = out_container.add_stream("libopus", rate=48000)
            try:
                out_stream.layout = "mono"
            except Exception:
                pass
            resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)

            def _emit(frame: Any) -> None:
                for packet in out_stream.encode(frame):
                    out_container.mux(packet)

            for frame in in_container.decode(in_stream):
                frame.pts = None
                resampled = resampler.resample(frame)
                if resampled is None:
                    continue
                if not isinstance(resampled, list):
                    resampled = [resampled]
                for resampled_frame in resampled:
                    _emit(resampled_frame)
            _emit(None)  # flush encoder
        return out_path
    except Exception as exc:
        LOGGER.warning("WAV->OGG/Opus transcode failed (%s); using original audio", exc)
        try:
            if out_path and os.path.isfile(out_path) and out_path != source_path:
                os.remove(out_path)
        except OSError:
            pass
        return source_path
    finally:
        for container in (out_container, in_container):
            try:
                if container is not None:
                    container.close()
            except Exception:
                pass

async def to_voice_note_ogg_async(source_path: Optional[str]) -> Optional[str]:
    return await asyncio.to_thread(to_voice_note_ogg, source_path)

# ---------------------------------------------------------------------------
# High-level helpers used by partner services
# ---------------------------------------------------------------------------

@dataclass
class VoiceReplyArtifacts:
    transcript: str
    reply_text: str
    wav_path: Optional[str]
    ogg_path: Optional[str]

    @property
    def audio_path(self) -> Optional[str]:
        """Best audio file to send (OGG/Opus preferred, WAV fallback)."""
        return self.ogg_path or self.wav_path

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_path)

async def transcribe_attachment(
    attachment: Dict[str, Any],
    *,
    settings: Optional[Dict[str, Any]] = None,
    cleanup_materialized: bool = True,
) -> str:
    """Convenience: materialise an attachment to disk and transcribe it."""
    path = resolve_audio_to_path(attachment)
    if not path:
        return ""
    try:
        return await transcribe_voice_note_async(path, settings=settings)
    finally:
        if cleanup_materialized and os.path.basename(path).startswith(_TMP_PREFIX):
            cleanup_paths(path)

async def build_voice_reply(reply_text: str, *, context: str = "") -> Optional[VoiceReplyArtifacts]:
    """Synthesize ``reply_text`` to audio and transcode to OGG/Opus. Returns ``None``
    when TTS is unavailable (caller should send text)."""
    wav = await synthesize_voice_reply_async(reply_text, context=context)
    if not wav:
        return None
    ogg = await to_voice_note_ogg_async(wav)
    return VoiceReplyArtifacts(transcript="", reply_text=reply_text, wav_path=wav, ogg_path=ogg)

def attachment_from_audio_file(
    path: str,
    *,
    filename: Optional[str] = None,
    platform: str = "webrtc",
) -> Optional[Dict[str, Any]]:
    """Build a data-channel attachment payload from a synthesized voice reply.

    Messaging partners can send from a local file path directly, but WebRTC app
    clients need the audio bytes embedded in the chat envelope so the message can
    also be queued while the client is offline.
    """
    if not path or not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext in (".ogg", ".oga", ".opus"):
        mimetype = "audio/ogg; codecs=opus"
    elif ext == ".wav":
        mimetype = "audio/wav"
    elif ext in (".m4a", ".aac", ".mp4"):
        mimetype = "audio/mp4"
    elif ext == ".mp3":
        mimetype = "audio/mpeg"
    elif ext in (".aiff", ".aif"):
        mimetype = "audio/aiff"
    else:
        mimetype = "application/octet-stream"
    try:
        raw = Path(path).read_bytes()
        data = read_secure_file(path) if raw.startswith(SPM_FILE_HEADER) else raw
        data_b64 = base64.b64encode(data).decode("ascii")
        size_bytes = len(data)
    except OSError as exc:
        LOGGER.warning("Failed to read synthesized voice reply %s: %s", path, exc)
        return None
    resolved_filename = filename or ("voice-reply.ogg" if ext in (".ogg", ".oga", ".opus") else f"voice-reply{ext or '.audio'}")
    return {
        "filename": resolved_filename,
        "mimetype": mimetype,
        "data": data_b64,
        "size_bytes": size_bytes,
        "meta": {
            "platform": platform,
            "kind": "voice",
            "role": "voice_reply",
        },
    }

def cleanup_paths(*paths: Optional[str]) -> None:
    """Best-effort removal of temp files this module created."""
    owned_media_root = (Path(tempfile.gettempdir()) / "autoyou_media").resolve()
    candidates: List[str] = []
    for path in paths:
        if not path:
            continue
        candidates.append(path)
        try:
            p = Path(path)
            if p.name.startswith(_TMP_PREFIX):
                stem = p.with_suffix("")
                for ext in _AUDIO_EXTENSIONS:
                    candidates.append(str(stem.with_suffix(ext)))
        except Exception:
            pass
    seen: set[str] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            candidate_path = Path(path)
            is_owned_media = False
            try:
                candidate_path.resolve().relative_to(owned_media_root)
                is_owned_media = True
            except ValueError:
                pass
            if os.path.isfile(path) and (
                os.path.basename(path).startswith(_TMP_PREFIX) or is_owned_media
            ):
                os.remove(path)
        except OSError:
            pass
