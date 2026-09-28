# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-652076696120284254432061-6cbe8c7a7bf49bf7d16254b7

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-652076696120284254432061-6cbe8c7a7bf49bf7d16254b7"


import html
import sys
from pathlib import Path


def iter_admin_ui_asset_candidates(
    filename: str,
    *,
    resources_root: Path,
    module_file: str,
) -> tuple[Path, ...]:
    """Return source and packaged locations for a bundled admin UI asset."""
    safe_filename = Path(filename).name
    module_dir = Path(module_file).resolve().parent
    candidates: list[Path] = [
        resources_root / "assets" / safe_filename,
        module_dir / "assets" / safe_filename,
    ]

    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                executable_dir / "assets" / safe_filename,
                executable_dir.parent / "Resources" / "assets" / safe_filename,
            ]
        )
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass).resolve() / "assets" / safe_filename)

    return tuple(candidates)


def resolve_admin_ui_asset(
    filename: str,
    *,
    resources_root: Path,
    module_file: str,
) -> Path:
    """Resolve a bundled admin UI asset for source and frozen builds."""
    candidates = iter_admin_ui_asset_candidates(
        filename,
        resources_root=resources_root,
        module_file=module_file,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def admin_ui_assets_present(*, resources_root: Path, module_file: str) -> bool:
    return (
        resolve_admin_ui_asset(
            "admin-ui.css",
            resources_root=resources_root,
            module_file=module_file,
        ).exists()
        and resolve_admin_ui_asset(
            "admin-ui.js",
            resources_root=resources_root,
            module_file=module_file,
        ).exists()
    )


def build_admin_ui_shell_html(*, page_title: str, current_theme: str) -> str:
    escaped_title = html.escape(str(page_title or "AutoYou Admin"))
    escaped_theme = html.escape(str(current_theme or "dark"), quote=True)
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="{escaped_theme}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <link rel="icon" type="image/png" href="/assets/logo.png">
  <link rel="stylesheet" href="/assets/admin-ui.css">
</head>
<body>
  <div id="autoyou-admin-root">
    <main style="min-height: 100vh; display: grid; place-items: center; padding: 32px; font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #0f172a; background: #f8fafc;">
      <p style="margin: 0; font-size: 16px; line-height: 1.5; text-align: center;">Loading AutoYou server admin...</p>
    </main>
  </div>
  <script src="/assets/admin-ui.js"></script>
</body>
</html>
"""
