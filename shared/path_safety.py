# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Join a client-supplied name onto a directory without leaving it."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import os
from pathlib import Path
from typing import Optional, Union


def contained_path(base: Union[str, Path], *parts: str) -> Optional[Path]:
    """Return ``base`` joined with ``parts``, or None when the result leaves ``base``.

    The joined path is normalized before the check, so ``..`` segments, absolute
    names and (on Windows) backslashes or drive letters cannot escape ``base``.
    """
    root = os.path.realpath(base)
    candidate = os.path.realpath(os.path.join(root, *parts))
    if not candidate.startswith(root + os.sep):
        return None
    return Path(candidate)
