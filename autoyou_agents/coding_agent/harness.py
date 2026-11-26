# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Harness internals for AutoYou's Coding Agent.

Provides:
- Durable session-scoped task plan & progress checkpointing (`manage_coding_plan`).
- Automatic tracking of files read, files modified, and verification results.
- Anti-thrash duplicate tool-call guard (nudges the model instead of burning
  budget or opening the circuit breaker on repeated identical calls).
- Deterministic context compaction for older bulky tool outputs so local LLMs
  can run long multi-file coding sessions without prompt overflow or attention
  dilution.
- Pending builder/website/game handoff auto-injection into the system prompt.
- Output token floor (`>= 4096`) so local LLMs writing complete source files or
  patches do not truncate mid-JSON.
- Safe web-documentation reference lookup (`research_web_reference`) honoring
  AutoYou's internet-access gate and SSRF guards.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext

from autoyou_agents.shared_tools.coding_handoff import load_coding_handoff
from shared.session_execution import _parse_nonnegative_float, _parse_nonnegative_int

logger = logging.getLogger(__name__)

CODING_HARNESS_STATE_KEY = "_autoyou_coding_harness"
ACTIVE_CODING_HANDOFF_STATE_KEY = "_autoyou_active_coding_handoff"
CODING_OUTPUT_TOKENS_FLOOR = 4096
_RECENT_CONTENTS_TO_KEEP = 8
_COMPACT_TOOL_RESPONSE_MIN_CHARS = 1200
_MAX_TRACKED_FILES = 32
_MAX_TRACKED_VERIFICATIONS = 12
_MAX_NOTES = 12
_DUPLICATE_REPEAT_THRESHOLD = 2

# Lightweight orientation, planning, status, and syntax-check tools do not count
# against the primary heavy-mutation/shell tool budget so the model never runs
# out of room just for orienting, planning, or verifying its edits.
LIGHTWEIGHT_CODING_TOOLS = frozenset(
    {
        "get_workspace_root",
        "get_pending_builder_handoff",
        "manage_coding_plan",
        "get_file_outline",
        "verify_workspace_files",
        "git_status",
        "git_diff",
        "get_current_datetime",
    }
)


def normalize_coding_harness_state(raw_value: Any) -> Dict[str, Any]:
    data = raw_value if isinstance(raw_value, dict) else {}
    def string_list(key: str) -> List[str]:
        values = data.get(key)
        return [p for p in values if isinstance(p, str) and p.strip()] if isinstance(values, list) else []
    steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    clean_steps: List[Dict[str, str]] = []
    for item in steps:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            status = str(item.get("status") or "pending").strip().lower()
            if status not in {"pending", "in_progress", "done", "blocked", "skipped"}:
                status = "pending"
            if title:
                clean_steps.append({"title": title[:160], "status": status})
        elif isinstance(item, str) and item.strip():
            clean_steps.append({"title": item.strip()[:160], "status": "pending"})

    return {
        "goal": str(data.get("goal") or "").strip()[:400],
        "stage": str(data.get("stage") or "orient").strip().lower()[:32],
        "steps": clean_steps,
        "files_read": string_list("files_read")[-_MAX_TRACKED_FILES:],
        "files_modified": string_list("files_modified")[-_MAX_TRACKED_FILES:],
        "pending_verification": string_list("pending_verification"),
        "finalization_attempts": _parse_nonnegative_int(data.get("finalization_attempts"), 0),
        "verifications": [
            v for v in (data.get("verifications") if isinstance(data.get("verifications"), list) else []) if isinstance(v, dict)
        ][- _MAX_TRACKED_VERIFICATIONS:],
        "notes": string_list("notes")[-_MAX_NOTES:],
        "last_call_signature": str(data.get("last_call_signature") or ""),
        "invocation_id": str(data.get("invocation_id") or ""),
        "repeat_streak": _parse_nonnegative_int(data.get("repeat_streak"), 0),
        "lightweight_calls": _parse_nonnegative_int(data.get("lightweight_calls"), 0),
        "compactions": _parse_nonnegative_int(data.get("compactions"), 0),
        "updated_at": _parse_nonnegative_float(data.get("updated_at"), time.time()),
    }


