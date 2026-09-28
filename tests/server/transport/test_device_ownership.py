# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-652076696120284254432061-36598c167c2bf98ac715cb7a

"""Own/Shared follows the canonical device identity, not a disposable session."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-652076696120284254432061-36598c167c2bf98ac715cb7a"


from types import SimpleNamespace

import server


def test_ownership_and_connection_path_follow_canonical_device():
    webrtc = server.WebRTCManager()
    identities = {
        "session-one": SimpleNamespace(owner_key="cloud:synthetic-own", transport="cloud", pairing_mode="cloud_pair"),
        "session-two": SimpleNamespace(owner_key="cloud:synthetic-own", transport="cloud", pairing_mode="cloud_pair"),
        "local": SimpleNamespace(owner_key="local:synthetic-guest", transport="local", pairing_mode="local_pair"),
        "signal": SimpleNamespace(owner_key="signal:synthetic-guest", transport="signal", pairing_mode="auto_pair"),
    }
    webrtc._resolve_chat_identity = lambda session: identities.get(session)
    assert webrtc.remember_device_ownership(identities["session-one"], "own") == "own"
    assert webrtc.device_ownership_for_session("session-two") == "own"
    assert webrtc.describe_connected_device("session-two")["connected_via"] == "AutoYou Cloud"
    assert webrtc.device_ownership_for_session("local") == "shared"
    assert webrtc.describe_connected_device("local")["connected_via"] == "Local network"
    assert webrtc.describe_connected_device("signal")["connected_via"] == "Signal"
    assert webrtc.remember_device_ownership(identities["local"], "forged-own") == "shared"
