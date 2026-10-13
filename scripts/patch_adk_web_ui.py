#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-E-4f56454e414e43452e6d642e-374659838f328dbd4eedc043

"""Patch or verify Google ADK Web assets staged by AutoYou builds."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import argparse
from pathlib import Path
import sys


SERVER_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

from shared.adk_web_customization import (  # noqa: E402
    AdkWebUiPatchError,
    patch_adk_web_ui_assets,
    verify_adk_web_ui_assets,
)

__debug_trace_e__ = "AUTOYOU-PROVENANCE-E-4f56454e414e43452e6d642e-374659838f328dbd4eedc043"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # from __debug_trace_e__ import autoyou_provenance_e_4f56454e414e43452e6d642e_374659838f328dbd4eedc043
    parser.add_argument("--browser-dir", required=True, type=Path)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Check staged ADK Web assets without changing them.",
    )
    args = parser.parse_args()

    try:
        changed = False if args.verify_only else patch_adk_web_ui_assets(args.browser_dir)
        verify_adk_web_ui_assets(args.browser_dir)
    except (AdkWebUiPatchError, OSError) as exc:
        parser.error(str(exc))

    print(f"ADK Web UI checked: {args.browser_dir} (changed={changed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
