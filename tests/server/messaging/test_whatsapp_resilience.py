# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-to-cf69f1c5b37c4c08657ae921


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
import json
import logging
import os
import sys
import shutil
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-to-cf69f1c5b37c4c08657ae921"


ensure_repo_on_path()

import server
import whatsapp_service
from shared.session_execution import SessionExecutionManager
from whatsapp_service import WhatsAppService


@pytest.fixture(autouse=True)
def _disable_keystore(monkeypatch):
    monkeypatch.setattr(server, "_get_server_keystore", lambda: None)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: False)


def _capture_config_session():
    return (
        server.STATE.server_password,
        server.STATE.config_unlock_password,
        server.STATE.config_store,
    )


def _restore_config_session(snapshot):
    server._set_config_session(
        config_store=snapshot[2],
        server_password=snapshot[0],
        config_unlock_password=snapshot[1],
    )


def _set_encrypted_session(password: str):
    server._set_config_session(
        config_store=server.CONFIG_STORE_ENCRYPTED,
        server_password=password,
        config_unlock_password=password,
    )


def _same_origin_headers() -> dict[str, str]:
    return {
        "Origin": "http://testserver",
        "Referer": "http://testserver/admin",
    }


def _admin_test_client() -> TestClient:
    """Return a TestClient carrying an admin session.

    These endpoints expose pairing QR codes, message logs and destructive
    service controls, so they require authentication just like their
    ``/api/*/send`` siblings. The admin UI supplies the session cookie on every
    call; tests have to do the same.
    """
    client = TestClient(server.admin_app)
    server.ADMIN_SESSIONS["test-admin-session"] = True
    client.cookies.set("admin_session", "test-admin-session")
    return client


def test_whatsapp_self_message_verification_rejects_non_self_sent_chat():
    service = WhatsAppService()
    service.phone_number = "+12125550100"

    assert not service._is_verified_inbound_self_message(
        {
            "fromMe": True,
            "selfChat": False,
            "from": "+12125550100@c.us",
            "to": "+12125550101@c.us",
            "remoteChatId": "+12125550101@c.us",
        }
    )


def test_whatsapp_self_message_verification_accepts_owner_self_chat():
    service = WhatsAppService()
    service.phone_number = "+12125550100"

    assert service._is_verified_inbound_self_message(
        {
            "fromMe": True,
            "selfChat": True,
            "from": "+12125550100@c.us",
            "to": "+12125550100@c.us",
            "remoteChatId": "+12125550100@c.us",
        }
    )


def test_whatsapp_self_message_verification_accepts_lid_self_chat_after_status_snapshot():
    service = WhatsAppService()
    service.phone_number = "12125550100"
    service.last_self_chat_id = "autoyou-test-self@lid"

    assert service._is_verified_inbound_self_message(
        {
            "fromMe": True,
            "selfChat": True,
            "from": "12125550100@c.us",
            "to": "autoyou-test-self@lid",
            "remoteChatId": "autoyou-test-self@lid",
        }
    )


def test_whatsapp_self_message_verification_accepts_lid_self_chat_from_linked_qr_device():
    service = WhatsAppService()
    service.phone_number = "12125550100"

    assert service._is_verified_inbound_self_message(
        {
            "fromMe": True,
            "selfChat": True,
            "from": "12125550100@c.us",
            "to": "autoyou-test-self@lid",
            "remoteChatId": "autoyou-test-self@lid",
        }
    )


def test_whatsapp_self_message_verification_rejects_self_chat_without_from_me():
    service = WhatsAppService()
    service.phone_number = "12125550100"
    service.last_self_chat_id = "autoyou-test-self@lid"

    assert not service._is_verified_inbound_self_message(
        {
            "fromMe": False,
            "selfChat": True,
            "from": "12125550100@c.us",
            "to": "autoyou-test-self@lid",
            "remoteChatId": "autoyou-test-self@lid",
        }
    )


@pytest.mark.asyncio
async def test_whatsapp_server_name_updates_without_renaming_linked_device():
    service = WhatsAppService(device_name="Stable synthetic device")

    class DummyWebSocket:
        closed = False

        def __init__(self):
            self.messages = []

        async def send(self, message):
            self.messages.append(message)

    websocket = DummyWebSocket()
    service.websocket = websocket

    assert await service.update_server_name("Synthetic Server Name") is True
    assert service.device_name == "Stable synthetic device"
    assert json.loads(websocket.messages[0]) == {
        "action": "set_server_name",
        "data": {"serverName": "Synthetic Server Name"},
    }


@pytest.mark.asyncio
async def test_whatsapp_restart_deduplicates_concurrent_requests():
    service = WhatsAppService()
    stop_calls = []
    start_calls = []

    async def fake_stop():
        stop_calls.append(True)
        await asyncio.sleep(0.05)

    async def fake_start():
        start_calls.append(True)
        await asyncio.sleep(0.05)
        return True

    service.stop = fake_stop  # type: ignore[assignment]
    service.start = fake_start  # type: ignore[assignment]
    service.is_paired = True
    service.phone_number = "+12125550100"

    results = await asyncio.gather(service.restart(), service.restart())

    assert results == [True, True]
    assert stop_calls == [True]
    assert start_calls == [True]


