# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-9a71783b667a0c71f6e21801

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-9a71783b667a0c71f6e21801"


import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from shared.ollama_capabilities import inspect_ollama_model_capabilities

try:
    import ollama
except Exception:  # pragma: no cover - optional import guard
    ollama = None

try:
    from huggingface_hub import HfApi
except Exception:  # pragma: no cover - optional import guard
    HfApi = None  # type: ignore[assignment]


LOGGER = logging.getLogger("autoyou.admin.model_library")

OLLAMA_SEARCH_URL = "https://ollama.com/search"
OLLAMA_LIBRARY_URL = "https://ollama.com/library/{slug}"
HUGGINGFACE_MODEL_URL = "https://huggingface.co/{repo_id}"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
HF_TOKEN_ENV_NAMES = ("HF_TOKEN", "HUGGINGFACE_TOKEN")
DEFAULT_CACHE_TTL_SECONDS = 300.0

_QUANTIZATION_PREFERENCE = [
    "Q4_K_M",
    "Q4_K_S",
    "Q5_K_M",
    "Q5_K_S",
    "Q6_K",
    "Q8_0",
    "IQ4_XS",
    "IQ4_NL",
    "IQ3_M",
    "IQ3_XS",
    "Q4_1",
    "Q4_0",
    "Q5_1",
    "Q5_0",
    "Q3_K_L",
    "Q3_K_M",
    "Q3_K_S",
    "Q3_K_XL",
    "Q2_K",
    "F16",
    "BF16",
    "F32",
]


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


def _maybe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _normalize_api_base(api_base: str) -> str:
    normalized = (api_base or "http://localhost:11434").strip()
    return normalized.rstrip("/")


def is_ollama_cloud_model_reference(name: str) -> bool:
    """Return True when an Ollama model reference points at Ollama Cloud.

    Current Ollama cloud tags use forms like `:cloud` and `:31b-cloud`.
    See https://docs.ollama.com/cloud and the public library pages such as
    https://ollama.com/library/gemma4.
    """
    normalized = (name or "").strip().lower()
    if not normalized:
        return False
    tag = normalized.rsplit(":", 1)[-1]
    return bool(re.search(r"(?:^|-)cloud$", tag))


def _read_env_token() -> Optional[str]:
    for name in HF_TOKEN_ENV_NAMES:
        token = os.getenv(name, "").strip()
        if token:
            return token
    return None


def _quantization_sort_key(name: Optional[str]) -> Tuple[int, str]:
    normalized = (name or "").upper()
    try:
        return (_QUANTIZATION_PREFERENCE.index(normalized), normalized)
    except ValueError:
        return (len(_QUANTIZATION_PREFERENCE), normalized)


def extract_hf_quantization(filename: str) -> Optional[str]:
    stem = Path(filename or "").stem
    if not stem:
        return None

    matches = re.findall(
        r"((?:IQ|Q)\d+(?:_[A-Z0-9]+)+|Q\d+_\d|Q8_0|Q6_K|Q5_[01]|Q4_[01]|F16|F32|BF16|FP16|FP32)$",
        stem,
        flags=re.IGNORECASE,
    )
    if matches:
        return matches[-1]

    last_token = stem.split("-")[-1]
    if re.match(r"^(?:IQ|Q)[A-Z0-9_]+$|^(?:F16|F32|BF16|FP16|FP32)$", last_token, flags=re.IGNORECASE):
        return last_token

    return None


def build_hf_ollama_reference(repo_id: str, quantization: Optional[str] = None) -> str:
    repo = (repo_id or "").strip().strip("/")
    if not repo:
        raise ValueError("Hugging Face repository id cannot be empty")
    reference = f"hf.co/{repo}"
    quant = (quantization or "").strip()
    if quant:
        reference = f"{reference}:{quant}"
    return reference


@dataclass
class _CacheEntry:
    value: Any
    created_at: float = field(default_factory=_now_ts)


@dataclass
class DownloadJob:
    job_id: str
    source: str
    reference: str
    model_name: str
    title: str
    status: str = "queued"
    message: str = "Queued"
    progress: float = 0.0
    completed_bytes: int = 0
    total_bytes: int = 0
    digest: str = ""
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


