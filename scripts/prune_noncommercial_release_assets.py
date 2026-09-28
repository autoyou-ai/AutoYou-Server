#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Prune or check non-commercial model assets from release bundle roots."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


MODEL_SUFFIXES = {".onnx", ".tflite", ".pt", ".pth"}


def _is_openwakeword_model_asset(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    if "openwakeword" not in parts:
        return False
    joined = "/".join(parts)
    if "/openwakeword/resources/models/" not in joined:
        return False
    return path.is_file() and path.suffix.lower() in MODEL_SUFFIXES


def find_noncommercial_assets(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if _is_openwakeword_model_asset(path))


def _remove_empty_parents(path: Path, stop_at: Path) -> None:
    current = path.parent
    stop_at = stop_at.resolve()
    while current.exists():
        try:
            if current.resolve() == stop_at:
                return
        except OSError:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def prune_noncommercial_assets(root: Path) -> list[Path]:
    matches = find_noncommercial_assets(root)
    for path in matches:
        path.unlink(missing_ok=True)
        _remove_empty_parents(path, root)

    for resources_dir in sorted(root.rglob("openwakeword/resources")) if root.exists() else []:
        if resources_dir.is_dir():
            shutil.rmtree(resources_dir, ignore_errors=True)
    return matches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check or prune non-commercial assets from AutoYou release roots.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail if non-commercial assets are present.")
    mode.add_argument("--prune", action="store_true", help="Delete non-commercial assets if present.")
    parser.add_argument("--root", action="append", type=Path, required=True, help="Root directory to scan. Repeatable.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    all_matches: list[Path] = []
    for root in args.root:
        root = root.resolve()
        matches = prune_noncommercial_assets(root) if args.prune else find_noncommercial_assets(root)
        for match in matches:
            print(match)
        all_matches.extend(matches)

    if args.check and all_matches:
        print(
            "Commercial release blocker: openWakeWord non-commercial pretrained model assets were found. "
            "Prune them, replace them, or obtain compatible commercial rights."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
