#!/usr/bin/env python3
"""Stage a validated owned SDK and public configuration before package signing."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

SERVER = Path(__file__).resolve().parents[1]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
from shared.iroh_release import (SDK_API, TARGETS, digest, load_release_config, read_json,
                                 python_library_name, source_fingerprints, validate_config, validate_sdk)


def stage_runtime(bundle: Path, *, generation: str, sdk: Path | None = None, config: Path | None = None,
                  target: str | None = None, workspace: Path = SERVER / "transport-rust") -> list[str]:
    if generation not in {"legacy", "iroh"}:
        raise ValueError("unknown release generation")
    if generation == "legacy":
        if (bundle / "transport").exists():
            raise ValueError("legacy package contains new-generation transport assets")
        return []
    if sdk is None or config is None or target is None or target.startswith(("android-", "ios-")):
        raise ValueError("desktop Iroh packages require a target SDK and public configuration")
    metadata = validate_sdk(sdk, workspace=workspace, target=target)
    release = validate_config(read_json(config))
    destination = bundle / "transport"
    if destination.exists():
        raise ValueError("refusing to replace an existing transport package")
    # Validate all inputs before touching the output; the enclosing build owns this fresh directory.
    native = TARGETS[target][1]
    binding = destination / "iroh" / target
    binding.mkdir(parents=True)
    python_native = python_library_name(target)
    components = [(sdk / native, native), (sdk / "generated/autoyou_client_bindings.py", "autoyou_client_bindings.py"),
                  (sdk / python_native, python_native)]
    for source, name in components:
        shutil.copy2(source, binding / name)
        original = source.relative_to(sdk).as_posix()
        if digest(binding / name) != metadata["files"][original]:
            raise ValueError("native SDK changed during staging")
    manifest = dict(SDK_API, target_tag=target, lock_sha256=metadata["lock_sha256"],
        source_fingerprints=metadata["source_fingerprints"],
        files={name: digest(binding / name) for _, name in components})
    (binding / "binding-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name in metadata["files"]:
        if name.startswith("legal/"):
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sdk / name, path)
            if digest(path) != metadata["files"][name]:
                raise ValueError("native SDK legal input changed during staging")
    (destination / "release.json").write_text(json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (bundle / "VERSION").write_text(release["version"] + "\n", encoding="utf-8")
    return ["VERSION", *[p.relative_to(bundle).as_posix() for p in sorted(destination.rglob("*")) if p.is_file()]]


def extend_integrity(bundle: Path, manifest: dict, paths: list[str]) -> None:
    if not paths:
        return
    manifest.setdefault("tracked_roots", []).append("transport")
    manifest.setdefault("files", {}).update({path: digest(bundle / path) for path in paths})
    manifest.setdefault("allowed_python_files", []).extend(path for path in paths if path.endswith(".py"))


def refresh_signed_bindings(bundle: Path) -> None:
    """Signing can change native libraries; wrapper/source/config trust remains sealed by the app."""
    for path in (bundle / "transport/iroh").glob("*/binding-manifest.json"):
        manifest = read_json(path)
        target = manifest["target_tag"]
        native = TARGETS[target][1]
        python_native = python_library_name(target)
        files = manifest["files"]
        if set(files) != {native, python_native, "autoyou_client_bindings.py"} or digest(path.parent / "autoyou_client_bindings.py") != files["autoyou_client_bindings.py"]:
            raise ValueError("generated wrapper changed during signing")
        files[native] = digest(path.parent / native)
        files[python_native] = digest(path.parent / python_native)
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stage_from_environment(bundle: Path, manifest: dict) -> None:
    generation = os.environ.get("AUTOYOU_RELEASE_GENERATION", "iroh")
    if generation != "iroh":
        raise ValueError("This native backend requires the owned Iroh SDK; legacy WebRTC is a transport policy, not an SDK-free package")
    from shared.iroh_binding import target_tag
    paths = stage_runtime(bundle, generation=generation,
        sdk=Path(os.environ["AUTOYOU_IROH_SDK"]) if os.environ.get("AUTOYOU_IROH_SDK") else None,
        config=Path(os.environ["AUTOYOU_IROH_CONFIG"]) if os.environ.get("AUTOYOU_IROH_CONFIG") else None,
        target=target_tag() if generation == "iroh" else None)
    extend_integrity(bundle, manifest, paths)


def prepare_source_runtime(source: Path) -> dict | None:
    """Prepare the private backend's SDK before importing application services."""
    source = source.resolve()
    test_root = os.environ.get("AUTOYOU_TEST_ROOT")
    generation = os.environ.get("AUTOYOU_RELEASE_GENERATION")
    if test_root and not source.is_relative_to(Path(test_root).resolve()):
        if generation == "iroh":
            raise ValueError("test source transport resources must be isolated")
        return None
    destination = source / "transport"
    generation = generation or "iroh"
    if generation == "legacy":
        raise ValueError("This native backend requires the owned Iroh SDK; legacy WebRTC is a transport policy, not an SDK-free package")
    if generation != "iroh":
        raise ValueError("unknown release generation")
    if sys.version_info < (3, 11):
        raise ValueError("the Iroh generation requires Python 3.11 or newer")
    from shared.iroh_binding import target_tag
    target = target_tag()
    workspace = source / "transport-rust"
    if not destination.exists():
        sdk, config = os.environ.get("AUTOYOU_IROH_SDK"), os.environ.get("AUTOYOU_IROH_CONFIG")
        if not sdk or not config:
            raise ValueError("Iroh source startup requires AUTOYOU_IROH_SDK and AUTOYOU_IROH_CONFIG")
        with tempfile.TemporaryDirectory(prefix=".iroh-source-", dir=source) as temporary:
            bundle = Path(temporary)
            stage_runtime(bundle, generation="iroh", sdk=Path(sdk), config=Path(config),
                          target=target, workspace=workspace)
            if destination.exists():
                raise ValueError("refusing to replace existing source transport resources")
            (bundle / "transport").rename(destination)
    release = load_release_config(resources_root=source)
    if release is None:
        raise ValueError("source transport resources are missing the public release configuration")
    binding = destination / "iroh" / target
    metadata = read_json(binding / "binding-manifest.json")
    lock = (workspace / "Cargo.lock").read_bytes().replace(b"\r\n", b"\n")
    if (metadata.get("source_fingerprints") != source_fingerprints(workspace)
            or metadata.get("lock_sha256") not in {hashlib.sha256(raw).hexdigest()
                                                    for raw in (lock, lock.replace(b"\n", b"\r\n"))}):
        raise ValueError("source transport SDK is stale; stage an SDK built from this checkout")
    return release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--refresh-signed", action="store_true")
    args = parser.parse_args(argv)
    if args.refresh_signed:
        refresh_signed_bindings(args.bundle)
        path = args.bundle / "runtime_integrity.json"
        if path.exists():
            manifest = read_json(path, 32 * 1024 * 1024)
            for name in manifest["files"]:
                manifest["files"][name] = digest(args.bundle / name)
            path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        path = args.bundle / "runtime_integrity.json"
        manifest = read_json(path, 32 * 1024 * 1024)
        stage_from_environment(args.bundle, manifest)
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
