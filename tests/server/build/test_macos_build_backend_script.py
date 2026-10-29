# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

from tests.support.paths import REPO_ROOT


BUILD_SCRIPT = REPO_ROOT / "servers" / "macos" / "build-backend.sh"
BUILD_ALL_SCRIPT = REPO_ROOT / "servers" / "macos" / "build-all.sh"
SERVER_INTEL_SCRIPT = REPO_ROOT / "servers" / "macos" / "intel" / "build-all.sh"
SERVER_INTEL_FALLBACK_SCRIPT = REPO_ROOT / "servers" / "macos" / "intel" / "create-fallback-app.sh"


def test_macos_build_backend_keeps_unittest_available_for_runtime_imports():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "--nofollow-import-to=unittest" not in text
    assert "--assume-yes-for-downloads" in text
    assert '"$PYTHON_CMD" -c "import playwright"' in text
    assert '"$PYTHON_CMD" -m playwright install chromium' in text


def test_macos_server_builds_use_public_logo_asset_not_client_icons():
    for script in (
        BUILD_SCRIPT,
        REPO_ROOT / "servers" / "macos" / "build-frontend.sh",
        SERVER_INTEL_FALLBACK_SCRIPT,
    ):
        text = script.read_text(encoding="utf-8")
        assert "assets/logo.png" in text
        assert "clients/ios/AutoYouApp/Resources" not in text


def test_macos_server_release_requires_google_adk_license_text():
    text = BUILD_ALL_SCRIPT.read_text(encoding="utf-8")
    assert "google-adk-APACHE-2.0.txt" in text


def test_macos_build_backend_bundles_whisper_runtime_and_skips_it_in_hardening():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "whisper-cpp" in text
    assert "install_whisper_cpp_runtime" in text
    assert 'runtime/whisper' in text
    assert '--skip-dir "runtime/whisper"' in text


def test_macos_build_backend_verifies_managed_frontend_runtime_imports():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "verify_packaged_backend_imports" in text
    assert "--verify-runtime-import" in text
    assert "autoyou_agents.agent_builder_agent.website.backend.app" in text
    assert "autoyou_agents.notify_agent.website.backend.app" in text
    assert "autoyou_agents.remote_desktop_agent.website.backend.app" in text
    assert "autoyou_agents.skills_agent.website.backend.app" in text
    assert "autoyou_agents.tasks_agent.website.backend.app" in text
    assert "autoyou_agents.voice_training_agent.website.backend.app" in text
    assert "autoyou_agents.website_agent.website.backend.app" in text


def test_macos_build_backend_includes_aiplatform_metadata_packages():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "--include-distribution-metadata=google-cloud-aiplatform" in text
    assert "--include-module=agentplatform" in text
    assert "--nofollow-import-to=agentplatform._genai" in text
    assert "--include-package=agentplatform" not in text


def test_macos_intel_server_uses_runtime_nuitka_include_mode():
    build_backend = BUILD_SCRIPT.read_text(encoding="utf-8")
    server_intel = SERVER_INTEL_SCRIPT.read_text(encoding="utf-8")
    server_py = (REPO_ROOT / "server.py").read_text(encoding="utf-8")
    internet_tool = (REPO_ROOT / "autoyou_agents" / "internet_agent" / "internet_tool.py").read_text(encoding="utf-8")

    assert 'GOOGLE_NUITKA_INCLUDE_MODE="${AUTOYOU_MACOS_NUITKA_GOOGLE_INCLUDE_MODE:-broad}"' in build_backend
    assert 'nuitka_args+=("--include-package=google")' in build_backend
    assert "MACOS_RUNTIME_SITE_PACKAGE_DISTRIBUTION_OVERLAY" in build_backend
    assert "install_runtime_stdlib_overlay" in build_backend
    assert "Installing runtime stdlib overlay for Intel runtime-only launcher" in build_backend
    assert '--allow-source-dir "runtime_stdlib"' in build_backend
    assert "Installing runtime distribution overlay closure for Intel runtime-only launcher" in build_backend
    assert "sync_runtime_adk_browser_bundle" in build_backend
    assert "patch_adk_browser_ui \"$resources_root\"" in build_backend
    assert "scripts/patch_adk_web_ui.py" in build_backend
    assert "Using runtime-only AI provider overlay" in build_backend
    assert "--nofollow-import-to=google" in build_backend
    assert "--nofollow-import-to=litellm" in build_backend
    assert "Skipping packaged AI-agent frontend import verification for runtime-only launcher include mode" in build_backend
    assert "AUTOYOU_MACOS_BACKEND_VERIFY_TIMEOUT_SECONDS" in build_backend
    assert "Runtime-only packaged server import verification timed out; continuing" in build_backend
    assert 'AUTOYOU_MACOS_NUITKA_GOOGLE_INCLUDE_MODE:-runtime' in server_intel
    assert "\nfrom google.adk.cli.fast_api import get_fast_api_app" not in server_py
    assert "\nfrom google.adk.tools import FunctionTool" not in internet_tool
    assert "class _LazyFunctionTool" in internet_tool


