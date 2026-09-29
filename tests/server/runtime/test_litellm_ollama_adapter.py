# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-9f09aa9c1c8cf6af35830930


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import json
from types import SimpleNamespace

from autoyou_agents.litellm_ollama_adapter import (
    _build_dynamic_keyword_hint_map,
    _extract_latest_user_request,
    _fix_unknown_tool_name,
    _safe_set_function_attr,
    _unwrap_json_wrapped_content,
    is_ollama_chat_model,
    looks_like_degenerate_tool_schema_echo,
    normalize_ollama_response,
    normalize_ollama_stream_response,
    prepare_messages_for_ollama,
    prepare_tools_for_ollama,
    repair_missing_tool_results,
    response_degenerated_into_tool_schema_echo,
    summarize_tool_names_for_debug,
)

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-9f09aa9c1c8cf6af35830930"


def test_prepare_messages_for_ollama_copies_and_sanitizes_assistant_tool_markers():
    original_messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "[TOOL_CALLS]get_current_datetime"}],
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "[TOOL_CALLS]get_current_datetime", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
        },
    ]

    prepared = prepare_messages_for_ollama(original_messages)

    assert isinstance(original_messages[0]["content"], list)
    assert original_messages[0]["tool_calls"][0]["function"]["name"] == "[TOOL_CALLS]get_current_datetime"
    assert prepared[0]["content"] is None
    assert prepared[0]["tool_calls"][0]["function"]["name"] == "get_current_datetime"
    assert prepared[1]["role"] == "tool"
    assert prepared[1]["tool_call_id"] == "call-1"
    assert "Missing tool result" in prepared[1]["content"]
    assert prepared[2]["content"] == "hello"

def test_prepare_messages_for_ollama_keeps_images_extractable_by_litellm():
    """Vision turns must stay in list form or litellm cannot build Ollama's `images`."""
    from litellm.litellm_core_utils.prompt_templates.common_utils import (
        convert_content_list_to_str,
        extract_images_from_message,
    )

    data_url = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD"
    prepared = prepare_messages_for_ollama(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this image"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
    )

    assert isinstance(prepared[0]["content"], list)
    assert convert_content_list_to_str(prepared[0]) == "Analyze this image"
    assert extract_images_from_message(prepared[0]) == ["/9j/4AAQSkZJRgABAQAAAQABAAD"]


def test_prepare_messages_for_ollama_keeps_image_only_turn():
    """An image with no caption must not collapse to content=None."""
    from litellm.litellm_core_utils.prompt_templates.common_utils import (
        extract_images_from_message,
    )

    prepared = prepare_messages_for_ollama(
        [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
                ],
            }
        ]
    )

    assert prepared[0]["content"] is not None
    assert extract_images_from_message(prepared[0]) == ["AAAA"]


def test_prepare_messages_for_ollama_normalizes_inline_data_to_image_url():
    """Google ADK inline_data parts must be converted to image_url for LiteLLM extraction."""
    from litellm.litellm_core_utils.prompt_templates.common_utils import (
        extract_images_from_message,
    )

    prepared = prepare_messages_for_ollama(
        [
            {
                "role": "user",
                "content": [
                    {"text": "Analyze image"},
                    {"inline_data": {"mime_type": "image/jpeg", "data": "BBBB"}},
                ],
            }
        ]
    )

    assert isinstance(prepared[0]["content"], list)
    assert extract_images_from_message(prepared[0]) == ["BBBB"]


def test_recommend_ollama_num_ctx_gemma4_26b():
    """Gemma 4 26B should get at least 16384 context size on reasonable RAM."""
    from shared.ollama_context_policy import recommend_ollama_num_ctx

    assert recommend_ollama_num_ctx("gemma4:26b", total_ram_gb=64.0) == 32768
    assert recommend_ollama_num_ctx("gemma4:26b", total_ram_gb=32.0) == 16384


def test_normalize_ollama_response_strips_prefixed_tool_name_and_marker_content():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="[TOOL_CALLS]get_current_datetime",
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(name="[TOOL_CALLS]get_current_datetime")
                        )
                    ],
                )
            )
        ]
    )

    normalize_ollama_response(response)

    message = response.choices[0].message
    assert message.content is None
    assert message.tool_calls[0].function.name == "get_current_datetime"

def test_normalize_ollama_response_rewrites_transfer_to_agent_to_explicit_tool():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="transfer_to_agent",
                                arguments='{"agent_name": "autoyou_notes_agent"}',
                            )
                        )
                    ],
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "Go to notes agent and save this"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "autoyou_notes_agent",
                "description": "Notes agent",
                "parameters": {"type": "object", "properties": {"request": {"type": "string"}}},
            },
        }
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    function = response.choices[0].message.tool_calls[0].function
    assert function.name == "autoyou_notes_agent"
    assert function.arguments == '{"request": "Go to notes agent and save this"}'

def test_normalize_ollama_response_normalizes_malformed_single_quoted_arguments():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="autoyou_coding_agent",
                                arguments="{'request': 'apply patch'}",
                            )
                        )
                    ],
                )
            )
        ]
    )

    tools = [
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
        }
    ]

    normalize_ollama_response(response, tools=tools)

    function = response.choices[0].message.tool_calls[0].function
    assert function.arguments == '{"request": "apply patch"}'

