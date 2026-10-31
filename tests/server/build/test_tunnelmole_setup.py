# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Pairing setup checks installed code without downloads or live runtime changes."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from shared import tunnelmole_downloader as downloader, tunnelmole_service as service
from scripts import bootstrap_autoyou as bootstrap


@pytest.fixture
def setup_script(monkeypatch):
    path = Path(service.__file__).resolve().parents[1] / "scripts/download_tunnelmole.py"
    spec = importlib.util.spec_from_file_location("tunnelmole_setup_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(path)])
    return module


def test_runtime_check_prefers_node_without_resolving_or_downloading_binary(setup_script, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["setup", "--check-runtime"])
    monkeypatch.setattr(service, "_resolve_node_tunnelmole_runtime", lambda: {"node_command": "synthetic-node"})
    monkeypatch.setattr(service, "resolve_tunnelmole_binary", lambda **kw: pytest.fail("Node needs no fallback"))
    monkeypatch.setattr(downloader, "download_tunnelmole", lambda **kw: pytest.fail("must not download"))
    assert setup_script.main() == 0
    assert "no standalone binary needed" in capsys.readouterr().out


@pytest.mark.parametrize("available", [True, False])
def test_runtime_check_uses_only_existing_fallback(setup_script, monkeypatch, tmp_path, capsys, available):
    monkeypatch.setattr(sys, "argv", ["setup", "--check-runtime"])
    binary = tmp_path / "synthetic-tmole"
    monkeypatch.setattr(service, "_resolve_node_tunnelmole_runtime", lambda: None)
    calls = []
    monkeypatch.setattr(service, "resolve_tunnelmole_binary", lambda **kw: calls.append(kw) or (str(binary) if available else None))
    monkeypatch.setattr(downloader, "download_tunnelmole", lambda **kw: pytest.fail("check must not download"))
    assert setup_script.main() == (0 if available else 1)
    assert calls == [{"allow_download": False}]
    if not available:
        message = capsys.readouterr().err
        assert "lockfile-pinned Node.js client" in message
        assert "internet connection" not in message


@pytest.mark.parametrize("force", [True, False])
def test_explicit_binary_setup_attempts_download_once(setup_script, monkeypatch, tmp_path, force):
    monkeypatch.setattr(sys, "argv", ["setup", *(["--force"] if force else [])])
    calls = []
    monkeypatch.setattr(service, "resolve_tunnelmole_binary", lambda **kw: None)
    target = tmp_path / "synthetic-tmole"
    def download(**kwargs):
        calls.append(kwargs)
        target.write_bytes(b"synthetic reviewed binary")
        return target
    monkeypatch.setattr(downloader, "download_tunnelmole", download)
    assert setup_script.main() == 0
    assert calls == [{"force": force}]


def test_binary_refusal_is_not_reported_as_an_internet_failure(setup_script, monkeypatch, capsys):
    monkeypatch.setattr(service, "resolve_tunnelmole_binary", lambda **kw: None)
    monkeypatch.setattr(downloader, "download_tunnelmole", lambda **kw: None)
    assert setup_script.main() == 1
    error = capsys.readouterr().err
    assert "reviewed standalone" in error
    assert "internet connection" not in error


def test_check_cannot_force_a_download(setup_script, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["setup", "--check-runtime", "--force"])
    with pytest.raises(SystemExit) as exc:
        setup_script.main()
    assert exc.value.code == 2


def test_bootstrap_checks_after_node_setup(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "create_venv", lambda _: Path(sys.executable))
    monkeypatch.setattr(bootstrap, "report_autoyou_config_storage_status", lambda: None)
    monkeypatch.setattr(bootstrap, "warn_on_ports", lambda *_: [])
    calls = []
    monkeypatch.setattr(bootstrap, "check_node_and_install_packages", lambda _: calls.append("node"))
    monkeypatch.setattr(bootstrap, "preseed_tunnelmole", lambda _: calls.append("pairing"))
    assert bootstrap.main(["--profile", "base", "--with", "messaging", "--skip-install",
                           "--skip-playwright", "--skip-docker", "--skip-ollama", "--install-only"]) == 0
    assert calls == ["node", "pairing"]


def test_bootstrap_uses_non_downloading_check(monkeypatch, tmp_path, capsys):
    script = tmp_path / "scripts/download_tunnelmole.py"
    script.parent.mkdir()
    script.touch()
    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "[OK] Node client ready\n", "")
    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    bootstrap.preseed_tunnelmole(Path(sys.executable))
    assert calls == [[sys.executable, str(script), "--check-runtime"]]
    assert "pre-seed" not in capsys.readouterr().out
