# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Tools for scaffolding, validating, packaging, and handing off AutoYou offline games.

Works identically in native source runs and compiled v2 macOS/Windows builds,
honoring ``AUTOYOU_TEST_ROOT`` so tests never mutate live operator storage.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import json
import html
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext
from google.adk.tools.transfer_to_agent_tool import transfer_to_agent as _transfer_to_agent

from autoyou_agents.shared_tools.coding_handoff import (
    dispatch_agent_handoff,
    load_coding_handoff,
    store_coding_handoff,
)
from shared import game_package

_EXTERNAL_URL_RE = re.compile(r"""(?:src|href)\s*=\s*["']https?://""", re.IGNORECASE)


def _server_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _mutable_root() -> Path:
    test_root = str(os.getenv("AUTOYOU_TEST_ROOT") or "").strip()
    if test_root:
        root = Path(test_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root
    from shared.platform_runtime import get_mutable_data_dir

    root = get_mutable_data_dir("AutoYou", anchor=_server_root() / "server.py").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _game_projects_root() -> Path:
    override = str(os.getenv("AUTOYOU_GAME_PROJECTS_ROOT") or "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    elif str(os.getenv("AUTOYOU_TEST_ROOT") or "").strip():
        root = (_mutable_root() / "game_projects").resolve()
    else:
        try:
            from shared.platform_runtime import get_service_data_dir

            root = get_service_data_dir("game_projects", anchor=_server_root() / "server.py").resolve()
        except Exception:
            root = (_mutable_root() / "game_projects").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _mobile_games_library_dir() -> Path:
    root = (_mutable_root() / "mobile_games").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _normalize_game_id(raw_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(raw_id or "").strip().lower()).strip("-")
    if not slug or not game_package.GAME_ID.fullmatch(slug):
        raise ValueError(
            f"Invalid game_id '{raw_id}'. Use lowercase letters, digits, and hyphens (1-64 chars)."
        )
    return slug


def _resolve_game_project_dir(game_dir_or_id: str) -> Path:
    raw = str(game_dir_or_id or "").strip()
    if not raw:
        raise ValueError("game_dir_or_id is required")
    projects_root = _game_projects_root()
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if resolved != projects_root and projects_root not in resolved.parents:
            ws_env = str(os.getenv("AUTOYOU_WORKSPACE_ROOT") or "").strip()
            allowed_ws = Path(ws_env).resolve() if ws_env else _server_root().resolve()
            if resolved != allowed_ws and allowed_ws not in resolved.parents:
                raise ValueError(f"Game directory escapes permitted roots: {raw}")
        return resolved
    slug = _normalize_game_id(raw)
    resolved = (projects_root / slug).resolve()
    if not resolved.is_relative_to(projects_root):
        raise ValueError(f"Game directory escapes project root: {raw}")
    return resolved


def _render_index_html(title: str, description: str) -> str:
    safe_title = html.escape(title, quote=True)
    safe_desc = html.escape(description, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
  <base href="./">
  <title>{safe_title}</title>
  <link rel="stylesheet" href="./style.css">
</head>
<body>
  <div id="hud">
    <div class="hud-left">
      <div class="title">{safe_title}</div>
      <div class="stats">
        <span>Score: <strong id="score">0</strong></span>
        <span>Best: <strong id="best">0</strong></span>
        <span>Level: <strong id="level">1</strong></span>
      </div>
    </div>
    <div class="corner">
      <button id="pause" type="button" aria-label="Pause game">II</button>
      <button id="settings-open" type="button" aria-label="Game settings">&#9881;</button>
      <button id="quit" type="button" aria-label="Quit game">&times;</button>
    </div>
  </div>
  <canvas id="game-canvas" aria-label="{safe_title} game canvas"></canvas>
  <div id="overlay" class="overlay">
    <div class="card">
      <h1>{safe_title}</h1>
      <p id="overlay-text">{safe_desc or "Use arrow keys / WASD or drag/tap to play. Collect energy cores and dodge hazards."}</p>
      <div class="actions">
        <button id="start-btn" type="button" class="primary">Play</button>
      </div>
    </div>
  </div>
  <div id="settings-modal" class="overlay hidden" role="dialog" aria-label="Settings">
    <div class="card">
      <h2>Settings</h2>
      <label class="toggle">
        <input id="sound-toggle" type="checkbox" checked>
        <span>Synthesized Sound Effects</span>
      </label>
      <label class="toggle">
        <input id="touch-toggle" type="checkbox" checked>
        <span>On-screen Touch Pad</span>
      </label>
      <div class="actions">
        <button id="settings-close" type="button" class="primary">Resume</button>
      </div>
    </div>
  </div>
  <div id="touch-controls" aria-label="Touch controls">
    <button data-action="left" type="button">&#9664;</button>
    <button data-action="up" type="button">&#9650;</button>
    <button data-action="down" type="button">&#9660;</button>
    <button data-action="right" type="button">&#9654;</button>
  </div>
  <script src="./game.js"></script>
</body>
</html>
"""


def _render_style_css() -> str:
    return """* { box-sizing: border-box; user-select: none; -webkit-user-select: none; touch-action: none; }
html, body {
  margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden;
  background: radial-gradient(circle at 50% 25%, #162447 0%, #0b1021 75%);
  color: #eef3ff; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
#hud {
  position: fixed; top: 0; left: 0; right: 0; z-index: 10;
  display: flex; justify-content: space-between; align-items: center;
  padding: calc(10px + env(safe-area-inset-top)) 14px 10px;
  background: linear-gradient(180deg, rgba(8,12,24,0.85), rgba(8,12,24,0));
}
.hud-left .title { font-weight: 700; font-size: 16px; letter-spacing: 0.04em; }
.hud-left .stats { display: flex; gap: 14px; font-size: 13px; color: #b8c7e8; margin-top: 2px; }
.corner { display: flex; gap: 8px; }
.corner button {
  width: 38px; height: 38px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.2);
  background: rgba(18,28,54,0.82); color: #fff; font-size: 16px; font-weight: 700; cursor: pointer;
}
.corner button:active { transform: scale(0.95); }
#quit { background: rgba(190, 42, 62, 0.85); border-color: rgba(255,140,150,0.4); }
#game-canvas { display: block; width: 100%; height: 100%; }
.overlay {
  position: fixed; inset: 0; z-index: 20; display: flex; align-items: center; justify-content: center;
  background: rgba(6, 10, 22, 0.72); backdrop-filter: blur(4px);
}
.overlay.hidden { display: none; }
.card {
  width: min(92vw, 380px); padding: 24px; border-radius: 16px;
  background: #131c36; border: 1px solid rgba(255,255,255,0.16); text-align: center;
  box-shadow: 0 20px 50px rgba(0,0,0,0.45);
}
.card h1, .card h2 { margin: 0 0 10px; font-size: 24px; }
.card p { margin: 0 0 18px; color: #c4d2f0; line-height: 1.45; font-size: 14px; }
.toggle { display: flex; align-items: center; justify-content: space-between; margin: 12px 0; font-size: 14px; }
.actions button.primary {
  padding: 12px 28px; border-radius: 12px; border: none; cursor: pointer;
  background: linear-gradient(135deg, #38bdf8, #6366f1); color: #fff; font-weight: 700; font-size: 16px;
}
#touch-controls {
  position: fixed; bottom: calc(14px + env(safe-area-inset-bottom)); left: 50%; transform: translateX(-50%);
  z-index: 12; display: flex; gap: 10px;
}
#touch-controls.hidden { display: none; }
#touch-controls button {
  width: 54px; height: 54px; border-radius: 14px; border: 1px solid rgba(255,255,255,0.22);
  background: rgba(20, 32, 64, 0.72); color: #eef3ff; font-size: 20px; cursor: pointer;
}
"""


def _render_game_js(game_id: str, include_database: bool) -> str:
    db_snippet = """
  async function persistBest(val) {
    try { localStorage.setItem(BEST_KEY, String(val)); } catch (_) {}
    try {
      if (window.AutoYouGame && window.AutoYouGame.db && typeof window.AutoYouGame.db.execute === "function") {
        await window.AutoYouGame.db.execute(
          "INSERT INTO scores (key, value) VALUES ('best', ?) ON CONFLICT(key) DO UPDATE SET value = MAX(value, excluded.value)",
          [val]
        );
      }
    } catch (_) {}
  }

  async function hydrateBest() {
    try {
      const local = Number(localStorage.getItem(BEST_KEY) || 0);
      if (Number.isFinite(local) && local > best) best = local;
    } catch (_) {}
    try {
      if (window.AutoYouGame && window.AutoYouGame.db && typeof window.AutoYouGame.db.execute === "function") {
        const res = await window.AutoYouGame.db.execute("SELECT value FROM scores WHERE key = 'best'");
        const row = res && res.rows && res.rows[0];
        const dbBest = Number(Array.isArray(row) ? row[0] : (row && row.value));
        if (Number.isFinite(dbBest) && dbBest > best) best = dbBest;
      }
    } catch (_) {}
    bestEl.textContent = String(best);
  }
""" if include_database else """
  function persistBest(val) {
    try { localStorage.setItem(BEST_KEY, String(val)); } catch (_) {}
  }

  function hydrateBest() {
    try {
      const local = Number(localStorage.getItem(BEST_KEY) || 0);
      if (Number.isFinite(local) && local > best) best = local;
    } catch (_) {}
    bestEl.textContent = String(best);
  }
"""
    return f"""(function () {{
  "use strict";

  const GAME_ID = {json.dumps(game_id)};
  const BEST_KEY = "autoyou." + GAME_ID + ".best.v1";
  const canvas = document.getElementById("game-canvas");
  const ctx = canvas.getContext("2d");
  const scoreEl = document.getElementById("score");
  const bestEl = document.getElementById("best");
  const levelEl = document.getElementById("level");
  const overlay = document.getElementById("overlay");
  const overlayText = document.getElementById("overlay-text");
  const startBtn = document.getElementById("start-btn");
  const pauseBtn = document.getElementById("pause");
  const settingsBtn = document.getElementById("settings-open");
  const settingsModal = document.getElementById("settings-modal");
  const settingsClose = document.getElementById("settings-close");
  const quitBtn = document.getElementById("quit");
  const soundToggle = document.getElementById("sound-toggle");
  const touchToggle = document.getElementById("touch-toggle");
  const touchControls = document.getElementById("touch-controls");

  let width = 800;
  let height = 600;
  let mode = "title";
  let score = 0;
  let best = 0;
  let level = 1;
  let lastTime = 0;
  const input = {{ left: false, right: false, up: false, down: false }};
  const player = {{ x: 400, y: 300, r: 16, speed: 290 }};
  let target = {{ x: 240, y: 240, r: 12 }};
  let hazards = [];
  let audioCtx = null;
{db_snippet}
  function beep(freq, dur) {{
    if (!soundToggle.checked) return;
    try {{
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!audioCtx) audioCtx = new AC();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "triangle";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.08, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + dur);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + dur);
    }} catch (_) {{}}
  }}

  function resize() {{
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = window.innerWidth || 800;
    height = window.innerHeight || 600;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }}

  function resetInputs() {{
    input.left = input.right = input.up = input.down = false;
  }}

  function spawnTarget() {{
    const pad = 64;
    target.x = pad + Math.random() * Math.max(40, width - pad * 2);
    target.y = pad + 44 + Math.random() * Math.max(40, height - pad * 2 - 80);
  }}

  function spawnHazard() {{
    const speed = 90 + level * 22;
    const angle = Math.random() * Math.PI * 2;
    const edge = Math.floor(Math.random() * 4);
    let x = 40, y = 90;
    if (edge === 0) {{ x = Math.random() * width; y = 70; }}
    else if (edge === 1) {{ x = width - 20; y = 70 + Math.random() * (height - 120); }}
    else if (edge === 2) {{ x = Math.random() * width; y = height - 40; }}
    else {{ x = 20; y = 70 + Math.random() * (height - 120); }}
    hazards.push({{ x, y, r: 14, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed }});
  }}

  function startGame() {{
    score = 0;
    level = 1;
    player.x = width * 0.5;
    player.y = height * 0.5;
    hazards = [];
    spawnTarget();
    for (let i = 0; i < 3; i++) spawnHazard();
    scoreEl.textContent = "0";
    levelEl.textContent = "1";
    mode = "running";
    overlay.classList.add("hidden");
    settingsModal.classList.add("hidden");
    lastTime = performance.now();
  }}

  function endGame() {{
    mode = "gameover";
    resetInputs();
    beep(180, 0.25);
    if (score > best) {{
      best = score;
      bestEl.textContent = String(best);
      persistBest(best);
    }}
    overlayText.textContent = "Game Over! Score: " + score + " (Best: " + best + ").";
    startBtn.textContent = "Play Again";
    overlay.classList.remove("hidden");
  }}

  function togglePause() {{
    if (mode === "running") {{
      mode = "paused";
      resetInputs();
      overlayText.textContent = "Paused";
      startBtn.textContent = "Resume";
      overlay.classList.remove("hidden");
    }} else if (mode === "paused") {{
      mode = "running";
      overlay.classList.add("hidden");
      lastTime = performance.now();
    }}
  }}

  function handleQuit() {{
    resetInputs();
    if (window.AutoYouGame && typeof window.AutoYouGame.quit === "function") {{
      window.AutoYouGame.quit();
    }}
  }}

  function handleRemoteFrame(frame) {{
    if (!frame || typeof frame !== "object") return;
    if (frame.event === "state_reset" || frame.event === "session_end") {{
      resetInputs();
      return;
    }}
    if (frame.input_type === "button" && frame.button) {{
      const down = frame.phase === "down" || frame.pressed === true;
      if (frame.button in input) input[frame.button] = down;
    }}
  }}
  window.autoYouGameInput = handleRemoteFrame;

  if (window.AutoYouGame && window.AutoYouGame.input && typeof window.AutoYouGame.input.on === "function") {{
    window.AutoYouGame.input.on(handleRemoteFrame);
  }}

  function update(dt) {{
    if (mode !== "running") return;
    const dx = (input.right ? 1 : 0) - (input.left ? 1 : 0);
    const dy = (input.down ? 1 : 0) - (input.up ? 1 : 0);
    const len = Math.hypot(dx, dy) || 1;
    player.x = Math.max(player.r, Math.min(width - player.r, player.x + (dx / len) * player.speed * dt));
    player.y = Math.max(60 + player.r, Math.min(height - player.r, player.y + (dy / len) * player.speed * dt));

    if (Math.hypot(player.x - target.x, player.y - target.y) < player.r + target.r) {{
      score += 10 * level;
      scoreEl.textContent = String(score);
      beep(620 + level * 35, 0.1);
      const nextLevel = 1 + Math.floor(score / 60);
      if (nextLevel > level) {{
        level = nextLevel;
        levelEl.textContent = String(level);
        spawnHazard();
      }}
      spawnTarget();
    }}

    for (const h of hazards) {{
      h.x += h.vx * dt;
      h.y += h.vy * dt;
      if (h.x < h.r || h.x > width - h.r) h.vx *= -1;
      if (h.y < 60 + h.r || h.y > height - h.r) h.vy *= -1;
      if (Math.hypot(player.x - h.x, player.y - h.y) < player.r + h.r - 2) {{
        endGame();
        return;
      }}
    }}
  }}

  function draw() {{
    ctx.clearRect(0, 0, width, height);
    // Subtle grid
    ctx.strokeStyle = "rgba(120, 160, 255, 0.08)";
    ctx.lineWidth = 1;
    for (let x = 0; x < width; x += 48) {{
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
    }}
    for (let y = 0; y < height; y += 48) {{
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
    }}
    // Target core
    ctx.beginPath();
    ctx.fillStyle = "#34d399";
    ctx.arc(target.x, target.y, target.r, 0, Math.PI * 2);
    ctx.fill();
    // Hazards
    ctx.fillStyle = "#f43f5e";
    for (const h of hazards) {{
      ctx.beginPath();
      ctx.arc(h.x, h.y, h.r, 0, Math.PI * 2);
      ctx.fill();
    }}
    // Player
    ctx.beginPath();
    ctx.fillStyle = "#38bdf8";
    ctx.arc(player.x, player.y, player.r, 0, Math.PI * 2);
    ctx.fill();
  }}

  function frameLoop(now) {{
    const dt = Math.min(0.05, Math.max(0.001, (now - lastTime) / 1000));
    lastTime = now;
    update(dt);
    draw();
    requestAnimationFrame(frameLoop);
  }}

  const keyMap = {{
    ArrowLeft: "left", KeyA: "left",
    ArrowRight: "right", KeyD: "right",
    ArrowUp: "up", KeyW: "up",
    ArrowDown: "down", KeyS: "down",
  }};
  window.addEventListener("keydown", (e) => {{
    if (e.code === "Escape" || e.code === "KeyP") {{ togglePause(); return; }}
    if (keyMap[e.code]) {{ input[keyMap[e.code]] = true; e.preventDefault(); }}
  }});
  window.addEventListener("keyup", (e) => {{
    if (keyMap[e.code]) {{ input[keyMap[e.code]] = false; }}
  }});
  window.addEventListener("blur", resetInputs);
  document.addEventListener("visibilitychange", () => {{
    if (document.hidden && mode === "running") togglePause();
  }});

  for (const btn of touchControls.querySelectorAll("button[data-action]")) {{
    const act = btn.dataset.action;
    btn.addEventListener("pointerdown", (e) => {{ e.preventDefault(); input[act] = true; }});
    btn.addEventListener("pointerup", () => {{ input[act] = false; }});
    btn.addEventListener("pointercancel", () => {{ input[act] = false; }});
    btn.addEventListener("pointerleave", () => {{ input[act] = false; }});
  }}

  startBtn.addEventListener("click", () => {{
    if (mode === "paused") togglePause();
    else startGame();
  }});
  pauseBtn.addEventListener("click", togglePause);
  settingsBtn.addEventListener("click", () => {{
    if (mode === "running") mode = "paused";
    resetInputs();
    settingsModal.classList.remove("hidden");
  }});
  settingsClose.addEventListener("click", () => {{
    settingsModal.classList.add("hidden");
    if (mode === "paused") {{ mode = "running"; lastTime = performance.now(); }}
  }});
  touchToggle.addEventListener("change", () => {{
    touchControls.classList.toggle("hidden", !touchToggle.checked);
  }});
  quitBtn.addEventListener("click", handleQuit);

  window.addEventListener("resize", resize);
  resize();
  hydrateBest();
  requestAnimationFrame(frameLoop);
}})();
"""


def list_games_library() -> Dict[str, Any]:
    """List built-in offline games, published mobile_games packages, and editable game projects."""
    try:
        server_root = _server_root()
        mutable = _mutable_root()
        projects_root = _game_projects_root()
        mobile_dir = _mobile_games_library_dir()

        projects: List[Dict[str, Any]] = []
        if projects_root.is_dir():
            for child in sorted(projects_root.iterdir()):
                if not child.is_dir():
                    continue
                gj = child / "game.json"
                meta: Dict[str, Any] = {"id": child.name, "dir": str(child)}
                if gj.is_file():
                    try:
                        parsed = json.loads(gj.read_text(encoding="utf-8"))
                        meta["title"] = parsed.get("title", child.name)
                        meta["version"] = parsed.get("version", "1.0.0")
                        meta["files"] = parsed.get("files", [])
                    except Exception:
                        meta["manifest_error"] = "Invalid JSON in game.json"
                projects.append(meta)

        published: List[Dict[str, Any]] = []
        for candidate in sorted(mobile_dir.iterdir()) if mobile_dir.is_dir() else []:
            if candidate.is_file() and candidate.suffix.lower() in {".aygame", ".html", ".htm"}:
                published.append(
                    {
                        "name": candidate.name,
                        "path": str(candidate),
                        "size": candidate.stat().st_size,
                    }
                )

        builtin: List[Dict[str, Any]] = []
        try:
            from shared import game_catalog

            builtin = game_catalog.listing(game_catalog.catalog_dirs(server_root, mutable))
        except Exception:
            builtin = []

        return {
            "status": "success",
            "game_projects_root": str(projects_root),
            "mobile_games_dir": str(mobile_dir),
            "projects": projects,
            "published_library": published,
            "builtin_catalog": builtin,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def scaffold_offline_game(
    game_id: str,
    title: str,
    genre: str = "arcade",
    description: str = "",
    include_database: bool = False,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Create a complete, immediately playable offline AutoYou game project.

    Includes ``game.json``, ``index.html`` (with ``<base href="./">`` and an
    in-game Quit ``X`` button next to Pause and Settings calling
    ``AutoYouGame.quit()``), ``style.css``, and ``game.js`` with zero external
    CDN/network dependencies.
    """
    try:
        slug = _normalize_game_id(game_id)
        clean_title = str(title or slug.replace("-", " ").title()).strip()[:80]
        clean_desc = str(description or f"Portable offline {genre} game for AutoYou.").strip()[:300]
        project_dir = _resolve_game_project_dir(slug)
        if project_dir.exists() and not overwrite:
            return {
                "status": "error",
                "message": f"Game project '{slug}' already exists at {project_dir}. Pass overwrite=True or pick a new game_id.",
                "game_dir": str(project_dir),
            }

        project_dir.mkdir(parents=True, exist_ok=True)
        for name in ("game.json", "index.html", "style.css", "game.js"):
            if (project_dir / name).is_symlink() or not (project_dir / name).resolve().is_relative_to(project_dir):
                raise ValueError(f"Refusing to overwrite linked game asset: {name}")
        files_list = ["index.html", "style.css", "game.js"]
        descriptor: Dict[str, Any] = {
            "id": slug,
            "title": clean_title,
            "version": "1.0.0",
            "entrypoint": "index.html",
            "files": files_list,
        }
        if include_database:
            descriptor["database"] = {
                "schema_version": 1,
                "migrations": [
                    {
                        "version": 1,
                        "statements": [
                            "CREATE TABLE IF NOT EXISTS scores (key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
                        ],
                    }
                ],
            }

        (project_dir / "game.json").write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")
        (project_dir / "index.html").write_text(_render_index_html(clean_title, clean_desc), encoding="utf-8")
        (project_dir / "style.css").write_text(_render_style_css(), encoding="utf-8")
        (project_dir / "game.js").write_text(_render_game_js(slug, include_database), encoding="utf-8")

        return {
            "status": "success",
            "game_id": slug,
            "title": clean_title,
            "genre": genre,
            "game_dir": str(project_dir),
            "files": [str(project_dir / f) for f in ["game.json", *files_list]],
            "include_database": bool(include_database),
            "message": f"Scaffolded playable offline game '{clean_title}' at {project_dir}.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def validate_offline_game(game_dir_or_id: str) -> Dict[str, Any]:
    """Validate an offline AutoYou game project against all runtime & packaging requirements."""
    try:
        project_dir = _resolve_game_project_dir(game_dir_or_id)
        if not project_dir.is_dir():
            return {"status": "error", "valid": False, "message": f"Game directory not found: {project_dir}"}

        issues: List[str] = []
        warnings: List[str] = []
        game_json_path = project_dir / "game.json"
        if not game_json_path.is_file():
            issues.append("Missing game.json manifest descriptor.")
            return {"status": "error", "valid": False, "game_dir": str(project_dir), "issues": issues, "warnings": warnings}

        try:
            descriptor = json.loads(game_json_path.read_text(encoding="utf-8"))
            if not isinstance(descriptor, dict):
                raise ValueError("game.json must contain an object")
        except Exception as exc:
            issues.append(f"Invalid JSON in game.json: {exc}")
            return {"status": "error", "valid": False, "game_dir": str(project_dir), "issues": issues, "warnings": warnings}

        game_id = str(descriptor.get("id") or "")
        if not game_package.GAME_ID.fullmatch(game_id):
            issues.append(f"Invalid game id {game_id!r}; must match [a-z0-9-]{{1,64}}.")
        if not descriptor.get("title"):
            issues.append("game.json is missing 'title'.")
        if not descriptor.get("version"):
            issues.append("game.json is missing 'version'.")

        files = descriptor.get("files")
        if not isinstance(files, list) or not files:
            issues.append("game.json must declare a non-empty 'files' list.")
            files = []

        entry = str(descriptor.get("entrypoint") or "index.html")
        if entry not in files:
            issues.append(f"Entrypoint {entry!r} is not listed in game.json 'files'.")

        manifest_files = []
        seen_paths = set()
        for rel in files:
            try:
                game_package.safe_path(rel)
            except Exception as exc:
                issues.append(f"Invalid declared path {rel!r}: {exc}")
                continue
            full = project_dir / rel
            if full.is_symlink() or not full.resolve().is_relative_to(project_dir):
                issues.append(f"Declared asset is linked or outside the game project: {rel}")
                continue
            path_key = game_package.path_key(rel)
            if path_key in seen_paths:
                issues.append(f"Duplicate declared game asset: {rel}")
                continue
            seen_paths.add(path_key)
            if not full.is_file():
                issues.append(f"Declared asset missing: {rel}")
                continue
            size = full.stat().st_size
            manifest_files.append({"path": rel, "size": size})
            if rel.lower().endswith((".html", ".htm")):
                text = full.read_text(encoding="utf-8", errors="replace")
                if size > game_package.MAX_HTML:
                    issues.append(f"Entry HTML {rel} exceeds 8 MiB limit.")
                if not re.search(r"<head(?=[\s>])[^>]*>", text, re.IGNORECASE):
                    issues.append(f"{rel} is missing a <head> element.")
                if _EXTERNAL_URL_RE.search(text):
                    issues.append(f"{rel} references an external http(s) asset/CDN; offline games must bundle assets locally.")
            elif rel.lower().endswith((".js", ".mjs")):
                node = shutil.which("node")
                if node:
                    from autoyou_agents.shared_tools._subprocess_env import scrubbed_subprocess_env
                    from shared.process_lifecycle import process_spawn_kwargs
                    proc = subprocess.run([node, "--check", str(full)], capture_output=True, text=True, timeout=10,
                                          env=scrubbed_subprocess_env(), **process_spawn_kwargs(hide_window=True))
                    if proc.returncode != 0:
                        issues.append(f"JavaScript syntax error in {rel}: {(proc.stderr or proc.stdout).strip()[:240]}")

        if manifest_files and not game_package.folder_supports_quit(project_dir, manifest_files):
            issues.append(game_package.QUIT_REQUIREMENT)

        return {
            "status": "success" if not issues else "error",
            "valid": len(issues) == 0,
            "game_dir": str(project_dir),
            "game_id": game_id,
            "files_checked": len(manifest_files),
            "issues": issues,
            "warnings": warnings,
            "message": "Offline game passed all AutoYou contract checks." if not issues else f"Found {len(issues)} validation issue(s).",
        }
    except Exception as exc:
        return {"status": "error", "valid": False, "message": str(exc)}


def build_offline_game_package(
    game_dir_or_id: str,
    publish_to_library: bool = False,
) -> Dict[str, Any]:
    """Build a verified ``.aygame`` package using ``shared.game_package.build`` and optionally publish it."""
    try:
        project_dir = _resolve_game_project_dir(game_dir_or_id)
        validation = validate_offline_game(str(project_dir))
        if not validation.get("valid"):
            return {
                "status": "error",
                "message": "Cannot build .aygame package until validation issues are resolved.",
                "issues": validation.get("issues", []),
                "game_dir": str(project_dir),
            }

        game_id = str(validation.get("game_id") or project_dir.name)
        dist_dir = project_dir / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        package_path = dist_dir / f"{game_id}.aygame"

        manifest = game_package.build(project_dir, package_path)
        verified_manifest, _ = game_package.read_manifest(package_path)
        quit_ok = game_package.package_supports_quit(package_path)
        if not quit_ok:
            return {"status": "error", "message": game_package.QUIT_REQUIREMENT}

        published_path = None
        if publish_to_library:
            lib_dir = _mobile_games_library_dir()
            target = lib_dir / f"{game_id}.aygame"
            tmp = lib_dir / f".{game_id}.aygame.tmp"
            shutil.copyfile(package_path, tmp)
            os.replace(tmp, target)
            published_path = str(target)

        return {
            "status": "success",
            "game_id": verified_manifest["id"],
            "title": verified_manifest["title"],
            "version": verified_manifest["version"],
            "revision": verified_manifest["revision"],
            "package_path": str(package_path),
            "package_bytes": package_path.stat().st_size,
            "files_count": len(verified_manifest["files"]),
            "published": bool(published_path),
            "published_path": published_path,
            "message": (
                f"Built verified package {package_path.name} ({package_path.stat().st_size} bytes)"
                + (f" and published to {published_path}." if published_path else ".")
            ),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def prepare_game_coding_handoff(
    game_id: str,
    implementation_brief: str,
    constraints: str = "",
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Store a structured game-implementation handoff for ``autoyou_coding_agent``."""
    try:
        if tool_context is None:
            return {"status": "error", "message": "tool_context is required"}
        project_dir = _resolve_game_project_dir(game_id)
        if not project_dir.is_dir():
            return {
                "status": "error",
                "message": f"Scaffold the game first with scaffold_offline_game; directory does not exist: {project_dir}",
            }
        files = sorted(str(p) for p in project_dir.rglob("*") if p.is_file() and "dist" not in p.parts)
        default_constraints = (
            f"Offline AutoYou game project located at {project_dir}. "
            "Bundle all assets locally (zero external CDNs/API calls), keep <head> and <base href=\"./\">, "
            "keep the Quit (X) button next to Pause & Settings wired to AutoYouGame.quit(), keep game.json 'files' "
            "synchronized with every asset, and verify edits with verify_workspace_files."
        )
        merged_constraints = "\n".join(p for p in [default_constraints, (constraints or "").strip()] if p)
        payload: Dict[str, Any] = {
            "source_agent": "games_agent",
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "game_id": project_dir.name,
            "agent_name": project_dir.name,
            "game_dir": str(project_dir),
            "agent_dir": str(project_dir),
            "implementation_brief": str(implementation_brief or "").strip(),
            "constraints": merged_constraints,
            "scaffold_files": files,
            "frontend_requirement": "yes",
        }
        store_coding_handoff(tool_context.state, payload)
        return {
            "status": "success",
            "target_agent": "autoyou_coding_agent",
            "game_id": project_dir.name,
            "game_dir": str(project_dir),
            "scaffold_files": files,
            "message": f"Prepared coding handoff for offline game '{project_dir.name}'.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def handoff_to_coding_agent(tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Transfer or dispatch control to ``autoyou_coding_agent`` with the prepared game handoff."""
    try:
        if tool_context is None:
            return {"status": "error", "message": "tool_context is required"}
        payload = load_coding_handoff(tool_context.state, clear=False)
        if not payload:
            return {
                "status": "error",
                "message": "No prepared game coding handoff exists in session state. Call prepare_game_coding_handoff first.",
            }
        return dispatch_agent_handoff(
            target_agent="autoyou_coding_agent",
            payload=payload,
            tool_context=tool_context,
            transfer_fn=_transfer_to_agent,
        )
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
