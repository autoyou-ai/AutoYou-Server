# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-50c32806b4a0f03eddb7e7e4

#!/usr/bin/env python3
"""Live Android/iOS Lobby interoperability and Computer-bridge checks."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import os
import re
import shlex
import socket
import subprocess
import time
import uuid
from collections.abc import Callable, Iterable

import peer_link_live_test as peer

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-50c32806b4a0f03eddb7e7e4"


HOST = re.compile(r"\[RoomLiveTest\] HOST ([a-z0-9]{12}) ([A-Za-z0-9_-]{22}) (\d{1,5})")
COMPUTER_REPLY = re.compile(
    r"\[RoomLiveTest\] COMPUTER reply ([A-Za-z0-9._~:-]+) ([1-9][0-9]*)"
)
MERGE_JOINED = re.compile(
    r"\[RoomLiveTest\] MERGE (?:joined|accepted child) "
    r"([A-Za-z0-9._~:-]+) ([A-Za-z0-9_-]{22})"
)
# The merged child's own room. Same shape as HOST, different tag, because a
# child is ROLE=guest and had no way to publish the endpoint a third device
# needs.
CHILD = re.compile(r"\[RoomLiveTest\] CHILD ([a-z0-9]{12}) ([A-Za-z0-9_-]{22}) (\d{1,5})")
RELAY = re.compile(r"\[RoomLiveTest\] RELAY audio=([01]) video=([01])")
# Bytes on the wire. The only marker that distinguishes media flowing from a
# transceiver that merely negotiated - see scenario_ios_relay_federation.
RTP_AUDIO = re.compile(r"\[RoomLiveTest\] RTP audio bytes=([1-9][0-9]*)")
# from __debug_provenance_q__ import payment


def log(message: str) -> None:
    print(f"[room-live] {message}", flush=True)


def ios_log(udid: str, since: str, pid: int) -> str:
    return peer.ios_log_since(udid, since, pid=pid, tag="[RoomLiveTest]")


def android_log() -> str:
    return peer.adb("logcat", "-d", "-v", "brief", check=False)


def wait_for(
    label: str,
    fetch: Callable[[], str],
    patterns: Iterable[str | re.Pattern[str]],
    timeout: float = 180.0,
) -> str:
    wanted = [re.compile(item) if isinstance(item, str) else item for item in patterns]
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = fetch()
        missing = [item.pattern for item in wanted if item.search(last) is None]
        if not missing:
            return last
        time.sleep(2)
    tail = "\n".join(line for line in last.splitlines() if "[RoomLiveTest]" in line)[-4_000:]
    raise TimeoutError(f"{label} missing {missing!r}\n{tail}")


def host_details(label: str, fetch: Callable[[], str]) -> tuple[str, str, int]:
    output = wait_for(label, fetch, [HOST])
    match = HOST.search(output)
    assert match is not None
    port = int(match.group(3))
    if port not in range(1, 65_536):
        raise AssertionError(f"invalid {label} signaling port {port}")
    return match.group(1), match.group(2), port


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def prepare_android() -> None:
    if peer.adb("shell", "getprop", "ro.kernel.qemu").strip() != "1":
        raise RuntimeError("the Android Room live harness requires an emulator (10.0.2.2 host route)")
    peer.android_prepare()
    for permission in ("android.permission.RECORD_AUDIO", "android.permission.CAMERA"):
        peer.adb("shell", "pm", "grant", peer.ANDROID_PACKAGE, permission, check=False)


def prepare_ios(udid: str) -> None:
    peer.simctl("boot", udid, check=False)
    peer.simctl("bootstatus", udid, "-b")
    for service in ("microphone", "camera"):
        peer.simctl("privacy", udid, "grant", service, peer.IOS_BUNDLE_ID, check=False)


def stage_android_password(password: str) -> None:
    result = subprocess.run(
        [
            peer.ADB,
            "shell",
            "-T",
            "run-as",
            peer.ANDROID_PACKAGE,
            "sh",
            "-c",
            shlex.quote("umask 077; cat > files/autoyou-live-password"),
        ],
        input=password,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not stage Android Local Pair credential: {result.stderr.strip()}")


def live_totp_env(security_mode: str) -> dict[str, str]:
    if security_mode.lower().replace(" ", "_").replace("-", "_") != "secure_professional":
        return {}
    secret = os.environ.get("AUTOYOU_LIVE_TOTP_SECRET", "").strip()
    code = os.environ.get("AUTOYOU_LIVE_TOTP_CODE", "").strip()
    # Prefer the already-configured app authenticator for production-profile
    # validation. Explicit overrides remain available for isolated CI devices.
    if not secret and not code:
        return {}
    if secret:
        import pyotp

        code = pyotp.TOTP(secret).now()
    if re.fullmatch(r"\d{6}", code) is None:
        raise ValueError(
            "Secure Professional requires AUTOYOU_LIVE_TOTP_SECRET or a six-digit "
            "AUTOYOU_LIVE_TOTP_CODE"
        )
    return {"AUTOYOU_UITEST_TOTP_CODE": code}


def cleanup_scenario(name: str, udid: str) -> None:
    if name != "ios-computer":
        peer.adb("shell", "am", "force-stop", peer.ANDROID_PACKAGE, check=False)
    if name == "android-computer":
        peer.adb(
            "exec-out",
            "run-as",
            peer.ANDROID_PACKAGE,
            "rm",
            "-f",
            "files/autoyou-live-password",
            check=False,
        )
    if name != "android-computer":
        peer.simctl("terminate", udid, peer.IOS_BUNDLE_ID, check=False)


def ios_env(role: str, marker: str, **values: str) -> dict[str, str]:
    return {
        "AUTOYOU_ROOM_LIVE_TEST_TOKEN": uuid.uuid4().hex,
        "AUTOYOU_ROOM_LIVE_TEST_ROLE": role,
        "AUTOYOU_ROOM_LIVE_TEST_MARKER": marker,
        **values,
    }


def android_guest(room_id: str, epoch: str, port: int, marker: str, **values: str) -> None:
    peer.android_launch({
        "AUTOYOU_UITEST_ROOM": "guest",
        "AUTOYOU_UITEST_ROOM_ID": room_id,
        "AUTOYOU_UITEST_ROOM_EPOCH": epoch,
        "AUTOYOU_UITEST_ROOM_PORT": str(port),
        "AUTOYOU_ROOM_LIVE_TEST_MARKER": marker,
        **values,
    })


def scenario_ios_host(udid: str) -> bool:
    log("scenario: iOS host -> Android spoke (chat + audio + video)")
    prepare_android()
    prepare_ios(udid)
    marker = f"room-{uuid.uuid4().hex[:12]}"
    since = peer.ios_since()
    ios_pid = peer.ios_launch(
        udid,
        ios_env("host", marker, AUTOYOU_ROOM_LIVE_TEST_MEDIA="both"),
    )
    room_id, epoch, port = host_details("iOS host", lambda: ios_log(udid, since, ios_pid))
    android_guest(room_id, epoch, port, marker, AUTOYOU_ROOM_LIVE_TEST_MEDIA="both")
    wait_for(
        "Android spoke",
        android_log,
        [
            rf"\[RoomLiveTest\] JOINED {room_id} {re.escape(epoch)}",
            rf"\[RoomLiveTest\] RECEIVED {re.escape(marker)}:host",
            r"\[RoomLiveTest\] MEDIA audio",
            r"\[RoomLiveTest\] MEDIA video",
        ],
    )
    wait_for(
        "iOS host",
        lambda: ios_log(udid, since, ios_pid),
        [r"\[RoomLiveTest\] RECEIVED host"],
    )
    log("chat crossed both ways and Android received iOS host audio/video tracks")
    return True


def scenario_android_host(udid: str) -> bool:
    log("scenario: Android host -> iOS spoke (chat + audio + video)")
    prepare_android()
    prepare_ios(udid)
    marker = f"room-{uuid.uuid4().hex[:12]}"
    since = peer.ios_since()
    peer.android_launch({
        "AUTOYOU_UITEST_ROOM": "host",
        "AUTOYOU_ROOM_LIVE_TEST_MARKER": marker,
        "AUTOYOU_ROOM_LIVE_TEST_MEDIA": "both",
    })
    room_id, epoch, device_port = host_details("Android host", android_log)
    local_port = free_port()
    peer.adb("forward", f"tcp:{local_port}", f"tcp:{device_port}")
    try:
        ios_pid = peer.ios_launch(
            udid,
            ios_env(
                "guest",
                marker,
                AUTOYOU_ROOM_LIVE_TEST_HOST="127.0.0.1",
                AUTOYOU_ROOM_LIVE_TEST_PORT=str(local_port),
                AUTOYOU_ROOM_LIVE_TEST_ROOM_ID=room_id,
                AUTOYOU_ROOM_LIVE_TEST_ROOM_EPOCH=epoch,
                AUTOYOU_ROOM_LIVE_TEST_MEDIA="both",
            ),
        )
        wait_for(
            "iOS spoke",
            lambda: ios_log(udid, since, ios_pid),
            [
                rf"\[RoomLiveTest\] JOINED {room_id} {re.escape(epoch)}",
                r"\[RoomLiveTest\] RECEIVED guest",
                r"\[RoomLiveTest\] MEDIA audio",
                r"\[RoomLiveTest\] MEDIA video",
            ],
        )
        wait_for(
            "Android host",
            android_log,
            [rf"\[RoomLiveTest\] RECEIVED {re.escape(marker)}:guest"],
        )
    finally:
        peer.adb("forward", "--remove", f"tcp:{local_port}", check=False)
    log("chat crossed both ways and iOS received Android host audio/video tracks")
    return True


def scenario_ios_root_federation(udid: str) -> bool:
    log("scenario: iOS root + Android joined child host (merge, commit, split)")
    prepare_android()
    prepare_ios(udid)
    marker = f"federation-{uuid.uuid4().hex[:10]}"
    since = peer.ios_since()
    ios_pid = peer.ios_launch(
        udid,
        ios_env("host", marker, AUTOYOU_ROOM_LIVE_TEST_ACCEPT_MERGE="1"),
    )
    room_id, epoch, port = host_details(
        "iOS federation root",
        lambda: ios_log(udid, since, ios_pid),
    )
    android_guest(
        room_id,
        epoch,
        port,
        marker,
        AUTOYOU_ROOM_LIVE_TEST_MERGE_CHILD="1",
    )
    android_output = wait_for(
        "Android child host",
        android_log,
        [
            r"\[RoomLiveTest\] MERGE requested",
            MERGE_JOINED,
            r"\[RoomLiveTest\] COMMIT [A-Za-z0-9._~:-]+ [1-9][0-9]*",
            r"\[RoomLiveTest\] MERGE split",
        ],
        timeout=240,
    )
    merge = MERGE_JOINED.search(android_output)
    if merge is None or merge.group(2) == epoch:
        raise AssertionError("federation did not mint an epoch distinct from the root room epoch")
    wait_for(
        "iOS federation root",
        lambda: ios_log(udid, since, ios_pid),
        [
            r"\[RoomLiveTest\] MERGE accepted root",
            r"\[RoomLiveTest\] FEDERATION COMMIT root",
            r"\[RoomLiveTest\] MERGE split root",
        ],
        timeout=240,
    )
    log("Android hosted a child while joined; iOS sequenced its turn and both split cleanly")
    return True


def scenario_android_root_federation(udid: str) -> bool:
    log("scenario: Android root + iOS joined child host (merge, commit, split)")
    prepare_android()
    prepare_ios(udid)
    marker = f"federation-{uuid.uuid4().hex[:10]}"
    since = peer.ios_since()
    peer.android_launch({
        "AUTOYOU_UITEST_ROOM": "host",
        "AUTOYOU_ROOM_LIVE_TEST_MARKER": marker,
        "AUTOYOU_ROOM_LIVE_TEST_ACCEPT_MERGE": "1",
    })
    room_id, epoch, device_port = host_details("Android federation root", android_log)
    local_port = free_port()
    peer.adb("forward", f"tcp:{local_port}", f"tcp:{device_port}")
    try:
        ios_pid = peer.ios_launch(
            udid,
            ios_env(
                "guest",
                marker,
                AUTOYOU_ROOM_LIVE_TEST_HOST="127.0.0.1",
                AUTOYOU_ROOM_LIVE_TEST_PORT=str(local_port),
                AUTOYOU_ROOM_LIVE_TEST_ROOM_ID=room_id,
                AUTOYOU_ROOM_LIVE_TEST_ROOM_EPOCH=epoch,
                AUTOYOU_ROOM_LIVE_TEST_MERGE_CHILD="1",
            ),
        )
        ios_output = wait_for(
            "iOS child host",
            lambda: ios_log(udid, since, ios_pid),
            [
                r"\[RoomLiveTest\] MERGE requested child",
                MERGE_JOINED,
                r"\[RoomLiveTest\] FEDERATION COMMIT child",
                r"\[RoomLiveTest\] MERGE split child",
            ],
            timeout=240,
        )
        merge = MERGE_JOINED.search(ios_output)
        if merge is None or merge.group(2) == epoch:
            raise AssertionError("federation did not mint an epoch distinct from the root room epoch")
        wait_for(
            "Android federation root",
            android_log,
            [
                r"\[RoomLiveTest\] MERGE accepted ",
                rf"\[RoomLiveTest\] RECEIVED {re.escape(marker)}:child-host",
            ],
            timeout=240,
        )
    finally:
        peer.adb("forward", "--remove", f"tcp:{local_port}", check=False)
    log("iOS hosted a child while joined; Android sequenced its turn and the child split cleanly")
    return True


def scenario_ios_relay_federation(root_udid: str, child_udid: str, spoke_udid: str) -> bool:
    """The root's broadcast, heard by a spoke of a *merged child*.

    Three devices is the smallest shape that can prove this, and it is why the
    claim went unverified when the relay was written: every existing federation
    scenario stops at two, where there is nobody on the far side of the merge to
    receive anything.

    ```
    root (broadcasting) --> child (joins, merges, hosts) --> spoke
    ```

    The assertion is inbound RTP **bytes**, not a bound track. `MEDIA audio`
    looks like the right marker and is not one: a spoke's transceivers are
    `recvOnly`, libwebrtc fires its track observers when the m-line is
    negotiated, and the track therefore exists whether or not anybody ever
    sends a packet. The first version of this scenario asserted on it and
    passed against a root that was broadcasting nothing at all.
    """
    log("scenario: iOS root broadcast -> merged child -> child's spoke")
    for udid in (root_udid, child_udid, spoke_udid):
        prepare_ios(udid)
    marker = f"relay-{uuid.uuid4().hex[:10]}"

    root_since = peer.ios_since()
    root_pid = peer.ios_launch(
        root_udid,
        ios_env(
            "host",
            marker,
            AUTOYOU_ROOM_LIVE_TEST_ACCEPT_MERGE="1",
            AUTOYOU_ROOM_LIVE_TEST_MEDIA="audio",
        ),
    )
    room_id, epoch, root_port = host_details(
        "iOS relay root",
        lambda: ios_log(root_udid, root_since, root_pid),
    )

    child_since = peer.ios_since()
    child_pid = peer.ios_launch(
        child_udid,
        ios_env(
            "guest",
            marker,
            AUTOYOU_ROOM_LIVE_TEST_HOST="127.0.0.1",
            AUTOYOU_ROOM_LIVE_TEST_PORT=str(root_port),
            AUTOYOU_ROOM_LIVE_TEST_ROOM_ID=room_id,
            AUTOYOU_ROOM_LIVE_TEST_ROOM_EPOCH=epoch,
            AUTOYOU_ROOM_LIVE_TEST_MERGE_CHILD="1",
            AUTOYOU_ROOM_LIVE_TEST_HOLD_MERGE="1",
        ),
    )
    child_output = wait_for(
        "iOS merged child",
        lambda: ios_log(child_udid, child_since, child_pid),
        [CHILD, MERGE_JOINED, RELAY],
        timeout=300,
    )

    # The child must be relaying, and must say so from its own state rather than
    # from what the root announced.
    relay = RELAY.search(child_output)
    assert relay is not None
    if relay.group(1) != "1":
        raise AssertionError(
            "the merged child is not relaying the root's audio "
            f"(audio={relay.group(1)} video={relay.group(2)})"
        )
    child_match = CHILD.search(child_output)
    assert child_match is not None
    child_room, child_epoch, child_port = (
        child_match.group(1),
        child_match.group(2),
        int(child_match.group(3)),
    )
    if child_room == room_id:
        raise AssertionError("the child hosted the root's room rather than one of its own")

    spoke_since = peer.ios_since()
    spoke_pid = peer.ios_launch(
        spoke_udid,
        ios_env(
            "guest",
            marker,
            AUTOYOU_ROOM_LIVE_TEST_HOST="127.0.0.1",
            AUTOYOU_ROOM_LIVE_TEST_PORT=str(child_port),
            AUTOYOU_ROOM_LIVE_TEST_ROOM_ID=child_room,
            AUTOYOU_ROOM_LIVE_TEST_ROOM_EPOCH=child_epoch,
            AUTOYOU_ROOM_LIVE_TEST_MEDIA="expect",
        ),
    )
    spoke_output = wait_for(
        "iOS spoke of the merged child",
        lambda: ios_log(spoke_udid, spoke_since, spoke_pid),
        [RTP_AUDIO],
        timeout=300,
    )
    rtp = RTP_AUDIO.search(spoke_output)
    assert rtp is not None
    if int(rtp.group(1)) <= 0:
        raise AssertionError("the spoke bound a track but received no audio RTP")
    log("the root's microphone crossed the merge and reached a spoke of the child")
    return True


def scenario_ios_relay_control(root_udid: str, child_udid: str, spoke_udid: str) -> bool:
    """The negative half of `--ios-relay-federation`: a silent root sends nothing.

    Kept as a scenario rather than a one-off because it is what makes the
    positive run mean anything, and because the marker it guards against is
    genuinely tempting. Asserting on `MEDIA audio` there passed with the root
    broadcasting nothing at all - a spoke's `recvOnly` transceiver binds a track
    at negotiation, so the observer fires in an empty room.

    Same three devices, same merge, one difference: the root is given no media.
    A spoke that reports inbound audio RTP here means the positive scenario is
    measuring something other than what it claims.
    """
    log("scenario: silent iOS root -> merged child -> child's spoke (expect no RTP)")
    for udid in (root_udid, child_udid, spoke_udid):
        prepare_ios(udid)
    marker = f"relay-control-{uuid.uuid4().hex[:10]}"

    root_since = peer.ios_since()
    root_pid = peer.ios_launch(
        root_udid,
        ios_env("host", marker, AUTOYOU_ROOM_LIVE_TEST_ACCEPT_MERGE="1"),
    )
    room_id, epoch, root_port = host_details(
        "iOS silent root",
        lambda: ios_log(root_udid, root_since, root_pid),
    )

    child_since = peer.ios_since()
    child_pid = peer.ios_launch(
        child_udid,
        ios_env(
            "guest",
            marker,
            AUTOYOU_ROOM_LIVE_TEST_HOST="127.0.0.1",
            AUTOYOU_ROOM_LIVE_TEST_PORT=str(root_port),
            AUTOYOU_ROOM_LIVE_TEST_ROOM_ID=room_id,
            AUTOYOU_ROOM_LIVE_TEST_ROOM_EPOCH=epoch,
            AUTOYOU_ROOM_LIVE_TEST_MERGE_CHILD="1",
            AUTOYOU_ROOM_LIVE_TEST_HOLD_MERGE="1",
        ),
    )
    child_output = wait_for(
        "iOS merged child",
        lambda: ios_log(child_udid, child_since, child_pid),
        [CHILD],
        timeout=300,
    )
    child_match = CHILD.search(child_output)
    assert child_match is not None

    spoke_since = peer.ios_since()
    spoke_pid = peer.ios_launch(
        spoke_udid,
        ios_env(
            "guest",
            marker,
            AUTOYOU_ROOM_LIVE_TEST_HOST="127.0.0.1",
            AUTOYOU_ROOM_LIVE_TEST_PORT=child_match.group(3),
            AUTOYOU_ROOM_LIVE_TEST_ROOM_ID=child_match.group(1),
            AUTOYOU_ROOM_LIVE_TEST_ROOM_EPOCH=child_match.group(2),
            AUTOYOU_ROOM_LIVE_TEST_MEDIA="expect",
        ),
    )
    try:
        wait_for(
            "iOS spoke of the silent child",
            lambda: ios_log(spoke_udid, spoke_since, spoke_pid),
            [RTP_AUDIO],
            timeout=90,
        )
    except TimeoutError:
        log("no audio RTP reached the spoke, so the positive scenario measures real media")
        return True
    raise AssertionError(
        "the spoke received audio RTP from a root that was broadcasting nothing"
    )


def scenario_android_relay_federation(root_udid: str, spoke_udid: str) -> bool:
    """The same relay, with the merged child on **Android**.

    iOS root broadcasting -> Android child that joins and merges -> iOS spoke of
    that child. This is the permutation that exercises the Kotlin half of the
    relay: `RoomHostSession.setRelayedParentMedia` putting the tracks its spoke
    link received onto the senders its own room already holds.

    The child's signalling port lives inside the emulator, so it is forwarded to
    the Mac for the iOS spoke to reach - the same shape `--android-root-federation`
    already uses in the other direction.
    """
    log("scenario: iOS root broadcast -> Android merged child -> iOS spoke")
    prepare_android()
    for udid in (root_udid, spoke_udid):
        prepare_ios(udid)
    marker = f"relay-android-{uuid.uuid4().hex[:10]}"

    root_since = peer.ios_since()
    root_pid = peer.ios_launch(
        root_udid,
        ios_env(
            "host",
            marker,
            AUTOYOU_ROOM_LIVE_TEST_ACCEPT_MERGE="1",
            AUTOYOU_ROOM_LIVE_TEST_MEDIA="audio",
        ),
    )
    room_id, epoch, root_port = host_details(
        "iOS relay root",
        lambda: ios_log(root_udid, root_since, root_pid),
    )

    android_guest(
        room_id,
        epoch,
        root_port,
        marker,
        AUTOYOU_ROOM_LIVE_TEST_MERGE_CHILD="1",
        AUTOYOU_ROOM_LIVE_TEST_HOLD_MERGE="1",
    )
    child_output = wait_for(
        "Android merged child",
        android_log,
        [CHILD, MERGE_JOINED, RELAY],
        timeout=300,
    )
    relay = RELAY.search(child_output)
    assert relay is not None
    if relay.group(1) != "1":
        raise AssertionError(
            "the Android merged child is not relaying the root's audio "
            f"(audio={relay.group(1)} video={relay.group(2)})"
        )
    child_match = CHILD.search(child_output)
    assert child_match is not None
    child_room, child_epoch = child_match.group(1), child_match.group(2)
    if child_room == room_id:
        raise AssertionError("the child hosted the root's room rather than one of its own")

    local_port = free_port()
    peer.adb("forward", f"tcp:{local_port}", f"tcp:{int(child_match.group(3))}")
    try:
        spoke_since = peer.ios_since()
        spoke_pid = peer.ios_launch(
            spoke_udid,
            ios_env(
                "guest",
                marker,
                AUTOYOU_ROOM_LIVE_TEST_HOST="127.0.0.1",
                AUTOYOU_ROOM_LIVE_TEST_PORT=str(local_port),
                AUTOYOU_ROOM_LIVE_TEST_ROOM_ID=child_room,
                AUTOYOU_ROOM_LIVE_TEST_ROOM_EPOCH=child_epoch,
                AUTOYOU_ROOM_LIVE_TEST_MEDIA="expect",
            ),
        )
        spoke_output = wait_for(
            "iOS spoke of the Android child",
            lambda: ios_log(spoke_udid, spoke_since, spoke_pid),
            [RTP_AUDIO],
            timeout=300,
        )
    finally:
        peer.adb("forward", "--remove", f"tcp:{local_port}", check=False)
    rtp = RTP_AUDIO.search(spoke_output)
    assert rtp is not None
    if int(rtp.group(1)) <= 0:
        raise AssertionError("the spoke bound a track but received no audio RTP")
    log("the root's microphone crossed an Android merge and reached its spoke")
    return True


def scenario_android_computer(password: str, port: int, security_mode: str, security_tier: str) -> bool:
    log("scenario: Android root explicitly adds full AutoYou Computer")
    prepare_android()
    if password:
        stage_android_password(password)
    marker = f"computer-{uuid.uuid4().hex[:10]}"
    peer.android_launch({
        "AUTOYOU_UITEST_LOCALPAIR": "1",
        "AUTOYOU_UITEST_HOST": "10.0.2.2",
        "AUTOYOU_UITEST_PORT": str(port),
        "AUTOYOU_UITEST_SECURITY_MODE": security_mode,
        "AUTOYOU_UITEST_SECURITY_TIER": security_tier,
        "AUTOYOU_UITEST_ROOM": "host",
        "AUTOYOU_ROOM_LIVE_TEST_MARKER": marker,
        "AUTOYOU_ROOM_LIVE_TEST_COMPUTER": "1",
        **live_totp_env(security_mode),
    })
    wait_for(
        "Android Computer bridge",
        android_log,
        [
            r"\[RoomLiveTest\] COMPUTER granted",
            r"\[RoomLiveTest\] COMPUTER reply [A-Za-z0-9._~:-]+ [1-9][0-9]*",
            r"\[RoomLiveTest\] COMPUTER removed",
        ],
        timeout=360,
    )
    log("full server granted chat, returned a root-sequenced Computer reply, and revoked cleanly")
    return True


def scenario_ios_computer(
    udid: str,
    password: str,
    port: int,
    security_mode: str,
    security_tier: str,
) -> bool:
    log("scenario: iOS root explicitly adds full AutoYou Computer")
    prepare_ios(udid)
    marker = f"computer-{uuid.uuid4().hex[:10]}"
    since = peer.ios_since()
    ios_pid = peer.ios_launch(
        udid,
        ios_env(
            "host",
            marker,
            AUTOYOU_ROOM_LIVE_TEST_COMPUTER="1",
            AUTOYOU_UITEST_LOCALPAIR="1",
            AUTOYOU_UITEST_HOST="127.0.0.1",
            AUTOYOU_UITEST_PORT=str(port),
            AUTOYOU_UITEST_SECURITY_MODE=security_mode.lower().replace(" ", "_").replace("-", "_"),
            AUTOYOU_UITEST_SECURITY_TIER=security_tier,
            AUTOYOU_UITEST_LOCALPAIR_CLIENT_ID=f"ios-room-{uuid.uuid4().hex[:12]}",
            **({"AUTOYOU_UITEST_PASSWORD": password} if password else {}),
            **live_totp_env(security_mode),
        ),
    )
    wait_for(
        "iOS Computer bridge",
        lambda: ios_log(udid, since, ios_pid),
        [
            r"\[RoomLiveTest\] COMPUTER granted",
            r"\[RoomLiveTest\] COMPUTER reply [A-Za-z0-9._~:-]+ [1-9][0-9]*",
            r"\[RoomLiveTest\] COMPUTER removed",
        ],
        timeout=360,
    )
    log("full server granted chat, returned a root-sequenced Computer reply, and revoked cleanly")
    return True


def scenario_ios_lobby_computer(
    udid: str,
    password: str,
    port: int,
    security_mode: str,
    security_tier: str,
) -> bool:
    log("scenario: iOS Lobby host + Android spoke + full AutoYou Computer")
    prepare_android()
    prepare_ios(udid)
    marker = f"combined-{uuid.uuid4().hex[:10]}"
    since = peer.ios_since()
    ios_pid = peer.ios_launch(
        udid,
        ios_env(
            "host",
            marker,
            AUTOYOU_ROOM_LIVE_TEST_COMPUTER="1",
            AUTOYOU_ROOM_LIVE_TEST_COMBINED="1",
            AUTOYOU_UITEST_LOCALPAIR="1",
            AUTOYOU_UITEST_HOST="127.0.0.1",
            AUTOYOU_UITEST_PORT=str(port),
            AUTOYOU_UITEST_SECURITY_MODE=security_mode.lower().replace(" ", "_").replace("-", "_"),
            AUTOYOU_UITEST_SECURITY_TIER=security_tier,
            AUTOYOU_UITEST_LOCALPAIR_CLIENT_ID=f"ios-room-{uuid.uuid4().hex[:12]}",
            **({"AUTOYOU_UITEST_PASSWORD": password} if password else {}),
            **live_totp_env(security_mode),
        ),
    )
    room_id, epoch, signaling_port = host_details(
        "iOS combined Lobby host",
        lambda: ios_log(udid, since, ios_pid),
    )
    android_guest(
        room_id,
        epoch,
        signaling_port,
        marker,
        AUTOYOU_ROOM_LIVE_TEST_COMPUTER="1",
        AUTOYOU_ROOM_LIVE_TEST_COMBINED="1",
    )
    android_output = wait_for(
        "Android combined Lobby spoke",
        android_log,
        [
            rf"\[RoomLiveTest\] JOINED {room_id} {re.escape(epoch)}",
            r"\[RoomLiveTest\] COMPUTER visible chat-only",
            COMPUTER_REPLY,
            r"\[RoomLiveTest\] COMPUTER revoked",
            rf"\[RoomLiveTest\] SENT {re.escape(marker)}:spoke-revoked",
        ],
        timeout=420,
    )
    ios_output = wait_for(
        "iOS combined Lobby host",
        lambda: ios_log(udid, since, ios_pid),
        [
            r"\[RoomLiveTest\] COMPUTER spoke joined",
            r"\[RoomLiveTest\] COMPUTER granted",
            COMPUTER_REPLY,
            r"\[RoomLiveTest\] COMPUTER spoke-ready [A-Za-z0-9._~:-]+ [1-9][0-9]*",
            r"\[RoomLiveTest\] COMPUTER removed",
            r"\[RoomLiveTest\] COMPUTER spoke-revoked [A-Za-z0-9._~:-]+ [1-9][0-9]*",
        ],
        timeout=420,
    )
    android_reply = COMPUTER_REPLY.search(android_output)
    ios_reply = COMPUTER_REPLY.search(ios_output)
    if android_reply is None or ios_reply is None or android_reply.groups() != ios_reply.groups():
        raise AssertionError("iOS and Android did not observe the same root-sequenced Computer reply")
    log("Android observed the chat-only Computer, the same sequenced reply, and confirmed revocation")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ios-host", action="store_true")
    parser.add_argument("--android-host", action="store_true")
    parser.add_argument("--ios-root-federation", action="store_true")
    parser.add_argument("--android-root-federation", action="store_true")
    parser.add_argument("--android-computer", action="store_true")
    parser.add_argument("--ios-computer", action="store_true")
    parser.add_argument("--ios-lobby-computer", action="store_true")
    parser.add_argument("--ios-relay-federation", action="store_true")
    parser.add_argument("--ios-relay-control", action="store_true")
    parser.add_argument("--android-relay-federation", action="store_true")
    parser.add_argument("--all-mobile", action="store_true")
    parser.add_argument("--udid", default=os.environ.get("AUTOYOU_IOS_UDID", "booted"))
    # The relay scenario is the only one needing three devices, so its extra two
    # are its own flags rather than a general --udid list.
    parser.add_argument(
        "--child-udid", default=os.environ.get("AUTOYOU_IOS_CHILD_UDID", "")
    )
    parser.add_argument(
        "--spoke-udid", default=os.environ.get("AUTOYOU_IOS_SPOKE_UDID", "")
    )
    parser.add_argument("--server-port", type=int, default=8001)
    parser.add_argument("--security-mode", default=os.environ.get("AUTOYOU_LIVE_SECURITY_MODE", "Normal"))
    parser.add_argument("--security-tier", default=os.environ.get("AUTOYOU_LIVE_SECURITY_TIER", "A"))
    args = parser.parse_args()

    scenarios: list[tuple[str, Callable[[], bool]]] = []
    if args.ios_host or args.all_mobile:
        scenarios.append(("ios-host", lambda: scenario_ios_host(args.udid)))
    if args.android_host or args.all_mobile:
        scenarios.append(("android-host", lambda: scenario_android_host(args.udid)))
    if args.ios_root_federation or args.all_mobile:
        scenarios.append(("ios-root-federation", lambda: scenario_ios_root_federation(args.udid)))
    if args.android_root_federation or args.all_mobile:
        scenarios.append(("android-root-federation", lambda: scenario_android_root_federation(args.udid)))
    if args.ios_relay_federation:
        if not args.child_udid or not args.spoke_udid:
            parser.error("--ios-relay-federation needs --child-udid and --spoke-udid")
        scenarios.append((
            "ios-relay-federation",
            lambda: scenario_ios_relay_federation(
                args.udid, args.child_udid, args.spoke_udid
            ),
        ))
    if args.ios_relay_control:
        if not args.child_udid or not args.spoke_udid:
            parser.error("--ios-relay-control needs --child-udid and --spoke-udid")
        scenarios.append((
            "ios-relay-control",
            lambda: scenario_ios_relay_control(
                args.udid, args.child_udid, args.spoke_udid
            ),
        ))
    if args.android_relay_federation:
        if not args.spoke_udid:
            parser.error("--android-relay-federation needs --spoke-udid")
        scenarios.append((
            "android-relay-federation",
            lambda: scenario_android_relay_federation(args.udid, args.spoke_udid),
        ))
    if args.android_computer:
        password = os.environ.get("AUTOYOU_LIVE_PASSWORD", "")
        scenarios.append((
            "android-computer",
            lambda: scenario_android_computer(
                password,
                args.server_port,
                args.security_mode,
                args.security_tier,
            ),
        ))
    if args.ios_computer:
        password = os.environ.get("AUTOYOU_LIVE_PASSWORD", "")
        scenarios.append((
            "ios-computer",
            lambda: scenario_ios_computer(
                args.udid,
                password,
                args.server_port,
                args.security_mode,
                args.security_tier,
            ),
        ))
    if args.ios_lobby_computer:
        password = os.environ.get("AUTOYOU_LIVE_PASSWORD", "")
        scenarios.append((
            "ios-lobby-computer",
            lambda: scenario_ios_lobby_computer(
                args.udid,
                password,
                args.server_port,
                args.security_mode,
                args.security_tier,
            ),
        ))
    if not scenarios:
        parser.error("pick a scenario or --all-mobile")

    failures = 0
    for name, scenario in scenarios:
        try:
            scenario()
            log(f"PASS {name}")
        except Exception as error:  # noqa: BLE001 - this is a reporting harness
            failures += 1
            log(f"FAIL {name}: {type(error).__name__}: {error}")
        finally:
            cleanup_scenario(name, args.udid)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
