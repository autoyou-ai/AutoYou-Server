# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""A model that will not load is answered for by a smaller installed one.

Owner report 2026-11-04: every reply was "the AI agent server returned an
internal error" because Ollama could not load the configured model
(`std::bad_alloc`: the computer was low on system memory) while small models
still loaded. Nothing here touches a real Ollama: the installed-model list and
the models are stand-ins with synthetic names.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import asyncio
from types import SimpleNamespace
from typing import Any, AsyncGenerator, List

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

import autoyou_agents.agent as root_agent_module
from autoyou_agents import model_fallback

# The wire form the live server logged on 2026-11-04.
BAD_ALLOC = (
    "litellm.InternalServerError: litellm.InternalServerError: Ollama_chatException - "
    '{"error":"llama-server process has terminated: exit status 1: error loading model: std::bad_alloc"}'
)
PRIMARY = "big-test:27b"
INSTALLED = [PRIMARY, "small-test:8b", "tiny-test:3b"]

CALLS: List[str] = []


class _StandInOllama(BaseLlm):
    """Answers like LiteLlm: the request's model wins over the instance's; PRIMARY cannot load."""

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        effective = model_fallback._bare_name(llm_request.model or self.model)
        CALLS.append(effective)
        if effective in UNLOADABLE:
            raise RuntimeError(BAD_ALLOC)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=f"answer from {effective}")]),
            model_version=effective,
        )


UNLOADABLE = {PRIMARY}


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    CALLS.clear()
    UNLOADABLE.clear()
    UNLOADABLE.add(PRIMARY)
    model_fallback._skipped.clear()
    monkeypatch.setenv(model_fallback.FALLBACK_MODELS_ENV, "small-test:8b, tiny-test:3b")
    monkeypatch.setattr(model_fallback, "_installed_models", lambda api_base: list(INSTALLED))
    monkeypatch.setattr(
        model_fallback,
        "_make_fallback_llm",
        lambda name, settings: _StandInOllama(model=model_fallback._OLLAMA_CHAT + name),
    )
    yield
    model_fallback._skipped.clear()


def _context(model: Any, state: Any = None) -> Any:
    return SimpleNamespace(
        _invocation_context=SimpleNamespace(agent=SimpleNamespace(canonical_model=model)),
        state={} if state is None else state,
    )


def _request(model: str = PRIMARY) -> LlmRequest:
    return LlmRequest(
        model=model_fallback._OLLAMA_CHAT + model,
        contents=[types.Content(role="user", parts=[types.Part(text="How long does a phone battery last?")])],
    )


def _primary() -> _StandInOllama:
    return _StandInOllama(model=model_fallback._OLLAMA_CHAT + PRIMARY)


def test_load_and_memory_errors_are_told_apart_from_other_failures():
    assert model_fallback.is_model_load_error(BAD_ALLOC)
    assert model_fallback.is_memory_shortage(BAD_ALLOC)
    stopped = 'Ollama_chatException - {"error":"model runner has unexpectedly stopped, this may be due to resource limitations"}'
    assert model_fallback.is_model_load_error(stopped)
    corrupt = 'Ollama_chatException - {"error":"llama-server process has terminated: error loading model: unknown model architecture"}'
    assert model_fallback.is_model_load_error(corrupt) and not model_fallback.is_memory_shortage(corrupt)
    for other in (
        "litellm.APIConnectionError: Ollama_chatException - connection refused",
        "the request (9100 tokens) exceeds the available context size (8192 tokens)",
        "llama-server returned invalid tool call arguments",
    ):
        assert not model_fallback.is_model_load_error(other)


def test_fallbacks_follow_the_configured_order_and_skip_what_failed(monkeypatch):
    assert model_fallback.fallback_models([PRIMARY], "http://ollama.test") == ["small-test:8b", "tiny-test:3b"]
    assert model_fallback.fallback_models([PRIMARY, "small-test:8b"], "http://ollama.test") == ["tiny-test:3b"]

    monkeypatch.setenv(model_fallback.FALLBACK_MODELS_ENV, "absent-test:1b, tiny-test")
    assert model_fallback.fallback_models([PRIMARY], "http://ollama.test") == []  # "tiny-test" means tiny-test:latest

    monkeypatch.setenv(model_fallback.FALLBACK_MODELS_ENV, "none")
    assert model_fallback.fallback_models([PRIMARY], "http://ollama.test") == []

    monkeypatch.delenv(model_fallback.FALLBACK_MODELS_ENV)
    monkeypatch.setattr(model_fallback, "_installed_models", lambda api_base: ["ministral-3:3b", "gemma4:e4b", PRIMARY])
    assert model_fallback.fallback_models([PRIMARY], "http://ollama.test") == ["gemma4:e4b", "ministral-3:3b"]


