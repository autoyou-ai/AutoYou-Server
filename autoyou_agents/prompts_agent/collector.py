# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-T-000000000000000000000000-3dc3dbac8b8cbf75051141c3

"""Multi-source prompt harvesting engine and background cron scheduler."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import unquote

from .config import (
    AGENT_NAME,
    DATA_DIR,
    DEFAULT_SETTINGS,
    SUPPORTED_SOFTWARES,
    SYNC_INTERVAL_MINUTES,
    get_default_scan_paths,
)
from .db import PromptsDatabase, utc_now

__debug_trace_t__ = "AUTOYOU-PROVENANCE-T-000000000000000000000000-3dc3dbac8b8cbf75051141c3"

logger = logging.getLogger(__name__)

_NOISE_PREFIXES = (
    "<permissions instructions>",
    "<environment_context>",
    "<user_instructions>",
    "# Collaboration Mode",
    "<automation_context>",
)

_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*(?:</USER_REQUEST>|\Z)", re.S)


def _clean_text(val: Any) -> str:
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, list):
        parts = [_clean_text(item) for item in val]
        return "\n".join(p for p in parts if p).strip()
    if isinstance(val, dict):
        for k in ("text", "content", "message", "input_text", "output_text", "parts"):
            res = _clean_text(val.get(k))
            if res:
                return res
    return ""


def _infer_project_name(cwd_or_path: str) -> str:
    """Extract human-readable project name from path or URI."""
    if not cwd_or_path:
        return "General"
    path_str = cwd_or_path.replace("file:///", "").replace("file://", "")
    p = Path(path_str)
    name = p.name or p.parent.name
    return name if name and name not in {"", ".", "/"} else "General"


# ─── Individual Source Parsers ───────────────────────────────────────────────

def parse_codex_file(path: Path) -> List[Dict[str, Any]]:
    """Parse Codex rollout JSONL file or history file."""
    turns: List[Dict[str, Any]] = []
    session_id = path.stem
    project_name = "General"
    model = "codex"
    first_ts = None
    # from __debug_trace_t__ import autoyou_provenance_t_000000000000000000000000_3dc3dbac8b8cbf75051141c3
    last_ts = None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue

                ts = obj.get("timestamp") or obj.get("created_at") or utc_now()
                first_ts = first_ts or ts
                last_ts = ts
                rec_type = str(obj.get("type") or "").lower()
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
                ptype = str(payload.get("type") or "").lower()

                if rec_type in ("session_meta", "session"):
                    if payload.get("id") or payload.get("session_id"):
                        session_id = str(payload.get("id") or payload.get("session_id"))
                    if payload.get("cwd"):
                        project_name = _infer_project_name(str(payload["cwd"]))
                    if payload.get("model"):
                        model = str(payload["model"])
                    continue

                if payload.get("cwd") and project_name == "General":
                    project_name = _infer_project_name(str(payload["cwd"]))

                if rec_type == "event_msg":
                    if ptype in ("user_message", "human"):
                        msg = _clean_text(payload.get("message") or payload.get("content"))
                        if msg and not msg.startswith(_NOISE_PREFIXES):
                            turns.append({
                                "role": "user",
                                "prompt_text": msg,
                                "timestamp": ts,
                                "model": model,
                            })
                    elif ptype in ("agent_message", "assistant_message", "task_complete"):
                        msg = _clean_text(payload.get("message") or payload.get("last_agent_message") or payload.get("content"))
                        if msg:
                            if turns and turns[-1].get("role") == "user":
                                turns[-1]["output_text"] = msg
                                turns[-1]["is_final_output"] = (ptype == "task_complete")
                            else:
                                turns.append({
                                    "role": "assistant",
                                    "prompt_text": "",
                                    "output_text": msg,
                                    "timestamp": ts,
                                    "is_final_output": (ptype == "task_complete"),
                                })

                elif rec_type in ("response_item", "message"):
                    role = str(payload.get("role") or "").lower()
                    content = _clean_text(payload.get("content") or payload.get("message"))
                    if role in ("user", "human") and content and not content.startswith(_NOISE_PREFIXES):
                        turns.append({
                            "role": "user",
                            "prompt_text": content,
                            "timestamp": ts,
                            "model": model,
                        })
                    elif role in ("assistant", "agent") and content:
                        if turns and turns[-1].get("role") == "user":
                            turns[-1]["output_text"] = content
                        else:
                            turns.append({
                                "role": "assistant",
                                "prompt_text": "",
                                "output_text": content,
                                "timestamp": ts,
                            })

        if not turns:
            return []

        title = path.stem
        first_prompt = next((t["prompt_text"] for t in turns if t["prompt_text"]), "")
        if first_prompt:
            title = first_prompt.splitlines()[0][:80].strip("# ").strip()

        return [{
            "session_id": session_id,
            "software": "codex",
            "project_name": project_name,
            "title": title or "Codex Session",
            "source_file": str(path),
            "created_at": first_ts or utc_now(),
            "updated_at": last_ts or utc_now(),
            "turns": turns,
            "model": model,
        }]
    except Exception as exc:
        logger.debug("Error parsing Codex file %s: %s", path, exc)
        return []


def parse_claude_file(path: Path) -> Optional[Dict[str, Any]]:
    """Parse Claude Projects or Claude Code session JSONL file."""
    turns: List[Dict[str, Any]] = []
    sess_id = path.stem
    project_name = _infer_project_name(path.parent.name)
    title = path.stem
    first_ts = None
    last_ts = None
    model = "claude"

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue

                ts = obj.get("timestamp") or utc_now()
                first_ts = first_ts or ts
                last_ts = ts
                rtype = str(obj.get("type") or "").lower()

                if rtype == "custom-title":
                    title = str(obj.get("customTitle") or title)
                    continue
                if rtype == "ai-title":
                    title = str(obj.get("aiTitle") or title)
                    continue
                if rtype == "system" and obj.get("cwd"):
                    project_name = _infer_project_name(str(obj["cwd"]))
                    continue

                if rtype in ("user", "assistant"):
                    msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
                    content = msg.get("content")
                    text = _clean_text(content)
                    if not text:
                        continue
                    if rtype == "user":
                        turns.append({
                            "role": "user",
                            "prompt_text": text,
                            "timestamp": ts,
                            "model": model,
                        })
                    else:
                        if turns and turns[-1].get("role") == "user":
                            turns[-1]["output_text"] = text
                        else:
                            turns.append({
                                "role": "assistant",
                                "prompt_text": "",
                                "output_text": text,
                                "timestamp": ts,
                            })

        if not turns:
            return None

        # Clean title if still file stem
        if title == path.stem and turns:
            first_prompt = next((t["prompt_text"] for t in turns if t["prompt_text"]), "")
            if first_prompt:
                title = first_prompt.splitlines()[0][:80].strip("# ").strip()

        return {
            "session_id": sess_id,
            "software": "claude",
            "project_name": project_name,
            "title": title or "Claude Session",
            "source_file": str(path),
            "created_at": first_ts or utc_now(),
            "updated_at": last_ts or utc_now(),
            "turns": turns,
            "model": model,
        }
    except Exception as exc:
        logger.debug("Error parsing Claude file %s: %s", path, exc)
        return None


def parse_antigravity_transcript(path: Path) -> Optional[Dict[str, Any]]:
    """Parse Antigravity brain transcript.jsonl file."""
    # brain/<conversation_id>/.system_generated/logs/transcript.jsonl
    brain_id = path.parents[2].name if len(path.parents) >= 3 else path.stem
    turns: List[Dict[str, Any]] = []
    first_ts = None
    last_ts = None
    title = ""

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue

                ts = obj.get("created_at") or utc_now()
                first_ts = first_ts or ts
                last_ts = ts
                step_type = str(obj.get("type") or "").upper()
                content = str(obj.get("content") or "")

                if step_type == "USER_INPUT":
                    m = _USER_REQUEST_RE.search(content)
                    raw_user = m.group(1).strip() if m else content.strip()
                    if raw_user:
                        if not title:
                            title = raw_user.splitlines()[0][:80].strip()
                        turns.append({
                            "role": "user",
                            "prompt_text": raw_user,
                            "timestamp": ts,
                            "model": "antigravity",
                        })
                elif step_type == "PLANNER_RESPONSE":
                    if content.strip():
                        if turns and turns[-1].get("role") == "user":
                            turns[-1]["output_text"] = content.strip()
                        else:
                            turns.append({
                                "role": "assistant",
                                "prompt_text": "",
                                "output_text": content.strip(),
                                "timestamp": ts,
                            })

        if not turns:
            return None

        return {
            "session_id": brain_id,
            "software": "antigravity",
            "project_name": "AutoYou",
            "title": title or f"Antigravity Session {brain_id[:8]}",
            "source_file": str(path),
            "created_at": first_ts or utc_now(),
            "updated_at": last_ts or utc_now(),
            "turns": turns,
            "model": "gemini-antigravity",
        }
    except Exception as exc:
        logger.debug("Error parsing Antigravity transcript %s: %s", path, exc)
        return None


def parse_vscode_chat_session(path: Path) -> Optional[Dict[str, Any]]:
    """Parse VS Code chat session JSONL or JSON file."""
    turns: List[Dict[str, Any]] = []
    sess_id = path.stem
    project_name = "General"
    title = path.stem
    created_at = None

    # Check for workspace.json in the same workspace storage folder
    ws_file = path.parent.parent / "workspace.json"
    if ws_file.is_file():
        try:
            ws_data = json.loads(ws_file.read_text(encoding="utf-8"))
            folder = str(ws_data.get("folder") or ws_data.get("workspace") or "")
            if folder:
                project_name = _infer_project_name(folder)
        except Exception:
            pass

    try:
        # VS Code chat sessions are JSONL lines with kind=0 / kind=1 or top-level JSON
        content_text = path.read_text(encoding="utf-8", errors="replace")
        lines = [l.strip() for l in content_text.splitlines() if l.strip()]

        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue

            # Case A: root session object (kind=0 or plain dict)
            v = obj.get("v") if isinstance(obj.get("v"), dict) else obj
            if isinstance(v, dict):
                sess_id = str(v.get("sessionId") or sess_id)
                creation_date = v.get("creationDate")
                if creation_date:
                    try:
                        created_at = datetime.fromtimestamp(creation_date / 1000, timezone.utc).isoformat()
                    except Exception:
                        pass

                # Parse requests array
                reqs = v.get("requests") or []
                for idx, req in enumerate(reqs):
                    if not isinstance(req, dict):
                        continue
                    msg = req.get("message")
                    prompt_txt = _clean_text(msg)
                    if not prompt_txt and isinstance(msg, dict):
                        prompt_txt = str(msg.get("text") or "")

                    resp_txt = ""
                    resp = req.get("response") or []
                    if isinstance(resp, list):
                        parts = []
                        for part in resp:
                            if isinstance(part, dict):
                                parts.append(str(part.get("value") or part.get("text") or ""))
                            elif isinstance(part, str):
                                parts.append(part)
                        resp_txt = "\n".join(p for p in parts if p)
                    elif isinstance(resp, str):
                        resp_txt = resp

                    if prompt_txt:
                        if not title or title == path.stem:
                            title = prompt_txt.splitlines()[0][:80].strip()
                        turns.append({
                            "role": "user",
                            "prompt_text": prompt_txt,
                            "output_text": resp_txt,
                            "timestamp": created_at or utc_now(),
                            "model": "vscode-copilot",
                        })

        if not turns:
            # Check draft / input state if requests array is empty
            for line in lines:
                try:
                    obj = json.loads(line)
                    v = obj.get("v") if isinstance(obj.get("v"), dict) else obj
                    if isinstance(v, dict):
                        input_state = v.get("inputState") if isinstance(v.get("inputState"), dict) else {}
                        draft = _clean_text(input_state.get("inputText"))
                        if draft and len(draft.strip()) > 2:
                            turns.append({
                                "role": "user",
                                "prompt_text": draft.strip(),
                                "output_text": "",
                                "timestamp": created_at or utc_now(),
                                "model": "vscode-copilot",
                            })
                            title = draft.splitlines()[0][:80].strip()
                            break
                    if obj.get("k") == ["inputState", "inputText"] and isinstance(obj.get("v"), str):
                        delta_txt = obj["v"].strip()
                        if len(delta_txt) > 2:
                            turns.append({
                                "role": "user",
                                "prompt_text": delta_txt,
                                "output_text": "",
                                "timestamp": created_at or utc_now(),
                                "model": "vscode-copilot",
                            })
                            title = delta_txt.splitlines()[0][:80].strip()
                            break
                except Exception:
                    continue

        if not turns:
            return None

        return {
            "session_id": sess_id,
            "software": "vscode",
            "project_name": project_name,
            "title": title or "VS Code Chat",
            "source_file": str(path),
            "created_at": created_at or utc_now(),
            "updated_at": created_at or utc_now(),
            "turns": turns,
            "model": "vscode-copilot",
        }
    except Exception as exc:
        logger.debug("Error parsing VS Code chat file %s: %s", path, exc)
        return None


def parse_vscode_state_db(db_path: Path) -> List[Dict[str, Any]]:
    """Parse VS Code Copilot chat sessions and active drafts from state.vscdb SQLite file."""
    sessions: List[Dict[str, Any]] = []
    if not db_path.is_file():
        return sessions

    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row

        # 1. Draft input prompt in untitledInputState
        row = conn.execute("SELECT value FROM ItemTable WHERE key = 'chat.untitledInputState'").fetchone()
        if row and row["value"]:
            try:
                data = json.loads(row["value"])
                input_txt = _clean_text(data.get("inputText"))
                if input_txt and len(input_txt) > 2:
                    sessions.append({
                        "session_id": f"vsc_draft_{db_path.parent.name[:12]}",
                        "software": "vscode",
                        "project_name": _infer_project_name(str(db_path.parent)),
                        "title": input_txt.splitlines()[0][:80].strip(),
                        "source_file": str(db_path),
                        "created_at": utc_now(),
                        "updated_at": utc_now(),
                        "turns": [{
                            "role": "user",
                            "prompt_text": input_txt,
                            "output_text": "",
                            "timestamp": utc_now(),
                            "model": "vscode-copilot",
                        }],
                        "model": "vscode-copilot",
                    })
            except Exception:
                pass

        # 2. Check for any Copilot session or interactive chat entries
        rows = conn.execute("""
            SELECT key, value FROM ItemTable
            WHERE key LIKE '%copilot%' OR key LIKE '%interactive-session%'
        """).fetchall()
        for r in rows:
            val = r["value"]
            if not val:
                continue
            try:
                data = json.loads(val)
                if isinstance(data, dict):
                    requests = data.get("requests") or []
                    turns = []
                    for req in requests:
                        if isinstance(req, dict):
                            p_txt = _clean_text(req.get("message") or req.get("text"))
                            if p_txt:
                                turns.append({
                                    "role": "user",
                                    "prompt_text": p_txt,
                                    "output_text": _clean_text(req.get("response")),
                                    "timestamp": utc_now(),
                                    "model": "vscode-copilot",
                                })
                    if turns:
                        sessions.append({
                            "session_id": f"vsc_{hashlib.md5(r['key'].encode()).hexdigest()[:12]}",
                            "software": "vscode",
                            "project_name": _infer_project_name(str(db_path.parent)),
                            "title": turns[0]["prompt_text"].splitlines()[0][:80].strip(),
                            "source_file": str(db_path),
                            "created_at": utc_now(),
                            "updated_at": utc_now(),
                            "turns": turns,
                            "model": "vscode-copilot",
                        })
            except Exception:
                pass

        conn.close()
    except Exception as exc:
        logger.debug("Error reading VS Code state DB %s: %s", db_path, exc)

    return sessions


def parse_cursor_composer_storage(db_path: Path) -> List[Dict[str, Any]]:
    """Parse Cursor composer and chat conversations from state.vscdb SQLite file."""
    sessions: List[Dict[str, Any]] = []
    if not db_path.is_file():
        return sessions

    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT key, value FROM ItemTable
            WHERE key LIKE '%composer.composerData%'
               OR key LIKE '%workbench.panel.aichat%'
        """).fetchall()

        for r in rows:
            val = r["value"]
            if not val:
                continue
            try:
                data = json.loads(val)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            # Composer conversations
            conversations = data.get("conversations") or data.get("composerData") or []
            if isinstance(conversations, dict):
                conversations = list(conversations.values())
            if not isinstance(conversations, list):
                continue

            for conv in conversations:
                if not isinstance(conv, dict):
                    continue
                cid = str(conv.get("conversationId") or conv.get("id") or uuid.uuid4().hex[:12])
                name = str(conv.get("name") or "Composer Session")
                headers = conv.get("headers") or conv.get("messages") or []
                turns: List[Dict[str, Any]] = []

                for m in headers:
                    if not isinstance(m, dict):
                        continue
                    role = str(m.get("type") or m.get("role") or "").lower()
                    text = _clean_text(m.get("text") or m.get("content"))
                    if not text:
                        continue
                    if role in ("user", "human"):
                        turns.append({
                            "role": "user",
                            "prompt_text": text,
                            "timestamp": utc_now(),
                            "model": "cursor",
                        })
                    elif role in ("assistant", "ai"):
                        if turns and turns[-1].get("role") == "user":
                            turns[-1]["output_text"] = text
                        else:
                            turns.append({
                                "role": "assistant",
                                "prompt_text": "",
                                "output_text": text,
                                "timestamp": utc_now(),
                            })

                if turns:
                    sessions.append({
                        "session_id": cid,
                        "software": "cursor",
                        "project_name": _infer_project_name(str(conv.get("projectPath") or "")),
                        "title": name,
                        "source_file": str(db_path),
                        "created_at": utc_now(),
                        "updated_at": utc_now(),
                        "turns": turns,
                        "model": "cursor",
                    })

        conn.close()
    except Exception as exc:
        logger.debug("Error reading Cursor DB %s: %s", db_path, exc)

    return sessions


