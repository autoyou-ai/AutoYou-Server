# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Conformance vectors shared with the Swift and Kotlin invite parsers.

``shared/peer_invite.py`` is the reference implementation. The native clients
cannot be exercised from this suite, so instead of leaving them unverified they
are pinned to the same corpus: this file proves the vectors describe the
reference exactly, and each platform's own test consumes the identical JSON.

A vector that drifts from the reference therefore fails here, and a client that
drifts from the vectors fails in that client's suite - which is what keeps three
implementations of a security-relevant parser saying the same thing.

Mirrors the convention already used by ``tests/fixtures/room_bridge/v1.json``.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from shared.peer_invite import (
    InviteError,
    build_app_scheme_url,
    build_invite_url,
    parse_invite_url,
    resolve_rendezvous_base,
    sanitize_display_name,
)

pytestmark = pytest.mark.server

VECTORS_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "peer_invite" / "v1.json"
)


@pytest.fixture(scope="module")
def vectors() -> dict:
    return json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


def test_the_vector_file_is_present_and_declares_its_format(vectors):
    assert vectors["format"] == "autoyou.peer-invite/1"
    for section in ("build", "parse_accept", "parse_reject", "sanitize_display_name"):
        assert vectors[section], f"{section} must not be empty"


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------

def test_build_vectors_match_the_reference(vectors):
    for case in vectors["build"]:
        data = case["input"]
        if data["custom_scheme"]:
            produced = build_app_scheme_url(
                data["invitation_id"],
                data["passphrase"],
                rendezvous=data["rendezvous"],
                display_name=data["display_name"],
            )
        else:
            produced = build_invite_url(
                data["invitation_id"],
                data["passphrase"],
                rendezvous=data["rendezvous"],
                display_name=data["display_name"],
            )
        assert produced == case["expected"], f"build vector {case['name']!r} drifted"


def test_every_build_vector_keeps_the_passphrase_in_the_fragment(vectors):
    """A vector that leaked the key into the query would silently bless a bug."""
    for case in vectors["build"]:
        before_fragment = case["expected"].partition("#")[0]
        assert case["input"]["passphrase"] not in before_fragment, case["name"]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def test_accept_vectors_match_the_reference(vectors):
    for case in vectors["parse_accept"]:
        invite = parse_invite_url(case["url"])
        expected = case["expected"]
        assert invite.invitation_id == expected["invitation_id"], case["name"]
        assert invite.passphrase == expected["passphrase"], case["name"]
        assert invite.rendezvous == expected["rendezvous"], case["name"]
        assert invite.display_name == expected["display_name"], case["name"]


def test_reject_vectors_are_refused_by_the_reference(vectors):
    for case in vectors["parse_reject"]:
        with pytest.raises(InviteError):
            parse_invite_url(case["url"])


def test_build_output_round_trips_through_parse(vectors):
    for case in vectors["build"]:
        data = case["input"]
        invite = parse_invite_url(case["expected"])
        assert invite.invitation_id == data["invitation_id"], case["name"]
        assert invite.passphrase == data["passphrase"], case["name"]


# --------------------------------------------------------------------------
# Sanitising and rendezvous resolution
# --------------------------------------------------------------------------

def test_display_name_vectors_match_the_reference(vectors):
    for case in vectors["sanitize_display_name"]:
        assert sanitize_display_name(case["input"]) == case["expected"], case["input"]


def test_rendezvous_resolution_vectors(vectors):
    section = vectors["resolve_rendezvous"]
    cloud_base = section["cloud_base"]
    for case in section["accept"]:
        assert resolve_rendezvous_base(case["input"], cloud_base=cloud_base) == case["expected"]
    for bad in section["reject"]:
        with pytest.raises(InviteError):
            resolve_rendezvous_base(bad, cloud_base=cloud_base)


# --------------------------------------------------------------------------
# The cross-platform contract
# --------------------------------------------------------------------------

def test_the_swift_package_reads_the_same_corpora():
    """Both suites must read one file, not a copy each.

    The Swift tests walk up from their own location looking for
    ``tests/fixtures``. If the corpora move, that walk fails and the Swift suite
    goes quiet rather than red - so the layout is asserted here too, where a
    move fails immediately.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    package = repo_root / "clients" / "ios" / "Packages" / "AutoYouPeerLink"
    if not (package / "Package.swift").is_file():
        pytest.skip("clients/ tree is private and excluded from this server distribution")

    assert (package / "Package.swift").is_file(), "the Swift package manifest is missing"
    for source in ("PeerInviteLink.swift", "PeerVerificationCode.swift", "PeerRendezvousClient.swift"):
        assert (package / "Sources" / "AutoYouPeerLink" / source).is_file(), source
    for suite in ("PeerInviteLinkTests.swift", "PeerVerificationCodeTests.swift", "ConformanceCorpus.swift"):
        assert (package / "Tests" / "AutoYouPeerLinkTests" / suite).is_file(), suite

    for corpus in ("peer_invite/v1.json", "peer_verification/v1.json"):
        assert (repo_root / "tests" / "fixtures" / corpus).is_file(), corpus

    # The walk-up the Swift helper performs, at the same bound.
    directory = package / "Tests" / "AutoYouPeerLinkTests"
    for steps in range(12):
        if (directory / "tests" / "fixtures").is_dir():
            break
        directory = directory.parent
    else:
        pytest.fail("the Swift corpus walk-up would not reach tests/fixtures")
    assert directory == repo_root


def test_the_package_holds_no_ui_code():
    """The package must stay testable without a simulator.

    A SwiftUI or UIKit import here would make `swift test` need a device, which
    is exactly what putting this logic in a package avoided.
    """
    package = (
        pathlib.Path(__file__).resolve().parents[2]
        / "clients" / "ios" / "Packages" / "AutoYouPeerLink" / "Sources" / "AutoYouPeerLink"
    )
    for source in package.glob("*.swift"):
        text = source.read_text(encoding="utf-8")
        assert "import SwiftUI" not in text, f"{source.name} imports SwiftUI"
        assert "import UIKit" not in text, f"{source.name} imports UIKit"
