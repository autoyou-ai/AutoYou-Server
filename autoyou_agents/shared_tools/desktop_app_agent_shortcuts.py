# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Deterministic one-tool shortcuts for weak desktop bridge models."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Tuple

from shared.session_execution import create_tool_call_llm_response


_EXACT_TOOL_RE = re.compile(r"\b(?:call|use)\s+(?:exactly\s+one|only\s+one|only)\s+tool\b", re.IGNORECASE)
_QUOTED_RE = re.compile(r"['\"]([^'\"]+)['\"]")


def _extract_request_text(llm_request: Any) -> str:
    texts: list[str] = []
    for content in getattr(llm_request, "contents", []) or []:
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    return "\n".join(texts)


def _number_arg(text: str, name: str, *, as_int: bool = False) -> Optional[int | float]:
    pattern = re.compile(rf"\b{re.escape(name)}\b\s*(?:=|:)?\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return None
    value = float(match.group(1))
    return int(value) if as_int else value


def _string_arg(text: str, name: str) -> Optional[str]:
    quoted = re.search(rf"\b{re.escape(name)}\b\s*(?:=|:)?\s*(['\"])(.*?)\1", text, re.IGNORECASE | re.DOTALL)
    if quoted:
        return quoted.group(2).strip()
    pattern = re.compile(rf"\b{re.escape(name)}\b\s*(?:=|:)?\s*([^\n,.;]+)", re.IGNORECASE)
    match = pattern.search(text)
    return match.group(1).strip(" `") if match else None


def _bool_arg(text: str, name: str) -> Optional[bool]:
    pattern = re.compile(rf"\b{re.escape(name)}\b\s*(?:=|:)?\s*(true|false)", re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).lower() == "true"


def _prompt_arg(text: str) -> Optional[str]:
    for name in ("prompt", "text"):
        value = _string_arg(text, name)
        if value:
            return value
    return None


def _attachment_paths_arg(text: str) -> list[str]:
    marker = re.search(r"\battachment_paths\b\s*(?:=|:)?\s*\[([^\]]*)\]", text, flags=re.IGNORECASE | re.DOTALL)
    if marker:
        return [match.group(1) for match in _QUOTED_RE.finditer(marker.group(1))]
    value = _string_arg(text, "attachment_path")
    return [value] if value else []


def _add_common_launch_args(text: str, args: Dict[str, Any], names: Iterable[str]) -> None:
    for key in names:
        value = _bool_arg(text, key)
        if value is not None:
            args[key] = value


_SEND_PROMPT_INTENT_RE = re.compile(
    r"^(?:send|execute|submit|run)\s+(?:the\s+)?(?:current\s+|drafted\s+)?prompt(?:\s+in\s+[\w\s_]+agent)?[\s.!\?]*$",
    re.IGNORECASE,
)
_SEND_DRAFT_INTENT_RE = re.compile(
    r"^(?:send|submit|execute)\s+(?:draft|current\s+prompt)[\s.!\?]*$",
    re.IGNORECASE,
)
_ASK_DESKTOP_PREFIX_RE = re.compile(
    r"^(?:send|execute|submit)\s+prompt\s*(?:to\s+[\w\s_]+)?[\:\s]+(.+)$|"
    r"^(?:ask|tell)\s+(?:claude|codex)(?:\s+desktop)?[\:\s]+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_STATUS_INTENT_RE = re.compile(
    r"^(?:get\s+|check\s+)?status(?:[\s_]+check)?[\s.!\?]*$",
    re.IGNORECASE,
)
_USAGE_INTENT_RE = re.compile(
    r"^(?:get\s+|check\s+)?usage(?:[\s_]+check)?[\s.!\?]*$",
    re.IGNORECASE,
)


def desktop_exact_tool_call_from_text(
    text: str,
    tool_names: Iterable[str],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return a deterministic tool call when the request explicitly names one tool or expresses a clear desktop action intent."""
    cleaned = str(text or "").strip()
    available = set(tool_names)

    if not _EXACT_TOOL_RE.search(cleaned):
        if _SEND_PROMPT_INTENT_RE.search(cleaned) or _SEND_DRAFT_INTENT_RE.search(cleaned):
            # "send the prompt" means submit the draft and finish. The ask_* tools also submit,
            # but then schedule an async return task that polls the app until it goes idle,
            # re-focusing its window on every poll - minutes of the pointer and foreground being
            # taken over for a request that only asked to press send. Reserve those for "ask".
            for candidate in (
                "send_current_claude_desktop_prompt",
                "send_current_codex_desktop_prompt",
                "ask_claude_and_return",
                "ask_codex_and_return",
            ):
                if candidate in available:
                    return candidate, {}

        ask_prefix_match = _ASK_DESKTOP_PREFIX_RE.search(cleaned)
        if ask_prefix_match:
            # Group 1 is "send/execute/submit prompt: X"; group 2 is "ask/tell claude: X".
            # These are different intents and must not collapse to the same tool. "send prompt"
            # means submit it and be done, so it maps to the one-shot send. "ask" means the
            # caller wants the answer back, which costs an async return task that polls the app
            # until it goes idle - re-focusing the window every poll. Preferring the ask tool for
            # both meant a plain "send prompt: X" silently signed up for minutes of polling and
            # repeated window activation.
            send_only_text = (ask_prefix_match.group(1) or "").strip()
            answer_wanted_text = (ask_prefix_match.group(2) or "").strip()
            prompt_text = send_only_text or answer_wanted_text
            if prompt_text:
                if send_only_text:
                    candidates = (
                        "send_prompt_to_claude_desktop",
                        "send_prompt_to_codex_desktop",
                        "ask_claude_and_return",
                        "ask_codex_and_return",
                    )
                else:
                    candidates = (
                        "ask_claude_and_return",
                        "ask_codex_and_return",
                        "send_prompt_to_claude_desktop",
                        "send_prompt_to_codex_desktop",
                    )
                for candidate in candidates:
                    if candidate in available:
                        return candidate, {"prompt": prompt_text}

        if _STATUS_INTENT_RE.search(cleaned):
            for candidate in ("get_claude_desktop_status", "get_codex_desktop_status"):
                if candidate in available:
                    return candidate, {}

        if _USAGE_INTENT_RE.search(cleaned):
            for candidate in ("get_claude_usage", "get_codex_usage"):
                if candidate in available:
                    return candidate, {}

        return None

    # Prefer the most specific name when one tool name is a prefix of another
    # (for example ``get_*_desktop_prompt_status`` vs ``get_*_desktop_prompt``).
    matching_names = [name for name in available if name in cleaned]
    tool_name = max(matching_names, key=len, default="")
    if not tool_name:
        return None

    args: Dict[str, Any] = {}
    if "release_status" in tool_name:
        platform_tag = _string_arg(cleaned, "platform_tag")
        screenshot_limit = _number_arg(cleaned, "screenshot_limit", as_int=True)
        if platform_tag:
            args["platform_tag"] = platform_tag
        if screenshot_limit is not None:
            args["screenshot_limit"] = screenshot_limit
    elif tool_name.startswith("get_") and tool_name.endswith("_desktop_status"):
        _add_common_launch_args(cleaned, args, ("capture_screenshot",))
    elif tool_name.startswith("get_") and tool_name.endswith("_desktop_prompt"):
        _add_common_launch_args(cleaned, args, ("launch_if_needed",))
    elif tool_name.startswith("get_") and tool_name.endswith("_desktop_prompt_status"):
        _add_common_launch_args(cleaned, args, ("launch_if_needed",))
    elif tool_name.startswith("list_") and tool_name.endswith("_desktop_asset_packs"):
        platform_tag = _string_arg(cleaned, "platform_tag")
        if platform_tag:
            args["platform_tag"] = platform_tag
    elif tool_name.startswith("select_") and tool_name.endswith("_desktop_model"):
        for key in ("model", "effort", "speed", "advanced"):
            value = _string_arg(cleaned, key)
            if value:
                args[key] = value
        _add_common_launch_args(cleaned, args, ("launch_if_needed",))
    elif tool_name.startswith("select_") and tool_name.endswith("_desktop_project"):
        value = _string_arg(cleaned, "project_name") or _string_arg(cleaned, "project")
        if value:
            args["project_name"] = value
        _add_common_launch_args(cleaned, args, ("launch_if_needed",))
    elif tool_name.startswith("select_") and tool_name.endswith("_desktop_permissions"):
        value = _string_arg(cleaned, "permissions")
        if value:
            args["permissions"] = value
        _add_common_launch_args(cleaned, args, ("launch_if_needed",))
    elif tool_name.startswith(("add_to_", "replace_")) and tool_name.endswith("_desktop_prompt"):
        prompt = _prompt_arg(cleaned)
        if prompt:
            args["prompt"] = prompt
        working_directory = _string_arg(cleaned, "working_directory")
        if working_directory:
            args["working_directory"] = working_directory
        paths = _attachment_paths_arg(cleaned)
        if paths:
            args["attachment_paths"] = paths
        launch_args = ("launch_if_needed", "prepend_newline") if tool_name.startswith("add_to_") else ("launch_if_needed",)
        _add_common_launch_args(cleaned, args, launch_args)
    elif tool_name.startswith("add_") and tool_name.endswith("_desktop_attachments"):
        paths = _attachment_paths_arg(cleaned)
        if paths:
            args["attachment_paths"] = paths
        _add_common_launch_args(cleaned, args, ("launch_if_needed",))
    elif tool_name.startswith(("send_current_", "queue_")) and tool_name.endswith("_desktop_prompt"):
        _add_common_launch_args(cleaned, args, ("launch_if_needed", "capture_after"))
    elif tool_name.startswith("copy_") and tool_name.endswith("_desktop_final_response"):
        prompt = _prompt_arg(cleaned)
        if prompt:
            args["expected_prompt"] = prompt
        _add_common_launch_args(cleaned, args, ("launch_if_needed",))
    elif tool_name.startswith("wait_for_"):
        prompt = _prompt_arg(cleaned)
        if prompt:
            args["expected_prompt"] = prompt
        for key, as_int in (
            ("poll_interval_seconds", False),
            ("stable_polls", True),
            ("max_wait_seconds", False),
            ("initial_wait_seconds", False),
        ):
            value = _number_arg(cleaned, key, as_int=as_int)
            if value is not None:
                args[key] = value
    elif tool_name.startswith("send_prompt_to_") or tool_name in ("ask_claude_and_return", "ask_codex_and_return"):
        prompt = _prompt_arg(cleaned)
        if prompt:
            args["prompt"] = prompt
        paths = _attachment_paths_arg(cleaned)
        if paths:
            args["attachment_paths"] = paths
        initial_delay = _number_arg(cleaned, "initial_delay_seconds")
        if initial_delay is not None:
            args["initial_delay_seconds"] = initial_delay
        for key in ("launch_if_needed", "capture_before_submit", "capture_after_submit"):
            value = _bool_arg(cleaned, key)
            if value is not None:
                args[key] = value
    elif tool_name.startswith("find_") and tool_name.endswith("_desktop_screenshot_attachments"):
        directory = _string_arg(cleaned, "directory")
        limit = _number_arg(cleaned, "limit", as_int=True)
        if directory:
            args["directory"] = directory
        if limit is not None:
            args["limit"] = limit
    elif tool_name.startswith("capture_") and tool_name.endswith("_desktop_screenshot"):
        label = _string_arg(cleaned, "label")
        if label:
            args["label"] = label

    return tool_name, args


_SHORTCUT_INVOCATION_STATE_KEY = "_autoyou_desktop_shortcut_invocation_id"


def _shortcut_state_get(state: Any, key: str) -> str:
    try:
        if hasattr(state, "get"):
            return str(state.get(key, "") or "").strip()
        return str(getattr(state, key, "") or "").strip()
    except Exception:
        return ""


def _shortcut_state_set(state: Any, key: str, value: str) -> None:
    try:
        if hasattr(state, "__setitem__"):
            state[key] = value
        else:
            setattr(state, key, value)
    except Exception:
        pass


def make_desktop_exact_tool_callback(agent_name: str, tool_names: Iterable[str]):
    names = tuple(str(name).strip() for name in tool_names if str(name).strip())

    async def _desktop_exact_tool_callback(callback_context: Any, llm_request: Any) -> Any:
        # Fire at most once per invocation.
        #
        # _extract_request_text() flattens the WHOLE conversation, so the phrase that triggered
        # the shortcut is still present on every later turn of the same invocation. Without this
        # guard the callback re-injected the same tool call each time the model was consulted:
        # send -> tool result -> model -> send again. That both duplicated the prompt in the
        # desktop app and prevented the model from ever producing a final answer, so the loop
        # continued until some tool-call budget ran out.
        invocation_id = str(getattr(callback_context, "invocation_id", "") or "").strip()
        state = getattr(callback_context, "state", None)
        if invocation_id and state is not None:
            if _shortcut_state_get(state, _SHORTCUT_INVOCATION_STATE_KEY) == invocation_id:
                # Already handled this turn - let the model speak instead of re-dispatching.
                return None

        call = desktop_exact_tool_call_from_text(_extract_request_text(llm_request), names)
        if call is None:
            return None
        if invocation_id and state is not None:
            _shortcut_state_set(state, _SHORTCUT_INVOCATION_STATE_KEY, invocation_id)
        tool_name, args = call
        return create_tool_call_llm_response(
            tool_name,
            args,
            custom_metadata={
                "response_author": agent_name,
                "agent_name": agent_name,
                "route_reason": "deterministic_desktop_exact_tool",
            },
        )

    return _desktop_exact_tool_callback
