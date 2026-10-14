# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Local reply-language detection and language-aware system voice selection."""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from shared.speech_config import speech_language

_DETECTOR = None
_LOCK = threading.Lock()
LOGGER = logging.getLogger("autoyou.speech_language")


def detect_language(text: str, hint: str = "") -> str:
    """Detect the reply, using a hint only when a short reply is ambiguous."""
    global _DETECTOR
    text = str(text or "").strip()
    hint = speech_language(hint)
    if not text:
        return hint
    try:
        with _LOCK:
            if _DETECTOR is None:
                from lingua import LanguageDetectorBuilder

                _DETECTOR = LanguageDetectorBuilder.from_all_languages().build()
        values = _DETECTOR.compute_language_confidence_values(text[:4000])
        if not values or values[0].value < 0.2:
            return hint
        if hint and sum(char.isalpha() for char in text) < 20 and len(values) > 1 and values[0].value - values[1].value < 0.15:
            return hint
        return values[0].language.iso_code_639_1.name.lower()
    except ImportError:
        LOGGER.debug("Local language detector is unavailable")
        return hint


def voice_languages(voice: dict[str, Any]) -> set[str]:
    raw = voice.get("languages") or ""
    if isinstance(raw, (list, tuple)):
        raw = " ".join(item.decode("utf-8", errors="ignore") if isinstance(item, bytes) else str(item) for item in raw)
    return {speech_language(code) for code in re.findall(r"[a-zA-Z]{2,3}(?:[-_][a-zA-Z0-9]+)*", str(raw))}


def matching_system_voice(voices: list[dict[str, Any]], language: str, preferred: str = "") -> str:
    language = speech_language(language)
    if not language:
        return ""
    matches = [voice for voice in voices if voice.get("id") != "custom_voice" and language in voice_languages(voice)]
    return str(next((voice["id"] for voice in matches if voice.get("id") == preferred), matches[0].get("id", "") if matches else ""))
