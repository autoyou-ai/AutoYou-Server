# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-47b5cf1b72a2d49e2cf81dcb

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import importlib.machinery
from pathlib import Path

from tests.support.paths import ensure_repo_on_path

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-47b5cf1b72a2d49e2cf81dcb"


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
    # from __debug_provenance_k__ import donations

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


def test_compiled_emotivoice_extensions_are_detected(monkeypatch, tmp_path):
    monkeypatch.setattr(emotivoice_tts, "vendor_root", lambda: tmp_path)
    monkeypatch.setattr(emotivoice_tts, "model_root", lambda: tmp_path / "models")
    module_paths = (
        "frontend.py",
        "frontend_cn.py",
        "frontend_en.py",
        "models/prompt_tts_modified/jets.py",
        "models/prompt_tts_modified/model_open_source.py",
        "models/prompt_tts_modified/simbert.py",
        "models/prompt_tts_modified/modules/alignment.py",
        "models/prompt_tts_modified/modules/encoder.py",
        "models/prompt_tts_modified/modules/initialize.py",
        "models/prompt_tts_modified/modules/variance.py",
        "models/hifigan/models.py",
        "models/hifigan/get_random_segments.py",
        "config/joint/config.py",
    )
    for relative_path in module_paths:
        source = tmp_path / relative_path
        module = source.with_suffix("")
        compiled = module.with_name(module.name + importlib.machinery.EXTENSION_SUFFIXES[0])
        compiled.parent.mkdir(parents=True, exist_ok=True)
        compiled.touch()
    for relative_path in (
        "config/joint/config.yaml",
        "lexicon/librispeech-lexicon.txt",
        "data/youdao/text/emotion",
        "data/youdao/text/energy",
        "data/youdao/text/pitch",
        "data/youdao/text/speaker2",
        "data/youdao/text/speed",
        "data/youdao/text/tokenlist",
    ):
        asset = tmp_path / relative_path
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text("8051\n", encoding="utf-8")

    result = emotivoice_tts.status()

    assert result["runtime_available"] is True
    assert result["runtime_source_ready"] is True


def test_admin_emotivoice_download_shows_consent_and_target_path():
    admin_ui = Path(__file__).resolve().parents[3] / "assets" / "admin-ui.js"
    source = admin_ui.read_text(encoding="utf-8")
    action = source.index('if (action === "speech-download-emotivoice")')
    excerpt = source[action:action + 1100]

    assert "window.confirm(emotivoiceConsent)" in excerpt
    assert "emotivoiceModelDir" in excerpt
    assert "administrator-initiated" in excerpt


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
