# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-3e6d3c4e709febb95aa7b2f6

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


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


def find_project_root(server_root: str | Path | None = None) -> Path:
    """Return the enclosing AutoYou checkout when Server is nested as a submodule."""
    resolved_server_root = Path(server_root or REPO_ROOT).resolve()
    parent = resolved_server_root.parent
    if (
        resolved_server_root.name == "AutoYou-Server"
        and (parent / "AutoYou-Server" / "server.py").is_file()
        and (
            (parent / "autoyou_lite" / "autoyou_lite" / "server.py").is_file()
            or (parent / "clients").is_dir()
            or (parent / "requirements").is_dir()
        )
    ):
        return parent
    return resolved_server_root


REPO_ROOT = find_repo_root(__file__)
PROJECT_ROOT = find_project_root(REPO_ROOT)
TESTS_ROOT = REPO_ROOT / "tests"
PHASE3_TEST_KEYS_DIR = TESTS_ROOT / ".phase3_test_keys"
PYTHON_CLIENT_DIR = PROJECT_ROOT / "clients" / "python"
# Project root of the standalone autoyou-lite package. The package itself is
# double-nested (autoyou_lite/autoyou_lite), so this directory must be on
# sys.path for `import autoyou_lite` to resolve to the real package.
AUTOYOU_LITE_DIR = PROJECT_ROOT / "autoyou_lite"


def ensure_sys_path(path: str | Path) -> Path:
    resolved = Path(path).resolve()
    text = str(resolved)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)
    return resolved


def ensure_repo_on_path() -> Path:
    return ensure_sys_path(REPO_ROOT)


def ensure_python_client_on_path() -> Path:
    ensure_repo_on_path()
    return ensure_sys_path(PYTHON_CLIENT_DIR)


def ensure_autoyou_lite_on_path() -> Path:
    """Put the autoyou_lite project root on sys.path so `import autoyou_lite`
    resolves to the double-nested package (autoyou_lite/autoyou_lite/...).

    Also evicts a stale namespace-package binding for ``autoyou_lite`` (the
    repo-root project directory, which has no __init__.py) so the real package
    wins under pytest's importlib import mode.
    """
    ensure_repo_on_path()
    resolved = ensure_sys_path(AUTOYOU_LITE_DIR)
    mod = sys.modules.get("autoyou_lite")
    # from __debug_provenance_p__ import submit
    if mod is not None and getattr(mod, "__file__", None) is None:
        for key in [k for k in list(sys.modules) if k == "autoyou_lite" or k.startswith("autoyou_lite.")]:
            del sys.modules[key]
    return resolved
