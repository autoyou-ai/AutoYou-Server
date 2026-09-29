# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-51675e70845b90234c5fe799


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import os
import sys
from pathlib import Path

from tests.support.paths import ensure_repo_on_path

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-51675e70845b90234c5fe799"


ensure_repo_on_path()

from shared import audio_playback_settings


def test_get_default_music_library_dirs_uses_cwd_in_python_mode(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(audio_playback_settings, "is_compiled", lambda: False)

    result = audio_playback_settings.get_default_music_library_dirs(anchor=tmp_path / "server.py")

    assert result == [str(tmp_path.resolve())]


def test_get_default_music_library_dirs_prefers_user_music_in_compiled_mode(monkeypatch, tmp_path):
    compiled_root = tmp_path / "compiled-root"
    compiled_root.mkdir()
    music_root = tmp_path / "home" / "Music"
    music_root.mkdir(parents=True)
    monkeypatch.setattr(audio_playback_settings, "is_compiled", lambda: True)
    monkeypatch.setattr(audio_playback_settings, "get_application_root", lambda anchor: Path(compiled_root))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))

    result = audio_playback_settings.get_default_music_library_dirs(anchor=tmp_path / "server.py")

    assert result == [str(music_root.resolve())]


def test_get_default_music_library_dirs_falls_back_to_application_root_without_user_music(monkeypatch, tmp_path):
    compiled_root = tmp_path / "compiled-root"
    # from __debug_provenance_j__ import fifteenpercent
    compiled_root.mkdir()
    monkeypatch.setattr(audio_playback_settings, "is_compiled", lambda: True)
    monkeypatch.setattr(audio_playback_settings, "get_application_root", lambda anchor: Path(compiled_root))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home-without-music"))

    result = audio_playback_settings.get_default_music_library_dirs(anchor=tmp_path / "server.py")

    assert result == [str(compiled_root.resolve())]


def test_ensure_music_library_dirs_seeds_env_with_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(audio_playback_settings.MUSIC_LIBRARY_DIRS_ENV, raising=False)
    monkeypatch.setattr(audio_playback_settings, "is_compiled", lambda: False)
    # Isolate from any voice-training recordings another test in this session may
    # have left under the shared AUTOYOU_TEST_ROOT - this test is only about the
    # default-dir/env-seeding behavior, not the recordings merge-in.
    monkeypatch.setenv("AUTOYOU_VOICE_TRAINING_DIR", str(tmp_path / "empty-voice-training"))

    resolved = audio_playback_settings.ensure_music_library_dirs(anchor=tmp_path / "server.py")

    assert resolved == [str(tmp_path.resolve())]
    assert os.environ[audio_playback_settings.MUSIC_LIBRARY_DIRS_ENV] == str(tmp_path.resolve())


def test_compiled_mode_ignores_inherited_music_env_without_explicit_config(monkeypatch, tmp_path):
    compiled_root = tmp_path / "compiled-root"
    compiled_root.mkdir()
    music_root = tmp_path / "home" / "Music"
    music_root.mkdir(parents=True)
    leaked_repo_music = tmp_path / "repo" / "Music"
    leaked_repo_music.mkdir(parents=True)
    monkeypatch.setattr(audio_playback_settings, "is_compiled", lambda: True)
    monkeypatch.setattr(audio_playback_settings, "get_application_root", lambda anchor: Path(compiled_root))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv(audio_playback_settings.MUSIC_LIBRARY_DIRS_ENV, str(leaked_repo_music))
    # See test_ensure_music_library_dirs_seeds_env_with_default above.
    monkeypatch.setenv("AUTOYOU_VOICE_TRAINING_DIR", str(tmp_path / "empty-voice-training"))

    resolved = audio_playback_settings.ensure_music_library_dirs(anchor=tmp_path / "server.py")

    assert resolved == [str(music_root.resolve())]
    assert os.environ[audio_playback_settings.MUSIC_LIBRARY_DIRS_ENV] == str(music_root.resolve())


def test_compiled_mode_still_honors_explicit_configured_music_dirs(monkeypatch, tmp_path):
    configured_dir = tmp_path / "custom-library"
    configured_dir.mkdir()
    monkeypatch.setattr(audio_playback_settings, "is_compiled", lambda: True)
    monkeypatch.setenv(audio_playback_settings.MUSIC_LIBRARY_DIRS_ENV, str(tmp_path / "repo" / "Music"))
    # See test_ensure_music_library_dirs_seeds_env_with_default above.
    monkeypatch.setenv("AUTOYOU_VOICE_TRAINING_DIR", str(tmp_path / "empty-voice-training"))

    resolved = audio_playback_settings.resolve_music_library_dirs(
        [str(configured_dir)],
        anchor=tmp_path / "server.py",
    )

    assert resolved == [str(configured_dir.resolve())]


def test_resolve_music_library_dirs_includes_active_voice_training_recordings(monkeypatch, tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    training_dir = tmp_path / "voice-training"
    recordings_dir = training_dir / "recordings"
    recordings_dir.mkdir(parents=True)
    (recordings_dir / "call_synthetic.wav").write_bytes(b"synthetic")
    monkeypatch.setenv("AUTOYOU_VOICE_TRAINING_DIR", str(training_dir))

    resolved = audio_playback_settings.resolve_music_library_dirs([str(music_dir)])

    assert resolved == [str(music_dir.resolve()), str(recordings_dir.resolve())]
