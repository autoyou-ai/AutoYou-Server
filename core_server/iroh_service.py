# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Server-owned Iroh lifecycle and post-proof application attachment."""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any

from core_server.session_business import SessionBusinessAdapter
from shared.iroh_admission import PairedEndpointAdmission
from shared.iroh_grants import EndpointGrantRegistry, PairedEndpoint
from shared.iroh_keys import EndpointKeys
from shared.iroh_pairing import VerifiedPairingRedemption, PAIR_ALPN
from shared.iroh_runtime import IrohSessionRuntime
from shared.iroh_state_store import ProtectedTransportState
from shared.session_transport import SessionDenied, TransportPolicy


@dataclass(frozen=True)
class VerifiedPairingOrigin:
    """Created by a successful existing proof entry point, never decoded wire."""
    transport: str
    sender_id: str
    pairing_mode: str
    device_ownership: str


class IrohServerService:
    def __init__(self, *, runtime: Any, grants: EndpointGrantRegistry,
                 capabilities: dict[str, Any], grant_seconds: int = 86400) -> None:
        if type(grant_seconds) is not int or not 60 <= grant_seconds <= 86400:
            raise ValueError("invalid transport grant duration")
        capabilities = dict(capabilities, delivery=capabilities.get("chat") is True)
        if capabilities.get("files") is True:
            from shared.iroh_files import file_limits
            capabilities["file_limits"] = file_limits()
        self.runtime, self.grants, self.capabilities = runtime, grants, capabilities
        self.grant_seconds = grant_seconds
        self.business = SessionBusinessAdapter(runtime=runtime, engine=runtime.WEBRTC, grants=grants)
        self.admission = PairedEndpointAdmission(grants=grants, role="server", capabilities=capabilities,
            on_prepared=self.business.prepared, on_ready=self.business.ready)
        self.pairing = VerifiedPairingRedemption(grants=grants, capabilities=capabilities)
        self.endpoint: IrohSessionRuntime | None = None
        self.files: Any = None
        self._lifecycle_lock = asyncio.Lock()

    async def start(self, *, policy: dict[str, Any], unlocked_password: str | None,
                    api: Any = None, keys: Any = None, file_keys: Any = None, delivery_keys: Any = None) -> None:
        async with self._lifecycle_lock:
            if self.endpoint is not None:
                return
            if self.capabilities.get("chat") is True and self.business.delivery is None:
                from shared.iroh_delivery import load_delivery_service
                self.business.delivery = await load_delivery_service(delivery_keys or EndpointKeys(role="server", purpose="delivery"),
                    unlocked_password=unlocked_password)
            if self.capabilities.get("files") is True and self.files is None:
                from shared.iroh_files import load_file_service
                self.files = await load_file_service(file_keys or EndpointKeys(role="server", purpose="resume"),
                    unlocked_password=unlocked_password)
                self.business.attach_stream_handler(7, self.files)
            self.endpoint = await IrohSessionRuntime.start(role="server", policy=policy,
                keys=keys or EndpointKeys(role="server"), enroll=True,
                unlocked_password=unlocked_password, api=api, on_connected=self.connected,
                on_enrollment=self.enrollment, on_closed=self.closed, on_binary_frame=self.business.binary)

    async def connected(self, transport: IrohSessionRuntime, context: Any) -> None:
        owner = self.pairing if context.protocol == PAIR_ALPN else self.admission
        await owner.connected(transport, context)

    async def enrollment(self, transport: IrohSessionRuntime, context: Any, payload: bytes) -> None:
        owner = self.pairing if context.protocol == PAIR_ALPN else self.admission
        await owner.enrollment(transport, context, payload)

    async def closed(self, context: Any, binding: Any, user_requested: bool) -> None:
        await self.pairing.closed(context, binding, user_requested)
        await self.admission.closed(context, binding, user_requested)
        await self.business.closed(context, binding, user_requested)

    async def issue_after_verified_pairing(self, offer: dict[str, Any], *,
                                         origin: VerifiedPairingOrigin, raw_session_id: str) -> dict[str, Any]:
        if self.endpoint is None or not isinstance(origin, VerifiedPairingOrigin):
            raise SessionDenied("transport pairing is not ready")
        if not isinstance(offer, dict) or offer.get("transport") != "iroh" or \
                type(offer.get("version")) is not int or offer["version"] != 1:
            raise SessionDenied("unsupported transport offer")
        endpoint_id = offer.get("endpoint_id")
        self.endpoint.api.validate_endpoint_id(endpoint_id)
        identity = self.runtime.bind_transport_chat_owner(origin.transport, origin.sender_id,
            raw_session_id=raw_session_id, pairing_mode=origin.pairing_mode)
        self.runtime.WEBRTC.remember_device_ownership(identity, origin.device_ownership)
        # The current application/session authority supplies the base owner.
        # Selection of a conversation thread remains the history service's job.
        device_id = "iroh-device-" + hashlib.sha256(endpoint_id.encode("utf-8")).hexdigest()
        scopes = frozenset({"chat", "browser", "pairing"})
        if self.capabilities.get("files") is True and self.files is not None:
            scopes |= {"files"}
        grant = PairedEndpoint(endpoint_id=endpoint_id, device_id=device_id,
            owner_key=identity.owner_key, canonical_user_id=identity.canonical_user_id,
            conversation_key=identity.canonical_session_id, origin_transport=origin.transport,
            origin_sender_id=origin.sender_id, pairing_mode=origin.pairing_mode,
            device_ownership=origin.device_ownership, authorization_epoch=1,
            expires_at_ms=int(time.time() * 1000) + self.grant_seconds * 1000, scopes=scopes)
        descriptor = self.pairing.issue_after_verified_proof(self.endpoint, grant)
        metadata = self.runtime._build_client_session_identity_payload(identity, pairing_mode=origin.pairing_mode)
        return dict(metadata, transport="iroh", iroh=descriptor, session_id=raw_session_id)

    async def revoke_device(self, device_id: str, *, authorization_epoch: int) -> bool:
        self.pairing.cancel_device(device_id)
        if self.endpoint is None:
            raise SessionDenied("transport service is not running")
        return await self.admission.revoke_device(self.endpoint, device_id,
            authorization_epoch=authorization_epoch)

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            endpoint, self.endpoint = self.endpoint, None
            self.pairing.clear()
            if endpoint is not None:
                await endpoint.close()


