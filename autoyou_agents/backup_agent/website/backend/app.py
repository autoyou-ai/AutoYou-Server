# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-9f24e186f962e810bd01d3fc

"""Authenticated backup website. Each HTTP request fits the WebRTC proxy."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-9f24e186f962e810bd01d3fc"


import asyncio
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response

from autoyou_agents.shared_tools.scheduler_mission_control import (
    _api_auth_error,
    _json_response,
    create_agent_website_app,
)
from shared.backup_transfers import BackupTransfers, CHUNK_SIZE, TransferError


AGENT_NAME = "backup_agent"
WEBSITE_ROOT = Path(__file__).resolve().parents[1]
STORE = BackupTransfers()


def _error(exc: TransferError):
    payload = {"success": False, "error": str(exc)}
    if exc.offset is not None:
        payload["offset"] = exc.offset
    return _json_response(payload, status_code=exc.status)


async def _bounded_body(request: Request, maximum: int) -> bytes:
    result = bytearray()
    async for piece in request.stream():
        result.extend(piece)
        if len(result) > maximum:
            raise TransferError("Request body is too large.", 413)
    return bytes(result)


def _extra_routes(app, agent_name: str) -> None:
    @app.get("/api/files")
    async def files(request: Request):
        if denied := _api_auth_error(agent_name, request):
            return denied
        try:
            offset = int(request.query_params.get("offset", "0"))
            items, next_offset = await asyncio.to_thread(STORE.list_files_page, offset)
            return _json_response({"success": True, "items": items, "next_offset": next_offset,
                                   "chunk_size": CHUNK_SIZE})
        except ValueError:
            return _error(TransferError("Invalid backup listing page."))
        except TransferError as exc:
            return _error(exc)

    @app.post("/api/uploads")
    async def start(request: Request):
        if denied := _api_auth_error(agent_name, request):
            return denied
        try:
            import json
            payload = json.loads((await _bounded_body(request, 4096)).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Expected an upload description.")
            result = await asyncio.to_thread(
                STORE.create, payload.get("name"), payload.get("size"), payload.get("path")
            )
            return _json_response({"success": True, **result}, status_code=201)
        except (ValueError, UnicodeError) as exc:
            return _error(TransferError(str(exc)))
        except TransferError as exc:
            return _error(exc)

    @app.get("/api/uploads/{transfer_id}")
    async def status(request: Request, transfer_id: str):
        if denied := _api_auth_error(agent_name, request):
            return denied
        try:
            result = await asyncio.to_thread(STORE.status, transfer_id, request.headers.get("x-transfer-token", ""))
            return _json_response({"success": True, **result})
        except TransferError as exc:
            return _error(exc)

    @app.put("/api/uploads/{transfer_id}/chunk")
    async def chunk(request: Request, transfer_id: str):
        if denied := _api_auth_error(agent_name, request):
            return denied
        try:
            offset = int(request.headers.get("x-chunk-offset", ""))
            body = await _bounded_body(request, CHUNK_SIZE)
            result = await asyncio.to_thread(
                STORE.append, transfer_id, request.headers.get("x-transfer-token", ""),
                offset, body, request.headers.get("x-chunk-sha256", ""),
            )
            return _json_response({"success": True, **result})
        except ValueError:
            return _error(TransferError("Invalid chunk offset."))
        except TransferError as exc:
            return _error(exc)

    @app.post("/api/uploads/{transfer_id}/complete")
    async def finish(request: Request, transfer_id: str):
        if denied := _api_auth_error(agent_name, request):
            return denied
        try:
            result = await asyncio.to_thread(STORE.complete, transfer_id, request.headers.get("x-transfer-token", ""))
            return _json_response({"success": True, **result})
        except TransferError as exc:
            return _error(exc)

    @app.post("/api/uploads/{transfer_id}/cancel")
    async def cancel(request: Request, transfer_id: str):
        if denied := _api_auth_error(agent_name, request):
            return denied
        try:
            await asyncio.to_thread(STORE.cancel, transfer_id, request.headers.get("x-transfer-token", ""))
            return _json_response({"success": True})
        except TransferError as exc:
            return _error(exc)

    @app.get("/api/files/{transfer_id}/chunk")
    async def download_chunk(request: Request, transfer_id: str, offset: int = 0):
        if denied := _api_auth_error(agent_name, request):
            return denied
        try:
            block, next_offset, total = await asyncio.to_thread(STORE.read_chunk, transfer_id, offset)
            return Response(content=block, media_type="application/octet-stream", headers={
                "Cache-Control": "no-store", "X-Next-Offset": str(next_offset), "X-Total-Size": str(total),
            })
        except TransferError as exc:
            return _error(exc)


app = create_agent_website_app(
    agent_name=AGENT_NAME,
    title="Backup Agent",
    description="Resumable file backup over the secure AutoYou connection.",
    index_path=WEBSITE_ROOT / "frontend" / "index.html",
    assets_dir=WEBSITE_ROOT / "frontend" / "assets",
    extra_routes_fn=_extra_routes,
)
