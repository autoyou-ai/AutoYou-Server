# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-746f20706179203130252061-4451f43ae24a0c7030825dfa


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-746f20706179203130252061-4451f43ae24a0c7030825dfa"

import pytest
from pathlib import Path
from autoyou_agents.skills_agent import agent as skills_agent

def test_skills_agent_crud(tmp_path, monkeypatch):
    # Mock get_skills_root to return our temporary path
    monkeypatch.setattr(skills_agent, "get_skills_root", lambda: tmp_path)

    # 1. Initially, there should be no skills
    res = skills_agent.list_skills()
    assert res["status"] == "success"
    assert res["count"] == 0
    assert len(res["skills"]) == 0

    # 2. Create a new skill
    skill_name = "test-calc-skill"
    description = "A basic test skill to perform mathematical calculations."
    instructions = "# Math Guide\nRun scripts/calc.py to perform operations."
    files = {
        "scripts/calc.py": "print(2 + 2)",
        "references/info.md": "# Math Info\nThis is info."
    }

    create_res = skills_agent.create_or_save_skill(
        name=skill_name,
        description=description,
        instructions=instructions,
        files=files
    )
    assert create_res["status"] == "success"
    assert "saved successfully" in create_res["message"]

    # 3. Check invalid names
    invalid_res = skills_agent.create_or_save_skill(
        name="Invalid Name With Spaces",
        description="description",
        instructions="instructions"
    )
    assert invalid_res["status"] == "error"
    assert "lowercase kebab-case" in invalid_res["message"]

    # 4. Check invalid file paths (outside scripts/references/assets)
    invalid_file_res = skills_agent.create_or_save_skill(
        name="test-invalid-file",
        description="description",
        instructions="instructions",
        files={"outside/file.py": "print(1)"}
    )
    assert invalid_file_res["status"] == "error"
    assert "must reside in" in invalid_file_res["message"]

    # 5. List skills and verify the created skill is present
    list_res = skills_agent.list_skills()
    assert list_res["status"] == "success"
    assert list_res["count"] == 1
    assert list_res["skills"][0]["name"] == skill_name
    assert list_res["skills"][0]["description"] == description

    # 6. Read skill details
    detail_res = skills_agent.read_skill_details(skill_name)
    assert detail_res["status"] == "success"
    assert detail_res["name"] == skill_name
    assert detail_res["description"] == description
    assert "scripts/calc.py" in detail_res["files"]
    assert detail_res["files"]["scripts/calc.py"] == "print(2 + 2)"
    assert detail_res["files"]["references/info.md"] == "# Math Info\nThis is info."

    # 7. Delete skill
    delete_res = skills_agent.delete_skill(skill_name)
    assert delete_res["status"] == "success"
    assert "deleted successfully" in delete_res["message"]

    # 8. Verify it's gone
    list_res_after = skills_agent.list_skills()
    assert list_res_after["count"] == 0


def test_dynamic_skill_toolset(tmp_path, monkeypatch):
    # Mock get_skills_root to return our temporary path
    monkeypatch.setattr(skills_agent, "get_skills_root", lambda: tmp_path)

    toolset = skills_agent.DynamicSkillToolset(tmp_path)
    
    # Empty initially
    skills = toolset._list_skills()
    assert len(skills) == 0

    # Add a skill on disk
    skills_agent.create_or_save_skill(
        name="disk-skill",
        description="Loaded dynamically from disk.",
        instructions="# Disk Skill Instructions"
    )

    # Dynamic reload on next _list_skills call
    skills = toolset._list_skills()
    assert len(skills) == 1
    assert skills[0].name == "disk-skill"

    # Get skill
    skill = toolset._get_skill("disk-skill")
    assert skill is not None
    assert skill.name == "disk-skill"
    assert skill.description == "Loaded dynamically from disk."
