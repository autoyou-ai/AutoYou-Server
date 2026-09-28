# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-0ee9d7d3aaf4853b37314a78

"""Skills Agent implementation module."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-0ee9d7d3aaf4853b37314a78"


import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import google.adk.skills as adk_skills
from google.adk.skills import models
from google.adk.tools.skill_toolset import SkillToolset
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

logger = logging.getLogger(__name__)


# ─── Path Resolver ──────────────────────────────────────────────────────────

def get_skills_root() -> Path:
    """Resolve the skills storage folder.
    
    - Compiled Mode: stored in the user data directory under 'skills/'.
    - Dev Mode: stored in the repository root under 'skills/'.
    """
    try:
        from shared.platform_runtime import get_user_data_dir, is_compiled
        if is_compiled():
            skills_dir = get_user_data_dir("AutoYou") / "skills"
        else:
            from autoyou_agents.shared_tools.agent_install_registry import _get_repo_root
            skills_dir = _get_repo_root() / "skills"
    except Exception:
        skills_dir = Path.cwd() / "skills"
    
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir.resolve()


# ─── Dynamic Skill Toolset ───────────────────────────────────────────────────

class DynamicSkillToolset(SkillToolset):
    """Subclass of ADK SkillToolset that dynamically loads skills from disk at runtime."""

    def __init__(self, skills_dir: Path, **kwargs: Any):
        self._skills_dir = skills_dir
        super().__init__(skills=[], **kwargs)

    def _list_skills(self) -> List[models.Skill]:
        skills = []
        if self._skills_dir.is_dir():
            skills_map = adk_skills.list_skills_in_dir(self._skills_dir)
            for skill_id in skills_map:
                try:
                    skills.append(adk_skills.load_skill_from_dir(self._skills_dir / skill_id))
                except Exception as e:
                    logger.warning("Failed to load skill %s: %s", skill_id, e)
        # Refresh internal mapping
        self._skills = {skill.name: skill for skill in skills}
        return skills

    def _get_skill(self, skill_name: str) -> Optional[models.Skill]:
        try:
            return adk_skills.load_skill_from_dir(self._skills_dir / skill_name)
        except Exception:
            return None


# ─── Agent Custom CRUD Tools ─────────────────────────────────────────────────

def list_skills() -> Dict[str, Any]:
    """List all available skill names and descriptions currently saved on disk.
    
    Returns:
        Dict[str, Any]: A dict containing status, skills, and total count.
    """
    skills_dir = get_skills_root()
    skills_map = adk_skills.list_skills_in_dir(skills_dir)
    result = []
    for skill_id, fm in skills_map.items():
        result.append({
            "name": fm.name,
            "description": fm.description,
            "license": fm.license,
            "compatibility": fm.compatibility,
            "allowed_tools": fm.allowed_tools,
            "metadata": fm.metadata
        })
    return {"status": "success", "skills": result, "count": len(result)}


def create_or_save_skill(
    name: str,
    description: str,
    instructions: str,
    files: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Create a new skill or update an existing skill's files, instructions, and frontmatter.
    
    Args:
        name: The name of the skill (lowercase kebab-case or snake_case matching the directory name).
        description: A description of what the skill does (under 1024 characters).
        instructions: Detailed markdown instructions for the body of SKILL.md.
        files: Optional dict mapping relative file paths (e.g. 'scripts/run.py') to their string content.
    """
    import re
    # Validate name according to Frontmatter validators (lowercase kebab-case or snake-case)
    name = str(name).strip().lower()
    if not re.match(r"^([a-z0-9]+(-[a-z0-9]+)*|[a-z0-9]+(_[a-z0-9]+)*)$", name):
        return {
            "status": "error",
            "message": "Skill name must be lowercase kebab-case or snake_case with no spaces/special characters."
        }
    if len(name) > 64:
        return {"status": "error", "message": "Skill name must be under 64 characters."}
    
    description = str(description).strip()
    if not description:
        return {"status": "error", "message": "Description is required."}
    if len(description) > 1024:
        return {"status": "error", "message": "Description must be under 1024 characters."}

    # Validate all file paths first
    validated_files = []
    if files:
        for rel_path, content in files.items():
            # Clean path to prevent path traversal
            rel_path = str(rel_path).replace("\\", "/").strip("/")
            parts = [p for p in rel_path.split("/") if p not in ("", ".", "..")]
            if not parts:
                continue
            
            # Enforce that path starts with scripts, references, or assets
            if parts[0] not in ("scripts", "references", "assets"):
                return {
                    "status": "error",
                    "message": f"File path '{rel_path}' must reside in 'scripts/', 'references/', or 'assets/'."
                }
            validated_files.append((parts, content))

    skills_dir = get_skills_root()
    skill_path = skills_dir / name
    skill_path.mkdir(parents=True, exist_ok=True)
    
    # Write SKILL.md with standardized YAML frontmatter format
    skill_md_content = f"""---
name: {name}
description: "{description}"
---
{instructions.strip()}
"""
    (skill_path / "SKILL.md").write_text(skill_md_content, encoding="utf-8")
    
    # Write additional files
    for parts, content in validated_files:
        dest_file = skill_path.joinpath(*parts)
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_text(content, encoding="utf-8")
            
    return {"status": "success", "message": f"Skill '{name}' saved successfully."}


