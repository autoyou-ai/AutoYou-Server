# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-534454206164647265737320-0646ebeeb61afaee700848b6

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Generate the Windows server (MSIX) tile assets from the circular brand mark.

The AutoYou *server* identity is the circular "box-in-circle" mark
(``assets/logo.png``); the *client* identity is the bare square "box"
(``assets/logo-alt.png``). The packaged Windows host
(``servers/windows/AutoYouWindowsHost``) is a server, so its Start menu /
taskbar / Store / splash tiles all use the circular mark.

The manifest declares ``BackgroundColor="transparent"``, so every tile is
written as RGBA with the circular mark centred on a transparent canvas. Square
tiles fill (or near-fill) the canvas; the wide tile and splash screen centre
the mark with breathing room.

Run from anywhere:

    python scripts/generate_windows_server_tiles.py
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-534454206164647265737320-0646ebeeb61afaee700848b6"


from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOGO = REPO_ROOT / "assets" / "logo.png"  # circular = server identity
ASSETS_DIR = REPO_ROOT / "servers" / "windows" / "AutoYouWindowsHost" / "Assets"

# (filename, width, height, logo_fraction_of_shorter_side)
#
# Padding follows Microsoft's tile/icon guidance
# (learn.microsoft.com/windows/apps/design/iconography/app-icon-construction):
# the medium/wide Start tiles keep the mark within ~66% width / ~50% height with
# transparent padding, while the small app-list/taskbar icons (44px, 24px
# unplated) stay fuller so the mark remains legible at tiny sizes.
TILES = [
    ("StoreLogo.png", 50, 50, 0.85),
    ("Square44x44Logo.scale-200.png", 88, 88, 0.90),
    ("Square44x44Logo.targetsize-24_altform-unplated.png", 24, 24, 0.92),
    ("Square150x150Logo.scale-200.png", 300, 300, 0.66),
    ("LockScreenLogo.scale-200.png", 48, 48, 0.90),
    ("Wide310x150Logo.scale-200.png", 620, 300, 0.50),
    ("SplashScreen.scale-200.png", 1240, 600, 0.55),
]


def make_tile(logo: Image.Image, width: int, height: int, fraction: float, out: Path) -> None:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    target = max(1, round(min(width, height) * fraction))
    resized = logo.resize((target, target), Image.LANCZOS)
    x = (width - target) // 2
    y = (height - target) // 2
    canvas.alpha_composite(resized, (x, y))
    canvas.save(out)
    print(f"wrote {out.relative_to(REPO_ROOT)} ({width}x{height})")


def main() -> None:
    if not SOURCE_LOGO.exists():
        raise SystemExit(f"Missing source brand mark: {SOURCE_LOGO}")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    logo = Image.open(SOURCE_LOGO).convert("RGBA")
    for filename, width, height, fraction in TILES:
        make_tile(logo, width, height, fraction, ASSETS_DIR / filename)


if __name__ == "__main__":
    main()
