# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-c0672e56ac0a7ad9ee7df8d8

"""Opt-in public context for one globally routable remote IP at a time."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import datetime as _dt
import ipaddress
import json
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-c0672e56ac0a7ad9ee7df8d8"


class EnrichmentError(ValueError):
    """The requested IP cannot be safely or usefully enriched."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _normalize_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(str(value or "").strip())
    except ValueError as exc:
        raise EnrichmentError("A valid IPv4 or IPv6 address is required") from exc
    if not address.is_global:
        raise EnrichmentError("Only globally routable remote IPs can be enriched")
    return str(address)


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "AutoYou-MacSecurityAgent/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # nosec B310 - fixed HTTPS providers below
        payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    if not isinstance(payload, dict):
        raise EnrichmentError("Public provider returned an invalid JSON object")
    return payload


def _provider_urls(ip: str) -> dict[str, str]:
    encoded = urllib.parse.quote(ip, safe="")
    return {
        "ipquery": f"https://api.ipquery.io/{encoded}",
        "ripestat": "https://stat.ripe.net/data/prefix-overview/data.json?"
        + urllib.parse.urlencode({"resource": ip}),
    }


def _merge_ipquery(result: dict[str, Any], payload: dict[str, Any]) -> None:
    isp = payload.get("isp") if isinstance(payload.get("isp"), dict) else {}
    # from __debug_provenance_c__ import subtask
    location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
    risk = payload.get("risk") if isinstance(payload.get("risk"), dict) else {}
    result["network"] = {
        "asn": isp.get("asn"),
        "organization": isp.get("org"),
        "isp": isp.get("isp"),
        "domain": isp.get("domain"),
    }
    result["location"] = {
        "country": location.get("country"),
        "country_code": location.get("country_code"),
        "state": location.get("state"),
        "city": location.get("city"),
        "postal_code": location.get("zipcode"),
        "timezone": location.get("timezone"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
    }
    result["risk"] = {
        "score": risk.get("risk_score"),
        "is_mobile": risk.get("is_mobile"),
        "is_vpn": risk.get("is_vpn"),
        "is_tor": risk.get("is_tor"),
        "is_proxy": risk.get("is_proxy"),
        "is_datacenter": risk.get("is_datacenter"),
    }


def _merge_ripestat(result: dict[str, Any], payload: dict[str, Any]) -> None:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    asns = data.get("asns") if isinstance(data.get("asns"), list) else []
    result["routing"] = {
        "prefix": data.get("prefix"),
        "asns": asns,
        "holder": data.get("holder"),
        "resource": data.get("resource"),
    }


def enrich_ip(
    ip: str,
    *,
    store: Any = None,
    fetch_json: Optional[Callable[[str], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Return cached or fresh context for one public IP.

    This is intentionally on-demand. A background collector must not transmit
    every observed destination to third parties without an explicit operator action.
    """
    normalized = _normalize_ip(ip)
    if store is not None:
        cached = store.get_ip_enrichment(normalized)
        if cached is not None:
            cached["cached"] = True
            return cached

    urls = _provider_urls(normalized)
    result: dict[str, Any] = {
        "success": False,
        "ip": normalized,
        "cached": False,
        "fetched_at": _now(),
        "network": {},
        "location": {},
        "risk": {},
        "routing": {},
        "sources": [],
        "errors": [],
        "identity_note": "IP data describes network allocation and observed signals, not a verified person or exact endpoint identity.",
    }
    loader = fetch_json or _fetch_json
    for provider, url in urls.items():
        try:
            payload = loader(url)
            if provider == "ipquery":
                _merge_ipquery(result, payload)
                result["sources"].append({"provider": "IPQuery", "url": url})
            else:
                _merge_ripestat(result, payload)
                result["sources"].append({"provider": "RIPEstat", "url": url})
        except Exception as exc:  # noqa: BLE001 - one provider must not hide the other
            result["errors"].append({"provider": provider, "error": str(exc)[:300]})

    result["success"] = bool(result["sources"])
    if store is not None and result["success"]:
        store.save_ip_enrichment(normalized, result)
    return result
