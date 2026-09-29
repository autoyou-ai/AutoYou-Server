# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-359cb5bfc41ea5542e390e03

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
import re
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent

# Memory is handled by root AutoYou memory tools.

from .page_tool import PageTool
from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime
from shared.session_execution import create_text_llm_response
from shared.remote_access_policy import normalize_remote_access_role

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-359cb5bfc41ea5542e390e03"


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_AUTHENTICATED_ACTOR_ROLE_STATE_KEY = "autoyou_authenticated_actor_role"

# Initialize default PageTool (DB-backed by default). This can be replaced in create_page_agent.
page_tool = PageTool()

_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+", re.IGNORECASE)
_PAGE_FEED_ACTION_RE = re.compile(
    r"\b(?:add|save|post|send|put|submit|ingest|include)\b",
    re.IGNORECASE,
)
_PAGE_FEED_TARGET_RE = re.compile(
    r"\b(?:page\s*feed|for\s*you\s*page|autoforyou|auto\s*for\s*you|feed)\b",
    re.IGNORECASE,
)
_PAGE_FEED_READ_RE = re.compile(
    r"\b(?:show|list|query|view|get|display|count|how many|what(?:'s| is)|open)\b",
    re.IGNORECASE,
)
_PAGE_FEED_COUNT_RE = re.compile(r"\b(?:count|how many|number of)\b", re.IGNORECASE)

def _extract_text_from_llm_request(llm_request: Any) -> str:
    chunks: List[str] = []
    for content in getattr(llm_request, "contents", []) or []:
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks).strip()

def _extract_page_feed_add_url(user_text: str) -> str:
    text = str(user_text or "").strip()
    if not text:
        return ""
    if not _PAGE_FEED_ACTION_RE.search(text) or not _PAGE_FEED_TARGET_RE.search(text):
        return ""
    match = _URL_RE.search(text)
    if not match:
        return ""
    url = match.group(0).rstrip(".,;:!?)]}")
    if url.lower().startswith("www."):
        url = f"http://{url}"
    return url

def _looks_like_page_feed_query(user_text: str) -> bool:
    text = " ".join(str(user_text or "").split()).strip()
    if not text:
        return False
    if _URL_RE.search(text) and _PAGE_FEED_ACTION_RE.search(text):
        return False
    return bool(_PAGE_FEED_TARGET_RE.search(text) and _PAGE_FEED_READ_RE.search(text))

def _format_feed_item_preview(item: Dict[str, Any]) -> str:
    title = str(item.get("title") or item.get("name") or item.get("url") or item.get("open_url") or "Untitled").strip()
    item_id = item.get("id")
    item_type = str(item.get("item_type") or item.get("type") or "").strip()
    source = str(item.get("source") or "").strip()
    label = f"#{item_id}: {title}" if item_id is not None else title
    details = [value for value in (item_type, source) if value]
    if details:
        label += f" ({', '.join(details)})"
    return label

def _format_page_feed_query_response(user_text: str, result: Dict[str, Any]) -> str:
    if result.get("status") != "success":
        return str(result.get("message") or "Failed to read the AutoYou Page feed.")

    items = list(result.get("items") or [])
    count = int(result.get("count") or len(items))
    noun = "item" if count == 1 else "items"
    if _PAGE_FEED_COUNT_RE.search(str(user_text or "")):
        prefix = f"The AutoYou Page feed has {count} {noun}."
    else:
        prefix = f"Found {count} AutoYou Page feed {noun}."
    if not items:
        return prefix
    preview_lines = "\n".join(f"- {_format_feed_item_preview(item)}" for item in items[:5])
    return f"{prefix}\n\nTop items:\n{preview_lines}"

async def _page_agent_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    del callback_context
    user_text = _extract_text_from_llm_request(llm_request)
    url = _extract_page_feed_add_url(user_text)
    # from __debug_provenance_x__ import email
    if not url:
        if not _looks_like_page_feed_query(user_text):
            return None
        result = query_feed(limit=5, timeline_all=True)
        return create_text_llm_response(
            _format_page_feed_query_response(user_text, result),
            custom_metadata={
                "response_author": AGENT_NAME,
                "route_reason": "deterministic_page_feed_query",
            },
        )
    result = add_link(url=url)
    message = str(result.get("message") or result.get("error") or "").strip()
    if not message:
        status = str(result.get("status") or "").strip() or "unknown"
        message = f"Page feed add finished with status: {status}."
    return create_text_llm_response(
        message,
        custom_metadata={
            "response_author": AGENT_NAME,
            "route_reason": "deterministic_page_feed_add",
        },
    )

