#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
For AutoYou Page Service

A simple HTTP server that serves the "For AutoYou Page" website on 127.0.0.1:8067.
This service handles HTTP requests forwarded from remote clients via WebRTC datachannel.
"""

import asyncio
import difflib
import html
import inspect
import ipaddress
import logging
import json
import mimetypes
import shutil
import subprocess
import sys
from typing import Callable, Dict, Any, Optional
from datetime import datetime
import os
import re
import unicodedata
from urllib.parse import urlparse, parse_qs
import urllib.request
from contextlib import suppress
import requests
from autoyou_agents.shared_tools.agent_directory import build_agent_directory_payload
from autoyou_agents.shared_tools.frontend_registry import load_frontend_registry
from autoyou_agents.shared_tools.agent_identity import (
  format_agent_display_name,
)
from shared.remote_access_policy import (
    DEVICE_OWNERSHIP_HEADER,
    DEVICE_SHARED,
    REMOTE_BROWSER_HEADER,
    REMOTE_BROWSER_IDENTITY_HEADERS,
    REMOTE_BROWSER_VIA_HOME_NETWORK,
    normalize_remote_access_role,
    remote_access_denial_message,
    remote_http_request_allowed,
)
from shared.request_logging import install_route_aware_request_logging
from shared.ui_theme import get_ui_theme, normalize_ui_theme, set_ui_theme
from shared.url_safety import (
    UnsafeURLError,
    assert_safe_http_url,
    is_safe_http_url,
    safe_follow_redirects,
)

try:
  from fastapi import FastAPI, Request, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
  from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
  from fastapi.staticfiles import StaticFiles
  import uvicorn
except ImportError:
  FastAPI = None
  uvicorn = None
  print("Warning: FastAPI/uvicorn not available for autoyou_page_service")

try:
  import websockets
except ImportError:
  websockets = None

# Configure logging
LOGGER = logging.getLogger(__name__)

AGENT_WEBSITE_PUBLIC_STATUS = 404
AGENT_WEBSITE_PUBLIC_TITLE = "Website unavailable"
AGENT_WEBSITE_PUBLIC_DETAIL = "The requested agent website is unavailable."
AGENT_WEBSITES_QUERY_MEDIA_TYPE = "application/json"
AGENT_WEBSITES_ACCEPT_QUERY = '"application/json"'
AGENT_WEBSITES_QUERY_MAX_BYTES = 4096
AGENT_WEBSITES_QUERY_MAX_LENGTH = 200
AGENT_WEBSITES_QUERY_DEFAULT_LIMIT = 24
AGENT_WEBSITES_QUERY_MAX_LIMIT = 50

#: Set where the network boundary is enforced outside this process (for example
#: a container port published only on the host's loopback), so a peer that is
#: not this computer is still the operator. Home-network browsers are otherwise
#: held to HTTPS and the remote client role.
TRUST_NETWORK_PEERS_ENV = "AUTOYOU_PAGE_TRUST_NETWORK_PEERS"
_THIS_COMPUTER_PEERS = {"127.0.0.1", "::1", "localhost", "testclient"}
_AGENT_PATH = re.compile(r"^/agent/([A-Za-z0-9_-]{1,100})(?:/|$)")
_REDIRECT_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")

def _no_store_html_response(content: str, status_code: int = 200) -> HTMLResponse:
  response = HTMLResponse(content=content, status_code=status_code)
  response.headers["Cache-Control"] = "no-store"
  response.headers["Pragma"] = "no-cache"
  response.headers["Expires"] = "0"
  return response

class AutoYouPageService:
    """Simple HTTP server for the 'For AutoYou Page' website."""

    def __init__(
        self,
        port: int = 8067,
        host: str = "127.0.0.1",
        timeline_days: int = 7,
        https_port: Optional[int] = None,
        ssl_certfile: Optional[str] = None,
        ssl_keyfile: Optional[str] = None,
        remote_access_role_provider: Optional[Callable[[], Any]] = None,
    ):
        self.port = port
        self.host = host
        self.timeline_days = max(0, int(timeline_days))
        # The remote client role for browsers that are not on this computer.
        # Read per request so an admin change applies without a restart.
        self.remote_access_role_provider = remote_access_role_provider
        self.trust_network_peers = str(os.getenv(TRUST_NETWORK_PEERS_ENV, "")).strip().lower() in {
            "1", "true", "yes", "on",
        }
        self.app = None
        self.server = None
        self.server_task = None
        # Optional additive HTTPS mirror. When all three are set, start() serves
        # the same app over TLS on https_port in parallel with plain HTTP so
        # path_proxy agent-site browsing works over https:// without breaking the
        # existing HTTP surface.
        self.https_port = https_port
        self.ssl_certfile = ssl_certfile
        self.ssl_keyfile = ssl_keyfile
        self.https_server = None
        self.feed_items: list[Dict[str, Any]] = []
        self._next_id: int = 1
        # The page feed moved to page_agent. This process is now only the
        # website proxy and must not race that backend for page_feed.db.
        self.db = None
        try:
            from shared.platform_runtime import get_config_dir
            config_dir = str(get_config_dir("AutoYou", anchor=__file__))
        except Exception as e:
            LOGGER.error(f"Failed to resolve Page proxy data directory: {e}")
            config_dir = os.getcwd()

        # Ensure uploads directory exists for blob storage
        try:
            self.uploads_dir = os.path.join(config_dir, "uploads")
            os.makedirs(self.uploads_dir, exist_ok=True)
        except Exception as e:
            self.uploads_dir = os.getcwd()
            LOGGER.error(f"Failed to prepare uploads directory: {e}")
        self.config_dir = config_dir

        if FastAPI is not None:
            self._setup_app()

    @staticmethod
    def _peer_is_this_computer(peer_host: Any) -> bool:
        normalized = str(peer_host or "").strip().lower()
        if normalized.startswith("::ffff:"):
            normalized = normalized[len("::ffff:"):]
        if normalized in _THIS_COMPUTER_PEERS:
            return True
        try:
            return ipaddress.ip_address(normalized.split("%", 1)[0]).is_loopback
        except ValueError:
            return False

    def _is_home_network_browser(self, peer_host: Any) -> bool:
        """A browser on another device reaching this service directly over the network.

        Paired devices arrive through the WebRTC proxy from 127.0.0.1 and were
        already held to the remote client role there.
        """
        return not self.trust_network_peers and not self._peer_is_this_computer(peer_host)

    def _remote_access_role(self) -> str:
        provider = self.remote_access_role_provider
        if provider is None:
            provider = getattr(sys.modules.get("server"), "_get_remote_browser_access_role", None)
        try:
            value = provider() if callable(provider) else None
        except Exception as exc:
            LOGGER.warning("Could not read the remote client role; using viewer: %s", exc)
            value = None
        return normalize_remote_access_role(value)

    def _https_mirror_configured(self) -> bool:
        return bool(self.https_port and self.ssl_certfile and self.ssl_keyfile)

    def _https_url_for(self, url: Any) -> Optional[str]:
        """The same address on this service's HTTPS mirror, or None if unsafe to build."""
        hostname = str(getattr(url, "hostname", "") or "").strip()
        try:
            parsed = ipaddress.ip_address(hostname.strip("[]"))
            authority = f"[{parsed}]" if parsed.version == 6 else str(parsed)
        except ValueError:
            if not _REDIRECT_HOST.fullmatch(hostname):
                return None
            authority = hostname
        path = str(getattr(url, "path", "") or "/")
        query = str(getattr(url, "query", "") or "")
        return f"https://{authority}:{int(self.https_port)}{path}" + (f"?{query}" if query else "")

    @staticmethod
    def _home_network_identity_headers(raw_headers: Any, role: str, path: str) -> list:
        """ASGI headers with any browser-supplied identity dropped and AutoYou's own stamped."""
        kept = [
            (key, value)
            for key, value in raw_headers
            if key.decode("latin-1").lower() not in REMOTE_BROWSER_IDENTITY_HEADERS
        ]
        kept.append((REMOTE_BROWSER_HEADER.lower().encode("latin-1"), REMOTE_BROWSER_VIA_HOME_NETWORK.encode("latin-1")))
        kept.append((b"x-autoyou-remote-access-role", role.encode("latin-1")))
        # A browser on another device has no account pairing: always shared.
        kept.append((DEVICE_OWNERSHIP_HEADER.lower().encode("latin-1"), DEVICE_SHARED.encode("latin-1")))
        agent_match = _AGENT_PATH.match(path or "")
        if agent_match:
            # Agent websites already render the remote-role view for this marker.
            kept.append((b"x-autoyou-agent-frontend", agent_match.group(1).encode("latin-1")))
        return kept

    def _get_ui_theme(self) -> str:
        return get_ui_theme(app_name="AutoYou", anchor=__file__, default="dark")

    def _set_ui_theme(self, theme: Any) -> str:
        return set_ui_theme(normalize_ui_theme(theme), app_name="AutoYou", anchor=__file__)

    @staticmethod
    def _serialize_inline_json(payload: Any) -> str:
        return json.dumps(payload).replace("</", "<\\/")

    def _default_browser_path(self) -> str:
        """Resolve the browser home path from the registry's default_agent.

        The directory sentinel -> the Agent Websites listing; a specific agent ->
        its proxied path; otherwise fall back to the page feed.
        """
        try:
            default_agent = str(load_frontend_registry().get("default_agent") or "").strip()
        except Exception:
            default_agent = ""
        if default_agent in {"agent_websites", "directory", "websites"}:
            return "/websites"
        if default_agent:
            return f"/agent/{default_agent}/"
        return "/agent/page_agent/"

    def _load_agent_frontends(self) -> Dict[str, Any]:
        payload = load_frontend_registry()
        raw_frontends = payload.get("frontends", [])
        normalized_frontends = []
        for item in raw_frontends if isinstance(raw_frontends, list) else []:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            launch_path = str(
                normalized.get("launch_path")
                or normalized.get("proxy_path")
                or ""
            ).strip()
            route_mode = str(normalized.get("route_mode") or "").strip().lower().replace("-", "_")
            direct_port_value = normalized.get("direct_forward_port")
            try:
                direct_port = int(direct_port_value) if direct_port_value not in (None, "") else 0
            except (TypeError, ValueError):
                direct_port = 0
            uses_direct_forward_port = (
                route_mode == "direct_forward"
                or (route_mode != "path_proxy" and (bool(normalized.get("uses_direct_forward_port")) or direct_port > 0))
            )
            normalized["route_mode"] = "direct_forward" if uses_direct_forward_port else "path_proxy"
            if uses_direct_forward_port and not str(normalized.get("local_url") or "").strip() and direct_port > 0:
                entry_path = str(normalized.get("entry_path") or "/").strip() or "/"
                if not entry_path.startswith("/"):
                    entry_path = f"/{entry_path}"
                normalized["local_url"] = f"http://127.0.0.1:{direct_port}{entry_path if entry_path != '/' else '/'}"
            if not uses_direct_forward_port and launch_path:
                if not launch_path.startswith("/"):
                    launch_path = f"/{launch_path}"
                normalized["launch_path"] = launch_path
                normalized["open_url"] = launch_path
                normalized["local_url"] = ""
            normalized_frontends.append(normalized)
        normalized_frontends.sort(
            key=lambda item: (
                format_agent_display_name(item.get("agent_name")).casefold(),
                str(item.get("agent_name") or "").casefold(),
            )
        )
        return {
            "success": True,
            "frontends": normalized_frontends,
            "autoyou_browser_base_url": payload.get("browser_base_url"),
            "default_agent": payload.get("default_agent"),
            "updated_at": payload.get("updated_at"),
        }

    @staticmethod
    def _normalize_agent_search_text(value: Any) -> str:
        text = unicodedata.normalize("NFKD", str(value or "").casefold())
        text = "".join(character for character in text if not unicodedata.combining(character))
        return " ".join(re.sub(r"[_\W]+", " ", text, flags=re.UNICODE).split())

    @classmethod
    def _agent_frontend_search_score(cls, frontend: Dict[str, Any], query: str) -> Optional[float]:
        registered_name = cls._normalize_agent_search_text(frontend.get("agent_name"))
        display_name = cls._normalize_agent_search_text(format_agent_display_name(frontend.get("agent_name")))
        title = cls._normalize_agent_search_text(frontend.get("title"))
        description = cls._normalize_agent_search_text(frontend.get("description"))
        name_text = " ".join(dict.fromkeys(filter(None, (registered_name, display_name, title))))
        name_tokens = set(name_text.split())
        description_tokens = set(description.split())
        query_tokens = query.split()

        score = 0.0
        matched_all_tokens = True
        fuzzy_name_score = 0.0
        for token in query_tokens:
            if token in name_tokens:
                score += 90
            elif any(candidate.startswith(token) for candidate in name_tokens):
                score += 65
            elif token in name_text:
                score += 45
            elif token in description_tokens:
                score += 28
            elif any(candidate.startswith(token) for candidate in description_tokens):
                score += 18
            elif token in description:
                score += 10
            else:
                fuzzy_token_score = max(
                    (
                        difflib.SequenceMatcher(None, token, candidate).ratio()
                        for candidate in name_tokens
                        if len(token) >= 3 and candidate
                    ),
                    default=0.0,
                )
                if fuzzy_token_score >= 0.72:
                    score += fuzzy_token_score * 55
                    fuzzy_name_score = max(fuzzy_name_score, fuzzy_token_score)
                else:
                    matched_all_tokens = False

        if query == registered_name or query == display_name or query == title:
            score += 500
        elif query in name_text:
            score += 240
        elif query in description:
            score += 100

        # ponytail: a linear registry scan beats a maintained index at today's
        # agent counts; add an index only if registries grow into the hundreds.
        if len(query) >= 3:
            fuzzy_name_score = max(fuzzy_name_score, max(
                (
                    difflib.SequenceMatcher(None, query, candidate).ratio()
                    for candidate in (registered_name, display_name, title)
                    if candidate
                ),
                default=0.0,
            ))
            if fuzzy_name_score >= 0.78:
                score += fuzzy_name_score * 120

        if not matched_all_tokens and fuzzy_name_score < 0.78:
            return None
        return score

    def _search_agent_frontends(self, query: str, *, limit: int) -> Dict[str, Any]:
        payload = self._load_agent_frontends()
        normalized_query = self._normalize_agent_search_text(query)
        frontends = payload.get("frontends") if isinstance(payload.get("frontends"), list) else []
        total = len(frontends)
        if normalized_query:
            ranked = []
            for frontend in frontends:
                if not isinstance(frontend, dict):
                    continue
                score = self._agent_frontend_search_score(frontend, normalized_query)
                if score is not None:
                    ranked.append((score, frontend))
            ranked.sort(
                key=lambda entry: (
                    -entry[0],
                    format_agent_display_name(entry[1].get("agent_name")).casefold(),
                    str(entry[1].get("agent_name") or "").casefold(),
                )
            )
            frontends = [frontend for _, frontend in ranked]
        payload["frontends"] = frontends[:limit]
        payload["query"] = str(query or "").strip()
        payload["count"] = len(payload["frontends"])
        payload["total"] = total
        return payload

    def _load_agent_directory(self) -> Dict[str, Any]:
        registry = load_frontend_registry()
        return build_agent_directory_payload(
            frontend_registry=registry,
            installed_only=True,
        )

    @staticmethod
    def _frontend_registry_map() -> Dict[str, Dict[str, Any]]:
        registry = load_frontend_registry()
        results: Dict[str, Dict[str, Any]] = {}
        for item in registry.get("frontends", []):
            if not isinstance(item, dict):
                continue
            agent_name = str(item.get("agent_name") or "").strip()
            if not agent_name:
                continue
            results[agent_name] = item
        return results

    @staticmethod
    def _proxy_request_headers(
        headers: Dict[str, str],
        *,
        forwarded_host: str = "",
        forwarded_proto: str = "",
    ) -> Dict[str, str]:
        blocked = {
            "accept-encoding",
            "connection",
            "content-length",
            "host",
            "transfer-encoding",
            "x-forwarded-host",
            "x-forwarded-proto",
        }
        proxied = {
            key: value
            for key, value in headers.items()
            if key.lower() not in blocked
        }
        if forwarded_host:
            proxied["X-Forwarded-Host"] = forwarded_host
        if forwarded_proto:
            proxied["X-Forwarded-Proto"] = forwarded_proto
        return proxied

    @staticmethod
    def _rewrite_proxy_location(agent_name: str, location: Optional[str]) -> Optional[str]:
        if not location:
            return location

        if location.startswith(("http://127.0.0.1:", "http://localhost:")):
            parsed = urlparse(location)
            path = parsed.path or "/"
            prefix = f"/agent/{agent_name}"
            rewritten = prefix + (path if path != "/" else "/")
            if parsed.query:
                rewritten += f"?{parsed.query}"
            if parsed.fragment:
                rewritten += f"#{parsed.fragment}"
            return rewritten

        if location.startswith("/") and not location.startswith("//"):
            if location.startswith("/agent/"):
                return location
            prefix = f"/agent/{agent_name}"
            return prefix + (location if location != "/" else "/")

        return location

    @staticmethod
    def _inject_agent_proxy_shim(html_bytes: bytes, agent_name: str) -> bytes:
        """Rewrite an HTML response so it works correctly when served through
        the /agent/<agent_name>/ proxy:

        1. Rewrite absolute-path HTML attributes (src="/...", href="/...",
           action="/...") so static assets and links load via the proxy prefix.
        2. Inject a <base> tag so remaining relative URLs resolve correctly.
        3. Inject a JS shim that intercepts fetch() and XMLHttpRequest so
           dynamic API calls are also prefixed at runtime.
        """
        import re as _re
        prefix = f"/agent/{agent_name}"
        prefix_b = prefix.encode("utf-8")
        page_service_paths = (
            b"/websites",
            b"/agent-websites",
            b"/agent-frontends",
            b"/api/websites",
            b"/api/agent-websites",
            b"/api/agent-frontends",
            b"/api/agent-directory",
        )

        def _is_page_service_path(path: bytes) -> bool:
            return any(
                path == candidate or path.startswith(candidate + separator)
                for candidate in page_service_paths
                for separator in (b"/", b"?", b"#")
            )

        # Step 1: rewrite absolute-path attributes - two passes (double/single
        # quotes) so we capture the full path and can skip already-prefixed or
        # protocol-relative ("//") values without backreference headaches.
        def _make_attr_rewriter(quote: bytes) -> "_re.Pattern[bytes]":
            q = _re.escape(quote)
            return _re.compile(
                b"((?:src|href|action)\\s*=\\s*" + q + b")(/[^" + quote + b"]*)" + q,
                _re.IGNORECASE,
            )
        def _rewrite_attr(quote: bytes) -> "Callable":
            def _sub(m: "_re.Match[bytes]") -> bytes:
                path = m.group(2)
                if (
                    path.startswith(b"//")
                    or path.startswith(prefix_b)
                    or path.startswith(b"/agent/")
                    or _is_page_service_path(path)
                ):
                    return m.group(0)
                return m.group(1) + prefix_b + path + quote
            return _sub
        for _q in (b'"', b"'"):
            html_bytes = _make_attr_rewriter(_q).sub(_rewrite_attr(_q), html_bytes)

        # Step 2+3: build and inject the <base> + JS shim right after <head>.
        # The shim patches every mechanism by which the admin UI (or any agent
        # UI) might fire a request to an absolute path that would otherwise
        # bypass the /agent/<name>/ prefix:
        #   • fetch()              - XHR-style API calls
        #   • XMLHttpRequest.open  - legacy AJAX
        #   • location.assign/replace - JS navigation after login redirects
        #   • Element.setAttribute - dynamic src/href mutations from JS
        viewport_injection = ""
        if not _re.search(
            rb'<meta\b[^>]*\bname\s*=\s*["\']viewport["\']',
            html_bytes[:8192],
            _re.IGNORECASE,
        ):
            viewport_injection = '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        shim = (
            viewport_injection
            + f'<base href="{prefix}/">'
            f'<script>(function(){{'
            f'var _A="{prefix}";'
            f'var _R=(function(){{try{{var p=String(location.pathname||"");'
            f'var i=p.indexOf(_A);if(i>=0)return p.slice(0,i);'
            f'var j=p.indexOf("/agent/");if(j>=0)return p.slice(0,j);'
            f'}}catch(_){{}}return "";}})();'
            f'var _B=_R+_A;'
            f'function _g(u){{return /^(?:\\/websites|\\/agent-websites|\\/agent-frontends|\\/api\\/websites|\\/api\\/agent-websites|\\/api\\/agent-frontends|\\/api\\/agent-directory)(?:[\\/?#]|$)/.test(u);}}'
            f'function _r(u){{'
            f'if(typeof u!=="string")return u;'
            f'if(!u.startsWith("/")||u.startsWith("//"))return u;'
            f'if(u.startsWith("/agent/")||_g(u))return _R+u;'
            f'if(u===_B||u.startsWith(_B+"/"))return u;'
            f'return _B+u;}}'
            # fetch()
            f'var _f=window.fetch;'
            f'window.fetch=function(u,o){{return _f.call(this,_r(u),o);}};'
            # XMLHttpRequest
            f'var _x=XMLHttpRequest.prototype.open;'
            f'XMLHttpRequest.prototype.open=function(){{'
            f'var a=[].slice.call(arguments);a[1]=_r(a[1]);return _x.apply(this,a);}};'
            # location.assign / location.replace (e.g. after login redirect)
            f'try{{var _la=location.assign.bind(location);'
            f'location.assign=function(u){{return _la(_r(u));}};}}catch(_){{}}'
            f'try{{var _lrp=location.replace.bind(location);'
            f'location.replace=function(u){{return _lrp(_r(u));}};}}catch(_){{}}'
            # Element.prototype.setAttribute (dynamic src/href/action from JS)
            f'var _sa=Element.prototype.setAttribute;'
            f'Element.prototype.setAttribute=function(n,v){{'
            f'if((n==="src"||n==="href"||n==="action")&&typeof v==="string")v=_r(v);'
            f'return _sa.call(this,n,v);}};'
            f'function _pd(C,n){{try{{if(!C||!C.prototype)return;'
            f'var p=C.prototype,d=null,c=p;while(c&&!d){{d=Object.getOwnPropertyDescriptor(c,n);c=Object.getPrototypeOf(c);}}'
            f'if(!d||!d.configurable||!d.set)return;'
            f'Object.defineProperty(p,n,{{configurable:true,enumerable:d.enumerable,'
            f'get:function(){{return d.get?d.get.call(this):this.getAttribute(n);}},'
            f'set:function(v){{return d.set.call(this,_r(v));}}}});}}catch(_){{}}}}'
            f'_pd(typeof HTMLMediaElement==="undefined"?null:HTMLMediaElement,"src");'
            f'_pd(typeof HTMLSourceElement==="undefined"?null:HTMLSourceElement,"src");'
            f'_pd(typeof HTMLImageElement==="undefined"?null:HTMLImageElement,"src");'
            f'_pd(typeof HTMLScriptElement==="undefined"?null:HTMLScriptElement,"src");'
            f'_pd(typeof HTMLIFrameElement==="undefined"?null:HTMLIFrameElement,"src");'
            f'_pd(typeof HTMLAnchorElement==="undefined"?null:HTMLAnchorElement,"href");'
            f'_pd(typeof HTMLLinkElement==="undefined"?null:HTMLLinkElement,"href");'
            f'_pd(typeof HTMLFormElement==="undefined"?null:HTMLFormElement,"action");'
            f'}})();</script>'
        )
        shim_bytes = shim.encode("utf-8")
        lower = html_bytes[:4096].lower()
        head_open = lower.find(b"<head>")
        if head_open != -1:
            insert_at = head_open + len(b"<head>")
            return html_bytes[:insert_at] + shim_bytes + html_bytes[insert_at:]
        head_close = lower.find(b"</head>")
        if head_close != -1:
            return html_bytes[:head_close] + shim_bytes + html_bytes[head_close:]
        return shim_bytes + html_bytes

    def _proxy_response_headers(self, headers: Dict[str, str], agent_name: str) -> Dict[str, str]:
        blocked = {
            "connection",
            "content-encoding",
            "content-length",
            "date",
            "server",
            "transfer-encoding",
            "x-powered-by",
        }
        rewritten: Dict[str, str] = {}
        for key, value in headers.items():
            lower_key = key.lower()
            if lower_key in blocked:
                continue
            if lower_key == "location":
                rewritten[key] = self._rewrite_proxy_location(agent_name, value) or value
                continue
            rewritten[key] = value
        return rewritten

    def _agent_website_error_response(
        self,
        request: Request,
        *,
        status_code: int,
        title: str,
        detail: str,
    ) -> Response:
        accepts = str(request.headers.get("accept") or "").lower()
        wants_json = "/api/" in str(request.url.path) or "application/json" in accepts
        payload = {"success": False, "error": detail, "title": title}
        if wants_json:
            return JSONResponse(payload, status_code=status_code)
        return _no_store_html_response(
            content=f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>{html.escape(title)}</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      background: #08121d;
      color: #eef6ff;
      font-family: "Aptos", "Segoe UI", sans-serif;
    }}
    main {{
      width: min(680px, 100%);
      padding: 24px;
      border-radius: 24px;
      border: 1px solid rgba(117, 150, 190, 0.24);
      background: linear-gradient(180deg, rgba(12, 24, 46, 0.92), rgba(8, 17, 32, 0.88));
      box-shadow: 0 18px 44px rgba(0, 0, 0, 0.28);
    }}
    h1 {{ margin: 0 0 10px; font-size: clamp(1.6rem, 4vw, 2.4rem); }}
    p {{ margin: 0; color: #a8bbd3; line-height: 1.6; }}
    a {{
      display: inline-flex;
      margin-top: 18px;
      color: #eef6ff;
      text-decoration: none;
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid rgba(117, 150, 190, 0.2);
      background: rgba(9, 18, 32, 0.82);
    }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    <p>{html.escape(detail)}</p>
    <a href="/websites">Back to websites</a>
  </main>
</body>
</html>""",
            status_code=status_code,
        )

    def _agent_website_fail_closed(
        self,
        request: Request,
        *,
        agent_name: str,
        reason: str,
        exc: Optional[Exception] = None,
    ) -> Response:
        if exc is not None:
            LOGGER.warning("Agent website access denied for '%s': %s", agent_name, reason, exc_info=exc)
        else:
            LOGGER.info("Agent website access denied for '%s': %s", agent_name, reason)
        return self._agent_website_error_response(
            request,
            status_code=AGENT_WEBSITE_PUBLIC_STATUS,
            title=AGENT_WEBSITE_PUBLIC_TITLE,
            detail=AGENT_WEBSITE_PUBLIC_DETAIL,
        )

    @staticmethod
    def _proxy_websocket_headers(headers: Dict[str, str]) -> Dict[str, str]:
        blocked = {
            "accept-encoding",
            "connection",
            "content-length",
            "host",
            "origin",
            "sec-websocket-accept",
            "sec-websocket-extensions",
            "sec-websocket-key",
            "sec-websocket-protocol",
            "sec-websocket-version",
            "transfer-encoding",
            "upgrade",
        }
        return {
            key: value
            for key, value in headers.items()
            if key.lower() not in blocked
        }

    async def _proxy_agent_frontend_websocket(
        self,
        websocket: WebSocket,
        *,
        agent_name: str,
        proxy_path: str,
    ) -> None:
        if websockets is None:
            await websocket.close(code=1011, reason="WebSocket proxy unavailable")
            return

        home_network_role = None
        if self._is_home_network_browser(websocket.client.host if websocket.client else ""):
            if websocket.url.scheme != "wss" and self._https_mirror_configured():
                await websocket.close(code=1008, reason="Use HTTPS on the home network")
                return
            home_network_role = self._remote_access_role()
            if not remote_http_request_allowed(home_network_role, "GET", websocket.url.path, websocket=True):
                await websocket.close(
                    code=1008,
                    reason=remote_access_denial_message(home_network_role, "GET", websocket.url.path, websocket=True),
                )
                return

        frontend = self._frontend_registry_map().get(agent_name)
        if not frontend:
            await websocket.close(code=1008, reason=AGENT_WEBSITE_PUBLIC_DETAIL)
            return
        if not bool(frontend.get("websocket_enabled", False)):
            await websocket.close(code=1008, reason="WebSocket not enabled for this website")
            return

        raw_proxy_port = frontend.get("proxy_port")
        try:
            proxy_port = int(raw_proxy_port)
        except (TypeError, ValueError):
            proxy_port = 0
        if proxy_port <= 0 or proxy_port > 65535:
            await websocket.close(code=1008, reason=AGENT_WEBSITE_PUBLIC_DETAIL)
            return

        target_path = f"/{proxy_path}" if proxy_path else "/"
        target_url = f"ws://127.0.0.1:{proxy_port}{target_path}"
        if websocket.url.query:
            target_url = f"{target_url}?{websocket.url.query}"

        requested_protocols = [
            item.strip()
            for item in str(websocket.headers.get("sec-websocket-protocol") or "").split(",")
            if item.strip()
        ]
        connect_kwargs: Dict[str, Any] = {
            "max_size": None,
            "subprotocols": requested_protocols or None,
        }
        extra_headers = self._proxy_websocket_headers(dict(websocket.headers))
        if home_network_role is not None:
            extra_headers = {
                key.decode("latin-1"): value.decode("latin-1")
                for key, value in self._home_network_identity_headers(
                    [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in extra_headers.items()],
                    home_network_role,
                    websocket.url.path,
                )
            }
        if extra_headers:
            header_param = (
                "additional_headers"
                if "additional_headers" in inspect.signature(websockets.connect).parameters
                else "extra_headers"
            )
            connect_kwargs[header_param] = extra_headers

        try:
            async with websockets.connect(target_url, **connect_kwargs) as upstream:
                await websocket.accept(subprotocol=getattr(upstream, "subprotocol", None))

                async def _client_to_upstream() -> None:
                    while True:
                        message = await websocket.receive()
                        msg_type = message.get("type")
                        if msg_type == "websocket.disconnect":
                            await upstream.close()
                            return
                        if "text" in message and message["text"] is not None:
                            await upstream.send(message["text"])
                        elif "bytes" in message and message["bytes"] is not None:
                            await upstream.send(message["bytes"])

                async def _upstream_to_client() -> None:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(str(message))

                client_task = asyncio.create_task(_client_to_upstream())
                upstream_task = asyncio.create_task(_upstream_to_client())
                done, pending = await asyncio.wait(
                    {client_task, upstream_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    with suppress(Exception):
                        task.result()
        except WebSocketDisconnect:
            return
        except Exception as exc:
            LOGGER.warning("Agent website WebSocket proxy failed for %s: %s", agent_name, exc)
            with suppress(Exception):
                await websocket.close(code=1011, reason=AGENT_WEBSITE_PUBLIC_DETAIL)

    async def _proxy_agent_frontend_request(
        self,
        request: Request,
        *,
        agent_name: str,
        proxy_path: str,
    ) -> Response:
        frontend = self._frontend_registry_map().get(agent_name)
        if not frontend:
            return self._agent_website_fail_closed(
                request,
                agent_name=agent_name,
                reason="No installed frontend is registered for the requested agent path.",
            )

        raw_proxy_port = frontend.get("proxy_port")
        try:
            proxy_port = int(raw_proxy_port)
        except (TypeError, ValueError):
            proxy_port = 0
        if proxy_port <= 0 or proxy_port > 65535:
          return self._agent_website_fail_closed(
                request,
                agent_name=agent_name,
                reason=f"Frontend registry entry has no valid proxy port (value={raw_proxy_port!r}).",
            )

        target_path = f"/{proxy_path}" if proxy_path else "/"
        target_url = f"http://127.0.0.1:{proxy_port}{target_path}"
        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"

        body = await request.body()
        is_media_stream = request.method.upper() != "HEAD" and "/api/stream/" in target_path.lower()
        request_kwargs = {
            "headers": self._proxy_request_headers(
                dict(request.headers),
                forwarded_host=str(request.headers.get("host") or ""),
                forwarded_proto=str(request.url.scheme or ""),
            ),
            "data": body,
            "allow_redirects": False,
            "timeout": 30,
        }
        if is_media_stream:
            request_kwargs["stream"] = True
            request_kwargs["headers"]["Accept-Encoding"] = "identity"
        try:
            upstream = await asyncio.to_thread(
                requests.request,
                request.method,
                target_url,
                **request_kwargs,
            )
        except requests.RequestException as exc:
              return self._agent_website_fail_closed(
                request,
                agent_name=agent_name,
                reason=f"Failed to reach registered localhost frontend on port {proxy_port}.",
                exc=exc,
            )

        content_type = (upstream.headers.get("content-type") or "").lower()
        if is_media_stream and "text/html" not in content_type:
            response_headers = self._proxy_response_headers(dict(upstream.headers), agent_name)
            if upstream.headers.get("content-length"):
                response_headers["Content-Length"] = str(upstream.headers["content-length"])

            def iter_media_stream():
                try:
                    for chunk in upstream.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            yield chunk
                finally:
                    upstream.close()

            return StreamingResponse(
                iter_media_stream(),
                status_code=upstream.status_code,
                headers=response_headers,
            )

        content = b"" if request.method.upper() == "HEAD" else upstream.content
        if content and "text/html" in content_type:
            content = self._inject_agent_proxy_shim(content, agent_name)

        response = Response(
            content=content,
            status_code=upstream.status_code,
            headers=self._proxy_response_headers(dict(upstream.headers), agent_name),
        )
        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @staticmethod
    def _render_agent_frontend_card_markup(frontend: Dict[str, Any]) -> str:
      agent_name = html.escape(str(frontend.get("agent_name") or "").strip())
      title = html.escape(str(frontend.get("title") or frontend.get("agent_name") or "Website").strip())
      description = html.escape(str(frontend.get("description") or "Private website ready to open in AutoYou.").strip())
      search_name = html.escape(
        AutoYouPageService._normalize_agent_search_text(
          " ".join(
            (
              str(frontend.get("agent_name") or ""),
              format_agent_display_name(frontend.get("agent_name")),
              str(frontend.get("title") or ""),
            )
          )
        ),
        quote=True,
      )
      launch_path = html.escape(str(frontend.get("launch_path") or "Unavailable").strip())
      local_url = str(frontend.get("local_url") or "").strip()
      local_url_html = html.escape(local_url)
      proxy_port = str(frontend.get("proxy_port") or "").strip()
      route_mode = str(frontend.get("route_mode") or "").strip().lower().replace("-", "_")
      route_label = "Direct same-port" if route_mode == "direct_forward" else "Primary browser path"
      open_target = str(frontend.get("open_url") or frontend.get("launch_url") or frontend.get("launch_path") or "").strip()
      open_target_html = html.escape(open_target, quote=True)
      local_url_meta = (
        f'<div><strong>Local URL</strong><br><code>{local_url_html}</code></div>'
        if local_url
        else ""
      )
      proxy_port_html = (
        f'<div><strong>Registered port</strong><br><code>{html.escape(proxy_port)}</code></div>'
        if proxy_port
        else '<div><strong>Registered port</strong><br><span>Not registered</span></div>'
      )
      open_action = (
        f'<a class="button primary" href="{open_target_html}" data-launch-link="1" data-launch-target="{open_target_html}" onclick="return launchFrontend(event, this)">Open</a>'
        if frontend.get("frontend_port_registered") and open_target
        else '<button class="button secondary" type="button" disabled>Website Not Ready</button>'
      )
      copy_action = (
            f'<button class="button secondary" type="button" data-no-loading="1" data-copy-target="{open_target_html}" onclick="copyLaunchTarget(this.getAttribute(\'data-copy-target\'))">Copy Link</button>'
        if open_target
        else ""
      )
      return f"""
        <article class="card" data-agent-name="{agent_name}" data-search-name="{search_name}">
          <div>
            <h2>{title}</h2>
            <p>{description}</p>
          </div>
          <div class="meta">
            <div><strong>Website path</strong><br><code>{launch_path}</code></div>
            <div><strong>Route</strong><br><span>{html.escape(route_label)}</span></div>
            {local_url_meta}
            {proxy_port_html}
          </div>
          <div class="actions">
            {open_action}
            {copy_action}
          </div>
        </article>
      """.strip()

    def _render_agent_frontends_grid_markup(self, payload: Dict[str, Any]) -> str:
      items = payload.get("frontends") if isinstance(payload.get("frontends"), list) else []
      if not items:
        return '<div class="empty">No websites are ready right now. Turn one on in the admin UI and it will appear here.</div>'
      return "\n".join(self._render_agent_frontend_card_markup(item) for item in items if isinstance(item, dict))

    def _render_agent_frontends_page(self) -> str:
      initial_payload = self._load_agent_frontends()
      initial_client_payload = {
        "autoyou_browser_base_url": initial_payload.get("autoyou_browser_base_url"),
      }
      initial_items = initial_payload.get("frontends") if isinstance(initial_payload.get("frontends"), list) else []
      initial_status = (
        f"Found {len(initial_items)} website{'s' if len(initial_items) != 1 else ''} ready to open."
        if initial_items
        else "No websites are ready right now."
      )
      html_content = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>AutoYou Agent Websites</title>
  <script>
    (() => {
      const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
      const syncTheme = () => { document.documentElement.dataset.theme = systemTheme.matches ? 'dark' : 'light'; };
      syncTheme();
      if (systemTheme.addEventListener) systemTheme.addEventListener('change', syncTheme);
      else systemTheme.addListener(syncTheme);
    })();
  </script>
  <style>
    :root {
      color-scheme: dark;
      --bg: #1c1c1e;
      --card: #2c2c2e;
      --border: #48484a;
      --text: #f5f5f7;
      --muted: #aeaeb2;
      --primary: #0a84ff;
      --primary-ink: #ffffff;
      --accent: #64a9ff;
      --page-gradient: var(--bg);
      --hero-surface: var(--card);
      --code-bg: #38383a;
      --shadow: none;
    }
    :root[data-theme="light"] {
      color-scheme: light;
      --bg: #f5f5f7;
      --card: #ffffff;
      --border: #d8d8dc;
      --text: #1d1d1f;
      --muted: #626269;
      --primary: #006ad4;
      --primary-ink: #ffffff;
      --accent: #006ad4;
      --page-gradient: var(--bg);
      --hero-surface: var(--card);
      --code-bg: #f0f0f2;
      --shadow: none;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--page-gradient);
      color: var(--text);
    }
    main {
      width: min(1080px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }
    .hero {
      margin-bottom: 18px;
      padding: 24px;
      border-radius: 24px;
      border: 1px solid var(--border);
      background: var(--hero-surface);
      box-shadow: var(--shadow);
    }
    .eyebrow {
      margin: 0 0 8px;
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--accent);
    }
    h1 {
      margin: 0 0 8px;
      font-size: clamp(2rem, 4vw, 3rem);
      line-height: 1.05;
    }
    .summary {
      margin: 0;
      color: var(--muted);
      max-width: 64ch;
      line-height: 1.55;
    }
    .search-form {
      display: flex;
      gap: 10px;
      margin: 18px 0 0;
    }
    .search-label {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }
    .search-input {
      width: 100%;
      min-height: 48px;
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 0 16px;
      color: var(--text);
      background: var(--card);
      font: inherit;
      font-size: 1rem;
      outline: none;
      -webkit-appearance: none;
    }
    .search-input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(104, 181, 255, 0.18);
    }
    .toolbar {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      margin: 18px 0 20px;
    }
    .button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 46px;
      border: 1px solid transparent;
      border-radius: 16px;
      padding: 0 18px;
      font-weight: 700;
      cursor: pointer;
      text-decoration: none;
      touch-action: manipulation;
      -webkit-tap-highlight-color: transparent;
      box-shadow: 0 10px 20px rgba(2, 6, 23, 0.14);
      transition: transform 160ms ease, border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
    }
    .button.primary {
      background: var(--primary);
      color: var(--primary-ink);
    }
    .button.secondary {
      background: var(--card);
      color: var(--text);
      border: 1px solid var(--border);
    }
    .button:hover {
      transform: translateY(-1px);
      box-shadow: 0 14px 24px rgba(2, 6, 23, 0.18);
    }
    :root[data-theme="light"] .button {
      box-shadow: 0 10px 18px rgba(148, 163, 184, 0.14);
    }
    :root[data-theme="light"] .button.secondary {
      background: #ffffff;
      color: #111827;
      border-color: #cbd5e1;
    }
    .status {
      color: var(--muted);
      font-size: 0.92rem;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
    }
    .card {
      border-radius: 20px;
      border: 1px solid var(--border);
      background: var(--card);
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      min-height: 220px;
    }
    .card h2 {
      margin: 0;
      font-size: 1.1rem;
    }
    .card p {
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
    }
    .meta {
      display: grid;
      gap: 8px;
      font-size: 0.86rem;
    }
    .meta code {
      display: inline-block;
      padding: 2px 6px;
      border-radius: 8px;
      background: var(--code-bg);
      color: inherit;
      word-break: break-all;
    }
    .actions {
      margin-top: auto;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .empty {
      padding: 20px;
      border-radius: 18px;
      border: 1px dashed var(--border);
      color: var(--muted);
    }
    .button[aria-busy="true"] {
      opacity: 0.8;
      cursor: progress;
    }
    .button.loading {
      position: relative;
      cursor: wait;
      pointer-events: none;
      opacity: 0.82;
    }
    .button.loading > * {
      opacity: 0;
      visibility: hidden;
    }
    .button.loading::after {
      content: "";
      position: absolute;
      inset: 0;
      margin: auto;
      width: 16px;
      height: 16px;
      border-radius: 50%;
      border: 2.5px solid rgba(255, 255, 255, 0.25);
      border-top-color: currentColor;
      animation: ps-btn-spin 0.65s linear infinite;
    }
    @keyframes ps-btn-spin {
      to { transform: rotate(360deg); }
    }
    .card.is-opening {
      border-color: var(--accent);
    }
    .card[hidden] { display: none; }
    @media (max-width: 520px) {
      main {
        width: min(100% - 20px, 1080px);
        padding-top: 10px;
      }
      .hero, .card { border-radius: 16px; }
      .hero { padding: 18px; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <p class="eyebrow">AutoYou Browser</p>
      <h1>Websites</h1>
      <p class="summary">
        Open the websites AutoYou has ready inside this browser session.
        Use this page to launch private websites, copy their local links, and jump back into the browser from anywhere.
      </p>
      <form class="search-form" id="frontend-search-form" role="search">
        <label class="search-label" for="frontend-search">Search websites by agent name or description</label>
        <input
          class="search-input"
          id="frontend-search"
          type="search"
          maxlength="200"
          autocomplete="off"
          enterkeyhint="search"
          placeholder="Search agent names and descriptions"
        >
      </form>
    </section>

    <div class="toolbar">
      <button class="button primary" id="frontends-refresh" type="button" onclick="refreshAgentFrontends()">Refresh</button>
      <a class="button secondary" href="/">Back to AutoYou Page</a>
      <span class="status" id="status" aria-live="polite">__INITIAL_FRONTEND_STATUS__</span>
    </div>

    <section id="frontend-grid" class="grid">__INITIAL_FRONTEND_GRID_HTML__</section>
  </main>

  <script>
    const initialFrontendPayload = __INITIAL_AGENT_FRONTENDS__;
      const FRONTENDS_CACHE_KEY = 'autoyou.agentWebsites.v1';
      let registryRefreshTimer = null;
      let lastFrontendInteractionAt = 0;
      let searchTimer = null;
      let searchRequestSerial = 0;
      let searchAbortController = null;
      const initialGridMarkup = document.getElementById('frontend-grid').innerHTML;
      const initialStatusText = document.getElementById('status').textContent;

    function escapeHtml(value) {
      return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    function resolveLaunchTarget(target) {
      const rawTarget = String(target || '').trim();
      if (!rawTarget) {
        return '';
      }
      try {
        const currentOrigin = window.location.origin || '';
        const resolved = new URL(rawTarget, currentOrigin);
        const configuredBase = String(
          (initialFrontendPayload && initialFrontendPayload.autoyou_browser_base_url) || ''
        ).trim();
        if (configuredBase) {
          const configuredUrl = new URL(configuredBase, currentOrigin);
          const sameLoopbackHost =
            ['127.0.0.1', 'localhost', '::1'].includes((resolved.hostname || '').toLowerCase()) &&
            ['127.0.0.1', 'localhost', '::1'].includes((configuredUrl.hostname || '').toLowerCase());
          if (
            sameLoopbackHost &&
            resolved.port === configuredUrl.port &&
            configuredUrl.port
          ) {
            return new URL(
              `${resolved.pathname || '/'}${resolved.search || ''}${resolved.hash || ''}`,
              currentOrigin,
            ).toString();
          }
        }
        return resolved.toString();
      } catch (_) {
        return rawTarget;
      }
    }

    async function copyLaunchTarget(path) {
      const target = resolveLaunchTarget(path);
      try {
        await navigator.clipboard.writeText(target);
      } catch (_) {
        window.prompt('Copy this browser URL', target);
      }
    }

    function hasServerRenderedGrid() {
      const grid = document.getElementById('frontend-grid');
      if (!grid) return false;
      return Array.from(grid.children).some((child) => child.classList && child.classList.contains('card'));
    }

    function connectionLooksSlow() {
      try {
        const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
        if (!connection) return false;
        const effectiveType = String(connection.effectiveType || '').toLowerCase();
        return !!connection.saveData || effectiveType === 'slow-2g' || effectiveType === '2g';
      } catch (_) {
        return false;
      }
    }

    function cancelFrontendsRefresh() {
      if (registryRefreshTimer !== null) {
        clearTimeout(registryRefreshTimer);
        registryRefreshTimer = null;
      }
    }

    function cancelAgentSearch() {
      if (searchTimer !== null) {
        clearTimeout(searchTimer);
        searchTimer = null;
      }
      if (searchAbortController) {
        searchAbortController.abort();
        searchAbortController = null;
      }
    }

    function normalizeSearchText(value) {
      return String(value || '')
        .toLowerCase()
        .replace(/[_-]+/g, ' ')
        .replace(/\\s+/g, ' ')
        .trim();
    }

    function restoreInitialFrontends() {
      const grid = document.getElementById('frontend-grid');
      grid.innerHTML = initialGridMarkup;
      document.getElementById('status').textContent = initialStatusText;
    }

    function renderLocalNameFallback(query, options = {}) {
      const grid = document.getElementById('frontend-grid');
      grid.innerHTML = initialGridMarkup;
      const terms = normalizeSearchText(query).split(' ').filter(Boolean);
      let visible = 0;
      grid.querySelectorAll('.card').forEach((card) => {
        const searchName = normalizeSearchText(card.getAttribute('data-search-name'));
        const matches = terms.every((term) => searchName.includes(term));
        card.hidden = !matches;
        if (matches) visible += 1;
      });
      if (!visible) {
        grid.insertAdjacentHTML('beforeend', '<div class="empty">No agent name matches yet. Checking descriptions...</div>');
      }
      document.getElementById('status').textContent = options.failed
        ? `Showing ${visible} local name match${visible === 1 ? '' : 'es'}; description search is unavailable.`
        : 'Checking names and descriptions...';
    }

    function noteFrontendInteraction() {
      lastFrontendInteractionAt = Date.now();
    }

    function scheduleFrontendsRefresh(delayMs = 3500) {
      cancelFrontendsRefresh();
      if (connectionLooksSlow()) {
        return;
      }
      registryRefreshTimer = window.setTimeout(() => {
        registryRefreshTimer = null;
        if (document.visibilityState === 'visible') {
          refreshAgentFrontends({ quiet: true });
        }
      }, delayMs);
    }

    function readCachedFrontends() {
      try {
        const raw = sessionStorage.getItem(FRONTENDS_CACHE_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || !Array.isArray(parsed.frontends)) return null;
        return parsed;
      } catch (_) {
        return null;
      }
    }

    function writeCachedFrontends(payload) {
      try {
        if (!payload || !Array.isArray(payload.frontends)) return;
        sessionStorage.setItem(
          FRONTENDS_CACHE_KEY,
          JSON.stringify({
            frontends: payload.frontends,
            updated_at: payload.updated_at || null,
          }),
        );
      } catch (_) {}
    }

    function statusTextForFrontends(payload, options = {}) {
      const items = Array.isArray(payload && payload.frontends) ? payload.frontends : [];
      if (options.searchQuery) {
        return `Found ${items.length} match${items.length === 1 ? '' : 'es'} for “${options.searchQuery}”.`;
      }
      if (!items.length) {
        return options.fromCache
          ? 'Showing cached websites. No websites were cached.'
          : 'No websites are ready right now.';
      }
      return options.fromCache
        ? `Showing ${items.length} cached website${items.length === 1 ? '' : 's'} while the live list refreshes.`
        : `Found ${items.length} website${items.length === 1 ? '' : 's'} ready to open.`;
    }

    function launchFrontend(event, link) {
      const status = document.getElementById('status');
      const target = resolveLaunchTarget((link && (link.getAttribute('data-launch-target') || link.getAttribute('href'))) || '');
      if (!target) {
        return true;
      }
      try {
        event && event.preventDefault();
        event && event.stopPropagation();
      } catch (_) {}
      noteFrontendInteraction();
      cancelFrontendsRefresh();
      try {
        link.setAttribute('aria-busy', 'true');
        link.closest('.card')?.classList.add('is-opening');
      } catch (_) {}
      if (status) {
        status.textContent = 'Opening website...';
      }
      window.location.assign(target);
      return false;
    }

    function renderFrontendCard(frontend) {
      const proxyPort = frontend.proxy_port ? `<div><strong>Registered port</strong><br><code>${escapeHtml(frontend.proxy_port)}</code></div>` : `<div><strong>Registered port</strong><br><span>Not registered</span></div>`;
      const launchPath = (frontend.launch_path || 'Unavailable').trim();
      const localUrl = (frontend.local_url || '').trim();
      const routeMode = String(frontend.route_mode || '').toLowerCase().replace('-', '_');
      const routeLabel = routeMode === 'direct_forward' ? 'Direct same-port' : 'Primary browser path';
      const openTarget = resolveLaunchTarget(frontend.open_url || frontend.launch_url || frontend.launch_path || '');
      const openAction = (frontend.frontend_port_registered && openTarget)
        ? `<a class="button primary" href=${JSON.stringify(openTarget)} data-launch-link="1" data-launch-target=${JSON.stringify(openTarget)} onclick="return launchFrontend(event, this)">Open</a>`
        : `<button class="button secondary" type="button" disabled>Website Not Ready</button>`;
      const localUrlMeta = localUrl
        ? `<div><strong>Local URL</strong><br><code>${escapeHtml(localUrl)}</code></div>`
        : '';
      const searchName = normalizeSearchText(`${frontend.agent_name || ''} ${frontend.title || ''}`);
      return `
        <article class="card" data-agent-name="${escapeHtml(frontend.agent_name || '')}" data-search-name="${escapeHtml(searchName)}">
          <div>
            <h2>${escapeHtml(frontend.title || frontend.agent_name || 'Website')}</h2>
            <p>${escapeHtml(frontend.description || 'Private website ready to open in AutoYou.')}</p>
          </div>
          <div class="meta">
            <div><strong>Website path</strong><br><code>${escapeHtml(launchPath)}</code></div>
            <div><strong>Route</strong><br><span>${escapeHtml(routeLabel)}</span></div>
            ${localUrlMeta}
            ${proxyPort}
          </div>
          <div class="actions">
            ${openAction}
            <button class="button secondary" type="button" data-no-loading="1" onclick="copyLaunchTarget(${JSON.stringify(openTarget)})">Copy Link</button>
          </div>
        </article>
      `;
    }

    function renderFrontendsPayload(payload, options = {}) {
      const status = document.getElementById('status');
      const grid = document.getElementById('frontend-grid');
      const items = Array.isArray(payload && payload.frontends) ? payload.frontends : [];
      if (!items.length) {
        if (!options.skipRender) {
          grid.innerHTML = options.searchQuery
            ? '<div class="empty">No agent websites match that name or description.</div>'
            : '<div class="empty">No websites are ready right now. Turn one on in the admin UI and it will appear here.</div>';
        }
        status.textContent = statusTextForFrontends(payload, options);
        return;
      }
      if (!options.skipRender) {
        grid.innerHTML = items.map(renderFrontendCard).join('');
      }
      status.textContent = statusTextForFrontends(payload, options);
    }

    async function searchAgentFrontends(options = {}) {
      const input = document.getElementById('frontend-search');
      const query = String(input && input.value || '').trim();
      searchTimer = null;
      if (!query) {
        cancelAgentSearch();
        searchRequestSerial += 1;
        restoreInitialFrontends();
        return;
      }

      if (searchAbortController) searchAbortController.abort();
      searchAbortController = typeof AbortController === 'function' ? new AbortController() : null;
      const requestSerial = ++searchRequestSerial;
      const refreshButton = document.getElementById('frontends-refresh');
      if (options.manual && refreshButton) {
        refreshButton.disabled = true;
        refreshButton.setAttribute('aria-busy', 'true');
        refreshButton.classList.add('loading');
      }
      try {
        const requestOptions = {
          method: 'QUERY',
          headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ query, limit: 24 }),
        };
        if (searchAbortController) requestOptions.signal = searchAbortController.signal;
        const response = await fetch('/api/websites', requestOptions);
        const payload = await response.json();
        if (!response.ok || !payload.success) {
          throw new Error(payload.error || ('HTTP ' + response.status));
        }
        if (requestSerial !== searchRequestSerial) return;
        renderFrontendsPayload(payload, { searchQuery: query });
      } catch (error) {
        if (error && error.name === 'AbortError') return;
        if (requestSerial !== searchRequestSerial) return;
        renderLocalNameFallback(query, { failed: true });
      } finally {
        if (options.manual && refreshButton) {
          refreshButton.disabled = false;
          refreshButton.removeAttribute('aria-busy');
          refreshButton.classList.remove('loading');
        }
      }
    }

    function scheduleAgentSearch() {
      const input = document.getElementById('frontend-search');
      const query = String(input && input.value || '').trim();
      cancelAgentSearch();
      searchRequestSerial += 1;
      if (!query) {
        restoreInitialFrontends();
        return;
      }
      renderLocalNameFallback(query);
      searchTimer = window.setTimeout(
        () => searchAgentFrontends(),
        connectionLooksSlow() ? 420 : 180,
      );
    }

    async function refreshAgentFrontends(options = {}) {
      const searchInput = document.getElementById('frontend-search');
      if (String(searchInput && searchInput.value || '').trim()) {
        return searchAgentFrontends({ manual: true });
      }
      const status = document.getElementById('status');
      const grid = document.getElementById('frontend-grid');
      const refreshButton = document.getElementById('frontends-refresh');
      noteFrontendInteraction();
      if (options.quiet !== true) {
        cancelFrontendsRefresh();
      }
      if (refreshButton && !options.quiet) {
        refreshButton.disabled = true;
        refreshButton.setAttribute('aria-busy', 'true');
        refreshButton.classList.add('loading');
      }
      if (!options.quiet) {
        status.textContent = 'Refreshing websites...';
      }
      try {
        const response = await fetch('/api/websites');
        const payload = await response.json();
        if (!response.ok || !payload.success) {
          throw new Error(payload.error || ('HTTP ' + response.status));
        }
        writeCachedFrontends(payload);
        const shouldSkipQuietRender = !!options.quiet && (Date.now() - lastFrontendInteractionAt) < 2500 && hasServerRenderedGrid();
        renderFrontendsPayload(payload, { skipRender: shouldSkipQuietRender });
      } catch (error) {
        const cached = readCachedFrontends();
        if (cached && Array.isArray(cached.frontends) && cached.frontends.length) {
          renderFrontendsPayload(cached, { fromCache: true });
          return;
        }
        grid.innerHTML = `<div class="empty">Failed to load websites: ${escapeHtml(error)}</div>`;
        status.textContent = 'Website list unavailable.';
      } finally {
        if (refreshButton) {
          refreshButton.disabled = false;
          refreshButton.removeAttribute('aria-busy');
          refreshButton.classList.remove('loading');
        }
      }
    }

    document.addEventListener('pointerdown', (event) => {
      if (event.target && event.target.closest && event.target.closest('a,button,input,select,textarea')) {
        noteFrontendInteraction();
        scheduleFrontendsRefresh(9000);
      }
    }, { passive: true });
    document.addEventListener('keydown', () => {
      noteFrontendInteraction();
      scheduleFrontendsRefresh(9000);
    });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        cancelFrontendsRefresh();
      } else if (document.getElementById('frontend-grid')?.children?.length) {
        scheduleFrontendsRefresh(5000);
      }
    });
    window.addEventListener('pagehide', () => {
      cancelFrontendsRefresh();
      cancelAgentSearch();
    });

    document.getElementById('frontend-search-form').addEventListener('submit', (event) => {
      event.preventDefault();
      cancelAgentSearch();
      renderLocalNameFallback(document.getElementById('frontend-search').value);
      searchAgentFrontends({ manual: true });
    });
    document.getElementById('frontend-search').addEventListener('input', scheduleAgentSearch);
    document.getElementById('frontend-search').addEventListener('search', scheduleAgentSearch);

    // ── Universal button loading spinner ────────────────────────────────
    // Handles both the Refresh button (explicit .loading class) and any
    // other type=button that triggers a server action (Copy Link, etc.)
    (function () {
      function btnLoading(el) {
        if (!el) return;
        el.classList.add('loading');
        el.setAttribute('data-loading', '1');
      }
      function btnDone(el) {
        if (!el) return;
        el.classList.remove('loading');
        el.removeAttribute('data-loading');
        el.disabled = false;
        if (el._psBtnCT) { clearTimeout(el._psBtnCT); el._psBtnCT = null; }
      }

      // bfcache restore: clear any stale spinners
      window.addEventListener('pageshow', function (e) {
        if (e.persisted) {
          document.querySelectorAll('button.loading,[data-loading]').forEach(function (b) { btnDone(b); });
        }
      });

      // Click delegation: attach spinner to async type=button actions
      document.addEventListener('click', function (e) {
        var btn = e.target && e.target.closest ? e.target.closest('button') : null;
        if (!btn || btn.type === 'submit') return;
        if (btn.disabled || btn.classList.contains('loading') || btn.hasAttribute('data-loading')) return;
        if (btn.matches && btn.matches('[data-no-loading]')) return;
        // Skip the Refresh button - it manages its own .loading state explicitly
        if (btn.id === 'frontends-refresh') return;
        btnLoading(btn);
        var t = window.setTimeout(function () {
          if (btn._psBtnCT === t) btnDone(btn);
        }, 8000);
        btn._psBtnCT = t;
      }, true);
    })();

    const hasInitialMarkup = hasServerRenderedGrid();
    if (hasInitialMarkup) {
      scheduleFrontendsRefresh();
    } else {
      const cached = readCachedFrontends();
      if (cached && Array.isArray(cached.frontends) && cached.frontends.length) {
        renderFrontendsPayload(cached, { fromCache: true, skipRender: hasInitialMarkup });
        scheduleFrontendsRefresh(2500);
      } else {
        refreshAgentFrontends();
      }
    }
  </script>
</body>
</html>
"""
      html_content = html_content.replace(
          '__INITIAL_AGENT_FRONTENDS__',
          self._serialize_inline_json(initial_client_payload),
      )
      html_content = html_content.replace(
          '__INITIAL_FRONTEND_GRID_HTML__',
          self._render_agent_frontends_grid_markup(initial_payload),
      )
      html_content = html_content.replace(
          '__INITIAL_FRONTEND_STATUS__',
          html.escape(initial_status),
      )
      return html_content

    def _setup_app(self) -> None:
        """Setup FastAPI application with routes."""
        self.app = FastAPI(
            title="For AutoYou Page",
            description="A simple website for AutoYou remote client access",
            version="1.0.0"
        )
        install_route_aware_request_logging(
            self.app,
            logger_name="autoyou.http.page_service",
            debug_path_prefixes=("/health",),
        )

        @self.app.middleware("http")
        async def home_network_browser_gate(request: Request, call_next):
            """Hold browsers on other devices to HTTPS and the remote client role.

            With home network access on, this service listens beyond loopback,
            and pages, notes and every agent website behind it would otherwise
            treat a neighbour's browser as this computer's owner.
            """
            peer = request.client.host if request.client else ""
            if not self._is_home_network_browser(peer):
                return await call_next(request)
            method = request.method.upper()
            path = request.url.path or "/"
            if request.url.scheme != "https" and self._https_mirror_configured():
                https_url = self._https_url_for(request.url)
                if method in {"GET", "HEAD"} and https_url:
                    return RedirectResponse(url=https_url, status_code=307)
                return JSONResponse(
                    {
                        "success": False,
                        "error": "This computer serves its websites to the home network over HTTPS only.",
                        "https_url": https_url or "",
                    },
                    status_code=403,
                )
            role = self._remote_access_role()
            if not remote_http_request_allowed(role, method, path):
                return JSONResponse(
                    {
                        "success": False,
                        "error": remote_access_denial_message(role, method, path),
                        "remote_access_role": role,
                    },
                    status_code=403,
                )
            request.scope["headers"] = self._home_network_identity_headers(
                request.scope.get("headers") or [], role, path,
            )
            return await call_next(request)

        # Setup routes
        self._setup_routes()
    
    def _setup_routes(self) -> None:
        """Setup HTTP routes for the For AutoYou Page website."""

        @self.app.get("/websites", response_class=HTMLResponse)
        @self.app.get("/agent-websites", response_class=HTMLResponse)
        async def agent_websites_page():
            return _no_store_html_response(content=self._render_agent_frontends_page())

        @self.app.get("/agent-frontends", response_class=HTMLResponse)
        async def agent_frontends_page():
            return RedirectResponse(url="/websites", status_code=307)

        @self.app.get("/api/websites")
        @self.app.get("/api/agent-websites")
        async def api_agent_websites():
            payload = await asyncio.to_thread(self._load_agent_frontends)
            return JSONResponse(
                payload,
                status_code=200,
                headers={"Accept-Query": AGENT_WEBSITES_ACCEPT_QUERY},
            )

        @self.app.api_route("/api/websites", methods=["QUERY"])
        @self.app.api_route("/api/agent-websites", methods=["QUERY"])
        async def query_agent_websites(request: Request):
            response_headers = {
                "Accept-Query": AGENT_WEBSITES_ACCEPT_QUERY,
                "Cache-Control": "private, max-age=30",
                "Vary": "Accept, Content-Type",
            }
            content_type = str(request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if content_type != AGENT_WEBSITES_QUERY_MEDIA_TYPE:
                return JSONResponse(
                    {"success": False, "error": "QUERY requires Content-Type: application/json."},
                    status_code=415 if content_type else 400,
                    headers=response_headers,
                )
            raw_body = await request.body()
            if len(raw_body) > AGENT_WEBSITES_QUERY_MAX_BYTES:
                return JSONResponse(
                    {"success": False, "error": "Agent website search query is too large."},
                    status_code=413,
                    headers=response_headers,
                )
            try:
                query_payload = json.loads(raw_body)
            except (TypeError, ValueError, UnicodeDecodeError):
                return JSONResponse(
                    {"success": False, "error": "Agent website search query must be valid JSON."},
                    status_code=400,
                    headers=response_headers,
                )
            if not isinstance(query_payload, dict):
                return JSONResponse(
                    {"success": False, "error": "Agent website search query must be a JSON object."},
                    status_code=422,
                    headers=response_headers,
                )
            query = query_payload.get("query", "")
            limit = query_payload.get("limit", AGENT_WEBSITES_QUERY_DEFAULT_LIMIT)
            if not isinstance(query, str) or len(query) > AGENT_WEBSITES_QUERY_MAX_LENGTH:
                return JSONResponse(
                    {"success": False, "error": "query must be a string of at most 200 characters."},
                    status_code=422,
                    headers=response_headers,
                )
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= AGENT_WEBSITES_QUERY_MAX_LIMIT:
                return JSONResponse(
                    {"success": False, "error": "limit must be an integer from 1 through 50."},
                    status_code=422,
                    headers=response_headers,
                )
            payload = await asyncio.to_thread(self._search_agent_frontends, query, limit=limit)
            return JSONResponse(payload, status_code=200, headers=response_headers)

        @self.app.options("/api/websites")
        @self.app.options("/api/agent-websites")
        async def options_agent_websites():
            return Response(
                status_code=204,
                headers={
                    "Allow": "GET, HEAD, OPTIONS, QUERY",
                    "Accept-Query": AGENT_WEBSITES_ACCEPT_QUERY,
                },
            )

        @self.app.get("/api/agent-frontends")
        async def api_agent_frontends():
            payload = await asyncio.to_thread(self._load_agent_frontends)
            return JSONResponse(payload, status_code=200)

        @self.app.get("/api/ui/theme")
        async def api_ui_theme():
            return JSONResponse({
                "success": True,
                "theme": self._get_ui_theme(),
                "themes": ["dark", "light"],
            })

        @self.app.post("/api/ui/theme")
        async def api_set_ui_theme(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            saved_theme = await asyncio.to_thread(self._set_ui_theme, (payload or {}).get("theme"))
            return JSONResponse({"success": True, "theme": saved_theme})

        @self.app.get("/api/agent-directory")
        async def api_agent_directory():
            payload = await asyncio.to_thread(self._load_agent_directory)
            status_code = 200 if payload.get("success") else 500
            return JSONResponse(payload, status_code=status_code)

        @self.app.get("/agent/{agent_name}")
        async def agent_frontend_root(request: Request, agent_name: str):
            if agent_name not in self._frontend_registry_map():
                return self._agent_website_fail_closed(
                    request,
                    agent_name=agent_name,
                    reason="No installed frontend is registered for the requested agent path.",
                )
            return RedirectResponse(url=f"/agent/{agent_name}/", status_code=307)

        @self.app.api_route(
            "/agent/{agent_name}/{proxy_path:path}",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "QUERY"],
        )
        async def proxy_agent_frontend(request: Request, agent_name: str, proxy_path: str = ""):
            return await self._proxy_agent_frontend_request(
                request,
                agent_name=agent_name,
                proxy_path=proxy_path,
            )

        @self.app.websocket("/agent/{agent_name}/{proxy_path:path}")
        async def proxy_agent_frontend_websocket(websocket: WebSocket, agent_name: str, proxy_path: str = ""):
            await self._proxy_agent_frontend_websocket(
                websocket,
                agent_name=agent_name,
                proxy_path=proxy_path,
            )
        
        @self.app.get("/", response_class=HTMLResponse)
        async def home_page():
            """Redirect the page-service root to the configured default agent website.

            The page feed UI/API now lives in the page_agent managed frontend;
            this service is the shared agent-website proxy host. The default
            (home) website is operator-settable via the frontend registry's
            ``default_agent`` (admin: POST /api/agent-websites/default), so every
            connected WebRTC browser client lands on the chosen website. Defaults
            to page_agent.
            """
            return RedirectResponse(url=self._default_browser_path(), status_code=307)

        @self.app.get("/api/status")
        async def api_status():
            """Get service status."""
            return JSONResponse({
                "service": "autoyou_page_service",
                "status": "active",
                "port": self.port,
                "timestamp": datetime.now().isoformat(),
                "capabilities": [
                    "webrtc_datachannel_forwarding",
                    "http_request_processing",
                    "real_time_communication"
                ]
            })
        
        @self.app.get("/api/info")
        async def api_info():
            """Get service information."""
            return JSONResponse({
                "name": "For AutoYou Page Service",
                "version": "1.0.0",
                "description": "HTTP server for AutoYou remote client access via WebRTC datachannel",
                "endpoints": {
                    "/": "Main page",
                    "/websites": "Directory of websites available in the AutoYou browser",
                    "/api/websites": "Website metadata for the browser directory",
                    "/agent-websites": "Legacy alias for /websites",
                    "/api/agent-websites": "Legacy alias for /api/websites",
                    "/agent-frontends": "Legacy redirect to /websites",
                    "/api/agent-frontends": "Legacy alias for /api/websites",
                    "/api/agent-directory": "All installed AutoYou sub-agents",
                    "/api/status": "Service status",
                    "/api/info": "Service information",
                    "/api/echo": "Echo test",
                    "/health": "Health check"
                },
                "transport": "WebRTC SCTP Datachannel",
                "security": "End-to-end encrypted"
            })
        
        # Header-name prefixes/exact-names that must never be reflected by /api/echo.
        # Even though the page service only expects loopback traffic, the WebRTC
        # datachannel proxy forwards requests from any paired client to this
        # endpoint, so anything that leaks via reflection is remote-reachable.
        _ECHO_SENSITIVE_HEADER_EXACT = frozenset(
            {
                "authorization",
                "cookie",
                "set-cookie",
                "proxy-authorization",
                "proxy-authenticate",
                "www-authenticate",
            }
        )
        _ECHO_SENSITIVE_HEADER_PREFIXES = ("x-", "authorization-", "sec-")

        def _safe_headers_for_echo(raw_headers):
            safe = {}
            for name, value in raw_headers.items():
                lower = name.lower()
                if lower in _ECHO_SENSITIVE_HEADER_EXACT:
                    continue
                if any(lower.startswith(prefix) for prefix in _ECHO_SENSITIVE_HEADER_PREFIXES):
                    continue
                safe[name] = value
            return safe

        @self.app.post("/api/echo")
        async def api_echo(request: Request):
            """Echo test endpoint (headers are filtered to prevent
            information disclosure when reached via the datachannel proxy)."""
            try:
                body = await request.body()
                content_type = request.headers.get("content-type", "")

                if content_type.startswith("application/json"):
                    try:
                        data = json.loads(body)
                        return JSONResponse({
                            "echo": data,
                            "timestamp": datetime.now().isoformat(),
                            "method": request.method,
                            "headers": _safe_headers_for_echo(request.headers),
                        })
                    except json.JSONDecodeError:
                        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
                else:
                    return JSONResponse({
                        "echo": body.decode('utf-8', errors='ignore'),
                        "timestamp": datetime.now().isoformat(),
                        "method": request.method,
                        "content_type": content_type,
                    })
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=500)
        
        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            return PlainTextResponse("OK")
        
        @self.app.get("/favicon.ico")
        async def favicon():
            """Simple favicon response."""
            return PlainTextResponse("", status_code=204)
    
    async def start(self) -> None:
        """Start the For AutoYou Page service."""
        if FastAPI is None or uvicorn is None:
            LOGGER.error("FastAPI/uvicorn not available, cannot start autoyou_page_service")
            return
        
        try:
            config = uvicorn.Config(
                app=self.app,
                host=self.host,
                port=self.port,
                log_level="info",
                access_log=False,
            )
            self.server = uvicorn.Server(config)
            LOGGER.info(f"Starting For AutoYou Page service on {self.host}:{self.port}")

            serve_coros = [self.server.serve()]
            if self.https_port and self.ssl_certfile and self.ssl_keyfile:
                https_config = uvicorn.Config(
                    app=self.app,
                    host=self.host,
                    port=int(self.https_port),
                    log_level="warning",
                    access_log=False,
                    ssl_certfile=self.ssl_certfile,
                    ssl_keyfile=self.ssl_keyfile,
                )
                self.https_server = uvicorn.Server(https_config)
                LOGGER.info(
                    f"Starting For AutoYou Page service HTTPS mirror on {self.host}:{self.https_port}"
                )
                serve_coros.append(self.https_server.serve())

            await asyncio.gather(*serve_coros)

        except Exception as e:
            LOGGER.error(f"Failed to start For AutoYou Page service: {e}")
            raise
    
    async def start_background(self) -> None:
        """Start the service in the background."""
        if self.server_task is None or self.server_task.done():
            self.server_task = asyncio.create_task(self.start())
            LOGGER.info("For AutoYou Page service started in background")
    
    async def stop(self) -> None:
        """Stop the For AutoYou Page service."""
        try:
            if self.https_server is not None:
                try:
                    self.https_server.should_exit = True
                except Exception:
                    pass
            if self.server:
                LOGGER.info("Stopping For AutoYou Page service...")
                # Signal uvicorn to exit gracefully
                self.server.should_exit = True

                # Wait for the server task to complete without canceling it,
                # so uvicorn has a chance to close sockets cleanly.
                if self.server_task:
                    try:
                        await asyncio.wait_for(self.server_task, timeout=5)
                    except asyncio.TimeoutError:
                        LOGGER.warning("Server did not stop within timeout; forcing exit")
                        try:
                            # Fall back to force exit if available
                            setattr(self.server, "force_exit", True)
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)
                    except asyncio.CancelledError:
                        # Already cancelled elsewhere; continue
                        pass

                LOGGER.info("For AutoYou Page service stopped")
                # Clear references to help GC and avoid stale state
                self.server_task = None
                self.server = None
        except Exception as e:
            LOGGER.error(f"Error stopping For AutoYou Page service: {e}")
    
    def is_running(self) -> bool:
        """Check if the service is running."""
        return (self.server_task is not None and 
                not self.server_task.done() and 
                self.server is not None)

# Global service instance
_autoyou_service: Optional[AutoYouPageService] = None

async def start_autoyou_page_service(
    port: int = 8067,
    host: str = "127.0.0.1",
    timeline_days: int = 7,
    https_port: Optional[int] = None,
    ssl_certfile: Optional[str] = None,
    ssl_keyfile: Optional[str] = None,
) -> AutoYouPageService:
    """Start the For AutoYou Page service (optionally with an additive HTTPS mirror)."""
    global _autoyou_service

    if _autoyou_service is None:
        _autoyou_service = AutoYouPageService(
            port=port,
            host=host,
            timeline_days=timeline_days,
            https_port=https_port,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
        )

    await _autoyou_service.start_background()
    return _autoyou_service

async def stop_autoyou_page_service() -> None:
    """Stop the For AutoYou Page service."""
    global _autoyou_service
    
    if _autoyou_service:
        await _autoyou_service.stop()
        _autoyou_service = None

def get_autoyou_page_service() -> Optional[AutoYouPageService]:
    """Get the current For AutoYou Page service instance."""
    return _autoyou_service

async def is_autoyou_page_service_running() -> bool:
    """Check if the For AutoYou Page service is running."""
    return _autoyou_service is not None and _autoyou_service.is_running()

if __name__ == "__main__":
    # Run the service directly for testing
    async def main():
        import os
        from typing import cast
        # Allow overriding host/port via environment for local runs
        host = cast(str, os.environ.get("AUTOYOU_PAGE_HOST") or "127.0.0.1")
        port_str = os.environ.get("AUTOYOU_PAGE_PORT") or os.environ.get("PORT") or "8067"
        try:
            port = int(port_str)
        except ValueError:
            port = 8067
        service = AutoYouPageService(port=port, host=host)
        await service.start()
    
    asyncio.run(main())
