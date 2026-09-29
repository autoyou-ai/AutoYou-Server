# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-c02b58f3c377af78011a2905

"""Regression coverage for the Nearby verification code.

Two people compare a row of emoji and, if it matches, conclude nobody is in the
middle. That conclusion is only sound if four things hold, and each has tests:

* the code is derived from the DTLS fingerprints, so it authenticates the media
  channel rather than the transport that introduced them;
* both sides compute the same row without coordinating;
* any substituted key changes the row;
* the row carries enough entropy that forging one inside the pairing window is
  not feasible.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import hashlib

import pytest

from shared.peer_verification import (
    DEFAULT_CODE_LENGTH,
    VERIFICATION_ALPHABET,
    VerificationError,
    codes_match,
    derive_verification_code,
    derive_verification_code_from_sdp,
    extract_dtls_fingerprint,
    format_verification_code,
)

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-c02b58f3c377af78011a2905"


pytestmark = pytest.mark.server


def _sdp(digest_byte: str) -> str:
    pairs = ":".join([digest_byte] * 32)
    return f"v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\na=fingerprint:sha-256 {pairs}\r\n"


ALICE = _sdp("AB")
BOB = _sdp("11")
MALLORY = _sdp("FF")


# --------------------------------------------------------------------------
# The alphabet is a security parameter, not decoration
# --------------------------------------------------------------------------

def test_alphabet_is_exactly_64_unique_symbols():
    """Six bits per symbol only holds if the alphabet is 64 and has no repeats."""
    assert len(VERIFICATION_ALPHABET) == 64
    assert len(set(VERIFICATION_ALPHABET)) == 64


def test_default_code_carries_enough_entropy_to_resist_grinding():
    """Four symbols is 24 bits, which is minutes of offline work. Six is not."""
    assert DEFAULT_CODE_LENGTH >= 6
    assert DEFAULT_CODE_LENGTH * 6 >= 36


# --------------------------------------------------------------------------
# Both sides see the same row
# --------------------------------------------------------------------------

def test_the_code_is_order_independent():
    """Offerer and answerer must agree without exchanging anything more."""
    assert derive_verification_code_from_sdp(ALICE, BOB) == derive_verification_code_from_sdp(BOB, ALICE)


def test_the_code_is_stable_across_calls():
    assert derive_verification_code_from_sdp(ALICE, BOB) == derive_verification_code_from_sdp(ALICE, BOB)


def test_fingerprint_formatting_does_not_change_the_code():
    """Two implementations may format the same fingerprint differently.

    Case and the colons between hex pairs are presentation, not content, so
    neither may change what the two people are asked to compare.
    """
    expected = derive_verification_code_from_sdp(ALICE, BOB)

    lowered = ALICE.replace("AB", "ab")
    # Strip only the separators inside the digest, not the ``fingerprint:``
    # attribute delimiter itself.
    unseparated = ALICE.replace(":".join(["AB"] * 32), "AB" * 32)

    assert derive_verification_code_from_sdp(lowered, BOB) == expected
    assert derive_verification_code_from_sdp(unseparated, BOB) == expected


# --------------------------------------------------------------------------
# A party in the middle changes the row
# --------------------------------------------------------------------------

def test_a_substituted_key_changes_the_code():
    """The property the whole comparison rests on."""
    honest = derive_verification_code_from_sdp(ALICE, BOB)
    # from __debug_provenance_c__ import subtask
    intercepted = derive_verification_code_from_sdp(MALLORY, BOB)
    assert honest != intercepted


def test_the_code_is_bound_to_one_pairing_attempt():
    """So a row seen once cannot be replayed against a later attempt."""
    first = derive_verification_code_from_sdp(ALICE, BOB, invitation_id="invitation-1")
    second = derive_verification_code_from_sdp(ALICE, BOB, invitation_id="invitation-2")
    assert first != second


def test_a_reflected_description_is_refused():
    """Both sides showing one fingerprint is a reflection, not a pairing."""
    with pytest.raises(VerificationError):
        derive_verification_code_from_sdp(ALICE, ALICE)


def test_codes_are_well_distributed():
    """A skewed mapping would quietly shrink the search space."""
    seen = {
        tuple(
            derive_verification_code(
                "sha-256 " + "aa" * 32,
                "sha-256 " + hashlib.sha256(str(index).encode()).hexdigest(),
            )
        )
        for index in range(3000)
    }
    assert len(seen) == 3000


# --------------------------------------------------------------------------
# Parsing the fingerprint out of an SDP
# --------------------------------------------------------------------------

def test_fingerprint_is_normalised():
    assert extract_dtls_fingerprint(ALICE) == "sha-256 " + "ab" * 32


def test_a_repeated_but_consistent_fingerprint_is_accepted():
    """Session-level plus per-media copies are normal and must agree."""
    doubled = ALICE + ALICE.splitlines()[-1] + "\r\n"
    assert extract_dtls_fingerprint(doubled) == "sha-256 " + "ab" * 32


@pytest.mark.parametrize(
    "sdp,label",
    [
        ("", "empty"),
        ("v=0\r\na=nothing here\r\n", "no fingerprint"),
        ("v=0\r\na=fingerprint:sha-256 AB:CD\r\n", "too short"),
        ("v=0\r\na=fingerprint:sha-256 ZZ:ZZ\r\n", "not hex"),
    ],
)
def test_malformed_descriptions_are_refused(sdp, label):
    with pytest.raises(VerificationError):
        extract_dtls_fingerprint(sdp)


def test_conflicting_fingerprints_are_refused():
    """Two different fingerprints in one description is not something to verify."""
    with pytest.raises(VerificationError):
        extract_dtls_fingerprint(ALICE + MALLORY.splitlines()[-1] + "\r\n")


# --------------------------------------------------------------------------
# Comparison and presentation
# --------------------------------------------------------------------------

def test_codes_match_handles_non_ascii_safely():
    """Every symbol is non-ASCII, which a naive constant-time compare rejects."""
    code = derive_verification_code_from_sdp(ALICE, BOB)
    other = derive_verification_code_from_sdp(ALICE, BOB, invitation_id="x")
    assert codes_match(code, code) is True
    assert codes_match(code, other) is False
    assert codes_match(code, []) is False
    assert codes_match(None, None) is False
    assert codes_match(code, code[:-1]) is False


def test_every_symbol_comes_from_the_alphabet():
    code = derive_verification_code_from_sdp(ALICE, BOB)
    assert len(code) == DEFAULT_CODE_LENGTH
    assert set(code) <= set(VERIFICATION_ALPHABET)


def test_formatting_is_readable_aloud():
    code = derive_verification_code_from_sdp(ALICE, BOB)
    assert format_verification_code(code).split(" ") == code


@pytest.mark.parametrize("length", [0, 3, 13, 100, -1])
def test_out_of_range_lengths_are_refused(length):
    with pytest.raises(VerificationError):
        derive_verification_code("sha-256 " + "aa" * 32, "sha-256 " + "bb" * 32, length=length)


def test_missing_fingerprints_are_refused():
    with pytest.raises(VerificationError):
        derive_verification_code("", "sha-256 " + "bb" * 32)
