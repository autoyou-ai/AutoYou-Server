"""Authenticated Notes replication over the existing paired agent website."""
import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, model_validator

from autoyou_agents.notes_agent import sync
from shared import content_access
from shared.content_library import settings
from shared.remote_access_policy import REMOTE_BROWSER_HEADER, REMOTE_BROWSER_VIA_WEBRTC


class Media(BaseModel):
    id: int | None = Field(default=None, gt=0)
    digest: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
    client_media_id: str = Field(default="", pattern=r"^[A-Za-z0-9_-]{0,64}$")
    filename: str = Field(default="", max_length=180)
    transcription: str | None = Field(default=None, max_length=262144)
    mimetype: str = Field(default="application/octet-stream", pattern=r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$", max_length=128)

    @model_validator(mode="after")
    def check_reference(self):
        if (self.id is None) != bool(self.digest and self.client_media_id and self.filename) or (self.id and self.digest):
            raise ValueError("Invalid attachment reference")
        return self


class Edit(BaseModel):
    base_revision: int = Field(ge=0)
    mutation: str = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    title: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=2 * 1024 * 1024)
    url: str = Field(default="", max_length=8192)
    created_ms: int | None = Field(default=None, ge=0, le=253402300799000)
    updated_ms: int | None = Field(default=None, ge=0, le=253402300799000)
    tags: list[Annotated[str, Field(max_length=128)]] = Field(max_length=128)
    category: str = Field(max_length=256)
    media: list[Media] = Field(max_length=1024)


async def bounded(request, limit):
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > limit:
            raise HTTPException(413, "Note exceeds the transfer limit")
        data.extend(part)
    return bytes(data)


def router(get_tool):
    def paired(request: Request):
        # All supported gateways strip client-supplied identity. Only the loopback
        # WebRTC forwarder stamps a paired session, role and agent together.
        if (not request.client or request.client.host not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
                or request.headers.get(REMOTE_BROWSER_HEADER) != REMOTE_BROWSER_VIA_WEBRTC
                or request.headers.get("x-autoyou-agent-frontend") != "notes_agent"
                or not request.headers.get("x-autoyou-webrtc-session-id")
                or request.headers.get(content_access.ROLE_HEADER) not in {"viewer", "editor", "admin"}):
            raise HTTPException(403, "Sync requires a paired computer connection")
        if not settings()["notes_sync_enabled"]:
            raise HTTPException(403, "Notes sync is disabled in My Content on this computer")

    api = APIRouter(prefix="/api/sync", dependencies=[Depends(paired)])

    async def run(request, action, fn, *args):
        if action != "read":
            denied = content_access.deny(request, action)
            if denied is not None:
                return denied
        def invoke():
            tool = get_tool()
            if tool is None:
                raise HTTPException(409, "Install and unlock Notes to sync")
            return fn(tool, *args)
        try:
            result = await asyncio.to_thread(invoke)
        except sync.Conflict as error:
            raise HTTPException(409, str(error)) from error
        except LookupError as error:
            raise HTTPException(404, "Note not found") from error
        except ValueError as error:
            raise HTTPException(400, "Invalid note or attachment") from error
        return JSONResponse(result or {"success": True}, headers={"Cache-Control": "no-store"})

    @api.get("/changes")
    async def changes(request: Request, after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200)):
        return await run(request, "read", sync.changes, after, limit)

    def key_of(key):
        if not sync.KEY.fullmatch(key):
            raise HTTPException(400, "Invalid note key")
        return key

    @api.get("/notes/{key}")
    async def note(key: str, request: Request):
        return await run(request, "read", sync.record, key_of(key))

    @api.put("/notes/{key}/media/{digest}")
    async def stage(key: str, digest: str, request: Request, offset: int = Query(ge=0), total: int = Query(gt=0, le=sync.MAX_FILE), filename: str = Query(default="attachment", max_length=180)):
        denied = content_access.deny(request, "upload")
        if denied is not None:
            return denied
        if not sync.DIGEST.fullmatch(digest):
            raise HTTPException(400, "Invalid attachment digest")
        return await run(request, "upload", sync.stage, key_of(key), digest, await bounded(request, sync.CHUNK), filename, offset, total)

    @api.post("/notes/{key}")
    async def save(key: str, request: Request):
        denied = content_access.deny(request, "edit")
        if denied is not None:
            return denied
        try:
            payload = Edit.model_validate(json.loads(await bounded(request, 12 * 1024 * 1024)))
        except (ValidationError, ValueError, UnicodeError, RecursionError) as error:
            raise HTTPException(400, "Invalid note") from error
        return await run(request, "edit", sync.save, key_of(key), payload)

    @api.delete("/notes/{key}")
    async def delete(key: str, request: Request, revision: int = Query(ge=0), pending_mutation: str = Query(default="", pattern=r"^[A-Za-z0-9._-]{0,128}$")):
        return await run(request, "delete", sync.remove, key_of(key), revision, pending_mutation)

    return api
