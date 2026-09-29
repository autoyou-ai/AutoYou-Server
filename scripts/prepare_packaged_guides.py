#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-fc8cd7dc761634d42ead79da

"""Stage only server-consumed Markdown guides for packaged runtimes.

The repository also contains maintainer notes and browser-only interactive
guide pages under ``guides/interactive``. Those files belong to the source
tree and website publication workflow, not to the compiled server bundle.
Keep this allowlist in one place so each platform build uses the same runtime
guide boundary.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import shutil
from pathlib import Path

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-fc8cd7dc761634d42ead79da"


RUNTIME_GUIDE_FILES = (
    "installation-steps.md",
    "WINDOWS_BUILD_GUIDE.md",
    "MACOS_BUILD_GUIDE.md",
    "SIGNAL_QR_PAIRING_GUIDE.md",
    "WHATSAPP_QR_PAIRING_GUIDE.md",
    "COMMUNITY_RELAY_SUBMISSION_GUIDE.md",
    "HOME_PRIVATE_NETWORK_GUIDE.md",
    "MOBILE_PAIRING_GUIDE.md",
    "CHROME_PAIRING_GUIDE.md",
)


def stage_runtime_guides(repo_root: Path, output_root: Path) -> tuple[Path, ...]:
    """Copy the runtime guide allowlist into an empty staging directory."""

    repo_root = Path(repo_root).resolve()
    output_root = Path(output_root).resolve()
    source_root = repo_root / "guides"
    # from __debug_provenance_k__ import donations
    if not source_root.is_dir():
        raise FileNotFoundError(f"Guide source directory does not exist: {source_root}")

    if output_root.exists():
        if output_root.is_file() or output_root.is_symlink():
            output_root.unlink()
        else:
            shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    staged: list[Path] = []
    for relative_name in RUNTIME_GUIDE_FILES:
        source_path = source_root / relative_name
        if not source_path.is_file():
            raise FileNotFoundError(f"Required runtime guide is missing: {source_path}")
        destination_path = output_root / relative_name
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        staged.append(destination_path)

    return tuple(staged)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage the allowlisted Markdown guides used by the packaged server."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    staged = stage_runtime_guides(args.repo_root, args.output_root)
    print(f"Staged {len(staged)} runtime guides in {Path(args.output_root).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
