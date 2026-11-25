# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Hardened local cryptographic vault for AutoYou (Argon2id + AES-256-GCM + HKDF-SHA-512).

Design goals:
* Offline brute-force resistance: Argon2id (64 MiB memory-hard, 4 iterations,
  4 lanes, 256-bit random salt) with automatic Scrypt fallback on platforms
  without OpenSSL/cryptography Argon2id bindings.
* Post-quantum symmetric strength: AES-256-GCM (256-bit keys retain 128-bit
  post-quantum security under Grover's algorithm) with 96-bit CSPRNG nonces
  and strict AAD binding every header and KDF parameter against tampering or
  downgrade attacks.
* Envelope encryption held strictly in memory: a single 256-bit CSPRNG Master
  DEK is wrapped on disk in ``local_vault.v2.enc`` (owner-only 0o600) and
  unwrapped into process memory on unlock. Domain-separated 256-bit subkeys
  are derived via HKDF-SHA-512 for config, Maximus storage, conversation
  history, saved connections, cloud credentials, local authenticators, and
  peer model credentials.
* Instant password rotation without data loss: changing the user password
  atomically re-wraps the Master DEK under a fresh 256-bit salt and Argon2id
  KEK while keeping all live in-memory services and encrypted stores working.
* Cross-platform safe: pure ``cryptography`` + stdlib; zero OS Keychain modal
  dialogs on macOS rebuilds while remaining 100% compatible with Windows,
  WSL, Linux, Docker, and existing OS keystore deployments.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import base64
import gc
import json
import logging
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id as _Argon2id

    _HAS_ARGON2ID = True
except Exception:  # pragma: no cover - older cryptography builds fall back to Scrypt.
    _Argon2id = None  # type: ignore[assignment]
    _HAS_ARGON2ID = False

try:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt as _Scrypt

    _HAS_SCRYPT = True
except Exception:  # pragma: no cover
    _Scrypt = None  # type: ignore[assignment]
    _HAS_SCRYPT = False

LOGGER = logging.getLogger("autoyou.vault_crypto")

VAULT_ENVELOPE_PREFIX = b"AUTOYOU-V2-AEAD\n"
VAULT_ENVELOPE_PREFIX_STR = "AUTOYOU-V2-AEAD\n"
VAULT_DATA_HEADER = b"AUTOYOU-VAULT-DATA-V2\n"
ARGON2_SALT_MAGIC = b"AY-ARGON2ID-V2\x00"

DEFAULT_VAULT_FILENAME = "local_vault.v2.enc"
DEFAULT_BOOTSTRAP_PASSWORD = "autoyou123"

# Argon2id parameters: 64 MiB memory-hard, 4 passes, 4 lanes, 32-byte salt -> 32-byte KEK.
# ~160ms on Apple Silicon / modern CPUs; brutally expensive on GPUs/ASICs.
ARGON2_MEMORY_COST_KIB = 65_536
ARGON2_ITERATIONS = 4
ARGON2_LANES = 4
ARGON2_MIN_MEMORY_COST_KIB = 16_384
ARGON2_MIN_ITERATIONS = 2
ARGON2_MIN_LANES = 1
ARGON2_MAX_MEMORY_COST_KIB = 1_048_576
ARGON2_MAX_ITERATIONS = 32
ARGON2_MAX_LANES = 16

# Scrypt fallback parameters (64 MiB memory-hard: N=2^16, r=8, p=1).
SCRYPT_N = 1 << 16
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MIN_N = 1 << 14
SCRYPT_MAX_N = 1 << 20

SALT_BYTES = 32
NONCE_BYTES = 12
KEY_BYTES = 32
MAX_ENVELOPE_BYTES = 64 * 1024 * 1024

ALLOWED_DOMAINS = frozenset(
    {
        "config",
        "spm",
        "history",
        "connections",
        "cloud",
        "authenticators",
        "peer-model",
        "local-secrets",
    }
)


class VaultCryptoError(RuntimeError):
    """Raised when encrypted vault operations fail or detect tampering."""


class VaultInvalidPasswordError(VaultCryptoError):
    """Raised when the supplied password cannot authenticate the vault envelope."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    if not isinstance(text, str) or not text:
        raise VaultCryptoError("Invalid base64 field in vault envelope")
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise VaultCryptoError("Corrupted base64 field in vault envelope") from exc


def _wipe_bytearray(buf: Optional[bytearray]) -> None:
    if buf is None:
        return
    for idx in range(len(buf)):
        buf[idx] = 0


def _canonical_aad(header: Dict[str, Any]) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _atomic_write_owner_only(path: Path, payload: bytes) -> None:
    """Atomically write bytes with owner-only permissions (0o600 file, 0o700 dir)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    try:
        try:
            fd = os.open(str(temporary), flags, 0o600)
        except OSError:
            fd = os.open(str(temporary), flags)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        if os.name != "nt":
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                gc.collect()
                time.sleep(0.05)
        if os.name != "nt":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            try:
                dir_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def preferred_kdf_name() -> str:
    if _HAS_ARGON2ID:
        return "argon2id"
    if _HAS_SCRYPT:
        return "scrypt"
    raise VaultCryptoError("No memory-hard KDF (Argon2id or Scrypt) is available")


def derive_password_kek(
    password: str,
    salt: bytes,
    *,
    kdf: Optional[str] = None,
    kdf_params: Optional[Dict[str, int]] = None,
) -> Tuple[bytes, str, Dict[str, int]]:
    """Derive a 256-bit key-encryption key using Argon2id (or Scrypt fallback)."""
    if not isinstance(password, str) or not password:
        raise VaultInvalidPasswordError("A non-empty password is required")
    if not isinstance(salt, (bytes, bytearray)) or len(salt) < 16:
        raise VaultCryptoError("KDF salt must be at least 16 bytes")
    chosen = (kdf or preferred_kdf_name()).strip().lower()
    password_bytes = password.encode("utf-8")

    if chosen == "argon2id":
        if not _HAS_ARGON2ID or _Argon2id is None:
            raise VaultCryptoError("Argon2id is unavailable on this runtime")
        params = dict(kdf_params or {})
        memory_cost = int(params.get("m", ARGON2_MEMORY_COST_KIB))
        iterations = int(params.get("t", ARGON2_ITERATIONS))
        lanes = int(params.get("p", ARGON2_LANES))
        if not (ARGON2_MIN_MEMORY_COST_KIB <= memory_cost <= ARGON2_MAX_MEMORY_COST_KIB):
            raise VaultCryptoError("Argon2id memory parameter is out of policy bounds")
        if not (ARGON2_MIN_ITERATIONS <= iterations <= ARGON2_MAX_ITERATIONS):
            raise VaultCryptoError("Argon2id iteration parameter is out of policy bounds")
        if not (ARGON2_MIN_LANES <= lanes <= ARGON2_MAX_LANES):
            raise VaultCryptoError("Argon2id lane parameter is out of policy bounds")
        deriver = _Argon2id(
            salt=bytes(salt),
            length=KEY_BYTES,
            iterations=iterations,
            lanes=lanes,
            memory_cost=memory_cost,
        )
        return deriver.derive(password_bytes), "argon2id", {
            "m": memory_cost,
            "t": iterations,
            "p": lanes,
        }

    if chosen == "scrypt":
        if not _HAS_SCRYPT or _Scrypt is None:
            raise VaultCryptoError("Scrypt is unavailable on this runtime")
        params = dict(kdf_params or {})
        n = int(params.get("n", SCRYPT_N))
        r = int(params.get("r", SCRYPT_R))
        p = int(params.get("p", SCRYPT_P))
        if n < SCRYPT_MIN_N or n > SCRYPT_MAX_N or (n & (n - 1)) != 0 or r < 8 or p < 1:
            raise VaultCryptoError("Scrypt parameters are out of policy bounds")
        deriver = _Scrypt(salt=bytes(salt), length=KEY_BYTES, n=n, r=r, p=p)
        return deriver.derive(password_bytes), "scrypt", {"n": n, "r": r, "p": p}

    raise VaultCryptoError(f"Unsupported KDF: {chosen!r}")


def is_v2_aead_envelope(envelope: str | bytes) -> bool:
    if isinstance(envelope, bytes):
        return envelope.lstrip().startswith(VAULT_ENVELOPE_PREFIX)
    if isinstance(envelope, str):
        return envelope.lstrip().startswith(VAULT_ENVELOPE_PREFIX_STR)
    return False


def aead_encrypt_v2(
    plaintext: str | bytes,
    password: str,
    *,
    purpose: str = "config",
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Encrypt plaintext with Argon2id (64 MiB, t=4, p=4) + AES-256-GCM.

    Every header and metadata field is bound via GCM AAD to prevent KDF
    downgrade, metadata forgery, or cross-purpose envelope substitution.
    """
    data = plaintext.encode("utf-8") if isinstance(plaintext, str) else bytes(plaintext)
    salt = secrets.token_bytes(SALT_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    kek, kdf_name, kdf_params = derive_password_kek(password, salt)
    kek_buf = bytearray(kek)
    try:
        header: Dict[str, Any] = {
            "v": 2,
            "purpose": str(purpose or "config"),
            "kdf": kdf_name,
            "kdf_params": kdf_params,
            "cipher": "aes-256-gcm",
            "salt": _b64e(salt),
        }
        if meta:
            header["meta"] = dict(meta)
        aad = _canonical_aad(header)
        ciphertext = AESGCM(bytes(kek_buf)).encrypt(nonce, data, aad)
        envelope = {
            **header,
            "nonce": _b64e(nonce),
            "ct": _b64e(ciphertext),
        }
        encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return VAULT_ENVELOPE_PREFIX_STR + encoded
    finally:
        _wipe_bytearray(kek_buf)


def parse_v2_aead_header(envelope: str | bytes) -> Dict[str, Any]:
    """Parse the outer JSON header of a V2 envelope without decrypting."""
    raw = envelope.decode("utf-8") if isinstance(envelope, bytes) else str(envelope)
    stripped = raw.lstrip()
    if not stripped.startswith(VAULT_ENVELOPE_PREFIX_STR):
        raise VaultCryptoError("Not a V2 AEAD envelope")
    body = stripped[len(VAULT_ENVELOPE_PREFIX_STR) :].strip()
    if len(body.encode("utf-8")) > MAX_ENVELOPE_BYTES:
        raise VaultCryptoError("Vault envelope exceeds size limit")
    try:
        parsed = json.loads(body)
    except Exception as exc:
        raise VaultCryptoError("Vault envelope JSON is invalid") from exc
    if not isinstance(parsed, dict) or parsed.get("v") != 2:
        raise VaultCryptoError("Unsupported vault envelope version")
    if parsed.get("cipher") != "aes-256-gcm":
        raise VaultCryptoError("Unsupported vault cipher")
    return parsed


def aead_decrypt_v2(
    envelope: str | bytes,
    password: str,
    *,
    purpose: str = "config",
) -> bytes:
    """Authenticate and decrypt a V2 Argon2id + AES-256-GCM envelope."""
    parsed = parse_v2_aead_header(envelope)
    expected_purpose = str(purpose or "config")
    if parsed.get("purpose") != expected_purpose:
        raise VaultCryptoError("Vault envelope purpose mismatch")
    salt = _b64d(parsed.get("salt", ""))
    nonce = _b64d(parsed.get("nonce", ""))
    ciphertext = _b64d(parsed.get("ct", ""))
    if len(nonce) != NONCE_BYTES or len(ciphertext) < 16:
        raise VaultCryptoError("Invalid nonce or ciphertext in vault envelope")

    header: Dict[str, Any] = {
        "v": 2,
        "purpose": parsed["purpose"],
        "kdf": parsed.get("kdf"),
        "kdf_params": parsed.get("kdf_params"),
        "cipher": "aes-256-gcm",
        "salt": parsed["salt"],
    }
    if "meta" in parsed:
        header["meta"] = parsed["meta"]
    aad = _canonical_aad(header)

    kek, _, _ = derive_password_kek(
        password,
        salt,
        kdf=str(parsed.get("kdf") or ""),
        kdf_params=parsed.get("kdf_params") if isinstance(parsed.get("kdf_params"), dict) else None,
    )
    kek_buf = bytearray(kek)
    try:
        return AESGCM(bytes(kek_buf)).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise VaultInvalidPasswordError("Invalid password or tampered vault envelope") from exc
    finally:
        _wipe_bytearray(kek_buf)


def derive_subkey(master_key: bytes, domain: str, *, vault_id: str = "") -> bytes:
    """Derive a domain-separated 256-bit subkey via HKDF-SHA-512."""
    if not isinstance(master_key, (bytes, bytearray)) or len(master_key) != KEY_BYTES:
        raise VaultCryptoError("Master key must be exactly 32 bytes")
    normalized = str(domain or "").strip().lower()
    if normalized not in ALLOWED_DOMAINS:
        raise VaultCryptoError(f"Unsupported vault domain: {domain!r}")
    hkdf = HKDF(
        algorithm=hashes.SHA512(),
        length=KEY_BYTES,
        salt=vault_id.encode("utf-8") if vault_id else None,
        info=f"autoyou-local-vault-v2:{normalized}".encode("utf-8"),
    )
    return hkdf.derive(bytes(master_key))


def is_argon2_salt_blob(blob: bytes) -> bool:
    return isinstance(blob, (bytes, bytearray)) and bytes(blob).startswith(ARGON2_SALT_MAGIC)


def make_argon2_salt_blob() -> bytes:
    """Create a versioned Argon2id salt blob for Secure Professional Maximus."""
    return ARGON2_SALT_MAGIC + secrets.token_bytes(SALT_BYTES)


def derive_spm_argon2_key(password: str, salt_blob: bytes) -> bytes:
    """Derive a Fernet-compatible (32-byte urlsafe-base64) key via Argon2id/Scrypt."""
    if not is_argon2_salt_blob(salt_blob):
        raise VaultCryptoError("Not an Argon2id Maximus salt blob")
    raw_salt = bytes(salt_blob)[len(ARGON2_SALT_MAGIC) :]
    if len(raw_salt) < 16:
        raise VaultCryptoError("Argon2id Maximus salt is too short")
    raw_key, _, _ = derive_password_kek(password, raw_salt)
    return base64.urlsafe_b64encode(raw_key)


class VaultCipher:
    """AES-256-GCM authenticated cipher bound to a single domain subkey in memory."""

    def __init__(self, key: bytes, *, domain: str) -> None:
        if not isinstance(key, (bytes, bytearray)) or len(key) != KEY_BYTES:
            raise VaultCryptoError("VaultCipher requires a 32-byte key")
        self._domain = str(domain or "local-secrets")
        self._aad = f"AUTOYOU-VAULT-DATA-V2|{self._domain}".encode("utf-8")
        self._key = bytearray(key)

    @property
    def domain(self) -> str:
        return self._domain

    def seal(self, plaintext: bytes) -> bytes:
        nonce = secrets.token_bytes(NONCE_BYTES)
        ct = AESGCM(bytes(self._key)).encrypt(nonce, bytes(plaintext), self._aad)
        return VAULT_DATA_HEADER + nonce + ct

    def open(self, blob: bytes) -> bytes:
        raw = bytes(blob)
        if not raw.startswith(VAULT_DATA_HEADER):
            raise VaultCryptoError(f"Invalid header on encrypted {self._domain} store")
        body = raw[len(VAULT_DATA_HEADER) :]
        if len(body) < NONCE_BYTES + 16:
            raise VaultCryptoError(f"Truncated encrypted {self._domain} store")
        nonce, ct = body[:NONCE_BYTES], body[NONCE_BYTES:]
        try:
            return AESGCM(bytes(self._key)).decrypt(nonce, ct, self._aad)
        except InvalidTag as exc:
            raise VaultCryptoError(f"Authentication failed reading encrypted {self._domain} store") from exc

    def seal_json(self, payload: Any) -> bytes:
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        return self.seal(encoded)

    def open_json(self, blob: bytes) -> Any:
        raw = self.open(blob)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise VaultCryptoError(f"Decrypted {self._domain} JSON is invalid") from exc

    def save_json_file(self, path: str | Path, payload: Any) -> None:
        _atomic_write_owner_only(Path(path), self.seal_json(payload))

    def load_json_file(self, path: str | Path) -> Optional[Any]:
        target = Path(path)
        if not target.is_file():
            return None
        return self.open_json(target.read_bytes())

    def close(self) -> None:
        _wipe_bytearray(self._key)


class LocalMasterVault:
    """In-memory Master DEK manager backed by an Argon2id+AES-256-GCM key file.

    Only the password-wrapped Master DEK ever touches disk (``local_vault.v2.enc``,
    owner-only ``0o600``). Once unlocked, the 256-bit Master DEK and derived
    domain subkeys live strictly in process memory.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._master_key: Optional[bytearray] = None
        self._vault_id: str = ""
        self._session_password: Optional[str] = None
        self._uses_default_password: bool = False
        self._kdf_name: str = preferred_kdf_name()
        self._kdf_params: Dict[str, int] = (
            {"m": ARGON2_MEMORY_COST_KIB, "t": ARGON2_ITERATIONS, "p": ARGON2_LANES}
            if self._kdf_name == "argon2id"
            else {"n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}
        )
        self._created_at: float = 0.0
        self._password_changed_at: float = 0.0
        self._key_rotated_at: float = 0.0

    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def unlocked(self) -> bool:
        with self._lock:
            return self._master_key is not None

    @property
    def session_password(self) -> Optional[str]:
        with self._lock:
            return self._session_password

    @property
    def uses_default_password(self) -> bool:
        with self._lock:
            if self._master_key is not None:
                return self._uses_default_password
            meta = self.inspect_metadata()
            return bool(meta.get("uses_default_password"))

    def inspect_metadata(self) -> Dict[str, Any]:
        """Inspect authenticated outer envelope metadata without unlocking."""
        if not self.path.is_file():
            return {
                "exists": False,
                "uses_default_password": False,
                "kdf": preferred_kdf_name(),
                "cipher": "aes-256-gcm",
            }
        try:
            parsed = parse_v2_aead_header(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("LocalMasterVault header could not be parsed: %s", exc)
            return {
                "exists": True,
                "corrupt": True,
                "uses_default_password": False,
                "kdf": "unknown",
                "cipher": "unknown",
            }
        meta = parsed.get("meta") if isinstance(parsed.get("meta"), dict) else {}
        return {
            "exists": True,
            "corrupt": False,
            "uses_default_password": bool(meta.get("uses_default_password", False)),
            "kdf": str(parsed.get("kdf") or ""),
            "kdf_params": dict(parsed.get("kdf_params") or {}),
            "cipher": str(parsed.get("cipher") or "aes-256-gcm"),
        }

    def status(self, *, legacy_keychain_backup: bool = False) -> Dict[str, Any]:
        with self._lock:
            meta = self.inspect_metadata()
            unlocked = self._master_key is not None
            exists = bool(meta.get("exists"))
            kdf_params = self._kdf_params if unlocked else dict(meta.get("kdf_params") or {})
            return {
                "exists": exists,
                "unlocked": unlocked,
                "needs_password": exists and not unlocked and not bool(meta.get("corrupt")),
                "uses_default_password": self._uses_default_password if unlocked else bool(meta.get("uses_default_password")),
                "kdf": self._kdf_name if unlocked else str(meta.get("kdf") or preferred_kdf_name()),
                "kdf_memory_kib": int(kdf_params.get("m", ARGON2_MEMORY_COST_KIB)),
                "kdf_iterations": int(kdf_params.get("t", ARGON2_ITERATIONS)),
                "cipher": "aes-256-gcm",
                "vault_id": self._vault_id if unlocked else "",
                "legacy_keychain_backup": bool(legacy_keychain_backup),
            }

    def _write_locked_envelope(self, password: str, *, default_password: str = DEFAULT_BOOTSTRAP_PASSWORD) -> None:
        assert self._master_key is not None
        is_default = password == default_password
        inner = {
            "v": 2,
            "vault_id": self._vault_id,
            "master_key_b64": _b64e(bytes(self._master_key)),
            "uses_default_password": is_default,
            "created_at": self._created_at,
            "password_changed_at": self._password_changed_at,
            "key_rotated_at": self._key_rotated_at,
        }
        envelope = aead_encrypt_v2(
            json.dumps(inner, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
            password,
            purpose="local-master-vault",
            meta={"uses_default_password": is_default},
        )
        _atomic_write_owner_only(self.path, envelope.encode("utf-8"))
        parsed = parse_v2_aead_header(envelope)
        self._kdf_name = str(parsed.get("kdf") or preferred_kdf_name())
        self._kdf_params = dict(parsed.get("kdf_params") or {})
        self._uses_default_password = is_default
        self._session_password = password

    def create(
        self,
        password: str,
        *,
        default_password: str = DEFAULT_BOOTSTRAP_PASSWORD,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Generate a new 256-bit Master DEK, wrap it on disk, and hold it in memory."""
        if not isinstance(password, str) or not password:
            raise VaultInvalidPasswordError("A password is required to create the local vault")
        with self._lock:
            if self.path.exists() and not overwrite:
                raise VaultCryptoError("Local vault already exists; unlock it instead of overwriting")
            now = time.time()
            _wipe_bytearray(self._master_key)
            self._master_key = bytearray(secrets.token_bytes(KEY_BYTES))
            self._vault_id = secrets.token_hex(16)
            self._created_at = now
            self._password_changed_at = now
            self._key_rotated_at = now
            self._write_locked_envelope(password, default_password=default_password)
            return self.status()

    def unlock(
        self,
        password: str,
        *,
        default_password: str = DEFAULT_BOOTSTRAP_PASSWORD,
    ) -> Dict[str, Any]:
        """Decrypt the wrapped Master DEK into memory using ``password``."""
        if not isinstance(password, str) or not password:
            raise VaultInvalidPasswordError("Enter your AutoYou password to unlock the vault")
        with self._lock:
            if not self.path.is_file():
                raise VaultCryptoError("Local vault file does not exist")
            raw_text = self.path.read_text(encoding="utf-8")
            parsed_header = parse_v2_aead_header(raw_text)
            plaintext = aead_decrypt_v2(raw_text, password, purpose="local-master-vault")
            try:
                inner = json.loads(plaintext.decode("utf-8"))
            except Exception as exc:
                raise VaultCryptoError("Decrypted local vault payload is invalid") from exc
            if not isinstance(inner, dict) or inner.get("v") != 2:
                raise VaultCryptoError("Unsupported decrypted local vault version")
            raw_key = _b64d(str(inner.get("master_key_b64") or ""))
            if len(raw_key) != KEY_BYTES:
                raise VaultCryptoError("Local vault master key has an invalid length")
            outer_meta = parsed_header.get("meta") if isinstance(parsed_header.get("meta"), dict) else {}
            inner_default = bool(inner.get("uses_default_password", False))
            if bool(outer_meta.get("uses_default_password", False)) != inner_default:
                raise VaultCryptoError("Local vault metadata integrity check failed")
            _wipe_bytearray(self._master_key)
            self._master_key = bytearray(raw_key)
            self._vault_id = str(inner.get("vault_id") or "")
            self._created_at = float(inner.get("created_at") or 0.0)
            self._password_changed_at = float(inner.get("password_changed_at") or 0.0)
            self._key_rotated_at = float(inner.get("key_rotated_at") or 0.0)
            self._uses_default_password = password == default_password
            self._session_password = password
            self._kdf_name = str(parsed_header.get("kdf") or preferred_kdf_name())
            self._kdf_params = dict(parsed_header.get("kdf_params") or {})
            return self.status()

    def lock(self) -> None:
        """Zero and discard all in-memory key material."""
        with self._lock:
            _wipe_bytearray(self._master_key)
            self._master_key = None
            self._session_password = None

    def change_password(
        self,
        new_password: str,
        *,
        current_password: Optional[str] = None,
        default_password: str = DEFAULT_BOOTSTRAP_PASSWORD,
    ) -> Dict[str, Any]:
        """Re-wrap the existing 256-bit Master DEK under a new Argon2id password KEK."""
        if not isinstance(new_password, str) or not new_password:
            raise VaultInvalidPasswordError("New password must not be empty")
        with self._lock:
            if self._master_key is None:
                if current_password is None:
                    raise VaultCryptoError("Unlock the local vault before changing its password")
                self.unlock(current_password, default_password=default_password)
            elif current_password is not None and self._session_password is not None:
                if not secrets.compare_digest(current_password, self._session_password):
                    raise VaultInvalidPasswordError("Current password does not match")
            self._password_changed_at = time.time()
            self._write_locked_envelope(new_password, default_password=default_password)
            return self.status()

    def rotate_master_key(
        self,
        *,
        on_rotate: Optional[Callable[[Callable[[str], VaultCipher], Callable[[str], VaultCipher]], None]] = None,
        default_password: str = DEFAULT_BOOTSTRAP_PASSWORD,
    ) -> Dict[str, Any]:
        """Generate a fresh 256-bit Master DEK and optionally re-encrypt dependent stores."""
        with self._lock:
            if self._master_key is None or not self._session_password:
                raise VaultCryptoError("Unlock the local vault before rotating its master key")
            old_key = bytes(self._master_key)
            old_vault_id = self._vault_id
            new_key = secrets.token_bytes(KEY_BYTES)
            new_vault_id = secrets.token_hex(16)

            def _old_cipher(domain: str) -> VaultCipher:
                return VaultCipher(derive_subkey(old_key, domain, vault_id=old_vault_id), domain=domain)

            def _new_cipher(domain: str) -> VaultCipher:
                return VaultCipher(derive_subkey(new_key, domain, vault_id=new_vault_id), domain=domain)

            if on_rotate is not None:
                on_rotate(_old_cipher, _new_cipher)

            previous_buf = self._master_key
            previous_id = self._vault_id
            previous_rotated = self._key_rotated_at
            self._master_key = bytearray(new_key)
            self._vault_id = new_vault_id
            self._key_rotated_at = time.time()
            try:
                self._write_locked_envelope(self._session_password, default_password=default_password)
            except Exception:
                _wipe_bytearray(self._master_key)
                self._master_key = previous_buf
                self._vault_id = previous_id
                self._key_rotated_at = previous_rotated
                raise
            _wipe_bytearray(previous_buf)
            return self.status()

    def subkey(self, domain: str) -> bytes:
        with self._lock:
            if self._master_key is None:
                raise VaultCryptoError("Local vault is locked")
            return derive_subkey(bytes(self._master_key), domain, vault_id=self._vault_id)

    def cipher(self, domain: str) -> VaultCipher:
        return VaultCipher(self.subkey(domain), domain=domain)

    def export_client_subkeys_b64(self) -> Dict[str, str]:
        """Export domain subkeys for the native parent shell over private stdio IPC."""
        with self._lock:
            if self._master_key is None:
                raise VaultCryptoError("Local vault is locked")
            return {
                "vault_id": self._vault_id,
                "history": base64.b64encode(self.subkey("history")).decode("ascii"),
                "connections": base64.b64encode(self.subkey("connections")).decode("ascii"),
                "peer_model": base64.b64encode(self.subkey("peer-model")).decode("ascii"),
            }


__all__ = [
    "ALLOWED_DOMAINS",
    "ARGON2_ITERATIONS",
    "ARGON2_LANES",
    "ARGON2_MEMORY_COST_KIB",
    "ARGON2_SALT_MAGIC",
    "DEFAULT_BOOTSTRAP_PASSWORD",
    "DEFAULT_VAULT_FILENAME",
    "LocalMasterVault",
    "VAULT_DATA_HEADER",
    "VAULT_ENVELOPE_PREFIX",
    "VAULT_ENVELOPE_PREFIX_STR",
    "VaultCipher",
    "VaultCryptoError",
    "VaultInvalidPasswordError",
    "aead_decrypt_v2",
    "aead_encrypt_v2",
    "derive_password_kek",
    "derive_spm_argon2_key",
    "derive_subkey",
    "is_argon2_salt_blob",
    "is_v2_aead_envelope",
    "make_argon2_salt_blob",
    "parse_v2_aead_header",
    "preferred_kdf_name",
]
