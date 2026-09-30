# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-desktop-assets-20260929

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import asyncio
import io
import json
import logging
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image

from routers.admin_ui import register_routes

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-desktop-assets-20260929"


def _test_client() -> TestClient:
    admin_app = FastAPI()
    auth_app = FastAPI()

    def require_login(request: Request):
        if request.headers.get("x-test-admin") != "authorized":
            return JSONResponse(status_code=401, content={"detail": "admin login required"})
        return None

    server = SimpleNamespace(
        asyncio=asyncio,
        _require_api_login=require_login,
        _json_response_no_store=lambda payload: JSONResponse(content=payload, headers={"Cache-Control": "no-store"}),
        LOGGER=logging.getLogger("desktop_asset_route_test"),
    )
    register_routes(admin_app, auth_app, server)
    return TestClient(admin_app)


def _desktop_asset_bundle() -> bytes:
    sprite = io.BytesIO()
    Image.new("RGB", (10, 8), color=(40, 60, 80)).save(sprite, format="PNG")
    manifest = {
        "schema_version": 2,
        "agent_name": "codex_desktop_agent",
        "app_id": "codex_desktop",
        "asset_packs": [
            {
                "asset_pack_id": "route-upload-synthetic",
                "platform": "windows",
                "targets": [
                    {
                        "target_id": "composer_box",
                        "click_point": [0.5, 0.8],
                        "expected_image_path": "sprites/composer.png",
                    }
                ],
            }
        ],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("sprites/composer.png", sprite.getvalue())
    return output.getvalue()


def test_desktop_asset_catalog_and_prompt_require_admin_login() -> None:
    with _test_client() as client:
        anonymous = client.get("/api/admin/desktop-assets")
        assert anonymous.status_code == 401

        headers = {"x-test-admin": "authorized"}
        catalog = client.get("/api/admin/desktop-assets", headers=headers)
        assert catalog.status_code == 200
        assert {item["agent_name"] for item in catalog.json()["agents"]} == {
            "claude_desktop_agent",
            "codex_desktop_agent",
        }
        assert catalog.headers["cache-control"] == "no-store"

        prompt = client.get(
            "/api/admin/desktop-assets/codex_desktop_agent/setup-prompt",
            params={"platform": "windows", "app_version": "26.803.41515", "theme": "dark", "display_scale": "1.5"},
            headers=headers,
        )
        assert prompt.status_code == 200
        assert "26.803.41515" in prompt.json()["prompt"]
        assert "\"$schema\"" in prompt.json()["prompt"]

        outside_catalog = client.get(
            "/api/admin/desktop-assets/untrusted_agent/setup-prompt",
            headers=headers,
        )
        assert outside_catalog.status_code == 404


def test_desktop_asset_preferences_are_admin_only_and_test_root_scoped(monkeypatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(runtime_root))
    monkeypatch.delenv("AUTOYOU_RUNTIME_ROOT", raising=False)

    with _test_client() as client:
        response = client.post(
            "/api/admin/desktop-assets/codex_desktop_agent/preferences",
            json={"theme": "custom:studio", "display_scale": 1.5},
            headers={"x-test-admin": "authorized"},
        )
        assert response.status_code == 200
        assert response.json()["preferences"] == {"theme": "custom:studio", "display_scale": 1.5}
        assert (runtime_root / "AutoYou" / "autoyou_agents" / "codex_desktop_agent" / "desktop_assets" / "preferences.json").is_file()

        invalid = client.post(
            "/api/admin/desktop-assets/codex_desktop_agent/preferences",
            json={"theme": "dark", "display_scale": 5},
            headers={"x-test-admin": "authorized"},
        )
        assert invalid.status_code == 400

        anonymous = client.post(
            "/api/admin/desktop-assets/codex_desktop_agent/preferences",
            json={"theme": "dark", "display_scale": 1.0},
        )
        assert anonymous.status_code == 401


def test_desktop_asset_zip_upload_is_authenticated_and_test_root_scoped(monkeypatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(runtime_root))
    monkeypatch.delenv("AUTOYOU_RUNTIME_ROOT", raising=False)

    with _test_client() as client:
        anonymous = client.post(
            "/api/admin/desktop-assets/codex_desktop_agent/import",
            files={"bundle": ("pack.zip", _desktop_asset_bundle(), "application/zip")},
        )
        assert anonymous.status_code == 401

        response = client.post(
            "/api/admin/desktop-assets/codex_desktop_agent/import",
            files={"bundle": ("pack.zip", _desktop_asset_bundle(), "application/zip")},
            headers={"x-test-admin": "authorized"},
        )

    assert response.status_code == 200
    assert response.json()["installed_pack_ids"] == ["route-upload-synthetic"]
    stored_root = runtime_root / "AutoYou" / "autoyou_agents" / "codex_desktop_agent" / "desktop_assets" / "user_packs"
    assert stored_root.is_relative_to(runtime_root)
    assert len(list(stored_root.glob("*/sprites/*.png"))) == 1
