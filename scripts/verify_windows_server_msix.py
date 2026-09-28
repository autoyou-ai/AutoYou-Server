#!/usr/bin/env python3
"""Verify an AutoYou Server MSIX artifact before Store upload."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote
from zipfile import BadZipFile, ZipFile


LEGAL_FILES = ("LICENSE", "NOTICE.txt", "sbom.cdx.json", "THIRD-PARTY-NOTICES.md")
REQUIRED_PAYLOAD_PATHS = (
    "AutoYou.exe",
    "AutoYou.dll",
    "AutoYou.deps.json",
    "AutoYou.runtimeconfig.json",
    "Assets/TrayLogo.png",
    "Backend/AutoYou.exe",
    "Backend/runtime/node/node.exe",
    "Backend/runtime/tunnelmole/tmole.exe",
    "release-profile.json",
    "Backend/release-profile.json",
) + tuple(f"Legal/{name}" for name in LEGAL_FILES) + tuple(f"Backend/Legal/{name}" for name in LEGAL_FILES)
REQUIRED_PAYLOAD_PREFIXES = (
    "Backend/runtime/playwright",
    "Backend/node/whatsapp",
    "Backend/runtime_modules/autoyou_agents",
)
REQUIRED_PAYLOAD_PATH_GROUPS = (
    (
        "Backend/runtime_site_packages/google/adk/cli/browser/index.html",
        "Backend/google/adk/cli/browser/index.html",
    ),
    (
        "Backend/runtime_site_packages/google/adk/cli/browser/assets/audio-processor.js",
        "Backend/google/adk/cli/browser/assets/audio-processor.js",
    ),
    (
        "Backend/runtime_site_packages/google/adk/cli/browser/assets/config/runtime-config.json",
        "Backend/google/adk/cli/browser/assets/config/runtime-config.json",
    ),
)
BLOCKED_MUTABLE_FILE_NAMES = {
    "config.keystore.enc",
    "config.encrypted",
    "config.encrypted.bak",
    "agent_install_registry.json",
    "agent_frontends_registry.json",
    "login_ui_state.db",
    "page_feed.db",
    "sessions.db",
    "sessions.db.bak",
}
LOCAL_BUILD_PATH_MARKERS = (b"\\Projects\\AutoYou", b"/Projects/AutoYou")
MAX_BYTE_SCAN_SIZE = 16 * 1024 * 1024
MANIFEST_ASSET_ATTRIBUTES = {"Logo", "Square150x150Logo", "Square44x44Logo", "Wide310x150Logo", "Image"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _normalized_names(package: ZipFile) -> dict[str, str]:
    names: dict[str, str] = {}
    for info in package.infolist():
        normalized = info.filename.replace("\\", "/")
        names.setdefault(normalized, info.filename)
        names.setdefault(unquote(normalized), info.filename)
    return names


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _attr(element: ET.Element, name: str) -> str | None:
    return next((value for key, value in element.attrib.items() if _local_name(key) == name), None)


def _manifest_errors(
    manifest: str,
    *,
    identity_name: str,
    publisher: str,
    publisher_display_name: str,
) -> list[str]:
    try:
        root = ET.fromstring(manifest)
    except ET.ParseError as exc:
        return [f"manifest XML parse failed: {exc}"]

    errors: list[str] = []
    elements = list(root.iter())
    identity = next((element for element in elements if _local_name(element.tag) == "Identity"), None)
    if identity is None:
        errors.append("manifest XML missing Identity")
    else:
        if _attr(identity, "Name") != identity_name:
            errors.append(f"identity name mismatch: expected {identity_name}, got {_attr(identity, 'Name')}")
        if _attr(identity, "Publisher") != publisher:
            errors.append(f"publisher mismatch: expected {publisher}, got {_attr(identity, 'Publisher')}")

    actual_display_name = next(
        ((element.text or "").strip() for element in elements if _local_name(element.tag) == "PublisherDisplayName"),
        None,
    )
    if actual_display_name != publisher_display_name:
        errors.append(
            "publisher display name mismatch: "
            f"expected {publisher_display_name}, got {actual_display_name}"
        )

    capabilities = {
        _attr(element, "Name")
        for element in elements
        if _local_name(element.tag) in {"Capability", "DeviceCapability"}
    }
    for capability in ("runFullTrust", "privateNetworkClientServer", "bluetooth"):
        if capability not in capabilities:
            errors.append(f"manifest XML missing {capability} capability")

    applications = [element for element in elements if _local_name(element.tag) == "Application"]
    if not any(_attr(application, "RuntimeBehavior") == "packagedClassicApp" for application in applications):
        errors.append("manifest XML missing packagedClassicApp runtime behavior")
    if not any(_attr(application, "TrustLevel") == "mediumIL" for application in applications):
        errors.append("manifest XML missing mediumIL trust level")
    if any(_local_name(element.tag) == "Service" for element in elements) or re.search(
        r"<\s*(?:[A-Za-z0-9._-]+:)?Service\b", manifest
    ):
        errors.append("manifest includes a Service declaration")
    if any(marker in manifest for marker in ("allowElevation", "requireAdministrator", "windows.service")):
        errors.append("manifest includes a blocked elevation or service marker")
    if any(
        (_attr(element, "Language") or "").lower() == "x-generate"
        for element in elements
        if _local_name(element.tag) == "Resource"
    ):
        errors.append("manifest XML uses unsupported Store language: x-generate")
    return errors


def _manifest_asset_references(manifest: str) -> list[str]:
    try:
        root = ET.fromstring(manifest)
    except ET.ParseError:
        return []
    return [
        value.replace("\\", "/")
        for element in root.iter()
        for key, value in element.attrib.items()
        if _local_name(key) in MANIFEST_ASSET_ATTRIBUTES and value
    ]


def _release_profile_errors(package: ZipFile, names: dict[str, str]) -> list[str]:
    profiles: list[dict[str, object]] = []
    errors: list[str] = []
    for path in ("release-profile.json", "Backend/release-profile.json"):
        if path not in names:
            continue
        try:
            profile = json.loads(package.read(names[path]).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{path} is invalid JSON: {exc}")
            continue
        if not isinstance(profile, dict):
            errors.append(f"{path} must contain a JSON object")
            continue
        for key in ("releaseProfile", "dependencyProfile", "artifactProfile", "legalBundle"):
            if not isinstance(profile.get(key), str) or not profile[key].strip():
                errors.append(f"{path} is missing {key}")
        profiles.append(profile)
    if len(profiles) == 2:
        for key in ("releaseProfile", "dependencyProfile", "artifactProfile", "legalBundle"):
            if profiles[0].get(key) != profiles[1].get(key):
                errors.append(f"release profile mismatch between root and Backend: {key}")
    return errors


def verify_server_msix(
    path: Path,
    expected_sha256: str,
    identity_name: str,
    publisher: str,
    publisher_display_name: str,
) -> dict[str, object]:
    if not path.is_file():
        return {"path": str(path), "ok": False, "errors": [f"missing file: {path}"]}

    errors: list[str] = []
    actual_sha256 = sha256_file(path)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        errors.append("expected SHA-256 must be 64 hexadecimal characters")
    elif actual_sha256 != expected_sha256.upper():
        errors.append(f"sha256 mismatch: expected {expected_sha256.upper()}, got {actual_sha256}")

    try:
        with ZipFile(path) as package:
            names = _normalized_names(package)
            manifest_name = names.get("AppxManifest.xml")
            if manifest_name is None:
                errors.append("missing AppxManifest.xml")
                manifest = ""
            else:
                try:
                    manifest = package.read(manifest_name).decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    errors.append(f"AppxManifest.xml is not UTF-8: {exc}")
                    manifest = ""

            if manifest:
                errors.extend(
                    _manifest_errors(
                        manifest,
                        identity_name=identity_name,
                        publisher=publisher,
                        publisher_display_name=publisher_display_name,
                    )
                )
                for asset_path in _manifest_asset_references(manifest):
                    if asset_path not in names:
                        errors.append(f"manifest references missing asset: {asset_path}")

            for required_path in REQUIRED_PAYLOAD_PATHS:
                if required_path not in names:
                    errors.append(f"missing payload path: {required_path}")
            for prefix in REQUIRED_PAYLOAD_PREFIXES:
                normalized_prefix = prefix.rstrip("/") + "/"
                if not any(name.startswith(normalized_prefix) for name in names):
                    errors.append(f"missing payload folder: {prefix}")
            for alternatives in REQUIRED_PAYLOAD_PATH_GROUPS:
                if not any(path_option in names for path_option in alternatives):
                    errors.append(f"missing payload path: {' or '.join(alternatives)}")
            errors.extend(_release_profile_errors(package, names))

            for info in package.infolist():
                normalized_name = info.filename.replace("\\", "/")
                file_name = normalized_name.rsplit("/", 1)[-1].lower()
                if file_name in BLOCKED_MUTABLE_FILE_NAMES:
                    errors.append(f"payload includes mutable runtime state: {normalized_name}")
                elif file_name.endswith(".pdb"):
                    errors.append(f"payload includes debug symbol file: {normalized_name}")
                elif info.file_size <= MAX_BYTE_SCAN_SIZE and any(
                    marker in package.read(info.filename) for marker in LOCAL_BUILD_PATH_MARKERS
                ):
                    errors.append(f"payload includes local build path marker: {normalized_name}")
    except (BadZipFile, OSError) as exc:
        errors.append(f"invalid MSIX archive: {exc}")

    return {
        "path": str(path),
        "sha256": actual_sha256,
        "ok": not errors,
        "errors": errors,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-msix", type=Path, required=True)
    parser.add_argument("--server-sha256", required=True)
    parser.add_argument("--server-identity-name", required=True)
    parser.add_argument("--server-publisher", required=True)
    parser.add_argument("--server-publisher-display-name", required=True)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = verify_server_msix(
        args.server_msix,
        args.server_sha256,
        args.server_identity_name,
        args.server_publisher,
        args.server_publisher_display_name,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{'OK' if result['ok'] else 'FAIL'}: AutoYou Server MSIX - {result['path']}")
        for error in result["errors"]:
            print(f"  - {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
