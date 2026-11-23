# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-7f353a72ebd3bb0a95003323

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


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
    get_wan2gp_config,
    save_wan2gp_config,
    wan2gp_environment_status,
    draw_things_environment_status,
    detect_and_apply_wan2gp_paths,
    start_wan2gp_install,
    get_active_llm_info,
    seconds_to_video_frames,
    _normalize_media_type,
    _normalize_model_type,
)
from . import engines as media_engines
from . import factory as media_factory
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime, inject_realtime_datetime_into_request
from shared.adk_state import (
    AUTOYOU_CONVERSATION_SESSION_STATE_KEY,
    AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY,
    AUTOYOU_OWNER_KEY_STATE_KEY,
    AUTOYOU_OWNER_KEY_USER_STATE_KEY,
    AUTOYOU_RECENT_MEDIA_ATTACHMENTS_STATE_KEY,
    AUTOYOU_REPLY_TARGET_STATE_KEY,
    AUTOYOU_REPLY_TARGET_USER_STATE_KEY,
    AUTOYOU_TURN_ATTACHMENTS_STATE_KEY,
    derive_reply_target_from_owner_key,
    normalize_reply_target,
    normalize_turn_attachments,
    state_get_first,
)
from shared.session_execution import SESSION_CONTROL_STATE_KEY, normalize_session_control_state
from shared.session_execution import create_text_llm_response

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-7f353a72ebd3bb0a95003323"


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
_COMPLEX_MEDIA_RE = re.compile(
    r"\b(?:"
    r"project|factory|script|storyboard|scene|scenes|stage|stages|refine|refinement|assemble|"
    r"pause|resume|cancel|edit|modify|restyle|inpaint|upscale|transform|replace|"
    r"settings?|config(?:uration)?|profile|memory\s+profile|resolution|frames|steps|engine|"
    r"wan2gp|comfyui|mlx|draw\s*things|sdcpp|hardware|gpu|vram|install|verify|detect|"
    r"long\s+video|multi[- ]?scene|goal\s*loop|plan\s+first|without\s+rendering"
    r")\b|\b\d+(?:\.\d+)?\s*(?:sec|secs|second|seconds|min|mins|minute|minutes|hr|hrs|hour|hours|s\b|m\b)\b",
    re.IGNORECASE,
)
_ATTACHMENT_PATH_RE = re.compile(r"@\s*([^\r\n,]+?)(?:\s*\(\d+\s+sampled|\.|,|$)")


def _looks_like_generation_request(user_text: str) -> bool:
    normalized = " ".join(str(user_text or "").split()).strip()
    if not normalized:
        return False
    return bool(
        _GENERATION_ACTION_RE.search(normalized)
        and (_IMAGE_NOUN_RE.search(normalized) or _VIDEO_NOUN_RE.search(normalized))
    )


def _looks_like_complex_media_request(user_text: str, callback_context: Optional[Any] = None) -> bool:
    normalized = " ".join(str(user_text or "").split()).strip()
    if _COMPLEX_MEDIA_RE.search(normalized):
        return True
    if "attachments:" in normalized.lower():
        return True
    turn_atts = _extract_turn_attachments(callback_context, include_recent=False)
    return bool(turn_atts)


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


def _extract_turn_attachments(tool_context: Optional[Any], *, include_recent: bool = True) -> List[Dict[str, Any]]:
    """Extract multimodal attachments (image, video, audio) from ADK session state."""
    if tool_context is None:
        return []
    state = getattr(tool_context, "state", None)
    if state is None:
        return []
    current = normalize_turn_attachments(state_get_first(state, AUTOYOU_TURN_ATTACHMENTS_STATE_KEY))
    if current:
        return current
    if include_recent:
        return normalize_turn_attachments(state_get_first(state, AUTOYOU_RECENT_MEDIA_ATTACHMENTS_STATE_KEY))
    return []


def _pick_attachment_by_kind(attachments: List[Dict[str, Any]], kind: str) -> Optional[str]:
    for att in attachments:
        if str(att.get("kind") or "").lower() == kind:
            p = str(att.get("path") or "").strip()
            if p and Path(p).is_file():
                return p
    return None


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
    extra_kwargs: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        call_kwargs: Dict[str, Any] = {
            "prompt": prompt,
            "media_type": media_type,
            "model_type": model_type,
            "resolution": resolution,
            "steps": steps,
            "frames": frames,
            "seed": seed,
            "history_item_id": item_id,
        }
        if extra_kwargs:
            call_kwargs.update({k: v for k, v in extra_kwargs.items() if v is not None})
        result = generate_media_sync(**call_kwargs)
        if result.get("status") != "success":
            update_history_status(item_id, "failed", result.get("message") or "Generation failed.")
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