def test_macos_release_lto_is_explicit_opt_in():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'AUTOYOU_MACOS_RELEASE_LTO:-no' in text
    assert 'set AUTOYOU_MACOS_RELEASE_LTO=yes to opt into LTO' in text


def test_macos_build_backend_excludes_packaged_test_submodules():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "--nofollow-import-to=*.tests" not in text
    assert "--nofollow-import-to=*.testing" not in text
    assert "--nofollow-import-to=scipy.*.tests.*" in text
    assert "--nofollow-import-to=sklearn.*.tests.*" in text
    assert "modules such as jinja2.tests are runtime code" in text


def test_macos_release_profiles_keep_voice_stack_in_connector_full_only():
    build_all = BUILD_ALL_SCRIPT.read_text(encoding="utf-8")
    build_backend = BUILD_SCRIPT.read_text(encoding="utf-8")
    binary_default = (REPO_ROOT / "requirements" / "binary-default.txt").read_text(encoding="utf-8")
    full = (REPO_ROOT / "requirements" / "full.txt").read_text(encoding="utf-8")

    assert 'Release profile: "binary-default" (default) or "connector-full"' in build_all
    assert 'REQUIREMENTS_TYPE="full"' in build_all
    assert 'RELEASE_PROFILE="connector-full"' in build_all
    assert "requirements_has_voice" in build_backend
    assert "desktop_args+=(--include-emotivoice)" in build_backend
    assert "compiled EmotiVoice JETS module" in build_backend
    assert "-r voice.txt" not in binary_default
    assert "-r voice.txt" in full


def test_macos_full_voice_build_bundles_and_verifies_emotivoice_runtime_deps():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "Installing runtime distribution overlay closure for voice dependencies" in text
    for package in (
        '"transformers"',
        '"g2p-en"',
        '"pypinyin-dict"',
        '"modelscope"',
        '"soundfile"',
        '"nltk"',
        '"onnxruntime"',
    ):
        assert package in text
    for module in (
        'shared.emotivoice_tts',
        'torch',
        'transformers',
        'g2p_en',
        'pypinyin_dict',
        'soundfile',
        'nltk',
        'scipy',
    ):
        assert f'--verify-runtime-import "{module}"' in text


@pytest.mark.skipif(os.name == "nt", reason="exercises the macOS shell build step")
@pytest.mark.parametrize("download_fails", [False, True])
def test_macos_scipy_selects_openblas_and_stops_on_download_failure(tmp_path, download_fails):
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    function = "install_openblas_scipy() {" + text.split("install_openblas_scipy() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    fake_python = r'''
fake_python() {
    if [[ "$1" == -c ]]; then
        case "$2" in
            "import scipy;"*) [[ -f "$BUILD_DIR/installed" ]] ;;
            "import platform;"*) echo macosx_12_0_arm64 ;;
            "from importlib.metadata"*) echo 1.17.1 ;;
            *) return 3 ;;
        esac
    else
        printf '%s\n' "$*" >> "$BUILD_DIR/pip.log"
        if [[ "$3" == download ]]; then
            [[ "$SCIPY_TEST_DOWNLOAD_FAILS" == 0 ]] || return 4
            while [[ "$1" != --dest ]]; do shift; done
            touch "$2/scipy-synthetic.whl"
        elif [[ "$3" == install ]]; then
            touch "$BUILD_DIR/installed"
        else
            return 5
        fi
    fi
}
PYTHON_CMD=fake_python
'''
    result = subprocess.run(["bash", "-ec", function + fake_python + "install_openblas_scipy"],
                            env=dict(os.environ, BUILD_DIR=str(tmp_path),
                                     SCIPY_TEST_DOWNLOAD_FAILS=str(int(download_fails))), capture_output=True, text=True)
    log = (tmp_path / "pip.log").read_text(encoding="utf-8")
    assert "--platform macosx_12_0_arm64" in log and "scipy==1.17.1" in log
    assert (result.returncode != 0) == download_fails
    assert (tmp_path / "installed").exists() != download_fails
    assert ("install --force-reinstall --no-deps" in log) != download_fails
    main = text.split("main() {", 1)[1]
    assert main.index("install_openblas_scipy") < main.index("compile_python_backend")


def test_app_store_source_dependencies_are_prepared_before_nuitka():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    main = text.split("main() {", 1)[1]
    assert 'AUTOYOU_MACOS_APP_STORE:-0' in main
    dependencies = text.split("ensure_system_deps() {", 1)[1].split("\n}\n", 1)[0]
    assert 'if [[ "${AUTOYOU_MACOS_APP_STORE:-0}" == "1" ]]; then' in dependencies
    assert 'brew_deps+=("autoconf" "automake" "libtool" "pkgconf")' in dependencies
    assert main.index('build_macos_appstore_dependencies.py') < main.index('compile_python_backend')
    assert 'App Store dependencies require full voice requirements and a fresh launcher build.' in main


@pytest.mark.skipif(os.name == "nt", reason="exercises the macOS shell build step")
def test_torch_openmp_load_paths_are_relative_and_rewritten_libraries_are_signed(tmp_path):
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    function = "patch_torch_libomp() {" + text.split("patch_torch_libomp() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    libraries = tmp_path / "synthetic torch" / "lib"
    libraries.mkdir(parents=True)
    for name in ("libomp.dylib", "libtorch_cpu.dylib", "libtorch_python.dylib", "libc10.dylib"):
        (libraries / name).touch()
    shims = r'''
