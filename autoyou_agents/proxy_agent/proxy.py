# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-0d9336fe758b372c591cde00

"""Bounded, SSRF-safe server-side HTTP relay used by Proxy Agent."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import json
import re
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlparse, urlunsplit

import requests

from shared.url_safety import UnsafeURLError, assert_safe_http_url

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-0d9336fe758b372c591cde00"


MAX_URL_LENGTH = 2048
MAX_REQUEST_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 5
ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_ATTR_RE = re.compile(r"(?P<prefix>\b(?:href|src|action|poster)\s*=\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)", re.I)
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)


@dataclass(frozen=True)
class RelayResponse:
    status_code: int
    content_type: str
    body: bytes
    final_url: str


def normalize_target(raw: str) -> str:
    value = str(raw or "").strip()
    if not value or len(value) > MAX_URL_LENGTH:
        raise ValueError("A non-empty URL up to 2048 characters is required.")
    if value.startswith("//"):
        value = f"https:{value}"
    elif not re.match(r"^https?://", value, re.I):
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Only public http(s) URLs without credentials are supported.")
    if parsed.fragment:
        value = value.split("#", 1)[0]
    try:
        assert_safe_http_url(value)
    except UnsafeURLError as exc:
        raise ValueError(str(exc)) from exc
    return value


def normalize_websocket_target(raw: str) -> str:
    """Return a public ``ws(s)`` URL under the same SSRF boundary as HTTP."""
    value = str(raw or "").strip()
    if not value or len(value) > MAX_URL_LENGTH:
        raise ValueError("A non-empty WebSocket URL up to 2048 characters is required.")
    if not re.match(r"^wss?://", value, re.I):
        if re.match(r"^https?://", value, re.I):
            raise ValueError("WebSocket targets must use ws:// or wss://.")
        value = f"wss://{value}"
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    # from __debug_provenance_s__ import btc
    if scheme not in {"ws", "wss"} or parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("Only public ws(s) URLs without credentials are supported.")
    if parsed.fragment:
        raise ValueError("WebSocket URLs must not contain a fragment.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("WebSocket URL has an invalid port.") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("WebSocket URL has an invalid port.")
    normalized = urlunsplit((scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    validation_scheme = "https" if scheme == "wss" else "http"
    validation_url = urlunsplit((validation_scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    try:
        assert_safe_http_url(validation_url)
    except UnsafeURLError as exc:
        raise ValueError(str(exc)) from exc
    return normalized


def _request_headers(source: dict[str, str] | None) -> dict[str, str]:
    raw = source or {}
    headers = {
        "User-Agent": "AutoYou Proxy Agent/1.0 (self-hosted)",
        "Accept": str(raw.get("Accept") or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")[:512],
        "Accept-Language": str(raw.get("Accept-Language") or "en-US,en;q=0.8")[:128],
        "Accept-Encoding": "identity",
    }
    content_type = str(raw.get("Content-Type") or "").strip()
    if content_type.lower().startswith(("application/x-www-form-urlencoded", "multipart/form-data", "application/json", "text/plain")):
        headers["Content-Type"] = content_type[:128]
    return headers


def fetch_target(
    target: str,
    *,
    method: str = "GET",
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> RelayResponse:
    normalized_method = str(method or "GET").upper()
    if normalized_method not in ALLOWED_METHODS:
        raise ValueError("That HTTP method is not supported by the relay.")
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("Request body is too large.")
    current = normalize_target(target)
    session = requests.Session()
    session.trust_env = False
    try:
        for _ in range(MAX_REDIRECTS + 1):
            assert_safe_http_url(current)
            response = session.request(
                normalized_method,
                current,
                headers=_request_headers(headers),
                data=body if normalized_method not in {"GET", "HEAD"} else None,
                allow_redirects=False,
                stream=True,
                timeout=(8, 25),
            )
            if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("Location"):
                next_url = urljoin(current, response.headers["Location"])
                response.close()
                current = normalize_target(next_url)
                continue
            content_length = int(response.headers.get("Content-Length") or 0)
            if content_length > MAX_RESPONSE_BYTES:
                response.close()
                raise ValueError("Upstream response is too large.")
            content = b"" if normalized_method == "HEAD" else response.raw.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES:
                response.close()
                raise ValueError("Upstream response is too large.")
            content_type = str(response.headers.get("Content-Type") or "application/octet-stream").split(";", 1)[0].strip().lower()
            result = RelayResponse(response.status_code, content_type, content, current)
            response.close()
            return result
    finally:
        session.close()
    raise ValueError("Too many redirects.")


def _proxy_url(url: str, endpoint: str) -> str:
    return f"{endpoint}?url={quote(url, safe='')}"


def rewrite_html(body: bytes, *, base_url: str, endpoint: str) -> bytes:
    text = body.decode("utf-8", errors="replace")

    def replace_attr(match: re.Match[str]) -> str:
        value = match.group("value").strip()
        if not value or value.startswith(("#", "data:", "javascript:", "mailto:", "tel:")):
            return match.group(0)
        absolute = urljoin(base_url, value)
        if urlparse(absolute).scheme.lower() not in {"http", "https"}:
            return match.group(0)
        return f"{match.group('prefix')}{match.group('quote')}{_proxy_url(absolute, endpoint)}{match.group('quote')}"

    text = _ATTR_RE.sub(replace_attr, text)

    def replace_css(match: re.Match[str]) -> str:
        value = match.group(2).strip()
        if not value or value.startswith(("data:", "#")):
            return match.group(0)
        absolute = urljoin(base_url, value)
        return f"url({match.group(1)}{_proxy_url(absolute, endpoint)}{match.group(1)})"

    text = _CSS_URL_RE.sub(replace_css, text)
    base_json = json.dumps(base_url)
    endpoint_json = json.dumps(endpoint)
    shim = f"""<script>
