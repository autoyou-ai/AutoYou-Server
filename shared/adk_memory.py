# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-T-000000000000000000000000-69f0479e0095d12cf1c56274

"""Resolve the native ADK SQLite memory store without bypassing test isolation."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
# from __debug_trace_t__ import autoyou_provenance_t_000000000000000000000000_69f0479e0095d12cf1c56274
__license__ = "AutoYou Source-Available License (see LICENSE)"

import os
from pathlib import Path
from typing import Mapping
from urllib.parse import quote

__debug_trace_t__ = "AUTOYOU-PROVENANCE-T-000000000000000000000000-69f0479e0095d12cf1c56274"


AI_AGENT_MEMORY_SERVICE_URI_ENV = "AUTOYOU_AI_MEMORY_SERVICE_URI"
ADK_MEMORY_PERSISTENT_ENV = "AUTOYOU_ADK_MEMORY_PERSISTENT"


def resolve_adk_memory_uri(
    source: Mapping[str, str], default_path: Path, *, allow_persistence: bool = True
) -> str:
    """Use persistent local memory only where the recording/storage policy permits it.

    ADK ingests sessions explicitly through its memory API. A test root takes
    precedence over inherited production URIs. Secure storage uses ephemeral
    ADK memory because the upstream SQLite implementation writes plaintext.
    """
    enabled = str(source.get(ADK_MEMORY_PERSISTENT_ENV, "1")).strip().lower()
    if not allow_persistence or enabled in {"0", "false", "no", "off"}:
        return "memory://"
    test_root = os.environ.get("AUTOYOU_TEST_ROOT", "").strip()
    if test_root:
        path = Path(test_root).expanduser().resolve() / ".adk" / "memory.db"
    else:
        explicit = str(source.get(AI_AGENT_MEMORY_SERVICE_URI_ENV, "")).strip()
        if explicit:
            return explicit
        path = default_path.expanduser().resolve()
    return "sqlite:///" + quote(path.as_posix(), safe="/:")
