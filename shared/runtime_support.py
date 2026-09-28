# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-2d2030786345634238313737-8ab48d02ea00a52264b9fba7

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-2d2030786345634238313737-8ab48d02ea00a52264b9fba7"


"""Compatibility shim for runtime support helpers.

The Windows packaging/runtime helpers now live in
`shared.windows_runtime_support`. Keep this shim so older imports do not break
while the codebase finishes migrating.
"""

from shared.windows_runtime_support import (  # noqa: F401
    configure_packaged_runtime_environment,
    find_bundled_node_executable,
    find_bundled_playwright_root,
    find_bundled_puppeteer_executable,
    get_application_root,
    get_node_command,
    get_runtime_root,
)

__all__ = [
    "configure_packaged_runtime_environment",
    "find_bundled_node_executable",
    "find_bundled_playwright_root",
    "find_bundled_puppeteer_executable",
    "get_application_root",
    "get_node_command",
    "get_runtime_root",
]