def test_a_model_that_cannot_load_is_answered_by_the_first_fallback_that_does():
    UNLOADABLE.add("small-test:8b")
    primary = _primary()
    primary._additional_args = {"api_base": "http://ollama.test", "num_ctx": 12288, "think": False}
    seen_settings = []
    original = model_fallback._make_fallback_llm

    def recording(name, settings):
        seen_settings.append(dict(settings))
        return original(name, settings)

    model_fallback._make_fallback_llm = recording
    try:
        response = asyncio.run(model_fallback.answer_with_fallback_model(_context(primary), _request(), RuntimeError(BAD_ALLOC)))
    finally:
        model_fallback._make_fallback_llm = original

    assert response.content.parts[0].text == "answer from tiny-test:3b"
    assert response.custom_metadata["model_fallback"] == {"from": PRIMARY, "to": "tiny-test:3b", "reason": "memory"}
    assert CALLS == ["small-test:8b", "tiny-test:3b"]
    # The agent's own window and endpoint carry over; thinking does not.
    assert seen_settings[0] == {"api_base": "http://ollama.test", "num_ctx": 12288}
    assert model_fallback._skip_target(PRIMARY) == ("tiny-test:3b", "memory")


def test_other_errors_and_other_providers_are_left_to_the_other_callbacks():
    primary = _primary()
    assert asyncio.run(
        model_fallback.answer_with_fallback_model(_context(primary), _request(), RuntimeError("connection refused"))
    ) is None
    cloud = SimpleNamespace(model="openrouter/some-test-model")
    assert asyncio.run(model_fallback.answer_with_fallback_model(_context(cloud), _request(), RuntimeError(BAD_ALLOC))) is None
    UNLOADABLE.update(INSTALLED)
    assert asyncio.run(model_fallback.answer_with_fallback_model(_context(primary), _request(), RuntimeError(BAD_ALLOC))) is None
    assert CALLS == ["small-test:8b", "tiny-test:3b"]


def test_after_a_failed_load_calls_go_straight_to_the_fallback_until_the_pause_ends(monkeypatch):
    primary = _primary()
    request = _request()
    asyncio.run(model_fallback.route_to_loadable_model(_context(primary), request))
    assert request.model == model_fallback._OLLAMA_CHAT + PRIMARY  # nothing failed yet

    model_fallback._skipped[PRIMARY] = (model_fallback.time.monotonic(), "small-test:8b", "memory")
    asyncio.run(model_fallback.route_to_loadable_model(_context(primary), request))
    assert request.model == model_fallback._OLLAMA_CHAT + "small-test:8b"

    # A request with reasoning on stays on the model that supports it.
    thinking = _primary()
    thinking._additional_args = {"think": True}
    kept = _request()
    asyncio.run(model_fallback.route_to_loadable_model(_context(thinking), kept))
    assert kept.model == model_fallback._OLLAMA_CHAT + PRIMARY

    model_fallback._skipped[PRIMARY] = (model_fallback.time.monotonic() - model_fallback.SKIP_SECONDS - 1, "small-test:8b", "memory")
    retried = _request()
    asyncio.run(model_fallback.route_to_loadable_model(_context(primary), retried))
    assert retried.model == model_fallback._OLLAMA_CHAT + PRIMARY
    assert PRIMARY not in model_fallback._skipped


def _text_response(text: str, **fields: Any) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]), **fields)


