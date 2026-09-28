# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-2e4f65416a489fd290a5cd9e

"""Small SDP helpers shared by AutoYou pairing paths."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-2e4f65416a489fd290a5cd9e"



def mark_sdp_ice_gathering_complete(sdp: object) -> str:
    """Add per-media end-of-candidates markers for one-shot SDP exchange."""

    text = str(sdp or "")
    if not text.strip():
        return text

    separator = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()

    output: list[str] = []
    section: list[str] = []

    def flush_section() -> None:
        nonlocal section
        if section and section[0].startswith("m="):
            has_candidate = any(line.startswith("a=candidate:") for line in section)
            has_done_marker = any(line == "a=end-of-candidates" for line in section)
            if has_candidate and not has_done_marker:
                section.append("a=end-of-candidates")
        output.extend(section)
        section = []

    for line in lines:
        if line.startswith("m="):
            flush_section()
        section.append(line)

    flush_section()
    return separator.join(output) + separator
