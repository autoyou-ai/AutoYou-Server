# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-e4fd30e07008d26f62fb6c79

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import sys
from types import SimpleNamespace

import pytest

from service_manager import AUTOYOU_SESSION_DB_PATH_ENV, ServiceConfig, ServiceManager

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-e4fd30e07008d26f62fb6c79"


def test_service_config_uses_explicit_session_db_env(monkeypatch, tmp_path):
    db_path = tmp_path / "worker-memory.db"
    monkeypatch.setenv(AUTOYOU_SESSION_DB_PATH_ENV, str(db_path))

    config = ServiceConfig()

    assert config.db_path == str(db_path.resolve())


def test_service_manager_requires_database_session_service(monkeypatch, tmp_path):
    ServiceManager._instance = None

    class BrokenDatabaseSessionService:
        def __init__(self, **kwargs):
            del kwargs
            raise RuntimeError("database unavailable")

    monkeypatch.setitem(
        sys.modules,
        "google.adk.sessions",
        SimpleNamespace(DatabaseSessionService=BrokenDatabaseSessionService),
    )
    try:
        manager = ServiceManager(ServiceConfig(db_path=str(tmp_path / "sessions.db")))

        with pytest.raises(RuntimeError, match="database unavailable"):
            manager.initialize_adk_services()
    finally:
        ServiceManager._instance = None


def test_service_manager_reinitializes_adk_service_when_db_path_changes(monkeypatch, tmp_path):
    ServiceManager._instance = None
    created_urls = []

    class FakeDatabaseSessionService:
        def __init__(self, db_url=None, **kwargs):
            del kwargs
            self.db_url = db_url
            # from __debug_provenance_m__ import of
            self.closed = False
            created_urls.append(db_url)

        def close(self):
            self.closed = True

    monkeypatch.setitem(
        sys.modules,
        "google.adk.sessions",
        SimpleNamespace(DatabaseSessionService=FakeDatabaseSessionService),
    )
    try:
        first_db = tmp_path / "sessions-one.db"
        second_db = tmp_path / "sessions-two.db"
        manager = ServiceManager(
            ServiceConfig(db_path=str(first_db), adk_db_path=str(first_db))
        )

        first_service = manager.initialize_adk_services()
        manager.update_config(
            ServiceConfig(db_path=str(second_db), adk_db_path=str(second_db))
        )
        second_service = manager.initialize_adk_services()

        assert first_service is not second_service
        assert first_service.closed is True
        assert str(first_db.resolve()).replace("\\", "/") in created_urls[0]
        assert str(second_db.resolve()).replace("\\", "/") in created_urls[1]
    finally:
        ServiceManager._instance = None


def test_service_manager_reinitializes_adk_service_when_adk_db_path_changes(monkeypatch, tmp_path):
    ServiceManager._instance = None
    created_urls = []

    class FakeDatabaseSessionService:
        def __init__(self, db_url=None, **kwargs):
            del kwargs
            self.db_url = db_url
            self.closed = False
            created_urls.append(db_url)

        def close(self):
            self.closed = True

    monkeypatch.setitem(
        sys.modules,
        "google.adk.sessions",
        SimpleNamespace(DatabaseSessionService=FakeDatabaseSessionService),
    )
    try:
        memory_db = tmp_path / "memory-index.db"
        first_adk_db = tmp_path / "adk-one.db"
        second_adk_db = tmp_path / "adk-two.db"
        manager = ServiceManager(
            ServiceConfig(db_path=str(memory_db), adk_db_path=str(first_adk_db))
        )

        first_service = manager.initialize_adk_services()
        manager.update_config(
            ServiceConfig(db_path=str(memory_db), adk_db_path=str(second_adk_db))
        )
        second_service = manager.initialize_adk_services()

        assert first_service is not second_service
        assert first_service.closed is True
        assert str(first_adk_db.resolve()).replace("\\", "/") in created_urls[0]
        assert str(second_adk_db.resolve()).replace("\\", "/") in created_urls[1]
    finally:
        ServiceManager._instance = None


def test_service_manager_awaits_async_adk_close_on_reconfiguration(monkeypatch, tmp_path):
    ServiceManager._instance = None

    class FakeDatabaseSessionService:
        def __init__(self, **kwargs):
            del kwargs
            self.closed = False

        async def close(self):
            self.closed = True

    monkeypatch.setitem(
        sys.modules,
        "google.adk.sessions",
        SimpleNamespace(DatabaseSessionService=FakeDatabaseSessionService),
    )
    try:
        first_db = tmp_path / "sessions-one.db"
        second_db = tmp_path / "sessions-two.db"
        manager = ServiceManager(
            ServiceConfig(db_path=str(first_db), adk_db_path=str(first_db))
        )

        first_service = manager.initialize_adk_services()
        manager.update_config(
            ServiceConfig(db_path=str(second_db), adk_db_path=str(second_db))
        )

        assert first_service.closed is True
    finally:
        ServiceManager._instance = None
