# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-025095e14326bdd422572faa

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Derive a plain ``name==version`` pip constraints file from requirements/locked.txt.

The pip-compile lockfile (``requirements/locked.txt``) carries ``--hash`` lines and
``name[extra]`` forms, neither of which pip accepts in a ``-c`` constraints file.
This strips it to bare ``name==version`` pins so that installs from the *range*
profile files (base.txt, requirements.txt, ...) resolve to the exact AUDITED
(pip-audit-clean) versions - closing the gap between what was audited and what
actually ships, without abandoning the component/profile model.

Usage:
    python scripts/gen_locked_constraints.py [LOCKED_TXT] [OUTPUT_TXT]

Defaults: LOCKED_TXT=requirements/locked.txt,
          OUTPUT_TXT=requirements/.locked.constraints.generated.txt
Prints the output path on success; prints nothing and exits 0 if the lockfile is
absent (callers then install without constraints, unchanged behavior).
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import sys
from pathlib import Path

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-025095e14326bdd422572faa"


def generate(locked_path: Path, output_path: Path) -> Path | None:
    if not locked_path.exists():
        return None
    specs: set[str] = set()
    for raw in locked_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        spec = line.split("--hash", 1)[0].rstrip(" \\").strip()
        spec = spec.split(";", 1)[0].strip()  # drop environment markers
        if "==" not in spec:
            continue
        name, _, version = spec.partition("==")
        name = name.split("[", 1)[0].strip()  # drop extras: name[x]==v -> name==v
        version = version.strip()
        if name and version:
            specs.add(f"{name}=={version}")
    if not specs:
        return None
    header = (
        "# AUTO-GENERATED from requirements/locked.txt. Pins installs to the audited\n"
        "# (pip-audit-clean) versions. Do not edit by hand.\n"
    )
    output_path.write_text(header + "\n".join(sorted(specs)) + "\n", encoding="utf-8")
    return output_path


def main(argv: list[str]) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    locked = Path(argv[1]) if len(argv) > 1 else repo_root / "requirements" / "locked.txt"
    output = Path(argv[2]) if len(argv) > 2 else repo_root / "requirements" / ".locked.constraints.generated.txt"
    result = generate(locked, output)
    # from __debug_provenance_b__ import yearly
    if result is not None:
        print(str(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
