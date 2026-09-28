# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Model Picker agent - hardware-aware local model selection via LLMFit.

This agent wraps the open-source LLMFit tool (https://github.com/AlexsJones/llmfit,
MIT licensed). LLMFit detects the local hardware and scores models by how well
they fit; the agent surfaces a view-only analysis, then - only after explicit
user confirmation and an active admin TOTP session - downloads the chosen model
and switches AutoYou to it.

The admin TOTP session machinery is shared with ``admin_agent`` so a single
elevated session covers both surfaces. The view-only LLMFit analysis and disk
inspection require no session.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from autoyou_agents.shared_tools.datetime_tool import (
    get_current_datetime,
    inject_realtime_datetime_into_request,
)
from shared.session_execution import create_text_llm_response, create_tool_call_llm_response

# Reuse the battle-tested admin TOTP/session + HTTP machinery so the model
# picker shares one elevated admin session with the admin agent.
from autoyou_agents.admin_agent.agent import (
    verify_admin_totp,
    check_admin_session,
    revoke_admin_session,
    _http,
    _check_admin_session,
    _get_session_token,
    _extract_text_from_llm_request,
    _extract_totp_reply_code,
    _get_invocation_id,
    _tool_dispatch_already_happened,
    _mark_tool_dispatch,
    _record_admin_tool_result,
    _state_get,
    _state_set,
    _ADMIN_TOTP_PENDING_STATE_KEY,
    _ADMIN_TOOL_RESULT_INVOCATION_ID_STATE_KEY,
    _ADMIN_TOOL_RESULT_MESSAGE_STATE_KEY,
)

from shared.llmfit_integration import (
    analyze_system,
    recommend_models,
    get_disk_space,
    get_llmfit_binary,
    llmfit_version,
    LLMFIT_SOURCE_URL,
    LLMFIT_LICENSE,
    LLMFitError,
)

LOGGER = logging.getLogger(__name__)

_GB = 1024 ** 3

# ─────────────────────────────────────────────────────────────────────────────
# Recommendation shaping
# ─────────────────────────────────────────────────────────────────────────────

def _download_reference(rec: Dict[str, Any]) -> str:
    """Best pullable reference for a recommendation (Ollama name preferred)."""
    for key in ("ollama_name", "reference", "name"):
        value = str(rec.get(key) or "").strip()
        if value:
            return value
    return ""

def _compact_recommendation(rec: Dict[str, Any]) -> Dict[str, Any]:
    params = rec.get("params_b")
    if params in (None, ""):
        params = rec.get("parameter_count")
    compact = {
        "name": rec.get("name") or rec.get("ollama_name") or rec.get("reference"),
        "download_reference": _download_reference(rec),
        "provider": rec.get("provider"),
        "fit_level": rec.get("fit_level"),
        "score": rec.get("score"),
        "params_b": params,
        "best_quant": rec.get("best_quant"),
        "estimated_tps": rec.get("estimated_tps"),
        "disk_size_gb": rec.get("disk_size_gb"),
        "memory_required_gb": rec.get("memory_required_gb"),
        "context_length": rec.get("effective_context_length") or rec.get("context_length"),
        "use_case": rec.get("use_case") or rec.get("category"),
        "runtime": rec.get("runtime_label") or rec.get("runtime"),
        "installed": bool(rec.get("installed")),
    }
    return {key: value for key, value in compact.items() if value not in (None, "")}

