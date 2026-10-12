"""Agent installs fail clearly when protected install state cannot be opened."""

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

import asyncio
import json

from fastapi.responses import JSONResponse

import server


class _Request:
    async def json(self):
        return {"agent_name": "notes_agent"}


def _raise_registry_error(**_kwargs):
    raise server.SecureStorageError("Cannot decrypt protected file agent_install_registry.json")


def test_install_agent_reports_unreadable_protected_registry(monkeypatch):
    monkeypatch.setattr(server, "_require_api_login", lambda request: None)
    monkeypatch.setattr(server, "can_install_agent_in_runtime", lambda *args, **kwargs: True)
    monkeypatch.setattr(server, "refresh_agent_install_registry", _raise_registry_error)

    response = asyncio.run(server.admin_install_agent(_Request()))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    payload = json.loads(response.body)
    assert not payload["success"]
    assert "Restore the matching Secure Professional Maximus key" in payload["error"]
    assert payload["storage_error"] == "Cannot decrypt protected file agent_install_registry.json"


def test_builder_suite_install_reports_unreadable_protected_registry(monkeypatch):
    monkeypatch.setattr(server, "_require_api_login", lambda request: None)
    monkeypatch.setattr(server, "install_builder_suite_agents", _raise_registry_error)

    response = asyncio.run(server.admin_install_builder_suite(_Request()))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    payload = json.loads(response.body)
    assert not payload["success"]
    assert payload["storage_error"] == "Cannot decrypt protected file agent_install_registry.json"
