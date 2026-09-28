# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from pathlib import Path
from types import SimpleNamespace
import builtins

import pytest

import shared.platform_runtime as platform_runtime
import shared.windows_runtime_support as windows_runtime_support
import shared.macos_runtime_support as macos_runtime_support
import shared.whisper_downloader as whisper_downloader


@pytest.fixture(autouse=True)
def _disable_pytest_runtime_override(monkeypatch):
    monkeypatch.delenv(platform_runtime.TEST_RUNTIME_ROOT_ENV, raising=False)
    monkeypatch.delenv(macos_runtime_support.PACKAGED_RESOURCES_ROOT_ENV, raising=False)
    monkeypatch.delenv(windows_runtime_support.PACKAGED_RESOURCES_ROOT_ENV, raising=False)
    monkeypatch.delenv("AUTOYOU_OLLAMA_EXE", raising=False)
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.delenv("AUTOYOU_WHISPER_MODELS_DIR", raising=False)


def test_get_jailbreak_data_dir_uses_user_data_in_compiled(monkeypatch, tmp_path):
    fake_user_data = (tmp_path / "appdata" / "AutoYou").resolve()
    fake_user_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(platform_runtime, "get_user_data_dir", lambda app_name="AutoYou": fake_user_data)

    jailbreak_dir = platform_runtime.get_jailbreak_data_dir("AutoYou", anchor=tmp_path / "repo" / "server.py")

    expected = fake_user_data
    assert jailbreak_dir == expected
    assert jailbreak_dir.is_dir()


def test_is_jailbreak_active_stays_scoped_to_workspace_in_dev(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    anchor = tmp_path / "repo" / "server.py"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# anchor\n", encoding="utf-8")

    compiled_ack = (
        tmp_path
        / "appdata"
        / "AutoYou"
        / platform_runtime.JAILBREAK_ACKNOWLEDGEMENT_FILENAME
    )
    compiled_ack.parent.mkdir(parents=True, exist_ok=True)
    compiled_ack.write_text("compiled\n", encoding="utf-8")

    assert platform_runtime.is_jailbreak_active(anchor=anchor) is False

    workspace_ack = anchor.parent / platform_runtime.JAILBREAK_ACKNOWLEDGEMENT_FILENAME
    workspace_ack.write_text("workspace\n", encoding="utf-8")

    assert platform_runtime.is_jailbreak_active(anchor=anchor) is True


def test_get_config_dir_uses_user_data_in_compiled_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(platform_runtime, "get_platform", lambda: "windows")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    config_dir = platform_runtime.get_config_dir("AutoYou")

    expected = (tmp_path / "appdata" / "AutoYou").resolve()
    assert config_dir == expected
    assert config_dir.is_dir()


def test_get_service_data_dir_uses_user_data_in_compiled(monkeypatch, tmp_path):
    fake_user_data = (tmp_path / "appdata" / "AutoYou").resolve()
    fake_user_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(platform_runtime, "get_user_data_dir", lambda app_name="AutoYou": fake_user_data)

    service_dir = platform_runtime.get_service_data_dir("whatsapp", anchor=tmp_path / "server.py")

    expected = (fake_user_data / "whatsapp").resolve()
    assert service_dir == expected
    assert service_dir.is_dir()


def test_get_logs_dir_uses_application_root_in_dev(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)

    anchor = tmp_path / "repo" / "server.py"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# anchor\n", encoding="utf-8")

    logs_dir = platform_runtime.get_logs_dir(anchor=anchor)

    expected = (anchor.parent / "logs").resolve()
    assert logs_dir == expected
    assert logs_dir.is_dir()


def test_configure_whisper_cache_environment_sets_writable_defaults(monkeypatch, tmp_path):
    fake_user_data = (tmp_path / "appdata" / "AutoYou").resolve()
    fake_user_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(platform_runtime, "get_user_data_dir", lambda app_name="AutoYou": fake_user_data)
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("TORCH_HOME", raising=False)
    monkeypatch.delenv("TRANSFORMERS_CACHE", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)

    cache_dir = platform_runtime.configure_whisper_cache_environment("AutoYou")
    expected_root = (fake_user_data / "whisper_cache").resolve()
    expected_hub = expected_root / "hub"

    assert cache_dir == expected_root
    assert expected_hub.is_dir()
    assert Path(platform_runtime.os.environ["HF_HOME"]).resolve() == expected_root
    assert Path(platform_runtime.os.environ["TORCH_HOME"]).resolve() == expected_root / "torch"
    assert Path(platform_runtime.os.environ["TRANSFORMERS_CACHE"]).resolve() == expected_hub
    assert Path(platform_runtime.os.environ["HUGGINGFACE_HUB_CACHE"]).resolve() == expected_hub
    assert Path(platform_runtime.os.environ["HF_HUB_CACHE"]).resolve() == expected_hub


def test_configure_whisper_cache_environment_preserves_existing_env(monkeypatch, tmp_path):
    explicit = tmp_path / "custom-cache"
    explicit_hub = explicit / "hub"
    explicit_torch = explicit / "torch"
    monkeypatch.setenv("HF_HOME", str(explicit))
    monkeypatch.setenv("TORCH_HOME", str(explicit_torch))
    monkeypatch.setenv("TRANSFORMERS_CACHE", str(explicit_hub))
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(explicit_hub))
    monkeypatch.setenv("HF_HUB_CACHE", str(explicit_hub))

    cache_dir = platform_runtime.configure_whisper_cache_environment("AutoYou")

    assert cache_dir.name == "whisper_cache"
    assert platform_runtime.os.environ["HF_HOME"] == str(explicit)
    assert platform_runtime.os.environ["TORCH_HOME"] == str(explicit_torch)
    assert platform_runtime.os.environ["TRANSFORMERS_CACHE"] == str(explicit_hub)
    assert platform_runtime.os.environ["HUGGINGFACE_HUB_CACHE"] == str(explicit_hub)
    assert platform_runtime.os.environ["HF_HUB_CACHE"] == str(explicit_hub)


