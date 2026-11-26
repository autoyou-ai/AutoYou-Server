"""Failure and recovery coverage for the local coding/builder harness.

All workspace, cache and model fixtures are synthetic and isolated.
"""

import json
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoyou_agents.coding_agent import agent as coding_agent
from autoyou_agents.coding_agent import harness
from autoyou_agents.internet_agent import research_rag
from autoyou_agents.shared_tools import workspace_rag, workspace_tools as wt
from shared.session_execution import SESSION_CONTROL_STATE_KEY


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "state"))
    return root


def test_rag_incremental_edits_and_deletions_remain_searchable(workspace):
    target = workspace / "sample.py"
    target.write_text("def obsolete_orbit():\n    return 1\n", encoding="utf-8")
    assert workspace_rag.rag_search_workspace("obsolete_orbit")["results"]
    target.write_text("def updated_velocity():\n    return 234\n", encoding="utf-8")
    result = workspace_rag.rag_search_workspace("updated_velocity")
    assert result["status"] == "success", result
    assert result["results"]
    assert workspace_rag.rag_search_workspace("obsolete_orbit")["results"] == []
    target.unlink()
    assert workspace_rag.rag_search_workspace("updated_velocity")["results"] == []
    # Updating another document must not leave FTS index corruption behind.
    target.write_text("def fresh_vector():\n    return 5\n", encoding="utf-8")
    assert workspace_rag.rag_search_workspace("fresh_vector")["results"]


def test_rag_single_file_scan_keeps_sibling_index(workspace):
    for name in ("first", "second"):
        (workspace / f"{name}.py").write_text(f"def {name}_symbol():\n    pass\n", encoding="utf-8")
    assert workspace_rag.find_symbol("second_symbol")["matches"]
    assert workspace_rag.find_symbol("first_symbol", path="first.py")["matches"]
    import sqlite3
    with sqlite3.connect(workspace_rag._index_db_path(workspace)) as conn:
        assert conn.execute("SELECT path FROM files ORDER BY path").fetchall() == [("first.py",), ("second.py",)]


