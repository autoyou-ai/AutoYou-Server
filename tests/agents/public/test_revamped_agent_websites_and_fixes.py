# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Regression tests for the 7 revamped agent websites and bugfixes."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import asyncio
import shutil
import subprocess

from fastapi.testclient import TestClient
from PIL import Image

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()


def test_scheduler_mission_control_allows_same_origin_website_builder_framing():
    from autoyou_agents.shared_tools import scheduler_mission_control as smc

    assert smc.SECURITY_HEADERS["X-Frame-Options"] == "SAMEORIGIN"
    csp = smc.SECURITY_HEADERS["Content-Security-Policy"]
    assert "frame-ancestors 'self'" in csp
    assert "frame-ancestors 'none'" not in csp


def test_remote_desktop_rejects_zero_size_bounds_and_supports_hotkeys(monkeypatch):
    from autoyou_agents.remote_desktop_agent.website.backend import app as rd_app

    assert rd_app._normalize_capture_bounds({"left": 0, "top": 0, "width": 0, "height": 0}) is None
    assert rd_app._normalize_capture_bounds({"left": 0, "top": 0, "width": 5, "height": 5}) is None
    valid = rd_app._normalize_capture_bounds({"left": 10, "top": 20, "width": 1920, "height": 1080})
    assert valid == {"left": 10, "top": 20, "width": 1920, "height": 1080}

    calls = []

    class FakePyAutoGUI:
        FAILSAFE = True

        def moveRel(self, dx, dy):
            calls.append(("moveRel", dx, dy))

        def hotkey(self, *keys):
            calls.append(("hotkey", keys))

        def write(self, text):
            calls.append(("write", text))

    monkeypatch.setattr(rd_app, "_control_backend", lambda: FakePyAutoGUI())
    monkeypatch.setattr(rd_app, "_current_platform_tag", lambda: "macos")

    assert rd_app._simulate_input({"type": "moverel", "dx": 15, "dy": -8}) is True
    assert rd_app._simulate_input({"type": "hotkey", "keys": ["cmd", "c"]}) is True
    assert rd_app._simulate_input({"type": "type", "text": "hello"}) is True
    assert calls == [
        ("moveRel", 15, -8),
        ("hotkey", ("command", "c")),
        ("write", "hello"),
    ]


def test_remote_desktop_snapshot_and_wake_endpoints(monkeypatch):
    from autoyou_agents.remote_desktop_agent.website.backend import app as rd_app

    monkeypatch.setattr(
        rd_app,
        "_describe_chat_auth_state",
        lambda _req, _name: {"authenticated": True, "required": False, "via": "open"},
    )
    monkeypatch.setattr(
        rd_app,
        "_get_monitor_bounds_sync",
        lambda _tid: {"left": 0, "top": 0, "width": 640, "height": 360},
    )
    sample = Image.new("RGB", (64, 36), (40, 120, 220))
    buf = BytesIO()
    sample.save(buf, format="JPEG")
    jpeg = buf.getvalue()
    monkeypatch.setattr(rd_app, "_capture_and_encode_sync", lambda *_args: jpeg)

    client = TestClient(rd_app.app)
    snap = client.get("/api/remote_desktop/snapshot?target_type=monitor&target_id=0")
    assert snap.status_code == 200
    assert snap.headers["content-type"] == "image/jpeg"
    assert snap.content == jpeg

    monkeypatch.setattr(rd_app, "_current_platform_tag", lambda: "linux")
    wake = client.post("/api/remote_desktop/wake")
    assert wake.status_code == 200
    assert wake.json()["success"] is True


def test_games_studio_package_play_serves_sandboxed_html_with_quit_bridge(tmp_path, monkeypatch):
    from autoyou_agents.games_agent.website.backend import app as game_studio
    from shared import game_package

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(
        game_studio._smc,
        "_describe_chat_auth_state",
        lambda _req, _name: {"authenticated": True, "required": False, "via": "open"},
    )
    pkg_dir = tmp_path / "mobile_games"
    pkg_dir.mkdir(parents=True)
    html_src = pkg_dir / "synthetic-arcade.html"
    html_src.write_text(
        "<!doctype html><html><head><title>Arcade</title></head>"
        "<body><button onclick=\"AutoYouGame.quit()\">Quit</button></body></html>",
        encoding="utf-8",
    )
    # Also verify a compiled AYGAME1 binary package (.aygame) works end-to-end.
    built_pkg = pkg_dir / "synthetic-built.aygame"
    game_package.wrap_html(html_src, built_pkg, "synthetic-built", title="Synthetic Built")

    server = SimpleNamespace(
        __file__=str(tmp_path / "server.py"),
        get_mutable_data_dir=lambda *_args, **_kwargs: tmp_path,
    )
    monkeypatch.setattr(game_studio._smc, "_runtime_server", lambda: server)

    with TestClient(game_studio.app) as client:
        for game_id in ("synthetic-arcade", "synthetic-built"):
            resp = client.get(f"/api/game/packages/{game_id}/play")
            assert resp.status_code == 200
            assert "autoyou_game_quit" in resp.text
            assert "frame-ancestors 'self'" in resp.headers["content-security-policy"]
            assert "sandbox" in resp.headers["content-security-policy"]
            assert resp.headers["x-frame-options"] == "SAMEORIGIN"


