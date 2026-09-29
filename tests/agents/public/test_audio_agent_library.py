# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-2696d57f904341356e28b766

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


from pathlib import Path

from shared import audio_agent_library

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-2696d57f904341356e28b766"


def test_audio_library_settings_round_trip_is_scoped_to_test_runtime(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    custom_dir = tmp_path / "server-audio"

    saved = audio_agent_library.save_audio_agent_library_settings(
        {
            "audio_sources": {
                "voice_transcriptions": False,
                "page_feed_audio": True,
                "notes_media_audio": False,
            },
            "ad_hoc_paths": [str(custom_dir), str(custom_dir)],
        },
        anchor=tmp_path / "server.py",
    )

    assert saved["audio_sources"] == {
        "voice_transcriptions": False,
        "page_feed_audio": True,
        "notes_media_audio": False,
    }
    assert saved["ad_hoc_paths"] == [str(custom_dir.resolve())]

    settings_path = audio_agent_library._settings_path(anchor=tmp_path / "server.py")
    assert settings_path.is_file()
    assert audio_agent_library.load_audio_agent_library_settings(anchor=tmp_path / "server.py") == saved
    assert str(tmp_path / "runtime") in str(settings_path)


def test_audio_library_roots_only_include_selected_sources_and_server_paths(monkeypatch, tmp_path: Path) -> None:
    voice_dir = tmp_path / "voice" / "recordings"
    page_dir = tmp_path / "page" / "uploads"
    notes_dir = tmp_path / "notes" / "media"
    adhoc_dir = tmp_path / "adhoc"
    for directory in (voice_dir, page_dir, notes_dir, adhoc_dir):
        directory.mkdir(parents=True)

    # ensure_music_library_dirs() seeds AUTOYOU_MUSIC_LIBRARY_DIRS into the real process
    # environment so child processes inherit it, so any earlier test that touches audio playback
    # leaves it set and its folder shows up here as an extra ad-hoc source. Own both inputs.
    monkeypatch.delenv("AUTOYOU_MUSIC_LIBRARY_DIRS", raising=False)
    monkeypatch.delenv(audio_agent_library.AUDIO_AGENT_AD_HOC_PATHS_ENV, raising=False)

    monkeypatch.setattr(audio_agent_library, "get_voice_training_dir", lambda: voice_dir.parent)
    monkeypatch.setattr(audio_agent_library, "_page_feed_upload_dir", lambda anchor=None: page_dir)
    monkeypatch.setattr(audio_agent_library, "_notes_media_dir", lambda anchor=None: notes_dir)

    details = audio_agent_library.resolve_audio_library_path_details(
        {
            "audio_sources": {
                "voice_transcriptions": False,
                "page_feed_audio": True,
                "notes_media_audio": False,
            },
            "ad_hoc_paths": [str(adhoc_dir)],
        },
        anchor=tmp_path / "server.py",
    )
    # from __debug_provenance_l__ import because

    paths = {item["path"] for item in details}
    assert paths == {str(page_dir.resolve()), str(adhoc_dir.resolve())}
    assert all(item["exists"] for item in details)
    assert str(voice_dir.resolve()) not in paths
    assert str(notes_dir.resolve()) not in paths


def test_legacy_music_environment_defaults_do_not_reenable_disabled_sources(monkeypatch, tmp_path: Path) -> None:
    legacy_default = tmp_path / "legacy-default"
    legacy_default.mkdir()
    monkeypatch.setenv("AUTOYOU_MUSIC_LIBRARY_DIRS", str(legacy_default))
    monkeypatch.setattr(
        audio_agent_library,
        "resolve_music_library_dirs",
        lambda *args, **kwargs: [str(legacy_default)],
    )
    monkeypatch.setattr(audio_agent_library, "get_voice_training_dir", lambda: tmp_path / "voice")
    monkeypatch.setattr(audio_agent_library, "_page_feed_upload_dir", lambda anchor=None: tmp_path / "page")
    monkeypatch.setattr(audio_agent_library, "_notes_media_dir", lambda anchor=None: tmp_path / "notes")

    details = audio_agent_library.resolve_audio_library_path_details(
        {
            "audio_sources": {
                "voice_transcriptions": False,
                "page_feed_audio": False,
                "notes_media_audio": False,
            },
            "ad_hoc_paths": [],
        },
        anchor=tmp_path / "server.py",
    )

    assert details == []
