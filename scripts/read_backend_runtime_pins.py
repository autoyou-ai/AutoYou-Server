# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-313d494f2200a6e3e23e7819

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-313d494f2200a6e3e23e7819"


import argparse
import json
import re
import sys
from pathlib import Path


_PIN_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)")
_INCLUDE_RE = re.compile(r"^\s*(?:-r|--requirement)\s+(.+?)\s*$")


def _normalize_package_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _resolve_exact_pins(
    requirements_file: Path,
    requested_packages: list[str],
) -> dict[str, str]:
    requested_lookup = {_normalize_package_name(name): name for name in requested_packages}
    discovered: dict[str, str] = {}
    visited_files: set[Path] = set()

    def walk(path: Path) -> None:
        resolved_path = path.resolve()
        if resolved_path in visited_files:
            return
        if not resolved_path.is_file():
            raise FileNotFoundError(f"Requirements file not found: {resolved_path}")
        visited_files.add(resolved_path)

        for raw_line in resolved_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            include_match = _INCLUDE_RE.match(line)
            if include_match:
                nested_path = include_match.group(1).strip().strip("\"'")
                walk((resolved_path.parent / nested_path).resolve())
                continue

            pin_match = _PIN_RE.match(line)
            if not pin_match:
                continue

            package_name = _normalize_package_name(pin_match.group(1))
            if package_name not in requested_lookup:
                continue

            discovered[package_name] = pin_match.group(2).strip()

    walk(requirements_file)

    missing = [name for name in requested_packages if _normalize_package_name(name) not in discovered]
    if missing:
        missing_display = ", ".join(missing)
        raise ValueError(
            f"Missing exact '==' runtime pins for: {missing_display} in {requirements_file.resolve()}"
        )

    return {_normalize_package_name(name): discovered[_normalize_package_name(name)] for name in requested_packages}


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve exact backend runtime package pins from recursive requirements files."
    )
    parser.add_argument(
        "--requirements-file",
        required=True,
        help="Root requirements file to parse. Supports recursive -r/--requirement includes.",
    )
    parser.add_argument(
        "--packages",
        nargs="+",
        required=True,
        help="Package names to resolve in output order.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "lines", "spec"),
        default="json",
        help="Output format.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)

    requirements_file = Path(args.requirements_file).expanduser()
    packages = [_normalize_package_name(name) for name in args.packages]
    pins = _resolve_exact_pins(requirements_file=requirements_file, requested_packages=packages)

    if args.format == "json":
        print(json.dumps(pins, sort_keys=True))
        return 0

    ordered_specs = [f"{name}=={pins[name]}" for name in packages]
    if args.format == "lines":
        print("\n".join(ordered_specs))
        return 0

    print(", ".join(ordered_specs))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
