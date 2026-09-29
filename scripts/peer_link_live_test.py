# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-2a906dabc364a66ddab400ce

#!/usr/bin/env python3
"""Live cross-client Peer Link check.

Drives a real WebRTC client-to-client link between the Python client and a
mobile client running the debug Peer Link hook, then asserts that a chat turn
crosses it. This is the one part of the feature unit tests cannot cover: the
ICE handshake, the answerer, and the DataChannel framing only exist at runtime.

Roles
-----
``--android-host``  Python is the guest; the Android app answers.
``--android-guest`` Python is the host; the Android app offers.
``--ios-host``      Python is the guest; the iOS app answers.
``--ios-guest``     Python is the host; the iOS app offers.
``--python-only``   Both ends are Python, for a device-free smoke test.

Usage::

    python3 scripts/peer_link_live_test.py --python-only
    python3 scripts/peer_link_live_test.py --android-host
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import argparse
import asyncio
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-2a906dabc364a66ddab400ce"


REPO_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AUTOYOU_TEST_ROOT", tempfile.mkdtemp(prefix="autoyou-peer-link-live-"))
for candidate in (REPO_ROOT, REPO_ROOT / "clients" / "python"):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from peer_link import (  # noqa: E402
    PeerAnswerAccepted,
    PeerAnswerRejected,
    PeerCapability,
    PeerHostMode,
    PeerHub,
    PeerIdentity,
    PeerOfferEnvelope,
    PeerPairCodec,
    PeerRelayPolicy,
)

ADB = os.environ.get("ADB") or str(Path.home() / "Library/Android/sdk/platform-tools/adb")
ANDROID_PACKAGE = os.environ.get("AUTOYOU_ANDROID_PACKAGE", "com.autoyou.app")
ANDROID_ACTIVITY = f"{ANDROID_PACKAGE}/com.autoyou.app.MainActivity"
PASSPHRASE = secrets.token_urlsafe(32)
IOS_BUNDLE_ID = os.environ.get("AUTOYOU_IOS_BUNDLE_ID", "com.autoyou.app")
ANDROID_AUTOMATION_TOKEN = ""


def log(message: str) -> None:
    print(f"[peer-link] {message}", flush=True)


def unescape_blob(blob: str) -> str:
    """Device loggers fold newlines to spaces; peer parsers lstrip, so the blob
    is already usable. Kept as a seam in case a platform needs real escaping."""
    return blob


# ── Android driver ───────────────────────────────────────────────────────────


def adb(*args: str, check: bool = True) -> str:
    try:
        result = subprocess.run([ADB, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except subprocess.TimeoutExpired:
        raise TimeoutError("Android test command timed out; check the emulator connection") from None
    if check and result.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def android_reset() -> None:
    """Force-stop the app so a scenario starts from a clean process.

    Successive `am start` calls reuse the running process, so peer connections
    from an earlier scenario would linger and compete for sockets and the audio
    device. Only call this before a scenario's *first* launch — killing the
    process mid-flow would drop the offer it is holding.
    """
    adb("shell", "am", "force-stop", ANDROID_PACKAGE, check=False)
    time.sleep(1.5)


def android_launch(extras: Dict[str, str]) -> None:
    # `adb shell` re-parses its arguments through the device's sh, so every
    # value is single-quoted and newlines are folded to spaces. Peer command
    # parsers all lstrip after the prefix, so a space reads the same as "\n".
    args = ["shell", "am", "start", "-n", ANDROID_ACTIVITY]
    launch_extras = dict(extras)
    if ANDROID_AUTOMATION_TOKEN and launch_extras:
        launch_extras["AUTOYOU_UITEST_TOKEN"] = ANDROID_AUTOMATION_TOKEN
    for key, value in launch_extras.items():
        flattened = value.replace("\r", "").replace("\n", " ")
        args += ["--es", key, shlex.quote(flattened)]
    try:
        result = subprocess.run([ADB, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except subprocess.TimeoutExpired:
        raise TimeoutError("Android test app launch timed out; check the emulator connection") from None
    if result.returncode != 0:
        # Intent values can contain credentials; never copy the command into an error.
        raise RuntimeError(f"adb app launch failed: {result.stderr.strip()}")


def android_prepare(timeout: float = 30.0) -> None:
    """Start a fresh debug process and read its per-process automation token."""
    global ANDROID_AUTOMATION_TOKEN
    ANDROID_AUTOMATION_TOKEN = ""
    android_reset()
    adb("logcat", "-c", check=False)
    android_launch({})
    deadline = time.time() + timeout
    pattern = re.compile(r"\[PeerLinkTest\] TOKEN ([A-Za-z0-9-]+)")
    while time.time() < deadline:
        output = adb("logcat", "-d", "-v", "brief", check=False)
        match = pattern.search(output)
        if match:
            ANDROID_AUTOMATION_TOKEN = match.group(1)
            return
        time.sleep(0.5)
    raise TimeoutError("Android debug app did not publish its Peer Link automation token")


def grant_android_media_permissions() -> None:
    for permission in ("android.permission.RECORD_AUDIO", "android.permission.CAMERA"):
        adb("shell", "pm", "grant", ANDROID_PACKAGE, permission, check=False)


def android_collect_blob(kind: str, since: str, timeout: float = 90.0) -> str:
    """Reassemble a `[PeerLinkTest] <kind> i/n <slice>` run out of logcat."""
    pattern = re.compile(rf"\[PeerLinkTest\] {kind} (\d+)/(\d+) (.*)$")
    done_pattern = re.compile(rf"\[PeerLinkTest\] {kind} DONE (\d+)")
    slices: Dict[int, str] = {}
    expected_total: Optional[int] = None
    expected_length: Optional[int] = None
    deadline = time.time() + timeout

    while time.time() < deadline:
        output = adb("logcat", "-d", "-v", "brief", check=False)
        for line in output.splitlines():
            match = pattern.search(line)
            if match:
                index, total, payload = int(match.group(1)), int(match.group(2)), match.group(3)
                slices[index] = payload
                expected_total = total
                continue
            done = done_pattern.search(line)
            if done:
                expected_length = int(done.group(1))
        if (
            expected_total is not None
            and len(slices) == expected_total
            and expected_length is not None
        ):
            blob = "".join(slices[i] for i in range(expected_total))
            if len(blob) == expected_length:
                return unescape_blob(blob)
        time.sleep(1.5)

    raise TimeoutError(
        f"Timed out collecting the {kind} blob from Android "
        f"(got {len(slices)}/{expected_total} slices)"
    )


# ── iOS driver ───────────────────────────────────────────────────────────────


def ios_since() -> str:
    """A `log show --start` stamp for right now, in the simulator's local time."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 2))


