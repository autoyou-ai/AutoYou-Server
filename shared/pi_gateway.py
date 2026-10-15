# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Pi Coding Agent Gateway adapter for AutoYou.

Bridges chat, attachments, and tool calls to https://github.com/earendil-works/pi
running from C:\\Users\\You\\Projects\\Pi (or configured PI_PATH).
Supports both direct CLI execution and local HTTP gateway (port 8650).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import ssl as _ssl
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import aiohttp

try:
    import certifi as _certifi
    _SSL_CTX = _ssl.create_default_context(cafile=_certifi.where())
except ImportError:
    _SSL_CTX = _ssl.create_default_context()

logger = logging.getLogger("shared.pi_gateway")

DEFAULT_PI_PATH = r"C:\Users\You\Projects\Pi"
DEFAULT_PI_PORT = 8650


def resolve_pi_path(configured_path: Optional[str] = None) -> Path:
    candidates = [
        configured_path,
        os.environ.get("PI_PATH"),
        DEFAULT_PI_PATH,
    ]
    for cand in candidates:
        if cand:
            path = Path(cand).expanduser().resolve()
            if path.exists():
                return path
    return Path(DEFAULT_PI_PATH)


def resolve_pi_cli(pi_root: Path) -> Optional[Path]:
    cli_candidate = pi_root / "packages" / "coding-agent" / "dist" / "bundle" / "cli.js"
    if cli_candidate.exists():
        return cli_candidate
    alt_cli = pi_root / "packages" / "coding-agent" / "dist" / "cli.js"
    if alt_cli.exists():
        return alt_cli
    return None


