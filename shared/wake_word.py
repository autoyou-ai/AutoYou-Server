# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Personal wake word: a few recordings of the owner's word, matched by DTW.

AutoYou does not ship a pretrained keyword model. The usual open options either
need a vendor access key (Porcupine) or carry non-commercial model licences
(openWakeWord's published models), and both were deliberately kept out of the
release (see ``.llm/flows/voice.md``). This module instead builds a model from
the owner's own recordings, which is the classic template approach used by
personal keyword spotters: every recording becomes a template of MFCC frames,
and live audio matches when a slope-constrained dynamic time warping distance
to any template falls under a threshold calibrated from those same recordings.

The algorithm is deliberately small and fully specified, because every client
runs it too (Chrome and the admin page in JavaScript, Android in Kotlin, Apple
platforms in Swift, desktop runtimes in Python). ``tests/fixtures/wake_word``
holds shared vectors so each port can prove it computes the same numbers.

Feature recipe (version 1), applied to mono 16 kHz PCM:

* pre-emphasis ``y[n] = x[n] - 0.97 x[n-1]`` over the analysed segment
* 25 ms frames (400 samples), 10 ms hop (160), Hamming window
* 512-point power spectrum ``|FFT|^2 / 512``
* 26 triangular HTK-mel filters between 60 Hz and 7600 Hz, weights evaluated at
  each FFT bin's centre frequency
* natural log with a 1e-10 floor, orthonormal DCT-II, coefficients 1..12
  (c0 is dropped so loudness does not matter)
* cepstral mean normalisation over the segment

Distance: cosine distance between frames, Sakoe-Chiba symmetric DTW with slope
constraint P=1 (steps (1,1), (1,2), (2,1)), normalised by ``n + m``.

Nothing here touches the network or the disk, and nothing raises on bad audio:
it returns empty features and no match instead, because callers sit on live
audio paths.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import difflib
import io
import math
import re
import time
import uuid
import wave
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

MODEL_FORMAT = "autoyou-wake-word"
MODEL_VERSION = 1

SAMPLE_RATE = 16_000
FRAME_LENGTH = 400
HOP_LENGTH = 160
N_FFT = 512
N_MELS = 26
N_MFCC = 12
PRE_EMPHASIS = 0.97
MEL_LOW_HZ = 60.0
MEL_HIGH_HZ = 7600.0
LOG_FLOOR = 1e-10
COSINE_EPSILON = 1e-9

#: Enrollment bounds. A wake word is a short phrase; anything outside these
#: bounds is almost certainly a mis-capture (silence, a cough, a sentence).
MIN_SAMPLE_SECONDS = 0.25
MAX_SAMPLE_SECONDS = 2.5
MIN_SAMPLE_RMS_DBFS = -42.0
MIN_TEMPLATES = 3
MAX_TEMPLATES = 8

#: Threshold limits keep a bad enrollment from producing a model that fires on
#: everything (too loose) or never (too tight).
MIN_DISTANCE_THRESHOLD = 0.06
MAX_DISTANCE_THRESHOLD = 0.32
NO_NEGATIVES_THRESHOLD_FLOOR = 0.12
NO_NEGATIVES_THRESHOLD_CAP = 0.22
DEFAULT_SENSITIVITY = 0.5

#: Live detection: evaluate every N frames while there is speech in the window.
EVALUATION_HOP_FRAMES = 5
SPEECH_FRAME_DBFS = -48.0
MIN_SPEECH_FRAME_RATIO = 0.3
REFRACTORY_SECONDS = 2.0

_PCM_SCALE = 32768.0


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def pcm16_to_float(pcm: bytes | bytearray | memoryview | np.ndarray) -> np.ndarray:
    """Mono s16le bytes (or an int16 array) as float64 samples in [-1, 1)."""
    if isinstance(pcm, np.ndarray):
        data = pcm.astype(np.float64)
        if pcm.dtype == np.int16:
            data = data / _PCM_SCALE
        return data
    raw = bytes(pcm or b"")
    usable = len(raw) - (len(raw) % 2)
    if usable <= 0:
        return np.zeros(0, dtype=np.float64)
    return np.frombuffer(raw[:usable], dtype="<i2").astype(np.float64) / _PCM_SCALE


def float_to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 32767.0 / _PCM_SCALE)
    return (clipped * _PCM_SCALE).astype("<i2").tobytes()


