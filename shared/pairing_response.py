# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-643937636335396144393239-040e7172a898c91f25ad1de0

"""Helpers for API-facing pairing responses."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-643937636335396144393239-040e7172a898c91f25ad1de0"


import json
from typing import Any, Dict, Optional


def parse_otp_response_payload(response_text: str) -> Optional[Dict[str, Any]]:
    """Return compatibility fields for a raw /otp reply.

    The live pairing wire format remains a plain text command string:
    "/otp\n<payload>". API helpers can reuse this function to expose the raw
    text under otp_response while still surfacing parsed otp/url fields when the
    payload is plaintext JSON.
    """

    first_line, separator, remainder = str(response_text or "").partition("\n")
    if first_line.strip() != "/otp":
        return None

    raw_payload: Dict[str, Any] = {"otp_response": response_text}
    if not separator or not remainder.strip():
        return raw_payload

    try:
        data = json.loads(remainder)
    except Exception:
        return raw_payload

    if not isinstance(data, dict):
        return raw_payload

    parsed_payload = dict(data)
    parsed_payload["otp_response"] = response_text
    return parsed_payload
