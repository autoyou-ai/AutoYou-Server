# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Synthetic offline game packages for the server tests, built inside this repository.

The same two-version game the client repositories keep as binary fixtures,
generated here so the server tests never read outside this repository: v2 adds
a SQLite migration, and its music, artwork and level differ from v1. The
music is long enough that each package is larger than 1 MB, so range reads span
several chunks.
"""

from __future__ import annotations

import array
import json
import math
import os
import sqlite3
import tempfile
import wave
from functools import lru_cache
from pathlib import Path

_STYLE = (
    "body{font:18px system-ui;padding:12px;color:#173528;background:#edf6ef}"
    "h1{font-size:26px}button{display:block;padding:12px;margin:8px 0}"
)
_SCRIPT = (
    "(async()=>{const level=await(await fetch('levels.json')).json();"
    "document.querySelector('#level').textContent='Offline level: '+level.name;"
    "const r=await AutoYouGame.db.execute('SELECT steps FROM progress WHERE id=?',[1]);"
    "document.querySelector('#progress').textContent='SQLite steps: '+r.rows[0][0];})();"
)


def _write_music(path: Path, frequency: float) -> None:
    rate, seconds = 16_000, 40
    samples = array.array("h", (int(6000 * math.sin(2 * math.pi * frequency * i / rate)) for i in range(rate * seconds)))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())


def _write_seed(path: Path, version: int) -> None:
    columns = "id INTEGER PRIMARY KEY, steps INTEGER NOT NULL"
    if version >= 2:
        columns += ", power TEXT NOT NULL DEFAULT 'new'"
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE progress ({columns})")
        db.execute("INSERT INTO progress (id, steps) VALUES (1, 0)")
        db.execute(f"PRAGMA user_version = {version}")
    # Leave no journal beside the seed.
    db.close()


def write_project(root: Path, version: int) -> Path:
    """One game.json project for version 1 or 2."""
    project = root / f"project-v{version}"
    (project / "assets").mkdir(parents=True, exist_ok=True)
    migrations = [] if version == 1 else [
        {"version": 2, "statements": ["ALTER TABLE progress ADD COLUMN power TEXT NOT NULL DEFAULT 'new'"]}
    ]
    manifest = {
        "id": "synthetic-package",
        "title": "Synthetic Package",
        "version": str(version),
        "entrypoint": "index.html",
        "files": ["index.html", "style.css", "main.js", "levels.json", "assets/background.svg",
                  "assets/music.wav", "seed.sqlite"],
        "database": {"seed": "seed.sqlite", "schema_version": version, "migrations": migrations},
    }
    (project / "game.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (project / "index.html").write_text(
        '<!doctype html><html><head><link rel="stylesheet" href="style.css"><script src="main.js" defer></script>'
        f"</head><body><h1>Synthetic package v{version}</h1>"
        '<img src="assets/background.svg" width="48" alt="Offline artwork"><p id="level"></p><p id="progress"></p>'
        '<audio id="music" src="assets/music.wav" preload="auto"></audio></body></html>',
        encoding="utf-8",
    )
    (project / "style.css").write_text(_STYLE, encoding="utf-8")
    (project / "main.js").write_text(_SCRIPT, encoding="utf-8")
    (project / "levels.json").write_text(json.dumps({"name": f"Synthetic valley {version}"}), encoding="utf-8")
    colour = "#27623e" if version == 1 else "#3269ad"
    (project / "assets" / "background.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect width="80" height="80" fill="{colour}"/></svg>',
        encoding="utf-8",
    )
    _write_music(project / "assets" / "music.wav", 220.0 if version == 1 else 330.0)
    _write_seed(project / "seed.sqlite", version)
    return project


@lru_cache(maxsize=1)
def offline_game_packages() -> Path:
    """A folder holding package-v1.aygame and package-v2.aygame, built once per test run."""
    from shared import game_package

    base = Path(os.environ.get("AUTOYOU_TEST_ROOT") or tempfile.gettempdir())
    root = Path(tempfile.mkdtemp(prefix="offline-games-", dir=base))
    for version in (1, 2):
        game_package.build(write_project(root, version), root / f"package-v{version}.aygame")
    return root
