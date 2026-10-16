# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Server call-audio policy projected from the client's directional viewpoint."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import unicodedata

from shared.session_transport import SessionDenied


AUDIO_MODES = frozenset({"call", "background_keepalive", "silent_recording"})


def audio_mode(payload):
    mode = payload.get("audio_mode", "call")
    if not isinstance(mode,str) or mode not in AUDIO_MODES:
        raise SessionDenied("native audio mode is outside its supported profile")
    return mode


def native_screen_mode(payload):
    """Screen routing is immutable call consent, independent of AI processing."""
    mode = payload.get("screen_mode", "")
    if not isinstance(mode, str) or mode not in {"", "watch", "interactive"}:
        raise SessionDenied("invalid native screen mode")
    if mode and audio_mode(payload) != "call":
        raise SessionDenied("screen consent cannot authorize background audio")
    if mode == "watch" and payload.get("active") is True and payload.get("audio_microphone", True) is not False:
        raise SessionDenied("watch consent cannot authorize a microphone")
    return mode


def native_audio_scope(record):
    """Validate a local reply target's captured consent, never grant new consent."""
    fields={"version","call_id","expires_at_ms","session_generation","authorization_epoch","audio_instance_id"}
    if not isinstance(record,dict) or set(record) not in (fields,fields|{"speech_generation"}):
        raise SessionDenied("invalid native audio reply scope")
    for key in ("call_id","audio_instance_id"):
        value=record[key]
        if not isinstance(value,str) or not value or len(value.encode("utf-8"))>128 or \
                any(unicodedata.category(char)=="Cc" for char in value):
            raise SessionDenied("invalid native audio reply identity")
    for key in fields-{"call_id","audio_instance_id"} | ({"speech_generation"} if "speech_generation" in record else set()):
        if type(record[key]) is not int or not 0<=record[key]<2**64:
            raise SessionDenied("invalid native audio reply generation")
    if record["version"]!=1 or not record["expires_at_ms"] or not record["session_generation"]:
        raise SessionDenied("unsupported native audio reply scope")
    return dict(record)


@dataclass(frozen=True)
class NativeAudioPolicy:
    revision: int
    send_microphone: bool
    receive_audio: bool
    receive_generation: int
    version: int = 1

    @classmethod
    def parse(cls, record):
        if not isinstance(record, dict) or set(record) != {
            "version", "revision", "send_microphone", "receive_audio", "receive_generation"
        }:
            raise SessionDenied("invalid native audio policy")
        for key in ("version", "revision", "receive_generation"):
            if type(record[key]) is not int:
                raise SessionDenied("native audio policy requires integer generations")
        if record["version"] != 1 or not 1 <= record["revision"] <= 2**63-1 or \
                not 0 <= record["receive_generation"] <= 2**63-1 or \
                type(record["send_microphone"]) is not bool or type(record["receive_audio"]) is not bool or \
                record["receive_audio"] != (record["receive_generation"] > 0):
            raise SessionDenied("native audio policy is outside its supported profile")
        return cls(**record)

    def record(self):
        record = asdict(self)
        self.parse(record)
        return record

    def required_sources(self):
        return tuple(key for allowed, key in (
            (self.send_microphone, ("send", 1)), (self.receive_audio, ("receive", 2))) if allowed)
