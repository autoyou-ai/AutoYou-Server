"""Workspace search and continuation contracts, using synthetic files only."""
import shutil

import pytest

from autoyou_agents.shared_tools import workspace_tools as wt


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def test_default_continuation_keeps_a_full_page(workspace):
    (workspace / "large.py").write_text("\n".join(f"value_{i} = {i}" for i in range(700)), encoding="utf-8")
    first = wt.read_file("large.py")
    second = wt.read_file("large.py", offset=first["next_start_line"])
    assert second["start_line"] == 251
    assert second["end_line"] == 500
    assert second["next_start_line"] == 501
    assert len(second["content"].splitlines()) == 250


@pytest.mark.parametrize("case_sensitive", [False, True])
def test_fast_search_matches_fallback_with_context_and_filters(workspace, monkeypatch, case_sensitive):
    if not shutil.which("rg"):
        pytest.skip("ripgrep is optional")
    (workspace / "src").mkdir()
    (workspace / "src" / "sample.py").write_text("before\n-dash+[item]\nafter\n", encoding="utf-8")
    (workspace / "other.txt").write_text("-dash+[item]", encoding="utf-8")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "ignored.py").write_text("-dash+[item]", encoding="utf-8")
    # Confirm the native path actually runs; do not merely compare two fallbacks.
    candidates = wt._literal_search_candidates(workspace, "-dash+[item]", case_sensitive)
    assert candidates is not None
    fast = wt.search_workspace("-dash+[item]", file_type="py", context_lines=1, case_sensitive=case_sensitive)
    monkeypatch.setattr(wt.shutil, "which", lambda name: None)
    portable = wt.search_workspace("-dash+[item]", file_type="py", context_lines=1, case_sensitive=case_sensitive)
    assert fast == portable
    assert len(fast["results"]) == 1
    assert fast["results"][0]["after"] == ["after"]


def test_missing_native_search_still_searches_one_file(workspace, monkeypatch):
    (workspace / "sample.py").write_text("alpha needle omega", encoding="utf-8")
    monkeypatch.setattr(wt.shutil, "which", lambda name: "/missing/synthetic-rg")
    result = wt.search_workspace("needle", path="sample.py")
    assert result["results"][0]["line"] == 1


def test_regex_keeps_python_lookbehind_semantics(workspace, monkeypatch):
    (workspace / "sample.py").write_text("before needle after", encoding="utf-8")
    monkeypatch.setattr(wt, "_literal_search_candidates", lambda *args: pytest.fail("regex must retain Python semantics"))
    assert len(wt.search_workspace(r"(?<=before )needle", use_regex=True)["results"]) == 1


def test_ascii_case_insensitive_shortlist_keeps_python_unicode_matches(workspace, monkeypatch):
    (workspace / "sample.txt").write_text("\u0130\n\u0131\n\u017f\n\u212a", encoding="utf-8")
    actual = [wt.search_workspace(pattern) for pattern in ["i", "s", "k"]]
    monkeypatch.setattr(wt.shutil, "which", lambda name: None)
    expected = [wt.search_workspace(pattern) for pattern in ["i", "s", "k"]]
    assert actual == expected
    assert len(actual[0]["results"]) == 2
