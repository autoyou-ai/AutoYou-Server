# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Bluetooth Pair signaling service.

This module keeps the Bluetooth transport separate from the HTTP Local Pair
endpoint. The core handler accepts complete Bluetooth Pair command text
(`/autopair...`) and routes it through PairingRouter with platform="bluetooth".
An optional BLE GATT server adapter can expose that handler over a real radio.
Non-Windows hosts use the optional `bless` runtime; Windows uses the direct
WinRT GATT adapter so its package dependencies remain compatible with modern
Python versions.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import platform
import queue
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import UUID

from .bluetooth_pairing_protocol import (
    AUTOYOU_BLUETOOTH_PAIR_RX_UUID,
    AUTOYOU_BLUETOOTH_PAIR_SERVICE_UUID,
    AUTOYOU_BLUETOOTH_PAIR_STATUS_UUID,
    AUTOYOU_BLUETOOTH_PAIR_TX_UUID,
    DEFAULT_FRAME_PAYLOAD_BYTES,
    KIND_AUTOPAIR_REQUEST,
    KIND_ERROR,
    BluetoothFrameReassembler,
    BluetoothPairFrameStream,
    BluetoothPairMessage,
    chunk_message,
    chunk_text_response,
)

LOGGER = logging.getLogger("autoyou.bluetooth_pairing")
AUTOPAIR_COMMAND = "/autopair"
AUTOPAIR_HELLO_COMMAND = "/autopair_hello"
AUTOPAIR_CANDIDATES_COMMAND = "/autopair_candidates"

def _json_error(message: str) -> str:
    return "/autopair_answer\n" + json.dumps({"error": message}, separators=(",", ":"))


def _is_autopair_command(text: str) -> bool:
    stripped = str(text or "").lstrip()
    if stripped.startswith(AUTOPAIR_HELLO_COMMAND):
        return True
    if stripped.startswith(AUTOPAIR_CANDIDATES_COMMAND):
        return True
    if stripped == AUTOPAIR_COMMAND:
        return True
    return stripped.startswith(AUTOPAIR_COMMAND) and stripped[len(AUTOPAIR_COMMAND)].isspace()


@dataclass(frozen=True)
class BluetoothPairingStatus:
    enabled: bool
    running: bool
    transport: str
    error: str = ""
    service_uuid: str = AUTOYOU_BLUETOOTH_PAIR_SERVICE_UUID
    rx_uuid: str = AUTOYOU_BLUETOOTH_PAIR_RX_UUID
    tx_uuid: str = AUTOYOU_BLUETOOTH_PAIR_TX_UUID


class BluetoothPairingCommandHandler:
    """Process complete Bluetooth Pair AutoPair commands without HTTP."""

    def __init__(
        self,
        *,
        pairing_router: Any,
        is_enabled: Callable[[], bool],
    ) -> None:
        self.pairing_router = pairing_router
        self.is_enabled = is_enabled

    async def handle_text(self, command_text: str, *, client_id: str) -> str:
        normalized_client_id = str(client_id or "").strip()
        if not normalized_client_id:
            return _json_error("Bluetooth Pair request is missing a client id.")
        if not self.is_enabled():
            return _json_error("Bluetooth Pair is off on this server.")
        if self.pairing_router is None:
            return _json_error("Pairing subsystem not ready.")
        text = str(command_text or "").strip()
        if not _is_autopair_command(text):
            return _json_error("Bluetooth Pair accepts only /autopair signaling commands.")
        result = await self.pairing_router.process_message(
            text,
            platform="bluetooth",
            sender_id=normalized_client_id,
            identity_sender_id=normalized_client_id,
        )
        fragment_sentinel = getattr(
            self.pairing_router,
            "FRAGMENT_CONSUMED",
            "__autopair_fragment_consumed__",
        )
        if result == fragment_sentinel:
            return _json_error("Incomplete Bluetooth Pair request.")
        if not result:
            return _json_error("Bluetooth Pair request did not produce an answer.")
        return str(result)


