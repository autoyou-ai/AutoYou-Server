# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License. See LICENSE.

"""Opt-in policy for live direct messages from non-owner partner contacts."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import OrderedDict

from pairing_router import PairingRouter, pairing_router
from shared.pairing_response import PAIRING_REPLY_COMMANDS, is_app_pairing_message, pairing_reply_link

LOGGER = logging.getLogger(__name__)
PROVIDERS = frozenset({"telegram", "telegram_user", "signal", "whatsapp"})
PAIR_REQUESTS = frozenset({"/pair", "/otp_pair", "/pair_hello", "/autopair", "/autopair_hello", "/autopair_candidates"})
DEFAULT_TRIGGER = "@autoyou"
CONTACT_LIMIT = 100


def normalize_trigger(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"@[A-Za-z0-9_]{1,32}", value.strip()):
        raise ValueError("Use @ followed by 1–32 letters, digits, or underscores.")
    return value.strip()


class MessagingContacts:
    def __init__(self, router=pairing_router):
        self.router = router
        self.read_config = dict
        self.chat = None
        self.recent: OrderedDict[str, dict] = OrderedDict()
        self._events: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._chat_tasks: dict[str, asyncio.Task] = {}
        self._history: dict[str, list[tuple[str, str]]] = {}

    def config(self) -> dict:
        value = (self.read_config() or {}).get("messaging_contacts")
        return value if isinstance(value, dict) else {}

    @property
    def enabled(self) -> bool:
        return self.config().get("enabled") is True

    def snapshot(self) -> dict:
        cfg = self.config()
        return {
            "enabled": cfg.get("enabled") is True,
            "trigger": cfg.get("trigger", DEFAULT_TRIGGER),
            "recent": list(reversed(self.recent.values())),
            "approved": [{**value, "key": key} for key, value in self.approved().items()],
        }

    def approved(self) -> dict:
        rows = self.config().get("approved")
        return {key: row for key, row in rows.items() if isinstance(row, dict)} if isinstance(rows, dict) else {}

    def forget(self, key: str) -> None:
        self._history.pop(key, None)
        if task := self._chat_tasks.pop(key, None):
            task.cancel()

    def _allowed(self, key: str, capability: str) -> bool:
        cfg = self.config()
        rows = cfg.get("approved")
        row = rows.get(key) if isinstance(rows, dict) else None
        return cfg.get("enabled") is True and isinstance(row, dict) and row.get(capability) is True

    async def receive(self, provider, account, sender, label, event_id, text, reply):
        """Adapters must pass stable provider IDs and an in-chat reply callback."""
        if not self.enabled or provider not in PROVIDERS or not account or not sender or account == sender:
            return
        key = hashlib.sha256(f"{provider}\0{account}\0{sender}".encode()).hexdigest()
        display = "".join(c for c in str(label or "") if c.isprintable()).strip()[:120]
        self.recent[key] = {"key": key, "provider": provider, "label": display,
                            "hint": str(sender)[-8:], "seen_at": int(time.time())}
        self.recent.move_to_end(key)
        if len(self.recent) > CONTACT_LIMIT:
            self.recent.popitem(last=False)
        if not isinstance(text, str) or len(text) > 65536 or len(text.encode("utf-8", errors="replace")) > 65536 or not event_id:
            return
        text = text.strip()
        parts = text.split(maxsplit=1)
        if not parts or is_app_pairing_message(text):
            return
        command, *rest = parts
        sender_scope = f"contact-{key}"
        if command in PAIR_REQUESTS or (not command.startswith(("/", "@", "http", "autoyou:"))
                                        and self.router.has_pending_autopair_buffer(provider, sender_scope)):
            if not self._allowed(key, "pair") or self._duplicate(key, event_id):
                return
            try:
                response = await self.router.process_message(
                    text, platform=provider, sender_id=sender_scope, approved_contact=True,
                )
                if response and response != PairingRouter.FRAGMENT_CONSUMED and self._allowed(key, "pair"):
                    # Router errors can contain upstream exception text; keep that local.
                    kind, sep, _ = response.partition("\n")
                    safe = response if sep and kind in PAIRING_REPLY_COMMANDS else "Pairing did not complete. Check your pairing settings and try again."
                    await reply(pairing_reply_link(safe, limit=4000 if provider.startswith("telegram") else 0))
            except Exception:
                LOGGER.warning("Partner contact pairing failed (%s)", provider)
            return

        try:
            trigger = normalize_trigger(self.config().get("trigger", DEFAULT_TRIGGER))
        except ValueError:
            return
        prompt = rest[0].strip() if rest else ""
        # Neither a command nor an AutoYou link can be forwarded to the model.
        if (command != trigger or not prompt or len(prompt) > 8192 or is_app_pairing_message(prompt)
                or prompt.startswith("/") or "autoyou://" in prompt.lower()
                or "/peer-link/#" in prompt.lower() or "/peerpair" in prompt.lower()
                or not self._allowed(key, "chat") or self._duplicate(key, event_id)):
            return
        if key not in self._chat_tasks and len(self._chat_tasks) < 8:
            self._chat_tasks[key] = asyncio.create_task(self._respond(key, prompt, reply))

    def _duplicate(self, key, event_id) -> bool:
        event = key, str(event_id)[:160]
        if event in self._events:
            return True
        self._events[event] = None
        if len(self._events) > 512:
            self._events.popitem(last=False)
        return False

    async def _respond(self, key, prompt, reply) -> None:
        try:
            if not self._allowed(key, "chat") or self.chat is None:
                return
            history = self._history.get(key, [])
            answer = (await asyncio.wait_for(self.chat(history, prompt), timeout=90)).strip()[:1800]
            if answer and self._allowed(key, "chat"):
                await reply(answer)
                if self._allowed(key, "chat"):
                    self._history[key] = (history + [(prompt, answer)])[-4:]
        except Exception:
            LOGGER.warning("Partner contact chat failed")
            if self._allowed(key, "chat"):
                try:
                    await reply("AutoYou can’t answer right now. Please try again later.")
                except Exception:
                    pass
        finally:
            if self._chat_tasks.get(key) is asyncio.current_task():
                self._chat_tasks.pop(key, None)


messaging_contacts = MessagingContacts()
