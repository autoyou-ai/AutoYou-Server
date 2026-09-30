# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-94a5f28396092b459f8c226e


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys
import textwrap
import time

import pytest

import scripts.bootstrap_autoyou as bootstrap
from shared import process_lifecycle

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-94a5f28396092b459f8c226e"


def test_pyaudio_import_probe_uses_platform_binding(monkeypatch):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Windows")
    assert bootstrap.pyaudio_import_works(Path("synthetic-python")) is True
    assert commands[-1][-1] == "import pyaudiowpatch"

    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    assert bootstrap.pyaudio_import_works(Path("synthetic-python")) is True
    assert commands[-1][-1] == "import pyaudio"


def test_default_bootstrap_profiles_install_bluetooth_pair_runtime():
    assert bootstrap.COMPONENT_REQUIREMENTS["bluetooth"] == "bluetooth.txt"

    for profile in ("recommended", "full", "source-full", "connector-full", "binary-default"):
        assert "bluetooth" in bootstrap.resolve_components(profile, [], [])


def test_training_full_bootstrap_profile_installs_the_fine_tuning_stack():
    assert "tuning" in bootstrap.resolve_components("training-full", [], [])


def test_source_wrappers_enable_the_internet_component_by_default():
    bat_text = (bootstrap.REPO_ROOT / "run_autoyou.bat").read_text(encoding="utf-8")
    sh_text = (bootstrap.REPO_ROOT / "run_autoyou.sh").read_text(encoding="utf-8")

    assert "bootstrap_autoyou.py --with internet %*" in bat_text
    assert "bootstrap_autoyou.py --with internet \"$@\"" in sh_text
    assert "--without internet" in bat_text
    assert "--without internet" in sh_text


def test_binary_default_requirements_include_bluetooth_pair_runtime():
    requirements_text = (bootstrap.REQUIREMENTS_DIR / "binary-default.txt").read_text(encoding="utf-8")

    assert "-r bluetooth.txt" in requirements_text


def test_server_macos_requirements_include_bluetooth_pair_runtime():
    requirements_text = (bootstrap.REQUIREMENTS_DIR / "server-macos.txt").read_text(encoding="utf-8")

    assert "-r bluetooth.txt" in requirements_text


def test_test_root_bootstrap_drops_inherited_runtime_state(monkeypatch, tmp_path):
    test_root = tmp_path / "runtime"
    monkeypatch.setenv(bootstrap.TEST_RUNTIME_ROOT_ENV, str(test_root))
    for variable in (
        "INTERNET_AGENT_BROWSER_HEADLESS",
        "AUTOYOU_INTERNET_BROWSER_HEADLESS",
        "AUTOYOU_BROWSER_HEADLESS",
    ):
        monkeypatch.delenv(variable, raising=False)
    for variable in bootstrap.TEST_RUNTIME_STATE_ENV_VARS:
        monkeypatch.setenv(variable, "synthetic-live-state")

    env = bootstrap.build_server_env(
        service="autoyou",
        admin_port=8001,
        ai_agent_port=8081,
        auth_port=8082,
        lib_port=8002,
        lib_auth_port=8083,
        server_password="synthetic-password",
        lib_password="",
        lib_admin_password="",
    )

    assert env[bootstrap.TEST_RUNTIME_ROOT_ENV] == str(test_root)
    assert env["AUTOYOU_SERVER_PASSWORD"] == "synthetic-password"
    assert env["INTERNET_AGENT_BROWSER_HEADLESS"] == "1"
    assert all(variable not in env for variable in bootstrap.TEST_RUNTIME_STATE_ENV_VARS)


def test_browser_headless_alias_is_not_overridden_by_canonical_default(monkeypatch):
    monkeypatch.setenv("AUTOYOU_INTERNET_BROWSER_HEADLESS", "0")
    monkeypatch.delenv("INTERNET_AGENT_BROWSER_HEADLESS", raising=False)
    monkeypatch.delenv("AUTOYOU_BROWSER_HEADLESS", raising=False)

    env = bootstrap.build_server_env(
        service="autoyou",
        admin_port=8001,
        ai_agent_port=8081,
        auth_port=8082,
        lib_port=8002,
        lib_auth_port=8083,
        server_password="synthetic-password",
        lib_password="",
        lib_admin_password="",
    )

    assert env["AUTOYOU_INTERNET_BROWSER_HEADLESS"] == "0"
    assert "INTERNET_AGENT_BROWSER_HEADLESS" not in env


def test_bootstrap_software_update_opt_out_sets_process_environment(monkeypatch):
    monkeypatch.delenv("AUTOYOU_SOFTWARE_UPDATES_ENABLED", raising=False)

    args = bootstrap.parse_args(["--no-software-updates"])
    env = bootstrap.build_server_env(
        service="autoyou",
        admin_port=8001,
        ai_agent_port=8081,
        auth_port=8082,
        lib_port=8002,
        lib_auth_port=8083,
        server_password="synthetic-password",
        lib_password="",
        lib_admin_password="",
        software_updates_enabled=not args.no_software_updates,
    )

    assert env["AUTOYOU_SOFTWARE_UPDATES_ENABLED"] == "0"


def test_dotenv_fallback_loads_launcher_settings_without_python_dotenv(monkeypatch, tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "AUTOYOU_TEST_BROWSER_HEADLESS=0\n"
        "export AUTOYOU_TEST_QUOTED='visible'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AUTOYOU_TEST_BROWSER_HEADLESS", raising=False)
    monkeypatch.delenv("AUTOYOU_TEST_QUOTED", raising=False)

    assert bootstrap._load_dotenv_fallback(dotenv_path) is True
    assert os.environ["AUTOYOU_TEST_BROWSER_HEADLESS"] == "0"
    assert os.environ["AUTOYOU_TEST_QUOTED"] == "visible"


