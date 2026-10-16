# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Local media adapter contracts; application consent supplies each source.

These are host records, never an enrollment or application wire decoder. The
shared Rust core owns packet parsing. Codecs/capture/renderers receive only a
checked source binding, including the session and application lease floors.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Protocol
import unicodedata
from shared.session_transport import SessionBinding, SessionDenied, SessionRegistry


class MediaKind(IntEnum):
    MICROPHONE = 1
    CAMERA = 2
    SCREEN = 3
    SYSTEM_AUDIO = 4


class MediaCodec(IntEnum):
    OPUS = 1
    H264 = 2
    VP8 = 3
    PCM16 = 4


@dataclass(frozen=True)
class MediaSourceBinding:
    session: SessionBinding
    lease_id: str
    call_id: str
    participant_id: str
    target_id: str
    source_id: int
    media_generation: int
    expires_at_ms: int
    direction: str
    kind: MediaKind
    codec: MediaCodec
    sample_rate: int = 0
    channels: int = 0
    width: int = 0
    height: int = 0
    fps: int = 0
    layout: str = "single"
    maximum_delay_ms: int = 200

    def check(self, registry: SessionRegistry, *, now_ms: int,
              approved_source: MediaSourceBinding, current_media_generation: int) -> None:
        registry.check(self.session, scope="media")
        if any(not isinstance(value, str) or not value or len(value.encode('utf-8')) > 128 or any(unicodedata.category(c) == 'Cc' for c in value)
               for value in (self.lease_id, self.call_id, self.participant_id, self.target_id)):
            raise SessionDenied("invalid media source authority")
        if any(type(value) is not int or not 0 < value < 2**64
               for value in (self.source_id, self.media_generation, self.expires_at_ms)) or \
                self != approved_source or self.media_generation != current_media_generation or \
                not now_ms < self.expires_at_ms <= self.session.expires_at_ms:
            raise SessionDenied("media source lease is expired or superseded")
        if type(self.maximum_delay_ms) is not int or not 20 <= self.maximum_delay_ms <= 2000 or \
                self.direction not in {'send', 'receive'} or self.layout not in {'single', 'composite'} or \
                not isinstance(self.kind, MediaKind) or not isinstance(self.codec, MediaCodec):
            raise SessionDenied("invalid media source profile")
        numbers = (self.sample_rate, self.channels, self.width, self.height, self.fps)
        if any(type(value) is not int for value in numbers):
            raise SessionDenied("invalid media format")
        if self.kind in {MediaKind.MICROPHONE, MediaKind.SYSTEM_AUDIO}:
            if self.codec not in {MediaCodec.OPUS, MediaCodec.PCM16} or self.sample_rate != 48000 or \
                    self.channels not in {1, 2} or any((self.width, self.height, self.fps)):
                raise SessionDenied("invalid audio format")
        elif self.codec not in {MediaCodec.H264, MediaCodec.VP8} or self.sample_rate or self.channels or \
                not 0 < self.width <= 8192 or not 0 < self.height <= 8192 or not 0 < self.fps <= 120:
            raise SessionDenied("invalid video format")

    def lease_record(self) -> dict[str, Any]:
        """Local consent record for Rust; it carries no peer-chosen owner/path."""
        return dict(lease_id=self.lease_id, call_id=self.call_id, participant_id=self.participant_id,
            target_id=self.target_id, source_id=self.source_id, media_generation=self.media_generation,
            authorization_epoch=self.session.authorization_epoch, expires_at_ms=self.expires_at_ms,
            kind=int(self.kind), codec=int(self.codec), sample_rate=self.sample_rate, channels=self.channels,
            width=self.width, height=self.height, fps=self.fps, layout=self.layout, maximum_delay_ms=self.maximum_delay_ms)


class MediaSourceAdapter(Protocol):
    """A source owns capture/playout and restores its devices on close."""
    binding: MediaSourceBinding

    async def capture(self) -> Any: ...
    async def render(self, decoded: Any) -> None: ...
    async def close(self) -> None: ...


class SessionStreamHandler(Protocol):
    """One binary/media/input owner after authenticated stream admission."""

    async def receive(self, channel: Any, frame: Any) -> None: ...
    async def closed(self, binding: SessionBinding) -> None: ...
