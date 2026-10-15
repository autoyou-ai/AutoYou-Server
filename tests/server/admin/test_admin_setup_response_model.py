# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Setup response choices use the real script with an isolated in-memory API."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from shared.admin_setup_profiles import build_setup_profile_payload
from tests.support.paths import REPO_ROOT


def test_existing_missing_and_downloading_setup_models():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise the setup JavaScript")
    runner = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const metadata = JSON.parse(fs.readFileSync(0, "utf8"));
let bootstrap, inventory, calls, jobs, failInventory, failDownload;
function merge(target, changes) {
    for (const [key, value] of Object.entries(changes)) {
        if (value && typeof value === "object" && !Array.isArray(value)) merge(target[key] ||= {}, value);
        else target[key] = value;
    }
}
const sandbox = {
    document: {getElementById: () => null, querySelector: () => null, addEventListener() {}, documentElement: {setAttribute() {}}},
    window: {location: {pathname: "/", hash: "", origin: "http://admin.test"}, addEventListener() {},
             setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {}, requestAnimationFrame() {}},
    navigator: {}, console, URL, TextEncoder, TextDecoder,
    fetch: async (url, options = {}) => {
        const body = JSON.parse(options.body || "{}");
        calls.push({url, body, method: options.method || "GET"});
        let data = {success: true}, status = 200;
        if (url === "/api/admin/bootstrap") data = bootstrap;
        if (url === "/api/admin/config") {merge(bootstrap.config, body); data = {bootstrap};}
        if (url === "/api/wizard/status") data = {password_changed: true, ollama: {local_models: inventory}};
        if (url === "/api/model-library/local") {
            data = failInventory ? {error: "Synthetic inventory unavailable"} : {models: inventory, selected_model: bootstrap.config.ollama.model};
            if (failInventory) status = 503;
        }
        if (url === "/api/model-library/download") {
            if (failDownload) {status = 503; data = {error: "Synthetic download failure"};}
            else {const job = {job_id: "synthetic-model-job", reference: body.reference, status: "queued"}; jobs.push(job); data = {job};}
        }
        if (url === "/api/model-library/downloads") data = {jobs};
        return {ok: status < 400, status, text: async () => JSON.stringify(data)};
    }
};
let source = fs.readFileSync(process.argv[1], "utf8");
source = source.replace(/    boot\(\);\s*\}\(\)\);\s*$/, '    globalThis.admin = {state, initializeForms, currentSetupAnswers, guidedReadiness, renderGuidedLocalModel, renderSetupScreen, handleAction, refreshModelDownloads, ensureAiData};\n}());');
vm.runInNewContext(source, sandbox);
const a = sandbox.admin;
function reset(model = "synthetic-model:8b", models = ["synthetic-model:8b"]) {
    inventory = models; jobs = []; calls = []; failInventory = false; failDownload = false;
    bootstrap = {metadata: {setup_profiles: metadata}, admin: {theme: "dark"},
        config: {server: {name: "Synthetic setup", bind_host: "127.0.0.1"}, security: {mode: "secure"},
                 ollama: {model, enabled: true}, ai_provider: {provider: "ollama"}, telegram: {acl_sender_ids: ["111000111"]}},
        status: {}, agents: {agent_overview: []}};
    a.state.bootstrap = bootstrap; a.initializeForms(bootstrap);
    Object.assign(a.state.setup, {guidedStep: 2, mode: "guided", answers: {}, modelCheckError: "", applied: false,
        payload: {password_changed: true, ollama: {local_models: inventory}}});
    Object.assign(a.state.aiLibrary, {local: {models: inventory}, localLoading: false, downloads: {jobs}, pendingSelection: null});
    a.state.screen = "setup";
    a.currentSetupAnswers().network_scope = "loopback";
}
const writes = () => calls.filter(call => call.method === "POST");
(async () => {
    reset();
    assert.equal(a.guidedReadiness()[1].ready, true, "String model inventories count as installed");
    await a.handleAction("setup-guided-continue");
    assert.equal(a.state.setup.guidedStep, 3, "Keep an existing model without pressing Save");
    assert.equal(writes().length, 0, "An unchanged valid choice does not rewrite configuration");

    reset("", []);
    inventory = ["synthetic-model:8b"];
    await a.handleAction("setup-guided-continue");
    assert.equal(a.state.setup.guidedStep, 3, "A model returned by the first inventory check is usable immediately");
    assert.equal(bootstrap.config.ollama.model, "synthetic-model:8b");

    reset("ollama_chat/synthetic-model", [{name: "synthetic-model:8b"}]);
    await a.handleAction("setup-guided-continue");
    assert.equal(a.state.setup.guidedStep, 3);
    assert.equal(bootstrap.config.ollama.model, "synthetic-model:8b", "Resolve an unambiguous installed tag");

    reset("synthetic-model:8b", [{name: "synthetic-model:8b"}, {model: "synthetic-model:14b"}]);
    a.state.forms.aiProvider.ollama_model = "synthetic-model:14b";
    assert.equal(a.guidedReadiness()[1].ready, false, "A different draft model is not saved yet");
    await a.ensureAiData(true);
    assert.equal(a.state.forms.aiProvider.ollama_model, "synthetic-model:14b", "Background inventory does not replace a draft selection");
    await a.handleAction("setup-guided-continue");
    assert.equal(bootstrap.config.ollama.model, "synthetic-model:14b");
    assert.equal(a.guidedReadiness()[1].ready, true);
    assert.equal(writes().length, 1);

    reset();
    a.currentSetupAnswers().ai_path = "native_ollama_gateway";
    await a.handleAction("setup-guided-continue");
    assert.equal(bootstrap.config.ai_provider.provider, "ollama_gateway");
    assert.equal(a.guidedReadiness()[1].ready, true);

    reset("synthetic-model:14b", []);
    assert.match(a.renderGuidedLocalModel(), /not installed on this computer/);
    await a.handleAction("setup-guided-continue");
    assert.equal(a.state.setup.guidedStep, 2);
    assert.equal(writes().length, 0, "Continue does not silently start a large download");
    await a.handleAction("setup-model-later");
    assert.equal(a.state.setup.guidedStep, 3);
    assert.equal(a.guidedReadiness()[1].ready, false, "Deferring installation does not mark a model ready");

    reset();
    a.state.forms.aiProvider.ollama_model = "synthetic-model:14b";
    await a.handleAction("setup-install-local-model");
    assert.equal(a.state.setup.guidedStep, 3);
    assert.equal(writes().filter(call => call.url === "/api/model-library/download").length, 1);
    assert.equal(a.guidedReadiness()[1].ready, false);
    inventory = ["synthetic-model:8b", "synthetic-model:14b"];
    jobs[0].status = "completed";
    await a.refreshModelDownloads(true);
    assert.equal(bootstrap.config.ollama.model, "synthetic-model:14b");
    assert.equal(a.state.aiLibrary.pendingSelection, null);
    assert.equal(a.guidedReadiness()[1].ready, true);
    assert(!calls.some(call => call.url === "/api/model-library/select"), "Setup saves a completed download without restarting services early");

    reset();
    a.state.forms.aiProvider.ollama_model = "synthetic-model:14b";
    await a.handleAction("setup-install-local-model");
    a.currentSetupAnswers().ai_path = "cloud_provider";
    inventory = ["synthetic-model:8b", "synthetic-model:14b"]; jobs[0].status = "completed";
    await a.refreshModelDownloads(true);
    assert.equal(bootstrap.config.ollama.model, "synthetic-model:8b", "A completed download must not undo a newer provider choice");
    assert(!calls.some(call => call.url === "/api/admin/config"));

    reset("synthetic-model:14b", []);
    await a.handleAction("setup-install-local-model");
    jobs[0].status = "failed";
    jobs[0].error = "Synthetic interrupted download";
    await a.refreshModelDownloads(true);
    assert.equal(a.state.aiLibrary.pendingSelection, null);
    assert.equal(a.guidedReadiness()[1].ready, false, "A failed download never marks setup ready");

    reset("synthetic-model", ["synthetic-model:8b", "synthetic-model:14b"]);
    assert.match(a.renderGuidedLocalModel(), /Choose an exact installed model tag/);
    await a.handleAction("setup-guided-continue");
    assert.equal(a.state.setup.guidedStep, 2);
    assert.equal(writes().length, 0, "Ambiguous model families are not treated as an exact installed tag");

    reset(); failInventory = true;
    await a.handleAction("setup-guided-continue");
    assert.equal(a.state.setup.guidedStep, 2);
    assert.match(a.renderGuidedLocalModel(), /Could not check installed models/);
    assert.equal(a.guidedReadiness()[1].ready, false);
    assert.equal(writes().length, 0);
    failInventory = false;
    await a.handleAction("setup-check-local-model");
    assert.equal(a.guidedReadiness()[1].ready, true);

    reset("synthetic-model:14b", []); failDownload = true;
    await assert.rejects(a.handleAction("setup-install-local-model"), /Synthetic download failure/);
    assert.equal(a.state.setup.guidedStep, 2);
    assert.equal(a.state.aiLibrary.pendingSelection, null);
    assert.equal(a.guidedReadiness()[1].ready, false);
    console.log("Existing choices, aliases, draft preservation, installation, deferral, completion and failures passed");
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    result = subprocess.run(
        [node, "-e", runner, str(REPO_ROOT / "assets/admin-ui.js")],
        input=json.dumps(build_setup_profile_payload()), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("installed", [True, False])
def test_response_continue_and_install_in_browser(width, installed, tmp_path):
    browser_api = pytest.importorskip("playwright.sync_api")
    model = "synthetic-model:14b"
    inventory = [model] if installed else []
    jobs, writes, failures = [], [], []
    bootstrap = {
        "metadata": {"setup_profiles": build_setup_profile_payload()}, "admin": {"theme": "dark"},
        "config": {"server": {"name": "Synthetic setup", "bind_host": "127.0.0.1"},
                   "security": {"mode": "secure"}, "ollama": {"model": model, "enabled": True},
                   "ai_provider": {"provider": "ollama"}},
        "status": {}, "agents": {"agent_overview": []},
    }

    def serve(route):
        path = route.request.url.split("http://admin.test", 1)[1].split("?", 1)[0]
        if path == "/":
            return route.fulfill(content_type="text/html", body=(
                '<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<link rel="stylesheet" href="/assets/admin-ui.css"></head><body>'
                '<div id="autoyou-admin-root"></div><script src="/assets/admin-ui.js"></script></body></html>'
            ))
        if path in {"/assets/admin-ui.js", "/assets/admin-ui.css"}:
            return route.fulfill(content_type="text/javascript" if path.endswith(".js") else "text/css",
                                 body=(REPO_ROOT / path.lstrip("/")).read_text(encoding="utf-8"))
        data = {"success": True}
        if route.request.method == "POST":
            body = json.loads(route.request.post_data or "{}")
            writes.append((path, body))
            if path == "/api/admin/config":
                for section, changes in body.items():
                    if isinstance(changes, dict):
                        bootstrap["config"].setdefault(section, {}).update(changes)
                    else:
                        bootstrap["config"][section] = changes
                data = {"bootstrap": bootstrap}
            if path == "/api/model-library/download":
                jobs.append({"job_id": "synthetic-download", "reference": model, "status": "running"})
                data = {"job": jobs[-1]}
        elif path == "/api/admin/bootstrap":
            data = bootstrap
        elif path == "/api/wizard/status":
            data = {"password_changed": True, "ollama": {"local_models": inventory}}
        elif path == "/api/model-library/local":
            data = {"models": [{"name": name} for name in inventory], "selected_model": model}
        elif path == "/api/model-library/downloads":
            data = {"jobs": jobs}
        return route.fulfill(content_type="application/json", body=json.dumps(data))

    with browser_api.sync_playwright() as automation:
        try:
            browser = automation.chromium.launch(headless=True)
        except Exception as error:
            pytest.skip(f"Chromium is unavailable: {error}")
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.on("pageerror", lambda error: failures.append(str(error)))
            page.route("http://admin.test/**", serve)
            page.goto("http://admin.test/", wait_until="networkidle")
            if not page.locator('[data-action="nav:setup"]').first.is_visible():
                page.locator('[data-action="toggle-nav"]').first.click()
            page.locator('[data-action="nav:setup"]').first.click()
            page.locator('[data-action="setup-guided-step:2"]').first.click()
            status = page.locator("#ayu-setup-model-status")
            browser_api.expect(status).to_contain_text("is installed." if installed else "is not installed on this computer.")
            page.screenshot(path=str(tmp_path / f"response-model-{width}-{installed}.png"), full_page=True, animations="disabled")
            page.locator('[data-action="setup-guided-continue"]').click()
            heading = page.locator("[data-setup-heading]")
            if not installed:
                browser_api.expect(heading).to_contain_text("What should power responses?")
                assert not writes, "A missing model requires an explicit installation choice"
                page.locator('[data-action="setup-model-later"]').click()
                browser_api.expect(heading).to_contain_text("What can AutoYou access?")
                page.locator('[data-action="setup-guided-step:2"]').first.click()
                page.locator('[data-action="setup-install-local-model"]').click()
                browser_api.expect(heading).to_contain_text("What can AutoYou access?")
                assert sum(path == "/api/model-library/download" for path, _ in writes) == 1
                inventory.append(model)
                jobs[0]["status"] = "completed"
                page.get_by_text("Model downloaded and selected.", exact=True).wait_for()
            browser_api.expect(heading).to_contain_text("What can AutoYou access?")
            assert not any(path == "/api/admin/config" for path, _ in writes), "Keep an unchanged valid model without forcing Save"
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            assert not failures, failures
        finally:
            browser.close()