def _append_unique(items: List[str], value: str, *, limit: int = _MAX_TRACKED_FILES) -> List[str]:
    cleaned = str(value or "").strip()
    if not cleaned:
        return items
    updated = [item for item in items if item != cleaned]
    updated.append(cleaned)
    return updated[-limit:]


def _tool_signature(tool_name: str, args: Optional[Dict[str, Any]]) -> str:
    try:
        serialized = json.dumps(args or {}, sort_keys=True, ensure_ascii=True, default=str)
    except Exception:
        serialized = repr(args)
    digest = hashlib.sha256(f"{tool_name}:{serialized}".encode("utf-8")).hexdigest()[:20]
    return f"{tool_name}:{digest}"


def check_duplicate_tool_call(
    harness_state: Dict[str, Any],
    tool_name: str,
    args: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Detect repeated identical consecutive calls and return a constructive nudge."""
    if tool_name in {"manage_coding_plan", "git_status", "git_diff", "verify_workspace_files", "get_current_datetime"}:
        harness_state["last_call_signature"] = ""
        harness_state["repeat_streak"] = 0
        return None

    sig = _tool_signature(tool_name, args)
    if sig == harness_state.get("last_call_signature"):
        streak = int(harness_state.get("repeat_streak") or 0) + 1
        harness_state["repeat_streak"] = streak
        if streak >= _DUPLICATE_REPEAT_THRESHOLD:
            return {
                "status": "skipped_duplicate",
                "tool": tool_name,
                "repeat_count": streak,
                "message": (
                    f"Skipped repeating the exact same `{tool_name}` call ({streak}x in a row). "
                    "Use the result already returned above, change your arguments/line offset, "
                    "or move on to editing/verifying the files."
                ),
            }
    else:
        harness_state["last_call_signature"] = sig
        harness_state["repeat_streak"] = 1
    return None


def record_coding_tool_activity(
    harness_state: Dict[str, Any],
    tool_name: str,
    args: Optional[Dict[str, Any]],
    response: Any,
) -> Dict[str, Any]:
    """Update durable coding harness state from observed tool calls."""
    args_dict = args if isinstance(args, dict) else {}
    resp_dict = response if isinstance(response, dict) else {}
    status = str(resp_dict.get("status") or "").strip().lower()

    if status == "skipped_duplicate":
        harness_state["updated_at"] = time.time()
        return harness_state
    if status != "success":
        # A transient failure must be retryable with the same arguments.
        harness_state["last_call_signature"] = ""
        harness_state["repeat_streak"] = 0

    if tool_name in {"read_file", "get_file_outline"} and status == "success":
        rel_path = str(resp_dict.get("path") or args_dict.get("path") or "").strip()
        if rel_path:
            harness_state["files_read"] = _append_unique(harness_state["files_read"], rel_path)
    elif tool_name == "read_files_batch" and status == "success":
        for item in (resp_dict.get("results") or resp_dict.get("files") or []):
            if isinstance(item, dict) and item.get("status") == "success" and item.get("path"):
                harness_state["files_read"] = _append_unique(harness_state["files_read"], str(item["path"]))
    elif tool_name in {"write_file", "append_file", "replace_text", "insert_before", "insert_after", "multi_edit_file"} and status == "success":
        rel_path = str(resp_dict.get("path") or args_dict.get("path") or "").strip()
        if rel_path:
            harness_state["files_modified"] = _append_unique(harness_state["files_modified"], rel_path)
            if rel_path not in harness_state["pending_verification"]:
                harness_state["pending_verification"].append(rel_path)
        harness_state["stage"] = "implement"
        harness_state["finalization_attempts"] = 0
    elif tool_name == "apply_patch" and status == "success":
        for change in (resp_dict.get("changes") or resp_dict.get("changed_files") or []):
            rel_path = change.get("path") if isinstance(change, dict) else change
            if rel_path:
                harness_state["files_modified"] = _append_unique(harness_state["files_modified"], str(rel_path))
                if isinstance(change, dict) and change.get("action") == "deleted":
                    harness_state["pending_verification"] = [p for p in harness_state["pending_verification"] if p != str(rel_path)]
                elif str(rel_path) not in harness_state["pending_verification"]:
                    harness_state["pending_verification"].append(str(rel_path))
        harness_state["stage"] = "implement"
        harness_state["finalization_attempts"] = 0
    elif tool_name in {"verify_workspace_files", "run_command"}:
        summary = ""
        if tool_name == "verify_workspace_files":
            verified = {str(report.get("path")) for report in resp_dict.get("reports", []) if isinstance(report, dict) and report.get("ok")}
            harness_state["pending_verification"] = [p for p in harness_state["pending_verification"] if p not in verified]
            ok = bool(resp_dict.get("all_passed", resp_dict.get("ok", False)))
            checked = int(resp_dict.get("checked_count", resp_dict.get("checked", 0)) or 0)
            summary = f"verify_workspace_files: {'OK' if ok else 'ISSUES'} ({checked} files)"
            if ok and harness_state.get("files_modified"):
                harness_state["stage"] = "verify"
        else:
            cmd = str(args_dict.get("command") or "")[:80]
            rc = resp_dict.get("exit_code", resp_dict.get("returncode"))
            summary = f"run_command `{cmd}` -> status={status or 'unknown'} rc={rc}"
            if status == "success" and harness_state.get("files_modified"):
                harness_state["stage"] = "verify"
        verifications = list(harness_state.get("verifications") or [])
        verifications.append(
            {
                "tool": tool_name,
                "status": "ok" if (status == "success" and resp_dict.get("ok", True)) else (status or "error"),
                "summary": summary[:200],
            }
        )
        harness_state["verifications"] = verifications[-_MAX_TRACKED_VERIFICATIONS:]

    harness_state["updated_at"] = time.time()
    return harness_state


def manage_coding_plan(
    action: str = "get",
    goal: str = "",
    stage: str = "",
    steps_json: str = "",
    note: str = "",
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Maintain a durable coding plan and progress checkpoint in session state.

    Args:
        action: One of ``get``, ``init``, ``update``, ``complete_step``, ``note``, ``clear``.
        goal: Short goal statement for the current coding task.
        stage: Current stage: ``orient``, ``plan``, ``implement``, ``verify``, or ``done``.
        steps_json: JSON list of step titles (strings) or ``[{"title": ..., "status": ...}]`` objects,
            or for ``complete_step`` either a step index (1-based) or matching title substring.
        note: Short technical note or finding to keep across context compaction.
    """
    if tool_context is None:
        return {"status": "error", "message": "tool_context is required"}

    act = str(action or "get").strip().lower()
    if act not in {"get", "init", "update", "complete_step", "note", "clear"}:
        return {"status": "error", "message": f"Unknown coding plan action: {act}"}
    if act == "clear":
        fresh = normalize_coding_harness_state({})
        tool_context.state[CODING_HARNESS_STATE_KEY] = fresh
        tool_context.state[ACTIVE_CODING_HANDOFF_STATE_KEY] = None
        return {"status": "success", "action": "clear", "plan": fresh}

    state = normalize_coding_harness_state(tool_context.state.get(CODING_HARNESS_STATE_KEY))

    if goal and goal.strip():
        state["goal"] = goal.strip()[:400]
    if stage and stage.strip():
        norm_stage = stage.strip().lower()
        if norm_stage in {"orient", "plan", "implement", "verify", "done"}:
            state["stage"] = norm_stage

    if act in {"init", "update"} and steps_json and steps_json.strip():
        parsed_steps: Optional[Any] = None
        raw_steps = steps_json.strip()
        try:
            parsed_steps = json.loads(raw_steps)
        except Exception:
            # Accept newline/semicolon separated checklists gracefully from smaller local LLMs.
            lines = [line.strip(" -*\t") for line in raw_steps.replace(";", "\n").splitlines() if line.strip()]
            parsed_steps = lines
        if isinstance(parsed_steps, list):
            new_steps: List[Dict[str, str]] = []
            for item in parsed_steps:
                if isinstance(item, dict):
                    title = str(item.get("title") or "").strip()
                    st = str(item.get("status") or "pending").strip().lower()
                    if st not in {"pending", "in_progress", "done", "blocked", "skipped"}:
                        st = "pending"
                    if title:
                        new_steps.append({"title": title[:160], "status": st})
                elif isinstance(item, str) and item.strip():
                    new_steps.append({"title": item.strip()[:160], "status": "pending"})
            if new_steps:
                if act == "init" and all(s["status"] == "pending" for s in new_steps):
                    new_steps[0]["status"] = "in_progress"
                state["steps"] = new_steps
                if not stage:
                    state["stage"] = "plan"

    elif act == "complete_step":
        selector = (steps_json or note or "").strip()
        steps = list(state.get("steps") or [])
        completed_idx: Optional[int] = None
        if selector.isdigit():
            idx = int(selector) - 1
            if 0 <= idx < len(steps):
                completed_idx = idx
        elif selector:
            low = selector.lower()
            for idx, step in enumerate(steps):
                if low in step["title"].lower():
                    completed_idx = idx
                    break
        else:
            for idx, step in enumerate(steps):
                if step["status"] in {"in_progress", "pending"}:
                    completed_idx = idx
                    break
        if completed_idx is not None:
            steps[completed_idx]["status"] = "done"
            for nxt in range(completed_idx + 1, len(steps)):
                if steps[nxt]["status"] == "pending":
                    steps[nxt]["status"] = "in_progress"
                    break
            state["steps"] = steps

    if note and note.strip() and act != "complete_step":
        notes = list(state.get("notes") or [])
        notes.append(note.strip()[:240])
        state["notes"] = notes[-_MAX_NOTES:]

    state["updated_at"] = time.time()
    tool_context.state[CODING_HARNESS_STATE_KEY] = state
    return {
        "status": "success",
        "action": act,
        "goal": state["goal"],
        "stage": state["stage"],
        "steps": state["steps"],
        "files_read": state["files_read"],
        "files_modified": state["files_modified"],
        "verifications": state["verifications"],
        "notes": state["notes"],
    }


def _summarize_tool_response_dict(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a compact deterministic summary of a bulky historical tool response."""
    summary: Dict[str, Any] = {
        "_compacted": True,
        "tool": name,
        "status": payload.get("status", "unknown"),
    }
    for key in (
        "path",
        "start_line",
        "end_line",
        "total_lines",
        "next_start_line",
        "truncated",
        "returncode",
        "timed_out",
        "ok",
        "checked",
        "total_matches",
        "count",
        "replaced_occurrences",
        "applied_edits",
        "changed_files",
        "bytes_written",
    ):
        if key in payload and payload[key] is not None:
            summary[key] = payload[key]

    if "message" in payload and isinstance(payload["message"], str):
        summary["message"] = payload["message"][:220]

    if "content" in payload and isinstance(payload["content"], str):
        lines = payload["content"].splitlines()
        head = "\n".join(lines[:4])[:220]
        summary["content_preview"] = f"[Compacted {len(payload['content'])} chars / {len(lines)} lines] {head}"
    if "stdout" in payload and isinstance(payload["stdout"], str) and payload["stdout"].strip():
        tail = payload["stdout"][-260:].strip()
        summary["stdout_tail"] = f"[Compacted {len(payload['stdout'])} chars] ...{tail}"
    if "stderr" in payload and isinstance(payload["stderr"], str) and payload["stderr"].strip():
        tail = payload["stderr"][-260:].strip()
        summary["stderr_tail"] = f"[Compacted {len(payload['stderr'])} chars] ...{tail}"
    if "results" in payload and isinstance(payload["results"], list):
        top_paths: List[str] = []
        for item in payload["results"][:6]:
            if isinstance(item, dict) and item.get("path"):
                line = item.get("line") or item.get("start_line")
                label = f"{item['path']}:{line}" if line else str(item["path"])
                if label not in top_paths:
                    top_paths.append(label)
        summary["result_count"] = len(payload["results"])
        summary["sample_hits"] = top_paths
    if "files" in payload and isinstance(payload["files"], list):
        summary["file_count"] = len(payload["files"])
        sample = []
        for item in payload["files"][:8]:
            if isinstance(item, dict) and item.get("path"):
                sample.append(str(item["path"]))
            elif isinstance(item, str):
                sample.append(item)
        summary["sample_files"] = sample
    if "symbols" in payload and isinstance(payload["symbols"], list):
        summary["symbol_count"] = len(payload["symbols"])
        summary["sample_symbols"] = [
            f"{s.get('name')}@{s.get('line')}"
            for s in payload["symbols"][:8]
            if isinstance(s, dict)
        ]
    if "diff" in payload and isinstance(payload["diff"], str):
        summary["diff_preview"] = f"[Compacted diff {len(payload['diff'])} chars] {payload['diff'][:220]}"

    return summary


def compact_coding_llm_request(llm_request: Any) -> int:
    """Deterministically compact bulky older tool responses inside ``llm_request.contents``.

    Preserves the first user turn and the most recent ``_RECENT_CONTENTS_TO_KEEP``
    content entries verbatim so recent reads, edits, and compiler diagnostics stay
    at full resolution while preventing context overflow on long local-LLM coding runs.

    Returns the number of tool response parts compacted.
    """
    if llm_request is None:
        return 0
    contents = getattr(llm_request, "contents", None)
    if not isinstance(contents, list) or len(contents) <= _RECENT_CONTENTS_TO_KEEP + 1:
        return 0

    try:
        from google.genai import types
    except Exception:
        return 0

    cutoff = len(contents) - _RECENT_CONTENTS_TO_KEEP
    compacted_parts = 0
    new_contents = list(contents)

    for idx in range(1, cutoff):
        content = contents[idx]
        parts = getattr(content, "parts", None)
        if not isinstance(parts, list) or not parts:
            continue
        modified = False
        new_parts = []
        for part in parts:
            fn_resp = getattr(part, "function_response", None)
            if fn_resp is None:
                new_parts.append(part)
                continue
            resp_dict = getattr(fn_resp, "response", None)
            if not isinstance(resp_dict, dict) or resp_dict.get("_compacted"):
                new_parts.append(part)
                continue
            try:
                serialized_len = len(json.dumps(resp_dict, ensure_ascii=False, default=str))
            except Exception:
                serialized_len = len(str(resp_dict))
            if serialized_len < _COMPACT_TOOL_RESPONSE_MIN_CHARS:
                new_parts.append(part)
                continue
            tool_name = str(getattr(fn_resp, "name", "") or "tool")
            compact_payload = _summarize_tool_response_dict(tool_name, resp_dict)
            new_part = types.Part.from_function_response(
                name=tool_name,
                response=compact_payload,
            )
            call_id = getattr(fn_resp, "id", None)
            if call_id and getattr(new_part, "function_response", None) is not None:
                new_part.function_response.id = call_id
            new_parts.append(new_part)
            modified = True
            compacted_parts += 1
        if modified:
            new_contents[idx] = types.Content(
                role=getattr(content, "role", "user") or "user",
                parts=new_parts,
            )

    if compacted_parts > 0:
        llm_request.contents = new_contents
    return compacted_parts


def _append_system_instruction(llm_request: Any, block: str) -> None:
    if not block or llm_request is None:
        return
    append_fn = getattr(llm_request, "append_instructions", None)
    if callable(append_fn):
        try:
            append_fn([block])
            return
        except Exception:
            pass
    config = getattr(llm_request, "config", None)
    if config is None:
        return
    existing = getattr(config, "system_instruction", None)
    if isinstance(existing, str):
        config.system_instruction = f"{existing}\n\n{block}" if existing else block
    elif existing is None:
        config.system_instruction = block


def _ensure_coding_output_token_floor(llm_request: Any) -> None:
    """Give coding turns enough output tokens to emit full code edits without JSON cutoff."""
    if llm_request is None:
        return
    config = getattr(llm_request, "config", None)
    if config is None:
        return
    try:
        floor = int(os.getenv("AUTOYOU_CODING_MIN_OUTPUT_TOKENS", str(CODING_OUTPUT_TOKENS_FLOOR)))
    except ValueError:
        floor = CODING_OUTPUT_TOKENS_FLOOR
    if floor <= 0:
        return
    current = getattr(config, "max_output_tokens", None)
    if current is None or (isinstance(current, int) and current < floor):
        try:
            config.max_output_tokens = floor
        except Exception:
            pass


def build_coding_harness_status_block(
    harness_state: Dict[str, Any],
    *,
    tool_calls: int = 0,
    tool_budget: int = 0,
    pending_handoff: Optional[Dict[str, Any]] = None,
) -> str:
    """Render a compact state summary appended to the coding agent's system prompt."""
    blocks: List[str] = []

    if isinstance(pending_handoff, dict) and pending_handoff:
        compact_handoff = {
            "source_agent": pending_handoff.get("source_agent"),
            "agent_name": pending_handoff.get("agent_name") or pending_handoff.get("game_id"),
            "agent_dir": pending_handoff.get("agent_dir") or pending_handoff.get("game_dir"),
            "tool_name": pending_handoff.get("tool_name"),
            "frontend_requirement": pending_handoff.get("frontend_requirement"),
            "proxy_path": pending_handoff.get("proxy_path"),
            "implementation_brief": str(pending_handoff.get("implementation_brief") or "")[:900],
            "constraints": str(pending_handoff.get("constraints") or "")[:1200],
            "testing_requirements": str(pending_handoff.get("testing_requirements") or "")[:600],
            "scaffold_files": (pending_handoff.get("scaffold_files") or [])[:12],
        }
        blocks.append(
            "[PENDING BUILDER HANDOFF]\n"
            "A builder/website/game handoff is active in session state. Implement it directly without asking the user to repeat it:\n"
            + json.dumps({k: v for k, v in compact_handoff.items() if v is not None}, ensure_ascii=False)
        )

    has_progress = bool(
        harness_state.get("goal")
        or harness_state.get("steps")
        or harness_state.get("files_modified")
        or harness_state.get("verifications")
        or harness_state.get("notes")
        or (tool_budget > 0 and tool_calls >= max(8, int(tool_budget * 0.75)))
    )
    if has_progress:
        lines = [
            "[CODING HARNESS STATE]",
            f"Stage: {harness_state.get('stage') or 'orient'} | Tool calls: {tool_calls}/{tool_budget if tool_budget > 0 else 'unlimited'}",
        ]
        if harness_state.get("goal"):
            lines.append(f"Goal: {harness_state['goal']}")
        steps = harness_state.get("steps") or []
        if steps:
            formatted = ", ".join(f"[{s['status']}] {s['title']}" for s in steps[:10])
            lines.append(f"Plan: {formatted}")
        if harness_state.get("files_modified"):
            lines.append("Modified files: " + ", ".join(harness_state["files_modified"][-10:]))
        if harness_state.get("verifications"):
            last_v = harness_state["verifications"][-3:]
            lines.append("Verifications: " + "; ".join(f"{v.get('status')}: {v.get('summary')}" for v in last_v))
        if harness_state.get("notes"):
            lines.append("Notes: " + " | ".join(harness_state["notes"][-4:]))
        if tool_budget > 0 and tool_calls >= max(8, int(tool_budget * 0.8)):
            lines.append(
                "Budget reminder: finish remaining edits, run `verify_workspace_files`, and summarize your verified results."
            )
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def prepare_coding_llm_request(
    callback_context: Any,
    llm_request: Any,
    *,
    tool_calls: int,
    tool_budget: int,
) -> Dict[str, Any]:
    """Run compaction, output-token sizing, and state injection before each LLM call."""
    harness_state = normalize_coding_harness_state(callback_context.state.get(CODING_HARNESS_STATE_KEY))
    invocation_id = str(getattr(callback_context, "invocation_id", "") or "")
    if harness_state["invocation_id"] != invocation_id:
        harness_state.update(invocation_id=invocation_id, lightweight_calls=0,
                             last_call_signature="", repeat_streak=0, finalization_attempts=0)
    if llm_request is None:
        callback_context.state[CODING_HARNESS_STATE_KEY] = harness_state
        return harness_state

    compacted = compact_coding_llm_request(llm_request)
    if compacted > 0:
        harness_state["compactions"] = int(harness_state.get("compactions") or 0) + compacted

    _ensure_coding_output_token_floor(llm_request)

    pending_handoff = load_coding_handoff(callback_context.state, clear=False) or callback_context.state.get(ACTIVE_CODING_HANDOFF_STATE_KEY)
    status_block = build_coding_harness_status_block(
        harness_state,
        tool_calls=tool_calls,
        tool_budget=tool_budget,
        pending_handoff=pending_handoff,
    )
    if status_block:
        _append_system_instruction(llm_request, status_block)
    operation = callback_context.state.get("_autoyou_coding_operation")
    if isinstance(operation, dict) and operation.get("phase") == "started" and operation.get("invocation_id") != invocation_id:
        _append_system_instruction(llm_request,
            "[INTERRUPTED OPERATION] A previous run stopped during " + str(operation.get("tool"))
            + ". Inspect the current files and reconcile the durable plan before continuing; do not blindly replay this operation.")

    callback_context.state[CODING_HARNESS_STATE_KEY] = harness_state
    return harness_state


async def research_web_reference(
    query: str,
    url: str = "",
    max_results: int = 5,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Look up technical documentation, API references, or error explanations on the web.

    Obeys AutoYou's internet-access toggle and SSRF guards. Uses sanctioned public
    search APIs/feeds and fast HTTP extraction so coding tasks can check docs
    without leaving the coding workflow.

    Args:
        query: Technical search query (e.g. "FastAPI lifespan async context manager").
        url: Optional specific documentation URL to fetch directly.
        max_results: Maximum search hits to return (1-8, default 5).
    """
    from shared.internet_access import internet_disabled_payload, is_internet_access_enabled

    if not is_internet_access_enabled():
        return internet_disabled_payload()

    clean_query = str(query or "").strip()
    clean_url = str(url or "").strip()
    if not clean_query and not clean_url:
        return {"status": "error", "message": "Provide a technical `query` or documentation `url`."}

    try:
        from autoyou_agents.internet_agent import internet_tool as tool
        if clean_url:
            fetched = await tool.fetch_webpage(clean_url, query=clean_query, max_chars=4000, tool_context=tool_context)
            if fetched.get("status") == "success":
                return {
                    "status": "success",
                    "mode": "fetch",
                    "url": fetched.get("url", clean_url),
                    "title": fetched.get("title", ""),
                    "excerpt": fetched.get("text", "")[:3600],
                    "rag_matches": fetched.get("rag_matches", [])[:4],
                }
            return {
                "status": "error",
                "mode": "fetch",
                "url": clean_url,
                "message": fetched.get("error") or fetched.get("message") or "Could not fetch reference URL.",
            }

        bounded = max(1, min(int(max_results or 5), 8))
        result = await tool.research_web(clean_query, max_results=bounded, fetch_top=2, tool_context=tool_context)
        if result.get("status") != "success":
            return {
                "status": "error",
                "query": clean_query,
                "message": result.get("error") or result.get("message") or "Web reference lookup failed.",
            }
        return {
            "status": "success",
            "mode": "search",
            "query": clean_query,
            "sources": result.get("sources", [])[:bounded],
            "excerpts": result.get("top_excerpts", [])[:4],
            "providers_used": result.get("providers_used", []),
        }
    except Exception as exc:
        logger.warning("research_web_reference failed: %s", exc)
        return {"status": "error", "message": f"Web reference lookup failed: {exc}"}