def test_normalize_ollama_response_fills_missing_request_for_explicit_agent_tool():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="autoyou_internet_agent",
                                arguments="{}",
                            )
                        )
                    ],
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "Go to internet agent only"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "autoyou_internet_agent",
                "description": "Internet agent",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        }
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    function = response.choices[0].message.tool_calls[0].function
    assert function.name == "autoyou_internet_agent"
    assert function.arguments == '{"request": "Go to internet agent only"}'


def test_normalize_ollama_response_fills_missing_request_for_mapping_tool_call():
    """LiteLLM can return dict-shaped choices, which must use the same repair path."""
    response = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_mapping_1",
                            "type": "function",
                            "function": {
                                "name": "autoyou_internet_agent",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
            }
        ]
    }
    messages = [{"role": "user", "content": "Search the synthetic status page."}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "autoyou_internet_agent",
                "description": "Internet agent",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        }
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    function = response["choices"][0]["message"]["tool_calls"][0]["function"]
    assert function["name"] == "autoyou_internet_agent"
    assert function["arguments"] == '{"request": "Search the synthetic status page."}'

def test_normalize_ollama_response_converts_bare_agent_name_text_into_tool_call():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="`autoyou_coding_agent`",
                    tool_calls=None,
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "List all scheduled tasks."}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "autoyou_coding_agent",
                "description": "Coding agent",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        }
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    message = response.choices[0].message
    # from __debug_provenance_s__ import btc
    assert message.content is None
    assert isinstance(message.tool_calls, list)
    function = message.tool_calls[0].function
    assert function.name == "autoyou_coding_agent"
    assert function.arguments == '{"request": "List all scheduled tasks."}'

def test_normalize_ollama_response_synthesizes_tool_from_hidden_routing_reasoning():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"',
                    reasoning_content=(
                        'The user is asking "How many notes do I have?". '
                        "This is a notes management request. Therefore, I should "
                        "route this request to the `autoyou_notes_agent`."
                    ),
                    tool_calls=None,
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "How many notes do I have"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "autoyou_notes_agent",
                "description": "Notes agent",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        }
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    message = response.choices[0].message
    assert message.content is None
    assert isinstance(message.tool_calls, list)
    function = message.tool_calls[0].function
    assert function.name == "autoyou_notes_agent"
    assert function.arguments == '{"request": "How many notes do I have"}'

def test_normalize_ollama_response_synthesizes_tool_from_reasoning_only_routing():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    reasoning_content=(
                        'The user is asking "How many notes do I have brooo?". '
                        "This is a request to count the number of notes in their account. "
                        "According to the system instructions, for note-related queries "
                        "(count, list, etc.), I should use the `autoyou_notes_agent`. "
                        "I will call the `autoyou_notes_agent` with the user's query."
                    ),
                    tool_calls=None,
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "How many notes do I have brooo ?"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "autoyou_notes_agent",
                "description": "Notes agent",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        }
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    message = response.choices[0].message
    assert message.content is None
    assert isinstance(message.tool_calls, list)
    function = message.tool_calls[0].function
    assert function.name == "autoyou_notes_agent"
    assert function.arguments == '{"request": "How many notes do I have brooo ?"}'

def test_normalize_ollama_response_synthesizes_tool_from_planner_json_action():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"thought": "This needs the notes specialist.", "action": "autoyou_notes_agent"}',
                    tool_calls=None,
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "What notes do I have?"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "autoyou_notes_agent",
                "description": "Notes agent",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        }
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    message = response.choices[0].message
    assert message.content is None
    assert isinstance(message.tool_calls, list)
    function = message.tool_calls[0].function
    assert function.name == "autoyou_notes_agent"
    assert function.arguments == '{"request": "What notes do I have?"}'

def test_normalize_ollama_response_does_not_synthesize_negated_routing_reasoning():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"',
                    reasoning_content=(
                        "This is a memory recall request. Use scan_entire_memory first. "
                        "Do not route memory-recall questions to autoyou_notes_agent."
                    ),
                    tool_calls=None,
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "What do you remember about me?"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "autoyou_notes_agent",
                "description": "Notes agent",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        }
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    message = response.choices[0].message
    assert not message.tool_calls

def test_normalize_ollama_response_does_not_synthesize_from_checklist_tool_reference():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    reasoning_content=(
                        'The user said "Yoooo". This is a casual greeting/exclamation. '
                        "No specific task or query is implied. "
                        "Constraint Checklist: "
                        "Call specialized tool if applicable? No specific specialized task detected. "
                        "Use `autoyou_notes_agent` for notes? The user previously asked about note count, "
                        'but now is just saying "Yoooo". I should respond conversationally.'
                    ),
                    tool_calls=None,
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "Yoooo"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "autoyou_notes_agent",
                "description": "Notes agent",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        }
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    message = response.choices[0].message
    assert message.content is None
    assert not message.tool_calls

def test_repair_missing_tool_results_inserts_placeholder_for_dangling_tool_call():
    original_messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_y0k79bmu",
                    "type": "function",
                    "function": {"name": "autoyou_internet_agent", "arguments": "{}"},
                }
            ],
        },
        {"role": "user", "content": "next turn"},
    ]

    prepared = repair_missing_tool_results(original_messages)

    assert len(prepared) == 3
    assert original_messages[1]["role"] == "user"
    assert prepared[1]["role"] == "tool"
    assert prepared[1]["tool_call_id"] == "call_y0k79bmu"
    assert "Missing tool result" in prepared[1]["content"]
    assert prepared[2]["role"] == "user"