def test_packaged_builder_roots_support_search_and_rag(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    project = tmp_path / "game_projects"
    workspace.mkdir()
    project.mkdir()
    target = project / "game.py"
    target.write_text("def score_combo():\n    return 10\n", encoding="utf-8")
    monkeypatch.delenv("AUTOYOU_WORKSPACE_ROOT", raising=False)
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(wt, "_workspace_root", lambda: workspace)
    monkeypatch.setattr(wt, "_extra_compiled_workspace_roots", lambda: (project,))
    assert list(wt._iter_workspace_files(project)) == [target]
    assert wt.search_workspace("score_combo", path=str(project))["results"]
    result = workspace_rag.find_symbol("score_combo", path=str(project))
    assert result["status"] == "success", result
    assert Path(result["matches"][0]["path"]) == target
    assert workspace_rag.rag_search_workspace("score combo", path=str(project))["results"]


@pytest.mark.parametrize("patch", [
    "*** Begin Patch\n*** Add File: existing.txt\n+clobbered\n*** End Patch",
    "*** Begin Patch\n*** Update File: existing.txt\n@@\n-original\n+changed\n*** Add File: existing.txt\n+clobbered\n*** End Patch",
    "*** Begin Patch\n*** Add File: new.txt\n+truncated",
])
def test_patch_rejects_overwrite_duplicate_and_truncated_operations(workspace, patch):
    (workspace / "existing.txt").write_text("original\n", encoding="utf-8")
    result = wt.apply_patch(patch)
    assert result["status"] == "error", result
    assert (workspace / "existing.txt").read_text() == "original\n"
    assert not (workspace / "new.txt").exists()


def test_unified_patch_inserts_at_coordinates_and_keeps_crlf(workspace):
    target = workspace / "sample.txt"
    target.write_bytes(b"first\r\nlast\r\n")
    patch = "--- a/sample.txt\n+++ b/sample.txt\n@@ -0,0 +1 @@\n+before\n@@ -1,0 +3 @@\n+middle\n"
    result = wt.apply_patch(patch)
    assert result["status"] == "success", result
    assert target.read_bytes() == b"before\r\nfirst\r\nmiddle\r\nlast\r\n"


def test_patch_honors_no_newline_marker(workspace):
    target = workspace / "sample.txt"
    target.write_text("old\n", encoding="utf-8")
    patch = "--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n"
    assert wt.apply_patch(patch)["status"] == "success"
    assert target.read_bytes() == b"new"


def test_patch_rolls_back_an_io_failure(workspace, monkeypatch):
    for name in ("first", "second"):
        (workspace / f"{name}.txt").write_text("old\n", encoding="utf-8")
    real_replace = __import__("os").replace
    def fail_second(src, dst):
        if Path(dst) == workspace / "second.txt":
            raise PermissionError("synthetic locked file")
        return real_replace(src, dst)
    monkeypatch.setattr(wt.os, "replace", fail_second)
    patch = "*** Begin Patch\n*** Update File: first.txt\n@@\n-old\n+new\n*** Update File: second.txt\n@@\n-old\n+new\n*** End Patch"
    result = wt.apply_patch(patch)
    assert result["status"] == "error", result
    assert (workspace / "first.txt").read_text() == "old\n"
    assert (workspace / "second.txt").read_text() == "old\n"
    assert not list(workspace.glob(".*.tmp"))


def test_verify_does_not_silently_ignore_last_file(workspace):
    paths = [f"file{i}.py" for i in range(21)]
    for path in paths:
        (workspace / path).write_text("value = 1\n", encoding="utf-8")
    (workspace / paths[-1]).write_text("def invalid(:\n", encoding="utf-8")
    result = wt.verify_workspace_files(paths)
    assert result["all_passed"] is False, result
    assert result["checked_count"] == 21


def test_verify_empty_paths_and_nonobject_game_manifest_fail(workspace):
    assert wt.verify_workspace_files([""])["status"] == "error"
    (workspace / "game.json").write_text("[]", encoding="utf-8")
    assert wt.verify_workspace_files(["game.json"])["all_passed"] is False


def test_research_store_clear_removes_fts_terms():
    store = research_rag.WebResearchRAGStore()
    store.index_document("https://example.com/old", "old", "obsolete_orbit telemetry")
    store.clear()
    store.index_document("https://example.com/new", "new", "new_velocity telemetry")
    assert store.query("obsolete_orbit") == []
    assert store.query("new_velocity")
    if store._fts_enabled:
        store._conn.execute("INSERT INTO web_chunks_fts(web_chunks_fts) VALUES('integrity-check')")


@pytest.mark.asyncio
async def test_coding_failed_tool_can_retry_and_new_turn_resets_guard():
    ctx = SimpleNamespace(state={}, invocation_id="synthetic-turn-1")
    tool = SimpleNamespace(name="read_file")
    args = {"path": "synthetic.py"}
    assert await coding_agent._coding_before_tool_callback(tool, args, ctx) is None
    await coding_agent._coding_after_tool_callback(tool, args, ctx, {"status": "error", "message": "Temporary read failure"})
    assert await coding_agent._coding_before_tool_callback(tool, args, ctx) is None
    await coding_agent._coding_after_tool_callback(tool, args, ctx, {"status": "success", "path": "synthetic.py"})
    ctx.invocation_id = "synthetic-turn-2"
    assert await coding_agent._coding_before_tool_callback(tool, args, ctx) is None


@pytest.mark.asyncio
async def test_coding_orientation_cap_is_per_turn():
    ctx = SimpleNamespace(state={}, invocation_id="synthetic-turn-2")
    state = harness.normalize_coding_harness_state({})
    state.update({"lightweight_calls": 240, "invocation_id": "synthetic-turn-1"})
    ctx.state[harness.CODING_HARNESS_STATE_KEY] = state
    result = await coding_agent._coding_before_tool_callback(SimpleNamespace(name="get_file_outline"), {"path": "synthetic.py"}, ctx)
    assert result is None, result
    assert ctx.state[harness.CODING_HARNESS_STATE_KEY]["lightweight_calls"] == 1


@pytest.mark.asyncio
async def test_explicit_coding_budget_also_bounds_orientation():
    ctx = SimpleNamespace(state={SESSION_CONTROL_STATE_KEY: {"tool_call_budget": 1}}, invocation_id="synthetic-budget")
    tool = SimpleNamespace(name="get_file_outline")
    assert await coding_agent._coding_before_tool_callback(tool, {"path": "one.py"}, ctx) is None
    result = await coding_agent._coding_before_tool_callback(tool, {"path": "two.py"}, ctx)
    assert result and result["status"] == "error"


@pytest.mark.asyncio
async def test_research_memory_is_isolated_across_concurrent_sessions(monkeypatch):
    from autoyou_agents.internet_agent import internet_tool
    async def fake_fetch(url, **kwargs):
        await asyncio.sleep(0)
        research_rag.get_research_rag_store().index_document(url, "Synthetic", url.rsplit("/", 1)[-1])
        return {"status": "success", "url": url}
    monkeypatch.setattr(internet_tool._get_internet_tool(), "fetch_webpage", fake_fetch)
    contexts = [SimpleNamespace(state={}) for _ in range(2)]
    await asyncio.gather(*[
        internet_tool.fetch_webpage(f"https://example.com/{term}", tool_context=ctx)
        for term, ctx in zip(("orbit_alpha", "velocity_beta"), contexts)
    ])
    first, second = [internet_tool.query_research_memory("", tool_context=ctx)["matches"] for ctx in contexts]
    assert [r["url"] for r in first] == ["https://example.com/orbit_alpha"]
    assert [r["url"] for r in second] == ["https://example.com/velocity_beta"]
    # Scope survives the shallow copy/persistence used by AgentTool.
    clone = SimpleNamespace(state=json.loads(json.dumps(contexts[0].state)))
    assert internet_tool.query_research_memory("orbit_alpha", tool_context=clone)["matches"]
    assert not internet_tool.query_research_memory("orbit_alpha", tool_context=contexts[1])["matches"]


def test_research_context_is_not_exposed_as_a_model_argument():
    from google.adk.tools import FunctionTool
    from autoyou_agents.internet_agent import internet_tool
    for function in (internet_tool.fetch_webpage, internet_tool.fetch_webpages_batch,
                     internet_tool.research_web, internet_tool.query_research_memory, internet_tool.scrape_website):
        declaration = FunctionTool(function)._get_declaration()
        schema = declaration.parameters_json_schema or declaration.parameters.model_dump(exclude_none=True)
        assert "tool_context" not in (schema.get("properties") or {})


@pytest.mark.asyncio
async def test_default_harness_survives_months_of_elapsed_time_and_many_calls(monkeypatch):
    from shared import session_execution as execution
    from autoyou_agents.internet_agent import expanded_harness
    import autoyou_agents.agent as root
    for key in ("AUTOYOU_CODING_AGENT_MAX_TOOL_CALLS", "AUTOYOU_CODING_AGENT_MAX_TURN_SECONDS",
                "AUTOYOU_SESSION_TURN_TIMEOUT_SECONDS", "AUTOYOU_INTERNET_MAX_TOOL_CALLS",
                "AUTOYOU_INTERNET_MAX_CALLS_PER_DOMAIN", "AUTOYOU_DYNAMIC_AGENT_MAX_TOOL_CALLS",
                "AUTOYOU_MAX_LLM_CALLS", "ADK_MAX_LLM_CALLS"):
        monkeypatch.delenv(key, raising=False)
    assert execution.resolve_turn_timeout_seconds() == 0
    assert execution.resolve_agent_model_call_budget() == 0
    ctx = SimpleNamespace(state={SESSION_CONTROL_STATE_KEY: {
        "turn_started_at": time.time() - 180 * 86400,
        "tool_calls": 1_000_000, "model_calls": 1_000_000,
    }}, invocation_id="synthetic-long-horizon")
    assert await coding_agent._coding_before_model_callback(ctx, None) is None
    assert await coding_agent._coding_before_tool_callback(SimpleNamespace(name="read_file"), {"path": "synthetic.py"}, ctx) is None
    assert ctx.state[SESSION_CONTROL_STATE_KEY]["tool_calls"] == 1_000_001
    ctx.state[expanded_harness._GUARD_STATE_KEY] = {
        "invocation_id": ctx.invocation_id, "count": 1_000_000, "domains": {"example.com": 1_000_000},
    }
    assert await expanded_harness.before_tool_callback(SimpleNamespace(name="fetch_webpage"), {"url": "https://example.com/next"}, ctx) is None
    dynamic = root._make_dynamic_agent_before_tool_callback("synthetic_custom_agent")
    assert await dynamic(SimpleNamespace(name="lookup"), {}, ctx) is None
    guard_key = root._dynamic_agent_guard_state_key("synthetic_custom_agent")
    ctx.state[guard_key]["tool_calls"] = 1_000_000
    assert await dynamic(SimpleNamespace(name="lookup"), {}, ctx) is None


@pytest.mark.asyncio
async def test_unbounded_session_remains_cancellable_and_zero_overrides_default():
    from shared.session_execution import SessionExecutionManager, SessionTurnCancelledError
    manager = SessionExecutionManager(default_turn_timeout_seconds=0.001)
    identity = manager.resolve_transport_identity("synthetic", "test-operator")
    started = asyncio.Event()
    cleaned_up = asyncio.Event()
    async def handler():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()
    task = asyncio.create_task(manager.submit_turn(identity, handler, timeout_seconds=0))
    try:
        await started.wait()
        await asyncio.sleep(0.02)
        assert not task.done()
        await manager.cancel_turn(identity)
        with pytest.raises(SessionTurnCancelledError):
            await task
        assert cleaned_up.is_set()
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_nested_adk_runner_can_exceed_500_model_calls():
    from google.adk.agents import Agent
    from google.adk.agents.run_config import RunConfig
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.sessions import InMemorySessionService
    from google.adk.tools import AgentTool
    from google.genai import types
    class CountingModel(BaseLlm):
        model: str = "synthetic-long-horizon"
        count: int = 0
        async def generate_content_async(self, llm_request, stream=False):
            self.count += 1
            part = types.Part.from_function_call(name="tick", args={}) if self.count <= 510 else types.Part(text="Finished 510 tool calls")
            yield LlmResponse(content=types.Content(role="model", parts=[part]))
    def tick() -> dict:
        return {"status": "success"}
    model = CountingModel()
    child = Agent(name="synthetic_worker", model=model, instruction="Synthetic loop", tools=[tick])
    # AgentTool constructs a real child Runner and propagates the parent RunConfig.
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.tools import ToolContext
    service = InMemorySessionService()
    parent = Agent(name="synthetic_parent", model=model)
    session = await service.create_session(app_name="synthetic", user_id="operator", session_id="test")
    invocation = InvocationContext(invocation_id="synthetic-invocation", session=session, session_service=service,
                                   agent=parent, run_config=RunConfig(max_llm_calls=0))
    ctx = ToolContext(invocation_context=invocation)
    result = await AgentTool(child).run_async(args={"request": "Run synthetic tool calls"}, tool_context=ctx)
    assert model.count == 511
    assert "510 tool calls" in str(result)


@pytest.mark.asyncio
async def test_coding_final_response_verifies_pending_files_and_retains_handoff(workspace):
    from google.genai import types
    from google.adk.models.llm_response import LlmResponse
    from autoyou_agents.shared_tools import coding_handoff
    ctx = SimpleNamespace(state={}, invocation_id="synthetic-verification")
    payload = {"source_agent": "games_agent", "implementation_brief": "Synthetic work", "constraints": "No external assets"}
    coding_handoff.store_coding_handoff(ctx.state, payload)
    assert coding_agent.get_pending_builder_handoff(ctx)["handoff_present"]
    request = SimpleNamespace(contents=[], config=SimpleNamespace(system_instruction="", max_output_tokens=1024))
    harness.prepare_coding_llm_request(ctx, request, tool_calls=0, tool_budget=0)
    assert "No external assets" in request.config.system_instruction
    # More modified files than fit in the recent-files preview still all need verification.
    for index in range(50):
        name = f"part{index}.py"
        (workspace / name).write_text("value = 1\n", encoding="utf-8")
        await coding_agent._coding_after_tool_callback(SimpleNamespace(name="write_file"), {"path": name}, ctx,
                                                       {"status": "success", "path": name})
    final = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Done, all files are ready.")]))
    forced = await coding_agent._coding_after_model_callback(ctx, final)
    call = forced.content.parts[0].function_call
    assert call.name == "verify_workspace_files"
    assert len(json.loads(call.args["paths_json"])) == 50
    result = wt.verify_workspace_files(call.args["paths_json"])
    await coding_agent._coding_after_tool_callback(SimpleNamespace(name=call.name), dict(call.args), ctx, result)
    assert not ctx.state[harness.CODING_HARNESS_STATE_KEY]["pending_verification"]
    assert await coding_agent._coding_after_model_callback(ctx, final) is None


@pytest.mark.asyncio
async def test_coding_progress_reply_continues_via_checkpoint():
    from google.genai import types
    from google.adk.models.llm_response import LlmResponse
    ctx = SimpleNamespace(state={}, invocation_id="synthetic-progress")
    reply = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Let me inspect the files.")]))
    result = await coding_agent._coding_after_model_callback(ctx, reply)
    assert result.content.parts[0].function_call.name == "manage_coding_plan"


def test_plan_preserves_more_than_twenty_steps_and_survives_serialization():
    ctx = SimpleNamespace(state={}, invocation_id="synthetic-plan")
    steps = [f"Implement component {index}" for index in range(100)]
    plan = harness.manage_coding_plan(action="init", goal="Synthetic long project", steps_json=json.dumps(steps), tool_context=ctx)
    assert len(plan["steps"]) == 100
    restored = SimpleNamespace(state=json.loads(json.dumps(ctx.state)))
    plan = harness.manage_coding_plan(action="complete_step", steps_json="75", tool_context=restored)
    assert len(plan["steps"]) == 100
    assert plan["steps"][74]["status"] == "done"


def test_game_builder_rejects_symlinked_projects_and_assets(tmp_path, monkeypatch):
    from autoyou_agents.games_agent import game_builder_tool as games
    projects = tmp_path / "projects"
    projects.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("AUTOYOU_GAME_PROJECTS_ROOT", str(projects))
    (projects / "linked-game").symlink_to(outside, target_is_directory=True)
    assert games.scaffold_offline_game("linked-game", "Synthetic", overwrite=True)["status"] == "error"
    assert not list(outside.iterdir())
    result = games.scaffold_offline_game("safe-game", "Synthetic")
    assert result["status"] == "success"
    project = Path(result["game_dir"])
    (outside / "private.js").write_text("synthetic_private_value = 1", encoding="utf-8")
    (project / "game.js").unlink()
    (project / "game.js").symlink_to(outside / "private.js")
    assert games.validate_offline_game(str(project))["valid"] is False


@pytest.mark.asyncio
async def test_coding_command_cancellation_stops_its_worker(workspace, monkeypatch):
    import threading
    started = threading.Event()
    stopped = threading.Event()
    def fake_command(command, cwd, timeout, cancel):
        started.set()
        assert cancel.wait(3), "Cancellation did not reach the tool worker"
        stopped.set()
        return {"status": "error", "cancelled": True}
    monkeypatch.setattr(coding_agent, "_run_command_with_cancel", fake_command)
    task = asyncio.create_task(coding_agent.run_command("synthetic", timeout_seconds=0))
    await asyncio.to_thread(started.wait, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_specialist_checkpoint_survives_child_cancellation_and_session_reopen(tmp_path):
    from google.adk.agents import Agent
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.agents.run_config import RunConfig
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.sessions import DatabaseSessionService
    from google.adk.tools import ToolContext
    from google.genai import types
    from autoyou_agents.shared_tools.agent_execution import CheckpointedAgentTool
    ready = asyncio.Event()
    class CheckpointModel(BaseLlm):
        model: str = "synthetic-checkpoint"
        count: int = 0
        async def generate_content_async(self, llm_request, stream=False):
            self.count += 1
            if self.count == 1:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_function_call(
                    name="save_checkpoint", args={})]))
            else:
                ready.set()
                await asyncio.Event().wait()
                if False:
                    yield
    def save_checkpoint(tool_context: ToolContext) -> dict:
        tool_context.state["_autoyou_coding_harness"] = {"goal": "Synthetic persistent task", "stage": "implement"}
        return {"status": "success"}
    url = f"sqlite+aiosqlite:///{tmp_path / 'sessions.sqlite3'}"
    service = DatabaseSessionService(db_url=url)
    model = CheckpointModel()
    parent = Agent(name="synthetic_parent", model=model)
    child = Agent(name="synthetic_child", model=model, tools=[save_checkpoint])
    session = await service.create_session(app_name="synthetic", user_id="operator", session_id="checkpoint")
    ctx = ToolContext(invocation_context=InvocationContext(invocation_id="synthetic-root-run", session=session,
                      session_service=service, agent=parent, run_config=RunConfig(max_llm_calls=0)))
    task = asyncio.create_task(CheckpointedAgentTool(child).run_async(args={"request": "Synthetic task"}, tool_context=ctx))
    try:
        await asyncio.wait_for(ready.wait(), 5)
        # The specialist has not returned, yet a fresh service can recover its plan.
        reopened = DatabaseSessionService(db_url=url)
        restored = await reopened.get_session(app_name="synthetic", user_id="operator", session_id="checkpoint")
        assert restored.state["_autoyou_coding_harness"]["goal"] == "Synthetic persistent task"
        assert any(event.custom_metadata and event.custom_metadata.get("autoyou_specialist_checkpoint") for event in restored.events)
        await reopened.close()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await service.close()


