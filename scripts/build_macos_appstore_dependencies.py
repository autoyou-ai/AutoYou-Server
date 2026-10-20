#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Rebuild libuv consumers with its public process-title fallback for the Mac App Store."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import zipfile


PRIVATE_TITLE_API = b"_LSSetApplicationLaunchServicesServerConnectionStatus"
PUBLIC_TITLE_BRANCH = "#if 1 /* AutoYou App Store: libuv public thread-name fallback. */"


def patch_libuv(source: Path) -> int:
    files = sorted(source.rglob("darwin-proctitle.c"))
    if not files:
        raise RuntimeError(f"No vendored libuv process-title source in {source}")
    for path in files:
        text = path.read_text(encoding="utf-8")
        if PUBLIC_TITLE_BRANCH in text:
            continue
        fallback = re.search(r"#if TARGET_OS_IPHONE\n\s*return uv__(?:thread_setname|pthread_setname_np)\(title\);\n#else\n", text)
        if text.count("#if TARGET_OS_IPHONE\n") != 1 or not fallback:
            raise RuntimeError(f"libuv process-title layout changed; review before building: {path}")
        path.write_text(text.replace("#if TARGET_OS_IPHONE\n", PUBLIC_TITLE_BRANCH + "\n"), encoding="utf-8")
    return len(files)


def checkout(repository: str, version: str, destination: Path) -> None:
    tag = "v" + version
    url = f"https://github.com/{repository}.git"
    if not destination.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", tag,
                        "--recurse-submodules", "--shallow-submodules", url, str(destination)], check=True)
    remote = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=destination, text=True).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=destination, text=True).strip()
    release = subprocess.check_output(["git", "rev-parse", f"{tag}^{{commit}}"], cwd=destination, text=True).strip()
    if remote != url or head != release:
        raise RuntimeError(f"Unexpected source checkout; preserving it for inspection: {destination}")
    patch_libuv(destination)


def verify_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.endswith((".so", ".dylib")) and PRIVATE_TITLE_API in archive.read(name):
                raise RuntimeError(f"Rebuilt wheel still references the private process-title API: {wheel.name}/{name}")


def build_wheel(name: str, repository: str, version: str, work: Path, jobs: int) -> Path:
    directory = work / f"{name}-{version}"
    wheels = directory / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    built = list(wheels.glob("*.whl"))
    if not built:
        source = (directory / "source").resolve()
        checkout(repository, version, source)
        architecture = platform.machine()
        env = dict(os.environ, MAX_JOBS=str(jobs), BUILD_TEST="0", MACOSX_DEPLOYMENT_TARGET="14.0",
                   ARCHFLAGS=f"-arch {architecture}", _PYTHON_HOST_PLATFORM=f"macosx-14.0-{architecture}")
        if name == "torch":
            env.update(PYTORCH_BUILD_VERSION=version, PYTORCH_BUILD_NUMBER="1", USE_DISTRIBUTED="1", CMAKE_FRESH="1")
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheels),
                        str(source)], env=env, check=True)
        built = list(wheels.glob("*.whl"))
    if len(built) != 1:
        raise RuntimeError(f"Expected one {name} wheel in {wheels}")
    verify_wheel(built[0])
    return built[0]


def replace_node(source: Path, destination: Path) -> None:
    if PRIVATE_TITLE_API in source.read_bytes():
        raise RuntimeError(f"Node still references the private process-title API: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    shutil.copy2(source, destination)
    subprocess.run(["codesign", "--force", "--sign", "-", str(destination)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--node-root", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    if sys.platform != "darwin" or args.jobs < 1:
        parser.error("Requires macOS and a positive --jobs value")
    node_root = args.node_root.resolve()
    node_version = subprocess.check_output([str(node_root / "bin/node"), "--version"], text=True).strip().removeprefix("v")
    versions = {name: importlib.metadata.version(name) for name in ("uvloop", "torch")}
    if any(not re.fullmatch(r"\d+\.\d+\.\d+", version) for version in (node_version, *versions.values())):
        parser.error("Expected stable release versions for Node, uvloop, and PyTorch")
    patch_id = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
    work = args.build_dir.resolve() / f"{patch_id}-{sys.implementation.cache_tag}-{platform.machine()}"
    work.mkdir(parents=True, exist_ok=True)
    node_source = work / f"node-{node_version}"
    node_binary = node_source / "out/Release/node"
    needs_build = not node_binary.is_file() or any(
        not list((work / f"{name}-{version}/wheels").glob("*.whl")) for name, version in versions.items())
    configured_torch = work / f"torch-{versions['torch']}/source/build/CMakeCache.txt"
    required_gib = 20 if node_binary.is_file() and configured_torch.is_file() else 30
    if needs_build and shutil.disk_usage(work).free < required_gib * 1024**3:
        parser.error(f"The App Store dependency source builds need at least {required_gib} GiB free on the selected build volume")

    if not node_binary.is_file():
        checkout("nodejs/node", node_version, node_source)
        env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET="14.0")
        subprocess.run(["./configure"], cwd=node_source, env=env, check=True)
        subprocess.run(["make", f"-j{args.jobs}"], cwd=node_source, env=env, check=True)
    replace_node(node_binary, node_root / "bin/node")
    playwright = importlib.metadata.distribution("playwright")
    replace_node(node_binary, Path(playwright.locate_file("playwright/driver/node")))

    for name, repository in (("uvloop", "MagicStack/uvloop"), ("torch", "pytorch/pytorch")):
        wheel = build_wheel(name, repository, versions[name], work, args.jobs)
        subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)], check=True)
        if importlib.metadata.version(name) != versions[name]:
            raise RuntimeError(f"Rebuilt {name} changed the pinned version")
    print("App Store Node, Playwright driver, uvloop, and PyTorch dependencies prepared; scan the final signed app.")


if __name__ == "__main__":
    main()
