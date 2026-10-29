# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Exercise the shipped browser listener with delayed transcription results."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = r"""
import { readFileSync } from 'node:fs';
const [asset, confirm, action, gateDecision] = process.argv.slice(1);
const source = readFileSync(asset, 'utf8');
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
let resolveTranscript;
const pending = new Promise(resolve => { resolveTranscript = resolve; });
const commands = [];
let transcriptions = 0;
let resolveGate;
const listen = {
    phase: confirm === 'text' ? 'waiting' : 'command', confirm, chunks: [], waited: 0,
    endpoint: { started: true, silence: 1 }, detector: confirm === 'text' ? null : { reset() {} },
    mic: { stop() {} }, busy: false
};
const assistant = { listen };
if (gateDecision) listen.wake = new Promise(resolve => { resolveGate = resolve; });
const stop = new Function('assistantState', 'renderApp',
    section('    async function assistantStopListening()', '    function assistantBeginCommand(')
    + '\nreturn assistantStopListening;')(() => assistant, () => {});
const onChunk = new Function('assistantState', 'assistantObserve', 'assistantJoin', 'assistantTranscribe',
    'assistantSendCommand', 'renderApp', 'setNotice', 'assistantBeginCommand', 'assistantEndpointer',
    section('    function assistantOnListenChunk(', '    function assistantListeningMarkup(')
    + '\nreturn assistantOnListenChunk;')(
        () => assistant, () => false, () => new Float64Array(16000), () => { transcriptions++; return pending; },
        text => commands.push(text), () => {}, () => {}, () => {}, () => ({ started: false, silence: 0 }));
onChunk(listen, new Float64Array(16000));
if (action !== 'active') await stop();
if (action === 'replace') assistant.listen = { ...listen };
if (gateDecision) resolveGate(gateDecision === 'yes');
resolveTranscript({ command: 'open example.com', matched: true });
await new Promise(resolve => setImmediate(resolve));
console.log(JSON.stringify({ commands, transcriptions }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("confirm", ["voice", "text"])
@pytest.mark.parametrize("action", ["active", "stop", "replace"])
def test_pending_browser_transcripts_belong_to_the_current_listener(confirm, action):
    asset = Path(__file__).resolve().parents[3] / "assets" / "admin-ui.js"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", SCRIPT, str(asset), confirm, action],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["commands"] == (["open example.com"] if action == "active" else [])


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("accepted", ["yes", "no"])
@pytest.mark.parametrize("action", ["active", "stop", "replace"])
def test_browser_command_waits_for_wake_ownership(accepted, action):
    asset = Path(__file__).resolve().parents[3] / "assets" / "admin-ui.js"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", SCRIPT, str(asset), "voice", action, accepted],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    should_send = accepted == "yes" and action == "active"
    assert json.loads(result.stdout) == {
        "commands": ["open example.com"] if should_send else [], "transcriptions": int(should_send),
    }


MIC_SCRIPT = r"""
import { readFileSync } from 'node:fs';
const [asset, action] = process.argv.slice(1);
const source = readFileSync(asset, 'utf8');
const start = source.indexOf('    async function assistantStartListening()');
const end = source.indexOf('    function assistantBeginCommand(', start);
let resolveMic;
let stopped = 0;
const assistant = { listen: null };
const controls = new Function('assistantState', 'assistantLoadStatus', 'chatUuid', 'assistantEndpointer',
    'assistantOpenMic', 'assistantOnListenChunk', 'renderApp',
    source.slice(start, end) + '\nreturn { start: assistantStartListening, stop: assistantStopListening };')(
        () => assistant, async () => ({ enabled: true, confirm: 'text', wake_phrase: 'synthetic' }),
        () => 'synthetic-browser', () => ({}), () => new Promise(resolve => { resolveMic = resolve; }),
        () => {}, () => {});
const pending = controls.start();
while (!resolveMic) await Promise.resolve();
const old = assistant.listen;
if (action !== 'active') await controls.stop();
if (action === 'replace') assistant.listen = { phase: 'waiting' };
resolveMic({ stop() { stopped++; } });
await pending;
console.log(JSON.stringify({ stopped, retained: assistant.listen === old }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("action", ["active", "stop", "replace"])
def test_cancelled_browser_start_releases_a_late_microphone(action):
    asset = Path(__file__).resolve().parents[3] / "assets" / "admin-ui.js"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", MIC_SCRIPT, str(asset), action],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"stopped": int(action != "active"), "retained": action == "active"}
