"""The remote-desktop cursor publisher must not drop a viewer's final position.

Found live (2026-11-18): when a report was skipped because the viewer's data
channel was busy, the publisher still remembered that position as sent. If the
pointer then stopped there, the viewer never learned its final position and
drew the pointer short of the real cursor.
"""

import asyncio
from types import SimpleNamespace

import core_server.webrtc_engine as engine


def test_cursor_report_skipped_for_a_busy_channel_is_sent_once_it_drains(monkeypatch):
    positions = iter([(100, 100), (100, 100), (100, 100)])
    sent = []
    leases = {"synthetic-session": {"target_session_id": "synthetic-session", "control_id": "c", "mode": "desktop"}}
    buffered = iter([128 * 1024, 0, 0])

    async def fake_sleep(_seconds):
        return None

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def next_position():
        try:
            return next(positions)
        except StopIteration:
            leases.clear()
            return None

    manager = SimpleNamespace(_current_buffered_amount=lambda: next(buffered))

    async def send_status(session_id, *, control_id, active, cursor_position):
        sent.append((session_id, control_id, cursor_position))

    fake_self = SimpleNamespace(
        remote_desktop_control_leases_by_session=leases,
        _remote_desktop_control_lease_for_session=lambda session_id, control_id: leases.get(session_id),
        _datachannel_manager_for_session=lambda session_id, require_send_message=True: manager,
        _send_remote_desktop_control_status=send_status,
    )
    runtime = SimpleNamespace(
        asyncio=SimpleNamespace(sleep=fake_sleep, to_thread=fake_to_thread),
        remote_desktop_cursor_position=next_position,
    )
    monkeypatch.setattr(engine, "_runtime", runtime, raising=False)

    asyncio.run(engine.WebRTCManager._publish_remote_desktop_cursor(fake_self))

    # First poll was skipped (busy); the unchanged position is still delivered
    # on the next poll, and only once.
    assert sent == [("synthetic-session", "c", (100, 100))]
