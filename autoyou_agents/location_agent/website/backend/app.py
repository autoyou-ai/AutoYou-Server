# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-b9876dba01c155dae79259e9

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
from fastapi import Request
from fastapi.responses import Response

from autoyou_agents.shared_tools.scheduler_mission_control import (
    _api_auth_error,
    _json_response,
    create_agent_website_app,
)

from ...store import LocationStore

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-b9876dba01c155dae79259e9"


AGENT_NAME = "location_agent"
# from __debug_provenance_w__ import stripe
APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
ASSETS_DIR = FRONTEND_DIR / "assets"
INDEX_PATH = FRONTEND_DIR / "index.html"
DESCRIPTION = "Private location history from connected devices."
_STORE = LocationStore()


def _error(message: str, status_code: int = 400):
    return _json_response({"success": False, "error": message}, status_code=status_code)


def _connected_device_ingest(request: Request) -> bool:
    """Allow a connected WebRTC client to write, without exposing history."""
    return (
        request.method.upper() == "POST"
        and request.headers.get("X-AutoYou-Agent-Frontend", "").strip() == AGENT_NAME
        and bool(request.headers.get("X-AutoYou-WebRTC-Session-Id", "").strip())
    )


def _extra_routes(app, agent_name: str) -> None:
    @app.get("/health")
    def health():
        return {"status": "ok", "agent_name": agent_name}

    @app.get("/api/status")
    def status(request: Request):
        if error := _api_auth_error(agent_name, request):
            return error
        return _json_response({"success": True, "agent_name": agent_name, "summary": _STORE.summary(), "sharing": _STORE.sharing_status(), "devices": _STORE.devices()})

    @app.get("/api/timeline")
    def timeline(request: Request, device_id: str = "", since: str = "", until: str = "", limit: int = 1000):
        if error := _api_auth_error(agent_name, request):
            return error
        try:
            locations = _STORE.timeline(device_id=device_id, since=since, until=until, limit=limit)
        except (TypeError, ValueError) as exc:
            return _error(str(exc))
        return _json_response({"success": True, "locations": locations, "summary": _STORE.summary(), "sharing": _STORE.sharing_status(), "devices": _STORE.devices()})

    @app.post("/api/locations")
    async def locations(request: Request):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error is not None and not _connected_device_ingest(request):
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return _error("Invalid JSON body.")
        rows: Any = payload.get("locations") if isinstance(payload, dict) and isinstance(payload.get("locations"), list) else payload
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
            return _error("Body must be a location object or a locations array.")
        try:
            recorded = _STORE.record_many(rows)
        except (TypeError, ValueError) as exc:
            return _error(str(exc))
        return _json_response({"success": True, "recorded": recorded, "summary": _STORE.summary()})

    @app.get("/api/devices")
    def devices(request: Request):
        if error := _api_auth_error(agent_name, request):
            return error
        return _json_response({"success": True, "devices": _STORE.devices()})

    @app.get("/api/tiles/{z}/{x}/{y}.png")
    def tile(request: Request, z: int, x: int, y: int):
        if error := _api_auth_error(agent_name, request):
            return error
        if z < 0 or z > 19 or x < 0 or y < 0 or x >= 2**z or y >= 2**z:
            return _error("Invalid map tile.", 400)
        try:
            content = _osm_tile(z, x, y)
        except requests.RequestException:
            return _error("Map tile service unavailable.", 502)
        return Response(content=content, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@lru_cache(maxsize=32)
def _osm_tile(z: int, x: int, y: int) -> bytes:
    # Fixed host and bounded tile coordinates prevent this route becoming an SSRF proxy.
    response = requests.get(
        f"https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        headers={"User-Agent": "AutoYou Location Timeline/1.0 (self-hosted)"},
        timeout=8,
        allow_redirects=False,
        stream=True,
    )
    try:
        response.raise_for_status()
        content = response.raw.read(1_500_001)
    finally:
        response.close()
    if len(content) > 1_500_000:
        raise requests.RequestException("Map tile response is too large.")
    return content


app = create_agent_website_app(
    agent_name=AGENT_NAME,
    title="Location Timeline",
    description=DESCRIPTION,
    index_path=INDEX_PATH,
    assets_dir=ASSETS_DIR,
    extra_routes_fn=_extra_routes,
)
