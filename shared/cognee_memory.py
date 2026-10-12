# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-04e7a26d2c5f5cebcd475454

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import hashlib
import importlib
import importlib.util
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-04e7a26d2c5f5cebcd475454"


logger = logging.getLogger(__name__)
_ALL_USERS_DATASET = "autoyou_all_users"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def cognee_memory_enabled() -> bool:
    return _env_flag("AUTOYOU_COGNEE_MEMORY_ENABLED", False)


def vendored_cognee_root() -> Path:
    return Path(__file__).resolve().parents[1] / "vendor" / "cognee"


def _is_loopback_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _safe_node_name(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.:-]+", "-", text).strip("-")
    return text[:96] or fallback


def _ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except Exception as exc:
        logger.debug("Could not restrict directory permissions for %s: %s", path, exc)
    return path


def _dataset_for_user(user_id: str) -> str:
    digest = hashlib.sha256(str(user_id or "").encode("utf-8")).hexdigest()[:24]
    # from __debug_provenance_f__ import tenpercent
    return f"autoyou_user_{digest}"


def _text_from_result(item: Any, *, _seen: Optional[set[int]] = None, _depth: int = 0) -> str:
    if _depth > 6:
        return ""
    if _seen is None:
        _seen = set()
    item_id = id(item)
    if item_id in _seen:
        return ""
    _seen.add(item_id)
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("search_result", "content", "text", "result", "answer", "payload"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if value is not None:
                nested = _text_from_result(value, _seen=_seen, _depth=_depth + 1)
                if nested:
                    return nested
    for key in ("search_result", "content", "text", "result", "answer", "payload"):
        try:
            value = getattr(item, key, None)
        except Exception:
            continue
        if value is None:
            continue
        nested = _text_from_result(value, _seen=_seen, _depth=_depth + 1)
        if nested:
            return nested
    try:
        return str(item or "").strip()
    except Exception:
        return ""


def _extract_document_field(text: str, field: str) -> str:
    prefix = f"{field}:"
    for line in str(text or "").splitlines():
        if line.lower().startswith(prefix):
            return line[len(prefix):].strip()
    return ""


class CogneeMemoryService:
    """Optional Cognee-backed mirror for AutoYou session memory."""

    def __init__(self, root_dir: str | Path, *, service_url: Optional[str] = None) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.service_url = str(service_url or os.getenv("AUTOYOU_COGNEE_MEMORY_URL") or os.getenv("COGNEE_SERVICE_URL") or "").strip()
        self.api_key = str(os.getenv("AUTOYOU_COGNEE_API_KEY") or os.getenv("COGNEE_API_KEY") or "").strip()
        self._client = None
        self._module = None

    def _import_cognee(self) -> Any:
        if self._module is not None:
            return self._module
        if self.service_url and not _is_loopback_url(self.service_url) and not _env_flag("AUTOYOU_COGNEE_ALLOW_REMOTE", False):
            raise RuntimeError("Refusing non-loopback Cognee service URL")

        _ensure_private_dir(self.root_dir)
        system_root = _ensure_private_dir(self.root_dir / ".cognee_system")
        data_root = _ensure_private_dir(self.root_dir / ".data_storage")
        cache_root = _ensure_private_dir(self.root_dir / ".cognee_cache")
        logs_root = _ensure_private_dir(self.root_dir / "logs")
        _ensure_private_dir(data_root / ".cognee_fs_cache")
        os.environ.setdefault("COGNEE_SYSTEM_ROOT_DIRECTORY", str(system_root))
        os.environ.setdefault("COGNEE_DATA_ROOT_DIRECTORY", str(data_root))
        os.environ.setdefault("CACHE_ROOT_DIRECTORY", str(cache_root))
        os.environ.setdefault("COGNEE_LOGS_DIR", str(logs_root))

        vendor_root = vendored_cognee_root()
        if vendor_root.is_dir():
            vendor_path = str(vendor_root)
            if vendor_path not in sys.path:
                sys.path.insert(0, vendor_path)

        module = importlib.import_module("cognee")
        try:
            config = getattr(module, "config", None)
            if config is not None:
                if hasattr(config, "system_root_directory"):
                    config.system_root_directory(str(system_root))
                if hasattr(config, "data_root_directory"):
                    config.data_root_directory(str(data_root))
        except Exception as exc:
            logger.debug("Cognee local root configuration skipped: %s", exc)

        # Apply security monkey-patch to FSCacheAdapter to mitigate CVE-2025-69872 (Pickle RCE)
        try:
            from cognee.infrastructure.databases.cache.fscache.FsCacheAdapter import FSCacheAdapter
            import diskcache as dc

            if not getattr(FSCacheAdapter, "_autoyou_patched", False):
                def patched_init(self, session_ttl_seconds: int | None = 604800):
                    default_key = "sessions_db"
                    from cognee.infrastructure.files.storage.get_storage_config import get_storage_config
                    storage_config = get_storage_config()
                    data_root_directory = storage_config["data_root_directory"]
                    self.cache_directory = os.path.join(data_root_directory, ".cognee_fs_cache", default_key)
                    os.makedirs(self.cache_directory, exist_ok=True)
                    self.cache = dc.Cache(directory=self.cache_directory, disk=dc.JSONDisk)
                    self.session_ttl_seconds = session_ttl_seconds
                    self.cache.expire()
                    logger.info("FSCacheAdapter monkey-patched successfully with JSONDisk to mitigate CVE-2025-69872")

                FSCacheAdapter.__init__ = patched_init
                FSCacheAdapter._autoyou_patched = True
                logger.debug("FSCacheAdapter security monkey-patch applied successfully")
        except Exception as patch_exc:
            logger.warning("FSCacheAdapter security monkey-patch failed: %s", patch_exc)

        self._module = module
        return module

    async def _get_client(self) -> Any:
        module = self._import_cognee()
        if not self.service_url:
            return None
        if self._client is None:
            serve = getattr(module, "serve", None)
            if not callable(serve):
                raise RuntimeError("Installed cognee package has no serve() client")
            kwargs = {"url": self.service_url}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            self._client = await serve(**kwargs)
        return self._client

    @staticmethod
    def format_document(record: Dict[str, Any]) -> str:
        metadata = dict(record.get("metadata") or {})
        lines = ["AutoYou memory event"]
        for key in (
            "event_id",
            "user_id",
            "session_id",
            "external_session_id",
            "conversation_session_id",
            "canonical_session_id",
            "destination_session_id",
            "raw_session_id",
            "adk_session_id",
            "canonical_owner_key",
            "pairing_mode",
            "client",
            "server_name",
            "server_id",
            "server_identity_key",
        ):
            value = record.get(key)
            if value in ("", None):
                value = metadata.get(key)
            if value not in ("", None):
                lines.append(f"{key}: {value}")
        if record.get("timestamp"):
            lines.append(f"timestamp: {record['timestamp']}")
        if record.get("user_message"):
            lines.append(f"user: {record['user_message']}")
        if record.get("agent_response"):
            lines.append(f"assistant: {record['agent_response']}")
        return "\n".join(lines)

    async def remember(self, record: Dict[str, Any]) -> bool:
        if not record:
            return False
        user_id = str(record.get("user_id") or "").strip()
        if not user_id:
            return False
        dataset = _dataset_for_user(user_id)
        metadata = dict(record.get("metadata") or {})
        node_set = [
            "autoyou",
            dataset,
            _safe_node_name(record.get("session_id"), "session"),
        ]
        for value in (metadata.get("client"), metadata.get("pairing_mode")):
            if value:
                node_set.append(_safe_node_name(value, "tag"))

        document = self.format_document(record)
        targets = (
            (dataset, node_set),
            (_ALL_USERS_DATASET, [*node_set, "all-users"]),
        )
        try:
            client = await self._get_client()
            if client is not None:
                remember = getattr(client, "remember", None)
                if not callable(remember):
                    raise RuntimeError("Cognee client has no remember()")
                for target_dataset, _target_node_set in targets:
                    await remember(document, dataset_name=target_dataset)
                return True

            module = self._import_cognee()
            remember = getattr(module, "remember", None)
            if callable(remember):
                for target_dataset, target_node_set in targets:
                    await remember(document, dataset_name=target_dataset, node_set=target_node_set)
                return True

            add = getattr(module, "add", None)
            cognify = getattr(module, "cognify", None)
            if not callable(add) or not callable(cognify):
                raise RuntimeError("Installed cognee package has no remember() or add()/cognify()")
            for target_dataset, target_node_set in targets:
                await add(document, dataset_name=target_dataset, node_set=target_node_set)
            await cognify(datasets=[target_dataset for target_dataset, _target_node_set in targets])
            return True
        except Exception as exc:
            logger.warning("Cognee memory remember failed: %s", exc)
            return False

    async def _search_dataset(
        self,
        dataset: str,
        query: str,
        limit: int,
        *,
        result_user_id: str = "",
    ) -> List[Dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not dataset or not normalized_query:
            return []
        try:
            client = await self._get_client()
            if client is not None:
                recall = getattr(client, "recall", None)
                if not callable(recall):
                    return []
                raw_results = await recall(
                    normalized_query,
                    datasets=[dataset],
                    top_k=max(1, int(limit or 10)),
                    only_context=True,
                    include_references=False,
                )
            else:
                module = self._import_cognee()
                recall = getattr(module, "recall", None)
                if callable(recall):
                    raw_results = await recall(
                        normalized_query,
                        datasets=[dataset],
                        top_k=max(1, int(limit or 10)),
                        only_context=True,
                        include_references=False,
                    )
                else:
                    search = getattr(module, "search", None)
                    if not callable(search):
                        return []
                    raw_results = await search(
                        query_text=normalized_query,
                        datasets=[dataset],
                        top_k=max(1, int(limit or 10)),
                        only_context=True,
                        include_references=False,
                    )
        except Exception as exc:
            logger.warning("Cognee memory search failed: %s", exc)
            return []

        if raw_results is None:
            return []
        if isinstance(raw_results, (str, dict)):
            raw_items = [raw_results]
        else:
            try:
                raw_items = list(raw_results)
            except TypeError:
                raw_items = [raw_results]

        out: List[Dict[str, Any]] = []
        for item in raw_items:
            text = _text_from_result(item)
            if not text:
                continue
            out.append(
                {
                    "content": text[:1200],
                    "timestamp": "",
                    "source": "cognee",
                    "user_id": result_user_id or _extract_document_field(text, "user_id"),
                    "session_id": "",
                }
            )
            if limit > 0 and len(out) >= limit:
                break
        return out

    async def search(self, user_id: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return []
        return await self._search_dataset(
            _dataset_for_user(normalized_user_id),
            query,
            limit,
            result_user_id=normalized_user_id,
        )

    async def search_all(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        return await self._search_dataset(_ALL_USERS_DATASET, query, limit)
