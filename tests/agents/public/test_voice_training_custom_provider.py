# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-0d552ed9e06a4299d2206d8b

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from autoyou_agents.voice_training_agent import agent as voice_agent
from autoyou_agents.voice_training_agent import reference_selection as refsel
from autoyou_agents.voice_training_agent import train_model
from shared import custom_voice_tts
from shared.custom_voice_tts import (
    CUSTOM_VOICE_ACTIVE_MODEL_FILENAME,
    CUSTOM_VOICE_METADATA_FILENAME,
    VOICE_CLONE_CONSENT_PHRASE,
    VOICE_CLONE_CONSENT_VERSION,
    VOICE_CLONE_PREVIEW_FILENAME,
    VOICE_CLONE_REFERENCE_FILENAME,
    VoiceCloneEngineError,
    VoiceCloneWorker,
    custom_voice_model_ready,
    custom_voice_status,
    get_custom_voice_model_dir,
    load_voice_clone_consent,
    record_voice_clone_consent,
    reference_key,
    revoke_voice_clone_consent,
    synthesize_custom_voice_to_file,
    voice_clone_outputs_dir,
)
from shared.speech_config import normalize_speech_config
from shared.voice_training_quality import analyze_pcm16_audio, voice_training_rejection_reasons

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-0d552ed9e06a4299d2206d8b"

SYNTHETIC_TRANSCRIPT = "Synthetic phrase for a cloned voice test."


@pytest.fixture
def voice_root(monkeypatch, tmp_path):
    """A private AutoYou state root, so no test sees another's consent or voices."""
    root = tmp_path / "autoyou-root"
    root.mkdir()
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(root))
    monkeypatch.delenv("AUTOYOU_VOICE_TRAINING_DIR", raising=False)
    monkeypatch.delenv("AUTOYOU_VOICE_CLONE_PYTHON", raising=False)
    vt_dir, _, _, _ = train_model._get_voice_training_dirs()
    assert root.resolve() in vt_dir.resolve().parents
    return vt_dir


