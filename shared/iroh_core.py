# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Installation-owned Core routing; existing pairing still owns every app grant."""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

from shared.iroh_delivery import _joined_disk
from shared.session_transport import SessionDenied


class CoreAccessDenied(SessionDenied):
    def __init__(self, status: int):
        self.status = status
        super().__init__("Core transport authority was denied")


class CoreRelayDenied(SessionDenied):
    pass


class CoreRoutingOwner:
    def __init__(self, *, api: Any, endpoint: Any, policy: dict, state: Any,
                 issuer: str, public_key: str, owner_id: str, device_id: str,
                 request: Callable, is_current: Callable[[], bool], on_denied: Callable | None = None,
                 enroll: bool = False,
                 check_authority: Callable | None = None,
                 now_ms: Callable[[], int] = lambda: int(time.time() * 1000)) -> None:
        url = urlparse(issuer)
        if (not policy.get("brokered_relays") or not re.fullmatch(r"[0-9a-f]{64}", public_key)
                or any(not re.fullmatch(r"[a-z0-9]{15}", value) for value in (owner_id, device_id))
                or url.scheme not in {"https", "http"} or not url.netloc or url.path or url.query or url.fragment
                or url.username or url.password):
            raise ValueError("invalid pinned Core transport context")
        if url.scheme != "https":
            import os
            if not os.getenv("AUTOYOU_TEST_ROOT") or url.hostname not in {"127.0.0.1", "::1", "localhost"}:
                raise ValueError("Core transport requires HTTPS")
        self.api, self.endpoint, self.state = api, endpoint, state
        self.policy = json.dumps(policy, separators=(",", ":"), allow_nan=False)
        self.relay_only = policy.get("relay_only") is True
        self.issuer, self.public_key, self.owner_id, self.device_id = issuer, public_key, owner_id, device_id
        self.request, self.is_current, self.on_denied, self.now_ms = request, is_current, on_denied, now_ms
        self._revision = 0
        self._closed = False
        self._gate = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self.status = "idle"
        self._enroll_requested = enroll
        self.check_authority = check_authority

    def _current(self, revision: int) -> None:
        if self._closed or revision != self._revision or not self.is_current():
            raise CoreAccessDenied(401)

    async def _request(self, method: str, path: str, payload: dict | None, revision: int) -> dict:
        self._current(revision)
        try:
            result = await self.request(method, path, payload)
        except CoreAccessDenied as denial:
            if path == "/v1/iroh/relay-credentials" and denial.status in {402, 403}:
                raise CoreRelayDenied("Core relay allowance was denied") from None
            raise
        self._current(revision)
        if not isinstance(result, dict):
            raise SessionDenied("invalid Core response")
        return result

    def _empty(self) -> dict:
        return json.loads(bytes(self.api.empty_core_routing_store()))

    async def _accept(self, envelope: dict, *, device: str, owner: str | None, endpoint: str, relay: bool = False):
        def update(value):
            function = self.api.accept_core_relay_configuration if relay else self.api.accept_core_routing_record
            args = [json.dumps(value, separators=(",", ":")).encode(),
                    json.dumps(envelope, separators=(",", ":")), self.public_key, self.issuer]
            if owner is None and not relay:
                function = self.api.accept_core_device_routing_record
            else:
                args.append(owner)
            record = function(*args, device, endpoint, self.now_ms(), self.policy)
            return json.loads(bytes(record.protected_store)), record
        return await _joined_disk(lambda: self.state.transaction(update, default_factory=self._empty))

    async def _proof(self, revision: int, *, routes: list[str] | None = None) -> dict:
        endpoint = self.endpoint.endpoint_info().endpoint_id
        payload = {"device_id": self.device_id, "endpoint_id": endpoint}
        if routes is not None:
            payload.update(purpose="routing", relay_urls=routes)
        challenge = await self._request("POST", "/v1/iroh/endpoints/challenge", payload, revision)
        signature = await _joined_disk(self.endpoint.sign_core_proof, challenge["payload"],
                                       self.issuer, self.owner_id, self.device_id)
        self._current(revision)
        return {"device_id": self.device_id, "payload": challenge["payload"], "signature": signature}

    async def refresh(self) -> None:
        async with self._gate:
            revision = self._revision
            self._current(revision)
            generation = self.endpoint.core_relay_generation()
            endpoint = self.endpoint.endpoint_info().endpoint_id
            identity = await self._request("GET", f"/v1/iroh/endpoints/{self.device_id}/ownership", None, revision)
            if identity.get("active") is not True:
                if identity.get("epoch", 0) != 0 and not self._enroll_requested:
                    raise CoreAccessDenied(403)
                proof = await self._proof(revision)
                await self._request("POST", "/v1/iroh/endpoints/associate", proof, revision)
                self._enroll_requested = False
            elif identity.get("endpoint_id") != endpoint:
                raise SessionDenied("Core endpoint association requires explicit recovery")
            envelope = await self._request("POST", "/v1/iroh/relay-credentials", {"device_id": self.device_id}, revision)
            credentials = await self._accept(envelope, device=self.device_id, owner=self.owner_id, endpoint=endpoint, relay=True)
            self._current(revision)
            await _joined_disk(self.endpoint.set_core_relays, credentials.credentials_json, credentials.expires_at_ms, generation)
            self._current(revision)
            routes = self.endpoint.current_relay_urls()
            if routes or identity.get("relay_urls"):
                proof = await self._proof(revision, routes=routes)
                record = await self._request("POST", f"/v1/iroh/endpoints/{self.device_id}/routing", proof, revision)
                await self._accept(record, device=self.device_id, owner=self.owner_id, endpoint=endpoint)
            self.status = "ready"

    async def lookup(self, *, device_id: str, endpoint_id: str, owner_id: str | None = None) -> str:
        """The caller supplies an already paired endpoint and canonical roster IDs."""
        record = await self.device_record(device_id=device_id,endpoint_id=endpoint_id,owner_id=owner_id)
        if not record.ticket:
            raise ConnectionError("Core has no fresh relay routing for this endpoint")
        return record.ticket

    async def wait_for_routes(self) -> None:
        revision = self._revision
        deadline = asyncio.get_running_loop().time() + 5
        while not self.endpoint.current_relay_urls():
            self._current(revision)
            if asyncio.get_running_loop().time() >= deadline:
                if self.relay_only:
                    raise ConnectionError("The approved Core relay is unavailable")
                return
            await asyncio.sleep(0.1)
        await self.refresh()

    async def device_record(self, *, device_id: str, endpoint_id: str, owner_id: str | None = None):
        if not re.fullmatch(r"[a-z0-9]{15}", device_id):
            raise SessionDenied("invalid Core device")
        revision = self._revision
        envelope = await self._request("GET", f"/v1/iroh/endpoints/{device_id}", None, revision)
        record = await self._accept(envelope, device=device_id, owner=owner_id, endpoint=endpoint_id)
        self._current(revision)
        return record

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._closed:
            raise SessionDenied("Core routing owner is closed")
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="iroh-core-routing")

    async def _run(self) -> None:
        while not self._closed:
            self._wake.clear()
            try:
                try:
                    await self.refresh()
                except CoreRelayDenied:
                    self.status = "relay_denied"
                    await self.invalidate()
                if self.check_authority:
                    await self.check_authority()
            except CoreAccessDenied:
                if self._closed or not self.is_current():
                    break
                self.status = "denied"
                await self.invalidate()
                if self.on_denied:
                    await self.on_denied()
            except (OSError, TimeoutError, ConnectionError):
                self.status = "offline"  # Existing verified credentials expire in the native worker.
            except Exception:
                self.status = "unavailable"
                await self.invalidate()
            if self._closed:
                break
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=30)
            except TimeoutError:
                pass

    async def invalidate(self) -> None:
        self._revision += 1
        await _joined_disk(self.endpoint.clear_core_relays)

    async def close(self) -> None:
        self._closed = True
        try:
            await self.invalidate()
        finally:
            task, self._task = self._task, None
            if task and task is not asyncio.current_task():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.state.close()
