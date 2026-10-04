# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Atomic protected transport state with its own key, separate from endpoint identity."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
from typing import Any, Callable, TypeVar

from cryptography.fernet import Fernet, InvalidToken

from shared.iroh_instance import EndpointLease
from shared.iroh_keys import EndpointKeyUnavailable, EndpointKeys

_STATE_LOCK = threading.RLock()
_MAX_STATE_BYTES = 16 * 1024 * 1024
_Result = TypeVar("_Result")


class ProtectedTransportStateUnavailable(RuntimeError):
    pass


class ProtectedTransportState:
    def __init__(self, *, keys: EndpointKeys, unlocked_password: str | None = None) -> None:
        if keys.purpose == "identity":
            raise ValueError("transport state requires a separate storage key")
        self.keys, self._password = keys, unlocked_password
        self.root = keys.root / "state"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "state.enc"

    def _fernet(self, *, create: bool) -> Fernet:
        try:
            raw_key = self.keys.load(enroll=create, unlocked_password=self._password)
        except EndpointKeyUnavailable:
            raise ProtectedTransportStateUnavailable("protected transport state must be unlocked or recovered") from None
        return Fernet(base64.urlsafe_b64encode(raw_key))

    def _read(self, default_factory: Callable[[], Any]) -> Any:
        if not self.path.exists():
            if self.keys.witness.exists():
                raise ProtectedTransportStateUnavailable("protected transport state was lost and requires recovery")
            return default_factory()
        try:
            if self.path.stat().st_size > _MAX_STATE_BYTES:
                raise ValueError
            plaintext = self._fernet(create=False).decrypt(self.path.read_bytes())
            if len(plaintext) > _MAX_STATE_BYTES:
                raise ValueError
            return json.loads(plaintext)
        except (InvalidToken, ValueError, OSError):
            raise ProtectedTransportStateUnavailable("protected transport state is corrupt or unavailable") from None

    def read(self, *, default_factory: Callable[[], Any]) -> Any:
        with _STATE_LOCK, EndpointLease(self.root, identity_transaction=True):
            return self._read(default_factory)

    def transaction(self, update: Callable[[Any], tuple[Any, _Result]], *, default_factory: Callable[[], Any]) -> _Result:
        """Persist first, then return the new grant/counter/receipt to its caller."""
        with _STATE_LOCK, EndpointLease(self.root, identity_transaction=True):
            value, result = update(self._read(default_factory))
            plaintext = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(plaintext) > _MAX_STATE_BYTES // 2:
                raise ProtectedTransportStateUnavailable("protected transport state exceeds its bound")
            ciphertext = self._fernet(create=not self.path.exists()).encrypt(plaintext)
            descriptor, temporary = tempfile.mkstemp(dir=self.root, prefix="state-", suffix=".tmp")
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(ciphertext)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                if os.name != "nt":
                    self.path.chmod(0o600)
                    directory = os.open(self.root, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return result

    def close(self) -> None:
        self._password = None
