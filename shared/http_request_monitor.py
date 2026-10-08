# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Opt-in, metadata-only capture of inbound HTTP requests to AutoYou services."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlsplit

from fastapi import Request


LOGGER = logging.getLogger("autoyou.http_request_monitor")
MAX_EVENTS = 25000
MAX_PAGE_SIZE = 200
RETENTION_DAYS = 30
ENABLE_CACHE_SECONDS = 0.5
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_SAFE_QUERY_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SENSITIVE_QUERY_KEY = re.compile(r"(?:token|auth|password|secret|otp|code|key|session|credential)", re.I)
_SENSITIVE_PATH_FIELD = re.compile(r"(?:token|auth|password|secret|otp|session|credential)[=_:-].+", re.I)
_EMAIL_PATH_SEGMENT = re.compile(r"^[A-Z0-9._%+-]{1,64}@[A-Z0-9.-]+\.[A-Z]{2,63}$", re.I)
_PHONE_PATH_SEGMENT = re.compile(r"^\+?[\d(). -]{9,}$")
_LONG_SECRET_SEGMENT = re.compile(r"^(?:\d{6}|[A-Fa-f0-9]{32,}|[A-Za-z0-9_-]{40,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})$")
_AGENT_PATH = re.compile(r"^/agent/([A-Za-z0-9_-]{1,100})(?:/|$)")
_PROBE_MARKERS = (
    "/.env", "/.git", "/wp-admin", "/wp-login", "/xmlrpc.php", "/phpmyadmin",
    "/actuator", "/server-status", "/cgi-bin", "/vendor/phpunit", "/boaform",
    "/autodiscover", "/etc/passwd", "/config.php", "/config.json",
)
_STANDARD_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "QUERY"})


def default_storage_path() -> Path:
    """Resolve private storage through the common runtime root, including tests."""
    from shared.platform_runtime import get_service_data_dir

    return get_service_data_dir("http_request_monitor", anchor=__file__) / "events.sqlite3"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _bounded_text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return "".join(char for char in text if char >= " " and char != "\x7f")[:limit]


def _safe_unknown_path(path: str) -> str:
    parts = []
    for part in (path or "/").split("/"):
        if (
            _LONG_SECRET_SEGMENT.fullmatch(part)
            or _SENSITIVE_PATH_FIELD.search(part)
            or _EMAIL_PATH_SEGMENT.fullmatch(part)
            or _PHONE_PATH_SEGMENT.fullmatch(part)
        ):
            parts.append("[redacted]")
        else:
            parts.append(_bounded_text(part, 100))
    return ("/".join(parts) or "/")[:512]


def _safe_query_keys(query: str) -> list[str]:
    names: set[str] = set()
    try:
        pairs = parse_qsl(query, keep_blank_values=True, max_num_fields=64)
    except (ValueError, UnicodeError):
        return ["[redacted]"] if query else []
    for key, _value in pairs:
        name = _bounded_text(key, 64)
        if _SENSITIVE_QUERY_KEY.search(name):
            names.add("[redacted]")
        elif _SAFE_QUERY_KEY.fullmatch(name):
            names.add(name)
        else:
            names.add("[other]")
    return sorted(names)[:32]


def _safe_host(value: str) -> str:
    raw = _bounded_text(value, 256).strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit("//" + raw)
        host = str(parsed.hostname or "").lower()
        if not host:
            return ""
        try:
            address = ipaddress.ip_address(host)
            host = f"[{address}]" if address.version == 6 else str(address)
        except ValueError:
            if not re.fullmatch(r"[a-z0-9.-]{1,253}", host):
                return ""
        return host
    except ValueError:
        return ""


def _referer_origin(value: str) -> str:
    raw = _bounded_text(value, 1024).strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        host = _safe_host(f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname)
        if not host:
            return ""
        try:
            port = parsed.port
        except ValueError:
            return ""
        default_port = (parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)
        authority = host if not port or default_port else f"{host}:{port}"
        return f"{parsed.scheme.lower()}://{authority}"
    except ValueError:
        return ""


def _source_scope(address: str) -> str:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return "unknown"
    if parsed.is_loopback:
        return "loopback"
    if parsed.is_private or parsed.is_link_local:
        return "private"
    if parsed.is_global:
        return "public"
    return "reserved"


def _event_kind(path: str, method: str, status_code: int) -> tuple[str, str]:
    normalized = (path or "/").lower()
    for marker in _PROBE_MARKERS:
        if marker in normalized:
            return "probe", "Known scan path"
    if method.upper() not in _STANDARD_METHODS:
        return "probe", "Unusual HTTP method"
    if status_code in {404, 405}:
        return "unmatched", "No matching route"
    return "request", ""