def test_prepare_messages_for_ollama_heals_dangling_tool_call_history():
    original_messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "[TOOL_CALLS]autoyou_internet_agent"}],
            "tool_calls": [
                {
                    "id": "call_y0k79bmu",
                    "type": "function",
                    "function": {"name": "[TOOL_CALLS]autoyou_internet_agent", "arguments": "{}"},
                }
            ],
        },
        {"role": "user", "content": "next turn"},
    ]

    prepared = prepare_messages_for_ollama(original_messages)

    assert prepared[0]["content"] is None
    assert prepared[0]["tool_calls"][0]["function"]["name"] == "autoyou_internet_agent"
    assert prepared[1]["role"] == "tool"
    assert prepared[1]["tool_call_id"] == "call_y0k79bmu"
    assert prepared[2]["content"] == "next turn"

def test_prepare_messages_for_ollama_normalizes_single_quoted_tool_arguments_in_object_calls():
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(
            name="autoyou_coding_agent",
            arguments="{'request': 'fix server.py'}",
        ),
    )
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [tool_call],
        }
    ]

    prepared = prepare_messages_for_ollama(messages)

    assert prepared[0]["tool_calls"][0].function.arguments == '{"request": "fix server.py"}'

def test_prepare_tools_for_ollama_normalizes_function_names_without_mutating_input():
    original_tools = [
        {
            "type": "function",
            "function": {
                "name": "[TOOL_CALLS]autoyou_internet_agent",
                "description": "Transfer to internet agent",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    prepared_tools = prepare_tools_for_ollama(original_tools)

    assert original_tools[0]["function"]["name"] == "[TOOL_CALLS]autoyou_internet_agent"
    assert prepared_tools[0]["function"]["name"] == "autoyou_internet_agent"
    assert summarize_tool_names_for_debug(prepared_tools) == ["autoyou_internet_agent"]

def test_is_ollama_chat_model_detects_prefixed_provider_name():
    assert is_ollama_chat_model("ollama_chat/ministral-3:8b") is True
    assert is_ollama_chat_model("ollama/ministral-3:8b") is True
    assert is_ollama_chat_model("gemini/gemini-2.5-flash") is False

def test_is_ollama_chat_model_detects_configured_bare_model(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:12b")

    assert is_ollama_chat_model("gemma4:12b") is True
    assert is_ollama_chat_model("ministral-3:8b") is False

def test_normalize_ollama_response_rewrites_self_referential_tool_call():
    """Model calls autoyou_agent (root agent name) → rewrite to sub-agent via keyword hint."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="autoyou_agent",
                                arguments='{"request": "what notes do I have?"}',
                            )
                        )
                    ],
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "what notes do I have?"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "autoyou_notes_agent",
                "description": "Notes agent",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_current_datetime",
                "description": "Get current datetime",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    function = response.choices[0].message.tool_calls[0].function
    # Should be rewritten to autoyou_notes_agent (keyword hint "note")
    assert function.name == "autoyou_notes_agent"

def test_normalize_ollama_response_self_referential_fallback_to_datetime():
    """Unresolvable self-calls are stripped instead of forced onto datetime."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="autoyou_agent",
                                arguments='{}',
                            )
                        )
                    ],
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "hello there"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_current_datetime",
                "description": "Get current datetime",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    function = response.choices[0].message.tool_calls[0].function
    assert function.name is None
    assert function.arguments == "{}"

def test_normalize_ollama_response_fixes_unknown_tool_name_via_fuzzy_match(monkeypatch):
    """Model hallucinates 'notes_agent' but real tool is 'autoyou_notes_agent'.

    This exercises the plain substring-fuzzy-match fallback in
    ``_fix_unknown_tool_name`` specifically, so it must not run with
    two-stage routing active -- when it is (real specialists are registered
    the moment ``autoyou_agents.agent`` is imported, which happens ambiently
    in a full test run), a hallucinated name that matches a real specialist
    is correctly rewritten to ``route_to_specialist`` instead (see
    ``test_fix_unknown_tool_name_rewrites_direct_specialist_calls_under_two_stage``
    for that path). Forcing two-stage routing off here isolates the fallback
    path this test is actually about, regardless of ambient specialist state.
    """
    import autoyou_agents.agent as root_agent

    monkeypatch.setattr(root_agent, "_SPECIALIST_AGENT_TOOLS", {})

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="notes_agent",
                                arguments='{"request": "list notes"}',
                            )
                        )
                    ],
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "list notes"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "autoyou_notes_agent",
                "description": "Notes agent",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        },
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    function = response.choices[0].message.tool_calls[0].function
    assert function.name == "autoyou_notes_agent"

def _unmatchable_tool_call_response(name: str = "find"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(name=name, arguments='{"query": "cnn breaking news"}')
                        )
                    ],
                )
            )
        ]
    )


