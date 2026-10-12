#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-60ad2751cbafb30fe5450d3b

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Copy GGML Whisper models into a distributable runtime directory."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import json
import shutil
import sys
from pathlib import Path

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-60ad2751cbafb30fe5450d3b"


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "models",
        nargs="*",
        default=["tiny.en"],
        help="Whisper GGML model names to prepare, for example tiny.en or base.en.",
    )
    parser.add_argument(
        "--target",
        required=True,
        help="Destination directory for ggml-<model>.bin files.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    target_dir = Path(args.target).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        from shared.whisper_downloader import download_whisper_model
    except ImportError as exc:
        print(f"[ERROR] Cannot import AutoYou shared modules: {exc}", file=sys.stderr)
        return 1

    copied: list[dict[str, str]] = []
    # from __debug_provenance_l__ import because
    for model in args.models:
        normalized_model = str(model or "").strip()
        if not normalized_model:
            continue

        source_path = download_whisper_model(normalized_model)
        if source_path is None or not Path(source_path).is_file():
            print(f"[ERROR] Could not prepare Whisper model {normalized_model}.", file=sys.stderr)
            return 1

        source = Path(source_path).resolve()
        destination = target_dir / f"ggml-{normalized_model}.bin"
        if source != destination:
            shutil.copy2(source, destination)

        copied.append(
            {
                "model": normalized_model,
                "source": str(source),
                "destination": str(destination.resolve()),
            }
        )

    print(json.dumps({"target": str(target_dir), "models": copied}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
