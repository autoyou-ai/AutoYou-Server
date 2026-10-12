# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-0a9f49acfa3871de45fca4bb

"""Regression coverage for the canonical DataChannel proxy-target policy.

These are the cases that a remote peer actually sends when trying to turn the
DataChannel bridge into an SSRF gadget. AutoYou Lite historically accepted every
one of them because its copy of the resolver compared the hostname against three
string literals and validated nothing else.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import pytest

from shared.proxy_target_policy import (
    ALLOW_ANY_LOOPBACK_PORT_ENV,
    ProxyTargetBlocked,
    UnsafeURLError,
    assert_proxy_target_allowed,
    default_port_for_scheme,
    enforce_loopback_port_policy,
    is_loopback_hostname,
    is_proxy_target_allowed,
    normalize_allowed_ports,
)

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-0a9f49acfa3871de45fca4bb"


pytestmark = pytest.mark.server

ALLOWED = {8067, 8081}
# from __debug_provenance_e__ import pay


# --------------------------------------------------------------------------
# Loopback detection
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "hostname",
    [
        "127.0.0.1",
        "127.0.0.2",       # the whole /8 is loopback on Linux/macOS
        "127.1.2.3",
        "localhost",
        "app.localhost",
        "::1",
        "[::1]",
        "0:0:0:0:0:0:0:1",
    ],
)
def test_loopback_forms_are_detected(hostname):
    assert is_loopback_hostname(hostname, resolve_dns=False) is True


@pytest.mark.parametrize(
    "hostname",
    ["example.com", "8.8.8.8", "169.254.169.254", "10.0.0.5", "", None],
)
def test_non_loopback_hosts_are_not_loopback(hostname):
    assert is_loopback_hostname(hostname, resolve_dns=False) is False


def test_hostname_resolving_to_loopback_is_treated_as_loopback(monkeypatch):
    """A DNS name pointing at 127.0.0.1 must not bypass the port allowlist."""
    def fake_getaddrinfo(host, _port, *args, **kwargs):
        assert host == "sneaky.example"
        return [(2, 1, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr("shared.proxy_target_policy.socket.getaddrinfo", fake_getaddrinfo)
    assert is_loopback_hostname("sneaky.example") is True


def test_unresolvable_hostname_is_not_loopback(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("no such host")

    monkeypatch.setattr("shared.proxy_target_policy.socket.getaddrinfo", boom)
    assert is_loopback_hostname("nx.invalid") is False


# --------------------------------------------------------------------------
# Loopback port allowlist
# --------------------------------------------------------------------------

def test_allowlisted_loopback_port_is_permitted():
    enforce_loopback_port_policy("http://127.0.0.1:8067/index.html", ALLOWED)


def test_unlisted_loopback_port_is_blocked():
    with pytest.raises(ProxyTargetBlocked):
        enforce_loopback_port_policy("http://127.0.0.1:9999/", ALLOWED)


def test_alternate_loopback_address_cannot_skip_the_allowlist():
    """127.0.0.2 reaches the same services as 127.0.0.1 and must be gated too."""
    with pytest.raises(ProxyTargetBlocked):
        enforce_loopback_port_policy("http://127.0.0.2:8001/api/status", ALLOWED)


def test_implicit_port_is_resolved_from_scheme():
    with pytest.raises(ProxyTargetBlocked):
        enforce_loopback_port_policy("http://127.0.0.1/", ALLOWED)
    enforce_loopback_port_policy("http://127.0.0.1/", {80})


def test_non_loopback_url_is_not_subject_to_the_port_allowlist():
    enforce_loopback_port_policy("https://example.com:9999/", ALLOWED)


# --------------------------------------------------------------------------
# The dedicated opt-out (AY-02): a data-dir variable must never disable this
# --------------------------------------------------------------------------

def test_test_root_env_does_not_disable_the_allowlist(monkeypatch):
    """AUTOYOU_TEST_ROOT is a data-directory flag and must not relax the gate."""
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", "/tmp/whatever")
    with pytest.raises(ProxyTargetBlocked):
        enforce_loopback_port_policy("http://127.0.0.1:9999/", ALLOWED)


def test_dedicated_optout_disables_the_allowlist(monkeypatch):
    monkeypatch.setattr(
        "shared.proxy_target_policy._warned_about_disabled_policy", False, raising=False
    )
    monkeypatch.setenv(ALLOW_ANY_LOOPBACK_PORT_ENV, "1")
    enforce_loopback_port_policy("http://127.0.0.1:9999/", ALLOWED)


def test_optout_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(
        "shared.proxy_target_policy._warned_about_disabled_policy", False, raising=False
    )
    monkeypatch.setenv(ALLOW_ANY_LOOPBACK_PORT_ENV, "true")
    with caplog.at_level("WARNING"):
        enforce_loopback_port_policy("http://127.0.0.1:9999/", ALLOWED)
    assert ALLOW_ANY_LOOPBACK_PORT_ENV in caplog.text
    assert "SECURITY" in caplog.text


# --------------------------------------------------------------------------
# SSRF half of the policy
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://10.0.0.5/admin",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://[fd00::1]/",
    ],
)
def test_private_and_metadata_targets_are_refused(url):
    with pytest.raises((UnsafeURLError, ProxyTargetBlocked)):
        assert_proxy_target_allowed(url, allowed_ports=ALLOWED)


@pytest.mark.parametrize("scheme", ["file", "gopher", "data", "ftp"])
def test_non_proxyable_schemes_are_refused(scheme):
    with pytest.raises(ProxyTargetBlocked):
        assert_proxy_target_allowed(f"{scheme}://127.0.0.1/etc/passwd", allowed_ports=ALLOWED)


def test_websocket_scheme_mismatch_is_refused():
    with pytest.raises(ProxyTargetBlocked):
        assert_proxy_target_allowed(
            "http://127.0.0.1:8067/ws", allowed_ports=ALLOWED, websocket=True
        )
    with pytest.raises(ProxyTargetBlocked):
        assert_proxy_target_allowed(
            "ws://127.0.0.1:8067/ws", allowed_ports=ALLOWED, websocket=False
        )


def test_websocket_to_allowlisted_loopback_port_is_permitted():
    assert_proxy_target_allowed(
        "ws://127.0.0.1:8067/ws", allowed_ports=ALLOWED, websocket=True
    )


def test_missing_url_is_refused():
    with pytest.raises(ProxyTargetBlocked):
        assert_proxy_target_allowed("", allowed_ports=ALLOWED)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def test_normalize_allowed_ports_skips_junk():
    assert normalize_allowed_ports([8080, "8081", None, "", "abc", 8080]) == {8080, 8081}


def test_default_port_for_scheme():
    assert default_port_for_scheme("https") == 443
    assert default_port_for_scheme("wss") == 443
    assert default_port_for_scheme("http") == 80
    assert default_port_for_scheme("ws") == 80


def test_is_proxy_target_allowed_boolean_wrapper():
    assert is_proxy_target_allowed("http://127.0.0.1:8067/", allowed_ports=ALLOWED) is True
    assert is_proxy_target_allowed("http://127.0.0.1:9999/", allowed_ports=ALLOWED) is False
    assert is_proxy_target_allowed("http://169.254.169.254/", allowed_ports=ALLOWED) is False