def test_windows_runtime_support_uses_builtins_compiled_containing_dir(monkeypatch, tmp_path):
    bundle_root = (tmp_path / "windows-bundle").resolve()
    bundle_root.mkdir()
    monkeypatch.setattr(windows_runtime_support, "__compiled__", None, raising=False)
    monkeypatch.setattr(
        builtins,
        "__compiled__",
        SimpleNamespace(containing_dir=str(bundle_root)),
        raising=False,
    )

    resolved = windows_runtime_support.get_resources_root(tmp_path / "dist" / "server.py")

    assert resolved == bundle_root


def test_windows_runtime_support_honors_launcher_resources_root_override(monkeypatch, tmp_path):
    backend_root = (tmp_path / "AutoYou-win-x64" / "Backend").resolve()
    backend_root.mkdir(parents=True)
    (backend_root / "assets").mkdir()
    (backend_root / "runtime").mkdir()

    monkeypatch.setenv(windows_runtime_support.PACKAGED_RESOURCES_ROOT_ENV, str(backend_root))
    monkeypatch.setattr(windows_runtime_support, "__compiled__", None, raising=False)
    monkeypatch.setattr(
        builtins,
        "__compiled__",
        SimpleNamespace(containing_dir=str(backend_root / "runtime_modules" / "shared")),
        raising=False,
    )

    resolved = windows_runtime_support.get_resources_root(tmp_path / "dist" / "server.py")

    assert resolved == backend_root


def test_windows_runtime_support_resolves_backend_root_from_nested_containing_dir(monkeypatch, tmp_path):
    backend_root = (tmp_path / "AutoYou-win-x64" / "Backend").resolve()
    nested_root = backend_root / "runtime_modules" / "shared"
    nested_root.mkdir(parents=True)
    (backend_root / "assets").mkdir()
    (backend_root / "runtime").mkdir()

    monkeypatch.delenv(windows_runtime_support.PACKAGED_RESOURCES_ROOT_ENV, raising=False)
    monkeypatch.setattr(windows_runtime_support, "__compiled__", None, raising=False)
    monkeypatch.setattr(
        builtins,
        "__compiled__",
        SimpleNamespace(containing_dir=str(nested_root)),
        raising=False,
    )

    resolved = windows_runtime_support.get_resources_root(tmp_path / "dist" / "server.py")

    assert resolved == backend_root