_INTERNET_AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "internet_search",
            "description": "Search the internet",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_datetime",
            "description": "Get current datetime",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def test_unmatchable_tool_name_is_left_for_the_adk_safety_net(monkeypatch):
    """A hallucinated `find` must not come back as the current time.

    Coercing it onto an unrelated advertised tool answers a question the model
    never asked and burns the turn. ADK's _get_tool safety net instead reports
    "that tool does not exist, here are the ones that do", which the model can
    act on, so the name has to survive this layer for it to get there.
    """
    import autoyou_agents.agent as root_agent

    monkeypatch.setattr(root_agent, "_SPECIALIST_AGENT_TOOLS", {})
    monkeypatch.setattr(root_agent, "_ADK_TOOL_SAFETY_NET_INSTALLED", True)

    response = _unmatchable_tool_call_response()
    normalize_ollama_response(
        response,
        messages=[{"role": "user", "content": "summarize the news"}],
        tools=_INTERNET_AGENT_TOOLS,
    )

    assert response.choices[0].message.tool_calls[0].function.name == "find"


def test_unmatchable_tool_name_still_falls_back_when_no_safety_net_exists(monkeypatch):
    """Without the safety net an unknown name aborts the run, so coerce it."""
    import autoyou_agents.agent as root_agent

    monkeypatch.setattr(root_agent, "_SPECIALIST_AGENT_TOOLS", {})
    monkeypatch.setattr(root_agent, "_ADK_TOOL_SAFETY_NET_INSTALLED", False)

    response = _unmatchable_tool_call_response()
    normalize_ollama_response(
        response,
        messages=[{"role": "user", "content": "summarize the news"}],
        tools=_INTERNET_AGENT_TOOLS,
    )

    assert response.choices[0].message.tool_calls[0].function.name == "get_current_datetime"


def test_normalize_ollama_response_unwraps_json_wrapped_content():
    """Model wraps response in {"role":"assistant","content":"actual text"}."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"role": "assistant", "content": "Hello. This response was JSON wrapped."}',
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == "Hello. This response was JSON wrapped."

def test_normalize_ollama_response_unwraps_single_entry_json_answer_value():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"This malformed answer key has provider noise</code>": '
                        '"This is the actual assistant answer. It should be shown as plain text."}'
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the actual assistant answer. It should be shown as plain text."
    )

def test_normalize_ollama_response_recovers_malformed_gemma4_answer_wrapper():
    """Gemma 4 can emit the plain answer as a malformed JSON object key."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"This is the recovered assistant answer. '
                        'It can answer questions and use tools. '
                        'Please send the next request.助手 我可以帮忙":"'
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the recovered assistant answer. "
        "It can answer questions and use tools. "
        "Please send the next request."
    )

def test_normalize_ollama_response_strips_provider_metadata_tail_from_wrapper():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"This is the recovered assistant answer. '
                        'It should not include provider metadata|off_type: none|off_notes: []":"'
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the recovered assistant answer. It should not include provider metadata"
    )

def test_normalize_ollama_response_prefers_reasoning_answer_for_unterminated_wrapper():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"This provider answer was truncated before completion. '
                        "It should be replaced by the marked final answer._"
                    ),
                    reasoning_content=(
                        "-->This is the final visible answer. "
                        "It can answer questions and use tools. "
                        "Please send the next request. <|channel>thought--]"
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the final visible answer. "
        "It can answer questions and use tools. "
        "Please send the next request."
    )

def test_normalize_ollama_response_strips_inline_reasoning_marker_leak():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        "This is the visible assistant answer. "
                        "It should stay clear.\u200bo|thought|---PROMPT ANALYSIS--- hidden"
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the visible assistant answer. It should stay clear."
    )

def test_normalize_ollama_response_strips_channel_pipe_thought_marker_leak():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        "This is the visible assistant answer. It should stay clear."
                        "<channel|>[thought] [system_prompt] private provider metadata"
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the visible assistant answer. It should stay clear."
    )

def test_normalize_ollama_response_strips_outof_thought_marker():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="This is the visible assistant answer. It should stay clear.outof_thought",
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the visible assistant answer. It should stay clear."
    )

def test_normalize_ollama_response_strips_bare_thought_trailer():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="This is the visible assistant answer. It should stay clear.thought}",
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the visible assistant answer. It should stay clear."
    )

def test_normalize_ollama_response_drops_cjk_reasoning_prefix():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="思考过程：用户提供了一个语音文件路径。这是内部推理。",
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content is None

def test_normalize_ollama_response_strips_english_reasoning_preamble():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        "The user is asking for my identity. "
                        "According to the system instructions, I should answer directly. "
                        "I am AutoYou, your personal AI assistant. "
                        "I can coordinate specialized tools for notes, files, and web tasks."
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "I am AutoYou, your personal AI assistant. "
        "I can coordinate specialized tools for notes, files, and web tasks."
    )

def test_normalize_ollama_response_strips_leading_answer_reasoning_sentence():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        "I am, I should answer from the current role and instructions. "
                        "The assistant can answer questions and use available tools."
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "The assistant can answer questions and use available tools."
    )

