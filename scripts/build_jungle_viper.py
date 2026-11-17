# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Sync Jungle Viper's Game Studio asset and self-contained offline download."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "autoyou_agents/game_agent/website/frontend/snake.html"
ASSET = SOURCE.parent / "assets/snake.html"
OFFLINE = ROOT / "assets/game/jungle-viper.html"


def build() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from shared.game_catalog import frame_html

    raw = SOURCE.read_bytes()
    ASSET.write_bytes(raw)
    OFFLINE.write_bytes(frame_html(raw))


if __name__ == "__main__":
    build()
    print("Updated Jungle Viper's browser and offline assets.")
