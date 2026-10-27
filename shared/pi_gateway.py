# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Pi Coding Agent Gateway adapter for AutoYou.

Official integration for Pi (https://pi.dev / https://github.com/earendil-works/pi).
Supports both direct CLI execution and local HTTP gateway (default port 8650).
Auto-detects the Pi installation directory across standard workspace locations
or configured PI_PATH environment variable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import ssl as _ssl
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import aiohttp

try:
    import certifi as _certifi
    _SSL_CTX = _ssl.create_default_context(cafile=_certifi.where())
except ImportError:
    _SSL_CTX = _ssl.create_default_context()

logger = logging.getLogger("shared.pi_gateway")

DEFAULT_PI_PORT = 8650


def resolve_pi_node() -> Optional[str]:
    """Use the native host's bundled Node before consulting the developer PATH."""
    configured = (os.getenv("AUTOYOU_NODE_EXE") or "").strip()
    if configured:
        candidate = Path(os.path.expandvars(configured)).expanduser()
        return str(candidate.resolve()) if candidate.is_file() else None
    return shutil.which("node")


def managed_pi_path() -> Path:
    """The per-user npm prefix populated by Windows Easy Setup."""
    roaming = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    return roaming / "AutoYou" / "tools" / "pi"


def _bounded_text_context(context: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    """Keep prior text turns in order without forwarding binary attachments."""
    turns: List[Dict[str, str]] = []
    remaining = 16000
    for item in reversed((context or [])[-12:]):
        if not isinstance(item, dict):
            continue
        content = item.get("content", item.get("text", ""))
        if isinstance(content, list):
            content = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") in {"text", "input_text", "output_text"})
        if not isinstance(content, str) or not content.strip():
            continue
        text = content[:min(4000, remaining)]
        role = "assistant" if item.get("role") in {"assistant", "model"} else "user"
        turns.append({"role": role, "content": text})
        remaining -= len(text)
        if remaining <= 0:
            break
    return list(reversed(turns))


def get_default_pi_path() -> str:
    """Return the preferred default path for Pi Coding Agent (pi.dev), cross-platform."""
    env_path = (os.environ.get("PI_PATH") or "").strip()
    if env_path:
        return env_path

    repo_root = Path(__file__).resolve().parent.parent
    candidates = [
        managed_pi_path(),
        repo_root.parent / "Pi",
        repo_root.parent / "pi",
        Path.home() / "Projects" / "Pi",
        Path.home() / "Projects" / "pi",
        Path.home() / "pi",
        Path.home() / ".pi",
    ]
    for cand in candidates:
        try:
            if cand.exists():
                return str(cand.resolve())
        except Exception:
            continue

    return str(Path.home() / "Projects" / "Pi")


def resolve_pi_path(configured_path: Optional[str] = None) -> Path:
    """Resolve the Pi directory dynamically across platforms with multiple fallbacks."""
    candidates = [
        configured_path,
        os.environ.get("PI_PATH"),
    ]
    for cand in candidates:
        if cand and str(cand).strip():
            try:
                p = Path(os.path.expandvars(str(cand))).expanduser().resolve()
                return p
            except Exception:
                pass

    default_str = get_default_pi_path()
    try:
        p = Path(default_str).expanduser().resolve()
        if p.exists():
            return p
    except Exception:
        pass

    if configured_path and str(configured_path).strip():
        return Path(configured_path).expanduser().resolve()

    return Path(default_str)


def resolve_pi_cli(pi_root: Path) -> Optional[Path]:
    if pi_root.is_file() and pi_root.suffix.lower() in {".js", ".mjs"}:
        return pi_root
    for relative in (
        "packages/coding-agent/dist/bundle/cli.js", "packages/coding-agent/dist/cli.js",
        "node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js",
        "node_modules/@earendil-works/pi-coding-agent/dist/cli.js",
        "@earendil-works/pi-coding-agent/dist/bundle/cli.js",
        "@earendil-works/pi-coding-agent/dist/cli.js",
        "dist/bundle/cli.js", "dist/cli.js",
    ):
        candidate = pi_root / relative
        if candidate.is_file():
            return candidate
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
    if cli_file and cli_file.exists() and resolve_pi_node():
        return {
            "running": True,
            "available": True,
            "port": port,
            "api_base": normalized_base,
            "models": [os.getenv("PI_MODEL", "").strip() or "default"],
            "mode": "cli_direct",
            "pi_path": str(path),
        }

    return {
        "running": False,
        "available": False,
        "port": port,
        "api_base": normalized_base,
        "error": f"Pi CLI not found at {path}. Build it in your Pi directory with npm run build.",
    }


async def _run_pi_cli_direct(
    pi_root: Path,
    cli_file: Path,
    prompt: str,
    *,
    timeout: float = 180.0,
    model: str = "",
) -> Dict[str, Any]:
    """Run the local CLI with validated AutoYou tools and structured exit status."""
    from autoyou_agents.pi_agent.runtime import run_pi
    return await run_pi(cli_file, prompt, timeout=timeout, model=model)


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
    """Use the gateway, or the local CLI only if no HTTP connection was made.

    A timeout or server error after submission can follow actual file edits.
    Such requests must never be duplicated through a second backend.
    """
    port = int(os.environ.get("PI_PORT", str(DEFAULT_PI_PORT)) or DEFAULT_PI_PORT)
    path = resolve_pi_path(pi_path)
    cli_file = resolve_pi_cli(path)

    normalized_base = (api_base or f"http://127.0.0.1:{port}").strip().rstrip("/")
    if not normalized_base.startswith("http"):
        normalized_base = f"http://127.0.0.1:{normalized_base}"

    skipped_attachments = sum(
        len(item.get("attachments") or []) for item in context or []
        if isinstance(item, dict) and isinstance(item.get("attachments"), list)
    )

    # Try HTTP gateway first
    headers = {
        "Content-Type": "application/json",
        "x-openclaw-session-key": session_id,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "model": model or "pi-agent",
        "messages": [*_bounded_text_context(context), {"role": "user", "content": message}],
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
                        finish_reason = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else data.get("status")
                        if finish_reason not in {None, "stop", "completed"}:
                            return {"response": "Pi gateway did not finish the request successfully.", "provider": "pi", "finish_reason": "error", "error": "pi_gateway_incomplete"}
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
                            "attachments_forwarded": 0,
                            "attachments_skipped": skipped_attachments,
                        }
                return {
                    "response": "Pi gateway did not complete the request. It was not replayed through the CLI.",
                    "provider": "pi", "finish_reason": "error", "error": "pi_gateway_failed",
                }
    except aiohttp.ClientConnectorError:
        logger.debug("Pi gateway could not connect; checking local CLI")
    except Exception:
        logger.warning("Pi gateway request failed after submission; refusing duplicate CLI execution")
        return {
            "response": "Pi gateway response failed. Review the request state before retrying; the request was not replayed through the CLI.",
            "provider": "pi", "finish_reason": "error", "error": "pi_gateway_outcome_unknown",
        }

    # Fallback to direct Pi CLI execution
    if cli_file and cli_file.exists():
        previous = _bounded_text_context(context)
        cli_prompt = ("Previous conversation context (not a new request):\n" + json.dumps(previous, ensure_ascii=True) + "\n\nCurrent user request:\n" + message) if previous else message
        result = await _run_pi_cli_direct(path, cli_file, cli_prompt, timeout=timeout, model=model)
        if not result.get("ok"):
            return {"response": str(result.get("error") or "Pi CLI failed."), "provider": "pi", "finish_reason": "error", "error": "pi_cli_failed"}
        reply = str(result.get("response") or "")
        if on_chunk and reply:
            res = on_chunk({"content": reply, "type": "content"})
            if asyncio.iscoroutine(res):
                await res
        return {
            "response": reply,
            "model": model or "pi-cli",
            "agent_name": "Pi (Direct CLI)",
            "finish_reason": "stop",
            "usage": result.get("usage", {}),
            "provider": "pi",
            "openclaw_endpoint": "pi.cli",
            "attachments_forwarded": 0,
            "attachments_skipped": skipped_attachments,
        }

    logger.error("Pi gateway unavailable: neither HTTP gateway nor CLI is reachable at %s", path)
    return None
