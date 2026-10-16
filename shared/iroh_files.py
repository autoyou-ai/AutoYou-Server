# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Owned file adapter over the shared Rust parser and protected durable store.

The caller supplies the local endpoint and an installation-protected key. Wire
descriptors never supply an owner, storage path, epoch or authorization grant.
Files become readable only after whole-file verification and durable commit.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType
from shared.session_transport import SessionDenied

BLOCK_BYTES = 48 * 1024
MAX_FILES = 64
FILE_WINDOW_BLOCKS = 8
MAX_FILE_BYTES = 1024 * 1024 * 1024


def file_limits(*, attachment_max_bytes: int = MAX_FILE_BYTES, download_max_bytes: int = MAX_FILE_BYTES) -> dict:
    if any(type(value) is not int or not 1 <= value <= MAX_FILE_BYTES for value in (attachment_max_bytes, download_max_bytes)):
        raise ValueError("invalid native file consumer limits")
    return dict(version=1, attachment_max_bytes=attachment_max_bytes, download_max_bytes=download_max_bytes)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


async def load_file_service(keys: Any, *, unlocked_password: str | None = None,
                            attachment_max_bytes: int = MAX_FILE_BYTES) -> IrohFileService:
    if getattr(keys, "purpose", None) != "resume":
        raise ValueError("file storage requires a separate protected resume key")
    loading = asyncio.create_task(asyncio.to_thread(keys.load, enroll=True, unlocked_password=unlocked_password))
    try:
        key = await asyncio.shield(loading)
    except asyncio.CancelledError:
        await loading
        raise
    return IrohFileService(root=keys.root / "files", key=key, attachment_max_bytes=attachment_max_bytes)


class IrohFileService:
    """One handler owner, with separate verified stores per admitted channel."""
    def __init__(self, *, root: Path, key: bytes, attachment_max_bytes: int = MAX_FILE_BYTES) -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("file storage key is unavailable")
        self.root, self._key = root, key
        self.limits = file_limits(attachment_max_bytes=attachment_max_bytes)
        self._managers: dict[str, IrohFiles] = {}

    async def prepared(self, transport: Any, context: Any, channel: Any) -> None:
        binding = channel.binding
        if "files" not in binding.scopes:
            return
        if binding.transport_id in self._managers or len(self._managers) >= 32:
            raise SessionDenied("file channel capacity is exhausted")
        role = getattr(channel, "role", transport.role)
        manager = await IrohFiles.create(channel=channel, local_endpoint=context.local_endpoint_id,
            server_instance=context.local_endpoint_id if role == "server" else binding.endpoint_id,
            root=self.root, key=self._key, local_limits=self.limits,
            remote_limits=getattr(channel, "application_capabilities", {}).get("file_limits") if role == "client" else None)
        self._managers[binding.transport_id] = manager
        channel.native_files = manager
        channel.register_handler(MessageType.BINARY_TRANSFER_CONTROL, manager.control)
        channel.register_handler(MessageType.TRANSPORT_TRANSFER_LIMITS, manager.accept_limits)

    async def ready(self, channel: Any) -> None:
        manager = self._managers.get(channel.binding.transport_id)
        if manager is not None:
            await manager.announce_limits()

    async def receive(self, channel: Any, frame: Any) -> None:
        manager = self._managers.get(channel.binding.transport_id)
        if manager is None or manager.channel is not channel:
            raise SessionDenied("file capability is not attached to this session")
        await manager.receive(channel, frame)

    async def closed(self, binding: Any) -> None:
        if binding is None:
            return
        manager = self._managers.get(binding.transport_id)
        if manager is None or manager.binding != binding:
            return
        await manager.closed(binding)
        self._managers.pop(binding.transport_id, None)
        if getattr(manager.channel, "native_files", None) is manager:
            manager.channel.native_files = None


