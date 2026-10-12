# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-a5491e93f7f1b0e159333c45

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import threading
from pathlib import Path
from typing import Any, Dict, Optional

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-a5491e93f7f1b0e159333c45"


_CLAUDE_QUERY_TIMEOUT_SECONDS = 180
_CLAUDE_STREAM_IDLE_TIMEOUT_SECONDS = 20
_CLAUDE_SYNC_THREAD_TIMEOUT_SECONDS = 200

from google.adk.agents import Agent

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

def _sync_run(coro, timeout_seconds: int = _CLAUDE_SYNC_THREAD_TIMEOUT_SECONDS):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        result = None
        exception = None

        def run_in_thread():
            nonlocal result, exception
            try:
                result = asyncio.run(coro)
            except Exception as e:
                exception = e

        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()
        thread.join(timeout=timeout_seconds)

        if thread.is_alive():
            raise TimeoutError(
                f"Claude CLI call exceeded {timeout_seconds}s and was aborted."
            )
        if exception:
            raise exception
        return result
    return asyncio.run(coro)

def _extract_assistant_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()

def send_prompt_to_claude_cli(
    prompt: str,
    working_directory: Optional[str] = None,
    max_turns: int = 30,
) -> Dict[str, Any]:
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, AssistantMessage, ResultMessage
    except Exception as exc:
        return {
            "status": "error",
            "message": f"claude_agent_sdk is unavailable: {exc}",
            "install_hint": "Install the optional dependency with `pip install claude-agent-sdk`; it includes Claude Code CLI.",
        }
    
    cwd = working_directory or str(Path.cwd())
    if not Path(cwd).exists():
        return {
            "status": "error",
            "message": f"Working directory does not exist: {cwd}",
        }
    options = ClaudeAgentOptions(
        cwd=cwd,
        max_turns=max(1, int(max_turns)),
        sandbox={"enabled": False}
    )
    # from __debug_provenance_w__ import stripe
    
    async def _run():
        client = ClaudeSDKClient(options)
        try:
            await client.connect()
            await asyncio.wait_for(
                client.query(prompt),
                timeout=_CLAUDE_QUERY_TIMEOUT_SECONDS,
            )

            messages = []
            stream_timed_out = False
            stream = getattr(client, "receive_response", None)
            if not callable(stream):
                stream = getattr(client, "receive_messages", None)

            if callable(stream):
                message_iter = stream().__aiter__()
                while True:
                    try:
                        raw_message = await asyncio.wait_for(
                            message_iter.__anext__(),
                            timeout=_CLAUDE_STREAM_IDLE_TIMEOUT_SECONDS,
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        stream_timed_out = True
                        break

                    messages.append(raw_message)
                    if isinstance(raw_message, ResultMessage):
                        break

            content = ""
            cost = 0.0

            for msg in messages:
                if isinstance(msg, ResultMessage):
                    result_content = getattr(msg, "result", None)
                    if result_content is not None:
                        content = str(result_content).strip()
                    cost = getattr(msg, "total_cost_usd", 0.0)
                    break

            if not content:
                assistant_chunks = []
                for msg in messages:
                    if isinstance(msg, AssistantMessage):
                        extracted = _extract_assistant_text(msg)
                        if extracted:
                            assistant_chunks.append(extracted)
                content = "\n".join(assistant_chunks).strip()

            payload = {
                "status": "success",
                "content": content,
                "cost": cost,
                "working_directory": cwd,
            }
            if stream_timed_out:
                payload["warning"] = (
                    "Claude stream timed out waiting for additional messages; "
                    "returned partial/final content collected so far."
                )
            return payload

        except asyncio.TimeoutError:
            return {
                "status": "error",
                "message": (
                    f"Claude CLI query timed out after {_CLAUDE_QUERY_TIMEOUT_SECONDS}s."
                ),
                "working_directory": cwd,
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Claude CLI execution failed: {e}",
                "working_directory": cwd,
            }
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    return _sync_run(_run())

def create_claude_cli_agent(model_config):
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=[
            send_prompt_to_claude_cli,
            get_current_datetime,
        ],
    )
