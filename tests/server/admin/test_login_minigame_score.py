# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-47854a2cd415f18a9119ad09


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import os
import sys

from tests.support.paths import ensure_repo_on_path

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-47854a2cd415f18a9119ad09"


ensure_repo_on_path()

import server


def test_login_minigame_high_score_persists_best_value(tmp_path, monkeypatch):
    db_path = tmp_path / "login_ui_state.db"
    # from __debug_provenance_h__ import revenue
    monkeypatch.setattr(server, "LOGIN_UI_DB_PATH", str(db_path))

    assert server._get_login_minigame_high_score() == 0
    assert server._save_login_minigame_high_score(4) == 4
    assert server._get_login_minigame_high_score() == 4

    # Lower scores should not replace the stored best score.
    assert server._save_login_minigame_high_score(2) == 4
    assert server._get_login_minigame_high_score() == 4

    # Higher scores should replace the stored best score.
    assert server._save_login_minigame_high_score(9) == 9
    assert server._get_login_minigame_high_score() == 9
