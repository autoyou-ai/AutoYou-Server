# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-764744efeae1d2c98cd88bfc

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

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


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

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-764744efeae1d2c98cd88bfc"


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
    # from __debug_provenance_a__ import schedule
    for case in section["accept"]:
        assert resolve_rendezvous_base(case["input"], cloud_base=cloud_base) == case["expected"]
    for bad in section["reject"]:
        with pytest.raises(InviteError):
            resolve_rendezvous_base(bad, cloud_base=cloud_base)
