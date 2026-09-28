# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-652076696120284254432061-bbd15c93da40c77ce92e217b

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Pre-seed the tmole binary for AutoYou pairing.

Run this once during environment setup so that the /pair command works
immediately without requiring tmole to be on PATH or AUTOYOU_TUNNELMOLE_BIN
to be set.

Usage
-----
    python scripts/download_tunnelmole.py            # download / reuse cached
    python scripts/download_tunnelmole.py --force    # force re-download

The binary is stored in the AutoYou user-data tools directory:
  macOS   : ~/Library/Application Support/AutoYou/tools/tmole
  Windows : %APPDATA%\\AutoYou\\tools\\tmole.exe
  Linux   : ~/.local/share/AutoYou/tools/tmole

Exit codes
----------
  0  - binary is available (downloaded or already cached)
  1  - download failed
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-652076696120284254432061-bbd15c93da40c77ce92e217b"


import argparse
import os
import sys
from pathlib import Path

# Allow running from the repo root without installing.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and cache the tmole binary for AutoYou /pair pairing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if a cached binary already exists.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational output; only print the binary path on success.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        from shared.tunnelmole_downloader import download_tunnelmole
        from shared.tunnelmole_service import resolve_tunnelmole_binary
    except ImportError as exc:
        print(f"[ERROR] Cannot import AutoYou shared modules: {exc}", file=sys.stderr)
        print(
            "        Run this script from the AutoYou repo root, or add the repo root to PYTHONPATH.",
            file=sys.stderr,
        )
        return 1

    # Check whether a usable binary already exists.
    if not args.force:
        existing = resolve_tunnelmole_binary()
        if existing:
            if not args.quiet:
                print(f"[OK] tmole already available: {existing}")
            else:
                print(existing)
            return 0

    if not args.quiet:
        print(f"[INFO] Downloading tmole for {sys.platform}...")

    result = download_tunnelmole(force=args.force)
    if result is None or not Path(result).is_file():
        print("[ERROR] Failed to download tmole binary.", file=sys.stderr)
        print(
            "        Check your internet connection or set AUTOYOU_TUNNELMOLE_BIN "
            "to the path of an existing tmole binary.",
            file=sys.stderr,
        )
        return 1

    binary_path = Path(result).resolve()

    # Ensure the binary is executable on POSIX.
    if os.name != "nt":
        try:
            binary_path.chmod(0o755)
        except OSError:
            pass

    if not args.quiet:
        print(f"[OK] tmole installed: {binary_path}")
    else:
        print(str(binary_path))

    return 0


if __name__ == "__main__":
    sys.exit(main())