def test_windows_runtime_support_resolves_backend_root_from_runtime_modules_anchor(monkeypatch, tmp_path):
    backend_root = (tmp_path / "AutoYou-win-x64" / "Backend").resolve()
    runtime_modules_root = backend_root / "runtime_modules"
    runtime_modules_root.mkdir(parents=True)
    (backend_root / "assets").mkdir()
    (backend_root / "runtime").mkdir()

    monkeypatch.setenv(windows_runtime_support.PACKAGED_RUNTIME_ENV, "1")
    monkeypatch.setattr(windows_runtime_support, "__compiled__", None, raising=False)
    monkeypatch.delattr(builtins, "__compiled__", raising=False)
    monkeypatch.setattr(windows_runtime_support.sys, "frozen", False, raising=False)
    monkeypatch.setattr(windows_runtime_support.sys, "executable", str(backend_root / "AutoYou.exe"), raising=False)

    anchor = runtime_modules_root / "server.pyd"

    assert windows_runtime_support.get_resources_root(anchor) == backend_root
    assert windows_runtime_support.get_application_root(anchor) == backend_root


def test_is_compiled_detects_packaged_backend_layout_without_nuitka_builtins(monkeypatch, tmp_path):
    backend_root = (tmp_path / "AutoYou-win-x64" / "Backend").resolve()
    backend_root.mkdir(parents=True)
    for marker in ("runtime_modules", "runtime_site_packages", "runtime_stdlib"):
        (backend_root / marker).mkdir()

    monkeypatch.delenv(platform_runtime.PACKAGED_RUNTIME_ENV, raising=False)
    monkeypatch.setattr(platform_runtime.sys, "frozen", False, raising=False)
    monkeypatch.setattr(platform_runtime.sys, "executable", str(backend_root / "AutoYou.exe"))
    monkeypatch.delattr(builtins, "__compiled__", raising=False)

    assert platform_runtime.is_compiled() is True


def test_is_compiled_honors_packaged_runtime_env_flag(monkeypatch):
    monkeypatch.setenv(platform_runtime.PACKAGED_RUNTIME_ENV, "1")
    monkeypatch.setattr(platform_runtime.sys, "frozen", False, raising=False)
    monkeypatch.delattr(builtins, "__compiled__", raising=False)

    assert platform_runtime.is_compiled() is True


def test_linux_get_node_command_prefers_bundled_runtime(monkeypatch, tmp_path):
    anchor = tmp_path / "repo" / "server.py"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# anchor\n", encoding="utf-8")

    bundled_node = anchor.parent / "runtime" / "node" / "bin" / "node"
    bundled_node.parent.mkdir(parents=True, exist_ok=True)
    bundled_node.write_text("", encoding="utf-8")

    monkeypatch.setattr(platform_runtime, "get_platform", lambda: "linux")

    assert platform_runtime.get_node_command(anchor) == str(bundled_node.resolve())


def test_linux_get_node_command_resolves_bundle_root_from_runtime_modules_anchor(monkeypatch, tmp_path):
    # Nuitka's --file-reference-choice=runtime gives compiled modules a
    # __file__ inside runtime_modules/, one level below the real WSL bundle
    # root that servers/wsl/build-backend.sh actually populates runtime/,
    # assets/, guides/, and requirements/ into as siblings.
    bundle_root = (tmp_path / "AutoYouServer").resolve()
    anchor = bundle_root / "runtime_modules" / "server.py"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# anchor\n", encoding="utf-8")

    for marker in ("assets", "guides", "requirements"):
        (bundle_root / marker).mkdir()

    bundled_node = bundle_root / "runtime" / "node" / "bin" / "node"
    bundled_node.parent.mkdir(parents=True, exist_ok=True)
    bundled_node.write_text("", encoding="utf-8")

    monkeypatch.setattr(platform_runtime, "get_platform", lambda: "linux")

    assert platform_runtime.get_application_root(anchor) == bundle_root
    assert platform_runtime.get_runtime_root(anchor) == bundle_root / "runtime"
    assert platform_runtime.get_node_command(anchor) == str(bundled_node.resolve())