def simctl(*args: str, check: bool = True) -> str:
    result = subprocess.run(["xcrun", "simctl", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise RuntimeError(f"simctl {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def ios_launch(udid: str, env: Dict[str, str]) -> int:
    simctl("terminate", udid, IOS_BUNDLE_ID, check=False)
    environ = dict(os.environ)
    for key, value in env.items():
        environ[f"SIMCTL_CHILD_{key}"] = value
    result = subprocess.run(
        ["xcrun", "simctl", "launch", udid, IOS_BUNDLE_ID],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environ,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"iOS app launch failed: {result.stderr.strip()}")
    match = re.search(r":\s*(\d+)\s*$", result.stdout)
    if match is None:
        raise RuntimeError("iOS app launch did not report a process identifier")
    return int(match.group(1))


def grant_ios_media_permissions(udid: str) -> None:
    simctl("boot", udid, check=False)
    simctl("bootstatus", udid, "-b")
    for service in ("microphone", "camera"):
        simctl("privacy", udid, "grant", service, IOS_BUNDLE_ID, check=False)


def cleanup_mobile_scenario(name: str, udid: str) -> None:
    if "android" in name:
        if ANDROID_AUTOMATION_TOKEN:
            try:
                android_launch({"AUTOYOU_UITEST_PEER_CLEANUP": "1"})
                time.sleep(0.5)
            except RuntimeError:
                pass
        adb("shell", "am", "force-stop", ANDROID_PACKAGE, check=False)
    if "ios" in name:
        simctl("terminate", udid, IOS_BUNDLE_ID, check=False)
        try:
            ios_clear_answer_file(udid)
        except (OSError, RuntimeError):
            pass


def android_wait_for_state(marker: str, since: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    needle = f"[PeerLinkTest] STATE {marker}"
    while time.time() < deadline:
        if needle in adb("logcat", "-d", "-v", "brief", check=False):
            return
        time.sleep(1.5)
    raise TimeoutError(f"Android never reported STATE {marker}")


def ios_wait_for_state(
    udid: str,
    marker: str,
    since: str,
    timeout: float = 120.0,
    *,
    pid: Optional[int] = None,
) -> None:
    deadline = time.time() + timeout
    needle = f"[PeerLinkTest] STATE {marker}"
    while time.time() < deadline:
        if needle in ios_log_since(udid, since, pid=pid):
            return
        time.sleep(2.0)
    raise TimeoutError(f"iOS never reported STATE {marker}")


def android_wait_for_logs(markers: List[str], since: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        output = adb("logcat", "-d", "-v", "brief", check=False)
        missing = [marker for marker in markers if marker not in output]
        if not missing:
            return
        time.sleep(1.5)
    raise TimeoutError(f"Android never reported {missing!r}")


def ios_wait_for_logs(
    udid: str,
    markers: List[str],
    since: str,
    timeout: float = 120.0,
    *,
    pid: Optional[int] = None,
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        output = ios_log_since(udid, since, pid=pid)
        missing = [marker for marker in markers if marker not in output]
        if not missing:
            return
        time.sleep(2.0)
    raise TimeoutError(f"iOS never reported {missing!r}")


def ios_answer_path(udid: str) -> Path:
    container = simctl("get_app_container", udid, IOS_BUNDLE_ID, "data").strip()
    return Path(container) / "Documents" / "peer-link-answer.txt"


def ios_clear_answer_file(udid: str) -> None:
    ios_answer_path(udid).unlink(missing_ok=True)


def ios_write_answer_file(udid: str, answer: str) -> None:
    target = ios_answer_path(udid)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(answer, encoding="utf-8")
    os.replace(temporary, target)
    log("wrote the answer to the iOS app container")


def ios_log_since(
    udid: str,
    since: str,
    timeout: float = 30.0,
    *,
    pid: Optional[int] = None,
    tag: str = "[PeerLinkTest]",
) -> str:
    """`log show` scoped to this run.

    The window matters: without `--start`, a blob from an earlier run sits in
    the same buffer and the harness happily "collects" it, then waits forever
    for a device that never saw the offer.
    """
    predicate = f'eventMessage CONTAINS "{tag}"'
    if pid is not None:
        predicate = f"processIdentifier == {pid} AND {predicate}"
    result = subprocess.run(
        ["xcrun", "simctl", "spawn", udid, "log", "show", "--start", since,
         "--style", "compact",
         "--predicate", predicate],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"iOS log query failed: {result.stderr.strip()}")
    return result.stdout


def ios_collect_blob(
    udid: str,
    kind: str,
    since: str,
    timeout: float = 120.0,
    *,
    pid: Optional[int] = None,
) -> str:
    """Reassemble a `[PeerLinkTest] <kind> i/n <slice>` run out of the sim log.

    Polls `log show` rather than following `log stream`, so a slice emitted
    between the launch and the reader starting is still picked up.
    """
    pattern = re.compile(rf"\[PeerLinkTest\] {kind} (\d+)/(\d+) (.*?)\s*$")
    done_pattern = re.compile(rf"\[PeerLinkTest\] {kind} DONE (\d+)")
    deadline = time.time() + timeout

    while time.time() < deadline:
        output = ios_log_since(udid, since, pid=pid)
        slices: Dict[int, str] = {}
        expected_total: Optional[int] = None
        expected_length: Optional[int] = None
        for line in output.splitlines():
            match = pattern.search(line)
            if match:
                slices[int(match.group(1))] = match.group(3)
                expected_total = int(match.group(2))
                continue
            done = done_pattern.search(line)
            if done:
                expected_length = int(done.group(1))
        if (
            expected_total is not None
            and len(slices) == expected_total
            and expected_length is not None
        ):
            blob = "".join(slices[i] for i in range(expected_total))
            if len(blob) == expected_length:
                return unescape_blob(blob)
        time.sleep(2.0)

    raise TimeoutError(f"Timed out collecting the {kind} blob from iOS")


# ── Python ends ──────────────────────────────────────────────────────────────


class PythonPeerHost:
    """A minimal Python peer host: hub plus the ICE servers it answers with."""

    def __init__(self) -> None:
        self.received: List[str] = []
        self.hub = PeerHub(
            identity_provider=lambda: PeerIdentity(
                device_id=f"python-{uuid.uuid4().hex[:8]}",
                device_name="AutoYou Connect (test host)",
            ),
            ice_servers_provider=lambda: [],
            datachannel_manager_factory=self._make_manager,
        )
        self.hub.authenticator = PASSPHRASE
        self.hub.auto_accept = True
        self.hub.set_accept_incoming(True)
        self.hub.on_direct_chat = self._on_direct_chat
        # A silence generator stands in for a real microphone: the point of the
        # test is the consent gate and the signalling, not the audio content.
        self.hub.acquire_peer_call_audio = self._acquire_audio
        self.hub.release_peer_call_audio = self._release_audio
        self.hub.local_media_provider = lambda: (self.microphone, None)
        self.hub.on_guest_track = lambda link_id, track: log(
            f"host received a {track.kind} track from {link_id[:8]}"
        )
        self.microphone = None

    @staticmethod
    def _make_manager():
        from shared.datachannel_manager import DataChannelManager

        return DataChannelManager(role="client")

    def _acquire_audio(self):
        from aiortc.mediastreams import AudioStreamTrack

        if self.microphone is None:
            self.microphone = AudioStreamTrack()
        return self.microphone

    def _release_audio(self) -> None:
        track, self.microphone = self.microphone, None
        if track is not None:
            track.stop()

    def _on_direct_chat(self, chat) -> None:
        log(f"host received chat from {chat.device_name}: {chat.text!r}")
        self.received.append(chat.text)

    async def answer(self, offer_text: str) -> str:
        assert self.hub.submit_incoming_offer(offer_text), self.hub.status
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.hub.answer_text:
                return self.hub.answer_text
            await asyncio.sleep(0.2)
        raise TimeoutError(f"Python host never produced an answer ({self.hub.status})")

    async def wait_for_connected_guest(self, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for snapshot in self.hub.snapshots():
                if snapshot.connected:
                    log(f"host sees guest connected: {snapshot.device_name} ({snapshot.subtitle})")
                    return
            await asyncio.sleep(0.5)
        raise TimeoutError("No guest reached the connected state on the Python host")

    def send_chat(self, text: str) -> None:
        if self.hub.broadcast_direct_chat(text) != 1:
            raise RuntimeError("Python host could not send direct chat to its guest")


class PythonPeerGuest:
    """A minimal Python peer guest: an aiortc offerer over a peer data channel."""

    def __init__(self) -> None:
        from aiortc import RTCPeerConnection

        self.pc = RTCPeerConnection()
        self.channel = self.pc.createDataChannel("chat")
        self.opened = asyncio.Event()
        self.received: List[dict] = []

        @self.channel.on("open")
        def _on_open() -> None:  # noqa: ANN202
            log("guest data channel open")
            self.opened.set()

        @self.channel.on("message")
        def _on_message(raw) -> None:  # noqa: ANN001,ANN202
            if isinstance(raw, bytes):
                return
            try:
                self.received.append(json.loads(raw))
            except Exception:
                pass

    async def build_offer(self, *, prefer_direct: bool = False) -> str:
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        await self._wait_for_ice()
        return PeerPairCodec().build_offer_text(
            PeerOfferEnvelope(
                offer={"type": "offer", "sdp": self.pc.localDescription.sdp},
                device_id=f"python-guest-{uuid.uuid4().hex[:8]}",
                device_name="AutoYou Connect (test guest)",
                platform="python",
                requested=PeerRelayPolicy.encode(set(PeerCapability)),
                prefer_direct=prefer_direct,
            ),
            PASSPHRASE,
        )

    async def _wait_for_ice(self, timeout: float = 15.0) -> None:
        deadline = time.time() + timeout
        while self.pc.iceGatheringState != "complete" and time.time() < deadline:
            await asyncio.sleep(0.2)

    async def apply_answer(self, answer_text: str) -> None:
        from aiortc import RTCSessionDescription
        from aiortc.sdp import candidate_from_sdp

        result = PeerPairCodec().parse_answer_text(answer_text, PASSPHRASE)
        if isinstance(result, PeerAnswerRejected):
            raise RuntimeError(f"Peer host declined: {result.reason}")
        assert isinstance(result, PeerAnswerAccepted)
        envelope = result.envelope
        log(
            f"answer from {envelope.device_name} ({envelope.platform}) "
            f"mode={envelope.mode} hop={envelope.hop} caps={envelope.capabilities}"
        )
        await self.pc.setRemoteDescription(
            RTCSessionDescription(sdp=envelope.answer["sdp"], type="answer")
        )
        for candidate in envelope.candidates or []:
            raw = str(candidate.get("candidate") or "")
            if not raw:
                continue
            line = raw if raw.startswith("candidate:") else f"candidate:{raw}"
            obj = candidate_from_sdp(line)
            obj.sdpMid = candidate.get("sdpMid")
            obj.sdpMLineIndex = candidate.get("sdpMLineIndex")
            await self.pc.addIceCandidate(obj)
        return envelope

    def send_call_state(self, active: bool, video: bool = False) -> None:
        self.channel.send(
            json.dumps(
                {
                    "header": {
                        "message_id": str(uuid.uuid4()),
                        "message_type": "voice_call_control",
                        "timestamp": time.time(),
                    },
                    "payload": {
                        "event": "call_state",
                        "active": active,
                        "video_active": video,
                        "platform": "python",
                    },
                }
            )
        )

    async def wait_for_voice_event(self, predicate, timeout: float = 15.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for message in list(self.received):
                header = message.get("header") or {}
                if header.get("message_type") != "voice_call_control":
                    continue
                payload = message.get("payload") or {}
                if predicate(payload):
                    return payload
            await asyncio.sleep(0.2)
        raise TimeoutError("no matching voice_call_control arrived")

    def send_chat(self, text: str) -> None:
        self.channel.send(
            json.dumps(
                {
                    "header": {
                        "message_id": str(uuid.uuid4()),
                        "message_type": "chat",
                        "timestamp": time.time(),
                    },
                    "payload": {"message": text, "context": [], "metadata": {}},
                }
            )
        )

    async def wait_for_chat(self, text: str, timeout: float = 20.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any((message.get("payload") or {}).get("message") == text for message in self.received):
                return
            await asyncio.sleep(0.2)
        raise TimeoutError(f"Python guest never received direct chat {text!r}")

    async def close(self) -> None:
        await self.pc.close()


# ── Scenarios ────────────────────────────────────────────────────────────────


# Every device wait below runs on a worker thread.
#
# These helpers poll `adb logcat` / `simctl log show` in a `while` loop with
# `time.sleep`. Called directly from a scenario coroutine they block the event
# loop, and everything the loop owes stops with them: aiortc's ICE, DTLS and
# SCTP timers, and — the way this was found — the `send_message` task the peer
# hub had just created. A Python host would "send" a chat, hand control to a
# blocking log wait, and the send would still be sitting in the loop's queue
# when the wait timed out. Both roles, both platforms, every time.
async def awaited(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(fn, *args, **kwargs)


async def scenario_python_only() -> bool:
    log("scenario: Python guest -> Python host (no devices)")
    host = PythonPeerHost()
    guest = PythonPeerGuest()
    try:
        offer = await guest.build_offer()
        log(f"guest offer built ({len(offer)} chars)")
        answer = await host.answer(offer)
        log(f"host answer built ({len(answer)} chars)")
        envelope = await guest.apply_answer(answer)
        assert PeerHostMode.from_wire(envelope.mode) is PeerHostMode.DIRECT, envelope.mode

        await asyncio.wait_for(guest.opened.wait(), timeout=45)
        await host.wait_for_connected_guest()

        marker = f"peer-link-live-{uuid.uuid4().hex[:6]}"
        guest.send_chat(marker)
        deadline = time.time() + 20
        while time.time() < deadline and marker not in host.received:
            await asyncio.sleep(0.3)
        assert marker in host.received, "host never received the guest chat turn"
        log("chat crossed the peer link")

        # DIRECT mode must refuse browser traffic with a readable reason.
        guest.channel.send(
            json.dumps(
                {
                    "header": {
                        "message_id": str(uuid.uuid4()),
                        "message_type": "http_request",
                        "timestamp": time.time(),
                    },
                    "payload": {"request_id": "req-1", "method": "GET", "url": "http://x/"},
                }
            )
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            errors = [m for m in guest.received if m.get("header", {}).get("message_type") == "error"]
            if errors:
                log(f"direct-mode browser refusal: {errors[0]['payload']['error']}")
                break
            await asyncio.sleep(0.3)
        else:
            raise AssertionError("DIRECT mode did not refuse the browser request")
        return True
    finally:
        await guest.close()
        await host.hub.close_all_links("test finished")


async def scenario_direct_call() -> bool:
    """DIRECT-mode calling, including the consent gate on the microphone."""
    log("scenario: DIRECT-mode peer call (ring -> answer -> end)")
    host = PythonPeerHost()
    guest = PythonPeerGuest()
    try:
        answer = await host.answer(await guest.build_offer())
        await guest.apply_answer(answer)
        await asyncio.wait_for(guest.opened.wait(), timeout=45)
        await host.wait_for_connected_guest()

        # The host announces voice readiness itself: no server will.
        readiness = await guest.wait_for_voice_event(
            lambda payload: payload.get("event") == "readiness"
        )
        assert readiness.get("state") == "ready", readiness
        log("host announced voice readiness on the direct link")

        # A guest call must ring, not open the host's microphone.
        guest.send_call_state(True)
        deadline = time.time() + 15
        while time.time() < deadline and not host.hub.incoming_calls():
            await asyncio.sleep(0.2)
        invites = host.hub.incoming_calls()
        assert invites, "the guest's call never rang on the host"
        assert not host.hub.active_call_link_ids(), "the microphone opened without an answer"
        log(f"host is ringing for {invites[0].device_name} with the microphone still closed")

        # Answering opens it and tells the guest the call is up.
        link_id = invites[0].link_id
        assert host.hub.answer_direct_call(link_id), host.hub.status
        answered = await guest.wait_for_voice_event(
            lambda payload: payload.get("event") == "call_state" and payload.get("active") is True
        )
        assert answered.get("platform") == "python", answered
        assert host.hub.active_call_link_ids() == {link_id}
        log("host answered and the guest saw the call go active")

        # Hanging up tells the guest and closes the microphone.
        assert host.hub.end_direct_call(link_id)
        ended = await guest.wait_for_voice_event(
            lambda payload: payload.get("event") == "call_state" and payload.get("active") is False
        )
        assert ended is not None
        assert not host.hub.active_call_link_ids()
        log("host ended the call and the guest saw it hang up")
        return True
    finally:
        await guest.close()
        await host.hub.close_all_links("test finished")


async def scenario_android_host() -> bool:
    log("scenario: Python guest -> Android host")
    since = time.strftime("%m-%d %H:%M:%S.000", time.localtime())
    android_prepare()
    guest = PythonPeerGuest()
    marker = f"peer-{uuid.uuid4().hex[:12]}"
    try:
        offer = await guest.build_offer(prefer_direct=True)
        log(f"guest offer built ({len(offer)} chars); handing it to Android")
        android_launch(
            {
                "AUTOYOU_UITEST_PEERLINK": "host",
                "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
                "AUTOYOU_UITEST_PEER_DEVICE_NAME": "Android test host",
                "AUTOYOU_UITEST_PEER_OFFER": offer,
                "AUTOYOU_UITEST_PEER_MARKER": marker,
            }
        )
        answer = await awaited(android_collect_blob, "ANSWER", since)
        log(f"android answer collected ({len(answer)} chars)")
        await guest.apply_answer(answer)
        await asyncio.wait_for(guest.opened.wait(), timeout=60)
        log("data channel open against the Android host")

        await guest.wait_for_chat(f"{marker}:host")
        guest.send_chat(f"{marker}:guest")
        await awaited(android_wait_for_logs, [f"[PeerLinkTest] RECEIVED {marker}:guest"], since)
        return True
    finally:
        await guest.close()


def local_model_for_emulator() -> Dict[str, str]:
    """The Peer Assistant engine an emulator can reach on this computer.

    An emulator reaches the host's loopback as 10.0.2.2, so an Ollama on this
    computer becomes the phone's "local model server". Without one the
    scenario still runs, on the model-free automatic reply.
    """
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
            models = [item.get("name", "") for item in json.load(response).get("models", [])]
    except Exception:  # noqa: BLE001 - no local model is a supported outcome
        models = []
    chosen = os.environ.get("AUTOYOU_TEST_OLLAMA_MODEL") or next(
        (name for name in models if name and "embed" not in name), ""
    )
    if not chosen:
        return {"engine": "basic"}
    return {"engine": "local_server", "endpoint": "http://10.0.2.2:11434", "model": chosen}


def local_model_for_ios_simulator() -> Dict[str, str]:
    engine = local_model_for_emulator()
    if engine["engine"] == "local_server":
        engine["endpoint"] = "http://127.0.0.1:11434"
    return engine


async def scenario_android_assistant() -> bool:
    """A desktop-style guest asks; the Android host's own AI answers.

    Proves the Peer Assistant end to end on a real device: the reply arrives on
    the data channel stamped ``auto_reply`` with an "(AI)" sender label, and a
    message another assistant wrote is never answered (the loop guard).
    """
    log("scenario: Python guest -> Android host with its AI replying")
    since = time.strftime("%m-%d %H:%M:%S.000", time.localtime())
    android_prepare()
    guest = PythonPeerGuest()
    marker = f"peer-{uuid.uuid4().hex[:12]}"
    engine = local_model_for_emulator()
    log(f"assistant engine: {engine.get('engine')} {engine.get('model', '')}".rstrip())
    assistant = {
        "mode": "auto_bot",
        "instructions": "Answer for Sam in one short sentence.",
        "knowledge": "Sam is at the gym until 6pm and will call back after that.",
        **engine,
    }

    def ai_replies() -> List[dict]:
        return [
            message for message in guest.received
            if (message.get("header") or {}).get("message_type") == "chat"
            and ((message.get("payload") or {}).get("metadata") or {}).get("auto_reply") is True
        ]

    try:
        offer = await guest.build_offer(prefer_direct=True)
        android_launch(
            {
                "AUTOYOU_UITEST_PEERLINK": "host",
                "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
                "AUTOYOU_UITEST_PEER_DEVICE_NAME": "Sam's Pixel",
                "AUTOYOU_UITEST_PEER_OFFER": offer,
                "AUTOYOU_UITEST_PEER_MARKER": marker,
                "AUTOYOU_UITEST_PEER_ASSISTANT": json.dumps(assistant),
            }
        )
        await awaited(android_wait_for_logs, ["[PeerLinkTest] ASSISTANT auto_bot"], since)
        answer = await awaited(android_collect_blob, "ANSWER", since)
        await guest.apply_answer(answer)
        await asyncio.wait_for(guest.opened.wait(), timeout=60)
        log("data channel open against the Android host")
        await guest.wait_for_chat(f"{marker}:host")

        guest.send_chat("Hi! Is Sam around? Where is he right now?")
        deadline = time.time() + 120
        while not ai_replies() and time.time() < deadline:
            await asyncio.sleep(0.5)
        replies = ai_replies()
        if not replies:
            raise TimeoutError("the Android host's assistant never replied")
        metadata = replies[0]["payload"]["metadata"]
        text = replies[0]["payload"].get("message", "")
        log(f"assistant reply via {metadata.get('auto_reply_engine')} as {metadata.get('agent_display_name')!r}: {text!r}")
        if metadata.get("agent_display_name") != "Sam's Pixel (AI)":
            raise AssertionError(f"unexpected sender label {metadata.get('agent_display_name')!r}")
        if metadata.get("auto_reply_engine") != engine["engine"]:
            raise AssertionError(f"reply came from {metadata.get('auto_reply_engine')}, expected {engine['engine']}")
        if not text.strip():
            raise AssertionError("the assistant sent an empty reply")

        # Another assistant's message is never answered: no ping-pong between AIs.
        before = len(ai_replies())
        guest.channel.send(json.dumps({
            "header": {"message_id": str(uuid.uuid4()), "message_type": "chat", "timestamp": time.time()},
            "payload": {"message": "This is an automatic reply from another AI.", "context": [],
                        "metadata": {"auto_reply": True, "agent_display_name": "Other (AI)"}},
        }))
        await asyncio.sleep(12)
        if len(ai_replies()) != before:
            raise AssertionError("the host's assistant answered another assistant")
        log("loop guard held: no reply to another assistant")
        return True
    finally:
        await guest.close()


async def scenario_desktop_to_android_assistant() -> bool:
    """The desktop app's own runtime starts the link; the phone hosts and its AI answers.

    Uses ``v2.runtime.worker.DesktopRuntime`` - the process behind the Windows
    and macOS apps - exactly as the native shell drives it: ``client.connect``
    in peer mode, the Android host's reply through ``pair.answer``, then an
    ordinary ``client.send``. The reply must reach the desktop chat labelled as
    the phone's AI.
    """
    log("scenario: desktop runtime guest -> Android host with its AI replying")
    from v2.runtime.worker import DesktopRuntime

    since = time.strftime("%m-%d %H:%M:%S.000", time.localtime())
    android_prepare()
    events: List[tuple] = []
    runtime = DesktopRuntime(lambda *event: events.append(event))
    engine = local_model_for_emulator()
    log(f"assistant engine: {engine.get('engine')} {engine.get('model', '')}".rstrip())
    marker = f"peer-{uuid.uuid4().hex[:12]}"
    try:
        offer = await runtime.dispatch("client.connect", {
            "mode": "peer", "password": PASSPHRASE, "accept_terms": True, "direct": True,
            "web_port": 18000 + secrets.randbelow(1000), "display_name": "Maya's desktop"})
        android_launch({
            "AUTOYOU_UITEST_PEERLINK": "host",
            "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
            "AUTOYOU_UITEST_PEER_DEVICE_NAME": "Sam's Pixel",
            "AUTOYOU_UITEST_PEER_OFFER": offer["offer"],
            "AUTOYOU_UITEST_PEER_MARKER": marker,
            "AUTOYOU_UITEST_PEER_ASSISTANT": json.dumps({
                "mode": "auto_bot", "instructions": "Answer for Sam in one short sentence.",
                "knowledge": "Sam is at the gym until 6pm and will call back after that.", **engine}),
        })
        answer = await awaited(android_collect_blob, "ANSWER", since)
        await runtime.dispatch("pair.answer", {"response": answer})
        deadline = time.time() + 60
        while not (runtime.client and runtime.client.snapshot().get("connected")) and time.time() < deadline:
            await asyncio.sleep(0.2)
        if not (runtime.client and runtime.client.snapshot().get("connected")):
            raise TimeoutError("the desktop runtime never connected to the Android host")
        log("desktop runtime connected to the Android host")

        await runtime.dispatch("client.send", {"message": "Hi! Is Sam around? Where is he right now?"})

        def ai_chats() -> List[dict]:
            return [value for name, value in events if name == "client.chat"
                    and (value.get("payload") or {}).get("metadata", {}).get("auto_reply") is True]
        deadline = time.time() + 120
        while not ai_chats() and time.time() < deadline:
            await asyncio.sleep(0.5)
        if not ai_chats():
            raise TimeoutError("the desktop never received the phone's AI reply")
        reply = ai_chats()[0]
        log(f"desktop shows {reply['sender']!r}: {reply['payload'].get('message')!r}")
        if reply["sender"] != "Sam's Pixel (AI)":
            raise AssertionError(f"the desktop labelled the reply {reply['sender']!r}")
        return True
    finally:
        await runtime.close()


async def scenario_desktop_to_ios_assistant(udid: str) -> bool:
    """The desktop runtime starts a real link; the iOS host answers with AI."""
    log("scenario: desktop runtime guest -> iOS host with its AI replying")
    from v2.runtime.worker import DesktopRuntime

    since = ios_since()
    events: List[tuple] = []
    runtime = DesktopRuntime(lambda *event: events.append(event))
    # from __debug_provenance_h__ import revenue
    engine = local_model_for_ios_simulator()
    hold_seconds = 120
    marker = f"peer-{uuid.uuid4().hex[:12]}"
    assistant = {
        "mode": "auto_bot",
        "instructions": "Answer in one short sentence. If asked for the test code in the owner's notes, return it exactly.",
        "assistantName": "Nori",
        "knowledge": "The test code is blue-lantern-42.",
        "hybridDelaySeconds": 45,
        "remember": False,
        "endpoint": "",
        "model": "",
        **engine,
    }
    log(f"assistant engine: {engine.get('engine')} {engine.get('model', '')}".rstrip())
    try:
        offer = await runtime.dispatch("client.connect", {
            "mode": "peer", "password": PASSPHRASE, "accept_terms": True, "direct": True,
            "web_port": 18000 + secrets.randbelow(1000), "display_name": "AutoYou macOS test guest"})
        pid = ios_launch(udid, {
            "AUTOYOU_UITEST_PEERLINK": "host",
            "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
            "AUTOYOU_UITEST_PEER_DEVICE_NAME": "iOS assistant host",
            "AUTOYOU_UITEST_PEER_OFFER": offer["offer"],
            "AUTOYOU_UITEST_PEER_MARKER": marker,
            "AUTOYOU_UITEST_PEER_ASSISTANT": json.dumps(assistant),
            "AUTOYOU_UITEST_PEER_HOLD_SECONDS": str(hold_seconds),
            "AUTOYOU_PEER_LIVE_TEST_TOKEN": uuid.uuid4().hex,
        })
        await awaited(ios_wait_for_logs, udid, [f"[PeerLinkTest] ASSISTANT auto_bot {engine['engine']}"], since, pid=pid)
        answer = await awaited(ios_collect_blob, udid, "ANSWER", since, pid=pid)
        await runtime.dispatch("pair.answer", {"response": answer})
        deadline = time.time() + 60
        while not (runtime.client and runtime.client.snapshot().get("connected")) and time.time() < deadline:
            await asyncio.sleep(0.2)
        if not (runtime.client and runtime.client.snapshot().get("connected")):
            raise TimeoutError("the desktop runtime never connected to the iOS host")
        log("desktop runtime connected to the iOS host")

        def host_marker_received() -> bool:
            return any(name == "client.chat" and (value.get("payload") or {}).get("message") == marker + ":host"
                       for name, value in events)
        deadline = time.time() + 20
        while not host_marker_received() and time.time() < deadline:
            await asyncio.sleep(0.2)
        if not host_marker_received():
            raise TimeoutError("the iOS host's direct chat did not reach the desktop")

        await runtime.dispatch("client.send", {"message": "What is the test code?"})
        def ai_chats() -> List[dict]:
            return [value for name, value in events if name == "client.chat"
                    and (value.get("payload") or {}).get("metadata", {}).get("auto_reply") is True]
        deadline = time.time() + 120
        while not ai_chats() and time.time() < deadline:
            await asyncio.sleep(0.5)
        replies = ai_chats()
        if not replies:
            raise TimeoutError("the iOS host's assistant never replied")
        payload = replies[0]["payload"]
        metadata = payload.get("metadata") or {}
        text = payload.get("message", "")
        log(f"iOS assistant replied via {metadata.get('auto_reply_engine')} as {metadata.get('agent_display_name')!r}: {text!r}")
        if metadata.get("agent_display_name") != "Nori (AI)":
            raise AssertionError(f"unexpected sender label {metadata.get('agent_display_name')!r}")
        if metadata.get("auto_reply_engine") != engine["engine"]:
            raise AssertionError(f"reply came from {metadata.get('auto_reply_engine')}, expected {engine['engine']}")
        if "blue-lantern-42" not in text:
            raise AssertionError("the iOS assistant did not use the configured system prompt and notes")
        return True
    finally:
        simctl("terminate", udid, IOS_BUNDLE_ID, check=False)
        await runtime.close()


async def scenario_android_guest() -> bool:
    log("scenario: Android guest -> Python host")
    since = time.strftime("%m-%d %H:%M:%S.000", time.localtime())
    android_prepare()
    host = PythonPeerHost()
    marker = f"peer-{uuid.uuid4().hex[:12]}"
    try:
        android_launch(
            {
                "AUTOYOU_UITEST_PEERLINK": "guest",
                "AUTOYOU_UITEST_PEER_DIRECT": "1",
                "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
                "AUTOYOU_UITEST_PEER_DEVICE_NAME": "Android test guest",
                "AUTOYOU_UITEST_PEER_MARKER": marker,
            }
        )
        offer = await awaited(android_collect_blob, "OFFER", since)
        log(f"android offer collected ({len(offer)} chars)")
        answer = await host.answer(offer)
        android_launch(
            {
                "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
                "AUTOYOU_UITEST_PEER_ANSWER": answer,
            }
        )
        await host.wait_for_connected_guest(timeout=90)
        deadline = time.time() + 20
        while time.time() < deadline and f"{marker}:guest" not in host.received:
            await asyncio.sleep(0.2)
        assert f"{marker}:guest" in host.received, "Python host never received Android chat"
        host.send_chat(f"{marker}:host")
        await awaited(android_wait_for_logs, [f"[PeerLinkTest] RECEIVED {marker}:host"], since)
        return True
    finally:
        await host.hub.close_all_links("test finished")


async def scenario_ios_host(udid: str) -> bool:
    log("scenario: Python guest -> iOS host")
    since = ios_since()
    guest = PythonPeerGuest()
    marker = f"peer-{uuid.uuid4().hex[:12]}"
    try:
        offer = await guest.build_offer(prefer_direct=True)
        log(f"guest offer built ({len(offer)} chars); handing it to iOS")
        pid = ios_launch(
            udid,
            {
                "AUTOYOU_UITEST_PEERLINK": "host",
                "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
                "AUTOYOU_UITEST_PEER_DEVICE_NAME": "iOS test host",
                "AUTOYOU_UITEST_PEER_OFFER": offer,
                "AUTOYOU_UITEST_PEER_MARKER": marker,
                "AUTOYOU_PEER_LIVE_TEST_TOKEN": uuid.uuid4().hex,
            },
        )
        answer = await awaited(ios_collect_blob, udid, "ANSWER", since, pid=pid)
        log(f"ios answer collected ({len(answer)} chars)")
        await guest.apply_answer(answer)
        await asyncio.wait_for(guest.opened.wait(), timeout=60)
        log("data channel open against the iOS host")
        await guest.wait_for_chat(f"{marker}:host")
        guest.send_chat(f"{marker}:guest")
        await awaited(ios_wait_for_logs, udid, [f"[PeerLinkTest] RECEIVED {marker}:guest"], since, pid=pid)
        return True
    finally:
        await guest.close()


async def scenario_ios_guest(udid: str) -> bool:
    log("scenario: iOS guest -> Python host")
    since = ios_since()
    host = PythonPeerHost()
    marker = f"peer-{uuid.uuid4().hex[:12]}"
    try:
        ios_clear_answer_file(udid)
        pid = ios_launch(
            udid,
            {
                "AUTOYOU_UITEST_PEERLINK": "guest",
                "AUTOYOU_UITEST_PEER_DIRECT": "1",
                "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
                "AUTOYOU_UITEST_PEER_DEVICE_NAME": "iOS test guest",
                "AUTOYOU_UITEST_PEER_MARKER": marker,
                "AUTOYOU_PEER_LIVE_TEST_TOKEN": uuid.uuid4().hex,
            },
        )
        offer = await awaited(ios_collect_blob, udid, "OFFER", since, pid=pid)
        log(f"ios offer collected ({len(offer)} chars)")
        answer = await host.answer(offer)
        # Relaunching would drop the peer connection the offer belongs to, and a
        # running process keeps its original environment, so the answer goes
        # into the app container for the guest hook to pick up.
        await awaited(ios_write_answer_file, udid, answer)
        await host.wait_for_connected_guest(timeout=120)
        deadline = time.time() + 20
        while time.time() < deadline and f"{marker}:guest" not in host.received:
            await asyncio.sleep(0.2)
        assert f"{marker}:guest" in host.received, "Python host never received iOS chat"
        host.send_chat(f"{marker}:host")
        await awaited(ios_wait_for_logs, udid, [f"[PeerLinkTest] RECEIVED {marker}:host"], since, pid=pid)
        return True
    finally:
        await host.hub.close_all_links("test finished")


async def scenario_android_to_ios(
    udid: str,
    call_kind: Optional[str] = None,
    call_initiator: str = "guest",
) -> bool:
    """The headline case: an Android guest linking to an iOS host."""
    label = f" with {call_initiator}-initiated {call_kind} call" if call_kind else ""
    log(f"scenario: Android guest -> iOS host{label}")
    since = time.strftime("%m-%d %H:%M:%S.000", time.localtime())
    ios_window = ios_since()
    android_prepare()
    grant_android_media_permissions()
    grant_ios_media_permissions(udid)
    marker = f"peer-{uuid.uuid4().hex[:12]}"

    ios_host_env = {
        "AUTOYOU_UITEST_PEERLINK": "host",
        "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
        "AUTOYOU_UITEST_PEER_DEVICE_NAME": "iOS test host",
        "AUTOYOU_PEER_LIVE_TEST_TOKEN": uuid.uuid4().hex,
        "AUTOYOU_UITEST_PEER_MARKER": marker,
    }
    if call_kind:
        if call_initiator == "host":
            ios_host_env["AUTOYOU_UITEST_PEER_HOST_CALL"] = call_kind
            ios_host_env["AUTOYOU_UITEST_PEER_CALL_SECONDS"] = "6"
        else:
            ios_host_env["AUTOYOU_UITEST_PEER_AUTOANSWER_CALL"] = "1"
            ios_host_env["AUTOYOU_UITEST_PEER_CALL"] = call_kind

    android_launch(
        {
            "AUTOYOU_UITEST_PEERLINK": "guest",
            "AUTOYOU_UITEST_PEER_DIRECT": "1",
            "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
            "AUTOYOU_UITEST_PEER_DEVICE_NAME": "Android test guest",
            "AUTOYOU_UITEST_PEER_MARKER": marker,
            **(
                {"AUTOYOU_UITEST_PEER_AUTOANSWER_GUEST_CALL": "1"}
                if call_kind and call_initiator == "host"
                else ({"AUTOYOU_UITEST_PEER_CALL": call_kind} if call_kind else {})
            ),
        }
    )
    offer = await awaited(android_collect_blob, "OFFER", since)
    log(f"android offer collected ({len(offer)} chars)")

    ios_host_env["AUTOYOU_UITEST_PEER_OFFER"] = offer
    ios_pid = ios_launch(udid, ios_host_env)
    answer = await awaited(ios_collect_blob, udid, "ANSWER", ios_window, pid=ios_pid)
    log(f"ios answer collected ({len(answer)} chars)")

    android_launch(
        {
            "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
            "AUTOYOU_UITEST_PEER_ANSWER": answer,
            "AUTOYOU_UITEST_PEER_MARKER": marker,
            **(
                {
                    "AUTOYOU_UITEST_PEER_AUTOANSWER_GUEST_CALL": "1",
                    "AUTOYOU_UITEST_PEER_CALL_DURATION_MS": "6000",
                }
                if call_kind and call_initiator == "host"
                else ({
                    "AUTOYOU_UITEST_PEER_CALL": call_kind,
                    "AUTOYOU_UITEST_PEER_CALL_DURATION_MS": "6000",
                } if call_kind else {})
            ),
        }
    )
    await awaited(android_wait_for_state, "connected", since)
    android_markers = [f"[PeerLinkTest] RECEIVED {marker}:host"]
    ios_markers = [f"[PeerLinkTest] RECEIVED {marker}:guest"]
    if call_kind:
        android_markers += ["[PeerLinkTest] CALL ACTIVE guest",
                            "[PeerLinkTest] CALL END guest",
                            "[PeerLinkTest] MEDIA LOCAL audio guest",
                            "[PeerLinkTest] MEDIA REMOTE audio guest"]
        ios_markers += ["[PeerLinkTest] CALL ACTIVE host",
                        "[PeerLinkTest] MEDIA LOCAL audio host",
                        "[PeerLinkTest] MEDIA REMOTE audio host"]
        if call_initiator == "host":
            android_markers += [f"[PeerLinkTest] CALL RING guest {call_kind}",
                                "[PeerLinkTest] CALL ANSWER guest requested"]
            ios_markers += [f"[PeerLinkTest] CALL START host {call_kind} ok",
                            "[PeerLinkTest] CALL END host requested"]
        else:
            android_markers += [f"[PeerLinkTest] CALL START guest {call_kind} requested",
                                "[PeerLinkTest] CALL END guest requested"]
            ios_markers += ["[PeerLinkTest] CALL RING host audio",
                            "[PeerLinkTest] CALL END host"]
        if call_kind == "video":
            android_markers += ["[PeerLinkTest] CALL VIDEO guest requested",
                                "[PeerLinkTest] MEDIA LOCAL video guest"]
            ios_markers += ["[PeerLinkTest] CALL VIDEO host active",
                            "[PeerLinkTest] MEDIA REMOTE video host"]
            if call_initiator == "host":
                android_markers += ["[PeerLinkTest] MEDIA REMOTE video guest"]
                ios_markers += ["[PeerLinkTest] MEDIA LOCAL video host"]
    await awaited(android_wait_for_logs, android_markers, since, timeout=180)
    await awaited(ios_wait_for_logs, udid, ios_markers, ios_window, timeout=180, pid=ios_pid)
    log("both direct-chat markers crossed and the requested call completed")
    return True


async def scenario_ios_to_android(
    udid: str,
    call_kind: Optional[str] = None,
    call_initiator: str = "guest",
) -> bool:
    """The mirror case: an iOS guest linking to an Android host."""
    label = f" with {call_initiator}-initiated {call_kind} call" if call_kind else ""
    log(f"scenario: iOS guest -> Android host{label}")
    since = time.strftime("%m-%d %H:%M:%S.000", time.localtime())
    ios_window = ios_since()
    android_prepare()
    grant_android_media_permissions()
    grant_ios_media_permissions(udid)
    marker = f"peer-{uuid.uuid4().hex[:12]}"

    ios_guest_env = {
        "AUTOYOU_UITEST_PEERLINK": "guest",
        "AUTOYOU_UITEST_PEER_DIRECT": "1",
        "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
        "AUTOYOU_UITEST_PEER_DEVICE_NAME": "iOS test guest",
        "AUTOYOU_PEER_LIVE_TEST_TOKEN": uuid.uuid4().hex,
        "AUTOYOU_UITEST_PEER_MARKER": marker,
    }
    if call_kind:
        if call_initiator == "host":
            ios_guest_env["AUTOYOU_UITEST_PEER_AUTOANSWER_GUEST_CALL"] = "1"
        else:
            ios_guest_env["AUTOYOU_UITEST_PEER_CALL"] = call_kind
            ios_guest_env["AUTOYOU_UITEST_PEER_CALL_SECONDS"] = "6"
    ios_clear_answer_file(udid)
    ios_pid = ios_launch(udid, ios_guest_env)
    offer = await awaited(ios_collect_blob, udid, "OFFER", ios_window, pid=ios_pid)
    log(f"ios offer collected ({len(offer)} chars)")

    android_launch(
        {
            "AUTOYOU_UITEST_PEERLINK": "host",
            "AUTOYOU_UITEST_PEER_PASSPHRASE": PASSPHRASE,
            "AUTOYOU_UITEST_PEER_DEVICE_NAME": "Android test host",
            "AUTOYOU_UITEST_PEER_OFFER": offer,
            "AUTOYOU_UITEST_PEER_MARKER": marker,
            **(
                {
                    "AUTOYOU_UITEST_PEER_HOST_CALL": call_kind,
                    "AUTOYOU_UITEST_PEER_CALL_DURATION_MS": "6000",
                }
                if call_kind and call_initiator == "host"
                else ({"AUTOYOU_UITEST_PEER_CALL": call_kind} if call_kind else {})
            ),
        }
    )
    answer = await awaited(android_collect_blob, "ANSWER", since)
    log(f"android answer collected ({len(answer)} chars)")

    await awaited(ios_write_answer_file, udid, answer)
    await awaited(android_wait_for_state, "guest-connected", since)
    android_markers = [f"[PeerLinkTest] RECEIVED {marker}:guest"]
    ios_markers = [f"[PeerLinkTest] RECEIVED {marker}:host"]
    if call_kind:
        android_markers += ["[PeerLinkTest] CALL ACTIVE host",
                            "[PeerLinkTest] CALL END host",
                            "[PeerLinkTest] MEDIA LOCAL audio host",
                            "[PeerLinkTest] MEDIA REMOTE audio host"]
        ios_markers += ["[PeerLinkTest] CALL ACTIVE guest",
                        "[PeerLinkTest] CALL END guest",
                        "[PeerLinkTest] MEDIA LOCAL audio guest",
                        "[PeerLinkTest] MEDIA REMOTE audio guest"]
        if call_initiator == "host":
            android_markers += [f"[PeerLinkTest] CALL START host {call_kind} ok",
                                "[PeerLinkTest] CALL END host requested"]
            ios_markers += [f"[PeerLinkTest] CALL RING guest {call_kind}",
                            "[PeerLinkTest] CALL ANSWER guest requested"]
        else:
            android_markers += [f"[PeerLinkTest] CALL RING host {call_kind}",
                                "[PeerLinkTest] CALL ANSWER host requested"]
        if call_kind == "video":
            android_markers += ["[PeerLinkTest] CALL VIDEO host active",
                                "[PeerLinkTest] MEDIA LOCAL video host",
                                "[PeerLinkTest] MEDIA REMOTE video host"]
            ios_markers += ["[PeerLinkTest] CALL VIDEO guest active",
                            "[PeerLinkTest] MEDIA LOCAL video guest",
                            "[PeerLinkTest] MEDIA REMOTE video guest"]
    await awaited(android_wait_for_logs, android_markers, since, timeout=180)
    await awaited(ios_wait_for_logs, udid, ios_markers, ios_window, timeout=180, pid=ios_pid)
    log("both direct-chat markers crossed and the requested call completed")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-only", action="store_true")
    parser.add_argument("--direct-call", action="store_true")
    parser.add_argument("--android-host", action="store_true")
    parser.add_argument("--android-guest", action="store_true")
    parser.add_argument("--desktop-to-android-assistant", action="store_true",
                        help="the desktop runtime starts the link; the Android host's AI answers")
    parser.add_argument("--desktop-to-ios-assistant", action="store_true",
                        help="the desktop runtime starts the link; the iOS host's AI answers")
    parser.add_argument("--android-assistant", action="store_true",
                        help="the Android host's Peer Assistant answers a guest (uses a local Ollama when present)")
    parser.add_argument("--ios-host", action="store_true")
    parser.add_argument("--ios-guest", action="store_true")
    parser.add_argument("--android-to-ios", action="store_true")
    parser.add_argument("--ios-to-android", action="store_true")
    parser.add_argument(
        "--cross-mobile-matrix",
        action="store_true",
        help="run audio/video calls with both host and guest initiating in both platform directions",
    )
    parser.add_argument("--udid", default=os.environ.get("AUTOYOU_IOS_UDID", "booted"))
    args = parser.parse_args()

    scenarios = []
    if args.python_only:
        scenarios.append(("python-only", scenario_python_only()))
    if args.direct_call:
        scenarios.append(("direct-call", scenario_direct_call()))
    if args.android_host:
        scenarios.append(("android-host", scenario_android_host()))
    if args.android_guest:
        scenarios.append(("android-guest", scenario_android_guest()))
    if args.android_assistant:
        scenarios.append(("android-assistant", scenario_android_assistant()))
    if args.desktop_to_android_assistant:
        scenarios.append(("desktop-to-android-assistant", scenario_desktop_to_android_assistant()))
    if args.desktop_to_ios_assistant:
        scenarios.append(("desktop-to-ios-assistant", scenario_desktop_to_ios_assistant(args.udid)))
    if args.ios_host:
        scenarios.append(("ios-host", scenario_ios_host(args.udid)))
    if args.ios_guest:
        scenarios.append(("ios-guest", scenario_ios_guest(args.udid)))
    if args.android_to_ios:
        scenarios.append(("android-to-ios", scenario_android_to_ios(args.udid)))
    if args.ios_to_android:
        scenarios.append(("ios-to-android", scenario_ios_to_android(args.udid)))
    if args.cross_mobile_matrix:
        for call_initiator in ("guest", "host"):
            for call_kind in ("audio", "video"):
                scenarios.append((
                    f"android-to-ios-{call_initiator}-{call_kind}",
                    scenario_android_to_ios(args.udid, call_kind, call_initiator),
                ))
                scenarios.append((
                    f"ios-to-android-{call_initiator}-{call_kind}",
                    scenario_ios_to_android(args.udid, call_kind, call_initiator),
                ))
    if not scenarios:
        parser.error("pick at least one scenario")

    failures = 0
    for name, coro in scenarios:
        try:
            asyncio.run(coro)
            log(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001 - a harness reports, it does not raise
            failures += 1
            log(f"FAIL {name}: {type(exc).__name__}: {exc}")
        finally:
            cleanup_mobile_scenario(name, args.udid)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
