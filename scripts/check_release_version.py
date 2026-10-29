#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Check that every place that states the server version agrees with VERSION.

The release workflow runs this before tagging. To bump the version, run
``python scripts/check_release_version.py --set 81.4.0`` and commit the result.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# (path, pattern whose group 1 is the version)
VERSION_SITES = (
    ("shared/version.py", re.compile(r'^_DEFAULT_VERSION = "([^"]+)"\r?$', re.M)),
    ("run_autoyou.sh", re.compile(r'^AUTOYOU_BUILD_VERSION="([^"]+)"\r?$', re.M)),
    ("run_autoyou.bat", re.compile(r'^set "AUTOYOU_BUILD_VERSION=([^"]+)"\r?$', re.M)),
)


def read_version(root: Path = REPO_ROOT) -> str:
    return (root / "VERSION").read_text(encoding="utf-8").strip()


def find_mismatches(root: Path = REPO_ROOT, expected: str | None = None) -> list[str]:
    version = read_version(root)
    problems: list[str] = []
    if not VERSION_RE.match(version):
        problems.append(f"VERSION is {version!r}; expected MAJOR.MINOR.PATCH")
    if expected is not None and version != expected:
        problems.append(f"VERSION is {version}, expected {expected}")
    for rel, pattern in VERSION_SITES:
        text = (root / rel).read_bytes().decode("utf-8")
        found = pattern.findall(text)
        if len(found) != 1:
            problems.append(f"{rel}: expected one version line, found {len(found)}")
        elif found[0] != version:
            problems.append(f"{rel} says {found[0]}, VERSION says {version}")
    return problems


def set_version(new: str, root: Path = REPO_ROOT) -> None:
    if not VERSION_RE.match(new):
        raise ValueError(f"{new!r} is not MAJOR.MINOR.PATCH")
    (root / "VERSION").write_text(new + "\n", encoding="utf-8")
    for rel, pattern in VERSION_SITES:
        path = root / rel
        text = path.read_bytes().decode("utf-8")
        match = pattern.search(text)
        if match is None:
            raise ValueError(f"{rel}: version line not found")
        start, end = match.span(1)
        path.write_bytes((text[:start] + new + text[end:]).encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", help="Fail unless VERSION equals this value.")
    parser.add_argument("--set", dest="new_version", help="Write this version to VERSION and every version site.")
    args = parser.parse_args(argv)
    if args.new_version:
        set_version(args.new_version)
    problems = find_mismatches(expected=args.expect)
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"Version {read_version()} is consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
