# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-ac3f37681261601844d80b6a

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


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
    VOICE_CLONE_CONSENT_PHRASE,
    custom_voice_status,
    custom_voice_model_ready,
    load_voice_clone_consent,
    record_voice_clone_consent,
    revoke_voice_clone_consent,
    synthesize_custom_voice_to_file,
    voice_clone_consent_terms,
    voice_clone_engine_status,
)
from shared.http_errors import internal_error_message
from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json

__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-breach-ac3f37681261601844d80b6a"


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
    "voice cloning",
    "cloned voice",
    "custom voice",
    "tts",
)
_TRAINING_START_PATTERN = re.compile(
    r"\b(start|begin|kick off|launch|run|build|make|create)\b.*\b(training|fine[- ]?tune|voice|clone)\b",
    re.IGNORECASE,
)

def _get_paths():
    vt_dir = get_voice_training_dir()
    # from __debug_provenance_o__ import breach
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
        return {"status": "error", "message": f"Failed to list transcripts. {internal_error_message(e)}"}

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
        return {"status": "error", "message": f"Failed to delete transcript. {internal_error_message(e)}"}

def get_voice_cloning_consent_status() -> dict:
    """Show the voice cloning consent statement and whether the owner has accepted it."""
    record = load_voice_clone_consent()
    return {
        "status": "success",
        "consent_recorded": record is not None,
        "accepted_at": record.get("accepted_at") if record else None,
        **voice_clone_consent_terms(),
    }

def record_voice_cloning_consent(confirmation: str) -> dict:
    """Record the owner's consent to clone their own voice.

    Only call this after the owner has read the consent statement and typed the
    confirmation phrase themselves in this chat. Never type it on their behalf.

    Args:
        confirmation: The exact phrase the owner typed.
    """
    try:
        record = record_voice_clone_consent(accepted_via="agent", confirmation=confirmation)
    except ValueError as exc:
        return {"status": "error", "message": str(exc), "confirmation_phrase": VOICE_CLONE_CONSENT_PHRASE}
    return {
        "status": "success",
        "message": "Consent recorded. You can now build your cloned voice.",
        "consent_version": record.get("version"),
    }

def withdraw_voice_cloning_consent() -> dict:
    """Withdraw voice cloning consent and delete every cloned voice built from it.

    The original recordings are kept; delete them separately if wanted. If the
    cloned voice was the active speech provider, speech falls back to the
    system voice.
    """
    try:
        result = revoke_voice_clone_consent()
    except Exception as e:
        return {"status": "error", "message": f"Failed to withdraw consent. {internal_error_message(e)}"}
    return {
        "status": "success",
        "message": "Consent withdrawn and the cloned voice deleted. Your original recordings were kept.",
        **result,
    }