def test_event_cache_keeps_request_and_call_group_while_persisted_checkpoints_grow():
    from google.adk.events import Event
    from google.genai import types
    from autoyou_agents.shared_tools.agent_execution import bound_session_event_cache
    goal = Event(author="user", content=types.Content(role="user", parts=[types.Part(text="Synthetic original goal")]))
    call = Event(author="synthetic_root", content=types.Content(role="model", parts=[types.Part.from_function_call(name="worker", args={})]))
    events = [goal, call]
    ctx = SimpleNamespace(_invocation_context=SimpleNamespace(session=SimpleNamespace(events=events), session_service=SimpleNamespace()))
    for _ in range(2000):
        events.append(Event(author="synthetic_child", custom_metadata={"autoyou_specialist_checkpoint": True}))
        bound_session_event_cache(ctx)
    assert len(events) < 300
    assert events[:2] == [goal, call]


def test_site_feed_redirect_cannot_reach_a_private_address(monkeypatch):
    from autoyou_agents.internet_agent import search_providers
    from shared import url_safety
    visited = []
    def guard(url, **kwargs):
        if "127.0.0.1" in url:
            raise url_safety.UnsafeURLError("synthetic private destination")
    def get(url, **kwargs):
        visited.append(url)
        assert "127.0.0.1" not in url
        return SimpleNamespace(status_code=302, headers={"location": "http://127.0.0.1/private"})
    monkeypatch.setattr(url_safety, "assert_safe_http_url", guard)
    result = search_providers._search_site_direct_feed(SimpleNamespace(get=get), "site:example.com headlines", 5)
    assert result[1] == []
    assert visited == ["https://example.com/"]


