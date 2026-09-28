# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Native HTTP adapter for the external Odysseus companion API.

Odysseus remains an authenticated external application.  AutoYou only uses
its documented companion endpoints and never vendors or manages its data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import ssl as _ssl
from http.cookies import SimpleCookie
from typing import Any, Dict, List, Mapping, Optional, Tuple

import aiohttp

try:
    import certifi as _certifi

    _SSL_CTX = _ssl.create_default_context(cafile=_certifi.where())
except ImportError:
    _SSL_CTX = _ssl.create_default_context()


logger = logging.getLogger(__name__)
_DEFAULT_BASE = "http://127.0.0.1:7000"
_DEFAULT_TIMEOUT = 90.0
_ODYSSEUS_SESSION_CACHE: Dict[Tuple[str, str, str], str] = {}
_ODYSSEUS_COOKIE_CACHE: Dict[Tuple[str, str], str] = {}


class OdysseusBackendError(RuntimeError):
    """A safe, user-facing Odysseus integration error."""

    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.status = int(status or 0)


def _normalize_base(api_base: str) -> str:
    return (api_base or _DEFAULT_BASE).strip().rstrip("/") or _DEFAULT_BASE


def _credential_key(api_base: str, token: str, username: str) -> str:
    identity = token or username or "anonymous"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _safe_error_body(body: str) -> str:
    """Extract a short non-secret diagnostic from an HTTP response body."""
    try:
        parsed = json.loads(body or "{}")
    except Exception:
        parsed = {}
    if isinstance(parsed, dict):
        value = parsed.get("detail") or parsed.get("error") or parsed.get("message")
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    return "remote service returned an error"


def _cookie_from_headers(headers: Mapping[str, Any]) -> str:
    values: List[str] = []
    try:
        values = list(headers.getall("Set-Cookie", []))  # type: ignore[attr-defined]
    except Exception:
        raw = headers.get("set-cookie") or headers.get("Set-Cookie")
        if raw:
            values = [str(raw)]
    for raw in values:
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            continue
        morsel = jar.get("odysseus_session")
        if morsel and morsel.value:
            return str(morsel.value)
    return ""


