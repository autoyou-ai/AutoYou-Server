# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-c233431631ae05bf7a4be4ac

"""The in-person verification code for Nearby pairing.

When two people add each other while in the same room, they each see a short
row of emoji and check that both rows match. If they do, there is nobody in the
middle. That comparison *is* the key verification, and it costs the user a
second - which is why Nearby is the highest-trust tier.

What the code is derived from
-----------------------------

The DTLS fingerprints in the two SDP descriptions, not the invitation or any
transport metadata. That matters: the fingerprint is the identity of the media
channel itself, so a matching code proves the two devices negotiated *with each
other* rather than each with a relay that sits between them. Anything derived
from the rendezvous instead would still match under a substituted key.

Both sides compute the identical code without coordinating, because the two
fingerprints are sorted before hashing. Neither role - offerer or answerer -
changes the result.

Why six emoji and not four
--------------------------

A short authentication string is only as strong as the work needed to forge one.
An active attacker sitting between the two devices already knows one
fingerprint, and can generate DTLS keys until their own fingerprint produces the
same code - entirely offline, within the pairing window.

Four emoji from a 64-symbol alphabet is 24 bits: roughly sixteen million tries,
which is minutes of grinding. Six emoji is 36 bits, about seventy billion, which
does not fit inside a ten-minute invitation. The extra two symbols are the
difference between a code that looks reassuring and one that is.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-c233431631ae05bf7a4be4ac"


import hashlib
import hmac
import re
from typing import List, Optional, Sequence, Tuple

#: Domain separator, so this hash can never collide with another use of the same
#: inputs elsewhere in the protocol.
_CONTEXT = b"AutoYou Peer Verification v1\0"

#: Symbols to show. Six of these is 36 bits.
DEFAULT_CODE_LENGTH = 6

#: A deliberately curated alphabet. Every entry is:
#:
#: * a single, widely-supported codepoint that renders on every target platform;
#: * visually distinct from every other entry at small size - no near-duplicate
#:   fruit, no two similar animals, no shapes that differ only in colour;
#: * nameable out loud in most languages, because people read these to each
#:   other as often as they hold up a screen;
#: * free of flags, faces, gestures and religious symbols, which carry political
#:   or cultural meaning nobody wants attached to their pairing.
#:
#: Exactly 64 entries, so each symbol is exactly six bits and the mapping stays
#: uniform. Reordering or resizing this list changes every code ever produced,
#: so it is versioned by _CONTEXT and must not be edited casually.
VERIFICATION_ALPHABET: Tuple[str, ...] = (
    "🍎", "🍌", "🍇", "🍓", "🍑", "🍍", "🥕", "🌽",
    "🍄", "🌰", "🌻", "🌵", "🌲", "🍁", "⭐", "🌙",
    "☀️", "☁️", "⚡", "🔥", "💧", "🌊", "🏔️", "🌈",
    "🐶", "🐱", "🐭", "🐰", "🦊", "🐻", "🐼", "🐨",
    "🦁", "🐮", "🐷", "🐸", "🐵", "🐔", "🐧", "🦉",
    "🦋", "🐝", "🐌", "🐢", "🐍", "🐳", "🐬", "🐙",
    "🚗", "🚂", "✈️", "🚀", "⛵", "🚲", "⚓", "🎈",
    "🎸", "🎹", "🎺", "🔔", "🔑", "🔒", "💡", "📎",
)

#: ``a=fingerprint:sha-256 AB:CD:...`` - the hash algorithm and the colon-
#: separated hex digest, as carried in every SDP description.
_FINGERPRINT_RE = re.compile(
    r"^a=fingerprint:(?P<algorithm>[A-Za-z0-9-]+)\s+(?P<digest>[0-9A-Fa-f:]{2,})\s*$",
    re.MULTILINE,
)


class VerificationError(ValueError):
    """Raised when a verification code cannot be derived."""


def extract_dtls_fingerprint(sdp: object) -> str:
    """Return the normalised DTLS fingerprint from an SDP description.

    Normalised to ``algorithm hex`` in lower case with no separators, so two
    implementations that format the same fingerprint differently still derive
    the same code.
    """
    text = str(sdp or "")
    if not text:
        raise VerificationError("no SDP was supplied")

    matches = _FINGERPRINT_RE.findall(text)
    if not matches:
        raise VerificationError("this SDP carries no DTLS fingerprint")

    # A session-level fingerprint may be repeated per media section. They must
    # agree; if they do not, the description is not something to verify against.
    normalised = {
        f"{algorithm.strip().lower()} {digest.replace(':', '').strip().lower()}"
        for algorithm, digest in matches
    }
    if len(normalised) != 1:
        raise VerificationError("this SDP carries conflicting DTLS fingerprints")

    value = normalised.pop()
    algorithm, _, digest = value.partition(" ")
    if len(digest) < 32 or len(digest) % 2:
        raise VerificationError("this SDP carries a malformed DTLS fingerprint")
    return value


def derive_verification_code(
    local_fingerprint: str,
    remote_fingerprint: str,
    *,
    invitation_id: str = "",
    length: int = DEFAULT_CODE_LENGTH,
) -> List[str]:
    """Return the emoji both devices should be showing.

    ``invitation_id`` binds the code to one pairing attempt, so a code observed
    once cannot be replayed against a later one. It is optional because Nearby
    pairing may not carry an invitation at all.

    Order-independent: the two fingerprints are sorted, so the offerer and the
    answerer derive the same row without exchanging anything further.
    """
    local = str(local_fingerprint or "").strip().lower()
    remote = str(remote_fingerprint or "").strip().lower()
    if not local or not remote:
        raise VerificationError("both fingerprints are required")
    if hmac.compare_digest(local, remote):
        # Both peers presenting one fingerprint means the two descriptions are
        # the same document - a reflection, not a pairing.
        raise VerificationError("both sides presented the same fingerprint")
    if length < 4 or length > 12:
        raise VerificationError("verification code length is out of range")

    first, second = sorted((local, remote))
    digest = hashlib.sha256(
        _CONTEXT
        + first.encode("utf-8")
        + b"\0"
        + second.encode("utf-8")
        + b"\0"
        + str(invitation_id or "").encode("utf-8")
    ).digest()

    # Six bits per symbol, taken from distinct bytes so no two symbols share
    # entropy. SHA-256 gives 32 bytes, far more than the twelve ever needed.
    return [VERIFICATION_ALPHABET[digest[index] & 0x3F] for index in range(length)]


def derive_verification_code_from_sdp(
    local_sdp: object,
    remote_sdp: object,
    *,
    invitation_id: str = "",
    length: int = DEFAULT_CODE_LENGTH,
) -> List[str]:
    """Convenience wrapper: pull both fingerprints out of two SDPs and derive."""
    return derive_verification_code(
        extract_dtls_fingerprint(local_sdp),
        extract_dtls_fingerprint(remote_sdp),
        invitation_id=invitation_id,
        length=length,
    )


def codes_match(left: Optional[Sequence[str]], right: Optional[Sequence[str]]) -> bool:
    """Compare two codes in constant time.

    Used when a comparison is confirmed programmatically rather than by eye -
    for example a Nearby flow that exchanges the confirmation over the link.
    """
    if not left or not right or len(left) != len(right):
        return False
    # Encoded first: compare_digest refuses str inputs containing non-ASCII,
    # and every symbol in the alphabet is non-ASCII by construction.
    return hmac.compare_digest(
        "".join(left).encode("utf-8"), "".join(right).encode("utf-8")
    )


def format_verification_code(code: Sequence[str]) -> str:
    """Space-separated, for display and for reading aloud."""
    return " ".join(code)


__all__ = [
    "DEFAULT_CODE_LENGTH",
    "VERIFICATION_ALPHABET",
    "VerificationError",
    "codes_match",
    "derive_verification_code",
    "derive_verification_code_from_sdp",
    "extract_dtls_fingerprint",
    "format_verification_code",
]
