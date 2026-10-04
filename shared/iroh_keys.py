# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Protected per-install endpoint seed; corruption never silently rotates it."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
from pathlib import Path

from shared import keystore
from shared.encrypted_json_store import EncryptedJsonStore
from shared.iroh_instance import EndpointLease
from shared.platform_runtime import get_service_data_dir

_KEY_LOCK = threading.RLock()


class EndpointKeyUnavailable(RuntimeError):
    pass


class EndpointNeedsPairing(EndpointKeyUnavailable):
    pass


class EndpointKeys:
    """Use the existing platform credential boundary with a distinct service.

    A public witness detects lost/corrupt credentials. It contains no seed,
    account, owner, Ticket, grant or resume secret. Rotation is a separate
    authorized enrollment operation.
    """

    def __init__(self, *, role: str, app_name: str = "AutoYou", anchor: str | Path | None = None,
                 purpose: str = "identity") -> None:
        if role not in {"server", "client", "lite"}:
            raise ValueError("unsupported endpoint role")
        if purpose not in {"identity", "grants", "delivery", "resume"}:
            raise ValueError("unsupported endpoint key purpose")
        self.role, self.purpose = role, purpose
        suffix = "" if purpose == "identity" else f"/{purpose}"
        self.root = get_service_data_dir(f"iroh/{role}{suffix}", app_name=app_name, anchor=anchor).resolve()
        self.witness = self.root / "endpoint.identity.json"
        scope = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()[:16]
        self.service = keystore.server_keystore_service_name(
            self.witness, service=f"autoyou-iroh-{role}-{purpose}-{scope}",
        )
        self.username = "endpoint-seed-v1"
        self.password_store = EncryptedJsonStore(
            self.root / "endpoint.seed.enc", default_factory=dict, secure_app_name=app_name,
        )

    def claim_instance(self) -> EndpointLease:
        return EndpointLease(self.root)

    def _read_seed(self) -> bytes | None:
        encoded = keystore.get_keyring_password(self.service, self.username)
        if encoded is None:
            return None
        try:
            seed = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
        except (ValueError, UnicodeError):
            raise EndpointNeedsPairing("endpoint credential requires authorized recovery") from None
        if len(seed) != 32:
            raise EndpointNeedsPairing("endpoint credential requires authorized recovery")
        return seed

    def _read_password_seed(self, password: str | None) -> bytes:
        if not password:
            raise EndpointKeyUnavailable("endpoint password storage must be unlocked")
        try:
            if self.password_store.path.stat().st_size > 16 * 1024:
                raise ValueError
            value = self.password_store.load(password)
            if not isinstance(value, dict) or set(value) != {"schema", "seed"} or value["schema"] != 1:
                raise ValueError
            seed = base64.b64decode(value["seed"].encode("ascii"), altchars=b"-_", validate=True)
            if len(seed) != 32:
                raise ValueError
            return seed
        except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
            raise EndpointNeedsPairing("endpoint password credential requires authorized recovery") from None

    def load(self, *, enroll: bool = False, unlocked_password: str | None = None) -> bytes:
        """Use a password fallback only when explicitly unlocked by the host.

        The witness fixes the storage backend. Missing/corrupt credentials never
        trigger fallback, creation, migration, or rotation of an existing key.
        """
        with _KEY_LOCK, EndpointLease(self.root, identity_transaction=True):
            if self.witness.exists():
                try:
                    if self.witness.stat().st_size > 1024:
                        raise ValueError
                    witness = json.loads(self.witness.read_text(encoding="utf-8"))
                    expected = witness["seed_sha256"]
                    if witness["schema"] != 1 or not isinstance(expected, str) or len(expected) != 64:
                        raise ValueError
                    storage = witness.get("storage", "platform")
                    if storage not in {"platform", "password"}:
                        raise ValueError
                except (OSError, ValueError, KeyError, TypeError):
                    raise EndpointNeedsPairing("endpoint identity requires authorized recovery") from None
                seed = self._read_seed() if storage == "platform" else self._read_password_seed(unlocked_password)
                if seed is None or not secrets.compare_digest(hashlib.sha256(seed).hexdigest(), expected):
                    raise EndpointNeedsPairing("endpoint credential is missing or has changed")
                return seed
            if not enroll:
                raise EndpointNeedsPairing("endpoint has not been enrolled")
            if self.password_store.exists():
                storage = "password"
                seed = self._read_password_seed(unlocked_password)
            else:
                storage = "platform"
                seed = self._read_seed()
            if seed is None:
                seed = secrets.token_bytes(32)
                if not keystore.replace_key(self.service, self.username, seed):
                    if not unlocked_password:
                        raise EndpointKeyUnavailable("platform protected endpoint storage is unavailable")
                    storage = "password"
                    self.password_store.save({
                        "schema": 1, "seed": base64.urlsafe_b64encode(seed).decode("ascii"),
                    }, unlocked_password)
                    if os.name != "nt":
                        self.password_store.path.chmod(0o600)
                    persisted = self._read_password_seed(unlocked_password)
                else:
                    persisted = self._read_seed()
                if persisted != seed:
                    raise EndpointKeyUnavailable("platform protected endpoint storage did not persist")
            witness = json.dumps({"schema": 1, "storage": storage,
                "seed_sha256": hashlib.sha256(seed).hexdigest()}, sort_keys=True).encode("utf-8")
            try:
                descriptor = os.open(self.witness, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(witness); handle.flush(); os.fsync(handle.fileno())
            except FileExistsError:
                # All cooperating writers own identity.lock. An unexpected
                # witness is not a reason to overwrite or change credentials.
                raise EndpointNeedsPairing("endpoint identity changed during enrollment") from None
            return seed
