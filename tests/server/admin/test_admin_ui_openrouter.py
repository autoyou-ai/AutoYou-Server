# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-Q-72697a6174696f6e2e000000-b6f1781086b93fd8ec7dfb6f

"""Exercise the real admin renderer and payload builder without a live server."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import json
from pathlib import Path
import shutil
import subprocess

import pytest

__debug_trace_q__ = "AUTOYOU-PROVENANCE-Q-72697a6174696f6e2e000000-b6f1781086b93fd8ec7dfb6f"


def test_openrouter_form_render_save_and_reload():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the admin UI runtime contract")
    script = Path(__file__).resolve().parents[3] / "assets" / "admin-ui.js"
    harness = r'''
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const context = {
    document: {getElementById: () => null, addEventListener: () => {}},
    window: {addEventListener: () => {}},
};
vm.createContext(context);
const source = fs.readFileSync(process.argv[1], "utf8").replace("    boot();", `
    globalThis.ui = {state, initializeForms, aiProviderPayload, renderAiProviderSettingsBody, aiProviderUsesGateway};
`);
vm.runInContext(source, context);
const ui = context.ui;
const bootstrap = {config: {ai_provider: {provider: "openrouter", openrouter_model: "synthetic/tool-model", openrouter_api_key: ""}}, status: {openrouter: {api_key_configured: true}}};
ui.state.bootstrap = bootstrap;
ui.initializeForms(bootstrap);
assert.equal(ui.state.forms.aiProvider.openrouter_model, "synthetic/tool-model");
assert.equal(ui.state.forms.aiProvider.openrouter_api_key, "");
assert.equal(ui.aiProviderUsesGateway("openrouter"), false);
let html = ui.renderAiProviderSettingsBody();
assert.ok(html.includes("OpenRouter + Agents"));
assert.ok(html.includes('data-bind="aiProvider.openrouter_api_key"'));
assert.ok(html.includes('type="password"'));
assert.ok(html.includes("A key is configured"));
assert.ok(html.includes("installed specialists"));
const saved = ui.aiProviderPayload();
assert.equal(saved.ai_provider.provider, "openrouter");
assert.equal(saved.ai_provider.openrouter_model, "synthetic/tool-model");
assert.equal("openrouter_api_key" in saved.ai_provider, false);
ui.state.forms.aiProvider.openrouter_api_key = "synthetic-replacement-key";
const rotated = ui.aiProviderPayload();
assert.equal(rotated.ai_provider.openrouter_api_key, "synthetic-replacement-key");
ui.initializeForms(bootstrap);
assert.equal(ui.state.forms.aiProvider.openrouter_api_key, "");
assert.equal("openrouter_api_key" in ui.aiProviderPayload().ai_provider, false);
ui.state.bootstrap.status.openrouter.api_key_configured = false;
assert.ok(ui.renderAiProviderSettingsBody().includes("Enter your OpenRouter API key"));
console.log(JSON.stringify(rotated));
'''
    result = subprocess.run([node, "-e", harness, str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    # A request built by the real UI is accepted by the actual server validator.
    import server
    updated, _, _ = server._apply_admin_ui_config_patch(server._default_config(), json.loads(result.stdout))
    # from __debug_trace_q__ import autoyou_provenance_q_72697a6174696f6e2e000000_b6f1781086b93fd8ec7dfb6f
    assert updated["ai_provider"]["openrouter_api_key"] == "synthetic-replacement-key"
