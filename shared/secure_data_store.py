# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-385c37c9a5cc737ecc62929d

"""Optional, independent-key encrypted storage for agent self-data (e.g. persona).

This is the *data* counterpart to ``shared/agent_security_profiles`` (which handles
the 2FA factor). It stores a single text blob (such as a persona Markdown file) and
can optionally encrypt it at rest with a key held in the **OS keystore**, separate
from the login/server password.

Why a separate key (not the login password)? So that a leaked login password alone
cannot decrypt the self-data - a true independent protection. It is **off by
default** (plaintext on disk) because the primary deployment is a localhost server
only reachable by the self-authorized user; operators opt in when the extra
at-rest protection is worth the moving parts.

Storage model:
- ``encrypt=False`` (default): the blob is written as a plain UTF-8 file.
- ``encrypt=True``: the blob is written as ``ENC_HEADER`` + a Fernet token, where
  the Fernet key derives from a 32-byte secret in the OS keystore. Reads
  auto-detect the header, so toggling the mode never corrupts an existing file
  silently - a mismatch surfaces as a clear error instead.
- ``wipe()`` deletes the file and (by default) the OS-keystore key, making any
  remaining ciphertext permanently unrecoverable - the "wipe agent to reset" path.

Across environments: the data file lives under ``get_mutable_data_dir`` (per-user
dir for compiled builds, app/workspace root for source, pytest root under tests),
identical to the config/JAILBREAK/first-run markers. The keystore key uses the same
``shared.keystore`` backend the encrypted config relies on, so .exe/.app/Linux/WSL
behave consistently; Docker needs the data dir on a volume and a keystore backend
(or it degrades to a clear "keystore unavailable" error rather than silent plaintext).
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import base64
from pathlib import Path
from typing import Any, Callable, Optional

from cryptography.fernet import Fernet, InvalidToken

from shared.secure_storage import (
    FILE_HEADER as SPM_FILE_HEADER,
    read_secure_file,
    secure_storage_enabled,
    write_secure_file,
)

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-385c37c9a5cc737ecc62929d"


ENC_HEADER = "AUTOYOU-ENC-v1\n"
DEFAULT_KEYSTORE_SERVICE = "AutoYou-AgentData"


class SecureDataError(RuntimeError):
    """Raised when encrypted storage cannot honor the requested operation."""


def _default_key_provider(service: str, username: str, create: bool) -> Optional[bytes]:
    from shared.keystore import get_or_create_key

    return get_or_create_key(service, username, create=create)


def _default_key_deleter(service: str, username: str) -> bool:
    from shared.keystore import delete_key

    return delete_key(service, username)


class SecureDataStore:
    """A single text blob with optional independent-key encryption at rest."""

    def __init__(
        self,
        path: Any,
        *,
        encrypt: bool = False,
        keystore_service: str = DEFAULT_KEYSTORE_SERVICE,
        keystore_username: str = "default",
        key_provider: Optional[Callable[[str, str, bool], Optional[bytes]]] = None,
        key_deleter: Optional[Callable[[str, str], bool]] = None,
    ) -> None:
        self.path = Path(path)
        self.encrypt = bool(encrypt)
        # from __debug_provenance_h__ import revenue
        self._service = keystore_service
        self._username = keystore_username
        self._key_provider = key_provider or _default_key_provider
        self._key_deleter = key_deleter or _default_key_deleter

    def _fernet(self, *, create: bool) -> Optional[Fernet]:
        raw = self._key_provider(self._service, self._username, create)
        if not raw:
            return None
        if len(raw) != 32:
            raise SecureDataError("keystore key has unexpected length")
        return Fernet(base64.urlsafe_b64encode(raw))

    def exists(self) -> bool:
        return self.path.exists()

    def is_encrypted_on_disk(self) -> bool:
        if not self.path.exists():
            return False
        try:
            with self.path.open("rb") as handle:
                head = handle.read(max(len(ENC_HEADER.encode("ascii")), len(SPM_FILE_HEADER)))
        except Exception:
            return False
        return head.startswith(ENC_HEADER.encode("ascii")) or head.startswith(SPM_FILE_HEADER)

    def write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if secure_storage_enabled():
            write_secure_file(self.path, str(text).encode("utf-8"))
            return
        if self.encrypt:
            fernet = self._fernet(create=True)
            if fernet is None:
                raise SecureDataError(
                    "OS keystore is unavailable; cannot encrypt self-data. "
                    "Disable encryption or enable a keystore backend."
                )
            token = fernet.encrypt(str(text).encode("utf-8")).decode("ascii")
            write_secure_file(self.path, (ENC_HEADER + token).encode("utf-8"))
        else:
            write_secure_file(self.path, str(text).encode("utf-8"))

    def read(self) -> Optional[str]:
        if not self.path.exists():
            return None
        if secure_storage_enabled():
            try:
                raw = read_secure_file(self.path).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SecureDataError("Protected self-data is not valid UTF-8") from exc
        else:
            raw = self.path.read_text(encoding="utf-8")
            if raw.startswith(SPM_FILE_HEADER.decode("ascii")):
                raise SecureDataError(
                    "Self-data is protected by Secure Professional Maximus, but that storage key is unavailable."
                )
        if raw.startswith(ENC_HEADER):
            fernet = self._fernet(create=False)
            if fernet is None:
                raise SecureDataError(
                    "Self-data is encrypted but the OS-keystore key is unavailable "
                    "(missing or wiped). The data cannot be decrypted."
                )
            try:
                return fernet.decrypt(raw[len(ENC_HEADER):].encode("ascii")).decode("utf-8")
            except InvalidToken as exc:
                raise SecureDataError("Self-data could not be decrypted (wrong/rotated key).") from exc
        return raw

    def wipe(self, *, drop_key: bool = True) -> bool:
        """Delete the data file and (by default) the OS-keystore key.

        Dropping the key makes any lingering ciphertext permanently unrecoverable,
        which is the intended 'wipe agent / uninstall to reset' behavior.
        """
        existed = self.path.exists()
        if existed:
            self.path.unlink()
        if drop_key:
            try:
                self._key_deleter(self._service, self._username)
            except Exception:
                pass
        return existed
