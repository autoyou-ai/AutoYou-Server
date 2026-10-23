# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Choose the owner's clearest clips and join them into one cloning reference.

Chatterbox clones from a few seconds of speech: it reads the first 6 seconds of
the reference for speaking style and the first 10 for timbre. Quality of those
seconds matters far more than quantity. On call audio the common failures are
silence, room noise, clipping and muffled clips (a phone line that kept almost
nothing above ~1.3 kHz broke the clone in testing), so each clip is measured for
loudness, speech fraction, clipping and bandwidth before it can be used.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import io
import math
import os
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

REFERENCE_SAMPLE_RATE = 24000
CURATED_SOURCES = frozenset({"upload", "manual", "curated"})
SPEECH_FRAME_DBFS = -40.0
FRAME_SECONDS = 0.025


@dataclass(frozen=True)
class ReferenceThresholds:
    min_clip_seconds: float = 1.0
    max_clip_seconds: float = 30.0
    min_speech_ratio: float = 0.5
    min_rms_dbfs: float = -35.0
    max_clipped_ratio: float = 0.001
    min_bandwidth_hz: float = 3000.0
    target_speech_seconds: float = 10.0
    max_reference_seconds: float = 15.0
    min_speech_seconds: float = 5.0

    @classmethod
    def from_environment(cls) -> "ReferenceThresholds":
        def read(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, "") or default)
            except ValueError:
                return default

        base = cls()
        return cls(
            min_clip_seconds=read("AUTOYOU_VOICE_CLONE_MIN_CLIP_SECONDS", base.min_clip_seconds),
            max_clip_seconds=read("AUTOYOU_VOICE_CLONE_MAX_CLIP_SECONDS", base.max_clip_seconds),
            min_speech_ratio=read("AUTOYOU_VOICE_CLONE_MIN_SPEECH_RATIO", base.min_speech_ratio),
            min_rms_dbfs=read("AUTOYOU_VOICE_CLONE_MIN_RMS_DBFS", base.min_rms_dbfs),
            max_clipped_ratio=read("AUTOYOU_VOICE_CLONE_MAX_CLIPPED_RATIO", base.max_clipped_ratio),
            min_bandwidth_hz=read("AUTOYOU_VOICE_CLONE_MIN_BANDWIDTH_HZ", base.min_bandwidth_hz),
            target_speech_seconds=read("AUTOYOU_VOICE_CLONE_TARGET_SECONDS", base.target_speech_seconds),
            max_reference_seconds=read("AUTOYOU_VOICE_CLONE_MAX_REFERENCE_SECONDS", base.max_reference_seconds),
            min_speech_seconds=read("AUTOYOU_VOICE_CLONE_MIN_SPEECH_SECONDS", base.min_speech_seconds),
        )


@dataclass(frozen=True)
class ReferenceCandidate:
    sample_id: str
    audio_path: Path
    source: str
    user_id: str = ""
    sample_rate: int = 0
    duration_seconds: float = 0.0
    speech_seconds: float = 0.0
    speech_ratio: float = 0.0
    rms_dbfs: float = -240.0
    peak_dbfs: float = -240.0
    clipped_ratio: float = 0.0
    bandwidth_hz: float = 0.0
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def curated(self) -> bool:
        return self.source.lower() in CURATED_SOURCES

    def summary(self) -> Dict[str, Any]:
        """What metadata keeps about a clip: measurements, never transcript text or the user id."""
        data = asdict(self)
        data.pop("audio_path", None)
        data.pop("user_id", None)
        data["reasons"] = list(self.reasons)
        for key in ("duration_seconds", "speech_seconds", "speech_ratio", "rms_dbfs", "peak_dbfs"):
            data[key] = round(float(data[key]), 3)
        data["clipped_ratio"] = round(float(data["clipped_ratio"]), 6)
        data["bandwidth_hz"] = round(float(data["bandwidth_hz"]))
        return data


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(max(float(value), 1e-12))


