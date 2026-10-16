# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Private native presentation paths and deletion fences, without a transcript."""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 4096
FENCE_TTL_MS = 7 * 24 * 60 * 60 * 1000
_budget_lock = threading.RLock()


def storage_name(identifier: str, filename: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ValueError("invalid file operation identifier")
    extension = Path(filename).suffix.lstrip(".")
    if not re.fullmatch(r"[A-Za-z0-9]{1,16}", extension):
        extension = "bin"
    return f"{identifier}-payload.{extension}"


def deleted(target: Path) -> bool:
    return (target.parent / ("." + target.name.split("-", 1)[0] + ".deleted")).exists()


def reserve(temporary: Path, total: int, *, now_ms: int | None = None,
            maximum_bytes: int = MAX_BYTES, maximum_entries: int = MAX_ENTRIES) -> int:
    """Reserve disk budget before bounded writes, including concurrent writers.

    The single endpoint owner serializes admission. Sparse temporary lengths
    count against the budget without allocating a plaintext memory buffer.
    History owns completed payload retention; pressure rejects new promotions.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    directory = temporary.parent
    root = directory.parent
    if not re.fullmatch(r"[0-9a-f]{64}", directory.name) or \
            not re.fullmatch(r"\.[0-9a-f]{32}\.partial", temporary.name) or type(total) is not int or total < 0:
        raise ValueError("invalid native presentation reservation")
    with _budget_lock:
        if root.is_symlink() or directory.is_symlink():
            raise ValueError("invalid native presentation directory")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        size = entries = 0
        for scope in root.iterdir():
            if scope.is_symlink() or not scope.is_dir() or not re.fullmatch(r"[0-9a-f]{64}", scope.name):
                raise ValueError("invalid native presentation scope")
            entries += 1
            for item in scope.iterdir():
                if item.is_symlink() or not item.is_file():
                    raise ValueError("invalid native presentation file")
                if re.fullmatch(r"\.[0-9a-f]{32}\.deleted", item.name) and item.stat().st_mtime_ns // 1000000 + FENCE_TTL_MS < now_ms:
                    item.unlink()
                    continue
                entries += 1
                if entries >= maximum_entries:
                    raise OSError("native presentation entry quota is exhausted")
                size += item.stat().st_size
            if entries >= maximum_entries:
                raise OSError("native presentation entry quota is exhausted")
        if size + total > maximum_bytes:
            raise OSError("native presentation byte quota is exhausted")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.ftruncate(descriptor, total)
        except BaseException:
            os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise
        return descriptor


def client_root() -> Path:
    from shared.platform_runtime import get_service_data_dir
    return get_service_data_dir("iroh/client/resume", app_name="AutoYou-Iroh").resolve() / "native-file-history"


def find_payload(handle: str, *, allowed_root: Path | None = None) -> Path | None:
    """Resolve an opaque local bridge handle within the owned payload store.

    No pathname is accepted from a transcript or peer. The existing bounded
    store owns payload retention; this lookup does not create another journal.
    """
    import hashlib
    if not isinstance(handle, str) or not re.fullmatch(r"[0-9a-f]{32}", handle):
        raise ValueError("invalid native history handle")
    selected = allowed_root or client_root()
    if selected.is_symlink():
        raise ValueError("invalid native history directory")
    root = selected.resolve()
    if not root.exists():
        return None
    count = 0
    for scope in root.iterdir():
        count += 1
        if count > MAX_ENTRIES or scope.is_symlink() or not scope.is_dir() or not re.fullmatch(r"[0-9a-f]{64}", scope.name):
            raise ValueError("invalid native history scope")
        for item in scope.iterdir():
            count += 1
            if count > MAX_ENTRIES or item.is_symlink() or not item.is_file():
                raise ValueError("invalid native history file")
            if not re.fullmatch(r"[0-9a-f]{32}-payload\.[A-Za-z0-9]{1,16}", item.name):
                continue
            if hashlib.sha256(str(item).encode("utf-8")).hexdigest()[:32] == handle:
                return None if deleted(item) else item
    return None


def forget(target: Path, *, allowed_root: Path | None = None) -> bool:
    """Only a host-chosen, scoped native path can create a deletion fence."""
    target = Path(target)
    root = (allowed_root or client_root()).resolve()
    identifier = target.name.split("-", 1)[0]
    if target.parent.parent.resolve() != root or not re.fullmatch(r"[0-9a-f]{64}", target.parent.name) or \
            not re.fullmatch(r"[0-9a-f]{32}", identifier) or target.is_symlink() or target.parent.is_symlink():
        return False
    marker = target.parent / ("." + identifier + ".deleted")
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    target.unlink(missing_ok=True)
    return True
