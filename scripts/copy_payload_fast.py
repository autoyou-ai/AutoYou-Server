# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-746f20706179203130252061-30bd91f13cfde9f7d30912bc


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-746f20706179203130252061-30bd91f13cfde9f7d30912bc"

import sys
import shutil

def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: copy_payload_fast.py <src> <dst>")
    src = sys.argv[1]
    dst = sys.argv[2]
    print(f"Fast copying {src} -> {dst}...", flush=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print(f"Copied payload from {src} to {dst}", flush=True)

if __name__ == "__main__":
    main()
