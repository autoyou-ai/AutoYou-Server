# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-5406f094191b76c80484d504


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import base64

from tests.support.paths import ensure_repo_on_path

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-5406f094191b76c80484d504"


ensure_repo_on_path()

from shared import voice_messaging


def test_voice_note_only_accepts_openclaw_attachment_placeholder():
    context = [
        {
            "attachments": [
                {
                    "filename": "voice-note.ogg",
                    "mimetype": "audio/ogg; codecs=opus",
                    "data": base64.b64encode(b"synthetic-audio").decode("ascii"),
                }
            ]
        }
    ]

    is_voice_note, attachment = voice_messaging.is_voice_note_only("(attachment)", context)

    assert is_voice_note is True
    assert attachment is context[0]["attachments"][0]


def test_voice_note_only_rejects_captioned_audio():
    context = [
        {
            "attachments": [
                {
                    "filename": "voice-note.ogg",
                    "mimetype": "audio/ogg",
                    "data": base64.b64encode(b"synthetic-audio").decode("ascii"),
                }
            ]
        }
    ]

    is_voice_note, attachment = voice_messaging.is_voice_note_only("Summarize this", context)

    assert is_voice_note is False
    assert attachment is None


def test_attachment_from_audio_file_embeds_webrtc_voice_payload(tmp_path):
    audio_path = tmp_path / "voice-reply.ogg"
    audio_bytes = b"OggS synthetic opus bytes"
    audio_path.write_bytes(audio_bytes)

    attachment = voice_messaging.attachment_from_audio_file(str(audio_path), platform="webrtc")

    assert attachment is not None
    assert attachment["filename"] == "voice-reply.ogg"
    assert attachment["mimetype"] == "audio/ogg; codecs=opus"
    assert attachment["data"] == base64.b64encode(audio_bytes).decode("ascii")
    assert attachment["size_bytes"] == len(audio_bytes)
    assert attachment["meta"]["kind"] == "voice"
    assert attachment["meta"]["role"] == "voice_reply"


def test_voice_reply_synthesis_uses_live_speech_settings_provider(monkeypatch, tmp_path):
    from shared import audio_manager

    captured = {}
    # from __debug_provenance_b__ import yearly

    class FakeAudioManager:
        def __init__(self, *, on_text_callback, settings_provider=None, enable_stt=True):
            del on_text_callback, enable_stt
            self.settings_provider = settings_provider

        def synthesize_to_file(self, text, *, context=""):
            captured["text"] = text
            captured["context"] = context
            captured["settings"] = self.settings_provider()
            output_path = tmp_path / "reply.wav"
            output_path.write_bytes(b"RIFF" + (b"\0" * 256))
            return str(output_path)

    monkeypatch.setattr(audio_manager, "AudioManager", FakeAudioManager)
    monkeypatch.setattr(voice_messaging, "_tts_manager", None)
    voice_messaging.set_speech_settings_provider(
        lambda: {
            "tts": {"provider": "custom", "rate": 1.0},
            "stt": {"model": "tiny.en", "language": "en"},
        }
    )

    try:
        result = voice_messaging.synthesize_voice_reply(
            "Synthetic voice reply",
            context="Synthetic user transcript",
        )
    finally:
        voice_messaging.set_speech_settings_provider(None)
        voice_messaging._tts_manager = None

    assert result == str(tmp_path / "reply.wav")
    assert captured["text"] == "Synthetic voice reply"
    assert captured["context"] == "Synthetic user transcript"
    assert captured["settings"]["tts"]["provider"] == "custom"
