# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Sanctioned multi-provider web search orchestrator for AutoYou Internet Agent.

Replaces fragile single-engine HTML scraping (which hits anti-bot challenges or
returns off-topic RSS noise) with fast, concurrent queries against:

1. Operator-configured official search APIs (when keys/URLs are provided):
   - SearXNG (``SEARXNG_BASE_URL`` / ``AUTOYOU_SEARXNG_URL``)
   - Brave Search API (``BRAVE_SEARCH_API_KEY``)
   - Tavily Search API (``TAVILY_API_KEY``)
   - Exa Search API (``EXA_API_KEY``)
   - Serper API (``SERPER_API_KEY``)
2. Zero-config sanctioned public APIs and RSS/Atom feeds (run in parallel via
   the caller's ``requests.Session`` so unit-test mocks intercept cleanly and no
   anti-bot challenges or CAPTCHAs are ever bypassed):
   - Google News RSS (news, current events, announcements, ``site:`` queries)
   - Bing News RSS (news supplement when non-Bing providers confirm real network)
   - Hacker News Algolia API (software, AI, engineering, startups, releases)
   - GitHub Search API (repositories, open-source libraries, frameworks)
   - StackExchange API (programming Q&A, error debugging)
   - arXiv Atom API (scientific papers, ML/AI research)
   - DuckDuckGo Instant Answer JSON API (entity definitions & official links)
   - Direct RSS/Atom feed & homepage headline discovery for ``site:<domain>``
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from types import SimpleNamespace
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_PROVIDER_TIMEOUT = 6.0
_SITE_RE = re.compile(r"\bsite:(\S+)", re.IGNORECASE)
_NEWS_INTENT_RE = re.compile(
    r"\b(?:news|headlines?|latest|today|breaking|update|updates|announce\w*|release|released|202\d|current|recent|world|market|stock|election|war|sport|weather|economy)\b",
    re.IGNORECASE,
)
_TECH_CODE_INTENT_RE = re.compile(
    r"\b(?:python|javascript|typescript|rust|golang|swift|kotlin|java|c\+\+|react|fastapi|django|flask|docker|"
    r"kubernetes|git|github|npm|pip|pypi|linux|macos|windows|api|sdk|library|framework|cli|llm|ollama|sqlite|"
    r"playwright|nuitka|electron|html5|css|canvas|error|exception|traceback|bug|fix|install|docs|documentation|"
    r"release\s+notes|pep\s*\d+|rfc\s*\d+|benchmark|model|open[\s-]?source|agent)\b",
    re.IGNORECASE,
)
_PAPER_INTENT_RE = re.compile(
    r"\b(?:paper|arxiv|preprint|study|research|theorem|benchmark|architecture|transformer|attention|diffusion|"
    r"quantum|algorithm|neural|dataset|survey|citation|pdf)\b",
    re.IGNORECASE,
)
_TRACKING_QUERY_PARAMS = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source", "ocid",
    }
)
_DDG_CHALLENGE_MARKERS = (
    "anomaly-modal",
    "anomaly.js?",
    "bots use duckduckgo too",
    "confirm this search was made by a human",
)
_ASSISTANT_HEADERS = {
    "User-Agent": "AutoYou/1.0 (https://autoyou.me; personal assistant web research)",
    "Accept": "application/json, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
}


def _looks_like_bot_challenge(text: str) -> bool:
    low = str(text or "").lower()
    return any(marker in low for marker in _DDG_CHALLENGE_MARKERS)


