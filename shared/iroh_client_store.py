# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Protected Python storage adapter for the shared Rust client grant codec."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
from typing import Any, Callable

from shared.iroh_grants import PairedEndpoint
from shared.iroh_state_store import ProtectedTransportState, ProtectedTransportStateUnavailable
from shared.session_transport import SessionBinding, SessionDenied, TransportKind


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _grant_json(grant: PairedEndpoint) -> str:
    return _json(dict(asdict(grant), scopes=sorted(grant.scopes))).decode("utf-8")


class ClientGrantRegistry:
    """Rust validates associations and counters; the host commits atomically.

    The server continues to own its business grant registry. Client adapters in
    all languages use the same Rust codec for protected enrollment state.
    """
    def __init__(self, store: ProtectedTransportState, *, api: Any, now_ms: Callable[[], int]) -> None:
        self.store, self.api, self.now_ms = store, api, now_ms

    def _empty(self) -> dict:
        return json.loads(bytes(self.api.empty_client_store()))

    def _peer(self, value: Any, endpoint_id: str) -> Any:
        try:
            return self.api.load_client_peer(_json(value), endpoint_id, self.now_ms())
        except self.api.BindingError.InvalidInput:
            raise ProtectedTransportStateUnavailable("stored client grants are invalid") from None
        except self.api.BindingError.PermissionDenied:
            raise SessionDenied("endpoint has no active pairing grant") from None

    @staticmethod
    def _grant(peer: Any) -> PairedEndpoint:
        value = json.loads(peer.grant_json)
        return PairedEndpoint(**dict(value, scopes=frozenset(value["scopes"])))

    def register(self, grant: PairedEndpoint, *, core_device_id: str | None = None) -> None:
        def update(value: Any) -> tuple[dict, None]:
            try:
                state = self.api.register_client_grant(_json(value), _grant_json(grant), self.now_ms())
                state = self.api.associate_core_client_peer(state, grant.endpoint_id, core_device_id)
            except self.api.BindingError.InvalidInput:
                raise ProtectedTransportStateUnavailable("stored client grants are invalid") from None
            except self.api.BindingError.PermissionDenied:
                raise SessionDenied("client grant registration was rejected") from None
            return json.loads(bytes(state)), None
        self.store.transaction(update, default_factory=self._empty)

    def grant_for_endpoint(self, endpoint_id: str) -> PairedEndpoint:
        return self._grant(self._peer(self.store.read(default_factory=self._empty), endpoint_id))

    def generation_floor(self, endpoint_id: str) -> int:
        return self._peer(self.store.read(default_factory=self._empty), endpoint_id).generation_floor

    def deny_core_endpoint(self, endpoint_id: str) -> None:
        def update(value):
            return json.loads(bytes(self.api.deny_core_client_peer(_json(value),endpoint_id))), None
        self.store.transaction(update,default_factory=self._empty)

    def associate_core_endpoint(self, endpoint_id: str, device: str) -> None:
        def update(value):
            return json.loads(bytes(self.api.associate_core_client_peer(_json(value),endpoint_id,device))),None
        self.store.transaction(update,default_factory=self._empty)

    def core_device_for_endpoint(self, endpoint_id: str) -> str | None:
        return getattr(self._peer(self.store.read(default_factory=self._empty),endpoint_id),"core_device_id",None)

    def binding_for_remote_generation(self, endpoint_id: str, transport_id: str, *, generation: int,
                                     authorization_epoch: int, expires_at_ms: int,
                                     scopes: frozenset[str]) -> SessionBinding:
        def update(value: Any) -> tuple[dict, SessionBinding]:
            grant = self._grant(self._peer(value, endpoint_id))
            admitted = replace(grant, authorization_epoch=authorization_epoch, expires_at_ms=expires_at_ms, scopes=scopes)
            try:
                state = self.api.admit_client_generation(_json(value), _grant_json(admitted), generation, self.now_ms())
            except (self.api.BindingError.PermissionDenied, self.api.BindingError.InvalidInput):
                raise SessionDenied("remote session is stale or outside its paired scope") from None
            binding = SessionBinding(endpoint_id, grant.device_id, grant.owner_key, grant.canonical_user_id,
                grant.conversation_key, transport_id, generation, authorization_epoch, expires_at_ms, scopes, TransportKind.IROH)
            binding.validate()
            return json.loads(bytes(state)), binding
        return self.store.transaction(update, default_factory=self._empty)
