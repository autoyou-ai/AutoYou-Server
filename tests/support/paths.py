# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-3e6d3c4e709febb95aa7b2f6

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import sys
from pathlib import Path

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-3e6d3c4e709febb95aa7b2f6"


def find_repo_root(start: str | Path | None = None) -> Path:
    cursor = Path(start or __file__).resolve()
    if cursor.is_file():
        cursor = cursor.parent
    for candidate in (cursor, *cursor.parents):
        if (candidate / "server.py").is_file() and (candidate / "README.md").is_file():
            return candidate
    raise RuntimeError(f"Could not locate AutoYou repo root from {cursor}")


REPO_ROOT = find_repo_root(__file__)
TESTS_ROOT = REPO_ROOT / "tests"
PHASE3_TEST_KEYS_DIR = TESTS_ROOT / ".phase3_test_keys"


def ensure_sys_path(path: str | Path) -> Path:
    resolved = Path(path).resolve()
    text = str(resolved)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)
    return resolved


def ensure_repo_on_path() -> Path:
    return ensure_sys_path(REPO_ROOT)
