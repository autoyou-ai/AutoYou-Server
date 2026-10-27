# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Personal wake word engine: features, DTW, calibration, streaming, phrases.

Audio here is synthetic: "words" are sequences of voiced segments, each a
harmonic series shaped by three formant peaks. Different formant sequences are
different words; the same sequence with a new pitch, tempo and noise is the
same word said again. No recording of anyone is involved.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from shared import wake_word as ww

SR = ww.SAMPLE_RATE

# Formant triples (Hz) per segment: vowel-like sounds strung into a "word".
WAKE = [(700, 1200, 2600), (300, 2300, 3000), (500, 900, 2400), (350, 1700, 2600)]
OTHER = [(400, 800, 2500), (650, 1700, 2700), (300, 900, 2200), (600, 1000, 2500)]
ALSO_OTHER = [(300, 2300, 3000), (700, 1200, 2600), (350, 1700, 2600), (500, 900, 2400)]


def synth_word(formants, *, rng, f0=130.0, segment_seconds=0.16, stretch=1.0, level=0.25):
    out = []
    for f1, f2, f3 in formants:
        n = int(segment_seconds * stretch * SR)
        t = np.arange(n) / SR
        pitch = f0 * (1.0 + 0.03 * np.sin(2 * math.pi * 3 * t))
        phase = 2 * math.pi * np.cumsum(pitch) / SR
        signal = np.zeros(n)
        for harmonic in range(1, int(3800 / f0)):
            freq = harmonic * f0
            gain = sum(math.exp(-((freq - f) / 90.0) ** 2) for f in (f1, f2, f3)) + 0.02
            signal += gain * np.sin(harmonic * phase)
        envelope = np.minimum(1.0, np.minimum(t, t[::-1]) / 0.02)
        out.append(signal * envelope)
    word = np.concatenate(out)
    word = word / (np.max(np.abs(word)) + 1e-9) * level
    return word


def with_noise(signal, *, rng, snr_db=25.0, pad_seconds=0.4):
    power = np.mean(signal ** 2)
    sigma = math.sqrt(power / 10 ** (snr_db / 10))
    pad = rng.normal(0, sigma, int(pad_seconds * SR))
    return np.concatenate([pad, signal + rng.normal(0, sigma, signal.size), pad])


def enroll(rng, formants=WAKE, count=3):
    recordings = []
    for index in range(count):
        word = synth_word(formants, rng=rng, f0=125 + 8 * index, stretch=0.92 + 0.08 * index)
        recordings.append(ww.float_to_pcm16(with_noise(word, rng=rng)))
    return recordings


def stream(detector, samples, chunk=320):
    pcm = ww.float_to_pcm16(samples)
    hits = []
    for start in range(0, len(pcm), chunk * 2):
        hit = detector.feed(pcm[start : start + chunk * 2])
        if hit is not None:
            hits.append(hit)
    return hits