class InMemoryBluetoothPairingTransport:
    """Deterministic transport used by tests and non-radio host bridges."""

    def __init__(
        self,
        handler: BluetoothPairingCommandHandler,
        *,
        max_payload_bytes: int = DEFAULT_FRAME_PAYLOAD_BYTES,
    ) -> None:
        self.handler = handler
        self.max_payload_bytes = max_payload_bytes
        self.reassembler = BluetoothFrameReassembler()

    async def receive_frame(self, raw_frame: bytes) -> List[bytes]:
        message = self.reassembler.push(raw_frame)
        if message is None:
            return []
        return await self.handle_message(message)

    async def handle_message(self, message: BluetoothPairMessage) -> List[bytes]:
        if message.kind != KIND_AUTOPAIR_REQUEST:
            answer = _json_error("Unsupported Bluetooth Pair message kind.")
        else:
            answer = await self.handler.handle_text(message.text, client_id=message.client_id)
        return chunk_text_response(
            answer,
            client_id=message.client_id,
            request_message_id=message.message_id,
            max_payload_bytes=self.max_payload_bytes,
        )


def _enum_value(value: Any) -> int:
    raw = getattr(value, "value", value)
    try:
        return int(raw)
    except Exception:
        return -1


class WinRtBluetoothPairingServer:
    """Windows BLE GATT server backed directly by Python WinRT packages.

    The upstream `bless` Windows backend currently has dependency metadata and
    import problems on Python 3.13. This small adapter uses the same WinRT GATT
    primitives directly and keeps AutoYou's BLE protocol in this module.
    """

    def __init__(
        self,
        handler: BluetoothPairingCommandHandler,
        *,
        name: str = "AutoYou Bluetooth Pair",
        max_payload_bytes: int = DEFAULT_FRAME_PAYLOAD_BYTES,
    ) -> None:
        self.handler = handler
        self.name = name
        self.max_payload_bytes = max_payload_bytes
        self.transport = InMemoryBluetoothPairingTransport(
            handler,
            max_payload_bytes=max_payload_bytes,
        )
        self._rx_stream = BluetoothPairFrameStream()
        self._running = False
        self._last_error = ""
        self._last_status = b"stopped"
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._advertising_event: Optional[asyncio.Event] = None
        self._pending_notify_frames: queue.Queue[bytes] = queue.Queue()
        self._service_provider: Any = None
        self._local_service: Any = None
        self._tx_characteristic: Any = None
        self._event_tokens: List[tuple[Any, str, Any]] = []
        self._subscribed_clients: List[Any] = []

    @property
    def status(self) -> BluetoothPairingStatus:
        return BluetoothPairingStatus(
            enabled=True,
            running=self._running,
            transport="winrt-gatt",
            error=self._last_error,
        )

    async def start(self) -> bool:
        if self._running:
            return True
        if platform.system() != "Windows":
            self._last_error = "The WinRT Bluetooth Pair backend is only available on Windows."
            return False
        self._loop = asyncio.get_running_loop()
        self._advertising_event = asyncio.Event()
        try:
            from winrt.windows.devices.bluetooth.genericattributeprofile import (  # type: ignore
                GattCharacteristicProperties,
                GattLocalCharacteristicParameters,
                GattProtectionLevel,
                GattServiceProvider,
                GattServiceProviderAdvertisingParameters,
                GattWriteOption,
            )
            from winrt.windows.storage.streams import DataReader, DataWriter  # type: ignore
        except Exception as exc:
            self._last_error = (
                "Bluetooth Pair WinRT runtime is unavailable. Install AutoYou's "
                "Bluetooth requirements or run a host Bluetooth bridge."
            )
            LOGGER.warning("%s Detail: %s", self._last_error, exc)
            return False

        self._DataReader = DataReader
        self._DataWriter = DataWriter
        self._GattWriteOption = GattWriteOption

        try:
            result = await GattServiceProvider.create_async(
                UUID(AUTOYOU_BLUETOOTH_PAIR_SERVICE_UUID)
            )
            provider = getattr(result, "service_provider", None)
            if provider is None:
                error = getattr(result, "error", "")
                raise RuntimeError(f"WinRT could not create the Bluetooth Pair service provider: {error}")
            self._service_provider = provider
            self._local_service = provider.service
            if self._local_service is None:
                raise RuntimeError("WinRT did not return a local Bluetooth Pair service.")

            self._add_event_token(
                provider,
                "remove_advertisement_status_changed",
                provider.add_advertisement_status_changed(self._status_update),
            )

            await self._add_characteristic(
                AUTOYOU_BLUETOOTH_PAIR_RX_UUID,
                int(GattCharacteristicProperties.WRITE)
                | int(GattCharacteristicProperties.WRITE_WITHOUT_RESPONSE),
                readable=False,
                writable=True,
                notifiable=False,
                GattCharacteristicProperties=GattCharacteristicProperties,
                GattLocalCharacteristicParameters=GattLocalCharacteristicParameters,
                GattProtectionLevel=GattProtectionLevel,
            )
            self._tx_characteristic = await self._add_characteristic(
                AUTOYOU_BLUETOOTH_PAIR_TX_UUID,
                int(GattCharacteristicProperties.READ) | int(GattCharacteristicProperties.NOTIFY),
                readable=True,
                writable=False,
                notifiable=True,
                GattCharacteristicProperties=GattCharacteristicProperties,
                GattLocalCharacteristicParameters=GattLocalCharacteristicParameters,
                GattProtectionLevel=GattProtectionLevel,
            )
            await self._add_characteristic(
                AUTOYOU_BLUETOOTH_PAIR_STATUS_UUID,
                int(GattCharacteristicProperties.READ),
                readable=True,
                writable=False,
                notifiable=False,
                GattCharacteristicProperties=GattCharacteristicProperties,
                GattLocalCharacteristicParameters=GattLocalCharacteristicParameters,
                GattProtectionLevel=GattProtectionLevel,
            )

            advertising = GattServiceProviderAdvertisingParameters()
            advertising.is_discoverable = True
            advertising.is_connectable = True
            start_with_parameters = getattr(provider, "start_advertising_with_parameters", None)
            if callable(start_with_parameters):
                start_with_parameters(advertising)
            else:
                provider.start_advertising(advertising)
            try:
                await asyncio.wait_for(self._advertising_event.wait(), timeout=8.0)
            except asyncio.TimeoutError:
                if not self._is_provider_advertising():
                    status = getattr(provider, "advertisement_status", "unknown")
                    raise RuntimeError(
                        f"Bluetooth Pair WinRT GATT server did not start advertising (status {status})."
                    )
            self._running = True
            self._last_error = ""
            self._last_status = b"ready"
            LOGGER.info("Bluetooth Pair WinRT GATT server advertising as %s", self.name)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.warning("Bluetooth Pair WinRT GATT server failed to start: %s", exc, exc_info=True)
            await self.stop()
            return False

    async def stop(self) -> None:
        provider = self._service_provider
        self._service_provider = None
        self._local_service = None
        self._tx_characteristic = None
        self._running = False
        self._rx_stream.reset()
        self._loop = None
        self._advertising_event = None
        self._last_status = b"stopped"
        for owner, remover_name, token in self._event_tokens:
            remover = getattr(owner, remover_name, None)
            if callable(remover):
                try:
                    remover(token)
                except Exception:
                    pass
        self._event_tokens.clear()
        if provider is not None:
            try:
                provider.stop_advertising()
            except Exception as exc:
                LOGGER.debug("Bluetooth Pair WinRT stop failed: %s", exc)

    async def _add_characteristic(
        self,
        uuid_text: str,
        property_flags: int,
        *,
        readable: bool,
        writable: bool,
        notifiable: bool,
        GattCharacteristicProperties: Any,
        GattLocalCharacteristicParameters: Any,
        GattProtectionLevel: Any,
    ) -> Any:
        parameters = GattLocalCharacteristicParameters()
        parameters.characteristic_properties = GattCharacteristicProperties(property_flags)
        parameters.read_protection_level = GattProtectionLevel.PLAIN
        parameters.write_protection_level = GattProtectionLevel.PLAIN
        result = await self._local_service.create_characteristic_async(UUID(uuid_text), parameters)
        characteristic = getattr(result, "characteristic", None)
        if characteristic is None:
            error = getattr(result, "error", "")
            raise RuntimeError(f"WinRT could not create Bluetooth characteristic {uuid_text}: {error}")
        if readable:
            self._add_event_token(
                characteristic,
                "remove_read_requested",
                characteristic.add_read_requested(self._read_characteristic),
            )
        if writable:
            self._add_event_token(
                characteristic,
                "remove_write_requested",
                characteristic.add_write_requested(self._write_characteristic),
            )
        if notifiable:
            self._add_event_token(
                characteristic,
                "remove_subscribed_clients_changed",
                characteristic.add_subscribed_clients_changed(self._subscribe_characteristic),
            )
        return characteristic

    def _add_event_token(self, owner: Any, remover_name: str, token: Any) -> None:
        self._event_tokens.append((owner, remover_name, token))

    def _is_provider_advertising(self) -> bool:
        provider = self._service_provider
        if provider is None:
            return False
        return _enum_value(getattr(provider, "advertisement_status", None)) == 2

    def _status_update(self, _provider: Any, args: Any) -> None:
        if args is not None and _enum_value(getattr(args, "status", None)) == 2:
            loop = self._loop
            event = self._advertising_event
            if loop is not None and event is not None and not loop.is_closed():
                loop.call_soon_threadsafe(event.set)

    def _read_characteristic(self, sender: Any, args: Any) -> None:
        deferral = args.get_deferral()
        if deferral is None:
            return
        try:
            value = self._read_value_for_uuid(str(getattr(sender, "uuid", "") or ""))
            writer = self._DataWriter()
            writer.write_bytes(value if value is not None else b"")
            request_holder: Dict[str, Any] = {}

            async def get_request() -> None:
                request_holder["request"] = await args.get_request_async()

            asyncio.new_event_loop().run_until_complete(get_request())
            request = request_holder.get("request")
            if request is not None:
                request.respond_with_value(writer.detach_buffer())
        except Exception as exc:
            LOGGER.warning("Bluetooth Pair WinRT read failed: %s", exc, exc_info=True)
        finally:
            try:
                deferral.complete()
            except Exception:
                pass

    def _read_value_for_uuid(self, uuid_text: str) -> bytes:
        normalized = str(uuid_text or "").lower()
        if normalized == AUTOYOU_BLUETOOTH_PAIR_STATUS_UUID.lower():
            return self._last_status
        if normalized == AUTOYOU_BLUETOOTH_PAIR_TX_UUID.lower():
            try:
                return self._pending_notify_frames.get_nowait()
            except queue.Empty:
                return b""
        return b""

    def _write_characteristic(self, _sender: Any, args: Any) -> None:
        deferral = args.get_deferral()
        if deferral is None:
            return
        try:
            request_holder: Dict[str, Any] = {}

            async def get_request() -> None:
                request_holder["request"] = await args.get_request_async()

            asyncio.new_event_loop().run_until_complete(get_request())
            request = request_holder.get("request")
            if request is None:
                return
            reader = self._DataReader.from_buffer(request.value)
            payload = bytearray()
            for _index in range(reader.unconsumed_buffer_length):
                payload.append(reader.read_byte())
            loop = self._loop
            if loop is None or loop.is_closed():
                LOGGER.warning("Bluetooth Pair WinRT write received while the event loop is not available.")
                return
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._process_write_frame(bytes(payload)))
            )
            if request.option == self._GattWriteOption.WRITE_WITH_RESPONSE:
                request.respond()
        except Exception as exc:
            LOGGER.warning("Bluetooth Pair WinRT write failed: %s", exc, exc_info=True)
        finally:
            try:
                deferral.complete()
            except Exception:
                pass

    def _subscribe_characteristic(self, sender: Any, _args: Any) -> None:
        clients = getattr(sender, "subscribed_clients", None)
        self._subscribed_clients = list(clients) if clients is not None else []
        LOGGER.info("Bluetooth Pair WinRT client subscription count: %s", len(self._subscribed_clients))

    async def _process_write_frame(self, raw_frame: bytes) -> None:
        try:
            complete_frames = self._rx_stream.push(raw_frame)
            for raw_complete_frame in complete_frames:
                response_frames = await self.transport.receive_frame(raw_complete_frame)
                for frame in response_frames:
                    self._pending_notify_frames.put(frame)
                    await self._notify_frame(frame)
        except Exception as exc:
            self._rx_stream.reset()
            LOGGER.warning("Bluetooth Pair WinRT frame handling failed: %s", exc, exc_info=True)
            error_frames = chunk_message(
                _json_error(str(exc)),
                client_id="unknown",
                kind=KIND_ERROR,
                max_payload_bytes=self.max_payload_bytes,
            )
            for frame in error_frames:
                self._pending_notify_frames.put(frame)

    async def _notify_frame(self, frame: bytes) -> None:
        characteristic = self._tx_characteristic
        if characteristic is None:
            return
        writer = self._DataWriter()
        writer.write_bytes(frame)
        result = characteristic.notify_value_async(writer.detach_buffer())
        if inspect.isawaitable(result):
            await result