def test_whatsapp_check_nodejs_falls_back_to_bundle_runtime(tmp_path, monkeypatch):
    service = WhatsAppService()
    node_dir = tmp_path / "node" / "whatsapp"
    bundled_node = tmp_path / "runtime" / "node" / "bin" / "node"
    node_dir.mkdir(parents=True)
    bundled_node.parent.mkdir(parents=True)
    bundled_node.write_text("", encoding="utf-8")

    attempted = []

    def fake_run(cmd, capture_output=True, text=True, timeout=5):
        attempted.append(cmd[0])
        if cmd[0] == "node":
            raise FileNotFoundError("node not on PATH")
        return SimpleNamespace(returncode=0, stdout="v22.16.0\n", stderr="")

    service.node_dir = node_dir
    service.node_command = "node"
    monkeypatch.setattr(whatsapp_service.subprocess, "run", fake_run)

    assert service._check_nodejs() is True
    assert service.node_command == str(bundled_node.resolve())
    assert attempted == ["node", str(bundled_node.resolve())]


def test_whatsapp_browser_discovery_uses_playwright_cache(tmp_path, monkeypatch):
    browsers_root = tmp_path / "ms-playwright"
    chrome = browsers_root / "chromium-1208" / "chrome-win64" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("", encoding="utf-8")

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers_root))

    assert whatsapp_service._find_local_chromium_executable() == chrome


@pytest.mark.asyncio
async def test_whatsapp_cleanup_session_reports_partial_delete(tmp_path, monkeypatch):
    service = WhatsAppService()
    service.node_dir = str(tmp_path)
    auth_dir = tmp_path / ".wwebjs_auth"
    cache_dir = tmp_path / ".wwebjs_cache"
    auth_dir.mkdir()
    cache_dir.mkdir()

    async def fake_stop():
        return None

    service.stop = fake_stop  # type: ignore[assignment]
    async def fake_remove_tree(path: str, label: str, attempts: int = 8, initial_delay: float = 0.5):
        return False

    service._remove_tree_with_retries = fake_remove_tree  # type: ignore[assignment]

    assert await service.cleanup_session() is False
    assert auth_dir.exists()
    assert cache_dir.exists()


@pytest.mark.asyncio
async def test_whatsapp_stop_requests_graceful_shutdown_before_force(monkeypatch):
    service = WhatsAppService()
    force_kill_calls = []

    class DummyProcess:
        def __init__(self):
            self.pid = 4321
            self.returncode = None
            self.terminate_calls = 0
            self.kill_calls = 0

        async def wait(self):
            self.returncode = 0
            return 0

        def terminate(self):
            self.terminate_calls += 1

        def kill(self):
            self.kill_calls += 1

    class DummyWebSocket:
        def __init__(self):
            self.closed = False
            self.messages = []

        async def send(self, message):
            self.messages.append(message)

        async def close(self):
            self.closed = True

    async def fake_force_kill(process_pid: int, extra_pids=None):
        force_kill_calls.append((process_pid, extra_pids))

    async def fake_cleanup_lingering():
        return None

    service.node_process = DummyProcess()
    service.websocket = DummyWebSocket()
    service._collect_process_tree_pids = lambda pid: [pid]  # type: ignore[assignment]
    service._force_kill_process_tree = fake_force_kill  # type: ignore[assignment]
    service._cleanup_lingering_whatsapp_processes = fake_cleanup_lingering  # type: ignore[assignment]

    await service.stop()

    assert service.websocket is None
    assert force_kill_calls == []


@pytest.mark.asyncio
async def test_whatsapp_stop_force_kills_only_when_tracked_process_still_live(monkeypatch):
    service = WhatsAppService()
    force_kill_calls = []

    class DummyProcess:
        def __init__(self):
            self.pid = 4321
            self.returncode = None

        async def wait(self):
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = 0

    class DummyWebSocket:
        closed = False

        async def send(self, _message):
            return None

        async def close(self):
            self.closed = True

    async def fake_force_kill(process_pid: int, extra_pids=None):
        force_kill_calls.append((process_pid, extra_pids))

    service.node_process = DummyProcess()
    service.websocket = DummyWebSocket()
    service._collect_process_tree_pids = lambda pid: [pid, 9876]  # type: ignore[assignment]
    service._live_process_tree_pids = lambda process_pid, extra_pids=None: [9876]  # type: ignore[assignment]
    service._force_kill_process_tree = fake_force_kill  # type: ignore[assignment]
    service._cleanup_lingering_whatsapp_processes = lambda: asyncio.sleep(0)  # type: ignore[assignment]

    await service.stop()

    assert force_kill_calls == [(4321, [9876])]


@pytest.mark.asyncio
async def test_whatsapp_start_attaches_existing_bridge_before_cleanup_or_spawn():
    service = WhatsAppService()
    calls = []

    async def fake_attach():
        calls.append("attach")
        service._attached_external_node = True
        return True

    async def fail_async_call():
        raise AssertionError("startup should not clean up or spawn when attach succeeds")

    service._attach_existing_websocket_bridge = fake_attach  # type: ignore[assignment]
    service._cleanup_lingering_whatsapp_processes = fail_async_call  # type: ignore[assignment]
    service._start_node_client = fail_async_call  # type: ignore[assignment]
    service._check_nodejs = lambda: (_ for _ in ()).throw(AssertionError("node probe should not run"))  # type: ignore[assignment]

    assert await service.start() is True
    assert calls == ["attach"]
    assert service._attached_external_node is True


@pytest.mark.asyncio
async def test_whatsapp_attach_existing_bridge_marks_external(monkeypatch):
    service = WhatsAppService()
    calls = []

    async def fake_wait_for_websocket(timeout=30, *, log_failure=True):
        calls.append(("wait", timeout, log_failure))
        return True

    async def fake_connect_websocket():
        calls.append(("connect", None))
        return True

    monkeypatch.setattr(whatsapp_service, "websockets", object())
    service.node_process = object()
    service._wait_for_websocket = fake_wait_for_websocket  # type: ignore[assignment]
    service._connect_websocket = fake_connect_websocket  # type: ignore[assignment]

    assert await service._attach_existing_websocket_bridge() is True
    assert calls == [("wait", 3, False), ("connect", None)]
    assert service.node_process is None
    assert service._attached_external_node is True