def test_windows_runtime_support_sets_unattended_browser_defaults(monkeypatch, tmp_path):
    backend_root = (tmp_path / "AutoYou-win-x64" / "Backend").resolve()
    chromium = backend_root / "runtime" / "playwright" / "chromium-123" / "chrome-win" / "chrome.exe"
    chromium.parent.mkdir(parents=True, exist_ok=True)
    chromium.write_text("", encoding="utf-8")

    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("PUPPETEER_EXECUTABLE_PATH", raising=False)
    monkeypatch.delenv("AUTOYOU_BROWSER_HEADLESS", raising=False)
    monkeypatch.delenv("INTERNET_AGENT_BROWSER_HEADLESS", raising=False)
    monkeypatch.delenv("WHATSAPP_BROWSER_HEADLESS", raising=False)

    windows_runtime_support.configure_packaged_runtime_environment(backend_root)

    assert windows_runtime_support.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(
        (backend_root / "runtime" / "playwright").resolve()
    )
    assert windows_runtime_support.os.environ["PUPPETEER_EXECUTABLE_PATH"] == str(chromium.resolve())
    assert windows_runtime_support.os.environ["AUTOYOU_BROWSER_HEADLESS"] == "1"
    assert "INTERNET_AGENT_BROWSER_HEADLESS" not in windows_runtime_support.os.environ
    assert windows_runtime_support.os.environ["WHATSAPP_BROWSER_HEADLESS"] == "1"


def test_windows_runtime_support_sets_bundled_model_runtime_env(monkeypatch, tmp_path):
    backend_root = (tmp_path / "AutoYou-win-x64" / "Backend").resolve()
    ollama_exe = backend_root / "runtime" / "ollama" / "ollama.exe"
    ollama_models = backend_root / "runtime" / "ollama" / "models"
    whisper_models = backend_root / "runtime" / "whisper" / "models"
    ollama_exe.parent.mkdir(parents=True, exist_ok=True)
    ollama_models.mkdir(parents=True)
    whisper_models.mkdir(parents=True)
    ollama_exe.write_text("", encoding="utf-8")

    windows_runtime_support.configure_packaged_runtime_environment(backend_root)

    assert windows_runtime_support.os.environ["AUTOYOU_OLLAMA_EXE"] == str(ollama_exe.resolve())
    assert windows_runtime_support.os.environ["OLLAMA_MODELS"] == str(ollama_models.resolve())
    assert windows_runtime_support.os.environ["AUTOYOU_WHISPER_MODELS_DIR"] == str(whisper_models.resolve())


def test_linux_runtime_configures_bundled_model_env(monkeypatch, tmp_path):
    anchor = tmp_path / "repo" / "server.py"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# anchor\n", encoding="utf-8")

    ollama_exe = anchor.parent / "runtime" / "ollama" / "ollama"
    ollama_models = anchor.parent / "runtime" / "ollama" / "models"
    whisper_models = anchor.parent / "runtime" / "whisper" / "models"
    ollama_exe.parent.mkdir(parents=True, exist_ok=True)
    ollama_models.mkdir(parents=True)
    whisper_models.mkdir(parents=True)
    ollama_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(platform_runtime, "get_platform", lambda: "linux")

    platform_runtime.configure_runtime(anchor)

    assert platform_runtime.os.environ["AUTOYOU_OLLAMA_EXE"] == str(ollama_exe.resolve())
    assert platform_runtime.os.environ["OLLAMA_MODELS"] == str(ollama_models.resolve())
    assert platform_runtime.os.environ["AUTOYOU_WHISPER_MODELS_DIR"] == str(whisper_models.resolve())


