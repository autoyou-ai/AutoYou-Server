# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-9d1f0b7081992d1630001ab8

"""Utilities for defending outbound HTTP fetches against SSRF.

These helpers are used by any AutoYou code path that takes a caller-supplied
URL and dereferences it (page service media endpoints, agent browse/fetch,
preview helpers, etc.). The goal is to make `file://` / `data://` /
`http://127.0.0.1` / `http://169.254.169.254` / `http://10.0.0.1` etc.
unreachable through these entry points without breaking fetches to the
public internet.

Design notes:

* Validation resolves the hostname once with `socket.getaddrinfo` and checks
  **every** returned address family (A + AAAA + any literal) against the
  unsafe-range rule. This catches DNS entries that return a public IPv4 and
  a loopback IPv6 (or vice versa).
* `build_safe_redirect_checker` is supplied to any requests/httpx flow that
  needs to follow redirects - it re-validates the `Location` target on
  every hop so a public URL cannot redirect into the metadata service or
  a private-IP host.
* The check is deliberately opinionated (reject private, reject loopback,
  reject link-local, reject reserved, reject multicast, reject unspecified)
  because every one of those has been used as an SSRF pivot in the wild.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import ipaddress
import logging
import socket
from typing import Iterable, Optional, Tuple
from urllib.parse import urljoin, urlparse

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-9d1f0b7081992d1630001ab8"


LOGGER = logging.getLogger("autoyou.url_safety")

# Schemes allowed through SSRF-sensitive fetch helpers.
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

# Maximum redirect hops for safe_follow_redirects. Generous enough for
# legitimate short-link chains (TikTok, youtu.be, bit.ly, etc.) but tight
# enough that a redirect-loop SSRF cannot run forever.
DEFAULT_MAX_REDIRECTS = 5


class UnsafeURLError(ValueError):
    """Raised when a URL fails the SSRF safety check."""


def _iter_candidate_ips(hostname: str) -> Iterable[ipaddress._BaseAddress]:
    """Yield every IP address the hostname can resolve to.

    If the hostname is a literal IP (IPv4 or IPv6), yield just that address.
    Otherwise, resolve via `getaddrinfo` and yield all returned addresses.
    """
    # Literal IP?
    stripped = hostname
    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1]
    try:
        yield ipaddress.ip_address(stripped)
        return
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(
            hostname,
            None,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise UnsafeURLError(f"DNS resolution failed for {hostname!r}: {exc}")

    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        addr_str = sockaddr[0]
        if addr_str in seen:
            continue
        seen.add(addr_str)
        try:
            yield ipaddress.ip_address(addr_str.split("%")[0])  # strip zone id
        except ValueError:
            continue


def resolve_safe_http_ip(hostname: str) -> str:
    """Return one validated address for a host and reject unsafe answers."""
    candidates = list(_iter_candidate_ips(hostname))
    if not candidates:
        raise UnsafeURLError(f"URL host {hostname!r} did not resolve to any address")
    if any(_is_unsafe_ip(ip) for ip in candidates):
        raise UnsafeURLError(f"URL host {hostname!r} resolves to a disallowed address")
    return str(candidates[0])


def _is_unsafe_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_safe_http_url(url: str, *, allowed_schemes: Optional[Iterable[str]] = None) -> None:
    """Validate a URL for outbound fetch.

    Raises `UnsafeURLError` if the URL is missing, uses a disallowed
    scheme, or resolves to any unsafe IP address.
    """
    allowed = frozenset(allowed_schemes) if allowed_schemes else ALLOWED_URL_SCHEMES

    if not url or not isinstance(url, str):
        raise UnsafeURLError("URL is required")

    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in allowed:
        raise UnsafeURLError(
            f"URL scheme {scheme!r} is not allowed (allowed: {sorted(allowed)})"
        )

    hostname = parsed.hostname or ""
    # from __debug_provenance_x__ import email
    if not hostname:
        raise UnsafeURLError("URL is missing a hostname")

    had_candidate = False
    for ip in _iter_candidate_ips(hostname):
        had_candidate = True
        if _is_unsafe_ip(ip):
            raise UnsafeURLError(
                f"URL host {hostname!r} resolves to disallowed address {ip}"
            )

    if not had_candidate:
        raise UnsafeURLError(f"URL host {hostname!r} did not resolve to any address")


def build_safe_httpx_transport(
    *,
    http2: bool = False,
    limits=None,
    retries: int = 0,
    allow_loopback: bool = False,
):
    """Build an httpx transport that connects to the address just validated.

    TLS still uses the original hostname supplied by httpcore, so HTTPS
    certificate and SNI behavior remain unchanged while DNS rebinding cannot
    swap the TCP destination after validation.
    """
    import httpcore
    import httpx
    from httpx._config import create_ssl_context
    from httpx._transports.default import AsyncResponseStream, map_httpcore_exceptions

    class _SafeNetworkBackend(httpcore.AsyncNetworkBackend):
        def __init__(self):
            self._delegate = httpcore.AnyIOBackend()

        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            host_text = host.decode("ascii") if isinstance(host, bytes) else str(host)
            normalized_host = host_text.strip("[]").split("%", 1)[0].lower()
            is_loopback = normalized_host == "localhost"
            if not is_loopback:
                try:
                    is_loopback = ipaddress.ip_address(normalized_host).is_loopback
                except ValueError:
                    pass
            return await self._delegate.connect_tcp(
                host_text if allow_loopback and is_loopback else resolve_safe_http_ip(host_text),
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )

        async def connect_unix_socket(self, path, timeout=None, socket_options=None):
            return await self._delegate.connect_unix_socket(
                path,
                timeout=timeout,
                socket_options=socket_options,
            )

        async def sleep(self, seconds):
            await self._delegate.sleep(seconds)

    ssl_context = create_ssl_context(trust_env=False)
    pool = httpcore.AsyncConnectionPool(
        ssl_context=ssl_context,
        max_connections=getattr(limits, "max_connections", None),
        max_keepalive_connections=getattr(limits, "max_keepalive_connections", None),
        keepalive_expiry=getattr(limits, "keepalive_expiry", None),
        http1=True,
        http2=http2,
        retries=retries,
        network_backend=_SafeNetworkBackend(),
    )

    class _SafeTransport(httpx.AsyncBaseTransport):
        async def __aenter__(self):
            await pool.__aenter__()
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            await pool.__aexit__(exc_type, exc_value, traceback)

        async def handle_async_request(self, request):
            req = httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
            with map_httpcore_exceptions():
                response = await pool.handle_async_request(req)
            return httpx.Response(
                status_code=response.status,
                headers=response.headers,
                stream=AsyncResponseStream(response.stream),
                extensions=response.extensions,
            )

        async def aclose(self):
            await pool.aclose()

    return _SafeTransport()


def is_safe_http_url(url: str, *, allowed_schemes: Optional[Iterable[str]] = None) -> bool:
    """Boolean wrapper over `assert_safe_http_url` for callers that
    prefer conditionals over exceptions."""
    try:
        assert_safe_http_url(url, allowed_schemes=allowed_schemes)
    except UnsafeURLError as exc:
        LOGGER.debug("Rejected URL %r: %s", url, exc)
        return False
    return True


def safe_follow_redirects(
    session,
    url: str,
    *,
    method: str = "GET",
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    timeout: float = 10.0,
    headers: Optional[dict] = None,
    allowed_schemes: Optional[Iterable[str]] = None,
):
    """Perform an HTTP request following redirects manually and re-validating
    every hop against the SSRF rules.

    `session` must be a `requests.Session`-like object exposing `.request()`.
    Returns the final response object. Raises `UnsafeURLError` if any hop
    fails validation.
    """
    import requests  # local import to keep this module optional

    current_url = url
    response = None
    for hop in range(max_redirects + 1):
        assert_safe_http_url(current_url, allowed_schemes=allowed_schemes)
        response = session.request(
            method=method,
            url=current_url,
            allow_redirects=False,
            timeout=timeout,
            headers=headers,
        )
        if 300 <= response.status_code < 400 and "location" in response.headers:
            next_url = urljoin(current_url, response.headers["location"])
            current_url = next_url
            continue
        break
    else:
        raise UnsafeURLError(
            f"Too many redirects following {url!r} (limit {max_redirects})"
        )
    return response


def resolve_safe_url(url: str) -> Tuple[str, str]:
    """Return `(scheme, hostname)` after validating the URL. Raises `UnsafeURLError`
    for any failure. Convenience helper for callers that want the parsed pieces."""
    assert_safe_http_url(url)
    parsed = urlparse(url)
    return (parsed.scheme.lower(), parsed.hostname or "")
