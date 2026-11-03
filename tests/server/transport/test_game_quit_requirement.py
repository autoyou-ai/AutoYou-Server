# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.

"""Games must bring their own Quit (X): native clients show no close button over a game."""

import json
from pathlib import Path
import re

import pytest

from shared import game_package
from shared.game_package_catalog import GamePackages

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("source", [
    "AutoYouGame.quit()",
    "window.AutoYouGame?.quit?.()",
    "AutoYouGame . quit ( true )",
    "button.onclick = () => window.AutoYouGame.quit();",
])
def test_quit_calls_are_recognised(source):
    assert game_package.supports_quit(source)
    assert game_package.supports_quit(source.encode())


@pytest.mark.parametrize("source", ["", "quit()", "AutoYouGame.quitting()", "AutoYouGame.db.execute('quit')", "window.close()"])
def test_pages_without_a_quit_call_are_not_compatible(source):
    assert not game_package.supports_quit(source)
    with pytest.raises(ValueError, match=re.escape("AutoYouGame.quit()")):
        game_package.require_quit(source)


def _project(root, script):
    root.mkdir(parents=True)
    (root / "index.html").write_text('<!doctype html><html><head><script src="main.js"></script></head><body>'
                                     '<button id="quit">X</button></body></html>', encoding="utf-8")
    (root / "main.js").write_text(script, encoding="utf-8")
    (root / "game.json").write_text(json.dumps({"id": "synthetic-quit", "title": "Synthetic Quit", "version": "1",
                                                "entrypoint": "index.html", "files": ["index.html", "main.js"]}), encoding="utf-8")
    return root


def test_build_accepts_a_quit_call_in_a_script_file_and_refuses_a_game_without_one(tmp_path):
    good = _project(tmp_path / "good", "document.querySelector('#quit').onclick = () => AutoYouGame.quit();")
    game_package.build(good, tmp_path / "good.aygame")
    assert game_package.package_supports_quit(tmp_path / "good.aygame")
    bad = _project(tmp_path / "bad", "console.log('no way out');")
    with pytest.raises(ValueError, match=re.escape(game_package.QUIT_REQUIREMENT)):
        game_package.build(bad, tmp_path / "bad.aygame")
    assert not (tmp_path / "bad.aygame").exists()


def test_catalog_hides_stored_packages_without_quit(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "mobile_games").mkdir(parents=True)
    game_package.build(_project(tmp_path / "good", "AutoYouGame.quit()"), data / "mobile_games" / "synthetic-quit.aygame")
    # An older package from before the rule: build it with the check switched off.
    monkeypatch.setattr(game_package, "folder_supports_quit", lambda *_args: True)
    project = _project(tmp_path / "old", "console.log('legacy');")
    (project / "game.json").write_text(json.dumps({"id": "synthetic-legacy", "title": "Synthetic Legacy", "version": "1",
                                                   "entrypoint": "index.html", "files": ["index.html", "main.js"]}), encoding="utf-8")
    game_package.build(project, data / "mobile_games" / "synthetic-legacy.aygame")
    monkeypatch.undo()
    catalog = GamePackages(tmp_path / "app", data)
    assert [game["id"] for game in catalog.listing()["games"]] == ["synthetic-quit"]
    with pytest.raises(ValueError, match=re.escape("AutoYouGame.quit()")):
        catalog.resolve("synthetic-legacy")


def test_neon_horizon_has_its_own_quit_next_to_pause_and_settings():
    for page in ("autoyou_agents/game_agent/website/frontend/play.html", "assets/game/neon-horizon.html"):
        html = (ROOT / page).read_text(encoding="utf-8")
        corner = re.search(r'<div class="corner">(.*?)</div>', html, re.S).group(1)
        assert re.findall(r'<button id="([a-z-]+)"', corner) == ["pause", "settings-open", "quit"]
        assert 'aria-label="Quit game"' in corner
        assert game_package.supports_quit(html)
        assert "document.querySelector('#quit').addEventListener('click', quitGame)" in html


def test_built_in_neon_horizon_package_is_served_and_compatible(tmp_path):
    catalog = GamePackages(ROOT, tmp_path)
    package = catalog.resolve("neon-horizon")
    manifest, _ = game_package.read_manifest(package)
    assert manifest["id"] == "neon-horizon" and manifest["title"] == "Neon Horizon"
    assert game_package.package_supports_quit(package)
