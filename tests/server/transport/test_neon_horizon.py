"""Exercise the shipped game in Node with synthetic input and isolated storage."""

import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
PAGES = (
    "autoyou_agents/game_agent/website/frontend/play.html",
    "assets/game/neon-horizon.html",
)


@pytest.mark.parametrize("page", PAGES)
def test_horizon_gameplay_controls_motion_and_records(page, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the gameplay tests")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("neon_horizon_checks.cjs")), str(ROOT / page)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_download_matches_canonical_game_and_remains_offline():
    spec = importlib.util.spec_from_file_location("horizon_builder", ROOT / "scripts/build_neon_horizon.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = (ROOT / PAGES[0]).read_text(encoding="utf-8")
    mobile = (ROOT / PAGES[1]).read_text(encoding="utf-8")
    assert mobile == builder.render_mobile(source)
    assert len(mobile.encode("utf-8")) < 511 * 1024
    assert "connect-src 'none'" in mobile
    assert "<body data-offline>" in mobile
    assert '<script src=' not in mobile