def canonicalize_url(url: str) -> str:
    """Normalize URL for deduplication by removing fragments and tracking params."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            return raw
        filtered_qs = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if k.lower() not in _TRACKING_QUERY_PARAMS
        ]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower().removeprefix("www."),
                path,
                urlencode(filtered_qs, doseq=True),
                "",
            )
        )
    except Exception:
        return raw


def _site_domain(query: str) -> str:
    match = _SITE_RE.search(str(query or ""))
    if not match:
        return ""
    raw = match.group(1).strip().strip(".,;/")
    parsed = urlsplit(f"//{raw}")
    host = (parsed.hostname or raw.split("/", 1)[0]).strip().lower()
    return host.removeprefix("www.")


def _strip_site_operator(query: str) -> str:
    return " ".join(_SITE_RE.sub(" ", str(query or "")).split()).strip()


def _url_on_domain(url: str, domain: str) -> bool:
    if not domain:
        return True
    host = (urlsplit(str(url or "")).hostname or "").lower().removeprefix("www.")
    return bool(host) and (host == domain or host.endswith(f".{domain}"))


def _classify_intents(query: str) -> set[str]:
    intents: set[str] = set()
    if _site_domain(query):
        intents.add("site")
    if _NEWS_INTENT_RE.search(query):
        intents.add("news")
    if _TECH_CODE_INTENT_RE.search(query):
        intents.add("tech")
    if _PAPER_INTENT_RE.search(query):
        intents.add("paper")
    if not intents:
        intents.add("general")
    return intents


def _make_item(
    *,
    title: str,
    url: str,
    snippet: str,
    source: str,
    published: str = "",
    publisher_url: str = "",
    authority_bonus: float = 0.0,
) -> Optional[Dict[str, Any]]:
    clean_title = " ".join(str(title or "").split()).strip()
    clean_url = str(url or "").strip()
    if not clean_title or not clean_url.startswith(("http://", "https://")):
        return None
    clean_snippet = " ".join(str(snippet or "").split()).strip() or "No description available"
    item: Dict[str, Any] = {
        "title": clean_title[:240],
        "url": clean_url,
        "snippet": clean_snippet[:400],
        "description": clean_snippet[:400],
        "source_provider": source,
    }
    if published:
        item["published"] = str(published).strip()[:64]
    if publisher_url:
        item["publisher_url"] = str(publisher_url).strip()
    if authority_bonus:
        item["_authority_bonus"] = float(authority_bonus)
    return item


# ── Operator-configured Search APIs ──────────────────────────────────────────

def _search_searxng(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    base = (os.getenv("SEARXNG_BASE_URL") or os.getenv("AUTOYOU_SEARXNG_URL") or "").strip().rstrip("/")
    if not base:
        return "searxng", [], False
    try:
        resp = session.get(
            f"{base}/search",
            params={"q": query, "format": "json", "language": "en"},
            headers=_ASSISTANT_HEADERS,
            timeout=_PROVIDER_TIMEOUT,
        )
        resp.raise_for_status()
        if _looks_like_bot_challenge(getattr(resp, "text", "")):
            return "searxng", [], True
        data = resp.json() if callable(getattr(resp, "json", None)) else {}
        out: List[Dict[str, Any]] = []
        for entry in (data.get("results") or [])[: max_results * 2]:
            item = _make_item(
                title=entry.get("title", ""),
                url=entry.get("url", ""),
                snippet=entry.get("content", ""),
                source="searxng",
                published=entry.get("publishedDate", ""),
                authority_bonus=2.0,
            )
            if item:
                out.append(item)
        return "searxng", out, False
    except Exception as exc:
        logger.debug("SearXNG search failed: %s", exc)
        return "searxng", [], False


def _search_brave_api(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    api_key = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()
    if not api_key:
        return "brave", [], False
    try:
        resp = session.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max(1, min(max_results, 20))},
            headers={**_ASSISTANT_HEADERS, "X-Subscription-Token": api_key, "Accept": "application/json"},
            timeout=_PROVIDER_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json() if callable(getattr(resp, "json", None)) else {}
        out: List[Dict[str, Any]] = []
        for entry in ((data.get("web") or {}).get("results") or [])[:max_results]:
            item = _make_item(
                title=entry.get("title", ""),
                url=entry.get("url", ""),
                snippet=entry.get("description", ""),
                source="brave",
                published=entry.get("age", ""),
                authority_bonus=2.5,
            )
            if item:
                out.append(item)
        return "brave", out, False
    except Exception as exc:
        logger.debug("Brave Search API failed: %s", exc)
        return "brave", [], False


def _search_tavily_api(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    api_key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not api_key:
        return "tavily", [], False
    try:
        resp = session.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max(1, min(max_results, 10)),
                "search_depth": "basic",
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=_PROVIDER_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json() if callable(getattr(resp, "json", None)) else {}
        out: List[Dict[str, Any]] = []
        for entry in (data.get("results") or [])[:max_results]:
            item = _make_item(
                title=entry.get("title", ""),
                url=entry.get("url", ""),
                snippet=entry.get("content", ""),
                source="tavily",
                published=entry.get("published_date", ""),
                authority_bonus=2.5,
            )
            if item:
                out.append(item)
        return "tavily", out, False
    except Exception as exc:
        logger.debug("Tavily API failed: %s", exc)
        return "tavily", [], False


def _search_serper_api(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    api_key = (os.getenv("SERPER_API_KEY") or "").strip()
    if not api_key:
        return "serper", [], False
    try:
        resp = session.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": max(1, min(max_results, 10))},
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=_PROVIDER_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json() if callable(getattr(resp, "json", None)) else {}
        out: List[Dict[str, Any]] = []
        for entry in (data.get("organic") or [])[:max_results]:
            item = _make_item(
                title=entry.get("title", ""),
                url=entry.get("link", ""),
                snippet=entry.get("snippet", ""),
                source="serper",
                published=entry.get("date", ""),
                authority_bonus=2.5,
            )
            if item:
                out.append(item)
        return "serper", out, False
    except Exception as exc:
        logger.debug("Serper API failed: %s", exc)
        return "serper", [], False


def _search_exa_api(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    api_key = (os.getenv("EXA_API_KEY") or "").strip()
    if not api_key:
        return "exa", [], False
    try:
        resp = session.post(
            "https://api.exa.ai/search",
            json={"query": query, "numResults": max(1, min(max_results, 10)), "contents": {"highlights": True}},
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=_PROVIDER_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json() if callable(getattr(resp, "json", None)) else {}
        out: List[Dict[str, Any]] = []
        for entry in (data.get("results") or [])[:max_results]:
            highlights = entry.get("highlights") or []
            snippet = " ".join(str(h) for h in highlights[:2]) if isinstance(highlights, list) else str(entry.get("text") or "")
            item = _make_item(
                title=entry.get("title", ""),
                url=entry.get("url", ""),
                snippet=snippet,
                source="exa",
                published=entry.get("publishedDate", ""),
                authority_bonus=2.5,
            )
            if item:
                out.append(item)
        return "exa", out, False
    except Exception as exc:
        logger.debug("Exa API failed: %s", exc)
        return "exa", [], False


# ── Zero-config Sanctioned Public APIs & Feeds ───────────────────────────────

def _search_google_news_rss(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    try:
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
        resp = session.get(url, headers=_ASSISTANT_HEADERS, timeout=_PROVIDER_TIMEOUT)
        resp.raise_for_status()
        body = getattr(resp, "text", "") or ""
        if _looks_like_bot_challenge(body):
            return "google_news_rss", [], True
        root = ET.fromstring(body)
        domain_filter = _site_domain(query)
        out: List[Dict[str, Any]] = []
        for entry in root.findall("./channel/item")[: max(max_results * 2, 16)]:
            title = (entry.findtext("title") or "").strip()
            link = (entry.findtext("link") or "").strip()
            pub = (entry.findtext("pubDate") or "").strip()
            source_el = entry.find("source")
            pub_url = str(source_el.attrib.get("url") or "").strip() if source_el is not None else ""
            pub_name = (source_el.text or "").strip() if source_el is not None else ""
            if domain_filter:
                if not (_url_on_domain(pub_url, domain_filter) or _url_on_domain(link, domain_filter)):
                    continue
            desc_raw = (entry.findtext("description") or "").strip()
            desc = BeautifulSoup(desc_raw, "html.parser").get_text(" ", strip=True) if desc_raw else ""
            if pub_name and pub_name.lower() not in desc.lower():
                desc = f"{pub_name}: {desc}" if desc else pub_name
            item = _make_item(
                title=title,
                url=link,
                snippet=desc,
                source="google_news_rss",
                published=pub,
                publisher_url=pub_url,
                authority_bonus=1.2,
            )
            if item:
                out.append(item)
                if len(out) >= max_results:
                    break
        return "google_news_rss", out, False
    except Exception as exc:
        logger.debug("Google News RSS failed: %s", exc)
        return "google_news_rss", [], False


def _search_bing_news_rss(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    try:
        url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss"
        resp = session.get(url, headers=_ASSISTANT_HEADERS, timeout=_PROVIDER_TIMEOUT)
        resp.raise_for_status()
        body = getattr(resp, "text", "") or ""
        if _looks_like_bot_challenge(body):
            return "bing_news_rss", [], True
        root = ET.fromstring(body)
        domain_filter = _site_domain(query)
        out: List[Dict[str, Any]] = []
        for entry in root.findall("./channel/item")[: max(max_results * 2, 16)]:
            title = (entry.findtext("title") or "").strip()
            link = (entry.findtext("link") or "").strip()
            # Bing News RSS wraps target links in apiclick.aspx?...&url=https%3a%2f%2f...
            if "bing.com/news/apiclick" in link:
                qs = dict(parse_qsl(urlsplit(link).query))
                if qs.get("url", "").startswith(("http://", "https://")):
                    link = qs["url"]
            if domain_filter and not _url_on_domain(link, domain_filter):
                continue
            desc_raw = (entry.findtext("description") or "").strip()
            desc = BeautifulSoup(desc_raw, "html.parser").get_text(" ", strip=True) if desc_raw else ""
            pub = (entry.findtext("pubDate") or "").strip()
            item = _make_item(
                title=title,
                url=link,
                snippet=desc,
                source="bing_news_rss",
                published=pub,
                authority_bonus=1.5,
            )
            if item:
                out.append(item)
                if len(out) >= max_results:
                    break
        return "bing_news_rss", out, False
    except Exception as exc:
        logger.debug("Bing News RSS failed: %s", exc)
        return "bing_news_rss", [], False


def _search_hn_algolia(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    clean_q = _strip_site_operator(query)
    domain_filter = _site_domain(query)
    if not clean_q:
        return "hn_algolia", [], False
    try:
        resp = session.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": clean_q, "tags": "story", "hitsPerPage": max(5, min(max_results * 2, 15))},
            headers=_ASSISTANT_HEADERS,
            timeout=_PROVIDER_TIMEOUT,
        )
        resp.raise_for_status()
        body = getattr(resp, "text", "") or ""
        if _looks_like_bot_challenge(body):
            return "hn_algolia", [], True
        data = resp.json() if callable(getattr(resp, "json", None)) else {}
        out: List[Dict[str, Any]] = []
        for hit in (data.get("hits") or []):
            story_url = str(hit.get("url") or "").strip()
            obj_id = str(hit.get("objectID") or "").strip()
            if not story_url and obj_id:
                story_url = f"https://news.ycombinator.com/item?id={obj_id}"
            if domain_filter and not _url_on_domain(story_url, domain_filter):
                continue
            title = str(hit.get("title") or "").strip()
            points = hit.get("points") or 0
            comments = hit.get("num_comments") or 0
            created = str(hit.get("created_at") or "")[:10]
            story_text = BeautifulSoup(str(hit.get("story_text") or ""), "html.parser").get_text(" ", strip=True)
            snippet = story_text[:280] if story_text else f"Hacker News discussion ({points} points, {comments} comments, {created})."
            item = _make_item(
                title=title,
                url=story_url,
                snippet=snippet,
                source="hn_algolia",
                published=created,
                authority_bonus=1.4 if story_url and "ycombinator.com" not in story_url else 0.6,
            )
            if item:
                out.append(item)
                if len(out) >= max_results:
                    break
        return "hn_algolia", out, False
    except Exception as exc:
        logger.debug("HN Algolia search failed: %s", exc)
        return "hn_algolia", [], False


def _search_github_repos(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    clean_q = _strip_site_operator(query)
    domain_filter = _site_domain(query)
    if domain_filter and domain_filter != "github.com":
        return "github", [], False
    if not clean_q:
        return "github", [], False
    try:
        headers = {**_ASSISTANT_HEADERS, "Accept": "application/vnd.github+json"}
        gh_token = (os.getenv("GITHUB_TOKEN") or "").strip()
        if gh_token:
            headers["Authorization"] = f"Bearer {gh_token}"
        resp = session.get(
            "https://api.github.com/search/repositories",
            params={"q": clean_q, "sort": "stars", "order": "desc", "per_page": max(3, min(max_results, 8))},
            headers=headers,
            timeout=_PROVIDER_TIMEOUT,
        )
        resp.raise_for_status()
        body = getattr(resp, "text", "") or ""
        if _looks_like_bot_challenge(body):
            return "github", [], True
        data = resp.json() if callable(getattr(resp, "json", None)) else {}
        out: List[Dict[str, Any]] = []
        for repo in (data.get("items") or [])[:max_results]:
            full_name = str(repo.get("full_name") or "").strip()
            html_url = str(repo.get("html_url") or "").strip()
            desc = str(repo.get("description") or "").strip()
            stars = repo.get("stargazers_count") or 0
            lang = repo.get("language") or ""
            updated = str(repo.get("pushed_at") or "")[:10]
            snippet = f"{desc} [{lang}, {stars} stars, updated {updated}]".strip()
            item = _make_item(
                title=f"{full_name} (GitHub)",
                url=html_url,
                snippet=snippet,
                source="github",
                published=updated,
                authority_bonus=1.5,
            )
            if item:
                out.append(item)
        return "github", out, False
    except Exception as exc:
        logger.debug("GitHub search failed: %s", exc)
        return "github", [], False


def _search_stackexchange(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    clean_q = _strip_site_operator(query)
    domain_filter = _site_domain(query)
    if domain_filter and "stackoverflow.com" not in domain_filter and "stackexchange.com" not in domain_filter:
        return "stackexchange", [], False
    if not clean_q:
        return "stackexchange", [], False
    try:
        resp = session.get(
            "https://api.stackexchange.com/2.3/search/excerpts",
            params={
                "order": "desc",
                "sort": "relevance",
                "q": clean_q,
                "site": "stackoverflow",
                "pagesize": max(3, min(max_results, 8)),
            },
            headers=_ASSISTANT_HEADERS,
            timeout=_PROVIDER_TIMEOUT,
        )
        resp.raise_for_status()
        body = getattr(resp, "text", "") or ""
        if _looks_like_bot_challenge(body):
            return "stackexchange", [], True
        data = resp.json() if callable(getattr(resp, "json", None)) else {}
        out: List[Dict[str, Any]] = []
        for entry in (data.get("items") or [])[:max_results]:
            qid = entry.get("question_id")
            if not qid:
                continue
            title = BeautifulSoup(str(entry.get("title") or ""), "html.parser").get_text(" ", strip=True)
            excerpt = BeautifulSoup(str(entry.get("excerpt") or ""), "html.parser").get_text(" ", strip=True)
            url = f"https://stackoverflow.com/questions/{qid}"
            score = entry.get("score") or 0
            item = _make_item(
                title=title,
                url=url,
                snippet=f"{excerpt} (score: {score})",
                source="stackexchange",
                authority_bonus=1.2,
            )
            if item:
                out.append(item)
        return "stackexchange", out, False
    except Exception as exc:
        logger.debug("StackExchange search failed: %s", exc)
        return "stackexchange", [], False


def _search_arxiv(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    clean_q = _strip_site_operator(query)
    domain_filter = _site_domain(query)
    if domain_filter and domain_filter != "arxiv.org":
        return "arxiv", [], False
    if not clean_q:
        return "arxiv", [], False
    try:
        resp = session.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f"all:{clean_q}", "start": 0, "max_results": max(3, min(max_results, 8))},
            headers=_ASSISTANT_HEADERS,
            timeout=_PROVIDER_TIMEOUT,
        )
        resp.raise_for_status()
        body = getattr(resp, "text", "") or ""
        if _looks_like_bot_challenge(body):
            return "arxiv", [], True
        root = ET.fromstring(body)
        ns = "{http://www.w3.org/2005/Atom}"
        out: List[Dict[str, Any]] = []
        for entry in root.findall(f"{ns}entry")[:max_results]:
            title = " ".join((entry.findtext(f"{ns}title") or "").split()).strip()
            link = (entry.findtext(f"{ns}id") or "").strip()
            summary = " ".join((entry.findtext(f"{ns}summary") or "").split()).strip()
            published = (entry.findtext(f"{ns}published") or "")[:10]
            item = _make_item(
                title=title,
                url=link,
                snippet=summary[:350],
                source="arxiv",
                published=published,
                authority_bonus=1.6,
            )
            if item:
                out.append(item)
        return "arxiv", out, False
    except Exception as exc:
        logger.debug("arXiv search failed: %s", exc)
        return "arxiv", [], False


def _search_ddg_instant_answer(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    clean_q = _strip_site_operator(query)
    domain_filter = _site_domain(query)
    if not clean_q or domain_filter:
        return "ddg_instant", [], False
    try:
        resp = session.get(
            "https://api.duckduckgo.com/",
            params={"q": clean_q, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers=_ASSISTANT_HEADERS,
            timeout=_PROVIDER_TIMEOUT,
        )
        resp.raise_for_status()
        body = getattr(resp, "text", "") or ""
        if _looks_like_bot_challenge(body):
            return "ddg_instant", [], True
        data = resp.json() if callable(getattr(resp, "json", None)) else {}
        out: List[Dict[str, Any]] = []
        abstract_text = str(data.get("AbstractText") or "").strip()
        abstract_url = str(data.get("AbstractURL") or "").strip()
        heading = str(data.get("Heading") or clean_q).strip()
        if abstract_text and abstract_url:
            item = _make_item(
                title=heading,
                url=abstract_url,
                snippet=abstract_text,
                source="ddg_instant",
                authority_bonus=1.0,
            )
            if item:
                out.append(item)
        for topic in (data.get("RelatedTopics") or []):
            if len(out) >= max_results:
                break
            if isinstance(topic, dict) and topic.get("FirstURL") and topic.get("Text"):
                txt = str(topic["Text"]).strip()
                item = _make_item(
                    title=txt.split(" - ", 1)[0][:160],
                    url=str(topic["FirstURL"]),
                    snippet=txt,
                    source="ddg_instant",
                    authority_bonus=0.5,
                )
                if item:
                    out.append(item)
        return "ddg_instant", out, False
    except Exception as exc:
        logger.debug("DDG Instant Answer failed: %s", exc)
        return "ddg_instant", [], False


def _get_public_page(session: Any, url: str):
    from shared.url_safety import safe_follow_redirects
    # Retain the caller's Session/get hook while validating every redirect.
    adapter = SimpleNamespace(request=lambda method, url, **kwargs: session.get(url, **kwargs))
    return safe_follow_redirects(adapter, url, headers=_ASSISTANT_HEADERS, timeout=_PROVIDER_TIMEOUT)


def _search_site_direct_feed(session: Any, query: str, max_results: int) -> Tuple[str, List[Dict[str, Any]], bool]:
    """For `site:<domain>` queries, inspect the site's advertised RSS/Atom feed or homepage headlines."""
    domain = _site_domain(query)
    if not domain:
        return "site_feed", [], False
    try:
        from shared.url_safety import is_safe_http_url
        if not is_safe_http_url(f"https://{domain}/"):
            return "site_feed", [], False
    except Exception:
        return "site_feed", [], False

    try:
        resp = _get_public_page(session, f"https://{domain}/")
        resp.raise_for_status()
        body = getattr(resp, "text", "") or ""
        if _looks_like_bot_challenge(body):
            return "site_feed", [], True
        try:
            from .research_rag import extract_structured_feed, extract_structured_page
        except ImportError:
            from research_rag import extract_structured_feed, extract_structured_page

        parsed = extract_structured_page(body, f"https://{domain}/")
        out: List[Dict[str, Any]] = []

        # If the homepage advertises an RSS/Atom feed, read the first on-domain feed for rich dated items.
        for feed in (parsed.get("feeds") or [])[:2]:
            feed_url = str(feed.get("url") or "")
            if not feed_url or not _url_on_domain(feed_url, domain):
                continue
            try:
                fresp = _get_public_page(session, feed_url)
                fresp.raise_for_status()
                fdata = extract_structured_feed(getattr(fresp, "text", "") or "", feed_url)
                for hl in (fdata.get("headlines") or [])[:max_results]:
                    item = _make_item(
                        title=hl.get("title", ""),
                        url=hl.get("url", ""),
                        snippet=f"From {domain} RSS feed",
                        source="site_feed",
                        published=hl.get("published", ""),
                        authority_bonus=2.0,
                    )
                    if item and _url_on_domain(item["url"], domain):
                        out.append(item)
                if out:
                    return "site_feed", out[:max_results], False
            except Exception:
                continue

        # Fallback: direct headlines on the homepage itself.
        for hl in (parsed.get("headlines") or [])[:max_results]:
            item = _make_item(
                title=hl.get("title", ""),
                url=hl.get("url", ""),
                snippet=f"Headline on {domain}",
                source="site_feed",
                authority_bonus=1.8,
            )
            if item and _url_on_domain(item["url"], domain):
                out.append(item)
        return "site_feed", out[:max_results], False
    except Exception as exc:
        logger.debug("Direct site feed discovery failed for %s: %s", domain, exc)
        return "site_feed", [], False


