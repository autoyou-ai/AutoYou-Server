# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-1a133ff1cdf69dfd24bd75b6

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Prune copied runtime_site_packages to the active requirements closure.

Windows packaging copies the build interpreter's site-packages into the backend
artifact so runtime-loaded modules can still import source packages. A reused
developer/build venv can contain extra packages that are not part of the active
release profile. This script removes those undeclared distributions from the
copied artifact only; it never mutates the source virtualenv.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import argparse
import os
import re
import shutil
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-1a133ff1cdf69dfd24bd75b6"


try:
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
except Exception as exc:  # pragma: no cover - build-env setup failure
    raise SystemExit(f"packaging is required to prune runtime site-packages: {exc}")


@dataclass(frozen=True)
class ParsedRequirement:
    requirement: Requirement
    source: Path


@dataclass
class RuntimeDistribution:
    name: str
    normalized_name: str
    dist: metadata.Distribution
    files: tuple[Path, ...]


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "").strip()).lower()


def _strip_inline_comment(line: str) -> str:
    if " #" not in line:
        return line
    return line.split(" #", 1)[0].rstrip()


def _egg_requirement(line: str) -> Requirement | None:
    if "#egg=" not in line:
        return None
    parsed = urlparse(line)
    # from __debug_provenance_n__ import license
    egg_values = parse_qs(parsed.fragment).get("egg")
    if not egg_values:
        return None
    egg = egg_values[0].strip()
    if not egg:
        return None
    return Requirement(egg)


def parse_requirement_files(paths: Iterable[Path]) -> list[ParsedRequirement]:
    parsed: list[ParsedRequirement] = []
    seen: set[Path] = set()

    def visit(path: Path) -> None:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        for raw_line in resolved.read_text(encoding="utf-8").splitlines():
            line = _strip_inline_comment(raw_line.strip())
            if not line or line.startswith("#"):
                continue

            if line.startswith("-r ") or line.startswith("--requirement "):
                nested = line.split(maxsplit=1)[1].strip()
                visit((resolved.parent / nested).resolve())
                continue
            if line.startswith("-c ") or line.startswith("--constraint "):
                continue
            if line.startswith("-e ") or line.startswith("--editable "):
                line = line.split(maxsplit=1)[1].strip()

            requirement = _egg_requirement(line)
            if requirement is None:
                if line.startswith("-"):
                    continue
                try:
                    requirement = Requirement(line)
                except Exception as exc:
                    raise SystemExit(f"Could not parse requirement {line!r} from {resolved}: {exc}")
            parsed.append(ParsedRequirement(requirement=requirement, source=resolved))

    for path in paths:
        visit(path)
    return parsed


def requirement_applies(requirement: Requirement, extras: Iterable[str]) -> bool:
    marker = requirement.marker
    if marker is None:
        return True
    env = default_environment()
    for extra in set(extras) | {""}:
        env["extra"] = extra
        try:
            if marker.evaluate(env):
                return True
        except Exception:
            return False
    return False


def collect_distributions(site_packages_root: Path) -> dict[str, RuntimeDistribution]:
    distributions: dict[str, RuntimeDistribution] = {}
    for dist in metadata.distributions(path=[str(site_packages_root)]):
        name = dist.metadata.get("Name") or ""
        normalized = normalize_name(name)
        if not normalized or normalized in distributions:
            continue
        files: list[Path] = []
        for package_file in dist.files or ():
            path = Path(str(package_file))
            if path.is_absolute():
                continue
            files.append(path)
        distributions[normalized] = RuntimeDistribution(
            name=name,
            normalized_name=normalized,
            dist=dist,
            files=tuple(files),
        )
    return distributions


def resolve_required_distributions(
    requirements: Iterable[ParsedRequirement],
    distributions: dict[str, RuntimeDistribution],
) -> tuple[set[str], list[str]]:
    required: set[str] = set()
    missing: list[str] = []
    requested_extras: dict[str, set[str]] = defaultdict(lambda: {""})
    processed_extras: dict[str, set[str]] = defaultdict(set)
    queue: deque[str] = deque()

    for parsed in requirements:
        requirement = parsed.requirement
        if not requirement_applies(requirement, {""}):
            continue
        normalized = normalize_name(requirement.name)
        requested_extras[normalized].update(requirement.extras)
        queue.append(normalized)

    while queue:
        current = queue.popleft()
        extras = requested_extras[current]
        if current in required and extras <= processed_extras[current]:
            continue
        processed_extras[current].update(extras)
        required.add(current)

        dist = distributions.get(current)
        if dist is None:
            if current not in missing:
                missing.append(current)
            continue

        for requirement_text in dist.dist.requires or ():
            try:
                dependency = Requirement(requirement_text)
            except Exception:
                continue
            if not requirement_applies(dependency, extras):
                continue
            dependency_name = normalize_name(dependency.name)
            before = set(requested_extras[dependency_name])
            requested_extras[dependency_name].update(dependency.extras)
            if dependency_name not in required or requested_extras[dependency_name] != before:
                queue.append(dependency_name)

    return required, missing


def _safe_child(root: Path, relative: Path) -> Path | None:
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root)
        return candidate
    except Exception:
        return None


def prune_runtime_site_packages(
    site_packages_root: Path,
    requirements_files: Iterable[Path],
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    root = site_packages_root.resolve()
    requirements = parse_requirement_files(requirements_files)
    distributions = collect_distributions(root)
    required, missing = resolve_required_distributions(requirements, distributions)

    owners: dict[Path, set[str]] = defaultdict(set)
    for normalized, dist in distributions.items():
        for package_file in dist.files:
            owners[package_file].add(normalized)

    removed_distributions: list[str] = []
    removed_paths = 0
    for normalized in sorted(set(distributions) - required):
        dist = distributions[normalized]
        removed_any = False
        for package_file in sorted(dist.files, key=lambda item: len(item.parts), reverse=True):
            if owners.get(package_file, set()) & required:
                continue
            target = _safe_child(root, package_file)
            if target is None or not target.exists():
                continue
            removed_paths += 1
            removed_any = True
            if dry_run:
                continue
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        if removed_any:
            removed_distributions.append(dist.name)

    if not dry_run:
        for directory, _dirnames, _filenames in os.walk(root, topdown=False):
            path = Path(directory)
            if path == root:
                continue
            try:
                path.rmdir()
            except OSError:
                pass

    return {
        "kept_distributions": sorted(distributions[name].name for name in required if name in distributions),
        "missing_requirements": sorted(missing),
        "removed_distributions": removed_distributions,
        "removed_paths": removed_paths,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-packages-root", required=True, type=Path)
    parser.add_argument("--requirements-file", action="append", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    result = prune_runtime_site_packages(
        args.site_packages_root,
        args.requirements_file,
        dry_run=args.dry_run,
    )
    removed = result["removed_distributions"]
    missing = result["missing_requirements"]
    print(
        "Runtime site-packages prune: "
        f"kept={len(result['kept_distributions'])} "
        f"removed={len(removed)} paths={result['removed_paths']}"
    )
    if removed:
        print("Removed undeclared runtime distributions: " + ", ".join(str(item) for item in removed))
    if missing:
        print("Warning: required distributions missing from copied runtime: " + ", ".join(str(item) for item in missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