def test_features_have_the_documented_shape_and_ignore_loudness():
    rng = np.random.default_rng(1)
    word = synth_word(WAKE, rng=rng)
    loud = ww.mfcc(word)
    quiet = ww.mfcc(word * 0.1)
    assert loud.shape == (1 + (word.size - ww.FRAME_LENGTH) // ww.HOP_LENGTH, ww.N_MFCC)
    assert np.allclose(loud.mean(axis=0), 0.0, atol=1e-9)
    assert np.max(np.abs(loud - quiet)) < 1e-6  # c0 dropped + mean-normalised


def test_dtw_is_zero_for_identical_input_and_handles_unreachable_lengths():
    rng = np.random.default_rng(2)
    features = ww.mfcc(synth_word(WAKE, rng=rng))
    assert ww.dtw_distance(features, features) == pytest.approx(0.0, abs=1e-9)
    assert ww.dtw_distance(features, features[: features.shape[0] // 3]) == math.inf
    assert ww.dtw_distance(features, np.zeros((0, ww.N_MFCC))) == math.inf


def test_batched_dtw_matches_single_pairs_even_with_mixed_lengths():
    rng = np.random.default_rng(3)
    a = ww.mfcc(synth_word(WAKE, rng=rng, stretch=0.8))
    b = ww.mfcc(synth_word(WAKE, rng=rng, stretch=1.2))
    c = ww.mfcc(synth_word(OTHER, rng=rng))
    singles = [ww.dtw_distance(a, b), ww.dtw_distance(c, a), ww.dtw_distance(b, c)]
    batched = ww.dtw_distances([a, c, b], [b, a, c])
    assert batched == pytest.approx(singles, rel=1e-12)


def test_early_abandon_never_drops_a_match_from_a_shorter_template():
    rng = np.random.default_rng(4)
    short = ww.mfcc(synth_word(WAKE[:2], rng=rng))
    long = ww.mfcc(synth_word(OTHER * 2, rng=rng))
    exact = ww.dtw_distances([long, short], [ww.mfcc(synth_word(ALSO_OTHER * 2, rng=rng)), short])
    abandoned = ww.dtw_distances(
        [long, short], [ww.mfcc(synth_word(ALSO_OTHER * 2, rng=rng)), short], abandon_above=0.05
    )
    assert exact[1] == pytest.approx(0.0, abs=1e-9)
    assert abandoned[1] == pytest.approx(0.0, abs=1e-9)
    assert abandoned[0] == math.inf


def test_model_detects_its_word_and_rejects_other_words():
    rng = np.random.default_rng(5)
    model, report = ww.build_model("Hey AutoYou", enroll(rng))
    assert report["templates"] == 3
    assert ww.MIN_DISTANCE_THRESHOLD <= model.distance_threshold <= ww.MAX_DISTANCE_THRESHOLD
    assert model.phrase == "hey autoyou"

    said_again = with_noise(synth_word(WAKE, rng=rng, f0=140, stretch=1.05), rng=rng, snr_db=18)
    assert ww.evaluate_clip(model, ww.float_to_pcm16(said_again))["detected"] is True
    for formants in (OTHER, ALSO_OTHER):
        other = with_noise(synth_word(formants, rng=rng, f0=140), rng=rng, snr_db=18)
        result = ww.evaluate_clip(model, ww.float_to_pcm16(other))
        assert result["detected"] is False
        assert result["score"] < 0.5


def test_streaming_detector_fires_once_inside_continuous_speech():
    rng = np.random.default_rng(6)
    model, _ = ww.build_model("hey autoyou", enroll(rng))
    sentence = np.concatenate([
        synth_word(OTHER, rng=rng, f0=135),
        synth_word(WAKE, rng=rng, f0=135, stretch=1.03),
        synth_word(ALSO_OTHER, rng=rng, f0=135),
    ])
    detector = ww.StreamingWakeWordDetector(model)
    hits = stream(detector, with_noise(sentence, rng=rng, snr_db=20))
    assert len(hits) == 1
    assert hits[0].score >= 0.5
    assert hits[0].pre_roll  # the word's audio, for transcript confirmation

    quiet = ww.StreamingWakeWordDetector(model)
    assert stream(quiet, with_noise(synth_word(OTHER, rng=rng), rng=rng)) == []
    silence = ww.StreamingWakeWordDetector(model)
    assert stream(silence, np.zeros(SR * 3)) == []
    assert silence.evaluations == 0  # silence costs no DTW work


def test_negatives_from_other_speech_keep_the_threshold_below_them():
    rng = np.random.default_rng(7)
    negatives = [ww.float_to_pcm16(with_noise(synth_word(OTHER + ALSO_OTHER, rng=rng), rng=rng))]
    model, report = ww.build_model("hey autoyou", enroll(rng), negatives=negatives)
    assert report["negatives_used"] == 1
    assert report["negative_min"] is not None
    assert model.base_threshold < report["negative_min"]


def test_bad_recordings_are_rejected_with_reasons():
    rng = np.random.default_rng(8)
    silence = ww.float_to_pcm16(np.zeros(SR))
    too_long = ww.float_to_pcm16(synth_word(WAKE * 6, rng=rng))
    assert "too_short" in ww.check_sample(silence).problems or "too_quiet" in ww.check_sample(silence).problems
    assert "too_long" in ww.check_sample(too_long).problems
    with pytest.raises(ValueError, match="at least 3"):
        ww.build_model("x", [silence, too_long] + enroll(rng, count=1))


def test_inconsistent_recording_is_dropped_when_enough_remain():
    rng = np.random.default_rng(9)
    recordings = enroll(rng, count=4) + [ww.float_to_pcm16(with_noise(synth_word(OTHER, rng=rng), rng=rng))]
    model, report = ww.build_model("hey autoyou", recordings)
    assert any("inconsistent" in item.get("problems", []) for item in report["rejected"])
    assert len(model.templates) == 4


def test_model_round_trips_and_sensitivity_moves_the_threshold():
    rng = np.random.default_rng(10)
    model, _ = ww.build_model("hey autoyou", enroll(rng))
    restored = ww.WakeWordModel.from_dict(model.to_dict())
    assert restored.phrase == model.phrase
    assert restored.distance_threshold == pytest.approx(model.distance_threshold, rel=1e-3)
    assert len(restored.templates) == len(model.templates)
    loose = ww.threshold_for_sensitivity(0.2, 1.0)
    strict = ww.threshold_for_sensitivity(0.2, 0.0)
    assert strict < 0.2 < loose
    assert "templates" not in model.to_dict(include_templates=False)
    with pytest.raises(ValueError):
        ww.WakeWordModel.from_dict({"format": "other"})


def test_wav_round_trip_resamples_and_downmixes():
    import io
    import wave

    rng = np.random.default_rng(11)
    word = synth_word(WAKE, rng=rng)
    stereo = np.repeat(ww.resample_linear(word, SR, 48_000)[:, None], 2, axis=1)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(48_000)
        writer.writeframes((stereo * 32767).astype("<i2").tobytes())
    pcm = ww.wav_bytes_to_pcm16(buffer.getvalue())
    assert abs(len(pcm) // 2 - word.size) <= 2
    with pytest.raises(ValueError):
        ww.wav_bytes_to_pcm16(b"not a wav")


@pytest.mark.parametrize(
    ("phrase", "transcript", "matched", "remainder"),
    [
        ("Hey AutoYou", "Hey AutoYou, what time is it?", True, "what time is it"),
        ("Hey AutoYou", "hey auto you turn on the lights", True, "turn on the lights"),
        ("AutoYou-Server", "What's the weather, AutoYou server?", True, "What's the weather"),
        ("Hey AutoYou", "Hey everyone, are we ready", False, "Hey everyone, are we ready"),
        ("Hey AutoYou", "Hey Otto you, play music", True, "play music"),
        ("Jarvis", "I was talking about lunch", False, "I was talking about lunch"),
        ("Jarvis", "Jarvis.", True, ""),
    ],
)
def test_phrase_matching_is_fuzzy_about_spelling_and_returns_the_command(phrase, transcript, matched, remainder):
    result = ww.match_phrase(phrase, transcript)
    assert result.matched is matched
    assert result.remainder == remainder


def test_normalize_phrase_keeps_only_words():
    assert ww.normalize_phrase("AutoYou-Server!") == "autoyou server"
    assert ww.normalize_phrase("  Hey,   Jarvis  ") == "hey jarvis"
    assert ww.normalize_phrase(None) == ""
