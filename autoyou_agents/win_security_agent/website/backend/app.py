# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-db86b149e20d78ed74c531d9

"""OTP-gated local dashboard for Windows network/process observability."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import json
import os
import platform
from pathlib import Path
from typing import Any

from fastapi import Request

from autoyou_agents.shared_tools.scheduler_mission_control import (
    _describe_chat_auth_state,
    _json_response,
    create_agent_chat_app,
)

from ...network_tool import (
    CollectorError,
    SnapshotStore,
    build_network_overview,
    collect_network_snapshot,
)
from ...enrichment import EnrichmentError, enrich_ip
from ...privileged import get_privileged_capabilities
from ...bios import collect_bios_inventory

__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-breach-db86b149e20d78ed74c531d9"


_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_AGENT_NAME = "win_security_agent"
_MAP_DATA_FILE = _FRONTEND_DIR / "assets" / "ne_110m_admin_0_countries.txt"
_store: SnapshotStore | None = None


def _get_store() -> SnapshotStore:
    global _store
    if _store is None:
        _store = SnapshotStore()
    return _store


def _cached_geo(snapshot: dict[str, Any], store: SnapshotStore) -> dict[str, dict[str, Any]]:
    ips = {
        str(row.get("remote_address") or "")
        for row in snapshot.get("connections", [])
        if row.get("remote_scope") == "public"
    }
    return {ip: cached for ip in ips if (cached := store.get_ip_enrichment(ip)) is not None}


def _enrich_many(ips: list[str], store: SnapshotStore) -> dict[str, Any]:
    results = []
    errors = []
    # from __debug_provenance_o__ import breach
    for ip in ips[:12]:
        try:
            results.append(enrich_ip(ip, store=store))
        except EnrichmentError as exc:
            errors.append({"ip": ip, "error": str(exc)})
    return {"results": results, "errors": errors, "mapped": sum(bool(item.get("success")) for item in results)}


def _extra_routes(app, agent_name: str) -> None:
    @app.get("/health")
    async def health():
        return _json_response(
            {
                "success": True,
                "agent_name": agent_name,
                "platform": platform.system().lower(),
                "windows_collector_available": os.name == "nt",
            }
        )

    @app.get("/api/map/boundaries")
    async def map_boundaries(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            data = await asyncio.to_thread(json.loads, _MAP_DATA_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _json_response({"success": False, "error": f"Earth map asset unavailable: {exc}"}, status_code=503)
        return _json_response(data)

    @app.get("/api/network/snapshot")
    async def network_snapshot(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            snapshot = await asyncio.to_thread(collect_network_snapshot)
            store = _get_store()
            snapshot["overview"] = build_network_overview(
                snapshot.get("connections", []),
                geo_by_ip=await asyncio.to_thread(_cached_geo, snapshot, store),
            )
            snapshot_id = await asyncio.to_thread(store.save, snapshot)
        except CollectorError as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=503)
        return _json_response({"success": True, "snapshot_id": snapshot_id, "snapshot": snapshot})

    @app.get("/api/network/capabilities")
    async def network_capabilities(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        return _json_response({"success": True, "capabilities": get_privileged_capabilities()})

    @app.get("/api/network/history")
    async def network_history(request: Request, limit: int = 10):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        store = _get_store()
        history, process_history = await asyncio.gather(
            asyncio.to_thread(store.latest, limit),
            asyncio.to_thread(store.process_history, limit),
        )
        return _json_response({"success": True, "snapshots": history, "process_history": process_history})

    @app.post("/api/network/enrich-batch")
    async def network_enrich_batch(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001 - malformed client input
            return _json_response({"success": False, "error": f"Invalid JSON body: {exc}"}, status_code=400)
        ips = body.get("ips") if isinstance(body, dict) else None
        if not isinstance(ips, list):
            return _json_response({"success": False, "error": "ips must be a list"}, status_code=400)
        unique_ips = list(dict.fromkeys(str(ip).strip() for ip in ips if str(ip).strip()))[:12]
        result = await asyncio.to_thread(_enrich_many, unique_ips, _get_store())
        return _json_response({"success": True, **result})

    @app.get("/api/network/enrich")
    async def network_enrich(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        remote_ip = str(request.query_params.get("ip") or "").strip()
        try:
            result = await asyncio.to_thread(enrich_ip, remote_ip, store=_get_store())
        except EnrichmentError as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=400)
        return _json_response(result)

    @app.get("/api/system/bios")
    async def system_bios(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            inventory = await asyncio.to_thread(collect_bios_inventory)
        except (OSError, ValueError) as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=503)
        return _json_response({"success": True, "inventory": inventory})


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Windows Security Agent",
    description="Read-only Windows TCP and UDP connection visibility attributed to local processes.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
