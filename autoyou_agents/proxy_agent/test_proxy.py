# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-1a6e25d54a61da377397fa17

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import pytest
import asyncio
import importlib
from starlette.requests import Request
from types import SimpleNamespace

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


@pytest.mark.parametrize("mime", ["text/html", "image/svg+xml", "application/xhtml+xml", "application/xml"])
@pytest.mark.parametrize("path_proxy", [False, True])
def test_active_upstream_documents_are_sandboxed_on_direct_navigation(monkeypatch, mime, path_proxy):
    backend = importlib.import_module("autoyou_agents.proxy_agent.website.backend.app")

    monkeypatch.setattr(backend, "_api_auth_error", lambda *_: None)
    monkeypatch.setattr(backend, "fetch_target", lambda *args, **kwargs: SimpleNamespace(
        body=b"<html><head></head><script>window.syntheticProbe = true</script></html>",
        content_type=mime, final_url="https://example.com/", status_code=200,
    ))

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    headers = [(b"x-autoyou-agent-frontend", b"proxy_agent")] if path_proxy else []
    request = Request({"type": "http", "method": "GET", "path": "/proxy", "headers": headers}, receive)
    response = asyncio.run(backend._serve(request, "https://example.com/"))
    csp = response.headers["content-security-policy"]
    sandbox = next(value.strip() for value in csp.split(";") if value.strip().startswith("sandbox"))
    assert "allow-scripts" in sandbox
    assert "allow-same-origin" not in sandbox
    assert "allow-popups-to-escape-sandbox" not in sandbox
    assert b"window.syntheticProbe" in response.body
    assert response.headers["referrer-policy"] == "no-referrer"
