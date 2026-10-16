# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""One native JSON pass; Python handlers receive their final built-in values."""

from functools import lru_cache
import json
import os

MAX_JSON_BYTES = 2 * 1024 * 1024 * 1024


@lru_cache(maxsize=1)
def _native_codec():
    from shared.iroh_release import load_release_config
    if load_release_config() is None and os.environ.get("AUTOYOU_RELEASE_GENERATION") != "iroh":
        return None
    from shared.iroh_binding import load_binding
    return load_binding().application_json


def loads(document):
    codec = _native_codec()
    if codec is None:
        return json.loads(document)
    return codec.loads(document, MAX_JSON_BYTES)


def dumps(value):
    codec = _native_codec()
    if codec is None:
        return json.dumps(value)
    return codec.dumps(value, MAX_JSON_BYTES).decode("utf-8")


def loads_bytes(document: bytes, max_bytes: int):
    if not isinstance(document, bytes) or not 0 < max_bytes <= MAX_JSON_BYTES or len(document) > max_bytes:
        raise ValueError("Invalid application JSON")
    codec = _native_codec()
    if codec is not None:
        return codec.loads(document, max_bytes)
    def nonfinite(_):
        raise ValueError("Non-finite JSON")
    return json.loads(document, parse_constant=nonfinite)


def dumps_bytes(value, max_bytes: int = MAX_JSON_BYTES) -> bytes:
    if not 0 < max_bytes <= MAX_JSON_BYTES:
        raise ValueError("Invalid application JSON")
    codec = _native_codec()
    if codec is not None:
        return codec.dumps(value, max_bytes)
    wire = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(wire) > max_bytes:
        raise ValueError("Invalid application JSON")
    return wire


def decode_envelope(document, api):
    return api.application_json.decode_envelope(document)


def encode_envelope(value, api):
    return api.application_json.encode_envelope(value, MAX_JSON_BYTES)


def prepare_message(value, api):
    lane, content, metadata, data = api.application_json.prepare_message(value)
    if content is None:
        return metadata, lane, None
    return None, lane, api.BrowserMessage(lane=lane, content=api.ByteStreamContent(content), metadata=metadata, data=data)