def _server_lifecycle_lock(runtime: Any) -> asyncio.Lock:
    # Creation has no await: concurrent startup/shutdown on this server's loop
    # always receives the same lock, including before a service is published.
    lock = getattr(runtime.STATE, "iroh_transport_lock", None)
    if lock is None:
        lock = runtime.STATE.iroh_transport_lock = asyncio.Lock()
    return lock


async def start_server_transport(runtime: Any) -> None:
    async with _server_lifecycle_lock(runtime):
        await _start_server_transport_locked(runtime)


async def _start_server_transport_locked(runtime: Any) -> None:
    config = (runtime.STATE.config or {}).get("session_transport", {})
    if not isinstance(config, dict):
        raise ValueError("invalid session transport configuration")
    mode = TransportPolicy(config.get("mode", "legacy"))
    if mode == TransportPolicy.LEGACY or getattr(runtime.STATE, "iroh_service", None) is not None:
        return
    if runtime._config_write_block_reason() is not None:
        raise SessionDenied("server configuration must be unlocked before transport startup")
    policy = config.get("iroh")
    if not isinstance(policy, dict):
        raise ValueError("Iroh requires an explicit endpoint network policy")
    password = runtime.STATE.config_unlock_password or runtime.STATE.server_password
    grants = EndpointGrantRegistry(ProtectedTransportState(keys=EndpointKeys(role="server", purpose="grants"),
        unlocked_password=password), now_ms=lambda: int(time.time() * 1000))
    service = IrohServerService(runtime=runtime, grants=grants,
        capabilities={"transport": "iroh", "wire_version": 1, "chat": True, "browser": True,
                       "pairing": True, "files": True, "media": False},
        grant_seconds=config.get("grant_seconds", 86400))
    try:
        await service.start(policy=policy, unlocked_password=password)
    except BaseException:
        await asyncio.shield(service.stop())
        raise
    runtime.STATE.iroh_service = service


async def stop_server_transport(runtime: Any) -> None:
    async with _server_lifecycle_lock(runtime):
        service = getattr(runtime.STATE, "iroh_service", None)
        runtime.STATE.iroh_service = None
        if service is not None:
            await service.stop()


async def delete_transport_conversation_history(runtime: Any, identity: Any, delete: Any) -> Any:
    """Fence queued native prompts even when the endpoint/transport is stopped."""
    async with _server_lifecycle_lock(runtime):
        service = getattr(runtime.STATE, "iroh_service", None)
        if service is not None and service.endpoint is not None and service.business.delivery is not None:
            return await service.business.delivery.delete_history(api=service.endpoint.api,
                endpoint_id=service.endpoint.endpoint_info.endpoint_id, identity=identity, delete=delete)
        from shared.iroh_delivery import IrohDeliveryService, _joined_disk
        from shared.iroh_binding import load_binding
        keys = EndpointKeys(role="server", purpose="delivery")
        if not keys.witness.exists() and not keys.password_store.exists() and not (keys.root / "operations").exists():
            return await delete()
        # Installed signed bindings and existing protected keys are required.
        # Maintenance never enrolls/rotates keys or opens a network endpoint.
        api = load_binding()
        password = runtime.STATE.config_unlock_password or runtime.STATE.server_password
        key = await _joined_disk(lambda: keys.load(enroll=False, unlocked_password=password))
        identity_keys = EndpointKeys(role="server")
        endpoint_id = await _joined_disk(lambda: api.endpoint_id_from_key(identity_keys.load(enroll=False, unlocked_password=password)))
        delivery = IrohDeliveryService(root=keys.root / "operations", key=key)
        return await delivery.delete_history(api=api, endpoint_id=endpoint_id, identity=identity, delete=delete)
