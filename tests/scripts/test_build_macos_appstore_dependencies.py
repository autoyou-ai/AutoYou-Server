# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from scripts import build_macos_appstore_dependencies as store


SOURCE = '''
int uv__thread_setname(const char* title) { return title[0] == 'A' ? 7 : 9; }
int uv__set_process_title(const char* title) {
#if TARGET_OS_IPHONE
  return uv__thread_setname(title);
#else
  extern int private_apple_api(const char*);
  return private_apple_api("_LSSetApplicationLaunchServicesServerConnectionStatus");
#endif
}
int main(void) { return uv__set_process_title("AutoYou synthetic helper") == 7 ? 0 : 1; }
'''


@pytest.mark.parametrize("helper", ["uv__thread_setname", "uv__pthread_setname_np"])
def test_libuv_patch_is_idempotent_and_preserves_the_source(tmp_path, helper):
    path = tmp_path / "darwin-proctitle.c"
    source = SOURCE.replace("uv__thread_setname", helper)
    path.write_text(source)
    assert store.patch_libuv(tmp_path) == 1
    expected = source.replace("#if TARGET_OS_IPHONE", store.PUBLIC_TITLE_BRANCH)
    assert path.read_text() == expected
    assert store.patch_libuv(tmp_path) == 1
    assert path.read_text() == expected


@pytest.mark.parametrize("text", ["unexpected upstream layout", SOURCE.replace("#if TARGET_OS_IPHONE", "#if OTHER_PLATFORM")])
def test_unknown_libuv_layout_fails_without_overwriting(tmp_path, text):
    path = tmp_path / "darwin-proctitle.c"
    path.write_text(text)
    with pytest.raises(RuntimeError, match="layout changed"):
        store.patch_libuv(tmp_path)
    assert path.read_text() == text


@pytest.mark.skipif(not shutil.which("cc"), reason="requires a C compiler")
@pytest.mark.parametrize("helper", ["uv__thread_setname", "uv__pthread_setname_np"])
def test_public_branch_compiles_and_executes_without_the_private_api(tmp_path, helper):
    source = tmp_path / "darwin-proctitle.c"
    source.write_text(SOURCE.replace("uv__thread_setname", helper))
    store.patch_libuv(tmp_path)
    binary = tmp_path / "synthetic-helper"
    subprocess.run(["cc", "-DTARGET_OS_IPHONE=0", str(source), "-o", str(binary)], check=True)
    assert store.PRIVATE_TITLE_API not in binary.read_bytes()
    subprocess.run([str(binary)], check=True)


def test_cached_wheel_with_private_api_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "minimum_macos", lambda path: (14, 0))
    wheel = tmp_path / "synthetic.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("torch/lib/libtorch_cpu.dylib", b"\xcf\xfa\xed\xfe" + store.PRIVATE_TITLE_API)
    with pytest.raises(RuntimeError, match="still references"):
        store.verify_wheel(wheel)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("uvloop/loop.so", b"\xcf\xfa\xed\xfe synthetic public code")
    store.verify_wheel(wheel)


@pytest.mark.parametrize("architecture", ["arm64", "x86_64"])
def test_torch_wheel_build_preserves_version_target_and_reuses_verified_cache(tmp_path, monkeypatch, architecture):
    monkeypatch.setattr(store.platform, "machine", lambda: architecture)
    monkeypatch.setattr(store, "minimum_macos", lambda path: (14, 0))
    monkeypatch.setattr(store, "checkout", lambda repository, version, destination: destination.mkdir())
    def build(command, *, env, check):
        assert env["PYTORCH_BUILD_VERSION"] == "2.13.0"
        assert env["PYTORCH_BUILD_NUMBER"] == "1"
        assert env["USE_DISTRIBUTED"] == "1"
        assert env["CMAKE_FRESH"] == "1"
        assert env["CMAKE_LIBRARY_PATH"].split(";")[0] == str((tmp_path / "openmp/lib").resolve())
        assert env["MACOSX_DEPLOYMENT_TARGET"] == "14.0"
        assert env["ARCHFLAGS"] == f"-arch {architecture}"
        assert env["_PYTHON_HOST_PLATFORM"] == f"macosx-14.0-{architecture}"
        assert env["BUILD_TEST"] == "0" and env["MAX_JOBS"] == "2"
        directory = Path(command[command.index("--wheel-dir") + 1])
        with zipfile.ZipFile(directory / "synthetic-torch.whl", "w") as archive:
            archive.writestr("torch/lib/libtorch_cpu.dylib", b"synthetic public code")
    monkeypatch.setattr(store.subprocess, "run", build)
    wheel = store.build_wheel("torch", "pytorch/pytorch", "2.13.0", tmp_path, 2)
    def unexpected_build(*args, **kwargs):
        raise AssertionError("An already verified cached wheel must not be rebuilt")
    monkeypatch.setattr(store.subprocess, "run", unexpected_build)
    assert store.build_wheel("torch", "pytorch/pytorch", "2.13.0", tmp_path, 2) == wheel


