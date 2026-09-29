# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

from pathlib import Path

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

from shared import audio_manager, emotivoice_tts
from shared.audio_manager import AudioManager


def test_conversation_emotion_prompt_uses_current_turn_text():
    assert emotivoice_tts.conversation_emotion_prompt("I am angry about this") == "Angry"
    assert emotivoice_tts.conversation_emotion_prompt("I'm so happy, thanks!") == "Happy"
    assert emotivoice_tts.conversation_emotion_prompt("我很难过") == "Sad"
    assert emotivoice_tts.conversation_emotion_prompt("Tell me the time") == "Neutral"


def test_emotivoice_model_status_uses_managed_voice_root(monkeypatch, tmp_path):
    monkeypatch.setattr(emotivoice_tts, "model_root", lambda: tmp_path / "models" / "emotivoice")

    result = emotivoice_tts.status()

    assert result["ready"] is False
    assert result["models_ready"] is False
    assert result["runtime_source_ready"] is True
    assert Path(result["model_dir"]) == tmp_path / "models" / "emotivoice"
    assert "8051" in result["speaker_ids"]


def test_compiled_runtime_without_vendor_source_reports_provider_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(emotivoice_tts, "model_root", lambda: tmp_path / "models" / "emotivoice")
    monkeypatch.setattr(emotivoice_tts, "vendor_root", lambda: tmp_path / "missing-vendor")

    result = emotivoice_tts.status()

    assert result["runtime_source_ready"] is False
    assert result["ready"] is False


def test_emotivoice_model_status_requires_all_managed_english_g2p_resources(monkeypatch, tmp_path):
    root = tmp_path / "models" / "emotivoice"
    monkeypatch.setattr(emotivoice_tts, "model_root", lambda: root)
    (root / "outputs" / "prompt_tts_open_source_joint" / "ckpt").mkdir(parents=True)
    (root / "outputs" / "prompt_tts_open_source_joint" / "ckpt" / "g_00140000").touch()
    (root / "outputs" / "style_encoder" / "ckpt").mkdir(parents=True)
    (root / "outputs" / "style_encoder" / "ckpt" / "checkpoint_163431").touch()
    bert = root / "simbert-base-chinese"
    bert.mkdir(parents=True)
    for name in ("config.json", "model.safetensors", "vocab.txt"):
        (bert / name).touch()

    assert emotivoice_tts.status()["models_ready"] is False

    for relative in (
        "nltk_data/taggers/averaged_perceptron_tagger",
        "nltk_data/taggers/averaged_perceptron_tagger_eng",
        "nltk_data/corpora/cmudict",
    ):
        (root / relative).mkdir(parents=True)

    assert emotivoice_tts.status()["models_ready"] is True


def test_audio_manager_passes_voice_conversation_context(monkeypatch, tmp_path):
    output_path = tmp_path / "reply.wav"
    captured = {}
    monkeypatch.setattr(audio_manager, "emotivoice_status", lambda: {"ready": True})

    def fake_synthesis(text, path, settings, *, context=""):
        captured.update(text=text, settings=settings, context=context)
        Path(path).write_bytes(b"RIFF" + (b"\0" * 256))

    monkeypatch.setattr(audio_manager, "synthesize_emotivoice", fake_synthesis)
    manager = AudioManager(
        on_text_callback=lambda _text: None,
        settings_provider=lambda: {
            "tts": {"provider": "emotivoice", "rate": 1.0, "emotivoice": {"speaker": "8051"}},
            "stt": {"model": "tiny.en"},
        },
        enable_stt=False,
    )
    try:
        result = manager.synthesize_to_file("Hello.", output_path=str(output_path), context="User sounds happy.")
    finally:
        manager.close()

    assert result == str(output_path)
    assert captured["text"] == "Hello"
    assert captured["context"] == "User sounds happy."
    assert captured["settings"]["tts"]["provider"] == "emotivoice"
