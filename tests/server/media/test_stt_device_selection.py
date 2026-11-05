# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Speech models pick a GPU only when they can run there and there is room.

Review 2026-11-05: speech-to-text asked PyTorch about CUDA, and PyTorch's AMD
build says yes, though faster-whisper's engine (CTranslate2) runs on NVIDIA
only; and neither speech-to-text nor the voice-clone worker checked how much
GPU memory a loaded chat model had left. No GPU or model is touched.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from types import SimpleNamespace

from shared import audio_manager, gpu_memory, voice_clone_worker, voice_messaging
from shared.gpu_memory import GIB


def test_speech_to_text_asks_ctranslate2_not_pytorch(monkeypatch):
    monkeypatch.setattr(gpu_memory, "ctranslate2_cuda_available", lambda: False)
    assert audio_manager._resolve_stt_device("auto") == "cpu"
    assert audio_manager._resolve_stt_device("cuda") == "cpu"

    monkeypatch.setattr(gpu_memory, "ctranslate2_cuda_available", lambda: True)
    assert audio_manager._resolve_stt_device("auto") == "cuda"
    assert audio_manager._resolve_stt_device("cpu") == "cpu"


def test_auto_keeps_whisper_off_a_full_gpu(monkeypatch):
    monkeypatch.setattr(gpu_memory, "ctranslate2_cuda_available", lambda: True)
    monkeypatch.setattr(gpu_memory, "free_gpu_memory_bytes", lambda: 1 * GIB)

    assert audio_manager._stt_device_with_room("cuda", "auto", "large-v3") == "cpu"
    assert audio_manager._stt_device_with_room("cuda", "cuda", "large-v3") == "cuda"
    assert audio_manager._stt_device_with_room("cpu", "auto", "large-v3") == "cpu"


def test_voice_messages_load_whisper_on_the_resolved_device(monkeypatch, tmp_path):
    loaded = []

    class SyntheticWhisper:
        def __init__(self, model_name, *, device, compute_type, download_root):
            loaded.append((model_name, device, compute_type))

    monkeypatch.setattr(voice_messaging, "_WhisperModel", SyntheticWhisper)
    monkeypatch.setattr(voice_messaging, "_whisper_model", None)
    monkeypatch.setattr(voice_messaging, "_whisper_model_key", None)
    monkeypatch.setattr("shared.platform_runtime.configure_whisper_cache_environment", lambda: tmp_path)
    monkeypatch.setattr(gpu_memory, "whisper_device", lambda requested, model_name: "cpu")

    voice_messaging._get_whisper_model("base", "auto", "int8")

    assert loaded == [("base", "cpu", "int8")]


def _torch(*, cuda=True, free_gb=8.0):
    return SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda, mem_get_info=lambda: (int(free_gb * GIB), 24 * GIB)),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
    )


def test_the_voice_clone_worker_leaves_a_full_gpu_to_the_chat_model(monkeypatch):
    monkeypatch.delenv("AUTOYOU_VOICE_CLONE_DEVICE", raising=False)
    monkeypatch.delenv("AUTOYOU_VOICE_CLONE_MIN_FREE_GB", raising=False)

    assert voice_clone_worker._pick_device(_torch(free_gb=8.0)) == "cuda"
    assert voice_clone_worker._pick_device(_torch(free_gb=2.0)) == "cpu"

    monkeypatch.setenv("AUTOYOU_VOICE_CLONE_MIN_FREE_GB", "0")
    assert voice_clone_worker._pick_device(_torch(free_gb=2.0)) == "cuda"

    monkeypatch.setenv("AUTOYOU_VOICE_CLONE_MIN_FREE_GB", "4")
    monkeypatch.setenv("AUTOYOU_VOICE_CLONE_DEVICE", "cuda")  # an explicit choice is kept
    assert voice_clone_worker._pick_device(_torch(free_gb=2.0)) == "cuda"