def wav_bytes_to_pcm16(payload: bytes) -> bytes:
    """Decode a WAV file to mono 16 kHz s16 PCM.

    Accepts 8/16/32-bit integer PCM at any rate and channel count; stereo is
    averaged and other rates are linearly resampled. Raises ``ValueError`` for
    anything that is not a readable WAV.
    """
    try:
        with wave.open(io.BytesIO(payload), "rb") as reader:
            channels = max(1, reader.getnchannels())
            width = reader.getsampwidth()
            rate = reader.getframerate()
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"Not a readable WAV file: {exc}") from exc
    if width == 1:
        data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float64) / _PCM_SCALE
    elif width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {width} bytes")
    if channels > 1:
        usable = len(data) - (len(data) % channels)
        data = data[:usable].reshape(-1, channels).mean(axis=1)
    data = resample_linear(data, rate, SAMPLE_RATE)
    return float_to_pcm16(data)


def pcm16_to_wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(int(sample_rate))
        writer.writeframes(bytes(pcm or b""))
    return buffer.getvalue()


def resample_linear(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float64)
    if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate or data.size < 2:
        return data
    duration = data.size / float(source_rate)
    target_size = max(1, int(round(duration * target_rate)))
    source_positions = np.arange(data.size, dtype=np.float64) / float(source_rate)
    target_positions = np.arange(target_size, dtype=np.float64) / float(target_rate)
    return np.interp(target_positions, source_positions, data)


def rms_dbfs(samples: np.ndarray) -> float:
    data = np.asarray(samples, dtype=np.float64)
    if data.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(data))))
    return 20.0 * math.log10(max(rms, 1e-6))


def frame_dbfs(samples: np.ndarray, frame_length: int = FRAME_LENGTH, hop_length: int = HOP_LENGTH) -> np.ndarray:
    frames = _frame(np.asarray(samples, dtype=np.float64), frame_length, hop_length)
    if frames.size == 0:
        return np.zeros(0, dtype=np.float64)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-6))


def trim_silence(samples: np.ndarray, *, margin_seconds: float = 0.05) -> np.ndarray:
    """Cut leading/trailing room noise around a recorded word.

    The cut level follows the recording's own background (its 10th-percentile
    frame level plus 10 dB), stays at least 30 dB under the peak so quiet
    consonants survive, never goes above peak-12 dB, and never below -50 dBFS.
    """
    data = np.asarray(samples, dtype=np.float64)
    levels = frame_dbfs(data)
    if levels.size == 0:
        return data
    peak = float(levels.max())
    background = float(np.percentile(levels, 10))
    floor = max(-50.0, min(peak - 12.0, max(peak - 30.0, background + 10.0)))
    voiced = np.flatnonzero(levels >= floor)
    if voiced.size == 0:
        return data[:0]
    margin = int(round(margin_seconds * SAMPLE_RATE))
    start = max(0, int(voiced[0]) * HOP_LENGTH - margin)
    end = min(data.size, int(voiced[-1]) * HOP_LENGTH + FRAME_LENGTH + margin)
    return data[start:end]


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


def _hz_to_mel(hz: np.ndarray | float) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + np.asarray(hz, dtype=np.float64) / 700.0)


def _mel_to_hz(mel: np.ndarray | float) -> np.ndarray:
    return 700.0 * (np.power(10.0, np.asarray(mel, dtype=np.float64) / 2595.0) - 1.0)


