# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from routers import admin as routes


def application(handler):
    state = SimpleNamespace(logged_in=True)

    def login(request):
        return None if state.logged_in else JSONResponse({"error": "Sign in"}, status_code=401)

    server = SimpleNamespace(
        _require_api_login=login, ChatRequest=SimpleNamespace, process_chat_message=handler,
        _json_response_no_store=lambda body: JSONResponse(body, headers={"Cache-Control": "no-store"}),
        AI_AGENT_SERVER_PORT=18081, LOGGER=logging.getLogger("synthetic-admin-chat"),
    )
    app = FastAPI()
    routes.register_routes(app, FastAPI(), server)
    return app, state


@pytest.mark.asyncio
async def test_async_chat_acknowledges_before_response_and_stop_reaches_provider():
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def handler(request, url, **kwargs):
        assert kwargs["authenticated_actor_role"] == "admin"
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    app, _ = application(handler)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        sent = await asyncio.wait_for(client.post("/api/chat", json={
            "async": True, "message": "synthetic request", "user_id": "synthetic-user",
            "session_id": "synthetic-thread", "request_id": "synthetic-turn",
        }), 2)
        assert sent.status_code == 202 and sent.json()["request_id"] == "synthetic-turn"
        await started.wait()
        assert (await client.get("/api/chat/requests/synthetic-turn")).json()["status"] == "thinking"
        stopped = await client.post("/api/chat/requests/synthetic-turn/stop")
        assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
        assert cleaned.is_set()
        assert (await client.get("/api/chat/requests/synthetic-turn")).json()["response"] is None


@pytest.mark.asyncio
async def test_sync_chat_remains_compatible_and_job_routes_require_login():
    async def handler(request, url, **kwargs):
        return {"response": "synthetic answer", "session_id": request.session_id}

    app, state = application(handler)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        response = await client.post("/api/chat", json={"message": "synthetic"})
        assert response.status_code == 200 and response.json()["response"] == "synthetic answer"
        assert (await client.get("/api/chat/requests/missing")).status_code == 404
        assert (await client.post("/api/chat/requests/missing/stop")).status_code == 404
        assert (await client.post("/api/chat", json={"async": True, "message": "synthetic", "request_id": "invalid id"})).status_code == 400
        state.logged_in = False
        assert (await client.post("/api/chat", json={"message": "synthetic"})).status_code == 401
        assert (await client.get("/api/chat/requests/synthetic-turn")).status_code == 401
        assert (await client.post("/api/chat/requests/synthetic-turn/stop")).status_code == 401
