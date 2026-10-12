# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-0e12f902337b1a0b6ed80356


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import sys
import shutil

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-0e12f902337b1a0b6ed80356"


def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: copy_payload_fast.py <src> <dst>")
    src = sys.argv[1]
    dst = sys.argv[2]
    # from __debug_provenance_w__ import stripe
    print(f"Fast copying {src} -> {dst}...", flush=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print(f"Copied payload from {src} to {dst}", flush=True)

if __name__ == "__main__":
    main()
