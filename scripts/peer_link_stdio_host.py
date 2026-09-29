# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-d4a4285b4b967133b6167ea3

#!/usr/bin/env python3
"""A real Python Peer Link host, driven over stdin/stdout.

`peer_link_live_test.py` owns the scenarios where Python drives both ends or
drives a phone over adb/simctl. This is the other shape: a harness written in
some *other* language needs a genuine peer on the far side of a real WebRTC
link, and needs to steer it. The Chrome extension's `tools/e2e-peerlink.mjs` is
the first such caller.

Nothing here is a stub. It is the same `PeerHub`, the same
`DataChannelManager`, the same aiortc peer connection the desktop client runs;
only the *driver* is a pipe instead of a GUI.

Protocol — one JSON object per line, both directions::

    <- {"event": "ready", "authenticator": "<43 chars>", "device_name": "..."}
    -> {"cmd": "offer", "text": "/peerpair\\n..."}
    <- {"event": "answer", "text": "/peerpair_answer\\n..."}
    <- {"event": "connected", "device_name": "...", "mode": "direct"}
    -> {"cmd": "chat", "text": "hello"}
    <- {"event": "chat", "text": "hi back", "from": "..."}
    -> {"cmd": "call", "video": false}      # host places a call
    -> {"cmd": "answer_call"}               # host answers a ringing guest
    -> {"cmd": "end_call"}
    <- {"event": "calls", "ringing": [...], "active": [...]}
    <- {"event": "track", "kind": "audio"}
    -> {"cmd": "quit"}

Usage::

    python3 scripts/peer_link_stdio_host.py --authenticator <43 chars>
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import argparse
import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-d4a4285b4b967133b6167ea3"


REPO_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AUTOYOU_TEST_ROOT", tempfile.mkdtemp(prefix="autoyou-peer-stdio-"))
for candidate in (REPO_ROOT, REPO_ROOT / "clients" / "python"):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from peer_link import (  # noqa: E402
    PeerAnswerAccepted,
    PeerAnswerRejected,
    PeerCapability,
    PeerHub,
    PeerIdentity,
    PeerOfferEnvelope,
    PeerPairCodec,
    PeerRelayPolicy,
)


def emit(event: str, **fields) -> None:
    """One JSON line on stdout. Everything human goes to stderr."""
    sys.stdout.write(json.dumps({"event": event, **fields}) + "\n")
    sys.stdout.flush()


def note(message: str) -> None:
    sys.stderr.write(f"[peer-host] {message}\n")
    sys.stderr.flush()


class StdioPeerHost:
    def __init__(self, authenticator: str, device_name: str, auto_answer: bool) -> None:
        self.device_name = device_name
        self.microphone = None
        self.hub = PeerHub(
            identity_provider=lambda: PeerIdentity(
                device_id=f"python-{uuid.uuid4().hex[:8]}",
                device_name=self.device_name,
            ),
            ice_servers_provider=lambda: [],
            datachannel_manager_factory=self._make_manager,
        )
        self.hub.authenticator = authenticator
        # The approval prompt is a human affordance; a pipe has no human, and
        # the consent this test is actually about is the *microphone* one,
        # which stays behind `answer_call` unless auto-answer was asked for.
        self.hub.auto_accept = True
        self.hub.auto_answer_calls = auto_answer
        self.hub.set_accept_incoming(True)
        self.hub.on_direct_chat = self._on_direct_chat
        self.hub.on_links_changed = self._on_links_changed
        self.hub.on_calls_changed = self._on_calls_changed
        self.hub.on_guest_track = self._on_guest_track
        self.hub.acquire_peer_call_audio = self._acquire_audio
        self.hub.release_peer_call_audio = self._release_audio
        self.hub.local_media_provider = lambda: (self.microphone, None)
        self._announced: set[str] = set()

    @staticmethod
    def _make_manager():
        from shared.datachannel_manager import DataChannelManager

        return DataChannelManager(role="client")

    # -- media -------------------------------------------------------------

    def _acquire_audio(self):
        # A silence generator stands in for a microphone: what is under test is
        # the consent gate and the signalling, not the audio content.
        from aiortc.mediastreams import AudioStreamTrack

        if self.microphone is None:
            self.microphone = AudioStreamTrack()
        return self.microphone

    def _release_audio(self) -> None:
        track, self.microphone = self.microphone, None
        if track is not None:
            track.stop()

    # -- hub callbacks -----------------------------------------------------

    def _on_direct_chat(self, chat) -> None:
        note(f"chat from {chat.device_name}: {chat.text!r}")
        emit("chat", text=chat.text, **{"from": chat.device_name, "link_id": chat.link_id})

    def _on_links_changed(self, snapshots) -> None:
        for snapshot in snapshots:
            if snapshot.connected and snapshot.link_id not in self._announced:
                self._announced.add(snapshot.link_id)
                note(f"connected: {snapshot.device_name} ({snapshot.subtitle})")
                emit(
                    "connected",
                    link_id=snapshot.link_id,
                    device_name=snapshot.device_name,
                    platform=snapshot.platform,
                    mode=snapshot.mode.value,
                    capabilities=sorted(capability.value for capability in snapshot.capabilities),
                )

    def _on_calls_changed(self, ringing, active) -> None:
        emit(
            "calls",
            ringing=[
                {"link_id": invite.link_id, "device_name": invite.device_name, "video": invite.wants_video}
                for invite in ringing
            ],
            active=sorted(active),
        )

    def _on_guest_track(self, link_id: str, track) -> None:
        note(f"track from {link_id[:8]}: {track.kind}")
        emit("track", link_id=link_id, kind=track.kind)

    # -- commands ----------------------------------------------------------

    def _first_link_id(self) -> str:
        snapshots = self.hub.snapshots()
        return snapshots[0].link_id if snapshots else ""

    async def handle(self, command: dict) -> bool:
        name = str(command.get("cmd") or "")
        if name == "offer":
            if not self.hub.submit_incoming_offer(str(command.get("text") or "")):
                emit("error", detail=self.hub.status)
                return True
            # auto_accept schedules the answer on the loop; wait for it.
            for _ in range(300):
                if self.hub.answer_text:
                    emit("answer", text=self.hub.answer_text)
                    self.hub.clear_answer_text()
                    return True
                await asyncio.sleep(0.1)
            emit("error", detail=f"no answer was produced: {self.hub.status}")
        elif name == "chat":
            link_id = str(command.get("link_id") or "") or self._first_link_id()
            emit("chat_sent", ok=self.hub.send_direct_chat(link_id, str(command.get("text") or "")))
        elif name == "call":
            link_id = str(command.get("link_id") or "") or self._first_link_id()
            emit("call_started", ok=self.hub.start_direct_call(link_id, bool(command.get("video"))))
        elif name == "answer_call":
            link_id = str(command.get("link_id") or "") or self._first_link_id()
            emit("call_answered", ok=self.hub.answer_direct_call(link_id, bool(command.get("video"))))
        elif name == "end_call":
            link_id = str(command.get("link_id") or "") or self._first_link_id()
            emit("call_ended", ok=self.hub.end_direct_call(link_id))
        elif name == "snapshot":
            emit(
                "snapshot",
                links=[
                    {
                        "link_id": item.link_id,
                        "device_name": item.device_name,
                        "mode": item.mode.value,
                        "connected": item.connected,
                    }
                    for item in self.hub.snapshots()
                ],
                status=self.hub.status,
            )
        elif name == "quit":
            return False
        else:
            emit("error", detail=f"unknown command {name!r}")
        return True

    async def run(self) -> None:
        emit("ready", authenticator=self.hub.authenticator, device_name=self.device_name)
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8").strip()
            if not text:
                continue
            try:
                command = json.loads(text)
            except ValueError:
                emit("error", detail="not JSON")
                continue
            try:
                if not await self.handle(command):
                    break
            except Exception as exc:  # noqa: BLE001 - report, never die mid-run
                emit("error", detail=f"{type(exc).__name__}: {exc}")
        await self.hub.close_all_links("the harness finished")


class StdioPeerGuest:
    """The other half: a real aiortc offerer, steered over the same pipe.

    Needed because a client under test is only half-covered by being a guest.
    Chrome hosting a link exercises an entirely different body of code — the
    hub, the answerer, the approval prompt, the ring — and something has to
    offer to it.
    """

    def __init__(self, authenticator: str, device_name: str, prefer_direct: bool) -> None:
        from aiortc import RTCPeerConnection

        self.authenticator = authenticator
        self.device_name = device_name
        self.prefer_direct = prefer_direct
        self.codec = PeerPairCodec()
        self.envelope: PeerOfferEnvelope | None = None
        self.pc = RTCPeerConnection()
        # Audio and video up front in `sendrecv`: AutoYou never renegotiates
        # after the one-shot offer/answer, so a call started later has to
        # attach its track to an m-line that already exists.
        self.pc.addTransceiver("audio", direction="sendrecv")
        self.pc.addTransceiver("video", direction="sendrecv")
        self.channel = self.pc.createDataChannel("chat")

        @self.channel.on("open")
        def _on_open() -> None:  # noqa: ANN202
            note("data channel open")
            emit("connected", device_name=self.device_name, mode="guest")

        @self.channel.on("message")
        def _on_message(raw) -> None:  # noqa: ANN001,ANN202
            if isinstance(raw, bytes):
                return
            try:
                message = json.loads(raw)
            except ValueError:
                return
            header = message.get("header") or {}
            payload = message.get("payload") or {}
            kind = str(header.get("message_type") or "")
            if kind == "chat":
                emit("chat", text=str(payload.get("message") or ""), **{"from": "host"})
            elif kind == "voice_call_control":
                emit("call_control", payload=payload)
            elif kind == "peer_control":
                emit("peer_control", payload=payload)

        @self.pc.on("track")
        def _on_track(track) -> None:  # noqa: ANN001,ANN202
            note(f"track from host: {track.kind}")
            emit("track", kind=track.kind)

    async def build_offer(self) -> str:
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        deadline = asyncio.get_running_loop().time() + 15
        # from __debug_provenance_i__ import or
        while self.pc.iceGatheringState != "complete":
            if asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(0.2)
        self.envelope = PeerOfferEnvelope(
            offer={"type": "offer", "sdp": self.pc.localDescription.sdp},
            device_id=f"python-guest-{uuid.uuid4().hex[:8]}",
            device_name=self.device_name,
            platform="python",
            requested=PeerRelayPolicy.encode(set(PeerCapability)),
            prefer_direct=self.prefer_direct,
        )
        return self.codec.build_offer_text(self.envelope, self.authenticator)

    async def apply_answer(self, answer_text: str) -> dict:
        from aiortc import RTCSessionDescription
        from aiortc.sdp import candidate_from_sdp

        result = self.codec.parse_answer_text(
            answer_text,
            self.authenticator,
            expected_invitation_id=self.envelope.invitation_id if self.envelope else None,
            expected_offer_nonce=self.envelope.offer_nonce if self.envelope else None,
        )
        if isinstance(result, PeerAnswerRejected):
            raise RuntimeError(f"the host declined: {result.reason}")
        assert isinstance(result, PeerAnswerAccepted)
        envelope = result.envelope
        await self.pc.setRemoteDescription(
            RTCSessionDescription(sdp=envelope.answer["sdp"], type="answer")
        )
        for candidate in envelope.candidates:
            try:
                value = str(candidate.get("candidate") or "")
                if not value:
                    continue
                line = value if value.startswith("candidate:") else f"candidate:{value}"
                parsed = candidate_from_sdp(line)
                parsed.sdpMid = candidate.get("sdpMid")
                parsed.sdpMLineIndex = candidate.get("sdpMLineIndex")
                await self.pc.addIceCandidate(parsed)
            except Exception as exc:  # noqa: BLE001
                note(f"rejected an answer candidate: {exc}")
        return {
            "device_name": envelope.device_name,
            "platform": envelope.platform,
            "mode": envelope.mode,
            "hop": envelope.hop,
            "capabilities": list(envelope.capabilities),
        }

    def _send(self, message: dict) -> bool:
        if self.channel.readyState != "open":
            return False
        self.channel.send(json.dumps(message))
        return True

    async def handle(self, command: dict) -> bool:
        import time as _time

        name = str(command.get("cmd") or "")
        if name == "offer":
            emit("offer", text=await self.build_offer())
        elif name == "answer":
            emit("answer_applied", **await self.apply_answer(str(command.get("text") or "")))
        elif name == "hello":
            # What a real guest sends the moment its channel opens; the host
            # answers it with peer_welcome.
            emit(
                "hello_sent",
                ok=self._send(
                    {
                        "header": {
                            "message_id": str(uuid.uuid4()),
                            "message_type": "peer_control",
                            "timestamp": _time.time(),
                        },
                        "payload": {
                            "event": "peer_hello",
                            "protocol": 3,
                            "device_id": "python-guest",
                            "device_name": self.device_name,
                            "platform": "python",
                            "requested": PeerRelayPolicy.encode(set(PeerCapability)),
                        },
                    }
                ),
            )
        elif name == "chat":
            emit(
                "chat_sent",
                ok=self._send(
                    {
                        "header": {
                            "message_id": str(uuid.uuid4()),
                            "message_type": "chat",
                            "timestamp": _time.time(),
                            "user_id": "python-guest",
                        },
                        "payload": {"message": str(command.get("text") or ""), "context": []},
                    }
                ),
            )
        elif name == "call":
            emit(
                "call_sent",
                ok=self._send(
                    {
                        "header": {
                            "message_id": str(uuid.uuid4()),
                            "message_type": "voice_call_control",
                            "timestamp": _time.time(),
                        },
                        "payload": {
                            "event": "call_state",
                            "active": True,
                            "video_active": bool(command.get("video")),
                            "platform": "python",
                        },
                    }
                ),
            )
        elif name == "end_call":
            emit(
                "call_sent",
                ok=self._send(
                    {
                        "header": {
                            "message_id": str(uuid.uuid4()),
                            "message_type": "voice_call_control",
                            "timestamp": _time.time(),
                        },
                        "payload": {"event": "call_state", "active": False, "video_active": False},
                    }
                ),
            )
        elif name == "quit":
            return False
        else:
            emit("error", detail=f"unknown command {name!r}")
        return True

    async def run(self) -> None:
        emit("ready", authenticator=self.authenticator, device_name=self.device_name, role="guest")
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8").strip()
            if not text:
                continue
            try:
                command = json.loads(text)
            except ValueError:
                emit("error", detail="not JSON")
                continue
            try:
                if not await self.handle(command):
                    break
            except Exception as exc:  # noqa: BLE001
                emit("error", detail=f"{type(exc).__name__}: {exc}")
        await self.pc.close()


class StdioDevicePeer:
    """A phone acting as the peer host, steered over the same pipe.

    The device drivers already exist in `peer_link_live_test.py` — launching
    the debug build with the offer in its environment and reassembling the
    answer out of the log. This wraps them so a harness in another language
    (the Chrome extension's) can put a real iPhone or Android emulator on the
    far side of its link, rather than only ever testing against Python.

    The offer has to be known before the app starts: the debug hook takes it
    at launch. That is why `offer` is the command that launches the app.
    """

    def __init__(self, platform: str, authenticator: str, udid: str) -> None:
        self.platform = platform
        self.authenticator = authenticator
        self.udid = udid
        self.marker = f"peer-{uuid.uuid4().hex[:12]}"
        self.pid: int | None = None
        self.since = ""
        self._live = None

    def _driver(self):
        if self._live is None:
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "peer_link_live_test", str(REPO_ROOT / "scripts" / "peer_link_live_test.py")
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._live = module
        return self._live

    async def _launch(self, offer: str) -> str:
        live = self._driver()
        loop = asyncio.get_running_loop()
        env = {
            "AUTOYOU_UITEST_PEERLINK": "host",
            "AUTOYOU_UITEST_PEER_PASSPHRASE": self.authenticator,
            "AUTOYOU_UITEST_PEER_DEVICE_NAME": f"{self.platform} test host",
            "AUTOYOU_UITEST_PEER_OFFER": offer,
            "AUTOYOU_UITEST_PEER_MARKER": self.marker,
            "AUTOYOU_PEER_LIVE_TEST_TOKEN": uuid.uuid4().hex,
        }
        if self.platform == "ios":
            self.since = live.ios_since()
            self.pid = await loop.run_in_executor(None, live.ios_launch, self.udid, env)
            note(f"iOS launched (pid {self.pid}); collecting the answer")
            return await loop.run_in_executor(
                None, lambda: live.ios_collect_blob(self.udid, "ANSWER", self.since, pid=self.pid)
            )
        await loop.run_in_executor(None, live.android_prepare)
        await loop.run_in_executor(None, live.grant_android_media_permissions)
        self.since = ""
        await loop.run_in_executor(None, live.android_launch, env)
        note("Android launched; collecting the answer")
        return await loop.run_in_executor(
            None, lambda: live.android_collect_blob("ANSWER", self.since)
        )

    async def _expect_log(self, marker: str, timeout: float) -> None:
        live = self._driver()
        loop = asyncio.get_running_loop()
        if self.platform == "ios":
            await loop.run_in_executor(
                None,
                lambda: live.ios_wait_for_logs(
                    self.udid, [marker], self.since, timeout=timeout, pid=self.pid
                ),
            )
        else:
            await loop.run_in_executor(
                None, lambda: live.android_wait_for_logs([marker], self.since, timeout=timeout)
            )

    async def handle(self, command: dict) -> bool:
        name = str(command.get("cmd") or "")
        if name == "offer":
            answer = await self._launch(str(command.get("text") or ""))
            emit("answer", text=answer, marker=self.marker)
        elif name == "expect_chat":
            text = str(command.get("text") or "")
            await self._expect_log(f"[PeerLinkTest] RECEIVED {text}", float(command.get("timeout") or 120))
            emit("chat_received", text=text)
        elif name == "quit":
            return False
        else:
            emit("error", detail=f"unknown command {name!r}")
        return True

    async def run(self) -> None:
        emit(
            "ready",
            authenticator=self.authenticator,
            device_name=f"{self.platform} test host",
            role=f"{self.platform}-host",
            marker=self.marker,
        )
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8").strip()
            if not text:
                continue
            try:
                command = json.loads(text)
            except ValueError:
                emit("error", detail="not JSON")
                continue
            try:
                if not await self.handle(command):
                    break
            except Exception as exc:  # noqa: BLE001
                emit("error", detail=f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authenticator", required=True)
    parser.add_argument(
        "--role", choices=("host", "guest", "ios-host", "android-host"), default="host"
    )
    parser.add_argument("--udid", default=os.environ.get("AUTOYOU_IOS_UDID", "booted"))
    parser.add_argument("--device-name", default="AutoYou Connect (stdio peer)")
    parser.add_argument("--prefer-direct", action="store_true")
    parser.add_argument(
        "--auto-answer",
        action="store_true",
        help="answer a guest's call without waiting for an answer_call command",
    )
    args = parser.parse_args()
    if not PeerPairCodec.is_valid_authenticator(args.authenticator):
        note("the authenticator must be 43 base64url characters")
        return 2
    if args.role in ("ios-host", "android-host"):
        platform = "ios" if args.role == "ios-host" else "android"
        asyncio.run(StdioDevicePeer(platform, args.authenticator, args.udid).run())
    elif args.role == "guest":
        asyncio.run(
            StdioPeerGuest(args.authenticator, args.device_name, args.prefer_direct).run()
        )
    else:
        asyncio.run(StdioPeerHost(args.authenticator, args.device_name, args.auto_answer).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
