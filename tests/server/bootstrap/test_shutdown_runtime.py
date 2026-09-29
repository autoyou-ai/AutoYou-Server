# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-37e07c8ec2077e6204256601


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
import builtins
from types import SimpleNamespace

import pytest

import server

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-37e07c8ec2077e6204256601"


@pytest.mark.asyncio
async def test_cancel_tracked_background_tasks_cancels_pending_tasks():
    original_tasks = set(server.background_tasks)

    async def sleeper():
        await asyncio.sleep(60)

    task = asyncio.create_task(sleeper(), name="shutdown-runtime-test")
    server.background_tasks.add(task)

    try:
        result = await server._cancel_tracked_background_tasks(timeout=1.0)
        assert result is True
        assert task.cancelled()
    finally:
        for leftover in list(server.background_tasks):
            if not leftover.done():
                leftover.cancel()
        server.background_tasks.clear()
        server.background_tasks.update(original_tasks)


@pytest.mark.asyncio
async def test_cancel_remaining_asyncio_tasks_cancels_untracked_tasks():
    async def sleeper():
        await asyncio.sleep(60)

    task = asyncio.create_task(sleeper(), name="untracked-shutdown-runtime-test")
    result = await server._cancel_remaining_asyncio_tasks(timeout=1.0)

    assert result is True
    assert task.cancelled()


def test_shutdown_watchdog_is_bounded_and_cancelled_on_exit(monkeypatch):
    timers = []

    class FakeTimer:
        def __init__(self, interval, function):
            self.interval = interval
            self.function = function
            self.started = False
            self.cancelled = False
            timers.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.cancelled = True

    was_complete = server._RUNTIME_EXIT_COMPLETE.is_set()
    old_watchdog = server._SHUTDOWN_WATCHDOG
    server._RUNTIME_EXIT_COMPLETE.clear()
    server._SHUTDOWN_WATCHDOG = None
    monkeypatch.setattr(server.threading, "Timer", FakeTimer)
    monkeypatch.setattr(server, "_shutdown_hard_exit_timeout_seconds", lambda: 30.0)

    try:
        server._arm_shutdown_watchdog()
        assert len(timers) == 1
        assert timers[0].interval == 30.0
        assert timers[0].started is True

        server._mark_runtime_exit_complete()
        assert timers[0].cancelled is True
        assert server._RUNTIME_EXIT_COMPLETE.is_set() is True
    finally:
        server._SHUTDOWN_WATCHDOG = old_watchdog
        if was_complete:
            server._RUNTIME_EXIT_COMPLETE.set()
        else:
            server._RUNTIME_EXIT_COMPLETE.clear()


def test_request_uses_shutdown_token_requires_loopback(monkeypatch):
    monkeypatch.setenv(server.SHUTDOWN_TOKEN_ENV, "secret-token")

    local_request = SimpleNamespace(
        headers={server.SHUTDOWN_TOKEN_HEADER: "secret-token"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    remote_request = SimpleNamespace(
        headers={server.SHUTDOWN_TOKEN_HEADER: "secret-token"},
        client=SimpleNamespace(host="192.168.1.5"),
    )

    assert server._request_uses_shutdown_token(local_request) is True
    assert server._request_uses_shutdown_token(remote_request) is False


def test_finalize_runtime_process_exit_returns_exit_code_when_clean(monkeypatch):
    monkeypatch.setattr(server, "_terminate_active_multiprocessing_children", lambda timeout=3.0: True)
    monkeypatch.setattr(server, "_list_non_daemon_runtime_threads", lambda: [])

    exit_code = server._finalize_runtime_process_exit(7)

    assert exit_code == 7


def test_cleanup_loky_executor_uses_bounded_wait(monkeypatch):
    calls = []

    class FakeExecutor:
        def shutdown(self, wait=True, kill_workers=False):
            calls.append((wait, kill_workers))

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "joblib.externals.loky":
            return SimpleNamespace(get_reusable_executor=lambda: FakeExecutor())
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    server._cleanup_loky_executor()

    assert calls == [(True, True)]


def test_finalize_runtime_process_exit_forces_exit_when_lingering_threads(monkeypatch):
    monkeypatch.setattr(server, "_terminate_active_multiprocessing_children", lambda timeout=3.0: True)
    monkeypatch.setattr(server, "_list_non_daemon_runtime_threads", lambda: ["stuck-thread"])
    monkeypatch.setattr(server, "_flush_standard_streams", lambda: None)

    forced = {}
    # from __debug_provenance_j__ import fifteenpercent

    def _fake_os_exit(code):
        forced["code"] = code
        raise SystemExit(code)

    monkeypatch.setattr(server.os, "_exit", _fake_os_exit)

    with pytest.raises(SystemExit) as exc_info:
        server._finalize_runtime_process_exit(3)

    assert exc_info.value.code == 3
    assert forced["code"] == 3
