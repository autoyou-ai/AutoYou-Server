# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-b3f2c49bda1e8a088b16d985

"""Process-wide at-rest protection for Secure Professional Maximus.

The application has several independently-owned SQLite and JSON stores.  A
single storage boundary keeps those callers unchanged while ensuring that a
Secure Professional Maximus process never leaves its mutable data in
plaintext on disk:

* JSON and binary files are Fernet envelopes with an explicit header.
* SQLite databases live in a shared in-memory connection and are encrypted
  as a snapshot on disk.  Existing plaintext databases are migrated on first
  open and removed only after the encrypted snapshot is durable.
* The Fernet key comes from the OS credential store, with a password-derived
  key and per-installation salt as the deliberate fallback.

This is an application-level SQLite adapter rather than a SQLCipher
dependency.  It keeps the existing sqlite3/aiosqlite/SQLAlchemy callers and
therefore does not require a client protocol change.  The process-wide
adapter is intentionally small; the database lock is the one concurrency
boundary for snapshot writes.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-b3f2c49bda1e8a088b16d985"


import base64
import gc
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import tempfile
import time
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .keystore import (
    _backend_name,
    get_or_create_key,
    keyring_available,
    macos_keychain_bootstrap_timeout_seconds,
    replace_key,
)


LOGGER = logging.getLogger("autoyou.secure_storage")

SECURE_PROFESSIONAL_MAXIMUS_MODE = "secure_professional_maximus"
FILE_HEADER = b"AUTOYOU-SPM-FILE-v1\n"
SQLITE_HEADER = b"AUTOYOU-SPM-SQLITE-v1\n"
_SQLITE_DATABASE_MAGIC = b"SQLite format 3\x00"
KEY_MODE_FILE = ".secure-professional-maximus.key-mode"
KEY_SALT_FILE = ".secure-professional-maximus.salt"
KEY_USERNAME = "secure-professional-maximus-master-key"
PBKDF2_ITERATIONS = 600_000
_DISCOVERY_SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        ".venv-publish-smoke",
        ".tmp",
        ".claude",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".codex_runlogs",
        ".llm",
        ".run",
        "DO_NOT_UPLOAD",
        "autoyou-outreach",
        "build",
        "cognee",
        "core_server",
        "docs",
        "reference",
        "requirements",
        "routers",
        "screenshots",
        "scripts",
        "shared",
        "tests",
        "vendor",
        "clients",
        "servers",
        "research",
        "openclaw",
        "whatsapp",
        "autoyou-core",
        "autoyou-website",
        # autoyou_lite runs its OWN separate Maximus boundary (app_name
        # "AutoYouLite") with a DIFFERENT key. Its sealed output lives under
        # this subtree inside the repo, which is a full-server scan root - the
        # full server must never walk into it or rotation/unseal trips over
        # envelopes it cannot decrypt with its own key.
        "autoyou_lite",
    }
)

_ORIGINAL_SQLITE_CONNECT = sqlite3.connect
_ORIGINAL_SQLITE_DBAPI_CONNECT = sqlite3.dbapi2.connect
_STORAGE_LOCK = threading.RLock()
_CONTEXT_LOCK = threading.RLock()
_DATABASES_LOCK = threading.RLock()
_CONTEXT: Optional["_SecureStorageContext"] = None
_DATABASES: Dict[Path, "_SecureDatabaseState"] = {}
_PROTECTED_FILES: Set[Path] = set()


class SecureStorageError(RuntimeError):
    """Raised when protected storage cannot be opened safely."""


def _derive_password_key(password: str, salt: bytes) -> bytes:
    if not password:
        raise SecureStorageError("Secure Professional Maximus needs a storage password")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _fernet_key(raw_key: bytes) -> bytes:
    """Normalise raw keychain bytes and already-encoded fallback keys."""
    if len(raw_key) == 32:
        return base64.urlsafe_b64encode(raw_key)
    if len(raw_key) == 44:
        return raw_key
    raise SecureStorageError("The protected-storage key has an invalid length")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                gc.collect()
                time.sleep(0.05)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_marker(path: Path) -> str:
    try:
        value = path.read_text(encoding="ascii").strip().lower()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise SecureStorageError(f"Cannot read protected-storage key marker: {exc}") from exc
    if value not in {"keychain", "password"}:
        raise SecureStorageError("Protected-storage key marker is invalid")
    return value


def _safe_identifier(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower())
    return cleaned.strip("-") or "autoyou"


@dataclass
class _SecureStorageContext:
    app_name: str
    root: Path
    service_name: str
    key_source: str
    key: bytes
    fernet: Fernet
    keyring_backend: str = "none"


@dataclass
class _SecureDatabaseState:
    path: Path
    uri: str
    anchor: Any
    lock: threading.RLock = field(default_factory=threading.RLock)

    def persist(self) -> None:
        with _STORAGE_LOCK:
            with self.lock:
                try:
                    payload = _normalise_sqlite_snapshot(self.anchor.serialize())
                except Exception as exc:  # pragma: no cover - sqlite error text varies by platform.
                    raise SecureStorageError(
                        f"Cannot snapshot protected SQLite database {self.path.name}: {exc}"
                    ) from exc
                context = _require_context()
                envelope = SQLITE_HEADER + context.fernet.encrypt(payload)
                _atomic_write(self.path, envelope)
                _remove_sqlite_sidecars(self.path)


def _require_context() -> _SecureStorageContext:
    context = _CONTEXT
    if context is None:
        raise SecureStorageError("Secure Professional Maximus storage is not enabled")
    return context


def _normalise_sqlite_snapshot(payload: bytes) -> bytes:
    """Make WAL-marked snapshots reopenable in the in-memory adapter.

    ``Connection.serialize()`` can preserve SQLite's WAL header bytes.  The
    protected adapter restores snapshots into a shared in-memory database,
    where those bytes make SQLite look for an on-disk WAL sidecar and fail
    with ``unable to open database file``.  The serialized page image is
    complete, so switching the image back to rollback-compatible header
    values is sufficient and keeps the encrypted on-disk format unchanged.
    """
    if len(payload) < 20 or payload[:16] != _SQLITE_DATABASE_MAGIC:
        return payload
    if payload[18] != 2 and payload[19] != 2:
        return payload
    normalized = bytearray(payload)
    normalized[18] = 0
    normalized[19] = 0
    return bytes(normalized)


def _password_from_environment() -> Optional[str]:
    return (
        os.environ.get("AUTOYOU_SECURE_STORAGE_PASSWORD")
        or os.environ.get("AUTOYOU_SERVER_PASSWORD")
        or None
    )


def _load_or_create_key(
    *,
    app_name: str,
    root: Path,
    password: Optional[str],
    operation_timeout_seconds: Optional[float] = None,
    allow_key_creation: bool = True,
) -> Tuple[bytes, str, str, str]:
    root.mkdir(parents=True, exist_ok=True)
    marker_path = root / KEY_MODE_FILE
    salt_path = root / KEY_SALT_FILE
    marker = _read_marker(marker_path)
    identifier = _safe_identifier(app_name)
    root_digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:20]
    service_name = f"autoyou-{identifier}-spm-{root_digest}"

    if marker == "keychain":
        key_kwargs: Dict[str, Any] = {"create": False}
        if operation_timeout_seconds is not None:
            key_kwargs["operation_timeout_seconds"] = operation_timeout_seconds
        key = get_or_create_key(service_name, KEY_USERNAME, **key_kwargs)
        if key is None:
            raise SecureStorageError(
                "The protected-storage key is missing from the OS credential store"
            )
        return _fernet_key(key), "os_keychain", service_name, _backend_name()

    if marker == "password":
        try:
            salt = salt_path.read_bytes()
        except OSError as exc:
            raise SecureStorageError("The protected-storage password salt is missing") from exc
        if len(salt) < 16:
            raise SecureStorageError("The protected-storage password salt is invalid")
        return _derive_password_key(password or _password_from_environment() or "", salt), "password_fallback", service_name, "none"

    if not allow_key_creation:
        # Automatic startup must never create an SPM boundary just because a
        # migration/default changed the selected mode. A real admin save or
        # explicit unlock is the only place allowed to enrol a fresh key.
        raise SecureStorageError(
            "Secure Professional Maximus has no enrolled protected-storage key"
        )

    key: Optional[bytes] = None
    if keyring_available():
        key_kwargs = {"create": allow_key_creation}
        if operation_timeout_seconds is not None:
            key_kwargs["operation_timeout_seconds"] = operation_timeout_seconds
        key = get_or_create_key(service_name, KEY_USERNAME, **key_kwargs)
    if key is not None:
        _atomic_write(marker_path, b"keychain\n")
        return _fernet_key(key), "os_keychain", service_name, _backend_name()

    resolved_password = password or _password_from_environment()
    if not resolved_password:
        raise SecureStorageError(
            "Secure Professional Maximus needs an available OS credential store or server password"
        )
    salt = os.urandom(16)
    _atomic_write(salt_path, salt)
    _atomic_write(marker_path, b"password\n")
    return _derive_password_key(resolved_password, salt), "password_fallback", service_name, "none"


def _same_context(context: _SecureStorageContext, app_name: str, root: Path) -> bool:
    return context.app_name == app_name and context.root == root


def enable_secure_storage(
    *,
    app_name: str,
    root: str | Path,
    password: Optional[str] = None,
    operation_timeout_seconds: Optional[float] = None,
    allow_key_creation: bool = True,
) -> Dict[str, Any]:
    """Enable the Secure Professional Maximus storage boundary.

    The operation is idempotent for one application/root pair.  A different
    root cannot replace an active context while database handles are alive;
    this prevents a live service from writing one database with two keys.
    """

    global _CONTEXT
    resolved_root = Path(root).expanduser().resolve()
    with _STORAGE_LOCK:
        with _CONTEXT_LOCK:
            if _CONTEXT is not None:
                if not _same_context(_CONTEXT, app_name, resolved_root):
                    with _DATABASES_LOCK:
                        if _DATABASES:
                            raise SecureStorageError(
                                "Cannot change protected-storage roots while databases are open"
                            )
                    _CONTEXT = None
                    if sqlite3.connect is not _ORIGINAL_SQLITE_CONNECT:
                        sqlite3.connect = _ORIGINAL_SQLITE_CONNECT
                    if sqlite3.dbapi2.connect is not _ORIGINAL_SQLITE_DBAPI_CONNECT:
                        sqlite3.dbapi2.connect = _ORIGINAL_SQLITE_DBAPI_CONNECT

                else:
                    os.environ["AUTOYOU_SECURE_STORAGE_MODE"] = SECURE_PROFESSIONAL_MAXIMUS_MODE
                    os.environ["AUTOYOU_SECURE_STORAGE_ROOT"] = str(resolved_root)
                    os.environ["AUTOYOU_SECURE_STORAGE_APP"] = app_name
                    if password:
                        os.environ["AUTOYOU_SECURE_STORAGE_PASSWORD"] = password
                    _install_sqlite_adapter()
                    return secure_storage_status()

            key, key_source, service_name, backend = _load_or_create_key(
                app_name=app_name,
                root=resolved_root,
                password=password,
                operation_timeout_seconds=operation_timeout_seconds,
                allow_key_creation=allow_key_creation,
            )
            _CONTEXT = _SecureStorageContext(
                app_name=app_name,
                root=resolved_root,
                service_name=service_name,
                key_source=key_source,
                key=key,
                fernet=Fernet(key),
                keyring_backend=backend,
            )
            os.environ["AUTOYOU_SECURE_STORAGE_MODE"] = SECURE_PROFESSIONAL_MAXIMUS_MODE
            os.environ["AUTOYOU_SECURE_STORAGE_ROOT"] = str(resolved_root)
            os.environ["AUTOYOU_SECURE_STORAGE_APP"] = app_name
            if password:
                os.environ["AUTOYOU_SECURE_STORAGE_PASSWORD"] = password
            _install_sqlite_adapter()
            return secure_storage_status()


def disable_secure_storage() -> None:
    """Flush and remove the process-wide adapter during a controlled shutdown."""

    global _CONTEXT
    with _STORAGE_LOCK:
        with _CONTEXT_LOCK:
            with _DATABASES_LOCK:
                states = list(_DATABASES.values())
                for state in states:
                    state.persist()
                    try:
                        state.anchor.close()
                    except Exception:
                        pass
                _DATABASES.clear()
            _PROTECTED_FILES.clear()
            _CONTEXT = None
            if sqlite3.connect is not _ORIGINAL_SQLITE_CONNECT:
                sqlite3.connect = _ORIGINAL_SQLITE_CONNECT
            if sqlite3.dbapi2.connect is not _ORIGINAL_SQLITE_DBAPI_CONNECT:
                sqlite3.dbapi2.connect = _ORIGINAL_SQLITE_DBAPI_CONNECT
            for variable in (
                "AUTOYOU_SECURE_STORAGE_MODE",
                "AUTOYOU_SECURE_STORAGE_ROOT",
                "AUTOYOU_SECURE_STORAGE_APP",
                "AUTOYOU_SECURE_STORAGE_PASSWORD",
            ):
                os.environ.pop(variable, None)


def secure_storage_enabled() -> bool:
    return _CONTEXT is not None


def is_secure_professional_mode(mode: Any) -> bool:
    normalized = str(mode or "").strip().lower()
    return normalized in {
        "secure-pro",
        "secure_professional",
        "secure-professional",
        SECURE_PROFESSIONAL_MAXIMUS_MODE,
        "secure-professional-maximus",
    }


def enable_secure_storage_from_environment() -> Optional[Dict[str, Any]]:
    """Enable the inherited storage boundary in a worker process.

    Server-launched workers inherit the mode, root, app name, and password
    environment.  They must attach to that same boundary before opening a
    protected database or dataset instead of silently reading ciphertext as
    ordinary input. Workers are never an enrolment path: they only read an
    already-enrolled credential, using the same no-UI macOS policy as main
    server startup so a background worker cannot create a prompt storm.
    """

    mode = os.environ.get("AUTOYOU_SECURE_STORAGE_MODE")
    if not is_secure_professional_mode(mode):
        return None
    root = os.environ.get("AUTOYOU_SECURE_STORAGE_ROOT")
    if not root:
        raise SecureStorageError("Protected worker storage root is missing")
    return enable_secure_storage(
        app_name=os.environ.get("AUTOYOU_SECURE_STORAGE_APP") or "AutoYou",
        root=root,
        password=os.environ.get("AUTOYOU_SECURE_STORAGE_PASSWORD"),
        operation_timeout_seconds=macos_keychain_bootstrap_timeout_seconds(),
        allow_key_creation=False,
    )


def secure_storage_status() -> Dict[str, Any]:
    context = _CONTEXT
    with _DATABASES_LOCK:
        database_count = len(_DATABASES)
        file_count = len(_PROTECTED_FILES)
    if context is None:
        return {
            "enabled": False,
            "mode": None,
            "key_source": None,
            "keychain_backend": None,
            "protected_database_count": 0,
            "protected_file_count": 0,
        }
    return {
        "enabled": True,
        "mode": SECURE_PROFESSIONAL_MAXIMUS_MODE,
        "key_source": context.key_source,
        "keychain_backend": context.keyring_backend,
        "protected_database_count": database_count,
        "protected_file_count": file_count,
    }


def _protected_path_header(path: Path) -> Optional[bytes]:
    try:
        with path.open("rb") as handle:
            prefix = handle.read(max(len(FILE_HEADER), len(SQLITE_HEADER)))
    except OSError:
        return None
    if prefix.startswith(FILE_HEADER):
        return FILE_HEADER
    if prefix.startswith(SQLITE_HEADER):
        return SQLITE_HEADER
    return None


def _skip_discovery_directory(parent: str, name: str) -> bool:
    if name in _DISCOVERY_SKIP_DIRECTORIES:
        return True
    lowered = name.lower()
    if lowered.startswith(".venv-") or lowered.startswith(".codex-"):
        return True
    return (Path(parent) / name).is_symlink()


def sealed_envelope_kind(path: str | Path) -> Optional[str]:
    """Return ``"file"``/``"sqlite"`` when ``path`` holds an SPM envelope, else ``None``.

    Readers that open owned stores directly (``sqlite3.connect``, ``open``) use
    this to tell "this file is sealed" apart from "this file is corrupt". Without
    it a sealed database surfaces as SQLite's opaque "file is not a database".
    """
    header = _protected_path_header(Path(path))
    if header == FILE_HEADER:
        return "file"
    if header == SQLITE_HEADER:
        return "sqlite"
    return None


def ensure_secure_storage_for_path(path: str | Path) -> bool:
    """Attach to the inherited SPM boundary when ``path`` is a sealed envelope.

    A worker that opens an owned store before anything enabled the boundary would
    otherwise read ciphertext as input. Returns ``True`` when the boundary is
    active (already, or newly attached from the inherited environment) and
    ``False`` when the file is plaintext or no boundary is configured.
    """
    if secure_storage_enabled():
        return True
    if sealed_envelope_kind(path) is None:
        return False
    return enable_secure_storage_from_environment() is not None


def find_sealed_envelopes(scan_roots: Iterable[str | Path]) -> List[Path]:
    """List SPM envelopes under ``scan_roots`` without needing an active context.

    Startup uses this to detect stores stranded by a downgrade that never ran
    :func:`unseal_secure_storage` - they are sealed bytes that every plaintext
    reader fails closed on.
    """
    found: Set[Path] = set()
    for raw_root in scan_roots:
        if not raw_root:
            continue
        try:
            root = Path(raw_root).expanduser().resolve()
        except OSError:
            continue
        if not root.exists():
            continue
        if root.is_file():
            if _protected_path_header(root):
                found.add(root)
            continue
        for current, directories, filenames in os.walk(root, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if not _skip_discovery_directory(current, name)
            ]
            for filename in filenames:
                candidate = Path(current) / filename
                if candidate.is_symlink():
                    continue
                try:
                    if candidate.is_file() and _protected_path_header(candidate):
                        found.add(candidate.resolve())
                except OSError:
                    continue
    return sorted(found, key=lambda path: str(path))


def _discover_protected_paths(
    context: _SecureStorageContext,
    scan_roots: Iterable[str | Path],
) -> List[Path]:
    """Find current envelopes without treating arbitrary plaintext as owned data."""
    candidates: Set[Path] = set()
    with _DATABASES_LOCK:
        candidates.update(path.resolve() for path in _DATABASES)
        candidates.update(path.resolve() for path in _PROTECTED_FILES)

    test_root = str(os.environ.get("AUTOYOU_TEST_ROOT") or "").strip()
    temp_media_root = (
        Path(test_root).expanduser().resolve() / "autoyou_media"
        if test_root
        else Path(tempfile.gettempdir()) / "autoyou_media"
    )
    roots: List[Path] = [context.root, temp_media_root]
    roots.extend(Path(root).expanduser() for root in scan_roots if root)
    seen_roots: Set[Path] = set()
    for raw_root in roots:
        try:
            root = raw_root.resolve()
        except OSError:
            continue
        if root in seen_roots or not root.exists():
            continue
        seen_roots.add(root)
        if root.is_file():
            candidates.add(root)
            continue
        for current, directories, filenames in os.walk(root, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if not _skip_discovery_directory(current, name)
            ]
            for filename in filenames:
                path = Path(current) / filename
                if path.is_symlink():
                    continue
                candidates.add(path.resolve())

    return sorted(
        (path for path in candidates if path.is_file() and _protected_path_header(path)),
        key=lambda path: str(path),
    )


def _decrypt_rekey_entry(path: Path, fernet: Fernet) -> Tuple[bytes, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SecureStorageError(f"Cannot read protected file {path.name}: {exc}") from exc
    if raw.startswith(FILE_HEADER):
        header = FILE_HEADER
    elif raw.startswith(SQLITE_HEADER):
        header = SQLITE_HEADER
    else:
        raise SecureStorageError(f"Protected file changed during rekey: {path.name}")
    try:
        return header, fernet.decrypt(raw[len(header) :])
    except (InvalidToken, ValueError) as exc:
        raise SecureStorageError(f"Cannot decrypt protected file {path.name} during rekey") from exc


def _restore_rekey_files(
    changed: List[Tuple[Path, Path]],
    staged: Iterable[Path],
) -> None:
    for path, backup in reversed(changed):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            if backup.exists():
                os.replace(backup, path)
        except OSError:
            pass
    for path in staged:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def rotate_secure_storage(
    *,
    password: Optional[str] = None,
    scan_roots: Iterable[str | Path] = (),
) -> Dict[str, Any]:
    """Rotate the Maximus data key and re-encrypt all discovered envelopes.

    The operation is event-driven: callers invoke it after a compromise, an
    administrator-requested rotation, or a fallback-password change.  It keeps
    the active process usable and rolls file replacements back if staging or
    key-store metadata fails.
    """
    global _CONTEXT
    with _STORAGE_LOCK:
        with _CONTEXT_LOCK:
            context = _require_context()
            states = list(_DATABASES.values())
            for state in states:
                state.persist()

            paths = _discover_protected_paths(context, scan_roots)
            entries: List[Tuple[Path, Path]] = []
            new_salt: Optional[bytes] = None
            old_salt: Optional[bytes] = None

            if context.key_source == "password_fallback":
                resolved_password = password or _password_from_environment()
                if not resolved_password:
                    raise SecureStorageError("A password is required to rotate password-based protected storage")
                salt_path = context.root / KEY_SALT_FILE
                try:
                    old_salt = salt_path.read_bytes()
                except OSError as exc:
                    raise SecureStorageError("The protected-storage password salt is missing") from exc
                new_salt = secrets.token_bytes(16)
                new_key = _derive_password_key(resolved_password, new_salt)
            else:
                old_salt = None
                new_raw_key = secrets.token_bytes(32)
                new_key = _fernet_key(new_raw_key)

            new_fernet = Fernet(new_key)
            skipped_undecryptable: List[str] = []
            try:
                for path in paths:
                    try:
                        header, payload = _decrypt_rekey_entry(path, context.fernet)
                    except SecureStorageError as exc:
                        # An envelope we cannot decrypt with the current key is an
                        # orphan - sealed by a different boundary/key or corrupted.
                        # Skipping it (leaving its bytes untouched) keeps rotation
                        # from being held hostage by one unreadable file. It is
                        # reported so an operator can investigate/remove it.
                        LOGGER.warning("rotation skipping undecryptable envelope %s: %s", path.name, exc)
                        skipped_undecryptable.append(str(path))
                        continue
                    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rekey.tmp")
                    _atomic_write(staged, header + new_fernet.encrypt(payload))
                    entries.append((path, staged))
            except Exception:
                _restore_rekey_files([], (entry[1] for entry in entries))
                raise

            changed: List[Tuple[Path, Path]] = []
            keychain_replaced = False
            salt_replaced = False
            old_key = context.key
            old_keyring_raw = base64.urlsafe_b64decode(old_key)
            try:
                for path, staged in entries:
                    backup = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rekey.bak")
                    os.replace(path, backup)
                    try:
                        os.replace(staged, path)
                    except Exception:
                        os.replace(backup, path)
                        raise
                    changed.append((path, backup))

                if context.key_source == "password_fallback":
                    assert new_salt is not None
                    _atomic_write(context.root / KEY_SALT_FILE, new_salt)
                    salt_replaced = True
                else:
                    if not replace_key(context.service_name, KEY_USERNAME, base64.urlsafe_b64decode(new_key)):
                        raise SecureStorageError("The OS credential store rejected the rotated protected-storage key")
                    keychain_replaced = True

                context.key = new_key
                context.fernet = new_fernet
                if context.key_source == "password_fallback" and password:
                    os.environ["AUTOYOU_SECURE_STORAGE_PASSWORD"] = password
            except Exception as exc:
                if salt_replaced and old_salt is not None:
                    _atomic_write(context.root / KEY_SALT_FILE, old_salt)
                if keychain_replaced:
                    replace_key(context.service_name, KEY_USERNAME, old_keyring_raw)
                _restore_rekey_files(changed, (entry[1] for entry in entries))
                if isinstance(exc, SecureStorageError):
                    raise
                raise SecureStorageError(f"Protected-storage key rotation failed: {exc}") from exc

            for _path, backup in changed:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    pass
            return {
                "enabled": True,
                "mode": SECURE_PROFESSIONAL_MAXIMUS_MODE,
                "key_source": context.key_source,
                "keychain_backend": context.keyring_backend,
                "files_rekeyed": len(entries),
                "databases_rekeyed": sum(1 for path in paths if path in _DATABASES),
                "skipped_undecryptable": skipped_undecryptable,
            }


def unseal_secure_storage(*, scan_roots: Iterable[str | Path] = ()) -> Dict[str, Any]:
    """Decrypt every protected envelope back to plaintext, then disable the boundary.

    Used when downgrading from Secure Professional Maximus to a lower mode so
    AutoYou-owned stores stay readable in Normal/Secure. Without this step the
    sealed files remain on disk and every reader fails closed: ``read_secure_file``
    raises and ``sqlite3.connect`` reports "file is not a database".

    The operation stages plaintext next to each envelope, atomically swaps all
    files, and only then tears down the in-memory adapter. Any failure rolls the
    files back and leaves the boundary enabled, so a downgrade never half-applies.
    """
    global _CONTEXT
    with _STORAGE_LOCK:
        with _CONTEXT_LOCK:
            context = _require_context()
            with _DATABASES_LOCK:
                states = list(_DATABASES.values())
            for state in states:
                state.persist()

            paths = _discover_protected_paths(context, scan_roots)
            entries: List[Tuple[Path, Path]] = []  # (target, staged_plaintext)
            database_count = 0
            skipped_undecryptable: List[str] = []
            try:
                for path in paths:
                    try:
                        header, payload = _decrypt_rekey_entry(path, context.fernet)
                    except SecureStorageError as exc:
                        # Orphan envelope (foreign key or corruption): leave it sealed
                        # rather than aborting the whole downgrade. It was already
                        # unreadable; the rest of the stores still return to plaintext.
                        LOGGER.warning("unseal skipping undecryptable envelope %s: %s", path.name, exc)
                        skipped_undecryptable.append(str(path))
                        continue
                    if header == SQLITE_HEADER:
                        # The decrypted snapshot is a complete SQLite image; writing
                        # it as the .db path yields an ordinary plaintext database.
                        payload = _normalise_sqlite_snapshot(payload)
                        database_count += 1
                    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.unseal.tmp")
                    _atomic_write(staged, payload)
                    entries.append((path, staged))
            except Exception as exc:
                _restore_rekey_files([], (staged for _target, staged in entries))
                if isinstance(exc, SecureStorageError):
                    raise
                raise SecureStorageError(f"Protected-storage unseal staging failed: {exc}") from exc

            changed: List[Tuple[Path, Path]] = []
            try:
                for path, staged in entries:
                    backup = path.with_name(f".{path.name}.{uuid.uuid4().hex}.unseal.bak")
                    os.replace(path, backup)
                    try:
                        os.replace(staged, path)
                    except Exception:
                        os.replace(backup, path)
                        raise
                    changed.append((path, backup))
                    _remove_sqlite_sidecars(path)
            except Exception as exc:
                _restore_rekey_files(changed, (staged for _target, staged in entries))
                if isinstance(exc, SecureStorageError):
                    raise
                raise SecureStorageError(f"Protected-storage unseal failed: {exc}") from exc

            for _path, backup in changed:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    pass

            # Plaintext is now on disk; tear the in-memory adapter down so future
            # opens read those plaintext files directly.
            with _DATABASES_LOCK:
                for state in list(_DATABASES.values()):
                    try:
                        state.anchor.close()
                    except Exception:
                        pass
                _DATABASES.clear()
            _PROTECTED_FILES.clear()
            _CONTEXT = None
            if sqlite3.connect is not _ORIGINAL_SQLITE_CONNECT:
                sqlite3.connect = _ORIGINAL_SQLITE_CONNECT
            if sqlite3.dbapi2.connect is not _ORIGINAL_SQLITE_DBAPI_CONNECT:
                sqlite3.dbapi2.connect = _ORIGINAL_SQLITE_DBAPI_CONNECT
            for variable in (
                "AUTOYOU_SECURE_STORAGE_MODE",
                "AUTOYOU_SECURE_STORAGE_ROOT",
                "AUTOYOU_SECURE_STORAGE_APP",
                "AUTOYOU_SECURE_STORAGE_PASSWORD",
            ):
                os.environ.pop(variable, None)
            return {
                "enabled": False,
                "mode": None,
                "files_unsealed": len(entries),
                "databases_unsealed": database_count,
                "skipped_undecryptable": skipped_undecryptable,
            }


def _existing_key_material(
    *,
    app_name: str,
    root: Path,
    password: Optional[str],
) -> Optional[bytes]:
    """Load the SPM key only if it already exists; never mint a new one.

    ``_load_or_create_key`` creates a fresh key when no marker is present. During
    recovery that is the worst possible outcome: a new key cannot decrypt the old
    envelopes, and writing new marker files makes the loss look intentional.
    """
    marker = _read_marker(root / KEY_MODE_FILE)
    if not marker:
        return None
    identifier = _safe_identifier(app_name)
    root_digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:20]
    service_name = f"autoyou-{identifier}-spm-{root_digest}"
    if marker == "keychain":
        key = get_or_create_key(service_name, KEY_USERNAME, create=False)
        return _fernet_key(key) if key else None
    if marker == "password":
        try:
            salt = (root / KEY_SALT_FILE).read_bytes()
        except OSError:
            return None
        resolved_password = password or _password_from_environment()
        if len(salt) < 16 or not resolved_password:
            return None
        return _derive_password_key(resolved_password, salt)
    return None


def recover_stranded_envelopes(
    *,
    app_name: str,
    root: str | Path,
    scan_roots: Iterable[str | Path] = (),
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """Unseal envelopes left behind by a downgrade that skipped the unseal step.

    A mode change applied in a *different* process - or a config/keystore reset -
    leaves Maximus envelopes on disk while the boundary is off, and every reader
    then fails closed. Call this at startup in a non-Maximus mode.

    Never mints a key: when the original key is gone the envelopes are reported
    as ``unrecoverable`` and left untouched, because a fresh key would guarantee
    permanent loss rather than merely fail to read.
    """
    resolved_root = Path(root).expanduser().resolve()
    raw_roots = [resolved_root, *scan_roots]
    roots: List[Path] = []
    seen_roots: Set[Path] = set()
    for raw_root in raw_roots:
        try:
            candidate = Path(raw_root).expanduser().resolve()
        except OSError:
            continue
        if candidate in seen_roots:
            continue
        seen_roots.add(candidate)
        roots.append(candidate)
    stranded = find_sealed_envelopes(roots)
    if not stranded:
        return {"stranded": 0, "recovered": 0, "unrecoverable": [], "restart_required": False}

    if secure_storage_enabled():
        # The boundary is live; this is not a stranded-envelope situation.
        return {
            "stranded": len(stranded),
            "recovered": 0,
            "unrecoverable": [],
            "restart_required": False,
        }

    key = _existing_key_material(app_name=app_name, root=resolved_root, password=password)
    if key is None:
        LOGGER.error(
            "%d Secure Professional Maximus envelope(s) are stranded on disk and the storage key "
            "is gone; these stores cannot be read: %s",
            len(stranded),
            ", ".join(path.name for path in stranded),
        )
        return {
            "stranded": len(stranded),
            "recovered": 0,
            "unrecoverable": [str(path) for path in stranded],
            "restart_required": False,
        }

    global _CONTEXT
    with _STORAGE_LOCK:
        with _CONTEXT_LOCK:
            _CONTEXT = _SecureStorageContext(
                app_name=app_name,
                root=resolved_root,
                service_name="",
                key_source="recovery",
                key=key,
                fernet=Fernet(key),
                keyring_backend="none",
            )
    try:
        result = unseal_secure_storage(scan_roots=roots)
    except Exception:
        # Drop the recovery context directly rather than through
        # disable_secure_storage(), which persists open databases first: this
        # key exists only to read old envelopes, and letting it write would seal
        # live stores with a key the rest of the system no longer knows about.
        with _STORAGE_LOCK:
            with _CONTEXT_LOCK:
                _CONTEXT = None
        raise

    skipped = list(result.get("skipped_undecryptable") or [])
    recovered = int(result.get("files_unsealed") or 0) - len(skipped)
    LOGGER.warning(
        "Recovered %d stranded Maximus envelope(s) back to plaintext after a downgrade that "
        "skipped the unseal step; %d could not be decrypted.",
        max(recovered, 0),
        len(skipped),
    )
    return {
        "stranded": len(stranded),
        "recovered": max(recovered, 0),
        "unrecoverable": skipped,
        "restart_required": recovered > 0,
    }


def seal_secure_paths(paths: Iterable[str | Path]) -> Dict[str, Any]:
    """Eagerly seal already-existing plaintext owned stores under Maximus.

    The adapter otherwise migrates plaintext to ciphertext lazily on first
    access, which leaves owned stores in cleartext under Maximus until something
    happens to open them. For sensitive, small, server-enumerable stores (2FA
    profiles, registries, control files) that window is unacceptable, so the
    server calls this on enable to force migration immediately.

    Idempotent: missing paths and already-sealed envelopes are skipped. A
    SQLite file is migrated by opening it through the patched connector (which
    snapshots+seals it); any other file is migrated through read_secure_file.
    Large agent databases are intentionally NOT passed here - they stay on the
    migrate-on-access path so enable/boot never has to load them wholesale.
    """
    if not secure_storage_enabled():
        raise SecureStorageError("Secure Professional Maximus storage is not enabled")
    sealed = 0
    skipped = 0
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            skipped += 1
            continue
        header = _protected_path_header(path)
        if header is not None:
            skipped += 1  # already an SPM envelope
            continue
        with path.open("rb") as handle:
            prefix = handle.read(len(_SQLITE_DATABASE_MAGIC))
        if prefix == _SQLITE_DATABASE_MAGIC:
            # Opening through the patched connector migrates+seals on close.
            connection = sqlite3.connect(str(path))
            try:
                if isinstance(connection, _SecureSQLiteConnection):
                    connection._state.persist()
            finally:
                connection.close()
        else:
            read_secure_file(path)  # migrates a plaintext file to a sealed envelope
        sealed += 1
    return {"sealed": sealed, "skipped": skipped}


def _encrypted_file_payload(payload: bytes) -> bytes:
    return FILE_HEADER + _require_context().fernet.encrypt(payload)


def _decrypt_payload(raw: bytes, header: bytes, path: Path) -> bytes:
    if not raw.startswith(header):
        return raw
    token = raw[len(header) :]
    try:
        return _require_context().fernet.decrypt(token)
    except (InvalidToken, ValueError) as exc:
        raise SecureStorageError(f"Cannot decrypt protected file {path.name}") from exc


def read_secure_file(path: str | Path, *, migrate_plaintext: bool = True) -> bytes:
    """Read a protected file, optionally leaving an external plaintext file unchanged."""
    with _STORAGE_LOCK:
        resolved = Path(path)
        raw = resolved.read_bytes()
        if not secure_storage_enabled():
            if raw.startswith(FILE_HEADER):
                raise SecureStorageError(
                    f"Protected file requires Secure Professional Maximus storage: {resolved.name}"
                )
            if raw.startswith(SQLITE_HEADER):
                raise SecureStorageError(
                    f"Protected SQLite data requires Secure Professional Maximus storage: {resolved.name}"
                )
            return raw
        if raw.startswith(FILE_HEADER):
            payload = _decrypt_payload(raw, FILE_HEADER, resolved)
        elif raw.startswith(SQLITE_HEADER):
            raise SecureStorageError(f"Protected SQLite data was read as a file: {resolved.name}")
        else:
            payload = raw
            if not migrate_plaintext:
                return payload
            write_secure_file(resolved, payload)
        with _DATABASES_LOCK:
            _PROTECTED_FILES.add(resolved.resolve())
        return payload


def write_secure_file(path: str | Path, payload: bytes) -> None:
    with _STORAGE_LOCK:
        resolved = Path(path)
        if secure_storage_enabled():
            _atomic_write(resolved, _encrypted_file_payload(payload))
            with _DATABASES_LOCK:
                _PROTECTED_FILES.add(resolved.resolve())
        else:
            if resolved.exists() and _protected_path_header(resolved) is not None:
                # Never turn a sealed record into plaintext merely because a
                # caller reached it before the original boundary was restored.
                # This keeps login/recovery paths fail-closed and prevents an
                # unavailable credential from being mistaken for a reset.
                raise SecureStorageError(
                    f"Protected file requires Secure Professional Maximus storage: {resolved.name}"
                )
            _atomic_write(resolved, payload)


def append_secure_file(path: str | Path, payload: bytes) -> None:
    """Append bytes while preserving one encrypted envelope on disk."""
    with _STORAGE_LOCK:
        resolved = Path(path)
        existing = read_secure_file(resolved) if resolved.exists() else b""
        write_secure_file(resolved, existing + bytes(payload))


@contextmanager
def materialize_secure_file(path: str | Path):
    """Temporarily expose a protected file to libraries that require a path.

    The temporary plaintext is removed when the context exits.  Plaintext
    external/user-selected files are yielded unchanged because they are not
    owned by AutoYou's protected storage boundary.
    """
    resolved = Path(path)
    temporary_directory: Optional[tempfile.TemporaryDirectory[str]] = None
    temporary: Optional[Path] = None
    try:
        with _STORAGE_LOCK:
            if secure_storage_enabled() and resolved.is_file():
                raw = resolved.read_bytes()
                if raw.startswith(FILE_HEADER):
                    temporary_directory = tempfile.TemporaryDirectory(prefix="autoyou-spm-")
                    temporary = Path(temporary_directory.name) / resolved.name
                    _atomic_write(temporary, _decrypt_payload(raw, FILE_HEADER, resolved))
        yield temporary or resolved
    finally:
        if temporary_directory is not None:
            # TemporaryDirectory removes the file; explicitly overwriting is
            # unnecessary and would create another plaintext copy.
            temporary_directory.cleanup()


def load_secure_json(path: str | Path, *, default: Any = None) -> Any:
    resolved = Path(path)
    if not resolved.exists():
        return default
    payload = read_secure_file(resolved)
    try:
        # Windows PowerShell 5.1 writes ``-Encoding UTF8`` with a UTF-8 BOM.
        # Older ecosystem installers used that encoding for the agent registry,
        # so accept the marker while continuing to write new JSON without it.
        return json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecureStorageError(f"Protected JSON file is invalid: {resolved.name}") from exc


def save_secure_json(path: str | Path, payload: Any) -> None:
    plaintext = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    write_secure_file(path, plaintext)


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            path.with_name(path.name + suffix).unlink()
        except FileNotFoundError:
            pass


def _database_path(database: Any, *, uri: bool) -> Optional[Path]:
    if isinstance(database, os.PathLike):
        return Path(database).expanduser().resolve()
    if not isinstance(database, str):
        return None
    if database in {":memory:", ""}:
        return None
    if database.startswith("file:"):
        parsed = urllib.parse.urlsplit(database)
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("mode", [""])[0].lower() == "memory":
            return None
        raw_path = urllib.parse.unquote(parsed.path)
        if not raw_path:
            return None
        return Path(raw_path).expanduser().resolve()
    return Path(database).expanduser().resolve()


def _initialise_database(path: Path, kwargs: Dict[str, Any]) -> _SecureDatabaseState:
    context = _require_context()
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:24]
    uri = f"file:autoyou_spm_{os.getpid()}_{digest}?mode=memory&cache=shared"
    anchor_kwargs = {
        "uri": True,
        "check_same_thread": False,
        "isolation_level": kwargs.get("isolation_level", "DEFERRED"),
    }
    anchor = _ORIGINAL_SQLITE_CONNECT(uri, **anchor_kwargs)
    # A brand-new in-memory connection has zero allocated pages until its
    # first write. sqlite3's Connection.serialize() (used by persist() below)
    # raises "unable to serialize 'main'" for a page-less database - this is
    # a real, reproducible sqlite3/SQLite limitation (confirmed on plain
    # ":memory:" and even on a freshly created, never-written file), not
    # something specific to this platform. Writing the harmless, already-
    # default user_version pragma forces the header page to exist so every
    # later serialize() call - including the very first persist() a few
    # lines down for brand-new databases - has something valid to snapshot.
    anchor.execute("PRAGMA user_version = 0")
    anchor.commit()
    state = _SecureDatabaseState(path=path, uri=uri, anchor=anchor)

    if path.exists():
        raw = path.read_bytes()
        try:
            if raw.startswith(SQLITE_HEADER):
                payload = _normalise_sqlite_snapshot(
                    context.fernet.decrypt(raw[len(SQLITE_HEADER) :])
                )
                # ``Connection.deserialize`` replaces the connection's
                # backing store and breaks SQLite's shared-cache URI for
                # subsequent client connections.  Load into a temporary
                # connection and back it up into the shared anchor instead.
                source = _ORIGINAL_SQLITE_CONNECT(":memory:")
                try:
                    source.deserialize(payload)
                    source.backup(anchor)
                finally:
                    source.close()
            elif raw:
                disk = _ORIGINAL_SQLITE_CONNECT(str(path), check_same_thread=False)
                try:
                    try:
                        disk.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    except sqlite3.DatabaseError:
                        pass
                    disk.backup(anchor)
                finally:
                    disk.close()
                try:
                    state.persist()
                except PermissionError:
                    # Windows can keep the plaintext DB locked until the
                    # caller's previous sqlite3 connection is released.
                    # The returned protected connection persists on close.
                    pass
        except (InvalidToken, ValueError, sqlite3.DatabaseError, OSError) as exc:
            try:
                anchor.close()
            except Exception:
                pass
            raise SecureStorageError(f"Cannot open protected SQLite database {path.name}") from exc
    else:
        state.persist()
    with _DATABASES_LOCK:
        _DATABASES[path] = state
    return state


class _SecureSQLiteConnection:
    """Thin connection proxy preserving the sqlite3 connection interface."""

    def __init__(self, connection: Any, state: _SecureDatabaseState) -> None:
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_state", state)
        object.__setattr__(self, "_closed", False)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_connection"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_connection", "_state", "_closed"}:
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_connection"), name, value)

    def __enter__(self) -> "_SecureSQLiteConnection":
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> Any:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()
        return False

    def commit(self) -> None:
        with self._state.lock:
            self._connection.commit()
            self._state.persist()

    def rollback(self) -> None:
        with self._state.lock:
            self._connection.rollback()

    def close(self) -> None:
        if self._closed:
            return
        with self._state.lock:
            try:
                if not self._connection.in_transaction:
                    self._state.persist()
            finally:
                self._connection.close()
                object.__setattr__(self, "_closed", True)


def _connect_argument_kwargs(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    names = (
        "timeout",
        "detect_types",
        "isolation_level",
        "check_same_thread",
        "factory",
        "cached_statements",
        "uri",
    )
    if len(args) > len(names):
        raise TypeError("too many positional arguments for sqlite3.connect")
    result = dict(kwargs)
    for name, value in zip(names, args):
        if name in result:
            raise TypeError(f"sqlite3.connect() got multiple values for argument {name!r}")
        result[name] = value
    return result


def _secure_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
    options = _connect_argument_kwargs(args, kwargs)
    path = _database_path(database, uri=bool(options.get("uri", False)))
    if path is None:
        return _ORIGINAL_SQLITE_CONNECT(database, **options)
    with _DATABASES_LOCK:
        state = _DATABASES.get(path)
    if state is None:
        # Keep lock acquisition in the same order as rotation/persistence:
        # storage lock first, then database registry lock.  This avoids a
        # connect-versus-rekey deadlock while a new protected database is
        # being migrated into the in-memory adapter.
        with _STORAGE_LOCK:
            with _DATABASES_LOCK:
                state = _DATABASES.get(path)
                if state is None:
                    state = _initialise_database(path, options)
    connection_options = dict(options)
    connection_options["uri"] = True
    connection = _ORIGINAL_SQLITE_CONNECT(state.uri, **connection_options)
    return _SecureSQLiteConnection(connection, state)


def _patched_sqlite_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
    if not secure_storage_enabled():
        return _ORIGINAL_SQLITE_CONNECT(database, *args, **kwargs)
    return _secure_connect(database, *args, **kwargs)


def _install_sqlite_adapter() -> None:
    if sqlite3.connect is not _patched_sqlite_connect:
        sqlite3.connect = _patched_sqlite_connect
    if sqlite3.dbapi2.connect is not _patched_sqlite_connect:
        sqlite3.dbapi2.connect = _patched_sqlite_connect


__all__ = [
    "FILE_HEADER",
    "SECURE_PROFESSIONAL_MAXIMUS_MODE",
    "SQLITE_HEADER",
    "SecureStorageError",
    "append_secure_file",
    "disable_secure_storage",
    "enable_secure_storage",
    "enable_secure_storage_from_environment",
    "ensure_secure_storage_for_path",
    "find_sealed_envelopes",
    "is_secure_professional_mode",
    "load_secure_json",
    "materialize_secure_file",
    "read_secure_file",
    "recover_stranded_envelopes",
    "rotate_secure_storage",
    "save_secure_json",
    "seal_secure_paths",
    "sealed_envelope_kind",
    "secure_storage_enabled",
    "secure_storage_status",
    "unseal_secure_storage",
    "write_secure_file",
]
