# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Keep shadowing ``sys.path`` entries out of multiprocessing children.

``cv2``'s loader inserts its own package directory (``site-packages/cv2``) near
the front of ``sys.path`` while it relinks the native extension, then restores
the saved list when it is done -- the ``sys.path = save_sys_path`` line in
``cv2/__init__.py``, added upstream for exactly this reason
(https://github.com/opencv/opencv/issues/18502).

That restore is not atomic with respect to other threads. ``site-packages/cv2``
ships a ``typing/`` package, so a thread that starts a ``spawn`` (or
``forkserver``) child *during* that window snapshots a ``sys.path`` on which
stdlib ``import typing`` resolves to ``cv2/typing/__init__.py``. The child dies
in interpreter bootstrap with a circular-import error out of ``inspect`` ->
``typing``, followed by ``numpy._core.multiarray failed to import``, long
before it reaches the code it was started to run.

``multiprocessing.spawn.get_preparation_data()`` is where that snapshot is
taken, so filtering it there covers every spawn site at once and -- unlike
mutating ``sys.path`` around each ``Process.start()`` -- never perturbs the
parent's own imports or races with them.
"""

from __future__ import annotations

import functools
import logging
import os
from typing import List, Sequence

LOGGER = logging.getLogger(__name__)

_GUARD_MARKER = "_autoyou_syspath_guard"
_installed = False


def _is_package_directory(entry: object) -> bool:
    """Return True when ``entry`` is itself an importable package directory.

    A well-formed ``sys.path`` entry holds *top-level* modules. When a package
    directory (one containing ``__init__.py``) also lands on ``sys.path``, every
    subpackage inside it becomes importable under its bare name -- which is how
    ``cv2/typing`` comes to mask the stdlib ``typing`` module.

    Python never adds such an entry itself, so dropping it from a child's path
    is safe: the package that added it re-adds it in the child at the point it
    is actually imported.
    """
    if not isinstance(entry, str) or not entry:
        return False
    try:
        if not os.path.isdir(entry):
            return False
        return any(
            os.path.isfile(os.path.join(entry, name))
            for name in ("__init__.py", "__init__.pyc")
        )
    except Exception:
        # A path entry we cannot stat is not one we should be removing.
        return False


def sanitize_child_sys_path(paths: Sequence[object]) -> List[object]:
    """Drop entries that would shadow top-level modules in a fresh interpreter."""
    return [entry for entry in paths if not _is_package_directory(entry)]


def install_multiprocessing_syspath_guard() -> bool:
    """Patch ``multiprocessing.spawn.get_preparation_data`` at most once.

    Returns True when the guard is active (an already-installed guard counts as
    active). Never raises: a failure here must not stop the process from
    starting, it only forfeits the protection.
    """
    global _installed
    if _installed:
        return True

    try:
        from multiprocessing import spawn as mp_spawn
    except Exception as exc:
        LOGGER.debug("multiprocessing sys.path guard unavailable: %s", exc)
        return False

    original = getattr(mp_spawn, "get_preparation_data", None)
    if original is None:
        LOGGER.debug("multiprocessing.spawn.get_preparation_data missing; guard skipped")
        return False
    if getattr(original, _GUARD_MARKER, False):
        _installed = True
        return True

    @functools.wraps(original)
    def _guarded_get_preparation_data(*args, **kwargs):
        data = original(*args, **kwargs)
        try:
            paths = data.get("sys_path")
            if isinstance(paths, list):
                cleaned = sanitize_child_sys_path(paths)
                if len(cleaned) != len(paths):
                    removed = [entry for entry in paths if entry not in cleaned]
                    data["sys_path"] = cleaned
                    LOGGER.warning(
                        "Dropped %d shadowing sys.path entry(ies) from multiprocessing "
                        "child to avoid masking stdlib modules: %s",
                        len(removed),
                        ", ".join(str(entry) for entry in removed),
                    )
        except Exception as exc:
            LOGGER.debug("Could not sanitize child sys.path: %s", exc)
        return data

    # functools.wraps copies the wrapped function's __dict__, so stamp the
    # marker afterwards or it would be overwritten.
    setattr(_guarded_get_preparation_data, _GUARD_MARKER, True)
    mp_spawn.get_preparation_data = _guarded_get_preparation_data
    _installed = True
    LOGGER.debug("Installed multiprocessing sys.path guard")
    return True
