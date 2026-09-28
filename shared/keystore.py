# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""OS keystore integration for AutoYou config encryption.

Uses the platform credential store (macOS Keychain, Windows Credential Manager,
Linux SecretService via libsecret / gnome-keyring) to persist a random 32-byte
Fernet key.  Config files on disk are encrypted with that key, so:

  • Config is encrypted at rest - no plaintext secrets in the filesystem.
  • Zero password friction - the OS handles the key transparently.
  • Survives service restarts - the key persists across reboots.
  • Migrating to a new machine requires re-setup (expected and correct).

Graceful degradation: if ``keyring`` is not installed or the OS backend is
unavailable (headless CI, Docker without a mounted secret service), callers
fall back gracefully - this library never raises on import.

Usage (autoyou-lite):
    pip install "autoyou-lite[keystore]"

Usage (main server):
    pip install keyring          # or install via OS package manager
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import queue
import secrets
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("autoyou.keystore")
_KEYRING_OPERATION_TIMEOUT_SECONDS = 8.0
_KEYRING_TIMEOUT = object()
_MACOS_KEYCHAIN_BOOTSTRAP_TIMEOUT_ENV = "AUTOYOU_MACOS_KEYCHAIN_BOOTSTRAP_TIMEOUT_SECONDS"
_DEFAULT_MACOS_KEYCHAIN_BOOTSTRAP_TIMEOUT_SECONDS = 1.0

# ── Default service / credential identifiers ─────────────────────────────────
# Each application uses its own service name so the stored keys don't collide.
_LIB_SERVICE_NAME = "autoyou-lite"
_SERVER_SERVICE_NAME = "autoyou-server"
_DEFAULT_CRED_NAME = "config-encryption-key"


def server_keystore_service_name(config_path: Path, service: str = _SERVER_SERVICE_NAME) -> str:
    """Give isolated desktop and test stores their own OS credentials."""
    test_root = str(os.getenv("AUTOYOU_TEST_ROOT") or "").strip()
    runtime_root = str(os.getenv("AUTOYOU_RUNTIME_ROOT") or "").strip()
    if not test_root and not runtime_root:
        return service
    scope = str(Path(config_path).expanduser().resolve(strict=False))
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
    return f"{service}-{'test' if test_root else 'runtime'}-{digest}"

# ── Optional dependency: keyring ──────────────────────────────────────────────
try:
    import keyring as _keyring_mod
    from keyring.errors import KeyringError as _KeyringError
    _HAS_KEYRING = True
except ImportError:
    _keyring_mod = None  # type: ignore[assignment]
    _KeyringError = Exception  # type: ignore[assignment, misc]
    _HAS_KEYRING = False

# ── Optional dependency: cryptography (Fernet) ────────────────────────────────
try:
    from cryptography.fernet import Fernet as _Fernet
    from cryptography.fernet import InvalidToken as _InvalidToken
    _HAS_FERNET = True
except ImportError:
    _Fernet = None  # type: ignore[assignment, misc]
    _InvalidToken = Exception  # type: ignore[assignment, misc]
    _HAS_FERNET = False


# ── Backend introspection ─────────────────────────────────────────────────────

def _backend_name() -> str:
    """Return the active keyring backend type name, or ``'none'``."""
    if not _HAS_KEYRING or _keyring_mod is None:
        return "none"
    try:
        backend_type = type(_keyring_mod.get_keyring())
        return f"{backend_type.__module__}.{backend_type.__qualname__}"
    except Exception:
        return "unknown"


def _keyring_operation_timeout_seconds(timeout_seconds: Optional[float] = None) -> float:
    """Resolve an optional per-operation timeout without changing global policy."""
    if timeout_seconds is not None:
        try:
            return max(float(timeout_seconds), 0.0)
        except (TypeError, ValueError):
            return _KEYRING_OPERATION_TIMEOUT_SECONDS
    raw_value = os.getenv("AUTOYOU_KEYRING_OPERATION_TIMEOUT_SECONDS", "").strip()
    if not raw_value:
        return _KEYRING_OPERATION_TIMEOUT_SECONDS
    try:
        return max(float(raw_value), 0.0)
    except ValueError:
        return _KEYRING_OPERATION_TIMEOUT_SECONDS


