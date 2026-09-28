# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

from __future__ import annotations

import inspect
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

from autoyou_agents.build_prompt_agent.build_prompt_tool import (
    available_application_agents,
    run_tool,
    status_prompt,
)


_AGENT_NAME = "build_prompt_agent"
_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


def _server_module() -> Any:
    import sys

    return sys.modules.get("server")


def _auth_error(request: Request) -> Any:
    try:
        auth = _describe_chat_auth_state(request, _AGENT_NAME)
    except Exception:
        return None
    if bool(auth.get("required")) and not bool(auth.get("authenticated")):
        return _json_response(
            {
                "success": False,
                "error": "Local one-time-code session required for Prompt Builder changes.",
                "auth": auth,
            },
            status_code=401,
        )
    return None


def _json_error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"success": False, "status": "error", "message": message}, status_code=status_code)


def _telegram_user_config() -> Dict[str, Any]:
    server_module = _server_module()
    config = getattr(getattr(server_module, "STATE", None), "config", None)
    telegram = config.get("telegram_user", {}) if isinstance(config, dict) else {}
    builder = telegram.get("prompt_builder", {}) if isinstance(telegram, dict) else {}
    return {
        "enabled": bool(builder.get("enabled", False)),
        "application_agent": str(builder.get("application_agent") or "codex_desktop_agent"),
        "exit_keyword": "exit confirm",
    }


def _extra_routes(app: Any, agent_name: str) -> None:
    del agent_name

    @app.get("/api/status")
    async def status(request: Request):
        auth_error = _auth_error(request)
        if auth_error:
            return auth_error
        return {
            "success": True,
            "prompt": status_prompt(_telegram_user_config()["application_agent"]),
            "application_agents": available_application_agents(),
            "telegram_user": _telegram_user_config(),
        }

    @app.post("/api/tool/{tool_name}")
    async def tool(tool_name: str, request: Request):
        auth_error = _auth_error(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            return _json_error("Request body must be a JSON object.", 400)
        payload.setdefault("application_agent", _telegram_user_config()["application_agent"])
        return run_tool(tool_name, payload)

    @app.post("/api/config")
    async def config(request: Request):
        auth_error = _auth_error(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return _json_error("Request body must be valid JSON.", 400)
        if not isinstance(payload, dict):
            return _json_error("Request body must be a JSON object.", 400)

        application_agent = str(payload.get("application_agent") or "codex_desktop_agent")
        enabled = bool(payload.get("enabled", False))
        server_module = _server_module()
        apply_update = getattr(server_module, "_apply_admin_ui_config_update", None)
        if not callable(apply_update):
            return _json_error("Server configuration is not available.", 503)
        try:
            result = apply_update(
                {
                    "telegram_user": {
                        "prompt_builder": {
                            "enabled": enabled,
                            "application_agent": application_agent,
                        }
                    }
                }
            )
            if inspect.isawaitable(result):
                result = await result
            return {"success": True, "telegram_user": _telegram_user_config(), "admin": result}
        except Exception as exc:
            return _json_error(str(exc), 409)


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Prompt Builder",
    description="Build exact prompts for a configured desktop agent.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
