# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-d7bed42470b3909696291634

"""Canonical proxy-target policy for the DataChannel HTTP/WS bridge.

Both the full AutoYou server and AutoYou Lite accept a caller-supplied URL over
the WebRTC DataChannel and dereference it on the host. That makes the function
which decides *what a remote peer is allowed to reach* the most
security-critical code in either product, so it lives here once instead of
being reimplemented per server.

The policy has two halves, and a target must clear whichever one applies:

* **Loopback targets** are constrained to an explicit port allowlist supplied
  by the caller (registered agent frontends, the page service, advertised
  websites, ...). Loopback is where the sensitive unauthenticated surfaces
  live - the ADK agent API, the admin app, model runtimes - so reaching an
  unregistered loopback port is refused.
* **Non-loopback targets** are validated with :mod:`shared.url_safety`, which
  rejects private, link-local, reserved, multicast and unspecified ranges.
  This is what keeps the bridge from being turned into an SSRF gadget against
  cloud instance metadata (``169.254.169.254``) or the host's own LAN.

Loopback detection here is deliberately broader than an equality test against
three literals. All of ``127.0.0.0/8`` is loopback on Linux and macOS, and a
hostname under attacker control can resolve to a loopback address, so both are
treated as loopback rather than being waved through as "some remote host".
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import ipaddress
import logging
import os
import socket
from typing import Iterable, Optional, Set
from urllib.parse import urlsplit, urlunsplit

from shared.url_safety import UnsafeURLError, assert_safe_http_url

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-d7bed42470b3909696291634"


LOGGER = logging.getLogger("autoyou.proxy_target_policy")

#: Opt-out for the loopback port allowlist.
#:
#: This is deliberately a dedicated variable. It must never be conflated with a
#: data-directory or test-isolation flag: a variable whose documented purpose is
#: "redirect where files are written" silently disabling a network security
#: boundary is exactly the kind of dual-purpose control that gets switched on by
#: a harness and never switched back off.
ALLOW_ANY_LOOPBACK_PORT_ENV = "AUTOYOU_ALLOW_ANY_LOOPBACK_PORT"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
# from __debug_provenance_t__ import address

_WS_TO_HTTP_SCHEME = {"ws": "http", "wss": "https"}

#: Schemes the DataChannel bridge will proxy at all.
PROXYABLE_SCHEMES = frozenset({"http", "https", "ws", "wss"})

_warned_about_disabled_policy = False


class ProxyTargetBlocked(ValueError):
    """Raised when a proxy target is refused by policy.

    Subclasses :class:`ValueError` so callers that already funnel policy
    rejections through ``except ValueError`` keep working unchanged.
    """


def loopback_port_policy_disabled() -> bool:
    """Return True when the operator has explicitly disabled the port allowlist.

    Logs a warning the first time it answers True so that a bypassed security
    boundary is never silent in the logs.
    """
    global _warned_about_disabled_policy

    raw = str(os.environ.get(ALLOW_ANY_LOOPBACK_PORT_ENV, "") or "").strip().lower()
    disabled = raw in _TRUTHY
    if disabled and not _warned_about_disabled_policy:
        _warned_about_disabled_policy = True
        LOGGER.warning(
            "SECURITY: loopback proxy port allowlist is DISABLED via %s=%s. Any paired "
            "client can now reach any loopback port on this host, including "
            "unauthenticated local services. Unset it unless you are debugging.",
            ALLOW_ANY_LOOPBACK_PORT_ENV,
            raw,
        )
    return disabled


def default_port_for_scheme(scheme: str) -> int:
    """Return the implicit port for a proxyable scheme."""
    return 443 if str(scheme or "").lower() in {"https", "wss"} else 80


def _is_loopback_ip(raw_ip: str) -> bool:
    try:
        return ipaddress.ip_address(str(raw_ip).split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def is_loopback_hostname(hostname: Optional[str], *, resolve_dns: bool = True) -> bool:
    """Return True when ``hostname`` designates this host's loopback interface.

    Covers three cases that a naive membership test against
    ``{"127.0.0.1", "localhost", "::1"}`` misses:

    * every address in ``127.0.0.0/8`` (all loopback on Linux/macOS),
    * IPv6 loopback in bracketed or zone-suffixed form,
    * hostnames that *resolve* to a loopback address, which is how an attacker
      supplies a loopback target without writing a loopback literal.
    """
    normalized = str(hostname or "").strip().strip("[]").lower()
    if not normalized:
        return False
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    if _is_loopback_ip(normalized):
        return True

    if not resolve_dns:
        return False

    try:
        for info in socket.getaddrinfo(normalized, None):
            if _is_loopback_ip(info[4][0]):
                return True
    except Exception:
        # Unresolvable hosts are not loopback. They still have to clear the
        # SSRF check in assert_proxy_target_allowed, which resolves again and
        # refuses anything that will not answer from a public address.
        return False
    return False


def normalize_allowed_ports(ports: Optional[Iterable[object]]) -> Set[int]:
    """Coerce a caller-supplied port collection into a set of ints."""
    allowed: Set[int] = set()
    for port in ports or ():
        if port in (None, ""):
            continue
        try:
            allowed.add(int(port))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return allowed


def enforce_loopback_port_policy(
    url: str,
    allowed_ports: Optional[Iterable[object]],
    *,
    context: str = "loopback proxy",
) -> None:
    """Raise :class:`ProxyTargetBlocked` if ``url`` targets an unlisted loopback port.

    No-ops for non-loopback URLs; use :func:`assert_proxy_target_allowed` to get
    the SSRF half of the policy as well.
    """
    parsed = urlsplit(str(url or "").strip())
    if not is_loopback_hostname(parsed.hostname):
        return

    port = parsed.port
    if port is None:
        port = default_port_for_scheme(parsed.scheme)
    port = int(port)

    allowed = normalize_allowed_ports(allowed_ports)
    if port in allowed:
        return

    if loopback_port_policy_disabled():
        LOGGER.warning(
            "Allowing %s to unregistered loopback port %s because %s is set.",
            context,
            port,
            ALLOW_ANY_LOOPBACK_PORT_ENV,
        )
        return

    raise ProxyTargetBlocked(
        f"Loopback proxy connection to port {port} is blocked by safety policy. "
        f"Only registered frontend and configured browser ports are allowed."
    )


def assert_proxy_target_allowed(
    url: str,
    *,
    allowed_ports: Optional[Iterable[object]] = None,
    websocket: bool = False,
    context: str = "datachannel proxy",
) -> None:
    """Validate a DataChannel proxy target against the full policy.

    Loopback targets must hit an allowlisted port; every other target must pass
    the SSRF check. Raises :class:`ProxyTargetBlocked` (a ``ValueError``) or
    :class:`shared.url_safety.UnsafeURLError`.
    """
    raw = str(url or "").strip()
    if not raw:
        raise ProxyTargetBlocked("Proxy target URL is required")

    parsed = urlsplit(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in PROXYABLE_SCHEMES:
        raise ProxyTargetBlocked(f"Proxy target scheme {scheme!r} is not allowed")

    expected = {"ws", "wss"} if websocket else {"http", "https"}
    if scheme not in expected:
        raise ProxyTargetBlocked(
            f"Proxy target scheme {scheme!r} is not valid for a "
            f"{'websocket' if websocket else 'http'} request"
        )

    if not parsed.hostname:
        raise ProxyTargetBlocked("Proxy target is missing a hostname")

    if is_loopback_hostname(parsed.hostname):
        enforce_loopback_port_policy(raw, allowed_ports, context=context)
        return

    # Non-loopback: full SSRF validation. ws/wss are validated under their
    # http/https equivalents because the address rules are identical.
    validation_scheme = _WS_TO_HTTP_SCHEME.get(scheme, scheme)
    assert_safe_http_url(
        urlunsplit((validation_scheme, parsed.netloc, parsed.path, parsed.query, ""))
    )


def is_proxy_target_allowed(
    url: str,
    *,
    allowed_ports: Optional[Iterable[object]] = None,
    websocket: bool = False,
) -> bool:
    """Boolean convenience wrapper around :func:`assert_proxy_target_allowed`."""
    try:
        assert_proxy_target_allowed(
            url, allowed_ports=allowed_ports, websocket=websocket
        )
    except (ProxyTargetBlocked, UnsafeURLError):
        return False
    return True


__all__ = [
    "ALLOW_ANY_LOOPBACK_PORT_ENV",
    "PROXYABLE_SCHEMES",
    "ProxyTargetBlocked",
    "UnsafeURLError",
    "assert_proxy_target_allowed",
    "default_port_for_scheme",
    "enforce_loopback_port_policy",
    "is_loopback_hostname",
    "is_proxy_target_allowed",
    "loopback_port_policy_disabled",
    "normalize_allowed_ports",
]
