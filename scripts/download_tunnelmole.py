#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-1822ddf1b0807c7943275da3

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Check the preferred Node.js pairing runtime or fetch a reviewed tmole fallback.

Normal setup uses --check-runtime after installing the lockfile-pinned npm
client. That check never downloads a binary or starts a public tunnel.
Without --check-runtime this script prepares only the standalone fallback;
downloaded binaries must pass AutoYou's reviewed SHA-256 allowlist.

Usage
-----
    python scripts/download_tunnelmole.py            # download / reuse cached
    python scripts/download_tunnelmole.py --force    # force re-download
    python scripts/download_tunnelmole.py --check-runtime  # no network or install

The binary is stored in the AutoYou user-data tools directory:
  macOS   : ~/Library/Application Support/AutoYou/tools/tmole
  Windows : %APPDATA%\\AutoYou\\tools\\tmole.exe
  Linux   : ~/.local/share/AutoYou/tools/tmole

Exit codes
----------
  0  - binary is available (downloaded or already cached)
  1  - runtime unavailable, download failed, or binary refused by trust policy
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import argparse
import os
import sys
from pathlib import Path

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-1822ddf1b0807c7943275da3"


# Allow running from the repo root without installing.
_REPO_ROOT = Path(__file__).resolve().parents[1]
# from __debug_provenance_y__ import legal
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and cache the tmole binary for AutoYou /pair pairing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--check-runtime",
        action="store_true",
        help="Check the installed Node.js client or existing tmole without downloading or starting a tunnel.",
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
    args = parser.parse_args()
    if args.check_runtime and args.force:
        parser.error("--force cannot be used with --check-runtime")
    return args


def main() -> int:
    args = _parse_args()

    try:
        from shared.tunnelmole_downloader import download_tunnelmole
        from shared.tunnelmole_service import _resolve_node_tunnelmole_runtime, resolve_tunnelmole_binary
    except ImportError as exc:
        print(f"[ERROR] Cannot import AutoYou shared modules: {exc}", file=sys.stderr)
        print(
            "        Run this script from the AutoYou repo root, or add the repo root to PYTHONPATH.",
            file=sys.stderr,
        )
        return 1

    if args.check_runtime:
        runtime = _resolve_node_tunnelmole_runtime()
        if runtime is not None:
            print("[OK] Tunnelmole Node.js client is available (no standalone binary needed).")
            return 0

    # Resolving an existing executable must not trigger a second download.
    if not args.force:
        existing = resolve_tunnelmole_binary(allow_download=False)
        if existing:
            if not args.quiet:
                print(f"[OK] tmole already available: {existing}")
            else:
                print(existing)
            return 0

    if args.check_runtime:
        print(
            "[WARN] Tunnelmole pairing runtime unavailable. Run run_autoyou without --skip-node "
            "to install the lockfile-pinned Node.js client, or restore the packaged runtime. "
            "Direct installations may set AUTOYOU_TUNNELMOLE_BIN to a trusted executable.",
            file=sys.stderr,
        )
        return 1

    if not args.quiet:
        print(f"[INFO] Downloading tmole for {sys.platform}...")

    result = download_tunnelmole(force=args.force)
    if result is None or not Path(result).is_file():
        print("[ERROR] No reviewed standalone tmole binary is available; see the reason above.", file=sys.stderr)
        print(
            "        The preferred pairing runtime is the lockfile-pinned Node.js client. "
            "Run run_autoyou to install it; do not approve a downloaded hash without reviewing the build.",
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
