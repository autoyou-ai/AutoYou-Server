# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-1c0f8954d1da33dc3d4389e9

"""Skills Agent UI backend.

Serves the skills manager interface for viewing, creating, editing,
deleting custom skills, and running scripts.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-1c0f8954d1da33dc3d4389e9"


import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from shared.runtime_module_loader import import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module

_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"

_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
create_agent_chat_app = _smc.create_agent_chat_app
_describe_chat_auth_state = _smc._describe_chat_auth_state
_json_response = _smc._json_response

from autoyou_agents.skills_agent.agent import (
    list_skills,
    create_or_save_skill,
    read_skill_details,
    delete_skill,
    get_skills_root,
)

logger = logging.getLogger(__name__)

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_AGENT_NAME = "skills_agent"


def _extra_routes(app, agent_name: str) -> None:

    @app.get("/api/skills")
    async def get_all_skills(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        res = list_skills()
        return _json_response({"success": True, **res})

    @app.get("/api/skills/{name}")
    async def get_skill(name: str, request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        res = read_skill_details(name)
        if res.get("status") == "error":
            return _json_response({"success": False, "error": res.get("message")}, status_code=404)
        return _json_response({"success": True, **res})

    @app.post("/api/skills")
    async def save_skill(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body"}, status_code=400)
        
        name = payload.get("name")
        description = payload.get("description")
        instructions = payload.get("instructions")
        files = payload.get("files")
        
        res = create_or_save_skill(name, description, instructions, files)
        if res.get("status") == "error":
            return _json_response({"success": False, "error": res.get("message")}, status_code=400)
        return _json_response({"success": True, "message": res.get("message")})

    @app.delete("/api/skills/{name}")
    async def remove_skill(name: str, request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        res = delete_skill(name)
        if res.get("status") == "error":
            return _json_response({"success": False, "error": res.get("message")}, status_code=400)
        return _json_response({"success": True, "message": res.get("message")})

    @app.post("/api/skills/{name}/run")
    async def run_skill_script(name: str, request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        
        script_path = str(payload.get("script") or "scripts/run.py").replace("\\", "/").strip("/")
        parts = [p for p in script_path.split("/") if p not in ("", ".", "..")]
        if not parts or parts[0] != "scripts":
            return _json_response({"success": False, "error": "Script path must reside inside scripts/"}, status_code=400)
        
        skills_root = get_skills_root()
        abs_script_path = skills_root.joinpath(name, *parts)
        if not abs_script_path.is_file():
            return _json_response({"success": False, "error": f"Script file '{script_path}' not found in skill folder."}, status_code=404)
        
        args = []
        if abs_script_path.suffix == ".py":
            args = [sys.executable, str(abs_script_path)]
        elif abs_script_path.suffix in (".sh", ".bash"):
            if os.name == "nt":
                args = ["bash", str(abs_script_path)]
            else:
                args = ["/bin/sh", str(abs_script_path)]
        else:
            args = [str(abs_script_path)]
            
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(skills_root / name),
            )
            return _json_response({
                "success": True,
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr
            })
        except subprocess.TimeoutExpired:
            return _json_response({"success": False, "error": "Execution timed out after 30 seconds."}, status_code=504)
        except Exception as exc:
            return _json_response({"success": False, "error": f"Failed to execute script: {exc}"}, status_code=500)


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Skills Manager",
    description="Manage custom AutoYou skills, instructions, files, and runnable scripts.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
