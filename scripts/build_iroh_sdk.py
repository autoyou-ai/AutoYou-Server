#!/usr/bin/env python3
"""Build the owned native SDK for one declared target, never a product package.

Use --plan to review commands without compiling. Product/final SDK builds are
reserved for the final build milestone. Toolchains and signing remain owned by
the calling platform's existing release workflow.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import zipfile

SERVER = Path(__file__).resolve().parents[1]
WORKSPACE = SERVER / "transport-rust"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
from shared.iroh_release import SDK_API, TARGETS, digest, source_fingerprints


def commands(cargo: str, target: str, library: Path, generated: Path) -> list[list[str]]:
    manifest = str(WORKSPACE / "Cargo.toml")
    return [
        [cargo, "+1.99.0", "build", "--manifest-path", manifest, "--locked", "--release", "--lib",
         "-p", "autoyou-client-bindings", "--target", TARGETS[target][0]],
        [cargo, "+1.99.0", "run", "--manifest-path", manifest, "--locked", "--release", "-p",
         "autoyou-client-bindings", "--features", "bindgen", "--bin", "autoyou-bindgen", "--", str(library), str(generated)],
    ]


def target_environment(target: str, build: Path, ndk: Path | None) -> dict[str, str]:
    env = dict(os.environ, CARGO_TARGET_DIR=str(build.resolve()), CARGO_INCREMENTAL="0")
    # A release SDK must not inherit a fixture's loopback-only policy or test identity.
    if env.get("AUTOYOU_TEST_ROOT"):
        raise ValueError("product SDK builds cannot run under a test runtime root")
    triple = TARGETS[target][0]
    if target.startswith("android-"):
        if ndk is None or "Pkg.Revision = 27.1.12297006" not in (ndk / "source.properties").read_text():
            raise ValueError("the frozen Android NDK 27.1.12297006 is required")
        host = {"Windows": "windows-x86_64", "Darwin": "darwin-x86_64", "Linux": "linux-x86_64"}[platform.system()]
        tools = ndk / "toolchains/llvm/prebuilt" / host / "bin"
        clang = tools / (triple + "29-clang" + (".cmd" if os.name == "nt" else ""))
        ar = tools / ("llvm-ar.exe" if os.name == "nt" else "llvm-ar")
        if not clang.is_file() or not ar.is_file():
            raise ValueError("Android target compiler is missing")
        env.update(ANDROID_NDK_HOME=str(ndk.resolve()), ANDROID_PLATFORM="android-29", ANDROID_STL="c++_static",
                   CARGO_ENCODED_RUSTFLAGS="-Clink-arg=-Wl,-z,max-page-size=16384\x1f-Clink-arg=-Wl,-z,common-page-size=16384")
        env["CARGO_TARGET_" + triple.upper().replace("-", "_") + "_LINKER"] = str(clang)
        env["CC_" + triple.replace("-", "_")] = str(clang)
        env["AR_" + triple.replace("-", "_")] = str(ar)
    elif target.startswith(("ios-", "macos-")):
        if platform.system() != "Darwin":
            raise ValueError("Apple SDKs must be built on the owning macOS host")
        if target.startswith("ios-"):
            env["IPHONEOS_DEPLOYMENT_TARGET"] = "17.0"
        else:
            env["MACOSX_DEPLOYMENT_TARGET"] = "13.0"
    elif target.startswith("windows-") and platform.system() != "Windows":
        raise ValueError("Windows SDKs require the owning MSVC host")
    elif target.startswith("linux-") and platform.system() != "Linux":
        raise ValueError("Linux/WSL SDKs require the owning glibc Linux host")
    return env


def validate_binary(path: Path, target: str) -> dict:
    data = path.read_bytes()
    if target.startswith(("android-", "linux-")):
        if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
            raise ValueError("64-bit little-endian ELF is required")
        machine = struct.unpack_from("<H", data, 18)[0]
        if machine != (183 if target.endswith(("aarch64", "arm64-v8a")) else 62):
            raise ValueError("native ELF architecture mismatch")
        offset = struct.unpack_from("<Q", data, 32)[0]
        entry, count = struct.unpack_from("<HH", data, 54)
        if entry < 56 or not count or count > 1024 or offset + entry * count > len(data):
            raise ValueError("invalid ELF program table")
        loads = [struct.unpack_from("<Q", data, offset + i * entry + 48)[0] for i in range(count)
                 if struct.unpack_from("<I", data, offset + i * entry)[0] == 1]
        if not loads or (target.startswith("android-") and any(alignment < 16384 for alignment in loads)):
            raise ValueError("Android LOAD segments must support 16 KiB pages")
        relro = [struct.unpack_from("<Q", data, offset+i*entry+16)[0] + struct.unpack_from("<Q", data, offset+i*entry+40)[0]
                 for i in range(count) if struct.unpack_from("<I", data, offset+i*entry)[0] == 0x6474e552]
        if target.startswith("android-") and any(end % 16384 for end in relro):
            raise ValueError("Android RELRO must end on a 16 KiB boundary")
        versions = [tuple(map(int, m.split(b"."))) for m in re.findall(rb"GLIBC_([0-9]+\.[0-9]+(?:\.[0-9]+)?)", data)]
        if target.startswith("linux-") and versions and max(versions) > (2, 35, 0):
            raise ValueError("Linux SDK exceeds the frozen glibc 2.35 baseline")
        return {"format": "ELF64", "architecture": machine, "load_alignment": min(loads), "relro_aligned": True, "status": "passed"}
    if target.startswith("windows-"):
        if len(data) < 64 or data[:2] != b"MZ":
            raise ValueError("invalid Windows native library")
        pe = struct.unpack_from("<I", data, 60)[0]
        if pe + 26 > len(data) or data[pe:pe+4] != b"PE\0\0" or struct.unpack_from("<H", data, pe+4)[0] != 0x8664:
            raise ValueError("native PE architecture mismatch")
        return {"format": "PE64", "architecture": "x86_64", "status": "passed"}
    arch = "arm64" if target.endswith("aarch64") else "x86_64"
    subprocess.run(["xcrun", "lipo", str(path), "-verify_arch", arch], check=True, capture_output=True)
    return {"format": "Mach-O/archive", "architecture": arch, "status": "passed"}


def write_legal(output: Path, metadata: dict, cargo_home: Path) -> None:
    """Ship exact covered crate archives and upstream notices, with the complete locked build graph."""
    legal = output / "legal"
    legal.mkdir()
    locked = {(p["name"], p["version"]): p for p in tomllib.loads((WORKSPACE / "Cargo.lock").read_text())["package"]}
    notices = ["AutoYou owned Iroh SDK — dependency notices\nThe enclosing product's license and release legal gates still apply.\n",
               "Unmodified MPL covered source is included in covered-sources.zip.\n",
               "SBOM inventories the locked build and runtime graph; target linkage is recorded separately.\n"]
    components = []
    with zipfile.ZipFile(legal / "covered-sources.zip", "w", compression=zipfile.ZIP_STORED) as covered:
        for item in sorted(metadata["packages"], key=lambda p: (p["name"], p["version"])):
            if not item.get("source"):
                continue
            record = locked[(item["name"], item["version"])]
            license_value = item.get("license")
            if not license_value:
                raise ValueError("native dependency has unknown license: " + item["name"])
            directory = Path(item["manifest_path"]).parent
            archive = next(cargo_home.glob(f"registry/cache/*/{item['name']}-{item['version']}.crate"), None)
            if archive is None or digest(archive) != record["checksum"]:
                raise ValueError("dependency archive provenance failure: " + item["name"])
            uri = f"https://crates.io/api/v1/crates/{item['name']}/{item['version']}/download"
            notices.append(f"\n{item['name']} {item['version']} — {license_value}\nOriginal source: {uri}\nSHA-256: {record['checksum']}\n")
            if "MPL-2.0" in license_value:
                covered.write(archive, archive.name)
            candidates = sorted(p for p in directory.rglob("*") if p.is_file() and
                (p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "PATENTS")) or p.name.upper() in {"AUTHORS", "NOTICE"}))
            for path in candidates:
                if path.stat().st_size <= 256 * 1024:
                    notices.append(f"\n[{path.relative_to(directory).as_posix()}]\n" + path.read_text(encoding="utf-8", errors="replace"))
            if not candidates and item["name"].startswith("sonora"):
                fallback = SERVER / "transport-rust/third-party/sonora-LICENSE"
                notices.append("\n[Upstream workspace BSD license]\n" + fallback.read_text(encoding="utf-8"))
            components.append({"type": "library", "name": item["name"], "version": item["version"],
                "purl": f"pkg:cargo/{item['name']}@{item['version']}", "licenses": [{"expression": license_value}],
                "hashes": [{"alg": "SHA-256", "content": record["checksum"]}],
                "externalReferences": [{"type": "distribution", "url": uri}]})
    (legal / "NOTICE.txt").write_text("\n".join(notices), encoding="utf-8", newline="\n")
    (legal / "sbom.cdx.json").write_text(json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "metadata": {"properties": [{"name": "autoyou:inventory-scope", "value": "locked-build-and-runtime-graph"}]},
        "components": components}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--ndk", type=Path)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    target, native, minimum = TARGETS[args.target]
    library = args.build_root.resolve() / target / "release" / native
    if args.plan:
        print(json.dumps({"target": args.target, "minimum": minimum,
            "commands": commands(args.cargo, args.target, library, args.output / "generated"),
            "final_build": False}, indent=2))
        return 0
    if args.output.exists():
        parser.error("--output must be a fresh SDK directory; retained SDKs are never overwritten")
    env = target_environment(args.target, args.build_root, args.ndk)
    env["CARGO_HOME"] = str(args.cargo_home.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="iroh-sdk-", dir=args.output.parent) as name:
        stage = Path(name)
        for command in commands(args.cargo, args.target, library, stage / "generated"):
            subprocess.run(command, cwd=WORKSPACE, env=env, check=True)
        binary = validate_binary(library, args.target)
        shutil.copy2(library, stage / native)
        metadata = json.loads(subprocess.check_output([args.cargo, "+1.99.0", "metadata", "--manifest-path", str(WORKSPACE / "Cargo.toml"),
            "--locked", "--format-version", "1"], cwd=WORKSPACE, env=env))
        write_legal(stage, metadata, args.cargo_home)
        files = {p.relative_to(stage).as_posix(): digest(p) for p in sorted(stage.rglob("*")) if p.is_file()}
        (stage / "sdk-manifest.json").write_text(json.dumps(dict(SDK_API, target_tag=args.target, target_triple=target,
            minimum_os=minimum, binary_validation=binary, lock_sha256=digest(WORKSPACE / "Cargo.lock"),
            source_fingerprints=source_fingerprints(WORKSPACE), files=files), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        from shared.iroh_release import validate_sdk
        validate_sdk(stage, workspace=WORKSPACE, target=args.target)
        shutil.copytree(stage, args.output)
    print("Owned Iroh SDK staged: " + str(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
