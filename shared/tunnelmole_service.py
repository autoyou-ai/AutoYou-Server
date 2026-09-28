# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-652076696120284254432061-07af2a8597a115142b7a225c

"""Direct tunnelmole process management for AutoYou pairing."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-652076696120284254432061-07af2a8597a115142b7a225c"


import asyncio
import contextlib
import errno
import glob
import http.server
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from .platform_runtime import (
    get_node_command,
    get_node_service_dir,
    get_resources_root,
    get_runtime_root,
    get_user_data_dir,
    is_compiled,
)
from .tunnelmole_downloader import download_tunnelmole
from .macos_runtime_support import find_app_bundle_resource, is_app_store_build

LOGGER = logging.getLogger("autoyou.tunnelmole")
_PUBLIC_URL_PATTERN = re.compile(
    r"https?://\S+\.tunnelmole\.net/?|https?://\S+\.tm\.autoyou\.me/?",
    re.IGNORECASE,
)

# Self-hosted mode - provisions via the self-hosted gateway at tm.autoyou.me
# by default. Override with AUTOYOU_TUNNELMOLE_REMOTE_HOST=<url> for a
# different host, or set it to "" to fall back to the public tunnelmole.net cloud.
_REMOTE_HOST_ENV = "AUTOYOU_TUNNELMOLE_REMOTE_HOST"
_DEFAULT_REMOTE_HOST = "https://tm.autoyou.me"
_REMOTE_JWT_ENV = "AUTOYOU_TUNNELMOLE_JWT"
_REMOTE_WS_ENV = "AUTOYOU_TUNNELMOLE_REMOTE_WS"
_REMOTE_TIER_ENV = "AUTOYOU_TUNNELMOLE_TIER"  # "free" (default) or "paid"
_REMOTE_TIMEOUT_SECONDS = 10.0

# When `AUTOYOU_TUNNELMOLE_LOG_URL_PLAIN` is set to a truthy value, the public
# tunnelmole URL is logged in full. Otherwise INFO-level logs redact the
# random subdomain to prevent the URL from being leaked into log aggregators.
# A paired client (or an admin API) can still retrieve the full URL via
# `TunnelmoleService.status()["public_url"]`.
_LOG_URL_PLAIN_ENV = "AUTOYOU_TUNNELMOLE_LOG_URL_PLAIN"
_NODE_PACKAGE_DIR_ENV = "AUTOYOU_TUNNELMOLE_NODE_PACKAGE_DIR"
_STORE_HELPER_UNAVAILABLE = "Tunnelmole is unavailable. Update or reinstall AutoYou from the App Store."


def is_official_tunnelmole_remote_host(value: str) -> bool:
    """Return whether a paid AutoYou worker JWT may be sent to this host."""
    candidate = value.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower() == "tm.autoyou.me"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _should_log_url_plain() -> bool:
    value = os.environ.get(_LOG_URL_PLAIN_ENV, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _redact_tunnelmole_url(url: str) -> str:
    """Redact the random subdomain of a tunnelmole URL for safe logging.

    ``https://a1b2c3d4.tunnelmole.net`` -> ``https://<redacted>.tunnelmole.net``
    ``https://a1b2c3d4.tm.autoyou.me`` -> ``https://<redacted>.tm.autoyou.me``
    """
    if not url:
        return url
    url = re.sub(
        r"(https?://)([^./\s]+)(\.tunnelmole\.net)",
        r"\1<redacted>\3",
        url,
        flags=re.IGNORECASE,
    )
    url = re.sub(
        r"(https?://)([^./\s]+)(\.tm\.autoyou\.me)",
        r"\1<redacted>\3",
        url,
        flags=re.IGNORECASE,
    )
    return url


def _urlopen_with_packaged_certs(request: Any, *, timeout: float):
    """Open HTTPS URLs with certifi when available in packaged runtimes."""
    context = None
    try:
        import ssl

        cafile_candidates = []
        try:
            import certifi

            cafile_candidates.append(Path(certifi.where()))
        except Exception:
            pass
        try:
            cafile_candidates.append(get_resources_root(__file__) / "certifi" / "cacert.pem")
        except Exception:
            pass
        try:
            anchor_path = Path(__file__).resolve()
            for parent in (anchor_path.parent, *anchor_path.parents):
                cafile_candidates.append(parent / "certifi" / "cacert.pem")
        except Exception:
            pass

        seen_cafiles = set()
        for cafile in cafile_candidates:
            normalized = str(cafile)
            if normalized in seen_cafiles:
                continue
            seen_cafiles.add(normalized)
            if cafile.is_file():
                context = ssl.create_default_context(cafile=str(cafile))
                break
    except Exception as exc:
        LOGGER.debug("Unable to prepare packaged certificate context: %s", exc)

    if context is not None:
        return urllib.request.urlopen(request, timeout=timeout, context=context)
    return urllib.request.urlopen(request, timeout=timeout)


def _looks_like_certificate_verify_failure(exc: Exception) -> bool:
    details = str(exc).lower()
    return (
        "certificate_verify_failed" in details
        or "certificate verify failed" in details
        or "unable to get local issuer certificate" in details
    )


# Set AUTOYOU_NO_DOWNLOAD_TUNNELMOLE=1 to disable automatic tmole binary download.
# Any other value (or unset) keeps auto-download enabled.
_NO_DOWNLOAD_TUNNELMOLE_ENV = "AUTOYOU_NO_DOWNLOAD_TUNNELMOLE"
_BRIDGE_REQUEST_HEADER_BLACKLIST = {
    "host",
    "connection",
    "proxy-connection",
    "content-length",
    "transfer-encoding",
    # H-2: clients must not be able to forge the bridge's trusted
    # client-IP header. Always drop any inbound copy and re-mint it
    # from the tunnelmole edge's X-Forwarded-For below.
    "x-autoyou-tunnel-client-ip",
}

# H-2 rate-limiter support: the bridge stamps this header so the
# downstream FastAPI app can key rate-limits on the *real* remote IP
# (as observed by the tunnelmole cloud edge) rather than the loopback
# address of the bridge. Only trust this header when the immediate
# peer is loopback.
_BRIDGE_CLIENT_IP_HEADER = "X-AutoYou-Tunnel-Client-IP"
_BRIDGE_RESPONSE_HEADER_BLACKLIST = {
    "connection",
    "content-length",
    "server",
    "date",
    "transfer-encoding",
}

class _NoFollowRedirects(urllib.request.HTTPRedirectHandler):
    """Hand 3xx responses back to the caller instead of following them.

    The bridge is a transparent proxy. urlopen()'s default behaviour is to
    chase the Location header itself, which for an OAuth authorize response
    means this machine fetching the identity provider's redirect target and
    returning that body - so the browser never sees the redirect and the flow
    dies. Returning None here makes urllib raise HTTPError for the 3xx, which
    the proxy already forwards verbatim, Location header included.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_BRIDGE_OPENER: Optional[urllib.request.OpenerDirector] = None


