# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-c013053b06fc45e318a35111


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
from rest_api import (
    _payload_visible_agent_response_text,
    _select_agent_response_payload,
    _should_retry_empty_root_model_response,
)

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-c013053b06fc45e318a35111"


def test_select_agent_response_prefers_last_text_payload_over_terminal_non_text_event():
    text_payload = {
        "content": {
            "parts": [
                {"text": 'The note "Website Bug" has been created successfully.'},
            ]
        },
        "author": "autoyou_notes_agent",
        "id": "message-event",
    }
    terminal_payload = {
        "author": "autoyou_notes_agent",
        "id": "terminal-event",
        "invocationId": "inv-123",
        "partial": False,
    }

    selected = _select_agent_response_payload(
        last_payload=terminal_payload,
        last_text_payload=text_payload,
        last_error_payload=None,
    )

    assert _payload_visible_agent_response_text(selected) == (
        'The note "Website Bug" has been created successfully.'
    )
    assert selected["id"] == "terminal-event"
    assert selected["invocationId"] == "inv-123"


def test_select_agent_response_prefers_error_payload_when_stream_ends_without_text():
    error_payload = {
        "author": "autoyou_notes_agent",
        "errorMessage": "Tool execution failed",
    }
    terminal_payload = {
        "author": "autoyou_notes_agent",
        "invocationId": "inv-456",
        "partial": False,
    }

    selected = _select_agent_response_payload(
        last_payload=terminal_payload,
        last_text_payload=None,
        last_error_payload=error_payload,
    )

    assert selected["errorMessage"] == "Tool execution failed"
    assert selected["invocationId"] == "inv-456"


def test_select_agent_response_falls_back_to_last_payload_when_no_text_or_error_exists():
    terminal_payload = {
        "author": "autoyou_notes_agent",
        "invocationId": "inv-789",
        "partial": False,
    }

    selected = _select_agent_response_payload(
        last_payload=terminal_payload,
        last_text_payload=None,
        last_error_payload=None,
    )

    assert selected == terminal_payload


def test_should_retry_empty_root_model_response_for_no_tool_root_stub():
    payload = {
        "author": "autoyou_agent",
        "content": {"parts": [{"text": '{"'}]},
    }

    assert _should_retry_empty_root_model_response(payload, None) is True


def test_should_not_retry_empty_response_after_tool_activity():
    payload = {
        "author": "autoyou_agent",
        "content": {
            "parts": [
                {
                    "functionResponse": {
                        "name": "autoyou_page_agent",
                        "response": {"message": "Added item."},
                    }
                }
            ]
        },
    }

    assert _should_retry_empty_root_model_response(payload, None) is False


def test_payload_visible_agent_response_text_uses_tool_response_message():
    tool_only_payload = {
        "author": "autoyou_files_agent",
        "content": {
            "parts": [
                {
                    "functionResponse": {
                        "name": "rename_path",
                        "response": {
                            "status": "success",
                            "message": "Renamed /tmp/song.mp3 to /tmp/song-final.mp3.",
                        },
                    }
                }
            ]
        },
    }

    assert _payload_visible_agent_response_text(tool_only_payload) == (
        "Renamed /tmp/song.mp3 to /tmp/song-final.mp3."
    )


def test_payload_visible_agent_response_text_ignores_malformed_json_stub():
    gemma4_payload = {
        "author": "autoyou_agent",
        "content": {
            "parts": [
                {
                    "text": (
                        "Internal reasoning should not be surfaced. "
                        'prompt: hello": "This draft answer should stay hidden."}'
                    ),
                    "thought": True,
                },
                {"text": '{"'},
            ]
        },
    }

    assert _payload_visible_agent_response_text(gemma4_payload) is None


def test_payload_visible_agent_response_text_recovers_safe_answer_from_thought_when_visible_stub():
    gemma4_payload = {
        "author": "autoyou_agent",
        "content": {
            "parts": [
                {
                    "text": (
                        "private reasoning thoughtthought_step--]---**[Internal Note]**-->"
                        "This is the final visible answer. "
                        "It can answer the request and coordinate tools. "
                        "Please send the next request. <|channel>thought--]"
                    ),
                    "thought": True,
                },
                {"text": '{"'},
            ]
        },
    }

    assert _payload_visible_agent_response_text(gemma4_payload) == (
        "This is the final visible answer. "
        "It can answer the request and coordinate tools. "
        "Please send the next request."
    )