class OdysseusClient:
    """Small authenticated client for the Odysseus companion API."""

    def __init__(
        self,
        *,
        api_base: str,
        token: str = "",
        username: str = "",
        password: str = "",
        totp_secret: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.api_base = _normalize_base(api_base)
        self.token = (token or os.getenv("ODYSSEUS_API_TOKEN", "")).strip()
        self.username = (username or os.getenv("ODYSSEUS_USERNAME", "admin")).strip() or "admin"
        self.password = password or os.getenv("ODYSSEUS_PASSWORD", "")
        self.totp_secret = totp_secret or os.getenv("ODYSSEUS_TOTP_SECRET", "")
        self.timeout = max(5.0, float(timeout or _DEFAULT_TIMEOUT))
        self._identity_key = _credential_key(self.api_base, self.token, self.username)
        self._cookie = _ODYSSEUS_COOKIE_CACHE.get((self.api_base, self.username), "")

    def _headers(self) -> Dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        if self._cookie:
            return {"Cookie": f"odysseus_session={self._cookie}"}
        return {}

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        form_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Tuple[int, Mapping[str, Any], str]:
        url = f"{self.api_base}/{path.lstrip('/')}"
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout, connect=10.0)
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=_SSL_CTX),
                timeout=timeout,
            ) as session:
                async with session.request(
                    method,
                    url,
                    headers=dict(headers or {}),
                    json=json_body,
                    data=form_body,
                ) as response:
                    return response.status, response.headers, await response.text()
        except asyncio_timeout_errors() as exc:
            raise OdysseusBackendError("Odysseus request timed out.") from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise OdysseusBackendError("Odysseus service is unreachable.") from exc

    async def _login(self) -> None:
        if not self.password:
            raise OdysseusBackendError(
                "Odysseus authentication is required. Create a chat API token and add it in AutoYou."
            )
        payload: Dict[str, Any] = {"username": self.username, "password": self.password, "remember": True}
        status, headers, body = await self._raw_request(
            "POST", "/api/auth/login", json_body=payload, headers={"Content-Type": "application/json"}
        )
        try:
            data = json.loads(body or "{}")
        except Exception:
            data = {}
        if status >= 400:
            raise OdysseusBackendError("Odysseus login failed.", status=status)

        if isinstance(data, dict) and data.get("requires_totp"):
            secret = self.totp_secret.strip()
            if not secret:
                raise OdysseusBackendError("Odysseus login requires 2FA, but no TOTP secret is configured.")
            try:
                import pyotp  # type: ignore[import-not-found]

                payload["totp_code"] = pyotp.TOTP(secret).now()
            except ImportError as exc:
                raise OdysseusBackendError("Install the AutoYou TOTP extra to use Odysseus 2FA fallback.") from exc
            except Exception as exc:
                raise OdysseusBackendError("The configured Odysseus TOTP secret is invalid.") from exc
            status, headers, body = await self._raw_request(
                "POST", "/api/auth/login", json_body=payload, headers={"Content-Type": "application/json"}
            )
            if status >= 400:
                raise OdysseusBackendError("Odysseus 2FA login failed.", status=status)
            try:
                data = json.loads(body or "{}")
            except Exception:
                data = {}

        if not isinstance(data, dict) or data.get("ok") is False:
            raise OdysseusBackendError("Odysseus login was not accepted.", status=status)
        cookie = _cookie_from_headers(headers)
        if not cookie:
            raise OdysseusBackendError("Odysseus login did not return a session cookie.")
        self._cookie = cookie
        _ODYSSEUS_COOKIE_CACHE[(self.api_base, self.username)] = cookie

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        form_body: Optional[Mapping[str, Any]] = None,
        retry_auth: bool = True,
    ) -> Dict[str, Any]:
        status, _, body = await self._raw_request(
            method,
            path,
            json_body=json_body,
            form_body=form_body,
            headers=self._headers(),
        )
        if status == 401 and not self.token and retry_auth:
            self._cookie = ""
            await self._login()
            return await self._request_json(
                method, path, json_body=json_body, form_body=form_body, retry_auth=False
            )
        if status >= 400:
            raise OdysseusBackendError(
                f"Odysseus request failed ({status}): {_safe_error_body(body)}", status=status
            )
        try:
            parsed = json.loads(body or "{}")
        except Exception as exc:
            raise OdysseusBackendError("Odysseus returned invalid JSON.", status=status) from exc
        if not isinstance(parsed, dict):
            raise OdysseusBackendError("Odysseus returned an unexpected response.", status=status)
        return parsed

    async def model_endpoints(self) -> List[Dict[str, Any]]:
        data = await self._request_json("GET", "/api/companion/models")
        raw_endpoints = data.get("endpoints")
        if not isinstance(raw_endpoints, list):
            return []
        endpoints: List[Dict[str, Any]] = []
        for raw in raw_endpoints:
            if not isinstance(raw, dict):
                continue
            endpoint_id = str(raw.get("endpoint_id") or "").strip()
            if not endpoint_id:
                continue
            endpoints.append(
                {
                    "endpoint_id": endpoint_id,
                    "name": str(raw.get("name") or endpoint_id),
                    "endpoint_url": str(raw.get("endpoint_url") or ""),
                    "models": [str(value).strip() for value in (raw.get("models") or []) if str(value).strip()],
                    "supports_tools": bool(raw.get("supports_tools")),
                }
            )
        return endpoints

    @staticmethod
    def _select_endpoint(endpoints: List[Dict[str, Any]], model: str) -> Tuple[str, str]:
        requested = str(model or "").strip()
        for endpoint in endpoints:
            models = endpoint.get("models") or []
            if requested and requested in models:
                return str(endpoint["endpoint_id"]), requested
        if requested:
            requested_name = requested.rsplit("/", 1)[-1]
            for endpoint in endpoints:
                for available in endpoint.get("models") or []:
                    if str(available).rsplit("/", 1)[-1] == requested_name:
                        return str(endpoint["endpoint_id"]), str(available)
        for endpoint in endpoints:
            models = endpoint.get("models") or []
            if models:
                return str(endpoint["endpoint_id"]), str(models[0])
        available = [str(endpoint.get("endpoint_id") or "") for endpoint in endpoints]
        raise OdysseusBackendError(
            "Odysseus has no enabled chat model endpoint. Add an Ollama or other model endpoint in Odysseus."
            if not available
            else "Odysseus returned no usable models for its enabled endpoints."
        )

    async def _session_for(self, autoyou_session_id: str, model: str) -> Tuple[str, str]:
        cache_key = (self.api_base, self._identity_key, autoyou_session_id)
        cached = _ODYSSEUS_SESSION_CACHE.get(cache_key)
        if cached:
            return cached, model
        endpoint_id, selected_model = self._select_endpoint(await self.model_endpoints(), model)
        data = await self._request_json(
            "POST",
            "/api/session",
            form_body={
                "name": f"AutoYou {autoyou_session_id[-12:]}",
                "endpoint_id": endpoint_id,
                "model": selected_model,
                "skip_validation": "false",
            },
        )
        session_id = str(data.get("id") or "").strip()
        if not session_id:
            raise OdysseusBackendError("Odysseus did not return a chat session id.")
        _ODYSSEUS_SESSION_CACHE[cache_key] = session_id
        return session_id, selected_model

    async def chat(self, *, message: str, session_id: str, model: str = "") -> str:
        odysseus_session, selected_model = await self._session_for(session_id, model)
        data = await self._request_json(
            "POST",
            "/api/chat",
            json_body={
                "message": message,
                "session": odysseus_session,
                "attachments": [],
                "use_web": False,
                "use_research": False,
                "model": selected_model,
            },
        )
        reply = data.get("response")
        if not isinstance(reply, str) or not reply.strip():
            raise OdysseusBackendError("Odysseus returned an empty chat response.")
        return reply


