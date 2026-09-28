# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Tests for pi-mono-inspired workspace_tools enhancements.

Covers: read_file offset/has_more, search_workspace context_lines/file_type/file_glob,
run_command rolling deque buffer, and binary/image detection.
"""
import os
import sys
from pathlib import Path

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()


# ---------------------------------------------------------------------------
# read_file - offset + pagination
# ---------------------------------------------------------------------------

def test_read_file_returns_numbered_content_and_has_more(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    f = tmp_path / "sample.txt"
    lines = [f"line {i}" for i in range(1, 21)]
    f.write_text("\n".join(lines), encoding="utf-8")

    result = wt.read_file("sample.txt", start_line=1, end_line=10)

    assert result["status"] == "success"
    assert result["start_line"] == 1
    assert result["end_line"] == 10
    assert result["total_lines"] == 20
    assert result["has_more"] is True
    assert "next_start_line" in result
    assert result["next_start_line"] == 11
    assert "continuation_hint" in result
    assert "offset=11" in result["continuation_hint"]
    assert "line 1" in result["content"]
    assert "line 11" not in result["content"]


def test_read_file_offset_continues_from_given_line(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    f = tmp_path / "sample.txt"
    f.write_text("\n".join(f"line {i}" for i in range(1, 21)), encoding="utf-8")

    result = wt.read_file("sample.txt", end_line=10, offset=11)

    assert result["status"] == "success"
    assert result["start_line"] == 11
    assert "line 11" in result["content"]
    assert result["content"].startswith("line 11")


def test_read_file_last_page_has_more_false(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    f = tmp_path / "small.txt"
    f.write_text("a\nb\nc\n", encoding="utf-8")

    result = wt.read_file("small.txt", start_line=1, end_line=100)

    assert result["has_more"] is False
    assert "next_start_line" not in result
    assert "continuation_hint" not in result


def test_read_file_detects_binary_extension(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    f = tmp_path / "lib.so"
    f.write_bytes(b"\x7fELF")

    result = wt.read_file("lib.so")

    assert result["status"] == "error"
    assert result.get("binary") is True


def test_read_file_returns_data_uri_for_image(tmp_path, monkeypatch):
    import base64
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    png_1px = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00"
        b"\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00"
        b"\x00IEND\xaeB`\x82"
    )
    f = tmp_path / "icon.png"
    f.write_bytes(png_1px)

    result = wt.read_file("icon.png")

    assert result["status"] == "success"
    assert result.get("image") is True
    assert result["mime_type"] == "image/png"
    assert result["data_uri"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# search_workspace - context_lines + file_type + file_glob
# ---------------------------------------------------------------------------

def test_search_workspace_context_lines_includes_surrounding_lines(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    f = tmp_path / "code.py"
    f.write_text("alpha\nbeta\nTARGET\ndelta\nepsilon\n", encoding="utf-8")

    result = wt.search_workspace("TARGET", path=str(tmp_path), context_lines=1)

    assert result["status"] == "success"
    assert len(result["results"]) == 1
    entry = result["results"][0]
    assert entry["before"] == ["beta"]
    assert entry["after"] == ["delta"]


def test_search_workspace_file_type_py_filters_non_python(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "match.py").write_text("NEEDLE\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("NEEDLE\n", encoding="utf-8")

    result = wt.search_workspace("NEEDLE", path=str(tmp_path), file_type="py")

    assert result["status"] == "success"
    paths = [r["path"] for r in result["results"]]
    assert any("match.py" in p for p in paths)
    assert all(".txt" not in p for p in paths)


def test_search_workspace_file_glob_restricts_to_pattern(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "app.js").write_text("NEEDLE\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("NEEDLE\n", encoding="utf-8")

    result = wt.search_workspace("NEEDLE", path=str(tmp_path), file_glob="*.js")

    assert result["status"] == "success"
    paths = [r["path"] for r in result["results"]]
    assert any("app.js" in p for p in paths)
    assert all(".md" not in p for p in paths)


def test_search_workspace_no_context_lines_plain_result(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "f.txt").write_text("hello world\n", encoding="utf-8")

    result = wt.search_workspace("hello", path=str(tmp_path))

    assert result["status"] == "success"
    entry = result["results"][0]
    assert "before" not in entry
    assert "after" not in entry


# ---------------------------------------------------------------------------
# run_command - rolling deque + timed_out flag
# ---------------------------------------------------------------------------

def test_run_command_captures_stdout(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_ENABLE_AGENT_RUN_COMMAND", "1")

    cmd = "echo hello_from_test"
    result = wt.run_command(cmd, cwd=str(tmp_path))

    assert result["status"] == "success"
    assert "hello_from_test" in result["stdout"]
    assert result["exit_code"] == 0


def test_run_command_nonzero_exit_is_error_status(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_ENABLE_AGENT_RUN_COMMAND", "1")

    result = wt.run_command("exit 1", cwd=str(tmp_path))

    assert result["status"] == "error"
    assert result["exit_code"] == 1
    assert result.get("timed_out") is not True


def test_run_command_requires_explicit_opt_in(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("AUTOYOU_ENABLE_AGENT_RUN_COMMAND", raising=False)

    result = wt.run_command("echo should_not_run", cwd=str(tmp_path))

    assert result["status"] == "error"
    assert "disabled by default" in result["message"]
    assert "AUTOYOU_ENABLE_AGENT_RUN_COMMAND" in result["message"]


def test_run_command_blocks_destructive_patterns(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))

    result = wt.run_command("sudo rm -rf /")

    assert result["status"] == "error"
    assert "blocked" in result["message"].lower() or "safety" in result["message"].lower()


def test_run_command_blocks_subshell_and_wrapper_evasion(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.workspace_tools as wt

    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))

    # Test $(...)
    result_subshell = wt.run_command("echo $(curl http://example.com)")
    assert result_subshell["status"] == "error"
    assert "blocked" in result_subshell["message"].lower() or "safety" in result_subshell["message"].lower()

    # Test backticks
    result_backticks = wt.run_command("echo `curl http://example.com``")
    assert result_backticks["status"] == "error"
    assert "blocked" in result_backticks["message"].lower() or "safety" in result_backticks["message"].lower()

    # Test python -c
    result_python = wt.run_command("python -c 'print(1)'")
    assert result_python["status"] == "error"
    assert "blocked" in result_python["message"].lower() or "safety" in result_python["message"].lower()

    # Test bash -c
    result_bash = wt.run_command("bash -c 'echo 1'")
    assert result_bash["status"] == "error"
    assert "blocked" in result_bash["message"].lower() or "safety" in result_bash["message"].lower()

