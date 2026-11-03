# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Whisper's inventions never become a question (owner report 2026-11-03).

After the wake word, near-silence came back as "You" and a mumble as "200g de
quake 20g de quake 20g de quake 50g de quake"; both were sent to the model.
The checks work the same in every language.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from shared import assistant_mode as am
from shared import voice_messaging
from shared import wake_word as ww


@pytest.mark.parametrize("text,voiced", [
    ("You", 0.4),
    ("Thank you.", 0.6),
    ("谢谢", 0.5),
    ("ご視聴ありがとうございました", 1.0),
    ("Sous-titres réalisés par la communauté d'Amara.org", 1.5),
    ("200g de quake 20g de quake 20g de quake 50g de quake", 2.0),
    ("in peer-to-peer-to-peerleich application since ever since since since in tune", 4.0),
    ("", 1.0),
])
def test_inventions_are_noise(text, voiced):
    assert am.transcript_is_noise(text, voiced) is True


@pytest.mark.parametrize("text,voiced", [
    ("What is this?", 0.9),
    ("Thank you.", 3.0),
    ("Set a timer for ten minutes", 1.3),
    ("Add milk, eggs and bread to my shopping list", 2.2),
    ("Quel temps fera-t-il demain à Lyon ?", 1.8),
    ("¿Qué hora es en Madrid?", 1.4),
    ("明天东京的天气怎么样", 1.6),
])
def test_questions_in_any_language_are_kept(text, voiced):
    assert am.transcript_is_noise(text, voiced) is False


def test_speech_seconds_counts_speech_not_length():
    rate = ww.SAMPLE_RATE
    quiet = np.zeros(rate * 2)
    tone = 0.3 * np.sin(2 * np.pi * 220 * np.arange(int(rate * 0.8)) / rate)
    assert am.speech_seconds(ww.float_to_pcm16(quiet)) == 0.0
    assert 0.6 <= am.speech_seconds(ww.float_to_pcm16(np.concatenate([quiet, tone, quiet]))) <= 1.0


def test_whisper_segments_that_are_silence_or_loops_are_dropped():
    speech = SimpleNamespace(no_speech_prob=0.05, avg_logprob=-0.3, compression_ratio=1.4)
    silence = SimpleNamespace(no_speech_prob=0.7, avg_logprob=-1.3, compression_ratio=1.2)
    loop = SimpleNamespace(no_speech_prob=0.1, avg_logprob=-0.4, compression_ratio=3.1)
    assert voice_messaging._segment_is_speech(speech) is True
    assert voice_messaging._segment_is_speech(silence) is False
    assert voice_messaging._segment_is_speech(loop) is False


def test_questions_are_transcribed_strictly(monkeypatch):
    seen = {}

    def fake_transcribe(path, *, settings=None, strict=False):
        seen["strict"] = strict
        return "what time is it"

    monkeypatch.setattr(voice_messaging, "transcribe_voice_note", fake_transcribe)
    text = am.local_transcriber()(ww.float_to_pcm16(np.zeros(ww.SAMPLE_RATE // 2)))
    assert text == "what time is it" and seen["strict"] is True
