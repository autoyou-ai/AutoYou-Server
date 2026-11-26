# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Comprehensive tests for AutoYou's upgraded Coding, Internet, Games, and Builder harness.

All tests are strictly isolated via ``tmp_path`` and ``AUTOYOU_TEST_ROOT`` and
use synthetic fixtures only.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import types

from tests.support.paths import REPO_ROOT, ensure_repo_on_path

ensure_repo_on_path()

from autoyou_agents.coding_agent import agent as coding_agent_mod
from autoyou_agents.coding_agent import harness as coding_harness
from autoyou_agents.games_agent import game_builder_tool
from autoyou_agents.internet_agent import expanded_harness, internet_tool, research_rag, search_providers
from autoyou_agents.shared_tools import coding_handoff, website_handoff, workspace_rag, workspace_tools
from shared import game_package, session_execution


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "test_root"))
    return ws


# ── 1. Workspace RAG + AST Symbol Navigation + Patch Tools ──────────────────

def test_workspace_rag_symbol_search_and_outline(isolated_workspace):
    (isolated_workspace / "engine.py").write_text(
        '"""Synthetic physics engine."""\n'
        "class OrbitalIntegrator:\n"
        "    def step_velocity(self, dt: float) -> float:\n"
        "        return dt * 9.81\n"
        "\n"
        "def compute_apogee(speed: float) -> float:\n"
        "    return speed * speed / 19.62\n",
        encoding="utf-8",
    )
    (isolated_workspace / "ui.js").write_text(
        "export function renderTelemetryHud(state) { return state.speed; }\n"
        "class HudController { update() {} }\n",
        encoding="utf-8",
    )

    rag_res = workspace_rag.rag_search_workspace("orbital velocity integrator apogee")
    assert rag_res["status"] == "success"
    assert rag_res["results"]
    assert rag_res["results"][0]["path"] == "engine.py"

    sym_res = workspace_rag.find_symbol("compute_apogee")
    assert sym_res["status"] == "success"
    assert any(m["name"] == "compute_apogee" and m["path"] == "engine.py" for m in sym_res["matches"])

    js_sym = workspace_rag.find_symbol("renderTelemetryHud")
    assert js_sym["status"] == "success"
    assert any(m["path"] == "ui.js" for m in js_sym["matches"])

    outline = workspace_rag.get_file_outline("engine.py")
    assert outline["status"] == "success"
    names = [s["name"] for s in outline["symbols"]]
    assert "OrbitalIntegrator" in names
    assert "compute_apogee" in names