@pytest.mark.asyncio
async def test_whatsapp_stop_detaches_external_bridge_without_shutdown_or_cleanup():
    service = WhatsAppService()
    cleanup_calls = []

    class DummyWebSocket:
        def __init__(self):
            self.closed = False
            self.messages = []

        async def send(self, message):
            self.messages.append(message)

        async def close(self):
            self.closed = True

    async def fake_cleanup_lingering():
        cleanup_calls.append(True)

    websocket = DummyWebSocket()
    service.websocket = websocket
    service._attached_external_node = True
    service._cleanup_lingering_whatsapp_processes = fake_cleanup_lingering  # type: ignore[assignment]

    await service.stop()

    assert websocket.closed is True
    assert websocket.messages == []
    assert cleanup_calls == []
    assert service._attached_external_node is False


def test_whatsapp_log_helpers_redact_identifiers():
    service = WhatsAppService()
    service.phone_number = "+12125550100"

    snapshot = service._status_snapshot()
    assert "+12125550100" not in snapshot
    assert "***0100" in snapshot

    summary = service._payload_summary(
        {
            "phone_number": "+12125550100",
            "phoneNumber": "+12125550100",
            "self_chat_id": "12125550100@c.us",
            "nested": {"to": "+12125550100"},
        }
    )
    assert "+12125550100" not in summary
    assert "12125550100@c.us" not in summary
    assert "***0100" in summary


def test_whatsapp_freeform_log_redaction_strips_identifiers():
    raw = (
        "[WhatsApp] Ignoring non-self sent message event=message_create "
        "from=15555550100@c.us to=15555550101@lid "
        "chat=120363555501234567@g.us chatContactIsMe=false"
    )

    redacted = whatsapp_service._redact_whatsapp_log_line(raw)

    assert "15555550100" not in redacted
    assert "15555550101" not in redacted
    assert "120363555501234567" not in redacted
    assert "***0100@c.us" in redacted
    assert "***0101@lid" in redacted
    assert "***4567@g.us" in redacted
    assert "event=message_create" in redacted
    assert "chatContactIsMe=false" in redacted
    assert whatsapp_service._redact_whatsapp_log_line("[WebSocket] Server successfully listening on port 8083") == (
        "[WebSocket] Server successfully listening on port 8083"
    )


@pytest.mark.asyncio
async def test_whatsapp_node_output_monitor_suppresses_optional_diagnostics_by_default(caplog, monkeypatch):
    service = WhatsAppService()
    monkeypatch.delenv("AUTOYOU_WHATSAPP_OBFUSCATED_LOGGING", raising=False)
    monkeypatch.delenv("WHATSAPP_OBFUSCATED_LOGGING", raising=False)
    monkeypatch.delenv("AUTOYOU_WHATSAPP_LOG_NON_SELF_SENT", raising=False)
    monkeypatch.delenv("WHATSAPP_LOG_NON_SELF_SENT", raising=False)

    class DummyStream:
        def __init__(self, lines):
            self.lines = [line.encode("utf-8") for line in lines]

        async def readline(self):
            if self.lines:
                return self.lines.pop(0)
            return b""

    class DummyProcess:
        pid = 98765
        stdout = DummyStream(
            [
                "[WhatsApp] Ignoring non-self sent message event=message_create from=15555550100@c.us to=15555550101@lid chat=120363555501234567@g.us",
            ]
        )
        stderr = DummyStream(
            [
                "[WhatsApp] Contact lookup failed, continuing without contact metadata: Cannot read properties of undefined (reading '_serialized')",
                "[WhatsApp] Error sending message to +15555550100: synthetic failure",
            ]
        )

    caplog.set_level(logging.INFO, logger="autoyou.whatsapp_service")

    await service._monitor_node_output(DummyProcess())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Ignoring non-self sent message" not in logged
    assert "Contact lookup failed" not in logged
    assert "event=message_create" not in logged
    assert "15555550100" not in logged
    assert "15555550101" not in logged
    assert "120363555501234567" not in logged
    assert "+***0100" in logged


@pytest.mark.asyncio
async def test_whatsapp_node_output_monitor_redacts_child_logs_when_enabled(caplog, monkeypatch):
    service = WhatsAppService()
    monkeypatch.setenv("AUTOYOU_WHATSAPP_OBFUSCATED_LOGGING", "1")

    class DummyStream:
        def __init__(self, lines):
            self.lines = [line.encode("utf-8") for line in lines]

        async def readline(self):
            if self.lines:
                return self.lines.pop(0)
            return b""

    class DummyProcess:
        pid = 98765
        stdout = DummyStream(
            [
                "[WhatsApp] Ignoring non-self sent message event=message_create from=15555550100@c.us to=15555550101@lid chat=120363555501234567@g.us",
            ]
        )
        stderr = DummyStream(
            [
                "[WhatsApp] Contact lookup failed, continuing without contact metadata: Cannot read properties of undefined (reading '_serialized')",
                "[WhatsApp] Error sending message to +15555550100: synthetic failure",
            ]
        )

    caplog.set_level(logging.INFO, logger="autoyou.whatsapp_service")

    await service._monitor_node_output(DummyProcess())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "15555550100" not in logged
    assert "15555550101" not in logged
    assert "120363555501234567" not in logged
    assert "***0100@c.us" in logged
    assert "***0101@lid" in logged
    assert "***4567@g.us" in logged
    assert "+***0100" in logged
    assert "event=message_create" in logged
    assert "Contact lookup failed" in logged