def load_wav_mono(path: str | Path) -> Tuple[np.ndarray, int]:
    """Read a WAV file as mono float32 in [-1, 1]."""
    try:
        import soundfile as sf

        audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
        return np.asarray(audio.mean(axis=1), dtype=np.float32), int(sample_rate)
    except ImportError:
        pass
    with wave.open(str(path), "rb") as wf:
        channels = int(wf.getnchannels() or 1)
        width = int(wf.getsampwidth() or 0)
        sample_rate = int(wf.getframerate() or 16000)
        raw = wf.readframes(wf.getnframes())
    if width == 2:
        audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        audio = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {width} bytes")
    if channels > 1:
        audio = audio[: (audio.size // channels) * channels].reshape(-1, channels).mean(axis=1)
    return audio, sample_rate


def _frame_rms(audio: np.ndarray, sample_rate: int) -> Tuple[np.ndarray, int]:
    frame = max(1, int(round(sample_rate * FRAME_SECONDS)))
    count = audio.size // frame
    if count <= 0:
        return np.zeros(0, dtype=np.float32), frame
    frames = audio[: count * frame].reshape(count, frame)
    return np.sqrt(np.mean(frames * frames, axis=1)), frame


def speech_mask(audio: np.ndarray, sample_rate: int) -> Tuple[np.ndarray, int]:
    rms, frame = _frame_rms(audio, sample_rate)
    return rms >= 10.0 ** (SPEECH_FRAME_DBFS / 20.0), frame


def bandwidth_hz(audio: np.ndarray, sample_rate: int, *, energy: float = 0.99) -> float:
    """Frequency below which ``energy`` of the speech energy lies (above 50 Hz)."""
    mask, frame = speech_mask(audio, sample_rate)
    if mask.any():
        voiced = audio[: mask.size * frame].reshape(mask.size, frame)[mask].reshape(-1)
    else:
        voiced = audio
    n_fft = 1024 if sample_rate <= 24000 else 2048
    if voiced.size < n_fft:
        return 0.0
    hop = n_fft // 2
    window = np.hanning(n_fft).astype(np.float32)
    starts = range(0, voiced.size - n_fft + 1, hop)
    power = np.zeros(n_fft // 2 + 1, dtype=np.float64)
    for start in starts:
        spectrum = np.fft.rfft(voiced[start : start + n_fft] * window)
        power += (spectrum.real**2 + spectrum.imag**2)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    power[freqs < 50.0] = 0.0
    total = float(power.sum())
    if total <= 0.0:
        return 0.0
    cumulative = np.cumsum(power) / total
    index = int(np.searchsorted(cumulative, energy))
    return float(freqs[min(index, freqs.size - 1)])


def measure_clip(
    audio: np.ndarray,
    sample_rate: int,
    *,
    sample_id: str,
    audio_path: Path,
    source: str,
    user_id: str = "",
    thresholds: Optional[ReferenceThresholds] = None,
) -> ReferenceCandidate:
    limits = thresholds or ReferenceThresholds()
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    duration = float(audio.size) / float(sample_rate) if sample_rate else 0.0
    if audio.size == 0:
        return ReferenceCandidate(
            sample_id=sample_id,
            audio_path=audio_path,
            source=source,
            user_id=user_id,
            sample_rate=sample_rate,
            reasons=("empty",),
        )
    mask, frame = speech_mask(audio, sample_rate)
    speech_ratio = float(mask.mean()) if mask.size else 0.0
    speech_seconds = float(mask.sum()) * frame / float(sample_rate)
    magnitude = np.abs(audio)
    rms_dbfs = _dbfs(float(np.sqrt(np.mean(audio * audio))))
    peak_dbfs = _dbfs(float(magnitude.max()))
    clipped_ratio = float(np.mean(magnitude >= 0.98))
    band = bandwidth_hz(audio, sample_rate)

    reasons: List[str] = []
    if duration < limits.min_clip_seconds:
        reasons.append("too_short")
    if limits.max_clip_seconds > 0 and duration > limits.max_clip_seconds:
        reasons.append("too_long")
    if speech_ratio < limits.min_speech_ratio:
        reasons.append("too_much_silence")
    if rms_dbfs < limits.min_rms_dbfs:
        reasons.append("too_quiet")
    if clipped_ratio > limits.max_clipped_ratio:
        reasons.append("clipped")
    if speech_seconds > 0 and band < limits.min_bandwidth_hz:
        reasons.append("muffled")
    return ReferenceCandidate(
        sample_id=sample_id,
        audio_path=audio_path,
        source=source,
        user_id=user_id,
        sample_rate=int(sample_rate),
        duration_seconds=duration,
        speech_seconds=speech_seconds,
        speech_ratio=speech_ratio,
        rms_dbfs=rms_dbfs,
        peak_dbfs=peak_dbfs,
        clipped_ratio=clipped_ratio,
        bandwidth_hz=band,
        reasons=tuple(reasons),
    )


def _quality_score(candidate: ReferenceCandidate) -> float:
    bandwidth = min(candidate.bandwidth_hz, 8000.0) / 1000.0
    loudness = -abs(candidate.rms_dbfs + 20.0) / 10.0
    return bandwidth + 2.0 * candidate.speech_ratio + loudness + min(candidate.speech_seconds, 10.0) / 10.0


def select_reference_clips(
    candidates: Sequence[ReferenceCandidate],
    *,
    pinned_ids: Optional[Iterable[str]] = None,
    thresholds: Optional[ReferenceThresholds] = None,
) -> Tuple[List[ReferenceCandidate], Dict[str, Any]]:
    """Pick clips for the reference, best first, and explain the choice.

    Pinned clips (chosen by the owner) are used in the given order even when a
    check fails; the failures are reported as warnings.
    """
    limits = thresholds or ReferenceThresholds()
    rejected: Dict[str, int] = {}
    for candidate in candidates:
        for reason in candidate.reasons:
            rejected[reason] = rejected.get(reason, 0) + 1
    eligible = [candidate for candidate in candidates if not candidate.reasons]
    warnings: List[str] = []

    pinned = [str(item).strip() for item in (pinned_ids or []) if str(item).strip()]
    selected: List[ReferenceCandidate] = []
    if pinned:
        by_id = {candidate.sample_id: candidate for candidate in candidates}
        missing = [item for item in pinned if item not in by_id]
        if missing:
            return [], _report(candidates, eligible, rejected, [], warnings, limits,
                               problem="pinned_clips_not_found", missing=missing)
        selected = [by_id[item] for item in dict.fromkeys(pinned)]
        for candidate in selected:
            if candidate.reasons:
                warnings.append(f"pinned_clip_{candidate.sample_id}_{'_'.join(candidate.reasons)}")
    else:
        ranked = sorted(eligible, key=lambda item: (item.curated, _quality_score(item)), reverse=True)
        speech_total = 0.0
        duration_total = 0.0
        for candidate in ranked:
            if speech_total >= limits.target_speech_seconds:
                break
            if selected and duration_total + candidate.duration_seconds > limits.max_reference_seconds + 2.0:
                continue
            selected.append(candidate)
            speech_total += candidate.speech_seconds
            duration_total += candidate.duration_seconds

    speech_total = sum(candidate.speech_seconds for candidate in selected)
    if speech_total < limits.min_speech_seconds:
        return [], _report(candidates, eligible, rejected, selected, warnings, limits, problem="not_enough_clean_speech")
    callers = {candidate.user_id for candidate in selected if candidate.user_id}
    if len(callers) > 1:
        warnings.append("clips_from_more_than_one_caller")
    if speech_total < limits.target_speech_seconds:
        warnings.append("short_reference")
    if selected and max(candidate.bandwidth_hz for candidate in selected) < 6000.0:
        warnings.append("phone_quality_reference")
    return selected, _report(candidates, eligible, rejected, selected, warnings, limits)


def _report(
    candidates: Sequence[ReferenceCandidate],
    eligible: Sequence[ReferenceCandidate],
    rejected: Dict[str, int],
    selected: Sequence[ReferenceCandidate],
    warnings: List[str],
    limits: ReferenceThresholds,
    *,
    problem: str = "",
    missing: Optional[List[str]] = None,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "ok": not problem,
        "problem": problem,
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "eligible_speech_seconds": round(sum(item.speech_seconds for item in eligible), 2),
        "rejected": dict(sorted(rejected.items())),
        "selected": [item.sample_id for item in selected],
        "selected_speech_seconds": round(sum(item.speech_seconds for item in selected), 2),
        "caller_count": len({item.user_id for item in selected if item.user_id}),
        "warnings": list(warnings),
        "thresholds": asdict(limits),
    }
    if missing:
        report["missing"] = list(missing)
    return report


def describe_selection_problem(report: Dict[str, Any]) -> str:
    problem = str(report.get("problem") or "")
    if problem == "pinned_clips_not_found":
        return "These clip IDs were not found in the voice dataset: " + ", ".join(report.get("missing") or []) + "."
    if problem == "not_enough_clean_speech":
        limits = report.get("thresholds") or {}
        rejected = report.get("rejected") or {}
        detail = ", ".join(f"{count} {reason.replace('_', ' ')}" for reason, count in rejected.items())
        return (
            f"Not enough clear speech to clone from: {report.get('eligible_speech_seconds', 0)}s usable, "
            f"{limits.get('min_speech_seconds', 5)}s needed. "
            + (f"Clips set aside: {detail}. " if detail else "")
            + "Upload a 10-20 second WAV of you reading aloud in a quiet room."
        )
    return problem.replace("_", " ")


def _trim_silence(audio: np.ndarray, sample_rate: int, *, margin_seconds: float = 0.1) -> np.ndarray:
    mask, frame = speech_mask(audio, sample_rate)
    if not mask.any():
        return audio
    voiced = np.flatnonzero(mask)
    margin = int(margin_seconds * sample_rate)
    start = max(0, int(voiced[0]) * frame - margin)
    end = min(audio.size, (int(voiced[-1]) + 1) * frame + margin)
    return audio[start:end]


def resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or audio.size == 0:
        return np.asarray(audio, dtype=np.float32)
    try:
        from scipy.signal import resample_poly

        divisor = math.gcd(int(source_rate), int(target_rate))
        return np.asarray(
            resample_poly(audio, int(target_rate) // divisor, int(source_rate) // divisor), dtype=np.float32
        )
    except ImportError:
        duration = audio.size / float(source_rate)
        target_size = max(1, int(round(duration * target_rate)))
        source_times = np.arange(audio.size) / float(source_rate)
        target_times = np.arange(target_size) / float(target_rate)
        return np.interp(target_times, source_times, audio).astype(np.float32)


def build_reference_audio(
    clips: Sequence[Tuple[np.ndarray, int]],
    *,
    sample_rate: int = REFERENCE_SAMPLE_RATE,
    gap_seconds: float = 0.25,
    max_seconds: float = 15.0,
) -> np.ndarray:
    """Trim, level and join clips (best first) into one mono reference."""
    gap = np.zeros(int(gap_seconds * sample_rate), dtype=np.float32)
    parts: List[np.ndarray] = []
    for audio, rate in clips:
        trimmed = _trim_silence(np.asarray(audio, dtype=np.float32).reshape(-1), int(rate))
        converted = resample(trimmed, int(rate), sample_rate)
        peak = float(np.abs(converted).max()) if converted.size else 0.0
        if peak > 0:
            converted = converted / peak * 0.9
        if parts:
            parts.append(gap)
        parts.append(converted.astype(np.float32))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    joined = np.concatenate(parts)
    limit = int(max_seconds * sample_rate)
    return joined[:limit] if limit > 0 else joined


def wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm.tobytes())
    return buffer.getvalue()
