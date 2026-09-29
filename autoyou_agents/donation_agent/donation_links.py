# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-daf1c487e6257fda6c0e3f72

"""Operator-published donation links for the Donation Agent website.

The operator edits ``config/donations.json`` (or points
``AUTOYOU_DONATIONS_CONFIG_PATH`` at another file) after creating their own
self-custody wallets. This module loads that file, sanitizes every field, and
returns only public, render-safe data:

- crypto entries publish the operator's own receive addresses (display + copy
  only; the agent never generates wallets or executes payments)
- provider entries publish official HTTPS donation routes
- social entries publish official community profiles

Nothing here talks to account-service or any cloud endpoint; the donation page
stays static-friendly so it can be cached and served over poor networks.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from shared.platform_runtime import get_resources_root

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-daf1c487e6257fda6c0e3f72"


_DEFAULT_CONFIG_PATH = Path("config") / "donations.json"

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"path": "", "mtime": None, "loaded_at": 0.0, "links": None}
_CACHE_TTL_SECONDS = 30.0

# Conservative superset of base58 / bech32 / 0x-hex address alphabets.
_ADDRESS_RE = re.compile(r"^[A-Za-z0-9]{8,128}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

_DEFAULT_CRYPTO_TEMPLATE: List[Dict[str, Any]] = [
    {
        "id": "btc",
        "name": "Bitcoin",
        "network": "Bitcoin Network",
        "symbol": "BTC",
        "address": "",
        "tokens": ["BTC"],
        "accent": "#f7931a",
    },
    {
        "id": "eth-ethereum",
        "name": "Ethereum",
        "network": "Ethereum Network",
        "symbol": "ETH",
        "address": "",
        "tokens": ["ETH"],
        "accent": "#8b5cf6",
    },
    {
        "id": "usdt-ethereum",
        "name": "Tether (USDT) on Ethereum",
        "network": "Ethereum Network",
        "symbol": "USDT",
        "address": "",
        "tokens": ["USDT"],
        "accent": "#26a17b",
    },
    {
        "id": "usdt-tron",
        "name": "Tether (USDT) on Tron",
        "network": "Tron Network",
        "symbol": "USDT",
        "address": "",
        "tokens": ["USDT"],
        "accent": "#ff5b5b",
    },
    {
        "id": "usdt-solana",
        "name": "Tether (USDT) on Solana",
        "network": "Solana Network",
        "symbol": "USDT",
        "address": "",
        "tokens": ["USDT"],
        "accent": "#14f195",
    },
    {
        "id": "usdc-ethereum",
        "name": "USDC (Ethereum)",
        "network": "Ethereum Network",
        "symbol": "USDC",
        "address": "",
        "tokens": ["USDC"],
        "accent": "#2775ca",
    },
    {
        "id": "usdc-bnb",
        "name": "USDC (BNB Chain)",
        "network": "BNB Smart Chain",
        "symbol": "USDC",
        "address": "",
        "tokens": ["USDC"],
        "accent": "#f0b90b",
    },
    {
        "id": "xrp",
        "name": "Ripple (XRP)",
        "network": "XRP Ledger",
        "symbol": "XRP",
        "address": "",
        "tokens": ["XRP"],
        # Ripple's primary brand colour is near-black (#23292f), which is
        # invisible against the dark donation theme - the card reads as
        # unconfigured next to every other route. Use the XRP blue instead.
        "accent": "#00AAE4",
    },
    {
        "id": "doge",
        "name": "Dogecoin",
        "network": "Dogecoin Network",
        "symbol": "DOGE",
        "address": "",
        "tokens": ["DOGE"],
        "accent": "#c2a633",
    },
    {
        "id": "hyperliquid",
        "name": "Hyperliquid",
        "network": "Hyperliquid",
        "symbol": "HYPE",
        "address": "",
        "tokens": ["HYPE"],
        "accent": "#d7ff5f",
    },
    {
        "id": "eth-bnb",
        "name": "ETH (BNB Chain)",
        "network": "BNB Smart Chain",
        "symbol": "ETH",
        "address": "",
        "tokens": ["ETH"],
        "accent": "#f0b90b",
    },
]

_DEFAULT_PROVIDERS: List[Dict[str, Any]] = [
    {
        "id": "buymeacoffee",
        "label": "Buy Me a Coffee",
        "url": "https://buymeacoffee.com/autoyou",
        "description": "One-time or monthly support with a card.",
    },
]

_DEFAULT_SOCIALS: List[Dict[str, Any]] = [
    {
        "id": "github",
        "label": "GitHub",
        "url": "https://github.com/autoyou-ai",
        "handle": "autoyou-ai",
    },
]


def _config_path() -> Path:
    override = str(os.getenv("AUTOYOU_DONATIONS_CONFIG_PATH", "") or "").strip()
    if override:
        return Path(override)
    packaged_path = get_resources_root(__file__) / _DEFAULT_CONFIG_PATH
    if packaged_path.is_file():
        return packaged_path
    return Path(__file__).resolve().parents[2] / _DEFAULT_CONFIG_PATH


def _is_local_development_host(hostname: Optional[str]) -> bool:
    host = str(hostname or "").strip().lower().strip("[]")
    return host in _LOCAL_HOSTS or host.endswith(".localhost")


def sanitize_public_link_url(value: Any) -> str:
    """Return a safe public link URL or empty string.

    Allows HTTPS everywhere and HTTP only for loopback development hosts.
    Rejects userinfo, embedded whitespace, and non-HTTP(S) schemes. Unlike the
    Funding OS base-URL sanitizer, this keeps path and query components so
    provider referral routes keep working.
    """
    text = str(value or "").strip()
    if not text or any(ch.isspace() for ch in text) or len(text) > 512:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        return ""
    if parsed.username or parsed.password:
        return ""
    try:
        hostname = parsed.hostname
    except ValueError:
        return ""
    if parsed.scheme == "http" and not _is_local_development_host(hostname):
        return ""
    return text


def sanitize_crypto_address(value: Any) -> str:
    """Return the address if it looks like a plausible public receive address."""
    text = str(value or "").strip()
    if not _ADDRESS_RE.match(text):
        return ""
    return text


def _sanitize_id(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower()
    if _ID_RE.match(text):
        return text
    return fallback


def _sanitize_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _sanitize_accent(value: Any) -> str:
    text = str(value or "").strip()
    if re.match(r"^#[0-9a-fA-F]{3,8}$", text):
        return text
    return ""


def _canonical_crypto_name(symbol: str, network: str) -> str | None:
    if symbol == "XRP":
        return "Ripple (XRP)"
    if symbol != "USDT":
        return None
    display_network = network[:-8] if network.lower().endswith(" network") else network
    return f"Tether (USDT) on {display_network}" if display_network else "Tether (USDT)"


def _sanitize_crypto_entry(raw: Any, index: int) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    address = sanitize_crypto_address(raw.get("address"))
    symbol = _sanitize_text(raw.get("symbol"), 12).upper()
    network = _sanitize_text(raw.get("network"), 64)
    name = _canonical_crypto_name(symbol, network) or _sanitize_text(raw.get("name"), 48) or "Crypto"
    entry = {
        "id": _sanitize_id(raw.get("id"), f"network-{index}"),
        "name": name,
        "network": network,
        "symbol": symbol,
        "address": address,
        "tokens": [
            _sanitize_text(token, 12).upper()
            for token in (raw.get("tokens") or [])
            if _sanitize_text(token, 12)
        ][:24],
        "accent": _sanitize_accent(raw.get("accent")),
        "configured": bool(address),
    }
    memo = _sanitize_text(raw.get("memo"), 80)
    note = _sanitize_text(raw.get("note"), 180)
    deposit_url = sanitize_public_link_url(raw.get("deposit_url"))
    if symbol == "XRP" and memo:
        entry["memo"] = memo
    if note:
        entry["note"] = note
    if deposit_url:
        entry["deposit_url"] = deposit_url
    return entry


def _sanitize_link_entry(raw: Any, index: int, *, kind: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    url = sanitize_public_link_url(raw.get("url"))
    if not url:
        return None
    entry = {
        "id": _sanitize_id(raw.get("id"), f"{kind}-{index}"),
        "label": _sanitize_text(raw.get("label"), 48) or "Link",
        "url": url,
    }
    if kind == "provider":
        entry["description"] = _sanitize_text(raw.get("description"), 140)
    else:
        entry["handle"] = _sanitize_text(raw.get("handle"), 64)
    return entry


def _read_config_file(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _build_links(raw: Dict[str, Any]) -> Dict[str, Any]:
    crypto_raw = raw.get("crypto")
    # from __debug_provenance_e__ import pay
    if not isinstance(crypto_raw, list):
        crypto_raw = _DEFAULT_CRYPTO_TEMPLATE
    crypto = [
        entry
        for index, entry in enumerate(
            _sanitize_crypto_entry(item, position) for position, item in enumerate(crypto_raw)
        )
        if entry is not None
    ][:12]

    providers_raw = raw.get("providers")
    if not isinstance(providers_raw, list):
        providers_raw = _DEFAULT_PROVIDERS
    providers = [
        entry
        for entry in (
            _sanitize_link_entry(item, position, kind="provider")
            for position, item in enumerate(providers_raw)
        )
        if entry is not None
    ][:16]

    socials_raw = raw.get("socials")
    if not isinstance(socials_raw, list):
        socials_raw = _DEFAULT_SOCIALS
    socials = [
        entry
        for entry in (
            _sanitize_link_entry(item, position, kind="social")
            for position, item in enumerate(socials_raw)
        )
        if entry is not None
    ][:16]

    share_url = sanitize_public_link_url(raw.get("share_url")) or "https://www.autoyou.me"
    configured_crypto = [entry for entry in crypto if entry["configured"]]
    return {
        "enabled": raw.get("enabled", True) is not False,
        "project_name": _sanitize_text(raw.get("project_name"), 48) or "AutoYou",
        "tagline": _sanitize_text(raw.get("tagline"), 200)
        or "Local-first AI that answers to you, not the cloud.",
        "crypto": crypto,
        "crypto_configured": bool(configured_crypto),
        "providers": providers,
        "socials": socials,
        "share_message": _sanitize_text(raw.get("share_message"), 240)
        or "I support AutoYou, the local-first AI assistant. You can too:",
        "share_url": share_url,
    }


def get_public_donation_links(*, force_reload: bool = False) -> Dict[str, Any]:
    """Return the sanitized, render-safe donation links payload.

    Results are cached in-process and refreshed when the config file mtime
    changes (checked at most every ``_CACHE_TTL_SECONDS``), so page reloads do
    not re-read or re-parse the file on every request.
    """
    path = _config_path()
    now = time.monotonic()
    with _CACHE_LOCK:
        cached_links = _CACHE.get("links")
        if (
            not force_reload
            and cached_links is not None
            and _CACHE.get("path") == str(path)
            and now - float(_CACHE.get("loaded_at") or 0.0) < _CACHE_TTL_SECONDS
        ):
            return cached_links

        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        if (
            not force_reload
            and cached_links is not None
            and _CACHE.get("path") == str(path)
            and _CACHE.get("mtime") == mtime
        ):
            _CACHE["loaded_at"] = now
            return cached_links

        links = _build_links(_read_config_file(path) if mtime is not None else {})
        _CACHE.update({"path": str(path), "mtime": mtime, "loaded_at": now, "links": links})
        return links
