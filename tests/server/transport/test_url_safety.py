# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-7ff3980b4b9baff11d345f10


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"
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


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    hosts: list = []

    def do_GET(self):
        type(self).hosts.append(self.headers.get("Host"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


def test_safe_requests_session_allows_loopback_only_when_requested():
    from shared.url_safety import build_safe_requests_session

    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as upstream:
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{upstream.server_address[1]}/"
        with build_safe_requests_session(allow_loopback=True) as session:
            assert session.get(url, timeout=5).text == "ok"
        with build_safe_requests_session() as session, pytest.raises(UnsafeURLError):
            session.get(url, timeout=5)
        upstream.shutdown()


def test_safe_requests_session_connects_to_the_validated_address(monkeypatch):
    import shared.url_safety as url_safety

    resolved = []

    def fake_resolve(host):
        resolved.append(host)
        assert host == "pinned.example.test"
        return "127.0.0.1"

    monkeypatch.setattr(url_safety, "resolve_safe_http_ip", fake_resolve)
    _RecordingHandler.hosts = []
    with socketserver.TCPServer(("127.0.0.1", 0), _RecordingHandler) as upstream:
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        port = upstream.server_address[1]
        with url_safety.build_safe_requests_session() as session:
            assert session.get(f"http://pinned.example.test:{port}/", timeout=5).text == "ok"
        upstream.shutdown()
    # The TCP connection used the validated answer; HTTP kept the original host.
    assert resolved == ["pinned.example.test"]
    assert _RecordingHandler.hosts == [f"pinned.example.test:{port}"]


def test_proxy_relay_refuses_a_host_that_rebinds_to_a_private_address(monkeypatch):
    import socket

    import shared.url_safety as url_safety
    from autoyou_agents.proxy_agent.proxy import fetch_target

    # Validation sees a public answer twice; the connection-time lookup rebinds
    # to loopback. Without pinning, the request would reach the local server.
    answers = iter(["203.0.113.10", "203.0.113.10", "127.0.0.1"])
    real_is_unsafe = url_safety._is_unsafe_ip

    def rebinding_getaddrinfo(host, *args, **kwargs):
        assert host == "rebind.example.test"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers, "127.0.0.1"), 0))]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", rebinding_getaddrinfo)
    # Treat the synthetic documentation address as a public answer.
    monkeypatch.setattr(url_safety, "_is_unsafe_ip", lambda ip: str(ip) != "203.0.113.10" and real_is_unsafe(ip))
    _RecordingHandler.hosts = []
    with socketserver.TCPServer(("127.0.0.1", 0), _RecordingHandler) as upstream:
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        port = upstream.server_address[1]
        with pytest.raises(ValueError, match="disallowed address"):
            fetch_target(f"http://rebind.example.test:{port}/")
        upstream.shutdown()
    assert _RecordingHandler.hosts == []


def test_safe_https_pins_socket_but_preserves_tls_hostname(monkeypatch):
    from shared import url_safety
    from urllib3.connection import HTTPSConnection
    observed = []
    monkeypatch.setattr(url_safety, "resolve_safe_http_ip", lambda host: "203.0.113.20")
    def connect(self):
        observed.append(self._dns_host)
        return "synthetic-socket"
    monkeypatch.setattr(HTTPSConnection, "_new_conn", connect)
    with url_safety.build_safe_requests_session() as session:
        pool = session.get_adapter("https://public.example").poolmanager.connection_from_host("public.example", port=443, scheme="https")
        connection = pool._new_conn()
        assert connection._new_conn() == "synthetic-socket"
        assert observed == ["203.0.113.20"]
        assert connection.host == "public.example"


def test_private_service_exception_is_explicit_and_keeps_socket_pinning(monkeypatch):
    import ipaddress
    from shared import url_safety
    from urllib3.connection import HTTPConnection

    monkeypatch.setattr(url_safety, "_iter_candidate_ips", lambda host: iter([ipaddress.ip_address("10.20.0.5")]))
    monkeypatch.setattr(HTTPConnection, "_new_conn", lambda connection: connection._dns_host)
    with url_safety.build_safe_requests_session(allow_private_hosts=("search.internal.test",)) as session:
        def connection(host):
            pool = session.get_adapter("http://").poolmanager.connection_from_host(host, port=8080, scheme="http")
            return pool._new_conn()
        trusted = connection("search.internal.test")
        assert trusted._new_conn() == "10.20.0.5"
        assert trusted.host == "search.internal.test"
        with pytest.raises(UnsafeURLError, match="disallowed address"):
            connection("another.internal.test")._new_conn()


def test_configured_private_search_host_does_not_bypass_page_fetch_guard(monkeypatch):
    import ipaddress
    from shared import url_safety
    from autoyou_agents.internet_agent.internet_tool import InternetTool
    from urllib3.connection import HTTPConnection

    monkeypatch.setenv("AUTOYOU_INTERNET_ALLOW_PRIVATE_IPS", "0")
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://search.internal.test:8080")
    monkeypatch.setattr(url_safety, "_iter_candidate_ips", lambda host: iter([ipaddress.ip_address("10.20.0.5")]))
    monkeypatch.setattr(HTTPConnection, "_new_conn", lambda connection: connection._dns_host)
    internet = InternetTool()
    try:
        pool = internet.session.get_adapter("http://").poolmanager.connection_from_host("search.internal.test", port=8080, scheme="http")
        assert pool._new_conn()._new_conn() == "10.20.0.5"
        assert internet._http_get_safe("http://search.internal.test:8080/private")["status_code"] == 403
    finally:
        internet.session.close()
