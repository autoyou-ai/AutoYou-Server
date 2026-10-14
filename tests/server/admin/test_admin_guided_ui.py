"""Run the real admin script in an isolated VM; never contact a live server."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.support.paths import REPO_ROOT
from shared.admin_setup_profiles import build_setup_profile_payload, compile_setup_recipe


def test_guided_ui_choices_readiness_and_agent_lifecycle():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise the admin JavaScript")
    fixture = {
        "metadata": {"setup_profiles": build_setup_profile_payload()},
        "config": {
            "server": {"name": "Synthetic server", "bind_host": "127.0.0.1"},
            "security": {"mode": "secure"},
            "ai_provider": {"provider": "ollama"},
            "ollama": {"model": "synthetic-model:latest"},
            "telegram": {"acl_sender_ids": ["111000111"]},
        },
        "admin": {"theme": "dark", "server_name": "Synthetic server"},
        "agents": {"agent_overview": [{"agent_name": "tv_agent", "installed": False}, {"agent_name": "memory_agent", "installed": True}]},
        "status": {"ai_agent": {"status": "Running"}, "autoyou_page": {"status": "Running"}, "instance": {"bind_host": "127.0.0.1"}},
    }
    recipe = compile_setup_recipe({"profile_id": "easy_private", "permissions": {"local_memory": False}})
    runner = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const fixture = JSON.parse(fs.readFileSync(0, "utf8"));
let calls = [], failInstall = false;
const sandbox = {
    document: {getElementById: () => null, addEventListener() {}, querySelector: () => null, documentElement: {setAttribute() {}}},
    window: {location: {pathname: "/", hash: "", origin: "http://127.0.0.1"}, addEventListener() {}, setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {}, requestAnimationFrame() {}, confirm: () => true},
    navigator: {}, console, URL, TextEncoder, TextDecoder, setTimeout: () => 0, clearTimeout() {},
    fetch: async (url, options = {}) => {
        const body = options.body ? JSON.parse(options.body) : {};
        calls.push({url, body});
        let data = {success: true};
        if (url === "/api/setup/recipe/apply") data = {bootstrap: fixture.bootstrap, recipe: fixture.recipe};
        if (url === "/api/admin/config") data = {bootstrap: fixture.bootstrap};
        if (url === "/api/admin/bootstrap") data = fixture.bootstrap;
        if (url === "/api/wizard/status") data = {password_changed: true, ollama: {local_models: ["synthetic-model:latest"]}};
        const failed = failInstall && url === "/api/agents/install";
        return {ok: !failed, statusText: "Synthetic install failure", text: async () => JSON.stringify(failed ? {error: "Synthetic install failure"} : data)};
    }
};
sandbox.window.window = sandbox.window;
let source = fs.readFileSync(process.argv[1], "utf8");
source = source.replace(/    boot\(\);\s*\}\(\)\);\s*$/, '    globalThis.admin = {state, initializeForms, updateSetupAnswer, currentSetupAnswers, renderSetupScreen, renderOverview, renderLiveViewScreen, agentMatchesFilter, renderAgentLibraryToolbar, guidedReadiness, applyGuidedSetup, bootGuidedSetup, handleAction};\n}());');
vm.runInNewContext(source, sandbox);
const a = sandbox.admin;
assert(a, "VM must load the real script without calling boot");
a.state.bootstrap = fixture.bootstrap;
a.initializeForms(fixture.bootstrap);
a.state.setup.payload = {password_changed: true, ollama: {local_models: ["synthetic-model:latest"]}};
a.state.setup.answers = JSON.parse(JSON.stringify(fixture.recipe.answers));
for (let step = 0; step < 6; step++) {
    a.state.setup.guidedStep = step;
    assert.match(a.renderSetupScreen(), new RegExp("Step " + (step + 1) + " of 6"));
}
assert.match(a.renderOverview(), /Server name/);
assert(a.renderOverview().indexOf("Server name") < a.renderOverview().indexOf("Service controls"));
assert(!a.renderOverview().includes("Server ID:"));
assert.match(a.renderLiveViewScreen(), /data-disclosure="live-connections"/);
assert(!a.renderLiveViewScreen().includes('data-disclosure="live-connections" open'));
a.updateSetupAnswer("intents", "media_center");
assert(!a.currentSetupAnswers().intents.includes("media_center"));
assert.equal(a.state.setup.applied, false);
a.updateSetupAnswer("intents", "media_center");
a.state.agentCategory = "media";
assert.equal(a.agentMatchesFilter({agent_name: "tv_agent", display_name: "Media Center"}), true);
assert.equal(a.agentMatchesFilter({agent_name: "coding_agent", display_name: "Coding Agent"}), false);
a.state.agentQuery = '<script>alert("synthetic")</script>';
assert(!a.renderAgentLibraryToolbar([]).includes('<script>alert('));
a.state.agentQuery = "";
(async () => {
    await a.handleAction("setup-permission:share_screen");
    assert.equal(a.currentSetupAnswers().permissions.share_screen, true);
    await a.handleAction("setup-permission:share_screen");
    assert.equal(a.currentSetupAnswers().permissions.share_screen, false);
    const before = calls.length;
    a.state.setup.payload.password_changed = false;
    await assert.rejects(a.applyGuidedSetup(), /password/);
    assert.equal(calls.length, before, "Unready setup must not write configuration or agent state");
    a.state.setup.payload.password_changed = true;
    await a.applyGuidedSetup();
    assert.equal(a.state.setup.applied, true);
    assert(calls.some(c => c.url === "/api/agents/install" && c.body.agent_name === "tv_agent"));
    assert(calls.some(c => c.url === "/api/agents/uninstall" && c.body.agent_name === "memory_agent"));
    assert(!calls.some(c => /agents\/(install|uninstall)/.test(c.url) && c.body.agent_name === "coding_agent"));
    assert(!calls.some(c => c.url === "/api/wizard/complete"));
    failInstall = true;
    await assert.rejects(a.applyGuidedSetup(), /Synthetic install failure/);
    assert.equal(a.state.setup.applied, false);
    assert.match(a.state.setup.applyError, /Earlier successful changes are saved/);
    failInstall = false;
    a.state.setup.applied = true;
    fixture.bootstrap.status.ai_agent.status = "Stopped";
    await assert.rejects(a.handleAction("setup-guided-boot"), /AI is not running/);
    assert.equal(a.state.setup.applying, false);
    assert.match(a.state.setup.applyError, /Startup stopped/);
    assert.equal(a.state.setup.progress, "");
    assert(!calls.some(c => c.url === "/api/wizard/complete"), "Failed startup must not complete setup");
    console.log("Guided UI, consent, filters, readiness, partial failures and agent lifecycle passed");
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    result = subprocess.run(
        [node, "-e", runner, str(REPO_ROOT / "assets" / "admin-ui.js")],
        input=json.dumps({"bootstrap": fixture, "recipe": recipe}),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
