#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Locally record acceptance of the current AutoYou build notices."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
LICENSE_PATH = REPO_ROOT / "LICENSE"
NOTICES_PATH = REPO_ROOT / "THIRD-PARTY-NOTICES.md"
ACK_FILENAME = "BUILD_LICENSE_ACKNOWLEDGEMENT.json"
ACCEPT_PHRASE = "I AGREE"


def acknowledgement_path() -> Path:
    test_root = os.environ.get("AUTOYOU_TEST_ROOT", "").strip()
    if test_root:
        return Path(test_root).expanduser().resolve() / "AutoYou" / ACK_FILENAME
    if os.name == "nt":
        data_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return data_root / "AutoYou" / ACK_FILENAME
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/AutoYou" / ACK_FILENAME
    config_root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_root / "autoyou" / ACK_FILENAME


def current_notices() -> dict[str, str]:
    result = {}
    for label, path in (("license", LICENSE_PATH), ("third_party_notices", NOTICES_PATH)):
        content = path.read_bytes()
        result[f"{label}_sha256"] = hashlib.sha256(content).hexdigest()
    match = re.search(r"^Version\s+(.+?)\s*$", LICENSE_PATH.read_text(encoding="utf-8"), re.MULTILINE)
    result["license_version"] = match.group(1) if match else "unknown"
    return result


def read_acknowledgement(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def is_current_acknowledgement(payload: dict[str, Any] | None, notices: dict[str, str]) -> bool:
    return bool(payload) and all(payload.get(key) == value for key, value in notices.items())


def save_acknowledgement(path: Path, notices: dict[str, str], method: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **notices,
        "accepted_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "method": method,
    }
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f"{path.name}.", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acknowledge AutoYou's current local-build notices.")
    parser.add_argument(
        "--accept-terms",
        action="store_true",
        help="record acknowledgment without an interactive prompt (for an explicit local CLI build)",
    )
    args = parser.parse_args(argv)

    try:
        notices = current_notices()
    except OSError as exc:
        print(f"Build acknowledgment unavailable: {exc}", file=sys.stderr)
        return 1

    path = acknowledgement_path()
    if is_current_acknowledgement(read_acknowledgement(path), notices):
        print(f"Current AutoYou build notices already acknowledged locally (license {notices['license_version']}).")
        return 0

    print(f"AutoYou Source-Available License {notices['license_version']}")
    print(f"Review: {LICENSE_PATH}")
    print(f"Third-party notices: {NOTICES_PATH}")
    print("Private Personal Use builds only; this acknowledgment grants no redistribution or commercial rights.")
    print("This confirmation is saved on this device and does not send an email or contact AutoYou.")

    if args.accept_terms:
        method = "command_line"
    else:
        if not sys.stdin.isatty():
            print("Build requires acknowledgment. Review the files above and rerun with --accept-terms.", file=sys.stderr)
            return 2
        print("\n" + LICENSE_PATH.read_text(encoding="utf-8"))
        print("\n" + NOTICES_PATH.read_text(encoding="utf-8"))
        try:
            response = input(f'Type "{ACCEPT_PHRASE}" to continue: ').strip().upper()
        except EOFError:
            response = ""
        if response != ACCEPT_PHRASE:
            print("Build canceled; no acknowledgment was saved.", file=sys.stderr)
            return 2
        method = "interactive"

    try:
        save_acknowledgement(path, notices, method)
    except OSError as exc:
        print(f"Could not save the local build acknowledgment at {path}: {exc}", file=sys.stderr)
        return 1

    print(f"ACKNOWLEDGEMENT saved locally at {path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
