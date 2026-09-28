# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-f6a66cdc74d205b7d389d98b

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-f6a66cdc74d205b7d389d98b"


import os
import logging
import re
from typing import Optional, List, Dict, Any

from google.adk.agents import Agent
from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from . import train_model
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime, inject_realtime_datetime_into_request
from shared.session_execution import create_text_llm_response
from shared.voice_training_storage import (
    copy_voice_training_data,
    get_voice_training_dir,
    get_voice_training_storage_info,
    reset_voice_training_dir,
    set_voice_training_dir,
)
from shared.custom_voice_tts import (
    CUSTOM_VOICE_PROVIDER,
    custom_voice_status,
    custom_voice_model_ready,
    synthesize_custom_voice_to_file,
)
from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json

LOGGER = logging.getLogger(__name__)
_LAST_TRAINING_PROGRESS_STATE_KEY = "voice_training_agent:last_training_progress"
_LAST_TRAINING_STATUS_STATE_KEY = "voice_training_agent:last_training_status"

_TRAINING_STATUS_TERMS = (
    "status",
    "state",
    "progress",
    "update",
    "updates",
    "current",
    "now",
    "going",
    "running",
    "done",
    "complete",
    "completed",
    "finished",
    "error",
    "failed",
    "stuck",
)
_TRAINING_CONTEXT_TERMS = (
    "voice training",
    "training",
    "fine tune",
    "fine-tune",
    "finetune",
    "voice clone",
    "custom voice",
    "tts",
)
_TRAINING_START_PATTERN = re.compile(r"\b(start|begin|kick off|launch|run)\b.*\b(training|fine[- ]?tune|voice)\b", re.IGNORECASE)

def _get_paths():
    vt_dir = get_voice_training_dir()
    transcripts_file = vt_dir / "transcripts.json"
    recordings_dir = vt_dir / "recordings"
    return vt_dir, transcripts_file, recordings_dir

def list_voice_transcripts(limit: int = 20) -> dict:
    """List the captured voice recordings and call transcripts used for custom voice preparation.
    
    Args:
        limit: Maximum number of transcripts to return (default: 20).
    """
    _, transcripts_file, _ = _get_paths()
    if not transcripts_file.exists():
        return {"status": "success", "transcripts": [], "count": 0}
    try:
        data = load_secure_json(transcripts_file, default=[])
        # Sort by timestamp descending
        data.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return {"status": "success", "transcripts": data[:limit], "count": len(data)}
    except SecureStorageError:
        raise
    except Exception as e:
        return {"status": "error", "message": f"Failed to list transcripts: {e}"}

def get_voice_transcript(transcript_id: str) -> dict:
    """Retrieve details of a specific voice recording transcript.
    
    Args:
        transcript_id: The ID of the transcript to retrieve.
    """
    _, transcripts_file, _ = _get_paths()
    if not transcripts_file.exists():
        return {"status": "error", "message": "No transcripts found"}
    try:
        data = load_secure_json(transcripts_file, default=[])
        for item in data:
            if item.get("id") == transcript_id:
                return {"status": "success", "transcript": item}
        return {"status": "error", "message": f"Transcript {transcript_id} not found"}
    except SecureStorageError:
        raise
    except Exception as e:
        return {"status": "error", "message": f"Failed to get transcript: {e}"}

def delete_voice_transcript(transcript_id: str) -> dict:
    """Delete a voice recording transcript and its corresponding WAV audio file.
    
    Args:
        transcript_id: The ID of the transcript to delete.
    """
    _, transcripts_file, recordings_dir = _get_paths()
    if not transcripts_file.exists():
        return {"status": "error", "message": "No transcripts found"}
    try:
        data = load_secure_json(transcripts_file, default=[])
        
        updated = []
        deleted_item = None
        for item in data:
            if item.get("id") == transcript_id:
                deleted_item = item
            else:
                updated.append(item)
                
        if not deleted_item:
            return {"status": "error", "message": f"Transcript {transcript_id} not found"}
            
        # Delete wav file
        wav_filename = deleted_item.get("filename")
        if wav_filename:
            wav_path = recordings_dir / wav_filename
            if wav_path.exists():
                try:
                    os.remove(wav_path)
                except Exception as e:
                    LOGGER.warning("Failed to delete audio file %s: %s", wav_path, e)
                    
        save_secure_json(transcripts_file, updated)
            
        return {"status": "success", "message": f"Deleted transcript {transcript_id}"}
    except SecureStorageError:
        raise
    except Exception as e:
        return {"status": "error", "message": f"Failed to delete transcript: {e}"}