def test_linux_runtime_sets_unattended_browser_and_bundled_playwright_env(monkeypatch, tmp_path):
    anchor = tmp_path / "repo" / "server.py"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# anchor\n", encoding="utf-8")

    chromium = anchor.parent / "runtime" / "playwright" / "chromium-123" / "chrome-linux" / "chrome"
    chromium.parent.mkdir(parents=True, exist_ok=True)
    chromium.write_text("", encoding="utf-8")

    monkeypatch.setattr(platform_runtime, "get_platform", lambda: "linux")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("AUTOYOU_BROWSER_HEADLESS", raising=False)
    monkeypatch.delenv("INTERNET_AGENT_BROWSER_HEADLESS", raising=False)
    monkeypatch.delenv("WHATSAPP_BROWSER_HEADLESS", raising=False)

    platform_runtime.configure_runtime(anchor)

    assert platform_runtime.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(
        (anchor.parent / "runtime" / "playwright").resolve()
    )
    assert platform_runtime.os.environ["AUTOYOU_BROWSER_HEADLESS"] == "1"
    assert "INTERNET_AGENT_BROWSER_HEADLESS" not in platform_runtime.os.environ
    assert platform_runtime.os.environ["WHATSAPP_BROWSER_HEADLESS"] == "1"


@pytest.mark.parametrize("chrome_dir_name", ["chrome-linux", "chrome-linux64"])
def test_linux_find_bundled_browser_executable_matches_both_chromium_dir_names(
    monkeypatch, tmp_path, chrome_dir_name
):
    # Playwright's chromium archive naming changed from chrome-linux to
    # chrome-linux64 across versions (mirroring the chrome-win/chrome-win64
    # pair the Windows pattern already handles) -- both must resolve.
    anchor = tmp_path / "repo" / "server.py"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# anchor\n", encoding="utf-8")

    chrome = anchor.parent / "runtime" / "playwright" / "chromium-1228" / chrome_dir_name / "chrome"
    chrome.parent.mkdir(parents=True, exist_ok=True)
    chrome.write_text("", encoding="utf-8")

    monkeypatch.setattr(platform_runtime, "get_platform", lambda: "linux")
    # find_bundled_playwright_root() checks $PLAYWRIGHT_BROWSERS_PATH before
    # falling back to the bundle-relative lookup; some sibling tests set this
    # directly on os.environ (not via monkeypatch), so it can otherwise leak
    # in from whichever test ran immediately before this one.
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    assert platform_runtime.find_bundled_browser_executable(anchor) == chrome.resolve()


def test_whisper_downloader_prefers_bundled_model_without_cache_writes(monkeypatch, tmp_path):
    bundled_models = tmp_path / "runtime" / "whisper" / "models"
    bundled_models.mkdir(parents=True)
    bundled_model = bundled_models / "ggml-tiny.en.bin"
    bundled_model.write_bytes(b"synthetic model")

    monkeypatch.setenv("AUTOYOU_WHISPER_MODELS_DIR", str(bundled_models))
    monkeypatch.setattr(
        whisper_downloader,
        "_models_dir",
        lambda: pytest.fail("mutable Whisper model cache should not be used"),
    )

    assert whisper_downloader.download_whisper_model("tiny.en") == bundled_model.resolve()


def test_macos_runtime_support_uses_builtins_compiled_containing_dir(monkeypatch, tmp_path):
    bundle_root = (tmp_path / "mac-bundle.dist").resolve()
    bundle_root.mkdir()
    monkeypatch.setattr(macos_runtime_support, "__compiled__", None, raising=False)
    monkeypatch.setattr(
        builtins,
        "__compiled__",
        SimpleNamespace(containing_dir=str(bundle_root)),
        raising=False,
    )

    resolved = macos_runtime_support.get_resources_root(tmp_path / "dist" / "server.py")

    assert resolved == bundle_root


def test_macos_runtime_support_prefers_app_bundle_executable_dir(monkeypatch, tmp_path):
    outer_backend_root = (tmp_path / "AutoYou.app" / "Contents" / "Resources" / "backend").resolve()
    outer_backend_root.mkdir(parents=True)
    executable_dir = outer_backend_root / "AutoYouServer.app" / "Contents" / "MacOS"
    executable_dir.mkdir(parents=True)
    executable_path = executable_dir / "AutoYouServer"
    executable_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(macos_runtime_support, "__compiled__", None, raising=False)
    monkeypatch.setattr(
        builtins,
        "__compiled__",
        SimpleNamespace(containing_dir=str(outer_backend_root)),
        raising=False,
    )
    monkeypatch.setattr(macos_runtime_support.sys, "executable", str(executable_path))

    resolved = macos_runtime_support.get_resources_root(tmp_path / "dist" / "server.py")

    assert resolved == executable_dir.resolve()


