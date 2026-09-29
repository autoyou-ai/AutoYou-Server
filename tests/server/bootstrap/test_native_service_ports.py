# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-41a379fad7b8673124167756

"""Service port changes never adopt or terminate an unrelated local listener."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import logging
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core_server import services

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-41a379fad7b8673124167756"


def runtime(monkeypatch):
    state = SimpleNamespace(agent_process=None, main_server_port=18081,
                            config={}, service_manager=SimpleNamespace(config=SimpleNamespace(ai_agent_server_port=18081)))
    value = SimpleNamespace(STATE=state, AI_AGENT_SERVER_PORT=18081,
        os=SimpleNamespace(environ={"AUTOYOU_NATIVE_OWNED_SERVER": "1"}), LOGGER=logging.getLogger(__name__),
        asyncio=SimpleNamespace(sleep=AsyncMock(), to_thread=asyncio.to_thread),
        stop_ai_agent_server=AsyncMock(), start_ai_agent_server_background=AsyncMock(return_value=True),
        _configured_ai_agent_bind_host=lambda _: "127.0.0.1")
    monkeypatch.setattr(services, "_runtime_module", value)
    return value


def test_restart_changes_port_only_after_stopping_owned_worker(monkeypatch):
    value = runtime(monkeypatch)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        chosen = probe.getsockname()[1]
    observed = []
    async def stop():
        observed.append(("stop", value.AI_AGENT_SERVER_PORT))
    async def start():
        observed.append(("start", value.AI_AGENT_SERVER_PORT))
        assert value.STATE.main_server_port == chosen
        assert value.STATE.service_manager.config.ai_agent_server_port == chosen
        assert value.os.environ["AUTOYOU_AI_PORT"] == str(chosen)
        return True
    value.stop_ai_agent_server.side_effect = stop
    value.start_ai_agent_server_background.side_effect = start
    assert asyncio.run(services.restart_ai_agent_server(port=chosen))
    assert observed == [("stop", 18081), ("start", chosen)]


def test_busy_port_leaves_current_worker_and_other_listener_untouched(monkeypatch):
    value = runtime(monkeypatch)
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        for port in (other.getsockname()[1], 65536, True):
            with pytest.raises(ValueError):
                asyncio.run(services.restart_ai_agent_server(port=port))
        assert other.fileno() >= 0
    value.stop_ai_agent_server.assert_not_awaited()
    value.start_ai_agent_server_background.assert_not_awaited()
    assert value.AI_AGENT_SERVER_PORT == 18081


def test_owned_server_does_not_reuse_or_clean_up_a_foreign_worker(monkeypatch):
    value = runtime(monkeypatch)
    value._invalidate_admin_status_cache = Mock()
    value._normalize_probe_host = lambda host: host
    value._is_agent_process_running = lambda _: False
    value._ensure_local_ollama_runtime_ready = Mock()
    value.is_port_in_use = lambda *_: True
    value._is_ai_agent_server_healthy = Mock(return_value=True)
    value._cleanup_lingering_ai_agent_processes = Mock()
    value.sync_managed_frontend_backends = AsyncMock()
    assert asyncio.run(services.start_ai_agent_server_background()) is False
    value._is_ai_agent_server_healthy.assert_not_called()
    value._cleanup_lingering_ai_agent_processes.assert_not_called()


def test_native_launch_restores_saved_port_and_moves_away_from_foreign_listener(monkeypatch):
    value = runtime(monkeypatch)
    value.os.environ["AUTOYOU_NATIVE_AI_PORT_AUTOMATIC"] = "1"
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        saved = other.getsockname()[1]
        value.STATE.config = {"ai_agent": {"port": saved}}
        services.apply_native_saved_ai_port()
        assert value.AI_AGENT_SERVER_PORT != saved
        assert value.STATE.config["ai_agent"]["port"] == saved
        with socket.create_connection(("127.0.0.1", saved)):
            pass
    services.apply_native_saved_ai_port()
    assert value.AI_AGENT_SERVER_PORT == saved
    assert value.os.environ["AUTOYOU_AI_PORT"] == str(saved)
    assert value.STATE.main_server_port == saved


def test_explicit_launch_port_and_non_native_servers_keep_their_startup_choice(monkeypatch):
    value = runtime(monkeypatch)
    value.STATE.config = {"ai_agent": {"port": 19081}}
    # from __debug_provenance_e__ import pay
    services.apply_native_saved_ai_port()
    assert value.AI_AGENT_SERVER_PORT == 18081
    value.os.environ = {"AUTOYOU_NATIVE_AI_PORT_AUTOMATIC": "1"}
    services.apply_native_saved_ai_port()
    assert value.AI_AGENT_SERVER_PORT == 18081


def test_admin_save_uses_selected_ai_port_and_bounds_it(monkeypatch):
    import server
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        chosen = probe.getsockname()[1]
    config = server._default_config()
    monkeypatch.setattr(server, "_loaded_config_for_update", lambda: config)
    monkeypatch.setattr(server, "_save_and_reload_state_config", lambda cfg: cfg)
    monkeypatch.setattr(server.STATE, "config", config)
    monkeypatch.setattr(server, "_apply_ai_agent_runtime_settings", Mock())
    monkeypatch.setattr(server, "_client_name_history_enabled", lambda *_: True)
    monkeypatch.setattr(server, "_is_native_gateway_provider", lambda: False)
    monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", AsyncMock(return_value={}))
    restart = AsyncMock(return_value=True)
    monkeypatch.setattr(server, "restart_ai_agent_server", restart)
    asyncio.run(server._apply_admin_ui_config_update({"ai_agent": {"port": chosen}}))
    restart.assert_awaited_once_with(port=chosen)
    assert server.STATE.config["ai_agent"]["port"] == chosen
    with pytest.raises(ValueError):
        server._apply_admin_ui_config_patch(config, {"ai_agent": {"port": 65536}})
