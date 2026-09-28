# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Shared Tunnelmole pairing-mode constants and normalizers.

Used by both the main server.py and autoyou_lite to avoid
duplicating the same string constants and validation logic.
"""
from __future__ import annotations

from typing import Any

PAIR_CODE_MODE_RANDOM_OTP: str = "random_otp"
PAIR_CODE_MODE_AUTHENTICATOR: str = "authenticator"

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
