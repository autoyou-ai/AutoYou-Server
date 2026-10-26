#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Install the PyTorch build that trains on this machine's GPU (platform builds).

The Windows and WSL backend builds call this for the training-full profile
before installing requirements. See shared/torch_wheel_selection.py for the
selection rules and the AUTOYOU_TORCH_BACKEND / AUTOYOU_TORCH_ROCM_ARCH
overrides; bootstrap_autoyou.py performs the same step itself.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.torch_wheel_selection import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