def test_workspace_batch_read_multi_edit_apply_patch_and_verify(isolated_workspace):
    (isolated_workspace / "src").mkdir()
    (isolated_workspace / "src" / "math_mod.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    (isolated_workspace / "src" / "notes.txt").write_text("line1\nline2\n", encoding="utf-8")

    found = workspace_tools.find_files(pattern="*.py")
    assert found["status"] == "success"
    assert found["files"] == ["src/math_mod.py"]

    batch = workspace_tools.read_files_batch([
        {"path": "src/math_mod.py", "start_line": 1, "end_line": 5},
        "src/notes.txt",
    ])
    assert batch["status"] == "success"
    assert len(batch["results"]) == 2
    assert "def add" in batch["results"][0]["content"]

    multi = workspace_tools.multi_edit_file(
        "src/math_mod.py",
        [
            {"old_text": "return a + b", "new_text": "return int(a) + int(b)"},
            {"old_text": "return a * b", "new_text": "return int(a) * int(b)"},
        ],
    )
    assert multi["status"] == "success"
    assert multi["edits_applied"] == 2

    # Atomic rollback if one hunk fails
    failed_multi = workspace_tools.multi_edit_file(
        "src/math_mod.py",
        [
            {"old_text": "return int(a) + int(b)", "new_text": "return 0"},
            {"old_text": "THIS_DOES_NOT_EXIST", "new_text": "nope"},
        ],
    )
    assert failed_multi["status"] == "error"
    assert "return int(a) + int(b)" in (isolated_workspace / "src" / "math_mod.py").read_text(encoding="utf-8")

    # Codex *** Begin Patch format
    patch_text = """*** Begin Patch
*** Update File: src/math_mod.py
@@
 def mul(a, b):
-    return int(a) * int(b)
+    return int(a) * int(b) * 1
*** Add File: src/helper.py
+def greet(name: str) -> str:
+    return f"hello {name}"
*** End Patch"""
    patched = workspace_tools.apply_patch(patch_text)
    assert patched["status"] == "success"
    changed_paths = [c["path"] for c in patched["changes"]]
    assert "src/math_mod.py" in changed_paths
    assert "src/helper.py" in changed_paths

    # Append chunked writing
    appended = workspace_tools.append_file("src/helper.py", "\ndef bye() -> str:\n    return 'bye'\n")
    assert appended["status"] == "success"

    # Shell-free verification catches bad syntax and passes clean files
    ok_verify = workspace_tools.verify_workspace_files(["src/math_mod.py", "src/helper.py"])
    assert ok_verify["status"] == "success"
    assert ok_verify["all_passed"] is True

    (isolated_workspace / "src" / "broken.py").write_text("def oops(:\n    pass\n", encoding="utf-8")
    bad_verify = workspace_tools.verify_workspace_files(["src/broken.py"])
    assert bad_verify["all_passed"] is False
    assert any("syntax" in issue.lower() for issue in bad_verify["reports"][0]["errors"])


def test_compiled_workspace_root_uses_writable_mutable_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOYOU_WORKSPACE_ROOT", raising=False)
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_PACKAGED_RUNTIME", "1")
    root = workspace_tools._workspace_root()
    assert str(tmp_path) in str(root)
    assert root.name == "workspace"
    assert root.is_dir()


# ── 2. Coding Agent Harness: Plan, Compaction, Anti-Thrash, Budgets ─────────

def test_coding_defaults_prevent_premature_exhaustion(monkeypatch):
    monkeypatch.delenv("AUTOYOU_CODING_AGENT_MAX_TOOL_CALLS", raising=False)
    monkeypatch.delenv("AUTOYOU_CODING_AGENT_MAX_TURN_SECONDS", raising=False)
    assert session_execution.resolve_coding_tool_call_budget() == 0
    assert session_execution.resolve_coding_turn_budget_seconds() == 0
    assert session_execution.resolve_coding_breaker_cooldown_seconds() <= 120.0


@pytest.mark.asyncio
async def test_coding_harness_plan_compaction_duplicate_guard_and_lightweight_tools():
    ctx = SimpleNamespace(state={}, invocation_id="coding-inv-1")

    # 1. Durable plan creation and step completion
    init = coding_harness.manage_coding_plan(
        action="init",
        goal="Implement feature X",
        steps_json=json.dumps(["Inspect code", "Edit module", "Verify"]),
        tool_context=ctx,
    )
    assert init["status"] == "success"
    assert len(init["steps"]) == 3
    assert init["steps"][0]["status"] == "in_progress"

    done = coding_harness.manage_coding_plan(action="complete_step", steps_json="1", tool_context=ctx)
    assert done["steps"][0]["status"] == "done"
    assert done["steps"][1]["status"] == "in_progress"

    # 2. Lightweight tools do not consume heavy tool_calls budget
    for _ in range(10):
        res = await coding_agent_mod._coding_before_tool_callback(
            SimpleNamespace(name="get_file_outline"), {"path": f"file_{_}.py"}, ctx
        )
        assert res is None
    assert ctx.state[session_execution.SESSION_CONTROL_STATE_KEY]["tool_calls"] == 0

    # 3. Duplicate consecutive calls return constructive nudge without opening breaker
    first = await coding_agent_mod._coding_before_tool_callback(
        SimpleNamespace(name="read_file"), {"path": "a.py", "start_line": 1}, ctx
    )
    dup = await coding_agent_mod._coding_before_tool_callback(
        SimpleNamespace(name="read_file"), {"path": "a.py", "start_line": 1}, ctx
    )
    assert first is None
    assert dup is not None and dup["status"] == "skipped_duplicate"
    assert ctx.state[session_execution.SESSION_CONTROL_STATE_KEY]["status"] == "running"
    assert ctx.state[session_execution.SESSION_CONTROL_STATE_KEY]["tool_calls"] == 1

    # 4. Context compaction shrinks old bulky tool responses while preserving recent ones
    bulky_payload = {"status": "success", "path": "big.py", "content": "x = 1\n" * 600}
    contents = [types.Content(role="user", parts=[types.Part(text="Build feature X")])]
    for i in range(12):
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_function_response(name="read_file", response=dict(bulky_payload))],
            )
        )
    llm_req = SimpleNamespace(
        contents=contents,
        config=SimpleNamespace(system_instruction="Base prompt", max_output_tokens=1024),
    )
    await coding_agent_mod._coding_before_model_callback(ctx, llm_req)
    assert llm_req.config.max_output_tokens >= 4096
    assert "[CODING HARNESS STATE]" in llm_req.config.system_instruction
    # Older responses compacted, latest response kept intact
    early_resp = llm_req.contents[1].parts[0].function_response.response
    late_resp = llm_req.contents[-1].parts[0].function_response.response
    assert early_resp.get("_compacted") is True
    assert late_resp.get("_compacted") is None


