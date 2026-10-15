# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""The admin sign-in over Docker's loopback-published ports, and exactly nothing more.

A container whose ports are published only on the host's loopback receives a browser on the same computer
from its network gateway, so the sign-in used to answer "This endpoint requires localhost or HTTPS" to the
project's own documented ``docker compose up``. ``AUTOYOU_ADMIN_TRUST_PUBLISHED_LOOPBACK=1`` declares that
setup. These tests pin what it allows and, as important, what it still refuses.
"""
from __future__ import annotations

import pytest
from starlette.requests import Request

import server
from shared.remote_access_policy import REMOTE_BROWSER_HEADER

GATEWAY = "172.17.0.1"          # the Docker bridge gateway as the container sees it
PUBLIC_PEER = "8.8.8.8"  # a public address (documentation ranges count as private)


def _request(headers=None, client=(GATEWAY, 50000)):
    return Request({
        "type": "http", "method": "POST", "scheme": "http", "path": "/login", "raw_path": b"/login",
        "query_string": b"",
        "headers": [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()],
        "client": client, "server": ("testserver", 80),
    })


@pytest.fixture
def declared(monkeypatch):
    monkeypatch.setenv("AUTOYOU_ADMIN_TRUST_PUBLISHED_LOOPBACK", "1")
    monkeypatch.delenv("AUTOYOU_TRUSTED_HTTPS_PROXY", raising=False)


def _refused(request):
    response = server._require_loopback_or_https_request(request)
    return response is not None and response.status_code == 403


def test_without_the_declaration_the_gateway_peer_is_still_refused(monkeypatch):
    monkeypatch.delenv("AUTOYOU_ADMIN_TRUST_PUBLISHED_LOOPBACK", raising=False)
    monkeypatch.delenv("AUTOYOU_TRUSTED_HTTPS_PROXY", raising=False)

    assert _refused(_request({"Host": "localhost:8001"}))
    assert _refused(_request({"Host": "127.0.0.1:8001"}))


@pytest.mark.parametrize("host", ["localhost:8001", "127.0.0.1:8001", "127.0.0.1", "[::1]:8001", "LOCALHOST:8001", "app.localhost:8001"])
def test_a_browser_on_this_computer_is_let_in_once_the_operator_declared_the_setup(declared, host):
    assert server._require_loopback_or_https_request(_request({"Host": host})) is None


@pytest.mark.parametrize("host", ["192.168.1.40:8001", "10.0.0.23:8001", "my-computer.lan:8001", "evil.example:8001", "localhost.evil.example:8001", ""])
def test_a_lan_or_foreign_address_in_the_address_bar_still_needs_https(declared, host):
    assert _refused(_request({"Host": host} if host else {}))


def test_the_declaration_never_trusts_a_public_peer(declared):
    assert _refused(_request({"Host": "localhost:8001"}, client=(PUBLIC_PEER, 50000)))


@pytest.mark.parametrize("header", [
    {"X-Forwarded-For": "10.0.0.23"}, {"X-Forwarded-Host": "evil.example"}, {"X-Real-IP": "10.0.0.23"},
    {"Forwarded": "for=10.0.0.23"}, {REMOTE_BROWSER_HEADER: "home_network"},
])
def test_a_relayed_request_never_counts_as_this_computer(declared, header):
    assert _refused(_request({"Host": "localhost:8001", **header}))


def test_plain_https_and_loopback_behaviour_is_unchanged(declared, monkeypatch):
    assert server._require_loopback_or_https_request(_request({"Host": "10.0.0.23:8001"}, client=("127.0.0.1", 50000))) is None
    secure = Request({"type": "http", "method": "POST", "scheme": "https", "path": "/login", "raw_path": b"/login",
                      "query_string": b"", "headers": [(b"host", b"10.0.0.23:8001")], "client": (GATEWAY, 50000),
                      "server": ("testserver", 443)})
    assert server._require_loopback_or_https_request(secure) is None


@pytest.mark.parametrize("value", ["0", "", "false", "off", "no"])
def test_only_an_explicit_yes_turns_it_on(monkeypatch, value):
    monkeypatch.setenv("AUTOYOU_ADMIN_TRUST_PUBLISHED_LOOPBACK", value)
    monkeypatch.delenv("AUTOYOU_TRUSTED_HTTPS_PROXY", raising=False)

    assert _refused(_request({"Host": "localhost:8001"}))


def test_the_bundled_compose_file_declares_it_and_publishes_only_on_loopback():
    import re

    from tests.support.paths import REPO_ROOT

    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "AUTOYOU_ADMIN_TRUST_PUBLISHED_LOOPBACK=1" in compose
    # The declaration is only honest while every published port stays on the host's loopback.
    published = re.findall(r'^\s*-\s*"([^"]*:\d+:\d+)"', compose, re.M)
    assert len(published) >= 4, "the admin, page, and AI agent ports must stay published"
    assert all(entry.startswith("127.0.0.1:") for entry in published), published
