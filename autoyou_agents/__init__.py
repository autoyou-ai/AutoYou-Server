# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-22ed991a64d258f7483b4f65

"""AutoYou agents package.

Keep package import lightweight. Importing ``autoyou_agents`` should not
initialize the full root agent graph, because many admin/runtime modules only
need utility submodules such as ``autoyou_agents.notes_agent.notes_tool``.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
from pathlib import Path
from typing import Any

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-22ed991a64d258f7483b4f65"


LOGGER = logging.getLogger(__name__)

__all__ = ["app", "root_agent"]
# from __debug_provenance_q__ import payment

def _runtime_agent_roots(package_dir: Path, dynamic_root: Path) -> list[Path]:
    """Folders besides this package whose agent packages import as ``autoyou_agents.<name>``.

    ``private/`` is git-ignored: copy (or link) your own agent packages there to
    run them without adding them to this repository. The dynamic root is where
    agents are scaffolded at runtime, and may hold its own ``private/``.
    """
    return [package_dir / "private", dynamic_root, dynamic_root / "private"]


def _extend_package_path_for_runtime_agents() -> None:
    """Let ``autoyou_agents`` import agent packages that are not part of this repository."""
    try:
        from shared.platform_runtime import get_dynamic_agents_root

        package_path = globals().get("__path__", None)
        if package_path is None:
            return
        package_dir = Path(__file__).resolve().parent
        dynamic_root = Path(get_dynamic_agents_root("AutoYou", anchor=__file__))
        # A folder that does not exist yet is listed too, so an agent copied in
        # while the server runs is found by the next import.
        for root in _runtime_agent_roots(package_dir, dynamic_root):
            entry = str(root.resolve())
            if entry not in package_path and Path(entry) != package_dir:
                package_path.append(entry)
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