class IrohFiles:
    def __init__(self, *, channel: Any, incoming: Any, outgoing: Any,
                 now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
                  on_progress: Callable | None = None, on_committed: Callable | None = None,
                   history_root: Path | None = None, local_limits: dict | None = None,
                   remote_limits: dict | None = None) -> None:
        self.channel, self.api, self.binding = channel, channel.api, channel.binding
        self.incoming, self.outgoing, self.now_ms = incoming, outgoing, now_ms
        self.receiver = self.api.FileTransferReceiver(incoming, self.binding.generation)
        self.local_limits = local_limits or file_limits()
        self.receiver.configure_limits(_json(self.local_limits))
        self.remote_limits = None
        self._limits_ready = asyncio.Event()
        if remote_limits is not None:
            self._set_remote_limits(bytes(self.api.transfer_limits(_json(remote_limits), uuid.uuid4().hex)))
        self.on_progress, self.on_committed = on_progress, on_committed
        self.history_root = history_root
        self._closed = False
        self._jobs: set[asyncio.Task] = set()
        self._disk_jobs: set[asyncio.Task] = set()
        self._waiters: dict[str, tuple[dict, asyncio.Queue]] = {}
        self._sending: set[str] = set()
        self._sender_failures: dict[str, str] = {}
        self._staging: set[str] = set()
        self._controls: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._control_worker: asyncio.Task | None = None
        self._cancelled: dict[str, int] = {}
        self._closing_task: asyncio.Task | None = None
        self._progress_at: dict[str, tuple[int, int]] = {}

    def _set_remote_limits(self, wire: bytes) -> None:
        limits = json.loads(self.api.parse_transfer_limits(wire))
        if self.remote_limits is not None and limits != self.remote_limits:
            raise SessionDenied("file limits changed within an admitted generation")
        self.remote_limits = limits
        self._limits_ready.set()

    async def accept_limits(self, message: Any) -> None:
        self._check()
        self._set_remote_limits(self.channel._wire(message))

    async def announce_limits(self) -> None:
        self._check()
        wire = bytes(self.api.transfer_limits(_json(self.local_limits), uuid.uuid4().hex))
        if not await self.channel.send_message(DataChannelMessage.from_json(wire.decode("utf-8"))):
            raise ConnectionError("file limits could not be announced")
        self._check()

    @classmethod
    async def create(cls, *, channel: Any, local_endpoint: str, server_instance: str,
                     root: Path, key: bytes, **options: Any) -> IrohFiles:
        binding = channel.binding
        if channel.registry.check(binding, scope="files") is not channel:
            raise SessionDenied("file adapter does not own the verified session")
        scope = dict(local_endpoint=local_endpoint, remote_endpoint=binding.endpoint_id,
            server_instance=server_instance, device_id=binding.device_id, owner_key=binding.owner_key,
            canonical_user_id=binding.canonical_user_id, conversation_key=binding.conversation_key,
            authorization_epoch=binding.authorization_epoch)

        def open_stores() -> tuple[Any, Any]:
            incoming, outgoing = (channel.api.FileTransferStore(str(root.resolve()), key,
                _json(dict(scope, direction=direction)), True) for direction in ("incoming", "outgoing"))
            incoming.sweep(int(time.time() * 1000))
            return incoming, outgoing

        creation = asyncio.create_task(asyncio.to_thread(open_stores))
        try:
            incoming, outgoing = await asyncio.shield(creation)
        except asyncio.CancelledError:
            await creation
            raise
        if channel.registry.check(binding, scope="files") is not channel or not channel.connection_active:
            raise SessionDenied("file adapter session was replaced during initialization")
        namespace = hashlib.sha256(_json(scope).encode()).hexdigest()
        return cls(channel=channel, incoming=incoming, outgoing=outgoing,
            history_root=root.resolve().parent / "native-file-history" / namespace, **options)

    def _check(self) -> None:
        if self._closed or not self.channel.is_ready or self.channel.binding != self.binding or \
                self.channel.registry.check(self.binding, scope="files") is not self.channel:
            raise SessionDenied("file adapter is closed or superseded")

    async def _disk(self, function: Callable, *args: Any, cancel_event: threading.Event | None = None) -> Any:
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self._disk_jobs.add(task)
        task.add_done_callback(self._disk_jobs.discard)
        try:
            value = await asyncio.shield(task)
        except asyncio.CancelledError as cancellation:
            if cancel_event is not None:
                cancel_event.set()
            # A cancelled asyncio worker cannot abandon a still running fsync.
            # Teardown waits for it before shutting down the shared receiver.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                # Retrieve a fenced IO failure even when it won the race with
                # cancellation; the caller still receives its cancellation.
                task.exception()
            raise cancellation
        return value

    async def _io(self, function: Callable, *args: Any) -> Any:
        self._check()
        value = await self._disk(function, *args)
        self._check()
        return value

    async def _open_source(self, path: Path) -> Any:
        def open_source() -> Any:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
            try:
                return os.fdopen(descriptor, "rb")
            except BaseException:
                os.close(descriptor)
                raise
        opening = asyncio.create_task(asyncio.to_thread(open_source))
        self._disk_jobs.add(opening)
        opening.add_done_callback(self._disk_jobs.discard)
        try:
            return await asyncio.shield(opening)
        except asyncio.CancelledError:
            opened = await opening
            await self._disk(opened.close)
            raise

    async def _send_control(self, control: dict | str) -> None:
        self._check()
        text = control if isinstance(control, str) else _json(control)
        wire = bytes(self.api.binary_transfer_control(text, uuid.uuid4().hex))
        parsed = DataChannelMessage.from_json(wire.decode("utf-8"))
        if not await self.channel.send_message(parsed):
            raise ConnectionError("file control could not be queued")

    def control(self, message: DataChannelMessage) -> None:
        self._check()
        if message.header.message_type != MessageType.BINARY_TRANSFER_CONTROL:
            raise SessionDenied("invalid file control family")
        # Validate through the shared parser before retaining peer metadata.
        wire = bytes(self.api.binary_transfer_control(_json(message.payload), message.header.message_id))
        control = json.loads(self.api.parse_binary_transfer_control(wire))
        descriptor = control["descriptor"]
        identifier = descriptor["transfer_id"]
        if control["event"] == "status":
            waiting = self._waiters.get(identifier)
            if waiting is not None:
                expected, queue = waiting
                if descriptor != expected:
                    raise SessionDenied("file receipt does not match its owned operation")
                if identifier in self._sending and control["phase"] in {"deleted", "unavailable"}:
                    self._sender_failures[identifier] = control["phase"]
                try:
                    queue.put_nowait(control)
                except asyncio.QueueFull:
                    raise SessionDenied("file receipt capacity is exhausted") from None
            return
        if control["event"] == "cancel":
            self._mark_cancelled(descriptor)
        try:
            self._controls.put_nowait(_json(control))
        except asyncio.QueueFull:
            raise SessionDenied("file control capacity is exhausted") from None
        if self._control_worker is None or self._control_worker.done():
            self._control_worker = self._own(self._process_controls())

    def _mark_cancelled(self, descriptor: dict) -> None:
        now = self.now_ms()
        self._cancelled = {key: expiry for key, expiry in self._cancelled.items() if expiry > now}
        identifier = descriptor["transfer_id"]
        if descriptor["expires_at_ms"] <= now:
            return
        if identifier not in self._cancelled and len(self._cancelled) >= 4096:
            raise SessionDenied("file cancellation capacity is exhausted")
        self._cancelled[identifier] = descriptor["expires_at_ms"]

    def _own(self, coroutine: Any) -> asyncio.Task:
        task = asyncio.create_task(coroutine, name="iroh-file-operation")
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)
        return task

    async def _run_owned(self, coroutine: Any) -> Any:
        try:
            self._check()
            if len(self._jobs) >= MAX_FILES * 2 + 2:
                raise SessionDenied("file operation capacity is exhausted")
        except BaseException:
            coroutine.close()
            raise
        value = await self._own(coroutine)
        self._check()
        return value

    async def _process_controls(self) -> None:
        try:
            while not self._controls.empty():
                control = self._controls.get_nowait()
                try:
                    status = await self._io(self.receiver.control, control, self.now_ms())
                    parsed = json.loads(control)
                    if parsed["event"] == "cancel":
                        await self._forget_retained(parsed["descriptor"])
                    await self._send_control(status)
                finally:
                    self._controls.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.channel.disconnect()

    async def receive(self, channel: Any, frame: Any) -> None:
        self._check()
        if channel is not self.channel or frame.lane != 7 or frame.generation != self.binding.generation:
            raise SessionDenied("file record belongs to another session")
        event = await self._io(self.receiver.receive, frame.stream_id, bytes(frame.payload), self.now_ms())
        descriptor = json.loads(event.descriptor_json)
        identifier = descriptor["transfer_id"]
        status = json.loads(event.control_json) if event.control_json is not None else None
        cancelled = identifier in self._cancelled
        if event.receipt is not None:
            receipt = event.receipt
            await self.channel.acknowledge_byte_stream(7, receipt.stream_id, receipt.total, bytes(receipt.digest))
            self._check()
            cancelled = identifier in self._cancelled
        # Priority Cancel may have arrived while a Finish fsync was running.
        # Its owned worker will publish the tombstone; suppress the old commit.
        if status is not None and not (cancelled and status["phase"] == "committed"):
            await self._send_control(status)
            cancelled = identifier in self._cancelled
        previous = self._progress_at.get(identifier, (0, 0))
        terminal = event.kind in {self.api.ByteStreamKind.FINISH, self.api.ByteStreamKind.ABORT}
        if not cancelled and self.on_progress is not None and (terminal or self.now_ms() - previous[0] >= 250):
            self._progress_at[identifier] = (self.now_ms(), event.durable_offset)
            result = self.on_progress(descriptor, event.durable_offset, status)
            if inspect.isawaitable(result):
                await result
                self._check()
                cancelled = identifier in self._cancelled
        if terminal:
            self._progress_at.pop(identifier, None)
        if not cancelled and event.newly_committed and status is not None and status["phase"] == "committed" and self.on_committed is not None:
            self._check()
            result = self.on_committed(descriptor)
            if inspect.isawaitable(result):
                await result
                self._check()

    async def _query(self, descriptor: dict, *, timeout: float = 30) -> dict:
        self._check()
        identifier = descriptor["transfer_id"]
        if identifier in self._waiters or len(self._waiters) >= MAX_FILES:
            raise SessionDenied("file receipt waiter capacity is exhausted")
        queue = asyncio.Queue(maxsize=8)
        self._waiters[identifier] = (descriptor, queue)
        try:
            await self._send_control(dict(event="query", descriptor=descriptor))
            result = await asyncio.wait_for(queue.get(), timeout)
            while not queue.empty():
                result = queue.get_nowait()
            if isinstance(result, Exception):
                raise result
            self._check()
            return result
        finally:
            if self._waiters.get(identifier, (None, None))[1] is queue:
                del self._waiters[identifier]

    async def stage_bytes(self, data: bytes, *, filename: str, mime_type: str, purpose: str = "attachment",
                          retention_ms: int = 86400000, metadata: dict | None = None) -> dict:
        return await self._run_owned(self._stage_bytes(data, filename=filename, mime_type=mime_type,
            purpose=purpose, retention_ms=retention_ms, metadata=metadata))

    async def _stage_bytes(self, data: bytes, *, filename: str, mime_type: str, purpose: str,
                           retention_ms: int, metadata: dict | None) -> dict:
        """Compatibility caller already owns these bytes; wire carriage stays raw.

        Streaming file-path staging is separate so a large selected file need
        not first become a Python byte string or an application JSON body.
        """
        self._check()
        if len(data) > 4 * 1024 * 1024 or len(self._staging) >= MAX_FILES:
            raise ValueError("in-memory file staging is bounded; use a streaming source")
        descriptor = dict(transfer_id=uuid.uuid4().hex, purpose=purpose, filename=filename, mime_type=mime_type,
            total=len(data), offset=0, sha256=list(hashlib.sha256(data).digest()),
            expires_at_ms=self.now_ms() + retention_ms, metadata=metadata or {})
        # The shared descriptor validator bounds metadata, names, sizes and TTL.
        self.api.binary_transfer_metadata(_json(descriptor), uuid.uuid4().hex)
        self._staging.add(descriptor["transfer_id"])
        complete = False
        try:
            await self._io(self.outgoing.stage, _json(descriptor), self.binding.generation, self.now_ms())
            for offset in range(0, len(data), BLOCK_BYTES):
                await self._io(self.outgoing.append, descriptor["transfer_id"], self.binding.generation,
                    offset, data[offset:offset + BLOCK_BYTES], self.now_ms())
            await self._io(self.outgoing.finish, descriptor["transfer_id"], self.binding.generation, self.now_ms())
            complete = True
            return descriptor
        finally:
            self._staging.discard(descriptor["transfer_id"])
            if not complete:
                try:
                    await self._disk(self.outgoing.cancel, _json(descriptor), self.binding.generation, self.now_ms())
                except (self.api.BindingError.PermissionDenied, self.api.BindingError.StorageUnavailable,
                        self.api.BindingError.StorageCapacity, self.api.BindingError.InvalidInput):
                    pass
            await self._disk(self.outgoing.release, descriptor["transfer_id"])

    async def stage_path(self, path: Path, *, filename: str | None = None,
                         mime_type: str = "application/octet-stream", purpose: str = "attachment",
                         retention_ms: int = 86400000, metadata: dict | None = None) -> dict:
        return await self._run_owned(self._stage_path(path, filename=filename, mime_type=mime_type,
            purpose=purpose, retention_ms=retention_ms, metadata=metadata))

    async def _stage_path(self, path: Path, *, filename: str | None, mime_type: str,
                          purpose: str, retention_ms: int, metadata: dict | None) -> dict:
        """Copy an explicitly selected local source without accumulating its body.

        Paths are host inputs and never accepted from a remote descriptor. Two
        bounded passes freeze the content digest, then encrypt the durable source.
        Replacing or changing the source during staging cannot silently ship it.
        """
        self._check()
        if len(self._staging) >= MAX_FILES:
            raise SessionDenied("file staging capacity is exhausted")
        identifier = uuid.uuid4().hex
        self._staging.add(identifier)
        source = None
        descriptor = None
        staged = complete = False
        try:
            source = await self._open_source(Path(path))
            self._check()
            before = await self._io(os.fstat, source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024 * 1024:
                raise ValueError("selected file exceeds the supported file bounds")
            digest = hashlib.sha256()
            length = 0
            while True:
                data = await self._io(source.read, BLOCK_BYTES)
                if not data:
                    break
                length += len(data)
                if length > before.st_size:
                    raise ConnectionError("selected file changed during staging")
                digest.update(data)
            if length != before.st_size:
                raise ConnectionError("selected file changed during staging")
            descriptor = dict(transfer_id=identifier, purpose=purpose, filename=filename or Path(path).name,
                mime_type=mime_type, total=length, offset=0, sha256=list(digest.digest()),
                expires_at_ms=self.now_ms() + retention_ms, metadata=metadata or {})
            self.api.binary_transfer_metadata(_json(descriptor), uuid.uuid4().hex)
            staged = True
            await self._io(self.outgoing.stage, _json(descriptor), self.binding.generation, self.now_ms())
            await self._io(source.seek, 0)
            offset = 0
            while offset < length:
                data = await self._io(source.read, min(BLOCK_BYTES, length - offset))
                if not data:
                    raise ConnectionError("selected file changed during staging")
                await self._io(self.outgoing.append, identifier, self.binding.generation, offset, data, self.now_ms())
                offset += len(data)
            after = await self._io(os.fstat, source.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ConnectionError("selected file changed during staging")
            await self._io(self.outgoing.finish, identifier, self.binding.generation, self.now_ms())
            complete = True
            return descriptor
        finally:
            self._staging.discard(identifier)
            if source is not None:
                await self._disk(source.close)
            if staged and not complete and descriptor is not None:
                try:
                    await self._disk(self.outgoing.cancel, _json(descriptor), self.binding.generation, self.now_ms())
                except (self.api.BindingError.PermissionDenied, self.api.BindingError.StorageUnavailable,
                        self.api.BindingError.StorageCapacity, self.api.BindingError.InvalidInput):
                    pass
            await self._disk(self.outgoing.release, identifier)

    async def stage_reader(self, reader: Callable, *, filename: str, mime_type: str,
                           metadata: dict | None = None, retention_ms: int = 86400000) -> dict:
        """A host-owned reusable bounded reader, including protected/base64 sources."""
        return await self._run_owned(self._stage_reader(reader, filename, mime_type, metadata, retention_ms))

    async def _stage_reader(self, reader: Callable, filename: str, mime_type: str,
                            metadata: dict | None, retention_ms: int) -> dict:
        self._check()
        if len(self._staging) >= MAX_FILES:
            raise SessionDenied("file staging capacity is exhausted")
        identifier = uuid.uuid4().hex
        self._staging.add(identifier)
        stopped = threading.Event()

        def stage() -> dict:
            descriptor = None
            complete = False
            def checked_blocks():
                iterator = iter(reader())
                try:
                    while True:
                        if stopped.is_set():
                            raise ConnectionError("file staging was cancelled")
                        self._check()
                        block = next(iterator, None)
                        if block is None:
                            break
                        if not isinstance(block, bytes) or not 0 < len(block) <= BLOCK_BYTES:
                            raise ValueError("file reader exceeded its block bound")
                        yield block
                finally:
                    close = getattr(iterator, "close", None)
                    if close is not None:
                        close()
            try:
                digest, size = hashlib.sha256(), 0
                for block in checked_blocks():
                    size += len(block)
                    if size > 1024 * 1024 * 1024:
                        raise ValueError("file reader exceeded its supported size")
                    digest.update(block)
                descriptor = dict(transfer_id=identifier, purpose="attachment", filename=filename,
                    mime_type=mime_type, total=size, offset=0, sha256=list(digest.digest()),
                    expires_at_ms=self.now_ms() + retention_ms, metadata=metadata or {})
                self.api.binary_transfer_metadata(_json(descriptor), uuid.uuid4().hex)
                self.outgoing.stage(_json(descriptor), self.binding.generation, self.now_ms())
                offset = 0
                # Reblock a reader's short intermediate yields for the durable
                # shared store. Its last block alone may be smaller than 48 KiB.
                buffered = bytearray()
                for block in checked_blocks():
                    buffered.extend(block)
                    while len(buffered) >= BLOCK_BYTES:
                        self.outgoing.append(identifier, self.binding.generation, offset, bytes(buffered[:BLOCK_BYTES]), self.now_ms())
                        del buffered[:BLOCK_BYTES]; offset += BLOCK_BYTES
                if buffered:
                    self.outgoing.append(identifier, self.binding.generation, offset, bytes(buffered), self.now_ms())
                self._check()
                if stopped.is_set():
                    raise ConnectionError("file staging was cancelled")
                self.outgoing.finish(identifier, self.binding.generation, self.now_ms())
                complete = True
                return descriptor
            finally:
                if descriptor is not None and not complete:
                    self.outgoing.cancel(_json(descriptor), self.binding.generation, self.now_ms())
                self.outgoing.release(identifier)
        try:
            result = await self._disk(stage, cancel_event=stopped)
            self._check(); return result
        finally:
            self._staging.discard(identifier)

    async def promote_attachment(self, descriptor: dict, *, directory: Path) -> dict:
        """Promote only an authenticated committed file into business storage.

        The directory is selected by the host. A peer cannot supply a path,
        scope, owner or protected-storage key through attachment metadata.
        """
        return await self._run_owned(self._promote_attachment(descriptor, directory))

    async def _promote_attachment(self, descriptor: dict, directory: Path) -> dict:
        from shared.openclaw_gateway import safe_filename
        from shared.secure_storage import write_secure_stream
        self._check()
        self.api.binary_transfer_metadata(_json(descriptor), uuid.uuid4().hex)
        if descriptor.get("offset") != 0 or descriptor.get("purpose") != "attachment":
            raise SessionDenied("invalid attachment reference")
        directory = Path(directory).resolve()
        test_root = os.environ.get("AUTOYOU_TEST_ROOT")
        if test_root and not directory.is_relative_to(Path(test_root).resolve()):
            raise SessionDenied("test attachment directory escaped its test root")
        target = directory / uuid.uuid4().hex / safe_filename(descriptor["filename"], descriptor["mime_type"])
        stopped = threading.Event()
        complete = False

        def checked():
            self._check()
            if stopped.is_set() or descriptor["transfer_id"] in self._cancelled:
                raise SessionDenied("attachment was cancelled")

        def promote() -> None:
            checked()
            checkpoint = self.incoming.checkpoint(descriptor["transfer_id"], self.now_ms())
            if checkpoint is None or checkpoint.phase != self.api.FileTransferPhase.COMMITTED or \
                    json.loads(checkpoint.descriptor_json) != descriptor:
                raise SessionDenied("attachment is not a matching committed file")
            if checkpoint.generation != self.binding.generation:
                self.incoming.stage(_json(dict(descriptor, offset=descriptor["total"])), self.binding.generation, self.now_ms())
            def blocks():
                offset = 0
                while offset < descriptor["total"]:
                    checked()
                    block = bytes(self.incoming.read(descriptor["transfer_id"], self.binding.generation, offset, BLOCK_BYTES, self.now_ms()))
                    if not block:
                        raise ConnectionError("committed attachment is truncated")
                    offset += len(block); yield block
            write_secure_stream(target, blocks(), expected_size=descriptor["total"],
                expected_sha256=bytes(descriptor["sha256"]), before_commit=checked)
        try:
            await self._disk(promote, cancel_event=stopped)
            checked(); complete = True
            return dict(filename=descriptor["filename"], mimetype=descriptor["mime_type"],
                size_bytes=descriptor["total"], path=str(target))
        finally:
            if not complete:
                await self._disk(target.unlink, True)

    async def read(self, identifier: str, offset: int = 0, maximum: int = BLOCK_BYTES) -> bytes:
        return bytes(await self._io(self.incoming.read, identifier, self.binding.generation, offset, maximum, self.now_ms()))

    async def retain(self, descriptor: dict) -> Path:
        """Verified bounded promotion into the local client's private UI cache."""
        return await self._run_owned(self._retain(descriptor))

    def was_deleted(self, descriptor: dict) -> bool:
        from shared.iroh_file_history import deleted, storage_name
        self._check()
        self.api.binary_transfer_metadata(_json(descriptor), uuid.uuid4().hex)
        return descriptor["transfer_id"] in self._cancelled or self.history_root is not None and \
            deleted(self.history_root / storage_name(descriptor["transfer_id"], descriptor["filename"]))

    async def _retain(self, descriptor: dict) -> Path:
        from shared.iroh_file_history import deleted, reserve, storage_name
        self._check(); self.api.binary_transfer_metadata(_json(descriptor), uuid.uuid4().hex)
        if self.history_root is None or descriptor.get("offset") != 0 or descriptor.get("purpose") != "attachment":
            raise SessionDenied("native attachment presentation is unavailable")
        if self.history_root.is_symlink():
            raise SessionDenied("native presentation directory is invalid")
        directory = self.history_root.resolve()
        test = os.environ.get("AUTOYOU_TEST_ROOT")
        if test and not directory.is_relative_to(Path(test).resolve()):
            raise SessionDenied("presentation directory escaped its test root")
        target = directory / storage_name(descriptor["transfer_id"], descriptor["filename"])
        temporary = directory / ("." + uuid.uuid4().hex + ".partial")
        stopped = threading.Event(); complete = False; published = False

        def checked():
            self._check()
            if stopped.is_set() or descriptor["transfer_id"] in self._cancelled or deleted(target):
                raise SessionDenied("native attachment was deleted or cancelled")

        def promote():
            nonlocal published
            checked()
            checkpoint = self.incoming.checkpoint(descriptor["transfer_id"], self.now_ms())
            if checkpoint is None or checkpoint.phase != self.api.FileTransferPhase.COMMITTED or json.loads(checkpoint.descriptor_json) != descriptor:
                raise SessionDenied("native attachment is not a matching committed file")
            if checkpoint.generation != self.binding.generation:
                self.incoming.stage(_json(dict(descriptor, offset=descriptor["total"])), self.binding.generation, self.now_ms())
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if directory.is_symlink() or target.is_symlink():
                raise SessionDenied("native presentation path is invalid")
            import shutil
            if shutil.disk_usage(directory).free < descriptor["total"] + 1024 * 1024:
                raise OSError("native presentation storage is exhausted")
            offset = 0; digest = hashlib.sha256()
            descriptor_fd = reserve(temporary, descriptor["total"], now_ms=self.now_ms())
            with os.fdopen(descriptor_fd, "wb") as output:
                while offset < descriptor["total"]:
                    checked()
                    data = bytes(self.incoming.read(descriptor["transfer_id"], self.binding.generation, offset, BLOCK_BYTES, self.now_ms()))
                    if not data or len(data) > BLOCK_BYTES:
                        raise ConnectionError("native attachment is truncated")
                    output.write(data); digest.update(data); offset += len(data)
                if offset != descriptor["total"] or digest.digest() != bytes(descriptor["sha256"]):
                    raise ConnectionError("native attachment digest does not match")
                output.flush(); os.fsync(output.fileno())
            checked(); os.replace(temporary, target); published = True; checked()
        try:
            await self._disk(promote, cancel_event=stopped)
            checked(); complete = True; return target
        finally:
            await self._disk(temporary.unlink, True)
            if published and not complete:
                await self._disk(target.unlink, True)

    async def send(self, descriptor: dict, *, timeout: float = 30) -> dict:
        return await self._run_owned(self._send(descriptor, timeout=timeout))

    async def _send(self, descriptor: dict, *, timeout: float) -> dict:
        """Resume only this explicitly selected, locally committed file operation."""
        self._check()
        await asyncio.wait_for(self._limits_ready.wait(), min(timeout, 10))
        self._check()
        limit_name = "attachment_max_bytes" if descriptor.get("purpose") == "attachment" else "download_max_bytes"
        if descriptor["total"] > self.remote_limits[limit_name]:
            raise ValueError("file exceeds this peer's supported consumer limit")
        identifier = descriptor["transfer_id"]
        if identifier in self._sending or len(self._sending) >= MAX_FILES or descriptor.get("offset") != 0:
            raise SessionDenied("file operation is already active or invalid")
        self._sending.add(identifier)
        writer = None
        stream_id = None
        queued = 0
        opened = False
        completed = False
        try:
            checkpoint = await self._io(self.outgoing.checkpoint, identifier, self.now_ms())
            if checkpoint is None or checkpoint.phase != self.api.FileTransferPhase.COMMITTED or json.loads(checkpoint.descriptor_json) != descriptor:
                raise SessionDenied("file source is not a committed owned operation")
            # Advancing the source generation requires its exact durable total.
            if checkpoint.generation != self.binding.generation:
                await self._io(self.outgoing.stage, _json(dict(descriptor, offset=descriptor["total"])), self.binding.generation, self.now_ms())
            status = await self._query(descriptor, timeout=timeout)
            if status["phase"] == "committed":
                return status
            if status["phase"] != "pending" or status.get("failure") is not None:
                raise ConnectionError("file receiver did not admit the operation")
            offset = status["offset"]
            digest = hashlib.sha256()
            scanned = 0
            while scanned < offset:
                data = bytes(await self._io(self.outgoing.read, identifier, self.binding.generation, scanned,
                    min(BLOCK_BYTES, offset - scanned), self.now_ms()))
                if not data:
                    raise SessionDenied("file resume prefix is unavailable")
                digest.update(data); scanned += len(data)
            if list(digest.digest()) != status["prefix_sha256"]:
                raise SessionDenied("file receiver resume prefix does not match its source")
            writer = self.api.ByteStreamWriter(7, self.api.ByteStreamContent.RAW_FILE, descriptor["total"] - offset,
                bytes(self.api.binary_transfer_metadata(_json(dict(descriptor, offset=offset)), uuid.uuid4().hex)))
            self._check()
            stream_id = self.channel.endpoint.allocate_byte_stream(self.channel.connection_id, 7)
            final_receipt = asyncio.Queue(maxsize=8)
            self._waiters[identifier] = (descriptor, final_receipt)
            if not await self.channel.send_byte_record(7, stream_id, bytes(writer.open_record())):
                raise ConnectionError("file open could not be queued")
            opened = True
            current = offset
            window = 0
            while current < descriptor["total"]:
                if identifier in self._sender_failures:
                    raise ConnectionError("file operation was rejected or cancelled")
                data = bytes(await self._io(self.outgoing.read, identifier, self.binding.generation, current, BLOCK_BYTES, self.now_ms()))
                if not data or not await self.channel.send_byte_record(7, stream_id, bytes(writer.data_record(data))):
                    raise ConnectionError("file data could not be queued")
                current += len(data); queued += len(data)
                window += 1
                if window >= FILE_WINDOW_BLOCKS or current == descriptor["total"]:
                    # Network send capacity is not disk-consumer capacity. Keep
                    # at most eight raw blocks outstanding until a scoped query
                    # reports their durable prefix. This is flow control only;
                    # whole-file commit still requires the final receipt.
                    async with asyncio.timeout(timeout):
                        while True:
                            await self._send_control(dict(event="query", descriptor=descriptor))
                            result = await final_receipt.get()
                            if isinstance(result, Exception):
                                raise result
                            self._check()
                            if result["phase"] != "pending" or result.get("failure") is not None or result["offset"] > current:
                                raise ConnectionError("file prefix was rejected or cancelled")
                            if result["offset"] == current:
                                break
                            await asyncio.sleep(.01)
                    window = 0
            if not await self.channel.send_byte_record(7, stream_id, bytes(writer.finish_record())):
                raise ConnectionError("file finish could not be queued")
            completed = True
            # Byte completion alone never satisfies this application receipt.
            async with asyncio.timeout(timeout):
                while True:
                    result = await final_receipt.get()
                    if isinstance(result, Exception):
                        raise result
                    self._check()
                    if result["phase"] == "committed":
                        return result
                    if result["phase"] in {"deleted", "unavailable"}:
                        raise ConnectionError("file operation was rejected or cancelled")
        finally:
            self._waiters.pop(identifier, None)
            self._sending.discard(identifier)
            self._sender_failures.pop(identifier, None)
            if opened and stream_id is not None and writer is not None and not completed:
                task = asyncio.create_task(self.channel.send_byte_record(7, stream_id, bytes(writer.cancel_record(queued))))
                try:
                    if not await asyncio.shield(task):
                        self.channel.disconnect()
                except BaseException:
                    self.channel.disconnect()
            await self._disk(self.outgoing.release, identifier)

    async def cancel(self, descriptor: dict) -> None:
        await self._run_owned(self._cancel(descriptor))

    async def _forget_retained(self, descriptor: dict) -> None:
        from shared.iroh_file_history import forget, storage_name
        if self.history_root is None or not self.history_root.is_dir():
            return
        target = self.history_root / storage_name(descriptor["transfer_id"], descriptor["filename"])
        await self._disk(lambda: forget(target, allowed_root=self.history_root.parent))

    async def _cancel(self, descriptor: dict) -> None:
        self._check()
        control = _json(dict(event="cancel", descriptor=descriptor))
        self.api.binary_transfer_control(control, uuid.uuid4().hex)
        self._mark_cancelled(descriptor)
        if descriptor["transfer_id"] in self._sending:
            self._sender_failures[descriptor["transfer_id"]] = "deleted"
        await self._io(self.receiver.control, control, self.now_ms())
        await self._io(self.outgoing.cancel, _json(descriptor), self.binding.generation, self.now_ms())
        await self._forget_retained(descriptor)
        await self._send_control(dict(event="cancel", descriptor=descriptor))

    async def closed(self, binding: Any) -> None:
        if binding != self.binding:
            return
        if self._closing_task is None:
            self._closed = True
            self._closing_task = asyncio.create_task(self._close_owned())
        try:
            await asyncio.shield(self._closing_task)
        except asyncio.CancelledError:
            await self._closing_task
            raise

    async def _close_owned(self) -> None:
        self._limits_ready.set()
        for _, queue in self._waiters.values():
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(ConnectionError("file session closed"))
        self._waiters.clear()
        tasks = tuple(self._jobs)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._disk_jobs:
            await asyncio.gather(*tuple(self._disk_jobs), return_exceptions=True)
        while not self._controls.empty():
            self._controls.get_nowait(); self._controls.task_done()
        await asyncio.to_thread(self.receiver.shutdown)
        self._progress_at.clear()
