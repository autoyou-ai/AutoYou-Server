# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import asyncio
import os
import sys
import time
from types import SimpleNamespace

import pytest
from google.genai import types

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from autoyou_agents.audio_agent import agent as audio_agent
from shared.adk_state import AUTOYOU_OWNER_KEY_USER_STATE_KEY, AUTOYOU_REPLY_TARGET_USER_STATE_KEY
from shared.session_execution import SESSION_CONTROL_STATE_KEY


class _FakeToolContext:
    def __init__(self, state=None):
        self.state = dict(state or {})


class _FakeCallbackState:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get(self, key, default=None):
        return self._values.get(key, default)

    def __getitem__(self, key):
        return self._values[key]

    def __setitem__(self, key, value):
        self._values[key] = value


def _llm_request(text: str):
    return SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part(text=text)],
            )
        ]
    )


@pytest.fixture(autouse=True)
def _reset_audio_playback_singleton(monkeypatch):
    """Isolate audio tests from cross-test ServiceManager singleton pollution.

    ``_audio_playback_enabled()`` falls back to the shared ServiceManager
    singleton's ``config.audio_playback_enabled`` when the env override is unset.
    Another test leaving that field ``False`` made play paths report "disabled"
    instead of the expected result - a rare, order-dependent flake. Clear the env
    override and reset the singleton field to its default (None -> enabled) before
    each test, restoring afterwards. The singleton is only touched if it already
    exists (we never force-create it here).
    """
    monkeypatch.delenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", raising=False)
    import service_manager as _service_manager

    mgr = getattr(_service_manager, "_service_manager", None)
    cfg = getattr(mgr, "config", None) if mgr is not None else None
    if cfg is None or not hasattr(cfg, "audio_playback_enabled"):
        yield
        return
    previous = cfg.audio_playback_enabled
    cfg.audio_playback_enabled = None
    try:
        yield
    finally:
        cfg.audio_playback_enabled = previous


def test_search_local_audio_library_stores_results(monkeypatch, tmp_path):
    library_dir = tmp_path / "music"
    library_dir.mkdir()
    (library_dir / "Luna Rise.mp3").write_bytes(b"")
    (library_dir / "Luna Dream.flac").write_bytes(b"")
    (library_dir / "Ocean.wav").write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(library_dir))

    tool_context = _FakeToolContext()
    result = audio_agent.search_local_audio_library("luna", tool_context=tool_context)

    assert result["status"] == "success"
    assert result["count"] == 2
    assert result["tracks"][0]["result_index"] == 1
    assert tool_context.state["user:music_last_results"][0]["title"].lower().startswith("luna")


def test_search_local_audio_library_excludes_voice_training_recordings(monkeypatch, tmp_path):
    """Saved call recordings must stay out of the AI-searchable/playable library.

    They are surfaced to a human only via the Audio Player / Voice Training app
    (a separate, human-driven listing that still includes them - see
    shared/audio_playback_settings.py's include_voice_training_recordings flag);
    the agent must never auto-select and play back a private call recording
    mid-call while browsing for music.
    """
    library_dir = tmp_path / "music"
    library_dir.mkdir()
    training_dir = tmp_path / "voice-training"
    recordings_dir = training_dir / "recordings"
    recordings_dir.mkdir(parents=True)
    recording = recordings_dir / "call_synthetic.wav"
    recording.write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(library_dir))
    monkeypatch.setenv("AUTOYOU_VOICE_TRAINING_DIR", str(training_dir))

    result = audio_agent.search_local_audio_library("synthetic", tool_context=_FakeToolContext())

    assert result["status"] == "success"
    assert result["count"] == 0
    assert not any(track["file_path"] == str(recording) for track in result["tracks"])


def test_search_local_audio_library_default_root_browse_skips_hidden_runtime_dirs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(audio_agent.MUSIC_LIBRARY_DIRS_ENV, raising=False)

    (tmp_path / "Aura.mp3").write_bytes(b"")
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "Night Drive.mp3").write_bytes(b"")

    hidden_runtime_dir = tmp_path / ".venv" / "Lib" / "site-packages"
    hidden_runtime_dir.mkdir(parents=True)
    (hidden_runtime_dir / "Dependency Fixture.wav").write_bytes(b"")

    tool_context = _FakeToolContext()
    result = audio_agent.search_local_audio_library("", tool_context=tool_context)

    assert result["status"] == "success"
    assert result["count"] == 2
    assert {track["file_name"] for track in result["tracks"]} == {"Aura.mp3", "Night Drive.mp3"}