def test_ollama_runtime_options_promotes_num_predict_for_higher_max_tokens():
    from autoyou_agents.agent import _with_ollama_runtime_options

    updated = _with_ollama_runtime_options(
        "ollama_chat/qwen3.5:4b",
        {"num_ctx": 32768, "num_predict": 1024, "max_tokens": 4096},
    )
    assert updated["num_predict"] == 4096

    clamped = _with_ollama_runtime_options(
        "ollama_chat/qwen3.5:4b",
        {"num_ctx": 4096, "num_predict": 1024, "max_tokens": 8192},
    )
    assert clamped["num_predict"] == 2048


# ── 3. Internet Agent: Research RAG, Multi-Provider Search, Fast HTTP ───────

def test_research_rag_extracts_and_queries_full_long_document():
    research_rag.reset_research_rag_store()
    filler = "Introductory background paragraph about web protocols. " * 140  # > 7,000 chars
    deep_detail = "Quantum error correction threshold reached 0.0014 with surface codes in synthetic benchmark."
    html = (
        "<html><head><title>Deep Research Article</title>"
        '<link rel="alternate" type="application/rss+xml" title="Feed" href="/rss.xml">'
        "</head><body><main>"
        "<h1><a href='/story-1'>Major Quantum Benchmark Update</a></h1>"
        f"<p>{filler}</p><p>{deep_detail}</p>"
        "</main></body></html>"
    )
    parsed = research_rag.extract_structured_page(html, "https://science.example/article")
    assert parsed["title"] == "Deep Research Article"
    assert parsed["feeds"][0]["url"] == "https://science.example/rss.xml"
    assert parsed["headlines"][0]["title"] == "Major Quantum Benchmark Update"

    store = research_rag.get_research_rag_store()
    info = store.index_document(parsed["url"], parsed["title"], parsed["text"], headlines=parsed["headlines"])
    assert info["indexed"] is True
    assert info["chunks"] >= 2

    # Query a fact sitting past the 5,000-character truncation boundary!
    hits = store.query("surface codes 0.0014 threshold", max_chunks=3)
    assert hits
    assert "0.0014" in hits[0]["excerpt"]
    assert hits[0]["url"] == "https://science.example/article"


