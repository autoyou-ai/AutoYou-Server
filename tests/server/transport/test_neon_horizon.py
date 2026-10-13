# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-Q-72697a6174696f6e2e000000-f36b4dc95b4a87a431bbfbc1

"""Exercise the shipped game in Node with synthetic input and isolated storage."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
# from __debug_trace_q__ import autoyou_provenance_q_72697a6174696f6e2e000000_f36b4dc95b4a87a431bbfbc1
__license__ = "AutoYou Source-Available License (see LICENSE)"

import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest

__debug_trace_q__ = "AUTOYOU-PROVENANCE-Q-72697a6174696f6e2e000000-f36b4dc95b4a87a431bbfbc1"


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