def test_search_local_audio_library_default_root_query_still_finds_root_track(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(audio_agent.MUSIC_LIBRARY_DIRS_ENV, raising=False)

    (tmp_path / "Aura.mp3").write_bytes(b"")
    hidden_runtime_dir = tmp_path / ".venv" / "Lib" / "site-packages"
    hidden_runtime_dir.mkdir(parents=True)
    (hidden_runtime_dir / "Dependency Fixture.wav").write_bytes(b"")

    tool_context = _FakeToolContext()
    result = audio_agent.search_local_audio_library("aura", tool_context=tool_context)

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["tracks"][0]["file_name"] == "Aura.mp3"


def test_search_local_audio_library_default_limit_is_uncapped(monkeypatch, tmp_path):
    library_dir = tmp_path / "music"
    library_dir.mkdir()
    for index in range(1, 13):
        (library_dir / f"Track {index:02d}.mp3").write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(library_dir))

    tool_context = _FakeToolContext()
    result = audio_agent.search_local_audio_library("", tool_context=tool_context)

    assert result["status"] == "success"
    assert result["count"] == 12
    assert len(result["tracks"]) == 12


def test_search_local_audio_library_compiled_guidance_points_to_config_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(audio_agent.MUSIC_LIBRARY_DIRS_ENV, raising=False)
    monkeypatch.setattr(audio_agent, "is_compiled", lambda: True)
    monkeypatch.setattr(audio_agent, "get_config_dir", lambda app_name, anchor=None: tmp_path / "userdata")
    # The Audio Player site library (Page Agent feed / Notes Agent media) is
    # read straight off disk via shared.audio_agent_library, which does not go
    # through the patched get_config_dir. Any such folder that exists - left by
    # an earlier test in the same run, or simply present on the machine - gets
    # appended to the resolved dirs, which stops them matching the default
    # runtime set and suppresses the configuration guidance asserted below.
    # Pin it: this test is about the compiled-build guidance, not site sources.
    monkeypatch.setattr(audio_agent, "_resolve_audio_agent_site_library_dirs", lambda: [])

    tool_context = _FakeToolContext()
    result = audio_agent.search_local_audio_library("", tool_context=tool_context)

    assert result["status"] == "success"
    assert result["count"] == 0
    assert "audio_playback.music_library_dirs" in result["message"]
    assert str(tmp_path / "userdata") in result["message"]


def test_play_local_audio_on_saved_reply_target_uses_last_search_result(monkeypatch, tmp_path):
    track_path = tmp_path / "music" / "Night Drive.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(track_path.parent))
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")

    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(audio_agent, "_http", fake_http)
    tool_context = _FakeToolContext(
        {
            "user:admin_session_valid_until": time.time() + 300,
            "user:admin_session_auth_token": "token-abc",
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
                "transport": "webrtc",
                "session_id": "relay-123",
                "owner_key": "cloud:phone-1",
            },
        }
    )

    search_result = audio_agent.search_local_audio_library("night", tool_context=tool_context)
    assert search_result["status"] == "success"

    result = audio_agent.play_local_audio_on_saved_reply_target("1", tool_context=tool_context)

    assert result["status"] == "success"
    assert tool_context.state["user:music_current_track"]["file_name"] == "Night Drive.mp3"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/webrtc/playback/play",
            "payload": {
                "session_id": "relay-123",
                "owner_key": "cloud:phone-1",
                "file_path": str(track_path),
            },
            "token": "token-abc",
            "timeout": 30,
        }
    ]