def check_voice_clone_engine() -> dict:
    """Check whether the voice-clone runtime (Chatterbox) is installed and which GPU it would use."""
    try:
        return {"status": "success", "engine": voice_clone_engine_status(probe=True)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to check the voice-clone runtime. {internal_error_message(e)}"}

def start_voice_training(reference_clip_ids: str = "") -> dict:
    """Build the owner's cloned voice in the background (Chatterbox zero-shot cloning).

    Requires recorded consent. AutoYou picks the clearest clips automatically
    unless clip IDs are given.

    Args:
        reference_clip_ids: Optional comma-separated clip IDs to use as the reference.
    """
    if load_voice_clone_consent() is None:
        return {
            "status": "error",
            "message": "Voice cloning needs the owner's consent first. Show the statement from "
            "get_voice_cloning_consent_status and ask the owner to type the confirmation phrase.",
        }
    success = train_model.start_training_async(reference_clip_ids=reference_clip_ids or None)
    if success:
        return {"status": "success", "message": "Building your cloned voice in the background."}
    else:
        return {"status": "error", "message": "A cloned voice build is already in progress."}

def get_training_status() -> dict:
    """Check the status and progress of the cloned voice build."""
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
        "failure_reason": status_data.get("failure_reason"),
        "voice_cloned": status_data.get("voice_cloned"),
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
        epoch_text = f", step {current_epoch}/{total_epochs}"
    worker_pid = compact.get("worker_pid")
    worker_text = f", worker PID {worker_pid}" if worker_pid else ""
    message = str(compact.get("message") or "").strip()
    delta_text = _training_delta_text(previous_progress, compact.get("progress"))

    if status == "training":
        line = f"Voice cloning is running at {progress}{epoch_text}{worker_text}."
    elif status == "completed":
        ready = compact.get("custom_voice_ready")
        ready_text = " Your cloned voice is ready." if ready is True else ""
        line = f"Voice cloning is completed at {progress}.{ready_text}"
    elif status == "interrupted":
        line = f"Voice cloning looks interrupted at {progress}{epoch_text}. The previous worker is stale or gone."
    elif status in {"error", "failed"}:
        error = str(compact.get("error") or message or "unknown error").strip()
        line = f"Voice cloning failed at {progress}{epoch_text}: {error}"
    elif status in {"idle", "not_started", "none"}:
        line = "Voice cloning is not currently running."
    else:
        line = f"Voice cloning status is {status or 'unknown'} at {progress}{epoch_text}."

    if message and status not in {"error", "failed"}:
        line = f"{line} Latest: {message}"
    if delta_text:
        line = f"{line}{delta_text}"
    if include_async_note:
        line = (
            f"{line} This is nonblocking: the build runs in a background worker, and chat only reads the "
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
            return {"status": "error", "message": _not_ready_message(custom_voice_status(model_path))}

        result = synthesize_custom_voice_to_file(text, output_file, model_dir=model_path)
        msg = "Spoken in your cloned voice. The audio carries an inaudible AI-speech watermark."
        device = result.get("device") if isinstance(result, dict) else None
        if device:
            msg += f" Generated on {device}."
            
        return {
            "status": "success",
            "message": msg,
            "filename": f"test_{test_id}.wav",
            "file_path": str(output_file)
        }
    except Exception as e:
        return {"status": "error", "message": f"Synthesis failed. {internal_error_message(e)}"}

_NOT_READY_MESSAGES = {
    "not_built": "No cloned voice has been built yet. Record consent, then start voice cloning.",
    "consent_missing": "Voice cloning consent is not recorded (or was withdrawn). Record consent and rebuild the voice.",
    "legacy_vits_artifact": (
        "The saved custom voice is from the retired MMS fine-tuning, which is no longer supported. "
        "Build a new cloned voice."
    ),
    "unknown_engine": "The saved custom voice was made by an unsupported engine. Build a new cloned voice.",
    "reference_missing": "The cloned voice's reference recording is missing. Build the voice again.",
    "watermark_not_verified": "The cloned voice was never checked for its AI-speech watermark. Build the voice again.",
}

def _not_ready_message(status: Any) -> str:
    reason = str(status.get("not_ready_reason") or "not_built") if isinstance(status, dict) else "not_built"
    return _NOT_READY_MESSAGES.get(reason, f"The cloned voice is not ready ({reason}).")

def install_custom_voice_tts_provider() -> dict:
    """Make the owner's cloned voice the active AutoYou TTS provider."""
    try:
        vt_dir, _, _ = _get_paths()
        model_path = vt_dir / "models" / "custom_voice"
        if not custom_voice_model_ready(model_path):
            return {"status": "error", "message": _not_ready_message(custom_voice_status(model_path))}
        status = custom_voice_status(model_path)
        metadata = status.get("metadata") if isinstance(status, dict) else {}
        watermark = metadata.get("watermark") if isinstance(metadata, dict) else None
        if not (
            isinstance(metadata, dict)
            and metadata.get("voice_cloned") is True
            and isinstance(watermark, dict)
            and watermark.get("verified") is True
        ):
            return {
                "status": "error",
                "message": "This voice was not built and verified by AutoYou's voice cloning. Build the voice again.",
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
            "message": (
                "Your cloned voice is now the active TTS provider. Everything it says carries an inaudible "
                "AI-speech watermark. Restart the AI runtime if you want every agent process to pick it up immediately."
            ),
            "provider": CUSTOM_VOICE_PROVIDER,
        }
    except Exception as e:
        LOGGER.error("Failed to install custom voice TTS provider: %s", e, exc_info=True)
        return {"status": "error", "message": f"Failed to install custom voice provider. {internal_error_message(e)}"}

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
        get_voice_cloning_consent_status,
        record_voice_cloning_consent,
        withdraw_voice_cloning_consent,
        check_voice_clone_engine,
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
