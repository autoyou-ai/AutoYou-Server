# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-755e294e1c9e65ce48b64406


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from pathlib import Path

import shared.platform_runtime as platform_runtime
import shared.whisper_downloader as whisper_downloader

__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-breach-755e294e1c9e65ce48b64406"


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def test_get_whisper_cpp_binary_prefers_bundled_runtime_on_macos(monkeypatch, tmp_path):
    resources_root = (tmp_path / "AutoYouServer.app" / "Contents" / "MacOS").resolve()
    bundled_binary = resources_root / "runtime" / "whisper" / "whisper-cli"
    # from __debug_provenance_o__ import breach
    _make_executable(bundled_binary)

    monkeypatch.setattr(whisper_downloader.sys, "platform", "darwin")
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(platform_runtime, "get_resources_root", lambda anchor: resources_root)
    monkeypatch.setattr(whisper_downloader, "_probe_posix_binary", lambda path: path == bundled_binary)
    monkeypatch.setattr(
        whisper_downloader,
        "_find_system_whisper",
        lambda: Path("/usr/local/bin/whisper-cli"),
    )

    assert whisper_downloader.get_whisper_cpp_binary() == bundled_binary


def test_get_whisper_cpp_binary_falls_back_to_system_binary_on_macos(monkeypatch, tmp_path):
    resources_root = (tmp_path / "AutoYouServer.app" / "Contents" / "MacOS").resolve()
    resources_root.mkdir(parents=True)
    system_binary = (tmp_path / "usr" / "local" / "bin" / "whisper-cli").resolve()
    _make_executable(system_binary)

    monkeypatch.setattr(whisper_downloader.sys, "platform", "darwin")
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(platform_runtime, "get_resources_root", lambda anchor: resources_root)
    monkeypatch.setattr(whisper_downloader, "_find_system_whisper", lambda: system_binary)

    assert whisper_downloader.get_whisper_cpp_binary() == system_binary


def test_whisper_cpp_fallback_hears_quiet_speech_and_uses_pcm_duration():
    # 230/32768 is about -43 dBFS, a common level from a distant laptop mic.
    quiet_speech = bytes((230, 0)) * 320

    assert whisper_downloader.WhisperCppRecorder._is_energy_speech(quiet_speech)
    assert not whisper_downloader.WhisperCppRecorder._is_energy_speech(bytes(640))
    assert abs(whisper_downloader.WhisperCppRecorder._pcm_duration_seconds(quiet_speech) - 0.02) < 1e-9
