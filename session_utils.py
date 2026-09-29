# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-e74657007740934cca684451


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import os
import json
import logging
import sqlite3
import threading
import uuid
import asyncio
import copy
from datetime import datetime
from typing import Dict, Optional, Any, List
import re
from pathlib import Path
from shared.session_execution import build_canonical_session_id
from shared.platform_runtime import get_config_dir

# ADK Core Imports
from google.adk.events import Event, EventActions

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-e74657007740934cca684451"


logger = logging.getLogger(__name__)


def _default_sessions_db_path() -> str:
    return str((get_config_dir("AutoYou", anchor=__file__) / "sessions.db").resolve())

# ADK memory service removed. Memory is handled by AutoYou's SQLite index plus optional Cognee mirror.

_AUTOYOU_APP_NAME = "autoyou_agents"


def _cognee_search_timeout_seconds() -> float:
    raw = (
        os.getenv("AUTOYOU_COGNEE_SEARCH_TIMEOUT_SECONDS")
        or os.getenv("AUTOYOU_COGNEE_MEMORY_SEARCH_TIMEOUT_SECONDS")
        or "8"
    )
    try:
        return max(0.05, float(raw))
    except (TypeError, ValueError):
        return 8.0


def _cognee_mirror_delay_seconds() -> float:
    raw = os.getenv("AUTOYOU_COGNEE_MIRROR_DELAY_SECONDS") or "0"
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _looks_like_existing_adk_session_error(error: Exception) -> bool:
    message = str(error or "").strip().lower()
    return "session" in message and "already exists" in message

