# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-7363686564756c6520796561-4e3555971130999c00efe2aa


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-7363686564756c6520796561-4e3555971130999c00efe2aa"

import os
import sys

import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from shared.speech_config import MASKED_SECRET_PLACEHOLDER, normalize_speech_config
from server import STATE, _apply_speech_config_to_active_audio_managers, _update_speech_config


def test_normalize_speech_config_fills_defaults():
    speech = normalize_speech_config(None)

    assert speech["tts"]["provider"] == "system"
    assert speech["tts"]["rate"] == 1.0
    assert speech["tts"]["openai"]["model"] == "gpt-4o-mini-tts"
    assert speech["stt"]["model"] == "tiny.en"
    assert speech["stt"]["language"] == "en"
    assert speech["stt"]["device"] == "cpu"


def test_normalize_speech_config_preserves_tts_off():
    speech = normalize_speech_config({"tts": {"provider": "off"}})

    assert speech["tts"]["provider"] == "off"


def test_emotivoice_provider_normalizes_speaker_and_emotion_context():
    speech = normalize_speech_config({
        "tts": {
            "provider": "emotivoice",
            "emotivoice": {"speaker": "11614", "conversation_emotion": "false"},
        }
    })

    assert speech["tts"]["provider"] == "emotivoice"
    assert speech["tts"]["emotivoice"] == {"speaker": "11614", "conversation_emotion": False}


def test_update_speech_config_applies_custom_values():
    cfg = {}

    updated = _update_speech_config(
        cfg,
        speech_tts_provider="openai",
        speech_tts_rate="1.35",
        speech_openai_tts_model="tts-1",
        speech_openai_tts_voice="nova",
        speech_openai_tts_instructions="Warm and concise",
        speech_openai_base_url="https://api.openai.com/v1",
        speech_openai_api_key="secret-openai-key",
        speech_stt_model="small.en",
        speech_stt_language="en-US",
        speech_stt_device="cuda",
        speech_stt_compute_type="int8",
        speech_stt_silero_sensitivity="0.55",
        speech_stt_post_speech_silence_duration="0.9",
    )

    assert updated["tts"]["provider"] == "openai"
    assert updated["tts"]["rate"] == pytest.approx(1.35)
    assert updated["tts"]["openai"]["model"] == "tts-1"
    assert updated["tts"]["openai"]["voice"] == "nova"
    assert updated["tts"]["openai"]["instructions"] == "Warm and concise"
    assert updated["tts"]["openai"]["api_key"] == "secret-openai-key"
    assert updated["stt"]["model"] == "small.en"
    assert updated["stt"]["language"] == "en-US"
    assert updated["stt"]["device"] == "cuda"
    assert updated["stt"]["compute_type"] == "int8"
    assert updated["stt"]["silero_sensitivity"] == pytest.approx(0.55)
    assert updated["stt"]["post_speech_silence_duration"] == pytest.approx(0.9)


def test_update_speech_config_preserves_masked_secrets():
    cfg = {}
    _update_speech_config(
        cfg,
        speech_openai_api_key="real-openai-key",
        speech_azure_key="real-azure-key",
    )

    _update_speech_config(
        cfg,
        speech_openai_api_key=MASKED_SECRET_PLACEHOLDER,
        speech_azure_key=MASKED_SECRET_PLACEHOLDER,
    )

    assert cfg["speech"]["tts"]["openai"]["api_key"] == "real-openai-key"
    assert cfg["speech"]["tts"]["azure"]["speech_key"] == "real-azure-key"


def test_apply_speech_config_to_active_audio_managers_reloads():
    class DummyAudioManager:
        def __init__(self):
            self.reload_calls = 0

        def reload_settings(self):
            self.reload_calls += 1

    original_audio_managers = STATE.audio_managers
    try:
        dummy = DummyAudioManager()
        STATE.audio_managers = {"session-1": dummy}
        _apply_speech_config_to_active_audio_managers()
        assert dummy.reload_calls == 1
    finally:
        STATE.audio_managers = original_audio_managers


def test_update_speech_config_rejects_invalid_tts_rate():
    with pytest.raises(ValueError):
        _update_speech_config({}, speech_tts_rate="99")
