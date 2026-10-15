# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Exercise the shipped UI against synthetic APIs, never a running server."""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
from urllib.parse import urlsplit

import pytest

from shared.admin_setup_profiles import build_setup_profile_payload
from tests.support.paths import REPO_ROOT


def test_profile_photo_cache_rejects_stale_identity_and_invalid_payloads():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise the admin JavaScript")
    runner = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
let calls = 0, status = 200, payload, deferred;
const sandbox = {
    document: {getElementById: () => null, querySelector: () => null, addEventListener() {}},
    window: {location: {pathname: "/", hash: ""}, addEventListener() {}}, console,
    fetch: async () => {calls++; if (deferred) return deferred;
        return {ok: status === 200, status, text: async () => JSON.stringify(payload)};}
};
let source = fs.readFileSync(process.argv[1], "utf8").replace(/    boot\(\);\s*\}\(\)\);\s*$/,
    '    globalThis.admin = {state, syncAdminProfileImage, adminProfileImageUrl};\n}());');
vm.runInNewContext(source, sandbox);
const a = sandbox.admin, url = "/assets/admin/profile-image?v=1";
function reset(id = "synthetic-one", version = url) {
    a.state.bootstrap = {admin: {server_id: id, avatar_user_id: id, avatar_url: version}};
    payload = {profile_user_id: id, avatar_url: version, has_photo: true, mime_type: "image/gif", data_base64: "R0lGODlh"};
    status = 200;
}
(async () => {
    reset();
    await a.syncAdminProfileImage();
    assert.equal(a.adminProfileImageUrl("synthetic-one", url), "data:image/gif;base64,R0lGODlh");
    await a.syncAdminProfileImage();
    assert.equal(calls, 1, "Reuse the in-memory photo across renders");
    reset("synthetic-one", url + "2");
    payload.avatar_url += "3";
    await a.syncAdminProfileImage();
    assert.equal(a.adminProfileImageUrl("synthetic-one", payload.avatar_url), "data:image/gif;base64,R0lGODlh",
        "Accept the current server photo version returned by secure storage");

    for (const overrides of [
        {profile_user_id: "synthetic-other"}, {mime_type: "text/html"}, {has_photo: false},
        {avatar_url: "https://example.invalid/private"}, {data_base64: "invalid%"},
        {data_base64: "A".repeat(4 * Math.ceil(2 * 1024 * 1024 / 3) + 4)}
    ]) {
        reset("synthetic-one", url + calls); Object.assign(payload, overrides);
        await a.syncAdminProfileImage();
        assert.equal(a.state.profileImage.dataUrl, "", "Invalid or different-server photos stay hidden");
    }
    reset("synthetic-auth-error"); status = 401;
    await a.syncAdminProfileImage();
    assert.equal(a.state.profileImage.dataUrl, "");
    const before = calls; await a.syncAdminProfileImage();
    assert.equal(calls, before, "Do not create a failing request loop");

    reset("synthetic-old");
    let resolveOld;
    deferred = new Promise(resolve => resolveOld = resolve);
    const oldRequest = a.syncAdminProfileImage();
    deferred = null; reset("synthetic-new");
    await a.syncAdminProfileImage();
    resolveOld({ok: true, status: 200, text: async () => JSON.stringify({
        profile_user_id: "synthetic-old", avatar_url: url, has_photo: true, mime_type: "image/png", data_base64: "T0xE"
    })});
    await oldRequest;
    assert.equal(a.adminProfileImageUrl("synthetic-new", url), "data:image/gif;base64,R0lGODlh");
    reset("synthetic-new", ""); await a.syncAdminProfileImage();
    assert.equal(a.state.profileImage.dataUrl, "", "Removing a photo releases cached bytes");
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    result = subprocess.run([node, "-e", runner, str(REPO_ROOT / "assets/admin-ui.js")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("width,prefix", [(1440, ""), (1180, ""), (1024, ""), (820, ""),
                                         (390, ""), (320, ""), (1280, "/agent/admin_agent"),
                                         (390, "/agent/admin_agent")])
def test_responsive_cards_stable_poll_render_and_private_proxy_photo(width, prefix, tmp_path):
    browser_api = pytest.importorskip("playwright.sync_api")
    import server

    gif = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    requests, failures = [], []
    bootstrap = {
        "metadata": {"setup_profiles": build_setup_profile_payload()},
        "admin": {"theme": "light", "server_name": "Synthetic server", "server_id": "synthetic-server",
                  "avatar_user_id": "synthetic-server", "avatar_url": "/assets/admin/profile-image?v=1"},
        "config": {"server": {"name": "Synthetic server", "bind_host": "127.0.0.1"},
                   "ai_provider": {"provider": "ollama"}, "ollama": {"model": "synthetic-model:8b"}},
        "status": {}, "agents": {"agent_overview": []},
    }
    shell = ('<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
             '<link rel="stylesheet" href="/assets/admin-ui.css"></head><body>'
             '<div id="autoyou-admin-root"></div><script src="/assets/admin-ui.js"></script></body></html>')
    if prefix:
        shell = server.WebRTCManager._inject_agent_proxy_shim(shell.encode(), prefix).decode()

    def serve(route):
        raw_path = urlsplit(route.request.url).path
        requests.append(raw_path)
        path = raw_path.removeprefix(prefix) if prefix else raw_path
        if path == "/":
            return route.fulfill(content_type="text/html", body=shell)
        if path == "/assets/admin-ui.js":
            script = (REPO_ROOT / "assets/admin-ui.js").read_text(encoding="utf-8")
            script = script.replace("    boot();\n}());", "    window.__admin = {state, renderApp, refreshModelDownloads};\n    boot();\n}());")
            return route.fulfill(content_type="text/javascript", body=script)
        if path == "/assets/admin-ui.css":
            return route.fulfill(content_type="text/css", body=(REPO_ROOT / path.lstrip("/")).read_text(encoding="utf-8"))
        if path.startswith("/assets/"):
            return route.fulfill(status=502 if "profile-image" in path else 200,
                                 content_type="image/gif", body=base64.b64decode(gif))
        data = {"success": True}
        if path == "/api/admin/bootstrap":
            data = bootstrap
        elif path == "/api/admin/profile-image":
            data = {"profile_user_id": "synthetic-server", "has_photo": True,
                    "avatar_url": bootstrap["admin"]["avatar_url"], "mime_type": "image/gif", "data_base64": gif}
        elif path == "/api/wizard/status":
            data = {"password_changed": True, "ollama": {"local_models": ["synthetic-model:8b"]}}
        elif path == "/api/model-library/downloads":
            data = {"jobs": []}
        return route.fulfill(content_type="application/json", body=json.dumps(data))

    with browser_api.sync_playwright() as automation:
        try:
            browser = automation.chromium.launch(headless=True)
        except Exception as error:
            pytest.skip(f"Chromium is unavailable: {error}")
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.on("pageerror", lambda error: failures.append(str(error)))
            page.route("**/*", serve)
            page.goto("http://admin.test" + prefix + "/", wait_until="networkidle")
            page.wait_for_function("document.querySelector('img[data-admin-profile-image]')?.naturalWidth > 0")
            assert not any("/assets/admin/profile-image" in path for path in requests)
            assert requests.count(prefix + "/api/admin/profile-image") == 1
            if not page.locator('[data-action="nav:setup"]').first.is_visible():
                page.locator('[data-action="toggle-nav"]').first.click()
            page.locator('[data-action="nav:setup"]').first.click()
            page.locator('[data-disclosure="setup-presets"] summary').click()
            page.wait_for_function("window.__admin.state.disclosure['setup-presets'] === true")
            assert page.locator(".ayu-setup-profile-card").count() == 12
            page.evaluate("window.__admin.renderApp()")
            overflow = page.locator(".ayu-setup-profile-card").evaluate_all("""cards => cards.flatMap(card => {
                const bounds = card.getBoundingClientRect();
                return [...card.querySelectorAll('.ayu-badge, p, .ayu-choice-card-title')].filter(item => {
                    const box = item.getBoundingClientRect();
                    return box.left < bounds.left || box.right > bounds.right + 1 || item.scrollWidth > item.clientWidth + 1;
                }).map(item => item.textContent);
            })""")
            assert overflow == []
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.evaluate("window.beforePoll = document.querySelector('[data-setup-heading]'); window.beforePhoto = document.querySelector('img[data-admin-profile-image]')")
            page.evaluate("async () => {for(let i=0; i<3; i++) await window.__admin.refreshModelDownloads(true)}")
            assert page.evaluate("beforePoll === document.querySelector('[data-setup-heading]') && beforePhoto === document.querySelector('img[data-admin-profile-image]')")
            assert page.locator(".ayu-guided-step-body").evaluate("el => getComputedStyle(el).animationName") == "none"
            page.evaluate("window.__admin.state.bootstrap.admin.server_name = 'Updated synthetic server'; window.__admin.renderApp({passive:true})")
            assert page.locator(".ayu-brand-title").text_content() == "Updated synthetic server"
            assert requests.count(prefix + "/api/admin/profile-image") == 1
            page.screenshot(path=str(tmp_path / f"cards-{width}-{'proxy' if prefix else 'direct'}.png"), full_page=True, animations="disabled")
            page.evaluate("window.__admin.state.screen = 'chat'; window.__admin.renderApp()")
            page.wait_for_function("document.querySelector('.ayu-chat-identity img')?.naturalWidth > 0")
            assert not any("/assets/admin/profile-image" in path for path in requests)
            assert requests.count(prefix + "/api/admin/profile-image") == 1
            assert not failures, failures
        finally:
            browser.close()
