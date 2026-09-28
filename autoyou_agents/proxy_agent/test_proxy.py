# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-746f20706179203130252061-696ce5ac60d4725e79193ea2

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-746f20706179203130252061-696ce5ac60d4725e79193ea2"


import pytest

from .proxy import normalize_target, normalize_websocket_target, rewrite_html


def test_proxy_rejects_private_targets():
    with pytest.raises(ValueError):
        normalize_target("http://127.0.0.1:8001/")
    with pytest.raises(ValueError):
        normalize_websocket_target("wss://127.0.0.1/socket")


def test_proxy_rewrites_public_html_links():
    body = rewrite_html(b'<a href="/docs">Docs</a>', base_url="https://example.com/start", endpoint="/proxy")
    assert b"/proxy?url=https%3A%2F%2Fexample.com%2Fdocs" in body
    assert b"WrappedWebSocket" in body
