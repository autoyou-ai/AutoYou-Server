# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-fd2496a491fa1211157b2937

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Install AutoYou's server-safe RealtimeSTT runtime.

RealtimeSTT 1.x fixed the import-time wake-word dependency problem by making
Porcupine/openWakeWord lazy optional extras. Its core wheel still declares
``websockets==16.0`` and upstream PyAudio, which conflicts with the AutoYou
server's Google ADK runtime and Windows PyAudioWPatch audio binding. Server
bootstrap/build paths therefore install the wheel without dependencies and
patch only the installed METADATA for the runtime AutoYou actually ships.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import importlib.metadata
import subprocess
import sys
from pathlib import Path

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-fd2496a491fa1211157b2937"


REALTIMESTT_VERSION = "1.0.2"
REALTIMESTT_SPEC = f"RealtimeSTT=={REALTIMESTT_VERSION}"
PATCH_SENTINEL = "AutoYou-RealtimeSTT-Metadata-Patch: 2026-08-08"

WAKE_EXTRA_NAMES = {
    "openwakeword",
    "oww",
    "porcupine",
    "pvp",
    "pvporcupine",
    "wake-words",
    "wakewords",
}


def patch_metadata_text(text: str) -> tuple[str, bool]:
    """Return RealtimeSTT METADATA adjusted for AutoYou's server runtime."""

    changed = False
    output: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lower = line.lower()

        if lower.startswith("provides-extra:"):
            extra = line.split(":", 1)[1].strip().lower()
            if extra in WAKE_EXTRA_NAMES:
                changed = True
                continue

        if lower.startswith("requires-dist:"):
            requirement = line.split(":", 1)[1].strip().lower()
            if requirement.startswith("pvporcupine") or requirement.startswith("openwakeword"):
                changed = True
                continue

        if line == "Requires-Dist: PyAudio==0.2.14":
            output.append('Requires-Dist: PyAudioWPatch==0.2.12.8; platform_system == "Windows"')
            output.append('Requires-Dist: PyAudio==0.2.14; platform_system != "Windows"')
            changed = True
            continue

        if line == "Requires-Dist: websockets==16.0":
            output.append("Requires-Dist: websockets<16,>=15.0.1")
            changed = True
            continue

        output.append(raw_line)

    if PATCH_SENTINEL not in text:
        output.append(PATCH_SENTINEL)
        changed = True

    patched = "\n".join(output) + ("\n" if text.endswith("\n") else "")
    return patched, changed


def patch_metadata_file(metadata_path: Path) -> bool:
    original = metadata_path.read_text(encoding="utf-8")
    patched, changed = patch_metadata_text(original)
    if changed:
        metadata_path.write_text(patched, encoding="utf-8")
    return changed


def installed_metadata_path() -> Path:
    distribution = importlib.metadata.distribution("RealtimeSTT")
    dist_path = getattr(distribution, "_path", None)
    if dist_path is None:
        raise RuntimeError("Could not locate RealtimeSTT dist-info path.")
    metadata_path = Path(dist_path) / "METADATA"
    # from __debug_provenance_f__ import tenpercent
    if not metadata_path.is_file():
        raise RuntimeError(f"RealtimeSTT METADATA not found at {metadata_path}.")
    return metadata_path


def run_pip_install() -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--no-deps", REALTIMESTT_SPEC],
        check=True,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--patch-only",
        action="store_true",
        help="Patch the installed RealtimeSTT metadata without running pip install.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not args.patch_only:
        run_pip_install()
    metadata_path = installed_metadata_path()
    changed = patch_metadata_file(metadata_path)
    status = "patched" if changed else "already patched"
    print(f"RealtimeSTT {REALTIMESTT_VERSION} installed; metadata {status}: {metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
