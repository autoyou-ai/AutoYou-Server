# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Native PyAV codec/DSP adapter; no socket, RTP, SDP or peer connection.

The Rust owner validates AYMF bytes and source consent before decode. Instances
are used serially by an owned codec worker, and closed only after it is joined.
Audio always uses 48 kHz and 20 ms packets; PCM16 requires explicit negotiation.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import struct
from typing import Any

import av
import numpy as np

from shared.session_media import MediaCodec, MediaKind, MediaSourceBinding
from shared.session_transport import SessionDenied


@dataclass(frozen=True)
class EncodingQuality:
    audio_bitrate: int = 64000
    video_bitrate: int = 3000000
    h264_encoder: str = "libopenh264"

    def check(self) -> None:
        if type(self.audio_bitrate) is not int or not 16000 <= self.audio_bitrate <= 256000 or \
                type(self.video_bitrate) is not int or not 100000 <= self.video_bitrate <= 50000000 or \
                self.h264_encoder not in {"libopenh264", "libx264"}:
            raise ValueError("invalid approved media encoding profile")


class AVMediaEncoder:
    def __init__(self, *, api: Any, binding: MediaSourceBinding, quality: EncodingQuality) -> None:
        quality.check()
        self.api, self.binding, self.quality = api, binding, quality
        self._codec = None
        self._closed = False
        self._sequence = 0
        self._input_samples = 0
        self._input_profile = None
        self._encoded_samples = 0
        self._audio_origin_us = None
        self._audio_discontinuous = False
        self._resampler = None
        self._fifo = None
        self._dimensions = None
        self._last_video_us = None
        self._quality_scale = 1000
        self._keyframe_required = True
        if binding.kind in {MediaKind.MICROPHONE, MediaKind.SYSTEM_AUDIO}:
            self._resampler = av.AudioResampler(format="s16", layout="mono" if binding.channels == 1 else "stereo", rate=48000)
            self._fifo = av.AudioFifo()
            if binding.codec == MediaCodec.OPUS:
                self._codec = av.CodecContext.create("libopus", "w")
                self._codec.sample_rate = 48000
                self._codec.layout = "mono" if binding.channels == 1 else "stereo"
                self._codec.format = "s16"
                self._codec.time_base = Fraction(1, 48000)
                self._codec.bit_rate = quality.audio_bitrate
                self._codec.options = {"application": "voip", "frame_duration": "20"}
            elif binding.codec != MediaCodec.PCM16:
                raise SessionDenied("audio codec was not negotiated")
        elif binding.codec not in {MediaCodec.H264, MediaCodec.VP8}:
            raise SessionDenied("video codec was not negotiated")

    def feedback(self, feedback: Any) -> None:
        scale = feedback.quality_scale_permille
        if type(scale) is not int or not 250 <= scale <= 1000:
            raise ValueError("invalid local media feedback")
        self._keyframe_required |= bool(feedback.keyframe_required)
        if abs(scale-self._quality_scale) >= 100:
            self._quality_scale = scale
            # Reinitialization applies the selected bitrate to native encoders
            # which don't reconfigure an already-open AVCodecContext. The next
            # video frame contains a complete keyframe/parameter set again.
            if self.binding.kind in {MediaKind.CAMERA, MediaKind.SCREEN}:
                self._codec = None
                self._keyframe_required = True

    def _packet(self, data: bytes, *, timestamp_us: int, duration_us: int,
                width: int = 0, height: int = 0, keyframe: bool = False) -> Any:
        packet = self.api.MediaPacket(kind=int(self.binding.kind), codec=int(self.binding.codec), keyframe=keyframe,
            media_generation=self.binding.media_generation, authorization_epoch=self.binding.session.authorization_epoch,
            source_id=self.binding.source_id, sequence=self._sequence, timestamp_us=timestamp_us,
            duration_us=duration_us, width=width, height=height, channels=self.binding.channels, data=data)
        self._sequence += 1
        return packet

    def frame_dropped(self) -> None:
        self._keyframe_required = True
        if self.binding.kind in {MediaKind.MICROPHONE,MediaKind.SYSTEM_AUDIO}:
            self._audio_discontinuous = True

    def encode(self, frame: Any, timestamp_us: int) -> list[Any]:
        if self._closed:
            raise ConnectionError("media encoder is closed")
        if type(timestamp_us) is not int or not 0 <= timestamp_us < 2**63:
            raise ValueError("invalid native monotonic media clock")
        if self.binding.kind in {MediaKind.MICROPHONE, MediaKind.SYSTEM_AUDIO}:
            return self._audio(frame, timestamp_us)
        return self._video(frame, timestamp_us)

    def _audio(self, frame: Any, timestamp_us: int) -> list[Any]:
        if not isinstance(frame, av.AudioFrame) or not 8000 <= frame.sample_rate <= 192000 or \
                not 0 < frame.samples <= frame.sample_rate//5 or len(frame.layout.channels) > 2:
            raise ValueError("invalid bounded native audio frame")
        profile = (frame.sample_rate,frame.format.name,frame.layout.name)
        if self._input_profile is not None and self._input_profile != profile:
            raise ValueError("audio capture format change requires a new approved source generation")
        self._input_profile = profile
        if self._audio_discontinuous:
            # Expired hardware/codec backlog must not be encoded later or make
            # new samples inherit the clock of an old, undelivered audio frame.
            self._resampler = av.AudioResampler(format="s16",layout="mono" if self.binding.channels == 1 else "stereo",rate=48000)
            self._fifo = av.AudioFifo()
            self._audio_origin_us = timestamp_us-self._encoded_samples*1_000_000//48000
            self._audio_discontinuous = False
        if self._audio_origin_us is None:
            self._audio_origin_us = timestamp_us
        # Existing tracks may fan out a frame to several consumers. Do not
        # rewrite its timestamps while normalizing this encoder's sample clock.
        sample = av.AudioFrame.from_ndarray(frame.to_ndarray(), format=frame.format.name, layout=frame.layout.name)
        sample.sample_rate = frame.sample_rate
        sample.pts = self._input_samples
        sample.time_base = Fraction(1, frame.sample_rate)
        self._input_samples += frame.samples
        converted = self._resampler.resample(sample)
        for normalized in converted:
            normalized.pts = None
            self._fifo.write(normalized)
        if self._fifo.samples > 9600:
            raise ValueError("audio capture backlog exceeds the bounded codec input")
        output = []
        while self._fifo.samples >= 960:
            normalized = self._fifo.read(960)
            normalized.pts = self._encoded_samples
            normalized.time_base = Fraction(1, 48000)
            stamp = self._audio_origin_us+self._encoded_samples*1_000_000//48000
            if self.binding.codec == MediaCodec.PCM16:
                bodies = [normalized.to_ndarray().tobytes()]
            else:
                bodies = [bytes(packet) for packet in self._codec.encode(normalized)]
                if len(bodies) != 1:
                    raise RuntimeError("negotiated Opus encoder did not produce one 20 ms packet")
            self._encoded_samples += 960
            output.extend(self._packet(body, timestamp_us=stamp, duration_us=20000) for body in bodies)
        return output

    def _video(self, frame: Any, timestamp_us: int) -> list[Any]:
        if not isinstance(frame, av.VideoFrame) or not 0 < frame.width <= 8192 or not 0 < frame.height <= 8192:
            raise ValueError("invalid bounded native video frame")
        interval = max(1, 1_000_000//self.binding.fps)
        if self._last_video_us is not None and timestamp_us-self._last_video_us < interval-2000:
            return []
        ratio = min(1.0, self.binding.width/frame.width, self.binding.height/frame.height)
        width, height = int(frame.width*ratio)//2*2, int(frame.height*ratio)//2*2
        if width < 2 or height < 2:
            raise ValueError("native video dimensions cannot encode 4:2:0")
        if self._codec is None or self._dimensions != (width,height):
            name = self.quality.h264_encoder if self.binding.codec == MediaCodec.H264 else "libvpx"
            self._codec = av.CodecContext.create(name,"w")
            self._codec.width, self._codec.height, self._codec.pix_fmt = width, height, "yuv420p"
            self._codec.time_base = Fraction(1,1_000_000)
            self._codec.framerate = Fraction(self.binding.fps,1)
            self._codec.bit_rate = max(100000,self.quality.video_bitrate*self._quality_scale//1000)
            self._codec.max_b_frames = 0
            self._codec.thread_count = 1
            self._codec.gop_size = self.binding.fps*2
            if name == "libx264":
                self._codec.options = {"preset":"veryfast","tune":"zerolatency","profile":"baseline",
                    "x264-params":"annexb=1:repeat-headers=1:bframes=0"}
            elif name == "libvpx":
                self._codec.options = {"deadline":"realtime","lag-in-frames":"0","cpu-used":"8"}
            self._dimensions = (width,height)
            self._keyframe_required = True
        converted = frame.reformat(width=width,height=height,format="yuv420p")
        # Reformat can return the original frame. Copy before setting encoder
        # timing/keyframe metadata shared by other current capture consumers.
        if converted is frame:
            converted = av.VideoFrame.from_ndarray(frame.to_ndarray(format="yuv420p"),format="yuv420p")
        converted.pts, converted.time_base = timestamp_us, Fraction(1,1_000_000)
        if self._keyframe_required:
            converted.pict_type = av.video.frame.PictureType.I
        packets = self._codec.encode(converted)
        if len(packets) != 1:
            raise RuntimeError("negotiated video encoder did not produce one independent frame")
        packet = packets[0]
        if self._keyframe_required and not packet.is_keyframe:
            raise RuntimeError("native video encoder ignored requested keyframe")
        self._keyframe_required = False
        self._last_video_us = timestamp_us
        return [self._packet(bytes(packet),timestamp_us=timestamp_us,duration_us=interval,
            width=width,height=height,keyframe=bool(packet.is_keyframe))]

    def close(self) -> None:
        self._closed = True
        self._codec = self._fifo = self._resampler = None


@dataclass(frozen=True)
class CapturedMedia:
    """Local adapter receipt; its timestamp includes time spent in device queues."""
    frame: Any
    captured_at_us: int
    discontinuity: bool = False
    current_check: Any = None  # Local-only capture fence; never serialized.


@dataclass(frozen=True)
class DecodedMedia:
    frames: tuple[Any, ...]
    # Concealment is available only to audio playout, never speech/recording.
    concealed: tuple[Any, ...]
    presentation_us: int
    rate_adjustment_ppm: int
    expires_at_us: int = 0


class AVMediaDecoder:
    def __init__(self, binding: MediaSourceBinding) -> None:
        self.binding = binding
        self._codec = None
        self._closed = False
        self._geometry = None

    def _reset(self) -> None:
        self._codec = None
        if self.binding.codec != MediaCodec.PCM16:
            self._codec = av.CodecContext.create({MediaCodec.OPUS:"opus",MediaCodec.H264:"h264",MediaCodec.VP8:"vp8"}[self.binding.codec],"r")
            self._codec.thread_count = 1
            if self.binding.codec == MediaCodec.OPUS:
                self._codec.extradata = b"OpusHead"+bytes([1,self.binding.channels])+struct.pack("<HIhB",0,48000,0,0)

    def decode(self, playout: Any) -> DecodedMedia:
        if self._closed:
            raise ConnectionError("media decoder is closed")
        packet = playout.packet
        if (packet.source_id,packet.media_generation,packet.authorization_epoch,packet.kind,packet.codec) != (
                self.binding.source_id,self.binding.media_generation,self.binding.session.authorization_epoch,
                int(self.binding.kind),int(self.binding.codec)):
            raise SessionDenied("decoded media source is superseded")
        audio = self.binding.kind in {MediaKind.MICROPHONE,MediaKind.SYSTEM_AUDIO}
        if audio:
            if packet.channels != self.binding.channels or packet.duration_us != 20000 or len(packet.data) > 16384:
                raise ValueError("decoded audio profile changed")
        elif not 0 < packet.width <= self.binding.width or not 0 < packet.height <= self.binding.height or len(packet.data) > 4*1024*1024:
            raise ValueError("decoded video profile changed")
        if playout.reset_decoder or self._codec is None:
            if not audio and not packet.keyframe:
                raise SessionDenied("video decoder reset requires a keyframe")
            self._reset()
        if not audio and self._geometry not in {None,(packet.width,packet.height)} and not packet.keyframe:
            raise SessionDenied("video geometry changed without a keyframe")
        if self.binding.codec == MediaCodec.PCM16:
            if len(packet.data) != 960*self.binding.channels*2:
                raise ValueError("invalid diagnostic PCM16 frame")
            frame = av.AudioFrame(format="s16",layout="mono" if self.binding.channels == 1 else "stereo",samples=960)
            frame.planes[0].update(bytes(packet.data))
            frame.sample_rate = 48000
            frames = [frame]
        else:
            frames = self._codec.decode(av.Packet(bytes(packet.data)))
        if len(frames) > 1:
            raise ValueError("encoded frame expanded to multiple native pictures")
        for frame in frames:
            if audio:
                if frame.samples > 960 or frame.sample_rate != 48000 or len(frame.layout.channels) != self.binding.channels:
                    raise ValueError("decoded audio exceeded its negotiated sample bound")
                frame.pts, frame.time_base = packet.timestamp_us*48000//1_000_000, Fraction(1,48000)
            else:
                if (frame.width,frame.height) != (packet.width,packet.height):
                    raise ValueError("decoded video dimensions differ from checked geometry")
                self._geometry = (frame.width,frame.height)
                frame.pts, frame.time_base = packet.timestamp_us, Fraction(1,1_000_000)
        concealment = []
        if audio:
            if not 0 <= playout.missing_audio_frames <= 5:
                raise ValueError("unbounded renderer concealment")
            for index in range(playout.missing_audio_frames):
                silence = av.AudioFrame(format="s16",layout="mono" if self.binding.channels == 1 else "stereo",samples=960)
                silence.planes[0].update(bytes(1920*self.binding.channels))
                silence.sample_rate = 48000
                silence.pts = packet.timestamp_us*48000//1_000_000-(playout.missing_audio_frames-index)*960
                silence.time_base = Fraction(1,48000)
                concealment.append(silence)
        return DecodedMedia(tuple(frames),tuple(concealment),playout.presentation_us,playout.rate_adjustment_ppm,
            getattr(playout,"expires_at_us",0))

    def close(self) -> None:
        self._closed = True
        self._codec = None


class AudioRateMatcher:
    """Small continuous resampling correction for the real playback device.

    Model/recording consumers keep the original samples. The native device
    consumer supplies queue feedback and uses these copies for playout only.
    """
    def __init__(self, channels: int) -> None:
        if channels not in {1,2}:
            raise ValueError("invalid playback channels")
        self.channels = channels
        self._resampler = av.AudioResampler(format="s16",layout="mono" if channels == 1 else "stereo",rate=48000)
        self._tail = None
        self._position = 0.0

    def apply(self, frame: Any, rate_adjustment_ppm: int) -> tuple[Any, ...]:
        if type(rate_adjustment_ppm) is not int or not -500 <= rate_adjustment_ppm <= 500 or frame.samples > 960:
            raise ValueError("unbounded audio device correction")
        output = []
        for normalized in self._resampler.resample(frame):
            samples = normalized.to_ndarray().reshape(-1,self.channels)
            if self._tail is not None:
                samples = np.concatenate((self._tail,samples),axis=0)
            step = 1+rate_adjustment_ppm/1_000_000
            positions = np.arange(self._position,max(0,len(samples)-1),step)
            if len(positions):
                lower = positions.astype(np.int64)
                fraction = (positions-lower)[:,None]
                corrected = np.rint(samples[lower]*(1-fraction)+samples[lower+1]*fraction).clip(-32768,32767).astype(np.int16)
                result = av.AudioFrame.from_ndarray(corrected.reshape(1,-1),format="s16",layout="mono" if self.channels == 1 else "stereo")
                result.sample_rate, result.time_base = 48000, Fraction(1,48000)
                output.append(result)
                self._position = float(positions[-1]+step-(len(samples)-1))
            else:
                self._position = max(0.0,self._position-(len(samples)-1))
            self._tail = samples[-1:].copy()
        return tuple(output)

    def reset(self) -> None:
        self._tail = None
        self._position = 0.0
        self._resampler = av.AudioResampler(format="s16",layout="mono" if self.channels == 1 else "stereo",rate=48000)