def _is_hardware_status_request(user_text: str) -> bool:
    lowered = " ".join(str(user_text or "").lower().split())
    if any(k in lowered for k in ("install", "switch", "change", "set ", "update", "generate", "create", "render")):
        return False
    return bool(
        re.search(
            r"\b(?:hardware|gpu|vram|engines?|installation|verify)\b",
            lowered,
        )
        and re.search(
            r"\b(?:media|video|image|factory|wan2gp|comfyui|mlx|status|available|check|verify)\b",
            lowered,
        )
    )


def _format_hardware_status_markdown(report: Dict[str, Any]) -> str:
    if report.get("status") != "success":
        return f"Could not inspect Media Factory hardware status: {report.get('message') or 'unknown error'}."
    lines = [
        "**Media Factory Hardware & Engine Status**",
        f"- **GPU**: {report.get('gpu', 'Unknown')} ({report.get('vram_gb', 0)} GB VRAM)",
        f"- **System RAM**: {report.get('ram_gb', 0)} GB",
        f"- **Recommended Memory Profile**: {report.get('recommended_profile', 'auto')}",
        f"- **Active Engine**: `{report.get('active_engine', 'wan2gp')}` (Ready: {', '.join(report.get('ready_engines') or ['none'])})",
        f"- **FFmpeg Multi-Scene Stitching**: {'Ready' if report.get('ffmpeg_available') else 'Missing'}",
        f"- **Long-Video Factory Ready**: {'Yes' if report.get('long_video_ready') else 'No'}",
        "",
        "**Engines**:",
    ]
    for name, info in (report.get("engines_summary") or {}).items():
        badge = "✅ Ready" if info.get("ready") else ("⏳ Not Ready" if info.get("supported") else "— Unsupported OS")
        lines.append(f"- `{name}`: {badge} — {info.get('guidance', '')}")
    return "\n".join(lines)


async def _media_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)
    user_text = _extract_text_from_llm_request(llm_request)
    if not user_text:
        return None

    if _is_hardware_status_request(user_text):
        report = get_media_hardware_status()
        return create_text_llm_response(
            _format_hardware_status_markdown(report),
            custom_metadata={"response_author": AGENT_NAME, "media_query_kind": "hardware_status"},
        )

    if _is_history_request(user_text):
        history = list_history(limit=10)
        if not history:
            return create_text_llm_response(
                "You have no media generation history yet. You can ask me to generate an image, modify a photo, or start a long-video Media Factory project!",
                custom_metadata={"response_author": AGENT_NAME, "media_query_kind": "history_empty"},
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
            "\nYou can manage scenes, edit scripts, or configure engines in the "
            "**Media Factory Studio** at http://127.0.0.1:8067/agent/media_generation_agent/."
        )
        return create_text_llm_response(
            "\n".join(lines),
            custom_metadata={"response_author": AGENT_NAME, "media_query_kind": "history"},
        )

    # Simple one-shot generation stays fast & deterministic; complex factory,
    # script editing, settings, hardware, and multimodal requests go to the LLM.
    if _looks_like_generation_request(user_text) and not _looks_like_complex_media_request(user_text, callback_context):
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


def _delivery_kwargs(tool_context: Optional[Any]) -> Dict[str, Any]:
    creator_user_id, _ = _extract_runtime_identifiers(tool_context)
    owner_key = _extract_owner_key(tool_context)
    canonical_session_id = _extract_canonical_session_id(tool_context)
    conversation_session_id = _extract_conversation_session_id(tool_context)
    reply_target = _extract_delivery_target(tool_context, owner_key)
    return {
        "owner_key": owner_key,
        "canonical_user_id": creator_user_id,
        "canonical_session_id": canonical_session_id,
        "conversation_session_id": conversation_session_id,
        "reply_target": reply_target,
    }


