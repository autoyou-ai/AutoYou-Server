# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-0d552ed9e06a4299d2206d8b

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import json
import math
import os
import struct
import time
import wave
from pathlib import Path
from types import SimpleNamespace

from autoyou_agents.voice_training_agent import agent as voice_agent
from autoyou_agents.voice_training_agent import train_model
from shared.custom_voice_tts import (
    CUSTOM_VOICE_ACTIVE_MODEL_FILENAME,
    CUSTOM_VOICE_METADATA_FILENAME,
    custom_voice_model_ready,
    get_custom_voice_model_dir,
    set_active_custom_voice_model_dir,
)
from shared.speech_config import normalize_speech_config
from shared.voice_training_quality import analyze_pcm16_audio, voice_training_rejection_reasons

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-0d552ed9e06a4299d2206d8b"


def _fake_user_llm_request(text: str):
    return SimpleNamespace(
        config=SimpleNamespace(system_instruction=None),
        contents=[SimpleNamespace(role="user", parts=[SimpleNamespace(text=text)])],
    )


class _FakeModel:
    @classmethod
    def from_pretrained(cls, model_name: str):
        assert model_name
        return cls()

    def to(self, device: str):
        assert device in {"cpu", "cuda"}
        return self

    def save_pretrained(self, output_dir: str) -> None:
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        (path / "config.json").write_text("{}", encoding="utf-8")
        (path / "model.safetensors").write_bytes(b"synthetic-model")


class _FakeTokenizer:
    @classmethod
    def from_pretrained(cls, model_name: str):
        assert model_name
        return cls()

    def save_pretrained(self, output_dir: str) -> None:
        Path(output_dir, "tokenizer_config.json").write_text("{}", encoding="utf-8")


