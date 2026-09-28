# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-7deeae93bd4aaecb51f18f2f

"""Regression tests for the JAILBREAK root-prompt override round-trip.

These pin two classes of bug:

1. Editing the root agent instructions, saving, and restarting silently had no
   effect in a source run: the override file was written by ``server.py`` but the
   agent runtime only consulted it in compiled mode, and reader/writer resolved
   the JAILBREAK directory from different anchors.

2. Raw vs. per-section editing clobbered each other and per-section edits did not
   round-trip under JAILBREAK. The override now stores the FULL prompt module so
   every section and the raw instruction stay in sync, with backward-compat for
   legacy raw-instruction-blob override files.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-7deeae93bd4aaecb51f18f2f"


import importlib

import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import server
from shared import platform_runtime


_SAMPLE_PROMPT_PY = '''AGENT_NAME = "autoyou"
AGENT_DESCRIPTION = "desc"
INTRODUCTION = """base intro"""
SPECIAL_POLICIES = """base special policies"""
AGENT_INSTRUCTION = """base instruction"""
DEFAULT_INSTRUCTION = """base instruction"""
'''


@pytest.fixture()
def jailbreak_root(tmp_path, monkeypatch):
    """Point the per-user data dir at a temp dir and return the AutoYou root."""
    monkeypatch.setenv(platform_runtime.TEST_RUNTIME_ROOT_ENV, str(tmp_path))
    root = tmp_path / "AutoYou"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _activate_jailbreak(root):
    (root / platform_runtime.JAILBREAK_ACKNOWLEDGEMENT_FILENAME).write_text("ack", encoding="utf-8")


def _override_path(root):
    return root / platform_runtime.JAILBREAK_ROOT_PROMPT_FILENAME


def _write_override(root, content: str) -> None:
    """Put an override in place the way the product actually gets one.

    An override is exec'd by the agent, so its presence on disk is not
    authorisation: `get_jailbreak_root_prompt` refuses one whose detached HMAC is
    missing or stale, which is what anything other than the admin save path
    produces. Writing the file alone - as these tests used to - is exactly the
    case that is meant to be refused.

    Adoption is the path a pre-existing unsigned override takes: trusted server
    startup signs what is already on disk once, so operator overrides written
    before signing existed keep working. `server.py` calls it at line ~392.
    """
    _override_path(root).write_text(content, encoding="utf-8")
    platform_runtime.adopt_unsigned_jailbreak_root_prompt(anchor=str(root))


def _instruction_of(source: str) -> str:
    return server._extract_string_assignment_value(source, "AGENT_INSTRUCTION")


# ── write/read routing ────────────────────────────────────────────────────────

def test_dev_jailbreak_write_stores_full_module_override(jailbreak_root, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    _activate_jailbreak(jailbreak_root)

    prompt_py = tmp_path / "prompt.py"
    prompt_py.write_text(_SAMPLE_PROMPT_PY, encoding="utf-8")
    monkeypatch.setattr(server, "_agent_prompt_file_path", lambda: str(prompt_py))

    new_content = _SAMPLE_PROMPT_PY.replace("base instruction", "JAILBROKEN dev")
    server._write_agent_prompt_file(new_content)

    saved = _override_path(jailbreak_root).read_text(encoding="utf-8")
    # The override is a FULL module (so sections round-trip), not a bare blob.
    assert server._override_defines_full_prompt_module(saved)
    assert _instruction_of(saved) == "JAILBROKEN dev"
    # The real prompt.py is left untouched so deactivating JAILBREAK restores base.
    assert prompt_py.read_text(encoding="utf-8") == _SAMPLE_PROMPT_PY


def test_dev_inactive_jailbreak_writes_prompt_py(jailbreak_root, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    prompt_py = tmp_path / "prompt.py"
    prompt_py.write_text(_SAMPLE_PROMPT_PY, encoding="utf-8")
    monkeypatch.setattr(server, "_agent_prompt_file_path", lambda: str(prompt_py))

    server._write_agent_prompt_file(_SAMPLE_PROMPT_PY.replace("base instruction", "edited base"))

    assert "edited base" in prompt_py.read_text(encoding="utf-8")
    assert not _override_path(jailbreak_root).exists()


def test_compiled_inactive_jailbreak_is_read_only(jailbreak_root, monkeypatch):
    monkeypatch.setattr(server, "is_compiled", lambda: True)
    with pytest.raises(PermissionError):
        server._write_agent_prompt_file(_SAMPLE_PROMPT_PY)


def test_compiled_active_jailbreak_writes_override(jailbreak_root, monkeypatch):
    monkeypatch.setattr(server, "is_compiled", lambda: True)
    _activate_jailbreak(jailbreak_root)

    server._write_agent_prompt_file(_SAMPLE_PROMPT_PY.replace("base instruction", "JAILBROKEN compiled"))

    saved = _override_path(jailbreak_root).read_text(encoding="utf-8")
    assert _instruction_of(saved) == "JAILBROKEN compiled"


# ── round-trip: raw + section + legacy blob ───────────────────────────────────

def test_section_edit_under_jailbreak_roundtrips(jailbreak_root, tmp_path, monkeypatch):
    """A per-section edit persists and is visible again on the next read."""
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    _activate_jailbreak(jailbreak_root)
    prompt_py = tmp_path / "prompt.py"
    prompt_py.write_text(_SAMPLE_PROMPT_PY, encoding="utf-8")
    monkeypatch.setattr(server, "_agent_prompt_file_path", lambda: str(prompt_py))

    # Simulate the section editor: read effective content, edit one section, save.
    content = server._read_agent_prompt_file()
    edited = server._replace_string_assignment_value(content, "SPECIAL_POLICIES", "JB special")
    server._write_agent_prompt_file(edited)

    # Re-read: the section change is preserved (full-module round-trip).
    reread = server._read_agent_prompt_file()
    assert server._extract_string_assignment_value(reread, "SPECIAL_POLICIES") == "JB special"


def test_raw_then_section_edit_do_not_clobber(jailbreak_root, tmp_path, monkeypatch):
    """A raw AGENT_INSTRUCTION edit and a later section edit both survive."""
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    _activate_jailbreak(jailbreak_root)
    prompt_py = tmp_path / "prompt.py"
    prompt_py.write_text(_SAMPLE_PROMPT_PY, encoding="utf-8")
    monkeypatch.setattr(server, "_agent_prompt_file_path", lambda: str(prompt_py))

    # Raw edit of AGENT_INSTRUCTION.
    c1 = server._read_agent_prompt_file()
    server._write_agent_prompt_file(
        server._replace_string_assignment_value(c1, "AGENT_INSTRUCTION", "RAW custom")
    )
    # Section edit of INTRODUCTION afterwards.
    c2 = server._read_agent_prompt_file()
    server._write_agent_prompt_file(
        server._replace_string_assignment_value(c2, "INTRODUCTION", "INTRO custom")
    )

    final = server._read_agent_prompt_file()
    assert server._extract_string_assignment_value(final, "AGENT_INSTRUCTION") == "RAW custom"
    assert server._extract_string_assignment_value(final, "INTRODUCTION") == "INTRO custom"


def test_legacy_blob_override_still_applies(jailbreak_root, tmp_path, monkeypatch):
    """Older override files holding a raw instruction blob keep working."""
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    _activate_jailbreak(jailbreak_root)
    _write_override(jailbreak_root, "legacy blob instruction")
    prompt_py = tmp_path / "prompt.py"
    prompt_py.write_text(_SAMPLE_PROMPT_PY, encoding="utf-8")
    monkeypatch.setattr(server, "_agent_prompt_file_path", lambda: str(prompt_py))

    content = server._read_agent_prompt_file()
    assert _instruction_of(content) == "legacy blob instruction"
    # Base sections remain intact under a legacy blob overlay.
    assert server._extract_string_assignment_value(content, "INTRODUCTION") == "base intro"


# ── anchor + runtime application ──────────────────────────────────────────────

def test_reader_and_writer_resolve_same_jailbreak_dir_in_source_run(monkeypatch):
    monkeypatch.delenv(platform_runtime.TEST_RUNTIME_ROOT_ENV, raising=False)
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)

    agent_mod = importlib.import_module("autoyou_agents.agent")

    writer_dir = platform_runtime.get_jailbreak_data_dir(anchor=server.__file__).resolve()
    reader_dir = platform_runtime.get_jailbreak_data_dir(
        anchor=agent_mod.jailbreak_prompt_anchor()
    ).resolve()
    assert writer_dir == reader_dir


def test_agent_runtime_applies_full_module_override(jailbreak_root, monkeypatch):
    """Runtime overlays every section the override defines, not just the blob."""
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    _activate_jailbreak(jailbreak_root)
    full = _SAMPLE_PROMPT_PY.replace("base intro", "JB intro").replace(
        "base instruction", "JB runtime instruction"
    )
    _write_override(jailbreak_root, full)

    agent_mod = importlib.import_module("autoyou_agents.agent")
    agent_mod._reload_prompt_from_disk()
    try:
        assert agent_mod.root_prompt.AGENT_INSTRUCTION == "JB runtime instruction"
        assert agent_mod.root_prompt.INTRODUCTION == "JB intro"
    finally:
        (jailbreak_root / platform_runtime.JAILBREAK_ACKNOWLEDGEMENT_FILENAME).unlink(missing_ok=True)
        _override_path(jailbreak_root).unlink(missing_ok=True)
        agent_mod._reload_prompt_from_disk()


def test_agent_runtime_applies_legacy_blob_override(jailbreak_root, monkeypatch):
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    _activate_jailbreak(jailbreak_root)
    _write_override(jailbreak_root, "RUNTIME JAILBROKEN blob")

    agent_mod = importlib.import_module("autoyou_agents.agent")
    agent_mod._reload_prompt_from_disk()
    try:
        assert agent_mod.root_prompt.AGENT_INSTRUCTION == "RUNTIME JAILBROKEN blob"
    finally:
        (jailbreak_root / platform_runtime.JAILBREAK_ACKNOWLEDGEMENT_FILENAME).unlink(missing_ok=True)
        _override_path(jailbreak_root).unlink(missing_ok=True)
        agent_mod._reload_prompt_from_disk()