def start_voice_training(epochs: int = train_model.DEFAULT_TRAINING_EPOCHS) -> dict:
    """Start local custom voice fine-tuning in the background.
    
    Args:
        epochs: Number of fine-tuning epochs to run.
    """
    success = train_model.start_training_async(epochs=epochs)
    if success:
        return {"status": "success", "message": "Custom voice fine-tuning started in the background."}
    else:
        return {"status": "error", "message": "Custom voice fine-tuning is already in progress."}

def get_training_status() -> dict:
    """Check the status and progress of local custom voice fine-tuning."""
    try:
        status_data = train_model.get_status()
        return {"status": "success", "training_status": status_data}
    except Exception as e:
        return {"status": "error", "message": f"Failed to get training status: {e}"}

def get_voice_training_storage() -> dict:
    """Return the active voice training storage directory and available disk space."""
    try:
        return {"status": "success", "storage": get_voice_training_storage_info()}
    except Exception as e:
        return {"status": "error", "message": f"Failed to read voice training storage: {e}"}

def set_voice_training_storage_path(path: str, migrate_existing: bool = True) -> dict:
    """Set a custom voice training storage directory.

    Args:
        path: Absolute or relative folder path where voice recordings, status, and model artifacts should live.
        migrate_existing: Copy the current voice training files into the new folder before switching.
    """
    try:
        current_status = train_model.get_status()
        if current_status.get("status") == "training":
            return {
                "status": "error",
                "message": "Voice training is currently running. Stop or let it finish before changing storage.",
            }
        source_dir = get_voice_training_dir()
        migration = None
        if migrate_existing:
            migration = copy_voice_training_data(source_dir, path)
        storage = set_voice_training_dir(path)
        return {
            "status": "success",
            "message": "Voice training storage path updated. Restart the AI runtime and managed voice training backend to apply it everywhere.",
            "storage": storage,
            "migration": migration,
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to set voice training storage path: {e}"}

def reset_voice_training_storage_path() -> dict:
    """Reset voice training storage back to the default AutoYou location."""
    try:
        current_status = train_model.get_status()
        if current_status.get("status") == "training":
            return {
                "status": "error",
                "message": "Voice training is currently running. Stop or let it finish before changing storage.",
            }
        storage = reset_voice_training_dir()
        return {
            "status": "success",
            "message": "Voice training storage path reset to the default location. Restart runtimes to apply it everywhere.",
            "storage": storage,
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to reset voice training storage path: {e}"}

def _resolve_state_container(tool_context_or_state: Any) -> Any:
    state = getattr(tool_context_or_state, "state", None)
    return state if state is not None else tool_context_or_state

def _state_get(tool_context_or_state: Any, key: str) -> Any:
    if tool_context_or_state is None or not key:
        return None
    state = _resolve_state_container(tool_context_or_state)
    if isinstance(state, dict):
        return state.get(key)
    try:
        return getattr(state, key)
    except Exception:
        return None

def _state_set(tool_context_or_state: Any, key: str, value: Any) -> None:
    if tool_context_or_state is None or not key:
        return
    state = _resolve_state_container(tool_context_or_state)
    if isinstance(state, dict):
        state[key] = value
        return
    try:
        setattr(state, key, value)
    except Exception:
        pass

def _as_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed != parsed:
        return None
    return parsed

def _format_progress(value: Any) -> str:
    parsed = _as_float(value)
    if parsed is None:
        return "unknown progress"
    bounded = max(0.0, min(100.0, parsed))
    return f"{bounded:.1f}%"

def _compact_training_status(status_data: Dict[str, Any]) -> Dict[str, Any]:
    custom_voice = status_data.get("custom_voice") if isinstance(status_data, dict) else None
    compact: Dict[str, Any] = {
        "status": status_data.get("status"),
        "progress": status_data.get("progress"),
        "current_epoch": status_data.get("current_epoch"),
        "total_epochs": status_data.get("total_epochs"),
        "message": status_data.get("message"),
        "async_backend": status_data.get("async_backend"),
        "worker_pid": status_data.get("worker_pid"),
        "training_stale": bool(status_data.get("training_stale") or False),
        "error": status_data.get("error"),
        "fine_tuning_applied": status_data.get("fine_tuning_applied"),
        "custom_voice_ready": custom_voice.get("ready") if isinstance(custom_voice, dict) else None,
    }
    return {key: value for key, value in compact.items() if value not in (None, "")}

def _training_delta_text(previous_progress: Any, current_progress: Any) -> str:
    previous = _as_float(previous_progress)
    current = _as_float(current_progress)
    if previous is None or current is None:
        return ""
    delta = current - previous
    if abs(delta) < 0.05:
        return " No progress change since the last chat update."
    sign = "+" if delta > 0 else ""
    return f" Progress changed {sign}{delta:.1f} points since the last chat update."

def _render_training_chat_message(
    status_data: Dict[str, Any],
    *,
    previous_progress: Any = None,
    include_async_note: bool = False,
) -> str:
    compact = _compact_training_status(status_data)
    status = str(compact.get("status") or "unknown").strip().lower()
    progress = _format_progress(compact.get("progress"))
    current_epoch = compact.get("current_epoch")
    total_epochs = compact.get("total_epochs")
    epoch_text = ""
    if current_epoch is not None and total_epochs is not None:
        epoch_text = f", epoch {current_epoch}/{total_epochs}"
    worker_pid = compact.get("worker_pid")
    worker_text = f", worker PID {worker_pid}" if worker_pid else ""
    message = str(compact.get("message") or "").strip()
    delta_text = _training_delta_text(previous_progress, compact.get("progress"))

    if status == "training":
        line = f"Voice training is running at {progress}{epoch_text}{worker_text}."
    elif status == "completed":
        ready = compact.get("custom_voice_ready")
        ready_text = " Custom voice artifacts are ready." if ready is True else ""
        line = f"Voice training is completed at {progress}.{ready_text}"
    elif status == "interrupted":
        line = f"Voice training looks interrupted at {progress}{epoch_text}. The previous worker is stale or gone."
    elif status in {"error", "failed"}:
        error = str(compact.get("error") or message or "unknown error").strip()
        line = f"Voice training failed at {progress}{epoch_text}: {error}"
    elif status in {"idle", "not_started", "none"}:
        line = "Voice training is not currently running."
    else:
        line = f"Voice training status is {status or 'unknown'} at {progress}{epoch_text}."

    if message and status not in {"error", "failed"}:
        line = f"{line} Latest: {message}"
    if delta_text:
        line = f"{line}{delta_text}"
    if include_async_note:
        line = (
            f"{line} This is nonblocking: training runs in a background worker, and chat only reads the "
            'latest status snapshot. Ask "training update" any time for another chat message.'
        )
    return line

def _record_training_snapshot(tool_context_or_state: Any, status_data: Dict[str, Any]) -> None:
    _state_set(tool_context_or_state, _LAST_TRAINING_PROGRESS_STATE_KEY, status_data.get("progress"))
    _state_set(tool_context_or_state, _LAST_TRAINING_STATUS_STATE_KEY, status_data.get("status"))

def get_training_chat_update(tool_context: Optional[Any] = None) -> dict:
    """Return a compact, nonblocking voice training update optimized for chat turns.

    Args:
        tool_context: Injected by ADK. Used to remember the previous progress for this chat.
    """
    try:
        status_data = train_model.get_status()
        previous_progress = _state_get(tool_context, _LAST_TRAINING_PROGRESS_STATE_KEY)
        _record_training_snapshot(tool_context, status_data)
        return {
            "status": "success",
            "nonblocking": True,
            "message": _render_training_chat_message(status_data, previous_progress=previous_progress),
            "training_status": _compact_training_status(status_data),
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to get training chat update: {e}"}

def test_synthesize_voice(text: str) -> dict:
    """Test synthesis of text using the current custom voice provider.
    
    Args:
        text: The phrase to speak.
    """
    try:
        import uuid
        
        vt_dir, _, _ = _get_paths()
        test_dir = vt_dir / "test_syntheses"
        test_dir.mkdir(parents=True, exist_ok=True)
        
        test_id = str(uuid.uuid4())[:8]
        output_file = test_dir / f"test_{test_id}.wav"
        
        model_path = vt_dir / "models" / "custom_voice"
        
        if not custom_voice_model_ready(model_path):
            return {"status": "error", "message": "Custom voice model is not ready. Run voice fine-tuning first."}

        synthesize_custom_voice_to_file(text, output_file, model_dir=model_path)
        status = custom_voice_status(model_path)
        metadata = status.get("metadata") if isinstance(status, dict) else {}
        if isinstance(metadata, dict) and metadata.get("fine_tuning_applied") is False:
            msg = "Base VITS provider synthesis succeeded, but this artifact is not a cloned voice."
        else:
            msg = "Custom cloned voice synthesis succeeded."
            
        return {
            "status": "success",
            "message": msg,
            "filename": f"test_{test_id}.wav",
            "file_path": str(output_file)
        }
    except Exception as e:
        return {"status": "error", "message": f"Synthesis failed: {e}"}

def install_custom_voice_tts_provider() -> dict:
    """Install the prepared custom voice as the active AutoYou TTS provider."""
    try:
        vt_dir, _, _ = _get_paths()
        model_path = vt_dir / "models" / "custom_voice"
        if not custom_voice_model_ready(model_path):
            return {
                "status": "error",
                "message": "Custom voice model is not ready. Run voice fine-tuning first.",
            }
        status = custom_voice_status(model_path)
        metadata = status.get("metadata") if isinstance(status, dict) else {}
        allow_untrained = os.environ.get("AUTOYOU_ALLOW_UNTRAINED_CUSTOM_VOICE_INSTALL", "").strip().lower()
        if isinstance(metadata, dict) and metadata.get("fine_tuning_applied") is not True and allow_untrained not in {"1", "true", "yes"}:
            return {
                "status": "error",
                "message": (
                    "Custom voice artifacts exist, but they are only a base provider and not a fine-tuned clone. "
                    "Run voice fine-tuning before installing, or set AUTOYOU_ALLOW_UNTRAINED_CUSTOM_VOICE_INSTALL=1 "
                    "for compatibility testing."
                ),
            }
        dataset_quality = metadata.get("dataset_quality") if isinstance(metadata, dict) else None
        allow_unrecommended = os.environ.get("AUTOYOU_ALLOW_UNRECOMMENDED_CUSTOM_VOICE_INSTALL", "").strip().lower()
        quality_recommended = isinstance(dataset_quality, dict) and dataset_quality.get("install_recommended") is True
        if (
            isinstance(metadata, dict)
            and metadata.get("fine_tuning_applied") is True
            and not quality_recommended
            and allow_unrecommended not in {"1", "true", "yes"}
        ):
            reasons = (
                dataset_quality.get("install_blocking_reasons")
                if isinstance(dataset_quality, dict)
                else ["missing_dataset_quality_metadata"]
            ) or ["dataset_quality_gate_failed"]
            return {
                "status": "error",
                "message": (
                    "Custom voice artifacts exist, but the training dataset did not pass quality gates "
                    f"({', '.join(str(reason) for reason in reasons)}). Upload clearer curated samples with exact "
                    "transcripts and retrain, or set AUTOYOU_ALLOW_UNRECOMMENDED_CUSTOM_VOICE_INSTALL=1 for testing."
                ),
            }

        from shared.speech_config import normalize_speech_config
        try:
            from autoyou_agents.shared_tools.scheduler_mission_control import _runtime_server

            runtime_server = _runtime_server()
        except Exception:
            import server as runtime_server

        block_reason = (
            runtime_server._config_write_block_reason()
            if hasattr(runtime_server, "_config_write_block_reason")
            else ""
        )
        if block_reason:
            return {"status": "error", "message": block_reason}

        cfg = runtime_server.STATE.config or runtime_server._default_config()
        speech_cfg = normalize_speech_config(cfg.get("speech"))
        speech_cfg["tts"]["provider"] = CUSTOM_VOICE_PROVIDER
        cfg["speech"] = normalize_speech_config(speech_cfg)
        if hasattr(runtime_server, "_save_and_reload_state_config"):
            runtime_server.STATE.config = runtime_server._save_and_reload_state_config(cfg)
        else:
            runtime_server._persist_state_config(cfg)
            runtime_server.STATE.config = cfg
        if hasattr(runtime_server, "_apply_speech_config_to_active_audio_managers"):
            runtime_server._apply_speech_config_to_active_audio_managers()
        return {
            "status": "success",
            "message": "Custom cloned voice installed as the active TTS provider. Restart the AI runtime if you want every agent process to pick it up immediately.",
            "provider": CUSTOM_VOICE_PROVIDER,
        }
    except Exception as e:
        LOGGER.error("Failed to install custom voice TTS provider: %s", e, exc_info=True)
        return {"status": "error", "message": f"Failed to install custom voice provider: {e}"}

def _extract_text_from_llm_request(llm_request: Any) -> str:
    for content in reversed(getattr(llm_request, "contents", []) or []):
        if str(getattr(content, "role", "") or "").strip().lower() not in {"", "user"}:
            continue
        parts: List[str] = []
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return " ".join(parts).strip()
    return ""

def _is_training_progress_request(user_text: str) -> bool:
    normalized = " ".join(str(user_text or "").lower().split())
    if not normalized:
        return False
    if _TRAINING_START_PATTERN.search(normalized) and not any(term in normalized for term in _TRAINING_STATUS_TERMS):
        return False
    if "async" in normalized and any(term in normalized for term in _TRAINING_CONTEXT_TERMS):
        return True
    if not any(term in normalized for term in _TRAINING_CONTEXT_TERMS):
        return False
    return any(term in normalized for term in _TRAINING_STATUS_TERMS)

async def _voice_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)
    user_text = _extract_text_from_llm_request(llm_request)
    if not _is_training_progress_request(user_text):
        return None

    try:
        status_data = train_model.get_status()
        previous_progress = _state_get(callback_context, _LAST_TRAINING_PROGRESS_STATE_KEY)
        _record_training_snapshot(callback_context, status_data)
        return create_text_llm_response(
            _render_training_chat_message(
                status_data,
                previous_progress=previous_progress,
                include_async_note=any(term in user_text.lower() for term in ("async", "live", "chat")),
            ),
            custom_metadata={"response_author": AGENT_NAME, "voice_training_query_kind": "status"},
        )
    except Exception as e:
        return create_text_llm_response(
            f"Failed to read voice training status: {e}",
            custom_metadata={"response_author": AGENT_NAME, "voice_training_query_kind": "status_error"},
        )
    return None

def create_voice_training_agent(model_config: Any) -> Agent:
    """Create a voice training agent with the provided model configuration."""
    tools = [
        list_voice_transcripts,
        get_voice_transcript,
        delete_voice_transcript,
        start_voice_training,
        get_training_status,
        get_training_chat_update,
        get_voice_training_storage,
        set_voice_training_storage_path,
        reset_voice_training_storage_path,
        test_synthesize_voice,
        install_custom_voice_tts_provider,
        get_current_datetime,
    ]
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=[_voice_before_model_callback],
        tools=tools
    )
