# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-S-000000000000000000000000-4b1a7244afa42397d2600afd

"""Game Studio publishes bounded HTML into test-scoped server storage."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from autoyou_agents.game_agent.website.backend import app as game_studio

__debug_trace_s__ = "AUTOYOU-PROVENANCE-S-000000000000000000000000-4b1a7244afa42397d2600afd"


@pytest.mark.parametrize("field", ["code", "totp_code"])
@pytest.mark.parametrize("view_mode", ["open", "totp"])
@pytest.mark.parametrize("assigned_profile", [False, True])
def test_game_publishing_requires_otp_even_with_public_browsing(tmp_path, monkeypatch, field, view_mode, assigned_profile):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    server = SimpleNamespace(
        __file__=str(tmp_path / "server.py"),
        get_mutable_data_dir=lambda *_args, **_kwargs: tmp_path,
        STATE=SimpleNamespace(config={}),
        _default_config=lambda: {},
        _is_logged_in=lambda _request: False,
        agent_has_assigned_2fa_profile=lambda _agent: assigned_profile,
        verify_agent_assigned_2fa=lambda agent, code: agent == "game_agent" and code == "654321",
        _get_pairing_totp_secret=lambda _cfg: "synthetic-test-secret",
        _verify_totp_secret=lambda secret, code: not assigned_profile and secret == "synthetic-test-secret" and code == "654321",
    )
    monkeypatch.setattr(game_studio._smc, "_runtime_server", lambda: server)
    monkeypatch.setattr(game_studio._smc, "_get_agent_security_settings", lambda _agent: {"auth_mode": view_mode, "session_ttl_days": 1})
    monkeypatch.setattr(game_studio._smc, "_totp_capabilities", lambda: {"totp_configured": not assigned_profile})
    monkeypatch.setattr(game_studio._smc, "_create_agent_chat_session", lambda *_args: "synthetic-game-session")
    monkeypatch.setattr(game_studio._smc, "_agent_chat_session_is_valid", lambda agent, token: agent == "game_agent" and token == "synthetic-game-session")
    with TestClient(game_studio.app) as client:
        route = "/api/game/publish-auth"
        assert client.get(route + "/status").json() == {"success": True, "required": True, "authenticated": False}
        html = b"<html><head></head><body>Synthetic game<button onclick=\"AutoYouGame.quit()\">Quit</button></body></html>"
        assert client.post("/api/game/publish/synthetic-publishing", content=html).status_code == 401
        assert client.post(route + "/login", json={}).status_code == 400
        assert client.post(route + "/login", json=[]).status_code == 400
        assert client.post(route + "/login", content=b"x" * 4097).status_code == 413
        assert client.post(route + "/login", json={field: "000000"}).status_code == 401
        accepted = client.post(route + "/login", json={field: "654321"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["token"] == "synthetic-game-session"
        client.headers["Authorization"] = "Bearer synthetic-other-agent-session"
        assert client.post("/api/game/publish/synthetic-publishing", content=html).status_code == 401
        client.headers["Authorization"] = "Bearer " + accepted.json()["token"]
        assert client.get(route + "/status").json()["authenticated"]
        assert client.post("/api/game/publish/synthetic-publishing", content=html).status_code == 200
        assert (tmp_path / "mobile_games/synthetic-publishing.html").read_bytes() == html


def test_game_studio_publish_requires_auth_and_saves_html(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    auth = {"authenticated": False}
    server = SimpleNamespace(
        __file__=str(tmp_path / "server.py"),
        get_mutable_data_dir=lambda *_args, **_kwargs: tmp_path,
        _is_logged_in=lambda _request: False,
    )
    monkeypatch.setattr(game_studio._smc, "_agent_chat_session_is_valid", lambda agent, token: auth["authenticated"] and agent == "game_agent" and token == "synthetic-game-session")
    monkeypatch.setattr(game_studio._smc, "_runtime_server", lambda: server)
    html = b"<html><head></head><body>synthetic game<button onclick=\"AutoYouGame.quit()\">Quit</button></body></html>"
    # from __debug_trace_s__ import autoyou_provenance_s_000000000000000000000000_4b1a7244afa42397d2600afd
    target = tmp_path / "mobile_games" / "synthetic-runner.html"

    with TestClient(game_studio.app) as client:
        assert client.post("/api/game/publish/synthetic-runner", content=html).status_code == 401
        auth["authenticated"] = True
        assert client.post("/api/game/publish/synthetic-runner", content=html).status_code == 401
        client.headers["Authorization"] = "Bearer synthetic-game-session"
        assert client.post("/api/game/publish/INVALID", content=html).status_code == 400
        assert client.post("/api/game/publish/synthetic-runner", content=b"<html>no head</html>").status_code == 400
        assert client.post("/api/game/publish/synthetic-runner", content=b"\xff<head></head>").status_code == 400
        # AutoYou shows no close button over a game, so a page without its own Quit is refused.
        refused = client.post("/api/game/publish/synthetic-runner", content=b"<html><head></head><body>no quit</body></html>")
        assert refused.status_code == 400 and "AutoYouGame.quit()" in refused.json()["error"]
        assert client.post("/api/game/publish/synthetic-runner", content=b"x" * (game_studio._MAX_MOBILE_GAME_BYTES + 1)).status_code == 413
        assert not target.exists()
        assert client.post("/api/game/publish/synthetic-runner", content=html).status_code == 200
        assert target.read_bytes() == html
        client.headers.pop("Authorization")
        client.cookies.set(game_studio._smc._agent_chat_cookie_name("game_agent"), "synthetic-game-session")
        assert client.post("/api/game/publish/synthetic-runner", content=html).status_code == 200
        # A downloaded game includes the sandbox policy. Re-publishing must retain the complete source.
        from shared.game_catalog import FRAME_CSP_META, MAX_GAME_BYTES, read_game

        prefix = b"<html><head>"
        maximum = html + b" " * (MAX_GAME_BYTES - len(FRAME_CSP_META) - len(html))
        for _ in range(3):
            downloaded = prefix + FRAME_CSP_META + maximum[len(prefix):]
            assert len(downloaded) == MAX_GAME_BYTES
            response = client.post("/api/game/publish/synthetic-runner", content=downloaded)
            assert response.status_code == 200, response.text
            assert target.read_bytes() == maximum
            assert read_game(target) == maximum


def test_complete_package_upload_poll_and_download_are_authenticated_and_owner_bound(tmp_path,monkeypatch):
    import hashlib,json,time
    from pathlib import Path
    monkeypatch.setenv("AUTOYOU_TEST_ROOT",str(tmp_path))
    server=SimpleNamespace(__file__=str(tmp_path/"server.py"),get_mutable_data_dir=lambda *_args,**_kwargs:tmp_path)
    monkeypatch.setattr(game_studio._smc,"_runtime_server",lambda:server)
    owners={"Bearer synthetic-package-owner","Bearer synthetic-package-other"}
    monkeypatch.setattr(game_studio._smc,"_agent_chat_session_is_valid",lambda _agent,token:"Bearer "+token in owners)
    monkeypatch.setattr(game_studio._smc,"_describe_chat_auth_state",lambda request,_agent:{"authenticated":request.headers.get("Authorization") in owners})
    from tests.support.offline_games import offline_game_packages
    file=offline_game_packages()/"package-v1.aygame"
    checksum=hashlib.sha256(file.read_bytes()).hexdigest();base="/api/game/packages/uploads"
    with TestClient(game_studio.app) as client:
        metadata={"id":"synthetic-package","size":file.stat().st_size,"sha256":checksum}
        assert client.post(base,json=metadata).status_code==401
        client.headers["Authorization"]="Bearer synthetic-package-owner"
        reply=client.post(base,json=metadata);assert reply.status_code==200;key=reply.json()["upload_id"]
        offset=0
        with file.open("rb") as stream:
            while chunk:=stream.read(1024*1024):
                reply=client.put(base+"/"+key+"?offset="+str(offset),content=chunk);assert reply.status_code==200;offset=reply.json()["offset"]
        client.headers["Authorization"]="Bearer synthetic-package-other"
        assert client.post(base+"/"+key+"/complete").status_code==400
        client.headers["Authorization"]="Bearer synthetic-package-owner"
        deadline=time.monotonic()+10
        while True:
            reply=client.post(base+"/"+key+"/complete");assert reply.status_code==200,reply.text
            if not reply.json().get("pending"):break
            assert time.monotonic()<deadline;time.sleep(.01)
        assert reply.json()["sha256"]==checksum
        assert client.get("/api/game/packages").json()["games"][0]["id"]=="synthetic-package"
        assert client.get("/api/game/packages/synthetic-package/download").content==file.read_bytes()


def test_snake_game_compatibility_powerups_and_route(tmp_path, monkeypatch):
    import re
    import shutil
    import subprocess
    from shared import game_catalog, game_package

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    snake_path = game_studio._FRONTEND_DIR / "snake.html"
    assert snake_path.is_file()
    raw = snake_path.read_bytes()
    html = raw.decode("utf-8")

    # Verify Quit (X) button next to Pause and Settings and offline/package compatibility
    corner = re.search(r'<div class="corner">(.*?)</div>', html, re.S).group(1)
    assert re.findall(r'<button id="([a-z-]+)"', corner) == ["pause", "settings-open", "quit"]
    assert 'aria-label="Quit game"' in corner
    assert game_package.supports_quit(raw)
    assert game_catalog.read_game(snake_path) == raw
    framed = game_catalog.frame_html(raw).decode("utf-8")
    assert "connect-src 'none'" in framed and "window.autoYouGameInput = handleRemoteFrame" in framed

    # Verify .aygame packaging for iOS, Android, v2 Windows, and v2 macOS
    aygame = tmp_path / "neon-viper.aygame"
    manifest = game_package.wrap_html(snake_path, aygame, "neon-viper", "Neon Viper")
    assert manifest["id"] == "neon-viper" and manifest["title"] == "Neon Viper"
    assert game_package.package_supports_quit(aygame)

    # Verify /snake route and publishing in Game Studio
    auth = {"authenticated": False}
    server = SimpleNamespace(
        __file__=str(tmp_path / "server.py"),
        get_mutable_data_dir=lambda *_args, **_kwargs: tmp_path,
        _is_logged_in=lambda _request: False,
    )
    monkeypatch.setattr(game_studio._smc, "_describe_chat_auth_state", lambda _req, _agent: auth)
    monkeypatch.setattr(game_studio._smc, "_agent_chat_session_is_valid", lambda agent, token: auth["authenticated"] and agent == "game_agent" and token == "synthetic-game-session")
    monkeypatch.setattr(game_studio._smc, "_runtime_server", lambda: server)
    with TestClient(game_studio.app) as client:
        assert client.get("/snake").status_code == 401
        auth["authenticated"] = True
        res = client.get("/snake")
        assert res.status_code == 200 and "JUNGLE VIPER" in res.text
        client.headers["Authorization"] = "Bearer synthetic-game-session"
        assert client.post("/api/game/publish/neon-viper", content=raw).status_code == 200
        assert (tmp_path / "mobile_games" / "neon-viper.html").read_bytes() == raw

    # Exercise gameplay, power-up systems, audio, and cross-platform inputs in Node VM
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    check_script = tmp_path / "check_snake.cjs"
    check_script.write_text(r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const source = html.match(/<script\b[^>]*>([\s\S]*?)<\/script[^>]*>/i)[1];

let clock = 0, quitCalled = 0;
const elements = new Map(), storage = new Map();
const gradient = {addColorStop() {}};
const context = new Proxy({}, {get: (_, name) => name.startsWith('create') ? () => gradient : () => {}});
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    textContent: '', hidden: false, style: {}, dataset: {}, events: new Map(),
    addEventListener(name, cb) {
      if (!this.events.has(name)) this.events.set(name, []);
      this.events.get(name).push(cb);
    },
    setAttribute() {}, removeAttribute() {}, toggleAttribute() {},
    setPointerCapture() {}, focus() {}, click() {
      for (const cb of this.events.get('click') || []) cb({currentTarget: this, preventDefault() {}});
    },
    getContext: () => context,
  });
  return elements.get(id);
}
const controls = ['up', 'left', 'down', 'right', 'boost'].map(name => {
  const b = element(name); b.dataset.action = name; return b;
});
const document = element('document');
let created = 0;
Object.assign(document, {
  querySelector: element, querySelectorAll: () => controls,
  createElement: () => element(`created-${created++}`),
  documentElement: element('html'),
  body: {hasAttribute: () => false},
});
const window = element('window');
window.AutoYouGame = {quit: () => { quitCalled++; return true; }};
const sandbox = {
  document, window, innerWidth: 800, innerHeight: 600, devicePixelRatio: 2,
  location: {pathname: '/snake'}, performance: {now: () => clock},
  localStorage: {
    getItem: k => storage.get(k) ?? null,
    setItem: (k, v) => storage.set(k, String(v)),
  },
  Audio: class { constructor(src) { this.src = src; } play() { return Promise.resolve(); } },
  btoa: text => Buffer.from(text, 'binary').toString('base64'),
  requestAnimationFrame() {}, setTimeout() {}, clearTimeout() {},
  addEventListener: window.addEventListener.bind(window),
};
vm.createContext(sandbox);
vm.runInContext(source.replace('    best = loadBest();', '    globalThis.inspect = text => eval(text);\n    best = loadBest();'), sandbox);
const run = sandbox.inspect;

// 1. Verify WAV sound synthesis
const sound = run("wavSound(440, 0.1, 'sine')");
const wav = Buffer.from(sound.src.split(',', 2)[1], 'base64');
assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
assert.equal(wav.toString('ascii', 8, 12), 'WAVE');
assert.ok(wav.subarray(44).some(b => b !== 0));

// 2. Start run and verify movement + 2-step turn queue buffering
run('start()');
assert.equal(run('mode'), 'running');
assert.equal(run('snake.length'), 4);
run("action('down'); action('left');");
assert.equal(run('turnQueue.length'), 2);
run('stepGrid()');
assert.equal(run('dir.x'), 0);
assert.equal(run('dir.y'), 1);
run('stepGrid()');
assert.equal(run('dir.x'), -1);
assert.equal(run('dir.y'), 0);

// 3. Eat Bio-Core and Golden Nova Core
const head = run('snake[0]');
run(`foods = [{kind: 'core', x: ${head.x - 1}, y: ${head.y}, pulse: 0}]`);
run('stepGrid()');
assert.equal(run('cores'), 1);
assert.ok(run('score') > 0);
assert.equal(run('streak'), 1);

const head2 = run('snake[0]');
run(`foods = [{kind: 'nova', x: ${head2.x - 1}, y: ${head2.y}, life: 10, maxLife: 10, pulse: 0}]`);
run('stepGrid()');
assert.equal(run('run.novas'), 1);
assert.equal(run('cores'), 2);

// 4. Test all 6 Power-Ups & Synergy Combo
for (const kind of ['magnet', 'ghost', 'chrono', 'surge', 'double', 'trim']) {
  const h = run('snake[0]');
  run(`collectPowerUp({kind: '${kind}', x: ${h.x}, y: ${h.y}})`);
}
assert.equal(run('run.powers'), 6);
assert.ok(run('run.synergies') >= 4);
assert.ok(run('magnet') > 0 && run('ghost') > 0 && run('chrono') > 0 && run('surge') > 0 && run('doubled') > 0);

// 5. Magnet pulls nearby cores automatically during update(dt)
const h3 = run('snake[0]');
run(`foods = [{kind: 'core', x: ${h3.x + 2}, y: ${h3.y}, pulse: 0}]`);
for (let i = 0; i < 20; i++) { clock += 50; run('update(0.05)'); }
assert.ok(run('cores') >= 3);

// 6. Surge smashes active hazard into a gem
const h4 = run('snake[0]');
const d4 = run('dir');
const hx = (h4.x + d4.x + run('cols')) % run('cols');
const hy = (h4.y + d4.y + run('rows')) % run('rows');
run(`surge = 5; hazards = [{x: ${hx}, y: ${hy}, warmup: 0, life: 10, maxLife: 10}]`);
run('stepGrid()');
assert.equal(run('run.smashes'), 1);

// 7. Aegis Shield absorbs fatal wall crash in solid-wall mode
run(`settings.walls = 'solid'; ghost = 0; overdrive = 0; invincible = 0; shield = true; snake[0] = {x: 0, y: 5}; dir = {x: -1, y: 0}; turnQueue = []`);
run('stepGrid()');
assert.equal(run('mode'), 'running');
assert.equal(run('shield'), false);
assert.ok(run('ghost') > 0);

// 8. Remote frame & Quit button
run(`handleRemoteFrame({event: 'game_input', input_type: 'button', button: 'up', phase: 'down', session_id: 'p1'})`);
element('#quit').click();
assert.equal(quitCalled, 1);
""", encoding="utf-8")
    result = subprocess.run([node, str(check_script), str(snake_path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