def test_batch_reads_do_not_drop_files_or_hide_one_bad_entry(workspace):
    for index in range(21):
        (workspace / f"{index}.txt").write_text(f"synthetic {index}", encoding="utf-8")
    entries = [{"path": f"{i}.txt"} for i in range(21)]
    entries[10]["start_line"] = "invalid"
    result = wt.read_files_batch(entries)
    assert len(result["results"]) == 21
    assert result["results"][10]["status"] == "error"
    assert result["results"][-1]["status"] == "success"


def test_multi_edit_whitespace_fallback_obeys_expected_count(workspace):
    (workspace / "sample.txt").write_text("  value = 1\n  other = 2\n", encoding="utf-8")
    result = wt.multi_edit_file("sample.txt", [{"old_text": "value = 1\nother = 2", "new_text": "value = 2\nother = 2", "expected_replacements": 2}])
    assert result["status"] == "error"
    assert (workspace / "sample.txt").read_text() == "  value = 1\n  other = 2\n"


def test_scoped_rag_filters_before_global_result_limit(workspace):
    (workspace / "z").mkdir()
    for index in range(125):
        (workspace / f"a{index:03}.js").write_text('function shared_symbol() { return "nebula"; }\n', encoding="utf-8")
    (workspace / "z" / "target.py").write_text('def shared_symbol():\n    return "nebula"\n', encoding="utf-8")
    workspace_rag.find_symbol("shared_symbol")
    assert workspace_rag.find_symbol("shared_symbol", path="z")["matches"][0]["path"] == "z/target.py"
    assert workspace_rag.rag_search_workspace("nebula", path="z")["results"][0]["path"] == "z/target.py"
    assert workspace_rag.rag_search_workspace("nebula", file_type="py")["results"][0]["path"] == "z/target.py"