@pytest.mark.asyncio
async def test_internet_research_web_and_expanded_harness_budget(monkeypatch):
    research_rag.reset_research_rag_store()
    tool = internet_tool.InternetTool()

    rss_xml = (
        "<rss><channel>"
        "<item><title>Python 3.13 Free-Threaded Mode Guide</title>"
        "<link>https://docs.example/python-313</link>"
        "<description>Overview of Python 3.13 JIT and free-threaded builds.</description>"
        "<pubDate>Thu, 26 Nov 2026 10:00:00 GMT</pubDate></item>"
        "</channel></rss>"
    )
    article_html = (
        "<html><head><title>Python 3.13 Guide</title></head><body><main>"
        "<h1>Python 3.13 Free-Threaded Mode</h1>"
        "<p>Python 3.13 introduces an experimental free-threaded build without the GIL and an experimental JIT compiler. "
        + ("Detailed explanation of CPython 3.13 interpreter optimizations. " * 12)
        + "</p></main></body></html>"
    )

    def _fake_get(url, **kwargs):
        resp = SimpleNamespace(status_code=200, headers={"Content-Type": "text/html"}, raise_for_status=lambda: None)
        if "news.google.com" in url:
            resp.headers = {"Content-Type": "application/rss+xml"}
            resp.text = rss_xml
            resp.content = rss_xml.encode()
        elif "docs.example/python-313" in url:
            resp.text = article_html
            resp.content = article_html.encode()
        else:
            resp.text = "{}"
            resp.content = b"{}"
            resp.json = lambda: {}
        return resp

    monkeypatch.setattr(tool.session, "get", _fake_get)
    monkeypatch.setattr(internet_tool, "_internet_ssrf_guard_disabled", lambda: True)

    res = await tool.research_web("Python 3.13 free-threaded features", max_results=5, fetch_top=2)
    assert res["status"] == "success"
    assert res["pages_fetched"] == 1
    assert res["top_excerpts"]
    assert "free-threaded" in res["top_excerpts"][0]["excerpt"].lower()

    # Expanded harness: raised budget & single duplicate does NOT kill session
    assert expanded_harness._DEFAULT_TOOL_CALL_BUDGET == 0
    ctx = SimpleNamespace(state={}, invocation_id="inet-guard-1")
    first = await expanded_harness.before_tool_callback(
        SimpleNamespace(name="research_web"), {"query": "python 3.13"}, ctx
    )
    dup = await expanded_harness.before_tool_callback(
        SimpleNamespace(name="research_web"), {"query": "python 3.13"}, ctx
    )
    assert first is None
    assert dup["status"] == "skipped_duplicate"
    assert ctx.state.get(expanded_harness._TERMINAL_STATE_KEY, False) is False
    # Next distinct call is still allowed!
    second = await expanded_harness.before_tool_callback(
        SimpleNamespace(name="fetch_webpage"), {"url": "https://docs.example/python-313"}, ctx
    )
    assert second is None
    # Recording research_web populates both search and page evidence
    expanded_harness.after_tool_callback(
        SimpleNamespace(name="research_web"), {"query": "python 3.13"}, ctx, res
    )
    kinds = {item["kind"] for item in ctx.state[expanded_harness._EVIDENCE_STATE_KEY]}
    assert {"search", "page"}.issubset(kinds)


# ── 4. Games Builder + Cross-AgentTool Handoffs ─────────────────────────────

def test_games_agent_scaffold_validate_package_publish(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))

    scaffolded = game_builder_tool.scaffold_offline_game(
        game_id="synthetic-astro-dash",
        title="Astro Dash",
        genre="arcade",
        description="Collect stars and dodge asteroids offline.",
        include_database=True,
    )
    assert scaffolded["status"] == "success"
    game_dir = Path(scaffolded["game_dir"])
    assert (game_dir / "game.json").is_file()
    assert (game_dir / "index.html").is_file()
    assert (game_dir / "game.js").is_file()

    validated = game_builder_tool.validate_offline_game("synthetic-astro-dash")
    assert validated["status"] == "success"
    assert validated["valid"] is True

    built = game_builder_tool.build_offline_game_package("synthetic-astro-dash", publish_to_library=True)
    assert built["status"] == "success"
    assert built["published"] is True
    pkg = Path(built["package_path"])
    assert pkg.is_file()
    assert Path(built["published_path"]).is_file()
    manifest, _ = game_package.read_manifest(pkg)
    assert manifest["id"] == "synthetic-astro-dash"
    assert game_package.package_supports_quit(pkg)

    # Validation catches external CDN violations
    html_path = game_dir / "index.html"
    html_path.write_text(
        html_path.read_text(encoding="utf-8").replace(
            '<script src="./game.js"></script>',
            '<script src="https://cdn.example/bad.js"></script><script src="./game.js"></script>',
        ),
        encoding="utf-8",
    )
    bad = game_builder_tool.validate_offline_game("synthetic-astro-dash")
    assert bad["valid"] is False
    assert any("cdn" in i.lower() or "external" in i.lower() for i in bad["issues"])


