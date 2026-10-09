"""Public, signed-package inputs for the owned Iroh generation.

This module never reads credentials or writes installation state. Legacy source
and packages remain usable without a native SDK or a Core configuration.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

IROH_VERSION = "82.0.0"
MAX_CONFIG_BYTES = 64 * 1024
SDK_API = {"schema": 1, "api_version": 1, "wire_version": 1, "core_version": "0.1.0",
           "iroh_version": "1.3.0", "noq_version": "1.3.0", "uniffi_version": "0.32.2",
           "rust_toolchain": "1.99.0"}
TARGETS = {
    "windows-x86_64": ("x86_64-pc-windows-msvc", "autoyou_client_bindings.dll", "Windows 10 19041"),
    "macos-aarch64": ("aarch64-apple-darwin", "libautoyou_client_bindings.dylib", "macOS 13"),
    "macos-x86_64": ("x86_64-apple-darwin", "libautoyou_client_bindings.dylib", "macOS 13"),
    "linux-x86_64": ("x86_64-unknown-linux-gnu", "libautoyou_client_bindings.so", "glibc 2.35"),
    "linux-aarch64": ("aarch64-unknown-linux-gnu", "libautoyou_client_bindings.so", "glibc 2.35"),
    "android-arm64-v8a": ("aarch64-linux-android", "libautoyou_client_bindings.so", "Android API 29"),
    "android-x86_64": ("x86_64-linux-android", "libautoyou_client_bindings.so", "Android API 29"),
    "ios-aarch64": ("aarch64-apple-ios", "libautoyou_client_bindings.a", "iOS 17"),
    "ios-simulator-aarch64": ("aarch64-apple-ios-sim", "libautoyou_client_bindings.a", "iOS 17"),
    "ios-simulator-x86_64": ("x86_64-apple-ios", "libautoyou_client_bindings.a", "iOS 17"),
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def read_json(path: Path, limit: int = MAX_CONFIG_BYTES) -> dict:
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Iroh input is missing or oversized")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate Iroh input key")
            result[key] = value
        return result
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("Iroh input must be an object")
    return value


def _public_url(value: object, *, origin: bool = False, proxy: bool = False) -> None:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("invalid public Iroh URL")
    url = urlsplit(value)
    if (url.scheme not in ({"http", "https"} if proxy else {"https"}) or not url.hostname or url.username is not None or url.password is not None
            or url.port == 0 or url.query or url.fragment or url.path not in ("", "/")
            or (origin and value != f"https://{url.netloc}")):
        raise ValueError("Iroh URLs require a credential-free HTTPS origin")


def validate_config(value: dict) -> dict:
    if (set(value) != {"schema_version", "generation", "version", "policy", "core"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["generation"] != "iroh" or value["version"] != IROH_VERSION):
        raise ValueError("unsupported Iroh release configuration")
    if len(json.dumps(value).encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ValueError("Iroh policy exceeds the native input bound")
    core = value["core"]
    if not isinstance(core, dict) or set(core) != {"issuer", "public_key"}:
        raise ValueError("explicit public Core pins are required")
    _public_url(core["issuer"], origin=True)
    key = core["public_key"]
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key) or key == "00" * 32:
        raise ValueError("invalid Core public key")
    policy = value["policy"]
    allowed = {"bind_addresses", "relays", "relay_only", "local_only", "allow_lan_peers",
               "brokered_relays", "proxy_url", "extra_ca_der", "advertise_direct_hints"}
    if not isinstance(policy, dict) or set(policy) - allowed or "bind_addresses" not in policy:
        raise ValueError("invalid managed endpoint policy")
    for flag in allowed - {"bind_addresses", "relays", "proxy_url", "extra_ca_der"}:
        if flag in policy and type(policy[flag]) is not bool:
            raise ValueError("invalid endpoint policy flag")
    if policy.get("local_only") or policy.get("brokered_relays") is not True:
        raise ValueError("packages require brokered public routing without embedded relay credentials")
    binds = policy["bind_addresses"]
    if not isinstance(binds, list) or len(binds) > 2 or (not binds and not policy.get("relay_only")):
        raise ValueError("invalid endpoint binds")
    families = set()
    for address in binds:
        if not isinstance(address, str):
            raise ValueError("invalid endpoint bind")
        host, sep, port = address.rpartition(":")
        ip = ipaddress.ip_address(host.strip("[]"))
        if not sep or not port.isdecimal() or not 0 <= int(port) <= 65535 or ip.version in families:
            raise ValueError("invalid or duplicate endpoint bind family")
        families.add(ip.version)
    relays = policy.get("relays", [])
    if not isinstance(relays, list) or len(relays) > 8 or (policy.get("relay_only") and not relays):
        raise ValueError("invalid relay allowlist")
    urls = set()
    for relay in relays:
        if not isinstance(relay, dict) or set(relay) - {"url", "token"} or relay.get("token", "") != "":
            raise ValueError("relay credentials cannot be packaged")
        _public_url(relay.get("url"))
        normalized = relay["url"].rstrip("/")
        if normalized in urls:
            raise ValueError("duplicate relay URL")
        urls.add(normalized)
    if policy.get("proxy_url") is not None:
        _public_url(policy["proxy_url"], proxy=True)
    roots = policy.get("extra_ca_der", [])
    if (not isinstance(roots, list) or len(roots) > 8 or any(not isinstance(root, str) for root in roots)
            or len(set(roots)) != len(roots)):
        raise ValueError("invalid corporate CA roots")
    for root in roots:
        if not isinstance(root, str):
            raise ValueError("invalid corporate CA root")
        raw = base64.b64decode(root, validate=True)
        if not raw or len(raw) > 16 * 1024 or base64.b64encode(raw).decode() != root:
            raise ValueError("invalid corporate CA root")
        from cryptography.x509 import load_der_x509_certificate
        load_der_x509_certificate(raw)
    return value


def load_release_config(*, resources_root: Path | None = None) -> dict | None:
    test_root = os.environ.get("AUTOYOU_TEST_ROOT")
    if resources_root is None:
        if test_root:
            return None
        from shared.platform_runtime import get_resources_root
        resources_root = get_resources_root(Path(__file__).resolve().parents[1] / "server.py")
    elif test_root and not resources_root.resolve().is_relative_to(Path(test_root).resolve()):
        raise ValueError("test release resources must be isolated")
    path = resources_root / "transport" / "release.json"
    if not path.exists():
        return None
    return validate_config(read_json(path))


def server_transport_config(config: dict) -> dict:
    release = load_release_config()
    transport = config.get("session_transport", {})
    if not isinstance(transport, dict):
        raise ValueError("invalid session transport configuration")
    if release is None:
        return transport
    # An explicit legacy mode is the rollback switch; protected stores are untouched.
    result = dict(mode="prefer_iroh", iroh=release["policy"], core=release["core"])
    result.update({key: value for key, value in transport.items() if key not in {"iroh", "core"}})
    return result


def source_fingerprints(workspace: Path) -> dict[str, str]:
    return {p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for p in sorted(workspace.rglob("*"))
            if p.is_file() and p.suffix in {".rs", ".toml"} and "target" not in p.relative_to(workspace).parts}


def validate_sdk(root: Path, *, workspace: Path, target: str) -> dict:
    if target not in TARGETS:
        raise ValueError("unsupported Iroh SDK target")
    value = read_json(root / "sdk-manifest.json", 1024 * 1024)
    if (any(type(value.get(k)) is not type(v) or value.get(k) != v for k, v in SDK_API.items()) or value.get("target_tag") != target
            or value.get("target_triple") != TARGETS[target][0]
            or value.get("lock_sha256") != digest(workspace / "Cargo.lock")
            or value.get("source_fingerprints") != source_fingerprints(workspace)):
        raise ValueError("native SDK source, dependency graph or target mismatch")
    files = value.get("files")
    if not isinstance(files, dict) or not files or len(files) > 1024:
        raise ValueError("missing native SDK file inventory")
    required = {TARGETS[target][1], "generated/autoyou_client_bindings.py", "generated/AutoYouTransport.swift",
                "generated/AutoYouTransportFFI.h", "generated/AutoYouTransportFFI.modulemap",
                "generated/uniffi/autoyou_client_bindings/autoyou_client_bindings.kt", "legal/NOTICE.txt",
                "legal/sbom.cdx.json", "legal/covered-sources.zip"}
    if set(files) != required:
        raise ValueError("incomplete native SDK")
    for name, expected in files.items():
        p = (root / name).resolve()
        if (not isinstance(name, str) or "\\" in name or p == root.resolve() or not p.is_relative_to(root.resolve())
                or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
                or not p.is_file() or p.stat().st_size > 256 * 1024 * 1024 or digest(p) != expected):
            raise ValueError("native SDK integrity failure")
    return value
