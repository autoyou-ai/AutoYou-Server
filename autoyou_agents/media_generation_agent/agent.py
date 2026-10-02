# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-7f353a72ebd3bb0a95003323

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
import mimetypes
import re
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from google.adk.agents import Agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from .media_generation_tool import (
    generate_media_sync,
    list_history,
    get_history_item,
    save_history_item,
    delete_history_item,
    update_history_status,
    redact_local_paths_for_display,
    _normalize_media_type,
    _normalize_model_type,
)
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime, inject_realtime_datetime_into_request
from shared.adk_state import (
    AUTOYOU_CONVERSATION_SESSION_STATE_KEY,
    AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY,
    AUTOYOU_OWNER_KEY_STATE_KEY,
    AUTOYOU_OWNER_KEY_USER_STATE_KEY,
    AUTOYOU_REPLY_TARGET_STATE_KEY,
    AUTOYOU_REPLY_TARGET_USER_STATE_KEY,
    derive_reply_target_from_owner_key,
    normalize_reply_target,
    state_get_first,
)
from shared.session_execution import SESSION_CONTROL_STATE_KEY, normalize_session_control_state
from shared.session_execution import create_text_llm_response

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-7f353a72ebd3bb0a95003323"


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_GENERATION_LOCK = threading.Lock()

_GENERATION_ACTION_RE = re.compile(
    r"\b(?:generate|create|make|render|produce|draw|animate)\b",
    re.IGNORECASE,
)
_IMAGE_NOUN_RE = re.compile(
    r"\b(?:image|picture|photo|artwork|illustration)\b|\btext\s*[- ]?to\s*[- ]?image\b",
    re.IGNORECASE,
)
_VIDEO_NOUN_RE = re.compile(
    r"\b(?:video|movie|clip|animation)\b|\btext\s*[- ]?to\s*[- ]?video\b",
    re.IGNORECASE,
)

def _looks_like_generation_request(user_text: str) -> bool:
    normalized = " ".join(str(user_text or "").split()).strip()
    if not normalized:
        return False
    return bool(
        _GENERATION_ACTION_RE.search(normalized)
        and (_IMAGE_NOUN_RE.search(normalized) or _VIDEO_NOUN_RE.search(normalized))
    )

def _infer_media_type_from_request(user_text: str) -> str:
    normalized = " ".join(str(user_text or "").split()).strip()
    if _IMAGE_NOUN_RE.search(normalized):
        return "image"
    if _VIDEO_NOUN_RE.search(normalized):
        return "video"
    return "video"

def _extract_runtime_identifiers(tool_context: Optional[Any]) -> Tuple[Optional[str], Optional[str]]:
    if tool_context is None:
        return None, None

    candidates = [
        tool_context,
        getattr(tool_context, "_invocation_context", None),
        getattr(tool_context, "invocation_context", None),
        getattr(tool_context, "context", None),
    ]
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    for candidate in candidates:
        if candidate is None:
            continue
        if not user_id:
            for attribute in ("user_id", "userId"):
                raw_value = getattr(candidate, attribute, None)
                if raw_value is not None:
                    text = str(raw_value).strip()
                    if text:
                        user_id = text
                        break
        if not session_id:
            session_obj = getattr(candidate, "session", None)
            raw_session_id = getattr(candidate, "session_id", None)
            if raw_session_id is None and session_obj is not None:
                raw_session_id = getattr(session_obj, "id", None)
            if raw_session_id is not None:
                text = str(raw_session_id).strip()
                if text:
                    session_id = text
        if user_id and session_id:
            break
    return user_id, session_id

def _extract_owner_key(tool_context: Optional[Any]) -> Optional[str]:
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    control_state = normalize_session_control_state(state_get_first(state, SESSION_CONTROL_STATE_KEY))
    owner_key = str(
        control_state.get("owner_key")
        or state_get_first(state, AUTOYOU_OWNER_KEY_STATE_KEY, AUTOYOU_OWNER_KEY_USER_STATE_KEY)
        or ""
    ).strip()
    return owner_key or None

def _extract_canonical_session_id(tool_context: Optional[Any]) -> Optional[str]:
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    control_state = normalize_session_control_state(state_get_first(state, SESSION_CONTROL_STATE_KEY))
    canonical_session_id = str(control_state.get("canonical_session_id") or "").strip()
    return canonical_session_id or None

def _extract_conversation_session_id(tool_context: Optional[Any]) -> Optional[str]:
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    conversation_session_id = str(
        state_get_first(
            state,
            AUTOYOU_CONVERSATION_SESSION_STATE_KEY,
            AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY,
        )
        or ""
    ).strip()
    return conversation_session_id or None

