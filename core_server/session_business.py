# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Attach authenticated connection adapters to current application handlers.

No connection setup, socket, codec or WebRTC object is created here. Canonical
owners come from the existing session authority. A transport callback cannot
choose another owner or erase a replacement channel's resources.
"""
from __future__ import annotations

import asyncio
from typing import Any
from core_server.session_dispatch import (bootstrap_application_session,
    dispatch_application_message, dispatch_bound_control_message)
from shared.datachannel_manager import MessageType
from shared.session_transport import SessionDenied
from shared.session_media import SessionStreamHandler


class SessionBusinessAdapter:
    def __init__(self, *, runtime: Any, engine: Any, grants: Any) -> None:
        self.runtime, self.engine, self.grants = runtime, engine, grants
        self._channels: dict[str, Any] = {}
        self._streams: dict[int, SessionStreamHandler] = {}

    def attach_stream_handler(self, lane: int, handler: SessionStreamHandler) -> None:
        if lane not in {7, 8, 9} or lane in self._streams:
            raise ValueError("invalid or already owned application stream lane")
        self._streams[lane] = handler

    async def binary(self, channel: Any, frame: Any) -> None:
        binding = channel.binding
        if self._channels.get(binding.transport_id) is not channel or \
                self.engine.datachannel_managers.get(binding.transport_id) is not channel or \
                frame.generation != binding.generation:
            raise SessionDenied("application stream adapter is closed or superseded")
        scope = {7: 'files', 8: 'media', 9: 'control'}.get(frame.lane)
        handler = self._streams.get(frame.lane)
        if scope is None or handler is None:
            raise SessionDenied("application stream capability is not attached")
        channel.registry.check(binding, scope=scope)
        await handler.receive(channel, frame)

    async def prepared(self, transport: Any, context: Any, channel: Any) -> None:
        binding = channel.binding
        grant = self.grants.grant_for_endpoint(context.remote_endpoint_id)
        identity = self.runtime.bind_transport_chat_owner(grant.origin_transport, grant.origin_sender_id,
            raw_session_id=context.transport_id, pairing_mode=grant.pairing_mode)
        if (identity.owner_key, identity.canonical_user_id, identity.canonical_session_id) != (
                binding.owner_key, binding.canonical_user_id, binding.conversation_key):
            self.runtime.get_session_execution_manager().release_transport_session(
                context.transport_id, expected_owner_key=identity.owner_key)
            raise SessionDenied("paired owner does not match the canonical session authority")
        if context.transport_id in self.engine.datachannel_managers:
            raise SessionDenied("application session already has an adapter")
        self.engine.remember_device_ownership(identity, grant.device_ownership)
        self._channels[context.transport_id] = channel
        self.engine.datachannel_managers[context.transport_id] = channel

        async def received(message: Any) -> None:
            transport.registry.check(binding)
            if self.engine.datachannel_managers.get(context.transport_id) is not channel:
                raise SessionDenied("application adapter has been replaced")
            if await dispatch_bound_control_message(self.engine, message,
                    trusted_transport_id=context.transport_id, channel=channel):
                return
            await dispatch_application_message(self.engine, message,
                trusted_transport_id=context.transport_id, session_id=context.transport_id)

        for kind in (MessageType.CHAT, MessageType.HTTP_REQUEST, MessageType.HTTP_REQUEST_CANCEL,
                MessageType.HTTP_STREAM_ABORT, MessageType.ERROR, MessageType.VOICE_CALL_CONTROL,
                MessageType.HTTP_WS_DATA, MessageType.HTTP_WS_CLOSE, MessageType.PAIRING_CONTROL,
                MessageType.ROOM_BRIDGE_CONTROL):
            channel.register_handler(kind, received)

    async def ready(self, _transport: Any, context: Any, channel: Any) -> None:
        if self._channels.get(context.transport_id) is not channel or not channel.is_ready:
            raise SessionDenied("application adapter is not ready")
        bootstrap_application_session(self.engine, context.transport_id, runtime=self.runtime)

    async def closed(self, context: Any, binding: Any, _user_requested: bool) -> None:
        channel = self._channels.pop(context.transport_id, None)
        if channel is None or self.engine.datachannel_managers.get(context.transport_id) is not channel:
            return
        del self.engine.datachannel_managers[context.transport_id]
        try:
            self.engine._suspend_room_bridge_transport(context.transport_id)
        finally:
            try:
                lease = self.engine.remote_desktop_control_leases_by_session.get(context.transport_id)
                if lease is not None:
                    await self.engine._release_remote_desktop_control(context.transport_id, expected_lease=lease)
            finally:
                try:
                    cleanup = [self.engine._cancel_session_message_tasks(context.transport_id)]
                    if binding is not None:
                        cleanup.extend(handler.closed(binding) for handler in self._streams.values())
                    results = await asyncio.gather(*cleanup, return_exceptions=True)
                    if any(isinstance(result, BaseException) for result in results):
                        raise RuntimeError("application adapter cleanup failed")
                finally:
                    if binding is not None:
                        self.runtime.get_session_execution_manager().release_transport_session(
                            context.transport_id, expected_owner_key=binding.owner_key)
