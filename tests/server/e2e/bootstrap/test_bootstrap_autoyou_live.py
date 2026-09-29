# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-a77af66dd307517a6a6faa0f

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import base64
import hashlib
import json
import os
import time
import urllib.request
import zlib

import pytest

from shared.pairing_cpace import (
    build_cpace_hello,
    complete_cpace_handshake,
    decrypt_cpace_message,
    encrypt_cpace_message,
)
from tests.server.e2e.bootstrap.harness import launch_bootstrap_server

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-a77af66dd307517a6a6faa0f"


pytestmark = pytest.mark.skipif(
    os.getenv("AUTOYOU_RUN_BOOTSTRAP_E2E") != "1",
    reason="Set AUTOYOU_RUN_BOOTSTRAP_E2E=1 to launch a live source server.",
)


def test_bootstrap_launches_source_server_and_accepts_configured_password(tmp_path):
    password = os.getenv("AUTOYOU_E2E_SERVER_PASSWORD", "1234")
    install = os.getenv("AUTOYOU_BOOTSTRAP_E2E_INSTALL") == "1"
    running = launch_bootstrap_server(
        runtime_root=tmp_path / "runtime",
        password=password,
        install=install,
    )
    try:
        status = running.wait_for_admin_status(timeout=10)
        assert status["status"] == "running"
        assert status["service"] == "AutoYou Admin Server"

        login = running.login()
        assert login["success"] is True
    finally:
        running.shutdown()


def test_bootstrap_live_secure_local_pair_datachannel_round_trip(tmp_path):
    pytest.importorskip("aiortc")

    password = os.getenv("AUTOYOU_E2E_SERVER_PASSWORD", "1234")
    install = os.getenv("AUTOYOU_BOOTSTRAP_E2E_INSTALL") == "1"
    running = launch_bootstrap_server(
        runtime_root=tmp_path / "runtime",
        password=password,
        install=install,
    )
    try:
        assert running.login()["success"] is True
        asyncio.run(_run_live_secure_local_pair_datachannel(running, password))
    finally:
        running.shutdown()


async def _run_live_secure_local_pair_datachannel(running, password: str) -> None:
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

    from shared.datachannel_manager import (
        DataChannelManager,
        DataChannelMessage,
        MessageHeader,
        MessageType,
    )

    client_pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    data_channel = client_pc.createDataChannel("autoyou", ordered=True)
    channel_manager = DataChannelManager(role="client")
    channel_manager.set_session_id("admin-web")
    channel_open = asyncio.Event()
    received_messages: list[DataChannelMessage] = []
    # from __debug_provenance_w__ import stripe
    raw_messages: list[str] = []

    async def _accept_message(message: DataChannelMessage) -> None:
        return None

    channel_manager.register_handler(MessageType.CHAT, _accept_message)
    channel_manager.register_handler(MessageType.VOICE_CALL_CONTROL, _accept_message)

    @data_channel.on("open")  # type: ignore[misc]
    def _on_open() -> None:
        channel_manager.set_datachannel(data_channel)
        channel_open.set()

    @data_channel.on("message")  # type: ignore[misc]
    def _on_message(data) -> None:
        raw = data if isinstance(data, str) else data.decode("utf-8", errors="replace")
        raw_messages.append(raw)

        async def _handle_raw_message() -> None:
            parsed = await channel_manager.handle_received_message(raw)
            if parsed is not None:
                received_messages.append(parsed)

        asyncio.create_task(_handle_raw_message())

    try:
        offer = await client_pc.createOffer()
        await client_pc.setLocalDescription(offer)
        await _wait_for_ice_gathering_complete(client_pc)

        answer = await asyncio.to_thread(
            _post_autopair_offer,
            running,
            password,
            {
                "type": client_pc.localDescription.type,
                "sdp": client_pc.localDescription.sdp,
            },
        )
        assert answer["type"] == "answer"
        await client_pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )

        await asyncio.wait_for(channel_open.wait(), timeout=20)

        ping_id = "bootstrap-e2e-ping"
        ping_message = DataChannelMessage(
            header=MessageHeader(
                message_id=ping_id,
                message_type=MessageType.PING,
                timestamp=time.time(),
                session_id="admin-web",
                user_id="bootstrap-e2e-client",
            ),
            payload={"keepalive": True},
        )
        assert await channel_manager.send_message(ping_message)

        pong = await _wait_for_pong(received_messages, raw_messages, ping_id)
        assert pong.payload.get("ping_id") == ping_id
        assert pong.header.message_type == MessageType.PONG
        assert pong.header.session_id in {"admin-web", "bootstrap-e2e-client"}
    finally:
        await client_pc.close()


def _post_autopair_offer(running, password: str, offer: dict) -> dict:
    sender_id = "bootstrap-e2e-client"
    session = _start_secure_local_pair_session(running, password, sender_id)
    payload = {
        "hash": hashlib.sha256(password.encode("utf-8")).hexdigest(),
        "offer": offer,
        "candidates": [],
        "_autoyou_pairing_platform": "local",
        "_autoyou_sender_id": sender_id,
    }
    request = urllib.request.Request(
        f"{running.admin_base_url}/api/autopair?session_id={sender_id}",
        data=encrypt_cpace_message(json.dumps(payload, separators=(",", ":")), session).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "text/plain",
            "Content-Type": "text/plain",
            "Origin": running.admin_base_url,
            "X-AutoYou-Platform": "local",
            "X-AutoYou-Session-Id": sender_id,
        },
    )
    with running.opener.open(request, timeout=30) as response:
        response_text = response.read().decode("utf-8")
    assert response_text.startswith("/autopair_answer\ncpace1:")
    wrapped = json.loads(_decode_pairing_payload(decrypt_cpace_message(response_text.split("\n", 1)[1], session)))
    return wrapped["answer"]


def _start_secure_local_pair_session(running, password: str, sender_id: str):
    hello_env, handshake = build_cpace_hello(password, purpose="autopair")
    request = urllib.request.Request(
        f"{running.admin_base_url}/api/autopair_hello?session_id={sender_id}",
        data=hello_env.encode("utf-8"),
        method="POST",
        headers={
            "Accept": "text/plain",
            "Content-Type": "text/plain",
            "Origin": running.admin_base_url,
            "X-AutoYou-Platform": "local",
            "X-AutoYou-Session-Id": sender_id,
        },
    )
    with running.opener.open(request, timeout=30) as response:
        response_text = response.read().decode("utf-8")
    assert response_text.startswith("/autopair_hello_answer\n")
    return complete_cpace_handshake(response_text.split("\n", 1)[1], handshake)


def _decode_pairing_payload(payload: str) -> str:
    if not str(payload or "").startswith("z:"):
        return payload
    encoded = str(payload)[2:]
    encoded += "=" * ((-len(encoded)) % 4)
    return zlib.decompress(base64.urlsafe_b64decode(encoded)).decode("utf-8")


async def _wait_for_ice_gathering_complete(peer_connection, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while peer_connection.iceGatheringState != "complete":
        if asyncio.get_running_loop().time() >= deadline:
            return
        await asyncio.sleep(0.05)


async def _wait_for_pong(
    received_messages: list,
    raw_messages: list[str],
    ping_id: str,
    timeout: float = 20.0,
):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        for message in received_messages:
            message_type = getattr(message.header.message_type, "value", message.header.message_type)
            if message_type != "pong":
                continue
            if isinstance(message.payload, dict) and message.payload.get("ping_id") == ping_id:
                return message
        await asyncio.sleep(0.05)
    raise AssertionError(f"Timed out waiting for pong {ping_id}. Received: {raw_messages!r}")