def test_dotenv_fallback_preserves_inherited_environment(monkeypatch, tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("AUTOYOU_TEST_DOTENV_PRECEDENCE=from-file\n", encoding="utf-8")
    monkeypatch.setenv("AUTOYOU_TEST_DOTENV_PRECEDENCE", "from-process")

    assert bootstrap._load_dotenv_fallback(dotenv_path) is False
    assert os.environ["AUTOYOU_TEST_DOTENV_PRECEDENCE"] == "from-process"


def test_resolve_executable_prefers_launchable_windows_suffix(monkeypatch):
    def fake_which(command):
        return {
            "docker": "F:/Docker/resources/bin/docker",
            "docker.exe": "F:/Docker/resources/bin/docker.exe",
        }.get(command)

    monkeypatch.setattr(bootstrap.shutil, "which", fake_which)

    assert bootstrap.resolve_executable("docker", is_windows=True) == "F:/Docker/resources/bin/docker.exe"


def test_resolve_npm_executable_prefers_cmd_on_windows(monkeypatch):
    def fake_which(command):
        return {
            "npm": "F:/nodejs/npm",
            "npm.cmd": "F:/nodejs/npm.cmd",
        }.get(command)

    monkeypatch.setattr(bootstrap.shutil, "which", fake_which)

    assert bootstrap.resolve_npm_executable(is_windows=True) == "F:/nodejs/npm.cmd"


def test_resolve_docker_executable_prefers_exe_on_windows(monkeypatch):
    def fake_which(command):
        return {
            "docker": "F:/Docker/docker",
            "docker.exe": "F:/Docker/docker.exe",
        }.get(command)

    monkeypatch.setattr(bootstrap.shutil, "which", fake_which)

    assert bootstrap.resolve_docker_executable(is_windows=True) == "F:/Docker/docker.exe"


def test_check_docker_warns_when_resolved_command_cannot_launch(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap, "resolve_docker_executable", lambda: "F:/Docker/resources/bin/docker")

    def fake_run(*args, **kwargs):
        raise OSError(193, "%1 is not a valid Win32 application")

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    bootstrap.check_docker()

    output = capsys.readouterr().out
    assert "Docker command exists but could not be queried" in output


def test_check_docker_prints_install_guidance_when_missing(monkeypatch, capsys):
    monkeypatch.delenv(bootstrap.AUTOYOU_BOOTSTRAP_INSTALL_DOCKER_ENV, raising=False)
    monkeypatch.setattr(bootstrap, "resolve_docker_executable", lambda: None)
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Darwin")

    bootstrap.check_docker()

    output = capsys.readouterr().out
    assert "Docker not found" in output
    assert "brew install --cask docker" in output
    assert bootstrap.AUTOYOU_BOOTSTRAP_INSTALL_DOCKER_ENV in output


def test_try_install_docker_is_opt_in(monkeypatch, capsys):
    monkeypatch.delenv(bootstrap.AUTOYOU_BOOTSTRAP_INSTALL_DOCKER_ENV, raising=False)
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")

    called = {"run": False}

    def fake_run_command(*args, **kwargs):
        called["run"] = True
        raise AssertionError("Docker install should not run without opt-in")

    monkeypatch.setattr(bootstrap, "run_command", fake_run_command)

    assert bootstrap.try_install_docker() is False
    assert called["run"] is False
    assert "sudo apt-get install -y docker.io" in capsys.readouterr().out


def test_upgrade_packaging_tools_caps_setuptools(monkeypatch):
    captured = {}

    def fake_run_command(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs

    monkeypatch.setattr(bootstrap, "run_command", fake_run_command)

    bootstrap.upgrade_packaging_tools(Path(sys.executable))

    assert "pip>=26.1.2,<27" in captured["command"]
    assert "setuptools>=83,<84" in captured["command"]
    assert "setuptools" not in captured["command"]
    assert captured["kwargs"]["check"] is False


def test_reconcile_runtime_dependency_drift_uses_locked_constraints(monkeypatch, tmp_path):
    script = tmp_path / "scripts" / "reconcile_python_runtime_env.py"
    script.parent.mkdir()
    script.write_text("pass\n", encoding="utf-8")
    constraints = tmp_path / "requirements" / ".locked.constraints.generated.txt"
    constraints.parent.mkdir()
    constraints.write_text("rich==14.3.4\n", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "build_locked_constraints", lambda: constraints)
    monkeypatch.setattr(bootstrap, "run_command", lambda command, **kwargs: captured.update(command=command))

    bootstrap.reconcile_runtime_dependency_drift(Path("/tmp/venv/bin/python"))

    assert captured["command"] == [
        str(Path("/tmp/venv/bin/python")),
        str(script),
        "--constraints",
        str(constraints),
    ]


def test_reconcile_runtime_dependency_drift_keeps_tuning_packages(monkeypatch, tmp_path):
    script = tmp_path / "scripts" / "reconcile_python_runtime_env.py"
    script.parent.mkdir()
    script.write_text("pass\n", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "build_locked_constraints", lambda: None)
    monkeypatch.setattr(bootstrap, "run_command", lambda command, **kwargs: captured.update(command=command))

    bootstrap.reconcile_runtime_dependency_drift(Path("/tmp/venv/bin/python"), ("tuning",))

    assert captured["command"] == [
        str(Path("/tmp/venv/bin/python")),
        str(script),
        "--include-tuning",
    ]


def test_voice_component_installs_realtimestt_runtime_shim(monkeypatch):
    commands = []

    monkeypatch.setattr(bootstrap, "build_locked_constraints", lambda: None)
    monkeypatch.setattr(bootstrap, "run_command", lambda command, **kwargs: commands.append(command))

    bootstrap.install_requirements(Path("synthetic-python"), ["voice"])

    assert commands[0] == [
        "synthetic-python",
        str(bootstrap.REPO_ROOT / "scripts" / "install_realtimestt_runtime.py"),
    ]
    assert commands[1][:4] == [
        "synthetic-python",
        "-m",
        "pip",
        "install",
    ]


def test_check_node_and_install_packages_warns_when_npm_cannot_launch(monkeypatch, tmp_path, capsys):
    package_dir = tmp_path / "node" / "whatsapp"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda command: "F:/nodejs/node.EXE" if command == "node" else None)
    monkeypatch.setattr(bootstrap, "resolve_npm_executable", lambda: "F:/nodejs/npm")
    monkeypatch.setattr(bootstrap.subprocess, "check_output", lambda *args, **kwargs: "v24.12.0")

    def fake_run(*args, **kwargs):
        raise OSError(193, "%1 is not a valid Win32 application")

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    bootstrap.check_node_and_install_packages(Path(sys.executable))

    output = capsys.readouterr().out
    assert "Node.js v24.12.0 detected" in output
    assert "Unable to run npm for node/whatsapp" in output


def test_node_security_runtime_floor_selects_portable_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "resolve_executable", lambda *args, **kwargs: "synthetic-node")
    monkeypatch.setattr(bootstrap, "resolve_npm_executable", lambda: "synthetic-npm")
    fallback = []
    monkeypatch.setattr(bootstrap, "ensure_portable_node_runtime", lambda _python: fallback.append(True))
    for version, supported in [("18.20.8", False), ("20.18.0", False), ("22.11.0", False),
                               ("22.12.0", True), ("22.22.3", True), ("24.0.0", True)]:
        monkeypatch.setattr(bootstrap.subprocess, "check_output", lambda *args, **kwargs: "v" + version)
        fallback.clear()
        bootstrap.check_node_and_install_packages(tmp_path / "python")
        assert bool(fallback) is not supported


