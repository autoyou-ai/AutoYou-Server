# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import math
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass(frozen=True)
class VoiceAudioQuality:
    duration_seconds: float
    rms_dbfs: float
    peak_dbfs: float
    speech_frame_ratio: float
    clipped_sample_ratio: float
    sample_rate: int
    frame_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration_seconds": round(float(self.duration_seconds), 3),
            "rms_dbfs": round(float(self.rms_dbfs), 2),
            "peak_dbfs": round(float(self.peak_dbfs), 2),
            "speech_frame_ratio": round(float(self.speech_frame_ratio), 3),
            "clipped_sample_ratio": round(float(self.clipped_sample_ratio), 6),
            "sample_rate": int(self.sample_rate),
            "frame_count": int(self.frame_count),
        }


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(max(float(value), 1e-12))


def analyze_pcm16_audio(
    audio_data: bytes,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
) -> VoiceAudioQuality:
    raw = bytes(audio_data or b"")
    sample_rate = max(1, int(sample_rate or 16000))
    channels = max(1, int(channels or 1))
    sample_count = len(raw) // 2
    if sample_count <= 0:
        return VoiceAudioQuality(0.0, -240.0, -240.0, 0.0, 0.0, sample_rate, 0)

    samples = array("h")
    samples.frombytes(raw[: sample_count * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    if channels > 1:
        mono: List[int] = []
        frame_total = len(samples) // channels
        for index in range(frame_total):
            start = index * channels
            mono.append(int(sum(samples[start : start + channels]) / channels))
        values = mono
    else:
        values = samples

    if not values:
        return VoiceAudioQuality(0.0, -240.0, -240.0, 0.0, 0.0, sample_rate, 0)

    inv_max = 1.0 / 32768.0
    abs_peak = 0
    square_sum = 0.0
    clipped = 0
    for value in values:
        magnitude = abs(int(value))
        abs_peak = max(abs_peak, magnitude)
        clipped += 1 if magnitude >= 32112 else 0
        normalized = float(value) * inv_max
        square_sum += normalized * normalized

    duration = float(len(values)) / float(sample_rate)
    rms = math.sqrt(square_sum / float(len(values)))
    peak = float(abs_peak) * inv_max
    clipped_ratio = float(clipped) / float(len(values))

    frame_size = max(1, int(round(sample_rate * 0.025)))
    frame_total = max(1, len(values) // frame_size)
    speech_threshold = 10.0 ** (-40.0 / 20.0)
    speech_frames = 0
    for frame_index in range(frame_total):
        start = frame_index * frame_size
        end = start + frame_size
        frame_square_sum = 0.0
        for value in values[start:end]:
            normalized = float(value) * inv_max
            frame_square_sum += normalized * normalized
        frame_rms = math.sqrt(frame_square_sum / float(frame_size))
        if frame_rms >= speech_threshold:
            speech_frames += 1
    speech_ratio = float(speech_frames) / float(frame_total)

    return VoiceAudioQuality(
        duration_seconds=duration,
        rms_dbfs=_dbfs(rms),
        peak_dbfs=_dbfs(peak),
        speech_frame_ratio=speech_ratio,
        clipped_sample_ratio=clipped_ratio,
        sample_rate=sample_rate,
        frame_count=len(values),
    )


def analyze_wav_file(path: str | Path) -> VoiceAudioQuality:
    with wave.open(str(path), "rb") as wf:
        channels = int(wf.getnchannels() or 1)
        sample_width = int(wf.getsampwidth() or 0)
        sample_rate = int(wf.getframerate() or 16000)
        raw = wf.readframes(wf.getnframes())
    if sample_width != 2:
        return VoiceAudioQuality(0.0, -240.0, -240.0, 0.0, 0.0, sample_rate, 0)
    return analyze_pcm16_audio(raw, sample_rate=sample_rate, channels=channels)


def voice_training_rejection_reasons(
    quality: VoiceAudioQuality,
    transcript: str,
    *,
    min_duration_seconds: float,
    max_duration_seconds: float,
    min_speech_frame_ratio: float,
    min_rms_dbfs: float,
    min_transcript_chars: int,
    max_transcript_chars: int,
) -> List[str]:
    reasons: List[str] = []
    text_len = len(str(transcript or "").strip())
    if quality.duration_seconds < min_duration_seconds:
        reasons.append("too_short")
    if max_duration_seconds > 0 and quality.duration_seconds > max_duration_seconds:
        reasons.append("too_long")
    if quality.speech_frame_ratio < min_speech_frame_ratio:
        reasons.append("too_much_silence")
    if quality.rms_dbfs < min_rms_dbfs:
        reasons.append("too_quiet")
    if text_len < min_transcript_chars:
        reasons.append("transcript_too_short")
    if max_transcript_chars > 0 and text_len > max_transcript_chars:
        reasons.append("transcript_too_long")
    return reasons
