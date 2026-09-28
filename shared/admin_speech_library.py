# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.speech_config import STT_MODEL_SUGGESTIONS

try:
    from faster_whisper.utils import _MODELS as _FASTER_WHISPER_MODELS
except Exception:  # pragma: no cover - optional dependency
    _FASTER_WHISPER_MODELS = {}

try:
    from huggingface_hub import scan_cache_dir, snapshot_download
    from huggingface_hub.constants import HF_HUB_CACHE
except Exception:  # pragma: no cover - optional dependency
    scan_cache_dir = None  # type: ignore[assignment]
    snapshot_download = None  # type: ignore[assignment]
    HF_HUB_CACHE = ""

try:
    from tqdm.auto import tqdm
except Exception:  # pragma: no cover - optional dependency
    tqdm = None  # type: ignore[assignment]


LOGGER = logging.getLogger("autoyou.admin.speech_library")

_ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]

_MODEL_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "tiny.en": {
        "label": "Tiny English",
        "summary": "Fastest offline option for simple English voice commands on CPU.",
        "profile": "Best for: low-end CPU / Raspberry Pi tests",
    },
    "tiny": {
        "label": "Tiny Multilingual",
        "summary": "Small multilingual starter model when you need more than English.",
        "profile": "Best for: quick multilingual checks",
    },
    "base.en": {
        "label": "Base English",
        "summary": "Balanced English model with better accuracy than tiny while staying lightweight.",
        "profile": "Best for: everyday English command use",
    },
    "base": {
        "label": "Base Multilingual",
        "summary": "General multilingual model for local-first installs that need a wider language range.",
        "profile": "Best for: multilingual home labs",
    },
    "small.en": {
        "label": "Small English",
        "summary": "Stronger English accuracy with moderate hardware requirements.",
        "profile": "Best for: clearer call transcription on CPU/GPU",
    },
    "small": {
        "label": "Small Multilingual",
        "summary": "Improved multilingual transcription for users who speak more than one language.",
        "profile": "Best for: mixed-language calls",
    },
    "medium.en": {
        "label": "Medium English",
        "summary": "High-quality English transcription that benefits from more RAM and a better CPU or GPU.",
        "profile": "Best for: workstation-class hosts",
    },
    "medium": {
        "label": "Medium Multilingual",
        "summary": "Heavier multilingual model for better recognition quality across languages.",
        "profile": "Best for: multilingual accuracy over speed",
    },
    "large-v3": {
        "label": "Large v3",
        "summary": "Highest-quality standard Whisper family model in the default faster-whisper catalog.",
        "profile": "Best for: GPU-backed servers",
    },
    "distil-large-v3": {
        "label": "Distil Large v3",
        "summary": "Optimized large-class model with a better speed-to-quality balance than full large-v3.",
        "profile": "Best for: fast high-quality transcription",
    },
    "distil-small.en": {
        "label": "Distil Small English",
        "summary": "Compact distilled English model for faster warm-up and lower memory use.",
        "profile": "Best for: latency-sensitive English calls",
    },
    "distil-medium.en": {
        "label": "Distil Medium English",
        "summary": "Mid-tier distilled English model for improved quality without full large-model overhead.",
        "profile": "Best for: balanced English accuracy",
    },
    "large-v3-turbo": {
        "label": "Large v3 Turbo",
        "summary": "Turbo-flavored large-v3 variant for faster inference on stronger machines.",
        "profile": "Best for: modern GPU systems",
    },
    "turbo": {
        "label": "Turbo Alias",
        "summary": "Alias for the faster large-v3 turbo family.",
        "profile": "Best for: power users who prefer turbo shorthand",
    },
}


def _now_ts() -> float:
    return time.time()


def _format_bytes(size: Optional[int]) -> str:
    if not isinstance(size, int) or size <= 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024.0
    return f"{size}B"


def _format_timestamp(value: Optional[float]) -> str:
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def _build_repo_id(model_name: str) -> str:
    normalized = (model_name or "").strip()
    if not normalized:
        raise ValueError("STT model name cannot be empty")
    if "/" in normalized:
        return normalized
    repo_id = _FASTER_WHISPER_MODELS.get(normalized)
    if repo_id:
        return repo_id
    raise ValueError(f"Unsupported STT model '{normalized}'")


def _suggested_models(current_model: str = "") -> List[str]:
    ordered: List[str] = []
    for name in list(STT_MODEL_SUGGESTIONS) + ["distil-small.en", "distil-medium.en", "large-v3-turbo"]:
        if name not in ordered:
            ordered.append(name)

    extra_current = (current_model or "").strip()
    if extra_current and extra_current not in ordered:
        ordered.insert(0, extra_current)
    return ordered