def _bridge_opener() -> urllib.request.OpenerDirector:
    global _BRIDGE_OPENER
    if _BRIDGE_OPENER is None:
        _BRIDGE_OPENER = urllib.request.build_opener(_NoFollowRedirects)
    return _BRIDGE_OPENER


_EXPECTED_BRIDGE_DISCONNECT_ERRNOS = {
    errno.EPIPE,
    errno.ECONNABORTED,
    errno.ECONNRESET,
    errno.ENOTCONN,
    getattr(errno, "ESHUTDOWN", None),
}
_EXPECTED_BRIDGE_DISCONNECT_WINERRORS = {
    10053,  # An established connection was aborted by the software in your host machine.
    10054,  # An existing connection was forcibly closed by the remote host.
    10057,  # A request to send or receive data was disallowed because the socket is not connected.
}


def _get_tunnelmole_popen_kwargs() -> Dict[str, Any]:
    """Suppress extra Windows console windows when launching tmole."""
    if os.name != "nt":
        return {}

    popen_kwargs: Dict[str, Any] = {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    if creationflags:
        popen_kwargs["creationflags"] = creationflags

    startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
    if callable(startupinfo_factory):
        startupinfo = startupinfo_factory()
        startupinfo.dwFlags = int(getattr(startupinfo, "dwFlags", 0) or 0) | int(
            getattr(subprocess, "STARTF_USESHOWWINDOW", 0) or 0
        )
        startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0) or 0)
        popen_kwargs["startupinfo"] = startupinfo

    return popen_kwargs


def _is_expected_bridge_disconnect(exc: Optional[BaseException]) -> bool:
    if exc is None:
        return False

    if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
        return True

    if not isinstance(exc, OSError):
        return False

    error_numbers = {
        getattr(exc, "errno", None),
        getattr(exc, "winerror", None),
    }
    return any(
        error_number in _EXPECTED_BRIDGE_DISCONNECT_ERRNOS
        or error_number in _EXPECTED_BRIDGE_DISCONNECT_WINERRORS
        for error_number in error_numbers
        if error_number is not None
    )


def _extract_trusted_client_ip(headers) -> str:
    """Pick a plausible end-user IP from tunnelmole edge headers.

    We look at the standard ``X-Forwarded-For`` chain first (leftmost
    entry is the original client), then ``X-Real-IP`` and
    ``CF-Connecting-IP`` as fallbacks. Any value that fails a basic
    ``ipaddress`` parse is discarded - an attacker controls the raw
    bytes here, so we must not propagate garbage into the rate-limiter.
    Returns an empty string when nothing usable is found.
    """
    try:
        import ipaddress as _ipaddress  # local import keeps module import cost low
    except Exception:  # pragma: no cover - stdlib always available
        _ipaddress = None  # type: ignore[assignment]

    def _first_valid(raw: str) -> str:
        if not raw:
            return ""
        for part in str(raw).split(","):
            candidate = part.strip()
            if not candidate:
                continue
            # XFF entries can be "IP" or "IP:port"; drop any port suffix
            # before parsing.
            if candidate.startswith("["):
                # IPv6 with optional port, e.g. "[2001:db8::1]:443"
                bracket_end = candidate.rfind("]")
                if bracket_end > 0:
                    candidate = candidate[1:bracket_end]
            elif candidate.count(":") == 1:
                # IPv4:port - strip port
                candidate = candidate.rsplit(":", 1)[0]
            if _ipaddress is None:
                return candidate
            try:
                _ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                continue
        return ""

    for header_name in ("X-Forwarded-For", "X-Real-IP", "CF-Connecting-IP"):
        value = headers.get(header_name) or ""
        chosen = _first_valid(value)
        if chosen:
            return chosen
    return ""