def test_linux_build_uses_its_verified_node_for_package_installation():
    script = (bootstrap.REPO_ROOT / "servers/wsl/build-backend.sh").read_text()
    assert script.index("\nbundle_node_runtime\n") < script.index("\ninstall_node_service_deps\n")
    assert 'export PATH="${BUILD_ROOT}/node-runtime/bin:${PATH}"' in script


def test_check_node_and_install_packages_pins_node_dir_on_path(monkeypatch, tmp_path):
    # The POSIX npm launcher resolves `node` via PATH (#!/usr/bin/env node), so a
    # portable/fallback Node only works if its own bin directory is first on PATH.
    package_dir = tmp_path / "node" / "whatsapp"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text("{}", encoding="utf-8")

    node_bin = tmp_path / "native" / "node" / "node-v22.22.3-darwin-arm64" / "bin"
    node_bin.mkdir(parents=True)
    node_path = node_bin / "node"
    npm_path = node_bin / "npm"

    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "resolve_executable", lambda name, **kwargs: str(node_path) if name == "node" else None)
    monkeypatch.setattr(bootstrap, "resolve_npm_executable", lambda *args, **kwargs: str(npm_path))
    monkeypatch.setattr(bootstrap.subprocess, "check_output", lambda *args, **kwargs: "v22.22.3")
    monkeypatch.setenv("PATH", "/usr/bin")

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return bootstrap.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    bootstrap.check_node_and_install_packages(Path(sys.executable))

    assert captured["command"][0] == str(npm_path)
    path_value = (captured["env"] or {}).get("PATH", "")
    assert path_value.split(bootstrap.os.pathsep)[0] == str(node_bin)


def test_check_node_and_install_packages_skips_puppeteer_download_when_playwright_exists(monkeypatch, tmp_path):
    package_dir = tmp_path / "node" / "whatsapp"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text("{}", encoding="utf-8")

    node_path = tmp_path / "node.exe"
    npm_path = tmp_path / "npm.cmd"

    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "resolve_executable", lambda name, **kwargs: str(node_path) if name == "node" else None)
    monkeypatch.setattr(bootstrap, "resolve_npm_executable", lambda *args, **kwargs: str(npm_path))
    monkeypatch.setattr(bootstrap, "executable_version", lambda executable, flag="--version": (22, 22, 3))
    monkeypatch.setattr(bootstrap.subprocess, "check_output", lambda *args, **kwargs: "v22.22.3")
    monkeypatch.setattr(bootstrap, "find_existing_playwright_chromium", lambda: tmp_path / "chrome.exe")

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return bootstrap.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    bootstrap.check_node_and_install_packages(Path(sys.executable))

    assert captured["command"][0] == str(npm_path)
    assert captured["env"]["PUPPETEER_SKIP_DOWNLOAD"] == "1"


def test_full_profile_does_not_reinstall_existing_playwright_browser(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap, "create_venv", lambda python_executable: Path(sys.executable))
    monkeypatch.setattr(bootstrap, "playwright_package_installed", lambda venv_python: True)
    monkeypatch.setattr(bootstrap, "playwright_browser_install_needed", lambda venv_python, browser: False)
    def fail_install(*args, **kwargs):
        raise AssertionError("existing browser should not be reinstalled")

    monkeypatch.setattr(bootstrap, "install_playwright_browsers", fail_install)
    monkeypatch.setattr(bootstrap, "report_autoyou_config_storage_status", lambda: None)
    monkeypatch.setattr(bootstrap, "warn_on_ports", lambda host, ports: [])
    monkeypatch.setattr(bootstrap, "launch_server_process", lambda *args, **kwargs: 0)

    assert bootstrap.main(
        [
            "--profile",
            "full",
            "--skip-install",
            "--skip-node",
            "--skip-docker",
            "--skip-ollama",
            "--skip-tunnelmole",
            "--host",
            "0.0.0.0",
        ]
    ) == 0
    assert "Admin UI: http://127.0.0.1:8001/ (bound to 0.0.0.0)" in capsys.readouterr().out