def test_play_local_audio_on_saved_reply_target_accepts_song_number_selection(monkeypatch, tmp_path):
    track_path = tmp_path / "music" / "Night Drive.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(track_path.parent))
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")

    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(audio_agent, "_http", fake_http)
    tool_context = _FakeToolContext(
        {
            "user:admin_session_valid_until": time.time() + 300,
            "user:admin_session_auth_token": "token-song-number",
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
                "transport": "webrtc",
                "session_id": "relay-song-number",
                "owner_key": "cloud:phone-song-number",
            },
        }
    )

    search_result = audio_agent.search_local_audio_library("night", tool_context=tool_context)
    assert search_result["status"] == "success"

    result = audio_agent.play_local_audio_on_saved_reply_target("song number 1", tool_context=tool_context)

    assert result["status"] == "success"
    assert tool_context.state["user:music_current_track"]["file_name"] == "Night Drive.mp3"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/webrtc/playback/play",
            "payload": {
                "session_id": "relay-song-number",
                "owner_key": "cloud:phone-song-number",
                "file_path": str(track_path),
            },
            "token": "token-song-number",
            "timeout": 30,
        }
    ]


def test_play_local_audio_on_saved_reply_target_accepts_prefixed_filename_selection(monkeypatch, tmp_path):
    track_path = tmp_path / "music" / "Nebula Signal.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(track_path.parent))
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "internal-prefixed-filename-token")

    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(audio_agent, "_http", fake_http)
    tool_context = _FakeToolContext(
        {
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
                "transport": "webrtc",
                "session_id": "relay-prefixed-filename",
                "owner_key": "cloud:phone-prefixed-filename",
            },
        }
    )

    result = audio_agent.play_local_audio_on_saved_reply_target("the song Nebula Signal.mp3", tool_context=tool_context)

    assert result["status"] == "success"
    assert tool_context.state["user:music_current_track"]["file_name"] == "Nebula Signal.mp3"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/webrtc/playback/play",
            "payload": {
                "session_id": "relay-prefixed-filename",
                "owner_key": "cloud:phone-prefixed-filename",
                "file_path": str(track_path),
            },
            "token": "internal-prefixed-filename-token",
            "timeout": 30,
        }
    ]


def test_play_local_audio_on_saved_reply_target_failed_lookup_keeps_last_search_results(monkeypatch, tmp_path):
    library_dir = tmp_path / "music"
    library_dir.mkdir()
    (library_dir / "Aura.mp3").write_bytes(b"")
    (library_dir / "Night Drive.mp3").write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(library_dir))

    tool_context = _FakeToolContext()
    search_result = audio_agent.search_local_audio_library("", tool_context=tool_context)

    assert search_result["status"] == "success"
    original_file_names = [track["file_name"] for track in search_result["tracks"]]

    result = audio_agent.play_local_audio_on_saved_reply_target("song number 99", tool_context=tool_context)

    assert result == {
        "status": "error",
        "message": "No local audio files matched '99'.",
    }
    assert [track["file_name"] for track in tool_context.state["user:music_last_results"]] == original_file_names


def test_play_local_audio_on_saved_reply_target_uses_internal_ai_token_without_admin_session(monkeypatch, tmp_path):
    track_path = tmp_path / "music" / "Aura.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(track_path.parent))
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "internal-playback-token")

    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(audio_agent, "_http", fake_http)
    tool_context = _FakeToolContext(
        {
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
                "transport": "webrtc",
                "session_id": "relay-777",
                "owner_key": "cloud:phone-777",
            },
        }
    )

    result = audio_agent.play_local_audio_on_saved_reply_target("aura", tool_context=tool_context)

    assert result["status"] == "success"
    assert tool_context.state["user:music_current_track"]["file_name"] == "Aura.mp3"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/webrtc/playback/play",
            "payload": {
                "session_id": "relay-777",
                "owner_key": "cloud:phone-777",
                "file_path": str(track_path),
            },
            "token": "internal-playback-token",
            "timeout": 30,
        }
    ]


def test_play_local_audio_on_saved_reply_target_falls_back_to_owner_key_when_saved_target_is_not_webrtc(monkeypatch, tmp_path):
    track_path = tmp_path / "music" / "Aura.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(track_path.parent))
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "internal-owner-fallback-token")

    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(audio_agent, "_http", fake_http)
    tool_context = _FakeToolContext(
        {
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
                "transport": "telegram",
                "chat_id": 5550001001,
            },
            AUTOYOU_OWNER_KEY_USER_STATE_KEY: "telegram:5550001001",
        }
    )

    result = audio_agent.play_local_audio_on_saved_reply_target("aura", tool_context=tool_context)

    assert result["status"] == "success"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/webrtc/playback/play",
            "payload": {
                "owner_key": "telegram:5550001001",
                "file_path": str(track_path),
            },
            "token": "internal-owner-fallback-token",
            "timeout": 30,
        }
    ]