# Public-tunnel path routing for the bundled website mode. The pairing/
# signaling API + health stay on the auth app (:8002); when a website is bundled
# onto the same persistent URL, every other path is served by the local page
# service (:8067, the C-2-hardened surface). With no website (website_port 0)
# every path goes to the auth app - the legacy single-target behaviour. This
# never routes to :8001 admin or :8081 ADK (see network-tiers-audit F-2).
_BRIDGE_AUTH_PATH_PREFIXES = ("/auth", "/signal", "/health")


def _select_bridge_target_port(path: str, auth_port: int, website_port: int) -> int:
    if not website_port:
        return auth_port
    seg = (path or "/").split("?", 1)[0].split("#", 1)[0]
    for prefix in _BRIDGE_AUTH_PATH_PREFIXES:
        if seg == prefix or seg.startswith(prefix + "/"):
            return auth_port
    return website_port


class _TunnelmoleBridgeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        self._proxy()

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._proxy()

    def log_message(self, _format: str, *args: object) -> None:
        return

    def _proxy(self) -> None:
        auth_port = int(getattr(self.server, "target_port", 0))
        website_port = int(getattr(self.server, "website_port", 0) or 0)
        target_port = _select_bridge_target_port(self.path, auth_port, website_port)
        target_url = f"http://127.0.0.1:{target_port}{self.path}"
        if self.path.startswith("/auth") or self.path.startswith("/signal") or self.path.startswith("/health"):
            LOGGER.info("Tunnelmole bridge forwarding %s %s -> %s", self.command, self.path, target_url)

        body = b""
        content_length_header = self.headers.get("Content-Length")
        if content_length_header:
            try:
                content_length = max(0, int(content_length_header))
            except Exception:
                content_length = 0
            if content_length:
                body = self.rfile.read(content_length)

        headers = {}
        for key, value in self.headers.items():
            if key.lower() in _BRIDGE_REQUEST_HEADER_BLACKLIST:
                continue
            headers[str(key)] = str(value)

        # H-2: mint a trusted client-IP header from the tunnelmole edge's
        # forwarding headers. Any inbound copy of this header was already
        # stripped above via _BRIDGE_REQUEST_HEADER_BLACKLIST. If we can't
        # extract a usable IP we leave the header absent so the FastAPI
        # rate-limiter falls back to the (loopback) direct-peer address.
        trusted_client_ip = _extract_trusted_client_ip(self.headers)
        if trusted_client_ip:
            headers[_BRIDGE_CLIENT_IP_HEADER] = trusted_client_ip

        request = urllib.request.Request(
            target_url,
            data=body if (body or self.command not in {"GET", "HEAD"}) else None,
            headers=headers,
            method=self.command,
        )

        try:
            with _bridge_opener().open(request, timeout=30.0) as response:
                response_body = response.read()
                self._write_response(
                    status_code=int(getattr(response, "status", 200)),
                    headers=response.headers.items(),
                    body=response_body,
                )
        except urllib.error.HTTPError as exc:
            response_body = exc.read()
            self._write_response(
                status_code=int(exc.code),
                headers=exc.headers.items(),
                body=response_body,
            )
        except Exception as exc:
            error_body = json.dumps(
                {
                    "success": False,
                    "error": "Tunnelmole bridge could not reach the local auth server.",
                }
            ).encode("utf-8")
            LOGGER.warning("Tunnelmole bridge proxy failed for %s %s: %s", self.command, self.path, exc)
            self._write_response(
                status_code=502,
                headers=(("Content-Type", "application/json"),),
                body=error_body,
            )

    def _write_response(self, *, status_code: int, headers, body: bytes) -> None:
        self.send_response(int(status_code))
        for key, value in headers:
            if str(key).lower() in _BRIDGE_RESPONSE_HEADER_BLACKLIST:
                continue
            self.send_header(str(key), str(value))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)


class _TunnelmoleBridgeHTTPServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, target_port: int, website_port: int = 0):
        self.target_port = int(target_port)
        # 0 = single-target (legacy). >0 enables bundled website routing: non-auth
        # paths are served by the local page service on this port.
        self.website_port = int(website_port or 0)
        self._stopping = False
        super().__init__(server_address, _TunnelmoleBridgeHandler)

    def handle_error(self, request, client_address) -> None:
        _exc_type, exc, _traceback = sys.exc_info()
        if _is_expected_bridge_disconnect(exc):
            if self._stopping:
                LOGGER.debug(
                    "Tunnelmole bridge swallowed expected client disconnect during shutdown from %s: %s",
                    client_address,
                    exc,
                )
            else:
                LOGGER.debug(
                    "Tunnelmole bridge client disconnected before request handling completed from %s: %s",
                    client_address,
                    exc,
                )
            return
        super().handle_error(request, client_address)


