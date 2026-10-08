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
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


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
