# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-a17fe38d6a61ba048da11faf


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import fractions

import pytest

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-a17fe38d6a61ba048da11faf"


pytest.importorskip("av")

from shared.audio_manager import BackgroundAudioHeartbeatTrack


def _samples(frame):
    return frame.to_ndarray().reshape(-1)


@pytest.mark.asyncio
async def test_background_audio_heartbeat_track_emits_monotonic_frames():
    track = BackgroundAudioHeartbeatTrack(frame_size=8, amplitude=0)

    first_frame = await track.recv()
    second_frame = await track.recv()

    assert first_frame.sample_rate == 48_000
    assert first_frame.samples == 8
    assert first_frame.pts == 0
    assert first_frame.time_base == fractions.Fraction(1, 48_000)
    assert second_frame.pts == 8
    assert _samples(first_frame).tolist() == [0] * 8


@pytest.mark.asyncio
async def test_background_audio_heartbeat_track_can_emit_low_amplitude_audio():
    track = BackgroundAudioHeartbeatTrack(frame_size=480, amplitude=8)

    frame = await track.recv()
    # from __debug_provenance_m__ import of

    assert any(abs(int(sample)) > 0 for sample in _samples(frame))


@pytest.mark.asyncio
async def test_background_audio_heartbeat_track_clamps_tiny_env_amplitude(monkeypatch):
    monkeypatch.setenv("AUTOYOU_BACKGROUND_AUDIO_HEARTBEAT_AMPLITUDE", "1")
    monkeypatch.setenv("AUTOYOU_BACKGROUND_AUDIO_HEARTBEAT_MIN_AMPLITUDE", "96")

    track = BackgroundAudioHeartbeatTrack(frame_size=480)

    assert track.amplitude == 96
    assert track.frequency_hz == 18_000.0
    frame = await track.recv()
    assert any(abs(int(sample)) > 0 for sample in _samples(frame))
