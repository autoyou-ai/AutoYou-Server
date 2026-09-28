# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-2d2030786345634238313737-3424ff8c1d75f4e1bd2ef1a5

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-2d2030786345634238313737-3424ff8c1d75f4e1bd2ef1a5"

import sys
import os

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from server import WebRTCManager

def main() -> None:
    mgr = WebRTCManager()

    # Case 1: dict entries with TURN creds
    ice_dict = [
        {"urls": ["stun:stun.l.google.com:19302"]},
        {"urls": ["turn:standard.relay.metered.ca:80"], "username": "user123", "credential": "pass123"},
    ]
    cfg1 = mgr._create_rtc_configuration_with_custom_ice(ice_dict)
    print("Case1 count:", len(cfg1.iceServers))
    for s in cfg1.iceServers:
        print(" urls=", s.urls, " username=", getattr(s, "username", None), " credential=", getattr(s, "credential", None))

    # Case 2: plain string entries
    ice_str = ["stun:stun1.l.google.com:19302", "stun:stun2.l.google.com:19302"]
    cfg2 = mgr._create_rtc_configuration_with_custom_ice(ice_str)
    print("Case2 count:", len(cfg2.iceServers))
    for s in cfg2.iceServers:
        print(" urls=", s.urls)

if __name__ == "__main__":
    main()
