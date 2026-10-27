# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.

"""The games this computer can host, and the page that plays one of them.

The library is the one the phones already download from: built-in games under
``assets/game`` and games published from Game Studio into ``mobile_games``
(a published game of the same name replaces the built-in one). Neon Horizon is
the default.

Any game in the library plays the same way. A trusted host page connects to the
local game-input stream and passes each player's input frame into the game,
which runs in a sandboxed frame with no network and no access to this
computer's sessions. A game that understands AutoYou's protocol receives the
frames through ``window.autoYouGameInput`` or the ``autoyougameinput`` event;
any other browser game receives the keys most games read (arrows, Space,
Enter, Escape) and pointer presses, so it plays without changes.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import html as html_escape
import json
import re
from pathlib import Path
from typing import Iterable

DEFAULT_GAME = "neon-horizon"
GAME_ID = re.compile(r"[a-z0-9-]{1,64}")
MAX_GAME_BYTES = 8 * 1024 * 1024
# The same policy the phones apply to a downloaded game: inline code only, no network.
FRAME_CSP_META = (b'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
                  b'script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data:; '
                  b'media-src data:; connect-src \'none\'; form-action \'none\'">')
# Served with the frame, so even opening it on its own keeps it in an opaque, sandboxed origin.
FRAME_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts allow-pointer-lock; frame-ancestors 'self'",
    "X-Frame-Options": "SAMEORIGIN",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


def catalog_dirs(app_root: Path, data_dir: Path) -> tuple[Path, Path]:
    return app_root / "assets" / "game", data_dir / "mobile_games"


def read_game(path: Path) -> bytes:
    """The game's HTML, or ValueError when it is missing, too large, not UTF-8, or has no head element."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_GAME_BYTES - len(FRAME_CSP_META):
        raise ValueError("Game is unavailable")
    raw = path.read_bytes()
    if len(raw) > MAX_GAME_BYTES - len(FRAME_CSP_META):
        raise ValueError("Game is too large")
    raw.decode("utf-8")
    if re.search(br"<head(?=[\s>])[^>]*>", raw[:4096], re.IGNORECASE) is None:
        raise ValueError("Game HTML needs a head element")
    return raw


def title_for(game_id: str) -> str:
    return game_id.replace("-", " ").title()


def catalog(dirs: Iterable[Path]) -> dict[str, Path]:
    """Every playable game by id; a later folder (published games) replaces an earlier one (built-in)."""
    found: dict[str, Path] = {}
    for folder in dirs:
        try:
            files = sorted(folder.glob("*.html"))[:64]
        except OSError:
            continue
        for path in files:
            if not GAME_ID.fullmatch(path.stem):
                continue
            try:
                read_game(path)
            except (OSError, ValueError, UnicodeError):
                continue
            found[path.stem] = path
    return found


def listing(dirs: Iterable[Path]) -> list[dict[str, object]]:
    games = catalog(dirs)
    return [{"id": game_id, "title": title_for(game_id), "default": game_id == DEFAULT_GAME}
            for game_id in sorted(games, key=lambda value: (value != DEFAULT_GAME, value))[:64]]