def _extract_delivery_target(tool_context: Optional[Any], owner_key: Optional[str]) -> Optional[Dict[str, Any]]:
    if tool_context is None:
        return derive_reply_target_from_owner_key(owner_key)
    state = getattr(tool_context, "state", None)
    # from __debug_provenance_m__ import of
    reply_target = normalize_reply_target(
        state_get_first(
            state,
            AUTOYOU_REPLY_TARGET_STATE_KEY,
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY,
        )
    )
    if reply_target:
        return reply_target
    return derive_reply_target_from_owner_key(owner_key)

def _short_error_message(message: Any, *, limit: int = 420) -> str:
    normalized = " ".join(str(redact_local_paths_for_display(message) or "").split()).strip()
    if not normalized:
        return "Generation failed."
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."

def _attachment_for_generated_file(result: Dict[str, Any], media_type: str) -> Optional[Dict[str, Any]]:
    file_path = Path(str(result.get("file_path") or "")).expanduser()
    if not file_path.is_file():
        return None
    mimetype = mimetypes.guess_type(str(file_path))[0] or (
        "video/mp4" if media_type == "video" else "image/png"
    )
    return {
        "filename": str(result.get("file_name") or file_path.name),
        "mimetype": mimetype,
        "path": str(file_path),
        "size_bytes": file_path.stat().st_size,
        "meta": {
            "kind": "video" if str(media_type).lower() == "video" else "image",
            "role": "media_generation_result",
            "source": "media_generation_agent",
            "item_id": result.get("item_id"),
        },
    }

def _inline_context_for_generated_media(result: Dict[str, Any], media_type: str) -> List[Dict[str, Any]]:
    attachment = _attachment_for_generated_file(result, media_type)
    if not attachment:
        return []
    try:
        from shared.media_messaging import inline_context_for_client

        return inline_context_for_client([attachment], source="media_generation_agent")
    except Exception as exc:
        logger.warning("Failed to inline generated media attachment: %s", exc)
        return []