(() => {{ const base={base_json}, endpoint={endpoint_json};
const relay=(u)=>{{try{{const x=new URL(String(u),base);return (x.protocol==='http:'||x.protocol==='https:')?endpoint+'?url='+encodeURIComponent(x.href):u}}catch{{return u}}}};
const f=window.fetch; if(f) window.fetch=(u,o)=>f.call(window,relay(u),o);
const open=XMLHttpRequest.prototype.open; XMLHttpRequest.prototype.open=function(m,u){{return open.call(this,m,relay(u),...Array.prototype.slice.call(arguments,2))}};
const NativeWebSocket=window.WebSocket;
const socketRelay=(u)=>{{try{{const x=new URL(String(u),document.baseURI||location.href);if(x.protocol==='http:')x.protocol='ws:';if(x.protocol==='https:')x.protocol='wss:';if(x.protocol!=='ws:'&&x.protocol!=='wss:')return u;const e=new URL(endpoint,document.baseURI||location.href);e.protocol=location.protocol==='https:'?'wss:':'ws:';e.search='?url='+encodeURIComponent(x.href);return e.href}}catch{{return u}}}};
if(NativeWebSocket){{const WrappedWebSocket=function(u,p){{const target=socketRelay(u);return p===undefined?new NativeWebSocket(target):new NativeWebSocket(target,p)}};WrappedWebSocket.prototype=NativeWebSocket.prototype;for(const key of ['CONNECTING','OPEN','CLOSING','CLOSED'])WrappedWebSocket[key]=NativeWebSocket[key];window.WebSocket=WrappedWebSocket}}
document.addEventListener('click',e=>{{const a=e.target.closest&&e.target.closest('a');if(a&&a.href){{const next=relay(a.href);if(next!==a.href){{e.preventDefault();window.location.assign(next)}}}}}},true);
}})();</script>"""
    lower = text.lower()
    position = lower.find("<head>")
    if position >= 0:
        insert_at = position + len("<head>")
        text = text[:insert_at] + shim + text[insert_at:]
    else:
        text = shim + text
    return text.encode("utf-8")


__all__ = [
    "RelayResponse",
    "normalize_target",
    "normalize_websocket_target",
    "fetch_target",
    "rewrite_html",
    "ALLOWED_METHODS",
]
