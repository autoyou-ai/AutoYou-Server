# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-2b443b135e11dbe9238f6360


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import sys
import types
import os
import json
import builtins
import importlib
from pathlib import Path
from types import SimpleNamespace

import shared.platform_runtime as platform_runtime

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-2b443b135e11dbe9238f6360"


if "pystray" not in sys.modules:
    sys.modules["pystray"] = types.SimpleNamespace(
        Menu=lambda *args, **kwargs: ("menu", args, kwargs),
        MenuItem=lambda *args, **kwargs: ("menu-item", args, kwargs),
        Icon=lambda *args, **kwargs: types.SimpleNamespace(run=lambda: None, stop=lambda: None),
    )

import autoyou_app


def test_main_routes_private_desktop_mode_without_starting_server(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    calls = []
    monkeypatch.setattr(autoyou_app, "run_desktop_stdio_mode", lambda: calls.append("desktop"))
    monkeypatch.setattr(autoyou_app, "run_server_mode", lambda: calls.append("server"))
    assert autoyou_app.main(["--desktop-stdio"]) == "desktop-stdio"
    assert calls == ["desktop"]


def test_run_desktop_stdio_mode_prepares_packaged_audio_model_runtime(monkeypatch, tmp_path):
    calls = []

    class _FakePlatformRuntime:
        @staticmethod
        def configure_runtime(anchor):
            calls.append(("runtime", Path(anchor).name))

        @staticmethod
        def configure_whisper_cache_environment(app_name: str):
            calls.append(("cache", app_name))
            return tmp_path / "whisper_cache"

    fake_worker = SimpleNamespace(main=lambda: calls.append(("worker-main", None)))

    def fake_import_module(module_name):
        calls.append(("import", module_name))
        assert autoyou_app.os.environ[autoyou_app.PACKAGED_RUNTIME_ENV] == "1"
        assert autoyou_app.os.environ[autoyou_app.PACKAGED_RESOURCES_ROOT_ENV] == str(tmp_path)
        assert autoyou_app.os.environ["PYTHON_DOTENV_DISABLED"] == "1"
        assert autoyou_app.os.environ["AUTOYOU_V2_BUNDLED"] == "1"
        return fake_worker

    monkeypatch.setattr(autoyou_app, "APP_ROOT", tmp_path)
    monkeypatch.setattr(autoyou_app, "_looks_like_packaged_executable", lambda: True)
    monkeypatch.setattr(autoyou_app.sys, "executable", r"C:\runtime\AutoYou\AutoYou.exe")
    monkeypatch.setattr(
        autoyou_app,
        "_patch_dotenv_for_packaged_runtime",
        lambda: calls.append(("dotenv", None)),
    )
    monkeypatch.setattr(
        autoyou_app,
        "_prepare_runtime_server_imports",
        lambda app_root: calls.append(("imports", app_root)),
    )
    monkeypatch.setattr(
        autoyou_app,
        "_get_shared_platform_runtime_module",
        lambda: _FakePlatformRuntime,
    )
    monkeypatch.setattr(autoyou_app.importlib, "import_module", fake_import_module)
    monkeypatch.delenv(autoyou_app.PACKAGED_RUNTIME_ENV, raising=False)
    monkeypatch.delenv(autoyou_app.PACKAGED_RESOURCES_ROOT_ENV, raising=False)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.delenv("AUTOYOU_V2_BUNDLED", raising=False)
    monkeypatch.delenv("AUTOYOU_V2_SERVER_EXECUTABLE", raising=False)

    autoyou_app.run_desktop_stdio_mode()

    assert (
        autoyou_app.os.environ["AUTOYOU_V2_SERVER_EXECUTABLE"]
        == r"C:\runtime\AutoYou\AutoYou.exe"
    )
    assert calls == [
        ("dotenv", None),
        ("imports", tmp_path),
        ("runtime", "autoyou_app.py"),
        ("cache", "AutoYou"),
        ("import", "v2.runtime.worker"),
        ("worker-main", None),
    ]


def test_packaged_launcher_disables_bytecode_cache_writes():
    assert autoyou_app.sys.dont_write_bytecode is True
    assert autoyou_app.os.environ["PYTHONDONTWRITEBYTECODE"] == "1"


def test_admin_browser_url_maps_wildcard_bind_to_localhost():
    assert autoyou_app._get_admin_browser_url(
        {"AUTOYOU_BIND_HOST": "0.0.0.0", "AUTOYOU_ADMIN_PORT": "18001"}
    ) == "http://127.0.0.1:18001/"
    assert autoyou_app._get_admin_browser_url(
        {}, ["--host", "0.0.0.0", "--admin", "18002"]
    ) == "http://127.0.0.1:18002/"


class _FakeProcess:
    def __init__(self):
        self._returncode = None

    def poll(self):
        return self._returncode


def test_tray_start_server_uses_compiled_entrypoint(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(cmd, cwd=None, env=None, creationflags=0, stdout=None, stderr=None, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        captured["creationflags"] = creationflags
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(platform_runtime, "get_logs_dir", lambda app_name="AutoYou", anchor=None: tmp_path)
    monkeypatch.setattr(autoyou_app, "APP_ROOT", Path(r"C:\runtime\AutoYou"))
    monkeypatch.setattr(autoyou_app.sys, "executable", r"C:\runtime\AutoYou\AutoYou.exe")
    monkeypatch.setattr(autoyou_app.subprocess, "Popen", fake_popen)

    tray = autoyou_app.TrayApp()
    tray.start_server()

    assert captured["cmd"] == [r"C:\runtime\AutoYou\AutoYou.exe", "--run-server"]
    assert captured["cwd"] == r"C:\runtime\AutoYou"
    assert autoyou_app.SHUTDOWN_TOKEN_ENV in captured["env"]
    assert captured["env"][autoyou_app.SHUTDOWN_TOKEN_ENV]
    assert captured["env"][autoyou_app.PACKAGED_RESOURCES_ROOT_ENV] == r"C:\runtime\AutoYou"
    assert captured["env"]["AUTOYOU_PARENT_PID"] == str(os.getpid())
    if os.name == "nt":
        assert captured["creationflags"] & autoyou_app.subprocess.CREATE_NEW_PROCESS_GROUP


def test_patch_dotenv_for_packaged_runtime_uses_cwd_and_swallows_missing_start_path(monkeypatch):
    import dotenv
    import dotenv.main as dotenv_main

    find_calls = []
    load_calls = []

    def fake_find_dotenv(*args, **kwargs):
        find_calls.append(dict(kwargs))
        raise OSError("Starting path not found")

    def fake_load_dotenv(*args, **kwargs):
        load_calls.append((args, dict(kwargs)))
        raise OSError("Starting path not found")

    monkeypatch.setenv(autoyou_app.PACKAGED_RUNTIME_ENV, "1")
    monkeypatch.setattr(dotenv_main, "find_dotenv", fake_find_dotenv)
    monkeypatch.setattr(dotenv, "find_dotenv", fake_find_dotenv)
    monkeypatch.setattr(dotenv_main, "load_dotenv", fake_load_dotenv)
    monkeypatch.setattr(dotenv, "load_dotenv", fake_load_dotenv)

    autoyou_app._patch_dotenv_for_packaged_runtime()

    assert dotenv.find_dotenv() == ""
    assert dotenv.load_dotenv() is False
    assert find_calls == [{"usecwd": True}]
    assert load_calls == [((), {})]


def test_patch_dotenv_for_packaged_runtime_swallows_compiled_frame_assertions(monkeypatch):
    import dotenv
    import dotenv.main as dotenv_main

    def fake_find_dotenv(*args, **kwargs):
        raise AssertionError()

    def fake_load_dotenv(*args, **kwargs):
        raise AssertionError()

    monkeypatch.setenv(autoyou_app.PACKAGED_RUNTIME_ENV, "1")
    monkeypatch.setattr(dotenv_main, "find_dotenv", fake_find_dotenv)
    monkeypatch.setattr(dotenv, "find_dotenv", fake_find_dotenv)
    monkeypatch.setattr(dotenv_main, "load_dotenv", fake_load_dotenv)
    monkeypatch.setattr(dotenv, "load_dotenv", fake_load_dotenv)

    autoyou_app._patch_dotenv_for_packaged_runtime()

    assert dotenv.find_dotenv() == ""
    assert dotenv.load_dotenv() is False


def test_main_patches_dotenv_before_verify_runtime_imports(monkeypatch):
    calls = []

    monkeypatch.setattr(
        autoyou_app,
        "_configure_runtime_import_paths",
        lambda root: calls.append(("paths", root)),
    )
    monkeypatch.setattr(
        autoyou_app,
        "_patch_dotenv_for_packaged_runtime",
        lambda: calls.append(("dotenv", None)),
    )
    monkeypatch.setattr(
        autoyou_app,
        "verify_runtime_imports",
        lambda modules: calls.append(("verify", modules)),
    )

    result = autoyou_app.main(["--verify-runtime-import", "autoyou_agents.agent"])

    assert result == "verify-runtime-imports"
    assert calls[0][0] == "paths"
    assert calls[1] == ("dotenv", None)
    assert calls[2] == ("verify", ("autoyou_agents.agent",))


def test_main_reads_keyring_password_in_isolated_helper_mode(monkeypatch, capsys):
    import keyring

    calls = []
    monkeypatch.setattr(autoyou_app, "_configure_runtime_import_paths", lambda root: None)
    monkeypatch.setattr(autoyou_app, "_configure_packaged_multiprocessing_spawn", lambda: None)
    monkeypatch.setattr(autoyou_app, "_patch_dotenv_for_packaged_runtime", lambda: None)
    monkeypatch.setattr(autoyou_app, "_append_launcher_log", lambda _message: None)
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda service, username: calls.append((service, username)) or "synthetic-key",
    )

    assert autoyou_app.main(
        [
            "--read-keyring-password",
            "--keyring-service",
            "autoyou-server",
            "--keyring-username",
            "config-encryption-key",
        ]
    ) == "keyring-password"
    assert calls == [("autoyou-server", "config-encryption-key")]
    assert json.loads(capsys.readouterr().out) == {"value": "synthetic-key"}


def test_get_application_root_prefers_macos_bundle_runtime_when_compiled_dir_is_stale(
    monkeypatch,
    tmp_path,
):
    stale_backend_root = (tmp_path / "build" / "backend").resolve()
    stale_backend_root.mkdir(parents=True)

    executable_dir = (
        tmp_path
        / "autoyou_app.app"
        / "Contents"
        / "MacOS"
    ).resolve()
    executable_dir.mkdir(parents=True)
    (executable_dir / "runtime_modules").mkdir()
    executable_path = executable_dir / "AutoYouServer"
    executable_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        builtins,
        "__compiled__",
        SimpleNamespace(containing_dir=str(stale_backend_root)),
        raising=False,
    )
    monkeypatch.setattr(autoyou_app, "IS_MAC", True)
    monkeypatch.setattr(autoyou_app.sys, "executable", str(executable_path))

    assert autoyou_app.get_application_root() == executable_dir


def test_tray_stop_server_requests_local_shutdown(monkeypatch):
    captured = {}

    class FakeProcess:
        def __init__(self):
            self.wait_calls = []

        def poll(self):
            return None

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            return 0

    process = FakeProcess()
    tray = autoyou_app.TrayApp()
    tray.server_process = process
    tray.server_shutdown_token = "shutdown-token"
    tray.server_admin_port = 9001
    tray.server_ai_port = 9002
    tray.server_auth_port = 9003

    def fake_request_local_shutdown(port: int, shutdown_token: str, timeout: float = 3.0) -> bool:
        captured["port"] = port
        captured["shutdown_token"] = shutdown_token
        captured["timeout"] = timeout
        return True

    monkeypatch.setattr(autoyou_app, "_request_local_shutdown", fake_request_local_shutdown)
    monkeypatch.setattr(autoyou_app, "_is_loopback_port_open", lambda *args, **kwargs: False)

    tray.stop_server()

    assert captured == {"port": 9001, "shutdown_token": "shutdown-token", "timeout": 3.0}
    assert process.wait_calls == [autoyou_app.DEFAULT_GRACEFUL_SHUTDOWN_WAIT_SECONDS]
    assert tray.server_process is None


def test_tray_stop_server_forces_listener_cleanup_when_admin_port_survives(monkeypatch):
    killed_pids = []

    class FakeProcess:
        pid = 43210

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    tray = autoyou_app.TrayApp()
    tray.server_process = FakeProcess()
    tray.server_shutdown_token = "shutdown-token"
    tray.server_admin_port = 9001

    monkeypatch.setattr(autoyou_app, "_request_local_shutdown", lambda *args, **kwargs: True)
    monkeypatch.setattr(autoyou_app, "_is_loopback_port_open", lambda port, host="127.0.0.1", timeout=0.25: port == 9001)
    monkeypatch.setattr(autoyou_app, "_list_loopback_listener_pids", lambda port: [111, 222] if port == 9001 else [])
    monkeypatch.setattr(autoyou_app, "_force_kill_pid_tree", lambda pid: killed_pids.append(pid))

    tray.stop_server()

    assert killed_pids == [111, 222, 43210]


def test_tray_menu_prioritizes_admin_ui_and_uses_autoyou_tooltip(monkeypatch):
    captured = {}

    class FakeIcon:
        def run(self):
            return None

    class FakeMenu:
        SEPARATOR = "separator"

        def __call__(self, *items):
            return items

    def fake_icon(name, image, title, menu):
        captured["icon"] = {"name": name, "title": title, "menu": menu}
        return FakeIcon()

    monkeypatch.setattr(autoyou_app.TrayApp, "start_server", lambda self: captured.setdefault("started", True))
    monkeypatch.setattr(autoyou_app.TrayApp, "_get_icon_image", lambda self: object())
    monkeypatch.setattr(
        autoyou_app,
        "_load_pystray_module",
        lambda: SimpleNamespace(
            Menu=FakeMenu(),
            MenuItem=lambda title, action, **kwargs: {"title": title, "kwargs": kwargs},
            Icon=fake_icon,
        ),
    )

    tray = autoyou_app.TrayApp()
    tray.run()

    menu_titles = [item["title"] for item in captured["icon"]["menu"] if isinstance(item, dict)]
    assert menu_titles[:2] == ["Admin UI (Settings)", "Open Chat"]
    assert captured["icon"]["menu"][0]["kwargs"]["default"] is True
    assert captured["icon"]["title"] == "AutoYou"


def test_main_routes_compiled_server_script_reentry(monkeypatch):
    captured = {}

    def fake_run_server_mode():
        captured["argv"] = list(autoyou_app.sys.argv)
        # from __debug_provenance_x__ import email

    monkeypatch.setattr(autoyou_app, "run_server_mode", fake_run_server_mode)
    monkeypatch.setattr(autoyou_app, "run_ai_agent_server_mode", lambda *args, **kwargs: captured.setdefault("ai", True))
    monkeypatch.setattr(autoyou_app.TrayApp, "run", lambda self: captured.setdefault("tray", True))
    monkeypatch.setattr(
        autoyou_app.sys,
        "argv",
        [
            r"C:\runtime\AutoYou\AutoYou.exe",
            r"C:\runtime\AutoYou\server.py",
            "--admin",
            "9001",
        ],
    )

    mode = autoyou_app.main()

    assert mode == "server"
    assert captured["argv"] == [r"C:\runtime\AutoYou\AutoYou.exe", "--admin", "9001"]
    assert "tray" not in captured


def test_run_fine_tuning_runner_mode_imports_the_allowlisted_runtime_worker(monkeypatch, tmp_path):
    captured = {}
    (tmp_path / "runtime_modules").mkdir()

    def fake_runner_main(argv):
        captured["argv"] = list(argv)
        return 0

    monkeypatch.setattr(autoyou_app, "APP_ROOT", tmp_path)
    monkeypatch.setattr(autoyou_app, "_patch_dotenv_for_packaged_runtime", lambda: captured.setdefault("dotenv", True))
    monkeypatch.setattr(
        autoyou_app,
        "_prepare_runtime_server_imports",
        lambda root: captured.setdefault("prepared_root", root),
    )
    monkeypatch.setattr(
        autoyou_app,
        "_ensure_runtime_package_chain",
        lambda module, root: captured.setdefault("package_chain", (module, root)),
    )
    monkeypatch.setattr(
        autoyou_app.importlib,
        "import_module",
        lambda module: captured.setdefault("module", module) and SimpleNamespace(main=fake_runner_main),
    )

    assert autoyou_app.run_fine_tuning_runner_mode(["--prepare-only"]) == 0
    assert captured["prepared_root"] == tmp_path
    assert captured["package_chain"] == (
        "autoyou_agents.fine_tuning_agent.training_runner",
        tmp_path / "runtime_modules",
    )
    assert captured["module"] == "autoyou_agents.fine_tuning_agent.training_runner"
    assert captured["argv"] == ["--prepare-only"]


def test_main_routes_packaged_fine_tuning_runner_arguments(monkeypatch):
    captured = {}

    def fake_runner(argv):
        captured["argv"] = list(argv)
        return 0

    monkeypatch.setattr(
        autoyou_app,
        "run_fine_tuning_runner_mode",
        fake_runner,
    )
    monkeypatch.setattr(
        autoyou_app.sys,
        "argv",
        [r"C:\runtime\AutoYou\AutoYou.exe", "--run-fine-tuning-runner", "--prepare-only"],
    )

    assert autoyou_app.main() == "fine-tuning-runner"
    assert captured["argv"] == ["--prepare-only"]


def test_packaged_multiprocessing_spawn_marks_packaged_executable_frozen(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "AutoYou.dist"
    (runtime_dir / "runtime_modules").mkdir(parents=True)
    executable = runtime_dir / "AutoYouServer"
    executable.write_text("", encoding="utf-8")

    monkeypatch.delattr(autoyou_app.sys, "frozen", raising=False)
    monkeypatch.setattr(autoyou_app.sys, "executable", str(executable))

    autoyou_app._configure_packaged_multiprocessing_spawn()

    assert autoyou_app.sys.frozen is True


def test_packaged_multiprocessing_spawn_leaves_source_python_unfrozen(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "python"
    executable.write_text("", encoding="utf-8")

    monkeypatch.delattr(autoyou_app.sys, "frozen", raising=False)
    monkeypatch.setattr(autoyou_app.sys, "executable", str(executable))

    autoyou_app._configure_packaged_multiprocessing_spawn()

    assert getattr(autoyou_app.sys, "frozen", False) is False


def test_reload_runtime_site_packages_modules_prefers_runtime_bundle(monkeypatch, tmp_path):
    # The runtime finder is intentionally process-wide in a packaged process;
    # keep this synthetic bundle finder from leaking into later source tests.
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    runtime_site_packages_root = tmp_path / "runtime_site_packages"
    websockets_root = runtime_site_packages_root / "websockets"
    asyncio_root = websockets_root / "asyncio"
    asyncio_root.mkdir(parents=True)

    (websockets_root / "__init__.py").write_text('SOURCE = "runtime-root"\n', encoding="utf-8")
    (asyncio_root / "__init__.py").write_text('SOURCE = "runtime-asyncio"\n', encoding="utf-8")
    (asyncio_root / "client.py").write_text('SOURCE = "runtime-asyncio-client"\n', encoding="utf-8")
    (websockets_root / "client.py").write_text('SOURCE = "runtime-client"\n', encoding="utf-8")

    for module_name in (
        "websockets",
        "websockets.asyncio",
        "websockets.asyncio.client",
        "websockets.client",
    ):
        fake_module = types.ModuleType(module_name)
        fake_module.__file__ = f"embedded://{module_name}"
        monkeypatch.setitem(sys.modules, module_name, fake_module)

    autoyou_app._reload_runtime_site_packages_modules(
        tmp_path,
        (
            "websockets",
            "websockets.asyncio",
            "websockets.asyncio.client",
            "websockets.client",
        ),
    )

    assert sys.modules["websockets"].SOURCE == "runtime-root"
    assert sys.modules["websockets.asyncio"].SOURCE == "runtime-asyncio"
    assert sys.modules["websockets.asyncio.client"].SOURCE == "runtime-asyncio-client"
    assert sys.modules["websockets.client"].SOURCE == "runtime-client"


def test_main_verifies_runtime_imports_before_other_modes(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        autoyou_app,
        "verify_runtime_imports",
        lambda module_names: captured.setdefault("modules", module_names),
    )
    monkeypatch.setattr(autoyou_app, "run_server_mode", lambda: captured.setdefault("server", True))
    monkeypatch.setattr(autoyou_app, "run_ai_agent_server_mode", lambda *args, **kwargs: captured.setdefault("ai", True))
    monkeypatch.setattr(autoyou_app.TrayApp, "run", lambda self: captured.setdefault("tray", True))
    monkeypatch.setattr(
        autoyou_app.sys,
        "argv",
        [
            r"C:\runtime\AutoYou\AutoYou.exe",
            "--verify-runtime-import",
            "websockets.asyncio.client",
            "--verify-runtime-import",
            "websockets.client",
        ],
    )

    mode = autoyou_app.main()

    assert mode == "verify-runtime-imports"
    assert captured["modules"] == ("websockets.asyncio.client", "websockets.client")
    assert "server" not in captured
    assert "ai" not in captured
    assert "tray" not in captured


def test_main_verifies_server_imports_before_other_modes(monkeypatch):
    captured = {}

    monkeypatch.setattr(autoyou_app, "verify_server_imports", lambda: captured.setdefault("server-imports", True))
    monkeypatch.setattr(autoyou_app, "run_server_mode", lambda: captured.setdefault("server", True))
    monkeypatch.setattr(autoyou_app, "run_ai_agent_server_mode", lambda *args, **kwargs: captured.setdefault("ai", True))
    monkeypatch.setattr(autoyou_app.TrayApp, "run", lambda self: captured.setdefault("tray", True))
    monkeypatch.setattr(
        autoyou_app.sys,
        "argv",
        [
            r"C:\runtime\AutoYou\AutoYou.exe",
            "--verify-server-imports",
        ],
    )

    mode = autoyou_app.main()

    assert mode == "verify-server-imports"
    assert captured["server-imports"] is True
    assert "server" not in captured
    assert "ai" not in captured
    assert "tray" not in captured


def test_main_verifies_server_and_runtime_imports_together(monkeypatch):
    captured = {}

    monkeypatch.setattr(autoyou_app, "verify_server_imports", lambda: captured.setdefault("server-imports", True))
    monkeypatch.setattr(
        autoyou_app,
        "verify_runtime_imports",
        lambda module_names: captured.setdefault("modules", module_names),
    )
    monkeypatch.setattr(autoyou_app, "run_server_mode", lambda: captured.setdefault("server", True))
    monkeypatch.setattr(autoyou_app, "run_ai_agent_server_mode", lambda *args, **kwargs: captured.setdefault("ai", True))
    monkeypatch.setattr(autoyou_app.TrayApp, "run", lambda self: captured.setdefault("tray", True))
    monkeypatch.setattr(
        autoyou_app.sys,
        "argv",
        [
            r"C:\runtime\AutoYou\AutoYou.exe",
            "--verify-server-imports",
            "--verify-runtime-import",
            "autoyou_agents.remote_desktop_agent.website.backend.app",
        ],
    )

    mode = autoyou_app.main()

    assert mode == "verify-imports"
    assert captured["server-imports"] is True
    assert captured["modules"] == ("autoyou_agents.remote_desktop_agent.website.backend.app",)
    assert "server" not in captured
    assert "ai" not in captured
    assert "tray" not in captured


def test_run_autoyou_lite_server_mode_reports_separate_bundle_requirement(monkeypatch):
    class _FakePlatformRuntime:
        @staticmethod
        def configure_whisper_cache_environment(app_name: str):
            return None

    monkeypatch.setattr(autoyou_app, "_prepare_runtime_server_imports", lambda app_root: None)
    monkeypatch.setattr(autoyou_app, "_get_shared_platform_runtime_module", lambda: _FakePlatformRuntime())

    def _raise_missing_module(*args, **kwargs):
        raise ModuleNotFoundError("autoyou_lite.server")

    monkeypatch.setattr(autoyou_app, "_load_runtime_module_from_path", _raise_missing_module)
    previous_packaged_runtime = autoyou_app.os.environ.get(autoyou_app.PACKAGED_RUNTIME_ENV)

    try:
        autoyou_app.run_autoyou_lite_server_mode("127.0.0.1", 8099, 8098)
        raise AssertionError("Expected run_autoyou_lite_server_mode to fail")
    except RuntimeError as exc:
        assert "not bundled with the main AutoYou.exe backend" in str(exc)
    finally:
        if previous_packaged_runtime is None:
            autoyou_app.os.environ.pop(autoyou_app.PACKAGED_RUNTIME_ENV, None)
        else:
            autoyou_app.os.environ[autoyou_app.PACKAGED_RUNTIME_ENV] = previous_packaged_runtime


def test_verify_packaged_runtime_integrity_accepts_expected_hashes(tmp_path):
    runtime_modules_root = tmp_path / "runtime_modules"
    runtime_modules_root.mkdir()
    compiled_module = runtime_modules_root / "server.cp312-win_amd64.pyd"
    compiled_module.write_bytes(b"server-binary")
    bridge_stub = runtime_modules_root / "autoyou_agents" / "__init__.py"
    bridge_stub.parent.mkdir(parents=True)
    bridge_stub.write_text("# stub\n", encoding="utf-8")

    manifest = {
        "version": 1,
        "algorithm": "sha256",
        "tracked_roots": ["runtime_modules"],
        "forbidden_paths": ["runtime_source"],
        "allowed_python_files": ["runtime_modules/autoyou_agents/__init__.py"],
        "files": {
            "runtime_modules/server.cp312-win_amd64.pyd": autoyou_app._hash_file(compiled_module),
            "runtime_modules/autoyou_agents/__init__.py": autoyou_app._hash_file(bridge_stub),
        },
    }
    (tmp_path / "runtime_integrity.json").write_text(json.dumps(manifest), encoding="utf-8")

    autoyou_app._RUNTIME_INTEGRITY_VERIFIED = False
    autoyou_app._verify_packaged_runtime_integrity(tmp_path)


def test_verify_packaged_runtime_integrity_rejects_forbidden_runtime_source(tmp_path):
    runtime_modules_root = tmp_path / "runtime_modules"
    runtime_modules_root.mkdir()
    compiled_module = runtime_modules_root / "server.cp312-win_amd64.pyd"
    compiled_module.write_bytes(b"server-binary")
    (tmp_path / "runtime_source").mkdir()

    manifest = {
        "version": 1,
        "algorithm": "sha256",
        "tracked_roots": ["runtime_modules"],
        "forbidden_paths": ["runtime_source"],
        "allowed_python_files": [],
        "files": {
            "runtime_modules/server.cp312-win_amd64.pyd": autoyou_app._hash_file(compiled_module),
        },
    }
    (tmp_path / "runtime_integrity.json").write_text(json.dumps(manifest), encoding="utf-8")

    autoyou_app._RUNTIME_INTEGRITY_VERIFIED = False
    try:
        autoyou_app._verify_packaged_runtime_integrity(tmp_path)
        raise AssertionError("Expected packaged runtime integrity verification to fail")
    except RuntimeError as exc:
        assert "Forbidden packaged runtime path present" in str(exc)


def test_configure_runtime_modules_path_extends_loaded_package_path(tmp_path):
    runtime_modules_root = tmp_path / "runtime_modules"
    module_dir = runtime_modules_root / "autoyou_agents" / "fake_agent" / "website" / "backend"
    module_dir.mkdir(parents=True)
    for package_dir in (
        runtime_modules_root / "autoyou_agents" / "fake_agent",
        runtime_modules_root / "autoyou_agents" / "fake_agent" / "website",
        runtime_modules_root / "autoyou_agents" / "fake_agent" / "website" / "backend",
    ):
        (package_dir / "__init__.py").write_text("# package\n", encoding="utf-8")
    (module_dir / "app.py").write_text("VALUE = 'runtime'\n", encoding="utf-8")

    affected_names = [
        name
        for name in tuple(sys.modules)
        if name == "autoyou_agents" or name.startswith("autoyou_agents.fake_agent")
    ]
    original_modules = {name: sys.modules[name] for name in affected_names}
    original_sys_path = list(sys.path)
    original_pythonpath = os.environ.get("PYTHONPATH")
    try:
        for name in affected_names:
            sys.modules.pop(name, None)
        existing_package_root = tmp_path / "already_loaded_agents"
        existing_package_root.mkdir()
        package = types.ModuleType("autoyou_agents")
        package.__file__ = str(existing_package_root / "__init__.py")
        package.__package__ = "autoyou_agents"
        package.__path__ = [str(existing_package_root)]
        sys.modules["autoyou_agents"] = package

        autoyou_app._configure_runtime_modules_path(tmp_path)

        imported_module = importlib.import_module("autoyou_agents.fake_agent.website.backend.app")
        assert imported_module.VALUE == "runtime"
        assert str((runtime_modules_root / "autoyou_agents").resolve()) in list(package.__path__)
    finally:
        for name in tuple(sys.modules):
            if name == "autoyou_agents" or name.startswith("autoyou_agents.fake_agent"):
                sys.modules.pop(name, None)
        sys.modules.update(original_modules)
        sys.path[:] = original_sys_path
        if original_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = original_pythonpath


def test_ensure_runtime_package_chain_precreates_nested_runtime_packages(tmp_path):
    runtime_modules_root = tmp_path / "runtime_modules"
    module_dir = runtime_modules_root / "autoyou_agents" / "fake_agent" / "website" / "backend"
    module_dir.mkdir(parents=True)
    for package_dir in (
        runtime_modules_root / "autoyou_agents",
        runtime_modules_root / "autoyou_agents" / "fake_agent",
        runtime_modules_root / "autoyou_agents" / "fake_agent" / "website",
        runtime_modules_root / "autoyou_agents" / "fake_agent" / "website" / "backend",
    ):
        (package_dir / "__init__.py").write_text("# package\n", encoding="utf-8")
    (module_dir / "app.py").write_text("VALUE = 'runtime'\n", encoding="utf-8")

    module_name = "autoyou_agents.fake_agent.website.backend.app"
    affected_names = [
        name
        for name in tuple(sys.modules)
        if name == "autoyou_agents" or name.startswith("autoyou_agents.fake_agent")
    ]
    original_modules = {name: sys.modules[name] for name in affected_names}
    original_sys_path = list(sys.path)
    try:
        for name in affected_names:
            sys.modules.pop(name, None)
        sys.path.insert(0, str(runtime_modules_root))

        autoyou_app._ensure_runtime_package_chain(module_name, runtime_modules_root)

        imported_module = importlib.import_module(module_name)
        assert imported_module.VALUE == "runtime"
        assert "autoyou_agents.fake_agent.website" in sys.modules
        assert str((runtime_modules_root / "autoyou_agents" / "fake_agent").resolve()) in [
            str(Path(path).resolve())
            for path in sys.modules["autoyou_agents.fake_agent"].__path__
        ]
    finally:
        for name in tuple(sys.modules):
            if name == "autoyou_agents" or name.startswith("autoyou_agents.fake_agent"):
                sys.modules.pop(name, None)
        sys.modules.update(original_modules)
        sys.path[:] = original_sys_path


def test_configure_runtime_site_packages_path_adds_pywin32_locations(monkeypatch, tmp_path):
    runtime_site_packages_root = tmp_path / "runtime_site_packages"
    win32_root = runtime_site_packages_root / "win32"
    win32_lib_root = runtime_site_packages_root / "win32" / "lib"
    pythonwin_root = runtime_site_packages_root / "Pythonwin"
    pywin32_system32_root = runtime_site_packages_root / "pywin32_system32"
    win32_lib_root.mkdir(parents=True)
    pythonwin_root.mkdir(parents=True)
    pywin32_system32_root.mkdir(parents=True)

    monkeypatch.setattr(autoyou_app.sys, "path", [])
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("PYTHONPATH", "")

    autoyou_app._configure_runtime_site_packages_path(tmp_path)

    assert str(runtime_site_packages_root) in autoyou_app.sys.path
    assert str(win32_root) in autoyou_app.sys.path
    assert str(win32_lib_root) in autoyou_app.sys.path
    assert str(pythonwin_root) in autoyou_app.sys.path
    assert str(pywin32_system32_root) in autoyou_app.sys.path
    assert str(pywin32_system32_root) in os.environ["PATH"]
