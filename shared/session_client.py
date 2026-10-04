# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Stable application handlers over independently admitted client sessions.

An application registers handlers before it has a connection. Attaching a new
channel does not carry an old channel's chunks, acknowledgements or pending
sends into the new generation. Every send captures its owning channel.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from shared.session_transport import SessionDenied


class SessionMessageAdapter:
    def __init__(self) -> None:
        self.message_handlers: dict[Any, Callable] = {}
        self.session_id: str | None = None
        self._channel: Any = None
        self._periodic: asyncio.Task | None = None
        self._ping_event: Callable | None = None
        self._interceptor: Callable | None = None

    @property
    def is_ready(self) -> bool:
        return bool(self._channel is not None and self._channel.is_ready)

    @property
    def binding(self) -> Any:
        return self._channel.binding if self._channel is not None else None

    @property
    def on_ping_pong_event(self) -> Callable | None:
        return self._ping_event

    @on_ping_pong_event.setter
    def on_ping_pong_event(self, callback: Callable | None) -> None:
        self._ping_event = callback
        if self._channel is not None:
            self._channel.on_ping_pong_event = callback

    @property
    def relay_interceptor(self) -> Callable | None:
        return self._interceptor

    @relay_interceptor.setter
    def relay_interceptor(self, callback: Callable | None) -> None:
        self._interceptor = callback
        if self._channel is not None:
            self._channel.relay_interceptor = callback

    def register_handler(self, message_type: Any, handler: Callable) -> None:
        self.message_handlers[message_type] = handler
        if self._channel is not None:
            self._channel.register_handler(message_type, handler)

    def attach(self, channel: Any) -> None:
        channel.binding.validate()
        if self._channel is not None and self._channel is not channel:
            raise SessionDenied("previous client session must be retired before attachment")
        self._channel = channel
        self.session_id = channel.binding.transport_id
        for message_type, handler in self.message_handlers.items():
            channel.register_handler(message_type, handler)
        channel.on_ping_pong_event = self._ping_event
        channel.relay_interceptor = self._interceptor

    def detach(self, binding: Any) -> bool:
        if self._channel is None or self._channel.binding != binding:
            return False
        if self._periodic is not None:
            self._periodic.cancel()
            self._periodic = None
        self._channel = None
        self.session_id = None
        return True

    def set_session_id(self, session_id: str) -> None:
        if self._channel is None or session_id != self._channel.binding.transport_id:
            raise SessionDenied("client session requires verified admission")
        self._channel.set_session_id(session_id)

    async def send_message(self, message: Any, **options: Any) -> bool:
        channel = self._channel
        return bool(channel is not None and await channel.send_message(message, **options))

    async def send_ping(self, session_id: str | None = None) -> bool:
        channel = self._channel
        return bool(channel is not None and await channel.send_ping(session_id))

    async def send_ping_and_wait(self, session_id: str | None = None, timeout: float = 4.0) -> bool:
        channel = self._channel
        return bool(channel is not None and await channel.send_ping_and_wait(session_id, timeout=timeout))

    def get_metrics(self) -> dict[str, Any]:
        return self._channel.get_metrics() if self._channel is not None else {"transport": "iroh", "connected": False}

    async def start_periodic_tasks(self) -> None:
        if self._periodic is not None and not self._periodic.done():
            return
        channel = self._channel
        if channel is None or not channel.is_ready:
            return

        async def keepalive() -> None:
            while self._channel is channel and channel.is_ready:
                await asyncio.sleep(15)
                if self._channel is channel and channel.is_ready:
                    await channel.send_ping(channel.session_id)

        self._periodic = asyncio.create_task(keepalive(), name="autoyou-session-keepalive")

    def disconnect(self) -> None:
        if self._periodic is not None:
            self._periodic.cancel()
            self._periodic = None
        if self._channel is not None:
            self._channel.disconnect()

    async def cleanup(self) -> None:
        periodic = self._periodic
        self.disconnect()
        if periodic is not None:
            await asyncio.gather(periodic, return_exceptions=True)
        self._channel = None
        self.session_id = None
        self.message_handlers.clear()