def test_macos_runtime_support_honors_launcher_resources_root_override(monkeypatch, tmp_path):
    resources_root = (tmp_path / "AutoYouServer.app" / "Contents" / "MacOS").resolve()
    resources_root.mkdir(parents=True)
    monkeypatch.setenv(macos_runtime_support.PACKAGED_RESOURCES_ROOT_ENV, str(resources_root))
    monkeypatch.setattr(
        builtins,
        "__compiled__",
        SimpleNamespace(containing_dir=str(tmp_path / "stale-build-root")),
        raising=False,
    )

    assert macos_runtime_support.get_application_root(tmp_path / "server.py") == resources_root
    assert macos_runtime_support.get_resources_root(tmp_path / "server.py") == resources_root


def test_get_embedded_agents_root_prefers_runtime_modules_layout(monkeypatch, tmp_path):
    resources_root = (tmp_path / "AutoYouServer.app" / "Contents" / "MacOS").resolve()
    embedded_root = resources_root / "runtime_modules" / "autoyou_agents"
    embedded_root.mkdir(parents=True)

    monkeypatch.setattr(platform_runtime, "get_resources_root", lambda anchor: resources_root)

    resolved = platform_runtime.get_embedded_agents_root(resources_root / "server.py")

    assert resolved == embedded_root.resolve()


def test_source_agents_root_keeps_embedded_and_dynamic_sibling(monkeypatch, tmp_path):
    server_root = tmp_path / "AutoYou-Server"
    embedded_root = server_root / "autoyou_agents"
    embedded_root.mkdir(parents=True)
    (server_root / "server.py").write_text("", encoding="utf-8")
    sibling_root = tmp_path / "autoyou_agents"
    sibling_root.mkdir()
    (sibling_root / "__init__.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(platform_runtime, "get_resources_root", lambda anchor: server_root)
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)

    anchor = server_root / "server.py"
    assert platform_runtime.get_embedded_agents_root(anchor) == embedded_root.resolve()
    assert platform_runtime.get_dynamic_agents_root(anchor=anchor) == sibling_root.resolve()
    assert platform_runtime.get_dynamic_agents_root(anchor=embedded_root / "notes_agent" / "agent.py") == sibling_root.resolve()


def test_get_adk_agents_base_dir_returns_parent_of_embedded_app(monkeypatch, tmp_path):
    resources_root = (tmp_path / "AutoYouServer.app" / "Contents" / "MacOS").resolve()
    embedded_root = resources_root / "runtime_modules" / "autoyou_agents"
    embedded_root.mkdir(parents=True)
    (embedded_root / "agent.py").write_text("root_agent = object()\n", encoding="utf-8")

    monkeypatch.setattr(platform_runtime, "get_resources_root", lambda anchor: resources_root)

    resolved = platform_runtime.get_adk_agents_base_dir(resources_root / "server.py")

    assert resolved == (resources_root / "runtime_modules").resolve()


def test_resolve_adk_agents_base_dir_normalizes_direct_app_root(monkeypatch, tmp_path):
    repo_root = (tmp_path / "repo").resolve()
    app_root = repo_root / "autoyou_agents"
    app_root.mkdir(parents=True)
    (app_root / "agent.py").write_text("root_agent = object()\n", encoding="utf-8")

    monkeypatch.setattr(platform_runtime, "get_resources_root", lambda anchor: repo_root)
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)

    resolved = platform_runtime.resolve_adk_agents_base_dir(repo_root / "server.py", app_root)

    assert resolved == repo_root.resolve()


