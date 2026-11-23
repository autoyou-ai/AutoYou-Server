# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Resumable, checkpointed multi-stage Media Factory for long-running videos and image editing.

Projects and scenes persist inside ``media_history.db`` so multi-hour renders can
be edited, paused, resumed after server restarts, refined scene-by-scene, and
assembled into a master MP4 via FFmpeg without losing completed work.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import json
import math
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from shared.media_generation_runtime import (
    assemble_video_clips,
    extract_video_frame,
    probe_media_file,
)
from . import media_generation_tool as mgt

VALID_PROJECT_KINDS = ("long_video", "short_video", "image", "image_edit", "video_edit")
VALID_PROJECT_STATUSES = (
    "draft",
    "script_ready",
    "storyboard_ready",
    "rendering",
    "refining",
    "assembling",
    "paused",
    "completed",
    "failed",
    "cancelled",
)
VALID_STAGES = ("plan", "keyframes", "render", "refine", "assemble")

# Max scenes planned in one LLM call; multi-hour films are planned chapter-by-chapter.
_PLAN_BATCH_MAX_SCENES = 16


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    return Path(mgt.DB_PATH)


def _output_dir() -> Path:
    return Path(mgt.OUTPUT_DIR)


def _connect() -> sqlite3.Connection:
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_factory_db() -> None:
    """Create or migrate the Media Factory tables inside media_history.db."""
    mgt.init_db()
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'long_video',
                status TEXT NOT NULL DEFAULT 'draft',
                target_duration_seconds REAL NOT NULL DEFAULT 30.0,
                scene_duration_seconds REAL NOT NULL DEFAULT 5.0,
                fps INTEGER NOT NULL DEFAULT 24,
                aspect_ratio TEXT NOT NULL DEFAULT '16:9',
                resolution TEXT NOT NULL DEFAULT '848x480',
                engine TEXT NOT NULL DEFAULT '',
                video_model TEXT NOT NULL DEFAULT '',
                image_model TEXT NOT NULL DEFAULT '',
                edit_model TEXT NOT NULL DEFAULT '',
                memory_profile TEXT NOT NULL DEFAULT 'auto',
                llm_provider_override TEXT NOT NULL DEFAULT '',
                llm_model_override TEXT NOT NULL DEFAULT '',
                original_request TEXT NOT NULL DEFAULT '',
                style_bible TEXT NOT NULL DEFAULT '',
                script_text TEXT NOT NULL DEFAULT '',
                embedded_context_json TEXT NOT NULL DEFAULT '{}',
                settings_json TEXT NOT NULL DEFAULT '{}',
                goal_run_id TEXT NOT NULL DEFAULT '',
                final_output_path TEXT NOT NULL DEFAULT '',
                final_file_name TEXT NOT NULL DEFAULT '',
                final_history_item_id INTEGER,
                progress_percent REAL NOT NULL DEFAULT 0.0,
                current_stage TEXT NOT NULL DEFAULT 'plan',
                progress_message TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                owner_pid INTEGER,
                owner_started_at REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_scenes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES media_projects(id) ON DELETE CASCADE,
                scene_index INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                duration_seconds REAL NOT NULL DEFAULT 5.0,
                frames INTEGER NOT NULL DEFAULT 49,
                prompt TEXT NOT NULL DEFAULT '',
                enhanced_prompt TEXT NOT NULL DEFAULT '',
                negative_prompt TEXT NOT NULL DEFAULT '',
                camera_direction TEXT NOT NULL DEFAULT '',
                audio_cue TEXT NOT NULL DEFAULT '',
                narration_text TEXT NOT NULL DEFAULT '',
                transition TEXT NOT NULL DEFAULT 'cut',
                reference_image_path TEXT NOT NULL DEFAULT '',
                input_video_path TEXT NOT NULL DEFAULT '',
                input_audio_path TEXT NOT NULL DEFAULT '',
                keyframe_image_path TEXT NOT NULL DEFAULT '',
                rendered_clip_path TEXT NOT NULL DEFAULT '',
                refined_clip_path TEXT NOT NULL DEFAULT '',
                seed INTEGER NOT NULL DEFAULT -1,
                steps INTEGER,
                resolution TEXT NOT NULL DEFAULT '',
                model_type TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                quality_score REAL,
                quality_notes TEXT NOT NULL DEFAULT '',
                history_item_id INTEGER,
                error_message TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_stage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES media_projects(id) ON DELETE CASCADE,
                stage TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_scenes_project ON media_scenes(project_id, scene_index)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_logs_project ON media_stage_logs(project_id, id)")
        conn.commit()
    finally:
        conn.close()


def _project_workspace_dir(project_id: int) -> Path:
    root = _output_dir() / "projects" / f"project_{int(project_id)}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _archive_project_asset(project_id: int, source_path: str, *, subdir: str = "assets") -> str:
    """Copy an external/temp/content-referenced media file into the project workspace as clean bytes."""
    raw = str(source_path or "").strip()
    if not raw:
        return ""
    src = Path(raw).expanduser()
    if not src.is_file():
        return ""
    assets_dir = _project_workspace_dir(project_id) / subdir
    assets_dir.mkdir(parents=True, exist_ok=True)
    try:
        if src.resolve().parent == assets_dir.resolve():
            return str(src.resolve())
    except OSError:
        pass
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", src.name).strip("._") or "asset.bin"
    dest = assets_dir / safe_name
    try:
        from shared import content_files

        expected_size = int(content_files.size(src))
        is_protected = bool(content_files.protected(src))
    except Exception:
        expected_size = src.stat().st_size
        is_protected = False
    counter = 1
    while dest.exists() and dest.stat().st_size != expected_size:
        counter += 1
        dest = assets_dir / f"{dest.stem}_{counter}{dest.suffix}"
    if not dest.exists():
        if is_protected:
            from shared import content_files

            with dest.open("wb") as handle:
                for chunk in content_files.read_range(src):
                    handle.write(chunk)
        else:
            shutil.copy2(src, dest)
    return str(dest)


