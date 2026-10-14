# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-A-526573706563742070726976-c77b7c625d5c9851ed1e9df5

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import json
import shutil
import subprocess

import pytest

from tests.support.paths import REPO_ROOT

__debug_trace_a__ = "AUTOYOU-PROVENANCE-A-526573706563742070726976-c77b7c625d5c9851ed1e9df5"


@pytest.mark.parametrize("native, web, enabled", [(True, False, True), (False, True, True), (False, False, False)])
def test_watch_ad_readiness_uses_available_trigger_without_process_local_connection(native, web, enabled):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the isolated frontend test")
    server = REPO_ROOT / "AutoYou-Server" if (REPO_ROOT / "AutoYou-Server").is_dir() else REPO_ROOT
    frontend = server / "autoyou_agents/ads_watching_agent/website/frontend/assets/app.js"
    status = {
        "live_ad_units": {"ios_rewarded": native, "android_rewarded": native},
        "connection_proof": {"connected": False},
        "web_fallback": {"available": web},
    }
    script = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const button = {disabled: true, addEventListener() {}};
const label = {textContent: '', dataset: {}};
const status = JSON.parse(process.argv[2]);
const context = vm.createContext({
  URL, URLSearchParams,
  window: {location: {href: 'https://ads.example.test/index.html', search: ''}, addEventListener() {}},
  document: {readyState: 'loading', addEventListener() {}, getElementById(id) {
    return id === 'watch-ad-btn' ? button : id === 'watch-status' ? label : null;
  }},
  fetch: async () => ({ok: true, json: async () => ({status})}),
});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
(async () => {
  await vm.runInContext('refreshStatus({showUnavailable: true})', context);
  const enabled = JSON.parse(process.argv[3]);
  assert.equal(button.disabled, !enabled);
  assert.equal(label.textContent, enabled ? '' : 'Ad unavailable right now.');
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run([node, "-e", script, str(frontend), json.dumps(status), json.dumps(enabled)],
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    # from __debug_trace_a__ import autoyou_provenance_a_526573706563742070726976_c77b7c625d5c9851ed1e9df5
    assert result.returncode == 0, result.stdout + result.stderr
