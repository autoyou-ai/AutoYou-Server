# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Read-only Pi readiness checks before a coding request can start."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from autoyou_agents.shared_tools._subprocess_env import scrubbed_subprocess_env
from autoyou_agents.shared_tools.agent_install_registry import is_agent_installed
from shared.pi_gateway import resolve_pi_cli, resolve_pi_node, resolve_pi_path
from shared.process_lifecycle import process_spawn_kwargs


def inherits_local_ollama() -> bool:
    return os.getenv("PI_MODEL", "").strip() in {"", "default", "pi-agent"} and os.getenv("AI_PROVIDER", "").strip().lower() == "ollama"


def coding_model() -> str:
    if inherits_local_ollama():
        return os.getenv("OLLAMA_MODEL", "").strip()
    return os.getenv("PI_MODEL", "").strip()


def cli_status() -> dict:
    try:
        if not is_agent_installed("pi_agent"):
            return {"available": False, "reason": "Pi Agent is not installed. Install it from Agents or select the Built-in coding backend."}
    except Exception:
        return {"available": False, "reason": "Pi installation state could not be read."}
    root = resolve_pi_path(os.getenv("PI_PATH"))
    cli, node = resolve_pi_cli(root), resolve_pi_node()
    available = cli is not None and node is not None
    return {"available": available, "cli": cli, "node": node, "path": str(root),
            "reason": "" if available else "Pi's CLI or Node.js is unavailable. Check the Pi installation in AI & Models."}


@lru_cache(maxsize=16)
def _cli_version(node: str, cli: str, modified: int, size: int) -> str:
    del modified, size  # Cache identity changes when the installed CLI changes.
    try:
        node_result = subprocess.run([node, "--version"], capture_output=True, text=True,
                                     timeout=5, stdin=subprocess.DEVNULL, env=scrubbed_subprocess_env(),
                                     **process_spawn_kwargs(hide_window=True))
        node_match = re.search(r"\bv?(\d+)\.(\d+)\.(\d+)\b", node_result.stdout)
        if node_result.returncode != 0 or not node_match or tuple(int(part) for part in node_match.groups()) < (22, 19, 0):
            return ""
        result = subprocess.run([node, cli, "--version"], capture_output=True, text=True,
                                timeout=5, stdin=subprocess.DEVNULL, env=scrubbed_subprocess_env(),
                                **process_spawn_kwargs(hide_window=True))
        match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", result.stdout)
        if result.returncode == 0 and match and tuple(int(part) for part in match.groups()) >= (1, 2, 0):
            return match.group(0)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _json_file(path: Path) -> dict:
    try:
        if path.stat().st_size > 2_000_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _profile_binding(model: str) -> tuple[str, str, str]:
    profile = Path(os.path.expandvars(os.getenv("PI_CODING_AGENT_DIR") or str(Path.home() / ".pi" / "agent"))).expanduser()
    if not model or model in {"pi-agent", "default"}:
        settings = _json_file(profile / "settings.json")
        provider, selected = str(settings.get("defaultProvider") or ""), str(settings.get("defaultModel") or "")
    elif "/" in model:
        provider, selected = model.split("/", 1)
    else:
        provider, selected = "ollama", model
    providers = _json_file(profile / "models.json").get("providers")
    config = providers.get(provider) or {} if isinstance(providers, dict) else {}
    base = os.path.expandvars(str(config.get("baseUrl") or "")) if isinstance(config, dict) else ""
    return provider, selected, base.rstrip("/")


def _profile_model_is_available(node: str, cli: str, provider: str, model: str) -> bool:
    # Pi's offline catalog performs its own provider/auth configuration check.
    # This does not send a model request or provision credentials.
    try:
        result = subprocess.run(
            [node, cli, "--offline", "--no-extensions", "--no-mcp", "--no-skills", "--no-context-files", "--list-models", model],
            capture_output=True, text=True, timeout=18, stdin=subprocess.DEVNULL,
            env={**scrubbed_subprocess_env(), "NO_COLOR": "1"}, **process_spawn_kwargs(hide_window=True),
        )
        return result.returncode == 0 and any(line.split()[:2] == [provider, model] for line in result.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        return False


async def runtime_status(status: dict | None = None) -> dict:
    status = dict(status if status is not None else await asyncio.to_thread(cli_status))
    if not status.get("available"):
        return status
    try:
        stat = Path(status["cli"]).stat()
        version = await asyncio.to_thread(_cli_version, status["node"], str(status["cli"]), stat.st_mtime_ns, stat.st_size)
    except (KeyError, OSError):
        version = ""
    if not version:
        return {**status, "available": False, "reason": "Pi 1.2 or newer could not run with the configured Node.js runtime."}
    model = coding_model()
    inherited = inherits_local_ollama()
    if inherited:
        provider, selected = "ollama", model
        base = os.getenv("OLLAMA_API_BASE", "http://127.0.0.1:11434").strip().rstrip("/")
    else:
        provider, selected, base = _profile_binding(model)
    if not provider or not selected:
        return {**status, "available": False, "reason": "Choose a Pi model or configure Pi's default provider and model."}
    if not inherited:
        ready = await asyncio.to_thread(_profile_model_is_available, status["node"], str(status["cli"]), provider, selected)
        if not ready:
            return {**status, "available": False, "reason": "Pi's selected model or provider authentication is unavailable. Configure it in Pi before retrying."}
    if provider == "ollama":
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {**status, "available": False, "reason": "The Pi Ollama endpoint is not configured."}
        compatible_base = base if base.endswith("/v1") else base + "/v1"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
                async with session.get(compatible_base + "/models") as response:
                    data = await response.json() if response.status == 200 else {}
            entries = (data.get("data") or []) if isinstance(data, dict) else []
            names = {str(item.get("id") or "") for item in entries if isinstance(item, dict)}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            names = set()
        if selected not in names and selected + ":latest" not in names:
            return {**status, "available": False, "reason": "The selected Pi model is unavailable on its Ollama endpoint. Start Ollama or select an installed model."}
    return {**status, "available": True, "version": version, "model": model,
            "local_ollama_base": base if inherited else ""}