def test_research_rag_indexes_the_tail_beyond_sixty_chunks_and_bounds_memory(monkeypatch):
    monkeypatch.setattr(research_rag, "_MAX_STORE_CHARS", 400_000)
    store = research_rag.WebResearchRAGStore()
    text = ("Synthetic background content. " * 5000) + "\n\nThe unique tail marker is xenon_tail_482."
    first = store.index_document("https://example.com/first", "Synthetic first", text)
    assert first["chunks"] > 60
    assert any("xenon_tail_482" in hit["excerpt"] for hit in store.query("xenon_tail_482"))
    for index in range(6):
        store.index_document(f"https://example.com/{index}", "Synthetic", text)
    assert store.stats()["total_chars"] <= 400_000
    assert "https://example.com/first" not in store.stats()["urls"]


def test_unified_delete_checks_current_contents_before_deleting(workspace):
    target = workspace / "sample.txt"
    target.write_text("changed by operator\n", encoding="utf-8")
    patch = "--- a/sample.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-obsolete contents\n"
    assert wt.apply_patch(patch, allow_delete=True)["status"] == "error"
    assert target.read_text() == "changed by operator\n"
    assert wt.apply_patch(patch.replace("obsolete contents", "changed by operator"), allow_delete=True)["status"] == "success"
    assert not target.exists()