def test_play_attachment_audio_on_saved_reply_target_uses_attachment_path(monkeypatch, tmp_path):
    track_path = tmp_path / "music" / "Aura.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "attachment-playback-token")

    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(audio_agent, "_http", fake_http)
    tool_context = _FakeToolContext(
        {
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
                "transport": "webrtc",
                "session_id": "relay-777",
                "owner_key": "cloud:phone-777",
            },
        }
    )

    result = audio_agent.play_attachment_audio_on_saved_reply_target(
        [{"mimetype": "audio/mpeg", "path": str(track_path), "filename": "Aura.mp3"}],
        intent_hint="Play on WebRTC audio call",
        tool_context=tool_context,
    )

    assert result["status"] == "success"
    assert result["track"]["file_name"] == "Aura.mp3"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/webrtc/playback/play",
            "payload": {
                "session_id": "relay-777",
                "owner_key": "cloud:phone-777",
                "file_path": str(track_path),
            },
            "token": "attachment-playback-token",
            "timeout": 30,
        }
    ]


def test_play_local_audio_on_saved_reply_target_uses_session_control_owner_key_fallback(monkeypatch, tmp_path):
    track_path = tmp_path / "music" / "Aura.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(track_path.parent))
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "session-control-owner-token")

    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(audio_agent, "_http", fake_http)
    tool_context = _FakeToolContext(
        {
            SESSION_CONTROL_STATE_KEY: {
                "canonical_user_id": "user::telegram:5550001001",
                "canonical_session_id": "session::telegram:5550001001::15",
                "owner_key": "telegram:5550001001",
            },
        }
    )

    search_result = audio_agent.search_local_audio_library("aura", tool_context=tool_context)
    assert search_result["status"] == "success"

    result = audio_agent.play_local_audio_on_saved_reply_target("aura", tool_context=tool_context)

    assert result["status"] == "success"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/webrtc/playback/play",
            "payload": {
                "owner_key": "telegram:5550001001",
                "file_path": str(track_path),
            },
            "token": "session-control-owner-token",
            "timeout": 30,
        }
    ]


def test_play_local_audio_on_saved_reply_target_accepts_explicit_reply_target_without_tool_context(monkeypatch, tmp_path):
    track_path = tmp_path / "music" / "Aura.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(track_path.parent))
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    monkeypatch.setenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "explicit-reply-target-token")

    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": timeout,
            }
        )
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(audio_agent, "_http", fake_http)

    result = audio_agent.play_local_audio_on_saved_reply_target(
        "aura",
        tool_context=None,
        reply_target={"transport": "webrtc", "owner_key": "telegram:5550001001", "session_id": "5550001001"},
    )

    assert result["status"] == "success"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/webrtc/playback/play",
            "payload": {
                "session_id": "5550001001",
                "owner_key": "telegram:5550001001",
                "file_path": str(track_path),
            },
            "token": "explicit-reply-target-token",
            "timeout": 30,
        }
    ]


def test_queue_navigation_and_repeat_state(monkeypatch, tmp_path):
    library_dir = tmp_path / "albums"
    library_dir.mkdir()
    first_track = library_dir / "Alpha.mp3"
    second_track = library_dir / "Beta.mp3"
    first_track.write_bytes(b"")
    second_track.write_bytes(b"")
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")

    calls = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        calls.append((path, payload))
        return {"status": "success", "data": {"ok": True}}

    monkeypatch.setattr(audio_agent, "_http", fake_http)
    tool_context = _FakeToolContext(
        {
            "user:admin_session_valid_until": time.time() + 300,
            "user:admin_session_auth_token": "token-queue",
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
                "transport": "webrtc",
                "owner_key": "cloud:laptop-1",
            },
        }
    )

    first = audio_agent._build_music_track(str(first_track))
    second = audio_agent._build_music_track(str(second_track))

    tool_context.state["user:music_current_track"] = first
    tool_context.state["user:music_queue_tracks"] = [second]
    tool_context.state["user:music_history_tracks"] = []

    repeat_result = audio_agent.set_saved_reply_target_audio_repeat_mode("all", tool_context=tool_context)
    shuffle_result = audio_agent.set_saved_reply_target_audio_shuffle(True, tool_context=tool_context)
    next_result = audio_agent.next_saved_reply_target_audio(tool_context=tool_context)
    previous_result = audio_agent.previous_saved_reply_target_audio(tool_context=tool_context)

    assert repeat_result == {
        "status": "success",
        "repeat_mode": "all",
        "message": "Repeat mode set to all.",
    }
    assert shuffle_result == {
        "status": "success",
        "shuffle_enabled": True,
        "message": "Shuffle enabled.",
    }
    assert next_result["status"] == "success"
    assert previous_result["status"] == "success"
    assert tool_context.state["user:music_current_track"]["track_id"] == first["track_id"]
    assert tool_context.state["user:music_queue_tracks"][0]["track_id"] == second["track_id"]
    assert calls == [
        (
            "/api/webrtc/playback/play",
            {"owner_key": "cloud:laptop-1", "file_path": second["file_path"]},
        ),
        (
            "/api/webrtc/playback/play",
            {"owner_key": "cloud:laptop-1", "file_path": first["file_path"]},
        ),
    ]


