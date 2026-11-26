"""Opt-in Ollama checks against real AutoYou tools in disposable storage.

AUTOYOU_RUN_LIVE_HARNESS_TESTS=1 enables these tests. They never log in to or
change the running AutoYou server, its model configuration, or its registries.
"""

import ast
import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.agents import Agent
from google.adk.agents.run_config import RunConfig
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

import autoyou_agents.agent as runtime
from autoyou_agents.coding_agent.agent import create_coding_agent
from autoyou_agents.games_agent.agent import create_games_agent
from autoyou_agents.games_agent import game_builder_tool
from autoyou_agents.shared_tools.agent_execution import CheckpointedAgentTool as AgentTool
from shared import game_package


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("AUTOYOU_RUN_LIVE_HARNESS_TESTS") != "1", reason="opt-in local-model integration tests",
)]


@pytest.fixture
def live_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("AUTOYOU_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("AUTOYOU_GAME_PROJECTS_ROOT", str(workspace / "game_projects"))
    monkeypatch.setenv("AUTOYOU_CODING_BACKEND", "builtin")
    monkeypatch.setenv("AUTOYOU_CODING_AGENT_MAX_TOOL_CALLS", "0")
    monkeypatch.setenv("AUTOYOU_CODING_AGENT_MAX_TURN_SECONDS", "0")
    return workspace


def local_model():
    model = os.getenv("AUTOYOU_LIVE_HARNESS_MODEL", "qwen3.5:4b")
    return LiteLlm(model=f"ollama_chat/{model}", api_base=os.getenv("OLLAMA_API_BASE", "http://127.0.0.1:11434"),
                   num_ctx=32768, num_predict=8192, temperature=0, timeout=180)


async def turn(agent, prompt, *, service=None, session_id="synthetic-live", content=None):
    service = service or InMemorySessionService()
    if not await service.get_session(app_name="harness_probe", user_id="synthetic-operator", session_id=session_id):
        await service.create_session(app_name="harness_probe", user_id="synthetic-operator", session_id=session_id)
    runner = Runner(agent=agent, app_name="harness_probe", session_service=service)
    observed = []
    async def run():
        async for event in runner.run_async(
            user_id="synthetic-operator", session_id=session_id,
            new_message=content or types.Content(role="user", parts=[types.Part(text=prompt)]),
            run_config=RunConfig(max_llm_calls=0),
        ):
            observed.append(event)
    await asyncio.wait_for(run(), timeout=float(os.getenv("AUTOYOU_LIVE_HARNESS_TIMEOUT_SECONDS", "900")))
    calls = []
    for event in observed:
        for call in event.get_function_calls():
            calls.append(call.name)
            if call.name == "route_to_specialist":
                calls.append(str((call.args or {}).get("agent") or ""))
    responses = [response for event in observed for response in event.get_function_responses()]
    text = "\n".join(part.text for event in observed if event.is_final_response()
                     for part in (event.content.parts if event.content else []) if part.text)
    assert text.strip(), "Model finished without a final response"
    return service, calls, responses, text


async def test_live_coding_edits_and_verifies_actual_files(live_workspace):
    agent = create_coding_agent(local_model())
    service, calls, _, _ = await turn(agent,
        "Create scores.py in the workspace. Define score(combo: int) -> int returning combo * 10. "
        "Write the file using your tools and verify its syntax before finishing.")
    target = live_workspace / "scores.py"
    assert target.is_file()
    assert "write_file" in calls
    assert "verify_workspace_files" in calls
    _, calls, responses, _ = await turn(agent,
        "Inspect scores.py, then patch score so zero and negative combo values count as one: "
        "score(-5) == 10, score(0) == 10, score(3) == 30. Use apply_patch, replace_text or multi_edit_file "
        "to edit the existing file. Verify after the edit, then report the actual result.", service=service)
    assert {"apply_patch", "replace_text", "multi_edit_file"}.intersection(calls)
    assert "verify_workspace_files" in calls
    assert any(r.name == "verify_workspace_files" and r.response.get("all_passed") for r in responses)
    tree = ast.parse(target.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "score")
    # Evaluate only a simple arithmetic function, with no access to imports,
    # attributes, arbitrary calls, filesystem or the operator environment.
    allowed = (ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Call, ast.Name, ast.Load,
               ast.BinOp, ast.Mult, ast.Constant, ast.IfExp, ast.Compare, ast.Gt, ast.Lt,
               ast.GtE, ast.LtE, ast.UnaryOp, ast.USub, ast.If, ast.Expr, ast.Assign, ast.Store)
    assert all(isinstance(node, allowed) for node in ast.walk(function)), ast.dump(function)
    assert all(isinstance(node.func, ast.Name) and node.func.id in {"int", "max"}
               for node in ast.walk(function) if isinstance(node, ast.Call))
    namespace = {"__builtins__": {}, "int": int, "max": max}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "synthetic-score", "exec"), namespace)
    assert [namespace["score"](n) for n in (-5, 0, 3)] == [10, 10, 30]


