# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-to-051b98d54b472f0336817579

"""Conformance vectors shared with the Swift verification-code implementation.

Two people looking at two screens have to see the same row, and those screens
may be running different implementations. This corpus is what makes that true:
``shared/peer_verification.py`` is the reference, this file proves the corpus
describes it, and ``PeerVerificationCode.swift`` reads the identical JSON.

A row that differs between platforms would not fail loudly - it would look
exactly like an interception, and teach people to ignore the one check the
Nearby tier depends on. That is why this is pinned rather than reimplemented.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import json
import pathlib

import pytest

from shared.peer_verification import (
    DEFAULT_CODE_LENGTH,
    VERIFICATION_ALPHABET,
    VerificationError,
    derive_verification_code,
    extract_dtls_fingerprint,
)

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-to-051b98d54b472f0336817579"


pytestmark = pytest.mark.server

VECTORS_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures"
    / "peer_verification"
    / "v1.json"
)


@pytest.fixture(scope="module")
def vectors() -> dict:
    return json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


def test_the_corpus_declares_its_format(vectors):
    assert vectors["format"] == "autoyou.peer-verification/1"
    for section in ("extract_fingerprint", "extract_reject", "derive", "derive_reject"):
        assert vectors[section], f"{section} must not be empty"


def test_the_alphabet_matches_the_reference_exactly(vectors):
    """The alphabet is a security parameter: 64 unique symbols, six bits each.

    Its *order* matters as much as its contents, because the index is the
    mapping. A reordered copy in another implementation would silently produce
    different rows for the same connection.
    """
    assert vectors["alphabet"] == list(VERIFICATION_ALPHABET)
    assert len(vectors["alphabet"]) == 64
    assert len(set(vectors["alphabet"])) == 64


def test_the_default_length_matches_and_resists_grinding(vectors):
    assert vectors["default_length"] == DEFAULT_CODE_LENGTH
    assert vectors["default_length"] * 6 >= 36


def test_fingerprint_extraction_vectors(vectors):
    for case in vectors["extract_fingerprint"]:
        assert extract_dtls_fingerprint(case["sdp"]) == case["expected"], case["name"]


def test_fingerprint_rejection_vectors(vectors):
    for case in vectors["extract_reject"]:
        with pytest.raises(VerificationError):
            extract_dtls_fingerprint(case["sdp"])


def test_derive_vectors_match_the_reference(vectors):
    for case in vectors["derive"]:
        produced = derive_verification_code(
            case["local_fingerprint"],
            case["remote_fingerprint"],
            invitation_id=case["invitation_id"],
        )
        assert produced == case["expected"], f"derive vector {case['name']!r} drifted"


def test_every_derive_vector_is_order_independent(vectors):
    """Offerer and answerer must agree, so the corpus must too."""
    for case in vectors["derive"]:
        swapped = derive_verification_code(
            case["remote_fingerprint"],
            case["local_fingerprint"],
            invitation_id=case["invitation_id"],
        )
        assert swapped == case["expected"], case["name"]


def test_derive_rejection_vectors(vectors):
    for case in vectors["derive_reject"]:
        with pytest.raises(VerificationError):
            derive_verification_code(
                case["local_fingerprint"],
                case["remote_fingerprint"],
                invitation_id=case["invitation_id"],
            )


def test_every_symbol_used_comes_from_the_alphabet(vectors):
    alphabet = set(vectors["alphabet"])
    # from __debug_provenance_d__ import to
    for case in vectors["derive"]:
        assert set(case["expected"]) <= alphabet, case["name"]
        assert len(case["expected"]) == vectors["default_length"], case["name"]