@pytest.mark.asyncio
async def test_whatsapp_message_processing_log_omits_body(caplog, monkeypatch):
    service = WhatsAppService()
    service.phone_number = "+15555550100"
    secret_body = "synthetic private WhatsApp message body"
    forwarded = []

    async def fake_pairing_process_message(**_kwargs):
        return None

    async def fake_forward_to_chat_api(message_text, context=None, *, reply_chat_id=None):
        forwarded.append((message_text, context, reply_chat_id))

    monkeypatch.setattr(whatsapp_service.pairing_router, "process_message", fake_pairing_process_message)
    service._forward_to_chat_api = fake_forward_to_chat_api  # type: ignore[assignment]
    caplog.set_level(logging.INFO, logger="autoyou.whatsapp_service")

    await service._handle_incoming_message(
        {
            "id": "synthetic-message-id",
            "body": secret_body,
            "fromMe": True,
            "selfChat": True,
            "from": "+15555550100@c.us",
            "to": "+15555550100@c.us",
            "remoteChatId": "15555550100@c.us",
            "sourceEvent": "message_create",
        }
    )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_body not in logged
    assert f"len={len(secret_body)}" in logged
    assert forwarded == [(secret_body, [], "15555550100@c.us")]


@pytest.mark.asyncio
async def test_whatsapp_send_logs_omit_message_body_and_redact_recipient(caplog):
    service = WhatsAppService()
    service.phone_number = "+15555550100"
    secret_body = "synthetic private WhatsApp reply"

    class DummyWebSocket:
        closed = False

        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(payload)

    websocket = DummyWebSocket()
    service.websocket = websocket
    caplog.set_level(logging.INFO, logger="autoyou.whatsapp_service")

    await service._send_message_to_self(secret_body, append_signature=False)
    assert await service.send_message("+15555550101", secret_body) is True

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_body not in logged
    assert "+15555550100" not in logged
    assert "+15555550101" not in logged
    assert "Sent response to WhatsApp len=" in logged
    assert "[WhatsApp Service] Sent message to +***0101 len=" in logged
    assert len(websocket.sent) == 2
    assert secret_body in websocket.sent[0]
    assert secret_body in websocket.sent[1]


@pytest.mark.asyncio
async def test_whatsapp_remove_tree_with_retries_succeeds_after_transient_lock(tmp_path, monkeypatch):
    service = WhatsAppService()
    locked_dir = tmp_path / ".wwebjs_auth"
    locked_dir.mkdir()
    real_rmtree = shutil.rmtree
    attempts = {"count": 0}

    def flaky_rmtree(path, onerror=None):
        attempts["count"] += 1
        if attempts["count"] < 3:
            return None
        return real_rmtree(path, onerror=onerror)

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    result = await service._remove_tree_with_retries(str(locked_dir), ".wwebjs_auth", attempts=4, initial_delay=0.01)

    assert result is True
    assert attempts["count"] == 3
    assert not locked_dir.exists()


@pytest.mark.asyncio
async def test_whatsapp_control_channel_recovery_reconnects_before_restart():
    service = WhatsAppService()

    class DummyProcess:
        returncode = None

    class DummyWebSocket:
        closed = False
        # from __debug_provenance_d__ import to

    reconnect_calls = []
    restart_calls = []

    async def fake_connect():
        reconnect_calls.append(True)
        service.websocket = DummyWebSocket()
        return True

    async def fake_restart():
        restart_calls.append(True)
        return True

    service.node_process = DummyProcess()
    service._connect_websocket = fake_connect  # type: ignore[assignment]
    service.restart = fake_restart  # type: ignore[assignment]

    await service._recover_control_channel("test")

    assert reconnect_calls == [True]
    assert restart_calls == []


@pytest.mark.asyncio
async def test_whatsapp_ready_control_channel_recovery_waits_longer_before_restart(monkeypatch):
    service = WhatsAppService()

    class DummyProcess:
        returncode = None

    reconnect_calls = []
    restart_calls = []
    sleep_calls = []

    async def fake_connect():
        reconnect_calls.append(True)
        return False

    async def fake_restart():
        restart_calls.append(True)
        return True

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    service.node_process = DummyProcess()
    service.connection_status = "connected"
    service.is_ready = True
    service.client_state = "READY"
    service._connect_websocket = fake_connect  # type: ignore[assignment]
    service.restart = fake_restart  # type: ignore[assignment]
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await service._recover_control_channel("connection_closed")

    assert len(reconnect_calls) == service._ready_control_recovery_attempts
    assert restart_calls == [True]
    assert sleep_calls == [2.0, 4.0, 6.0, 8.0, 8.0, 8.0]


@pytest.mark.asyncio
async def test_whatsapp_control_channel_recovery_restarts_when_node_not_running():
    service = WhatsAppService()
    restart_calls = []

    async def fake_restart():
        restart_calls.append(True)
        return True

    service.restart = fake_restart  # type: ignore[assignment]

    await service._recover_control_channel("test")

    assert restart_calls == [True]


