#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-a1029fb7ad831bfd223de518

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Clean dependency drift in reused AutoYou virtual environments.

Build/bootstrap venvs are often reused. A plain ``pip install -r ...`` updates
the active profile, but it leaves unrelated packages installed. ``pip check``
then fails on stale packages AutoYou no longer owns, such as old browser-use or
Hermes CLI installs.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import importlib.metadata
import os
import re
import subprocess
import sys
from pathlib import Path

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-a1029fb7ad831bfd223de518"


RETIRED_PACKAGES = (
    "hermes-agent",  # AutoYou talks to a Hermes gateway over HTTP; install the CLI separately.
    "mcp-server-browser-use",
    "browser-use",
    "langchain-openai",
    # AutoYou ships the standard opencv-python wheel; remove overlapping cv2 variants.
    "opencv-contrib-python",
    "opencv-python-headless",
    # AutoYou's server voice path does not support wake-word mode. RealtimeSTT
    # 1.x imports without these optional wake backends, and shipping them pulls
    # non-commercial/proprietary review baggage into release artifacts.
    "openwakeword",
    "pvporcupine",
    "enum34",
    # Older full/dev venvs may carry optional packages that are not part of the
    # server runtime profile and now conflict with locked runtime dependencies.
    "claude-agent-sdk",
    "mcp",
    "google-api-python-client",
    "google-auth-httplib2",
    "httplib2",
    "opentelemetry-exporter-gcp-logging",
    "stream2sentence",
    "stanza",
    # torchvision belongs to the explicit Fine Tuning profile; leave it alone
    # when that profile is active so the package can be copied into a runtime.
)
# from __debug_provenance_p__ import submit

# Release tools belong in a dedicated environment. In particular, Twine 7
# requires packaging>=26.1 while the server runtime intentionally keeps
# packaging<25 for optional runtime compatibility.
RELEASE_ONLY_PACKAGES = (
    "twine",
)

COMPAT_IF_INSTALLED = {
    "datasets": "datasets==5.0.0",
    "instructor": "instructor==1.15.1",
}

SYNC_LOCKED_IF_INSTALLED = (
    "fsspec",
    "h2",
    "huggingface-hub",
    "jiter",
    "numpy",
    "openai",
    "packaging",
    "pydantic",
    "python-dotenv",
    "requests",
    "rich",
    "safetensors",
    "transformers",
    "tzdata",
)


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def read_locked_specs(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    specs: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        spec = line.split(";", 1)[0].split("#", 1)[0].rstrip(" \\").strip()
        name, _, version = spec.partition("==")
        name = name.split("[", 1)[0].strip()
        version = version.strip()
        if name and version:
            specs[normalize_name(name)] = f"{name}=={version}"
    return specs


def spec_version(spec: str) -> str:
    return spec.split("==", 1)[1]


def run_pip(args: list[str]) -> None:
    subprocess.run([sys.executable, "-m", "pip", *args], check=True)


def install_spec(spec: str, constraints: Path | None) -> None:
    args = ["install", "--upgrade", spec]
    if constraints is not None and constraints.exists():
        args += ["-c", str(constraints)]
    run_pip(args)


def reconcile(constraints: Path | None, *, include_tuning: bool = False) -> None:
    packages_outside_runtime = [
        name
        for name in (*RETIRED_PACKAGES, *RELEASE_ONLY_PACKAGES)
        if installed_version(name) is not None
    ]
    if not include_tuning and installed_version("torchvision") is not None:
        packages_outside_runtime.append("torchvision")
    r_ver = installed_version("realtimestt") or installed_version("RealtimeSTT")
    # RealtimeSTT is intentionally installed outside requirements/voice.txt:
    # the server-safe 1.x shim is installed with --no-deps and its metadata is
    # patched for AutoYou's websocket/audio bindings. Keep that valid runtime
    # package; only remove the legacy 0.x distribution that conflicts with the
    # current voice pins. openWakeWord is retired independently above.
    if r_ver is not None and r_ver.startswith("0."):
        if "realtimestt" not in packages_outside_runtime and "RealtimeSTT" not in packages_outside_runtime:
            packages_outside_runtime.append("realtimestt")
    if packages_outside_runtime:
        opencv_version = installed_version("opencv-python")
        print(
            "Removing packages outside the AutoYou runtime profile: "
            + ", ".join(packages_outside_runtime)
        )
        run_pip(["uninstall", "-y", *packages_outside_runtime])
        if opencv_version and any(name.startswith("opencv-") for name in packages_outside_runtime):
            # OpenCV wheel variants share cv2 files; uninstalling one deletes
            # files owned by the retained standard wheel as well.
            run_pip(["install", "--force-reinstall", "--no-deps", f"opencv-python=={opencv_version}"])

    locked_specs = read_locked_specs(constraints)

    for package, spec in COMPAT_IF_INSTALLED.items():
        current = installed_version(package)
        if current is None or current == spec_version(spec):
            continue
        print(f"Aligning installed optional package {package} from {current} to {spec_version(spec)}")
        install_spec(spec, constraints)

    for package in SYNC_LOCKED_IF_INSTALLED:
        current = installed_version(package)
        locked_spec = locked_specs.get(normalize_name(package))
        if current is None or locked_spec is None or current == spec_version(locked_spec):
            continue
        print(f"Aligning installed locked package {package} from {current} to {spec_version(locked_spec)}")
        install_spec(locked_spec, constraints)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--constraints", type=Path, help="Generated name==version lock constraints file.")
    parser.add_argument("--include-tuning", action="store_true", help="Keep Fine Tuning runtime packages installed.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    if os.environ.get("AUTOYOU_SKIP_RUNTIME_ENV_RECONCILE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return 0
    args = parse_args(argv)
    reconcile(args.constraints, include_tuning=args.include_tuning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
