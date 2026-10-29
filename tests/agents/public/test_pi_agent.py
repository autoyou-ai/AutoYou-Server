# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Pi routing and real subprocess/bridge contracts, isolated from live state."""
import json
import asyncio
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai import types

from autoyou_agents.pi_agent import agent as pi_agent
from autoyou_agents.pi_agent.runtime import run_pi, tool_definitions
from autoyou_agents.shared_tools.coding_handoff import CODING_HANDOFF_STATE_KEY
from autoyou_agents.shared_tools import conversation_context


def context(state=None):
    return SimpleNamespace(state=state if state is not None else {}, invocation_id="synthetic-turn",
                           user_content=types.Content(role="user", parts=[types.Part(text="Fix the sample test")]))


@pytest.fixture(autouse=True)
def isolate_callback_preflight(monkeypatch):
    # These tests exercise dispatch/state contracts. Readiness has its own
    # isolated endpoint and install-registry tests in test_pi_agent_readiness.
    async def checked_status():
        return pi_agent.pi_cli_status()
    monkeypatch.setattr(pi_agent, "pi_runtime_status", checked_status)


@pytest.mark.asyncio
async def test_auto_uses_builtin_only_before_pi_is_available(monkeypatch):
    monkeypatch.setenv("AUTOYOU_CODING_BACKEND", "auto")
    monkeypatch.setattr(pi_agent, "pi_cli_status", lambda: {"available": False})
    assert await pi_agent.forward_coding_to_pi(context()) is None
    forced = await pi_agent.forward_coding_to_pi(context(), force=True)
    assert "Pi is unavailable" in forced.parts[0].text


@pytest.mark.asyncio
async def test_builtin_never_probes_or_starts_pi(monkeypatch):
    monkeypatch.setenv("AUTOYOU_CODING_BACKEND", "builtin")
    monkeypatch.setattr(pi_agent, "pi_cli_status", lambda: pytest.fail("Pi must not be probed"))
    assert await pi_agent.forward_coding_to_pi(context()) is None


@pytest.mark.asyncio
async def test_pi_failure_is_not_replayed_in_auto_mode(monkeypatch):
    monkeypatch.setenv("AUTOYOU_CODING_BACKEND", "auto")
    monkeypatch.setattr(pi_agent, "pi_cli_status", lambda: {"available": True, "cli": Path("synthetic-cli.js")})
    execute = AsyncMock(return_value={"ok": False, "started": True, "error": "Synthetic timeout"})
    monkeypatch.setattr(pi_agent, "run_pi", execute)
    ctx = context()
    reply = await pi_agent.forward_coding_to_pi(ctx)
    assert "not replayed" in reply.parts[0].text
    assert ctx.state["coding_backend_last_result"] == {"backend": "pi", "ok": False, "started": True}
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_falls_back_if_pi_never_started(monkeypatch):
    monkeypatch.setenv("AUTOYOU_CODING_BACKEND", "auto")
    monkeypatch.setattr(pi_agent, "pi_cli_status", lambda: {"available": True, "cli": Path("synthetic-cli.js")})
    monkeypatch.setattr(pi_agent, "run_pi", AsyncMock(return_value={"ok": False, "started": False, "error": "Synthetic startup failure"}))
    assert await pi_agent.forward_coding_to_pi(context()) is None
    forced = await pi_agent.forward_coding_to_pi(context(), force=True)
    assert forced is not None


@pytest.mark.asyncio
async def test_pi_respects_remaining_coding_turn_budget(monkeypatch):
    import time
    from shared.session_execution import SESSION_CONTROL_STATE_KEY
    monkeypatch.setenv("AUTOYOU_CODING_BACKEND", "auto")
    monkeypatch.setenv("AUTOYOU_PI_TIMEOUT_SECONDS", "180")
    monkeypatch.setattr(pi_agent, "pi_cli_status", lambda: {"available": True, "cli": Path("synthetic-cli.js")})
    execute = AsyncMock(return_value={"ok": True, "response": "Synthetic result"})
    monkeypatch.setattr(pi_agent, "run_pi", execute)
    ctx = context({SESSION_CONTROL_STATE_KEY: {"turn_started_at": time.time() - 20, "turn_timeout_seconds": 30}})
    await pi_agent.forward_coding_to_pi(ctx)
    assert 0 < execute.await_args.kwargs["timeout"] <= 10