def _post_reply_target_message(
    *,
    reply_target: Dict[str, Any],
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
    context: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    try:
        from autoyou_agents.admin_agent.agent import (
            _dispatch_reply_target_message,
            _get_internal_ai_agent_api_token,
            _http,
        )
    except Exception as exc:
        return {"status": "error", "message": f"Internal delivery helpers unavailable: {exc}"}

    token = _get_internal_ai_agent_api_token()
    if not token:
        return {"status": "error", "message": "Internal AI agent API token is not available."}

    normalized_target = normalize_reply_target(reply_target)
    if not normalized_target:
        return {"status": "error", "message": "No valid reply target is available."}

    transport = str(normalized_target.get("transport") or "").strip().lower()
    if transport != "webrtc":
        return _dispatch_reply_target_message(
            message=message,
            reply_target=normalized_target,
            context=context,
            token=token,
        )

    payload: Dict[str, Any] = {
        "message": str(message or "").strip(),
        "metadata": dict(metadata or {}),
        "context": list(context or []),
    }
    session_id = str(normalized_target.get("session_id") or "").strip()
    owner_key = str(normalized_target.get("owner_key") or "").strip()
    if session_id:
        payload["session_id"] = session_id
    if owner_key:
        payload["owner_key"] = owner_key

    return _http(
        "POST",
        "/api/webrtc/send",
        payload,
        timeout=60,
        token=token,
    )

def _build_delivery_metadata(
    *,
    media_type: str,
    item_id: int,
    owner_key: Optional[str],
    canonical_user_id: Optional[str],
    canonical_session_id: Optional[str],
    conversation_session_id: Optional[str],
    reply_target: Optional[Dict[str, Any]],
    status: str,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "source": "media_generation_agent",
        "is_notification": True,
        "notification_delivery_mode": "media_generation_result",
        "notification_delivery_label": "media_generation",
        "agent_name": AGENT_NAME,
        "agent_display_name": "Media Generation",
        "media_generation": {
            "item_id": item_id,
            "media_type": media_type,
            "status": status,
        },
    }
    if owner_key:
        metadata["canonical_owner_key"] = owner_key
    if canonical_user_id:
        metadata["canonical_user_id"] = canonical_user_id
    if canonical_session_id:
        metadata["ai_agent_session_id"] = canonical_session_id
    # Only the id the device was given for the conversation that asked is
    # named, and the result is pinned to it. The history id and the id of a
    # scheduled run are not ids a device holds, so with none to name the
    # engine sends the result to the conversation the device is in now.
    asked_conversation_session_id = str(conversation_session_id or "").strip()
    if asked_conversation_session_id:
        metadata["conversation_session_id"] = asked_conversation_session_id
        metadata["conversation_force_target"] = True
    normalized_reply_target = normalize_reply_target(reply_target)
    if normalized_reply_target:
        metadata["reply_target"] = normalized_reply_target
    return metadata

def _notify_generation_finished(
    *,
    result: Dict[str, Any],
    media_type: str,
    item_id: int,
    owner_key: Optional[str],
    canonical_user_id: Optional[str],
    canonical_session_id: Optional[str],
    conversation_session_id: Optional[str],
    reply_target: Optional[Dict[str, Any]],
) -> None:
    normalized_target = normalize_reply_target(reply_target)
    if not normalized_target:
        logger.info("Media generation job %s finished without a saved reply target.", item_id)
        return

    status = str(result.get("status") or "").strip().lower()
    if status == "success":
        noun = "video" if media_type == "video" else "image"
        context = _inline_context_for_generated_media(result, media_type)
        message = f"Your {noun} is ready."
        if not context:
            file_path = str(result.get("file_path") or "").strip()
            message = f"Your {noun} is ready: {file_path}" if file_path else message
        delivery_status = "completed"
    else:
        noun = "video" if media_type == "video" else "image"
        context = []
        message = f"{noun.title()} generation failed: {_short_error_message(result.get('message'))}"
        delivery_status = "failed"

    metadata = _build_delivery_metadata(
        media_type=media_type,
        item_id=item_id,
        owner_key=owner_key,
        canonical_user_id=canonical_user_id,
        canonical_session_id=canonical_session_id,
        conversation_session_id=conversation_session_id,
        reply_target=normalized_target,
        status=delivery_status,
    )
    delivery_result = _post_reply_target_message(
        reply_target=normalized_target,
        message=message,
        metadata=metadata,
        context=context,
    )
    if delivery_result.get("status") != "success":
        logger.warning(
            "Failed to deliver media generation notification for job %s: %s",
            item_id,
            delivery_result,
        )

def _run_generation_job(
    *,
    prompt: str,
    media_type: str,
    model_type: Optional[str],
    resolution: Optional[str],
    steps: Optional[int],
    frames: Optional[int],
    seed: Optional[int],
    item_id: int,
    owner_key: Optional[str],
    canonical_user_id: Optional[str],
    canonical_session_id: Optional[str],
    conversation_session_id: Optional[str],
    reply_target: Optional[Dict[str, Any]],
) -> None:
    try:
        with _GENERATION_LOCK:
            result = generate_media_sync(
                prompt=prompt,
                media_type=media_type,
                model_type=model_type,
                resolution=resolution,
                steps=steps,
                frames=frames,
                seed=seed,
                history_item_id=item_id,
            )
    except Exception as exc:
        message = f"Background media generation job crashed: {exc}"
        update_history_status(item_id, "failed", message)
        result = {"status": "error", "message": message, "item_id": item_id, "media_type": media_type}

    _notify_generation_finished(
        result=result,
        media_type=media_type,
        item_id=item_id,
        owner_key=owner_key,
        canonical_user_id=canonical_user_id,
        canonical_session_id=canonical_session_id,
        conversation_session_id=conversation_session_id,
        reply_target=reply_target,
    )

def _extract_text_from_llm_request(llm_request: Any) -> str:
    for content in reversed(getattr(llm_request, "contents", []) or []):
        if str(getattr(content, "role", "") or "").strip().lower() not in {"", "user"}:
            continue
        parts: list[str] = []
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return " ".join(parts).strip()
    return ""

def _is_history_request(user_text: str) -> bool:
    lowered = " ".join(str(user_text or "").lower().split())
    return any(
        phrase in lowered
        for phrase in (
            "show media history",
            "list media history",
            "what media have i generated",
            "show generated files",
            "show my videos",
            "list my videos",
            "show my images",
            "list my images",
            "show my creations",
        )
    )

async def _media_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)
    user_text = _extract_text_from_llm_request(llm_request)
    if not user_text:
        return None

    if _is_history_request(user_text):
        history = list_history(limit=10)
        if not history:
            return create_text_llm_response(
                "You have no media generation history yet. You can ask me to generate a video or image!",
                custom_metadata={"response_author": AGENT_NAME, "media_query_kind": "history_empty"}
            )
            
        lines = ["Here is your recent media generation history:"]
        for item in history:
            status_symbol = "✅" if item["status"] == "completed" else "❌" if item["status"] == "failed" else "⏳"
            timestamp = item["timestamp"][:16].replace("T", " ")
            lines.append(
                f"- **[{item['media_type'].upper()}]** #{item['id']}: {item['original_prompt'][:60]}... "
                f"({status_symbol} {item['status']}, {timestamp})"
            )
        lines.append(
            "\nYou can manage, view details, re-run, or configure Wan2GP settings in the "
            "**Media Generator App** at http://127.0.0.1:8067/agent/media_generation_agent/."
        )
        return create_text_llm_response(
            "\n".join(lines),
            custom_metadata={"response_author": AGENT_NAME, "media_query_kind": "history"}
        )

    if _looks_like_generation_request(user_text):
        result = generate_media(
            user_text,
            media_type=_infer_media_type_from_request(user_text),
            tool_context=callback_context,
        )
        message = str(result.get("message") or "").strip()
        if not message:
            message = "Media generation has started. I'll send it here when it is ready."
        return create_text_llm_response(
            message,
            custom_metadata={
                "response_author": AGENT_NAME,
                "media_generation": result,
                "media_generation_deterministic_reply": True,
            },
        )

    return None