def test_normalize_ollama_response_promotes_clean_reasoning_answer_for_stub_content():
    """Gemma 4 may put the final answer in reasoning while content is only ``{"``."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"',
                    reasoning_content=(
                        'analysis text thoughtthought_step--]---**[Internal Note]**-->'
                        "This is the final visible answer. "
                        "It can answer questions and use tools. "
                        "Please send the next request. <|channel>thought--]"
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the final visible answer. "
        "It can answer questions and use tools. "
        "Please send the next request."
    )

def test_normalize_ollama_response_promotes_malformed_reasoning_trailer():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"',
                    reasoning_content=(
                        "Private analysis should stay hidden. "
                        "thought Kachun-san, This is the final visible answer. "
                        'It can handle the request now.": "null"}'
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the final visible answer. It can handle the request now."
    )

def test_normalize_ollama_response_promotes_colon_malformed_reasoning_trailer():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"',
                    reasoning_content=(
                        "Private analysis should stay hidden. "
                        "thought Kachero: This is the final visible answer. "
                        'It can handle the request now.": ""}'
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the final visible answer. It can handle the request now."
    )

def test_normalize_ollama_response_prefers_complete_reasoning_answer_over_clipped_visible_suffix():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="assistant answer. It should replace a clipped visible suffix.",
                    reasoning_content=(
                        "Private analysis should stay hidden. "
                        "thought Planner: This is the complete assistant answer. "
                        'It should replace a clipped visible suffix.": ""}'
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the complete assistant answer. It should replace a clipped visible suffix."
    )

def test_normalize_ollama_response_recovers_malformed_visible_value_fragment():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        'partial assistant fragment.": "This is the final visible answer. '
                        'It can handle the request now."}<|tool_response'
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the final visible answer. It can handle the request now."
    )

def test_normalize_ollama_response_prefers_complete_value_over_clipped_prefix():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        'visible answer.": "This is the complete visible assistant answer. '
                        'It should replace the clipped prefix."'
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the complete visible assistant answer. It should replace the clipped prefix."
    )

def test_normalize_ollama_response_drops_tool_response_artifact_text():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"thought": "" }<|tool_response>',
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content is None

def test_normalize_ollama_response_drops_visible_thought_action_json():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"thought": "The user asked '
                        "'What are you' again. I should provide a concise "
                        'answer.", "action": "none"}'
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content is None

def test_normalize_ollama_response_promotes_reasoning_answer_over_thought_action_json():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"thought": "The user asked '
                        "'What are you' again. I should answer as AutoYou."
                        '", "action": "none"}'
                    ),
                    reasoning_content=(
                        "Assistant: I am AutoYou, your personal AI assistant. "
                        "I can answer questions and route tasks to specialized agents."
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "I am AutoYou, your personal AI assistant. "
        "I can answer questions and route tasks to specialized agents."
    )

def test_normalize_ollama_response_recovers_response_from_planner_json():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"thought": "Private planning stays hidden.", '
                        '"action": "none", '
                        '"response": "I am AutoYou, your personal AI assistant. '
                        "I can answer questions and route tasks to specialized agents."
                        '"}'
                    ),
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "I am AutoYou, your personal AI assistant. "
        "I can answer questions and route tasks to specialized agents."
    )

def test_normalize_ollama_response_reads_reasoning_from_message_getter():
    class LiteLlmLikeMessage:
        content = '{"'
        tool_calls = None

        def __init__(self):
            self._fields = {
                "content": self.content,
                "tool_calls": self.tool_calls,
                "reasoning_content": (
                    "-->This is the final visible answer. "
                    "It can coordinate tools for the request."
                ),
            }

        def get(self, key, default=None):
            return self._fields.get(key, default)

    response = SimpleNamespace(choices=[SimpleNamespace(message=LiteLlmLikeMessage())])

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "This is the final visible answer. "
        "It can coordinate tools for the request."
    )

async def test_normalize_ollama_stream_response_promotes_answer_from_prior_reasoning_chunk():
    class AsyncChunks:
        def __init__(self, chunks):
            self._chunks = chunks

        async def __aiter__(self):
            for chunk in self._chunks:
                yield chunk

    reasoning_chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta={
                    "reasoning_content": (
                        "-->This is the final visible answer. "
                        "It can coordinate tools for the request."
                    )
                }
            )
        ]
    )
    stub_chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta={
                    "content": '{"',
                    "tool_calls": None,
                }
            )
        ]
    )

    normalized = []
    async for chunk in normalize_ollama_stream_response(AsyncChunks([reasoning_chunk, stub_chunk])):
        normalized.append(chunk)

    assert normalized[1].choices[0].delta["content"] == (
        "This is the final visible answer. "
        "It can coordinate tools for the request."
    )

def test_normalize_ollama_response_does_not_promote_reasoning_when_tool_calls_exist():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"',
                    reasoning_content="-->This answer should not replace a tool call.",
                    tool_calls=[
                        SimpleNamespace(function=SimpleNamespace(name="get_current_datetime", arguments="{}"))
                    ],
                )
            )
        ]
    )

    normalize_ollama_response(response, tools=[
        {"type": "function", "function": {"name": "get_current_datetime"}}
    ])

    assert response.choices[0].message.content == '{"'
    assert response.choices[0].message.tool_calls[0].function.name == "get_current_datetime"

def test_normalize_ollama_response_preserves_normal_content():
    """Normal text content should not be altered by JSON unwrapping."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Just a normal response without JSON wrapping.",
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == "Just a normal response without JSON wrapping."

def test_prepare_messages_for_ollama_unwraps_json_wrapped_assistant_content():
    """Assistant message with JSON-wrapped content in message history gets unwrapped."""
    messages = [
        {
            "role": "assistant",
            "content": '{"role": "assistant", "content": "Previous answer"}',
        },
        {"role": "user", "content": "follow up"},
    ]

    prepared = prepare_messages_for_ollama(messages)

    assert prepared[0]["content"] == "Previous answer"
    assert prepared[1]["content"] == "follow up"

# ── _safe_set_function_attr tests ──────────────────────────────────────────

