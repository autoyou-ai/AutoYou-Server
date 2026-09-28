# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from shared.platform_runtime import get_config_dir
from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json

DEFAULT_UI_THEME = "dark"
VALID_UI_THEMES = {"dark", "light"}


def normalize_ui_theme(value: Any, default: str = DEFAULT_UI_THEME) -> str:
    normalized_default = str(default or DEFAULT_UI_THEME).strip().lower()
    if normalized_default not in VALID_UI_THEMES:
        normalized_default = DEFAULT_UI_THEME
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_UI_THEMES else normalized_default


def ui_preferences_path(
    *,
    app_name: str = "AutoYou",
    anchor: str | Path,
) -> Path:
    return get_config_dir(app_name, anchor=anchor) / "ui_preferences.json"


def load_ui_preferences(
    *,
    app_name: str = "AutoYou",
    anchor: str | Path,
) -> Dict[str, Any]:
    path = ui_preferences_path(app_name=app_name, anchor=anchor)
    try:
        payload = load_secure_json(path, default={})
        if isinstance(payload, dict):
            return {
                "theme": normalize_ui_theme(payload.get("theme")),
            }
    except FileNotFoundError:
        pass
    except SecureStorageError:
        # The login/recovery surface must remain available when a prior Secure
        # Professional Maximus session sealed this non-critical preference.
        # Do not rewrite the ciphertext here; use the visual default until the
        # original storage boundary is unlocked.
        pass
    except Exception:
        pass
    return {"theme": DEFAULT_UI_THEME}


def save_ui_preferences(
    preferences: Dict[str, Any],
    *,
    app_name: str = "AutoYou",
    anchor: str | Path,
) -> Dict[str, Any]:
    normalized = {
        "theme": normalize_ui_theme((preferences or {}).get("theme")),
    }
    path = ui_preferences_path(app_name=app_name, anchor=anchor)
    save_secure_json(path, normalized)
    return normalized


def get_ui_theme(
    *,
    app_name: str = "AutoYou",
    anchor: str | Path,
    default: str = DEFAULT_UI_THEME,
) -> str:
    stored = load_ui_preferences(app_name=app_name, anchor=anchor)
    return normalize_ui_theme(stored.get("theme"), default=default)


def set_ui_theme(
    theme: Any,
    *,
    app_name: str = "AutoYou",
    anchor: str | Path,
) -> str:
    saved = save_ui_preferences({"theme": theme}, app_name=app_name, anchor=anchor)
    return str(saved.get("theme") or DEFAULT_UI_THEME)
