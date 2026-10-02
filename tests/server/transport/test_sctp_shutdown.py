# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiortc import rtcsctptransport
from OpenSSL import SSL

from shared.webrtc_transport import configure_sctp_fragment_size


@pytest.mark.asyncio
async def test_queued_retransmit_cannot_send_after_sctp_stop(monkeypatch):
    transport_type = rtcsctptransport.RTCSctpTransport
    monkeypatch.setattr(transport_type, "_transmit", transport_type._transmit)
    monkeypatch.setattr(rtcsctptransport, "USERDATA_MAX_LENGTH", rtcsctptransport.USERDATA_MAX_LENGTH)
    configure_sctp_fragment_size()
    installed = transport_type._transmit
    configure_sctp_fragment_size()
    assert transport_type._transmit is installed

    dtls = SimpleNamespace(state="connected", _unregister_data_receiver=lambda _: None)
    transport = transport_type(dtls)
    transport._association_state = transport.State.ESTABLISHED
    transport._RTCSctpTransport__state = "connected"
    chunk = rtcsctptransport.DataChunk()
    chunk._book_size = 1
    chunk._sent_count = 0
    chunk._retransmit = True
    transport._sent_queue.append(chunk)
    send = AsyncMock()
    monkeypatch.setattr(transport, "_send_chunk", send)

    queued = asyncio.create_task(transport._transmit())
    await transport.stop()
    send.reset_mock()
    send.side_effect = SSL.Error([("SSL routines", "", "protocol is shutdown")])
    await queued
    send.assert_not_awaited()

    # A live transport error must still reach its caller.
    transport._RTCSctpTransport__state = "connected"
    with pytest.raises(SSL.Error):
        await transport._transmit()

    # If stop wins while an in-flight send is awaiting I/O, it is teardown.
    async def close_during_send(_):
        transport._set_state(transport.State.CLOSED)
        raise ConnectionError("synthetic closed transport")

    chunk._retransmit = True
    send.side_effect = close_during_send
    await transport._transmit()
    assert transport.state == "closed"
