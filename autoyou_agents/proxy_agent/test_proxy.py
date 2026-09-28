from __future__ import annotations

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