def macos_keychain_bootstrap_timeout_seconds() -> Optional[float]:
    """Return the automatic-startup Keychain policy for macOS reads.

    A positive value keeps startup non-interactive: the Keychain query uses
    ``kSecUseAuthenticationUISkip`` and either returns an already-authorized
    credential or immediately reports it unavailable. ``0`` is an operator
    opt-in to a foreground, interactive read. Non-macOS platforms retain their
    ordinary keyring behavior and therefore return ``None``.
    """
    if sys.platform != "darwin":
        return None
    raw_value = os.getenv(
        _MACOS_KEYCHAIN_BOOTSTRAP_TIMEOUT_ENV,
        str(_DEFAULT_MACOS_KEYCHAIN_BOOTSTRAP_TIMEOUT_SECONDS),
    ).strip()
    try:
        return max(float(raw_value), 0.0)
    except ValueError:
        return _DEFAULT_MACOS_KEYCHAIN_BOOTSTRAP_TIMEOUT_SECONDS


def _uses_synchronous_macos_keychain(timeout_seconds: Optional[float] = None) -> bool:
    """Whether a real macOS Keychain operation must stay on the caller thread.

    Security.framework authorization sheets cannot be cancelled from Python.
    Timing one out only detaches the call while its Keychain prompt remains
    active, so a restarting desktop host can create an unbounded series of
    prompts. A deliberate timeout override remains available for headless or
    automated macOS environments.
    """
    if sys.platform != "darwin":
        return False
    if timeout_seconds is not None or os.getenv("AUTOYOU_KEYRING_OPERATION_TIMEOUT_SECONDS", "").strip():
        return False
    return _backend_name().lower().startswith("keyring.backends.macos.")


def call_keyring_operation(
    operation: Callable[..., Any],
    *args: Any,
    operation_name: str = "operation",
    default: Any = _KEYRING_TIMEOUT,
    timeout_seconds: Optional[float] = None,
    **kwargs: Any,
) -> Any:
    """Run a keyring call without leaving uncancellable Keychain work behind."""
    if _uses_synchronous_macos_keychain(timeout_seconds):
        # On macOS, one foreground authorization request is preferable to a
        # timed-out daemon thread whose Keychain sheet continues after startup.
        return operation(*args, **kwargs)

    resolved_timeout_seconds = _keyring_operation_timeout_seconds(timeout_seconds)
    if resolved_timeout_seconds <= 0:
        return operation(*args, **kwargs)

    result_queue: "queue.Queue[tuple[bool, Any]]" = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            result_queue.put((True, operation(*args, **kwargs)))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread.
            result_queue.put((False, exc))

    worker = threading.Thread(
        target=_worker,
        name=f"autoyou-keyring-{operation_name}",
        daemon=True,
    )
    worker.start()
    worker.join(resolved_timeout_seconds)

    if worker.is_alive():
        logger.warning(
            "Keystore %s timed out after %.1fs; falling back without OS keystore.",
            operation_name,
            resolved_timeout_seconds,
        )
        if default is _KEYRING_TIMEOUT:
            return None
        return default

    ok, payload = result_queue.get_nowait()
    if ok:
        return payload
    raise payload


def _should_skip_macos_keychain_ui(operation_timeout_seconds: Optional[float]) -> bool:
    """Whether this is an automatic macOS read that must never show a sheet."""
    if sys.platform != "darwin" or operation_timeout_seconds is None:
        return False
    if _keyring_operation_timeout_seconds(operation_timeout_seconds) <= 0:
        return False
    return _backend_name().lower().startswith("keyring.backends.macos.")


def _macos_keychain_api() -> Any:
    """Return Keyring's Security.framework bridge without importing it globally."""
    try:
        from keyring.backends.macOS import api as macos_api
    except Exception:
        return None
    return macos_api


def _read_macos_keyring_password_without_ui(
    service_name: str,
    username: str,
) -> Optional[str]:
    """Read an existing generic-password item without triggering Keychain UI.

    The normal Keyring backend allows Security.framework to create an
    authorization sheet. That is correct for an explicit user action, but it
    is fundamentally unsafe during automatic startup: a child process can be
    terminated while the operator is completing the sheet. ``UISkip`` makes
    this an all-or-nothing probe instead, so the recovery UI can ask for a
    deliberate foreground unlock when consent is still needed.
    """
    macos_api = _macos_keychain_api()
    if macos_api is None:
        return None
    try:
        query = macos_api.create_query(
            kSecClass=macos_api.k_("kSecClassGenericPassword"),
            kSecMatchLimit=macos_api.k_("kSecMatchLimitOne"),
            kSecAttrService=service_name,
            kSecAttrAccount=username,
            kSecReturnData=True,
            kSecUseAuthenticationUI=macos_api.k_("kSecUseAuthenticationUISkip"),
        )
        data = macos_api.c_void_p()
        status = macos_api.SecItemCopyMatching(query, macos_api.byref(data))
        if status != 0:
            return None
        value = macos_api.cfstr_to_str(data)
    except Exception:
        return None
    return value if isinstance(value, str) else None