def test_audio_playback_disabled_short_circuits_transport(monkeypatch):
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "0")
    tool_context = _FakeToolContext(
        {
            "user:admin_session_valid_until": time.time() + 300,
            "user:admin_session_auth_token": "token-disabled",
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
                "transport": "webrtc",
                "owner_key": "cloud:desktop-1",
            },
        }
    )

    result = audio_agent.pause_saved_reply_target_audio(tool_context=tool_context)

    assert result == {
        "status": "error",
        "message": "Audio playback is disabled in server settings.",
        "state": "disabled",
    }


def test_audio_callback_marks_dispatch_on_raw_callback_state(monkeypatch, caplog):
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    callback_state = _FakeCallbackState()
    callback_context = SimpleNamespace(state=callback_state, invocation_id="turn-1")

    with caplog.at_level("WARNING", logger="autoyou_agents.admin_agent.agent"):
        response = asyncio.run(
            audio_agent._audio_before_model_callback(
                callback_context,
                _llm_request("pause the music"),
            )
        )

    assert response is not None
    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert function_call.name == "pause_saved_reply_target_audio"
    assert callback_state.get(audio_agent._AUDIO_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY) == "turn-1"
    assert "Could not store _autoyou_audio_tool_dispatch_invocation_id in state" not in caplog.text

    second_response = asyncio.run(
        audio_agent._audio_before_model_callback(
            callback_context,
            _llm_request("pause the music"),
        )
    )

    assert second_response is None


def test_audio_callback_does_not_treat_descriptive_go_back_phrase_as_previous_track(monkeypatch):
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    callback_context = SimpleNamespace(state=_FakeCallbackState(), invocation_id="turn-non-command-back")

    response = asyncio.run(
        audio_agent._audio_before_model_callback(
            callback_context,
            _llm_request("go back should not result aggressively"),
        )
    )

    assert response is None


def test_audio_callback_returns_recorded_search_results_without_model_rewrite(tmp_path):
    callback_state = _FakeCallbackState()
    callback_context = SimpleNamespace(state=callback_state, invocation_id="turn-2")
    track_path = tmp_path / "Aura.mp3"
    track_path.write_bytes(b"")
    track = audio_agent._build_music_track(str(track_path))
    track["result_index"] = 1

    audio_agent._mark_tool_dispatch(callback_state, "turn-2")
    audio_agent._audio_after_tool_callback(
        SimpleNamespace(name="search_local_audio_library"),
        {},
        callback_context,
        {
            "status": "success",
            "count": 1,
            "tracks": [track],
            "message": "Found 1 track(s).",
        },
    )

    response = asyncio.run(
        audio_agent._audio_before_model_callback(
            callback_context,
            _llm_request("search my music for aura"),
        )
    )

    assert response is not None
    assert response.content.parts[0].text == "Found 1 track.\n1. Aura (Aura.mp3)"