async def test_live_games_to_coding_root_handoff(live_workspace, monkeypatch):
    model = local_model()
    games = create_games_agent(model)
    coding = create_coding_agent(model)
    started = time.monotonic()
    def trace_before(tool, args, tool_context):
        print(f"probe {time.monotonic()-started:.1f}s {tool_context.agent_name} -> {tool.name}", flush=True)
    def trace_after(tool, args, tool_context, tool_response):
        status = tool_response.get("status", "unknown") if isinstance(tool_response, dict) else "text"
        print(f"probe {time.monotonic()-started:.1f}s {tool_context.agent_name} <- {tool.name}: {status}", flush=True)
    games.before_tool_callback = [trace_before]
    games.after_tool_callback = [trace_after]
    coding.before_tool_callback = [trace_before, *coding.before_tool_callback]
    coding.after_tool_callback = [*coding.after_tool_callback, trace_after]
    enabled = {games.name, coding.name}
    monkeypatch.setattr(runtime, "_is_runtime_agent_enabled", lambda name: name in enabled)
    monkeypatch.setenv("AUTOYOU_TWO_STAGE_ROUTER", "1")
    monkeypatch.setattr(runtime, "_SPECIALIST_AGENT_TOOLS", {games.name: AgentTool(games), coding.name: AgentTool(coding)})
    monkeypatch.setattr(runtime, "_SPECIALIST_AGENT_DESCRIPTIONS", {games.name: games.description, coding.name: coding.description})
    root = Agent(name=runtime.root_prompt.AGENT_NAME, model=model,
                 instruction="Coordinate the user's game and coding tasks, using the prepared handoff and reporting concrete results.",
                 tools=[runtime.route_to_specialist],
                 before_model_callback=runtime._root_before_model_callback,
                 before_tool_callback=runtime._root_before_tool_callback,
                 after_tool_callback=runtime._root_after_tool_callback)
    service, calls, _, text = await turn(root,
        "@games Scaffold an offline game with game_id synthetic-orbit and title Synthetic Orbit. "
        "Then call prepare_game_coding_handoff with this implementation brief: add a Python file score_helper.py "
        "inside the game project defining score(combo) = max(1, combo) * 10; keep all existing game assets. "
        "Then call handoff_to_coding_agent so coding implements and verifies that file. Complete this handoff now.")
    assert games.name in calls, (calls, text)
    assert coding.name in calls, (calls, text)
    project = live_workspace / "game_projects" / "synthetic-orbit"
    assert (project / "score_helper.py").is_file(), text
    session = await service.get_session(app_name="harness_probe", user_id="synthetic-operator", session_id="synthetic-live")
    assert session.state.get("_autoyou_coding_harness", {}).get("files_modified")
    validated = game_builder_tool.validate_offline_game(str(project))
    assert validated["valid"], validated
    built = game_builder_tool.build_offline_game_package(str(project), publish_to_library=False)
    assert built["status"] == "success", built
    assert Path(built["package_path"]).is_file()
    assert game_package.package_supports_quit(built["package_path"])
    assert not built["published"]
    # Exercise the packaged game's actual browser behavior, including Quit.
    from playwright.async_api import async_playwright
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(offline=True)
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.add_init_script("window.AutoYouGame = { quit() { window.__syntheticQuit = true; } };")
            await page.goto((project / "index.html").as_uri())
            await page.locator("#start-btn").click()
            await page.keyboard.down("ArrowRight")
            await page.wait_for_timeout(100)
            await page.keyboard.up("ArrowRight")
            await page.locator("#settings-open").click()
            await page.locator("#settings-close").click()
            await page.locator("#quit").click()
            await page.wait_for_function("window.__syntheticQuit === true")
            assert not errors, errors
        finally:
            await browser.close()


async def test_live_multimodal_ollama_adapter(live_workspace):
    import struct
    import zlib
    # Synthetic solid red PNG, encoded without an image-generation service.
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00" * 64) * 64))
    png += chunk(b"IEND", b"")
    agent = Agent(name="synthetic_vision", model=local_model(), instruction="Answer the user's image question concisely.")
    _, _, _, text = await turn(agent, "", content=types.Content(role="user", parts=[
        types.Part(text="What is the solid color in this image? Answer with one color word."),
        types.Part(inline_data=types.Blob(mime_type="image/png", data=png)),
    ]))
    assert "red" in text.lower(), text


async def test_live_http_fetch_and_session_research_recall(live_workspace):
    from autoyou_agents.internet_agent import internet_tool
    ctx = SimpleNamespace(state={})
    result = await asyncio.wait_for(internet_tool.fetch_webpage(
        "https://www.iana.org/help/example-domains", query="documentation examples", tool_context=ctx), 30)
    assert result["status"] == "success", result.get("error", result)
    assert result["fetch_method"] == "http_fast"
    assert "example" in result["title"].lower()
    recall = internet_tool.query_research_memory("documentation", tool_context=ctx)
    assert recall["matches"], recall
    assert all("iana.org" in item["url"] for item in recall["matches"])
    assert internet_tool.query_research_memory("documentation", tool_context=SimpleNamespace(state={}))["matches"] == []
