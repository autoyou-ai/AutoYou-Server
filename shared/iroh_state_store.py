# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Platform key and policy callbacks for the shared native protected-state owner."""

from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Any, Callable, TypeVar

from shared.iroh_binding import load_binding
from shared.iroh_instance import EndpointAlreadyRunning
from shared.iroh_keys import EndpointKeyUnavailable, EndpointKeys
from shared.session_transport import SessionDenied

_STATE_LOCK = threading.RLock()
_MAX_STATE_BYTES = 16 * 1024 * 1024
_Result = TypeVar("_Result")


class ProtectedTransportStateUnavailable(RuntimeError):
    pass


@contextmanager
def authorization_errors(api):
    try:
        yield
    except api.AuthorizationError.Denied as error:
        raise SessionDenied(error.reason) from None
    except api.AuthorizationError.Capacity:
        raise ProtectedTransportStateUnavailable("protected authorization state exceeds its bound") from None
    except (api.AuthorizationError.InvalidState, api.AuthorizationError.Unavailable, api.AuthorizationError.Closed):
        raise ProtectedTransportStateUnavailable("protected authorization state is corrupt or unavailable") from None


class ProtectedTransportState:
    def __init__(self, *, keys: EndpointKeys, unlocked_password: str | None = None) -> None:
        if keys.purpose == "identity":
            raise ValueError("transport state requires a separate storage key")
        self.keys, self._password = keys, unlocked_password
        self._api = None
        self.root = keys.root / "state"
        self.path = self.root / "state.enc"
        self.rollback_baseline = self.root / "rollback-baseline.enc"

    def _key(self, *, create: bool) -> bytes:
        try:
            return self.keys.load(enroll=create, unlocked_password=self._password)
        except EndpointKeyUnavailable:
            raise ProtectedTransportStateUnavailable("protected transport state must be unlocked or recovered") from None

    @contextmanager
    def _transaction(self):
        # Preserve same-process callback serialization while Rust owns the OS lease.
        with _STATE_LOCK:
            if self._api is None:
                self._api = load_binding()
            api = self._api
            transaction = None
            try:
                transaction = api.ProtectedStateTransaction(str(self.keys.root))
                yield api, transaction
            except api.BindingError.Backpressure:
                raise EndpointAlreadyRunning("endpoint identity is already owned by another process") from None
            except api.BindingError.StorageCapacity:
                raise ProtectedTransportStateUnavailable("protected transport state exceeds its bound") from None
            except (api.BindingError.StorageUnavailable, api.BindingError.InvalidInput, api.BindingError.Closed):
                raise ProtectedTransportStateUnavailable("protected transport state is corrupt, lost or unavailable") from None
            finally:
                if transaction is not None:
                    transaction.abort()

    @staticmethod
    def _read(api, transaction, key):
        try:
            return api.application_json.loads(transaction.read(key), _MAX_STATE_BYTES)
        except ValueError:
            raise ProtectedTransportStateUnavailable("protected transport state is corrupt or unavailable") from None

    @staticmethod
    def _encode(api, value):
        try:
            return api.application_json.dumps(value, _MAX_STATE_BYTES // 2)
        except ValueError:
            raise ProtectedTransportStateUnavailable("protected transport state is invalid or exceeds its bound") from None

    def read(self, *, default_factory: Callable[[], Any]) -> Any:
        with self._transaction() as (api, transaction):
            if not transaction.has_state():
                return default_factory()
            return self._read(api, transaction, self._key(create=False))

    def transaction(self, update: Callable[[Any], tuple[Any, _Result]], *, default_factory: Callable[[], Any]) -> _Result:
        """Return authority only after the native owner durably commits it."""
        with self._transaction() as (api, transaction):
            existing = transaction.has_state()
            key = self._key(create=False) if existing else None
            before = self._read(api, transaction, key) if existing else default_factory()
            baseline = self._encode(api, before) if transaction.baseline_required() else b""
            value, result = update(before)
            plaintext = self._encode(api, value)
            transaction.commit(key if existing else self._key(create=True), plaintext, baseline)
            return result

    @staticmethod
    def _command(api, command):
        try:
            return api.application_json.dumps(command, 512 * 1024)
        except (ValueError, TypeError):
            raise SessionDenied("invalid authorization command") from None

    def query_authorization(self, command, *, now_ms: Callable[[], int]):
        with self._transaction() as (api, transaction), authorization_errors(api):
            key = self._key(create=False) if transaction.has_state() else None
            return transaction.query_authorization(key, self._command(api, command), now_ms())

    def update_authorization(self, command, *, now_ms: Callable[[], int]):
        with self._transaction() as (api, transaction), authorization_errors(api):
            key = self._key(create=False) if transaction.has_state() else None
            prepared = transaction.prepare_authorization(key, self._command(api, command), now_ms())
            try:
                return prepared.commit(key if key is not None else self._key(create=True))
            finally:
                prepared.abort()

    def read_client_snapshot(self):
        with self._transaction() as (api, transaction), authorization_errors(api):
            key = self._key(create=False) if transaction.has_state() else None
            return transaction.read_client_snapshot(key)

    def compare_client_snapshot(self, expected, updated, *, endpoint_id=None, core_device_id=None):
        with self._transaction() as (api, transaction), authorization_errors(api):
            key = self._key(create=False) if transaction.has_state() else None
            prepared = transaction.prepare_client_compare(key, expected, updated, endpoint_id, core_device_id)
            try:
                prepared.commit(key if key is not None else self._key(create=True))
            finally:
                prepared.abort()

    def close(self) -> None:
        self._password = None