def test_the_notice_is_added_once_and_only_to_a_final_fallback_answer():
    primary = _primary()
    state: dict = {}
    fallback_answer = _text_response(
        "A phone battery lasts two to three years.",
        custom_metadata={"model_fallback": {"from": PRIMARY, "to": "small-test:8b", "reason": "memory"}},
    )
    call = LlmResponse(
        content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="calculate", args={}))]),
        custom_metadata={"model_fallback": {"from": PRIMARY, "to": "small-test:8b", "reason": "memory"}},
    )

    assert asyncio.run(model_fallback.add_fallback_notice(_context(primary, state), call)) is None
    noticed = asyncio.run(model_fallback.add_fallback_notice(_context(primary, state), fallback_answer))
    text = noticed.content.parts[0].text
    assert text.startswith("A phone battery lasts two to three years.\n\n(Answered by small-test:8b:")
    assert "low on memory" in text and "Restarting the computer" in text
    again = _text_response("Still here.", custom_metadata={"model_fallback": {"from": PRIMARY, "to": "small-test:8b"}})
    assert asyncio.run(model_fallback.add_fallback_notice(_context(primary, state), again)) is None

    # While calls are redirected, only text the fallback wrote gets it.
    model_fallback._skipped[PRIMARY] = (model_fallback.time.monotonic(), "tiny-test:3b", "load")
    fresh: dict = {}
    assert asyncio.run(
        model_fallback.add_fallback_notice(_context(primary, fresh), _text_response("hi", model_version=PRIMARY))
    ) is None
    assert asyncio.run(
        model_fallback.add_fallback_notice(_context(primary, fresh), _text_response("hi", model_version="tiny-test:3b", partial=True))
    ) is None
    redirected = asyncio.run(
        model_fallback.add_fallback_notice(_context(primary, fresh), _text_response("hi", model_version="tiny-test:3b"))
    )
    assert "failed to load" in redirected.content.parts[0].text


def test_specialists_get_the_fallback_callbacks_last_and_once():
    def own_after(callback_context, llm_response):
        return None

    agent = SimpleNamespace(name="autoyou_example_agent", before_model_callback=None, after_model_callback=own_after)
    root_agent_module._install_model_fallback_callbacks(agent)
    root_agent_module._install_model_fallback_callbacks(agent)

    assert agent.before_model_callback == [model_fallback.route_to_loadable_model]
    assert agent.after_model_callback == [own_after, model_fallback.add_fallback_notice]
    assert agent.on_model_error_callback == [model_fallback.answer_with_fallback_model]


def test_the_root_agent_carries_the_fallback_callbacks():
    root = root_agent_module.root_agent
    for attribute, callback in (
        ("before_model_callback", model_fallback.route_to_loadable_model),
        ("after_model_callback", model_fallback.add_fallback_notice),
        ("on_model_error_callback", model_fallback.answer_with_fallback_model),
    ):
        assert list(getattr(root, attribute))[-1] is callback


async def _ask(runner: InMemoryRunner, session_id: str, text: str) -> str:
    replies = []
    async for event in runner.run_async(
        user_id="user-test",
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=text)]),
    ):
        if event.content and event.content.parts:
            replies.extend(part.text for part in event.content.parts if part.text)
    return "".join(replies)


def test_a_real_agent_run_answers_and_then_skips_the_model_that_will_not_load():
    """The ADK flow end to end: error callback, after-model notice, then the redirect."""
    agent = root_agent_module._install_model_fallback_callbacks(
        LlmAgent(name="autoyou_fallback_test_agent", model=_primary(), instruction="Answer briefly.")
    )

    async def conversation():
        runner = InMemoryRunner(agent=agent, app_name="fallback_test")
        session = await runner.session_service.create_session(app_name="fallback_test", user_id="user-test")
        first = await _ask(runner, session.id, "How long does a phone battery last?")
        calls_after_first = list(CALLS)
        second = await _ask(runner, session.id, "And a laptop battery?")
        return first, calls_after_first, second

    first, calls_after_first, second = asyncio.run(conversation())

    assert first.startswith("answer from small-test:8b\n\n(Answered by small-test:8b: big-test:27b could not load")
    assert calls_after_first == [PRIMARY, "small-test:8b"]
    # The second turn goes straight to the fallback and does not repeat the notice.
    assert second == "answer from small-test:8b"
    assert CALLS[len(calls_after_first):] == ["small-test:8b"]
