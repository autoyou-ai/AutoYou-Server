# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-4243e900c76de1f67ec7266a

"""Shared helpers for registering agent-local web servers with AutoYou's proxy."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-4243e900c76de1f67ec7266a"


import os
import re
from typing import Any, Dict, Optional

import requests


def normalize_agent_name(name: str) -> str:
    """Normalize arbitrary user input to AutoYou's ``snake_case_agent`` form."""
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", str(name or "")).lower().strip("_")
    normalized = re.sub(r"_+", "_", normalized)
    if normalized.endswith("_agent"):
        normalized = normalized[: -len("_agent")]
    if not normalized:
        raise ValueError("agent_name is required")
    return f"{normalized}_agent"


def _admin_api_url(path: str) -> str:
    host = os.environ.get("ADMIN_WEB_SERVICE_HOST", "127.0.0.1")
    port = int(os.environ.get("ADMIN_WEB_SERVICE_PORT", "8001"))
    return f"http://{host}:{port}{path}"


def _localhost_admin_urls(path: str) -> list[str]:
    port = int(os.environ.get("ADMIN_WEB_SERVICE_PORT", "8001"))
    return [
        f"http://127.0.0.1:{port}{path}",
        f"http://localhost:{port}{path}",
    ]


def _internal_token() -> str:
    return str(os.environ.get("AUTOYOU_AI_INTERNAL_API_TOKEN", "") or "").strip()


def _auth_headers() -> Dict[str, str]:
    token = _internal_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def call_admin_api(
    path: str,
    *,
    method: str = "POST",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call the AutoYou admin API with a localhost compatibility fallback."""
    url = _admin_api_url(path)
    timeout = int(os.environ.get("AI_AGENT_HTTP_TIMEOUT", "10"))
    headers = _auth_headers()
    try:
        response = getattr(requests, method.lower())(url, json=payload, headers=headers, timeout=timeout)
    except requests.exceptions.ConnectionError:
        url = url.replace("127.0.0.1", "localhost")
        response = getattr(requests, method.lower())(url, json=payload, headers=headers, timeout=timeout)

    try:
        data = response.json()
    except ValueError:
        data = {"text": response.text}

    return {
        "status": "success" if response.ok else "error",
        "code": response.status_code,
        "data": data,
        "url": url,
    }


def register_agent_web_port(agent_name: str, port: int) -> Dict[str, Any]:
    """Register an agent-local HTTP port with AutoYou's localhost-only proxy bridge."""
    normalized_name = normalize_agent_name(agent_name)
    timeout = int(os.environ.get("AI_AGENT_HTTP_TIMEOUT", "10"))
    payload = {"agent_name": normalized_name, "port": int(port)}

    headers = _auth_headers()
    response = None
    url = None
    last_error = None
    for url in _localhost_admin_urls("/api/builder/agent_port"):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            break
        except requests.exceptions.RequestException as exc:
            last_error = exc

    if response is None:
        result = {
            "status": "error",
            "code": None,
            "data": {"error": str(last_error) if last_error else "Failed to reach localhost admin API"},
            "url": url or _localhost_admin_urls("/api/builder/agent_port")[0],
        }
    else:
        try:
            data = response.json()
        except ValueError:
            data = {"text": response.text}
        result = {
            "status": "success" if response.ok else "error",
            "code": response.status_code,
            "data": data,
            "url": url,
        }

    result["agent_name"] = normalized_name
    result["proxy_path"] = f"/agent/{normalized_name}/"
    return result