def test_resolve_adk_agents_base_dir_falls_back_from_wrong_packaged_root(monkeypatch, tmp_path):
    resources_root = (tmp_path / "AutoYouServer.app" / "Contents" / "MacOS").resolve()
    embedded_root = resources_root / "runtime_modules" / "autoyou_agents"
    embedded_root.mkdir(parents=True)
    (embedded_root / "agent.py").write_text("root_agent = object()\n", encoding="utf-8")
    wrong_requested_dir = (tmp_path / "build" / "backend").resolve()
    wrong_requested_dir.mkdir(parents=True)

    monkeypatch.setattr(platform_runtime, "get_resources_root", lambda anchor: resources_root)
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)

    resolved = platform_runtime.resolve_adk_agents_base_dir(
        resources_root / "server.py",
        wrong_requested_dir,
    )

    assert resolved == (resources_root / "runtime_modules").resolve()


def _symlinks_supported(tmp_path: Path) -> bool:
    """Whether this machine can actually create symlinks.

    Windows refuses them without developer mode or elevation, so asserting on
    symlinks unconditionally makes the suite unrunnable on a stock Windows
    checkout.
    """
    probe_target = tmp_path / "_symlink_probe_target"
    probe_link = tmp_path / "_symlink_probe_link"
    probe_target.write_text("probe", encoding="utf-8")
    try:
        probe_link.symlink_to(probe_target)
    except (OSError, NotImplementedError):
        return False
    finally:
        probe_target.unlink(missing_ok=True)
    probe_link.unlink(missing_ok=True)
    return True


def _make_adk_browser_tree(root: Path) -> Path:
    """Build a miniature copy of google/adk/cli/browser/."""
    browser = root / "browser"
    (browser / "assets" / "config").mkdir(parents=True)
    (browser / "main.js").write_text("console.log(1)\n", encoding="utf-8")
    (browser / "index.html").write_text("<html></html>\n", encoding="utf-8")
    (browser / "assets" / "audio-processor.js").write_text("// worklet\n", encoding="utf-8")
    (browser / "assets" / "config" / "runtime-config.json").write_text(
        '{\n  "backendUrl": ""\n}\n', encoding="utf-8"
    )
    return browser


def _simulate_adk_runtime_config_write(web_assets_dir: Path) -> None:
    """Mirror ApiServer._setup_runtime_config()'s unconditional rewrite."""
    import json
    import os

    path = web_assets_dir / "assets" / "config" / "runtime-config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["backendUrl"] = ""
    config["telemetry"] = None
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")


def test_resolve_adk_web_assets_dir_keeps_writable_source_in_place(monkeypatch, tmp_path):
    browser = _make_adk_browser_tree(tmp_path)
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)

    assert platform_runtime.resolve_adk_web_assets_dir(browser) == browser.resolve()


def test_resolve_adk_web_assets_dir_redirects_out_of_app_bundle(monkeypatch, tmp_path):
    bundle_resources = tmp_path / "AutoYou.app" / "Contents" / "Resources"
    bundle_resources.mkdir(parents=True)
    browser = _make_adk_browser_tree(bundle_resources)
    user_data = (tmp_path / "appdata" / "AutoYou").resolve()
    user_data.mkdir(parents=True)

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(platform_runtime, "get_user_data_dir", lambda app_name="AutoYou": user_data)

    mirror = platform_runtime.resolve_adk_web_assets_dir(browser)

    assert mirror == (user_data / platform_runtime.ADK_WEB_ASSETS_DIRNAME).resolve()


def test_adk_runtime_config_write_leaves_signed_bundle_untouched(monkeypatch, tmp_path):
    """The whole point: a server start must not modify a sealed resource."""
    bundle_resources = tmp_path / "AutoYou.app" / "Contents" / "Resources"
    bundle_resources.mkdir(parents=True)
    browser = _make_adk_browser_tree(bundle_resources)
    user_data = (tmp_path / "appdata" / "AutoYou").resolve()
    user_data.mkdir(parents=True)

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(platform_runtime, "get_user_data_dir", lambda app_name="AutoYou": user_data)

    sealed = browser / "assets" / "config" / "runtime-config.json"
    sealed_before = sealed.read_bytes()

    mirror = platform_runtime.resolve_adk_web_assets_dir(browser)
    _simulate_adk_runtime_config_write(mirror)

    assert sealed.read_bytes() == sealed_before
    mirrored = (mirror / "assets" / "config" / "runtime-config.json").read_text(encoding="utf-8")
    assert '"telemetry": null' in mirrored


