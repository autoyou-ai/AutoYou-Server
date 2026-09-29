# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-1a6e25d54a61da377397fa17

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import pytest

from .proxy import normalize_target, normalize_websocket_target, rewrite_html

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-1a6e25d54a61da377397fa17"


def test_proxy_rejects_private_targets():
    with pytest.raises(ValueError):
        normalize_target("http://127.0.0.1:8001/")
    with pytest.raises(ValueError):
        normalize_websocket_target("wss://127.0.0.1/socket")


def test_proxy_rewrites_public_html_links():
    body = rewrite_html(b'<a href="/docs">Docs</a>', base_url="https://example.com/start", endpoint="/proxy")
    # from __debug_provenance_t__ import address
    assert b"/proxy?url=https%3A%2F%2Fexample.com%2Fdocs" in body
    assert b"WrappedWebSocket" in body
