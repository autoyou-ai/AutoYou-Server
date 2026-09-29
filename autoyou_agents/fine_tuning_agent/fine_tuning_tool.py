# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-7aa8b418bc4ca938aa55663f

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import importlib.util
import inspect
import io
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from shared.platform_runtime import (
    find_bundled_browser_executable,
    get_node_command,
    get_node_service_dir,
    get_service_data_dir,
)
from shared.secure_storage import (
    SecureStorageError,
    append_secure_file,
    read_secure_file,
    write_secure_file,
)

from .training_data import (
    DatasetBuildResult,
    build_dataset_from_folder,
    build_dataset_from_bytes,
    build_samples_from_messages,
    parse_live_telegram_user_log,
    parse_live_whatsapp_log,
    parse_whatsapp_history_dump,
    split_samples,
    write_jsonl,
)
from .training_runner import (
    DEFAULT_OLLAMA_BASE_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TRAINING_MODEL,
    DEFAULT_VISION_MAX_PIXELS,
    DEFAULT_VISION_MIN_PIXELS,
    _accelerator_backend,
)

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-7aa8b418bc4ca938aa55663f"


AGENT_PACKAGE_NAME = "fine_tuning_agent"
DATA_DIR = get_service_data_dir(AGENT_PACKAGE_NAME, anchor=__file__)
WORKSPACE_DIR = DATA_DIR / "workspace"
# from __debug_provenance_h__ import revenue
DATASETS_DIR = WORKSPACE_DIR / "datasets"
RUNS_DIR = WORKSPACE_DIR / "runs"
DUMPS_DIR = WORKSPACE_DIR / "dumps"
HF_CACHE_DIR = WORKSPACE_DIR / "hf_cache"
TOOLS_DIR = WORKSPACE_DIR / "tools"
LLAMA_CPP_DIR = Path(os.getenv("AUTOYOU_FINE_TUNING_LLAMA_CPP_DIR", str(TOOLS_DIR / "llama.cpp")))
DB_PATH = DATA_DIR / "fine_tuning_history.db"

# Zero means no static byte ceiling. Operators can still set a positive value
# for constrained disks; collection itself is governed by source/time budgets.
MAX_UPLOAD_BYTES = int(os.getenv("AUTOYOU_FINE_TUNING_MAX_UPLOAD_BYTES", "0"))
MAX_FOLDER_IMPORT_BYTES = int(os.getenv("AUTOYOU_FINE_TUNING_MAX_FOLDER_IMPORT_BYTES", "0"))
MAX_DATA_COLLECTOR_IMPORT_BYTES = int(os.getenv("AUTOYOU_FINE_TUNING_MAX_DATA_COLLECTOR_IMPORT_BYTES", "0"))
DEFAULT_MAX_STEPS = int(os.getenv("AUTOYOU_FINE_TUNING_MAX_STEPS", "80"))
DEFAULT_MAX_RUNS = int(os.getenv("AUTOYOU_FINE_TUNING_MAX_RUNS", "8"))
DEFAULT_MIN_FREE_GB = float(os.getenv("AUTOYOU_FINE_TUNING_MIN_FREE_GB", "5"))
_CANCEL_GRACE_SECONDS = 10

#: Clock seams for the trainer supervision loop.
#:
#: The loop below has to be driven by a test with a fake clock, and the obvious
#: way to do that - ``monkeypatch.setattr(module.time, "monotonic", ...)`` -
#: patches the *shared* ``time`` module for the whole process. Any asyncio loop
#: running in another thread then calls the fake on every tick: it drains a
#: finite iterator and the loop dies with ``StopIteration``, in a test that has
#: nothing to do with training.
#:
#: These aliases give the loop something a test can replace without touching
#: anything else in the process.
_monotonic = time.monotonic
_sleep = time.sleep

_JOB_LOCK = threading.Lock()
_DUMP_LOCK = threading.Lock()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_IMAGE_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})


def _training_runner_command() -> List[str]:
    """Run the protected runner through AutoYou.exe when this is a bundle."""

    executable = Path(sys.executable).resolve()
    if (executable.parent / "runtime_modules").is_dir():
        return [str(executable), "--run-fine-tuning-runner"]
    return [sys.executable, "-m", "autoyou_agents.fine_tuning_agent.training_runner"]