def _rank_and_deduplicate(
    query: str,
    raw_items: List[Dict[str, Any]],
    *,
    max_results: int,
    vet_fn: Callable[[str, List[Dict[str, Any]]], Tuple[List[Dict[str, Any]], int, int]],
) -> List[Dict[str, Any]]:
    vetted, _, _ = vet_fn(query, raw_items)
    if not vetted:
        return []

    query_terms = [
        tok.lower()
        for tok in re.findall(r"[A-Za-z0-9]{2,}", _strip_site_operator(query))
    ]
    deduped: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    for item in vetted:
        canon = canonicalize_url(item.get("url", ""))
        if not canon:
            continue
        title_low = str(item.get("title") or "").lower()
        snip_low = str(item.get("snippet") or "").lower()
        url_low = str(item.get("url") or "").lower()

        score = float(item.get("_authority_bonus") or 0.0)
        for term in query_terms:
            if term in title_low:
                score += 2.2
            if term in snip_low:
                score += 0.9
            if term in url_low:
                score += 0.5
        if item.get("published"):
            score += 0.6
        if "news.google.com/rss/articles" in url_low:
            # Prefer direct publisher article URLs over opaque Google News redirect wrappers
            score -= 1.5

        cleaned_item = {k: v for k, v in item.items() if not k.startswith("_")}
        existing = deduped.get(canon)
        if existing is None or score > existing[0]:
            deduped[canon] = (score, cleaned_item)

    ranked = sorted(deduped.values(), key=lambda pair: pair[0], reverse=True)

    # Apply light domain diversity so one domain doesn't take every slot
    final: List[Dict[str, Any]] = []
    overflow: List[Dict[str, Any]] = []
    domain_counts: Dict[str, int] = {}
    site_restricted = bool(_site_domain(query))

    for _, item in ranked:
        host = (urlsplit(item["url"]).hostname or "").lower().removeprefix("www.")
        if not site_restricted and host and domain_counts.get(host, 0) >= 2:
            overflow.append(item)
            continue
        domain_counts[host] = domain_counts.get(host, 0) + 1
        final.append(item)
        if len(final) >= max_results:
            break

    for item in overflow:
        if len(final) >= max_results:
            break
        final.append(item)

    return final