def test_payload_visible_agent_response_text_ignores_visible_thought_action_json():
    gemma4_payload = {
        "author": "autoyou_agent",
        "content": {
            "parts": [
                {
                    "text": (
                        'The user asked "What are you" again. I should provide '
                        "a concise answer as AutoYou."
                    ),
                    "thought": True,
                },
                {
                    "text": (
                        '{"thought": "The user asked '
                        "'What are you' again. I should provide a concise "
                        'answer.", "action": "none"}'
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(gemma4_payload) is None
    assert _should_retry_empty_root_model_response(gemma4_payload, None) is True


def test_payload_visible_agent_response_text_recovers_answer_when_planner_json_has_response():
    gemma4_payload = {
        "author": "autoyou_agent",
        "content": {
            "parts": [
                {
                    "text": (
                        '{"thought": "Private planning stays hidden.", '
                        '"action": "none", '
                        '"response": "I am AutoYou, your personal AI assistant. '
                        "I can answer questions and route tasks to specialized agents."
                        '"}'
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(gemma4_payload) == (
        "I am AutoYou, your personal AI assistant. "
        "I can answer questions and route tasks to specialized agents."
    )


def test_payload_visible_agent_response_text_recovers_malformed_reasoning_trailer():
    gemma4_payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        "Private analysis should stay hidden. "
                        "thought Kachun-san, This is the final visible answer. "
                        'It can handle the request now.": "null"}'
                    ),
                    "thought": True,
                },
                {"text": '{"'},
            ]
        },
    }

    assert _payload_visible_agent_response_text(gemma4_payload) == (
        "This is the final visible answer. It can handle the request now."
    )


def test_payload_visible_agent_response_text_recovers_colon_malformed_reasoning_trailer():
    gemma4_payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        "Private analysis should stay hidden. "
                        "thought Kachero: This is the final visible answer. "
                        'It can handle the request now.": ""}'
                    ),
                    "thought": True,
                },
                {"text": '{"'},
            ]
        },
    }

    assert _payload_visible_agent_response_text(gemma4_payload) == (
        "This is the final visible answer. It can handle the request now."
    )


def test_payload_visible_agent_response_text_prefers_complete_thought_answer_over_clipped_visible_suffix():
    gemma4_payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        "Private analysis should stay hidden. "
                        "thought Planner: This is the complete assistant answer. "
                        'It should replace a clipped visible suffix.": ""}'
                    ),
                    "thought": True,
                },
                {"text": "assistant answer. It should replace a clipped visible suffix."},
            ]
        },
    }

    assert _payload_visible_agent_response_text(gemma4_payload) == (
        "This is the complete assistant answer. It should replace a clipped visible suffix."
    )


def test_payload_visible_agent_response_text_recovers_malformed_visible_value_fragment():
    payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        'partial assistant fragment.": "This is the final visible answer. '
                        'It can handle the request now."}<|tool_response'
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == (
        "This is the final visible answer. It can handle the request now."
    )


def test_payload_visible_agent_response_text_prefers_complete_value_over_clipped_prefix():
    payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        'visible answer.": "This is the complete visible assistant answer. '
                        'It should replace the clipped prefix."'
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == (
        "This is the complete visible assistant answer. It should replace the clipped prefix."
    )