def mel_filterbank() -> np.ndarray:
    """``(N_MELS, N_FFT//2 + 1)`` triangular weights at bin centre frequencies."""
    edges = _mel_to_hz(np.linspace(_hz_to_mel(MEL_LOW_HZ), _hz_to_mel(MEL_HIGH_HZ), N_MELS + 2))
    bin_hz = np.arange(N_FFT // 2 + 1, dtype=np.float64) * SAMPLE_RATE / N_FFT
    bank = np.zeros((N_MELS, bin_hz.size), dtype=np.float64)
    for index in range(N_MELS):
        left, centre, right = edges[index], edges[index + 1], edges[index + 2]
        rising = (bin_hz - left) / (centre - left)
        falling = (right - bin_hz) / (right - centre)
        bank[index] = np.maximum(0.0, np.minimum(rising, falling))
    return bank


def dct_matrix() -> np.ndarray:
    """Rows 1..N_MFCC of the orthonormal DCT-II over N_MELS inputs."""
    n = np.arange(N_MELS, dtype=np.float64)
    rows = []
    for k in range(1, N_MFCC + 1):
        rows.append(math.sqrt(2.0 / N_MELS) * np.cos(math.pi * k * (2.0 * n + 1.0) / (2.0 * N_MELS)))
    return np.vstack(rows)


_HAMMING = 0.54 - 0.46 * np.cos(2.0 * math.pi * np.arange(FRAME_LENGTH, dtype=np.float64) / (FRAME_LENGTH - 1))
_MEL_BANK = mel_filterbank()
_DCT = dct_matrix()


def _frame(samples: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    if samples.size < frame_length:
        return np.zeros((0, frame_length), dtype=np.float64)
    count = 1 + (samples.size - frame_length) // hop_length
    index = np.arange(frame_length)[None, :] + hop_length * np.arange(count)[:, None]
    return samples[index]


def mfcc(samples: np.ndarray, *, normalize: bool = True) -> np.ndarray:
    """MFCC frames ``(frames, N_MFCC)`` for float samples at 16 kHz."""
    data = np.asarray(samples, dtype=np.float64)
    if data.size < FRAME_LENGTH:
        return np.zeros((0, N_MFCC), dtype=np.float64)
    emphasized = np.empty_like(data)
    emphasized[0] = data[0]
    emphasized[1:] = data[1:] - PRE_EMPHASIS * data[:-1]
    frames = _frame(emphasized, FRAME_LENGTH, HOP_LENGTH) * _HAMMING
    spectrum = np.abs(np.fft.rfft(frames, n=N_FFT, axis=1)) ** 2 / N_FFT
    energies = spectrum @ _MEL_BANK.T
    cepstra = np.log(np.maximum(energies, LOG_FLOOR)) @ _DCT.T
    if normalize and cepstra.shape[0]:
        cepstra = cepstra - cepstra.mean(axis=0, keepdims=True)
    return cepstra


def cosine_cost_matrix(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    a = np.asarray(first, dtype=np.float64)
    b = np.asarray(second, dtype=np.float64)
    a_norm = np.sqrt(np.sum(a * a, axis=1)) + COSINE_EPSILON
    b_norm = np.sqrt(np.sum(b * b, axis=1)) + COSINE_EPSILON
    similarity = (a @ b.T) / (a_norm[:, None] * b_norm[None, :])
    return 1.0 - similarity


def dtw_distance(first: np.ndarray, second: np.ndarray) -> float:
    """Normalised symmetric P=1 DTW distance; ``inf`` when no legal path exists."""
    return dtw_distances([first], [second])[0]


def dtw_distances(
    firsts: Sequence[np.ndarray],
    seconds: Sequence[np.ndarray],
    *,
    abandon_above: Optional[float] = None,
    open_start: int = 0,
    open_end: int = 0,
) -> List[float]:
    """DTW distance for each ``(firsts[k], seconds[k])`` pair, computed together.

    ``firsts`` are templates (rows), ``seconds`` the audio being searched
    (columns). With ``open_start``/``open_end`` the path may begin in any of the
    first ``open_start + 1`` columns and finish in any of the last
    ``open_end + 1``, so a word need not fill the window exactly; the distance
    is then the path's cost over its own length ``n + (j_end - j_start + 1)``.
    With both at zero this is the ordinary closed DTW normalised by ``n + m``.

    Pairs are stacked into one padded array so a model with several templates
    costs about the same as one: the recurrence only reads cells up and to the
    left, so padding never reaches a pair's own cells. ``abandon_above`` (a
    normalised distance) stops early once every pair is certainly beyond it;
    those pairs report ``inf``. That is for live detection only; the shared
    test vectors are computed without it.
    """
    count = len(firsts)
    results = [math.inf] * count
    pairs = []
    for index in range(count):
        a = np.asarray(firsts[index], dtype=np.float64)
        b = np.asarray(seconds[index], dtype=np.float64)
        n, m = a.shape[0], b.shape[0]
        if n == 0 or m == 0 or n > 2 * m + 1 or m > 2 * n + 1 + max(0, open_start) + max(0, open_end):
            continue
        pairs.append((index, n, m, cosine_cost_matrix(a, b)))
    if not pairs:
        return results
    open_start = max(0, int(open_start))
    open_end = max(0, int(open_end))
    inf = math.inf
    k = len(pairs)
    n_max = max(item[1] for item in pairs)
    m_max = max(item[2] for item in pairs)
    cost = np.full((k, n_max, m_max), inf, dtype=np.float64)
    for slot, (_index, n, m, matrix) in enumerate(pairs):
        cost[slot, :n, :m] = matrix
    # Padded accumulated cost: column j of the problem lives at j + 2, and the
    # two leading columns stay inf so the (i-1, j-2) step needs no bounds test.
    # ``origin`` carries each path's first column, for the open-start length.
    total = np.full((k, n_max, m_max + 2), inf, dtype=np.float64)
    origin = np.zeros((k, n_max, m_max + 2), dtype=np.int64)
    first_columns = min(open_start + 1, m_max)
    total[:, 0, 2 : 2 + first_columns] = 2.0 * cost[:, 0, :first_columns]
    origin[:, 0, 2 : 2 + first_columns] = np.arange(first_columns)[None, :]
    diagonal_step = 2.0 * cost
    shifted = np.full_like(cost, inf)
    shifted[:, :, 1:] = cost[:, :, :-1]
    horizontal_step = 2.0 * shifted + cost
    vertical_step = np.full_like(cost, inf)
    vertical_step[:, 1:, :] = 2.0 * cost[:, :-1, :] + cost[:, 1:, :]
    limits = None
    rows_needed = np.array([n for (_i, n, _m, _c) in pairs])
    if abandon_above is not None and math.isfinite(abandon_above):
        limits = np.array([abandon_above * (n + m) for (_i, n, m, _c) in pairs], dtype=np.float64)
    scratch = np.empty((k, m_max), dtype=np.float64)
    last_row = n_max - 1
    for i in range(1, n_max):
        row = total[:, i, 2:]
        row_origin = origin[:, i, 2:]
        np.add(total[:, i - 1, 1 : m_max + 1], diagonal_step[:, i, :], out=row)
        row_origin[...] = origin[:, i - 1, 1 : m_max + 1]
        np.add(total[:, i - 1, 0:m_max], horizontal_step[:, i, :], out=scratch)
        better = scratch < row
        row[better] = scratch[better]
        row_origin[better] = origin[:, i - 1, 0:m_max][better]
        if i >= 2:
            np.add(total[:, i - 2, 1 : m_max + 1], vertical_step[:, i, :], out=scratch)
            better = scratch < row
            row[better] = scratch[better]
            row_origin[better] = origin[:, i - 2, 1 : m_max + 1][better]
        if limits is not None and (i & 7) == 0:
            # Partial costs only grow, so a pair still being computed whose best
            # cell already exceeds its limit cannot finish under it. Pairs whose
            # last row is behind us are decided by their own final cells.
            active = rows_needed > i
            hopeless_active = np.min(row, axis=1) > limits
            if bool(np.all(hopeless_active[active])):
                if not bool(np.any(~active)):
                    return results
                last_row = i
                break
    for slot, (index, n, m, _matrix) in enumerate(pairs):
        if n - 1 > last_row:
            continue
        best = math.inf
        for j_end in range(max(0, m - 1 - open_end), m):
            final = float(total[slot, n - 1, j_end + 2])
            if not math.isfinite(final):
                continue
            j_start = int(origin[slot, n - 1, j_end + 2])
            distance = final / (n + (j_end - j_start + 1))
            if distance < best:
                best = distance
        if math.isfinite(best) and (abandon_above is None or best <= abandon_above):
            results[index] = best
    return results


#: Live audio is searched with this many frames of slack on each side of a
#: template-length window, so a word need not line up with the evaluation grid
#: or be separated from the words around it by a pause.
WINDOW_SLACK_FRAMES = 6


def window_for(raw_features: np.ndarray, template_frames: int) -> np.ndarray:
    return trailing_window(raw_features, int(template_frames) + 2 * WINDOW_SLACK_FRAMES)


def match_distances(
    templates: Sequence[np.ndarray],
    raw_features: np.ndarray,
    *,
    abandon_above: Optional[float] = None,
) -> List[float]:
    """Distance from each template to the newest audio (un-normalised MFCC).

    This is the one comparison live detection, clip testing and calibration all
    use, and the one every client port reproduces.
    """
    return dtw_distances(
        list(templates),
        [window_for(raw_features, template.shape[0]) for template in templates],
        abandon_above=abandon_above,
        open_start=2 * WINDOW_SLACK_FRAMES,
        open_end=2 * WINDOW_SLACK_FRAMES,
    )


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass
class WakeWordTemplate:
    id: str
    features: np.ndarray
    duration_seconds: float
    source: str = "recording"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "frames": int(self.features.shape[0]),
            "duration_seconds": round(float(self.duration_seconds), 3),
            "mfcc": [[round(float(value), 5) for value in frame] for frame in self.features],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WakeWordTemplate":
        features = np.asarray(payload.get("mfcc") or [], dtype=np.float64)
        if features.ndim != 2 or features.shape[1] != N_MFCC:
            raise ValueError("Template features have the wrong shape")
        return cls(
            id=str(payload.get("id") or uuid.uuid4().hex[:12]),
            features=features,
            duration_seconds=float(payload.get("duration_seconds") or features.shape[0] * HOP_LENGTH / SAMPLE_RATE),
            source=str(payload.get("source") or "recording"),
        )


@dataclass
class WakeWordModel:
    phrase: str
    templates: List[WakeWordTemplate]
    base_threshold: float
    sensitivity: float = DEFAULT_SENSITIVITY
    created_at: float = field(default_factory=time.time)
    calibration: Dict[str, Any] = field(default_factory=dict)

    @property
    def distance_threshold(self) -> float:
        return threshold_for_sensitivity(self.base_threshold, self.sensitivity)

    @property
    def max_template_frames(self) -> int:
        return max((t.features.shape[0] for t in self.templates), default=0)

    def score(self, distance: float) -> float:
        """0..1, exactly 0.5 at the threshold, 1.0 for a perfect match."""
        threshold = self.distance_threshold
        if not math.isfinite(distance) or threshold <= 0:
            return 0.0
        return float(max(0.0, min(1.0, 1.0 - distance / (2.0 * threshold))))

    def best_distance(self, features: np.ndarray) -> Tuple[float, Optional[str]]:
        best = math.inf
        best_id: Optional[str] = None
        for template in self.templates:
            distance = dtw_distance(template.features, features)
            if distance < best:
                best = distance
                best_id = template.id
        return best, best_id

    def to_dict(self, *, include_templates: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "format": MODEL_FORMAT,
            "version": MODEL_VERSION,
            "phrase": self.phrase,
            "sample_rate": SAMPLE_RATE,
            "frame_length": FRAME_LENGTH,
            "hop_length": HOP_LENGTH,
            "n_fft": N_FFT,
            "n_mels": N_MELS,
            "n_mfcc": N_MFCC,
            "pre_emphasis": PRE_EMPHASIS,
            "mel_low_hz": MEL_LOW_HZ,
            "mel_high_hz": MEL_HIGH_HZ,
            "base_threshold": round(float(self.base_threshold), 5),
            "sensitivity": round(float(self.sensitivity), 3),
            "distance_threshold": round(float(self.distance_threshold), 5),
            "evaluation_hop_frames": EVALUATION_HOP_FRAMES,
            "speech_frame_dbfs": SPEECH_FRAME_DBFS,
            "min_speech_frame_ratio": MIN_SPEECH_FRAME_RATIO,
            "refractory_seconds": REFRACTORY_SECONDS,
            "template_count": len(self.templates),
            "created_at": float(self.created_at),
            "calibration": dict(self.calibration),
        }
        if include_templates:
            payload["templates"] = [template.to_dict() for template in self.templates]
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WakeWordModel":
        if str(payload.get("format") or "") != MODEL_FORMAT:
            raise ValueError("Not an AutoYou wake word model")
        if int(payload.get("version") or 0) != MODEL_VERSION:
            raise ValueError("Unsupported wake word model version")
        templates = [WakeWordTemplate.from_dict(item) for item in payload.get("templates") or []]
        if not templates:
            raise ValueError("Wake word model has no templates")
        return cls(
            phrase=str(payload.get("phrase") or ""),
            templates=templates,
            base_threshold=float(payload.get("base_threshold") or MAX_DISTANCE_THRESHOLD / 2),
            sensitivity=normalize_sensitivity(payload.get("sensitivity")),
            created_at=float(payload.get("created_at") or time.time()),
            calibration=dict(payload.get("calibration") or {}),
        )


def normalize_sensitivity(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_SENSITIVITY
    if not math.isfinite(number):
        return DEFAULT_SENSITIVITY
    return max(0.0, min(1.0, number))


def threshold_for_sensitivity(base_threshold: float, sensitivity: float) -> float:
    scaled = float(base_threshold) * (0.8 + 0.4 * normalize_sensitivity(sensitivity))
    return max(MIN_DISTANCE_THRESHOLD, min(MAX_DISTANCE_THRESHOLD, scaled))


@dataclass
class SampleCheck:
    ok: bool
    duration_seconds: float
    rms_dbfs: float
    problems: List[str]
    samples: np.ndarray

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "duration_seconds": round(self.duration_seconds, 3),
            "rms_dbfs": round(self.rms_dbfs, 1),
            "problems": list(self.problems),
        }


def check_sample(pcm: bytes | np.ndarray) -> SampleCheck:
    """Trim one recording of the wake word and say whether it is usable."""
    trimmed = trim_silence(pcm16_to_float(pcm))
    duration = trimmed.size / float(SAMPLE_RATE)
    level = rms_dbfs(trimmed)
    problems: List[str] = []
    if duration < MIN_SAMPLE_SECONDS:
        problems.append("too_short")
    if duration > MAX_SAMPLE_SECONDS:
        problems.append("too_long")
    if level < MIN_SAMPLE_RMS_DBFS:
        problems.append("too_quiet")
    if trimmed.size and float(np.max(np.abs(trimmed))) >= 0.999:
        problems.append("clipped")
    usable = not {"too_short", "too_long", "too_quiet"} & set(problems)
    return SampleCheck(ok=usable, duration_seconds=duration, rms_dbfs=level, problems=problems, samples=trimmed)


def _sliding_windows(features: np.ndarray, width: int, step: int) -> Iterable[np.ndarray]:
    if features.shape[0] < width or width <= 0:
        if features.shape[0]:
            yield features
        return
    for start in range(0, features.shape[0] - width + 1, max(1, step)):
        yield features[start : start + width]


def _segment_mfcc(samples: np.ndarray) -> np.ndarray:
    """Template features: the whole trimmed recording, mean-normalised."""
    return mfcc(samples, normalize=True)


def trailing_window(raw_features: np.ndarray, frames: int) -> np.ndarray:
    """The last ``frames`` un-normalised MFCC frames, mean-normalised.

    Live audio is compared against each template over a window of exactly that
    template's length ending at the newest frame. Every port does this the same
    way, which is what the shared vectors pin.
    """
    window = np.asarray(raw_features, dtype=np.float64)[-max(1, int(frames)):]
    if window.shape[0] == 0:
        return window
    return window - window.mean(axis=0, keepdims=True)


def build_model(
    phrase: str,
    recordings: Sequence[bytes | np.ndarray],
    *,
    negatives: Sequence[bytes | np.ndarray] = (),
    sensitivity: float = DEFAULT_SENSITIVITY,
    sources: Optional[Sequence[str]] = None,
) -> Tuple[WakeWordModel, Dict[str, Any]]:
    """Turn recordings of the wake word into a calibrated model.

    ``negatives`` are recordings known *not* to contain the phrase (ordinary
    speech from the Voice Training library, background noise). They tighten
    the threshold so the model rejects them. Raises ``ValueError`` with a
    readable message when there are not enough usable recordings.
    """
    usable: List[Tuple[np.ndarray, float, str]] = []
    rejected: List[Dict[str, Any]] = []
    for index, recording in enumerate(recordings):
        check = check_sample(recording)
        source = (sources[index] if sources and index < len(sources) else "recording") or "recording"
        if not check.ok:
            rejected.append({"index": index, **check.to_dict()})
            continue
        features = _segment_mfcc(check.samples)
        if features.shape[0] < 8:
            rejected.append({"index": index, **check.to_dict(), "problems": ["too_short"]})
            continue
        usable.append((features, check.duration_seconds, source))
    if len(usable) < MIN_TEMPLATES:
        raise ValueError(
            f"Need at least {MIN_TEMPLATES} clear recordings of the wake word; {len(usable)} were usable."
        )
    usable = usable[:MAX_TEMPLATES]

    pairwise: List[float] = []
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            distance = dtw_distance(usable[i][0], usable[j][0])
            if math.isfinite(distance):
                pairwise.append(distance)

    # Drop a recording that disagrees with all the others (a mis-capture), as
    # long as enough remain.
    if len(usable) > MIN_TEMPLATES:
        averages = []
        for i in range(len(usable)):
            distances = [dtw_distance(usable[i][0], usable[j][0]) for j in range(len(usable)) if j != i]
            finite = [d for d in distances if math.isfinite(d)]
            averages.append(sum(finite) / len(finite) if finite else math.inf)
        worst = int(np.argmax(averages))
        median = float(np.median([a for a in averages if math.isfinite(a)] or [0.0]))
        if math.isfinite(averages[worst]) and averages[worst] > 1.6 * median:
            rejected.append({"index": worst, "problems": ["inconsistent"]})
            usable.pop(worst)
            pairwise = [
                dtw_distance(usable[i][0], usable[j][0])
                for i in range(len(usable))
                for j in range(i + 1, len(usable))
            ]
            pairwise = [d for d in pairwise if math.isfinite(d)]

    if not pairwise:
        raise ValueError("The recordings are too different in length to compare; record the word the same way each time.")
    positive_high = float(np.percentile(pairwise, 90))
    positive_mean = float(np.mean(pairwise))
    # Without other speech to compare against, allow generous room above how
    # much the owner's own recordings differ, but stay under the distance at
    # which unrelated speech starts (about 0.27-0.30 in measurements).
    threshold = min(NO_NEGATIVES_THRESHOLD_CAP, max(NO_NEGATIVES_THRESHOLD_FLOOR, positive_high * 1.8))

    negative_low = math.inf
    templates_features = [item[0] for item in usable]
    for negative in negatives:
        samples = pcm16_to_float(negative)
        features = mfcc(samples, normalize=False) if samples.size else np.zeros((0, N_MFCC))
        if features.shape[0] < 8:
            continue
        width = max(template.shape[0] for template in templates_features) + 2 * WINDOW_SLACK_FRAMES
        for window in _sliding_windows(features, width, EVALUATION_HOP_FRAMES):
            distance = min(match_distances(templates_features, window))
            if distance < negative_low:
                negative_low = distance
    warnings: List[str] = []
    if math.isfinite(negative_low):
        if negative_low > positive_high:
            # Sit 40% of the way from the owner's recordings to the closest
            # other speech, and never within 15% of that speech.
            threshold = positive_high + 0.4 * (negative_low - positive_high)
            threshold = min(threshold, negative_low * 0.85)
        else:
            warnings.append("similar_to_other_speech")
            threshold = positive_high * 1.05
    threshold = max(MIN_DISTANCE_THRESHOLD, min(MAX_DISTANCE_THRESHOLD, threshold))

    templates = [
        WakeWordTemplate(id=uuid.uuid4().hex[:12], features=features, duration_seconds=duration, source=source)
        for features, duration, source in usable
    ]
    calibration = {
        "positive_mean": round(positive_mean, 5),
        "positive_p90": round(positive_high, 5),
        "negative_min": round(negative_low, 5) if math.isfinite(negative_low) else None,
        "negatives_used": len(negatives),
        "warnings": warnings,
    }
    model = WakeWordModel(
        phrase=normalize_phrase(phrase),
        templates=templates,
        base_threshold=threshold,
        sensitivity=normalize_sensitivity(sensitivity),
        calibration=calibration,
    )
    report = {
        "templates": len(templates),
        "rejected": rejected,
        "distance_threshold": round(model.distance_threshold, 5),
        **calibration,
    }
    return model, report


def evaluate_clip(model: WakeWordModel, pcm: bytes | np.ndarray) -> Dict[str, Any]:
    """Best match of the model anywhere in a clip, for "test my wake word"."""
    samples = pcm16_to_float(pcm)
    best = math.inf
    best_at = 0.0
    width = max(1, model.max_template_frames + 2 * WINDOW_SLACK_FRAMES)
    total_frames = 0 if samples.size < FRAME_LENGTH else 1 + (samples.size - FRAME_LENGTH) // HOP_LENGTH
    if total_frames:
        for end_frame in range(min(width, total_frames), total_frames + 1, 2):
            start_frame = max(0, end_frame - width)
            segment = samples[start_frame * HOP_LENGTH : (end_frame - 1) * HOP_LENGTH + FRAME_LENGTH]
            raw = mfcc(segment, normalize=False)
            distance = min(match_distances([template.features for template in model.templates], raw))
            if distance < best:
                best = distance
                best_at = end_frame * HOP_LENGTH / SAMPLE_RATE
    return {
        "detected": bool(math.isfinite(best) and best <= model.distance_threshold),
        "distance": round(best, 5) if math.isfinite(best) else None,
        "score": round(model.score(best), 4),
        "threshold": round(model.distance_threshold, 5),
        "at_seconds": round(best_at, 3),
    }


# ---------------------------------------------------------------------------
# Streaming detection
# ---------------------------------------------------------------------------


@dataclass
class WakeDetection:
    score: float
    distance: float
    template_id: Optional[str]
    at: float
    pre_roll: bytes


class StreamingWakeWordDetector:
    """Feed PCM chunks; get a :class:`WakeDetection` when the word is heard.

    Keeps only a bounded trailing window (template length plus margin) and
    evaluates only while that window holds speech, so silence costs almost
    nothing. Not thread-safe; one instance per audio stream.
    """

    def __init__(self, model: WakeWordModel, *, pre_roll_seconds: float = 2.0) -> None:
        self.model = model
        width_frames = max(1, model.max_template_frames + 2 * WINDOW_SLACK_FRAMES)
        self._window_samples = (width_frames - 1) * HOP_LENGTH + FRAME_LENGTH
        self._keep_samples = max(self._window_samples, int(pre_roll_seconds * SAMPLE_RATE))
        self._buffer = np.zeros(0, dtype=np.float64)
        self._samples_since_eval = 0
        self._last_hit_at = -math.inf
        self._clock = 0.0
        self.evaluations = 0

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float64)
        self._samples_since_eval = 0

    def feed(self, pcm: bytes | np.ndarray) -> Optional[WakeDetection]:
        samples = pcm16_to_float(pcm)
        if samples.size == 0:
            return None
        self._clock += samples.size / float(SAMPLE_RATE)
        self._buffer = np.concatenate((self._buffer, samples))[-self._keep_samples:]
        self._samples_since_eval += samples.size
        hop = EVALUATION_HOP_FRAMES * HOP_LENGTH
        if self._samples_since_eval < hop or self._buffer.size < self._window_samples:
            return None
        self._samples_since_eval = 0
        if self._clock - self._last_hit_at < REFRACTORY_SECONDS:
            return None
        window = self._buffer[-self._window_samples:]
        levels = frame_dbfs(window)
        if levels.size == 0 or float(np.mean(levels >= SPEECH_FRAME_DBFS)) < MIN_SPEECH_FRAME_RATIO:
            return None
        self.evaluations += 1
        raw = mfcc(window, normalize=False)
        distances = match_distances(
            [template.features for template in self.model.templates],
            raw,
            abandon_above=self.model.distance_threshold,
        )
        best = min(distances)
        best_id: Optional[str] = None
        if math.isfinite(best):
            best_id = self.model.templates[distances.index(best)].id
        if math.isfinite(best) and best <= self.model.distance_threshold:
            self._last_hit_at = self._clock
            pre_roll = float_to_pcm16(self._buffer)
            self._buffer = np.zeros(0, dtype=np.float64)
            return WakeDetection(
                score=self.model.score(best),
                distance=best,
                template_id=best_id,
                at=self._clock,
                pre_roll=pre_roll,
            )
        return None


# ---------------------------------------------------------------------------
# Phrase text
# ---------------------------------------------------------------------------

_NON_WORD = re.compile(r"[^\w\s']+", re.UNICODE)


def normalize_phrase(value: Any) -> str:
    """Lower-case words only: "AutoYou-Server!" -> "autoyou server"."""
    text = str(value or "").replace("_", " ").replace("-", " ")
    text = _NON_WORD.sub(" ", text.lower())
    return re.sub(r"\s+", " ", text).strip()[:64]


def _compact(tokens: Sequence[str]) -> str:
    return "".join(tokens).replace("'", "")


@dataclass
class PhraseMatch:
    matched: bool
    score: float
    remainder: str
    start: int = -1
    end: int = -1


def match_phrase(phrase: str, transcript: str, *, min_ratio: float = 0.78) -> PhraseMatch:
    """Find the wake phrase in a transcript and return what was said around it.

    Whisper writes the same name many ways ("AutoYou", "Auto you", "auto-you"),
    so tokens are compared with spaces removed and a fuzzy ratio. The remainder
    is the command: the words after the phrase, or before it when the phrase
    came last ("what time is it, AutoYou").
    """
    phrase_tokens = normalize_phrase(phrase).split()
    original_tokens = str(transcript or "").split()
    tokens = [normalize_phrase(token) for token in original_tokens]
    if not phrase_tokens or not tokens:
        return PhraseMatch(False, 0.0, str(transcript or "").strip())
    target = _compact(phrase_tokens)
    best = PhraseMatch(False, 0.0, str(transcript or "").strip())
    span_sizes = sorted({max(1, len(phrase_tokens) + delta) for delta in (-1, 0, 1, 2)})
    for size in span_sizes:
        for start in range(0, max(0, len(tokens) - size) + 1):
            candidate = _compact(tokens[start : start + size])
            if not candidate:
                continue
            ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
            if ratio > best.score:
                best = PhraseMatch(ratio >= min_ratio, ratio, "", start, start + size)
    if not best.matched:
        return PhraseMatch(False, best.score, str(transcript or "").strip())
    after = " ".join(original_tokens[best.end :]).strip()
    before = " ".join(original_tokens[: best.start]).strip()
    remainder = after if re.search(r"\w", after) else before
    remainder = remainder.strip(" ,.;:!?-—").strip()
    best.remainder = remainder
    return best
