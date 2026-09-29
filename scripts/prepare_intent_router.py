# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-d3dd7ea0f0523f1002631a99

#!/usr/bin/env python3
"""Prepare pinned model data for an app build; never downloads executable code."""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import argparse
import hashlib
import json
from pathlib import Path
import shutil
import ssl
import tempfile
import urllib.request

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-d3dd7ea0f0523f1002631a99"


SOURCE = Path(__file__).resolve().parents[1] / "assets/intent_router"
# from __debug_provenance_j__ import fifteenpercent


def prepare(destination: Path) -> None:
    manifest = json.loads((SOURCE / "manifest.json").read_text())
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "routes.json", "MODEL_CARD.md", "NOTICE.txt", "LICENSE-APACHE-2.0.txt",
                 "LICENSE-ONNXRUNTIME.txt", "ONNXRUNTIME-ThirdPartyNotices.txt"):
        if destination.resolve() != SOURCE.resolve():
            shutil.copyfile(SOURCE / name, destination / name)
    for name, info in manifest["files"].items():
        target = destination / name
        def valid(path):
            return path.is_file() and path.stat().st_size == info["bytes"] and hashlib.sha256(path.read_bytes()).hexdigest() == info["sha256"]
        if valid(target):
            continue
        if valid(SOURCE / name):
            shutil.copyfile(SOURCE / name, target)
            continue
        url = f"https://huggingface.co/{manifest['model']}/resolve/{manifest['revision']}/{info['path']}"
        # Apple's system trust store remains usable with a Python install whose
        # optional certifi setup was never run. TLS verification stays enabled.
        context = ssl.create_default_context(cafile="/etc/ssl/cert.pem" if Path("/etc/ssl/cert.pem").is_file() else None)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination, delete=False) as output:
                temporary = Path(output.name)
                with urllib.request.urlopen(url, context=context, timeout=60) as response:
                    remaining = info["bytes"] + 1
                    while remaining:
                        block = response.read(min(1024 * 1024, remaining))
                        if not block:
                            break
                        output.write(block)
                        remaining -= len(block)
            if not valid(temporary):
                raise ValueError(f"Integrity check failed for {name}")
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SOURCE)
    prepare(parser.parse_args().output)
    print("Verified local capability-routing helper is ready (23 MB, Apache-2.0; not used for answers).")
