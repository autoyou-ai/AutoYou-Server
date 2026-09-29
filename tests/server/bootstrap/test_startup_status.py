# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-0eae165d073f02f196ea298a


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import os
import sys
import asyncio

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