def test_research_url_filter_does_not_treat_underscores_as_wildcards():
    store = research_rag.WebResearchRAGStore()
    for suffix in ("a_b", "axb"):
        store.index_document(f"https://example.com/{suffix}", "Synthetic", "Shared orbital telemetry")
    assert [hit["url"] for hit in store.query("orbital", url_filter="/a_b")] == ["https://example.com/a_b"]


def test_concurrent_workspace_appends_preserve_every_chunk(workspace, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    original = wt._atomic_write_bytes
    def slow_write(path, payload):
        time.sleep(0.002)
        original(path, payload)
    monkeypatch.setattr(wt, "_atomic_write_bytes", slow_write)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: wt.append_file("shared.txt", f"synthetic-{i}\n"), range(32)))
    assert all(result["status"] == "success" for result in results)
    assert set((workspace / "shared.txt").read_text().splitlines()) == {f"synthetic-{i}" for i in range(32)}


def test_serialized_workspace_tools_retain_adk_schemas():
    from google.adk.tools import FunctionTool
    for function in (wt.write_file, wt.append_file, wt.multi_edit_file, wt.apply_patch):
        declaration = FunctionTool(function)._get_declaration()
        schema = declaration.parameters_json_schema or declaration.parameters.model_dump(exclude_none=True)
        assert declaration.name == function.__name__
        assert schema["properties"]
        assert "args" not in schema["properties"] and "kwargs" not in schema["properties"]


