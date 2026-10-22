# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Host checks for links: match a domain exactly or as a parent domain.

Substring checks such as ``"x.com" in url`` also match ``netflix.com`` or
``https://example.test/?youtube.com``; these helpers compare parsed hostnames.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from urllib.parse import urlsplit

SOCIAL_PLATFORM_DOMAINS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("youtube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    ("tiktok", ("tiktok.com",)),
    ("threads", ("threads.net", "threads.com")),
    ("instagram", ("instagram.com",)),
    ("twitter", ("twitter.com", "x.com")),
)


def url_hostname(url: str) -> str:
    """Return the lowercase hostname of a link, accepting scheme-less input."""
    text = str(url or "").strip()
    if not text:
        return ""
    if "://" not in text and not text.startswith("//"):
        text = f"https://{text}"
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".").lower()


def host_matches(host: str, *domains: str) -> bool:
    """True when ``host`` is one of ``domains`` or a subdomain of one."""
    host = str(host or "").rstrip(".").lower()
    return bool(host) and any(host == domain or host.endswith(f".{domain}") for domain in domains)


def url_matches(url: str, *domains: str) -> bool:
    return host_matches(url_hostname(url), *domains)


def social_platform(url: str) -> str:
    """Return the social platform a link belongs to, or ``""``."""
    host = url_hostname(url)
    for platform, domains in SOCIAL_PLATFORM_DOMAINS:
        if host_matches(host, *domains):
            return platform
    return ""
