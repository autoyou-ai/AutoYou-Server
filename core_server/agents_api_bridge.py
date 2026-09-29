# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Route selected AutoYou chat turns through the OpenAI Agents API.

The Agents API session is a short-lived router. The original message and any
attachments stay with the AutoYou server, whose configured provider produces
the reply shown to the user.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TypeVar

import httpx


T = TypeVar("T")

AGENTS_API_BASE_URL = "https://api.openai.com/v1"
AGENTS_API_BETA_HEADER = "agents=v1"
AUTYOU_CHAT_TOOL = "send_to_autoyou"


class AgentsAPIError(RuntimeError):
    """A request could not complete through the Agents API."""


class AgentsAPIConfigurationError(AgentsAPIError):
    """The AutoYou instance has no Agents API credential configured."""


def _get_api_key() -> str:
    return str(
        os.environ.get("AUTOYOU_AGENTS_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()


def _get_model() -> str:
    return str(os.environ.get("AUTOYOU_AGENTS_API_MODEL") or "gpt-6-astra").strip() or "gpt-6-astra"


def _event_parts(event: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    payload = event.get("data") if isinstance(event.get("data"), dict) else event
    event_type = str(event.get("type") or event.get("event") or payload.get("type") or "").strip()
    return event_type, payload


def _session_id_from_event(payload: Dict[str, Any]) -> str:
    session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    nested = data.get("session") if isinstance(data.get("session"), dict) else {}
    return str(session.get("id") or nested.get("id") or payload.get("session_id") or "").strip()


def _required_actions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    nested = data.get("session") if isinstance(data.get("session"), dict) else {}
    actions = session.get("required_actions") or nested.get("required_actions") or payload.get("required_actions")
    return [item for item in actions if isinstance(item, dict)] if isinstance(actions, list) else []


def _sse_event(event_name: str, data_lines: List[str]) -> Optional[Dict[str, Any]]:
    raw = "\n".join(data_lines).strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise AgentsAPIError("Agents API returned an invalid event stream.") from None
    if not isinstance(payload, dict):
        raise AgentsAPIError("Agents API returned an invalid event stream.")
    if event_name and not payload.get("type"):
        payload["type"] = event_name
    return payload


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        response = await client.request(method, path, json=json_body)
    except httpx.HTTPError:
        raise AgentsAPIError("Could not connect to the Agents API.") from None
    if response.status_code >= 400:
        raise AgentsAPIError(f"Agents API returned HTTP {response.status_code}.")
    if not response.content:
        return {}
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        raise AgentsAPIError("Agents API returned an invalid response.") from None
    if not isinstance(payload, dict):
        raise AgentsAPIError("Agents API returned an invalid response.")
    return payload


async def _submit_tool_result(
    client: httpx.AsyncClient,
    session_id: str,
    action: Dict[str, Any],
    *,
    success: bool,
    output: str = "",
    error: str = "",
) -> None:
    result: Dict[str, Any] = {
        "type": "agent.session.input.tool_result",
        "turn_id": str(action.get("turn_id") or ""),
        "call_id": str(action.get("call_id") or ""),
        "success": success,
    }
    if success:
        result["output"] = output
    else:
        result["error"] = error or "AutoYou could not process this request."
    await _request_json(
        client,
        "POST",
        f"/agents/sessions/{session_id}/events",
        json_body={"events": [result]},
    )


async def run_agents_api_turn(
    message: str,
    call_autoyou: Callable[[], Awaitable[T]],
    *,
    attachment_count: int = 0,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> T:
    """Use an Agents API session to route one turn to the local AutoYou server.

    The callback owns the original chat request, including private attachments.
    The Agents API receives only the text message and an attachment count. Its
    function result is deliberately generic so AutoYou's answer is not sent
    back to OpenAI or rewritten by the routing model.
    """
    credential = str(api_key or _get_api_key()).strip()
    if not credential:
        raise AgentsAPIConfigurationError("Set OPENAI_API_KEY for this AutoYou instance.")

    text = str(message or "").strip()
    if not text:
        raise AgentsAPIError("A message is required for Agents API routing.")

    try:
        count = max(0, int(attachment_count))
    except (TypeError, ValueError):
        count = 0
    input_text = text
    if count:
        input_text += f"\n\nThe local AutoYou handler also received {count} private attachment(s)."

    instructions = (
        "You are a routing-only agent for one private AutoYou server. "
        f"For every user request, call the {AUTYOU_CHAT_TOOL} function exactly once. "
        "Do not answer from your own knowledge. The application keeps the original "
        "message and all attachment contents locally and sends them to AutoYou's "
        "configured AI model. After the tool result, give a brief acknowledgement."
    )
    create_payload = {
        "agent": {
            "model": str(model or _get_model()),
            "instructions": instructions,
            "tools": [
                {
                    "type": "function",
                    "name": AUTYOU_CHAT_TOOL,
                    "description": "Route the current user turn to its private AutoYou server.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
                {"type": "programmatic_tool_calling", "enabled": False},
            ],
        },
        "environment": {"type": "none"},
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": input_text}],
            }
        ],
        "stream": True,
    }

    timeout = httpx.Timeout(360.0, connect=10.0)
    headers = {
        "Authorization": f"Bearer {credential}",
        "OpenAI-Beta": AGENTS_API_BETA_HEADER,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    session_id = ""
    autoyou_response: Any = None
    local_error = ""
    tool_call_seen = False
    completed = False

    async with httpx.AsyncClient(
        base_url=AGENTS_API_BASE_URL,
        headers=headers,
        timeout=timeout,
        transport=transport,
    ) as client:
        try:
            async with client.stream("POST", "/agents/sessions", json=create_payload) as response:
                if response.status_code >= 400:
                    raise AgentsAPIError(f"Agents API returned HTTP {response.status_code}.")

                event_name = ""
                data_lines: List[str] = []

                async def handle_event(event: Dict[str, Any]) -> None:
                    nonlocal session_id, autoyou_response, local_error, tool_call_seen, completed
                    event_type, payload = _event_parts(event)
                    session_id = session_id or _session_id_from_event(payload)

                    if event_type in {"agent.session.requires_action", "agent.session.action_required"}:
                        if not session_id:
                            local_error = "Agents API did not return a session ID."
                            return
                        actions = _required_actions(payload)
                        function_actions = [
                            action for action in actions
                            if action.get("type") == "function_call"
                            and action.get("name") == AUTYOU_CHAT_TOOL
                        ]
                        if len(actions) != 1 or len(function_actions) != 1 or tool_call_seen:
                            local_error = "Agents API did not request one valid AutoYou routing call."
                            for action in actions:
                                if action.get("type") == "function_call":
                                    await _submit_tool_result(
                                        client,
                                        session_id,
                                        action,
                                        success=False,
                                        error="Only one AutoYou routing call is supported per turn.",
                                    )
                            return

                        tool_call_seen = True
                        action = function_actions[0]
                        try:
                            autoyou_response = await call_autoyou()
                            await _submit_tool_result(
                                client,
                                session_id,
                                action,
                                success=True,
                                output="AutoYou processed the request.",
                            )
                        except Exception:
                            local_error = "AutoYou could not process this request."
                            await _submit_tool_result(
                                client,
                                session_id,
                                action,
                                success=False,
                                error=local_error,
                            )
                        return

                    if event_type in {"agent.session.turn.failed", "agent.session.failed", "error"}:
                        local_error = "Agents API turn failed."
                        return
                    if event_type in {"agent.session.turn.cancelled"}:
                        local_error = "Agents API turn was cancelled."
                        return
                    if event_type == "agent.session.turn.completed":
                        completed = True

                async for line in response.aiter_lines():
                    if not line:
                        event = _sse_event(event_name, data_lines)
                        if event is not None:
                            await handle_event(event)
                        event_name = ""
                        data_lines = []
                        if completed:
                            break
                        continue
                    if line.startswith(":"):
                        continue
                    field, separator, value = line.partition(":")
                    if not separator:
                        continue
                    value = value[1:] if value.startswith(" ") else value
                    if field == "event":
                        event_name = value.strip()
                    elif field == "data":
                        data_lines.append(value)

                if not completed and not local_error:
                    event = _sse_event(event_name, data_lines)
                    if event is not None:
                        await handle_event(event)

            if local_error:
                raise AgentsAPIError(local_error)
            if not completed:
                raise AgentsAPIError("Agents API stream ended before the turn completed.")
            if not tool_call_seen or autoyou_response is None:
                raise AgentsAPIError("Agents API completed without routing the request to AutoYou.")
            return autoyou_response
        except httpx.HTTPError:
            raise AgentsAPIError("Could not complete the Agents API session.") from None
        finally:
            if session_id:
                try:
                    deleted = await client.delete(f"/agents/sessions/{session_id}")
                    if deleted.status_code == 409:
                        await asyncio.sleep(0.2)
                        await client.delete(f"/agents/sessions/{session_id}")
                except httpx.HTTPError:
                    pass

