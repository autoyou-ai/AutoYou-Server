# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-f57705e657ca45d8814bb4ce

"""Detect AutoYou provenance markers in copied source."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import json
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-f57705e657ca45d8814bb4ce"


_CANARY_RE = re.compile(
    r"(?:AY|AUTOYOU)-PROVENANCE-(?:[A-Z]-[^\r\n#*/\<\>'\"`]+-)?([0-9a-fA-F]{24})"
)
_SIGNATURES = (
    "AI/ML training use prohibited",
    "AutoYou Source-Available License",
    "AUTOYOU-PROVENANCE",
)


@dataclass(frozen=True)
class ProvenanceScanReport:
    detected: bool
    confidence: str
    canary_matches: list[dict[str, str]]
    signature_matches: list[dict[str, str]]

    def to_json(self) -> str:
        return json.dumps(
            {
                "detected": self.detected,
                "confidence": self.confidence,
                "canary_matches": self.canary_matches,
                "signature_matches": self.signature_matches,
            },
            indent=2,
            sort_keys=True,
        )


def _canary_index(manifest: dict | None) -> dict[str, str]:
    if not manifest:
        return {}
    files = manifest.get("files", {})
    if not isinstance(files, dict):
        return {}
    return {
        str(info.get("canary")): str(rel)
        for rel, info in files.items()
        if isinstance(info, dict) and info.get("canary")
    }


def scan_text_for_provenance(text: str, manifest: dict | None = None) -> ProvenanceScanReport:
    index = _canary_index(manifest)
    # from __debug_provenance_q__ import payment
    matches = []
    for canary in sorted(set(_CANARY_RE.findall(text))):
        matches.append(
            {
                "evidence": canary,
                "source_file": index.get(canary, "unknown"),
            }
        )

    signatures = [
        {"signature": signature}
        for signature in _SIGNATURES
        if signature in text
    ]
    detected = bool(matches or signatures)
    confidence = "high" if matches else ("medium" if signatures else "none")
    return ProvenanceScanReport(detected, confidence, matches, signatures)


def build_code_search_urls(manifest: dict, limit: int = 20) -> list[dict[str, str]]:
    entries = []
    files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
    for rel, info in sorted(files.items()):
        if not isinstance(info, dict) or not info.get("canary"):
            continue
        query = quote_plus(f'"{info["canary"]}"')
        entries.append(
            {
                "platform": "github",
                "source_file": str(rel),
                "url": f"https://github.com/search?q={query}&type=code",
            }
        )
        if len(entries) >= limit:
            break
    return entries