class ModelLibraryService:
    def __init__(self) -> None:
        self._cache: Dict[str, _CacheEntry] = {}
        self._cache_lock = threading.Lock()
        self._jobs: Dict[str, DownloadJob] = {}
        self._jobs_lock = threading.Lock()
        self._hf_api = HfApi() if HfApi is not None else None

    def _cache_get(self, key: str, max_age_seconds: float = DEFAULT_CACHE_TTL_SECONDS) -> Any:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if (_now_ts() - entry.created_at) > max_age_seconds:
                self._cache.pop(key, None)
                return None
            return entry.value

    def _cache_set(self, key: str, value: Any) -> Any:
        with self._cache_lock:
            self._cache[key] = _CacheEntry(value=value)
        return value

    def get_ollama_runtime_status(self, api_base: str, selected_model: str = "") -> Dict[str, Any]:
        normalized_base = _normalize_api_base(api_base)
        cli_path = shutil.which("ollama")
        installed = bool(cli_path)
        server_json_path = Path.home() / ".ollama" / "server.json"
        cloud_disabled = False
        cloud_disable_error = ""
        if server_json_path.exists():
            try:
                data = json.loads(server_json_path.read_text(encoding="utf-8"))
                cloud_disabled = bool(data.get("disable_ollama_cloud", False))
            except Exception as exc:
                cloud_disable_error = str(exc)

        installed_models = self.list_local_models(normalized_base)
        local_models = [model for model in installed_models if not bool(model.get("is_cloud"))]
        cloud_models = [model for model in installed_models if bool(model.get("is_cloud"))]
        api_reachable = bool(installed_models) or self._ping_ollama(normalized_base)
        selected = (selected_model or "").strip()
        has_selected_model = bool(selected and any(model["name"] == selected for model in installed_models))

        if not installed:
            state = "missing"
            detail = "Ollama is not installed on this machine yet."
        elif not api_reachable:
            state = "stopped"
            detail = f"Ollama is installed but not responding at {normalized_base}."
        elif not installed_models:
            state = "ready"
            detail = "Ollama is running and ready, but no models are installed yet."
        elif not local_models:
            state = "ready"
            detail = (
                f"Ollama is running with {len(cloud_models)} cloud model reference(s) available, "
                "but no local models are installed yet."
            )
        elif not cloud_models:
            state = "ready"
            detail = f"Ollama is running with {len(local_models)} local model(s) available."
        else:
            state = "ready"
            detail = (
                f"Ollama is running with {len(local_models)} local model(s) and "
                f"{len(cloud_models)} cloud model reference(s) available."
            )

        return {
            "api_base": normalized_base,
            "installed": installed,
            "cli_path": cli_path or "",
            "api_reachable": api_reachable,
            "download_url": OLLAMA_DOWNLOAD_URL,
            "cloud_disabled": cloud_disabled,
            "cloud_disable_error": cloud_disable_error,
            "server_json_path": str(server_json_path),
            "state": state,
            "detail": detail,
            "local_model_count": len(local_models),
            "cloud_model_count": len(cloud_models),
            "installed_model_count": len(installed_models),
            "selected_model": selected,
            "selected_model_installed": has_selected_model,
        }

    def _ping_ollama(self, api_base: str) -> bool:
        try:
            with httpx.Client(timeout=2.5) as client:
                response = client.get(f"{api_base}/api/tags")
            return response.status_code == 200
        except Exception:
            return False

    def list_local_models(self, api_base: str) -> List[Dict[str, Any]]:
        normalized_base = _normalize_api_base(api_base)
        cache_key = f"local_models::{normalized_base}"
        cached = self._cache_get(cache_key, max_age_seconds=4.0)
        if cached is not None:
            return cached

        if ollama is None:
            return []

        try:
            client = ollama.Client(host=normalized_base)
            payload = client.list()
            models = payload.get("models", [])
        except Exception as exc:
            LOGGER.debug("Failed to list local Ollama models: %s", exc)
            return []

        normalized_models: List[Dict[str, Any]] = []
        for model in models:
            name = (model.get("model") or model.get("name") or "").strip()
            if not name:
                continue
            details = model.get("details") or {}
            size = model.get("size")
            capability = inspect_ollama_model_capabilities(
                normalized_base,
                name,
                client=client,
            )
            normalized_models.append(
                {
                    "name": name,
                    "size": size,
                    "size_human": _format_bytes(size),
                    "modified_at": str(model.get("modified_at") or ""),
                    "digest": str(model.get("digest") or ""),
                    "family": details.get("family") or capability.get("family") or "",
                    "parameter_size": details.get("parameter_size") or capability.get("parameter_size") or "",
                    "quantization_level": details.get("quantization_level") or capability.get("quantization_level") or "",
                    "available": capability.get("available", False),
                    "capabilities": capability.get("capabilities", []),
                    "supports_thinking": capability.get("supports_thinking"),
                    "thinking_levels": capability.get("thinking_levels", []),
                    "supports_tools": capability.get("supports_tools"),
                    "supports_vision": capability.get("supports_vision"),
                    "capability_source": capability.get("capability_source", "unavailable"),
                    "capability_error": capability.get("error", ""),
                    "is_huggingface": name.startswith("hf.co/"),
                    "is_cloud": is_ollama_cloud_model_reference(name),
                }
            )

        normalized_models.sort(key=lambda item: ((item.get("modified_at") or ""), item["name"]), reverse=True)
        return self._cache_set(cache_key, normalized_models)

    def search_ollama_catalog(
        self,
        query: str = "",
        *,
        page: int = 1,
        include_cloud: bool = False,
    ) -> Dict[str, Any]:
        normalized_query = (query or "").strip()
        safe_page = max(1, int(page or 1))
        cache_key = f"ollama_search::{normalized_query.lower()}::{safe_page}::{include_cloud}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        params = {"page": safe_page}
        if normalized_query:
            params["q"] = normalized_query

        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.get(OLLAMA_SEARCH_URL, params=params)
            response.raise_for_status()

        items, has_more = self._parse_ollama_search_results(response.text, safe_page)
        if not include_cloud:
            filtered_items: List[Dict[str, Any]] = []
            for item in items:
                if not item.get("has_cloud_badge"):
                    filtered_items.append(item)
                    continue
                details = self.get_ollama_model_details(item["id"])
                local_variants = [variant for variant in details.get("variants", []) if not variant.get("is_cloud")]
                if local_variants:
                    item = dict(item)
                    item["local_variant_count"] = len(local_variants)
                    # Remove 'Cloud' capability since we're showing this with local variants available
                    item["capabilities"] = [cap for cap in item.get("capabilities", []) if cap.lower() != "cloud"]
                    filtered_items.append(item)
            items = filtered_items

        payload = {
            "source": "ollama",
            "query": normalized_query,
            "page": safe_page,
            "items": items,
            "has_more": has_more,
        }
        return self._cache_set(cache_key, payload)

    def _parse_ollama_search_results(self, html_text: str, page: int) -> Tuple[List[Dict[str, Any]], bool]:
        soup = BeautifulSoup(html_text, "html.parser")
        items: List[Dict[str, Any]] = []
        for li in soup.select("li[x-test-model], li.flex.items-baseline"):
            link = li.find("a", href=re.compile(r"^/library/[^/]+$"))
            if link is None:
                continue

            href = link.get("href", "")
            slug = href.split("/library/", 1)[-1].strip("/")
            if not slug:
                continue

            title_el = link.find(attrs={"x-test-search-response-title": True}) or link.find("h2")
            title = " ".join(title_el.get_text(" ", strip=True).split()) if title_el else slug
            summary_el = link.find("p", class_=re.compile(r"text-neutral-800"))
            summary = " ".join(summary_el.get_text(" ", strip=True).split()) if summary_el else ""
            capabilities = [
                " ".join(el.get_text(" ", strip=True).split())
                for el in link.find_all(attrs={"x-test-capability": True})
            ]
            sizes = [
                " ".join(el.get_text(" ", strip=True).split())
                for el in link.find_all(attrs={"x-test-size": True})
            ]
            pulls_el = link.find(attrs={"x-test-pull-count": True})
            tag_count_el = link.find(attrs={"x-test-tag-count": True})
            updated_el = link.find(attrs={"x-test-updated": True})
            items.append(
                {
                    "id": slug,
                    "title": title,
                    "summary": summary,
                    "capabilities": capabilities,
                    "sizes": sizes,
                    "pull_count": pulls_el.get_text(" ", strip=True) if pulls_el else "",
                    "tag_count": tag_count_el.get_text(" ", strip=True) if tag_count_el else "",
                    "updated": updated_el.get_text(" ", strip=True) if updated_el else "",
                    "url": f"https://ollama.com{href}",
                    "has_cloud_badge": any(cap.lower() == "cloud" for cap in capabilities),
                }
            )

        has_more = f'hx-get="/search?page={page + 1}"' in html_text
        return items, has_more

    def get_ollama_model_details(self, slug: str) -> Dict[str, Any]:
        normalized_slug = (slug or "").strip().strip("/")
        if not normalized_slug:
            raise ValueError("Ollama model slug cannot be empty")

        cache_key = f"ollama_detail::{normalized_slug}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.get(OLLAMA_LIBRARY_URL.format(slug=normalized_slug))
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        title_el = soup.find("title")
        title = normalized_slug
        if title_el:
            title = title_el.get_text(strip=True).split("·", 1)[0].strip() or normalized_slug
        description_meta = soup.find("meta", attrs={"name": "description"})
        summary = (description_meta.get("content", "") if description_meta else "").strip()

        variants_by_name: Dict[str, Dict[str, Any]] = {}
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if not href.startswith("/library/") or href.endswith("/tags"):
                continue

            model_name = href.split("/library/", 1)[-1].strip("/")
            if ":" not in model_name:
                continue

            classes = link.get("class") or []
            if "sm:hidden" not in classes:
                continue

            if model_name in variants_by_name:
                continue

            paragraphs = link.find_all("p")
            tag_name = " ".join(paragraphs[0].get_text(" ", strip=True).split()) if paragraphs else model_name
            metadata_text = " ".join(paragraphs[1].get_text(" ", strip=True).split()) if len(paragraphs) > 1 else ""
            metadata_bits = [bit.strip() for bit in metadata_text.split("·") if bit.strip()]
            variants_by_name[model_name] = {
                "name": model_name,
                "display_name": tag_name,
                "size": metadata_bits[0] if len(metadata_bits) > 0 else "",
                "context": metadata_bits[1] if len(metadata_bits) > 1 else "",
                "input_type": metadata_bits[2] if len(metadata_bits) > 2 else "",
                "updated": metadata_bits[3] if len(metadata_bits) > 3 else "",
                "is_cloud": is_ollama_cloud_model_reference(model_name),
                "url": f"https://ollama.com{href}",
            }

        variants = sorted(variants_by_name.values(), key=lambda item: (item["is_cloud"], item["name"]))
        default_reference = ""
        for variant in variants:
            if not variant["is_cloud"]:
                default_reference = variant["name"]
                break
        if not default_reference and variants:
            default_reference = variants[0]["name"]

        payload = {
            "source": "ollama",
            "id": normalized_slug,
            "title": title,
            "summary": summary,
            "url": f"https://ollama.com/library/{normalized_slug}",
            "variants": variants,
            "default_reference": default_reference,
            "has_cloud_variant": any(variant["is_cloud"] for variant in variants),
            "local_variant_count": len([variant for variant in variants if not variant["is_cloud"]]),
        }
        return self._cache_set(cache_key, payload)

    def search_huggingface_gguf(self, query: str = "", *, page: int = 1, page_size: int = 12) -> Dict[str, Any]:
        if self._hf_api is None:
            raise RuntimeError("huggingface_hub is not installed")

        normalized_query = (query or "").strip()
        safe_page = max(1, int(page or 1))
        safe_page_size = max(1, min(30, int(page_size or 12)))
        cache_key = f"hf_search::{normalized_query.lower()}::{safe_page}::{safe_page_size}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        limit = safe_page * safe_page_size + 1
        models = list(
            self._hf_api.list_models(
                search=normalized_query or None,
                tags="gguf",
                gated=False,
                sort="downloads",
                direction=-1,
                limit=limit,
                token=_read_env_token(),
            )
        )
        has_more = len(models) > (safe_page * safe_page_size)
        start_index = (safe_page - 1) * safe_page_size
        page_items = models[start_index : start_index + safe_page_size]
        items: List[Dict[str, Any]] = []
        for model in page_items:
            tags = [tag for tag in list(model.tags or []) if isinstance(tag, str)]
            items.append(
                {
                    "source": "huggingface",
                    "id": model.id,
                    "title": model.id,
                    "summary": "",
                    "downloads": model.downloads or 0,
                    "likes": model.likes or 0,
                    "pipeline_tag": model.pipeline_tag or "",
                    "author": getattr(model, "author", "") or "",
                    "last_modified": str(getattr(model, "last_modified", "") or ""),
                    "tags": tags[:10],
                    "url": HUGGINGFACE_MODEL_URL.format(repo_id=model.id),
                }
            )

        payload = {
            "source": "huggingface",
            "query": normalized_query,
            "page": safe_page,
            "page_size": safe_page_size,
            "items": items,
            "has_more": has_more,
        }
        return self._cache_set(cache_key, payload)

    def get_huggingface_repo_details(self, repo_id: str) -> Dict[str, Any]:
        if self._hf_api is None:
            raise RuntimeError("huggingface_hub is not installed")

        normalized_repo = (repo_id or "").strip().strip("/")
        if not normalized_repo:
            raise ValueError("Hugging Face repository id cannot be empty")

        cache_key = f"hf_detail::{normalized_repo}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        info = self._hf_api.model_info(
            normalized_repo,
            files_metadata=True,
            token=_read_env_token(),
        )
        files = []
        for sibling in list(info.siblings or []):
            filename = (sibling.rfilename or "").strip()
            if not filename.lower().endswith(".gguf"):
                continue
            quant = extract_hf_quantization(filename)
            size = sibling.size
            if size is None and getattr(sibling, "lfs", None) is not None:
                size = getattr(sibling.lfs, "size", None)
            files.append(
                {
                    "filename": filename,
                    "quantization": quant or Path(filename).stem,
                    "size": size or 0,
                    "size_human": _format_bytes(size or 0),
                    "download_url": f"https://huggingface.co/{normalized_repo}/resolve/main/{filename}",
                }
            )

        files.sort(key=lambda item: (_quantization_sort_key(item["quantization"]), item["filename"]))
        default_quantization = files[0]["quantization"] if files else ""
        for preferred in _QUANTIZATION_PREFERENCE:
            match = next((file for file in files if str(file["quantization"]).upper() == preferred), None)
            if match is not None:
                default_quantization = match["quantization"]
                break

        payload = {
            "source": "huggingface",
            "id": normalized_repo,
            "title": normalized_repo,
            "summary": str(getattr(info, "card_data", None) or ""),
            "url": HUGGINGFACE_MODEL_URL.format(repo_id=normalized_repo),
            "downloads": info.downloads or 0,
            "likes": info.likes or 0,
            "last_modified": str(info.last_modified or ""),
            "gguf_files": files,
            "default_quantization": default_quantization,
            "default_reference": build_hf_ollama_reference(normalized_repo, default_quantization) if default_quantization else build_hf_ollama_reference(normalized_repo),
        }
        return self._cache_set(cache_key, payload)

    def start_pull_job(self, *, source: str, reference: str, title: str, api_base: str) -> Dict[str, Any]:
        normalized_source = (source or "").strip().lower()
        normalized_reference = (reference or "").strip()
        if normalized_source not in {"ollama", "huggingface"}:
            raise ValueError("Unsupported model source")
        if not normalized_reference:
            raise ValueError("Model reference cannot be empty")

        job_title = (title or normalized_reference).strip()
        with self._jobs_lock:
            for job in self._jobs.values():
                if job.model_name == normalized_reference and job.status in {"queued", "running"}:
                    return job.to_dict()

            job = DownloadJob(
                job_id=uuid.uuid4().hex,
                source=normalized_source,
                reference=normalized_reference,
                model_name=normalized_reference,
                title=job_title,
            )
            self._jobs[job.job_id] = job

        worker = threading.Thread(
            target=self._pull_worker,
            kwargs={"job_id": job.job_id, "api_base": _normalize_api_base(api_base)},
            name=f"autoyou-model-pull-{job.job_id[:8]}",
            daemon=True,
        )
        worker.start()
        return job.to_dict()

    def _pull_worker(self, *, job_id: str, api_base: str) -> None:
        job = self._get_job(job_id)
        if job is None:
            return

        self._update_job(job_id, status="running", message="Connecting to Ollama...")

        if ollama is None:
            self._update_job(job_id, status="failed", error="Ollama Python client is not installed")
            return

        try:
            client_kwargs = {"host": api_base}
            try:
                client_kwargs["timeout"] = httpx.Timeout(300.0, connect=60.0)
            except Exception:
                client_kwargs["timeout"] = 300.0

            max_retries = 3
            for attempt in range(1, max_retries + 1):
                try:
                    client = ollama.Client(**client_kwargs)
                    for progress in client.pull(job.model_name, stream=True):
                        total = int(progress.total or 0)
                        completed = int(progress.completed or 0)
                        percent = 0.0
                        if total > 0:
                            percent = max(0.0, min(1.0, completed / total))
                        message = (progress.status or "Downloading").strip() or "Downloading"
                        self._update_job(
                            job_id,
                            status="running",
                            message=message,
                            completed_bytes=completed,
                            total_bytes=total,
                            digest=str(progress.digest or ""),
                            progress=percent,
                        )
                    break
                except Exception as stream_err:
                    err_msg = str(stream_err)
                    if attempt < max_retries and ("timeout" in err_msg.lower() or "handshake" in err_msg.lower()):
                        LOGGER.warning(
                            "Ollama pull attempt %d/%d for %s failed with transient error (%s); retrying...",
                            attempt, max_retries, job.model_name, err_msg
                        )
                        time.sleep(2.0 * attempt)
                    else:
                        raise stream_err

            self._update_job(
                job_id,
                status="completed",
                message="Download complete",
                progress=1.0,
                finished_at=_now_ts(),
            )
            self._cache_set(f"local_models::{api_base}", None)
            with self._cache_lock:
                self._cache.pop(f"local_models::{api_base}", None)
        except Exception as exc:
            LOGGER.warning("Model download failed for %s: %s", job.model_name, exc)
            self._update_job(
                job_id,
                status="failed",
                error=str(exc),
                message="Download failed",
                finished_at=_now_ts(),
            )

    def delete_local_model(self, reference: str, api_base: str) -> Dict[str, Any]:
        """Delete an installed local Ollama model and invalidate the cache.

        Args:
            reference: The installed model name/tag (e.g. ``llama3.2:latest``).
            api_base: The Ollama API base URL.

        Returns:
            dict with ``status`` ("success"|"error") and a human ``message``.
        """
        normalized_reference = (reference or "").strip()
        if not normalized_reference:
            return {"status": "error", "message": "Model reference cannot be empty"}
        if ollama is None:
            return {"status": "error", "message": "Ollama Python client is not installed"}

        normalized_base = _normalize_api_base(api_base)
        installed = {model["name"] for model in self.list_local_models(normalized_base)}
        if normalized_reference not in installed:
            # Refresh once in case the cached listing is stale before giving up.
            with self._cache_lock:
                self._cache.pop(f"local_models::{normalized_base}", None)
            installed = {model["name"] for model in self.list_local_models(normalized_base)}
            if normalized_reference not in installed:
                return {
                    "status": "error",
                    "message": f"Model '{normalized_reference}' is not installed locally.",
                }

        try:
            ollama.Client(host=normalized_base).delete(normalized_reference)
        except Exception as exc:
            LOGGER.warning("Failed to delete local Ollama model %s: %s", normalized_reference, exc)
            return {
                "status": "error",
                "message": f"Failed to delete '{normalized_reference}': {exc}",
            }

        with self._cache_lock:
            self._cache.pop(f"local_models::{normalized_base}", None)
        return {
            "status": "success",
            "message": f"Deleted local model '{normalized_reference}'.",
            "model": normalized_reference,
        }

    def _get_job(self, job_id: str) -> Optional[DownloadJob]:
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

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self._get_job(job_id)
        return job.to_dict() if job is not None else None

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda item: item.updated_at, reverse=True)
        return [job.to_dict() for job in jobs[:10]]