def test_safe_set_function_attr_on_simple_namespace():
    """Works on plain SimpleNamespace objects."""
    obj = SimpleNamespace(name="old_name")
    result = _safe_set_function_attr(obj, "name", "new_name")
    assert result is True
    assert obj.name == "new_name"

def test_safe_set_function_attr_on_dict_backed_object():
    """Works on objects that use __dict__ for attribute storage."""
    class DictBacked:
        def __init__(self):
            self.name = "old"
    obj = DictBacked()
    result = _safe_set_function_attr(obj, "name", "new")
    assert result is True
    assert obj.name == "new"

def test_safe_set_function_attr_returns_false_on_truly_immutable():
    """Returns False when the attribute absolutely cannot be set (e.g. frozen namedtuple)."""
    from collections import namedtuple
    Frozen = namedtuple("Frozen", ["name"])
    obj = Frozen(name="immutable")
    result = _safe_set_function_attr(obj, "name", "changed")
    # namedtuple attrs cannot be set; should return False
    assert result is False
    assert obj.name == "immutable"

# ── JSON unwrapping edge cases ─────────────────────────────────────────────

def test_unwrap_json_multiline_content():
    """Handles multiline content within JSON wrapper."""
    text = '{"role": "assistant", "content": "Line 1\\nLine 2\\nLine 3"}'
    result = _unwrap_json_wrapped_content(text)
    assert result == "Line 1\nLine 2\nLine 3"

def test_unwrap_json_with_escaped_quotes():
    """Handles content containing escaped quotes."""
    text = '{"role": "assistant", "content": "He said \\"hello\\" to me"}'
    result = _unwrap_json_wrapped_content(text)
    assert result == 'He said "hello" to me'

def test_unwrap_json_content_only_no_role():
    """Model omits role key, just wraps in {"content": "..."}."""
    text = '{"content": "Just the answer"}'
    result = _unwrap_json_wrapped_content(text)
    assert result == "Just the answer"

def test_unwrap_json_response_key_passthrough():
    """{"response": "..."} is NOT unwrapped at adapter level - too dangerous.

    This pattern is handled safely by rest_api.py's defense-in-depth layer
    (with len(parsed) <= 2 check) rather than the adapter, because
    {"response": "..."} is a legitimate tool output structure.
    """
    text = '{"response": "This response key is valid tool data."}'
    result = _unwrap_json_wrapped_content(text)
    assert result == text  # Passes through unchanged

def test_unwrap_preserves_non_json_text():
    """Non-JSON text should pass through unchanged."""
    text = "This is a normal response."
    result = _unwrap_json_wrapped_content(text)
    assert result == text

# ── ADK safety net integration test ────────────────────────────────────────

def test_normalize_self_referential_with_no_arguments():
    """qwen3.5:2b-style: calls autoyou_agent with minimal/no arguments."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="autoyou_agent",
                                arguments='{"request": "hello"}',
                            )
                        )
                    ],
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "hello"}]
    tools = [
        {"type": "function", "function": {"name": "get_current_datetime", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "autoyou_notes_agent", "parameters": {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]}}},
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    fn = response.choices[0].message.tool_calls[0].function
    # Should NOT remain as autoyou_agent
    assert fn.name != "autoyou_agent"
    assert fn.name in (None, "autoyou_notes_agent")

def test_normalize_json_wrapped_response_in_response_object():
    """qwen3.5:4b-style: entire response content is JSON-wrapped."""
    wrapped = '{\n  "role" : "assistant",\n  "content": "Here are your notes: 1. Shopping list 2. TODO"\n}'
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=wrapped,
                    tool_calls=None,
                )
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == "Here are your notes: 1. Shopping list 2. TODO"

# ── Dynamic keyword hint map tests ─────────────────────────────────────────

def test_dynamic_hint_map_covers_user_built_agents():
    """User-built agents scaffolded via agent_builder_agent get keyword hints."""
    available = {
        "autoyou_notes_agent",
        "weather_agent",
        "spotify_control_agent",
        "get_current_datetime",
    }
    hint_map = _build_dynamic_keyword_hint_map(available)

    assert hint_map.get("weather") == "weather_agent"
    assert hint_map.get("spotify") == "spotify_control_agent"
    assert hint_map.get("control") == "spotify_control_agent"
    # Non-agent tools should NOT produce hints
    assert "datetime" not in hint_map

def test_self_referential_routes_to_user_built_agent():
    """Self-referential call with user-built agent keyword in user request routes correctly."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="autoyou_agent",
                                arguments='{}',
                            )
                        )
                    ],
                )
            )
        ]
    )
    messages = [{"role": "user", "content": "check the weather today"}]
    tools = [
        {"type": "function", "function": {"name": "get_current_datetime", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "weather_agent", "parameters": {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]}}},
    ]

    normalize_ollama_response(response, messages=messages, tools=tools)

    fn = response.choices[0].message.tool_calls[0].function
    assert fn.name == "weather_agent"