def read_skill_details(name: str) -> Dict[str, Any]:
    """Read a specific skill's detailed file structure and file contents from disk.
    
    Args:
        name: The exact name of the skill directory.
    """
    skills_dir = get_skills_root()
    skill_path = skills_dir / name
    if not skill_path.is_dir():
        return {"status": "error", "message": f"Skill '{name}' not found."}
        
    try:
        skill = adk_skills.load_skill_from_dir(skill_path)
    except Exception as e:
        return {"status": "error", "message": f"Failed to parse skill: {e}"}
        
    # Build files dictionary recursively
    files = {}
    for root, _, filenames in os.walk(skill_path):
        for fn in filenames:
            file_absolute = Path(root) / fn
            rel_path = file_absolute.relative_to(skill_path)
            rel_path_str = str(rel_path).replace("\\", "/")
            try:
                files[rel_path_str] = file_absolute.read_text(encoding="utf-8")
            except Exception:
                # Skip binary files or other read errors
                continue
                
    return {
        "status": "success",
        "name": skill.name,
        "description": skill.description,
        "instructions": skill.instructions,
        "frontmatter": skill.frontmatter.model_dump(),
        "files": files
    }


def delete_skill(name: str) -> Dict[str, Any]:
    """Delete a skill folder and all its files from disk.
    
    Args:
        name: The exact name of the skill directory to delete.
    """
    skills_dir = get_skills_root()
    skill_path = skills_dir / name
    if not skill_path.is_dir():
        return {"status": "error", "message": f"Skill '{name}' does not exist."}
        
    try:
        shutil.rmtree(skill_path)
        return {"status": "success", "message": f"Skill '{name}' deleted successfully."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to delete skill: {e}"}


# ─── Factory and Callbacks ───────────────────────────────────────────────────

def get_tools() -> List[Any]:
    """Get list of custom tools for managing skills."""
    return [
        get_current_datetime,
        list_skills,
        create_or_save_skill,
        read_skill_details,
        delete_skill,
    ]


async def _skills_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    """Inject host clock into model turns."""
    from autoyou_agents.shared_tools.datetime_tool import inject_realtime_datetime_into_request
    inject_realtime_datetime_into_request(llm_request)
    return None


def create_skills_agent(model_config: Any) -> Any:
    """Factory function to build the Skills Agent."""
    from google.adk.agents import Agent
    from autoyou_agents.skills_agent.prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION

    skills_dir = get_skills_root()
    skill_toolset = DynamicSkillToolset(skills_dir)

    # Combine DynamicSkillToolset and custom CRUD tools
    all_agent_tools = get_tools() + [skill_toolset]

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=all_agent_tools,
        before_model_callback=_skills_before_model_callback,
    )