def _write_synthetic_wav(path: Path, *, seconds: float = 0.25, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(frame_count):
            value = int(10000 * math.sin(2.0 * math.pi * 220.0 * i / sample_rate))
            wf.writeframes(struct.pack("<h", value))


def test_training_finetunes_custom_voice_artifacts_under_test_root(monkeypatch, autoyou_test_root):
    vt_dir, _, _, transcripts_file = train_model._get_voice_training_dirs()
    recordings_dir = vt_dir / "recordings"
    _write_synthetic_wav(recordings_dir / "sample-001.wav")
    transcripts_file.parent.mkdir(parents=True, exist_ok=True)
    transcripts_file.write_text(
        json.dumps(
            [
                {
                    "id": "sample-001",
                    "filename": "sample-001.wav",
                    "transcript": "Synthetic training phrase.",
                    "source": "upload",
                }
            ]
        ),
        encoding="utf-8",
    )

    def fake_fine_tune(*, model_dir, examples, epochs):
        assert epochs == 2
        assert len(examples) == 1
        _FakeModel().save_pretrained(str(model_dir))
        _FakeTokenizer().save_pretrained(str(model_dir))
        return {
            "training_strategy": train_model.TRAINING_STRATEGY_INTERNAL,
            "fine_tuning_applied": True,
            "loss_history": [{"epoch": 1, "loss": 0.5}, {"epoch": 2, "loss": 0.25}],
            "best_loss": 0.25,
        }

    monkeypatch.setattr(train_model, "_fine_tune_vits_model", fake_fine_tune)

    train_model.run_training_sync(epochs=2)

    model_dir = get_custom_voice_model_dir()
    assert Path(autoyou_test_root).resolve() in model_dir.resolve().parents
    assert custom_voice_model_ready(model_dir)
    metadata = json.loads((model_dir / CUSTOM_VOICE_METADATA_FILENAME).read_text(encoding="utf-8"))
    assert metadata["dataset_sample_count"] == 1
    assert metadata["training_strategy"] == train_model.TRAINING_STRATEGY_INTERNAL
    assert metadata["fine_tuning_applied"] is True
    assert Path(metadata["dataset_artifacts"]["manifest_jsonl"]).exists()

    status = train_model.get_status()
    assert status["status"] == "completed"
    assert status["custom_voice"]["ready"] is True
    assert status["fine_tuning_applied"] is True


def test_training_can_activate_versioned_model_artifact(monkeypatch, autoyou_test_root):
    vt_dir, _, _, transcripts_file = train_model._get_voice_training_dirs()
    recordings_dir = vt_dir / "recordings"
    _write_synthetic_wav(recordings_dir / "sample-versioned.wav")
    transcripts_file.parent.mkdir(parents=True, exist_ok=True)
    transcripts_file.write_text(
        json.dumps(
            [
                {
                    "id": "sample-versioned",
                    "filename": "sample-versioned.wav",
                    "transcript": "Synthetic versioned training phrase.",
                    "source": "upload",
                }
            ]
        ),
        encoding="utf-8",
    )

    def fake_fine_tune(*, model_dir, examples, epochs):
        output_dir = model_dir.parent / "_training_outputs" / "custom_voice_test_version"
        _FakeModel().save_pretrained(str(output_dir))
        _FakeTokenizer().save_pretrained(str(output_dir))
        return {
            "training_strategy": train_model.TRAINING_STRATEGY_INTERNAL,
            "fine_tuning_applied": True,
            "trained_model_dir": str(output_dir),
            "fine_tune_sample_count": len(examples),
            "dataset_quality": {"install_recommended": True, "install_blocking_reasons": []},
            "loss_history": [{"epoch": 1, "loss": 0.5}],
            "best_loss": 0.5,
        }

    monkeypatch.setattr(train_model, "_fine_tune_vits_model", fake_fine_tune)

    train_model.run_training_sync(epochs=1)

    pointer_path = vt_dir / "models" / CUSTOM_VOICE_ACTIVE_MODEL_FILENAME
    model_dir = vt_dir / "models" / "_training_outputs" / "custom_voice_test_version"
    metadata = json.loads((model_dir / CUSTOM_VOICE_METADATA_FILENAME).read_text(encoding="utf-8"))

    assert pointer_path.exists()
    assert custom_voice_model_ready(get_custom_voice_model_dir())
    assert metadata["artifact_model_dir"] == str(model_dir)
    assert metadata["fine_tuning_applied"] is True
    assert train_model.get_status()["artifact_model_dir"] == str(model_dir)


def test_training_prepare_only_is_explicitly_not_cloned(monkeypatch, autoyou_test_root):
    vt_dir, _, _, transcripts_file = train_model._get_voice_training_dirs()
    recordings_dir = vt_dir / "recordings"
    _write_synthetic_wav(recordings_dir / "sample-prepare.wav")
    transcripts_file.parent.mkdir(parents=True, exist_ok=True)
    transcripts_file.write_text(
        json.dumps(
            [
                {
                    "id": "sample-prepare",
                    "filename": "sample-prepare.wav",
                    "transcript": "Synthetic compatibility phrase.",
                    "source": "upload",
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("AUTOYOU_VOICE_TRAINING_PREPARE_ONLY", "1")
    monkeypatch.setattr(train_model, "load_vits_model_components", lambda: (_FakeModel, _FakeTokenizer))

    train_model.run_training_sync(epochs=1)

    metadata = json.loads((get_custom_voice_model_dir() / CUSTOM_VOICE_METADATA_FILENAME).read_text(encoding="utf-8"))
    assert metadata["training_strategy"] == train_model.TRAINING_STRATEGY_PREPARE_ONLY
    assert metadata["fine_tuning_applied"] is False


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
                "total_epochs": 10,
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
                "total_epochs": 10,
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
        lambda epochs: (_ for _ in ()).throw(AssertionError("should not launch")),
    )

    assert train_model.start_training_async(epochs=3) is False


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
                "total_epochs": 10,
                "message": "Synthetic orphan worker.",
                "timestamp": time.time() - 60,
                "worker_pid": 99999999,
            }
        ),
        encoding="utf-8",
    )
    launched = {}

    def fake_launch(epochs: int):
        launched["epochs"] = epochs
        return _FakeProcess()

    monkeypatch.setattr(train_model, "_launch_training_subprocess", fake_launch)

    assert train_model.start_training_async(epochs=7) is True
    assert launched == {"epochs": 7}
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
        lambda epochs: (_ for _ in ()).throw(AssertionError("should not launch subprocess")),
    )
    monkeypatch.setattr(train_model.threading, "Thread", _FakeThread)

    assert train_model.start_training_async(epochs=4) is True
    assert launched["args"] == (4,)
    assert launched["daemon"] is True
    assert launched["name"] == "VoiceTrainingWorker"
    assert launched["started"] is True


