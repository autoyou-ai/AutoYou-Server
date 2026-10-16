# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Preserve duplicate HTTP fields through the existing target/header policy."""
from __future__ import annotations

import re
from typing import Any, Callable

_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
NATIVE_STREAM_HEADER = "X-AutoYou-Application-Transport"
NATIVE_HTML_HEADER = "X-AutoYou-Agent-HTML-Adapted"


class InvalidHTTPHeaders(ValueError):
    """Peer input rejected before any upstream write."""


def forward_header_pairs(raw: Any, fallback: dict[str, Any], *,
                         sanitize: Callable[[dict[str, str]], dict[str, str]],
                         canonical: dict[str, str]) -> list[tuple[str, str]]:
    if not isinstance(fallback, dict):
        raise InvalidHTTPHeaders("invalid native HTTP header map")
    source = raw if raw is not None else list(fallback.items())
    if not isinstance(source, (list, tuple)) or len(source) > 256:
        raise InvalidHTTPHeaders("invalid native HTTP headers")
    pairs: list[tuple[str, str]] = []
    size = 0
    for pair in source:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise InvalidHTTPHeaders("invalid native HTTP header pair")
        name, value = pair
        if not isinstance(name, str) or not isinstance(value, str) or not _TOKEN.fullmatch(name) or \
                any(ord(char) < 32 and char != '\t' or ord(char) == 127 or ord(char) > 255 for char in value):
            raise InvalidHTTPHeaders("invalid native HTTP header value")
        size += len(name) + len(value)
        if size > 16 * 1024:
            raise InvalidHTTPHeaders("native HTTP headers exceed their bound")
        pairs.append((name, value))
    hop_tokens = {token.strip().lower() for name, value in pairs if name.lower() == "connection"
                  for token in value.split(",") if token.strip()}
    lengths = [value.strip() for name, value in pairs if name.lower() == "content-length"]
    if lengths and (any(not value.isascii() or not value.isdecimal() for value in lengths) or len(set(lengths)) != 1):
        raise InvalidHTTPHeaders("conflicting native HTTP content lengths")
    trusted = {name.lower() for name in canonical}
    output: list[tuple[str, str]] = []
    for name, value in pairs:
        if name.lower() in trusted or name.lower() in hop_tokens:
            continue
        accepted = sanitize({name: value})
        # Only retain this source field. The policy may add canonical fields;
        # those are appended once below, never copied from a peer's raw list.
        if any(key.lower() == name.lower() and text == value for key, text in accepted.items()):
            output.append((name, value))
    output.extend(canonical.items())
    return output
