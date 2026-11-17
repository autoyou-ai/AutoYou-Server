# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-L-6520726563757272696e6720-d4fb6dc5b6bd65cfde221d6e

"""H.264 encoding that does not depend on x264.

Microsoft Store packages ship an LGPL FFmpeg without the GPL x264 and x265
libraries, so the ``libx264`` encoder does not exist there. aiortc 1.14 creates
its outgoing H.264 encoder as ``libx264`` unconditionally, and FFmpeg's generic
``h264`` name can resolve to a GPU encoder that only opens on matching
hardware. Every H.264 encoder AutoYou creates comes from
:func:`open_h264_encoder` instead, which uses the first encoder in
:data:`ENCODER_PREFERENCE` that actually opens on this computer:

* ``libx264`` where the build has it (source installs, non-Store packages),
  so behaviour there is unchanged;
* ``libopenh264`` (Cisco OpenH264, BSD licensed);
* ``h264_mf``, the Windows Media Foundation encoder;
* ``h264_videotoolbox``, Apple's native H.264 encoder.

``AUTOYOU_H264_ENCODER`` names an encoder to try first.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

from contextlib import suppress
import fractions
import logging
import os
import threading
import time
from typing import Iterable, Iterator, Optional

import av

__debug_trace_l__ = "AUTOYOU-PROVENANCE-L-6520726563757272696e6720-d4fb6dc5b6bd65cfde221d6e"

LOGGER = logging.getLogger(__name__)

ENCODER_PREFERENCE = ("libx264", "libopenh264", "h264_mf", "h264_videotoolbox")
ENCODER_ENV = "AUTOYOU_H264_ENCODER"
# x264's default keyframe interval. OpenH264 and Media Foundation would
# otherwise insert a keyframe every 12 frames; WebRTC asks for one with a PLI.
KEYFRAME_INTERVAL = 250

_usable: dict[str, bool] = {}
_lock = threading.Lock()


def encoder_candidates(available: Optional[Iterable[str]] = None) -> tuple[str, ...]:
    """H.264 encoders this FFmpeg build has, in the order they are tried."""
    names = list(ENCODER_PREFERENCE)
    override = os.environ.get(ENCODER_ENV, "").strip()
    if override:
        names = [override] + [name for name in names if name != override]
    present = set(av.codecs_available if available is None else available)
    return tuple(name for name in names if name in present)


def configure_h264_encoder(
    context: "av.CodecContext",
    name: str,
    *,
    width: int,
    height: int,
    bit_rate: int,
    frame_rate: int,
) -> None:
    """Set real-time, WebRTC-compatible (baseline) options for *name*."""
    context.width = width
    context.height = height
    context.bit_rate = bit_rate
    context.pix_fmt = "yuv420p"
    context.framerate = fractions.Fraction(frame_rate, 1)
    context.time_base = fractions.Fraction(1, frame_rate)
    if name == "libx264":
        # aiortc's own settings, tuned for zero-lookahead realtime encoding.
        context.options = {"level": "31", "tune": "zerolatency", "preset": "ultrafast", "rc-lookahead": "0"}
        with suppress(Exception):
            context.max_b_frames = 0
            context.profile = "Baseline"
    elif name == "libopenh264":
        # Constrained baseline is OpenH264's default profile.
        context.gop_size = KEYFRAME_INTERVAL
        context.options = {"rc_mode": "bitrate"}
    elif name == "h264_mf":
        # Media Foundation encodes baseline unless asked for main or high.
        context.gop_size = KEYFRAME_INTERVAL
        context.max_b_frames = 0
        context.options = {"rate_control": "cbr", "scenario": "video_conference"}
    elif name == "h264_videotoolbox":
        context.gop_size = KEYFRAME_INTERVAL
        context.max_b_frames = 0
        context.flags |= av.codec.context.Flags.low_delay
        context.options = {"profile": "constrained_baseline", "realtime": "1", "allow_sw": "1", "prio_speed": "1"}
    else:
        context.gop_size = KEYFRAME_INTERVAL
        context.max_b_frames = 0


def _create(name: str, width: int, height: int, bit_rate: int, frame_rate: int) -> "av.CodecContext":
    context = av.CodecContext.create(name, "w")
    configure_h264_encoder(
        context, name, width=width, height=height, bit_rate=bit_rate, frame_rate=frame_rate
    )
    return context


def _opens(name: str) -> bool:
    """Whether *name* encodes a frame here (GPU encoders need their hardware)."""
    try:
        context = _create(name, 320, 240, 500_000, 30)
        frame = av.VideoFrame(320, 240, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        frame.pts = 0
        list(context.encode(frame))
        return True
    except Exception as exc:  # FFmpeg raises a different error per encoder
        LOGGER.info("H.264 encoder %s is not usable here: %s", name, exc)
        return False


def select_h264_encoder() -> str:
    """Name of the first H.264 encoder that works on this computer."""
    with _lock:
        for name in encoder_candidates():
            if name not in _usable:
                _usable[name] = _opens(name)
            if _usable[name]:
                return name
    raise RuntimeError(
        "No H.264 encoder is available; tried " + ", ".join(ENCODER_PREFERENCE)
    )


def open_h264_encoder(width: int, height: int, bit_rate: int, frame_rate: int) -> "av.CodecContext":
    """An H.264 encoder context from :func:`select_h264_encoder`, configured."""
    return _create(select_h264_encoder(), width, height, bit_rate, frame_rate)


def install_aiortc_h264_encoder() -> None:
    """Make aiortc's H.264 encoder use :func:`open_h264_encoder`.

    Apply before creating peers. It replaces only the encoder creation in
    aiortc 1.14's ``H264Encoder._encode_frame``; bitstream splitting and RTP
    packetization stay aiortc's own.
    """
    from aiortc.codecs import h264

    if getattr(h264.H264Encoder._encode_frame, "_autoyou_h264", False):
        return

    _BITRATE_REOPEN_RATIO = 0.50
    _BITRATE_REOPEN_COOLDOWN_S = 2.0
    _MIN_TARGET_BITRATE = 200_000

    def _encode_frame(self, frame: av.VideoFrame, force_keyframe: bool) -> Iterator[bytes]:
        raw_bitrate = int(getattr(self, "target_bitrate", 500_000) or 500_000)
        cap_bitrate = getattr(self, "_autoyou_max_bitrate", None)
        if cap_bitrate:
            raw_bitrate = min(raw_bitrate, int(cap_bitrate))
        target_bitrate = max(_MIN_TARGET_BITRATE, raw_bitrate)
        if self.codec:
            size_changed = frame.width != self.codec.width or frame.height != self.codec.height
            codec_bitrate = int(getattr(self.codec, "bit_rate", 0) or 0)
            bitrate_changed = (
                codec_bitrate > 0
                and abs(target_bitrate - codec_bitrate) / codec_bitrate >= _BITRATE_REOPEN_RATIO
                and (time.monotonic() - getattr(self, "_autoyou_last_reopen_at", float("-inf")))
                >= _BITRATE_REOPEN_COOLDOWN_S
            )
            if size_changed or bitrate_changed:
                self.buffer_data = b""
                self.buffer_pts = None
                with suppress(Exception):
                    self.codec.close()
                self.codec = None
                force_keyframe = True

        if force_keyframe:
            frame.pict_type = av.video.frame.PictureType.I
        else:
            frame.pict_type = av.video.frame.PictureType.NONE

        if self.codec is None:
            self.codec = open_h264_encoder(
                frame.width, frame.height, target_bitrate, h264.MAX_FRAME_RATE
            )
            self._autoyou_last_reopen_at = time.monotonic()

        data_to_send = b""
        for package in self.codec.encode(frame):
            data_to_send += bytes(package)

        if data_to_send:
            yield from self._split_bitstream(data_to_send)

    _encode_frame._autoyou_h264 = True
    # from __debug_trace_l__ import autoyou_provenance_l_6520726563757272696e6720_d4fb6dc5b6bd65cfde221d6e
    h264.H264Encoder._encode_frame = _encode_frame
