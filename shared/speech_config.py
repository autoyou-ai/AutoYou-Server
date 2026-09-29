# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-ec54efd74753b92b05c7d36e

"""
Speech configuration helpers for AutoYou.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-ec54efd74753b92b05c7d36e"


import copy
import os
from typing import Any, Dict, Optional

MASKED_SECRET_PLACEHOLDER = "********"

OPENAI_TTS_MODELS = (
    "gpt-4o-mini-tts",
    "tts-1",
    "tts-1-hd",
)

OPENAI_TTS_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
)

STT_MODEL_SUGGESTIONS = (
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "medium.en",
    "large-v3",
    "distil-large-v3",
)

STT_COMPUTE_TYPE_SUGGESTIONS = (
    "default",
    "int8",
    "int8_float16",
    "int16",
    "float16",
    "float32",
)

STT_DEVICE_SUGGESTIONS = (
    "cpu",
    "cuda",
    "auto",
)

DEFAULT_SPEECH_CONFIG: Dict[str, Any] = {
    "tts": {
        "provider": "system",
        "rate": 1.0,
        "system_voice": "",
        "openai": {
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini-tts",
            "voice": "alloy",
            "instructions": "",
            "response_format": "wav",
        },
        "azure": {
            "speech_key": "",
            "speech_region": "",
            "voice": "en-US-AvaMultilingualNeural",
            "endpoint_id": "",
        },
        "emotivoice": {
            "speaker": "8051",
            "conversation_emotion": True,
        },
    },
    "stt": {
        "model": "tiny.en",
        "language": "en",
        "device": (os.getenv("AUTOYOU_STT_DEVICE", "cpu") or "cpu").strip().lower(),
        "compute_type": (os.getenv("AUTOYOU_STT_COMPUTE_TYPE", "float32") or "float32").strip(),
        "silero_sensitivity": 0.4,
        "post_speech_silence_duration": 0.6,
    },
    "voice_training": {
        "capture_enabled": False,
    },
}


def deepcopy_speech_config() -> Dict[str, Any]:
    return copy.deepcopy(DEFAULT_SPEECH_CONFIG)


def normalize_speech_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = deepcopy_speech_config()
    if not isinstance(config, dict):
        return normalized

    tts = config.get("tts")
    if isinstance(tts, dict):
        normalized["tts"]["provider"] = str(tts.get("provider") or normalized["tts"]["provider"]).strip().lower()
        try:
            rate = float(tts.get("rate", normalized["tts"]["rate"]))
        except Exception:
            rate = float(normalized["tts"]["rate"])
        normalized["tts"]["rate"] = min(4.0, max(0.25, rate))
        normalized["tts"]["system_voice"] = str(tts.get("system_voice") or "").strip()

        openai_cfg = tts.get("openai")
        if isinstance(openai_cfg, dict):
            normalized["tts"]["openai"]["api_key"] = str(openai_cfg.get("api_key") or "").strip()
            normalized["tts"]["openai"]["base_url"] = str(
                openai_cfg.get("base_url") or normalized["tts"]["openai"]["base_url"]
            ).strip()
            normalized["tts"]["openai"]["model"] = str(
                openai_cfg.get("model") or normalized["tts"]["openai"]["model"]
            ).strip()
            normalized["tts"]["openai"]["voice"] = str(
                openai_cfg.get("voice") or normalized["tts"]["openai"]["voice"]
            ).strip()
            normalized["tts"]["openai"]["instructions"] = str(openai_cfg.get("instructions") or "").strip()
            normalized["tts"]["openai"]["response_format"] = str(
                openai_cfg.get("response_format") or normalized["tts"]["openai"]["response_format"]
            ).strip().lower()

        azure_cfg = tts.get("azure")
        if isinstance(azure_cfg, dict):
            normalized["tts"]["azure"]["speech_key"] = str(azure_cfg.get("speech_key") or "").strip()
            normalized["tts"]["azure"]["speech_region"] = str(azure_cfg.get("speech_region") or "").strip()
            normalized["tts"]["azure"]["voice"] = str(
                azure_cfg.get("voice") or normalized["tts"]["azure"]["voice"]
            ).strip()
            normalized["tts"]["azure"]["endpoint_id"] = str(azure_cfg.get("endpoint_id") or "").strip()

        emotivoice_cfg = tts.get("emotivoice")
        if isinstance(emotivoice_cfg, dict):
            normalized["tts"]["emotivoice"]["speaker"] = str(
                emotivoice_cfg.get("speaker") or normalized["tts"]["emotivoice"]["speaker"]
            ).strip()
            conversation_emotion = emotivoice_cfg.get("conversation_emotion", True)
            if isinstance(conversation_emotion, str):
                conversation_emotion = conversation_emotion.strip().lower() not in {"0", "false", "no", "off"}
            normalized["tts"]["emotivoice"]["conversation_emotion"] = bool(conversation_emotion)

    stt = config.get("stt")
    if isinstance(stt, dict):
        normalized["stt"]["model"] = str(stt.get("model") or normalized["stt"]["model"]).strip()
        normalized["stt"]["language"] = str(stt.get("language") or normalized["stt"]["language"]).strip()
        normalized["stt"]["device"] = str(
            stt.get("device") or normalized["stt"]["device"]
        ).strip().lower()
        normalized["stt"]["compute_type"] = str(
            stt.get("compute_type") or normalized["stt"]["compute_type"]
        ).strip()
        try:
            silero_sensitivity = float(
                stt.get("silero_sensitivity", normalized["stt"]["silero_sensitivity"])
            )
        except Exception:
            silero_sensitivity = float(normalized["stt"]["silero_sensitivity"])
        try:
            silence = float(
                stt.get(
                    "post_speech_silence_duration",
                    normalized["stt"]["post_speech_silence_duration"],
                )
            )
        except Exception:
            silence = float(normalized["stt"]["post_speech_silence_duration"])
        normalized["stt"]["silero_sensitivity"] = min(1.0, max(0.0, silero_sensitivity))
        normalized["stt"]["post_speech_silence_duration"] = min(5.0, max(0.1, silence))

    voice_training = config.get("voice_training")
    if isinstance(voice_training, dict):
        capture_enabled = voice_training.get("capture_enabled", normalized["voice_training"]["capture_enabled"])
        if isinstance(capture_enabled, str):
            normalized["voice_training"]["capture_enabled"] = capture_enabled.strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
        else:
            normalized["voice_training"]["capture_enabled"] = bool(capture_enabled)

    if normalized["stt"]["device"] not in {"cpu", "cuda", "auto"}:
        normalized["stt"]["device"] = "cpu"

    if normalized["tts"]["provider"] not in {"system", "openai", "azure", "custom", "emotivoice", "off"}:
        normalized["tts"]["provider"] = "system"

    if not normalized["tts"]["openai"]["response_format"]:
        normalized["tts"]["openai"]["response_format"] = "wav"

    return normalized
