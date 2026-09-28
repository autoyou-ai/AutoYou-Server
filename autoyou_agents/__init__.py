# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-7363686564756c6520796561-fa8e858ec8751542ebb90289

"""AutoYou agents package.

Keep package import lightweight. Importing ``autoyou_agents`` should not
initialize the full root agent graph, because many admin/runtime modules only
need utility submodules such as ``autoyou_agents.notes_agent.notes_tool``.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-7363686564756c6520796561-fa8e858ec8751542ebb90289"


import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

__all__ = ["app", "root_agent"]

def _extend_package_path_for_runtime_agents() -> None:
    """Allow compiled builds to import user-scaffolded autoyou_agents packages."""
    try:
        from shared.platform_runtime import get_dynamic_agents_root, is_compiled

        package_path = globals().get("__path__", None)
        if package_path is None:
            return
        sibling = Path(__file__).resolve().parents[2] / "autoyou_agents"
        if not is_compiled() and (sibling / "__init__.py").is_file():
            sibling_path = str(sibling.resolve())
            if sibling_path not in package_path:
                package_path.append(sibling_path)
            private_root = sibling / "private"
            if private_root.is_dir() and str(private_root.resolve()) not in package_path:
                package_path.append(str(private_root.resolve()))
        dynamic_root = str(get_dynamic_agents_root("AutoYou", anchor=__file__))
        if dynamic_root not in package_path:
            package_path.append(dynamic_root)
    except Exception as exc:
        LOGGER.debug("Could not extend autoyou_agents package path: %s", exc)

_extend_package_path_for_runtime_agents()

def __getattr__(name: str) -> Any:
    if name not in {"app", "root_agent"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        if name == "app":
            from autoyou_agents.agent import app as resolved_value
        else:
            from autoyou_agents.agent import root_agent as resolved_value
    except Exception as exc:
        LOGGER.warning("autoyou_agents %s import failed: %s", name, exc)
        resolved_value = None
    return resolved_value