@pytest.mark.asyncio
async def test_whatsapp_recovery_handoff_clears_recovery_task_before_restart(monkeypatch):
    service = WhatsAppService()
    observed_recovery_tasks = []

    class DummyProcess:
        returncode = None

    async def fake_connect():
        return False

    async def fake_start():
        return True

    async def fake_stop():
        observed_recovery_tasks.append(service._control_channel_recovery_task)
        service._shutdown_in_progress = False
        service._stop_requested = False

    async def fast_sleep(_seconds):
        return None

    service.node_process = DummyProcess()
    service._connect_websocket = fake_connect  # type: ignore[assignment]
    service.start = fake_start  # type: ignore[assignment]
    service.stop = fake_stop  # type: ignore[assignment]
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    recovery_task = asyncio.create_task(service._recover_control_channel("connection_closed"))
    service._control_channel_recovery_task = recovery_task

    await recovery_task

    assert observed_recovery_tasks == [None]
    assert service._restart_task is None


@pytest.mark.asyncio
async def test_whatsapp_stop_cancels_pending_restart_task():
    service = WhatsAppService()
    pending_restart = asyncio.create_task(asyncio.sleep(60))
    service._restart_task = pending_restart

    await service.stop()

    assert pending_restart.cancelled()


@pytest.mark.asyncio
async def test_whatsapp_stop_does_not_cancel_current_restart_request_task():
    service = WhatsAppService()
    current_task = asyncio.current_task()
    assert current_task is not None
    service._restart_request_tasks.add(current_task)

    await service.stop()

    assert not current_task.cancelled()


@pytest.mark.asyncio
async def test_whatsapp_restart_stop_does_not_cancel_restart_request_waiter():
    service = WhatsAppService()
    current_task = asyncio.current_task()
    assert current_task is not None
    waiter = asyncio.create_task(asyncio.sleep(60))
    service._restart_task = current_task
    service._restart_request_tasks.add(waiter)

    try:
        await service.stop()

        assert not waiter.cancelled()
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        service._restart_task = None


@pytest.mark.asyncio
async def test_whatsapp_client_restart_request_ignored_during_shutdown(monkeypatch):
    service = WhatsAppService()
    service._shutdown_in_progress = True
    restart_calls = []

    async def fake_restart():
        restart_calls.append(True)
        return True

    async def fast_sleep(_seconds):
        return None

    service.restart = fake_restart  # type: ignore[assignment]
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    await service._handle_service_restart_request({"reason": "test"})

    assert restart_calls == []


@pytest.mark.asyncio
async def test_whatsapp_forward_to_chat_api_uses_typing_without_text_ack(monkeypatch):
    service = WhatsAppService()
    service.phone_number = "+12125550100"
    sent_messages = []

    class DummyExecutionManager:
        def bind_transport_owner(self, transport: str, sender_id: str, *, raw_session_id=None):
            return SimpleNamespace(
                canonical_session_id=f"session::{transport}:{sender_id}",
                canonical_user_id=f"user::{transport}:{sender_id}",
                owner_key=f"{transport}:{sender_id}",
            )

        async def submit_turn(self, identity, handler, *, on_status=None, timeout_seconds=None, label="chat"):
            if on_status:
                await on_status(SimpleNamespace(status="queued", queue_position=2))
            return SimpleNamespace(response="final reply")

    async def fake_send_message_to_self(message: str, append_signature: bool = True):
        sent_messages.append((message, append_signature))

    async def fake_send_typing(_chat_id: str):
        return True

    async def fake_stop_typing(_chat_id: str):
        return True

    async def fake_stop_typing_indicator():
        return None

    monkeypatch.setattr(whatsapp_service, "get_session_execution_manager", lambda: DummyExecutionManager())
    service._send_message_to_self = fake_send_message_to_self  # type: ignore[assignment]
    service.send_typing = fake_send_typing  # type: ignore[assignment]
    service.stop_typing = fake_stop_typing  # type: ignore[assignment]
    service._stop_typing_indicator = fake_stop_typing_indicator  # type: ignore[assignment]

    await service._forward_to_chat_api("hello")
    await asyncio.gather(*list(service.chat_tasks))

    assert sent_messages == [("final reply", True)]


@pytest.mark.asyncio
async def test_whatsapp_forward_to_chat_api_attaches_conversation_metadata(monkeypatch):
    import rest_api

    service = WhatsAppService()
    service.phone_number = "+12125550100"
    captured = []

    class DummyExecutionManager:
        def bind_transport_owner(self, transport: str, sender_id: str, *, raw_session_id=None):
            return SimpleNamespace(
                transport=transport,
                sender_id=sender_id,
                raw_session_id=raw_session_id,
                canonical_session_id=f"session::{transport}:{sender_id}",
                canonical_user_id=f"user::{transport}:{sender_id}",
                owner_key=f"{transport}:{sender_id}",
                thread_id=2,
            )

        async def submit_turn(self, identity, handler, *, on_status=None, timeout_seconds=None, label="chat"):
            if on_status:
                await on_status(SimpleNamespace(status="queued", queue_position=1))
            return await handler()

    async def fake_process_chat_message(chat_request, ai_agent_url=None, on_chunk=None, on_media_reply=None):
        del ai_agent_url, on_chunk, on_media_reply
        captured.append(chat_request)
        return SimpleNamespace(response="stored", media_reply_attachments=[])

    monkeypatch.setattr(whatsapp_service, "get_session_execution_manager", lambda: DummyExecutionManager())
    monkeypatch.setattr(whatsapp_service, "_resolve_conversation_identity", lambda identity, start_new_thread=False: identity)
    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)
    service._send_message_to_self = lambda *args, **kwargs: asyncio.sleep(0)  # type: ignore[assignment]
    service.send_typing = lambda *args, **kwargs: asyncio.sleep(0)  # type: ignore[assignment]
    service.stop_typing = lambda *args, **kwargs: asyncio.sleep(0)  # type: ignore[assignment]
    service._stop_typing_indicator = lambda: asyncio.sleep(0)  # type: ignore[assignment]

    await service._forward_to_chat_api("remember synthetic WhatsApp metadata")
    await asyncio.gather(*list(service.chat_tasks))

    metadata = captured[0].metadata
    assert metadata["client"] == "whatsapp"
    assert metadata["canonical_owner_key"] == "whatsapp:+12125550100"
    assert metadata["canonical_user_id"] == "user::whatsapp:+12125550100"
    assert metadata["canonical_session_id"] == "session::whatsapp:+12125550100"
    assert metadata["destination_session_id"] == "+12125550100"
    assert metadata["raw_session_id"] == "+12125550100"
    assert metadata["conversation_thread_id"] == 2