def test_payload_visible_agent_response_text_ignores_tool_response_artifact():
    payload = {
        "content": {
            "parts": [
                {"text": '{"thought": "" }<|tool_response>'},
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) is None


def test_payload_visible_agent_response_text_keeps_complete_json_text():
    payload = {
        "content": {
            "parts": [
                {"text": '{"status":"ok"}'},
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == '{"status":"ok"}'


def test_payload_visible_agent_response_text_unwraps_single_entry_json_answer_value():
    payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        '{"This malformed answer key has provider noise</code>": '
                        '"This is the actual assistant answer. It should be shown as plain text."}'
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == (
        "This is the actual assistant answer. It should be shown as plain text."
    )


def test_payload_visible_agent_response_text_recovers_malformed_gemma4_answer_wrapper():
    payload = {
        "author": "autoyou_agent",
        "content": {
            "parts": [
                {
                    "text": (
                        '{"This is the recovered assistant answer. '
                        'It can help answer questions and use tools. '
                        'Please send the next request.助手 我可以帮忙":"'
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == (
        "This is the recovered assistant answer. "
        "It can help answer questions and use tools. "
        "Please send the next request."
    )


def test_payload_visible_agent_response_text_strips_provider_metadata_tail_from_wrapper():
    payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        '{"This is the recovered assistant answer. '
                        'It should not include provider metadata|off_type: none|off_notes: []":"'
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == (
        "This is the recovered assistant answer. It should not include provider metadata"
    )


def test_payload_visible_agent_response_text_recovers_unterminated_answer_wrapper():
    payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        '{"This is the recovered assistant answer. '
                        "It can coordinate tools for the request._"
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == (
        "This is the recovered assistant answer. "
        "It can coordinate tools for the request."
    )


def test_payload_visible_agent_response_text_strips_inline_reasoning_marker_leak():
    payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        "This is the visible assistant answer. "
                        "It should stay clear.\u200bo|thought|---PROMPT ANALYSIS--- hidden"
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == (
        "This is the visible assistant answer. It should stay clear."
    )


def test_payload_visible_agent_response_text_strips_channel_pipe_thought_marker_leak():
    payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        "This is the visible assistant answer. It should stay clear."
                        "<channel|>[thought] [system_prompt] private provider metadata"
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == (
        "This is the visible assistant answer. It should stay clear."
    )


def test_payload_visible_agent_response_text_strips_outof_thought_marker():
    payload = {
        "content": {
            "parts": [
                {"text": "This is the visible assistant answer. It should stay clear.outof_thought"},
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == (
        "This is the visible assistant answer. It should stay clear."
    )


def test_payload_visible_agent_response_text_strips_bare_thought_trailer():
    payload = {
        "content": {
            "parts": [
                {"text": "This is the visible assistant answer. It should stay clear.thought}"},
            ]
        },
    }
    # from __debug_provenance_v__ import wallet

    assert _payload_visible_agent_response_text(payload) == (
        "This is the visible assistant answer. It should stay clear."
    )


def test_payload_visible_agent_response_text_drops_cjk_reasoning_prefix():
    payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        "思考过程：用户提供了一个语音文件路径。"
                        "这段内容是模型内部推理，不应返回给用户。"
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) is None


def test_payload_visible_agent_response_text_strips_english_reasoning_preamble():
    payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        "The user is asking for my identity. "
                        "According to the system instructions, I should answer directly. "
                        "I am AutoYou, your personal AI assistant. "
                        "I can coordinate specialized tools for notes, files, and web tasks."
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == (
        "I am AutoYou, your personal AI assistant. "
        "I can coordinate specialized tools for notes, files, and web tasks."
    )


def test_payload_visible_agent_response_text_strips_leading_answer_reasoning_sentence():
    payload = {
        "content": {
            "parts": [
                {
                    "text": (
                        "I am, I should answer from the current role and instructions. "
                        "The assistant can answer questions and use available tools."
                    )
                },
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == (
        "The assistant can answer questions and use available tools."
    )


def test_payload_visible_agent_response_text_hides_thought_parts_by_default():
    payload = {
        "content": {
            "parts": [
                {"text": "Let me consider the best way to phrase this.", "thought": True},
                {"text": "Here is the final answer to your question."},
            ]
        },
    }

    assert _payload_visible_agent_response_text(payload) == "Here is the final answer to your question."


def test_payload_visible_agent_response_text_labels_thought_parts_when_show_thinking_enabled():
    import server

    original_config = server.STATE.config
    server.STATE.config = {"model_behavior": {"show_thinking": True}}
    try:
        payload = {
            "content": {
                "parts": [
                    {"text": "Let me consider the best way to phrase this.", "thought": True},
                    {"text": "Here is the final answer to your question."},
                ]
            },
        }

        result = _payload_visible_agent_response_text(payload)
        assert "[Thinking]" in result
        assert "Let me consider the best way to phrase this." in result
        assert "Here is the final answer to your question." in result
    finally:
        server.STATE.config = original_config
