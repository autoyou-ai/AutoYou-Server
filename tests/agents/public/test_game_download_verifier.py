"""Execute the production bounded download verifier with real local WebCrypto."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_game_download_verifier(tmp_path, record_property):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    fixture = Path(__file__).with_name("fixtures") / "game_downloads.test.cjs"
    result = subprocess.run([node, "--test", str(fixture)], cwd=tmp_path, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    (tmp_path / "game-download-verifier.tap").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# tests 9" in result.stdout and "# pass 9" in result.stdout and "# fail 0" in result.stdout
    record_property("node_hermetic_cases", 9)