def append_stage_log(
    project_id: int,
    stage: str,
    message: str,
    *,
    level: str = "info",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    init_factory_db()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO media_stage_logs (project_id, stage, level, message, details_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                int(project_id),
                str(stage or "general"),
                str(level or "info"),
                str(message or "")[:2000],
                json.dumps(details or {}, ensure_ascii=False),
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _decode_json_field(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        parsed = json.loads(str(raw or ""))
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except Exception:
        return fallback


def _hydrate_scene(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["has_keyframe"] = bool(item.get("keyframe_image_path") and Path(item["keyframe_image_path"]).is_file())
    clip_path = item.get("refined_clip_path") or item.get("rendered_clip_path") or ""
    item["active_clip_path"] = clip_path
    item["has_clip"] = bool(clip_path and Path(clip_path).is_file())
    item["keyframe_file_name"] = Path(item["keyframe_image_path"]).name if item["has_keyframe"] else ""
    item["clip_file_name"] = Path(clip_path).name if item["has_clip"] else ""
    return item


def _hydrate_project(row: sqlite3.Row, *, include_scenes: bool = True, include_logs: bool = False) -> Dict[str, Any]:
    project = dict(row)
    project["embedded_context"] = _decode_json_field(project.pop("embedded_context_json", "{}"), {})
    project["settings"] = _decode_json_field(project.pop("settings_json", "{}"), {})
    project["has_final_output"] = bool(project.get("final_output_path") and Path(project["final_output_path"]).is_file())
    if include_scenes or include_logs:
        conn = _connect()
        try:
            if include_scenes:
                rows = conn.execute(
                    "SELECT * FROM media_scenes WHERE project_id = ? ORDER BY scene_index ASC, id ASC",
                    (project["id"],),
                ).fetchall()
                scenes = [_hydrate_scene(r) for r in rows]
                project["scenes"] = scenes
                project["scene_count"] = len(scenes)
                project["rendered_scene_count"] = sum(1 for s in scenes if s["has_clip"])
                project["total_planned_duration_seconds"] = round(sum(float(s.get("duration_seconds") or 0) for s in scenes), 2)
            if include_logs:
                log_rows = conn.execute(
                    "SELECT * FROM media_stage_logs WHERE project_id = ? ORDER BY id DESC LIMIT 60",
                    (project["id"],),
                ).fetchall()
                project["stage_logs"] = [
                    {
                        **dict(lr),
                        "details": _decode_json_field(lr["details_json"], {}),
                    }
                    for lr in reversed(log_rows)
                ]
        finally:
            conn.close()
    return project


def sanitize_project_for_api(project: Dict[str, Any]) -> Dict[str, Any]:
    """Redact host filesystem paths from project payloads exposed over HTTP/chat."""
    out = dict(project or {})
    if out.get("final_output_path"):
        fname = out.get("final_file_name") or Path(str(out["final_output_path"])).name
        out["final_output_path"] = f"[local media file]/{fname}"
    for key in ("error_message", "progress_message"):
        if key in out:
            out[key] = mgt.redact_local_paths_for_display(out[key])
    if isinstance(out.get("scenes"), list):
        safe_scenes = []
        for scene in out["scenes"]:
            s = dict(scene)
            for pkey in ("reference_image_path", "input_video_path", "input_audio_path", "keyframe_image_path", "rendered_clip_path", "refined_clip_path", "active_clip_path"):
                val = str(s.get(pkey) or "").strip()
                if val:
                    s[pkey] = f"[project file]/{Path(val).name}"
            if s.get("error_message"):
                s["error_message"] = mgt.redact_local_paths_for_display(s["error_message"])
            safe_scenes.append(s)
        out["scenes"] = safe_scenes
    if isinstance(out.get("stage_logs"), list):
        out["stage_logs"] = [
            {
                "id": entry.get("id"),
                "stage": entry.get("stage"),
                "level": entry.get("level"),
                "message": mgt.redact_local_paths_for_display(entry.get("message")),
                "created_at": entry.get("created_at"),
            }
            for entry in out["stage_logs"]
        ]
    return out


def reconcile_interrupted_projects() -> int:
    """Move projects whose owner process died cleanly to 'paused' so work can be resumed."""
    init_factory_db()
    conn = _connect()
    reconciled = 0
    try:
        rows = conn.execute(
            "SELECT id, owner_pid, owner_started_at FROM media_projects "
            "WHERE status IN ('rendering', 'refining', 'assembling')"
        ).fetchall()
        for row in rows:
            if not mgt._job_owner_alive(row["owner_pid"], row["owner_started_at"]):
                conn.execute(
                    "UPDATE media_projects SET status='paused', "
                    "progress_message='Paused after server restart — click Resume to continue from the last completed scene.', "
                    "updated_at=? WHERE id=?",
                    (_now_iso(), row["id"]),
                )
                reconciled += 1
        conn.commit()
    finally:
        conn.close()
    return reconciled


def _resolution_for_aspect(aspect_ratio: str, default_resolution: str) -> str:
    presets = {
        "16:9": "848x480",
        "16:9-hd": "1280x720",
        "9:16": "480x848",
        "9:16-hd": "720x1280",
        "1:1": "512x512",
        "4:3": "768x576",
        "21:9": "1024x448",
    }
    key = str(aspect_ratio or "").strip().lower()
    return presets.get(key, default_resolution)


def create_project(
    *,
    title: str,
    original_request: str,
    kind: str = "long_video",
    target_duration_seconds: float = 30.0,
    scene_duration_seconds: Optional[float] = None,
    fps: Optional[int] = None,
    aspect_ratio: str = "16:9",
    resolution: Optional[str] = None,
    engine: Optional[str] = None,
    video_model: Optional[str] = None,
    image_model: Optional[str] = None,
    edit_model: Optional[str] = None,
    memory_profile: Optional[str] = None,
    llm_provider_override: Optional[str] = None,
    llm_model_override: Optional[str] = None,
    style_bible: str = "",
    embedded_context: Optional[Dict[str, Any]] = None,
    attachments: Optional[Sequence[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
    plan_now: bool = False,
) -> Dict[str, Any]:
    """Create a persistent Media Factory project with embedded multimodal context."""
    init_factory_db()
    cfg = mgt.get_wan2gp_config()
    norm_kind = str(kind or "long_video").strip().lower()
    if norm_kind not in VALID_PROJECT_KINDS:
        norm_kind = "long_video"
    clean_title = str(title or "").strip()[:140] or (str(original_request or "Untitled Production").strip()[:64] or "Untitled Production")
    target_dur = max(2.0, min(86400.0, float(target_duration_seconds or 30.0)))
    scene_dur = max(2.0, min(20.0, float(scene_duration_seconds or cfg.get("default_scene_duration_seconds") or 5.0)))
    effective_fps = max(8, min(60, int(fps or cfg.get("video_fps") or 24)))
    effective_res = str(resolution or _resolution_for_aspect(aspect_ratio, str(cfg.get("resolution") or "848x480"))).strip()
    now = _now_iso()

    ctx_payload = dict(embedded_context or {})
    merged_settings = {
        "continuity_chain_frames": bool(cfg.get("continuity_chain_frames", True)),
        "use_keyframes": False,
        "transition": "crossfade",
        "transition_seconds": 0.35,
        "video_steps": int(cfg.get("num_inference_steps") or 8),
        "image_steps": int(cfg.get("image_num_inference_steps") or 10),
        **(settings or {}),
    }

    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO media_projects (
                title, kind, status, target_duration_seconds, scene_duration_seconds,
                fps, aspect_ratio, resolution, engine, video_model, image_model, edit_model,
                memory_profile, llm_provider_override, llm_model_override,
                original_request, style_bible, script_text, embedded_context_json, settings_json,
                current_stage, progress_message, created_at, updated_at
            ) VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, 'plan', 'Project created.', ?, ?)
            """,
            (
                clean_title,
                norm_kind,
                target_dur,
                scene_dur,
                effective_fps,
                str(aspect_ratio or "16:9"),
                effective_res,
                str(engine or cfg.get("engine") or "wan2gp"),
                str(video_model or cfg.get("model_type") or ""),
                str(image_model or cfg.get("image_model_type") or ""),
                str(edit_model or cfg.get("image_edit_model_type") or ""),
                str(memory_profile or cfg.get("memory_profile") or "auto"),
                str(llm_provider_override or cfg.get("llm_provider_override") or ""),
                str(llm_model_override or cfg.get("llm_model_override") or ""),
                str(original_request or "").strip(),
                str(style_bible or "").strip(),
                json.dumps(ctx_payload, ensure_ascii=False),
                json.dumps(merged_settings, ensure_ascii=False),
                now,
                now,
            ),
        )
        project_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    # Archive attached images, videos, and audio inside the project workspace.
    archived_refs: Dict[str, List[str]] = {"images": [], "videos": [], "audio": []}
    for att in attachments or ():
        if not isinstance(att, dict):
            continue
        raw_path = str(att.get("path") or "").strip()
        if not raw_path:
            continue
        saved = _archive_project_asset(project_id, raw_path)
        if not saved:
            continue
        mime = str(att.get("mimetype") or "").lower()
        att_kind = str(att.get("kind") or (att.get("meta") or {}).get("kind") or "").lower()
        if mime.startswith("image/") or att_kind == "image":
            archived_refs["images"].append(saved)
        elif mime.startswith("video/") or att_kind == "video":
            archived_refs["videos"].append(saved)
        elif mime.startswith("audio/") or att_kind in {"audio", "voice"}:
            archived_refs["audio"].append(saved)

    if any(archived_refs.values()):
        ctx_payload["archived_media"] = archived_refs
        update_project(project_id, {"embedded_context": ctx_payload})

    append_stage_log(project_id, "plan", f"Created project '{clean_title}' (target {target_dur:.0f}s @ {effective_res}).")
    if plan_now:
        return plan_project_script(project_id)
    return get_project(project_id, include_logs=True)  # type: ignore[return-value]


def get_project(project_id: int, *, include_scenes: bool = True, include_logs: bool = False) -> Optional[Dict[str, Any]]:
    init_factory_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM media_projects WHERE id = ?", (int(project_id),)).fetchone()
        if not row:
            return None
    finally:
        conn.close()
    return _hydrate_project(row, include_scenes=include_scenes, include_logs=include_logs)


def list_projects(limit: int = 30) -> List[Dict[str, Any]]:
    reconcile_interrupted_projects()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM media_projects ORDER BY id DESC LIMIT ?",
            (max(1, min(200, int(limit or 30))),),
        ).fetchall()
    finally:
        conn.close()
    return [_hydrate_project(r, include_scenes=True, include_logs=False) for r in rows]


def delete_project(project_id: int) -> bool:
    init_factory_db()
    project = get_project(project_id, include_scenes=False)
    if not project:
        return False
    conn = _connect()
    try:
        conn.execute("DELETE FROM media_projects WHERE id = ?", (int(project_id),))
        conn.commit()
    finally:
        conn.close()
    work_dir = _output_dir() / "projects" / f"project_{int(project_id)}"
    if work_dir.is_dir():
        shutil.rmtree(work_dir, ignore_errors=True)
    return True


def update_project(project_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    init_factory_db()
    existing = get_project(project_id, include_scenes=False)
    if not existing:
        return None
    allowed_scalar = {
        "title", "kind", "status", "target_duration_seconds", "scene_duration_seconds",
        "fps", "aspect_ratio", "resolution", "engine", "video_model", "image_model",
        "edit_model", "memory_profile", "llm_provider_override", "llm_model_override",
        "original_request", "style_bible", "script_text", "goal_run_id",
        "final_output_path", "final_file_name", "final_history_item_id",
        "progress_percent", "current_stage", "progress_message", "error_message",
        "owner_pid", "owner_started_at",
    }
    assignments: List[str] = ["updated_at = ?"]
    values: List[Any] = [_now_iso()]
    for key, val in (updates or {}).items():
        if key in allowed_scalar:
            assignments.append(f"{key} = ?")
            values.append(val)
        elif key == "embedded_context" and isinstance(val, dict):
            merged = {**existing.get("embedded_context", {}), **val}
            assignments.append("embedded_context_json = ?")
            values.append(json.dumps(merged, ensure_ascii=False))
        elif key == "settings" and isinstance(val, dict):
            merged = {**existing.get("settings", {}), **val}
            assignments.append("settings_json = ?")
            values.append(json.dumps(merged, ensure_ascii=False))
    values.append(int(project_id))
    conn = _connect()
    try:
        conn.execute(f"UPDATE media_projects SET {', '.join(assignments)} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()
    return get_project(project_id, include_scenes=True, include_logs=True)


# ── Stage 1: LLM Script & Scene Breakdown (supports multi-hour batched planning) ──


def _extract_json_payload(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1) if fenced else raw
    first = candidate.find("{")
    last = candidate.rfind("}")
    if first < 0 or last <= first:
        return None
    try:
        parsed = json.loads(candidate[first : last + 1])
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        return None


def _heuristic_scene_plan(
    *,
    title: str,
    brief: str,
    style_bible: str,
    start_index: int,
    count: int,
    total_scenes: int,
    scene_duration: float,
) -> Dict[str, Any]:
    beats = [
        "Opening establishing shot introducing the world and atmosphere",
        "Closer visual reveal focusing on the central subject in motion",
        "Dynamic tracking sequence deepening the narrative momentum",
        "Atmospheric detail beat with shifting light and visual texture",
        "Dramatic turning point with expressive camera movement",
        "Climactic visual crescendo highlighting the core story payoff",
        "Reflective resolution pulling back to a wide cinematic tableau",
    ]
    resolved_style = style_bible or "Cinematic natural lighting, rich depth of field, coherent color palette, smooth camera choreography, no text or watermarks."
    scenes = []
    for offset in range(count):
        idx = start_index + offset
        beat = beats[min(len(beats) - 1, int(idx * len(beats) / max(1, total_scenes)))]
        scenes.append(
            {
                "title": f"Scene {idx + 1}",
                "duration_seconds": scene_duration,
                "prompt": (
                    f"{brief}. {beat}. {resolved_style} "
                    "No readable text, no logos, and no watermark appear anywhere in the frame."
                ),
                "camera_direction": "Smooth slow dolly push-in" if idx % 2 == 0 else "Steady wide tracking pan",
                "audio_cue": "Ambient cinematic soundscape matching the scene",
                "narration_text": "",
                "transition": "cut" if idx == 0 else "crossfade",
            }
        )
    return {
        "style_bible": resolved_style,
        "script_text": "\n\n".join(f"SCENE {start_index + i + 1} ({s['title']}): {s['prompt']}" for i, s in enumerate(scenes)),
        "scenes": scenes,
    }


def plan_project_script(
    project_id: int,
    *,
    instructions: str = "",
    scene_count: Optional[int] = None,
    replace_existing: bool = True,
) -> Dict[str, Any]:
    """Generate an editable script, style bible, and timed scene list using the chosen LLM."""
    project = get_project(project_id, include_scenes=True)
    if not project:
        raise ValueError(f"Project #{project_id} not found.")

    target_dur = float(project["target_duration_seconds"] or 30.0)
    scene_dur = max(2.0, float(project["scene_duration_seconds"] or 5.0))
    fps = int(project["fps"] or 24)
    total_scenes = int(scene_count) if scene_count and int(scene_count) > 0 else max(1, int(math.ceil(target_dur / scene_dur)))
    brief = str(project["original_request"] or project["title"]).strip()
    if instructions and instructions.strip():
        brief = f"{brief}\nAdditional direction: {instructions.strip()}"

    embedded_ctx = project.get("embedded_context") or {}
    ctx_snippet = ""
    if embedded_ctx.get("conversation_summary"):
        ctx_snippet += f"\nConversation context: {str(embedded_ctx['conversation_summary'])[:1000]}"
    archived = embedded_ctx.get("archived_media") or {}
    if archived.get("images"):
        ctx_snippet += f"\nAttached reference images ({len(archived['images'])}): keep subject and style consistent with the attached visual reference."
    if archived.get("videos"):
        ctx_snippet += f"\nAttached reference video ({len(archived['videos'])}): continue or adapt its visual motion and aesthetic."
    if archived.get("audio"):
        ctx_snippet += f"\nAttached audio track ({len(archived['audio'])}): pace scenes to complement the audio rhythm and mood."

    all_scenes: List[Dict[str, Any]] = []
    style_bible = str(project.get("style_bible") or "").strip()
    script_parts: List[str] = []

    llm_info = mgt.get_active_llm_info(
        provider_override=project.get("llm_provider_override") or None,
        model_override=project.get("llm_model_override") or None,
    )
    append_stage_log(
        project_id, "plan",
        f"Planning {total_scenes} scene(s) for {target_dur:.0f}s video using {llm_info['provider']}/{llm_info['model']}.",
    )

    batches = max(1, int(math.ceil(total_scenes / _PLAN_BATCH_MAX_SCENES)))
    for batch_idx in range(batches):
        start_idx = batch_idx * _PLAN_BATCH_MAX_SCENES
        batch_size = min(_PLAN_BATCH_MAX_SCENES, total_scenes - start_idx)
        prior_summary = ""
        if all_scenes:
            tail_scenes = all_scenes[-3:]
            prior_summary = "\nPrevious scenes for strict visual/narrative continuity:\n" + "\n".join(
                f"- Scene {start_idx - len(tail_scenes) + i + 1}: {s.get('title', '')} — {str(s.get('prompt', ''))[:160]}"
                for i, s in enumerate(tail_scenes)
            )
        system_prompt = (
            "You are an expert film director and AI video production planner for long-form local video synthesis "
            "(Wan 2.2 / LTX-2 / ComfyUI / MLX).\n"
            "Return ONLY valid JSON with keys:\n"
            '  "style_bible": string (concise visual continuity rules: subjects, wardrobe, color palette, lens, lighting),\n'
            '  "script_text": string (human-readable screenplay/narrative outline for these scenes),\n'
            '  "scenes": list of objects, each with keys:\n'
            '    "title", "duration_seconds" (number), "prompt" (single paragraph chronological visual prompt under 120 words, no markdown), '
            '"camera_direction", "audio_cue", "narration_text", "transition" ("cut" or "crossfade").\n'
            "Make every scene flow seamlessly from the previous scene so long videos feel cohesive."
        )
        user_prompt = (
            f"Project title: {project['title']}\n"
            f"Total target duration: {target_dur:.0f} seconds ({total_scenes} total scenes, ~{scene_dur:.1f}s each).\n"
            f"Generate batch {batch_idx + 1}/{batches}: scenes {start_idx + 1} through {start_idx + batch_size} (exactly {batch_size} scenes).\n"
            f"Creative brief: {brief}\n"
            + (f"Existing style bible: {style_bible}\n" if style_bible else "")
            + ctx_snippet
            + prior_summary
        )
        raw_reply = mgt.llm_chat_complete(
            system_prompt,
            user_prompt,
            provider_override=project.get("llm_provider_override") or None,
            model_override=project.get("llm_model_override") or None,
            max_tokens=2800,
            temperature=0.5,
            timeout=90,
        )
        parsed = _extract_json_payload(raw_reply or "")
        if not parsed or not isinstance(parsed.get("scenes"), list) or not parsed["scenes"]:
            parsed = _heuristic_scene_plan(
                title=project["title"],
                brief=brief,
                style_bible=style_bible,
                start_index=start_idx,
                count=batch_size,
                total_scenes=total_scenes,
                scene_duration=scene_dur,
            )
        if not style_bible and parsed.get("style_bible"):
            style_bible = str(parsed["style_bible"]).strip()
        if parsed.get("script_text"):
            script_parts.append(str(parsed["script_text"]).strip())
        for offset, scene_obj in enumerate(parsed["scenes"][:batch_size]):
            if not isinstance(scene_obj, dict):
                continue
            dur = max(2.0, min(20.0, float(scene_obj.get("duration_seconds") or scene_dur)))
            all_scenes.append(
                {
                    "scene_index": start_idx + offset,
                    "title": str(scene_obj.get("title") or f"Scene {start_idx + offset + 1}").strip()[:120],
                    "duration_seconds": dur,
                    "frames": mgt.seconds_to_video_frames(dur, fps=fps),
                    "prompt": mgt._clean_prompt_text(str(scene_obj.get("prompt") or brief)),
                    "camera_direction": str(scene_obj.get("camera_direction") or "").strip()[:240],
                    "audio_cue": str(scene_obj.get("audio_cue") or "").strip()[:240],
                    "narration_text": str(scene_obj.get("narration_text") or "").strip()[:600],
                    "transition": "crossfade" if str(scene_obj.get("transition") or "").lower() == "crossfade" else "cut",
                }
            )

    # Pad if LLM returned fewer scenes than requested.
    while len(all_scenes) < total_scenes:
        idx = len(all_scenes)
        fallback = _heuristic_scene_plan(
            title=project["title"], brief=brief, style_bible=style_bible,
            start_index=idx, count=1, total_scenes=total_scenes, scene_duration=scene_dur,
        )["scenes"][0]
        all_scenes.append(
            {
                "scene_index": idx,
                "title": fallback["title"],
                "duration_seconds": scene_dur,
                "frames": mgt.seconds_to_video_frames(scene_dur, fps=fps),
                "prompt": mgt._clean_prompt_text(fallback["prompt"]),
                "camera_direction": fallback["camera_direction"],
                "audio_cue": fallback["audio_cue"],
                "narration_text": "",
                "transition": fallback["transition"],
            }
        )

    # Attach first archived multimodal reference to scene 0 by default so context is visually grounded!
    first_ref_img = (archived.get("images") or [""])[0]
    first_in_vid = (archived.get("videos") or [""])[0]
    first_in_aud = (archived.get("audio") or [""])[0]

    conn = _connect()
    now = _now_iso()
    try:
        if replace_existing:
            conn.execute("DELETE FROM media_scenes WHERE project_id = ?", (int(project_id),))
            start_offset = 0
        else:
            row = conn.execute("SELECT COALESCE(MAX(scene_index), -1) FROM media_scenes WHERE project_id = ?", (int(project_id),)).fetchone()
            start_offset = int(row[0]) + 1
        for idx, s in enumerate(all_scenes):
            scene_idx = start_offset + idx
            conn.execute(
                """
                INSERT INTO media_scenes (
                    project_id, scene_index, title, duration_seconds, frames, prompt,
                    enhanced_prompt, negative_prompt, camera_direction, audio_cue,
                    narration_text, transition, reference_image_path, input_video_path,
                    input_audio_path, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    int(project_id),
                    scene_idx,
                    s["title"],
                    s["duration_seconds"],
                    s["frames"],
                    s["prompt"],
                    s["camera_direction"],
                    s["audio_cue"],
                    s["narration_text"],
                    s["transition"],
                    first_ref_img if scene_idx == 0 else "",
                    first_in_vid if scene_idx == 0 else "",
                    first_in_aud if scene_idx == 0 else "",
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    full_script = "\n\n".join(script_parts) or "\n\n".join(
        f"SCENE {s['scene_index'] + 1} — {s['title']} ({s['duration_seconds']}s): {s['prompt']}" for s in all_scenes
    )
    updated = update_project(
        project_id,
        {
            "status": "script_ready",
            "current_stage": "plan",
            "style_bible": style_bible,
            "script_text": full_script,
            "progress_percent": 15.0,
            "progress_message": f"Script ready with {len(all_scenes)} editable scene(s).",
            "error_message": "",
        },
    )
    append_stage_log(project_id, "plan", f"Planned {len(all_scenes)} scene(s); script ready for editing or rendering.")
    return updated  # type: ignore[return-value]


# ── Scene Editing Helpers ───────────────────────────────────────────────────


def add_scene(project_id: int, scene_data: Dict[str, Any]) -> Dict[str, Any]:
    project = get_project(project_id, include_scenes=True)
    if not project:
        raise ValueError(f"Project #{project_id} not found.")
    fps = int(project.get("fps") or 24)
    dur = max(2.0, min(20.0, float(scene_data.get("duration_seconds") or project.get("scene_duration_seconds") or 5.0)))
    frames = int(scene_data.get("frames") or mgt.seconds_to_video_frames(dur, fps=fps))
    next_index = len(project.get("scenes") or [])
    ref_img = _archive_project_asset(project_id, str(scene_data.get("reference_image_path") or ""))
    in_vid = _archive_project_asset(project_id, str(scene_data.get("input_video_path") or ""))
    in_aud = _archive_project_asset(project_id, str(scene_data.get("input_audio_path") or ""))
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO media_scenes (
                project_id, scene_index, title, duration_seconds, frames, prompt,
                enhanced_prompt, negative_prompt, camera_direction, audio_cue,
                narration_text, transition, reference_image_path, input_video_path,
                input_audio_path, seed, steps, resolution, model_type, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                int(project_id),
                int(scene_data.get("scene_index") if scene_data.get("scene_index") is not None else next_index),
                str(scene_data.get("title") or f"Scene {next_index + 1}").strip()[:120],
                dur,
                frames,
                str(scene_data.get("prompt") or "").strip(),
                str(scene_data.get("enhanced_prompt") or "").strip(),
                str(scene_data.get("negative_prompt") or "").strip(),
                str(scene_data.get("camera_direction") or "").strip(),
                str(scene_data.get("audio_cue") or "").strip(),
                str(scene_data.get("narration_text") or "").strip(),
                str(scene_data.get("transition") or "crossfade").strip().lower(),
                ref_img,
                in_vid,
                in_aud,
                int(scene_data.get("seed") if scene_data.get("seed") is not None else -1),
                int(scene_data["steps"]) if scene_data.get("steps") else None,
                str(scene_data.get("resolution") or "").strip(),
                str(scene_data.get("model_type") or "").strip(),
                _now_iso(),
            ),
        )
        scene_id = int(cur.lastrowid)
        conn.commit()
        row = conn.execute("SELECT * FROM media_scenes WHERE id = ?", (scene_id,)).fetchone()
        return _hydrate_scene(row)
    finally:
        conn.close()


def update_scene(project_id: int, scene_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    project = get_project(project_id, include_scenes=False)
    if not project:
        return None
    fps = int(project.get("fps") or 24)
    allowed = {
        "scene_index", "title", "duration_seconds", "frames", "prompt", "enhanced_prompt",
        "negative_prompt", "camera_direction", "audio_cue", "narration_text", "transition",
        "reference_image_path", "input_video_path", "input_audio_path",
        "keyframe_image_path", "rendered_clip_path", "refined_clip_path",
        "seed", "steps", "resolution", "model_type", "status", "attempts",
        "quality_score", "quality_notes", "history_item_id", "error_message",
    }
    normalized = dict(updates or {})
    if "duration_seconds" in normalized and "frames" not in normalized:
        dur = max(2.0, min(20.0, float(normalized["duration_seconds"])))
        normalized["duration_seconds"] = dur
        normalized["frames"] = mgt.seconds_to_video_frames(dur, fps=fps)
    for asset_key in ("reference_image_path", "input_video_path", "input_audio_path"):
        if asset_key in normalized and normalized[asset_key]:
            archived = _archive_project_asset(project_id, str(normalized[asset_key]))
            if archived:
                normalized[asset_key] = archived

    assignments = ["updated_at = ?"]
    values: List[Any] = [_now_iso()]
    for key, val in normalized.items():
        if key in allowed:
            assignments.append(f"{key} = ?")
            values.append(val)
    values.extend([int(project_id), int(scene_id)])
    conn = _connect()
    try:
        conn.execute(
            f"UPDATE media_scenes SET {', '.join(assignments)} WHERE project_id = ? AND id = ?",
            values,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM media_scenes WHERE project_id = ? AND id = ?", (int(project_id), int(scene_id))).fetchone()
        return _hydrate_scene(row) if row else None
    finally:
        conn.close()


def delete_scene(project_id: int, scene_id: int) -> bool:
    init_factory_db()
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM media_scenes WHERE project_id = ? AND id = ?", (int(project_id), int(scene_id)))
        if cur.rowcount == 0:
            return False
        rows = conn.execute("SELECT id FROM media_scenes WHERE project_id = ? ORDER BY scene_index ASC, id ASC", (int(project_id),)).fetchall()
        for new_idx, r in enumerate(rows):
            conn.execute("UPDATE media_scenes SET scene_index = ? WHERE id = ?", (new_idx, r["id"]))
        conn.commit()
        return True
    finally:
        conn.close()


def enhance_scene_prompt(project_id: int, scene_id: int, *, direction: str = "") -> Dict[str, Any]:
    """Use the project's chosen LLM + style bible + embedded context to enhance one scene prompt."""
    project = get_project(project_id, include_scenes=True)
    if not project:
        raise ValueError(f"Project #{project_id} not found.")
    scene = next((s for s in project["scenes"] if int(s["id"]) == int(scene_id)), None)
    if not scene:
        raise ValueError(f"Scene #{scene_id} not found in project #{project_id}.")
    context_bits = []
    if project.get("style_bible"):
        context_bits.append(f"Style Bible: {project['style_bible']}")
    if scene.get("camera_direction"):
        context_bits.append(f"Camera: {scene['camera_direction']}")
    if scene.get("audio_cue"):
        context_bits.append(f"Audio/Mood: {scene['audio_cue']}")
    if direction:
        context_bits.append(f"Refinement note: {direction}")
    enhanced = mgt.ai_enhance_prompt(
        scene["prompt"] or project["original_request"],
        media_type="video",
        context_notes="\n".join(context_bits),
        llm_provider_override=project.get("llm_provider_override") or None,
        llm_model_override=project.get("llm_model_override") or None,
    )
    updated = update_scene(project_id, scene_id, {"enhanced_prompt": enhanced})
    append_stage_log(project_id, "plan", f"Enhanced Scene {scene['scene_index'] + 1} prompt via LLM.")
    return updated  # type: ignore[return-value]


# ── Standalone Image Modification / Editing ─────────────────────────────────


def modify_image_sync(
    *,
    instruction: str,
    source_image_path: str,
    model_type: Optional[str] = None,
    resolution: Optional[str] = None,
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    denoise_strength: float = 0.65,
    engine_override: Optional[str] = None,
    context_notes: Optional[str] = None,
    history_item_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Modify an existing image using instruction-guided editing models (Flux Kontext, Qwen Image Edit, MLX, ComfyUI)."""
    src = Path(str(source_image_path or "")).expanduser()
    if not src.is_file():
        return {"status": "error", "message": "Source image file was not found for modification."}
    return mgt.generate_media_sync(
        prompt=instruction,
        media_type="image",
        model_type=model_type,
        resolution=resolution,
        steps=steps,
        seed=seed,
        history_item_id=history_item_id,
        reference_image_path=str(src),
        edit_mode=True,
        denoise_strength=denoise_strength,
        engine_override=engine_override,
        context_notes=context_notes,
    )


# ── Stage 2: Keyframe / Storyboard Generation ───────────────────────────────


def run_keyframes_stage(
    project_id: int,
    *,
    scene_indices: Optional[Sequence[int]] = None,
    stop_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Generate visual anchor keyframes for scenes so long videos maintain strong visual continuity."""
    project = get_project(project_id, include_scenes=True)
    if not project:
        raise ValueError(f"Project #{project_id} not found.")
    scenes = project.get("scenes") or []
    if not scenes:
        project = plan_project_script(project_id)
        scenes = project.get("scenes") or []

    target_set = set(int(i) for i in scene_indices) if scene_indices is not None else None
    update_project(
        project_id,
        {
            "current_stage": "keyframes",
            "progress_message": "Generating storyboard keyframes...",
            "owner_pid": os.getpid(),
            "owner_started_at": mgt._process_start_time(os.getpid()),
        },
    )
    generated = 0
    failed = 0
    for idx, scene in enumerate(scenes):
        if stop_check and stop_check():
            update_project(project_id, {"status": "paused", "progress_message": "Paused during keyframe generation."})
            return {"status": "paused", "generated": generated, "failed": failed}
        if target_set is not None and scene["scene_index"] not in target_set:
            continue
        if scene.get("has_keyframe") and target_set is None:
            continue
        # If the scene already has a user-attached reference image, adopt it as keyframe directly.
        if scene.get("reference_image_path") and Path(scene["reference_image_path"]).is_file() and target_set is None:
            update_scene(project_id, scene["id"], {"keyframe_image_path": scene["reference_image_path"], "status": "keyframe_ready"})
            generated += 1
            continue
        prompt_text = scene.get("enhanced_prompt") or scene.get("prompt") or project["original_request"]
        if project.get("style_bible"):
            prompt_text = f"{prompt_text}. Style: {project['style_bible']}"
        append_stage_log(project_id, "keyframes", f"Generating keyframe for Scene {scene['scene_index'] + 1}/{len(scenes)}.")
        res = mgt.generate_media_sync(
            prompt=prompt_text,
            media_type="image",
            model_type=project.get("image_model") or None,
            resolution=scene.get("resolution") or project.get("resolution") or None,
            steps=(project.get("settings") or {}).get("image_steps"),
            seed=scene.get("seed", -1),
            engine_override=project.get("engine") or None,
            llm_provider_override=project.get("llm_provider_override") or None,
            llm_model_override=project.get("llm_model_override") or None,
        )
        if res.get("status") == "success" and res.get("file_path"):
            saved_kf = _archive_project_asset(project_id, str(res["file_path"]), subdir="keyframes")
            update_scene(project_id, scene["id"], {"keyframe_image_path": saved_kf, "status": "keyframe_ready", "error_message": ""})
            generated += 1
        else:
            update_scene(project_id, scene["id"], {"error_message": str(res.get("message") or "Keyframe failed")})
            failed += 1
        pct = 15.0 + 15.0 * ((idx + 1) / max(1, len(scenes)))
        update_project(project_id, {"progress_percent": round(pct, 1), "progress_message": f"Keyframes: {idx + 1}/{len(scenes)}"})

    update_project(
        project_id,
        {
            "status": "storyboard_ready" if failed == 0 else "paused",
            "progress_percent": 30.0 if failed == 0 else project.get("progress_percent", 20.0),
            "progress_message": f"Keyframes ready ({generated} generated, {failed} failed).",
        },
    )
    return {"status": "success" if failed == 0 else "partial", "generated": generated, "failed": failed}


# ── Stage 3: Staged Scene Rendering (with Continuity Chaining & Checkpoints) ─


def run_render_stage(
    project_id: int,
    *,
    scene_indices: Optional[Sequence[int]] = None,
    force_rerender: bool = False,
    stop_check: Optional[Callable[[], bool]] = None,
    on_scene_done: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Render scenes sequentially with per-scene DB checkpoints and optional last-frame chaining."""
    project = get_project(project_id, include_scenes=True)
    if not project:
        raise ValueError(f"Project #{project_id} not found.")
    scenes = project.get("scenes") or []
    if not scenes:
        project = plan_project_script(project_id)
        scenes = project.get("scenes") or []

    settings = project.get("settings") or {}
    chain_frames = bool(settings.get("continuity_chain_frames", True))
    target_set = set(int(i) for i in scene_indices) if scene_indices is not None else None
    app_dir = Path(mgt.get_wan2gp_config().get("app_dir") or "")

    update_project(
        project_id,
        {
            "status": "rendering",
            "current_stage": "render",
            "progress_message": f"Rendering {len(scenes)} scene(s) in stages...",
            "error_message": "",
            "owner_pid": os.getpid(),
            "owner_started_at": mgt._process_start_time(os.getpid()),
        },
    )

    rendered = 0
    failed = 0
    previous_clip_path: Optional[Path] = None

    for idx, scene in enumerate(scenes):
        if stop_check and stop_check():
            update_project(project_id, {"status": "paused", "progress_message": f"Paused before Scene {scene['scene_index'] + 1}."})
            return {"status": "paused", "rendered": rendered, "failed": failed}

        existing_clip = scene.get("refined_clip_path") or scene.get("rendered_clip_path") or ""
        if target_set is not None and scene["scene_index"] not in target_set:
            if existing_clip and Path(existing_clip).is_file():
                previous_clip_path = Path(existing_clip)
            continue

        if not force_rerender and existing_clip and Path(existing_clip).is_file() and scene.get("status") in {"rendered", "refined", "approved"}:
            previous_clip_path = Path(existing_clip)
            rendered += 1
            continue

        # Choose visual anchor: explicit keyframe/reference image -> else last frame of previous scene!
        ref_image = ""
        for cand in (scene.get("keyframe_image_path"), scene.get("reference_image_path")):
            if cand and Path(cand).is_file():
                ref_image = str(cand)
                break
        if not ref_image and chain_frames and previous_clip_path and previous_clip_path.is_file():
            try:
                chain_path = _project_workspace_dir(project_id) / "chain_frames" / f"before_scene_{scene['scene_index']:04d}.png"
                extract_video_frame(previous_clip_path, chain_path, position="last", app_dir=app_dir)
                ref_image = str(chain_path)
                append_stage_log(project_id, "render", f"Chained last frame of Scene {scene['scene_index']} into Scene {scene['scene_index'] + 1}.")
            except Exception as exc:
                append_stage_log(project_id, "render", f"Continuity frame extraction skipped for Scene {scene['scene_index'] + 1}: {exc}", level="warning")

        prompt_text = scene.get("enhanced_prompt") or scene.get("prompt") or project["original_request"]
        context_bits = []
        if project.get("style_bible"):
            context_bits.append(f"Visual style bible: {project['style_bible']}")
        if scene.get("camera_direction"):
            context_bits.append(f"Camera: {scene['camera_direction']}")
        if scene.get("audio_cue"):
            context_bits.append(f"Sound: {scene['audio_cue']}")

        update_scene(project_id, scene["id"], {"status": "rendering", "attempts": int(scene.get("attempts") or 0) + 1, "error_message": ""})
        update_project(
            project_id,
            {
                "progress_percent": round(30.0 + 45.0 * (idx / max(1, len(scenes))), 1),
                "progress_message": f"Rendering Scene {scene['scene_index'] + 1}/{len(scenes)}: {scene.get('title') or ''}",
            },
        )
        append_stage_log(project_id, "render", f"Rendering Scene {scene['scene_index'] + 1}/{len(scenes)} ({scene['frames']} frames).")

        item_id = mgt.save_history_item(
            media_type="video",
            original_prompt=prompt_text,
            optimized_prompt=scene.get("enhanced_prompt") or "Queued by Media Factory.",
            file_path="",
            file_name="",
            settings={
                "model_type": scene.get("model_type") or project.get("video_model") or None,
                "resolution": scene.get("resolution") or project.get("resolution") or None,
                "num_inference_steps": scene.get("steps") or settings.get("video_steps"),
                "video_length": scene.get("frames"),
                "seed": scene.get("seed", -1),
            },
            status="queued",
        )
        res = mgt.generate_media_sync(
            prompt=prompt_text,
            media_type="video",
            model_type=scene.get("model_type") or project.get("video_model") or None,
            resolution=scene.get("resolution") or project.get("resolution") or None,
            steps=scene.get("steps") or settings.get("video_steps"),
            frames=int(scene.get("frames") or 49),
            duration_seconds=float(scene.get("duration_seconds") or 5.0),
            fps=int(project.get("fps") or 24),
            seed=scene.get("seed", -1),
            history_item_id=item_id,
            optimized_prompt=scene.get("enhanced_prompt") or None,
            negative_prompt=scene.get("negative_prompt") or None,
            reference_image_path=ref_image or None,
            input_video_path=scene.get("input_video_path") or None,
            input_audio_path=scene.get("input_audio_path") or None,
            engine_override=project.get("engine") or None,
            llm_provider_override=project.get("llm_provider_override") or None,
            llm_model_override=project.get("llm_model_override") or None,
            context_notes="\n".join(context_bits) if context_bits else None,
        )

        if res.get("status") == "success" and res.get("file_path"):
            clip_archive = _archive_project_asset(project_id, str(res["file_path"]), subdir="clips")
            probe = probe_media_file(Path(clip_archive), app_dir=app_dir)
            if probe.get("valid"):
                updated_scene = update_scene(
                    project_id,
                    scene["id"],
                    {
                        "rendered_clip_path": clip_archive,
                        "enhanced_prompt": res.get("optimized_prompt") or scene.get("enhanced_prompt") or "",
                        "history_item_id": item_id,
                        "status": "rendered",
                        "quality_score": 1.0,
                        "quality_notes": f"{probe.get('width', 0)}x{probe.get('height', 0)}, {probe.get('duration_seconds', 0)}s",
                        "error_message": "",
                    },
                )
                previous_clip_path = Path(clip_archive)
                rendered += 1
                append_stage_log(project_id, "render", f"Scene {scene['scene_index'] + 1} rendered OK ({updated_scene['quality_notes']}).")
                if on_scene_done and updated_scene:
                    on_scene_done(updated_scene)
            else:
                err = f"Rendered clip failed media verification: {probe.get('error') or 'invalid video stream'}"
                update_scene(project_id, scene["id"], {"status": "failed", "history_item_id": item_id, "error_message": err, "quality_score": 0.0})
                append_stage_log(project_id, "render", f"Scene {scene['scene_index'] + 1} failed verification: {err}", level="error")
                failed += 1
        else:
            err = str(res.get("message") or "Scene render failed.")
            update_scene(project_id, scene["id"], {"status": "failed", "history_item_id": item_id, "error_message": err, "quality_score": 0.0})
            append_stage_log(project_id, "render", f"Scene {scene['scene_index'] + 1} failed: {err[:300]}", level="error")
            failed += 1

    pct = round(30.0 + 45.0 * (rendered / max(1, len(scenes))), 1)
    status = "rendered" if failed == 0 else ("paused" if rendered > 0 else "failed")
    update_project(
        project_id,
        {
            "status": "rendering" if status == "rendered" else status,
            "progress_percent": pct,
            "progress_message": f"Render stage finished: {rendered}/{len(scenes)} scenes complete" + (f", {failed} failed." if failed else "."),
        },
    )
    return {"status": "success" if failed == 0 else "partial", "rendered": rendered, "failed": failed, "total": len(scenes)}


# ── Stage 4: Verification & Refinement ──────────────────────────────────────


def run_refine_stage(
    project_id: int,
    *,
    scene_indices: Optional[Sequence[int]] = None,
    direction: str = "",
    force_rerender: bool = False,
    max_repair_attempts: int = 2,
    stop_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Verify every rendered clip and refine/repair any failed, weak, or user-selected scenes."""
    project = get_project(project_id, include_scenes=True)
    if not project:
        raise ValueError(f"Project #{project_id} not found.")
    scenes = project.get("scenes") or []
    target_set = set(int(i) for i in scene_indices) if scene_indices is not None else None
    app_dir = Path(mgt.get_wan2gp_config().get("app_dir") or "")

    update_project(
        project_id,
        {
            "status": "refining",
            "current_stage": "refine",
            "progress_percent": 78.0,
            "progress_message": "Verifying and refining rendered scenes...",
            "owner_pid": os.getpid(),
            "owner_started_at": mgt._process_start_time(os.getpid()),
        },
    )

    scenes_needing_repair: List[int] = []
    verified = 0
    for scene in scenes:
        if target_set is not None and scene["scene_index"] not in target_set:
            continue
        clip_path = scene.get("refined_clip_path") or scene.get("rendered_clip_path") or ""
        probe = probe_media_file(Path(clip_path), app_dir=app_dir) if clip_path else {"valid": False, "error": "Clip missing"}
        needs_work = force_rerender or (not probe.get("valid")) or scene.get("status") == "failed"
        if needs_work:
            scenes_needing_repair.append(scene["scene_index"])
            if direction or scene.get("error_message"):
                try:
                    enhance_scene_prompt(
                        project_id,
                        scene["id"],
                        direction=direction or f"Fix prior render issue cleanly and keep prompt crisp ({scene.get('error_message', '')[:120]})",
                    )
                except Exception:
                    pass
            # Bump seed if fixed seed failed so retry explores fresh trajectory.
            if int(scene.get("seed") or -1) >= 0:
                update_scene(project_id, scene["id"], {"seed": int(scene["seed"]) + 1})
        else:
            update_scene(
                project_id,
                scene["id"],
                {
                    "status": "approved",
                    "quality_score": 1.0,
                    "quality_notes": f"Verified {probe.get('width', 0)}x{probe.get('height', 0)}, {probe.get('duration_seconds', 0)}s",
                },
            )
            verified += 1

    repaired = 0
    still_failed = 0
    if scenes_needing_repair:
        append_stage_log(project_id, "refine", f"Refining {len(scenes_needing_repair)} scene(s): {scenes_needing_repair}.")
        for attempt in range(max(1, int(max_repair_attempts))):
            if stop_check and stop_check():
                update_project(project_id, {"status": "paused", "progress_message": "Paused during refinement."})
                return {"status": "paused", "verified": verified, "repaired": repaired, "failed": len(scenes_needing_repair)}
            render_res = run_render_stage(
                project_id,
                scene_indices=scenes_needing_repair,
                force_rerender=True,
                stop_check=stop_check,
            )
            if render_res.get("status") == "paused":
                return {"status": "paused", "verified": verified, "repaired": repaired, "failed": len(scenes_needing_repair)}
            refreshed = get_project(project_id, include_scenes=True)
            remaining = []
            for s in (refreshed or {}).get("scenes") or []:
                if s["scene_index"] in scenes_needing_repair:
                    if s.get("has_clip") and s.get("status") in {"rendered", "refined", "approved"}:
                        update_scene(project_id, s["id"], {"refined_clip_path": s["active_clip_path"], "status": "refined"})
                        repaired += 1
                    else:
                        remaining.append(s["scene_index"])
            scenes_needing_repair = remaining
            if not scenes_needing_repair:
                break
        still_failed = len(scenes_needing_repair)

    update_project(
        project_id,
        {
            "progress_percent": 88.0 if still_failed == 0 else 80.0,
            "progress_message": f"Refinement complete: {verified} verified, {repaired} refined, {still_failed} remaining issues.",
        },
    )
    append_stage_log(project_id, "refine", f"Verified={verified}, refined={repaired}, remaining_failed={still_failed}.")
    return {
        "status": "success" if still_failed == 0 else "partial",
        "verified": verified,
        "repaired": repaired,
        "failed": still_failed,
    }


# ── Stage 5: Final FFmpeg Assembly & Content Library Publishing ─────────────


def run_assemble_stage(
    project_id: int,
    *,
    audio_path: Optional[str] = None,
    stop_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Stitch all rendered/refined scene clips into a master MP4 and publish to Gallery & Content Library."""
    project = get_project(project_id, include_scenes=True)
    if not project:
        raise ValueError(f"Project #{project_id} not found.")
    scenes = project.get("scenes") or []
    clip_paths: List[Path] = []
    transitions: List[str] = []
    for scene in scenes:
        active_clip = scene.get("refined_clip_path") or scene.get("rendered_clip_path") or ""
        if active_clip and Path(active_clip).is_file():
            clip_paths.append(Path(active_clip))
            transitions.append(str(scene.get("transition") or "cut"))

    if not clip_paths:
        err = "No rendered scene clips are available to assemble yet."
        update_project(project_id, {"status": "failed", "error_message": err})
        return {"status": "error", "message": err}

    if stop_check and stop_check():
        update_project(project_id, {"status": "paused", "progress_message": "Paused before final assembly."})
        return {"status": "paused", "message": "Paused before final assembly."}

    update_project(
        project_id,
        {
            "status": "assembling",
            "current_stage": "assemble",
            "progress_percent": 92.0,
            "progress_message": f"Assembling {len(clip_paths)} scenes into final master video...",
            "owner_pid": os.getpid(),
            "owner_started_at": mgt._process_start_time(os.getpid()),
        },
    )
    append_stage_log(project_id, "assemble", f"Assembling {len(clip_paths)} scene clips with FFmpeg.")

    settings = project.get("settings") or {}
    embedded_audio = ((project.get("embedded_context") or {}).get("archived_media") or {}).get("audio") or []
    effective_audio = Path(audio_path) if (audio_path and Path(audio_path).is_file()) else (
        Path(embedded_audio[0]) if (embedded_audio and Path(embedded_audio[0]).is_file()) else None
    )

    safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(project["title"] or "factory_video")).strip("_")[:48] or "factory_video"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_file_name = f"autoyou_factory_{project_id}_{stamp}_{safe_slug}.mp4"
    staged_output = _project_workspace_dir(project_id) / final_file_name
    app_dir = Path(mgt.get_wan2gp_config().get("app_dir") or "")

    assembled = assemble_video_clips(
        clip_paths,
        staged_output,
        transitions=transitions,
        transition_seconds=float(settings.get("transition_seconds") or 0.35),
        audio_path=effective_audio,
        resolution=project.get("resolution") or None,
        fps=int(project.get("fps") or 24),
        app_dir=app_dir,
    )

    permanent_dir = _output_dir() / "videos"
    permanent_dir.mkdir(parents=True, exist_ok=True)
    final_path = permanent_dir / final_file_name
    from shared import content_files

    content_files.copy(staged_output, final_path)

    total_frames = sum(int(s.get("frames") or 0) for s in scenes)
    history_item_id = mgt.save_history_item(
        media_type="video",
        original_prompt=f"[Media Factory #{project_id}] {project['title']}: {project['original_request'][:300]}",
        optimized_prompt=project.get("script_text")[:1200] or project["original_request"],
        file_path=str(final_path),
        file_name=final_file_name,
        settings={
            "model_type": project.get("video_model") or "media_factory",
            "resolution": project.get("resolution"),
            "num_inference_steps": settings.get("video_steps"),
            "video_length": total_frames,
            "seed": -1,
        },
        status="completed",
    )

    probe = assembled.get("probe") or {}
    msg = (
        f"Assembled {len(clip_paths)} scene(s) into {final_file_name} "
        f"({probe.get('duration_seconds', 0)}s, {probe.get('width', 0)}x{probe.get('height', 0)})."
    )
    update_project(
        project_id,
        {
            "status": "completed",
            "current_stage": "assemble",
            "final_output_path": str(final_path),
            "final_file_name": final_file_name,
            "final_history_item_id": history_item_id,
            "progress_percent": 100.0,
            "progress_message": msg,
            "error_message": "",
        },
    )
    append_stage_log(project_id, "assemble", msg)
    return {
        "status": "success",
        "project_id": project_id,
        "item_id": history_item_id,
        "media_type": "video",
        "file_path": str(final_path),
        "file_name": final_file_name,
        "scenes_assembled": len(clip_paths),
        "probe": probe,
        "message": msg,
    }


init_factory_db()
