# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""H.264 still works when the FFmpeg build has no x264 (Store packages)."""

import fractions

import pytest

av = pytest.importorskip("av")
pytest.importorskip("aiortc")

from aiortc.codecs import get_decoder, get_encoder, h264 as aiortc_h264
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtcrtpparameters import RTCRtpCodecParameters

from shared import h264_encoders
from shared import video_call_manager


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv(h264_encoders.ENCODER_ENV, raising=False)
    monkeypatch.setattr(h264_encoders, "_usable", {})
    # install_aiortc_h264_encoder() patches aiortc's class; put it back afterwards.
    monkeypatch.setattr(aiortc_h264.H264Encoder, "_encode_frame", aiortc_h264.H264Encoder._encode_frame)


def _without_x264(monkeypatch):
    monkeypatch.setattr(av, "codecs_available", set(av.codecs_available) - {"libx264", "libx264rgb"})


def _frames(count, width=320, height=240):
    for index in range(count):
        frame = av.VideoFrame(width, height, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes([(index * 23 + 30) % 256]) * plane.buffer_size)
        frame.pts = index * 3000
        frame.time_base = fractions.Fraction(1, 90000)
        yield frame


def test_candidates_follow_the_preference_and_the_override(monkeypatch):
    available = {"libopenh264", "h264_mf", "h264_nvenc", "libx264"}
    assert h264_encoders.encoder_candidates(available) == ("libx264", "libopenh264", "h264_mf")
    assert h264_encoders.encoder_candidates({"h264_mf", "vp8"}) == ("h264_mf",)
    monkeypatch.setenv(h264_encoders.ENCODER_ENV, "h264_nvenc")
    assert h264_encoders.encoder_candidates(available) == ("h264_nvenc", "libx264", "libopenh264", "h264_mf")
    monkeypatch.setenv(h264_encoders.ENCODER_ENV, "not_built_here")
    assert h264_encoders.encoder_candidates(available)[0] == "libx264"


def test_selection_skips_an_encoder_that_does_not_open_and_remembers(monkeypatch):
    probes = []

    def opens(name):
        probes.append(name)
        return name != "libx264"

    monkeypatch.setattr(h264_encoders, "_opens", opens)
    monkeypatch.setattr(av, "codecs_available", {"libx264", "libopenh264", "h264_mf"})
    assert h264_encoders.select_h264_encoder() == "libopenh264"
    assert h264_encoders.select_h264_encoder() == "libopenh264"
    assert probes == ["libx264", "libopenh264"]


def test_selection_fails_clearly_when_no_encoder_works(monkeypatch):
    monkeypatch.setattr(h264_encoders, "_opens", lambda name: False)
    monkeypatch.setattr(av, "codecs_available", {"libopenh264"})
    with pytest.raises(RuntimeError, match="No H.264 encoder"):
        h264_encoders.select_h264_encoder()


def test_aiortc_sends_h264_that_decodes_without_x264(monkeypatch):
    _without_x264(monkeypatch)
    if not h264_encoders.encoder_candidates():
        pytest.skip("this FFmpeg build has no OpenH264 or Media Foundation encoder")
    h264_encoders.install_aiortc_h264_encoder()
    h264_encoders.install_aiortc_h264_encoder()  # a second call changes nothing
    assert aiortc_h264.H264Encoder._encode_frame._autoyou_h264

    codec = RTCRtpCodecParameters(mimeType="video/H264", clockRate=90000, payloadType=102)
    encoder, decoder = get_encoder(codec), get_decoder(codec)
    decoded = 0
    for index, frame in enumerate(_frames(8)):
        payloads, timestamp = encoder.encode(frame, force_keyframe=index == 0)
        if payloads:
            data = b"".join(aiortc_h264.h264_depayload(payload) for payload in payloads)
            decoded += len(decoder.decode(JitterFrame(data=data, timestamp=timestamp)))
    assert encoder.codec.name in ("libopenh264", "h264_mf")
    assert decoded > 0


def test_recording_uses_the_selected_encoder_then_mpeg4(monkeypatch):
    monkeypatch.setattr(h264_encoders, "select_h264_encoder", lambda: "libopenh264")
    assert video_call_manager._recording_video_codecs() == ("libopenh264", "mpeg4")

    def unavailable():
        raise RuntimeError("No H.264 encoder is available")

    monkeypatch.setattr(h264_encoders, "select_h264_encoder", unavailable)
    assert video_call_manager._recording_video_codecs() == ("mpeg4",)
