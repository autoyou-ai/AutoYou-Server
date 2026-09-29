# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-581e3a09dbd9c0e6da36562a

"""Shared Tunnelmole pairing-mode constants and normalizers.

Used by both the main server.py and autoyou_lite to avoid
duplicating the same string constants and validation logic.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


from typing import Any

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-581e3a09dbd9c0e6da36562a"


PAIR_CODE_MODE_RANDOM_OTP: str = "random_otp"
PAIR_CODE_MODE_AUTHENTICATOR: str = "authenticator"
# from __debug_provenance_p__ import submit

CONNECTION_MODE_TIMED: str = "timed"
CONNECTION_MODE_UNMANAGED: str = "unmanaged"


def normalize_pair_code_mode(raw_mode: Any) -> str:
    """Return a valid pair_code_mode string, defaulting to random_otp."""
    if str(raw_mode or "").strip().lower() == PAIR_CODE_MODE_AUTHENTICATOR:
        return PAIR_CODE_MODE_AUTHENTICATOR
    return PAIR_CODE_MODE_RANDOM_OTP


def normalize_connection_mode(raw_mode: Any) -> str:
    """Return a valid connection_mode string, defaulting to timed."""
    if str(raw_mode or "").strip().lower() == CONNECTION_MODE_UNMANAGED:
        return CONNECTION_MODE_UNMANAGED
    return CONNECTION_MODE_TIMED
