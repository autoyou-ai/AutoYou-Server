# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-ab5029b5d397ef561f9cfdbc

"""Owner-only Telegram Saved Messages transport.

This adapter intentionally uses Telegram's user API only for the account that
completed the local QR sign-in.  It never enumerates dialogs or reads history:
live updates are accepted only when both sides of the peer are that account's
Saved Messages conversation.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import base64
import inspect
import json
import logging
import mimetypes
import sys
import time
from collections import deque
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Iterable, List, Optional, Set

import qrcode

from pairing_router import PairingRouter, pairing_router
from shared.client_conversation_contract import build_autoyou_conversation_metadata
from shared.platform_runtime import get_service_data_dir
from shared.pending_media_queue import (
    delete_media_payload,
    prune_and_trim_media_entries,
    read_media_payload,
    store_media_payload,
)
from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json
from shared.session_execution import (
    SessionQueueFullError,
    SessionTurnTimeoutError,
    get_session_execution_manager,
)

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-ab5029b5d397ef561f9cfdbc"


try:  # Keep imports safe in binary-default builds where the optional partner is absent.
    from telethon import TelegramClient, events
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession
except Exception:  # pragma: no cover - covered by the unavailable status path
    TelegramClient = None
    events = None

    class SessionPasswordNeededError(Exception):
        """Never raised without telethon; aliasing Exception here would make
        the QR wait misread any transport error as a 2FA password prompt."""

    StringSession = None


LOGGER = logging.getLogger("autoyou.telegram_user_service")

_MAX_MEDIA_BYTES = 25 * 1024 * 1024
_LOG_LIMIT = 1000
_SERVER_MESSAGE_ID_LIMIT = 256
_PENDING_MEDIA_LIMIT = 20
# Telegram QR sign-in tokens expire roughly every 30 seconds, so one sign-in
# attempt keeps refreshing the token for about 20 minutes before it gives up
# and waits for the admin to reopen the pairing dialog.
_QR_REFRESH_LIMIT = 40


def _prompt_builder_command_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _extract_conversation_request(message_text: str) -> tuple[bool, str, bool]:
    normalized = str(message_text or "").strip()
    starts_new = normalized.lower() in {"/new", "/newconversation", "/newchat"}
    return starts_new, normalized, starts_new


def _get_conversation_session_manager() -> Any:
    try:
        from rest_api import get_session_manager

        return get_session_manager()
    except Exception:
        return None


def _resolve_conversation_identity(identity: Any, *, start_new_thread: bool = False) -> Any:
    """Use the shared conversation thread mapping when it is available."""
    manager = _get_conversation_session_manager()
    owner_key = str(getattr(identity, "owner_key", "") or "").strip()
    if manager is None or not owner_key:
        return identity
    try:
        if start_new_thread and hasattr(manager, "advance_conversation_thread"):
            thread_id, session_id = manager.advance_conversation_thread(owner_key)
        elif hasattr(manager, "get_current_conversation_thread") and hasattr(
            manager, "get_current_conversation_session_id"
        ):
            thread_id = manager.get_current_conversation_thread(owner_key)
            session_id = manager.get_current_conversation_session_id(owner_key)
        else:
            return identity
        return replace(
            identity,
            canonical_session_id=str(session_id),
            thread_id=(int(thread_id) if int(thread_id) > 1 else None),
        )
    except Exception:
        return identity


class TelegramUserService:
    """Telegram QR client restricted to one owner's Saved Messages peer."""

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session_string: str = "",
        server_name: str = "AutoYou Server",
        training_export_consent: bool = False,
        on_session_saved: Optional[Callable[..., Any]] = None,
        client_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.api_id = _safe_int(api_id)
        self.api_hash = str(api_hash or "").strip()
        self.session_string = str(session_string or "").strip()
        self.server_name = str(server_name or "AutoYou Server").strip() or "AutoYou Server"
        self.training_export_consent = training_export_consent is True
        self._on_session_saved = on_session_saved
        self._client_factory = client_factory
        # This directory is intentionally only a service-data anchor. Telethon
        # uses a StringSession, so no plaintext .session file is created here.
        self.data_dir = get_service_data_dir("telegram_user", anchor=__file__)

        self._client: Any = None
        self._owner_id = 0
        self._authorized = False
        self._status = "stopped"
        self._needs_password = False
        self._last_error = ""
        self._qr_login: Any = None
        self._qr_url = ""
        self._qr_wait_task: Optional[asyncio.Task] = None
        self._event_handler_registered = False
        self._start_lock = asyncio.Lock()
        self._chat_tasks: Set[asyncio.Task] = set()
        self._prompt_builder_active: Optional[bool] = None
        self._prompt_builder_target_override = ""
        self._server_message_ids: Deque[int] = deque(maxlen=_SERVER_MESSAGE_ID_LIMIT)
        self._server_message_id_set: Set[int] = set()
        self.message_log: List[Dict[str, Any]] = []
        self._pending_media_replies: List[Dict[str, Any]] = self._load_pending_media_replies()

    @property
    def client(self) -> Any:
        """Expose the transport client for narrow local integration diagnostics."""
        return self._client

    def _configured_agent_label(self) -> str:
        """Use the mutable Server name without changing Telegram's linked device."""
        try:
            runtime_server = sys.modules.get("server")
            configured = getattr(runtime_server, "get_configured_server_name", lambda: "")()
            if str(configured or "").strip():
                return str(configured).strip()
        except Exception:
            pass
        return self.server_name

    def _prompt_builder_settings(self) -> tuple[bool, str]:
        """Read the owner-scoped prompt-builder switch from the live server config."""
        enabled = False
        target = "codex_desktop_agent"
        try:
            runtime_server = sys.modules.get("server")
            config = getattr(getattr(runtime_server, "STATE", None), "config", None)
            telegram_cfg = config.get("telegram_user", {}) if isinstance(config, dict) else {}
            builder_cfg = telegram_cfg.get("prompt_builder", {}) if isinstance(telegram_cfg, dict) else {}
            if isinstance(builder_cfg, dict):
                enabled = bool(builder_cfg.get("enabled", False))
                target = str(builder_cfg.get("application_agent") or target).strip().lower().replace("-", "_")
        except Exception:
            pass
        if target in {"codex", "codex_desktop"}:
            target = "codex_desktop_agent"
        elif target in {"claude", "claude_desktop"}:
            target = "claude_desktop_agent"
        if target not in {"codex_desktop_agent", "claude_desktop_agent"}:
            target = "codex_desktop_agent"
        if self._prompt_builder_target_override:
            target = self._prompt_builder_target_override
        if not enabled:
            self._prompt_builder_active = False
        elif self._prompt_builder_active is None:
            self._prompt_builder_active = True
        return enabled, target

    def get_status(self) -> Dict[str, Any]:
        """Return non-sensitive state; never return a session, QR token, or account ID."""
        connected = bool(self._authorized and self._client is not None)
        prompt_builder_enabled, prompt_builder_agent = self._prompt_builder_settings()
        # from __debug_provenance_n__ import license
        return {
            "status": self._status,
            "connected": connected,
            "authorized": connected,
            "ready": connected,
            "needs_password": bool(self._needs_password),
            "owner_scoped": True,
            "saved_messages_only": True,
            "training_export_consent": self.training_export_consent,
            "media_enabled": True,
            "pending_media_replies": len(self._pending_media_replies),
            "message_count": len(self.message_log),
            "dependency_available": TelegramClient is not None,
            "prompt_builder": {
                "enabled": prompt_builder_enabled,
                "active": bool(self._prompt_builder_active)
                if self._prompt_builder_active is not None
                else prompt_builder_enabled,
                "application_agent": prompt_builder_agent,
            },
        }

    status = get_status

    def get_message_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return only the bounded in-process Saved Messages event log."""
        try:
            count = max(1, min(int(limit), _LOG_LIMIT))
        except (TypeError, ValueError):
            count = 100
        return [dict(entry) for entry in self.message_log[-count:]]

    async def start(self) -> bool:
        """Connect the optional client and register live Saved Messages updates."""
        async with self._start_lock:
            if (TelegramClient is None or StringSession is None) and self._client_factory is None:
                self._status = "unavailable"
                self._last_error = "Telegram User support is not installed in this runtime."
                return False
            if self.api_id <= 0 or not self.api_hash:
                self._status = "misconfigured"
                self._last_error = "Telegram API settings are incomplete."
                return False
            try:
                if self._client is None:
                    self._client = self._create_client()
                connect = getattr(self._client, "connect", None)
                if callable(connect):
                    await _maybe_await(connect())
                authorized = getattr(self._client, "is_user_authorized", None)
                self._authorized = bool(await _maybe_await(authorized())) if callable(authorized) else False
                if self._authorized:
                    await self._finish_authorization()
                else:
                    self._status = "awaiting_qr"
                    self._needs_password = False
                return True
            except Exception as exc:
                self._status = "error"
                self._last_error = type(exc).__name__
                LOGGER.warning("Telegram User client could not start: %s", exc)
                return False

    async def stop(self) -> bool:
        """Disconnect without logging the user out or removing the protected session."""
        task = self._qr_wait_task
        self._qr_wait_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        client = self._client
        if client is not None and self._event_handler_registered:
            remove = getattr(client, "remove_event_handler", None)
            if callable(remove):
                try:
                    remove(self._handle_new_message)
                except Exception:
                    pass
        self._event_handler_registered = False
        if client is not None:
            disconnect = getattr(client, "disconnect", None)
            if callable(disconnect):
                try:
                    await _maybe_await(disconnect())
                except Exception as exc:
                    LOGGER.debug("Telegram User disconnect failed: %s", exc)
        self._authorized = False
        self._qr_login = None
        self._qr_url = ""
        self._status = "stopped"
        return True

    async def disconnect(self) -> bool:
        """Explicitly revoke the Telegram sign-in; the server then clears encrypted state."""
        client = self._client
        if client is not None:
            try:
                log_out = getattr(client, "log_out", None)
                if callable(log_out):
                    await _maybe_await(log_out())
            except Exception as exc:
                LOGGER.warning("Telegram User logout failed: %s", exc)
                return False
        self.session_string = ""
        self._authorized = False
        self._owner_id = 0
        self._qr_login = None
        self._qr_url = ""
        self._needs_password = False
        self._status = "disconnected"
        return True

    log_out = disconnect

    async def begin_qr_login(self) -> Dict[str, Any]:
        """Create one QR sign-in and wait in the background for Telegram approval."""
        if not await self._ensure_started():
            return {"success": False, "status": self._status, "error": "Telegram User is not ready."}
        if self._authorized:
            return {"success": True, "connected": True, "status": "connected"}
        if self._needs_password:
            return {"success": False, "status": "needs_password", "needs_password": True}
        if self._qr_url and self._qr_wait_task is not None and not self._qr_wait_task.done():
            # A live sign-in is already refreshing its token in the background;
            # hand back the current code instead of invalidating it with a new one.
            return {
                "success": True,
                "status": "ready",
                "qr_url": await self.get_qr_code_data_url(),
                "saved_messages_only": True,
            }
        try:
            qr_login = getattr(self._client, "qr_login", None)
            if not callable(qr_login):
                raise RuntimeError("QR sign-in is unavailable")
            self._qr_login = await _maybe_await(qr_login())
            self._qr_url = str(getattr(self._qr_login, "url", "") or "").strip()
            if not self._qr_url:
                raise RuntimeError("Telegram did not return a QR sign-in URL")
            self._needs_password = False
            self._status = "awaiting_qr"
            if self._qr_wait_task is not None and not self._qr_wait_task.done():
                self._qr_wait_task.cancel()
            self._qr_wait_task = asyncio.create_task(self._wait_for_qr_login(self._qr_login))
            return {
                "success": True,
                "status": "ready",
                "qr_url": await self.get_qr_code_data_url(),
                "saved_messages_only": True,
            }
        except Exception as exc:
            self._status = "error"
            self._last_error = type(exc).__name__
            LOGGER.warning("Telegram User QR sign-in could not start: %s", exc)
            return {"success": False, "status": "error", "error": "Could not prepare Telegram sign-in."}

    start_qr_login = begin_qr_login

    async def get_qr_code_data_url(self) -> str:
        """Render the secret QR deep link as a browser-safe image data URL."""
        if not self._qr_url or self._needs_password or self._authorized:
            return ""
        try:
            image = qrcode.make(self._qr_url)
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception as exc:
            LOGGER.debug("Telegram User QR image rendering failed: %s", exc)
            return ""

    async def complete_2fa(self, password: str) -> Dict[str, Any]:
        """Complete an account's own optional Telegram two-step verification."""
        if not self._client or not str(password or ""):
            return {"success": False, "needs_password": True}
        try:
            sign_in = getattr(self._client, "sign_in", None)
            if not callable(sign_in):
                raise RuntimeError("Password sign-in is unavailable")
            await _maybe_await(sign_in(password=str(password)))
            self._authorized = True
            await self._finish_authorization()
            return {"success": True, "connected": True, "status": "connected"}
        except Exception as exc:
            self._status = "needs_password"
            self._needs_password = True
            self._last_error = type(exc).__name__
            LOGGER.info("Telegram User two-step verification was not accepted.")
            return {"success": False, "needs_password": True}

    submit_2fa_password = complete_2fa
    complete_two_factor_auth = complete_2fa

    async def send_message(self, message: str) -> bool:
        """Send a text message only to the connected account's Saved Messages."""
        return await self._send_text(message, append_signature=True)

    send_saved_message = send_message

    async def send_media_attachments(self, attachments: Iterable[Dict[str, Any]]) -> bool:
        """Send image, file, audio, voice, or video attachments only to Saved Messages."""
        candidates = [item for item in list(attachments or []) if isinstance(item, dict)]
        if not candidates:
            return False
        if not await self._ensure_authorized():
            return any(self._queue_pending_media_reply(item) for item in candidates)
        accepted = False
        for raw_attachment in candidates:
            if await self._send_media_attachment(raw_attachment):
                accepted = True
            elif self._queue_pending_media_reply(raw_attachment):
                accepted = True
        return accepted

    async def _send_media_attachment(self, raw_attachment: Dict[str, Any]) -> bool:
        """Attempt one attachment without mutating the persistent retry queue."""
        if not self._authorized or self._client is None:
            return False
        try:
            if not isinstance(raw_attachment, dict):
                return False
            data, filename = self._attachment_bytes(raw_attachment)
            if not data or len(data) > _MAX_MEDIA_BYTES:
                return False
            stream = BytesIO(data)
            stream.name = filename  # Telethon uses this name for the uploaded filename.
            caption = str(raw_attachment.get("caption") or "").strip()
            agent_label = self._configured_agent_label()
            if caption and not caption.rstrip().endswith(f"~ {agent_label}"):
                caption = f"{caption}\n~ {agent_label}"
            elif not caption:
                caption = f"~ {agent_label}"
            sent = await _maybe_await(
                self._client.send_file(
                    "me",
                    stream,
                    caption=caption,
                    voice_note=bool(raw_attachment.get("asVoice") or raw_attachment.get("as_voice")),
                )
            )
            self._remember_server_message(sent)
            self._log_message(
                message="[media reply]",
                direction="assistant",
                kind="voice" if raw_attachment.get("asVoice") or raw_attachment.get("as_voice") else self._attachment_kind(raw_attachment),
                has_media=True,
            )
            return True
        except Exception as exc:
            LOGGER.warning("Telegram User media delivery failed: %s", exc)
        return False

    async def send_audio(self, file_path: str, *, as_voice: bool = True) -> bool:
        """Send a generated audio reply back as a voice note when possible."""
        path = Path(str(file_path or "")).expanduser()
        if not path.is_file():
            return False
        return await self.send_media_attachments(
            [
                {
                    "path": str(path),
                    "filename": path.name,
                    "mimetype": mimetypes.guess_type(path.name)[0] or "audio/ogg",
                    "as_voice": bool(as_voice),
                }
            ]
        )

    async def _ensure_started(self) -> bool:
        if self._client is None:
            return await self.start()
        return True

    async def _ensure_authorized(self) -> bool:
        if not await self._ensure_started():
            return False
        return bool(self._authorized)

    def _create_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(
                session_string=self.session_string,
                api_id=self.api_id,
                api_hash=self.api_hash,
                device_model=self.server_name,
            )
        return TelegramClient(
            StringSession(self.session_string),
            self.api_id,
            self.api_hash,
            device_model=self.server_name,
            app_version="AutoYou",
            system_version="AutoYou private server",
        )

    async def _wait_for_qr_login(self, qr_login: Any) -> None:
        refreshes = 0
        try:
            while True:
                wait = getattr(qr_login, "wait", None)
                if not callable(wait):
                    raise RuntimeError("QR sign-in wait is unavailable")
                try:
                    await _maybe_await(wait())
                except asyncio.TimeoutError:
                    # Telegram QR tokens expire roughly every 30 seconds.
                    # Refresh the token in place so an open pairing dialog keeps
                    # showing a scannable code instead of a permanently stale one.
                    refreshes += 1
                    recreate = getattr(qr_login, "recreate", None)
                    if refreshes >= _QR_REFRESH_LIMIT or not callable(recreate):
                        self._expire_qr_login(qr_login)
                        return
                    await _maybe_await(recreate())
                    self._qr_url = str(getattr(qr_login, "url", "") or "").strip()
                    continue
                authorized = getattr(self._client, "is_user_authorized", None)
                self._authorized = bool(await _maybe_await(authorized())) if callable(authorized) else True
                if self._authorized:
                    await self._finish_authorization()
                else:
                    self._expire_qr_login(qr_login)
                return
        except SessionPasswordNeededError:
            self._needs_password = True
            self._status = "needs_password"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = type(exc).__name__
            LOGGER.debug("Telegram User QR wait ended: %s", exc)
            self._expire_qr_login(qr_login)

    def _expire_qr_login(self, qr_login: Any) -> None:
        """Forget a stale QR token so the next admin poll starts a fresh sign-in."""
        if self._qr_login is not qr_login:
            return
        self._qr_login = None
        self._qr_url = ""
        if not self._authorized and not self._needs_password:
            self._status = "awaiting_qr"

    async def _finish_authorization(self) -> None:
        get_me = getattr(self._client, "get_me", None)
        me = await _maybe_await(get_me()) if callable(get_me) else None
        owner_id = _safe_int(getattr(me, "id", None))
        if owner_id <= 0:
            raise RuntimeError("Telegram did not confirm the QR account")
        self._owner_id = owner_id
        self._authorized = True
        self._needs_password = False
        self._status = "connected"
        self._last_error = ""
        self._qr_login = None
        self._qr_url = ""
        self._register_live_handler()
        await self._persist_authorized_session()
        await self._flush_pending_media_replies()

    async def _persist_authorized_session(self) -> None:
        callback = self._on_session_saved
        session = getattr(getattr(self._client, "session", None), "save", None)
        if not callable(session):
            return
        try:
            session_value = str(session() or "").strip()
            if not session_value:
                return
            self.session_string = session_value
            if callback is not None:
                saved = callback(session_value)
                saved = await _maybe_await(saved)
                if saved is False:
                    LOGGER.warning("Telegram User session could not be saved to protected configuration.")
        except Exception as exc:
            LOGGER.warning("Telegram User session persistence failed: %s", exc)

    def _register_live_handler(self) -> None:
        if self._event_handler_registered or self._client is None:
            return
        add_handler = getattr(self._client, "add_event_handler", None)
        if not callable(add_handler):
            return
        try:
            builder = events.NewMessage() if events is not None else None
            if builder is None:
                add_handler(self._handle_new_message)
            else:
                add_handler(self._handle_new_message, builder)
            self._event_handler_registered = True
        except Exception as exc:
            LOGGER.warning("Telegram User live updates could not be registered: %s", exc)

    async def _handle_new_message(self, event: Any) -> None:
        # Telethon events expose ``event.message`` as a Message object, while a
        # raw Message's ``message`` attribute is its text body.  Accept both
        # forms without mistaking the latter string for the message object.
        candidate = getattr(event, "message", None)
        message = candidate if candidate is not None and not isinstance(candidate, (str, bytes)) else event
        if not self._is_owner_saved_message(message):
            return
        if self._is_forwarded(message):
            return
        message_id = _safe_int(getattr(message, "id", None))
        text = str(getattr(message, "raw_text", None) or getattr(message, "message", None) or "")
        if message_id in self._server_message_id_set or self._is_server_authored_text(text):
            return
        attachments = await self._download_message_attachment(message)
        prompt_builder_enabled, _ = self._prompt_builder_settings()
        prompt_builder_active = bool(self._prompt_builder_active)
        if not text and attachments and not prompt_builder_active:
            text = f"[Attachment: {attachments[0]['filename']}]"
        if not text and not attachments:
            return
        kind = self._message_kind(message, bool(attachments))
        context = [{"source": "telegram_user", "attachments": attachments}] if attachments else []

        try:
            pairing_reply = await pairing_router.process_message(
                message_text=text.strip(),
                platform="telegram_user",
                sender_id=str(self._owner_id),
            )
        except Exception as exc:
            LOGGER.warning("Telegram User pairing router failed: %s", exc)
            pairing_reply = None
        if pairing_reply and pairing_reply != PairingRouter.FRAGMENT_CONSUMED:
            # Pairing payloads can contain one-time secrets. They are never part
            # of the Saved Messages runtime log or any training export.
            await self._send_text(pairing_reply, append_signature=False, record=False)
            return
        if pairing_reply == PairingRouter.FRAGMENT_CONSUMED:
            return
        if PairingRouter.looks_like_raw_autopair_fragment(text.strip()):
            return
        self._log_message(message=text, direction="user", kind=kind, has_media=bool(attachments))
        if (
            prompt_builder_enabled
            and not prompt_builder_active
            and _prompt_builder_command_text(text) in {"build prompt", "prompt builder"}
        ):
            self._prompt_builder_active = True
            await self._send_text(
                "Prompt builder mode is active. Send exit confirm to return to the main agent.",
                append_signature=False,
            )
            return
        if prompt_builder_active:
            await self._handle_prompt_builder_message(text, attachments)
            return
        await self._forward_to_chat_api(text, context=context)

    async def _handle_prompt_builder_message(
        self,
        text: str,
        attachments: List[Dict[str, Any]],
    ) -> None:
        """Run the deterministic prompt-builder vocabulary from Saved Messages."""
        from autoyou_agents.build_prompt_agent.build_prompt_tool import run_tool

        normalized = _prompt_builder_command_text(text)
        if normalized == "exit confirm":
            self._prompt_builder_active = False
            await self._send_text(
                "You are back in the main AutoYou agent. Prompt builder mode is off.",
                append_signature=False,
            )
            return

        _, target = self._prompt_builder_settings()
        command_map = {
            "get prompt": "get_prompt",
            "status prompt": "status_prompt",
            "result prompt": "result_prompt",
            "delete prompt": "delete_prompt",
            "new prompt": "new_prompt",
            "execute prompt": "execute_prompt",
            "send prompt": "execute_prompt",
            "stop prompt": "stop_prompt",
        }
        tool_name = command_map.get(normalized)
        if normalized.startswith("configure prompt"):
            requested = normalized[len("configure prompt"):].strip()
            if requested in {"codex", "codex desktop", "codex desktop agent"}:
                target = "codex_desktop_agent"
                self._prompt_builder_target_override = target
            elif requested in {"claude", "claude desktop", "claude desktop agent"}:
                target = "claude_desktop_agent"
                self._prompt_builder_target_override = target
            tool_name = "configure_prompt"

        if tool_name is None:
            tool_name = "build_prompt"
            payload: Dict[str, Any] = {
                "text": text,
                "attachments": attachments,
                "application_agent": target,
            }
        else:
            payload = {"application_agent": target}

        try:
            result = await asyncio.to_thread(run_tool, tool_name, payload)
        except Exception as exc:
            result = {"success": False, "status": "error", "message": str(exc)}

        if result.get("success"):
            if tool_name == "result_prompt" and result.get("result_text"):
                await self._send_text(str(result["result_text"]), append_signature=True)
                return
            if tool_name in {"get_prompt", "status_prompt", "result_prompt", "build_prompt", "execute_prompt", "stop_prompt", "delete_prompt", "new_prompt", "configure_prompt"}:
                await self._send_text(
                    f"Prompt builder {tool_name}: {result.get('status', 'success')}; "
                    f"{result.get('characters', 0)} characters, {result.get('images', 0)} images, "
                    f"{result.get('words', 0)} words.",
                    append_signature=False,
                )
            return

        await self._send_text(
            f"Prompt builder {tool_name} failed: {result.get('message') or result.get('status') or 'error'}",
            append_signature=False,
        )

    def _is_owner_saved_message(self, message: Any) -> bool:
        if self._owner_id <= 0 or message is None:
            return False
        peer = getattr(message, "peer_id", None)
        peer_owner = _safe_int(getattr(peer, "user_id", None))
        if peer_owner != self._owner_id:
            return False
        sender_id = _safe_int(
            getattr(message, "sender_id", None)
            or getattr(getattr(message, "from_id", None), "user_id", None)
        )
        return sender_id == self._owner_id or (sender_id == 0 and bool(getattr(message, "out", False)))

    @staticmethod
    def _is_forwarded(message: Any) -> bool:
        # ``Message.forward`` is a Telethon convenience method, not a forward
        # marker.  Only the protocol's ``fwd_from`` field denotes forwarded
        # content here.
        return bool(getattr(message, "fwd_from", None))

    def _is_server_authored_text(self, text: str) -> bool:
        last_line = ((text or "").strip().splitlines() or [""])[-1].strip()
        labels = {self.server_name, self._configured_agent_label()}
        return bool(last_line and any(last_line.endswith(f"~ {label}") for label in labels if label))

    async def _download_message_attachment(self, message: Any) -> List[Dict[str, Any]]:
        if not getattr(message, "media", None):
            return []
        file_info = getattr(message, "file", None)
        size = _safe_int(getattr(file_info, "size", None))
        if size > _MAX_MEDIA_BYTES:
            LOGGER.info("Skipping oversized Telegram User media (%d bytes).", size)
            return []
        downloader = getattr(self._client, "download_media", None)
        if not callable(downloader):
            return []
        try:
            data = await _maybe_await(downloader(message, bytes))
            if not isinstance(data, (bytes, bytearray)) or not data or len(data) > _MAX_MEDIA_BYTES:
                return []
            filename = self._message_filename(message)
            mimetype = str(getattr(file_info, "mime_type", "") or "").strip()
            if not mimetype:
                mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            return [
                {
                    "filename": filename,
                    "mimetype": mimetype,
                    "data": base64.b64encode(bytes(data)).decode("ascii"),
                    "size_bytes": len(data),
                    "kind": self._message_kind(message, True),
                }
            ]
        except Exception as exc:
            LOGGER.warning("Telegram User media download failed: %s", exc)
            return []

    @staticmethod
    def _message_kind(message: Any, has_media: bool) -> str:
        if not has_media:
            return "message"
        if bool(getattr(message, "voice", None)):
            return "voice"
        if bool(getattr(message, "audio", None)):
            return "audio"
        if bool(getattr(message, "video", None) or getattr(message, "video_note", None)):
            return "video"
        if bool(getattr(message, "photo", None)):
            return "image"
        return "file"

    @staticmethod
    def _message_filename(message: Any) -> str:
        file_info = getattr(message, "file", None)
        candidate = str(getattr(file_info, "name", "") or "").strip()
        if candidate:
            return Path(candidate).name[:180]
        kind = TelegramUserService._message_kind(message, True)
        extensions = {"voice": ".ogg", "audio": ".audio", "video": ".mp4", "image": ".jpg", "file": ""}
        return f"telegram_saved_{kind}{extensions.get(kind, '')}"

    def _track_chat_task(self, coro: Awaitable[Any], *, session_key: str) -> None:
        task = asyncio.create_task(coro)
        self._chat_tasks.add(task)

        def _done(done_task: asyncio.Task) -> None:
            self._chat_tasks.discard(done_task)
            try:
                exc = done_task.exception()
            except asyncio.CancelledError:
                return
            except Exception:
                return
            if exc is not None:
                LOGGER.warning("Telegram User chat task failed for %s: %s", session_key, exc)

        task.add_done_callback(_done)

    async def _forward_to_chat_api(self, message: str, *, context: Optional[List[Dict[str, Any]]] = None) -> None:
        if self._owner_id <= 0:
            return
        starts_new, normalized, control_only = _extract_conversation_request(message)
        execution_manager = get_session_execution_manager()
        identity = execution_manager.bind_transport_owner(
            "telegram_user",
            str(self._owner_id),
            raw_session_id=str(self._owner_id),
        )
        identity = _resolve_conversation_identity(identity, start_new_thread=starts_new)
        if starts_new and control_only:
            await self._send_text("Started a new conversation.", append_signature=True)
            return

        async def _run_chat_request() -> None:
            from rest_api import ChatRequest, process_chat_message

            progress = {"last_sent": time.monotonic()}
            queue_state = {"position": 0}

            async def _on_execution_status(status: Any) -> None:
                queue_state["position"] = _safe_int(getattr(status, "queue_position", 0))

            async def _on_chunk(chunk: Dict[str, Any]) -> None:
                if not isinstance(chunk, dict) or not chunk.get("_autoyou_progress"):
                    return
                if time.monotonic() - progress["last_sent"] < 60.0:
                    return
                progress["last_sent"] = time.monotonic()
                await self._send_text(str(chunk.get("progress_text") or "Still working..."), append_signature=True)

            async def _send_immediate_media_reply(attachments: List[Dict[str, Any]]) -> bool:
                return await self.send_media_attachments(attachments)

            async def _execute_chat_request() -> Any:
                metadata = {
                    "client": "telegram_user",
                    "source": "telegram_saved_messages",
                    "owner_scoped": True,
                    "saved_messages_only": True,
                    "reply_target": {"transport": "telegram_user"},
                    "session_execution": {"queue_position": queue_state["position"]},
                    **build_autoyou_conversation_metadata(identity, reset=starts_new),
                }
                request = ChatRequest(
                    message=normalized,
                    user_id=identity.canonical_user_id,
                    session_id=identity.canonical_session_id,
                    context=context or [],
                    metadata=metadata,
                )
                return await process_chat_message(
                    request,
                    ai_agent_url=self._ai_agent_url(),
                    on_chunk=_on_chunk,
                    on_media_reply=_send_immediate_media_reply,
                )

            try:
                response = await execution_manager.submit_turn(
                    identity,
                    _execute_chat_request,
                    on_status=_on_execution_status,
                    label="telegram-user-chat",
                )
                media = list(getattr(response, "media_reply_attachments", []) or [])
                if media:
                    await self.send_media_attachments(media)
                voice_path = getattr(response, "voice_reply_audio_path", None)
                if voice_path:
                    audio_sent = await self.send_audio(str(voice_path))
                    try:
                        from shared.voice_messaging import cleanup_paths

                        cleanup_paths(str(voice_path))
                    except Exception:
                        pass
                    if not audio_sent and str(getattr(response, "response", "") or "").strip():
                        await self._send_text(str(response.response), append_signature=True)
                elif str(getattr(response, "response", "") or "").strip():
                    await self._send_text(str(response.response), append_signature=True)
            except SessionQueueFullError as exc:
                await self._send_text(exc.status.message, append_signature=True)
            except SessionTurnTimeoutError as exc:
                await self._send_text(exc.status.message, append_signature=True)
            except Exception as exc:
                LOGGER.error("Telegram User chat processing failed: %s", exc)
                await self._send_text("Sorry, something went wrong while processing your request.", append_signature=True)

        self._track_chat_task(_run_chat_request(), session_key=identity.canonical_session_id)

    def _ai_agent_url(self) -> str:
        try:
            import server

            return f"http://127.0.0.1:{int(getattr(server, 'AI_AGENT_SERVER_PORT', 8081))}"
        except Exception:
            return "http://127.0.0.1:8081"

    async def _send_text(self, message: str, *, append_signature: bool, record: bool = True) -> bool:
        body = str(message or "").strip()
        if not body or not await self._ensure_authorized():
            return False
        agent_label = self._configured_agent_label()
        if append_signature and not body.rstrip().endswith(f"~ {agent_label}"):
            body = f"{body}\n\n~ {agent_label}"
        try:
            sent = await _maybe_await(self._client.send_message("me", body))
            self._remember_server_message(sent)
            if record:
                self._log_message(message=body, direction="assistant", kind="message", has_media=False)
            return True
        except Exception as exc:
            LOGGER.warning("Telegram User text delivery failed: %s", exc)
            return False

    def _remember_server_message(self, message: Any) -> None:
        message_id = _safe_int(getattr(message, "id", None))
        if message_id <= 0:
            return
        if len(self._server_message_ids) == self._server_message_ids.maxlen:
            expired = self._server_message_ids.popleft()
            self._server_message_id_set.discard(expired)
        self._server_message_ids.append(message_id)
        self._server_message_id_set.add(message_id)

    def _log_message(self, *, message: str, direction: str, kind: str, has_media: bool) -> None:
        self.message_log.append(
            {
                "timestamp": int(time.time() * 1000),
                "message": str(message or ""),
                "direction": str(direction or "user"),
                "kind": str(kind or "message"),
                "has_media": bool(has_media),
                "saved_messages": True,
                "owner_scoped": True,
                "forwarded": False,
            }
        )
        if len(self.message_log) > _LOG_LIMIT:
            del self.message_log[:-_LOG_LIMIT]

    def _pending_media_reply_path(self) -> Path:
        return Path(self.data_dir) / "pending_media_replies.json"

    def _load_pending_media_replies(self) -> List[Dict[str, Any]]:
        path = self._pending_media_reply_path()
        try:
            if not path.is_file():
                return []
            raw = load_secure_json(path, default=[])
            entries = prune_and_trim_media_entries(raw if isinstance(raw, list) else [], limit_count=_PENDING_MEDIA_LIMIT)
            if entries != raw:
                self._pending_media_replies = entries
                self._persist_pending_media_replies()
            return entries
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.warning("Telegram User pending-media queue could not load: %s", exc)
            return []

    def _persist_pending_media_replies(self) -> None:
        path = self._pending_media_reply_path()
        try:
            if not self._pending_media_replies:
                path.unlink(missing_ok=True)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            entries = prune_and_trim_media_entries(self._pending_media_replies, limit_count=_PENDING_MEDIA_LIMIT)
            self._pending_media_replies = entries
            save_secure_json(path, entries)
        except SecureStorageError:
            raise
        except Exception as exc:
            LOGGER.warning("Telegram User pending-media queue could not persist: %s", exc)

    def _queue_pending_media_reply(self, attachment: Dict[str, Any]) -> bool:
        data, filename = self._attachment_bytes(attachment)
        if not data or len(data) > _MAX_MEDIA_BYTES:
            return False
        mimetype = str(attachment.get("mimetype") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
        try:
            stored = store_media_payload(
                self.data_dir,
                data,
                filename=filename,
                mimetype=mimetype,
                prefix="telegram-user",
            )
            if not stored:
                return False
            self._pending_media_replies.append(
                {
                    "queued_at": time.time(),
                    "filename": filename,
                    "mimetype": mimetype,
                    "caption": str(attachment.get("caption") or ""),
                    "as_voice": bool(attachment.get("asVoice") or attachment.get("as_voice")),
                    **stored,
                }
            )
            self._pending_media_replies = prune_and_trim_media_entries(
                self._pending_media_replies,
                limit_count=_PENDING_MEDIA_LIMIT,
            )
            self._persist_pending_media_replies()
            return True
        except Exception as exc:
            LOGGER.warning("Telegram User media reply could not be queued: %s", exc)
            return False

    async def _flush_pending_media_replies(self) -> None:
        if not self._authorized or not self._pending_media_replies:
            return
        remaining: List[Dict[str, Any]] = []
        for entry in list(self._pending_media_replies):
            data = read_media_payload(entry)
            if not data:
                delete_media_payload(entry)
                continue
            attachment = {
                "filename": entry.get("filename") or "telegram_media",
                "mimetype": entry.get("mimetype") or "application/octet-stream",
                "caption": entry.get("caption") or "",
                "as_voice": bool(entry.get("as_voice")),
                "data": data,
            }
            if await self._send_media_attachment(attachment):
                delete_media_payload(entry)
            else:
                remaining.append(entry)
        self._pending_media_replies = prune_and_trim_media_entries(
            remaining,
            limit_count=_PENDING_MEDIA_LIMIT,
        )
        self._persist_pending_media_replies()

    @staticmethod
    def _attachment_kind(attachment: Dict[str, Any]) -> str:
        value = str(attachment.get("kind") or "").strip().lower()
        if value:
            return value
        mimetype = str(attachment.get("mimetype") or "").lower()
        if mimetype.startswith("image/"):
            return "image"
        if mimetype.startswith("video/"):
            return "video"
        if mimetype.startswith("audio/"):
            return "audio"
        return "file"

    @staticmethod
    def _attachment_bytes(attachment: Dict[str, Any]) -> tuple[bytes, str]:
        supplied_filename = str(attachment.get("filename") or "").strip()
        filename = Path(supplied_filename or "telegram_media").name[:180] or "telegram_media"
        data = attachment.get("data")
        if isinstance(data, (bytes, bytearray)):
            return bytes(data), filename
        if isinstance(data, str) and data.startswith("data:"):
            try:
                encoded = data.split(",", 1)[1]
                return base64.b64decode(encoded, validate=True), filename
            except Exception:
                return b"", filename
        if isinstance(data, str) and data:
            try:
                return base64.b64decode(data, validate=True), filename
            except Exception:
                return b"", filename
        path = Path(str(attachment.get("path") or "")).expanduser()
        try:
            if path.is_file() and path.stat().st_size <= _MAX_MEDIA_BYTES:
                return path.read_bytes(), filename if supplied_filename else path.name
        except OSError:
            pass
        return b"", filename
