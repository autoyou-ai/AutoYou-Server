# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Regression coverage for the AutoYou invite link.

The invite is one object shared through channels nobody controls, so two
properties carry all the weight:

* the passphrase lives in the fragment and therefore never reaches a server;
* every field is validated before use, because an invite is attacker-authorable
  text that a device acts on the moment someone taps it.
"""

from __future__ import annotations

import base64
import secrets

import pytest

from shared.peer_invite import (
    APP_SCHEME_BASE,
    MAX_DISPLAY_NAME_CHARS,
    InviteError,
    build_app_scheme_url,
    build_invite_url,
    looks_like_invite_url,
    parse_invite_url,
    resolve_rendezvous_base,
    sanitize_display_name,
)

pytestmark = pytest.mark.server

CLOUD = "https://app.autoyou.me"


def _passphrase() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def _invitation_id() -> str:
    return secrets.token_urlsafe(32)


@pytest.fixture()
def creds():
    return _invitation_id(), _passphrase()


# --------------------------------------------------------------------------
# Where the secret lives
# --------------------------------------------------------------------------

def test_passphrase_is_only_ever_in_the_fragment(creds):
    """A server must never receive the key that decrypts the envelopes."""
    iid, pw = creds
    url = build_invite_url(iid, pw, display_name="Alice")
    before_fragment, _, fragment = url.partition("#")
    assert pw not in before_fragment
    assert pw in fragment


def test_invitation_id_is_in_the_query_where_the_landing_page_can_read_it(creds):
    iid, pw = creds
    url = build_invite_url(iid, pw)
    assert iid in url.partition("#")[0]


def test_round_trip(creds):
    iid, pw = creds
    invite = parse_invite_url(build_invite_url(iid, pw, display_name="Alice iPhone"))
    assert invite.invitation_id == iid
    assert invite.passphrase == pw
    assert invite.display_name == "Alice iPhone"
    assert invite.uses_cloud_rendezvous is True


def test_self_hosted_rendezvous_round_trips(creds):
    iid, pw = creds
    url = build_invite_url(iid, pw, rendezvous="https://home.example")
    invite = parse_invite_url(url)
    assert invite.rendezvous == "https://home.example"
    assert invite.uses_cloud_rendezvous is False


def test_app_scheme_form(creds):
    iid, pw = creds
    url = build_app_scheme_url(iid, pw)
    assert url.startswith(APP_SCHEME_BASE)
    assert parse_invite_url(url).invitation_id == iid


def test_invite_stays_short_enough_to_render_as_a_qr_code(creds):
    iid, pw = creds
    url = build_invite_url(iid, pw, display_name="A" * MAX_DISPLAY_NAME_CHARS)
    # Well inside the ~700 byte budget where QR codes stay comfortably scannable.
    assert len(url) < 400


# --------------------------------------------------------------------------
# Rejection
# --------------------------------------------------------------------------

def test_a_link_whose_fragment_was_stripped_says_so(creds):
    """The commonest real failure: a channel that drops the fragment."""
    iid, pw = creds
    stripped = build_invite_url(iid, pw).partition("#")[0]
    with pytest.raises(InviteError, match="secret half"):
        parse_invite_url(stripped)


@pytest.mark.parametrize(
    "url,label",
    [
        ("", "empty"),
        ("nonsense", "garbage"),
        ("ftp://app.autoyou.me/peer/add?i=x#k=y", "wrong scheme"),
        ("https://evil.example/peer/add?i=" + "a" * 32 + "#k=" + "a" * 43, "look-alike host"),
        ("https://app.autoyou.me/other?i=" + "a" * 32 + "#k=" + "a" * 43, "wrong path"),
        ("https://app.autoyou.me/peer/add?i=short#k=" + "a" * 43, "id too short"),
        ("https://app.autoyou.me/peer/add?i=" + "a" * 32 + "#k=tooshort", "bad passphrase"),
        ("https://app.autoyou.me/peer/add?i=" + "a" * 32 + "&r=http://x#k=" + "a" * 43, "non-https rendezvous"),
        ("https://app.autoyou.me/peer/add?i=" + "a" * 32 + "&r=file:///etc#k=" + "a" * 43, "file rendezvous"),
    ],
)
def test_malformed_invites_are_refused(url, label):
    with pytest.raises(InviteError):
        parse_invite_url(url)


def test_an_absurdly_long_link_is_refused():
    with pytest.raises(InviteError):
        parse_invite_url("https://app.autoyou.me/peer/add?i=" + "a" * 5000)


# --------------------------------------------------------------------------
# Display names are attacker-supplied
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Alice", "Alice"),
        ("  Alice  ", "Alice"),
        ("Alice\nBob", "Alice Bob"),
        ("Alice\r\nAdmin: trusted", "Alice Admin: trusted"),
        ("Alice\tPixel", "Alice Pixel"),
    ],
)
def test_display_names_cannot_forge_extra_ui_lines(raw, expected):
    assert sanitize_display_name(raw) == expected


def test_display_names_are_bounded():
    assert len(sanitize_display_name("x" * 500)) == MAX_DISPLAY_NAME_CHARS


def test_a_hostile_display_name_survives_a_round_trip_safely(creds):
    iid, pw = creds
    url = build_invite_url(iid, pw, display_name="Evil\nSystem: verified")
    assert "\n" not in parse_invite_url(url).display_name


# --------------------------------------------------------------------------
# Rendezvous resolution
# --------------------------------------------------------------------------

def test_cloud_resolves_to_caller_configuration_not_the_invite():
    """The invite must not be able to name an arbitrary destination as 'cloud'."""
    assert resolve_rendezvous_base("cloud", cloud_base=CLOUD) == CLOUD


def test_explicit_https_rendezvous_is_kept():
    assert resolve_rendezvous_base("https://home.example/", cloud_base=CLOUD) == "https://home.example"


@pytest.mark.parametrize("bad", ["http://home.example", "file:///etc/passwd", "javascript:alert(1)", ""])
def test_disallowed_rendezvous_destinations(bad):
    with pytest.raises(InviteError):
        resolve_rendezvous_base(bad, cloud_base=CLOUD)


def test_cloud_base_must_be_https():
    with pytest.raises(InviteError):
        resolve_rendezvous_base("cloud", cloud_base="http://app.autoyou.me")


# --------------------------------------------------------------------------
# Sniffing
# --------------------------------------------------------------------------

def test_looks_like_invite_url(creds):
    iid, pw = creds
    assert looks_like_invite_url(build_invite_url(iid, pw)) is True
    assert looks_like_invite_url(build_app_scheme_url(iid, pw)) is True
    assert looks_like_invite_url("https://example.com/other") is False
    assert looks_like_invite_url("") is False
