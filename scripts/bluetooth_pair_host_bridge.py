# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-2ee1c49363d05055fb515919

"""Host Bluetooth Pair bridge for WSL/Docker AutoYou servers.

Run this on the OS that owns the Bluetooth radio when the AutoYou server itself
is inside WSL or Docker. iOS/Android/Python clients still exchange `/autopair`
and `/autopair_answer` over BLE; this bridge only forwards the already received
signaling body to the local server API.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-2ee1c49363d05055fb515919"


import argparse
import asyncio
import base64
import getpass
import json
import logging
import os
import platform
import sys
import zlib
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import quote, urlsplit, urlunsplit

try:
    import aiohttp
except Exception:  # pragma: no cover - exercised when optional deps are absent
    aiohttp = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.bluetooth_pairing_protocol import DEFAULT_FRAME_PAYLOAD_BYTES
from shared.bluetooth_pairing_service import BlessBluetoothPairingServer

LOGGER = logging.getLogger("autoyou.bluetooth_pair_host_bridge")
AUTOPAIR_COMMAND = "/autopair"


def _json_error(message: str) -> str:
    return "/autopair_answer\n" + json.dumps({"error": message}, separators=(",", ":"))


def _server_origin(server_url: str) -> str:
    parsed = urlsplit(server_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("server URL must include scheme and host, e.g. http://127.0.0.1:8001")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _extract_autopair_body(command_text: str) -> str:
    text = str(command_text or "").replace("```", "").strip()
    is_autopair = text == AUTOPAIR_COMMAND or (
        text.startswith(AUTOPAIR_COMMAND) and text[len(AUTOPAIR_COMMAND)].isspace()
    )
    if not is_autopair:
        raise ValueError("Bluetooth Pair bridge accepts only /autopair commands.")
    body = text[len(AUTOPAIR_COMMAND) :].lstrip("\r\n \t")
    if not body:
        raise ValueError("Bluetooth Pair bridge received an empty /autopair body.")
    return body


def _decode_z_payload(text: str) -> str:
    payload = text[2:].strip()
    padding = "=" * (-len(payload) % 4)
    raw = base64.urlsafe_b64decode((payload + padding).encode("ascii"))
    return zlib.decompress(raw).decode("utf-8")


def _try_load_plaintext_payload(body: str) -> Optional[dict]:
    raw = str(body or "").strip()
    if raw.startswith("z:"):
        raw = _decode_z_payload(raw)
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def build_forward_body(command_text: str, *, client_id: str) -> Tuple[str, str]:
    """Return `(body_text, content_type)` for the server's `/api/autopair`.

    Normal-mode BLE clients send plaintext JSON or `z:` compressed JSON. The
    HTTP endpoint needs explicit Bluetooth identity fields in that JSON body.
    Secure modes send an encrypted blob, so identity is preserved separately via
    request headers and query params.
    """

    normalized_client_id = str(client_id or "").strip()
    if not normalized_client_id:
        raise ValueError("Bluetooth Pair bridge requires a client id.")
    body = _extract_autopair_body(command_text)
    payload = _try_load_plaintext_payload(body)
    if payload is None:
        return body, "text/plain"
    payload["_autoyou_pairing_platform"] = "bluetooth"
    payload["_autoyou_sender_id"] = normalized_client_id
    return json.dumps(payload, separators=(",", ":")), "application/json"


class HttpBluetoothPairForwarder:
    """BLE handler that forwards complete `/autopair` commands to AutoYou HTTP."""

    def __init__(
        self,
        *,
        server_url: str,
        password: str,
        totp: str = "",
        request_timeout: float = 45.0,
    ) -> None:
        if aiohttp is None:
            raise RuntimeError("aiohttp is required for the Bluetooth Pair host bridge.")
        self.server_url = _server_origin(server_url).rstrip("/")
        self.password = password
        self.totp = totp
        self.request_timeout = request_timeout
        self._session: Optional[Any] = None

    async def __aenter__(self) -> "HttpBluetoothPairForwarder":
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        self._session = aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            timeout=timeout,
        )
        await self.login()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        session = self._session
        self._session = None
        if session is not None:
            await session.close()

    @property
    def session(self) -> Any:
        if self._session is None:
            raise RuntimeError("Bluetooth Pair host bridge is not connected to the server.")
        return self._session

    async def login(self) -> None:
        data = {"password": self.password}
        if self.totp:
            data["totp"] = self.totp
        async with self.session.post(
            f"{self.server_url}/login",
            data=data,
            headers={"Accept": "application/json", "X-AutoYou-Async": "1"},
        ) as response:
            text = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"AutoYou admin login failed (HTTP {response.status}): {text[:240]}")

    async def handle_text(self, command_text: str, *, client_id: str) -> str:
        try:
            return await self._handle_text(command_text, client_id=client_id, retry_login=True)
        except Exception as exc:
            LOGGER.warning("Bluetooth Pair host bridge request failed: %s", exc, exc_info=True)
            return _json_error(str(exc))

    async def _handle_text(self, command_text: str, *, client_id: str, retry_login: bool) -> str:
        normalized_client_id = str(client_id or "").strip()
        body, content_type = build_forward_body(command_text, client_id=normalized_client_id)
        url = f"{self.server_url}/api/autopair?session_id={quote(normalized_client_id, safe='')}"
        headers = {
            "Accept": "text/plain, application/json",
            "Content-Type": content_type,
            "Origin": self.server_url,
            "Referer": f"{self.server_url}/",
            "X-AutoYou-Platform": "bluetooth",
            "X-AutoYou-Session-Id": normalized_client_id,
        }
        async with self.session.post(url, data=body.encode("utf-8"), headers=headers) as response:
            text = await response.text()
            if response.status == 401 and retry_login:
                await self.login()
                return await self._handle_text(command_text, client_id=normalized_client_id, retry_login=False)
            if response.status >= 400:
                raise RuntimeError(f"AutoYou /api/autopair failed (HTTP {response.status}): {text[:240]}")
            if not text.lstrip().startswith("/autopair_answer"):
                raise RuntimeError(f"AutoYou returned an unexpected Bluetooth Pair response: {text[:240]}")
            return text


def _default_bridge_name() -> str:
    host = platform.node().strip()
    return host or "AutoYou Bluetooth Pair"


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Advertise AutoYou Bluetooth Pair from the host OS.")
    parser.add_argument(
        "--server",
        default=os.environ.get("AUTOYOU_BRIDGE_SERVER_URL", "http://127.0.0.1:8001"),
        help="AutoYou admin server URL reachable from this host.",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("AUTOYOU_BRIDGE_PASSWORD", ""),
        help="AutoYou server password. Defaults to AUTOYOU_BRIDGE_PASSWORD or an interactive prompt.",
    )
    parser.add_argument(
        "--totp",
        default=os.environ.get("AUTOYOU_BRIDGE_TOTP", ""),
        help="Optional current admin TOTP code for the login step.",
    )
    parser.add_argument(
        "--name",
        default=os.environ.get("AUTOYOU_BLUETOOTH_NAME", _default_bridge_name()),
        help="Bluetooth advertisement name shown to nearby clients.",
    )
    parser.add_argument(
        "--frame-payload-bytes",
        type=int,
        default=DEFAULT_FRAME_PAYLOAD_BYTES,
        help="Maximum payload bytes per BLE JSON frame.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args(argv)


async def amain(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    password = str(args.password or "")
    if not password:
        password = getpass.getpass("AutoYou server password: ")
    if not password:
        raise SystemExit("AutoYou server password is required.")

    async with HttpBluetoothPairForwarder(
        server_url=args.server,
        password=password,
        totp=str(args.totp or ""),
        request_timeout=float(args.timeout),
    ) as forwarder:
        runtime = BlessBluetoothPairingServer(
            forwarder,  # type: ignore[arg-type]
            name=str(args.name or _default_bridge_name()),
            max_payload_bytes=int(args.frame_payload_bytes),
        )
        if not await runtime.start():
            status = runtime.status
            LOGGER.error("Bluetooth Pair host bridge did not start: %s", status.error)
            return 2
        LOGGER.info(
            "Bluetooth Pair host bridge advertising '%s' and forwarding to %s. Press Ctrl+C to stop.",
            args.name,
            forwarder.server_url,
        )
        try:
            while True:
                await asyncio.sleep(3600)
        except (asyncio.CancelledError, KeyboardInterrupt):
            LOGGER.info("Stopping Bluetooth Pair host bridge.")
        finally:
            await runtime.stop()
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(amain()))
    except KeyboardInterrupt:
        raise SystemExit(0)


if __name__ == "__main__":
    main()
