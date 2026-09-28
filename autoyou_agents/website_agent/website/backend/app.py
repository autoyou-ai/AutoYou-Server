# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-a00534050dd4c73a38471d48

"""Website Builder chat UI backend.

Serves the website builder chat interface with live preview and publish controls.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-a00534050dd4c73a38471d48"


from shared.runtime_module_loader import import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module

_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"
_FR_MOD = "autoyou_agents.shared_tools.frontend_registry"

_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
_fr = _import_autoyou_shared_tools_module(_FR_MOD, anchor=__file__)

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Request

create_agent_chat_app = _smc.create_agent_chat_app
_describe_chat_auth_state = _smc._describe_chat_auth_state
_json_response = _smc._json_response
load_frontend_registry = _fr.load_frontend_registry

from autoyou_agents.agent_builder_agent.agent import list_existing_agents, get_scaffold_status

logger = logging.getLogger(__name__)

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_AGENT_NAME = "website_agent"


def _admin_api_url(path: str) -> str:
    host = os.environ.get("ADMIN_WEB_SERVICE_HOST", "localhost")
    port = int(os.environ.get("ADMIN_WEB_SERVICE_PORT", "8001"))
    return f"http://{host}:{port}{path}"


def _internal_token() -> str:
    return str(os.environ.get("AUTOYOU_AI_INTERNAL_API_TOKEN", "") or "").strip()


def _get_proxy_port(agent_name: str) -> Optional[int]:
    registry = load_frontend_registry()
    for entry in registry.get("frontends", []):
        if entry.get("agent_name") == agent_name:
            pp = entry.get("proxy_port")
            if pp:
                return int(pp)
    return None


def _extra_routes(app, agent_name: str) -> None:

    @app.get("/api/agents/list")
    async def website_list_agents(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        result = list_existing_agents()
        registry = load_frontend_registry()
        proxy_ports: Dict[str, int] = {}
        for entry in registry.get("frontends", []):
            an = entry.get("agent_name", "")
            pp = entry.get("proxy_port")
            if an and pp:
                proxy_ports[an] = int(pp)
        agents_with_websites = []
        for a in (result.get("agents") or []):
            status = get_scaffold_status(a)
            website_dir = Path(str(status.get("agent_dir", ""))) / "website" if status.get("agent_dir") else None
            has_website = bool(website_dir and website_dir.is_dir()) if website_dir else False
            port = proxy_ports.get(a)
            agents_with_websites.append({
                "name": a,
                "has_website": has_website,
                "proxy_port": port,
                "preview_path": f"/agent/{a}/" if port else None,
                "published": bool(port),
            })
        return _json_response({"success": True, "agents": agents_with_websites})

    @app.get("/api/preview/{target_agent}")
    async def website_preview(target_agent: str, request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        port = _get_proxy_port(target_agent)
        if port:
            return _json_response({
                "success": True,
                "available": True,
                "path": f"/agent/{target_agent}/",
                "port": port,
            })
        return _json_response({"success": True, "available": False, "path": None, "port": None})

    @app.post("/api/publish/{target_agent}")
    async def website_publish(target_agent: str, request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        import aiohttp as _aiohttp
        token = _internal_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with _aiohttp.ClientSession() as session:
                async with session.post(
                    _admin_api_url("/api/agents/frontend"),
                    json={"agent_name": target_agent, "enabled": True},
                    headers=headers,
                    timeout=_aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json(content_type=None)
                    return _json_response(data, status_code=resp.status)
        except Exception as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=502)

    @app.post("/api/unpublish/{target_agent}")
    async def website_unpublish(target_agent: str, request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        import aiohttp as _aiohttp
        token = _internal_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with _aiohttp.ClientSession() as session:
                async with session.post(
                    _admin_api_url("/api/agents/frontend"),
                    json={"agent_name": target_agent, "enabled": False},
                    headers=headers,
                    timeout=_aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json(content_type=None)
                    return _json_response(data, status_code=resp.status)
        except Exception as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=502)


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Website Builder",
    description="Chat with the Website Builder to design, scaffold, and publish agent frontend UIs.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
