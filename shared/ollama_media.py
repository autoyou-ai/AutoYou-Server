# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Prepare bounded chat media for an Ollama model off the request loop."""

from __future__ import annotations

import base64
import io
import logging
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.conversation_media_store import read_attachment_bytes
from shared.media_messaging import attachment_mimetype
from shared.openclaw_gateway import safe_filename

logger = logging.getLogger(__name__)

MAX_IMAGE_SIDE = 1536
_MAX_MEDIA_PARTS = 4
_MAX_ATTACHMENTS = 8
_MAX_TEXT_FILES = 2
_AUDIO_RATE = 16000
_MAX_AUDIO_SAMPLES = 30 * _AUDIO_RATE
_IMAGE_MIMES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


@dataclass
class OllamaAttachments:
    media: list[tuple[str, str]]
    notes: list[str]
    forwarded: int


def _bounded_bytes(attachment: dict[str, Any], limit: int) -> bytes:
    encoded = attachment.get("data")
    url = attachment.get("url")
    if not encoded and isinstance(url, str) and url.startswith("data:"):
        encoded = url.partition(",")[2]
    if isinstance(encoded, str) and len(encoded) > 4 * ((limit + 2) // 3):
        return b""
    if not encoded and attachment.get("path"):
        path = Path(attachment["path"])
        if path.drive.startswith("\\") or path.stat().st_size > 4 * ((limit + 2) // 3) + 4096:
            return b""
    data = read_attachment_bytes(attachment)
    return data if data and len(data) <= limit else b""


def _encode_image(image: Any, max_side: int) -> tuple[str, bytes]:
    if image.width * image.height > 36_000_000:
        raise ValueError("Image exceeds the pixel limit.")
    image.thumbnail((max_side, max_side))
    output = io.BytesIO()
    if image.mode in {"RGBA", "LA", "P"}:
        image.save(output, format="PNG")
        return "image/png", output.getvalue()
    image.convert("RGB").save(output, format="JPEG", quality=88)
    return "image/jpeg", output.getvalue()


def _prepare_image(data: bytes, max_side: int) -> tuple[str, bytes]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        mime = _IMAGE_MIMES.get(image.format)
        if mime and max(image.size) <= max_side:
            image.verify()
            return mime, data
        return _encode_image(image, max_side)


def _sample_video(data: bytes, sample_count: int) -> list[tuple[str, bytes]]:
    import av

    frames = []
    seen = set()
    with av.open(io.BytesIO(data)) as container:
        if not container.streams.video:
            return frames
        stream = container.streams.video[0]
        if stream.width * stream.height > 36_000_000:
            return frames
        stream.codec_context.thread_count = 1
        stream.codec_context.skip_frame = "NONKEY"
        duration = stream.duration
        if not duration and container.duration and stream.time_base:
            duration = int(container.duration / av.time_base / stream.time_base)
        offsets = [duration * index // sample_count for index in range(sample_count)] if duration else [0]
        for offset in offsets:
            if offset:
                try:
                    container.seek((stream.start_time or 0) + offset, stream=stream)
                except av.FFmpegError:
                    break
            frame = next(container.decode(stream), None)
            if frame is None or frame.pts in seen or frame.width * frame.height > 36_000_000:
                continue
            seen.add(frame.pts)
            frames.append(_encode_image(frame.to_image(), 768))
    return frames


def _prepare_audio(data: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            if (
                source.getframerate() == _AUDIO_RATE
                and source.getnchannels() == 1
                and source.getsampwidth() == 2
                and 0 < source.getnframes() <= _MAX_AUDIO_SAMPLES
            ):
                return data
    except (wave.Error, EOFError):
        pass

    import av

    pcm = bytearray()
    max_pcm_bytes = 2 * _MAX_AUDIO_SAMPLES
    with av.open(io.BytesIO(data)) as container:
        if not container.streams.audio:
            return b""
        stream = container.streams.audio[0]
        stream.codec_context.thread_count = 1
        resampler = av.AudioResampler(format="s16", layout="mono", rate=_AUDIO_RATE)
        for frame in container.decode(stream):
            for sample in resampler.resample(frame):
                pcm.extend(sample.to_ndarray().tobytes()[:max_pcm_bytes - len(pcm)])
            if len(pcm) >= max_pcm_bytes:
                break
        if len(pcm) < max_pcm_bytes:
            for sample in resampler.resample(None):
                pcm.extend(sample.to_ndarray().tobytes()[:max_pcm_bytes - len(pcm)])
    if not pcm:
        return b""
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, _AUDIO_RATE, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    return output.getvalue()


def prepare_ollama_attachments(
    attachments: list[dict[str, Any]],
    *,
    vision: bool,
    audio: bool = False,
    text_files: bool = False,
) -> OllamaAttachments:
    """Return only media the chosen transport can send. File paths stay local."""
    result = OllamaAttachments(media=[], notes=[], forwarded=0)
    text_count = 0
    for attachment in attachments[:_MAX_ATTACHMENTS]:
        capacity = _MAX_MEDIA_PARTS - len(result.media)
        parts = []
        note = ""
        try:
            mime = attachment_mimetype(attachment).split(";", 1)[0].lower()
            name = safe_filename(str(attachment.get("filename") or ""), mime)
            if capacity and vision and mime.startswith("image/"):
                data = _bounded_bytes(attachment, 10 * 1024 * 1024)
                if data:
                    parts = [_prepare_image(data, MAX_IMAGE_SIDE)]
            elif capacity and vision and mime.startswith("video/"):
                data = _bounded_bytes(attachment, 25 * 1024 * 1024)
                if data:
                    parts = _sample_video(data, min(capacity, 3))
                if parts:
                    note = f"Video attachment {name} is represented by {len(parts)} sampled frames."
            elif capacity and audio and mime.startswith("audio/"):
                data = _bounded_bytes(attachment, 8 * 1024 * 1024)
                wav = _prepare_audio(data) if data else b""
                if wav:
                    parts = [("audio/wav", wav)]
                    note = f"Audio attachment {name} contains up to the first 30 seconds."
                    audio = False
            elif text_files and text_count < _MAX_TEXT_FILES and (mime.startswith("text/") or mime == "application/json"):
                data = _bounded_bytes(attachment, 8192)
                if data:
                    note = f"Text attachment {name} (data):\n{data.decode('utf-8-sig')}"
                    text_count += 1
        except Exception as exc:
            logger.debug("Ollama could not prepare an attachment (%s)", type(exc).__name__)
            continue
        if parts or note:
            result.forwarded += 1
            result.media.extend((kind, base64.b64encode(data).decode("ascii")) for kind, data in parts)
            if note:
                result.notes.append(note)
    return result
