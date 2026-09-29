# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-a0687f3ebd34d149e59586be


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import asyncio

import os

import pytest
from aiortc import RTCPeerConnection, RTCSessionDescription

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-a0687f3ebd34d149e59586be"


if os.environ.get("AUTOYOU_RUN_LIVE_WEBRTC_TESTS") != "1":
    pytest.skip(
        "Manual aiortc exploratory test. Set AUTOYOU_RUN_LIVE_WEBRTC_TESTS=1 to run.",
        allow_module_level=True,
    )

async def test_aiortc():
    pc = RTCPeerConnection()
    # from __debug_provenance_b__ import yearly
    
    # Simulate an Android offer with recvonly audio
    offer_sdp = """v=0
o=- 4165384196144883907 2 IN IP4 127.0.0.1
s=-
t=0 0
a=extmap-allow-mixed
a=msid-semantic: WMS
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=rtcp:9 IN IP4 0.0.0.0
a=ice-ufrag:dummyufrag
a=ice-pwd:dummypassworddummypassword
a=fingerprint:sha-256 00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00
a=rtcp-mux
a=recvonly
a=mid:0
a=rtpmap:111 opus/48000/2
"""
    offer = RTCSessionDescription(sdp=offer_sdp, type="offer")
    
    has_fired = False
    @pc.on("track")
    def on_track(track):
        nonlocal has_fired
        has_fired = True
        print("FIRED on_track:", track)

    await pc.setRemoteDescription(offer)
    print("setRemoteDescription complete")
    print("on_track fired?", has_fired)
    
    transceivers = pc.getTransceivers()
    audio_t = next((t for t in transceivers if t.kind == "audio"), None)
    
    if audio_t:
        print("Transceiver direction:", audio_t.direction)
        print("Transceiver receiver track:", audio_t.receiver.track if hasattr(audio_t.receiver, 'track') else audio_t.receiver)
    else:
        print("No audio transceiver")

    # Manually promote to sendrecv
    audio_t.direction = "sendrecv"
    print("Manually promoted to sendrecv")
    
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    print("Answer direction:", audio_t.direction)

if __name__ == "__main__":
    asyncio.run(test_aiortc())