def asyncio_timeout_errors():
    import asyncio

    return (asyncio.TimeoutError,)


async def probe_odysseus(
    api_base: str,
    *,
    token: str = "",
    username: str = "",
    password: str = "",
    totp_secret: str = "",
) -> Dict[str, Any]:
    """Return service, authentication, and usable-model readiness."""
    base = _normalize_base(api_base)
    result: Dict[str, Any] = {
        "available": False,
        "reachable": False,
        "authenticated": False,
        "api_base": base,
        "models": [],
        "endpoint_count": 0,
    }
    try:
        client = OdysseusClient(
            api_base=base,
            token=token,
            username=username,
            password=password,
            totp_secret=totp_secret,
            timeout=8.0,
        )
        status, _, _ = await client._raw_request("GET", "/api/health")
        if status != 200:
            result["error"] = f"Odysseus health check returned HTTP {status}."
            return result
        result["reachable"] = True
        if not client.token and not client.password:
            result["error"] = "Odysseus is reachable but requires a chat API token."
            return result
        endpoints = await client.model_endpoints()
        result["authenticated"] = True
        result["available"] = bool(endpoints)
        result["endpoint_count"] = len(endpoints)
        result["models"] = [model for endpoint in endpoints for model in (endpoint.get("models") or [])]
        if not endpoints:
            result["error"] = "Authenticated, but no enabled Odysseus model endpoints are configured."
        return result
    except OdysseusBackendError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:
        logger.debug("Odysseus probe failed: %s", exc)
        result["error"] = "Odysseus probe failed."
        return result


async def call_odysseus(
    *,
    api_base: str,
    model: str,
    message: str,
    session_id: str,
    token: str = "",
    username: str = "",
    password: str = "",
    totp_secret: str = "",
) -> str:
    """Send one AutoYou message through Odysseus and return plain text."""
    client = OdysseusClient(
        api_base=api_base,
        token=token,
        username=username,
        password=password,
        totp_secret=totp_secret,
    )
    try:
        return await client.chat(message=message, session_id=session_id, model=model)
    except OdysseusBackendError as exc:
        logger.error("Odysseus call failed: %s", exc)
        return (
            "I could not reach Odysseus AI. Check that the service is running, "
            "the chat token is valid, and an enabled model endpoint is configured."
        )
    except Exception as exc:
        logger.error("Unexpected Odysseus call failure: %s", exc)
        return "I could not reach Odysseus AI. Please check its AutoYou integration settings."


def reset_odysseus_cache() -> None:
    """Forget cached Odysseus sessions and login cookies after a config change."""
    _ODYSSEUS_SESSION_CACHE.clear()
    _ODYSSEUS_COOKIE_CACHE.clear()


__all__ = [
    "OdysseusBackendError",
    "OdysseusClient",
    "call_odysseus",
    "probe_odysseus",
    "reset_odysseus_cache",
]
