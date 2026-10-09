# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Device-to-endpoint association after the existing pairing authority succeeds."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Callable

from shared.iroh_state_store import ProtectedTransportState, ProtectedTransportStateUnavailable
from shared.session_transport import SessionBinding, SessionDenied, TransportKind


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
        SessionBinding(self.endpoint_id, self.device_id, self.owner_key, self.canonical_user_id,
            self.conversation_key, "verified-enrollment", 1, self.authorization_epoch,
            self.expires_at_ms, self.scopes, TransportKind.IROH).validate()
        if self.device_ownership not in {"own", "shared"} or any(
            not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512
            for value in (self.origin_transport, self.origin_sender_id, self.pairing_mode)
        ):
            raise SessionDenied("invalid verified pairing origin")


class EndpointGrantRegistry:
    """The host provides PairedEndpoint; wire dictionaries never confer roles.

    Generations, revocations, expiry and owner associations survive process
    death. Corruption does not produce an empty authorization database.
    """

    def __init__(self, store: ProtectedTransportState, *, now_ms: Callable[[], int]) -> None:
        self.store, self.now_ms = store, now_ms

    @staticmethod
    def _empty() -> dict:
        return {"schema": 1, "devices": {}}

    @staticmethod
    def _state(value: Any) -> dict:
        try:
            if not isinstance(value, dict) or set(value) != {"schema", "devices"} or value["schema"] != 1:
                raise ValueError
            devices = value["devices"]
            if not isinstance(devices, dict) or len(devices) > 4096:
                raise ValueError
            endpoints = set()
            for device, row in devices.items():
                grant = EndpointGrantRegistry._grant(row)
                if device != grant.device_id or grant.endpoint_id in endpoints:
                    raise ValueError
                endpoints.add(grant.endpoint_id)
                if type(row["generation"]) is not int or not 0 <= row["generation"] < 2**64 or type(row["revoked"]) is not bool:
                    raise ValueError
                if row.get("core_device_id") is not None and not re.fullmatch(r"[a-z0-9]{15}",row["core_device_id"]):
                    raise ValueError
            return value
        except (ValueError, TypeError, KeyError, SessionDenied):
            raise ProtectedTransportStateUnavailable("stored endpoint grants are invalid") from None

    @staticmethod
    def _grant(row: dict) -> PairedEndpoint:
        raw = row["grant"]
        if not isinstance(raw, dict) or not isinstance(raw.get("scopes"), list) or any(not isinstance(s, str) for s in raw["scopes"]):
            raise ValueError
        grant = PairedEndpoint(**dict(raw, scopes=frozenset(raw["scopes"])))
        grant.validate()
        return grant

    def register(self, grant: PairedEndpoint, *, core_device_id: str | None = None) -> None:
        grant.validate()
        if core_device_id is not None and (grant.origin_transport != "cloud" or not re.fullmatch(r"[a-z0-9]{15}",core_device_id)):
            raise SessionDenied("invalid verified Core grant device")
        if grant.expires_at_ms <= self.now_ms():
            raise SessionDenied("pairing grant has expired")

        def update(value: dict) -> tuple[dict, None]:
            state = self._state(value)
            devices = state["devices"]
            existing = devices.get(grant.device_id)
            if existing:
                old = self._grant(existing)
                if grant.endpoint_id != old.endpoint_id or grant.owner_key != old.owner_key or \
                        grant.canonical_user_id != old.canonical_user_id or grant.conversation_key != old.conversation_key:
                    raise SessionDenied("endpoint association requires authorized recovery")
                if grant.authorization_epoch < old.authorization_epoch or (existing["revoked"] and grant.authorization_epoch <= old.authorization_epoch):
                    raise SessionDenied("stale authorization epoch")
            elif len(devices) >= 4096:
                raise SessionDenied("device grant capacity has been reached")
            if any(device != grant.device_id and self._grant(row).endpoint_id == grant.endpoint_id for device, row in devices.items()):
                raise SessionDenied("endpoint already belongs to another device")
            raw = asdict(grant)
            raw["scopes"] = sorted(grant.scopes)
            devices[grant.device_id] = {"grant": raw, "generation": existing["generation"] if existing else 0, "revoked": False}
            associated = core_device_id or (existing or {}).get("core_device_id")
            if associated is not None:
                if existing and existing.get("core_device_id") not in {None, associated}:
                    raise SessionDenied("Core grant device requires authorized recovery")
                devices[grant.device_id]["core_device_id"] = associated
            return state, None

        self.store.transaction(update, default_factory=self._empty)

    def authorization_epoch(self, device_id: str) -> int:
        state = self._state(self.store.read(default_factory=self._empty))
        row = state["devices"].get(device_id)
        return self._grant(row).authorization_epoch if row is not None else 0

    def authority_devices(self, devices: set[str] | None = None) -> tuple[tuple[str,str], ...]:
        state=self._state(self.store.read(default_factory=self._empty))
        return tuple((row["core_device_id"],self._grant(row).endpoint_id) for device,row in state["devices"].items()
                     if row.get("core_device_id") and not row["revoked"] and self._grant(row).origin_transport=="cloud"
                     and (devices is None or device in devices))

    def authority_device(self, endpoint_id: str) -> str | None:
        state = self._state(self.store.read(default_factory=self._empty))
        return next((row.get("core_device_id") for row in state["devices"].values()
                     if self._grant(row).endpoint_id == endpoint_id and not row["revoked"]), None)

    def revoke_origin(self, origin: str, *, core_device_id: str | None = None) -> tuple[tuple[str,int], ...]:
        def update(value):
            state=self._state(value);revoked=[]
            for device,row in state["devices"].items():
                grant=self._grant(row)
                if row["revoked"] or grant.origin_transport!=origin or (core_device_id is not None and row.get("core_device_id")!=core_device_id):
                    continue
                epoch=grant.authorization_epoch+1
                if epoch>=2**64: raise SessionDenied("authorization epoch exhausted")
                row["grant"]["authorization_epoch"]=epoch;row["revoked"]=True;revoked.append((device,epoch))
            return state,tuple(revoked)
        return self.store.transaction(update,default_factory=self._empty)

    def binding_for_connection(self, endpoint_id: str, transport_id: str) -> SessionBinding:
        def update(value: dict) -> tuple[dict, SessionBinding]:
            state = self._state(value)
            row = next((row for row in state["devices"].values() if self._grant(row).endpoint_id == endpoint_id), None)
            if row is None or row["revoked"]:
                raise SessionDenied("endpoint has no active pairing grant")
            grant = self._grant(row)
            if grant.expires_at_ms <= self.now_ms() or row["generation"] == 2**64 - 1:
                raise SessionDenied("endpoint pairing grant has expired or requires recovery")
            row["generation"] += 1
            binding = SessionBinding(grant.endpoint_id, grant.device_id, grant.owner_key, grant.canonical_user_id,
                grant.conversation_key, transport_id, row["generation"], grant.authorization_epoch,
                grant.expires_at_ms, grant.scopes, TransportKind.IROH)
            binding.validate()
            return state, binding

        return self.store.transaction(update, default_factory=self._empty)

    def grant_for_endpoint(self, endpoint_id: str) -> PairedEndpoint:
        state = self._state(self.store.read(default_factory=self._empty))
        row = next((row for row in state["devices"].values() if self._grant(row).endpoint_id == endpoint_id), None)
        if row is None or row["revoked"]:
            raise SessionDenied("endpoint has no active pairing grant")
        grant = self._grant(row)
        if grant.expires_at_ms <= self.now_ms():
            raise SessionDenied("endpoint pairing grant has expired")
        return grant

    def generation_floor(self, endpoint_id: str) -> int:
        """Read the protected counter; an in-memory retry cannot rewind it."""
        state = self._state(self.store.read(default_factory=self._empty))
        row = next((row for row in state["devices"].values() if self._grant(row).endpoint_id == endpoint_id), None)
        if row is None or row["revoked"] or self._grant(row).expires_at_ms <= self.now_ms():
            raise SessionDenied("endpoint has no active pairing grant")
        return row["generation"]

    def binding_for_remote_generation(self, endpoint_id: str, transport_id: str, *, generation: int,
                                      authorization_epoch: int, expires_at_ms: int,
                                      scopes: frozenset[str]) -> SessionBinding:
        """Client accepts the paired server's counter without widening its grant."""
        if type(generation) is not int or not 0 < generation < 2**64:
            raise SessionDenied("invalid remote connection generation")

        def update(value: dict) -> tuple[dict, SessionBinding]:
            state = self._state(value)
            row = next((row for row in state["devices"].values() if self._grant(row).endpoint_id == endpoint_id), None)
            if row is None or row["revoked"]:
                raise SessionDenied("endpoint has no active pairing grant")
            grant = self._grant(row)
            if generation <= row["generation"] or authorization_epoch != grant.authorization_epoch or \
                    not self.now_ms() < expires_at_ms <= grant.expires_at_ms or not scopes <= grant.scopes:
                raise SessionDenied("remote session is stale or outside its paired scope")
            row["generation"] = generation
            binding = SessionBinding(grant.endpoint_id, grant.device_id, grant.owner_key, grant.canonical_user_id,
                grant.conversation_key, transport_id, generation, authorization_epoch, expires_at_ms, scopes, TransportKind.IROH)
            binding.validate()
            return state, binding

        return self.store.transaction(update, default_factory=self._empty)

    def revoke(self, device_id: str, *, authorization_epoch: int) -> bool:
        if type(authorization_epoch) is not int or not 0 <= authorization_epoch < 2**64:
            raise ValueError("invalid revocation epoch")

        def update(value: dict) -> tuple[dict, bool]:
            state = self._state(value)
            row = state["devices"].get(device_id)
            if row is None:
                return state, False
            if authorization_epoch <= self._grant(row).authorization_epoch:
                raise SessionDenied("revocation epoch must advance")
            row["grant"]["authorization_epoch"] = authorization_epoch
            row["revoked"] = True
            return state, True

        return self.store.transaction(update, default_factory=self._empty)