# Runs inside the game's frame. Frames arrive from the host page by postMessage; this window's own
# trusted key, pointer, and controller presses go back to the host page so the app can name them.
_FRAME_ADAPTER = r"""<script>
(() => {
  if (window.__autoyouGameAdapter) return;
  window.__autoyouGameAdapter = true;
  const KEY = {
    left: ['ArrowLeft', 'ArrowLeft', 37], right: ['ArrowRight', 'ArrowRight', 39],
    up: ['ArrowUp', 'ArrowUp', 38], down: ['ArrowDown', 'ArrowDown', 40],
    jump: [' ', 'Space', 32], action_a: [' ', 'Space', 32], action_b: ['Enter', 'Enter', 13],
    start: ['Enter', 'Enter', 13], select: ['Escape', 'Escape', 27],
    arrowleft: ['ArrowLeft', 'ArrowLeft', 37], arrowright: ['ArrowRight', 'ArrowRight', 39],
    arrowup: ['ArrowUp', 'ArrowUp', 38], arrowdown: ['ArrowDown', 'ArrowDown', 40],
    space: [' ', 'Space', 32], enter: ['Enter', 'Enter', 13], escape: ['Escape', 'Escape', 27],
    tab: ['Tab', 'Tab', 9], shift: ['Shift', 'ShiftLeft', 16], control: ['Control', 'ControlLeft', 17],
  };
  const keyFor = name => {
    const key = String(name || '').toLowerCase();
    if (KEY[key]) return KEY[key];
    if (/^[a-z]$/.test(key)) return [key, 'Key' + key.toUpperCase(), key.toUpperCase().charCodeAt(0)];
    if (/^[0-9]$/.test(key)) return [key, 'Digit' + key, key.charCodeAt(0)];
    return null;
  };
  const held = new Map();
  const press = (spec, down, owner) => {
    if (!spec) return;
    const id = owner + ':' + spec[1];
    if (down === held.has(id)) return;
    if (down) held.set(id, spec); else held.delete(id);
    const target = document.activeElement || document.body || document;
    target.dispatchEvent(new KeyboardEvent(down ? 'keydown' : 'keyup', {
      key: spec[0], code: spec[1], keyCode: spec[2], which: spec[2], bubbles: true, cancelable: true,
    }));
  };
  const releaseAll = prefix => {
    for (const [id, spec] of Array.from(held)) if (!prefix || id.startsWith(prefix)) press(spec, false, id.split(':')[0]);
  };
  const point = (x, y) => [Math.max(0, Math.min(1, Number(x) || 0)) * innerWidth, Math.max(0, Math.min(1, Number(y) || 0)) * innerHeight];
  const pointer = (type, x, y, id) => {
    const [cx, cy] = point(x, y);
    const target = document.elementFromPoint(cx, cy) || document.body;
    const init = {clientX: cx, clientY: cy, bubbles: true, cancelable: true, pointerId: id, pointerType: 'touch', isPrimary: true, button: 0, buttons: type === 'up' ? 0 : 1};
    target.dispatchEvent(new PointerEvent('pointer' + type, init));
    const mouse = type === 'down' ? 'mousedown' : type === 'up' ? 'mouseup' : 'mousemove';
    target.dispatchEvent(new MouseEvent(mouse, init));
    if (type === 'up') target.dispatchEvent(new MouseEvent('click', init));
  };
  const sticks = new Map();
  const touches = new Map();
  addEventListener('message', event => {
    if (event.source !== parent) return;
    const frame = event.data && event.data.autoyouGameInput;
    if (!frame || typeof frame !== 'object') return;
    dispatchEvent(new CustomEvent('autoyougameinput', {detail: frame}));
    if (typeof window.autoYouGameInput === 'function') { try { window.autoYouGameInput(frame); } catch (_) {} return; }
    const owner = String(frame.session_id || 'player');
    const kind = frame.input_type;
    if (kind === 'state_reset') { releaseAll(''); touches.clear(); sticks.clear(); return; }
    if (kind === 'session_end') { releaseAll(owner + ':'); return; }
    if (frame.event === 'game_input' && kind === 'button') {
      press(KEY[String(frame.button || '').toLowerCase()], frame.phase === 'down', owner);
    } else if (frame.event === 'game_input' && kind === 'axis' && /^left_[xy]$/.test(frame.axis)) {
      const value = Number(frame.value);
      const direction = value < -0.55 ? -1 : value > 0.55 ? 1 : 0;
      const names = frame.axis === 'left_x' ? ['left', 'right'] : ['up', 'down'];
      const id = owner + ':' + frame.axis, was = sticks.get(id) || 0;
      if (direction === was) return;
      if (was) press(KEY[names[was < 0 ? 0 : 1]], false, owner);
      if (direction) press(KEY[names[direction < 0 ? 0 : 1]], true, owner);
      sticks.set(id, direction);
    } else if (frame.event === 'remote_desktop_keyboard' && frame.action === 'key') {
      const spec = keyFor(frame.key);
      if (frame.phase === 'press') { press(spec, true, owner); press(spec, false, owner); }
      else press(spec, frame.phase === 'down', owner);
    } else if (frame.event === 'remote_desktop_keyboard' && frame.action === 'hide') {
      releaseAll(owner + ':');
    } else if (frame.event === 'game_input' && kind === 'touch' && Array.isArray(frame.points)) {
      const seen = new Set();
      for (const touch of frame.points) {
        const id = owner + ':' + touch.id;
        seen.add(id);
        pointer(touches.has(id) ? 'move' : 'down', touch.x, touch.y, Number(touch.id) || 1);
        touches.set(id, touch);
      }
      for (const [id, touch] of Array.from(touches)) {
        if (id.startsWith(owner + ':') && !seen.has(id)) { pointer('up', touch.x, touch.y, Number(touch.id) || 1); touches.delete(id); }
      }
    } else if (frame.event === 'remote_desktop_input' && kind === 'button' && frame.x != null) {
      if (frame.phase === 'down' || frame.phase === 'click') pointer('down', frame.x, frame.y, 1);
      if (frame.phase === 'up' || frame.phase === 'click') pointer('up', frame.x, frame.y, 1);
    }
  });
  const report = (device, value) => { try { parent.postMessage({autoyouLocalInput: {device, value: String(value)}}, '*'); } catch (_) {} };
  addEventListener('keydown', event => { if (event.isTrusted && !event.repeat) report('keyboard', event.key); }, true);
  addEventListener('pointerdown', event => { if (event.isTrusted) report(event.pointerType || 'mouse', 'press'); }, true);
  const pads = new Map();
  let polling = false;
  const poll = () => {
    let any = false;
    for (const pad of (navigator.getGamepads ? Array.from(navigator.getGamepads()) : [])) {
      if (!pad || !pad.connected) continue;
      any = true;
      const was = pads.get(pad.index) || [];
      pad.buttons.forEach((button, index) => { if (button.pressed && !was[index]) report('controller', index); });
      pads.set(pad.index, pad.buttons.map(button => button.pressed));
    }
    polling = any;
    if (any) requestAnimationFrame(poll);
  };
  addEventListener('gamepadconnected', () => { if (!polling) { polling = true; requestAnimationFrame(poll); } });
})();
</script>"""


