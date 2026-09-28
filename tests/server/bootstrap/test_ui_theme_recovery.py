# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

from shared import ui_theme
from shared.secure_storage import SecureStorageError


def test_locked_ui_preferences_fall_back_without_rewriting(monkeypatch, tmp_path):
    calls = []

    def locked_preferences(*_args, **_kwargs):
        calls.append(True)
        raise SecureStorageError("Protected file requires Secure Professional Maximus storage")

    monkeypatch.setattr(ui_theme, "load_secure_json", locked_preferences)

    assert ui_theme.load_ui_preferences(app_name="AutoYou", anchor=tmp_path) == {
        "theme": ui_theme.DEFAULT_UI_THEME
    }
    assert calls == [True]