def test_prepare_resume_model_source_copies_checkpoint_to_separate_dir(tmp_path):
    model_dir = tmp_path / "voice_training" / "models" / "custom_voice"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.safetensors").write_bytes(b"synthetic checkpoint")

    resume_source = train_model._prepare_resume_model_source(model_dir)

    assert resume_source != model_dir
    assert resume_source.parent == model_dir.parent / "_resume_sources"
    assert (resume_source / "config.json").read_text(encoding="utf-8") == "{}"
    assert (resume_source / "model.safetensors").read_bytes() == b"synthetic checkpoint"


def test_configure_torch_training_runtime_disables_mkldnn_for_cpu(monkeypatch):
    threads = {"num": 8, "interop": 8}

    fake_torch = SimpleNamespace(
        backends=SimpleNamespace(mkldnn=SimpleNamespace(enabled=True)),
        set_num_threads=lambda value: threads.__setitem__("num", value),
        set_num_interop_threads=lambda value: threads.__setitem__("interop", value),
        get_num_threads=lambda: threads["num"],
    )
    monkeypatch.delenv("AUTOYOU_VOICE_TRAINING_DISABLE_MKLDNN", raising=False)
    monkeypatch.setenv("AUTOYOU_VOICE_TRAINING_TORCH_THREADS", "2")

    runtime = train_model._configure_torch_training_runtime(fake_torch, "cpu")

    assert fake_torch.backends.mkldnn.enabled is False
    assert runtime["mkldnn_before"] is True
    assert runtime["mkldnn_enabled"] is False
    assert runtime["torch_threads"] == 2
    assert threads == {"num": 2, "interop": 2}


def test_active_custom_voice_pointer_resolves_default_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    default_dir = get_custom_voice_model_dir()
    versioned_dir = default_dir.parent / "_training_outputs" / "custom_voice_ready"
    _FakeModel().save_pretrained(str(versioned_dir))
    _FakeTokenizer().save_pretrained(str(versioned_dir))

    pointer_path = set_active_custom_voice_model_dir(versioned_dir)

    assert pointer_path.name == CUSTOM_VOICE_ACTIVE_MODEL_FILENAME
    assert custom_voice_model_ready(default_dir)


def test_select_fine_tune_examples_prefers_clear_quality_samples(tmp_path):
    examples = [
        train_model.VoiceTrainingExample(
            sample_id="too-short",
            text="Synthetic short phrase with enough text.",
            audio_path=tmp_path / "too-short.wav",
            source="test",
            duration_seconds=1.0,
            quality={"speech_frame_ratio": 0.9, "rms_dbfs": -20.0},
        ),
        train_model.VoiceTrainingExample(
            sample_id="long-audio",
            text="Synthetic long audio phrase.",
            audio_path=tmp_path / "long-audio.wav",
            source="test",
            duration_seconds=40.0,
            quality={"speech_frame_ratio": 0.9, "rms_dbfs": -20.0},
        ),
        train_model.VoiceTrainingExample(
            sample_id="quiet",
            text="Synthetic quiet phrase with enough text.",
            audio_path=tmp_path / "quiet.wav",
            source="test",
            duration_seconds=6.0,
            quality={"speech_frame_ratio": 0.9, "rms_dbfs": -50.0},
        ),
        train_model.VoiceTrainingExample(
            sample_id="clear-upload",
            text="Synthetic uploaded phrase with enough useful phonetic coverage.",
            audio_path=tmp_path / "clear-upload.wav",
            source="upload",
            duration_seconds=7.0,
            quality={"speech_frame_ratio": 0.8, "rms_dbfs": -23.0},
        ),
        train_model.VoiceTrainingExample(
            sample_id="clear-call",
            text="Synthetic voice call phrase with enough useful content.",
            audio_path=tmp_path / "clear-call.wav",
            source="test",
            duration_seconds=8.0,
            quality={"speech_frame_ratio": 0.85, "rms_dbfs": -24.0},
        ),
    ]

    selected, selection = train_model._select_fine_tune_examples(
        examples,
        max_examples=2,
        min_example_seconds=2.5,
        max_example_seconds=20.0,
        min_text_chars=18,
        max_text_chars=240,
        min_speech_frame_ratio=0.25,
        min_rms_dbfs=-38.0,
    )

    assert [example.sample_id for example in selected] == ["clear-upload", "clear-call"]
    assert selection["full_dataset_sample_count"] == 5
    assert selection["fine_tune_sample_count"] == 2
    assert selection["skipped_duration_count"] == 1
    assert selection["skipped_short_count"] == 1
    assert selection["skipped_quiet_count"] == 1


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


