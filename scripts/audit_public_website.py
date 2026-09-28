# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-7cb3dff0577fd7bd418ae44f

#!/usr/bin/env python3
"""Reject internal planning and reviewer notes from the public website tree."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-7cb3dff0577fd7bd418ae44f"


import argparse
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WEBSITE_ROOT = REPO_ROOT / "autoyou-website"
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".mdx", ".php", ".svg", ".txt", ".xml"}
FORBIDDEN_MARKERS = (
    ("account rollout plan", re.compile(r"\baccount\s+rollout\s+plan\b", re.I)),
    ("more profiles to claim", re.compile(r"\bmore\s+profiles\s+to\s+claim\b", re.I)),
    ("candidate channels", re.compile(r"\bcandidate\s+channels\b", re.I)),
    ("launch plan", re.compile(r"\blaunch\s+plan\b", re.I)),
    ("private prompt", re.compile(r"\bprivate\s+prompts?\b", re.I)),
    ("reviewer note", re.compile(r"\breviewer\s+(?:setup|request|note|instructions?)\b", re.I)),
    ("App Store review contact", re.compile(r"\bapp[- ]store\s+review\s+contact\b", re.I)),
    ("review path", re.compile(r"\breview\s+path\b", re.I)),
    ("developer/operator note", re.compile(r"\b(?:developer|operator)\s+(?:note|instructions?)\b", re.I)),
    ("unfinished note", re.compile(r"\b(?:TODO|FIXME)\b", re.I)),
)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def find_violations(website_root: Path = WEBSITE_ROOT) -> list[str]:
    violations: list[str] = []
    for path in website_root.rglob("*"):
        if "node_modules" in path.parts:
            continue
        if not path.is_file():
            continue
        relative = _relative(path, website_root)
        if "graphify-out" in path.parts:
            violations.append(f"{relative}: internal code graph must not exist in public website tree")
            continue
        if path.name.lower().startswith("readme"):
            violations.append(f"{relative}: README files are not publishable")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for label, marker in FORBIDDEN_MARKERS:
            match = marker.search(content)
            if match:
                line = content.count("\n", 0, match.start()) + 1
                violations.append(f"{relative}:{line}: forbidden {label}")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--website-root", type=Path, default=WEBSITE_ROOT)
    args = parser.parse_args()
    violations = find_violations(args.website_root)
    if violations:
        print("PUBLIC_WEBSITE_AUDIT_FAILED")
        print("\n".join(violations))
        return 1
    print("PUBLIC_WEBSITE_AUDIT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