@dataclass
class SpeechDownloadJob:
    job_id: str
    model_name: str
    repo_id: str
    title: str
    status: str = "queued"
    message: str = "Queued"
    progress: float = 0.0
    completed_bytes: int = 0
    total_bytes: int = 0
    local_path: str = ""
    error: str = ""
    created_at: float = field(default_factory=_now_ts)
    updated_at: float = field(default_factory=_now_ts)
    finished_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["completed_bytes_human"] = _format_bytes(self.completed_bytes)
        payload["total_bytes_human"] = _format_bytes(self.total_bytes)
        payload["progress_percent"] = round(max(0.0, min(1.0, self.progress)) * 100.0, 1)
        return payload


class SpeechModelLibraryService:
    def __init__(self) -> None:
        self._jobs: Dict[str, SpeechDownloadJob] = {}
        self._jobs_lock = threading.Lock()

    def is_supported(self) -> bool:
        return bool(snapshot_download and scan_cache_dir and tqdm and _FASTER_WHISPER_MODELS)

    def cache_dir(self) -> str:
        return str(HF_HUB_CACHE or Path.home() / ".cache" / "huggingface" / "hub")

    def _scan_cache(self) -> Dict[str, Dict[str, Any]]:
        if not self.is_supported():
            return {}

        try:
            info = scan_cache_dir()
        except Exception as exc:
            LOGGER.debug("Failed to inspect Hugging Face cache: %s", exc)
            return {}

        repos: Dict[str, Dict[str, Any]] = {}
        for repo in list(info.repos or []):
            repo_id = str(getattr(repo, "repo_id", "") or "").strip()
            repo_type = str(getattr(repo, "repo_type", "") or "").strip()
            if not repo_id or repo_type not in {"", "model"}:
                continue
            repos[repo_id] = {
                "repo_id": repo_id,
                "repo_path": str(getattr(repo, "repo_path", "") or ""),
                "size_on_disk": int(getattr(repo, "size_on_disk", 0) or 0),
                "last_accessed": _format_timestamp(getattr(repo, "last_accessed", None)),
                "last_modified": _format_timestamp(getattr(repo, "last_modified", None)),
            }
        return repos

    def get_status(self, current_model: str = "") -> Dict[str, Any]:
        cache_index = self._scan_cache()
        items: List[Dict[str, Any]] = []
        installed: List[Dict[str, Any]] = []

        for model_name in _suggested_models(current_model):
            try:
                repo_id = _build_repo_id(model_name)
            except ValueError:
                repo_id = model_name
            metadata = _MODEL_DESCRIPTIONS.get(model_name, {})
            cached = cache_index.get(repo_id)
            item = {
                "model": model_name,
                "repo_id": repo_id,
                "label": metadata.get("label") or model_name,
                "summary": metadata.get("summary") or "Compatible faster-whisper model for AutoYou speech-to-text.",
                "profile": metadata.get("profile") or "Best for: general local transcription",
                "installed": bool(cached),
                "selected": model_name == (current_model or "").strip(),
                "size_on_disk": int((cached or {}).get("size_on_disk") or 0),
                "size_on_disk_human": _format_bytes(int((cached or {}).get("size_on_disk") or 0)),
                "repo_path": str((cached or {}).get("repo_path") or ""),
                "last_modified": str((cached or {}).get("last_modified") or ""),
            }
            items.append(item)
            if item["installed"]:
                installed.append(item)

        installed.sort(key=lambda item: (item.get("last_modified", ""), item["model"]), reverse=True)
        return {
            "supported": self.is_supported(),
            "selected_model": (current_model or "").strip(),
            "cache_dir": self.cache_dir(),
            "models": items,
            "installed_models": installed,
        }

    def delete_model(self, model_name: str) -> Dict[str, Any]:
        """Delete a cached faster-whisper STT model from the Hugging Face cache.

        Args:
            model_name: The STT model name (e.g. ``small.en``) or its repo id.

        Returns:
            dict with ``status`` ("success"|"error") and a human ``message``.
        """
        if not self.is_supported():
            return {
                "status": "error",
                "message": "Speech model management is unavailable because faster-whisper or huggingface_hub is missing",
            }

        normalized_name = (model_name or "").strip()
        if not normalized_name:
            return {"status": "error", "message": "STT model name cannot be empty"}
        try:
            repo_id = _build_repo_id(normalized_name)
        except ValueError:
            repo_id = normalized_name

        try:
            info = scan_cache_dir()
        except Exception as exc:
            LOGGER.warning("Failed to inspect Hugging Face cache for deletion: %s", exc)
            return {"status": "error", "message": f"Failed to inspect speech model cache: {exc}"}

        target = None
        for repo in list(info.repos or []):
            if str(getattr(repo, "repo_type", "") or "").strip() not in {"", "model"}:
                continue
            if str(getattr(repo, "repo_id", "") or "").strip() == repo_id:
                target = repo
                break

        if target is None:
            return {
                "status": "error",
                "message": f"STT model '{normalized_name}' is not cached locally.",
            }

        revisions = [
            str(getattr(rev, "commit_hash", "") or "").strip()
            for rev in (getattr(target, "revisions", None) or [])
            if str(getattr(rev, "commit_hash", "") or "").strip()
        ]
        if not revisions:
            return {
                "status": "error",
                "message": f"No deletable revisions were found for STT model '{normalized_name}'.",
            }

        try:
            strategy = info.delete_revisions(*revisions)
            freed = str(getattr(strategy, "expected_freed_size_str", "") or "").strip()
            strategy.execute()
        except Exception as exc:
            LOGGER.warning("Failed to delete cached STT model %s: %s", normalized_name, exc)
            return {"status": "error", "message": f"Failed to delete '{normalized_name}': {exc}"}

        message = f"Deleted cached STT model '{normalized_name}'."
        if freed:
            message += f" Freed {freed}."
        return {"status": "success", "message": message, "model": normalized_name, "repo_id": repo_id}

    def _get_job(self, job_id: str) -> Optional[SpeechDownloadJob]:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def _update_job(self, job_id: str, **updates: Any) -> None:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in updates.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.updated_at = _now_ts()
            if job.status in {"completed", "failed"} and job.finished_at is None:
                job.finished_at = job.updated_at

    def _make_tqdm_class(self, job_id: str):
        service = self

        class _JobTqdm(tqdm):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("leave", False)
                super().__init__(*args, **kwargs)
                service._sync_progress(job_id, self)

            def update(self, n=1):
                result = super().update(n)
                service._sync_progress(job_id, self)
                return result

            def refresh(self, *args, **kwargs):
                result = super().refresh(*args, **kwargs)
                service._sync_progress(job_id, self)
                return result

            def close(self):
                service._sync_progress(job_id, self)
                return super().close()

        return _JobTqdm

    def _sync_progress(self, job_id: str, progress_bar: Any) -> None:
        total = int(getattr(progress_bar, "total", 0) or 0)
        completed = int(getattr(progress_bar, "n", 0) or 0)
        message = str(getattr(progress_bar, "desc", "") or "").strip() or "Downloading STT model files..."
        percent = 0.0
        if total > 0:
            percent = max(0.0, min(1.0, completed / total))
        self._update_job(
            job_id,
            status="running",
            message=message,
            completed_bytes=completed,
            total_bytes=total,
            progress=percent,
        )

    def start_download_job(self, model_name: str) -> Dict[str, Any]:
        if not self.is_supported():
            raise RuntimeError("Speech model downloads are unavailable because faster-whisper or huggingface_hub is missing")

        normalized_name = (model_name or "").strip()
        repo_id = _build_repo_id(normalized_name)
        with self._jobs_lock:
            for existing in self._jobs.values():
                if existing.model_name == normalized_name and existing.status in {"queued", "running"}:
                    return existing.to_dict()

            job = SpeechDownloadJob(
                job_id=uuid.uuid4().hex,
                model_name=normalized_name,
                repo_id=repo_id,
                title=_MODEL_DESCRIPTIONS.get(normalized_name, {}).get("label") or normalized_name,
            )
            self._jobs[job.job_id] = job

        worker = threading.Thread(
            target=self._download_worker,
            kwargs={"job_id": job.job_id},
            name=f"autoyou-stt-cache-{job.job_id[:8]}",
            daemon=True,
        )
        worker.start()
        return job.to_dict()

    def _download_worker(self, *, job_id: str) -> None:
        job = self._get_job(job_id)
        if job is None:
            return

        if not self.is_supported():
            self._update_job(job_id, status="failed", error="Speech model downloads are not supported in this environment")
            return

        self._update_job(job_id, status="running", message=f"Preparing {job.repo_id}...")

        try:
            local_path = snapshot_download(
                job.repo_id,
                allow_patterns=_ALLOW_PATTERNS,
                resume_download=True,
                tqdm_class=self._make_tqdm_class(job_id),
            )
            self._update_job(
                job_id,
                status="completed",
                message="STT model cached locally",
                progress=1.0,
                local_path=str(local_path),
                finished_at=_now_ts(),
            )
        except Exception as exc:
            LOGGER.warning("Failed to cache STT model %s: %s", job.model_name, exc)
            self._update_job(
                job_id,
                status="failed",
                message="STT model download failed",
                error=str(exc),
                finished_at=_now_ts(),
            )

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self._get_job(job_id)
        return job.to_dict() if job is not None else None

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda item: item.updated_at, reverse=True)
        return [job.to_dict() for job in jobs[:10]]
