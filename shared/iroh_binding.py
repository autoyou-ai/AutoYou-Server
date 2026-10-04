# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Load an attested binding from owned application resources, never from PATH."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import threading
from pathlib import Path
from types import ModuleType

from shared.platform_runtime import get_resources_root

_EXPECTED = {
    "schema": 1, "api_version": 1, "wire_version": 1, "core_version": "0.1.0",
    "iroh_version": "1.3.0", "noq_version": "1.3.0", "uniffi_version": "0.32.2",
    "rust_toolchain": "1.99.0",
}
_LOCK = threading.RLock()
_LOADED: dict[tuple[str, str, str], ModuleType] = {}


class IrohBindingUnavailable(RuntimeError):
    """A packaging/capability failure, never permission to downgrade a denial."""


def target_tag() -> str:
    system = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(platform.system())
    architecture = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
        platform.machine().lower(),
    )
    if not system or not architecture:
        raise IrohBindingUnavailable("Iroh binding target is unsupported")
    return f"{system}-{architecture}"


def library_name() -> str:
    if sys.platform == "win32":
        return "autoyou_client_bindings.dll"
    if sys.platform == "darwin":
        return "libautoyou_client_bindings.dylib"
    return "libautoyou_client_bindings.so"


def _digest(path: Path, maximum_bytes: int) -> str:
    if not path.is_file() or path.stat().st_size > maximum_bytes:
        raise IrohBindingUnavailable("Iroh binding artifact is missing or oversized")
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def load_binding(*, artifact_root: Path | None = None) -> ModuleType:
    """An explicit artifact root is reserved for hermetic qualification.

    Installed runtimes use the fixed resources path. The enclosing signed
    distribution owns manifest authenticity; these checks also reject mixed
    generated wrappers, architectures and dependency graphs before startup.
    """
    if sys.version_info < (3, 11):
        raise IrohBindingUnavailable("the Iroh generation requires Python 3.11 or newer")
    test_root = os.environ.get("AUTOYOU_TEST_ROOT")
    if artifact_root is not None:
        root = Path(artifact_root).resolve()
        if not test_root or not root.is_relative_to(Path(test_root).resolve()):
            raise IrohBindingUnavailable("explicit binding artifacts require an isolated test root")
    else:
        if test_root:
            # A test must never accidentally load an installed candidate.
            raise IrohBindingUnavailable("test binding artifacts must be explicitly scoped")
        anchor = Path(__file__).resolve().parents[1] / "server.py"
        root = (get_resources_root(anchor) / "transport" / "iroh" / target_tag()).resolve()
    manifest_path = root / "binding-manifest.json"
    try:
        if manifest_path.stat().st_size > 64 * 1024:
            raise ValueError
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in _EXPECTED.items()):
            raise ValueError
        if manifest.get("target_tag") != target_tag():
            raise ValueError
        lock_hash = manifest["lock_sha256"]
        if not isinstance(lock_hash, str) or len(lock_hash) != 64 or any(c not in "0123456789abcdef" for c in lock_hash):
            raise ValueError
        module_name, native_name = "autoyou_client_bindings.py", library_name()
        files = manifest["files"]
        if not isinstance(files, dict) or set(files) != {module_name, native_name}:
            raise ValueError
        paths = {name: (root / name).resolve() for name in files}
        if any(path.parent != root for path in paths.values()):
            raise ValueError
        for name, path in paths.items():
            if files[name] != _digest(path, 8 * 1024 * 1024 if name == module_name else 256 * 1024 * 1024):
                raise ValueError
        cache_key = (str(root), files[module_name], files[native_name])
        with _LOCK:
            if cache_key in _LOADED:
                return _LOADED[cache_key]
            root_scope = hashlib.sha256(str(root).encode()).hexdigest()[:16]
            name = f"_autoyou_iroh_{files[native_name]}_{root_scope}"
            module = ModuleType(name)
            module.__file__ = str(paths[module_name])
            sys.modules[name] = module
            try:
                source = paths[module_name].read_bytes()
                if hashlib.sha256(source).hexdigest() != files[module_name]:
                    raise ValueError
                exec(compile(source, module.__file__, "exec"), module.__dict__)
                actual = module.core_info()
                for key in ("api_version", "wire_version", "core_version", "iroh_version", "noq_version", "uniffi_version"):
                    if getattr(actual, key) != manifest[key]:
                        raise ValueError
                if actual.lock_sha256 != lock_hash:
                    raise ValueError
            except BaseException:
                if sys.modules.get(name) is module:
                    sys.modules.pop(name)
                raise
            _LOADED[cache_key] = module
            return module
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        raise IrohBindingUnavailable("Iroh binding provenance or ABI validation failed") from None