def test_normalize_ollama_response_strips_filler_prefixed_reasoning_preamble():
    """A leading filler word ("Okay,") must not defeat the reasoning-preamble scrubber."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        "Okay, the user is asking for my identity. "
                        "I am AutoYou, your personal AI assistant. "
                        "I can coordinate specialized tools for notes, files, and web tasks."
                    ),
                    tool_calls=None,
                ),
                finish_reason="stop",
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == (
        "I am AutoYou, your personal AI assistant. "
        "I can coordinate specialized tools for notes, files, and web tasks."
    )

def test_normalize_ollama_response_replaces_truncated_reasoning_with_cutoff_notice():
    """A response cut off mid-reasoning by a token budget must never be delivered raw."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        "Okay, the user asked \"Explain Marxism\". I need to provide a concise "
                        "explanation of Marxism. Let me recall what I know about Marxism.\n\nMar"
                    ),
                    tool_calls=None,
                ),
                finish_reason="MAX_TOKENS",
            )
        ]
    )

    normalize_ollama_response(response)

    content = response.choices[0].message.content
    assert "Marxism" not in content
    assert "cut off" in content.lower()

def test_normalize_ollama_response_preserves_complete_stop_finished_content():
    """A normal, complete finish_reason=stop response must not be touched by the new safety net."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="The current local time is 3:45 PM.",
                    tool_calls=None,
                ),
                finish_reason="stop",
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == "The current local time is 3:45 PM."

def test_normalize_ollama_response_salvages_complete_answer_before_run_on():
    """A model that answers correctly then never stops must not lose the answer.

    Observed with ministral-3:8b: "hi" produced a clean greeting followed by the
    system prompt read back, until num_predict ended the turn mid-word.
    """
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        "Hey! How can I help you today? "
                        "Never send a progress-only placeholder as your final answ"
                    ),
                    tool_calls=None,
                ),
                finish_reason="length",
            )
        ]
    )

    normalize_ollama_response(response)

    assert response.choices[0].message.content == "Hey! How can I help you today?"

def test_normalize_ollama_response_does_not_salvage_a_reasoning_fragment():
    """Upstream strippers can leave a mid-thought fragment that reads like prose."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Let me recall what I know about Marxism. Marxism is a the",
                    tool_calls=None,
                ),
                finish_reason="length",
            )
        ]
    )

    normalize_ollama_response(response)

    content = response.choices[0].message.content
    assert "Marxism" not in content
    assert "cut off" in content.lower()

def test_normalize_ollama_response_requires_a_complete_sentence_to_salvage():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Sure, here is the beginning of a thought that never finishes and",
                    tool_calls=None,
                ),
                finish_reason="length",
            )
        ]
    )

    normalize_ollama_response(response)

    assert "cut off" in response.choices[0].message.content.lower()

# Captured verbatim from a Cloud-Pair reply where ministral-3:14b collapsed
# while 33 agent tools were advertised. finish_reason was "stop" and no tool
# call was emitted, so every pre-existing safety net let this through.
_OBSERVED_TOOL_SCHEMA_COLLAPSE = (
    '- what is a search for the":"andsearching for the":"function (": " +"".\n\n'
    '###: {"description of the":"automnamerican be used to get":"parameters for youtog":'
    '"requests. YouTube":"and":"function(?:","required":"function autocomplete=":'
    '{"name":"autocomplete the":"search and search":"andfinding" and query":"sorting":'
    '"specials";,":"functions as a search":"requested":"for":"replacing":"functionality '
    'checkerboard to website":"parameters for the":"required":"request youtow", '
    '"+120000000000000000000000000000000000000'
)

_AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "autoyou_notes_agent",
            "description": "Handles note taking requests.",
            "parameters": {
                "type": "object",
                "properties": {"request": {"type": "string"}},
                "required": ["request"],
            },
        },
    }
]

def test_detects_observed_tool_schema_collapse():
    assert looks_like_degenerate_tool_schema_echo(
        _OBSERVED_TOOL_SCHEMA_COLLAPSE,
        advertised_tool_names={"autoyou_notes_agent"},
    )

def test_detects_degenerate_character_run():
    assert looks_like_degenerate_tool_schema_echo(
        "Sure, the total comes to +1200000000000000000000000000000000000000"
    )

def test_keeps_legitimate_fenced_json_answer():
    """A model legitimately asked to show a schema answers inside a fence."""
    answer = (
        "Sure, here is the schema you asked for:\n"
        "```json\n"
        '{"name":"x","description":"y","parameters":{"type":"object","required":["a"]}}\n'
        "```\n"
        "Let me know if you want changes."
    )
    assert not looks_like_degenerate_tool_schema_echo(answer)

def test_keeps_normal_reply_that_mentions_a_tool_name():
    assert not looks_like_degenerate_tool_schema_echo(
        "I used the autoyou_notes_agent to save that for you. It is stored now.",
        advertised_tool_names={"autoyou_notes_agent"},
    )

def test_normalize_ollama_response_replaces_tool_schema_collapse():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=_OBSERVED_TOOL_SCHEMA_COLLAPSE,
                    tool_calls=None,
                ),
                finish_reason="stop",
            )
        ]
    )

    normalize_ollama_response(response, tools=_AGENT_TOOLS)

    content = response.choices[0].message.content
    assert "checkerboard" not in content
    assert "unusable response" in content

def test_collapse_notice_wins_over_cutoff_notice_when_also_truncated():
    """A collapse that also exhausts the budget is still a collapse.

    A model echoing schema does not stop on its own, so finish_reason is almost
    always "length" here. The truncation branch used to overwrite the content
    first and leave the collapse check inspecting the cut-off notice, which told
    the user to ask a shorter question instead of to change model.
    """
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=_OBSERVED_TOOL_SCHEMA_COLLAPSE,
                    tool_calls=None,
                ),
                finish_reason="length",
            )
        ]
    )

    normalize_ollama_response(response, tools=_AGENT_TOOLS)

    content = response.choices[0].message.content
    assert "unusable response" in content
    assert "cut off" not in content.lower()