@pytest.mark.asyncio
async def test_pi_preserves_handoff_history_and_root_context(monkeypatch):
    monkeypatch.setenv("AUTOYOU_CODING_BACKEND", "auto")
    monkeypatch.setattr(pi_agent, "pi_cli_status", lambda: {"available": True, "cli": Path("synthetic-cli.js")})
    execute = AsyncMock(return_value={"ok": True, "started": True, "response": "Verified sample fix"})
    monkeypatch.setattr(pi_agent, "run_pi", execute)
    ctx = context({
        CODING_HANDOFF_STATE_KEY: {"task": "Implement the previous requested sample"},
        "pi_coding_history": [{"user": "Prior sample request", "assistant": "Prior sample result"}],
        conversation_context.context_ref_state_key("autoyou_coding_agent"): conversation_context.stash_context_block("Synthetic root context"),
    })
    await pi_agent.forward_coding_to_pi(ctx)
    prompt = execute.await_args.args[1]
    assert "Prior sample request" in prompt
    assert "Implement the previous requested sample" in prompt
    assert "Synthetic root context" in prompt
    assert ctx.state[CODING_HANDOFF_STATE_KEY] is None
    assert ctx.state["pi_coding_history"][-1]["assistant"] == "Verified sample fix"


@pytest.mark.asyncio
async def test_failed_turn_cannot_preserve_another_conversations_history(monkeypatch):
    from shared.adk_state import AUTOYOU_CONVERSATION_SESSION_STATE_KEY
    monkeypatch.setenv("AUTOYOU_CODING_BACKEND", "pi")
    monkeypatch.setattr(pi_agent, "pi_cli_status", lambda: {"available": True, "cli": Path("synthetic-cli.js")})
    execute = AsyncMock(return_value={"ok": False, "started": True, "error": "Synthetic failure"})
    monkeypatch.setattr(pi_agent, "run_pi", execute)
    ctx = context({AUTOYOU_CONVERSATION_SESSION_STATE_KEY: "synthetic-new-conversation",
                   "pi_coding_history_scope": "synthetic-other-scope",
                   "pi_coding_history": [{"user": "other-conversation-private-marker", "assistant": "other result"}]})
    await pi_agent.forward_coding_to_pi(ctx)
    await pi_agent.forward_coding_to_pi(ctx)
    assert ctx.state["pi_coding_history"] == []
    assert all("other-conversation-private-marker" not in call.args[1] for call in execute.await_args_list)


def test_pi_tools_use_existing_workspace_contracts():
    tools = {item["name"]: item for item in tool_definitions()}
    assert "run_command" in tools
    assert "read_file" in tools
    assert "delete_path" not in tools
    assert "move_path" not in tools
    assert tools["write_file"]["parameters"]["required"] == ["path", "content"]


@pytest.mark.parametrize("configured", ["", "default", "pi-agent"])
def test_coding_pi_inherits_current_ollama_model(monkeypatch, configured):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "synthetic-local:small")
    monkeypatch.setenv("PI_MODEL", configured)
    assert pi_agent.pi_coding_model() == "synthetic-local:small"
    monkeypatch.setenv("PI_MODEL", "synthetic-override:small")
    assert pi_agent.pi_coding_model() == "synthetic-override:small"
    monkeypatch.setenv("AI_PROVIDER", "pi")
    monkeypatch.setenv("PI_MODEL", configured)
    assert pi_agent.pi_coding_model() == configured


@pytest.mark.asyncio
async def test_real_node_bridge_confines_files_and_commands(monkeypatch, tmp_path):
    bundled_node = shutil.which("node")
    if not bundled_node:
        pytest.skip("Node.js is optional")
    monkeypatch.setenv("AUTOYOU_NODE_EXE", bundled_node)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("AUTOYOU_ENABLE_AGENT_RUN_COMMAND", raising=False)
    cli = tmp_path / "synthetic-cli.mjs"
    cli.write_text("""
const args = process.argv.slice(2);
for (const flag of ['--no-session', '--no-builtin-tools', '--no-extensions', '--no-mcp']) {
  if (!args.includes(flag)) process.exit(2);
}
if (args.includes('--no-approve')) process.exit(3);
let input = ''; for await (const chunk of process.stdin) input += chunk;
const base = process.env.AUTOYOU_PI_TOOL_URL;
const headers = {Authorization: 'Bearer ' + process.env.AUTOYOU_PI_TOOL_TOKEN, 'Content-Type': 'application/json'};
await fetch(base + '/tools', {headers});
const call = async (name, body) => (await fetch(base + '/call/' + name, {method:'POST', headers, body:JSON.stringify(body)})).json();
const wrote = await call('write_file', {path:'sample.txt',content:input});
const escaped = await call('write_file', {path:'../outside.txt',content:'must fail'});
const command = await call('run_command', {command:'echo synthetic'});
const text = JSON.stringify({wrote,escaped,command,cwd:process.cwd()});
console.log(JSON.stringify({type:'message_end',message:{role:'assistant',stopReason:'stop',content:[{type:'text',text}]}}));
""", encoding="utf-8")
    result = await run_pi(cli, "synthetic stdin prompt", timeout=15)
    assert result["ok"]
    response = json.loads(result["response"])
    assert response["wrote"]["status"] == "success"
    assert response["escaped"]["status"] == "error"
    assert response["command"]["status"] == "error"
    assert Path(response["cwd"]) == tmp_path
    assert (tmp_path / "sample.txt").read_text() == "synthetic stdin prompt"
    assert not (tmp_path.parent / "outside.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code,stop_reason", [(1, "stop"), (0, "error"), (0, "length")])
