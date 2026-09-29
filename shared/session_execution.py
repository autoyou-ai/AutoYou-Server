# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-4295cbefb9e944ea5ad8582c

"""Shared transport identity, queueing, and session execution helpers.

This module centralizes:
- Canonical user/session identity resolution across transports.
- Per-session queued execution for long-running chat turns.
- Session control state helpers shared between the main server and ADK agents.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import contextlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Dict, Optional

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-4295cbefb9e944ea5ad8582c"


STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DEGRADED = "degraded"
STATUS_PAUSED = "paused"
STATUS_BREAKER_OPEN = "breaker_open"
STATUS_RESUMABLE = "resumable"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

SESSION_CONTROL_STATE_KEY = "_autoyou_session_control"
SESSION_RESUME_ARTIFACT_NAME = "autoyou_resume_state.json"


def _parse_positive_float(raw_value: Optional[str], default: float) -> float:
    try:
        parsed = float(str(raw_value or "").strip())
        if parsed > 0:
            return parsed
    except Exception:
        pass
    return float(default)


def _parse_positive_int(raw_value: Optional[str], default: int) -> int:
    try:
        parsed = int(str(raw_value or "").strip())
        if parsed > 0:
            return parsed
    except Exception:
        pass
    return int(default)


def resolve_turn_timeout_seconds() -> float:
    return _parse_positive_float(os.getenv("AUTOYOU_SESSION_TURN_TIMEOUT_SECONDS"), 900.0)


def resolve_queue_limit() -> int:
    return _parse_positive_int(os.getenv("AUTOYOU_SESSION_QUEUE_LIMIT"), 20)


def resolve_coding_model_call_budget() -> int:
    # Kept for metadata/backward compatibility only. The coding agent no longer
    # enforces a hard model-call cutoff; real timeouts and tool failures open the
    # breaker instead.
    return 0


def resolve_coding_tool_call_budget() -> int:
    return _parse_positive_int(os.getenv("AUTOYOU_CODING_AGENT_MAX_TOOL_CALLS"), 32)


def resolve_coding_turn_budget_seconds() -> float:
    return _parse_positive_float(os.getenv("AUTOYOU_CODING_AGENT_MAX_TURN_SECONDS"), 600.0)


def resolve_coding_breaker_cooldown_seconds() -> float:
    return _parse_positive_float(os.getenv("AUTOYOU_CODING_AGENT_BREAKER_COOLDOWN_SECONDS"), 900.0)


def resolve_timeout_breaker_threshold() -> int:
    return _parse_positive_int(os.getenv("AUTOYOU_SESSION_TIMEOUT_BREAKER_THRESHOLD"), 2)


def _normalize_transport(value: Optional[str], fallback: str = "unknown") -> str:
    normalized = str(value or "").strip().lower()
    return normalized or fallback


def _normalize_sender_id(value: Optional[str], fallback: str = "unknown") -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def build_owner_key(transport: Optional[str], sender_id: Optional[str]) -> str:
    return f"{_normalize_transport(transport)}:{_normalize_sender_id(sender_id)}"


def build_transport_alias(transport: Optional[str], sender_id: Optional[str]) -> str:
    return f"transport:{build_owner_key(transport, sender_id)}"


def build_webrtc_alias(session_id: Optional[str]) -> str:
    return f"webrtc:{_normalize_sender_id(session_id)}"


def build_guest_owner_key(session_id: Optional[str]) -> str:
    return f"guest:{_normalize_sender_id(session_id)}"


def build_canonical_user_id(owner_key: str) -> str:
    return f"user::{owner_key}"


def _normalize_thread_id(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 1 else None


def build_canonical_session_id(owner_key: str, thread_id: Optional[int] = None) -> str:
    normalized_thread_id = _normalize_thread_id(thread_id)
    if normalized_thread_id is None:
        return f"session::{owner_key}"
    return f"session::{owner_key}::{normalized_thread_id}"


@dataclass(frozen=True)
class UnifiedSessionIdentity:
    transport: str
    sender_id: str
    raw_session_id: str
    owner_key: str
    canonical_user_id: str
    canonical_session_id: str
    thread_id: Optional[int] = None
    pairing_mode: str = ""

    def with_thread(self, thread_id: Optional[int]) -> "UnifiedSessionIdentity":
        normalized_thread_id = _normalize_thread_id(thread_id)
        return replace(
            self,
            canonical_session_id=build_canonical_session_id(self.owner_key, normalized_thread_id),
            thread_id=normalized_thread_id,
        )


@dataclass(frozen=True)
class SessionExecutionStatus:
    status: str
    message: str
    canonical_user_id: str
    canonical_session_id: str
    queue_position: int = 0
    resumable: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class SessionExecutionError(Exception):
    """Base class for session execution control exceptions."""

    def __init__(self, status: SessionExecutionStatus):
        super().__init__(status.message)
        self.status = status


class SessionQueueFullError(SessionExecutionError):
    """Raised when a session queue cannot accept more turns."""


class SessionTurnTimeoutError(SessionExecutionError):
    """Raised when a queued turn exceeds the configured execution timeout."""


class SessionTurnCancelledError(SessionExecutionError):
    """Raised when a user stops an active or queued session turn."""


def normalize_session_control_state(raw_value: Any) -> Dict[str, Any]:
    data = raw_value if isinstance(raw_value, dict) else {}
    now = time.time()
    breaker_open_until = 0.0
    try:
        breaker_open_until = float(data.get("breaker_open_until") or 0.0)
    except Exception:
        breaker_open_until = 0.0

    normalized = {
        "status": str(data.get("status") or STATUS_RUNNING),
        "canonical_user_id": str(data.get("canonical_user_id") or ""),
        "canonical_session_id": str(data.get("canonical_session_id") or ""),
        "owner_key": str(data.get("owner_key") or ""),
        "turn_started_at": float(data.get("turn_started_at") or 0.0),
        "turn_timeout_seconds": float(
            data.get("turn_timeout_seconds") or resolve_coding_turn_budget_seconds()
        ),
        "model_call_budget": int(
            data.get("model_call_budget") or resolve_coding_model_call_budget()
        ),
        "tool_call_budget": int(
            data.get("tool_call_budget") or resolve_coding_tool_call_budget()
        ),
        "model_calls": int(data.get("model_calls") or 0),
        "tool_calls": int(data.get("tool_calls") or 0),
        "consecutive_timeouts": int(data.get("consecutive_timeouts") or 0),
        "breaker_open_until": breaker_open_until,
        "degraded_mode": bool(data.get("degraded_mode") or (breaker_open_until > now)),
        "resumable": bool(data.get("resumable") or False),
        "pause_reason": str(data.get("pause_reason") or ""),
        "last_invocation_id": str(data.get("last_invocation_id") or ""),
        "last_agent_name": str(data.get("last_agent_name") or ""),
        "last_tool_name": str(data.get("last_tool_name") or ""),
        "last_error_message": str(data.get("last_error_message") or ""),
        "updated_at": float(data.get("updated_at") or now),
        "queue_position": int(data.get("queue_position") or 0),
        "degraded_scope": str(data.get("degraded_scope") or "coding_agent"),
    }
    if normalized["breaker_open_until"] and normalized["breaker_open_until"] <= now:
        normalized["breaker_open_until"] = 0.0
        if normalized["status"] == STATUS_BREAKER_OPEN:
            normalized["status"] = STATUS_RUNNING
    return normalized


def prepare_session_control_for_turn(
    existing_state: Any,
    *,
    canonical_user_id: str,
    canonical_session_id: str,
    owner_key: str,
    queue_position: int = 0,
) -> Dict[str, Any]:
    state = normalize_session_control_state(existing_state)
    now = time.time()
    breaker_open_until = float(state.get("breaker_open_until") or 0.0)
    breaker_active = breaker_open_until > now
    state.update(
        {
            "status": STATUS_BREAKER_OPEN if breaker_active else STATUS_RUNNING,
            "canonical_user_id": canonical_user_id,
            "canonical_session_id": canonical_session_id,
            "owner_key": owner_key,
            "turn_started_at": now,
            "turn_timeout_seconds": resolve_coding_turn_budget_seconds(),
            "model_call_budget": resolve_coding_model_call_budget(),
            "tool_call_budget": resolve_coding_tool_call_budget(),
            "model_calls": 0,
            "tool_calls": 0,
            "degraded_mode": breaker_active,
            "resumable": breaker_active,
            "pause_reason": state.get("pause_reason", "") if breaker_active else "",
            "last_error_message": "",
            "updated_at": now,
            "queue_position": int(max(0, queue_position)),
        }
    )
    if not breaker_active:
        state["breaker_open_until"] = 0.0
    return state


def mark_session_control_paused(
    state: Any,
    *,
    reason: str,
    breaker_open_until: Optional[float] = None,
    degraded_scope: str = "coding_agent",
    last_agent_name: str = "coding_agent",
    last_invocation_id: Optional[str] = None,
) -> Dict[str, Any]:
    normalized = normalize_session_control_state(state)
    normalized["status"] = STATUS_PAUSED
    normalized["degraded_mode"] = True
    normalized["resumable"] = True
    normalized["pause_reason"] = str(reason or "Paused")
    normalized["degraded_scope"] = degraded_scope
    normalized["last_agent_name"] = last_agent_name
    if last_invocation_id:
        normalized["last_invocation_id"] = str(last_invocation_id)
    if breaker_open_until and breaker_open_until > time.time():
        normalized["breaker_open_until"] = float(breaker_open_until)
        normalized["status"] = STATUS_BREAKER_OPEN
    normalized["updated_at"] = time.time()
    return normalized


def build_session_execution_metadata(state: Any) -> Dict[str, Any]:
    normalized = normalize_session_control_state(state)
    status = normalized.get("status") or STATUS_RUNNING
    resumable = bool(normalized.get("resumable") or status in (STATUS_PAUSED, STATUS_BREAKER_OPEN))
    return {
        "status": status,
        "degraded": bool(normalized.get("degraded_mode") or False),
        "resumable": resumable,
        "pause_reason": normalized.get("pause_reason") or "",
        "breaker_open_until": normalized.get("breaker_open_until") or 0.0,
        "consecutive_timeouts": int(normalized.get("consecutive_timeouts") or 0),
        "last_invocation_id": normalized.get("last_invocation_id") or "",
        "last_agent_name": normalized.get("last_agent_name") or "",
        "last_tool_name": normalized.get("last_tool_name") or "",
        "model_calls": int(normalized.get("model_calls") or 0),
        "tool_calls": int(normalized.get("tool_calls") or 0),
        "canonical_user_id": normalized.get("canonical_user_id") or "",
        "canonical_session_id": normalized.get("canonical_session_id") or "",
        "owner_key": normalized.get("owner_key") or "",
        "degraded_scope": normalized.get("degraded_scope") or "coding_agent",
    }


def build_agent_steering_actions(*, include_coding: bool = True) -> list[Dict[str, Any]]:
    actions: list[Dict[str, Any]] = [
        {
            "id": "go_to_main_agent",
            "label": "Main agent",
            "type": "chat_prefill",
            "command": "go to root agent",
            "fill_mode": "replace",
            "steering_target": "autoyou_agent",
        },
    ]
    if include_coding:
        actions.append(
            {
                "id": "go_to_coding_agent",
                "label": "Coding agent",
                "type": "chat_prefill",
                "command": "go to coding agent",
                "fill_mode": "replace",
                "steering_target": "autoyou_coding_agent",
            }
        )
    return actions


def build_session_recovery_actions(state: Any) -> list[Dict[str, Any]]:
    metadata = build_session_execution_metadata(state)
    status = str(metadata.get("status") or "").strip()
    if status not in (STATUS_PAUSED, STATUS_BREAKER_OPEN) and not metadata.get("resumable"):
        return []
    return build_agent_steering_actions(include_coding=True)


def build_coding_guard_message(state: Any) -> str:
    metadata = build_session_execution_metadata(state)
    reason = metadata.get("pause_reason") or "The coding workflow has been paused."
    if metadata.get("status") == STATUS_BREAKER_OPEN and metadata.get("breaker_open_until"):
        return f"{reason} Use the available action to go back to the main agent, or return to the coding agent with a smaller continuation."
    if metadata.get("resumable"):
        return f"{reason} This coding task is paused and resumable from the coding agent."
    return reason


def create_text_llm_response(text: str, *, custom_metadata: Optional[Dict[str, Any]] = None):
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=str(text))]),
        custom_metadata=custom_metadata or {},
    )


def create_tool_call_llm_response(
    tool_name: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    call_id: Optional[str] = None,
    custom_metadata: Optional[Dict[str, Any]] = None,
):
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    part = types.Part.from_function_call(
        name=str(tool_name or "").strip(),
        args=dict(args or {}),
    )
    if part.function_call is not None:
        part.function_call.id = str(call_id or f"call_{uuid.uuid4().hex[:12]}")

    return LlmResponse(
        content=types.Content(role="model", parts=[part]),
        custom_metadata=custom_metadata or {},
    )


async def maybe_save_resume_artifact(callback_context: Any, state: Any) -> None:
    if callback_context is None or not hasattr(callback_context, "save_artifact"):
        return
    try:
        from google.genai import types

        payload = json.dumps(build_session_execution_metadata(state), separators=(",", ":"))
        await callback_context.save_artifact(
            SESSION_RESUME_ARTIFACT_NAME,
            types.Part(text=payload),
        )
    except Exception:
        return


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


@dataclass
class _QueuedTurn:
    turn_id: str
    label: str
    future: asyncio.Future
    handler: Callable[[], Awaitable[Any]]
    on_status: Optional[Callable[[SessionExecutionStatus], Any]]
    timeout_seconds: float


@dataclass
class _RuntimeContext:
    identity: UnifiedSessionIdentity
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    worker_task: Optional[asyncio.Task] = None
    active_task: Optional[asyncio.Task] = None
    active_turn: Optional[_QueuedTurn] = None
    active_turn_id: Optional[str] = None
    started_at: float = 0.0
    last_progress_at: float = 0.0
    queued_turns: int = 0
    consecutive_timeouts: int = 0
    breaker_open_until: float = 0.0
    degraded_mode: bool = False
    last_invocation_id: str = ""
    last_status: str = STATUS_RUNNING
    last_error_message: str = ""


class SessionExecutionManager:
    """Serialize long-running chat turns per canonical session."""

    def __init__(
        self,
        *,
        queue_limit: Optional[int] = None,
        default_turn_timeout_seconds: Optional[float] = None,
        timeout_breaker_threshold: Optional[int] = None,
        breaker_cooldown_seconds: Optional[float] = None,
    ) -> None:
        self.queue_limit = int(queue_limit or resolve_queue_limit())
        self.default_turn_timeout_seconds = float(
            default_turn_timeout_seconds or resolve_turn_timeout_seconds()
        )
        self.timeout_breaker_threshold = int(
            timeout_breaker_threshold or resolve_timeout_breaker_threshold()
        )
        self.breaker_cooldown_seconds = float(
            breaker_cooldown_seconds or resolve_coding_breaker_cooldown_seconds()
        )
        self._owner_aliases: Dict[str, str] = {}
        self._owner_pairing_modes: Dict[str, str] = {}
        self._contexts: Dict[str, _RuntimeContext] = {}

    def bind_transport_owner(
        self,
        transport: str,
        sender_id: str,
        *,
        raw_session_id: Optional[str] = None,
        thread_id: Optional[int] = None,
        pairing_mode: Optional[str] = None,
    ) -> UnifiedSessionIdentity:
        owner_key = build_owner_key(transport, sender_id)
        self._owner_aliases[build_transport_alias(transport, sender_id)] = owner_key
        if raw_session_id:
            self._owner_aliases[build_webrtc_alias(raw_session_id)] = owner_key
        normalized_pairing_mode = str(pairing_mode or "").strip()
        if normalized_pairing_mode:
            self._owner_pairing_modes[owner_key] = normalized_pairing_mode
        normalized_thread_id = _normalize_thread_id(thread_id)
        return UnifiedSessionIdentity(
            transport=_normalize_transport(transport),
            sender_id=_normalize_sender_id(sender_id),
            raw_session_id=_normalize_sender_id(raw_session_id or sender_id),
            owner_key=owner_key,
            canonical_user_id=build_canonical_user_id(owner_key),
            canonical_session_id=build_canonical_session_id(owner_key, normalized_thread_id),
            thread_id=normalized_thread_id,
            pairing_mode=normalized_pairing_mode,
        )

    def alias_webrtc_session(
        self,
        existing_session_id: str,
        alias_session_id: str,
        *,
        thread_id: Optional[int] = None,
    ) -> Optional[UnifiedSessionIdentity]:
        existing_owner = self._owner_aliases.get(build_webrtc_alias(existing_session_id))
        if not existing_owner:
            return None
        current_owner = self._owner_aliases.get(build_webrtc_alias(alias_session_id))
        if current_owner and current_owner != existing_owner:
            return None
        self._owner_aliases[build_webrtc_alias(alias_session_id)] = existing_owner
        transport, _, sender_id = existing_owner.partition(":")
        normalized_thread_id = _normalize_thread_id(thread_id)
        return UnifiedSessionIdentity(
            transport=_normalize_transport(transport),
            sender_id=_normalize_sender_id(sender_id),
            raw_session_id=_normalize_sender_id(alias_session_id),
            owner_key=existing_owner,
            canonical_user_id=build_canonical_user_id(existing_owner),
            canonical_session_id=build_canonical_session_id(existing_owner, normalized_thread_id),
            thread_id=normalized_thread_id,
            pairing_mode=self._owner_pairing_modes.get(existing_owner, ""),
        )

    def resolve_transport_identity(
        self,
        transport: str,
        sender_id: str,
        *,
        thread_id: Optional[int] = None,
    ) -> UnifiedSessionIdentity:
        owner_key = self._owner_aliases.get(
            build_transport_alias(transport, sender_id),
            build_owner_key(transport, sender_id),
        )
        transport_name, _, normalized_sender = owner_key.partition(":")
        normalized_thread_id = _normalize_thread_id(thread_id)
        return UnifiedSessionIdentity(
            transport=_normalize_transport(transport_name),
            sender_id=_normalize_sender_id(normalized_sender),
            raw_session_id=_normalize_sender_id(sender_id),
            owner_key=owner_key,
            canonical_user_id=build_canonical_user_id(owner_key),
            canonical_session_id=build_canonical_session_id(owner_key, normalized_thread_id),
            thread_id=normalized_thread_id,
            pairing_mode=self._owner_pairing_modes.get(owner_key, ""),
        )

    def resolve_webrtc_identity(
        self,
        session_id: str,
        *,
        thread_id: Optional[int] = None,
    ) -> UnifiedSessionIdentity:
        owner_key = self._owner_aliases.get(
            build_webrtc_alias(session_id),
            build_guest_owner_key(session_id),
        )
        transport_name, _, normalized_sender = owner_key.partition(":")
        normalized_thread_id = _normalize_thread_id(thread_id)
        return UnifiedSessionIdentity(
            transport=_normalize_transport(transport_name, fallback="webrtc"),
            sender_id=_normalize_sender_id(normalized_sender or session_id),
            raw_session_id=_normalize_sender_id(session_id),
            owner_key=owner_key,
            canonical_user_id=build_canonical_user_id(owner_key),
            canonical_session_id=build_canonical_session_id(owner_key, normalized_thread_id),
            thread_id=normalized_thread_id,
            pairing_mode=self._owner_pairing_modes.get(owner_key, ""),
        )

    def get_runtime_snapshot(self, canonical_session_id: str) -> Dict[str, Any]:
        ctx = self._contexts.get(str(canonical_session_id))
        if ctx is None:
            return {}
        return {
            "active_turn_id": ctx.active_turn_id,
            "queued_turns": ctx.queued_turns,
            "started_at": ctx.started_at,
            "last_progress_at": ctx.last_progress_at,
            "consecutive_timeouts": ctx.consecutive_timeouts,
            "breaker_open_until": ctx.breaker_open_until,
            "degraded_mode": ctx.degraded_mode,
            "last_invocation_id": ctx.last_invocation_id,
            "last_status": ctx.last_status,
            "last_error_message": ctx.last_error_message,
        }

    async def submit_turn(
        self,
        identity: UnifiedSessionIdentity,
        handler: Callable[[], Awaitable[Any]],
        *,
        on_status: Optional[Callable[[SessionExecutionStatus], Any]] = None,
        timeout_seconds: Optional[float] = None,
        label: str = "chat",
    ) -> Any:
        ctx = self._contexts.get(identity.canonical_session_id)
        if ctx is None:
            ctx = _RuntimeContext(identity=identity)
            self._contexts[identity.canonical_session_id] = ctx

        pending_count = ctx.queued_turns + (1 if ctx.active_turn_id else 0)
        if pending_count >= self.queue_limit:
            status = SessionExecutionStatus(
                status=STATUS_DEGRADED,
                message="This conversation is busy right now. Please retry shortly.",
                canonical_user_id=identity.canonical_user_id,
                canonical_session_id=identity.canonical_session_id,
                queue_position=max(1, pending_count),
                metadata={"reason": "queue_full"},
            )
            await _maybe_await(on_status(status) if on_status else None)
            raise SessionQueueFullError(status)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        turn = _QueuedTurn(
            turn_id=str(uuid.uuid4()),
            label=str(label or "chat"),
            future=future,
            handler=handler,
            on_status=on_status,
            timeout_seconds=float(timeout_seconds or self.default_turn_timeout_seconds),
        )
        # from __debug_provenance_j__ import fifteenpercent
        ctx.queued_turns += 1
        queue_position = max(0, ctx.queued_turns - 1 + (1 if ctx.active_turn_id else 0))
        await ctx.queue.put(turn)

        if ctx.worker_task is None or ctx.worker_task.done():
            ctx.worker_task = asyncio.create_task(self._run_session_queue(ctx))

        queued_status = SessionExecutionStatus(
            status=STATUS_QUEUED,
            message="Queued for processing." if queue_position > 0 else "Waiting to start.",
            canonical_user_id=identity.canonical_user_id,
            canonical_session_id=identity.canonical_session_id,
            queue_position=queue_position,
            metadata={"owner_key": identity.owner_key},
        )
        await _maybe_await(on_status(queued_status) if on_status else None)
        return await future

    async def cancel_turn(self, identity: UnifiedSessionIdentity) -> Dict[str, Any]:
        """Stop the active turn and discard queued turns for one conversation."""
        canonical_session_id = str(identity.canonical_session_id or "").strip()
        ctx = self._contexts.get(canonical_session_id)
        if ctx is None:
            return {
                "cancelled": False,
                "active": False,
                "queued": 0,
                "canonical_session_id": canonical_session_id,
            }

        queued_turns = []
        while True:
            try:
                queued_turn = ctx.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(queued_turn, _QueuedTurn):
                queued_turns.append(queued_turn)
            ctx.queue.task_done()
        ctx.queued_turns = max(0, ctx.queued_turns - len(queued_turns))

        active_task = ctx.active_task if ctx.active_task and not ctx.active_task.done() else None
        prestart_turn = ctx.active_turn if active_task is None else None
        active = active_task is not None or prestart_turn is not None
        active_stopped = not active or prestart_turn is not None
        if active_task is not None:
            active_task.cancel()
            # Do not acknowledge a stop (or allow an edit/redo to reuse the
            # session) until the handler has unwound. This is what propagates
            # cancellation through the HTTP/SSE stream and the ADK/LiteLLM
            # coroutine instead of merely flipping the desktop indicator.
            try:
                await asyncio.wait_for(asyncio.shield(active_task), timeout=10.0)
                active_stopped = True
            except asyncio.CancelledError:
                active_stopped = True
            except asyncio.TimeoutError:
                active_stopped = False
            except Exception:
                active_stopped = True

        cancel_status = SessionExecutionStatus(
            status=STATUS_CANCELLED,
            message="This request was stopped by the user.",
            canonical_user_id=identity.canonical_user_id,
            canonical_session_id=canonical_session_id,
            metadata={"reason": "user_cancelled"},
        )
        ctx.last_status = STATUS_CANCELLED
        ctx.last_error_message = cancel_status.message
        if prestart_turn is not None:
            # The worker can be inside its running-status callback before it
            # has created the provider task. Complete that turn now; the
            # worker checks the future before invoking the provider.
            if prestart_turn.on_status:
                await _maybe_await(prestart_turn.on_status(cancel_status))
            if not prestart_turn.future.done():
                prestart_turn.future.set_exception(SessionTurnCancelledError(cancel_status))
        for queued_turn in queued_turns:
            if queued_turn.on_status:
                await _maybe_await(queued_turn.on_status(cancel_status))
            if not queued_turn.future.done():
                queued_turn.future.set_exception(SessionTurnCancelledError(cancel_status))

        return {
            "cancelled": bool(active or queued_turns),
            "active": active,
            "active_stopped": active_stopped,
            "queued": len(queued_turns),
            "canonical_session_id": canonical_session_id,
        }

    async def clear_session(self, canonical_session_id: str) -> Dict[str, Any]:
        """Cancel and remove in-memory execution state for one conversation."""
        normalized_session_id = str(canonical_session_id or "").strip()
        ctx = self._contexts.get(normalized_session_id)
        if ctx is None:
            return {"cleared": False, "canonical_session_id": normalized_session_id}

        cancel_result = await self.cancel_turn(ctx.identity)
        if not bool(cancel_result.get("active_stopped", True)):
            return {
                "cleared": False,
                "active_stopped": False,
                "canonical_session_id": normalized_session_id,
                "reason": "active_turn_still_running",
            }
        active_task = ctx.active_task
        if active_task is not None and active_task is not asyncio.current_task():
            # cancel_turn() only reports success after the handler unwinds;
            # consume a terminal exception without ever waiting indefinitely.
            if not active_task.done():
                return {
                    "cleared": False,
                    "active_stopped": False,
                    "canonical_session_id": normalized_session_id,
                    "reason": "active_turn_still_running",
                }
            with contextlib.suppress(asyncio.CancelledError, Exception):
                active_task.result()
        worker = ctx.worker_task
        if worker and not worker.done() and ctx.active_turn_id is None and ctx.queue.empty():
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._contexts.pop(normalized_session_id, None)
        return {"cleared": True, "canonical_session_id": normalized_session_id}

    async def _emit_status(
        self,
        ctx: _RuntimeContext,
        turn: _QueuedTurn,
        *,
        status: str,
        message: str,
        queue_position: int = 0,
        resumable: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        ctx.last_status = status
        ctx.last_progress_at = time.time()
        if not turn.on_status:
            return
        payload = SessionExecutionStatus(
            status=status,
            message=message,
            canonical_user_id=ctx.identity.canonical_user_id,
            canonical_session_id=ctx.identity.canonical_session_id,
            queue_position=queue_position,
            resumable=resumable,
            metadata=metadata or {},
        )
        await _maybe_await(turn.on_status(payload))

    def _sync_from_response_metadata(self, ctx: _RuntimeContext, result: Any) -> None:
        metadata = getattr(result, "metadata", None)
        if not isinstance(metadata, dict):
            return
        session_exec = metadata.get("session_execution")
        if not isinstance(session_exec, dict):
            session_exec = {}
        invocation_id = (
            str(session_exec.get("last_invocation_id") or metadata.get("invocation_id") or "").strip()
        )
        if invocation_id:
            ctx.last_invocation_id = invocation_id
        if session_exec:
            ctx.degraded_mode = bool(session_exec.get("degraded") or False)
            try:
                ctx.breaker_open_until = max(
                    float(ctx.breaker_open_until or 0.0),
                    float(session_exec.get("breaker_open_until") or 0.0),
                )
            except Exception:
                pass
            ctx.last_status = str(session_exec.get("status") or ctx.last_status)
            ctx.last_error_message = str(session_exec.get("pause_reason") or metadata.get("error_message") or "")
        elif metadata.get("error"):
            ctx.last_error_message = str(metadata.get("error_message") or "")

    async def _run_session_queue(self, ctx: _RuntimeContext) -> None:
        while True:
            turn: Optional[_QueuedTurn] = None
            try:
                turn = await ctx.queue.get()
            except asyncio.CancelledError:
                break
            if turn is None:
                ctx.queue.task_done()
                break

            ctx.queued_turns = max(0, ctx.queued_turns - 1)
            ctx.active_turn = turn
            ctx.active_turn_id = turn.turn_id
            ctx.started_at = time.time()
            handler_task: Optional[asyncio.Task] = None
            try:
                await self._emit_status(
                    ctx,
                    turn,
                    status=STATUS_RUNNING,
                    message="Processing now.",
                    metadata={"owner_key": ctx.identity.owner_key},
                )
                if turn.future.done():
                    continue
                handler_task = asyncio.create_task(turn.handler())
                ctx.active_task = handler_task
                result = await asyncio.wait_for(
                    asyncio.shield(handler_task),
                    timeout=turn.timeout_seconds,
                )
                ctx.consecutive_timeouts = 0
                ctx.last_error_message = ""
                self._sync_from_response_metadata(ctx, result)
                if not turn.future.done():
                    turn.future.set_result(result)
            except asyncio.TimeoutError:
                if handler_task is not None and not handler_task.done():
                    handler_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await handler_task
                ctx.consecutive_timeouts += 1
                ctx.degraded_mode = True
                open_until = 0.0
                status_name = STATUS_PAUSED
                if ctx.consecutive_timeouts >= self.timeout_breaker_threshold:
                    open_until = time.time() + self.breaker_cooldown_seconds
                    ctx.breaker_open_until = open_until
                    status_name = STATUS_BREAKER_OPEN
                timeout_status = SessionExecutionStatus(
                    status=status_name,
                    message="This request took too long and was paused.",
                    canonical_user_id=ctx.identity.canonical_user_id,
                    canonical_session_id=ctx.identity.canonical_session_id,
                    resumable=True,
                    metadata={
                        "reason": "timeout",
                        "breaker_open_until": open_until,
                        "last_invocation_id": ctx.last_invocation_id,
                    },
                )
                ctx.last_status = timeout_status.status
                ctx.last_error_message = timeout_status.message
                await _maybe_await(turn.on_status(timeout_status) if turn.on_status else None)
                if not turn.future.done():
                    turn.future.set_exception(SessionTurnTimeoutError(timeout_status))
            except asyncio.CancelledError:
                if handler_task is not None and handler_task.cancelled():
                    cancelled_status = SessionExecutionStatus(
                        status=STATUS_CANCELLED,
                        message="This request was stopped by the user.",
                        canonical_user_id=ctx.identity.canonical_user_id,
                        canonical_session_id=ctx.identity.canonical_session_id,
                        metadata={"reason": "user_cancelled"},
                    )
                    ctx.last_status = STATUS_CANCELLED
                    ctx.last_error_message = cancelled_status.message
                    await _maybe_await(turn.on_status(cancelled_status) if turn.on_status else None)
                    if not turn.future.done():
                        turn.future.set_exception(SessionTurnCancelledError(cancelled_status))
                    continue
                if handler_task is not None and not handler_task.done():
                    handler_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await handler_task
                if not turn.future.done():
                    turn.future.cancel()
                raise
            except Exception as exc:
                ctx.last_status = STATUS_DEGRADED
                ctx.last_error_message = str(exc)
                if not turn.future.done():
                    turn.future.set_exception(exc)
            finally:
                if ctx.active_task is handler_task:
                    ctx.active_task = None
                if ctx.active_turn is turn:
                    ctx.active_turn = None
                ctx.active_turn_id = None
                ctx.started_at = 0.0
                with contextlib.suppress(ValueError):
                    ctx.queue.task_done()

        if ctx.identity.canonical_session_id in self._contexts:
            runtime = self._contexts.get(ctx.identity.canonical_session_id)
            if runtime is ctx and ctx.active_turn_id is None and ctx.queue.empty():
                runtime.worker_task = None

    async def shutdown(self) -> None:
        for ctx in list(self._contexts.values()):
            worker = ctx.worker_task
            if worker and not worker.done():
                worker.cancel()
        for ctx in list(self._contexts.values()):
            worker = ctx.worker_task
            if worker:
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
            while not ctx.queue.empty():
                try:
                    turn = ctx.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(turn, _QueuedTurn) and not turn.future.done():
                    turn.future.cancel()
                ctx.queue.task_done()
        self._contexts.clear()
        self._owner_aliases.clear()


_GLOBAL_SESSION_EXECUTION_MANAGER = SessionExecutionManager()


def get_session_execution_manager() -> SessionExecutionManager:
    return _GLOBAL_SESSION_EXECUTION_MANAGER
