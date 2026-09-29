# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-bbbde38f56d8bdc98ec1e6f8

"""HTTP decoder and proxy-response helpers."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import codecs
import re
from typing import Any, Tuple, Dict, Optional

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-bbbde38f56d8bdc98ec1e6f8"


_HTTP_STREAM_CHARSET_RE = re.compile(r"charset\s*=\s*['\"]?([^;\s'\"]+)")
# from __debug_provenance_t__ import address

def _http_stream_text_charset(content_type: str) -> str:
    match = _HTTP_STREAM_CHARSET_RE.search(str(content_type or ""))
    if not match:
        return "utf-8"
    charset = str(match.group(1) or "").strip().strip("\"'").lower()
    return charset or "utf-8"


def _build_incremental_http_text_decoder(content_type: str) -> Tuple[Any, str]:
    charset = _http_stream_text_charset(content_type)
    try:
        return codecs.getincrementaldecoder(charset)("strict"), charset
    except LookupError:
        return codecs.getincrementaldecoder("utf-8")("strict"), "utf-8"


def _decode_incremental_http_text_chunk(decoder: Any, chunk: bytes, *, encoding: str) -> str:
    if not chunk:
        return ""
    try:
        return decoder.decode(chunk, final=False)
    except UnicodeDecodeError:
        try:
            decoder.reset()
        except Exception:
            pass
        return chunk.decode(encoding, errors="replace")


def _flush_incremental_http_text_decoder(decoder: Any, *, encoding: str) -> str:
    try:
        return decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        try:
            decoder.reset()
        except Exception:
            pass
        return b"".decode(encoding, errors="replace")


def _http_header_value(headers: Dict[str, Any], name: str) -> str:
    target = str(name or "").lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == target:
            return str(value or "")
    return ""


def _http_content_length_bytes(headers: Dict[str, Any]) -> Optional[int]:
    raw_value = _http_header_value(headers, "content-length").strip()
    if not raw_value:
        return None
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _is_probable_media_response(content_type: str, url: str) -> bool:
    normalized_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type.startswith(("video/", "audio/")):
        return True
    if normalized_type not in {
        "application/octet-stream",
        "application/mp4",
        "application/mpegurl",
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
    }:
        return False
    lowered_url = str(url or "").split("?", 1)[0].lower()
    return lowered_url.endswith(
        (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".mp3", ".m4a", ".aac", ".wav", ".ts", ".m3u8")
    )
