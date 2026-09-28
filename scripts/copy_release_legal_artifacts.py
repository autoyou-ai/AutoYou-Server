#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Copy a generated AutoYou legal bundle into a release artifact directory."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

try:
    from scripts.generate_release_legal_artifacts import REPO_ROOT, generate_artifacts, load_config
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from generate_release_legal_artifacts import REPO_ROOT, generate_artifacts, load_config


def copy_release_legal_artifacts(*, artifact_id: str, target: Path, generate: bool) -> list[Path]:
    config = load_config()
    if generate:
        generate_artifacts(config)
    generated_root = REPO_ROOT / config.get("generatedRoot", "docs/legal/generated")
    source_dir = generated_root / artifact_id
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Generated legal bundle not found for artifact {artifact_id!r}: {source_dir}")

    target.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for filename in ("NOTICE.txt", "sbom.cdx.json"):
        source = source_dir / filename
        if not source.is_file():
            raise FileNotFoundError(f"Missing generated legal file: {source}")
        destination = target / filename
        shutil.copy2(source, destination)
        copied.append(destination)

    for repo_file in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
        source = REPO_ROOT / repo_file
        if not source.is_file():
            raise FileNotFoundError(f"Missing repository legal file: {source}")
        destination = target / repo_file
        shutil.copy2(source, destination)
        copied.append(destination)
    return copied


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy generated AutoYou legal files into a package output.")
    parser.add_argument("--artifact", required=True, help="Artifact profile id from docs/legal/release-artifacts.json.")
    parser.add_argument("--target", type=Path, required=True, help="Directory that should receive the Legal files.")
    parser.add_argument("--generate", action="store_true", help="Refresh the artifact legal bundle before copying.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    copied = copy_release_legal_artifacts(
        artifact_id=args.artifact,
        target=args.target.resolve(),
        generate=bool(args.generate),
    )
    for path in copied:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
