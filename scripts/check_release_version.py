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

# (path, pattern whose group 1 is the version, expected match count).
# Windows manifests use a four-part version; the pattern captures the first
# three parts and requires the trailing ".0".
V = r"(\d+\.\d+\.\d+)"
VERSION_SITES = (
    ("shared/version.py", re.compile(r'^_DEFAULT_VERSION = "' + V + r'"\r?$', re.M), 1),
    ("run_autoyou.sh", re.compile(r'^AUTOYOU_BUILD_VERSION="' + V + r'"\r?$', re.M), 1),
    ("run_autoyou.bat", re.compile(r'^set "AUTOYOU_BUILD_VERSION=' + V + r'"\r?$', re.M), 1),
    ("installer/AutoYouInstaller.iss", re.compile(r'#define MyAppVersion "' + V + r'"'), 1),
    ("servers/macos/apple/AutoYou/Info.plist",
     re.compile(r"<key>CFBundle(?:ShortVersionString|Version)</key>\s*<string>" + V + r"</string>"), 2),
    ("servers/windows/AutoYouWindowsHost/Package.appxmanifest",
     re.compile(r'Publisher="CN=OpenStorey LLC"\s+Version="' + V + r'\.0"'), 1),
    ("servers/windows/AutoYouWindowsHost/app.manifest",
     re.compile(r'<assemblyIdentity version="' + V + r'\.0" name="AutoYou\.app"'), 1),
    ("servers/README.md", re.compile(r"`VERSION` file,\s+currently `" + V + "`"), 1),
    ("servers/macos/README.md", re.compile(r"`VERSION` file, currently `" + V + "`"), 1),
    ("servers/macos/intel/README.md", re.compile(r"currently `" + V + "`"), 1),
    ("servers/windows/README.md", re.compile(r"`VERSION` file, currently `" + V + "`"), 1),
    ("servers/wsl/README.md", re.compile(r"`VERSION` value, currently `" + V + "`"), 1),
    ("servers/wsl/snap/README.md", re.compile(r"^`" + V + "`; it is not declared", re.M), 1),
    ("guides/MACOS_BUILD_GUIDE.md", re.compile(r"`VERSION`\s+file, currently `" + V + "`"), 1),
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
    for rel, pattern, count in VERSION_SITES:
        text = (root / rel).read_bytes().decode("utf-8")
        found = pattern.findall(text)
        if len(found) != count:
            problems.append(f"{rel}: expected {count} version mention(s), found {len(found)}")
        for value in sorted(set(found) - {version}):
            problems.append(f"{rel} says {value}, VERSION says {version}")
    return problems


def set_version(new: str, root: Path = REPO_ROOT) -> None:
    if not VERSION_RE.match(new):
        raise ValueError(f"{new!r} is not MAJOR.MINOR.PATCH")
    (root / "VERSION").write_text(new + "\n", encoding="utf-8")
    for rel, pattern, _count in VERSION_SITES:
        path = root / rel
        text = path.read_bytes().decode("utf-8")
        matches = list(pattern.finditer(text))
        if not matches:
            raise ValueError(f"{rel}: version mention not found")
        for match in reversed(matches):
            start, end = match.span(1)
            text = text[:start] + new + text[end:]
        path.write_bytes(text.encode("utf-8"))


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