def test_cross_agent_tool_handoff_uses_root_dispatch_in_isolated_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    game_builder_tool.scaffold_offline_game("synthetic-handoff-game", "Handoff Game")

    # Simulate isolated AgentTool child runner where parent_agent is None and find_agent only sees self
    isolated_agent = SimpleNamespace(
        parent_agent=None,
        find_agent=lambda name: None if name != "autoyou_games_agent" else True,
    )
    ctx = SimpleNamespace(
        state={},
        actions=SimpleNamespace(transfer_to_agent=None, skip_summarization=None),
        _invocation_context=SimpleNamespace(agent=isolated_agent),
    )

    prep = game_builder_tool.prepare_game_coding_handoff(
        "synthetic-handoff-game",
        "Add power-ups and boss wave.",
        tool_context=ctx,
    )
    assert prep["status"] == "success"

    handoff = game_builder_tool.handoff_to_coding_agent(tool_context=ctx)
    assert handoff["status"] == "success"
    assert handoff["handoff_mode"] == "root_dispatch"
    assert ctx.actions.transfer_to_agent is None
    assert ctx.state[coding_handoff.PENDING_AGENT_HANDOFF_TARGET_STATE_KEY] == "autoyou_coding_agent"
    assert ctx.state[coding_handoff.PERSISTENT_CODING_HANDOFF_STATE_KEY]["game_id"] == "synthetic-handoff-game"

    # Coding agent consumes persistent handoff cleanly
    consumed = coding_agent_mod.get_pending_builder_handoff(ctx)
    assert consumed["handoff_present"] is True
    assert consumed["handoff"]["game_id"] == "synthetic-handoff-game"
    assert ctx.state[coding_handoff.PERSISTENT_CODING_HANDOFF_STATE_KEY] is None


# ── 5. v2 macOS & Windows Packaging Coverage ────────────────────────────────

def test_new_harness_modules_are_included_in_v2_packaged_runtime_plan():
    scripts_dir = str(REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import build_packaged_runtime_modules as build_mod

    compiled = {path.as_posix() for path in build_mod._iter_python_module_sources(REPO_ROOT)}
    expected_modules = (
        "autoyou_agents/shared_tools/workspace_rag.py",
        "autoyou_agents/shared_tools/agent_execution.py",
        "autoyou_agents/coding_agent/harness.py",
        "autoyou_agents/internet_agent/research_rag.py",
        "autoyou_agents/internet_agent/search_providers.py",
        "autoyou_agents/games_agent/game_builder_tool.py",
    )
    for mod_path in expected_modules:
        assert mod_path in compiled, f"{mod_path} missing from packaged runtime module plan!"
    assert not [p for p in compiled if p.rsplit("/", 1)[-1].startswith("test_")]


# ── 6. Live-Run Regression Guards ───────────────────────────────────────────

def test_conversation_context_detects_retrieved_and_cited_followups():
    from autoyou_agents.shared_tools.conversation_context import (
        ConversationTurn,
        classify_follow_up,
    )

    history = [
        ConversationTurn(
            role="user",
            text="Research the latest SQLite FTS5 BM25 documentation and give 2 source URLs.",
            timestamp=1000.0,
        ),
        ConversationTurn(
            role="assistant",
            text="Here are the SQLite FTS5 docs: https://www.sqlite.org/fts5.html",
            timestamp=1008.0,
        ),
    ]
    signal = classify_follow_up(
        "From what you just retrieved, name one source URL you cited.",
        history,
        now=1015.0,
    )
    assert signal.is_follow_up is True
    assert "explicit_reference" in signal.reasons


def test_game_projects_root_stays_outside_games_agent_source_tree(tmp_path, monkeypatch):
    from autoyou_agents.games_agent import game_builder_tool
    from autoyou_agents.shared_tools import workspace_rag

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("AUTOYOU_DATA_DIR", raising=False)
    projects_root = game_builder_tool._game_projects_root()
    games_agent_dir = Path(game_builder_tool.__file__).resolve().parent
    assert not projects_root.is_relative_to(games_agent_dir)
    assert projects_root.name == "game_projects"

    rag_db = workspace_rag._index_db_path(REPO_ROOT)
    agents_dir = REPO_ROOT / "autoyou_agents"
    assert not rag_db.is_relative_to(agents_dir)
    assert rag_db.parent.name == "coding_workspace_rag"


def test_ollama_adapter_does_not_misclassify_handoff_function_tools_as_agent_tools():
    from autoyou_agents.litellm_ollama_adapter import _extract_explicit_agent_tool_names

    tools = [
        {
            "type": "function",
            "function": {
                "name": "handoff_to_coding_agent",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "autoyou_coding_agent",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        },
    ]
    extracted = _extract_explicit_agent_tool_names(tools)
    assert "autoyou_coding_agent" in extracted
    assert "handoff_to_coding_agent" not in extracted
