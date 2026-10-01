# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""One paired, connected device on the real engine, for chat-delivery tests."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import pytest


class OpenChannel:
    """A connected device's data channel: keeps what the computer sends it."""

    def __init__(self):
        self.sent = []

    async def send_message(self, message):
        self.sent.append(message)
        return True


def connect_device(tmp_path, monkeypatch, *, session_id="synthetic-live", sender_id="synthetic-device"):
    """Pair and connect one device on the real engine.

    The engine, the identity registry and the conversation store are the real
    ones, so every id a test compares comes from the builders the server uses.
    Returns ``(webrtc, channel, identity, manager)``;
    ``manager.advance_conversation_thread(identity.owner_key)`` is the device
    starting a new conversation. Everything is written under ``tmp_path``.
    """
    sessions = pytest.importorskip("google.adk.sessions")
    import server
    from session_utils import MemoryIntegratedSessionManager
    from shared import session_execution

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "root"))
    monkeypatch.setattr(
        session_execution,
        "_GLOBAL_SESSION_EXECUTION_MANAGER",
        session_execution.SessionExecutionManager(),
    )
    manager = MemoryIntegratedSessionManager(
        db_path=str(tmp_path / "sessions.db"),
        adk_session_service=sessions.InMemorySessionService(),
        cognee_memory_enabled=False,
    )
    monkeypatch.setattr(server, "_get_conversation_session_manager", lambda: manager)
    webrtc = server.WebRTCManager()
    identity = server.bind_transport_chat_owner("local", sender_id, raw_session_id=session_id)
    channel = OpenChannel()
    webrtc.datachannel_managers[session_id] = channel
    return webrtc, channel, identity, manager
