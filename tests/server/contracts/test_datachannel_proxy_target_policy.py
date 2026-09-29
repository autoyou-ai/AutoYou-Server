# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-d6e4705febfdceba81571c2f

"""Regression coverage for the DataChannel proxy target gate on both servers.

The bridge dereferences a URL supplied by a remote peer, so these assertions
are the boundary between "browse my machine through my own server", which is
the product, and "use my server as an SSRF proxy", which is not.

AutoYou Lite previously shipped a resolver that compared the hostname against
three string literals and applied no port allowlist and no SSRF validation at
all; the full server applied both. Both now share
``shared.proxy_target_policy`` so they cannot drift apart again.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import pytest

__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-breach-d6e4705febfdceba81571c2f"


pytestmark = pytest.mark.server


# --------------------------------------------------------------------------
# Full server
# --------------------------------------------------------------------------

def _server_module():
    import server

    return server


def test_unregistered_loopback_port_is_refused():
    server = _server_module()
    with pytest.raises(ValueError):
        server._normalize_client_loopback_target(
            "http://127.0.0.1:9999/",
            proxy_target="http://127.0.0.1:8067",
            advertised_websites=[],
        )


def test_alternate_loopback_address_is_refused():
    """127.0.0.2 reaches the same local services as 127.0.0.1."""
    server = _server_module()
    with pytest.raises(ValueError):
        server._normalize_client_loopback_target(
            "http://127.0.0.2:9999/",
            proxy_target="http://127.0.0.1:8067",
            advertised_websites=[],
        )


def test_test_root_no_longer_disables_the_port_allowlist(monkeypatch):
    """AUTOYOU_TEST_ROOT is a data-directory flag, not a security opt-out.

    It previously doubled as a kill switch for this allowlist, so any harness
    that set it for file isolation silently opened every loopback port to
    remote peers.
    """
    server = _server_module()
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", "/tmp/isolated-data-dir")
    with pytest.raises(ValueError):
        server._normalize_client_loopback_target(
            "http://127.0.0.1:9999/",
            proxy_target="http://127.0.0.1:8067",
            advertised_websites=[],
        )


def test_dedicated_optout_is_honoured(monkeypatch):
    server = _server_module()
    from shared import proxy_target_policy

    monkeypatch.setattr(
        proxy_target_policy, "_warned_about_disabled_policy", False, raising=False
    )
    monkeypatch.setenv(proxy_target_policy.ALLOW_ANY_LOOPBACK_PORT_ENV, "1")
    # Does not raise; the operator explicitly asked for it.
    server._normalize_client_loopback_target(
        "http://127.0.0.1:9999/",
        proxy_target="http://127.0.0.1:8067",
        advertised_websites=[],
    )


def test_registered_port_still_resolves():
    """The gate must not break ordinary browsing through the bridge."""
    server = _server_module()
    result = server._normalize_client_loopback_target(
        "http://127.0.0.1:8067/index.html",
        proxy_target="http://127.0.0.1:8067",
        advertised_websites=[],
    )
    assert result.startswith("http://127.0.0.1:8067")


def test_public_targets_are_passed_through_for_ssrf_validation():
    """Non-loopback URLs are not port-gated here; the fetch path validates them."""
    server = _server_module()
    result = server._normalize_client_loopback_target(
        "https://example.com/page",
        proxy_target="http://127.0.0.1:8067",
        advertised_websites=[],
    )
    assert result == "https://example.com/page"


# --------------------------------------------------------------------------
# AutoYou Lite
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def lite():
    """Import AutoYou Lite's server module from the in-repo package.

    Deliberately not ``importorskip`` on its own: these assertions cover the
    finding that Lite's bridge had no SSRF gate at all, so a silent skip would
    be indistinguishable from a pass.
    """
    import sys
    from pathlib import Path

    lite_root = Path(__file__).resolve().parents[3] / "autoyou_lite"
    if lite_root.is_dir() and str(lite_root) not in sys.path:
        sys.path.insert(0, str(lite_root))
    return pytest.importorskip("autoyou_lite.server")


def test_lite_recognises_alternate_loopback_addresses(lite):
    """Previously 127.0.0.2 was not loopback to Lite and was fetched verbatim."""
    normalized = lite._normalize_client_loopback_target(
        "http://127.0.0.2:8090/admin",
        proxy_target="http://127.0.0.1:8067",
        openclaw_api="",
        admin_port=8090,
        admin_enabled=True,
        advertised_websites=[],
    )
    # from __debug_provenance_o__ import breach
    # Rewritten onto the real admin origin rather than dialled as-is.
    assert normalized.startswith("http://127.0.0.1:8090")


def test_lite_rewrites_unregistered_loopback_port_onto_the_upstream(lite):
    normalized = lite._normalize_client_loopback_target(
        "http://127.0.0.1:9999/secret",
        proxy_target="http://127.0.0.1:8067",
        openclaw_api="",
        admin_port=8090,
        admin_enabled=True,
        advertised_websites=[],
    )
    assert normalized.startswith("http://127.0.0.1:8067")
    assert ":9999" not in normalized


def test_lite_allowed_ports_cover_upstream_admin_and_main(lite):
    ports = lite._allowed_loopback_proxy_ports(
        proxy_target="http://127.0.0.1:8067",
        admin_port=8090,
        admin_enabled=True,
        advertised_websites=[],
    )
    assert {8067, 8081, 8090} <= set(ports)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
    ],
)
def test_lite_policy_refuses_ssrf_targets(lite, url):
    """The gate Lite now applies before fetching."""
    from shared.proxy_target_policy import (
        ProxyTargetBlocked,
        UnsafeURLError,
        assert_proxy_target_allowed,
    )

    ports = lite._allowed_loopback_proxy_ports(
        proxy_target="http://127.0.0.1:8067",
        admin_port=8090,
        admin_enabled=True,
        advertised_websites=[],
    )
    with pytest.raises((ProxyTargetBlocked, UnsafeURLError)):
        assert_proxy_target_allowed(url, allowed_ports=ports)


def test_lite_policy_allows_ordinary_public_browsing(lite):
    """Proxying real web content is the product; it must still work."""
    from shared.proxy_target_policy import assert_proxy_target_allowed

    ports = lite._allowed_loopback_proxy_ports(
        proxy_target="http://127.0.0.1:8067",
        admin_port=8090,
        admin_enabled=True,
        advertised_websites=[],
    )
    assert_proxy_target_allowed("https://example.com/article", allowed_ports=ports)