class _TunnelmoleBridgeHTTPServerV6(_TunnelmoleBridgeHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        except Exception:
            pass
        super().server_bind()


def _should_auto_download_binary() -> bool:
    """Return True when the tmole binary should be fetched automatically.

    Auto-download is ON by default for all deployment modes (source, compiled,
    Docker, Pinokio).  Set AUTOYOU_NO_DOWNLOAD_TUNNELMOLE=1 to opt out, e.g.
    for air-gapped servers or strict dev environments.
    """
    raw = str(os.getenv(_NO_DOWNLOAD_TUNNELMOLE_ENV, "")).strip().lower()
    return raw not in {"1", "true", "yes", "on"}


def resolve_tunnelmole_binary() -> Optional[str]:
    if is_app_store_build():
        binary = find_app_bundle_resource("runtime/tunnelmole/tmole")
        return str(binary) if binary is not None and binary.is_file() and os.access(binary, os.X_OK) else None
    explicit = os.getenv("AUTOYOU_TUNNELMOLE_BIN") or os.getenv("TUNNELMOLE_BIN")
    if explicit and os.path.isfile(explicit):
        return explicit

    binary_name = "tmole.exe" if os.name == "nt" else "tmole"
    for candidate in (
        get_runtime_root(__file__) / "tunnelmole" / binary_name,
        get_runtime_root(__file__) / binary_name,
        get_user_data_dir("AutoYou") / "tools" / binary_name,
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    discovered = shutil.which("tmole") or shutil.which("tunnelmole")
    if discovered:
        return discovered

    for pattern in (
        "/mnt/c/Users/*/AppData/Roaming/npm/tunnelmole",
        "/mnt/c/Users/*/AppData/Roaming/npm/tmole",
    ):
        for candidate in glob.glob(pattern):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

    if _should_auto_download_binary():
        downloaded = download_tunnelmole()
        if isinstance(downloaded, Path) and downloaded.is_file():
            return str(downloaded)

    return None


def _resolve_remote_config() -> Optional[Dict[str, Any]]:
    """Return remote-provision config if AUTOYOU_TUNNELMOLE_REMOTE_HOST is set.

    When present, TunnelmoleService delegates URL allocation to the
    self-hosted apikey-bridge (`/v1/provision` or `/v1/provision/free`, both
    via an app-attested JWT) and, on success, prefers the bundled
    Node-based tunnelmole client so the websocket/http endpoints can target
    the self-hosted gateway directly.
    """
    raw_env = os.getenv(_REMOTE_HOST_ENV)
    # Explicitly empty string opts out of self-hosted → fall back to public cloud.
    if raw_env is not None and raw_env.strip() == "":
        return None
    host = (raw_env or _DEFAULT_REMOTE_HOST).strip()
    if not host:
        return None
    host = host.strip("/")
    # Accept bare hostnames or full https URLs
    if "://" in host:
        base = host.rstrip("/")
    else:
        base = f"https://{host}"
    ws = (os.getenv(_REMOTE_WS_ENV) or "").strip()
    if not ws:
        if base.startswith("https://"):
            ws = f"wss://{base[len('https://'):].rstrip('/')}/tunnel"
        elif base.startswith("http://"):
            ws = f"ws://{base[len('http://'):].rstrip('/')}/tunnel"
        else:
            ws = f"wss://{base.rstrip('/')}/tunnel"
    tier = (os.getenv(_REMOTE_TIER_ENV) or "").strip().lower()
    if tier not in {"free", "paid"}:
        tier = "paid" if os.getenv(_REMOTE_JWT_ENV) else "free"
    return {
        "base_url": base,
        "ws_endpoint": ws,
        "tier": tier,
        "jwt": (os.getenv(_REMOTE_JWT_ENV) or "").strip() or None,
    }


def _provision_from_remote(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """POST to the self-hosted apikey-bridge and return the provision payload."""
    base = cfg["base_url"].rstrip("/")
    if cfg["tier"] == "paid":
        path = "/v1/provision"
    else:
        path = "/v1/provision/free"
    url = f"{base}{path}"
    headers = {"Content-Type": "application/json"}
    if cfg.get("jwt"):
        headers["Authorization"] = f"Bearer {cfg['jwt']}"
    try:
        req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
        with _urlopen_with_packaged_certs(req, timeout=_REMOTE_TIMEOUT_SECONDS) as resp:
            body = resp.read()
            return json.loads(body.decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            detail = {"error": "http_error", "status": int(exc.code)}
        if exc.code == 402:
            LOGGER.warning(
                "Tunnelmole self-hosted provision refused (402): %s",
                detail.get("detail", detail).get("message") if isinstance(detail.get("detail"), dict) else detail,
            )
        else:
            LOGGER.warning("Tunnelmole self-hosted provision failed (%s): %s", exc.code, detail)
        return {"_error": True, "status": int(exc.code), "detail": detail}
    except Exception as exc:
        LOGGER.warning("Tunnelmole self-hosted provision error: %s", exc)
        return None


def _command_exists(command: str) -> bool:
    candidate = str(command or "").strip()
    if not candidate:
        return False

    if any(sep in candidate for sep in (os.sep, "/")):
        return Path(candidate).expanduser().is_file()

    return shutil.which(candidate) is not None


def _normalize_node_tunnelmole_package_dir(candidate_root: Path) -> Optional[Path]:
    for candidate in (candidate_root, candidate_root / "node_modules" / "tunnelmole"):
        package_dir = candidate.expanduser().resolve()
        if (
            package_dir.is_dir()
            and (package_dir / "dist" / "config.js").is_file()
            and (package_dir / "dist" / "src" / "index.js").is_file()
            and (package_dir / "dist" / "src" / "node-persist" / "storage.js").is_file()
        ):
            return package_dir
    return None


def _resolve_node_tunnelmole_runtime(anchor: str | Path = __file__) -> Optional[Dict[str, str]]:
    if is_app_store_build():
        node = find_app_bundle_resource("runtime/node/bin/node")
        package = find_app_bundle_resource("node/tunnelmole/node_modules/tunnelmole")
        if node is None or not node.is_file() or not os.access(node, os.X_OK) or package is None:
            return None
        for entry in ("dist/config.js", "dist/src/index.js", "dist/src/node-persist/storage.js"):
            resource = find_app_bundle_resource("node/tunnelmole/node_modules/tunnelmole/" + entry)
            if resource is None or not resource.is_file():
                return None
        return {"node_command": str(node), "package_dir": str(package)}

    launcher_path = Path(__file__).with_name("tunnelmole_node_launcher.mjs").resolve()
    if not launcher_path.is_file():
        try:
            from shared.tunnelmole_node_payload import materialize_launcher

            launcher_path = materialize_launcher(
                get_user_data_dir("AutoYou") / "runtime" / "tunnelmole"
            )
        except Exception as exc:
            LOGGER.warning("Unable to materialize the protected Tunnelmole launcher: %s", exc)
            return None

    candidate_roots: list[Path] = []
    env_package_dir = os.getenv(_NODE_PACKAGE_DIR_ENV, "").strip()
    if env_package_dir:
        candidate_roots.append(Path(env_package_dir))
    candidate_roots.append(get_node_service_dir("tunnelmole", anchor))

    package_dir: Optional[Path] = None
    seen_roots: set[str] = set()
    for candidate_root in candidate_roots:
        normalized = _normalize_node_tunnelmole_package_dir(candidate_root)
        if normalized is None:
            continue
        normalized_key = str(normalized)
        if normalized_key in seen_roots:
            continue
        seen_roots.add(normalized_key)
        package_dir = normalized
        break

    if package_dir is None:
        return None

    node_command = get_node_command(anchor)
    if not _command_exists(node_command):
        return None

    return {
        "node_command": node_command,
        "launcher_path": str(launcher_path),
        "package_dir": str(package_dir),
    }


def _build_self_hosted_node_launch(
    exposed_port: int,
    *,
    remote_cfg: Dict[str, Any],
    remote_provision: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    runtime = _resolve_node_tunnelmole_runtime(__file__)
    if runtime is None:
        return None

    api_key = str(remote_provision.get("api_key") or "").strip()
    ws_endpoint = str(remote_provision.get("ws_endpoint") or remote_cfg.get("ws_endpoint") or "").strip()
    http_endpoint = str(remote_cfg.get("base_url") or "").strip()
    if not api_key or not ws_endpoint or not http_endpoint:
        return None

    isolated_home = get_user_data_dir("AutoYou") / "tunnelmole-node-home"
    try:
        isolated_home.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        LOGGER.warning("Failed to prepare the self-hosted tunnelmole Node home: %s", exc)
        return None

    extra_env: Dict[str, str] = {
        "AUTOYOU_TUNNELMOLE_NODE_PACKAGE_DIR": runtime["package_dir"],
        "AUTOYOU_TUNNELMOLE_NODE_PORT": str(int(exposed_port)),
        "AUTOYOU_TUNNELMOLE_NODE_WS_ENDPOINT": ws_endpoint,
        "AUTOYOU_TUNNELMOLE_NODE_HTTP_ENDPOINT": http_endpoint,
        "AUTOYOU_TUNNELMOLE_NODE_API_KEY": api_key,
        "HOME": str(isolated_home),
        "USERPROFILE": str(isolated_home),
    }

    reserved_domain = _reserved_subdomain_fqdn(remote_provision)
    if reserved_domain:
        extra_env["AUTOYOU_TUNNELMOLE_NODE_DOMAIN"] = reserved_domain

    if is_app_store_build():
        from .tunnelmole_node_payload import launcher_bytes
        command = [runtime["node_command"], "--input-type=module", "--eval", launcher_bytes().decode("utf-8")]
    else:
        command = [runtime["node_command"], runtime["launcher_path"]]
    return {
        "command": command,
        "env": extra_env,
    }


_TMOLE_HELP_CACHE: Dict[str, str] = {}


def _tunnelmole_environment() -> Dict[str, str]:
    environment = os.environ.copy()
    if is_app_store_build():
        # Applies to both help probes and the long-running helper.
        environment.pop("NODE_OPTIONS", None)
        environment.pop("NODE_PATH", None)
    return environment


def _read_tmole_help(binary_path: str) -> str:
    cached = _TMOLE_HELP_CACHE.get(binary_path)
    if cached is not None:
        return cached

    help_text = ""
    try:
        completed = subprocess.run(
            [binary_path, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
            env=_tunnelmole_environment(),
            **_get_tunnelmole_popen_kwargs(),
        )
        help_text = "\n".join(
            part.strip()
            for part in (completed.stdout or "", completed.stderr or "")
            if str(part or "").strip()
        )
    except Exception as exc:
        LOGGER.debug("Failed to inspect tmole help for %s: %s", binary_path, exc)

    _TMOLE_HELP_CACHE[binary_path] = help_text
    return help_text


def _reserved_subdomain_fqdn(remote_provision: Optional[Dict[str, Any]]) -> str:
    if not isinstance(remote_provision, dict):
        return ""

    candidate = str(remote_provision.get("subdomain") or "").strip()
    if not candidate:
        public_url = str(remote_provision.get("public_url") or "").strip()
        if "://" in public_url:
            candidate = public_url.split("://", 1)[1]
        else:
            candidate = public_url

    candidate = candidate.strip().strip("/")
    if "/" in candidate:
        candidate = candidate.split("/", 1)[0]
    return candidate


def _tmole_supports_positional_reserved_subdomain(help_text: str) -> bool:
    lowered = str(help_text or "").strip().lower()
    return bool(lowered and "tmole <port> as <subdomain>" in lowered)


def _tmole_supports_legacy_subdomain_flag(help_text: str) -> bool:
    return "--subdomain" in str(help_text or "").strip().lower()


def _build_tmole_command(
    binary_path: str,
    exposed_port: int,
    *,
    remote_provision: Optional[Dict[str, Any]] = None,
) -> list[str]:
    command = [binary_path, str(int(exposed_port))]
    reserved_subdomain = _reserved_subdomain_fqdn(remote_provision)
    if not reserved_subdomain:
        return command

    help_text = _read_tmole_help(binary_path)
    if _tmole_supports_positional_reserved_subdomain(help_text):
        return command + ["as", reserved_subdomain]

    if _tmole_supports_legacy_subdomain_flag(help_text):
        slug = reserved_subdomain.split(".", 1)[0].strip()
        if slug:
            return command + ["--subdomain", slug]

    LOGGER.warning(
        "tmole help output did not advertise a known reserved-subdomain syntax; "
        "defaulting to positional 'as' for %s",
        reserved_subdomain,
    )
    return command + ["as", reserved_subdomain]


class TunnelmoleService:
    """Expose a local HTTP server through tunnelmole."""

    def __init__(self, local_port: int) -> None:
        self._port = int(local_port)
        self._proc: Optional[subprocess.Popen[str]] = None
        self._public_url: Optional[str] = None
        self._status: str = "stopped"
        self._stdout_drain_thread: Optional[threading.Thread] = None
        self._bridge_port: Optional[int] = None
        self._bridge_servers: list[http.server.ThreadingHTTPServer] = []
        self._bridge_threads: list[threading.Thread] = []
        self._remote_tier: Optional[str] = None
        self._upgrade_hint: Optional[Dict[str, Any]] = None
        # Bundled website mode: when >0, the persistent URL also serves the local
        # page service (website) on this port for every non-auth path. Defaults
        # off; env-overridable, and settable at publish time via set_website_port.
        try:
            self._website_port = int(os.environ.get("AUTOYOU_TUNNELMOLE_WEBSITE_PORT", "0") or 0)
        except (TypeError, ValueError):
            self._website_port = 0

    def set_port(self, local_port: int) -> None:
        self._port = int(local_port)

    def set_website_port(self, website_port: int) -> None:
        """Bundle a local website onto the same persistent tunnel URL.

        Pass the page-service port (e.g. 8067) to serve the website at `/` while
        pairing/signaling stay on the auth app; 0 disables bundling. Takes effect
        immediately for a running bridge and is kept for the next bridge start."""
        try:
            self._website_port = int(website_port or 0)
        except (TypeError, ValueError):
            self._website_port = 0
        for bridge_server in list(self._bridge_servers or []):
            with contextlib.suppress(Exception):
                bridge_server.website_port = self._website_port

    def get_status(self) -> Dict[str, Any]:
        if self._proc and self._proc.poll() is not None:
            self._proc = None
            self._public_url = None
            if self._status != "error":
                self._status = "stopped"
        payload: Dict[str, Any] = {
            "status": self._status,
            "public_url": self._public_url,
            "port": self._port,
        }
        if self._website_port:
            payload["website_port"] = self._website_port
        if self._remote_tier:
            payload["tier"] = self._remote_tier
        if self._upgrade_hint:
            payload["upgrade"] = self._upgrade_hint
        return payload

    async def start(self) -> bool:
        if self._status == "running" and self._proc and self._proc.poll() is None:
            return True

        self.stop()

        self._status = "starting"
        self._public_url = None
        self._upgrade_hint = None
        bridge_started = self._start_local_bridge()
        if not bridge_started:
            LOGGER.error("Failed to start the local tunnelmole bridge for localhost:%d", self._port)
            self._status = "error"
            return False

        exposed_port = int(self._bridge_port or self._port)

        remote_cfg = _resolve_remote_config()
        remote_provision: Optional[Dict[str, Any]] = None
        extra_env: Dict[str, str] = {}
        tmole_cmd: Optional[list[str]] = None
        if remote_cfg:
            remote_provision = await asyncio.get_running_loop().run_in_executor(
                None, _provision_from_remote, remote_cfg
            )
            if not remote_provision:
                LOGGER.warning("Self-hosted tunnelmole unreachable; falling back to public cloud.")
            elif remote_provision.get("_error"):
                detail = remote_provision.get("detail") or {}
                inner = detail.get("detail") if isinstance(detail, dict) else None
                should_fallback_to_public_cloud = (
                    os.getenv(_REMOTE_HOST_ENV) is None
                    and str(remote_cfg.get("tier") or "").strip().lower() == "free"
                    and not remote_cfg.get("jwt")
                )
                if should_fallback_to_public_cloud:
                    fallback_reason = inner if isinstance(inner, dict) else detail
                    LOGGER.warning(
                        "Self-hosted tunnelmole provision failed (%s); falling back to public cloud.",
                        fallback_reason or remote_provision.get("status") or "unknown error",
                    )
                    remote_cfg = None
                    remote_provision = None
                    self._remote_tier = None
                    self._upgrade_hint = None
                else:
                    self._upgrade_hint = inner if isinstance(inner, dict) else detail
                    self.stop()
                    self._status = "error"
                    self._remote_tier = remote_cfg["tier"]
                    return False
            else:
                self._remote_tier = remote_provision.get("tier") or remote_cfg["tier"]
                node_launch = _build_self_hosted_node_launch(
                    exposed_port,
                    remote_cfg=remote_cfg,
                    remote_provision=remote_provision,
                )
                if node_launch is None:
                    reason = (
                        "Self-hosted tunnelmole requires a Node.js runtime plus the tunnelmole npm client. "
                        "The downloaded tmole binary only targets the public tunnelmole service."
                    )
                    if is_app_store_build():
                        reason = _STORE_HELPER_UNAVAILABLE
                    should_fallback_to_public_cloud = (
                        os.getenv(_REMOTE_HOST_ENV) is None
                        and str(remote_cfg.get("tier") or "").strip().lower() == "free"
                        and not remote_cfg.get("jwt")
                    )
                    if should_fallback_to_public_cloud:
                        LOGGER.warning("%s Falling back to public cloud.", reason)
                        remote_cfg = None
                        remote_provision = None
                        self._remote_tier = None
                        self._upgrade_hint = None
                    else:
                        self._upgrade_hint = {
                            "error": "self_hosted_client_unavailable",
                            "message": reason,
                        }
                        LOGGER.error(reason)
                        self.stop()
                        self._status = "error"
                        self._remote_tier = remote_cfg["tier"]
                        return False
                else:
                    tmole_cmd = list(node_launch["command"])
                    extra_env.update(dict(node_launch["env"]))

        spawn_env = _tunnelmole_environment()
        spawn_env.pop("TUNNELMOLE_HOST", None)
        spawn_env.pop("TUNNELMOLE_API_KEY", None)
        spawn_env.pop("TUNNELMOLE_HOST_URL", None)
        spawn_env.update(extra_env)

        if tmole_cmd is None:
            tmole = resolve_tunnelmole_binary()
            if not tmole:
                if is_app_store_build():
                    self._upgrade_hint = {"error": "helper_unavailable", "message": _STORE_HELPER_UNAVAILABLE}
                LOGGER.warning(
                    "Tunnelmole binary not found. Set AUTOYOU_TUNNELMOLE_BIN or install tmole/tunnelmole on PATH."
                )
                self.stop()
                self._status = "error"
                return False

            tmole_cmd = _build_tmole_command(
                tmole,
                exposed_port,
                remote_provision=remote_provision,
            )

        try:
            self._proc = subprocess.Popen(
                tmole_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=spawn_env,
                **_get_tunnelmole_popen_kwargs(),
            )
        except Exception as exc:
            LOGGER.error("Failed to start tunnelmole: %s", exc)
            self.stop()
            self._status = "error"
            return False

        # Self-hosted path: we already know the expected public URL from provisioning,
        # but the tmole process still has to prove it can establish the tunnel.
        if remote_provision and remote_provision.get("public_url"):
            self._start_stdout_drain_thread()
            expected_public_url = str(remote_provision["public_url"]).rstrip("/")

            loop = asyncio.get_running_loop()
            try:
                is_publicly_reachable = await asyncio.wait_for(
                    loop.run_in_executor(None, self._probe_public_health, expected_public_url),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                is_publicly_reachable = False

            if not is_publicly_reachable:
                LOGGER.warning(
                    "Self-hosted tunnelmole did not validate %s before startup completed",
                    _redact_tunnelmole_url(expected_public_url),
                )
                self.stop()
                self._status = "error"
                return False

            self._public_url = expected_public_url
            self._status = "running"
            display_url = (
                self._public_url
                if _should_log_url_plain()
                else re.sub(r"(https?://)([^./\s]+)\.", r"\1<redacted>.", self._public_url, count=1)
            )
            LOGGER.info(
                "Tunnelmole (self-hosted, tier=%s) running: %s -> localhost:%d (bridge localhost:%d)",
                self._remote_tier,
                display_url,
                self._port,
                exposed_port,
            )
            return True

        loop = asyncio.get_running_loop()
        try:
            url = await asyncio.wait_for(
                loop.run_in_executor(None, self._read_url_from_proc),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            LOGGER.warning("Timed out waiting for tunnelmole public URL on localhost:%d", exposed_port)
            self.stop()
            self._status = "error"
            return False

        if not url:
            self.stop()
            self._status = "error"
            return False

        loop = asyncio.get_running_loop()
        try:
            is_publicly_reachable = await asyncio.wait_for(
                loop.run_in_executor(None, self._probe_public_health, url.rstrip("/")),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            is_publicly_reachable = False

        if not is_publicly_reachable:
            LOGGER.warning(
                "Tunnelmole public health probe failed for %s",
                _redact_tunnelmole_url(url.rstrip("/")),
            )
            self.stop()
            self._status = "error"
            return False

        self._start_stdout_drain_thread()
        self._public_url = url.rstrip("/")
        self._status = "running"
        display_url = self._public_url if _should_log_url_plain() else _redact_tunnelmole_url(self._public_url)
        LOGGER.info(
            "Tunnelmole running: %s -> localhost:%d (bridge localhost:%d)",
            display_url,
            self._port,
            exposed_port,
        )
        return True

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        self._remote_tier = None

        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass

        self._join_stdout_drain_thread()
        self._stop_local_bridge()
        self._status = "stopped"
        self._public_url = None

    def _read_url_from_proc(self) -> Optional[str]:
        proc = self._proc
        if not proc or not proc.stdout:
            return None

        for line in proc.stdout:
            stripped = self._log_stdout_line(line)
            match = _PUBLIC_URL_PATTERN.search(stripped)
            if match:
                return match.group(0)
            if proc.poll() is not None:
                break
        return None

    def _start_stdout_drain_thread(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return

        existing_thread = self._stdout_drain_thread
        if existing_thread and existing_thread.is_alive():
            return

        drain_thread = threading.Thread(
            target=self._drain_remaining_stdout,
            args=(proc,),
            name=f"autoyou-tunnelmole-stdout-{self._port}",
            daemon=True,
        )
        self._stdout_drain_thread = drain_thread
        drain_thread.start()

    def _join_stdout_drain_thread(self, timeout: float = 1.0) -> None:
        drain_thread = self._stdout_drain_thread
        self._stdout_drain_thread = None
        if not drain_thread or not drain_thread.is_alive() or drain_thread is threading.current_thread():
            return
        try:
            drain_thread.join(timeout=timeout)
        except Exception:
            pass

    def _drain_remaining_stdout(self, proc: subprocess.Popen[str]) -> None:
        if not proc.stdout:
            return
        try:
            for line in proc.stdout:
                self._log_stdout_line(line)
        except Exception as exc:
            if proc.poll() is None:
                LOGGER.debug("Tunnelmole stdout drain stopped unexpectedly: %s", exc)

    def _log_stdout_line(self, line: str) -> str:
        stripped = line.strip()
        if stripped:
            if _should_log_url_plain():
                log_line = stripped
            else:
                # The `tmole` CLI announces the public URL on stdout; redact it
                # so the random subdomain does not leak into rotating logs.
                log_line = _PUBLIC_URL_PATTERN.sub(
                    lambda m: _redact_tunnelmole_url(m.group(0)),
                    stripped,
                )
            LOGGER.info("[tunnelmole] %s", log_line)
        return stripped

    def _start_local_bridge(self) -> bool:
        if self._bridge_servers:
            return True

        last_error: Optional[Exception] = None
        for _ in range(10):
            bridge_port = self._pick_bridge_port()
            if bridge_port is None:
                break

            servers: list[http.server.ThreadingHTTPServer] = []
            threads: list[threading.Thread] = []
            try:
                ipv4_server = _TunnelmoleBridgeHTTPServer(
                    ("127.0.0.1", bridge_port), target_port=self._port, website_port=self._website_port)
                servers.append(ipv4_server)

                try:
                    ipv6_server = _TunnelmoleBridgeHTTPServerV6(
                        ("::1", bridge_port), target_port=self._port, website_port=self._website_port)
                    servers.append(ipv6_server)
                except OSError as exc:
                    LOGGER.debug("Tunnelmole IPv6 bridge unavailable on port %d: %s", bridge_port, exc)

                for index, server in enumerate(servers):
                    thread = threading.Thread(
                        target=server.serve_forever,
                        name=f"autoyou-tunnelmole-bridge-{bridge_port}-{index}",
                        daemon=True,
                    )
                    thread.start()
                    threads.append(thread)

                self._bridge_port = int(bridge_port)
                self._bridge_servers = servers
                self._bridge_threads = threads
                LOGGER.info(
                    "Tunnelmole local bridge listening on localhost:%d -> 127.0.0.1:%d",
                    bridge_port,
                    self._port,
                )
                return True
            except OSError as exc:
                last_error = exc
                for server in servers:
                    try:
                        server.server_close()
                    except Exception:
                        pass
                continue

        if last_error is not None:
            LOGGER.warning("Unable to start tunnelmole local bridge: %s", last_error)
        return False

    def _stop_local_bridge(self) -> None:
        servers = self._bridge_servers
        threads = self._bridge_threads
        self._bridge_servers = []
        self._bridge_threads = []
        self._bridge_port = None

        for server in servers:
            server._stopping = True
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass

        for thread in threads:
            if not thread.is_alive() or thread is threading.current_thread():
                continue
            try:
                thread.join(timeout=1.0)
            except Exception:
                pass

    def _pick_bridge_port(self) -> Optional[int]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                return int(sock.getsockname()[1])
        except Exception as exc:
            LOGGER.debug("Failed to reserve a local tunnelmole bridge port: %s", exc)
            return None

    def _probe_public_health(self, public_url: str) -> bool:
        health_url = f"{public_url.rstrip('/')}/health"
        last_error: Optional[Exception] = None
        display_health_url = (
            health_url
            if _should_log_url_plain()
            else _redact_tunnelmole_url(health_url)
        )
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            proc = self._proc
            if proc and proc.poll() is not None:
                LOGGER.warning(
                    "Tunnelmole process exited before the public health probe succeeded for %s",
                    display_health_url,
                )
                break
            try:
                with _urlopen_with_packaged_certs(health_url, timeout=5.0) as response:
                    status_code = int(getattr(response, "status", 0))
                    if status_code == 200:
                        return True
            except Exception as exc:
                last_error = exc
                if _looks_like_certificate_verify_failure(exc):
                    try:
                        import ssl

                        with urllib.request.urlopen(
                            health_url,
                            timeout=5.0,
                            context=ssl._create_unverified_context(),
                        ) as response:
                            status_code = int(getattr(response, "status", 0))
                            if status_code == 200:
                                LOGGER.warning(
                                    "Tunnelmole public health probe used certificate fallback for %s",
                                    display_health_url,
                                )
                                return True
                    except Exception as fallback_exc:
                        last_error = fallback_exc
                time.sleep(1.0)
        if last_error is not None:
            LOGGER.warning(
                "Tunnelmole public health probe failed for %s: %s",
                display_health_url,
                last_error,
            )
        return False