def test_adk_web_assets_mirror_serves_every_packaged_file(monkeypatch, tmp_path):
    bundle_resources = tmp_path / "AutoYou.app" / "Contents" / "Resources"
    bundle_resources.mkdir(parents=True)
    browser = _make_adk_browser_tree(bundle_resources)
    user_data = (tmp_path / "appdata" / "AutoYou").resolve()
    user_data.mkdir(parents=True)

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(platform_runtime, "get_user_data_dir", lambda app_name="AutoYou": user_data)

    mirror = platform_runtime.resolve_adk_web_assets_dir(browser)

    packaged = {p.relative_to(browser).as_posix() for p in browser.rglob("*") if p.is_file()}
    served = {p.relative_to(mirror).as_posix() for p in mirror.rglob("*") if p.is_file()}
    assert served == packaged

    # Read-only siblings are shared where the platform allows it. Windows
    # refuses symlinks without developer mode or elevation, and the mirror
    # deliberately falls back to copying there - see
    # test_adk_web_assets_mirror_falls_back_to_copy_without_symlinks.
    if _symlinks_supported(tmp_path):
        assert (mirror / "main.js").is_symlink()
    else:
        assert (mirror / "main.js").read_bytes() == (browser / "main.js").read_bytes()

    # The mutable config must always be a real, writable copy so ADK's rewrite
    # can never reach back into the sealed bundle.
    assert not (mirror / "assets" / "config" / "runtime-config.json").is_symlink()


def test_adk_web_assets_mirror_rebuild_never_deletes_packaged_assets(monkeypatch, tmp_path):
    """Rebuilding must unlink symlinks, not recurse through them."""
    bundle_resources = tmp_path / "AutoYou.app" / "Contents" / "Resources"
    bundle_resources.mkdir(parents=True)
    browser = _make_adk_browser_tree(bundle_resources)
    user_data = (tmp_path / "appdata" / "AutoYou").resolve()
    user_data.mkdir(parents=True)

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(platform_runtime, "get_user_data_dir", lambda app_name="AutoYou": user_data)

    packaged = {p.relative_to(browser).as_posix() for p in browser.rglob("*") if p.is_file()}
    for _ in range(3):
        mirror = platform_runtime.resolve_adk_web_assets_dir(browser)

    assert {p.relative_to(browser).as_posix() for p in browser.rglob("*") if p.is_file()} == packaged
    assert {p.relative_to(mirror).as_posix() for p in mirror.rglob("*") if p.is_file()} == packaged


def test_adk_web_assets_mirror_falls_back_to_copy_without_symlinks(monkeypatch, tmp_path):
    """Windows refuses symlinks without developer mode; the mirror must still build."""
    bundle_resources = tmp_path / "AutoYou.app" / "Contents" / "Resources"
    bundle_resources.mkdir(parents=True)
    browser = _make_adk_browser_tree(bundle_resources)
    user_data = (tmp_path / "appdata" / "AutoYou").resolve()
    user_data.mkdir(parents=True)

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(platform_runtime, "get_user_data_dir", lambda app_name="AutoYou": user_data)

    def _no_symlinks(self, target, target_is_directory=False):
        raise OSError("symlink privilege not held")

    monkeypatch.setattr(Path, "symlink_to", _no_symlinks)

    mirror = platform_runtime.resolve_adk_web_assets_dir(browser)
    _simulate_adk_runtime_config_write(mirror)

    packaged = {p.relative_to(browser).as_posix() for p in browser.rglob("*") if p.is_file()}
    assert {p.relative_to(mirror).as_posix() for p in mirror.rglob("*") if p.is_file()} == packaged
    assert not (mirror / "main.js").is_symlink()
    assert (browser / "assets" / "config" / "runtime-config.json").read_text(
        encoding="utf-8"
    ) == '{\n  "backendUrl": ""\n}\n'