def _voice_like(seconds: float, *, sample_rate: int = 16000, top_hz: float = 6000.0, rms: float = 0.1, seed: int = 1):
    """Harmonics of 150 Hz up to ``top_hz`` with random phases: a stand-in for voiced speech."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    audio = np.zeros_like(t)
    harmonic = 150.0
    while harmonic <= top_hz and harmonic < sample_rate / 2:
        audio += np.sin(2 * np.pi * harmonic * t + rng.uniform(0, 2 * np.pi))
        harmonic += 150.0
    audio *= rms / max(float(np.sqrt(np.mean(audio**2))), 1e-9)
    return audio.astype(np.float32)


def _write_wav(path: Path, audio, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def _seed_dataset(vt_dir: Path, clips) -> None:
    """clips: (id, audio, source, user_id) tuples."""
    entries = []
    for clip_id, audio, source, user_id in clips:
        filename = f"{clip_id}.wav"
        _write_wav(vt_dir / "recordings" / filename, audio)
        entry = {"id": clip_id, "filename": filename, "transcript": SYNTHETIC_TRANSCRIPT, "source": source}
        if user_id:
            entry["user_id"] = user_id
        entries.append(entry)
    (vt_dir / "transcripts.json").write_text(json.dumps(entries), encoding="utf-8")


def _standard_dataset(vt_dir: Path) -> None:
    _seed_dataset(
        vt_dir,
        [
            ("clear-a", _voice_like(4.0, seed=1), "voice_call", "synthetic::caller-one"),
            ("clear-b", _voice_like(4.0, seed=2), "voice_call", "synthetic::caller-one"),
            ("clear-c", _voice_like(4.0, seed=3), "upload", ""),
            ("muffled", _voice_like(4.0, top_hz=900.0, seed=4), "voice_call", "synthetic::caller-one"),
            ("silent", np.zeros(16000 * 3, dtype=np.float32), "voice_call", "synthetic::caller-one"),
        ],
    )


class _FakeWorker:
    """Stands in for the Chatterbox runtime: writes a tone and reports a watermark score."""

    def __init__(self, *, preview_score: float = 0.97, reference_score: float = 0.02) -> None:
        self.preview_score = preview_score
        self.reference_score = reference_score
        self.calls = []
        self.closed = False

    def ensure_loaded(self, *, allow_download, timeout=None):
        self.calls.append(("load", allow_download))
        return {"device": "cpu", "accelerator": "CPU", "chatterbox_version": "0.1.7", "load_seconds": 0.1}

    def use_reference(self, reference_path, *, key, exaggeration=0.5):
        assert Path(reference_path).is_file()
        self.calls.append(("reference", key))
        return {"key": key}

    def synthesize(self, texts, output_path, *, options=None):
        self.calls.append(("synthesize", list(texts), dict(options or {})))
        _write_wav(Path(output_path), _voice_like(1.0, sample_rate=24000), 24000)
        return {"audio_seconds": 1.0, "wall_seconds": 2.9, "realtime_factor": 2.9, "device": "cpu"}

    def detect_watermark(self, audio_path):
        is_preview = Path(audio_path).name == VOICE_CLONE_PREVIEW_FILENAME
        self.calls.append(("detect", Path(audio_path).name))
        return {"score": self.preview_score if is_preview else self.reference_score}

    def close(self):
        self.closed = True


def _use_fake_worker(monkeypatch, worker: _FakeWorker) -> _FakeWorker:
    monkeypatch.setattr(train_model, "open_voice_clone_worker", lambda **kwargs: worker)
    monkeypatch.setattr(train_model, "resolve_voice_clone_python", lambda: ("synthetic-python", "runtime"))
    return worker


def _consent() -> None:
    record_voice_clone_consent(accepted_via="website", statement_version=VOICE_CLONE_CONSENT_VERSION)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ── Building the cloned voice ───────────────────────────────────────────────


def test_build_refuses_without_consent(monkeypatch, voice_root):
    _standard_dataset(voice_root)
    monkeypatch.setattr(
        train_model, "open_voice_clone_worker", lambda **kwargs: pytest.fail("worker must not start without consent")
    )

    train_model.run_training_sync()

    status = train_model.get_status()
    assert status["status"] == "failed"
    assert status["failure_reason"] == "consent_required"
    assert not custom_voice_model_ready()
    assert not voice_clone_outputs_dir().exists() or not any(voice_clone_outputs_dir().iterdir())


def test_build_creates_watermarked_clone_from_clean_clips(monkeypatch, voice_root):
    _standard_dataset(voice_root)
    _consent()
    worker = _use_fake_worker(monkeypatch, _FakeWorker())

    train_model.run_training_sync()

    status = train_model.get_status()
    assert status["status"] == "completed", status
    assert status["voice_cloned"] is True
    assert custom_voice_model_ready()
    model_dir = custom_voice_tts.resolve_custom_voice_model_dir()
    assert voice_clone_outputs_dir().resolve() in model_dir.parents
    metadata = _read_json(model_dir / CUSTOM_VOICE_METADATA_FILENAME)
    assert metadata["engine"] == "chatterbox"
    assert metadata["model_revision"] == custom_voice_tts.VOICE_CLONE_MODEL_REVISION
    assert metadata["voice_cloned"] is True
    assert metadata["fine_tuning_applied"] is False
    assert metadata["watermark"] == {
        "method": "perth_implicit",
        "enabled": True,
        "verified": True,
        "preview_score": 0.97,
        "reference_score": 0.02,
        "min_score": 0.5,
    }
    chosen = [clip["sample_id"] for clip in metadata["reference"]["clips"]]
    assert chosen[0] == "clear-c"  # the curated upload ranks first
    assert set(chosen) <= {"clear-a", "clear-b", "clear-c"}
    assert metadata["reference"]["selection"]["rejected"]["muffled"] == 1
    assert metadata["consent"]["version"] == VOICE_CLONE_CONSENT_VERSION
    # Metadata keeps measurements, never what was said or who called.
    serialized = json.dumps(metadata)
    assert SYNTHETIC_TRANSCRIPT not in serialized
    assert "caller-one" not in serialized

    with wave.open(str(model_dir / VOICE_CLONE_REFERENCE_FILENAME), "rb") as wf:
        assert wf.getframerate() == 24000
        assert 8.0 < wf.getnframes() / 24000 <= 15.0
    assert (model_dir / VOICE_CLONE_PREVIEW_FILENAME).is_file()
    assert ("load", True) in worker.calls
    assert worker.closed is True
    assert (voice_root / "models" / CUSTOM_VOICE_ACTIVE_MODEL_FILENAME).exists()


def test_rebuild_replaces_the_previous_reference(monkeypatch, voice_root):
    _standard_dataset(voice_root)
    _consent()
    _use_fake_worker(monkeypatch, _FakeWorker())
    train_model.run_training_sync()
    first = custom_voice_tts.resolve_custom_voice_model_dir()

    train_model.run_training_sync(["clear-a", "clear-b"])

    second = custom_voice_tts.resolve_custom_voice_model_dir()
    assert second != first
    assert not first.exists()
    assert [path.name for path in voice_clone_outputs_dir().iterdir()] == [second.name]


def test_build_fails_closed_when_watermark_is_not_detected(monkeypatch, voice_root):
    _standard_dataset(voice_root)
    _consent()
    _use_fake_worker(monkeypatch, _FakeWorker(preview_score=0.1))

    train_model.run_training_sync()

    status = train_model.get_status()
    assert status["status"] == "failed"
    assert status["failure_reason"] == "watermark_not_detected"
    assert not custom_voice_model_ready()
    assert list(voice_clone_outputs_dir().iterdir()) == []


def test_build_explains_when_clips_are_not_clean_enough(monkeypatch, voice_root):
    _seed_dataset(
        voice_root,
        [
            ("muffled", _voice_like(6.0, top_hz=900.0), "voice_call", ""),
            ("silent", np.zeros(16000 * 6, dtype=np.float32), "voice_call", ""),
            ("short", _voice_like(0.5), "voice_call", ""),
        ],
    )
    _consent()
    _use_fake_worker(monkeypatch, _FakeWorker())

    train_model.run_training_sync()

    status = train_model.get_status()
    assert status["status"] == "failed"
    assert status["failure_reason"] == "not_enough_clean_speech"
    assert "Upload a 10-20 second WAV" in status["message"]
    assert status["reference_selection"]["rejected"] == {
        "muffled": 1,
        "too_much_silence": 1,
        "too_quiet": 1,
        "too_short": 1,
    }


def test_build_uses_pinned_clips_in_order_and_warns_about_failed_checks(monkeypatch, voice_root):
    _standard_dataset(voice_root)
    _consent()
    _use_fake_worker(monkeypatch, _FakeWorker())

    train_model.run_training_sync(["clear-b", "muffled", "clear-a"])

    metadata = _read_json(custom_voice_tts.resolve_custom_voice_model_dir() / CUSTOM_VOICE_METADATA_FILENAME)
    assert [clip["sample_id"] for clip in metadata["reference"]["clips"]] == ["clear-b", "muffled", "clear-a"]
    assert metadata["reference"]["pinned"] is True
    assert "pinned_clip_muffled_muffled" in metadata["install_warnings"]


def test_build_reports_unknown_pinned_clip(monkeypatch, voice_root):
    _standard_dataset(voice_root)
    _consent()
    _use_fake_worker(monkeypatch, _FakeWorker())

    train_model.run_training_sync(["does-not-exist"])

    status = train_model.get_status()
    assert status["failure_reason"] == "pinned_clips_not_found"
    assert "does-not-exist" in status["message"]


def test_reference_candidates_ignore_missing_and_external_audio(tmp_path):
    vt_dir = tmp_path / "voice_training"
    _write_wav(vt_dir / "recordings" / "usable.wav", _voice_like(2.0))
    external_wav = tmp_path / "outside.wav"
    _write_wav(external_wav, _voice_like(2.0))

    candidates = train_model.collect_reference_candidates(
        vt_dir,
        [
            {"id": "usable", "filename": "usable.wav"},
            {"id": "missing", "filename": "missing.wav"},
            {"id": "external", "audio_path": str(external_wav)},
        ],
    )

    assert [candidate.sample_id for candidate in candidates] == ["usable"]


# ── Consent ────────────────────────────────────────────────────────────────


def test_consent_needs_the_exact_phrase_or_the_current_website_statement(voice_root):
    with pytest.raises(ValueError):
        record_voice_clone_consent(accepted_via="agent", confirmation="yes go ahead")
    with pytest.raises(ValueError):
        record_voice_clone_consent(accepted_via="website", statement_version="2000-01-01")
    with pytest.raises(ValueError):
        record_voice_clone_consent(accepted_via="agent", statement_version=VOICE_CLONE_CONSENT_VERSION)
    assert load_voice_clone_consent() is None

    record = record_voice_clone_consent(accepted_via="agent", confirmation="  i consent to cloning MY OWN voice. ")

    assert record["version"] == VOICE_CLONE_CONSENT_VERSION
    assert load_voice_clone_consent()["consent_id"] == record["consent_id"]


def test_consent_for_an_older_statement_does_not_count(voice_root):
    _consent()
    path = custom_voice_tts.voice_clone_consent_path()
    record = _read_json(path)
    record["version"] = "2000-01-01"
    path.write_text(json.dumps(record), encoding="utf-8")

    assert load_voice_clone_consent() is None


def test_withdrawing_consent_deletes_the_clone_and_keeps_recordings(monkeypatch, voice_root):
    _standard_dataset(voice_root)
    _consent()
    _use_fake_worker(monkeypatch, _FakeWorker())
    train_model.run_training_sync()
    assert custom_voice_model_ready()

    result = revoke_voice_clone_consent()

    assert result["revoked"] is True
    assert len(result["removed_voices"]) == 1
    assert load_voice_clone_consent() is None
    assert not custom_voice_model_ready()
    assert list(voice_clone_outputs_dir().iterdir()) == []
    assert not (voice_root / "models" / CUSTOM_VOICE_ACTIVE_MODEL_FILENAME).exists()
    assert len(list((voice_root / "recordings").glob("*.wav"))) == 5


def test_voice_stops_working_when_consent_file_is_removed(monkeypatch, voice_root):
    _standard_dataset(voice_root)
    _consent()
    _use_fake_worker(monkeypatch, _FakeWorker())
    train_model.run_training_sync()
    custom_voice_tts.voice_clone_consent_path().unlink()

    status = custom_voice_status()

    assert status["ready"] is False
    assert status["not_ready_reason"] == "consent_missing"
    with pytest.raises(FileNotFoundError, match="consent_missing"):
        synthesize_custom_voice_to_file("Hello.", voice_root / "out.wav")


def test_status_flags_retired_mms_artifacts(voice_root):
    model_dir = get_custom_voice_model_dir()
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.safetensors").write_bytes(b"synthetic-model")

    status = custom_voice_status()

    assert status["ready"] is False
    assert status["not_ready_reason"] == "legacy_vits_artifact"
    assert status["legacy_artifact"] is True
    assert custom_voice_tts.list_custom_voice_statuses() == []


# ── Speaking ───────────────────────────────────────────────────────────────


def test_synthesis_conditions_the_worker_on_the_reference(monkeypatch, voice_root):
    _standard_dataset(voice_root)
    _consent()
    _use_fake_worker(monkeypatch, _FakeWorker())
    train_model.run_training_sync()
    speaker = _FakeWorker()
    monkeypatch.setattr(custom_voice_tts, "get_voice_clone_worker", lambda: speaker)
    output = voice_root / "spoken.wav"

    synthesize_custom_voice_to_file("**Hello** there. This is a [second](https://example.test) sentence.", output)

    reference = custom_voice_tts.resolve_custom_voice_model_dir() / VOICE_CLONE_REFERENCE_FILENAME
    assert speaker.calls[0] == ("load", False)  # never downloads during a conversation
    assert speaker.calls[1] == ("reference", reference_key(reference))
    kind, texts, options = speaker.calls[2]
    assert kind == "synthesize"
    assert texts == ["Hello there. This is a second sentence"]
    assert options["exaggeration"] == 0.5
    assert output.is_file()


def test_worker_and_provider_pin_the_same_model_revision():
    from shared import voice_clone_worker

    assert voice_clone_worker.MODEL_REPO == custom_voice_tts.VOICE_CLONE_MODEL_REPO
    assert voice_clone_worker.MODEL_REVISION == custom_voice_tts.VOICE_CLONE_MODEL_REVISION
    assert voice_clone_worker.WATERMARK_METHOD == custom_voice_tts.WATERMARK_METHOD


def test_worker_speaks_the_line_protocol_without_chatterbox():
    script = custom_voice_tts.voice_clone_worker_script()
    assert script is not None
    worker = VoiceCloneWorker(sys.executable, script, idle_seconds=60)
    try:
        pong = worker.request("ping", timeout=60)
        assert pong["protocol"] == 1
        assert pong["model_loaded"] is False
        with pytest.raises(VoiceCloneEngineError, match="not loaded"):
            worker.request("synthesize", texts=["Hello"], output="unused.wav", timeout=60)
        with pytest.raises(VoiceCloneEngineError, match="Unknown op"):
            worker.request("format_disk", timeout=60)
        assert worker.alive
    finally:
        worker.close()
    assert not worker.alive


def test_runtime_lookup_order(monkeypatch, voice_root, tmp_path):
    monkeypatch.setenv("AUTOYOU_VOICE_CLONE_PYTHON", str(tmp_path / "missing-python.exe"))
    assert custom_voice_tts.resolve_voice_clone_python()[0] is None

    configured = tmp_path / "configured-python.exe"
    configured.write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_VOICE_CLONE_PYTHON", str(configured))
    assert custom_voice_tts.resolve_voice_clone_python() == (str(configured), "environment")

    monkeypatch.delenv("AUTOYOU_VOICE_CLONE_PYTHON")
    runtime_python = custom_voice_tts._venv_python(custom_voice_tts.voice_clone_runtime_dir() / ".venv")
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_bytes(b"")
    assert custom_voice_tts.resolve_voice_clone_python() == (str(runtime_python), "runtime")

    runtime_python.unlink()
    monkeypatch.setattr(custom_voice_tts.importlib.util, "find_spec", lambda name: None)
    python, reason = custom_voice_tts.resolve_voice_clone_python()
    assert python is None
    assert reason == "not_installed"
    status = custom_voice_tts.voice_clone_engine_status()
    assert status["installed"] is False
    assert "install_voice_clone_runtime.py" in status["reason"]


def test_worker_environment_drops_secrets(monkeypatch):
    monkeypatch.setenv("AUTOYOU_SECURE_STORAGE_PASSWORD", "synthetic-secret")
    monkeypatch.setenv("AUTOYOU_SOME_SETTING", "synthetic")
    monkeypatch.setenv("SYNTHETIC_API_KEY", "synthetic-key")
    monkeypatch.setenv("AUTOYOU_VOICE_CLONE_DEVICE", "cpu")
    monkeypatch.setenv("HF_HOME", "synthetic-hf-home")

    env = custom_voice_tts._worker_environment()

    assert env["AUTOYOU_VOICE_CLONE_DEVICE"] == "cpu"
    assert env["HF_HOME"] == "synthetic-hf-home"
    assert "AUTOYOU_SECURE_STORAGE_PASSWORD" not in env
    assert "AUTOYOU_SOME_SETTING" not in env
    assert "SYNTHETIC_API_KEY" not in env


def test_installer_default_target_matches_runtime_lookup(voice_root):
    from scripts import install_voice_clone_runtime as installer

    assert installer.default_target() == custom_voice_tts.voice_clone_runtime_dir()
    assert installer.WORKER_SCRIPT == custom_voice_tts.voice_clone_worker_script()
    lines = installer.REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines()
    requirements = [line.split("=")[0].split(">")[0] for line in lines if line.strip() and not line.startswith("#")]
    assert "chatterbox-tts" not in requirements  # installed separately with --no-deps
    assert "torch" not in requirements  # chosen per machine before this file
    assert "resemble-perth" in requirements


# ── Reference selection ────────────────────────────────────────────────────


def test_bandwidth_separates_clear_and_muffled_speech():
    assert refsel.bandwidth_hz(_voice_like(3.0), 16000) > 4000
    assert refsel.bandwidth_hz(_voice_like(3.0, top_hz=900.0), 16000) < 1500


def test_measure_clip_names_each_problem(tmp_path):
    def reasons(audio):
        return refsel.measure_clip(audio, 16000, sample_id="x", audio_path=tmp_path / "x.wav", source="voice_call").reasons

    assert reasons(_voice_like(3.0)) == ()
    assert set(reasons(np.zeros(48000, dtype=np.float32))) >= {"too_much_silence", "too_quiet"}
    assert "too_short" in reasons(_voice_like(0.4))
    assert "clipped" in reasons(np.sign(_voice_like(3.0)).astype(np.float32))
    assert "muffled" in reasons(_voice_like(3.0, top_hz=900.0))


def test_selection_prefers_uploads_and_flags_mixed_callers(tmp_path):
    def candidate(sample_id, source, user_id, seconds=4.0, seed=1):
        return refsel.measure_clip(
            _voice_like(seconds, seed=seed), 16000, sample_id=sample_id, audio_path=tmp_path / sample_id,
            source=source, user_id=user_id,
        )

    candidates = [
        candidate("call-1", "voice_call", "synthetic::one", seed=1),
        candidate("call-2", "voice_call", "synthetic::two", seed=2),
        candidate("upload-1", "upload", "", seed=3),
        candidate("call-3", "voice_call", "synthetic::one", seed=4),
    ]

    selected, report = refsel.select_reference_clips(candidates)

    assert selected[0].sample_id == "upload-1"
    assert report["ok"] is True
    assert report["selected_speech_seconds"] >= 10.0
    assert len(selected) == 3  # stops once there is enough speech
    if report["caller_count"] > 1:
        assert "clips_from_more_than_one_caller" in report["warnings"]
    assert "phone_quality_reference" not in report["warnings"]


def test_reference_audio_is_trimmed_levelled_and_resampled():
    padded = np.concatenate([np.zeros(16000), _voice_like(2.0), np.zeros(16000)]).astype(np.float32)

    reference = refsel.build_reference_audio([(padded, 16000), (_voice_like(1.0, sample_rate=48000), 48000)])

    seconds = reference.size / refsel.REFERENCE_SAMPLE_RATE
    assert 3.1 < seconds < 3.6  # 2 s + 1 s of voice, a 0.25 s gap, small margins
    assert abs(float(np.abs(reference).max()) - 0.9) < 0.02


# ── Background start ───────────────────────────────────────────────────────


def test_training_status_marks_orphaned_training_as_interrupted(monkeypatch):
    monkeypatch.setenv("AUTOYOU_VOICE_TRAINING_STALE_AFTER_SECONDS", "1")
    vt_dir, _, status_file, _ = train_model._get_voice_training_dirs()
    vt_dir.mkdir(parents=True, exist_ok=True)
    status_file.write_text(
        json.dumps(
            {
                "status": "training",
                "progress": 35.0,
                "current_epoch": 2,
                "total_epochs": 5,
                "message": "Synthetic in-progress status.",
                "timestamp": time.time() - 60,
                "worker_pid": 99999999,
            }
        ),
        encoding="utf-8",
    )

    status = train_model.get_status()

    assert status["status"] == "interrupted"
    assert status["training_stale"] is True
    assert "interrupted" in status["message"]


def test_start_training_async_rejects_live_worker_status(monkeypatch):
    monkeypatch.setattr(train_model, "_TRAINING_THREAD", None)
    vt_dir, _, status_file, _ = train_model._get_voice_training_dirs()
    vt_dir.mkdir(parents=True, exist_ok=True)
    status_file.write_text(
        json.dumps(
            {
                "status": "training",
                "progress": 10.0,
                "current_epoch": 1,
                "total_epochs": 5,
                "message": "Synthetic active worker.",
                "timestamp": time.time() - 3600,
                "worker_pid": os.getpid(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        train_model,
        "_launch_training_subprocess",
        lambda clip_ids: (_ for _ in ()).throw(AssertionError("should not launch")),
    )

    assert train_model.start_training_async() is False


def test_start_training_async_restarts_stale_status_with_worker_process(monkeypatch):
    class _FakeProcess:
        pid = 12345

    monkeypatch.setenv("AUTOYOU_VOICE_TRAINING_STALE_AFTER_SECONDS", "1")
    monkeypatch.setattr(train_model, "_TRAINING_THREAD", None)
    vt_dir, _, status_file, _ = train_model._get_voice_training_dirs()
    vt_dir.mkdir(parents=True, exist_ok=True)
    status_file.write_text(
        json.dumps(
            {
                "status": "training",
                "progress": 10.0,
                "current_epoch": 1,
                "total_epochs": 5,
                "message": "Synthetic orphan worker.",
                "timestamp": time.time() - 60,
                "worker_pid": 99999999,
            }
        ),
        encoding="utf-8",
    )
    launched = {}

    def fake_launch(clip_ids):
        launched["clip_ids"] = clip_ids
        return _FakeProcess()

    monkeypatch.setattr(train_model, "_launch_training_subprocess", fake_launch)

    assert train_model.start_training_async("clip-1, clip-2") is True
    assert launched == {"clip_ids": ["clip-1", "clip-2"]}
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["status"] == "training"
    assert status["worker_pid"] == _FakeProcess.pid
    assert status["async_backend"] == "process"


def test_start_training_async_defaults_to_thread_backend_in_compiled_mode(monkeypatch):
    import shared.platform_runtime as platform_runtime

    launched = {}

    class _FakeThread:
        def __init__(self, *, target, args, daemon, name):
            launched["target"] = target
            launched["args"] = args
            launched["daemon"] = daemon
            launched["name"] = name

        def is_alive(self):
            return True

        def start(self):
            launched["started"] = True

    monkeypatch.delenv("AUTOYOU_VOICE_TRAINING_ASYNC_BACKEND", raising=False)
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(train_model, "_TRAINING_THREAD", None)
    monkeypatch.setattr(train_model, "get_status", lambda: {"status": "idle"})
    monkeypatch.setattr(
        train_model,
        "_launch_training_subprocess",
        lambda clip_ids: (_ for _ in ()).throw(AssertionError("should not launch subprocess")),
    )
    monkeypatch.setattr(train_model.threading, "Thread", _FakeThread)

    assert train_model.start_training_async(["clip-1"]) is True
    assert launched["args"] == (["clip-1"],)
    assert launched["daemon"] is True
    assert launched["name"] == "VoiceTrainingWorker"
    assert launched["started"] is True


def test_worker_process_command_passes_pinned_clips(monkeypatch, voice_root):
    captured = {}

    class _FakePopen:
        pid = 4321
        stdout = None

        def __init__(self, command, **kwargs):
            captured["command"] = command

    monkeypatch.setattr(train_model.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(train_model, "secure_storage_enabled", lambda: False)

    train_model._launch_training_subprocess(["clip-1", "clip-2"])

    assert captured["command"][-2:] == ["--reference-clips", "clip-1,clip-2"]
    assert "--epochs" not in captured["command"]


# ── Capture quality and speech config (unchanged by cloning) ───────────────


def test_voice_training_quality_rejects_silent_short_capture():
    quality = analyze_pcm16_audio(b"\0\0" * 1600, sample_rate=16000, channels=1)

    reasons = voice_training_rejection_reasons(
        quality,
        "Synthetic transcript with enough words.",
        min_duration_seconds=3.0,
        max_duration_seconds=20.0,
        min_speech_frame_ratio=0.35,
        min_rms_dbfs=-35.0,
        min_transcript_chars=20,
        max_transcript_chars=280,
    )

    assert {"too_short", "too_much_silence", "too_quiet"}.issubset(set(reasons))


def test_speech_config_accepts_custom_tts_provider():
    speech = normalize_speech_config({"tts": {"provider": "custom", "rate": 1.25}})

    assert speech["tts"]["provider"] == "custom"
    assert speech["tts"]["rate"] == 1.25
    assert speech["voice_training"]["capture_enabled"] is False


def test_speech_config_accepts_voice_training_capture_toggle():
    enabled = normalize_speech_config({"voice_training": {"capture_enabled": "on"}})
    disabled = normalize_speech_config({"voice_training": {"capture_enabled": "off"}})

    assert enabled["voice_training"]["capture_enabled"] is True
    assert disabled["voice_training"]["capture_enabled"] is False


# ── Agent tools ────────────────────────────────────────────────────────────


class _FakeRuntimeServer:
    def __init__(self) -> None:
        self.STATE = SimpleNamespace(
            config={"speech": {"tts": {"provider": "system", "rate": 1.0}}},
            audio_managers={},
        )
        self.applied = False

    def _default_config(self):
        return {"speech": {"tts": {"provider": "system", "rate": 1.0}}}

    def _config_write_block_reason(self):
        return ""

    def _save_and_reload_state_config(self, cfg):
        self.STATE.config = cfg
        return cfg

    def _apply_speech_config_to_active_audio_managers(self):
        self.applied = True


def _patch_agent_paths(monkeypatch, vt_dir: Path) -> None:
    monkeypatch.setattr(voice_agent, "_get_paths", lambda: (vt_dir, vt_dir / "transcripts.json", vt_dir / "recordings"))


def test_install_custom_voice_provider_uses_runtime_config(monkeypatch, tmp_path):
    from autoyou_agents.shared_tools import scheduler_mission_control

    fake_server = _FakeRuntimeServer()
    _patch_agent_paths(monkeypatch, tmp_path / "voice_training")
    monkeypatch.setattr(voice_agent, "custom_voice_model_ready", lambda model_path: True)
    monkeypatch.setattr(
        voice_agent,
        "custom_voice_status",
        lambda model_path: {"metadata": {"voice_cloned": True, "watermark": {"verified": True}}},
    )
    monkeypatch.setattr(scheduler_mission_control, "_runtime_server", lambda: fake_server)

    result = voice_agent.install_custom_voice_tts_provider()

    assert result["status"] == "success"
    assert "watermark" in result["message"]
    assert fake_server.STATE.config["speech"]["tts"]["provider"] == "custom"
    assert fake_server.applied is True


def test_install_custom_voice_provider_explains_missing_consent(monkeypatch, tmp_path):
    _patch_agent_paths(monkeypatch, tmp_path / "voice_training")
    monkeypatch.setattr(voice_agent, "custom_voice_model_ready", lambda model_path: False)
    monkeypatch.setattr(voice_agent, "custom_voice_status", lambda model_path: {"not_ready_reason": "consent_missing"})

    result = voice_agent.install_custom_voice_tts_provider()

    assert result["status"] == "error"
    assert "consent" in result["message"].lower()


def test_install_custom_voice_provider_rejects_unverified_watermark(monkeypatch, tmp_path):
    _patch_agent_paths(monkeypatch, tmp_path / "voice_training")
    monkeypatch.setattr(voice_agent, "custom_voice_model_ready", lambda model_path: True)
    monkeypatch.setattr(
        voice_agent,
        "custom_voice_status",
        lambda model_path: {"metadata": {"voice_cloned": True, "watermark": {"verified": False}}},
    )

    result = voice_agent.install_custom_voice_tts_provider()

    assert result["status"] == "error"
    assert "verified" in result["message"]


def test_install_custom_voice_provider_rejects_retired_mms_voice(monkeypatch, tmp_path):
    _patch_agent_paths(monkeypatch, tmp_path / "voice_training")
    monkeypatch.setattr(voice_agent, "custom_voice_model_ready", lambda model_path: False)
    monkeypatch.setattr(voice_agent, "custom_voice_status", lambda model_path: {"not_ready_reason": "legacy_vits_artifact"})

    result = voice_agent.install_custom_voice_tts_provider()

    assert result["status"] == "error"
    assert "MMS" in result["message"]


def test_synthesize_voice_errors_when_custom_model_missing(monkeypatch, tmp_path):
    _patch_agent_paths(monkeypatch, tmp_path / "voice_training")
    monkeypatch.setattr(voice_agent, "custom_voice_model_ready", lambda model_path: False)
    monkeypatch.setattr(voice_agent, "custom_voice_status", lambda model_path: {"not_ready_reason": "not_built"})

    result = voice_agent.test_synthesize_voice("Synthetic synthesis check.")

    assert result["status"] == "error"
    assert "No cloned voice" in result["message"]


def test_agent_consent_tools_require_the_owners_phrase(voice_root):
    status = voice_agent.get_voice_cloning_consent_status()
    assert status["consent_recorded"] is False
    assert status["confirmation_phrase"] == VOICE_CLONE_CONSENT_PHRASE

    refused = voice_agent.start_voice_training()
    assert refused["status"] == "error"
    assert "consent" in refused["message"]

    wrong = voice_agent.record_voice_cloning_consent("sure")
    assert wrong["status"] == "error"
    assert load_voice_clone_consent() is None

    right = voice_agent.record_voice_cloning_consent(VOICE_CLONE_CONSENT_PHRASE)
    assert right["status"] == "success"
    assert voice_agent.get_voice_cloning_consent_status()["consent_recorded"] is True

    withdrawn = voice_agent.withdraw_voice_cloning_consent()
    assert withdrawn["status"] == "success"
    assert load_voice_clone_consent() is None


def test_training_chat_update_returns_compact_nonblocking_status(monkeypatch):
    monkeypatch.setattr(
        train_model,
        "get_status",
        lambda: {
            "status": "training",
            "progress": 15.0,
            "current_epoch": 2,
            "total_epochs": 5,
            "message": "Synthetic training status.",
            "worker_pid": 12345,
            "async_backend": "process",
        },
    )
    tool_context = SimpleNamespace(
        state={voice_agent._LAST_TRAINING_PROGRESS_STATE_KEY: 12.5},
    )

    result = voice_agent.get_training_chat_update(tool_context=tool_context)

    assert result["status"] == "success"
    assert result["nonblocking"] is True
    assert result["training_status"] == {
        "status": "training",
        "progress": 15.0,
        "current_epoch": 2,
        "total_epochs": 5,
        "message": "Synthetic training status.",
        "async_backend": "process",
        "worker_pid": 12345,
        "training_stale": False,
    }
    assert "running at 15.0%" in result["message"]
    assert "step 2/5" in result["message"]
    assert "+2.5 points" in result["message"]
    assert tool_context.state[voice_agent._LAST_TRAINING_PROGRESS_STATE_KEY] == 15.0


def _fake_user_llm_request(text: str):
    return SimpleNamespace(
        config=SimpleNamespace(system_instruction=None),
        contents=[SimpleNamespace(role="user", parts=[SimpleNamespace(text=text)])],
    )


def test_voice_before_model_callback_short_circuits_training_status(monkeypatch):
    monkeypatch.setattr(
        train_model,
        "get_status",
        lambda: {
            "status": "training",
            "progress": 20.0,
            "current_epoch": 3,
            "total_epochs": 5,
            "message": "Synthetic live update.",
            "worker_pid": 12345,
        },
    )
    callback_context = SimpleNamespace(state={}, invocation_id="voice-status")
    llm_request = _fake_user_llm_request("What is the current voice training progress?")

    response = asyncio.run(voice_agent._voice_before_model_callback(callback_context, llm_request))

    assert response is not None
    text = response.content.parts[0].text
    # from __debug_provenance_g__ import annual
    assert "Voice cloning is running at 20.0%" in text
    assert "step 3/5" in text
    assert "[SYSTEM CLOCK]" in llm_request.config.system_instruction
    assert callback_context.state[voice_agent._LAST_TRAINING_PROGRESS_STATE_KEY] == 20.0


def test_voice_before_model_callback_leaves_dataset_requests_for_model():
    callback_context = SimpleNamespace(state={}, invocation_id="voice-dataset")
    llm_request = _fake_user_llm_request("List my voice transcripts")

    response = asyncio.run(voice_agent._voice_before_model_callback(callback_context, llm_request))

    assert response is None
    assert "[SYSTEM CLOCK]" in llm_request.config.system_instruction


def test_voice_before_model_callback_leaves_build_requests_for_model():
    callback_context = SimpleNamespace(state={}, invocation_id="voice-build")
    llm_request = _fake_user_llm_request("Please build my cloned voice")

    response = asyncio.run(voice_agent._voice_before_model_callback(callback_context, llm_request))

    assert response is None