async def test_real_cli_failure_never_becomes_success(monkeypatch, tmp_path, exit_code, stop_reason):
    if not shutil.which("node"):
        pytest.skip("Node.js is optional")
    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    cli = tmp_path / "synthetic-cli.mjs"
    event = {"type": "message_end", "message": {"role": "assistant", "stopReason": stop_reason, "content": [{"type": "text", "text": "Not a successful result"}]}}
    cli.write_text("process.stdin.resume(); process.stdin.on('end', () => {console.log(" + json.dumps(json.dumps(event)) + "); process.exit(" + str(exit_code) + ");});", encoding="utf-8")
    result = await run_pi(cli, "synthetic request", timeout=10)
    assert not result["ok"]
    assert result["started"]


@pytest.mark.asyncio
async def test_cli_timeout_stops_process(monkeypatch, tmp_path):
    if not shutil.which("node"):
        pytest.skip("Node.js is optional")
    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    cli = tmp_path / "synthetic-cli.mjs"
    cli.write_text("setInterval(() => {}, 1000);", encoding="utf-8")
    result = await run_pi(cli, "synthetic request", timeout=0.15)
    assert not result["ok"]
    assert "timed out" in result["error"]


@pytest.mark.asyncio
async def test_coding_cancellation_stops_active_command(monkeypatch, tmp_path):
    from autoyou_agents.shared_tools.workspace_tools import _run_command_with_cancel
    if not shutil.which("node"):
        pytest.skip("Node.js is optional")
    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_ENABLE_AGENT_RUN_COMMAND", "1")
    sleeper = tmp_path / "synthetic-delay.mjs"
    sleeper.write_text("setTimeout(() => {}, 30000);", encoding="utf-8")
    cancel = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_run_command_with_cancel, 'node "synthetic-delay.mjs"', ".", 30, cancel))
    await asyncio.sleep(0.3)
    cancel.set()
    result = await asyncio.wait_for(task, timeout=10)
    assert result["status"] == "error", result
    assert result.get("cancelled") is True, (result.get("stderr"), result.get("stdout"), result.get("exit_code"))


def test_pi_extension_is_in_packaged_runtime_plan():
    from scripts.build_packaged_runtime_modules import _iter_agent_asset_files
    server_root = Path(__file__).resolve().parents[3]
    assert Path("autoyou_agents/pi_agent/runtime_tools.mjs") in set(_iter_agent_asset_files(server_root))


@pytest.mark.asyncio
async def test_installed_pi_uses_synthetic_ollama_and_autoyou_tools(monkeypatch, tmp_path):
    """Exercise the actual installed Pi with no real provider or credentials."""
    installed = pi_agent.resolve_pi_cli(pi_agent.resolve_pi_path())
    if installed is None or not shutil.which("node"):
        pytest.skip("Installed Pi and Node.js are optional")
    calls = []

    class SyntheticOllama(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            if len(calls) == 1:
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_synthetic", "type": "function", "function": {"name": "write_file", "arguments": json.dumps({"path": "pi-proof.txt", "content": "synthetic proof"})}}]}
                reason = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "Synthetic Pi bridge verified"}
                reason = "stop"
            for event in [
                {"id": "chatcmpl-synthetic", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": "chatcmpl-synthetic", "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]},
            ]:
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticOllama)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    profile = tmp_path / "pi-profile"
    profile.mkdir()
    # A new machine has no custom Pi Ollama provider. Its existing profile and
    # credentials must remain untouched while the per-turn profile is used.
    original_profile = json.dumps({"providers": {}})
    (profile / "models.json").write_text(original_profile, encoding="utf-8")
    (profile / "auth.json").write_text('{"synthetic": "must-not-be-copied"}', encoding="utf-8")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(profile))
    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    try:
        result = await run_pi(installed, "Write the synthetic proof and report the outcome.", model="synthetic", timeout=35,
                              local_ollama_base=f"http://127.0.0.1:{endpoint.server_port}")
    finally:
        endpoint.shutdown()
        endpoint.server_close()
    assert result.get("ok"), result
    assert result["response"] == "Synthetic Pi bridge verified"
    assert (tmp_path / "pi-proof.txt").read_text() == "synthetic proof"
    assert len(calls) == 2
    names = {item["function"]["name"] for item in calls[0]["tools"]}
    assert "write_file" in names
    assert "run_command" in names
    assert "bash" not in names
    assert "delete_path" not in names
    assert not list(profile.rglob("*.jsonl"))
    assert (profile / "models.json").read_text() == original_profile
    assert (profile / "auth.json").read_text() == '{"synthetic": "must-not-be-copied"}'
