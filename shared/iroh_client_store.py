# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Platform key adapter for native protected client authorization transactions."""
from __future__ import annotations

from typing import Any, Callable

from shared.iroh_grants import PairedEndpoint, _grant_payload, _grant_from_record, _binding_from_peer
from shared.iroh_state_store import ProtectedTransportState
from shared.session_transport import SessionBinding


class ClientGrantRegistry:
    def __init__(self, store: ProtectedTransportState, *, api: Any, now_ms: Callable[[], int]) -> None:
        self.store, self.api, self.now_ms = store, api, now_ms
        self.store._api = api

    @staticmethod
    def _empty() -> dict:
        return {"schema": 2, "devices": {}}

    def _peer(self, endpoint_id):
        return self.store.query_authorization(dict(client=dict(endpoint_id=endpoint_id)), now_ms=self.now_ms)

    def _update(self, operation, **values):
        return self.store.update_authorization(dict(client=dict(update=dict(operation=operation, **values))), now_ms=self.now_ms)

    def register(self, grant: PairedEndpoint, *, core_device_id: str | None = None) -> None:
        self._update("register", grant=_grant_payload(grant), core_device_id=core_device_id)

    def grant_for_endpoint(self, endpoint_id: str) -> PairedEndpoint:
        return _grant_from_record(self._peer(endpoint_id).grant)

    def generation_floor(self, endpoint_id: str) -> int:
        return self._peer(endpoint_id).generation

    def revoke(self, device_id: str, *, authorization_epoch: int) -> bool:
        return self._update("revoke", device_id=device_id, authorization_epoch=authorization_epoch).changed

    def deny_core_endpoint(self, endpoint_id: str) -> None:
        self._update("deny_core", endpoint_id=endpoint_id)

    def associate_core_endpoint(self, endpoint_id: str, device: str) -> None:
        self._update("associate_core", endpoint_id=endpoint_id, device_id=device)

    def core_device_for_endpoint(self, endpoint_id: str) -> str | None:
        return self._peer(endpoint_id).core_device_id

    def binding_for_remote_generation(self, endpoint_id: str, transport_id: str, *, generation: int,
                                     authorization_epoch: int, expires_at_ms: int,
                                     scopes: frozenset[str]) -> SessionBinding:
        value = self._update("remote_binding", endpoint_id=endpoint_id, transport_id=transport_id,
            generation=generation, authorization_epoch=authorization_epoch, expires_at_ms=expires_at_ms, scopes=sorted(scopes))
        return _binding_from_peer(value, transport_id)
