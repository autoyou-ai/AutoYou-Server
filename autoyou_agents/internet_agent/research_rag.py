# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Structured web-page extractor and session-scoped Web Research RAG store.

Design goals:
- Zero extra third-party dependencies (stdlib ``sqlite3`` + ``BeautifulSoup``).
- Extracts clean readable Markdown/text, headlines, metadata, JSON-LD article
  bodies, and RSS/Atom feed links without letting inline scripts or nav chrome
  drown out the page content.
- Indexes the FULL extracted text of every fetched/scraped web page into a
  bounded in-memory SQLite FTS5 (BM25) store with an automatic pure-Python BM25
  fallback, so follow-up questions can retrieve any detail from long articles
  without re-fetching the network or truncating at 5,000 characters.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import json
import math
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
import uuid
from collections import Counter, OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

_NON_CONTENT_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "iframe",
    "canvas",
    "form",
    "button",
    "input",
    "select",
    "textarea",
)
_CHROME_TAGS = ("nav", "footer", "aside")
_MAIN_SELECTORS = (
    "article",
    "main",
    '[role="main"]',
    "#main-content",
    "#content",
    ".post-content",
    ".article-body",
    ".entry-content",
    ".markdown-body",
    ".document",
)
_NON_NAVIGABLE_PREFIXES = ("javascript:", "mailto:", "tel:", "data:", "blob:", "#")
_TOKEN_RE = re.compile(r"\w{2,}", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "that", "this", "with", "from", "have", "were",
        "are", "was", "but", "not", "you", "your", "all", "can", "had", "her",
        "his", "one", "our", "out", "has", "what", "when", "where", "which",
        "who", "will", "about", "into", "more", "than", "then", "them", "they",
        "their", "there", "these", "those", "would", "could", "should", "also",
    }
)
_NON_ARTICLE_HEADING_RE = re.compile(
    r"\b(?:drm system|more top stories|sign in|sign up|streaming now|watch live|cookie|privacy policy)\b",
    re.IGNORECASE,
)

_MAX_DOCUMENTS = 120
_MAX_DOCUMENT_CHARS = 1_500_000
_MAX_STORE_CHARS = 4_000_000
_CHUNK_TARGET_CHARS = 900
_CHUNK_OVERLAP_CHARS = 150


def _absolutize_url(base_url: str, candidate: Any) -> str:
    href = str(candidate or "").strip()
    if not href or href.lower().startswith(_NON_NAVIGABLE_PREFIXES):
        return ""
    try:
        resolved = urljoin(base_url, href)
    except Exception:
        return ""
    return resolved if resolved.lower().startswith(("http://", "https://")) else ""


def _tokenize(text: str) -> List[str]:
    return [
        tok.lower()
        for tok in _TOKEN_RE.findall(str(text or ""))
        if len(tok) >= 2 and tok.lower() not in _STOPWORDS
    ]


def _extract_json_ld_articles(soup: BeautifulSoup) -> Tuple[str, str, str]:
    """Extract (headline, date_published, article_body) from JSON-LD scripts if present."""
    headline = ""
    published = ""
    body_parts: List[str] = []

    def _visit(obj: Any) -> None:
        nonlocal headline, published
        if isinstance(obj, list):
            for item in obj:
                _visit(item)
            return
        if not isinstance(obj, dict):
            return
        if "@graph" in obj:
            _visit(obj["@graph"])
        if not headline:
            hl = obj.get("headline") or obj.get("name")
            if isinstance(hl, str) and len(hl.strip()) >= 8:
                headline = hl.strip()[:240]
        if not published:
            dp = obj.get("datePublished") or obj.get("dateModified") or obj.get("uploadDate")
            if isinstance(dp, str) and dp.strip():
                published = dp.strip()[:48]
        article_body = obj.get("articleBody")
        if isinstance(article_body, str) and len(article_body.strip()) >= 120:
            body_parts.append(" ".join(article_body.split()))

    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.IGNORECASE)}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw or len(raw) > 500_000:
            continue
        try:
            _visit(json.loads(raw))
        except Exception:
            continue

    return headline, published, "\n\n".join(body_parts)


