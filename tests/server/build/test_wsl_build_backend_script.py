# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import os
import hashlib
import json
import shutil
import subprocess
import sys

import pytest

from tests.support.paths import REPO_ROOT


BUILD_SCRIPT = REPO_ROOT / "servers" / "wsl" / "build-backend.sh"
README = REPO_ROOT / "servers" / "wsl" / "README.md"
REQUIREMENTS = REPO_ROOT / "servers" / "wsl" / "requirements.txt"


def test_wsl_build_backend_uses_packaged_runtime_builder_and_hardening():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "scripts/build_packaged_runtime_modules.py" in text
    assert "scripts/verify_backend_hardening.py" in text
    assert "runtime_integrity.json" in text
    assert "--allow-source-dir runtime_stdlib" in text
    assert "--allow-source-dir runtime_site_packages" in text
    assert "--verify-server-imports" in text
    assert "--nofollow-imports" in text
    assert '${EXTRA_SOURCES_ARGS[@]+"${EXTRA_SOURCES_ARGS[@]}"}' in text
    assert "--include-sibling-agents" not in text
    assert "--include-emotivoice" in text
    assert "runtime_modules/vendor/emotivoice/models/prompt_tts_modified/jets*.so" in text
    assert "runtime_modules/vendor/emotivoice/LICENSE" in text
    assert "--unofficial" in text
    assert "UNOFFICIAL_BUILD" in text


def test_wsl_build_backend_supports_local_patchelf_without_system_install():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "AUTOYOU_PATCHELF" in text
    assert ".venv/native/patchelf/bin/patchelf" in text
    assert "patchelf is required" in text


def test_wsl_build_backend_does_not_mutate_windows_or_macos_scripts():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "servers/windows" not in text
    assert "servers/macos" not in text


def test_wsl_server_docs_and_requirements_define_build_contract():
    readme = README.read_text(encoding="utf-8")
    requirements = REQUIREMENTS.read_text(encoding="utf-8")

    assert "servers/wsl/artifacts/backend/AutoYouServer/AutoYou" in readme
    assert "runtime_modules/" in readme
    assert "runtime_stdlib/" in readme
    assert "runtime_site_packages/" in readme
    assert "scripts/bluetooth_pair_host_bridge.py" in readme
    assert "min(nproc, 16)" in readme
    assert "nuitka==4.1.3" in requirements


def test_wsl_binary_is_named_autoyou_and_defaults_to_16_job_cap():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "--output-filename=AutoYou" in text
    assert "Output: ${FINAL_BACKEND_ROOT}/AutoYou" in text
    assert "DEFAULT_MAX_JOBS" in text
    assert "--jobs max" in text


def test_wsl_build_backend_pins_and_reconciles_reused_dependency_drift_before_pip_check():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'pip_install_args+=(-c "$LOCKED_CONSTRAINTS")' in text
    assert "scripts/reconcile_python_runtime_env.py" in text
    assert text.index("scripts/reconcile_python_runtime_env.py") < text.index('"$PYTHON_CMD" -m pip check')


def test_packaged_launcher_searches_linux_stdlib_extension_modules():
    launcher = (REPO_ROOT / "autoyou_app.py").read_text(encoding="utf-8")

    assert 'runtime_stdlib_root / "lib-dynload"' in launcher


def test_packaged_launcher_does_not_steal_server_auth_arg():
    launcher = (REPO_ROOT / "autoyou_app.py").read_text(encoding="utf-8")

    assert "ArgumentParser(add_help=False, allow_abbrev=False)" in launcher


@pytest.mark.parametrize("damage", [None, "missing-module", "missing-agent-bridge", "modified-module", "escaping-path"])
def test_wsl_cleanup_without_path_marker_preserves_runtime_integrity(tmp_path, damage):
    bundle = tmp_path / "bundle"
    files = {
        "runtime_modules/server.so": b"synthetic compiled module",
        "runtime_modules/autoyou_agents/__init__.pyc": b"synthetic required bridge",
        "transport/release.json": b"synthetic public config",
    }
    hashes = {}
    for name, data in files.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    hashes["runtime_modules/shared/__init__.pyc"] = "0" * 64
    manifest = {"version": 1, "files": hashes, "allowed_python_files": ["transport/bindings.py"]}
    if damage == "missing-module":
        (bundle / "runtime_modules/server.so").unlink()
    elif damage == "missing-agent-bridge":
        (bundle / "runtime_modules/autoyou_agents/__init__.pyc").unlink()
    elif damage == "modified-module":
        (bundle / "runtime_modules/server.so").write_bytes(b"modified synthetic module")
    elif damage == "escaping-path":
        hashes["../outside.pyc"] = "0" * 64
    manifest_path = bundle / "runtime_integrity.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    before = manifest_path.read_bytes()
    # Execute the build's actual Python step without invoking a build or a live app.
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    code = text.split("<<'PY_INTEGRITY'\n", 1)[1].split("\nPY_INTEGRITY", 1)[0]
    result = subprocess.run([sys.executable, "-", str(bundle)], input=code, text=True,
                            capture_output=True, env={**os.environ, "AUTOYOU_TEST_ROOT": str(tmp_path)})
    if damage:
        assert result.returncode != 0
        assert manifest_path.read_bytes() == before
    else:
        assert result.returncode == 0, result.stderr
        refreshed = json.loads(manifest_path.read_text())
        assert refreshed["files"] == {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        assert refreshed["allowed_python_files"] == manifest["allowed_python_files"]
        for name, data in files.items():
            assert (bundle / name).read_bytes() == data


@pytest.mark.skipif(sys.platform == "win32", reason="Exercises the Linux build shell")
@pytest.mark.parametrize("extra, message", [
    ([], "requires --unofficial"),
    (["--unofficial", "--accept-terms"], "Cannot both skip and record"),
])
def test_wsl_validation_acknowledgment_option_rejects_conflicting_modes(tmp_path, extra, message):
    script = tmp_path / "build-backend.sh"
    shutil.copyfile(BUILD_SCRIPT, script)
    result = subprocess.run(
        ["bash", str(script), "--skip-local-build-acknowledgement", *extra],
        text=True, capture_output=True, check=False,
        env={**os.environ, "AUTOYOU_TEST_ROOT": str(tmp_path / "state")},
    )
    assert result.returncode == 2
    assert message in result.stderr
    assert not (tmp_path / "state").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Exercises the Linux build shell")
def test_wsl_validation_build_does_not_forge_or_call_license_acceptance(tmp_path):
    script = tmp_path / "repo/servers/wsl/build-backend.sh"
    script.parent.mkdir(parents=True)
    shutil.copyfile(BUILD_SCRIPT, script)
    calls = tmp_path / "python-calls"
    python = tmp_path / "python"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$AUTOYOU_TEST_CALLS"\nexit 0\n')
    python.chmod(0o700)
    result = subprocess.run(
        ["bash", str(script), "--unofficial", "--skip-local-build-acknowledgement", "--python", str(python)],
        text=True, capture_output=True, check=False,
        env={**os.environ, "AUTOYOU_TEST_ROOT": str(tmp_path / "state"), "AUTOYOU_TEST_CALLS": str(calls)},
    )
    assert result.returncode == 1
    assert "No local license acknowledgment recorded" in result.stdout
    assert "Missing packaged guide staging helper" in result.stderr
    assert "acknowledge_local_build.py" not in calls.read_text()
    assert not (tmp_path / "state").exists()
