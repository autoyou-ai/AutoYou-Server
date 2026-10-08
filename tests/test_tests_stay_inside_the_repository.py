# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Tests, scripts and server modules read only this repository.

The repository is published on its own, so code that reaches above its root
either fails for everyone who clones it or quietly depends on, and reveals,
whatever happens to sit next to it on one machine. Anything from another source
tree comes in only through an explicit argument or manifest.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED = [REPO_ROOT / "tests", REPO_ROOT / "autoyou_agents", REPO_ROOT / "conftest.py"]
CODE = [REPO_ROOT / name for name in ("scripts", "shared", "core_server", "routers", "servers")]

_FILE_PARENTS = re.compile(r"Path\(\s*__file__\s*\)(?:\.resolve\(\))?\.parents\[(\d+)\]")
_ROOT_PARENT = re.compile(
    r"\b(?:REPO_ROOT|PROJECT_ROOT|ROOT|SERVER_ROOT|repo_root|project_root|server_root|resolved_server_root)\.parent\b"
)


def _python_test_sources():
    for entry in SCANNED:
        if entry.is_file():
            yield entry
            continue
        for path in entry.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if entry.name == "autoyou_agents" and not path.name.startswith(("test_", "conftest")):
                continue
            yield path


def test_no_test_builds_a_path_above_the_repository_root():
    escapes = []
    for path in _python_test_sources():
        relative = path.relative_to(REPO_ROOT)
        root_index = len(relative.parts) - 1  # Path(__file__).parents[root_index] is the root
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for match in _FILE_PARENTS.finditer(line):
                if int(match.group(1)) > root_index:
                    escapes.append(f"{relative.as_posix()}:{number}: {line.strip()}")
            if _ROOT_PARENT.search(line):
                escapes.append(f"{relative.as_posix()}:{number}: {line.strip()}")
    assert escapes == [], "tests must stay inside this repository:\n" + "\n".join(escapes)


def _code_sources():
    yield from sorted(REPO_ROOT.glob("*.py"))
    for entry in CODE:
        for path in sorted(entry.rglob("*.py")):
            if not any(part in {"__pycache__", "dist", "build", "obj", "bin", "artifacts"} for part in path.parts):
                yield path


def test_no_script_or_server_module_builds_a_path_above_the_repository_root():
    escapes = []
    for path in _code_sources():
        relative = path.relative_to(REPO_ROOT)
        root_index = len(relative.parts) - 1
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for match in _FILE_PARENTS.finditer(line):
                if int(match.group(1)) > root_index:
                    escapes.append(f"{relative.as_posix()}:{number}: {line.strip()}")
            if _ROOT_PARENT.search(line):
                escapes.append(f"{relative.as_posix()}:{number}: {line.strip()}")
    assert escapes == [], "code must stay inside this repository:\n" + "\n".join(escapes)