def test_audio_callback_play_request_includes_explicit_playback_context_args(monkeypatch):
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    callback_state = _FakeCallbackState(
        {
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY: {
                "transport": "webrtc",
                "owner_key": "telegram:5550001001",
                "session_id": "5550001001",
            },
            SESSION_CONTROL_STATE_KEY: {
                "owner_key": "telegram:5550001001",
                "canonical_user_id": "user::telegram:5550001001",
                "canonical_session_id": "session::telegram:5550001001::16",
            },
            AUTOYOU_OWNER_KEY_USER_STATE_KEY: "telegram:5550001001",
        }
    )
    callback_context = SimpleNamespace(state=callback_state, invocation_id="turn-3")

    response = asyncio.run(
        audio_agent._audio_before_model_callback(
            callback_context,
            _llm_request("Play aura"),
        )
    )

    assert response is not None
    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert function_call.name == "play_local_audio_on_saved_reply_target"
    assert function_call.args == {
        "track_selection": "aura",
        "reply_target": {
            "transport": "webrtc",
            "owner_key": "telegram:5550001001",
            "session_id": "5550001001",
        },
        "session_control": {
            "owner_key": "telegram:5550001001",
            "canonical_user_id": "user::telegram:5550001001",
            "canonical_session_id": "session::telegram:5550001001::16",
        },
        "owner_key": "telegram:5550001001",
    }


@pytest.mark.parametrize(
    "request_text",
    [
        "Play songs in random",
        "Play all songs in random",
    ],
)
def test_audio_callback_random_all_phrases_dispatch_shuffle_with_empty_query(monkeypatch, request_text):
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    callback_context = SimpleNamespace(state=_FakeCallbackState(), invocation_id="turn-random-all")

    response = asyncio.run(
        audio_agent._audio_before_model_callback(
            callback_context,
            _llm_request(request_text),
        )
    )

    assert response is not None
    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert function_call.name == "shuffle_play_local_audio_on_saved_reply_target"
    assert function_call.args == {"query": ""}


# ---------------------------------------------------------------------------
# Create-intent hallucination guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "create_phrase",
    [
        "Create a first song",
        "create a song",
        "generate a new track",
        "compose some music",
        "write a new song",
        "record an audio",
        "produce a beat",
        "make me a track",
        "synthesize a tune",
    ],
)
def test_audio_callback_rejects_create_intent_deterministically(monkeypatch, create_phrase):
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    callback_context = SimpleNamespace(state=_FakeCallbackState(), invocation_id="turn-create")

    response = asyncio.run(
        audio_agent._audio_before_model_callback(
            callback_context,
            _llm_request(create_phrase),
        )
    )

    assert response is not None, f"Expected rejection for: {create_phrase!r}"
    parts = response.content.parts
    assert len(parts) == 1
    text_part = parts[0]
    assert "not supported" in (text_part.text or "").lower() or "cannot" in (text_part.text or "").lower() or "can only" in (text_part.text or "").lower()
    # Metadata must flag this as a create rejection
    custom_metadata = getattr(response, "custom_metadata", {}) or {}
    assert custom_metadata.get("audio_create_rejected") is True


def test_audio_callback_does_not_reject_legitimate_play_request(monkeypatch):
    monkeypatch.setenv("AUTOYOU_AUDIO_PLAYBACK_ENABLED", "1")
    callback_context = SimpleNamespace(state=_FakeCallbackState(), invocation_id="turn-play")

    response = asyncio.run(
        audio_agent._audio_before_model_callback(
            callback_context,
            _llm_request("play my music"),
        )
    )

    # Should dispatch to play, not reject
    assert response is not None
    parts = response.content.parts
    # Must not have the rejection metadata
    custom_metadata = getattr(response, "custom_metadata", {}) or {}
    assert not custom_metadata.get("audio_create_rejected")


def test_audio_create_intent_pattern_matches_expected_phrases():
    """Verify the compiled regex matches all create-like phrases directly."""
    pattern = audio_agent._AUDIO_CREATE_INTENT_PATTERN
    should_match = [
        "create a song",
        "Create a first song",
        "generate music",
        "compose a track",
        "write a melody",
        "record an audio file",
        "produce a beat",
        "make me some music",
        "synthesize a tune",
        "build a new track",
    ]
    should_not_match = [
        "play the music",
        "pause the track",
        "list songs",
        "search for music",
        "next track",
        "shuffle play",
        "stop audio",
    ]
    for phrase in should_match:
        assert pattern.search(phrase), f"Should match: {phrase!r}"
    for phrase in should_not_match:
        assert not pattern.search(phrase), f"Should NOT match: {phrase!r}"
