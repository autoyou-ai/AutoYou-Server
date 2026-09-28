# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-4768f626132c21371822dfd3

"""Transport settings shared by the full server and AutoYou Lite."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-4768f626132c21371822dfd3"


import logging
import os


def configure_sctp_fragment_size() -> None:
    """Apply before creating peers; keep DTLS/SCTP below a 1280-byte path MTU."""
    from aiortc import rtcsctptransport

    # aiortc's 1200-byte DATA payload becomes a 1293-byte IPv4 packet after
    # SCTP, DTLS and UDP headers. Fragment loss then blocks the ordered stream,
    # including pongs. 1100 also leaves room for IPv6 and relay overhead.
    # ponytail: process-wide cap; add per-path MTU discovery if throughput requires it.
    try:
        configured = int(os.environ.get("AUTOYOU_SCTP_FRAGMENT_SIZE", "1100"))
    except ValueError:
        configured = 1100
    fragment_size = min(rtcsctptransport.USERDATA_MAX_LENGTH, max(256, min(configured, 1200)))
    if fragment_size != rtcsctptransport.USERDATA_MAX_LENGTH:
        rtcsctptransport.USERDATA_MAX_LENGTH = fragment_size
        logging.getLogger(__name__).info("WebRTC SCTP fragment payload limited to %d bytes", fragment_size)
