# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-72d342e42ceb185386f0b106


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-72d342e42ceb185386f0b106"


try:
    from google.adk.tools import ToolContext
except Exception:  # pragma: no cover - fallback for environments without ADK
    ToolContext = Any  # type: ignore

from service_manager import get_service_manager
from shared.adk_state import (
    AUTOYOU_CONVERSATION_SESSION_STATE_KEY,
    AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY,
    AUTOYOU_OWNER_KEY_STATE_KEY,
    AUTOYOU_OWNER_KEY_USER_STATE_KEY,
    state_get_first,
)
from shared.session_execution import SESSION_CONTROL_STATE_KEY, normalize_session_control_state

logger = logging.getLogger(__name__)


_GENERIC_USER_IDS = {
    "user",
    "unknown",
    "autoyou-client",
    "autoyou_client",
    "client",
    "default",
}

_FULL_SCAN_QUERY_TOKENS = {
    "*",
    "__all__",
    "all",
    "all memory",
    "entire memory",
    "full memory",
}


def _normalize_id(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _is_generic_user_id(value: Optional[str]) -> bool:
    text = _normalize_id(value)
    return bool(text and text.lower() in _GENERIC_USER_IDS)


def _strict_single_user_mode_enabled(service_manager: Any) -> bool:
    try:
        return bool(getattr(getattr(service_manager, "config", None), "strict_single_user_mode", False))
    except Exception:
        return False


def _context_state(tool_context: ToolContext) -> Dict[str, Any]:
    try:
        state_obj = getattr(tool_context, "state", None)
        if hasattr(state_obj, "to_dict"):
            state = state_obj.to_dict()
        elif isinstance(state_obj, dict):
            state = state_obj
        else:
            state = {}
        return dict(state) if isinstance(state, dict) else {}
    except Exception:
        return {}


def _extract_runtime_context(tool_context: ToolContext) -> tuple[Optional[str], Optional[str]]:
    """Best-effort extraction of runtime user/session IDs from ADK context."""
    user_id: Optional[str] = None
    session_id: Optional[str] = None

    candidates = [tool_context]
    try:
        candidates.append(getattr(tool_context, "_invocation_context", None))
    except Exception:
        pass
    try:
        candidates.append(getattr(tool_context, "invocation_context", None))
    except Exception:
        pass

    for ctx in candidates:
        if ctx is None:
            continue
        try:
            if not user_id:
                user_id = _normalize_id(getattr(ctx, "user_id", None))
        except Exception:
            pass
        try:
            session = getattr(ctx, "session", None)
            if session is not None:
                if not session_id:
                    session_id = _normalize_id(getattr(session, "id", None) or getattr(session, "session_id", None))
                if not user_id:
                    user_id = _normalize_id(getattr(session, "user_id", None))
        except Exception:
            pass
        try:
            if not session_id:
                session_id = _normalize_id(getattr(ctx, "session_id", None))
        except Exception:
            pass

    # Last-chance extraction from state-like containers.
    state = _context_state(tool_context)
    if state:
        if not user_id:
            user_id = _normalize_id(state.get("user_id") or state.get("app:user_id"))
        if not session_id:
            session_id = _normalize_id(state.get("session_id") or state.get("app:session_id"))

    return _normalize_id(user_id), _normalize_id(session_id)


def _memory_metadata_from_context_state(tool_context: ToolContext) -> Dict[str, Any]:
    state = _context_state(tool_context)
    if not state:
        return {}

    control_state = normalize_session_control_state(state_get_first(state, SESSION_CONTROL_STATE_KEY))
    owner_key = str(
        control_state.get("owner_key")
        or state_get_first(state, AUTOYOU_OWNER_KEY_STATE_KEY, AUTOYOU_OWNER_KEY_USER_STATE_KEY)
        or ""
    ).strip()
    conversation_session_id = str(
        state_get_first(
            state,
            AUTOYOU_CONVERSATION_SESSION_STATE_KEY,
            AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY,
        )
        or ""
    ).strip()

    metadata = {
        "canonical_owner_key": owner_key,
        "owner_key": owner_key,
        "canonical_user_id": str(control_state.get("canonical_user_id") or "").strip(),
        "canonical_session_id": str(control_state.get("canonical_session_id") or "").strip(),
        "conversation_session_id": conversation_session_id,
    }
    return {k: v for k, v in metadata.items() if v not in ("", None)}


def _is_full_scan_query(query: str) -> bool:
    return str(query or "").strip().lower() in _FULL_SCAN_QUERY_TOKENS


def _normalize_limit(limit: Any, default_limit: int = 8) -> int:
    """Normalize memory result limit.

    - `limit > 0`: return up to that many matches
    - `limit <= 0`: scan/return all matches
    """
    if limit is None:
        return default_limit

    # Accept common string forms used by model/tool callers.
    if isinstance(limit, str):
        token = limit.strip().lower()
        if token in _FULL_SCAN_QUERY_TOKENS:
            return 0

    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        return default_limit

    if parsed <= 0:
        return 0
    return max(1, parsed)


def _fallback_from_session_events(
    session_data: Optional[Dict[str, Any]],
    query: str,
    limit: int,
    full_scan_query: bool = False,
) -> List[Dict[str, Any]]:
    """Fallback retrieval using locally persisted session events when indexed memory is empty."""
    if not session_data:
        return []
    events = session_data.get("events") or []
    if not isinstance(events, list):
        return []

    terms = [t.strip().lower() for t in str(query).split() if t.strip()]
    out: List[Dict[str, Any]] = []
    for ev in reversed(events):
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        text = f"{data.get('user_message', '')}\n{data.get('agent_response', '')}".strip()
        if not text:
            continue
        text_l = text.lower()
        if not full_scan_query and terms and not any(t in text_l for t in terms):
            continue
        out.append(
            {
                "content": text[:1200],
                "timestamp": ev.get("timestamp"),
                "source": "session_events_fallback",
            }
        )
        if limit > 0 and len(out) >= limit:
            break
    return out


def _resolve_memory_scope(
    *,
    tool_context: ToolContext,
    service_manager: Any,
    session_manager: Any,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    external_session_id: Optional[str] = None,
    strict_current_session: bool = False,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve the memory user/session, retaining the runtime thread when required."""
    runtime_user_id, runtime_session_id = _extract_runtime_context(tool_context)
    explicit_user_id = _normalize_id(user_id)
    explicit_session_id = _normalize_id(session_id)
    resolved_user_id = runtime_user_id or explicit_user_id
    resolved_session_id = runtime_session_id or (None if strict_current_session else explicit_session_id)
    allow_single_user_fallback = _strict_single_user_mode_enabled(service_manager)

    # If caller passes an external session id, resolve to internal session id.
    if external_session_id and not strict_current_session:
        try:
            mapped = session_manager.get_mapped_session_id(external_session_id, resolved_user_id)
            if mapped:
                resolved_session_id = mapped
        except Exception as e:
            logger.warning("Failed to resolve external session mapping: %s", e)

    # Recover user_id from session if runtime context omitted it.
    if not resolved_user_id and resolved_session_id and hasattr(session_manager, "get_user_id_for_session"):
        try:
            resolved_user_id = _normalize_id(session_manager.get_user_id_for_session(resolved_session_id))
        except Exception as e:
            logger.warning("Failed to resolve user_id from session_id: %s", e)

    # If model passed a generic user id ("user"), prefer IDs inferred from session/DB.
    if _is_generic_user_id(resolved_user_id):
        if resolved_session_id and hasattr(session_manager, "get_user_id_for_session"):
            try:
                inferred = _normalize_id(session_manager.get_user_id_for_session(resolved_session_id))
                if inferred:
                    resolved_user_id = inferred
            except Exception:
                pass
        if (
            allow_single_user_fallback and
            _is_generic_user_id(resolved_user_id) and
            hasattr(session_manager, "get_single_known_user_id")
        ):
            try:
                inferred_single = _normalize_id(session_manager.get_single_known_user_id())
                if inferred_single:
                    logger.info("Using strict single-user memory fallback for unresolved generic user_id")
                    resolved_user_id = inferred_single
            except Exception:
                pass

    if (
        allow_single_user_fallback and
        not resolved_user_id and
        hasattr(session_manager, "get_single_known_user_id")
    ):
        try:
            inferred_single = _normalize_id(session_manager.get_single_known_user_id())
            if inferred_single:
                logger.info("Using strict single-user memory fallback for missing user_id")
                resolved_user_id = inferred_single
        except Exception:
            pass

    return _normalize_id(resolved_user_id), _normalize_id(resolved_session_id)


def _default_memory_session_id(user_id: str) -> str:
    digest = hashlib.sha256(str(user_id).encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"memory-note-{digest}"


async def remember_long_term_memory(
    tool_context: ToolContext,
    content: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    external_session_id: Optional[str] = None,
    source: str = "memory_tool",
) -> Dict[str, Any]:
    """Store an explicit durable memory through AutoYou's active memory backend."""
    note = str(content or "").strip()
    if not note:
        return {"status": "error", "message": "Memory content is required."}

    sm = get_service_manager()
    # from __debug_provenance_v__ import wallet
    session_manager = sm.get_session_manager()
    if not hasattr(session_manager, "add_session_event"):
        return {"status": "error", "message": "Memory storage is unavailable."}

    resolved_user_id, resolved_session_id = _resolve_memory_scope(
        tool_context=tool_context,
        service_manager=sm,
        session_manager=session_manager,
        user_id=user_id,
        session_id=session_id,
        external_session_id=external_session_id,
    )

    if not resolved_user_id:
        return {
            "status": "error",
            "message": "Could not resolve user_id from runtime context. Pass user_id explicitly.",
        }

    if not resolved_session_id:
        resolved_session_id = _default_memory_session_id(resolved_user_id)

    source_label = str(source or "memory_tool").strip() or "memory_tool"
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    normalized_external_session_id = _normalize_id(external_session_id)
    memory_metadata = {
        "source": source_label,
        "source_user_id": resolved_user_id,
        "adk_session_id": resolved_session_id,
        "external_session_id": normalized_external_session_id,
        "memory_event_type": "explicit_memory_note",
    }
    memory_metadata.update(_memory_metadata_from_context_state(tool_context))
    event_data = {
        "user_message": note,
        "agent_response": "",
        "timestamp": timestamp,
        "source": source_label,
        "memory_metadata": memory_metadata,
    }

    try:
        ok = await session_manager.add_session_event(
            user_id=resolved_user_id,
            session_id=resolved_session_id,
            event_type="memory_note",
            event_data=event_data,
            external_session_id=normalized_external_session_id,
        )
    except Exception as e:
        logger.warning("remember_long_term_memory failed: %s", e)
        ok = False

    if not ok:
        return {"status": "error", "message": "Failed to store memory."}

    return {
        "status": "success",
        "message": "Stored memory.",
        "source": source_label,
        "user_id": resolved_user_id,
        "session_id": resolved_session_id,
        "external_session_id": normalized_external_session_id,
        "content": note,
    }


async def fetch_long_term_memory(
    tool_context: ToolContext,
    query: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    external_session_id: Optional[str] = None,
    limit: int = 8,
    scope_to_current_session: bool = False,
) -> Dict[str, Any]:
    """Search long-term memory wired through session_utils/service_manager.

    This tool searches AutoYou's active memory backend through
    MemoryIntegratedSessionManager and falls back to local session event
    history when indexed memory has no hits. The default lookup spans the
    current user's stored history. Set `scope_to_current_session=True` for
    a strict logical-conversation lookup.

    Limit semantics:
    - `limit > 0`: return up to N matching items
    - `limit <= 0`: return all matching items
    - `query="*"` (or `all memory`): scan all stored memory rows in the
      selected scope
    """
    max_limit = _normalize_limit(limit, default_limit=8)
    q = str(query or "").strip()
    if not q:
        if max_limit == 0:
            # Empty query + full-scan limit means "scan everything".
            q = "*"
        else:
            return {"status": "error", "message": "Query is required"}
    full_scan_query = _is_full_scan_query(q)

    sm = get_service_manager()
    session_manager = sm.get_session_manager()
    resolved_user_id, resolved_session_id = _resolve_memory_scope(
        tool_context=tool_context,
        service_manager=sm,
        session_manager=session_manager,
        user_id=user_id,
        session_id=session_id,
        external_session_id=external_session_id,
        strict_current_session=scope_to_current_session,
    )

    if not resolved_user_id:
        return {
            "status": "error",
            "message": "Could not resolve user_id from runtime context. Pass user_id explicitly.",
        }
    if scope_to_current_session and not resolved_session_id:
        return {
            "status": "error",
            "message": "Could not resolve the current session for a session-scoped memory lookup.",
        }

    memory_results: List[Dict[str, Any]] = []
    try:
        memory_results = await session_manager.search_memory(
            user_id=resolved_user_id,
            query=q,
            limit=max_limit,
            session_id=resolved_session_id if scope_to_current_session else None,
        )
    except Exception as e:
        logger.warning("search_memory failed: %s", e)

    if memory_results:
        source_label = str(memory_results[0].get("source") or "memory_search_index")
        return {
            "status": "success",
            "source": source_label,
            "user_id": resolved_user_id,
            "session_id": resolved_session_id,
            "limit": "all" if max_limit == 0 else max_limit,
            "count": len(memory_results),
            "results": memory_results,
        }

    # Fallback to persisted local session events for current session, if available.
    fallback = _fallback_from_session_events(
        await session_manager.get_user_session(resolved_user_id, resolved_session_id)
        if resolved_session_id
        else None,
        q,
        max_limit,
        full_scan_query=full_scan_query,
    )
    return {
        "status": "success",
        "source": "session_events_fallback",
        "user_id": resolved_user_id,
        "session_id": resolved_session_id,
        "limit": "all" if max_limit == 0 else max_limit,
        "count": len(fallback),
        "results": fallback,
        "message": "No indexed memory hits; returned fallback session history snippets.",
    }


async def scan_entire_memory(
    tool_context: ToolContext,
    query: str = "*",
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    external_session_id: Optional[str] = None,
    scope_to_current_session: bool = True,
) -> Dict[str, Any]:
    """Scan all matching memory in the current logical conversation by default.

    Set ``scope_to_current_session=False`` only after the user explicitly asks
    to search across their prior conversations.
    """
    return await fetch_long_term_memory(
        tool_context=tool_context,
        query=query or "*",
        user_id=user_id,
        session_id=session_id,
        external_session_id=external_session_id,
        limit=0,
        scope_to_current_session=scope_to_current_session,
    )


async def scan_all_client_memory(
    tool_context: ToolContext,
    query: str = "*",
    limit: int = 0,
) -> Dict[str, Any]:
    """Privileged owner-level memory search across all connected client identities."""
    q = str(query or "*").strip() or "*"
    max_limit = _normalize_limit(limit, default_limit=0)

    sm = get_service_manager()
    if not _strict_single_user_mode_enabled(sm):
        return {
            "status": "error",
            "message": "All-client memory search requires strict single-user mode.",
        }

    session_manager = sm.get_session_manager()
    if not hasattr(session_manager, "search_all_memory"):
        return {"status": "error", "message": "All-client memory search is unavailable."}

    try:
        memory_results = await session_manager.search_all_memory(q, max_limit)
    except Exception as e:
        logger.warning("search_all_memory failed: %s", e)
        return {"status": "error", "message": "All-client memory search failed."}

    source_label = str(memory_results[0].get("source") or "memory_search_index") if memory_results else "memory_search_index"
    return {
        "status": "success",
        "source": source_label,
        "scope": "all_clients",
        "limit": "all" if max_limit == 0 else max_limit,
        "count": len(memory_results),
        "results": memory_results,
    }
