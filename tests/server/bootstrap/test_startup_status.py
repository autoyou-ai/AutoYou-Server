# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-0eae165d073f02f196ea298a


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"
import os
import sys
import asyncio
import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-0eae165d073f02f196ea298a"


ensure_repo_on_path()

import server
from core_server import services


def test_native_startup_service_skip_switch(monkeypatch):
    monkeypatch.setenv("AUTOYOU_SKIP_STARTUP_SERVICES", "yes")
    assert services._startup_services_are_skipped() is True
    monkeypatch.delenv("AUTOYOU_SKIP_STARTUP_SERVICES")
    assert services._startup_services_are_skipped() is False


def test_native_startup_can_autostart_ai_without_legacy_services(monkeypatch):
    monkeypatch.setenv("AUTOYOU_SKIP_STARTUP_SERVICES", "yes")
    monkeypatch.setenv("AUTOYOU_START_AI_WHEN_STARTUP_SERVICES_SKIPPED", "true")
    assert services._start_ai_when_startup_services_are_skipped() is True
    monkeypatch.setenv("AUTOYOU_START_AI_WHEN_STARTUP_SERVICES_SKIPPED", "0")
    assert services._start_ai_when_startup_services_are_skipped() is False


def test_page_service_auto_starts_from_main_bootstrap_config(monkeypatch):
    started = []
    monkeypatch.setattr(server.STATE, "config", {"autoyou_page": {"auto_start": True}})
    monkeypatch.setattr(server, "AUTOYOU_PAGE_SERVICE_AVAILABLE", True)

    async def fake_start():
        started.append(True)

    monkeypatch.setattr(server, "start_autoyou_page_service_background", fake_start)

    asyncio.run(server._start_autoyou_page_service_if_enabled())

    assert started == [True]


def test_startup_status_elapsed_freezes_after_completion(monkeypatch):
    original_status = dict(server.STATE.startup_status)
    # from __debug_provenance_k__ import donations
    original_initialized = server.STATE.initialized_services_on_startup

    try:
        time_values = iter([100.0, 112.0])
        # The module seam, not `server.time` - a finite fake on the shared
        # module is called by every thread in the process.
        monkeypatch.setattr(server, "_startup_clock", lambda: next(time_values))

        server.STATE.initialized_services_on_startup = False
        server.STATE.startup_status = {
            "status": "idle",
            "headline": "Waiting for password",
            "detail": "Enter the server password to decrypt configuration.",
            "step": 0,
            "total_steps": 8,
            "started_at": None,
            "completed_at": None,
            "updated_at": None,
            "error": "",
        }

        server._update_startup_status(
            status="starting",
            headline="Decrypting configuration",
            detail="Preparing startup.",
            step=1,
            total_steps=8,
        )
        server._update_startup_status(
            status="complete",
            headline="Initialization complete",
            detail="Services are ready.",
            step=8,
            total_steps=8,
        )

        monkeypatch.setattr(server, "_startup_clock", lambda: 180.0)
        payload = server._startup_status_payload()

        assert payload["status"] == "complete"
        assert payload["elapsed_seconds"] == 12
    finally:
        server.STATE.startup_status = original_status
        server.STATE.initialized_services_on_startup = original_initialized


def test_startup_status_elapsed_advances_while_initializing(monkeypatch):
    original_status = dict(server.STATE.startup_status)
    original_initialized = server.STATE.initialized_services_on_startup

    try:
        monkeypatch.setattr(server, "_startup_clock", lambda: 200.0)
        server.STATE.initialized_services_on_startup = False
        server.STATE.startup_status = {
            "status": "idle",
            "headline": "Waiting for password",
            "detail": "Enter the server password to decrypt configuration.",
            "step": 0,
            "total_steps": 8,
            "started_at": None,
            "completed_at": None,
            "updated_at": None,
            "error": "",
        }

        server._update_startup_status(
            status="initializing",
            headline="Starting transports",
            detail="Working.",
            step=4,
            total_steps=8,
        )

        monkeypatch.setattr(server, "_startup_clock", lambda: 219.0)
        payload = server._startup_status_payload()

        assert payload["status"] == "initializing"
        assert payload["elapsed_seconds"] == 19
    finally:
        server.STATE.startup_status = original_status
        server.STATE.initialized_services_on_startup = original_initialized


@pytest.mark.parametrize("config, locked, expected", [
    ({"security": {"mode": "SecureProfessional"}}, False, ["pairing", "transport"]),
    ({"security": {"mode": "SecureProfessional"}}, True, []),
    ({}, False, []),
])
def test_fresh_launch_starts_pairing_only_with_unlocked_config(monkeypatch, config, locked, expected):
    from types import SimpleNamespace
    from core_server import iroh_service

    events = []
    runtime = SimpleNamespace(
        STATE=SimpleNamespace(config=config),
        _config_write_block_reason=lambda: "synthetic locked config" if locked else None,
        _configure_pairing_router_helpers=lambda: events.append("pairing"),
    )

    async def start_transport(actual):
        assert actual is runtime
        events.append("transport")

    monkeypatch.setattr(services, "_runtime", lambda: runtime)
    monkeypatch.setattr(iroh_service, "start_server_transport", start_transport)
    asyncio.run(services._start_pairing_transport_if_unlocked())
    assert events == expected


@pytest.mark.parametrize("transport_failure", [False, True])
def test_deferred_optional_services_still_initialize_native_transport(monkeypatch, transport_failure):
    from types import SimpleNamespace
    from core_server import iroh_service
    import logging

    events, statuses = [], []
    state = SimpleNamespace(initialized_services_on_startup=False, startup_status={"step": 0})
    runtime = SimpleNamespace(
        os=os, STATE=state, LOGGER=logging.getLogger("autoyou.tests.deferred-startup"),
        _configure_pairing_router_helpers=lambda: events.append("pairing"),
        _update_startup_status=lambda **value: statuses.append(value),
    )
    async def start_transport(actual):
        assert actual is runtime and not state.initialized_services_on_startup
        events.append("transport")
        if transport_failure:
            raise RuntimeError("synthetic endpoint startup failure")
    monkeypatch.setenv("AUTOYOU_SKIP_STARTUP_SERVICES", "1")
    monkeypatch.setattr(services, "_runtime", lambda: runtime)
    monkeypatch.setattr(iroh_service, "start_server_transport", start_transport)
    asyncio.run(services._initialize_services_on_startup())
    assert events[-1] == "transport"
    assert state.initialized_services_on_startup is (not transport_failure)
    assert statuses[-1]["status"] == ("error" if transport_failure else "complete")
