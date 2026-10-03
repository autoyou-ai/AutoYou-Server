# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ACK_SCRIPT = REPO_ROOT / "scripts" / "acknowledge_local_build.py"


def run_acknowledgement(test_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["AUTOYOU_TEST_ROOT"] = str(test_root)
    return subprocess.run(
        [sys.executable, str(ACK_SCRIPT), *args],
        cwd=REPO_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def test_local_build_acknowledgement_is_cached_and_invalidated_by_current_notices(tmp_path):
    missing = run_acknowledgement(tmp_path)
    assert missing.returncode == 2
    assert not (tmp_path / "AutoYou" / "BUILD_LICENSE_ACKNOWLEDGEMENT.json").exists()

    result = run_acknowledgement(tmp_path, "--accept-terms")
    assert result.returncode == 0
    assert "does not send an email or contact AutoYou" in result.stdout

    receipt_path = tmp_path / "AutoYou" / "BUILD_LICENSE_ACKNOWLEDGEMENT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["method"] == "command_line"
    assert receipt["license_sha256"]
    assert receipt["third_party_notices_sha256"]
    assert "email" not in receipt

    cached = run_acknowledgement(tmp_path)
    assert cached.returncode == 0
    assert "already acknowledged locally" in cached.stdout

    receipt["license_sha256"] = "stale-license-hash"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    stale = run_acknowledgement(tmp_path)
    assert stale.returncode == 2
    assert "rerun with --accept-terms" in stale.stderr

    refreshed = run_acknowledgement(tmp_path, "--accept-terms")
    assert refreshed.returncode == 0
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["license_sha256"] != "stale-license-hash"