def generate_media(
    prompt: str,
    media_type: str = "video",
    model_type: Optional[str] = None,
    resolution: Optional[str] = None,
    steps: Optional[int] = None,
    frames: Optional[int] = None,
    seed: Optional[int] = None,
    duration_seconds: Optional[float] = None,
    reference_image_path: Optional[str] = None,
    end_image_path: Optional[str] = None,
    input_video_path: Optional[str] = None,
    input_audio_path: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    engine: Optional[str] = None,
    edit_mode: bool = False,
    use_attached_media: bool = True,
    tool_context: Optional[Any] = None,
) -> dict:
    """Start local video or image generation (including I2V, V2V, audio-guided, or image editing).

    Args:
        prompt: Visually precise description of what to generate.
        media_type: "video" or "image" (default: "video").
        model_type: Optional model ID (e.g. ltx2_distilled_gguf_q4_k_m, flux_schnell, flux_dev_kontext).
        resolution: Optional WIDTHxHEIGHT (e.g. 848x480, 1280x720).
        steps: Number of inference steps.
        frames: Video length in frames (or pass duration_seconds).
        seed: Random seed (-1 for random).
        duration_seconds: Desired clip length in seconds (converted to frames automatically).
        reference_image_path: Optional image path for Image-to-Video or Image Editing.
        end_image_path: Optional ending keyframe image path for video.
        input_video_path: Optional input video path for Video-to-Video / continuation.
        input_audio_path: Optional audio path for audio-guided video generation.
        negative_prompt: Optional elements to avoid.
        engine: Optional engine override (wan2gp, comfyui, mlx, draw_things, sdcpp).
        edit_mode: Set True when editing/modifying a reference image.
        use_attached_media: Automatically adopt image/video/audio attached in the chat turn.
    """
    item_id = None
    try:
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            return {"status": "error", "message": "Prompt description is required."}

        normalized_media_type = _normalize_media_type(media_type)
        normalized_model_type = _normalize_model_type(model_type)

        # Auto-adopt multimodal attachments from the chat turn when not explicitly provided.
        if use_attached_media and tool_context is not None:
            atts = _extract_turn_attachments(tool_context, include_recent=edit_mode)
            if not reference_image_path:
                reference_image_path = _pick_attachment_by_kind(atts, "image")
            if normalized_media_type == "video":
                if not input_video_path:
                    input_video_path = _pick_attachment_by_kind(atts, "video")
                if not input_audio_path:
                    input_audio_path = _pick_attachment_by_kind(atts, "audio")

        resolved_frames = frames
        if normalized_media_type == "video" and resolved_frames is None and duration_seconds is not None and float(duration_seconds) > 0:
            resolved_frames = seconds_to_video_frames(float(duration_seconds))

        settings = {
            "model_type": normalized_model_type,
            "resolution": resolution,
            "num_inference_steps": steps,
            "video_length": resolved_frames,
            "seed": seed,
        }
        item_id = save_history_item(
            media_type=normalized_media_type,
            original_prompt=normalized_prompt,
            optimized_prompt="Queued for generation.",
            file_path="",
            file_name="",
            settings=settings,
            status="queued",
        )

        extra_kwargs: Dict[str, Any] = {}
        for k, v in (
            ("duration_seconds", duration_seconds),
            ("reference_image_path", reference_image_path),
            ("end_image_path", end_image_path),
            ("input_video_path", input_video_path),
            ("input_audio_path", input_audio_path),
            ("negative_prompt", negative_prompt),
            ("engine_override", engine),
        ):
            if v is not None:
                extra_kwargs[k] = v
        if edit_mode:
            extra_kwargs["edit_mode"] = True

        delivery = _delivery_kwargs(tool_context)
        thread_kwargs: Dict[str, Any] = {
            "prompt": normalized_prompt,
            "media_type": normalized_media_type,
            "model_type": normalized_model_type,
            "resolution": resolution,
            "steps": steps,
            "frames": resolved_frames,
            "seed": seed,
            "item_id": item_id,
            **delivery,
        }
        if extra_kwargs:
            thread_kwargs["extra_kwargs"] = extra_kwargs

        thread = threading.Thread(
            target=_run_generation_job,
            kwargs=thread_kwargs,
            daemon=True,
            name=f"media-generation-agent-{item_id}",
        )
        thread.start()

        noun = "video" if normalized_media_type == "video" else "image"
        return {
            "status": "started",
            "item_id": item_id,
            "media_type": normalized_media_type,
            "used_reference_image": bool(reference_image_path),
            "used_input_video": bool(input_video_path),
            "used_input_audio": bool(input_audio_path),
            "message": (
                f"{noun.title()} generation has started. "
                f"I'll send the {noun} here when it is ready."
            ),
            "delivery_target_available": bool(delivery["reply_target"]),
        }
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        if item_id is not None:
            update_history_status(item_id, "failed", f"Could not start generation: {e}")
        return {"status": "error", "message": f"Failed to execute generation: {str(e)}"}


