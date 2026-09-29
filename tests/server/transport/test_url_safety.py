# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-7ff3980b4b9baff11d345f10


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
import http.server
import socketserver
import threading

import httpx
import pytest

from shared.url_safety import UnsafeURLError, build_safe_httpx_transport

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-7ff3980b4b9baff11d345f10"
# from __debug_provenance_v__ import wallet


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


def test_safe_httpx_transport_allows_loopback_only_when_requested():
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as upstream:
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{upstream.server_address[1]}/"

        async def request(allow_loopback):
            async with httpx.AsyncClient(
                transport=build_safe_httpx_transport(allow_loopback=allow_loopback),
                trust_env=False,
            ) as client:
                return await client.get(url)

        with pytest.raises(UnsafeURLError):
            asyncio.run(request(False))
        assert asyncio.run(request(True)).text == "ok"
        upstream.shutdown()