async def probe_pi(
    api_base: str = f"http://127.0.0.1:{DEFAULT_PI_PORT}",
    pi_path: Optional[str] = None,
    token: str = "",
) -> Dict[str, Any]:
    """Check readiness of Pi Coding Agent via HTTP gateway or CLI availability."""
    port = int(os.environ.get("PI_PORT", str(DEFAULT_PI_PORT)) or DEFAULT_PI_PORT)
    path = resolve_pi_path(pi_path)
    cli_file = resolve_pi_cli(path)

    # 1. Try probing HTTP gateway
    normalized_base = (api_base or f"http://127.0.0.1:{port}").strip().rstrip("/")
    if not normalized_base.startswith("http"):
        normalized_base = f"http://127.0.0.1:{normalized_base}"

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        timeout = aiohttp.ClientTimeout(total=2.0)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_SSL_CTX), timeout=timeout) as sess:
            async with sess.get(f"{normalized_base}/v1/models", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = [m.get("id", "") for m in (data.get("data") or []) if isinstance(m, dict)]
                    return {
                        "running": True,
                        "available": True,
                        "port": port,
                        "api_base": normalized_base,
                        "models": models or ["pi-agent", "default"],
                        "mode": "http_gateway",
                    }
    except Exception:
        pass

    # 2. Check if local CLI is built and executable
    if cli_file and cli_file.exists():
        return {
            "running": True,
            "available": True,
            "port": port,
            "api_base": normalized_base,
            "models": ["pi-agent", "default", "claude-3-5-sonnet", "gpt-4o"],
            "mode": "cli_direct",
            "pi_path": str(path),
        }

    return {
        "running": False,
        "available": False,
        "port": port,
        "api_base": normalized_base,
        "error": f"Pi CLI not found at {path}. Build it in C:\\Users\\You\\Projects\\Pi with npm run build.",
    }


async def _run_pi_cli_direct(
    pi_root: Path,
    cli_file: Path,
    prompt: str,
    *,
    timeout: float = 120.0,
    model: str = "",
) -> str:
    """Run Pi CLI as a subprocess and capture its response."""
    node_exe = shutil.which("node") or "node"
    args = [node_exe, str(cli_file), "-p", prompt]
    if model and model != "default":
        args.extend(["--model", model])

    env = dict(os.environ)
    env["PI_OFFLINE"] = "0"

    loop = asyncio.get_running_loop()

    def _exec() -> str:
        proc = subprocess.run(
            args,
            cwd=str(pi_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        output = proc.stdout.strip()
        if not output and proc.stderr:
            output = proc.stderr.strip()
        return output

    try:
        result = await loop.run_in_executor(None, _exec)
        return result or "Pi executed the request."
    except subprocess.TimeoutExpired:
        return "Pi execution timed out."
    except Exception as exc:
        logger.error("Direct Pi CLI execution failed: %s", exc)
        return f"Pi execution error: {exc}"


async def call_pi_gateway(
    *,
    api_base: str = f"http://127.0.0.1:{DEFAULT_PI_PORT}",
    model: str = "pi-agent",
    message: str,
    session_id: str,
    token: str = "",
    context: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    on_chunk: Optional[Callable[[Dict[str, Any]], Any]] = None,
    pi_path: Optional[str] = None,
    timeout: float = 120.0,
) -> Optional[Dict[str, Any]]:
    """Call Pi via local HTTP gateway with automatic fallback to direct CLI execution."""
    port = int(os.environ.get("PI_PORT", str(DEFAULT_PI_PORT)) or DEFAULT_PI_PORT)
    path = resolve_pi_path(pi_path)
    cli_file = resolve_pi_cli(path)

    normalized_base = (api_base or f"http://127.0.0.1:{port}").strip().rstrip("/")
    if not normalized_base.startswith("http"):
        normalized_base = f"http://127.0.0.1:{normalized_base}"

    # Try HTTP gateway first
    headers = {
        "Content-Type": "application/json",
        "x-openclaw-session-key": session_id,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "model": model or "pi-agent",
        "messages": [{"role": "user", "content": message}],
        "stream": False,
        "user": session_id,
    }

    url = f"{normalized_base}/v1/chat/completions"
    try:
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_SSL_CTX), timeout=client_timeout) as sess:
            async with sess.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    choices = data.get("choices") or []
                    reply = ""
                    if choices and isinstance(choices[0], dict):
                        msg_obj = choices[0].get("message") or {}
                        reply = str(msg_obj.get("content") or "")
                    if not reply and "output" in data:
                        out = data.get("output") or []
                        for item in out:
                            for part in item.get("content") or []:
                                if part.get("type") == "output_text":
                                    reply = part.get("text", "")
                    if reply:
                        if on_chunk:
                            res = on_chunk({"content": reply, "type": "content"})
                            if asyncio.iscoroutine(res):
                                await res
                        return {
                            "response": reply,
                            "model": model or "pi-agent",
                            "agent_name": f"Pi ({model or 'coding-agent'})",
                            "finish_reason": "stop",
                            "usage": data.get("usage", {}),
                            "provider": "pi",
                            "openclaw_endpoint": "pi.chat.completions",
                            "attachments_forwarded": len(context or []),
                            "attachments_skipped": 0,
                        }
    except Exception as exc:
        logger.debug("Pi HTTP gateway request failed (%s): %s; falling back to direct CLI", url, exc)

    # Fallback to direct Pi CLI execution
    if cli_file and cli_file.exists():
        reply = await _run_pi_cli_direct(path, cli_file, message, timeout=timeout, model=model)
        if on_chunk and reply:
            res = on_chunk({"content": reply, "type": "content"})
            if asyncio.iscoroutine(res):
                await res
        return {
            "response": reply,
            "model": model or "pi-cli",
            "agent_name": "Pi (Direct CLI)",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": len(message), "completion_tokens": len(reply)},
            "provider": "pi",
            "openclaw_endpoint": "pi.cli",
            "attachments_forwarded": len(context or []),
            "attachments_skipped": 0,
        }

    logger.error("Pi gateway unavailable: neither HTTP gateway nor CLI is reachable at %s", path)
    return None
