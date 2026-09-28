# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import os
import sys

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import server


def test_login_minigame_high_score_persists_best_value(tmp_path, monkeypatch):
    db_path = tmp_path / "login_ui_state.db"
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
