# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-363546313441613439306132-042e669d6d77502cbb7bf266

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-363546313441613439306132-042e669d6d77502cbb7bf266"


import logging
import re
from typing import Any, Dict, Optional

from google.adk.agents import Agent

from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

from .fine_tuning_tool import (
    create_dataset_from_datacollector_handoff as _create_dataset_from_datacollector_handoff,
    create_dataset_from_folder as _create_dataset_from_folder,
    cancel_fine_tuning_job as _cancel_fine_tuning_job,
    delete_dataset as _delete_dataset,
    delete_fine_tuning_job as _delete_fine_tuning_job,
    get_fine_tuning_status as _get_fine_tuning_status,
    get_training_job as _get_training_job,
    install_fine_tuned_model as _install_fine_tuned_model,
    list_datasets as _list_datasets,
    list_training_jobs as _list_training_jobs,
    start_training_job as _start_training_job,
    tail_fine_tuning_job_log as _tail_fine_tuning_job_log,
)
from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME

LOGGER = logging.getLogger(__name__)

_START_TERMS_RE = re.compile(r"\b(start|run|launch|kick off|begin)\b.*\b(fine[- ]?tun|train|training)\b", re.IGNORECASE)
_STATUS_TERMS_RE = re.compile(r"\b(status|progress|eta|log|done|complete|failed|running)\b.*\b(fine[- ]?tun|train|training|model)\b", re.IGNORECASE)
_HALT_TERMS_RE = re.compile(r"\b(cancel|halt|stop|abort)\b.*\b(fine[- ]?tun|train|training|job)\b", re.IGNORECASE)

def _public_job_summary(job_payload: Dict[str, Any]) -> Dict[str, Any]:
    if job_payload.get("status") != "success":
        return job_payload
    job = dict(job_payload.get("job") or {})
    dataset = dict(job_payload.get("dataset") or {})
    safe_dataset = {
        "id": dataset.get("id"),
        "source_type": dataset.get("source_type"),
        "title": dataset.get("title"),
        "sample_count": dataset.get("sample_count"),
        "train_sample_count": dataset.get("train_sample_count"),
        "eval_sample_count": dataset.get("eval_sample_count"),
        "message_count": dataset.get("message_count"),
        "assistant_message_count": dataset.get("assistant_message_count"),
        "warnings": dataset.get("warnings") or [],
    }
    return {"status": "success", "job": job, "dataset": safe_dataset}

def get_fine_tuning_status() -> dict:
    """Inspect fine-tuning storage, dependencies, Ollama, and training readiness."""
    return _get_fine_tuning_status()


def inspect_local_intent_router() -> dict:
    """Check the offline classifier, its license and readiness; no Ollama needed."""
    from shared.intent_router import routing_status
    return routing_status()


def classify_local_request(request: str) -> dict:
    """Evaluate a supplied request locally. Returns a routing hint, never executes it.

    Similarity scores are not probabilities. Uncertain or long requests defer to
    the configured language model. This does not train a language model.
    """
    from shared.intent_router import classify_intent
    return classify_intent(request)

def list_fine_tuning_datasets(limit: Optional[int] = 10) -> dict:
    """List prepared datasets without exposing message contents.

    Args:
        limit: Maximum number of datasets to return.
    """
    return _list_datasets(limit=limit or 10)

def create_folder_dataset(folder_path: str, me_name: Optional[str] = None, title: Optional[str] = None) -> dict:
    """Prepare a private training dataset from supported files in a local folder."""
    return _create_dataset_from_folder(folder_path=folder_path, me_name=me_name, title=title)


def delete_fine_tuning_dataset(dataset_id: str) -> dict:
    """Remove an unreferenced dataset and its private local files."""
    return _delete_dataset(dataset_id)


def import_data_collector_handoff(handoff_code: str, collector_url: Optional[str] = None, title: Optional[str] = None) -> dict:
    """Consume one secure loopback export created by Data Collector Agent."""
    return _create_dataset_from_datacollector_handoff(
        handoff_code=handoff_code,
        collector_url=collector_url,
        title=title,
    )

