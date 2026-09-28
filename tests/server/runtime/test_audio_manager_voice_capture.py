# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

from array import array
import json
import math

from shared.audio_manager import AudioManager
from shared.speech_config import normalize_speech_config
from shared.voice_training_storage import get_voice_training_dir


def _speech_settings(*, capture_enabled: bool):
    return normalize_speech_config(
        {
            "tts": {"provider": "off"},
            "voice_training": {"capture_enabled": capture_enabled},
        }
    )


def _synthetic_pcm(seconds: float, *, sample_rate: int = 16_000) -> bytes:
    sample_count = int(seconds * sample_rate)
    samples = array(
        "h",
        (
            int(9000 * math.sin(2.0 * math.pi * 220.0 * index / sample_rate))
            for index in range(sample_count)
        ),
    )
    return samples.tobytes()


def test_voice_training_capture_defaults_off():
    settings = normalize_speech_config({})

    assert settings["voice_training"]["capture_enabled"] is False


def test_voice_training_capture_saves_long_call_with_quality_warnings(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    manager = AudioManager(
        on_text_callback=lambda text: None,
        settings_provider=lambda: _speech_settings(capture_enabled=True),
        enable_stt=False,
    )
    transcript = " ".join(["Synthetic voice training transcript"] * 20)

    try:
        manager._save_captured_voice_thread(_synthetic_pcm(21.0), transcript)
    finally:
        manager.close()

    vt_dir = get_voice_training_dir()
    transcript_file = vt_dir / "transcripts.json"
    entries = json.loads(transcript_file.read_text(encoding="utf-8"))

    assert len(entries) == 1
    assert entries[0]["transcript"] == transcript
    assert entries[0]["source"] == "voice_call"
    assert entries[0]["training_eligible"] is False
    assert {"too_long", "transcript_too_long"}.issubset(set(entries[0]["quality_warnings"]))
    assert (vt_dir / "recordings" / entries[0]["filename"]).is_file()


def test_voice_training_capture_respects_disabled_speech_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    manager = AudioManager(
        on_text_callback=lambda text: None,
        settings_provider=lambda: _speech_settings(capture_enabled=False),
        enable_stt=False,
    )

    try:
        manager._save_captured_voice_thread(
            _synthetic_pcm(4.0),
            "Synthetic transcript long enough for capture.",
        )
    finally:
        manager.close()

    assert not (get_voice_training_dir() / "transcripts.json").exists()
