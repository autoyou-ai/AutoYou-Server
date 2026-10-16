# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Platform records and key adapters for the shared native grant policy."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from shared.iroh_binding import load_binding
from shared.iroh_state_store import ProtectedTransportState, authorization_errors
from shared.session_transport import SessionBinding, SessionDenied, TransportKind


@lru_cache(maxsize=1)
def _native_api():
    return load_binding()


@dataclass(frozen=True)
class PairedEndpoint:
    endpoint_id: str
    device_id: str
    owner_key: str
    canonical_user_id: str
    conversation_key: str
    origin_transport: str
    origin_sender_id: str
    pairing_mode: str
    device_ownership: str
    authorization_epoch: int
    expires_at_ms: int
    scopes: frozenset[str]

    def validate(self) -> None:
        api = _native_api()
        with authorization_errors(api):
            try:
                encoded = api.application_json.dumps(_grant_payload(self), 64 * 1024)
            except (ValueError, TypeError):
                raise SessionDenied("invalid verified pairing identity") from None
            api.validate_endpoint_grant(encoded)


def _grant_payload(grant):
    value = {name: getattr(grant, name) for name in PairedEndpoint.__dataclass_fields__}
    try:
        value["scopes"] = sorted(grant.scopes)
    except TypeError:
        raise SessionDenied("invalid verified session scope") from None
    return value


def _grant_from_record(record):
    if record is None:
        return None
    value = {name: getattr(record, name) for name in PairedEndpoint.__dataclass_fields__}
    value["scopes"] = frozenset(record.scopes)
    return PairedEndpoint(**value)


def _binding_from_peer(value, transport_id):
    grant = _grant_from_record(value.grant)
    return SessionBinding(grant.endpoint_id, grant.device_id, grant.owner_key, grant.canonical_user_id,
        grant.conversation_key, transport_id, value.generation, grant.authorization_epoch,
        grant.expires_at_ms, grant.scopes, TransportKind.IROH)


class EndpointGrantRegistry:
    """Verified pairing supplies identity; Rust owns policy and protected commits."""
    def __init__(self, store: ProtectedTransportState, *, now_ms: Callable[[], int]) -> None:
        self.store, self.now_ms = store, now_ms

    @staticmethod
    def _empty() -> dict:
        return {"schema": 1, "devices": {}}

    def _query(self, operation, **values):
        return self.store.query_authorization(dict(server=dict(query=dict(operation=operation, **values))), now_ms=self.now_ms)

    def _update(self, operation, **values):
        return self.store.update_authorization(dict(server=dict(update=dict(operation=operation, **values))), now_ms=self.now_ms)

    def register(self, grant: PairedEndpoint, *, core_device_id: str | None = None) -> None:
        self._update("register", grant=_grant_payload(grant), core_device_id=core_device_id)

    def authorization_epoch(self, device_id: str) -> int:
        return self._query("epoch", device_id=device_id).epoch

    def pairing_identity_for_endpoint(self, endpoint_id: str) -> PairedEndpoint | None:
        return _grant_from_record(self._query("identity", endpoint_id=endpoint_id).grant)

    def authority_devices(self, devices: set[str] | None = None) -> tuple[tuple[str, str], ...]:
        value = self._query("authority_devices", devices=None if devices is None else sorted(devices))
        return tuple((row.device_id, row.endpoint_id) for row in value.devices)

    def authority_device(self, endpoint_id: str) -> str | None:
        return self._query("authority_device", endpoint_id=endpoint_id).device_id

    def revoke_origin(self, origin: str, *, core_device_id: str | None = None) -> tuple[tuple[str, int], ...]:
        value = self._update("revoke_origin", origin=origin, core_device_id=core_device_id)
        return tuple((row.device_id, row.authorization_epoch) for row in value.devices)

    def binding_for_connection(self, endpoint_id: str, transport_id: str) -> SessionBinding:
        return _binding_from_peer(self._update("binding", endpoint_id=endpoint_id, transport_id=transport_id), transport_id)

    def grant_for_endpoint(self, endpoint_id: str) -> PairedEndpoint:
        return _grant_from_record(self._query("peer", endpoint_id=endpoint_id).grant)

    def generation_floor(self, endpoint_id: str) -> int:
        return self._query("peer", endpoint_id=endpoint_id).generation

    def binding_for_remote_generation(self, endpoint_id: str, transport_id: str, *, generation: int,
                                      authorization_epoch: int, expires_at_ms: int,
                                      scopes: frozenset[str]) -> SessionBinding:
        value = self._update("remote_binding", endpoint_id=endpoint_id, transport_id=transport_id,
            generation=generation, authorization_epoch=authorization_epoch, expires_at_ms=expires_at_ms, scopes=sorted(scopes))
        return _binding_from_peer(value, transport_id)

    def revoke(self, device_id: str, *, authorization_epoch: int) -> bool:
        if type(authorization_epoch) is not int or not 0 <= authorization_epoch < 2**64:
            raise ValueError("invalid revocation epoch")
        return self._update("revoke", device_id=device_id, authorization_epoch=authorization_epoch).changed