@pytest.mark.asyncio
async def test_whatsapp_preserves_older_reply_when_newer_message_arrives(monkeypatch):
    service = WhatsAppService()
    service.phone_number = "+12125550100"
    service.last_self_chat_id = "12125550100@c.us"
    sent_messages = []
    stop_calls = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class DummyExecutionManager:
        def __init__(self):
            self.calls = 0
            self._manager = SessionExecutionManager(queue_limit=10, default_turn_timeout_seconds=30.0)

        def bind_transport_owner(self, transport: str, sender_id: str, *, raw_session_id=None):
            return SimpleNamespace(
                canonical_session_id=f"session::{transport}:{sender_id}",
                canonical_user_id=f"user::{transport}:{sender_id}",
                owner_key=f"{transport}:{sender_id}",
            )

        async def submit_turn(self, identity, handler, *, on_status=None, timeout_seconds=None, label="chat"):
            async def fake_handler():
                self.calls += 1
                call_number = self.calls
                if call_number == 1:
                    first_started.set()
                    await release_first.wait()
                    return SimpleNamespace(response="old reply")
                return SimpleNamespace(response="new reply")

            return await self._manager.submit_turn(
                identity,
                fake_handler,
                on_status=on_status,
                timeout_seconds=timeout_seconds,
                label=label,
            )

    async def fake_send_message_to_self(message: str, append_signature: bool = True):
        sent_messages.append((message, append_signature))

    async def fake_send_typing(_chat_id: str):
        return True

    async def fake_stop_typing(_chat_id: str):
        stop_calls.append(True)
        return True

    async def fake_stop_typing_indicator():
        stop_calls.append(True)

    manager = DummyExecutionManager()
    monkeypatch.setattr(whatsapp_service, "get_session_execution_manager", lambda: manager)
    service._send_message_to_self = fake_send_message_to_self  # type: ignore[assignment]
    service.send_typing = fake_send_typing  # type: ignore[assignment]
    service.stop_typing = fake_stop_typing  # type: ignore[assignment]
    service._stop_typing_indicator = fake_stop_typing_indicator  # type: ignore[assignment]

    await service._forward_to_chat_api("first")
    await first_started.wait()
    await service._forward_to_chat_api("second")
    release_first.set()
    await asyncio.gather(*list(service.chat_tasks))

    assert sent_messages == [("old reply", True), ("new reply", True)]
    assert stop_calls == [True, True]


def test_admin_reset_whatsapp_restarts_after_cleanup(monkeypatch):
    original_service = server.STATE.whatsapp_service
    original_config = server.STATE.config
    original_session = _capture_config_session()

    class DummyWhatsAppService:
        def __init__(self):
            self.cleanup_calls = 0

        async def cleanup_session(self):
            self.cleanup_calls += 1
            return True

    cleanup_service = DummyWhatsAppService()
    restart_states = []
    saved_configs = []

    async def fake_restart():
        restart_states.append(server.STATE.whatsapp_service)
        server.STATE.whatsapp_service = object()

    def fake_save(config, password):
        saved_configs.append((config["whatsapp"]["paired"], config["whatsapp"]["phone_number"], password))

    try:
        server.STATE.whatsapp_service = cleanup_service
        server.STATE.config = {
            "whatsapp": {
                "enabled": True,
                "websocket_port": 8083,
                "device_name": "AutoYou-WhatsApp",
                "ai_api_url": "http://localhost:8081/api/chat",
                "paired": True,
                "phone_number": "+12125550100",
            }
        }
        _set_encrypted_session("secret")

        monkeypatch.setattr(server, "start_or_restart_whatsapp", fake_restart)
        monkeypatch.setattr(server, "save_encrypted_config", fake_save)

        with _admin_test_client() as client:
            response = client.post("/api/whatsapp/reset", headers=_same_origin_headers())

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["restarted"] is True
        assert cleanup_service.cleanup_calls == 1
        assert restart_states == [None]
        assert saved_configs == [(False, "", "secret")]
        assert server.STATE.config["whatsapp"]["paired"] is False
        assert server.STATE.config["whatsapp"]["phone_number"] == ""
    finally:
        server.STATE.whatsapp_service = original_service
        server.STATE.config = original_config
        _restore_config_session(original_session)


