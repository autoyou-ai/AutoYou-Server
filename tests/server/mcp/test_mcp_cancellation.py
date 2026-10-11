"""Cancellation crosses the private REST bridge without retrying chat work."""

import asyncio

import pytest

from routers.mcp import _chat_until_disconnect


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