def generate_media(
    prompt: str,
    media_type: str = "video",
    model_type: Optional[str] = None,
    resolution: Optional[str] = None,
    steps: Optional[int] = None,
    frames: Optional[int] = None,
    seed: Optional[int] = None,
    tool_context: Optional[Any] = None,
) -> dict:
    """Start high-quality video or image generation using the local Wan2GP wrapper.
    
    Args:
        prompt: Describe what you want the video or image to look like. Be visually precise.
        media_type: Type of media: "video" or "image" (default: "video").
        model_type: Optional Wan2GP model name (e.g. ltx2_distilled_gguf_q4_k_m).
        resolution: Optional output resolution (e.g. 416x240, 848x480).
        steps: Number of inference steps (default: 8).
        frames: Video length in frames (e.g. 49 frames for 2 seconds clip) (default: 49).
        seed: Random seed for reproducibility (-1 for random).
        
    Returns:
        dict: Job-start summary. The finished file is sent back to the saved reply target.
    """
    try:
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            return {"status": "error", "message": "Prompt description is required."}

        normalized_media_type = _normalize_media_type(media_type)
        normalized_model_type = _normalize_model_type(model_type)
        settings = {
            "model_type": normalized_model_type,
            "resolution": resolution,
            "num_inference_steps": steps,
            "video_length": frames,
            "seed": seed
        }
        item_id = save_history_item(
            media_type=normalized_media_type,
            original_prompt=normalized_prompt,
            optimized_prompt="Queued for generation.",
            file_path="",
            file_name="",
            settings=settings,
            status="generating"
        )

        creator_user_id, _creator_ai_session_id = _extract_runtime_identifiers(tool_context)
        owner_key = _extract_owner_key(tool_context)
        canonical_session_id = _extract_canonical_session_id(tool_context)
        conversation_session_id = _extract_conversation_session_id(tool_context)
        reply_target = _extract_delivery_target(tool_context, owner_key)

        thread = threading.Thread(
            target=_run_generation_job,
            kwargs={
                "prompt": normalized_prompt,
                "media_type": normalized_media_type,
                "model_type": normalized_model_type,
                "resolution": resolution,
                "steps": steps,
                "frames": frames,
                "seed": seed,
                "item_id": item_id,
                "owner_key": owner_key,
                "canonical_user_id": creator_user_id,
                "canonical_session_id": canonical_session_id,
                "conversation_session_id": conversation_session_id,
                "reply_target": reply_target,
            },
            daemon=True,
            name=f"media-generation-agent-{item_id}",
        )
        thread.start()

        noun = "video" if normalized_media_type == "video" else "image"
        return {
            "status": "started",
            "item_id": item_id,
            "media_type": normalized_media_type,
            "message": (
                f"{noun.title()} generation has started. "
                f"I'll send the {noun} here when it is ready."
            ),
            "delivery_target_available": bool(reply_target),
        }
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to execute generation: {str(e)}"}

def get_media_history(limit: Optional[int] = 10, media_type: Optional[str] = None) -> dict:
    """List previously generated media runs.
    
    Args:
        limit: Max number of history items to return (default: 10).
        media_type: Filter by media type: "video" or "image".
    """
    try:
        items = list_history(limit=limit or 10, media_type=media_type)
        return {"status": "success", "history": items, "count": len(items)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to load history: {str(e)}"}

def get_media_item(item_id: int) -> dict:
    """Retrieve details of a specific past generation run.
    
    Args:
        item_id: The ID of the history item to retrieve.
    """
    try:
        item = get_history_item(item_id)
        if item:
            return {"status": "success", "item": item}
        return {"status": "error", "message": f"History item {item_id} not found"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to load history item: {str(e)}"}

def create_media_generation_agent(model_config: Any) -> Agent:
    """Create a media generation agent with the provided model configuration.
    
    Args:
        model_config: The model configuration to use for the agent
        
    Returns:
        Agent: Configured media generation agent
    """
    tools = [
        generate_media,
        get_media_history,
        get_media_item,
        get_current_datetime,
    ]
    
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=[_media_before_model_callback],
        tools=tools
    )