def test_ads_watching_status_endpoint_reports_device_lifecycle_truthfully(monkeypatch):
    from autoyou_agents.ads_watching_agent.website.backend import app as ads_backend

    status_record = {
        "control_id": "ctrl-synthetic-1",
        "state": "failed",
        "reason": "No ad is available right now.",
        "platform": "ios",
        "timestamp_ms": 1700000000000,
    }

    class FakeWebRTC:
        def latest_rewarded_ad_status(self, *, session_id="", owner_key="", control_id=""):
            if control_id and control_id != status_record["control_id"]:
                return None
            return dict(status_record)

        def latest_rewarded_ad_completion(self, *, session_id="", owner_key=""):
            return None

    monkeypatch.setattr(ads_backend, "_runtime_server", lambda: SimpleNamespace(WEBRTC=FakeWebRTC()))

    client = TestClient(ads_backend.app)
    resp = client.get("/api/watch-ad/status?control_id=ctrl-synthetic-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["ad_status"]["state"] == "failed"
    assert body["ad_status"]["reason"] == "No ad is available right now."


def test_agent_py_explicit_route_aliases_include_games_agent():
    from autoyou_agents import agent as root_agent

    all_aliases = {
        alias
        for aliases in root_agent._EXPLICIT_ROUTE_ALIASES.values()
        for alias in aliases
    }
    assert "games_agent" in all_aliases
    assert "game studio" in all_aliases


def test_webrtc_engine_rewarded_ad_status_lifecycle_clears_lease():
    from core_server import webrtc_engine

    manager = webrtc_engine.WebRTCManager()
    session_id = "sess-synthetic-ad-1"
    control_id = "ctrl-synthetic-ad-1"

    manager._record_rewarded_ad_status(
        session_id,
        control_id=control_id,
        state="sent",
        reason="",
        platform="server",
    )
    latest = manager.latest_rewarded_ad_status(session_id=session_id, control_id=control_id)
    assert latest is not None
    assert latest["state"] == "sent"

    # Simulate native device reporting opening -> opened -> failed/dismissed
    cleared = []
    manager._clear_rewarded_ad_control_lease = lambda sid, cid: cleared.append((sid, cid))
    asyncio.run(
        manager._handle_voice_call_control_message(
            SimpleNamespace(
                header=SimpleNamespace(session_id=session_id),
                payload={
                    "event": "rewarded_ad_status",
                    "control_id": control_id,
                    "state": "opened",
                    "platform": "ios",
                },
            ),
            trusted_session_id=session_id,
        )
    )
    assert manager.latest_rewarded_ad_status(session_id=session_id, control_id=control_id)["state"] == "opened"
    assert cleared == []

    asyncio.run(
        manager._handle_voice_call_control_message(
            SimpleNamespace(
                header=SimpleNamespace(session_id=session_id),
                payload={
                    "event": "rewarded_ad_status",
                    "control_id": control_id,
                    "state": "failed",
                    "reason": "No ad is available right now.",
                    "platform": "ios",
                },
            ),
            trusted_session_id=session_id,
        )
    )
    failed = manager.latest_rewarded_ad_status(session_id=session_id, control_id=control_id)
    assert failed["state"] == "failed"
    assert failed["reason"] == "No ad is available right now."
    assert cleared == [(session_id, control_id)]


def test_ads_watching_frontend_waits_for_device_open_confirmation():
    node = shutil.which("node")
    if not node:
        return
    js_path = (
        Path(__file__).resolve().parents[3]
        / "autoyou_agents"
        / "ads_watching_agent"
        / "website"
        / "frontend"
        / "assets"
        / "app.js"
    )
    script = f"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync({str(js_path)!r}, 'utf8');
const elements = {{}};
function makeEl(id) {{
  return {{
    id,
    textContent: '',
    hidden: false,
    disabled: false,
    dataset: {{}},
    attributes: {{}},
    classList: {{
      _set: new Set(),
      add(c) {{ this._set.add(c); }},
      remove(c) {{ this._set.delete(c); }},
      contains(c) {{ return this._set.has(c); }},
    }},
    setAttribute(k, v) {{ this.attributes[k] = String(v); }},
    getAttribute(k) {{ return this.attributes[k] || ''; }},
    removeAttribute(k) {{ delete this.attributes[k]; }},
    addEventListener(ev, fn) {{ this['_on_' + ev] = fn; }},
  }};
}}
for (const id of ['watch-ad-btn','watch-status','web-fallback-shell','web-fallback-frame','reward-celebration','reward-amount','reward-close','theme-toggle']) {{
  elements[id] = makeEl(id);
}}
elements['web-fallback-shell'].hidden = true;
elements['reward-celebration'].hidden = true;
let pollCallback = null;
let capturedControlId = '';
let statusResponse = {{ success: true, ad_status: {{ control_id: '', state: 'opening', reason: '' }} }};
const ctx = {{
  console,
  URL,
  URLSearchParams,
  Date,
  Number,
  Math,
  String,
  Boolean,
  Object,
  Array,
  Error,
  Promise,
  setTimeout: () => 1,
  clearTimeout: () => {{}},
  setInterval: (fn) => {{ pollCallback = fn; return 2; }},
  clearInterval: () => {{ pollCallback = null; }},
  localStorage: {{ _m: {{}}, getItem(k) {{ return this._m[k] || null; }}, setItem(k, v) {{ this._m[k] = String(v); }} }},
  matchMedia: () => ({{ matches: false }}),
  document: {{
    readyState: 'complete',
    documentElement: {{ dataset: {{}}, setAttribute(k, v) {{ this[k] = v; }} }},
    getElementById: (id) => elements[id] || null,
    addEventListener: () => {{}},
  }},
  window: {{
    location: {{ href: 'http://127.0.0.1:8067/agent/ads_watching_agent/', search: '' }},
    addEventListener: () => {{}},
  }},
  fetch: async (url, opts) => {{
    const s = String(url);
    if (s.includes('/api/status')) {{
      return {{
        ok: true,
        json: async () => ({{
          success: true,
          status: {{
            live_ad_units: {{ ios_rewarded: true }},
            web_fallback: {{ available: false }},
          }},
        }}),
      }};
    }}
    if (s.includes('/api/watch-ad/status')) {{
      return {{ ok: true, json: async () => statusResponse }};
    }}
    if (s.includes('/api/watch-ad')) {{
      const reqBody = JSON.parse((opts && opts.body) || '{{}}');
      capturedControlId = String(reqBody.control_id || '');
      return {{
        ok: true,
        json: async () => ({{
          success: true,
          result: {{ status: 'sent', triggered_count: 1 }},
        }}),
      }};
    }}
    throw new Error('unexpected url: ' + s);
  }},
}};
ctx.globalThis = ctx;
vm.runInNewContext(code, ctx);
(async () => {{
  await new Promise(r => setImmediate(r));
  await elements['watch-ad-btn']._on_click();
  if (!capturedControlId) {{
    throw new Error('Expected app.js to send a generated control_id in POST /api/watch-ad body');
  }}
  // Immediately after POST /api/watch-ad returns status:'sent', MUST NOT say "Ad opened."!
  if (elements['watch-status'].textContent === 'Ad opened.') {{
    throw new Error('Falsely claimed Ad opened before device confirmation!');
  }}
  if (elements['watch-status'].textContent !== 'Opening ad...') {{
    throw new Error('Expected Opening ad..., got: ' + elements['watch-status'].textContent);
  }}
  // Device confirms opened -> NOW it says "Ad opened."
  statusResponse = {{ success: true, ad_status: {{ control_id: capturedControlId, state: 'opened', reason: '' }} }};
  await pollCallback();
  if (elements['watch-status'].textContent !== 'Ad opened.') {{
    throw new Error('Expected Ad opened. after device opened, got: ' + elements['watch-status'].textContent);
  }}
  // Device reports failure -> displays real reason
  statusResponse = {{ success: true, ad_status: {{ control_id: capturedControlId, state: 'failed', reason: 'No ad available' }} }};
  await pollCallback();
  if (elements['watch-status'].textContent !== 'No ad available') {{
    throw new Error('Expected real error reason, got: ' + elements['watch-status'].textContent);
  }}
  console.log('ADS_FRONTEND_TRUTHFUL_OK');
}})().catch(err => {{ console.error(err); process.exit(1); }});
"""
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"Node test failed: {proc.stdout}\n{proc.stderr}"
    assert "ADS_FRONTEND_TRUTHFUL_OK" in proc.stdout
