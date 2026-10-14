# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Multilingual contracts, isolated data-only installs, and common audio paths."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared import audio_manager, native_tts, voice_messaging, whisper_downloader
from shared.speech_config import normalize_speech_config, speech_language
from shared.speech_language import matching_system_voice


@pytest.mark.parametrize("text, language", [("The train arrives at the station in ten minutes.", "en"), ("El tren llega a la estacion en diez minutos.", "es"), ("\u96fb\u8eca\u306f\u5341\u5206\u5f8c\u306b\u99c5\u306b\u5230\u7740\u3057\u307e\u3059\u3002", "ja")])
def test_actual_language_detector_selects_the_reply_language(text, language):
    pytest.importorskip("lingua")
    from shared.speech_language import detect_language
    assert detect_language(text) == language


@pytest.mark.parametrize("language", ["", "auto", "ja", "es-MX", "zh_Hans"])
def test_auto_or_non_english_stt_uses_multilingual_weights(language):
    config = normalize_speech_config({"stt": {"model": "base.en", "language": language}})
    assert config["stt"]["model"] == "base"
    assert speech_language(config["stt"]["language"]) == speech_language(language)


def test_explicit_english_model_and_locale_are_preserved():
    config = normalize_speech_config({"stt": {"model": "small.en", "language": "en-US"}})
    assert config["stt"]["model"] == "small.en"
    assert config["stt"]["language"] == "en-US"
    assert speech_language(config["stt"]["language"]) == "en"


@pytest.mark.parametrize("model, expected", [("distil-small.en", "small"), ("distil-medium.en", "medium")])
def test_distilled_english_models_switch_to_existing_multilingual_weights(model, expected):
    assert normalize_speech_config({"stt": {"model": model, "language": "auto"}})["stt"]["model"] == expected


def test_cantonese_language_is_not_silently_replaced_with_mandarin():
    assert speech_language("yue") == "yue"


@pytest.mark.parametrize("model", ["base", "small.en", "medium", "large-v3", "distil-large-v3"])
def test_cpp_model_mapping_keeps_selected_language_coverage(model):
    assert whisper_downloader.whisper_cpp_model(model) == model


def test_cpp_does_not_silently_replace_an_unsupported_model_with_english():
    with pytest.raises(ValueError):
        whisper_downloader.whisper_cpp_model("synthetic/unsupported")


def test_system_voice_matching_ignores_an_english_selection_for_spanish():
    voices = [{"id": "english", "languages": "en-US"}, {"id": "spanish", "languages": [b"\x05es-MX"]}]
    assert matching_system_voice(voices, "es-ES", "english") == "spanish"
    assert matching_system_voice(voices, "ja", "english") == ""


def test_speech_sanitizer_preserves_multilingual_and_supplementary_letters():
    text = "**\u3053\u3093\u306b\u3061\u306f** \u0627\u0644\u0639\u0627\u0644\u0645 \U00020000 \U0001f642"
    cleaned = audio_manager._sanitize_text_for_tts(text)
    assert "\u3053\u3093\u306b\u3061\u306f" in cleaned
    assert "\u0627\u0644\u0639\u0627\u0644\u0645" in cleaned
    assert "\U00020000" in cleaned
    assert "\U0001f642" not in cleaned


def test_speech_sanitizer_preserves_spelling_joiners():
    text = "\u0645\u06cc\u200c\u0631\u0648\u0645 \u0915\u094d\u200d\u0937"
    assert audio_manager._sanitize_text_for_tts(text) == text