def test_check_docker_warns_when_docker_daemon_probe_cannot_launch(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap, "resolve_docker_executable", lambda: "F:/Docker/docker")
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 1:
            return bootstrap.subprocess.CompletedProcess(args[0], 0)
        raise OSError(193, "%1 is not a valid Win32 application")

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    bootstrap.check_docker()

    output = capsys.readouterr().out
    assert "Docker command exists but could not query the daemon" in output


def test_check_docker_times_out_when_daemon_does_not_respond(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap, "resolve_docker_executable", lambda: "/usr/local/bin/docker")

    def fake_run(command, **kwargs):
        if command[-1] == "--version":
            return bootstrap.subprocess.CompletedProcess(command, 0)
        raise bootstrap.subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    bootstrap.check_docker()

    assert "Docker daemon status check timed out" in capsys.readouterr().out


def test_main_skips_second_launch_when_existing_autoyou_is_reachable(monkeypatch):
    monkeypatch.delenv(bootstrap.TEST_RUNTIME_ROOT_ENV, raising=False)
    monkeypatch.setattr(bootstrap, "create_venv", lambda python_executable: Path(sys.executable))
    monkeypatch.setattr(bootstrap, "warn_on_ports", lambda host, ports: [("Admin UI", 8001), ("AI agent", 8081)])
    monkeypatch.setattr(bootstrap, "autoyou_admin_is_reachable", lambda host, port: True)

    launched = {"called": False}

    def _unexpected_launch(*args, **kwargs):
        launched["called"] = True
        return 99

    monkeypatch.setattr(bootstrap.subprocess, "call", _unexpected_launch)

    exit_code = bootstrap.main(["--skip-install", "--skip-node", "--skip-docker", "--skip-ollama"])

    assert exit_code == 0
    assert launched["called"] is False


def test_main_refuses_to_reuse_existing_autoyou_when_test_root_is_set(monkeypatch, tmp_path):
    monkeypatch.setenv(bootstrap.TEST_RUNTIME_ROOT_ENV, str(tmp_path / "autoyou-test"))
    monkeypatch.setattr(bootstrap, "create_venv", lambda python_executable: Path(sys.executable))
    monkeypatch.setattr(bootstrap, "warn_on_ports", lambda host, ports: [("Admin UI", 8001), ("AI agent", 8081)])
    monkeypatch.setattr(bootstrap, "autoyou_admin_is_reachable", lambda host, port: True)

    launched = {"called": False}

    def _unexpected_launch(*args, **kwargs):
        launched["called"] = True
        return 99

    monkeypatch.setattr(bootstrap, "launch_server_process", _unexpected_launch)

    exit_code = bootstrap.main(["--skip-install", "--skip-node", "--skip-docker", "--skip-ollama", "--skip-tunnelmole"])

    assert exit_code == 1
    assert launched["called"] is False


def test_main_fails_cleanly_when_conflicting_ports_are_not_autoyou(monkeypatch):
    monkeypatch.setattr(bootstrap, "create_venv", lambda python_executable: Path(sys.executable))
    monkeypatch.setattr(bootstrap, "warn_on_ports", lambda host, ports: [("Admin UI", 8001)])
    monkeypatch.setattr(bootstrap, "autoyou_admin_is_reachable", lambda host, port: False)

    launched = {"called": False}

    def _unexpected_launch(*args, **kwargs):
        launched["called"] = True
        return 99

    monkeypatch.setattr(bootstrap.subprocess, "call", _unexpected_launch)

    exit_code = bootstrap.main(["--skip-install", "--skip-node", "--skip-docker", "--skip-ollama"])

    assert exit_code == 1
    assert launched["called"] is False