def test_admin_reset_whatsapp_works_without_running_service(monkeypatch):
    original_service = server.STATE.whatsapp_service
    original_config = server.STATE.config
    original_session = _capture_config_session()
    original_service_cls = server.WhatsAppService

    created_services = []
    restart_calls = []

    class DummyWhatsAppService:
        def __init__(self, websocket_port: int = 8083, device_name: str = "AutoYou-WhatsApp", ai_api_url: str = "http://localhost:8081/api/chat"):
            self.websocket_port = websocket_port
            self.device_name = device_name
            self.ai_api_url = ai_api_url
            self.cleanup_calls = 0
            created_services.append(self)

        async def cleanup_session(self):
            self.cleanup_calls += 1
            return True

    async def fake_restart():
        restart_calls.append(True)
        server.STATE.whatsapp_service = object()

    try:
        server.STATE.whatsapp_service = None
        server.STATE.config = {
            "whatsapp": {
                "enabled": True,
                "websocket_port": 9001,
                "device_name": "RePair",
                "ai_api_url": "http://localhost:9009/api/chat",
                "paired": True,
                "phone_number": "+12125550100",
            }
        }
        server._set_config_session(config_store=server.CONFIG_STORE_NONE)

        monkeypatch.setattr(server, "WhatsAppService", DummyWhatsAppService)
        monkeypatch.setattr(server, "start_or_restart_whatsapp", fake_restart)

        with _admin_test_client() as client:
            response = client.post("/api/whatsapp/reset", headers=_same_origin_headers())

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["restarted"] is True
        assert len(created_services) == 1
        assert created_services[0].websocket_port == 9001
        assert created_services[0].device_name == "RePair"
        assert created_services[0].ai_api_url == "http://localhost:9009/api/chat"
        assert created_services[0].cleanup_calls == 1
        assert restart_calls == [True]
        assert server.STATE.config["whatsapp"]["paired"] is False
        assert server.STATE.config["whatsapp"]["phone_number"] == ""
    finally:
        server.STATE.whatsapp_service = original_service
        server.STATE.config = original_config
        _restore_config_session(original_session)
        server.WhatsAppService = original_service_cls


def test_whatsapp_self_media_replies_carry_server_signature(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "get_configured_server_name", lambda: "Test AutoYou Server")
    service = WhatsAppService()
    service.phone_number = "12125550100"
    audio_path = tmp_path / "synthetic-reply.ogg"
    audio_path.write_bytes(b"OggS synthetic reply")
    image_path = tmp_path / "synthetic-reply.jpg"
    image_path.write_bytes(b"\xff\xd8synthetic reply\xff\xd9")
    signature = "~ Test AutoYou Server"

    voice_command = service._build_voice_reply_command_data(str(audio_path))
    media_command = service._build_media_reply_command_data(
        {"path": str(image_path), "filename": "synthetic-reply.jpg", "mimetype": "image/jpeg"},
        to=service.phone_number,
    )
    external_media_command = service._build_media_reply_command_data(
        {"path": str(image_path), "filename": "synthetic-reply.jpg", "mimetype": "image/jpeg"},
        to="12125550101",
    )

    assert voice_command["caption"] == signature
    assert media_command["caption"] == signature
    assert not external_media_command["caption"].endswith(signature)


@pytest.mark.asyncio
async def test_whatsapp_media_send_failure_ack_queues_for_retry(tmp_path):
    service = WhatsAppService()

    class FakeWebSocket:
        closed = False
        close_code = None

        def __init__(self):
            self.sent = []

        async def send(self, payload):
            command = json.loads(payload)
            self.sent.append(command)
            command_id = ((command.get("data") or {}).get("commandId") or "").strip()
            await service._handle_websocket_message(
                {
                    "event": "command_result",
                    "data": {
                        "action": "send_media",
                        "commandId": command_id,
                        "success": False,
                        "error": "synthetic sendMediaMessage failure",
                    },
                }
            )

    image_path = tmp_path / "synthetic-photo.jpg"
    image_path.write_bytes(b"\xff\xd8synthetic failed whatsapp image\xff\xd9")

    service.state_dir = tmp_path / "whatsapp"
    service._pending_media_reply_file = service.state_dir / "pending_media_replies.json"
    service._pending_media_replies = service._load_pending_media_replies()
    service.phone_number = "+12125550100"
    service.last_self_chat_id = "12125550100@c.us"
    service.websocket = FakeWebSocket()

    assert await service._send_media_attachments_to_self(
        [
            {
                "filename": "synthetic-photo.jpg",
                "mimetype": "image/jpeg",
                "path": str(image_path),
                "meta": {"kind": "image"},
            }
        ]
    ) is True

    assert len(service._pending_media_replies) == 1
    queued_json = json.loads(service._pending_media_reply_file.read_text(encoding="utf-8"))
    assert "data" not in queued_json[0]["command_data"]
    assert queued_json[0]["command_data"]["media_path"]


@pytest.mark.asyncio
async def test_whatsapp_status_starting_and_pairing_do_not_mark_ready():
    service = WhatsAppService()

    await service._handle_websocket_message({"event": "status", "data": "starting"})
    assert service.connection_status == "starting"
    assert service.is_ready is False
    assert service.is_paired is False

    await service._handle_websocket_message({"event": "qr", "data": "qr-payload"})
    assert service.connection_status == "pairing"
    assert service.last_qr_code == "qr-payload"
    assert service.is_ready is False
    assert service.is_paired is False

    await service._handle_websocket_message({"event": "status", "data": "authenticated"})
    assert service.connection_status == "authenticated"
    assert service.last_qr_code is None
    assert service.is_ready is False
    assert service.is_paired is False

    await service._handle_websocket_message({"event": "status", "data": "auth_stalled"})
    assert service.connection_status == "auth_stalled"
    assert service.is_ready is False
    assert service.is_paired is False


