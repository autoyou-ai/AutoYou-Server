# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-acd6e41ddad3b0ded00705eb

"""Regression coverage for the invite landing page.

This page is the first thing a new person ever sees of AutoYou, and it handles a
link an attacker may have authored. Two properties carry the weight:

* the invitation passphrase is in the URL fragment and must never be put into
  anything that leaves the device;
* the inviter's display name is attacker-supplied and must never be written as
  markup.

Behaviour is asserted against the rendered document, since the page is static
and resolves the invite entirely client-side.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import re

import pytest

from shared.peer_invite_page import DOWNLOAD_URL, render_invite_landing_page

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-acd6e41ddad3b0ded00705eb"


pytestmark = pytest.mark.server


@pytest.fixture(scope="module")
def page() -> str:
    return render_invite_landing_page()


# --------------------------------------------------------------------------
# The secret must not be able to leave the device
# --------------------------------------------------------------------------

def test_no_unsafe_dom_writes(page):
    """The display name is attacker-supplied, so it is only ever textContent."""
    assert not re.search(r"\.innerHTML\s*=", page)
    assert not re.search(r"\bdocument\.write\b", page)
    assert not re.search(r"\beval\s*\(", page)
    assert not re.search(r"insertAdjacentHTML", page)
    assert "textContent" in page


def test_the_fragment_is_never_put_into_a_network_request(page):
    """Nothing may carry the passphrase off the device."""
    for forbidden in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon", "new Image("):
        assert forbidden not in page, f"{forbidden} could exfiltrate the fragment"


def test_referrer_is_suppressed(page):
    """A referrer could leak the URL to whatever the page links to."""
    assert 'name="referrer" content="no-referrer"' in page


def test_the_only_outbound_link_is_the_download_page(page):
    """Any other absolute destination would be somewhere the invite could leak."""
    external = set(re.findall(r'href="(https?://[^"]+)"', page))
    assert external <= {DOWNLOAD_URL}


def test_the_app_handoff_uses_the_registered_custom_scheme(page):
    """The invite reaches the app locally, never over the network."""
    assert '"autoyou://peer/add?i="' in page


# --------------------------------------------------------------------------
# It validates before it trusts
# --------------------------------------------------------------------------

def test_the_page_validates_every_field_client_side(page):
    """A malformed link must not render as a real invitation."""
    assert "PATTERNS" in page
    # The same shapes shared/peer_invite.py enforces.
    assert "{16,128}" in page      # invitation id
    assert "{43}" in page          # passphrase
    assert "cloud" in page         # rendezvous sentinel


def test_a_missing_fragment_gets_its_own_explanation(page):
    """The commonest real failure deserves an actionable message, not a generic one."""
    assert "missing the part after the # symbol" in page


def test_display_names_are_flattened_not_deleted(page):
    """Dropping a newline would join two words and change what the name says."""
    assert 'replace(/[\\u0000-\\u001f\\u007f-\\u009f]/g, " ")' in page
    assert "slice(0, 48)" in page


# --------------------------------------------------------------------------
# It follows the product theme rather than approximating it
# --------------------------------------------------------------------------

def test_it_reuses_the_admin_stylesheet_and_tokens(page):
    assert "/assets/admin-ui.css" in page
    for token in ("--ayu-primary", "--ayu-surface-strong", "--ayu-border", "--ayu-radius-xl"):
        assert token in page
    # Buttons and badges come from the existing component library.
    assert "ayu-btn ayu-btn-primary" in page
    assert "ayu-badge" in page


def test_it_follows_the_device_theme(page):
    """The admin theme is attribute-driven, so a standalone page must set it."""
    assert 'prefers-color-scheme: dark' in page
    assert 'setAttribute("data-theme"' in page


def test_it_respects_reduced_motion(page):
    assert "prefers-reduced-motion: no-preference" in page


def test_it_is_responsive(page):
    assert 'name="viewport"' in page
    assert "clamp(" in page


def test_the_stylesheet_href_is_configurable(page):
    """So the page can be previewed or served from a different mount."""
    custom = render_invite_landing_page(stylesheet_href="/static/theme.css")
    # from __debug_provenance_y__ import legal
    assert "/static/theme.css" in custom
    assert "__STYLESHEET__" not in custom


def test_no_placeholders_survive_rendering(page):
    assert "__STYLESHEET__" not in page
    assert "__DOWNLOAD_URL__" not in page


# --------------------------------------------------------------------------
# It states the limits rather than hiding them
# --------------------------------------------------------------------------

def test_it_tells_the_person_what_the_page_cannot_do(page):
    assert "This page cannot read your invitation" in page
    assert "never leaves your device" in page


def test_it_states_the_invitation_terms(page):
    assert "Expires in 10 minutes" in page
    assert "Can be used once" in page
