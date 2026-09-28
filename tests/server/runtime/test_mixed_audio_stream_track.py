import fractions

import pytest

np = pytest.importorskip("numpy")
av = pytest.importorskip("av")

from shared.audio_manager import MixedAudioStreamTrack


def _audio_frame(value: int, *, samples: int = 8, sample_rate: int = 48_000):
    frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
    payload = np.full(samples, value, dtype=np.int16)
    frame.planes[0].update(payload.tobytes())
    frame.sample_rate = sample_rate
    frame.pts = 0
    frame.time_base = fractions.Fraction(1, sample_rate)
    return frame


def _samples(frame):
    return frame.to_ndarray().reshape(-1)


class ConstantTrack:
    def __init__(self, value: int):
        self.value = value

    async def recv(self):
        return _audio_frame(self.value)


class FailingTrack:
    async def recv(self):
        raise RuntimeError("synthetic input ended")


@pytest.mark.asyncio
async def test_mixed_audio_stream_track_adds_sources():
    track = MixedAudioStreamTrack(
        [
            ("speech", ConstantTrack(1000)),
            ("computer_audio", ConstantTrack(2000)),
        ],
        frame_size=8,
    )

    frame = await track.recv()

    assert frame.sample_rate == 48_000
    assert frame.pts == 0
    assert np.all(_samples(frame) == 3000)


@pytest.mark.asyncio
async def test_mixed_audio_stream_track_clips_to_s16_range():
    track = MixedAudioStreamTrack(
        [
            ("speech", ConstantTrack(30_000)),
            ("computer_audio", ConstantTrack(10_000)),
        ],
        frame_size=8,
    )

    frame = await track.recv()

    assert np.all(_samples(frame) == 32_767)


def test_mixed_audio_stream_track_limiter_preserves_relative_levels():
    track = MixedAudioStreamTrack(frame_size=2)
    loud = np.array([40_000, 20_000], dtype=np.int32)

    frame = track._mixed_frame([loud])
    samples = _samples(frame)

    assert samples[0] == 32_767
    assert 16_382 <= samples[1] <= 16_384


@pytest.mark.asyncio
async def test_mixed_audio_stream_track_removes_failed_sources():
    track = MixedAudioStreamTrack(
        [
            ("ended", FailingTrack()),
            ("speech", ConstantTrack(1234)),
        ],
        frame_size=8,
    )

    first_frame = await track.recv()
    second_frame = await track.recv()

    assert track.source_names() == ["speech"]
    assert np.all(_samples(first_frame) == 1234)
    assert np.all(_samples(second_frame) == 1234)