@pytest.mark.asyncio
async def test_whatsapp_auth_and_ready_snapshots_clear_stale_qr():
    service = WhatsAppService()

    await service._handle_websocket_message({"event": "qr", "data": "qr-payload"})
    assert service.last_qr_code == "qr-payload"

    await service._handle_websocket_message({"event": "status", "data": "starting"})
    assert service.last_qr_code is None

    await service._handle_websocket_message({"event": "qr", "data": "next-qr-payload"})
    assert service.last_qr_code == "next-qr-payload"

    await service._handle_websocket_message(
        {
            "event": "status_response",
            "data": {
                "ready": False,
                "qr_available": False,
                "current_service_status": "starting",
            },
        }
    )
    assert service.last_qr_code is None

    await service._handle_websocket_message({"event": "qr", "data": "ready-qr-payload"})
    assert service.last_qr_code == "ready-qr-payload"

    await service._handle_websocket_message({"event": "state_changed", "data": {"state": "READY"}})
    assert service.last_qr_code is None


@pytest.mark.asyncio
async def test_whatsapp_status_response_ready_marks_connected_and_survives_connected_state():
    service = WhatsAppService()

    await service._handle_websocket_message(
        {
            "event": "status_response",
            "data": {
                "ready": True,
                "phone_number": "12125550100",
                "self_chat_id": "autoyou-test-self@lid",
                "current_service_status": "connected",
                "web_state": "CONNECTED",
            },
        }
    )

    assert service.connection_status == "connected"
    assert service.is_ready is True
    assert service.is_paired is True
    assert service.last_self_chat_id == "autoyou-test-self@lid"

    await service._handle_websocket_message({"event": "state_changed", "data": {"state": "CONNECTED"}})

    assert service.connection_status == "connected"
    assert service.is_ready is True
    assert service.is_paired is True


def test_admin_whatsapp_qr_returns_pending_while_starting(monkeypatch):
    original_service = server.STATE.whatsapp_service

    class DummyWhatsAppService:
        async def get_status(self):
            return {
                "paired": False,
                "node_process_running": True,
                "status": "starting",
                "last_qr_available": False,
            }

        async def wait_for_qr_code(self, timeout_seconds: float = 12.0):
            return None

        async def get_qr_code_data_url(self):
            return None

    try:
        server.STATE.whatsapp_service = DummyWhatsAppService()
        with _admin_test_client() as client:
            response = client.get("/api/whatsapp/qr")

        assert response.status_code == 202
        body = response.json()
        assert body["success"] is False
        assert body["pending"] is True
        assert "still initializing" in body["error"]
    finally:
        server.STATE.whatsapp_service = original_service


def test_admin_whatsapp_qr_returns_already_paired_when_transport_ready():
    original_service = server.STATE.whatsapp_service

    class DummyWhatsAppService:
        async def get_status(self):
            return {
                "paired": False,
                "connection_healthy": True,
                "ready": True,
                "phone_number": "12125550100",
                "node_process_running": True,
            }

    try:
        server.STATE.whatsapp_service = DummyWhatsAppService()
        with _admin_test_client() as client:
            response = client.get("/api/whatsapp/qr")

        assert response.status_code == 400
        assert "already paired" in response.json()["error"]
    finally:
        server.STATE.whatsapp_service = original_service


def test_admin_whatsapp_status_uses_fast_snapshot_without_settle_sleep(monkeypatch):
    original_service = server.STATE.whatsapp_service
    original_config = server.STATE.config

    class DummyWhatsAppService:
        async def get_status(self):
            return {
                "paired": False,
                "connection_healthy": True,
                "ready": True,
                "phone_number": "12125550100",
                "status": "connected",
                "node_process_running": True,
            }

    async def fail_sleep(_seconds):
        raise AssertionError("admin status polling should not wait before reading WhatsApp status")

    try:
        server.STATE.whatsapp_service = DummyWhatsAppService()
        server.STATE.config = {"whatsapp": {"enabled": True}}
        server._invalidate_admin_status_cache("whatsapp_status")
        monkeypatch.setattr(server.asyncio, "sleep", fail_sleep)

        with _admin_test_client() as client:
            response = client.get("/api/whatsapp/status")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "connected"
        assert body["phone_number"] == "12125550100"
    finally:
        server._invalidate_admin_status_cache("whatsapp_status")
        server.STATE.whatsapp_service = original_service
        server.STATE.config = original_config


@pytest.mark.asyncio
async def test_whatsapp_status_helper_treats_ready_phone_as_connected():
    original_service = server.STATE.whatsapp_service
    original_config = server.STATE.config

    class DummyWhatsAppService:
        async def get_status(self):
            return {
                "paired": False,
                "connection_healthy": False,
                "ready": True,
                "phone_number": "12125550100",
                "status": "connected",
            }

    try:
        server.STATE.whatsapp_service = DummyWhatsAppService()
        server.STATE.config = {"whatsapp": {"enabled": True}}

        status, detail = await server._whatsapp_status()

        assert status == "Connected"
        assert detail == "12125550100"
    finally:
        server.STATE.whatsapp_service = original_service
        server.STATE.config = original_config


@pytest.mark.asyncio
async def test_whatsapp_pairing_status_returns_disabled_snapshot_when_service_disappears(monkeypatch):
    original_service = server.STATE.whatsapp_service
    original_config = server.STATE.config

    class DummyWhatsAppService:
        async def get_status(self):
            raise AssertionError("get_status should not be called after service teardown")

    async def fake_sleep(_seconds):
        server.STATE.whatsapp_service = None

    try:
        server.STATE.whatsapp_service = DummyWhatsAppService()
        server.STATE.config = {
            "whatsapp": {
                "enabled": False,
            }
        }
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        status = await server._check_and_update_whatsapp_pairing_status()

        assert status["status"] == "stopped"
        assert status["paired"] is False
        assert status["error"] == "Service disabled"
    finally:
        server.STATE.whatsapp_service = original_service
        server.STATE.config = original_config