def test_launch_server_process_requests_graceful_shutdown_on_interrupt(monkeypatch):
    captured = {}

    class FakeProcess:
        def __init__(self):
            self.wait_calls = []
            self.terminate_calls = 0
            self.kill_calls = 0

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            if len(self.wait_calls) == 1:
                raise KeyboardInterrupt()
            return 0

        def terminate(self):
            self.terminate_calls += 1

        def kill(self):
            self.kill_calls += 1

    process = FakeProcess()

    def fake_popen(command, cwd=None, env=None, **kwargs):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env"] = env
        # from __debug_provenance_y__ import legal
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(bootstrap.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(bootstrap, "request_server_shutdown", lambda host, admin_port, shutdown_token: True)
    # Off Windows the graceful wait stays one bounded wait; Windows slices it (tests below).
    monkeypatch.setattr(bootstrap, "_is_windows", lambda: False)

    exit_code = bootstrap.launch_server_process(
        [sys.executable, "server.py"],
        cwd=Path.cwd(),
        env={"EXISTING": "1"},
        host="127.0.0.1",
        admin_port=8001,
    )

    assert exit_code == 0
    assert captured["env"]["EXISTING"] == "1"
    assert bootstrap.SHUTDOWN_TOKEN_ENV in captured["env"]
    assert captured["env"]["AUTOYOU_PARENT_PID"] == str(os.getpid())
    assert captured["kwargs"] == bootstrap.process_spawn_kwargs(hide_window=False)
    assert process.wait_calls == [1.0, bootstrap.DEFAULT_GRACEFUL_SHUTDOWN_WAIT_SECONDS]
    assert process.terminate_calls == 0
    assert process.kill_calls == 0


def test_launch_server_process_force_cleans_process_after_admin_port_closes(monkeypatch):
    class FakeProcess:
        pid = 4321

        def __init__(self):
            self.wait_calls = []
            self.killed = False

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            if self.killed:
                return 0
            raise bootstrap.subprocess.TimeoutExpired("server", timeout)

    process = FakeProcess()
    killed = []

    monkeypatch.setattr(bootstrap.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(bootstrap, "process_tree_pids", lambda pid: [pid, 4322])
    monkeypatch.setattr(
        bootstrap,
        "live_pids",
        lambda pids: [] if process.killed else list(pids),
    )

    def fake_force_kill_process_tree(pid, extra_pids=(), process_group=False):
        process.killed = True
        killed.append((pid, list(extra_pids), process_group))

    monkeypatch.setattr(bootstrap, "force_kill_process_tree", fake_force_kill_process_tree)
    monkeypatch.setattr(bootstrap, "port_in_use", lambda host, port: len(process.wait_calls) == 1)
    monkeypatch.setattr(bootstrap, "graceful_shutdown_wait_seconds", lambda: 10.0)
    monotonic_values = iter((0.0, 10.0))
    # The module's own seam, not `bootstrap.time` - that attribute is the shared
    # `time` module, and a finite fake installed there is called by every
    # asyncio loop in the process.
    monkeypatch.setattr(bootstrap, "_monotonic", lambda: next(monotonic_values))

    exit_code = bootstrap.launch_server_process(
        [sys.executable, "server.py"],
        cwd=Path.cwd(),
        env={},
        host="127.0.0.1",
        admin_port=8001,
    )

    assert exit_code == 0
    assert process.wait_calls == [1.0, 1.0, 1.0, 5]
    assert killed == [(4321, [4321, 4322], True)]


class _FakeOwnedJob:
    """Stands in for OwnedProcessJob and records how the launcher drives it."""

    def __init__(self, leftover=0):
        self.leftover = leftover
        self.terminate_grace = []
        self.closed = 0

    def terminate_remaining(self, *, grace_seconds=0.0, exit_code=1):
        self.terminate_grace.append(grace_seconds)
        return self.leftover

    def close(self):
        self.closed += 1


def _install_fake_job(monkeypatch, job):
    """Route bootstrap's OwnedProcessJob to a fake; returns the processes it was asked to own."""
    adopted = []

    class _Factory:
        @staticmethod
        def for_process(process):
            adopted.append(process)
            return job

    monkeypatch.setattr(bootstrap, "OwnedProcessJob", _Factory)
    return adopted


class _ScriptedProcess:
    """Popen stand-in whose wait() plays back a script: an int is an exit code, an exception is raised.

    It has no ``pid`` on purpose: a made-up PID handed to the real psutil helpers could name a process
    that genuinely exists on the machine running the tests.
    """

    def __init__(self, waits, clock=None):
        self._waits = list(waits)
        self._clock = clock
        self.wait_calls = []
        self.terminate_calls = 0
        self.kill_calls = 0

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        outcome = self._waits.pop(0) if self._waits else 0
        if isinstance(outcome, subprocess.TimeoutExpired) and self._clock is not None:
            self._clock[0] += timeout
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1


def _launch(monkeypatch, process):
    monkeypatch.setattr(bootstrap.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(bootstrap, "port_in_use", lambda host, port: False)
    monkeypatch.delenv(bootstrap.GRACEFUL_SHUTDOWN_WAIT_ENV, raising=False)
    return bootstrap.launch_server_process(
        [sys.executable, "server.py"],
        cwd=Path.cwd(),
        env={},
        host="127.0.0.1",
        admin_port=8001,
    )


def _windows_launch_setup(monkeypatch, *, leftover=0):
    monkeypatch.setattr(bootstrap, "_is_windows", lambda: True)
    monkeypatch.delenv(bootstrap.NO_PROCESS_JOB_ENV, raising=False)
    monkeypatch.setattr(bootstrap, "request_server_shutdown", lambda *args, **kwargs: True)
    job = _FakeOwnedJob(leftover)
    adopted = _install_fake_job(monkeypatch, job)
    return job, adopted


def test_launch_server_process_owns_the_server_in_a_job_on_windows(monkeypatch):
    job, adopted = _windows_launch_setup(monkeypatch)
    monkeypatch.setattr(
        bootstrap,
        "force_kill_process_tree",
        lambda *args, **kwargs: pytest.fail("the job is authoritative; no PID-snapshot kill is needed"),
    )
    process = _ScriptedProcess([0])

    assert _launch(monkeypatch, process) == 0

    assert adopted == [process]
    # A clean exit lets already-exiting descendants finish before the job ends whatever is left.
    assert job.terminate_grace == [bootstrap.POST_EXIT_CHILD_GRACE_SECONDS]
    assert job.closed == 1


def test_launch_server_process_reports_processes_the_job_had_to_stop(monkeypatch, capsys):
    _windows_launch_setup(monkeypatch, leftover=2)

    assert _launch(monkeypatch, _ScriptedProcess([0])) == 0

    assert "2 process(es) outlived AutoYou's own shutdown" in capsys.readouterr().out


@pytest.mark.parametrize("windows, disabled", [(False, False), (True, True)])
def test_launch_server_process_uses_no_job_off_windows_or_when_disabled(monkeypatch, windows, disabled):
    monkeypatch.setattr(bootstrap, "_is_windows", lambda: windows)
    if disabled:
        monkeypatch.setenv(bootstrap.NO_PROCESS_JOB_ENV, "1")
    else:
        monkeypatch.delenv(bootstrap.NO_PROCESS_JOB_ENV, raising=False)
    adopted = _install_fake_job(monkeypatch, _FakeOwnedJob())

    assert _launch(monkeypatch, _ScriptedProcess([0])) == 0

    assert adopted == []


def test_launch_server_process_slices_and_bounds_the_graceful_wait_on_windows(monkeypatch, capsys):
    job, _ = _windows_launch_setup(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(bootstrap, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(bootstrap, "graceful_shutdown_wait_seconds", lambda: 3.0)

    def still_running():
        return subprocess.TimeoutExpired("server", 1.0)

    process = _ScriptedProcess(
        [KeyboardInterrupt(), still_running(), still_running(), still_running(), 0],
        clock,
    )

    assert _launch(monkeypatch, process) == 0

    # One interrupt slice, three one-second slices that exhaust the 3 s budget, then the forced stop.
    assert process.wait_calls == [1.0, 1.0, 1.0, 1.0, 8]
    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert job.terminate_grace == [0.0]
    assert job.closed == 1
    assert "did not exit after graceful shutdown request" in capsys.readouterr().out


def test_second_interrupt_stops_immediately_on_windows(monkeypatch, capsys):
    job, _ = _windows_launch_setup(monkeypatch)
    process = _ScriptedProcess([KeyboardInterrupt(), KeyboardInterrupt(), 0])

    assert _launch(monkeypatch, process) == 0

    assert process.wait_calls == [1.0, 1.0, 8]
    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    # Force path: nothing is left a grace period, the job ends the whole tree.
    assert job.terminate_grace == [0.0]
    assert job.closed == 1
    out = capsys.readouterr().out
    assert "Press Ctrl+C again" in out
    assert "Second interrupt received" in out


def test_second_interrupt_propagates_off_windows_as_before(monkeypatch):
    monkeypatch.setattr(bootstrap, "_is_windows", lambda: False)
    monkeypatch.setattr(bootstrap, "request_server_shutdown", lambda *args, **kwargs: True)
    process = _ScriptedProcess([KeyboardInterrupt(), KeyboardInterrupt()])

    with pytest.raises(KeyboardInterrupt):
        _launch(monkeypatch, process)

    assert process.wait_calls == [1.0, bootstrap.DEFAULT_GRACEFUL_SHUTDOWN_WAIT_SECONDS]
    assert process.terminate_calls == 0


def test_interrupt_with_an_unreachable_admin_port_goes_straight_to_the_forced_stop(monkeypatch):
    job, _ = _windows_launch_setup(monkeypatch)
    monkeypatch.setattr(bootstrap, "request_server_shutdown", lambda *args, **kwargs: False)
    process = _ScriptedProcess([KeyboardInterrupt(), 0])

    assert _launch(monkeypatch, process) == 0

    assert process.wait_calls == [1.0, 8]
    assert process.terminate_calls == 1
    assert job.terminate_grace == [0.0]


@pytest.mark.parametrize("windows", [True, False])
def test_batch_prompt_note_is_printed_only_on_windows(monkeypatch, capsys, windows):
    monkeypatch.setattr(bootstrap, "_is_windows", lambda: windows)
    monkeypatch.setattr(bootstrap, "request_server_shutdown", lambda *args, **kwargs: True)
    _install_fake_job(monkeypatch, _FakeOwnedJob())

    assert _launch(monkeypatch, _ScriptedProcess([KeyboardInterrupt(), 0])) == 0

    assert ("Terminate batch job (Y/N)?" in capsys.readouterr().out) is windows


def test_owned_process_job_is_never_built_from_a_bare_pid():
    # Only a real Popen carries a process handle; a PID alone must never join a job that gets killed.
    assert process_lifecycle.OwnedProcessJob.for_process(SimpleNamespace(pid=os.getpid())) is None


def test_owned_process_job_is_absent_off_windows(monkeypatch):
    monkeypatch.setattr(process_lifecycle, "_windows_jobs_supported", lambda: False)

    assert process_lifecycle.OwnedProcessJob.for_process(SimpleNamespace(_handle=1)) is None


_WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="job objects exist only on Windows")

# A stand-in server: some time after launch it starts a long-lived descendant (the shape of a node
# driver or browser), then lingers briefly and exits. The descendant is what must not be left behind.
_LATE_CHILD_SERVER = textwrap.dedent(
    """
    import subprocess, sys, time
    pid_file, marker, linger = sys.argv[1], sys.argv[2], float(sys.argv[3])
    time.sleep(0.4)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", marker],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with open(pid_file, "w") as handle:
        handle.write(str(child.pid))
    time.sleep(linger)
    """
)


def _marked_process_alive(pid, marker):
    """True while ``pid`` is still the process that was started with ``marker`` on its command line."""
    psutil = pytest.importorskip("psutil")
    try:
        process = psutil.Process(pid)
        return marker in " ".join(process.cmdline()) and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _kill_marked(pid, marker):
    if pid is not None and _marked_process_alive(pid, marker):
        import psutil

        psutil.Process(pid).kill()


@_WINDOWS_ONLY
def test_owned_process_job_counts_and_stops_descendants_started_after_assignment(tmp_path):
    marker = f"ay-job-child-{os.getpid()}-{time.time_ns()}"
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "server.py"
    script.write_text(_LATE_CHILD_SERVER, encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(script), str(pid_file), marker, "120"])
    child_pid = None
    try:
        job = process_lifecycle.OwnedProcessJob.for_process(process)
        assert job is not None
        assert _wait_until(pid_file.exists)
        child_pid = int(pid_file.read_text())

        assert job.active_processes() >= 2

        assert job.terminate_remaining() >= 2
        assert _wait_until(lambda: process.poll() is not None)
        assert _wait_until(lambda: not _marked_process_alive(child_pid, marker))
        job.close()
    finally:
        _kill_marked(child_pid, marker)
        process.kill()
        process.wait(timeout=10)


@_WINDOWS_ONLY
def test_owned_process_job_close_ends_the_whole_tree(tmp_path):
    marker = f"ay-job-close-{os.getpid()}-{time.time_ns()}"
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "server.py"
    script.write_text(_LATE_CHILD_SERVER, encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(script), str(pid_file), marker, "120"])
    child_pid = None
    try:
        job = process_lifecycle.OwnedProcessJob.for_process(process)
        assert job is not None
        assert _wait_until(pid_file.exists)
        child_pid = int(pid_file.read_text())

        job.close()  # kill-on-close: the last handle going away ends every member

        assert _wait_until(lambda: process.poll() is not None)
        assert _wait_until(lambda: not _marked_process_alive(child_pid, marker))
    finally:
        _kill_marked(child_pid, marker)
        process.kill()
        process.wait(timeout=10)


@_WINDOWS_ONLY
def test_launch_server_process_reaps_descendants_that_outlive_a_clean_exit(monkeypatch, tmp_path):
    pytest.importorskip("psutil")
    marker = f"ay-late-child-{os.getpid()}-{time.time_ns()}"
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "late_child_server.py"
    script.write_text(_LATE_CHILD_SERVER, encoding="utf-8")
    monkeypatch.delenv(bootstrap.NO_PROCESS_JOB_ENV, raising=False)
    monkeypatch.setattr(bootstrap, "port_in_use", lambda host, port: False)
    child_pid = None
    try:
        exit_code = bootstrap.launch_server_process(
            [sys.executable, str(script), str(pid_file), marker, "0.6"],
            cwd=tmp_path,
            env=dict(os.environ),
            host="127.0.0.1",
            admin_port=8001,
        )

        assert exit_code == 0
        child_pid = int(pid_file.read_text())
        # The descendant appeared after launch, so a PID snapshot taken at launch never saw it.
        assert _wait_until(lambda: not _marked_process_alive(child_pid, marker), timeout=5), (
            "a descendant outlived the launcher"
        )
    finally:
        _kill_marked(child_pid, marker)


_HARD_KILLED_LAUNCHER = textwrap.dedent(
    """
    import os, sys
    sys.path.insert(0, sys.argv[1])
    import scripts.bootstrap_autoyou as bootstrap
    bootstrap.launch_server_process(
        [sys.executable, sys.argv[2], sys.argv[3], sys.argv[4], "120"],
        cwd=os.getcwd(), env=dict(os.environ), host="127.0.0.1", admin_port=int(sys.argv[5]),
    )
    """
)


@_WINDOWS_ONLY
def test_killing_the_launcher_ends_the_server_and_everything_it_started(tmp_path):
    pytest.importorskip("psutil")
    import socket

    marker = f"ay-hard-kill-{os.getpid()}-{time.time_ns()}"
    pid_file = tmp_path / "child.pid"
    server = tmp_path / "server.py"
    server.write_text(_LATE_CHILD_SERVER, encoding="utf-8")
    launcher = tmp_path / "launcher.py"
    launcher.write_text(_HARD_KILLED_LAUNCHER, encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]
    repo_root = str(bootstrap.REPO_ROOT)
    process = subprocess.Popen(
        [sys.executable, str(launcher), repo_root, str(server), str(pid_file), marker, str(unused_port)],
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid = None
    try:
        assert _wait_until(pid_file.exists, timeout=30)
        child_pid = int(pid_file.read_text())
        assert _marked_process_alive(child_pid, marker)

        process.kill()  # e.g. `taskkill /F`, or the console window being closed

        assert _wait_until(lambda: not _marked_process_alive(child_pid, marker), timeout=10), (
            "the server's descendant survived its launcher being killed"
        )
    finally:
        _kill_marked(child_pid, marker)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


def test_report_autoyou_config_storage_status_for_keystore_config(monkeypatch, capsys):
    monkeypatch.setattr(
        bootstrap,
        "get_autoyou_config_storage_status",
        lambda: {
            "config_dir": Path("C:/cfg"),
            "encrypted_path": Path("C:/cfg/config.encrypted"),
            "encrypted_exists": False,
            "keystore_path": Path("C:/cfg/config.keystore.enc"),
            "keystore_exists": True,
            "keystore_available": True,
            "keystore_backend": "WinVaultKeyring",
            "keystore_has_key": True,
        },
    )

    bootstrap.report_autoyou_config_storage_status()

    output = capsys.readouterr().out
    assert "Detected keystore-backed config" in output
    assert "WinVaultKeyring" in output
    assert "config.encrypted not found yet" not in output


def test_main_reports_bootstrap_status_without_legacy_warning(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap, "create_venv", lambda python_executable: Path(sys.executable))
    monkeypatch.setattr(
        bootstrap,
        "get_autoyou_config_storage_status",
        lambda: {
            "config_dir": Path("C:/cfg"),
            "encrypted_path": Path("C:/cfg/config.encrypted"),
            "encrypted_exists": False,
            "keystore_path": Path("C:/cfg/config.keystore.enc"),
            "keystore_exists": True,
            "keystore_available": True,
            "keystore_backend": "WinVaultKeyring",
            "keystore_has_key": True,
        },
    )
    monkeypatch.setattr(bootstrap, "warn_on_ports", lambda host, ports: [])
    monkeypatch.setattr(bootstrap, "launch_server_process", lambda *args, **kwargs: 0)

    exit_code = bootstrap.main(["--skip-install", "--skip-node", "--skip-docker", "--skip-ollama"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Detected keystore-backed config" in output
    assert "config.encrypted not found yet" not in output


def test_bootstrap_storage_diagnostic_defers_mac_keychain_credential_probe(monkeypatch):
    import shared.keystore as keystore

    calls = []

    def fake_status(*_args, **kwargs):
        calls.append(kwargs)
        return {
            "available": True,
            "backend": "keyring.backends.macOS.Keyring",
            "has_key": None,
        }

    monkeypatch.setattr(keystore, "get_keystore_status", fake_status)

    status = bootstrap.get_autoyou_config_storage_status()

    assert calls == [{"include_has_key": False}]
    assert status["keystore_has_key"] is None


def test_main_passes_server_password_to_main_server_environment(monkeypatch):
    captured = {}

    monkeypatch.setattr(bootstrap, "create_venv", lambda python_executable: Path(sys.executable))
    monkeypatch.setattr(bootstrap, "report_autoyou_config_storage_status", lambda: None)
    monkeypatch.setattr(bootstrap, "warn_on_ports", lambda host, ports: [])
    monkeypatch.setattr(
        bootstrap,
        "launch_server_process",
        lambda command, **kwargs: captured.update({"command": command, "kwargs": kwargs}) or 0,
    )

    exit_code = bootstrap.main(
        [
            "--profile",
            "base",
            "--skip-install",
            "--skip-node",
            "--skip-docker",
            "--skip-ollama",
            "--skip-tunnelmole",
            "--server-password",
            "1234",
        ]
    )

    assert exit_code == 0
    assert captured["command"][1].endswith("server.py")
    assert captured["kwargs"]["env"]["AUTOYOU_SERVER_PASSWORD"] == "1234"


def test_autoyou_lite_service_launches_canonical_package(monkeypatch):
    captured = {}

    monkeypatch.delenv("AUTOYOU_BIND_HOST", raising=False)
    monkeypatch.setattr(bootstrap, "create_venv", lambda python_executable: Path(sys.executable))
    monkeypatch.setattr(bootstrap, "warn_on_ports", lambda host, ports: [])
    monkeypatch.setattr(bootstrap, "preseed_tunnelmole", lambda python_executable: None)

    def _fake_launch(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(bootstrap, "launch_server_process", _fake_launch)

    exit_code = bootstrap.main(
        [
            "--service",
            "autoyou-lite",
            "--skip-install",
            "--skip-node",
            "--skip-docker",
            "--skip-ollama",
            "--skip-tunnelmole",
            "--lib-password",
            "synthetic-pairing-password",
            "--lib-admin-password",
            "synthetic-admin-password",
        ]
    )

    assert exit_code == 0
    assert captured["command"][:3] == [sys.executable, "-m", "autoyou_lite.server"]
    assert "--host" not in captured["command"]
    assert "--password" not in captured["command"]
    assert "--admin-password" not in captured["command"]
    assert all("synthetic" not in str(value) for value in captured["command"])
    assert captured["kwargs"]["env"]["AUTOYOU_LITE_PASSWORD"] == "synthetic-pairing-password"
    assert captured["kwargs"]["env"]["AUTOYOU_LITE_ADMIN_PASSWORD"] == "synthetic-admin-password"
    assert Path(captured["kwargs"]["env"]["PYTHONPATH"].split(os.pathsep)[0]).name == "autoyou_lite"


def test_autoyou_lite_service_uses_private_parent_source(monkeypatch, tmp_path):
    server_root = tmp_path / "AutoYou-Server"
    server_root.mkdir()
    lite_source = tmp_path / "autoyou_lite" / "autoyou_lite" / "server.py"
    lite_source.parent.mkdir(parents=True)
    lite_source.write_text("# synthetic Lite source\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "REPO_ROOT", server_root)

    env = bootstrap.build_server_env(
        service="autoyou-lite",
        admin_port=8001,
        ai_agent_port=8081,
        auth_port=8082,
        lib_port=8099,
        lib_auth_port=8098,
        server_password="",
        lib_password="",
        lib_admin_password="",
    )

    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(lite_source.parents[1])


def test_bootstrap_cli_version_flag_prints_version(capsys):
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        bootstrap.parse_args(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert f"AutoYou {bootstrap.get_version()}" in (captured.out + captured.err)

    with pytest.raises(SystemExit) as exc_info_short:
        bootstrap.parse_args(["-V"])
    assert exc_info_short.value.code == 0
    captured_short = capsys.readouterr()
    assert f"AutoYou {bootstrap.get_version()}" in (captured_short.out + captured_short.err)


def test_bootstrap_upgrade_flag_and_env_propagate():
    args_upgrade = bootstrap.parse_args(["--upgrade"])
    assert args_upgrade.upgrade is True

    args_u = bootstrap.parse_args(["-U"])
    assert args_u.upgrade is True

    args_default = bootstrap.parse_args([])
    assert args_default.upgrade is False


def test_install_requirements_honors_upgrade_flag(monkeypatch):
    commands = []
    monkeypatch.setattr(bootstrap, "build_locked_constraints", lambda: None)
    monkeypatch.setattr(bootstrap, "run_command", lambda command, **kwargs: commands.append(command))

    bootstrap.install_requirements(Path("synthetic-python"), ["base"], upgrade=True)
    assert len(commands) == 1
    assert commands[0][:5] == [
        "synthetic-python",
        "-m",
        "pip",
        "install",
        "--upgrade",
    ]


def test_print_profile_summary_includes_version(capsys):
    bootstrap.print_profile_summary("base", ["base"])
    captured = capsys.readouterr()
    assert f"AutoYou v{bootstrap.get_version()} bootstrap profile: base" in captured.out


def test_launchers_reflect_build_version_and_support_upgrade():
    root = bootstrap.REPO_ROOT
    bat_text = (root / "run_autoyou.bat").read_text(encoding="utf-8")
    sh_text = (root / "run_autoyou.sh").read_text(encoding="utf-8")

    assert "AUTOYOU_BUILD_VERSION" in bat_text
    assert "AutoYou v%AUTOYOU_BUILD_VERSION% Launcher" in bat_text
    assert "AUTOYOU_BUILD_VERSION" in sh_text
    assert 'AutoYou v${AUTOYOU_BUILD_VERSION} Launcher' in sh_text