def _extract_feed_links(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """Discover RSS/Atom feed URLs advertised in `<head>`."""
    feeds: List[Dict[str, str]] = []
    seen = set()
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        ltype = str(link.get("type") or "").lower()
        if "alternate" not in rel:
            continue
        if not any(tok in ltype for tok in ("rss", "atom", "xml")):
            continue
        href = _absolutize_url(base_url, link.get("href"))
        if not href or href in seen:
            continue
        seen.add(href)
        feeds.append({"url": href, "title": str(link.get("title") or "").strip()[:120], "type": ltype})
        if len(feeds) >= 8:
            break
    return feeds


def _dom_to_markdown_blocks(root: Any) -> str:
    """Convert a BeautifulSoup subtree into clean readable text/Markdown."""
    blocks: List[str] = []
    seen_lines = set()

    def _emit(line: str) -> None:
        cleaned = " ".join(line.split()).strip()
        if not cleaned or len(cleaned) < 2:
            return
        if cleaned in seen_lines and len(cleaned) < 120:
            return
        seen_lines.add(cleaned)
        blocks.append(cleaned)

    block_tags = root.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote", "tr"])
    if block_tags:
        for tag in block_tags:
            # Skip nested blocks already represented inside a parent paragraph/pre
            if tag.name == "p" and tag.find_parent(["li", "blockquote", "pre"]):
                continue
            if tag.name in {"h1", "h2", "h3", "h4"}:
                text = " ".join(tag.get_text(" ", strip=True).split())
                if text:
                    level = int(tag.name[1])
                    _emit("#" * level + " " + text)
            elif tag.name == "pre":
                code = tag.get_text("\n").strip()
                if code:
                    blocks.append("```\n" + code + "\n```")
            elif tag.name == "li":
                text = " ".join(tag.get_text(" ", strip=True).split())
                if text and len(text) >= 4:
                    _emit("- " + text)
            elif tag.name == "tr":
                cells = [
                    " ".join(cell.get_text(" ", strip=True).split())
                    for cell in tag.find_all(["th", "td"])
                ]
                cells = [c for c in cells if c]
                if len(cells) >= 2:
                    _emit(" | ".join(cells))
            else:
                text = " ".join(tag.get_text(" ", strip=True).split())
                if text:
                    _emit(text)

    joined = "\n\n".join(blocks).strip()
    if len(joined) >= 200:
        return joined
    fallback = " ".join(root.get_text(" ", strip=True).split())
    return fallback if len(fallback) > len(joined) else joined


def extract_structured_feed(xml_text: str, url: str) -> Dict[str, Any]:
    """Parse an RSS or Atom XML feed into structured text and headline items."""
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return {}

    tag_lower = root.tag.lower()
    items_out: List[Dict[str, str]] = []
    feed_title = ""

    if tag_lower.endswith("rss") or root.find("./channel") is not None:
        channel = root.find("./channel") or root
        feed_title = (channel.findtext("title") or "").strip()
        for item in channel.findall("item")[:40]:
            title = " ".join((item.findtext("title") or "").split()).strip()
            link = (item.findtext("link") or "").strip()
            desc_raw = (item.findtext("description") or "").strip()
            desc = BeautifulSoup(desc_raw, "html.parser").get_text(" ", strip=True) if desc_raw else ""
            pub = (item.findtext("pubDate") or "").strip()
            if title and link:
                items_out.append({"title": title[:240], "url": link, "snippet": desc[:400], "published": pub[:64]})
    elif tag_lower.endswith("feed"):
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"
        feed_title = (root.findtext(f"{ns}title") or "").strip()
        for entry in root.findall(f"{ns}entry")[:40]:
            title = " ".join((entry.findtext(f"{ns}title") or "").split()).strip()
            link = ""
            for link_el in entry.findall(f"{ns}link"):
                href = (link_el.attrib.get("href") or "").strip()
                rel = (link_el.attrib.get("rel") or "alternate").strip()
                if href and (rel == "alternate" or not link):
                    link = href
            summary_raw = (entry.findtext(f"{ns}summary") or entry.findtext(f"{ns}content") or "").strip()
            desc = BeautifulSoup(summary_raw, "html.parser").get_text(" ", strip=True) if summary_raw else ""
            pub = (entry.findtext(f"{ns}updated") or entry.findtext(f"{ns}published") or "").strip()
            if title and link:
                items_out.append({"title": title[:240], "url": link, "snippet": desc[:400], "published": pub[:64]})

    if not items_out:
        return {}

    lines = [f"# {feed_title}" if feed_title else f"# Feed ({url})"]
    for item in items_out:
        pub_part = f" [{item['published']}]" if item.get("published") else ""
        snippet_part = f" — {item['snippet']}" if item.get("snippet") else ""
        lines.append(f"- {item['title']}{pub_part} ({item['url']}){snippet_part}")

    return {
        "url": url,
        "title": feed_title or url,
        "text": "\n".join(lines),
        "headlines": [{"title": i["title"], "url": i["url"], "published": i.get("published", "")} for i in items_out[:25]],
        "links": [{"url": i["url"], "text": i["title"][:120]} for i in items_out[:40]],
        "feeds": [],
        "metadata": {"title": feed_title or url, "url": url, "content_type": "feed"},
    }


def extract_structured_page(raw_content: str, url: str, *, content_type: str = "") -> Dict[str, Any]:
    """Extract clean readable text, headlines, links, feeds, and metadata from HTML/XML/JSON."""
    ct_lower = str(content_type or "").lower()
    stripped = str(raw_content or "").lstrip()

    if (
        "xml" in ct_lower
        or "rss" in ct_lower
        or "atom" in ct_lower
        or stripped.startswith(("<?xml", "<rss", "<feed"))
    ):
        feed_data = extract_structured_feed(raw_content, url)
        if feed_data:
            return feed_data

    if "json" in ct_lower or (stripped.startswith(("{", "[")) and not stripped.startswith("<!")):
        try:
            parsed_json = json.loads(raw_content)
            pretty = json.dumps(parsed_json, indent=2, ensure_ascii=False)
            return {
                "url": url,
                "title": f"JSON response ({urlsplit(url).netloc})",
                "text": pretty[:40_000],
                "headlines": [],
                "links": [],
                "feeds": [],
                "metadata": {"url": url, "content_type": "application/json"},
            }
        except Exception:
            pass

    if "text/plain" in ct_lower or "text/markdown" in ct_lower:
        return {
            "url": url,
            "title": urlsplit(url).path.rsplit("/", 1)[-1] or url,
            "text": raw_content[:60_000],
            "headlines": [],
            "links": [],
            "feeds": [],
            "metadata": {"url": url, "content_type": ct_lower or "text/plain"},
        }

    soup = BeautifulSoup(raw_content, "html.parser")
    jsonld_headline, jsonld_published, jsonld_body = _extract_json_ld_articles(soup)
    feeds = _extract_feed_links(soup, url)

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if not title and og_title:
        title = str(og_title.get("content") or "").strip()
    title = title or jsonld_headline or "No title"

    meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    description = str(meta_desc.get("content") or "").strip() if meta_desc else ""
    published = jsonld_published
    if not published:
        pub_meta = soup.find("meta", attrs={"property": "article:published_time"}) or soup.find("meta", attrs={"name": "pubdate"})
        if pub_meta:
            published = str(pub_meta.get("content") or "").strip()[:48]
        else:
            time_el = soup.find("time", attrs={"datetime": True})
            if time_el:
                published = str(time_el.get("datetime") or "").strip()[:48]

    for tag in soup(list(_NON_CONTENT_TAGS)):
        try:
            tag.decompose()
        except Exception:
            continue

    # Choose main content region when available
    best_region = None
    best_len = 0
    for selector in _MAIN_SELECTORS:
        try:
            for el in soup.select(selector):
                txt_len = len(el.get_text(" ", strip=True))
                if txt_len > best_len:
                    best_len = txt_len
                    best_region = el
        except Exception:
            continue

    content_root = best_region if (best_region is not None and best_len >= 350) else soup

    # Drop nav/footer/aside chrome inside a cloned or selected subtree when safe
    if content_root is soup:
        for chrome in soup(list(_CHROME_TAGS)):
            try:
                if len(chrome.get_text(" ", strip=True)) < best_len or best_len == 0:
                    chrome.decompose()
            except Exception:
                continue

    readable_text = _dom_to_markdown_blocks(content_root)
    if jsonld_body and len(jsonld_body) > len(readable_text) + 200:
        readable_text = f"# {title}\n\n{jsonld_body}"

    # Extract linked headlines
    headlines: List[Dict[str, str]] = []
    seen_titles = set()
    for heading in content_root.find_all(("h1", "h2", "h3")):
        h_text = " ".join(heading.get_text(" ", strip=True).split()).strip()
        h_low = h_text.lower()
        if not (12 <= len(h_text) <= 240) or h_low in seen_titles:
            continue
        if _NON_ARTICLE_HEADING_RE.search(h_text):
            continue
        anchor = heading.find("a", href=True) or heading.find_parent("a", href=True)
        if anchor is None:
            continue
        href = _absolutize_url(url, anchor.get("href"))
        if not href:
            continue
        seen_titles.add(h_low)
        headlines.append({"title": h_text, "url": href})
        if len(headlines) >= 24:
            break

    # Extract deduplicated links
    links: List[Dict[str, str]] = []
    seen_links = set()
    for anchor in content_root.find_all("a", href=True):
        href = _absolutize_url(url, anchor.get("href"))
        txt = " ".join(anchor.get_text(" ", strip=True).split())
        if not href or not txt or href in seen_links:
            continue
        seen_links.add(href)
        links.append({"url": href, "text": txt[:120]})
        if len(links) >= 50:
            break

    return {
        "url": url,
        "title": title,
        "text": readable_text,
        "headlines": headlines,
        "links": links,
        "feeds": feeds,
        "metadata": {
            "title": title,
            "description": description[:400],
            "published": published,
            "url": url,
            "content_type": ct_lower or "text/html",
        },
    }


def _chunk_text(text: str, *, target_chars: int = _CHUNK_TARGET_CHARS, overlap: int = _CHUNK_OVERLAP_CHARS) -> List[str]:
    clean = str(text or "").strip()
    if not clean:
        return []
    if len(clean) <= target_chars:
        return [clean]

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", clean) if p.strip()]
    chunks: List[str] = []
    buf: List[str] = []
    buf_len = 0

    for para in paragraphs:
        if len(para) > target_chars * 1.4:
            if buf:
                chunks.append("\n\n".join(buf))
                buf, buf_len = [], 0
            step = max(200, target_chars - overlap)
            for start in range(0, len(para), step):
                piece = para[start : start + target_chars].strip()
                if piece:
                    chunks.append(piece)
            continue

        if buf and buf_len + len(para) + 2 > target_chars:
            joined = "\n\n".join(buf)
            chunks.append(joined)
            tail = joined[-overlap:].strip() if overlap > 0 else ""
            buf = [tail, para] if tail else [para]
            buf_len = sum(len(x) for x in buf)
        else:
            buf.append(para)
            buf_len += len(para) + 2

    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


class WebResearchRAGStore:
    """Thread-safe bounded in-memory BM25/FTS5 store for fetched web content."""

    def __init__(self, *, max_documents: int = _MAX_DOCUMENTS):
        self._lock = threading.RLock()
        self._max_documents = max(1, int(max_documents))
        self._docs: Dict[str, Dict[str, Any]] = {}
        self._doc_order: List[str] = []
        self._chunks: List[Dict[str, Any]] = []
        self._total_chars = 0
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._fts_enabled = False
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS web_chunks (
                    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    fetched_at REAL NOT NULL
                )
                """
            )
            try:
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS web_chunks_fts USING fts5(
                        url UNINDEXED,
                        title,
                        body,
                        content='web_chunks',
                        content_rowid='chunk_id',
                        tokenize='unicode61'
                    )
                    """
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False
            self._conn.commit()

    def clear(self) -> None:
        with self._lock:
            self._docs.clear()
            self._doc_order.clear()
            self._chunks.clear()
            self._total_chars = 0
            if self._fts_enabled:
                self._conn.execute("INSERT INTO web_chunks_fts(web_chunks_fts) VALUES('delete-all')")
            self._conn.execute("DELETE FROM web_chunks")
            self._conn.commit()

    def _evict_url_locked(self, url: str) -> None:
        rows = self._conn.execute("SELECT chunk_id, title, body FROM web_chunks WHERE url = ?", (url,)).fetchall()
        if self._fts_enabled:
            for chunk_id, title, body in rows:
                self._conn.execute(
                    "INSERT INTO web_chunks_fts(web_chunks_fts, rowid, url, title, body) VALUES('delete', ?, ?, ?, ?)",
                    (chunk_id, url, title, body),
                )
        self._conn.execute("DELETE FROM web_chunks WHERE url = ?", (url,))
        self._chunks = [c for c in self._chunks if c["url"] != url]
        removed = self._docs.pop(url, None)
        if removed:
            self._total_chars -= removed["char_count"]
        if url in self._doc_order:
            self._doc_order.remove(url)

    def index_document(
        self,
        url: str,
        title: str,
        full_text: str,
        *,
        headlines: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_url = str(url or "").strip()
        if not clean_url:
            return {"indexed": False, "chunks": 0}
        clean_title = str(title or clean_url).strip()[:240]
        parts = [str(full_text or "").strip()]
        if headlines:
            hl_lines = [
                f"- {str(h.get('title') or '').strip()} ({str(h.get('url') or '').strip()})"
                for h in headlines[:20]
                if isinstance(h, dict) and str(h.get("title") or "").strip()
            ]
            if hl_lines:
                parts.insert(0, "Headlines:\n" + "\n".join(hl_lines))
        combined = "\n\n".join(p for p in parts if p).strip()
        if not combined:
            return {"indexed": False, "chunks": 0}

        original_chars = len(combined)
        combined = combined[:_MAX_DOCUMENT_CHARS]

        chunks = _chunk_text(combined)
        now = time.time()

        with self._lock:
            if clean_url in self._docs:
                self._evict_url_locked(clean_url)
            while self._doc_order and (len(self._doc_order) >= self._max_documents or self._total_chars + len(combined) > _MAX_STORE_CHARS):
                oldest = self._doc_order[0]
                self._evict_url_locked(oldest)

            for idx, chunk_body in enumerate(chunks):
                cur = self._conn.execute(
                    "INSERT INTO web_chunks (url, title, chunk_index, body, fetched_at) VALUES (?, ?, ?, ?, ?)",
                    (clean_url, clean_title, idx, chunk_body, now),
                )
                row_id = cur.lastrowid
                if self._fts_enabled:
                    self._conn.execute(
                        "INSERT INTO web_chunks_fts (rowid, url, title, body) VALUES (?, ?, ?, ?)",
                        (row_id, clean_url, clean_title, chunk_body),
                    )
                self._chunks.append(
                    {
                        "url": clean_url,
                        "title": clean_title,
                        "chunk_index": idx,
                        "body": chunk_body,
                    }
                )

            self._conn.commit()
            self._docs[clean_url] = {
                "url": clean_url,
                "title": clean_title,
                "char_count": len(combined),
                "chunk_count": len(chunks),
                "fetched_at": now,
                "metadata": dict(metadata or {}),
            }
            self._doc_order.append(clean_url)
            self._total_chars += len(combined)

        return {"indexed": True, "url": clean_url, "chunks": len(chunks), "total_chars": len(combined),
                "truncated": original_chars > len(combined)}

    def query(
        self,
        query: str,
        *,
        max_chunks: int = 6,
        url_filter: str = "",
    ) -> List[Dict[str, Any]]:
        tokens = _tokenize(query)
        limit = max(1, min(int(max_chunks or 6), 15))
        url_sub = str(url_filter or "").strip().lower()

        with self._lock:
            if not self._docs:
                return []

            if not tokens:
                # Return leading chunks from most recent docs
                out: List[Dict[str, Any]] = []
                for url in reversed(self._doc_order):
                    if url_sub and url_sub not in url.lower():
                        continue
                    for ch in self._chunks:
                        if ch["url"] == url and ch["chunk_index"] == 0:
                            out.append(
                                {
                                    "url": ch["url"],
                                    "title": ch["title"],
                                    "chunk_index": ch["chunk_index"],
                                    "score": 1.0,
                                    "excerpt": ch["body"][:1000],
                                }
                            )
                            break
                    if len(out) >= limit:
                        break
                return out

            if self._fts_enabled:
                fts_expr = " OR ".join(f'"{tok}"*' for tok in tokens[:10])
                try:
                    sql = (
                        "SELECT w.url, w.title, w.chunk_index, w.body, bm25(web_chunks_fts, 2.0, 1.0) AS rank "
                        "FROM web_chunks_fts f JOIN web_chunks w ON w.chunk_id = f.rowid "
                        "WHERE web_chunks_fts MATCH ? "
                    )
                    params: List[Any] = [fts_expr]
                    if url_sub:
                        sql += "AND instr(lower(w.url), ?) > 0 "
                        params.append(url_sub)
                    sql += "ORDER BY rank ASC LIMIT ?"
                    params.append(limit * 3)
                    rows = self._conn.execute(sql, params).fetchall()
                    if rows:
                        results: List[Dict[str, Any]] = []
                        per_url: Dict[str, int] = {}
                        for url, title, chunk_index, body, rank in rows:
                            if per_url.get(url, 0) >= 3 and len(self._docs) > 2:
                                continue
                            per_url[url] = per_url.get(url, 0) + 1
                            results.append(
                                {
                                    "url": url,
                                    "title": title,
                                    "chunk_index": int(chunk_index),
                                    "score": round(max(0.01, -float(rank)), 4),
                                    "excerpt": body[:1000],
                                }
                            )
                            if len(results) >= limit:
                                break
                        return results
                except sqlite3.OperationalError:
                    pass

            # Pure-Python BM25 fallback
            candidates = [
                ch for ch in self._chunks if not url_sub or url_sub in ch["url"].lower()
            ]
            if not candidates:
                return []
            for chunk in candidates:
                if "tokens" not in chunk:
                    chunk["tokens"] = Counter(_tokenize(chunk["title"] + " " + chunk["body"]))
            total_docs = len(candidates)
            df = Counter()
            avg_dl = sum(sum(c["tokens"].values()) for c in candidates) / max(1, total_docs)
            for c in candidates:
                for tok in set(tokens):
                    if tok in c["tokens"]:
                        df[tok] += 1

            scored = []
            for c in candidates:
                dl = max(1, sum(c["tokens"].values()))
                score = 0.0
                for tok in tokens:
                    tf = c["tokens"].get(tok, 0)
                    if tf <= 0:
                        continue
                    idf = math.log(1.0 + (total_docs - df[tok] + 0.5) / (df[tok] + 0.5))
                    score += idf * ((tf * 2.5) / (tf + 1.5 * (1.0 - 0.75 + 0.75 * dl / max(1.0, avg_dl))))
                if score > 0:
                    scored.append((score, c))

            scored.sort(key=lambda item: item[0], reverse=True)
            return [
                {
                    "url": c["url"],
                    "title": c["title"],
                    "chunk_index": c["chunk_index"],
                    "score": round(sc, 4),
                    "excerpt": c["body"][:1000],
                }
                for sc, c in scored[:limit]
            ]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "documents_indexed": len(self._docs),
                "chunks_indexed": len(self._chunks),
                "fts5_enabled": self._fts_enabled,
                "total_chars": self._total_chars,
                "urls": list(self._doc_order[-12:]),
            }


_RESEARCH_RAG_STORE = WebResearchRAGStore()
_SCOPED_STORES: OrderedDict[str, WebResearchRAGStore] = OrderedDict()
_SCOPED_STORES_LOCK = threading.RLock()
_CURRENT_STORE: ContextVar[Optional[WebResearchRAGStore]] = ContextVar("autoyou_research_store", default=None)
_SCOPE_STATE_KEY = "_autoyou_web_research_scope"


def get_research_rag_store(tool_context: Any = None) -> WebResearchRAGStore:
    if tool_context is None:
        return _CURRENT_STORE.get() or _RESEARCH_RAG_STORE
    state = tool_context.state
    scope = state.get(_SCOPE_STATE_KEY)
    if not isinstance(scope, str) or not scope:
        scope = uuid.uuid4().hex
        state[_SCOPE_STATE_KEY] = scope
    with _SCOPED_STORES_LOCK:
        store = _SCOPED_STORES.pop(scope, None) or WebResearchRAGStore()
        _SCOPED_STORES[scope] = store
        while len(_SCOPED_STORES) > 64:
            # Active calls retain their reference; never close a connection
            # underneath another task when evicting a cold session's cache.
            _SCOPED_STORES.popitem(last=False)
        return store


@contextmanager
def research_scope(tool_context: Any = None):
    """Isolate concurrent ADK sessions, including their nested AgentTool calls."""
    store = get_research_rag_store(tool_context) if tool_context is not None else (_CURRENT_STORE.get() or WebResearchRAGStore())
    token = _CURRENT_STORE.set(store)
    try:
        yield store
    finally:
        _CURRENT_STORE.reset(token)


def reset_research_rag_store() -> None:
    get_research_rag_store().clear()