class HTTPRequestCaptureStore:
    """Bounded SQLite event store. Request bodies and credential values are never accepted."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or default_storage_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._enabled_cache: Optional[bool] = None
        self._enabled_cache_until = 0.0
        self._last_prune = 0.0
        if os.name != "nt":
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO settings(name, value) VALUES ('enabled', '0')"
            )
            connection.execute(
                "INSERT OR IGNORE INTO settings(name, value) VALUES ('enabled_since', '')"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    service TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    source_ip TEXT NOT NULL,
                    source_port INTEGER,
                    source_scope TEXT NOT NULL,
                    destination_port INTEGER,
                    destination_host TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    route TEXT NOT NULL DEFAULT '',
                    query_keys TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    user_agent TEXT NOT NULL,
                    referer_origin TEXT NOT NULL,
                    http_version TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    signal TEXT NOT NULL
                )
                """
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(events)").fetchall()}
            if "route" not in columns:
                connection.execute("ALTER TABLE events ADD COLUMN route TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_http_events_time ON events(captured_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_http_events_source ON events(source_ip, captured_at DESC)"
            )
            self._prune_events(connection)
        if os.name != "nt":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def is_enabled(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._enabled_cache is not None and now < self._enabled_cache_until:
                return self._enabled_cache
            with self._connect() as connection:
                row = connection.execute("SELECT value FROM settings WHERE name = 'enabled'").fetchone()
            self._enabled_cache = bool(row and row[0] == "1")
            self._enabled_cache_until = now + ENABLE_CACHE_SECONDS
            return self._enabled_cache

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        value = bool(enabled)
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE settings SET value = ? WHERE name = 'enabled'",
                ("1" if value else "0",),
            )
            connection.execute(
                "UPDATE settings SET value = ? WHERE name = 'enabled_since'",
                (now if value else "",),
            )
        self._enabled_cache = value
        self._enabled_cache_until = time.monotonic() + ENABLE_CACHE_SECONDS
        return {"enabled": value, "enabled_since": now if value else None}

    def _prune_events(self, connection: sqlite3.Connection) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        connection.execute("DELETE FROM events WHERE captured_at < ?", (cutoff,))
        connection.execute(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)",
            (MAX_EVENTS,),
        )
        self._last_prune = time.monotonic()

    def record(self, event: dict[str, Any]) -> bool:
        captured_at = _bounded_text(event.get("captured_at") or _utc_now(), 40)
        with self._lock, self._connect() as connection:
            enabled = connection.execute(
                "SELECT value FROM settings WHERE name = 'enabled'"
            ).fetchone()
            if not enabled or enabled[0] != "1":
                self._enabled_cache = False
                self._enabled_cache_until = time.monotonic() + ENABLE_CACHE_SECONDS
                return False
            connection.execute(
                """
                INSERT INTO events (
                    captured_at, service, agent_name, source_ip, source_port, source_scope,
                    destination_port, destination_host, method, path, query_keys,
                    route, status_code, user_agent, referer_origin, http_version, event_kind, signal
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_at,
                    _bounded_text(event.get("service"), 64),
                    _bounded_text(event.get("agent_name"), 100),
                    _bounded_text(event.get("source_ip"), 64),
                    event.get("source_port"),
                    _bounded_text(event.get("source_scope"), 16),
                    event.get("destination_port"),
                    _bounded_text(event.get("destination_host"), 256),
                    _bounded_text(event.get("method"), 16),
                    _bounded_text(event.get("path"), 512),
                    json.dumps(event.get("query_keys") or [], separators=(",", ":")),
                    _bounded_text(event.get("route"), 512),
                    int(event.get("status_code") or 0),
                    _bounded_text(event.get("user_agent"), 256),
                    _bounded_text(event.get("referer_origin"), 300),
                    _bounded_text(event.get("http_version"), 12),
                    _bounded_text(event.get("event_kind"), 16),
                    _bounded_text(event.get("signal"), 64),
                ),
            )
            if time.monotonic() - self._last_prune >= 60:
                self._prune_events(connection)
        self._enabled_cache = True
        self._enabled_cache_until = time.monotonic() + ENABLE_CACHE_SECONDS
        return True

    def dashboard(
        self,
        *,
        limit: int = 100,
        service: str = "",
        before_id: Optional[int] = None,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        normalized_service = _bounded_text(service, 64).strip()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        where = "WHERE captured_at >= ?"
        params: list[Any] = [cutoff]
        if normalized_service:
            where += " AND service = ?"
            params.append(normalized_service)
        with self._lock, self._connect() as connection:
            # Dashboard reads enforce the advertised retention bound even if
            # capture has been disabled and no new event has triggered pruning.
            self._prune_events(connection)
            counts = connection.execute(
                f"""
                SELECT COUNT(*) AS events_24h,
                       COUNT(DISTINCT source_ip) AS sources_24h,
                       SUM(CASE WHEN event_kind = 'probe' THEN 1 ELSE 0 END) AS probes_24h,
                       MAX(captured_at) AS last_event
                FROM events {where}
                """,
                params,
            ).fetchone()
            total = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            top_sources = connection.execute(
                f"""
                SELECT source_ip, source_scope, COUNT(*) AS requests,
                       SUM(CASE WHEN event_kind = 'probe' THEN 1 ELSE 0 END) AS probes,
                       SUM(CASE WHEN event_kind = 'unmatched' THEN 1 ELSE 0 END) AS unmatched,
                       COUNT(DISTINCT destination_port) AS destination_ports,
                       MAX(captured_at) AS last_seen
                FROM events {where}
                GROUP BY source_ip, source_scope
                ORDER BY probes DESC, destination_ports DESC, requests DESC, last_seen DESC
                LIMIT 12
                """,
                params,
            ).fetchall()
            services = connection.execute(
                f"""
                SELECT service, agent_name, destination_port, COUNT(*) AS requests,
                       MAX(captured_at) AS last_seen
                FROM events {where}
                GROUP BY service, agent_name, destination_port
                ORDER BY requests DESC, service, agent_name
                LIMIT 50
                """,
                params,
            ).fetchall()
            event_filters = []
            event_params: list[Any] = []
            if normalized_service:
                event_filters.append("service = ?")
                event_params.append(normalized_service)
            if before_id is not None and int(before_id) > 0:
                event_filters.append("id < ?")
                event_params.append(int(before_id))
            event_query = "SELECT * FROM events"
            if event_filters:
                event_query += " WHERE " + " AND ".join(event_filters)
            event_query += " ORDER BY id DESC LIMIT ?"
            event_params.append(bounded_limit + 1)
            event_rows = connection.execute(event_query, event_params).fetchall()

        has_more = len(event_rows) > bounded_limit
        event_rows = event_rows[:bounded_limit]

        events = []
        for row in event_rows:
            item = dict(row)
            try:
                item["query_keys"] = json.loads(item.pop("query_keys"))
            except (TypeError, ValueError):
                item["query_keys"] = []
            events.append(item)
        return {
            "enabled": self.is_enabled(),
            "retention_days": RETENTION_DAYS,
            "max_events": MAX_EVENTS,
            "summary": {
                "total_events": int(total or 0),
                "events_24h": int(counts["events_24h"] or 0),
                "unique_sources_24h": int(counts["sources_24h"] or 0),
                "probe_signals_24h": int(counts["probes_24h"] or 0),
                "last_event": counts["last_event"],
            },
            "top_sources": [dict(row) for row in top_sources],
            "services": [dict(row) for row in services],
            "events": events,
            "has_more": has_more,
            "next_cursor": int(events[-1]["id"]) if has_more and events else None,
        }


_STORE: Optional[HTTPRequestCaptureStore] = None
_STORE_LOCK = threading.Lock()


def get_http_request_capture_store() -> HTTPRequestCaptureStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = HTTPRequestCaptureStore()
        return _STORE


def _route_and_agent(request: Request, default_agent: str) -> tuple[str, str, str]:
    route = request.scope.get("route")
    route_path = getattr(route, "path", "") if route is not None else ""
    raw_path = request.url.path or "/"
    safe_path = _safe_unknown_path(raw_path)
    safe_route = _bounded_text(route_path, 512) if isinstance(route_path, str) else ""
    if safe_route and not any(marker in safe_route for marker in (":path}", ":full_path}")):
        raw_parts = safe_path.split("/")
        route_parts = safe_route.split("/")
        if len(raw_parts) == len(route_parts):
            path = "/".join(
                f"[{part[1:-1]}]" if part.startswith("{") and part.endswith("}") else raw_parts[index]
                for index, part in enumerate(route_parts)
            )[:512]
        else:
            path = safe_path
    else:
        path = safe_path
    match = _AGENT_PATH.match(raw_path)
    agent_name = default_agent
    if match:
        agent_name = match.group(1)
    if agent_name and not _SAFE_NAME.fullmatch(agent_name):
        agent_name = "unknown"
    return path, safe_route, agent_name


def safe_path_for_http_log(request: Request) -> str:
    """Return a route-safe path label for ordinary access logs."""
    path, _route, _agent_name = _route_and_agent(request, "")
    return path


def _make_event(request: Request, *, service_name: str, default_agent: str, status_code: int) -> dict[str, Any]:
    raw_path = request.url.path or "/"
    path, route, agent_name = _route_and_agent(request, default_agent)
    service = service_name
    if service_name == "admin" and raw_path.startswith("/agent/"):
        service = "website_gateway"
    if service_name == "admin" and raw_path in {"/websites", "/agent-websites", "/agent-frontends"}:
        service = "website_gateway"
        agent_name = "agent_directory"
    client = request.scope.get("client") or ("", None)
    source_ip = _bounded_text(client[0] if len(client) > 0 else "", 64)
    source_port = client[1] if len(client) > 1 and isinstance(client[1], int) and 0 <= client[1] <= 65535 else None
    server = request.scope.get("server") or ("", None)
    destination_port = server[1] if len(server) > 1 and isinstance(server[1], int) and 0 <= server[1] <= 65535 else None
    query = request.scope.get("query_string", b"")
    if isinstance(query, bytes):
        query = query.decode("latin-1", errors="replace")
    user_agent = _bounded_text(request.headers.get("user-agent", ""), 256)
    method = _bounded_text(request.method.upper(), 16)
    kind, signal = _event_kind(path, method, status_code)
    return {
        "captured_at": _utc_now(),
        "service": service,
        "agent_name": agent_name or "",
        "source_ip": source_ip,
        "source_port": source_port,
        "source_scope": _source_scope(source_ip),
        "destination_port": destination_port,
        "destination_host": _safe_host(request.headers.get("host", "")),
        "method": method,
        "path": path,
        "route": route,
        "query_keys": _safe_query_keys(query),
        "status_code": int(status_code),
        "user_agent": user_agent,
        "referer_origin": _referer_origin(request.headers.get("referer", "")),
        "http_version": _bounded_text(request.scope.get("http_version", "1.1"), 12),
        "event_kind": kind,
        "signal": signal,
    }


def install_http_request_capture(
    app: Any,
    *,
    service_name: str,
    agent_name: str = "",
    store: Optional[HTTPRequestCaptureStore] = None,
) -> None:
    """Add metadata capture to an ASGI HTTP app; disabled until an admin enables it."""
    app_state = getattr(app, "state", None)
    if app_state is None or not callable(getattr(app, "middleware", None)):
        return
    if getattr(app_state, "_autoyou_http_request_capture_installed", False):
        return
    app_state._autoyou_http_request_capture_installed = True
    capture_store = store or get_http_request_capture_store()
    normalized_service = _bounded_text(service_name, 64) or "unknown"
    normalized_agent = agent_name if _SAFE_NAME.fullmatch(agent_name or "") else ""

    @app.middleware("http")
    async def _autoyou_http_request_capture(request: Request, call_next):
        if request.scope.get("autoyou.capture_skip") or not capture_store.is_enabled():
            return await call_next(request)
        try:
            response = await call_next(request)
        except Exception:
            try:
                capture_store.record(_make_event(
                    request,
                    service_name=normalized_service,
                    default_agent=normalized_agent,
                    status_code=500,
                ))
            except Exception:
                LOGGER.warning("Failed to store HTTP request metadata", exc_info=True)
            raise
        try:
            capture_store.record(_make_event(
                request,
                service_name=normalized_service,
                default_agent=normalized_agent,
                status_code=response.status_code,
            ))
        except Exception:
            LOGGER.warning("Failed to store HTTP request metadata", exc_info=True)
        return response


def install_http_monitor_routes(
    app: Any,
    *,
    agent_name: str,
    auth_state: Callable[[Request, str], dict[str, Any]],
    json_response: Callable[..., Any],
    admin_is_logged_in: Optional[Callable[[Request], bool]] = None,
    store: Optional[HTTPRequestCaptureStore] = None,
) -> None:
    """Expose capture controls and events only to an authenticated security-agent user."""
    capture_store = store or get_http_request_capture_store()

    def authorized(request: Request) -> bool:
        auth = auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return False
        if auth.get("auth_mode") == "open":
            return bool(admin_is_logged_in and admin_is_logged_in(request))
        return True

    @app.get("/api/http-monitor")
    async def http_monitor_dashboard(
        request: Request,
        limit: int = 100,
        service: str = "",
        before_id: Optional[int] = None,
    ):
        if not authorized(request):
            return json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        payload = capture_store.dashboard(limit=limit, service=service, before_id=before_id)
        return json_response({"success": True, **payload})

    @app.post("/api/http-monitor")
    async def http_monitor_set_enabled(request: Request):
        if not authorized(request):
            return json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            payload = await request.json()
        except Exception:
            return json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
            return json_response({"success": False, "error": "enabled must be true or false."}, status_code=400)
        result = capture_store.set_enabled(payload["enabled"])
        return json_response({"success": True, **result})
