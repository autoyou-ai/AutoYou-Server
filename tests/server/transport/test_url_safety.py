import asyncio
import http.server
import socketserver
import threading

import httpx
import pytest

from shared.url_safety import UnsafeURLError, build_safe_httpx_transport


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
