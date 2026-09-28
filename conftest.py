# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from shared.platform_runtime import clear_test_runtime_state_overrides


collect_ignore_glob = [
    "openclaw/openclaw/**",
    # Runtime workspace the fine-tuning agent writes inside its own package
    # (DB, datasets, dumps, hf_cache, runs, and a vendored llama.cpp checkout).
    # It is generated data, not source/tests - never collect it.
    "autoyou_agents/fine_tuning_agent/fine_tuning_agent/**",
    "vendor/cognee/**",
    "**/workspace/tools/llama.cpp/**",
]


_EXTERNAL_AUTOYOU_TEST_ROOT = bool(os.environ.get("AUTOYOU_TEST_ROOT"))
_AUTOYOU_TEST_ROOT = Path(
    os.environ.get("AUTOYOU_TEST_ROOT") or tempfile.mkdtemp(prefix="autoyou-pytest-")
).resolve()
_AUTOYOU_TEST_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["AUTOYOU_TEST_ROOT"] = str(_AUTOYOU_TEST_ROOT)
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
clear_test_runtime_state_overrides(os.environ)


@atexit.register
def _cleanup_autoyou_test_root() -> None:
    if _EXTERNAL_AUTOYOU_TEST_ROOT:
        return
    shutil.rmtree(_AUTOYOU_TEST_ROOT, ignore_errors=True)


@pytest.fixture(scope="session")
def autoyou_test_root() -> Path:
    return _AUTOYOU_TEST_ROOT