def get_keyring_password(
    service_name: str,
    username: str,
    *,
    operation_timeout_seconds: Optional[float] = None,
) -> Optional[str]:
    """Read a credential without creating or replacing it.

    Automatic macOS startup reads use Security.framework's no-UI mode. They
    either return an already-authorized credential or leave the server locked
    for an explicit foreground unlock; they never create a Keychain sheet in a
    process that may be stopped or restarted. All ordinary reads retain the
    platform's normal foreground behavior.
    """
    if not keyring_available() or _keyring_mod is None:
        return None
    try:
        if _should_skip_macos_keychain_ui(operation_timeout_seconds):
            return _read_macos_keyring_password_without_ui(service_name, username)
        value = call_keyring_operation(
            _keyring_mod.get_password,
            service_name,
            username,
            operation_name="get_password",
            timeout_seconds=operation_timeout_seconds,
        )
        return value if isinstance(value, str) else None
    except _KeyringError as exc:
        logger.warning("Keystore read failed for service=%r: %s", service_name, exc)
        return None
    except Exception as exc:
        logger.warning("Unexpected keystore read error for service=%r: %s", service_name, exc)
        return None


class KeyringAccessError(RuntimeError):
    """The operating-system credential store is unavailable or rejected an operation."""


def read_keyring_password_strict(service_name: str, username: str) -> Optional[str]:
    """Read a credential for an explicit foreground user action.

    Unlike the startup-oriented helper above, this surfaces access failures so a
    caller cannot mistake a locked credential store for an empty vault.
    """
    if not keyring_available() or _keyring_mod is None:
        raise KeyringAccessError("The OS credential store is unavailable.")
    try:
        value = call_keyring_operation(
            _keyring_mod.get_password, service_name, username,
            operation_name="read_password", timeout_seconds=0,
        )
    except Exception as exc:
        raise KeyringAccessError("The OS credential store could not be read.") from exc
    if value is not None and not isinstance(value, str):
        raise KeyringAccessError("The OS credential store returned an invalid credential.")
    return value


def write_keyring_password(service_name: str, username: str, password: str) -> None:
    """Write a credential without falling back to an unprotected copy."""
    if not keyring_available() or _keyring_mod is None:
        raise KeyringAccessError("The OS credential store is unavailable.")
    try:
        result = call_keyring_operation(
            _keyring_mod.set_password, service_name, username, password,
            operation_name="write_password", default=False, timeout_seconds=0,
        )
    except Exception as exc:
        raise KeyringAccessError("The OS credential store could not save this authenticator.") from exc
    if result is False:
        raise KeyringAccessError("The OS credential store could not save this authenticator.")


def delete_keyring_password(service_name: str, username: str) -> bool:
    """Delete a credential, distinguishing an absent entry from store failure."""
    if read_keyring_password_strict(service_name, username) is None:
        return False
    try:
        result = call_keyring_operation(
            _keyring_mod.delete_password, service_name, username,
            operation_name="delete_password", default=False, timeout_seconds=0,
        )
    except Exception as exc:
        raise KeyringAccessError("The OS credential store could not remove this authenticator.") from exc
    if result is False:
        raise KeyringAccessError("The OS credential store could not remove this authenticator.")
    return True


# Backends that offer no real security - treat as unavailable.
_DEGENERATE_BACKENDS: frozenset[str] = frozenset({
    "backends.fail",
    "backends.null",
    "failkeyring",
    "nullkeyring",
    "plaintextkeyring",
    "fakekeyring",
    "nokeyring",
    "unknown",
    "none",
})


def keyring_available() -> bool:
    """Return ``True`` if keyring is importable and backed by a real credential store.

    Returns ``False`` for stub/degenerate backends like ``FailKeyring``,
    ``NullKeyring``, or ``PlaintextKeyring``.
    """
    if not _HAS_KEYRING or not _HAS_FERNET:
        return False
    try:
        name = _backend_name().lower()
        return not any(bad in name for bad in _DEGENERATE_BACKENDS)
    except Exception:
        return False


def get_keystore_status(
    service_name: str = _LIB_SERVICE_NAME,
    username: str = _DEFAULT_CRED_NAME,
    *,
    include_has_key: bool = True,
) -> Dict[str, Any]:
    """Return a dict describing the current keystore state.

    Suitable for serialising to JSON and returning from an admin API endpoint.
    Set ``include_has_key=False`` for startup diagnostics that only need the
    backend name: reading a macOS Keychain item can itself require an approval.
    """
    available = keyring_available()
    has_key: Optional[bool] = None if not include_has_key else False
    if include_has_key and available and _keyring_mod is not None:
        try:
            has_key = bool(
                call_keyring_operation(
                    _keyring_mod.get_password,
                    service_name,
                    username,
                    operation_name="get_password",
                )
            )
        except Exception:
            has_key = False
    return {
        "keyring_installed": _HAS_KEYRING,
        "available": available,
        "backend": _backend_name(),
        "has_key": has_key,
        "credential_lookup_deferred": not include_has_key,
    }


