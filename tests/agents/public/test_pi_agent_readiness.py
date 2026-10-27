# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Pi setup/readiness is checked without sending a model request."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoyou_agents.pi_agent import readiness
from shared import pi_gateway


@pytest.fixture
def endpoint():
    model_ids = ["synthetic-local:small"]
    requests = []
    class Models(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            requests.append(self.path)
            body = json.dumps({"data": [{"id": model} for model in model_ids]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Models)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", model_ids, requests
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def cli(monkeypatch, tmp_path):
    path = tmp_path / "synthetic-cli.js"
    path.write_text("// synthetic fixture", encoding="utf-8")
    monkeypatch.setattr(readiness, "_cli_version", lambda *args: "1.2.0")
    return {"available": True, "cli": path, "node": "synthetic-node"}


@pytest.mark.asyncio
async def test_fresh_pi_profile_can_inherit_existing_ollama_model(monkeypatch, tmp_path, endpoint, cli):
    base, _models, requests = endpoint
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("PI_MODEL", "pi-agent")
    monkeypatch.setenv("OLLAMA_MODEL", "synthetic-local:small")
    monkeypatch.setenv("OLLAMA_API_BASE", base)
    profile = tmp_path / "never-created-pi-profile"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(profile))
    result = await readiness.runtime_status(cli)
    assert result["available"]
    assert result["model"] == "synthetic-local:small"
    assert result["local_ollama_base"] == base
    assert requests == ["/v1/models"]
    assert not profile.exists()


@pytest.mark.asyncio
async def test_missing_model_is_unavailable_before_pi_starts(monkeypatch, endpoint, cli):
    base, models, _requests = endpoint
    models.clear()
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("PI_MODEL", "default")
    monkeypatch.setenv("OLLAMA_MODEL", "synthetic-missing:model")
    monkeypatch.setenv("OLLAMA_API_BASE", base)
    result = await readiness.runtime_status(cli)
    assert not result["available"]
    assert "selected Pi model is unavailable" in result["reason"]


@pytest.mark.asyncio
async def test_invalid_cli_is_unavailable_before_model_probe(monkeypatch, cli):
    monkeypatch.setattr(readiness, "_cli_version", lambda *args: "")
    result = await readiness.runtime_status(cli)
    assert not result["available"]
    assert "could not run" in result["reason"]


def test_uninstall_opt_out_is_respected_before_pi_discovery(monkeypatch):
    monkeypatch.setattr(readiness, "is_agent_installed", lambda name: False)
    monkeypatch.setattr(readiness, "resolve_pi_path", lambda *args: pytest.fail("Uninstalled Pi must not be used"))
    assert not readiness.cli_status()["available"]


def test_native_setup_npm_prefix_and_bundled_node_are_discoverable(monkeypatch, tmp_path):
    prefix = tmp_path / "AutoYou" / "tools" / "pi"
    package_cli = prefix / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "bundle" / "cli.js"
    package_cli.parent.mkdir(parents=True)
    package_cli.write_text("// synthetic fixture", encoding="utf-8")
    monkeypatch.delenv("PI_PATH", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert Path(pi_gateway.get_default_pi_path()) == prefix
    assert pi_gateway.resolve_pi_cli(prefix) == package_cli
    node = tmp_path / "bundled-node.exe"
    node.write_bytes(b"synthetic executable fixture")
    monkeypatch.setenv("AUTOYOU_NODE_EXE", str(node))
    monkeypatch.setattr(pi_gateway.shutil, "which", lambda *args: None)
    assert pi_gateway.resolve_pi_node() == str(node)
    monkeypatch.setenv("AUTOYOU_NODE_EXE", str(tmp_path / "missing-node.exe"))
    assert pi_gateway.resolve_pi_node() is None


@pytest.mark.asyncio
async def test_explicit_provider_uses_its_profile_instead_of_inheriting_main_endpoint(monkeypatch, tmp_path, cli):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("PI_MODEL", "synthetic-provider/synthetic-model")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    checked = []
    monkeypatch.setattr(readiness, "_profile_model_is_available", lambda node, path, provider, model: checked.append((provider, model)) or True)
    result = await readiness.runtime_status(cli)
    assert result["available"]
    assert result["local_ollama_base"] == ""
    assert checked == [("synthetic-provider", "synthetic-model")]


def test_cli_preflight_rejects_an_unsupported_node_before_running_pi(monkeypatch):
    called = []
    monkeypatch.setattr(readiness.subprocess, "run", lambda args, **kwargs: called.append(args) or SimpleNamespace(returncode=0, stdout="v20.20.0"))
    readiness._cli_version.cache_clear()
    assert readiness._cli_version("synthetic-node-old", "synthetic-cli", 1, 1) == ""
    assert called == [["synthetic-node-old", "--version"]]


def test_cli_preflight_checks_supported_node_and_pi_versions(monkeypatch):
    replies = iter(["v22.19.0", "1.2.0"])
    monkeypatch.setattr(readiness.subprocess, "run", lambda args, **kwargs: SimpleNamespace(returncode=0, stdout=next(replies)))
    readiness._cli_version.cache_clear()
    assert readiness._cli_version("synthetic-node-ready", "synthetic-cli", 2, 2) == "1.2.0"
