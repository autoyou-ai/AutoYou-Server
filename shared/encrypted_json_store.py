# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-646472657373202d20334163-f550008cde83f55e918b8027

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-646472657373202d20334163-f550008cde83f55e918b8027"


import base64
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .secure_storage import (
    FILE_HEADER,
    SecureStorageError,
    disable_secure_storage,
    enable_secure_storage,
    read_secure_file,
    secure_storage_enabled,
    write_secure_file,
)


_CONFIG_IO_LOCK = threading.RLock()


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt_payload(plaintext: str, password: str) -> str:
    salt = os.urandom(16)
    token = Fernet(_derive_key(password, salt)).encrypt(plaintext.encode("utf-8"))
    return base64.urlsafe_b64encode(salt + token).decode("utf-8")


def decrypt_payload(envelope: str, password: str) -> str:
    decoded = base64.urlsafe_b64decode(envelope)
    salt = decoded[:16]
    token = decoded[16:]
    return Fernet(_derive_key(password, salt)).decrypt(token).decode("utf-8")


class EncryptedJsonStore:
    """Small Fernet-backed JSON config store for AutoYou services."""

    def __init__(
        self,
        path: str | Path,
        *,
        default_factory: Callable[[], Any],
        secure_app_name: str = "AutoYouLite",
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.default_factory = default_factory
        self.secure_app_name = secure_app_name

    def exists(self) -> bool:
        return self.path.exists()

    def ensure_exists(self, password: str) -> None:
        if self.exists():
            return
        payload = self.default_factory()
        self.save(payload, password)

    def load(self, password: str) -> Optional[Any]:
        attached_storage = False
        try:
            with _CONFIG_IO_LOCK:
                raw = self.path.read_bytes()
                if raw.startswith(FILE_HEADER) and not secure_storage_enabled():
                    # A fresh Lite process must attach to the same SPM context
                    # before it can unlock a config saved by the prior process.
                    enable_secure_storage(
                        app_name=self.secure_app_name,
                        root=self.path.parent,
                        password=password or None,
                    )
                    attached_storage = True
                envelope = read_secure_file(self.path).decode("utf-8")
            plaintext = decrypt_payload(envelope, password)
            return json.loads(plaintext)
        except Exception:
            if attached_storage:
                # Do not leave a context derived from a failed password in the
                # process; a later admin unlock must be able to retry cleanly.
                disable_secure_storage()
            return None

    def save(self, payload: Any, password: str) -> None:
        plaintext = json.dumps(payload, indent=2, sort_keys=True)
        envelope = encrypt_payload(plaintext, password)
        with _CONFIG_IO_LOCK:
            if secure_storage_enabled():
                write_secure_file(self.path, envelope.encode("utf-8"))
                return
            if self.path.exists() and self.path.read_bytes().startswith(FILE_HEADER):
                raise SecureStorageError(
                    "Protected Lite configuration requires Secure Professional Maximus storage"
                )
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp_path.write_text(envelope, encoding="utf-8")
            with tmp_path.open("r+", encoding="utf-8") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
