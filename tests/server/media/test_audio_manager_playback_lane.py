# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Outbound audio lane separation: speech barge-in must never cancel music.

`AudioManager` exposes two outbound lanes - `tts_track` (spoken AI replies) and
`playback_track` (file/media playback). `_get_playback_track()` still falls back
to the speech lane when a server path did not register a dedicated media lane,
which makes the two roles share one `TTSAudioStreamTrack`.

On a shared lane every `stop_speaking()` caller (VAD barge-in via
`on_recording_start`, the client `stop_tts` event, background-audio suppression)
would clear the queue and silently kill a song the caller explicitly started -
the documented rule in `.llm/flows/voice.md` is that `stop_speaking()` clears
only reply speech and the media controls target only file playback.
"""

import threading

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

from shared.audio_manager import AudioManager, TTSAudioStreamTrack


def _bare_manager():
    manager = AudioManager.__new__(AudioManager)
    manager._closed = False
    manager._tts_generation = 0
    manager._tts_generation_lock = threading.Lock()
    manager._stop_tts_event = threading.Event()
    manager.tts_track = None
    manager.playback_track = None
    return manager


def _media_track(source: str = "audio_file") -> TTSAudioStreamTrack:
    track = TTSAudioStreamTrack()
    track._begin_playback("/music/aura.mp3", source=source)
    return track


def _speech_track() -> TTSAudioStreamTrack:
    track = TTSAudioStreamTrack()
    track._begin_playback("/tmp/reply.wav", source="tts")
    return track


# ── track-level role reporting ───────────────────────────────────────────────

def test_track_reports_media_vs_speech_role() -> None:
    media = _media_track()
    assert media.is_rendering_media() is True
    assert media.is_rendering_speech() is False

    speech = _speech_track()
    assert speech.is_rendering_speech() is True
    assert speech.is_rendering_media() is False


def test_idle_track_is_neither_media_nor_speech() -> None:
    track = TTSAudioStreamTrack()
    assert track.is_rendering_media() is False
    assert track.is_rendering_speech() is False


def test_paused_media_still_counts_as_media() -> None:
    media = _media_track()
    assert media.pause_playback() is True
    assert media.is_rendering_media() is True


# ── stop_speaking must not cancel music ──────────────────────────────────────

def test_vad_barge_in_does_not_stop_music_on_shared_lane() -> None:
    manager = _bare_manager()
    shared = _media_track()
    manager.tts_track = shared  # no dedicated media lane registered

    manager.stop_speaking(source="vad:on_recording_start")

    assert shared.get_playback_status()["state"] == "playing"
    assert shared.get_playback_status()["source"] == "audio_file"


def test_stop_tts_does_not_stop_music_on_shared_lane() -> None:
    manager = _bare_manager()
    shared = _media_track()
    manager.tts_track = shared

    manager.stop_speaking(source="ios:session-1:client_request")

    assert shared.get_playback_status()["state"] == "playing"


def test_stop_speaking_still_cancels_a_spoken_reply_on_shared_lane() -> None:
    manager = _bare_manager()
    shared = _speech_track()
    manager.tts_track = shared

    manager.stop_speaking(source="vad:on_recording_start")

    assert shared.get_playback_status()["state"] == "stopped"


def test_stop_speaking_never_touches_a_dedicated_media_lane() -> None:
    manager = _bare_manager()
    manager.tts_track = _speech_track()
    manager.playback_track = _media_track()

    manager.stop_speaking(source="vad:on_recording_start")

    assert manager.tts_track.get_playback_status()["state"] == "stopped"
    assert manager.playback_track.get_playback_status()["state"] == "playing"


def test_stop_speaking_bumps_tts_generation_even_when_music_is_protected() -> None:
    manager = _bare_manager()
    manager.tts_track = _media_track()

    manager.stop_speaking(source="vad:on_recording_start")

    # In-flight synthesis must still be abandoned; only the queue is spared.
    assert manager._tts_generation == 1
    assert manager._stop_tts_event.is_set()


# ── media controls must not cancel a spoken reply ────────────────────────────

def test_media_controls_decline_while_shared_lane_holds_speech() -> None:
    manager = _bare_manager()
    speech = _speech_track()
    manager.tts_track = speech

    assert manager.pause_playback() is False
    assert manager.resume_playback() is False
    assert manager.stop_playback(source="admin_api:stop") is False
    assert speech.get_playback_status()["state"] == "playing"


def test_media_controls_act_on_a_shared_lane_holding_music() -> None:
    manager = _bare_manager()
    music = _media_track()
    manager.tts_track = music

    assert manager.pause_playback() is True
    assert manager.resume_playback() is True
    assert manager.stop_playback(source="admin_api:stop") is True
    assert music.get_playback_status()["state"] == "stopped"


def test_media_stop_targets_the_dedicated_lane_and_spares_speech() -> None:
    manager = _bare_manager()
    manager.tts_track = _speech_track()
    manager.playback_track = _media_track()

    assert manager.stop_playback(source="admin_api:stop") is True
    assert manager.playback_track.get_playback_status()["state"] == "stopped"
    assert manager.tts_track.get_playback_status()["state"] == "playing"