@pytest.fixture
def tiny_catalog(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_TTS_MODEL_DIR", str(tmp_path / "must-not-use"))
    files = {"LICENSE": b"Synthetic license", "onnx/model.onnx": b"Synthetic model"}
    monkeypatch.setattr(native_tts, "CATALOG", {"test-model": {"repo": "synthetic/models", "revision": "a" * 40, "files": {name: (len(value), hashlib.sha256(value).hexdigest()) for name, value in files.items()}}})
    calls = []
    def download(request, timeout):
        assert timeout == 30
        assert "/resolve/" + "a" * 40 + "/" in request.full_url
        name = request.full_url.split("/" + "a" * 40 + "/", 1)[1]
        calls.append(name)
        return io.BytesIO(files[name])
    monkeypatch.setattr(native_tts.urllib.request, "urlopen", download)
    return files, calls, tmp_path


def test_runtime_install_is_verified_once_and_test_scoped(tiny_catalog):
    _, calls, tmp_path = tiny_catalog
    with ThreadPoolExecutor(max_workers=3) as pool:
        roots = list(pool.map(lambda _: native_tts.ensure_model("test-model"), range(3)))
    assert all(root == tmp_path / "AutoYou/tts_models/test-model" for root in roots)
    assert not (tmp_path / "must-not-use").exists()
    assert sorted(calls) == ["LICENSE", "onnx/model.onnx"]
    assert native_tts.model_installed("test-model")
    assert native_tts.download_jobs()[0]["status"] == "completed"
    assert native_tts.download_jobs()[0]["progress_percent"] == 100
    native_tts.ensure_model("test-model", download=False)
    assert len(calls) == 2


def test_failed_checksum_is_never_installed(monkeypatch, tiny_catalog):
    files, _, _ = tiny_catalog
    files["onnx/model.onnx"] = b"Corrupted model"
    with pytest.raises(ValueError, match="checksum"):
        native_tts.ensure_model("test-model")
    assert not native_tts.model_installed("test-model")
    assert not list(native_tts.model_root().rglob("*.part"))
    assert native_tts.download_jobs()[0]["status"] == "failed"


def test_disabled_download_never_accesses_network(tiny_catalog):
    _, calls, _ = tiny_catalog
    with pytest.raises(RuntimeError, match="missing"):
        native_tts.ensure_model("test-model", download=False)
    assert calls == []


def test_unknown_model_cannot_select_a_path_or_repository(tiny_catalog):
    with pytest.raises(ValueError, match="Unknown"):
        native_tts.ensure_model("../../outside")


def test_auto_tts_downloads_for_reply_language_without_using_english_voice(monkeypatch, tmp_path):
    manager = audio_manager.AudioManager.__new__(audio_manager.AudioManager)
    monkeypatch.setattr(audio_manager, "detect_language", lambda *args: "es")
    monkeypatch.setattr(audio_manager, "list_system_tts_voices", lambda: [{"id": "english", "languages": "en-US"}])
    monkeypatch.setattr(manager, "_synthesize_system_tts", lambda *args: pytest.fail("English voice must not be used for Spanish"))
    captured = []
    monkeypatch.setattr(native_tts, "synthesize", lambda *args, **kwargs: captured.append(kwargs["language"]))
    manager._synthesize_auto_tts("Una respuesta en espanol", str(tmp_path / "speech.wav"), normalize_speech_config(None))
    assert captured == ["es"]


def test_auto_tts_uses_installed_matching_voice_before_downloading(monkeypatch, tmp_path):
    manager = audio_manager.AudioManager.__new__(audio_manager.AudioManager)
    monkeypatch.setattr(audio_manager, "detect_language", lambda *args: "fr")
    monkeypatch.setattr(audio_manager, "list_system_tts_voices", lambda: [{"id": "french", "languages": "fr-FR"}])
    captured = []
    monkeypatch.setattr(manager, "_synthesize_system_tts", lambda text, path, settings: captured.append(settings["tts"]["system_voice"]))
    monkeypatch.setattr(native_tts, "synthesize", lambda *args, **kwargs: pytest.fail("A download is unnecessary"))
    settings = normalize_speech_config(None)
    manager._synthesize_auto_tts("Bonjour", str(tmp_path / "speech.wav"), settings)
    assert captured == ["french"]
    assert settings["tts"]["system_voice"] == ""


def test_native_rejects_unsupported_language_before_downloading(monkeypatch, tmp_path):
    monkeypatch.setattr(native_tts, "ensure_model", lambda *args, **kwargs: pytest.fail("Unsupported language must not download"))
    with pytest.raises(RuntimeError, match="does not support language zh"):
        native_tts.synthesize("\u4f60\u597d", str(tmp_path / "speech.wav"), normalize_speech_config(None), language="zh")


def test_calls_and_voice_notes_share_the_auto_tts_router(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_manager, "voice_runtime_available", lambda settings: True)
    manager = audio_manager.AudioManager(lambda *_: None, enable_stt=False)
    texts = []
    def synthesize(text, path, settings):
        texts.append(text)
        Path(path).write_bytes(b"RIFF" + b"\0" * 256)
    monkeypatch.setattr(manager, "_synthesize_auto_tts", synthesize)
    monkeypatch.setattr(manager, "_is_tts_generation_active", lambda generation: True)
    monkeypatch.setattr(manager, "tts_track", SimpleNamespace(queue_audio_file=lambda *args, **kwargs: None))
    try:
        manager.synthesize_to_file("**Bonjour**", output_path=str(tmp_path / "note.wav"))
        manager._speak_thread("**Hola**", normalize_speech_config(None), 1)
    finally:
        manager.tts_track = None
        manager.close()
    assert texts == ["Bonjour", "Hola"]


def test_download_failure_preserves_existing_call_readiness(monkeypatch):
    monkeypatch.setattr(audio_manager, "voice_runtime_available", lambda settings: True)
    events = []
    manager = audio_manager.AudioManager(lambda *_: None, enable_stt=False, status_callback=events.append)
    monkeypatch.setattr(manager, "_is_tts_generation_active", lambda generation: True)
    def fail(*args):
        raise RuntimeError("synthetic offline download")
    monkeypatch.setattr(manager, "_synthesize_auto_tts", fail)
    manager._emit_status("ready", "Voice pipeline ready")
    try:
        manager._speak_thread("Hola", normalize_speech_config(None), 1)
    finally:
        manager.close()
    assert events[-1]["state"] == "ready"
    assert "synthetic offline download" in events[-1]["detail"]


def test_compiled_speech_library_manages_models_without_faster_whisper(monkeypatch, tmp_path):
    from shared.admin_speech_library import SpeechModelLibraryService
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    service = SpeechModelLibraryService()
    monkeypatch.setattr(service, "uses_faster_whisper", lambda: False)
    model = tmp_path / "AutoYou/tools/whisper/models/ggml-base.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"synthetic-model")
    status = service.get_status("base")
    assert status["supported"] is True
    assert status["backend"] == "whisper.cpp"
    assert any(item["model"] == "base" and item["installed"] for item in status["models"])
    assert service.delete_model("../../outside")["status"] == "error"
    assert service.delete_model("base")["status"] == "success"
    assert not model.exists()


def test_compiled_speech_library_download_reports_job_progress(monkeypatch, tmp_path):
    from shared.admin_speech_library import SpeechModelLibraryService
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    service = SpeechModelLibraryService()
    monkeypatch.setattr(service, "uses_faster_whisper", lambda: False)
    def download(model, progress_callback):
        assert model == "base"
        progress_callback(512, 1024)
        target = tmp_path / "model.bin"
        target.write_bytes(b"synthetic-model")
        return target
    monkeypatch.setattr(whisper_downloader, "download_whisper_model", download)
    class ImmediateThread:
        def __init__(self, *, target, kwargs, **unused):
            self.target, self.kwargs = target, kwargs
        def start(self):
            self.target(**self.kwargs)
    import shared.admin_speech_library as library
    monkeypatch.setattr(library.threading, "Thread", ImmediateThread)
    job = service.start_download_job("base")
    assert job["status"] == "completed"
    assert job["progress_percent"] == 100
    assert job["completed_bytes"] == 512


@pytest.mark.parametrize("language, expected", [("auto", None), ("", None), ("ja-JP", "ja")])
def test_recorded_stt_languages_and_lazy_audio_lifetime(monkeypatch, tmp_path, language, expected):
    path = tmp_path / "synthetic.wav"
    path.write_bytes(b"synthetic-audio")
    active = []
    @contextmanager
    def materialize(_):
        active.append(True)
        try:
            yield path
        finally:
            active.clear()
    def segments():
        assert active, "Audio must remain materialized while Whisper's generator runs"
        yield SimpleNamespace(text="\u3053\u3093\u306b\u3061\u306f")
    def transcribe(path, **kwargs):
        assert kwargs["language"] == expected
        return segments(), None
    monkeypatch.setattr(voice_messaging, "_WhisperModel", object())
    monkeypatch.setattr(voice_messaging, "_get_whisper_model", lambda *args: SimpleNamespace(transcribe=transcribe))
    monkeypatch.setattr(voice_messaging, "materialize_secure_file", materialize)
    assert voice_messaging.transcribe_voice_note(str(path), settings={"stt": {"model": "base", "language": language}}) == "\u3053\u3093\u306b\u3061\u306f"