def search_sanctioned_providers(
    session: Any,
    query: str,
    max_results: int,
    *,
    vet_fn: Callable[[str, List[Dict[str, Any]]], Tuple[List[Dict[str, Any]], int, int]],
) -> Dict[str, Any]:
    """Run configured + zero-config sanctioned public API/feed search in parallel.

    Returns a standard InternetTool search dict (``status == 'success'`` when at
    least one vetted relevant result is found, else ``status == 'empty'`` so the
    caller can fall through to legacy DDG Lite / Bing Web RSS / Wikipedia).
    """
    bounded_max = max(1, min(int(max_results or 10), 20))
    domain_filter = _site_domain(query)

    # Tier 1: Operator-configured official search APIs (highest precision, single hop).
    configured_fns = [
        _search_searxng,
        _search_brave_api,
        _search_tavily_api,
        _search_exa_api,
        _search_serper_api,
    ]
    for fn in configured_fns:
        provider_name, items, _ = fn(session, query, bounded_max)
        if items:
            if domain_filter:
                items = [it for it in items if _url_on_domain(it.get("url", ""), domain_filter)]
            ranked = _rank_and_deduplicate(query, items, max_results=bounded_max, vet_fn=vet_fn)
            if ranked:
                return {
                    "status": "success",
                    "provider": provider_name,
                    "providers_used": [provider_name],
                    "results": ranked,
                    "results_count": len(ranked),
                    "query": query,
                }

    # Tier 2: Zero-config sanctioned public APIs & RSS/Atom feeds (parallel).
    intents = _classify_intents(query)
    selected_fns: List[Callable[[Any, str, int], Tuple[str, List[Dict[str, Any]], bool]]] = []

    if "site" in intents:
        selected_fns.extend([_search_site_direct_feed, _search_google_news_rss, _search_hn_algolia])
    else:
        selected_fns.append(_search_google_news_rss)
        if "tech" in intents or "general" in intents:
            selected_fns.append(_search_hn_algolia)
        if "tech" in intents:
            selected_fns.extend([_search_github_repos, _search_stackexchange])
        if "paper" in intents:
            selected_fns.append(_search_arxiv)
        if "general" in intents or "tech" in intents:
            selected_fns.append(_search_ddg_instant_answer)

    all_items: List[Dict[str, Any]] = []
    providers_used: List[str] = []
    challenge_flags: List[bool] = []

    with ThreadPoolExecutor(max_workers=min(6, max(1, len(selected_fns)))) as pool:
        futures = [pool.submit(fn, session, query, bounded_max) for fn in selected_fns]
        for fut in as_completed(futures):
            try:
                pname, pitems, challenged = fut.result()
                challenge_flags.append(challenged)
                if pitems:
                    providers_used.append(pname)
                    all_items.extend(pitems)
            except Exception as exc:
                logger.debug("Sanctioned search worker failed: %s", exc)

    # Supplement with Bing News RSS only when real non-Bing endpoints did NOT all
    # return mocked DDG challenge pages (keeps offline single-host unit tests clean!).
    all_challenged = bool(challenge_flags) and all(challenge_flags)
    if not all_challenged and ("news" in intents or "site" in intents or len(all_items) < 3):
        pname, pitems, _ = _search_bing_news_rss(session, query, bounded_max)
        if pitems:
            providers_used.append(pname)
            all_items.extend(pitems)

    ranked = _rank_and_deduplicate(query, all_items, max_results=bounded_max, vet_fn=vet_fn)
    if not ranked:
        return {
            "status": "empty",
            "provider": "sanctioned_multi",
            "providers_used": providers_used,
            "results": [],
            "results_count": 0,
            "query": query,
        }

    provider_label = providers_used[0] if len(set(providers_used)) == 1 else "sanctioned_multi"
    return {
        "status": "success",
        "provider": provider_label,
        "providers_used": sorted(set(providers_used)),
        "results": ranked,
        "results_count": len(ranked),
        "query": query,
    }