def modify_image(
    instruction: str,
    source_image_path: Optional[str] = None,
    model_type: Optional[str] = None,
    resolution: Optional[str] = None,
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    denoise_strength: float = 0.65,
    engine: Optional[str] = None,
    tool_context: Optional[Any] = None,
) -> dict:
    """Modify an attached, specified, or most-recently-generated image with natural-language instructions.

    Args:
        instruction: What to change in the image while preserving untouched details.
        source_image_path: Optional path to the image to edit (defaults to attached or latest generated image).
        model_type: Optional edit model ID (e.g. flux_dev_kontext, qwen_image_edit_20B).
        resolution: Optional output resolution.
        steps: Optional inference steps.
        seed: Optional random seed.
        denoise_strength: Edit strength between 0.1 and 1.0 (default 0.65).
        engine: Optional engine override.
    """
    resolved_src = str(source_image_path or "").strip()
    if not resolved_src or not Path(resolved_src).is_file():
        atts = _extract_turn_attachments(tool_context, include_recent=True)
        resolved_src = _pick_attachment_by_kind(atts, "image") or ""
    if not resolved_src:
        recent_images = list_history(limit=5, media_type="image")
        for item in recent_images:
            fp = str(item.get("file_path") or "").strip()
            if item.get("status") == "completed" and fp and Path(fp).is_file():
                resolved_src = fp
                break
    if not resolved_src:
        return {
            "status": "error",
            "message": "No source image found to modify. Attach an image in chat or generate one first.",
        }
    res = generate_media(
        prompt=instruction,
        media_type="image",
        model_type=model_type,
        resolution=resolution,
        steps=steps,
        seed=seed,
        reference_image_path=resolved_src,
        engine=engine,
        edit_mode=True,
        use_attached_media=False,
        tool_context=tool_context,
    )
    if res.get("status") == "started":
        res["message"] = "Image modification has started. I'll send the edited image here when it is ready."
        res["source_image_name"] = Path(resolved_src).name
        res["denoise_strength"] = denoise_strength
    return res


