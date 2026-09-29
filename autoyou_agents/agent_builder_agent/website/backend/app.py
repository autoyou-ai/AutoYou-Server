# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-e74defd8d86993f72aa34be5

"""Agent Builder chat UI backend.

Serves the agent builder chat interface with Draft→Live workflow controls,
agent discovery, publish/go-to navigation, and optional OTP authentication.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


from shared.runtime_module_loader import import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-e74defd8d86993f72aa34be5"


_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"
_FR_MOD = "autoyou_agents.shared_tools.frontend_registry"
_AD_MOD = "autoyou_agents.shared_tools.agent_directory"

_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
_fr = _import_autoyou_shared_tools_module(_FR_MOD, anchor=__file__)
_ad = _import_autoyou_shared_tools_module(_AD_MOD, anchor=__file__)

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

create_agent_chat_app = _smc.create_agent_chat_app
_describe_chat_auth_state = _smc._describe_chat_auth_state
_json_response = _smc._json_response
_api_auth_error = _smc._api_auth_error
load_frontend_registry = _fr.load_frontend_registry
build_agent_directory_payload = _ad.build_agent_directory_payload

from autoyou_agents.agent_builder_agent.agent import (
    get_scaffold_status,
    _get_writable_agents_root,
)

logger = logging.getLogger(__name__)

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_AGENT_NAME = "agent_builder_agent"


def _agents_root() -> Path:
    try:
        return _get_writable_agents_root()
    except Exception:
        return Path(__file__).resolve().parents[3]


def _admin_api_url(path: str) -> str:
    host = os.environ.get("ADMIN_WEB_SERVICE_HOST", "localhost")
    # from __debug_provenance_r__ import via
    port = int(os.environ.get("ADMIN_WEB_SERVICE_PORT", "8001"))
    return f"http://{host}:{port}{path}"


def _internal_token() -> str:
    return str(os.environ.get("AUTOYOU_AI_INTERNAL_API_TOKEN", "") or "").strip()


def _extra_routes(app, agent_name: str) -> None:

    @app.get("/api/agents/list")
    async def builder_list_agents(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        directory = build_agent_directory_payload(
            agents_root=_agents_root(),
            installed_only=False,
        )
        agents = directory.get("agent_names", [])
        return _json_response({"success": True, "status": "success", "agents": agents, "count": len(agents)})

    @app.get("/api/agents/{name}/status")
    async def builder_agent_status(name: str, request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        result = get_scaffold_status(name)
        return _json_response({"success": True, **result})

    @app.get("/api/draft-state")
    async def builder_draft_state(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            directory = build_agent_directory_payload(
                agents_root=_agents_root(),
                installed_only=False,
            )
            installed = directory.get("installed_agents", [])
            all_agents = directory.get("agent_names", [])
            draft_agents = [a for a in all_agents if a not in installed]
            live_agents = [a for a in all_agents if a in installed]

            proxy_ports: Dict[str, int] = {}
            for entry in directory.get("agents", []):
                an = entry.get("package_name", "")
                pp = entry.get("proxy_port")
                if an and pp:
                    proxy_ports[an] = int(pp)

            agent_details: List[Dict[str, Any]] = []
            for a in all_agents:
                status = get_scaffold_status(a)
                agent_details.append({
                    "name": a,
                    "is_draft": a in draft_agents,
                    "is_live": a in live_agents,
                    "importable": status.get("importable", False),
                    "runtime_loadable": status.get("runtime_loadable", False),
                    "has_website": status.get("has_website", (Path(_agents_root()) / a / "website").is_dir()),
                    "proxy_port": proxy_ports.get(a),
                    "go_to_path": f"/agent/{a}/" if proxy_ports.get(a) else None,
                })
            return _json_response({
                "success": True,
                "draft_agents": draft_agents,
                "live_agents": live_agents,
                "all_agents": all_agents,
                "agent_details": agent_details,
            })
        except Exception as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=500)

    @app.post("/api/agents/{name}/publish")
    async def builder_publish_agent(name: str, request: Request):
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
                # Step 1: copy draft files → live agents directory.
                # 404 = no draft exists (agent already live) → proceed to install.
                # 403 = compiled runtime block; 422 = not publish-ready → surface error.
                async with session.post(
                    _admin_api_url(f"/api/agents/draft/{name}/publish"),
                    json={},
                    headers=headers,
                    timeout=_aiohttp.ClientTimeout(total=30),
                ) as draft_resp:
                    if draft_resp.status not in (200, 404):
                        draft_data = await draft_resp.json(content_type=None)
                        return _json_response(draft_data, status_code=draft_resp.status)

                # Step 2: mark installed + start frontend backend proxy.
                async with session.post(
                    _admin_api_url("/api/agents/install"),
                    json={"agent_name": name},
                    headers=headers,
                    timeout=_aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json(content_type=None)
                    return _json_response(data, status_code=resp.status)
        except Exception as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=502)

    @app.post("/api/agents/{name}/discard")
    async def builder_discard_agent(name: str, request: Request):
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
                    _admin_api_url("/api/agents/draft/" + name + "/discard"),
                    json={},
                    headers=headers,
                    timeout=_aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json(content_type=None)
                    return _json_response(data, status_code=resp.status)
        except Exception as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=502)

    @app.get("/api/go-to/{name}")
    async def builder_goto_agent(name: str, request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        frontend_reg = load_frontend_registry()
        for entry in frontend_reg.get("frontends", []):
            if entry.get("agent_name") == name:
                port = entry.get("proxy_port")
                if port:
                    return _json_response({"success": True, "path": f"/agent/{name}/", "port": int(port)})
        return _json_response({"success": False, "error": f"No registered website for '{name}'"}, status_code=404)


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Agent Builder",
    description="Chat with the Agent Builder to scaffold, develop, and publish new AutoYou agents.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