def add_link(url: str, title: Optional[str] = None, source: Optional[str] = None, item_type: Optional[str] = None) -> dict:
    """Add a hyperlink as a new feed item."""
    try:
        res = page_tool.add_link(url=url, title=title, source=source, item_type=item_type)
        if res.get("success"):
            item = res.get("item")
            return {
                "status": "success",
                "item": item,
                "message": res.get("message") or f"Added link: {item.get('open_url') or item.get('url') if item else url}",
            }
        return {"status": "error", "message": res.get("error", "Failed to add link")}
    except Exception as e:
        return {"status": "error", "message": f"Exception adding link: {e}"}

def save_blob(
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
) -> dict:
    """Persist a binary blob and register it in the page feed.

    Wraps PageTool.add_blob and returns the created item or error.
    """
    try:
        res = page_tool.add_blob(
            filename=filename,
            data_base64=data_base64,
            mimetype=mimetype,
            source=source,
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
            title=title,
            metadata=metadata,
        )
        if res.get("success"):
            return {
                "status": "success",
                "item": res.get("item"),
                "message": res.get("message"),
            }
        return {"status": "error", "message": res.get("error", "Failed to save blob")}
    except Exception as e:
        return {"status": "error", "message": f"Exception saving blob: {e}"}

def save_blob_from_path(
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
) -> dict:
    """Persist a binary blob from a local path and register it in the page feed.

    Wraps PageTool.add_blob_from_path and returns the created item or error.
    """
    try:
        res = page_tool.add_blob_from_path(
            path=path,
            filename=filename,
            mimetype=mimetype,
            source=source,
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
            title=title,
            metadata=metadata,
        )
        if res.get("success"):
            return {
                "status": "success",
                "item": res.get("item"),
                "message": res.get("message"),
            }
        return {"status": "error", "message": res.get("error", "Failed to save blob from path")}
    except Exception as e:
        return {"status": "error", "message": f"Exception saving blob from path: {e}"}

def ingest_attachments(
    attachments: List[Dict[str, Any]],
    *,
    source: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    default_title: Optional[str] = None,
) -> dict:
    """Ingest a list of attachments and register them in the page feed.

    - URL-only items are added via add_link.
    - Data-backed items (base64 + filename/mimetype) are saved via save_blob.
    Returns a compact summary with items created and any errors.
    """
    try:
        res = page_tool.ingest_attachments(
            attachments=attachments,
            source=source,
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
            default_title=default_title,
        )
        if res.get("success"):
            return {
                "status": "success",
                "count": len(res.get("items", [])),
                "items": res.get("items", []),
                "skipped": res.get("skipped", []),
                "errors": res.get("errors", []),
                "message": res.get("message"),
            }
        return {"status": "error", "message": res.get("error", "Failed to ingest attachments")}
    except Exception as e:
        return {"status": "error", "message": f"Exception ingesting attachments: {e}"}

def delete_item(item_id: int) -> dict:
    """Delete a feed item by id."""
    try:
        res = page_tool.delete_item(int(item_id))
        if res.get("success"):
            return {
                "status": "success",
                "deleted": res.get("deleted", 0),
                "cleanup": res.get("cleanup", {}),
            }
        return {"status": "error", "message": res.get("error", "Failed to delete item")}
    except Exception as e:
        return {"status": "error", "message": f"Exception deleting item: {e}"}

def clear_feed() -> dict:
    """Clear all feed items."""
    try:
        res = page_tool.clear_feed()
        if res.get("success"):
            return {
                "status": "success",
                "count": res.get("count", 0),
                "cleanup": res.get("cleanup", {}),
            }
        return {"status": "error", "message": res.get("error", "Failed to clear feed")}
    except Exception as e:
        return {"status": "error", "message": f"Exception clearing feed: {e}"}

def list_feed(days: int = 0, limit: Optional[int] = None) -> dict:
    """List recent feed items."""
    try:
        res = page_tool.list_feed(days=days, limit=limit)
        if res.get("success"):
            items = res.get("items", [])
            return {"status": "success", "items": items, "count": len(items)}
        return {"status": "error", "message": res.get("error", "Failed to list feed")}
    except Exception as e:
        return {"status": "error", "message": f"Exception listing feed: {e}"}

def query_feed(
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
) -> dict:
    """Query feed items with filters."""
    try:
        res = page_tool.query_feed(
            order=order,
            types=types,
            source=source,
            sources=sources,
            date_from=date_from,
            date_to=date_to,
            favourites_only=favourites_only,
            tag_search=tag_search,
            limit=limit,
            timeline_all=timeline_all,
        )
        if res.get("success"):
            items = res.get("items", [])
            return {"status": "success", "items": items, "count": len(items)}
        return {"status": "error", "message": res.get("error", "Failed to query feed")}
    except Exception as e:
        return {"status": "error", "message": f"Exception querying feed: {e}"}