def _find_recommendation(reference: str, recommendations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    needle = (reference or "").strip().lower()
    if not needle:
        return None
    for rec in recommendations:
        candidates = {
            str(rec.get(key) or "").strip().lower()
            for key in ("ollama_name", "reference", "name")
        }
        if needle in candidates:
            return rec
    return None

# ─────────────────────────────────────────────────────────────────────────────
# View-only tools (no admin session required)
# ─────────────────────────────────────────────────────────────────────────────

def analyze_models(limit: int = 5) -> Dict[str, Any]:
    """Run an LLMFit hardware-fit pass and return a view-only analysis.

    Downloads/caches the LLMFit binary from GitHub on first use, detects this
    machine's RAM/CPU/GPU, and returns a ranked list of local models that fit,
    each with its fit level, parameter size, estimated tokens/sec, on-disk size,
    and whether it is already installed. This is read-only and never changes the
    active model.

    Args:
        limit: Maximum number of ranked recommendations to return (default 5).

    Returns:
        dict with ``system`` hardware info, ``recommendations`` list, and ``disk``.
    """
    if get_llmfit_binary() is None:
        return {
            "status": "error",
            "message": (
                "LLMFit is unavailable - it could not be found on PATH or downloaded "
                "from GitHub. Install it (e.g. 'brew install llmfit') or retry online."
            ),
        }

    system = analyze_system()
    recs = recommend_models(limit=limit)
    disk = get_disk_space()

    if not recs.get("available"):
        return {
            "status": "error",
            "message": f"LLMFit could not produce model recommendations: {recs.get('error')}",
            "system": system.get("system") if system.get("available") else None,
        }

    compact = [_compact_recommendation(rec) for rec in recs.get("recommendations", [])]
    free_gb = (disk.get("free_bytes") or 0) / _GB if disk.get("available") else None
    for item in compact:
        size_gb = item.get("disk_size_gb")
        if isinstance(size_gb, (int, float)) and free_gb is not None:
            item["fits_free_disk"] = bool(size_gb <= free_gb)

    return {
        "status": "success",
        "llmfit_source": LLMFIT_SOURCE_URL,
        "llmfit_license": LLMFIT_LICENSE,
        "llmfit_version": llmfit_version(),
        "system": system.get("system") if system.get("available") else None,
        "disk": {
            "free_human": disk.get("free_human"),
            "total_human": disk.get("total_human"),
            "path": disk.get("path"),
        } if disk.get("available") else None,
        "recommendations": compact,
        "message": (
            f"LLMFit analyzed this machine and ranked {len(compact)} fitting model(s). "
            "Present the top option and ask the user to confirm before downloading or switching."
        ),
    }

def get_recommended_model() -> Dict[str, Any]:
    """Return the single best-fitting model LLMFit suggests for this machine.

    View-only. Includes whether the model already fits in the free disk space
    and whether it is already installed, so the agent can decide whether a
    download step is needed before switching.

    Returns:
        dict with the top ``recommendation`` and ``fits_free_disk`` / ``installed``.
    """
    recs = recommend_models(limit=1)
    if not recs.get("available"):
        return {
            "status": "error",
            "message": f"LLMFit could not produce a recommendation: {recs.get('error')}",
        }
    items = recs.get("recommendations", [])
    if not items:
        return {"status": "error", "message": "LLMFit returned no fitting models for this machine."}

    top = _compact_recommendation(items[0])
    disk = get_disk_space()
    free_gb = (disk.get("free_bytes") or 0) / _GB if disk.get("available") else None
    size_gb = top.get("disk_size_gb")
    fits = None
    if isinstance(size_gb, (int, float)) and free_gb is not None:
        fits = bool(size_gb <= free_gb)
    return {
        "status": "success",
        "recommendation": top,
        "download_reference": top.get("download_reference"),
        "installed": top.get("installed", False),
        "fits_free_disk": fits,
        "free_disk_human": disk.get("free_human") if disk.get("available") else None,
        "message": (
            f"Top fit: {top.get('name')} ({top.get('fit_level', 'fit unknown')}). "
            "Confirm with the user before downloading or switching."
        ),
    }

def get_current_disk_space() -> Dict[str, Any]:
    """Report free and total disk space where AutoYou stores models (view-only)."""
    disk = get_disk_space()
    if not disk.get("available"):
        return {"status": "error", "message": disk.get("error") or "Could not read disk usage."}
    return {
        "status": "success",
        "path": disk.get("path"),
        "free_human": disk.get("free_human"),
        "used_human": disk.get("used_human"),
        "total_human": disk.get("total_human"),
        "free_bytes": disk.get("free_bytes"),
        "total_bytes": disk.get("total_bytes"),
        "message": f"{disk.get('free_human')} free of {disk.get('total_human')} at {disk.get('path')}.",
    }

# ─────────────────────────────────────────────────────────────────────────────
# High-risk tools (admin session required)
# ─────────────────────────────────────────────────────────────────────────────

def download_model(
    model_reference: str,
    source: str = "ollama",
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Download an LLMFit-recommended model, after a free-disk-space check.

    **Requires an active admin session.** Initiates a background download via the
    Admin Web API. Use ``analyze_models`` first to pick ``model_reference`` (use
    the recommendation's ``download_reference``).

    Args:
        model_reference: The pullable model reference (e.g. ``qwen2.5:7b``).
        source: ``ollama`` (default) or ``huggingface``.
        tool_context: Injected by ADK. Required for the admin session check.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with the download job, or an error/space-warning.
    """
    reference = str(model_reference or "").strip()
    if not reference:
        return {"status": "error", "message": "model_reference is required."}
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }

    # Free-disk-space guard using the LLMFit-estimated on-disk size.
    rec = _find_recommendation(reference, recommend_models(limit=25).get("recommendations", []))
    disk = get_disk_space()
    if rec and disk.get("available"):
        size_gb = rec.get("disk_size_gb")
        free_gb = (disk.get("free_bytes") or 0) / _GB
        if isinstance(size_gb, (int, float)) and size_gb > free_gb:
            return {
                "status": "error",
                "message": (
                    f"'{reference}' needs about {size_gb:.1f} GB but only {disk.get('free_human')} "
                    "is free. Free up disk space or pick a smaller model before downloading."
                ),
            }

    normalized_source = (source or "ollama").strip().lower()
    payload: Dict[str, Any] = {"source": normalized_source, "title": reference}
    if normalized_source == "huggingface":
        payload["repo_id"] = reference
    else:
        payload["reference"] = reference
    token = _get_session_token(tool_context)
    result = _http("POST", "/api/model-library/download", payload, port=port, host=host, token=token)
    if result.get("status") == "success":
        job = (result.get("data") or {}).get("job") or {}
        job_id = job.get("job_id")
        result["message"] = (
            f"Download started for '{reference}'."
            + (f" Track job {job_id}." if job_id else "")
            + " Once it completes, call switch_to_model() to activate it."
        )
    else:
        result["message"] = (result.get("data") or {}).get("error") or f"Failed to start download for '{reference}'."
    return result

def switch_to_model(
    model_reference: str,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Switch AutoYou's active model to the chosen one and hot-reload the runtime.

    **Requires an active admin session.** Call this ONLY after the user has
    explicitly confirmed they want to change models. The model should already be
    installed (use ``download_model`` first if needed).

    Args:
        model_reference: The model reference to activate (e.g. ``qwen2.5:7b``).
        tool_context: Injected by ADK. Required for the admin session check.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict confirming the switch and AI-runtime reload.
    """
    reference = str(model_reference or "").strip()
    if not reference:
        return {"status": "error", "message": "model_reference is required."}
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    token = _get_session_token(tool_context)
    result = _http(
        "POST",
        "/api/model-library/select",
        {"model": reference, "restart_ai": True},
        port=port,
        host=host,
        timeout=30,
        token=token,
    )
    data = result.get("data") or {}
    if result.get("status") == "success":
        restarted = bool(data.get("restarted_ai"))
        result["message"] = (
            f"AutoYou is now using '{reference}'."
            + (" The AI runtime was hot-reloaded." if restarted else " Restart the AI runtime to apply it.")
        )
    else:
        result["message"] = data.get("error") or f"Failed to switch to '{reference}'."
    return result

# ─────────────────────────────────────────────────────────────────────────────
# Callbacks - datetime injection + deterministic TOTP-reply routing
# ─────────────────────────────────────────────────────────────────────────────

_VERIFY_TOOLS = {"verify_admin_totp", "check_admin_session", "revoke_admin_session"}

async def _model_picker_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)

    user_text = _extract_text_from_llm_request(llm_request)
    if not user_text:
        return None

    invocation_id = _get_invocation_id(callback_context)
    if _tool_dispatch_already_happened(callback_context.state, invocation_id):
        result_invocation_id = str(
            _state_get(callback_context.state, _ADMIN_TOOL_RESULT_INVOCATION_ID_STATE_KEY) or ""
        ).strip()
        if invocation_id and invocation_id == result_invocation_id:
            result_message = str(
                _state_get(callback_context.state, _ADMIN_TOOL_RESULT_MESSAGE_STATE_KEY) or ""
            ).strip()
            if result_message:
                return create_text_llm_response(
                    result_message,
                    custom_metadata={"response_author": AGENT_NAME, "model_picker_deterministic_reply": True},
                )
        return None

    totp_code = _extract_totp_reply_code(user_text)
    if totp_code:
        should_verify = bool(_state_get(callback_context, _ADMIN_TOTP_PENDING_STATE_KEY)) or not _check_admin_session(callback_context)
        if should_verify:
            _mark_tool_dispatch(callback_context.state, invocation_id)
            return create_tool_call_llm_response(
                "verify_admin_totp",
                {"totp_code": totp_code},
                custom_metadata={"response_author": AGENT_NAME, "admin_action": "verify_admin_totp"},
            )
    return None

def _model_picker_after_tool_callback(tool: Any, args: Dict[str, Any], tool_context: Any, tool_response: Any) -> Any:
    del args
    invocation_id = _get_invocation_id(tool_context)
    if not invocation_id or not isinstance(tool_response, dict):
        return None

    tool_name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "").strip()
    if tool_name not in _VERIFY_TOOLS:
        return None

    if tool_name == "verify_admin_totp":
        _state_set(tool_context, _ADMIN_TOTP_PENDING_STATE_KEY, not bool(tool_response.get("valid")))
    elif tool_name == "check_admin_session":
        active = bool(tool_response.get("active"))
        totp_configured = tool_response.get("totp_configured")
        _state_set(tool_context, _ADMIN_TOTP_PENDING_STATE_KEY, (not active) and totp_configured is not False)
    elif tool_name == "revoke_admin_session":
        _state_set(tool_context, _ADMIN_TOTP_PENDING_STATE_KEY, False)

    _record_admin_tool_result(tool_context.state, invocation_id, tool_name, tool_response)
    return None

def create_model_picker_agent(model_config: Any) -> Agent:
    """Create the model picker agent with LLMFit + admin-gated model tools."""
    tools = [
        # View-only LLMFit analysis
        analyze_models,
        get_recommended_model,
        get_current_disk_space,
        # Admin session auth (shared with admin_agent)
        check_admin_session,
        verify_admin_totp,
        revoke_admin_session,
        # High-risk (admin session required)
        download_model,
        switch_to_model,
        # Utility
        get_current_datetime,
    ]
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=[_model_picker_before_model_callback],
        after_tool_callback=[_model_picker_after_tool_callback],
        tools=tools,
    )
