# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import asyncio

import pytest

from shared.admin_chat_jobs import AdminChatJobs


async def finish(jobs, request_id):
    await asyncio.wait_for(asyncio.shield(jobs._jobs[request_id].task), 2)
    return jobs.snapshot(request_id)


@pytest.mark.asyncio
async def test_acceptance_is_immediate_and_conversations_run_in_order():
    jobs = AdminChatJobs()
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def first():
        calls.append("first")
        started.set()
        await release.wait()
        return {"response": "first answer"}

    async def second():
        calls.append("second")
        return {"response": "second answer"}

    one = jobs.start("synthetic-user", "synthetic-thread", first)
    assert one["status"] == "queued" and one["response"] is None
    await started.wait()
    two = jobs.start("synthetic-user", "synthetic-thread", second)
    other = jobs.start("synthetic-user", "synthetic-other-thread", second)
    try:
        assert (await finish(jobs, other["request_id"]))["status"] == "completed"
        assert jobs.snapshot(two["request_id"])["status"] == "queued"
        release.set()
        assert (await finish(jobs, one["request_id"]))["response"] == {"response": "first answer"}
        assert (await finish(jobs, two["request_id"]))["status"] == "completed"
        assert calls == ["first", "second", "second"]
    finally:
        await jobs.shutdown()


@pytest.mark.asyncio
async def test_stopping_queued_request_does_not_cancel_the_active_request():
    jobs = AdminChatJobs()
    started, release = asyncio.Event(), asyncio.Event()

    async def first():
        started.set()
        await release.wait()
        return {"response": "kept"}

    async def queued():
        pytest.fail("Removed queued message must never run")

    one = jobs.start("synthetic-user", "synthetic-thread", first)
    await started.wait()
    two = jobs.start("synthetic-user", "synthetic-thread", queued)
    try:
        assert (await jobs.stop(two["request_id"]))["status"] == "stopped"
        assert jobs.snapshot(one["request_id"])["status"] == "thinking"
        release.set()
        assert (await finish(jobs, one["request_id"]))["response"]["response"] == "kept"
    finally:
        await jobs.shutdown()


@pytest.mark.asyncio
async def test_stop_waits_for_cleanup_discards_late_output_and_preserves_queue():
    jobs = AdminChatJobs(stop_timeout=0.02)
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    next_started = asyncio.Event()

    async def first():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await release.wait()
            return {"response": "late answer must be discarded"}

    async def second():
        next_started.set()
        return {"response": "next answer"}

    one = jobs.start("synthetic-user", "synthetic-thread", first)
    await started.wait()
    two = jobs.start("synthetic-user", "synthetic-thread", second)
    try:
        assert (await jobs.stop(one["request_id"]))["status"] == "stopping"
        await cleaning.wait()
        assert not next_started.is_set()
        assert (await jobs.stop(one["request_id"]))["status"] == "stopping"
        release.set()
        done = await finish(jobs, one["request_id"])
        assert done["status"] == "stopped" and done["response"] is None
        assert (await finish(jobs, two["request_id"]))["response"]["response"] == "next answer"
    finally:
        release.set()
        await jobs.shutdown()


@pytest.mark.asyncio
async def test_disconnected_stop_caller_does_not_mark_provider_cleanup_complete():
    jobs = AdminChatJobs(stop_timeout=1)
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
        return {"response": "discarded"}

    one = jobs.start("synthetic-user", "synthetic-thread", work)
    await started.wait()
    stop_call = asyncio.create_task(jobs.stop(one["request_id"]))
    try:
        await cleaning.wait()
        stop_call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop_call
        assert jobs.snapshot(one["request_id"])["status"] == "stopping"
        release.set()
        assert (await finish(jobs, one["request_id"]))["status"] == "stopped"
    finally:
        release.set()
        await jobs.shutdown()


@pytest.mark.asyncio
async def test_request_ids_are_idempotent_scoped_and_capacity_is_bounded():
    jobs = AdminChatJobs(limit=1)
    started = asyncio.Event()
    calls = []

    async def work():
        calls.append("one")
        started.set()
        await asyncio.Event().wait()

    one = jobs.start("synthetic-user", "synthetic-thread", work, request_id="synthetic-turn")
    await started.wait()
    try:
        assert jobs.start("synthetic-user", "synthetic-thread", work, request_id="synthetic-turn") == jobs.snapshot(one["request_id"])
        with pytest.raises(ValueError):
            jobs.start("synthetic-other-user", "synthetic-thread", work, request_id="synthetic-turn")
        with pytest.raises(RuntimeError):
            jobs.start("synthetic-user", "synthetic-thread", work)
        assert calls == ["one"]
        await jobs.stop(one["request_id"])
        new = jobs.start("synthetic-user", "synthetic-thread", work)
        assert new["request_id"] != one["request_id"]
    finally:
        await jobs.shutdown()