def create_video_project(
    title: str,
    brief: str,
    target_duration_seconds: float = 30.0,
    scene_duration_seconds: Optional[float] = None,
    resolution: Optional[str] = None,
    aspect_ratio: str = "16:9",
    engine: Optional[str] = None,
    style_bible: str = "",
    llm_provider_override: Optional[str] = None,
    llm_model_override: Optional[str] = None,
    plan_now: bool = True,
    auto_start: bool = False,
    use_keyframes: bool = False,
    tool_context: Optional[Any] = None,
) -> dict:
    """Create a persistent, resumable Media Factory project for short or multi-hour videos.

    Embeds conversation context and any attached images, videos, or audio files,
    generates an editable multi-scene script via the chosen LLM, and optionally
    starts the goal_loop controller immediately.
    """
    try:
        atts = _extract_turn_attachments(tool_context, include_recent=True)
        project = media_factory.create_project(
            title=title,
            original_request=brief,
            kind="long_video",
            target_duration_seconds=float(target_duration_seconds or 30.0),
            scene_duration_seconds=scene_duration_seconds,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            engine=engine,
            style_bible=style_bible,
            llm_provider_override=llm_provider_override,
            llm_model_override=llm_model_override,
            embedded_context={"conversation_summary": brief[:1200]},
            attachments=atts,
            plan_now=bool(plan_now),
        )
        goal_start = None
        if auto_start:
            goal_start = control_video_factory(
                project_id=int(project["id"]),
                action="run_all",
                use_keyframes=use_keyframes,
                tool_context=tool_context,
            )
        safe = media_factory.sanitize_project_for_api(project)
        return {
            "status": "success",
            "project": safe,
            "goal_loop": goal_start,
            "studio_url": "/agent/media_generation_agent/",
            "message": (
                f"Created Media Factory project #{safe['id']} '{safe['title']}' "
                f"({safe.get('scene_count', 0)} scenes, target {safe['target_duration_seconds']:.0f}s)."
                + (" Goal-loop rendering started in background." if auto_start else " Review or edit scenes, or tell me to start rendering.")
            ),
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to create video project: {exc}"}


def list_video_projects(limit: int = 10) -> dict:
    """List Media Factory projects and their current stage/progress."""
    try:
        projects = [media_factory.sanitize_project_for_api(p) for p in media_factory.list_projects(limit=limit)]
        return {"status": "success", "count": len(projects), "projects": projects}
    except Exception as exc:
        return {"status": "error", "message": f"Failed to list projects: {exc}"}


def get_video_project(project_id: int) -> dict:
    """Inspect full script, scenes, goal-loop status, and stage logs for a Media Factory project."""
    try:
        project = media_factory.get_project(int(project_id), include_scenes=True, include_logs=True)
        if not project:
            return {"status": "error", "message": f"Media Factory project #{project_id} not found."}
        from autoyou_agents.self_improvement import goal_loop

        return {
            "status": "success",
            "project": media_factory.sanitize_project_for_api(project),
            "goal_status": goal_loop.get_media_factory_goal_status(int(project_id)),
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to load project: {exc}"}


def edit_video_script(
    project_id: int,
    action: str = "update_scene",
    scene_index: Optional[int] = None,
    title: Optional[str] = None,
    prompt: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    camera_direction: Optional[str] = None,
    audio_cue: Optional[str] = None,
    narration_text: Optional[str] = None,
    transition: Optional[str] = None,
    style_bible: Optional[str] = None,
    script_text: Optional[str] = None,
    target_duration_seconds: Optional[float] = None,
    resolution: Optional[str] = None,
    direction: Optional[str] = None,
    tool_context: Optional[Any] = None,
) -> dict:
    """Edit a Media Factory project's script, settings, or individual scenes conversationally.

    Args:
        project_id: Target project ID.
        action: One of "update_project", "update_scene", "add_scene", "delete_scene", "enhance_scene", "replan".
        scene_index: 1-based OR 0-based scene number (1-based preferred; 1 -> first scene).
        title, prompt, duration_seconds, camera_direction, audio_cue, narration_text, transition: Scene fields.
        style_bible, script_text, target_duration_seconds, resolution: Project-level fields.
        direction: Optional LLM enhancement or replanning instruction.
    """
    try:
        pid = int(project_id)
        project = media_factory.get_project(pid, include_scenes=True)
        if not project:
            return {"status": "error", "message": f"Media Factory project #{pid} not found."}
        norm_action = str(action or "update_scene").strip().lower()
        scenes = project.get("scenes") or []

        def _resolve_scene() -> Optional[Dict[str, Any]]:
            if scene_index is None or not scenes:
                return None
            idx = int(scene_index)
            # Support 1-based natural human numbering ("scene 1") and 0-based indexing cleanly.
            if 1 <= idx <= len(scenes):
                return scenes[idx - 1]
            if 0 <= idx < len(scenes):
                return scenes[idx]
            return None

        if norm_action == "replan":
            updated = media_factory.plan_project_script(pid, instructions=direction or prompt or "")
            return {"status": "success", "project": media_factory.sanitize_project_for_api(updated), "message": f"Replanned project #{pid} with {updated.get('scene_count', 0)} scenes."}

        if norm_action == "update_project":
            updates: Dict[str, Any] = {}
            for k, v in (
                ("title", title),
                ("style_bible", style_bible),
                ("script_text", script_text),
                ("target_duration_seconds", target_duration_seconds),
                ("resolution", resolution),
            ):
                if v is not None:
                    updates[k] = v
            updated = media_factory.update_project(pid, updates)
            return {"status": "success", "project": media_factory.sanitize_project_for_api(updated or {}), "message": f"Updated project #{pid}."}

        if norm_action == "add_scene":
            atts = _extract_turn_attachments(tool_context, include_recent=False)
            created = media_factory.add_scene(
                pid,
                {
                    "title": title or f"Scene {len(scenes) + 1}",
                    "prompt": prompt or "",
                    "duration_seconds": duration_seconds or project.get("scene_duration_seconds") or 5.0,
                    "camera_direction": camera_direction or "",
                    "audio_cue": audio_cue or "",
                    "narration_text": narration_text or "",
                    "transition": transition or "crossfade",
                    "reference_image_path": _pick_attachment_by_kind(atts, "image") or "",
                    "input_video_path": _pick_attachment_by_kind(atts, "video") or "",
                    "input_audio_path": _pick_attachment_by_kind(atts, "audio") or "",
                },
            )
            return {"status": "success", "scene": created, "message": f"Added Scene {created['scene_index'] + 1} to project #{pid}."}

        target_scene = _resolve_scene()
        if not target_scene:
            return {"status": "error", "message": f"Specify a valid scene_index (1..{len(scenes)}) for project #{pid}."}

        if norm_action == "delete_scene":
            media_factory.delete_scene(pid, target_scene["id"])
            return {"status": "success", "message": f"Deleted scene from project #{pid}."}

        if norm_action == "enhance_scene":
            enhanced = media_factory.enhance_scene_prompt(pid, target_scene["id"], direction=direction or prompt or "")
            return {"status": "success", "scene": enhanced, "message": f"Enhanced Scene {target_scene['scene_index'] + 1} via LLM."}

        updates = {}
        for k, v in (
            ("title", title),
            ("prompt", prompt),
            ("duration_seconds", duration_seconds),
            ("camera_direction", camera_direction),
            ("audio_cue", audio_cue),
            ("narration_text", narration_text),
            ("transition", transition),
            ("resolution", resolution),
        ):
            if v is not None:
                updates[k] = v
        atts = _extract_turn_attachments(tool_context, include_recent=False)
        if _pick_attachment_by_kind(atts, "image"):
            updates["reference_image_path"] = _pick_attachment_by_kind(atts, "image")
        if _pick_attachment_by_kind(atts, "video"):
            updates["input_video_path"] = _pick_attachment_by_kind(atts, "video")
        if _pick_attachment_by_kind(atts, "audio"):
            updates["input_audio_path"] = _pick_attachment_by_kind(atts, "audio")
        updated_scene = media_factory.update_scene(pid, target_scene["id"], updates)
        return {"status": "success", "scene": updated_scene, "message": f"Updated Scene {target_scene['scene_index'] + 1} of project #{pid}."}
    except Exception as exc:
        return {"status": "error", "message": f"Failed to edit video script: {exc}"}


def control_video_factory(
    project_id: int,
    action: str = "run_all",
    stage: Optional[str] = None,
    use_keyframes: bool = False,
    scene_indices: Optional[List[int]] = None,
    refinement_direction: str = "",
    tool_context: Optional[Any] = None,
) -> dict:
    """Control a long-running Media Factory project via autoyou_agents/self_improvement/goal_loop.py.

    Args:
        project_id: Media Factory project ID.
        action: "run_all", "run_stage", "pause", "resume", "refine", "assemble", "cancel", or "status".
        stage: When action="run_stage", one of "plan", "keyframes", "render", "refine", "assemble".
        use_keyframes: Include storyboard keyframe generation before video rendering.
        scene_indices: Optional list of 1-based scene numbers to render or refine.
        refinement_direction: Optional critique/direction for refinement pass.
    """
    from autoyou_agents.self_improvement import goal_loop

    pid = int(project_id)
    norm_action = str(action or "status").strip().lower()
    if norm_action == "status":
        return {"status": "success", "goal_status": goal_loop.get_media_factory_goal_status(pid)}
    if norm_action == "pause":
        return goal_loop.pause_media_factory_goal(pid)
    if norm_action == "cancel":
        return goal_loop.cancel_media_factory_goal(pid)

    delivery = _delivery_kwargs(tool_context)
    if norm_action == "resume":
        return goal_loop.resume_media_factory_goal(pid, use_keyframes=use_keyframes, **delivery)

    # Convert 1-based chat scene numbers to 0-based indices for factory internals.
    zero_based: Optional[Tuple[int, ...]] = None
    if scene_indices:
        zero_based = tuple(max(0, int(i) - 1) for i in scene_indices)

    if norm_action == "refine":
        stages: Tuple[str, ...] = ("refine", "assemble")
    elif norm_action == "assemble":
        stages = ("assemble",)
    elif norm_action == "run_stage":
        st = str(stage or "render").strip().lower()
        stages = (st,) if st in media_factory.VALID_STAGES else ("render",)
    else:
        stages = ("plan", "keyframes", "render", "refine", "assemble") if use_keyframes else ("plan", "render", "refine", "assemble")

    cfg = goal_loop.MediaGoalConfig(
        project_id=pid,
        stages=stages,
        use_keyframes=use_keyframes,
        scene_indices=zero_based,
        refinement_direction=str(refinement_direction or "").strip(),
        **delivery,
    )
    return goal_loop.start_media_factory_goal(cfg)


def get_media_settings() -> dict:
    """Return current Media Factory configuration, active LLM, and recommended memory profile."""
    try:
        cfg = get_wan2gp_config()
        return {
            "status": "success",
            "settings": cfg,
            "active_llm": get_active_llm_info(),
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to read media settings: {exc}"}


def update_media_settings(
    engine: Optional[str] = None,
    memory_profile: Optional[str] = None,
    model_type: Optional[str] = None,
    image_model_type: Optional[str] = None,
    image_edit_model_type: Optional[str] = None,
    resolution: Optional[str] = None,
    image_resolution: Optional[str] = None,
    num_inference_steps: Optional[int] = None,
    image_num_inference_steps: Optional[int] = None,
    video_length: Optional[int] = None,
    video_duration_seconds: Optional[float] = None,
    video_fps: Optional[int] = None,
    default_scene_duration_seconds: Optional[float] = None,
    enhance_prompt: Optional[bool] = None,
    llm_provider_override: Optional[str] = None,
    llm_model_override: Optional[str] = None,
    continuity_chain_frames: Optional[bool] = None,
    sliding_window_size: Optional[int] = None,
    sliding_window_overlap: Optional[int] = None,
    comfyui_url: Optional[str] = None,
) -> dict:
    """Update Media Factory settings directly from chat."""
    try:
        cfg = get_wan2gp_config()
        changed: Dict[str, Any] = {}
        if engine is not None:
            norm_engine = str(engine).strip().lower()
            if norm_engine not in media_engines.VALID_ENGINES:
                return {"status": "error", "message": f"Invalid engine '{engine}'. Choose from: {', '.join(media_engines.VALID_ENGINES)}."}
            cfg["engine"] = norm_engine
            changed["engine"] = norm_engine
        if memory_profile is not None:
            cfg["memory_profile"] = str(memory_profile).strip()
            changed["memory_profile"] = cfg["memory_profile"]
        if video_fps is not None:
            cfg["video_fps"] = max(8, min(60, int(video_fps)))
            changed["video_fps"] = cfg["video_fps"]
        if video_duration_seconds is not None and video_length is None:
            video_length = seconds_to_video_frames(float(video_duration_seconds), fps=int(cfg.get("video_fps") or 24))
        for key, value in (
            ("model_type", _normalize_model_type(model_type) if model_type else None),
            ("image_model_type", _normalize_model_type(image_model_type) if image_model_type else None),
            ("image_edit_model_type", _normalize_model_type(image_edit_model_type) if image_edit_model_type else None),
            ("resolution", resolution),
            ("image_resolution", image_resolution),
            ("num_inference_steps", int(num_inference_steps) if num_inference_steps is not None else None),
            ("image_num_inference_steps", int(image_num_inference_steps) if image_num_inference_steps is not None else None),
            ("video_length", int(video_length) if video_length is not None else None),
            ("default_scene_duration_seconds", float(default_scene_duration_seconds) if default_scene_duration_seconds is not None else None),
            ("enhance_prompt", bool(enhance_prompt) if enhance_prompt is not None else None),
            ("llm_provider_override", llm_provider_override),
            ("llm_model_override", llm_model_override),
            ("continuity_chain_frames", bool(continuity_chain_frames) if continuity_chain_frames is not None else None),
            ("sliding_window_size", int(sliding_window_size) if sliding_window_size is not None else None),
            ("sliding_window_overlap", int(sliding_window_overlap) if sliding_window_overlap is not None else None),
            ("comfyui_url", comfyui_url),
        ):
            if value is not None:
                cfg[key] = value
                changed[key] = value
        save_wan2gp_config(cfg)
        return {
            "status": "success",
            "updated": changed,
            "settings": cfg,
            "message": f"Updated Media Factory settings: {', '.join(f'{k}={v}' for k, v in changed.items()) or 'no changes'}.",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to update settings: {exc}"}


def get_media_hardware_status() -> dict:
    """Inspect local GPU/VRAM/RAM telemetry, optimal memory profile, FFmpeg, and all 5 engines."""
    try:
        cfg = get_wan2gp_config()
        wan_env = wan2gp_environment_status()
        dt = draw_things_environment_status(cfg)
        verification = media_engines.verify_media_factory_installation(cfg, wan_env=wan_env, draw_things_status=dt)
        hw = verification.get("hardware") or {}
        profile = hw.get("recommended_memory_profile") or {}
        engines_summary = {
            name: {
                "ready": bool(info.get("ready")),
                "supported": bool(info.get("supported")),
                "guidance": str(info.get("guidance") or "")[:180],
            }
            for name, info in (verification.get("engines") or {}).items()
        }
        return {
            "status": "success",
            "ok": bool(verification.get("ok")),
            "long_video_ready": bool(verification.get("long_video_ready")),
            "active_engine": verification.get("active_engine"),
            "ready_engines": verification.get("ready_engines") or [],
            "gpu": hw.get("gpu_name") or "CPU",
            "gpu_backend": hw.get("gpu_backend") or "cpu",
            "vram_gb": hw.get("gpu_vram_gb", 0.0),
            "ram_gb": hw.get("system_ram_gb", 0.0),
            "unified_memory": bool(hw.get("unified_memory")),
            "recommended_profile": profile.get("label") or str(profile.get("profile") or "auto"),
            "ffmpeg_available": bool(hw.get("ffmpeg_available")),
            "engines_summary": engines_summary,
            "checks": [
                {"name": c.get("name"), "ok": bool(c.get("ok")), "detail": str(c.get("detail") or "")[:180]}
                for c in (verification.get("checks") or [])
            ],
        }
    except Exception as exc:
        return {"status": "error", "message": f"Hardware status check failed: {exc}"}


def install_or_verify_media_engine(action: str = "verify", engine: str = "wan2gp") -> dict:
    """Verify, auto-detect, or start a managed background install for Wan2GP, ComfyUI, or Apple MLX."""
    try:
        norm_action = str(action or "verify").strip().lower()
        norm_engine = str(engine or "wan2gp").strip().lower()
        if norm_action == "detect":
            if norm_engine == "wan2gp":
                return detect_and_apply_wan2gp_paths()
            cfg = get_wan2gp_config()
            return {"status": "success", "engines": media_engines.all_engines_status(cfg)["engines"]}
        if norm_action == "install":
            if norm_engine == "wan2gp":
                return start_wan2gp_install()
            def _save_patch(patch: Dict[str, Any]) -> None:
                c = get_wan2gp_config()
                c.update(patch)
                save_wan2gp_config(c)
            return media_engines.start_managed_engine_install(norm_engine, on_config_update=_save_patch)
        return get_media_hardware_status()
    except Exception as exc:
        return {"status": "error", "message": f"Engine action failed: {exc}"}


def ingest_attachments(
    attachments: List[Dict[str, Any]],
    *,
    intent_hint: Optional[str] = None,
    source: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    tool_context: Optional[Any] = None,
) -> Dict[str, Any]:
    """Handle multimodal media attachments routed to media_generation_agent for editing or animation."""
    norm = normalize_turn_attachments(attachments)
    img = _pick_attachment_by_kind(norm, "image")
    vid = _pick_attachment_by_kind(norm, "video")
    aud = _pick_attachment_by_kind(norm, "audio")
    prompt_text = str(intent_hint or "").strip()
    lowered = prompt_text.lower()

    if img and any(k in lowered for k in ("edit", "modify", "change", "replace", "restyle", "inpaint", "remove", "add")):
        return modify_image(
            instruction=prompt_text or "Enhance this image cleanly with cinematic lighting.",
            source_image_path=img,
            tool_context=tool_context,
        )
    if img or vid or aud:
        media_type = "video" if (vid or aud or any(k in lowered for k in ("video", "animate", "movie", "clip"))) else "image"
        return generate_media(
            prompt=prompt_text or ("Animate this media smoothly with cinematic motion." if media_type == "video" else "Enhance this image."),
            media_type=media_type,
            reference_image_path=img,
            input_video_path=vid,
            input_audio_path=aud,
            edit_mode=(media_type == "image" and bool(img)),
            use_attached_media=False,
            tool_context=tool_context,
        )
    return {"status": "error", "message": "No supported image, video, or audio attachment was found."}


def get_media_history(limit: Optional[int] = 10, media_type: Optional[str] = None) -> dict:
    """List previously generated media runs."""
    try:
        items = list_history(limit=limit or 10, media_type=media_type)
        return {"status": "success", "history": items, "count": len(items)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to load history: {str(e)}"}


def get_media_item(item_id: int) -> dict:
    """Retrieve details of a specific past generation run."""
    try:
        item = get_history_item(item_id)
        if item:
            return {"status": "success", "item": item}
        return {"status": "error", "message": f"History item {item_id} not found"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to load history item: {str(e)}"}


def create_media_generation_agent(model_config: Any) -> Agent:
    """Create the Media Factory agent with the provided active AutoYou chat model configuration."""
    tools = [
        generate_media,
        modify_image,
        create_video_project,
        edit_video_script,
        control_video_factory,
        list_video_projects,
        get_video_project,
        get_media_settings,
        update_media_settings,
        get_media_hardware_status,
        install_or_verify_media_engine,
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
        tools=tools,
    )
