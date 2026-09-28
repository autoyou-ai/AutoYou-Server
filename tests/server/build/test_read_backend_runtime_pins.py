# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


from tests.support.paths import REPO_ROOT
SCRIPT_PATH = REPO_ROOT / "scripts" / "read_backend_runtime_pins.py"


def run_pin_reader(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_read_backend_runtime_pins_follows_recursive_includes(tmp_path: Path) -> None:
    nested_dir = tmp_path / "requirements"
    nested_dir.mkdir()

    (tmp_path / "requirements.txt").write_text("-r requirements/full.txt\n", encoding="utf-8")
    (nested_dir / "full.txt").write_text(
        "\n".join(
            [
                "# Product runtime bundle",
                "-r base.txt",
                "uvicorn[standard]>=0.34.0,<1.0.0",
            ]
        ),
        encoding="utf-8",
    )
    (nested_dir / "base.txt").write_text(
        "\n".join(
            [
                "google-adk==2.2.0",
                "google-genai==2.8.0",
                "google-cloud-aiplatform==1.156.0",
                "fastapi==0.136.3",
            ]
        ),
        encoding="utf-8",
    )

    result = run_pin_reader(
        "--requirements-file",
        str(tmp_path / "requirements.txt"),
        "--packages",
        "google-adk",
        "google-genai",
        "google-cloud-aiplatform",
        "fastapi",
        "--format",
        "json",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "fastapi": "0.136.3",
        "google-adk": "2.2.0",
        "google-cloud-aiplatform": "1.156.0",
        "google-genai": "2.8.0",
    }


def test_read_backend_runtime_pins_reports_missing_exact_pin(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "\n".join(
            [
                "google-adk>=2.2.0",
                "google-genai==2.8.0",
                "google-cloud-aiplatform==1.156.0",
                "fastapi==0.136.3",
            ]
        ),
        encoding="utf-8",
    )

    result = run_pin_reader(
        "--requirements-file",
        str(tmp_path / "requirements.txt"),
        "--packages",
        "google-adk",
        "google-genai",
        "google-cloud-aiplatform",
        "fastapi",
        "--format",
        "spec",
    )

    assert result.returncode == 1
    assert "Missing exact '==' runtime pins for: google-adk" in result.stderr
