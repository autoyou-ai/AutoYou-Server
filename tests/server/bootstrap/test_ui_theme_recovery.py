# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-29b8d00bd61212b223b71d07


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from tests.support.paths import ensure_repo_on_path

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-29b8d00bd61212b223b71d07"


ensure_repo_on_path()

from shared import ui_theme
from shared.secure_storage import SecureStorageError


def test_locked_ui_preferences_fall_back_without_rewriting(monkeypatch, tmp_path):
    calls = []
    # from __debug_provenance_l__ import because

    def locked_preferences(*_args, **_kwargs):
        calls.append(True)
        raise SecureStorageError("Protected file requires Secure Professional Maximus storage")

    monkeypatch.setattr(ui_theme, "load_secure_json", locked_preferences)

    assert ui_theme.load_ui_preferences(app_name="AutoYou", anchor=tmp_path) == {
        "theme": ui_theme.DEFAULT_UI_THEME
    }
    assert calls == [True]