class MemoryIntegratedSessionManager:
    """
    Enhanced session manager wrapping Google ADK DatabaseSessionService.
    Manages user sessions securely without legacy SQL collisions, 
    persisting natively through ADK events.
    """
    
    def __init__(self, 
                 db_path: Optional[str] = None,
                 record_messages: bool = True,
                 adk_session_service=None,
                 cognee_memory_enabled: Optional[bool] = None,
                 cognee_memory_service=None):
        self.db_path = os.path.abspath(os.path.expanduser(str(db_path or _default_sessions_db_path())))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.record_messages = record_messages

        self.enable_memory = True # Memory is natively handled
        self._session_mapping_cache: Dict[str, str] = {}
        self._conversation_thread_cache: Dict[str, int] = {}
        self.adk_session_service = adk_session_service
        self._cognee_memory = cognee_memory_service
        self._cognee_mirror_tasks: set[asyncio.Task] = set()
        self._cognee_search_timeout_seconds = _cognee_search_timeout_seconds()
        self._cognee_mirror_delay_seconds = _cognee_mirror_delay_seconds()
        self._cognee_mirror_semaphore = asyncio.Semaphore(1)
        if self._cognee_memory is None:
            if cognee_memory_enabled is None:
                try:
                    from shared.cognee_memory import cognee_memory_enabled as _cognee_enabled
                    cognee_memory_enabled = _cognee_enabled()
                except Exception:
                    cognee_memory_enabled = False
            if cognee_memory_enabled:
                try:
                    from shared.cognee_memory import CogneeMemoryService
                    self._cognee_memory = CogneeMemoryService(Path(self.db_path).parent / "cognee")
                except Exception as e:
                    logger.warning("Cognee memory service unavailable: %s", e)
        
        self._init_mapping_database()

        # Configure ADK if not provided.
        if not self.adk_session_service:
            self._configure_adk_session_service()

    def close(self) -> None:
        for task in list(self._cognee_mirror_tasks):
            task.cancel()
        self._cognee_mirror_tasks.clear()

    def _schedule_cognee_memory_mirror(self, memory_record: Dict[str, Any]) -> None:
        if self._cognee_memory is None:
            return

        async def _mirror() -> None:
            try:
                if self._cognee_mirror_delay_seconds > 0:
                    await asyncio.sleep(self._cognee_mirror_delay_seconds)
                async with self._cognee_mirror_semaphore:
                    await self._cognee_memory.remember(memory_record)
            except asyncio.CancelledError:
                raise
            except Exception as cognee_error:
                logger.warning("Cognee memory mirror failed: %s", cognee_error)

        task = asyncio.create_task(_mirror())
        self._cognee_mirror_tasks.add(task)
        task.add_done_callback(self._cognee_mirror_tasks.discard)
    
    def _configure_adk_session_service(self):
        """Configure ADK DatabaseSessionService natively using a connection URL."""
        try:
            from google.adk.sessions import DatabaseSessionService
            abs_path = os.path.abspath(os.path.expanduser(self.db_path))
            abs_posix = abs_path.replace("\\", "/")
            if os.name == "nt" and re.match(r"^[a-zA-Z]:/[a-zA-Z]:/", abs_posix):
                abs_posix = abs_posix[3:]
            db_url = f"sqlite+aiosqlite:///{abs_posix}"
            
            self.adk_session_service = DatabaseSessionService(
                db_url=db_url,
                connect_args={"timeout": 15.0, "check_same_thread": False}
            )
            logger.info(f"ADK DatabaseSessionService initialized natively with {self.db_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize DatabaseSessionService: {e}")
            raise
    

    def _init_mapping_database(self):
        """Initialize SQLite tables used for mappings and denormalized memory search."""
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS session_mappings (
                        external_session_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Indexes for fast resolution
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_mappings_session_id 
                    ON session_mappings(session_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_mappings_user_id 
                    ON session_mappings(user_id)
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS memory_search (
                        event_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        external_session_id TEXT,
                        event_type TEXT NOT NULL,
                        timestamp_text TEXT NOT NULL,
                        sort_timestamp REAL NOT NULL,
                        user_message TEXT,
                        agent_response TEXT,
                        content TEXT NOT NULL,
                        normalized_content TEXT NOT NULL,
                        metadata_json TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(memory_search)").fetchall()
                }
                if "metadata_json" not in columns:
                    conn.execute("ALTER TABLE memory_search ADD COLUMN metadata_json TEXT")
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_memory_search_user_time
                    ON memory_search(user_id, sort_timestamp DESC)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_memory_search_user_session_time
                    ON memory_search(user_id, session_id, sort_timestamp DESC)
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS conversation_threads (
                        owner_key TEXT PRIMARY KEY,
                        current_thread INTEGER NOT NULL DEFAULT 1,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_conversation_threads_updated_at
                    ON conversation_threads(updated_at)
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS session_owners (
                        session_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        external_session_id TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_session_owners_user_id
                    ON session_owners(user_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_session_owners_external_session_id
                    ON session_owners(external_session_id)
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS session_context_usage (
                        session_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        external_session_id TEXT,
                        available INTEGER NOT NULL DEFAULT 0,
                        prompt_tokens INTEGER,
                        candidates_tokens INTEGER,
                        total_tokens INTEGER,
                        cached_tokens INTEGER,
                        thoughts_tokens INTEGER,
                        tool_use_prompt_tokens INTEGER,
                        context_window INTEGER,
                        snapshot_json TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_session_context_usage_user_id
                    ON session_context_usage(user_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_session_context_usage_external_session_id
                    ON session_context_usage(external_session_id)
                """)
                conn.commit()
                logger.info(f"Mapping database verified at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize mapping database: {e}")

    @staticmethod
    def _new_event_id(prefix: str) -> str:
        """Generate a unique event id to satisfy ADK event PK uniqueness."""
        safe_prefix = (str(prefix or "event").strip() or "event").replace(" ", "_")
        return f"{safe_prefix}_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _normalize_memory_text(value: Any) -> str:
        return " ".join(str(value or "").lower().split())

    @staticmethod
    def _coerce_sort_timestamp(value: Any) -> float:
        if value is None:
            return float(datetime.now().timestamp())
        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip()
        if not text:
            return float(datetime.now().timestamp())
        try:
            return float(text)
        except (TypeError, ValueError):
            pass
        try:
            return float(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
        except Exception:
            return float(datetime.now().timestamp())

    @staticmethod
    def _coerce_optional_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            parsed = int(value)
        except Exception:
            return None
        return parsed if parsed >= 0 else None

    @classmethod
    def _build_memory_search_record(
        cls,
        *,
        event_id: str,
        user_id: str,
        session_id: str,
        event_type: str,
        event_data: Dict[str, Any],
        external_session_id: Optional[str] = None,
        sort_timestamp: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        raw_data = dict(event_data or {})
        user_message = str(raw_data.get("user_message", "") or "")
        agent_response = str(raw_data.get("agent_response", "") or "")
        content = f"{user_message}\n{agent_response}".strip()
        if not content:
            return None

        timestamp_text = str(raw_data.get("timestamp") or "").strip()
        effective_sort_timestamp = cls._coerce_sort_timestamp(
            timestamp_text or sort_timestamp
        )
        if not timestamp_text:
            timestamp_text = datetime.fromtimestamp(effective_sort_timestamp).isoformat()
        attachments = raw_data.get("attachments") if isinstance(raw_data.get("attachments"), list) else []
        has_voice = "voice" in str(event_type or "").lower() or any(
            str((item or {}).get("mimetype") or "").lower().startswith("audio/")
            or str(((item or {}).get("meta") or {}).get("kind") or "").lower() == "audio"
            for item in attachments
            if isinstance(item, dict)
        )

        return {
            "event_id": str(event_id),
            "user_id": str(user_id),
            "session_id": str(session_id),
            "external_session_id": str(external_session_id or raw_data.get("external_session_id") or "") or None,
            "event_type": str(event_type or raw_data.get("_event_type") or "unknown"),
            "timestamp_text": timestamp_text,
            "sort_timestamp": effective_sort_timestamp,
            "user_message": user_message,
            "agent_response": agent_response,
            "content": content,
            "normalized_content": cls._normalize_memory_text(content),
            "metadata": {
                **(raw_data.get("memory_metadata") if isinstance(raw_data.get("memory_metadata"), dict) else {}),
                **{
                    key: raw_data.get(key)
                    for key in (
                        "client",
                        "source",
                        "source_user_id",
                        "external_session_id",
                        "conversation_session_id",
                        "conversation_thread_id",
                        "canonical_owner_key",
                        "owner_key",
                        "canonical_user_id",
                        "canonical_session_id",
                        "destination_session_id",
                        "raw_session_id",
                        "pairing_mode",
                        "pairing_type",
                        "pair_mode",
                        "server_name",
                        "server_id",
                        "server_identity_key",
                        "adk_session_id",
                    )
                    if raw_data.get(key) not in ("", None)
                },
                "has_files": bool(attachments),
                "has_voice": bool(has_voice),
            },
        }

    def _upsert_memory_search_record(self, record: Dict[str, Any]) -> None:
        if not record:
            return
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                conn.execute(
                    """
                    INSERT INTO memory_search (
                        event_id,
                        user_id,
                        session_id,
                        external_session_id,
                        event_type,
                        timestamp_text,
                        sort_timestamp,
                        user_message,
                        agent_response,
                        content,
                        normalized_content,
                        metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        user_id=excluded.user_id,
                        session_id=excluded.session_id,
                        external_session_id=excluded.external_session_id,
                        event_type=excluded.event_type,
                        timestamp_text=excluded.timestamp_text,
                        sort_timestamp=excluded.sort_timestamp,
                        user_message=excluded.user_message,
                        agent_response=excluded.agent_response,
                        content=excluded.content,
                        normalized_content=excluded.normalized_content,
                        metadata_json=excluded.metadata_json
                    """,
                    (
                        record["event_id"],
                        record["user_id"],
                        record["session_id"],
                        record.get("external_session_id"),
                        record["event_type"],
                        record["timestamp_text"],
                        record["sort_timestamp"],
                        record.get("user_message"),
                        record.get("agent_response"),
                        record["content"],
                        record["normalized_content"],
                        json.dumps(record.get("metadata") or {}, sort_keys=True, default=str),
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning("Failed to update denormalized memory_search index: %s", e)

    def _upsert_session_owner(
        self,
        *,
        session_id: str,
        user_id: str,
        external_session_id: Optional[str] = None,
    ) -> None:
        normalized_session_id = str(session_id or "").strip()
        normalized_user_id = str(user_id or "").strip()
        normalized_external_session_id = str(external_session_id or "").strip() or None
        if not normalized_session_id or not normalized_user_id:
            return
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                conn.execute(
                    """
                    INSERT INTO session_owners (session_id, user_id, external_session_id, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(session_id) DO UPDATE SET
                        user_id=excluded.user_id,
                        external_session_id=COALESCE(excluded.external_session_id, session_owners.external_session_id),
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (normalized_session_id, normalized_user_id, normalized_external_session_id),
                )
                conn.commit()
        except Exception as e:
            logger.warning("Failed to persist session owner for %s: %s", normalized_session_id, e)

    def upsert_session_context_usage_snapshot(
        self,
        *,
        session_id: str,
        user_id: str,
        snapshot: Dict[str, Any],
        external_session_id: Optional[str] = None,
    ) -> None:
        normalized_session_id = str(session_id or "").strip()
        normalized_user_id = str(user_id or "").strip()
        normalized_external_session_id = str(external_session_id or "").strip() or None
        if not normalized_session_id or not normalized_user_id or not isinstance(snapshot, dict):
            return

        try:
            serialized_snapshot = json.dumps(snapshot, sort_keys=True)
        except Exception:
            logger.warning(
                "Failed to serialize context-usage snapshot for %s; skipping persist",
                normalized_session_id,
            )
            return

        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                conn.execute(
                    """
                    INSERT INTO session_context_usage (
                        session_id,
                        user_id,
                        external_session_id,
                        available,
                        prompt_tokens,
                        candidates_tokens,
                        total_tokens,
                        cached_tokens,
                        thoughts_tokens,
                        tool_use_prompt_tokens,
                        context_window,
                        snapshot_json,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(session_id) DO UPDATE SET
                        user_id=excluded.user_id,
                        external_session_id=COALESCE(excluded.external_session_id, session_context_usage.external_session_id),
                        available=excluded.available,
                        prompt_tokens=excluded.prompt_tokens,
                        candidates_tokens=excluded.candidates_tokens,
                        total_tokens=excluded.total_tokens,
                        cached_tokens=excluded.cached_tokens,
                        thoughts_tokens=excluded.thoughts_tokens,
                        tool_use_prompt_tokens=excluded.tool_use_prompt_tokens,
                        context_window=excluded.context_window,
                        snapshot_json=excluded.snapshot_json,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        normalized_session_id,
                        normalized_user_id,
                        normalized_external_session_id,
                        1 if bool(snapshot.get("available")) else 0,
                        self._coerce_optional_int(snapshot.get("prompt_tokens")),
                        self._coerce_optional_int(snapshot.get("candidates_tokens")),
                        self._coerce_optional_int(snapshot.get("total_tokens")),
                        self._coerce_optional_int(snapshot.get("cached_tokens")),
                        self._coerce_optional_int(snapshot.get("thoughts_tokens")),
                        self._coerce_optional_int(snapshot.get("tool_use_prompt_tokens")),
                        self._coerce_optional_int(snapshot.get("context_window")),
                        serialized_snapshot,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning(
                "Failed to persist context-usage snapshot for %s: %s",
                normalized_session_id,
                e,
            )

    def get_session_context_usage_snapshot(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_session_id = str(session_id or "").strip()
        normalized_user_id = str(user_id or "").strip()
        if not normalized_session_id:
            return None

        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                if normalized_user_id:
                    row = conn.execute(
                        """
                        SELECT snapshot_json
                        FROM session_context_usage
                        WHERE session_id = ? AND user_id = ?
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (normalized_session_id, normalized_user_id),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT snapshot_json
                        FROM session_context_usage
                        WHERE session_id = ?
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (normalized_session_id,),
                    ).fetchone()
                if row and row[0]:
                    payload = json.loads(str(row[0]))
                    if isinstance(payload, dict):
                        return payload
        except Exception as e:
            logger.debug(
                "Failed to read context-usage snapshot for %s: %s",
                normalized_session_id,
                e,
            )
        return None
    
    async def create_user_session(self, user_id: str, session_id: str, initial_state: Dict[str, Any], external_session_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new ADK Session."""
        try:
            if not self.adk_session_service:
                logger.warning("ADK session service not bound, session creation aborted")
                return {}

            # Execute Native ADK Create
            await self.adk_session_service.create_session(
                app_name=_AUTOYOU_APP_NAME,
                user_id=user_id,
                session_id=session_id,
                state={**initial_state, "message_count": 0}
            )
            logger.info(f"ADK session {session_id} created for user {user_id}")
            self._upsert_session_owner(
                session_id=session_id,
                user_id=user_id,
                external_session_id=external_session_id,
            )
            
            # Map external session natively
            if external_session_id:
                self.set_session_mapping(external_session_id, session_id, user_id)
            
            # Reconstruct legacy response structure
            return {
                **initial_state,
                "message_count": 0,
                "events": []
            }
                
        except Exception as e:
            if _looks_like_existing_adk_session_error(e):
                logger.info("ADK session %s already exists for user %s; binding local metadata", session_id, user_id)
                self._upsert_session_owner(
                    session_id=session_id,
                    user_id=user_id,
                    external_session_id=external_session_id,
                )
                if external_session_id:
                    self.set_session_mapping(external_session_id, session_id, user_id)
                existing = await self.get_user_session(user_id, session_id)
                if isinstance(existing, dict):
                    existing.setdefault("message_count", 0)
                    existing.setdefault("events", [])
                    return existing
                return {
                    **dict(initial_state or {}),
                    "message_count": 0,
                    "events": [],
                }
            logger.error(f"Failed to create ADK session: {e}")
            raise
    
    async def get_user_session(self, user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve ADK Session and map it back to AutoYou dict format."""
        try:
            if not self.adk_session_service:
                return None
            
            adk_session = await self.adk_session_service.get_session(
                app_name=_AUTOYOU_APP_NAME,
                user_id=user_id,
                session_id=session_id
            )
            
            if not adk_session:
                return None

            # Render events to list of dicts for backward compatibility
            events_rendered = []
            for ev in adk_session.events:
                state_delta = ev.actions.state_delta if ev.actions and ev.actions.state_delta else {}
                ev_data = state_delta.get("event_data_raw") if isinstance(state_delta, dict) else {}
                logical_event_type = None
                if isinstance(ev_data, dict):
                    logical_event_type = ev_data.get("_event_type")
                events_rendered.append({
                    "type": logical_event_type or ev.id,
                    "data": ev_data,
                    "timestamp": datetime.fromtimestamp(ev.timestamp).isoformat() if hasattr(ev, 'timestamp') else datetime.now().isoformat()
                })
            
            return {
                **(adk_session.state or {}),
                "events": events_rendered
            }
                
        except Exception as e:
            logger.error(f"Failed to get ADK session: {e}")
            return None

    async def update_user_session(self, user_id: str, session_id: str, session_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Since ADK requires Events to modify state persistently, we push a system event 
        containing the new state delta.
        """
        try:
            if not self.adk_session_service:
                return None
                
            adk_session = await self.adk_session_service.get_session(
                app_name=_AUTOYOU_APP_NAME,
                user_id=user_id,
                session_id=session_id
            )
            if not adk_session:
                return None
                
            # Filter pure state attributes
            state_delta = {k: v for k, v in session_data.items() if k != "events"}
                
            # Fire an invisible system event updating the state dictionary atomically
            system_event = Event(
                id=self._new_event_id("system_update"),
                author="system",
                actions=EventActions(state_delta=state_delta)
            )
            
            await self.adk_session_service.append_event(session=adk_session, event=system_event)
            return session_data

        except Exception as e:
            logger.error(f"Failed to update ADK session: {e}")
            return None

    async def add_session_event(self, user_id: str, session_id: str, event_type: str, event_data: Dict[str, Any], external_session_id: Optional[str] = None) -> bool:
        """Add an event using ADK's native `append_event` with state delta."""
        try:
            if not self.adk_session_service:
                return False
                
            adk_session = await self.adk_session_service.get_session(
                app_name=_AUTOYOU_APP_NAME,
                user_id=user_id,
                session_id=session_id
            )
            
            if not adk_session:
                await self.create_user_session(user_id, session_id, {}, external_session_id=external_session_id)
                adk_session = await self.adk_session_service.get_session(
                    app_name=_AUTOYOU_APP_NAME,
                    user_id=user_id,
                    session_id=session_id
                )

            self._upsert_session_owner(
                session_id=session_id,
                user_id=user_id,
                external_session_id=external_session_id,
            )

            current_count = adk_session.state.get("message_count", 0) + 1
            
            if self.record_messages:
                # Store the custom AI Agent history directly into ADK's action engine
                raw_event_data = dict(event_data or {})
                raw_event_data["_event_type"] = event_type
                if external_session_id:
                    raw_event_data["external_session_id"] = external_session_id
                event_id = self._new_event_id(event_type)
                adk_event = Event(
                    id=event_id,
                    author="autoyou",
                    actions=EventActions(state_delta={
                        "message_count": current_count,
                        "event_data_raw": raw_event_data
                    })
                )
                await self.adk_session_service.append_event(session=adk_session, event=adk_event)
                memory_record = self._build_memory_search_record(
                    event_id=event_id,
                    user_id=user_id,
                    session_id=session_id,
                    event_type=event_type,
                    event_data=raw_event_data,
                    external_session_id=external_session_id,
                )
                if memory_record:
                    await asyncio.to_thread(self._upsert_memory_search_record, memory_record)
                    self._schedule_cognee_memory_mirror(memory_record)
            else:
                # Just bump the counter if we aren't storing the data
                adk_event = Event(
                    id=self._new_event_id("system_update"),
                    author="system",
                    actions=EventActions(state_delta={"message_count": current_count})
                )
                await self.adk_session_service.append_event(session=adk_session, event=adk_event)
                
            return True
            
        except Exception as e:
            logger.error(f"Failed to add ADK session event: {e}")
            return False

    @staticmethod
    def _is_full_scan_query(query: str) -> bool:
        return str(query or "").strip().lower() in {
            "*",
            "__all__",
            "all",
            "all memory",
            "entire memory",
            "full memory",
        }

    def _search_memory_index_sync(
        self,
        user_id: Optional[str],
        search_terms: List[str],
        limit: int,
        scan_all_query: bool = False,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search the denormalized memory_search table before falling back to blob scans."""
        results: List[Dict[str, Any]] = []
        with sqlite3.connect(self.db_path, timeout=15.0) as db:
            db.execute("PRAGMA busy_timeout = 3000")
            query = [
                "SELECT event_id, user_id, session_id, external_session_id, event_type, timestamp_text, user_message, agent_response, content, sort_timestamp, metadata_json",
                "FROM memory_search",
                "WHERE 1=1",
            ]
            params: List[Any] = []

            if user_id:
                query.append("AND user_id=?")
                params.append(user_id)

            if session_id:
                query.append("AND session_id=?")
                params.append(session_id)

            if not scan_all_query and search_terms:
                query.append(
                    "AND (" + " OR ".join("instr(normalized_content, ?) > 0" for _ in search_terms) + ")"
                )
                params.extend(search_terms)

            query.append("ORDER BY sort_timestamp DESC")
            if limit > 0:
                query.append("LIMIT ?")
                params.append(limit)

            cursor = db.execute(" ".join(query), params)
            for row in cursor:
                (
                    row_event_id,
                    row_user_id,
                    row_session_id,
                    row_external_session_id,
                    row_event_type,
                    row_timestamp_text,
                    row_user_message,
                    row_agent_response,
                    row_content,
                    row_sort_timestamp,
                    row_metadata_json,
                ) = row
                row_metadata = {}
                try:
                    parsed_metadata = json.loads(row_metadata_json or "{}")
                    if isinstance(parsed_metadata, dict):
                        row_metadata = parsed_metadata
                except Exception:
                    row_metadata = {}
                results.append(
                    {
                        "event_id": row_event_id,
                        "user_id": row_user_id,
                        "session_id": row_session_id,
                        "external_session_id": row_external_session_id or "",
                        "event_type": row_event_type,
                        "timestamp": row_timestamp_text,
                        "sort_timestamp": row_sort_timestamp,
                        "user_message": row_user_message or "",
                        "agent_response": row_agent_response or "",
                        "content": row_content or "",
                        "source": "memory_search_index",
                        "metadata": row_metadata,
                    }
                )
        return results

    @staticmethod
    def _memory_result_key(result: Dict[str, Any]) -> tuple:
        event_id = str((result or {}).get("event_id") or "").strip()
        if event_id:
            return ("event", event_id)

        user_id = str((result or {}).get("user_id") or "").strip()
        session_id = str((result or {}).get("session_id") or "").strip()
        content = MemoryIntegratedSessionManager._normalize_memory_text(
            (result or {}).get("content") or ""
        )
        if content:
            return ("content", user_id, session_id, content)

        return ("object", id(result))

    @classmethod
    def _merge_memory_results(
        cls,
        local_results: List[Dict[str, Any]],
        cognee_results: List[Dict[str, Any]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for result in list(local_results or []) + list(cognee_results or []):
            if not isinstance(result, dict):
                continue
            key = cls._memory_result_key(result)
            if key in seen:
                continue
            seen.add(key)
            merged.append(result)
            if limit > 0 and len(merged) >= limit:
                break
        return merged

    def get_user_id_for_session(self, session_id: str) -> Optional[str]:
        """Resolve user_id from AutoYou-owned session metadata or mapping rows."""
        sid = str(session_id or "").strip()
        if not sid:
            return None
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                row = conn.execute(
                    "SELECT user_id FROM session_owners WHERE session_id=? ORDER BY updated_at DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                if row and row[0]:
                    return str(row[0])

                row = conn.execute(
                    "SELECT user_id FROM session_mappings WHERE session_id=? ORDER BY updated_at DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                if row and row[0]:
                    return str(row[0])

                row = conn.execute(
                    "SELECT user_id FROM memory_search WHERE session_id=? ORDER BY sort_timestamp DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                if row and row[0]:
                    return str(row[0])

                row = conn.execute(
                    "SELECT user_id FROM session_mappings WHERE external_session_id=? ORDER BY updated_at DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                if row and row[0]:
                    return str(row[0])
        except Exception as e:
            logger.debug(f"Failed to resolve user_id for session {sid}: {e}")
        return None

    async def delete_conversation_history(
        self,
        user_id: str,
        session_id: str,
        *,
        external_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete AutoYou-managed history for one server conversation.

        This deliberately removes only records owned by AutoYou. A configured
        third-party model provider may retain its own copy outside this store.
        """
        normalized_user_id = str(user_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        normalized_external_id = str(external_session_id or "").strip()
        if not normalized_session_id:
            return {"deleted": False, "components": [], "reason": "missing_session_id"}

        components: list[str] = []
        session_ids = {normalized_session_id}
        external_ids = {normalized_session_id}
        if normalized_external_id:
            external_ids.add(normalized_external_id)
        cache_keys = set(external_ids)
        user_clause = " AND user_id = ?" if normalized_user_id else ""
        user_values = (normalized_user_id,) if normalized_user_id else ()

        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                external_placeholders = ", ".join("?" for _ in external_ids)
                external_values = tuple(sorted(external_ids))
                for table_name in ("memory_search", "session_context_usage", "session_mappings", "session_owners"):
                    rows = conn.execute(
                        f"SELECT session_id, external_session_id FROM {table_name} "
                        f"WHERE (external_session_id IN ({external_placeholders}) OR session_id = ?){user_clause}",
                        (*external_values, normalized_session_id, *user_values),
                    ).fetchall()
                    for row in rows:
                        if row and str(row[0] or "").strip():
                            session_ids.add(str(row[0]).strip())
                        if row and str(row[1] or "").strip():
                            external_ids.add(str(row[1]).strip())
                            cache_keys.add(str(row[1]).strip())

                session_placeholders = ", ".join("?" for _ in session_ids)
                session_values = tuple(sorted(session_ids))
                for table_name in ("session_mappings", "session_owners"):
                    rows = conn.execute(
                        f"SELECT session_id, external_session_id FROM {table_name} "
                        f"WHERE session_id IN ({session_placeholders}){user_clause}",
                        (*session_values, *user_values),
                    ).fetchall()
                    for row in rows:
                        if row and str(row[0] or "").strip():
                            session_ids.add(str(row[0]).strip())
                        if row and str(row[1] or "").strip():
                            external_ids.add(str(row[1]).strip())
                            cache_keys.add(str(row[1]).strip())

                session_placeholders = ", ".join("?" for _ in session_ids)
                external_placeholders = ", ".join("?" for _ in external_ids)
                scope_values = (*sorted(session_ids), *sorted(external_ids))
                for table_name in ("memory_search", "session_context_usage", "session_mappings", "session_owners"):
                    conn.execute(
                        f"DELETE FROM {table_name} "
                        f"WHERE (session_id IN ({session_placeholders}) "
                        f"OR external_session_id IN ({external_placeholders})){user_clause}",
                        (*scope_values, *user_values),
                    )
                conn.commit()
            components.append("autoyou_sqlite_history")
        except Exception as exc:
            logger.warning("Failed to delete AutoYou history for %s: %s", normalized_session_id, exc)

        if self.adk_session_service is not None:
            delete_session = getattr(self.adk_session_service, "delete_session", None)
            if callable(delete_session) and normalized_user_id:
                deleted_adk = False
                for adk_session_id in sorted(session_ids):
                    try:
                        result = delete_session(
                            app_name=_AUTOYOU_APP_NAME,
                            user_id=normalized_user_id,
                            session_id=adk_session_id,
                        )
                        if asyncio.iscoroutine(result):
                            await result
                        deleted_adk = True
                    except Exception as exc:
                        logger.warning("Failed to delete ADK conversation session %s: %s", adk_session_id, exc)
                if deleted_adk:
                    components.append("adk_session")

        with self._lock:
            for cache_key in cache_keys:
                self._session_mapping_cache.pop(cache_key, None)

        return {
            "deleted": bool(components),
            "components": components,
            "provider_history_retained": True,
        }

    async def replace_conversation_turn(
        self,
        user_id: str,
        session_id: str,
        *,
        edited_message_id: Optional[str] = None,
        edited_message_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Remove one ADK turn and everything after it without changing the conversation id.

        ADK sessions are append-only; there is no event-edit API. Rebuilding the
        existing session id from the events before the selected user turn gives
        edit/redo the expected same-conversation behavior while preserving the
        external AutoYou conversation identity and mapping.
        """
        normalized_user_id = str(user_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        target_id = str(edited_message_id or "").strip()
        target_text = str(edited_message_text or "").strip()
        if not normalized_user_id or not normalized_session_id:
            return {"replaced": False, "reason": "missing_session_identity"}
        service = self.adk_session_service
        if service is None:
            return {"replaced": False, "reason": "adk_session_store_unavailable"}

        try:
            current = await service.get_session(
                app_name=_AUTOYOU_APP_NAME,
                user_id=normalized_user_id,
                session_id=normalized_session_id,
            )
            if current is None:
                return {"replaced": False, "reason": "session_not_found"}
            events = list(getattr(current, "events", []) or [])

            target_index = None
            for index, event in enumerate(events):
                if str(getattr(event, "author", "") or "").strip().lower() != "user":
                    continue
                actions = getattr(event, "actions", None)
                state_delta = getattr(actions, "state_delta", None) if actions is not None else None
                turn_metadata = state_delta.get("_autoyou_turn_metadata") if isinstance(state_delta, dict) else None
                stored_id = str((turn_metadata or {}).get("client_prompt_id") or "").strip()
                if target_id and stored_id == target_id:
                    target_index = index
                    break

            # Support messages sent by older clients that did not persist a
            # client prompt id in the ADK user event. Exact text is deliberately
            # used as a conservative fallback; ambiguous repeated prompts are
            # rejected instead of deleting the wrong turn.
            if target_index is None and target_text:
                text_matches = []
                for index, event in enumerate(events):
                    if str(getattr(event, "author", "") or "").strip().lower() != "user":
                        continue
                    content = getattr(event, "content", None)
                    parts = getattr(content, "parts", None) if content is not None else None
                    event_text = "".join(
                        str(getattr(part, "text", "") or "")
                        for part in (parts or [])
                        if getattr(part, "text", None) is not None
                    ).strip()
                    if event_text == target_text:
                        text_matches.append(index)
                if len(text_matches) == 1:
                    target_index = text_matches[0]

            if target_index is None:
                return {"replaced": False, "reason": "target_turn_not_found"}

            retained_events = [copy.deepcopy(event) for event in events[:target_index]]
            original_events = [copy.deepcopy(event) for event in events]
            original_state = copy.deepcopy(getattr(current, "state", {}) or {})
            delete_session = getattr(service, "delete_session", None)
            create_session = getattr(service, "create_session", None)
            if not callable(delete_session) or not callable(create_session):
                return {"replaced": False, "reason": "adk_session_rebuild_unavailable"}

            async def _rebuild(events_to_restore: list[Any]) -> bool:
                delete_result = delete_session(
                    app_name=_AUTOYOU_APP_NAME,
                    user_id=normalized_user_id,
                    session_id=normalized_session_id,
                )
                if asyncio.iscoroutine(delete_result):
                    await delete_result
                create_result = create_session(
                    app_name=_AUTOYOU_APP_NAME,
                    user_id=normalized_user_id,
                    session_id=normalized_session_id,
                    state=copy.deepcopy(original_state),
                )
                if asyncio.iscoroutine(create_result):
                    await create_result
                rebuilt_session = await service.get_session(
                    app_name=_AUTOYOU_APP_NAME,
                    user_id=normalized_user_id,
                    session_id=normalized_session_id,
                )
                if rebuilt_session is None:
                    return False
                for event in events_to_restore:
                    await service.append_event(
                        session=rebuilt_session,
                        event=copy.deepcopy(event),
                    )
                return True

            try:
                rebuilt_ok = await _rebuild(retained_events)
            except Exception:
                rebuilt_ok = False
            if not rebuilt_ok:
                # ADK exposes an append-only event stream, so replacement is a
                # delete/recreate operation.  Restore the original stream if
                # any step of the first rebuild fails; never leave a partial
                # conversation behind.
                try:
                    await _rebuild(original_events)
                except Exception as rollback_exc:
                    logger.error(
                        "Could not restore ADK conversation %s after failed replacement: %s",
                        normalized_session_id,
                        rollback_exc,
                    )
                return {"replaced": False, "reason": "adk_session_rebuild_failed"}

            retained_ids = {
                str(getattr(event, "id", "") or "").strip()
                for event in retained_events
                if str(getattr(event, "id", "") or "").strip()
            }
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                if retained_ids:
                    placeholders = ", ".join("?" for _ in retained_ids)
                    conn.execute(
                        f"DELETE FROM memory_search WHERE session_id = ? AND event_id NOT IN ({placeholders})",
                        (normalized_session_id, *sorted(retained_ids)),
                    )
                else:
                    conn.execute(
                        "DELETE FROM memory_search WHERE session_id = ?",
                        (normalized_session_id,),
                    )
                conn.execute(
                    "DELETE FROM session_context_usage WHERE session_id = ?",
                    (normalized_session_id,),
                )
                conn.commit()

            return {
                "replaced": True,
                "session_id": normalized_session_id,
                "removed_event_count": len(events) - target_index,
                "retained_event_count": len(retained_events),
            }
        except Exception as exc:
            logger.warning(
                "Failed to replace ADK conversation turn for %s: %s",
                normalized_session_id,
                exc,
            )
            return {"replaced": False, "reason": "adk_session_rebuild_failed"}

    def get_single_known_user_id(self) -> Optional[str]:
        """Return a user_id only when a single distinct one exists in AutoYou-owned local metadata."""
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                rows = conn.execute(
                    """
                    SELECT user_id FROM (
                        SELECT DISTINCT user_id FROM session_owners
                        UNION
                        SELECT DISTINCT user_id FROM session_mappings
                        UNION
                        SELECT DISTINCT user_id FROM memory_search
                    ) WHERE user_id IS NOT NULL AND TRIM(user_id) != ''
                    """
                ).fetchall()
                user_ids = [str(r[0]).strip() for r in rows if r and r[0]]
                user_ids = [u for u in user_ids if u]
                if len(user_ids) == 1:
                    return user_ids[0]
        except Exception as e:
            logger.debug(f"Failed to infer single known user_id: {e}")
        return None
    
    async def search_memory(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search AutoYou's denormalized memory_search table.

        Note:
        Search is always scoped to `user_id`. When `session_id` is supplied it
        is also scoped to that exact logical conversation. Callers that need
        cross-conversation recall must opt in by omitting `session_id`.
        """
        if not user_id or not str(user_id).strip():
            logger.warning("search_memory called without a valid user_id; returning empty result")
            return []

        if not query or not str(query).strip():
            return []

        try:
            normalized_limit = int(limit)
        except (TypeError, ValueError):
            normalized_limit = 10

        search_terms = [t.lower() for t in str(query).split() if t.strip()]
        scan_all_query = self._is_full_scan_query(str(query))
        # from __debug_provenance_l__ import because
        if not search_terms and not scan_all_query:
            return []
        normalized_session_id = str(session_id or "").strip() or None

        cognee_results: List[Dict[str, Any]] = []
        # Cognee's current search interface has no server-enforced session
        # filter. Do not merge owner-wide Cognee hits into a thread-scoped
        # lookup, because that would reintroduce cross-conversation recall.
        if self._cognee_memory is not None and normalized_session_id is None:
            try:
                raw_cognee_results = await asyncio.wait_for(
                    self._cognee_memory.search(user_id, query, normalized_limit),
                    timeout=self._cognee_search_timeout_seconds,
                )
                if isinstance(raw_cognee_results, list):
                    cognee_results = raw_cognee_results
            except asyncio.TimeoutError:
                logger.info(
                    "Cognee memory query timed out after %.1fs; falling back to memory_search",
                    self._cognee_search_timeout_seconds,
                )
            except Exception as e:
                logger.warning("Cognee memory query failed: %s", e)

        try:
            indexed_results = await asyncio.to_thread(
                self._search_memory_index_sync,
                user_id,
                search_terms,
                normalized_limit,
                scan_all_query,
                normalized_session_id,
            )
            return self._merge_memory_results(indexed_results, cognee_results, normalized_limit)
        except Exception as e:
            logger.warning("Denormalized memory_search query failed: %s", e)
        return self._merge_memory_results([], cognee_results, normalized_limit)

    async def search_all_memory(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Privileged all-user search used by memory_agent for owner-level recall."""
        if not query or not str(query).strip():
            return []

        try:
            normalized_limit = int(limit)
        except (TypeError, ValueError):
            normalized_limit = 10

        search_terms = [t.lower() for t in str(query).split() if t.strip()]
        scan_all_query = self._is_full_scan_query(str(query))
        if not search_terms and not scan_all_query:
            return []

        cognee_results: List[Dict[str, Any]] = []
        if self._cognee_memory is not None and hasattr(self._cognee_memory, "search_all"):
            try:
                raw_cognee_results = await asyncio.wait_for(
                    self._cognee_memory.search_all(query, normalized_limit),
                    timeout=self._cognee_search_timeout_seconds,
                )
                if isinstance(raw_cognee_results, list):
                    cognee_results = raw_cognee_results
            except asyncio.TimeoutError:
                logger.info(
                    "Cognee all-user memory query timed out after %.1fs; falling back to memory_search",
                    self._cognee_search_timeout_seconds,
                )
            except Exception as e:
                logger.warning("Cognee all-user memory query failed: %s", e)

        try:
            indexed_results = await asyncio.to_thread(
                self._search_memory_index_sync,
                None,
                search_terms,
                normalized_limit,
                scan_all_query,
            )
            return self._merge_memory_results(indexed_results, cognee_results, normalized_limit)
        except Exception as e:
            logger.warning("All-user memory_search query failed: %s", e)
        return self._merge_memory_results([], cognee_results, normalized_limit)

    # ---------------------------------------------------------
    # Mapping Cache Methods (SQLite Backed)
    # ---------------------------------------------------------

    def get_mapped_session_id(self, external_session_id: str, user_id: Optional[str] = None) -> Optional[str]:
        with self._lock:
            if cached := self._session_mapping_cache.get(external_session_id):
                return cached
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                if user_id:
                    cursor = conn.execute(
                        "SELECT session_id FROM session_mappings WHERE external_session_id = ? AND user_id = ? ORDER BY updated_at DESC LIMIT 1",
                        (external_session_id, user_id)
                    )
                else:
                    cursor = conn.execute(
                        "SELECT session_id FROM session_mappings WHERE external_session_id = ? ORDER BY updated_at DESC LIMIT 1",
                        (external_session_id,)
                    )
                row = cursor.fetchone()
                if row and row[0]:
                    internal_session_id = row[0]
                    with self._lock:
                        self._session_mapping_cache[external_session_id] = internal_session_id
                    return internal_session_id

                if user_id:
                    cursor = conn.execute(
                        "SELECT session_id FROM session_owners WHERE external_session_id = ? AND user_id = ? ORDER BY updated_at DESC LIMIT 1",
                        (external_session_id, user_id)
                    )
                else:
                    cursor = conn.execute(
                        "SELECT session_id FROM session_owners WHERE external_session_id = ? ORDER BY updated_at DESC LIMIT 1",
                        (external_session_id,)
                    )
                row = cursor.fetchone()
                if row and row[0]:
                    internal_session_id = row[0]
                    with self._lock:
                        self._session_mapping_cache[external_session_id] = internal_session_id
                    return internal_session_id
        except Exception as e:
            logger.warning(f"DB lookup for mapping failed: {e}")
        return None
    
    def set_session_mapping(self, external_session_id: str, internal_session_id: str, user_id: Optional[str] = None) -> None:
        with self._lock:
            self._session_mapping_cache[external_session_id] = internal_session_id
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                if user_id:
                    conn.execute(
                        "INSERT OR REPLACE INTO session_mappings (external_session_id, session_id, user_id, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                        (external_session_id, internal_session_id, user_id)
                    )
                    conn.commit()
                    self._upsert_session_owner(
                        session_id=internal_session_id,
                        user_id=user_id,
                        external_session_id=external_session_id,
                    )
        except Exception as e:
            logger.warning(f"Failed to persist mapping: {e}")
    
    def clear_session_mapping(self, external_session_id: str) -> None:
        with self._lock:
            if external_session_id in self._session_mapping_cache:
                del self._session_mapping_cache[external_session_id]
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("DELETE FROM session_mappings WHERE external_session_id = ?", (external_session_id,))
                conn.commit()
        except Exception:
            pass
    
    def get_all_session_mappings(self) -> Dict[str, str]:
        with self._lock:
            return self._session_mapping_cache.copy()

    def preload_session_mappings(self) -> int:
        loaded = 0
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                rows = conn.execute("SELECT external_session_id, session_id FROM session_mappings").fetchall()
                with self._lock:
                    for ext_id, internal_id in rows:
                        self._session_mapping_cache[ext_id] = internal_id
                        loaded += 1
        except Exception:
            pass
        return loaded

    def get_current_conversation_thread(self, owner_key: str) -> int:
        normalized_owner_key = str(owner_key or "").strip()
        if not normalized_owner_key:
            return 1

        with self._lock:
            cached = self._conversation_thread_cache.get(normalized_owner_key)
        if cached and cached > 0:
            return cached

        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA busy_timeout = 3000")
                row = conn.execute(
                    "SELECT current_thread FROM conversation_threads WHERE owner_key = ?",
                    (normalized_owner_key,),
                ).fetchone()
            current_thread = int(row[0]) if row and row[0] else 1
        except Exception as e:
            logger.warning("Failed to read conversation thread for %s: %s", normalized_owner_key, e)
            current_thread = 1

        with self._lock:
            self._conversation_thread_cache[normalized_owner_key] = max(1, current_thread)
        return max(1, current_thread)

    def get_current_conversation_session_id(self, owner_key: str) -> str:
        normalized_owner_key = str(owner_key or "").strip()
        return build_canonical_session_id(
            normalized_owner_key,
            self.get_current_conversation_thread(normalized_owner_key),
        )

    def advance_conversation_thread(self, owner_key: str) -> tuple[int, str]:
        normalized_owner_key = str(owner_key or "").strip()
        if not normalized_owner_key:
            raise ValueError("owner_key is required to advance conversation thread")

        with self._lock:
            current_thread = self._conversation_thread_cache.get(normalized_owner_key)
            if not current_thread:
                try:
                    with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                        conn.execute("PRAGMA busy_timeout = 3000")
                        row = conn.execute(
                            "SELECT current_thread FROM conversation_threads WHERE owner_key = ?",
                            (normalized_owner_key,),
                        ).fetchone()
                    current_thread = int(row[0]) if row and row[0] else 1
                except Exception as e:
                    logger.warning(
                        "Failed to read conversation thread for %s before advance: %s",
                        normalized_owner_key,
                        e,
                    )
                    current_thread = 1

            next_thread = max(1, int(current_thread)) + 1
            self._conversation_thread_cache[normalized_owner_key] = next_thread

            try:
                with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                    conn.execute("PRAGMA busy_timeout = 3000")
                    conn.execute(
                        """
                        INSERT INTO conversation_threads (owner_key, current_thread, updated_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(owner_key) DO UPDATE SET
                            current_thread=excluded.current_thread,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (normalized_owner_key, next_thread),
                    )
                    conn.commit()
            except Exception as e:
                logger.warning("Failed to persist conversation thread for %s: %s", normalized_owner_key, e)

        return next_thread, build_canonical_session_id(normalized_owner_key, next_thread)

class SessionMetrics:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = os.path.abspath(os.path.expanduser(str(db_path or _default_sessions_db_path())))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._message_counts = {}
        self._lock = threading.Lock()
    
    def record_message(self, user_id: str, session_id: str):
        with self._lock:
            key = f"{user_id}:{session_id}"
            self._message_counts[key] = self._message_counts.get(key, 0) + 1
    
    def get_basic_stats(self) -> Dict[str, Any]:
        return {
            "total_sessions": len(self._message_counts),
            "active_sessions": len(self._message_counts),
            "total_messages": sum(self._message_counts.values()),
            "timestamp": datetime.now().isoformat()
        }
