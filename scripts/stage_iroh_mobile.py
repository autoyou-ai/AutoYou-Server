#!/usr/bin/env python3
"""Prepare validated Android inputs or an iOS Swift package and public assets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

SERVER = Path(__file__).resolve().parents[1]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
from shared.iroh_release import TARGETS, digest, read_json, validate_config, validate_sdk


def mobile_inputs(sdk_root: Path, config: Path, targets: tuple[str, ...], workspace: Path) -> tuple[dict, dict]:
    release = validate_config(read_json(config))
    metadata = {target: validate_sdk(sdk_root / target, workspace=workspace, target=target) for target in targets}
    # Different architecture slices must expose the same generated public ABI.
    for name in ("generated/AutoYouTransport.swift", "generated/AutoYouTransportFFI.h",
                 "generated/uniffi/autoyou_client_bindings/autoyou_client_bindings.kt", "legal/sbom.cdx.json"):
        if len({value["files"][name] for value in metadata.values()}) != 1:
            raise ValueError("mobile SDK slices disagree on wrappers or the locked graph")
    return release, metadata


def assets(output: Path, release: dict, sdk: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("iroh-policy.json", release["policy"]), ("iroh-core.json", release["core"]), ("iroh-release.json", release)):
        (output / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    shutil.copytree(sdk / "legal", output / "legal/iroh")


def stage_android(sdk_root: Path, config: Path, output: Path, *, workspace: Path = SERVER / "transport-rust") -> None:
    targets = ("android-arm64-v8a", "android-x86_64")
    release, metadata = mobile_inputs(sdk_root, config, targets, workspace)
    expected_files = {}
    for name, expected in metadata[targets[0]]["files"].items():
        if name.startswith("legal/"):
            expected_files["assets/legal/iroh/" + name.removeprefix("legal/")] = expected
        elif name.startswith("generated/uniffi/"):
            expected_files["generated/kotlin/" + name.removeprefix("generated/")] = expected
    for target in targets:
        native = TARGETS[target][1]
        expected_files[f"jniLibs/{target.removeprefix('android-')}/{native}"] = metadata[target]["files"][native]
    for name, value in (("iroh-policy.json", release["policy"]), ("iroh-core.json", release["core"]), ("iroh-release.json", release)):
        expected_files["assets/" + name] = hashlib.sha256((json.dumps(value, indent=2, sort_keys=True) + "\n").encode()).hexdigest()
    if output.exists() and any(output.iterdir()):
        # A repeat build may reuse a complete identical set; never replace an unknown SDK.
        inventory = read_json(output / "mobile-inputs.json", 1024 * 1024)
        if inventory["sdk_manifests"] != {t: digest(sdk_root / t / "sdk-manifest.json") for t in targets} or inventory["config_sha256"] != digest(config):
            raise ValueError("mobile output belongs to different release inputs; select a fresh output")
        if inventory["files"] != expected_files:
            raise ValueError("staged mobile inventory differs from trusted inputs")
        for name, expected in expected_files.items():
            if digest(output / name) != expected:
                raise ValueError("staged mobile input was modified")
        return
    # Gradle creates declared output directories before running an Exec task.
    output.mkdir(parents=True, exist_ok=True)
    first = sdk_root / targets[0]
    shutil.copytree(first / "generated/uniffi", output / "generated/kotlin/uniffi")
    assets(output / "assets", release, first)
    for target in targets:
        source = sdk_root / target / TARGETS[target][1]
        dest = output / "jniLibs" / target.removeprefix("android-") / source.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        if digest(dest) != metadata[target]["files"][source.name]:
            raise ValueError("Android native library changed during staging")
    (output / "mobile-inputs.json").write_text(json.dumps(dict(schema_version=1,
        sdk_manifests={t: digest(sdk_root / t / "sdk-manifest.json") for t in targets}, config_sha256=digest(config),
        files=expected_files), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stage_ios(sdk_root: Path, config: Path, output: Path, *, workspace: Path = SERVER / "transport-rust") -> None:
    targets = ("ios-aarch64", "ios-simulator-aarch64", "ios-simulator-x86_64")
    release, _ = mobile_inputs(sdk_root, config, targets, workspace)
    if output.exists():
        raise ValueError("iOS package output must be fresh")
    output.mkdir(parents=True)
    first = sdk_root / targets[0]
    source = output / "Sources/AutoYouTransport"
    source.mkdir(parents=True)
    shutil.copy2(first / "generated/AutoYouTransport.swift", source / "AutoYouTransport.swift")
    headers = output / "headers"
    headers.mkdir()
    for name in ("AutoYouTransportFFI.h", "AutoYouTransportFFI.modulemap"):
        shutil.copy2(first / "generated" / name, headers / ("module.modulemap" if name.endswith("modulemap") else name))
    simulator = output / "libautoyou_client_bindings-simulator.a"
    subprocess.run(["xcrun", "lipo", "-create", *[str(sdk_root / t / TARGETS[t][1]) for t in targets[1:]], "-output", str(simulator)], check=True)
    subprocess.run(["xcodebuild", "-create-xcframework", "-library", str(first / TARGETS[targets[0]][1]), "-headers", str(headers),
        "-library", str(simulator), "-headers", str(headers), "-output", str(output / "AutoYouTransportFFI.xcframework")], check=True)
    (output / "Package.swift").write_text('''// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "AutoYouTransport", platforms: [.iOS(.v17)],
    products: [.library(name: "AutoYouTransport", targets: ["AutoYouTransport"])],
    targets: [.binaryTarget(name: "AutoYouTransportFFI", path: "AutoYouTransportFFI.xcframework"),
        .target(name: "AutoYouTransport", dependencies: ["AutoYouTransportFFI"],
            linkerSettings: [.linkedFramework("Security"), .linkedFramework("CoreFoundation"),
                .linkedFramework("SystemConfiguration"), .linkedLibrary("resolv"), .linkedLibrary("c++")])])
''', encoding="utf-8")
    assets(output / "assets", release, first)
    (output / "mobile-inputs.json").write_text(json.dumps(dict(schema_version=1,
        sdk_manifests={t: digest(sdk_root / t / "sdk-manifest.json") for t in targets}, config_sha256=digest(config),
        files={p.relative_to(output).as_posix(): digest(p) for p in output.rglob("*") if p.is_file()}), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("android", "ios"), required=True)
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    (stage_android if args.platform == "android" else stage_ios)(args.sdk_root.resolve(), args.config.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
