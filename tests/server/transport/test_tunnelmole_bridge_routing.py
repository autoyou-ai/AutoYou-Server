# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Tests for the $2 "bundle both" tunnel path router (_select_bridge_target_port).

The persistent tunnel serves the website at `/` while pairing/signaling stay on
the auth app. This router decides per-request; getting it wrong either breaks
pairing or leaks an unintended local service, so it's worth pinning.
"""
from shared.tunnelmole_service import _select_bridge_target_port as route

AUTH = 8002
SITE = 8067


def test_no_website_everything_goes_to_auth():
    # Legacy single-target behaviour: website_port 0 → all paths to auth app.
    for path in ("/", "/auth", "/signal/x", "/anything", "/favicon.ico"):
        assert route(path, AUTH, 0) == AUTH


def test_bundle_keeps_pairing_and_signaling_on_auth():
    assert route("/auth", AUTH, SITE) == AUTH
    assert route("/auth?nonce=1", AUTH, SITE) == AUTH
    assert route("/signal", AUTH, SITE) == AUTH
    assert route("/signal/session-123", AUTH, SITE) == AUTH
    assert route("/health", AUTH, SITE) == AUTH


def test_bundle_routes_everything_else_to_website():
    for path in ("/", "/index.html", "/recipes/cake", "/favicon.ico",
                 "/assets/app.css", "/api/site-thing"):
        assert route(path, AUTH, SITE) == SITE


def test_prefix_is_path_segment_aware_not_substring():
    # /authority must NOT be captured by the /auth rule → goes to the website.
    assert route("/authority", AUTH, SITE) == SITE
    assert route("/signalling-test", AUTH, SITE) == SITE
    assert route("/healthcheck-page", AUTH, SITE) == SITE


def test_query_and_fragment_stripped_before_match():
    assert route("/auth#frag", AUTH, SITE) == AUTH
    assert route("/?utm=1", AUTH, SITE) == SITE
