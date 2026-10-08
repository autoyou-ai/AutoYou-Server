# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""The public tunnel uses the lockfile-pinned npm client, not an unsigned binary.

tunnelmole.com serves tmole unsigned from an unversioned URL. AutoYou now runs
the npm tunnelmole package (integrity-pinned, registry-signed) through its Node
launcher for the public cloud, in a home of its own so the self-hosted API key
is never offered to the third-party service, and only runs a cached tmole
binary whose sha256 has been reviewed.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

from shared import tunnelmole_service

LAUNCHER = Path(tunnelmole_service.__file__).with_name("tunnelmole_node_launcher.mjs")


@pytest.fixture
def node_runtime(monkeypatch, tmp_path):
    runtime = {
        "node_command": "synthetic-node",
        "launcher_path": str(LAUNCHER),
        "package_dir": str(tmp_path / "pkg"),
    }
    monkeypatch.setattr(tunnelmole_service, "_resolve_node_tunnelmole_runtime", lambda *_a, **_k: runtime)
    monkeypatch.setattr(tunnelmole_service, "get_user_data_dir", lambda _name: tmp_path / "data")
    monkeypatch.setattr(tunnelmole_service, "is_app_store_build", lambda: False)
    return runtime


def test_public_launch_carries_no_key_and_has_its_own_home(node_runtime, tmp_path):
    launch = tunnelmole_service._build_public_node_launch(8001)

    assert launch["command"] == ["synthetic-node", str(LAUNCHER)]
    env = launch["env"]
    assert env["AUTOYOU_TUNNELMOLE_NODE_PUBLIC"] == "1"
    assert env["AUTOYOU_TUNNELMOLE_NODE_PORT"] == "8001"
    assert not any(name in env for name in (
        "AUTOYOU_TUNNELMOLE_NODE_API_KEY",
        "AUTOYOU_TUNNELMOLE_NODE_WS_ENDPOINT",
        "AUTOYOU_TUNNELMOLE_NODE_HTTP_ENDPOINT",
    ))
    assert env["HOME"] == env["USERPROFILE"] == str(tmp_path / "data" / "tunnelmole-public-node-home")
    assert "tunnelmole-node-home" not in env["HOME"].split(os.sep)


def test_spawn_environment_turns_telemetry_off_and_drops_inherited_launcher_settings(monkeypatch):
    monkeypatch.setenv("AUTOYOU_TUNNELMOLE_NODE_API_KEY", "synthetic-key")
    monkeypatch.setenv("AUTOYOU_TUNNELMOLE_NODE_PUBLIC", "1")
    monkeypatch.delenv("TUNNELMOLE_TELEMETRY", raising=False)

    env = tunnelmole_service._tunnelmole_environment()

    assert env["TUNNELMOLE_TELEMETRY"] == "0"
    assert "AUTOYOU_TUNNELMOLE_NODE_API_KEY" not in env
    assert "AUTOYOU_TUNNELMOLE_NODE_PUBLIC" not in env


def test_unreviewed_cached_binary_is_not_run(monkeypatch, tmp_path):
    tools = tmp_path / "data" / "tools"
    tools.mkdir(parents=True)
    binary = tools / ("tmole.exe" if os.name == "nt" else "tmole")
    binary.write_bytes(b"unreviewed-build")
    binary.chmod(0o755)
    monkeypatch.setattr(tunnelmole_service, "is_app_store_build", lambda: False)
    monkeypatch.setattr(tunnelmole_service, "get_runtime_root", lambda _anchor: tmp_path / "runtime")
    monkeypatch.setattr(tunnelmole_service, "get_user_data_dir", lambda _name: tmp_path / "data")
    monkeypatch.setattr(tunnelmole_service.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tunnelmole_service.glob, "glob", lambda _pattern: [])
    monkeypatch.setattr(tunnelmole_service, "_should_auto_download_binary", lambda: False)
    for name in ("AUTOYOU_TUNNELMOLE_BIN", "TUNNELMOLE_BIN", "AUTOYOU_TUNNELMOLE_SHA256",
                 "AUTOYOU_ALLOW_UNVERIFIED_TUNNELMOLE"):
        monkeypatch.delenv(name, raising=False)

    assert tunnelmole_service.resolve_tunnelmole_binary() is None


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_launcher_refuses_a_public_run_that_carries_a_self_hosted_key(tmp_path):
    env = {
        **os.environ,
        "AUTOYOU_TUNNELMOLE_NODE_PUBLIC": "1",
        "AUTOYOU_TUNNELMOLE_NODE_PACKAGE_DIR": str(tmp_path),
        "AUTOYOU_TUNNELMOLE_NODE_PORT": "8001",
        "AUTOYOU_TUNNELMOLE_NODE_API_KEY": "synthetic-key",
    }
    result = subprocess.run(["node", str(LAUNCHER)], env=env, capture_output=True, text=True, timeout=30)

    assert result.returncode == 1
    assert "must not carry self-hosted endpoints or an API key" in result.stderr
