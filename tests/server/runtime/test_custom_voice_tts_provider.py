# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

from pathlib import Path

from shared import audio_manager
from shared.audio_manager import AudioManager
from shared.custom_voice_tts import _normalize_custom_voice_tts_text, _split_custom_voice_tts_text


def test_audio_manager_synthesizes_with_custom_provider(monkeypatch, tmp_path):
    output_path = tmp_path / "custom.wav"

    monkeypatch.setattr(audio_manager, "custom_voice_model_ready", lambda *args, **kwargs: True)

    def fake_custom_tts(self, text, temp_path, settings, *, fallback_to_system=True):
        assert text == "Hello custom voice"
        assert settings["tts"]["provider"] == "custom"
        assert fallback_to_system is False
        Path(temp_path).write_bytes(b"RIFF" + (b"\0" * 256))

    monkeypatch.setattr(AudioManager, "_synthesize_custom_voice_tts", fake_custom_tts)

    manager = AudioManager(
        on_text_callback=lambda text: None,
        settings_provider=lambda: {
            "tts": {"provider": "custom", "rate": 1.0},
            "stt": {"model": "tiny.en"},
        },
        enable_stt=False,
    )

    try:
        result = manager.synthesize_to_file("Hello custom voice.", output_path=str(output_path))
    finally:
        manager.close()

    assert result == str(output_path)
    assert output_path.stat().st_size > 128


def test_custom_voice_tts_text_is_normalized_and_chunked():
    raw = """
    ## Capabilities
    - **Answer questions** and explain things.
    - Use `tools`, links like [docs](https://example.test), and emoji :)
    """

    normalized = _normalize_custom_voice_tts_text(raw)
    chunks = _split_custom_voice_tts_text(raw, max_chars=55)

    assert "https://" not in normalized
    assert "**" not in normalized
    assert "`" not in normalized
    assert "docs" in normalized
    assert chunks
    assert all(len(chunk) <= 55 for chunk in chunks)