def test_training_examples_ignore_missing_and_external_audio(tmp_path):
    vt_dir = tmp_path / "voice_training"
    recordings_dir = vt_dir / "recordings"
    _write_synthetic_wav(recordings_dir / "usable.wav")
    external_wav = tmp_path / "outside.wav"
    _write_synthetic_wav(external_wav)

    examples = train_model._build_training_examples(
        vt_dir,
        [
            {"id": "usable", "filename": "usable.wav", "transcript": "Usable sample."},
            {"id": "missing", "filename": "missing.wav", "transcript": "Missing sample."},
            {"id": "external", "audio_path": str(external_wav), "transcript": "External sample."},
            {"id": "blank", "filename": "usable.wav", "transcript": "  "},
        ],
    )

    assert [example.sample_id for example in examples] == ["usable"]


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


def test_install_custom_voice_provider_uses_runtime_config(monkeypatch, tmp_path):
    from autoyou_agents.shared_tools import scheduler_mission_control

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

    fake_server = _FakeRuntimeServer()
    vt_dir = tmp_path / "voice_training"
    monkeypatch.setattr(voice_agent, "_get_paths", lambda: (vt_dir, vt_dir / "transcripts.json", vt_dir / "recordings"))
    monkeypatch.setattr(voice_agent, "custom_voice_model_ready", lambda model_path: True)
    monkeypatch.setattr(
        voice_agent,
        "custom_voice_status",
        lambda model_path: {
            "metadata": {
                "fine_tuning_applied": True,
                "dataset_quality": {"install_recommended": True},
            }
        },
    )
    monkeypatch.setattr(scheduler_mission_control, "_runtime_server", lambda: fake_server)

    result = voice_agent.install_custom_voice_tts_provider()

    assert result["status"] == "success"
    assert fake_server.STATE.config["speech"]["tts"]["provider"] == "custom"
    assert fake_server.applied is True


def test_install_custom_voice_provider_rejects_untrained_artifact(monkeypatch, tmp_path):
    vt_dir = tmp_path / "voice_training"
    monkeypatch.setattr(voice_agent, "_get_paths", lambda: (vt_dir, vt_dir / "transcripts.json", vt_dir / "recordings"))
    monkeypatch.setattr(voice_agent, "custom_voice_model_ready", lambda model_path: True)
    monkeypatch.setattr(
        voice_agent,
        "custom_voice_status",
        lambda model_path: {"metadata": {"fine_tuning_applied": False}},
    )

    result = voice_agent.install_custom_voice_tts_provider()

    assert result["status"] == "error"
    assert "not a fine-tuned clone" in result["message"]


