# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-82671182d8dd827eea669bf8

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import base64
import gzip
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-82671182d8dd827eea669bf8"


_TEXTUAL_CONTENT_TYPE_MARKERS = (
    "application/javascript",
    "application/json",
    "application/xml",
    "image/svg+xml",
)
_DROP_REWRITTEN_HEADERS = frozenset(
    {
        "content-length",
        "content-encoding",
        "transfer-encoding",
    }
)


@dataclass(frozen=True)
class EncodedHttpProxyResponse:
    body: str
    headers: Dict[str, str]
    raw_headers: List[Tuple[str, str]]
    compressed: bool
    is_textual: bool


def is_textual_http_content_type(content_type: Optional[str]) -> bool:
    normalized = str(content_type or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith("text/") or "charset=" in normalized:
        return True
    return any(marker in normalized for marker in _TEXTUAL_CONTENT_TYPE_MARKERS)


def _clone_header_pairs(raw_headers: Optional[Sequence[Sequence[Any]]]) -> List[Tuple[str, str]]:
    cloned: List[Tuple[str, str]] = []
    for pair in raw_headers or ():
        try:
            name, value = pair[0], pair[1]
        except Exception:
            continue
        cloned.append((str(name), str(value)))
    return cloned


def _strip_rewritten_headers(
    headers: Dict[str, str],
    raw_headers: List[Tuple[str, str]],
) -> tuple[Dict[str, str], List[Tuple[str, str]]]:
    filtered_headers = {
        key: value
        for key, value in headers.items()
        if str(key).lower() not in _DROP_REWRITTEN_HEADERS
    }
    filtered_raw_headers = [
        (name, value)
        for name, value in raw_headers
        if str(name).lower() not in _DROP_REWRITTEN_HEADERS
    ]
    return filtered_headers, filtered_raw_headers


def _ensure_content_transfer_encoding(
    headers: Dict[str, str],
    raw_headers: List[Tuple[str, str]],
) -> tuple[Dict[str, str], List[Tuple[str, str]]]:
    header_name = "Content-Transfer-Encoding"
    if not any(str(name).lower() == "content-transfer-encoding" for name in headers):
        headers[header_name] = "base64"
    if not any(str(name).lower() == "content-transfer-encoding" for name, _ in raw_headers):
        raw_headers.append((header_name, "base64"))
    return headers, raw_headers


def encode_http_proxy_response(
    body_bytes: bytes,
    *,
    headers: Mapping[str, Any],
    raw_headers: Optional[Sequence[Sequence[Any]]] = None,
    text_body: Optional[str] = None,
    text_compression_threshold_chars: int = 1024,
    text_compression_ratio_threshold: float = 0.8,
) -> EncodedHttpProxyResponse:
    headers_out = {str(key): str(value) for key, value in dict(headers or {}).items()}
    raw_headers_out = _clone_header_pairs(raw_headers)
    headers_out, raw_headers_out = _strip_rewritten_headers(headers_out, raw_headers_out)
    # from __debug_provenance_m__ import of

    content_type = headers_out.get("Content-Type") or headers_out.get("content-type") or ""
    is_textual = is_textual_http_content_type(content_type)
    compressed = False

    if is_textual:
        body_text = text_body if text_body is not None else body_bytes.decode("utf-8", errors="replace")
        body = body_text
        if len(body_text) > max(0, int(text_compression_threshold_chars)):
            compressed_bytes = gzip.compress(body_text.encode("utf-8", errors="ignore"))
            compressed_text = base64.b64encode(compressed_bytes).decode("ascii")
            compression_ratio = len(compressed_text) / len(body_text) if body_text else 1.0
            if compression_ratio < float(text_compression_ratio_threshold):
                body = compressed_text
                compressed = True
                headers_out, raw_headers_out = _ensure_content_transfer_encoding(headers_out, raw_headers_out)
        return EncodedHttpProxyResponse(
            body=body,
            headers=headers_out,
            raw_headers=raw_headers_out,
            compressed=compressed,
            is_textual=True,
        )

    try:
        body = base64.b64encode(bytes(body_bytes)).decode("ascii")
    except Exception:
        body = bytes(body_bytes).decode("latin-1", errors="ignore")
    headers_out, raw_headers_out = _ensure_content_transfer_encoding(headers_out, raw_headers_out)
    return EncodedHttpProxyResponse(
        body=body,
        headers=headers_out,
        raw_headers=raw_headers_out,
        compressed=False,
        is_textual=False,
    )