# ─── Multi-Source Scanner & Incremental Harvester ───────────────────────────

class PromptsCollector:
    """Discovers and incrementally syncs prompts across all AI software."""

    def __init__(self, db: Optional[PromptsDatabase] = None):
        self.db = db or PromptsDatabase()
        self._sync_lock = threading.Lock()
        self._is_syncing = False
        self._last_run_id = ""

    @property
    def is_syncing(self) -> bool:
        return self._is_syncing

    def scan_and_collect(
        self,
        full_resync: bool = False,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        """Run incremental or full collection sync across all detected software."""
        if not self._sync_lock.acquire(blocking=False):
            return {"status": "busy", "message": "Another collection sync is already in progress."}

        run_id = uuid.uuid4().hex[:12]
        self._last_run_id = run_id
        self._is_syncing = True
        self.db.record_sync_start(run_id)

        sources_scanned = 0
        new_prompts_count = 0
        updated_sessions_count = 0
        error_msg: Optional[str] = None

        try:
            scan_paths = get_default_scan_paths()
            all_files: List[Tuple[str, Path]] = []

            # 1. Gather candidate files
            for software, roots in scan_paths.items():
                for root in roots:
                    if not root.exists():
                        continue
                    if root.is_file():
                        all_files.append((software, root))
                    elif root.is_dir():
                        if software == "antigravity":
                            patterns = ("transcript.jsonl", "transcript_full.jsonl")
                        elif software in ("vscode", "cursor"):
                            patterns = ("*.jsonl", "*.json", "*.vscdb")
                        else:
                            patterns = ("*.jsonl", "*.json")
                        for pat in patterns:
                            try:
                                for f in root.rglob(pat):
                                    if f.is_file():
                                        all_files.append((software, f))
                            except (OSError, PermissionError):
                                continue

            total_files = len(all_files)
            logger.info("PromptsCollector starting sync %s: %d candidate files found.", run_id, total_files)

            # 2. Process each candidate file incrementally
            for idx, (software, file_path) in enumerate(all_files):
                sources_scanned += 1
                if progress_callback:
                    progress_callback(str(file_path), idx + 1, total_files)

                try:
                    stat = file_path.stat()
                    mtime = stat.st_mtime
                    fsize = stat.st_size

                    # Check cache for incremental sync
                    cached = self.db.get_cached_file(str(file_path))
                    if not full_resync and cached:
                        # Only skip if the file was previously parsed and yielded sessions.
                        # Files cached with 0 sessions are re-evaluated so updated parsers
                        # (such as Copilot drafts, deltas, and state.vscdb) can extract them.
                        if cached["mtime"] >= mtime and cached["file_size"] == fsize and cached.get("session_count", 0) > 0:
                            continue  # File unchanged since last sync

                    # Parse according to software
                    parsed_sessions: List[Dict[str, Any]] = []
                    if software == "antigravity":
                        res = parse_antigravity_transcript(file_path)
                        if res:
                            parsed_sessions = [res]
                    elif software == "codex":
                        parsed_sessions = parse_codex_file(file_path)
                    elif software == "claude":
                        res = parse_claude_file(file_path)
                        if res:
                            parsed_sessions = [res]
                    elif software == "vscode":
                        if file_path.suffix == ".vscdb":
                            parsed_sessions = parse_vscode_state_db(file_path)
                        else:
                            res = parse_vscode_chat_session(file_path)
                            if res:
                                parsed_sessions = [res]
                    elif software == "cursor":
                        if file_path.name.endswith(".vscdb"):
                            parsed_sessions = parse_cursor_composer_storage(file_path)

                    # Ingest parsed sessions into DB via high-throughput transaction bundle
                    file_sess_count = len(parsed_sessions)
                    for sess in parsed_sessions:
                        sid = sess["session_id"]
                        p_name = sess["project_name"]
                        proj_id = f"proj_{software}_{hashlib.md5(p_name.encode()).hexdigest()[:8]}"

                        self.db.upsert_project(proj_id, p_name, str(file_path.parent), software)
                        inserted_turns = self.db.insert_session_bundle(
                            session_id=sid,
                            software=software,
                            project_name=p_name,
                            title=sess["title"],
                            source_file=sess.get("source_file"),
                            created_at=sess.get("created_at"),
                            updated_at=sess.get("updated_at"),
                            model=sess.get("model"),
                            project_id=proj_id,
                            turns=sess.get("turns", []),
                        )
                        new_prompts_count += inserted_turns
                        updated_sessions_count += 1

                    # Update cache entry
                    self.db.update_cached_file(str(file_path), software, mtime, fsize, file_sess_count)

                except (OSError, PermissionError) as exc:
                    logger.debug("Skipping unreadable file %s: %s", file_path, exc)
                except Exception as exc:
                    logger.warning("Error harvesting file %s: %s", file_path, exc)

            status = "completed"
        except Exception as exc:
            status = "failed"
            error_msg = str(exc)
            logger.exception("PromptsCollector sync failed: %s", exc)
        finally:
            self._is_syncing = False
            self.db.record_sync_finish(
                run_id=run_id,
                status=status,
                sources_scanned=sources_scanned,
                new_prompts=new_prompts_count,
                updated_sessions=updated_sessions_count,
                error_message=error_msg,
            )
            self._sync_lock.release()

        return {
            "run_id": run_id,
            "status": status,
            "sources_scanned": sources_scanned,
            "new_prompts": new_prompts_count,
            "updated_sessions": updated_sessions_count,
            "error_message": error_msg,
        }

    def ingest_push_prompt(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Ingest single prompt pushed from external software hook."""
        prompt_text = str(payload.get("prompt_text") or payload.get("prompt") or "").strip()
        if not prompt_text:
            raise ValueError("prompt_text cannot be empty")

        software = str(payload.get("software") or "custom_hook").strip().lower()
        project_name = str(payload.get("project") or payload.get("project_name") or "Custom").strip()
        session_id = str(payload.get("session_id") or f"push_{uuid.uuid4().hex[:10]}")
        title = str(payload.get("title") or prompt_text.splitlines()[0][:80]).strip()
        output_text = payload.get("output_text")
        attachments = payload.get("attachments") or []
        model = payload.get("model")
        ts = payload.get("timestamp") or utc_now()

        proj_id = f"proj_{software}_{hashlib.md5(project_name.encode()).hexdigest()[:8]}"
        self.db.upsert_project(proj_id, project_name, payload.get("cwd") or "", software)

        # Get existing turn count for this session
        sess = self.db.get_session_by_id(session_id)
        turn_index = len(sess.get("turns", [])) if sess else 0

        self.db.upsert_session(
            session_id=session_id,
            software=software,
            project_name=project_name,
            title=title,
            source_file="hook_push",
            created_at=ts,
            updated_at=ts,
            turn_count=turn_index + 1,
            model=model,
            project_id=proj_id,
        )

        prompt_id = f"{session_id}_t{turn_index}"
        self.db.insert_prompt(
            prompt_id=prompt_id,
            session_id=session_id,
            turn_index=turn_index,
            role="user",
            prompt_text=prompt_text,
            output_text=output_text,
            attachments=attachments,
            model=model,
            timestamp=ts,
            is_final_output=bool(output_text),
            metadata=payload.get("metadata"),
        )

        return {
            "status": "success",
            "prompt_id": prompt_id,
            "session_id": session_id,
            "turn_index": turn_index,
        }


# ─── Background Periodic Scheduler Thread ────────────────────────────────────

class CollectorScheduler(threading.Thread):
    """Background thread that triggers collection runs periodically."""

    def __init__(self, collector: PromptsCollector, check_interval_seconds: int = 60):
        super().__init__(daemon=True, name="PromptsCollectorScheduler")
        self.collector = collector
        self.check_interval_seconds = check_interval_seconds
        self._stop_event = threading.Event()
        self._last_run_time: float = 0.0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        logger.info("PromptsCollectorScheduler background thread started.")
        # Mark any stale running job from previous crash/restart as interrupted
        try:
            with self.collector.db._connection() as conn:
                conn.execute("UPDATE sync_history SET status = 'interrupted' WHERE status = 'running'")
        except Exception:
            pass

        stats = self.collector.db.get_stats()
        collection_enabled = self.collector.db.get_setting("collection_enabled", False)
        interval_mode = self.collector.db.get_setting("sync_interval", "manual")
        interval_minutes = SYNC_INTERVAL_MINUTES.get(interval_mode, 0)

        # Trigger initial harvest if explicitly enabled and scheduled
        if collection_enabled and interval_minutes > 0 and (stats["total_prompts"] == 0 or stats["by_software"].get("antigravity", 0) == 0):
            logger.info("Performing prompts harvest across detected software...")
            try:
                self.collector.scan_and_collect(full_resync=False)
                self._last_run_time = time.time()
            except Exception as exc:
                logger.warning("Prompts harvest error: %s", exc)

        while not self._stop_event.is_set():
            try:
                collection_enabled = self.collector.db.get_setting("collection_enabled", False)
                interval_mode = self.collector.db.get_setting("sync_interval", "manual")
                interval_minutes = SYNC_INTERVAL_MINUTES.get(interval_mode, 0)

                if collection_enabled and interval_minutes > 0:
                    interval_seconds = interval_minutes * 60
                    now = time.time()
                    if now - self._last_run_time >= interval_seconds:
                        logger.info("Triggering scheduled prompts harvest (%s interval)...", interval_mode)
                        self.collector.scan_and_collect(full_resync=False)
                        self._last_run_time = time.time()
            except Exception as exc:
                logger.error("Error in PromptsCollectorScheduler tick: %s", exc)

            self._stop_event.wait(self.check_interval_seconds)


_GLOBAL_COLLECTOR: Optional[PromptsCollector] = None
_GLOBAL_SCHEDULER: Optional[CollectorScheduler] = None
_COLLECTOR_INIT_LOCK = threading.Lock()


def get_global_collector() -> PromptsCollector:
    global _GLOBAL_COLLECTOR, _GLOBAL_SCHEDULER
    with _COLLECTOR_INIT_LOCK:
        if _GLOBAL_COLLECTOR is None:
            _GLOBAL_COLLECTOR = PromptsCollector()
            # Start background scheduler if not disabled by environment
            if os.environ.get("AUTOYOU_DISABLE_PROMPTS_SCHEDULER") != "1" and not os.environ.get("AUTOYOU_TEST_ROOT"):
                _GLOBAL_SCHEDULER = CollectorScheduler(_GLOBAL_COLLECTOR)
                _GLOBAL_SCHEDULER.start()
        return _GLOBAL_COLLECTOR
