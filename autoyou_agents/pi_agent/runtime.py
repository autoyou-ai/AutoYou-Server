# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Run Pi with the same scoped tools and execution guards as Coding Agent.

Pi's own file/shell tools and automatically discovered extensions are disabled.
The short-lived loopback bridge exports only AutoYou's existing workspace tools.
Conversation state stays with AutoYou; Pi is invoked with --no-session.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args, get_type_hints

from autoyou_agents.shared_tools import workspace_tools
from autoyou_agents.shared_tools._subprocess_env import scrubbed_subprocess_env
from shared.process_lifecycle import process_spawn_kwargs, force_kill_process_tree
from shared.pi_gateway import resolve_pi_node


TOOL_NAMES = (
    "get_workspace_root", "list_workspace", "search_workspace", "read_file",
    "write_file", "replace_text", "insert_before", "insert_after", "create_directory",
    "run_command", "git_status", "git_diff",
)


def tool_definitions() -> list[dict[str, Any]]:
    definitions = []
    for name in TOOL_NAMES:
        function = getattr(workspace_tools, name)
        hints = get_type_hints(function)
        properties, required = {}, []
        for parameter in inspect.signature(function).parameters.values():
            annotation = hints.get(parameter.name, str)
            types = get_args(annotation) or (annotation,)
            actual = next((item for item in types if item is not type(None)), str)
            schema = {"type": {int: "integer", float: "number", bool: "boolean"}.get(actual, "string")}
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter.name)
            elif parameter.default is not None:
                schema["default"] = parameter.default
            properties[parameter.name] = schema
        definitions.append({
            "name": name, "description": inspect.getdoc(function) or name,
            "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        })
    return definitions


class WorkspaceBridge:
    def __init__(self, callback_context: Any = None, *, read_only: bool = False):
        self.context = callback_context
        self.loop = asyncio.get_running_loop()
        self.token = secrets.token_urlsafe(32)
        self.closed = threading.Event()
        self.call_lock = threading.Lock()
        self.discovered = False
        self.allowed_tools = frozenset(TOOL_NAMES if not read_only else ("get_workspace_root", "list_workspace", "search_workspace", "read_file", "git_status", "git_diff"))
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_json(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                return not bridge.closed.is_set() and secrets.compare_digest(
                    self.headers.get("Authorization", ""), f"Bearer {bridge.token}"
                )

            def do_GET(self):
                if not self.authorized():
                    return self.send_json(403, {"status": "error"})
                if self.path != "/tools":
                    return self.send_json(404, {"status": "error"})
                bridge.discovered = True
                self.send_json(200, [tool for tool in tool_definitions() if tool["name"] in bridge.allowed_tools])

            def do_POST(self):
                if not self.authorized():
                    return self.send_json(403, {"status": "error"})
                name = self.path.removeprefix("/call/")
                if not self.path.startswith("/call/") or name not in bridge.allowed_tools:
                    return self.send_json(404, {"status": "error"})
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size < 0 or size > 1_100_000:
                        return self.send_json(413, {"status": "error"})
                    args = json.loads(self.rfile.read(size))
                    if not isinstance(args, dict):
                        raise ValueError("Tool arguments must be an object")
                    with bridge.call_lock:
                        if bridge.closed.is_set():
                            return self.send_json(409, {"status": "error", "message": "Coding turn ended"})
                        result = bridge.call(name, args)
                    self.send_json(200, result)
                except (ValueError, TypeError) as exc:
                    self.send_json(400, {"status": "error", "message": str(exc)})
                except Exception:
                    self.send_json(500, {"status": "error", "message": "Workspace tool failed"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)

    def start(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def call(self, name, args):
        if self.context is not None:
            from autoyou_agents.coding_agent.agent import _coding_before_tool_callback, _coding_after_tool_callback
            tool = SimpleNamespace(name=name)
            future = asyncio.run_coroutine_threadsafe(_coding_before_tool_callback(tool, args, self.context), self.loop)
            blocked = future.result(timeout=10)
            if blocked is not None:
                return blocked
        if name == "run_command":
            result = workspace_tools._run_command_with_cancel(**args, cancel_event=self.closed)
        else:
            result = getattr(workspace_tools, name)(**args)
        if self.context is not None:
            future = asyncio.run_coroutine_threadsafe(_coding_after_tool_callback(tool, args, self.context, result), self.loop)
            result = future.result(timeout=10) or result
        return result

    async def close(self):
        self.closed.set()
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        drained = await asyncio.to_thread(self.call_lock.acquire, True, 15)
        if drained:
            self.call_lock.release()
        return drained


async def run_pi(
    cli_file: Path, prompt: str, *, model: str = "", timeout: float = 180,
    callback_context: Any = None, instruction: str = "",
    read_only: bool = False,
    local_ollama_base: str = "",
) -> dict[str, Any]:
    outcome: dict[str, Any] = {}

    def finish(**fields):
        outcome.update(fields)
        return outcome

    node = resolve_pi_node()
    if not node:
        return finish(ok=False, started=False, error="Node.js is unavailable for Pi.")
    try:
        bridge = WorkspaceBridge(callback_context, read_only=read_only)
    except OSError:
        return finish(ok=False, started=False, error="The local Pi workspace bridge could not start.")
    env = scrubbed_subprocess_env()
    env["AUTOYOU_PI_TOOL_URL"] = bridge.start()
    env["AUTOYOU_PI_TOOL_TOKEN"] = bridge.token
    # Preserve the model's provider credentials only through Pi's own profile.
    # AutoYou's server/admin credentials never reach the Node child.
    args = [node, str(cli_file), "--print", "--mode", "json", "--offline", "--no-session", "--no-builtin-tools",
            "--no-extensions", "--no-mcp", "--no-skills", "--no-prompt-templates", "--no-context-files",
            "--extension", str(Path(__file__).with_name("runtime_tools.mjs"))]
    selected = (model or os.getenv("PI_MODEL", "")).strip()
    if selected and selected not in {"default", "pi-agent"}:
        if "/" in selected and not local_ollama_base:
            args.extend(["--model", selected])
        else:
            args.extend(["--provider", "ollama", "--model", selected])
    if instruction:
        args.extend(["--append-system-prompt", instruction])
    process = None
    temporary_profile = None
    try:
        if local_ollama_base:
            # A fresh installation need not have Pi's custom Ollama provider.
            # Use only the already-selected AutoYou local endpoint/model. Never
            # copy Pi auth.json or write its regular profile/session directory.
            temporary_profile = tempfile.TemporaryDirectory(prefix="autoyou-pi-profile-", dir=os.getenv("AUTOYOU_TEST_ROOT") or None)
            profile = Path(temporary_profile.name)
            endpoint = local_ollama_base.rstrip("/")
            if not endpoint.endswith("/v1"):
                endpoint += "/v1"
            (profile / "models.json").write_text(json.dumps({"providers": {"ollama": {
                "baseUrl": endpoint, "api": "openai-completions", "apiKey": "ollama",
                "models": [{"id": selected, "contextWindow": 32768, "maxTokens": 4096}],
            }}}), encoding="utf-8")
            env["PI_CODING_AGENT_DIR"] = str(profile)
        process = await asyncio.create_subprocess_exec(
            *args, cwd=str(workspace_tools._workspace_root()), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=2_000_000, **process_spawn_kwargs(hide_window=True),
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(prompt.encode("utf-8")), timeout=timeout)
        if process.returncode != 0:
            return finish(ok=False, started=True, error=f"Pi exited with code {process.returncode}.", exit_code=process.returncode)
        if not bridge.discovered:
            return finish(ok=False, started=True, error="Pi did not load the AutoYou workspace tool bridge.")
        final = None
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            message = event.get("message", {})
            if event.get("type") == "message_end" and message.get("role") == "assistant":
                final = message
        if not final or final.get("stopReason") != "stop":
            return finish(ok=False, started=True, error="Pi did not finish the coding turn successfully.")
        reply = "\n".join(part.get("text", "") for part in final.get("content", []) if part.get("type") == "text").strip()
        if not reply:
            return finish(ok=False, started=True, error="Pi returned no final text response.")
        return finish(ok=True, started=True, response=reply, usage=final.get("usage", {}))
    except asyncio.TimeoutError:
        return finish(ok=False, started=process is not None, error="Pi coding turn timed out. Review workspace changes before retrying.", resumable=True)
    except OSError:
        return finish(ok=False, started=process is not None, error="Pi could not start. Check the installed runtime.")
    finally:
        bridge.closed.set()
        if process is not None and process.returncode is None:
            await asyncio.to_thread(force_kill_process_tree, process.pid, process_group=True)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (ProcessLookupError, asyncio.TimeoutError):
                    outcome.update(ok=False, started=True, error="Pi process cleanup could not be confirmed. Review running processes before retrying.")
        if not await bridge.close():
            outcome.update(ok=False, started=True, error="A workspace tool has not finished stopping. Wait for it to finish before retrying.")
        if temporary_profile is not None:
            temporary_profile.cleanup()