class BlessBluetoothPairingServer:
    """BLE GATT server adapter backed by the optional `bless` package.

    This adapter is intentionally thin. It exposes RX writes and TX
    notifications/readbacks; the pairing semantics remain in
    BluetoothPairingCommandHandler so WSL/Docker host bridges can reuse the same
    code without pretending to own a Bluetooth adapter.
    """

    def __init__(
        self,
        handler: BluetoothPairingCommandHandler,
        *,
        name: str = "AutoYou Bluetooth Pair",
        max_payload_bytes: int = DEFAULT_FRAME_PAYLOAD_BYTES,
    ) -> None:
        self.handler = handler
        self.name = name
        self.max_payload_bytes = max_payload_bytes
        self.transport = InMemoryBluetoothPairingTransport(
            handler,
            max_payload_bytes=max_payload_bytes,
        )
        self._rx_stream = BluetoothPairFrameStream()
        self._server: Any = None
        self._running = False
        self._last_error = ""
        self._pending_notify_frames: asyncio.Queue[bytes] = asyncio.Queue()
        self._last_status = b"stopped"
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def status(self) -> BluetoothPairingStatus:
        return BluetoothPairingStatus(
            enabled=True,
            running=self._running,
            transport="ble-gatt",
            error=self._last_error,
        )

    async def start(self) -> bool:
        if self._running:
            return True
        self._loop = asyncio.get_running_loop()
        try:
            from bless import BlessServer  # type: ignore
            from bless.backends.characteristic import (  # type: ignore
                GATTAttributePermissions,
                GATTCharacteristicProperties,
            )
        except Exception as exc:
            self._last_error = (
                "Bluetooth Pair BLE runtime is unavailable. Install optional "
                "`bless` support or run a host Bluetooth bridge."
            )
            LOGGER.warning("%s Detail: %s", self._last_error, exc)
            return False

        try:
            server = BlessServer(name=self.name)
            server.read_request_func = self._read_request
            server.write_request_func = self._write_request
            await self._maybe_await(server.add_new_service(AUTOYOU_BLUETOOTH_PAIR_SERVICE_UUID))
            await self._maybe_await(
                server.add_new_characteristic(
                    AUTOYOU_BLUETOOTH_PAIR_SERVICE_UUID,
                    AUTOYOU_BLUETOOTH_PAIR_RX_UUID,
                    GATTCharacteristicProperties.write
                    | GATTCharacteristicProperties.write_without_response,
                    None,
                    GATTAttributePermissions.writeable,
                )
            )
            await self._maybe_await(
                server.add_new_characteristic(
                    AUTOYOU_BLUETOOTH_PAIR_SERVICE_UUID,
                    AUTOYOU_BLUETOOTH_PAIR_TX_UUID,
                    GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
                    None,
                    GATTAttributePermissions.readable,
                )
            )
            await self._maybe_await(
                server.add_new_characteristic(
                    AUTOYOU_BLUETOOTH_PAIR_SERVICE_UUID,
                    AUTOYOU_BLUETOOTH_PAIR_STATUS_UUID,
                    GATTCharacteristicProperties.read,
                    b"ready",
                    GATTAttributePermissions.readable,
                )
            )
            await self._maybe_await(server.start())
            self._server = server
            self._running = True
            self._last_error = ""
            self._last_status = b"ready"
            LOGGER.info("Bluetooth Pair BLE GATT server advertising as %s", self.name)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.warning("Bluetooth Pair BLE GATT server failed to start: %s", exc, exc_info=True)
            await self.stop()
            return False

    async def stop(self) -> None:
        server = self._server
        self._server = None
        self._running = False
        self._rx_stream.reset()
        self._loop = None
        self._last_status = b"stopped"
        if server is not None:
            try:
                await self._maybe_await(server.stop())
            except Exception as exc:
                LOGGER.debug("Bluetooth Pair BLE stop failed: %s", exc)

    async def _maybe_await(self, value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    def _read_request(self, characteristic: Any, **_: Any) -> bytes:
        uuid_text = str(getattr(characteristic, "uuid", "") or "").lower()
        if uuid_text == AUTOYOU_BLUETOOTH_PAIR_STATUS_UUID.lower():
            return self._last_status
        try:
            return self._pending_notify_frames.get_nowait()
        except asyncio.QueueEmpty:
            return b""

    def _write_request(self, characteristic: Any, value: Any, **_: Any) -> None:
        uuid_text = str(getattr(characteristic, "uuid", "") or "").lower()
        if uuid_text and uuid_text != AUTOYOU_BLUETOOTH_PAIR_RX_UUID.lower():
            return
        raw = bytes(value or b"")
        loop = self._loop
        if loop is None or loop.is_closed():
            LOGGER.warning("Bluetooth Pair write received while the event loop is not available.")
            return
        loop.call_soon_threadsafe(lambda: asyncio.create_task(self._process_write_frame(raw)))

    async def _process_write_frame(self, raw_frame: bytes) -> None:
        try:
            complete_frames = self._rx_stream.push(raw_frame)
            for raw_complete_frame in complete_frames:
                response_frames = await self.transport.receive_frame(raw_complete_frame)
                for frame in response_frames:
                    await self._pending_notify_frames.put(frame)
                    await self._notify_next_frame(frame)
        except Exception as exc:
            self._rx_stream.reset()
            LOGGER.warning("Bluetooth Pair frame handling failed: %s", exc, exc_info=True)
            error_frames = chunk_message(
                _json_error(str(exc)),
                client_id="unknown",
                kind=KIND_ERROR,
                max_payload_bytes=self.max_payload_bytes,
            )
            for frame in error_frames:
                await self._pending_notify_frames.put(frame)

    async def _notify_next_frame(self, frame: bytes) -> None:
        server = self._server
        if server is None:
            return
        try:
            # bless backends differ slightly; prefer characteristic value update
            # when available and fall back to queue-backed read.
            services = getattr(server, "services", None)
            if isinstance(services, dict):
                characteristic = (
                    services.get(AUTOYOU_BLUETOOTH_PAIR_SERVICE_UUID, {})
                    .get("Characteristics", {})
                    .get(AUTOYOU_BLUETOOTH_PAIR_TX_UUID)
                )
                if characteristic is not None and hasattr(characteristic, "value"):
                    characteristic.value = frame
            update = getattr(server, "update_value", None)
            if callable(update):
                await self._maybe_await(
                    update(AUTOYOU_BLUETOOTH_PAIR_SERVICE_UUID, AUTOYOU_BLUETOOTH_PAIR_TX_UUID)
                )
        except Exception as exc:
            LOGGER.debug("Bluetooth Pair notify failed; client can read TX: %s", exc)


class DisabledBluetoothPairingServer:
    def __init__(self, reason: str = "Bluetooth Pair is disabled") -> None:
        self.reason = reason

    @property
    def status(self) -> BluetoothPairingStatus:
        return BluetoothPairingStatus(
            enabled=False,
            running=False,
            transport="disabled",
            error=self.reason,
        )

    async def start(self) -> bool:
        return False

    async def stop(self) -> None:
        return None


def create_bluetooth_pairing_server(
    handler: BluetoothPairingCommandHandler,
    *,
    name: str = "AutoYou Bluetooth Pair",
    max_payload_bytes: int = DEFAULT_FRAME_PAYLOAD_BYTES,
) -> Any:
    if platform.system() == "Windows":
        return WinRtBluetoothPairingServer(
            handler,
            name=name,
            max_payload_bytes=max_payload_bytes,
        )
    return BlessBluetoothPairingServer(
        handler,
        name=name,
        max_payload_bytes=max_payload_bytes,
    )
