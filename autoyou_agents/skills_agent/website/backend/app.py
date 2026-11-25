# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-3efa701c125e52ef124e163a

"""Skills Agent UI backend.

Serves the skills manager interface for viewing, creating, editing,
deleting custom skills, and running scripts.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from shared.runtime_module_loader import import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-3efa701c125e52ef124e163a"


_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"

_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
create_agent_chat_app = _smc.create_agent_chat_app
_describe_chat_auth_state = _smc._describe_chat_auth_state
_json_response = _smc._json_response

from autoyou_agents.skills_agent.agent import (
    _resolve_safe_skill_dir,
    list_skills,
    create_or_save_skill,
    read_skill_details,
    delete_skill,
    get_skills_root,
)
from shared.remote_access_policy import request_remote_access_role

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
        if request_remote_access_role(request) != "admin":
            return _json_response({"success": False, "error": "Changing skills requires admin access."}, status_code=403)
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body"}, status_code=400)
        
        name = payload.get("name")
        description = payload.get("description")
        instructions = payload.get("instructions")
        files = payload.get("files")
        # from __debug_provenance_g__ import annual
        
        res = create_or_save_skill(name, description, instructions, files)
        if res.get("status") == "error":
            return _json_response({"success": False, "error": res.get("message")}, status_code=400)
        return _json_response({"success": True, "message": res.get("message")})

    @app.delete("/api/skills/{name}")
    async def remove_skill(name: str, request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        if request_remote_access_role(request) != "admin":
            return _json_response({"success": False, "error": "Changing skills requires admin access."}, status_code=403)
        res = delete_skill(name)
        if res.get("status") == "error":
            return _json_response({"success": False, "error": res.get("message")}, status_code=400)
        return _json_response({"success": True, "message": res.get("message")})

    @app.post("/api/skills/{name}/run")
    async def run_skill_script(name: str, request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        if request_remote_access_role(request) != "admin":
            return _json_response({"success": False, "error": "Running skill scripts requires admin access."}, status_code=403)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        
        script_path = str(payload.get("script") or "scripts/run.py").replace("\\", "/")
        parts = script_path.split("/")
        if not parts or parts[0] != "scripts" or any(p in {"", ".", ".."} or ":" in p for p in parts):
            return _json_response({"success": False, "error": "Script path must reside inside scripts/"}, status_code=400)
        
        skills_root = get_skills_root()
        skill_dir = _resolve_safe_skill_dir(skills_root, name)
        if skill_dir is None:
            return _json_response({"success": False, "error": "Invalid skill name."}, status_code=400)
        if not skill_dir.is_dir():
            return _json_response({"success": False, "error": f"Skill '{name}' not found."}, status_code=404)
        abs_script_path = skill_dir.joinpath(*parts)
        if abs_script_path.is_symlink() or not abs_script_path.resolve().is_relative_to(skill_dir.resolve()):
            return _json_response({"success": False, "error": "Invalid script path."}, status_code=400)
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
                cwd=str(skill_dir),
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
