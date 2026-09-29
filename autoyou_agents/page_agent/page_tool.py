#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-67e26a61f52c5ec9acd45c29

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
PageTool: Utilities for processing hyperlinks and managing the AutoYou page feed.

Provides a unified interface that can operate either:
- Directly against the local SQLite feed (via PageFeedDB), or
- Via the running page service HTTP API (if base_url is provided).

This mirrors the behaviour of autoyou_page_service.py endpoints and
page_feed_db.py operations so agents can add links, query, tag, and
favourite items efficiently.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import json
import logging
import mimetypes
import urllib.request
from urllib.parse import quote, urlencode, urlparse, urlunparse

from typing import Any, Dict, List, Optional
import base64
import uuid
import os
import urllib.error

from shared.secure_storage import FILE_HEADER as SPM_FILE_HEADER, read_secure_file, write_secure_file

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-67e26a61f52c5ec9acd45c29"


LOGGER = logging.getLogger(__name__)


def _internal_page_tool_headers() -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    token = str(os.getenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

_X_HOST_ALIASES = {
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "m.twitter.com",
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "m.x.com",
}

_GENERIC_BLOB_MIME_TYPES = {
    "",
    "application/octet-stream",
    "binary/octet-stream",
    "application/binary",
    "application/x-binary",
    "application/unknown",
    "application/mp4",
}

_MIMETYPE_EXTENSION_HINTS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-m4v": ".m4v",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}

_FALLBACK_KIND_MIMETYPES = {
    "image": "image/jpeg",
    "camera": "image/jpeg",
    "video": "video/mp4",
    "voice": "audio/mpeg",
    "audio": "audio/mpeg",
}

def _blob_id_from_feed_url(url: Optional[str]) -> str:
    raw = str(url or "").strip()
    if raw.lower().startswith("blob://") and "://" in raw:
        return raw.split("://", 1)[1].strip()
    return ""

def _attachment_kind_from_metadata(metadata: Optional[Dict[str, Any]]) -> str:
    payload = metadata if isinstance(metadata, dict) else {}
    nested_meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    for candidate in (payload.get("kind"), nested_meta.get("kind")):
        normalized = str(candidate or "").strip().lower()
        if normalized:
            return normalized
    return ""

def _guess_mimetype_from_name(name: Optional[str]) -> Optional[str]:
    candidate = str(name or "").strip()
    if not candidate:
        return None
    guessed, _ = mimetypes.guess_type(candidate)
    if guessed:
        return guessed.lower()
    return None

def _normalize_attachment_mimetype(
    *,
    filename: Optional[str] = None,
    mimetype_hint: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    path: Optional[str] = None,
) -> str:
    provided = str(mimetype_hint or "").strip().lower()
    if provided == "application/mp4":
        provided = "video/mp4"
    if provided and provided not in _GENERIC_BLOB_MIME_TYPES:
        return provided

    for candidate in (filename, path):
        guessed = _guess_mimetype_from_name(candidate)
        if guessed:
            return guessed

    kind = _attachment_kind_from_metadata(metadata)
    fallback = _FALLBACK_KIND_MIMETYPES.get(kind)
    if fallback:
        return fallback
    return "application/octet-stream"

def _extension_for_mimetype(mimetype_value: str) -> str:
    return _MIMETYPE_EXTENSION_HINTS.get((mimetype_value or "").lower(), "")

def _classify_blob_item_type(mimetype_value: str) -> str:
    mt_l = (mimetype_value or "application/octet-stream").lower()
    if mt_l.startswith("image/"):
        return "image"
    if mt_l.startswith("video/"):
        return "video"
    if mt_l.startswith("audio/"):
        return "audio"
    if mt_l in (
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ):
        return "document"
    return "file"

class PageTool:
    """Tool for working with the AutoYou page feed and hyperlinks.

    If `base_url` is set, operations use HTTP endpoints exposed by
    autoyou_page_service.py. Otherwise, operations use PageFeedDB directly.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        db_path: Optional[str] = None,
        timeout: float = 5.0,
    ) -> None:
        self._static_base_url = (base_url or '').strip() or None
        if self._static_base_url and self._static_base_url.endswith('/'):
            self._static_base_url = self._static_base_url[:-1]

        self.timeout = float(timeout or 5.0)
        # from __debug_provenance_y__ import legal

        # Resolve central DB path and uploads directory
        try:
            from shared.platform_runtime import get_config_dir
            from pathlib import Path
            import os
            module_dir = Path(__file__).resolve().parent
            runtime_anchor = module_dir
            for parent in module_dir.parents:
                if (parent / "server.py").is_file() and (parent / "autoyou_agents").is_dir():
                    runtime_anchor = parent
                    break
            config_dir = get_config_dir("AutoYou", anchor=runtime_anchor)
            self._uploads_dir = str(config_dir / "uploads")
            if not db_path:
                db_path = str(config_dir / "page_feed.db")
        except Exception:
            from pathlib import Path
            self._uploads_dir = str(Path(__file__).resolve().parent / "uploads")
            if not db_path:
                db_path = str(Path(__file__).resolve().parent / "page_feed.db")

        self._db_path = db_path
        self._db_instance = None

    @property
    def base_url(self) -> Optional[str]:
        """Dynamically resolve the AutoYou Page Service URL from the core server STATE."""
        if self._static_base_url:
            return self._static_base_url
            
        try:
            import sys
            from shared.platform_runtime import get_application_root
            app_root = get_application_root(__file__)
            project_root = str(app_root)
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
                
            from server import STATE, _get_autoyou_page_service_base_url
            if STATE and STATE.config:
                # The page feed UI/API was migrated out of the page service into
                # the page_agent managed frontend. The page service at `port` is
                # now the agent-website proxy host, so reach the feed backend
                # through its stable proxy path. (URLs are built by string concat,
                # e.g. f"{base_url}/api/feed", so the prefix is preserved.)
                return _get_autoyou_page_service_base_url(STATE.config) + "/agent/page_agent"
        except Exception as e:
            LOGGER.debug(f"Could not dynamically resolve base_url from server STATE: {e}")
            
        return None

    @property
    def _db(self):
        """Lazy load and cache the DB connection if HTTP mode is unavailable."""
        if self.base_url is not None:
            return None # Use HTTP mode instead
            
        if self._db_instance is not None:
            return self._db_instance
            
        try:
            from page_feed_db import PageFeedDB
            self._db_instance = PageFeedDB(db_path=self._db_path)
        except Exception:
            try:
                import os, sys
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
                if project_root not in sys.path:
                    sys.path.append(project_root)
                from page_feed_db import PageFeedDB  # type: ignore
                self._db_instance = PageFeedDB(db_path=self._db_path)
            except Exception as e2:
                LOGGER.error(f"Failed to initialize PageFeedDB: {e2}")
                self._db_instance = None
                
        return self._db_instance

    # --- HTTP Helpers ---
    def _http_json(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("HTTP mode not enabled: base_url is None")
        url = f"{self.base_url}{path}"
        data = None
        headers = _internal_page_tool_headers()
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url=url, data=data, headers=headers, method=method.upper())
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
            try:
                return json.loads(raw.decode("utf-8", errors="ignore"))
            except Exception:
                return {}

    def _http_upload_multipart(
        self,
        path: str,
        field_name: str,
        filename: str,
        data_bytes: bytes,
        mimetype: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload one file through the authenticated local Page API."""
        if not self.base_url:
            raise RuntimeError("HTTP mode not enabled: base_url is None")
        boundary = f"----AutoYouFormBoundary{uuid.uuid4().hex}"
        ct = (mimetype or "application/octet-stream")
        # Build multipart body
        parts: List[bytes] = []
        # file part
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        disposition = f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        parts.append(disposition.encode("utf-8"))
        parts.append(f"Content-Type: {ct}\r\n\r\n".encode("utf-8"))
        parts.append(data_bytes)
        parts.append(b"\r\n")
        # end
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        url = f"{self.base_url}{path}"
        headers = _internal_page_tool_headers()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(url=url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
            try:
                return json.loads(raw.decode("utf-8", errors="ignore"))
            except Exception:
                return {}

    def _http_upload_blob(self, filename: str, data_bytes: bytes, mimetype: Optional[str] = None) -> Dict[str, Any]:
        """Upload a Page feed file blob, preserving the existing helper contract."""
        return self._http_upload_multipart("/api/blob", "file", filename, data_bytes, mimetype)

    def get_server_display_photo(self) -> Dict[str, Any]:
        """Check whether the server-owned Page avatar exists."""
        if not self.base_url:
            return {"success": False, "error": "The Page photo service is unavailable."}
        url = f"{self.base_url}/api/profile/avatar"
        try:
            request = urllib.request.Request(url, headers=_internal_page_tool_headers())
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read(64 * 1024 + 1)
                return {
                    "success": True,
                    "has_photo": True,
                    "mime_type": response.headers.get_content_type(),
                }
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"success": True, "has_photo": False}
            return {"success": False, "error": f"Page photo request failed (HTTP {exc.code})."}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def update_server_display_photo_from_path(self, path: str) -> Dict[str, Any]:
        """Replace the server-owned Page avatar with a local image attachment."""
        try:
            raw_path = os.path.normpath(str(path or "").strip().strip('"').strip("'"))
            with open(raw_path, "rb") as source:
                raw = source.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024:
                return {"success": False, "error": "Use a non-empty PNG, JPEG, or WebP image under 64 KB."}
            payload = read_secure_file(raw_path) if raw.startswith(SPM_FILE_HEADER) else raw
            if not payload or len(payload) > 64 * 1024:
                return {"success": False, "error": "Use a non-empty PNG, JPEG, or WebP image under 64 KB."}
            return self._http_upload_multipart(
                "/api/profile/avatar", "image", "profile-photo", payload
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def delete_server_display_photo(self) -> Dict[str, Any]:
        """Remove the server-owned Page avatar through the Page API."""
        if not self.base_url:
            return {"success": False, "error": "The Page photo service is unavailable."}
        try:
            return self._http_json("DELETE", "/api/profile/avatar")
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _user_facing_url_for_item(self, item: Optional[Dict[str, Any]]) -> str:
        if not isinstance(item, dict):
            return ""
        item_url = str(item.get("url") or "").strip()
        blob_id = _blob_id_from_feed_url(item_url)
        if blob_id:
            base_url = (self.base_url or "").strip().rstrip("/")
            if base_url:
                return f"{base_url}/api/blob/{quote(blob_id, safe='')}"
            return f"/api/blob/{quote(blob_id, safe='')}"

        parsed = urlparse(item_url)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            return item_url
        return ""

    def _enrich_item_for_user_display(self, item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return item
        enriched = dict(item)
        view_url = self._user_facing_url_for_item(enriched)
        if view_url:
            enriched.setdefault("open_url", view_url)
            enriched.setdefault("view_url", view_url)
        if _blob_id_from_feed_url(enriched.get("url")):
            enriched.setdefault("storage_location", "AutoYou page feed on this computer")
            enriched.setdefault("local_only", True)
        return enriched

    def _saved_item_message(self, item: Optional[Dict[str, Any]], *, action: str = "Saved") -> str:
        if not isinstance(item, dict):
            return f"{action} item to your AutoYou page feed."

        title = str(item.get("title") or item.get("filename") or item.get("url") or "item").strip()
        item_id = item.get("id")
        id_text = f" (feed item #{item_id})" if item_id not in (None, "") else ""
        view_url = str(item.get("view_url") or item.get("open_url") or "").strip()

        if _blob_id_from_feed_url(item.get("url")):
            base_url = (self.base_url or "").strip().rstrip("/")
            if base_url.startswith(("http://", "https://")):
                return (
                    f"{action} {title}{id_text} to your AutoYou page feed on this computer. "
                    f"Open it in Page Feed: [Page Feed]({base_url}/)"
                )
            return (
                f"{action} {title}{id_text} to your AutoYou page feed on this computer. "
                "Open the AutoYou Browser tab and choose Page Feed to view it."
            )

        if view_url:
            return f"{action} {title}{id_text} to your AutoYou page feed. Link: [{title}]({view_url})"
        return f"{action} {title}{id_text} to your AutoYou page feed."

    def _ingest_summary_message(self, items: List[Dict[str, Any]], errors: List[str], skipped: List[Dict[str, Any]]) -> str:
        if not items:
            return "No attachments were saved to the AutoYou page feed."
        if len(items) == 1:
            return self._saved_item_message(items[0], action="Saved")
        blob_items = [item for item in items if isinstance(item, dict) and _blob_id_from_feed_url(item.get("url"))]
        if blob_items:
            message = f"Saved {len(items)} items to your AutoYou page feed on this computer."
            base_url = (self.base_url or "").strip().rstrip("/")
            if base_url.startswith(("http://", "https://")):
                message += f" Open them in Page Feed: [Page Feed]({base_url}/)"
            else:
                message += " Open the AutoYou Browser tab and choose Page Feed to view them."
        else:
            message = f"Added {len(items)} items to your AutoYou page feed."
        if errors:
            message += f" {len(errors)} item(s) still failed."
        elif skipped:
            message += f" {len(skipped)} item(s) were skipped."
        return message

    # --- URL helpers ---
    @staticmethod
    def normalize_x_url(url: str) -> str:
        raw = str(url or '').strip()
        if not raw:
            return raw
        try:
            parsed = urlparse(raw)
            host = (parsed.hostname or '').strip().lower()
            if host not in _X_HOST_ALIASES:
                return raw
            return urlunparse(parsed._replace(
                scheme=parsed.scheme or 'https',
                netloc='x.com',
                query='',
                fragment='',
            ))
        except Exception:
            return raw

    @staticmethod
    def normalize_source_label(value: str) -> str:
        raw = str(value or '').strip()
        if not raw:
            return ""
        lower = raw.lower()
        if lower in _X_HOST_ALIASES:
            return "x.com"
        try:
            host = (urlparse(raw).hostname or '').strip().lower()
            if host in _X_HOST_ALIASES:
                return "x.com"
        except Exception:
            pass
        return raw

    @staticmethod
    def normalize_threads_url(url: str) -> str:
        raw = str(url or '').strip()
        if not raw:
            return raw
        try:
            parsed = urlparse(raw)
            host = (parsed.hostname or '').strip().lower()
            if 'threads.net' not in host and 'threads.com' not in host:
                return raw
            path = parsed.path or '/'
            if not path.endswith('/'):
                path += '/'
            return urlunparse(parsed._replace(
                scheme=parsed.scheme or 'https',
                path=path,
                query='',
                fragment='',
            ))
        except Exception:
            return raw

    def classify_url(self, url: str) -> str:
        """Classify the URL into a feed item type (article/video/instagram/etc.)."""
        u = (url or '').lower()
        if "youtube.com" in u or "youtu.be" in u:
            return "youtube"
        if "tiktok.com" in u:
            return "tiktok"
        if "threads.net" in u or "threads.com" in u:
            return "threads"
        if "instagram.com" in u:
            return "instagram"
        if "twitter.com" in u or "x.com" in u:
            return "twitter"
        if any(x in u for x in [".mp4", "/shorts/", "hls", "/video/"]):
            return "video"
        return "article"

    def default_source(self, url: str) -> str:
        """Extract hostname to use as source label."""
        try:
            return self.normalize_source_label(urlparse(self.normalize_x_url(url)).hostname or "")
        except Exception:
            return ""

    def get_title(self, url: str, timeout: float = 2.5) -> Optional[str]:
        """Fetch the page and attempt to extract <title> (best-effort)."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AutoYou)"})
            with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
                raw = resp.read(65536)
                text = raw.decode("utf-8", errors="ignore")
            import re
            m = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
            if m:
                title = m.group(1).strip()
                import re as _re
                title = _re.sub(r"\s+", " ", title)
                return title
            return None
        except Exception:
            return None

    # --- Core operations ---
    def add_link(self, url: str, title: Optional[str] = None, source: Optional[str] = None, item_type: Optional[str] = None) -> Dict[str, Any]:
        """Add a new item to the feed from a hyperlink.

        Returns the created item dict (with id) when successful.
        """
        url = str(url or '').strip()
        if not url:
            return {"success": False, "error": "url is required"}
        item_type = item_type or self.classify_url(url)
        if str(item_type or '').strip().lower() in {"twitter", "x"}:
            item_type = "twitter"
            url = self.normalize_x_url(url)
        elif str(item_type or '').strip().lower() == "threads":
            item_type = "threads"
            url = self.normalize_threads_url(url)
        source = self.normalize_source_label(source or self.default_source(url))

        if self.base_url:
            try:
                j = self._http_json("POST", "/api/feed", {"url": url, "type": item_type, "title": title or "", "source": source})
                item = self._enrich_item_for_user_display(j.get("item"))
                if item:
                    return {
                        "success": True,
                        "item": item,
                        "message": self._saved_item_message(item, action="Added"),
                    }
                return {"success": False, "error": "unexpected response"}
            except Exception as e:
                return {"success": False, "error": str(e)}

        # DB fallback
        if self._db is None:
            return {"success": False, "error": "database unavailable"}
        try:
            item = self._enrich_item_for_user_display(
                self._db.insert(item_type=item_type, url=url, title=title or "", source=source or "")
            )
            return {
                "success": True,
                "item": item,
                "message": self._saved_item_message(item, action="Added"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_blob(
        self,
        filename: str,
        data_base64: str,
        mimetype: Optional[str] = None,
        *,
        source: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a binary blob and register it in the page feed.

        Accepts raw base64 or a data URL (e.g. `data:image/png;base64,...`).
        Normalizes base64 (whitespace-stripped, padded) and infers mimetype/filename
        when missing.

        In HTTP mode, uploads bytes to `/api/blob` then inserts a feed item with
        `blob://{uuid}` and source `Local`. In DB mode, writes file to `uploads/`.
        """
        try:
            raw = str(data_base64 or "").strip()

            def _parse_data_url(s: str) -> tuple[Optional[str], Optional[str]]:
                try:
                    if s.lower().startswith("data:"):
                        head, _, tail = s.partition(",")
                        mt = None
                        if ";base64" in head:
                            mt = head[5:].split(";")[0] or None
                        return tail, (mt or None)
                    return s, None
                except Exception:
                    return s, None

            def _normalize_b64(s: str) -> str:
                s2 = "".join((s or "").split())
                if not s2:
                    return ""
                rem = len(s2) % 4
                if rem:
                    s2 += "=" * (4 - rem)
                return s2

            # Handle data URLs and normalize base64
            b64, mt_from_data = _parse_data_url(raw)
            b64_norm = _normalize_b64(b64 or "")
            if not b64_norm:
                return {"success": False, "error": "empty base64"}

            # Decode robustly
            data_bytes: bytes
            try:
                data_bytes = base64.b64decode(b64_norm, validate=True)
            except Exception:
                try:
                    data_bytes = base64.b64decode(b64_norm, validate=False)
                except Exception as e:
                    return {"success": False, "error": f"invalid base64: {e}"}

            # Infer mimetype and filename if missing
            mt = _normalize_attachment_mimetype(
                filename=filename,
                mimetype_hint=(mimetype or mt_from_data),
                metadata=metadata,
            )
            ext_hint = _extension_for_mimetype(mt)
            safe_name = filename or f"attachment{ext_hint or ''}"
            try:
                import os
                base = os.path.basename(safe_name) or "attachment"
                base = base.replace(" ", "_")
                # Only append an extension if the filename currently has none
                cur_ext = os.path.splitext(base)[1].lower()
                if not cur_ext and ext_hint:
                    base = f"{base}{ext_hint}"
                safe_name = base
            except Exception:
                pass

            # Classify type
            mt_l = (mt or "application/octet-stream").lower()
            item_type = _classify_blob_item_type(mt_l)

            # HTTP mode: upload then create feed item
            if self.base_url:
                up = self._http_upload_blob(filename=safe_name, data_bytes=data_bytes, mimetype=mt_l)
                blob_id = up.get("id")
                if not blob_id:
                    return {"success": False, "error": "blob upload failed"}
                final_title = title or safe_name
                j = self._http_json("POST", "/api/feed", {"url": f"blob://{blob_id}", "type": item_type, "title": final_title, "source": "Local"})
                item = self._enrich_item_for_user_display(j.get("item"))
                if item:
                    return {
                        "success": True,
                        "item": item,
                        "message": self._saved_item_message(item),
                    }
                return {"success": False, "error": "unexpected response creating feed item"}

            # DB mode: persist file and insert feed item
            if self._db is None:
                return {"success": False, "error": "database unavailable"}
            import os, time
            uploads_dir = self._uploads_dir
            os.makedirs(uploads_dir, exist_ok=True)
            ts = int(time.time() * 1000)
            base_root, base_ext = os.path.splitext(safe_name)
            if title:
                safe_title = "".join([c if c.isalnum() or c in ".-_" else "_" for c in str(title)])[:120] or base_root
                store_name = f"{safe_title}{base_ext}" if base_ext else safe_title
            else:
                store_name = safe_name
            target_path = os.path.join(uploads_dir, f"{ts}_{store_name}")
            write_secure_file(target_path, data_bytes)
            meta = self._db.insert_blob(filename=store_name, mimetype=mt_l, size=int(len(data_bytes)), path=target_path)
            blob_id = meta.get("id")
            final_title = title or safe_name
            item = self._enrich_item_for_user_display(
                self._db.insert(item_type=item_type, url=f"blob://{blob_id}", title=final_title, source="Local")
            )
            return {
                "success": True,
                "item": item,
                "message": self._saved_item_message(item),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_blob_from_path(
        self,
        path: str,
        filename: Optional[str] = None,
        mimetype: Optional[str] = None,
        *,
        source: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a blob from a local filesystem path and register it in the page feed.

        - Validates the path exists, reads bytes, infers mimetype/filename when missing.
        - HTTP mode uploads bytes to `/api/blob` and inserts a feed item.
        - DB mode copies the file into `uploads/` and registers it, preserving original name.
        """
        try:
            import os
            # Normalize common issues: stray quotes, whitespace, mixed separators
            raw_path = str(path or "").strip().strip('"').strip("'")
            norm_path = os.path.normpath(raw_path)

            exists = os.path.exists(norm_path)
            is_dir = os.path.isdir(norm_path)
            LOGGER.info(
                "add_blob_from_path: resolving path input='%s' norm='%s' exists=%s is_dir=%s filename='%s'",
                raw_path,
                norm_path,
                exists,
                is_dir,
                (filename or "")
            )

            if not exists:
                # If the provided path looks like a directory accidentally (e.g., missing filename),
                # try to join with the provided filename to locate the actual file.
                # Also handle cases where norm_path ends with a separator.
                candidate = None
                try:
                    # Prefer the directory part if available
                    dir_part = norm_path
                    if not os.path.isdir(dir_part):
                        dir_part = os.path.dirname(norm_path)
                    if dir_part and os.path.isdir(dir_part) and filename:
                        candidate = os.path.normpath(os.path.join(dir_part, filename))
                        if os.path.exists(candidate):
                            norm_path = candidate
                            exists = True
                            is_dir = False
                            LOGGER.info("add_blob_from_path: adjusted to candidate file '%s'", norm_path)
                except Exception:
                    pass

            if not exists or is_dir:
                # If still not resolved or points to a directory, surface a clear error
                return {"success": False, "error": f"path not readable: {norm_path}"}

            # Read bytes
            with open(norm_path, "rb") as f:
                raw_bytes = f.read()
            data_bytes = read_secure_file(norm_path) if raw_bytes.startswith(SPM_FILE_HEADER) else raw_bytes

            # Infer mimetype and filename if missing
            mt_hint = _normalize_attachment_mimetype(
                filename=filename or os.path.basename(norm_path),
                mimetype_hint=mimetype,
                metadata=metadata,
                path=norm_path,
            )

            # Safe filename
            base_name = (filename or os.path.basename(norm_path) or "attachment").replace(" ", "_")
            # If extension missing, attempt to add from mimetype
            import os as _os
            if not _os.path.splitext(base_name)[1]:
                ext = _extension_for_mimetype(mt_hint)
                if ext:
                    base_name = f"{base_name}{ext}"

            # Classify item type
            mt_l = (mt_hint or "application/octet-stream").lower()
            item_type = _classify_blob_item_type(mt_l)

            # HTTP mode: upload then create feed item
            if self.base_url:
                up = self._http_upload_blob(filename=base_name, data_bytes=data_bytes, mimetype=mt_l)
                blob_id = up.get("id")
                if not blob_id:
                    return {"success": False, "error": "blob upload failed"}
                final_title = title or base_name
                j = self._http_json("POST", "/api/feed", {"url": f"blob://{blob_id}", "type": item_type, "title": final_title, "source": (source or "Local")})
                item = self._enrich_item_for_user_display(j.get("item"))
                if item:
                    return {
                        "success": True,
                        "item": item,
                        "message": self._saved_item_message(item),
                    }
                return {"success": False, "error": "unexpected response creating feed item"}

            # DB mode: copy file to uploads and insert feed item
            if self._db is None:
                return {"success": False, "error": "database unavailable"}
            import time
            uploads_dir = self._uploads_dir
            os.makedirs(uploads_dir, exist_ok=True)
            ts = int(time.time() * 1000)
            base_root, base_ext = os.path.splitext(base_name)
            if title:
                safe_title = "".join([c if c.isalnum() or c in ".-_" else "_" for c in str(title)])[:120] or base_root
                store_name = f"{safe_title}{base_ext}" if base_ext else safe_title
            else:
                store_name = base_name
            target_path = os.path.join(uploads_dir, f"{ts}_{store_name}")
            # Copy bytes
            write_secure_file(target_path, data_bytes)
            meta = self._db.insert_blob(filename=store_name, mimetype=mt_l, size=int(len(data_bytes)), path=target_path)
            blob_id = meta.get("id")
            final_title = title or base_name
            item = self._enrich_item_for_user_display(
                self._db.insert(item_type=item_type, url=f"blob://{blob_id}", title=final_title, source=(source or "Local"))
            )
            return {
                "success": True,
                "item": item,
                "message": self._saved_item_message(item),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def ingest_attachments(
        self,
        attachments: List[Dict[str, Any]],
        *,
        source: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        default_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest a list of attachments and register them in the page feed.

        - For URL-only items, uses add_link.
        - For data-backed items (base64 + filename/mimetype), uses add_blob.
        - Persists bytes via NotesTool when needed (handled inside add_blob).

        Returns a summary dict with lists of created items and any errors.
        """
        items: List[Dict[str, Any]] = []
        saved_notes: List[Dict[str, Any]] = []  # populated when add_blob persists bytes
        skipped: List[Dict[str, Any]] = []
        errors: List[str] = []

        for att in attachments or []:
            try:
                if not isinstance(att, dict):
                    skipped.append({"reason": "non_dict", "value": str(att)})
                    continue

                filename = str(att.get("filename") or "attachment")
                data_b64 = att.get("data")
                path = att.get("path")
                mimetype = att.get("mimetype")
                title = att.get("title") or default_title or filename

                # URL-only registration
                url = str(att.get("url") or "").strip()
                if url and not data_b64:
                    if url.lower().startswith("data:"):
                        # Treat data URLs as blobs
                        res = self.add_blob(
                            filename=filename,
                            data_base64=url,
                            mimetype=mimetype,
                            source=source,
                            user_id=user_id,
                            session_id=session_id,
                            message_id=message_id,
                            metadata={k: v for k, v in att.items() if k not in {"filename", "mimetype", "data", "url", "title"}},
                            title=title,
                        )
                        if res.get("success"):
                            item = res.get("item")
                            if item:
                                items.append(self._enrich_item_for_user_display(item) or item)
                        else:
                            errors.append(f"add_blob failed for data URL '{filename}': {res.get('error')}")
                    else:
                        res = self.add_link(url=url, title=title, source=source, item_type=None)
                        if res.get("success"):
                            item = res.get("item")
                            enriched = self._enrich_item_for_user_display(item) if item else {"url": url}
                            items.append(enriched or {"url": url})
                        else:
                            errors.append(f"add_link failed for '{url}': {res.get('error')}")
                    continue

                # Path-backed blob registration
                if path and not data_b64:
                    # Normalize and adjust directory-only paths by appending filename
                    try:
                        import os as _os
                        import tempfile as _tempfile
                        raw_p = str(path).strip().strip('"').strip("'")
                        norm_p = _os.path.normpath(raw_p)
                        if _os.path.isdir(norm_p) and filename:
                            candidate = _os.path.normpath(_os.path.join(norm_p, filename))
                            path = candidate
                        # If the path does not exist and looks like a bare filename,
                        # attempt resolution into the temp autoyou_media directory used by REST API.
                        if not _os.path.exists(str(path)):
                            base_candidates = []
                            try:
                                troot = _os.path.join(_tempfile.gettempdir(), "autoyou_media")
                                # Build most-specific path first: source/user/session
                                if source and user_id and session_id:
                                    base_candidates.append(_os.path.join(troot, str(source), str(user_id), str(session_id), filename))
                                # Then source/user
                                if source and user_id:
                                    base_candidates.append(_os.path.join(troot, str(source), str(user_id), filename))
                                # Then source only
                                if source:
                                    base_candidates.append(_os.path.join(troot, str(source), filename))
                                # Try candidates
                                for cand in base_candidates:
                                    c_norm = _os.path.normpath(cand)
                                    if _os.path.exists(c_norm):
                                        LOGGER.info(
                                            "ingest_attachments: resolved missing path via temp dir -> '%s'",
                                            c_norm,
                                        )
                                        path = c_norm
                                        break
                            except Exception:
                                pass
                        LOGGER.info(
                            "ingest_attachments: path-backed item filename='%s' path='%s' exists=%s",
                            filename,
                            path,
                            _os.path.exists(str(path))
                        )
                    except Exception:
                        pass
                    meta = {k: v for k, v in att.items() if k not in {"filename", "mimetype", "data", "url", "title", "path"}}
                    res = self.add_blob_from_path(
                        path=str(path),
                        filename=filename,
                        mimetype=mimetype,
                        source=source,
                        user_id=user_id,
                        session_id=session_id,
                        message_id=message_id,
                        metadata=meta,
                        title=title,
                    )
                    if res.get("success"):
                        item = res.get("item")
                        if item:
                            items.append(self._enrich_item_for_user_display(item) or item)
                    else:
                        errors.append(f"add_blob_from_path failed for '{filename}' @ {path}: {res.get('error')}")
                    continue

                # Data-backed blob registration
                if data_b64:
                    meta = {k: v for k, v in att.items() if k not in {"filename", "mimetype", "data", "url", "title"}}
                    res = self.add_blob(
                        filename=filename,
                        data_base64=str(data_b64),
                        mimetype=mimetype,
                        source=source,
                        user_id=user_id,
                        session_id=session_id,
                        message_id=message_id,
                        metadata=meta,
                        title=title,
                    )
                    if res.get("success"):
                        item = res.get("item")
                        if item:
                            items.append(self._enrich_item_for_user_display(item) or item)
                        # add_blob already persists bytes; expose minimal note-save info when available
                    else:
                        errors.append(f"add_blob failed for '{filename}': {res.get('error')}")
                    continue

                skipped.append({"reason": "no_url_or_data", "filename": filename})
            except Exception as e:
                errors.append(f"ingest error for '{att}': {e}")

        return {
            "success": True,
            "items": items,
            "saved_notes": saved_notes,
            "skipped": skipped,
            "errors": errors,
            "message": self._ingest_summary_message(items, errors, skipped),
        }

    def delete_item(self, item_id: int) -> Dict[str, Any]:
        """Delete a feed item by id."""
        if self.base_url:
            try:
                j = self._http_json("DELETE", f"/api/feed/{int(item_id)}")
                return {
                    "success": True,
                    "deleted": int(j.get("deleted") or 0),
                    "cleanup": j.get("cleanup") or {},
                }
            except Exception as e:
                return {"success": False, "error": str(e)}
        if self._db is None:
            return {"success": False, "error": "database unavailable"}
        try:
            # Use cleanup-aware deletion in DB mode
            uploads_dir = self._uploads_dir
            summary = self._db.delete_item_and_cleanup(int(item_id), uploads_dir=uploads_dir)
            return {
                "success": True,
                "deleted": int(summary.get("deleted") or 0),
                "cleanup": summary,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def clear_feed(self) -> Dict[str, Any]:
        """Clear all feed items."""
        if self.base_url:
            try:
                j = self._http_json("GET", "/api/feed/clear")
                return {
                    "success": True,
                    "count": int(j.get("count") or 0),
                    "cleanup": j.get("cleanup") or {},
                }
            except Exception as e:
                return {"success": False, "error": str(e)}
        if self._db is None:
            return {"success": False, "error": "database unavailable"}
        try:
            # Use cleanup-aware clear in DB mode
            uploads_dir = self._uploads_dir
            summary = self._db.clear_all_and_cleanup(uploads_dir)
            # Reset sequence for clean IDs
            try:
                self._db.reset_sequence()
            except Exception:
                pass
            return {
                "success": True,
                "count": int(summary.get("items_deleted") or 0),
                "cleanup": summary,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_feed(self, days: int = 0, limit: Optional[int] = None) -> Dict[str, Any]:
        """List recent items (default: all items using days=0)."""
        if self.base_url:
            try:
                path = f"/api/feed?days={days}"
                if limit is not None:
                    path += f"&limit={limit}"
                j = self._http_json("GET", path)
                items = j.get("items") or []
                return {"success": True, "items": items}
            except Exception as e:
                return {"success": False, "error": str(e)}
        if self._db is None:
            return {"success": False, "error": "database unavailable"}
        try:
            if days > 0:
                items = self._db.load_recent_desc(days=days, limit=limit)
            else:
                items = self._db.query_items(order="desc", ignore_date_default=True, limit=limit)
            return {"success": True, "items": items}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def query_feed(
        self,
        order: str = "desc",
        types: Optional[List[str]] = None,
        source: Optional[str] = None,
        sources: Optional[List[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        favourites_only: bool = False,
        tag_search: Optional[str] = None,
        limit: Optional[int] = None,
        timeline_all: bool = False,
    ) -> Dict[str, Any]:
        """Query feed items with optional filters and sorting."""
        normalized_sources = [str(item).strip() for item in (sources or []) if str(item).strip()] or None
        if self.base_url:
            try:
                params = {
                    "order": (order or "desc").lower(),
                }
                if favourites_only:
                    params["favourites_only"] = "1"
                if types:
                    params["types"] = ",".join([t.strip().lower() for t in types if t.strip()])
                if normalized_sources:
                    params["sources"] = ",".join(normalized_sources)
                elif source:
                    params["source"] = str(source)
                if tag_search:
                    params["tag_search"] = str(tag_search)
                if date_from:
                    params["date_from"] = str(date_from)
                if date_to:
                    params["date_to"] = str(date_to)
                if limit is not None:
                    params["limit"] = str(limit)
                if timeline_all:
                    params["timeline_all"] = "1"
                qs = urlencode(params)
                j = self._http_json("GET", f"/api/feed/query{('?' + qs) if qs else ''}")
                items = j.get("items") or []
                return {"success": True, "items": items}
            except Exception as e:
                return {"success": False, "error": str(e)}
        if self._db is None:
            return {"success": False, "error": "database unavailable"}
        try:
            items = self._db.query_items(
                order=(order or "desc").lower(),
                types=types,
                source=(None if normalized_sources else source),
                sources=normalized_sources,
                date_from=date_from,
                date_to=date_to,
                favourites_only=bool(favourites_only),
                tag_search=tag_search,
                limit=limit,
                ignore_date_default=bool(timeline_all),
            )
            return {"success": True, "items": items}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_favourite(self, item_id: int, favourite: bool) -> Dict[str, Any]:
        """Set or unset favourite flag for an item."""
        if self.base_url:
            try:
                j = self._http_json("POST", f"/api/item/{int(item_id)}/favourite", {"favourite": bool(favourite)})
                return {"success": True, "ok": bool(j.get("ok"))}
            except Exception as e:
                return {"success": False, "error": str(e)}
        if self._db is None:
            return {"success": False, "error": "database unavailable"}
        try:
            ok = self._db.set_favourite(int(item_id), bool(favourite))
            return {"success": True, "ok": bool(ok)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_tag(self, item_id: int, tag: str) -> Dict[str, Any]:
        """Add a tag to an item."""
        tag_norm = (tag or "").strip()
        if not tag_norm:
            return {"success": False, "error": "tag is required"}
        if self.base_url:
            try:
                j = self._http_json("POST", f"/api/item/{int(item_id)}/tags", {"tag": tag_norm})
                return {"success": True, "ok": bool(j.get("ok"))}
            except Exception as e:
                return {"success": False, "error": str(e)}
        if self._db is None:
            return {"success": False, "error": "database unavailable"}
        try:
            uploads_dir = self._uploads_dir
            ok = self._db.add_tag(int(item_id), tag_norm, uploads_dir=uploads_dir)
            return {"success": True, "ok": bool(ok)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_tag(self, item_id: int, tag: str) -> Dict[str, Any]:
        """Delete a tag from an item."""
        tag_norm = (tag or "").strip()
        if not tag_norm:
            return {"success": False, "error": "tag is required"}
        if self.base_url:
            try:
                j = self._http_json("DELETE", f"/api/item/{int(item_id)}/tags/{quote(tag_norm, safe='')}")
                return {"success": True, "ok": bool(j.get("ok"))}
            except Exception as e:
                return {"success": False, "error": str(e)}
        if self._db is None:
            return {"success": False, "error": "database unavailable"}
        try:
            uploads_dir = self._uploads_dir
            ok = self._db.delete_tag(int(item_id), tag_norm, uploads_dir=uploads_dir)
            return {"success": True, "ok": bool(ok)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_tags(self, item_id: int) -> Dict[str, Any]:
        """List tags for an item (DB only)."""
        if self.base_url:
            # No explicit HTTP endpoint for listing tags; rely on DB if available
            LOGGER.debug("list_tags via HTTP not supported; requires DB access")
        if self._db is None:
            return {"success": False, "error": "database unavailable"}
        try:
            tags = self._db.list_tags(int(item_id))
            return {"success": True, "tags": tags}
        except Exception as e:
            return {"success": False, "error": str(e)}