def frame_html(raw: bytes) -> bytes:
    """The game as it runs inside the host page's frame: no network, and the input adapter loaded first."""
    head = re.search(br"<head(?=[\s>])[^>]*>", raw[:4096], re.IGNORECASE)
    if head is None:
        raise ValueError("Game HTML needs a head element")
    return raw[:head.end()] + FRAME_CSP_META + _FRAME_ADAPTER.encode("utf-8") + raw[head.end():]


def host_page_html(game_id: str, title: str) -> str:
    """The trusted page around one game: it connects the game-input stream and passes each frame in."""
    name = html_escape.escape(title)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} - AutoYou</title>
<style>html,body{{margin:0;height:100%;background:#05030f;overflow:hidden}}iframe{{display:block;border:0;width:100%;height:100%}}</style>
</head><body>
<iframe id="game" title="{name}" src="./frame/{html_escape.escape(game_id)}" sandbox="allow-scripts allow-pointer-lock"
        allow="gamepad; fullscreen; autoplay"></iframe>
<script>
(() => {{
  const game = document.getElementById('game');
  const gameId = {json.dumps(game_id)};
  let socket = null, timer = 0;
  // Inside the AutoYou app, name each local press for its window (WebView2 on Windows, WKWebView on macOS).
  const relay = message => {{
    try {{ window.chrome?.webview?.postMessage(message); }} catch (_) {{}}
    try {{ window.webkit?.messageHandlers?.autoyouGame?.postMessage(message); }} catch (_) {{}}
  }};
  addEventListener('message', event => {{
    if (event.source !== game.contentWindow) return;
    const local = event.data && event.data.autoyouLocalInput;
    if (local && typeof local === 'object') relay({{kind: 'game_input', device: String(local.device || ''), value: String(local.value || '')}});
  }});
  const deliver = frame => game.contentWindow?.postMessage({{autoyouGameInput: frame}}, '*');
  const retry = delay => {{ clearTimeout(timer); if (!document.hidden) timer = setTimeout(connect, delay); }};
  async function connect() {{
    if (socket || document.hidden) return;
    try {{
      const response = await fetch('./api/game/native-input/status', {{cache: 'no-store'}});
      const status = response.ok ? await response.json() : {{}};
      if (!status.available || status.engine_connected) {{ retry(3000); return; }}
      const url = new URL('./api/game/native-input', location.href);
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      url.searchParams.set('game', gameId);
      const ws = new WebSocket(url);
      socket = ws;
      ws.onmessage = event => {{
        try {{ const frame = JSON.parse(event.data); if (frame && frame.event !== 'heartbeat') deliver(frame); }} catch (_) {{}}
      }};
      ws.onclose = () => {{
        if (socket !== ws) return;
        socket = null;
        deliver({{event: 'game_input', input_type: 'state_reset', session_id: '*', all_sessions: true}});
        retry(2000);
      }};
    }} catch (_) {{ retry(3000); }}
  }}
  game.addEventListener('load', () => {{ game.focus(); connect(); }});
  document.addEventListener('visibilitychange', () => {{
    if (document.hidden) {{ clearTimeout(timer); socket?.close(); }} else connect();
  }});
}})();
</script>
</body></html>
"""
