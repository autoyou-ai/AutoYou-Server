# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from pathlib import Path
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import JSONResponse

from shared.runtime_module_loader import (
    import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module,
)

_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"
_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
create_agent_chat_app = _smc.create_agent_chat_app
_describe_chat_auth_state = _smc._describe_chat_auth_state
_json_response = _smc._json_response

from autoyou_agents.antigravity_agent.agent import (
    get_antigravity_release_status,
    get_antigravity_status,
    get_antigravity_usage,
    list_antigravity_asset_packs,
)
from autoyou_agents.build_prompt_agent.build_prompt_tool import (
    inspect_desktop_config,
    run_tool,
    status_prompt,
)

_AGENT_NAME = "antigravity_agent"
_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


def _auth_error(request: Request) -> Any:
    try:
        auth = _describe_chat_auth_state(request, _AGENT_NAME)
    except Exception:
        return None
    if bool(auth.get("required")) and not bool(auth.get("authenticated")):
        return _json_response(
            {
                "success": False,
                "error": "Local one-time-code session required for Antigravity controls.",
                "auth": auth,
            },
            status_code=401,
        )
    return None


def _json_error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"success": False, "status": "error", "message": message}, status_code=status_code)


def _extra_routes(app: Any, agent_name: str) -> None:
    del agent_name

    @app.get("/api/status")
    async def status(request: Request):
        auth_error = _auth_error(request)
        if auth_error:
            return auth_error
        return {
            "success": True,
            "agent_status": get_antigravity_status(capture_screenshot=False),
            "release_status": get_antigravity_release_status(),
            "prompt": status_prompt(_AGENT_NAME),
        }

    @app.get("/api/desktop-config")
    @app.get("/api/config")
    async def desktop_config(request: Request):
        auth_error = _auth_error(request)
        if auth_error:
            return auth_error
        probe_ocr = str(request.query_params.get("probe_live_ocr") or "").strip().lower() in {"1", "true", "yes"}
        return inspect_desktop_config(_AGENT_NAME, probe_live_ocr=probe_ocr)

    @app.get("/api/packs")
    async def packs(request: Request):
        auth_error = _auth_error(request)
        if auth_error:
            return auth_error
        platform_tag = request.query_params.get("platform")
        return list_antigravity_asset_packs(platform_tag=platform_tag)

    @app.post("/api/usage")
    async def usage(request: Request):
        auth_error = _auth_error(request)
        if auth_error:
            return auth_error
        return get_antigravity_usage()

    @app.post("/api/tool/{tool_name}")
    async def tool(tool_name: str, request: Request):
        auth_error = _auth_error(request)
        if auth_error:
            return auth_error
        try:
            payload: Dict[str, Any] = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            return _json_error("Request body must be a JSON object.", 400)
        payload["application_agent"] = _AGENT_NAME
        return run_tool(tool_name, payload)


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Antigravity Control",
    description="Control panel for Antigravity Desktop and agy CLI.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