def test_degeneration_check_ignores_responses_that_carry_a_tool_call():
    """A real tool call means the turn is fine; never retry those."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=_OBSERVED_TOOL_SCHEMA_COLLAPSE,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            type="function",
                            function=SimpleNamespace(
                                name="autoyou_notes_agent",
                                arguments='{"request": "save a note"}',
                            ),
                        )
                    ],
                ),
                finish_reason="stop",
            )
        ]
    )

    assert not response_degenerated_into_tool_schema_echo(response, tools=_AGENT_TOOLS)

def test_degeneration_check_flags_collapse_without_tool_calls():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=_OBSERVED_TOOL_SCHEMA_COLLAPSE,
                    tool_calls=None,
                ),
                finish_reason="stop",
            )
        ]
    )

    assert response_degenerated_into_tool_schema_echo(response, tools=_AGENT_TOOLS)

def test_latest_user_request_reads_multipart_content():
    """Media-bearing turns arrive as parts; the text still has to be found."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "describe this photo"}]},
    ]
    assert _extract_latest_user_request(messages) == "describe this photo"


def test_latest_user_request_ignores_scheduler_execution_guidance():
    messages = [
        {
            "role": "user",
            "content": (
                "Find current public updates online.\n\n"
                "Recurring task execution rules:\n"
                "- Produce a fresh result for each run.\n"
                "Never present remembered facts as newly verified."
            ),
        },
    ]

    assert _extract_latest_user_request(messages) == "Find current public updates online."


def test_agent_tool_call_always_gets_a_request_argument():
    """ADK's AgentTool reads args['request'] unconditionally - it must exist.

    Without a user message to borrow from, the key still has to be present or
    the whole root agent dies with KeyError for the turn.
    """
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            type="function",
                            function=SimpleNamespace(
                                name="autoyou_notes_agent",
                                arguments="{}",
                            ),
                        )
                    ],
                ),
                finish_reason="stop",
            )
        ]
    )

    normalize_ollama_response(response, messages=[], tools=_AGENT_TOOLS)

    arguments = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
    assert arguments["request"].strip()


def test_fix_unknown_tool_name_rewrites_direct_specialist_calls_under_two_stage(monkeypatch):
    """Direct specialist calls should be rewritten to route_to_specialist when not advertised."""
    import autoyou_agents.agent as root_agent
    from autoyou_agents.litellm_ollama_adapter import normalize_ollama_response

    # Mock specialist tool and active two stage router
    monkeypatch.setattr(root_agent, "_SPECIALIST_AGENT_TOOLS", {
        "autoyou_notes_agent": object(),
    })
    monkeypatch.setenv("AUTOYOU_TWO_STAGE_ROUTER", "1")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="autoyou_notes_agent",
                                arguments='{"request": "list my notes"}',
                            )
                        )
                    ],
                ),
                finish_reason="stop",
            )
        ]
    )

    normalize_ollama_response(response, tools=[{"type": "function", "function": {"name": "route_to_specialist"}}])

    fn = response.choices[0].message.tool_calls[0].function
    assert fn.name == "route_to_specialist"
    args = json.loads(fn.arguments)
    assert args == {"agent": "autoyou_notes_agent", "request": "list my notes"}


def test_fix_unknown_tool_name_normalizes_generic_web_aliases():
    function = SimpleNamespace(
        name="browser.open",
        arguments='{"url": "https://source-a.example/updates"}',
    )

    changed = _fix_unknown_tool_name(
        function,
        advertised_tool_names={"internet_search", "scrape_website", "navigate_page"},
        latest_user_request="Read the current public update.",
    )

    assert changed is True
    assert function.name == "scrape_website"
    assert json.loads(function.arguments) == {"url": "https://source-a.example/updates"}


def test_fix_unknown_tool_name_normalizes_web_run_search_payload():
    function = SimpleNamespace(
        name="web.run",
        arguments=json.dumps({"search_query": [{"q": "current public updates"}]}),
    )

    changed = _fix_unknown_tool_name(
        function,
        advertised_tool_names={"internet_search", "scrape_website", "navigate_page"},
        latest_user_request="Read the current public update.",
    )

    assert changed is True
    assert function.name == "internet_search"
    assert json.loads(function.arguments) == {"search_query": [{"q": "current public updates"}], "query": "current public updates"}


def test_fix_unknown_tool_name_normalizes_url_less_web_open_to_search():
    function = SimpleNamespace(name="web.open", arguments="{}")

    changed = _fix_unknown_tool_name(
        function,
        advertised_tool_names={"internet_search", "scrape_website", "navigate_page"},
        latest_user_request="Read the current public update.",
    )

    assert changed is True
    assert function.name == "internet_search"
    assert json.loads(function.arguments) == {"query": "Read the current public update."}


def test_fix_unknown_tool_name_normalizes_live_find_alias_to_search():
    function = SimpleNamespace(name="find", arguments="{}")

    changed = _fix_unknown_tool_name(
        function,
        advertised_tool_names={"internet_search", "scrape_website", "navigate_page"},
        latest_user_request="Find the latest public updates online.",
    )

    assert changed is True
    assert function.name == "internet_search"
    assert json.loads(function.arguments) == {"query": "Find the latest public updates online."}
