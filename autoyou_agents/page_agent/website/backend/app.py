# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-425a59663352447455546d73-59eaa94531dec4852571267d

"""AutoYou Page feed website backend.

The page feed UI/API, migrated out of the dual-role ``autoyou_page_service``
into a standard managed-frontend backend that reuses ``page_feed_db.PageFeedDB``.
``autoyou_page_service`` keeps only the shared agent-website proxy host at 8067.
The feed helper methods and routes below carry the blob/SSRF hardening plus a
remote WebRTC write guard for feed/blob/item mutation routes.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-425a59663352447455546d73-59eaa94531dec4852571267d"


import asyncio
import base64
import hashlib
import html
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.request
from contextlib import suppress
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote, urlparse

import requests

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from shared.ui_theme import get_ui_theme, normalize_ui_theme, set_ui_theme
from autoyou_agents.shared_tools.scheduler_mission_control import install_agent_website_auth
from shared.remote_access_policy import (
    REMOTE_BROWSER_HEADER,
    normalize_remote_access_role,
    remote_access_denial_message,
    remote_http_request_allowed,
)
from shared.media_messaging import is_adts_aac
from shared.url_safety import (
    UnsafeURLError,
    assert_safe_http_url,
    build_safe_httpx_transport,
    is_safe_http_url,
    safe_follow_redirects,
)
from shared.secure_storage import (
    FILE_HEADER as SPM_FILE_HEADER,
    read_secure_file,
    secure_storage_enabled,
    write_secure_file,
)

LOGGER = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"
PAGE_ASSETS = {
    "page.css": (FRONTEND_DIR / "assets" / "page.css", "text/css; charset=utf-8"),
    "page.js": (FRONTEND_DIR / "assets" / "page.js", "text/javascript; charset=utf-8"),
    "autoyou-mark.svg": (FRONTEND_DIR / "assets" / "autoyou-mark.svg", "image/svg+xml"),
}
REMOTE_WRITE_DENIAL_MESSAGE = "Page feed write actions are only available from the local owner browser."
FEED_PAGE_SIZE = 20
MAX_FEED_PAGE_SIZE = 100
TOP_TAG_LIMIT = 8
# get_configured_server_name() returns this when the owner never named the server.
DEFAULT_SERVER_NAME = "AutoYou-Server"
_PROFILE_PHOTO_TYPES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

# How the page describes each stored type: (label, type tab, accent, icon).
# Accents and icons are classes in frontend/assets/page.css.
_ITEM_KINDS: Dict[str, tuple[str, str, str, str]] = {
    "article": ("Article", "articles", "amber", "globe"),
    "youtube": ("YouTube", "videos", "blue", "play"),
    "video": ("Video", "videos", "blue", "play"),
    "tiktok": ("TikTok", "social", "teal", "play"),
    "twitter": ("X post", "social", "slate", "chat"),
    "instagram": ("Instagram", "social", "rose", "image"),
    "threads": ("Threads", "social", "slate", "chat"),
    "image": ("Photo", "photos", "green", "image"),
    "gif": ("GIF", "photos", "green", "image"),
    "audio": ("Audio", "audio", "purple", "wave"),
    "document": ("Document", "files", "slate", "doc"),
    "text": ("Text", "files", "slate", "doc"),
    "file": ("File", "files", "slate", "doc"),
}
_SOCIAL_TILE_LABELS = {"twitter": "X", "instagram": "Instagram", "threads": "Threads", "tiktok": "TikTok"}
# Type tabs on the page and the stored types each one shows.
FEED_TYPE_TABS: Dict[str, List[str]] = {
    "latest": [],
    "articles": ["article"],
    "videos": ["video", "youtube", "tiktok"],
    "social": ["twitter", "instagram", "threads", "tiktok"],
    "photos": ["image", "gif"],
    "audio": ["audio"],
    "files": ["document", "text", "file"],
}


def _canonical_item_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "x":
        return "twitter"
    return normalized or "article"


def _parse_local_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _relative_day(value: Any, now: Optional[datetime] = None) -> str:
    """Match relativeDay() in assets/page.js so the first paint does not change."""
    parsed = _parse_local_timestamp(value)
    if parsed is None:
        return ""
    today = (now or datetime.now()).date()
    day = parsed.date()
    if day == today:
        return "Today"
    if (today - day).days == 1:
        return "Yesterday"
    label = f"{parsed.strftime('%b')} {day.day}"
    return label if day.year == today.year else f"{label}, {day.year}"


def _encode_feed_cursor(added_at: Any, item_id: Any) -> Optional[str]:
    if not added_at or item_id is None:
        return None
    raw = f"{added_at}\n{int(item_id)}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_feed_cursor(cursor: Any) -> tuple[Optional[str], Optional[int]]:
    raw = str(cursor or "").strip()
    if not raw:
        return (None, None)
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        added_at, item_id = decoded.split("\n", 1)
        return (added_at or None, int(item_id))
    except Exception:
        return (None, None)


def _asset_version(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "0"
def _is_internal_page_tool_request(request: Request) -> bool:
    """Allow the local agent tool without weakening browser OTP isolation."""
    client_host = str(getattr(request.client, "host", "") or "").strip().lower()
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return False
    if request.headers.get("Origin") or request.headers.get("Referer"):
        return False
    expected_token = str(os.getenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "") or "").strip()
    authorization = str(request.headers.get("Authorization", "") or "").strip()
    if not expected_token or not authorization.lower().startswith("bearer "):
        return False
    provided_token = authorization[7:].strip()
    return bool(provided_token and secrets.compare_digest(provided_token, expected_token))

def _no_store_html_response(content: str, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(content=content, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

class PageFeedService:
    """Feed UI/API (extracted verbatim from ``AutoYouPageService``)."""

    def __init__(self, timeline_days: int = 0):
        # Fallback window when no server configuration is loaded; 0 is the entire feed.
        self.timeline_days = max(0, int(timeline_days))
        self.feed_items: list[Dict[str, Any]] = []
        self._next_id: int = 1
        try:
            from shared.platform_runtime import get_config_dir
            from pathlib import Path
            module_dir = Path(__file__).resolve().parent
            runtime_anchor = module_dir
            for parent in module_dir.parents:
                if (parent / "server.py").is_file() and (parent / "autoyou_agents").is_dir():
                    runtime_anchor = parent
                    break
            config_dir = str(get_config_dir("AutoYou", anchor=runtime_anchor))

            from page_feed_db import PageFeedDB
            self.db = PageFeedDB(db_path=os.path.join(config_dir, "page_feed.db"))
        except Exception as e:
            self.db = None
            LOGGER.error(f"Failed to initialize page_feed.db: {e}")
            config_dir = os.getcwd()

        try:
            self.uploads_dir = os.path.join(config_dir, "uploads")
            os.makedirs(self.uploads_dir, exist_ok=True)
        except Exception as e:
            self.uploads_dir = os.getcwd()
            LOGGER.error(f"Failed to prepare uploads directory: {e}")
        self.config_dir = config_dir

        try:
            if self.db is not None:
                self.feed_items = self.db.load_all()
                self._next_id = (self.db.max_id() + 1)
        except Exception as e:
            LOGGER.error(f"Failed to load feed from DB: {e}")
            self.feed_items = []
            self._next_id = 1

    def _get_ui_theme(self) -> str:
        return get_ui_theme(app_name="AutoYou", anchor=__file__, default="dark")

    def _set_ui_theme(self, theme: Any) -> str:
        return set_ui_theme(normalize_ui_theme(theme), app_name="AutoYou", anchor=__file__)

    @staticmethod
    def _read_blob_bytes(path: str | Path) -> bytes:
        candidate = Path(path)
        return read_secure_file(candidate)

    @classmethod
    def _blob_plain_size(cls, path: str | Path) -> int:
        candidate = Path(path)
        if not candidate.is_file():
            return 0
        if not secure_storage_enabled():
            return int(candidate.stat().st_size)
        return len(cls._read_blob_bytes(candidate))

    @staticmethod
    def _seal_blob_path(path: str | Path) -> None:
        candidate = Path(path)
        if not candidate.is_file() or not secure_storage_enabled():
            return
        raw = candidate.read_bytes()
        if raw.startswith(SPM_FILE_HEADER):
            return
        write_secure_file(candidate, raw)

    @staticmethod
    def _serialize_inline_json(payload: Any) -> str:
        # Escape markup characters so saved titles can never close the script tag.
        return json.dumps(payload).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    @staticmethod
    def _blob_api_url(blob_id: str, *, preview: bool = False) -> str:
        suffix = "?preview=1" if preview else ""
        return f"./api/blob/{quote(str(blob_id or ''), safe='')}{suffix}"

    def _window_days(self) -> int:
        """Days of feed shown by default; 0 shows the entire feed.

        Read per request from Admin's ``autoyou_page.feed_window_days`` so a
        change applies without restarting the website.
        """
        server_module = sys.modules.get("server")
        config = getattr(getattr(server_module, "STATE", None), "config", None)
        page_config = config.get("autoyou_page") if isinstance(config, dict) else None
        if isinstance(page_config, dict) and page_config.get("feed_window_days") is not None:
            with suppress(TypeError, ValueError):
                return max(0, min(3650, int(page_config.get("feed_window_days"))))
        return self.timeline_days

    @staticmethod
    def _owner_photo_path() -> Optional[Path]:
        """The Admin profile photo; this website runs inside the server process."""
        finder = getattr(sys.modules.get("server"), "_get_admin_profile_image_path", None)
        if not callable(finder):
            return None
        try:
            found = finder()
        except Exception as exc:
            LOGGER.debug("Admin profile photo unavailable: %s", exc)
            return None
        path = Path(found) if found else None
        return path if path is not None and path.suffix.lower() in _PROFILE_PHOTO_TYPES else None

    def _owner_profile(self) -> Dict[str, Any]:
        """The server's name and Admin profile photo, shown as this Page's profile.

        Reading them in-process means trusted contacts see the owner's photo
        without an Admin session. Without a photo the Page shows the AutoYou mark.
        """
        name = ""
        namer = getattr(sys.modules.get("server"), "get_configured_server_name", None)
        if callable(namer):
            with suppress(Exception):
                name = str(namer() or "").strip()[:64]
        if name == DEFAULT_SERVER_NAME:
            name = ""
        mark_url = f"./assets/autoyou-mark.svg?v={_asset_version(PAGE_ASSETS['autoyou-mark.svg'][0])}"
        photo_url = ""
        photo = self._owner_photo_path()
        if photo is not None:
            with suppress(OSError):
                photo_url = f"./api/profile/avatar?v={photo.stat().st_mtime_ns}"
        return {"name": name, "avatar_url": photo_url or mark_url, "mark_url": mark_url, "has_photo": bool(photo_url)}

    def _viewer_access(self, request: Request) -> Dict[str, Any]:
        """What this browser may do under the shared remote role policy."""
        if not self._is_remote_browser_request(request):
            return {"role": "owner", "remote": False, "can_add": True, "can_edit": True, "can_delete": True, "can_manage": True}
        role = normalize_remote_access_role(request.headers.get("X-AutoYou-Remote-Access-Role"))
        return {
            "role": role,
            "remote": True,
            "can_add": remote_http_request_allowed(role, "POST", "/api/feed"),
            "can_edit": remote_http_request_allowed(role, "POST", "/api/item/0/favourite"),
            "can_delete": remote_http_request_allowed(role, "DELETE", "/api/feed/0"),
            "can_manage": remote_http_request_allowed(role, "GET", "/api/feed/clear"),
        }

    def _feed_page(
        self,
        *,
        order: str = "desc",
        types: Optional[List[str]] = None,
        sources: Optional[List[str]] = None,
        favourites_only: bool = False,
        stored_only: bool = False,
        tag_search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        all_time: bool = False,
        cursor: Optional[str] = None,
        limit: int = FEED_PAGE_SIZE,
    ) -> Dict[str, Any]:
        """One page of the website's feed, continuing after ``cursor``."""
        bounded = max(1, min(int(limit or FEED_PAGE_SIZE), MAX_FEED_PAGE_SIZE))
        window = self._window_days()
        search = str(tag_search or "").strip() or None
        # Search spans the whole feed; otherwise Admin's window applies unless
        # the viewer asked for all of it or chose their own dates.
        if not all_time and not search and window > 0 and not date_from and not date_to:
            date_from = (datetime.now() - timedelta(days=window)).isoformat()
        cursor_added_at, cursor_id = _decode_feed_cursor(cursor)
        descending = str(order or "desc").lower() != "asc"
        if getattr(self, "db", None) is not None:
            rows = list(
                self.db.query_items(
                    order="desc" if descending else "asc",
                    types=types or None,
                    sources=sources or None,
                    date_from=date_from,
                    date_to=date_to,
                    favourites_only=favourites_only,
                    tag_search=search,
                    limit=bounded + 1,
                    ignore_date_default=True,
                    cursor_added_at=cursor_added_at,
                    cursor_id=cursor_id,
                    stored_only=stored_only,
                )
            )
        else:
            rows = self._filter_memory_items(
                descending=descending,
                types=types,
                sources=sources,
                favourites_only=favourites_only,
                stored_only=stored_only,
                search=search,
                date_from=date_from,
                date_to=date_to,
                cursor=(cursor_added_at, cursor_id),
            )[: bounded + 1]
        has_more = len(rows) > bounded
        rows = rows[:bounded]
        last = rows[-1] if rows else None
        return {
            "items": self._present_items(rows),
            "has_more": has_more,
            "next_cursor": _encode_feed_cursor(last.get("added_at"), last.get("id")) if has_more and last else None,
            "window_days": window,
        }

    def _filter_memory_items(
        self,
        *,
        descending: bool,
        types: Optional[List[str]],
        sources: Optional[List[str]],
        favourites_only: bool,
        stored_only: bool,
        search: Optional[str],
        date_from: Optional[str],
        date_to: Optional[str],
        cursor: tuple[Optional[str], Optional[int]],
    ) -> List[Dict[str, Any]]:
        """In-memory stand-in for PageFeedDB.query_items when the database is unavailable."""
        wanted_types = {_canonical_item_type(value) for value in (types or [])}
        wanted_sources = {str(value) for value in (sources or [])}
        needle = (search or "").lower()

        def matches(entry: Dict[str, Any]) -> bool:
            added_at = str(entry.get("added_at") or "")
            if wanted_types and _canonical_item_type(entry.get("type")) not in wanted_types:
                return False
            if wanted_sources and str(entry.get("source") or "") not in wanted_sources:
                return False
            if favourites_only and not entry.get("favourite"):
                return False
            if stored_only and not str(entry.get("url") or "").startswith("blob://"):
                return False
            if needle and needle not in str(entry.get("title") or "").lower() and not any(
                needle in str(tag).lower() for tag in entry.get("tags") or []
            ):
                return False
            if date_from and added_at < date_from:
                return False
            if date_to and added_at > date_to:
                return False
            return True

        def sort_key(entry: Dict[str, Any]) -> tuple[str, int]:
            return (str(entry.get("added_at") or ""), int(entry.get("id") or 0))

        items = sorted((entry for entry in self.feed_items if matches(entry)), key=sort_key, reverse=descending)
        cursor_added_at, cursor_id = cursor
        if cursor_added_at and cursor_id is not None:
            boundary = (str(cursor_added_at), int(cursor_id))
            items = [entry for entry in items if (sort_key(entry) < boundary if descending else sort_key(entry) > boundary)]
        return items

    def _page_summary(self) -> Dict[str, Any]:
        """Whole-feed totals, top tags, the latest favourite and a cover photo."""
        summary: Dict[str, Any] = {
            "total": 0,
            "favourites": 0,
            "sources": 0,
            "latest_id": None,
            "latest_added_at": None,
            "top_tags": [],
        }
        favourite_rows: List[Dict[str, Any]] = []
        cover_rows: List[Dict[str, Any]] = []
        try:
            if getattr(self, "db", None) is not None:
                summary.update(self.db.summary(tag_limit=TOP_TAG_LIMIT))
                favourite_rows = list(self.db.query_items(order="desc", favourites_only=True, limit=1, ignore_date_default=True))
                cover_rows = list(self.db.query_items(order="desc", types=["image"], limit=1, ignore_date_default=True))
            else:
                items = sorted(self.feed_items, key=lambda entry: (str(entry.get("added_at") or ""), int(entry.get("id") or 0)), reverse=True)
                summary.update(
                    {
                        "total": len(items),
                        "favourites": sum(1 for entry in items if entry.get("favourite")),
                        "sources": len({str(entry.get("source")) for entry in items if str(entry.get("source") or "").strip()}),
                        "latest_id": items[0].get("id") if items else None,
                        "latest_added_at": items[0].get("added_at") if items else None,
                    }
                )
                favourite_rows = [entry for entry in items if entry.get("favourite")][:1]
                cover_rows = [entry for entry in items if _canonical_item_type(entry.get("type")) == "image"][:1]
        except Exception as exc:
            LOGGER.warning("Failed to summarise the page feed: %s", exc)
        latest_favourite = self._present_items(favourite_rows)[0] if favourite_rows else None
        cover = None
        if cover_rows:
            cover_item = self._present_items(cover_rows)[0]
            if cover_item["view"]["thumb"]:
                cover = {"url": cover_item["view"]["thumb"], "item_id": cover_item.get("id")}
        return {**summary, "latest_favourite": latest_favourite, "cover": cover, "window_days": self._window_days()}

    @staticmethod
    def _youtube_id_from_url(url: str) -> str:
        try:
            parsed = urlparse(str(url or "").strip())
            host = (parsed.hostname or "").lower()
            if "youtu.be" in host:
                return parsed.path.lstrip("/")
            if "youtube.com" in host:
                query = parse_qs(parsed.query or "")
                video_id = (query.get("v") or [""])[0].strip()
                if video_id:
                    return video_id
                parts = [part for part in parsed.path.split("/") if part]
                if len(parts) >= 2 and parts[0] in {"embed", "shorts"}:
                    return parts[1]
        except Exception:
            return ""
        return ""

    @staticmethod
    def _normalize_x_url(url: str) -> str:
        raw = str(url or "").strip()
        if not raw:
            return raw
        try:
            parsed = urlparse(raw)
            host = (parsed.hostname or "").lower()
            if host not in {
                "twitter.com",
                "www.twitter.com",
                "mobile.twitter.com",
                "m.twitter.com",
                "x.com",
                "www.x.com",
                "mobile.x.com",
                "m.x.com",
            }:
                return raw
            return parsed._replace(
                scheme=parsed.scheme or "https",
                netloc="x.com",
                query="",
                fragment="",
            ).geturl()
        except Exception:
            return raw

    @staticmethod
    def _normalize_source_label(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        lower = raw.lower()
        if lower in {
            "twitter.com",
            "www.twitter.com",
            "mobile.twitter.com",
            "m.twitter.com",
            "x.com",
            "www.x.com",
            "mobile.x.com",
            "m.x.com",
        }:
            return "x.com"
        try:
            host = (urlparse(raw).hostname or "").strip().lower()
            if host in {
                "twitter.com",
                "www.twitter.com",
                "mobile.twitter.com",
                "m.twitter.com",
                "x.com",
                "www.x.com",
                "mobile.x.com",
                "m.x.com",
            }:
                return "x.com"
        except Exception:
            pass
        return raw

    @staticmethod
    def _normalize_threads_url(url: str) -> str:
        raw = str(url or "").strip()
        if not raw:
            return raw
        try:
            parsed = urlparse(raw)
            host = (parsed.hostname or "").lower()
            if "threads.net" not in host and "threads.com" not in host:
                return raw
            path = parsed.path or "/"
            if not path.endswith("/"):
                path += "/"
            return parsed._replace(
                scheme=parsed.scheme or "https",
                path=path,
                query="",
                fragment="",
            ).geturl()
        except Exception:
            return raw

    @staticmethod
    def _twitter_post_id_from_url(url: str) -> str:
        try:
            parsed = urlparse(PageFeedService._normalize_x_url(url))
            parts = [part for part in parsed.path.split("/") if part]
            status_index = parts.index("status") if "status" in parts else -1
            if status_index >= 0 and len(parts) > status_index + 1:
                return parts[status_index + 1]
        except Exception:
            return ""
        return ""

    @staticmethod
    def _twitter_embed_url_from_url(url: str, theme: str = "dark") -> str:
        post_id = PageFeedService._twitter_post_id_from_url(url)
        if not post_id:
            return ""
        normalized_theme = normalize_ui_theme(theme)
        return f"https://platform.twitter.com/embed/Tweet.html?id={post_id}&dnt=true&theme={normalized_theme}&conversation=none"

    @staticmethod
    def _instagram_embed_url_from_url(url: str) -> str:
        try:
            parsed = urlparse(str(url or "").strip())
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"p", "reel", "tv"}:
                return f"https://www.instagram.com/{parts[0]}/{parts[1]}/embed/"
        except Exception:
            return ""
        return ""

    @staticmethod
    def _host_label_from_url(url: str) -> str:
        try:
            parsed = urlparse(str(url or "").strip())
            return (parsed.hostname or "").replace("www.", "")
        except Exception:
            return ""

    @staticmethod
    def _favicon_url_from_url(url: str) -> str:
        try:
            parsed = urlparse(str(url or "").strip())
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return ""
            return f"{parsed.scheme}://{parsed.hostname}/favicon.ico"
        except Exception:
            return ""

    # ----- Feed presentation -----------------------------------------------
    # One description per item (``view``) drives both this module's first
    # paint and assets/page.js, so labels and thumbnails never disagree.

    @staticmethod
    def _safe_link(url: Any) -> str:
        """Only http(s) links and this site's blob API may reach href/src."""
        value = str(url or "").strip()
        if value.startswith("./api/blob/"):
            return value
        try:
            parsed = urlparse(value)
        except Exception:
            return ""
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return value
        return ""

    @staticmethod
    def _social_title(item_type: str, url: str) -> str:
        """A readable name for a saved post that has no title of its own."""
        parts = [part for part in urlparse(url).path.split("/") if part] if url else []
        handle = next((part[1:] for part in parts if part.startswith("@")), "")
        if item_type == "twitter" and parts and parts[0].lower() not in {"i", "intent", "home", "search"}:
            handle = parts[0]
        handle = re.sub(r"[^A-Za-z0-9_.]", "", handle)[:40]
        fallback = {
            "twitter": "Post on X",
            "threads": "Threads post",
            "tiktok": "TikTok video",
            "instagram": "Instagram post",
            "youtube": "YouTube video",
        }.get(item_type, "")
        if handle and item_type in {"twitter", "threads", "tiktok"}:
            return f"Post by @{handle}" if item_type != "tiktok" else f"TikTok by @{handle}"
        return fallback

    @staticmethod
    def _site_initials(host: str) -> str:
        name = str(host or "").lower()
        if name.startswith("www."):
            name = name[4:]
        letters = [char for char in name.split(".")[0] if char.isalnum()]
        if not letters:
            return ""
        return letters[0].upper() + (letters[1].lower() if len(letters) > 1 else "")

    def _present_item(self, item: Dict[str, Any], blob_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        item_type = _canonical_item_type(item.get("type"))
        url = str(item.get("url") or "").strip()
        is_blob = url.startswith("blob://")
        blob_id = url.split("://", 1)[1] if is_blob else ""
        source = str(item.get("source") or "").strip()
        uploaded = is_blob or source.lower() == "local"
        host = "Uploaded" if uploaded else (self._host_label_from_url(url) or source or "Link")
        filename = str((blob_meta or {}).get("filename") or "")
        ext_source = filename or ("" if is_blob else (urlparse(url).path if url else ""))
        ext = os.path.splitext(ext_source)[1].lstrip(".").upper()[:4]
        title = str(item.get("title") or "").strip()
        platform = item_type in {"youtube", "twitter", "instagram", "threads", "tiktok"}
        display_title = (
            title
            or filename
            or (self._social_title(item_type, url) if platform else "")
            or ("" if uploaded else host)
            or url
            or "Saved item"
        )
        kind, tab, accent, icon = _ITEM_KINDS.get(item_type, ("Link", "articles", "amber", "globe"))
        thumb = video_thumb = favicon = label = ""
        if item_type in {"image", "gif"}:
            thumb = self._blob_api_url(blob_id, preview=True) if blob_id else self._safe_link(url)
        elif item_type == "youtube":
            video_id = re.sub(r"[^A-Za-z0-9_-]", "", self._youtube_id_from_url(url))
            thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
        elif item_type == "video" and blob_id:
            video_thumb = self._blob_api_url(blob_id)
        elif item_type in _SOCIAL_TILE_LABELS:
            label = _SOCIAL_TILE_LABELS[item_type]
        elif item_type in {"document", "text", "file"}:
            label = ext or ("TXT" if item_type == "text" else "")
        elif not uploaded:
            favicon = self._favicon_url_from_url(url)
            label = self._site_initials(host)
        link = self._normalize_x_url(url) if item_type == "twitter" else url
        return {
            "kind": kind,
            # The platform already names the site, so its host is not repeated.
            "byline": [kind] if platform else [kind, host],
            "tab": tab,
            "accent": accent,
            "icon": icon,
            "host": host,
            "title": display_title,
            "content": str(item.get("content") or "")[:50000],
            "thumb": thumb,
            "video_thumb": video_thumb,
            "favicon": favicon,
            "label": label,
            "ext": ext,
            "size": int((blob_meta or {}).get("size") or 0),
            "open_url": self._blob_api_url(blob_id) if blob_id else self._safe_link(link),
            "uploaded": uploaded,
        }

    def _present_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        blob_ids = [
            str(item.get("url") or "")[len("blob://"):]
            for item in items
            if isinstance(item, dict) and str(item.get("url") or "").startswith("blob://")
        ]
        metas: Dict[str, Any] = {}
        if blob_ids and getattr(self, "db", None) is not None and hasattr(self.db, "get_blob_metas"):
            with suppress(Exception):
                metas = self.db.get_blob_metas(blob_ids)
        presented = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            meta = metas.get(url[len("blob://"):]) if url.startswith("blob://") else None
            presented.append({**item, "view": self._present_item(item, meta)})
        return presented

    @staticmethod
    def _esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""), quote=True)

    def _render_feed_thumb(self, view: Dict[str, Any]) -> str:
        """First-paint thumbnail; page.js thumbMarkup() builds the same markup."""
        esc = self._esc
        play = (
            '<span class="thumb-play" aria-hidden="true"><i class="ic ic-play-fill"></i></span>'
            if view.get("icon") == "play"
            else ""
        )
        if view.get("thumb"):
            return (
                f'<span class="feed-thumb"><img src="{esc(view["thumb"])}" alt="" loading="lazy" '
                f'decoding="async" referrerpolicy="no-referrer">{play}</span>'
            )
        if view.get("video_thumb"):
            return (
                f'<span class="feed-thumb"><video data-src="{esc(view["video_thumb"])}#t=0.1" muted playsinline '
                f'preload="none" tabindex="-1" aria-hidden="true"></video>{play}</span>'
            )
        parts = [f'<span class="feed-thumb tile accent-{esc(view.get("accent") or "slate")}">']
        if view.get("favicon"):
            parts.append(
                f'<img class="tile-favicon" src="{esc(view["favicon"])}" alt="" loading="lazy" referrerpolicy="no-referrer">'
            )
        if view.get("label") and view.get("tab") != "files":
            label_class = "tile-label tile-word" if len(view["label"]) > 3 else "tile-label"
            parts.append(f'<span class="{label_class}">{esc(view["label"])}</span>')
        else:
            parts.append(f'<i class="ic ic-{esc(view.get("icon") or "doc")} tile-icon" aria-hidden="true"></i>')
            if view.get("tab") == "files" and view.get("label"):
                parts.append(f'<span class="tile-ext">{esc(view["label"])}</span>')
        parts.append("</span>")
        return "".join(parts)

    def _render_feed_row(self, item: Dict[str, Any], now: datetime) -> str:
        """First-paint row; page.js rowMarkup() builds the same markup."""
        esc = self._esc
        view = item.get("view") or self._present_item(item)
        item_id = int(item.get("id") or 0)
        meta = '<span aria-hidden="true">·</span>'.join(
            f"<span>{esc(part)}</span>"
            for part in [*(view.get("byline") or [view.get("kind"), view.get("host")]), _relative_day(item.get("added_at"), now)]
            if part
        )
        favourite = (
            '<i class="ic ic-heart-fill feed-fav" aria-hidden="true"></i><span class="sr-only">Favourite</span>'
            if item.get("favourite")
            else ""
        )
        return (
            f'<article class="feed-row" data-item-id="{item_id}">'
            + f'<button class="feed-item" type="button" data-action="open" data-item-id="{item_id}">'
            + self._render_feed_thumb(view)
            + '<span class="feed-body">'
            + f'<span class="feed-title">{esc(view.get("title"))}</span>'
            + (f'<span class="feed-preview">{esc(view.get("content"))}</span>' if view.get("content") else "")
            + f'<span class="feed-meta">{meta}{favourite}</span>'
            + "</span>"
            + '<i class="ic ic-chevron-right feed-chevron" aria-hidden="true"></i>'
            + "</button></article>"
        )

    def _render_empty_feed(self, access: Dict[str, Any]) -> str:
        if access.get("can_add"):
            copy = "Save a link or upload a file, or ask AutoYou in Chat to save something here."
        else:
            copy = "Nothing has been saved to this Page yet."
        return (
            '<div class="empty-state"><span class="empty-icon" aria-hidden="true"><i class="ic ic-layers"></i></span>'
            f'<p class="empty-title">Nothing here yet</p><p class="empty-copy">{self._esc(copy)}</p></div>'
        )

    def _render_tag_chips(self, top_tags: List[Dict[str, Any]]) -> str:
        return "".join(
            f'<button class="tag-chip" type="button" data-tag="{self._esc(entry.get("tag"))}">'
            f'<i class="ic ic-tag" aria-hidden="true"></i><span>{self._esc(entry.get("tag"))}</span></button>'
            for entry in top_tags
            if entry.get("tag")
        )

    def _render_favourite_card(self, item: Optional[Dict[str, Any]], now: datetime) -> str:
        if not item:
            return ""
        esc = self._esc
        view = item.get("view") or self._present_item(item)
        item_id = int(item.get("id") or 0)
        return (
            f'<button class="fav-card" type="button" data-action="open" data-item-id="{item_id}">'
            '<span class="fav-card-body">'
            '<span class="fav-card-top"><span class="fav-card-label"><i class="ic ic-heart-fill" aria-hidden="true"></i>'
            f'Latest favourite</span><span class="fav-card-time">{esc(_relative_day(item.get("added_at"), now))}</span></span>'
            f'<span class="fav-card-title">{esc(view.get("title"))}</span>'
            f'<span class="fav-card-meta">{esc(" · ".join(view.get("byline") or [view.get("kind"), view.get("host")]))}</span>'
            "</span>"
            '<i class="ic ic-chevron-right" aria-hidden="true"></i>'
            "</button>"
        )

    def _render_cover(self, cover: Optional[Dict[str, Any]]) -> str:
        if cover and cover.get("url"):
            return (
                f'<img class="cover-img" src="{self._esc(cover["url"])}" alt="" decoding="async" '
                'referrerpolicy="no-referrer">'
            )
        return '<span class="cover-fallback" aria-hidden="true"></span>'

    def _render_avatar(self, profile: Dict[str, Any], access: Dict[str, Any]) -> str:
        variant = "has-photo" if profile.get("has_photo") else "is-mark"
        image = (
            f'<img class="avatar-img" src="{self._esc(profile.get("avatar_url"))}" '
            'alt="" decoding="async">'
        )
        if not access.get("can_edit"):
            return f'<span class="brand-tile {variant}">{image}</span>'
        label = "Change Page photo" if profile.get("has_photo") else "Upload Page photo"
        remove = (
            '<button id="profile-photo-remove" class="profile-photo-remove" type="button"'
            + ("" if profile.get("has_photo") else " hidden")
            + ">Remove photo</button>"
            if access.get("can_delete")
            else ""
        )
        return (
            '<div class="profile-photo-tools">'
            f'<button id="profile-photo-select" class="brand-tile profile-photo-button {variant}" type="button" '
            f'aria-label="{label}" title="{label}">{image}<span class="profile-photo-edit" aria-hidden="true">✎</span></button>'
            '<input id="profile-photo-input" type="file" accept="image/png,image/jpeg,image/webp" hidden>'
            f'{remove}</div>'
        )

    def _render_home(
        self,
        access: Dict[str, Any],
        first_page: Dict[str, Any],
        summary: Dict[str, Any],
        theme: str,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Fill index.html; page.js takes over from the same bootstrap data."""
        esc = self._esc
        now = datetime.now()
        profile = profile or self._owner_profile()
        owner_name = str(profile.get("name") or "")
        items = first_page.get("items") or []
        window = int(first_page.get("window_days") or 0)
        rows = "".join(self._render_feed_row(item, now) for item in items) or self._render_empty_feed(access)
        role = str(access.get("role") or "owner")
        if role in {"owner", "admin"}:
            description = (
                "Everything you and AutoYou save, from articles and videos to photos and files, "
                "in one feed you can share with people you trust."
            )
        elif access.get("can_add"):
            description = "Articles, videos, photos and files saved to this Page. You can add to it too."
        else:
            description = "Articles, videos, photos and files saved to this Page."
        role_label = {"viewer": "View only", "editor": "Can add"}.get(role, "")
        notice = (
            f'<p class="feed-notice">Showing the last {window} day{"s" if window != 1 else ""}. '
            '<button class="link-button" type="button" data-action="show-all">Show everything</button></p>'
            if window > 0
            else ""
        )
        bootstrap = {
            "items": items,
            "has_more": bool(first_page.get("has_more")),
            "next_cursor": first_page.get("next_cursor"),
            "window_days": window,
            "summary": summary,
            "access": access,
            "theme": theme,
            "type_tabs": FEED_TYPE_TABS,
            "page_size": FEED_PAGE_SIZE,
            "profile": profile,
        }
        access_attrs = " ".join(
            f'data-can-{name}="{1 if access.get(f"can_{name}") else 0}"' for name in ("add", "edit", "delete", "manage")
        )
        replacements = {
            "__INITIAL_UI_THEME__": esc(theme),
            "__PAGE_TITLE__": esc(f"{owner_name} · AutoYou Page" if owner_name else "AutoYou Page"),
            "__FAVICON_URL__": esc(profile.get("mark_url")),
            "__PROFILE_AVATAR__": self._render_avatar(profile, access),
            "__PROFILE_NAME__": esc(owner_name or "AutoYou Page"),
            "__PROFILE_TAGLINE__": esc("AutoYou Page · saved links, media and files" if owner_name else "Saved links, media and files."),
            "__PAGE_CSS_URL__": f"./assets/page.css?v={_asset_version(PAGE_ASSETS['page.css'][0])}",
            "__PAGE_JS_URL__": f"./assets/page.js?v={_asset_version(PAGE_ASSETS['page.js'][0])}",
            "__PAGE_ACCESS_ATTRS__": f'{access_attrs} data-role="{esc(role)}"',
            "__ROLE_PILL__": f'<span class="role-pill">{esc(role_label)}</span>' if role_label else "",
            "__HERO_COVER__": self._render_cover(summary.get("cover")),
            "__STAT_TOTAL__": esc(summary.get("total") or 0),
            "__STAT_FAVOURITES__": esc(summary.get("favourites") or 0),
            "__STAT_SOURCES__": esc(summary.get("sources") or 0),
            "__HERO_DESCRIPTION__": esc(description),
            "__TOP_TAGS__": self._render_tag_chips(summary.get("top_tags") or []),
            "__LATEST_FAVOURITE__": self._render_favourite_card(summary.get("latest_favourite"), now),
            "__FEED_NOTICE__": notice,
            "__FEED_ROWS__": rows,
            "__PAGE_BOOTSTRAP_JSON__": self._serialize_inline_json(bootstrap),
        }
        template = INDEX_HTML_PATH.read_text(encoding="utf-8")
        # One pass, so saved titles that look like placeholders are never expanded.
        return re.sub(r"__[A-Z_]+__", lambda match: replacements.get(match.group(0), match.group(0)), template)

    def _classify_url(self, url: str) -> str:
        """Classify the URL into a feed item type."""
        u = url.lower()
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

    def _append_item(self, item_type: str, url: str, title: Optional[str] = None, source: Optional[str] = None, content: str = "") -> Dict[str, Any]:
        """Append a new item to the feed and return it."""
        item_type = "twitter" if str(item_type or "").strip().lower() in {"twitter", "x"} else str(item_type or "").strip().lower()
        if item_type == "twitter":
            url = self._normalize_x_url(url)
        elif item_type == "threads":
            url = self._normalize_threads_url(url)
        source = self._normalize_source_label(source or self._default_source(url))
        # Persist to DB if available; otherwise fall back to in-memory only
        if getattr(self, "db", None) is not None:
            try:
                item = self.db.insert(item_type=item_type, url=url, title=title, source=source, content=content)
                self.feed_items.append(item)
                self._next_id = item["id"] + 1
                return item
            except Exception as e:
                LOGGER.error(f"DB insert failed, falling back to memory: {e}")
        # Memory-only fallback
        item = {
            "id": self._next_id,
            "type": item_type,
            "url": url,
            "title": title or "",
            "content": str(content or "")[:50000],
            "source": source or "",
            "added_at": datetime.now().isoformat(),
        }
        self._next_id += 1
        self.feed_items.append(item)
        return item

    def _default_source(self, url: str) -> str:
        """Extract hostname to use as source label."""
        try:
            host = urlparse(self._normalize_x_url(url)).hostname or ""
            return self._normalize_source_label(host)
        except Exception:
            return ""

    def _get_title_sync(self, url: str, timeout: float = 2.5) -> Optional[str]:
        """Fetch the page and attempt to extract <title>. Lightweight and bounded.

        Returns the title string if found, else None.

        Uses `requests` with manual redirect-following so every hop is
        re-validated against the SSRF rules (see shared.url_safety).
        """
        try:
            assert_safe_http_url(url)
        except UnsafeURLError:
            return None
        try:
            session = requests.Session()
            try:
                resp = safe_follow_redirects(
                    session,
                    url,
                    method="GET",
                    max_redirects=5,
                    timeout=timeout,
                    headers={"User-Agent": "Mozilla/5.0 (AutoYou)"},
                )
            finally:
                session.close()
            # Read up to ~64KB to find title
            raw = resp.content[:65536]
            text = raw.decode("utf-8", errors="ignore")
            m = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
            if m:
                title = m.group(1).strip()
                title = re.sub(r"\s+", " ", title)
                return title
            return None
        except Exception:
            return None
    
    def _normalize_tiktok_url(self, url: str) -> str:
        """Normalize TikTok URLs.

        - Follow short links like `https://www.tiktok.com/t/…` to the canonical
          `…/@user/video/<id>/` form.
        - Strip query to keep embed stable.

        Every redirect hop is re-validated against the SSRF rules so a
        TikTok short link cannot redirect into a private-IP or loopback
        address.
        """
        try:
            u = (url or "").strip()
            if not u:
                return url
            try:
                assert_safe_http_url(u)
            except UnsafeURLError:
                return url
            low = u.lower()
            if "tiktok.com/t/" in low:
                with suppress(Exception):
                    session = requests.Session()
                    try:
                        r = safe_follow_redirects(
                            session,
                            u,
                            method="GET",
                            max_redirects=5,
                            timeout=7,
                            headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                            },
                        )
                    finally:
                        session.close()
                    if r.ok and r.url:
                        u = r.url
            # Canonicalize path and drop query for stability
            with suppress(Exception):
                p = urlparse(u)
                if p.hostname and "tiktok.com" in p.hostname:
                    path = p.path
                    if not path.endswith("/"):
                        path = path + "/"
                    u = f"{p.scheme}://{p.hostname}{path}"
            return u
        except Exception:
            return url

    def _resolve_media_url(self, url: str) -> Optional[str]:
        """Resolve a share URL to a direct media URL when possible.

        Uses yt-dlp if available; otherwise returns the original URL for
        straightforward video links. The input URL is validated against
        the SSRF rules before it is handed to yt-dlp, which prevents
        `file://` and private-IP inputs from reaching yt-dlp's loader.
        The resolved URL is validated again before being returned so a
        platform that redirects to a private host cannot pivot onto
        loopback / metadata addresses through the stream proxy.
        """
        try:
            try:
                assert_safe_http_url(url)
            except UnsafeURLError:
                return None

            # Fast-path for direct video links
            direct_exts = (".mp4", ".webm", ".m4v")
            if any(url.lower().endswith(ext) for ext in direct_exts):
                return url

            yt_dlp = None
            with suppress(Exception):
                import yt_dlp  # type: ignore
            if yt_dlp is None:
                return None

            opts = {
                "quiet": True,
                "noplaylist": True,
                "nocheckcertificate": True,
                "skip_download": True,
                "format": "best",
            }
            with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
                info = ydl.extract_info(url, download=False)
                # Prefer top-level URL if present, else best format
                resolved = info.get("url")
                if not resolved:
                    for fmt in (info.get("formats") or []):
                        u = fmt.get("url")
                        if u:
                            resolved = u
                            break
                if resolved and not is_safe_http_url(resolved):
                    return None
                return resolved
        except Exception:
            return None

    def _resolve_instagram_preview_url(self, url: str, timeout: float = 5.0) -> Optional[str]:
        """Resolve an Instagram post/reel URL to a client-loadable preview image URL.

        Redirects are followed manually and every hop is re-validated
        against the SSRF rules so a crafted URL cannot pivot onto a
        private-IP / loopback / metadata host.
        """
        try:
            normalized = (url or "").strip()
            if not normalized:
                return None
            parsed = urlparse(normalized)
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) < 2 or parts[0] not in {"p", "reel", "tv"}:
                return None
            preview_url = f"{parsed.scheme or 'https'}://www.instagram.com/{parts[0]}/{parts[1]}/media/?size=l"
            try:
                assert_safe_http_url(preview_url)
            except UnsafeURLError:
                return None

            session = requests.Session()
            try:
                response = safe_follow_redirects(
                    session,
                    preview_url,
                    method="GET",
                    max_redirects=5,
                    timeout=timeout,
                    headers={"User-Agent": "Mozilla/5.0 (AutoYou)"},
                )
            finally:
                session.close()

            try:
                content_type = str(response.headers.get("content-type") or "").lower()
                if response.ok and content_type.startswith("image/") and response.url:
                    return str(response.url)
            finally:
                response.close()
        except Exception:
            return None
        return None

    def _safe_filename(self, name: str) -> str:
        """Sanitize a filename for safe storage on disk.

        - Strips directory components
        - Replaces disallowed characters with underscores
        - Limits length to 120 chars
        """
        try:
            base = os.path.basename(name or "upload.bin")
            safe = re.sub(r"[^a-zA-Z0-9._-]", "_", base)
            if not safe:
                safe = "upload.bin"
            return safe[:120]
        except Exception:
            return "upload.bin"

    @staticmethod
    def _canonicalize_blob_mimetype(filename: str, mimetype: Optional[str]) -> str:
        provided = str(mimetype or "").split(";", 1)[0].strip().lower()
        extension = os.path.splitext(str(filename or ""))[1].lower()

        alias_map = {
            "application/mp4": "video/mp4",
            "audio/m4a": "audio/mp4",
            "audio/x-m4a": "audio/mp4",
            "audio/mp4a-latm": "audio/mp4",
            "video/x-m4v": "video/mp4",
        }
        generic_types = {
            "",
            "application/octet-stream",
            "binary/octet-stream",
            "application/binary",
            "application/x-binary",
            "application/unknown",
        }

        if provided in alias_map:
            return alias_map[provided]
        if provided not in generic_types:
            return provided

        extension_map = {
            ".m4a": "audio/mp4",
            ".m4b": "audio/mp4",
            ".m4v": "video/mp4",
        }
        if extension in extension_map:
            return extension_map[extension]

        guessed, _ = mimetypes.guess_type(str(filename or ""))
        guessed_value = str(guessed or "").strip().lower()
        if guessed_value in alias_map:
            return alias_map[guessed_value]
        if guessed_value:
            return guessed_value
        return "application/octet-stream"

    def _ext_from_mime(self, mime: Optional[str], fallback_ext: str = "") -> str:
        """Return a reasonable file extension for a given MIME type.

        If `fallback_ext` is provided, it is returned when a mapping is unavailable.
        """
        try:
            m = (mime or "").lower()
            mapping = {
                "image/jpeg": ".jpg",
                "image/jpg": ".jpg",
                "image/png": ".png",
                "image/gif": ".gif",
                "image/webp": ".webp",
                "video/mp4": ".mp4",
                "video/webm": ".webm",
                "video/quicktime": ".mov",
                "video/x-m4v": ".m4v",
                "audio/mpeg": ".mp3",
                "audio/mp3": ".mp3",
                "audio/mp4": ".m4a",
                "audio/m4a": ".m4a",
                "audio/x-m4a": ".m4a",
                "audio/aac": ".aac",
                "audio/wav": ".wav",
                "audio/webm": ".webm",
                "application/pdf": ".pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                "application/msword": ".doc",
                "text/plain": ".txt",
            }
            return mapping.get(m, fallback_ext)
        except Exception:
            return fallback_ext

    def _is_browser_safe_inline_image(self, filename: str, mimetype: Optional[str]) -> bool:
        safe_mime = (mimetype or "").lower()
        safe_name = (filename or "").lower()
        if safe_mime in {
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/gif",
            "image/webp",
            "image/svg+xml",
            "image/bmp",
        }:
            return True
        return safe_name.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"))

    @staticmethod
    def _blob_preview_passthrough_bytes() -> int:
        try:
            return max(0, int(os.environ.get("AUTOYOU_PAGE_BLOB_PREVIEW_PASSTHROUGH_BYTES", "1048576")))
        except Exception:
            return 1048576

    @staticmethod
    def _blob_preview_max_edge() -> int:
        try:
            return max(64, int(os.environ.get("AUTOYOU_PAGE_BLOB_PREVIEW_MAX_EDGE", "1280")))
        except Exception:
            return 1280

    @staticmethod
    def _is_previewable_image(filename: str, mimetype: Optional[str]) -> bool:
        safe_mime = (mimetype or "").split(";", 1)[0].strip().lower()
        safe_name = (filename or "").lower()
        if safe_mime == "image/svg+xml" or safe_name.endswith(".svg"):
            return False
        return safe_mime.startswith("image/") or safe_name.endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
        )

    def _prepare_blob_preview(self, path: str, mimetype: Optional[str]) -> Optional[tuple[str, str]]:
        if not self._is_previewable_image(os.path.basename(path), mimetype):
            return None

        try:
            original_size = self._blob_plain_size(path)
        except OSError:
            return None
        if original_size <= self._blob_preview_passthrough_bytes():
            return path, mimetype or "application/octet-stream"

        try:
            from PIL import Image, ImageOps  # type: ignore
        except Exception as exc:
            LOGGER.warning("Large page blob preview unavailable because Pillow failed to import: %s", exc)
            return None

        max_edge = self._blob_preview_max_edge()
        try:
            stat = os.stat(path)
            key = hashlib.sha256(
                f"{Path(path).resolve()}:{stat.st_mtime_ns}:{stat.st_size}:{max_edge}".encode("utf-8")
            ).hexdigest()[:24]
            preview_dir = Path(self.uploads_dir) / ".previews"
            preview_dir.mkdir(parents=True, exist_ok=True)
            preview_path = preview_dir / f"{key}.jpg"
            if preview_path.is_file() and preview_path.stat().st_size > 0:
                return str(preview_path), "image/jpeg"

            with Image.open(BytesIO(self._read_blob_bytes(path))) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((max_edge, max_edge))
                if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, (255, 255, 255))
                    background.paste(rgba, mask=rgba.split()[-1])
                    image = background
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                if secure_storage_enabled():
                    preview_bytes = BytesIO()
                    image.save(preview_bytes, format="JPEG", quality=82, optimize=True)
                    write_secure_file(preview_path, preview_bytes.getvalue())
                else:
                    tmp_path = preview_path.with_suffix(".tmp")
                    image.save(tmp_path, format="JPEG", quality=82, optimize=True)
                    os.replace(tmp_path, preview_path)
            return str(preview_path), "image/jpeg"
        except Exception as exc:
            LOGGER.warning("Failed to generate page blob preview for %s: %s", path, exc)
            return None

    def _needs_browser_image_conversion(self, filename: str, mimetype: Optional[str]) -> bool:
        safe_mime = (mimetype or "").lower()
        safe_name = (filename or "").lower()
        unsupported_mimes = {
            "image/heic",
            "image/heif",
            "image/heic-sequence",
            "image/heif-sequence",
        }
        unsupported_exts = (".heic", ".heif", ".heics", ".heifs")
        return safe_mime in unsupported_mimes or safe_name.endswith(unsupported_exts)

    def _normalize_uploaded_blob(
        self,
        *,
        stored_path: str,
        filename: str,
        mimetype: Optional[str],
    ) -> tuple[str, str, str, str]:
        """Return a browser-friendly stored blob description.

        Returns:
            `(path, filename, mimetype, render_type)`
        """
        safe_name = self._safe_filename(filename or os.path.basename(stored_path))
        safe_mime = self._canonicalize_blob_mimetype(safe_name, mimetype)
        if safe_mime.startswith("audio/"):
            try:
                with open(stored_path, "rb") as media_file:
                    if is_adts_aac(media_file.read(64 * 1024)):
                        safe_mime = "audio/aac"
            except OSError:
                pass

        if self._is_browser_safe_inline_image(safe_name, safe_mime):
            return stored_path, safe_name, safe_mime, "image"

        if not self._needs_browser_image_conversion(safe_name, safe_mime):
            return stored_path, safe_name, safe_mime, ""

        converted_root = os.path.splitext(stored_path)[0]
        converted_path = f"{converted_root}.jpg"
        converted_name = self._safe_filename(f"{os.path.splitext(safe_name)[0]}.jpg")

        # Best-effort browser normalization for HEIC/HEIF uploads on macOS.
        sips_path = shutil.which("sips")
        if sips_path:
            try:
                proc = subprocess.run(
                    [sips_path, "-s", "format", "jpeg", stored_path, "--out", converted_path],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode == 0 and os.path.exists(converted_path) and os.path.getsize(converted_path) > 0:
                    with suppress(Exception):
                        os.remove(stored_path)
                    LOGGER.info("Converted uploaded image to browser-safe JPEG: %s -> %s", stored_path, converted_path)
                    return converted_path, converted_name, "image/jpeg", "image"
                LOGGER.warning(
                    "sips failed to convert uploaded image for browser rendering: %s",
                    (proc.stderr or proc.stdout or "").strip(),
                )
            except Exception as exc:
                LOGGER.warning("Failed to normalize uploaded image via sips: %s", exc)

        # If conversion is unavailable, store it as a file so the UI does not
        # render a broken inline image tile.
        return stored_path, safe_name, safe_mime, "file"

    @staticmethod
    def _is_remote_browser_request(request: Request) -> bool:
        return bool(
            request.headers.get(REMOTE_BROWSER_HEADER)
            or request.headers.get("X-AutoYou-WebRTC-Session-Id")
            or request.headers.get("X-AutoYou-WebRTC-Owner-Key")
            or request.headers.get("X-AutoYou-Agent-Frontend")
        )

    @staticmethod
    def _is_remote_write_path(method: str, path: str) -> bool:
        normalized_method = str(method or "").upper()
        normalized_path = str(path or "/")
        if normalized_path in {"/api/feed/refresh", "/api/feed/clear"}:
            return True
        # The site theme is the owner's shared preference, not a viewer's.
        if normalized_path == "/api/ui/theme" and normalized_method not in {"GET", "HEAD"}:
            return True
        if normalized_method in {"POST", "PUT", "PATCH", "DELETE", "OPTIONS"} and (
            normalized_path == "/api/profile/avatar"
            or normalized_path == "/api/feed"
            or normalized_path.startswith("/api/feed/")
            or normalized_path == "/api/blob"
            or normalized_path.startswith("/api/item/")
        ):
            return True
        return False

    def register_routes(self, app) -> None:
        """Register the page-feed routes on the given FastAPI app."""
        @app.middleware("http")
        async def remote_browser_write_guard(request: Request, call_next):
            if self._is_remote_browser_request(request) and self._is_remote_write_path(
                request.method,
                request.url.path,
            ):
                role = normalize_remote_access_role(request.headers.get("X-AutoYou-Remote-Access-Role"))
                if not remote_http_request_allowed(role, request.method, request.url.path):
                    return JSONResponse(
                        {
                            "success": False,
                            "error": (
                                remote_access_denial_message(role, request.method, request.url.path)
                                if request.url.path == "/api/profile/avatar"
                                else REMOTE_WRITE_DENIAL_MESSAGE
                            ),
                            "remote_access_role": role,
                        },
                        status_code=403,
                    )
            return await call_next(request)

        def delete_feed_item_payload(item_id: int) -> Dict[str, Any]:
            deleted = 0
            cleanup: Dict[str, Any] = {}
            if getattr(self, "db", None) is not None:
                try:
                    cleanup = self.db.delete_item_and_cleanup(item_id, uploads_dir=self.uploads_dir)
                    deleted = int(cleanup.get("deleted", 0))
                except Exception as e:
                    LOGGER.error(f"DB delete failed: {e}")
            before = len(self.feed_items)
            self.feed_items = [i for i in self.feed_items if i.get("id") != item_id]
            after = len(self.feed_items)
            return {
                "ok": True,
                "deleted": deleted or (before - after),
                "cleanup": cleanup,
            }

        @app.get("/api/status")
        async def api_status() -> JSONResponse:
            """Return health/status for the extracted page-agent website."""
            return JSONResponse({
                "service": "autoyou_page_agent",
                "status": "active",
                "timestamp": datetime.now().isoformat(),
                "timeline_days": self._window_days(),
                "db_available": getattr(self, "db", None) is not None,
                "loaded_items": len(self.feed_items),
                "capabilities": [
                    "feed_query",
                    "blob_upload",
                    "range_blob_streaming",
                ],
            })

        @app.get("/api/ui/theme")
        async def api_ui_theme() -> JSONResponse:
            return JSONResponse({
                "success": True,
                "theme": self._get_ui_theme(),
                "themes": ["dark", "light"],
            })

        @app.post("/api/ui/theme")
        async def api_set_ui_theme(request: Request) -> JSONResponse:
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            saved_theme = await asyncio.to_thread(self._set_ui_theme, (payload or {}).get("theme"))
            return JSONResponse({"success": True, "theme": saved_theme})

        @app.get("/api/feed")
        async def api_feed(request: Request) -> JSONResponse:
            """Return current feed items."""
            try:
                days_param = request.query_params.get("days")
                limit_param = request.query_params.get("limit")
                try:
                    query_limit = int(limit_param) if limit_param is not None else None
                except ValueError:
                    query_limit = None
                if query_limit is not None and query_limit <= 0:
                    query_limit = None
                
                # Default to configured timeline window, latest first if DB is available
                if getattr(self, "db", None) is not None:
                    try:
                        query_days = int(days_param) if days_param is not None else self._window_days()
                    except ValueError:
                        query_days = self._window_days()
                        
                    if query_days == 0:
                        items = self.db.query_items(order="desc", ignore_date_default=True, limit=query_limit)
                    else:
                        items = self.db.load_recent_desc(days=query_days, limit=query_limit)
                    return JSONResponse({"items": items})
                items = list(self.feed_items)
                if query_limit is not None:
                    items = items[:query_limit]
                return JSONResponse({"items": items})
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/feed")
        async def api_feed_add(request: Request) -> JSONResponse:
            """Add a new item to the feed from a link."""
            try:
                data = await request.json()
                url = str(data.get("url", "")).strip()
                content = str(data.get("content") or "")[:50000]
                item_type = str(data.get("type") or (self._classify_url(url) if url else "text"))
                if not url and not content:
                    raise HTTPException(status_code=400, detail="url or content is required")
                if not url:
                    item_type = "text"
                item_type = "twitter" if item_type.strip().lower() in {"twitter", "x"} else item_type.strip().lower()
                if item_type == "twitter":
                    url = self._normalize_x_url(url)
                elif item_type == "threads":
                    url = self._normalize_threads_url(url)
                title = data.get("title")
                source = self._normalize_source_label(data.get("source") or self._default_source(url))
                # Normalize TikTok short links to canonical video URL
                if item_type == "tiktok":
                    with suppress(Exception):
                        url = await asyncio.to_thread(self._normalize_tiktok_url, url)
                        source = self._default_source(url)
                # Try to enrich title for articles when not provided
                if (not title or not str(title).strip()) and item_type == "article":
                    try:
                        fetched = await asyncio.to_thread(self._get_title_sync, url)
                        if fetched:
                            title = fetched
                    except Exception:
                        pass
                item = self._append_item(item_type, url, title, source, content)
                try:
                    if getattr(self, "db", None) is not None and url.lower().startswith("blob://") and title:
                        blob_id = url.split("://", 1)[1]
                        path, mime = self.db.get_blob_path_and_type(blob_id)
                        if path:
                            base = os.path.basename(path)
                            ext = os.path.splitext(base)[1] or self._ext_from_mime(mime or "", "")
                            safe_title = self._safe_filename(str(title))
                            new_name = f"{safe_title}{ext}" if ext else safe_title
                            new_path = os.path.join(self.uploads_dir, new_name)
                            if os.path.abspath(new_path) != os.path.abspath(path):
                                if os.path.exists(new_path):
                                    ts = str(int(datetime.now().timestamp() * 1000))
                                    stem, e = os.path.splitext(new_name)
                                    new_name = f"{stem}_{ts}{e}"
                                    new_path = os.path.join(self.uploads_dir, new_name)
                                with suppress(Exception):
                                    os.replace(path, new_path)
                                    self.db.update_blob_path_and_filename(blob_id, new_path, new_name)
                except Exception:
                    pass
                return JSONResponse({"item": self._present_items([{"tags": [], "favourite": False, **item}])[0]})
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.delete("/api/feed/{item_id}")
        async def api_feed_delete(item_id: int) -> JSONResponse:
            """Delete a feed item by id."""
            try:
                return JSONResponse(delete_feed_item_payload(item_id))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/feed/{item_id}/delete")
        async def api_feed_delete_via_post(item_id: int) -> JSONResponse:
            """Delete a feed item via POST for tunnel- and proxy-friendly clients."""
            try:
                return JSONResponse(delete_feed_item_payload(item_id))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/feed/refresh")
        async def api_feed_refresh() -> JSONResponse:
            """Clear feed items and reset IDs."""
            try:
                # Clear DB and reset sequence if available
                if getattr(self, "db", None) is not None:
                    try:
                        summary = self.db.clear_all_and_cleanup(self.uploads_dir)
                        self.db.reset_sequence()
                    except Exception as e:
                        LOGGER.error(f"DB clear failed: {e}")
                # Mirror in-memory state
                self.feed_items = []
                self._next_id = 1
                return JSONResponse({"ok": True, "count": len(self.feed_items), "cleanup": (summary if 'summary' in locals() else {})})
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/feed/clear")
        async def api_feed_clear() -> JSONResponse:
            """Alias for clearing the feed."""
            try:
                if getattr(self, "db", None) is not None:
                    try:
                        summary = self.db.clear_all_and_cleanup(self.uploads_dir)
                        self.db.reset_sequence()
                    except Exception as e:
                        LOGGER.error(f"DB clear failed: {e}")
                self.feed_items = []
                self._next_id = 1
                return JSONResponse({"ok": True, "count": len(self.feed_items), "cleanup": (summary if 'summary' in locals() else {})})
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/feed/query")
        async def api_feed_query(request: Request) -> JSONResponse:
            """Query feed items with sorting and filters (efficient via DB)."""
            try:
                # Parse query params
                qp = request.query_params
                order = (qp.get("order") or "desc").lower()
                favourites_only = bool(qp.get("favourites_only"))
                tag_search = qp.get("tag_search") or None
                types_csv = qp.get("types") or ""
                types = [t.strip().lower() for t in types_csv.split(",") if t.strip()] or None
                # Multi-source OR support
                sources_csv = qp.get("sources") or ""
                sources = [s.strip() for s in sources_csv.split(",") if s.strip()] or None
                # Backward compatibility: single source param
                source = (qp.get("source") or None)
                date_from = qp.get("date_from") or None
                date_to = qp.get("date_to") or None
                timeline_all = bool(qp.get("timeline_all"))
                try:
                    limit = int(qp.get("limit")) if qp.get("limit") is not None else None
                except ValueError:
                    limit = None
                if limit is not None and limit <= 0:
                    limit = None
                # Defaults: last N days per the configured window; 0 is the entire feed
                now = datetime.now().isoformat()
                window_days = self._window_days()
                if not timeline_all and not tag_search and window_days > 0:
                    if not date_from or not date_to:
                        df = (datetime.now() - timedelta(days=window_days)).isoformat()
                        date_from = date_from or df
                        date_to = date_to or now
                elif timeline_all:
                    # Explicitly disable date filtering when entire timeline is requested
                    date_from = None
                    date_to = None
                # Use DB if available
                if getattr(self, "db", None) is not None:
                    items = self.db.query_items(
                        order=("asc" if order == "asc" else "desc"),
                        types=types,
                        # If sources provided, prefer them; else use single source for compatibility
                        source=(None if sources else source),
                        sources=sources,
                        date_from=date_from,
                        date_to=date_to,
                        favourites_only=favourites_only,
                        tag_search=tag_search,
                        limit=limit,
                        ignore_date_default=(timeline_all or bool(tag_search) or window_days == 0),
                    )
                    return JSONResponse({"items": items})
                # Fallback: in-memory filtering (less efficient)
                def in_date(i):
                    try:
                        if (timeline_all or tag_search) and not date_from and not date_to:
                            return True
                        t = i.get("added_at")
                        return (not date_from or (t and t >= date_from)) and (not date_to or (t and t <= date_to))
                    except:
                        return True
                items = [i for i in self.feed_items if in_date(i)]
                if tag_search:
                    q_lower = tag_search.lower()
                    items = [
                        i for i in items
                        if q_lower in str(i.get("title") or "").lower()
                        or any(q_lower in str(tag).lower() for tag in i.get("tags", []))
                    ]
                if types:
                    items = [i for i in items if str(i.get("type")).lower() in types]
                if sources:
                    src_set = set(sources)
                    items = [i for i in items if str(i.get("source") or "") in src_set]
                elif source:
                    items = [i for i in items if str(i.get("source") or "") == source]
                if favourites_only:
                    items = [i for i in items if bool(i.get("favourite"))]
                # Basic order by added_at
                try:
                    items.sort(key=lambda x: x.get("added_at", ""), reverse=(order != "asc"))
                except:
                    pass
                if limit is not None:
                  items = items[:limit]
                return JSONResponse({"items": items})
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/feed/page")
        async def api_feed_page(request: Request) -> JSONResponse:
            """One page of the website feed, with display details, for infinite scroll."""
            qp = request.query_params
            view = str(qp.get("view") or "feed").strip().lower()
            tab = str(qp.get("tab") or "latest").strip().lower()
            sources = [value.strip() for value in str(qp.get("sources") or "").split(",") if value.strip()]
            try:
                limit = int(qp.get("limit") or FEED_PAGE_SIZE)
            except ValueError:
                limit = FEED_PAGE_SIZE
            try:
                payload = await asyncio.to_thread(
                    self._feed_page,
                    order=str(qp.get("order") or "desc"),
                    types=list(FEED_TYPE_TABS.get(tab, [])),
                    sources=[] if view == "uploads" else sources,
                    favourites_only=view == "favourites",
                    stored_only=view == "uploads",
                    tag_search=qp.get("q"),
                    date_from=qp.get("date_from") or None,
                    date_to=qp.get("date_to") or None,
                    all_time=str(qp.get("all") or "").strip().lower() in {"1", "true", "yes"},
                    cursor=qp.get("cursor"),
                    limit=limit,
                )
            except Exception as exc:
                LOGGER.error("Page feed query failed: %s", exc)
                return JSONResponse({"success": False, "error": "The feed could not be loaded."}, status_code=500)
            return JSONResponse({"success": True, **payload})

        @app.get("/api/feed/summary")
        async def api_feed_summary() -> JSONResponse:
            """Totals, top tags, the latest favourite and the cover photo for the page header."""
            summary = await asyncio.to_thread(self._page_summary)
            return JSONResponse({"success": True, **summary})

        # Per-blob upload size cap. The default is deliberately generous
        # (2 GB) so videos and RAW photos still work; the intent is to stop
        # a paired client from filling the disk by uploading an unbounded
        # stream. Override via `AUTOYOU_MAX_BLOB_SIZE_BYTES` (0 disables).
        try:
            _max_blob_bytes = int(os.environ.get("AUTOYOU_MAX_BLOB_SIZE_BYTES", str(2 * 1024 * 1024 * 1024)))
        except ValueError:
            _max_blob_bytes = 2 * 1024 * 1024 * 1024

        @app.post("/api/blob")
        async def api_blob_upload(file: UploadFile = File(...)) -> JSONResponse:
            """Upload a file and store it locally, returning blob metadata.

            Expects multipart/form-data with field `file`. Upload is rejected
            with 413 (Payload Too Large) if the body exceeds the configured
            per-blob cap (`AUTOYOU_MAX_BLOB_SIZE_BYTES`, default 2 GiB).
            """
            try:
                if getattr(self, "db", None) is None:
                    raise HTTPException(status_code=500, detail="Blob storage not available")

                original_name = file.filename or "upload.bin"
                safe_name = self._safe_filename(original_name)
                ts = int(datetime.now().timestamp() * 1000)
                target_path = os.path.join(self.uploads_dir, f"{ts}_{safe_name}")

                size = 0
                oversize = False
                with open(target_path, "wb") as out:
                    while True:
                        chunk = await file.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if _max_blob_bytes > 0 and size > _max_blob_bytes:
                            oversize = True
                            break
                        out.write(chunk)
                if oversize:
                    try:
                        os.unlink(target_path)
                    except OSError:
                        pass
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds the {_max_blob_bytes}-byte blob size cap",
                    )

                mimetype = file.content_type or "application/octet-stream"
                stored_path, stored_name, stored_mimetype, render_type = self._normalize_uploaded_blob(
                    stored_path=target_path,
                    filename=safe_name,
                    mimetype=mimetype,
                )
                self._seal_blob_path(stored_path)
                stored_size = self._blob_plain_size(stored_path) if os.path.exists(stored_path) else int(size)
                meta = self.db.insert_blob(
                    filename=stored_name,
                    mimetype=stored_mimetype,
                    size=int(stored_size),
                    path=stored_path,
                )
                payload = {
                    "id": meta["id"],
                    "filename": meta["filename"],
                    "mimetype": meta["mimetype"],
                    "size": meta["size"],
                }
                if render_type:
                    payload["render_type"] = render_type
                return JSONResponse(payload)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/blob/{blob_id}")
        async def api_blob_stream(blob_id: str, request: Request) -> StreamingResponse:
            """Stream a locally stored blob with Range support."""
            try:
                if getattr(self, "db", None) is None:
                    raise HTTPException(status_code=500, detail="Blob storage not available")

                path, mime = self.db.get_blob_path_and_type(str(blob_id))
                if not path or not os.path.exists(path):
                    if path:
                        candidate = os.path.join(self.uploads_dir, os.path.basename(path))
                        if os.path.exists(candidate):
                            path = candidate
                if not path or not os.path.exists(path):
                    raise HTTPException(status_code=404, detail="Blob not found")
                mime = self._canonicalize_blob_mimetype(os.path.basename(path), mime)
                preview_requested = str(request.query_params.get("preview") or "").strip().lower() in {"1", "true", "yes", "on"}
                if preview_requested:
                    preview = self._prepare_blob_preview(path, mime)
                    if preview is not None:
                        path, mime = preview
                    elif self._is_previewable_image(os.path.basename(path), mime):
                        try:
                            original_size = self._blob_plain_size(path)
                        except OSError:
                            original_size = self._blob_preview_passthrough_bytes() + 1
                        if original_size > self._blob_preview_passthrough_bytes():
                            raise HTTPException(status_code=415, detail="Blob preview unavailable")

                blob_bytes: Optional[bytes] = None
                if str(mime or "").lower().startswith("audio/"):
                    blob_bytes = self._read_blob_bytes(path)
                    if is_adts_aac(blob_bytes):
                        mime = "audio/aac"
                file_size = len(blob_bytes) if blob_bytes is not None else self._blob_plain_size(path)
                range_header = request.headers.get("Range")
                start = 0
                end = file_size - 1
                status_code = 200

                if range_header:
                    m = re.match(r"bytes=(\d+)-(\d*)", range_header)
                    if m:
                        start = int(m.group(1))
                        if m.group(2):
                            end = int(m.group(2))
                        status_code = 206
                    start = max(0, start)
                    end = min(file_size - 1, end)
                    if start > end:
                        start = 0
                        end = file_size - 1

                def iter_file(p: str, s: int, e: int, chunk: int = 65536):
                    data = blob_bytes if blob_bytes is not None else self._read_blob_bytes(p)
                    position = max(0, int(s))
                    end_position = min(len(data) - 1, int(e))
                    while position <= end_position:
                        next_position = min(end_position + 1, position + chunk)
                        yield data[position:next_position]
                        position = next_position

                # Prepare Content-Disposition name using DB post title + original extension
                try:
                    # Derive extension from original filename or MIME
                    base_name = os.path.basename(path)
                    base_root, base_ext = os.path.splitext(base_name)
                    ext = base_ext or self._ext_from_mime(mime or "", "")
                    # Fetch associated post title if any
                    post_title = None
                    if getattr(self, "db", None) is not None:
                        with suppress(Exception):
                            post_title = self.db.get_feed_title_by_blob_id(str(blob_id))
                    safe_root = self._safe_filename(post_title or base_root)
                    download_name = f"{safe_root}{ext}" if ext else safe_root
                except Exception:
                    download_name = os.path.basename(path)

                resp = StreamingResponse(iter_file(path, start, end), status_code=status_code, media_type=(mime or "application/octet-stream"))
                resp.headers["Accept-Ranges"] = "bytes"
                resp.headers["Content-Length"] = str(end - start + 1)
                if status_code == 206:
                    resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                # Security hardening (H-11):
                # Treat user-uploaded blobs as untrusted. The stored MIME came
                # from the uploader's browser and can lie; force the browser
                # to honour the declared type without sniffing, and force a
                # download for MIME types that can host active content
                # (HTML/JS/SVG/XHTML). Images, audio, video, and PDFs remain
                # viewable inline so existing feed previews keep working.
                mime_lc = (mime or "application/octet-stream").strip().lower()
                _RISKY_INLINE_MIMES = (
                    "text/html",
                    "application/xhtml+xml",
                    "image/svg+xml",
                    "application/javascript",
                    "text/javascript",
                    "application/ecmascript",
                    "text/ecmascript",
                )
                force_attachment = any(mime_lc.startswith(t) for t in _RISKY_INLINE_MIMES)
                disposition_kind = "attachment" if force_attachment else "inline"
                # Provide a stable filename for downloads using the DB post title
                resp.headers["Content-Disposition"] = f"{disposition_kind}; filename=\"{download_name}\""
                resp.headers["X-Content-Type-Options"] = "nosniff"
                # Lock down any content the browser does end up rendering
                # (e.g. an image/png that turns out to embed HTML after
                # nosniff is respected will just fail to render; but if
                # something slips through, sandbox denies script/network).
                resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'unsafe-inline'; sandbox"
                resp.headers["Referrer-Policy"] = "no-referrer"
                if preview_requested:
                    resp.headers["X-AutoYou-Blob-Preview"] = "1"
                return resp
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/media/resolve")
        async def api_media_resolve(request: Request) -> JSONResponse:
            """Resolve a media share URL to a direct media URL.

            Returns { ok: bool, url: str|null }
            """
            try:
                url = str(request.query_params.get("url") or "").strip()
                if not url:
                    raise HTTPException(status_code=400, detail="url is required")
                resolved = await asyncio.to_thread(self._resolve_media_url, url)
                return JSONResponse({"ok": bool(resolved), "url": resolved})
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/media/preview")
        async def api_media_preview(request: Request) -> JSONResponse:
            """Resolve a share URL to a client-loadable preview asset URL when possible."""
            try:
                url = str(request.query_params.get("url") or "").strip()
                if not url:
                    raise HTTPException(status_code=400, detail="url is required")
                item_type = self._classify_url(url)
                preview_url: Optional[str] = None
                if item_type == "instagram":
                    preview_url = await asyncio.to_thread(self._resolve_instagram_preview_url, url)
                return JSONResponse({"ok": bool(preview_url), "url": preview_url})
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/media/stream")
        async def api_media_stream(request: Request) -> StreamingResponse:
            """Proxy stream an upstream media URL with Range support.

            Accepts query param `url`. For providers that require referer/UA
            headers (e.g., TikTok), sets appropriate defaults.

            Rejects URLs that use a non-http(s) scheme or whose host
            resolves to a private / loopback / link-local / metadata IP.
            """
            try:
                url = str(request.query_params.get("url") or "").strip()
                if not url:
                    raise HTTPException(status_code=400, detail="url is required")
                try:
                    assert_safe_http_url(url)
                except UnsafeURLError as exc:
                    raise HTTPException(status_code=400, detail=f"Rejected URL: {exc}")
                # Forward Range header when present
                fwd_headers: Dict[str, str] = {}
                rng = request.headers.get("Range")
                if rng:
                    fwd_headers["Range"] = rng
                fwd_headers["User-Agent"] = "Mozilla/5.0 (AutoYou)"
                host = urlparse(url).hostname or ""
                if "tiktok.com" in host:
                    fwd_headers["Referer"] = "https://www.tiktok.com/"

                try:
                    import httpx
                    client = httpx.AsyncClient(
                        transport=build_safe_httpx_transport(),
                        timeout=httpx.Timeout(30.0, connect=5.0),
                        trust_env=False,
                    )
                    stream_cm = client.stream("GET", url, headers=fwd_headers)
                    try:
                        upstream = await stream_cm.__aenter__()
                    except Exception:
                        await client.aclose()
                        raise
                except Exception as e:
                    raise HTTPException(status_code=502, detail=f"Upstream error: {e}")

                status = upstream.status_code
                if status not in (200, 206):
                    await stream_cm.__aexit__(None, None, None)
                    await client.aclose()
                    raise HTTPException(status_code=status, detail=f"Upstream responded {status}")

                media_type = upstream.headers.get("Content-Type") or "application/octet-stream"

                async def iter_upstream():
                    try:
                        async for chunk in upstream.aiter_bytes():
                            if chunk:
                                yield chunk
                    finally:
                        await stream_cm.__aexit__(None, None, None)
                        await client.aclose()

                resp = StreamingResponse(iter_upstream(), status_code=status, media_type=media_type)
                # Propagate size/range headers where available
                cl = upstream.headers.get("Content-Length")
                cr = upstream.headers.get("Content-Range")
                if cl:
                    resp.headers["Content-Length"] = str(cl)
                if cr:
                    resp.headers["Content-Range"] = cr
                resp.headers["Accept-Ranges"] = "bytes"
                # Security hardening (H-11): this handler proxies bytes from
                # an arbitrary third-party URL. Force the browser to honour
                # the declared Content-Type (no MIME sniffing) and sandbox
                # the response so any HTML/SVG that does slip through cannot
                # execute script against this origin.
                resp.headers["X-Content-Type-Options"] = "nosniff"
                resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'unsafe-inline'; sandbox"
                resp.headers["Referrer-Policy"] = "no-referrer"
                return resp
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/item/{item_id}/favourite")
        async def api_item_favourite(item_id: int, request: Request) -> JSONResponse:
            """Toggle or set favourite flag for an item."""
            try:
                data = await request.json()
                fav = bool(data.get("favourite", False))
                ok = False
                if getattr(self, "db", None) is not None:
                    try:
                        ok = self.db.set_favourite(item_id, fav)
                    except Exception as e:
                        LOGGER.error(f"DB set_favourite failed: {e}")
                # Mirror in memory
                for i in self.feed_items:
                    if i.get("id") == item_id:
                        i["favourite"] = fav
                        break
                return JSONResponse({"ok": bool(ok)})
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/item/{item_id}/tags")
        async def api_item_add_tag(item_id: int, request: Request) -> JSONResponse:
            """Add a tag to an item."""
            try:
                data = await request.json()
                tag = str(data.get("tag", "")).strip()
                if not tag:
                    raise HTTPException(status_code=400, detail="tag is required")
                ok = False
                if getattr(self, "db", None) is not None:
                    try:
                        ok = self.db.add_tag(item_id, tag, uploads_dir=self.uploads_dir)
                    except Exception as e:
                        LOGGER.error(f"DB add_tag failed: {e}")
                # No need to mirror tags in memory; queries will fetch fresh
                return JSONResponse({"ok": bool(ok)})
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/item/{item_id}/title")
        async def api_item_set_title(item_id: int, request: Request) -> JSONResponse:
            """Update the title for a feed item.

            Mirrors the change into in-memory items and persists via PageFeedDB
            when available.
            """
            try:
                data = await request.json()
                title = str(data.get("title", ""))
                ok = False
                if getattr(self, "db", None) is not None:
                    try:
                        ok = self.db.set_title(item_id, title)
                    except Exception as e:
                        LOGGER.error(f"DB set_title failed: {e}")
                # Mirror in memory for immediate UI feedback
                for i in self.feed_items:
                    if i.get("id") == item_id:
                        i["title"] = title
                        break
                return JSONResponse({"ok": bool(ok)})
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.delete("/api/item/{item_id}/tags/{tag}")
        async def api_item_delete_tag(item_id: int, tag: str) -> JSONResponse:
            """Delete a tag from an item."""
            try:
                ok = False
                if getattr(self, "db", None) is not None:
                    try:
                        ok = self.db.delete_tag(item_id, tag, uploads_dir=self.uploads_dir)
                    except Exception as e:
                        LOGGER.error(f"DB delete_tag failed: {e}")
                return JSONResponse({"ok": bool(ok)})
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/", response_class=HTMLResponse)
        async def home_page(request: Request):
            """Serve the AutoYou Page with its header and first feed page already rendered."""
            access = self._viewer_access(request)
            first_page = await asyncio.to_thread(self._feed_page)
            summary = await asyncio.to_thread(self._page_summary)
            profile = await asyncio.to_thread(self._owner_profile)
            html_content = await asyncio.to_thread(
                self._render_home, access, first_page, summary, self._get_ui_theme(), profile
            )
            return _no_store_html_response(html_content)

        @app.get("/api/profile/avatar")
        async def owner_profile_avatar(request: Request) -> Response:
            """The owner's Admin profile photo; 404 leaves the AutoYou mark in place."""
            photo = self._owner_photo_path()
            if photo is None:
                raise HTTPException(status_code=404, detail="No profile photo")
            try:
                image_bytes = await asyncio.to_thread(read_secure_file, photo)
            except Exception as exc:
                LOGGER.debug("Admin profile photo could not be read: %s", exc)
                raise HTTPException(status_code=404, detail="No profile photo") from exc
            versioned = bool(request.query_params.get("v"))
            return Response(
                content=image_bytes,
                media_type=_PROFILE_PHOTO_TYPES[photo.suffix.lower()],
                headers={
                    "Cache-Control": "private, max-age=86400" if versioned else "no-cache",
                    "X-Content-Type-Options": "nosniff",
                },
            )

        async def save_owner_profile_avatar(request: Request) -> JSONResponse:
            server_module = sys.modules.get("server")
            save = getattr(server_module, "_save_admin_profile_image", None)
            if not callable(save):
                raise HTTPException(status_code=503, detail="Server profile photo storage is unavailable")
            try:
                form = await request.form()
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Invalid image upload form") from exc
            uploaded = form.get("image")
            if uploaded is None or not hasattr(uploaded, "read"):
                raise HTTPException(status_code=400, detail="Profile image file is required")
            try:
                payload = await uploaded.read(64 * 1024 + 1)
            finally:
                await uploaded.close()
            try:
                image_path = await asyncio.to_thread(save, payload)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            webrtc = getattr(server_module, "WEBRTC", None)
            if webrtc is not None:
                await webrtc.broadcast_server_profile()
            return JSONResponse(
                {
                    "success": True,
                    "has_photo": True,
                    "avatar_url": f"./api/profile/avatar?v={image_path.stat().st_mtime_ns}",
                },
                headers={"Cache-Control": "no-store"},
            )

        app.add_api_route("/api/profile/avatar", save_owner_profile_avatar, methods=["POST", "PUT", "PATCH"])

        @app.delete("/api/profile/avatar")
        async def delete_owner_profile_avatar() -> JSONResponse:
            server_module = sys.modules.get("server")
            delete = getattr(server_module, "_delete_admin_profile_image_files", None)
            if not callable(delete):
                raise HTTPException(status_code=503, detail="Server profile photo storage is unavailable")
            await asyncio.to_thread(delete)
            webrtc = getattr(server_module, "WEBRTC", None)
            if webrtc is not None:
                await webrtc.broadcast_server_profile()
            return JSONResponse(
                {"success": True, "has_photo": False},
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/assets/{asset_name}")
        async def page_asset(asset_name: str) -> Response:
            """Serve the page's stylesheet and script; index.html versions their URLs."""
            entry = PAGE_ASSETS.get(asset_name)
            if entry is None:
                raise HTTPException(status_code=404, detail="Not found")
            asset_path, media_type = entry
            if not asset_path.is_file():
                raise HTTPException(status_code=404, detail="Not found")
            return Response(
                content=asset_path.read_bytes(),
                media_type=media_type,
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )

service = PageFeedService()
app = FastAPI(title="AutoYou Page")
install_agent_website_auth(
    app,
    agent_name="page_agent",
    title="AutoYou Page",
    register_auth_routes=True,
    is_authenticated=_is_internal_page_tool_request,
)
service.register_routes(app)