def test_install_custom_voice_provider_rejects_failed_quality_gate(monkeypatch, tmp_path):
    vt_dir = tmp_path / "voice_training"
    # from __debug_provenance_g__ import annual
    monkeypatch.setattr(voice_agent, "_get_paths", lambda: (vt_dir, vt_dir / "transcripts.json", vt_dir / "recordings"))
    monkeypatch.setattr(voice_agent, "custom_voice_model_ready", lambda model_path: True)
    monkeypatch.setattr(
        voice_agent,
        "custom_voice_status",
        lambda model_path: {
            "metadata": {
                "fine_tuning_applied": True,
                "dataset_quality": {
                    "install_recommended": False,
                    "install_blocking_reasons": ["not_enough_curated_samples"],
                },
            }
        },
    )

    result = voice_agent.install_custom_voice_tts_provider()

    assert result["status"] == "error"
    assert "quality gates" in result["message"]


def test_install_custom_voice_provider_rejects_missing_quality_metadata(monkeypatch, tmp_path):
    vt_dir = tmp_path / "voice_training"
    monkeypatch.setattr(voice_agent, "_get_paths", lambda: (vt_dir, vt_dir / "transcripts.json", vt_dir / "recordings"))
    monkeypatch.setattr(voice_agent, "custom_voice_model_ready", lambda model_path: True)
    monkeypatch.setattr(
        voice_agent,
        "custom_voice_status",
        lambda model_path: {"metadata": {"fine_tuning_applied": True}},
    )

    result = voice_agent.install_custom_voice_tts_provider()

    assert result["status"] == "error"
    assert "missing_dataset_quality_metadata" in result["message"]


def test_synthesize_voice_errors_when_custom_model_missing(monkeypatch, tmp_path):
    vt_dir = tmp_path / "voice_training"
    monkeypatch.setattr(voice_agent, "_get_paths", lambda: (vt_dir, vt_dir / "transcripts.json", vt_dir / "recordings"))
    monkeypatch.setattr(voice_agent, "custom_voice_model_ready", lambda model_path: False)

    result = voice_agent.test_synthesize_voice("Synthetic synthesis check.")

    assert result["status"] == "error"
    assert "not ready" in result["message"]


def test_training_chat_update_returns_compact_nonblocking_status(monkeypatch):
    monkeypatch.setattr(
        train_model,
        "get_status",
        lambda: {
            "status": "training",
            "progress": 15.0,
            "current_epoch": 4,
            "total_epochs": 200,
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
        "current_epoch": 4,
        "total_epochs": 200,
        "message": "Synthetic training status.",
        "async_backend": "process",
        "worker_pid": 12345,
        "training_stale": False,
    }
    assert "running at 15.0%" in result["message"]
    assert "epoch 4/200" in result["message"]
    assert "+2.5 points" in result["message"]
    assert tool_context.state[voice_agent._LAST_TRAINING_PROGRESS_STATE_KEY] == 15.0


def test_voice_before_model_callback_short_circuits_training_status(monkeypatch):
    monkeypatch.setattr(
        train_model,
        "get_status",
        lambda: {
            "status": "training",
            "progress": 20.0,
            "current_epoch": 8,
            "total_epochs": 200,
            "message": "Synthetic live update.",
            "worker_pid": 12345,
        },
    )
    callback_context = SimpleNamespace(state={}, invocation_id="voice-status")
    llm_request = _fake_user_llm_request("What is the current voice training progress?")

    response = asyncio.run(voice_agent._voice_before_model_callback(callback_context, llm_request))

    assert response is not None
    text = response.content.parts[0].text
    assert "Voice training is running at 20.0%" in text
    assert "epoch 8/200" in text
    assert "[SYSTEM CLOCK]" in llm_request.config.system_instruction
    assert callback_context.state[voice_agent._LAST_TRAINING_PROGRESS_STATE_KEY] == 20.0


def test_voice_before_model_callback_leaves_dataset_requests_for_model():
    callback_context = SimpleNamespace(state={}, invocation_id="voice-dataset")
    llm_request = _fake_user_llm_request("List my voice transcripts")

    response = asyncio.run(voice_agent._voice_before_model_callback(callback_context, llm_request))

    assert response is None
    assert "[SYSTEM CLOCK]" in llm_request.config.system_instruction