def test_node_replacement_rejects_private_api_without_changing_destination(tmp_path):
    source, destination = tmp_path / "built-node", tmp_path / "node"
    source.write_bytes(store.PRIVATE_TITLE_API)
    destination.write_bytes(b"original generated helper")
    with pytest.raises(RuntimeError, match="still references"):
        store.replace_node(source, destination)
    assert destination.read_bytes() == b"original generated helper"


@pytest.mark.parametrize("output, expected", [
    ("platform MACOS\n minos 11.0\n sdk 11.0\n version 956.6", (11, 0)),
    ("minos 11.0\nminos 14.0", (14, 0)),
    ("cmd LC_VERSION_MIN_MACOSX\n cmdsize 16\n version 10.13\n sdk 14.0", (10, 13)),
])
def test_minimum_macos_uses_deployment_metadata_not_linker_version(tmp_path, monkeypatch, output, expected):
    monkeypatch.setattr(store.subprocess, "check_output", lambda *args, **kwargs: output)
    assert store.minimum_macos(tmp_path / "synthetic.dylib") == expected


def test_wheel_requiring_a_newer_macos_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "minimum_macos", lambda path: (26, 0))
    wheel = tmp_path / "synthetic.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("torch/lib/libomp.dylib", b"synthetic public library")
    with pytest.raises(RuntimeError, match="newer macOS than 14"):
        store.verify_wheel(wheel)


@pytest.mark.parametrize("compatible", [True, False])
def test_openmp_uses_the_pinned_compatible_runtime_and_reuses_it(tmp_path, monkeypatch, compatible):
    target = tmp_path / "openmp/lib/libomp.dylib"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"newer macOS library")
    downloads = tmp_path / "openmp/upstream"
    downloads.mkdir()
    wheel = downloads / "synthetic-torch.whl"
    payload = b"compatible OpenMP" if compatible else b"newer macOS library"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("torch/lib/libomp.dylib", payload)
    monkeypatch.setattr(store, "minimum_macos", lambda path: (11, 0) if path.read_bytes() == b"compatible OpenMP" else (26, 0))
    def unexpected_download(*args, **kwargs):
        raise AssertionError("An existing upstream wheel or verified runtime must be reused")
    monkeypatch.setattr(store.subprocess, "run", unexpected_download)
    if compatible:
        assert store.prepare_openmp(tmp_path, "2.13.0") == target
        assert target.read_bytes() == payload
        wheel.unlink()
        assert store.prepare_openmp(tmp_path, "2.13.0") == target
    else:
        with pytest.raises(RuntimeError, match="newer macOS than 14"):
            store.prepare_openmp(tmp_path, "2.13.0")
        assert target.read_bytes() == b"newer macOS library"


@pytest.mark.parametrize("configured", [False, True])
def test_source_build_refuses_insufficient_space_before_cloning_or_compiling(tmp_path, monkeypatch, capsys, configured):
    monkeypatch.setattr(store.sys, "platform", "darwin")
    monkeypatch.setattr(store.sys, "argv", ["store-dependencies", "--build-dir", str(tmp_path / "build"),
                                          "--node-root", str(tmp_path / "node")])
    monkeypatch.setattr(store.subprocess, "check_output", lambda *args, **kwargs: "v22.16.0")
    monkeypatch.setattr(store.importlib.metadata, "version", lambda name: {"torch": "2.13.0", "uvloop": "0.23.0"}[name])
    monkeypatch.setattr(store.shutil, "disk_usage", lambda path: SimpleNamespace(free=(19 if configured else 29) * 1024**3))
    if configured:
        import hashlib
        work = tmp_path / "build" / (hashlib.sha256(Path(store.__file__).read_bytes()).hexdigest()[:12]
                                     + f"-{store.sys.implementation.cache_tag}-{store.platform.machine()}")
        node = work / "node-22.16.0/out/Release/node"
        node.parent.mkdir(parents=True)
        node.write_bytes(b"synthetic cached node")
        cache = work / "torch-2.13.0/source/build/CMakeCache.txt"
        cache.parent.mkdir(parents=True)
        cache.touch()
    def unexpected_build(*args, **kwargs):
        raise AssertionError("Low-space preflight must stop before cloning or compiling")
    monkeypatch.setattr(store.subprocess, "run", unexpected_build)
    with pytest.raises(SystemExit) as stopped:
        store.main()
    assert stopped.value.code == 2
    assert ("20 GiB" if configured else "30 GiB") in capsys.readouterr().err