fake_python() { printf '%s\n' "$TORCH_TEST_LIB"; }
log() { :; }
log_warning() { :; }
log_success() { :; }
otool() {
    printf '%s:\n' "$2"
    case "$2" in
        *libtorch_cpu.dylib) printf '\t/opt/llvm-openmp/lib/libomp.dylib (compatibility version 5.0.0)\n' ;;
        *libtorch_python.dylib) printf '\t@rpath/libomp.dylib (compatibility version 5.0.0)\n' ;;
        *) printf '\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n' ;;
    esac
}
install_name_tool() { printf 'rewrite:%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" >> "$TORCH_TEST_LOG"; }
adhoc_sign_macos_path() { printf 'sign:%s\n' "$1" >> "$TORCH_TEST_LOG"; }
bundle_macos_dylib_closure() { printf 'closure:%s\n' "$1" >> "$TORCH_TEST_LOG"; }
PYTHON_CMD=fake_python
'''
    log = tmp_path / "commands.log"
    subprocess.run(["bash", "-ec", function + shims + "patch_torch_libomp"], check=True,
                   env=dict(os.environ, TORCH_TEST_LIB=str(libraries), TORCH_TEST_LOG=str(log)))
    calls = log.read_text(encoding="utf-8")
    assert "rewrite:-change|/opt/llvm-openmp/lib/libomp.dylib|@loader_path/libomp.dylib|" in calls
    assert "rewrite:-change|@rpath/libomp.dylib|@loader_path/libomp.dylib|" in calls
    assert calls.count("sign:") == 2
    assert f"closure:{libraries / 'libomp.dylib'}" in calls


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("cc"), reason="requires macOS native signing tools")
def test_native_dylib_rewrites_are_signed_before_import(tmp_path):
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    functions = "\n".join(
        name + "() {" + text.split(name + "() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
        for name in ("bundle_macos_dylib_closure", "adhoc_sign_macos_path")
    )
    source = tmp_path / "original.dylib"
    target = tmp_path / "runtime"
    subprocess.run(["cc", "-dynamiclib", "-x", "c", "-o", str(source), "-"],
                   input="int synthetic_voice_runtime(void) { return 42; }", text=True, check=True)
    script = functions + '''
log() { :; }
log_warning() { printf '%s\\n' "$*" >&2; }
resolve_macos_dependency_path() { return 1; }
bundle_macos_dylib_closure "$1" "$2" synthetic.dylib
'''
    subprocess.run(["bash", "-ec", script, "native-sign-check", str(source), str(target)], check=True)
    binary = target / "synthetic.dylib"
    subprocess.run(["codesign", "--verify", "--strict", str(binary)], check=True)
    import ctypes
    assert ctypes.CDLL(str(binary)).synthetic_voice_runtime() == 42
    pyaudio = text.split("patch_pyaudio_portaudio() {", 1)[1].split("\n}\n", 1)[0]
    assert "find_spec('pyaudio')" in pyaudio
    assert pyaudio.count('adhoc_sign_macos_path "$portaudio_so"') == 2


def test_macos_release_wrappers_require_official_build_authorization():
    build_all = BUILD_ALL_SCRIPT.read_text(encoding="utf-8")
    sign_and_compress = (REPO_ROOT / "servers" / "macos" / "sign-and-compress.sh").read_text(encoding="utf-8")
    notarize = (REPO_ROOT / "servers" / "macos" / "notarize.sh").read_text(encoding="utf-8")
    build_backend = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "check_official_build_authorization.py" in build_all
    assert "--artifact-profile \"$(release_artifact_profile)\"" in build_all
    assert 'AUTOYOU_BUILD_ARTIFACT_PROFILE="$artifact_profile" "${sign_command[@]}"' in build_all
    assert 'AUTOYOU_BUILD_ARTIFACT_PROFILE="$artifact_profile" "${notarize_command[@]}"' in build_all
    assert "autoyou-server-macos-connector-full" in build_all

    assert "check_official_build_authorization.py" in sign_and_compress
    assert "--artifact-profile \"$(release_artifact_profile)\"" in sign_and_compress
    assert "release-profile.json" in sign_and_compress

    assert "check_official_build_authorization.py" in notarize
    assert "--artifact-profile \"$(release_artifact_profile)\"" in notarize

    for script in (build_all, build_backend, sign_and_compress, notarize):
        assert "AUTOYOU_SKIP_STRICT_RELEASE_LEGAL_GATE" in script


def test_macos_build_backend_bundles_bluetooth_pair_runtime_from_overlay():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert '"bless"' in text
    assert '"bleak"' in text
    assert '"CoreBluetooth"' in text
    assert '"libdispatch"' in text
    assert "--nofollow-import-to=bless" in text
    assert "--nofollow-import-to=CoreBluetooth" in text


def test_whisper_cpp_build_pins_the_supported_macos_instead_of_the_host():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    function = text.split("build_minimal_whisper_cpp_cli() {", 1)[1].split("\nbundle_macos_executable_with_dylib_closure()", 1)[0]
    assert '-DCMAKE_OSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-13.0}"' in function


def test_macos_build_backend_reconciles_reused_dependency_drift_before_pip_check():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'local locked_constraints=""' in text
    assert "scripts/reconcile_python_runtime_env.py" in text
    assert text.index("scripts/reconcile_python_runtime_env.py") < text.index('"$PYTHON_CMD" -m pip check')


def test_macos_build_all_can_pin_backend_python_for_intel_builds():
    text = BUILD_ALL_SCRIPT.read_text(encoding="utf-8")

    assert "--python PYTHON" in text
    assert "BACKEND_PYTHON" in text
    assert 'backend_args+=("--python" "$BACKEND_PYTHON")' in text


def test_macos_intel_wrappers_select_x86_64_and_verify_architecture():
    server_text = SERVER_INTEL_SCRIPT.read_text(encoding="utf-8")
    fallback_text = SERVER_INTEL_FALLBACK_SCRIPT.read_text(encoding="utf-8")

    assert 'TARGET_ARCH="x86_64"' in server_text
    assert "verify_macho_arch" in server_text

    assert "default_build_jobs" in server_text
    assert "Using maximum logical CPU build jobs" in server_text
    assert 'shared_build_args=(--python "$PYTHON_BIN")' in server_text
    assert 'shared_build_args+=(--jobs "$BUILD_JOBS")' in server_text
    assert 'build-all.sh" "${shared_build_args[@]}" "$@"' in server_text
    assert "frontend_failed_after_backend_success" in server_text
    assert "create-fallback-app.sh" in server_text
    assert "sign-and-compress.sh" in server_text
    assert "AutoYou-macOS-${TARGET_ARCH}.dmg" in server_text
    assert "Contents/Resources/backend/AutoYou.dist/AutoYouServer" in server_text

    assert "AutoYouFallbackLauncher.m" in fallback_text
    assert 'clang -arch "$TARGET_ARCH"' in fallback_text
    assert 'environment[@"AUTOYOU_PACKAGED_RUNTIME"] = @"1";' in fallback_text
    assert "[task setArguments:@[" in fallback_text
    assert '@"--admin", self.adminPort' in fallback_text
    assert "[[NSWorkspace sharedWorkspace] openURL:self.adminURL];" in fallback_text