class _LoopbackNoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep validated loopback handoffs on their original local endpoint."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _open_loopback_data_collector(request: Any, *, timeout: float):
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _LoopbackNoRedirect(),
    ).open(request, timeout=timeout)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                original_filename TEXT,
                train_path TEXT NOT NULL,
                eval_path TEXT,
                sample_count INTEGER NOT NULL,
                train_sample_count INTEGER NOT NULL,
                eval_sample_count INTEGER NOT NULL,
                message_count INTEGER NOT NULL,
                assistant_message_count INTEGER NOT NULL,
                warnings_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS training_jobs (
                id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                status TEXT NOT NULL,
                progress_percent REAL NOT NULL,
                eta_seconds INTEGER,
                title TEXT NOT NULL,
                model_name TEXT NOT NULL,
                training_model_id TEXT NOT NULL,
                ollama_base_model TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                ollama_dir TEXT NOT NULL,
                modelfile_path TEXT,
                log_path TEXT NOT NULL,
                return_code INTEGER,
                error_message TEXT,
                install_status TEXT,
                config_json TEXT NOT NULL,
                FOREIGN KEY(dataset_id) REFERENCES datasets(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dump_jobs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                status TEXT NOT NULL,
                progress_percent REAL NOT NULL,
                eta_seconds INTEGER,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                output_path TEXT NOT NULL,
                log_path TEXT NOT NULL,
                dataset_id TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                chat_count INTEGER NOT NULL DEFAULT 0,
                return_code INTEGER,
                error_message TEXT,
                config_json TEXT NOT NULL,
                FOREIGN KEY(dataset_id) REFERENCES datasets(id)
            )
            """
        )
        conn.execute(
            """
            UPDATE training_jobs
               SET status = 'interrupted',
                   updated_at = ?,
                   completed_at = ?,
                   error_message = COALESCE(error_message, 'Training was interrupted by a runtime restart.')
             WHERE status IN ('queued', 'running', 'cancelling')
            """,
            (_now(), _now()),
        )
        conn.execute(
            """
            UPDATE dump_jobs
               SET status = 'interrupted',
                   updated_at = ?,
                   completed_at = ?,
                   error_message = COALESCE(error_message, 'WhatsApp history dump was interrupted by a runtime restart.')
             WHERE status IN ('queued', 'running')
            """,
            (_now(), _now()),
        )
        conn.commit()

init_db()

def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    for key in ("warnings_json", "metadata_json", "config_json"):
        if key in data:
            target_key = key[:-5] if key.endswith("_json") else key
            try:
                data[target_key] = json.loads(data.get(key) or "null")
            except Exception:
                data[target_key] = None
            data.pop(key, None)
    return data

def _safe_slug(value: str, *, default: str = "autoyou-whatsapp-persona") -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(value or "").strip().lower()).strip("-._:")
    slug = re.sub(r"-+", "-", slug)
    return slug[:80] or default

def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

def _ensure_workspace_child(path: Path) -> Path:
    resolved = Path(path).resolve()
    root = WORKSPACE_DIR.resolve()
    if resolved == root or root in resolved.parents:
        return resolved
    raise ValueError(f"Refusing to operate outside fine-tuning workspace: {resolved}")

def _safe_rmtree(path: Path) -> None:
    resolved = _ensure_workspace_child(path)
    if resolved.exists():
        shutil.rmtree(resolved)

def _insert_dataset(
    *,
    dataset_id: str,
    source_type: str,
    title: str,
    original_filename: Optional[str],
    train_path: Path,
    eval_path: Optional[Path],
    sample_count: int,
    train_sample_count: int,
    eval_sample_count: int,
    message_count: int,
    assistant_message_count: int,
    warnings: Iterable[str],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    created_at = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO datasets (
                id, created_at, source_type, title, original_filename,
                train_path, eval_path, sample_count, train_sample_count,
                eval_sample_count, message_count, assistant_message_count,
                warnings_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset_id,
                created_at,
                source_type,
                title,
                original_filename,
                str(train_path),
                str(eval_path) if eval_path else None,
                sample_count,
                train_sample_count,
                eval_sample_count,
                message_count,
                assistant_message_count,
                json.dumps(list(warnings)),
                json.dumps(metadata),
            ),
        )
        conn.commit()
    dataset = get_dataset(dataset_id)
    return dataset or {"id": dataset_id, "sample_count": sample_count}


def _image_asset_name(value: Any) -> Optional[str]:
    """Return a safe bundled image name, never a user-controlled path."""
    name = str(value or "").strip().replace("\\", "/")
    if not name or "/" in name or ":" in name or _CONTROL_CHAR_RE.search(name):
        return None
    if Path(name).suffix.lower() not in _IMAGE_SUFFIXES:
        return None
    return name


def _store_dataset_images(
    *,
    dataset_dir: Path,
    samples: Iterable[Dict[str, Any]],
    image_files: Optional[Dict[str, bytes]],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Seal referenced image assets beside the private JSONL manifest."""
    rows = [dict(sample) for sample in samples]
    referenced = {
        image_name
        for sample in rows
        for raw_name in (sample.get("images") or [])
        if (image_name := _image_asset_name(raw_name))
    }
    if not referenced:
        return rows, {"image_count": 0}

    bundled: Dict[str, bytes] = {}
    for raw_name, payload in (image_files or {}).items():
        image_name = _image_asset_name(raw_name)
        if not image_name:
            raise ValueError("Image bundles support only PNG, JPEG, and WebP file names.")
        if image_name in bundled:
            raise ValueError(f"Image bundle contains duplicate file name: {image_name}")
        bundled[image_name] = bytes(payload)

    missing = sorted(referenced - set(bundled))
    if missing:
        raise ValueError("Each JSONL image reference must match an image file in the same upload.")

    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("Image datasets require Pillow in the Fine Tuning runtime.") from exc

    image_dir = dataset_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    format_counts: Dict[str, int] = {}
    for image_name in sorted(referenced):
        payload = bundled[image_name]
        try:
            with Image.open(io.BytesIO(payload)) as opened:
                opened.verify()
            with Image.open(io.BytesIO(payload)) as opened:
                image_format = str(opened.format or "").upper()
                if image_format not in _IMAGE_FORMATS:
                    raise ValueError(f"Unsupported image format: {image_format or 'unknown'}")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Image bundle contains an unreadable image: {image_name}") from exc
        write_secure_file(image_dir / image_name, payload)
        format_counts[image_format] = format_counts.get(image_format, 0) + 1

    for sample in rows:
        image_names = [
            image_name
            for raw_name in (sample.get("images") or [])
            if (image_name := _image_asset_name(raw_name))
        ]
        if image_names:
            sample["images"] = [f"images/{image_name}" for image_name in image_names]

    return rows, {
        "image_count": len(referenced),
        "image_format_counts": format_counts,
        "unreferenced_image_count": len(set(bundled) - referenced),
    }

def _persist_dataset(
    *,
    result: DatasetBuildResult,
    source_type: str,
    title: str,
    original_filename: Optional[str] = None,
    raw_bytes: Optional[bytes] = None,
    metadata: Optional[Dict[str, Any]] = None,
    image_files: Optional[Dict[str, bytes]] = None,
) -> Dict[str, Any]:
    if not result.samples:
        return {
            "status": "error",
            "message": "No trainable assistant/owner samples were found.",
            "warnings": result.warnings,
            "message_count": result.message_count,
            "assistant_message_count": result.assistant_message_count,
        }
    dataset_id = _new_id("ds")
    dataset_dir = DATASETS_DIR / dataset_id
    dataset_dir.mkdir(parents=True, exist_ok=False)
    try:
        if raw_bytes is not None and original_filename:
            safe_filename = Path(original_filename).name or "upload.dat"
            write_secure_file(dataset_dir / safe_filename, raw_bytes)
        samples, image_metadata = _store_dataset_images(
            dataset_dir=dataset_dir,
            samples=result.samples,
            image_files=image_files,
        )
    except (OSError, ValueError, SecureStorageError) as exc:
        _safe_rmtree(dataset_dir)
        return {"status": "error", "message": str(exc), "warnings": result.warnings}

    train_samples, eval_samples = split_samples(samples)
    train_path = dataset_dir / "train.jsonl"
    eval_path = dataset_dir / "eval.jsonl"
    train_count = write_jsonl(train_path, train_samples)
    eval_count = write_jsonl(eval_path, eval_samples) if eval_samples else 0
    dataset = _insert_dataset(
        dataset_id=dataset_id,
        source_type=source_type,
        title=title,
        original_filename=original_filename,
        train_path=train_path,
        eval_path=eval_path if eval_count else None,
        sample_count=len(samples),
        train_sample_count=train_count,
        eval_sample_count=eval_count,
        message_count=result.message_count,
        assistant_message_count=result.assistant_message_count,
        warnings=result.warnings,
        metadata={**(metadata or {}), **image_metadata},
    )
    return {"status": "success", "dataset": dataset}

def create_dataset_from_upload(
    *,
    filename: str,
    data: bytes,
    me_name: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    if not filename:
        return {"status": "error", "message": "filename is required."}
    if len(data or b"") <= 0:
        return {"status": "error", "message": "Uploaded file is empty."}
    if MAX_UPLOAD_BYTES > 0 and len(data) > MAX_UPLOAD_BYTES:
        return {
            "status": "error",
            "message": f"Upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        }
    result = build_dataset_from_bytes(data, filename=filename, me_name=me_name)
    return _persist_dataset(
        result=result,
        source_type="upload",
        title=title or Path(filename).stem or "Uploaded message dataset",
        original_filename=Path(filename).name,
        raw_bytes=data,
        metadata={"me_name_provided": bool(str(me_name or "").strip())},
    )


def create_dataset_from_uploads(
    *,
    files: Iterable[tuple[str, bytes]],
    me_name: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Prepare one dataset from browser-dropped files without retaining raw copies."""
    samples: List[Dict[str, Any]] = []
    warnings: List[str] = []
    file_names: List[str] = []
    image_files: Dict[str, bytes] = {}
    message_count = 0
    assistant_message_count = 0
    for original_name, data in files:
        filename = Path(str(original_name or "")).name or "upload.dat"
        file_names.append(filename)
        if not data:
            warnings.append(f"{filename}: uploaded file is empty.")
            continue
        if MAX_UPLOAD_BYTES > 0 and len(data) > MAX_UPLOAD_BYTES:
            warnings.append(f"{filename}: upload exceeds the configured size limit.")
            continue
        if Path(filename).suffix.lower() in _IMAGE_SUFFIXES:
            if filename in image_files:
                return {"status": "error", "message": f"Image bundle contains duplicate file name: {filename}"}
            image_files[filename] = data
            continue
        result = build_dataset_from_bytes(data, filename=filename, me_name=me_name)
        samples.extend(result.samples)
        message_count += result.message_count
        assistant_message_count += result.assistant_message_count
        warnings.extend(f"{filename}: {warning}" for warning in result.warnings)
    result = DatasetBuildResult(
        samples=samples,
        message_count=message_count,
        assistant_message_count=assistant_message_count,
        warnings=warnings,
    )
    return _persist_dataset(
        result=result,
        source_type="uploads",
        title=title or "Dropped data files",
        metadata={
            "me_name_provided": bool(str(me_name or "").strip()),
            "uploaded_file_count": len(file_names),
            "uploaded_file_names": file_names,
        },
        image_files=image_files,
    )


def create_dataset_from_folder(
    *,
    folder_path: str,
    me_name: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Prepare a dataset from a local folder without copying its raw source files."""
    if not str(folder_path or "").strip():
        return {"status": "error", "message": "folder_path is required."}
    root = Path(folder_path).expanduser().resolve()
    result = build_dataset_from_folder(root, me_name=me_name, max_total_bytes=MAX_FOLDER_IMPORT_BYTES)
    if not result.samples:
        return {
            "status": "error",
            "message": "No trainable samples were found in that folder.",
            "warnings": result.warnings,
        }
    return _persist_dataset(
        result=result,
        source_type="folder",
        title=title or root.name or "Folder dataset",
        original_filename=root.name,
        metadata={"source_folder_name": root.name, "me_name_provided": bool(str(me_name or "").strip())},
    )


def _normalize_data_collector_url(value: Optional[str] = None) -> str:
    raw = str(value or os.getenv("AUTOYOU_DATA_COLLECTOR_URL", "http://127.0.0.1:18067")).strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "http" or host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Data Collector must use an http:// loopback URL.")
    port = f":{parsed.port}" if parsed.port else ""
    authority = f"[{host}]" if host == "::1" else host
    return f"http://{authority}{port}"


def get_data_collector_status(url: Optional[str] = None) -> Dict[str, Any]:
    """Detect the independent Data Collector service without exposing its data."""
    try:
        base_url = _normalize_data_collector_url(url)
        with _open_loopback_data_collector(f"{base_url}/health", timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {
            "status": "success",
            "available": bool(payload.get("loopback_handoff")),
            "url": base_url,
            "message": "Data Collector is ready for a one-time local handoff." if payload.get("loopback_handoff") else "A collector answered but does not support local handoff.",
        }
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return {
            "status": "success",
            "available": False,
            "url": str(url or os.getenv("AUTOYOU_DATA_COLLECTOR_URL", "http://127.0.0.1:18067")),
            "message": "Data Collector is not running on loopback.",
        }


def create_dataset_from_datacollector_handoff(
    *,
    handoff_code: str,
    collector_url: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Consume one loopback-only collector export code and persist the dataset."""
    code = str(handoff_code or "").strip()
    if not code:
        return {"status": "error", "message": "A one-time Data Collector handoff code is required."}
    try:
        base_url = _normalize_data_collector_url(collector_url)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    dataset_id = _new_id("ds")
    dataset_dir = DATASETS_DIR / dataset_id
    transfer_path: Optional[Path] = None
    try:
        dataset_dir.mkdir(parents=True, exist_ok=False)
        request = urllib.request.Request(
            f"{base_url}/api/handoffs/consume",
            data=json.dumps({"code": code}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        transfer_path = dataset_dir / ".collector-handoff.zip"
        received = 0
        with _open_loopback_data_collector(request, timeout=60) as response, transfer_path.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if MAX_DATA_COLLECTOR_IMPORT_BYTES and received > MAX_DATA_COLLECTOR_IMPORT_BYTES:
                    raise ValueError("Data Collector export exceeds this agent's configured import size.")
                handle.write(chunk)
        with zipfile.ZipFile(transfer_path) as archive:
            expected = {"manifest.json", "train.jsonl", "eval.jsonl"}
            if any(name not in expected for name in archive.namelist()) or "manifest.json" not in archive.namelist() or "train.jsonl" not in archive.namelist():
                raise ValueError("Data Collector returned an invalid training export.")
            for name in archive.namelist():
                if MAX_DATA_COLLECTOR_IMPORT_BYTES and archive.getinfo(name).file_size > MAX_DATA_COLLECTOR_IMPORT_BYTES:
                    raise ValueError("Data Collector export contains an oversized file.")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            train_copy = dataset_dir / "train.jsonl"
            with archive.open("train.jsonl") as source:
                write_secure_file(train_copy, source.read())
            eval_copy: Optional[Path] = None
            if "eval.jsonl" in archive.namelist() and archive.getinfo("eval.jsonl").file_size:
                eval_copy = dataset_dir / "eval.jsonl"
                with archive.open("eval.jsonl") as source:
                    write_secure_file(eval_copy, source.read())
        train_count = int(manifest.get("train_sample_count") or 0) or _count_jsonl_rows(train_copy)
        eval_count = int(manifest.get("eval_sample_count") or 0) or (_count_jsonl_rows(eval_copy) if eval_copy else 0)
        sample_count = int(manifest.get("sample_count") or 0) or (train_count + eval_count)
        message_count = int(manifest.get("message_count") or 0) or sample_count
        assistant_count = int(manifest.get("assistant_message_count") or 0) or sample_count
        dataset = _insert_dataset(
            dataset_id=dataset_id,
            source_type="data_collector",
            title=title or str(manifest.get("title") or "Data Collector export"),
            original_filename="data-collector-export.zip",
            train_path=train_copy,
            eval_path=eval_copy,
            sample_count=sample_count,
            train_sample_count=train_count,
            eval_sample_count=eval_count,
            message_count=message_count,
            assistant_message_count=assistant_count,
            warnings=[],
            metadata={"export_id": manifest.get("id"), "transfer": "loopback_one_time_handoff"},
        )
        return {"status": "success", "dataset": dataset, "export": {"id": manifest.get("id"), "title": manifest.get("title")}}
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        if dataset_dir.exists():
            _safe_rmtree(dataset_dir)
        message = "Data Collector handoff could not be imported."
        if isinstance(exc, ValueError):
            message = str(exc)
        return {"status": "error", "message": message}
    finally:
        if transfer_path is not None:
            try:
                transfer_path.unlink()
            except OSError:
                pass


def _data_collector_root() -> Path:
    configured = str(os.getenv("AUTOYOU_DATA_COLLECTOR_ROOT", "")).strip()
    if configured:
        return Path(configured).expanduser()
    return get_service_data_dir("data_collector_agent", anchor=__file__)


def _data_collector_training_exports_dir() -> Path:
    return _data_collector_root() / "collected_context" / "index_unified" / "exports_for_training"


def _ensure_data_collector_child(path: Path) -> Path:
    resolved = Path(path).resolve()
    root = _data_collector_root().resolve()
    if resolved == root or root in resolved.parents:
        return resolved
    raise ValueError(f"Refusing to operate outside DataCollector root: {resolved}")


def _count_jsonl_rows(path: Path, *, migrate_plaintext: bool = True) -> int:
    return sum(1 for line in read_secure_file(path, migrate_plaintext=migrate_plaintext).decode("utf-8-sig").splitlines() if line.strip())


def list_datacollector_training_exports(limit: int = 20) -> Dict[str, Any]:
    exports_dir = _data_collector_training_exports_dir()
    if not exports_dir.exists():
        return {"status": "success", "exports": [], "count": 0, "root": str(exports_dir)}
    manifests: List[Dict[str, Any]] = []
    for manifest_path in exports_dir.glob("*/manifest.json"):
        try:
            manifest = json.loads(read_secure_file(manifest_path, migrate_plaintext=False).decode("utf-8"))
        except Exception:
            continue
        manifest["manifest_path"] = str(manifest_path)
        manifests.append(manifest)
    manifests.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    trimmed = manifests[: max(1, min(int(limit or 20), 200))]
    return {"status": "success", "exports": trimmed, "count": len(trimmed), "root": str(exports_dir)}


def create_dataset_from_datacollector_export(
    *,
    export_id: Optional[str] = None,
    manifest_path: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    exports_dir = _data_collector_training_exports_dir()
    if manifest_path:
        manifest_file = _ensure_data_collector_child(Path(manifest_path))
    elif export_id:
        manifest_file = exports_dir / str(export_id).strip() / "manifest.json"
    else:
        return {"status": "error", "message": "export_id or manifest_path is required."}
    if not manifest_file.exists():
        return {"status": "error", "message": f"DataCollector export manifest was not found: {manifest_file}"}
    try:
        manifest = json.loads(read_secure_file(manifest_file, migrate_plaintext=False).decode("utf-8"))
    except Exception as exc:
        return {"status": "error", "message": f"Failed to read DataCollector export manifest: {exc}"}

    train_path = manifest.get("train_path")
    if not train_path:
        return {"status": "error", "message": "DataCollector export manifest is missing train_path."}
    try:
        source_train = _ensure_data_collector_child(Path(train_path))
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    if not source_train.exists():
        return {"status": "error", "message": f"Training file was not found: {source_train}"}

    source_eval: Optional[Path] = None
    eval_path = manifest.get("eval_path")
    if eval_path:
        try:
            candidate = _ensure_data_collector_child(Path(eval_path))
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        if candidate.exists():
            source_eval = candidate

    dataset_id = _new_id("ds")
    dataset_dir = DATASETS_DIR / dataset_id
    dataset_dir.mkdir(parents=True, exist_ok=False)
    train_copy = dataset_dir / "train.jsonl"
    write_secure_file(train_copy, read_secure_file(source_train, migrate_plaintext=False))

    eval_copy: Optional[Path] = None
    eval_count = 0
    if source_eval is not None:
        eval_count = int(manifest.get("eval_sample_count") or 0) or _count_jsonl_rows(source_eval, migrate_plaintext=False)
        if eval_count > 0:
            eval_copy = dataset_dir / "eval.jsonl"
            write_secure_file(eval_copy, read_secure_file(source_eval, migrate_plaintext=False))

    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    write_secure_file(dataset_dir / "datacollector_manifest.json", manifest_bytes)

    train_count = int(manifest.get("train_sample_count") or 0) or _count_jsonl_rows(source_train, migrate_plaintext=False)
    sample_count = int(manifest.get("sample_count") or 0) or (train_count + eval_count)
    message_count = int(manifest.get("message_count") or 0) or (sample_count * 3)
    assistant_message_count = int(manifest.get("assistant_message_count") or 0) or sample_count
    dataset = _insert_dataset(
        dataset_id=dataset_id,
        source_type="datacollector",
        title=title or str(manifest.get("title") or export_id or "DataCollector chronology"),
        original_filename=source_train.name,
        train_path=train_copy,
        eval_path=eval_copy,
        sample_count=sample_count,
        train_sample_count=train_count,
        eval_sample_count=eval_count,
        message_count=message_count,
        assistant_message_count=assistant_message_count,
        warnings=[],
        metadata={
            "export_id": manifest.get("id"),
            "manifest_path": str(manifest_file),
            "filters": manifest.get("filters") or {},
            "autoyou_tuning_train_path": manifest.get("autoyou_tuning_train_path"),
            "autoyou_tuning_eval_path": manifest.get("autoyou_tuning_eval_path"),
        },
    )
    return {"status": "success", "dataset": dataset, "export": manifest}

def _runtime_server_state() -> Optional[Any]:
    for module_name in ("server", "__main__", "autoyou.server"):
        module = sys.modules.get(module_name)
        state = getattr(module, "STATE", None) if module is not None else None
        if state is not None:
            return state
    for module in list(sys.modules.values()):
        module_name = str(getattr(module, "__name__", "") or "")
        if module is None or not (module_name == "server" or module_name.startswith("autoyou")):
            continue
        try:
            state = getattr(module, "STATE", None)
        except Exception:
            continue
        if state is None:
            continue
        try:
            whatsapp_service = getattr(state, "whatsapp_service", None)
        except Exception:
            whatsapp_service = None
        if whatsapp_service is not None:
            return state
    return None

def _resolve_maybe_awaitable(value: Any, *, timeout_seconds: float = 5.0) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    result_box: Dict[str, Any] = {}

    def _runner() -> None:
        try:
            result_box["value"] = asyncio.run(value)
        except Exception as exc:
            result_box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True, name="fine-tuning-awaitable-status")
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        return None
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("value")

def _service_event_loop(service: Any) -> Optional[asyncio.AbstractEventLoop]:
    for attr_name in ("websocket_task", "node_output_task", "_restart_task", "_control_channel_recovery_task"):
        task = getattr(service, attr_name, None)
        if task is None or not hasattr(task, "get_loop"):
            continue
        try:
            loop = task.get_loop()
        except Exception:
            continue
        if loop is not None and not loop.is_closed():
            return loop
    return None

def _run_service_coroutine(service: Any, value: Any, *, timeout_seconds: float = 60.0) -> Any:
    if not inspect.isawaitable(value):
        return value
    loop = _service_event_loop(service)
    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(value, loop)
        return future.result(timeout=timeout_seconds)
    return _resolve_maybe_awaitable(value, timeout_seconds=timeout_seconds)

async def _probe_whatsapp_websocket_status_async(port: int = 8083, *, timeout_seconds: float = 5.0) -> Dict[str, Any]:
    try:
        import websockets
    except Exception as exc:
        return {"available": False, "status": {}, "message": f"WebSocket status probe is unavailable: {exc}"}

    uris = (
        f"ws://127.0.0.1:{int(port)}/",
        f"ws://[::1]:{int(port)}/",
        f"ws://localhost:{int(port)}/",
    )
    last_error = ""
    for uri in uris:
        try:
            return await _probe_whatsapp_websocket_status_uri(uri, timeout_seconds=timeout_seconds)
        except Exception as exc:
            last_error = str(exc)
    return {"available": False, "status": {}, "message": f"WhatsApp WebSocket bridge is not reachable: {last_error}"}

async def _probe_whatsapp_websocket_status_uri(uri: str, *, timeout_seconds: float = 5.0) -> Dict[str, Any]:
    import websockets

    try:
        async with websockets.connect(uri, max_size=None, close_timeout=1) as websocket:
            await websocket.send(json.dumps({"action": "get_status", "data": {"reason": "fine_tuning_status_probe"}}))
            deadline = time.monotonic() + max(1.0, float(timeout_seconds))
            status: Dict[str, Any] = {}
            current_state: Optional[str] = None
            while time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                raw_message = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                try:
                    payload = json.loads(raw_message)
                except Exception:
                    continue
                event = str(payload.get("event") or "")
                data = payload.get("data")
                if event == "status" and isinstance(data, str):
                    current_state = data
                if event == "status_response" and isinstance(data, dict):
                    status = {
                        "connected": bool(data.get("ready") or data.get("current_service_status") == "connected"),
                        "authenticated": bool(data.get("authenticated")),
                        "status": data.get("current_service_status") or current_state,
                        "state": data.get("web_state") or data.get("last_state"),
                        "websocket_connected": True,
                        "client_ready": bool(data.get("ready")),
                    }
                    break
            if not status:
                status = {
                    "connected": current_state == "connected",
                    "status": current_state or "unknown",
                    "websocket_connected": True,
                }
            return {"available": True, "status": status, "message": "WhatsApp WebSocket bridge is reachable."}
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

def _probe_whatsapp_websocket_status(port: int = 8083, *, timeout_seconds: float = 5.0) -> Dict[str, Any]:
    return _resolve_maybe_awaitable(
        _probe_whatsapp_websocket_status_async(port, timeout_seconds=timeout_seconds),
        timeout_seconds=timeout_seconds + 1,
    )

def get_live_whatsapp_snapshot() -> Dict[str, Any]:
    state = _runtime_server_state()
    service = getattr(state, "whatsapp_service", None) if state is not None else None
    if service is None:
        probe = _probe_whatsapp_websocket_status()
        if probe.get("available"):
            return {
                "available": True,
                "message_count": 0,
                "status": probe.get("status", {}),
                "message": "WhatsApp WebSocket bridge is reachable; in-process message log is not attached.",
            }
        return {
            "available": False,
            "message_count": 0,
            "status": {},
            "message": "WhatsApp service is not available in this runtime process.",
        }
    raw_log: List[Dict[str, Any]] = []
    try:
        raw_log = list(service.get_message_log())
    except Exception:
        raw_log = []
    status: Dict[str, Any] = {}
    try:
        raw_status = _run_service_coroutine(service, service.get_status(), timeout_seconds=8)
        if isinstance(raw_status, dict):
            status = {
                key: raw_status.get(key)
                for key in (
                    "connected",
                    "authenticated",
                    "status",
                    "state",
                    "websocket_connected",
                    "client_ready",
                    "last_error",
                )
                if key in raw_status
            }
    except Exception as exc:
        status = {"status_error": str(exc)}
    if not status.get("websocket_connected") or status.get("status") not in {"connected", "authenticated"}:
        probe = _probe_whatsapp_websocket_status(port=int(getattr(service, "websocket_port", 8083) or 8083))
        probe_status = probe.get("status", {}) if probe.get("available") else {}
        if probe_status.get("websocket_connected"):
            status.update(probe_status)
    return {
        "available": True,
        "message_count": len(raw_log),
        "status": status,
        "message": "WhatsApp runtime log is available.",
    }

def create_dataset_from_live_whatsapp(
    *,
    me_name: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    state = _runtime_server_state()
    service = getattr(state, "whatsapp_service", None) if state is not None else None
    if service is None:
        return {"status": "error", "message": "WhatsApp service is not available in this runtime process."}
    try:
        raw_log = list(service.get_message_log())
    except Exception as exc:
        return {"status": "error", "message": f"Failed to read WhatsApp message log: {exc}"}
    messages = parse_live_whatsapp_log(raw_log)
    result = build_samples_from_messages(messages, me_name=me_name)
    return _persist_dataset(
        result=result,
        source_type="whatsapp_live",
        title=title or "Connected WhatsApp runtime log",
        original_filename=None,
        raw_bytes=None,
        metadata={"runtime_log_messages": len(raw_log)},
    )


_TELEGRAM_USER_SAFE_STATES = {
    "authorized",
    "connecting",
    "connected",
    "disconnected",
    "error",
    "needs_password",
    "ready",
}


def _runtime_telegram_user_service() -> Optional[Any]:
    state = _runtime_server_state()
    return getattr(state, "telegram_user_service", None) if state is not None else None


def _telegram_user_runtime_status(service: Any) -> Dict[str, Any]:
    get_status = getattr(service, "get_status", None)
    if not callable(get_status):
        return {}
    try:
        value = _run_service_coroutine(service, get_status(), timeout_seconds=8)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _telegram_user_consent_granted(status: Dict[str, Any]) -> bool:
    return status.get("owner_scoped") is True and status.get("training_export_consent") is True


def _safe_telegram_user_status(status: Dict[str, Any]) -> Dict[str, Any]:
    raw_state = str(status.get("status") or status.get("state") or "").strip().lower()
    return {
        "connected": status.get("connected") is True,
        "authorized": status.get("authorized") is True,
        "ready": status.get("ready") is True,
        "status": raw_state if raw_state in _TELEGRAM_USER_SAFE_STATES else "unknown",
        "owner_scoped": status.get("owner_scoped") is True,
        "training_export_consent": status.get("training_export_consent") is True,
    }


def _telegram_user_unavailable_message(status: Dict[str, Any]) -> str:
    if status.get("owner_scoped") is not True:
        return "Telegram Saved Messages is unavailable until the connected account is confirmed as owner-only."
    if status.get("training_export_consent") is not True:
        return "Telegram Saved Messages is unavailable until Saved Messages training consent is enabled."
    return "Telegram Saved Messages live log is unavailable."


def _read_telegram_user_live_log(service: Any) -> Optional[List[Dict[str, Any]]]:
    get_message_log = getattr(service, "get_message_log", None)
    if not callable(get_message_log):
        return None
    try:
        value = _run_service_coroutine(service, get_message_log(), timeout_seconds=8)
    except Exception:
        return None
    # Refuse arbitrary iterators so this path cannot become a dialog/history reader.
    if not isinstance(value, (list, tuple)):
        return None
    return [item for item in value if isinstance(item, dict)]


def get_live_telegram_user_snapshot() -> Dict[str, Any]:
    service = _runtime_telegram_user_service()
    if service is None:
        return {
            "available": False,
            "message_count": 0,
            "status": {},
            "message": "Telegram Saved Messages is not connected in this runtime process.",
        }
    runtime_status = _telegram_user_runtime_status(service)
    safe_status = _safe_telegram_user_status(runtime_status)
    if not _telegram_user_consent_granted(runtime_status):
        return {
            "available": False,
            "message_count": 0,
            "status": safe_status,
            "message": _telegram_user_unavailable_message(runtime_status),
        }
    live_log = _read_telegram_user_live_log(service)
    if live_log is None:
        return {
            "available": False,
            "message_count": 0,
            "status": safe_status,
            "message": "Telegram Saved Messages live log is unavailable.",
        }
    return {
        "available": True,
        "message_count": sum(
            1
            for item in live_log
            if item.get("saved_messages") is True or item.get("is_saved_messages") is True
        ),
        "status": safe_status,
        "message": "Telegram Saved Messages live log is available.",
    }


def create_dataset_from_live_telegram_user(*, title: Optional[str] = None) -> Dict[str, Any]:
    """Create a dataset from the service's already-captured Saved Messages log only."""
    service = _runtime_telegram_user_service()
    if service is None:
        return {"status": "error", "message": "Telegram Saved Messages is not connected in this runtime process."}
    runtime_status = _telegram_user_runtime_status(service)
    if not _telegram_user_consent_granted(runtime_status):
        return {"status": "error", "message": _telegram_user_unavailable_message(runtime_status)}
    live_log = _read_telegram_user_live_log(service)
    if live_log is None:
        return {"status": "error", "message": "Telegram Saved Messages live log is unavailable."}
    result = build_samples_from_messages(parse_live_telegram_user_log(live_log))
    return _persist_dataset(
        result=result,
        source_type="telegram_user_live",
        title=title or "Telegram Saved Messages live log",
        metadata={
            "source_scope": "telegram_saved_messages",
            "live_log_only": True,
            "owner_scoped": True,
            "training_export_consent": True,
        },
    )

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _whatsapp_history_worker_path() -> Path:
    return _repo_root() / "autoyou_agents" / "data_collector_agent" / "whatsapp_history_dump.mjs"


def _whatsapp_service_anchor() -> Path:
    return _repo_root() / "whatsapp_service.py"

def _runtime_whatsapp_service() -> Optional[Any]:
    state = _runtime_server_state()
    return getattr(state, "whatsapp_service", None) if state is not None else None

def _command_available(command: str) -> bool:
    value = str(command or "").strip()
    if not value:
        return False
    if os.path.sep in value or (os.path.altsep and os.path.altsep in value):
        return Path(value).is_file()
    return shutil.which(value) is not None

def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))

def _tail_file(path: Path, *, lines: int = 20) -> str:
    try:
        raw_lines = read_secure_file(path).decode("utf-8", errors="replace").splitlines()
    except SecureStorageError:
        raise
    except OSError:
        return ""
    return "\n".join(raw_lines[-max(1, min(lines, 200)):])

def _clean_subprocess_output(*parts: str) -> str:
    raw = "\n".join(str(part or "") for part in parts if part)
    text = _ANSI_ESCAPE_RE.sub("", raw).replace("\r", "\n")
    text = _CONTROL_CHAR_RE.sub("", text)
    lines = []
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned and re.search(r"[A-Za-z0-9]", cleaned):
            lines.append(cleaned)
    return "\n".join(lines[-40:]).strip()

def _append_job_log(log_path: Path, message: str) -> None:
    try:
        append_secure_file(log_path, (str(message).rstrip() + "\n").encode("utf-8", errors="replace"))
    except SecureStorageError:
        raise
    except OSError:
        pass


def _start_logged_process(
    command: List[str],
    *,
    cwd: str,
    env: Dict[str, str],
    log_path: Path,
    creationflags: int = 0,
) -> tuple[subprocess.Popen[str], threading.Thread, List[BaseException]]:
    """Run a worker with stdout captured into the protected log boundary."""

    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        bufsize=1,
        creationflags=creationflags,
    )
    errors: List[BaseException] = []

    def _pump() -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                _append_job_log(log_path, line.rstrip("\r\n"))
        except BaseException as exc:  # propagate protected-storage failures to the caller
            errors.append(exc)
            try:
                process.terminate()
            except Exception:
                pass
        finally:
            stream.close()

    thread = threading.Thread(target=_pump, daemon=True, name="fine-tuning-secure-log")
    thread.start()
    return process, thread, errors


def _finish_logged_process(
    process: subprocess.Popen[str],
    thread: threading.Thread,
    errors: List[BaseException],
) -> None:
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    thread.join(timeout=10)
    if errors:
        raise errors[0]

def _write_ollama_modelfile(
    *,
    output_dir: Path,
    ollama_dir: Path,
    ollama_model_name: str,
    ollama_base_model: str,
    adapter_ref: Optional[str],
) -> Path:
    ollama_dir.mkdir(parents=True, exist_ok=True)
    modelfile_path = ollama_dir / "Modelfile"
    lines = [
        f"FROM {ollama_base_model}",
        "PARAMETER temperature 0.7",
        "PARAMETER top_p 0.9",
        "PARAMETER repeat_penalty 1.08",
        "PARAMETER num_ctx 8192",
        f'SYSTEM """{DEFAULT_SYSTEM_PROMPT}"""',
    ]
    if adapter_ref:
        lines.insert(1, f"ADAPTER {adapter_ref}")
    modelfile_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        "ollama_model_name": ollama_model_name,
        "ollama_base_model": ollama_base_model,
        "adapter_ref": adapter_ref,
        "adapter_gguf": str((ollama_dir / "adapter.gguf").resolve()) if (ollama_dir / "adapter.gguf").is_file() else None,
        "adapter_dir": str(output_dir.resolve()) if output_dir.exists() else None,
        "output_dir": str(output_dir.resolve()),
        "modelfile_path": str(modelfile_path.resolve()),
    }
    (ollama_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return modelfile_path

def _patch_llama_cpp_lora_converter(converter_path: Path) -> None:
    try:
        text = converter_path.read_text(encoding="utf-8")
    except Exception:
        return
    marker = "Mistral3Model.Ministral3Model"
    if marker in text:
        return
    needle = "            model_class = get_model_class(model_arch)\n"
    insert = (
        needle
        + '            if model_arch == "Mistral3ForConditionalGeneration" and hparams.get("text_config", {}).get("model_type") != "mistral4":\n'
        + "                from conversion.mistral3 import Mistral3Model\n"
        + "                model_class = Mistral3Model.Ministral3Model\n"
    )
    if needle in text:
        converter_path.write_text(text.replace(needle, insert, 1), encoding="utf-8")

def _ensure_llama_cpp_converter(log_path: Optional[Path] = None) -> Optional[Path]:
    converter_path = LLAMA_CPP_DIR / "convert_lora_to_gguf.py"
    if converter_path.is_file():
        _patch_llama_cpp_lora_converter(converter_path)
        return converter_path
    git = shutil.which("git")
    if not git:
        _append_job_log(log_path, "GGUF_CONVERT skipped: git was not found for llama.cpp checkout.") if log_path else None
        return None
    try:
        LLAMA_CPP_DIR.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [git, "clone", "--depth", "1", "https://github.com/ggerganov/llama.cpp", str(LLAMA_CPP_DIR)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as exc:
        _append_job_log(log_path, f"GGUF_CONVERT skipped: failed to clone llama.cpp: {exc}") if log_path else None
        return None
    if completed.returncode != 0:
        _append_job_log(log_path, "GGUF_CONVERT skipped: " + _clean_subprocess_output(completed.stdout, completed.stderr)) if log_path else None
        return None
    if converter_path.is_file():
        _patch_llama_cpp_lora_converter(converter_path)
        return converter_path
    _append_job_log(log_path, "GGUF_CONVERT skipped: llama.cpp converter was not found after clone.") if log_path else None
    return None

def _ensure_base_config_snapshot(model_id: str, log_path: Optional[Path] = None) -> Optional[Path]:
    slug = _safe_slug(model_id.replace("/", "-"), default="base-model")
    base_dir = WORKSPACE_DIR / "base_configs" / slug
    config_path = base_dir / "config.json"
    if config_path.is_file():
        return base_dir
    try:
        from huggingface_hub import snapshot_download

        base_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            model_id,
            local_dir=str(base_dir),
            allow_patterns=[
                "config.json",
                "tokenizer.json",
                "tokenizer.model",
                "tokenizer_config.json",
                "special_tokens_map.json",
                "tekken.json",
                "tekkenizer.json",
            ],
        )
    except Exception as exc:
        _append_job_log(log_path, f"GGUF_CONVERT skipped: failed to stage base config: {exc}") if log_path else None
        return None
    return base_dir if config_path.is_file() else None

def _ensure_gguf_adapter(
    *,
    job: Dict[str, Any],
    config: Dict[str, Any],
    output_dir: Path,
    ollama_dir: Path,
    log_path: Path,
) -> Dict[str, Any]:
    gguf_path = ollama_dir / "adapter.gguf"
    if gguf_path.is_file():
        _write_ollama_modelfile(
            output_dir=output_dir,
            ollama_dir=ollama_dir,
            ollama_model_name=str(job["model_name"]),
            ollama_base_model=str(config.get("ollama_base_model") or job["ollama_base_model"]),
            adapter_ref="adapter.gguf",
        )
        return {"status": "success", "adapter_gguf": str(gguf_path)}

    if not (output_dir / "adapter_model.safetensors").is_file() or not (output_dir / "adapter_config.json").is_file():
        return {"status": "skipped", "message": "PEFT adapter files were not found."}
    converter = _ensure_llama_cpp_converter(log_path)
    if converter is None:
        return {"status": "skipped", "message": "llama.cpp converter is unavailable."}
    model_id = str(config.get("training_model_id") or job["training_model_id"] or DEFAULT_TRAINING_MODEL)
    base_dir = _ensure_base_config_snapshot(model_id, log_path)
    if base_dir is None:
        return {"status": "skipped", "message": "Base config snapshot is unavailable."}

    cmd = [
        sys.executable,
        str(converter),
        str(output_dir),
        "--outfile",
        str(gguf_path),
        "--outtype",
        "f16",
        "--base",
        str(base_dir),
    ]
    _append_job_log(log_path, "GGUF_CONVERT command: " + " ".join(cmd))
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(converter.parent),
            capture_output=True,
            text=True,
            timeout=900,
        )
    except Exception as exc:
        _append_job_log(log_path, f"GGUF_CONVERT failed: {exc}")
        return {"status": "error", "message": str(exc)}
    output = _clean_subprocess_output(completed.stdout, completed.stderr)
    if output:
        _append_job_log(log_path, "GGUF_CONVERT output:\n" + output)
    if completed.returncode != 0 or not gguf_path.is_file():
        return {"status": "error", "message": output or "GGUF adapter conversion failed."}

    modelfile_path = _write_ollama_modelfile(
        output_dir=output_dir,
        ollama_dir=ollama_dir,
        ollama_model_name=str(job["model_name"]),
        ollama_base_model=str(config.get("ollama_base_model") or job["ollama_base_model"]),
        adapter_ref="adapter.gguf",
    )
    _append_job_log(log_path, f"GGUF_CONVERT completed adapter={gguf_path} modelfile={modelfile_path}")
    return {"status": "success", "adapter_gguf": str(gguf_path), "modelfile_path": str(modelfile_path)}

def _whatsapp_runtime_paths() -> Dict[str, Any]:
    service = _runtime_whatsapp_service()
    anchor = _whatsapp_service_anchor()
    state_dir = Path(getattr(service, "state_dir", "") or get_service_data_dir("whatsapp", anchor=anchor))
    node_dir = Path(getattr(service, "node_dir", "") or get_node_service_dir("whatsapp", anchor))
    node_command = str(getattr(service, "node_command", "") or get_node_command(anchor))
    device_name = str(getattr(service, "device_name", "") or os.getenv("DEVICE_NAME") or "AutoYou-WhatsApp")
    auth_path = Path(os.getenv("WWEBJS_AUTH_PATH") or state_dir / ".wwebjs_auth")
    cache_path = Path(os.getenv("WWEBJS_CACHE_PATH") or state_dir / ".wwebjs_cache")
    return {
        "state_dir": state_dir,
        "node_dir": node_dir,
        "node_command": node_command,
        "device_name": device_name,
        "auth_path": auth_path,
        "cache_path": cache_path,
        "service_attached": service is not None,
    }

def _find_whatsapp_dump_browser_executable() -> Optional[Path]:
    repo = _repo_root()
    candidate_roots = []
    env_playwright = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if env_playwright:
        candidate_roots.append(Path(env_playwright))
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        candidate_roots.append(Path(local_appdata) / "ms-playwright")
    candidate_roots.extend(
        [
            repo / "servers" / "windows" / "artifacts" / "playwright-browsers",
            repo / "servers" / "windows" / "artifacts" / "backend" / "AutoYouServer" / "runtime" / "playwright",
        ]
    )

    patterns = (
        "chromium-*/chrome-win/chrome.exe",
        "chromium-*/chrome-win64/chrome.exe",
        "chromium-*/chrome-linux/chrome",
        "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    )
    for root in candidate_roots:
        try:
            resolved_root = Path(root).expanduser().resolve()
        except Exception:
            continue
        if not resolved_root.exists():
            continue
        root_matches: List[Path] = []
        for pattern in patterns:
            root_matches.extend(resolved_root.glob(pattern))
        if root_matches:
            return sorted(root_matches)[-1].resolve()
    browser = find_bundled_browser_executable(_whatsapp_service_anchor())
    if browser is not None and browser.exists():
        return browser
    return None

def get_whatsapp_history_dump_support() -> Dict[str, Any]:
    paths = _whatsapp_runtime_paths()
    worker_path = _whatsapp_history_worker_path()
    node_dir = Path(paths["node_dir"])
    auth_path = Path(paths["auth_path"])
    cache_path = Path(paths["cache_path"])
    session_count = 0
    if auth_path.is_dir():
        try:
            session_count = len([child for child in auth_path.iterdir() if child.is_dir() and child.name.startswith("session-")])
        except Exception:
            session_count = 0
    module_path = node_dir / "node_modules" / "whatsapp-web.js"
    node_available = _command_available(str(paths["node_command"]))
    node_modules_ready = node_dir.is_dir() and module_path.exists()
    worker_ready = worker_path.is_file()
    auth_ready = auth_path.is_dir() and session_count > 0
    cache_ready = cache_path.exists()
    browser_path = _find_whatsapp_dump_browser_executable()
    browser_ready = browser_path is not None and browser_path.is_file()
    ready = bool(node_available and node_modules_ready and worker_ready and auth_ready and browser_ready)

    if not node_available:
        message = "Node.js is not available for the WhatsApp dump worker."
    elif not node_modules_ready:
        message = "node/whatsapp/node_modules is missing whatsapp-web.js."
    elif not worker_ready:
        message = "The fine-tuning WhatsApp dump worker is missing."
    elif not auth_ready:
        message = "WhatsApp is not authenticated yet. Pair WhatsApp in AutoYou before dumping history."
    elif not browser_ready:
        message = "Chromium was not found for the WhatsApp dump worker."
    else:
        message = "WhatsApp history dump worker is ready."

    live_snapshot = get_live_whatsapp_snapshot()
    return {
        "ready": ready,
        "message": message,
        "node_available": node_available,
        "node_command": str(paths["node_command"]),
        "node_dir": str(node_dir),
        "node_modules_ready": node_modules_ready,
        "worker_path": str(worker_path),
        "worker_ready": worker_ready,
        "auth_path": str(auth_path),
        "auth_ready": auth_ready,
        "auth_session_count": session_count,
        "cache_path": str(cache_path),
        "cache_ready": cache_ready,
        "browser_path": str(browser_path) if browser_path else None,
        "browser_ready": browser_ready,
        "device_name": str(paths["device_name"]),
        "service_attached": bool(paths["service_attached"]),
        "live_runtime_status": live_snapshot.get("status", {}),
    }

def _dump_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _scope_from_includes(include_personal: bool, include_groups: bool) -> str:
    """Map the generic include filters to the underlying extraction scope.

    Personal messages are the default; selecting neither still falls back to
    personal so an extraction never silently pulls nothing.
    """
    if include_personal and include_groups:
        return "all"
    if include_groups and not include_personal:
        return "groups"
    return "personal"


def _parse_dump_date(value: Any) -> Optional[str]:
    """Normalize a date picker value to ``YYYY-MM-DD`` (or None). Raises on garbage."""
    raw = str(value or "").strip()
    if not raw:
        return None
    from datetime import datetime as _dt

    candidate = raw[:10]
    try:
        _dt.strptime(candidate, "%Y-%m-%d")
        return candidate
    except Exception as exc:
        raise ValueError(f"Invalid date {value!r}; expected YYYY-MM-DD.") from exc


def _normalize_dump_timeline(config: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the timeline filter: all-time (default) or a bounded earliest/latest range.

    Enforces the From<=To boundary so the date pickers can never submit an
    inverted window.
    """
    mode = str(config.get("timeline_mode") or config.get("timeline") or "all_time").strip().lower()
    if mode in {"range", "custom", "bounded"}:
        mode = "range"
    else:
        mode = "all_time"
    earliest = _parse_dump_date(config.get("earliest"))
    latest = _parse_dump_date(config.get("latest"))
    if mode == "all_time":
        return {"timeline_mode": "all_time", "earliest": None, "latest": None}
    if earliest and latest and earliest > latest:
        raise ValueError("Earliest date must be on or before the latest date.")
    return {"timeline_mode": "range", "earliest": earliest, "latest": latest}


def _normalize_dump_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    config = dict(config or {})

    # Generic include filters: personal default ON, groups default OFF. An explicit
    # legacy ``chat_scope``/``scope`` still wins for backward compatibility.
    include_personal = _dump_truthy(config.get("include_personal")) if config.get("include_personal") is not None else True
    include_groups = _dump_truthy(config.get("include_groups")) if config.get("include_groups") is not None else False
    explicit_scope = str(config.get("chat_scope") or config.get("scope") or "").strip().lower()
    if explicit_scope in {"personal", "groups", "all"}:
        scope = explicit_scope
        include_personal = scope in {"personal", "all"}
        include_groups = scope in {"groups", "all"}
    else:
        scope = _scope_from_includes(include_personal, include_groups)

    timeline = _normalize_dump_timeline(config)

    title = str(config.get("title") or "").strip()
    me_name = str(config.get("me_name") or "").strip()
    all_available_history = True
    return {
        "chat_scope": scope,
        "include_personal": include_personal,
        "include_groups": include_groups,
        "timeline_mode": timeline["timeline_mode"],
        "earliest": timeline["earliest"],
        "latest": timeline["latest"],
        "all_available_history": all_available_history,
        "ready_timeout_seconds": _bounded_int(config.get("ready_timeout_seconds"), default=120, minimum=20, maximum=600),
        "timeout_seconds": _bounded_int(config.get("timeout_seconds"), default=14400, minimum=60, maximum=86400),
        "title": title,
        "me_name": me_name,
        "pause_runtime_bridge": _dump_truthy(config.get("pause_runtime_bridge")),
    }

def _dump_job_from_id(job_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM dump_jobs WHERE id = ?", (str(job_id or "").strip(),)).fetchone()
    return _row_to_dict(row)

def _update_dump_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    assignments = []
    values = []
    for key, value in fields.items():
        assignments.append(f"{key} = ?")
        values.append(value)
    values.append(job_id)
    with _connect() as conn:
        conn.execute(f"UPDATE dump_jobs SET {', '.join(assignments)} WHERE id = ?", values)
        conn.commit()

def _prepare_whatsapp_browser_environment() -> Optional[Path]:
    browser = _find_whatsapp_dump_browser_executable()
    if browser is not None:
        os.environ["PUPPETEER_EXECUTABLE_PATH"] = str(browser)
        parent = browser.parent.parent.parent if browser.parent.name in {"chrome-win", "chrome-win64"} else None
        if parent is not None and parent.exists():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(parent))
    return browser

def _append_log_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _append_job_log(path, line)


def _remove_plain_worker_output(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _pause_whatsapp_runtime_bridge_for_dump(log_path: Path) -> bool:
    service = _runtime_whatsapp_service()
    if service is None:
        _append_log_line(log_path, "No in-process WhatsApp bridge was available to pause.")
        return False
    try:
        raw_status = _resolve_maybe_awaitable(service.get_status(), timeout_seconds=8)
    except Exception:
        raw_status = {}
    active = bool(
        getattr(service, "node_process", None)
        or getattr(service, "websocket", None)
        or (isinstance(raw_status, dict) and (raw_status.get("websocket_connected") or raw_status.get("node_process_running")))
    )
    if not active:
        _append_log_line(log_path, "WhatsApp bridge was not active; no pause was needed.")
        return False
    _append_log_line(log_path, "Pausing the active WhatsApp bridge so the history dump can open the LocalAuth profile.")
    try:
        _run_service_coroutine(service, service.stop(), timeout_seconds=60)
        time.sleep(2)
        return True
    except Exception as exc:
        _append_log_line(log_path, f"Failed to pause WhatsApp bridge: {exc}")
        return False

def _restart_whatsapp_runtime_bridge_after_dump(log_path: Path) -> None:
    service = _runtime_whatsapp_service()
    if service is None:
        return
    _prepare_whatsapp_browser_environment()
    _append_log_line(log_path, "Restarting the WhatsApp bridge after history dump.")
    try:
        result = _run_service_coroutine(service, service.start(), timeout_seconds=120)
        _append_log_line(log_path, f"WhatsApp bridge restart requested: {bool(result)}.")
    except Exception as exc:
        _append_log_line(log_path, f"Failed to restart WhatsApp bridge after dump: {exc}")

def start_whatsapp_history_dump_job(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        normalized = _normalize_dump_config(config)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    support = get_whatsapp_history_dump_support()
    if not support.get("ready"):
        return {"status": "error", "message": support.get("message") or "WhatsApp dump is not ready.", "support": support}
    with _connect() as conn:
        running = conn.execute(
            "SELECT id FROM dump_jobs WHERE status IN ('queued', 'running') ORDER BY created_at LIMIT 1"
        ).fetchone()
    if running:
        return {
            "status": "error",
            "message": f"WhatsApp dump job {running['id']} is already active.",
            "active_dump_job_id": running["id"],
        }

    job_id = _new_id("dump")
    dump_path = DUMPS_DIR / f"{job_id}.json"
    log_path = DUMPS_DIR / f"{job_id}.log"
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    write_secure_file(log_path, b"Queued WhatsApp history dump.\n")
    created_at = _now()
    eta = normalized["timeout_seconds"]
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO dump_jobs (
                id, created_at, updated_at, status, progress_percent, eta_seconds,
                title, source_type, output_path, log_path, config_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                created_at,
                created_at,
                "queued",
                0.0,
                eta,
                normalized["title"] or f"WhatsApp history dump {job_id[-6:]}",
                "whatsapp_history_dump",
                str(dump_path),
                str(log_path),
                json.dumps(normalized),
            ),
        )
        conn.commit()

    thread = threading.Thread(target=_run_whatsapp_history_dump_job, args=(job_id,), daemon=True, name=f"fine-tuning-dump-{job_id}")
    thread.start()
    return {"status": "started", "job": get_data_dump_job(job_id).get("job")}

def _run_whatsapp_history_dump_job(job_id: str) -> None:
    with _DUMP_LOCK:
        job = _dump_job_from_id(job_id)
        if not job:
            return
        config = job.get("config") or {}
        output_path = _ensure_workspace_child(Path(str(job["output_path"])))
        log_path = _ensure_workspace_child(Path(str(job["log_path"])))
        support = get_whatsapp_history_dump_support()
        if not support.get("ready"):
            _update_dump_job(
                job_id,
                status="failed",
                completed_at=_now(),
                progress_percent=100.0,
                eta_seconds=0,
                error_message=support.get("message") or "WhatsApp dump is not ready.",
            )
            return

        paths = _whatsapp_runtime_paths()
        worker_path = _whatsapp_history_worker_path()
        worker_output_path = output_path.with_name(f".{output_path.name}.worker-plain")
        try:
            worker_output_path.unlink()
        except FileNotFoundError:
            pass
        timeout_seconds = int(config.get("timeout_seconds") or 14400)
        expected = timeout_seconds
        cmd = [
            str(paths["node_command"]),
            str(worker_path),
            "--output",
            str(worker_output_path),
            "--node-whatsapp-dir",
            str(paths["node_dir"]),
            "--auth-path",
            str(paths["auth_path"]),
            "--cache-path",
            str(paths["cache_path"]),
            "--client-id",
            str(paths["device_name"]),
            "--scope",
            str(config.get("chat_scope") or "personal"),
            "--ready-timeout-ms",
            str(int(config.get("ready_timeout_seconds") or 120) * 1000),
            "--history-timeout-ms",
            str(max(60, timeout_seconds - 30) * 1000),
            "--all-available-history",
        ]
        # Timeline window (inclusive). Omitted entirely for all-time extraction.
        if config.get("earliest"):
            cmd += ["--earliest", str(config.get("earliest"))]
        if config.get("latest"):
            cmd += ["--latest", str(config.get("latest"))]
        env = os.environ.copy()
        env["AUTOYOU_WHATSAPP_NODE_DIR"] = str(paths["node_dir"])
        env["WWEBJS_AUTH_PATH"] = str(paths["auth_path"])
        env["WWEBJS_CACHE_PATH"] = str(paths["cache_path"])
        env["DEVICE_NAME"] = str(paths["device_name"])
        browser = _prepare_whatsapp_browser_environment()
        if browser is not None:
            env["PUPPETEER_EXECUTABLE_PATH"] = str(browser)

        _update_dump_job(job_id, status="running", started_at=_now(), progress_percent=3.0, eta_seconds=expected)
        started_at = time.time()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        bridge_was_paused = False
        _append_log_line(log_path, "Starting WhatsApp history dump.")
        _append_log_line(
            log_path,
            "Scope: {scope}; all available history, time budget: {timeout_seconds} seconds.".format(
                scope=config.get("chat_scope") or "personal",
                timeout_seconds=timeout_seconds,
            )
        )
        if config.get("pause_runtime_bridge"):
            bridge_was_paused = _pause_whatsapp_runtime_bridge_for_dump(log_path)
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            proc, proc_thread, proc_errors = _start_logged_process(
                cmd,
                cwd=str(_repo_root()),
                env=env,
                log_path=log_path,
                creationflags=creationflags,
            )
        except Exception as exc:
            if bridge_was_paused:
                _restart_whatsapp_runtime_bridge_after_dump(log_path)
            _update_dump_job(job_id, status="failed", completed_at=_now(), error_message=f"Could not start dump worker: {exc}")
            return

        while proc.poll() is None:
            elapsed = max(1, int(time.time() - started_at))
            if elapsed > timeout_seconds:
                proc.terminate()
                try:
                    _finish_logged_process(proc, proc_thread, proc_errors)
                except Exception as exc:
                    _remove_plain_worker_output(worker_output_path)
                    if bridge_was_paused:
                        _restart_whatsapp_runtime_bridge_after_dump(log_path)
                    _update_dump_job(job_id, status="failed", completed_at=_now(), error_message=f"Could not seal dump log: {exc}")
                    return
                _remove_plain_worker_output(worker_output_path)
                if bridge_was_paused:
                    _restart_whatsapp_runtime_bridge_after_dump(log_path)
                _update_dump_job(
                    job_id,
                    status="failed",
                    completed_at=_now(),
                    progress_percent=100.0,
                    eta_seconds=0,
                    error_message=f"WhatsApp history dump timed out after {timeout_seconds} seconds.",
                )
                return
            progress = min(88.0, 3.0 + (elapsed / max(1, expected)) * 82.0)
            _update_dump_job(job_id, progress_percent=round(progress, 1), eta_seconds=max(0, expected - elapsed))
            time.sleep(5)
        try:
            _finish_logged_process(proc, proc_thread, proc_errors)
        except Exception as exc:
            _remove_plain_worker_output(worker_output_path)
            if bridge_was_paused:
                _restart_whatsapp_runtime_bridge_after_dump(log_path)
            _update_dump_job(job_id, status="failed", completed_at=_now(), error_message=f"Could not seal dump log: {exc}")
            return
        return_code = int(proc.returncode or 0)

        if bridge_was_paused:
            _restart_whatsapp_runtime_bridge_after_dump(log_path)

        if return_code != 0:
            _remove_plain_worker_output(worker_output_path)
            _update_dump_job(
                job_id,
                status="failed",
                completed_at=_now(),
                progress_percent=100.0,
                eta_seconds=0,
                return_code=return_code,
                error_message=_tail_file(log_path, lines=8) or "WhatsApp history dump failed.",
            )
            return
        if not worker_output_path.is_file():
            _update_dump_job(
                job_id,
                status="failed",
                completed_at=_now(),
                progress_percent=100.0,
                eta_seconds=0,
                return_code=return_code,
                error_message="WhatsApp history dump completed without writing an output file.",
            )
            return

        try:
            raw_dump = worker_output_path.read_bytes()
            payload = json.loads(raw_dump.decode("utf-8"))
            write_secure_file(output_path, raw_dump)
        except Exception as exc:
            _remove_plain_worker_output(worker_output_path)
            _update_dump_job(
                job_id,
                status="failed",
                completed_at=_now(),
                progress_percent=100.0,
                eta_seconds=0,
                return_code=return_code,
                error_message=f"Could not parse WhatsApp dump output: {exc}",
            )
            return
        _remove_plain_worker_output(worker_output_path)

        messages = parse_whatsapp_history_dump(payload)
        result = build_samples_from_messages(messages, me_name=str(config.get("me_name") or "").strip() or None)
        dataset_result = _persist_dataset(
            result=result,
            source_type="whatsapp_history_dump",
            title=str(config.get("title") or "").strip() or "WhatsApp history dump",
            original_filename=output_path.name,
            raw_bytes=raw_dump,
            metadata={
                "dump_job_id": job_id,
                "chat_scope": config.get("chat_scope") or "personal",
                "all_available_history": bool(config.get("all_available_history")),
                "time_budget_seconds": timeout_seconds,
                "time_limit_reached": bool(payload.get("timeLimitReached")),
                "dump_path": str(output_path),
                "chat_count": int(payload.get("chatCount") or 0),
                "dumped_message_count": int(payload.get("messageCount") or 0),
            },
        )
        if dataset_result.get("status") != "success":
            dumped_message_count = int(payload.get("messageCount") or 0)
            dumped_chat_count = int(payload.get("chatCount") or 0)
            if dumped_message_count > 0:
                _update_dump_job(
                    job_id,
                    status="completed",
                    completed_at=_now(),
                    progress_percent=100.0,
                    eta_seconds=0,
                    return_code=return_code,
                    message_count=dumped_message_count,
                    chat_count=dumped_chat_count,
                    error_message=(
                        "Dataset not created: "
                        + str(dataset_result.get("message") or "WhatsApp dump did not produce trainable owner replies.")
                    ),
                )
                return
            _update_dump_job(
                job_id,
                status="failed",
                completed_at=_now(),
                progress_percent=100.0,
                eta_seconds=0,
                return_code=return_code,
                message_count=dumped_message_count,
                chat_count=dumped_chat_count,
                error_message=dataset_result.get("message") or "WhatsApp dump did not produce trainable samples.",
            )
            return

        dataset = dataset_result.get("dataset") or {}
        _update_dump_job(
            job_id,
            status="completed",
            completed_at=_now(),
            progress_percent=100.0,
            eta_seconds=0,
            return_code=return_code,
            dataset_id=dataset.get("id"),
            message_count=int(payload.get("messageCount") or 0),
            chat_count=int(payload.get("chatCount") or 0),
            error_message=("Time budget reached; partial history was saved." if payload.get("timeLimitReached") else None),
        )

def list_data_dump_jobs(limit: int = 30) -> Dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM dump_jobs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit or 30), 100)),),
        ).fetchall()
    return {"status": "success", "jobs": [_row_to_dict(row) for row in rows], "count": len(rows)}

def get_data_dump_job(job_id: str) -> Dict[str, Any]:
    job = _dump_job_from_id(job_id)
    if not job:
        return {"status": "error", "message": f"Data dump job {job_id} was not found."}
    dataset = get_dataset(job.get("dataset_id") or "") if job.get("dataset_id") else None
    return {"status": "success", "job": job, "dataset": dataset}

def list_datasets(limit: int = 50) -> Dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM datasets ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit or 50), 200)),),
        ).fetchall()
    return {"status": "success", "datasets": [_row_to_dict(row) for row in rows], "count": len(rows)}

def get_dataset(dataset_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM datasets WHERE id = ?", (str(dataset_id or "").strip(),)).fetchone()
    return _row_to_dict(row)


def delete_dataset(dataset_id: str) -> Dict[str, Any]:
    """Remove a prepared dataset only when no training or dump record needs it."""

    dataset = get_dataset(dataset_id)
    if not dataset:
        return {"status": "error", "message": f"Dataset {dataset_id} was not found."}
    with _connect() as conn:
        references = conn.execute(
            """
            SELECT id, status, 'training' AS kind FROM training_jobs WHERE dataset_id = ?
            UNION ALL
            SELECT id, status, 'dump' AS kind FROM dump_jobs WHERE dataset_id = ?
            """,
            (dataset["id"], dataset["id"]),
        ).fetchall()
    if references:
        return {
            "status": "error",
            "message": "Delete the linked training or collection jobs before removing this dataset.",
            "references": [{"id": row["id"], "status": row["status"], "kind": row["kind"]} for row in references],
        }
    dataset_dir = DATASETS_DIR / str(dataset["id"])
    try:
        _safe_rmtree(dataset_dir)
    except Exception as exc:
        return {"status": "error", "message": f"Failed to remove dataset files: {exc}"}
    with _connect() as conn:
        conn.execute("DELETE FROM datasets WHERE id = ?", (dataset["id"],))
        conn.commit()
    return {"status": "success", "deleted_dataset_id": dataset["id"]}

def _update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    assignments = []
    values = []
    for key, value in fields.items():
        assignments.append(f"{key} = ?")
        values.append(value)
    values.append(job_id)
    with _connect() as conn:
        conn.execute(f"UPDATE training_jobs SET {', '.join(assignments)} WHERE id = ?", values)
        conn.commit()


def _mark_job_running(job_id: str) -> bool:
    """Claim a queued job without reviving one cancelled by another process."""

    now = _now()
    with _connect() as conn:
        updated = conn.execute(
            """
            UPDATE training_jobs
               SET status = 'running', started_at = ?, updated_at = ?, progress_percent = 2.0
             WHERE id = ? AND status = 'queued'
            """,
            (now, now, job_id),
        ).rowcount
        conn.commit()
    return bool(updated)


def _training_cancel_requested(job_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT status FROM training_jobs WHERE id = ?", (job_id,)).fetchone()
    return bool(row and row["status"] == "cancelling")


def cancel_fine_tuning_job(job_id: str) -> Dict[str, Any]:
    """Request a cross-process stop for a queued or running local trainer."""

    normalized_id = str(job_id or "").strip()
    if not normalized_id:
        return {"status": "error", "message": "job_id is required."}
    now = _now()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM training_jobs WHERE id = ?", (normalized_id,)).fetchone()
        job = _row_to_dict(row)
        if not job:
            return {"status": "error", "message": f"Training job {normalized_id} was not found."}
        current_status = str(job.get("status") or "")
        if current_status == "queued":
            conn.execute(
                """
                UPDATE training_jobs
                   SET status = 'cancelled', updated_at = ?, completed_at = ?, eta_seconds = 0,
                       error_message = 'Cancelled before the trainer started.'
                 WHERE id = ? AND status = 'queued'
                """,
                (now, now, normalized_id),
            )
            conn.commit()
            result_status = "cancelled"
            message = "Queued training job was cancelled."
        elif current_status in {"running", "cancelling"}:
            conn.execute(
                """
                UPDATE training_jobs
                   SET status = 'cancelling', updated_at = ?, error_message = 'Cancellation requested by the operator.'
                 WHERE id = ? AND status IN ('running', 'cancelling')
                """,
                (now, normalized_id),
            )
            conn.commit()
            result_status = "cancelling"
            message = "Cancellation requested; the trainer will stop at its next status check."
        else:
            return {"status": "error", "message": f"Training job is already {current_status or 'finished'}."}
    try:
        _append_job_log(Path(str(job.get("log_path") or "")), message)
    except Exception:
        pass
    return {"status": result_status, "message": message, "job": get_training_job(normalized_id).get("job")}

def _estimate_total_seconds(sample_count: int, max_steps: int, prepare_only: bool) -> int:
    if prepare_only:
        return 10
    steps = max(1, int(max_steps or DEFAULT_MAX_STEPS))
    samples = max(1, int(sample_count or 1))
    return max(90, min(24 * 3600, int(steps * 8 + samples * 0.8)))

def _job_from_id(job_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM training_jobs WHERE id = ?", (str(job_id or "").strip(),)).fetchone()
    return _row_to_dict(row)

def _normalize_job_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    config = dict(config or {})
    profile = _recommended_training_profile()

    def configured_value(name: str, default: Any) -> Any:
        value = config.get(name)
        return default if value is None or value == "" else value

    def configured_bool(name: str, env_name: str, default: bool) -> bool:
        value = config[name] if name in config else os.getenv(env_name, default)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    vision_min_pixels = int(configured_value("vision_min_pixels", DEFAULT_VISION_MIN_PIXELS))
    vision_max_pixels = int(configured_value("vision_max_pixels", DEFAULT_VISION_MAX_PIXELS))
    if vision_min_pixels <= 0 or vision_max_pixels < vision_min_pixels:
        raise ValueError("Vision pixel settings must be positive and max must be at least min.")

    allow_cpu_training = configured_bool("allow_cpu_training", "AUTOYOU_FINE_TUNING_ALLOW_CPU_TRAINING", False)
    # An explicit CPU opt-in means the caller requested training, not merely a
    # preparation run. An explicit prepare_only value still wins below.
    prepare_only_default = bool(profile["prepare_only"]) and not allow_cpu_training

    return {
        "training_model_id": str(configured_value("training_model_id", DEFAULT_TRAINING_MODEL)).strip(),
        "ollama_base_model": str(configured_value("ollama_base_model", DEFAULT_OLLAMA_BASE_MODEL)).strip(),
        "epochs": float(configured_value("epochs", 1.0)),
        "max_steps": int(configured_value("max_steps", DEFAULT_MAX_STEPS)),
        "max_seq_length": int(configured_value("max_seq_length", profile["max_seq_length"])),
        "batch_size": int(configured_value("batch_size", profile["batch_size"])),
        "gradient_accumulation_steps": int(configured_value("gradient_accumulation_steps", profile["gradient_accumulation_steps"])),
        "learning_rate": float(configured_value("learning_rate", 2e-4)),
        "lora_rank": int(configured_value("lora_rank", profile["lora_rank"])),
        "lora_alpha": int(configured_value("lora_alpha", profile["lora_alpha"])),
        "lora_dropout": float(configured_value("lora_dropout", 0.05)),
        "vision_min_pixels": vision_min_pixels,
        "vision_max_pixels": vision_max_pixels,
        "prepare_only": configured_bool("prepare_only", "AUTOYOU_FINE_TUNING_PREPARE_ONLY", prepare_only_default),
        "allow_cpu_training": allow_cpu_training,
    }

def start_training_job(
    *,
    dataset_id: str,
    model_name: Optional[str] = None,
    title: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    dataset = get_dataset(dataset_id)
    if not dataset:
        return {"status": "error", "message": f"Dataset {dataset_id} was not found."}
    with _connect() as conn:
        running = conn.execute(
            "SELECT id FROM training_jobs WHERE status IN ('queued', 'running', 'cancelling') ORDER BY created_at LIMIT 1"
        ).fetchone()
    if running:
        return {
            "status": "error",
            "message": f"Training job {running['id']} is already active.",
            "active_job_id": running["id"],
        }

    try:
        normalized_config = _normalize_job_config(config)
    except (TypeError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}

    prune_old_runs()

    job_id = _new_id("job")
    normalized_model_name = _safe_slug(model_name or f"autoyou-whatsapp-{job_id[-6:]}")
    job_dir = RUNS_DIR / job_id
    output_dir = job_dir / "adapter"
    ollama_dir = job_dir / "ollama"
    log_path = job_dir / "training.log"
    job_dir.mkdir(parents=True, exist_ok=False)
    write_secure_file(log_path, b"Queued fine-tuning job.\n")
    created_at = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO training_jobs (
                id, dataset_id, created_at, updated_at, status, progress_percent,
                eta_seconds, title, model_name, training_model_id, ollama_base_model,
                output_dir, ollama_dir, log_path, config_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                dataset["id"],
                created_at,
                created_at,
                "queued",
                0.0,
                _estimate_total_seconds(dataset.get("sample_count", 1), normalized_config["max_steps"], normalized_config["prepare_only"]),
                title or f"WhatsApp fine tune {job_id[-6:]}",
                normalized_model_name,
                normalized_config["training_model_id"],
                normalized_config["ollama_base_model"],
                str(output_dir),
                str(ollama_dir),
                str(log_path),
                json.dumps(normalized_config),
            ),
        )
        conn.commit()

    thread = threading.Thread(target=_run_training_job, args=(job_id,), daemon=True, name=f"fine-tuning-agent-{job_id}")
    thread.start()
    return {"status": "started", "job": get_training_job(job_id).get("job")}

def _run_training_job(job_id: str) -> None:
    with _JOB_LOCK:
        job = _job_from_id(job_id)
        if not job:
            return
        dataset = get_dataset(job["dataset_id"])
        if not dataset:
            _update_job(job_id, status="failed", completed_at=_now(), error_message="Dataset was removed.")
            return
        config = job.get("config") or {}
        output_dir = Path(job["output_dir"])
        ollama_dir = Path(job["ollama_dir"])
        log_path = Path(job["log_path"])
        output_dir.mkdir(parents=True, exist_ok=True)
        ollama_dir.mkdir(parents=True, exist_ok=True)
        if not _mark_job_running(job_id):
            return
        if _training_cancel_requested(job_id):
            _update_job(
                job_id,
                status="cancelled",
                completed_at=_now(),
                eta_seconds=0,
                error_message="Cancelled before the trainer started.",
            )
            return

        cmd = _training_runner_command() + [
            "--train-data",
            str(dataset["train_path"]),
            "--output-dir",
            str(output_dir),
            "--ollama-dir",
            str(ollama_dir),
            "--ollama-model-name",
            str(job["model_name"]),
            "--training-model-id",
            str(config.get("training_model_id") or job["training_model_id"]),
            "--ollama-base-model",
            str(config.get("ollama_base_model") or job["ollama_base_model"]),
            "--epochs",
            str(config.get("epochs", 1.0)),
            "--max-steps",
            str(config.get("max_steps", DEFAULT_MAX_STEPS)),
            "--max-seq-length",
            str(config.get("max_seq_length", 2048)),
            "--batch-size",
            str(config.get("batch_size", 1)),
            "--gradient-accumulation-steps",
            str(config.get("gradient_accumulation_steps", 8)),
            "--learning-rate",
            str(config.get("learning_rate", 2e-4)),
            "--lora-rank",
            str(config.get("lora_rank", 16)),
            "--lora-alpha",
            str(config.get("lora_alpha", 32)),
            "--lora-dropout",
            str(config.get("lora_dropout", 0.05)),
            "--vision-min-pixels",
            str(config.get("vision_min_pixels", DEFAULT_VISION_MIN_PIXELS)),
            "--vision-max-pixels",
            str(config.get("vision_max_pixels", DEFAULT_VISION_MAX_PIXELS)),
        ]
        if dataset.get("eval_path"):
            cmd.extend(["--eval-data", str(dataset["eval_path"])])
        if config.get("prepare_only"):
            cmd.append("--prepare-only")
        if config.get("allow_cpu_training"):
            cmd.append("--allow-cpu-training")

        env = os.environ.copy()
        project_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("HF_HOME", str(HF_CACHE_DIR))
        env.setdefault("TRANSFORMERS_CACHE", str(HF_CACHE_DIR / "transformers"))
        env.setdefault("HF_HUB_CACHE", str(HF_CACHE_DIR / "hub"))
        env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
        started_at = time.time()
        expected = _estimate_total_seconds(dataset.get("sample_count", 1), int(config.get("max_steps") or DEFAULT_MAX_STEPS), bool(config.get("prepare_only")))
        _append_job_log(log_path, "Command: " + " ".join(cmd))
        try:
            proc, proc_thread, proc_errors = _start_logged_process(
                cmd,
                cwd=project_root,
                env=env,
                log_path=log_path,
            )
        except Exception as exc:
            _update_job(job_id, status="failed", completed_at=_now(), error_message=f"Could not start trainer: {exc}")
            return

        cancellation_sent = False
        cancellation_sent_at: Optional[float] = None
        cancellation_killed = False
        while proc.poll() is None:
            if _training_cancel_requested(job_id):
                if not cancellation_sent:
                    _append_job_log(log_path, "Cancellation requested; stopping trainer process.")
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    cancellation_sent = True
                    cancellation_sent_at = _monotonic()
                elif not cancellation_killed and cancellation_sent_at is not None and _monotonic() - cancellation_sent_at >= _CANCEL_GRACE_SECONDS:
                    _append_job_log(log_path, "Trainer did not stop within the cancellation grace period; killing it.")
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    cancellation_killed = True
                _sleep(1)
                continue
            elapsed = max(1, int(time.time() - started_at))
            progress = min(92.0, 2.0 + (elapsed / max(1, expected)) * 88.0)
            eta = max(0, expected - elapsed)
            _update_job(job_id, progress_percent=round(progress, 1), eta_seconds=eta)
            _sleep(1)
        try:
            _finish_logged_process(proc, proc_thread, proc_errors)
        except Exception as exc:
            _update_job(job_id, status="failed", completed_at=_now(), return_code=int(proc.returncode or 1), error_message=f"Could not seal trainer log: {exc}")
            return
        return_code = int(proc.returncode or 0)

        if _training_cancel_requested(job_id):
            _update_job(
                job_id,
                status="cancelled",
                completed_at=_now(),
                progress_percent=100.0,
                eta_seconds=0,
                return_code=return_code,
                error_message="Cancelled by the operator.",
            )
            return

        if return_code == 0 and not bool(config.get("prepare_only")):
            conversion = _ensure_gguf_adapter(
                job=job,
                config=config,
                output_dir=output_dir,
                ollama_dir=ollama_dir,
                log_path=log_path,
            )
            if conversion.get("status") == "success":
                _update_job(job_id, progress_percent=96.0, eta_seconds=0)
            elif conversion.get("status") == "error":
                _append_job_log(log_path, f"GGUF_CONVERT error: {conversion.get('message')}")

        modelfile_path = ollama_dir / "Modelfile"
        if return_code == 0 and modelfile_path.is_file():
            _update_job(
                job_id,
                status="completed",
                completed_at=_now(),
                progress_percent=100.0,
                eta_seconds=0,
                return_code=return_code,
                modelfile_path=str(modelfile_path),
                error_message=None,
            )
            prune_old_runs()
        else:
            message = "Training failed."
            tail = tail_fine_tuning_job_log(job_id, lines=20).get("log_tail") or ""
            if tail:
                message = tail.splitlines()[-1][-500:]
            _update_job(
                job_id,
                status="failed",
                completed_at=_now(),
                progress_percent=100.0,
                eta_seconds=0,
                return_code=return_code,
                modelfile_path=str(modelfile_path) if modelfile_path.is_file() else None,
                error_message=message,
            )

def list_training_jobs(limit: int = 50) -> Dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM training_jobs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit or 50), 200)),),
        ).fetchall()
    return {"status": "success", "jobs": [_row_to_dict(row) for row in rows], "count": len(rows)}

def get_training_job(job_id: str) -> Dict[str, Any]:
    job = _job_from_id(job_id)
    if not job:
        return {"status": "error", "message": f"Training job {job_id} was not found."}
    dataset = get_dataset(job["dataset_id"])
    return {"status": "success", "job": job, "dataset": dataset}

def tail_fine_tuning_job_log(job_id: str, lines: int = 80) -> Dict[str, Any]:
    job = _job_from_id(job_id)
    if not job:
        return {"status": "error", "message": f"Training job {job_id} was not found."}
    log_path = Path(str(job.get("log_path") or ""))
    if not log_path.is_file():
        return {"status": "success", "log_tail": "", "lines": 0}
    try:
        raw_lines = read_secure_file(log_path).decode("utf-8", errors="replace").splitlines()
    except SecureStorageError:
        raise
    except OSError as exc:
        return {"status": "error", "message": f"Failed to read log: {exc}"}
    count = max(1, min(int(lines or 80), 400))
    tail = "\n".join(raw_lines[-count:])
    return {"status": "success", "log_tail": tail, "lines": min(len(raw_lines), count)}

def install_fine_tuned_model(job_id: str) -> Dict[str, Any]:
    job = _job_from_id(job_id)
    if not job:
        return {"status": "error", "message": f"Training job {job_id} was not found."}
    if job.get("status") != "completed":
        return {"status": "error", "message": "Only completed jobs can be installed into Ollama."}
    modelfile_path = Path(str(job.get("modelfile_path") or Path(job["ollama_dir"]) / "Modelfile"))
    if not modelfile_path.is_file():
        return {"status": "error", "message": "The job does not have an Ollama Modelfile."}
    config = job.get("config") or {}
    output_dir = Path(str(job.get("output_dir") or ""))
    ollama_dir = Path(str(job.get("ollama_dir") or modelfile_path.parent))
    if not (ollama_dir / "adapter.gguf").is_file() and (output_dir / "adapter_model.safetensors").is_file():
        conversion = _ensure_gguf_adapter(
            job=job,
            config=config,
            output_dir=output_dir,
            ollama_dir=ollama_dir,
            log_path=Path(str(job.get("log_path") or output_dir / "training.log")),
        )
        if conversion.get("status") == "success":
            modelfile_path = Path(str(conversion.get("modelfile_path") or modelfile_path))
    ollama = shutil.which("ollama")
    if not ollama:
        return {"status": "error", "message": "Ollama CLI was not found on PATH."}
    cmd = [ollama, "create", str(job["model_name"]), "-f", str(modelfile_path)]
    install_cwd = modelfile_path.parent
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=str(install_cwd))
    except Exception as exc:
        _update_job(job["id"], install_status=f"failed: {exc}")
        return {"status": "error", "message": f"Ollama install failed: {exc}"}
    output = _clean_subprocess_output(completed.stdout, completed.stderr)
    if completed.returncode != 0:
        if "adapter_config.json" in output or "no Modelfile or safetensors files found" in output:
            output = (
                output
                + "\nOllama did not import the PEFT safetensors adapter. "
                "The run still contains adapter_model.safetensors and adapter_config.json; "
                "this local Ollama build may require a GGUF adapter conversion or newer Mistral3 adapter import support."
            )
        _update_job(job["id"], install_status=f"failed: {output[-500:]}")
        return {"status": "error", "message": output or "ollama create failed.", "return_code": completed.returncode}
    _update_job(job["id"], install_status="installed")
    return {"status": "success", "model_name": job["model_name"], "message": output or "Model installed."}

def delete_fine_tuning_job(job_id: str, *, delete_ollama_model: bool = False) -> Dict[str, Any]:
    job = _job_from_id(job_id)
    if not job:
        return {"status": "error", "message": f"Training job {job_id} was not found."}
    if job.get("status") in {"queued", "running", "cancelling"}:
        return {"status": "error", "message": "Running jobs cannot be deleted."}
    ollama_remove_result: Optional[Dict[str, Any]] = None
    if delete_ollama_model:
        ollama_remove_result = remove_ollama_model(str(job.get("model_name") or ""))
    job_dir = Path(str(job.get("output_dir") or "")).parent
    try:
        _safe_rmtree(job_dir)
    except Exception as exc:
        return {"status": "error", "message": f"Failed to delete run files: {exc}"}
    with _connect() as conn:
        conn.execute("DELETE FROM training_jobs WHERE id = ?", (job["id"],))
        conn.commit()
    return {"status": "success", "deleted_job_id": job["id"], "ollama_remove": ollama_remove_result}

def remove_ollama_model(model_name: str) -> Dict[str, Any]:
    normalized = _safe_slug(model_name, default="")
    if not normalized:
        return {"status": "error", "message": "model_name is required."}
    ollama = shutil.which("ollama")
    if not ollama:
        return {"status": "error", "message": "Ollama CLI was not found on PATH."}
    completed = subprocess.run([ollama, "rm", normalized], capture_output=True, text=True, timeout=300)
    output = _clean_subprocess_output(completed.stdout, completed.stderr)
    if completed.returncode != 0:
        return {"status": "error", "message": output or "ollama rm failed.", "return_code": completed.returncode}
    return {"status": "success", "model_name": normalized, "message": output or "Model removed."}

def _directory_size_bytes(path: Path) -> int:
    try:
        resolved = _ensure_workspace_child(path)
    except Exception:
        return 0
    if not resolved.exists():
        return 0
    total = 0
    for file_path in resolved.rglob("*"):
        try:
            if file_path.is_file():
                total += int(file_path.stat().st_size)
        except Exception:
            continue
    return total

def _job_workspace_dir(job: Dict[str, Any]) -> Path:
    return Path(str(job.get("output_dir") or "")).parent

def prune_old_runs(max_runs: int = DEFAULT_MAX_RUNS, min_free_gb: float = DEFAULT_MIN_FREE_GB) -> Dict[str, Any]:
    max_runs = max(1, int(max_runs if max_runs is not None else DEFAULT_MAX_RUNS))
    min_free_bytes = int(float(min_free_gb if min_free_gb is not None else DEFAULT_MIN_FREE_GB) * 1024 * 1024 * 1024)
    deleted: List[Dict[str, Any]] = []
    try:
        usage = shutil.disk_usage(WORKSPACE_DIR)
    except Exception:
        usage = None
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM training_jobs
             WHERE status NOT IN ('queued', 'running', 'cancelling')
             ORDER BY created_at DESC
            """
        ).fetchall()
    low_disk = bool(usage is not None and usage.free < min_free_bytes)
    keep_count = max_runs
    stale_rows = list(rows[keep_count:])
    if low_disk:
        keep_count = max(1, max_runs // 2)
        stale_rows.extend(rows[keep_count:])
    seen: set[str] = set()
    for row in stale_rows:
        job = _row_to_dict(row)
        if not job or job["id"] in seen:
            continue
        seen.add(job["id"])
        try:
            job_dir = _job_workspace_dir(job)
            size_bytes = _directory_size_bytes(job_dir)
            _safe_rmtree(job_dir)
            with _connect() as conn:
                conn.execute("DELETE FROM training_jobs WHERE id = ?", (job["id"],))
                conn.commit()
            deleted.append(
                {
                    "job_id": job["id"],
                    "model_name": job.get("model_name"),
                    "size_bytes": size_bytes,
                }
            )
        except Exception:
            continue
    try:
        usage_after = shutil.disk_usage(WORKSPACE_DIR)
    except Exception:
        usage_after = None
    return {
        "status": "success",
        "deleted_jobs": deleted,
        "deleted_job_ids": [item["job_id"] for item in deleted],
        "deleted_count": len(deleted),
        "freed_bytes": sum(int(item.get("size_bytes") or 0) for item in deleted),
        "policy": {
            "max_runs": max_runs,
            "min_free_gb": float(min_free_gb or DEFAULT_MIN_FREE_GB),
            "low_disk": low_disk,
            "kept_recent_runs": keep_count,
        },
        "disk_before": {
            "free_bytes": usage.free,
            "free_gb": round(usage.free / (1024 ** 3), 2),
        }
        if usage is not None
        else None,
        "disk_after": {
            "free_bytes": usage_after.free,
            "free_gb": round(usage_after.free / (1024 ** 3), 2),
        }
        if usage_after is not None
        else None,
    }

def list_ollama_models() -> Dict[str, Any]:
    ollama = shutil.which("ollama")
    if not ollama:
        return {"status": "unavailable", "models": [], "message": "Ollama CLI was not found on PATH."}
    try:
        completed = subprocess.run([ollama, "list"], capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return {"status": "error", "models": [], "message": str(exc)}
    lines = (completed.stdout or "").splitlines()
    models = []
    for line in lines[1:]:
        parts = line.split()
        if parts:
            models.append(parts[0])
    return {
        "status": "success" if completed.returncode == 0 else "error",
        "models": models,
        "raw": completed.stdout[-2000:],
    }

def _dependency_status() -> Dict[str, bool]:
    return {
        name: importlib.util.find_spec(name) is not None
        for name in ("torch", "transformers", "peft", "datasets", "accelerate", "bitsandbytes", "sentencepiece", "PIL", "torchvision")
    }


def _mlx_capability_status() -> Dict[str, Any]:
    """Describe MLX without claiming its adapter format is an Ollama artifact."""
    system = platform.system()
    machine = platform.machine().lower()
    native_apple_silicon = system == "Darwin" and machine in {"arm64", "aarch64"}
    installed = bool(importlib.util.find_spec("mlx") and importlib.util.find_spec("mlx_lm"))
    if native_apple_silicon and installed:
        message = (
            "MLX and mlx-lm are detected for Apple Silicon text LoRA. "
            "AutoYou currently keeps it separate from the Torch-to-Ollama adapter installer."
        )
    elif native_apple_silicon:
        message = "Apple Silicon is detected. Install optional mlx and mlx-lm[train] to enable an MLX text-LoRA environment."
    elif installed:
        message = "MLX is installed, but this host is not detected as native Apple Silicon. Its usable backend depends on the installed MLX build."
    else:
        message = "MLX is an optional Apple-Silicon text-LoRA path and is not installed for this host."
    return {
        "available": native_apple_silicon and installed,
        "detected": installed,
        "native_apple_silicon": native_apple_silicon,
        "executor_integrated": False,
        "text_lora_supported": native_apple_silicon and installed,
        "vision_lora_supported": False,
        "message": message,
    }

def _training_capability_status() -> Dict[str, Any]:
    dependencies = _dependency_status()
    missing = [name for name in ("torch", "transformers", "peft", "datasets", "accelerate") if not dependencies.get(name)]
    torch_status: Dict[str, Any] = {
        "available": dependencies.get("torch", False),
        "cuda_available": False,
        "cuda_build": None,
        "rocm_build": None,
        "mps_available": False,
        "accelerator": "unavailable",
        "accelerator_available": False,
        "device_count": 0,
        "devices": [],
    }
    if dependencies.get("torch"):
        try:
            import torch  # type: ignore[import]

            cuda_available = bool(torch.cuda.is_available())
            mps = getattr(getattr(torch, "backends", None), "mps", None)
            mps_available = bool(mps is not None and mps.is_available())
            accelerator = _accelerator_backend(torch)
            device_count = int(torch.cuda.device_count()) if cuda_available else 0
            torch_status.update(
                {
                    "cuda_available": cuda_available,
                    "cuda_build": str(getattr(torch.version, "cuda", "") or "") or None,
                    "rocm_build": str(getattr(torch.version, "hip", "") or "") or None,
                    "mps_available": mps_available,
                    "accelerator": accelerator,
                    "accelerator_available": accelerator in {"cuda", "rocm", "mps"},
                    "device_count": device_count,
                    "devices": [str(torch.cuda.get_device_name(index)) for index in range(device_count)],
                }
            )
        except Exception as exc:
            torch_status["error"] = str(exc)

    nvidia_smi: Dict[str, Any] = {"available": False, "gpus": []}
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            completed = subprocess.run(
                [nvidia, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if completed.returncode == 0:
                gpus = []
                for line in (completed.stdout or "").splitlines():
                    parts = [part.strip() for part in line.split(",")]
                    if len(parts) >= 3:
                        gpus.append({"name": parts[0], "memory_total_mb": parts[1], "memory_free_mb": parts[2]})
                nvidia_smi = {"available": True, "gpus": gpus}
            else:
                nvidia_smi = {"available": False, "error": _clean_subprocess_output(completed.stdout, completed.stderr)}
        except Exception as exc:
            nvidia_smi = {"available": False, "error": str(exc)}

    accelerator = str(torch_status.get("accelerator") or "unavailable")
    cpu_training_available = not missing and bool(torch_status.get("available"))
    real_training_ready = not missing and bool(torch_status.get("accelerator_available"))
    if missing:
        message = "Missing trainer dependencies: " + ", ".join(missing)
        next_action = "Install the missing Python packages in the interpreter that starts the AutoYou server."
    elif not torch_status.get("accelerator_available"):
        message = "CPU LoRA training is available with explicit opt-in; an accelerated Torch backend is not exposed by this interpreter."
        next_action = "For speed, install a Torch build matching this machine: CUDA for NVIDIA, ROCm for a supported AMD setup, or the Apple Silicon MPS build on macOS."
    else:
        message = f"Real LoRA training is available through the {accelerator.upper()} backend."
        next_action = "Use the conservative profile first, then increase context or batch size only after a successful smoke run."
    return {
        "real_training_ready": real_training_ready,
        "message": message,
        "dependencies": dependencies,
        "torch": torch_status,
        "cpu": {
            "available": cpu_training_available,
            "requires_explicit_opt_in": True,
            "message": "CPU LoRA training is supported for small models, but is intentionally not the default for the 8B profile.",
        },
        "mlx": _mlx_capability_status(),
        "nvidia_smi": nvidia_smi,
        "host_nvidia_not_exposed_to_torch": bool(nvidia_smi.get("available") and not torch_status.get("cuda_available")),
        "next_action": next_action,
        "cpu_training_override": str(os.getenv("AUTOYOU_FINE_TUNING_ALLOW_CPU_TRAINING", "")).strip().lower()
        in {"1", "true", "yes", "on"},
    }


def _training_modalities(capability: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Expose actual importer/trainer coverage so the UI never fakes vision support."""
    capability = capability or _training_capability_status()
    dependencies = capability.get("dependencies") or _dependency_status()
    image_dataset_ready = bool(dependencies.get("PIL"))
    image_accelerated_ready = bool(
        image_dataset_ready
        and dependencies.get("torchvision")
        and capability.get("real_training_ready")
    )
    image_cpu_ready = bool(
        image_dataset_ready
        and dependencies.get("torchvision")
        and (capability.get("cpu") or {}).get("available")
    )
    image_training_ready = image_accelerated_ready or image_cpu_ready
    image_blockers = []
    if not image_dataset_ready:
        image_blockers.append("Pillow is missing")
    if not dependencies.get("torchvision"):
        image_blockers.append("torchvision is missing")
    if not capability.get("real_training_ready") and not (capability.get("cpu") or {}).get("available"):
        image_blockers.append("no supported Torch image-training runtime is available")
    image_message = (
        "Image datasets are ready for accelerated local vision LoRA training."
        if image_accelerated_ready
        else "Image datasets are ready for CPU vision LoRA with explicit opt-in; it can be very slow."
        if image_cpu_ready
        else "Image dataset bundles are accepted, but vision training is blocked: " + ", ".join(image_blockers) + "."
    )
    return {
        "text": {
            "available": True,
            "extensions": [".txt", ".csv", ".json", ".jsonl", ".log", ".md", ".markdown"],
            "message": "Text and structured conversation data can be prepared for training.",
        },
        "images": {
            "available": image_dataset_ready,
            "dataset_ready": image_dataset_ready,
            "training_ready": image_training_ready,
            "accelerated_training_ready": image_accelerated_ready,
            "cpu_training_available": image_cpu_ready,
            "extensions": [".png", ".jpg", ".jpeg", ".webp"],
            "manifest_format": "JSONL rows with an images array and matching image files in the same dropped bundle.",
            "blockers": image_blockers,
            "message": image_message,
        },
    }


def _recommended_training_profile(capability: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Pick conservative LoRA defaults for the hardware that is actually visible."""
    capability = capability or _training_capability_status()
    free_mb = 0
    for gpu in capability.get("nvidia_smi", {}).get("gpus", []):
        try:
            free_mb = max(free_mb, int(float(gpu.get("memory_free_mb") or 0)))
        except (TypeError, ValueError):
            continue
    accelerator = str((capability.get("torch") or {}).get("accelerator") or "accelerator").upper()
    if capability.get("real_training_ready") and free_mb >= 12 * 1024:
        return {
            "name": "balanced_accelerator",
            "prepare_only": False,
            "max_seq_length": 2048,
            "batch_size": 1,
            "gradient_accumulation_steps": 8,
            "lora_rank": 16,
            "lora_alpha": 32,
            "message": f"{accelerator} with at least 12 GB free VRAM: balanced local LoRA defaults are selected.",
        }
    if capability.get("real_training_ready"):
        return {
            "name": "constrained_accelerator",
            "prepare_only": False,
            "max_seq_length": 1024,
            "batch_size": 1,
            "gradient_accumulation_steps": 16,
            "lora_rank": 8,
            "lora_alpha": 16,
            "message": f"{accelerator} is available with limited or unreported free memory: conservative local LoRA defaults are selected.",
        }
    return {
        "name": "cpu_prepare",
        "prepare_only": True,
        "max_seq_length": 512,
        "batch_size": 1,
        "gradient_accumulation_steps": 16,
        "lora_rank": 8,
        "lora_alpha": 16,
        "message": "No CUDA trainer is available: validate data and prepare the run; explicitly enable tiny-model CPU training only when intended.",
    }


def get_fine_tuning_status() -> Dict[str, Any]:
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(WORKSPACE_DIR)
    with _connect() as conn:
        dataset_count = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        job_count = conn.execute("SELECT COUNT(*) FROM training_jobs").fetchone()[0]
        dump_job_count = conn.execute("SELECT COUNT(*) FROM dump_jobs").fetchone()[0]
        active_dump = conn.execute("SELECT id FROM dump_jobs WHERE status IN ('queued', 'running') ORDER BY created_at LIMIT 1").fetchone()
        active = conn.execute("SELECT id FROM training_jobs WHERE status IN ('queued', 'running', 'cancelling') ORDER BY created_at LIMIT 1").fetchone()
    training_capability = _training_capability_status()
    profile = _recommended_training_profile(training_capability)
    return {
        "status": "success",
        "workspace": str(WORKSPACE_DIR),
        "db_path": str(DB_PATH),
        "disk": {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "free_gb": round(usage.free / (1024 ** 3), 2),
        },
        "retention": {
            "max_runs": DEFAULT_MAX_RUNS,
            "min_free_gb": DEFAULT_MIN_FREE_GB,
            "low_disk": usage.free < int(DEFAULT_MIN_FREE_GB * 1024 * 1024 * 1024),
        },
        "counts": {"datasets": dataset_count, "jobs": job_count, "dump_jobs": dump_job_count},
        "active_job_id": active["id"] if active else None,
        "active_dump_job_id": active_dump["id"] if active_dump else None,
        "dependencies": _dependency_status(),
        "training_capability": training_capability,
        "training_modalities": _training_modalities(training_capability),
        "hardware_profile": profile,
        "ollama": list_ollama_models(),
        "whatsapp": get_live_whatsapp_snapshot(),
        "telegram_user": get_live_telegram_user_snapshot(),
        "whatsapp_dump": get_whatsapp_history_dump_support(),
        "defaults": {
            "training_model_id": DEFAULT_TRAINING_MODEL,
            "ollama_base_model": DEFAULT_OLLAMA_BASE_MODEL,
            "max_steps": DEFAULT_MAX_STEPS,
            "max_seq_length": profile["max_seq_length"],
            "batch_size": profile["batch_size"],
            "gradient_accumulation_steps": profile["gradient_accumulation_steps"],
            "lora_rank": profile["lora_rank"],
            "lora_alpha": profile["lora_alpha"],
            "prepare_only": profile["prepare_only"],
        },
    }
