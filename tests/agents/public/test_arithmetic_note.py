# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Plain arithmetic questions reach the model with their exact result.

Owner report 2026-11-03: "What is 37x52-3/3" was answered as 1921/3 (the
subtraction done before the division) instead of 1923.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from fractions import Fraction
from types import SimpleNamespace

import pytest
from google.genai import types

import autoyou_agents.agent as root_agent_module
from autoyou_agents.arithmetic import calculate, calculator_note, evaluate_arithmetic
from shared.adk_state import AUTOYOU_TURN_CONTINUATION_STATE_KEY


@pytest.mark.parametrize("message,result", [
    ("What is 37x52-3/3", "= 1923."),
    ("37x52-3/3", "= 1923."),
    ("37 × 52 − 3 ÷ 3 =", "= 1923."),
    ("what's (2+3)*4?", "= 20."),
    ("2^10", "= 1024."),
    ("What is 2 to the power of 8", "= 256."),
    ("what is 10/3?", "= 3.3333333333 (exactly 10/3)."),
    ("Calculate: 1,924 - 3", "= 1921."),
    ("[voice transcript] what is 12 times 7", "= 84."),
    ("what is -3^2", "= -9."),
    ("How much is 0.1 + 0.2?", "= 0.3 (exactly 3/10)."),
    # Spanish (owner, 2026-11-03: English + Spanish for now).
    ("¿Cuánto es 37x52-3/3?", "= 1923."),
    ("calcula 12 por 7, por favor", "= 84."),
    ("cuánto es 10 entre 4", "= 2.5 (exactly 5/2)."),
    ("qué es 2 elevado a 10", "= 1024."),
    ("¿Cuánto son 5 más 3 menos 1?", "= 7."),
    ("calcula 3 multiplicado por 4", "= 12."),
])
def test_arithmetic_questions_get_the_exact_result(message, result):
    note = calculator_note(message)
    assert note.startswith("[Calculator] ")
    assert result in note


@pytest.mark.parametrize("message", [
    "555-1234",
    "2026-11-03",
    "12/25",
    "5+5",
    "what is -5",
    "What is the capital of France?",
    "37x52-3/3 and tell me a joke",
    "what is 1/0",
    "what is 9^999",
    "what is 2^0.5",
    "what is __import__('os')",
    "Hi what is this",
    "",
    "cuánto es la capital de Francia",
    "¿Qué hora es?",
])
def test_anything_else_gets_no_note(message):
    assert calculator_note(message) == ""


def test_the_calculate_tool_needs_no_wording():
    assert calculate("37*52-3/3") == {"status": "success", "expression": "37*52-3/3", "result": "1923"}
    assert calculate("12 por 7")["result"] == "84"
    assert calculate("10/4")["result"] == "2.5 (exactly 5/2)"
    assert calculate("hola")["status"] == "error"


def test_numbers_are_exact_not_floating_point():
    assert evaluate_arithmetic("0.1 + 0.2") == Fraction(3, 10)
    assert evaluate_arithmetic("37*52-3/3") == 1923


@pytest.mark.asyncio
async def test_the_root_model_is_given_the_result_for_that_call(monkeypatch):
    from google.adk.models.llm_request import LlmRequest

    monkeypatch.setattr(root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", set())
    monkeypatch.setattr(root_agent_module, "_RUNTIME_AGENT_TOOL_NAMES_BY_INSTALL_NAME", {})
    monkeypatch.setattr(root_agent_module, "_is_runtime_agent_enabled", lambda name: False)
    request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="What is 37x52-3/3")])])
    context = SimpleNamespace(state={}, invocation_id="synthetic-arithmetic")

    response = await root_agent_module._root_before_model_callback(context, request)

    assert response is None
    assert "[Calculator] 37x52-3/3 = 1923." in str(request.config.system_instruction)

    plain = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="Hi what is this")])])
    assert await root_agent_module._root_before_model_callback(context, plain) is None
    assert "[Calculator]" not in str(plain.config.system_instruction or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt,noted", [(1, True), (0, False)])
async def test_a_continuation_run_is_told_to_finish_the_request(monkeypatch, attempt, noted):
    """The server re-sends the user's words and marks the run; the root adds the note."""
    from google.adk.models.llm_request import LlmRequest

    monkeypatch.setattr(root_agent_module, "_AVAILABLE_RUNTIME_AGENT_NAMES", set())
    monkeypatch.setattr(root_agent_module, "_RUNTIME_AGENT_TOOL_NAMES_BY_INSTALL_NAME", {})
    monkeypatch.setattr(root_agent_module, "_is_runtime_agent_enabled", lambda name: False)
    request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="¿Qué noticias hay hoy?")])])
    context = SimpleNamespace(state={AUTOYOU_TURN_CONTINUATION_STATE_KEY: attempt}, invocation_id="synthetic-continuation")

    assert await root_agent_module._root_before_model_callback(context, request) is None

    assert ("[Turn continuation]" in str(request.config.system_instruction or "")) is noted