@pytest.mark.asyncio
async def test_provider_cancellation_is_not_converted_into_a_model_error_reply():
    with pytest.raises(asyncio.CancelledError):
        await coding_agent._coding_model_error_callback(SimpleNamespace(state={}), None, asyncio.CancelledError())


@pytest.mark.asyncio
async def test_parent_runner_can_finish_after_midtool_database_checkpoints(tmp_path):
    from google.adk.agents import Agent
    from google.adk.agents.run_config import RunConfig
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import Runner
    from google.adk.sessions import DatabaseSessionService
    from google.adk.tools import ToolContext
    from google.genai import types
    from autoyou_agents.shared_tools.agent_execution import CheckpointedAgentTool
    class SequenceModel(BaseLlm):
        model: str = "synthetic-checkpoint-sequence"
        replies: list
        async def generate_content_async(self, llm_request, stream=False):
            yield LlmResponse(content=types.Content(role="model", parts=[self.replies.pop(0)]))
    def save_checkpoint(tool_context: ToolContext) -> dict:
        tool_context.state["_autoyou_coding_harness"] = {"goal": "Synthetic completed task"}
        return {"status": "success"}
    child_model = SequenceModel(replies=[types.Part.from_function_call(name="save_checkpoint", args={}), types.Part(text="Child completed")])
    child = Agent(name="synthetic_worker", model=child_model, tools=[save_checkpoint])
    root_model = SequenceModel(replies=[types.Part.from_function_call(name=child.name, args={"request": "Synthetic work"}), types.Part(text="Root completed")])
    parent = Agent(name="synthetic_root", model=root_model, tools=[CheckpointedAgentTool(child)])
    url = f"sqlite+aiosqlite:///{tmp_path / 'complete.sqlite3'}"
    service = DatabaseSessionService(db_url=url)
    await service.create_session(app_name="synthetic", user_id="operator", session_id="complete")
    runner = Runner(agent=parent, app_name="synthetic", session_service=service)
    try:
        events = [event async for event in runner.run_async(user_id="operator", session_id="complete",
                  new_message=types.Content(role="user", parts=[types.Part(text="Synthetic work")]), run_config=RunConfig(max_llm_calls=0))]
        assert any(event.content and any(part.text == "Root completed" for part in event.content.parts or []) for event in events)
        reopened = DatabaseSessionService(db_url=url)
        try:
            restored = await reopened.get_session(app_name="synthetic", user_id="operator", session_id="complete")
            assert restored.state["_autoyou_coding_harness"]["goal"] == "Synthetic completed task"
        finally:
            await reopened.close()
    finally:
        await runner.close()
        await service.close()
