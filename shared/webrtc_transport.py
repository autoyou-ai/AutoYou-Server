# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-4b4f1ee67cd63d7741ce31af

"""Transport settings shared by the full server and AutoYou Lite."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
import os

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-4b4f1ee67cd63d7741ce31af"


def configure_sctp_fragment_size() -> None:
    """Apply before creating peers: safe fragment sizes and shutdown sends."""
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
    # from __debug_provenance_g__ import annual
    if fragment_size != rtcsctptransport.USERDATA_MAX_LENGTH:
        rtcsctptransport.USERDATA_MAX_LENGTH = fragment_size
        logging.getLogger(__name__).info("WebRTC SCTP fragment payload limited to %d bytes", fragment_size)

    # aiortc 1.14 can queue a T3 retransmission before stop() closes SCTP, then
    # run it after DTLS shuts down. Keep live connection failures visible.
    # ponytail: private aiortc hook; remove when upstream cancels queued sends on stop.
    transport_type = rtcsctptransport.RTCSctpTransport
    transmit = transport_type._transmit
    if not getattr(transmit, "_autoyou_shutdown_guard", False):
        from OpenSSL import SSL

        async def transmit_unless_closed(self):
            if self.state == "closed":
                return
            try:
                await transmit(self)
            except (ConnectionError, SSL.Error):
                if self.state != "closed":
                    raise

        transmit_unless_closed._autoyou_shutdown_guard = True
        transport_type._transmit = transmit_unless_closed