# ── Key management ────────────────────────────────────────────────────────────

def get_or_create_key(
    service_name: str = _LIB_SERVICE_NAME,
    username: str = _DEFAULT_CRED_NAME,
    *,
    create: bool = True,
    operation_timeout_seconds: Optional[float] = None,
) -> Optional[bytes]:
    """Retrieve the 32-byte encryption key from the OS keystore.

    Creates and stores a fresh key on first call when *create* is true.
    Returns ``None`` when the keystore backend is unavailable, a key is missing
    during a read-only lookup, or any OS-level error occurs so the caller can
    degrade gracefully.
    """
    if not keyring_available() or _keyring_mod is None:
        return None
    try:
        stored = get_keyring_password(
            service_name,
            username,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        if stored:
            raw = base64.urlsafe_b64decode(stored.encode("ascii"))
            if len(raw) == 32:
                return raw
            # Wrong-length key - likely corruption; regenerate.
            logger.warning(
                "Keystore key for service=%r has unexpected length %d; regenerating.",
                service_name, len(raw),
            )
        if not create:
            return None
        # First run (or corrupt entry) - generate, persist, and return.
        key = secrets.token_bytes(32)
        encoded = base64.urlsafe_b64encode(key).decode("ascii")
        persisted = call_keyring_operation(
            _keyring_mod.set_password,
            service_name,
            username,
            encoded,
            operation_name="set_password",
            default=False,
            timeout_seconds=operation_timeout_seconds,
        )
        if persisted is False:
            return None
        logger.info(
            "Generated new config encryption key in OS keystore "
            "(service=%r, backend=%s).",
            service_name, _backend_name(),
        )
        return key
    except _KeyringError as exc:
        logger.warning("Keystore read/write failed for service=%r: %s", service_name, exc)
        return None
    except Exception as exc:
        logger.warning(
            "Unexpected keystore error for service=%r: %s", service_name, exc
        )
        return None


def replace_key(
    service_name: str = _LIB_SERVICE_NAME,
    username: str = _DEFAULT_CRED_NAME,
    raw_key: Optional[bytes] = None,
) -> bool:
    """Atomically replace a credential-store key with a supplied 32-byte key."""
    if not keyring_available() or _keyring_mod is None:
        return False
    key = bytes(raw_key or b"")
    if len(key) != 32:
        raise ValueError("Keystore keys must be exactly 32 bytes")
    try:
        encoded = base64.urlsafe_b64encode(key).decode("ascii")
        persisted = call_keyring_operation(
            _keyring_mod.set_password,
            service_name,
            username,
            encoded,
            operation_name="set_password",
            default=False,
        )
        return persisted is not False
    except _KeyringError:
        return False
    except Exception as exc:
        logger.warning("Failed to replace keystore key for service=%r: %s", service_name, exc)
        return False


def delete_key(
    service_name: str = _LIB_SERVICE_NAME,
    username: str = _DEFAULT_CRED_NAME,
) -> bool:
    """Remove the stored encryption key from the OS keystore.

    Returns ``True`` on success, ``False`` if the key was absent or an error
    occurred.  Useful for "factory reset" / re-enrolment flows.
    """
    if not _HAS_KEYRING or _keyring_mod is None:
        return False
    try:
        deleted = call_keyring_operation(
            _keyring_mod.delete_password,
            service_name,
            username,
            operation_name="delete_password",
            default=False,
        )
        if deleted is False:
            return False
        logger.info(
            "Deleted config encryption key from OS keystore (service=%r).", service_name
        )
        return True
    except _KeyringError:
        return False
    except Exception as exc:
        logger.warning(
            "Failed to delete keystore key for service=%r: %s", service_name, exc
        )
        return False


# ── Backward-compat helpers (autoyou_lite.keystore API) ────────────────────────
# These wrappers preserve the original autoyou_lite.keystore API for code that
# imports from shared.keystore or re-exports through autoyou_lite.keystore.

_SERVICE_NAME = _LIB_SERVICE_NAME
_CRED_NAME = _DEFAULT_CRED_NAME


def keyring_available_lib() -> bool:  # noqa: D401
    """Alias for :func:`keyring_available`."""
    return keyring_available()


def get_keystore_status_lib(*, include_has_key: bool = True) -> Dict[str, Any]:
    """get_keystore_status() with autoyou-lite defaults."""
    return get_keystore_status(
        _LIB_SERVICE_NAME,
        _DEFAULT_CRED_NAME,
        include_has_key=include_has_key,
    )


def delete_keystore_key(
    service: str = _LIB_SERVICE_NAME,
    username: str = _DEFAULT_CRED_NAME,
) -> bool:
    """Alias for :func:`delete_key` with autoyou-lite defaults."""
    return delete_key(service, username)


# ── KeystoreJsonStore ─────────────────────────────────────────────────────────

class KeystoreJsonStore:
    """JSON config store encrypted with a key held in the OS credential manager.

    The encryption key is generated once (on first ``save()``) and stored in the
    OS credential store (macOS Keychain, Windows Credential Manager, Linux
    SecretService).  Subsequent starts retrieve the key transparently - no
    password prompt, no plaintext secrets on disk.

    File format: raw Fernet token (binary) at *path*.

    If the keystore backend becomes unavailable after a key has been created
    (e.g., the secret-service daemon stops), ``load()`` returns ``None`` and the
    caller should fall back gracefully to the next store in its priority chain.

    Parameters
    ----------
    path:
        Path to the encrypted config file on disk.
    default_factory:
        Callable that returns a default config dict (used only to satisfy the
        interface; ``KeystoreJsonStore`` never calls it directly).
    service_name:
        OS keystore service name.  Use different values for different
        applications to avoid key collisions (e.g. ``"autoyou-lite"`` vs
        ``"autoyou-server"``).
    username:
        OS keystore credential name (default: ``"config-encryption-key"``).
    """

    def __init__(
        self,
        path: "str | os.PathLike[str]",
        *,
        default_factory: Callable[[], Any],
        service_name: str = _LIB_SERVICE_NAME,
        username: str = _DEFAULT_CRED_NAME,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.default_factory = default_factory
        self._service_name = service_name
        self._username = username

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _make_fernet(
        self,
        *,
        create_key: bool,
        operation_timeout_seconds: Optional[float] = None,
    ) -> Optional[Any]:
        """Return a ``Fernet`` instance keyed from the OS keystore, or ``None``."""
        if not _HAS_FERNET or _Fernet is None:
            return None
        key_kwargs: Dict[str, Any] = {"create": create_key}
        if operation_timeout_seconds is not None:
            key_kwargs["operation_timeout_seconds"] = operation_timeout_seconds
        raw = get_or_create_key(self._service_name, self._username, **key_kwargs)
        if raw is None:
            return None
        return _Fernet(base64.urlsafe_b64encode(raw))

    # ── Public interface ──────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return ``True`` if the OS keystore backend is currently usable."""
        return keyring_available()

    def exists(self) -> bool:
        return self.path.exists()

    def load(self, *, operation_timeout_seconds: Optional[float] = None) -> Optional[Any]:
        """Decrypt and deserialise the config.  Returns ``None`` on any failure."""
        if not self.path.exists():
            return None
        f = self._make_fernet(
            create_key=False,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        if f is None:
            return None
        try:
            ciphertext = self.path.read_bytes()
            plaintext = f.decrypt(ciphertext)
            return json.loads(plaintext.decode("utf-8"))
        except _InvalidToken:
            logger.error(
                "Config decryption failed at %s - possible keystore key mismatch. "
                "Delete the file or re-enrol the keystore key to reset.",
                self.path,
            )
            return None
        except json.JSONDecodeError as exc:
            logger.warning(
                "KeystoreJsonStore: corrupt JSON after decryption at %s: %s",
                self.path, exc,
            )
            return None
        except Exception as exc:
            logger.warning("KeystoreJsonStore.load() failed for %s: %s", self.path, exc)
            return None

    def save(
        self,
        payload: Any,
        *,
        operation_timeout_seconds: Optional[float] = None,
        allow_key_creation: bool = True,
    ) -> None:
        """Serialise *payload* to JSON, encrypt it, and write it atomically.

        Raises ``RuntimeError`` if the OS keystore is not available so the
        caller can fall back to an alternative store.
        """
        f = self._make_fernet(
            create_key=allow_key_creation,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        if f is None:
            raise RuntimeError(
                "OS keystore is not available. "
                "Install 'keyring' (pip install keyring) and ensure a credential "
                "manager daemon is running (Keychain / Credential Manager / libsecret)."
            )
        plaintext = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        ciphertext = f.encrypt(plaintext)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(ciphertext)
        os.replace(tmp, self.path)
