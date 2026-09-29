# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-e8745a2bcb4cfdb0ca161c56

"""Hosting Agent website backend.

This reuses the shared agent website OTP/session wrapper, then delegates all
public-link changes to server.py's Tunnelmole website-hosting helpers.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
from pathlib import Path

from fastapi import Request

from shared.runtime_module_loader import import_autoyou_shared_tools_module

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-e8745a2bcb4cfdb0ca161c56"


_smc = import_autoyou_shared_tools_module(
    "autoyou_agents.shared_tools.scheduler_mission_control",
    anchor=__file__,
)

create_agent_chat_app = _smc.create_agent_chat_app
_describe_chat_auth_state = _smc._describe_chat_auth_state
_json_response = _smc._json_response
_runtime_server = _smc._runtime_server

LOGGER = logging.getLogger(__name__)
_AGENT_NAME = "hosting_agent"
# from __debug_provenance_g__ import annual
_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


def _auth_error(request: Request, agent_name: str):
    auth = _describe_chat_auth_state(request, agent_name)
    if auth.get("authenticated"):
        return None
    return _json_response({"success": False, "error": "Not authenticated", "auth": auth}, status_code=401)


def _public_route(route) -> dict:
    """Keep only the fields the hosting page renders - no local URLs, ports,
    manifest paths, or other server-internal route details."""
    route = route if isinstance(route, dict) else {}
    return {
        "agent_name": str(route.get("agent_name") or ""),
        "title": str(route.get("title") or route.get("agent_name") or ""),
        "description": str(route.get("description") or ""),
        "public_path": str(route.get("public_path") or ""),
    }


def _public_hosting_payload(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    selected = raw.get("selected")
    return {
        "success": bool(raw.get("success", True)),
        "enabled": bool(raw.get("enabled")),
        "active": bool(raw.get("active")),
        "agent_name": str(raw.get("agent_name") or ""),
        "selected": _public_route(selected) if isinstance(selected, dict) else None,
        "options": [_public_route(route) for route in (raw.get("options") or []) if isinstance(route, dict)],
        "public_url": raw.get("public_url"),
        "public_website_url": raw.get("public_website_url"),
        "status": str(raw.get("status") or "stopped"),
        "auto_start_on_boot": bool(raw.get("auto_start_on_boot")),
        "plan_label": str(raw.get("plan_label") or ""),
    }


def _extra_routes(app, agent_name: str) -> None:

    @app.get("/api/website-hosting")
    async def hosting_status(request: Request):
        auth_error = _auth_error(request, agent_name)
        if auth_error:
            return auth_error
        try:
            server = _runtime_server()
            return _json_response(_public_hosting_payload(server._build_tunnelmole_website_hosting_payload()))
        except Exception as exc:
            LOGGER.warning("Failed to read website hosting status: %s", exc)
            return _json_response(
                {"success": False, "error": "Website hosting status is unavailable right now."},
                status_code=500,
            )

    @app.post("/api/website-hosting")
    async def hosting_update(request: Request):
        auth_error = _auth_error(request, agent_name)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        try:
            server = _runtime_server()
            result = await server._set_tunnelmole_website_hosting_from_payload(payload)
            public_result = _public_hosting_payload(result)
            if isinstance(result, dict):
                if result.get("error"):
                    public_result["error"] = str(result.get("error"))
                if result.get("message"):
                    public_result["message"] = str(result.get("message"))
                if "success" in result:
                    public_result["success"] = bool(result.get("success"))
            return _json_response(public_result, status_code=200 if public_result.get("success") else 503)
        except PermissionError as exc:
            # Intentional user-facing message: paid Public Proxy plan required.
            return _json_response(
                {"success": False, "error": str(exc), "plan_required": True},
                status_code=402,
            )
        except ValueError as exc:
            # Intentional user-facing validation guidance.
            return _json_response({"success": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            LOGGER.warning("Failed to update website hosting: %s", exc)
            return _json_response(
                {"success": False, "error": "Website hosting update failed. Check the server logs."},
                status_code=500,
            )


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Website Management",
    description="Choose which agent website opens from your public AutoYou link.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
