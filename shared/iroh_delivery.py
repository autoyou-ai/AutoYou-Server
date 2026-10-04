# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Owned prompt admission and encrypted recovery through the shared Rust store."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
import unicodedata
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from shared.datachannel_manager import MessageType
from shared.session_transport import SessionDenied


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


async def _joined_disk(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise cancellation


async def load_delivery_service(keys, *, unlocked_password=None, on_state=None):
    if keys.purpose != "delivery":
        raise ValueError("application delivery requires its own protected key")
    key = await _joined_disk(lambda: keys.load(enroll=True, unlocked_password=unlocked_password))
    return IrohDeliveryService(root=keys.root / "operations", key=key, on_state=on_state)


async def delete_local_pending_history(conversation_key=None, *, prompt_ids=None, keys=None, endpoint_keys=None, api=None):
    """Cancel this installation's outboxes without dialing or creating keys."""
    if prompt_ids is not None and (not isinstance(prompt_ids, (list, tuple)) or any(
        not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128
        or any(unicodedata.category(char) == "Cc" for char in value) for value in prompt_ids)):
        raise ValueError("local history prompt identifiers are invalid")
    from shared.iroh_keys import EndpointKeys
    keys = keys or EndpointKeys(role="client", app_name="AutoYou-Iroh", purpose="delivery")
    endpoint_keys = endpoint_keys or EndpointKeys(role="client", app_name="AutoYou-Iroh")
    if keys.role != "client" or keys.purpose != "delivery" or endpoint_keys.role != "client" or endpoint_keys.purpose != "identity":
        raise ValueError("local history requires client delivery and identity keys")
    root = keys.root / "operations"
    # Native stores redirect the supplied root under AUTOYOU_TEST_ROOT. A raw
    # path existence check would incorrectly skip journals in that test root.
    if api is None and not os.environ.get("AUTOYOU_TEST_ROOT") and not await _joined_disk(root.exists):
        return
    if api is None:
        from shared.iroh_binding import load_binding
        api = load_binding()
    if not await _joined_disk(api.delivery_store_has_state, str(root.resolve())):
        return
    key = await _joined_disk(lambda: keys.load(enroll=False))
    seed = await _joined_disk(lambda: endpoint_keys.load(enroll=False))
    endpoint = api.endpoint_id_from_key(seed)
    authority = dict(local_endpoint=endpoint, remote_endpoint=endpoint, server_instance=endpoint,
        device_id="local-history-maintenance", owner_key="local-device-history", canonical_user_id="local-device-history",
        conversation_key="local-history-maintenance", authorization_epoch=1, direction="outgoing")
    store = await _joined_disk(api.ApplicationDeliveryStore, str(root.resolve()), key, _json(authority), False)
    if prompt_ids is None:
        await _joined_disk(store.delete_pending_history, conversation_key)
    else:
        # The caller holds one endpoint quiescence barrier around the complete
        # selection. A long transcript must not resume between SDK-sized batches.
        for offset in range(0, len(prompt_ids), 4096):
            await _joined_disk(store.delete_pending_prompts, prompt_ids[offset:offset + 4096])


class IrohDeliveryService:
    def __init__(self, *, root: Path, key: bytes, on_state=None):
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("protected application delivery key is unavailable")
        self.root, self._key, self.on_state = root, key, on_state
        self._managers = {}
        self._family_locks = {}

    @staticmethod
    def _family(authority):
        return (authority.owner_key, authority.canonical_user_id,
            getattr(authority, "conversation_key", None) or getattr(authority, "canonical_session_id", None))

    @asynccontextmanager
    async def admission_guard(self, authority):
        key = self._family(authority)
        if any(not isinstance(value, str) or not value or len(value) > 512 for value in key):
            raise SessionDenied("conversation delivery authority is unavailable")
        lease = self._family_locks.get(key)
        if lease is None:
            if len(self._family_locks) >= 64:
                raise SessionDenied("conversation delivery capacity is exhausted")
            lease = [asyncio.Lock(), 0]
            self._family_locks[key] = lease
        lease[1] += 1
        try:
            async with lease[0]:
                yield
        finally:
            lease[1] -= 1
            if lease[1] == 0 and self._family_locks.get(key) is lease:
                del self._family_locks[key]

    async def delete_history(self, *, api, endpoint_id, identity, delete):
        async with self.admission_guard(identity):
            owner, user, conversation = self._family(identity)
            authority = dict(local_endpoint=endpoint_id, remote_endpoint=endpoint_id,
                server_instance=endpoint_id, device_id="history-deletion", owner_key=owner,
                canonical_user_id=user, conversation_key=conversation, authorization_epoch=1,
                direction="incoming")
            store = await _joined_disk(api.ApplicationDeliveryStore, str(self.root.resolve()), self._key, _json(authority), True)
            revision = await _joined_disk(store.pause_revision, 1)
            # The same family lock surrounds durable admission and application
            # handoff. No admitted prompt can cross the deletion boundary.
            try:
                return await delete()
            finally:
                async def resume():
                    await _joined_disk(store.resume_revision, revision, 1)
                    for manager in tuple(self._managers.values()):
                        if manager.server and self._family(manager.binding) == (owner, user, conversation):
                            try:
                                await manager.ready()
                            except (SessionDenied, ConnectionError):
                                manager.channel.disconnect()
                task = asyncio.create_task(resume())
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as cancellation:
                    while not task.done():
                        try:
                            await asyncio.shield(task)
                        except asyncio.CancelledError:
                            continue
                    task.result()
                    raise cancellation

    async def prepared(self, transport, context, channel):
        binding = channel.binding
        if "chat" not in binding.scopes:
            return
        if binding.transport_id in self._managers or len(self._managers) >= 32:
            raise SessionDenied("application delivery session capacity is exhausted")
        channel.registry.check(binding, scope="chat")
        server = transport.role == "server"
        scope = dict(local_endpoint=context.local_endpoint_id, remote_endpoint=binding.endpoint_id,
            server_instance=context.local_endpoint_id if server else binding.endpoint_id,
            device_id=binding.device_id, owner_key=binding.owner_key, canonical_user_id=binding.canonical_user_id,
            conversation_key=binding.conversation_key, authorization_epoch=binding.authorization_epoch,
            direction="incoming" if server else "outgoing")
        store = await _joined_disk(channel.api.ApplicationDeliveryStore, str(self.root.resolve()), self._key, _json(scope), True)
        await _joined_disk(store.sweep, int(time.time() * 1000))
        channel.registry.check(binding, scope="chat")
        manager = IrohDeliverySession(channel=channel, store=store, server=server, on_state=self.on_state,
            admission_guard=self.admission_guard)
        self._managers[binding.transport_id] = manager
        channel.native_delivery = manager
        channel.register_handler(MessageType.APPLICATION_DELIVERY_CONTROL, manager.control)

    async def ready(self, channel):
        manager = self._managers.get(channel.binding.transport_id)
        if manager is not None and manager.channel is channel:
            await manager.ready()

    async def closed(self, binding):
        if binding is None:
            return
        manager = self._managers.get(binding.transport_id)
        if manager is None or manager.binding != binding:
            return
        del self._managers[binding.transport_id]
        await manager.close()
        if getattr(manager.channel, "native_delivery", None) is manager:
            manager.channel.native_delivery = None


class IrohDeliverySession:
    def __init__(self, *, channel: Any, store: Any, server: bool, on_state=None, now_ms=lambda: int(time.time() * 1000), admission_guard=None):
        self.channel, self.binding, self.api = channel, channel.binding, channel.api
        self.store, self.server, self.on_state, self.now_ms = store, server, on_state, now_ms
        self.closed = False
        self.synced = asyncio.Event()
        self._controls = asyncio.Queue(maxsize=64)
        self._worker = None
        self._recovery = None
        self._jobs = set()
        self._disk_jobs = set()
        self._waiters = {}
        self._admission_guard = admission_guard

    def _check(self):
        if self.closed or not self.channel.connection_active or self.channel.binding != self.binding or \
                self.channel.registry.check(self.binding, scope="chat") is not self.channel:
            raise SessionDenied("application delivery session is obsolete")

    def _own(self, coroutine):
        try:
            self._check()
            if len(self._jobs) >= 66:
                raise SessionDenied("application delivery operation capacity is exhausted")
        except BaseException:
            coroutine.close()
            raise
        task = asyncio.create_task(coroutine)
        self._jobs.add(task); task.add_done_callback(self._jobs.discard)
        return task

    async def _disk(self, function, *args):
        self._check()
        task = asyncio.create_task(_joined_disk(function, *args))
        self._disk_jobs.add(task); task.add_done_callback(self._disk_jobs.discard)
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        self._check()
        return result

    async def _control(self, value):
        self._check()
        wire = bytes(self.api.application_delivery_control(_json(value), uuid.uuid4().hex))
        if not await self.channel._send_payload(wire):
            raise ConnectionError("application delivery control could not be sent")
        self._check()

    async def _receipt(self, row):
        await self._control(dict(event="receipt", operation_id=row.operation_id, digest=list(row.digest),
            revision=row.revision, expires_at_ms=row.expires_at_ms, status=row.phase.name.lower()))

    async def _notice(self, row):
        if self.on_state is not None:
            try:
                state = {"operation_id": row.operation_id, "state": row.phase.name.lower()}
                if getattr(row, "client_prompt_id", None) is not None:
                    state["client_prompt_id"] = row.client_prompt_id
                result = self.on_state(self.binding, state)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass

    async def ready(self):
        if self.server:
            revision = await self._disk(self.store.revision, self.binding.generation)
            await self._control(dict(event="sync", revision=revision))

    def control(self, message):
        self._check()
        value = json.loads(self.api.parse_application_delivery_control(self.channel._wire(message)))
        if self.server and value["event"] != "query" or not self.server and value["event"] == "query":
            raise SessionDenied("application delivery control has the wrong direction")
        try:
            self._controls.put_nowait(value)
        except asyncio.QueueFull:
            raise SessionDenied("application delivery control capacity is exhausted") from None
        if self._worker is None or self._worker.done():
            self._worker = self._own(self._consume_controls())

    async def _consume_controls(self):
        try:
            while not self._controls.empty():
                value = self._controls.get_nowait()
                try:
                    if value["event"] == "sync":
                        cancelled = await self._disk(self.store.align_revision, value["revision"], self.binding.generation, self.now_ms())
                        for row in cancelled:
                            await self._notice(row)
                        for row in await self._disk(self.store.observations, self.binding.generation, self.now_ms()):
                            await self._notice(row)
                        self.synced.set()
                        if self._recovery is None or self._recovery.done():
                            self._recovery = self._own(self._recover())
                    elif value["event"] == "query":
                        row = await self._disk(self.store.query, value["operation_id"], bytes(value["digest"]), value["revision"],
                            value["expires_at_ms"], self.binding.generation, self.now_ms())
                        await self._receipt(row)
                    else:
                        phase = getattr(self.api.DeliveryPhase, value["status"].upper())
                        row = await self._disk(self.store.receipt, value["operation_id"], bytes(value["digest"]), value["revision"],
                            value["expires_at_ms"], phase, self.binding.generation, self.now_ms())
                        queue = self._waiters.get(value["operation_id"])
                        if queue is not None:
                            try:
                                queue.put_nowait(dict(value, _can_replay=row.phase == self.api.DeliveryPhase.QUEUED and row.envelope is not None))
                            except asyncio.QueueFull:
                                raise SessionDenied("application receipt capacity is exhausted") from None
                        await self._notice(row)
                finally:
                    self._controls.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.channel.disconnect()

    async def send(self, wire):
        task = self._own(self._send(wire))
        return await task

    async def _send(self, wire):
        await asyncio.wait_for(self.synced.wait(), 10)
        self._check()
        expiry = self.now_ms() + 24 * 60 * 60 * 1000
        payload = json.loads(wire)["payload"]
        from shared.iroh_context import _items
        for _item, attachments in _items(payload.get("context") or []):
            for attachment in attachments:
                if isinstance(attachment.get("file_ref"), dict):
                    expiry = min(expiry, attachment["file_ref"]["expires_at_ms"])
        row = await self._disk(self.store.queue, wire, self.binding.generation, self.now_ms(), expiry)
        await self._notice(row)
        if row.phase == self.api.DeliveryPhase.QUEUED and row.envelope is not None:
            await self.channel._send_payload(bytes(row.envelope))
            self._check()
        return row.phase not in {self.api.DeliveryPhase.DELETED, self.api.DeliveryPhase.EXPIRED, self.api.DeliveryPhase.UNCERTAIN}

    async def _recover(self):
        try:
            rows = await self._disk(self.store.pending, self.binding.generation, self.now_ms())
            for row in rows:
                self._check()
                queue = asyncio.Queue(maxsize=8)
                self._waiters[row.operation_id] = queue
                try:
                    await self._control(dict(event="query", operation_id=row.operation_id, digest=list(row.digest),
                        revision=row.revision, expires_at_ms=row.expires_at_ms))
                    receipt = await asyncio.wait_for(queue.get(), 10)
                    if receipt["status"] == "missing" and receipt.get("_can_replay") and row.envelope is not None:
                        message = self.channel._decode_message(bytes(self.api.validate_envelope(bytes(row.envelope))))
                        files = getattr(self.channel, "native_files", None)
                        if message.payload.get("context"):
                            from shared.iroh_context import outgoing_context, needs_file_capability
                            if files is None and needs_file_capability(message.payload["context"]):
                                raise SessionDenied("queued attachment capability is unavailable")
                            if files is not None:
                                await outgoing_context(files, message.payload["context"])
                        self._check()
                        if not await self.channel._send_payload(self.channel._wire(message)):
                            return
                finally:
                    self._waiters.pop(row.operation_id, None)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, ConnectionError):
            return  # Keep the encrypted queue for a fresh admitted session.
        except Exception:
            self.channel.disconnect()

    async def admit(self, message, dispatch):
        return await self._own(self._guarded_admit(message, dispatch))

    async def _guarded_admit(self, message, dispatch):
        if self._admission_guard is not None:
            async with self._admission_guard(self.binding):
                await self._admit(message, dispatch)
        else:
            await self._admit(message, dispatch)

    async def _admit(self, message, dispatch):
        wire = self.channel._wire(message)
        row = await self._disk(self.store.begin, wire, self.binding.generation, self.now_ms())
        if not row.execute:
            await self._receipt(row)
            return
        try:
            self._check()
            await dispatch(message)
            self._check()
        except BaseException:
            # This owned write attests only uncertainty and grants no execution.
            # It must finish even when the callback/session was cancelled.
            try:
                await _joined_disk(self.store.finish, row.operation_id, self.binding.generation, self.now_ms(), False)
            except Exception:
                pass
            raise
        committed = await self._disk(self.store.finish, row.operation_id, self.binding.generation, self.now_ms(), True)
        await self._receipt(committed)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        for queue in self._waiters.values():
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(dict(status="deleted"))
        self._waiters.clear()
        jobs = tuple(self._jobs)
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        if self._disk_jobs:
            await asyncio.gather(*tuple(self._disk_jobs), return_exceptions=True)
        while not self._controls.empty():
            self._controls.get_nowait(); self._controls.task_done()
