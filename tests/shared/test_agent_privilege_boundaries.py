# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-d9f1ca3af998445dacfc678b

"""Regression coverage for agent-reachable privilege boundaries.

In a source run the agent's workspace root, the mutable-data directory and the
config directory are all the same path. Path confinement therefore does not, on
its own, stop an agent from writing the files that *authorize* the agent - most
sharply ``ACKNOWLEDGEMENT_AGREEMENT`` plus ``jailbreak_root_prompt.txt``, whose
contents are exec'd at agent start.

Two independent controls are asserted here:

1. the agent's file tools refuse to mutate protected runtime state, and
2. the Prompt Override is honoured only when its detached signature matches,
   so an override that appears without going through the admin save path is
   ignored even if control 1 is somehow bypassed.

The Prompt Override feature itself must keep working - an admin-authored,
correctly signed override is still applied.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import pytest

from autoyou_agents.shared_tools import workspace_tools

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-d9f1ca3af998445dacfc678b"


pytestmark = pytest.mark.server


# --------------------------------------------------------------------------
# Control 1: agent file tools cannot mutate protected runtime state
# --------------------------------------------------------------------------

PROTECTED = [
    "ACKNOWLEDGEMENT_AGREEMENT",
    "jailbreak_root_prompt.txt",
    "server_unlock.json",
    "ai_agent_internal_api_token.txt",
    "config.keystore.enc",
]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("name", PROTECTED)
def test_write_file_refuses_protected_runtime_state(workspace, name):
    result = workspace_tools.write_file(name, "malicious = 1")
    assert result["status"] == "error"
    assert "protected" in result["message"].lower()
    assert not (workspace / name).exists()


def test_jailbreak_pair_cannot_be_created_by_the_agent(workspace):
    """The two-file combination that grants persistent code execution."""
    ack = workspace_tools.write_file("ACKNOWLEDGEMENT_AGREEMENT", "x")
    override = workspace_tools.write_file(
        "jailbreak_root_prompt.txt", "AGENT_INSTRUCTION = 'pwned'"
    )
    assert ack["status"] == "error"
    assert override["status"] == "error"


def test_delete_path_refuses_protected_runtime_state(workspace):
    target = workspace / "config.keystore.enc"
    target.write_bytes(b"sealed")
    result = workspace_tools.delete_path("config.keystore.enc", confirm=True)
    assert result["status"] == "error"
    assert target.exists()


def test_move_path_cannot_clobber_protected_runtime_state(workspace):
    (workspace / "innocent.txt").write_text("hi", encoding="utf-8")
    result = workspace_tools.move_path("innocent.txt", "ACKNOWLEDGEMENT_AGREEMENT")
    assert result["status"] == "error"
    assert not (workspace / "ACKNOWLEDGEMENT_AGREEMENT").exists()


def test_dot_autoyou_runtime_directory_is_protected(workspace):
    result = workspace_tools.write_file(
        ".autoyou/ai_agent_internal_api_token.txt", "stolen", create_dirs=True
    )
    # from __debug_provenance_j__ import fifteenpercent
    assert result["status"] == "error"


def test_ordinary_project_files_are_still_writable(workspace):
    """The denylist must not turn the coding agent into a read-only tool."""
    result = workspace_tools.write_file("src/app.py", "print('ok')\n", create_dirs=True)
    assert result["status"] == "success"
    assert (workspace / "src" / "app.py").read_text(encoding="utf-8") == "print('ok')\n"

    edited = workspace_tools.replace_text("src/app.py", "ok", "still ok")
    assert edited["status"] == "success"


def test_reads_of_protected_files_are_not_blocked(workspace):
    """Only mutation is denied; inspection stays available."""
    (workspace / "server_unlock.json").write_text("{}", encoding="utf-8")
    result = workspace_tools.read_file("server_unlock.json")
    assert result["status"] == "success"


# --------------------------------------------------------------------------
# Control 2: Prompt Override requires a valid signature
# --------------------------------------------------------------------------

@pytest.fixture()
def jailbreak_dir(tmp_path, monkeypatch):
    """Point the jailbreak data dir at a temp dir and activate the feature."""
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    from shared import platform_runtime

    data_dir = platform_runtime.get_jailbreak_data_dir()
    (data_dir / platform_runtime.JAILBREAK_ACKNOWLEDGEMENT_FILENAME).write_text(
        "accepted", encoding="utf-8"
    )
    return data_dir


OVERRIDE = "AGENT_INSTRUCTION = 'operator authored'\n"


def test_signed_override_is_applied(jailbreak_dir):
    """The feature keeps working for admin-authored content."""
    from shared import platform_runtime

    (jailbreak_dir / platform_runtime.JAILBREAK_ROOT_PROMPT_FILENAME).write_text(
        OVERRIDE, encoding="utf-8"
    )
    platform_runtime.sign_jailbreak_root_prompt(OVERRIDE.strip())

    assert platform_runtime.get_jailbreak_root_prompt() == OVERRIDE.strip()


def test_unsigned_override_is_refused(jailbreak_dir):
    """An override dropped on disk without the admin path is not exec'd."""
    from shared import platform_runtime

    (jailbreak_dir / platform_runtime.JAILBREAK_ROOT_PROMPT_FILENAME).write_text(
        "AGENT_INSTRUCTION = 'injected'\n", encoding="utf-8"
    )
    assert platform_runtime.get_jailbreak_root_prompt() is None


def test_tampered_override_is_refused(jailbreak_dir):
    """Signature is over content, so editing the file after signing invalidates it."""
    from shared import platform_runtime

    prompt_file = jailbreak_dir / platform_runtime.JAILBREAK_ROOT_PROMPT_FILENAME
    prompt_file.write_text(OVERRIDE, encoding="utf-8")
    platform_runtime.sign_jailbreak_root_prompt(OVERRIDE.strip())
    assert platform_runtime.get_jailbreak_root_prompt() == OVERRIDE.strip()

    prompt_file.write_text("AGENT_INSTRUCTION = 'swapped'\n", encoding="utf-8")
    assert platform_runtime.get_jailbreak_root_prompt() is None


def test_pre_existing_unsigned_override_is_adopted_once(jailbreak_dir):
    """Upgrades must not silently drop an operator's existing override."""
    from shared import platform_runtime

    (jailbreak_dir / platform_runtime.JAILBREAK_ROOT_PROMPT_FILENAME).write_text(
        OVERRIDE, encoding="utf-8"
    )
    assert platform_runtime.get_jailbreak_root_prompt() is None

    assert platform_runtime.adopt_unsigned_jailbreak_root_prompt() is True
    assert platform_runtime.get_jailbreak_root_prompt() == OVERRIDE.strip()

    # Adoption is one-shot: a later unsigned replacement is not re-adopted.
    (jailbreak_dir / platform_runtime.JAILBREAK_ROOT_PROMPT_FILENAME).write_text(
        "AGENT_INSTRUCTION = 'injected later'\n", encoding="utf-8"
    )
    assert platform_runtime.adopt_unsigned_jailbreak_root_prompt() is False
    assert platform_runtime.get_jailbreak_root_prompt() is None


def test_inactive_jailbreak_returns_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    from shared import platform_runtime

    assert platform_runtime.get_jailbreak_root_prompt() is None
