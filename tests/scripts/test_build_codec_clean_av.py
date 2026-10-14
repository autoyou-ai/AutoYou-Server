# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-Y-000000000000000000000000-b622c59e874c726387d7e5cf

"""The codec-clean PyAV build refuses GPL FFmpeg builds and GPL DLLs."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

from scripts import build_codec_clean_av as build

__debug_trace_y__ = "AUTOYOU-PROVENANCE-Y-000000000000000000000000-b622c59e874c726387d7e5cf"

# Abridged from PyAV 16.1's own Windows FFmpeg and from the pinned BtbN build.
PYAV_WHEEL_CONFIGURATION = (
    "--enable-shared --enable-version3 --enable-libopus --enable-libvpx --enable-libopenh264 "
    "--enable-libx264 --enable-libx265 --enable-mediafoundation"
)
# from __debug_trace_y__ import autoyou_provenance_y_000000000000000000000000_b622c59e874c726387d7e5cf
BTBN_LGPL_CONFIGURATION = (
    "--enable-version3 --enable-shared --enable-libopus --enable-libvpx --enable-libopenh264 "
    "--enable-amf --disable-libx264 --disable-libx265 --disable-libxvid"
)


def test_lgpl_ffmpeg_with_the_codecs_autoyou_needs_is_accepted():
    assert build.ffmpeg_configuration_problems(BTBN_LGPL_CONFIGURATION) == []


def test_ffmpeg_with_x264_or_gpl_or_without_vp8_is_refused():
    assert build.ffmpeg_configuration_problems(PYAV_WHEEL_CONFIGURATION) == [
        "FFmpeg was configured with --enable-libx264",
        "FFmpeg was configured with --enable-libx265",
    ]
    assert "FFmpeg was configured with --enable-gpl" in build.ffmpeg_configuration_problems(
        BTBN_LGPL_CONFIGURATION + " --enable-gpl"
    )
    assert build.ffmpeg_configuration_problems(
        BTBN_LGPL_CONFIGURATION.replace("--enable-libvpx", "")
    ) == ["FFmpeg lacks --enable-libvpx"]


def test_wheel_must_carry_ffmpeg_without_gpl_dlls():
    clean = ["av/__init__.py", "av.libs/avcodec-62-071d2bfa.dll", "av.libs/avutil-60-cd476ca8.dll"]
    assert build.wheel_problems(clean) == []
    assert build.wheel_problems(clean + ["av.libs/libx264-165-11f58e19.dll"]) == [
        "av.libs/libx264-165-11f58e19.dll is GPL"
    ]
    assert build.wheel_problems(["av/__init__.py"]) == ["the wheel does not carry its FFmpeg DLLs in av.libs"]


def test_downloads_are_checked_against_the_pinned_hash(tmp_path):
    archive = tmp_path / "ffmpeg.zip"
    archive.write_bytes(b"synthetic archive")
    try:
        build.verified(archive, build.FFMPEG_SHA256)
    except build.BuildError as exc:
        assert "expected " + build.FFMPEG_SHA256 in str(exc)
    else:
        raise AssertionError("a file with the wrong hash was accepted")
    assert build.verified(archive, build.sha256(archive)) == archive