def set_favourite(item_id: int, favourite: bool = True) -> dict:
    """Set favourite flag for an item."""
    try:
        res = page_tool.set_favourite(int(item_id), bool(favourite))
        if res.get("success"):
            return {"status": "success", "ok": bool(res.get("ok"))}
        return {"status": "error", "message": res.get("error", "Failed to set favourite")}
    except Exception as e:
        return {"status": "error", "message": f"Exception setting favourite: {e}"}

def add_tag(item_id: int, tag: str) -> dict:
    """Add a tag to an item."""
    try:
        res = page_tool.add_tag(int(item_id), str(tag))
        if res.get("success"):
            return {"status": "success", "ok": bool(res.get("ok"))}
        return {"status": "error", "message": res.get("error", "Failed to add tag")}
    except Exception as e:
        return {"status": "error", "message": f"Exception adding tag: {e}"}

def delete_tag(item_id: int, tag: str) -> dict:
    """Delete a tag from an item."""
    try:
        res = page_tool.delete_tag(int(item_id), str(tag))
        if res.get("success"):
            return {"status": "success", "ok": bool(res.get("ok"))}
        return {"status": "error", "message": res.get("error", "Failed to delete tag")}
    except Exception as e:
        return {"status": "error", "message": f"Exception deleting tag: {e}"}

def list_tags(item_id: int) -> dict:
    """List tags for an item (DB mode only)."""
    try:
        res = page_tool.list_tags(int(item_id))
        if res.get("success"):
            return {"status": "success", "tags": res.get("tags", [])}
        return {"status": "error", "message": res.get("error", "Failed to list tags")}
    except Exception as e:
        return {"status": "error", "message": f"Exception listing tags: {e}"}

def _page_photo_actor_role(tool_context: Optional[Any]) -> str:
    state = getattr(tool_context, "state", None)
    try:
        return normalize_remote_access_role(state.get(_AUTHENTICATED_ACTOR_ROLE_STATE_KEY))
    except Exception:
        return "viewer"

def get_server_display_photo(tool_context: Optional[Any] = None) -> dict:
    """Read the current server-owned AutoYou Page photo."""
    result = page_tool.get_server_display_photo()
    if result.get("success"):
        result["message"] = (
            "The server has a display photo."
            if result.get("has_photo")
            else "The server is using the AutoYou mark as its display photo."
        )
    return result

def update_server_display_photo(path: str, tool_context: Optional[Any] = None) -> dict:
    """Set the server-owned Page photo from an image attachment path."""
    if _page_photo_actor_role(tool_context) not in {"editor", "admin"}:
        return {"status": "error", "message": "Only an editor or admin can change the server display photo."}
    result = page_tool.update_server_display_photo_from_path(path)
    if result.get("success"):
        return {"status": "success", "message": "Updated the server display photo."}
    message = result.get("detail") or result.get("error") or "Could not update the server display photo."
    return {"status": "error", "message": message}

def delete_server_display_photo(tool_context: Optional[Any] = None) -> dict:
    """Remove the server-owned Page photo; admin access is required."""
    if _page_photo_actor_role(tool_context) != "admin":
        return {"status": "error", "message": "Only an admin can remove the server display photo."}
    result = page_tool.delete_server_display_photo()
    if result.get("success"):
        return {"status": "success", "message": "Removed the server display photo; the AutoYou mark is showing again."}
    message = result.get("detail") or result.get("error") or "Could not remove the server display photo."
    return {"status": "error", "message": message}

def create_page_agent(model_config, base_url: Optional[str] = None, db_path: Optional[str] = None) -> Agent:
    """Create the Page Agent with provided model config.

    If `base_url` is provided, the agent tools will use HTTP endpoints from
    autoyou_page_service.py. Otherwise tools operate directly on the local DB.
    """
    global page_tool
    page_tool = PageTool(base_url=base_url, db_path=db_path)

    tools = [
        add_link,
        save_blob,
        save_blob_from_path,
        ingest_attachments,
        delete_item,
        clear_feed,
        list_feed,
        query_feed,
        set_favourite,
        add_tag,
        delete_tag,
        list_tags,
        get_server_display_photo,
        update_server_display_photo,
        delete_server_display_photo,
        get_current_datetime,
    ]

    # Note: Memory tools omitted. Root Memory Agent now provides access.

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
        before_model_callback=_page_agent_before_model_callback,
    )
