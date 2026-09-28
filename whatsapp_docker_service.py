# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-746f20706179203130252061-b845dbcc528e832e211966f3

"""
WhatsApp Docker Service - wwebjs-api integration for compiled AutoYou builds.

Uses the avoylenko/wwebjs-api Docker container (REST API on port 3000) instead
of the Node.js whatsapp_client.js subprocess.  This avoids the Node.js + native
module dependency that breaks inside a Nuitka standalone bundle.

Key contracts preserved from WhatsAppNodeService:
  - async start() -> bool
  - async stop() -> None
  - async restart() -> bool
  - async get_status() -> dict
  - async wait_for_qr_code(timeout_seconds) -> Optional[str]
  - async get_qr_code_data_url() -> Optional[str]
  - async send_message(to, message) -> bool
  - def get_message_log() -> list
  - async _handle_incoming_message(message_data)  [called by webhook]
  - async _send_message_to_self(message, append_signature)

The admin server registers a POST /webhook/whatsapp endpoint that forwards
events from the container to _handle_incoming_message on the active service
instance.  Set AUTOYOU_WHATSAPP_DOCKER_PORT to override the host-side REST
port (default 3200 - avoids clash with the existing WS port 8083).
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-746f20706179203130252061-b845dbcc528e832e211966f3"


import asyncio
import base64
import json
import logging
import os
import re
import sys
import time
from io import BytesIO
from typing import Any, Dict, List, Optional, Set

try:
    import qrcode
except ImportError:
    qrcode = None  # type: ignore[assignment]

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

try:
    import docker
    import docker.errors
except ImportError:
    docker = None  # type: ignore[assignment]

from pairing_router import PairingRouter, pairing_router
from shared.platform_runtime import get_mutable_data_dir, get_service_data_dir
from shared.session_execution import (
    STATUS_QUEUED,
    SessionQueueFullError,
    SessionTurnTimeoutError,
    get_session_execution_manager,
)

LOGGER = logging.getLogger("autoyou.whatsapp_docker")

# Docker image - official maintainer image, left untouched.
_DOCKER_IMAGE = "avoylenko/wwebjs-api:latest"
# wwebjs-api listens on 3000 inside the container.
_CONTAINER_PORT = 3000
# Default host-side port to bind.
_HOST_PORT = int(os.getenv("AUTOYOU_WHATSAPP_DOCKER_PORT", "3200"))
# Session ID used for the wwebjs-api session.
_SESSION_ID = "AutoYou"


def _get_conversation_session_manager():
    try:
        from rest_api import get_session_manager
        return get_session_manager()
    except Exception:
        return None


def _resolve_conversation_identity(identity, *, start_new_thread: bool = False):
    session_manager = _get_conversation_session_manager()
    if session_manager is None:
        return identity
    owner_key = str(getattr(identity, "owner_key", "") or "").strip()
    if not owner_key:
        return identity
    try:
        from dataclasses import replace
        if start_new_thread and hasattr(session_manager, "advance_conversation_thread"):
            thread_id, canonical_session_id = session_manager.advance_conversation_thread(owner_key)
        elif hasattr(session_manager, "get_current_conversation_thread") and hasattr(
            session_manager, "get_current_conversation_session_id"
        ):
            thread_id = session_manager.get_current_conversation_thread(owner_key)
            canonical_session_id = session_manager.get_current_conversation_session_id(owner_key)
        else:
            return identity
        return replace(
            identity,
            canonical_session_id=str(canonical_session_id),
            thread_id=(int(thread_id) if int(thread_id) > 1 else None),
        )
    except Exception:
        return identity


def _extract_conversation_request(message_text: str):
    normalized = str(message_text or "").strip()
    lowered = normalized.lower()
    start_new = lowered in {"/newconversation", "/newchat"}
    return start_new, normalized, start_new


class WhatsAppDockerService:
    """
    WhatsApp service backed by the avoylenko/wwebjs-api Docker container.

    The container exposes a REST API; this class drives it via httpx and
    receives inbound events via the /webhook/whatsapp endpoint registered
    by server.py.
    """

    def __init__(
        self,
        websocket_port: int = 8083,
        device_name: str = "AutoYou-WhatsApp",
        ai_api_url: str = "http://localhost:8081/api/chat",
    ):
        self.websocket_port = websocket_port  # kept for interface compat
        self.device_name = device_name
        self.ai_api_url = ai_api_url

        # REST API target
        self._host_port: int = _HOST_PORT
        self._api_base: str = f"http://127.0.0.1:{self._host_port}"
        self._session_id: str = _SESSION_ID

        # State
        self.connection_status: str = "disconnected"
        self.is_ready: bool = False
        self.is_paired: bool = False
        self.phone_number: Optional[str] = None
        self.last_qr_code: Optional[str] = None
        self.message_log: List[Dict[str, Any]] = []
        self.chat_tasks: Set[asyncio.Task] = set()
        self._restart_request_tasks: Set[asyncio.Task] = set()

        # Extended state attrs expected by server.py status rendering
        self.client_state: str = "UNLAUNCHED"
        self.battery_info: Optional[dict] = None
        self.reconnection_attempts: int = 0
        self.last_state_change: Optional[float] = None
        self.loading_screen_active: bool = False
        self.remote_session_saved: bool = False
        self.last_status_snapshot: Optional[dict] = None
        self.last_heartbeat: float = time.time()
        self.websocket_reconnect_attempts: int = 0

        # Docker / lifecycle
        self._container = None
        self._docker_client = None
        self._stop_requested: bool = False
        self._shutdown_in_progress: bool = False
        self._restart_task: Optional[asyncio.Task] = None
        self._health_task: Optional[asyncio.Task] = None

        # Data directory for session auth
        self._state_dir = get_service_data_dir("whatsapp_docker", anchor=__file__)

    # ------------------------------------------------------------------
    # Docker helpers
    # ------------------------------------------------------------------

    def _get_docker(self):
        if docker is None:
            return None
        if self._docker_client is not None:
            return self._docker_client
        try:
            self._docker_client = docker.from_env()
            self._docker_client.ping()
            return self._docker_client
        except Exception:
            self._docker_client = None
            return None

    def _admin_port(self) -> int:
        """Return the admin server port for the webhook URL."""
        try:
            return int(os.getenv("AUTOYOU_ADMIN_PORT", os.getenv("ADMIN_WEB_SERVICE_PORT", "8001")))
        except Exception:
            return 8001

    def _webhook_url(self) -> str:
        """Webhook URL the Docker container can POST events to."""
        admin_port = self._admin_port()
        # host.docker.internal resolves to the host on both Docker Desktop
        # (Windows/Mac) and Docker Engine on Linux with --add-host.
        return f"http://host.docker.internal:{admin_port}/webhook/whatsapp"

    def _stop_existing_container(self, client) -> None:
        container_name = f"autoyou-whatsapp-{self._session_id}"
        try:
            existing = client.containers.get(container_name)
            LOGGER.info("Removing existing WhatsApp container %s", container_name)
            existing.remove(force=True)
        except docker.errors.NotFound:
            pass
        except Exception as exc:
            LOGGER.debug("Could not remove existing container: %s", exc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Start the Docker WhatsApp client and session."""
        self._stop_requested = False
        self._shutdown_in_progress = False

        client = self._get_docker()
        if client is None:
            LOGGER.error(
                "Docker is not available. Cannot start WhatsApp service. "
                "Install Docker Desktop and ensure it is running."
            )
            return False

        try:
            self.connection_status = "starting"
            self.client_state = "STARTING"

            container_name = f"autoyou-whatsapp-{self._session_id}"
            self._stop_existing_container(client)

            webhook_url = self._webhook_url()
            LOGGER.info("Starting wwebjs-api container; webhook=%s", webhook_url)

            extra_hosts = {}
            if sys.platform == "linux":
                extra_hosts["host.docker.internal"] = "host-gateway"

            self._container = await asyncio.to_thread(
                client.containers.run,
                _DOCKER_IMAGE,
                name=container_name,
                ports={f"{_CONTAINER_PORT}/tcp": self._host_port},
                volumes={str(self._state_dir): {"bind": "/app/sessions", "mode": "rw"}},
                environment={
                    "BASE_WEBHOOK_URL": webhook_url,
                    "ENABLE_WEBSOCKET": "FALSE",
                    "DISABLED_CALLBACKS": "message_ack,group_update",
                    "SESSION_SAVE_PATH": "/app/sessions",
                },
                extra_hosts=extra_hosts if extra_hosts else None,
                detach=True,
                restart_policy={"Name": "on-failure", "MaximumRetryCount": 3},
            )

            # Wait for REST API to be ready
            if not await self._wait_for_api(timeout=30):
                LOGGER.error("wwebjs-api did not become ready within 30 seconds")
                await self.stop()
                return False

            # Initiate session
            await self._start_session(webhook_url)

            # Start background health/status poller
            self._health_task = asyncio.create_task(self._health_loop())

            LOGGER.info("WhatsApp Docker service started successfully")
            return True

        except Exception as exc:
            LOGGER.error("Failed to start WhatsApp Docker service: %s", exc)
            self.connection_status = "disconnected"
            return False

    async def _wait_for_api(self, timeout: int = 30) -> bool:
        if httpx is None:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop_requested:
                return False
            try:
                async with httpx.AsyncClient(timeout=2.0) as c:
                    resp = await c.get(f"{self._api_base}/")
                    if resp.status_code < 500:
                        return True
            except Exception:
                pass
            await asyncio.sleep(1)
        return False

    async def _start_session(self, webhook_url: str) -> None:
        if httpx is None:
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                resp = await c.post(
                    f"{self._api_base}/session/start/{self._session_id}",
                    json={"webhook": webhook_url},
                )
                LOGGER.debug("Session start response: %s", resp.status_code)
        except Exception as exc:
            LOGGER.warning("Session start request failed: %s", exc)

    async def stop(self) -> None:
        """Stop the Docker WhatsApp service."""
        self._stop_requested = True
        self._shutdown_in_progress = True

        for task in list(self.chat_tasks):
            if not task.done():
                task.cancel()
        if self.chat_tasks:
            await asyncio.gather(*list(self.chat_tasks), return_exceptions=True)
        self.chat_tasks.clear()

        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        self._health_task = None

        # Terminate session gracefully
        if httpx is not None and self._container is not None:
            try:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    await c.delete(f"{self._api_base}/session/terminate/{self._session_id}")
            except Exception:
                pass

        # Stop container
        if self._container is not None:
            try:
                LOGGER.info("Stopping WhatsApp Docker container...")
                await asyncio.to_thread(self._container.stop, 8)
                await asyncio.to_thread(self._container.remove)
            except Exception as exc:
                LOGGER.warning("Error stopping container: %s", exc)
            finally:
                self._container = None

        self.connection_status = "disconnected"
        self.is_ready = False
        self.is_paired = False
        self.client_state = "UNLAUNCHED"

    async def restart(self) -> bool:
        """Restart the service."""
        if self._shutdown_in_progress:
            return False
        if self._restart_task and not self._restart_task.done():
            return await self._restart_task
        self._restart_task = asyncio.create_task(self._restart_impl())
        try:
            return await self._restart_task
        finally:
            if self._restart_task and self._restart_task.done():
                self._restart_task = None

    async def _restart_impl(self) -> bool:
        LOGGER.info("Restarting WhatsApp Docker service...")
        await self.stop()
        await asyncio.sleep(2)
        self._stop_requested = False
        self._shutdown_in_progress = False
        return await self.start()

    # ------------------------------------------------------------------
    # Health / status polling
    # ------------------------------------------------------------------

    async def _health_loop(self) -> None:
        """Poll wwebjs-api session status and update local state."""
        if httpx is None:
            return
        while not self._stop_requested:
            try:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    resp = await c.get(
                        f"{self._api_base}/session/status/{self._session_id}"
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        state = str(data.get("state") or "").upper()
                        self._apply_session_state(state)

                        if state == "CONNECTED" and not self.phone_number:
                            await self._fetch_phone_number(c)
            except Exception as exc:
                LOGGER.debug("WhatsApp Docker health check error: %s", exc)
                if not self._stop_requested:
                    self.connection_status = "disconnected"
                    self.is_ready = False
            self.last_heartbeat = time.time()
            await asyncio.sleep(5)

    def _apply_session_state(self, state: str) -> None:
        if state == "CONNECTED":
            if not self.is_ready:
                LOGGER.info("WhatsApp Docker session CONNECTED")
                self.is_ready = True
                self.is_paired = True
                self.connection_status = "connected"
                self.client_state = "CONNECTED"
                self.last_state_change = time.time()
                self.last_qr_code = None
        elif state in ("STARTING", "INITIALIZING"):
            self.connection_status = "starting"
            self.client_state = "STARTING"
        elif state in ("DISCONNECTED", "LOGOUT"):
            self.is_ready = False
            self.is_paired = False
            self.connection_status = "disconnected"
            self.client_state = "DISCONNECTED"
        # QR state: leave connection_status as "pairing" if QR present

    async def _fetch_phone_number(self, http_client) -> None:
        """Try to obtain this client's WhatsApp number."""
        try:
            resp = await http_client.get(
                f"{self._api_base}/client/getNumberId/{self._session_id}"
            )
            if resp.status_code == 200:
                data = resp.json()
                # result may be "1234567890@c.us" or {"_serialized": "..."}
                result = data.get("result") or data.get("_serialized") or ""
                if isinstance(result, dict):
                    result = result.get("_serialized") or result.get("user") or ""
                number = str(result).split("@")[0].lstrip("+")
                if number:
                    self.phone_number = number
                    LOGGER.info("WhatsApp Docker phone number: %s", self.phone_number)
        except Exception as exc:
            LOGGER.debug("Could not fetch phone number: %s", exc)

    # ------------------------------------------------------------------
    # Webhook event handler (called by server.py /webhook/whatsapp)
    # ------------------------------------------------------------------

    async def handle_webhook_event(self, payload: Dict[str, Any]) -> None:
        """Process a webhook event delivered by the wwebjs-api container."""
        event = str(payload.get("event") or "").lower()
        data = payload.get("data") or {}

        if event in ("message", "message_create"):
            await self._handle_incoming_message(data)
        elif event == "qr":
            qr_text = data if isinstance(data, str) else str(data.get("qr") or data)
            if qr_text and qr_text != self.last_qr_code:
                LOGGER.info("WhatsApp Docker: new QR code received")
                self.last_qr_code = qr_text
                self.connection_status = "pairing"
                self.client_state = "QR_RECEIVED"
        elif event == "ready":
            self._apply_session_state("CONNECTED")
        elif event in ("authenticated", "auth_success"):
            self.remote_session_saved = True
            LOGGER.info("WhatsApp Docker: authenticated")
        elif event in ("disconnected", "logout"):
            self._apply_session_state("DISCONNECTED")
        elif event == "loading_screen":
            self.loading_screen_active = True
        else:
            LOGGER.debug("WhatsApp Docker unhandled event: %s", event)

    # ------------------------------------------------------------------
    # Incoming message processing (mirrors WhatsAppNodeService)
    # ------------------------------------------------------------------

    async def _handle_incoming_message(self, message_data: Dict[str, Any]) -> None:
        """Handle an incoming message from the webhook payload."""
        try:
            if not message_data.get("fromMe", False) and not message_data.get("selfChat", False):
                return
            if not message_data.get("fromMe", False) and message_data.get("selfChat", False):
                LOGGER.info(
                    "Accepting WhatsApp Docker self-chat message without fromMe flag (source=%s, from=%s, to=%s, chat=%s)",
                    message_data.get("sourceEvent") or "-",
                    message_data.get("from") or "-",
                    message_data.get("to") or "-",
                    message_data.get("remoteChatId") or "-",
                )

            message_text = str(message_data.get("body") or "").strip()
            if not message_text:
                return
            if message_text.startswith("/otp") or message_text.startswith("/autopair_answer"):
                return
            if message_text.splitlines()[-1].endswith(f"~ {self.device_name}"):
                return

            # Log
            self.message_log.append({
                "timestamp": time.time(),
                "message": message_text,
                "id": message_data.get("id"),
                "chat_name": message_data.get("chatName") or message_data.get("from"),
                "contact_name": (
                    message_data.get("contactName")
                    or (message_data.get("_data") or {}).get("notifyName")
                ),
                "source_event": message_data.get("sourceEvent"),
            })

            LOGGER.info("Processing WhatsApp Docker message: %s", repr(message_text[:50]))

            # Pairing commands
            try:
                response = await pairing_router.process_message(
                    message_text=message_text,
                    platform="whatsapp",
                    sender_id=self.phone_number or "unknown",
                )
            except Exception as exc:
                LOGGER.warning("Pairing router error (WhatsApp Docker): %s", exc)
                response = None

            if response and response != PairingRouter.FRAGMENT_CONSUMED:
                # A real pairing reply: send it back.
                await self._send_message_to_self(response, append_signature=False)
                return
            if response == PairingRouter.FRAGMENT_CONSUMED:
                # Fragment buffered - waiting for more pieces; stay silent.
                LOGGER.debug(
                    "WhatsApp Docker autopair fragment consumed for sender=%s - "
                    "waiting for continuation.",
                    self.phone_number or "unknown",
                )
                return
            # Guard against orphaned autopair payload fragments reaching the AI.
            if PairingRouter.looks_like_raw_autopair_fragment(message_text):
                LOGGER.warning(
                    "WhatsApp Docker message looks like a raw autopair payload fragment "
                    "(no pending buffer, len=%d). Suppressing AI dispatch.",
                    len(message_text),
                )
                return

            # AI processing
            start_new_thread, normalized_text, _ = _extract_conversation_request(message_text)
            from pairing_router import ConversationIdentity
            identity = ConversationIdentity(
                platform="whatsapp",
                owner_key=self.phone_number or "whatsapp_unknown",
                sender_id=self.phone_number or "unknown",
            )
            identity = _resolve_conversation_identity(identity, start_new_thread=start_new_thread)

            try:
                execution_manager = get_session_execution_manager()
                status, result = await execution_manager.enqueue_and_wait(
                    identity=identity,
                    user_text=normalized_text,
                    attachments=[],
                    timeout_seconds=120,
                )
                if result:
                    await self._send_message_to_self(result)
            except (SessionQueueFullError, SessionTurnTimeoutError) as exc:
                LOGGER.warning("Session execution error: %s", exc)
            except Exception as exc:
                LOGGER.error("Error processing WhatsApp Docker message: %s", exc)

        except Exception as exc:
            LOGGER.error("_handle_incoming_message error: %s", exc)

    def _track_chat_task(self, coro, *, session_key: str, label: str) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.chat_tasks.add(task)
        def _done(t):
            self.chat_tasks.discard(t)
        task.add_done_callback(_done)
        return task

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------

    async def _send_message_to_self(
        self, message: str, append_signature: bool = True
    ) -> None:
        """Send a message to the user's own WhatsApp number."""
        if not self.phone_number:
            LOGGER.error("Cannot send to self: phone number unknown")
            return
        if append_signature:
            message += f"\n~ {self.device_name}"
        await self.send_message(self.phone_number, message)

    async def send_message(self, to: str, message: str) -> bool:
        """Send a message to a WhatsApp number via the REST API."""
        if not self.is_ready:
            LOGGER.error("Cannot send message: service not ready")
            return False
        if httpx is None:
            LOGGER.error("httpx is required for Docker WhatsApp send_message")
            return False

        # Normalise to WhatsApp JID format
        raw = re.sub(r"[^0-9]", "", to)
        chat_id = f"{raw}@c.us" if "@" not in to else to

        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                resp = await c.post(
                    f"{self._api_base}/client/sendMessage/{self._session_id}",
                    json={"chatId": chat_id, "contentType": "string", "content": message},
                )
                if resp.status_code == 200:
                    LOGGER.info("Sent WhatsApp Docker message to %s", chat_id)
                    return True
                else:
                    LOGGER.error(
                        "wwebjs-api sendMessage failed: %s %s", resp.status_code, resp.text[:200]
                    )
                    return False
        except Exception as exc:
            LOGGER.error("Error sending WhatsApp Docker message: %s", exc)
            return False

    # ------------------------------------------------------------------
    # QR code
    # ------------------------------------------------------------------

    async def get_qr_code_data(self) -> Optional[str]:
        """Return QR code as base64-encoded PNG, or None if unavailable."""
        # First check cached QR from webhook
        raw_qr = self.last_qr_code
        if not raw_qr and httpx is not None:
            # Try fetching from the API
            try:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    resp = await c.get(
                        f"{self._api_base}/session/qr/{self._session_id}"
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        raw_qr = data.get("qr") or data.get("data")
                        if raw_qr:
                            self.last_qr_code = raw_qr
            except Exception:
                pass

        if not raw_qr:
            return None

        if qrcode is None:
            return None

        try:
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(raw_qr)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode()
        except Exception as exc:
            LOGGER.error("Error generating Docker QR PNG: %s", exc)
            return None

    async def get_qr_code_data_url(self) -> Optional[str]:
        """Return QR code as a data URL suitable for an <img> tag."""
        # Try PNG first
        png_b64 = await self.get_qr_code_data()
        if png_b64:
            return f"data:image/png;base64,{png_b64}"

        # Fetch image directly from wwebjs-api if available
        if httpx is not None:
            try:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    resp = await c.get(
                        f"{self._api_base}/session/qr/{self._session_id}/image"
                    )
                    if resp.status_code == 200:
                        b64 = base64.b64encode(resp.content).decode()
                        return f"data:image/png;base64,{b64}"
            except Exception:
                pass

        return None

    async def wait_for_qr_code(self, timeout_seconds: float = 12.0) -> Optional[str]:
        """Wait for a QR code to become available."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self.last_qr_code:
                return self.last_qr_code
            if self._stop_requested:
                return None
            await asyncio.sleep(0.5)
        return self.last_qr_code

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self) -> Dict[str, Any]:
        """Return status dict matching the WhatsAppNodeService shape."""
        return {
            "status": self.connection_status,
            "ready": self.is_ready,
            "paired": self.is_paired,
            "phone_number": self.phone_number,
            "node_process_running": self._container is not None,
            "websocket_connected": self.is_ready,  # REST-based; no WS
            "last_qr_available": self.last_qr_code is not None,
            "client_state": self.client_state,
            "battery_info": self.battery_info,
            "reconnection_attempts": self.reconnection_attempts,
            "last_state_change": self.last_state_change,
            "loading_screen_active": self.loading_screen_active,
            "remote_session_saved": self.remote_session_saved,
            "connection_healthy": self.is_ready and self.is_paired,
            "websocket_reconnect_attempts": self.websocket_reconnect_attempts,
            "last_heartbeat": self.last_heartbeat,
            "node_status_snapshot": self.last_status_snapshot,
            "backend": "docker",
        }

    # ------------------------------------------------------------------
    # Message log
    # ------------------------------------------------------------------

    def get_message_log(self) -> List[Dict[str, Any]]:
        return self.message_log.copy()

    async def cleanup_session(self) -> bool:
        """Terminate and remove the session (logout)."""
        if httpx is None or not self._container:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                resp = await c.delete(
                    f"{self._api_base}/session/terminate/{self._session_id}"
                )
                return resp.status_code == 200
        except Exception as exc:
            LOGGER.error("cleanup_session error: %s", exc)
            return False
