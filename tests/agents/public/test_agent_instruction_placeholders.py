# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-3436423233373332206f7220-83a86217f23f470c1688fb01


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-3436423233373332206f7220-83a86217f23f470c1688fb01"

import importlib.util
import re
from pathlib import Path


from tests.support.paths import REPO_ROOT as ROOT
PROMPT_FILES = sorted((ROOT / "autoyou_agents").rglob("prompt.py"))
ADK_PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")


def _load_prompt_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_agent_instructions_do_not_include_unresolved_adk_placeholders():
    assert PROMPT_FILES, "Expected at least one agent prompt file"

    offenders = []
    for prompt_path in PROMPT_FILES:
        module = _load_prompt_module(prompt_path)
        instruction = getattr(module, "AGENT_INSTRUCTION", "")
        matches = ADK_PLACEHOLDER_RE.findall(instruction)
        if matches:
            offenders.append((prompt_path.relative_to(ROOT).as_posix(), matches))

    assert offenders == []