def start_fine_tuning_job(
    dataset_id: str,
    model_name: Optional[str] = None,
    title: Optional[str] = None,
    training_model_id: Optional[str] = None,
    ollama_base_model: Optional[str] = None,
    max_steps: Optional[int] = None,
    vision_min_pixels: Optional[int] = None,
    vision_max_pixels: Optional[int] = None,
    prepare_only: Optional[bool] = None,
    allow_cpu_training: Optional[bool] = None,
) -> dict:
    """Start a local persona fine-tuning job in the background.

    Args:
        dataset_id: Prepared dataset id from `list_fine_tuning_datasets`.
        model_name: Ollama model name to create after training.
        title: Human-readable job title.
        training_model_id: Hugging Face model id used for LoRA training.
        ollama_base_model: Ollama base model for the generated Modelfile, defaulting to `ministral-3:8b`.
        max_steps: Optional training step limit.
        vision_min_pixels: Optional lower image-processing budget for a vision dataset.
        vision_max_pixels: Optional upper image-processing budget for a vision dataset.
        prepare_only: If true, validates data and writes an Ollama Modelfile without running model training.
        allow_cpu_training: Explicitly allow the supported but slow CPU path. Prefer a small local model rather than the default 8B profile.
    """
    config: Dict[str, Any] = {}
    if training_model_id:
        config["training_model_id"] = training_model_id
    if ollama_base_model:
        config["ollama_base_model"] = ollama_base_model
    if max_steps is not None:
        config["max_steps"] = int(max_steps)
    if vision_min_pixels is not None:
        config["vision_min_pixels"] = int(vision_min_pixels)
    if vision_max_pixels is not None:
        config["vision_max_pixels"] = int(vision_max_pixels)
    if prepare_only is not None:
        config["prepare_only"] = bool(prepare_only)
    if allow_cpu_training is not None:
        config["allow_cpu_training"] = bool(allow_cpu_training)
    return _start_training_job(dataset_id=dataset_id, model_name=model_name, title=title, config=config)


def cancel_fine_tuning_job(job_id: str) -> dict:
    """Request a safe stop for a queued or running fine-tuning job."""
    return _cancel_fine_tuning_job(job_id)

def list_fine_tuning_jobs(limit: Optional[int] = 10) -> dict:
    """List recent fine-tuning jobs."""
    return _list_training_jobs(limit=limit or 10)

def get_fine_tuning_job(job_id: str) -> dict:
    """Get a fine-tuning job by id, including progress and ETA."""
    return _public_job_summary(_get_training_job(job_id))

def tail_fine_tuning_job_log(job_id: str, lines: Optional[int] = 80) -> dict:
    """Read the tail of a trainer log.

    Args:
        job_id: Fine-tuning job id.
        lines: Number of log lines to return.
    """
    return _tail_fine_tuning_job_log(job_id, lines=lines or 80)

def install_fine_tuned_model(job_id: str) -> dict:
    """Install a completed fine-tuning job into Ollama with `ollama create`."""
    return _install_fine_tuned_model(job_id)

def delete_fine_tuning_job(job_id: str, delete_ollama_model: bool = False) -> dict:
    """Delete a completed or failed fine-tuning run and optionally remove its Ollama model.

    Args:
        job_id: Fine-tuning job id.
        delete_ollama_model: Also run `ollama rm` for the generated model name.
    """
    return _delete_fine_tuning_job(job_id, delete_ollama_model=delete_ollama_model)

async def _fine_tuning_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    try:
        user_text = ""
        contents = getattr(llm_request, "contents", None) or []
        if contents:
            last = contents[-1]
            parts = getattr(last, "parts", None) or []
            user_text = " ".join(str(getattr(part, "text", "") or "") for part in parts)
        if _STATUS_TERMS_RE.search(user_text or ""):
            from shared.session_execution import create_text_llm_response

            status = get_fine_tuning_status()
            active_job_id = status.get("active_job_id")
            if active_job_id:
                job_payload = get_fine_tuning_job(str(active_job_id))
                job = job_payload.get("job") or {}
                text = (
                    f"Fine-tuning job {active_job_id} is {job.get('status')} "
                    f"at {job.get('progress_percent')}% with ETA {job.get('eta_seconds')} seconds."
                )
            else:
                text = "No fine-tuning job is currently running."
            return create_text_llm_response(text)
        if _HALT_TERMS_RE.search(user_text or ""):
            from shared.session_execution import create_text_llm_response

            active_job_id = get_fine_tuning_status().get("active_job_id")
            if not active_job_id:
                return create_text_llm_response("No fine-tuning job is currently running.")
            result = cancel_fine_tuning_job(str(active_job_id))
            return create_text_llm_response(str(result.get("message") or "Fine-tuning cancellation requested."))
    except Exception as exc:
        LOGGER.debug("Fine-tuning before-model status shortcut failed: %s", exc)
    return None

def create_fine_tuning_agent(model_config: Any) -> Agent:
    """Create the AutoYou fine-tuning agent."""
    tools = [
        get_fine_tuning_status,
        inspect_local_intent_router,
        classify_local_request,
        list_fine_tuning_datasets,
        create_folder_dataset,
        delete_fine_tuning_dataset,
        import_data_collector_handoff,
        start_fine_tuning_job,
        cancel_fine_tuning_job,
        list_fine_tuning_jobs,
        get_fine_tuning_job,
        tail_fine_tuning_job_log,
        install_fine_tuned_model,
        delete_fine_tuning_job,
        get_current_datetime,
    ]
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=[_fine_tuning_before_model_callback],
        tools=tools,
    )
