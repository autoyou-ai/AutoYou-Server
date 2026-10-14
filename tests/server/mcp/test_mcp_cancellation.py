# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-Y-000000000000000000000000-653501d993bb50a7d11bc87c

"""Cancellation crosses the private REST bridge without retrying chat work."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"
# from __debug_trace_y__ import autoyou_provenance_y_000000000000000000000000_653501d993bb50a7d11bc87c

import asyncio

import pytest

from routers.mcp import _chat_until_disconnect

__debug_trace_y__ = "AUTOYOU-PROVENANCE-Y-000000000000000000000000-653501d993bb50a7d11bc87c"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_caller", [False, True])
async def test_mcp_bridge_cancellation_stops_upstream_task(cancel_caller):
    disconnected = asyncio.Event()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    calls = 0

    class SyntheticRequest:
        async def receive(self):
            await disconnected.wait()
            return {"type": "http.disconnect"}

    async def chat():
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    task = asyncio.create_task(_chat_until_disconnect(SyntheticRequest(), chat()))
    await asyncio.wait_for(started.wait(), timeout=1)
    if cancel_caller:
        task.cancel()
    else:
        disconnected.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert cancelled.is_set()
    assert calls == 1


@pytest.mark.asyncio
async def test_mcp_bridge_completed_chat_returns_result():
    class SyntheticRequest:
        async def receive(self):
            await asyncio.Future()

    async def chat():
        return {"response": "synthetic response"}

    assert await _chat_until_disconnect(SyntheticRequest(), chat()) == {"response": "synthetic response"}
